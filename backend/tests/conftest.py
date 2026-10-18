"""Test defaults: the local Supabase stack, unless DATABASE_URL is already set."""

import os
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:34322/postgres"
)


@pytest.fixture
def live_database() -> None:
    """Skip the test unless the configured database answers."""
    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError:
        pytest.skip("no database reachable")
    finally:
        engine.dispose()


@pytest.fixture
def anyio_backend_options() -> dict:
    """TestClient options so async DB code runs on a loop psycopg supports (see core.runtime)."""
    if sys.platform == "win32":
        from smart_dictionary.core.runtime import selector_event_loop

        return {"loop_factory": selector_event_loop}
    return {}
