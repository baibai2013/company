from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.chat import router as chat_router
from backend.api.routes.employees import router as employees_router
from backend.api.routes.events import router as events_router
from backend.api.routes.tasks import router as tasks_router

app = FastAPI(title="Company Backend", version="1.0")

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


@app.get("/health")
def health():
    return {"status": "ok"}
