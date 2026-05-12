# Company System 开发计划

**参考架构：** [ARCHITECTURE.md](./ARCHITECTURE.md) · [ARCHITECTURE_LANGGRAPH_A2A.md](./ARCHITECTURE_LANGGRAPH_A2A.md)  
**目标：** 在现有 `agents/` 基础上搭建 LangGraph + A2A 多 Agent 协作系统 + FastAPI 后端 + Vue 3 前端  
**原则：** 基础设施优先 → 共享库 → 核心服务 → 员工 Agent（可并行） → 前端  

---

## Subagent 分配指南

每个 Phase 标注了依赖关系。同一 Phase 内的 Task 可以并行分配给不同 Subagent。

| 符号 | 含义 |
|------|------|
| 🔒 | 依赖项（必须先完成） |
| ⚡ | 可并行（同 Phase 内） |
| 📦 | Subagent 需要的关键上下文 |

**关键路径：** Phase 0 → Phase 1 → Phase 2 + Phase 3 → Phase 4（并行 8 个 subagent） → Phase 5 → Phase 6 → Phase 7

---

## Phase 0 — 基础设施扩展

🔒 无依赖，最先执行。

### Task 0.1: Docker Compose 添加 Redis

**文件：** 修改 `infra/docker-compose.yml`

在现有 `n8n` service 后添加 Redis service，并将 Redis 加入 volumes：

```yaml
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    volumes:
      - redis_data:/data
```

volumes 块添加 `redis_data:`

- [ ] 修改 `infra/docker-compose.yml`，添加 Redis service + volume
- [ ] `docker compose -f infra/docker-compose.yml up redis -d`
- [ ] `docker compose -f infra/docker-compose.yml exec redis redis-cli ping` → 输出 `PONG`
- [ ] `git add infra/docker-compose.yml && git commit -m "infra: add redis service to docker-compose"`

---

### Task 0.2: PostgreSQL 新增数据库

📦 当前 `postgres` container 已运行，有 `admin` 用户，密码在 `infra/.env` 的 `POSTGRES_PASSWORD`。

新增两个数据库：
- `company_app` — FastAPI backend 用（Task/TaskStep/ChatMessage 表）
- `company_langgraph` — LangGraph checkpointer 用（自动建表）

**文件：** 修改 `infra/postgres/init.sh`

```bash
#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "postgres" <<-EOSQL
  CREATE DATABASE gitea;
  CREATE DATABASE mattermost;
  CREATE DATABASE n8n;
  CREATE DATABASE company_app;
  CREATE DATABASE company_langgraph;
EOSQL
```

- [ ] 修改 `infra/postgres/init.sh`，添加 `company_app` + `company_langgraph`
- [ ] 若 postgres 已运行，手动建库：
  ```bash
  docker compose -f infra/docker-compose.yml exec postgres \
    psql -U admin -c "CREATE DATABASE company_app;" postgres
  docker compose -f infra/docker-compose.yml exec postgres \
    psql -U admin -c "CREATE DATABASE company_langgraph;" postgres
  ```
- [ ] 验证：`psql postgresql://admin:$POSTGRES_PASSWORD@localhost:5432/company_app -c "\l"` 能看到两个库
- [ ] `git add infra/postgres/init.sh && git commit -m "infra: add company_app and company_langgraph databases"`

---

### Task 0.3: 依赖包更新

**文件：** 修改 `requirements.txt`（根目录），新建 `agents_v2/requirements.txt`

根目录 `requirements.txt` 追加（现有不动）：

```
# agents_v2 — LangGraph + A2A
langgraph>=0.2
langgraph-checkpoint-postgres
langchain-anthropic>=0.3
a2a-sdk>=0.2
pydantic-settings>=2.0

# backend
fastapi>=0.111
uvicorn[standard]>=0.29
sqlalchemy[asyncio]>=2.0
alembic>=1.13
asyncpg>=0.29
celery[redis]>=5.3
redis>=5.0
python-dotenv>=1.0
```

- [ ] 追加依赖到根目录 `requirements.txt`
- [ ] `source .venv/bin/activate && pip install -r requirements.txt`
- [ ] `python -c "import langgraph; import a2a; print('OK')"` → 无报错
- [ ] `git add requirements.txt && git commit -m "deps: add langgraph, a2a-sdk, fastapi, celery deps"`

---

## Phase 1 — 共享基础库 (agents_v2/shared/)

🔒 依赖 Phase 0 完成。  
⚡ Task 1.1 ~ 1.4 可并行。

### Task 1.1: LLM 统一客户端

**文件：** 新建 `agents_v2/__init__.py`（空），`agents_v2/shared/__init__.py`（空），`agents_v2/shared/claude_client.py`

```python
# agents_v2/shared/claude_client.py
from langchain_anthropic import ChatAnthropic
from anthropic import Anthropic
from pydantic_settings import BaseSettings
from pathlib import Path


class LLMSettings(BaseSettings):
    ANTHROPIC_API_KEY: str
    ANTHROPIC_BASE_URL: str = ""
    ANTHROPIC_EXTRA_HEADERS: dict = {}

    model_config = {"env_file": str(Path(__file__).parent.parent.parent / "infra" / ".env")}


settings = LLMSettings()


def make_langchain_llm(model: str = "claude-sonnet-4-6") -> ChatAnthropic:
    return ChatAnthropic(
        model=model,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        base_url=settings.ANTHROPIC_BASE_URL or None,
        default_headers=settings.ANTHROPIC_EXTRA_HEADERS or {},
        timeout=120.0,
        max_retries=2,
    )


def make_anthropic_client() -> Anthropic:
    return Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        base_url=settings.ANTHROPIC_BASE_URL or None,
    )
```

