"""Bulk loading into the ref_* tables with COPY, one transaction per dataset.

A hosted database limits how long one statement may run: Supabase stops statements after
about two minutes, and its pooler drops connections that stay busy that long. So rows are
parsed and spooled to a temporary file before the load transaction opens (`spooled`), and
COPY sends them in chunks of COPY_CHUNK rows, one short statement each.
"""

import itertools
import pickle
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

import psycopg
from psycopg import sql

from smart_dictionary.db.sync import connect
from smart_dictionary.importers.manifest import Dataset

__all__ = ["connect", "copy_rows", "finish_dataset", "replace_dataset", "spooled"]

# Rows per COPY statement: a few seconds each, even over a slow uplink.
COPY_CHUNK = 20_000


def replace_dataset(
    conn: psycopg.Connection, dataset: Dataset, *, version: str, sha256: str | None
) -> None:
    """Drop the dataset's previous rows (cascade) and register the new import."""
    conn.execute("delete from ref_datasets where id = %s", (dataset.id,))
    conn.execute(
        "insert into ref_datasets (id, name, version, license, attribution, source_url, sha256)"
        " values (%s, %s, %s, %s, %s, %s, %s)",
        (
            dataset.id,
            dataset.name,
            version,
            dataset.license,
            dataset.attribution,
            dataset.url,
            sha256,
        ),
    )


def copy_rows(
    conn: psycopg.Connection,
    table: str,
    columns: list[str],
    rows: Iterable[tuple],
    *,
    chunk: int = COPY_CHUNK,
) -> int:
    """COPY the rows into the table, `chunk` rows per statement; returns the row count."""
    statement = sql.SQL("copy {} ({}) from stdin").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    count = 0
    pending = iter(rows)
    while batch := list(itertools.islice(pending, chunk)):
        with conn.cursor() as cursor, cursor.copy(statement) as copy:
            for row in batch:
                copy.write_row(row)
        count += len(batch)
    return count


@contextmanager
def spooled(rows: Iterable[tuple]) -> Iterator[Callable[[], Iterator[tuple]]]:
    """Run the rows to the end into a temporary file; yields a function that replays them.

    Parsing can take minutes (the Wiktionary extract does). Spooled first, it's done before
    the load transaction opens, so the database only waits for the transfer, and a load
    that has to start over replays the file instead of parsing again."""
    with tempfile.TemporaryFile() as spool:
        for row in rows:
            pickle.dump(row, spool, protocol=pickle.HIGHEST_PROTOCOL)

        def replay() -> Iterator[tuple]:
            spool.seek(0)
            while True:
                try:
                    yield pickle.load(spool)
                except EOFError:
                    return

        yield replay


def finish_dataset(conn: psycopg.Connection, dataset_id: str, row_count: int) -> None:
    conn.execute("update ref_datasets set row_count = %s where id = %s", (row_count, dataset_id))
