"""提案 3 · Loop 1 retro_agent 集成测试。

跑在真实 dev pg 上(同 test_verifier_orchestrator 的 skip 模式)。
覆盖:
  - success retro:attempt > 1 写一条 retry lesson
  - failure retro:从 ok=False 的 acceptance_checks 抽 lesson(每条 1 个)
  - failure retro:无 acceptance_checks 时 fallback 写 generic llm_verifier_fail
  - cancelled retro:status=cancelled 写 1 条低 severity lesson
  - 失败 swallow:delegation 不存在,返回空列表不抛异常
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from backend.core.config import settings


def _has_dev_pg() -> bool:
    try:
        c = psycopg.connect(
            settings.database_url_sync.replace("+psycopg", ""),
            connect_timeout=2,
        )
        c.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_dev_pg(), reason="需要 dev postgres")


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests():
    """避免 asyncpg 跨 loop 串台。"""
    yield
    from backend.core.db import engine
    await engine.dispose()


@pytest.fixture
def force_fake_embedding(monkeypatch):
    """强制走伪 embedding,避免本地 dev 调真 OpenAI。"""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    yield


@pytest.fixture
def fixture_env(force_fake_embedding):
    """每个 test 用独立 employee 前缀 + shared_id(同 test_verifier_orchestrator)。"""
    suffix = uuid.uuid4().hex[:8]
    boss = f"_retro_boss_{suffix}"
    worker = f"_retro_worker_{suffix}"
    parent_task_id = str(uuid.uuid4())
    shared_id = str(uuid.uuid4())

    sync_url = settings.database_url_sync.replace("+psycopg", "")
    conn = psycopg.connect(sync_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO task (id, title, priority, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (shared_id, f"retro test {suffix}", "P2", "pending",
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
        "suffix": suffix,
    }

    # 清理:lessons → acceptance_checks → verifier_runs → delegation_events → delegations → task
    conn = psycopg.connect(sync_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM lessons WHERE employee_key IN (%s, %s) OR source_task_id = %s",
            (boss, worker, parent_task_id),
        )
        cur.execute(
            "DELETE FROM acceptance_checks WHERE verifier_run_id IN "
            "(SELECT id FROM verifier_runs WHERE delegation_id = %s)",
            (shared_id,),
        )
        cur.execute(
            "DELETE FROM verifier_runs WHERE delegation_id = %s", (shared_id,),
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
    status: str,
    acceptance_spec: dict | None = None,
):
    """直接 ORM 插一条 delegation,id 强制等于 shared_id(=task.id)。"""
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
                title="retro test",
                content="body",
                acceptance_spec=acceptance_spec or {},
                status=status,
                claimed_at=datetime.now(timezone.utc),
                started_at=datetime.now(timezone.utc),
                done_at=datetime.now(timezone.utc) if status == "done" else None,
            )
        )
        await s.commit()


# ── §1 success retro:attempt > 1 写 retry lesson ─────────────────────
@pytest.mark.asyncio
async def test_retry_to_pass_writes_one_lesson(fixture_env):
    boss = fixture_env["boss"]
    worker = fixture_env["worker"]
    parent = fixture_env["parent_task_id"]
    shared_id = fixture_env["shared_id"]

    from backend.repos import lessons_repo, verifier_run_repo
    from backend.services import retro_agent

    await _seed_delegation(
        boss=boss, worker=worker, parent_task_id=parent,
        shared_id=shared_id, status="done",
    )
    # 模拟两次 attempt:第二次 pass
    run1 = await verifier_run_repo.create(shared_id, attempt=1)
    await verifier_run_repo.update(run1.id, final_verdict="fail")
    run2 = await verifier_run_repo.create(shared_id, attempt=2)
    await verifier_run_repo.update(run2.id, final_verdict="pass")

    new_ids = await retro_agent.run_retro_for_delegation(shared_id)
    assert len(new_ids) == 1, f"应写 1 条 retry lesson,实际 {new_ids}"

    lesson = await lessons_repo.get(new_ids[0])
    assert lesson is not None
    assert lesson["pattern_tag"] == "retry_to_pass"
    assert lesson["severity"] == 5
    assert lesson["employee_key"] == worker
    assert "2 次重试" in lesson["title"]


# ── §2 failure retro:从 acceptance_checks 抽 lesson ──────────────────
@pytest.mark.asyncio
async def test_failure_extracts_per_failed_check(fixture_env):
    boss = fixture_env["boss"]
    worker = fixture_env["worker"]
    parent = fixture_env["parent_task_id"]
    shared_id = fixture_env["shared_id"]

    from backend.repos import (
        acceptance_check_repo,
        lessons_repo,
        verifier_run_repo,
    )
    from backend.services import retro_agent

    await _seed_delegation(
        boss=boss, worker=worker, parent_task_id=parent,
        shared_id=shared_id, status="in_progress",
    )
    run = await verifier_run_repo.create(shared_id, attempt=1)
    await verifier_run_repo.update(run.id, final_verdict="fail")
    # 写 2 个 fail check + 1 个 pass check;只有 fail 应被抽出来
    await acceptance_check_repo.record(
        run.id, check_name="missing_required_files",
        ok=False, duration_ms=10, err_msg="缺少 step.py",
    )
    await acceptance_check_repo.record(
        run.id, check_name="output_artifacts_present",
        ok=False, duration_ms=12, err_msg="artifacts.files 为空",
    )
    await acceptance_check_repo.record(
        run.id, check_name="build123d_executable",
        ok=True, duration_ms=20,
    )

    new_ids = await retro_agent.run_retro_for_delegation(shared_id)
    assert len(new_ids) == 2, f"应写 2 条 fail lesson,实际 {new_ids}"

    tags = set()
    for lid in new_ids:
        lesson = await lessons_repo.get(lid)
        assert lesson is not None
        assert lesson["severity"] == 7
        assert lesson["employee_key"] == worker
        tags.add(lesson["pattern_tag"])
    assert tags == {"missing_required_files", "output_artifacts_present"}


# ── §3 failure retro 无 acceptance_checks → fallback generic ─────────
@pytest.mark.asyncio
async def test_failure_without_checks_writes_generic(fixture_env):
    boss = fixture_env["boss"]
    worker = fixture_env["worker"]
    parent = fixture_env["parent_task_id"]
    shared_id = fixture_env["shared_id"]

    from backend.repos import lessons_repo, verifier_run_repo
    from backend.services import retro_agent

    await _seed_delegation(
        boss=boss, worker=worker, parent_task_id=parent,
        shared_id=shared_id, status="in_progress",
    )
    run = await verifier_run_repo.create(shared_id, attempt=1)
    await verifier_run_repo.update(
        run.id, final_verdict="fail",
        llm_verifier_status="fail",
        llm_verifier_reason="LLM 觉得交付物与 acceptance_spec 不一致",
    )

    new_ids = await retro_agent.run_retro_for_delegation(shared_id)
    assert len(new_ids) == 1, f"应 fallback 写 1 条 generic,实际 {new_ids}"

    lesson = await lessons_repo.get(new_ids[0])
    assert lesson["pattern_tag"] == "llm_verifier_fail"
    assert lesson["severity"] == 7
    assert "LLM 觉得交付物与 acceptance_spec 不一致" in lesson["body"]


# ── §4 cancelled retro:写一条低 severity lesson ─────────────────────
@pytest.mark.asyncio
async def test_cancelled_writes_low_severity_lesson(fixture_env):
    boss = fixture_env["boss"]
    worker = fixture_env["worker"]
    parent = fixture_env["parent_task_id"]
    shared_id = fixture_env["shared_id"]

    from backend.repos import lessons_repo
    from backend.services import retro_agent

    await _seed_delegation(
        boss=boss, worker=worker, parent_task_id=parent,
        shared_id=shared_id, status="cancelled",
    )

    new_ids = await retro_agent.run_retro_for_delegation(shared_id)
    assert len(new_ids) == 1

    lesson = await lessons_repo.get(new_ids[0])
    assert lesson["pattern_tag"] == "task_cancelled"
    assert lesson["severity"] == 3
    assert lesson["employee_key"] == worker


# ── §5 失败 swallow:delegation 不存在 → 返回空列表不抛 ───────────────
@pytest.mark.asyncio
async def test_missing_delegation_swallows():
    from backend.services import retro_agent

    fake_id = str(uuid.uuid4())
    new_ids = await retro_agent.run_retro_for_delegation(fake_id)
    assert new_ids == []


# ── §6 done 但 attempt=1 → 不写 lesson ────────────────────────────────
@pytest.mark.asyncio
async def test_first_pass_writes_nothing(fixture_env):
    boss = fixture_env["boss"]
    worker = fixture_env["worker"]
    parent = fixture_env["parent_task_id"]
    shared_id = fixture_env["shared_id"]

    from backend.repos import verifier_run_repo
    from backend.services import retro_agent

    await _seed_delegation(
        boss=boss, worker=worker, parent_task_id=parent,
        shared_id=shared_id, status="done",
    )
    run = await verifier_run_repo.create(shared_id, attempt=1)
    await verifier_run_repo.update(run.id, final_verdict="pass")

    new_ids = await retro_agent.run_retro_for_delegation(shared_id)
    assert new_ids == [], "首次就过的成功不应写 lesson"
