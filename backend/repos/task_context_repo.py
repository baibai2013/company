"""任务级共享上下文(L2)仓库 — 提案 1 §3.1。

写入：每次 LLM 完成回合 / 工具调用关键节点把"发生了什么"chunk 化落表,
       embedding 字段是可选的(没有 OPENAI_API_KEY 时留空)。
读取：context_builder 在员工启动时取最近 N 条做 token-budget 截断。

降级策略：
  - 不在这里生成 embedding(那是 memory_repo 的职责),caller 想做语义检索
    时可显式传 embedding。
"""
from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.proposal1_state import TaskContext

log = logging.getLogger(__name__)


# ── 写入 ───────────────────────────────────────────────────────────────
async def append_context(
    task_id: str,
    employee_key: str,
    role: str,
    content_chunk: str,
    embedding: list[float] | None = None,
    parent_task_id: str | None = None,
) -> TaskContext:
    """追加一条任务上下文 chunk。

    参数:
        task_id        根任务 id(UUID 字符串形式,与 task.id 对齐)
        employee_key   说话/做事的员工 key
        role           speak | act | decide | deliver
        content_chunk  markdown 内容,建议 512-2048 token
        embedding      1536 维向量,可选
        parent_task_id 子任务父级,可选

    返回:
        刚写入的 TaskContext 实例(已 refresh,带 id / created_at)。
    """
    async with AsyncSessionLocal() as s:
        row = TaskContext(
            task_id=task_id,
            parent_task_id=parent_task_id,
            employee_key=employee_key,
            role=role,
            content_chunk=content_chunk,
            embedding=embedding,
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


# ── 读取 ───────────────────────────────────────────────────────────────
async def list_for_task(task_id: str, limit: int = 50) -> list[TaskContext]:
    """按 created_at 升序取一个任务的所有 chunk(默认前 50 条)。

    用于审计 / 调试 / 回放;context_builder 走 latest_n_chunks。
    """
    async with AsyncSessionLocal() as s:
        rows: Sequence[TaskContext] = (await s.execute(
            select(TaskContext)
            .where(TaskContext.task_id == task_id)
            .order_by(TaskContext.created_at.asc())
            .limit(limit)
        )).scalars().all()
    return list(rows)


async def latest_n_chunks(task_id: str, n: int = 20) -> list[TaskContext]:
    """取最近 n 条 chunk,**按时间升序**返回(读起来像时间线)。

    内部用 desc + limit 取最近 n 条,再 reverse 成升序;
    这样做比 ORDER BY ASC LIMIT 性能更好(走 ix_task_context_task_time)。
    """
    async with AsyncSessionLocal() as s:
        rows: Sequence[TaskContext] = (await s.execute(
            select(TaskContext)
            .where(TaskContext.task_id == task_id)
            .order_by(TaskContext.created_at.desc())
            .limit(n)
        )).scalars().all()
    return list(reversed(list(rows)))
