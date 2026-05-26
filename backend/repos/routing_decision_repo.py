"""提案 4 §3 — supervisor 路由决策审计表 routing_decisions 的 CRUD 仓库。

只做表层 CRUD(写一条 / 列某 task 的全部决策),业务编排由
agents_v2.tech_lead.supervisor 调用。

字段语义见 backend/models/proposal4_crosscut.py:RoutingDecision。
表/索引由 Wave 0-D alembic(wave0_d_proposal4_crosscut)创建,本仓库
不重复声明。
"""
from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.proposal4_crosscut import RoutingDecision

log = logging.getLogger(__name__)


async def record(
    *,
    task_id: str,
    step_idx: int,
    candidates: list[str] | None,
    chosen: str,
    reason: str | None = None,
) -> RoutingDecision:
    """写入一条路由决策记录,返回 refreshed ORM 实例。

    Args:
        task_id:    UUID 字符串(routing_decisions.task_id 是 UUID 列)
        step_idx:   该任务内第几步路由(0 起步;同 task 多次路由可累加)
        candidates: 候选员工列表(可空)
        chosen:     选中的员工 key
        reason:     决策理由(自然语言)

    Returns:
        RoutingDecision(已 commit + refresh)
    """
    async with AsyncSessionLocal() as s:
        row = RoutingDecision(
            task_id=task_id,
            step_idx=step_idx,
            candidate_employees=candidates,
            chosen_employee=chosen,
            reason=reason,
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def list_for_task(task_id: str) -> list[RoutingDecision]:
    """列出某 task 的全部路由决策,按 step_idx 升序、routed_at 升序。

    用于审计"某任务为什么先派给 A 后又派给 B"。
    """
    async with AsyncSessionLocal() as s:
        rows: Sequence[RoutingDecision] = (await s.execute(
            select(RoutingDecision)
            .where(RoutingDecision.task_id == task_id)
            .order_by(
                RoutingDecision.step_idx.asc(),
                RoutingDecision.routed_at.asc(),
            )
        )).scalars().all()
    return list(rows)


__all__ = ["record", "list_for_task"]
