"""build123d_py 类型 checker:把交付的 .py 文件当成可执行脚本跑一遍。

设计意图:对应提案 2 §4.2 mechanical_build123d_py 的核心检查 —
"代码能不能起来"是任何 build123d 交付物的最低门槛。

Wave 2 暂用 asyncio.create_subprocess_exec 直接跑(沿用当前 venv);
Wave 4 §5.4 接 verifier.sb 沙箱后,这里换成 sandbox-exec wrap。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from backend.services.checkers import CheckResult, register


class Build123dExecutableChecker:
    """artifacts['py_file'] 路径若存在,subprocess 跑 'python <file>'。

    退出码 0 → ok=True;否则 ok=False,stderr 当作 err_msg。
    """

    name = "build123d_executable"
    timeout_seconds = 60

    async def run(self, artifacts: dict, spec: dict) -> CheckResult:
        t0 = time.monotonic()
        py_file = (artifacts or {}).get("py_file")

        if not py_file:
            return CheckResult(
                ok=False,
                duration_ms=int((time.monotonic() - t0) * 1000),
                err_msg="artifacts['py_file'] missing",
                output_log="",
            )
        if not os.path.exists(py_file):
            return CheckResult(
                ok=False,
                duration_ms=int((time.monotonic() - t0) * 1000),
                err_msg=f"py_file not found: {py_file}",
                output_log="",
            )

        # 用同一解释器跑,保证 build123d 等依赖能 import(本地 venv)。
        # 子进程 cwd 用脚本所在目录,避免 import 路径意外。
        cwd = os.path.dirname(os.path.abspath(py_file)) or "."
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                py_file,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            return CheckResult(
                ok=False,
                duration_ms=int((time.monotonic() - t0) * 1000),
                err_msg=f"failed to spawn python: {e}",
                output_log="",
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            # 超时:杀子进程,返回 ok=False(runner 也会兜底,但内置同样语义更稳)
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
            duration_ms = int((time.monotonic() - t0) * 1000)
            return CheckResult(
                ok=False,
                duration_ms=duration_ms,
                err_msg=f"timeout after {self.timeout_seconds}s",
                output_log="",
            )

        duration_ms = int((time.monotonic() - t0) * 1000)
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        ok = (proc.returncode == 0)

        return CheckResult(
            ok=ok,
            duration_ms=duration_ms,
            err_msg=None if ok else (stderr.strip().splitlines()[-1] if stderr.strip() else f"exit {proc.returncode}"),
            output_log=f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}",
        )


# 注册
register("build123d_py", Build123dExecutableChecker())


__all__ = ["Build123dExecutableChecker"]
