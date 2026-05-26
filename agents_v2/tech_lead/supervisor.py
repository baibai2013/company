"""提案 4 §3 阶段二 — TechLead Supervisor 最小可行 LangGraph。

5 节点:route → dispatch → wait_for_done → trigger_verifier → finish。

不在本 wave 范围:
  - reject 退回(verifier 三闸 fail 时不走重派,留 Wave 4)
  - 真 SSE 监听 delegation 状态(本 wave wait_for_done 直接 get 一次)
  - 起 supervisor 进程 / 接 A2A 端口

阶段一(generic 三节点壳)与本阶段共用 LangGraph + checkpointer 概念,
但 state 不同:本图 state 见 SupervisorState。

调用方示例:
    g = build_supervisor_graph(checkpointer=MemorySaver())
    final = await g.ainvoke(
        {
            "task_id": "<uuid>",
            "task_title": "实现机械腿结构",
            "task_content": "...",
            "from_employee": "tech_lead",
            "step_idx": 0,
        },
        config={"configurable": {"thread_id": "task-<uuid>"}},
    )
    final["chosen_employee"]    # 谁被派
    final["delegation_id"]      # 派活记录 id
    final["verifier_result"]    # 三闸 verdict dict
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agents_v2.tech_lead import routing as _routing
from backend.repos import delegation_repo, routing_decision_repo
from backend.services import delegation_service, verifier_orchestrator

log = logging.getLogger(__name__)


class SupervisorState(TypedDict, total=False):
    # 输入
    task_id: str
    task_title: str
    task_content: str
    from_employee: str          # 派活方(默认 tech_lead 自己)
    step_idx: int               # 这是任务内第几步路由(默认 0)
    acceptance_spec: dict | None
    due_in_minutes: int

    # route 输出
    chosen_employee: str
    candidates: list[str]
    routing_reason: str
    routing_decision_id: int | None

    # dispatch 输出
    delegation_id: str

    # wait_for_done 输出
    delegation_status: str

    # trigger_verifier 输出
    verifier_result: dict[str, Any]

    # finish 标志
    status: str  # 'routed' | 'dispatched' | 'awaiting' | 'verified' | 'done'


# ── 节点 ──────────────────────────────────────────────────────────────
async def route_node(state: SupervisorState) -> dict:
    """跑 routing.choose_employee + 写 routing_decisions。"""
    title = state.get("task_title") or ""
    content = state.get("task_content") or ""
    chosen, candidates, reason = _routing.choose_employee(title, content)

    decision_id: int | None = None
    task_id = state.get("task_id")
    step_idx = int(state.get("step_idx") or 0)
    if task_id:
        try:
            row = await routing_decision_repo.record(
                task_id=task_id,
                step_idx=step_idx,
                candidates=candidates,
                chosen=chosen,
                reason=reason,
            )
            decision_id = row.id
        except Exception as exc:
            # 不阻塞主流(dev pg 可能未起;真路径 caller 会有 task_id 必定能写)
            log.warning("supervisor.route: 写 routing_decisions 失败: %s", exc)

    return {
        "chosen_employee": chosen,
        "candidates": candidates,
        "routing_reason": reason,
        "routing_decision_id": decision_id,
        "status": "routed",
    }


async def dispatch_node(state: SupervisorState) -> dict:
    """走 delegation_service.create_delegation 写一条派活。"""
    chosen = state["chosen_employee"]
    from_emp = state.get("from_employee") or "tech_lead"
    task_id = state.get("task_id") or ""
    title = state.get("task_title") or ""
    content = state.get("task_content") or ""
    acceptance_spec = state.get("acceptance_spec")
    due_in_minutes = int(state.get("due_in_minutes") or 60)

    row = await delegation_service.create_delegation(
        from_employee=from_emp,
        to_employee=chosen,
        parent_task_id=task_id,
        title=title,
        content=content,
        acceptance_spec=acceptance_spec,
        due_in_minutes=due_in_minutes,
    )
    return {
        "delegation_id": str(row.id),
        "status": "dispatched",
    }


async def wait_for_done_node(state: SupervisorState) -> dict:
    """本 wave 占位:直接读一次 delegation_repo.get,返回当前 status。

    Wave 4 改成 SSE 长轮询 / postgres NOTIFY 等真异步等待;阶段二只验证
    "supervisor → 提案 1 派活 → 状态可读"链路通。
    """
    deleg_id = state["delegation_id"]
    row = await delegation_repo.get(deleg_id)
    cur_status = row.status if row else "unknown"
    return {
        "delegation_status": cur_status,
        "status": "awaiting",
    }


async def trigger_verifier_node(state: SupervisorState) -> dict:
    """调 verifier_orchestrator.on_delegation_done 跑提案 2 三闸。

    本 wave 不论 delegation 当前是否 done,都直接触发(三闸 stub 自己
    判断)。verifier 失败/拒绝不在阶段二处理(reject 退回留 Wave 4)。
    """
    deleg_id = state["delegation_id"]
    try:
        result = await verifier_orchestrator.on_delegation_done(deleg_id)
    except Exception as exc:
        # 真业务可能因 delegation 不在合适状态而抛;本 wave 不阻塞
        log.warning("supervisor.trigger_verifier: 调用三闸失败: %s", exc)
        result = {"final_verdict": "error", "reason": str(exc)}
    return {
        "verifier_result": result,
        "status": "verified",
    }


def finish_node(state: SupervisorState) -> dict:
    """终态:打 log,标 done。Wave 4 在此推 SSE 进度到前端。"""
    log.info(
        "supervisor.finish: task=%s chosen=%s deleg=%s verdict=%s",
        state.get("task_id"),
        state.get("chosen_employee"),
        state.get("delegation_id"),
        (state.get("verifier_result") or {}).get("final_verdict"),
    )
    return {"status": "done"}


# ── 构图 ──────────────────────────────────────────────────────────────
def build_supervisor_graph(checkpointer=None):
    """构造 supervisor LangGraph(线性 5 节点,无回环)。

    Args:
        checkpointer: 可选 LangGraph checkpointer。生产走 PostgresSaver,
            单测走 MemorySaver。

    Returns:
        compiled CompiledStateGraph,调用方用 `.ainvoke({...}, config=...)`。
    """
    g = StateGraph(SupervisorState)
    g.add_node("route", route_node)
    g.add_node("dispatch", dispatch_node)
    g.add_node("wait_for_done", wait_for_done_node)
    g.add_node("trigger_verifier", trigger_verifier_node)
    g.add_node("finish", finish_node)

    g.add_edge(START, "route")
    g.add_edge("route", "dispatch")
    g.add_edge("dispatch", "wait_for_done")
    g.add_edge("wait_for_done", "trigger_verifier")
    g.add_edge("trigger_verifier", "finish")
    g.add_edge("finish", END)

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()


__all__ = [
    "SupervisorState",
    "build_supervisor_graph",
    "route_node",
    "dispatch_node",
    "wait_for_done_node",
    "trigger_verifier_node",
    "finish_node",
]
