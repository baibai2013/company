"""派活状态机仓库 — 提案 1 §3.1 / §4.2。

只做"原子状态转移 + 事件流落表",副作用(飞书通知 / prompt 注入提示)放在
delegation_service 层做。

合法状态转移:
    pending      → claimed | cancelled
    claimed      → in_progress | cancelled
    in_progress  → done | escalated | cancelled
    done         → (终态)
    escalated    → in_progress | cancelled
    cancelled    → (终态)

非法转移 → raise ValueError("illegal transition: ...")
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import and_, not_, or_, select

from backend.core.db import AsyncSessionLocal
from backend.models.proposal1_state import Delegation, DelegationEvent

log = logging.getLogger(__name__)


# ── 状态转移合法性表 ────────────────────────────────────────────────
# 用 dict[当前状态] = {允许的下一状态}
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending":     {"claimed", "cancelled"},
    "claimed":     {"in_progress", "cancelled"},
    "in_progress": {"done", "escalated", "cancelled"},
    "done":        set(),                          # 终态
    "escalated":   {"in_progress", "cancelled"},
    "cancelled":   set(),                          # 终态
}

# 终态集合(列表查询排除用)
TERMINAL_STATUSES: tuple[str, ...] = ("done", "escalated", "cancelled")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── 写入 / 读取 ────────────────────────────────────────────────────
async def create(
    *,
    from_employee: str,
    to_employee: str,
    parent_task_id: str,
    title: str,
    content: str,
    acceptance_spec: dict | None = None,
    due_at: datetime | None = None,
) -> Delegation:
    """创建一条 pending 状态的派活记录,同步写一条 created 事件。

    返回 refreshed Delegation 实例(含 id / status / created_at)。
    """
    async with AsyncSessionLocal() as s:
        row = Delegation(
            from_employee=from_employee,
            to_employee=to_employee,
            parent_task_id=parent_task_id,
            title=title,
            content=content,
            acceptance_spec=acceptance_spec,
            due_at=due_at,
            status="pending",
        )
        s.add(row)
        await s.flush()           # 拿到 row.id
        s.add(DelegationEvent(
            delegation_id=row.id,
            event_type="created",
            actor=from_employee,
            payload={
                "title": title,
                "to_employee": to_employee,
                "due_at": due_at.isoformat() if due_at else None,
            },
        ))
        await s.commit()
        await s.refresh(row)
    return row


async def get(delegation_id: str) -> Delegation | None:
    """按 id 取一条派活记录,找不到返回 None。"""
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(Delegation).where(Delegation.id == delegation_id)
        )).scalar_one_or_none()
    return row


async def list_in_flight_for_employee(
    employee_key: str,
    direction: str,
) -> list[Delegation]:
    """列出某员工"在飞中"的委派(过滤掉 done/escalated/cancelled)。

    direction:
      - 'out' : from_employee == employee_key,即"我派给别人未回的"
      - 'in'  : to_employee   == employee_key,即"别人派给我未做完的"
    """
    if direction not in ("out", "in"):
        raise ValueError(f"direction must be 'out' or 'in', got {direction!r}")

    async with AsyncSessionLocal() as s:
        if direction == "out":
            cond = Delegation.from_employee == employee_key
        else:
            cond = Delegation.to_employee == employee_key

        rows: Sequence[Delegation] = (await s.execute(
            select(Delegation)
            .where(
                and_(
                    cond,
                    not_(Delegation.status.in_(TERMINAL_STATUSES)),
                )
            )
            .order_by(Delegation.created_at.asc())
        )).scalars().all()
    return list(rows)


async def list_pending_claim_for_employee(employee_key: str) -> list[Delegation]:
    """接活方视角:别人派给我但我还没认领的(status='pending')。"""
    async with AsyncSessionLocal() as s:
        rows: Sequence[Delegation] = (await s.execute(
            select(Delegation)
            .where(
                Delegation.to_employee == employee_key,
                Delegation.status == "pending",
            )
            .order_by(Delegation.created_at.asc())
        )).scalars().all()
    return list(rows)


async def list_overdue(now: datetime | None = None) -> list[Delegation]:
    """守护协程用:列出所有 due_at < now 且未到终态的委派。

    走部分索引 ix_delegations_inflight_due。
    """
    if now is None:
        now = _utcnow()
    async with AsyncSessionLocal() as s:
        rows: Sequence[Delegation] = (await s.execute(
            select(Delegation)
            .where(
                Delegation.due_at.isnot(None),
                Delegation.due_at < now,
                not_(Delegation.status.in_(TERMINAL_STATUSES)),
            )
            .order_by(Delegation.due_at.asc())
        )).scalars().all()
    return list(rows)


# ── 状态机原子转移 ──────────────────────────────────────────────────
async def transition_to(
    delegation_id: str,
    new_status: str,
    *,
    actor: str = "system",
    event_type: str | None = None,
    payload: dict | None = None,
    artifacts: dict | None = None,
    nudge: bool = False,
) -> Delegation:
    """把 delegation 从当前 status 切到 new_status,同时写一条事件。

    校验:
      - 必须是合法转移(见 _ALLOWED_TRANSITIONS),否则 ValueError
      - delegation 必须存在,否则 ValueError

    副作用:
      - 按 new_status 自动填对应时间戳列(claimed_at/started_at/done_at/escalated_at)
      - 写一条 DelegationEvent
      - nudge=True 时(状态可不变,只用于催办场景)更新 last_nudge_at + nudge_count++
        注意:nudge 通常配合 new_status == 当前 status 使用,因此对 nudge 模式
        我们**跳过**合法转移检查。

    其它 kwargs(actor / event_type / payload / artifacts)用于事件流细节。
    """
    if not nudge and new_status not in _flatten_all_statuses():
        raise ValueError(f"unknown status: {new_status!r}")

    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(Delegation).where(Delegation.id == delegation_id)
        )).scalar_one_or_none()
        if row is None:
            raise ValueError(f"delegation not found: {delegation_id}")

        old_status = row.status
        if not nudge:
            allowed = _ALLOWED_TRANSITIONS.get(old_status, set())
            if new_status not in allowed and new_status != old_status:
                raise ValueError(
                    f"illegal transition: {old_status} -> {new_status} "
                    f"(allowed: {sorted(allowed) or '∅(终态)'})"
                )

        # 状态切换
        now = _utcnow()
        if not nudge and new_status != old_status:
            row.status = new_status
            if new_status == "claimed":
                row.claimed_at = now
            elif new_status == "in_progress" and row.started_at is None:
                row.started_at = now
            elif new_status == "done":
                row.done_at = now
            elif new_status == "escalated":
                row.escalated_at = now

        # artifacts 落库(complete 时用)
        if artifacts is not None:
            row.artifacts = artifacts

        # 催办计数
        if nudge:
            row.last_nudge_at = now
            row.nudge_count = (row.nudge_count or 0) + 1

        # 事件流
        ev_type = event_type or _default_event_type(old_status, new_status, nudge)
        s.add(DelegationEvent(
            delegation_id=row.id,
            event_type=ev_type,
            actor=actor,
            payload=payload,
        ))

        await s.commit()
        await s.refresh(row)
    return row


# ── 私有 ───────────────────────────────────────────────────────────
def _flatten_all_statuses() -> set[str]:
    """所有合法状态名(用于参数校验)。"""
    out: set[str] = set(_ALLOWED_TRANSITIONS.keys())
    for v in _ALLOWED_TRANSITIONS.values():
        out |= v
    return out


def _default_event_type(old: str, new: str, nudge: bool) -> str:
    """根据状态变化推导默认 event_type,caller 也可以显式传。"""
    if nudge:
        return "nudged"
    if old == new:
        return "progress_update"
    return {
        "claimed":     "claimed",
        "in_progress": "progress_update",
        "done":        "done",
        "escalated":   "escalated",
        "cancelled":   "cancelled",
    }.get(new, "progress_update")
