"""Synchronous psycopg connections for batch work: imports, publishing, the CLI."""

import psycopg

from smart_dictionary.core.settings import get_settings


def connect() -> psycopg.Connection:
    """A connection that commits on a clean exit from its `with` block and rolls back on error."""
    url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(url)
