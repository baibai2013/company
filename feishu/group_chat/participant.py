"""
Participant 抽象层 — 统一 AI 员工和真实用户的交互接口。

设计理念参考 AgentScope 的 UserAgent/DialogAgent 统一抽象：
场景代码只调 participant.speak()，不关心对面是 LLM 还是人类。

使用方式：
    players = [Participant.from_key(k) for k in session.participants if k != host]
    for p in players:
        content = await p.speak(session, bus_pool, context="请猜一个数字")
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .event_bus import GroupEventBusPool
    from .models import GroupSession


class Participant(ABC):
    """参与者抽象基类。AI 员工和真实用户的统一接口。"""

    @property
    @abstractmethod
    def key(self) -> str:
        """唯一标识。AI: "project_manager"；用户: "user" 或 "user:ou_xxx"。"""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """显示名称，用于消息和 context 中。"""
        ...

    @property
    @abstractmethod
    def emoji(self) -> str:
        """表情符号前缀。"""
        ...

    @property
    @abstractmethod
    def is_human(self) -> bool:
        """是否为真实用户。"""
        ...

    @abstractmethod
    async def speak(
        self,
        session: "GroupSession",
        bus_pool: "GroupEventBusPool",
        context: str,
        visible_to: list[str] | None = None,
        timeout: float = 120.0,
    ) -> str | None:
        """获取参与者的发言。

        AI 员工：发送 SpeakRequest，等待 LLM 生成回复。
        真实用户：等待用户在飞书群里发消息。

        发言会自动追加到 session.history。

        Args:
            session: 当前群聊会话。
            bus_pool: Redis 事件总线。
            context: 给参与者的角色指令/上下文（AI 会用来生成回复，用户看不到）。
            visible_to: 消息可见范围，空=全员可见。
            timeout: 超时秒数。

        Returns:
            发言内容文本，超时或失败返回 None。
        """
        ...

    @staticmethod
    def from_key(key: str, display_name: str = "") -> "Participant":
        """工厂方法：根据 key 创建对应实例。

        Args:
            key: 参与者标识。以 "user" 开头视为真实用户，否则为 AI 员工。
            display_name: 可选，真实用户的显示名。AI 员工从 EMPLOYEE_CONFIG 自动获取。
        """
        if key.startswith("user"):
            return HumanParticipant(key, display_name=display_name)
        return AiParticipant(key)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(key={self.key!r})"


class AiParticipant(Participant):
    """AI 员工 — 通过 SpeakRequest/SpeakResponse 管道获取 LLM 回复。"""

    def __init__(self, employee_key: str):
        self._key = employee_key

    @property
    def key(self) -> str:
        return self._key

    @property
    def display_name(self) -> str:
        from .models import EMPLOYEE_CONFIG
        _, name = EMPLOYEE_CONFIG.get(self._key, ("👤", self._key))
        return name

    @property
    def emoji(self) -> str:
        from .models import EMPLOYEE_CONFIG
        e, _ = EMPLOYEE_CONFIG.get(self._key, ("👤", self._key))
        return e

    @property
    def is_human(self) -> bool:
        return False

    async def speak(
        self,
        session: "GroupSession",
        bus_pool: "GroupEventBusPool",
        context: str,
        visible_to: list[str] | None = None,
        timeout: float = 120.0,
    ) -> str | None:
        from .pipelines import announce
        return await announce(
            session, self._key, bus_pool,
            context=context, visible_to=visible_to, timeout=timeout,
        )


class HumanParticipant(Participant):
    """真实用户 — 等待飞书群里的真实消息。"""

    def __init__(self, key: str = "user", display_name: str = ""):
        self._key = key
        self._name = display_name or "老板"

    @property
    def key(self) -> str:
        return self._key

    @property
    def display_name(self) -> str:
        return self._name

    @property
    def emoji(self) -> str:
        return "👔"

    @property
    def is_human(self) -> bool:
        return True

    async def speak(
        self,
        session: "GroupSession",
        bus_pool: "GroupEventBusPool",
        context: str,
        visible_to: list[str] | None = None,
        timeout: float = 120.0,
    ) -> str | None:
        from .pipelines import wait_for_user_msg
        return await wait_for_user_msg(
            session, bus_pool, sender=self._key, timeout=timeout,
        )
