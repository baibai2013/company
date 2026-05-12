"""
成本工程师 A2A Server on port 9007.
"""
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from agents_v2.shared.a2a_server import create_a2a_app
from agents_v2.shared.db import async_checkpointer_ctx
from agents_v2.cost.graph import build_agent

CARD_PATH = Path(__file__).parent / "agent_card.json"
_app_ref = None


@asynccontextmanager
async def lifespan(inner_app: FastAPI):
    async with async_checkpointer_ctx() as cp:
        inner_app.state.agent = build_agent(cp)
        yield


async def handle_task(text: str, context: dict) -> str:
    agent = _app_ref.state.agent
    config = {"configurable": {"thread_id": context.get("task_id", "default")}}
    result = await agent.ainvoke(
        {"task_input": text, "plan": "", "execution_result": "", "messages": []},
        config=config,
    )
    return result["execution_result"]


_base = create_a2a_app(CARD_PATH, handle_task)
app = FastAPI(title="成本工程师", version="1.0", lifespan=lifespan)
_app_ref = app

for route in _base.routes:
    app.routes.append(route)


if __name__ == "__main__":
    uvicorn.run("agents_v2.cost.main:app", host="0.0.0.0", port=9007, reload=False)
