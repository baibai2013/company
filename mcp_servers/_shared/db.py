"""异步 PostgreSQL 连接 — 给 trace_tool_call 中间件写 tool_call_log 用。

设计取舍:**直接用 asyncpg 单连接 acquire/close**,不复用 backend.core.db 的全局
SQLAlchemy AsyncSessionLocal。理由:

1. SQLAlchemy 的 AsyncEngine 在第一次 connect 时绑定到当时的 event loop,后续如果
   loop 切换(测试里 asyncio.run 多次,或 MCP server 跨 loop)就会炸 "another
   operation is in progress / attached to a different loop"。
2. MCP server 子进程本身生命周期短、调用频率不算高,每次 trace 写库就 1-2 条
   INSERT,不需要常驻连接池。
3. asyncpg 直连绕开 SQLAlchemy 整层,trace 中间件本身的实现简单一截。

对外只暴露一个 async ctx mgr `get_async_conn()`,中间件用它写库。
连接参数从 backend.core.config.settings 取(沿用主进程 .env)。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from backend.core.config import settings


def _build_dsn() -> str:
    """把 settings 里的 PG 连接信息组合成 asyncpg 能直接吃的 DSN。"""
    return (
        f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/company_app"
    )


@asynccontextmanager
async def get_async_conn() -> AsyncIterator[asyncpg.Connection]:
    """开一条 asyncpg 连接,退出时自动 close。

    用法:
        async with get_async_conn() as conn:
            await conn.execute("INSERT ...")

    无显式事务包裹 — caller 想要原子性可自行 BEGIN/COMMIT 或用 conn.transaction()。
    """
    conn: asyncpg.Connection = await asyncpg.connect(_build_dsn())
    try:
        yield conn
    finally:
        await conn.close()


__all__ = ["get_async_conn"]
