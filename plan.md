# Company System v2 — 现状对齐路线图

**版本：** v2.0
**日期：** 2026-05-19
**关联文档：** [ARCHITECTURE.md](./ARCHITECTURE.md) · [ARCHITECTURE_LANGGRAPH_A2A.md](./ARCHITECTURE_LANGGRAPH_A2A.md)
**前作：** v1.0 已存档于 git 历史（`git show HEAD~1:plan.md`）。v1 是 "LangGraph + A2A，八员工独立部署 + TechLead :9000 supervisor" 蓝图，实际实现已**完全偏离**该路径。本文是与代码现状对齐的版本。

---

## 0. 一句话现状

> 基础设施 / Backend API / 通用 Agent / smart_graph 路由 / Claude Code CLI 后端 / 飞书 cc_bridge 双卡片进度 / 前端看板 / pgvector 长期记忆 / 跨员工 delegate / 定时任务 / sandbox / MCP server **已上线**；
> **没有**统一的多员工编排管线、**没有**端到端业务 demo（CEO 一句话 → CAD/固件/算法 三件套产出）、**没有** Pipeline 步骤可视化、**没有** v0.1.0 tag。

---

## 1. 实际架构（与 v1 plan 已偏离的部分用 ⚠ 标注）

```
                          ┌───────────────────────────────────┐
                          │  飞书群 / 飞书单聊                │
                          └───────────────┬───────────────────┘
                                          │ WebSocket（lark sdk）
                                          ▼
       ┌──────────────────────────────────────────────────────────────────┐
       │  feishu/                                                          │
       │  ├ bot.py            主 bot（绑公司 app）                          │
       │  ├ employee_bot.py   每员工独立 bot（员工自己 app_id 时启动）     │
       │  ├ cc_bridge/        ⚠ v1 没有：飞书 ↔ Claude Code CLI 直通 + 进度卡 │
       │  └ commands/         slash 命令分发                                │
       └────────────────┬─────────────────────────────────────────────────┘
                        │ HTTP (POST /run, /api/...)
                        ▼
            ┌─────────────────────────────────────────────┐
            │  Backend API :8000  (uvicorn --reload)       │
            │  backend/main.py + api/routes/*               │
            │   tasks  employees  events(SSE)  chat         │
            │   audit  llm_stats  system_config             │
            │   chat/ws.py (kanban WebSocket)               │
            └────┬────────────────────────────┬───────────┘
                 │ A2A HTTP (jsonrpc)         │ scheduler/event
                 ▼                            │
   ┌──────────────────────────────────────────┴──────────────┐
   │  agents_v2/generic/main.py  ⚠ v1 是 8 个独立目录；现在一份代码 │
   │  + EMPLOYEE_KEY 启动；端口、prompt、tools、cwd 全部从 DB registry │
   │                                                                 │
   │  内嵌：                                                         │
   │  • smart_graph (route → CHAT / WORK)                            │
   │      ├ exec_backend = "cc"   →  claude code CLI 子进程          │
   │      │     带 Bash/Read/Edit/Grep/TodoWrite + MCP company_tools │
   │      │     + sandbox-exec + acceptEdits + session resume        │
   │      └ exec_backend = "langchain" → react_node (legacy fallback)│
   │  • scheduler (cron + event-triggered + run-once)                │
   │  • cc_node (走完后判断要 @ 谁补充意见)                          │
   │  • registry NOTIFY listener (DB 改 → 进程内 hot reload)         │
   └─────────────────────────────────────────────────────────────────┘
                 │
                 ▼
   ┌─────────────────────────────────────────┐
   │  PostgreSQL (pgvector)                   │
   │   company_app   — task / message / chat /│
   │                   employee / memory /    │
   │                   audit / llm_call       │
   │   company_langgraph — checkpointer       │
   │  Redis  — task_events / progress / gate  │
   │  MCP server — company_tools (6 tools)    │
   └─────────────────────────────────────────┘

前端 :5173  Vue 3 + Pinia + Element Plus
   views/ : Home / Dashboard / Employees / EmployeeDetail / EmployeeNew / SystemConfig
   ⚠ v1 plan 里的 TaskBoard / Pipeline / GroupChat / DirectChat 有功能等价物嵌入 Dashboard，
     但**没有**独立的 task_step 链路可视化视图。
```

