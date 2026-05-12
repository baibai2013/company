"""
TechLead A2A Server on port 9000.
Endpoints:
  GET  /.well-known/agent.json  → Agent Card
  POST /pipeline                → run supervisor graph
  GET  /health
"""
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents_v2.shared.db import checkpointer_ctx
from agents_v2.tech_lead.supervisor import build_supervisor

AGENT_CARD = {
    "name": "技术负责人",
    "description": "技术决策、架构评审、任务编排，将需求拆解分派给各专业工程师",
    "url": "http://localhost:9000",
    "version": "1.0.0",
    "capabilities": {"streaming": False},
    "skills": [
        {
            "id": "orchestrate",
            "name": "任务编排",
            "description": "接收需求，分解并协调所有工程师完成任务",
            "inputModes": ["text"],
            "outputModes": ["text"],
        }
    ],
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    with checkpointer_ctx() as cp:
        app.state.supervisor = build_supervisor(cp)
        yield


app = FastAPI(title="TechLead Supervisor", version="1.0", lifespan=lifespan)


class PipelineRequest(BaseModel):
    description: str
    task_id: str = "default"


@app.get("/.well-known/agent.json")
def agent_card():
    return JSONResponse(AGENT_CARD)


@app.post("/pipeline")
async def run_pipeline(req: PipelineRequest):
    supervisor = app.state.supervisor
    config = {"configurable": {"thread_id": req.task_id}}
    result = await supervisor.ainvoke(
        {
            "task_description": req.description,
            "domain_plans": {},
            "completed_outputs": {},
            "next_employee": "",
            "phase": "init",
            "messages": [],
        },
        config=config,
    )
    return {"status": "done", "outputs": result["completed_outputs"]}


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("agents_v2.tech_lead.main:app", host="0.0.0.0", port=9000, reload=False)
