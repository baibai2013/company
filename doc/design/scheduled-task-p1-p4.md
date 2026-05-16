# 定时任务系统 · P1 & P4 详细设计

> 版本：v1.0 · 2026-05-16

---

## P1 执行可靠性

### 1.1 进程重启后 cron 任务漏执行

**现状**：`last_run` 只在 `AgentScheduler._status` 内存字典，进程重启后清零。`_cron_loop` 重建时从当前时刻计算下次触发，落在重启窗口内的那次执行永久丢失。

**解决方案**：每次 `_execute` 完成后将 `last_run_at` 写回 DB（PATCH 接口已有），`_cron_loop` 启动时做一次补跑判断。

**补跑逻辑**（保守策略，只补最近一次，避免重启后雪崩）：

```
_cron_loop 启动
  ↓
读取 cfg["last_run_at"]（DB 持久化值）
  ↓
计算上一个应触发时刻 prev_trigger = croniter.get_prev()
  ↓
if last_run_at < prev_trigger - 容忍窗口(60s):
    立即执行一次（补跑）
  ↓
正常进入等待循环
```

**任务配置新增字段**：

```json
{
  "last_run_at": "2026-05-16T09:00:00+00:00",
  "last_run_status": "success | error | timeout"
}
```

---

### 1.2 无幂等保证

**现状**：`reload()` 使用 `if tid in old_ids: continue` 跳过已有任务，理论上防重建，但以下场景会双触发：
- 配置更新导致任务 id 重新生成（前端改了 id 字段）
- 极短时间内两次 reload 竞争

**解决方案**：两层防护

**层 1 — 运行时 skip（已有，利用 asyncio 单线程）**：
```python
# _execute 入口
if self._status[task_id]["running"]:
    log.warning("task %s already running, skipped", task_id)
    return "SKIPPED"
```

**层 2 — DB 乐观锁**：执行前 PATCH `last_run_at` 为当前时间，若 PATCH 返回冲突（版本号不符）说明另一进程已抢先执行，放弃本次。适用于未来多副本部署场景，当前单进程下层 1 已足够。

**约束**：routes/employees.py 的 scheduled-tasks POST 接口必须由后端生成 id 且不允许客户端覆盖，当前已正确实现。

---

### 1.3 长任务无超时

**现状**：`agent_fn()` 调用完整 LangGraph ReAct loop，一次 LLM 调用挂起可以阻塞 asyncio event loop 数小时，同进程所有其他定时任务全部停摆。

**解决方案**：

```python
# scheduler._execute 内
timeout_s = cfg.get("timeout_seconds", 300)
try:
    result = await asyncio.wait_for(
        self.agent_fn(full_prompt, context),
        timeout=timeout_s,
    )
except asyncio.TimeoutError:
    result = f"任务超时（>{timeout_s}s），已中止"
    # 写 last_run_status = "timeout"
```

**任务配置新增字段**：

```json
{ "timeout_seconds": 300 }
```

**分级默认值建议**：
- 提醒/发送类：60s
- 系统巡检：300s（默认）
- 报告生成：600s
- 不限制：设 0（`wait_for` 不包装）

---

### 1.4 执行历史不持久

**现状**：执行记录在内存，重启丢失，无法回查失败原因和执行时长。

**解决方案**：新增 `agent_task_runs` 表，`_execute` 完成后异步 INSERT（不阻塞任务循环）。

**表结构**：

```sql
CREATE TABLE agent_task_runs (
    id              BIGSERIAL PRIMARY KEY,
    employee_key    VARCHAR(64)  NOT NULL,
    task_id         VARCHAR(64)  NOT NULL,
    task_name       VARCHAR(256),
    triggered_at    TIMESTAMPTZ  NOT NULL,
    finished_at     TIMESTAMPTZ,
    duration_ms     INT,
    status          VARCHAR(16)  NOT NULL,  -- success / error / timeout / skipped
    result_text     TEXT,                   -- 截断 2000 chars
    output_to       VARCHAR(64),
    execution_mode  VARCHAR(32)             -- agent / direct_api / webhook
);

CREATE INDEX ON agent_task_runs (employee_key, task_id, triggered_at DESC);
```

**保留策略**：每个任务保留最近 200 条，定期 DELETE 超出部分（后台任务每天跑一次）。

---

## P4 架构设计

### 4.1 Scheduler 与 Agent 进程耦合

**现状问题**：

```
Agent 进程
├── uvicorn HTTP server
├── LangGraph SmartGraph
└── AgentScheduler          ← 调度和执行绑定在一起

问题：agent 崩溃 = 调度崩溃；无中心调度节点；无法做跨员工任务依赖
```

**近期方案（方案 A）— 原地强化，零新进程**：

