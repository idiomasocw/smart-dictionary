"""Missing words become provisional entries (design §6.5) [LK-10, CG-4].

A lookup that finds nothing still answers at once: lookups never wait on AI (NF-1). When the
query is worth it, the lookup schedules one AI job to run after its response and tells the
plugin "generating". Asking again a moment later finds the entry, marked provisional. The
routine leases provisional entries first and replaces each with a full entry, same key.

A miss takes one of two paths, by the language it was searched in:
- **English:** the provisional_entry task drafts the missing word.
- **Spanish** (dir=l1, or dir=auto when every word reads as Spanish): the translate_query task
  names the English headwords the student means. If the best one is live already, the miss
  points to it. Otherwise it's drafted as above, with the student's Spanish among its
  equivalents, so the next search finds it directly.

1. **Gate**, in the lookup, SQL only:
   - every word is known in the query's language and none is blocked;
   - the miss isn't settled already;
   - the student has searches left today.
2. **One job at a time:** the lookup claims the miss (`running_since`) before it starts a
   job, so other lookups answer "generating" without starting another; an advisory lock per
   query backs it up. A claim older than RUNNING expires, so a crashed job can't hold a word.
3. **Generate** through the gateway.
4. **Check and publish:**
   - the factory's checks, with the counts of a short entry (V11);
   - the draft must be for the word asked;
   - it publishes as provisional, unless the factory already holds that key.
5. **Failures** (no provider answered; a draft that can't make an entry, fails its checks, is
   about another word or turns the word down; any error) are counted on the miss. For
   COOL_DOWN after each, lookups answer "unavailable" at once, with no job. After
   MAX_ATTEMPTS the word goes to the factory, first in its queue, and lookups answer
   "queued"; one the factory can't take (no seed candidate) stays "unavailable", and
   `smart-dictionary factory status` lists it.
"""

import logging
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import psycopg
from lemminflect import getAllLemmas
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from smart_dictionary.ai.gateway import Answer, Gateway, Task, Unavailable
from smart_dictionary.content.projections import english_forms
from smart_dictionary.content.publish import publish
from smart_dictionary.core import tunables
from smart_dictionary.core.entrydoc import CEFR, KIND_POS, POS, EntryDoc, Kind
from smart_dictionary.core.keys import entry_key, example_key, sense_key
from smart_dictionary.core.principal import Principal
from smart_dictionary.core.settings import get_settings
from smart_dictionary.core.text import norm
from smart_dictionary.db.sync import connect
from smart_dictionary.factory.packs import MAX_HINTS, hint
from smart_dictionary.factory.queue import queue
from smart_dictionary.factory.validate import BLOCKED, Context, Entry, score, validate
from smart_dictionary.search.service import Query

log = logging.getLogger(__name__)

# The tasks a missed search may take; their calls count toward the student's daily searches
# (settings.ai_searches_per_day): a Spanish search counts once, though it may take both.
AI_TASKS = ["provisional_entry", "translate_query"]
MIN_ZIPF = 1.0  # wordfreq knows the word at least this well
MAX_WORDS = 4
RETRY_AFTER_MS = 1500
# Failed attempts (the owner's choices, 2026-10-20).
COOL_DOWN = "10 minutes"  # "unavailable", with no AI call, after each failure
MAX_ATTEMPTS = 3  # then the factory takes the word
RUNNING = "3 minutes"  # a job's claim expires: longer than a Spanish search's worst case
# A word that failed its attempts jumps the factory's queue: above any seed's priority.
ASKED_PRIORITY = 100_000
CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]
# Entry statuses a provisional entry may take over: nobody has written the word yet.
REPLACEABLE = ("candidate", "queued")
# What a miss becomes when the factory already holds its entry.
HELD = {"published": "published", "provisional": "provisional", "retired": "invalid"}

Language = Literal["en", "es"]
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
    """The provisional_entry answer: a verdict on the word, and for an entry its senses."""

    verdict: Literal["entry", "not_english", "not_a_word", "unsuitable"]
    lemma: str | None = Field(default=None, max_length=80)
    pos: POS | None = None
    kind: Kind | None = None
    senses: list[DraftSense] = Field(default_factory=list, max_length=2)


class Headword(BaseModel):
    lemma: str = Field(max_length=80)
    pos: POS
    kind: Kind


class Translation(BaseModel):
    """The translate_query answer: the English headwords a Spanish search means, best first."""

    verdict: Literal["found", "none"]
    headwords: list[Headword] = Field(default_factory=list, max_length=3)


