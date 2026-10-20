import psycopg
import pytest

from smart_dictionary.db import roles
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import tokens


def test_app_url_keeps_the_host_and_the_pooler_project():
    pooler = (
        "postgresql+psycopg://postgres.abc123:old@aws-0-us-east-2.pooler.supabase.com:5432/postgres"
    )
    assert roles.app_url(pooler, "dictionary_app", "s3cret") == (
        "postgresql+psycopg://dictionary_app.abc123:s3cret@aws-0-us-east-2.pooler.supabase.com:5432/postgres"
    )
    direct = "postgresql+psycopg://postgres:old@db.abc123.supabase.co:5432/postgres?sslmode=require"
    assert roles.app_url(direct, "dictionary_app", "s3cret") == (
        "postgresql+psycopg://dictionary_app:s3cret@db.abc123.supabase.co:5432/postgres?sslmode=require"
    )


def test_app_role_reads_and_writes_rows_but_never_the_schema(migrated_database_url):
    with connect() as conn:
        url = roles.reset_password(conn, migrated_database_url)
        # Hosted Supabase turns on row-level security for every new table (its ensure_rls
        # event trigger), with no policies; the local stack doesn't, so do it here.
        for table in ("tenants", "entries", "misses", "lookups"):
            conn.execute(f"alter table {table} enable row level security")  # pyright: ignore[reportArgumentType]
        tokens.create(conn, "lms", ["admin"])
    roles.check_login(url, "dictionary_app")
    app_url = url.replace("postgresql+psycopg://", "postgresql://", 1)

    with psycopg.connect(app_url, autocommit=True) as app:
        assert app.execute("select current_user").fetchone() == ("dictionary_app",)
        # Under RLS without policies, a role that doesn't bypass it would see no rows here.
        assert app.execute("select id from tenants").fetchall() == [("ocw",)]
        assert app.execute("select count(*) from entries").fetchone() == (0,)
        # What the lookup path writes, through an identity column and a partitioned table.
        app.execute(
            "insert into misses (tenant_id, language, query_norm) values ('ocw', 'en', 'zzz')"
        )
        app.execute("insert into lookups (tenant_id, query, result) values ('ocw', 'zzz', 'miss')")
        assert app.execute("select extensions.similarity('house', 'hose') > 0").fetchone() == (
            True,
        )
        # The admin API's tables: settings change freely, the audit only grows, and tokens
        # are read but never written, so no request can mint or widen one.
        client = app.execute("select id from api_clients where name = 'lms'").fetchone()
        assert client is not None
        app.execute("insert into app_settings (name, value, updated_by) values ('x', '1', 'lms')")
        app.execute("update app_settings set value = '2' where name = 'x'")
        app.execute("delete from app_settings where name = 'x'")
        app.execute(
            "insert into admin_audit (client_id, client, action, target)"
            " values (%s, 'lms', 'setting.set', 'x')",
            client,
        )
        for statement in (
            "create table intruder (id int)",
            "drop table entries",
            "alter table entries add column intruder int",
            "create role intruder",
            "update admin_audit set actor = 'someone else'",
            "delete from admin_audit",
            "update api_clients set scopes = array['factory', 'admin']",
            "delete from api_clients",
            "insert into api_clients (tenant_id, name, scopes, token_hash)"
            " values ('ocw', 'mine', array['admin'], 'x')",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(statement)  # pyright: ignore[reportArgumentType]
