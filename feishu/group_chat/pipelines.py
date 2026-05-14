"""
多 Agent 编排原语（Pipeline Primitives）。

这是 Scenario 场景构建的基础积木，提供 sequential（顺序发言）、
fanout（并行发言）、announce（单人公告）、vote（投票）等通用编排函数。

架构层级：Transport（Redis 通信）→ **Pipelines（编排原语）** → Scenarios（场景逻辑）
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .event_bus import GroupEventBusPool
    from .models import GroupSession

from .models import ConversationMessage, EMPLOYEE_CONFIG, SpeakRequest, SpeakResponse
from .prompts import build_role_context, format_history

log = logging.getLogger(__name__)

_SPEAK_TIMEOUT = 120.0


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _wait_for_responses(
    bus_pool: "GroupEventBusPool",
    session_id: str,
    employees: list[str],
    timeout: float = _SPEAK_TIMEOUT,
) -> dict[str, SpeakResponse]:
    """等待多个员工的发言响应（并发监听 Redis 频道）。

    为每个员工创建独立的 Redis 订阅者，监听 speak_resp:{session_id} 频道，
    超时未响应的员工返回 success=False 的占位响应。
    """
    import asyncio
    import json as _json
    from .event_bus import GroupEventBus

    results: dict[str, SpeakResponse] = {}

    async def _wait_one(emp: str) -> SpeakResponse | None:
        sub = GroupEventBus()
        await sub.connect()
        try:
            channel = f"speak_resp:{session_id}"
            await sub._sub.subscribe(channel)
            async for msg in sub._sub.listen():
                if msg["type"] != "message":
                    continue
                data = _json.loads(msg["data"])
                if data.get("employee") == emp:
                    return SpeakResponse(
                        session_id=data["session_id"],
                        chat_id=data["chat_id"],
                        employee=data["employee"],
                        content=data["content"],
                        success=data.get("success", True),
                    )
        except asyncio.TimeoutError:
            return SpeakResponse(
                session_id=session_id, chat_id="", employee=emp,
                content="", success=False,
            )
        finally:
            await sub.disconnect()
        return None

    if not employees:
        return results

    tasks = {emp: asyncio.create_task(_wait_one(emp)) for emp in employees}
    done, pending = await asyncio.wait(tasks.values(), timeout=timeout)

    for emp, task in tasks.items():
        if task in done and not task.cancelled():
            resp = task.result()
            if resp:
                results[emp] = resp
        else:
            task.cancel()
            emoji, name = EMPLOYEE_CONFIG.get(emp, ("👤", emp))
            results[emp] = SpeakResponse(
                session_id=session_id, chat_id="", employee=emp,
                content=f"{emoji} {name} 未能及时回应", success=False,
            )

    return results


def _append_to_history(
    session: "GroupSession",
    employee: str,
    content: str,
    visible_to: list[str] | None = None,
) -> None:
    """将员工发言追加到会话历史。

    Args:
        session: 当前群聊会话。
        employee: 发言员工 key。
        content: 发言内容。
        visible_to: 可见范围。设置后仅指定员工可看到此消息；为空则全员可见。
    """
    emoji, name = EMPLOYEE_CONFIG.get(employee, ("👤", employee))
    msg = ConversationMessage(
        id=str(uuid.uuid4()),
        session_id=session.id,
        sender=employee,
        sender_name=f"{emoji} {name}",
        content=content,
        feishu_message_id="",
        created_at=time.time(),
        role="assistant",
        visible_to=visible_to or [],
    )
    session.history.append(msg)


# ── Pipeline primitives ───────────────────────────────────────────────────────

async def sequential(
    session: "GroupSession",
    participants: list[str],
    bus_pool: "GroupEventBusPool",
    role_context_fn=None,
    visible_to: list[str] | None = None,
    timeout: float = _SPEAK_TIMEOUT,
) -> list[str]:
    """顺序发言管道：参与者按顺序依次发言，每人可看到前面所有人的回复。

    Args:
        session: 当前群聊会话（history 会被修改）。
        participants: 发言顺序的员工 key 列表。
        bus_pool: Redis 事件总线。
        role_context_fn: 自定义上下文函数 (employee, session, index) -> str。
        visible_to: 消息可见范围，为空则全员可见。
        timeout: 单人超时时间（秒）。

    Returns:
        成功发言的员工 key 列表。
    """
    completed = []
    for i, emp in enumerate(participants):
        if role_context_fn:
            role_ctx = role_context_fn(emp, session, i)
        else:
            role_ctx = build_role_context(emp, session)

        req = SpeakRequest(
            session_id=session.id,
            chat_id=session.chat_id,
            employee=emp,
            history_text=format_history(session.history, viewer=emp),
            trigger_message_id=session.trigger_message_id,
            order=i,
            role_context=role_ctx,
        )
        await bus_pool.pub_bus.publish_speak_req(req)

        responses = await _wait_for_responses(bus_pool, session.id, [emp], timeout=timeout)
        resp = responses.get(emp)
        if resp and resp.success:
            _append_to_history(session, emp, resp.content, visible_to=visible_to)
        completed.append(emp)

    return completed


async def fanout(
    session: "GroupSession",
    participants: list[str],
    bus_pool: "GroupEventBusPool",
    role_context_fn=None,
    timeout: float = _SPEAK_TIMEOUT,
) -> dict[str, SpeakResponse]:
    """并行发言管道：所有参与者同时发言，看到相同的历史记录（互相不可见新回复）。

    Args:
        session: 当前群聊会话。
        participants: 参与发言的员工 key 列表（同时发言）。
        bus_pool: Redis 事件总线。
        role_context_fn: 自定义上下文函数 (employee, session, index) -> str。
        timeout: 总超时时间（秒）。

    Returns:
        员工 key → SpeakResponse 的字典。
    """
    history_text = format_history(session.history)

    for i, emp in enumerate(participants):
        if role_context_fn:
            role_ctx = role_context_fn(emp, session, i)
        else:
            role_ctx = build_role_context(emp, session)

        req = SpeakRequest(
            session_id=session.id,
            chat_id=session.chat_id,
            employee=emp,
            history_text=history_text,
            trigger_message_id=session.trigger_message_id,
            order=i,
            role_context=role_ctx,
        )
        await bus_pool.pub_bus.publish_speak_req(req)

    responses = await _wait_for_responses(bus_pool, session.id, participants, timeout=timeout)
    for emp in participants:
        resp = responses.get(emp)
        if resp and resp.success:
            _append_to_history(session, emp, resp.content)

    return responses


async def announce(
    session: "GroupSession",
    speaker: str,
    bus_pool: "GroupEventBusPool",
    context: str,
    visible_to: list[str] | None = None,
    viewer: str = "",
    timeout: float = 60.0,
) -> str | None:
    """单人公告：指定一名员工发言（如主持人宣布规则、结果等）。

    Args:
        session: 当前群聊会话。
        speaker: 发言者员工 key。
        bus_pool: Redis 事件总线。
        context: 给发言者的角色指令/上下文。
        visible_to: 该发言的可见范围，为空则全员可见。
        viewer: 发言者能看到的历史范围（用于私密阶段过滤 history）。
        timeout: 超时时间（秒）。

    Returns:
        发言内容字符串，失败返回 None。
    """
    req = SpeakRequest(
        session_id=session.id,
        chat_id=session.chat_id,
        employee=speaker,
        history_text=format_history(session.history, viewer=viewer or speaker),
        trigger_message_id=session.trigger_message_id,
        order=-1,
        role_context=context,
    )
    await bus_pool.pub_bus.publish_speak_req(req)

    responses = await _wait_for_responses(bus_pool, session.id, [speaker], timeout=timeout)
    resp = responses.get(speaker)
    if resp and resp.success:
        _append_to_history(session, speaker, resp.content, visible_to=visible_to)
        return resp.content
    return None


async def vote(
    session: "GroupSession",
    voters: list[str],
    candidates: list[str],
    bus_pool: "GroupEventBusPool",
    context_template: str = "",
    visible_to: list[str] | None = None,
    timeout: float = _SPEAK_TIMEOUT,
) -> tuple[str | None, dict[str, str]]:
    """投票原语：所有投票人并行发言，从回复中提取【投票:目标】并计票。

    通过正则从每人的回复中解析投票目标，取得票最多者为结果。
    平票时返回 None。

    Args:
        session: 当前群聊会话。
        voters: 参与投票的员工 key 列表。
        candidates: 合法投票目标的员工 key 列表。
        bus_pool: Redis 事件总线。
        context_template: 投票提示模板，可用 {candidates} 和 {voter} 占位符。
        visible_to: 投票消息的可见范围。
        timeout: 超时时间（秒）。

    Returns:
        (得票最多者或None, {投票人: 投票目标} 字典)。
    """
    import re as _re
    from collections import Counter

    candidate_names = ", ".join(candidates)

    def _vote_context(emp, sess, i):
        return context_template.format(
            candidates=candidate_names, voter=emp,
        ) if context_template else (
            f"现在是投票环节。请从以下候选人中选择一人投票：{candidate_names}\n"
            f"在你的回复最后必须写上【投票:某人的key】（如【投票:algorithm】）。\n"
            f"只能选一人，简短说明理由后投票。30字以内。"
        )

    # Use fanout — all vote concurrently seeing the same history
    responses = await fanout(
        session, voters, bus_pool,
        role_context_fn=_vote_context,
        timeout=timeout,
    )

    # Extract votes from responses
    votes: dict[str, str] = {}
    for emp in voters:
        resp = responses.get(emp)
        if resp and resp.success:
            # Parse 【投票:xxx】
            m = _re.search(r"【投票[:：](\w+)】", resp.content)
            if m and m.group(1) in candidates:
                votes[emp] = m.group(1)

    # Tally
    if not votes:
        return None, votes

    counter = Counter(votes.values())
    top = counter.most_common(2)
    if len(top) == 1 or top[0][1] > top[1][1]:
        return top[0][0], votes
    return None, votes  # tie
