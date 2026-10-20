"""Test defaults and throwaway databases.

DATABASE_URL defaults to the local Supabase stack. Database tests create their own
scratch database next to it and drop it afterwards, so they never touch dev data,
and they skip themselves when no database is reachable. Settings come from the
environment only, so a developer's backend/.env never changes what the tests see.
"""

import os
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from smart_dictionary.core.settings import Settings

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:34322/postgres"
)
Settings.model_config["env_file"] = None


def database_reachable() -> bool:
    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
        return True
    except OperationalError:
        return False
    finally:
        engine.dispose()


def clear_caches() -> None:
    from smart_dictionary.core import tunables
    from smart_dictionary.core.settings import get_settings
    from smart_dictionary.db import engine

    get_settings.cache_clear()
    engine.get_engine.cache_clear()
    tunables.forget()


@contextmanager
def scratch_database(*, migrate: bool) -> Iterator[str]:
    """Create a database, point DATABASE_URL at it, and drop it afterwards."""
    from alembic import command

    from smart_dictionary.cli import alembic_config

    base_url = os.environ["DATABASE_URL"]
    admin = create_engine(
        base_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3}
    )
    name = f"sd_test_{uuid.uuid4().hex[:10]}"
    with admin.connect() as connection:
        connection.execute(text(f'create database "{name}"'))
    url = make_url(base_url).set(database=name).render_as_string(hide_password=False)
    os.environ["DATABASE_URL"] = url
    clear_caches()
    try:
        if migrate:
            command.upgrade(alembic_config(), "head")
        yield url
    finally:
        os.environ["DATABASE_URL"] = base_url
        clear_caches()
        with admin.connect() as connection:
            connection.execute(text(f'drop database "{name}" with (force)'))
        admin.dispose()


@pytest.fixture
def live_database() -> None:
    """Skip the test unless the configured database answers."""
    if not database_reachable():
        pytest.skip("no database reachable")


@pytest.fixture
def scratch_database_url(live_database) -> Iterator[str]:
    with scratch_database(migrate=False) as url:
        yield url


@pytest.fixture
def migrated_database_url(live_database) -> Iterator[str]:
    with scratch_database(migrate=True) as url:
        yield url


@pytest.fixture(scope="session")
def anyio_backend_options() -> dict:
    """TestClient options so async DB code runs on a loop psycopg supports (see core.runtime)."""
    if sys.platform == "win32":
        from smart_dictionary.core.runtime import selector_event_loop

        return {"loop_factory": selector_event_loop}
    return {}
