from datetime import datetime

from pydantic import BaseModel, field_validator


class TaskCreate(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be empty")
        return v
    description: str | None = None
    priority: str = "P1"
    parent_id: str | None = None
    requester: str = "CEO"
    verifier: str | None = None


class DecomposeTaskItem(BaseModel):
    """芳芳拆解出的单条子任务(按需直派用)。"""
    title: str
    description: str | None = None
    executor: str
    priority: str = "P1"


class DecomposeDispatchRequest(BaseModel):
    """POST /api/tasks/decompose-dispatch 请求体:一组带指定执行人的子任务。"""
    tasks: list[DecomposeTaskItem]
    requester: str = "CEO"


class TaskStepRead(BaseModel):
    id: str
    step_name: str
    status: str
    input: str | None = None
    output: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None = None

    model_config = {"from_attributes": True}


class TaskRead(BaseModel):
    id: str
    parent_id: str | None
    title: str
    description: str | None
    priority: str
    status: str
    requester: str | None
    executor: str | None
    verifier: str | None
    created_at: datetime
    updated_at: datetime
    steps: list[TaskStepRead] = []

    model_config = {"from_attributes": True}


class TaskDetailRead(TaskRead):
    """Detail view — includes child tasks."""
    children: list[TaskRead] = []
