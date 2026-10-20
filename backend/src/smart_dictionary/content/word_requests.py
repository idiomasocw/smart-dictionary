"""Word requests (design §6.7): a real word nobody seeded waits for the owner's decision.

AI writes entries only for words the owner chose (ai.provisional). Any other word a student
searches for gets no entry; when it's a real headword, the student may request it:
- **Requestable:** Wiktionary has it as a headword (our import leaves misspellings out, so
  *recieve* never is), none of its words is blocked, and the owner hasn't rejected it, in the
  console or while curating the seeds. Typos and nonsense just get "not found".
- **One request per headword** gathers every student who asked: `word_request_votes` holds a
  user ID and the search they made, never a name. Each student has a daily number of requests.
- **The owner decides** in the console. Approving queues the word, approved and first in the
  factory's queue, and AI writes its Beta at once. Rejecting closes it for good, and retires
  any unapproved entry AI wrote for it before reviews existed (origin `ai_entry`).
"""

from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
from lemminflect import getAllLemmas
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from smart_dictionary.content.visibility import live
from smart_dictionary.core.entrydoc import KIND_POS
from smart_dictionary.core.ids import new_id
from smart_dictionary.core.keys import entry_key
from smart_dictionary.core.principal import Principal
from smart_dictionary.core.text import norm
from smart_dictionary.factory.queue import ASKED_PRIORITY
from smart_dictionary.factory.validate import BLOCKED

# Suggestions put the commonest parts of speech first.
POS_ORDER = ["noun", "verb", "adjective", "adverb", "phrase"]

_REQUESTABLE = """
select k.word, k.word_norm, k.pos, k.kind::text
from ref_wiktionary k
where k.word_norm = any({lemmas})
  and not exists (select from word_requests r
                  where r.lemma_norm = k.word_norm and r.status = 'rejected')
  and not exists (select from entries e
                  where e.language = 'en' and e.lemma_norm = k.word_norm
                    and e.status = 'retired')
  and not (exists (select from seed_candidates c
                   where c.lemma_norm = k.word_norm and c.decision in ('reject', 'merge'))
           and not exists (select from seed_candidates c
                           where c.lemma_norm = k.word_norm
                             and coalesce(c.decision, 'accept') = 'accept'))
"""
REQUESTABLE = text(_REQUESTABLE.format(lemmas="cast(:lemmas as text[])"))
REQUESTABLE_SYNC = _REQUESTABLE.format(lemmas="%(lemmas)s")

REQUEST_STATUS = text("""
select r.status,
       exists (select 1 from word_request_votes v join users u on u.id = v.user_id
               where v.request_id = r.id and u.tenant_id = :tenant
                 and u.external_id = :subject) as asked
from word_requests r where r.lemma_norm = :lemma
""")

VOTES_TODAY = text("""
select count(*) from word_request_votes
where user_id = :user and created_at > now() - interval '1 day'
""")
ASKED = text("select 1 from word_request_votes where request_id = :request and user_id = :user")
UPSERT = text("""
insert into word_requests (id, lemma, lemma_norm, pos, kind, origin)
values (:id, :lemma, :lemma_norm, :pos, cast(:kind as entry_kind), 'student')
on conflict (lemma_norm) do update set last_asked_at = now()
returning id, status
""")
VOTE = text("""
insert into word_request_votes (request_id, user_id, language, query)
values (:request, :user, :language, :query)
on conflict (request_id, user_id) do nothing
returning 1
""")


@dataclass(frozen=True)
class Candidate:
    """A headword a student may request, with the part of speech and kind Wiktionary gives
    it, the likeliest first."""

    lemma: str
    lemma_norm: str
    options: list[tuple[str, str]] = field(default_factory=list)  # (pos, kind)

    @property
    def pos(self) -> str | None:
        return self.options[0][0] if self.options else None

    @property
    def kind(self) -> str | None:
        return self.options[0][1] if self.options else None


# A leading article a headword is usually listed without: the last straw → last straw.
ARTICLES = ("a", "an", "the")


def headwords(query: str) -> list[str]:
    """The dictionary forms a normalized search may stand for, the search itself first:
    geese → goose, gave up → give up, the last straw → last straw."""
    words = query.split()
    if not words:
        return []
    lemmas = sorted({lemma for found in getAllLemmas(words[0]).values() for lemma in found})
    forms = [query, *(" ".join([lemma, *words[1:]]) for lemma in lemmas)]
    if len(words) > 2 and words[0] in ARTICLES:
        forms.append(" ".join(words[1:]))
    return list(dict.fromkeys(norm(form) for form in forms))


