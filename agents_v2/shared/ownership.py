"""路径归属解析。员工 A 想改某个文件时，查这里得到归属员工 key，然后用
delegate_to_employee 工具委托。

设计意图见 doc/design/employee-claude-code-backend.md 阶段 6.5。

数据表：path_ownership(path_pattern, employee_key, priority)
- path_pattern：fnmatch glob，如 'employees/firmware/**'
- priority：高的胜出（'**' 兜底归 sysadmin priority=0）

性能：进程内缓存全表，启动时一次性加载；后续支持 PG NOTIFY 失效（暂未实现，
通过手动 invalidate 接口或重启刷新）。
"""
from __future__ import annotations

import fnmatch
import logging
import threading
from pathlib import Path

import psycopg

from backend.core.config import settings

log = logging.getLogger("agents_v2.ownership")

_lock = threading.RLock()
_cache: list[tuple[str, str, int]] = []  # [(pattern, employee_key, priority), ...]
_loaded = False


def _load() -> None:
    """从 DB 拉 path_ownership 全表到内存。"""
    global _cache, _loaded
    dsn = settings.database_url_sync.replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT path_pattern, employee_key, priority FROM path_ownership ORDER BY priority DESC"
                )
                rows = cur.fetchall()
        with _lock:
            _cache = [(p, e, pr) for p, e, pr in rows]
            _loaded = True
        log.info("path_ownership loaded: %d rules", len(_cache))
    except Exception as exc:
        log.warning("path_ownership 加载失败：%s（按兜底 sysadmin 处理）", exc)
        with _lock:
            _cache = [("**", "sysadmin", 0)]
            _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load()


def _normalize(path: str) -> str:
    """绝对路径转项目相对路径，保留相对路径不变。无法相对化的原样返回。"""
    p = Path(path)
    if p.is_absolute():
        try:
            project_root = Path("/Users/liyijiang/work/company")
            return str(p.relative_to(project_root))
        except ValueError:
            return str(p)
    return str(p)


def resolve_owner(path: str) -> str | None:
    """返回拥有该路径的员工 key。多匹配时按 priority 高的胜。"""
    _ensure_loaded()
    norm = _normalize(path)
    best_key: str | None = None
    best_priority = -1
    with _lock:
        for pattern, key, priority in _cache:
            if fnmatch.fnmatch(norm, pattern):
                if priority > best_priority:
                    best_priority = priority
                    best_key = key
    return best_key


def list_all_owners() -> dict[str, list[str]]:
    """返回 {employee_key: [path_pattern, ...]}，给 CLAUDE.md 渲染同事范围用。"""
    _ensure_loaded()
    out: dict[str, list[str]] = {}
    with _lock:
        for pattern, key, _priority in _cache:
            out.setdefault(key, []).append(pattern)
    return out


def invalidate() -> None:
    """主动失效缓存（path_ownership 表更新后调用）。"""
    global _loaded
    with _lock:
        _loaded = False