- [ ] 创建 `agents_v2/__init__.py`（空文件）
- [ ] 创建 `agents_v2/shared/__init__.py`（空文件）
- [ ] 创建 `agents_v2/shared/claude_client.py`（如上）
- [ ] 验证：`python -c "from agents_v2.shared.claude_client import make_langchain_llm; print(make_langchain_llm().model)"` → 输出 `claude-sonnet-4-6`
- [ ] `git add agents_v2/ && git commit -m "feat(agents_v2): add shared LLM client with proxy support"`

---

### Task 1.2: PostgreSQL Checkpointer Factory

**文件：** 新建 `agents_v2/shared/db.py`

```python
# agents_v2/shared/db.py
import os
from pathlib import Path
from pydantic_settings import BaseSettings


class DBSettings(BaseSettings):
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "admin"

    model_config = {"env_file": str(Path(__file__).parent.parent.parent / "infra" / ".env")}


_db_settings = DBSettings()

APP_DSN = (
    f"postgresql+asyncpg://{_db_settings.POSTGRES_USER}:{_db_settings.POSTGRES_PASSWORD}"
    f"@{_db_settings.POSTGRES_HOST}:{_db_settings.POSTGRES_PORT}/company_app"
)

LANGGRAPH_DSN = (
    f"postgresql://{_db_settings.POSTGRES_USER}:{_db_settings.POSTGRES_PASSWORD}"
    f"@{_db_settings.POSTGRES_HOST}:{_db_settings.POSTGRES_PORT}/company_langgraph"
)


def get_checkpointer():
    """返回 LangGraph PostgresSaver 实例（同步连接）。"""
    from langgraph.checkpoint.postgres import PostgresSaver
    return PostgresSaver.from_conn_string(LANGGRAPH_DSN)
```

- [ ] 创建 `agents_v2/shared/db.py`（如上）
- [ ] 验证：`python -c "from agents_v2.shared.db import LANGGRAPH_DSN; print(LANGGRAPH_DSN)"` → 打印连接串
- [ ] `git add agents_v2/shared/db.py && git commit -m "feat(agents_v2): add PostgreSQL checkpointer factory"`

---

### Task 1.3: Claude Code CLI Runner 封装

📦 现有 `agents/base.py` 有完整的 `run_cli_agent(system, task, work_dir, ...) → (str, list[str])` 实现。  
本 Task 只做薄封装，不重复实现。

**文件：** 新建 `agents_v2/shared/claude_runner.py`

```python
# agents_v2/shared/claude_runner.py
"""
薄封装 agents/base.py 的 run_cli_agent，统一 agents_v2 的调用接口。
"""
from pathlib import Path
from agents.base import run_cli_agent as _run_cli_agent

PROJECTS_ROOT = Path.home() / "work" / "projects" / "robot-dog"


def run_agent(
    system_prompt: str,
    task: str,
    work_dir: Path | None = None,
    timeout: int = 300,
) -> tuple[str, list[str]]:
    """
    执行 Claude Code CLI agent。
    返回 (text_result, output_image_paths)。
    """
    if work_dir is None:
        work_dir = PROJECTS_ROOT
    return _run_cli_agent(
        system=system_prompt,
        task=task,
        work_dir=work_dir,
        timeout=timeout,
    )
```

- [ ] 创建 `agents_v2/shared/claude_runner.py`（如上）
- [ ] 验证：`python -c "from agents_v2.shared.claude_runner import run_agent; print('OK')"` → 无报错
- [ ] `git add agents_v2/shared/claude_runner.py && git commit -m "feat(agents_v2): add claude runner wrapper"`

---

### Task 1.4: A2A Server 基类

📦 A2A 协议：每个 Agent 暴露两个 HTTP 端点：
- `GET /.well-known/agent.json` → Agent Card（能力声明）
- `POST /` → 接收任务，body 为 `{"jsonrpc":"2.0","method":"tasks/send","params":{"message":{"parts":[{"text":"..."}]}}}`  
  返回 `{"result":{"artifacts":[{"parts":[{"text":"..."}]}]}}`

**文件：** 新建 `agents_v2/shared/a2a_server.py`

