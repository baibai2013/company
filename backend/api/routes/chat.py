from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.models.message import ChatMessage
from backend.schemas.message import MessageCreate, MessageRead

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/group", response_model=MessageRead)
async def post_group(body: MessageCreate, db: AsyncSession = Depends(get_db)):
    msg = ChatMessage(channel="group", role="user", sender=body.sender, content=body.content)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


@router.get("/group/history", response_model=list[MessageRead])
async def group_history(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.channel == "group").order_by(ChatMessage.created_at)
    )
    return result.scalars().all()


@router.post("/direct/{employee}", response_model=MessageRead)
async def post_direct(employee: str, body: MessageCreate, db: AsyncSession = Depends(get_db)):
    msg = ChatMessage(channel=employee, role="user", sender=body.sender, content=body.content)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


@router.get("/direct/{employee}/history", response_model=list[MessageRead])
async def direct_history(employee: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.channel == employee).order_by(ChatMessage.created_at)
    )
    return result.scalars().all()
