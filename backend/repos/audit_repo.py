"""Audit log repository."""
from __future__ import annotations

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.audit import AuditLog


async def write(actor: str, action: str, target_type: str, target_key: str,
                field_path: str | None = None,
                old_value=None, new_value=None) -> None:
    async with AsyncSessionLocal() as s:
        s.add(AuditLog(
            actor=actor, action=action,
            target_type=target_type, target_key=target_key,
            field_path=field_path,
            old_value=old_value, new_value=new_value,
        ))
        await s.commit()


async def list_recent(target_type: str | None = None, target_key: str | None = None,
                      limit: int = 50) -> list[AuditLog]:
    async with AsyncSessionLocal() as s:
        stmt = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
        if target_type:
            stmt = stmt.where(AuditLog.target_type == target_type)
        if target_key:
            stmt = stmt.where(AuditLog.target_key == target_key)
        return list((await s.execute(stmt)).scalars().all())


def to_dict(log: AuditLog) -> dict:
    return {
        "id": log.id,
        "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        "actor": log.actor,
        "action": log.action,
        "target_type": log.target_type,
        "target_key": log.target_key,
        "field_path": log.field_path,
        "old_value": log.old_value,
        "new_value": log.new_value,
    }
