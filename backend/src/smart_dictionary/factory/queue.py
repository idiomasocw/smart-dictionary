"""Queue seed candidates as entries for the factory (design §6.1 step 8, §3.3).

A queued entry has a key, its facts and a priority, but no document: the routine leases it,
writes it and submits it. Candidates wait for the curation run when they lack a part of
speech or kind, or when the two can't form an entry (a `word` marked `phrase`, as the entry
model requires). Rejected or merged candidates are never queued.

Priority puts the level first (A1 to C2, then words without a level), the owner's lists
before the open lists within a level, and frequency after that. The first milestone is
A1-B2 coverage, so ordering by tier alone would start with function words and leave basic
words that only CEFR-J lists behind the owner's long tail.
"""

from dataclasses import dataclass
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from smart_dictionary.core.ids import new_id
from smart_dictionary.core.keys import entry_key

LEVEL_RANK = {"A1": 6, "A2": 5, "B1": 4, "B2": 3, "C1": 2, "C2": 1}

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
) -> list[Queued]:
    """Queue the eligible candidates in priority order; returns what was (or would be) queued."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(ELIGIBLE, {"lemmas": lemmas, "levels": levels}).fetchall()
    for row in rows:
        row["priority"] = priority(row["levels"], row["tier"], row["zipf"])
    rows.sort(key=lambda row: (-row["priority"], row["lemma_norm"], row["pos"]))
    rows = rows[:limit] if limit is not None else rows

    queued: list[Queued] = []
    for row in rows:
        key = entry_key("en", row["lemma"], row["pos"], row["kind"], row["homograph"])
        cefr = easiest(row["levels"])
        if not dry_run:
            inserted = conn.execute(
                "insert into entries (id, key, language, lemma, lemma_norm, pos, kind, homograph,"
                " cefr, freq_zipf, priority, status, sources)"
                " values (%s, %s, 'en', %s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s)"
                " on conflict do nothing returning id",
                (
                    new_id(),
                    key,
                    row["lemma"],
                    row["lemma_norm"],
                    row["pos"],
                    row["kind"],
                    row["homograph"],
                    cefr,
                    row["zipf"],
                    row["priority"],
                    row["sources"],
                ),
            ).fetchone()
            if inserted is None:
                continue
            conn.execute(
                "update seed_candidates set entry_id = %s, decision = coalesce(decision, 'accept'),"
                " updated_at = now() where id = %s",
                (inserted[0], row["id"]),
            )
        queued.append(Queued(key, row["lemma"], row["pos"], row["kind"], cefr, row["priority"]))
    return queued
