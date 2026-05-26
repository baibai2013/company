"""提案 3 · 评测 fixture 库(evals_fixtures)CRUD 仓库。

字段语义见 backend/models/proposal3_learning.py 与提案文档 §3 + §4.4。
fixture id 是手编字符串(如 'mech-leg-v1'),所以 upsert 走 ON CONFLICT (id)。

Wave 3 只暴露最小集:upsert / get / list_by_tier / list_all / delete。
真实 baseline 比对、按 employee 聚合等查询留 Wave 4(L4 月报看板)。
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.core.db import AsyncSessionLocal
from backend.models.proposal3_learning import EvalsFixture

log = logging.getLogger(__name__)


async def upsert(
    fixture_id: str,
    *,
    title: str,
    employee_key: str,
    input_prompt: str,
    acceptance_spec: dict,
    golden_outputs: dict | None = None,
    tier: int = 2,
) -> EvalsFixture:
    """幂等写入 fixture(seed 脚本反复跑也安全)。

    走 postgres ON CONFLICT (id) DO UPDATE,把所有可变列覆盖一遍。
    返回 refreshed ORM 实例。
    """
    values: dict[str, Any] = {
        "id": fixture_id,
        "title": title,
        "employee_key": employee_key,
        "input_prompt": input_prompt,
        "acceptance_spec": acceptance_spec,
        "golden_outputs": golden_outputs,
        "tier": tier,
    }
    stmt = pg_insert(EvalsFixture).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[EvalsFixture.id],
        set_={
            "title": stmt.excluded.title,
            "employee_key": stmt.excluded.employee_key,
            "input_prompt": stmt.excluded.input_prompt,
            "acceptance_spec": stmt.excluded.acceptance_spec,
            "golden_outputs": stmt.excluded.golden_outputs,
            "tier": stmt.excluded.tier,
        },
    )
    async with AsyncSessionLocal() as s:
        await s.execute(stmt)
        await s.commit()

    # 写入后再读一次,拿 refreshed 行
    return await get(fixture_id)  # type: ignore[return-value]


async def get(fixture_id: str) -> EvalsFixture | None:
    """按 id 取一条 fixture,找不到返回 None。"""
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(EvalsFixture).where(EvalsFixture.id == fixture_id)
        )).scalar_one_or_none()
    return row


async def list_by_tier(tier: int | None = None) -> list[EvalsFixture]:
    """按 tier 列 fixture;tier=None 时返回全部。

    排序:先按 tier 升,再按 id 字典序(确定性,batch 跑顺序稳定)。
    """
    async with AsyncSessionLocal() as s:
        stmt = select(EvalsFixture)
        if tier is not None:
            stmt = stmt.where(EvalsFixture.tier == tier)
        stmt = stmt.order_by(EvalsFixture.tier.asc(), EvalsFixture.id.asc())
        rows: Sequence[EvalsFixture] = (await s.execute(stmt)).scalars().all()
    return list(rows)


async def list_all() -> list[EvalsFixture]:
    """列出全部 fixture(便利方法,等同 list_by_tier(None))。"""
    return await list_by_tier(None)


async def delete(fixture_id: str) -> bool:
    """删一条 fixture(主要给测试清扫用),返回是否真的删到。"""
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(EvalsFixture).where(EvalsFixture.id == fixture_id)
        )).scalar_one_or_none()
        if row is None:
            return False
        await s.delete(row)
        await s.commit()
    return True


__all__ = ["upsert", "get", "list_by_tier", "list_all", "delete"]
