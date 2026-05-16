"""
智能路由 Agent Graph — 供所有员工复用。

路由逻辑：
  route_node  → 判断 CHAT / WORK
  CHAT        → chat_node   : 用员工人设直接对话 (< 3s)
  WORK        → plan_node   → execute_node : 全流程输出

配置：所有 LLM 调用的模型、温度、prompts 均从 registry 读取。
节点在每次执行时实时读 config，所以 DB 修改后下次调用立刻生效。
"""
import hashlib as _hashlib
import time as _time
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agents_v2.shared.claude_client import make_langchain_llm

# ── 路由缓存 ─────────────────────────────────────────────────────────────────
# 相同文本的路由结果缓存 TTL=10 分钟，最多 200 条，LRU 淘汰

_ROUTE_CACHE: dict[str, tuple[str, float]] = {}   # md5 → (route, expire_ts)
_ROUTE_CACHE_TTL = 600      # 秒，10 分钟
_ROUTE_CACHE_MAX = 200


def _route_cache_get(text: str) -> str | None:
    """返回缓存的路由结果，未命中或已过期返回 None。"""
    key = _hashlib.md5(text.strip().lower().encode()).hexdigest()
    entry = _ROUTE_CACHE.get(key)
    if entry and _time.monotonic() < entry[1]:
        return entry[0]
    return None


def _route_cache_set(text: str, route: str) -> None:
    """写入缓存，超出上限时 LRU 淘汰最旧 10 条。"""
    key = _hashlib.md5(text.strip().lower().encode()).hexdigest()
    _ROUTE_CACHE[key] = (route, _time.monotonic() + _ROUTE_CACHE_TTL)
    if len(_ROUTE_CACHE) > _ROUTE_CACHE_MAX:
        to_drop = sorted(_ROUTE_CACHE, key=lambda k: _ROUTE_CACHE[k][1])[:10]
        for k in to_drop:
            _ROUTE_CACHE.pop(k, None)


# Fallback prompts — used when registry doesn't supply a global override.

_DEFAULT_ROUTE_PROMPT = """判断下面这条消息是「闲聊」还是「工作任务」。

闲聊：问候、状态询问、随便聊聊、简单确认（如"在吗""怎么样""最近忙吗"）、询问对话历史或之前说过的内容（如"我刚才说的xxx是什么""你还记得..."）。
工作任务：包含具体设计/开发/分析/建模/验证/输出/报告/计划等技术或业务要求，需要调用工具或产出具体结果。

只回复一个词：CHAT 或 WORK，不要有其他内容。"""

_DEFAULT_CHAT_SUFFIX = "\n\n性格：务实简洁，回复不超过 150 字，用中文，自然对话，不列大纲不输出 JSON。"

_DEFAULT_PLAN_SUFFIX = "\n请分析需求，制定执行方案（100字以内）。"

# Backwards-compat aliases (some imports reference these)
_ROUTE_PROMPT = _DEFAULT_ROUTE_PROMPT
_CHAT_SUFFIX = _DEFAULT_CHAT_SUFFIX


class SmartState(TypedDict):
    messages: Annotated[list, add_messages]
    task_input: Any
    route: str
    plan: str
    execution_result: str
    cc: list


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_history(state: "SmartState", cap: int = 40) -> list:
    """动态滑动窗口：
    - 总消息数 < 100：取最近 min(total, cap) 条原始消息
    - 总消息数 ≥ 100：取最近 20 条（依赖长期记忆摘要注入 system prompt 补充远程上下文）
    """
    msgs = state.get("messages") or []
    if len(msgs) >= 100:
        return msgs[-20:]
    return msgs[-cap:]


