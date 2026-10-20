"""Missing words (design §6.5) [LK-10, CG-4]: AI writes a Beta entry only for a word the owner
chose; a real word nobody chose can be requested, for the owner to decide.

A lookup that finds nothing still answers at once, decided with SQL only (NF-1):
- **invalid:** over MAX_WORDS words, a word the reference data doesn't know, or one nobody
  chose that can't be requested (typos, nonsense and blocked words end here);
- **ready:** the word is shown already, under the form `entry_key`;
- **generating:** an approved word nobody has written yet. One AI job runs after the response
  and publishes a Beta entry; asking again a moment later finds it. The routine leases Beta
  entries first and replaces each with a full one, same key;
- **queued:** the factory is writing the word, or AI failed it MAX_ATTEMPTS times and it's
  first in the factory's queue;
- **requestable** or **requested:** a real headword nobody seeded (content.word_requests). No
  AI: the student may request `headword`, and the owner approves or rejects it;
- **quota_exceeded** and **unavailable:** the student's daily AI searches are used up; AI is
  off, or failed this word a moment ago (it tries again after COOL_DOWN).

**Approved words only.** The owner chose a word by seeding it (the curated lists, `seeds
queue`) or by approving a request. AI writes the entry for that row: its lemma, part of speech
and kind come from the seed, never from the model, so neither a typo nor an invented word can
become a headword.

**Searches in Spanish** (dir=l1, or dir=auto when every word reads as Spanish and the search
isn't an English headword, so *pie* or *actual* stay English) take one AI call, translate_query,
to name the English headword meant. That headword then follows the rules above. The student's
Spanish joins the entry's equivalents only when it's spelled as the dictionary spells it.

**One job at a time:** the lookup claims the miss (`running_since`) before it starts a job,
so other lookups answer "generating" without starting another; an advisory lock per query
backs it up. A claim older than RUNNING expires, so a crashed job can't hold a word.

**Failures** are counted on the miss: no provider answered, a draft that fails the factory's
checks, any error. A refusal (`unsuitable`) can't improve with retries, so it goes straight to
the factory.
"""

import logging
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from smart_dictionary.ai.gateway import Answer, Gateway, Task, Unavailable
from smart_dictionary.content import word_requests
from smart_dictionary.content.projections import english_forms
from smart_dictionary.content.publish import publish
from smart_dictionary.content.visibility import live
from smart_dictionary.core import tunables
from smart_dictionary.core.entrydoc import CEFR, KIND_POS, POS, EntryDoc, Kind
from smart_dictionary.core.keys import entry_key, example_key, sense_key
from smart_dictionary.core.principal import Principal
from smart_dictionary.core.settings import get_settings
from smart_dictionary.core.text import norm
from smart_dictionary.db.sync import connect
from smart_dictionary.factory.packs import MAX_HINTS, hint
from smart_dictionary.factory.queue import ASKED_PRIORITY
from smart_dictionary.factory.validate import (
    BLOCKED,
    Context,
    Entry,
    Issue,
    _clitic_stems,
    score,
    validate,
)
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
CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]
# Entry statuses AI may write a Beta for: approved, and nobody has written them yet.
REPLACEABLE = ("candidate", "queued")
# Spanish words of one letter, which the lexicon's two-letter minimum leaves out.
ONE_LETTER = frozenset("aeouy")

Language = Literal["en", "es"]
Status = Literal["generating", "ready", "queued", "invalid", "quota_exceeded", "unavailable",
                 "requestable", "requested"]  # fmt: skip


class ProvisionalStatus(BaseModel):
    """What a lookup that found nothing did about it (docs/03 §5.3)."""

    status: Status
    entry_key: str | None = None
    retry_after_ms: int | None = None
    headword: str | None = Field(default=None, description="requestable: the word to request")


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
    """The provisional_entry answer for a headword the owner chose: its senses, or a refusal
    when its main meaning can't be shown within the content rules."""

    verdict: Literal["entry", "unsuitable"]
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
    prompt_version="provisional_entry.v3",
    system=(PROMPTS / "provisional_entry.v3.md").read_text(encoding="utf-8"),
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
    typed: str = ""  # the query as typed: a Spanish one may become an equivalent
    variety: str = "es-419"
    key: str | None = None  # English: the approved entry to write


