"""The settings the console can change (core.tunables), each change audited.

Only the names in TUNABLES exist here: keys, passwords and secrets can't be read or written
through the admin API at all. A change is checked against its bounds, stored, and recorded in
admin_audit in the same transaction (admin.audit).
"""

from contextlib import suppress
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from smart_dictionary.admin import audit
from smart_dictionary.core.settings import Settings
from smart_dictionary.core.tunables import TUNABLES, Tunable
from smart_dictionary.factory.tokens import Client


def listing(conn: psycopg.Connection, settings: Settings) -> list[dict[str, Any]]:
    """Every setting with its current value, its environment default and its override."""
    stored = {
        name: (value, updated_at, updated_by)
        for name, value, updated_at, updated_by in conn.execute(
            "select name, value, updated_at, updated_by from app_settings"
        )
    }
    return [_describe(tunable, settings, stored.get(tunable.name)) for tunable in TUNABLES.values()]


def change(
    conn: psycopg.Connection, client: Client, actor: str | None, tunable: Tunable, value: Any
) -> None:
    """Store an override; ValueError says why the value isn't allowed."""
    parsed = tunable.parse(value)
    before = conn.execute(
        "select value from app_settings where name = %s for update", (tunable.name,)
    ).fetchone()
    conn.execute(
        "insert into app_settings (name, value, updated_by) values (%s, %s, %s)"
        " on conflict (name) do update set value = excluded.value, updated_at = now(),"
        " updated_by = excluded.updated_by",
        (tunable.name, Jsonb(parsed), client.name),
    )
    previous = before[0] if before else None
    audit.record(conn, client, actor, "setting.set", tunable.name, previous, parsed)


def reset(conn: psycopg.Connection, client: Client, actor: str | None, tunable: Tunable) -> None:
    """Remove the override, so the environment's value applies again."""
    removed = conn.execute(
        "delete from app_settings where name = %s returning value", (tunable.name,)
    ).fetchone()
    if removed is not None:
        audit.record(conn, client, actor, "setting.reset", tunable.name, removed[0], None)


def describe(conn: psycopg.Connection, settings: Settings, tunable: Tunable) -> dict[str, Any]:
    row = conn.execute(
        "select value, updated_at, updated_by from app_settings where name = %s",
        (tunable.name,),
    ).fetchone()
    return _describe(tunable, settings, row)


def _describe(tunable: Tunable, settings: Settings, row: Any) -> dict[str, Any]:
    default = tunable.default(settings)
    override, overridden = None, False
    # A stored value that no longer parses is ignored here, as where it's read (core.tunables).
    if row is not None:
        with suppress(ValueError):
            override, overridden = tunable.parse(row[0]), True
    bounded = tunable.kind in ("integer", "number")
    return {
        "name": tunable.name,
        "kind": tunable.kind,
        "description": tunable.description,
        "minimum": tunable.minimum if bounded else None,
        "maximum": tunable.maximum if bounded else None,
        "value": override if overridden else default,
        "default": default,
        "overridden": overridden,
        "updated_at": row[1] if row is not None else None,
        "updated_by": row[2] if row is not None else None,
    }