**没了的东西（与 v1 plan 比）：**

| v1 plan 中存在 | 现状 |
|---|---|
| `agents_v2/{mechanical,hardware,...}/` 8 个独立目录 | 全部坍缩成 `agents_v2/generic/main.py`（fc28d4e、c48959a 等） |
| `agents_v2/tech_lead/main.py` :9000 supervisor | 已删除；编排意图分散到 smart_graph + delegate_to_employee + group_chat |
| 每员工独立 `agent_card.json` | 模板生成 → `tempfile/agent_card_<key>.json` |
| `POST :9000/pipeline` 接口 | 不存在；CEO 触发任务的统一入口缺失 |
| Frontend Pipeline 视图 / Chat 独立视图 | 未建（功能融入 Dashboard，但 step 链路不可视） |
| `v0.1.0-mvp` tag | 未打 |

---

## 2. 已完成（不必再做）

### Phase A0 — 基础设施 ✅
- `infra/docker-compose.yml`：pgvector/pg16 + redis/7 + gitea/1.22 + mattermost/9.11 + n8n/1.56
- `infra/postgres/init.sh`：`company_app` + `company_langgraph` 双库
- 4 个 alembic 迁移已落库（init_task_and_chat / employee_system_config_audit_log / employee_memory / embedding_to_employee_memory）

### Phase A1 — shared 基础库 ✅（远超 v1 plan 范围）
`agents_v2/shared/` 共 17 个模块：
- v1 plan 内：`claude_client.py` `db.py` `claude_runner.py` `a2a_server.py`
- v1 plan 外：`cc_executor.py` `cc_oneshot.py` `claude_pool.py` `sandbox.py` `mcp_config.py` `scheduler.py` `smart_graph.py` `prompt_watcher.py` `hot_reload_receiver.py` `employee_workspace.py` `ownership.py` `runner.py` `tools.py` `device_registry.py`

### Phase A2 — Backend API ✅（远超 v1 plan 范围）
- Models: `task` / `message` / `employee` / `memory`（带 embedding）/ `audit` / `llm_call`
- Routes: `tasks` `employees`(398 行) `events`(SSE) `chat` `audit` `llm_stats` `system_config`
- Services: `registry`（DB-driven 配置 + PG NOTIFY hot reload）+ `process_manager`
- Repos: `memory_repo`(语义检索) / `config_repo` / `employee_repo` / `llm_call_repo` / `audit_repo`
- Tests: 7 个测试文件（chat / kanban_ws / memory / tasks / business / platform / werewolf）
- WebSocket 看板聊天：`backend/chat/ws.py`

### Phase A3 — Generic Agent + smart_graph ✅
- 一份 `agents_v2/generic/main.py`（334 行）服务任意员工，DB 配置驱动
- `smart_graph.build_smart_agent`：route → CHAT / WORK 两路径
- 双后端：**cc**（默认主路径，claude code CLI + sandbox + MCP）/ **langchain**（fallback）
- WORK 路径在 cc 后端下**已删除 plan 节点**（commit 60abf6b：让 claude 用 TodoWrite 自己规划）
- cc_node：走完后判断要邀请哪些专家补充

### Phase A4 — 飞书 ✅（远超 v1 plan 范围）
- `feishu/bot.py` + `employee_bot.py`（每员工可绑自己飞书 app）
- `feishu/cc_bridge/`：飞书 ↔ Claude Code CLI 直通 + 双卡片（thinking 流 + 进度 / 结果）
- TodoWrite/TaskCreate/TaskUpdate 聚合成 todo 列表（commit 2c01d3d）
- Slash 命令分发 + 多话题路由 + 群聊 + P2P 单聊 + 长期记忆

