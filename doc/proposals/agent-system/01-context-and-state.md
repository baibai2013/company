# 提案 1:上下文与状态层 — Context & State Layer

> **覆盖症状**:S1(失忆)+ S2(派活断链)
> **状态**:草案 v1
> **依赖**:无(脊柱层)
> **被依赖**:提案 2(验证)、提案 3(学习)

---

## 1. 问题诊断(代码层)

### 1.1 S1 失忆 — 跨任务状态归零

**用户报告**:"agent 不知道之前做了什么,导致错修改"。

**代码层根因**:

| 现象 | 代码 | 行为 |
|---|---|---|
| LangGraph state 是 per-task 的 | `agents_v2/shared/db.py` `AsyncPostgresSaver` | checkpointer 用 `thread_id` 区分,任务结束 thread 不会自动复用 |
| `EmployeeMemory` 表存在但未自动注入 | `backend/models/memory.py` `embedding Vector(1536)` + `backend/repos/memory_repo.py:201` `search_semantic()` | API 写好了,但 **claude_pool 启子进程时不会查它,prompt 不带它** |
| claude_pool idle 释放冷启动 | `agents_v2/shared/claude_pool.py`(常驻 5min idle 后回收)| 每次冷启动 claude code 子进程,session 内的隐式上下文(LLM cache、上次对话)全丢 |
| 共享文档(CRDT)无索引 | `mcp_servers/company_tools/server.py:298-464` `doc_create/read/append/list` | 写得多读得少,agent 不会主动 `doc_list` 找历史文档 |

**症状链路**:
```
任务 A 完成 → state 归零
   ↓
任务 B 来了(同一员工)→ claude_pool 冷启动 / system prompt = 静态 CLAUDE.md
   ↓
agent 不知道任务 A 干了什么 → 把任务 A 的成果当不存在 → 重做或错改
```

### 1.2 S2 派活断链 — 委派后没有状态机

**用户报告**:"派完后不提醒他人干活,或自己不去干活"。

**代码层根因**:

| 现象 | 代码 | 行为 |
|---|---|---|
| `delegate_to_employee` 是 fire-and-forget | `mcp_servers/company_tools/server.py` 该工具函数 | 调一次发一条飞书消息后 return `"✅ 已发送"`,**不写状态、不跟踪、不超时** |
| 派活方"反向追踪"缺失 | 无对应代码 | 派活方下次 prompt 不会提醒"你派给 firmware 的活还没回来" |
| 接活方"我有活在身"缺失 | 无对应代码 | 接活方下次 prompt 不会提醒"你有 N 个未认领委派" |
| Mattermost / 飞书 #状态频道 强制度低 | `feishu/sender.py` 工具方便 `send_status` 但 **agent 不调就不调** | 没有"任务结束必须 broadcast"的硬约束 |

**症状链路**:
```
PM 调 delegate_to_employee("mechanical", "做电池仓") → ✅ 已发送
   ↓
PM 觉得"派出去了" → 自己去做别的(或闲着)
mechanical 没看到飞书(或看到了忘了)→ 没动手
   ↓
2 小时后 PM 来检查 → 啥都没有 → 打回重派 → 又两小时
   ↓
项目级时间线碎裂
```

### 1.3 S1 + S2 的本质同构

两者都是**"agent 不知道当前 in-flight 的事情"**:
- S1 关心的是"过去的我自己做过什么"
- S2 关心的是"现在我派出去 / 接到了什么活,各自进度如何"

它们共享同一个数据底座("agent 的工作上下文"),所以放一起设计。

---

## 2. 设计目标

| 目标 | 验收信号 |
|---|---|
| 员工出场 prompt 头部强制注入 3 类上下文(长期记忆 / 任务上下文 / in-flight 委派),无需 LLM 主动 query | grep 任意一次 claude_pool 启子进程的 stdin,system prompt 含 `[CONTEXT]` 段 |
| 派活动作产生强约束的状态对象,有自动催办 / 超时升级 / 双向通知 | `delegations` 表上每条记录 status 不是 done/escalated 时,守护协程每 30s 检查 |
| 派活方 prompt 头部自动看到"我派出去未回的活" | mock 一个未回任务,启该员工 → prompt 含警告条目 |
| 接活方 prompt 头部自动看到"我手头未认领的活" | 同上,接活方视角 |
| 任务结束有清晰的"完成"事件,可以被提案 2 的 verifier hook 消费 | `delegations.status` 转 `done` 时发 PG NOTIFY / Redis pub |

---