```python
# agents_v2/shared/a2a_server.py
import json
from pathlib import Path
from typing import Callable, Awaitable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


def create_a2a_app(
    agent_card_path: Path,
    handle_task: Callable[[str, dict], Awaitable[str]],
) -> FastAPI:
    """
    创建标准 A2A FastAPI 应用。
    handle_task(message_text, context) → result_text（由各员工实现）。
    """
    app = FastAPI()
    agent_card = json.loads(agent_card_path.read_text())

    @app.get("/.well-known/agent.json")
    async def get_agent_card():
        return JSONResponse(agent_card)

    @app.post("/")
    async def handle_jsonrpc(request: Request):
        body = await request.json()
        method = body.get("method", "")
        rpc_id = body.get("id", 1)

        if method not in ("tasks/send", "tasks/sendSubscribe"):
            return JSONResponse({"jsonrpc": "2.0", "id": rpc_id,
                                  "error": {"code": -32601, "message": "Method not found"}})

        params = body.get("params", {})
        message = params.get("message", {})
        parts = message.get("parts", [])
        text = next((p["text"] for p in parts if "text" in p), "")
        context = params.get("metadata", {})

        result_text = await handle_task(text, context)

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "id": f"task-{rpc_id}",
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"type": "text", "text": result_text}]}],
            },
        })

    return app
```

- [ ] 创建 `agents_v2/shared/a2a_server.py`（如上）
- [ ] 验证：`python -c "from agents_v2.shared.a2a_server import create_a2a_app; print('OK')"` → 无报错
- [ ] `git add agents_v2/shared/a2a_server.py && git commit -m "feat(agents_v2): add A2A server base"`

---

## Phase 2 — Backend API (FastAPI :8000)

🔒 依赖 Phase 0（PostgreSQL `company_app` 数据库存在，Redis 运行）。  
⚡ 可与 Phase 3 并行开发。

### Task 2.1: DB Models + Alembic Migrations

**文件：**
- 新建 `backend/__init__.py`（空）
- 新建 `backend/core/__init__.py`（空）
- 新建 `backend/core/config.py`
- 新建 `backend/core/db.py`
- 新建 `backend/models/__init__.py`（空）
- 新建 `backend/models/task.py`
- 新建 `backend/models/message.py`
- 新建 `alembic.ini`（根目录）
- 新建 `alembic/env.py`

**backend/core/config.py:**
```python
from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str = "localhost"
    POSTGRES_USER: str = "admin"
    POSTGRES_PORT: int = 5432
    REDIS_URL: str = "redis://localhost:6379/0"

    model_config = {"env_file": str(Path(__file__).parent.parent.parent / "infra" / ".env")}

    @property
    def database_url(self) -> str:
        return (f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/company_app")

    @property
    def database_url_sync(self) -> str:
        return (f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/company_app")

settings = Settings()
```

**backend/core/db.py:**
```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from backend.core.config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

**backend/models/task.py:**
```python
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.core.db import Base

def utcnow():
    return datetime.now(timezone.utc)

class Task(Base):
    __tablename__ = "task"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(2), default="P1")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    celery_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    steps: Mapped[list["TaskStep"]] = relationship("TaskStep", back_populates="task", cascade="all, delete-orphan")

class TaskStep(Base):
    __tablename__ = "task_step"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("task.id", ondelete="CASCADE"))
    step_name: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    input: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task: Mapped["Task"] = relationship("Task", back_populates="steps")
```

**backend/models/message.py:**
```python
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.db import Base

class ChatMessage(Base):
    __tablename__ = "chat_message"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    channel: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(10))
    sender: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=lambda: datetime.now(timezone.utc))
```

- [ ] 创建所有 backend/core/ + backend/models/ 文件（如上）
- [ ] `pip install alembic` && `alembic init alembic`（根目录执行）
- [ ] 配置 `alembic/env.py` 引入 `backend.core.db.Base` 和 `backend.core.config.settings.database_url_sync`
- [ ] `alembic revision --autogenerate -m "init task and chat tables"`
- [ ] `alembic upgrade head`
- [ ] 验证：`psql postgresql://admin:$POSTGRES_PASSWORD@localhost:5432/company_app -c "\dt"` → 看到 task/task_step/chat_message 三张表
- [ ] `git add backend/ alembic/ alembic.ini && git commit -m "feat(backend): DB models + alembic migrations"`

---

### Task 2.2: API Routes — Tasks + Employees

**文件：**
- 新建 `backend/schemas/__init__.py`（空）
- 新建 `backend/schemas/task.py`
- 新建 `backend/api/__init__.py`（空）
- 新建 `backend/api/deps.py`
- 新建 `backend/api/routes/__init__.py`（空）
- 新建 `backend/api/routes/tasks.py`
- 新建 `backend/api/routes/employees.py`
- 新建 `backend/main.py`

**backend/schemas/task.py:**
```python
from pydantic import BaseModel
from datetime import datetime

class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    priority: str = "P1"

class TaskRead(BaseModel):
    id: str
    title: str
    description: str | None
    priority: str
    status: str
    created_at: datetime
    model_config = {"from_attributes": True}

class TaskStepRead(BaseModel):
    id: str
    step_name: str
    status: str
    output: str | None
    started_at: datetime | None
    finished_at: datetime | None
    model_config = {"from_attributes": True}
```

