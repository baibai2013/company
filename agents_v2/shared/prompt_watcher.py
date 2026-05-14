"""
Agent 提示词热重载工具。

用法：在 Agent 的 main.py 启动时调用一次 watch_prompts()，
修改 prompts.py 后保存，进程自动重载，无需重启。

原理：watchdog 调用 macOS FSEvents（OS 级 kqueue），文件保存即触发，
     importlib.reload 更新模块 __dict__，下次请求自动使用新提示词。
"""
import importlib
import logging
import os
import sys

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

_log = logging.getLogger(__name__)


class _PromptReloadHandler(FileSystemEventHandler):
    """监听 prompts.py 变更并热重载。"""

    def __init__(self, module):
        self._mod = module

    def on_modified(self, event):
        if event.is_directory or not event.src_path.endswith("prompts.py"):
            return
        try:
            importlib.reload(self._mod)
            _log.info("🔄 prompts reloaded: %s", self._mod.__name__)
        except Exception as exc:
            _log.warning("prompts reload failed [%s]: %s", self._mod.__name__, exc)


def watch_prompts(agent_package_name: str) -> None:
    """在 Agent main.py 启动时调用，监听本 package 的 prompts.py 变更。

    Args:
        agent_package_name: Agent 的包名，如 "agents_v2.tech_lead"。
    """
    try:
        mod = importlib.import_module(f"{agent_package_name}.prompts")
    except ModuleNotFoundError:
        _log.debug("no prompts module found for %s, skipping watcher", agent_package_name)
        return

    prompts_dir = os.path.dirname(os.path.abspath(mod.__file__))
    handler = _PromptReloadHandler(mod)
    obs = Observer()
    obs.schedule(handler, path=prompts_dir, recursive=False)
    obs.daemon = True
    obs.start()
    _log.info("prompts watcher started: %s/prompts.py", agent_package_name)
