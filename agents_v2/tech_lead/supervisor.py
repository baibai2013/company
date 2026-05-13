"""
TechLead Supervisor: LangGraph StateGraph that decomposes tasks and delegates
to employee agents via A2A protocol.
"""
import json
import re
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agents_v2.shared.a2a_server import call_agent
from agents_v2.shared.claude_client import make_langchain_llm
from agents_v2.tech_lead.prompts import SYSTEM_PROMPT


def _employee_endpoints() -> dict[str, str]:
    """Build the {key: http://host:port} routing table from registry.

    Excludes tech_lead itself (this graph runs inside it) and any inactive employees.
    """
    from backend.services import registry
    if not registry._loaded:  # type: ignore[attr-defined]
        try:
            registry.warmup_sync()
        except RuntimeError:
            pass
    out = {}
    for emp in registry.list_keys_sync_cached(active_only=True):
        if emp == "tech_lead":
            continue
        cfg = registry.get_effective_sync(emp)
        if cfg and cfg.agent_port:
            out[emp] = f"http://localhost:{cfg.agent_port}"
    return out


# Backwards-compatible: many callers do `EMPLOYEES.get(k)` or `k in EMPLOYEES`.
class _DynamicEndpoints:
    def _data(self):
        return _employee_endpoints()
    def __getitem__(self, k):     return self._data()[k]
    def get(self, k, default=None): return self._data().get(k, default)
    def __contains__(self, k):     return k in self._data()
    def __iter__(self):            return iter(self._data())
    def __len__(self):             return len(self._data())
    def items(self):               return self._data().items()
    def keys(self):                return self._data().keys()
    def values(self):              return self._data().values()


EMPLOYEES: dict[str, str] = _DynamicEndpoints()  # type: ignore[assignment]


class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    task_description: str
    domain_plans: dict        # {employee_key: task_text}
    completed_outputs: dict   # {employee_key: result_text}
    next_employee: str        # next employee key to dispatch, or "DONE"
    phase: str


def plan_node(state: SupervisorState) -> dict:
    llm = make_langchain_llm()
    resp = llm.invoke([
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(state["task_description"]),
    ])
    m = re.search(r"\{.*\}", resp.content, re.DOTALL)
    plans = {}
    if m:
        try:
            plans = json.loads(m.group())
            # Only keep known employees
            plans = {k: v for k, v in plans.items() if k in EMPLOYEES}
        except json.JSONDecodeError:
            pass
    return {"domain_plans": plans, "phase": "dispatching"}


async def delegate_node(state: SupervisorState) -> dict:
    employee = state["next_employee"]
    url = EMPLOYEES[employee]
    task_text = state["domain_plans"].get(employee, state["task_description"])
    context_text = "\n".join(
        f"[{k} 输出]: {v[:500]}" for k, v in state["completed_outputs"].items()
    )
    message = f"{task_text}\n\n前序上下文：\n{context_text}" if context_text else task_text

    try:
        result_text = await call_agent(url, message)
    except Exception as exc:
        result_text = f"[错误] {employee} 调用失败: {exc}"

    return {"completed_outputs": {**state["completed_outputs"], employee: result_text}}


def route_node(state: SupervisorState) -> dict:
    remaining = [k for k in state["domain_plans"] if k not in state["completed_outputs"]]
    next_emp = remaining[0] if remaining else "DONE"
    return {"next_employee": next_emp}


def _route_after_delegate(state: SupervisorState) -> str:
    return END if state.get("next_employee") == "DONE" else "delegate"


def build_supervisor(checkpointer):
    """Compile the supervisor graph with the given checkpointer."""
    g = StateGraph(SupervisorState)
    g.add_node("plan", plan_node)
    g.add_node("route", route_node)
    g.add_node("delegate", delegate_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "route")
    g.add_conditional_edges(
        "route",
        lambda s: "delegate" if s.get("next_employee") != "DONE" else END,
        {"delegate": "delegate", END: END},
    )
    g.add_edge("delegate", "route")

    return g.compile(checkpointer=checkpointer)
