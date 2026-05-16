# 定时任务系统设计

> 版本：v0.3 · 2026-05-16
> 状态：已实现，持续迭代中

---

## 一、系统定位

每个 AI 员工都可以拥有自己的定时任务：系统巡检、每日站会播报、周期性提醒、一次性事项等。任务由员工自己（或 CEO）创建，配置持久化在 DB，agent 进程内嵌调度器驱动执行。

```
CEO / 员工
  │  "5分钟后提醒我吃饭" / "每天9点发站会"
  ▼
Agent（LLM）
  │  调用 schedule_task 工具
  ▼
Backend API → PostgreSQL
  │  behavior.scheduled_tasks[]
  ▼
AgentScheduler（每个 agent 进程内嵌）
  │  cron loop / delay sleep
  ▼
Agent（LLM 执行）
  │  思考 + 润色 + 调用 send_feishu_message / send_group_chat_message
  ▼
飞书 / 看板群聊
```

---

## 二、任务数据结构

每个任务是 `employee.behavior.scheduled_tasks[]` 数组中的一个 JSON 对象：

```json
{
  "id": "a2aa4f0b",
  "name": "提醒做周报",
  "prompt": "现在是周五下午，请生成本周项目进度周报并发送",
  "output_to": "feishu",
  "enabled": true,
  "once": false,

  // 触发方式（二选一）
  "cron": "0 17 * * 5",
  "delay_seconds": 300,

  // 一次性任务的绝对触发时间（由 schedule_task 工具自动写入）
  "fire_at": "2026-05-16T09:25:00+00:00",

  // 元数据
  "created_at": "2026-05-16T09:20:00+00:00",
  "created_by": "project_manager"
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 8位随机 hex，任务唯一标识 |
| `name` | string | 任务名，展示和日志用 |
| `prompt` | string | 触发时发给 agent 的指令，可以是提醒内容或复杂任务描述 |
| `output_to` | string | `feishu` / `group_chat` / `kanban` / `log` |
| `enabled` | bool | 是否启用，不删除地暂停任务 |
| `once` | bool | 执行一次后自动从 DB 删除 |
| `cron` | string | cron 表达式，周期任务 |
| `delay_seconds` | int | 相对延迟秒数（创建参考用） |
| `fire_at` | ISO datetime | 一次性任务的绝对触发时间，scheduler reload 后保持精确 |
| `created_at` | ISO datetime | 任务创建时间 |
| `created_by` | string | 创建该任务的员工 key |

---

## 三、架构分层

```
┌─────────────────────────────────────────────────────────────┐
│  工具层（Agent 可调用）                                      │
│  schedule_task · cancel_scheduled_task · list_scheduled_tasks│
│  send_feishu_message · send_group_chat_message               │
│  run_command · read_file · write_file · get_metrics          │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTP API
┌───────────────────────▼─────────────────────────────────────┐
│  后端 API（backend/api/routes/employees.py）                 │
│  GET/POST/PATCH/DELETE /api/employees/{key}/scheduled-tasks  │
│  → 操作 employee.behavior.scheduled_tasks JSON 数组          │
│  → registry.update() → PG NOTIFY config_changed             │
└───────────────────────┬─────────────────────────────────────┘
                        │ PG NOTIFY / /scheduler/reload
┌───────────────────────▼─────────────────────────────────────┐
│  AgentScheduler（每个 agent 进程内嵌一个实例）               │
│                                                              │
│  _loops: {task_id → asyncio.Task}                            │
│  _status: {task_id → {last_run, last_result, next_run, ...}} │
│                                                              │
│  start()   → 从 API 拉取任务，启动 cron loop                │
│  reload()  → 差量更新（只增/删，不重置正在等待的任务）       │
│  stop()    → 取消所有 loop                                  │
│  run_once()→ 立即执行一次（用于调试）                        │
└───────────────────────┬─────────────────────────────────────┘
                        │ agent_fn(prompt, context)
┌───────────────────────▼─────────────────────────────────────┐
│  Agent 执行（LangGraph SmartGraph）                          │
│                                                              │
│  scheduler 注入上下文：                                      │
│    【定时任务触发】任务名                                    │
│    【推送目标】feishu — 请用 send_feishu_message 发送        │
│    【注意】按你的性格润色；发送失败换备用渠道并说明          │
│                                                              │
│  agent 自主决策：                                            │
│    → 思考任务内容                                            │
│    → 调用工具（get_metrics / run_command 等）                │
│    → 按角色性格润色消息                                     │
│    → 调用 send_feishu_message / send_group_chat_message      │
│    → 发送失败时重试或切换备用渠道                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 四、执行流程

### 4.1 创建任务

```
用户：「5分钟后提醒我吃饭」
  ↓
芳芳（LLM）调用 schedule_task(
  name="吃饭提醒",
  prompt="午餐时间到了！记得按时吃饭，下午才有精力～",
  delay_minutes=5,
  output_to="feishu"
)
  ↓
工具写入 DB：{id, name, prompt, fire_at=now+5min, delay_seconds=300, ...}
  ↓
_trigger_scheduler_reload() → POST /scheduler/reload
  ↓
scheduler.reload()（差量：新增该任务的 cron loop，其他任务不受影响）
```

### 4.2 触发执行