PROMPTS = Path(__file__).parent / "prompts"
PROVISIONAL = Task(
    name="provisional_entry",
    prompt_version="provisional_entry.v1",
    system=(PROMPTS / "provisional_entry.v1.md").read_text(encoding="utf-8"),
    output=Draft,
    max_tokens=900,
)
TRANSLATE = Task(
    name="translate_query",
    prompt_version="translate_query.v1",
    system=(PROMPTS / "translate_query.v1.md").read_text(encoding="utf-8"),
    output=Translation,
    max_tokens=200,
)


@dataclass(frozen=True)
class Job:
    query: str  # normalized
    tenant: str
    user_id: uuid.UUID | None
    language: Language = "en"  # the language the student searched in
    typed: str = ""  # the query as typed: a Spanish one becomes an equivalent
    variety: str = "es-419"


# 1. The gate, in the lookup -----------------------------------------------------------------

KNOWN = text("""
select w from unnest(cast(:words as text[])) as w
where exists (select 1 from ref_freq f
              where f.language = :language and f.word = w and f.zipf >= :zipf)
   or exists (select 1 from ref_forms o where o.language = :language and o.form_norm = w)
   or (cast(:language as text) = 'en'
       and exists (select 1 from ref_wiktionary k where k.word_norm = w))
""")

# Each word's frequency in Spanish and in English.
LANGUAGES = text("""
select coalesce((select max(zipf) from ref_freq where language = 'es' and word = w), 0),
       coalesce((select max(zipf) from ref_freq where language = 'en' and word = w), 0)
from unnest(cast(:words as text[])) as w
""")

MISS = text("""
select status, entry_key, attempts,
       coalesce(running_since > now() - cast(:running as interval), false) as running,
       coalesce(last_attempt_at > now() - cast(:cool_down as interval), false) as cooling
from misses
where tenant_id = :tenant and language = :language and query_norm = :q
""")

# The lookup that starts a job claims the miss. Two lookups at once: the second waits on the
# row, sees the first one's claim, and starts nothing.
CLAIM = text("""
update misses set running_since = now()
where tenant_id = :tenant and language = :language and query_norm = :q
  and status = 'pending' and attempts < :max_attempts
  and (running_since is null or running_since <= now() - cast(:running as interval))
  and (last_attempt_at is null or last_attempt_at <= now() - cast(:cool_down as interval))
returning 1
""")
TIMING = {"running": RUNNING, "cool_down": COOL_DOWN, "max_attempts": MAX_ATTEMPTS}

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

# Searches, not calls: every call for a search carries its query as ref_key.
USED_TODAY = text("""
select count(distinct ref_key) from llm_calls
where task = any(cast(:tasks as text[])) and user_id = :user
  and outcome in ('ok', 'fallback') and created_at > now() - interval '1 day'
""")

# Hits stay with the lookup log (search.service), which counts misses without suggestions.
ADD_MISS = text("""
insert into misses (tenant_id, language, query_norm, hits) values (:tenant, :language, :q, 0)
on conflict (tenant_id, language, query_norm) do nothing
""")


async def on_miss(
    conn: AsyncConnection,
    principal: Principal,
    query: Query,
    gateway: Gateway,
    language: Language | None,
) -> tuple[ProvisionalStatus | None, Job | None]:
    """What a lookup that found nothing does, decided without AI; `language` is where
    searched_in() routed it. The Job, if any, is the work to run once the response has gone;
    the caller's transaction commits the rest."""
    if language is None:
        return None, None  # a student language with no AI path yet
    q = query.norm_l1 if language == "es" else query.norm_en
    words = q.split()
    if not words:
        return None, None
    if len(words) > MAX_WORDS or BLOCKED.intersection(words):
        return ProvisionalStatus(status="invalid"), None
    params = {"words": words, "zipf": MIN_ZIPF, "language": language}
    if not {row[0] for row in await conn.execute(KNOWN, params)}.issuperset(words):
        return ProvisionalStatus(status="invalid"), None
    params = {"tenant": principal.tenant, "language": language, "q": q}
    miss = (await conn.execute(MISS, params | TIMING)).first()
    if miss is not None and miss.status != "pending":
        return ProvisionalStatus(status=MISS_STATUS[miss.status], entry_key=miss.entry_key), None
    overrides = await tunables.overrides_async(conn)
    if not gateway.chain(overrides):
        return ProvisionalStatus(status="unavailable"), None
    generating = ProvisionalStatus(status="generating", retry_after_ms=RETRY_AFTER_MS)
    if miss is not None and miss.running:
        return generating, None  # a job is at work: no second one
    if miss is not None and (miss.cooling or miss.attempts >= MAX_ATTEMPTS):
        return ProvisionalStatus(status="unavailable"), None  # it failed; not again yet
    user_id = None
    if principal.subject:
        student = {"tenant": principal.tenant, "subject": principal.subject}
        user_id = (await conn.execute(STUDENT, student)).scalar_one()
        used = (await conn.execute(USED_TODAY, {"tasks": AI_TASKS, "user": user_id})).scalar_one()
        if used >= overrides.get("ai_searches_per_day", get_settings().ai_searches_per_day):
            return ProvisionalStatus(status="quota_exceeded"), None
    await conn.execute(ADD_MISS, params)
    if (await conn.execute(CLAIM, params | TIMING)).first() is None:
        return generating, None  # another lookup claimed it a moment ago
    job = Job(query=q, tenant=principal.tenant, user_id=user_id, language=language,
              typed=query.raw, variety=query.variety)  # fmt: skip
    return generating, job


