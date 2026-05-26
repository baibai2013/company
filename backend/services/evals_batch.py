"""提案 3 · §4.4 Loop 4 评测回归 — 批次编排器(Wave 3)。

负责:
  1. start 一个 evals_batch 行
  2. 按 tier 过滤 fixture,顺序调 evals_runner.run_fixture
  3. 汇总 pass_count / pass_rate / avg_iterations 后 finalize batch

Wave 4 改并行(asyncio.gather + 并发上限),并接 baseline diff(参考 §4.4 CI gate)。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.repos import evals_batch_repo, evals_fixture_repo
from backend.services import evals_runner

log = logging.getLogger(__name__)


async def run_batch(
    *,
    git_sha: str,
    tier: int | None = None,
    triggered_by: str = "manual",
    fixture_ids: list[str] | None = None,
) -> dict[str, Any]:
    """跑一批 fixture。

    fixture 选择优先级:
      - fixture_ids 显式传 → 用这些(忽略 tier)
      - 否则 → list_by_tier(tier),tier=None 即全部

    返回:
      {
        "batch_id": str,
        "git_sha": str,
        "triggered_by": str,
        "tier": int | None,
        "fixture_count": int,
        "pass_count": int,
        "pass_rate": float,           # 0.0 ~ 1.0,fixture_count=0 时为 1.0(空 batch 算 pass)
        "avg_iterations": float | None,
        "results": [run_fixture 返回值, ...],
      }

    Wave 3 顺序跑;Wave 4 接 asyncio.gather + sem 控并发。
    """
    # 1) 选 fixture
    if fixture_ids:
        fixtures = []
        for fid in fixture_ids:
            f = await evals_fixture_repo.get(fid)
            if f is None:
                log.warning("run_batch: fixture not found, skipped: %s", fid)
                continue
            fixtures.append(f)
    else:
        fixtures = await evals_fixture_repo.list_by_tier(tier)

    fixture_count = len(fixtures)

    # 2) start batch
    batch = await evals_batch_repo.start(
        git_sha=git_sha,
        triggered_by=triggered_by,
        fixture_count=fixture_count,
    )
    batch_id: uuid.UUID = batch.id

    # 3) 顺序跑
    results: list[dict[str, Any]] = []
    pass_count = 0
    iters_sum = 0
    iters_n = 0
    for f in fixtures:
        try:
            r = await evals_runner.run_fixture(
                f.id, git_sha=git_sha, batch_id=batch_id,
            )
        except Exception as e:
            log.exception("run_batch: run_fixture raised on %s", f.id)
            r = {
                "run_id": None,
                "fixture_id": f.id,
                "verdict": "fail",
                "ground_truth_pass_rate": 0.0,
                "iterations": 1,
                "duration_seconds": 0,
                "delegation_id": None,
                "error": f"{type(e).__name__}: {e}",
            }
        results.append(r)
        if r.get("verdict") == "pass":
            pass_count += 1
        if r.get("iterations") is not None:
            iters_sum += int(r["iterations"])
            iters_n += 1

    # 4) 汇总 + finalize
    pass_rate = (pass_count / fixture_count) if fixture_count > 0 else 1.0
    avg_iters = (iters_sum / iters_n) if iters_n > 0 else None

    await evals_batch_repo.finalize(
        batch_id,
        pass_count=pass_count,
        pass_rate=pass_rate,
        avg_iterations=avg_iters,
        avg_token_usage=None,  # Wave 4 接 cost-aware-llm-pipeline 后再填
    )

    return {
        "batch_id": str(batch_id),
        "git_sha": git_sha,
        "triggered_by": triggered_by,
        "tier": tier,
        "fixture_count": fixture_count,
        "pass_count": pass_count,
        "pass_rate": pass_rate,
        "avg_iterations": avg_iters,
        "results": results,
    }


__all__ = ["run_batch"]
