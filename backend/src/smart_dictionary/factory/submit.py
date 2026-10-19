"""Submitting written entries (design §6.4) [CG-3]: check, score, then publish, repair or
quarantine.

The server checks every submitted document again, whatever the routine's own run of the
checks said. An entry publishes, with its projections, when it has no errors, scores at
least the threshold and the in-run reviewer didn't ask for a repair. Otherwise it goes back
to the queue as `needs_repair` with the issues attached, and after MAX_REPAIRS failed
repairs it's quarantined. Every submission is kept as a revision, whatever its outcome.
"""

import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from smart_dictionary.content.publish import publish
from smart_dictionary.core.entrydoc import EntryDoc
from smart_dictionary.core.ids import new_id
from smart_dictionary.factory.validate import Context, Entry, Issue, score, validate

MAX_REPAIRS = 2

Outcome = Literal["published", "needs_repair", "quarantined", "not_leased"]
Verdict = Literal["pass", "fail", "uncertain"]


@dataclass(frozen=True)
class Submission:
    key: str
    doc: dict[str, Any]
    verdict: Verdict | None = None
    review_notes: list[str] = field(default_factory=list)
    needs_repair: bool = False


@dataclass(frozen=True)
class Checked:
    key: str
    score: float | None
    errors: list[Issue]
    warnings: list[Issue]


@dataclass(frozen=True)
class Result(Checked):
    outcome: Outcome = "not_leased"


class BatchClosed(Exception):
    """The batch was already submitted, or its lease expired."""


def check(conn: psycopg.Connection, items: list[tuple[str, dict[str, Any]]]) -> list[Checked]:
    """A dry run: the issues and score of each document, changing nothing."""
    stored = _entries(conn, [key for key, _ in items])
    context = Context.load(conn, stored.values(), [doc for _, doc in items])
    results: list[Checked] = []
    for key, doc in items:
        entry = stored.get(key)
        if entry is None:
            unknown = Issue("V2", "error", key, "no entry has this key")
            results.append(Checked(key, None, [unknown], []))
            continue
        results.append(_checked(key, validate(entry, doc, context), None))
    return results


def submit(
    conn: psycopg.Connection,
    batch_id: uuid.UUID,
    items: list[Submission],
    *,
    produced_by: dict[str, Any],
    threshold: float,
) -> tuple[str, list[Result]]:
    """Process the documents for a leased batch; returns the batch's status and each result."""
    batch = conn.execute(
        "select status, run_label from factory_batches where id = %s for update", (batch_id,)
    ).fetchone()
    if batch is None:
        raise LookupError(batch_id)
    if batch[0] != "leased":
        raise BatchClosed(batch[0])

    leased = _leased(conn, batch_id, [item.key for item in items])
    entries = [entry for entry, _ in leased.values()]
    context = Context.load(conn, entries, [item.doc for item in items])
    provenance = {**produced_by, "source": "routine", "batch_id": str(batch_id),
                  "run_label": batch[1]}  # fmt: skip
    results: list[Result] = []
    for item in items:
        row = leased.get(item.key)
        if row is None:
            unleased = Issue("V2", "error", item.key, "not leased to this batch")
            results.append(Result(item.key, None, [unleased], [], "not_leased"))
            continue
        entry, attempts = row
        issues = validate(entry, item.doc, context)
        checked = _checked(item.key, issues, item.verdict)
        qa = {
            "errors": [asdict(issue) for issue in checked.errors],
            "warnings": [asdict(issue) for issue in checked.warnings],
            "score": checked.score,
            "threshold": threshold,
            "review": {"verdict": item.verdict, "notes": item.review_notes},
        }
        assert checked.score is not None
        if not checked.errors and checked.score >= threshold and not item.needs_repair:
            doc = EntryDoc.model_validate(item.doc)
            publish(conn, doc, produced_by=provenance, qa=qa, quality=checked.score)
            outcome: Outcome = "published"
        else:
            outcome = "needs_repair" if attempts < MAX_REPAIRS else "quarantined"
            _send_back(conn, item, outcome, provenance, qa, checked.score)
        results.append(
            Result(checked.key, checked.score, checked.errors, checked.warnings, outcome)
        )
    return _close(conn, batch_id, results), results


def _checked(key: str, issues: list[Issue], verdict: str | None) -> Checked:
    return Checked(
        key=key,
        score=score(issues, verdict),
        errors=[issue for issue in issues if issue.severity == "error"],
        warnings=[issue for issue in issues if issue.severity == "warn"],
    )


def _entries(conn: psycopg.Connection, keys: list[str]) -> dict[str, Entry]:
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "select key, lemma, pos, kind::text as kind, doc from entries where key = any(%s)",
            (keys,),
        ).fetchall()
    return {row["key"]: Entry(**row) for row in rows}


def _leased(
    conn: psycopg.Connection, batch_id: uuid.UUID, keys: list[str]
) -> dict[str, tuple[Entry, int]]:
    """The submitted keys this batch still holds, locked, with their failed attempts."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "select key, lemma, pos, kind::text as kind, doc, attempts from entries"
            " where key = any(%s) and lease_batch_id = %s and status = 'generating'"
            " for update",
            (keys, batch_id),
        ).fetchall()
    return {row["key"]: (Entry(**{k: v for k, v in row.items() if k != "attempts"}),
                         row["attempts"]) for row in rows}  # fmt: skip


def _send_back(
    conn: psycopg.Connection,
    item: Submission,
    outcome: Outcome,
    provenance: dict[str, Any],
    qa: dict[str, Any],
    quality: float,
) -> None:
    """Record the failed submission and release the entry for repair, or quarantine it. Its
    stored document, if any, stays as it was."""
    row = conn.execute(
        "update entries set status = %s, attempts = attempts + 1, quality = %s,"
        " lease_batch_id = null, lease_expires_at = null, leased_from = null, updated_at = now()"
        " where key = %s returning id",
        (outcome, quality, item.key),
    ).fetchone()
    assert row is not None
    conn.execute(
        "insert into entry_revisions (id, entry_id, doc, produced_by, qa, outcome)"
        " values (%s, %s, %s, %s, %s, %s)",
        (new_id(), row[0], Jsonb(item.doc), Jsonb(provenance), Jsonb(qa), outcome),
    )


def _close(conn: psycopg.Connection, batch_id: uuid.UUID, results: list[Result]) -> str:
    """Add this submission's counts to the batch; it's `submitted` once it holds no entry."""
    found = conn.execute(
        "select results, (select count(*) from entries where lease_batch_id = %(id)s)"
        " from factory_batches where id = %(id)s",
        {"id": batch_id},
    ).fetchone()
    assert found is not None
    counts = Counter(found[0] or {})
    counts.update(result.outcome for result in results if result.outcome != "not_leased")
    status = "submitted" if found[1] == 0 else "leased"
    conn.execute(
        "update factory_batches set results = %s, status = %s,"
        " submitted_at = case when %s = 'submitted' then now() end where id = %s",
        (Jsonb(dict(counts)), status, status, batch_id),
    )
    return status
