"""
热更新接收器。

进程启动时调用 setup()，注册 SIGUSR1 信号处理器。
hot_reload.py 守护进程发送 SIGUSR1 时，自动 reload 指定模块。

用法（在 main.py 或进程入口加一行）：
    from agents_v2.shared.hot_reload_receiver import setup
    setup("agents_v2.tech_lead")        # reload agents_v2/tech_lead/prompts.py
    setup("group_chat.prompts")  # reload 整个 prompts 模块
    setup(["agents_v2.tech_lead", "group_chat.prompts"])  # 多个
"""
from __future__ import annotations

import importlib
import logging
import signal
from types import ModuleType

log = logging.getLogger(__name__)

_modules_to_reload: list[ModuleType] = []
_installed = False


def _handle_usr1(signum, frame):
    """SIGUSR1 处理：reload 所有注册模块。"""
    for mod in _modules_to_reload:
        try:
            importlib.reload(mod)
            log.info("🔄 reloaded: %s", mod.__name__)
        except Exception as exc:
            log.warning("reload failed [%s]: %s", mod.__name__, exc)


def setup(package_names: str | list[str]) -> None:
    """注册 SIGUSR1 处理器，收到信号后 reload 指定包的 prompts 模块。

    Args:
        package_names: 包名字符串或列表，如 "agents_v2.tech_lead"。
                       会自动尝试加载 <package>.prompts 子模块。
    """
    global _installed

    if isinstance(package_names, str):
        package_names = [package_names]

    for pkg in package_names:
        # 优先尝试加载 <pkg>.prompts，若失败则尝试直接把 pkg 当模块名
        for mod_name in (f"{pkg}.prompts", pkg):
            try:
                mod = importlib.import_module(mod_name)
                _modules_to_reload.append(mod)
                log.info("hot-reload registered: %s", mod_name)
                break
            except ModuleNotFoundError:
                continue

    if not _installed:
        signal.signal(signal.SIGUSR1, _handle_usr1)
        _installed = True
        log.debug("SIGUSR1 handler installed")