保持嵌入架构，但解决状态孤立问题：将执行状态持久化到 DB（P1 已解决），进程重启后自愈。跨员工触发通过 HTTP A2A 调用实现（基础设施已有）。

适用条件：任务总数 < 50，无复杂跨员工依赖，可接受进程崩溃后最多漏一次 cron。

**中期方案（方案 B）— 独立调度服务**：

当以下任一条件成立时升级：
- 任务总数 > 50
- 需要跨员工 DAG 依赖
- 需要任务级 SLA 保障（不允许漏执行）

```
┌──────────────────────────────────────────────────────┐
│  SchedulerService（独立进程，无 LLM）                 │
│                                                       │
│  职责：cron 评估 / 依赖解析 / 任务分发 / 执行记录     │
│  不做：LLM 调用 / 工具执行                           │
│                                                       │
│  数据源：PostgreSQL scheduled_tasks 表（独立，非 JSON）│
│  分发方式：HTTP POST agent:900x/scheduler/execute     │
│  状态存储：agent_task_runs 表                         │
└─────────────────────┬────────────────────────────────┘
                      │ HTTP
            ┌─────────┴──────────┐
            ▼                    ▼
      Agent-A (900x)       Agent-B (900y)
      仅负责执行            仅负责执行
      LangGraph + Tools     LangGraph + Tools
```

**本文聚焦方案 A 的强化设计**，方案 B 的迁移路径在最后说明。

---

### 4.2 output_to 语义重新设计

**现状混乱**：

```
output_to 同时承担两个角色：
  1. 建议渠道 → prompt hint 告知 agent 用哪个工具发送
  2. 强制渠道 → 早期 output_fn 直接发送（已废弃）

结果：语义不清，agent 可能忽略 output_to，发到其他地方
```

**新设计**：拆分为三个字段，职责单一：

```json
{
  "output_to": "feishu",           // 主渠道（建议，agent 可覆盖）
  "output_fallback": "group_chat", // 主渠道失败时自动切换（agent 工具层保证）
  "output_required": false         // true = scheduler 校验 agent 确实发送了
}
```

**`output_required = true` 的兜底机制**：

```
agent_fn() 执行完毕
  ↓
scheduler 检查 agent 的 tool_calls 列表
  ├── 包含 send_feishu_message 或 send_group_chat_message？
  │   └── YES → 记录成功，完成
  └── NO → agent 忘记发送了？
      ↓
      scheduler 直接调用 output_fn 兜底发送
      记录 status = "fallback_sent"
```

**send 工具的 fallback 链已内置**：

```python
send_feishu_message():
    失败 → 返回错误描述，agent 看到后可调用 send_group_chat_message

# 工具描述中已写明：
# "发送失败时可改用 send_group_chat_message"
```

---

### 4.3 跨员工任务依赖（DAG）

**需求**：
- 数据工程师完成 ETL → 触发算法工程师跑模型
- 项目经理发完周报 → 技术负责人自动发技术评审
- 任意员工任务失败 → 通知系统管理员

**设计原则**：不引入 Airflow/Prefect 等重型 DAG 引擎，用现有 HTTP A2A 基础设施实现。

**数据结构**：

```json
{
  "id": "run-model",
  "name": "跑推荐模型",
  "trigger": {
    "type": "event",
    "event_type": "task_completed",
    "filter": {
      "employee": "data_engineer",
      "task_id": "etl_daily",
      "status": "success"
    }
  },
  "prompt": "ETL 完成，开始跑推荐模型训练..."
}
```

**执行链路**：

```
data_engineer Agent
  ↓ etl_daily 任务完成
  ↓ _execute() 写 agent_task_runs 表
  ↓
  POST /api/scheduler/events
  body: { event_type: "task_completed", employee: "data_engineer",
          task_id: "etl_daily", status: "success" }
  ↓
Backend 查询 所有员工中 trigger.event_type="task_completed" 的任务
  ├── 匹配 filter？
  └── YES → POST algorithm:9004/scheduler/event
              body: { matched_task_cfg }
              ↓
              AgentScheduler._event_handlers["task_completed"]()
              ↓
              立即执行 "跑推荐模型" 任务
```

**新增 API 端点**：

```
POST /api/scheduler/events          # 任务完成后发事件
GET  /api/scheduler/event-subscribers  # 查询谁订阅了哪些事件
```

**Agent 进程新增端点**：

```
POST /scheduler/event    # 接收事件，触发对应任务
```

**DAG 深度限制**：最多 3 跳（A → B → C → D 不允许），防止循环依赖，超出时报错拒绝创建。

---

### 4.4 事件驱动触发

**现状**：所有触发均为时间驱动（cron/delay），事件驱动只能靠轮询模拟，有延迟且浪费 token。

**Trigger 类型统一化**：

