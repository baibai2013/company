"""
Employee CRUD + process control + LLM stats.

All employee config (persona / models / prompts / credentials) lives in the
employee table. This router exposes it via REST + maps process commands
through ProcessManager.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from backend.repos import audit_repo, employee_repo, llm_call_repo
from backend.services import process_manager, registry

router = APIRouter(prefix="/api/employees", tags=["employees"])


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
