"""Lookup and autocomplete (design §4.1-4.3): SQL generators fused into ranked groups.

There is no LLM in this path. Generators: G1 exact and G2 inflected forms
(entry_forms), G4 student-language equivalents, G5 prefixes for autocomplete, and
G6 trigram and phonetic matches, which only feed "did you mean". G3 (multiword
expressions in context), G7 (meaning) and context ranking arrive with spaCy and the
embedder.
"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from smart_dictionary.content.cards import VARIETY_LANGUAGE, preview
from smart_dictionary.core.principal import Principal
from smart_dictionary.core.text import norm

Direction = Literal["auto", "en", "l1"]
Side = Literal["en", "l1"]

BASE_SCORES = {"exact": 1.0, "form": 0.9, "equivalent": 0.85}
_LIVE = "e.status in ('published', 'provisional')"
_ENTRY = "e.key, e.lemma, e.pos, e.kind, e.cefr, e.freq_zipf, e.status, e.doc"

FORMS = text(f"""
select {_ENTRY}, f.form as matched, (f.form_norm = e.lemma_norm) as is_lemma
from entry_forms f join entries e on e.id = f.entry_id
where f.form_norm = :q and {_LIVE}
""")

EQUIVALENTS = text(f"""
select {_ENTRY}, q.term as matched, q.sense_key, q.rank
from equivalents q
join senses s on s.key = q.sense_key and s.retired_at is null
join entries e on e.id = s.entry_id
where q.term_norm = :q and q.variety = :variety and {_LIVE}
order by q.rank
""")

FUZZY = text(f"""
select * from (
  select distinct on (e.key) {_ENTRY}, f.form as matched,
         extensions.similarity(f.form_norm, :q) as similarity
  from entry_forms f join entries e on e.id = f.entry_id
  where f.form_norm operator(extensions.%) :q and {_LIVE}
  order by e.key, similarity desc
) found order by similarity desc limit 5
""")

FUZZY_L1 = text(f"""
select * from (
  select distinct on (e.key) {_ENTRY}, q.term as matched, q.sense_key,
         extensions.similarity(q.term_norm, :q) as similarity
  from equivalents q
  join senses s on s.key = q.sense_key and s.retired_at is null
  join entries e on e.id = s.entry_id
  where q.variety = :variety and q.term_norm operator(extensions.%) :q and {_LIVE}
  order by e.key, similarity desc
) found order by similarity desc limit 5
""")

PHONETIC = text(f"""
select distinct on (e.key) {_ENTRY}, f.form as matched
from entry_forms f join entries e on e.id = f.entry_id
where f.phonetic = extensions.dmetaphone(:q) and {_LIVE}
order by e.key
limit 5
""")

PREFIX = text(f"""
select {_ENTRY}, e.lemma as matched
from entries e
where e.language = 'en' and e.lemma_norm like :prefix escape '\\' and {_LIVE}
order by (e.lemma_norm = :q) desc, e.freq_zipf desc nulls last, length(e.lemma_norm), e.lemma_norm
limit :limit
""")

PREFIX_L1 = text(f"""
select * from (
  select distinct on (e.key) {_ENTRY}, q.term as matched, q.sense_key, length(q.term_norm) as size
  from equivalents q
  join senses s on s.key = q.sense_key and s.retired_at is null
  join entries e on e.id = s.entry_id
  where q.variety = :variety and q.term_norm like :prefix escape '\\' and {_LIVE}
  order by e.key, q.rank
) found order by size, freq_zipf desc nulls last
limit :limit
""")

LOG_LOOKUP = text("""
insert into lookups
  (tenant_id, meeting_id, query, direction, entry_key, sense_key, result, latency_ms)
