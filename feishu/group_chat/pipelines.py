"""
Pipeline primitives for multi-agent orchestration.

These are the building blocks that Scenarios compose to define
complex interaction flows (games, debates, brainstorms, etc.).

Layer 2 in the architecture: Transport (Redis) → **Pipelines** → Scenarios
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
    """Wait for speak responses from multiple employees concurrently."""
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
    """Append an employee's message to session history.

    Args:
        visible_to: If set, only these employees can see this message.
                    None/empty = visible to all (public).
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
    """Sequential pipeline: each participant speaks in order, seeing prior replies.

    Args:
        session: Current group session (history will be mutated).
        participants: Employee keys in speaking order.
        bus_pool: Event bus for pub/sub.
        role_context_fn: Optional (employee, session, index) -> str for custom context.
        visible_to: If set, all messages in this pipeline are only visible to these people.
        timeout: Per-participant timeout.

    Returns:
        List of employees who successfully spoke.
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
    """Fanout pipeline: all participants speak concurrently, seeing the same history.

    Args:
        session: Current group session.
        participants: Employee keys (all speak at once).
        bus_pool: Event bus.
        role_context_fn: Optional (employee, session, index) -> str.
        timeout: Total timeout for all responses.

    Returns:
        Dict of employee -> SpeakResponse.
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
    """Single speaker announcement (e.g., host declares rules or results).

    Args:
        session: Current group session.
        speaker: Employee key of the announcer.
        bus_pool: Event bus.
        context: Role context / instruction for the speaker.
        visible_to: If set, the response is only visible to these employees.
        viewer: If set, filter history shown to speaker (for private phases).
        timeout: Timeout for this single response.

    Returns:
        The speaker's response content, or None if failed.
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
    """Voting primitive: collect votes from all voters in parallel.

    Each voter's response is parsed for 【投票:target】pattern.

    Args:
        session: Current group session.
        voters: Employee keys who vote.
        candidates: Valid vote targets (employee keys).
        bus_pool: Event bus.
        context_template: Role context for voters (should instruct them to vote).
        visible_to: Visibility of vote messages in history.
        timeout: Timeout.

    Returns:
        Tuple of (winner_or_None, votes_dict).
        winner is None if tie.
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
