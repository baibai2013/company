"""LLM call stats (v2 — stub: returns zeros until P11 records start writing)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from backend.repos import llm_call_repo

router = APIRouter(prefix="/api/llm-calls", tags=["llm-calls"])


@router.get("/stats")
async def stats(
    employee: str | None = Query(None),
    hours: int = Query(24, ge=1, le=720),
) -> dict:
    return await llm_call_repo.stats(employee_key=employee, hours=hours)


@router.get("")
async def list_recent(
    employee: str | None = Query(None),
    limit: int = Query(50, le=500),
) -> list[dict]:
    rows = await llm_call_repo.list_recent(employee_key=employee, limit=limit)
    return [{
        "id": r.id,
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        "employee_key": r.employee_key,
        "call_type": r.call_type,
        "model": r.model,
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "latency_ms": r.latency_ms,
        "cost_usd": float(r.cost_usd) if r.cost_usd else 0.0,
        "success": r.success,
        "error_msg": r.error_msg,
    } for r in rows]
