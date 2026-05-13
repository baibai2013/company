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