@dataclass(frozen=True)
class Target:
    """An entry a missed search may stand for."""

    key: str
    lemma: str
    pos: str
    kind: str
    status: str
    approved: bool
    shown: bool


@dataclass(frozen=True)
class Resolution:
    """What a missed search stands for, decided without AI."""

    outcome: Literal["invalid", "shown", "writable", "factory", "requestable", "translate"]
    key: str | None = None
    candidate: word_requests.Candidate | None = None


INVALID = Resolution("invalid")

# 1. Deciding, in the lookup ------------------------------------------------------------------

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

ENGLISH_HEADWORD = text("""
select exists (select 1 from ref_wiktionary where word_norm = any(cast(:lemmas as text[])))
    or exists (select 1 from entries where language = 'en' and status <> 'retired'
               and approved_at is not null and lemma_norm = any(cast(:lemmas as text[])))
""")

_TARGETS = """
select e.key, e.lemma, e.pos, e.kind::text, e.status::text, e.approved_at is not null,
       {shown}
from entries e
where e.language = 'en' and e.status <> 'retired'
  and (e.lemma_norm = any({lemmas}) or e.key = {key})
order by e.priority desc, e.key
"""
TARGETS = text(_TARGETS.format(shown=live(), lemmas="cast(:lemmas as text[])", key=":key"))
TARGETS_SYNC = _TARGETS.format(shown=live(), lemmas="%(lemmas)s", key="%(key)s")

MISS = text("""
select status, entry_key, headword, attempts,
       coalesce(running_since > now() - cast(:running as interval), false) as running,
       coalesce(last_attempt_at > now() - cast(:cool_down as interval), false) as cooling
from misses
where tenant_id = :tenant and language = :language and query_norm = :q
""")

