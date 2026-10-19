"""Missing words become provisional entries (design §6.5) [LK-10, CG-4].

A lookup that finds nothing still answers at once: lookups never wait on AI (NF-1). When
the query is plausible English, the lookup schedules one generation to run after its
response and tells the plugin "generating". Asking again a moment later finds the new
entry, marked provisional. The routine leases provisional entries first and replaces each
with a full entry under the same key.

1. **Gate**, in the lookup, SQL only:
   - every word is known English (wordfreq or Wiktionary) and none is blocked;
   - the miss isn't settled already;
   - the student has generations left today.
2. **Dedupe:** an advisory lock per query, so students asking at once cause one AI call.
3. **Generate:** the gateway's provisional_entry task drafts one or two senses.
4. **Check and publish:**
   - the factory's checks, with the counts of a short entry (V11);
   - the draft must be for the word asked;
   - it publishes as provisional, unless the factory already holds that key.
"""

import logging
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from smart_dictionary.ai.gateway import Answer, Gateway, Task, Unavailable
from smart_dictionary.content.projections import english_forms
from smart_dictionary.content.publish import publish
from smart_dictionary.core.entrydoc import CEFR, KIND_POS, POS, EntryDoc, Kind
from smart_dictionary.core.keys import entry_key, example_key, sense_key
from smart_dictionary.core.principal import Principal
from smart_dictionary.core.text import norm
from smart_dictionary.db.sync import connect
from smart_dictionary.factory.packs import MAX_HINTS, hint
from smart_dictionary.factory.validate import BLOCKED, Context, Entry, score, validate
from smart_dictionary.search.service import Query

log = logging.getLogger(__name__)

QUOTA_PER_DAY = 10  # generations a student can start in 24 hours (design §7.1)
MIN_ZIPF = 1.0  # wordfreq knows the word at least this well
MAX_WORDS = 4
RETRY_AFTER_MS = 1500
CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]
# Entry statuses a provisional entry may take over: nobody has written the word yet.
REPLACEABLE = ("candidate", "queued")

Status = Literal["generating", "ready", "queued", "invalid", "quota_exceeded", "unavailable"]
# A settled miss, as the lookup reports it.
MISS_STATUS: dict[str, Status] = {
    "invalid": "invalid",
    "provisional": "ready",
    "published": "ready",
    "queued": "queued",
}


class ProvisionalStatus(BaseModel):
    """What a lookup that found nothing did about it (docs/03 §5.3)."""

    status: Status
    entry_key: str | None = None
    retry_after_ms: int | None = None


class DraftEquivalent(BaseModel):
    term: str = Field(max_length=80)
    match: Literal["exact", "near", "approx", "paraphrase"]


class DraftSense(BaseModel):
    cefr: CEFR
    definition: str = Field(max_length=240)
    short_gloss: str = Field(max_length=80)
    gloss_es: str = Field(max_length=240)
    equivalents: list[DraftEquivalent] = Field(min_length=1, max_length=3)
    example: str = Field(max_length=240)
    example_es: str = Field(max_length=300)


class Draft(BaseModel):
    """What the model answers: a verdict on the query, and for an entry its senses."""

    verdict: Literal["entry", "not_english", "not_a_word", "unsuitable"]
    lemma: str | None = Field(default=None, max_length=80)
    pos: POS | None = None
    kind: Kind | None = None
    senses: list[DraftSense] = Field(default_factory=list, max_length=2)


PROMPT = Path(__file__).parent / "prompts" / "provisional_entry.v1.md"
PROVISIONAL = Task(
    name="provisional_entry",
    prompt_version=PROMPT.stem,
    system=PROMPT.read_text(encoding="utf-8"),
    output=Draft,
    max_tokens=900,
)


@dataclass(frozen=True)
class Job:
    query: str  # normalized
    tenant: str
    user_id: uuid.UUID | None


# 1. The gate, in the lookup -----------------------------------------------------------------

KNOWN = text("""
select w from unnest(cast(:words as text[])) as w
where exists (select 1 from ref_freq f where f.language = 'en' and f.word = w and f.zipf >= :zipf)
   or exists (select 1 from ref_wiktionary k where k.word_norm = w)
   or exists (select 1 from ref_forms o where o.language = 'en' and o.form_norm = w)
""")

MISS = text("""
select status, entry_key from misses
where tenant_id = :tenant and language = 'en' and query_norm = :q
""")

STUDENT = text("""
with added as (
  insert into users (tenant_id, external_id) values (:tenant, :subject)
  on conflict (tenant_id, external_id) do nothing
  returning id
)
select id from added
union all
select id from users where tenant_id = :tenant and external_id = :subject
limit 1
""")

