"""提案 4 §3 阶段二 — TechLead Supervisor LangGraph 单测。

不真跑 dev pg / 不真触发 verifier 三闸,全用 monkeypatch 替身:
  - delegation_service.create_delegation → 返回假 Delegation
  - delegation_repo.get                  → 返回 in_progress 假 Delegation
  - verifier_orchestrator.on_delegation_done → 返回 verdict='pass'
  - routing_decision_repo.record        → 返回假 RoutingDecision (id=1)

覆盖:
  1. 命中关键词的任务跑完 5 节点,chosen 正确,verifier 被触发
  2. 0 命中的任务回退 tech_lead,但 dispatch 仍能跑(派给自己)
  3. trigger_verifier 异常被捕获,supervisor 仍走到 finish
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents_v2.tech_lead import supervisor as sup
from agents_v2.tech_lead.supervisor import build_supervisor_graph


def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


def _patch_external(monkeypatch, *,
                    verifier_result: dict | None = None,
                    raise_on_verifier: Exception | None = None):
    """统一 mock 外部依赖。返回 captured dict 让 caller 断言。"""
    captured: dict = {"create_calls": [], "get_calls": [], "verifier_calls": [],
                       "routing_records": []}

    async def fake_record(*, task_id, step_idx, candidates, chosen, reason):
        captured["routing_records"].append({
            "task_id": task_id,
            "step_idx": step_idx,
            "candidates": list(candidates) if candidates else None,
            "chosen": chosen,
            "reason": reason,
        })
        return SimpleNamespace(id=42)

    async def fake_create_delegation(**kwargs):
        captured["create_calls"].append(kwargs)
        return SimpleNamespace(id="deleg-uuid-001", status="pending")

    async def fake_get(deleg_id):
        captured["get_calls"].append(deleg_id)
        return SimpleNamespace(id=deleg_id, status="in_progress")

    async def fake_on_done(deleg_id):
        captured["verifier_calls"].append(deleg_id)
        if raise_on_verifier:
            raise raise_on_verifier
        return verifier_result or {
            "run_id": "run-1",
            "final_verdict": "pass",
            "reason": "stub pass",
        }

    monkeypatch.setattr(sup.routing_decision_repo, "record", fake_record)
    monkeypatch.setattr(sup.delegation_service, "create_delegation",
                        fake_create_delegation)
    monkeypatch.setattr(sup.delegation_repo, "get", fake_get)
    monkeypatch.setattr(sup.verifier_orchestrator, "on_delegation_done",
                        fake_on_done)
    return captured


async def test_full_supervisor_flow_routes_to_mechanical(monkeypatch):
    """命中机械关键词 → chosen=mechanical → 走完 5 节点,verifier pass。"""
    captured = _patch_external(monkeypatch)

    g = build_supervisor_graph(checkpointer=MemorySaver())
    final = await g.ainvoke(
        {
            "task_id": "11111111-1111-1111-1111-111111111111",
            "task_title": "实现机械腿装配",
            "task_content": "需要画 step 模型并出 BOM",
            "from_employee": "tech_lead",
            "step_idx": 0,
            "due_in_minutes": 30,
        },
        config=_cfg("sup-thread-1"),
    )

    # 路由产物
    assert final["chosen_employee"] == "mechanical"
    assert "mechanical" in final["candidates"]
    assert final["routing_decision_id"] == 42
    # 派活产物
    assert final["delegation_id"] == "deleg-uuid-001"
    # 状态读取
    assert final["delegation_status"] == "in_progress"
    # 三闸结果
    assert final["verifier_result"]["final_verdict"] == "pass"
    # 终态
    assert final["status"] == "done"

    # 副作用断言
    assert len(captured["routing_records"]) == 1
    assert captured["routing_records"][0]["chosen"] == "mechanical"
    assert captured["routing_records"][0]["step_idx"] == 0
    assert len(captured["create_calls"]) == 1
    assert captured["create_calls"][0]["to_employee"] == "mechanical"
    assert captured["create_calls"][0]["from_employee"] == "tech_lead"
    assert captured["create_calls"][0]["due_in_minutes"] == 30
    assert captured["get_calls"] == ["deleg-uuid-001"]
    assert captured["verifier_calls"] == ["deleg-uuid-001"]


async def test_no_keyword_falls_back_to_tech_lead(monkeypatch):
    """0 命中 → 兜底 tech_lead,dispatch 把 tech_lead 派给自己。"""
    captured = _patch_external(monkeypatch)

    g = build_supervisor_graph(checkpointer=MemorySaver())
    final = await g.ainvoke(
        {
            "task_id": "22222222-2222-2222-2222-222222222222",
            "task_title": "今天天气好",
            "task_content": "随便聊聊",
        },
        config=_cfg("sup-thread-2"),
    )
    assert final["chosen_employee"] == "tech_lead"
    assert final["candidates"] == ["tech_lead"]
    assert final["status"] == "done"

    # dispatch 把 tech_lead 派给自己(from=to=tech_lead)
    assert captured["create_calls"][0]["to_employee"] == "tech_lead"
    assert captured["create_calls"][0]["from_employee"] == "tech_lead"


async def test_verifier_exception_does_not_break_finish(monkeypatch):
    """verifier 抛错被吞,supervisor 仍走到 finish,verdict='error'。"""
    captured = _patch_external(
        monkeypatch,
        raise_on_verifier=RuntimeError("verifier 模拟挂掉"),
    )

    g = build_supervisor_graph(checkpointer=MemorySaver())
    final = await g.ainvoke(
        {
            "task_id": "33333333-3333-3333-3333-333333333333",
            "task_title": "esp32 i2c 驱动",
            "task_content": "",
        },
        config=_cfg("sup-thread-3"),
    )
    # 路由仍然命中 firmware
    assert final["chosen_employee"] == "firmware"
    # verifier 异常被吞,verdict 为 error
    assert final["verifier_result"]["final_verdict"] == "error"
    assert "verifier 模拟挂掉" in final["verifier_result"]["reason"]
    # 仍走到 finish
    assert final["status"] == "done"
    assert captured["verifier_calls"] == ["deleg-uuid-001"]


async def test_routing_record_failure_does_not_break_supervisor(monkeypatch):
    """写 routing_decisions 失败时,supervisor 应继续(routing_decision_id=None)。"""
    # 自定义:让 routing_decision_repo.record 抛
    async def bad_record(**_):
        raise RuntimeError("dev pg 没起")

    captured = _patch_external(monkeypatch)
    monkeypatch.setattr(sup.routing_decision_repo, "record", bad_record)

    g = build_supervisor_graph(checkpointer=MemorySaver())
    final = await g.ainvoke(
        {
            "task_id": "44444444-4444-4444-4444-444444444444",
            "task_title": "测试 回归 评测",
            "task_content": "",
        },
        config=_cfg("sup-thread-4"),
    )
    # 路由仍能选(只是没写 DB)
    # Wave 4 routing 命名对齐 a2a_router PORT_MAP:testing → test_engineer
    assert final["chosen_employee"] == "test_engineer"
    assert final["routing_decision_id"] is None
    # supervisor 仍跑到底
    assert final["status"] == "done"
    assert final["verifier_result"]["final_verdict"] == "pass"
