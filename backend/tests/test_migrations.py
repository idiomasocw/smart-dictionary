"""Apply every migration to a throwaway database, check the schema, then roll back."""

from alembic import command
from sqlalchemy import create_engine, text

from smart_dictionary.cli import alembic_config

EXPECTED_TABLES = {
    "tenants", "languages", "varieties", "entries", "entry_revisions", "entry_forms",
    "expression_patterns", "senses", "equivalents", "l1_forms", "sense_links", "audio_assets",
    "users", "lookups", "saved_items", "review_cards", "review_logs", "pushes", "content_flags",
    "reports", "misses", "seed_candidates", "factory_batches", "api_clients", "ai_cache",
    "llm_calls", "ref_datasets", "ref_lists", "ref_freq", "ref_forms", "ref_ipa", "ref_wiktionary",
}  # fmt: skip


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
