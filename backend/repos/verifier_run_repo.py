"""提案 2 · 验证执行记录(verifier_runs)CRUD 仓库。

字段语义见 backend/models/proposal2_verify.py 与提案文档 §3。
本仓库只做表层 CRUD,业务编排由 verifier_orchestrator 负责。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.proposal2_verify import VerifierRun

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """返回带时区(UTC)的当前时间。"""
    return datetime.now(timezone.utc)


# 允许 update 写入的字段白名单(防止 typo 把任意属性写到 ORM)。
_UPDATABLE_FIELDS: set[str] = {
    "completed_at",
    "llm_verifier_status",
    "llm_verifier_reason",
    "llm_verifier_model",
    "llm_verifier_tokens",
    "ground_truth_status",
    "ground_truth_logs",
    "gate_required",
    "gate_status",
    "gate_decided_by",
    "gate_decided_at",
    "gate_reason",
    "final_verdict",
}


async def create(delegation_id: str, attempt: int = 1) -> VerifierRun:
    """新建一条 verifier_run 记录(尚未跑任一闸)。

    Wave 0 schema 注释里 delegation_id 暂时指向 task.id 外键(由 sub-agent B
    决定),后续 wave 切到 delegations.id。本仓库不关心这一点,只透传字符串。
    """
    async with AsyncSessionLocal() as s:
        row = VerifierRun(
            delegation_id=delegation_id,
            attempt=attempt,
            started_at=_utcnow(),
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def update(run_id: uuid.UUID | str, **fields: Any) -> VerifierRun:
    """更新 verifier_run 的某些字段(只接受白名单内的列名)。

    fields 中允许的 key 见 _UPDATABLE_FIELDS;遇到未知 key 抛 ValueError。
    """
    unknown = set(fields.keys()) - _UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"unknown verifier_run fields: {sorted(unknown)}")

    rid = uuid.UUID(str(run_id)) if not isinstance(run_id, uuid.UUID) else run_id

    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(VerifierRun).where(VerifierRun.id == rid)
        )).scalar_one_or_none()
        if row is None:
            raise ValueError(f"verifier_run not found: {run_id}")
        for k, v in fields.items():
            setattr(row, k, v)
        await s.commit()
        await s.refresh(row)
    return row


async def get(run_id: uuid.UUID | str) -> VerifierRun | None:
    """按 id 取一条记录,找不到返回 None。"""
    rid = uuid.UUID(str(run_id)) if not isinstance(run_id, uuid.UUID) else run_id
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(VerifierRun).where(VerifierRun.id == rid)
        )).scalar_one_or_none()
    return row


async def list_for_delegation(delegation_id: str) -> list[VerifierRun]:
    """列出某 delegation 的全部 verifier_run,按 attempt 升序。"""
    async with AsyncSessionLocal() as s:
        rows: Sequence[VerifierRun] = (await s.execute(
            select(VerifierRun)
            .where(VerifierRun.delegation_id == delegation_id)
            .order_by(VerifierRun.attempt.asc())
        )).scalars().all()
    return list(rows)


async def latest_for_delegation(delegation_id: str) -> VerifierRun | None:
    """取该 delegation 最近一次的 verifier_run(attempt 最大者)。"""
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(VerifierRun)
            .where(VerifierRun.delegation_id == delegation_id)
            .order_by(VerifierRun.attempt.desc())
            .limit(1)
        )).scalar_one_or_none()
    return row


__all__ = [
    "create",
    "update",
    "get",
    "list_for_delegation",
    "latest_for_delegation",
]
