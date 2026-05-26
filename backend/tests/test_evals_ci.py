"""提案 4 · §5.3 scripts/evals_ci.py 测试 — Wave 4。

覆盖三种退出码:
  §1 没有 batch 行     → exit 0(skip,新仓不阻塞)
  §2 db 连不上(repo 抛)→ exit 0(skip)
  §3 batch 未 finalize → exit 0(in-flight 不卡)
  §4 pass_rate < 阈值  → exit 1
  §5 pass_rate ≥ 阈值  → exit 0
  §6 阈值优先级:CLI > env > 0.8

直接 in-process 调 ``_amain`` / ``_resolve_threshold``,不真起子进程。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# 把 scripts/ 加到 sys.path 让 evals_ci 能 import
_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _import_evals_ci():
    """每次新鲜 import,避免跨测试的 module-level 状态串。"""
    if "scripts.evals_ci" in sys.modules:
        return importlib.reload(sys.modules["scripts.evals_ci"])
    return importlib.import_module("scripts.evals_ci")


def _make_args(threshold: float | None = None) -> argparse.Namespace:
    return argparse.Namespace(threshold=threshold, batch_name="latest", verbose=False)


def _fake_batch(*, pass_rate: float, completed: bool = True) -> SimpleNamespace:
    """造一个像 EvalsBatch row 的对象,只填 evals_ci 用的字段。"""
    return SimpleNamespace(
        id=uuid.uuid4(),
        git_sha="testsha",
        fixture_count=10,
        pass_count=int(pass_rate * 10),
        pass_rate=pass_rate,
        completed_at=datetime.now(timezone.utc) if completed else None,
    )


# ── §1 empty:没数据 → exit 0 ───────────────────────────────────────
def test_evals_ci_empty_batches_returns_zero(monkeypatch, caplog):
    evals_ci = _import_evals_ci()

    async def _empty(limit=1):
        return []

    monkeypatch.setattr(
        "backend.repos.evals_batch_repo.list_recent", _empty,
    )

    with caplog.at_level("WARNING", logger="evals_ci"):
        rc = asyncio.run(evals_ci._amain(_make_args()))
    assert rc == 0
    assert any("no eval data" in r.message for r in caplog.records)


# ── §2 db_err:repo 抛 → exit 0 ─────────────────────────────────────
def test_evals_ci_db_error_returns_zero(monkeypatch, caplog):
    evals_ci = _import_evals_ci()

    async def _boom(limit=1):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(
        "backend.repos.evals_batch_repo.list_recent", _boom,
    )

    with caplog.at_level("WARNING", logger="evals_ci"):
        rc = asyncio.run(evals_ci._amain(_make_args()))
    assert rc == 0
    assert any("db unreachable" in r.message for r in caplog.records)


# ── §3 in-flight:batch 没 completed_at → exit 0 ────────────────────
def test_evals_ci_inflight_batch_returns_zero(monkeypatch):
    evals_ci = _import_evals_ci()

    batch = _fake_batch(pass_rate=0.0, completed=False)

    async def _list(limit=1):
        return [batch]

    monkeypatch.setattr(
        "backend.repos.evals_batch_repo.list_recent", _list,
    )

    rc = asyncio.run(evals_ci._amain(_make_args()))
    assert rc == 0


# ── §4 pass_rate 低于阈值 → exit 1 ──────────────────────────────────
def test_evals_ci_below_threshold_returns_one(monkeypatch, capsys):
    evals_ci = _import_evals_ci()

    batch = _fake_batch(pass_rate=0.5)

    async def _list(limit=1):
        return [batch]

    monkeypatch.setattr(
        "backend.repos.evals_batch_repo.list_recent", _list,
    )

    rc = asyncio.run(evals_ci._amain(_make_args(threshold=0.8)))
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAIL" in err
    assert "0.5000" in err


# ── §5 pass_rate 达标 → exit 0 ──────────────────────────────────────
def test_evals_ci_meets_threshold_returns_zero(monkeypatch, capsys):
    evals_ci = _import_evals_ci()

    batch = _fake_batch(pass_rate=0.9)

    async def _list(limit=1):
        return [batch]

    monkeypatch.setattr(
        "backend.repos.evals_batch_repo.list_recent", _list,
    )

    rc = asyncio.run(evals_ci._amain(_make_args(threshold=0.8)))
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASS" in out


# ── §6 阈值优先级 ────────────────────────────────────────────────────
def test_resolve_threshold_priority(monkeypatch):
    evals_ci = _import_evals_ci()

    # 1. CLI 优先于 env
    monkeypatch.setenv("EVALS_GATE_THRESHOLD", "0.7")
    assert evals_ci._resolve_threshold(0.95) == 0.95

    # 2. env > default
    monkeypatch.setenv("EVALS_GATE_THRESHOLD", "0.6")
    assert evals_ci._resolve_threshold(None) == 0.6

    # 3. env 非法值 → 默认 0.8
    monkeypatch.setenv("EVALS_GATE_THRESHOLD", "not-a-float")
    assert evals_ci._resolve_threshold(None) == 0.8

    # 4. 没设 env → 默认 0.8
    monkeypatch.delenv("EVALS_GATE_THRESHOLD", raising=False)
    assert evals_ci._resolve_threshold(None) == 0.8


# ── §7 主入口 main() 在 _amain 异常时 exit 2 ────────────────────────
def test_evals_ci_unexpected_error_returns_two(monkeypatch):
    """asyncio.run 本身抛非预期异常时 main() 应返回 2。"""
    evals_ci = _import_evals_ci()

    def _bad_run(coro, *a, **kw):
        # close coroutine 避免 RuntimeWarning,然后抛
        try:
            coro.close()
        except Exception:
            pass
        raise RuntimeError("loop broken")

    monkeypatch.setattr(evals_ci.asyncio, "run", _bad_run)
    monkeypatch.setattr(
        evals_ci, "_parse_args", lambda: _make_args(),
    )

    rc = evals_ci.main()
    assert rc == 2