## 3. 数据模型

### 3.1 新表 / 改表清单

```sql
-- 任务级共享上下文(LangGraph state 之外的"人类可读"摘要,跨员工共享)
CREATE TABLE task_context (
    id              BIGSERIAL PRIMARY KEY,
    task_id         UUID NOT NULL,                   -- 根任务 id
    parent_task_id  UUID,                             -- 子任务父级
    employee_key    TEXT NOT NULL,                    -- 谁说的 / 谁做的
    role            TEXT NOT NULL,                    -- speak | act | decide | deliver
    content_chunk   TEXT NOT NULL,                    -- markdown,512-2048 token
    embedding       VECTOR(1536),                     -- pgvector 语义检索
    created_at      TIMESTAMPTZ DEFAULT now(),
    INDEX (task_id, created_at),
    INDEX USING ivfflat (embedding vector_cosine_ops)
);

-- 派活状态机
CREATE TABLE delegations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_employee   TEXT NOT NULL,
    to_employee     TEXT NOT NULL,
    parent_task_id  UUID NOT NULL,                    -- 关联到根任务
    title           TEXT NOT NULL,                    -- 一句话标题
    content         TEXT NOT NULL,                    -- 完整委派内容(markdown)
    acceptance_spec JSONB,                            -- 验收标准(给提案 2 用)
    due_at          TIMESTAMPTZ,                      -- SLA 截止时间
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | claimed | in_progress | done | escalated | cancelled
    artifacts       JSONB,                            -- 接活方交付的文件 / 链接
    claimed_at      TIMESTAMPTZ,
    started_at      TIMESTAMPTZ,
    done_at         TIMESTAMPTZ,
    escalated_at    TIMESTAMPTZ,
    last_nudge_at   TIMESTAMPTZ,                      -- 最近一次催办
    nudge_count     INT DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now(),
    INDEX (from_employee, status),
    INDEX (to_employee, status),
    INDEX (parent_task_id, status),
    INDEX (status, due_at) WHERE status IN ('pending', 'claimed', 'in_progress')
);

-- 派活事件流(审计 + 提案 3 的 retro 输入)
CREATE TABLE delegation_events (
    id              BIGSERIAL PRIMARY KEY,
    delegation_id   UUID NOT NULL REFERENCES delegations(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,
                    -- created | claimed | progress_update | nudged | escalated | done | reopened
    actor           TEXT NOT NULL,                    -- 员工 key 或 system
    payload         JSONB,
    created_at      TIMESTAMPTZ DEFAULT now(),
    INDEX (delegation_id, created_at)
);

-- 已有 employee_memory 表,补字段(若没有)
ALTER TABLE employee_memory
    ADD COLUMN IF NOT EXISTS importance SMALLINT DEFAULT 5,    -- 1-10,summarizer 评分
    ADD COLUMN IF NOT EXISTS source_task_id UUID,              -- 来自哪个任务
    ADD COLUMN IF NOT EXISTS pinned BOOLEAN DEFAULT FALSE;      -- 人工置顶,不会衰减
```

### 3.2 三层记忆映射到表

| 层 | 表 | 写入时机 | 读取时机 |
|---|---|---|---|
| **L1 长期记忆** | `employee_memory` | 任务结束 → summarizer 抽取 5-10 条要点 | 员工启动时,按 `score = importance × recency_decay` 取 top 10 |
| **L2 任务级上下文** | `task_context` | 每次 LLM 完成回合 / 工具调用关键节点 | 员工接活时,按 `task_id` 取相关 chunks 做 token-budget 摘要 |
| **L3 公司级知识** | (已有 doc / decisions / specs,见提案集 §6 范围外说明) | 单独沉淀机制 | MCP `lookup_*` 工具按需查 |

---

## 4. 关键流程

### 4.1 员工启动 prompt 注入(S1 主路径)

