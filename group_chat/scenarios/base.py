"""
场景基类与注册表。

Scenario 封装完整的多 Agent 交互流程（游戏、辩论、头脑风暴等）：
- initialize(): 在流程开始前初始化 game_state
- run(): 通过组合 pipeline 原语执行完整流程
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..event_bus import GroupEventBusPool
    from ..models import GroupSession

log = logging.getLogger(__name__)


# ── Registry ─────────────────────────────────────────────────────────────────

SCENARIO_REGISTRY: dict[str, type["Scenario"]] = {}


def register(*template_names: str):
    """装饰器：将 Scenario 子类注册到一个或多个模板名下。"""
    def decorator(cls: type["Scenario"]):
        for name in template_names:
            SCENARIO_REGISTRY[name] = cls
        return cls
    return decorator


# ── Base class ────────────────────────────────────────────────────────────────

class Scenario:
    """多 Agent 编排场景的基类。

    子类通过 override initialize() 和 run() 定义自己的交互流程。
    orchestrator 的 dispatch_node 在 session.template 匹配已注册场景时，
    会将控制权委托给 scenario.run()。
    """

    def __init__(self, session: "GroupSession", session_store=None):
        self.session = session
        self.session_store = session_store

    def initialize(self, activity_rules: str) -> dict:
        """初始化游戏状态（在 decide_node 中调用）。

        Args:
            activity_rules: LLM 生成的活动规则文本。

        Returns:
            将存储到 session.game_state 的字典。
        """
        return {}

    async def run(self, bus_pool: "GroupEventBusPool") -> None:
        """执行完整的场景流程。

        子类在此方法中组合 pipeline 原语（sequential、fanout、announce、vote 等）
        实现游戏/互动的完整逻辑。session.history 会被就地修改。
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement run()"
        )
