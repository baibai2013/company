"""
Scenario plugins for group chat orchestration.

Each scenario defines a complete multi-agent interaction flow
(game, debate, brainstorm, etc.) by composing pipeline primitives.

Layer 3 in the architecture: Transport → Pipelines → **Scenarios**

热重载由 scripts/hot_reload.py 守护进程统一管理（SIGUSR1 → reload 各模块）。
"""
import importlib
import logging
import signal

from .base import Scenario, SCENARIO_REGISTRY, register  # noqa: F401
from . import guess_number  # noqa: F401
from . import werewolf  # noqa: F401
from . import robot_engineering  # noqa: F401
from . import concurrent_doc_edit  # noqa: F401

_log = logging.getLogger(__name__)

# 文件名 → 模块，新增 Scenario 在此注册即可被热重载
_scenario_modules = [guess_number, werewolf, robot_engineering, concurrent_doc_edit]


def reload_all():
    """重载所有 scenario 模块并更新注册表（供 SIGUSR1 handler 调用）。"""
    for mod in _scenario_modules:
        try:
            importlib.reload(mod)
            _log.info("🔄 scenario reloaded: %s", mod.__name__)
        except Exception as exc:
            _log.warning("reload failed [%s]: %s", mod.__name__, exc)
