"""轻量 claude code CLI 一次性调用：纯 LLM 推理，不带 MCP / sandbox / 进程池。

用于 cc 决策、route 等 1-shot 文本判断，避开重型 cc_executor 的工具链。
prompt 单次发完即退，stdout = LLM 文本，由调用方解析。
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("agents_v2.cc_oneshot")

CLAUDE_BIN = "/opt/homebrew/bin/claude"


class CLIOneshotFailed(Exception):
    pass


async def run_cli_oneshot(
    prompt: str,
    *,
    model: str = "claude-sonnet-4-6",
    effort: str = "low",
    timeout: float = 30.0,
    cwd: str | None = None,
) -> str:
    """spawn 一次性 `claude -p`，等返回，解码 stdout 文本。

    不复用进程池、不注入 MCP、不走 sandbox。失败抛 CLIOneshotFailed。
    """
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--model", model,
        "--effort", effort,
        "--output-format", "text",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or os.getcwd(),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        raise CLIOneshotFailed(f"timeout after {timeout}s") from exc

    if proc.returncode != 0:
        err = stderr_b.decode("utf-8", errors="replace")[:300]
        raise CLIOneshotFailed(f"rc={proc.returncode} stderr={err!r}")

    return stdout_b.decode("utf-8", errors="replace").strip()


async def run_cli_oneshot_pooled(
    prompt: str,
    *,
    model: str = "claude-sonnet-4-6",
    effort: str = "low",
    timeout: float = 30.0,
    employee_key: str = "",
    cwd: str | None = None,
) -> str:
    """池化版 oneshot：复用常驻 claude 进程(无 MCP / 无 sandbox),省冷启。

    用于 cc 补充意见这类无状态判断。按 (employee, cc_oneshot thread, model, effort)
    在 ClaudePool 里复用一个轻量常驻进程 —— 同员工多次 cc 决策共用,免每次冷启。
    session 会在该进程内累积,但每条 prompt 自包含(原始消息+回复+问哪些专家),
    模型只针对当前 prompt 作答;idle GC 到期自然回收,上下文不会无界增长。

    池不可用 / 复用失败 → 回退冷启 run_cli_oneshot(保证不退化)。
    """
    from agents_v2.shared.claude_pool import SpawnArgs, get_pool

    pool = get_pool()
    if not getattr(pool, "enabled", False):
        return await run_cli_oneshot(prompt, model=model, effort=effort, timeout=timeout, cwd=cwd)

    _cwd = cwd or os.getcwd()
    spawn_args = SpawnArgs(
        cwd=_cwd, model=model, effort=effort,
        extra_cli_args=[],          # 无 MCP：纯文本判断,不挂工具
        cmd_wrapper=None,           # 无 sandbox：只读模型推理
        employee_key=employee_key or None,
    )
    # 专用 pool_thread,与员工主工作进程隔离,避免串用同一会话
    key = (employee_key or "cc", _cwd, f"cc_oneshot:{employee_key}", model, effort)
    runner = None
    try:
        runner = await pool.acquire(key, spawn_args)
        final, _sid, _logs = await asyncio.wait_for(runner.submit(prompt), timeout=timeout)
        await pool.release(key, runner)
        return (final or "").strip()
    except Exception as exc:
        log.warning("run_cli_oneshot_pooled 复用失败(%s),回退冷启", type(exc).__name__)
        if runner is not None:
            try:
                await runner.terminate()
            except Exception:
                pass
        return await run_cli_oneshot(prompt, model=model, effort=effort, timeout=timeout, cwd=cwd)