```
claude_pool.spawn(employee_key, task_id)
       │
       ▼
┌──────────────────────────────────────────┐
│ build_context_preamble(employee, task)   │
│                                          │
│ ┌────────────────────────────────────┐   │
│ │ 1. L1 长期记忆 top 10               │   │
│ │    SELECT * FROM employee_memory   │   │
│ │    WHERE employee = :e             │   │
│ │    ORDER BY importance × decay DESC│   │
│ │    LIMIT 10                        │   │
│ └────────────────────────────────────┘   │
│                                          │
│ ┌────────────────────────────────────┐   │
│ │ 2. L2 任务上下文摘要                 │   │
│ │    SELECT * FROM task_context      │   │
│ │    WHERE task_id = :t              │   │
│ │    ORDER BY created_at             │   │
│ │    → token-budget summarizer 压缩   │   │
│ │    → 至 ≤2000 tokens                │   │
│ └────────────────────────────────────┘   │
│                                          │
│ ┌────────────────────────────────────┐   │
│ │ 3. In-flight 委派(派活方视角)       │   │
│ │    SELECT * FROM delegations       │   │
│ │    WHERE from_employee = :e        │   │
│ │      AND status NOT IN (done,      │   │
│ │           escalated, cancelled)    │   │
│ └────────────────────────────────────┘   │
│                                          │
│ ┌────────────────────────────────────┐   │
│ │ 4. 待认领委派(接活方视角)           │   │
│ │    SELECT * FROM delegations       │   │
│ │    WHERE to_employee = :e          │   │
│ │      AND status = 'pending'        │   │
│ └────────────────────────────────────┘   │
│                                          │
│ → 拼接成 markdown system prompt 段       │
└──────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│ 拼到原 CLAUDE.md system prompt 头部       │
│                                          │
│ [CONTEXT - 自动生成,你必须先读完再行动]    │
│ ## 你的长期记忆(top 10):                  │
│   - 上周做电池仓时和 firmware 约定...     │
│ ## 本任务此前发生了什么:                   │
│   - PM 在 14:32 派给你"做电池仓"          │
│   - 你在 14:45 提交 v1 但 testing 打回    │
│ ## 你派出去未回的活:                       │
│   ⚠️ 3 小时前派给 firmware "电流上限",     │
│     超过 SLA 1 小时,请决定:催 / 改派 / 撤  │
│ ## 你手头未认领的活:                       │
│   📥 PM 12:00 派给你"PCB 选型",待你认领    │
│                                          │
│ [/CONTEXT]                                │
│                                          │
│ 接下来是你的角色定义:                       │
│ ...原 CLAUDE.md...                        │
└──────────────────────────────────────────┘
```

### 4.2 派活状态机(S2 主路径)

```
       PM 调用 delegate_to_employee_v2(...)
                    │
                    ▼
        ┌───────────────────────┐
        │  写 delegations       │
        │  status='pending'     │
        │  写 delegation_events │
        │  → 飞书通知接活方       │
        │  → 给接活方 prompt 队列 │
        │     塞"待认领"提示       │
        └───────────────────────┘
                    │
                    ▼ (由接活方下次出场触发)
        ┌───────────────────────┐
        │ 接活方 LLM 看到提示    │
        │ → 调 claim_delegation │
        │ status='claimed'      │
        │ → 通知派活方"已认领"    │
        └───────────────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ 接活方边做边调          │
        │  update_delegation    │
        │ status='in_progress'  │
        │ progress 字段持续刷新   │
        └───────────────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ 接活方调               │
        │  complete_delegation  │
        │  artifacts={...}      │
        │ status='done'         │
        │ → 触发提案 2 的        │
        │   verifier(异步)       │
        │ → 通知派活方"活回来了"  │
        └───────────────────────┘
                    │
                    ▼ (verifier 通过才进入此态)
              [真 done,可结算]


超时分支(由 supervisor 守护协程驱动):
        ┌───────────────────────┐
        │ 每 30s 扫描:           │
        │  status IN (pending,  │
        │   claimed,            │
        │   in_progress)        │
        │  AND now() > due_at   │
        └───────────────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ 1st miss:nudge        │
        │  飞书 @接活方 + 派活方  │
        │  nudge_count++        │
        │  last_nudge_at = now  │
        └───────────────────────┘
                    │ (再 30min 还没动)
                    ▼
        ┌───────────────────────┐
        │ 2nd miss:escalate     │
        │  status='escalated'   │
        │  飞书 @TechLead/PM    │
        │  让人工接管             │
        └───────────────────────┘
```

---

## 5. 接口定义

### 5.1 新增 / 改造的 MCP 工具

