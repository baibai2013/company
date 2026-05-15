"""
长期记忆 + 语义检索测试 — M-A1 ~ M-A8

测试覆盖：
  - memory_repo 模块级函数 save / get_recent / search_semantic
  - OPENAI_BASE_URL 配置传入 AsyncOpenAI
  - 无 embedding 时回退时间倒序
  - 员工记忆隔离

SQLite 兼容处理：
  - employee_memory 表的 Vector(1536) 列用 TEXT 替代（create_all 时排除，手动建表）
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Column, Text, DateTime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


# ── SQLite-compatible EmployeeMemory 替代表 ───────────────────────────────────

import datetime as _dt
from sqlalchemy import Integer

class _TestBase(DeclarativeBase):
    pass


class _EmployeeMemorySQLite(_TestBase):
    """employee_memory 的 SQLite 兼容版本（Vector 列改 TEXT，BigInteger 改 Integer）。"""
    __tablename__ = "employee_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_key = Column(Text, nullable=False)
    session_id = Column(Text)
    chat_id = Column(Text)
    template = Column(Text)
    content = Column(Text, nullable=False)
    embedding = Column(Text)   # 存 JSON 字符串即可
    created_at = Column(DateTime, default=_dt.datetime.utcnow)


async def _make_memory_engine():
    """建立只含 employee_memory（SQLite 兼容）的 in-memory 引擎。"""
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(_TestBase.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# ── M-A1: memory_repo 导入正常 ───────────────────────────────────────────────

def test_memory_repo_import():
    """M-A1: backend.repos.memory_repo 可正常导入"""
    from backend.repos import memory_repo
    assert hasattr(memory_repo, "save")
    assert hasattr(memory_repo, "get_recent")
    assert hasattr(memory_repo, "search_semantic")


# ── M-A2: 配置中含 OPENAI_BASE_URL ──────────────────────────────────────────

def test_config_has_openai_base_url():
    """M-A2: backend.core.config.Settings 有 OPENAI_BASE_URL 字段"""
    from backend.core.config import Settings

    s = Settings()
    assert hasattr(s, "OPENAI_BASE_URL")


# ── M-A3: _embed 使用 OPENAI_BASE_URL ────────────────────────────────────────

@pytest.mark.asyncio
async def test_embed_uses_openai_base_url():
    """M-A3: _embed() 构建 AsyncOpenAI 时传入 OPENAI_BASE_URL（proxy URL）"""
    from backend.repos import memory_repo
    from backend.core import config as cfg_mod

    captured: list[dict] = []

    class MockAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            mock_resp = MagicMock()
            mock_resp.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]
            self.embeddings = MagicMock()
            self.embeddings.create = AsyncMock(return_value=mock_resp)

    original_settings = cfg_mod.settings
    try:
        mock_settings = MagicMock()
        mock_settings.OPENAI_API_KEY = "test-key"
        mock_settings.OPENAI_BASE_URL = "https://proxy.example.com/v1"
        cfg_mod.settings = mock_settings

        # _embed 内部 `from openai import AsyncOpenAI`，需要 patch openai 模块
        with patch("openai.AsyncOpenAI", MockAsyncOpenAI):
            result = await memory_repo._embed("测试文本")

        assert len(captured) == 1
        assert captured[0].get("base_url") == "https://proxy.example.com/v1"
        assert captured[0].get("api_key") == "test-key"
    finally:
        cfg_mod.settings = original_settings


# ── M-A4: save 不带 embedding 可正常写入 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_save_without_embedding():
    """M-A4: 无 OPENAI_API_KEY 时 save() 写入记忆（embedding=None），不抛异常"""
    from backend.repos import memory_repo
    from sqlalchemy import select

    engine, SessionFactory = await _make_memory_engine()

    # 替换 EmployeeMemory 类为 SQLite 兼容版
    with patch.object(memory_repo, "_embed", AsyncMock(return_value=None)), \
         patch.object(memory_repo, "AsyncSessionLocal", SessionFactory), \
         patch("backend.repos.memory_repo.EmployeeMemory", _EmployeeMemorySQLite):
        await memory_repo.save(
            employee_key="algorithm",
            content="本次讨论了激光雷达选型",
            session_id="sess-test",
        )

        async with SessionFactory() as s:
            rows = (await s.execute(
                select(_EmployeeMemorySQLite).where(
                    _EmployeeMemorySQLite.employee_key == "algorithm"
                )
            )).scalars().all()

    assert len(rows) == 1
    assert rows[0].content == "本次讨论了激光雷达选型"

    await engine.dispose()


# ── M-A5: get_recent 时间倒序（无 embedding）─────────────────────────────────

@pytest.mark.asyncio
async def test_memory_get_recent_time_order():
    """M-A5: get_recent() 返回按时间倒序的记忆，最新优先"""
    from backend.repos import memory_repo

    engine, SessionFactory = await _make_memory_engine()

    with patch.object(memory_repo, "_embed", AsyncMock(return_value=None)), \
         patch.object(memory_repo, "AsyncSessionLocal", SessionFactory), \
         patch("backend.repos.memory_repo.EmployeeMemory", _EmployeeMemorySQLite):
        for i in range(1, 4):
            await memory_repo.save(
                employee_key="mechanical",
                content=f"记忆{i}",
                session_id=f"sess-{i}",
            )
        results = await memory_repo.get_recent("mechanical")

    contents = [r.content for r in results]
    # 时间倒序：最新的在前
    assert contents[0] == "记忆3"
    assert contents[-1] == "记忆1"

    await engine.dispose()


# ── M-A6: 员工记忆隔离 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_isolation_per_employee():
    """M-A6: algorithm 的记忆不出现在 firmware 的 get_recent 中"""
    from backend.repos import memory_repo

    engine, SessionFactory = await _make_memory_engine()

    with patch.object(memory_repo, "_embed", AsyncMock(return_value=None)), \
         patch.object(memory_repo, "AsyncSessionLocal", SessionFactory), \
         patch("backend.repos.memory_repo.EmployeeMemory", _EmployeeMemorySQLite):
        await memory_repo.save("algorithm", "算法讨论内容", "sess-a")
        await memory_repo.save("firmware", "固件调试记录", "sess-f")

        algo_list = await memory_repo.get_recent("algorithm")
        firm_list = await memory_repo.get_recent("firmware")

    assert all(m.employee_key == "algorithm" for m in algo_list)
    assert all(m.employee_key == "firmware" for m in firm_list)
    assert not any(m.content == "固件调试记录" for m in algo_list)
    assert not any(m.content == "算法讨论内容" for m in firm_list)

    await engine.dispose()


# ── M-A7: search_semantic 无 embedding 回退 get_sync ─────────────────────────

@pytest.mark.asyncio
async def test_search_semantic_fallback_to_time_order():
    """M-A7: _embed 返回 None 时，search_semantic() 回退到时间倒序缓存"""
    from backend.repos import memory_repo

    # 预置缓存
    memory_repo._cache["test_emp_fallback"] = ["记忆A", "记忆B", "记忆C"]
    memory_repo._cache_warmed = True

    with patch.object(memory_repo, "_embed", AsyncMock(return_value=None)):
        results = await memory_repo.search_semantic("test_emp_fallback", "任意查询", limit=3)

    assert results == ["记忆A", "记忆B", "记忆C"]

    # 清理
    del memory_repo._cache["test_emp_fallback"]


# ── M-A8: get_recent 空表返回空列表 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_get_recent_empty():
    """M-A8: 空表 get_recent() 返回空列表"""
    from backend.repos import memory_repo

    engine, SessionFactory = await _make_memory_engine()

    with patch.object(memory_repo, "AsyncSessionLocal", SessionFactory), \
         patch("backend.repos.memory_repo.EmployeeMemory", _EmployeeMemorySQLite):
        results = await memory_repo.get_recent("no_such_employee")

    assert results == []

    await engine.dispose()
