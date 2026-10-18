"""Apply every migration to a throwaway database, check the schema, then roll back.

Skips itself when no database is reachable (e.g. the local stack is stopped).
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from smart_dictionary.cli import alembic_config
from smart_dictionary.core.settings import get_settings

EXPECTED_TABLES = {
    "tenants", "languages", "varieties", "entries", "entry_revisions", "entry_forms",
    "expression_patterns", "senses", "equivalents", "l1_forms", "sense_links", "audio_assets",
    "users", "lookups", "saved_items", "review_cards", "review_logs", "pushes", "content_flags",
    "reports", "misses", "seed_candidates", "factory_batches", "api_clients", "ai_cache",
    "llm_calls",
}  # fmt: skip


def _admin_engine() -> Engine:
    url = make_url(os.environ["DATABASE_URL"])
    return create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})


@pytest.fixture
def scratch_database_url(live_database, monkeypatch) -> Iterator[str]:
    admin = _admin_engine()
    name = f"sd_test_{uuid.uuid4().hex[:10]}"
    with admin.connect() as connection:
        connection.execute(text(f'create database "{name}"'))
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    rendered = url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", rendered)
    get_settings.cache_clear()
    try:
        yield rendered
    finally:
        get_settings.cache_clear()
        with admin.connect() as connection:
            connection.execute(text(f'drop database "{name}" with (force)'))
        admin.dispose()


def _public_tables(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("select tablename from pg_tables where schemaname = 'public'")
            )
            return set(rows.scalars())
    finally:
        engine.dispose()


def test_migrations_upgrade_and_downgrade(scratch_database_url):
    config = alembic_config()

    command.upgrade(config, "head")
    tables = _public_tables(scratch_database_url)
    assert tables >= EXPECTED_TABLES
    assert any(t.startswith("lookups_2") for t in tables), "monthly lookup partitions exist"

    engine = create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            tenant = connection.execute(text("select name from tenants where id = 'ocw'"))
            assert tenant.scalar_one() == "OneCulture World"
            varieties = set(connection.execute(text("select tag from varieties")).scalars())
            assert {"en-US", "en-GB", "es-419", "pt-BR"} <= varieties
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    assert not (_public_tables(scratch_database_url) & EXPECTED_TABLES)
