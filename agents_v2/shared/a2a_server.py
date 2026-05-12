"""
A2A Server 基类：用纯 FastAPI + httpx 实现 A2A 协议。
每个员工 Agent 调用 create_a2a_app() 得到一个标准 A2A FastAPI 应用。

协议端点：
  GET /.well-known/agent.json  → Agent Card（能力声明）
  POST /                       → JSON-RPC 2.0 任务接收
"""
import json
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def create_a2a_app(
    agent_card_path: Path,
    handle_task: Callable[[str, dict], Awaitable[str]],
) -> FastAPI:
    """
    创建标准 A2A FastAPI 应用。

    handle_task(message_text, context_dict) → result_text
    由各员工实现并传入。
    """
    app = FastAPI()
    agent_card = json.loads(agent_card_path.read_text(encoding="utf-8"))

    @app.get("/.well-known/agent.json")
    async def get_agent_card():
        return JSONResponse(agent_card)

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
        context = params.get("metadata", {})

        try:
            result_text = await handle_task(text, context)
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
                "artifacts": [{"parts": [{"type": "text", "text": result_text}]}],
            },
        })

    @app.get("/health")
    async def health():
        return {"status": "ok", "agent": agent_card.get("name", "unknown")}

    return app


async def call_agent(url: str, message: str, context: dict | None = None, timeout: int = 600) -> str:
    """
    A2A 客户端：向指定员工 URL 发送任务，返回结果文本。
    """
    import httpx

    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tasks/send",
        "params": {
            "message": {"parts": [{"type": "text", "text": message}]},
            "metadata": context or {},
        },
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"A2A error from {url}: {data['error']}")
        return data["result"]["artifacts"][0]["parts"][0]["text"]
