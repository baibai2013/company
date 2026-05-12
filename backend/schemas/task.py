from datetime import datetime

from pydantic import BaseModel


class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    priority: str = "P1"


class TaskStepRead(BaseModel):
    id: str
    step_name: str
    status: str
    output: str | None
    started_at: datetime | None
    finished_at: datetime | None
    model_config = {"from_attributes": True}


class TaskRead(BaseModel):
    id: str
    title: str
    description: str | None
    priority: str
    status: str
    created_at: datetime
    steps: list[TaskStepRead] = []
    model_config = {"from_attributes": True}
