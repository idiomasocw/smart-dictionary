"""The dictionary at a glance: entries by status, missed searches, AI use, audio and runs.

Counts only. AI use is counted per search and per student without saying who: the console
sees how many students reached the daily cap, never which ones.
"""

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

import psycopg

from smart_dictionary.ai.provisional import AI_TASKS, MAX_ATTEMPTS
from smart_dictionary.core.settings import AI_PROVIDERS, Settings
from smart_dictionary.factory import progress

# Every entry status, in the order a reader wants: done, at work, waiting, needing someone,
# then the ones outside the pipeline.
MEANINGS = {
    **progress.MEANINGS,
    "validating": "being checked after a submission",
    "candidate": "known headword, not queued for writing yet",
    "retired": "withdrawn; its keys are never reused",
}
STATUSES = ["published", "provisional", "generating", "validating", "queued", "needs_repair",
            "quarantined", "candidate", "retired"]  # fmt: skip
MISS_STATUSES = ["pending", "provisional", "published", "queued", "requestable", "invalid"]

AI_USE = """
select count(distinct (user_id, ref_key)),
       (select count(*) from (
          select user_id from llm_calls
          where task = any(%(tasks)s) and outcome in ('ok', 'fallback') and user_id is not null
            and created_at > now() - interval '1 day'
          group by user_id having count(distinct ref_key) >= %(cap)s) capped)
from llm_calls
where task = any(%(tasks)s) and outcome in ('ok', 'fallback') and user_id is not null
  and created_at > now() - interval '1 day'
"""


def providers_with_keys(settings: Settings) -> list[str]:
    """Names only: whether a key is stored, never the key."""
    return [name for name in AI_PROVIDERS if getattr(settings, f"{name}_api_key") is not None]


def stats(
    conn: psycopg.Connection, settings: Settings, overrides: Mapping[str, Any]
) -> dict[str, Any]:
    by_status = dict(conn.execute("select status::text, count(*) from entries group by 1"))
    published_today = _scalar(
        conn,
        "select count(*) from entries where status = 'published'"
        " and published_at > now() - interval '24 hours'",
    )
    waiting = sum(by_status.get(status, 0) for status in ("queued", "needs_repair", "provisional"))

    misses = dict(conn.execute("select status, count(*) from misses group by 1"))
    stuck = _scalar(
        conn,
        "select count(*) from misses where status = 'pending' and attempts >= %s",
        MAX_ATTEMPTS,
    )

    cap = overrides.get("ai_searches_per_day", settings.ai_searches_per_day)
    budget = overrides.get("ai_monthly_budget_usd", settings.ai_monthly_budget_usd)
    order = overrides.get("ai_provider_order", settings.ai_providers)
    keys = providers_with_keys(settings)
    use = conn.execute(AI_USE, {"tasks": AI_TASKS, "cap": max(cap, 1)}).fetchone()
    searches, at_cap = use or (0, 0)
    spent = _scalar(
        conn,
        "select coalesce(sum(cost_usd), 0) from llm_calls"
        " where created_at >= date_trunc('month', now())",
    )
    calls = conn.execute(
        "select provider, outcome, count(*), round(avg(latency_ms)), coalesce(sum(cost_usd), 0)"
        " from llm_calls where created_at > now() - interval '24 hours'"
        " group by 1, 2 order by 1, 2"
    ).fetchall()
    requests = dict(
        conn.execute(
            "select case when status <> 'pending' or origin = 'student' then status"
            " when origin = 'ai_entry' then 'pending_ai_entries' else 'pending_related' end,"
            " count(*) from word_requests group by 1"
            " union all select 'auto_approved', count(*) from word_requests"
            " where status = 'approved' and decided_by = 'auto:lists'"
        ).fetchall()
    )
    unapproved = _scalar(
        conn, "select count(*) from entries where approved_at is null and status <> 'retired'"
    )
    uploaded, unsent = conn.execute(
        "select count(*) filter (where uploaded_at is not null),"
        " count(*) filter (where uploaded_at is null) from audio_assets"
    ).fetchone() or (0, 0)

    return {
        "entries": {
            "by_status": [
                {"status": status, "count": by_status.get(status, 0), "meaning": MEANINGS[status]}
                for status in STATUSES
            ],
            "total": sum(by_status.values()),
            "published_last_24h": published_today,
            "waiting": waiting,
            "days_to_clear": round(waiting / published_today, 1) if published_today else None,
            "unapproved": unapproved,
        },
        "requests": {
            name: requests.get(name, 0)
            for name in (
                "pending",
                "pending_ai_entries",
                "pending_related",
                "approved",
                "auto_approved",
                "rejected",
            )
        },
        "misses": {
            "by_status": {status: misses.get(status, 0) for status in MISS_STATUSES},
            "stuck": stuck,
        },
        "ai": {
            "month_spend_usd": float(spent),
            "monthly_budget_usd": budget,
            "searches_per_day": cap,
            "searches_last_24h": searches,
            "students_at_cap": at_cap if cap else 0,
            "provider_order": order,
            "providers_with_keys": keys,
            "active_providers": [name for name in order if name in keys],
            "calls_last_24h": [
                {
                    "provider": provider,
                    "outcome": outcome,
                    "calls": count,
                    "avg_latency_ms": int(latency) if latency is not None else None,
                    "cost_usd": float(cost),
                }
                for provider, outcome, count, latency, cost in calls
            ],
        },
        "audio": {"uploaded": uploaded, "waiting": unsent},
        "recent_runs": [asdict(run) for run in progress.recent_runs(conn, 10)],
    }


def _scalar(conn: psycopg.Connection, query: Any, *params: Any) -> Any:
    row = conn.execute(query, params or None).fetchone()
    return row[0] if row else 0
