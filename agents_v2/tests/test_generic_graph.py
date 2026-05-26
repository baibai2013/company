"""提案 4 §3.3 阶段一 — generic 三节点壳单测。

用 LangGraph MemorySaver 避开 dev pg 依赖。覆盖:
  1. 一锤子 happy path(analyze → execute → self_review → END)
  2. needs_revision=True 触发一次回环,回环后强制 pass
  3. 没有 checkpointer 也能跑(无状态调用)
"""
from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents_v2.generic.graph import (
    analyze_node,
    build_generic_graph,
    execute_node,
    self_review_node,
)


def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


async def test_happy_path_three_nodes():
    """三节点直跑:analyze → execute → self_review 一次过。"""
    g = build_generic_graph(checkpointer=MemorySaver())
    final = await g.ainvoke(
        {
            "task_id": "t-1",
            "employee_key": "mechanical",
            "requirements": "实现机械腿装配的 step 文件",
        },
        config=_cfg("thread-happy"),
    )
    assert final["status"] == "done"
    # analyze 把 requirements 灌进 plan
    assert "机械腿" in final["execute_plan"]
    # execute stub 拼接员工 key
    assert "[stub] mechanical" in final["draft_output"]
    # self_review 默认 ok
    assert final["needs_revision"] is False
    assert final["review_notes"]


async def test_revision_loop_forces_one_revisit():
    """初始预置 needs_revision=True → 走一次回环 → 第二轮强制 pass。"""
    g = build_generic_graph(checkpointer=MemorySaver())
    final = await g.ainvoke(
        {
            "task_id": "t-2",
            "employee_key": "firmware",
            "requirements": "esp32 i2c 驱动调试",
            "needs_revision": True,  # 触发回环
            "review_notes": "需要改",
        },
        config=_cfg("thread-revision"),
    )
    # 回环结束后必须收敛到 done
    assert final["status"] == "done"
    assert final["needs_revision"] is False
    # review_notes 里应有 [revisited] 标记
    assert "[revisited]" in final["review_notes"]
    assert "[stub] firmware" in final["draft_output"]


async def test_runs_without_checkpointer():
    """不传 checkpointer 也能跑(无状态调用)。"""
    g = build_generic_graph(checkpointer=None)
    final = await g.ainvoke(
        {
            "employee_key": "algorithm",
            "requirements": "用 pybullet 跑步态控制仿真",
        },
        # 无 checkpointer 不需要 thread_id
    )
    assert final["status"] == "done"
    assert "[stub] algorithm" in final["draft_output"]
    assert "pybullet" in final["execute_plan"]


def test_analyze_truncates_long_requirements():
    """analyze_node 把超过 200 字的 requirements 截断,加 ...。"""
    long_req = "x" * 500
    out = analyze_node({"requirements": long_req})
    assert out["status"] == "analyzed"
    assert out["execute_plan"].endswith("...")
    assert len(out["execute_plan"]) <= 203  # 200 + "..."


def test_execute_node_uses_employee_key():
    """execute_node stub 把 employee_key 拼进 draft_output;不覆写 needs_revision。"""
    out = execute_node({
        "employee_key": "testing",
        "execute_plan": "回归测试覆盖率",
    })
    assert out["status"] == "executed"
    # 不应在 execute 输出里写 needs_revision(留给 caller / self_review 控制)
    assert "needs_revision" not in out
    assert "[stub] testing" in out["draft_output"]
    assert "回归测试覆盖率" in out["draft_output"]


def test_self_review_pass_then_revisited_short_circuits():
    """self_review:第一次 pass / 预置 revision 走回环 / 已 revisited 强制 pass。"""
    # 第一次 pass
    out1 = self_review_node({"review_notes": "", "needs_revision": False})
    assert out1["status"] == "done"
    assert out1["needs_revision"] is False

    # 预置 revision 触发回环标记
    out2 = self_review_node({"review_notes": "todo", "needs_revision": True})
    assert out2["status"] == "needs_revision"
    assert "[revisited]" in out2["review_notes"]
    assert out2["needs_revision"] is True

    # 已 revisited 再进来无条件 pass
    out3 = self_review_node({"review_notes": "todo [revisited]", "needs_revision": True})
    assert out3["status"] == "done"
    assert out3["needs_revision"] is False
