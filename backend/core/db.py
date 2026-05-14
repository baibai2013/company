from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.core.config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# 注册 pgvector asyncpg codec，让 SQLAlchemy 能读写 vector 列
@event.listens_for(engine.sync_engine, "connect")
def _register_vector_codec(dbapi_conn, _conn_record):
    from pgvector.asyncpg import register_vector
    dbapi_conn.run_async(register_vector)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
