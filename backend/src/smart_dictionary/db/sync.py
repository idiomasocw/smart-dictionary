"""Synchronous psycopg connections for batch work: imports, publishing, the CLI.

Batch work runs without a statement timeout. Hosted Supabase stops statements after two
minutes, which a bulk delete or read can reach on a small instance; imports also keep each
statement short (importers.db). The API's async pool keeps the server's timeout.
"""

import psycopg

from smart_dictionary.core.settings import get_settings


def connect() -> psycopg.Connection:
    """A connection that commits on a clean exit from its `with` block and rolls back on error.

    The timeout is lifted for the connection's transaction (SET LOCAL), which is the whole
    `with` block unless the code commits partway; it can't leak into a pooled session."""
    url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    conn = psycopg.connect(url)
    conn.execute("set local statement_timeout = 0")
    return conn