values (:tenant, :meeting, :query, :direction, :entry, :sense, :result, :latency)
""")

LOG_MISS = text("""
insert into misses (tenant_id, language, query_norm) values (:tenant, :language, :q)
on conflict (tenant_id, language, query_norm)
do update set hits = misses.hits + 1, last_seen = now()
""")


@dataclass(frozen=True)
class Query:
    raw: str
    direction: Direction
    variety: str

    @property
    def language(self) -> str:
        return VARIETY_LANGUAGE[self.variety]

    @property
    def norm_en(self) -> str:
        return norm(self.raw, "en")

    @property
    def norm_l1(self) -> str:
        return norm(self.raw, self.language)

    @property
    def wants_en(self) -> bool:
        return self.direction in ("auto", "en")

    @property
    def wants_l1(self) -> bool:
        return self.direction in ("auto", "l1")


class Matched(BaseModel):
    via: str
    text: str


class LookupItem(BaseModel):
    entry_key: str
    lemma: str
    pos: str
    kind: str
    cefr: str | None
    sense_key: str | None
    short_gloss: str | None
    equivalent: str | None
    matched: Matched
    score: float
    provisional: bool


class LookupGroup(BaseModel):
    side: Side
    items: list[LookupItem]


class Suggestion(BaseModel):
    entry_key: str
    lemma: str
    pos: str
    cefr: str | None
    equivalent: str | None
    side: Side
    matched: str


async def lookup(
    conn: AsyncConnection, query: Query, *, limit: int = 10
) -> tuple[list[LookupGroup], list[Suggestion]]:
    """Grouped results, English side first; "did you mean" only when nothing matched directly."""
    english: dict[str, LookupItem] = {}
    student: dict[str, LookupItem] = {}
    if query.wants_en and query.norm_en:
        for row in await conn.execute(FORMS, {"q": query.norm_en}):
            via = "exact" if row.is_lemma else "form"
            _keep_best(english, _item(row, query.variety, via, BASE_SCORES[via]))
    if query.wants_l1 and query.norm_l1:
        for row in await conn.execute(EQUIVALENTS, {"q": query.norm_l1, "variety": query.variety}):
            base = BASE_SCORES["equivalent"] - 0.03 * (row.rank - 1)
            _keep_best(student, _item(row, query.variety, "equivalent", base, row.sense_key))
    sides: list[tuple[Side, dict[str, LookupItem]]] = [("en", english), ("l1", student)]
    groups = [
        LookupGroup(side=side, items=sorted(found.values(), key=lambda i: -i.score)[:limit])
        for side, found in sides
        if found
    ]
    return groups, ([] if groups else await _did_you_mean(conn, query))


async def suggest(conn: AsyncConnection, query: Query, *, limit: int = 8) -> list[Suggestion]:
    found: list[Suggestion] = []
    if query.wants_en and query.norm_en:
        prefix = escape_like(query.norm_en) + "%"
        rows = await conn.execute(PREFIX, {"q": query.norm_en, "prefix": prefix, "limit": limit})
        found += [_suggestion(row, query.variety, "en") for row in rows]
    if query.wants_l1 and query.norm_l1:
        prefix = escape_like(query.norm_l1) + "%"
        params = {"variety": query.variety, "prefix": prefix, "limit": limit}
        rows = await conn.execute(PREFIX_L1, params)
        found += [_suggestion(row, query.variety, "l1", row.sense_key) for row in rows]
    return found[:limit]


async def log_lookup(
    conn: AsyncConnection,
    principal: Principal,
    query: Query,
    groups: list[LookupGroup],
    did_you_mean: list[Suggestion],
    latency_ms: int,
    miss_language: str | None = None,
) -> None:
    """Record the lookup [OP-1]; a lookup with no result at all also counts as a miss [LK-10],
    under `miss_language` when the missing-word flow has routed it (ai.provisional)."""
    top = groups[0].items[0] if groups else None
    result = "hit" if groups else ("invalid" if not query.norm_en else "miss")
    await conn.execute(
        LOG_LOOKUP,
        {
            "tenant": principal.tenant,
            "meeting": principal.meeting,
            "query": query.raw[:100],
            "direction": query.direction,
            "entry": top.entry_key if top else None,
            "sense": top.sense_key if top else None,
            "result": result,
            "latency": latency_ms,
        },
    )
    if result == "miss" and not did_you_mean:
        language = miss_language or (query.language if query.direction == "l1" else "en")
        normalized = query.norm_l1 if language != "en" else query.norm_en
        await conn.execute(
            LOG_MISS, {"tenant": principal.tenant, "language": language, "q": normalized}
        )
    await conn.commit()


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _did_you_mean(conn: AsyncConnection, query: Query) -> list[Suggestion]:
    found: dict[tuple[str, str], Suggestion] = {}
    if query.wants_en and query.norm_en:
        rows = list(await conn.execute(FUZZY, {"q": query.norm_en}))
        if " " not in query.norm_en:
            rows += list(await conn.execute(PHONETIC, {"q": query.norm_en}))
        for row in rows:
            found.setdefault((row.key, "en"), _suggestion(row, query.variety, "en"))
    if query.wants_l1 and query.norm_l1:
        params = {"q": query.norm_l1, "variety": query.variety}
        for row in await conn.execute(FUZZY_L1, params):
            found.setdefault((row.key, "l1"), _suggestion(row, query.variety, "l1", row.sense_key))
    return list(found.values())[:5]


def _item(
    row: Any, variety: str, via: str, base: float, sense_key: str | None = None
) -> LookupItem:
    provisional = row.status == "provisional"
    score = base + min(float(row.freq_zipf or 0), 7.0) / 70 - (0.05 if provisional else 0.0)
    matched_sense, short_gloss, equivalent = preview(row.doc, variety, sense_key)
    return LookupItem(
        entry_key=row.key,
        lemma=row.lemma,
        pos=row.pos,
        kind=row.kind,
        cefr=row.cefr,
        sense_key=matched_sense,
        short_gloss=short_gloss,
        equivalent=equivalent,
        matched=Matched(via=via, text=row.matched),
        score=round(score, 4),
        provisional=provisional,
    )


def _keep_best(found: dict[str, LookupItem], item: LookupItem) -> None:
    current = found.get(item.entry_key)
    if current is None or item.score > current.score:
        found[item.entry_key] = item


def _suggestion(row: Any, variety: str, side: Side, sense_key: str | None = None) -> Suggestion:
    _, _, equivalent = preview(row.doc, variety, sense_key)
    return Suggestion(
        entry_key=row.key,
        lemma=row.lemma,
        pos=row.pos,
        cefr=row.cefr,
        equivalent=equivalent,
        side=side,
        matched=row.matched,
    )
