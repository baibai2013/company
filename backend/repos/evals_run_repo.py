"""提案 3 · 评测执行结果(evals_runs)CRUD 仓库。

每条 evals_runs 对应"某个 fixture × 某个 git_sha"的一次 stub 执行,verdict 与
verifier_runs.final_verdict 同义('pass' | 'fail' | 'pending_gate' | 'needs_human')。

Wave 3 不接真实 token / cost 统计,字段保留以便 Wave 4 接 cost-aware-llm-pipeline 时填。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.proposal3_learning import EvalsRun

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# 允许 update / record_run 写入的列白名单(防 typo)
_UPDATABLE_FIELDS: set[str] = {
    "completed_at",
    "verifier_verdict",
    "ground_truth_pass_rate",
    "iterations",
    "duration_seconds",
    "token_usage",
    "cost_usd",
    "notes",
}


async def create(
    fixture_id: str,
    *,
    git_sha: str,
) -> EvalsRun:
    """新建一条 evals_run(尚未跑完;started_at=now,其余字段留空待 record)。"""
    async with AsyncSessionLocal() as s:
        row = EvalsRun(
            fixture_id=fixture_id,
            git_sha=git_sha,
            started_at=_utcnow(),
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def update(run_id: uuid.UUID | str, **fields: Any) -> EvalsRun:
    """更新 evals_run 的某些字段(只接受白名单内的列名)。"""
    unknown = set(fields.keys()) - _UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"unknown evals_run fields: {sorted(unknown)}")

    rid = uuid.UUID(str(run_id)) if not isinstance(run_id, uuid.UUID) else run_id
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(EvalsRun).where(EvalsRun.id == rid)
        )).scalar_one_or_none()
        if row is None:
            raise ValueError(f"evals_run not found: {run_id}")
        for k, v in fields.items():
            setattr(row, k, v)
        await s.commit()
        await s.refresh(row)
    return row


async def record_run(
    fixture_id: str,
    *,
    git_sha: str,
    verdict: str,
    ground_truth_pass_rate: float | None = None,
    iterations: int | None = None,
    duration_seconds: int | None = None,
    token_usage: int | None = None,
    cost_usd: float | None = None,
    notes: str | None = None,
) -> EvalsRun:
    """create + 立即填终值的便利组合,evals_runner 主路径走它。"""
    row = await create(fixture_id, git_sha=git_sha)
    return await update(
        row.id,
        completed_at=_utcnow(),
        verifier_verdict=verdict,
        ground_truth_pass_rate=ground_truth_pass_rate,
        iterations=iterations,
        duration_seconds=duration_seconds,
        token_usage=token_usage,
        cost_usd=cost_usd,
        notes=notes,
    )


async def get(run_id: uuid.UUID | str) -> EvalsRun | None:
    """按 id 取一条 evals_run。"""
    rid = uuid.UUID(str(run_id)) if not isinstance(run_id, uuid.UUID) else run_id
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(EvalsRun).where(EvalsRun.id == rid)
        )).scalar_one_or_none()
    return row


async def list_for_fixture(fixture_id: str, limit: int = 50) -> list[EvalsRun]:
    """列某 fixture 的全部 run,按 started_at 倒序(最新在前)。"""
    async with AsyncSessionLocal() as s:
        rows: Sequence[EvalsRun] = (await s.execute(
            select(EvalsRun)
            .where(EvalsRun.fixture_id == fixture_id)
            .order_by(EvalsRun.started_at.desc())
            .limit(limit)
        )).scalars().all()
    return list(rows)


async def latest_for_fixture(fixture_id: str) -> EvalsRun | None:
    """取该 fixture 最近一次的 evals_run。"""
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(EvalsRun)
            .where(EvalsRun.fixture_id == fixture_id)
            .order_by(EvalsRun.started_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    return row


async def list_for_git_sha(git_sha: str) -> list[EvalsRun]:
    """列某 git_sha 下的全部 run(给 baseline 对比 / batch 汇总用)。"""
    async with AsyncSessionLocal() as s:
        rows: Sequence[EvalsRun] = (await s.execute(
            select(EvalsRun)
            .where(EvalsRun.git_sha == git_sha)
            .order_by(EvalsRun.started_at.asc())
        )).scalars().all()
    return list(rows)


__all__ = [
    "create",
    "update",
    "record_run",
    "get",
    "list_for_fixture",
    "latest_for_fixture",
    "list_for_git_sha",
]
