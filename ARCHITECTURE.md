# Company System 架构设计文档

**版本：** v2.0  
**日期：** 2026-05-12  
**状态：** 设计确认

---

## 1. 背景与目标

### 1.1 现状问题

当前系统由 3 个 Python 文件构成（`dashboard.py` / `feishu_bot.py` / `worker.py`），存在以下问题：

- 前后端耦合：HTML、业务逻辑、AI 调用混写在同一文件
- 无模块边界：任务模块、聊天模块、飞书模块没有独立
- 状态不持久：pipeline 任务存在内存 dict，重启即丢失
- 不可扩展：新增功能只能往已有文件里堆代码

### 1.2 设计目标

- 前后端分离，各自独立部署和开发
- 模块边界清晰，每个模块单一职责
- 任务状态持久化，重启后可恢复
- 进程独立，任一模块崩溃不影响其他模块

---

## 2. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| HTTP API | FastAPI | Python 异步原生支持，自动生成 OpenAPI 文档 |
| ORM | SQLAlchemy 2.0 + Alembic | Python 事实标准，async 支持，migration 完整 |
| 数据库 | PostgreSQL | 关系型持久化，支持 JSONB，事务完整 |
| 任务队列 | Celery + Redis | 成熟稳定，支持任务链/分组/重试/优先级，重启恢复 |
| 消息总线 | Redis Pub/Sub | Celery 已依赖 Redis，SSE 推送复用同一实例 |
| 实时推送 | Server-Sent Events (SSE) | 单向推送够用，比 WebSocket 简单，无需额外依赖 |
| 前端框架 | Vue 3 + Vite | 组件化，Pinia 状态管理，Element Plus 企业级 UI |
| 飞书集成 | 独立进程，调内部 HTTP API | 解耦，飞书变更不影响核心系统 |

---

## 3. 系统架构

### 3.1 整体架构图

```
  ┌──────────────────────────────────────────────────────┐
  │                    用户入口                           │
  │    浏览器 (Vue3 :5173)        飞书 App               │
  └────────────┬──────────────────────┬──────────────────┘
               │ REST / SSE           │ WebSocket
               │                      ▼
               │               ┌──────────────┐
               │               │  feishu/bot  │ :8089
               │               │  命令解析     │
               │               └──────┬───────┘
               │                      │ HTTP
               ▼                      ▼
  ┌────────────────────────────────────────────────────┐
  │              backend/ (FastAPI :8000)               │
  │                                                    │
  │  /api/tasks      /api/chat      /api/events(SSE)   │
  │  /api/employees  /api/tasks/{id}/approve            │
  └───────┬─────────────────────────────┬──────────────┘
          │ SQLAlchemy (async)           │ redis.publish
          ▼                             ▼
  ┌───────────────┐            ┌────────────────┐
  │  PostgreSQL   │            │     Redis      │
  │  :5432        │            │     :6379      │
  │  (持久化)     │            │  (broker+SSE)  │
  └───────────────┘            └────────┬───────┘
                                        │ Celery broker
                                        ▼
                               ┌────────────────────┐
                               │  workers/ (Celery) │
                               │  pipeline 异步执行  │
                               └────────┬───────────┘
                                        │ HTTP POST /run
                                        ▼
                               ┌────────────────────┐
                               │  agents/worker     │ :8080
                               │  Claude Code CLI   │
                               └────────────────────┘
```

### 3.2 进程清单

| 进程 | 启动命令 | 端口 | 职责 |
|------|----------|------|------|
| PostgreSQL | `docker compose up postgres` | 5432 | 数据持久化 |
| Redis | `docker compose up redis` | 6379 | Celery broker + Pub/Sub |
| Backend API | `uvicorn backend.main:app` | 8000 | REST API + SSE |
| Celery Worker | `celery -A workers.celery_app worker` | — | 异步执行 pipeline |
| Agent Worker | `uvicorn agents.worker:app` | 8080 | Claude Code CLI 执行 |
| Feishu Bot | `python -m feishu.bot` | 8089 | 飞书命令接收 + /send |
| Frontend | `npm run dev` (frontend/) | 5173 | Vue 3 开发服务器 |

---

## 4. 模块设计

### 4.1 backend/ — HTTP API 层

**职责：** 暴露 REST API 和 SSE 端点，不含业务逻辑，只做请求路由和数据 CRUD。

```
backend/
├── main.py              # FastAPI app，注册 router，CORS，lifespan
├── api/
│   ├── deps.py          # 依赖注入：AsyncSession, get_db()
│   └── routes/
│       ├── tasks.py     # GET/POST /api/tasks, POST /api/tasks/{id}/approve
│       ├── employees.py # GET /api/employees
│       ├── chat.py      # POST/GET /api/chat/group, /api/chat/direct/{name}
│       └── events.py    # GET /api/events — SSE 长连接
├── core/
│   ├── config.py        # pydantic-settings 读取 infra/.env
│   └── db.py            # async_engine, AsyncSessionLocal, Base
├── models/
│   ├── task.py          # Task, TaskStep ORM 模型
│   └── message.py       # ChatMessage ORM 模型
└── schemas/
    ├── task.py          # TaskCreate, TaskRead, TaskStepRead
    └── message.py       # MessageCreate, MessageRead
```

