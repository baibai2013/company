"""
Redis Pub/Sub event bus for group chat orchestration.
Section VI of doc/design/group-chat-redesign.md.

Channel naming (with multi-group isolation):
  group_msg:{chat_id}            — group message entry
  speak_req:{employee}:{chat_id} — speak request for employee in group
  speak_resp:{session_id}        — speak response for session
  user_input:{chat_id}           — forwarded user message during active game
"""
import asyncio
import json
import logging
from typing import AsyncIterator, Callable, Awaitable

import redis.asyncio as aioredis

from .models import MessageEvent, SpeakRequest, SpeakResponse

REDIS_URL = "redis://localhost:6379/0"

log = logging.getLogger("group_chat.event_bus")


class GroupEventBus:
    def __init__(self, redis_url: str = REDIS_URL):
        self._redis_url = redis_url
        self._pub: aioredis.Redis | None = None
        self._sub: aioredis.client.PubSub | None = None
        self._sub_client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._pub = aioredis.from_url(self._redis_url)
        self._sub_client = aioredis.from_url(self._redis_url)
        self._sub = self._sub_client.pubsub()

    async def disconnect(self) -> None:
        if self._sub:
            await self._sub.unsubscribe()
            await self._sub.reset()
        if self._sub_client:
            await self._sub_client.close()
        if self._pub:
            await self._pub.close()

    # ── publish ───────────────────────────────────────────────────────────

    async def publish_message(self, event: MessageEvent) -> None:
        channel = f"group_msg:{event.chat_id}"
        payload = json.dumps({
            "message_id": event.message_id,
            "chat_id": event.chat_id,
            "sender": event.sender,
            "text": event.text,
            "image_base64": event.image_base64,
            "mentions": event.mentions,
        }, ensure_ascii=False)
        await self._pub.publish(channel, payload)
        log.info("published group_msg:%s mid=%s", event.chat_id, event.message_id)

    async def publish_speak_req(self, req: SpeakRequest) -> None:
        channel = f"speak_req:{req.employee}:{req.chat_id}"
        payload = json.dumps({
            "session_id": req.session_id,
            "chat_id": req.chat_id,
            "employee": req.employee,
            "history_text": req.history_text,
            "trigger_message_id": req.trigger_message_id,
            "image_base64": req.image_base64,
            "order": req.order,
            "summary_mode": req.summary_mode,
            "role_context": req.role_context,
        }, ensure_ascii=False)
        await self._pub.publish(channel, payload)
        log.info("published speak_req:%s:%s session=%s", req.employee, req.chat_id, req.session_id)

    async def publish_user_input(
        self, chat_id: str, text: str, message_id: str = "", sender: str = "",
    ) -> None:
        """转发用户消息到活跃场景（游戏进行中用户发的消息走这个频道）。"""
        channel = f"user_input:{chat_id}"
        payload = json.dumps({
            "chat_id": chat_id, "text": text,
            "message_id": message_id, "sender": sender,
        }, ensure_ascii=False)
        await self._pub.publish(channel, payload)
        log.info("published user_input:%s sender=%s", chat_id, sender)

    async def publish_speak_resp(self, resp: SpeakResponse) -> None:
        channel = f"speak_resp:{resp.session_id}"
        payload = json.dumps({
            "session_id": resp.session_id,
            "chat_id": resp.chat_id,
            "employee": resp.employee,
            "content": resp.content,
            "success": resp.success,
        }, ensure_ascii=False)
        await self._pub.publish(channel, payload)
        log.info("published speak_resp:%s employee=%s", resp.session_id, resp.employee)

    # ── subscribe ──────────────────────────────────────────────────────────

    async def subscribe_group(self, chat_id: str) -> AsyncIterator[MessageEvent]:
        """Subscribe to group_msg:{chat_id}, yield MessageEvent on each message."""
        channel = f"group_msg:{chat_id}"
        await self._sub.subscribe(channel)
        log.info("subscribed to %s", channel)
        async for msg in self._sub.listen():
            if msg["type"] != "message":
                continue
            try:
                data = json.loads(msg["data"])
                yield MessageEvent(
                    message_id=data["message_id"],
                    chat_id=data["chat_id"],
                    sender=data["sender"],
                    text=data["text"],
                    image_base64=data.get("image_base64", ""),
                    mentions=data.get("mentions", []),
                )
            except Exception as exc:
                log.warning("parse group_msg failed: %s", exc)

    async def subscribe_group_pattern(self) -> AsyncIterator[MessageEvent]:
        """Subscribe to group_msg:* (pattern). Used by orchestrator to listen to all groups."""
        pattern = "group_msg:*"
        await self._sub.psubscribe(pattern)
        log.info("pattern-subscribed to %s", pattern)
        async for msg in self._sub.listen():
            if msg["type"] != "pmessage":
                continue
            try:
                data = json.loads(msg["data"])
                yield MessageEvent(
                    message_id=data["message_id"],
                    chat_id=data["chat_id"],
                    sender=data["sender"],
                    text=data["text"],
                    image_base64=data.get("image_base64", ""),
                    mentions=data.get("mentions", []),
                )
            except Exception as exc:
                log.warning("parse group_msg pattern failed: %s", exc)

    async def subscribe_user_input(self, chat_id: str) -> AsyncIterator[dict]:
        """监听转发过来的用户消息（场景等待 CEO 输入时使用）。"""
        channel = f"user_input:{chat_id}"
        await self._sub.subscribe(channel)
        log.info("subscribed to %s (user_input)", channel)
        async for msg in self._sub.listen():
            if msg["type"] != "message":
                continue
            try:
                yield json.loads(msg["data"])
            except Exception as exc:
                log.warning("parse user_input failed: %s", exc)

    async def subscribe_speak_req(
        self, employee: str, chat_ids: list[str], callback: Callable[[SpeakRequest], Awaitable[None]],
    ) -> None:
        """Subscribe to speak_req:{employee}:{chat_id} for all groups the bot is in."""
        channels = [f"speak_req:{employee}:{cid}" for cid in chat_ids]
        if not channels:
            log.warning("subscribe_speak_req: no channels for employee=%s", employee)
            return
        await self._sub.subscribe(*channels)
        log.info("subscribed to %s", channels)
        async for msg in self._sub.listen():
            if msg["type"] != "message":
                continue
            try:
                data = json.loads(msg["data"])
                req = SpeakRequest(
                    session_id=data["session_id"],
                    chat_id=data["chat_id"],
                    employee=data["employee"],
                    history_text=data["history_text"],
                    trigger_message_id=data["trigger_message_id"],
                    image_base64=data.get("image_base64", ""),
                    order=data.get("order", 0),
                    summary_mode=data.get("summary_mode", False),
                    role_context=data.get("role_context", ""),
                )
                await callback(req)
            except Exception as exc:
                log.warning("handle speak_req failed: %s", exc)

    async def subscribe_speak_req_pattern(
        self, channel_pattern: str, callback: Callable[[SpeakRequest], Awaitable[None]],
    ) -> None:
        """用 Redis PSUBSCRIBE 订阅 speak_req:{pattern} 频道，供平台适配器使用。

        例：channel_pattern="kanban_*" 会订阅所有 speak_req:*:kanban_* 频道。
        """
        pattern = f"speak_req:*:{channel_pattern}"
        await self._sub.psubscribe(pattern)
        log.info("psubscribed to %s", pattern)
        async for msg in self._sub.listen():
            if msg["type"] != "pmessage":
                continue
            try:
                data = json.loads(msg["data"])
                req = SpeakRequest(
                    session_id=data["session_id"],
                    chat_id=data["chat_id"],
                    employee=data["employee"],
                    history_text=data["history_text"],
                    trigger_message_id=data["trigger_message_id"],
                    image_base64=data.get("image_base64", ""),
                    order=data.get("order", 0),
                    summary_mode=data.get("summary_mode", False),
                    role_context=data.get("role_context", ""),
                )
                await callback(req)
            except Exception as exc:
                log.warning("handle speak_req (pattern) failed: %s", exc)

    async def wait_for_speak_resp(
        self, session_id: str, timeout: float = 90.0,
    ) -> SpeakResponse | None:
        """Wait for a single speak response on speak_resp:{session_id}. Returns None on timeout."""
        channel = f"speak_resp:{session_id}"
        await self._sub.subscribe(channel)
        try:
            async for msg in self._sub.listen():
                if msg["type"] != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                    return SpeakResponse(
                        session_id=data["session_id"],
                        chat_id=data["chat_id"],
                        employee=data["employee"],
                        content=data["content"],
                        success=data.get("success", True),
                    )
                except Exception as exc:
                    log.warning("parse speak_resp failed: %s", exc)
                    return None
        except asyncio.TimeoutError:
            return None
        finally:
            await self._sub.unsubscribe(channel)


class GroupEventBusPool:
    """
    Pool of two GroupEventBus instances — one for subscribe loop (long-lived),
    one for publish / short-lived wait operations.
    Redis pubsub connections can't share subscribe and publish on the same connection.
    """

    def __init__(self, redis_url: str = REDIS_URL):
        self._redis_url = redis_url
        self.sub_bus: GroupEventBus | None = None
        self.pub_bus: GroupEventBus | None = None

    async def connect(self) -> None:
        self.sub_bus = GroupEventBus(self._redis_url)
        self.pub_bus = GroupEventBus(self._redis_url)
        await self.sub_bus.connect()
        await self.pub_bus.connect()

    async def disconnect(self) -> None:
        if self.sub_bus:
            await self.sub_bus.disconnect()
        if self.pub_bus:
            await self.pub_bus.disconnect()