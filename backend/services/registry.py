"""
Employee + global config registry.

Single source of truth for all employee/system configuration.
- In-memory cache, refreshed on PG NOTIFY 'config_changed'
- Returns "effective config" (employee value falls back to global default)
- Compat helpers for legacy EMPLOYEE_CONFIG / ROLE_DESCRIPTIONS dicts

Usage (async):
    cfg = await get_effective("mechanical")
    cfg.system_prompt        # str
    cfg.llm_calls["chat"]    # dict — model is guaranteed non-null
    cfg.global_prompts["decide_prompt"]
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
from dataclasses import dataclass
from typing import Any

from backend.core.config import settings

from backend.repos import config_repo, employee_repo

log = logging.getLogger("backend.services.registry")

# ── In-memory cache ───────────────────────────────────────────────────────────

_employees: dict[str, dict] = {}       # key -> employee dict
_global: dict[str, Any] = {}           # full system_config dict
_loaded = False
_lock = asyncio.Lock()
_listener_task: asyncio.Task | None = None
_change_hooks: list = []               # (key_or_None) -> None async callbacks


# ── Effective config dataclass ────────────────────────────────────────────────

@dataclass
class EffectiveConfig:
    """Merged employee + global config; what actual code consumes."""

    key: str
    name: str
    emoji: str
    role_desc: str
    feishu_app_id: str
    feishu_app_secret: str
    agent_port: int | None
    active: bool
    system_prompt: str
    persona: dict
    llm_calls: dict           # 7 call types, model always non-null
    behavior: dict
    global_prompts: dict      # for nodes that need shared prompts


# ── Loading / merging ─────────────────────────────────────────────────────────

def _merge_llm_calls(employee_calls: dict | None, default_models: dict) -> dict:
    """Each call inherits global default model when null/missing."""
    employee_calls = employee_calls or {}
    out = {}
    for call_type in ("route", "chat", "plan", "execute", "group_speak", "cc", "summary"):
        emp = (employee_calls.get(call_type) or {}).copy()
        if not emp.get("model"):
            emp["model"] = default_models.get(call_type)
        out[call_type] = emp
    return out


def _to_effective(emp: dict, glob: dict) -> EffectiveConfig:
    return EffectiveConfig(
        key=emp["key"],
        name=emp.get("name") or "",
        emoji=emp.get("emoji") or "",
        role_desc=emp.get("role_desc") or "",
        feishu_app_id=emp.get("feishu_app_id") or "",
        feishu_app_secret=emp.get("feishu_app_secret") or "",
        agent_port=emp.get("agent_port"),
        active=emp.get("active", True),
        system_prompt=emp.get("system_prompt") or "",
        persona=emp.get("persona") or {},
        llm_calls=_merge_llm_calls(emp.get("llm_calls"), glob.get("default_models") or {}),
        behavior=emp.get("behavior") or {},
        global_prompts=glob.get("global_prompts") or {},
    )


async def _load_all() -> None:
    """Reload employees + global config from DB into cache.

    使用 NullPool 引擎（不缓存连接），避免与 uvicorn 主事件 loop 产生
    "Future attached to a different loop" 冲突。
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from sqlalchemy import select as _select
    from backend.models.employee import Employee as _Employee, SystemConfig as _SysCfg

    _eng = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
    _Session = async_sessionmaker(_eng, expire_on_commit=False)
    try:
        async with _Session() as s:
            emp_rows = list((await s.execute(
                _select(_Employee).order_by(_Employee.agent_port)
            )).scalars().all())
            cfg_row = (await s.execute(_select(_SysCfg).limit(1))).scalars().first()
    finally:
        await _eng.dispose()

    rows = emp_rows
    cfg = cfg_row
    glob = {
        "default_models": cfg.default_models or {} if cfg else {},
        "global_prompts": cfg.global_prompts or {} if cfg else {},
        "system":         cfg.system or {} if cfg else {},
    }
    new_emp = {emp.key: employee_repo.to_dict(emp) for emp in rows}
    global _employees, _global, _loaded
    _employees = new_emp
    _global = glob
    _loaded = True
    log.info("registry loaded: %d employees, global ver=%s",
             len(new_emp), cfg.version if cfg else "n/a")


async def _ensure_loaded() -> None:
    if _loaded:
        return
    async with _lock:
        if not _loaded:
            await _load_all()


