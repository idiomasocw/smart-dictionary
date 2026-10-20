"""Leasing batches of entries to the routine (design §3.3, §6.3).

`FOR UPDATE SKIP LOCKED` means two runs never get the same entry. A lease lasts three hours.
Entries whose lease ran out go back to the status they were leased from at the start of the
next lease, so this needs no background job. Provisional entries and repairs come before
new ones, then priority decides.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from smart_dictionary.core.ids import new_id

LEASE = timedelta(hours=3)
QUEUE_ORDER = (
    "case status when 'provisional' then 0 when 'needs_repair' then 1 else 2 end,"
    " priority desc, key"
)


@dataclass(frozen=True)
class Batch:
    id: uuid.UUID | None
    lease_expires_at: datetime | None
    entry_ids: list[uuid.UUID]


def expire(conn: psycopg.Connection) -> int:
    """Return entries from expired leases to their previous status; returns how many."""
    returned = conn.execute(
        "update entries set status = leased_from, leased_from = null, lease_batch_id = null,"
        " lease_expires_at = null, updated_at = now()"
        " where status = 'generating' and lease_expires_at < now()"
    ).rowcount
    conn.execute(
        "update factory_batches set status = 'expired'"
        " where status = 'leased' and leased_at + %s < now()",
        (LEASE,),
    )
    return returned


def lease(conn: psycopg.Connection, size: int, label: str | None = None) -> Batch:
    """Lease up to `size` entries in queue order. No batch is recorded when nothing is left."""
    expire(conn)
    batch_id = new_id()
    rows = conn.execute(
        "with picked as ("
        "  select id from entries where status in ('provisional', 'needs_repair', 'queued')"
        "  and approved_at is not null"
        f"  order by {QUEUE_ORDER} limit %s for update skip locked)"
        " update entries e set leased_from = e.status, status = 'generating',"
        " lease_batch_id = %s, lease_expires_at = now() + %s, updated_at = now()"
        " from picked where e.id = picked.id"
        " returning e.id, e.lease_expires_at",
        (size, batch_id, LEASE),
    ).fetchall()
    if not rows:
        return Batch(None, None, [])
    entry_ids = [row[0] for row in rows]
    conn.execute(
        "insert into factory_batches (id, requested_size, entry_ids, run_label)"
        " values (%s, %s, %s, %s)",
        (batch_id, size, entry_ids, label),
    )
    return Batch(batch_id, rows[0][1], entry_ids)


def status(conn: psycopg.Connection, batch_id: uuid.UUID) -> dict[str, Any] | None:
    """The batch and the current status of each of its entries, or None."""
    with conn.cursor(row_factory=dict_row) as cursor:
        batch = cursor.execute(
            "select id as batch_id, status, run_label, requested_size, leased_at, submitted_at,"
            " entry_ids from factory_batches where id = %s",
            (batch_id,),
        ).fetchone()
        if batch is None:
            return None
        batch["entries"] = cursor.execute(
            "select key, status::text as status from entries where id = any(%s) order by key",
            (batch.pop("entry_ids"),),
        ).fetchall()
    return batch
