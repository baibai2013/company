"""提案 2 · 检查项明细(acceptance_checks)仓库。

每个 ground truth checker 跑完一次写一条记录,便于趋势分析(check_name × ok)。
"""
from __future__ import annotations

import logging
import uuid
from typing import Sequence

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.proposal2_verify import AcceptanceCheck

log = logging.getLogger(__name__)

# stdout/stderr 截断阈值(8KB),与 schema 注释保持一致。
_OUTPUT_LOG_MAX_BYTES = 8 * 1024


def _truncate(s: str | None, max_bytes: int = _OUTPUT_LOG_MAX_BYTES) -> str | None:
    """按 utf-8 字节数截断,超过时尾部加 "...<truncated>"。"""
    if s is None:
        return None
    encoded = s.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return s
    cut = encoded[: max_bytes - 16]
    # 解码时丢弃尾部可能切碎的字节
    return cut.decode("utf-8", errors="ignore") + "...<truncated>"


async def record(
    verifier_run_id: uuid.UUID | str,
    check_name: str,
    ok: bool,
    duration_ms: int,
    err_msg: str | None = None,
    output_log: str | None = None,
) -> AcceptanceCheck:
    """记录一次 checker 运行结果(自动截断 output_log)。"""
    rid = (
        uuid.UUID(str(verifier_run_id))
        if not isinstance(verifier_run_id, uuid.UUID)
        else verifier_run_id
    )
    async with AsyncSessionLocal() as s:
        row = AcceptanceCheck(
            verifier_run_id=rid,
            check_name=check_name,
            ok=ok,
            duration_ms=duration_ms,
            err_msg=_truncate(err_msg, max_bytes=4096),
            output_log=_truncate(output_log),
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def list_for_run(run_id: uuid.UUID | str) -> list[AcceptanceCheck]:
    """列出某 verifier_run 的全部 check 明细,按 created_at 升序。"""
    rid = uuid.UUID(str(run_id)) if not isinstance(run_id, uuid.UUID) else run_id
    async with AsyncSessionLocal() as s:
        rows: Sequence[AcceptanceCheck] = (await s.execute(
            select(AcceptanceCheck)
            .where(AcceptanceCheck.verifier_run_id == rid)
            .order_by(AcceptanceCheck.created_at.asc())
        )).scalars().all()
    return list(rows)


__all__ = ["record", "list_for_run"]
