"""任务结束摘要器 — 提案 1 §3.2 / §5.2。

任务结束时,把本员工在 task_context 里的发言/动作压缩成 5-10 条 lessons,
写入 employee_memory 形成 L1 长期记忆。

本 wave 是 stub:
  - **不调 LLM**,直接拿 task_context 末尾 3 条 chunk 作为 lesson 落库
  - 给固定 importance=5
  - source_task_id 填回去,后续可追溯
  - TODO(Wave 3 接 retro_agent): 真调 Haiku,让其抽 5-10 条 + 自评 importance
    + 自动生成 embedding(走 memory_repo._embed)
"""
from __future__ import annotations

import logging
import uuid
from typing import Sequence

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.memory import EmployeeMemory
from backend.models.proposal1_state import TaskContext

log = logging.getLogger(__name__)

# stub 期取末尾 N 条 chunk 当 lesson
_STUB_TAIL_N = 3
# stub 期默认 importance(后续 Haiku 自评会覆盖)
_STUB_DEFAULT_IMPORTANCE = 5


async def summarize_task_to_memory(
    employee_key: str,
    task_id: str,
) -> list[EmployeeMemory]:
    """把任务里"本员工参与的 chunk"压缩成若干 lessons,写入 employee_memory。

    返回:实际写入的 EmployeeMemory 实例列表(refreshed,带 id / created_at)。

    设计选择:
      - **只看本员工的 chunk**(过滤 employee_key 相等),不看其他人的;
        别人的发言进自己 L1 记忆会污染。
      - 不去重:同一任务多次调本函数会重复写,caller 自己保证只在任务结束调一次。
    """
    async with AsyncSessionLocal() as s:
        rows: Sequence[TaskContext] = (await s.execute(
            select(TaskContext)
            .where(
                TaskContext.task_id == task_id,
                TaskContext.employee_key == employee_key,
            )
            .order_by(TaskContext.created_at.asc())
        )).scalars().all()

    if not rows:
        log.info(
            "memory_summarizer: no task_context rows for employee=%s task=%s, skip",
            employee_key, task_id,
        )
        return []

    # ── stub 提取:取末尾 _STUB_TAIL_N 条 chunk 作为 lessons ──
    # TODO(Wave 3): 用 Haiku 抽 5-10 条带 importance 评分,这里先用尾部 3 条占位。
    tail = list(rows)[-_STUB_TAIL_N:]
    lessons_text: list[str] = [r.content_chunk for r in tail]

    # source_task_id 列是 PgUUID(as_uuid=True),这里转成 uuid.UUID
    try:
        src_task_uuid = uuid.UUID(str(task_id))
    except (ValueError, TypeError):
        src_task_uuid = None

    written: list[EmployeeMemory] = []
    async with AsyncSessionLocal() as s:
        for content in lessons_text:
            mem = EmployeeMemory(
                employee_key=employee_key,
                content=content,
                template="task_summary",
                importance=_STUB_DEFAULT_IMPORTANCE,
                source_task_id=src_task_uuid,
                pinned=False,
            )
            s.add(mem)
            written.append(mem)
        await s.commit()
        for m in written:
            await s.refresh(m)

    log.info(
        "memory_summarizer: wrote %d stub lessons employee=%s task=%s",
        len(written), employee_key, task_id,
    )
    return written