```
asyncio.sleep(remaining)  ← 等到 fire_at
  ↓
scheduler._execute()
  ↓
注入上下文 + 原始 prompt → agent_fn()
  ↓
LLM 思考：这是一个吃饭提醒，output_to=feishu，用 send_feishu_message
  ↓
调用 send_feishu_message(
  content="午饭时间到啦！🍚 记得好好吃饭补充能量，下午加油～",
  title="吃饭提醒"
)
  ↓
工具返回 "已成功发送到飞书群"
  ↓
scheduler 记录 last_run, last_result
  ↓
once=True → _auto_delete(task_id)
```

### 4.3 配置热更新链路

```
DB 变更（API PATCH）
  ↓
registry.update() → employee_repo.update_fields()
  ↓
DB trigger: trg_employee_change → pg_notify('config_changed', payload)
  ↓
registry._listen_loop → await invalidate(key)
  ↓
_on_config_change hook → scheduler.reload()（差量更新）
```

---

## 五、工具全景

### 5.1 所有员工自动获得（无需配置）

| 工具 | 功能 |
|------|------|
| `schedule_task` | 创建定时任务或一次性提醒 |
| `cancel_scheduled_task` | 取消/删除任务 |
| `list_scheduled_tasks` | 查看任务列表（含剩余时间/上次执行/下次执行） |
| `send_feishu_message` | 发送到飞书群，失败返回错误原因 |
| `send_group_chat_message` | 发送到看板群聊，可作为飞书的备用渠道 |

### 5.2 按需配置（behavior.tools）

| 工具 | 功能 |
|------|------|
| `run_command` | 执行 shell 命令（巡检/重启服务/跑测试） |
| `read_file` | 读取文件（日志/代码/配置） |
| `write_file` | 写入文件 |
| `get_metrics` | 获取实时系统指标（CPU/内存/磁盘/进程） |

---

## 六、已知问题与待改进点

### 6.1 已修复

- ✅ `reload()` 风暴杀死计时任务（改为差量更新）
- ✅ `CancelledError` 被吞导致 "Task was destroyed but pending"
- ✅ `FEISHU_CHAT_ID` 读不到（`os.getenv` → `settings`）
- ✅ Scheduler 使用 `asyncpg` 冲突（改用 HTTP API）
- ✅ Agent 不知道自己有哪些工具（`_tools_hint` 注入系统提示）
- ✅ 一次性任务 reload 后重置倒计时（`fire_at` 绝对时间）
- ✅ `task_type=reminder` 绕过 LLM（撤回：提醒也需要思考+润色）

### 6.2 待讨论的改进点

#### P1 · 执行可靠性

| 问题 | 现状 | 建议方向 |
|------|------|---------|
| **进程重启丢任务** | agent 重启后 scheduler 重建，delay 任务靠 `fire_at` 恢复，但 cron 任务若恰好在重启窗口内可能漏执行 | 记录 `last_run` 到 DB，重启后对比判断是否需要补跑 |
| **执行无幂等保证** | 同一任务可能因 reload 被重复触发 | 执行前写 DB lock / 执行记录，防止重复 |
| **长任务无超时** | `agent_fn` 无超时限制，监控任务可能跑几分钟不结束 | 加 `asyncio.wait_for(agent_fn(...), timeout=cfg.get("timeout_s", 300))` |

#### P2 · 可观测性

| 问题 | 现状 | 建议方向 |
|------|------|---------|
| **执行历史不持久** | `last_run`/`last_result` 只在内存 `_status`，重启丢失 | 写入独立 `scheduled_task_run` 表（task_id, started_at, finished_at, result, status） |
| **无执行日志 UI** | 只能看 log 文件 | 前端任务详情页加执行历史 tab |
| **send 成功/失败不可见** | agent 工具调用结果在 LLM 内部，外部看不到 | 工具调用结果写入执行记录 |

#### P3 · 功能完整性

| 问题 | 现状 | 建议方向 |
|------|------|---------|
| **飞书 chat_id 固定** | `FEISHU_CHAT_ID` 是全局配置，所有提醒发同一个群 | 支持按任务指定 `feishu_chat_id`，或支持发私聊 |
| **无重试机制** | 发送失败依赖 agent 自己判断重试 | `_execute` 层加 retry wrapper，agent 失败后自动重试 N 次 |
| **cron 时区问题** | `croniter` 使用服务器本地时间 | 明确写 UTC，或允许任务指定时区 |
| **任务无优先级/并发限制** | 所有任务并发执行，多个重型任务同时触发会打爆 | 加 semaphore 限制同时运行任务数 |

#### P4 · 设计层面

| 问题 | 现状 | 建议方向 |
|------|------|---------|
| **`output_to` 的角色混乱** | 创建时指定，但 agent 可以自己决定发哪里 | `output_to` 改为"推荐渠道"，agent 有权切换 |
| **Scheduler 与 agent 进程耦合** | 每个 agent 进程内嵌 scheduler，agent 挂了调度也挂了 | 考虑独立调度进程（celery beat / APScheduler），agent 只负责执行 |
| **无跨员工任务依赖** | 任务相互独立，无法表达"A 完成后触发 B" | 任务 DAG / 事件触发机制 |

---

## 七、相关文件索引

| 文件 | 职责 |
|------|------|
| `agents_v2/shared/scheduler.py` | AgentScheduler 实现 |
| `agents_v2/shared/tools.py` | 工具定义（schedule_task、send_* 等） |
| `agents_v2/generic/main.py` | lifespan 集成、工具注入、send 实现 |
| `backend/api/routes/employees.py` | 定时任务 CRUD API |
| `backend/services/registry.py` | 配置热更新、change hooks |
| `backend/core/config.py` | FEISHU_CHAT_ID 等环境配置 |
