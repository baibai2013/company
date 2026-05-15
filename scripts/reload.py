"""
手动热更新命令行工具。

处理 hot_reload.py 守护进程无法自动处理的"硬边界"场景：
  - models     修改 models.py 数据结构（清空内存态 + reload）
  - session    修改 session.py 序列化格式（清空 Redis sessions + reload）
  - eventbus   修改 event_bus.py（重连 orchestrator 的 Redis 订阅）
  - scenario   新增 Scenario 文件（自动扫描注册 + reload）
  - all        一键执行以上全部

用法：
  python scripts/reload.py models
  python scripts/reload.py session
  python scripts/reload.py eventbus
  python scripts/reload.py scenario
  python scripts/reload.py all
  python scripts/reload.py all --dry-run     # 只打印操作，不执行
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIDS_FILE = ROOT / ".pids"
AGENT_PIDS_DIR = ROOT / "logs" / ".pids"
SCENARIOS_DIR = ROOT / "feishu" / "group_chat" / "scenarios"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  reload  %(message)s",
)
log = logging.getLogger("reload")


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _read_pids() -> dict[str, int]:
    """读取所有进程 PID。"""
    pids: dict[str, int] = {}
    if PIDS_FILE.exists():
        for line in PIDS_FILE.read_text().splitlines():
            parts = line.strip().split()
            if len(parts) == 2:
                try:
                    pids[parts[0]] = int(parts[1])
                except ValueError:
                    pass
    if AGENT_PIDS_DIR.exists():
        for f in AGENT_PIDS_DIR.glob("*.pid"):
            try:
                pids[f.stem] = int(f.read_text().strip())
            except (ValueError, OSError):
                pass
    return pids


def _send_usr1(name: str, dry_run: bool = False) -> bool:
    """向进程发送 SIGUSR1。"""
    pids = _read_pids()
    pid = pids.get(name)
    if pid is None:
        log.warning("[skip] PID not found: %s", name)
        return False
    if dry_run:
        log.info("[dry-run] would send SIGUSR1 → %s (pid=%d)", name, pid)
        return True
    try:
        os.kill(pid, signal.SIGUSR1)
        log.info("SIGUSR1 → %s (pid=%d)", name, pid)
        return True
    except ProcessLookupError:
        log.warning("[skip] process not running: %s (pid=%d)", name, pid)
        return False


def _send_sigusr2(name: str, dry_run: bool = False) -> bool:
    """向进程发送 SIGUSR2（触发重连）。"""
    pids = _read_pids()
    pid = pids.get(name)
    if pid is None:
        log.warning("[skip] PID not found: %s", name)
        return False
    if dry_run:
        log.info("[dry-run] would send SIGUSR2 → %s (pid=%d)", name, pid)
        return True
    try:
        os.kill(pid, signal.SIGUSR2)
        log.info("SIGUSR2 → %s (pid=%d)", name, pid)
        return True
    except ProcessLookupError:
        log.warning("[skip] process not running: %s (pid=%d)", name, pid)
        return False


def _redis_client():
    """返回 redis.Redis 同步客户端。"""
    import redis
    return redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)


def _flush_redis_sessions(dry_run: bool = False) -> int:
    """清空 Redis 中所有 group_session:* 键。返回删除数量。"""
    r = _redis_client()
    keys = list(r.scan_iter("group_session:*"))
    if not keys:
        log.info("no active sessions in Redis")
        return 0
    if dry_run:
        log.info("[dry-run] would delete %d session keys: %s", len(keys), keys[:5])
        return len(keys)
    r.delete(*keys)
    log.info("deleted %d session keys from Redis", len(keys))
    return len(keys)


def _flush_redis_channels(dry_run: bool = False) -> int:
    """清空 Redis 中滞留的 speak_req/speak_resp 消息（可选）。"""
    r = _redis_client()
    keys = list(r.scan_iter("speak_req:*")) + list(r.scan_iter("speak_resp:*"))
    if not keys:
        return 0
    if dry_run:
        log.info("[dry-run] would delete %d stale channel keys", len(keys))
        return len(keys)
    r.delete(*keys)
    log.info("deleted %d stale channel keys", len(keys))
    return len(keys)


# ── 各场景处理 ────────────────────────────────────────────────────────────────

def reload_models(dry_run: bool = False):
    """热更 models.py：清空内存态 session → reload → 通知 orchestrator。

    适用场景：给 GroupSession / ConversationMessage 等 dataclass 加字段（有默认值）。
    不适用：删除字段、改字段类型（可能导致反序列化失败）。
    """
    log.info("=== reload: models ===")

    # 1. 清空 Redis sessions（清除旧格式数据，保证下次反序列化用新格式）
    _flush_redis_sessions(dry_run)

    # 2. SIGUSR1 → orchestrator（触发 importlib.reload(models + session)）
    _send_usr1("orchestrator", dry_run)

    log.info("models reload done — next session will use new field definitions")


def reload_session(dry_run: bool = False):
    """热更 session.py：清空 Redis sessions → reload → 通知 orchestrator。

    适用场景：修改 _serialize / _deserialize 逻辑、调整 TTL 等。
    """
    log.info("=== reload: session ===")

    # 清空旧格式 session，避免新代码反序列化失败
    _flush_redis_sessions(dry_run)

    # SIGUSR1 → orchestrator
    _send_usr1("orchestrator", dry_run)

    log.info("session reload done")


def reload_eventbus(dry_run: bool = False):
    """热更 event_bus.py：清理滞留消息 → SIGUSR2 触发 orchestrator 重连。

    适用场景：修改 Redis channel 名、消息格式、订阅逻辑。
    orchestrator 接到 SIGUSR2 后：关闭当前订阅 → reload event_bus → 重新订阅。

    注意：重连期间（约 1-2 秒）新消息会被丢弃。
    """
    log.info("=== reload: eventbus ===")

    # 清理滞留的 speak_req/resp 消息（避免新连接处理旧格式消息）
    _flush_redis_channels(dry_run)

    # SIGUSR2 → orchestrator（需要 orchestrator 注册 SIGUSR2 处理器）
    _send_sigusr2("orchestrator", dry_run)

    log.info("eventbus reload triggered — orchestrator will reconnect in ~2s")


def reload_scenario(dry_run: bool = False):
    """扫描 scenarios/ 目录，自动注册新发现的 Scenario 文件并热重载。

    适用场景：新增了 scenarios/xxx.py 但忘记在 __init__.py 里注册。
    会自动发现、import、更新 SCENARIO_REGISTRY，然后通知 orchestrator。
    """
    log.info("=== reload: scenario ===")

    # 扫描 scenarios/ 下所有 *.py（排除 base.py 和 __init__.py）
    found: list[str] = []
    for f in SCENARIOS_DIR.glob("*.py"):
        if f.stem in ("__init__", "base"):
            continue
        mod_name = f"group_chat.scenarios.{f.stem}"
        found.append(mod_name)

    log.info("found scenario files: %s", [f.stem for f in SCENARIOS_DIR.glob("*.py")
                                           if f.stem not in ("__init__", "base")])

    if dry_run:
        log.info("[dry-run] would import/reload: %s", found)
        _send_usr1("orchestrator", dry_run)
        return

    # 动态 import / reload 各 scenario 模块（@register 装饰器重新执行）
    for mod_name in found:
        try:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
                log.info("reloaded: %s", mod_name)
            else:
                importlib.import_module(mod_name)
                log.info("imported new: %s", mod_name)
        except Exception as exc:
            log.warning("failed to load %s: %s", mod_name, exc)

    # SIGUSR1 → orchestrator（让运行中的进程也重载 scenarios）
    _send_usr1("orchestrator", dry_run)

    # 打印当前注册表（仅本进程可见，orchestrator 侧由 SIGUSR1 触发）
    try:
        sys.path.insert(0, str(ROOT))
        from group_chat.scenarios.base import SCENARIO_REGISTRY
        log.info("SCENARIO_REGISTRY now: %s", list(SCENARIO_REGISTRY.keys()))
    except Exception:
        pass

    log.info("scenario reload done")


def reload_all(dry_run: bool = False):
    """执行全部热更新操作（models + session + eventbus + scenario）。"""
    log.info("=== reload: all ===")
    reload_models(dry_run)
    time.sleep(0.2)
    reload_session(dry_run)
    time.sleep(0.2)
    reload_eventbus(dry_run)
    time.sleep(0.2)
    reload_scenario(dry_run)
    log.info("=== reload: all done ===")


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

COMMANDS = {
    "models": reload_models,
    "session": reload_session,
    "eventbus": reload_eventbus,
    "scenario": reload_scenario,
    "all": reload_all,
}


def main():
    parser = argparse.ArgumentParser(
        description="手动热更新工具，处理自动热更无法覆盖的边界场景",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令说明：
  models    修改 models.py 后用（清空 session + reload）
  session   修改 session.py 后用（清空 session + reload）
  eventbus  修改 event_bus.py 后用（清理 channel + orchestrator 重连）
  scenario  新增 Scenario 文件后用（自动发现 + 注册 + reload）
  all       以上全部执行
        """,
    )
    parser.add_argument("command", choices=list(COMMANDS.keys()), help="要执行的热更类型")
    parser.add_argument("--dry-run", action="store_true", help="只打印操作，不实际执行")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))

    fn = COMMANDS[args.command]
    fn(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
