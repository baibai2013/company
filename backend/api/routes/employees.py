"""
Employee CRUD + process control + LLM stats.

All employee config (persona / models / prompts / credentials) lives in the
employee table. This router exposes it via REST + maps process commands
through ProcessManager.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from backend.repos import audit_repo, employee_repo, llm_call_repo
from backend.services import process_manager, registry

log = logging.getLogger("backend.api.employees")

router = APIRouter(prefix="/api/employees", tags=["employees"])

# P4.3: 跨员工事件路由（独立 router，挂到 /api/scheduler）
scheduler_router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


def _serialize(emp_dict: dict, redact_secret: bool = True) -> dict:
    out = dict(emp_dict)
    if redact_secret and out.get("feishu_app_secret"):
        out["feishu_app_secret"] = "•••" + out["feishu_app_secret"][-4:]
    return out


# ── Read ─────────────────────────────────────────────────────────────────────

@router.get("")
async def list_employees(active_only: bool = Query(False)) -> list[dict]:
    items = await registry.list_all(active_only=active_only)
    out = []
    for e in items:
        s = process_manager.status(e["key"])
        row = _serialize(e)
        row["agent_status"] = s["agent"]
        row["bot_status"] = s["bot"]
        out.append(row)
    return out


@router.get("/{key}")
async def get_employee(key: str, reveal_secret: bool = Query(False)) -> dict:
    raw = await registry.get_raw(key)
    if not raw:
        raise HTTPException(404, f"employee '{key}' not found")
    s = process_manager.status(key)
    row = _serialize(raw, redact_secret=not reveal_secret)
    row["agent_status"] = s["agent"]
    row["bot_status"] = s["bot"]
    return row


@router.get("/{key}/effective")
async def effective_config(key: str) -> dict:
    cfg = await registry.get_effective(key)
    if not cfg:
        raise HTTPException(404, f"employee '{key}' not found")
    return {
        "key": cfg.key,
        "name": cfg.name,
        "emoji": cfg.emoji,
        "role_desc": cfg.role_desc,
        "agent_port": cfg.agent_port,
        "active": cfg.active,
        "system_prompt": cfg.system_prompt,
        "persona": cfg.persona,
        "llm_calls": cfg.llm_calls,
        "behavior": cfg.behavior,
        "global_prompts_keys": list((cfg.global_prompts or {}).keys()),
    }


# ── Write ────────────────────────────────────────────────────────────────────

@router.post("")
async def create_employee(record: dict = Body(...)) -> dict:
    if not record.get("key"):
        raise HTTPException(400, "key required")
    if await registry.get_raw(record["key"]):
        raise HTTPException(409, f"employee '{record['key']}' already exists")
    if not record.get("agent_port"):
        record["agent_port"] = await employee_repo.next_available_port()
    record.setdefault("active", True)
    return await registry.create(record, actor="api")


@router.patch("/{key}")
async def update_employee(key: str, patch: dict = Body(...)) -> dict:
    if "key" in patch:
        del patch["key"]
    out = await registry.update(key, patch, actor="api")
    if not out:
        raise HTTPException(404, f"employee '{key}' not found")
    return _serialize(out)


@router.delete("/{key}")
async def deactivate_employee(key: str) -> dict:
    process_manager.stop(key)
    ok = await registry.deactivate(key, actor="api")
    if not ok:
        raise HTTPException(404, f"employee '{key}' not found")
    return {"ok": True}


# ── Process control ──────────────────────────────────────────────────────────

@router.post("/{key}/start")
async def start_employee(key: str) -> dict:
    await registry.get_effective(key)  # ensure cache contains the employee
    return process_manager.start(key)


@router.post("/{key}/stop")
async def stop_employee(key: str) -> dict:
    await registry.get_effective(key)
    return process_manager.stop(key)


@router.post("/{key}/restart")
async def restart_employee(key: str) -> dict:
    await registry.get_effective(key)
    return process_manager.restart(key)


@router.post("/{key}/reload")
async def reload_employee(key: str) -> dict:
    """Force registry cache invalidation. Running processes pick up changes on next LLM call."""
    await registry.invalidate(key)
    return {"ok": True, "reloaded": key}


@router.post("/{key}/dispatch")
async def dispatch_to_employee(key: str, payload: dict = Body(...)) -> dict:
    """阶段 6.5：跨员工委托入口。异步 fire-and-forget。

    payload:
        task: str            必填，要委托的任务描述
        context_files: list  可选，相关文件路径
        from_employee: str   可选，发起委托的员工 key（用于死循环检测 + 卡片标记来源）
        chat_id: str         可选，飞书 chat_id（让结果回到原对话）

    返回:
        {task_id: 唯一 ID, status: 'delegated'}
    立即返回，不等目标员工完成。
    """
    import asyncio
    import os
    import uuid
    from datetime import datetime, timezone

    cfg = await registry.get_effective(key)
    if not cfg:
        raise HTTPException(404, f"employee '{key}' not found")
    if not cfg.agent_port:
        raise HTTPException(400, f"employee '{key}' has no agent_port")

    task = (payload.get("task") or "").strip()
    if not task:
        raise HTTPException(400, "task is required")

    from_employee = payload.get("from_employee", "")
    context_files = payload.get("context_files") or []
    chat_id = payload.get("chat_id", "")

    task_id = uuid.uuid4().hex[:12]
    # 给 task 加上来源标记，让目标员工知道是委托来的
    if from_employee:
        prefix = f"【来自 {from_employee} 的委托】\n"
        if context_files:
            prefix += f"相关文件：{', '.join(context_files[:5])}\n"
        prefix += "\n"
        full_task = prefix + task
    else:
        full_task = task

    # 异步触发目标员工的 a2a JSON-RPC tasks/send
    import httpx as _httpx
    a2a_url = f"http://localhost:{cfg.agent_port}/"
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": task_id,
        "method": "tasks/send",
        "params": {
            "message": {"parts": [{"type": "text", "text": full_task}]},
            "metadata": {
                "task_id": f"delegate_{task_id}",
                "chat_id": chat_id,
                "from_employee": from_employee,
            },
        },
    }

    async def _fire():
        try:
            async with _httpx.AsyncClient(timeout=300) as client:
                await client.post(a2a_url, json=rpc_payload)
        except Exception as exc:
            log.warning("delegate %s → %s 失败: %s", from_employee, key, exc)

    asyncio.create_task(_fire())

    return {
        "task_id": task_id,
        "status": "delegated",
        "target_employee": key,
        "from_employee": from_employee,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{key}/status")
async def status_employee(key: str) -> dict:
    await registry.get_effective(key)
    return process_manager.status(key)


# ── Audit / observability (delegated; full pages in own routers) ─────────────

@router.get("/{key}/audit")
async def employee_audit(key: str, limit: int = Query(20, le=200)) -> list[dict]:
    rows = await audit_repo.list_recent(target_type="employee", target_key=key, limit=limit)
    return [audit_repo.to_dict(r) for r in rows]


@router.get("/{key}/llm-stats")
async def employee_llm_stats(key: str, hours: int = Query(24, ge=1, le=720)) -> dict:
    return await llm_call_repo.stats(employee_key=key, hours=hours)


# ── 定时任务管理 ───────────────────────────────────────────────────────────────

import uuid as _uuid


@router.get("/{key}/scheduled-tasks")
async def list_scheduled_tasks(key: str) -> list[dict]:
    raw = await registry.get_raw(key)
    if not raw:
        raise HTTPException(404, f"employee '{key}' not found")
    return (raw.get("behavior") or {}).get("scheduled_tasks", [])


@router.post("/{key}/scheduled-tasks")
async def create_scheduled_task(key: str, task: dict = Body(...)) -> dict:
    raw = await registry.get_raw(key)
    if not raw:
        raise HTTPException(404, f"employee '{key}' not found")
    behavior = dict(raw.get("behavior") or {})
    tasks = list(behavior.get("scheduled_tasks", []))
    task.setdefault("id", str(_uuid.uuid4())[:8])
    task.setdefault("enabled", True)
    tasks.append(task)
    behavior["scheduled_tasks"] = tasks
    await registry.update(key, {"behavior": behavior}, actor="api")
    return task


@router.patch("/{key}/scheduled-tasks/{task_id}")
async def update_scheduled_task(key: str, task_id: str, patch: dict = Body(...)) -> dict:
    raw = await registry.get_raw(key)
    if not raw:
        raise HTTPException(404, f"employee '{key}' not found")
    behavior = dict(raw.get("behavior") or {})
    tasks = list(behavior.get("scheduled_tasks", []))
    idx = next((i for i, t in enumerate(tasks) if t.get("id") == task_id), None)
    if idx is None:
        raise HTTPException(404, f"scheduled task '{task_id}' not found")
    tasks[idx] = {**tasks[idx], **patch}
    behavior["scheduled_tasks"] = tasks
    await registry.update(key, {"behavior": behavior}, actor="api")
    return tasks[idx]


@router.delete("/{key}/scheduled-tasks/{task_id}")
async def delete_scheduled_task(key: str, task_id: str) -> dict:
    raw = await registry.get_raw(key)
    if not raw:
        raise HTTPException(404, f"employee '{key}' not found")
    behavior = dict(raw.get("behavior") or {})
    tasks = [t for t in behavior.get("scheduled_tasks", []) if t.get("id") != task_id]
    behavior["scheduled_tasks"] = tasks
    await registry.update(key, {"behavior": behavior}, actor="api")
    return {"ok": True}


@router.post("/{key}/scheduled-tasks/{task_id}/run")
async def run_scheduled_task_now(key: str, task_id: str) -> dict:
    """立即执行一次 — 通过 agent 端口转发到 scheduler。"""
    import httpx
    raw = await registry.get_raw(key)
    if not raw:
        raise HTTPException(404, f"employee '{key}' not found")
    port = raw.get("agent_port")
    if not port:
        raise HTTPException(400, "agent_port not configured")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"http://localhost:{port}/scheduler/run/{task_id}")
            return resp.json()
    except Exception as e:
        raise HTTPException(502, f"agent unreachable: {e}")


# ── P4.3 跨员工事件路由 ───────────────────────────────────────────────────────

@scheduler_router.post("/events")
async def publish_scheduler_event(event: dict = Body(...)) -> dict:
    """任务完成后发布事件，backend 查找所有订阅该事件的员工任务并转发。

    event 格式：
        {"event_type": "task_completed", "employee": "data_engineer",
         "task_id": "etl_daily", "status": "success"}

    DAG 深度限制：最多 3 跳（通过 event.hop 计数），超出时拒绝，防止循环依赖。
    """
    import httpx

    hop = event.get("_hop", 0)
    if hop >= 3:
        return {"ok": False, "error": "DAG depth limit (3 hops) exceeded"}

    event_type = event.get("event_type", "")
    if not event_type:
        return {"ok": False, "error": "event_type required"}

    # 遍历所有活跃员工，找到订阅了该事件的任务
    all_employees = await employee_repo.list_all(active_only=True)
    forwarded: list[dict] = []

    for emp in all_employees:
        port = emp.agent_port
        if not port:
            continue
        behavior = emp.behavior or {}
        tasks = behavior.get("scheduled_tasks", [])

        for task in tasks:
            if not task.get("enabled"):
                continue
            trigger = task.get("trigger", {})
            if trigger.get("type") != "event":
                continue
            if trigger.get("event_type") != event_type:
                continue
            # 检查 filter 条件
            flt = trigger.get("filter", {})
            if any(event.get(k) != v for k, v in flt.items()):
                continue

            # 转发事件到对应 agent（注入 hop 计数）
            forwarded_event = {**event, "_hop": hop + 1}
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"http://localhost:{port}/scheduler/event",
                        json=forwarded_event,
                    )
                forwarded.append({
                    "employee": emp.key,
                    "task": task.get("name"),
                    "status": resp.status_code,
                })
            except Exception as e:
                forwarded.append({
                    "employee": emp.key,
                    "task": task.get("name"),
                    "error": str(e),
                })

    return {"ok": True, "forwarded": forwarded}


@scheduler_router.get("/event-subscribers")
async def list_event_subscribers() -> list[dict]:
    """查询所有员工中订阅了事件触发的任务。"""
    all_employees = await employee_repo.list_all(active_only=True)
    result = []
    for emp in all_employees:
        behavior = emp.behavior or {}
        for task in behavior.get("scheduled_tasks", []):
            trigger = task.get("trigger", {})
            if trigger.get("type") == "event":
                result.append({
                    "employee": emp.key,
                    "task_id": task.get("id"),
                    "task_name": task.get("name"),
                    "event_type": trigger.get("event_type"),
                    "filter": trigger.get("filter", {}),
                })
    return result
