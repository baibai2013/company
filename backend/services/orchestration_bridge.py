"""Task → group_chat orchestrator 触发桥。

把 Task.title + Task.description 合成一个 MessageEvent,publish 到 Redis 的
group_msg:task:{task_id} 频道。orchestrator 已经在 subscribe_group_pattern() 上
等着,会按正常流程跑 receive→decide→dispatch→conclude。

返回值: (ok, error_msg) — 失败不抛,让调用方决定要不要阻断。
"""
from __future__ import annotations

import logging
import uuid

log = logging.getLogger(__name__)


async def trigger_for_task(
    task_id: str,
    title: str,
    description: str | None = None,
    requester: str = "CEO",
) -> tuple[bool, str]:
    """合成 MessageEvent 并 publish 到 group_msg:task:{task_id} 频道。

    设计上不复用 orchestrator 的 GroupEventBus 实例,避免 backend 进程依赖
    长连接;每次 publish 自己开 Redis 连接发完关掉,简单稳。
    """
    text = title if not description else f"{title}\n\n{description}"

    try:
        # 延迟 import 让 backend 启动顺序不依赖 group_chat 包
        from group_chat.event_bus import GroupEventBus
        from group_chat.models import MessageEvent

        bus = GroupEventBus()
        await bus.connect()
        try:
            await bus.publish_message(MessageEvent(
                message_id=str(uuid.uuid4()),
                chat_id=f"task:{task_id}",
                sender=requester,
                text=text,
                image_base64="",
                mentions=[],
            ))
        finally:
            await bus.disconnect()
        log.info("orchestration_bridge: published task=%s requester=%s", task_id, requester)
        return True, ""
    except Exception as exc:
        log.warning("orchestration_bridge: publish failed task=%s err=%s", task_id, exc)
        return False, str(exc)
