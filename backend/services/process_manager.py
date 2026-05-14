"""
Per-employee process lifecycle manager.

Owns starting/stopping the two processes per employee:
  - agent server (HTTP on cfg.agent_port)
  - feishu bot (no HTTP — pure WebSocket consumer)

PIDs persist to logs/.pids/<role>_<employee>.pid for cross-restart visibility.
"""
from __future__ import annotations

import contextlib
import logging
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from backend.services import registry

log = logging.getLogger("backend.services.process_manager")

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = ROOT / "logs"
PID_DIR = LOG_DIR / ".pids"
PID_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

VENV_PY = ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PY) if VENV_PY.exists() else "python"

# Legacy per-employee main.py existed for these (so we keep using it).
# Newly added employees use the generic entry point automatically.
_LEGACY_AGENT_KEYS = {
    "tech_lead", "mechanical", "hardware", "firmware", "algorithm",
    "product_manager", "testing", "cost", "project_manager", "sysadmin",
}


@dataclass
class ProcessStatus:
    employee: str
    role: str            # "agent" | "bot"
    pid: int | None
    running: bool
    port: int | None = None
    listening: bool = False


def _pid_file(employee: str, role: str) -> Path:
    return PID_DIR / f"{role}_{employee}.pid"


def _log_file(employee: str, role: str) -> Path:
    return LOG_DIR / (f"{employee}.log" if role == "agent" else f"bot_{employee}.log")


def _read_pid(p: Path) -> int | None:
    try:
        return int(p.read_text().strip()) if p.exists() else None
    except Exception:
        return None


def _is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _port_listening(port: int) -> bool:
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _spawn(cmd: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(log_path, "a")
    proc = subprocess.Popen(
        cmd, stdout=f, stderr=subprocess.STDOUT,
        cwd=str(ROOT), start_new_session=True,
    )
    return proc.pid


# ── Public API ───────────────────────────────────────────────────────────────

def _warmed_cfg(employee: str):
    if not registry._loaded:  # type: ignore[attr-defined]
        try:
            registry.warmup_sync()
        except RuntimeError:
            pass
    return registry.get_effective_sync(employee)


def status(employee: str) -> dict:
    cfg = _warmed_cfg(employee)
    agent_pid = _read_pid(_pid_file(employee, "agent"))
    bot_pid   = _read_pid(_pid_file(employee, "bot"))
    listening = _port_listening(cfg.agent_port) if cfg and cfg.agent_port else False
    # An agent is "running" if either our PID is alive OR the port is occupied
    # (the latter handles processes started outside ProcessManager — e.g. start.sh).
    agent_running = _is_alive(agent_pid) or listening

    return {
        "employee": employee,
        "agent": {
            "pid": agent_pid,
            "running": agent_running,
            "port": cfg.agent_port if cfg else None,
            "listening": listening,
        },
        "bot": {
            "pid": bot_pid,
            "running": _is_alive(bot_pid),
            "has_credentials": bool(cfg and cfg.feishu_app_id),
        },
    }


def start_agent(employee: str) -> dict:
    cfg = _warmed_cfg(employee)
    if not cfg or not cfg.active:
        return {"ok": False, "error": "employee not active"}
    if not cfg.agent_port:
        return {"ok": False, "error": "no agent_port"}

    existing = _read_pid(_pid_file(employee, "agent"))
    if _is_alive(existing):
        return {"ok": True, "pid": existing, "already_running": True}

    if _port_listening(cfg.agent_port):
        return {"ok": False, "error": f"port {cfg.agent_port} occupied by another process"}

    if employee in _LEGACY_AGENT_KEYS:
        cmd = [PYTHON, "-m", f"agents_v2.{employee}.main"]
        env_extra = {}
    else:
        cmd = [PYTHON, "-m", "agents_v2.generic.main"]
        env_extra = {"EMPLOYEE_KEY": employee}

    log_path = _log_file(employee, "agent")
    f = open(log_path, "a")
    env = {**os.environ, **env_extra}
    proc = subprocess.Popen(
        cmd, stdout=f, stderr=subprocess.STDOUT,
        cwd=str(ROOT), env=env, start_new_session=True,
    )
    _pid_file(employee, "agent").write_text(str(proc.pid))
    log.info("started agent %s pid=%d port=%d", employee, proc.pid, cfg.agent_port)

    # Wait briefly for the port to come up so caller can verify.
    for _ in range(20):
        if _port_listening(cfg.agent_port):
            break
        time.sleep(0.25)
    return {"ok": True, "pid": proc.pid, "port": cfg.agent_port,
            "listening": _port_listening(cfg.agent_port)}


def start_bot(employee: str) -> dict:
    cfg = _warmed_cfg(employee)
    if not cfg or not cfg.active:
        return {"ok": False, "error": "employee not active"}
    if not cfg.feishu_app_id:
        return {"ok": False, "error": "no feishu credentials"}

    existing = _read_pid(_pid_file(employee, "bot"))
    if _is_alive(existing):
        return {"ok": True, "pid": existing, "already_running": True}

    pid = _spawn([PYTHON, "-m", "feishu.employee_bot", employee], _log_file(employee, "bot"))
    _pid_file(employee, "bot").write_text(str(pid))
    log.info("started bot %s pid=%d", employee, pid)
    return {"ok": True, "pid": pid}


def stop_role(employee: str, role: str, timeout: float = 5.0) -> dict:
    """role: 'agent' | 'bot'"""
    pid = _read_pid(_pid_file(employee, role))
    if not pid or not _is_alive(pid):
        with contextlib.suppress(FileNotFoundError):
            _pid_file(employee, role).unlink()
        return {"ok": True, "stopped": False, "reason": "not running"}

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    deadline = time.time() + timeout
    while _is_alive(pid) and time.time() < deadline:
        time.sleep(0.1)

    if _is_alive(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)

    with contextlib.suppress(FileNotFoundError):
        _pid_file(employee, role).unlink()
    log.info("stopped %s %s pid=%d", role, employee, pid)
    return {"ok": True, "stopped": True, "pid": pid}


def stop(employee: str) -> dict:
    return {
        "agent": stop_role(employee, "agent"),
        "bot":   stop_role(employee, "bot"),
    }


def start(employee: str) -> dict:
    return {
        "agent": start_agent(employee),
        "bot":   start_bot(employee),
    }


def restart(employee: str) -> dict:
    stop(employee)
    time.sleep(0.5)
    return start(employee)
