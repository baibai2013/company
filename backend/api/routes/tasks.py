import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.core.config import settings
from backend.models.task import Task, TaskStep
from backend.schemas.task import TaskCreate, TaskDetailRead, TaskRead, TaskStepRead

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# ── state machine ─────────────────────────────────────────────────────────────

VALID_STATUSES = {"pending", "in_progress", "done", "failed"}

# Terminal states have empty allowed-next sets
_TRANSITIONS: dict[str, set[str]] = {
    "pending":     {"in_progress"},
    "in_progress": {"done", "failed"},
    "done":        set(),
    "failed":      set(),
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
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        await r.publish(f"gate_signal:{task_id}", "approved")
        await r.aclose()
    except Exception:
        pass
    return {"ok": True}


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
