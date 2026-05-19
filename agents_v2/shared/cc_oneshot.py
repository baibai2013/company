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