USED_TODAY = text("""
select count(*) from llm_calls
where task = 'provisional_entry' and user_id = :user and outcome in ('ok', 'fallback')
  and created_at > now() - interval '1 day'
""")

# Hits stay with the lookup log (search.service), which counts misses without suggestions.
ADD_MISS = text("""
insert into misses (tenant_id, language, query_norm, hits) values (:tenant, 'en', :q, 0)
on conflict (tenant_id, language, query_norm) do nothing
""")


async def on_miss(
    conn: AsyncConnection, principal: Principal, query: Query, gateway: Gateway
) -> tuple[ProvisionalStatus | None, Job | None]:
    """What a lookup that found nothing does, decided without AI. The Job, if any, is the
    generation to run once the response has gone; the caller's transaction commits it."""
    if not query.wants_en:
        return None, None  # a search in the student's own language
    q = query.norm_en
    words = q.split()
    if not words:
        return None, None
    if len(words) > MAX_WORDS or BLOCKED.intersection(words):
        return ProvisionalStatus(status="invalid"), None
    known = {row[0] for row in await conn.execute(KNOWN, {"words": words, "zipf": MIN_ZIPF})}
    if not known.issuperset(words):
        return ProvisionalStatus(status="invalid"), None
    miss = (await conn.execute(MISS, {"tenant": principal.tenant, "q": q})).first()
    if miss is not None and miss.status != "pending":
        return ProvisionalStatus(status=MISS_STATUS[miss.status], entry_key=miss.entry_key), None
    if not gateway.configured:
        return ProvisionalStatus(status="unavailable"), None
    user_id = None
    if principal.subject:
        params = {"tenant": principal.tenant, "subject": principal.subject}
        user_id = (await conn.execute(STUDENT, params)).scalar_one()
        if (await conn.execute(USED_TODAY, {"user": user_id})).scalar_one() >= QUOTA_PER_DAY:
            return ProvisionalStatus(status="quota_exceeded"), None
    await conn.execute(ADD_MISS, {"tenant": principal.tenant, "q": q})
    status = ProvisionalStatus(status="generating", retry_after_ms=RETRY_AFTER_MS)
    return status, Job(query=q, tenant=principal.tenant, user_id=user_id)


# 2-4. The generation, after the response ----------------------------------------------------


def generate(job: Job, gateway: Gateway) -> None:
    """Runs in a worker thread once the lookup has answered. A failure only leaves the miss
    pending, so a later lookup tries again; it never reaches the student."""
    try:
        with connect() as conn:
            # Held until the connection closes, so one request writes the word at a time.
            lock = f"provisional:{job.tenant}:{job.query}"
            locked = conn.execute("select pg_try_advisory_lock(hashtextextended(%s, 0))",
                                  (lock,)).fetchone()  # fmt: skip
            conn.commit()
            if locked and locked[0]:
                _generate(conn, job, gateway)
    except Exception:
        log.exception("the provisional entry for %r failed", job.query)


def _generate(conn: psycopg.Connection, job: Job, gateway: Gateway) -> None:
    pending = conn.execute(
        "select status from misses where tenant_id = %s and language = 'en' and query_norm = %s",
        (job.tenant, job.query),
    ).fetchone()
    if pending is None or pending[0] != "pending":
        return  # settled while this job waited
    try:
        answer = gateway.run(conn, PROVISIONAL, _facts(conn, job.query), cache_key=job.query,
                             tenant=job.tenant, user_id=job.user_id, ref_key=job.query)  # fmt: skip
    except Unavailable:
        conn.commit()  # keep the logged attempts
        log.warning("no AI provider could write %r", job.query)
        return
    conn.commit()
    draft = answer.value
    if draft.verdict != "entry":
        _settle(conn, job, "invalid", None)
        gateway.remember(conn, PROVISIONAL, job.query, answer)
        conn.commit()
        return
    raw = _document(conn, draft)
    if raw is None:
        log.warning("the draft for %r can't make an entry: %s", job.query, draft)
        return
    entry = Entry(key=raw["key"], lemma=raw["lemma"], pos=raw["pos"], kind=raw["kind"])
    issues = validate(entry, raw, Context.load(conn, [entry], [raw]), provisional=True)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        log.warning("the draft for %r failed its checks: %s", job.query,
                    "; ".join(f"{i.check} {i.where}: {i.message}" for i in errors))  # fmt: skip
        return
    doc = EntryDoc.model_validate(raw)
    if job.query not in english_forms(doc):
        log.warning("the draft for %r is about %r instead", job.query, doc.lemma)
        return
    _publish(conn, job, gateway, answer, doc, [asdict(issue) for issue in issues],
             score(issues, None))  # fmt: skip


