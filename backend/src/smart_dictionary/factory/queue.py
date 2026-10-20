"""Queue seed candidates as entries for the factory (design §6.1 step 8, §3.3).

Queuing is the owner's choice, so every queued entry is approved: students may see it once it's
written, and AI may write its Beta when a student searches for it (design §6.5).

A queued entry has a key, its facts and a priority, but no document: the routine leases it,
writes it and submits it. Candidates wait for the curation run when they lack a part of
speech or kind, or when the two can't form an entry (a `word` marked `phrase`, as the entry
model requires). Rejected or merged candidates are never queued.

Priority puts the level first (A1 to C2, then words without a level), the owner's lists
before the open lists within a level, and frequency after that. The first milestone is
A1-B2 coverage, so ordering by tier alone would start with function words and leave basic
words that only CEFR-J lists behind the owner's long tail.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from smart_dictionary.core.ids import new_id
from smart_dictionary.core.keys import entry_key

LEVEL_RANK = {"A1": 6, "A2": 5, "B1": 4, "B2": 3, "C1": 2, "C2": 1}
# A word students asked for jumps the factory's queue: above any seed's priority.
ASKED_PRIORITY = 100_000

ELIGIBLE = """
select c.id, c.lemma, c.lemma_norm, c.pos, c.kind::text as kind, c.homograph, c.tier, c.zipf,
       c.levels, c.sources
from seed_candidates c
where c.pos is not null and c.kind is not null and c.entry_id is null
  and coalesce(c.decision, 'accept') = 'accept'
  and case c.kind when 'word' then c.pos <> 'phrase' when 'phrasal_verb' then c.pos = 'verb'
                  else c.pos = 'phrase' end
  and (%(lemmas)s::text[] is null or c.lemma_norm = any(%(lemmas)s))
  and (%(levels)s::text[] is null or c.levels && %(levels)s)
  and not exists (
    select from entries e
    where e.language = 'en' and e.lemma_norm = c.lemma_norm and e.pos = c.pos
      and e.kind = c.kind and e.homograph = c.homograph)
"""

CHUNK = 2000
# One statement per chunk. A key that already exists is skipped, so its candidate stays
# unlinked; sources travel as JSON because Postgres arrays of arrays must be rectangular.
INSERT = """
insert into entries (id, key, language, lemma, lemma_norm, pos, kind, homograph, cefr,
                     freq_zipf, priority, status, sources, approved_at, approved_by)
select r.id, r.key, 'en', r.lemma, r.lemma_norm, r.pos, r.kind::entry_kind, r.homograph,
       r.cefr, r.zipf, r.priority, 'queued',
       array(select jsonb_array_elements_text(r.sources::jsonb)), now(), 'seeds'
from unnest(%s::uuid[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[],
            %s::smallint[], %s::text[], %s::numeric[], %s::integer[], %s::text[])
  as r(id, key, lemma, lemma_norm, pos, kind, homograph, cefr, zipf, priority, sources)
on conflict do nothing
"""
# Links each candidate to the entry made for it: only the ids that were inserted match.
LINK = """
update seed_candidates c
set entry_id = e.id, decision = coalesce(c.decision, 'accept'), updated_at = now()
from unnest(%s::uuid[], %s::uuid[]) as made(candidate_id, entry_id)
join entries e on e.id = made.entry_id
where c.id = made.candidate_id
returning c.id
"""


@dataclass(frozen=True)
class Queued:
    key: str
    lemma: str
    pos: str
    kind: str
    cefr: str | None
    priority: int


def priority(levels: list[str], tier: int | None, zipf: Decimal | float | None) -> int:
    """Level first, the owner's lists (tier 0) next, then Zipf frequency."""
    rank = max((LEVEL_RANK.get(level, 0) for level in levels), default=0)
    owner = 1_000 if tier == 0 else 0
    return rank * 10_000 + owner + round(float(zipf or 0) * 100)


def easiest(levels: list[str]) -> str | None:
    known = [level for level in levels if level in LEVEL_RANK]
    return max(known, key=LEVEL_RANK.__getitem__) if known else None


def queue(
    conn: psycopg.Connection,
    *,
    lemmas: list[str] | None = None,
    levels: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> list[Queued]:
    """Queue the eligible candidates in priority order; returns what was (or would be) queued.

    The rows go in chunks of CHUNK, two statements each, and each chunk is committed: a
    full queue of ~38,000 words takes seconds over a remote connection instead of hours,
    shows its progress, and an interrupted run keeps its chunks, so running it again
    carries on from there."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(ELIGIBLE, {"lemmas": lemmas, "levels": levels}).fetchall()
    for row in rows:
        row["priority"] = priority(row["levels"], row["tier"], row["zipf"])
    rows.sort(key=lambda row: (-row["priority"], row["lemma_norm"], row["pos"]))
    rows = rows[:limit] if limit is not None else rows
    planned = [
        (row, entry_key("en", row["lemma"], row["pos"], row["kind"], row["homograph"]),
         easiest(row["levels"]))
        for row in rows
    ]  # fmt: skip
    if dry_run:
        return [_queued(row, key, cefr) for row, key, cefr in planned]

    queued: list[Queued] = []
    for start in range(0, len(planned), CHUNK):
        chunk = planned[start : start + CHUNK]
        ids = [new_id() for _ in chunk]
        conn.execute(
            INSERT,
            (
                ids,
                [key for _, key, _ in chunk],
                [row["lemma"] for row, _, _ in chunk],
                [row["lemma_norm"] for row, _, _ in chunk],
                [row["pos"] for row, _, _ in chunk],
                [row["kind"] for row, _, _ in chunk],
                [row["homograph"] for row, _, _ in chunk],
                [cefr for _, _, cefr in chunk],
                [row["zipf"] for row, _, _ in chunk],
                [row["priority"] for row, _, _ in chunk],
                [json.dumps(row["sources"]) for row, _, _ in chunk],
            ),
        )
        linked = {
            found[0]
            for found in conn.execute(LINK, ([row["id"] for row, _, _ in chunk], ids)).fetchall()
        }
        conn.commit()
        queued += [_queued(row, key, cefr) for row, key, cefr in chunk if row["id"] in linked]
        if progress is not None:
            progress(start + len(chunk), len(planned))
    return queued


def _queued(row: dict[str, Any], key: str, cefr: str | None) -> Queued:
    return Queued(key, row["lemma"], row["pos"], row["kind"], cefr, row["priority"])
