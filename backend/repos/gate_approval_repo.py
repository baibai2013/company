"""提案 2 · 人审批记录(gate_approvals)仓库。

闸 3 飞书审批的持久化层。Wave 2 只暴露 CRUD;真正的飞书 callback 接入与
24h 超时升级在 Wave 3 接(feishu/cc_bridge/gate_callback.py)。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import and_, select

from backend.core.db import AsyncSessionLocal
from backend.models.proposal2_verify import GateApproval

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# 合法的状态值,防止业务层误传。
_ALLOWED_STATUSES: set[str] = {"pending", "approved", "rejected", "timeout"}


async def create(
    verifier_run_id: uuid.UUID | str,
    deadline_at: datetime,
) -> GateApproval:
    """新建一条 pending 的 gate_approval。

    deadline_at 用于 supervisor 后续扫超时;表上没有这一列,
    默认 24h 超时由 list_pending_overdue 用 created_at + 24h 计算,
    本参数作为 caller 显式契约保留(便于将来扩展可变超时)。
    """
    rid = (
        uuid.UUID(str(verifier_run_id))
        if not isinstance(verifier_run_id, uuid.UUID)
        else verifier_run_id
    )
    # deadline_at 当前不入库,仅为接口预留(避免 caller 漏算 24h)
    _ = deadline_at  # noqa: F841 — 显式表达"参数我看到了"
    async with AsyncSessionLocal() as s:
        row = GateApproval(
            verifier_run_id=rid,
            status="pending",
            created_at=_utcnow(),
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def update_decision(
    gate_id: uuid.UUID | str,
    status: str,
    decided_by: str,
    reason: str | None = None,
) -> GateApproval:
    """飞书 callback 写入决策结果(approved / rejected / timeout)。"""
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"illegal gate status: {status!r}")
    if status == "pending":
        raise ValueError("update_decision 不应把状态改回 pending")

    gid = uuid.UUID(str(gate_id)) if not isinstance(gate_id, uuid.UUID) else gate_id
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(GateApproval).where(GateApproval.id == gid)
        )).scalar_one_or_none()
        if row is None:
            raise ValueError(f"gate_approval not found: {gate_id}")
        row.status = status
        row.decided_by = decided_by
        row.decided_at = _utcnow()
        row.reason = reason
        await s.commit()
        await s.refresh(row)
    return row


async def get(gate_id: uuid.UUID | str) -> GateApproval | None:
    """按 id 取一条记录,找不到返回 None。"""
    gid = uuid.UUID(str(gate_id)) if not isinstance(gate_id, uuid.UUID) else gate_id
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(GateApproval).where(GateApproval.id == gid)
        )).scalar_one_or_none()
    return row


async def list_pending_overdue(
    now: datetime | None = None,
    overdue_after: timedelta = timedelta(hours=24),
) -> list[GateApproval]:
    """supervisor 用:列出"还在 pending 且超过 24h 没决策"的审批。

    用于后续 Wave 3 的 escalation:@CEO + @TechLead,每 12h 重发一次。
    """
    if now is None:
        now = _utcnow()
    cutoff = now - overdue_after
    async with AsyncSessionLocal() as s:
        rows: Sequence[GateApproval] = (await s.execute(
            select(GateApproval)
            .where(
                and_(
                    GateApproval.status == "pending",
                    GateApproval.created_at < cutoff,
                )
            )
            .order_by(GateApproval.created_at.asc())
        )).scalars().all()
    return list(rows)


__all__ = [
    "create",
    "update_decision",
    "get",
    "list_pending_overdue",
]
