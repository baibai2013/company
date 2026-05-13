# 基础设施与环境配置

## Docker Compose 服务

**文件：** `infra/docker-compose.yml`  
**网络：** `company-net`（所有容器互通）

| 服务 | 镜像 | 端口 | 说明 |
|------|------|------|------|
| postgres | postgres:16-alpine | 5432 | 主数据库，含健康检查 |
| redis | redis:7-alpine | 6379 | 进度发布/订阅 |
| gitea | gitea/gitea:1.22 | 3000 / 222 | 代码仓库（SSH:222） |
| mattermost | mattermost-team-edition:9.11 | 8065 | 团队聊天备用频道 |
| n8n | n8nio/n8n:1.56.1 | 5678 | 自动化工作流 |

所有服务使用具名 volume 持久化数据（`postgres_data`、`redis_data` 等）。

---

## 环境变量

**文件：** `infra/.env`（不入 git）

```
# 数据库
POSTGRES_PASSWORD=<密码>

# n8n
N8N_PASSWORD=<密码>

# LLM
ANTHROPIC_API_KEY=sk-ant-xxx

# Gitea
GITEA_USER=admin
GITEA_TOKEN=<token>

# 飞书主 Bot
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_CHAT_ID=oc_xxx          # 大群 chat_id

# 员工独立 Bot（共 10 组）
PRODUCT_MANAGER_APP_ID=cli_xxx
PRODUCT_MANAGER_APP_SECRET=xxx
PROJECT_MANAGER_APP_ID=cli_xxx
PROJECT_MANAGER_APP_SECRET=xxx
TECH_LEAD_APP_ID=cli_xxx
TECH_LEAD_APP_SECRET=xxx
MECHANICAL_APP_ID=cli_xxx
MECHANICAL_APP_SECRET=xxx
HARDWARE_APP_ID=cli_xxx
HARDWARE_APP_SECRET=xxx
FIRMWARE_APP_ID=cli_xxx
FIRMWARE_APP_SECRET=xxx
ALGORITHM_APP_ID=cli_xxx
ALGORITHM_APP_SECRET=xxx
TESTING_APP_ID=cli_xxx
TESTING_APP_SECRET=xxx
COST_APP_ID=cli_xxx
COST_APP_SECRET=xxx
SYSADMIN_APP_ID=cli_xxx
SYSADMIN_APP_SECRET=xxx

# Mattermost（可选）
MATTERMOST_TOKEN=xxx
MM_STATUS_CHANNEL_ID=xxx
MM_APPROVAL_CHANNEL_ID=xxx
MATTERMOST_URL=http://localhost:8065
MM_TEAM_ID=xxx
MM_BOT_USER_ID=xxx
```

---

## PostgreSQL 配置

### 两个业务数据库

| 数据库 | 用途 | 访问方式 |
|--------|------|----------|
| `company_app` | 业务数据（任务、消息） | SQLAlchemy asyncpg |
| `company_langgraph` | LangGraph 状态快照 | psycopg3（AsyncPostgresSaver） |

### 初始化脚本

`infra/postgres/init.sh` 在首次启动时创建这两个数据库。

### 连接字符串（由 `agents_v2/shared/db.py` 构建）

```python
APP_DSN       = "postgresql+asyncpg://admin:{pw}@localhost:5432/company_app"
LANGGRAPH_DSN = "postgresql://admin:{pw}@localhost:5432/company_langgraph"
```

### Alembic 数据库迁移

**文件：** `alembic/`

```bash
# 应用最新迁移
cd /path/to/company
alembic upgrade head

# 生成新迁移（修改 models 后）
alembic revision --autogenerate -m "add xxx column"
```

`alembic/env.py` 从 `backend.core.config.settings` 读取同步 DSN，自动导入所有 Model 以生成正确的迁移脚本。

---

## Redis 配置

### 发布/订阅频道

| 频道 | 发布者 | 订阅者 | 内容 |
|------|--------|--------|------|
| `employee_events` | `runner.py` | Backend SSE | Agent 执行进度 |
| `gate_signal:{task_id}` | Backend `/approve` 端点 | TechLead supervisor | 审批信号 |

### 使用模式

```python
import redis.asyncio as aioredis

redis_client = aioredis.from_url("redis://localhost:6379")

# 发布进度（runner.py）
await redis_client.publish("employee_events", json.dumps(event))

# 订阅（backend SSE）
async for msg in pubsub.listen():
    ...

# 等待审批门（supervisor.py）
await redis_client.blpop(f"gate_signal:{task_id}", timeout=300)

# 解锁审批门（backend /approve 端点）
await redis_client.rpush(f"gate_signal:{task_id}", "approved")
```

---

## 日志

运行时日志写入 `logs/` 目录（不入 git）：

| 文件 | 内容 |
|------|------|
| `logs/backend.log` | FastAPI 请求/错误 |
| `logs/tech_lead.log` | TechLead supervisor 执行日志 |
| `logs/mechanical.log` | 机械工程师 Agent |
| `logs/bot_product_manager.log` | 产品经理飞书 Bot |
| `logs/docker.log` | Docker Compose 启动日志 |
| `logs/frontend.log` | Vite dev server |

查看日志：

```bash
tail -f logs/backend.log
tail -f logs/bot_product_manager.log logs/product_manager.log
```

---

## PID 管理

`start.sh` 将所有后台进程 PID 写入 `.pids` 文件，`stop.sh` 读取此文件逐一终止进程。

```bash
./stop.sh       # 按 .pids 停止所有 Python 进程
./start.sh      # 重新启动（自动清理残留端口）
```
