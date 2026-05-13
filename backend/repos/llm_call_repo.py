"""LLM call record repository (v2 — observability)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from backend.core.db import AsyncSessionLocal
from backend.models.llm_call import LlmCall


async def insert(employee_key: str, call_type: str, model: str,
                 prompt_tokens: int = 0, completion_tokens: int = 0,
                 latency_ms: int = 0, cost_usd: float = 0.0,
                 success: bool = True, session_id: str | None = None,
                 error_msg: str | None = None) -> None:
    async with AsyncSessionLocal() as s:
        s.add(LlmCall(
            employee_key=employee_key, call_type=call_type, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            latency_ms=latency_ms, cost_usd=cost_usd,
            success=success, session_id=session_id, error_msg=error_msg,
        ))
        await s.commit()


async def stats(employee_key: str | None = None,
                hours: int = 24) -> dict:
    """Aggregate stats over the last N hours."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with AsyncSessionLocal() as s:
        stmt = select(
            func.count().label("count"),
            func.sum(LlmCall.prompt_tokens).label("input_tokens"),
            func.sum(LlmCall.completion_tokens).label("output_tokens"),
            func.sum(LlmCall.cost_usd).label("cost"),
            func.avg(LlmCall.latency_ms).label("avg_latency"),
        ).where(LlmCall.timestamp >= since)
        if employee_key:
            stmt = stmt.where(LlmCall.employee_key == employee_key)
        row = (await s.execute(stmt)).one()
        return {
            "count": row.count or 0,
            "input_tokens": int(row.input_tokens or 0),
            "output_tokens": int(row.output_tokens or 0),
            "cost_usd": float(row.cost or 0),
            "avg_latency_ms": float(row.avg_latency or 0),
            "since": since.isoformat(),
        }


async def list_recent(employee_key: str | None = None, limit: int = 50) -> list[LlmCall]:
    async with AsyncSessionLocal() as s:
        stmt = select(LlmCall).order_by(LlmCall.timestamp.desc()).limit(limit)
        if employee_key:
            stmt = stmt.where(LlmCall.employee_key == employee_key)
        return list((await s.execute(stmt)).scalars().all())
