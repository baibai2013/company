"""
Scenario base class and registry.

A Scenario encapsulates a complete multi-agent interaction flow:
- initialize(): set up game_state before the flow starts
- run(): execute the full flow by composing pipeline primitives
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
    """Decorator: register a Scenario subclass under one or more template names."""
    def decorator(cls: type["Scenario"]):
        for name in template_names:
            SCENARIO_REGISTRY[name] = cls
        return cls
    return decorator


# ── Base class ────────────────────────────────────────────────────────────────

class Scenario:
    """Base class for orchestrated multi-agent scenarios.

    Subclasses override initialize() and run() to define their flow.
    The orchestrator's dispatch_node delegates to scenario.run() when
    the session template matches a registered scenario.
    """

    def __init__(self, session: "GroupSession"):
        self.session = session

    def initialize(self, activity_rules: str) -> dict:
        """Called in decide_node to create initial game_state.

        Returns:
            dict to be stored as session.game_state.
        """
        return {}

    async def run(self, bus_pool: "GroupEventBusPool") -> None:
        """Execute the complete scenario flow.

        Compose pipeline primitives (sequential, fanout, announce, etc.)
        to implement the interaction. Session history is mutated in-place.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement run()"
        )