**关键接口：**

```
POST   /api/tasks                    # 创建任务，写 DB，提交 Celery chain
GET    /api/tasks?status=&priority=  # 任务列表（看板数据源）
GET    /api/tasks/{id}               # 任务详情 + steps 列表
POST   /api/tasks/{id}/approve       # 解锁 gate，写 Redis signal
GET    /api/events                   # SSE，订阅任务状态变更实时推送
POST   /api/chat/group               # 群聊消息，触发并行 dispatch
GET    /api/chat/group/history       # 群聊历史
POST   /api/chat/direct/{employee}   # 单聊消息
GET    /api/chat/direct/{employee}/history
GET    /api/employees                # 员工列表 + 当前执行步骤
```

---

### 4.2 workers/ — Celery 异步任务层

**职责：** 定义 pipeline 任务链，调用 agents/worker 执行 AI 任务，写 DB 更新步骤状态。

```
workers/
├── celery_app.py        # Celery 实例，broker=Redis，backend=Redis
├── pipeline.py          # start_engineering_pipeline() 入口函数
└── tasks/
    ├── pm.py            # @app.task pm_task — 调 product_manager agent
    ├── techlead.py      # @app.task techlead_task — per-domain 拆解
    ├── gate.py          # @app.task gate_task — 写 gate_waiting，等 Redis signal
    ├── engineering.py   # @app.task mechanical/hardware/firmware/algorithm_task
    └── integration.py   # @app.task integration_task — TechLead 汇总
```

**Pipeline 执行链：**

```python
# workers/pipeline.py
from celery import chain, group, chord

def start_engineering_pipeline(task_id: str, description: str):
    return chain(
        pm_task.s(task_id, description),       # Step 1: PM 分析需求
        techlead_task.s(),                      # Step 2: TechLead 拆解到各域
        gate_task.s(),                          # Step 3: Gate — 等待 CEO 审批
        chord(                                  # Step 4: Round1 并行
            group(mechanical_task.s(), hardware_task.s()),
            round2_dispatch.s(),
        ),
        chord(                                  # Step 5: Round2 并行（读 Round1 输出）
            group(firmware_task.s(), algorithm_task.s()),
            round3_dispatch.s(),
        ),
        integration_task.s(),                  # Step 6: TechLead 集成 review
    ).apply_async()
```

**Gate 机制：**
- `gate_task` 将 `Task.status` 置为 `gate_waiting`，向 Redis 写 key `gate:{task_id}`
- CEO 发 `?approve` → feishu 调 `POST /api/tasks/{id}/approve` → backend 删除 Redis key → 向等待中的 gate_task 发 Celery signal → chain 继续
- 重启后 Celery 从 Redis broker 恢复未完成任务，`gate_waiting` 状态由 DB 持久化

---

### 4.3 feishu/ — 飞书集成层

**职责：** 接收飞书消息事件，解析命令，调用 backend HTTP API，发送回复。不含业务逻辑。

```
feishu/
├── bot.py           # FastAPI app：飞书 WebSocket 事件 + POST /send 端点
├── sender.py        # 封装飞书消息发送（文本/卡片/图片）
└── commands/
    ├── pipeline.py  # ?pipeline <需求> → POST /api/tasks
    ├── approve.py   # ?approve          → POST /api/tasks/{id}/approve
    ├── report.py    # ?report           → GET  /api/tasks
    └── direct.py    # ?<employee> <消息> → POST /api/chat/direct/{employee}
```

**命令路由：**

| 飞书指令 | 调用 | 说明 |
|----------|------|------|
| `?pipeline <需求>` | `POST /api/tasks` | 创建任务，启动 pipeline |
| `?approve` | `POST /api/tasks/{id}/approve` | 解锁当前 gate_waiting 任务 |
| `?report` | `GET /api/tasks` | 返回任务列表卡片 |
| `?<员工名> <消息>` | `POST /api/chat/direct/{employee}` | 直接与某员工单聊 |

---

### 4.4 frontend/ — Vue 3 前端

**职责：** 展示看板和聊天，通过 REST + SSE 与 backend 交互，不含业务逻辑。

```
frontend/
├── src/
│   ├── views/
│   │   ├── ChatView.vue        # 聊天页（左侧边栏 + 消息区）
│   │   └── ProjectView.vue     # 项目页（任务看板 + 流水线详情）
│   ├── components/
│   │   ├── chat/
│   │   │   ├── GroupChat.vue   # 群聊：并行广播，每人独立气泡
│   │   │   └── DirectChat.vue  # 单聊：持久 session 对话
│   │   └── project/
│   │       ├── TaskBoard.vue   # 看板：按状态列 + 优先级徽章
│   │       └── Pipeline.vue    # 流水线步骤链路：步骤名→状态→输出摘要
│   ├── stores/
│   │   ├── tasks.ts            # Pinia：任务列表，SSE 增量更新
│   │   └── chat.ts             # Pinia：消息历史
│   └── api/
│       └── client.ts           # axios 封装 + useEventSource() SSE 订阅
├── index.html
└── vite.config.ts              # proxy /api → localhost:8000
```