async def requestable(conn: AsyncConnection, lemmas: list[str]) -> Candidate | None:
    rows = (await conn.execute(REQUESTABLE, {"lemmas": lemmas})).all()
    return _candidate(lemmas, rows)


def requestable_sync(conn: psycopg.Connection, lemmas: list[str]) -> Candidate | None:
    rows = conn.execute(REQUESTABLE_SYNC, {"lemmas": lemmas}).fetchall()  # pyright: ignore[reportArgumentType]
    return _candidate(lemmas, rows)


async def status_for(
    conn: AsyncConnection, principal: Principal, candidate: Candidate
) -> Literal["requestable", "requested", "invalid"]:
    """What this student sees for a requestable word: `requested` once they've asked."""
    params = {"tenant": principal.tenant, "subject": principal.subject or "",
              "lemma": candidate.lemma_norm}  # fmt: skip
    row = (await conn.execute(REQUEST_STATUS, params)).first()
    if row is None:
        return "requestable"
    if row.status == "rejected":
        return "invalid"
    return "requested" if row.asked else "requestable"


async def vote(
    conn: AsyncConnection,
    user_id: Any,
    candidate: Candidate,
    *,
    language: str,
    query: str,
    per_day: int,
) -> Literal["requested", "quota_exceeded", "invalid"]:
    """Add this student's request for the headword; the caller commits, or rolls back when
    it's refused. Asking again for the same word costs nothing."""
    params = {"id": new_id(), "lemma": candidate.lemma, "lemma_norm": candidate.lemma_norm,
              "pos": candidate.pos, "kind": candidate.kind}  # fmt: skip
    request = (await conn.execute(UPSERT, params)).one()
    if request.status == "rejected":
        return "invalid"
    asked = await conn.execute(ASKED, {"request": request.id, "user": user_id})
    if asked.first() is not None:
        return "requested"
    used = (await conn.execute(VOTES_TODAY, {"user": user_id})).scalar_one()
    if used >= per_day:
        return "quota_exceeded"
    added = await conn.execute(
        VOTE, {"request": request.id, "user": user_id, "language": language, "query": query}
    )
    if added.first() is not None:
        await conn.execute(
            text("update word_requests set students = students + 1 where id = :id"),
            {"id": request.id},
        )
    return "requested"


# The owner's decisions (the admin API) -------------------------------------------------------


class Conflict(Exception):
    """The request can't take this decision in its current state."""


@dataclass(frozen=True)
class Approved:
    key: str
    write_now: bool  # nobody has written it: AI may write its Beta at once


def approve(
    conn: psycopg.Connection,
    request_id: Any,
    *,
    lemma: str,
    pos: str,
    kind: str,
    decided_by: str,
    note: str | None,
    tenant: str,
) -> Approved:
    """Approve the headword as `pos` and `kind`: its entry is queued (or kept, when one
    exists) and approved, first in the factory's queue. Other unapproved entries AI made for
    the same headword are retired. LookupError: no such request; ValueError: the headword or
    its part of speech can't make an entry; Conflict: decided already, or its key retired."""
    request = _locked(conn, request_id)
    if norm(lemma) != request["lemma_norm"]:
        raise ValueError(f"the headword must be {request['lemma']!r}, as requested")
    if request["status"] != "pending":
        raise Conflict(f"it was {request['status']} already")
    pos = KIND_POS.get(kind, pos)
    key = entry_key("en", lemma, pos, kind)
    existing = conn.execute(
        "select status::text, published_at is not null from entries where key = %s for update",
        (key,),
    ).fetchone()
    if existing is not None and existing[0] == "retired":
        raise Conflict(f"{key} was retired, and keys are never reused")
    if existing is None:
        queue_approved(conn, key, lemma, pos, kind, approved_by=decided_by, sources=["request"])
    else:
        conn.execute(
            "update entries set approved_at = coalesce(approved_at, now()),"
            " approved_by = coalesce(approved_by, %s), priority = greatest(priority, %s),"
            " updated_at = now() where key = %s",
            (decided_by, ASKED_PRIORITY, key),
        )
    conn.execute(
        "update entries set status = 'retired', updated_at = now()"
        " where language = 'en' and lemma_norm = %s and key <> %s and approved_at is null"
        " and status <> 'retired'",
        (request["lemma_norm"], key),
    )
    conn.execute(
        "update word_requests set status = 'approved', lemma = %s, pos = %s, kind = %s,"
        " entry_key = %s, decided_at = now(), decided_by = %s, note = %s where id = %s",
        (lemma, pos, kind, key, decided_by, note, request["id"]),
    )
    shown = existing is not None and existing[1]
    _reopen(conn, request, key, shown)
    write_now = not shown and (existing is None or existing[0] in ("candidate", "queued"))
    if write_now:
        conn.execute(
            "insert into misses (tenant_id, language, query_norm, hits) values (%s, 'en', %s, 0)"
            " on conflict (tenant_id, language, query_norm) do update set status = 'pending',"
            " attempts = 0, running_since = null, last_attempt_at = null, last_error = null",
            (tenant, norm(lemma)),
        )
    return Approved(key, write_now)


