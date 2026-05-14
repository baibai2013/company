"""
Scenario plugins for group chat orchestration.

Each scenario defines a complete multi-agent interaction flow
(game, debate, brainstorm, etc.) by composing pipeline primitives.

Layer 3 in the architecture: Transport → Pipelines → **Scenarios**
"""
from .base import Scenario, SCENARIO_REGISTRY, register  # noqa: F401

# Import scenario modules to trigger registration
from . import guess_number  # noqa: F401
from . import werewolf  # noqa: F401
