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
    add_participant,
)
from . import prompts as _p  # 模块级引用，支持 watchdog 热重载后自动使用新值
from .scenarios import SCENARIO_REGISTRY
from .session import SessionStore
from .task_steps import (
    create_tasks_from_decompose,
    extract_task_id,
    is_task_chat,
    mark_task_status,
    step_record,
)

log = logging.getLogger("group_chat.orchestrator")



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


# Task 触发场景的关键词识别(MVP 实现,B2 阶段可换 LLM intent classifier)
_ROBOT_ENG_KEYWORDS = (
    "机器狗", "四足", "腿部", "髋关节", "膝关节", "舵机",
    "STEP 文件", "step 文件", "PRD", "build123d",
)


def _match_robot_engineering(text: str) -> bool:
    if not text:
        return False
    return any(kw in text for kw in _ROBOT_ENG_KEYWORDS)




# ── Graph Nodes ───────────────────────────────────────────────────────────────

async def _receive_node(
    state: OrchestratorState,
    session_store: SessionStore,
    bus_pool: GroupEventBusPool,
) -> dict:
    """Load or create session, append the triggering message to history."""
    event = _state_get_event(state)
    task_id = extract_task_id(event.chat_id)

    # Task 触发场景:推进 pending → in_progress(幂等)
    if task_id:
        await mark_task_status(task_id, "in_progress")

    async with step_record(task_id, "receive", input_summary=event.text[:200]):
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
            platform_message_id=event.message_id,
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
    task_id = extract_task_id(event.chat_id)

    # Task 触发场景:关键词命中后跳过 LLM 决策,固定走 robot_engineering scenario
    if task_id and _match_robot_engineering(event.text):
        async with step_record(task_id, "decide",
                               input_summary=f"[scenario=robot_engineering] {event.text[:200]}"):
            session.template = "robot_engineering"
            session.host = "project_manager"
            participants = ["product_manager", "mechanical", "firmware", "algorithm", "cost"]
            # 过滤掉 registry 里没的 key(测试环境可能 EMPLOYEE_CONFIG 为空)
            if len(EMPLOYEE_CONFIG) > 0:
                participants = [e for e in participants if e in EMPLOYEE_CONFIG]
            session.mode = "parallel"
            session.participants = participants
            session.pending = list(participants)
            scenario_cls = SCENARIO_REGISTRY.get("robot_engineering")
            if scenario_cls:
                scenario = scenario_cls(session, session_store=session_store)
                session.game_state = scenario.initialize(event.text) or {"phase": "init"}
            else:
                session.game_state = {"phase": "init"}
            await session_store.save(session)
            log.info("decide_node: task scenario=robot_engineering participants=%s", participants)
            return {
                **_state_set_session(state, session),
                "decision_json": json.dumps({
                    "mode": "parallel",
                    "participants": participants,
                    "reason": "matched robot_engineering scenario",
                }, ensure_ascii=False),
            }

    # Fast path (RFC feishu-cli-direct Phase 3):
    # employee_bot 已经把 mentions 解析成 [@key] tags 注入到 text。
    # 解析这些 tags 直接路由,免跑 2-3 次 Haiku LLM(省 4-5s)。
    # 没命中再 fallback LLM 决策。
    import re as _re_fast
    fast_tags = _re_fast.findall(r"\[@([a-z_]+)\]", event.text or "")
    fast_valid = [t for t in fast_tags
                  if (len(EMPLOYEE_CONFIG) == 0 or t in EMPLOYEE_CONFIG)
                  and t != "user"]
    is_all_marker = "[全员]" in (event.text or "")
    # 接龙关键词:用户期望按顺序每人各发一条消息(纯群聊,不写共享文件)
    # 命中 → sequential 模式,每人收到前一个同事的发言再接力
    text_lower = (event.text or "")
    is_relay = any(kw in text_lower for kw in
                   ("接龙", "接力", "轮流", "依次", "按顺序发言", "排队发言"))
    # 共享文档并发编辑:全员同时改同一文件,每人改自己 section,flock 防冲突
    is_concurrent_doc = any(kw in text_lower for kw in
                            ("共享文档", "共编", "同写", "共同编辑",
                             "协同编辑", "并发编辑", "同时编辑"))

    if is_all_marker or fast_valid or is_relay or is_concurrent_doc:
        async with step_record(task_id, "decide",
                               input_summary=f"[fast-path] tags={fast_valid} all={is_all_marker} relay={is_relay} concurrent={is_concurrent_doc} text={event.text[:120]}"):
            if is_concurrent_doc:
                # 共享文档并发编辑: 走 scenario, fanout 真并发
                participants = list(EMPLOYEE_CONFIG.keys()) if EMPLOYEE_CONFIG else (fast_valid or [])
                participants = [p for p in participants if p != "user"]
                session.template = "concurrent_doc_edit"
                session.host = "project_manager"
                session.mode = "parallel"
                session.participants = participants
                session.pending = list(participants)
                # 把用户原文当 activity_rules 传给 scenario.initialize
                scenario_cls = SCENARIO_REGISTRY.get("concurrent_doc_edit")
                if scenario_cls:
                    scenario = scenario_cls(session, session_store=session_store)
                    session.game_state = scenario.initialize(event.text or "") or {}
                await session_store.save(session)
                log.info("decide_node[fast]: scenario=concurrent_doc_edit participants=%s",
                         participants)
                return {
                    **_state_set_session(state, session),
                    "decision_json": json.dumps({
                        "mode": "parallel",
                        "participants": participants,
                        "reason": "concurrent_doc keyword → scenario fanout",
                    }, ensure_ascii=False),
                }
            if is_relay:
                # 接龙优先匹配: 全员 sequential
                participants = list(EMPLOYEE_CONFIG.keys()) if EMPLOYEE_CONFIG else (fast_valid or [])
                participants = [p for p in participants if p != "user"]
                mode = "sequential"
                reason = "relay keyword → all employees sequential"
            elif is_all_marker:
                participants = list(EMPLOYEE_CONFIG.keys()) if EMPLOYEE_CONFIG else fast_valid
                # 排除 user
                participants = [p for p in participants if p != "user"]
                mode = "parallel"
                reason = "[全员] marker → all employees parallel"
            elif len(fast_valid) == 1:
                participants = fast_valid
                mode = "single"
                reason = f"[@{fast_valid[0]}] explicit single mention"
            else:
                participants = fast_valid
                mode = "parallel"
                reason = f"explicit multi mentions {fast_valid}"

            session.mode = mode
            session.participants = participants
            session.pending = list(participants)
            await session_store.save(session)
            log.info("decide_node[fast]: mode=%s participants=%s reason=%s",
                     mode, participants, reason)
            return {
                **_state_set_session(state, session),
                "decision_json": json.dumps({
                    "mode": mode,
                    "participants": participants,
                    "reason": reason,
                }, ensure_ascii=False),
            }

    async with step_record(task_id, "decide", input_summary=event.text[:200]):
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
                                await add_participant(session, session_store, "user")
                                session.pending.append("user")
                                decision.participants.append("user")
                            scenario = scenario_cls(session, session_store=session_store)
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
    task_id = extract_task_id(session.chat_id)

    if decision is None or decision.mode == "ignore":
        return {}

    async with step_record(
        task_id, "dispatch",
        input_summary=f"mode={decision.mode} participants={decision.participants}",
    ):
        # Check if this session has a registered scenario
        scenario_cls = SCENARIO_REGISTRY.get(session.template)
        if scenario_cls and session.game_state:
            # Delegate entirely to the scenario's run() method
            scenario = scenario_cls(session, session_store=session_store)
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
    task_id = extract_task_id(session.chat_id)
    is_task = is_task_chat(session.chat_id)

    if decision is None or decision.mode == "ignore":
        return {}

    async with step_record(
        task_id, "conclude",
        input_summary=f"completed={state.get('completed', [])}",
    ):
        # Only summarize if more than 1 participant spoke
        if len(state.get("completed", [])) <= 1 and decision.mode == "single":
            session.status = "done"
            # Task 触发场景下保留 session 用于后续审计;群聊场景沿用旧逻辑
            if not is_task:
                await session_store.delete(session.id)
            result = _state_set_session(state, session)
        else:
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

            # 将本次会话摘要写入所有参与员工的长期记忆
            try:
                from backend.repos import memory_repo
                await memory_repo.save_session_summary(session)
            except Exception as _mem_err:
                log.warning("conclude_node: memory save failed session=%s err=%s",
                            session.id[:8], _mem_err)

            session.status = "done"
            if not is_task:
                await session_store.delete(session.id)

            log.info("conclude_node: session=%s done, summary_len=%d task_id=%s",
                     session.id, len(session.summary), task_id)

            result = _state_set_session(state, session)

    # Task 触发场景:step_record 出栈后推进 in_progress → done
    if task_id:
        await mark_task_status(task_id, "done")

    return result