def queue_approved(
    conn: psycopg.Connection,
    key: str,
    lemma: str,
    pos: str,
    kind: str,
    *,
    approved_by: str,
    sources: list[str],
) -> None:
    """A new entry the owner chose, queued first in the factory's queue. Conflict: the same
    expression exists as another multiword kind (an idiom beside a phrase)."""
    twin = conn.execute(
        "select key from entries where language = 'en' and lemma_norm = %s and pos = %s"
        " and kind::text <> %s and kind <> 'word' and %s <> 'word' and status <> 'retired'",
        (norm(lemma), pos, kind, kind),
    ).fetchone()
    if twin is not None:
        raise Conflict(f"{twin[0]} is the same expression")
    zipf = conn.execute(
        "select max(zipf) from ref_freq where language = 'en' and word = %s", (norm(lemma),)
    ).fetchone()
    conn.execute(
        "insert into entries (id, key, language, lemma, lemma_norm, pos, kind, homograph,"
        " freq_zipf, priority, status, sources, approved_at, approved_by)"
        " values (%s, %s, 'en', %s, %s, %s, %s, 1, %s, %s, 'queued', %s, now(), %s)",
        (new_id(), key, lemma, norm(lemma), pos, kind, zipf[0] if zipf else None,
         ASKED_PRIORITY, sources, approved_by),
    )  # fmt: skip


def reject(
    conn: psycopg.Connection, request_id: Any, *, decided_by: str, note: str | None
) -> list[str]:
    """Close the headword: students can't request it again, and the unapproved entries AI
    made for it are retired, keys kept forever. Returns those keys. Conflict: approved."""
    request = _locked(conn, request_id)
    if request["status"] == "approved":
        raise Conflict("it was approved; its entry is in the factory")
    retired = [
        row[0]
        for row in conn.execute(
            "update entries set status = 'retired', updated_at = now()"
            " where language = 'en' and lemma_norm = %s and approved_at is null"
            " and status <> 'retired' returning key",
            (request["lemma_norm"],),
        )
    ]
    conn.execute(
        "update word_requests set status = 'rejected', decided_at = now(), decided_by = %s,"
        " note = %s where id = %s",
        (decided_by, note, request["id"]),
    )
    conn.execute(
        "update misses m set status = 'invalid', entry_key = null, headword = null,"
        " running_since = null"
        " where m.entry_key = any(%(keys)s) or m.entry_key = %(key)s"
        " or (m.status = 'requestable' and m.headword is not null"
        "     and lower(m.headword) = lower(%(lemma)s))"
        " or exists (select 1 from word_request_votes v where v.request_id = %(id)s"
        "            and v.language = m.language and v.query = m.query_norm)",
        {"keys": retired, "key": request["entry_key"], "lemma": request["lemma"],
         "id": request["id"]},
    )  # fmt: skip
    return retired


def listing(
    conn: psycopg.Connection,
    *,
    status: str | None,
    origin: str | None,
    q: str | None,
    page: int,
    per_page: int,
    request_id: Any = None,
) -> tuple[list[dict[str, Any]], int]:
    """One page of requests, the most asked first, with the evidence for each decision."""
    params = {"status": status, "origin": origin, "q": q, "id": request_id}
    total = conn.execute("select count(*)" + LIST_FILTER, params).fetchone()
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            LIST_COLUMNS
            + LIST_FILTER
            + " order by r.status = 'pending' desc, r.students desc, searches desc,"
            " r.last_asked_at desc limit %(limit)s offset %(offset)s",
            params | {"limit": per_page, "offset": (page - 1) * per_page},
        ).fetchall()
    for row in rows:
        row["options"] = _options(row.pop("wiktionary") or [])
    return rows, total[0] if total else 0