### Phase A5 — 看板前端 ⚠（部分完成）
- ✅ Dashboard / Employees / EmployeeDetail / EmployeeNew / SystemConfig
- ✅ WebSocket 群聊看板
- ❌ task_step 链路 Pipeline 视图
- ❌ 独立的 GroupChat / DirectChat 视图

### Phase A6 — 跨员工编排片段 ✅
- `delegate_to_employee` 工具（commit c07203e）
- `agents_v2/shared/scheduler.py`：cron + event-triggered + run-once
- 跨员工 PG NOTIFY 事件路由
- group_chat 框架（orchestrator / scenarios / pipelines / games — 含狼人杀场景测试）

---

## 3. 关键决策记录（v1 → v2 的 pivot）

| 决策 | 时间线（commit） | 理由 |
|---|---|---|
| 8 个员工目录 → 1 份 generic + DB 驱动 | fc28d4e、c48959a | 新员工不用复制目录、改代码；DB 改即生效（PG NOTIFY hot reload） |
| TechLead :9000 supervisor 删除 | fc28d4e | "改走 generic + DB"；代价：失去统一编排入口（见下方 B1） |
| WORK 路径切 claude code CLI | 7e89a5c（阶段 6） | langchain bind_tools 工具支持弱、context 污染、并发抖动；CLI 自带 TodoWrite + Bash/Read/Edit + 沙箱 |
| 删除 langchain plan 节点（cc 后端） | 60abf6b、9.6 阶段 | "闭眼出方案没工具支撑"；让 claude 自己 TodoWrite 内化规划 |
| 删除 claude code 热进程池 | a2ab565（阶段 11） | 池冷启抖动 > 单次 CLI 启动开销；改回轻量一次性进程 |
| chat 路径也切 claude code CLI | 00c8e31 | langchain react_chat 多次"过度工具化"和"历史污染" |
| 飞书直通 cc_bridge | 2a13a29 起 | bot ↔ Claude Code 直连，绕开 LangGraph，给特定流量更快路径 |
| 编排能力接 group_chat,不再造 supervisor | B1 设计期 | `group_chat/orchestrator.py` 已有 receive/decide/dispatch/conclude DAG + scenario 注册表,80% 能力已存在;入口用 chat_id="task:{id}" 合成 MessageEvent 复用现有路径 |

---

## 4. 剩余路线图

### Phase B1 — 编排能力补齐 🔥 P0

**详细设计：** [doc/tasks/B1-orchestration.md](./doc/tasks/B1-orchestration.md)

**问题陈述:** v1 plan 设想"CEO 一句话 → :9000 拆解 → 八员工 A2A 委派 → 收集结果"。当前现实是 `:9000` 不存在,编排逻辑分散在 `smart_graph` / `delegate_to_employee` / `group_chat` 三处,且没有代码路径把"任务拆解 → DAG 调度 → 聚合"端到端跑通。`task_step` 表存在但无人写。

**Task B1.1 — 选型决策** ✅
- 选定**方案 C**:把 `group_chat/orchestrator.py` 接到 Task 模型(详见 doc §2)
- 方案 A 重复造轮子;方案 B 不能可视化(DAG 在 prompt 里)

**Task B1.2 — task_step 写入打通** ✅
- 新增 `group_chat/task_steps.py`:`step_record` 上下文管理器 + `extract_task_id` + `mark_task_status` helpers
- `group_chat/orchestrator.py` 的 receive/decide/dispatch/conclude 4 个 node 各包一层 `step_record`
- `group_chat/pipelines.py` 的 `sequential` / `fanout` 在每个 employee speak 前写 `step_name="speak:{employee}"`
- `_conclude_node` 在 `chat_id=task:*` 时保留 session(不 delete)
- 新增 `GET /api/tasks/{task_id}/steps` 路由 + `TaskStepRead` 加 `input` / `duration_ms`
- **验证:** `pytest backend/tests/test_task_step_writes.py` 6 个用例全绿;`backend/tests/test_tasks.py` 18 个用例(含 T-A17/T-A18 GET /steps)全绿