**backend/api/routes/tasks.py:**
```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.core.db import get_db
from backend.models.task import Task, TaskStep
from backend.schemas.task import TaskCreate, TaskRead, TaskStepRead
import redis.asyncio as aioredis
from backend.core.config import settings

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

@router.post("", response_model=TaskRead)
async def create_task(body: TaskCreate, db: AsyncSession = Depends(get_db)):
    task = Task(title=body.title, description=body.description, priority=body.priority)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task

@router.get("", response_model=list[TaskRead])
async def list_tasks(status: str | None = None, db: AsyncSession = Depends(get_db)):
    q = select(Task).order_by(Task.created_at.desc())
    if status:
        q = q.where(Task.status == status)
    result = await db.execute(q)
    return result.scalars().all()

@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@router.post("/{task_id}/approve")
async def approve_task(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    r = aioredis.from_url(settings.REDIS_URL)
    await r.publish(f"gate_signal:{task_id}", "approved")
    await r.aclose()
    return {"ok": True}
```

**backend/main.py:**
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.api.routes.tasks import router as tasks_router
from backend.api.routes.employees import router as employees_router

app = FastAPI(title="Company Backend", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(tasks_router)
app.include_router(employees_router)

@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] 创建所有 schemas/ + api/routes/tasks.py + api/routes/employees.py + main.py 文件
- [ ] `uvicorn backend.main:app --port 8000 --reload` 后台启动
- [ ] `curl localhost:8000/health` → `{"status":"ok"}`
- [ ] `curl -X POST localhost:8000/api/tasks -H "Content-Type: application/json" -d '{"title":"测试任务"}'` → 返回带 id 的 JSON
- [ ] `curl localhost:8000/api/tasks` → 返回列表
- [ ] `git add backend/ && git commit -m "feat(backend): tasks + employees API routes"`

---

### Task 2.3: SSE Events Endpoint

**文件：** 新建 `backend/api/routes/events.py`，修改 `backend/main.py` 注册路由

**backend/api/routes/events.py:**
```python
import asyncio
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import redis.asyncio as aioredis
from backend.core.config import settings

router = APIRouter(prefix="/api/events", tags=["events"])

@router.get("")
async def sse_events():
    async def event_stream():
        r = aioredis.from_url(settings.REDIS_URL)
        pubsub = r.pubsub()
        await pubsub.subscribe("task_events")
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode()
                    yield f"data: {data}\n\n"
                await asyncio.sleep(0)
        finally:
            await pubsub.unsubscribe("task_events")
            await r.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

在 `backend/main.py` 追加：
```python
from backend.api.routes.events import router as events_router
app.include_router(events_router)
```

- [ ] 创建 `backend/api/routes/events.py`（如上）
- [ ] 修改 `backend/main.py` 注册 events_router
- [ ] 验证：`curl -N localhost:8000/api/events`（不要断开，应挂起等待事件）
- [ ] 另一终端：`redis-cli publish task_events '{"test":1}'` → curl 窗口应收到 `data: {"test":1}`
- [ ] `git add backend/api/routes/events.py backend/main.py && git commit -m "feat(backend): SSE events endpoint via Redis pub/sub"`

---

## Phase 3 — TechLead Supervisor (:9000)

🔒 依赖 Phase 1（shared/ 完成）。  
⚡ 可与 Phase 2 并行开发。

### Task 3.1: TechLead Supervisor Graph

**文件：**
- 新建 `agents_v2/tech_lead/__init__.py`（空）
- 新建 `agents_v2/tech_lead/prompts.py`
- 新建 `agents_v2/tech_lead/supervisor.py`

📦 员工端口映射：

```python
EMPLOYEES = {
    "mechanical":      "http://localhost:9001",
    "hardware":        "http://localhost:9002",
    "firmware":        "http://localhost:9003",
    "algorithm":       "http://localhost:9004",
    "product_manager": "http://localhost:9005",
    "testing":         "http://localhost:9006",
    "cost":            "http://localhost:9007",
    "project_manager": "http://localhost:9008",
}
```

**agents_v2/tech_lead/supervisor.py:**
```python
import asyncio
import httpx
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from agents_v2.shared.claude_client import make_langchain_llm
from agents_v2.shared.db import get_checkpointer
from langchain_core.messages import HumanMessage, SystemMessage

EMPLOYEES = {
    "mechanical":      "http://localhost:9001",
    "hardware":        "http://localhost:9002",
    "firmware":        "http://localhost:9003",
    "algorithm":       "http://localhost:9004",
    "product_manager": "http://localhost:9005",
    "testing":         "http://localhost:9006",
    "cost":            "http://localhost:9007",
    "project_manager": "http://localhost:9008",
}

class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    task_description: str
    domain_plans: dict        # {employee_key: task_text}
    completed_outputs: dict   # {employee_key: result_text}
    next_employee: str        # 下一个要派遣的员工 key，或 "DONE"
    phase: str

llm = make_langchain_llm()

def plan_node(state: SupervisorState) -> dict:
    """TechLead 将任务拆解为各员工子任务。"""
    resp = llm.invoke([
        SystemMessage("你是技术负责人，将用户需求拆解为各专业工程师的子任务。"
                      "以 JSON 格式输出，key 为员工标识，value 为任务描述。"
                      f"可用员工：{list(EMPLOYEES.keys())}"),
        HumanMessage(state["task_description"]),
    ])
    import json, re
    m = re.search(r"\{.*\}", resp.content, re.DOTALL)
    plans = json.loads(m.group()) if m else {}
    return {"domain_plans": plans, "phase": "round1"}

