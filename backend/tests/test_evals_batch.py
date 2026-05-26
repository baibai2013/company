"""提案 3 · §4.4 evals_batch 集成测试(Wave 3 stub)。

跑在真实 dev pg(同 evals_runner 测试)。覆盖:
  §1 空 batch(没有任何 fixture 匹配 tier)→ pass_rate=1.0,fixture_count=0
  §2 多 fixture batch(2 pass + 1 fail)→ pass_count=2,pass_rate≈0.667
  §3 显式 fixture_ids 选择(忽略 tier 过滤)
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
    yield
    from backend.core.db import engine
    await engine.dispose()


@pytest.fixture
def batch_env():
    """每个 test 一份独立 prefix + employee,退出时清评测/委派/任务/批次。"""
    suffix = uuid.uuid4().hex[:8]
    fid_prefix = f"evbat_{suffix}_"
    employee = f"_evbat_{suffix}"
    triggered_by = f"test-{suffix}"

    yield {
        "prefix": fid_prefix,
        "employee": employee,
        "triggered_by": triggered_by,
        "suffix": suffix,
    }

    sync_url = settings.database_url_sync.replace("+psycopg", "")
    conn = psycopg.connect(sync_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM evals_runs WHERE fixture_id LIKE %s",
            (fid_prefix + "%",),
        )
        cur.execute(
            "DELETE FROM evals_batches WHERE triggered_by = %s",
            (triggered_by,),
        )
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
        cur.execute("DELETE FROM task WHERE executor = %s", (employee,))
        cur.execute(
            "DELETE FROM evals_fixtures WHERE id LIKE %s",
            (fid_prefix + "%",),
        )
        conn.commit()
    finally:
        conn.close()


# ── §1 空 batch ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_batch_empty(batch_env):
    """没有任何 fixture 匹配指定 tier → batch 行存在,fixture_count=0,pass_rate=1.0(空集约定)。"""
    from backend.repos import evals_batch_repo
    from backend.services import evals_batch

    # 用一个绝对不可能撞到现有数据的 tier 值过滤(只有 1/2/3 是合法的,99 必空)
    result = await evals_batch.run_batch(
        git_sha="testsha-empty",
        tier=99,
        triggered_by=batch_env["triggered_by"],
    )

    assert result["fixture_count"] == 0
    assert result["pass_count"] == 0
    assert result["pass_rate"] == 1.0
    assert result["results"] == []

    # batch 行确实落库且 finalize 了
    batch = await evals_batch_repo.get(result["batch_id"])
    assert batch is not None
    assert batch.fixture_count == 0
    assert batch.completed_at is not None
    assert batch.pass_count == 0


# ── §2 多 fixture batch:2 pass + 1 fail ────────────────────────────
@pytest.mark.asyncio
async def test_run_batch_mixed(batch_env):
    from backend.repos import evals_batch_repo, evals_fixture_repo
    from backend.services import evals_batch

    prefix = batch_env["prefix"]
    employee = batch_env["employee"]

    # 用 fixture_ids 显式锁定本次 batch 的 3 条(避免被全局 fixture 干扰)
    fids = []
    for tag, spec, golden in [
        ("p1", {"auto_pass": True, "deliverable_type": "generic_artifacts"}, {"summary": "ok"}),
        ("p2", {"auto_pass": True, "deliverable_type": "generic_artifacts"}, {"summary": "ok"}),
        ("f1", {"deliverable_type": "generic_artifacts"}, {"fail_marker": "x"}),
    ]:
        fid = prefix + tag
        await evals_fixture_repo.upsert(
            fid,
            title=f"batch test {tag}",
            employee_key=employee,
            input_prompt="trivial",
            acceptance_spec=spec,
            golden_outputs=golden,
            tier=2,
        )
        fids.append(fid)

    result = await evals_batch.run_batch(
        git_sha="testsha-mixed",
        triggered_by=batch_env["triggered_by"],
        fixture_ids=fids,
    )

    assert result["fixture_count"] == 3
    assert result["pass_count"] == 2
    # pass_rate = 2/3 ≈ 0.6667
    assert abs(result["pass_rate"] - (2 / 3)) < 1e-6
    assert result["avg_iterations"] == 1.0
    verdicts = {r["fixture_id"]: r["verdict"] for r in result["results"]}
    assert verdicts[prefix + "p1"] == "pass"
    assert verdicts[prefix + "p2"] == "pass"
    assert verdicts[prefix + "f1"] == "fail"

    # batch 行 finalize 数据正确
    batch = await evals_batch_repo.get(result["batch_id"])
    assert batch is not None
    assert batch.fixture_count == 3
    assert batch.pass_count == 2
    assert abs((batch.pass_rate or 0) - (2 / 3)) < 1e-6
    assert batch.completed_at is not None


# ── §3 fixture_ids 选择优先于 tier ──────────────────────────────────
@pytest.mark.asyncio
async def test_run_batch_explicit_ids_overrides_tier(batch_env):
    from backend.repos import evals_fixture_repo
    from backend.services import evals_batch

    prefix = batch_env["prefix"]
    employee = batch_env["employee"]

    # 1 条 tier=1,1 条 tier=3,显式只跑 tier=3 那条但也"传 tier=1"
    f_t1 = prefix + "t1"
    f_t3 = prefix + "t3"
    await evals_fixture_repo.upsert(
        f_t1, title="tier1", employee_key=employee, input_prompt="x",
        acceptance_spec={"auto_pass": True, "deliverable_type": "generic_artifacts"},
        golden_outputs={"summary": "ok"}, tier=1,
    )
    await evals_fixture_repo.upsert(
        f_t3, title="tier3", employee_key=employee, input_prompt="x",
        acceptance_spec={"auto_pass": True, "deliverable_type": "generic_artifacts"},
        golden_outputs={"summary": "ok"}, tier=3,
    )

    # 显式 fixture_ids=[f_t3],tier=1 应被忽略
    result = await evals_batch.run_batch(
        git_sha="testsha-explicit",
        tier=1,
        triggered_by=batch_env["triggered_by"],
        fixture_ids=[f_t3],
    )
    assert result["fixture_count"] == 1
    assert result["results"][0]["fixture_id"] == f_t3
    assert result["results"][0]["verdict"] == "pass"
