"""
算法工程师 A2A Server on port 9004.
"""
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from agents_v2.shared.a2a_server import create_a2a_app
from agents_v2.shared.db import async_checkpointer_ctx
from agents_v2.algorithm.graph import build_agent

CARD_PATH = Path(__file__).parent / "agent_card.json"
_app_ref = None


@asynccontextmanager
async def lifespan(inner_app: FastAPI):
    async with async_checkpointer_ctx() as cp:
        inner_app.state.agent = build_agent(cp)
        yield


EMPLOYEE_NAME = "algorithm"

async def handle_task(text: str, context: dict) -> str:
    import json
    from agents_v2.shared.runner import run_with_events
    task_id = context.get("task_id", "default")
    config = {"configurable": {"thread_id": task_id}}
    data = await run_with_events(_app_ref.state.agent, text, config, EMPLOYEE_NAME, task_id, context=context)
    return json.dumps(data, ensure_ascii=False)


_base = create_a2a_app(CARD_PATH, handle_task)
app = FastAPI(title="算法工程师", version="1.0", lifespan=lifespan)
_app_ref = app

for route in _base.routes:
    app.routes.append(route)


if __name__ == "__main__":
    uvicorn.run("agents_v2.algorithm.main:app", host="0.0.0.0", port=9004, reload=False)
