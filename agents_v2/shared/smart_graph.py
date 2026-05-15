"""
智能路由 Agent Graph — 供所有员工复用。

路由逻辑：
  route_node  → 判断 CHAT / WORK
  CHAT        → chat_node   : 用员工人设直接对话 (< 3s)
  WORK        → plan_node   → execute_node : 全流程输出

配置：所有 LLM 调用的模型、温度、prompts 均从 registry 读取。
节点在每次执行时实时读 config，所以 DB 修改后下次调用立刻生效。
"""
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agents_v2.shared.claude_client import make_langchain_llm

# Fallback prompts — used when registry doesn't supply a global override.

_DEFAULT_ROUTE_PROMPT = """判断下面这条消息是「闲聊」还是「工作任务」。

闲聊：问候、状态询问、随便聊聊、简单确认（如"在吗""怎么样""最近忙吗"）。
工作任务：包含具体设计/开发/分析/建模/验证/输出/报告/计划等技术或业务要求。

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
    """Read EffectiveConfig from registry. Lazy warmup + start LISTEN on first event-loop access."""
    import asyncio
    from backend.services import registry

    if not registry._loaded:  # type: ignore[attr-defined]
        try:
            registry.warmup_sync()
        except RuntimeError:
            pass

    # If we're inside an event loop and the registry listener hasn't started,
    # spawn it so this process gets PG NOTIFY hot-reloads.
    if not registry._listener_task or registry._listener_task.done():  # type: ignore[attr-defined]
        try:
            asyncio.get_running_loop()
            registry.start_listener()
        except RuntimeError:
            pass

    return registry.get_effective_sync(employee_key)


def _llm_for(employee_key: str, call_type: str, default_model: str = "claude-sonnet-4-6"):
    """Build a ChatAnthropic for a specific call_type. Reads model + temperature + max_tokens from registry."""
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
    """构建 system prompt，并注入长期记忆。

    query 非空时做语义检索（pgvector），为空时回退最近 N 条。
    """
    cfg = _load_config(employee_key)
    base = cfg.system_prompt if cfg else ""

    try:
        from backend.repos import memory_repo
        if query:
            memories = memory_repo.search_semantic_sync(employee_key, query, limit=5)
        else:
            memories = memory_repo.get_sync(employee_key)[:5]
        if memories:
            mem_block = "\n".join(f"- {m[:200]}" for m in memories)
            base = (base or "") + f"\n\n【近期参与的讨论（供参考）】\n{mem_block}"
    except Exception:
        pass

    return (base or "") + suffix


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
    llm = _llm_for(employee_key, "route", default_model="claude-haiku-4-5-20251001")
    prompt = _global_prompt(employee_key, "route_prompt", _DEFAULT_ROUTE_PROMPT)
    resp = llm.invoke([SystemMessage(prompt), HumanMessage(text)])
    route = "CHAT" if "CHAT" in resp.content.upper() else "WORK"
    return {"route": route}


def _chat_node(state: SmartState, employee_key: str) -> dict:
    suffix = _global_prompt(employee_key, "chat_suffix", _DEFAULT_CHAT_SUFFIX)
    query = _text_only(state["task_input"])
    llm = _llm_for(employee_key, "chat", default_model="claude-sonnet-4-6")
    resp = llm.invoke([
        SystemMessage(_system_prompt_for(employee_key, suffix, query=query)),
        _human_msg(state["task_input"]),
    ])
    return {"execution_result": resp.content}


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
    resp = llm.invoke([
        SystemMessage(_system_prompt_for(employee_key, query=query)),
        _human_msg(state["task_input"], prefix=f"执行方案：{state['plan']}\n\n原始需求（如有图请一并分析）：\n"),
    ])
    return {"execution_result": resp.content}


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

def build_smart_agent(employee_key_or_prompt, checkpointer, cc_prompt: str = ""):
    """Build the routing graph for an employee.

    Two call shapes (the first is the new one; the second is kept for
    backwards compatibility with code that still passes a system_prompt):

        build_smart_agent("mechanical", checkpointer)
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
    g.add_node("route",   partial(_route_node,   employee_key=employee_key))
    g.add_node("chat",    partial(_chat_node,    employee_key=employee_key))
    g.add_node("plan",    partial(_plan_node,    employee_key=employee_key))
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
