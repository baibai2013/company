"""提案 3 · §4.4 Loop 4 评测回归 — 单个 fixture 执行器(Wave 3 stub 版)。

⚠️ Wave 3 范围:**不**真 spawn 一个 claude-code 跑 fixture,而是:
  1. 读 fixture(input_prompt / acceptance_spec / golden_outputs)
  2. 在 task + delegations 里造一行(满足 verifier_runs.delegation_id FK 与
     orchestrator 的输入约束)
  3. simulate_artifacts() 按 golden_outputs 灌 artifacts,让 orchestrator
     走完三闸 → 拿 verdict
  4. 把结果落 evals_runs

真 spawn agent 走 cc_bridge / agents_v2 是 Wave 4(§4.4 §5 evals_runner.run_eval_batch)。
本 stub 主要目的是把 evals 表 + verifier 链路串起来,让 batch / CI 接入有个能跑的骨架。

参考的 seed 模式见 backend/tests/test_verifier_orchestrator.py 的 _seed_delegation。
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert

from backend.core.db import AsyncSessionLocal
from backend.models.proposal1_state import Delegation
from backend.models.task import Task
from backend.repos import evals_fixture_repo, evals_run_repo
from backend.services import verifier_orchestrator

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────────────
# Stub artifacts 模拟器
# ──────────────────────────────────────────────────────────────────────
def simulate_artifacts(fixture: Any) -> dict:
    """根据 fixture 造一份 fake artifacts,让 verifier 有素材可看。

    规则(stub):
      - 如果 fixture 有 golden_outputs(dict)→ 直接当 artifacts 灌
        (模拟"完美交付",大概率 verifier 走 pass 路径)
      - 否则 → 兜底返回 {"summary": "stub run", "files": []}
        (verifier 默认 pass,但若 acceptance_spec 设置了 force_human / fail_marker /
         严格 ground_truth 检查,可能会 fail/needs_human)

    Wave 4 这里换成"真 spawn agent 跑完拿到的 artifacts dict"。
    """
    golden = getattr(fixture, "golden_outputs", None)
    if isinstance(golden, dict) and golden:
        return dict(golden)
    return {"summary": "stub run", "files": []}


# ──────────────────────────────────────────────────────────────────────
# 内部:为本次 fixture 造一对 task + delegation 行
# ──────────────────────────────────────────────────────────────────────
async def _seed_task_and_delegation(
    fixture: Any,
    artifacts: dict,
) -> str:
    """同步造 task + delegation,id 共享(绕开 Wave 0 schema 的 FK 折衷,
    与 test_verifier_orchestrator.py 同款)。

    返回 shared_id(=task.id=delegation.id),供 orchestrator 调用。

    employee_key 来自 fixture(派活方=eval_harness,接活方=fixture.employee_key),
    parent_task_id 用 shared_id 自指(Wave 3 stub 不真组工作流)。
    """
    shared_id = str(uuid.uuid4())
    now = _utcnow()
    title_prefix = f"[evals:{fixture.id}] "

    async with AsyncSessionLocal() as s:
        # task 行 — verifier_runs.delegation_id FK 当前指向 task.id
        await s.execute(
            insert(Task).values(
                id=shared_id,
                title=title_prefix + (fixture.title or fixture.id),
                priority="P3",
                status="in_progress",
                requester="eval_harness",
                executor=fixture.employee_key,
                created_at=now,
                updated_at=now,
            )
        )
        # delegation 行 — orchestrator 通过 delegation_repo.get 读 acceptance_spec / artifacts
        await s.execute(
            insert(Delegation).values(
                id=shared_id,
                from_employee="eval_harness",
                to_employee=fixture.employee_key,
                parent_task_id=shared_id,
                title=title_prefix + (fixture.title or fixture.id),
                content=fixture.input_prompt or "(no prompt)",
                acceptance_spec=fixture.acceptance_spec or {},
                artifacts=artifacts,
                status="in_progress",
                claimed_at=now,
                started_at=now,
            )
        )
        await s.commit()

    return shared_id


# ──────────────────────────────────────────────────────────────────────
# 主入口:跑单个 fixture
# ──────────────────────────────────────────────────────────────────────
async def run_fixture(
    fixture_id: str,
    *,
    git_sha: str,
    batch_id: uuid.UUID | str | None = None,
) -> dict[str, Any]:
    """跑单个 fixture(Wave 3 stub 路径,详见模块 docstring)。

    返回:
      {
        "run_id": str,
        "fixture_id": str,
        "verdict": "pass" | "fail" | "pending_gate",
        "ground_truth_pass_rate": float | None,
        "iterations": int,
        "duration_seconds": int,
        "delegation_id": str,
      }

    异常:fixture 不存在 → ValueError。

    batch_id 暂未落库(EvalsRun 没 batch_id 列;Wave 4 §4.4 baseline 需要时再扩列),
    但接口先留出来,evals_batch 调用时透传以便日志关联。
    """
    fixture = await evals_fixture_repo.get(fixture_id)
    if fixture is None:
        raise ValueError(f"fixture not found: {fixture_id}")

    t0 = time.monotonic()

    # 1) 模拟 artifacts
    artifacts = simulate_artifacts(fixture)

    # 2) 造 task + delegation
    delegation_id = await _seed_task_and_delegation(fixture, artifacts)

    # 3) 三闸编排(复用提案 2 的 verifier_orchestrator)
    try:
        result = await verifier_orchestrator.on_delegation_done(delegation_id)
    except Exception as e:
        # 防御:orchestrator 抛错也要把 run 落库,verdict='fail'(stub_error)
        log.exception("evals_runner: orchestrator raised on fixture=%s", fixture_id)
        duration = int(time.monotonic() - t0)
        run = await evals_run_repo.record_run(
            fixture_id,
            git_sha=git_sha,
            verdict="fail",
            ground_truth_pass_rate=None,
            iterations=1,
            duration_seconds=duration,
            notes=f"orchestrator raised: {type(e).__name__}: {e}"
            + (f" [batch={batch_id}]" if batch_id else ""),
        )
        return {
            "run_id": str(run.id),
            "fixture_id": fixture_id,
            "verdict": "fail",
            "ground_truth_pass_rate": None,
            "iterations": 1,
            "duration_seconds": duration,
            "delegation_id": delegation_id,
        }

    verdict = result.get("final_verdict", "fail")
    duration = int(time.monotonic() - t0)

    # ground_truth_pass_rate:Wave 3 用粗粒度替代 — pass=1.0 / 其它=0.0
    # Wave 4 接 acceptance_check 行级数据后,改成 sum(ok)/total。
    pass_rate = 1.0 if verdict == "pass" else 0.0

    # 4) 写 evals_runs
    run = await evals_run_repo.record_run(
        fixture_id,
        git_sha=git_sha,
        verdict=verdict,
        ground_truth_pass_rate=pass_rate,
        iterations=1,  # stub 单轮跑;真 spawn agent 后填 attempt 数
        duration_seconds=duration,
        token_usage=None,
        cost_usd=None,
        notes=(
            f"orch_reason={result.get('reason', '')}"
            + (f" gate_id={result.get('gate_id')}" if "gate_id" in result else "")
            + (f" [batch={batch_id}]" if batch_id else "")
        ),
    )

    return {
        "run_id": str(run.id),
        "fixture_id": fixture_id,
        "verdict": verdict,
        "ground_truth_pass_rate": pass_rate,
        "iterations": 1,
        "duration_seconds": duration,
        "delegation_id": delegation_id,
    }


__all__ = ["run_fixture", "simulate_artifacts"]
