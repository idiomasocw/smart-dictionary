"""How far the factory has got: `smart-dictionary factory status`.

Reads only. Entries are counted by status; the recent batches show each run's outcome.
"""

from dataclasses import dataclass
from datetime import datetime

import psycopg

from smart_dictionary.ai.provisional import MAX_ATTEMPTS
from smart_dictionary.factory.lease import LEASE

# The order a reader wants: done first, then what's waiting, then what needs a person.
STATUSES = ["published", "provisional", "generating", "queued", "needs_repair", "quarantined"]
MEANINGS = {
    "published": "written and live",
    "provisional": "AI Beta entries from student searches; runs rewrite these first",
    "generating": "in a run right now",
    "queued": "waiting for a run",
    "needs_repair": "back in the queue to be fixed",
    "quarantined": "failed repeatedly; needs you",
}


def report(conn: psycopg.Connection, runs: int = 10) -> list[str]:
    counts = dict(
        conn.execute(
            "select status::text, count(*) from entries where status::text = any(%s) group by 1",
            (STATUSES,),
        ).fetchall()
    )
    day = conn.execute(
        "select count(*) from entries where status = 'published'"
        " and published_at > now() - interval '24 hours'"
    ).fetchone()
    published_today = day[0] if day else 0
    lines = ["Entries:"]
    lines += [
        f"  {counts.get(status, 0):>7,}  {status:<13} {MEANINGS[status]}" for status in STATUSES
    ]
    waiting = sum(counts.get(status, 0) for status in ("queued", "needs_repair", "provisional"))
    lines.append(f"Published in the last 24 hours: {published_today:,}")
    if published_today:
        lines.append(
            f"At that pace, the {waiting:,} waiting take about {waiting / published_today:.0f} days"
        )

    batches = recent_runs(conn, runs)
    if batches:
        lines += ["", "Recent runs (UTC):"]
    for run in batches:
        took = f" in {run.minutes} min" if run.minutes is not None else ""
        outcomes = ", ".join(f"{name} {count}" for name, count in sorted(run.results.items()))
        lines.append(
            f"  {run.leased_at:%Y-%m-%d %H:%M}  {run.state:<9} {run.words:>3} words{took}"
            + (f"  ({outcomes})" if outcomes else "")
            + (f"  {run.label}" if run.label else "")
        )

    # Missed searches whose AI attempts all failed, and that the factory couldn't take: no
    # seed candidate names the word. Students see "unavailable" until someone queues it.
    stuck = conn.execute(
        "select language, query_norm, last_error from misses"
        " where status = 'pending' and attempts >= %s order by last_attempt_at desc",
        (MAX_ATTEMPTS,),
    ).fetchall()
    if stuck:
        lines += ["", f"Searches AI couldn't write and no seed covers ({len(stuck)}):"]
        lines += [f"  {query} ({language}): {error}" for language, query, error in stuck[:20]]
    return lines


@dataclass(frozen=True)
class Run:
    label: str | None
    leased_at: datetime
    submitted_at: datetime | None
    state: str  # leased, submitted, or expired: leased past its lease
    words: int
    minutes: int | None  # from lease to the last submission
    results: dict[str, int]


def recent_runs(conn: psycopg.Connection, limit: int) -> list[Run]:
    """The latest batches, newest first, for this report and the admin API."""
    rows = conn.execute(
        "select run_label, leased_at, submitted_at, status, cardinality(entry_ids), results,"
        " status = 'leased' and leased_at + %s < now()"
        " from factory_batches order by leased_at desc limit %s",
        (LEASE, limit),
    ).fetchall()
    return [
        Run(
            label=label,
            leased_at=leased_at,
            submitted_at=submitted_at,
            state="expired" if overdue else status,
            words=words,
            minutes=_minutes(leased_at, submitted_at) if submitted_at else None,
            results=results or {},
        )
        for label, leased_at, submitted_at, status, words, results, overdue in rows
    ]


def _minutes(start: datetime, end: datetime) -> int:
    return round((end - start).total_seconds() / 60)