# ── Public read API ───────────────────────────────────────────────────────────

async def get_effective(key: str) -> EffectiveConfig | None:
    await _ensure_loaded()
    emp = _employees.get(key)
    if not emp:
        return None
    return _to_effective(emp, _global)


# ── Synchronous read API (requires warmup) ────────────────────────────────────

def warmup_sync() -> None:
    """Block-load the cache once at process start. Safe to call multiple times.

    asyncio.run() 会创建临时 event loop，其中生成的 asyncpg 连接会留在 SQLAlchemy
    连接池里。uvicorn 启动后用自己的 loop 时会报 "Future attached to a different loop"。
    修复：warmup 完成后立即同步 dispose 连接池，让 uvicorn loop 重建连接。
    """
    if _loaded:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        # _load_all() 内部用 NullPool（不缓存连接），无 "different loop" 风险
        asyncio.run(_load_all())
    else:
        # Inside running loop — caller must await warmup() instead.
        raise RuntimeError("warmup_sync() called inside event loop; use 'await warmup()'")


async def warmup() -> None:
    await _ensure_loaded()


def get_sync(key: str) -> dict | None:
    """Synchronous raw read. Requires warmup() to have run."""
    return copy.deepcopy(_employees.get(key))


def get_effective_sync(key: str) -> EffectiveConfig | None:
    """Synchronous merged read. Requires warmup() to have run."""
    emp = _employees.get(key)
    if not emp:
        return None
    return _to_effective(emp, _global)


def list_keys_sync_cached(active_only: bool = True) -> list[str]:
    """Synchronous keys list — uses warm cache, doesn't touch DB."""
    items = list(_employees.values())
    if active_only:
        items = [e for e in items if e.get("active")]
    return [e["key"] for e in items]


def employee_config_compat_sync() -> dict[str, tuple[str, str]]:
    return {k: (e.get("emoji", "👤"), e.get("name", k)) for k, e in _employees.items()}


def role_descriptions_compat_sync() -> dict[str, str]:
    return {k: e.get("role_desc", "") for k, e in _employees.items()}


def global_sync() -> dict:
    return copy.deepcopy(_global)


async def get_raw(key: str) -> dict | None:
    await _ensure_loaded()
    return copy.deepcopy(_employees.get(key))


async def list_all(active_only: bool = False) -> list[dict]:
    await _ensure_loaded()
    items = list(_employees.values())
    if active_only:
        items = [e for e in items if e.get("active")]
    return [copy.deepcopy(e) for e in items]


async def list_keys(active_only: bool = True) -> list[str]:
    items = await list_all(active_only=active_only)
    return [e["key"] for e in items]


async def get_global() -> dict:
    await _ensure_loaded()
    return copy.deepcopy(_global)


# ── Compat helpers (legacy EMPLOYEE_CONFIG / ROLE_DESCRIPTIONS shape) ─────────

async def employee_config_compat() -> dict[str, tuple[str, str]]:
    """{key: (emoji, name)} — legacy shape used across feishu/."""
    items = await list_all(active_only=False)
    return {e["key"]: (e.get("emoji", "👤"), e.get("name", e["key"])) for e in items}


async def role_descriptions_compat() -> dict[str, str]:
    items = await list_all(active_only=False)
    return {e["key"]: e.get("role_desc", "") for e in items}


# ── Sync top-level helpers (spawn their own event loop — script use only) ────

def list_keys_sync(active_only: bool = True) -> list[str]:
    return asyncio.run(list_keys(active_only=active_only))


# ── Mutation API (writes audit + invalidates cache) ───────────────────────────

async def update(key: str, patch: dict, actor: str = "api") -> dict | None:
    """Update employee fields. Returns the updated dict, or None if missing."""
    from backend.repos import audit_repo
    existing = await employee_repo.get(key)
    if not existing:
        return None
    old = employee_repo.to_dict(existing)
    emp = await employee_repo.update_fields(key, patch)
    new = employee_repo.to_dict(emp)
    for field, value in patch.items():
        await audit_repo.write(
            actor=actor, action="update", target_type="employee",
            target_key=key, field_path=field,
            old_value=_jsonable(old.get(field)), new_value=_jsonable(value),
        )
    await invalidate(key)
    return new