async def delegate_node(state: SupervisorState) -> dict:
    """通过 A2A HTTP 将任务委派给员工。"""
    employee = state["next_employee"]
    url = EMPLOYEES[employee]
    task_text = state["domain_plans"].get(employee, state["task_description"])
    context_text = "\n".join(
        f"[{k} 输出]: {v[:500]}" for k, v in state["completed_outputs"].items()
    )
    message = f"{task_text}\n\n前序上下文：\n{context_text}" if context_text else task_text

    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tasks/send",
        "params": {"message": {"parts": [{"type": "text", "text": message}]}},
    }
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            r = await client.post(url, json=payload)
            result_text = r.json()["result"]["artifacts"][0]["parts"][0]["text"]
        except Exception as e:
            result_text = f"[错误] {employee} 调用失败: {e}"

    new_outputs = {**state["completed_outputs"], employee: result_text}
    return {"completed_outputs": new_outputs}

def route_node(state: SupervisorState) -> dict:
    """决定下一个要派遣的员工，或结束。"""
    remaining = [k for k in state["domain_plans"] if k not in state["completed_outputs"]]
    if not remaining:
        return {"next_employee": "DONE"}
    return {"next_employee": remaining[0]}

def route_after_delegate(state: SupervisorState) -> str:
    return END if state.get("next_employee") == "DONE" else "delegate"

graph = StateGraph(SupervisorState)
graph.add_node("plan", plan_node)
graph.add_node("route", route_node)
graph.add_node("delegate", delegate_node)

graph.add_edge(START, "plan")
graph.add_edge("plan", "route")
graph.add_conditional_edges("route", lambda s: "delegate" if s["next_employee"] != "DONE" else END,
                             {"delegate": "delegate", END: END})
graph.add_edge("delegate", "route")

checkpointer = get_checkpointer()
supervisor_app = graph.compile(checkpointer=checkpointer)
```

- [ ] 创建 `agents_v2/tech_lead/__init__.py`, `prompts.py`, `supervisor.py`（如上）
- [ ] 验证：`python -c "from agents_v2.tech_lead.supervisor import supervisor_app; print('graph nodes:', list(supervisor_app.nodes))"` → 无报错
- [ ] `git add agents_v2/tech_lead/ && git commit -m "feat(tech_lead): LangGraph supervisor graph with A2A delegation"`

---

### Task 3.2: TechLead A2A Server + 启动入口

**文件：** 新建 `agents_v2/tech_lead/main.py`

```python
# agents_v2/tech_lead/main.py
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from agents_v2.tech_lead.supervisor import supervisor_app

app = FastAPI(title="TechLead Supervisor", version="1.0")

AGENT_CARD = {
    "name": "技术负责人",
    "description": "技术决策、架构评审、任务编排，将需求拆解分派给各专业工程师",
    "url": "http://localhost:9000",
    "version": "1.0.0",
    "capabilities": {"streaming": False},
    "skills": [
        {"id": "orchestrate", "name": "任务编排",
         "description": "接收需求，分解并协调所有工程师完成任务",
         "inputModes": ["text"], "outputModes": ["text"]},
    ],
}

class TaskRequest(BaseModel):
    description: str
    task_id: str = "default"

@app.get("/.well-known/agent.json")
def agent_card():
    return JSONResponse(AGENT_CARD)

@app.post("/pipeline")
async def run_pipeline(req: TaskRequest):
    """接收来自 backend 的 pipeline 请求，启动 Supervisor graph。"""
    config = {"configurable": {"thread_id": req.task_id}}
    result = await supervisor_app.ainvoke(
        {"task_description": req.description, "domain_plans": {},
         "completed_outputs": {}, "next_employee": "", "phase": "init", "messages": []},
        config=config,
    )
    return {"status": "done", "outputs": result["completed_outputs"]}

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("agents_v2.tech_lead.main:app", host="0.0.0.0", port=9000, reload=False)
```

- [ ] 创建 `agents_v2/tech_lead/main.py`（如上）
- [ ] `python -m agents_v2.tech_lead.main &`
- [ ] `curl localhost:9000/.well-known/agent.json` → 返回 Agent Card JSON
- [ ] `curl localhost:9000/health` → `{"status":"ok"}`
- [ ] `kill %1` 停止后台进程
- [ ] `git add agents_v2/tech_lead/main.py && git commit -m "feat(tech_lead): A2A server + pipeline endpoint"`

---

## Phase 4 — 员工 Agents（可并行，8 个 Subagent）

🔒 依赖 Phase 1（shared/ 完成）。  
⚡ **Task 4.1 ~ 4.8 完全并行，分配给不同 Subagent 同时执行。**

每个员工结构相同：
```
agents_v2/<name>/
├── __init__.py      # 空
├── agent_card.json  # A2A 能力声明
├── prompts.py       # system prompt（从 employees/<category>/<name>.md 迁移）
├── graph.py         # LangGraph: analyze → plan → execute → review
└── main.py          # uvicorn 启动，A2A Server
```

**通用 graph 模板**（各员工替换 STATE_PROMPT / SYSTEM_PROMPT / NAME / PORT）：

```python
# agents_v2/<name>/graph.py
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from agents_v2.shared.claude_client import make_langchain_llm
from agents_v2.shared.claude_runner import run_agent
from agents_v2.shared.db import get_checkpointer
from agents_v2.<name>.prompts import SYSTEM_PROMPT
from langchain_core.messages import HumanMessage, SystemMessage

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    task_input: str
    plan: str
    execution_result: str
    review_passed: bool