async def searched_in(conn: AsyncConnection, query: Query) -> Language | None:
    """The language a missed search was made in, which picks its path: Spanish for a search
    in Spanish (dir=l1), or a dir=auto one whose every word is known in Spanish and at least as
    common there as in English; English for the rest. None for a search in another student
    language, which has no path yet."""
    spanish_student = query.language == "es"
    if query.direction == "l1":
        return "es" if spanish_student else None
    words = query.norm_l1.split()
    if query.direction == "auto" and spanish_student and words:
        rows = (await conn.execute(LANGUAGES, {"words": words})).all()
        if all(float(es) >= MIN_ZIPF and float(es) >= float(en) for es, en in rows):
            return "es"
    return "en"


# 2-4. The work, after the response ----------------------------------------------------------


class NoEntry(Exception):
    """The job ends without an entry. `word` is the English word it was writing, if known."""

    def __init__(self, reason: str, word: str | None = None) -> None:
        super().__init__(reason)
        self.word = word


def generate(job: Job, gateway: Gateway) -> None:
    """Runs in a worker thread once the lookup has answered. Every end settles the miss or
    records a failed attempt, and clears the job's claim; nothing reaches the student."""
    try:
        with connect() as conn:
            # Held until the connection closes, so one request handles the query at a time.
            lock = f"provisional:{job.tenant}:{job.language}:{job.query}"
            locked = conn.execute("select pg_try_advisory_lock(hashtextextended(%s, 0))",
                                  (lock,)).fetchone()  # fmt: skip
            conn.commit()
            if not (locked and locked[0]):
                return  # another job holds the word; its end clears the claim
            if not _pending(conn, job):
                return  # settled while this job waited
            try:
                if job.language == "es":
                    _spanish(conn, job, gateway)
                else:
                    _english(conn, job, gateway)
            except NoEntry as failure:
                conn.rollback()
                log.warning("no provisional entry for %r: %s", job.query, failure)
                _fail(conn, job, str(failure), failure.word)
                conn.commit()
    except Exception as error:
        log.exception("the provisional entry for %r failed", job.query)
        _fail_alone(job, f"error: {type(error).__name__}: {error}"[:300])


def _english(conn: psycopg.Connection, job: Job, gateway: Gateway) -> None:
    answer = _ask(conn, gateway, PROVISIONAL, _facts(conn, job.query), job, job.query)
    if answer.value.verdict != "entry":
        _settle(conn, job, "invalid", None)
        gateway.remember(conn, PROVISIONAL, job.query, answer)
        conn.commit()
        return
    _check_and_publish(conn, job, gateway, answer, job.query, job.query)


