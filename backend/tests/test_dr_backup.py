"""Wave 4 · 提案 4 §5.5 — DRBackup 模型 + dr_backup_repo 单元测试。

跑在内存 SQLite + Base.metadata.create_all,不依赖 dev pg。

覆盖:
  1. ``create`` 写一条 status='in_progress'
  2. ``mark_success`` 改字段 + 写 size_bytes
  3. ``mark_failed`` 追加 note 不覆盖
  4. ``latest`` 仅返回 success;失败不参与
  5. ``list_recent`` 按 created_at 降序
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# 关键:import 才会让 DRBackup 注册到 Base.metadata
from backend.core.db import Base
from backend.models import dr as _dr_models  # noqa: F401
from backend.models.dr import DRBackup
from backend.repos import dr_backup_repo


@pytest.fixture
async def in_memory_db(monkeypatch):
    """内存 SQLite + 把 dr_backup_repo 的 AsyncSessionLocal 切到该 engine。

    repo 内部通过 ``AsyncSessionLocal`` 拿 session,这里 monkeypatch 那个
    模块全局,避免连真 pg。
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    # 仅建 dr_backups 表,绕开其它 PG-only 表
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[DRBackup.__table__])
        )
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(dr_backup_repo, "AsyncSessionLocal", SessionLocal)
    yield SessionLocal

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.drop_all(c, tables=[DRBackup.__table__])
        )
    await engine.dispose()


# ── 模型层基础 ────────────────────────────────────────────────────
async def test_create_writes_in_progress_row(in_memory_db):
    row = await dr_backup_repo.create(s3_key="s3://company-dr/foo.dump")
    assert row.id is not None
    assert row.status == "in_progress"
    assert row.s3_key == "s3://company-dr/foo.dump"
    assert row.size_bytes is None
    assert row.created_at is not None


async def test_mark_success_updates_size_and_status(in_memory_db):
    row = await dr_backup_repo.create(s3_key="s3://company-dr/a.dump")
    updated = await dr_backup_repo.mark_success(
        row.id, size_bytes=12345, pg_db_size_at_backup=98765,
    )
    assert updated is not None
    assert updated.status == "success"
    assert updated.size_bytes == 12345
    assert updated.pg_db_size_at_backup == 98765


async def test_mark_success_can_override_s3_key(in_memory_db):
    """boto3 失败降级到本地路径时,wrapper 可在 mark_success 阶段改 s3_key。"""
    row = await dr_backup_repo.create(s3_key="s3://company-dr/b.dump")
    updated = await dr_backup_repo.mark_success(
        row.id, size_bytes=100, s3_key="/tmp/dr/b.dump",
    )
    assert updated.s3_key == "/tmp/dr/b.dump"


async def test_mark_failed_appends_note(in_memory_db):
    row = await dr_backup_repo.create(
        s3_key="s3://company-dr/c.dump", note="initial note",
    )
    updated = await dr_backup_repo.mark_failed(row.id, note="upload failed")
    assert updated is not None
    assert updated.status == "failed"
    # 追加而非覆盖
    assert "initial note" in updated.note
    assert "upload failed" in updated.note


async def test_mark_success_returns_none_for_unknown_id(in_memory_db):
    assert await dr_backup_repo.mark_success(99999, size_bytes=1) is None


# ── latest / list_recent ──────────────────────────────────────────
async def test_latest_returns_only_success(in_memory_db):
    # 写 3 条:成功、失败、成功(后写)
    r1 = await dr_backup_repo.create(s3_key="s3://x/1.dump")
    await dr_backup_repo.mark_success(r1.id, size_bytes=1)

    r2 = await dr_backup_repo.create(s3_key="s3://x/2.dump")
    await dr_backup_repo.mark_failed(r2.id, note="boom")

    r3 = await dr_backup_repo.create(s3_key="s3://x/3.dump")
    await dr_backup_repo.mark_success(r3.id, size_bytes=3)

    latest = await dr_backup_repo.latest()
    assert latest is not None
    # 取最新成功 = r3
    assert latest.id == r3.id
    assert latest.s3_key == "s3://x/3.dump"


async def test_latest_returns_none_when_no_success(in_memory_db):
    r = await dr_backup_repo.create(s3_key="s3://x/only-fail.dump")
    await dr_backup_repo.mark_failed(r.id, note="fail")
    assert await dr_backup_repo.latest() is None


async def test_list_recent_returns_descending(in_memory_db):
    ids = []
    for i in range(5):
        row = await dr_backup_repo.create(s3_key=f"s3://x/{i}.dump")
        ids.append(row.id)
    rows = await dr_backup_repo.list_recent(limit=3)
    assert len(rows) == 3
    # 最新写入排前
    assert rows[0].id == ids[-1]
    assert rows[2].id == ids[-3]


# ── 模型字段类型基本健康 ───────────────────────────────────────────
def test_drbackup_model_columns_present():
    """sanity:确保 DRBackup 列名不被未来重构无意中改掉。"""
    cols = {c.name for c in DRBackup.__table__.columns}
    assert {
        "id", "created_at", "s3_key", "size_bytes",
        "status", "pg_db_size_at_backup", "note",
    }.issubset(cols)
