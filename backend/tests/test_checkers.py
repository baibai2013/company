"""提案 2 · checker 单元测试(纯 Python,不依赖 pg)。

覆盖:
  - OutputArtifactsChecker 缺文件 / 文件齐
  - Build123dExecutableChecker 正常退出 / 抛异常 / 超时
  - runner.run_checks 与 run_check_subset
"""
from __future__ import annotations

import os
import tempfile

import pytest

from backend.services.checkers import CHECKERS, CheckResult, runner
from backend.services.checkers.build123d_executable import Build123dExecutableChecker
from backend.services.checkers.output_artifacts import OutputArtifactsChecker


# ── OutputArtifactsChecker ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_output_artifacts_missing_required():
    """spec 要求一个不存在的文件 → ok=False。"""
    checker = OutputArtifactsChecker()
    with tempfile.TemporaryDirectory() as td:
        present = os.path.join(td, "present.py")
        with open(present, "w") as f:
            f.write("# hi")
        # 声明里有 present,但 required 还要一个 missing.py
        result = await checker.run(
            artifacts={"files": [present]},
            spec={"required_files": [present, os.path.join(td, "missing.py")]},
        )
    assert isinstance(result, CheckResult)
    assert result.ok is False
    assert "missing" in (result.err_msg or "")


@pytest.mark.asyncio
async def test_output_artifacts_all_present():
    """spec 要求的文件都在 artifacts 里且磁盘存在 → ok=True。"""
    checker = OutputArtifactsChecker()
    with tempfile.TemporaryDirectory() as td:
        f1 = os.path.join(td, "a.py")
        f2 = os.path.join(td, "b.py")
        for p in (f1, f2):
            with open(p, "w") as f:
                f.write("# ok")
        result = await checker.run(
            artifacts={"files": [f1, f2]},
            spec={"required_files": [f1, f2]},
        )
    assert result.ok is True
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_output_artifacts_declared_but_not_on_disk():
    """artifacts['files'] 声明了但磁盘没有 → ok=False。"""
    checker = OutputArtifactsChecker()
    result = await checker.run(
        artifacts={"files": ["/nonexistent/path/xyz.py"]},
        spec={"required_files": ["/nonexistent/path/xyz.py"]},
    )
    assert result.ok is False
    assert "not found on disk" in (result.err_msg or "")


# ── Build123dExecutableChecker ─────────────────────────────────────
@pytest.mark.asyncio
async def test_build123d_runs_simple_print():
    """跑一段 print('hi') → exit 0 → ok=True。"""
    checker = Build123dExecutableChecker()
    with tempfile.TemporaryDirectory() as td:
        py = os.path.join(td, "ok.py")
        with open(py, "w") as f:
            f.write('print("hi from checker test")\n')
        result = await checker.run(
            artifacts={"py_file": py},
            spec={},
        )
    assert result.ok is True
    assert "hi from checker test" in result.output_log


@pytest.mark.asyncio
async def test_build123d_runs_raise():
    """跑一段会 raise 的脚本 → exit !=0 → ok=False。"""
    checker = Build123dExecutableChecker()
    with tempfile.TemporaryDirectory() as td:
        py = os.path.join(td, "boom.py")
        with open(py, "w") as f:
            f.write('raise RuntimeError("boom from test")\n')
        result = await checker.run(
            artifacts={"py_file": py},
            spec={},
        )
    assert result.ok is False
    assert "boom" in (result.output_log or "") or "boom" in (result.err_msg or "")


@pytest.mark.asyncio
async def test_build123d_missing_py_file():
    """artifacts 没有 py_file → ok=False。"""
    checker = Build123dExecutableChecker()
    result = await checker.run(artifacts={}, spec={})
    assert result.ok is False
    assert "py_file" in (result.err_msg or "")


@pytest.mark.asyncio
async def test_build123d_timeout():
    """跑一段 time.sleep(99) 子脚本,checker 用短 timeout → ok=False。"""
    # 临时把 timeout 调短
    checker = Build123dExecutableChecker()
    checker.timeout_seconds = 1  # 强制 1s 超时

    with tempfile.TemporaryDirectory() as td:
        py = os.path.join(td, "slow.py")
        with open(py, "w") as f:
            f.write("import time\ntime.sleep(99)\n")
        result = await checker.run(
            artifacts={"py_file": py},
            spec={},
        )
    assert result.ok is False
    assert "timeout" in (result.err_msg or "").lower()


# ── runner ────────────────────────────────────────────────────────
def test_registry_populated():
    """import backend.services.checkers 应该把两个通用 checker 自动注册到 build123d_py。"""
    names = [c.name for c in CHECKERS.get("build123d_py", [])]
    assert "build123d_executable" in names
    assert "output_artifacts_present" in names


@pytest.mark.asyncio
async def test_runner_runs_all_for_type():
    """runner.run_checks 跑完所有 checker,返回 (name, result) 列表。"""
    with tempfile.TemporaryDirectory() as td:
        py = os.path.join(td, "ok.py")
        with open(py, "w") as f:
            f.write('print("ok")\n')
        results = await runner.run_checks(
            "build123d_py",
            artifacts={"py_file": py, "files": [py]},
            spec={"required_files": [py]},
        )
    names = [n for n, _ in results]
    assert "build123d_executable" in names
    assert "output_artifacts_present" in names
    # 都该 ok
    for n, r in results:
        assert r.ok is True, f"{n} 应该 ok 但失败: {r.err_msg}"


@pytest.mark.asyncio
async def test_runner_subset():
    """run_check_subset 只跑指定 checker,不跑全部。"""
    with tempfile.TemporaryDirectory() as td:
        py = os.path.join(td, "ok.py")
        with open(py, "w") as f:
            f.write('print("ok")\n')
        results = await runner.run_check_subset(
            "build123d_py",
            check_names=["output_artifacts_present"],
            artifacts={"files": [py]},
            spec={"required_files": [py]},
        )
    assert len(results) == 1
    assert results[0][0] == "output_artifacts_present"
    assert results[0][1].ok is True


@pytest.mark.asyncio
async def test_runner_unknown_type():
    """未注册的 deliverable_type → 空列表(skipped)。"""
    results = await runner.run_checks(
        "totally_unknown_type",
        artifacts={},
        spec={},
    )
    assert results == []