llm = make_langchain_llm()

def plan_node(state: AgentState) -> dict:
    resp = llm.invoke([
        SystemMessage(SYSTEM_PROMPT + "\n请分析需求，制定执行方案（100字以内）。"),
        HumanMessage(state["task_input"]),
    ])
    return {"plan": resp.content}

def execute_node(state: AgentState) -> dict:
    result, _ = run_agent(SYSTEM_PROMPT, state["plan"])
    return {"execution_result": result}

def review_node(state: AgentState) -> dict:
    resp = llm.invoke([
        SystemMessage("判断以下执行结果是否完成了任务，只回答 YES 或 NO。"),
        HumanMessage(f"任务：{state['task_input']}\n结果：{state['execution_result'][:1000]}"),
    ])
    return {"review_passed": "YES" in resp.content.upper()}

def route_after_review(state: AgentState) -> str:
    return END if state["review_passed"] else "plan"

graph = StateGraph(AgentState)
graph.add_node("plan", plan_node)
graph.add_node("execute", execute_node)
graph.add_node("review", review_node)
graph.add_edge(START, "plan")
graph.add_edge("plan", "execute")
graph.add_edge("execute", "review")
graph.add_conditional_edges("review", route_after_review)

checkpointer = get_checkpointer()
agent_app = graph.compile(checkpointer=checkpointer)
```

**通用 main.py 模板：**

```python
# agents_v2/<name>/main.py
import uvicorn
from pathlib import Path
from agents_v2.shared.a2a_server import create_a2a_app
from agents_v2.<name>.graph import agent_app

CARD_PATH = Path(__file__).parent / "agent_card.json"

async def handle_task(text: str, context: dict) -> str:
    config = {"configurable": {"thread_id": context.get("task_id", "default")}}
    result = await agent_app.ainvoke(
        {"task_input": text, "plan": "", "execution_result": "", "review_passed": False, "messages": []},
        config=config,
    )
    return result["execution_result"]

app = create_a2a_app(CARD_PATH, handle_task)

if __name__ == "__main__":
    uvicorn.run("agents_v2.<name>.main:app", host="0.0.0.0", port=<PORT>, reload=False)
```

---

### Task 4.1: 机械工程师 (:9001)

📦 System Prompt 来源：`employees/engineering/mechanical.md`  
📦 专长：build123d CAD 建模、结构设计、公差分析

- [ ] 创建 `agents_v2/mechanical/__init__.py`（空）
- [ ] 创建 `agents_v2/mechanical/prompts.py`（从 `employees/engineering/mechanical.md` 提取内容作为 `SYSTEM_PROMPT`）
- [ ] 创建 `agents_v2/mechanical/graph.py`（套用通用模板，NAME=mechanical，加 build123d 专用 execute 逻辑）
- [ ] 创建 `agents_v2/mechanical/agent_card.json`：
  ```json
  {
    "name": "机械工程师", "url": "http://localhost:9001", "version": "1.0.0",
    "description": "build123d CAD 建模、结构设计、公差分析、装配方案",
    "capabilities": {"streaming": false},
    "skills": [
      {"id": "cad_modeling", "name": "CAD 建模",
       "description": "使用 build123d 生成零件 STEP/STL 文件",
       "inputModes": ["text"], "outputModes": ["text", "file"]}
    ]
  }
  ```
- [ ] 创建 `agents_v2/mechanical/main.py`（PORT=9001）
- [ ] `python -m agents_v2.mechanical.main &` + `curl localhost:9001/.well-known/agent.json` → Agent Card
- [ ] `git add agents_v2/mechanical/ && git commit -m "feat(mechanical): A2A agent with LangGraph graph"`

---

### Task 4.2: 硬件工程师 (:9002)

📦 System Prompt 来源：`employees/engineering/hardware.md`  
📦 专长：PCB 设计、电路原理图、BOM 清单

- [ ] 创建 `agents_v2/hardware/` 目录结构（同 4.1，PORT=9002）
- [ ] `agent_card.json` 中 skills 设为硬件相关（PCB 设计、BOM 输出）
- [ ] `python -m agents_v2.hardware.main &` + `curl localhost:9002/.well-known/agent.json`
- [ ] `git add agents_v2/hardware/ && git commit -m "feat(hardware): A2A agent with LangGraph graph"`

---

### Task 4.3: 固件工程师 (:9003)

📦 System Prompt 来源：`employees/engineering/firmware.md`  
📦 专长：嵌入式 C/Python、ESP32 PWM 控制、通信协议

- [ ] 创建 `agents_v2/firmware/` 目录结构（PORT=9003）
- [ ] `git add agents_v2/firmware/ && git commit -m "feat(firmware): A2A agent with LangGraph graph"`

---

### Task 4.4: 算法工程师 (:9004)

📦 System Prompt 来源：`employees/engineering/algorithm.md`  
📦 专长：步态规划、逆运动学、控制算法

- [ ] 创建 `agents_v2/algorithm/` 目录结构（PORT=9004）
- [ ] `git add agents_v2/algorithm/ && git commit -m "feat(algorithm): A2A agent with LangGraph graph"`

---

### Task 4.5: 产品经理 (:9005)

📦 System Prompt 来源：`employees/management/product-manager.md`  
📦 专长：需求文档、PRD、功能优先级

- [ ] 创建 `agents_v2/product_manager/` 目录结构（PORT=9005）
- [ ] `git add agents_v2/product_manager/ && git commit -m "feat(product_manager): A2A agent"`

---

### Task 4.6: 测试工程师 (:9006)

📦 System Prompt 来源：`employees/engineering/testing.md`  
📦 专长：测试计划、验收标准、缺陷跟踪

- [ ] 创建 `agents_v2/testing/` 目录结构（PORT=9006）
- [ ] `git add agents_v2/testing/ && git commit -m "feat(testing): A2A agent"`

---

### Task 4.7: 成本工程师 (:9007)

📦 System Prompt 来源：`employees/engineering/cost.md`  
📦 专长：供应商调研、BOM 报价、成本优化

- [ ] 创建 `agents_v2/cost/` 目录结构（PORT=9007）
- [ ] `git add agents_v2/cost/ && git commit -m "feat(cost): A2A agent"`

---

### Task 4.8: 项目经理 (:9008)

📦 System Prompt 来源：`employees/management/project-manager.md`  
📦 专长：里程碑计划、任务分配、周报生成

- [ ] 创建 `agents_v2/project_manager/` 目录结构（PORT=9008）
- [ ] `git add agents_v2/project_manager/ && git commit -m "feat(project_manager): A2A agent"`

---

## Phase 5 — Feishu Bot 迁移

🔒 依赖 Phase 2（Backend API :8000 运行）。

### Task 5.1: feishu/ 模块重构

📦 现有 `system/feishu_bot.py` 有完整的飞书 WebSocket 逻辑和命令解析。  
新版改为调用 `backend/` HTTP API，不含业务逻辑。

**文件：**
- 新建 `feishu/__init__.py`（空）
- 新建 `feishu/bot.py`（重构自 system/feishu_bot.py）
- 新建 `feishu/sender.py`（消息发送封装）
- 新建 `feishu/commands/pipeline.py`（`?pipeline` → `POST /api/tasks`）
- 新建 `feishu/commands/approve.py`（`?approve` → `POST /api/tasks/{id}/approve`）
- 新建 `feishu/commands/report.py`（`?report` → `GET /api/tasks`）

**命令路由逻辑（保留 system/feishu_bot.py 的 WebSocket 接收机制，只改 handler）：**

```python
# feishu/commands/pipeline.py
import httpx

