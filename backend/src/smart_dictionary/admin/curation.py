"""The owner's hands on single entries (design §5.5, §3.6): add a word, retire one, requeue a
quarantined one. Each is audited by the route that calls it.

- **Add:** a word the owner wants, queued approved and first in the factory's queue. A pending
  request for it is approved with it.
- **Retire:** a duplicate or an unwanted word leaves the dictionary; its key is kept forever,
  and searches that pointed to it answer "not found". Not while the routine is writing it.
- **Requeue:** a quarantined entry, after the cause was fixed (a check, the style guide),
  gets its attempts back and returns to the queue: as a Beta rewrite when it has one.
"""

from typing import Any

import psycopg

from smart_dictionary.content import word_requests
from smart_dictionary.content.word_requests import Conflict
from smart_dictionary.core.entrydoc import KIND_POS
from smart_dictionary.core.keys import entry_key
from smart_dictionary.core.text import norm


def add(
    conn: psycopg.Connection,
    *,
    lemma: str,
    pos: str,
    kind: str,
    decided_by: str,
    note: str | None,
    tenant: str,
) -> word_requests.Approved:
    """ValueError: the parts can't make an entry; Conflict: it exists, or a twin does."""
    pos = KIND_POS.get(kind, pos)
    key = entry_key("en", lemma, pos, kind)
    existing = conn.execute("select status::text from entries where key = %s", (key,)).fetchone()
    if existing is not None:
        raise Conflict(f"{key} exists already ({existing[0]})")
    request = conn.execute(
        "select id from word_requests where lemma_norm = %s and status = 'pending'",
        (norm(lemma),),
    ).fetchone()
    if request is not None:
        return word_requests.approve(conn, request[0], lemma=lemma, pos=pos, kind=kind,
                                     decided_by=decided_by, note=note, tenant=tenant)  # fmt: skip
    word_requests.queue_approved(conn, key, lemma, pos, kind, approved_by=decided_by,
                                 sources=["owner:console"])  # fmt: skip
    return word_requests.Approved(key, write_now=False)


def retire(conn: psycopg.Connection, key: str) -> str:
    """Returns the status it had. LookupError: no such entry; Conflict: retired already, or
    the routine is writing it."""
    row = _locked(conn, key)
    if row["status"] == "retired":
        raise Conflict("it was retired already")
    if row["status"] == "generating":
        raise Conflict("the routine is writing it now; retire it after the run")
    conn.execute("update entries set status = 'retired', updated_at = now() where key = %s", (key,))
    conn.execute(
        "update misses set status = 'invalid', entry_key = null, headword = null,"
        " running_since = null where entry_key = %s",
        (key,),
    )
    conn.execute(
        "update word_requests set status = 'rejected', decided_at = now(),"
        " note = coalesce(note, 'retired in the console') where entry_key = %s"
        " and status = 'pending'",
        (key,),
    )
    return row["status"]


def requeue(conn: psycopg.Connection, key: str) -> str:
    """Returns the status it goes back to. LookupError: no such entry; Conflict: it isn't
    quarantined."""
    row = _locked(conn, key)
    if row["status"] != "quarantined":
        raise Conflict(f"it's {row['status']}; only a quarantined entry can be requeued")
    if not row["published"]:
        status = "queued"
    elif row["provisional"]:
        status = "provisional"  # its Beta stays shown, and the rewrite is leased first
    else:
        status = "needs_repair"
    conn.execute(
        "update entries set status = %s::entry_status, attempts = 0, updated_at = now()"
        " where key = %s",
        (status, key),
    )
    return status


def _locked(conn: psycopg.Connection, key: str) -> dict[str, Any]:
    row = conn.execute(
        "select status::text, published_at is not null and doc is not null,"
        " coalesce((doc->>'provisional')::boolean, false)"
        " from entries where key = %s for update",
        (key,),
    ).fetchone()
    if row is None:
        raise LookupError(key)
    return {"status": row[0], "published": row[1], "provisional": row[2]}
