"""提案 4 · §5.4 沙箱与资源边界 — 子进程沙箱包装器(Wave 4)。

危险工具(派活、写文件、发飞书)走 sandbox 跑;开发环境若没有 docker,自动
降级到普通 subprocess + log.warning,绝不阻塞主路径。

**SANDBOX_BACKEND 环境变量**:
  - ``docker``    用 ``docker run --cpus --memory --network=none`` 包裹(默认尝试)
  - ``subprocess``强制用普通 subprocess(忽略 cpu/mem 限制,只剩 timeout)
  - ``disabled``  直接执行,不加任何隔离(本地调试用)

不显式设置时,自动探测:有 docker → ``docker``;否则 ``subprocess``。

**资源限制语义**:
  - cpu(整核数)/ memory_mb(MB)只在 docker 后端生效
  - timeout_seconds 在所有后端都生效(普通 subprocess 走 ``asyncio.wait_for``)
  - network=none 只在 docker 后端生效

用法::

    result = await run_sandboxed(
        ["python", "-c", "print(1)"],
        timeout_seconds=30,
        cpu=1.0,
        memory_mb=512,
    )
    # result = SandboxResult(returncode, stdout, stderr, backend, timed_out)
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Sequence

log = logging.getLogger(__name__)

# 允许的后端取值
_VALID_BACKENDS = {"docker", "subprocess", "disabled"}

# 默认沙箱镜像;真正部署时用 infra 自建镜像替换
_DEFAULT_DOCKER_IMAGE = os.environ.get("SANDBOX_DOCKER_IMAGE", "python:3.12-slim")


@dataclass
class SandboxResult:
    """沙箱执行结果。

    返回字段:
      - returncode:进程退出码(超时一律 124,与 GNU timeout 对齐)
      - stdout / stderr:UTF-8 解码失败则替换非法字符
      - backend:实际跑的后端(docker / subprocess / disabled)
      - timed_out:是否因超时被 kill
    """
    returncode: int
    stdout: str
    stderr: str
    backend: str
    timed_out: bool = False


def _detect_backend() -> str:
    """根据环境变量 + docker 可用性自动选后端。

    优先级:
      1. SANDBOX_BACKEND 显式合法值(docker/subprocess/disabled)
      2. 否则:有 docker 命令 → docker;没有 → subprocess(降级 + warn)
    """
    env_val = (os.environ.get("SANDBOX_BACKEND") or "").strip().lower()
    if env_val in _VALID_BACKENDS:
        # docker 后端但 docker 不在,降级 + warn(不抛)
        if env_val == "docker" and shutil.which("docker") is None:
            log.warning(
                "[sandbox] SANDBOX_BACKEND=docker 但 docker 命令不可用,降级 subprocess",
            )
            return "subprocess"
        return env_val
    # 自动探测
    if shutil.which("docker") is not None:
        return "docker"
    log.debug("[sandbox] docker 不可用,默认 subprocess 后端")
    return "subprocess"


def _build_docker_argv(
    cmd: Sequence[str],
    *,
    cpu: float,
    memory_mb: int,
    network_none: bool,
    image: str,
) -> list[str]:
    """构造 ``docker run --rm --cpus --memory --network=none <image> <cmd>``。"""
    argv = [
        "docker", "run", "--rm",
        "--cpus", str(cpu),
        "--memory", f"{memory_mb}m",
    ]
    if network_none:
        argv += ["--network", "none"]
    # 默认丢掉所有 capability,只读根文件系统
    argv += ["--cap-drop", "ALL", "--read-only"]
    argv += [image]
    argv += list(cmd)
    return argv


async def run_sandboxed(
    cmd: Sequence[str],
    *,
    timeout_seconds: float = 60.0,
    cpu: float = 1.0,
    memory_mb: int = 512,
    network_none: bool = True,
    image: str | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> SandboxResult:
    """异步跑一条命令,自动按当前后端加资源限制。

    超时一律返回 returncode=124,timed_out=True,**不抛**(降级哲学)。
    任何后端层面错误也都吞掉返回 returncode=125 + stderr 写原因,绝不
    raise 阻塞调用方。
    """
    backend = _detect_backend()
    argv: list[str]
    if backend == "docker":
        argv = _build_docker_argv(
            cmd,
            cpu=cpu,
            memory_mb=memory_mb,
            network_none=network_none,
            image=image or _DEFAULT_DOCKER_IMAGE,
        )
    else:
        # subprocess / disabled 都直接跑;disabled 仅作语义标记,行为相同
        argv = list(cmd)

    log.debug("[sandbox] backend=%s argv=%s timeout=%.1fs", backend, argv, timeout_seconds)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=({**os.environ, **env} if env else None),
            cwd=cwd,
        )
    except Exception as exc:
        log.warning("[sandbox] spawn 失败 backend=%s exc=%s", backend, exc)
        return SandboxResult(
            returncode=125,
            stdout="",
            stderr=f"sandbox spawn failed: {type(exc).__name__}: {exc}",
            backend=backend,
            timed_out=False,
        )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        log.warning("[sandbox] 超时 %ss kill 子进程 argv[0]=%s", timeout_seconds, argv[0])
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            stdout_b, stderr_b = await proc.communicate()
        except Exception:
            stdout_b, stderr_b = b"", b""
        return SandboxResult(
            returncode=124,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace") + "\n[sandbox] timed out",
            backend=backend,
            timed_out=True,
        )

    return SandboxResult(
        returncode=proc.returncode if proc.returncode is not None else 0,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
        backend=backend,
        timed_out=False,
    )


__all__ = ["run_sandboxed", "SandboxResult"]
