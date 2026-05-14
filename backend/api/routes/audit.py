"""Audit log read API."""
from __future__ import annotations

from fastapi import APIRouter, Query

from backend.repos import audit_repo

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
async def list_audit(
    target_type: str | None = Query(None),
    target_key:  str | None = Query(None),
    limit: int = Query(50, le=500),
) -> list[dict]:
    rows = await audit_repo.list_recent(
        target_type=target_type, target_key=target_key, limit=limit,
    )
    return [audit_repo.to_dict(r) for r in rows]
