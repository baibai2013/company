import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.core.config import settings
from backend.models.task import Task, TaskStep
from backend.schemas.task import TaskCreate, TaskRead, TaskStepRead

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=TaskRead)
async def create_task(body: TaskCreate, db: AsyncSession = Depends(get_db)):
    task = Task(title=body.title, description=body.description, priority=body.priority)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.get("", response_model=list[TaskRead])
async def list_tasks(status: str | None = None, db: AsyncSession = Depends(get_db)):
    q = select(Task).order_by(Task.created_at.desc())
    if status:
        q = q.where(Task.status == status)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/{task_id}/status")
async def update_task_status(task_id: str, status: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = status
    await db.commit()
    r = aioredis.from_url(settings.REDIS_URL)
    await r.publish("task_events", f'{{"task_id":"{task_id}","status":"{status}"}}')
    await r.aclose()
    return {"ok": True}


@router.post("/{task_id}/approve")
async def approve_task(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    r = aioredis.from_url(settings.REDIS_URL)
    await r.publish(f"gate_signal:{task_id}", "approved")
    await r.aclose()
    return {"ok": True}