BACKEND_URL = "http://localhost:8000"

async def handle_pipeline(text: str) -> str:
    """?pipeline <需求> → 创建任务并启动 pipeline。"""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BACKEND_URL}/api/tasks",
                              json={"title": text[:80], "description": text, "priority": "P1"})
        task = r.json()
        # 同时触发 TechLead pipeline
        await client.post("http://localhost:9000/pipeline",
                          json={"description": text, "task_id": task["id"]})
    return f"✅ 任务已创建：{task['id'][:8]}…\n标题：{task['title']}"
```

- [ ] 创建 `feishu/` 目录及所有模块文件
- [ ] 从 `system/feishu_bot.py` 提取 WebSocket 连接逻辑到 `feishu/bot.py`
- [ ] 将命令分发重写为调用 backend HTTP API
- [ ] `python -m feishu.bot` → 正常连接飞书 WebSocket
- [ ] 飞书群发 `?report` → 返回任务列表
- [ ] `git add feishu/ && git commit -m "feat(feishu): migrate bot to use backend HTTP API"`

---

## Phase 6 — Frontend Vue 3 (:5173)

🔒 依赖 Phase 2（Backend API 提供数据）。

### Task 6.1: Vite + Vue 3 项目初始化

```bash
cd /Users/liyijiang/work/company
npm create vue@latest frontend -- --typescript --router --pinia
cd frontend && npm install element-plus axios
```

**修改 `frontend/vite.config.ts` 添加 proxy：**
```typescript
server: {
  proxy: {
    "/api": { target: "http://localhost:8000", changeOrigin: true },
    "/api/events": { target: "http://localhost:8000", changeOrigin: true,
                     ws: false, configure: (proxy) => { proxy.on("proxyReq", (req) => { req.setHeader("Accept", "text/event-stream") }) } }
  }
}
```

- [ ] `npm create vue@latest frontend` 按提示选 TypeScript + Router + Pinia
- [ ] `npm install element-plus axios`
- [ ] 配置 vite.config.ts proxy
- [ ] `npm run dev` → http://localhost:5173 正常显示

---

### Task 6.2: Task Board + Pipeline 视图

**文件：**
- 新建 `frontend/src/api/client.ts`（axios 封装）
- 新建 `frontend/src/stores/tasks.ts`（Pinia，SSE 实时更新）
- 新建 `frontend/src/views/ProjectView.vue`（看板主页）
- 新建 `frontend/src/components/project/TaskBoard.vue`（按状态分列）
- 新建 `frontend/src/components/project/Pipeline.vue`（步骤链路）

**frontend/src/stores/tasks.ts 核心逻辑：**
```typescript
import { defineStore } from 'pinia'
import axios from 'axios'

