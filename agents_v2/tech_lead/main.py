"""
TechLead A2A Server on port 9000.
Endpoints:
  GET  /.well-known/agent.json  → Agent Card
  POST /                        → A2A JSON-RPC 2.0 (tasks/send)
  POST /pipeline                → run supervisor graph
  GET  /health
"""
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agents_v2.shared.claude_client import make_langchain_llm
from agents_v2.shared.db import async_checkpointer_ctx
from agents_v2.tech_lead.supervisor import build_supervisor

CHAT_SYSTEM_PROMPT = """你是机器狗公司的技术负责人（Tech Lead）。
性格特点：务实、简洁、有大局观，偶尔幽默。
职责：技术决策、架构评审、协调各工程师团队。
回复风格：直接、专业，用中文，不超过 200 字。
不要输出 JSON，不要列大纲，像真实的技术总监那样自然对话。"""

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
    async with async_checkpointer_ctx() as cp:
        app.state.supervisor = build_supervisor(cp)
        yield


app = FastAPI(title="TechLead Supervisor", version="1.0", lifespan=lifespan)


class PipelineRequest(BaseModel):
    description: str
    task_id: str = "default"


@app.get("/.well-known/agent.json")
def agent_card():
    return JSONResponse(AGENT_CARD)


@app.post("/")
async def handle_jsonrpc(request: Request):
    body = await request.json()
    method = body.get("method", "")
    rpc_id = body.get("id", 1)

    if method not in ("tasks/send", "tasks/sendSubscribe"):
        return JSONResponse({
            "jsonrpc": "2.0", "id": rpc_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        })

    params = body.get("params", {})
    parts = params.get("message", {}).get("parts", [])
    text = next((p["text"] for p in parts if "text" in p), "")

    try:
        llm = make_langchain_llm()
        resp = await llm.ainvoke([
            SystemMessage(CHAT_SYSTEM_PROMPT),
            HumanMessage(text),
        ])
        reply = resp.content
    except Exception as exc:
        return JSONResponse({
            "jsonrpc": "2.0", "id": rpc_id,
            "error": {"code": -32000, "message": str(exc)},
        })

    return JSONResponse({
        "jsonrpc": "2.0", "id": rpc_id,
        "result": {
            "id": f"task-{rpc_id}",
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"type": "text", "text": reply}]}],
        },
    })


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
