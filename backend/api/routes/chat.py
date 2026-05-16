import asyncio
import importlib
import re
from asyncio import CancelledError

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.core.db import AsyncSessionLocal
from backend.models.message import ChatMessage
from backend.schemas.message import MessageCreate, MessageRead
from backend.services import registry

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Keywords that signal a work task (not casual chat)
_WORK_RE = re.compile(
    r"设计|开发|实现|建模|分析|验证|输出|生成|创建|编写|规划|调试|测试|"
    r"优化|计算|评审|报告|方案|需求|任务|BOM|CAD|PCB|固件|算法|仿真|URDF|STEP",
    re.IGNORECASE,
)

def _employee_port(key: str) -> int | None:
    cfg = registry.get_effective_sync(key)
    return cfg.agent_port if cfg else None


def _employee_name(key: str) -> str:
    cfg = registry.get_effective_sync(key)
    return cfg.name if cfg else key


async def _save_msg(channel: str, role: str, sender: str, content: str) -> None:
    async with AsyncSessionLocal() as db:
        db.add(ChatMessage(channel=channel, role=role, sender=sender, content=content))
        await db.commit()


# ── fire-and-forget agent call ────────────────────────────────────────────────

async def _call_agent_and_save(
    employee_key: str, user_text: str, *, channel: str | None = None
) -> None:
    """Call employee A2A agent and persist the reply.

    For work tasks: saves an immediate ack so user sees activity fast,
    then saves the full result when the agent finishes.
    """
    port = _employee_port(employee_key)
    if not port:
        return

    try:
        a2a = importlib.import_module("agents_v2.shared.a2a_server")
        call_agent = a2a.call_agent
    except ModuleNotFoundError:
        return  # agents_v2 not available (e.g. unit-test env)

    save_channel = channel if channel is not None else employee_key
    url = f"http://localhost:{port}/"
    name = _employee_name(employee_key)

    # Save immediate ack for work tasks so the user isn't staring at silence
    is_work = bool(_WORK_RE.search(user_text))
    if is_work:
        await _save_msg(save_channel, "assistant", employee_key,
                        f"收到，我来处理这个任务，稍等…")

    try:
        reply = await call_agent(url, user_text, timeout=180)
        # For work tasks, prefix result with a progress marker
        if is_work:
            reply = f"✅ 完成！以下是结果：\n\n{reply}"
        await _save_msg(save_channel, "assistant", employee_key, reply)
    except CancelledError:
        pass  # Event loop teardown — exit silently without re-raising
    except Exception:
        pass  # Agent unreachable or DB write failed — frontend polls for reply


# ── group chat ────────────────────────────────────────────────────────────────

@router.post("/group")
async def post_group_message(body: MessageCreate) -> dict:
    """供 agent 工具调用：将消息注入群聊（持久化 + Redis publish）。"""
    from backend.chat.kanban_adapter import kanban_adapter
    await kanban_adapter.on_message(
        channel_id="group",
        sender=body.sender or "system",
        text=body.content if isinstance(body.content, str) else str(body.content),
    )
    return {"ok": True}


@router.get("/group/history", response_model=list[MessageRead])
async def group_history(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.channel == "group").order_by(ChatMessage.created_at)
    )
    return result.scalars().all()


# ── direct chat ───────────────────────────────────────────────────────────────

@router.post("/direct/{employee}", response_model=MessageRead)
async def post_direct(
    employee: str,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db),
):
    # Persist user message and return immediately
    msg = ChatMessage(channel=employee, role="user", sender=body.sender, content=body.content)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # Schedule agent call without blocking the response
    asyncio.create_task(_call_agent_and_save(employee, body.content))
    return msg


@router.get("/direct/{employee}/history", response_model=list[MessageRead])
async def direct_history(employee: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.channel == employee).order_by(ChatMessage.created_at)
    )
    return result.scalars().all()