**Task B1.3 — e2e 腿部建模** ✅(fake) / ⏸(真版手跑)
- 新增 `backend/services/orchestration_bridge.py`:`trigger_for_task` publish 到 Redis `group_msg:task:{id}`
- `POST /api/tasks` 末尾自动触发桥(失败仅 log,不阻断)
- 新增 `group_chat/scenarios/robot_engineering.py`:product_manager → fanout(机械/固件/算法) → cost
- `_decide_node` 顶部加关键词识别(机器狗/四足/腿部/...)→ 跳过 LLM 走 robot_engineering scenario
- `_receive_node` 推 task `pending → in_progress`;`_conclude_node` 推 `→ done`
- 新增 `scripts/e2e_leg_demo.sh` 真版本地脚本(30 分钟,跑真 claude CLI)
- **验证:** `pytest backend/tests/test_orchestration_e2e.py` 2 个用例全绿(含 7 个 task_step 行 + 5 speakers + task.status=done);Redis pub/sub 烟雾通过;真版待本地手跑

输入(飞书 / API 任意一个):
```
设计四足机器狗的左前腿,2-DOF(髋关节 + 膝关节),用 MG996R 舵机,
要求:髋摆 ±30°、膝摆 0~90°、单腿质量 < 200g,提供 STEP 文件。
```

期望输出(在 `~/work/projects/robot-dog/` 下):PRD / 3 个 STEP / 固件 C / IK Python / BOM,全程 step 链路在前端 Pipeline 视图可见(B2 阶段)。**真版 e2e 通过 = v0.1.0-mvp。**

---

### Phase B2 — 前端 Pipeline 视图 P1

依赖 B1.2（task_step 真有数据）。

- **B2.1** 新增 `frontend/src/views/PipelineView.vue` + `components/project/PipelineGraph.vue`
- **B2.2** SSE `/api/events` 已就绪，订阅 task_step 状态推送
- **B2.3** 路由 `/pipeline/:taskId`，从 Dashboard 任务卡片可跳转

**验证：** B1.3 e2e 用例触发后，前端 Pipeline 视图能看到 PRD → 机械 → 固件 → 算法 → 成本的 DAG 实时点亮。

---

### Phase B3 — 稳定性 P1

近 30 commit 里 8 条是 cc_bridge / chat / stdin / scheduler 的 fix。说明这条线还在抖动期。

- **B3.1** cc 后端 checkpointer 恢复语义验证：杀掉 generic agent 进程 → 重启 → 对同一 thread_id 续问，能否拿到上文?
- **B3.2** 引入 e2e 集成测试 `backend/tests/test_e2e_pipeline.py`：fake LLM + 走完 B1.3 用例（不真跑 claude CLI）
- **B3.3** cc_bridge 抖动收口：列出近 1 个月所有 cc_bridge 相关 fix commit，按"配置漂移 / 边界条件 / 并发"归类，最多挑 3 个根因写复现脚本 + 修复

**验证：** `pytest backend/tests/` 全绿；测试覆盖率 ≥ 现有水平。

---

### Phase B4 — 清理 / 退役 P2

- **B4.1** **`agents/`（v1 旧目录）退役**：v2 现在仍 `from agents.base import run_cli_agent`（在 `agents_v2/shared/claude_runner.py`），是反向依赖。把 `run_cli_agent` 内联到 `agents_v2/shared/runner.py` 后，删除整个 `agents/` 目录
- **B4.2** `system/feishu_bot.py`(v1) → 已被 `feishu/` 覆盖,确认无引用后删除
- **B4.3** `agents_v2/shared/a2a_server.py` 还在被 generic 使用,但 A2A 协议层是否还有外部调用方?如果没有,简化到 FastAPI 直通
- **B4.4** `git tag v0.1.0-mvp`(B1.3 通过后)

