"""
Shared test fixtures.
Each test function gets a fresh in-memory SQLite database, so tests
are fully isolated and don't touch the production PostgreSQL instance.

SQLite 兼容处理：
- JSONB 列替换为 JSON（通过 compile_column_property event listener）
- pgvector Vector 列所在的 employee_memory 表排除在外
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.types import JSON

from backend.api.deps import get_db
from backend.core.db import Base
from backend.main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# Tables that use Postgres-only types that cannot be shimmed
# (pgvector Vector / postgresql.ARRAY)。需要这些表的测试都直接连 dev pg。
_SKIP_TABLES = {
    "employee_memory",      # Vector
    "task_context",         # Vector
    "kb_documents",         # Vector + ARRAY
    "kb_retrieval_log",     # ARRAY
    "lessons",              # Vector
    "pattern_extracts",     # ARRAY
    "routing_decisions",    # ARRAY
}


def _patch_sqlite_jsonb():
    """Register JSONB → JSON fallback for SQLite DDL compilation."""
    try:
        from sqlalchemy.dialects.postgresql import JSONB

        # 只添加一次
        if not getattr(SQLiteTypeCompiler, "_jsonb_patched", False):
            def visit_JSONB(self, type_, **kw):
                return self.visit_JSON(JSON(), **kw)

            SQLiteTypeCompiler.visit_JSONB = visit_JSONB
            SQLiteTypeCompiler._jsonb_patched = True
    except ImportError:
        pass


_patch_sqlite_jsonb()


def _sqlite_compatible_tables():
    """Return only the SQLite-compatible table objects from Base.metadata."""
    return [t for name, t in Base.metadata.tables.items() if name not in _SKIP_TABLES]


@pytest.fixture
async def client():
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    tables = _sqlite_compatible_tables()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))

    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
    await engine.dispose()
