"""
Agent Worker — FastAPI webhook server
n8n 调用此服务来执行员工任务

POST /run
  body: {"employee": "mechanical", "task": "...", "context": "...", "project_root": "..."}
  response: {"summary": "...", "output": "...", "content": "..."}

GET /health
  response: {"status": "ok", "employees": [...]}
"""
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv(Path(__file__).parent.parent / "infra" / ".env")

from agents.employees import REGISTRY  # noqa: E402
from agents.base import post_to_mattermost, format_completion_card  # noqa: E402

app = FastAPI(title="Agent Worker", version="1.0")

STATUS_CHANNEL = os.getenv("MM_STATUS_CHANNEL_ID", "")
APPROVAL_CHANNEL = os.getenv("MM_APPROVAL_CHANNEL_ID", "")


class RunRequest(BaseModel):
    employee: str
    task: str
    task_id: str = "unknown"
    context: str = ""
    project_root: str = ""
    is_gate: bool = False
    image_base64: str = ""
    image_media_type: str = "image/jpeg"


class RunResponse(BaseModel):
    summary: str
    output: str
    content: str
    images: list[str] = []


@app.get("/health")
def health():
    return {"status": "ok", "employees": list(REGISTRY.keys())}


@app.post("/run", response_model=RunResponse)
def run_employee(req: RunRequest):
    if req.employee not in REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown employee: {req.employee}. Valid: {list(REGISTRY.keys())}")

    fn = REGISTRY[req.employee]
    result = fn(
        task=req.task,
        context=req.context,
        project_root=req.project_root,
        image_base64=req.image_base64 or None,
        image_media_type=req.image_media_type,
    )

    if STATUS_CHANNEL:
        card = format_completion_card(
            task_id=req.task_id,
            employee=req.employee,
            summary=result["summary"],
            output_path=result["output"],
        )
        post_to_mattermost(STATUS_CHANNEL, card)

    if req.is_gate and APPROVAL_CHANNEL:
        from agents.base import format_gate_card
        gate_msg = format_gate_card(
            task_id=req.task_id,
            task_name=req.task,
            description=result["summary"],
            action="在此频道回复「approved」解锁后续任务",
        )
        post_to_mattermost(APPROVAL_CHANNEL, gate_msg)

    return RunResponse(**result)


if __name__ == "__main__":
    uvicorn.run("agents.worker:app", host="0.0.0.0", port=8080, reload=True)
