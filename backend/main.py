from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.audit import router as audit_router
from backend.api.routes.chat import router as chat_router
from backend.api.routes.employees import router as employees_router
from backend.api.routes.events import router as events_router
from backend.api.routes.llm_stats import router as llm_stats_router
from backend.api.routes.system_config import router as system_config_router
from backend.api.routes.tasks import router as tasks_router
from backend.services import registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    await registry.warmup()
    registry.start_listener()
    yield
    await registry.stop_listener()


app = FastAPI(title="Company Backend", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks_router)
app.include_router(employees_router)
app.include_router(chat_router)
app.include_router(events_router)
app.include_router(system_config_router)
app.include_router(audit_router)
app.include_router(llm_stats_router)


@app.get("/health")
def health():
    return {"status": "ok"}