# The lookup that starts a job claims the miss. Two lookups at once: the second waits on the
# row, sees the first one's claim, and starts nothing.
CLAIM = text("""
update misses set running_since = now(), status = 'pending'
where tenant_id = :tenant and language = :language and query_norm = :q
  and attempts < :max_attempts
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


async def resolve(
    conn: AsyncConnection, query: Query, language: Language, tenant: str
) -> Resolution:
    """What a missed search stands for, in the language searched_in() routed it to."""
    q = query.norm_l1 if language == "es" else query.norm_en
    words = q.split()
    if not words or len(words) > MAX_WORDS:
        return INVALID
    params = {"words": words, "zipf": MIN_ZIPF, "language": language}
    if not {row[0] for row in await conn.execute(KNOWN, params)}.issuperset(words):
        return INVALID
    if language == "en":
        lemmas = word_requests.headwords(q)
        # A word the owner chose is written even when it's blocked (kill, drunk): the blocklist
        # keeps such words out of requests and examples, not out of the dictionary.
        found = _choose(await _targets(conn, lemmas, None))
        if found is not None:
            return found
        candidate = await word_requests.requestable(conn, lemmas)
        return Resolution("requestable", candidate=candidate) if candidate else INVALID
    if BLOCKED.intersection(words):
        return INVALID  # before spending a translation on it

    # Spanish: what the translation found last time, checked again, since the entry or the
    # owner's decision may have changed.
    params = {"tenant": tenant, "language": "es", "q": q}
    miss = (await conn.execute(MISS, params | TIMING)).first()
    if miss is None or miss.status == "pending":
        return Resolution("translate")
    if miss.status == "invalid":
        return INVALID
    lemmas = word_requests.headwords(norm(miss.headword)) if miss.headword else []
    found = _choose(await _targets(conn, lemmas, miss.entry_key))
    if found is not None:
        return found
    if miss.status == "requestable":
        candidate = await word_requests.requestable(conn, lemmas)
        return Resolution("requestable", candidate=candidate) if candidate else INVALID
    return Resolution("translate")


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
    found = await resolve(conn, query, language, principal.tenant)
    if found.outcome == "invalid":
        return ProvisionalStatus(status="invalid"), None
    if found.outcome == "shown":
        return ProvisionalStatus(status="ready", entry_key=found.key), None
    if found.outcome == "factory":
        return ProvisionalStatus(status="queued", entry_key=found.key), None
    if found.outcome == "requestable":
        assert found.candidate is not None
        status = await word_requests.status_for(conn, principal, found.candidate)
        headword = found.candidate.lemma if status != "invalid" else None
        return ProvisionalStatus(status=status, headword=headword), None
    return await _start(conn, principal, query, gateway, language, found.key)


async def _start(
    conn: AsyncConnection,
    principal: Principal,
    query: Query,
    gateway: Gateway,
    language: Language,
    key: str | None,
) -> tuple[ProvisionalStatus, Job | None]:
    """Start one AI job for the miss, unless one is at work, it failed a moment ago, AI is
    off or the student has no searches left today."""
    q = query.norm_l1 if language == "es" else query.norm_en
    waiting = ProvisionalStatus(status="queued", entry_key=key) if key else None
    overrides = await tunables.overrides_async(conn)
    if not gateway.chain(overrides):
        return waiting or ProvisionalStatus(status="unavailable"), None
    params = {"tenant": principal.tenant, "language": language, "q": q}
    miss = (await conn.execute(MISS, params | TIMING)).first()
    generating = ProvisionalStatus(status="generating", retry_after_ms=RETRY_AFTER_MS)
    if miss is not None and miss.running:
        return generating, None  # a job is at work: no second one
    if miss is not None and miss.attempts >= MAX_ATTEMPTS:
        return waiting or ProvisionalStatus(status="unavailable"), None  # the factory's now
    if miss is not None and miss.cooling:
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
              typed=query.raw, variety=query.variety, key=key)  # fmt: skip
    return generating, job


async def searched_in(conn: AsyncConnection, query: Query) -> Language | None:
    """The language a missed search was made in, which picks its path: Spanish for a search
    in Spanish (dir=l1), or a dir=auto one whose every word is known in Spanish and at least as
    common there as in English, unless it's an English headword (*pie*, *actual*: this is an
    English dictionary, and dir=l1 asks for the Spanish reading). English for the rest. None
    for a search in another student language, which has no path yet."""
    spanish_student = query.language == "es"
    if query.direction == "l1":
        return "es" if spanish_student else None
    words = query.norm_l1.split()
    if query.direction == "auto" and spanish_student and words:
        rows = (await conn.execute(LANGUAGES, {"words": words})).all()
        if all(float(es) >= MIN_ZIPF and float(es) >= float(en) for es, en in rows):
            lemmas = word_requests.headwords(query.norm_en)
            if not (await conn.execute(ENGLISH_HEADWORD, {"lemmas": lemmas})).scalar_one():
                return "es"
    return "en"


async def _targets(conn: AsyncConnection, lemmas: list[str], key: str | None) -> list[Target]:
    rows = await conn.execute(TARGETS, {"lemmas": lemmas, "key": key})
    return [Target(*row) for row in rows]


def _choose(targets: list[Target]) -> Resolution | None:
    """The best of the entries a search may stand for: one shown already, then an approved one
    nobody has written, then one the factory is on. None when the owner chose none of them."""
    shown = next((target for target in targets if target.shown), None)
    if shown is not None:
        return Resolution("shown", shown.key)
    approved = [target for target in targets if target.approved]
    writable = next((target for target in approved if target.status in REPLACEABLE), None)
    if writable is not None:
        return Resolution("writable", writable.key)
    if approved:
        return Resolution("factory", approved[0].key)
    return None


# 2. The work, after the response -------------------------------------------------------------


class NoEntry(Exception):
    """The job ends without an entry. `key` is the approved entry it was writing, if known;
    `final` says retrying can't help, so the factory takes the word at once."""

    def __init__(self, reason: str, key: str | None = None, *, final: bool = False) -> None:
        super().__init__(reason)
        self.key = key
        self.final = final


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
                _fail(conn, job, str(failure), failure.key, final=failure.final)
                conn.commit()
    except Exception as error:
        log.exception("the provisional entry for %r failed", job.query)
        _fail_alone(job, f"error: {type(error).__name__}: {error}"[:300])


def _english(conn: psycopg.Connection, job: Job, gateway: Gateway) -> None:
    target = _target(conn, job.key) if job.key else None
    if target is None or not target.approved:
        _settle(conn, job, "pending", None)  # unapproved since the lookup: nothing to write
        conn.commit()
        return
    if target.shown or target.status not in REPLACEABLE:
        _settle(conn, job, "published" if target.shown else "queued", target.key)
        conn.commit()
        return
    answer = _ask(conn, gateway, PROVISIONAL, _facts(conn, target), job, target.key)
    _check_and_publish(conn, job, gateway, answer, target, target.key, spanish=None)


