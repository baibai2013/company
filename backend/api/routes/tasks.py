import asyncio
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.core.config import settings
from backend.models.task import Task, TaskStep
from backend.schemas.task import (
    DecomposeDispatchRequest,
    TaskCreate,
    TaskDetailRead,
    TaskRead,
    TaskStepRead,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

log = logging.getLogger("api.tasks")

# ── state machine ─────────────────────────────────────────────────────────────

VALID_STATUSES = {"pending", "in_progress", "awaiting_approval", "done", "failed"}

# Terminal states have empty allowed-next sets。
# awaiting_approval:员工遇到关键/不可逆操作时暂停等 CEO 审批,批准后回 in_progress 继续。
_TRANSITIONS: dict[str, set[str]] = {
    "pending":           {"in_progress"},
    "in_progress":       {"awaiting_approval", "done", "failed"},
    "awaiting_approval": {"in_progress", "done", "failed"},
    "done":              set(),
    "failed":            set(),
}


def _check_transition(current: str, target: str) -> None:
    if target not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{target}'. Allowed: {sorted(VALID_STATUSES)}",
        )
    allowed = _TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot transition '{current}' → '{target}'. "
                   f"Allowed: {sorted(allowed) or '(terminal state)'}",
        )


# ── routes ────────────────────────────────────────────────────────────────────

