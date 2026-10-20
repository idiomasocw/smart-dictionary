"""Bearer tokens for API clients such as the factory routine and the LMS's admin console
(design §5.1, `api_clients`).

Only a SHA-256 digest is stored, so the database never holds a usable token; the token
itself is shown once, when it's created. Tokens are long and random, so an unsalted digest
is enough. Only the CLI creates them: the API's role can read `api_clients` but not write it.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass

import psycopg

from smart_dictionary.core.ids import new_id

# A recognizable prefix makes a leaked token easy to spot in logs and secret scanners, and
# tells at a glance whether it can change settings.
PREFIX = "sdf_"
ADMIN_PREFIX = "sda_"


@dataclass(frozen=True)
class Client:
    id: uuid.UUID
    name: str
    tenant: str
    scopes: tuple[str, ...]


def digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def create(conn: psycopg.Connection, name: str, scopes: list[str], tenant: str = "ocw") -> str:
    """Register a client and return its token, which can't be recovered afterwards."""
    token = (ADMIN_PREFIX if "admin" in scopes else PREFIX) + secrets.token_urlsafe(32)
    conn.execute(
        "insert into api_clients (id, tenant_id, name, scopes, token_hash)"
        " values (%s, %s, %s, %s, %s)",
        (new_id(), tenant, name, scopes, digest(token)),
    )
    return token


def revoke(conn: psycopg.Connection, name: str, scope: str) -> int:
    """Revoke every active token with this name and scope; returns how many."""
    cursor = conn.execute(
        "update api_clients set revoked_at = now()"
        " where name = %s and %s = any(scopes) and revoked_at is null",
        (name, scope),
    )
    return cursor.rowcount


def find(conn: psycopg.Connection, token: str, scope: str) -> Client | None:
    """The active client holding this token and scope, or None."""
    row = conn.execute(
        "select id, name, tenant_id, scopes from api_clients"
        " where token_hash = %s and revoked_at is null and %s = any(scopes)",
        (digest(token), scope),
    ).fetchone()
    return None if row is None else Client(row[0], row[1], row[2], tuple(row[3]))