LIST_FILTER = """
from word_requests r
where (cast(%(status)s as text) is null or r.status = %(status)s)
  and (cast(%(origin)s as text) is null or r.origin = %(origin)s)
  and (cast(%(q)s as text) is null or starts_with(r.lemma_norm, %(q)s))
  and (cast(%(id)s as uuid) is null or r.id = %(id)s)
"""
LIST_COLUMNS = f"""
select r.id, r.lemma, r.pos, r.kind::text as kind, r.origin, r.status, r.entry_key, r.students,
       r.first_asked_at, r.last_asked_at, r.decided_at, r.decided_by, r.note,
       (select coalesce(sum(m.hits), 0) from misses m
        where m.entry_key = r.entry_key
           or exists (select 1 from word_request_votes v where v.request_id = r.id
                      and v.language = m.language and v.query = m.query_norm)) as searches,
       array(select distinct v.query from word_request_votes v where v.request_id = r.id
             limit 5) as queries,
       (select max(f.zipf)::float from ref_freq f
        where f.language = 'en' and f.word = r.lemma_norm) as zipf,
       array(select distinct l.dataset_id || coalesce(' ' || l.level, '') from ref_lists l
             where l.lemma_norm = r.lemma_norm) as lists,
       array(select distinct coalesce(c.decision, 'undecided') from seed_candidates c
             where c.lemma_norm = r.lemma_norm) as seed_decisions,
       array(select distinct k.pos || '/' || k.kind from ref_wiktionary k
             where k.word_norm = r.lemma_norm) as wiktionary,
       (select e.doc #>> '{{senses,0,definition}}' from entries e
        where e.key = r.entry_key) as definition,
       (select {live()} from entries e where e.key = r.entry_key) as shown
"""


def _locked(conn: psycopg.Connection, request_id: Any) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cursor:
        request = cursor.execute(
            "select id, lemma, lemma_norm, status, origin, entry_key from word_requests"
            " where id = %s for update",
            (request_id,),
        ).fetchone()
    if request is None:
        raise LookupError(request_id)
    return request


def _reopen(conn: psycopg.Connection, request: dict[str, Any], key: str, shown: bool) -> None:
    """The searches that led to the request find the entry: at once when it's shown already,
    or through AI's Beta at their next search."""
    conn.execute(
        "update misses m set status = %(status)s, entry_key = %(entry)s, headword = null,"
        " attempts = 0, running_since = null, last_attempt_at = null, last_error = null"
        " where m.entry_key = %(key)s or m.entry_key = %(old)s"
        " or (m.status = 'requestable' and m.headword is not null"
        "     and lower(m.headword) = lower(%(lemma)s))"
        " or exists (select 1 from word_request_votes v where v.request_id = %(id)s"
        "            and v.language = m.language and v.query = m.query_norm)",
        {"status": "provisional" if shown else "pending", "entry": key if shown else None,
         "key": key, "old": request["entry_key"], "lemma": request["lemma"],
         "id": request["id"]},
    )  # fmt: skip


def _candidate(lemmas: list[str], rows: Any) -> Candidate | None:
    for lemma in lemmas:
        if BLOCKED.intersection(lemma.split()):
            continue
        found = [row for row in rows if row[1] == lemma]
        if found:
            options = _options([f"{row[2]}/{row[3]}" for row in found])
            return Candidate(found[0][0], lemma, [(o["pos"], o["kind"]) for o in options])
    return None


def _options(pairs: list[str]) -> list[dict[str, str]]:
    """Wiktionary's part of speech and kind, as an entry would take them, likeliest first:
    a multiword kind before a plain word, then the commonest parts of speech."""
    options: dict[tuple[str, str], None] = {}
    for pair in pairs:
        pos, _, kind = pair.partition("/")
        options[(KIND_POS.get(kind, pos), kind)] = None

    def rank(option: tuple[str, str]) -> tuple[int, int, str]:
        pos, kind = option
        order = POS_ORDER.index(pos) if pos in POS_ORDER else len(POS_ORDER)
        return (kind == "word", order, pos)

    return [{"pos": pos, "kind": kind} for pos, kind in sorted(options, key=rank)]
