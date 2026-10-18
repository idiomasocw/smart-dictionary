"""One async SQLAlchemy engine per process, created on first use."""

from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from smart_dictionary.core.settings import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


async def ping() -> None:
    """Raise if the database does not answer."""
    async with get_engine().connect() as connection:
        await connection.execute(text("select 1"))
