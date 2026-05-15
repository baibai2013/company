"""
热更新守护进程。

统一监听所有热更点，文件保存后自动向对应进程发送 SIGUSR1。
进程侧通过 agents_v2.shared.hot_reload_receiver 接收信号并 reload。

监听规则：
  group_chat/prompts.py      → orchestrator
  group_chat/pipelines.py    → orchestrator
  group_chat/scenarios/*.py  → orchestrator
  agents_v2/<name>/prompts.py       → <name> 进程

用法：
  python scripts/hot_reload.py          # 前台运行
  python scripts/hot_reload.py --daemon # 后台 nohup
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
PIDS_FILE = ROOT / ".pids"
AGENT_PIDS_DIR = ROOT / "logs" / ".pids"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  hot_reload  %(message)s",
)
log = logging.getLogger("hot_reload")


# ── PID 查询 ──────────────────────────────────────────────────────────────────

def _read_pids() -> dict[str, int]:
    """读取 .pids 和 logs/.pids/*.pid，返回 {进程名: PID}。"""
    pids: dict[str, int] = {}

    # 主 .pids 文件：每行 "<name> <pid>"
    if PIDS_FILE.exists():
        for line in PIDS_FILE.read_text().splitlines():
            parts = line.strip().split()
            if len(parts) == 2:
                name, pid_str = parts
                try:
                    pids[name] = int(pid_str)
                except ValueError:
                    pass

    # 各 agent 独立 pid 文件：logs/.pids/<name>.pid
    if AGENT_PIDS_DIR.exists():
        for pid_file in AGENT_PIDS_DIR.glob("*.pid"):
            name = pid_file.stem
            try:
                pids[name] = int(pid_file.read_text().strip())
            except (ValueError, OSError):
                pass

    return pids


def _send_usr1(process_name: str) -> bool:
    """向指定进程发送 SIGUSR1，返回是否成功。"""
    pids = _read_pids()
    pid = pids.get(process_name)
    if pid is None:
        log.warning("PID not found for process: %s", process_name)
        return False
    try:
        os.kill(pid, signal.SIGUSR1)
        log.info("SIGUSR1 → %s (pid=%d)", process_name, pid)
        return True
    except ProcessLookupError:
        log.warning("process %s (pid=%d) not running", process_name, pid)
        return False
    except PermissionError:
        log.error("no permission to signal %s (pid=%d)", process_name, pid)
        return False


# ── 监听规则 ──────────────────────────────────────────────────────────────────

def _agent_name_from_path(path: Path) -> str | None:
    """从 agents_v2/<name>/prompts.py 提取 agent 名。"""
    parts = path.parts
    try:
        idx = parts.index("agents_v2")
        if idx + 2 < len(parts) and parts[idx + 2] == "prompts.py":
            return parts[idx + 1]
    except ValueError:
        pass
    return None


class _HotReloadHandler(FileSystemEventHandler):
    """统一文件变更处理器，根据路径决定通知哪个进程。"""

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path).resolve()
        self._dispatch(path)

    # watchdog 有时只发 created（编辑器原子写），一并处理
    on_created = on_modified

    def _dispatch(self, path: Path):
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        name = str(rel)

        # feishu group_chat 层
        if "group_chat" in name and name.endswith(".py"):
            if "scenarios" in name or "pipelines" in name or "prompts" in name:
                _send_usr1("orchestrator")
                return

        # agents_v2 提示词
        agent = _agent_name_from_path(path)
        if agent:
            _send_usr1(agent)
            return


# ── 启动监听 ──────────────────────────────────────────────────────────────────

def _start(watch_paths: list[Path]):
    """启动 Observer，监听所有指定目录。"""
    handler = _HotReloadHandler()
    obs = Observer()

    for p in watch_paths:
        if p.exists():
            obs.schedule(handler, path=str(p), recursive=True)
            log.info("watching: %s", p)
        else:
            log.warning("path not found, skipping: %s", p)

    obs.start()
    log.info("hot_reload daemon ready (pid=%d)", os.getpid())

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        obs.stop()
        obs.join()
        log.info("hot_reload daemon stopped")


def main():
    parser = argparse.ArgumentParser(description="热更新守护进程")
    parser.add_argument("--daemon", action="store_true", help="后台运行（nohup）")
    args = parser.parse_args()

    watch_paths = [
        ROOT / "feishu" / "group_chat",
        ROOT / "agents_v2",
    ]

    if args.daemon:
        # 简单 fork 到后台
        pid = os.fork()
        if pid > 0:
            print(f"hot_reload daemon started (pid={pid})")
            sys.exit(0)
        os.setsid()

    _start(watch_paths)


if __name__ == "__main__":
    main()
