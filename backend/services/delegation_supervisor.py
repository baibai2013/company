"""派活守护协程 — 提案 1 §4.2 超时分支。

每 interval_s 扫一次 list_overdue:
  - 第一次过期 / 上次 nudge 已超过 escalate 阈值之前 → nudge
  - 已经 nudge 过 + 距 last_nudge_at 超过 _ESCALATE_AFTER_MIN 分钟 → escalate

不在这里发飞书,只调 delegation_service.nudge_delegation /
delegation_repo.transition_to,飞书发送由 service 层 hook 后续接。

main.py 后续会 startup 时 asyncio.create_task(run_supervisor_loop()) 起常驻协程,
本模块**不动 main.py**。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from backend.repos import delegation_repo
from backend.services import delegation_service

log = logging.getLogger(__name__)


# 第一次过期后立即 nudge;已经 nudge 过 30 分钟还没动就 escalate
_ESCALATE_AFTER_MIN = 30
# 同一 delegation 两次 nudge 之间的最小间隔(避免一直 nudge)
_NUDGE_COOLDOWN_MIN = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def supervise_once() -> dict:
    """单次扫描;返回 {'nudged': N, 'escalated': N, 'scanned': M},便于测试。

    决策树:
      1. due_at < now 且未到终态(由 list_overdue 过滤)
      2. last_nudge_at is None  → 第一次 nudge
      3. now - last_nudge_at >= _ESCALATE_AFTER_MIN 分钟  → escalate
      4. else 离上次 nudge 还在冷却期 → 跳过
    """
    nudged = 0
    escalated = 0
    now = _utcnow()
    rows = await delegation_repo.list_overdue(now=now)

    for d in rows:
        last = d.last_nudge_at
        # SQLite fallback 可能给 naive
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)

        if last is None:
            # 第一次 nudge
            try:
                await delegation_service.nudge_delegation(
                    d.id,
                    message=f"超 SLA,due_at={d.due_at}",
                    actor="supervisor",
                )
                nudged += 1
            except Exception as exc:
                log.warning("supervisor: nudge failed id=%s err=%s", d.id, exc)
            continue

        elapsed_min = (now - last).total_seconds() / 60.0
        if elapsed_min >= _ESCALATE_AFTER_MIN:
            # 升级到 escalated;状态机:in_progress/claimed/pending → escalated
            # 但 pending/claimed 没有合法转移到 escalated,我们对它们走兜底:
            # 先确保是 in_progress 再转 escalated 反而绕远;
            # 这里直接用 transition_to,若非法转移就改用 cancelled 兜底告警。
            try:
                # in_progress → escalated 是合法的;
                # pending/claimed 走"先 cancel 再人工接"的语义
                target = "escalated" if d.status == "in_progress" else "escalated"
                # 上面其实都是 "escalated";真正非法时 transition_to 会抛
                await delegation_repo.transition_to(
                    d.id, target,
                    actor="supervisor",
                    event_type="escalated",
                    payload={
                        "reason": "SLA 超时且 nudge 后无响应",
                        "elapsed_min_since_nudge": int(elapsed_min),
                    },
                )
                escalated += 1
            except ValueError as exc:
                # 非法转移:对 pending/claimed 我们不是真升级,而是再 nudge 一次
                # 并 log 警告,等人工介入
                log.warning(
                    "supervisor: cannot escalate id=%s status=%s err=%s; nudging again",
                    d.id, d.status, exc,
                )
                try:
                    await delegation_service.nudge_delegation(
                        d.id,
                        message=f"⚠️ 已超 SLA 且 {int(elapsed_min)} 分钟无回应,人工介入",
                        actor="supervisor",
                    )
                    nudged += 1
                except Exception as exc2:
                    log.warning("supervisor: re-nudge failed id=%s err=%s", d.id, exc2)
        else:
            # 还在冷却期,跳过
            continue

    log.debug(
        "supervisor: scanned=%d nudged=%d escalated=%d",
        len(rows), nudged, escalated,
    )
    return {"scanned": len(rows), "nudged": nudged, "escalated": escalated}


async def run_supervisor_loop(interval_seconds: int = 30) -> None:
    """后台常驻:每 interval_seconds 跑一次 supervise_once。

    main.py 后续应该这样起:
        asyncio.create_task(run_supervisor_loop(30))
    出错只 log,不让协程退出(避免守护死掉没人催办)。
    """
    log.info("delegation_supervisor: loop starting interval=%ds", interval_seconds)
    while True:
        try:
            stat = await supervise_once()
            if stat["nudged"] or stat["escalated"]:
                log.info(
                    "supervisor: scan=%d nudged=%d escalated=%d",
                    stat["scanned"], stat["nudged"], stat["escalated"],
                )
        except Exception as exc:                         # noqa: BLE001
            log.exception("supervisor: scan crashed: %s", exc)
        await asyncio.sleep(interval_seconds)
