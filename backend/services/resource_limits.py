"""提案 4 · §5.4 沙箱与资源边界 — 进程级资源边界 decorator(Wave 4)。

给 ``claude_pool`` 等"被外部输入驱动"的子进程入口用,提供:
  - cpu 时长上限:基于 ``asyncio.wait_for`` 的 wallclock 超时(也叫 cpu_seconds,
    本实现等同 wallclock,真正的 CPU-time 边界等 sandbox 后端拿到 cgroup 才能测)
  - 内存上限:用 ``resource.setrlimit(RLIMIT_AS)`` 给当前进程加;
    macOS 上 RLIMIT_AS 行为与 Linux 不一致(setrlimit 多半成功但实际不强制
    虚拟内存),失败时 try/except 降级 + log.warning,**不抛**

用法::

    @with_resource_budget(cpu_seconds=30, mem_mb=512)
    async def dangerous_call(...):
        ...

    @with_resource_budget(cpu_seconds=10)
    def sync_func(...):
        ...

边界哲学:超时只对装饰的函数生效,内部 spawn 的子进程不会自动继承
(子进程要走 ``sandbox.run_sandboxed``)。本模块刻意不把 setrlimit 放
子进程,因为对父 Python 进程改 RLIMIT_AS 风险大,改子进程要在
preexec_fn 里调,与本 decorator 的目的(包装函数调用)正交。
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

log = logging.getLogger(__name__)

# 全局开关:测试用 RESOURCE_LIMITS=off 完全关掉
_DISABLED = os.environ.get("RESOURCE_LIMITS", "on").lower() in ("0", "off", "false", "no")

T = TypeVar("T")


@dataclass(frozen=True)
class ResourceBudget:
    """资源预算配置。

    - cpu_seconds:wallclock 上限(秒);超时 -> ``asyncio.TimeoutError``
    - mem_mb:虚拟内存软上限(MB);macOS 不强制,Linux 一般生效
    - log_on_breach:超时/超内存时是否打 warning(默认 True)
    """
    cpu_seconds: float | None = None
    mem_mb: int | None = None
    log_on_breach: bool = True


class ResourceBudgetExceeded(Exception):
    """超资源预算时抛出(超时由 asyncio.TimeoutError 承担,这里包一层语义)。"""


def _try_set_memory_limit(mem_mb: int) -> bool:
    """尝试给当前进程加 RLIMIT_AS 软上限;失败 try/except 降级。

    Linux 一般有效;macOS 多半成功但实际不强制(系统内核策略)。
    返回 True/False 仅用于诊断 + 测试断言,业务路径不依赖该结果。
    """
    try:
        import resource
    except ImportError:
        log.warning("[resource_limits] resource 模块不可用,跳过 mem 限制")
        return False
    try:
        bytes_limit = mem_mb * 1024 * 1024
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        # 不要超过 hard(无权限提升)
        new_hard = hard if hard > 0 else bytes_limit
        new_soft = min(bytes_limit, new_hard) if new_hard > 0 else bytes_limit
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, new_hard))
        log.debug("[resource_limits] RLIMIT_AS 设为 %sMB(原 soft=%s)", mem_mb, soft)
        return True
    except (ValueError, OSError) as exc:
        log.warning("[resource_limits] setrlimit 失败,降级: %s", exc)
        return False
    except Exception as exc:
        log.warning("[resource_limits] setrlimit 未预期异常,降级: %s", exc)
        return False


def with_resource_budget(
    cpu_seconds: float | None = None,
    mem_mb: int | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """给函数(sync 或 async)挂资源预算装饰器。

    超时:async 函数走 ``asyncio.wait_for``;sync 函数没有跨平台无副作用的
    硬超时(signal.alarm 仅 Unix 主线程),退化为"调用前后量 wallclock 并 warn"。
    真正需要硬超时的 sync 调用建议改 async 或直接走 ``sandbox.run_sandboxed``。

    内存:进入函数前 ``setrlimit``,退出不还原(进程级一次性收紧;再次进入
    一般不会被新预算放宽,符合"边界只收不放"原则)。

    全局开关 ``RESOURCE_LIMITS=off`` 时整个 decorator 透传不生效。
    """
    budget = ResourceBudget(cpu_seconds=cpu_seconds, mem_mb=mem_mb)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if _DISABLED:
            return func

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if budget.mem_mb:
                    _try_set_memory_limit(budget.mem_mb)
                if budget.cpu_seconds is None:
                    return await func(*args, **kwargs)
                try:
                    return await asyncio.wait_for(
                        func(*args, **kwargs), timeout=budget.cpu_seconds,
                    )
                except asyncio.TimeoutError:
                    if budget.log_on_breach:
                        log.warning(
                            "[resource_limits] %s 超时 %.2fs 触发 budget",
                            func.__qualname__, budget.cpu_seconds,
                        )
                    raise
            return async_wrapper

        # sync 路径:wallclock 量但无硬超时
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            import time
            if budget.mem_mb:
                _try_set_memory_limit(budget.mem_mb)
            t0 = time.monotonic()
            result = func(*args, **kwargs)
            dt = time.monotonic() - t0
            if budget.cpu_seconds is not None and dt > budget.cpu_seconds:
                if budget.log_on_breach:
                    log.warning(
                        "[resource_limits] sync %s 跑了 %.2fs > 预算 %.2fs(无法硬中断)",
                        func.__qualname__, dt, budget.cpu_seconds,
                    )
            return result

        return sync_wrapper

    return decorator


__all__ = [
    "ResourceBudget",
    "ResourceBudgetExceeded",
    "with_resource_budget",
]
