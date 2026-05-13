from datetime import datetime

from pydantic import BaseModel, field_validator


class MessageCreate(BaseModel):
    content: str
    sender: str = "user"

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be empty")
        return v


class MessageRead(BaseModel):
    id: str
    channel: str
    role: str
    sender: str
    content: str
    created_at: datetime
    model_config = {"from_attributes": True}
