from datetime import datetime

from pydantic import BaseModel


class MessageCreate(BaseModel):
    content: str
    sender: str = "user"


class MessageRead(BaseModel):
    id: str
    channel: str
    role: str
    sender: str
    content: str
    created_at: datetime
    model_config = {"from_attributes": True}