```python
# 替代旧的 delegate_to_employee
@mcp.tool()
def delegate_v2(
    to_employee: str,
    title: str,
    content: str,
    acceptance_spec: dict,           # 验收标准,提案 2 会消费
    due_in_minutes: int = 60,
    parent_task_id: str | None = None,
) -> dict:
    """
    派活给指定员工。返回 delegation_id。
    自动:写表 + 飞书通知 + 给接活方 prompt 队列塞提示
    """

@mcp.tool()
def claim_delegation(delegation_id: str) -> dict:
    """接活方认领。pending → claimed。失败时返回原因(已认领/已撤回/不属于你)"""

@mcp.tool()
def update_delegation_progress(
    delegation_id: str,
    progress_note: str,
    percent: int | None = None,
) -> dict:
    """接活方更新进度。claimed/in_progress → in_progress(可重复调)"""

@mcp.tool()
def complete_delegation(
    delegation_id: str,
    artifacts: dict,                 # { "files": [...], "links": [...], "summary": "..." }
) -> dict:
    """接活方完成。in_progress → done。触发 verifier(异步)。"""

@mcp.tool()
def cancel_delegation(delegation_id: str, reason: str) -> dict:
    """派活方撤回。任意非终态 → cancelled。"""

@mcp.tool()
def list_my_delegations(
    direction: str = "both",         # "out" 派出去 / "in" 接到的 / "both"
    status_filter: list[str] | None = None,
) -> dict:
    """显式查询用,但通常 prompt 已经自动注入,LLM 主动调的频率应该低"""

@mcp.tool()
def nudge_delegation(delegation_id: str, message: str) -> dict:
    """派活方主动催办(自动催办之外的人工触发)"""
```

### 5.2 新增的 backend 服务

```python
# backend/services/context_builder.py
async def build_context_preamble(
    employee_key: str,
    task_id: str | None = None,
    token_budget: int = 4000,
) -> str:
    """
    返回拼好的 markdown,直接塞到 system prompt 头部。
    内部串四个 query + 一次 token-budget 压缩。
    """

# backend/services/delegation_service.py
async def create_delegation(...) -> Delegation: ...
async def claim_delegation(...) -> Delegation: ...
async def transition_to(delegation_id, new_status, **kwargs) -> Delegation: ...

# backend/services/delegation_supervisor.py
async def run_supervisor_loop():
    """每 30s 扫一次,执行 nudge / escalate / 通知。后台常驻。"""

# backend/services/memory_summarizer.py
async def summarize_task_to_memory(employee_key, task_id):
    """任务结束时调,Haiku 抽 5-10 条 lessons → employee_memory + embedding"""
```

### 5.3 改 claude_pool

```python
# agents_v2/shared/claude_pool.py
async def spawn_for_task(employee_key: str, task_id: str) -> ClaudeProcess:
    preamble = await context_builder.build_context_preamble(employee_key, task_id)
    base_system = load_employee_claude_md(employee_key)
    full_system = f"{preamble}\n\n---\n\n{base_system}"
    return await self._spawn(employee_key, system_prompt=full_system, ...)
```

---

## 6. 关键改动文件清单

| 类型 | 文件 | 改动 |
|---|---|---|
| 新建 | `alembic/versions/<ts>_context_state.py` | 三张新表 + memory 加字段 |
| 新建 | `backend/models/task_context.py` | TaskContext ORM |
| 新建 | `backend/models/delegation.py` | Delegation + DelegationEvent ORM |
| 新建 | `backend/repos/task_context_repo.py` | 写 / 查 / 摘要 |
| 新建 | `backend/repos/delegation_repo.py` | 状态机 + 查询 |
| 新建 | `backend/services/context_builder.py` | prompt 拼接核心 |
| 新建 | `backend/services/delegation_service.py` | 状态转移 + 通知触发 |
| 新建 | `backend/services/delegation_supervisor.py` | 守护协程 |
| 新建 | `backend/services/memory_summarizer.py` | 任务结束摘要 |
| 改 | `agents_v2/shared/claude_pool.py` | spawn 时调 build_context_preamble |
| 改 | `mcp_servers/company_tools/server.py` | 加 7 个新工具,移除旧 `delegate_to_employee` |
| 改 | `feishu/sender.py` | 增 `send_delegation_card`(带认领/超时按钮) |
| 改 | `agents_v2/shared/db.py` | 启 supervisor loop |

---

## 7. 实施分阶段(2 周内)

### Phase 1.A · 数据底座(1-2 天)

- [ ] alembic 迁移加三张表 + 索引
- [ ] ORM 模型 + repo 单测
- [ ] 接 pgvector 给 task_context 用(复用现有 pgvector 配置)

**验收**:能手动 INSERT / SELECT,索引能命中。

### Phase 1.B · 上下文注入(2-3 天)

- [ ] `context_builder.build_context_preamble` 实现
- [ ] `claude_pool.spawn_for_task` 改造
- [ ] `memory_summarizer` 任务结束钩子(挂到 LangGraph END node)

