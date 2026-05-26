"""提案 2 · verifier_orchestrator 集成测试。

跑在真实 dev pg(同 test_wave0_schema / test_delegation_service 的 skip 模式)。
依赖 verifier_runs / acceptance_checks / gate_approvals 表 + delegations 表。

覆盖:
  - acceptance_spec.auto_pass=True → verdict='pass' + delegation 转 'done'
  - acceptance_spec.force_human=True → 创建 pending gate_approval
  - artifacts 含 fail_marker → verdict='fail',delegation 留在 in_progress
  - acceptance_spec.gate=True 且其它都 ok → 创建 pending gate_approval
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from backend.core.config import settings


def _has_dev_pg() -> bool:
    """探测 dev pg 是否可连;不可连则 skip 整个文件。"""
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


# ── fixtures ───────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests():
    """跨 test 关掉全局 async engine,避免 asyncpg 跨 loop 串台。"""
    yield
    from backend.core.db import engine
    await engine.dispose()


@pytest.fixture
def fixture_env():
    """每个 test 用独立的 employee 前缀,跑完精准清表。

    流程:
      1. 同步建一条 task(verifier_runs.delegation_id 现在 FK 指向 task.id)
      2. 测试在 fixture_env['delegation_id'] 上跑 orchestrator(等于 task.id)
      3. 退出时按 employee 前缀 + task_id 清五张表
    """
    suffix = uuid.uuid4().hex[:8]
    boss = f"_v2_boss_{suffix}"
    worker = f"_v2_worker_{suffix}"
    parent_task_id = str(uuid.uuid4())
    # 用一个固定 UUID 同时当 task.id 与 delegation.id,绕开 Wave 0 schema 的 FK 折衷
    shared_id = str(uuid.uuid4())

    sync_url = settings.database_url_sync.replace("+psycopg", "")
    conn = psycopg.connect(sync_url)
    try:
        cur = conn.cursor()
        # 建 task 行(满足 verifier_runs.delegation_id FK)
        cur.execute(
            "INSERT INTO task (id, title, priority, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (shared_id, f"v2 test {suffix}", "P2", "pending",
             datetime.now(timezone.utc), datetime.now(timezone.utc)),
        )
        conn.commit()
    finally:
        conn.close()

    yield {
        "boss": boss,
        "worker": worker,
        "parent_task_id": parent_task_id,
        "shared_id": shared_id,
    }

    # 清理
    conn = psycopg.connect(sync_url)
    try:
        cur = conn.cursor()
        # 顺序:子表先清,再清父表
        cur.execute(
            "DELETE FROM gate_approvals WHERE verifier_run_id IN "
            "(SELECT id FROM verifier_runs WHERE delegation_id = %s)",
            (shared_id,),
        )
        cur.execute(
            "DELETE FROM acceptance_checks WHERE verifier_run_id IN "
            "(SELECT id FROM verifier_runs WHERE delegation_id = %s)",
            (shared_id,),
        )
        cur.execute(
            "DELETE FROM verifier_runs WHERE delegation_id = %s",
            (shared_id,),
        )
        cur.execute(
            "DELETE FROM delegation_events WHERE delegation_id IN "
            "(SELECT id FROM delegations WHERE from_employee IN (%s,%s) "
            " OR to_employee IN (%s,%s))",
            (boss, worker, boss, worker),
        )
        cur.execute(
            "DELETE FROM delegations WHERE from_employee IN (%s,%s) "
            "OR to_employee IN (%s,%s)",
            (boss, worker, boss, worker),
        )
        cur.execute("DELETE FROM task WHERE id = %s", (shared_id,))
        conn.commit()
    finally:
        conn.close()


async def _seed_delegation(
    *,
    boss: str,
    worker: str,
    parent_task_id: str,
    shared_id: str,
    acceptance_spec: dict,
    artifacts: dict | None,
):
    """直接用 sqlalchemy 插一条 delegation,id 强制等于 shared_id(=task.id)。

    走完整状态:pending → claimed → in_progress(模拟提案 2 的 'verifying' 占位)。
    """
    from sqlalchemy import insert

    from backend.core.db import AsyncSessionLocal
    from backend.models.proposal1_state import Delegation

    async with AsyncSessionLocal() as s:
        await s.execute(
            insert(Delegation).values(
                id=shared_id,
                from_employee=boss,
                to_employee=worker,
                parent_task_id=parent_task_id,
                title="v2 orchestrator test",
                content="body",
                acceptance_spec=acceptance_spec,
                artifacts=artifacts,
                status="in_progress",
                claimed_at=datetime.now(timezone.utc),
                started_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()


# ── §1 auto_pass → pass + delegation done ──────────────────────────
@pytest.mark.asyncio
async def test_auto_pass_marks_done(fixture_env):
    boss = fixture_env["boss"]
    worker = fixture_env["worker"]
    parent = fixture_env["parent_task_id"]
    shared_id = fixture_env["shared_id"]

    await _seed_delegation(
        boss=boss, worker=worker, parent_task_id=parent, shared_id=shared_id,
        acceptance_spec={
            "auto_pass": True,
            "deliverable_type": "generic_artifacts",
        },
        artifacts={"summary": "stub", "files": []},
    )

    from backend.repos import delegation_repo, verifier_run_repo
    from backend.services import verifier_orchestrator

    result = await verifier_orchestrator.on_delegation_done(shared_id)

    assert result["final_verdict"] == "pass"

    runs = await verifier_run_repo.list_for_delegation(shared_id)
    assert len(runs) == 1
    assert runs[0].llm_verifier_status == "pass"
    assert runs[0].final_verdict == "pass"
    assert runs[0].completed_at is not None

    d = await delegation_repo.get(shared_id)
    assert d is not None
    assert d.status == "done"


# ── §2 force_human → 创建 pending gate ─────────────────────────────
@pytest.mark.asyncio
async def test_force_human_creates_pending_gate(fixture_env):
    boss = fixture_env["boss"]
    worker = fixture_env["worker"]
    parent = fixture_env["parent_task_id"]
    shared_id = fixture_env["shared_id"]

    await _seed_delegation(
        boss=boss, worker=worker, parent_task_id=parent, shared_id=shared_id,
        acceptance_spec={
            "force_human": True,
            "deliverable_type": "generic_artifacts",
        },
        artifacts={"files": []},
    )

    from backend.repos import delegation_repo, verifier_run_repo
    from backend.services import verifier_orchestrator
    from backend.core.db import AsyncSessionLocal
    from backend.models.proposal2_verify import GateApproval
    from sqlalchemy import select

    result = await verifier_orchestrator.on_delegation_done(shared_id)

    assert result["final_verdict"] == "pending_gate"
    assert "gate_id" in result

    # gate_approvals 应该有一条 pending
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(GateApproval).where(GateApproval.id == uuid.UUID(result["gate_id"]))
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "pending"

    runs = await verifier_run_repo.list_for_delegation(shared_id)
    assert runs[0].llm_verifier_status == "needs_human"
    assert runs[0].gate_required is True
    assert runs[0].gate_status == "pending"

    # delegation 状态保留(不会被推到 done)
    d = await delegation_repo.get(shared_id)
    assert d.status == "in_progress"


# ── §3 fail_marker → verdict fail,delegation 不动 ──────────────────
@pytest.mark.asyncio
async def test_fail_marker_marks_fail(fixture_env):
    boss = fixture_env["boss"]
    worker = fixture_env["worker"]
    parent = fixture_env["parent_task_id"]
    shared_id = fixture_env["shared_id"]

    await _seed_delegation(
        boss=boss, worker=worker, parent_task_id=parent, shared_id=shared_id,
        acceptance_spec={
            "deliverable_type": "generic_artifacts",
        },
        artifacts={"fail_marker": "spec violated", "files": []},
    )

    from backend.repos import delegation_repo, verifier_run_repo
    from backend.services import verifier_orchestrator

    result = await verifier_orchestrator.on_delegation_done(shared_id)
    assert result["final_verdict"] == "fail"

    runs = await verifier_run_repo.list_for_delegation(shared_id)
    assert runs[0].llm_verifier_status == "fail"
    assert runs[0].final_verdict == "fail"

    # delegation 状态不动(等主进程飞书反馈链)
    d = await delegation_repo.get(shared_id)
    assert d.status == "in_progress"


# ── §4 gate=True 且闸 1/2 都 ok → 创建 pending gate ────────────────
@pytest.mark.asyncio
async def test_gate_required_creates_pending_gate(fixture_env):
    boss = fixture_env["boss"]
    worker = fixture_env["worker"]
    parent = fixture_env["parent_task_id"]
    shared_id = fixture_env["shared_id"]

    # 给一个 build123d_py 类型 + required_files 都齐全 + auto_pass
    with tempfile.TemporaryDirectory() as td:
        py = os.path.join(td, "ok.py")
        with open(py, "w") as f:
            f.write('print("ok")\n')

        await _seed_delegation(
            boss=boss, worker=worker, parent_task_id=parent, shared_id=shared_id,
            acceptance_spec={
                "auto_pass": True,
                "gate": True,
                "deliverable_type": "build123d_py",
                "required_files": [py],
            },
            artifacts={"py_file": py, "files": [py]},
        )

        from backend.repos import delegation_repo, verifier_run_repo, acceptance_check_repo
        from backend.services import verifier_orchestrator

        result = await verifier_orchestrator.on_delegation_done(shared_id)

    assert result["final_verdict"] == "pending_gate"
    runs = await verifier_run_repo.list_for_delegation(shared_id)
    run = runs[0]
    assert run.llm_verifier_status == "pass"
    assert run.ground_truth_status == "ok"
    assert run.gate_required is True
    assert run.gate_status == "pending"

    # ground_truth 应该真的跑过两个 checker
    checks = await acceptance_check_repo.list_for_run(run.id)
    names = {c.check_name for c in checks}
    assert "build123d_executable" in names
    assert "output_artifacts_present" in names
    assert all(c.ok for c in checks)

    # delegation 没被推到 done(等 gate)
    d = await delegation_repo.get(shared_id)
    assert d.status == "in_progress"


# ── §5 handle_gate_decision approved → done ────────────────────────
@pytest.mark.asyncio
async def test_gate_approved_promotes_done(fixture_env):
    boss = fixture_env["boss"]
    worker = fixture_env["worker"]
    parent = fixture_env["parent_task_id"]
    shared_id = fixture_env["shared_id"]

    await _seed_delegation(
        boss=boss, worker=worker, parent_task_id=parent, shared_id=shared_id,
        acceptance_spec={
            "auto_pass": True,
            "gate": True,
            "deliverable_type": "generic_artifacts",
        },
        artifacts={"files": []},
    )
    from backend.repos import delegation_repo, verifier_run_repo
    from backend.services import verifier_orchestrator

    result = await verifier_orchestrator.on_delegation_done(shared_id)
    assert result["final_verdict"] == "pending_gate"
    gate_id = result["gate_id"]

    # 模拟 CEO 在飞书点"通过"
    await verifier_orchestrator.handle_gate_decision(
        gate_id, decision="approved", decided_by="ceo_user_id", reason="LGTM",
    )

    runs = await verifier_run_repo.list_for_delegation(shared_id)
    assert runs[0].final_verdict == "pass"
    assert runs[0].gate_status == "approved"

    d = await delegation_repo.get(shared_id)
    assert d.status == "done"
