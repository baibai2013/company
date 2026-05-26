import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.audit import router as audit_router
from backend.api.routes.chat import router as chat_router
from backend.api.routes.employees import router as employees_router, scheduler_router
from backend.api.routes.events import router as events_router
from backend.api.routes.llm_stats import router as llm_stats_router
from backend.api.routes.projects import router as projects_router
from backend.api.routes.system_config import router as system_config_router
from backend.api.routes.tasks import router as tasks_router
from backend.chat.kanban_adapter import kanban_adapter
from backend.chat.task_adapter import task_adapter
from backend.chat.ws import router as ws_router
from backend.core.otel import init_tracer
from backend.services import registry
from backend.services.delegation_supervisor import run_supervisor_loop

log = logging.getLogger("backend.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # OTel 优先 init:env OTEL_EXPORTER_OTLP_ENDPOINT 没设时退化 NoOp,无副作用
    init_tracer()

    await registry.warmup()
    registry.start_listener()

    # 启动平台适配器（连接 Redis 事件总线）
    from group_chat.event_bus import GroupEventBusPool
    bus_pool = GroupEventBusPool()
    await bus_pool.connect()
    await kanban_adapter.start(bus_pool)
    # B1.3: 任务级编排桥,订阅 speak_req:*:task:*,A2A 调员工 → publish speak_resp
    task_bus_pool = GroupEventBusPool()
    await task_bus_pool.connect()
    await task_adapter.start(task_bus_pool)

    # 提案 1 派活状态机的守护协程:每 30s 扫一次 in-flight delegations,nudge / escalate
    supervisor_task = asyncio.create_task(
        run_supervisor_loop(interval_seconds=30),
        name="delegation_supervisor",
    )

    yield

    supervisor_task.cancel()
    try:
        await supervisor_task
    except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
        if not isinstance(exc, asyncio.CancelledError):
            log.warning("supervisor 任务关闭异常: %s", exc)

    await task_adapter.stop()
    await task_bus_pool.disconnect()
    await kanban_adapter.stop()
    await bus_pool.disconnect()
    await registry.stop_listener()


app = FastAPI(title="Company Backend", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks_router)
app.include_router(projects_router)
app.include_router(employees_router)
app.include_router(scheduler_router)
app.include_router(chat_router)
app.include_router(ws_router)
app.include_router(events_router)
app.include_router(system_config_router)
app.include_router(audit_router)
app.include_router(llm_stats_router)


@app.get("/health")
def health():
    return {"status": "ok"}
