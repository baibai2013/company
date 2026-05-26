"""提案 4 §3.3 阶段一 — 给 generic agent 套 LangGraph 三节点壳。

节点结构:
    analyze → execute → self_review
                          │
                          ├── needs_revision=True  → execute(回环)
                          └── needs_revision=False → END

每节点完成后由 LangGraph checkpointer 自动 snapshot 写表(走 PostgresSaver
进 dev pg);单测用 MemorySaver 避开 dev pg 依赖。后续提案 1 的
task_context 可直接消费 checkpoint state。

execute 节点的"真 spawn claude code"留 Wave 4,本 wave 用 stub 占位:
    state['draft_output'] = "[stub] {employee_key} 完成草稿: {execute_plan}"

self_review 节点的退回判定也是 stub:首次走完直接 pass;若 caller 在
state 里预置 needs_revision=True 则触发一次回环,**最多回环一次**(避免
死循环),回环完无条件 pass。
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from agents_v2.generic.state import EmployeeState

log = logging.getLogger(__name__)

# self_review 最多触发的回环次数(避免死循环;真业务后续 Wave 接 verifier
# 三闸再放开)。
_MAX_REVISION_LOOPS = 1


# ── 节点实现(全部 sync stub)──────────────────────────────────────────
def analyze_node(state: EmployeeState) -> dict:
    """拆解 requirements → execute_plan。

    本 wave stub:直接把 requirements 复制成 plan,truncate 到 200 字。
    真业务由后续 Wave 接 LLM 拆解。
    """
    req = state.get("requirements") or ""
    plan = req.strip()
    if len(plan) > 200:
        plan = plan[:200] + "..."
    return {
        "execute_plan": plan,
        "status": "analyzed",
    }


def execute_node(state: EmployeeState) -> dict:
    """执行 execute_plan → draft_output。

    本 wave stub:仅拼字符串。真 spawn claude code 留 Wave 4。
    """
    employee = state.get("employee_key") or "unknown"
    plan = state.get("execute_plan") or "(empty)"
    draft = f"[stub] {employee} 完成草稿: {plan}"
    # 不在此处覆写 needs_revision/review_notes:
    # - 首次进入时由 caller 预置(可能 needs_revision=True 触发回环)
    # - 第二次进入(回环)时由 self_review 上一轮已写入 [revisited] 标记
    return {
        "draft_output": draft,
        "status": "executed",
    }


def self_review_node(state: EmployeeState) -> dict:
    """自检 → 决定是否回到 execute。

    本 wave stub 规则:
      - 若 review_notes 非空且 status != 'needs_revision' 已被消化过 → pass
      - caller 可在初始 state 里预置 needs_revision=True 触发一次回环
      - 一旦回过环(再次进入本节点),无条件 pass(避免死循环)

    回环计数挂在 review_notes 里(stub 简化),避免引入额外 state 字段
    破坏 TypedDict 兼容性。
    """
    notes = state.get("review_notes") or ""
    needs_rev = bool(state.get("needs_revision", False))

    # 已经回过一次环(注释里能看到 [revisited]),无条件 pass
    if "[revisited]" in notes:
        return {
            "review_notes": notes,
            "needs_revision": False,
            "status": "done",
        }

    if needs_rev:
        return {
            "review_notes": (notes + " [revisited]").strip(),
            "needs_revision": True,
            "status": "needs_revision",
        }

    return {
        "review_notes": notes or "ok",
        "needs_revision": False,
        "status": "done",
    }


def _route_after_review(state: EmployeeState) -> str:
    """self_review 后的路由:needs_revision=True 回 execute,否则 END。

    回环次数已经在 self_review_node 内部用 review_notes 标记
    [revisited] 强制收敛到一次。
    """
    if state.get("needs_revision"):
        return "execute"
    return END


# ── 构图 ──────────────────────────────────────────────────────────────
def build_generic_graph(checkpointer=None):
    """构造 generic agent 的 LangGraph 三节点壳。

    Args:
        checkpointer: 可选的 LangGraph checkpointer。生产走 PostgresSaver
            (`langgraph-checkpoint-postgres`,Wave 1 requirements 已装),
            单测走 MemorySaver。None 表示不开 checkpoint(便于跑无状态的
            一锤子调用)。

    Returns:
        compiled CompiledStateGraph,调用方用 `.invoke({"requirements": ...,
        "employee_key": ...}, config={"configurable": {"thread_id": ...}})`。
    """
    g = StateGraph(EmployeeState)
    g.add_node("analyze", analyze_node)
    g.add_node("execute", execute_node)
    g.add_node("self_review", self_review_node)

    g.add_edge(START, "analyze")
    g.add_edge("analyze", "execute")
    g.add_edge("execute", "self_review")
    g.add_conditional_edges(
        "self_review",
        _route_after_review,
        {"execute": "execute", END: END},
    )

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()


__all__ = [
    "EmployeeState",
    "build_generic_graph",
    "analyze_node",
    "execute_node",
    "self_review_node",
]
