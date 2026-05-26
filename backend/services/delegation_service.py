"""派活状态机服务层 — 提案 1 §4.2 / §5.2 + 提案 2 §6.2(Wave 2 集成 verifier)。

包装 delegation_repo 的状态转移,加一层"副作用 hook"。本 wave 副作用:
  - 落 DelegationEvent(repo 层做了原子写)
  - complete_delegation 完成后 fire-and-forget 触发 verifier_orchestrator
  - log.info 提示后续主进程该做什么(飞书通知 / claude_pool spawn 提示)

⚠️ verifier 触发是 fire-and-forget(asyncio.create_task),失败只 log,不
阻塞 done 主路径。原因:本 wave verifier 仍是规则 stub,真业务 LLM 接入
在 Wave 4+;状态机暂未引入 'verifying' 中间态(见 verifier_orchestrator
docstring 的 TODO),所以这里仍是 in_progress→done 转移,verifier 只
落 verifier_run + 可能 gate_approval 记录,不再回退 delegation 状态。
"""
from __future__ import annotations

import asyncio
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
    log.info(
        "delegation_service: done id=%s by=%s artifacts_keys=%s",
        row.id, cur.to_employee, list(artifacts.keys()) if isinstance(artifacts, dict) else "?",
    )

    # 提案 2 三闸 — fire-and-forget,失败只 log,不阻塞 done 主路径
    asyncio.create_task(_trigger_verifier_safe(delegation_id))
    return row


async def _trigger_verifier_safe(delegation_id: str) -> None:
    """fire-and-forget 触发 verifier_orchestrator,任何异常吞掉只 log。

    单独抽函数是为了:
      1. 让 asyncio.create_task 拿到一个有 name 的 Task
      2. 集中 try/except,避免污染 complete_delegation 主路径
    """
    try:
        from backend.services import verifier_orchestrator
        result = await verifier_orchestrator.on_delegation_done(delegation_id)
        log.info(
            "verifier triggered for delegation=%s → %s",
            delegation_id, result.get("final_verdict"),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "verifier 触发失败,delegation=%s 已 done 但未验证: %s",
            delegation_id, exc,
        )


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

    # Wave 3:cancelled 路径也触发 retro(verifier_orchestrator 不会经过 cancelled)
    asyncio.create_task(_trigger_retro_for_cancelled_safe(delegation_id))
    return row


async def _trigger_retro_for_cancelled_safe(delegation_id: str) -> None:
    """cancelled 路径专用 retro 钩子,fire-and-forget,失败 swallow。

    pass/fail 路径走 verifier_orchestrator._finalize → retro;cancelled
    不会进 verifier,所以单独在这里挂一次。
    """
    try:
        from backend.services import retro_agent
        new_lessons = await retro_agent.run_retro_for_delegation(delegation_id)
        log.info(
            "retro(cancelled) triggered for delegation=%s → 抽出 %d 条 lesson",
            delegation_id, len(new_lessons),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "retro(cancelled) 触发失败,delegation=%s: %s",
            delegation_id, exc,
        )


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
