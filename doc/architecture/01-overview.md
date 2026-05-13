# 系统全貌

## 一句话描述

10 名「AI 员工」作为独立微服务运行，通过飞书接受指令，用 LangGraph 状态机完成推理，用 A2A 协议相互协作，状态持久化到 PostgreSQL，实时进度推送到 Redis。

---

## 系统层次

```
┌─────────────────────────────────────────────────────┐
│                   用户 / 飞书                        │
│    单聊 · 群聊 · @mention · 图片 · 富文本             │
└──────────────────┬──────────────────────────────────┘
                   │ WebSocket 长连接
┌──────────────────▼──────────────────────────────────┐
│              飞书集成层                               │
│  feishu/bot.py (:8089)  feishu/employee_bot.py       │
│  feishu/sender.py       feishu/commands/dispatch.py  │
└──────────────────┬──────────────────────────────────┘
                   │ HTTP / A2A JSON-RPC 2.0
┌──────────────────▼──────────────────────────────────┐
│              Agent 层（10 个微服务）                  │
│  tech_lead:9000  mechanical:9001  hardware:9002       │
│  firmware:9003   algorithm:9004   product_mgr:9005    │
│  testing:9006    cost:9007        project_mgr:9008    │
│  sysadmin:9009                                        │
│  ─────────────────────────────────────────────────── │
│  共享模块：smart_graph · runner · a2a_server          │
└──────┬───────────────────────┬───────────────────────┘
       │                       │
┌──────▼──────┐    ┌───────────▼────────────┐
│  LLM 层      │    │  数据层                 │
│ Claude API   │    │  PostgreSQL :5432       │
│ Haiku 4.5    │    │    company_app          │
│ Sonnet 4.6   │    │    company_langgraph    │
│ Opus 4.7     │    │  Redis :6379            │
└─────────────┘    └────────────────────────┘
                   ┌────────────────────────┐
                   │  前端看板               │
                   │  Vue3 + Vite :5173      │
                   │  Backend API :8000      │
                   └────────────────────────┘
```

---

## 技术栈

| 层级 | 技术 | 版本/说明 |
|------|------|----------|
| AI 框架 | LangGraph | StateGraph + Checkpointer |
| Agent 协议 | A2A JSON-RPC 2.0 | 自实现，兼容 Google A2A 规范 |
| LLM | Anthropic Claude | Haiku 4.5 / Sonnet 4.6 / Opus 4.7 |
| Web 框架 | FastAPI + uvicorn | 每个 Agent 独立进程 |
| 数据库 | PostgreSQL 16 | Docker，两个库 |
| 缓存/消息 | Redis 7 | 发布订阅、进度推送 |
| 飞书 SDK | lark-oapi | WebSocket 长连接 |
| 前端 | Vue 3 + Vite + Pinia | Element Plus UI |
| ORM | SQLAlchemy 2.0 async | alembic 迁移 |
| 容器 | Docker Compose | postgres / redis / gitea / mattermost / n8n |
| 运行时 | Python 3.13 | .venv 虚拟环境 |

---

## 端口分配

| 服务 | 端口 | 类型 | 说明 |
|------|------|------|------|
| Backend API | 8000 | HTTP REST | FastAPI，/api/* |
| Feishu Bot | 8089 | HTTP | 飞书回调接收 |
| Frontend | 5173 | HTTP | Vite 开发服务器 |
| TechLead | 9000 | HTTP A2A | 总协调员 |
| Mechanical | 9001 | HTTP A2A | 机械工程师 |
| Hardware | 9002 | HTTP A2A | 硬件工程师 |
| Firmware | 9003 | HTTP A2A | 固件工程师 |
| Algorithm | 9004 | HTTP A2A | 算法工程师 |
| Product Manager | 9005 | HTTP A2A | 产品经理 |
| Testing | 9006 | HTTP A2A | 测试工程师 |
| Cost | 9007 | HTTP A2A | 成本分析师 |
| Project Manager | 9008 | HTTP A2A | 项目经理 |
| SysAdmin | 9009 | HTTP A2A | 系统运维 |
| PostgreSQL | 5432 | TCP | Docker |
| Redis | 6379 | TCP | Docker |
| Gitea | 3000 | HTTP | Docker |
| Mattermost | 8065 | HTTP | Docker |
| n8n | 5678 | HTTP | Docker |

---

## 目录结构

```
company/
├── agents_v2/              # AI Agent 微服务（当前版本）
│   ├── shared/             # 所有 Agent 共用的核心模块
│   │   ├── smart_graph.py  # LangGraph 路由图（CHAT/WORK）
│   │   ├── runner.py       # astream + Redis 进度发布
│   │   ├── a2a_server.py   # A2A JSON-RPC 服务端/客户端
│   │   ├── claude_client.py# LLM 实例工厂
│   │   └── db.py           # PostgreSQL 连接 + checkpointer
│   ├── tech_lead/          # 总协调员（含 supervisor.py）
│   ├── mechanical/         # 机械
│   ├── hardware/           # 硬件
│   ├── firmware/           # 固件
│   ├── algorithm/          # 算法
│   ├── product_manager/    # 产品（含 CC 路由）
│   ├── testing/            # 测试
│   ├── cost/               # 成本
│   ├── project_manager/    # 项目
│   └── sysadmin/           # 运维（含巡查循环）
├── feishu/                 # 飞书集成
│   ├── bot.py              # 命令式 Bot（?pipeline 等）
│   ├── employee_bot.py     # 员工身份 Bot（每人独立 App）
│   ├── sender.py           # 发消息工具函数
│   └── commands/           # 各命令处理器
├── backend/                # REST API
│   ├── main.py
│   ├── models/
│   ├── schemas/
│   └── api/routes/
├── frontend/               # Vue3 看板
├── infra/                  # docker-compose.yml + .env
├── alembic/                # 数据库迁移
├── logs/                   # 运行时日志
├── start.sh                # 一键启动
└── stop.sh                 # 一键停止
```
