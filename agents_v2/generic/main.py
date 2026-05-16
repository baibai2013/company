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
from agents_v2.shared.scheduler import AgentScheduler
from agents_v2.shared.smart_graph import build_smart_agent
from agents_v2.shared.tools import resolve_tools
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
    # 把员工自己的飞书凭证写进进程 env，供工具（线程池内）直接读取
    if getattr(cfg, "feishu_app_id", None):
        os.environ["EMPLOYEE_FEISHU_APP_ID"] = cfg.feishu_app_id
    if getattr(cfg, "feishu_app_secret", None):
        os.environ["EMPLOYEE_FEISHU_APP_SECRET"] = cfg.feishu_app_secret
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
        try:
            mod = __import__(f"agents_v2.{_employee_key}.prompts", fromlist=["CC_PROMPT"])
            cc_prompt = getattr(mod, "CC_PROMPT", "") or ""
        except Exception:
            cc_prompt = ""

    # 解析工具：behavior.tools 的 + 所有员工默认获得的工具
    tool_names = list((cfg.behavior or {}).get("tools", [])) if cfg else []
    # 默认工具：定时任务管理 + 消息发送（让 agent 能主动推送并感知发送结果）
    for t in ("schedule_task", "cancel_scheduled_task", "list_scheduled_tasks",
              "send_feishu_message", "send_group_chat_message", "recall_history"):
        if t not in tool_names:
            tool_names.append(t)
    tools = resolve_tools(tool_names) if tool_names else None

    async with async_checkpointer_ctx() as cp:
        inner_app.state.agent = build_smart_agent(_employee_key, cp, cc_prompt=cc_prompt, tools=tools)

        # 启动定时任务（无论当前是否有任务，都创建 scheduler 以便后续动态添加）
        # 传入 tools 使 direct 模式可以跳过 LLM 直接调工具
        scheduler = AgentScheduler(
            _employee_key,
            agent_fn=_run_agent_for_scheduler,
            output_fn=_output_for_scheduler,
            tools=tools,
        )
        await scheduler.start()
        inner_app.state.scheduler = scheduler

        # 确保 PG NOTIFY listener 在 lifespan 里就跑起来（不等第一条消息）
        registry.start_listener()

        # 注册配置变更 hook：新增/删除任务时自动 reload scheduler
        async def _on_config_change(changed_key: str | None):
            if changed_key is None or changed_key == _employee_key:
                await inner_app.state.scheduler.reload()

        registry.register_change_hook(_on_config_change)

        yield

        await scheduler.stop()


async def _run_agent_for_scheduler(prompt: str, context: dict) -> str:
    """定时任务调用 agent graph。"""
    from agents_v2.shared.runner import run_with_events
    task_id = context.get("task_id", "scheduler")
    cfg = {"configurable": {"thread_id": task_id}}
    data = await run_with_events(
        _app_ref.state.agent, prompt, cfg,
        _employee_key, task_id, context=context,
    )
    # data 是 dict: {route, plan, result, cc}
    return data.get("result", "") if isinstance(data, dict) else str(data)


async def _output_for_scheduler(output_to: str, task_name: str, result: str, feishu_chat_id: str = "") -> None:
    """定时任务结果推送。feishu_chat_id 优先于全局 FEISHU_CHAT_ID（用于单聊回复）。"""
    import logging
    log = logging.getLogger(f"scheduler.{_employee_key}")

    if output_to == "log" or not output_to:
        log.info("[%s] %s: %s", _employee_key, task_name, result[:200])
        return

    if output_to == "feishu":
        try:
            from feishu.sender import make_client, send_rich_card
            from backend.core.config import settings
            chat_id = feishu_chat_id or settings.FEISHU_CHAT_ID
            if chat_id:
                send_rich_card(make_client(), chat_id, f"📋 {task_name}", result, "blue")
            else:
                log.warning("feishu push skipped: no chat_id configured")
        except Exception as e:
            log.warning("feishu push failed: %s", e)
        return

    if output_to in ("group_chat", "kanban"):
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                await session.post(
                    "http://localhost:8000/api/chat/group",
                    json={"sender": _employee_key, "content": result},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
        except Exception as e:
            log.warning("group_chat push failed: %s", e)
        return

    log.info("[%s] unknown output_to=%s, result: %s", _employee_key, output_to, result[:200])


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


@app.get("/scheduler/jobs")
def scheduler_jobs():
    """查询当前 agent 的定时任务状态。"""
    sched = getattr(app.state, "scheduler", None)
    if not sched:
        return {"jobs": [], "message": "no scheduler configured"}
    return {"jobs": sched.list_jobs()}


@app.post("/scheduler/reload")
async def scheduler_reload():
    """配置变更后重建定时任务列表（供 tools.py 调用）。"""
    sched = getattr(app.state, "scheduler", None)
    if not sched:
        return {"ok": False, "error": "no scheduler"}
    await sched.reload()
    return {"ok": True, "jobs": len(sched.list_jobs())}


@app.post("/scheduler/run/{task_id}")
async def scheduler_run_now(task_id: str):
    """立即执行一次指定定时任务。"""
    sched = getattr(app.state, "scheduler", None)
    if not sched:
        return {"ok": False, "error": "no scheduler"}
    result = await sched.run_once(task_id)
    if result is None:
        return {"ok": False, "error": f"task {task_id} not found"}
    return {"ok": True, "result": result[:1000]}


@app.post("/scheduler/event")
async def scheduler_receive_event(event: dict):
    """P4.3 接收来自 backend 路由的跨员工事件，立即执行匹配的 event-triggered 任务。"""
    sched = getattr(app.state, "scheduler", None)
    if not sched:
        return {"ok": False, "error": "no scheduler"}
    result = await sched.fire_event(event)
    return {"ok": True, "result": result}


if __name__ == "__main__":
    cfg = registry.get_effective_sync(_employee_key)
    uvicorn.run(
        "agents_v2.generic.main:app",
        host="0.0.0.0",
        port=cfg.agent_port,
        reload=False,
    )
