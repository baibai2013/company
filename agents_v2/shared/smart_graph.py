"""
智能路由 Agent Graph — 供所有员工复用。

路由逻辑：
  route_node  → 判断 CHAT / WORK
  CHAT        → chat_node   : 用员工人设直接对话 (< 3s)
  WORK        → plan_node   → execute_node : 全流程输出
"""
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agents_v2.shared.claude_client import make_langchain_llm

_ROUTE_PROMPT = """判断下面这条消息是「闲聊」还是「工作任务」。

闲聊：问候、状态询问、随便聊聊、简单确认（如"在吗""怎么样""最近忙吗"）。
工作任务：包含具体设计/开发/分析/建模/验证/输出/报告/计划等技术或业务要求。

只回复一个词：CHAT 或 WORK，不要有其他内容。"""

_CHAT_SUFFIX = "\n\n性格：务实简洁，回复不超过 150 字，用中文，自然对话，不列大纲不输出 JSON。"


class SmartState(TypedDict):
    messages: Annotated[list, add_messages]
    task_input: Any  # str or multimodal list [{type, text/image_url}]
    route: str          # "CHAT" | "WORK"
    plan: str
    execution_result: str
    cc: list            # employees to notify (PM only, empty for others)


def _text_only(task_input: Any) -> str:
    """提取纯文本部分（路由/CC 等不需要图片的节点用）。"""
    if isinstance(task_input, list):
        parts = [b.get("text", "") for b in task_input if b.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return task_input or ""


def _human_msg(task_input: Any, prefix: str = "") -> HumanMessage:
    """构造 HumanMessage，兼容纯文本和多模态 list。"""
    if isinstance(task_input, list):
        content = ([{"type": "text", "text": prefix}] if prefix else []) + task_input
        return HumanMessage(content=content)
    text = f"{prefix}{task_input}" if prefix else task_input
    return HumanMessage(content=text)


def _route_node(state: SmartState, system_prompt: str) -> dict:
    text = _text_only(state["task_input"])
    # 纯图片消息直接走 WORK
    if not text:
        return {"route": "WORK"}
    llm = make_langchain_llm("claude-haiku-4-5-20251001")
    resp = llm.invoke([SystemMessage(_ROUTE_PROMPT), HumanMessage(text)])
    route = "CHAT" if "CHAT" in resp.content.upper() else "WORK"
    return {"route": route}


def _chat_node(state: SmartState, system_prompt: str) -> dict:
    llm = make_langchain_llm("claude-sonnet-4-6")
    resp = llm.invoke([
        SystemMessage(system_prompt + _CHAT_SUFFIX),
        _human_msg(state["task_input"]),
    ])
    return {"execution_result": resp.content}


def _plan_node(state: SmartState, system_prompt: str) -> dict:
    llm = make_langchain_llm("claude-opus-4-7")
    resp = llm.invoke([
        SystemMessage(system_prompt + "\n请分析需求，制定执行方案（100字以内）。"),
        _human_msg(state["task_input"]),
    ])
    return {"plan": resp.content}


def _execute_node(state: SmartState, system_prompt: str) -> dict:
    llm = make_langchain_llm("claude-opus-4-7")
    resp = llm.invoke([
        SystemMessage(system_prompt),
        _human_msg(state["task_input"], prefix=f"执行方案：{state['plan']}\n\n原始需求（如有图请一并分析）：\n"),
    ])
    return {"execution_result": resp.content}


def _valid_employees() -> set[str]:
    """Active employee keys from registry. Used by the PM CC node to filter LLM-suggested specialists."""
    from backend.services import registry
    if not registry._loaded:  # type: ignore[attr-defined]
        try:
            registry.warmup_sync()
        except RuntimeError:
            return set()
    # Exclude product_manager (it's the one doing the CC) and sysadmin (ops, not specialist).
    return {k for k in registry.list_keys_sync_cached(active_only=True)
            if k not in {"product_manager", "sysadmin"}}


def _cc_node(state: SmartState, cc_prompt: str) -> dict:
    """Decide which specialists should add a comment (PM only)."""
    import json as _json
    import re as _re
    llm = make_langchain_llm("claude-haiku-4-5-20251001")
    context = f"原始消息：{_text_only(state['task_input'])}\n\n产品经理回复：{state['execution_result']}"
    resp = llm.invoke([
        SystemMessage(cc_prompt),
        HumanMessage(context),
    ])
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


def build_smart_agent(system_prompt: str, checkpointer, cc_prompt: str = ""):
    """Build a routing agent graph parameterized by system_prompt.
    Pass cc_prompt to enable PM-style specialist CC routing.
    """
    from functools import partial

    g = StateGraph(SmartState)
    g.add_node("route",   partial(_route_node,   system_prompt=system_prompt))
    g.add_node("chat",    partial(_chat_node,    system_prompt=system_prompt))
    g.add_node("plan",    partial(_plan_node,    system_prompt=system_prompt))
    g.add_node("execute", partial(_execute_node, system_prompt=system_prompt))

    g.add_edge(START, "route")
    g.add_conditional_edges("route", _decide_after_route, {"chat": "chat", "plan": "plan"})

    g.add_edge("plan", "execute")

    if cc_prompt:
        g.add_node("cc", partial(_cc_node, cc_prompt=cc_prompt))
        g.add_edge("chat",    "cc")
        g.add_edge("execute", "cc")
        g.add_edge("cc", END)
    else:
        g.add_edge("chat",    END)
        g.add_edge("execute", END)

    return g.compile(checkpointer=checkpointer)
