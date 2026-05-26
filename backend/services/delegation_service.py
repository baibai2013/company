"""派活状态机服务层 — 提案 1 §4.2 / §5.2。

包装 delegation_repo 的状态转移,加一层"副作用 hook"。本 wave 副作用只做:
  - 落 DelegationEvent(已经在 repo 层做了原子写)
  - log.info 提示后续主进程该做什么(飞书通知 / claude_pool spawn 提示)

后续 Wave / 主进程会把这里的 log 替换成真正的飞书 send_delegation_card
和 prompt 队列推送。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from backend.models.proposal1_state import Delegation
from backend.repos import delegation_repo

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── 创建 ───────────────────────────────────────────────────────────
async def create_delegation(
    *,
    from_employee: str,
    to_employee: str,
    parent_task_id: str,
    title: str,
    content: str,
    acceptance_spec: dict | None = None,
    due_in_minutes: int = 60,
) -> Delegation:
    """派活:写表 + 计算 due_at + 触发"通知接活方"hook(目前只 log)。

    due_in_minutes 给的是从现在起多少分钟后到期,默认 60。给 0 表示无 SLA。
    """
    due_at: datetime | None = None
    if due_in_minutes and due_in_minutes > 0:
        due_at = _utcnow() + timedelta(minutes=due_in_minutes)

    row = await delegation_repo.create(
        from_employee=from_employee,
        to_employee=to_employee,
        parent_task_id=parent_task_id,
        title=title,
        content=content,
        acceptance_spec=acceptance_spec,
        due_at=due_at,
    )
    # TODO(主进程集成): 这里后续要触发
    #   1. feishu.sender.send_delegation_card(to_employee, row)
    #   2. claude_pool 的 prompt 提示队列(让接活方下次启动看到 📥)
    log.info(
        "delegation_service: created id=%s from=%s to=%s due=%s",
        row.id, from_employee, to_employee,
        due_at.isoformat() if due_at else "none",
    )
    return row


# ── 认领 ───────────────────────────────────────────────────────────
async def claim_delegation(
    delegation_id: str,
    claimer_employee: str,
) -> Delegation:
    """接活方认领。校验 claimer == to_employee。

    pending → claimed
    """
    cur = await delegation_repo.get(delegation_id)
    if cur is None:
        raise ValueError(f"delegation not found: {delegation_id}")
    if cur.to_employee != claimer_employee:
        raise PermissionError(
            f"claimer {claimer_employee!r} != to_employee {cur.to_employee!r}"
        )

    row = await delegation_repo.transition_to(
        delegation_id, "claimed",
        actor=claimer_employee,
        event_type="claimed",
        payload={"claimer": claimer_employee},
    )
    # TODO(主进程集成): 通知派活方"已认领"
    log.info(
        "delegation_service: claimed id=%s by=%s from=%s",
        row.id, claimer_employee, row.from_employee,
    )
    return row


# ── 进度更新 ────────────────────────────────────────────────────────
async def update_progress(
    delegation_id: str,
    progress_note: str,
    percent: int | None = None,
) -> Delegation:
    """接活方刷新进度。claimed/in_progress → in_progress(可重复调)。"""
    cur = await delegation_repo.get(delegation_id)
    if cur is None:
        raise ValueError(f"delegation not found: {delegation_id}")

    # 已经在 in_progress 时,不切状态,只追加 progress_update 事件
    if cur.status == "in_progress":
        target = "in_progress"
    elif cur.status == "claimed":
        target = "in_progress"
    else:
        raise ValueError(
            f"update_progress requires status in (claimed, in_progress), got {cur.status!r}"
        )

    payload = {"note": progress_note}
    if percent is not None:
        payload["percent"] = percent

    row = await delegation_repo.transition_to(
        delegation_id, target,
        actor=cur.to_employee,
        event_type="progress_update",
        payload=payload,
    )
    log.info(
        "delegation_service: progress id=%s percent=%s note=%s",
        row.id, percent, progress_note[:60],
    )
    return row


# ── 完成 ───────────────────────────────────────────────────────────
async def complete_delegation(
    delegation_id: str,
    artifacts: dict,
) -> Delegation:
    """接活方完成。in_progress → done。

    artifacts 形如 {"files": [...], "links": [...], "summary": "..."}。
    本 wave 直接转 done;后续 Wave 提案 2 接 verifier 时,这里会改成
    "先转 awaiting_verify,verifier 通过再转 done"。
    """
    cur = await delegation_repo.get(delegation_id)
    if cur is None:
        raise ValueError(f"delegation not found: {delegation_id}")

    row = await delegation_repo.transition_to(
        delegation_id, "done",
        actor=cur.to_employee,
        event_type="done",
        payload={"summary": artifacts.get("summary") if isinstance(artifacts, dict) else None},
        artifacts=artifacts,
    )
    # TODO(主进程集成): 通知派活方"活回来了" + 触发 verifier(提案 2)
    log.info(
        "delegation_service: done id=%s by=%s artifacts_keys=%s",
        row.id, cur.to_employee, list(artifacts.keys()) if isinstance(artifacts, dict) else "?",
    )
    return row


# ── 撤回 ───────────────────────────────────────────────────────────
async def cancel_delegation(
    delegation_id: str,
    canceller_employee: str,
    reason: str,
) -> Delegation:
    """派活方或 system 撤回。任意非终态 → cancelled。"""
    cur = await delegation_repo.get(delegation_id)
    if cur is None:
        raise ValueError(f"delegation not found: {delegation_id}")

    row = await delegation_repo.transition_to(
        delegation_id, "cancelled",
        actor=canceller_employee,
        event_type="cancelled",
        payload={"reason": reason, "canceller": canceller_employee},
    )
    log.info(
        "delegation_service: cancelled id=%s by=%s reason=%s",
        row.id, canceller_employee, reason[:80],
    )
    return row


# ── 催办 ───────────────────────────────────────────────────────────
async def nudge_delegation(
    delegation_id: str,
    message: str,
    actor: str = "system",
) -> Delegation:
    """派活方主动催办或 supervisor 自动催办。

    不切状态,只 last_nudge_at = now + nudge_count++ + 写 nudged 事件。
    """
    cur = await delegation_repo.get(delegation_id)
    if cur is None:
        raise ValueError(f"delegation not found: {delegation_id}")

    row = await delegation_repo.transition_to(
        delegation_id, cur.status,
        actor=actor,
        event_type="nudged",
        payload={"message": message},
        nudge=True,
    )
    # TODO(主进程集成): 飞书 @接活方 + 派活方
    log.info(
        "delegation_service: nudged id=%s by=%s count=%d",
        row.id, actor, row.nudge_count,
    )
    return row
