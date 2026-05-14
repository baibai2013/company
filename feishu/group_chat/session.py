"""
Redis-backed session store for group chat sessions.
Section V of doc/design/group-chat-redesign.md.
"""
import json
import logging
import time
from typing import Optional

import redis.asyncio as aioredis

from .models import ConversationMessage, GroupSession

REDIS_URL = "redis://localhost:6379/0"
SESSION_PREFIX = "group_session:"
DEFAULT_TTL = 1800  # 30 min

log = logging.getLogger("feishu.group_chat.session")


class SessionStore:
    def __init__(self, redis_url: str = REDIS_URL):
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._redis = aioredis.from_url(self._redis_url)

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.close()

    def _key(self, session_id: str) -> str:
        return f"{SESSION_PREFIX}{session_id}"

    def _serialize(self, session: GroupSession) -> dict:
        return {
            "id": session.id,
            "chat_id": session.chat_id,
            "mode": session.mode,
            "status": session.status,
            "participants": session.participants,
            "pending": session.pending,
            "history": [
                {
                    "id": m.id,
                    "session_id": m.session_id,
                    "sender": m.sender,
                    "sender_name": m.sender_name,
                    "content": m.content,
                    "feishu_message_id": m.feishu_message_id,
                    "created_at": m.created_at,
                    "role": m.role,
                    "visible_to": m.visible_to,
                }
                for m in session.history
            ],
            "trigger_message_id": session.trigger_message_id,
            "created_at": session.created_at,
            "ttl": session.ttl,
            "template": session.template,
            "activity_rules": session.activity_rules,
            "host": session.host,
            "game_state": session.game_state,
            "role_assignments": {
                emp: {
                    "employee": r.employee,
                    "role_name": r.role_name,
                    "role_desc": r.role_desc,
                    "visible_to": r.visible_to,
                    "faction": r.faction,
                }
                for emp, r in session.role_assignments.items()
            },
            "role_history": session.role_history,
            "summary": session.summary,
        }

    def _deserialize(self, data: dict) -> GroupSession:
        from .models import SessionRole

        role_assignments = {}
        for emp, rdata in data.get("role_assignments", {}).items():
            role_assignments[emp] = SessionRole(
                employee=rdata["employee"],
                role_name=rdata["role_name"],
                role_desc=rdata["role_desc"],
                visible_to=rdata.get("visible_to", []),
                faction=rdata.get("faction", ""),
            )

        return GroupSession(
            id=data["id"],
            chat_id=data["chat_id"],
            mode=data.get("mode", "single"),
            status=data.get("status", "active"),
            participants=data.get("participants", []),
            pending=data.get("pending", []),
            history=[
                ConversationMessage(
                    id=m["id"],
                    session_id=m["session_id"],
                    sender=m["sender"],
                    sender_name=m["sender_name"],
                    content=m["content"],
                    feishu_message_id=m["feishu_message_id"],
                    created_at=m["created_at"],
                    role=m.get("role", "user"),
                    visible_to=m.get("visible_to", []),
                )
                for m in data.get("history", [])
            ],
            trigger_message_id=data.get("trigger_message_id", ""),
            created_at=data.get("created_at", 0.0),
            ttl=data.get("ttl", DEFAULT_TTL),
            template=data.get("template", "free"),
            activity_rules=data.get("activity_rules", ""),
            host=data.get("host", ""),
            game_state=data.get("game_state", {}),
            role_assignments=role_assignments,
            role_history=data.get("role_history", []),
            summary=data.get("summary", ""),
        )

    async def save(self, session: GroupSession) -> None:
        key = self._key(session.id)
        data = self._serialize(session)
        await self._redis.setex(key, session.ttl, json.dumps(data, ensure_ascii=False))
        log.info("session saved: %s status=%s", session.id, session.status)

    async def load(self, session_id: str) -> GroupSession | None:
        key = self._key(session_id)
        raw = await self._redis.get(key)
        if not raw:
            return None
        try:
            return self._deserialize(json.loads(raw))
        except Exception as exc:
            log.warning("session deserialize failed: %s", exc)
            return None

    async def delete(self, session_id: str) -> None:
        key = self._key(session_id)
        await self._redis.delete(key)

    async def find_active(self, chat_id: str, within_secs: int = 300) -> GroupSession | None:
        """Find the most recent active session in a group within the time window."""
        now = time.time()
        cursor = 0
        best: GroupSession | None = None
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=f"{SESSION_PREFIX}*", count=50,
            )
            for key in keys:
                raw = await self._redis.get(key)
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    if data.get("chat_id") != chat_id:
                        continue
                    if data.get("status") == "done":
                        continue
                    created = data.get("created_at", 0)
                    if now - created > within_secs:
                        continue
                    session = self._deserialize(data)
                    if best is None or session.created_at > best.created_at:
                        best = session
                except Exception:
                    continue
            if cursor == 0:
                break
        return best