@router.post("", response_model=TaskRead)
async def create_task(body: TaskCreate, db: AsyncSession = Depends(get_db)):
    task = Task(
        title=body.title,
        description=body.description,
        priority=body.priority,
        parent_id=body.parent_id,
        requester=body.requester,
        verifier=body.verifier,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 触发 orchestrator 编排(失败仅 log,不阻断 task 创建)
    try:
        from backend.services.orchestration_bridge import trigger_for_task
        await trigger_for_task(
            task.id, task.title, task.description, task.requester or "CEO",
        )
    except Exception as exc:
        import logging
        logging.getLogger("api.tasks").warning(
            "trigger_for_task failed task=%s err=%s", task.id, exc,
        )

    return task


async def _dispatch_one(tid: str, item: dict) -> None:
    """fire-and-forget:把一条已落库任务直派给指定执行人,并随执行结果回写状态。

    照搬 group_chat.orchestrator._execute_node._dispatch_one 的语义:
    in_progress → handle_dispatch(尊重指定 executor)→ done/failed。
    由长驻的 uvicorn 进程持有本协程生命周期,不阻塞 HTTP 响应。
    """
    from feishu.commands.dispatch import handle_dispatch
    from group_chat.task_steps import mark_task_status

    executor = item.get("executor", "")
    title = item.get("title", "")
    desc = item.get("description", "") or ""
    prompt = f"# 任务 {tid[:8]}\n## {title}\n\n{desc}"
    try:
        await mark_task_status(tid, "in_progress")
        log.info("decompose-dispatch: dispatching task=%s -> %s", tid[:8], executor)
        result = await handle_dispatch(
            employee=executor, task=prompt, task_id=tid, chat_id=f"task:{tid}",
        )
        # handle_dispatch 失败时也会返回非空 result(以 ❌ 开头的错误串),不能只看非空,
        # 否则连不上 agent / 调用异常都会被误判为成功 → 任务错置 done。
        res = result.get("result") or ""
        ok = bool(res) and not res.lstrip().startswith("❌")
        await mark_task_status(tid, "done" if ok else "failed")
        log.info("decompose-dispatch: task=%s done=%s", tid[:8], ok)
    except Exception as exc:
        log.warning("decompose-dispatch: dispatch failed task=%s err=%s", tid[:8], exc)
        try:
            await mark_task_status(tid, "failed")
        except Exception:
            pass


@router.post("/decompose-dispatch")
async def decompose_dispatch(body: DecomposeDispatchRequest):
    """建任务(带指定执行人)+ 立即直派。

    与 POST / 不同:此端点尊重芳芳指定的 executor、直接 A2A 派单,不经会议重判。
    落库后对每条任务 fire-and-forget 派单,立即返回指派清单(不等子任务跑完)。
    """
    from group_chat.task_steps import _VALID_EXECUTORS, create_tasks_from_decompose

    items = [it.model_dump() for it in body.tasks]
    task_ids = await create_tasks_from_decompose(items, requester=body.requester)
    if not task_ids:
        return {"task_ids": [], "assignments": []}

    # create_tasks_from_decompose 丢弃非法项,返回的 task_ids 与「过滤后」的 items 对齐
    valid_items = [
        it for it in items
        if (it.get("executor") or "") in _VALID_EXECUTORS and (it.get("title") or "").strip()
    ]
    paired = list(zip(task_ids, valid_items[: len(task_ids)]))

    for tid, item in paired:
        asyncio.create_task(_dispatch_one(tid, item))

    assignments = [
        {
            "id": tid,
            "title": item.get("title", ""),
            "executor": item.get("executor", ""),
            "priority": item.get("priority", "P1"),
        }
        for tid, item in paired
    ]
    log.info("decompose-dispatch: created+dispatched %d tasks", len(paired))
    return {"task_ids": list(task_ids), "assignments": assignments}


@router.get("", response_model=list[TaskRead])
async def list_tasks(status: str | None = None, db: AsyncSession = Depends(get_db)):
    q = select(Task).order_by(Task.created_at.desc())
    if status:
        if status not in VALID_STATUSES:
            raise HTTPException(status_code=422, detail=f"Invalid status filter '{status}'")
        q = q.where(Task.status == status)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{task_id}", response_model=TaskDetailRead)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    children_result = await db.execute(select(Task).where(Task.parent_id == task_id))
    children = children_result.scalars().all()
    detail = TaskDetailRead.model_validate(task)
    detail.children = [TaskRead.model_validate(c) for c in children]
    return detail


@router.patch("/{task_id}/status")
async def update_task_status(task_id: str, status: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    _check_transition(task.status, status)
    task.status = status
    await db.commit()
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        await r.publish("task_events", f'{{"task_id":"{task_id}","status":"{status}"}}')
        await r.aclose()
    except Exception:
        pass  # Redis optional — don't block the status update
    return {"ok": True}


@router.patch("/{task_id}/executor")
async def update_executor(task_id: str, executor: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.executor = executor
    await db.commit()
    return {"ok": True}


@router.post("/{task_id}/approve")
async def approve_task(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # 任务正卡在 awaiting_approval(员工因关键/不可逆操作暂停)时,审批=放行→回 in_progress,
    # 自主循环下次 my-tasks 会重新拾起继续干。其它状态保持原样,只发 gate_signal 兼容 verifier 流水线。
    resumed = False
    if task.status == "awaiting_approval":
        task.status = "in_progress"
        await db.commit()
        resumed = True
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        await r.publish(f"gate_signal:{task_id}", "approved")
        if resumed:
            await r.publish("task_events", f'{{"task_id":"{task_id}","status":"in_progress"}}')
        await r.aclose()
    except Exception:
        pass
    return {"ok": True, "resumed": resumed}


@router.delete("/{task_id}")
async def delete_task(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # Detach children before delete (FK is ON DELETE SET NULL but be explicit)
    children_result = await db.execute(select(Task).where(Task.parent_id == task_id))
    for child in children_result.scalars().all():
        child.parent_id = None
    await db.delete(task)
    await db.commit()
    return {"ok": True}


@router.get("/{task_id}/steps", response_model=list[TaskStepRead])
async def list_task_steps(task_id: str, db: AsyncSession = Depends(get_db)):
    """返回 task_step 链路,按 started_at 排序。前端 Pipeline 视图数据源。"""
    if not await db.get(Task, task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    q = select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.started_at)
    rows = (await db.execute(q)).scalars().all()
    out: list[TaskStepRead] = []
    for r in rows:
        duration_ms = None
        if r.started_at and r.finished_at:
            duration_ms = int((r.finished_at - r.started_at).total_seconds() * 1000)
        out.append(TaskStepRead(
            id=r.id,
            step_name=r.step_name,
            status=r.status,
            input=r.input,
            output=r.output,
            started_at=r.started_at,
            finished_at=r.finished_at,
            duration_ms=duration_ms,
        ))
    return out