export const useTaskStore = defineStore('tasks', {
  state: () => ({ tasks: [] as any[], sse: null as EventSource | null }),
  actions: {
    async fetchTasks() {
      const { data } = await axios.get('/api/tasks')
      this.tasks = data
    },
    startSSE() {
      this.sse = new EventSource('/api/events')
      this.sse.onmessage = (e) => {
        const event = JSON.parse(e.data)
        const task = this.tasks.find(t => t.id === event.task_id)
        if (task) task.status = event.status
      }
    },
  },
})
```

- [ ] 创建 `frontend/src/api/client.ts`, `stores/tasks.ts`
- [ ] 创建 TaskBoard.vue（三列：pending / running / done，按 priority 排序）
- [ ] 创建 Pipeline.vue（展示 task_steps 链路）
- [ ] `npm run dev` 验证看板显示任务列表，SSE 实时刷新状态
- [ ] `git add frontend/ && git commit -m "feat(frontend): task board with SSE real-time updates"`

---

### Task 6.3: Chat 视图

**文件：**
- 新建 `frontend/src/stores/chat.ts`
- 新建 `frontend/src/views/ChatView.vue`
- 新建 `frontend/src/components/chat/GroupChat.vue`
- 新建 `frontend/src/components/chat/DirectChat.vue`

- [ ] 实现 GroupChat（群聊 feed，向所有员工广播）
- [ ] 实现 DirectChat（单聊，员工选择器 + 对话框）
- [ ] 后端 `POST /api/chat/group` 和 `POST /api/chat/direct/{employee}` 接口需同步在 Task 2.2 中实现
- [ ] `git add frontend/src/views/ frontend/src/components/chat/ && git commit -m "feat(frontend): chat views"`

---

## Phase 7 — 集成联调

🔒 依赖所有前序 Phase 完成。

### Task 7.1: 全链路 Smoke Test

**启动顺序：**
```bash
docker compose -f infra/docker-compose.yml up postgres redis -d
uvicorn backend.main:app --port 8000 &
python -m agents_v2.tech_lead.main &
python -m agents_v2.mechanical.main &
python -m agents_v2.product_manager.main &
# (其余员工按需启动)
```

**测试用例：**

1. 创建任务 API：
   ```bash
   curl -X POST localhost:8000/api/tasks \
     -H "Content-Type: application/json" \
     -d '{"title":"设计四足机器狗腿部结构","description":"2-DOF，MG996R 舵机","priority":"P0"}'
   ```
   → 返回 task JSON，记录 `id`

2. 触发 pipeline：
   ```bash
   curl -X POST localhost:9000/pipeline \
     -H "Content-Type: application/json" \
     -d '{"description":"设计四足机器狗腿部结构，2-DOF，MG996R 舵机","task_id":"<id>"}'
   ```
   → TechLead 拆解任务，派遣机械工程师

3. SSE 实时进度：
   ```bash
   curl -N localhost:8000/api/events
   ```
   → 应收到 task_events 推送

4. 飞书指令测试：
   - 飞书群发 `?pipeline 设计腿部关节` → bot 调 backend → TechLead 响应

- [ ] 完整启动所有服务
- [ ] 跑通上述 4 个测试用例
- [ ] 验证前端看板实时显示任务状态变更
- [ ] `git tag v0.1.0-mvp`

---

## 附录：进程启动汇总

```bash
# 基础设施
docker compose -f infra/docker-compose.yml up postgres redis -d

# 后端
uvicorn backend.main:app --port 8000

# TechLead
python -m agents_v2.tech_lead.main

# 员工 Agents（按需）
python -m agents_v2.mechanical.main
python -m agents_v2.hardware.main
python -m agents_v2.firmware.main
python -m agents_v2.algorithm.main
python -m agents_v2.product_manager.main
python -m agents_v2.testing.main
python -m agents_v2.cost.main
python -m agents_v2.project_manager.main

# 飞书
python -m feishu.bot

# 前端
cd frontend && npm run dev
```

## 附录：目录结构（完成后）

```
company/
├── agents/              # 现有，不动（作兜底）
├── agents_v2/           # 新 Agent 系统
│   ├── shared/          # claude_client, db, a2a_server, claude_runner
│   ├── tech_lead/       # Supervisor :9000
│   ├── mechanical/      # :9001
│   ├── hardware/        # :9002
│   ├── firmware/        # :9003
│   ├── algorithm/       # :9004
│   ├── product_manager/ # :9005
│   ├── testing/         # :9006
│   ├── cost/            # :9007
│   └── project_manager/ # :9008
├── backend/             # FastAPI :8000
│   ├── core/            # config, db
│   ├── models/          # task, message
│   ├── schemas/         # pydantic schemas
│   └── api/routes/      # tasks, employees, chat, events
├── feishu/              # 飞书 Bot（重构自 system/）
├── frontend/            # Vue 3 :5173
├── alembic/             # DB migrations
├── infra/               # docker-compose + .env
└── system/              # 旧版（保留，迁移完成后退役）
```
