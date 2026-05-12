from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
