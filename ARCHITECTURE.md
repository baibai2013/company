# Company System — Architecture

> 技术选型：FastAPI + Celery + Redis + PostgreSQL + Vue 3

---

## 目标结构

```
company/
├── backend/                    # FastAPI 后端（:8000）
│   ├── api/
│   │   ├── routes/
│   │   │   ├── tasks.py        # /api/tasks CRUD
│   │   │   ├── employees.py    # /api/employees 状态查询
│   │   │   ├── chat.py         # /api/chat 消息历史
│   │   │   └── events.py       # /api/events SSE 实时推送
│   │   └── deps.py             # 依赖注入（DB session）
│   ├── core/
│   │   ├── config.py           # 统一配置（读 infra/.env）
│   │   └── db.py               # SQLAlchemy engine + session factory
│   ├── models/                 # ORM 表定义
│   │   ├── task.py             # Task, TaskStep
│   │   └── message.py          # ChatMessage
│   ├── schemas/                # Pydantic 请求/响应 schema
│   │   ├── task.py
│   │   └── message.py
│   └── main.py                 # FastAPI app 入口
│
├── workers/                    # Celery worker（独立进程）
│   ├── celery_app.py           # Celery 实例 + Redis broker 配置
│   ├── pipeline.py             # pipeline 任务链定义（chain/group/chord）
│   └── tasks/
│       ├── pm.py               # pm_task
│       ├── techlead.py         # techlead_task（per-domain 拆解）
│       ├── engineering.py      # mechanical / hardware / firmware / algorithm tasks
│       └── integration.py      # TechLead 集成 review task
│
├── agents/                     # Claude agent 封装（被 workers 调用）
│   ├── base.py                 # run_cli_agent()，run_agent()
│   ├── employees.py            # REGISTRY
│   └── worker.py               # FastAPI /run 端点（:8080，保持不动）
│
├── feishu/                     # 飞书模块（:8089）
│   ├── bot.py                  # 事件接收 WebSocket + /send HTTP 端点
│   ├── sender.py               # 发消息/卡片封装
│   └── commands/
│       ├── pipeline.py         # ?pipeline → POST /api/tasks
│       ├── approve.py          # ?approve → POST /api/tasks/{id}/approve
│       ├── report.py           # ?report  → GET  /api/tasks
│       └── direct.py           # ?<employee> 直接对话
│
├── frontend/                   # Vue 3 前端（Vite :5173 / 构建后 Nginx）
│   ├── src/
│   │   ├── views/
│   │   │   ├── ChatView.vue    # 聊天页（群聊 + 单聊）
│   │   │   └── ProjectView.vue # 项目页（任务看板 + 进度）
│   │   ├── components/
│   │   │   ├── chat/
│   │   │   │   ├── GroupChat.vue     # 并行广播，每人独立回复 feed
│   │   │   │   └── DirectChat.vue    # 单聊，持久 session
│   │   │   └── project/
│   │   │       ├── TaskBoard.vue     # 卡片看板（P0/P1/P2，状态列）
│   │   │       └── Pipeline.vue      # 流水线步骤链路（步骤 + 状态）
│   │   ├── stores/
│   │   │   ├── tasks.ts        # Pinia task store
│   │   │   └── chat.ts         # Pinia chat store
│   │   └── api/
│   │       └── client.ts       # axios 封装 + SSE 订阅
│   ├── index.html
│   └── vite.config.ts
│
├── agents/                     # （现有，保持）
├── employees/                  # （现有，保持）
├── system/                     # （现有，保持，迁移完成后逐步退役）
│   ├── dashboard.py            # 现有看板，新系统稳定前继续服务
│   └── feishu_bot.py           # 现有飞书机器人，迁移期保留
│
├── scripts/
│   └── setup_n8n_workflows.py  # W3 定时周报（保持）
│
└── infra/
    ├── .env                    # 密钥配置
    ├── .env.example
    ├── docker-compose.yml      # postgres + redis（新增 redis service）
    └── alembic/                # DB migration
        ├── alembic.ini
        └── versions/
```

---

## 进程清单

| 进程 | 命令 | 端口 | 说明 |
|------|------|------|------|
| PostgreSQL | docker compose up postgres | 5432 | 持久化存储 |
| Redis | docker compose up redis | 6379 | Celery broker + Pub/Sub |
| Backend API | uvicorn backend.main:app | 8000 | REST + SSE |
| Celery Worker | celery -A workers.celery_app worker | — | pipeline 异步执行 |
| Agent Worker | uvicorn agents.worker:app | 8080 | Claude agent /run |
| Feishu Bot | python -m feishu.bot | 8089 | 飞书 WS + /send |
| Frontend | npm run dev (frontend/) | 5173 | Vue 3 Vite dev |

