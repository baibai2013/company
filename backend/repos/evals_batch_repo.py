"""提案 3 · 评测批次(evals_batches)CRUD 仓库。

一次 batch 对应"某个 git_sha + 某个触发源"下跑的一组 fixture,行级聚合在 evals_runs。
本仓库只做表层 start / finalize / get,聚合计算在 evals_batch service 层完成后回写。

Wave 4 接 CI gate 时,会用 batch 的 pass_rate 与上次 baseline 对比。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.proposal3_learning import EvalsBatch

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def start(
    *,
    git_sha: str,
    triggered_by: str,
    fixture_count: int,
) -> EvalsBatch:
    """开一个新 batch,started_at=now,其余字段留空待 finalize。

    triggered_by 取值惯例:'cron' | 'manual' | 'ci-pr-<n>'(提案 §3 注释)。
    """
    async with AsyncSessionLocal() as s:
        row = EvalsBatch(
            git_sha=git_sha,
            triggered_by=triggered_by,
            fixture_count=fixture_count,
            started_at=_utcnow(),
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def finalize(
    batch_id: uuid.UUID | str,
    *,
    pass_count: int,
    pass_rate: float,
    avg_iterations: float | None = None,
    avg_token_usage: float | None = None,
) -> EvalsBatch:
    """补 pass_count / pass_rate / avg_* + completed_at,batch 结束时调一次。"""
    bid = uuid.UUID(str(batch_id)) if not isinstance(batch_id, uuid.UUID) else batch_id
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(EvalsBatch).where(EvalsBatch.id == bid)
        )).scalar_one_or_none()
        if row is None:
            raise ValueError(f"evals_batch not found: {batch_id}")
        row.pass_count = pass_count
        row.pass_rate = pass_rate
        row.avg_iterations = avg_iterations
        row.avg_token_usage = avg_token_usage
        row.completed_at = _utcnow()
        await s.commit()
        await s.refresh(row)
    return row


async def get(batch_id: uuid.UUID | str) -> EvalsBatch | None:
    """按 id 取一条 batch。"""
    bid = uuid.UUID(str(batch_id)) if not isinstance(batch_id, uuid.UUID) else batch_id
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(EvalsBatch).where(EvalsBatch.id == bid)
        )).scalar_one_or_none()
    return row


async def list_recent(limit: int = 20) -> list[EvalsBatch]:
    """列最近几次 batch(给 PM 看月趋势用)。"""
    async with AsyncSessionLocal() as s:
        rows: Sequence[EvalsBatch] = (await s.execute(
            select(EvalsBatch)
            .order_by(EvalsBatch.started_at.desc())
            .limit(limit)
        )).scalars().all()
    return list(rows)


__all__ = ["start", "finalize", "get", "list_recent"]
