from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from pydantic_settings import BaseSettings


class DBSettings(BaseSettings):
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "admin"

    model_config = {
        "env_file": str(Path(__file__).parent.parent.parent / "infra" / ".env"),
        "extra": "ignore",
    }


_db = DBSettings()

APP_DSN = (
    f"postgresql+asyncpg://{_db.POSTGRES_USER}:{_db.POSTGRES_PASSWORD}"
    f"@{_db.POSTGRES_HOST}:{_db.POSTGRES_PORT}/company_app"
)

LANGGRAPH_DSN = (
    f"postgresql://{_db.POSTGRES_USER}:{_db.POSTGRES_PASSWORD}"
    f"@{_db.POSTGRES_HOST}:{_db.POSTGRES_PORT}/company_langgraph"
)

# AsyncPostgresSaver uses psycopg3 directly — plain postgresql:// URL
LANGGRAPH_ASYNC_DSN = LANGGRAPH_DSN


@contextmanager
def checkpointer_ctx():
    """Sync PostgresSaver — use only for graph compilation, not ainvoke."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(LANGGRAPH_DSN) as cp:
        cp.setup()
        yield cp


@asynccontextmanager
async def async_checkpointer_ctx():
    """Async PostgresSaver — required for ainvoke / astream."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(LANGGRAPH_ASYNC_DSN) as cp:
        await cp.setup()
        yield cp
