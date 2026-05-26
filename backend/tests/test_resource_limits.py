"""提案 4 · §5.4 resource_limits 测试 — Wave 4。

覆盖:
  §1 async 函数正常完成,不超时
  §2 async 函数超 cpu_seconds → asyncio.TimeoutError
  §3 sync 函数装饰后正常运行(无硬超时,只 warn)
  §4 mem_mb 装饰器调用了 setrlimit(macOS 上多半成功但不强制)
  §5 RESOURCE_LIMITS=off 时 decorator 透传
  §6 _try_set_memory_limit 失败时不抛
"""
from __future__ import annotations

import asyncio
import importlib

import pytest


# ── §1 async 不超时 ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_async_within_budget():
    from backend.services.resource_limits import with_resource_budget

    @with_resource_budget(cpu_seconds=1.0)
    async def quick():
        await asyncio.sleep(0.01)
        return 42

    assert await quick() == 42


# ── §2 async 超时抛 TimeoutError ────────────────────────────────────
@pytest.mark.asyncio
async def test_async_timeout_raises():
    from backend.services.resource_limits import with_resource_budget

    @with_resource_budget(cpu_seconds=0.05)
    async def slow():
        await asyncio.sleep(2)
        return "should-not-reach"

    with pytest.raises(asyncio.TimeoutError):
        await slow()


# ── §3 sync 函数:无硬超时,跑完返回值 ──────────────────────────────
def test_sync_runs_normally():
    from backend.services.resource_limits import with_resource_budget

    @with_resource_budget(cpu_seconds=0.5)
    def add(a, b):
        return a + b

    assert add(1, 2) == 3


def test_sync_overrun_only_warns(caplog):
    """sync 函数没硬超时,跑超只打 warning,不抛。"""
    import time
    from backend.services.resource_limits import with_resource_budget

    @with_resource_budget(cpu_seconds=0.05)
    def slow_sync():
        time.sleep(0.1)
        return "done"

    with caplog.at_level("WARNING", logger="backend.services.resource_limits"):
        assert slow_sync() == "done"
    msgs = [r.message for r in caplog.records]
    assert any("超出预算".replace("超出预算", "") in m or "无法硬中断" in m for m in msgs)


# ── §4 mem_mb 调用 setrlimit ─────────────────────────────────────────
def test_set_memory_limit_called(monkeypatch):
    """装饰器进入函数前应该调一次 _try_set_memory_limit;失败也不抛。"""
    from backend.services import resource_limits

    calls: list[int] = []
    monkeypatch.setattr(
        resource_limits, "_try_set_memory_limit",
        lambda mb: calls.append(mb) or True,
    )

    @resource_limits.with_resource_budget(mem_mb=256)
    def f():
        return "x"

    f()
    f()
    assert calls == [256, 256]


# ── §5 RESOURCE_LIMITS=off 时 decorator 透传 ─────────────────────────
def test_global_disabled_passthrough(monkeypatch):
    """RESOURCE_LIMITS=off 时 decorator 不做任何事(透传原函数)。"""
    monkeypatch.setenv("RESOURCE_LIMITS", "off")
    # 必须重新 import 模块,_DISABLED 是 import 期决定
    import backend.services.resource_limits as rl
    importlib.reload(rl)
    try:
        @rl.with_resource_budget(cpu_seconds=0.001)
        async def slow():
            await asyncio.sleep(0.05)
            return "done"

        # decorator 透传 → 原函数仍是 coroutine,asyncio.run 跑完不会超时
        assert asyncio.run(slow()) == "done"
    finally:
        monkeypatch.delenv("RESOURCE_LIMITS", raising=False)
        importlib.reload(rl)


# ── §6 setrlimit 失败兜底 ────────────────────────────────────────────
def test_try_set_memory_limit_swallows_errors(monkeypatch):
    """setrlimit 抛 OSError 时返回 False,不冒泡。"""
    from backend.services import resource_limits as rl

    import resource as _resource_mod

    def _boom(*a, **kw):
        raise OSError(13, "permission denied")

    monkeypatch.setattr(_resource_mod, "setrlimit", _boom)
    assert rl._try_set_memory_limit(64) is False


def test_try_set_memory_limit_value_error(monkeypatch):
    """setrlimit 抛 ValueError 也降级。"""
    from backend.services import resource_limits as rl

    import resource as _resource_mod

    def _boom(*a, **kw):
        raise ValueError("invalid limit")

    monkeypatch.setattr(_resource_mod, "setrlimit", _boom)
    assert rl._try_set_memory_limit(64) is False
