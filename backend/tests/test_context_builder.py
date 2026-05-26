"""提案 1 — context_builder 集成测试(跑在真实 dev pg 上)。

不可用 SQLite,因为:
  - employee_memory 用了 pgvector(在 conftest 中已 skip)
  - task_context 用了 pgvector + UUID
  - delegations 用了 server_default=gen_random_uuid()(pgcrypto)

skip 模式参考 test_wave0_schema.py:dev pg 不可用时跳过,不伪造通过。
"""
from __future__ import annotations

import asyncio
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
    """每个测试结束 dispose 全局 engine,避免 asyncpg 连接跨 event loop 串台。

    pytest-asyncio 默认每个 test 一个新 loop,但 backend.core.db.engine 是模块级
    单例,池里的连接绑在旧 loop 上,导致下一个 test 抛
    "another operation is in progress"。
    """
    yield
    from backend.core.db import engine
    await engine.dispose()


@pytest.fixture
def cleanup_pg():
    """每个测试用例独立的清理 fixture:跑完抹掉本测试写的 employee/task 数据。

    用一个唯一 employee_key 前缀和一个唯一 task_id,测试结束后 DELETE。
    """
    suffix = uuid.uuid4().hex[:8]
    employee_key = f"_test_emp_{suffix}"
    task_id = str(uuid.uuid4())
    yield {"employee_key": employee_key, "task_id": task_id}

    # cleanup:psycopg 同步删干净
    conn = psycopg.connect(settings.database_url_sync.replace("+psycopg", ""))
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM employee_memory WHERE employee_key = %s", (employee_key,))
        cur.execute("DELETE FROM delegation_events WHERE delegation_id IN "
                    "(SELECT id FROM delegations WHERE from_employee = %s OR to_employee = %s)",
                    (employee_key, employee_key))
        cur.execute("DELETE FROM delegations WHERE from_employee = %s OR to_employee = %s",
                    (employee_key, employee_key))
        cur.execute("DELETE FROM task_context WHERE task_id = %s", (task_id,))
        conn.commit()
    finally:
        conn.close()


# ── helpers ─────────────────────────────────────────────────────────
async def _seed_memory(employee_key: str, content: str, importance: int = 5,
                       pinned: bool = False) -> None:
    from backend.core.db import AsyncSessionLocal
    from backend.models.memory import EmployeeMemory
    async with AsyncSessionLocal() as s:
        s.add(EmployeeMemory(
            employee_key=employee_key,
            content=content,
            importance=importance,
            pinned=pinned,
        ))
        await s.commit()


async def _seed_task_context(task_id: str, employee_key: str, role: str,
                             content: str) -> None:
    from backend.repos import task_context_repo
    await task_context_repo.append_context(
        task_id=task_id, employee_key=employee_key, role=role,
        content_chunk=content,
    )


# ── §1 空数据返回空串 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_empty_returns_empty_string(cleanup_pg):
    from backend.services.context_builder import build_context_preamble
    out = await build_context_preamble(
        employee_key=cleanup_pg["employee_key"],
        task_id=cleanup_pg["task_id"],
    )
    assert out == "", f"空数据应返回空串,得到: {out!r}"


# ── §2 L1 长期记忆段渲染 ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_l1_memory_section(cleanup_pg):
    emp = cleanup_pg["employee_key"]
    await _seed_memory(emp, "上周做电池仓时和 firmware 约定 5V 输入", importance=8)
    await _seed_memory(emp, "PCB 选型偏好 4 层板", importance=6, pinned=True)

    from backend.services.context_builder import build_context_preamble
    out = await build_context_preamble(employee_key=emp, task_id=None)
    assert "[CONTEXT" in out
    assert "## 你的长期记忆" in out
    assert "电池仓" in out
    assert "PCB 选型" in out
    # pinned 应该带 📌
    assert "📌" in out


