"""提案 1 — delegation_service / delegation_repo / supervisor 集成测试。

跑在真实 dev pg 上(同 test_wave0_schema.py 的 skip 模式)。
覆盖:
  - 状态转移合法/非法
  - 完整生命周期 pending → claimed → in_progress → done
  - 撤回 cancelled
  - supervisor 单次扫描:首次 nudge / 升级 escalate / 冷却期跳过
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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
    """同 test_context_builder.py:dispose 全局 engine,避免跨 loop asyncpg 串台。"""
    yield
    from backend.core.db import engine
    await engine.dispose()


@pytest.fixture
def cleanup_pg():
    """每个测试用唯一 employee 前缀,跑完按 employee_key 清。"""
    suffix = uuid.uuid4().hex[:8]
    boss = f"_test_boss_{suffix}"
    worker = f"_test_worker_{suffix}"
    parent_task = str(uuid.uuid4())
    yield {"boss": boss, "worker": worker, "parent_task": parent_task}

    conn = psycopg.connect(settings.database_url_sync.replace("+psycopg", ""))
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM delegation_events WHERE delegation_id IN "
            "(SELECT id FROM delegations WHERE from_employee IN (%s,%s) OR to_employee IN (%s,%s))",
            (boss, worker, boss, worker),
        )
        cur.execute(
            "DELETE FROM delegations WHERE from_employee IN (%s,%s) OR to_employee IN (%s,%s)",
            (boss, worker, boss, worker),
        )
        conn.commit()
    finally:
        conn.close()


# ── §1 完整生命周期 ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_full_lifecycle_pending_to_done(cleanup_pg):
    boss, worker, task = cleanup_pg["boss"], cleanup_pg["worker"], cleanup_pg["parent_task"]
    from backend.services import delegation_service

    d = await delegation_service.create_delegation(
        from_employee=boss, to_employee=worker,
        parent_task_id=task,
        title="做 X", content="详情",
        acceptance_spec={"must_have": ["x"]},
        due_in_minutes=120,
    )
    assert d.status == "pending"
    assert d.due_at is not None

    d2 = await delegation_service.claim_delegation(d.id, claimer_employee=worker)
    assert d2.status == "claimed"
    assert d2.claimed_at is not None

    d3 = await delegation_service.update_progress(d.id, "已起草 v1", percent=30)
    assert d3.status == "in_progress"
    assert d3.started_at is not None

    d4 = await delegation_service.update_progress(d.id, "继续推进", percent=70)
    assert d4.status == "in_progress"  # 不重复切

    d5 = await delegation_service.complete_delegation(
        d.id, artifacts={"summary": "搞定", "files": ["a.md"]},
    )
    assert d5.status == "done"
    assert d5.done_at is not None
    assert d5.artifacts == {"summary": "搞定", "files": ["a.md"]}


# ── §2 非法转移 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_illegal_transition_raises(cleanup_pg):
    boss, worker, task = cleanup_pg["boss"], cleanup_pg["worker"], cleanup_pg["parent_task"]
    from backend.repos import delegation_repo
    from backend.services import delegation_service

    d = await delegation_service.create_delegation(
        from_employee=boss, to_employee=worker,
        parent_task_id=task,
        title="t", content="c",
        due_in_minutes=60,
    )
    # pending → done 是非法的
    with pytest.raises(ValueError, match="illegal transition"):
        await delegation_repo.transition_to(d.id, "done")

    # done 是终态:先把它走到 done
    await delegation_service.claim_delegation(d.id, claimer_employee=worker)
    await delegation_service.update_progress(d.id, "p")
    await delegation_service.complete_delegation(d.id, artifacts={"summary": "ok"})

    # done → in_progress 非法
    with pytest.raises(ValueError, match="illegal transition"):
        await delegation_repo.transition_to(d.id, "in_progress")


# ── §3 接活方校验 ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_claim_requires_correct_to_employee(cleanup_pg):
    boss, worker, task = cleanup_pg["boss"], cleanup_pg["worker"], cleanup_pg["parent_task"]
    from backend.services import delegation_service

    d = await delegation_service.create_delegation(
        from_employee=boss, to_employee=worker,
        parent_task_id=task,
        title="t", content="c",
        due_in_minutes=60,
    )
    with pytest.raises(PermissionError):
        await delegation_service.claim_delegation(d.id, claimer_employee="someone_else")


# ── §4 撤回 ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cancel(cleanup_pg):
    boss, worker, task = cleanup_pg["boss"], cleanup_pg["worker"], cleanup_pg["parent_task"]
    from backend.services import delegation_service

    d = await delegation_service.create_delegation(
        from_employee=boss, to_employee=worker,
        parent_task_id=task,
        title="t", content="c",
        due_in_minutes=60,
    )
    d2 = await delegation_service.cancel_delegation(d.id, canceller_employee=boss, reason="不需要了")
    assert d2.status == "cancelled"


# ── §5 list_in_flight / list_pending_claim ─────────────────────────
@pytest.mark.asyncio
async def test_list_filters(cleanup_pg):
    boss, worker, task = cleanup_pg["boss"], cleanup_pg["worker"], cleanup_pg["parent_task"]
    from backend.repos import delegation_repo
    from backend.services import delegation_service

    d_pending = await delegation_service.create_delegation(
        from_employee=boss, to_employee=worker,
        parent_task_id=task,
        title="待认领", content="c", due_in_minutes=60,
    )
    d_done = await delegation_service.create_delegation(
        from_employee=boss, to_employee=worker,
        parent_task_id=task,
        title="待完成", content="c", due_in_minutes=60,
    )
    await delegation_service.claim_delegation(d_done.id, claimer_employee=worker)
    await delegation_service.update_progress(d_done.id, "go")
    await delegation_service.complete_delegation(d_done.id, artifacts={"summary": "ok"})

    out_list = await delegation_repo.list_in_flight_for_employee(boss, "out")
    out_titles = {x.title for x in out_list}
    assert "待认领" in out_titles
    assert "待完成" not in out_titles  # done 不算 in-flight

    in_list = await delegation_repo.list_in_flight_for_employee(worker, "in")
    in_titles = {x.title for x in in_list}
    assert "待认领" in in_titles

    pending_claim = await delegation_repo.list_pending_claim_for_employee(worker)
    assert {x.title for x in pending_claim} == {"待认领"}


# ── §6 supervise_once 首次 nudge ──────────────────────────────────
@pytest.mark.asyncio
async def test_supervise_once_first_nudge(cleanup_pg):
    """让一条 due_at 已过期且没 nudge 过的记录被 supervisor 首次 nudge。"""
    boss, worker, task = cleanup_pg["boss"], cleanup_pg["worker"], cleanup_pg["parent_task"]
    from backend.core.db import AsyncSessionLocal
    from backend.models.proposal1_state import Delegation
    from backend.repos import delegation_repo
    from backend.services import delegation_service, delegation_supervisor
    from sqlalchemy import update

    d = await delegation_service.create_delegation(
        from_employee=boss, to_employee=worker,
        parent_task_id=task,
        title="过期任务", content="c",
        due_in_minutes=10,
    )
    # 把 due_at 拉到 1 小时前模拟过期
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Delegation)
            .where(Delegation.id == d.id)
            .values(due_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await s.commit()

    stat = await delegation_supervisor.supervise_once()
    assert stat["scanned"] >= 1
    assert stat["nudged"] >= 1

    refreshed = await delegation_repo.get(d.id)
    assert refreshed.nudge_count >= 1
    assert refreshed.last_nudge_at is not None


# ── §7 supervise_once 升级 escalate ───────────────────────────────
@pytest.mark.asyncio
async def test_supervise_once_escalate(cleanup_pg):
    """已经 nudge 过 + last_nudge_at 距今 >30min + 状态 in_progress → 升级。"""
    boss, worker, task = cleanup_pg["boss"], cleanup_pg["worker"], cleanup_pg["parent_task"]
    from backend.core.db import AsyncSessionLocal
    from backend.models.proposal1_state import Delegation
    from backend.repos import delegation_repo
    from backend.services import delegation_service, delegation_supervisor
    from sqlalchemy import update

    d = await delegation_service.create_delegation(
        from_employee=boss, to_employee=worker,
        parent_task_id=task,
        title="即将升级", content="c",
        due_in_minutes=10,
    )
    # 走到 in_progress
    await delegation_service.claim_delegation(d.id, claimer_employee=worker)
    await delegation_service.update_progress(d.id, "做着")

    # 把 due_at 拉到 2 小时前 + last_nudge_at 拉到 45 分钟前
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Delegation)
            .where(Delegation.id == d.id)
            .values(
                due_at=datetime.now(timezone.utc) - timedelta(hours=2),
                last_nudge_at=datetime.now(timezone.utc) - timedelta(minutes=45),
                nudge_count=1,
            )
        )
        await s.commit()

    stat = await delegation_supervisor.supervise_once()
    assert stat["escalated"] >= 1

    refreshed = await delegation_repo.get(d.id)
    assert refreshed.status == "escalated"
    assert refreshed.escalated_at is not None
