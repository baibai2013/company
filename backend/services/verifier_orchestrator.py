"""提案 2 · 三闸编排器(verifier_orchestrator)+ 提案 3 retro 钩子(Wave 3 集成)。

复刻提案文档 §5.1 的伪码:闸 1 LLM → 闸 2 ground truth → 闸 3 human gate。
Wave 3 在 _finalize 终态后 fire-and-forget 触发 retro_agent.run_retro_for_delegation,
失败一律 swallow + log,不阻塞主路径。

⚠️ TODO(主进程后续集成):
1. 提案 2 §6.2 要求 complete_delegation 加 "verifying" 中间态。Wave 1 状态机
   还没引入这个状态(合法转移仅 in_progress→done/escalated/cancelled)。
   本 Wave 折衷:final_verdict='pass' 时,如果 delegation 已经在 done 就跳过
   transition_to;final_verdict='fail' 时不做状态回退(只 log + 让派活方人工处理)。
   等主进程把 'verifying' 加进 _ALLOWED_TRANSITIONS 后,把这里的 TODO 替换为:
     - pass:  verifying → done
     - fail:  verifying → in_progress  + rejection_reason 写 DelegationEvent
2. 飞书 gate callback 在 Wave 4+ 接(feishu/cc_bridge/gate_callback.py),
   handle_gate_decision() 已经预留接口供 callback 调用。
3. delegation_service.complete_delegation 当前直接 in_progress → done,
   Wave 2 已经 fire-and-forget 调用本模块的 on_delegation_done(已就位)。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.repos import (
    acceptance_check_repo,
    delegation_repo,
    gate_approval_repo,
    verifier_run_repo,
)
from backend.services import llm_verifier
from backend.services.checkers import runner as checker_runner

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Gate 默认 24h SLA,与提案 §4.3 一致。
_GATE_SLA = timedelta(hours=24)


# ── 内部辅助:终结一次 verifier_run ──────────────────────────────────
async def _finalize(
    *,
    run_id: uuid.UUID,
    delegation_id: str,
    final_verdict: str,
    reason: str = "",
) -> None:
    """把 verifier_run 标终态,并尝试推动 delegation 状态。

    pass:
        - 写 final_verdict='pass' + completed_at=now
        - delegation 当前若是 'in_progress',转 'done';若已是 'done' 则跳过
          (主进程接 'verifying' 中间态前的折衷)
    fail / needs_human(走 gate):
        - 写 final_verdict 并保留 delegation 状态不动
        - 派活方/接活方的反馈通知留给主进程飞书闭环(Wave 3)
    """
    await verifier_run_repo.update(
        run_id,
        final_verdict=final_verdict,
        completed_at=_utcnow(),
    )

    # Wave 3:终态(无论 pass / fail)都触发 retro,fire-and-forget,失败 swallow
    asyncio.create_task(_trigger_retro_safe(delegation_id))

    if final_verdict != "pass":
        log.info(
            "verifier_orchestrator: run=%s final=%s reason=%s "
            "(delegation 状态保留,等主进程飞书反馈链)",
            run_id, final_verdict, reason,
        )
        return

    # pass 路径:把 delegation 推到 done(若还没在 done)
    cur = await delegation_repo.get(delegation_id)
    if cur is None:
        log.warning("verifier pass 但 delegation 找不到: %s", delegation_id)
        return
    if cur.status == "done":
        log.info("verifier pass: delegation=%s already 'done',跳过 transition", delegation_id)
        return
    if cur.status in ("cancelled", "escalated"):
        log.warning(
            "verifier pass 但 delegation=%s 已是 %s,不再转 done",
            delegation_id, cur.status,
        )
        return
    try:
        await delegation_repo.transition_to(
            delegation_id,
            "done",
            actor="verifier",
            event_type="verifier_pass",
            payload={"verifier_run_id": str(run_id), "reason": reason},
        )
    except ValueError as e:
        # 非法转移(比如当前不在 in_progress / claimed)→ 只 log
        log.warning(
            "verifier pass 但状态机拒绝 transition: delegation=%s err=%s",
            delegation_id, e,
        )


# ── 主入口:三闸串起来 ──────────────────────────────────────────────
async def on_delegation_done(delegation_id: str) -> dict[str, Any]:
    """复刻提案 02 §5.1 的三闸编排。

    步骤:
      1. 创建 verifier_run(attempt = latest+1)
      2. 闸 1:run_llm_verifier
         - fail        → final_verdict='fail',直接终止
         - needs_human → 跳到闸 3(创建 gate_approval pending)
      3. 闸 2:按 acceptance_spec.deliverable_type 跑 ground truth checkers
         - 任一 fail → final_verdict='fail'
      4. 闸 3:若 acceptance_spec.gate=True → 创建 gate_approval pending
                否则 → final_verdict='pass'
      5. final_verdict='pass' → delegation 转 'done'
         其它 → 状态保留,等主进程的飞书反馈链(Wave 3)推进

    返回 {"run_id": ..., "final_verdict": ..., "reason": ...}。
    final_verdict 取值:'pass' | 'fail' | 'pending_gate'。
    """
    # 1. 计算 attempt
    latest = await verifier_run_repo.latest_for_delegation(delegation_id)
    attempt = (latest.attempt + 1) if latest else 1

    run = await verifier_run_repo.create(delegation_id, attempt=attempt)
    run_id = run.id

    # 取 acceptance_spec / artifacts
    delegation = await delegation_repo.get(delegation_id)
    acceptance_spec = (delegation.acceptance_spec or {}) if delegation else {}
    artifacts = (delegation.artifacts or {}) if delegation else {}

    deliverable_type = acceptance_spec.get("deliverable_type", "generic_artifacts")
    gate_required = bool(acceptance_spec.get("gate"))

    # 把 gate_required 落到 verifier_run(便于查询)
    await verifier_run_repo.update(run_id, gate_required=gate_required)

    # 2. 闸 1 LLM verifier
    v1 = await llm_verifier.run_llm_verifier(
        run_id,
        delegation_id=delegation_id,
        artifacts=artifacts,
        acceptance_spec=acceptance_spec,
    )
    v1_verdict = v1["verdict"]
    v1_reason = "; ".join(v1.get("reasons") or [])

    if v1_verdict == "fail":
        await _finalize(
            run_id=run_id,
            delegation_id=delegation_id,
            final_verdict="fail",
            reason=f"llm_verifier fail: {v1_reason}",
        )
        return {"run_id": str(run_id), "final_verdict": "fail", "reason": v1_reason}

    if v1_verdict == "needs_human":
        # 直接跳闸 3,标 gate_required=True 并创建 pending gate
        await verifier_run_repo.update(run_id, gate_required=True)
        gate = await gate_approval_repo.create(
            run_id, deadline_at=_utcnow() + _GATE_SLA,
        )
        await verifier_run_repo.update(
            run_id,
            gate_status="pending",
            final_verdict=None,  # 等 gate 决策后由 handle_gate_decision 终结
            completed_at=None,
        )
        log.info(
            "verifier_orchestrator: run=%s 闸1 needs_human → gate=%s pending",
            run_id, gate.id,
        )
        return {
            "run_id": str(run_id),
            "final_verdict": "pending_gate",
            "reason": v1_reason,
            "gate_id": str(gate.id),
        }

    # 3. 闸 2 ground truth checkers
    check_results = await checker_runner.run_checks(
        deliverable_type, artifacts, acceptance_spec,
    )
    # 落明细 + 汇总到 ground_truth_logs
    logs_summary: dict[str, dict[str, Any]] = {}
    all_ok = True
    for name, result in check_results:
        await acceptance_check_repo.record(
            run_id,
            check_name=name,
            ok=result.ok,
            duration_ms=result.duration_ms,
            err_msg=result.err_msg,
            output_log=result.output_log,
        )
        logs_summary[name] = {
            "ok": result.ok,
            "err_msg": result.err_msg,
            "duration_ms": result.duration_ms,
        }
        if not result.ok:
            all_ok = False

    gt_status = (
        "skipped" if not check_results
        else ("ok" if all_ok else "err")
    )
    await verifier_run_repo.update(
        run_id,
        ground_truth_status=gt_status,
        ground_truth_logs=logs_summary,
    )

    if not all_ok:
        fail_names = [n for n, r in check_results if not r.ok]
        reason = f"ground_truth checks failed: {fail_names}"
        await _finalize(
            run_id=run_id,
            delegation_id=delegation_id,
            final_verdict="fail",
            reason=reason,
        )
        return {"run_id": str(run_id), "final_verdict": "fail", "reason": reason}

    # 4. 闸 3 human gate(可选)
    if gate_required:
        gate = await gate_approval_repo.create(
            run_id, deadline_at=_utcnow() + _GATE_SLA,
        )
        await verifier_run_repo.update(
            run_id,
            gate_status="pending",
        )
        log.info(
            "verifier_orchestrator: run=%s 闸2 ok,闸3 gate=%s pending",
            run_id, gate.id,
        )
        return {
            "run_id": str(run_id),
            "final_verdict": "pending_gate",
            "reason": "awaiting human approval",
            "gate_id": str(gate.id),
        }

    # 5. 三闸全过 → pass
    await _finalize(
        run_id=run_id,
        delegation_id=delegation_id,
        final_verdict="pass",
        reason="all gates passed",
    )
    return {
        "run_id": str(run_id),
        "final_verdict": "pass",
        "reason": "all gates passed",
    }


# ── 飞书 gate callback 入口 ────────────────────────────────────────
async def handle_gate_decision(
    gate_id: uuid.UUID | str,
    decision: str,
    decided_by: str,
    reason: str = "",
) -> None:
    """飞书审批 callback 调:approved → delegation done;rejected → 留 in_progress。

    decision 取 'approved' | 'rejected' | 'timeout'。

    TODO(Wave 3 飞书闭环):本函数已就位,等 feishu/cc_bridge/gate_callback.py
    挂上 webhook 直接调即可。timeout 路径由 supervisor 协程扫
    gate_approval_repo.list_pending_overdue 后调用本函数(decision='timeout')。
    """
    if decision not in ("approved", "rejected", "timeout"):
        raise ValueError(f"illegal gate decision: {decision!r}")

    gate = await gate_approval_repo.update_decision(
        gate_id, status=decision, decided_by=decided_by, reason=reason or None,
    )
    run = await verifier_run_repo.get(gate.verifier_run_id)
    if run is None:
        log.warning("gate=%s 找不到 verifier_run %s", gate_id, gate.verifier_run_id)
        return

    # 同步 verifier_run.gate_* 字段
    await verifier_run_repo.update(
        run.id,
        gate_status=decision,
        gate_decided_by=decided_by,
        gate_decided_at=_utcnow(),
        gate_reason=reason or None,
    )

    if decision == "approved":
        await _finalize(
            run_id=run.id,
            delegation_id=run.delegation_id,
            final_verdict="pass",
            reason=f"gate approved by {decided_by}",
        )
    else:
        # rejected / timeout → 标 fail,delegation 状态保留(主进程接飞书反馈)
        await _finalize(
            run_id=run.id,
            delegation_id=run.delegation_id,
            final_verdict="fail",
            reason=f"gate {decision} by {decided_by}: {reason}",
        )


async def _trigger_retro_safe(delegation_id: str) -> None:
    """fire-and-forget 触发 retro_agent,任何异常 swallow + log。

    与 delegation_service._trigger_verifier_safe 同样的模式 — 集中 try/except
    避免污染 _finalize 主路径,asyncio.create_task 拿到一个有 name 的 Task。
    """
    try:
        from backend.services import retro_agent
        new_lessons = await retro_agent.run_retro_for_delegation(delegation_id)
        log.info(
            "retro triggered for delegation=%s → 抽出 %d 条 lesson",
            delegation_id, len(new_lessons),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "retro 触发失败,delegation=%s 已终态但未抽 lesson: %s",
            delegation_id, exc,
        )


__all__ = ["on_delegation_done", "handle_gate_decision"]