# ── §3 L2 任务上下文段 + token 截断 ─────────────────────────────────
@pytest.mark.asyncio
async def test_l2_task_context_section(cleanup_pg):
    emp = cleanup_pg["employee_key"]
    task = cleanup_pg["task_id"]
    await _seed_task_context(task, "pm", "speak",
                             "PM 14:32 派给你 做电池仓")
    await _seed_task_context(task, emp, "act",
                             "我读了 spec.md 并起草 v1 草图")
    await _seed_task_context(task, "testing", "decide",
                             "testing 14:45 打回:厚度超 2mm")

    from backend.services.context_builder import build_context_preamble
    out = await build_context_preamble(employee_key=emp, task_id=task)
    assert "## 本任务此前发生了什么" in out
    assert "做电池仓" in out
    assert "spec.md" in out
    assert "厚度超 2mm" in out


# ── §4 派活方:派出去未回的活段 ──────────────────────────────────────
@pytest.mark.asyncio
async def test_out_delegation_section_with_overdue(cleanup_pg):
    emp = cleanup_pg["employee_key"]
    parent_task = cleanup_pg["task_id"]
    from backend.services import delegation_service

    # 一条已超 SLA 的(due_in_minutes 设负数让它立即过期)
    overdue = await delegation_service.create_delegation(
        from_employee=emp, to_employee="firmware",
        parent_task_id=parent_task,
        title="电流上限确认",
        content="把电池保护板电流上限定下来",
        due_in_minutes=1,
    )
    # 把 due_at 拉到 2 小时前模拟超时
    from backend.core.db import AsyncSessionLocal
    from backend.models.proposal1_state import Delegation
    from sqlalchemy import update
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Delegation)
            .where(Delegation.id == overdue.id)
            .values(due_at=datetime.now(timezone.utc) - timedelta(hours=2))
        )
        await s.commit()

    # 一条还没到期的
    await delegation_service.create_delegation(
        from_employee=emp, to_employee="mechanical",
        parent_task_id=parent_task,
        title="结构件三视图",
        content="出三视图",
        due_in_minutes=120,
    )

    from backend.services.context_builder import build_context_preamble
    out = await build_context_preamble(employee_key=emp, task_id=None)
    assert "## 你派出去未回的活" in out
    assert "电流上限确认" in out
    assert "⚠️" in out, "超 SLA 应有 ⚠️ 标记"
    assert "结构件三视图" in out


# ── §5 接活方:未认领段 ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_in_pending_section(cleanup_pg):
    emp = cleanup_pg["employee_key"]
    parent_task = cleanup_pg["task_id"]
    from backend.services import delegation_service

    await delegation_service.create_delegation(
        from_employee="pm", to_employee=emp,
        parent_task_id=parent_task,
        title="PCB 选型",
        content="今天给方案",
        due_in_minutes=120,
    )

    from backend.services.context_builder import build_context_preamble
    out = await build_context_preamble(employee_key=emp, task_id=None)
    assert "## 你手头未认领的活" in out
    assert "PCB 选型" in out
    assert "📥" in out


# ── §6 四段同时存在 ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_all_four_sections_together(cleanup_pg):
    emp = cleanup_pg["employee_key"]
    task = cleanup_pg["task_id"]
    await _seed_memory(emp, "L1 一条记忆", importance=7)
    await _seed_task_context(task, emp, "act", "L2 一条 chunk")
    from backend.services import delegation_service
    await delegation_service.create_delegation(
        from_employee=emp, to_employee="firmware",
        parent_task_id=task,
        title="OUT 活",
        content="ddd",
        due_in_minutes=120,
    )
    await delegation_service.create_delegation(
        from_employee="pm", to_employee=emp,
        parent_task_id=task,
        title="IN 活",
        content="eee",
        due_in_minutes=120,
    )

    from backend.services.context_builder import build_context_preamble
    out = await build_context_preamble(employee_key=emp, task_id=task)
    for marker in (
        "## 你的长期记忆",
        "## 本任务此前发生了什么",
        "## 你派出去未回的活",
        "## 你手头未认领的活",
    ):
        assert marker in out, f"应包含段: {marker}, 实际:\n{out}"
    assert out.startswith("[CONTEXT")
    assert out.rstrip().endswith("[/CONTEXT]")
