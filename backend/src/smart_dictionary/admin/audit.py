"""The admin audit (design §5.5): every change the console makes, in admin_audit.

It records the token's name and `X-Admin-Actor`, the LMS person ID of whoever was signed in,
with the value before and after. The API's role can add rows but not change or delete them.
"""

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from smart_dictionary.factory.tokens import Client


def record(
    conn: psycopg.Connection,
    client: Client,
    actor: str | None,
    action: str,
    target: str,
    before: Any,
    after: Any,
) -> None:
    conn.execute(
        "insert into admin_audit (client_id, client, actor, action, target, before, after)"
        " values (%s, %s, %s, %s, %s, %s, %s)",
        (client.id, client.name, actor, action, target,
         None if before is None else Jsonb(before),
         None if after is None else Jsonb(after)),
    )  # fmt: skip


def decided_by(client: Client, actor: str | None) -> str:
    """Who made a decision, as the content tables record it: the token, and the person."""
    return f"{client.name}:{actor}" if actor else client.name
