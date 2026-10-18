"""Shared request dependencies."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from smart_dictionary.db.engine import get_engine


async def connection() -> AsyncIterator[AsyncConnection]:
    async with get_engine().connect() as conn:
        yield conn


Connection = Annotated[AsyncConnection, Depends(connection)]