```json
{
  "trigger": {
    "type": "cron | delay | event | webhook",

    // cron
    "cron": "0 9 * * 1-5",

    // delay（一次性）
    "delay_seconds": 300,
    "fire_at": "2026-05-16T09:25:00+00:00",

    // event（跨员工依赖 / 系统事件）
    "event_type": "task_completed | feishu_message | file_changed | api_call",
    "filter": { "任意 KV 过滤条件": "..." },

    // webhook（外部系统推送触发）
    "webhook_token": "随机 token，验证来源合法性"
  }
}
```

**事件来源列表**：

| event_type | 来源 | 说明 |
|------------|------|------|
| `task_completed` | scheduler | 另一任务完成 |
| `task_failed` | scheduler | 另一任务失败 |
| `feishu_message` | feishu bot | 收到指定飞书消息 |
| `file_changed` | watchdog（可选） | 文件系统变化 |
| `api_call` | 外部系统 | POST /scheduler/event |
| `manual` | CEO/员工 | 手动触发 |

**向前兼容**：无 `trigger` 字段时，按原 `cron`/`delay_seconds` 字段处理，现有任务无需迁移。

---

### 4.5 执行模式扩展

**现状**：所有任务都走完整 LangGraph SmartGraph（route → plan → ReAct loop），"发一条固定问候"这类简单任务每次消耗 1000+ tokens，且 LLM 有时忘记调用 send 工具。

**执行模式分类**：

```json
{ "execution_mode": "agent | direct | webhook" }
```

#### mode: agent（默认，当前实现）

```
scheduler → agent_fn() → SmartGraph → LLM 思考 → 调工具 → 发送
```

适用：需要 LLM 判断、工具调用、内容生成的复杂任务。

#### mode: direct（新增）

```
scheduler → 直接调用工具，跳过 LLM
```

适用：固定文案推送、已知结论的定期发送，token 消耗为零。

```json
{
  "execution_mode": "direct",
  "direct_actions": [
    { "tool": "get_metrics", "args": {} },
    { "tool": "send_feishu_message",
      "args": { "content": "{{get_metrics.result}}", "title": "系统指标" } }
  ]
}
```

变量插值：`{{工具名.result}}` 引用上一个动作的输出。

#### mode: webhook（新增）

```
scheduler → HTTP POST 外部 URL
```

适用：触发外部系统（CI/CD、数据管道等），不经过 LLM。

```json
{
  "execution_mode": "webhook",
  "webhook_url": "https://ci.example.com/trigger/build",
  "webhook_method": "POST",
  "webhook_headers": { "Authorization": "Bearer xxx" },
  "webhook_body": { "ref": "main", "triggered_by": "scheduler" }
}
```

---

## 完整数据结构（v2）

```json
{
  "id": "sys-monitor",
  "name": "系统巡检",

  // ── 触发 ──────────────────────────────────
  "trigger": {
    "type": "cron",
    "cron": "*/30 * * * *"
  },

  // ── 执行 ──────────────────────────────────
  "execution_mode": "agent",
  "prompt": "检查所有服务端口和系统指标，发现异常立即告警",
  "timeout_seconds": 300,

  // ── 输出 ──────────────────────────────────
  "output_to": "feishu",
  "output_fallback": "group_chat",
  "output_required": true,

  // ── 状态（持久化） ────────────────────────
  "enabled": true,
  "once": false,
  "last_run_at": "2026-05-16T09:30:00+00:00",
  "last_run_status": "success",

  // ── 元数据 ───────────────────────────────
  "created_at": "2026-05-16T09:00:00+00:00",
  "created_by": "sysadmin"
}
```

---

## 架构演进路线

```
现在（v0.3）                    近期（v1.0）                  中期（v2.0）
────────────────────           ─────────────────────         ──────────────────────
嵌入 agent 进程                强化嵌入 + 持久化              独立 SchedulerService
last_run 内存存储       →      agent_task_runs 表     →      多进程协调
纯 cron/delay 触发     →      +event 触发             →      完整 DAG 引擎
单一 agent 执行模式    →      direct/webhook 模式     →      分布式执行
output_fn 已废弃       →      output_required 校验    →      SLA 保障
```

---

## 方案 B 迁移检查表（未来参考）

当以下任一触发时开始迁移：

- [ ] 单员工定时任务 > 10 个
- [ ] 跨员工 DAG 深度 > 3 层
- [ ] 要求任务级 SLA（漏执行率 < 0.1%）
- [ ] 需要任务执行的水平扩展

迁移时：
1. 将 `behavior.scheduled_tasks[]` 迁移到独立 `scheduled_tasks` 表
2. 提取 `SchedulerService`，保留 `/scheduler/execute` 接口兼容
3. Agent 进程保留 `/scheduler/event` 端点，改为被动接收
4. 不改变 LangGraph / 工具 / send 机制（执行侧不变）