def _spanish(conn: psycopg.Connection, job: Job, gateway: Gateway) -> None:
    question = f"Spanish query: {job.typed or job.query}\nThe student's Spanish: {job.variety}"
    translated_key = f"{job.variety}:{job.query}"
    translation = _ask(conn, gateway, TRANSLATE, question, job, translated_key)
    gateway.remember(conn, TRANSLATE, translated_key, translation)
    conn.commit()
    choices = _choices(translation.value)
    for headword, key in choices:
        lemmas = word_requests.headwords(norm(headword.lemma))
        found = _choose(_targets_sync(conn, lemmas, key))
        if found is None:
            continue
        assert found.key is not None
        if found.outcome != "writable":
            _settle(conn, job, "published" if found.outcome == "shown" else "queued", found.key)
            conn.commit()
            return
        target = _target(conn, found.key)
        assert target is not None
        spanish = job.typed or job.query
        spelled = _spelled_right(conn, job.query)
        facts = _facts(conn, target, spanish=spanish, spelled_right=spelled)
        cache_key = f"{target.key}\nfor es:{job.query}"
        draft = _ask(conn, gateway, PROVISIONAL, facts, job, cache_key)
        _check_and_publish(conn, job, gateway, draft, target, cache_key,
                           spanish=spanish if spelled else None)  # fmt: skip
        return
    # Nobody chose any of them: the first a student may request.
    for headword, key in choices:
        candidate = word_requests.requestable_sync(
            conn, word_requests.headwords(norm(headword.lemma))
        )
        if candidate is not None:
            _settle(conn, job, "requestable", key, headword=candidate.lemma)
            conn.commit()
            return
    _settle(conn, job, "invalid", None)
    conn.commit()


def _choices(translation: Translation) -> list[tuple[Headword, str]]:
    """The headwords that make a valid key, best first."""
    if translation.verdict != "found":
        return []
    found = []
    for headword in translation.headwords:
        key = _key(headword.lemma, headword.pos, headword.kind)
        if key is not None:
            found.append((headword, key))
    return found


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
        raise NoEntry(f"no AI provider answered {task.name}", job.key) from None
    conn.commit()
    return answer


def _check_and_publish(
    conn: psycopg.Connection,
    job: Job,
    gateway: Gateway,
    answer: Answer[Draft],
    target: Target,
    cache_key: str,
    *,
    spanish: str | None,
) -> None:
    draft = answer.value
    if draft.verdict != "entry" or not draft.senses:
        raise NoEntry("the model turned the headword down as unsuitable", target.key, final=True)
    if spanish:
        _with_equivalent(draft, spanish, job.query)
    raw, issues = _checked(conn, target, draft)
    errors = [issue for issue in issues if issue.severity == "error"]
    dropped: list[str] = []
    passing = _passing_senses(draft, errors, target.key)
    if passing is not None:
        # Only some senses failed: a short entry with the others beats no entry at all.
        dropped = [f"{i.check} {i.where}: {i.message}" for i in errors]
        draft = Draft(verdict=draft.verdict, senses=passing)
        raw, issues = _checked(conn, target, draft)
        errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        found = "; ".join(f"{i.check} {i.where}: {i.message}" for i in errors)
        raise NoEntry(f"the draft failed its checks: {found}"[:300], target.key)
    doc = EntryDoc.model_validate(raw)
    forms = english_forms(doc)
    if job.language == "en" and not any(f in forms for f in word_requests.headwords(job.query)):
        raise NoEntry(f"{job.query!r} isn't a form of {doc.lemma!r}", target.key, final=True)
    current = _target(conn, target.key, lock=True)
    if current is None or not current.approved:
        _settle(conn, job, "pending", None)
        conn.commit()
        return
    if current.shown or current.status not in REPLACEABLE:
        # The factory got there first: point to it.
        _settle(conn, job, "published" if current.shown else "queued", current.key)
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
    qa = {"issues": [asdict(i) for i in issues], "dropped_senses": dropped}
    publish(conn, doc, produced_by=produced_by, qa=qa, quality=score(issues, None),
            status="provisional")  # fmt: skip
    _settle(conn, job, "provisional", doc.key)
    gateway.remember(conn, PROVISIONAL, cache_key, answer)
    conn.commit()