def _text_only(task_input: Any) -> str:
    if isinstance(task_input, list):
        parts = [b.get("text", "") for b in task_input if b.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return task_input or ""


def _human_msg(task_input: Any, prefix: str = "") -> HumanMessage:
    if isinstance(task_input, list):
        content = ([{"type": "text", "text": prefix}] if prefix else []) + task_input
        return HumanMessage(content=content)
    text = f"{prefix}{task_input}" if prefix else task_input
    return HumanMessage(content=text)


def _load_config(employee_key: str):
    """Read EffectiveConfig from registry (in-memory cache only — no DB calls).

    Agent processes pre-warm the registry at startup. Calling warmup_sync() or
    any async DB path here would create a new asyncio event loop in the thread
    pool executor, which conflicts with the shared asyncpg connection pool and
    causes 'another operation is in progress' errors.
    """
    from backend.services import registry
    return registry.get_effective_sync(employee_key)


def _llm_for(employee_key: str, call_type: str, default_model: str = "claude-sonnet-4-6"):
    """Build a ChatAnthropic for a specific call_type.
    优先级：session_config.llm_calls > DB registry > default_model。
    """
    from agents_v2.shared.runner import current_session_config
    session_cfg = current_session_config.get({})  # type: ignore[call-arg]
    session_llm = session_cfg.get("llm_calls", {})
    if session_llm.get(call_type):
        c = session_llm[call_type]
        return make_langchain_llm(
            model=c.get("model") or default_model,
            temperature=c.get("temperature"),
            max_tokens=c.get("max_tokens"),
        )

    cfg = _load_config(employee_key)
    if cfg and cfg.llm_calls.get(call_type):
        c = cfg.llm_calls[call_type]
        return make_langchain_llm(
            model=c.get("model") or default_model,
            temperature=c.get("temperature"),
            max_tokens=c.get("max_tokens"),
        )
    return make_langchain_llm(default_model)


def _system_prompt_for(employee_key: str, suffix: str = "", query: str = "") -> str:
    """构建 system prompt。
    优先级：session_config.system_prompt > DB registry > ""
    追加：session_config.system_prompt_suffix > source 渠道提示 > 长期记忆 > suffix
    """
    from agents_v2.shared.runner import current_session_config
    session_cfg = current_session_config.get({})  # type: ignore[call-arg]

    # system_prompt 覆盖
    if session_cfg.get("system_prompt"):
        base = session_cfg["system_prompt"]
    else:
        cfg = _load_config(employee_key)
        base = cfg.system_prompt if cfg else ""

    # system_prompt_suffix 追加
    if session_cfg.get("system_prompt_suffix"):
        base = (base or "") + "\n\n" + session_cfg["system_prompt_suffix"]

    # source 渠道提示
    source = session_cfg.get("source", "")
    if source == "feishu_p2p":
        source_hint = "\n\n【当前为飞书单聊，回复简洁口语化，不超过200字】"
    elif source in ("feishu_group", "kanban"):
        source_hint = "\n\n【当前为群聊，回复可适当正式，注意其他人也能看到】"
    elif source == "scheduler":
        source_hint = "\n\n【当前为定时任务触发，可以输出较完整的结构化内容】"
    else:
        source_hint = ""

    try:
        from backend.repos import memory_repo
        # Always use in-memory cache (get_sync) — avoid asyncio.run() in thread
        # pool context which conflicts with the shared asyncpg connection pool.
        memories = memory_repo.get_sync(employee_key)[:5]
        if memories:
            mem_block = "\n".join(f"- {m[:200]}" for m in memories)
            base = (base or "") + f"\n\n【近期参与的讨论（供参考）】\n{mem_block}"
    except Exception:
        pass

    return (base or "") + source_hint + suffix


def _global_prompt(employee_key: str, name: str, fallback: str) -> str:
    """Read a global prompt by name from registry; fall back to a default."""
    cfg = _load_config(employee_key)
    if cfg:
        v = (cfg.global_prompts or {}).get(name)
        if v:
            return v
    return fallback


# ── Nodes ────────────────────────────────────────────────────────────────────

def _route_node(state: SmartState, employee_key: str) -> dict:
    text = _text_only(state["task_input"])
    if not text:
        return {"route": "WORK"}

    # 缓存命中：跳过 LLM
    cached = _route_cache_get(text)
    if cached:
        return {"route": cached}

    llm = _llm_for(employee_key, "route", default_model="claude-haiku-4-5-20251001")
    prompt = _global_prompt(employee_key, "route_prompt", _DEFAULT_ROUTE_PROMPT)
    resp = llm.invoke([SystemMessage(prompt), HumanMessage(text)])
    route = "CHAT" if "CHAT" in resp.content.upper() else "WORK"
    _route_cache_set(text, route)
    return {"route": route}


def _chat_node(state: SmartState, employee_key: str) -> dict:
    suffix = _global_prompt(employee_key, "chat_suffix", _DEFAULT_CHAT_SUFFIX)
    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "chat", default_model="claude-sonnet-4-6")
    history = _get_history(state)
    human_msg = _human_msg(state["task_input"])
    resp = llm.invoke([
        SystemMessage(_system_prompt_for(employee_key, suffix, query=query)),
        *history,
        human_msg,
    ])
    return {"execution_result": resp.content, "messages": [human_msg, resp]}