def _spanish(conn: psycopg.Connection, job: Job, gateway: Gateway) -> None:
    question = f"Spanish query: {job.typed or job.query}\nThe student's Spanish: {job.variety}"
    translated_key = f"{job.variety}:{job.query}"
    translation = _ask(conn, gateway, TRANSLATE, question, job, translated_key)
    gateway.remember(conn, TRANSLATE, translated_key, translation)
    best = _best(translation.value)
    if best is None:
        _settle(conn, job, "invalid", None)
        conn.commit()
        return
    headword, key = best
    current = conn.execute("select status::text from entries where key = %s", (key,)).fetchone()
    if current is not None and current[0] not in REPLACEABLE:
        # Live already (ready), or the factory is writing it: the miss points there.
        _settle(conn, job, HELD.get(current[0], "queued"), key)
        conn.commit()
        return
    conn.commit()
    word = norm(headword.lemma)
    drafted_key = f"{word}\nfor es:{job.query}"
    facts = _facts(conn, word, spanish=job.typed or job.query, pos=headword.pos)
    draft = _ask(conn, gateway, PROVISIONAL, facts, job, drafted_key)
    if draft.value.verdict != "entry":
        raise NoEntry(f"translated as {word!r}, which the draft turned down", word)
    _with_equivalent(draft.value, job)
    _check_and_publish(conn, job, gateway, draft, word, drafted_key)


def _best(translation: Translation) -> tuple[Headword, str] | None:
    """The first headword that makes a valid key."""
    if translation.verdict != "found":
        return None
    for headword in translation.headwords:
        key = _key(headword.lemma, headword.pos, headword.kind)
        if key is not None:
            return headword, key
    return None


def _with_equivalent(draft: Draft, job: Job) -> None:
    """Make sure the student's Spanish is among the equivalents, so their next search reaches
    the entry through it (the equivalents generator) instead of missing again."""
    if any(norm(equivalent.term, "es") == job.query
           for sense in draft.senses for equivalent in sense.equivalents):  # fmt: skip
        return
    if draft.senses:
        term = " ".join((job.typed or job.query).split()).lower()
        draft.senses[0].equivalents.append(DraftEquivalent(term=term, match="near"))


def _ask[T: BaseModel](
    conn: psycopg.Connection,
    gateway: Gateway,
    task: Task[T],
    question: str,
    job: Job,
    cache_key: str,
) -> Answer[T]:
    """The task's answer, with the student's search as the call's ref_key (the quota counts
    searches). The logged calls are committed either way."""
    try:
        answer = gateway.run(conn, task, question, cache_key=cache_key, tenant=job.tenant,
                             user_id=job.user_id, ref_key=job.query)  # fmt: skip
    except Unavailable:
        conn.commit()
        raise NoEntry(f"no AI provider answered {task.name}") from None
    conn.commit()
    return answer


def _check_and_publish(
    conn: psycopg.Connection,
    job: Job,
    gateway: Gateway,
    answer: Answer[Draft],
    word: str,
    cache_key: str,
) -> None:
    raw = _document(conn, answer.value)
    if raw is None:
        raise NoEntry(f"the draft can't make an entry: {answer.value}"[:300], word)
    entry = Entry(key=raw["key"], lemma=raw["lemma"], pos=raw["pos"], kind=raw["kind"])
    issues = validate(entry, raw, Context.load(conn, [entry], [raw]), provisional=True)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        found = "; ".join(f"{i.check} {i.where}: {i.message}" for i in errors)
        raise NoEntry(f"the draft failed its checks: {found}"[:300], word)
    doc = EntryDoc.model_validate(raw)
    if word not in english_forms(doc):
        raise NoEntry(f"the draft is about {doc.lemma!r} instead", word)
    current = conn.execute(
        "select status::text from entries where key = %s for update", (doc.key,)
    ).fetchone()
    if current is not None and current[0] not in REPLACEABLE:
        # The factory has the word (being written, or live already): point to it.
        _settle(conn, job, HELD.get(current[0], "queued"), doc.key)
        conn.commit()
        return
    produced_by = {
        "source": "provisional",
        "task": PROVISIONAL.name,
        "prompt": PROVISIONAL.prompt_version,
        "provider": answer.provider,
        "model": answer.model,
        "query": job.query,
        "searched_in": job.language,
    }
    publish(conn, doc, produced_by=produced_by, qa={"issues": [asdict(i) for i in issues]},
            quality=score(issues, None), sources=["provisional"], status="provisional")  # fmt: skip
    _settle(conn, job, "provisional", doc.key)
    gateway.remember(conn, PROVISIONAL, cache_key, answer)
    conn.commit()


def _pending(conn: psycopg.Connection, job: Job) -> bool:
    row = conn.execute(
        "select status from misses where tenant_id = %s and language = %s and query_norm = %s",
        (job.tenant, job.language, job.query),
    ).fetchone()
    return row is not None and row[0] == "pending"


def _settle(conn: psycopg.Connection, job: Job, status: str, key: str | None) -> None:
    conn.execute(
        "update misses set status = %s, entry_key = %s, running_since = null"
        " where tenant_id = %s and language = %s and query_norm = %s",
        (status, key, job.tenant, job.language, job.query),
    )


