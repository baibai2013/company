"""Employee CRUD repository."""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import AsyncSessionLocal
from backend.models.employee import Employee


async def list_all(active_only: bool = False) -> list[Employee]:
    async with AsyncSessionLocal() as s:
        stmt = select(Employee).order_by(Employee.agent_port)
        if active_only:
            stmt = stmt.where(Employee.active.is_(True))
        return list((await s.execute(stmt)).scalars().all())


async def get(key: str) -> Employee | None:
    async with AsyncSessionLocal() as s:
        return await s.get(Employee, key)


async def create(record: dict) -> Employee:
    async with AsyncSessionLocal() as s:
        emp = Employee(**record)
        s.add(emp)
        await s.commit()
        await s.refresh(emp)
        return emp


async def update_fields(key: str, patch: dict[str, Any]) -> Employee | None:
    async with AsyncSessionLocal() as s:
        emp = await s.get(Employee, key)
        if not emp:
            return None
        for field, value in patch.items():
            setattr(emp, field, value)
        emp.version = (emp.version or 1) + 1
        await s.commit()
        await s.refresh(emp)
        return emp


async def deactivate(key: str) -> bool:
    return await update_fields(key, {"active": False}) is not None


async def delete_hard(key: str) -> bool:
    async with AsyncSessionLocal() as s:
        result = await s.execute(delete(Employee).where(Employee.key == key))
        await s.commit()
        return result.rowcount > 0


async def next_available_port(start: int = 9001, end: int = 9100) -> int:
    """Find the lowest unused agent_port in [start, end]."""
    async with AsyncSessionLocal() as s:
        used = {
            row[0] for row in (await s.execute(
                select(Employee.agent_port).where(Employee.agent_port.is_not(None))
            )).all()
        }
    for port in range(start, end + 1):
        if port not in used:
            return port
    raise RuntimeError(f"No free port in [{start}, {end}]")


def to_dict(emp: Employee) -> dict[str, Any]:
    return {
        "key": emp.key,
        "name": emp.name,
        "emoji": emp.emoji,
        "role_desc": emp.role_desc,
        "feishu_app_id": emp.feishu_app_id,
        "feishu_app_secret": emp.feishu_app_secret,
        "agent_port": emp.agent_port,
        "active": emp.active,
        "system_prompt": emp.system_prompt,
        "persona": emp.persona,
        "llm_calls": emp.llm_calls,
        "behavior": emp.behavior,
        "avatar_url": emp.avatar_url,
        "cwd": emp.cwd,
        "version": emp.version,
        "created_at": emp.created_at.isoformat() if emp.created_at else None,
        "updated_at": emp.updated_at.isoformat() if emp.updated_at else None,
    }
