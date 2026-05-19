"""机器狗工程流水线场景。

阶段 1: product_manager 出 PRD
阶段 2: mechanical / firmware / algorithm 并行执行
阶段 3: cost 汇总 BOM

不是游戏场景,只是借用 scenario 注册表来固定 DAG 形状,绕过 _decide_node 的
LLM 决策(LLM 决策对工程任务太不稳)。

由 Task 触发(chat_id=task:{task_id}) → orchestrator 关键词识别 →
session.template = "robot_engineering" → dispatch_node 走本 scenario.run()。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..event_bus import GroupEventBusPool

from ..pipelines import sequential as pipe_sequential, fanout as pipe_fanout
from .base import Scenario, register

log = logging.getLogger(__name__)


@register("robot_engineering")
class RobotEngineeringScenario(Scenario):
    """机器狗工程流水线。

    PRD → (机械/固件/算法 并行) → 成本 BOM。
    每阶段完成才进入下一阶段(sequential 与 fanout 内部已等待响应)。
    """

    def initialize(self, activity_rules: str = "") -> dict:
        return {
            "phase": "prd",
            "request": (activity_rules or "")[:500],
            "deliverables": {},
        }

    async def run(self, bus_pool: "GroupEventBusPool") -> None:
        session = self.session
        # 过滤当前 EMPLOYEE_CONFIG 实际存在的 key,避免空 registry 时把 fanout 跑空
        from ..models import EMPLOYEE_CONFIG

        def _present(*keys: str) -> list[str]:
            if len(EMPLOYEE_CONFIG) == 0:
                # 测试/未 warmup 环境:不过滤,让上层逻辑决定如何处理
                return list(keys)
            return [k for k in keys if k in EMPLOYEE_CONFIG]

        # 阶段 1: 产品经理出 PRD
        prd_team = _present("product_manager")
        if prd_team:
            log.info("robot_engineering: phase=prd team=%s", prd_team)
            await pipe_sequential(session, prd_team, bus_pool)
            session.game_state["phase"] = "engineering"

        # 阶段 2: 机械/固件/算法 并行
        eng_team = _present("mechanical", "firmware", "algorithm")
        if eng_team:
            log.info("robot_engineering: phase=engineering team=%s", eng_team)
            await pipe_fanout(session, eng_team, bus_pool)
            session.game_state["phase"] = "cost"

        # 阶段 3: 成本汇总 BOM
        cost_team = _present("cost")
        if cost_team:
            log.info("robot_engineering: phase=cost team=%s", cost_team)
            await pipe_sequential(session, cost_team, bus_pool)
            session.game_state["phase"] = "done"
