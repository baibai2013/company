"""提案 4 · §5.4 sandbox 测试 — Wave 4。

覆盖路径:
  §1 SANDBOX_BACKEND=disabled  → 直跑,得到真实 stdout
  §2 SANDBOX_BACKEND=subprocess→ 普通子进程,等同 disabled
  §3 SANDBOX_BACKEND=docker 但 docker 不可用 → 自动降级 subprocess + warn
  §4 自动探测:无 docker 命令 → subprocess
  §5 超时路径:returncode=124,timed_out=True
  §6 spawn 失败:不抛,returncode=125
  §7 backend 字段在结果里正确写回
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from backend.services import sandbox
from backend.services.sandbox import SandboxResult, run_sandboxed


# ── §1 disabled 后端 ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_sandboxed_disabled_runs_command(monkeypatch):
    """SANDBOX_BACKEND=disabled 时直接跑,行为与普通 subprocess 相同。"""
    monkeypatch.setenv("SANDBOX_BACKEND", "disabled")
    result = await run_sandboxed(
        [sys.executable, "-c", "print('hello-disabled')"],
        timeout_seconds=10,
    )
    assert isinstance(result, SandboxResult)
    assert result.returncode == 0
    assert result.backend == "disabled"
    assert "hello-disabled" in result.stdout
    assert result.timed_out is False


# ── §2 subprocess 后端 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_sandboxed_subprocess_backend(monkeypatch):
    monkeypatch.setenv("SANDBOX_BACKEND", "subprocess")
    result = await run_sandboxed(
        [sys.executable, "-c", "print('hello-sub')"],
        timeout_seconds=10,
    )
    assert result.returncode == 0
    assert result.backend == "subprocess"
    assert "hello-sub" in result.stdout


# ── §3 显式 docker 但 docker 不可用 → 降级 subprocess ─────────────────
@pytest.mark.asyncio
async def test_run_sandboxed_docker_unavailable_fallback(monkeypatch):
    """docker 命令不在 PATH 时,SANDBOX_BACKEND=docker 自动降级,不抛。"""
    monkeypatch.setenv("SANDBOX_BACKEND", "docker")
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)

    result = await run_sandboxed(
        [sys.executable, "-c", "print('fallback-ok')"],
        timeout_seconds=10,
    )
    assert result.returncode == 0
    # 关键:即便要求 docker,后端已被降级到 subprocess
    assert result.backend == "subprocess"
    assert "fallback-ok" in result.stdout


# ── §4 自动探测:没 docker 也没显式后端 → subprocess ────────────────
@pytest.mark.asyncio
async def test_run_sandboxed_auto_detect_no_docker(monkeypatch):
    monkeypatch.delenv("SANDBOX_BACKEND", raising=False)
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    result = await run_sandboxed(
        [sys.executable, "-c", "print('auto-sub')"],
        timeout_seconds=10,
    )
    assert result.backend == "subprocess"
    assert result.returncode == 0


# ── §5 超时路径 ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_sandboxed_timeout(monkeypatch):
    """超过 timeout_seconds 一律 returncode=124 + timed_out=True,不抛。"""
    monkeypatch.setenv("SANDBOX_BACKEND", "disabled")
    # 跑一个 5s sleep,但只给 0.5s
    result = await run_sandboxed(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_seconds=0.5,
    )
    assert result.timed_out is True
    assert result.returncode == 124
    assert "timed out" in result.stderr


# ── §6 spawn 失败兜底 ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_sandboxed_spawn_failure_returns_125(monkeypatch):
    """命令完全不存在时不抛,returncode=125 + stderr 写原因。"""
    monkeypatch.setenv("SANDBOX_BACKEND", "disabled")
    result = await run_sandboxed(
        ["/nonexistent/binary/that/should/never/exist", "--no"],
        timeout_seconds=2,
    )
    assert result.returncode == 125
    assert "spawn failed" in result.stderr
    assert result.timed_out is False


# ── §7 docker 后端命令构造(mock create_subprocess_exec)───────────
@pytest.mark.asyncio
async def test_docker_argv_construction(monkeypatch):
    """docker 后端时 argv 应包含 --cpus / --memory / --network=none。"""
    captured: dict = {}

    class _FakeProc:
        returncode = 0
        async def communicate(self):
            return b"docker-out", b""

    async def _fake_exec(*argv, **kw):
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setenv("SANDBOX_BACKEND", "docker")
    # which 让 docker "存在"
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", _fake_exec)

    result = await run_sandboxed(
        ["echo", "hi"],
        timeout_seconds=5,
        cpu=2.0,
        memory_mb=1024,
        image="my-image:latest",
    )
    assert result.backend == "docker"
    assert result.returncode == 0
    argv = captured["argv"]
    assert argv[0] == "docker"
    assert "--cpus" in argv
    assert "2.0" in argv
    assert "--memory" in argv
    assert "1024m" in argv
    assert "--network" in argv
    assert "none" in argv
    assert "my-image:latest" in argv
    # cmd 在 image 之后
    img_idx = argv.index("my-image:latest")
    assert argv[img_idx + 1:] == ("echo", "hi") or list(argv[img_idx + 1:]) == ["echo", "hi"]
