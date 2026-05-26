"""提案 4 §3.3 阶段一 — generic agent 三节点壳的状态定义。

LangGraph TypedDict state,被 graph.build_generic_graph 与三节点
analyze / execute / self_review 共享。

字段语义:
  - task_id           上层任务 id(可选,checkpoint thread_id 单独由 config 传)
  - employee_key      员工 key(用于 spawn / OTel)
  - requirements      用户原始需求 / 任务标题 + 内容
  - execute_plan      analyze 节点产出的执行计划摘要
  - draft_output      execute 节点产出的草稿(本 wave 是 stub)
  - review_notes      self_review 节点的自检意见
  - needs_revision    self_review 是否要求回到 execute 重做
  - status            'init' | 'analyzed' | 'executed' | 'review_pending'
                      | 'needs_revision' | 'done'

注意:本 wave 是阶段一最小壳,所有节点都是 sync stub,不真实调 claude
code 子进程(那留 Wave 4)。
"""
from __future__ import annotations

from typing import TypedDict


class EmployeeState(TypedDict, total=False):
    task_id: str | None
    employee_key: str
    requirements: str

    execute_plan: str
    draft_output: str
    review_notes: str
    needs_revision: bool

    status: str


__all__ = ["EmployeeState"]
