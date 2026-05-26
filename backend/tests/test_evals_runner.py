"""提案 3 · §4.4 evals_runner 集成测试(Wave 3 stub 路径)。

跑在真实 dev pg(同 test_verifier_orchestrator 的 skip 模式)。
覆盖三种 verdict 路径:pass / fail / pending_gate。

清扫策略:每个 test 用独立 fixture id 前缀(_run_pass_<suffix> 等),
退出时按前缀清 evals_runs + delegations / delegation_events + task + evals_fixtures。
"""
from __future__ import annotations

import uuid

import psycopg
import pytest

from backend.core.config import settings


def _has_dev_pg() -> bool:
    try:
        conn = psycopg.connect(
            settings.database_url_sync.replace("+psycopg", ""),
            connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_dev_pg(), reason="需要 dev postgres")


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests():
    """跨 test 关掉全局 async engine,避免 asyncpg 跨 loop 串台。"""
    yield
    from backend.core.db import engine
    await engine.dispose()


@pytest.fixture
def fixture_env():
    """每个 test 一份独立 fixture id 前缀,退出时精准清扫。

    清扫顺序(满足 FK):
      1. evals_runs (fk → evals_fixtures)
      2. acceptance_checks / gate_approvals (fk → verifier_runs)
      3. verifier_runs (fk → task)
      4. delegation_events / delegations
      5. task
      6. evals_fixtures
    """
    suffix = uuid.uuid4().hex[:8]
    fid_prefix = f"evtest_{suffix}_"
    employee = f"_evw_{suffix}"

    yield {"prefix": fid_prefix, "employee": employee, "suffix": suffix}

    sync_url = settings.database_url_sync.replace("+psycopg", "")
    conn = psycopg.connect(sync_url)
    try:
        cur = conn.cursor()
        # 1) evals_runs
        cur.execute(
            "DELETE FROM evals_runs WHERE fixture_id LIKE %s",
            (fid_prefix + "%",),
        )
        # 2 & 3) verifier 子链:通过本测试新建的 task(executor=employee)反查
        cur.execute(
            "DELETE FROM gate_approvals WHERE verifier_run_id IN ("
            "  SELECT id FROM verifier_runs WHERE delegation_id IN ("
            "    SELECT id FROM task WHERE executor = %s"
            "  )"
            ")",
            (employee,),
        )
        cur.execute(
            "DELETE FROM acceptance_checks WHERE verifier_run_id IN ("
            "  SELECT id FROM verifier_runs WHERE delegation_id IN ("
            "    SELECT id FROM task WHERE executor = %s"
            "  )"
            ")",
            (employee,),
        )
        cur.execute(
            "DELETE FROM verifier_runs WHERE delegation_id IN ("
            "  SELECT id FROM task WHERE executor = %s"
            ")",
            (employee,),
        )
        # 4) delegations / delegation_events(都是 evals_runner 造的)
        cur.execute(
            "DELETE FROM delegation_events WHERE delegation_id IN ("
            "  SELECT id FROM delegations WHERE to_employee = %s"
            ")",
            (employee,),
        )
        cur.execute(
            "DELETE FROM delegations WHERE to_employee = %s",
            (employee,),
        )
        # 5) task
        cur.execute("DELETE FROM task WHERE executor = %s", (employee,))
        # 6) evals_fixtures
        cur.execute(
            "DELETE FROM evals_fixtures WHERE id LIKE %s",
            (fid_prefix + "%",),
        )
        conn.commit()
    finally:
        conn.close()


# ── §1 verdict='pass'(auto_pass acceptance_spec)───────────────────
@pytest.mark.asyncio
async def test_run_fixture_pass_path(fixture_env):
    from backend.repos import evals_fixture_repo, evals_run_repo
    from backend.services import evals_runner

    fid = fixture_env["prefix"] + "pass"
    await evals_fixture_repo.upsert(
        fid,
        title="stub pass fixture",
        employee_key=fixture_env["employee"],
        input_prompt="trivial",
        acceptance_spec={
            "auto_pass": True,
            "deliverable_type": "generic_artifacts",
        },
        golden_outputs={"summary": "perfect", "files": []},
        tier=1,
    )

    result = await evals_runner.run_fixture(fid, git_sha="testsha-pass")

    assert result["fixture_id"] == fid
    assert result["verdict"] == "pass"
    assert result["ground_truth_pass_rate"] == 1.0
    assert result["iterations"] == 1
    assert result["delegation_id"]
    assert result["run_id"]

    # evals_runs 应该有一条对应记录
    runs = await evals_run_repo.list_for_fixture(fid)
    assert len(runs) == 1
    assert runs[0].verifier_verdict == "pass"
    assert runs[0].git_sha == "testsha-pass"
    assert runs[0].completed_at is not None


# ── §2 verdict='fail'(artifacts 含 fail_marker → llm_verifier fail)
@pytest.mark.asyncio
async def test_run_fixture_fail_path(fixture_env):
    from backend.repos import evals_fixture_repo, evals_run_repo
    from backend.services import evals_runner

    fid = fixture_env["prefix"] + "fail"
    # golden_outputs 里塞 fail_marker → simulate_artifacts 直接灌入 → llm_verifier fail
    await evals_fixture_repo.upsert(
        fid,
        title="stub fail fixture",
        employee_key=fixture_env["employee"],
        input_prompt="trivial",
        acceptance_spec={"deliverable_type": "generic_artifacts"},
        golden_outputs={"fail_marker": "spec violated", "files": []},
        tier=2,
    )

    result = await evals_runner.run_fixture(fid, git_sha="testsha-fail")

    assert result["verdict"] == "fail"
    assert result["ground_truth_pass_rate"] == 0.0

    runs = await evals_run_repo.list_for_fixture(fid)
    assert len(runs) == 1
    assert runs[0].verifier_verdict == "fail"


# ── §3 verdict='pending_gate'(force_human → 创建 pending gate)─────
@pytest.mark.asyncio
async def test_run_fixture_pending_gate_path(fixture_env):
    from backend.repos import evals_fixture_repo, evals_run_repo
    from backend.services import evals_runner

    fid = fixture_env["prefix"] + "gate"
    await evals_fixture_repo.upsert(
        fid,
        title="stub gate fixture",
        employee_key=fixture_env["employee"],
        input_prompt="trivial",
        acceptance_spec={
            "force_human": True,
            "deliverable_type": "generic_artifacts",
        },
        golden_outputs=None,
        tier=3,
    )

    result = await evals_runner.run_fixture(fid, git_sha="testsha-gate")
    assert result["verdict"] == "pending_gate"
    # 等 gate 决策才结案,但 evals_runs 立即落库(verdict=pending_gate)
    runs = await evals_run_repo.list_for_fixture(fid)
    assert len(runs) == 1
    assert runs[0].verifier_verdict == "pending_gate"
    assert runs[0].ground_truth_pass_rate == 0.0  # 非 pass → 0


# ── §4 fixture 不存在 → ValueError ──────────────────────────────────
@pytest.mark.asyncio
async def test_run_fixture_missing_raises(fixture_env):
    from backend.services import evals_runner

    bogus = fixture_env["prefix"] + "nope"
    with pytest.raises(ValueError, match="fixture not found"):
        await evals_runner.run_fixture(bogus, git_sha="testsha-x")
