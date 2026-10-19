"""Shared request dependencies."""

from collections.abc import AsyncIterator, Iterator
from typing import Annotated

import psycopg
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from smart_dictionary.db import sync
from smart_dictionary.db.engine import get_engine


async def connection() -> AsyncIterator[AsyncConnection]:
    async with get_engine().connect() as conn:
        yield conn


Connection = Annotated[AsyncConnection, Depends(connection)]


def sync_connection() -> Iterator[psycopg.Connection]:
    """For the factory's plain-function routes, which share code with the CLI. It commits
    when the request succeeds and rolls back when it raises."""
    with sync.connect() as conn:
        yield conn


SyncConnection = Annotated[psycopg.Connection, Depends(sync_connection)]
