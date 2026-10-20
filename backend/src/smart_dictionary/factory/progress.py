"""How far the factory has got: `smart-dictionary factory status`.

Reads only. Entries are counted by status; the recent batches show each run's outcome.
"""

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

    batches = conn.execute(
        "select run_label, leased_at, submitted_at, status, cardinality(entry_ids), results,"
        " status = 'leased' and leased_at + %s < now()"
        " from factory_batches order by leased_at desc limit %s",
        (LEASE, runs),
    ).fetchall()
    if batches:
        lines += ["", "Recent runs (UTC):"]
    for label, leased_at, submitted_at, status, words, results, overdue in batches:
        state = "expired" if overdue else status
        took = f" in {_minutes(leased_at, submitted_at)} min" if submitted_at else ""
        outcomes = ", ".join(f"{name} {count}" for name, count in sorted((results or {}).items()))
        lines.append(
            f"  {leased_at:%Y-%m-%d %H:%M}  {state:<9} {words:>3} words{took}"
            + (f"  ({outcomes})" if outcomes else "")
            + (f"  {label}" if label else "")
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


def _minutes(start: datetime, end: datetime) -> int:
    return round((end - start).total_seconds() / 60)