def _publish(
    conn: psycopg.Connection,
    job: Job,
    gateway: Gateway,
    answer: Answer[Draft],
    doc: EntryDoc,
    issues: list[dict[str, Any]],
    quality: float,
) -> None:
    current = conn.execute(
        "select status::text from entries where key = %s for update", (doc.key,)
    ).fetchone()
    if current is not None and current[0] not in REPLACEABLE:
        # The factory has the word (being written, or published already): point to it.
        settled = {"published": "published", "provisional": "provisional", "retired": "invalid"}
        _settle(conn, job, settled.get(current[0], "queued"), doc.key)
        conn.commit()
        return
    produced_by = {"source": "provisional", "task": PROVISIONAL.name,
                   "prompt": PROVISIONAL.prompt_version, "provider": answer.provider,
                   "model": answer.model, "query": job.query}  # fmt: skip
    publish(conn, doc, produced_by=produced_by, qa={"issues": issues}, quality=quality,
            sources=["provisional"], status="provisional")  # fmt: skip
    _settle(conn, job, "provisional", doc.key)
    gateway.remember(conn, PROVISIONAL, job.query, answer)
    conn.commit()


def _settle(conn: psycopg.Connection, job: Job, status: str, key: str | None) -> None:
    conn.execute(
        "update misses set status = %s, entry_key = %s"
        " where tenant_id = %s and language = 'en' and query_norm = %s",
        (status, key, job.tenant, job.query),
    )


def _facts(conn: psycopg.Connection, query: str) -> str:
    """The user turn: the query, and the open data's facts about it (hints, never prose)."""
    zipf = conn.execute(
        "select max(zipf) from ref_freq where language = 'en' and word = %s", (query,)
    ).fetchone()
    rows = conn.execute(
        "select pos, glosses from ref_wiktionary where word_norm = %s order by pos", (query,)
    ).fetchall()
    hints = list(dict.fromkeys(f"{pos}: {hint(gloss)}" for pos, glosses in rows
                               for gloss in glosses))[:MAX_HINTS]  # fmt: skip
    lines = [f"Query: {query}", "", "Facts:"]
    if zipf and zipf[0] is not None:
        lines.append(f"- frequency on the Zipf scale (0-8): {float(zipf[0]):.1f}")
    lines += [f"- Wiktionary hint, {line}" for line in hints] or ["- no Wiktionary entry"]
    return "\n".join(lines)


def _document(conn: psycopg.Connection, draft: Draft) -> dict[str, Any] | None:
    """The entry document for a draft, or None when the draft can't make a valid key."""
    if not (draft.lemma and draft.pos and draft.kind and draft.senses):
        return None
    lemma = " ".join(draft.lemma.split())
    kind = draft.kind
    pos = KIND_POS.get(kind, draft.pos)
    try:
        key = entry_key("en", lemma, pos, kind)
    except (KeyError, ValueError):  # a single word drafted as pos "phrase", for example
        return None
    senses = []
    for number, sense in enumerate(draft.senses, start=1):
        key_n = sense_key(key, number)
        senses.append({
            "key": key_n,
            "cefr": sense.cefr,
            "definition": sense.definition,
            "short_gloss": sense.short_gloss,
            "l1": {"es-419": {
                "gloss": sense.gloss_es,
                "equivalents": [equivalent.model_dump() for equivalent in sense.equivalents],
            }},
            "examples": [{
                "key": example_key(key_n, 1),
                "text": sense.example,
                "cefr": sense.cefr,
                "translations": {"es-419": sense.example_es},
            }],
        })  # fmt: skip
    doc: dict[str, Any] = {
        "schema_version": 1,
        "key": key,
        "language": "en",
        "lemma": lemma,
        "pos": pos,
        "kind": kind,
        "cefr": min((sense.cefr for sense in draft.senses), key=CEFR_ORDER.index),
        "pronunciations": _pronunciations(conn, lemma) if kind == "word" else [],
        "senses": senses,
        "provisional": True,
    }
    if kind != "word":
        first, *rest = lemma.split()
        doc["pattern"] = {"tokens": [{"lemma": first}, *({"text": word} for word in rest)]}
    return doc


def _pronunciations(conn: psycopg.Connection, lemma: str) -> list[dict[str, str]]:
    """The reference IPA (CMUdict, Britfone), first per variety, so V7 has nothing to flag."""
    found: dict[str, str] = {}
    for variety, ipa in conn.execute(
        "select variety, ipa from ref_ipa where word_norm = %s order by id", (norm(lemma),)
    ):
        found.setdefault(variety, ipa)
    return [{"variety": variety, "ipa": found[variety]}
            for variety in ("en-US", "en-GB") if variety in found]  # fmt: skip