---

### Phase B5 — 文档同步 P2

- **B5.1** `ARCHITECTURE.md` 现状校对：核对 9 个员工实际启动方式与文档是否一致
- **B5.2** `ARCHITECTURE_LANGGRAPH_A2A.md` 加 banner：该文是设计意图，与现状偏离，参见 plan.md
- **B5.3** `README.md` 更新启动顺序与端口表（DB 驱动后端口已动态化）

---

## 5. 已知风险（按致命度排）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| 1 | **没有任何"端到端造机器狗"的成功记录** | 整个项目核心叙事未被验证 | B1.3 |
| 2 | TechLead 编排能力下沉到 3 处 | 新加一种协作模式要改 3 个文件 | B1.1 选型 |
| 3 | ~~task_step 表是死的~~ | ~~Pipeline 视图无数据可显示~~ | ✅ B1.2 已打通(group_chat/task_steps.py + 4 node + speak: 行) |
| 4 | cc 后端 checkpointer 恢复未验证 | 进程崩了断点不能续 | B3.1 |
| 5 | 飞书侧 fix 频繁 | 用户感知不稳 | B3.3 |
| 6 | v1 → v2 反向依赖（agents_v2 import agents） | v1 不能删 | B4.1 |
| 7 | A2A 协议层是否仍有调用方未明 | 死代码 | B4.3 |
| 8 | 前端无 Pipeline 视图 | 任务进度只能从消息流揣测 | B2 |

---

## 6. 不在路线图内的事

明确**不打算**做的，避免后续犹豫：

- v1 plan 里的"复活 8 个独立员工目录" — 已 pivot，generic + DB 是终态
- 把 TechLead 重新做成独立 :9000 进程 — 除非 B1.1 选型选了方案 A
- 把 claude code CLI 换回 LangChain bind_tools — 阶段 1-11 已论证此路不通
- 加更多基础设施服务 — 已经 5 个 docker 服务，再加一个就要先删一个

---

## 7. 执行规则

1. **逐 Phase 执行**：每个 Phase 全部 Task 跑完后，汇报验证结果，等用户确认再进入下一 Phase
2. **每 Task 必须验证**：Task 步骤执行完毕后运行验证命令，输出结果写入汇报
3. **遇到错误立即停**：不跳过，修复后重新验证，再继续
4. **commit 后等 review**：本仓库规则，不允许 push remote 前自行确认
5. **B1.3 e2e 是终极验证**：任何架构变动都要确保 B1.3 仍能跑通

---

## 8. 附录：实际进程拓扑（start.sh 还原）

```
端口表（全部 DB 动态化，下面是当前 employee 表里的实际值）：
  :8000   backend FastAPI
  :8089   feishu bot（主公司 app）
  :8087   cc_bridge（飞书 ↔ Claude Code）
  :9000   tech_lead   ← 走 generic.main，不是独立目录
  :9001   mechanical
  :9002   hardware
  :9003   firmware
  :9004   algorithm
  :9005   product_manager
  :9006   testing
  :9007   cost
  :9008   project_manager
  :9009   sysadmin
  :5173   frontend
  :5432   postgres / :6379 redis / :3000 gitea / :8065 mattermost / :5678 n8n

启动顺序（start.sh）：
  1. docker compose up -d (5 个服务)
  2. 清残留 PID
  3. uvicorn backend.main:app :8000
  4. 遍历 active employee → start_py agents_v2.<key>.main 或 generic.main <key>
  5. npm run dev (frontend :5173)
  6. python -m feishu.bot
  7. 遍历有 feishu_app_id 的员工 → python -m feishu.employee_bot <key>
  8. CC Bridge (jurigged 热更)
```