def _fail(conn: psycopg.Connection, job: Job, reason: str, word: str | None) -> None:
    """Count a failed attempt and clear the claim; after the last one, hand the word over."""
    row = conn.execute(
        "update misses set attempts = attempts + 1, last_attempt_at = now(),"
        " last_error = %s, running_since = null"
        " where tenant_id = %s and language = %s and query_norm = %s returning attempts",
        (reason, job.tenant, job.language, job.query),
    ).fetchone()
    if row is not None and row[0] >= MAX_ATTEMPTS:
        english = word or (job.query if job.language == "en" else None)
        if english:
            _to_the_factory(conn, job, english)


def _fail_alone(job: Job, reason: str) -> None:
    """Record a failure after an error, on a fresh connection: the job's may be broken."""
    try:
        with connect() as conn:
            _fail(conn, job, reason, None)
    except Exception:
        log.exception("couldn't record the failure for %r", job.query)


def _to_the_factory(conn: psycopg.Connection, job: Job, word: str) -> None:
    """The word failed every attempt, so the routine writes it next. Its entry (queued from
    its seed candidates if it has none yet) moves to the front of the queue, and the miss points
    to it: lookups answer "queued". A word with no seed candidate stays "unavailable", and
    `factory status` lists it."""
    lemmas = sorted({word, *(lemma for found in getAllLemmas(word).values() for lemma in found)})
    current = _entry_for(conn, lemmas)
    if current is None:
        queue(conn, lemmas=lemmas)  # commits as it goes
        current = _entry_for(conn, lemmas)
    if current is None:
        return
    key, status = current
    conn.execute(
        "update entries set priority = greatest(priority, %s) where key = %s"
        " and status in ('candidate', 'queued', 'needs_repair')",
        (ASKED_PRIORITY, key),
    )
    _settle(conn, job, HELD.get(status, "queued"), key)


def _entry_for(conn: psycopg.Connection, lemmas: list[str]) -> tuple[str, str] | None:
    row = conn.execute(
        "select key, status::text from entries where language = 'en'"
        " and lemma_norm = any(%s) and status <> 'retired' order by priority desc, key limit 1",
        (lemmas,),
    ).fetchone()
    return (row[0], row[1]) if row else None


def _facts(
    conn: psycopg.Connection, word: str, *, spanish: str | None = None, pos: str | None = None
) -> str:
    """The user turn: the word, and the open data's facts about it (hints, never prose). For
    a Spanish search, what the student typed, so the draft covers that meaning."""
    zipf = conn.execute(
        "select max(zipf) from ref_freq where language = 'en' and word = %s", (word,)
    ).fetchone()
    rows = conn.execute(
        "select pos, glosses from ref_wiktionary where word_norm = %s order by pos", (word,)
    ).fetchall()
    hints = list(dict.fromkeys(f"{pos}: {hint(gloss)}" for pos, glosses in rows
                               for gloss in glosses))[:MAX_HINTS]  # fmt: skip
    lines = [f"Query: {word}"]
    if spanish:
        lines.append(
            f"A student searched in Spanish for «{spanish}», and the English {pos or 'word'}"
            f" «{word}» is what they meant. One sense must have that meaning, and its"
            f" equivalents must include «{spanish}»."
        )
    lines += ["", "Facts:"]
    if zipf and zipf[0] is not None:
        lines.append(f"- frequency on the Zipf scale (0-8): {float(zipf[0]):.1f}")
    lines += [f"- Wiktionary hint, {line}" for line in hints] or ["- no Wiktionary entry"]
    return "\n".join(lines)


def _key(lemma: str, pos: str, kind: str) -> str | None:
    """The entry key, or None when the parts can't make one (a single word with pos phrase)."""
    try:
        return entry_key("en", " ".join(lemma.split()), KIND_POS.get(kind, pos), kind)
    except (KeyError, ValueError):
        return None


def _document(conn: psycopg.Connection, draft: Draft) -> dict[str, Any] | None:
    """The entry document for a draft, or None when the draft can't make a valid key."""
    if not (draft.lemma and draft.pos and draft.kind and draft.senses):
        return None
    key = _key(draft.lemma, draft.pos, draft.kind)
    if key is None:
        return None
    lemma = " ".join(draft.lemma.split())
    kind = draft.kind
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
        "pos": KIND_POS.get(kind, draft.pos),
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
