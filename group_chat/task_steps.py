"""task_step 写入助手：把 group_chat orchestrator 的 graph 节点流转持久化到 task_step 表。

设计要点：
- 写库失败不阻断 graph 执行（finally 块内 swallow 异常并 log）
- task_id=None 时整体 noop（飞书群消息触发的场景）
- chat_id 为 "task:{uuid}" 时认为是 Task 触发,提取 task_id

放在 group_chat 包内而非 backend/services,是为了让 orchestrator 和 pipelines 都能 import,
不引入循环依赖(backend → group_chat 单向)。
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.task import Task, TaskStep

log = logging.getLogger(__name__)


_VALID_EXECUTORS = frozenset({
    "mechanical", "hardware", "firmware", "algorithm",
    "testing", "cost", "product_manager", "project_manager", "tech_lead",
})


async def create_tasks_from_decompose(
    items: list[dict],
    *,
    requester: str = "group_chat",
    parent_id: str | None = None,
) -> list[str]:
    """把 PM 拆解出的 task list 批量落库。

    每条 item 形如 {"title", "description", "executor", "priority"}。
    校验 executor 在白名单内,否则丢弃并 log。
    返回成功落库的 task_id 列表。

    失败仅 log 不抛——orchestrator graph 不应该因为落库失败而崩溃。
    """
    if not items:
        return []

    created: list[str] = []
    try:
        async with AsyncSessionLocal() as db:
            for it in items:
                if not isinstance(it, dict):
                    continue
                executor = (it.get("executor") or "").strip()
                if executor not in _VALID_EXECUTORS:
                    log.warning("decompose: drop task — executor=%r not in whitelist", executor)
                    continue
                title = (it.get("title") or "").strip()[:200]
                if not title:
                    continue
                desc = (it.get("description") or "").strip()
                priority = it.get("priority") or "P1"
                if priority not in ("P0", "P1", "P2"):
                    priority = "P1"
                tid = str(uuid.uuid4())
                db.add(Task(
                    id=tid,
                    parent_id=parent_id,
                    title=title,
                    description=desc,
                    priority=priority,
                    status="pending",
                    requester=requester,
                    executor=executor,
                ))
                created.append(tid)
            await db.commit()
        log.info("create_tasks_from_decompose: created %d tasks (out of %d items)",
                 len(created), len(items))
    except Exception as exc:
        log.warning("create_tasks_from_decompose: bulk insert failed err=%s", exc)
    return created

_TASK_CHAT_PREFIX = "task:"


def extract_task_id(chat_id: str | None) -> str | None:
    """从 chat_id 提取 task_id。"""
    if chat_id and chat_id.startswith(_TASK_CHAT_PREFIX):
        return chat_id[len(_TASK_CHAT_PREFIX):]
    return None


def is_task_chat(chat_id: str | None) -> bool:
    return bool(chat_id and chat_id.startswith(_TASK_CHAT_PREFIX))


async def mark_task_status(task_id: str, target: str) -> None:
    """把 Task.status 推到目标状态(pending → in_progress → done)。

    幂等:已是目标状态时 no-op。失败仅 log 不抛。
    """
    if not task_id:
        return
    from backend.models.task import Task
    try:
        async with AsyncSessionLocal() as db:
            task = await db.get(Task, task_id)
            if task is None or task.status == target:
                return
            # 沿用 backend.api.routes.tasks._TRANSITIONS 的语义
            allowed = {
                "pending": {"in_progress"},
                "in_progress": {"done", "failed"},
            }
            if target not in allowed.get(task.status, set()):
                log.info("mark_task_status: skip transition %s → %s task=%s",
                         task.status, target, task_id)
                return
            task.status = target
            await db.commit()
            log.info("mark_task_status: task=%s → %s", task_id, target)
    except Exception as exc:
        log.warning("mark_task_status: failed task=%s target=%s err=%s",
                    task_id, target, exc)


@asynccontextmanager
async def step_record(
    task_id: str | None,
    step_name: str,
    input_summary: str = "",
):
    """记录一条 task_step 行,跨 graph 节点的进入和退出。

    - task_id=None → 整体 noop
    - 入: status=running, started_at, input
    - 出(成功): status=done, finished_at
    - 出(异常): status=failed, finished_at, output=ERROR repr(exc)
    - 写库失败 → log warning,不抛
    """
    if not task_id:
        yield
        return

    started = datetime.now(timezone.utc)
    step_id = str(uuid.uuid4())
    persisted = False

    try:
        async with AsyncSessionLocal() as db:
            db.add(TaskStep(
                id=step_id,
                task_id=task_id,
                step_name=step_name,
                status="running",
                input=(input_summary or "")[:2000],
                started_at=started,
            ))
            await db.commit()
            persisted = True
    except Exception as exc:
        log.warning("step_record: insert failed task=%s step=%s err=%s",
                    task_id, step_name, exc)

    err: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        err = exc
        raise
    finally:
        if persisted:
            finished = datetime.now(timezone.utc)
            try:
                async with AsyncSessionLocal() as db:
                    row = (await db.execute(
                        select(TaskStep).where(TaskStep.id == step_id)
                    )).scalar_one_or_none()
                    if row is not None:
                        row.status = "failed" if err else "done"
                        row.finished_at = finished
                        if err:
                            row.output = f"ERROR: {err!r}"[:2000]
                        await db.commit()
            except Exception as exc:
                log.warning("step_record: update failed task=%s step=%s err=%s",
                            task_id, step_name, exc)