def _plan_node(state: SmartState, employee_key: str) -> dict:
    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "plan", default_model="claude-opus-4-6")
    resp = llm.invoke([
        SystemMessage(_system_prompt_for(employee_key, _DEFAULT_PLAN_SUFFIX, query=query)),
        _human_msg(state["task_input"]),
    ])
    return {"plan": resp.content}


def _execute_node(state: SmartState, employee_key: str) -> dict:
    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "execute", default_model="claude-opus-4-6")
    history = _get_history(state)
    human_msg = _human_msg(state["task_input"], prefix=f"执行方案：{state['plan']}\n\n原始需求（如有图请一并分析）：\n")
    resp = llm.invoke([
        SystemMessage(_system_prompt_for(employee_key, query=query)),
        *history,
        human_msg,
    ])
    return {"execution_result": resp.content, "messages": [human_msg, resp]}


def _tools_hint(tools: list) -> str:
    """告知 LLM 当前已绑定的工具，描述从 ToolMeta 自动读取。"""
    if not tools:
        return ""
    from agents_v2.shared.tools import TOOL_META
    items = []
    for t in tools:
        meta = TOOL_META.get(t.name)
        hint = meta.hint if meta else t.description[:20]
        items.append(f"`{t.name}`（{hint}）")
    return "\n\n你当前已绑定工具：" + "、".join(items) + "。用户询问相关能力时请如实告知并直接调用。"


def _is_all_action(tool_calls: list, tools: list) -> bool:
    """判断本轮所有工具调用是否都是 ACTION 类型（执行完即结束）。"""
    from agents_v2.shared.tools import TOOL_META, ToolType
    if not tool_calls:
        return False
    for tc in tool_calls:
        meta = TOOL_META.get(tc["name"])
        if not meta or meta.type != ToolType.ACTION:
            return False
    return True


def _react_node(state: SmartState, employee_key: str, tools: list, max_rounds: int = 8) -> dict:
    """ReAct 工具调用循环 — 带工具的 execute node。"""
    from langchain_core.messages import AIMessage, ToolMessage as TM

    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "execute", default_model="claude-opus-4-6").bind_tools(tools)
    tool_map = {t.name: t for t in tools}

    plan_prefix = f"执行方案：{state['plan']}\n\n" if state.get("plan") else ""
    history = _get_history(state)
    human_msg = _human_msg(state["task_input"], prefix=f"{plan_prefix}原始需求：\n")
    messages = [
        SystemMessage(_system_prompt_for(employee_key, _tools_hint(tools), query=query)),
        *history,
        human_msg,
    ]

    for _ in range(max_rounds):
        resp = llm.invoke(messages)
        messages.append(resp)

        if not resp.tool_calls:
            break

        for tc in resp.tool_calls:
            tool_fn = tool_map.get(tc["name"])
            result = tool_fn.invoke(tc["args"]) if tool_fn else f"未知工具: {tc['name']}"
            messages.append(TM(content=str(result), tool_call_id=tc["id"]))

    # 取最后一条 AI 文本回复
    final = next(
        (m.content for m in reversed(messages)
         if isinstance(m, AIMessage) and not m.tool_calls and m.content),
        None,
    )
    if not final:
        # ACTION 工具执行完不需要 LLM 再汇总
        last_tool_calls = next(
            (m.tool_calls for m in reversed(messages)
             if isinstance(m, AIMessage) and m.tool_calls),
            [],
        )
        if _is_all_action(last_tool_calls, tools):
            final = "操作已完成"
        else:
            summary = llm.invoke(messages)
            final = summary.content or "操作完成"
    # 把本轮对话写入历史：只保留无 tool_calls 的 AI 回复，避免孤立的 tool_use 块
    exchange = [
        m for m in messages[len(history) + 1:]
        if isinstance(m, HumanMessage)
        or (isinstance(m, AIMessage) and not getattr(m, "tool_calls", None))
    ]
    return {"execution_result": final, "messages": exchange}