async def create(record: dict, actor: str = "api") -> dict:
    from backend.repos import audit_repo
    emp = await employee_repo.create(record)
    await audit_repo.write(
        actor=actor, action="create", target_type="employee",
        target_key=emp.key, new_value=_jsonable(employee_repo.to_dict(emp)),
    )
    await invalidate(emp.key)
    return employee_repo.to_dict(emp)


async def deactivate(key: str, actor: str = "api") -> bool:
    from backend.repos import audit_repo
    ok = await employee_repo.deactivate(key)
    if ok:
        await audit_repo.write(
            actor=actor, action="deactivate", target_type="employee", target_key=key,
        )
        await invalidate(key)
    return ok


async def update_global(patch: dict, actor: str = "api") -> dict:
    from backend.repos import audit_repo
    cfg_before = await config_repo.get()
    old = config_repo.to_dict(cfg_before) if cfg_before else {}
    cfg = await config_repo.update_fields(patch)
    new = config_repo.to_dict(cfg)
    for field, value in patch.items():
        await audit_repo.write(
            actor=actor, action="update", target_type="system_config",
            target_key="global", field_path=field,
            old_value=_jsonable(old.get(field)), new_value=_jsonable(value),
        )
    await invalidate(None)
    return new


def _jsonable(v):
    """Make any value safe for JSONB column."""
    if v is None or isinstance(v, (str, int, float, bool, list, dict)):
        return v
    return str(v)


# ── Cache invalidation (manual + LISTEN/NOTIFY) ───────────────────────────────

def register_change_hook(fn) -> None:
    """注册配置变更回调。fn(key) 在 PG NOTIFY 或手动 invalidate 后被调用。"""
    _change_hooks.append(fn)


async def invalidate(key: str | None) -> None:
    """Reload the cache immediately, then call change hooks."""
    await _load_all()  # 主动刷新，使 get_effective_sync 也能读到最新数据
    log.debug("registry cache invalidated and reloaded (key=%s)", key)
    for hook in list(_change_hooks):
        try:
            await hook(key)
        except Exception as e:
            log.warning("change hook error: %s", e)


async def _listen_loop() -> None:
    """Background task: subscribe to PG 'config_changed' channel.

    同步 psycopg 在线程池里运行 LISTEN loop，避免 asyncpg 跨 loop Future 冲突。
    通过 asyncio.run_coroutine_threadsafe 将 invalidate 调度回主 event loop。
    """
    loop = asyncio.get_running_loop()
    dsn = (
        settings.database_url_sync
        .replace("postgresql+psycopg://", "")
        .replace("postgresql+asyncpg://", "")
        # 还原成标准 postgresql:// DSN
    )
    # 从 settings 直接构造同步 DSN
    raw_dsn = (
        f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/company_app"
    )

    import threading

    stop_event = threading.Event()

    def _sync_listen():
        """同步阻塞式 LISTEN，运行在独立线程中。"""
        while not stop_event.is_set():
            try:
                import psycopg
                with psycopg.connect(raw_dsn, autocommit=True) as conn:
                    conn.execute("LISTEN config_changed")
                    log.warning("registry LISTEN config_changed connected (psycopg sync thread)")
                    # 无超时阻塞等待，daemon 线程随进程退出自然终止
                    for notify in conn.notifies():
                        if stop_event.is_set():
                            break
                        try:
                            key = json.loads(notify.payload).get("key")
                        except Exception:
                            key = None
                        log.warning("registry NOTIFY: %s", notify.payload)
                        asyncio.run_coroutine_threadsafe(invalidate(key), loop)
            except Exception as e:
                if not stop_event.is_set():
                    log.warning("LISTEN sync thread error: %s — retry in 5s", e)
                    stop_event.wait(5)

    t = threading.Thread(target=_sync_listen, daemon=True, name="registry-listen-sync")
    t.start()
    try:
        # 等待直到 CancelledError（lifespan 结束）
        while t.is_alive():
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        stop_event.set()
        raise


def start_listener() -> asyncio.Task:
    """Spawn the LISTEN background task (idempotent)."""
    global _listener_task
    if _listener_task and not _listener_task.done():
        return _listener_task
    _listener_task = asyncio.create_task(_listen_loop(), name="registry-listener")
    return _listener_task


async def stop_listener() -> None:
    global _listener_task
    if _listener_task:
        _listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _listener_task
        _listener_task = None