def _checked(
    conn: psycopg.Connection, target: Target, draft: Draft
) -> tuple[dict[str, Any], list[Issue]]:
    raw = _document(conn, target, draft)
    entry = Entry(key=target.key, lemma=target.lemma, pos=target.pos, kind=target.kind)
    return raw, validate(entry, raw, Context.load(conn, [entry], [raw]), provisional=True)


def _passing_senses(draft: Draft, errors: list[Issue], key: str) -> list[DraftSense] | None:
    """The draft's senses with no error, when every error lies inside a sense and at least one
    sense is clean; None otherwise (no errors, or one about the entry as a whole)."""
    failing: set[int] = set()
    for issue in errors:
        number = _sense_number(issue.where, key)
        if number is None:
            return None
        failing.add(number)
    passing = [sense for number, sense in enumerate(draft.senses, start=1)
               if number not in failing]  # fmt: skip
    return passing if failing and passing else None


def _sense_number(where: str, key: str) -> int | None:
    """2 for en:x:n#2 or en:x:n#2.ex1; None for anything else."""
    if not where.startswith(f"{key}#"):
        return None
    number = where.removeprefix(f"{key}#").split(".")[0]
    return int(number) if number.isdigit() else None


def _with_equivalent(draft: Draft, spanish: str, query: str) -> None:
    """Add the student's Spanish to the equivalents, so their next search finds the entry
    directly. Only called when it's spelled as the dictionary spells it."""
    if any(norm(equivalent.term, "es") == query
           for sense in draft.senses for equivalent in sense.equivalents):  # fmt: skip
        return
    if len(draft.senses[0].equivalents) < 3:
        term = " ".join(spanish.split()).lower()
        draft.senses[0].equivalents.append(DraftEquivalent(term=term, match="near"))


def _spelled_right(conn: psycopg.Connection, query: str) -> bool:
    """Every word of a normalized Spanish search is a dictionary word: in Wiktionary's
    translations or UniMorph, by itself or under attached pronouns. The web-frequency list
    isn't enough, since it holds common typos (aveces, xq, hize)."""
    words = [word for word in query.split() if word not in ONE_LETTER]
    if not words:
        return True
    forms = sorted({form for word in words for form in [word, *_clitic_stems(word)]})
    known = {
        row[0]
        for row in conn.execute(
            "select w from unnest(%s::text[]) as w"
            " where exists (select 1 from ref_spanish_words s where s.word = w)"
            " or exists (select 1 from ref_forms o where o.language = 'es' and o.form_norm = w)",
            (forms,),
        )
    }
    return all(word in known or set(_clitic_stems(word)) & known for word in words)


def _target(conn: psycopg.Connection, key: str, *, lock: bool = False) -> Target | None:
    query = (
        f"select key, lemma, pos, kind::text, status::text, approved_at is not null, {live()}"
        " from entries e where key = %s and status <> 'retired'"
    )
    row = conn.execute(query + " for update" if lock else query, (key,)).fetchone()
    return Target(*row) if row else None


def _targets_sync(conn: psycopg.Connection, lemmas: list[str], key: str | None) -> list[Target]:
    rows = conn.execute(TARGETS_SYNC, {"lemmas": lemmas, "key": key})  # pyright: ignore[reportArgumentType]
    return [Target(*row) for row in rows]


def _pending(conn: psycopg.Connection, job: Job) -> bool:
    row = conn.execute(
        "select status from misses where tenant_id = %s and language = %s and query_norm = %s",
        (job.tenant, job.language, job.query),
    ).fetchone()
    return row is not None and row[0] == "pending"


def _settle(
    conn: psycopg.Connection,
    job: Job,
    status: str,
    key: str | None,
    *,
    headword: str | None = None,
) -> None:
    conn.execute(
        "update misses set status = %s, entry_key = %s, headword = %s, running_since = null"
        " where tenant_id = %s and language = %s and query_norm = %s",
        (status, key, headword, job.tenant, job.language, job.query),
    )