def _react_chat_node(state: SmartState, employee_key: str, tools: list) -> dict:
    """带工具的 chat node — 闲聊时也可调用工具。"""
    from langchain_core.messages import AIMessage, ToolMessage as TM

    suffix = _global_prompt(employee_key, "chat_suffix", _DEFAULT_CHAT_SUFFIX)
    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "chat", default_model="claude-sonnet-4-6").bind_tools(tools)
    tool_map = {t.name: t for t in tools}
    history = _get_history(state)
    human_msg = _human_msg(state["task_input"])

    messages = [
        SystemMessage(_system_prompt_for(employee_key, suffix + _tools_hint(tools), query=query)),
        *history,
        human_msg,
    ]

    for _ in range(4):
        resp = llm.invoke(messages)
        messages.append(resp)
        if not resp.tool_calls:
            break
        for tc in resp.tool_calls:
            tool_fn = tool_map.get(tc["name"])
            result = tool_fn.invoke(tc["args"]) if tool_fn else f"未知工具: {tc['name']}"
            messages.append(TM(content=str(result), tool_call_id=tc["id"]))

    final = next(
        (m.content for m in reversed(messages)
         if isinstance(m, AIMessage) and not m.tool_calls and m.content),
        None,
    )
    if not final:
        last_tool_calls = next(
            (m.tool_calls for m in reversed(messages)
             if isinstance(m, AIMessage) and m.tool_calls),
            [],
        )
        if _is_all_action(last_tool_calls, tools):
            final = "操作已完成"
    # 把本轮对话写入历史：只保留无 tool_calls 的 AI 回复，避免历史里出现孤立的 tool_use 块
    exchange = [
        m for m in messages[len(history) + 1:]
        if isinstance(m, HumanMessage)
        or (isinstance(m, AIMessage) and not getattr(m, "tool_calls", None))
    ]
    return {"execution_result": final or "", "messages": exchange}


def _valid_employees() -> set[str]:
    from backend.services import registry
    if not registry._loaded:  # type: ignore[attr-defined]
        try:
            registry.warmup_sync()
        except RuntimeError:
            return set()
    return {k for k in registry.list_keys_sync_cached(active_only=True)
            if k not in {"product_manager", "sysadmin"}}


def _cc_node(state: SmartState, employee_key: str, cc_prompt: str) -> dict:
    """PM-only: decide which specialists should add a follow-up."""
    import json as _json
    import re as _re
    llm = _llm_for(employee_key, "cc", default_model="claude-haiku-4-5-20251001")
    context = f"原始消息：{_text_only(state['task_input'])}\n\n产品经理回复：{state['execution_result']}"
    resp = llm.invoke([SystemMessage(cc_prompt), HumanMessage(context)])
    try:
        m = _re.search(r"\[.*?\]", resp.content, _re.DOTALL)
        cc = _json.loads(m.group()) if m else []
        valid = _valid_employees()
        cc = [e for e in cc if e in valid]
    except Exception:
        cc = []
    return {"cc": cc}


def _decide_after_route(state: SmartState) -> Literal["chat", "plan"]:
    return "chat" if state["route"] == "CHAT" else "plan"


# ── Public builder ───────────────────────────────────────────────────────────