**页面布局：**

```
┌─────────────────────────────────────────────────────┐
│  导航：[聊天]  [项目]                                │
├──────────┬──────────────────────────────────────────┤
│          │                                          │
│  频道    │          聊天页 / 项目页                  │
│  列表    │                                          │
│  ──────  │  聊天页：群聊 feed 或 单聊对话           │
│  # 群聊  │                                          │
│  @ 机械  │  项目页：                                │
│  @ 固件  │    任务看板（P0/P1/P2 × 状态列）         │
│  @ 算法  │    点击卡片 → 流水线步骤链路抽屉         │
│  …       │                                          │
└──────────┴──────────────────────────────────────────┘
```

---

### 4.5 agents/ — Claude Agent 层（现有，保持不动）

**职责：** 封装 Claude Code CLI 调用，提供 `run_cli_agent()` 接口，暴露 `POST /run` HTTP 端点。

- `agents/worker.py` — FastAPI `/run` 端点，被 workers/tasks 调用
- `agents/base.py` — `run_cli_agent()` subprocess 封装
- `agents/employees/` — 9 名员工的 system prompt + 参数

**此层在新架构中不修改，workers/tasks 直接 HTTP 调用 `POST :8080/run`。**

---

## 5. 数据模型

### Task

```sql
CREATE TABLE task (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    description TEXT,
    priority    VARCHAR(2) NOT NULL DEFAULT 'P1',  -- P0 / P1 / P2
    status      VARCHAR(20) NOT NULL DEFAULT 'pending',
                -- pending / running / gate_waiting / done / failed
    celery_id   TEXT,
    estimate_hours FLOAT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### TaskStep

```sql
CREATE TABLE task_step (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id     UUID NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    step_name   VARCHAR(50) NOT NULL,  -- pm / techlead / mechanical / ...
    status      VARCHAR(20) NOT NULL DEFAULT 'pending',
    input       TEXT,
    output      TEXT,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
```

### ChatMessage

```sql
CREATE TABLE chat_message (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel    VARCHAR(50) NOT NULL,  -- "group" 或 员工名
    role       VARCHAR(10) NOT NULL,  -- "user" / "employee"
    sender     VARCHAR(50) NOT NULL,
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 6. 实时推送链路

```
Celery task 更新 TaskStep.status
       ↓
redis.publish("task_events", {
    "task_id": "...",
    "step": "mechanical",
    "status": "done",
    "output": "..."
})
       ↓
FastAPI /api/events (SSE，AsyncGenerator)
  订阅 Redis channel，持续 yield data
       ↓
Vue3 useEventSource("/api/events")
  → Pinia tasks.store.updateStep()
  → TaskBoard / Pipeline 组件自动响应
```

---

## 7. 迁移策略

新系统与现有系统**并行运行**，分四个阶段迁移，每阶段独立可回滚：

| 阶段 | 内容 | 完成标志 |
|------|------|----------|
| Phase 1 | 搭建 `backend/` + DB schema + Alembic | `GET /api/tasks` 返回空列表 |
| Phase 2 | 搭建 `workers/` + Celery pipeline | `?pipeline` 命令创建任务并执行 |
| Phase 3 | 搭建 `frontend/` Vue 看板 | 看板功能与 `dashboard.py` 对等 |
| Phase 4 | 迁移 `system/feishu_bot.py` → `feishu/` | 飞书命令全部走新模块 |

Phase 1-2 完成后，`system/feishu_bot.py` 的 `?pipeline` 改为调 `POST :8000/api/tasks`，其余命令保持不变，实现渐进迁移。

---

## 8. 依赖清单

```
# backend + workers
fastapi>=0.111
uvicorn[standard]>=0.29
sqlalchemy[asyncio]>=2.0
alembic>=1.13
asyncpg>=0.29          # PostgreSQL async driver
celery[redis]>=5.3
redis>=5.0
pydantic-settings>=2.0
python-dotenv

# agents（现有，不变）
anthropic
httpx

# frontend
vue@^3.4
pinia@^2.1
element-plus@^2.7
axios@^1.6
vite@^5.2
```

---

## 9. 文件变更影响范围

| 文件/目录 | 操作 | 说明 |
|-----------|------|------|
| `backend/` | 新建 | 全新 API 层 |
| `workers/` | 新建 | 全新 Celery 任务层 |
| `feishu/` | 新建 | 从 `system/feishu_bot.py` 重构 |
| `frontend/` | 新建 | 替代 `system/dashboard.py` |
| `infra/docker-compose.yml` | 修改 | 新增 redis service |
| `infra/alembic/` | 新建 | DB migration |
| `agents/` | 不动 | 现有代码保持 |
| `employees/` | 不动 | 现有文档保持 |
| `system/dashboard.py` | 保留 | 迁移完成后退役 |
| `system/feishu_bot.py` | 保留 | Phase 4 后退役 |