def _fail(
    conn: psycopg.Connection, job: Job, reason: str, key: str | None, *, final: bool = False
) -> None:
    """Count a failed attempt and clear the claim; after the last one, or a final failure, the
    factory takes the word."""
    row = conn.execute(
        "update misses set attempts = case when %s then %s else attempts + 1 end,"
        " last_attempt_at = now(), last_error = %s, running_since = null"
        " where tenant_id = %s and language = %s and query_norm = %s returning attempts",
        (final, MAX_ATTEMPTS, reason, job.tenant, job.language, job.query),
    ).fetchone()
    if row is not None and row[0] >= MAX_ATTEMPTS and key:
        _to_the_factory(conn, job, key)


def _fail_alone(job: Job, reason: str) -> None:
    """Record a failure after an error, on a fresh connection: the job's may be broken."""
    try:
        with connect() as conn:
            _fail(conn, job, reason, job.key)
    except Exception:
        log.exception("couldn't record the failure for %r", job.query)


def _to_the_factory(conn: psycopg.Connection, job: Job, key: str) -> None:
    """AI couldn't write the approved word, so the routine writes it next: its entry moves to
    the front of the queue, and the miss points to it ("queued")."""
    conn.execute(
        "update entries set priority = greatest(priority, %s) where key = %s"
        " and approved_at is not null and status in ('candidate', 'queued', 'needs_repair')",
        (ASKED_PRIORITY, key),
    )
    _settle(conn, job, "queued", key)


def _facts(
    conn: psycopg.Connection,
    target: Target,
    *,
    spanish: str | None = None,
    spelled_right: bool = True,
) -> str:
    """The user turn: the headword as seeded, and the open data's facts about it (hints, never
    prose). For a Spanish search, what the student typed, so the draft covers that meaning."""
    word = norm(target.lemma)
    zipf = conn.execute(
        "select max(zipf) from ref_freq where language = 'en' and word = %s", (word,)
    ).fetchone()
    rows = conn.execute(
        "select pos, glosses from ref_wiktionary where word_norm = %s order by pos", (word,)
    ).fetchall()
    hints = list(dict.fromkeys(f"{pos}: {hint(gloss)}" for pos, glosses in rows
                               for gloss in glosses))[:MAX_HINTS]  # fmt: skip
    lines = [f"Headword: {target.lemma}", f"Part of speech: {target.pos}", f"Kind: {target.kind}"]
    if spanish and spelled_right:
        lines.append(
            f"A student searched in Spanish for «{spanish}», and this headword is what they"
            f" meant. One sense must have that meaning, and its equivalents must include"
            f" «{spanish}»."
        )
    elif spanish:
        lines.append(
            f"A student searched in Spanish for «{spanish}», maybe misspelled, and this headword"
            " is what they meant. One sense must have that meaning. Spell every Spanish word"
            " correctly, and never copy a misspelling."
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


def _document(conn: psycopg.Connection, target: Target, draft: Draft) -> dict[str, Any]:
    """The entry document: the seed's key and headword, the draft's senses."""
    senses = []
    for number, sense in enumerate(draft.senses, start=1):
        key_n = sense_key(target.key, number)
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
        "key": target.key,
        "language": "en",
        "lemma": target.lemma,
        "pos": target.pos,
        "kind": target.kind,
        "cefr": min((sense.cefr for sense in draft.senses), key=CEFR_ORDER.index),
        "pronunciations": _pronunciations(conn, target.lemma) if target.kind == "word" else [],
        "senses": senses,
        "provisional": True,
    }
    if target.kind != "word":
        first, *rest = target.lemma.split()
        doc["pattern"] = {"tokens": [{"lemma": first}, *({"text": word} for word in rest)]}
    return doc


def _pronunciations(conn: psycopg.Connection, lemma: str) -> list[dict[str, str]]:
    """The reference IPA (CMUdict, Britfone), first per variety, so V7 has nothing to flag."""
    found: dict[str, str] = {}
    with conn.cursor(row_factory=dict_row) as cursor:
        for row in cursor.execute(
            "select variety, ipa from ref_ipa where word_norm = %s order by id", (norm(lemma),)
        ):
            found.setdefault(row["variety"], row["ipa"])
    return [{"variety": variety, "ipa": found[variety]}
            for variety in ("en-US", "en-GB") if variety in found]  # fmt: skip
