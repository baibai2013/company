# 提案 4 — 横切系统补强(5 维度环绕骨干 1/2/3)

> **状态**:草案 v1
> **日期**:2026-05-26
> **作者**:CEO 与 Claude
> **关联**:[00-README](./00-README.md) / [01-context-and-state](./01-context-and-state.md) / [02-verification-and-gates](./02-verification-and-gates.md) / [03-self-evolving](./03-self-evolving.md) / `ARCHITECTURE_LANGGRAPH_A2A.md` / `doc/optimization-agentscope.md`

---

## 0. 为什么独立成篇

提案 1/2/3 是 agent 系统的"脊柱-免疫-学习"骨干,解决跨任务记忆 / 验证可信 / 失败可学这三件事。但要让骨干跑得稳、跑得久、跑得便宜,还需要 5 个**横切维度**的能力——它们都不属于某一根脊柱,而是穿过整个系统:

| 维度 | 关键问题 | 与骨干的关系 |
|---|---|---|
| **检索增强(RAG)** | prompt 里塞不下的领域知识从哪来 | 喂给提案 1 的"L3 公司级知识"层 |
| **工具调用(MCP 治理)** | 19 个工具堆一个 server,角色越界 | 提案 2 的 verifier 沙箱 + 提案 3 的失败回流共用 |
| **多 agent 协作** | `ARCHITECTURE_LANGGRAPH_A2A.md` 619 行设计未落地 | 提案 1 派活状态机 + 提案 2 gate 都假设 supervisor 存在 |
| **模型层训练** | 是否要 SFT / RLHF / Distill 自托管模型 | 必须等提案 3 evals 跑稳才有正反馈,否则瞎调 |
| **系统工程架构** | 看不见调用、SLO 没看板、CI 没 gate | 监控提案 1/2/3 的 SLI,失败时 page on-call |

00-README §6 把这 5 项标为"不在本提案范围",本文件把它们正式落成方案——**作为提案 1/2/3 落地之后(或并行)的横切补强**。

**与 1/2/3 的关系**:不替代、不阻塞、互为前后。读者请先理解 1/2/3 的脊柱再回来看本篇。

**实施总原则**:
- 提案 1/2/3 是 P0(没有不行)
- 本提案 4 的五维度按 P0/P1/P2 分级——**只有 §2(MCP 治理)和 §5.1-5.2(基础可观测性)是必做的 P0**;其余可按 ROI 排队
- §4(模型训练)在 30 天 / 1 个月节奏内**明确不做**,只做判断矩阵备查

---

## 1. 检索增强(RAG)— 三层知识从代码外补回 prompt

### 1.1 现状诊断

**用户原话脉络**:9 个员工的 prompt 都是"角色卡 + 当下任务 + Claude Code 内置工具调用",**没有任何外部知识检索**。

**代码层证据**:

| 文件 | 现象 |
|---|---|
| `feishu/cc_bridge/claude_runner.py` | 启动 claude code 子进程时只传 `--system-prompt`(角色卡)和初次用户消息,无任何 RAG 注入点 |
| `agents_v2/shared/claude_pool.py` | 池化按 `(employee, cwd, thread_id, model, effort)` 复用 — `cwd` 隐式绑定项目目录就是唯一的"知识源" |
| `mcp_servers/company_tools/server.py` | 19 个工具中**无一个是知识检索类**(都是动作/通信/调度) |
| `doc/optimization-agentscope.md` 优化 5 | 已经识别"Memory 分层"问题但**只覆盖会话级**,没有跨会话的语义检索 |

所以 9 个员工**只能依靠 cwd 文件 + claude code 自带 grep/glob**,缺三类知识:

1. **公司规范库**:`employees/*/duties.md` / 历史 ADR / 接口契约 / build123d 公司私有 Part 库 → 现在每次都靠 cwd 内 grep,频繁失误且 token 开销大
2. **跨项目历史经验**:past PR / past failure root cause / past CAD 装配冲突 → 现在永远丢失
3. **行业 / 领域知识**:四足机器狗机械结构 / 步态规划 / 控制理论参考资料 → 现在完全没有

