"""B1.2 — task_step 写入助手单测。

验证 group_chat.task_steps.step_record 上下文管理器:
- task_id=None 时整体 noop
- 正常完成 → status=done + finished_at
- 节点抛异常 → status=failed + output 含 ERROR
- chat_id "task:{id}" 提取 helpers
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.db import Base
from backend.models.task import Task, TaskStep
from group_chat import task_steps as ts


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_SKIP_TABLES = {  # PG-only 类型(Vector / ARRAY),与 conftest._SKIP_TABLES 同步
    "employee_memory", "task_context", "kb_documents", "kb_retrieval_log",
    "lessons", "pattern_extracts", "routing_decisions",
}


def _sqlite_compatible_tables():
    return [t for name, t in Base.metadata.tables.items() if name not in _SKIP_TABLES]


@pytest.fixture
async def step_db(monkeypatch):
    """In-memory SQLite + monkeypatch task_steps.AsyncSessionLocal。

    返回 sessionmaker,供测试代码直接读 task_step 行验证。
    """
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    tables = _sqlite_compatible_tables()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(ts, "AsyncSessionLocal", Session)

    yield Session

    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
    await engine.dispose()


async def _insert_task(Session, title="测试任务") -> str:
    task_id = str(uuid.uuid4())
    async with Session() as db:
        db.add(Task(id=task_id, title=title))
        await db.commit()
    return task_id


# ── 基础 helper 测试 ─────────────────────────────────────────────────────────

def test_extract_task_id_strips_prefix():
    assert ts.extract_task_id("task:abc-123") == "abc-123"
    assert ts.extract_task_id("oc_群id") is None
    assert ts.extract_task_id("") is None
    assert ts.extract_task_id(None) is None


def test_is_task_chat():
    assert ts.is_task_chat("task:foo") is True
    assert ts.is_task_chat("oc_xxx") is False
    assert ts.is_task_chat(None) is False


# ── step_record 行为测试 ──────────────────────────────────────────────────────

async def test_step_record_writes_done_on_success(step_db):
    """正常完成 → status=done, finished_at 非空"""
    task_id = await _insert_task(step_db)

    async with ts.step_record(task_id, "decide", input_summary="hello"):
        pass  # body 不抛异常

    async with step_db() as db:
        rows = (await db.execute(
            select(TaskStep).where(TaskStep.task_id == task_id)
        )).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.step_name == "decide"
    assert row.status == "done"
    assert row.input == "hello"
    assert row.started_at is not None
    assert row.finished_at is not None
    assert row.output is None


async def test_step_record_writes_failed_on_exception(step_db):
    """节点抛异常 → status=failed, output 含 ERROR repr,异常仍向外传播"""
    task_id = await _insert_task(step_db)

    with pytest.raises(ValueError):
        async with ts.step_record(task_id, "decide", input_summary="boom-input"):
            raise ValueError("boom")

    async with step_db() as db:
        row = (await db.execute(
            select(TaskStep).where(TaskStep.task_id == task_id)
        )).scalar_one()

    assert row.status == "failed"
    assert row.finished_at is not None
    assert "boom" in (row.output or "")
    assert "ValueError" in (row.output or "")


async def test_step_record_noop_when_task_id_none(step_db):
    """task_id=None → 整体 noop, 不写表"""
    async with ts.step_record(None, "decide", input_summary="ignored"):
        pass

    # 表里应当没任何 task_step 行(fixture 是 fresh DB)
    async with step_db() as db:
        rows = (await db.execute(select(TaskStep))).scalars().all()
    assert len(rows) == 0


async def test_step_record_truncates_long_input(step_db):
    """input_summary 超过 2000 字符要被截断"""
    task_id = await _insert_task(step_db)
    long_input = "a" * 5000

    async with ts.step_record(task_id, "decide", input_summary=long_input):
        pass

    async with step_db() as db:
        row = (await db.execute(select(TaskStep))).scalar_one()
    assert len(row.input) == 2000
