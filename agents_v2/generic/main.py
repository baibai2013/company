"""
通用员工 Agent — 一份代码服务任意员工。

启动方式（依赖 EMPLOYEE_KEY 环境变量）：

    EMPLOYEE_KEY=marketing python -m agents_v2.generic.main

或直接传 CLI 参数：

    python -m agents_v2.generic.main marketing

读取员工配置（包含 system_prompt / persona / agent_port / cc_prompt）来自 DB
registry。新员工无需复制目录、无需写 graph.py / main.py / agent_card.json。

行为与 agents_v2/<key>/main.py 完全等价，唯一差别是 agent_card 用模板生成。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from agents_v2.shared.a2a_server import create_a2a_app
from agents_v2.shared.db import async_checkpointer_ctx
from agents_v2.shared.smart_graph import build_smart_agent
from backend.services import registry

_app_ref: FastAPI | None = None
_employee_key: str = ""


def _load() -> tuple[str, dict]:
    key = os.environ.get("EMPLOYEE_KEY") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not key:
        raise SystemExit("EMPLOYEE_KEY env var or CLI arg required")
    registry.warmup_sync()
    cfg = registry.get_effective_sync(key)
    if not cfg or not cfg.active:
        raise SystemExit(f"employee '{key}' not found or inactive in DB")
    if not cfg.agent_port:
        raise SystemExit(f"employee '{key}' has no agent_port set")
    card = {
        "name": f"{cfg.emoji} {cfg.name}",
        "url": f"http://localhost:{cfg.agent_port}",
        "version": "1.0.0",
        "description": cfg.role_desc or cfg.system_prompt[:200],
        "capabilities": {"streaming": False},
        "skills": [{
            "id": cfg.key,
            "name": cfg.name,
            "description": cfg.role_desc or "",
            "inputModes":  ["text"],
            "outputModes": ["text"],
        }],
    }
    return key, card


def _write_card(card: dict) -> Path:
    f = Path(tempfile.gettempdir()) / f"agent_card_{card['skills'][0]['id']}.json"
    f.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    return f


@asynccontextmanager
async def lifespan(inner_app: FastAPI):
    cfg = registry.get_effective_sync(_employee_key)
    cc_prompt = ""
    # CC behavior: only PM/product_manager have CC prompt today; check behavior flag.
    if cfg and (cfg.behavior or {}).get("auto_cc_specialists"):
        # Try to import a CC_PROMPT from the legacy module; otherwise empty.
        try:
            mod = __import__(f"agents_v2.{_employee_key}.prompts", fromlist=["CC_PROMPT"])
            cc_prompt = getattr(mod, "CC_PROMPT", "") or ""
        except Exception:
            cc_prompt = ""
    async with async_checkpointer_ctx() as cp:
        inner_app.state.agent = build_smart_agent(_employee_key, cp, cc_prompt=cc_prompt)
        yield


async def handle_task(text: str, context: dict) -> str:
    from agents_v2.shared.runner import run_with_events
    task_id = context.get("task_id", "default")
    cfg = {"configurable": {"thread_id": task_id}}
    data = await run_with_events(
        _app_ref.state.agent, text, cfg,
        _employee_key, task_id, context=context,
    )
    return json.dumps(data, ensure_ascii=False)


# Module-level setup runs on import (uvicorn imports this module by string).
_employee_key, _card = _load()
_card_path = _write_card(_card)

_base = create_a2a_app(_card_path, handle_task)
app = FastAPI(title=_card["name"], version="1.0", lifespan=lifespan)
_app_ref = app

for _route in _base.routes:
    app.routes.append(_route)


if __name__ == "__main__":
    cfg = registry.get_effective_sync(_employee_key)
    uvicorn.run(
        "agents_v2.generic.main:app",
        host="0.0.0.0",
        port=cfg.agent_port,
        reload=False,
    )
