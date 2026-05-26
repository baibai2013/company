"""提案 2 · checker 调度器。

按 deliverable_type 从 CHECKERS 注册表里找一组 checker 串行跑,每个有自己的
timeout(asyncio.wait_for 兜底)。Wave 2 不接复杂 sandbox(profile=verifier 的
sandbox-exec 留给 Wave 4 §5.4)。
"""
from __future__ import annotations

import asyncio
import logging
import time

from backend.services.checkers import CHECKERS, CheckResult, Checker

log = logging.getLogger(__name__)


async def _run_one(checker: Checker, artifacts: dict, spec: dict) -> CheckResult:
    """单个 checker 跑一次,asyncio.wait_for 控时。

    超时:返回 ok=False 的 CheckResult,duration 以 timeout 为上限,err_msg 标"timeout"。
    异常:同样兜成 CheckResult,避免一个 checker 把整个 runner 炸掉。
    """
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(
            checker.run(artifacts, spec),
            timeout=checker.timeout_seconds,
        )
        return result
    except asyncio.TimeoutError:
        return CheckResult(
            ok=False,
            duration_ms=int((time.monotonic() - t0) * 1000),
            err_msg=f"timeout after {checker.timeout_seconds}s",
            output_log="",
        )
    except Exception as e:  # 防御:任何 checker 抛错都不能拖死 runner
        log.exception("checker %s raised", checker.name)
        return CheckResult(
            ok=False,
            duration_ms=int((time.monotonic() - t0) * 1000),
            err_msg=f"{type(e).__name__}: {e}",
            output_log="",
        )


async def run_checks(
    deliverable_type: str,
    artifacts: dict,
    spec: dict,
) -> list[tuple[str, CheckResult]]:
    """把某 deliverable_type 注册的所有 checker 串行跑一遍。

    返回 list of (check_name, CheckResult),对应 acceptance_checks 表的行级语义。
    """
    checkers = CHECKERS.get(deliverable_type, [])
    if not checkers:
        log.info(
            "run_checks: no checker registered for deliverable_type=%s; skipped",
            deliverable_type,
        )
        return []

    results: list[tuple[str, CheckResult]] = []
    for checker in checkers:
        result = await _run_one(checker, artifacts, spec)
        results.append((checker.name, result))
    return results


async def run_check_subset(
    deliverable_type: str,
    check_names: list[str],
    artifacts: dict,
    spec: dict,
) -> list[tuple[str, CheckResult]]:
    """LLM verifier 调 run_acceptance_check 时使用的子集运行接口。

    只跑 check_names 列出的那些 checker(按注册顺序保留);未注册的名字会被静默忽略。
    """
    wanted = set(check_names)
    checkers = [c for c in CHECKERS.get(deliverable_type, []) if c.name in wanted]
    results: list[tuple[str, CheckResult]] = []
    for checker in checkers:
        result = await _run_one(checker, artifacts, spec)
        results.append((checker.name, result))
    return results


__all__ = ["run_checks", "run_check_subset"]