def build_smart_agent(employee_key_or_prompt, checkpointer, cc_prompt: str = "", tools: list | None = None):
    """Build the routing graph for an employee.

    Two call shapes (the first is the new one; the second is kept for
    backwards compatibility with code that still passes a system_prompt):

        build_smart_agent("mechanical", checkpointer)
        build_smart_agent("mechanical", checkpointer, tools=[run_command, ...])
        build_smart_agent(SYSTEM_PROMPT_TEXT, checkpointer)  # legacy
    """
    from functools import partial

    # Detect legacy (string longer than a typical key looks like a prompt).
    arg = employee_key_or_prompt
    if isinstance(arg, str) and ("\n" in arg or len(arg) > 32):
        # Legacy: caller passed system_prompt directly. Wrap it.
        return _build_with_static_prompt(arg, checkpointer, cc_prompt)

    employee_key = arg

    g = StateGraph(SmartState)
    g.add_node("route", partial(_route_node, employee_key=employee_key))
    g.add_node("plan",  partial(_plan_node,  employee_key=employee_key))

    # 有工具时用 ReAct 循环，无工具时用纯 LLM
    if tools:
        g.add_node("chat",    partial(_react_chat_node, employee_key=employee_key, tools=tools))
        g.add_node("execute", partial(_react_node,      employee_key=employee_key, tools=tools))
    else:
        g.add_node("chat",    partial(_chat_node,    employee_key=employee_key))
        g.add_node("execute", partial(_execute_node, employee_key=employee_key))

    g.add_edge(START, "route")
    g.add_conditional_edges("route", _decide_after_route, {"chat": "chat", "plan": "plan"})
    g.add_edge("plan", "execute")

    if cc_prompt:
        g.add_node("cc", partial(_cc_node, employee_key=employee_key, cc_prompt=cc_prompt))
        g.add_edge("chat",    "cc")
        g.add_edge("execute", "cc")
        g.add_edge("cc", END)
    else:
        g.add_edge("chat",    END)
        g.add_edge("execute", END)

    return g.compile(checkpointer=checkpointer)


def _build_with_static_prompt(system_prompt: str, checkpointer, cc_prompt: str = ""):
    """Legacy path — used by tests and any caller that passes a literal prompt.

    Functionally identical to the keyed version but with a fixed system_prompt
    and the original hardcoded models.
    """
    from functools import partial

    def route(state):
        text = _text_only(state["task_input"])
        if not text:
            return {"route": "WORK"}
        llm = make_langchain_llm("claude-haiku-4-5-20251001")
        resp = llm.invoke([SystemMessage(_DEFAULT_ROUTE_PROMPT), HumanMessage(text)])
        return {"route": "CHAT" if "CHAT" in resp.content.upper() else "WORK"}

    def chat(state):
        llm = make_langchain_llm("claude-sonnet-4-6")
        resp = llm.invoke([SystemMessage(system_prompt + _DEFAULT_CHAT_SUFFIX), _human_msg(state["task_input"])])
        return {"execution_result": resp.content}

    def plan(state):
        llm = make_langchain_llm("claude-opus-4-6")
        resp = llm.invoke([SystemMessage(system_prompt + _DEFAULT_PLAN_SUFFIX), _human_msg(state["task_input"])])
        return {"plan": resp.content}

    def execute(state):
        llm = make_langchain_llm("claude-opus-4-6")
        resp = llm.invoke([
            SystemMessage(system_prompt),
            _human_msg(state["task_input"], prefix=f"执行方案：{state['plan']}\n\n原始需求（如有图请一并分析）：\n"),
        ])
        return {"execution_result": resp.content}

    g = StateGraph(SmartState)
    g.add_node("route", route)
    g.add_node("chat", chat)
    g.add_node("plan", plan)
    g.add_node("execute", execute)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", _decide_after_route, {"chat": "chat", "plan": "plan"})
    g.add_edge("plan", "execute")

    if cc_prompt:
        # Legacy CC path uses default haiku model and the registry's _valid_employees filter.
        def cc(state):
            import json as _json, re as _re
            llm = make_langchain_llm("claude-haiku-4-5-20251001")
            context = f"原始消息：{_text_only(state['task_input'])}\n\n产品经理回复：{state['execution_result']}"
            resp = llm.invoke([SystemMessage(cc_prompt), HumanMessage(context)])
            try:
                m = _re.search(r"\[.*?\]", resp.content, _re.DOTALL)
                ids = _json.loads(m.group()) if m else []
                valid = _valid_employees()
                ids = [e for e in ids if e in valid]
            except Exception:
                ids = []
            return {"cc": ids}
        g.add_node("cc", cc)
        g.add_edge("chat", "cc")
        g.add_edge("execute", "cc")
        g.add_edge("cc", END)
    else:
        g.add_edge("chat", END)
        g.add_edge("execute", END)

    return g.compile(checkpointer=checkpointer)