**验收**:启任意员工,日志能看到 `[CONTEXT]` 段被注入;mock 一段 task_context 能在 prompt 里看到摘要。

### Phase 1.C · 派活状态机(3-4 天)

- [ ] 7 个 MCP 工具实现 + 单测
- [ ] `delegation_supervisor` 守护协程,接到 backend startup
- [ ] 飞书 `send_delegation_card`(带"认领 / 标完成"按钮)
- [ ] 移除旧 `delegate_to_employee`,在 employees/CLAUDE.md 里替换为新工具用法

**验收**:
- PM 调 `delegate_v2` → mechanical prompt 收到提示 → mechanical 调 `claim_delegation` → PM prompt 看到"已认领"
- 设 1 分钟 due → 等 1 分钟 → 飞书自动催办 → 再等 30 分钟 → 自动 escalate

### Phase 1.D · 双向 prompt 注入闭环(1-2 天)

- [ ] 把"未回委派 / 未认领委派"接进 `build_context_preamble`
- [ ] 至少 2 个员工(PM + mechanical)做端到端 manual test
- [ ] 写 1-2 个测试任务 fixture

**验收**:派活方下次出场,prompt 头部能看到"⚠️ 你派出去 N 个未回";接活方下次出场看到"📥 N 个未认领"。

---

## 8. 验收标准 / SLI

| 指标 | 目标 | 测量方式 |
|---|---|---|
| 员工冷启动后,首条 LLM 输出能引用 L1/L2 上下文 | ≥ 90% | 抽样 30 次,人工评估是否引用了上下文 |
| 派活后 SLA 内被认领 | ≥ 95% | `delegations` 表 SQL 统计 |
| 派活超 SLA 后自动催办 | 100% | `delegation_events` 看 nudge 事件 |
| 派活方下次出场看到 in-flight 提示 | 100% | unit test:mock prompt 输出 |
| `delegations.status='done'` 事件可被外部消费(给提案 2)| 100% | PG NOTIFY 监听日志 |
| Token 消耗(同等任务对比 baseline)| ≤ +20%(注入开销可控) | 对照 baseline 任务 |

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| L2 任务上下文 token 爆炸 | 高 | prompt 超 200k | token-budget 摘要 + 只取与当前角色相关的 chunks(按 employee 字段过滤)|
| 多员工同时改 delegations 表 | 中 | 状态错乱 | 状态转移用 `UPDATE ... WHERE status = 'old' RETURNING` 乐观锁 |
| 自动催办飞书消息洪水 | 中 | 用户体验差 | nudge 间隔指数退避(30min / 1h / 2h);同 chat 同 delegation 30min 内只发一次 |
| summarizer 抽取质量差 | 中 | L1 记忆变噪音 | 只用置信度 ≥ 0.7 的条目;importance 字段允许 PM 手动 pin/delete |
| 守护协程崩溃后没人催办 | 低 | 派活悬挂 | 加 healthcheck endpoint;K8s liveness probe |
| 旧的 `delegate_to_employee` 还有员工在调 | 高 | 新旧并存 | 旧工具保留 1 周,内部转调新工具;在 stderr 警告 deprecated |

---

## 10. 与其他提案的接口

### 10.1 给提案 2(验证)

- `delegations.status` 转 `done` 时**不要立即真 done**,而是触发 verifier(异步)
- verifier 通过后才转真 `done`,失败转回 `in_progress` + 飞书通知
- `acceptance_spec` 字段是 verifier 的输入

### 10.2 给提案 3(学习)

- `delegation_events` 表是 retro_agent 的核心输入(看从 created 到 done 走了几个 nudge / escalate)
- `task_context` 是 retro_agent 抽 lessons 的素材
- summarizer 可以复用 retro_agent 的 LLM call 模板

---

## 11. 决策日志

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-05-25 | 用 `delegations` 而不是直接扩展 LangGraph state | LangGraph state 粒度细且 per-task,跨任务 / 跨员工查不直观;关系型表更适合状态机 + 索引查询 |
| 2026-05-25 | L2 用 markdown chunk + embedding 而不是结构化字段 | 工程任务的"发生过什么"难提取成固定 schema;markdown + 语义检索更通用 |
| 2026-05-25 | 守护协程 30s 间隔(不是 5s 也不是 5min)| 5s 太频,1000 个委派每天 ~17M 次 SQL;5min 太慢,催办体感差;30s 是中位 |
| 2026-05-25 | 派活方注入"未回 + 未认领",不注入"已认领进行中" | 后者不需要派活方关注,prompt 节流 |