# ── Execute node:把会议 summary 拆成可执行 task,派给员工真干活 ──────────────

async def _execute_node(
    state: OrchestratorState,
    session_store: SessionStore,
    bus_pool: GroupEventBusPool,
) -> dict:
    """会议结束后,让 PM 把 summary 拆成 task list → 落库 → 异步派单到员工 cc_bridge。

    设计要点:
    - fire-and-forget 派单:不阻塞 graph,每个 dispatch 自己负责更新 task.status
    - 闲聊兜底:summary 太短或 LLM 拆出空 list 时直接返回,不浪费 cc_bridge
    - 防重:session.executed_tasks 已有内容时跳过(orchestrator 同一 chat 的多轮消息会复用 session)
    - 派单走 handle_dispatch(已有的 A2A 调用)→ smart_graph._cc_work_node → ClaudeRunner
    """
    session = _state_get_session(state)
    decision = _state_get_decision(state)

    if decision is None or decision.mode == "ignore":
        return {}

    # 跳过条件 1:会议没产生 summary(单人模式或异常)
    summary = (session.summary or "").strip()
    if len(summary) < 50:
        log.info("execute_node: skip — summary too short (len=%d)", len(summary))
        return {}

    # 跳过条件 2:本场会议已经派过单(防止 orchestrator 多轮触发重复派)
    already_executed = getattr(session, "executed_tasks", None) or []
    if already_executed:
        log.info("execute_node: skip — session already executed %d tasks",
                 len(already_executed))
        return {}

    # ── 1. PM 拆解 summary(走 cli Opus 4.7,自动进池化复用) ────────────────
    # 为什么走 cli 而不是 langchain API:
    #   - 经过 lumos 代理的 langchain 路径偶发 404/超时,影响整个 _execute_node
    #   - cli 走本地账户认证,稳定;Opus 4.7 推理质量明显优于 Haiku(更不会乱拆)
    #   - cli 自然进 ClaudePool,跨多次会议复用同一 PM 子进程,省冷启
    history_compact = _p.format_history(session.history)[-8000:]
    pm_prompt = (
        f"{_p.EXECUTE_DECOMPOSE_PROMPT}\n\n"
        f"---\n\n"
        f"# 本场会议讨论(history)\n{history_compact}\n\n"
        f"# 会议小结(summary,芳芳已发布)\n{summary}\n\n"
        f"按上面的 EXECUTE_DECOMPOSE_PROMPT 规则拆出 task 列表,只输出 JSON 对象,"
        f"不要任何工具调用,不要 markdown 代码块包裹,不要前后说明文字。"
    )

    items: list[dict] = []
    try:
        from agents_v2.shared import cc_executor as _cc
        from backend.services import registry as _registry
        pm_cfg = _registry.get_effective_sync("project_manager")
        pm_cwd = (pm_cfg.cwd if pm_cfg else "") or "/Users/liyijiang/work/robot-dog"
        # chat_id 用 "task:pm-decompose-{session_id}" 触发池化路径
        # → pool_thread = "task_pool:project_manager",同员工跨会议复用
        pm_chat_id = f"task:pm-decompose-{session.id}"
        pm_thread_id = f"pm_decompose_{session.id}"

        text, _new_sid, _logs = await _cc.run_cc_node(
            employee_key="project_manager",
            query=pm_prompt,
            cwd=pm_cwd,
            chat_id=pm_chat_id,
            thread_id=pm_thread_id,
            feishu_app_id=(pm_cfg.feishu_app_id if pm_cfg else "") or "",
            feishu_app_secret=(pm_cfg.feishu_app_secret if pm_cfg else "") or "",
            agent_port=(pm_cfg.agent_port if pm_cfg else "") or "",
            model="claude-opus-4-7",
            effort="high",
        )
        import re as _re
        m = _re.search(r"\{.*\}", text or "", _re.DOTALL)
        data = json.loads(m.group()) if m else {}
        items = data.get("tasks", []) if isinstance(data, dict) else []
        log.info("execute_node: PM(cli opus-4-7) decomposed %d tasks", len(items))
    except Exception as exc:
        log.warning("execute_node: PM cli decompose failed err=%s — fallback to Haiku API", exc)
        # 兜底:cli 失败(沙箱/认证/账户问题)走 Haiku API,保证 _execute_node 不被一棒打死
        try:
            llm = make_langchain_llm("claude-haiku-4-5-20251001")
            resp = await llm.ainvoke([
                SystemMessage(_p.EXECUTE_DECOMPOSE_PROMPT),
                HumanMessage(pm_prompt),
            ])
            import re as _re
            m = _re.search(r"\{.*\}", resp.content, _re.DOTALL)
            data = json.loads(m.group()) if m else {}
            items = data.get("tasks", []) if isinstance(data, dict) else []
        except Exception as exc2:
            log.warning("execute_node: fallback Haiku also failed err=%s", exc2)
            items = []

    if not items:
        log.info("execute_node: PM decomposed 0 tasks (probably pure discussion), skip")
        return {}

    # ── 2. 落库 ───────────────────────────────────────────────────────────
    requester = f"group_chat:{session.chat_id[:20]}"
    task_ids = await create_tasks_from_decompose(items, requester=requester)
    if not task_ids:
        log.warning("execute_node: 0 tasks persisted (all dropped by validator)")
        return {}

    # ── 3. fire-and-forget 派单到员工 cc_bridge ───────────────────────────
    from feishu.commands.dispatch import handle_dispatch as _dispatch

    async def _dispatch_one(tid: str, item: dict) -> None:
        executor = item.get("executor", "")
        title = item.get("title", "")
        desc = item.get("description", "")
        prompt = f"# 任务 {tid[:8]}\n## {title}\n\n{desc}"
        try:
            await mark_task_status(tid, "in_progress")
            log.info("execute_node: dispatching task=%s -> %s", tid[:8], executor)
            result = await _dispatch(
                employee=executor,
                task=prompt,
                task_id=tid,
                chat_id=f"task:{tid}",
            )
            ok = bool(result.get("result"))
            await mark_task_status(tid, "done" if ok else "failed")
            log.info("execute_node: task=%s done=%s result_len=%d",
                     tid[:8], ok, len(result.get("result", "")))
        except Exception as exc:
            log.warning("execute_node: dispatch failed task=%s err=%s", tid[:8], exc)
            try:
                await mark_task_status(tid, "failed")
            except Exception:
                pass

    valid_items = [it for it in items
                   if (it.get("executor") or "") in {
                       "mechanical", "hardware", "firmware", "algorithm",
                       "testing", "cost", "product_manager",
                       "project_manager", "tech_lead",
                   } and (it.get("title") or "").strip()]
    paired = list(zip(task_ids, valid_items[:len(task_ids)]))

    for tid, item in paired:
        asyncio.create_task(_dispatch_one(tid, item))

    # 把 task_id 写到 session,顺手存盘(防重)
    try:
        session.executed_tasks = list(task_ids)  # type: ignore[attr-defined]
        await session_store.save(session)
    except Exception as exc:
        log.debug("execute_node: session.executed_tasks save best-effort: %s", exc)

    log.info("execute_node: dispatched %d tasks -> %s",
             len(paired), [(tid[:8], it.get("executor")) for tid, it in paired])

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
    g.add_node("execute", partial(
        _execute_node, session_store=session_store, bus_pool=bus_pool,
    ))

    g.add_edge(START, "receive")
    g.add_edge("receive", "decide")
    g.add_conditional_edges("decide", _after_decide, {
        "dispatch": "dispatch",
        "conclude": "conclude",
    })
    g.add_edge("dispatch", "conclude")
    g.add_edge("conclude", "execute")
    g.add_edge("execute", END)

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
    from group_chat.scenarios import reload_all as _reload_scenarios
    from group_chat import pipelines as _pipelines_mod

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
                                _importlib.import_module("group_chat.event_bus")
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