提案 1 的"L3 公司级知识"层在数据模型里**留了表位但没填实现**(见 [01-context-and-state §3.2](./01-context-and-state.md#32-三层记忆映射到表)),本节把它填完整。

### 1.2 设计目标

| 目标 | 说明 |
|---|---|
| **三层正交** | L1(任务上下文,提案 1 已有)/ L2(公司规范,本节)/ L3(领域知识,本节)三层互不重叠 |
| **零新增中间件** | 复用现有 PostgreSQL,加 `pgvector` 扩展,不引入 Pinecone/Milvus 等专项向量库 |
| **召回有审计** | 每次 RAG 召回都有 trace,可以反查"为什么这个 lesson 没被命中" |
| **写入无人化** | L2/L3 入库由 cron + git hook 自动跑,不依赖人手维护 |
| **token 预算可调** | RAG 注入字数有上限,超出按"相关性 × 时效性"截断 |

### 1.3 三层 RAG 模型

```
┌────────────────────────────────────────────────────────────────┐
│  L1 任务级上下文(提案 1 已有,本节不重复)                       │
│  - 表:task_context / employee_memory                           │
│  - 召回方式:thread_id 精确查 + recent N 条                     │
│  - 注入点:提案 1 §4.1 startup prompt header                   │
├────────────────────────────────────────────────────────────────┤
│  L2 公司规范库(本节实现)                                       │
│  - 来源:employees/*/duties.md / doc/**/*.md / ADR / 接口契约   │
│  - 写入:git post-commit hook 增量 embed → kb_documents 表     │
│  - 召回:语义检索(top-5) + 按角色过滤                          │
│  - 注入点:任意员工 prompt header,token 预算 1000              │
├────────────────────────────────────────────────────────────────┤
│  L3 领域知识(本节实现)                                         │
│  - 来源:四足机器人论文 / build123d 文档 / ESP32 datasheet     │
│  - 写入:人工/脚本 import,标注 domain_tag                       │
│  - 召回:仅 query 含 domain 关键词时触发                        │
│  - 注入点:相关角色 prompt header,token 预算 800                │
└────────────────────────────────────────────────────────────────┘
```

**关键设计选择**:

- **L2 写入用 git hook,不用定时扫**:文档变了就重 embed,延迟 < 5 秒
- **L3 与 L2 物理同表,只在 `domain_tag` 字段区分**:简化 SQL,只是召回条件不同
- **召回与 lesson 召回(提案 3 §4.2)同表分 namespace**:embeddings/相似度逻辑只写一次

### 1.4 数据模型

```sql
-- 启用 pgvector(基础设施一次性变更)
CREATE EXTENSION IF NOT EXISTS vector;

-- L2/L3 共用表
CREATE TABLE kb_documents (
  id BIGSERIAL PRIMARY KEY,
  source_path TEXT NOT NULL,          -- e.g. "employees/mechanical/duties.md#section-2"
  source_type TEXT NOT NULL,          -- 'duties' | 'adr' | 'contract' | 'domain' | 'past_pr'
  domain_tag TEXT,                    -- L3 用:'mechanical' | 'firmware' | 'algorithm' | NULL
  role_filter TEXT[],                 -- 哪些员工可召回,NULL=所有
  title TEXT,
  body TEXT NOT NULL,                 -- 原文(分片后,每片 200-500 token)
  embedding vector(1536),             -- text-embedding-3-small 维度
  meta JSONB,                         -- {git_sha, last_modified, author, ...}
  indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX kb_docs_embedding_idx ON kb_documents
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX kb_docs_role_idx ON kb_documents USING GIN (role_filter);
CREATE INDEX kb_docs_domain_idx ON kb_documents (domain_tag);

-- 召回 trace(便于审计:为什么命中/没命中)
CREATE TABLE kb_retrieval_log (
  id BIGSERIAL PRIMARY KEY,
  task_id UUID,                       -- FK → task_context.id (提案 1)
  employee_key TEXT NOT NULL,
  query TEXT NOT NULL,                -- 召回 query(通常是任务标题+摘要)
  layer TEXT NOT NULL,                -- 'L2' | 'L3'
  hit_doc_ids BIGINT[],
  hit_scores FLOAT[],
  injected_chars INT,                 -- 实际拼进 prompt 的字数(token 预算后的截断结果)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**为什么用 ivfflat 而不是 hnsw**:`pgvector` 的 hnsw 内存占用 3-5x ivfflat,在 < 10万文档量级下 ivfflat 召回 P95 < 50ms 已经够用。等文档量过 20 万再切 hnsw。

### 1.5 写入流水线

#### L2 增量入库(git post-commit hook)

```bash
# .git/hooks/post-commit(同时部署到 employees/ 和主项目)
#!/bin/bash
git diff --name-only HEAD^ HEAD | grep -E '\.(md|yaml|yml)$' | \
  xargs -I{} python -m backend.services.kb_ingest --path {}
```

```python
# backend/services/kb_ingest.py(简化版)
async def ingest_path(path: str):
    text = pathlib.Path(path).read_text()
    chunks = chunk_by_heading(text, max_tokens=400)  # 按 markdown heading 分片
    embeddings = await embed_batch([c.body for c in chunks])

    async with db.transaction():
        await db.execute(
            "DELETE FROM kb_documents WHERE source_path LIKE :prefix",
            {"prefix": f"{path}#%"}
        )
        for chunk, emb in zip(chunks, embeddings):
            await db.execute("""
                INSERT INTO kb_documents
                  (source_path, source_type, domain_tag, role_filter, title, body, embedding, meta)
                VALUES (:source_path, :source_type, :domain_tag, :role_filter, :title, :body, :embedding, :meta)
            """, {...})
```

**关键点**:
- 写入幂等:用 `source_path LIKE 'path#%'` 删除旧片再插新片
- chunk 边界用 markdown heading,语义不被切断
- `role_filter` 从 `source_path` 推导:`employees/mechanical/*` → `['mechanical']`,`doc/architecture/*` → `NULL`(所有)

#### L3 人工 import

```bash
# scripts/kb_import_domain.py
python scripts/kb_import_domain.py \
  --domain mechanical \
  --pdf "papers/quadruped_kinematics.pdf" \
  --role mechanical,algorithm
```

PDF 用 `pypdf` 提文本 → 同样 chunk → embed → 入库。

### 1.6 召回与注入(嵌入提案 1 的 context_builder)

```python
# backend/services/context_builder.py(扩展提案 1 已有的)

async def build_employee_prompt_header(
    employee_key: str,
    task_id: UUID,
    user_query: str,
) -> str:
    parts = []

    # 提案 1 已有:L1 任务上下文
    parts.append(await load_l1_task_context(task_id, employee_key))

    # 本节新增:L2 公司规范召回
    l2_hits = await retrieve_kb(
        query=user_query,
        layer='L2',
        role=employee_key,
        top_k=5,
        token_budget=1000,
    )
    if l2_hits:
        parts.append(format_kb_section("📘 公司规范 / 历史决策", l2_hits))

    # 本节新增:L3 领域知识(条件触发)
    if has_domain_keyword(user_query, employee_key):
        l3_hits = await retrieve_kb(
            query=user_query,
            layer='L3',
            role=employee_key,
            top_k=3,
            token_budget=800,
        )
        if l3_hits:
            parts.append(format_kb_section("📚 领域知识", l3_hits))

    # 提案 3 已有:相关 lessons
    parts.append(await retrieve_lessons(task_id, employee_key, user_query))

    return "\n\n".join(parts)


async def retrieve_kb(query, layer, role, top_k, token_budget):
    query_emb = await embed_one(query)
    rows = await db.fetch_all("""
        SELECT id, source_path, title, body, 1 - (embedding <=> :query_emb) AS score
        FROM kb_documents
        WHERE (role_filter IS NULL OR :role = ANY(role_filter))
          AND (
            (:layer = 'L2' AND domain_tag IS NULL)
            OR (:layer = 'L3' AND domain_tag IS NOT NULL)
          )
        ORDER BY embedding <=> :query_emb
        LIMIT :top_k
    """, {...})

    # 写 trace
    await db.execute("INSERT INTO kb_retrieval_log ...")

    return truncate_to_token_budget(rows, token_budget)
```

### 1.7 与 1/2/3 的接口

| 接口 | 给谁 | 内容 |
|---|---|---|
| `retrieve_kb()` 输出 | 提案 1 `context_builder` | 拼进 prompt header,在 L1 后、lessons 前 |
| `kb_retrieval_log` 表 | 提案 3 retro_agent | retro 时可看"哪些 KB 命中了仍出错"→ 反向更新 KB |
| `kb_documents` schema | 提案 3 lesson 入库 | lesson 召回与 KB 召回**用同一函数**,只是查不同表 |
| 不依赖 | 提案 2 | 验证三闸不消费 KB |

### 1.8 实施分阶段(2-3 周,P1)

| Phase | 时长 | 内容 | 验收 |
|---|---|---|---|
| **4.1.A** | 2 天 | pgvector 部署 + `kb_documents` / `kb_retrieval_log` 建表 + embedding API 接入(OpenAI text-embedding-3-small 或本地 bge-m3) | 跑通"插入一条→查相似"端到端 |
| **4.1.B** | 3-4 天 | L2 写入流水线:`kb_ingest.py` + git hook + 全量回填 employees/ 和 doc/ | `kb_documents` 至少 200 条,L2 召回 P95 < 100ms |
| **4.1.C** | 2-3 天 | 召回函数嵌入 `context_builder`,所有员工 prompt header 都注入 L2 | 任一员工 prompt 头部能看到匹配的规范片段,trace 表可查 |
| **4.1.D** | 3-5 天 | L3 人工 import 至少 3 个 domain(mechanical / firmware / algorithm)各 5-10 篇核心资料 | 关键词触发能命中 L3 |

### 1.9 ROI 与优先级:**P1**

- **不阻塞 1/2/3**(L3 知识层在提案 1 留了表位,本节填实)
- **直接降 token**:粗略估计每任务 prompt 减 30-50%(角色卡里塞的"职责描述"可以挪到 L2 按需召回)
- **风险**:embedding 服务费用 — 全公司一年 ~$100 量级(<10万文档),可忽略

---

## 2. 工具调用(MCP 治理)— 从 19 工具单 server 到按角色拆分

### 2.1 现状诊断

**代码层证据**:

```
mcp_servers/company_tools/server.py(单 server,19 个工具)

├─ 调度类(3): schedule_task, cancel_scheduled_task, list_scheduled_tasks
├─ 飞书消息类(6): send_feishu_message, reply_feishu_short, react_emoji,
│                send_group_chat_message, send_feishu_image, send_feishu_file
├─ 文档类(7): doc_create, doc_append, doc_replace_section,
│             doc_insert_after, doc_annotate, doc_read, doc_list
├─ 委派类(1): delegate_to_employee
└─ 历史类(1): recall_history
```

**问题**(按严重度排序):

1. **prompt 噪音**:每个员工启动都加载全部 19 个工具的 schema,机械工程师不需要 `react_emoji`,Verifier 不需要 `delegate_to_employee` → 多浪费 ~600 token/启动
2. **角色越界**:测试工程师可以调用 `delegate_to_employee`(只有 PM/TechLead 应该能派活),Verifier 可以调用 `send_feishu_file`(可能误发用户文件)→ **真实出现过事故**
3. **失败不可学**:工具调用失败(超时 / schema 不符)只在 stdout 报错,**不写 DB**,提案 3 的 retro 无从分析
4. **沙箱缺位**:`delegate_to_employee` 内部直接拉起 claude_pool 子进程,无 cgroup / 无 quota,误用可能跑爆机器
5. **测试无替身**:写单测时 `send_feishu_message` 真发飞书 → 没人写 MCP 工具的单测

### 2.2 设计目标

| 目标 | 说明 |
|---|---|
| **按角色装载** | 每个员工启动时只挂载它能用的 server,prompt token 降 30%+ |
| **权限白名单** | 工具粒度的 ACL,谁能调谁,与 employee_key 强绑定 |
| **调用全 trace** | 所有工具调用入 `tool_call_log` 表,失败的进 `tool_failure_queue` |
| **失败回流提案 3** | retro_agent 消费失败队列,产出 lesson |
| **沙箱兜底** | 危险工具(派活、发飞书)走资源边界 |

### 2.3 拆 server 方案(5 个 MCP server)

```
mcp_servers/
├── company_tools/        ← 拆完后只剩 deprecated 标记,2 个版本后删
├── messaging/            ← 飞书全部 6 个 + react_emoji
├── docs/                 ← doc_* 7 个
├── scheduling/           ← schedule_* 3 个 + recall_history
├── orchestration/        ← delegate_to_employee + (新增 supervisor 工具)
└── verification/         ← (提案 2 用) ground truth checker / verifier 调用工具
```

按员工角色装载白名单:

```yaml
# config/mcp_role_bindings.yaml
employees:
  pm:                    [scheduling, docs, messaging, orchestration]
  tech_lead:             [scheduling, docs, messaging, orchestration, verification]
  mechanical:            [docs, messaging]
  hardware:              [docs, messaging]
  firmware:              [docs, messaging]
  algorithm:             [docs, messaging]
  testing:               [docs, messaging, verification]
  cost:                  [docs, messaging]
  project_manager:       [scheduling, docs, messaging]

# 全局 deny(任何员工都不能调用)
global_deny:
  - delete_*             # 任何删除操作走人审
```

### 2.4 数据模型

```sql
-- 工具调用全 trace
CREATE TABLE tool_call_log (
  id BIGSERIAL PRIMARY KEY,
  task_id UUID,                       -- FK → task_context (提案 1)
  employee_key TEXT NOT NULL,
  server_name TEXT NOT NULL,          -- 'messaging' | 'docs' | ...
  tool_name TEXT NOT NULL,
  args JSONB,                         -- 已脱敏
  result_summary TEXT,                -- 截断到 1000 字
  status TEXT NOT NULL,               -- 'ok' | 'error' | 'timeout' | 'denied'
  duration_ms INT,
  error_class TEXT,                   -- error 时:'SchemaError' | 'NetworkError' | 'PermissionDenied'
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX tool_log_task_idx ON tool_call_log (task_id);
CREATE INDEX tool_log_employee_idx ON tool_call_log (employee_key, created_at DESC);
CREATE INDEX tool_log_failures_idx ON tool_call_log (status, created_at DESC)
  WHERE status != 'ok';

-- 失败队列(供提案 3 retro 消费)
CREATE TABLE tool_failure_queue (
  id BIGSERIAL PRIMARY KEY,
  log_id BIGINT REFERENCES tool_call_log(id),
  retro_consumed_at TIMESTAMPTZ,      -- NULL = 待消费
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 2.5 角色装载(改 claude_pool spawn)

```python
# agents_v2/shared/claude_pool.py 修改点

@dataclass(frozen=True)
class SpawnArgs:
    employee_key: str   # 新增
    cwd: str
    thread_id: str
    model: str
    effort: str

def _build_mcp_config(employee_key: str) -> str:
    """按角色生成 .mcp.json"""
    bindings = load_role_bindings()
    servers = bindings.get(employee_key, ['docs', 'messaging'])
    config = {
        "mcpServers": {
            name: {
                "command": f"python",
                "args": ["-m", f"mcp_servers.{name}.server"],
                "env": {"EMPLOYEE_KEY": employee_key, "TASK_ID": "..."}
            }
            for name in servers
        }
    }
    path = f"/tmp/mcp_{employee_key}_{random.token_hex(4)}.json"
    pathlib.Path(path).write_text(json.dumps(config))
    return path

# 改 PersistentRunner.spawn()
def spawn(self):
    mcp_config = _build_mcp_config(self.spawn_args.employee_key)
    cmd = [
        "claude", "code", "-p",
        "--input-format", "stream-json",
        "--mcp-config", mcp_config,        # 新增
        "--model", self.spawn_args.model,
        ...
    ]
```

### 2.6 工具调用 trace 注入(MCP 中间件)

```python
# mcp_servers/_shared/middleware.py(新)

@asynccontextmanager
async def trace_tool_call(server_name: str, tool_name: str, args: dict):
    employee_key = os.environ["EMPLOYEE_KEY"]
    task_id = os.environ.get("TASK_ID")
    started = time.monotonic()

    log_id = None
    try:
        yield  # 执行真实工具逻辑

        await db.execute("""
            INSERT INTO tool_call_log
              (task_id, employee_key, server_name, tool_name, args, status, duration_ms)
            VALUES (...) RETURNING id
        """, ...)

    except Exception as e:
        log_id = await db.execute_returning_id("""
            INSERT INTO tool_call_log (..., status, error_class) VALUES (...)
        """, status='error', error_class=type(e).__name__, ...)
        await db.execute(
            "INSERT INTO tool_failure_queue (log_id) VALUES (:log_id)",
            {"log_id": log_id}
        )
        raise


# 用法:每个工具加装饰器
@mcp.tool()
async def send_feishu_message(content: str, ...) -> str:
    async with trace_tool_call("messaging", "send_feishu_message", locals()):
        return await _real_send(content, ...)
```

### 2.7 与 1/2/3 的接口

| 接口 | 给谁 | 内容 |
|---|---|---|
| `tool_call_log` 表 | 提案 1 `task_context` | 任务上下文摘要可包含"本任务调用了哪些工具" |
| `tool_failure_queue` | 提案 3 retro_agent | retro 输入新增"工具失败明细",产出工具相关 lesson |
| `verification` server | 提案 2 verifier_orchestrator | verifier agent 装载此 server 调用 ground truth checker |
| `orchestration` server | 提案 2 gate 推进 | gate 通过后由 orchestration 工具触发下一步 |
| 不依赖 | RAG / 模型训练 | — |

### 2.8 实施分阶段(2 周,P0)

| Phase | 时长 | 内容 | 验收 |
|---|---|---|---|
| **4.2.A** | 2 天 | 抽取 `_shared/middleware.py` + `tool_call_log` / `tool_failure_queue` 建表 | 现有 server 加上中间件,所有调用入库 |
| **4.2.B** | 3-4 天 | 拆 5 个 server,在 `mcp_servers/<name>/server.py` 各自注册;旧 `company_tools` 改为 import 转发 | 所有员工跑老路径仍可用,新路径并行可用 |
| **4.2.C** | 2 天 | `mcp_role_bindings.yaml` + `_build_mcp_config()` 改 claude_pool | 任一员工启动只挂载白名单 server |
| **4.2.D** | 2 天 | 全量切到新路径,deprecate 老 `company_tools` | 旧 server 不再被任何员工引用 |
| **4.2.E** | 2 天 | 把 `tool_failure_queue` 接入提案 3 retro 输入 | retro_agent 能产出工具相关 lesson |

### 2.9 ROI 与优先级:**P0**

- **直接降 token**:每员工启动 prompt -600 token,9 员工 × 50 任务/天 ≈ 27 万 token / 天 节省
- **直接降事故**:权限越界这条线挡住误调用
- **提案 3 的输入质量翻倍**:retro 多了"工具失败"这条线索

---

## 3. 多 Agent 协作 — 把 ARCHITECTURE_LANGGRAPH_A2A 落地

### 3.1 现状诊断

**`ARCHITECTURE_LANGGRAPH_A2A.md` 已经有 619 行设计**,定义了:
- TechLead Supervisor + 8 员工子图
- A2A 协议(端口 :9001-:9008)
- LangGraph state machine + checkpointer

**但代码层是另一回事**:

| 文件 | 现状 |
|---|---|
| `agents_v2/generic/main.py` | 只有一个**通用 entry**,所有员工共用,角色靠 `system_prompt` 区分 |
| `agents_v2/shared/claude_pool.py` | 进程池**按 (employee_key, cwd, thread_id) 隔离**,但没有 LangGraph 节点编排 |
| `feishu/cc_bridge/claude_runner.py` | 直接 spawn claude code,**没有走 supervisor 路由** |
| 没有 `agents_v2/tech_lead/` 目录 | supervisor 完全不存在 |
| 没有 A2A server | 端口 :9001-:9008 都没起来 |

**所以**:派活靠 `delegate_to_employee` 工具(MCP)直接拉对方进程,**没有编排器**。这导致提案 1 的派活状态机虽然能记账,但**推进逻辑分散在每个员工的 prompt 里**,容易漏。

提案 2 的 gate 也假设有 supervisor 来"推进 / 暂停",但实际只能靠员工自觉。

### 3.2 设计目标

| 目标 | 说明 |
|---|---|
| **不大改架构** | 沿用 `ARCHITECTURE_LANGGRAPH_A2A.md` 的 LangGraph + A2A 设计,本节只做**落地路径**和**与 1/2/3 的责任划分** |
| **渐进迁移** | 老 `agents_v2/generic` 路径继续工作,新路径 supervisor 跑通后再切流量 |
| **明确责任边界** | supervisor 管"任务流转 / gate 推进 / 重试",员工管"领域执行",二者不互相代行 |
| **SSE 推进可见** | supervisor 进度通过 SSE 推到前端看板,CEO 不用问"现在到哪一步了" |

### 3.3 落地路径

#### 阶段一:先给现有 generic agent 套 LangGraph 单节点壳(2-3 天)

不上 supervisor,先让单个员工内部从"一锤子 claude code"变成"`analyze → execute → review` 三节点的 LangGraph",好处:

- **checkpointer 给提案 1 的 task_context 喂数据**:每节点完成自动 snapshot
- **failure 可重入**:某节点失败重启从断点恢复(对应提案 1 §10 失忆兜底)
- 不影响现有调用链:对外接口仍是 `agents_v2.generic.main:run_task`

#### 阶段二:起 TechLead Supervisor 进程(5-7 天)

```
agents_v2/
├── tech_lead/
│   ├── supervisor.py     ← LangGraph StateGraph
│   ├── routing.py        ← 任务 → 员工的路由策略
│   └── a2a_client.py     ← 调用员工 A2A server
├── mechanical/           ← 从 generic 派生
│   ├── graph.py
│   └── a2a_server.py
├── hardware/
├── firmware/
├── algorithm/
├── testing/
├── cost/
├── pm/
└── project_manager/
```

#### 阶段三:把 8 员工拆成 8 个 A2A server(每个 1 天 × 8)

最小可行的员工 graph:

```python
# agents_v2/mechanical/graph.py
from langgraph.graph import StateGraph

def build_mechanical_graph():
    g = StateGraph(EmployeeState)
    g.add_node("analyze", analyze_node)
    g.add_node("execute", execute_node)   # 内部跑 claude code
    g.add_node("self_review", self_review_node)
    g.add_node("submit_for_verify", submit_for_verify_node)  # 调提案 2

    g.add_edge("analyze", "execute")
    g.add_edge("execute", "self_review")
    g.add_conditional_edges("self_review", lambda s:
        "execute" if s.needs_revision else "submit_for_verify"
    )
    g.set_entry_point("analyze")
    g.set_finish_point("submit_for_verify")
    return g.compile(checkpointer=PostgresSaver(...))
```

### 3.4 Supervisor 与提案 2 gate / 提案 1 派活的责任边界

这是这一节最重要的设计澄清——三者职责不能混。

```
┌─────────────────────────────────────────────────────────────────┐
│  TechLead Supervisor(本节)                                       │
│  - 任务路由:接收用户原始任务,决定第一站派给谁                     │
│  - 流转推进:某员工 self_review pass → 调提案 2 verifier         │
│  - gate 协调:verifier reject → 决定回退/换人/上 human gate       │
│  - SSE 进度:把 state 流到前端                                    │
│  ✗ 不做:领域决策(由员工 prompt 做)/ 派活记账(提案 1 做)       │
├─────────────────────────────────────────────────────────────────┤
│  提案 1 · 派活状态机                                              │
│  - delegations 表:谁派给谁、SLA、超时                            │
│  - 双向 prompt 注入:派活方/接活方都能看到 in-flight              │
│  ✗ 不做:决定派给谁(supervisor 做)/ 决定能不能完成(提案 2)     │
├─────────────────────────────────────────────────────────────────┤
│  提案 2 · 验证三闸                                                │
│  - LLM verifier / ground truth check / human gate                │
│  - 给 supervisor 返回 pass/reject + 修复建议                     │
│  ✗ 不做:重试调度(supervisor 做)                                │
└─────────────────────────────────────────────────────────────────┘
```

**典型流程**(把 1/2/3/4 串起来):

```
1. 用户飞书 → backend → supervisor.route()
2. supervisor 决定派给"机械工程师"
3. supervisor 调 delegate_to_employee MCP 工具(orchestration server)
   → 提案 1 写 delegations(state=assigned)
   → 派活方/接活方 prompt 都注入这条记录
4. 机械工程师 graph 跑 analyze → execute → self_review
5. self_review pass → graph 走到 submit_for_verify
   → 提案 1 写 delegations.state=done(等验收)
6. supervisor 监听该状态变化 → 启动 verifier_orchestrator(提案 2)
7. 三闸通过 → supervisor.advance()
   - 流入提案 3 retro queue
   - 通知用户 / 派下一站
   三闸 reject → supervisor 决定:
   - 退回原员工 + 修复建议(进 employee state)
   - 或上 human gate(飞书审批)
```

### 3.5 数据模型(本节新增表很少,主要消费 1/2/3 的表)

```sql
-- supervisor state(LangGraph checkpointer 自带,不需手建)
-- 但需要一张"任务路由记录"便于查"为什么派给他不派给她"
CREATE TABLE routing_decisions (
  id BIGSERIAL PRIMARY KEY,
  task_id UUID NOT NULL,
  step_idx INT NOT NULL,              -- 这是任务内第几步
  candidate_employees TEXT[],         -- 候选名单
  chosen_employee TEXT NOT NULL,
  reason TEXT,                        -- supervisor 决策理由(LLM 输出)
  routed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.6 与 1/2/3 的接口

| 接口 | 给谁 | 内容 |
|---|---|---|
| `supervisor.advance(task_id)` | 提案 1 派活状态机 | supervisor 是 delegations 状态机的**唯一推进者** |
| `verifier_orchestrator(task_id)` 调用 | 提案 2 三闸 | supervisor 在 graph 里调用 |
| LangGraph checkpoint | 提案 1 task_context | 每节点 snapshot 写到 task_context |
| `routing_decisions` | 提案 3 retro | retro 时可分析"路由失误模式" |

### 3.7 实施分阶段(3-4 周,P1)

| Phase | 时长 | 内容 | 验收 |
|---|---|---|---|
| **4.3.A** | 3 天 | 给 generic 套 LangGraph 单节点壳 | checkpointer 入库,断电重启能续跑 |
| **4.3.B** | 5-7 天 | TechLead Supervisor 起进程 + 路由策略 | 至少 1 类任务能从用户 → supervisor → 单员工跑通 |
| **4.3.C** | 1-2 周 | 8 员工拆成 8 个 A2A server | 各 :9001-:9008 起来,A2A `/tasks/send` 可用 |
| **4.3.D** | 5-7 天 | 接入提案 1 delegations / 提案 2 verifier | 完整流程跑通,reject 能退回 |
| **4.3.E** | 3 天 | SSE 推到前端看板 | CEO 看板能实时看任务流转 |

### 3.8 ROI 与优先级:**P1**

- **必须做但不阻塞**:不做 supervisor,提案 1/2 也能跑(只是推进逻辑分散在员工 prompt 里),但**长期不可维护**
- **建议时点**:提案 1 落地后立即开 4.3.A,与提案 2 并行
- **风险**:LangGraph 学习曲线 + A2A 端口冲突 — 先在 dev 起 4 个员工试通

---

## 4. 模型层训练 — 30 天内不做的判断矩阵

### 4.1 当前模型现状

| 项 | 当前 |
|---|---|
| 推理模型 | Claude Sonnet 4.6(主力)/ Opus 4.7(复杂任务)/ Haiku 4.5(轻任务) |
| Embedding | OpenAI text-embedding-3-small(可换 bge-m3 本地) |
| 路由 | 简单规则:effort=high → Opus,effort=low → Haiku |
| 自托管 | 无 |
| Fine-tune | 无 |
| RLHF / DPO | 无 |
| Distill | 无 |

### 4.2 何时**不做**模型训练(本提案的明确判断)

> **结论:30 天内不做任何形式的模型训练。**

理由:

1. **没有评测体系**:提案 3 的 evals fixtures 都还没攒到 50 条(初始目标),贸然 SFT 没有回归基线 → 99% 的概率训完更差且不知道
2. **没有训练数据**:即便是 SFT,也至少需要 500-2000 条高质量"任务 → 期望输出"对,**当前每月有效任务大约 100 条,3-6 个月才够**
3. **prompt+RAG 红利还没吃完**:本提案 §1 RAG 落地后,token 利用率会大幅改善,**很多"模型不行"的现象其实是"上下文不全"**
4. **没有训练基础设施**:GPU 集群、训练脚本、checkpoint 管理、回滚预案都没有 → 一次性投入 > 2 周
5. **退化风险**:闭源模型(Claude)无法真正 fine-tune,只能是 RAG / few-shot;开源模型(Qwen / DeepSeek)需要换推理栈,**整个 9 员工的稳定性会回退 6 个月**

### 4.3 决策矩阵(等满足以下条件再启动)

按"必要条件 → 触发动作"的形式列:

| 触发条件 | 触发后动作 | 时点估计 |
|---|---|---|
| evals 通过率连续 3 个月平台期(< 5% 改善) | 启动 SFT 数据收集 | ~2026 Q4 |
| 同一类格式错误(如 build123d 代码格式)月发生率 > 5% 且 prompt 已优化无效 | 针对该子任务做 LoRA SFT(开源模型) | ~2026 Q4 |
| 飞书消息 / 卡片格式连续 1 个月被用户吐槽 | 用户偏好风格 SFT(`brand-voice` 维度) | 看用户反馈 |
| 月推理成本 > $5000 且 50%+ 是 Sonnet 复杂任务 | distill 部分任务到 Haiku 或自托管 7B | 看成本曲线 |
| 数据安全合规要求私有化部署 | 一次性切自托管 | 看合规节奏 |

### 4.4 替代路径优先级(都比训练优先)

```
ROI 排序(从高到低):

1. 提案 1/2/3 骨干落地           ← 必须
2. RAG L2/L3(本提案 §1)         ← 让模型"看到更多"
3. MCP 治理(本提案 §2)          ← 让模型"用对工具"
4. 多 agent 编排(本提案 §3)     ← 让模型"分工合作"
5. 系统工程(本提案 §5)          ← 让模型"被监督"
6. Few-shot / Prompt 微调       ← 已经在做
7. 模型升级(Opus 4.7 → 4.8)    ← 等官方
8. 模型 SFT / RLHF              ← 本节;30 天内不做
9. 自托管 7B + Distill          ← 至少 6 个月以后
```

### 4.5 长期蓝图(3-6 个月,仅作备查)

如果未来一定要做,推荐顺序:

```
Phase A · 数据飞轮(月 1-3)
  - 提案 3 evals 跑稳,产生 baseline
  - 提案 2 verifier 拒绝 / 通过的 case 沉淀
  - 提案 1 task_context 沉淀任务原始 trace
  - 写脚本批量构造 (input, expected_output) 对

Phase B · LoRA SFT 试点(月 4-5)
  - 选最痛的子任务(如 build123d 代码格式)
  - 基模型用 Qwen2.5-Coder-32B 或 DeepSeek-Coder-V2
  - LoRA r=16,bs=8,~5K 步
  - 与 Sonnet 在该子任务上对比 evals
  - **必须不退化才上**,退化就回滚

Phase C · 任务路由(月 6+)
  - 简单子任务路由到自托管模型
  - 复杂 / 创造性任务仍走 Claude
  - 推理成本下降但稳定性优先
```

### 4.6 与 1/2/3 的接口

| 接口 | 给谁 | 内容 |
|---|---|---|
| 数据出口 | 提案 1 task_context / 提案 2 verifier_runs / 提案 3 evals_runs | 未来训练数据全在这三张表 |
| 模型选择策略 | 提案 1 claude_pool spawn | 路由规则的实现位置 |
| 不依赖 | RAG / MCP 治理 / 多 agent | — |

### 4.7 ROI 与优先级:**P2(明确推后)**

- 当下 ROI 估计为负(没基线 + 没数据 + 没基建)
- 满足 §4.3 任一触发条件时再开 RFC

---

## 5. 系统工程架构 — 5 件支撑性基建

### 5.1 OpenTelemetry 全链路追踪(P0)

#### 现状诊断

| 现象 | 证据 |
|---|---|
| LLM 调用看不见 | `agents_v2/shared/claude_pool.py` 有 `log.debug` 但没结构化 trace |
| 跨员工调用看不见 | `delegate_to_employee` 只在自己进程 log,接活方 log 在另一进程 |
| 飞书 callback 链路看不见 | `feishu/sender.py` 发完即忘,响应时延 P95 不可知 |
| `doc/optimization-agentscope.md` 优化 1 ⭐⭐⭐ | 已识别此痛点 |

#### 方案

```python
# agents_v2/shared/otel.py(新)
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc import OTLPSpanExporter

tracer = trace.get_tracer("company-agents")

# 三层 span 命名规范
# - task.<task_id>           顶层 task
#   - employee.<key>          员工 graph 执行
#     - llm.call              单次 claude code 调用
#     - tool.<server>.<name>  MCP 工具调用
#     - rag.retrieve          KB / lessons 检索
#     - verifier.<gate>       提案 2 三闸

# 用法
with tracer.start_as_current_span("employee.mechanical") as span:
    span.set_attribute("task.id", task_id)
    span.set_attribute("delegation.id", delegation_id)
    ...
```

接出到 **Jaeger**(基础设施已有 docker-compose),前端看板加"trace 查看"按钮。

#### 数据导出对接

- `tool_call_log`(本提案 §2)同步导出到 OTLP
- `kb_retrieval_log`(本提案 §1)同步导出到 OTLP
- 提案 2 的 `verifier_runs` 同步导出
- LLM 调用直接走 `langchain_anthropic` 自带 OTel(已支持)

#### 验收

- 任意一个任务 ID 在 Jaeger 能看到完整链路
- LLM 调用的 token 数 / 延迟 / 成本可查
- P0

---

### 5.2 Grafana SLO 看板(P0)

#### 内容

```
仪表板分组:

【Agent 系统健康】
  - 任务"声称完成"vs"真实通过验证"差距率(目标 < 10%)
  - 派活后超 SLA 未推进比例(目标 < 5%)
  - 任务平均迭代轮数(目标下降趋势)

【提案 1 健康】
  - employee_memory 表大小 / 增长
  - delegations.state=in_flight & 超时数
  - prompt header 注入 token 量分布

【提案 2 健康】
  - verifier 三闸通过率 / 拒绝率
  - ground truth checker 平均执行时间
  - human gate 平均等待时间

【提案 3 健康】
  - lessons 命中率(被注入到下一任务的比例)
  - retro_agent 成功率
  - evals 月度趋势(主指标)

【本提案横切】
  - tool_call_log 失败率 / 错误分类
  - kb_retrieval_log 命中率 / 召回质量
  - 推理成本($) / 任务

【系统资源】
  - claude_pool 进程数 / 复用率
  - postgres 慢查询 / 表膨胀
  - GPU(若有) / CPU / 内存
```

#### 数据源

- PostgreSQL 直查(轻量指标 5min 聚合)
- Prometheus(基础设施已有,docker-compose `infra/ai-stack`)
- Jaeger(链路指标)

#### 验收

- CEO 早会能看到"昨日 agent 系统总览一页"
- on-call 触发条件:任一 SLO 红线持续 30min
- P0

---

### 5.3 CI gate(P1)

#### 现状

无 CI、无 PR 自动化、无 evals 卡控。

#### 方案

```yaml
# .github/workflows/agent-ci.yml(或 gitea action)
on: [pull_request]

jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python -m pytest tests/unit -x

  evals_subset:
    runs-on: ubuntu-latest
    steps:
      - run: python -m backend.evals.run --subset=quick --max-cost=5
      # quick subset 5-10 fixtures,跑 5 分钟内
      - run: python -m backend.evals.compare --baseline=main --threshold=-5%
      # 退化超过 5% 就 fail

  mcp_tools_lint:
    runs-on: ubuntu-latest
    steps:
      - run: python scripts/check_mcp_role_bindings.py
      # 任何工具新增必须更新 role_bindings.yaml
```

#### 验收

- PR 不过 evals 子集不能合
- merge 后跑 full evals → 月度报告
- P1

---

### 5.4 沙箱与资源边界(P1)

#### 现状

| 风险 | 证据 |
|---|---|
| `delegate_to_employee` 直接 spawn 子进程 | 无 cgroup,误用 fork bomb 可能 |
| `doc_*` 写文件无路径白名单 | 理论上能写到系统目录 |
| `send_feishu_file` 路径无校验 | 可能上传敏感文件 |

#### 方案

```yaml
# infra/ai-stack/docker-compose.yml 已有 sandbox 容器,扩展:

sandbox:
  image: agent-sandbox:latest
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 4G
  volumes:
    - /tmp/agent-fs:/sandbox  # 唯一可写路径
  cap_drop: [ALL]
  read_only: true
```

每员工 spawn 时,危险工具(派活、写文件、发飞书)走 sandbox 容器代理。

#### 验收

- 故意 fork bomb 测试不影响主机
- 路径越界写文件被拒
- P1

---

### 5.5 灾难恢复(P1)

#### 现状

| 风险 | 现状 |
|---|---|
| postgres 备份 | docker volume 自动 snapshot,**未跨机** |
| claude_pool 进程崩溃 | 自动重启但**任务状态丢失** |
| 飞书 webhook 重放 | 无幂等保证 |

#### 方案

```
1. postgres 跨机备份
   - 每天 02:00 dump 到对象存储(S3/MinIO)
   - 保留 30 天

2. claude_pool 状态恢复
   - 提案 1 task_context 持久化已覆盖任务级
   - 进程崩溃后:supervisor 从 LangGraph checkpoint 重启
   - 同会话 thread_id 唯一,旧子进程死掉不会脏数据

3. 飞书 webhook 幂等
   - 入口加 (event_id, occurred_at) UNIQUE 表,重放直接返回 200
   - 失败重试上限 3 次,过后入死信队列
```

#### 验收

- 拔电模拟 → postgres 数据无丢失,任务从断点续跑
- 飞书重放同一 event_id 100 次 → 仅处理 1 次
- P1

---

### 5.6 与 1/2/3 的接口

| 接口 | 给谁 | 内容 |
|---|---|---|
| OTel | 全部 | 1/2/3 的关键操作都加 span,看板 5.2 消费 |
| Grafana | 全部 | 1/2/3 的 SLI 上墙 |
| CI evals | 提案 3 | evals 子集是 PR gate 的核心 |
| 沙箱 | 提案 2 ground truth checker | checker 在 sandbox 跑 |
| 灾难恢复 | 提案 1 task_context | 备份对象,优先级 P0 |

### 5.7 实施分阶段

| Phase | 时长 | 优先级 | 内容 |
|---|---|---|---|
| **4.5.A** | 1 周 | P0 | OpenTelemetry + Jaeger 接入,关键 span 命名规范 |
| **4.5.B** | 1 周 | P0 | Grafana 看板 v1(只做 SLO 红线 + 成本) |
| **4.5.C** | 3-5 天 | P1 | CI agent-ci.yml,evals 子集卡控 |
| **4.5.D** | 1 周 | P1 | 沙箱容器 + 危险工具代理 |
| **4.5.E** | 3-5 天 | P1 | postgres 跨机备份 + 飞书幂等 |

---

## 6. 整体实施时间表(与 1/2/3 套合)

```
Week 1-2  ┃ 提案 1 上下文+派活        ┃                              ┃
Week 3-4  ┃ 提案 2 验证三闸           ┃ §2 MCP 治理(P0)            ┃ §5.1 OTel(P0)
Week 5-6  ┃ 提案 3 retro+evals 骨架  ┃ §1 RAG L2/L3(P1)           ┃ §5.2 Grafana(P0)
Week 7-8  ┃ 提案 3 模式提取          ┃ §3 Supervisor 落地(P1)      ┃ §5.3-5.5(P1)
Week 9+   ┃ 持续迭代                  ┃ §3 8 员工拆 A2A server      ┃ —
Month 3+  ┃ —                         ┃ —                            ┃ §4 模型训练(P2,看条件)
```

**关键依赖**:
- **§2 MCP 治理 必须先于 §5.1 OTel**:OTel span 要在 MCP 中间件里埋
- **§1 RAG 必须在提案 3 之前或同时**:lesson 召回与 KB 召回共用底座
- **§3 Supervisor 必须在提案 1 派活落地后**:supervisor 是派活状态机的推进者
- **§4 模型训练 必须在提案 3 evals 跑稳后**:否则没基线

---

## 7. 总 ROI 与优先级

| 维度 | 优先级 | 主要收益 | 实施周期 | 阻塞关系 |
|---|---|---|---|---|
| §1 RAG | **P1** | token -30% / 知识可检索 | 2-3 周 | 不阻塞 1/2/3 |
| §2 MCP 治理 | **P0** | 权限可控 / 失败可学 / token -600/启动 | 2 周 | 提案 2 ground truth checker 依赖 |
| §3 多 agent 编排 | **P1** | 流转可见 / 可重启 / 责任清晰 | 3-4 周 | 提案 1 派活落地后 |
| §4 模型训练 | **P2** | 成本下降 / 私有化(长期) | 3-6 月 | 必须等提案 3 evals 跑稳 |
| §5 系统工程 | **P0(.1-.2)/ P1(.3-.5)** | 看得见 / 防误炸 / 防丢数据 | 4 周 | §2 先行 |

**钱花在哪**(假设全做完):
- 一次性:工程师 2 人 × 8 周 = 16 人周
- 持续成本(每月):
  - embedding API ~$30
  - Jaeger / Grafana 自托管 ~$0(已有基础设施)
  - postgres 跨机备份 ~$10
  - 总 < $100/月

---

## 8. 不在本提案范围

为避免范围进一步蔓延,以下话题**不写进本文**:

| 话题 | 处理 |
|---|---|
| 具体业务 prompt 优化 | 各员工 `duties.md` 自维护 |
| 前端看板组件设计 | 见 `frontend/` 已有规划 |
| 飞书 bot 业务功能 | 见 `feishu/` 已有迭代 |
| build123d 工具链具体能力 | 见 `~/work/build123d-parts-lib` |

---

## 9. 决策日志

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-05-26 | 5 维度合并写一篇,不拆 5 篇 | 五维度互引频繁,拆篇导致重复;合篇便于 review 横切关系 |
| 2026-05-26 | 模型训练 30 天内明确不做 | 没评测、没数据、没基建,做了大概率退化且不可知 |
| 2026-05-26 | RAG 用 pgvector 不引专项中间件 | < 10万文档 ivfflat 够用,运维零增量 |
| 2026-05-26 | MCP 拆 5 个 server 而非 9 个(按员工) | 同一 server 多员工共享,降运维成本;权限靠白名单不靠拆 |
| 2026-05-26 | Supervisor 只做编排不做领域决策 | 责任边界混乱是多 agent 系统最大坑,先把边界划清 |

---

## 10. 阅读建议

- **30 分钟版**:看 §0 + §6 时间表 + §7 ROI 表
- **决策版(投不投资)**:加看 §1.9 / §2.9 / §3.8 / §4.7 / §5.7 各节优先级理由
- **实施版(怎么做)**:按 §1 → §2 → §5(P0 部分)→ §3 → §5(P1 部分)→ §4 顺序通读