---

## 数据库 Schema

### Task

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| title | TEXT | 任务标题 |
| description | TEXT | 任务描述 |
| priority | ENUM(P0,P1,P2) | 优先级 |
| status | ENUM(pending,running,gate_waiting,done,failed) | 当前状态 |
| celery_id | TEXT | Celery chain ID，用于追踪 |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

### TaskStep

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| task_id | FK → Task | |
| step_name | TEXT | pm / techlead / mechanical / … |
| status | ENUM(pending,running,done,failed) | |
| input | TEXT | 传入 context |
| output | TEXT | agent 输出 |
| started_at | TIMESTAMP | |
| finished_at | TIMESTAMP | |

### ChatMessage

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | |
| channel | TEXT | "group" 或 员工名 |
| role | ENUM(user,employee) | |
| sender | TEXT | 发送者标识 |
| content | TEXT | 消息内容 |
| created_at | TIMESTAMP | |

---

## Celery Pipeline 设计

```python
# workers/pipeline.py
from celery import chain, group, chord

def start_engineering_pipeline(task_id: str, description: str):
    return chain(
        pm_task.s(task_id, description),
        techlead_task.s(),           # 接收 PM 输出，产出 per-domain 拆解
        gate_task.s(),               # 写 DB status=gate_waiting，挂起等审批
        chord(
            group(mechanical_task.s(), hardware_task.s()),
            round2_callback.s(),     # Round1 全部完成后触发
        ),
        chord(
            group(firmware_task.s(), algorithm_task.s()),
            round3_callback.s(),     # Round2 全部完成后触发
        ),
        integration_task.s(),        # TechLead 汇总
    ).apply_async()
```

**Gate 机制：**
- `gate_task` 将 Task.status 置为 `gate_waiting`，向 Redis 写等待 key
- `?approve` → `POST /api/tasks/{id}/approve` → 删除 Redis key → Celery chain 继续
- 重启后 Celery 从 Redis broker 恢复未完成任务，gate 状态从 DB 读取

---

## API 端点

```
POST   /api/tasks                     # 创建任务（?pipeline 触发）
GET    /api/tasks                     # 列表，支持 ?status=&priority= 过滤
GET    /api/tasks/{id}                # 详情，含 steps 列表
POST   /api/tasks/{id}/approve        # 解锁 gate，继续 pipeline

GET    /api/events                    # SSE 实时推送任务/步骤状态变更

POST   /api/chat/group                # 群聊发消息（并行 dispatch 给指定员工）
GET    /api/chat/group/history        # 群聊历史
POST   /api/chat/direct/{employee}    # 单聊
GET    /api/chat/direct/{employee}/history

GET    /api/employees                 # 所有员工 + 当前执行步骤状态
```

---

## 实时推送链路

```
Celery task 写 DB
       ↓
redis.publish("task_events", json_payload)
       ↓
FastAPI /api/events (SSE, 长连接)
       ↓
Vue3 useEventSource() → Pinia store.patch() → UI 自动更新
```

---

## 前端页面结构

### 聊天页（ChatView）
- 左侧边栏：群聊 + 各员工单聊频道列表
- 群聊 feed：消息按时间排列，每个员工回复独立气泡（并行广播结果）
- 单聊：与指定员工的持久对话，会话通过 session_file 保持上下文

### 项目页（ProjectView）
- 任务看板（TaskBoard）：按状态列（待处理 / 执行中 / 审批等待 / 已完成）
  - 卡片显示：标题、优先级徽章、当前执行步骤、预估时间
- 任务详情抽屉：展开后显示 Pipeline 步骤链路（步骤名 → 状态 → 输出摘要）

---

## 依赖

```
# backend / workers
fastapi
uvicorn[standard]
sqlalchemy[asyncio]
alembic
asyncpg           # PostgreSQL async driver
celery[redis]
redis
pydantic-settings

# agents（现有）
anthropic
httpx
python-dotenv

# frontend
vue@3
pinia
element-plus
axios
vite
```

---

## 迁移策略

1. **阶段一**：新建 `backend/` + `workers/` + `feishu/`，`system/` 保持运行
2. **阶段二**：前端 `frontend/` 替代 `system/dashboard.py`（看板功能对等后切换）
3. **阶段三**：`system/feishu_bot.py` 逻辑迁入 `feishu/`，旧文件退役
4. **阶段四**：`system/` 目录删除

每个阶段独立可测试，不影响现有功能。
