"""Paged lists for the console: entries by letter, status or prefix; one entry with its
revisions; missed searches; and the audit of setting changes.

Pages are numbered from 1. Every filter is a bound parameter: the only SQL that varies is the
sort, picked from fixed strings.
"""

import string
from typing import Any, LiteralString

import psycopg
from psycopg.rows import dict_row

from smart_dictionary.ai.provisional import MAX_ATTEMPTS

# A lemma that doesn't start with a-z (a digit or a symbol) is listed under OTHER.
OTHER = "other"
LETTERS = [*string.ascii_lowercase, OTHER]
# The statuses whose latest revision says what went wrong.
SENT_BACK = ("needs_repair", "quarantined")

ENTRY_FILTER: LiteralString = """
from entries e
where (cast(%(status)s as text) is null or e.status::text = %(status)s)
  and (cast(%(letter)s as text) is null
       or (%(letter)s = 'other' and e.lemma_norm !~ '^[a-z]')
       or (%(letter)s <> 'other' and starts_with(e.lemma_norm, %(letter)s)))
  and (cast(%(q)s as text) is null or starts_with(e.lemma_norm, %(q)s))
"""
ENTRY_COLUMNS: LiteralString = """
select e.id, e.key, e.lemma, e.pos, e.kind::text as kind, e.homograph, e.cefr,
       e.status::text as status, e.approved_at is not null as approved,
       e.quality::float as quality, e.attempts,
       e.doc #>> '{senses,0,definition}' as definition,
       case when jsonb_typeof(e.doc -> 'senses') = 'array'
            then jsonb_array_length(e.doc -> 'senses') else 0 end as senses,
       e.updated_at, e.published_at,
       (select r.qa from entry_revisions r
        where r.entry_id = e.id and e.status::text in ('needs_repair', 'quarantined')
        order by r.created_at desc limit 1) as qa
"""
ENTRY_ORDER: dict[str, LiteralString] = {
    "lemma": " order by e.lemma_norm, e.lemma, e.pos, e.kind, e.homograph",
    "updated": " order by e.updated_at desc, e.key",
}

MISS_FILTER: LiteralString = """
from misses m
where (cast(%(status)s as text) is null or m.status = %(status)s)
  and (not %(stuck)s or (m.status = 'pending' and m.attempts >= %(max_attempts)s))
  and (cast(%(q)s as text) is null or starts_with(m.query_norm, %(q)s))
"""


def entries(
    conn: psycopg.Connection,
    *,
    letter: str | None,
    status: str | None,
    q: str | None,
    sort: str,
    page: int,
    per_page: int,
) -> tuple[list[dict[str, Any]], int]:
    """One page of entries and how many match in all."""
    params = {"status": status, "letter": letter, "q": q}
    total = _count(conn, "select count(*)" + ENTRY_FILTER, params)
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            ENTRY_COLUMNS + ENTRY_FILTER + ENTRY_ORDER[sort] + " limit %(limit)s offset %(offset)s",
            params | {"limit": per_page, "offset": (page - 1) * per_page},
        ).fetchall()
    return [_with_problems(row) for row in rows], total


def letters(conn: psycopg.Connection, status: str | None) -> list[dict[str, Any]]:
    """How many entries start with each letter, OTHER last, zeros included."""
    counts = dict(
        conn.execute(
            "select case when lemma_norm ~ '^[a-z]' then left(lemma_norm, 1) else 'other' end,"
            " count(*) from entries"
            " where cast(%(status)s as text) is null or status::text = %(status)s group by 1",
            {"status": status},
        ).fetchall()
    )
    return [{"letter": letter, "count": counts.get(letter, 0)} for letter in LETTERS]


def entry(conn: psycopg.Connection, key: str) -> dict[str, Any] | None:
    """One entry with its document and its latest revisions, newest first."""
    with conn.cursor(row_factory=dict_row) as cursor:
        found = cursor.execute(
            ENTRY_COLUMNS + ", e.priority, e.freq_zipf::float as freq_zipf, e.sources,"
            " e.created_at, e.lease_expires_at, e.doc from entries e where e.key = %s",
            (key,),
        ).fetchone()
        if found is None:
            return None
        revisions = cursor.execute(
            "select created_at, outcome, produced_by, qa from entry_revisions"
            " where entry_id = %s order by created_at desc limit 20",
            (found["id"],),
        ).fetchall()
    found = _with_problems(found)
    found["revisions"] = [
        revision
        | {
            "score": (revision["qa"] or {}).get("score"),
            "problems": problems(revision["qa"]),
            "warnings": [_issue(issue) for issue in (revision["qa"] or {}).get("warnings", [])],
        }
        for revision in revisions
    ]
    return found


def misses(
    conn: psycopg.Connection,
    *,
    status: str | None,
    stuck: bool,
    q: str | None,
    page: int,
    per_page: int,
) -> tuple[list[dict[str, Any]], int]:
    """One page of missed searches, the latest first. No student is named: a miss is the
    tenant's, whoever searched."""
    params = {"status": status, "stuck": stuck, "q": q, "max_attempts": MAX_ATTEMPTS}
    total = _count(conn, "select count(*)" + MISS_FILTER, params)
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "select m.tenant_id as tenant, m.language, m.query_norm as query, m.hits, m.status,"
            " m.entry_key, m.attempts, m.status = 'pending' and m.attempts >= %(max_attempts)s"
            " as stuck, m.last_error, m.first_seen, m.last_seen, m.last_attempt_at"
            + MISS_FILTER
            + " order by m.last_seen desc, m.query_norm limit %(limit)s offset %(offset)s",
            params | {"limit": per_page, "offset": (page - 1) * per_page},
        ).fetchall()
    return rows, total


def audit(conn: psycopg.Connection, *, page: int, per_page: int) -> tuple[list[dict], int]:
    total = _count(conn, "select count(*) from admin_audit", {})
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "select created_at, client, actor, action, target, before, after from admin_audit"
            " order by id desc limit %s offset %s",
            (per_page, (page - 1) * per_page),
        ).fetchall()
    return rows, total


def problems(qa: dict[str, Any] | None) -> list[str]:
    """Why a submission didn't publish, one line each: the checks' errors, a low score, or
    the reviewer's notes."""
    if not qa:
        return []
    found = [_issue(issue) for issue in qa.get("errors", [])]
    score, threshold = qa.get("score"), qa.get("threshold")
    if not found and score is not None and threshold is not None and score < threshold:
        found.append(f"scored {score:.2f}, below the {threshold:.2f} needed")
    review = qa.get("review") or {}
    if review.get("verdict") == "fail":
        found += [f"reviewer: {note}" for note in review.get("notes", [])] or ["reviewer: fail"]
    return found


def _issue(issue: dict[str, Any]) -> str:
    return f"{issue.get('check')} {issue.get('where')}: {issue.get('message')}"


def _with_problems(row: dict[str, Any]) -> dict[str, Any]:
    qa = row.pop("qa", None)
    row.pop("id", None)
    return row | {"problems": problems(qa)}


def _count(conn: psycopg.Connection, query: LiteralString, params: dict[str, Any]) -> int:
    row = conn.execute(query, params).fetchone()
    return row[0] if row else 0
