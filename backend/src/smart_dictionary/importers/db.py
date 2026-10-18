"""Bulk loading into the ref_* tables with COPY, one transaction per dataset."""

from collections.abc import Iterable

import psycopg
from psycopg import sql

from smart_dictionary.db.sync import connect
from smart_dictionary.importers.manifest import Dataset

__all__ = ["connect", "copy_rows", "finish_dataset", "replace_dataset"]


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
    conn: psycopg.Connection, table: str, columns: list[str], rows: Iterable[tuple]
) -> int:
    statement = sql.SQL("copy {} ({}) from stdin").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    count = 0
    with conn.cursor() as cursor, cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    return count


def finish_dataset(conn: psycopg.Connection, dataset_id: str, row_count: int) -> None:
    conn.execute("update ref_datasets set row_count = %s where id = %s", (row_count, dataset_id))
