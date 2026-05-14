"""
Group Orchestrator — LangGraph StateGraph that manages group chat sessions.

Section V of doc/design/group-chat-redesign.md.

Graph: START → receive → decide → dispatch → conclude → END

The dispatch_node handles both sequential and parallel modes internally.
Each session runs as a separate asyncio Task, with the graph checkpointed
via AsyncPostgresSaver for cross-process durability.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from functools import partial
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

load_dotenv()

from agents_v2.shared.claude_client import make_langchain_llm
from agents_v2.shared.db import async_checkpointer_ctx

from .event_bus import GroupEventBusPool
from .models import (
    ConversationMessage, EMPLOYEE_CONFIG,
    GroupSession, MessageEvent, OrchestratorDecision,
    ROLE_DESCRIPTIONS, SessionRole, SpeakRequest,
)
from .pipelines import (
    sequential as pipe_sequential,
    fanout as pipe_fanout,
    _append_to_history,
    _wait_for_responses,
)
from . import prompts as _p  # 模块级引用，支持 watchdog 热重载后自动使用新值
from .scenarios import SCENARIO_REGISTRY
from .session import SessionStore

log = logging.getLogger("feishu.group_chat.orchestrator")



# ── State ─────────────────────────────────────────────────────────────────────

class OrchestratorState(TypedDict):
    session_json: str       # serialized GroupSession
    event_json: str         # serialized MessageEvent
    decision_json: str      # serialized OrchestratorDecision
    completed: list[str]    # employees who have spoken this round
    summary: str
    error: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _state_get_session(state: OrchestratorState) -> GroupSession:
    from .session import SessionStore
    ss = SessionStore.__new__(SessionStore)
    return ss._deserialize(json.loads(state["session_json"]))


def _state_get_event(state: OrchestratorState) -> MessageEvent:
    data = json.loads(state["event_json"])
    return MessageEvent(
        message_id=data["message_id"],
        chat_id=data["chat_id"],
        sender=data["sender"],
        text=data["text"],
        image_base64=data.get("image_base64", ""),
        mentions=data.get("mentions", []),
    )


def _state_get_decision(state: OrchestratorState) -> OrchestratorDecision | None:
    raw = state.get("decision_json", "")
    if not raw:
        return None
    data = json.loads(raw)
    return OrchestratorDecision(
        mode=data["mode"],
        participants=data.get("participants", []),
        reason=data.get("reason", ""),
    )


def _state_set_session(state: OrchestratorState, session: GroupSession) -> dict:
    from .session import SessionStore
    ss = SessionStore.__new__(SessionStore)
    return {"session_json": json.dumps(ss._serialize(session), ensure_ascii=False)}




# ── Graph Nodes ───────────────────────────────────────────────────────────────

async def _receive_node(
    state: OrchestratorState,
    session_store: SessionStore,
    bus_pool: GroupEventBusPool,
) -> dict:
    """Load or create session, append the triggering message to history."""
    event = _state_get_event(state)
    session = _state_get_session(state)

    if not session.id:
        # New session: try to find active session in this group
        active = await session_store.find_active(event.chat_id)
        if active and active.status != "done":
            session = active
        else:
            session = GroupSession(
                id=event.message_id or str(uuid.uuid4()),
                chat_id=event.chat_id,
                trigger_message_id=event.message_id,
                created_at=time.time(),
            )

    # Append user message to history
    msg = ConversationMessage(
        id=str(uuid.uuid4()),
        session_id=session.id,
        sender="user",
        sender_name="用户",
        content=event.text or "[图片]",
        feishu_message_id=event.message_id,
        created_at=time.time(),
        role="user",
    )
    session.history.append(msg)
    session.trigger_message_id = event.message_id

    log.info("receive_node: session=%s chat=%s history_len=%d",
             session.id, session.chat_id, len(session.history))

    return _state_set_session(state, session)


async def _decide_node(
    state: OrchestratorState,
    session_store: SessionStore,
    bus_pool: GroupEventBusPool,
) -> dict:
    """LLM call to decide: mode, participants, and optionally session roles."""
    session = _state_get_session(state)
    event = _state_get_event(state)

    llm_haiku = make_langchain_llm("claude-haiku-4-5-20251001")

    # Step 1: Check for explicit role assignment
    explicit_roles = await _p.extract_explicit_roles(
        event.text, event.mentions, llm_haiku,
    )
    if explicit_roles:
        session.role_assignments = explicit_roles
        session.role_history.append((time.time(), dict(explicit_roles)))
        log.info("decide_node: explicit roles from user: %s", list(explicit_roles.keys()))

    # Step 2: Decide mode and participants
    resp = await llm_haiku.ainvoke([
        SystemMessage(_p.DECIDE_PROMPT),
        HumanMessage(event.text),
    ])

    import re as _re
    try:
        m = _re.search(r"\{.*\}", resp.content, _re.DOTALL)
        data = json.loads(m.group()) if m else {}
    except Exception:
        data = {}

    decision = OrchestratorDecision(
        mode=data.get("mode", "single"),
        participants=data.get("participants", []),
        reason=data.get("reason", ""),
    )

    # Default: if no participants, route to project_manager
    if not decision.participants and decision.mode != "ignore":
        decision.participants = ["project_manager"]

    # 过滤掉 LLM 幻觉出的无效 key（仅在 EMPLOYEE_CONFIG 非空时过滤，避免 registry 未 warmup 误删）
    if len(EMPLOYEE_CONFIG) > 0:
        decision.participants = [e for e in decision.participants if e in EMPLOYEE_CONFIG]
    if not decision.participants and decision.mode != "ignore":
        decision.participants = ["project_manager"]

    session.mode = decision.mode
    session.participants = decision.participants
    session.pending = list(decision.participants)

    # Step 3: Auto-assign roles if no explicit roles and not "free"
    if not session.role_assignments and decision.mode != "ignore":
        # Use ROLE_DECIDE_PROMPT for complex modes
        if decision.mode in ("sequential", "parallel") and len(decision.participants) > 1:
            try:
                # 过滤掉 LLM 幻觉出的无效 key，只保留 EMPLOYEE_CONFIG 里有的
                valid_participants = [e for e in decision.participants if e in EMPLOYEE_CONFIG]
                if not valid_participants:
                    # registry 未 warmup，跳过角色分配
                    raise RuntimeError("EMPLOYEE_CONFIG empty, registry not warmed up")
                participant_list = "\n".join(
                    f"- {e}: {EMPLOYEE_CONFIG.get(e, ('👤', e))[0]} {EMPLOYEE_CONFIG.get(e, ('👤', e))[1]} ({ROLE_DESCRIPTIONS.get(e, '')})"
                    for e in valid_participants
                )
                role_resp = await llm_haiku.ainvoke([
                    SystemMessage(_p.ROLE_DECIDE_PROMPT),
                    HumanMessage(f"参与者列表：\n{participant_list}\n\n话题：{event.text}"),
                ])
                m2 = _re.search(r"\{.*\}", role_resp.content, _re.DOTALL)
                if m2:
                    role_data = json.loads(m2.group())
                    session.template = role_data.get("template", "free")
                    session.activity_rules = role_data.get("activity_rules", "") or ""
                    session.host = role_data.get("host", "") or ""
                    roles_dict = role_data.get("roles", {})
                    for emp_key, rdata in roles_dict.items():
                        if emp_key in EMPLOYEE_CONFIG and isinstance(rdata, dict):
                            session.role_assignments[emp_key] = SessionRole(
                                employee=emp_key,
                                role_name=rdata.get("role_name", ""),
                                role_desc=rdata.get("role_desc", ""),
                                visible_to=rdata.get("visible_to", []),
                                faction=rdata.get("faction", ""),
                            )
                    if session.activity_rules:
                        log.info("decide_node: activity_rules=%s host=%s",
                                 session.activity_rules[:80], session.host)

                    # Initialize game state via scenario registry
                    scenario_cls = SCENARIO_REGISTRY.get(session.template)
                    if scenario_cls and session.host and not session.game_state:
                        # 游戏场景自动加入 CEO（真实用户）
                        if "user" not in session.participants:
                            session.participants.append("user")
                            session.pending.append("user")
                            decision.participants.append("user")
                        scenario = scenario_cls(session)
                        session.game_state = scenario.initialize(session.activity_rules)
                        log.info("decide_node: scenario=%s state=%s (user joined)",
                                 session.template, session.game_state)
            except Exception as exc:
                log.warning("decide_node: role assignment failed: %s", exc)

    await session_store.save(session)

    log.info("decide_node: mode=%s participants=%s reason=%s",
             decision.mode, decision.participants, decision.reason)

    return {
        **_state_set_session(state, session),
        "decision_json": json.dumps({
            "mode": decision.mode,
            "participants": decision.participants,
            "reason": decision.reason,
        }, ensure_ascii=False),
    }


async def _dispatch_node(
    state: OrchestratorState,
    session_store: SessionStore,
    bus_pool: GroupEventBusPool,
) -> dict:
    """Dispatch to registered Scenario or generic pipelines."""
    session = _state_get_session(state)
    decision = _state_get_decision(state)
    completed = list(state.get("completed", []))

    if decision is None or decision.mode == "ignore":
        return {}

    # Check if this session has a registered scenario
    scenario_cls = SCENARIO_REGISTRY.get(session.template)
    if scenario_cls and session.game_state:
        # Delegate entirely to the scenario's run() method
        scenario = scenario_cls(session)
        await scenario.run(bus_pool)
        completed = list(decision.participants)
    else:
        # Generic pipeline dispatch (no game logic)
        remaining = [e for e in decision.participants if e not in completed]
        if not remaining:
            return {"completed": completed}

        mode = decision.mode
        if mode == "single":
            await pipe_sequential(session, [remaining[0]], bus_pool)
            completed.append(remaining[0])
        elif mode == "sequential":
            spoken = await pipe_sequential(session, remaining, bus_pool)
            completed.extend(spoken)
        elif mode == "parallel":
            await pipe_fanout(session, remaining, bus_pool)
            completed.extend(remaining)

    await session_store.save(session)

    return {
        **_state_set_session(state, session),
        "completed": completed,
    }


async def _conclude_node(
    state: OrchestratorState,
    session_store: SessionStore,
    bus_pool: GroupEventBusPool,
) -> dict:
    """Generate summary and publish to group via project_manager."""
    session = _state_get_session(state)
    decision = _state_get_decision(state)

    if decision is None or decision.mode == "ignore":
        return {}

    # Only summarize if more than 1 participant spoke
    if len(state.get("completed", [])) <= 1 and decision.mode == "single":
        session.status = "done"
        await session_store.delete(session.id)
        return _state_set_session(state, session)

    history_text = _p.format_history(session.history)

    # Pick the host (or fall back to project_manager) to do the summary
    summarizer = session.host if session.host in EMPLOYEE_CONFIG else "project_manager"

    req = SpeakRequest(
        session_id=session.id,
        chat_id=session.chat_id,
        employee=summarizer,
        history_text=history_text,
        trigger_message_id=session.trigger_message_id,
        summary_mode=True,
        role_context=_p.build_summary_prompt(session),
    )
    await bus_pool.pub_bus.publish_speak_req(req)

    responses = await _wait_for_responses(bus_pool, session.id, [summarizer], timeout=60.0)
    resp = responses.get(summarizer)
    if resp and resp.success:
        session.summary = resp.content
        _append_to_history(session, summarizer, resp.content)

    session.status = "done"
    await session_store.delete(session.id)

    log.info("conclude_node: session=%s done, summary_len=%d", session.id, len(session.summary))

    return _state_set_session(state, session)




# ── Conditional edge ──────────────────────────────────────────────────────────

def _after_decide(state: OrchestratorState) -> Literal["dispatch", "conclude"]:
    decision = _state_get_decision(state)
    if decision is None or decision.mode == "ignore":
        return "conclude"
    return "dispatch"


# ── Build graph ───────────────────────────────────────────────────────────────

def _build_graph(session_store: SessionStore, bus_pool: GroupEventBusPool, cp):
    """Compile the orchestrator LangGraph graph with an already-open checkpointer."""
    g = StateGraph(OrchestratorState)

    g.add_node("receive", partial(
        _receive_node, session_store=session_store, bus_pool=bus_pool,
    ))
    g.add_node("decide", partial(
        _decide_node, session_store=session_store, bus_pool=bus_pool,
    ))
    g.add_node("dispatch", partial(
        _dispatch_node, session_store=session_store, bus_pool=bus_pool,
    ))
    g.add_node("conclude", partial(
        _conclude_node, session_store=session_store, bus_pool=bus_pool,
    ))

    g.add_edge(START, "receive")
    g.add_edge("receive", "decide")
    g.add_conditional_edges("decide", _after_decide, {
        "dispatch": "dispatch",
        "conclude": "conclude",
    })
    g.add_edge("dispatch", "conclude")
    g.add_edge("conclude", END)

    return g.compile(checkpointer=cp)


# ── Main orchestrator process ─────────────────────────────────────────────────

async def run_orchestrator():
    """Main entry point for the orchestrator process."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
    )

    log.info("Starting GroupOrchestrator...")

    # ── 热更新信号处理 ────────────────────────────────────────────────────────
    # SIGUSR1：reload prompts / pipelines / scenarios（文件级热更，守护进程自动触发）
    # SIGUSR2：eventbus 重连（scripts/reload.py eventbus 触发）
    import signal as _signal
    import importlib as _importlib
    from feishu.group_chat.scenarios import reload_all as _reload_scenarios
    from feishu.group_chat import pipelines as _pipelines_mod

    _reconnect_flag = False

    def _on_usr1(sig, frame):
        """热重载 prompts / pipelines / scenarios。"""
        try:
            _importlib.reload(_p)
            _importlib.reload(_pipelines_mod)
            _reload_scenarios()
            log.info("🔄 SIGUSR1: prompts + pipelines + scenarios reloaded")
        except Exception as exc:
            log.warning("SIGUSR1 reload error: %s", exc)

    def _on_usr2(sig, frame):
        """标记 eventbus 需要重连（下一个消息循环迭代时执行）。"""
        nonlocal _reconnect_flag
        _reconnect_flag = True
        log.info("🔌 SIGUSR2: eventbus reconnect scheduled")

    _signal.signal(_signal.SIGUSR1, _on_usr1)
    _signal.signal(_signal.SIGUSR2, _on_usr2)

    # 预热员工配置注册表（否则 EMPLOYEE_CONFIG 在 async 上下文返回空字典）
    from backend.services import registry as _registry
    await _registry.warmup()
    log.info("Registry warmed up: %d employees", len(list(_registry.list_keys_sync_cached())))

    session_store = SessionStore()
    await session_store.connect()

    bus_pool = GroupEventBusPool()
    await bus_pool.connect()

    async with async_checkpointer_ctx() as cp:
        graph = _build_graph(session_store, bus_pool, cp)
        log.info("Orchestrator graph compiled with checkpointer")

        # Track active tasks per chat to prevent duplicate games
        active_chat_tasks: dict[str, asyncio.Task] = {}

        async def handle_event(event: MessageEvent):
            """Process a group message event."""
            log.info("received group_msg chat=%s text=%.60s", event.chat_id, event.text)

            # Skip empty messages
            if not event.text and not event.image_base64:
                return

            # Create initial state
            from .session import SessionStore as SS
            ss = SS.__new__(SS)
            empty_session = GroupSession(chat_id=event.chat_id)
            initial_state: OrchestratorState = {
                "session_json": json.dumps(ss._serialize(empty_session), ensure_ascii=False),
                "event_json": json.dumps({
                    "message_id": event.message_id,
                    "chat_id": event.chat_id,
                    "sender": event.sender,
                    "text": event.text,
                    "image_base64": event.image_base64,
                    "mentions": event.mentions,
                }, ensure_ascii=False),
                "decision_json": "",
                "completed": [],
                "summary": "",
                "error": "",
            }

            config = {"configurable": {"thread_id": event.message_id}}

            try:
                async for chunk in graph.astream(initial_state, config=config, stream_mode="updates"):
                    for node_name, node_out in chunk.items():
                        log.debug("graph node=%s completed", node_name)
            except Exception as exc:
                log.error("graph execution failed for chat=%s: %s", event.chat_id, exc)

        async def _run_subscribe_loop():
            """订阅循环，支持 SIGUSR2 触发重连。"""
            nonlocal _reconnect_flag
            while True:
                _reconnect_flag = False
                try:
                    async for event in bus_pool.sub_bus.subscribe_group_pattern():
                        if _reconnect_flag:
                            # eventbus 热更：重连 Redis 订阅
                            log.info("reconnecting eventbus...")
                            await bus_pool.disconnect()
                            _importlib.reload(
                                _importlib.import_module("feishu.group_chat.event_bus")
                            )
                            await bus_pool.connect()
                            log.info("eventbus reconnected ✅")
                            break  # 跳出内层循环，重新订阅

                        existing = active_chat_tasks.get(event.chat_id)
                        if existing and not existing.done():
                            # 游戏进行中 → 转发用户消息给场景消费（而非丢弃）
                            await bus_pool.pub_bus.publish_user_input(
                                event.chat_id, event.text, event.message_id,
                                sender=event.sender,
                            )
                            log.info("forwarded user_input to active session chat=%s",
                                     event.chat_id)
                            continue

                        task = asyncio.create_task(handle_event(event))
                        active_chat_tasks[event.chat_id] = task
                        done = [cid for cid, t in active_chat_tasks.items() if t.done()]
                        for cid in done:
                            del active_chat_tasks[cid]
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error("subscribe loop error: %s, retrying in 2s...", exc)
                    await asyncio.sleep(2)
                else:
                    if not _reconnect_flag:
                        break  # 正常退出
        try:
            await _run_subscribe_loop()
        except asyncio.CancelledError:
            log.info("Orchestrator shutting down...")
        finally:
            await bus_pool.disconnect()
            await session_store.disconnect()


if __name__ == "__main__":
    asyncio.run(run_orchestrator())