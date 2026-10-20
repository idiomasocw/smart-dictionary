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
- **Related words** an entry links to that the dictionary lacks wait here too (origin
  `related`, content.related), with the links that named them.
- **Approved at once** (auto_approve), when the dictionary has no entry for the word and its
  part of speech is clear: by the lists' rule (`auto:lists`, the `auto_approve_listed`
  setting) when a curated list or a seed names it; else as chosen by AI (`ai:related`,
  `auto_approve_related`) when an entry the owner chose links to it and it's common enough.
  Both are on by default, and the owner confirms or retires them later (confirm). Anything
  less clear waits for the owner.
- **One multiword expression, one entry:** approving or adding one is refused while an approved
  entry has the same expression under another kind, or with or without its leading article
  (`expression_of`, migration 0013). One headword of one kind under two parts of speech is
  two entries on purpose: *a few* as a determiner and as a pronoun.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
from lemminflect import getAllLemmas
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from smart_dictionary.content.visibility import live
from smart_dictionary.core import tunables
from smart_dictionary.core.entrydoc import KIND_POS
from smart_dictionary.core.ids import new_id
from smart_dictionary.core.keys import KIND_CODES, POS_CODES, entry_key, parse_key
from smart_dictionary.core.principal import Principal
from smart_dictionary.core.settings import get_settings
from smart_dictionary.core.text import norm
from smart_dictionary.factory.queue import ASKED_PRIORITY, easiest, priority
from smart_dictionary.factory.validate import BLOCKED

# Suggestions put the commonest parts of speech first.
POS_ORDER = ["noun", "verb", "adjective", "adverb", "phrase"]
# Each key code's part of speech and kind.
CODE_PARTS = {
    **{code: (pos, "word") for pos, code in POS_CODES.items()},
    **{code: (KIND_POS[kind], kind) for kind, code in KIND_CODES.items()},
}

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


# A leading article a headword may be listed with or without: the last straw, last straw.
ARTICLES = ("a", "an", "the")


def headwords(query: str) -> list[str]:
    """The dictionary forms a normalized search may stand for, the search itself first:
    geese → goose, gave up → give up, the last straw → last straw, last straw → the last
    straw. A leading article goes only while two words remain (a few stays a few)."""
    words = query.split()
    if not words:
        return []
    lemmas = sorted({lemma for found in getAllLemmas(words[0]).values() for lemma in found})
    forms = [query, *(" ".join([lemma, *words[1:]]) for lemma in lemmas)]
    if len(words) > 2 and words[0] in ARTICLES:
        forms.append(" ".join(words[1:]))
    elif len(words) > 1 and words[0] not in ARTICLES:
        forms += [f"the {query}", f"{'an' if query[0] in 'aeiou' else 'a'} {query}"]
    return list(dict.fromkeys(norm(form) for form in forms))


async def requestable(conn: AsyncConnection, lemmas: list[str]) -> Candidate | None:
    rows = (await conn.execute(REQUESTABLE, {"lemmas": lemmas})).all()
    return candidate(lemmas, rows)


def requestable_sync(conn: psycopg.Connection, lemmas: list[str]) -> Candidate | None:
    rows = conn.execute(REQUESTABLE_SYNC, {"lemmas": lemmas}).fetchall()  # pyright: ignore[reportArgumentType]
    return candidate(lemmas, rows)


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
    write_now: bool  # a student waits and nobody has written it: AI may write its Beta at once
    decided_by: str = ""


# An entry for the same multiword expression under another kind, or with or without its
# article. The same headword and kind under another part of speech isn't one (a few).
_TWIN = """
select key from entries
where language = 'en' and status <> 'retired' {approved}
  and expression_of(lemma_norm) = expression_of(%(lemma)s) and key <> %(key)s
  and strpos(%(lemma)s, ' ') > 0
  and (lemma_norm <> %(lemma)s or kind::text <> %(kind)s)
order by approved_at is null, key limit 1
"""
APPROVED_TWIN = _TWIN.format(approved="and approved_at is not null")
TWIN = _TWIN.format(approved="")


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
    asked: bool = True,
) -> Approved:
    """Approve the headword as `pos` and `kind`: its entry is queued (or kept, when one
    exists) and approved, first in the factory's queue. Other unapproved entries AI made for
    the same headword or expression are retired. `asked=False`: nobody waits for it (a
    related word), so it takes its natural place in the queue and no Beta is written now.
    LookupError: no such request; ValueError: the headword or its part of speech can't make
    an entry; Conflict: decided already, its key retired, or an approved twin exists."""
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
    params = {"lemma": request["lemma_norm"], "key": key, "kind": kind}
    twin = conn.execute(APPROVED_TWIN, params).fetchone()
    if twin is not None:
        raise Conflict(f"{twin[0]} is the same expression; retire one of them, or reject this")
    conn.execute(
        "update entries set status = 'retired', updated_at = now()"
        " where language = 'en' and expression_of(lemma_norm) = expression_of(%s)"
        " and key <> %s and approved_at is null and status <> 'retired'",
        (request["lemma_norm"], key),
    )
    if existing is None:
        sources = ["related" if request["origin"] == "related" else "request"]
        queue_approved(conn, key, lemma, pos, kind, approved_by=decided_by, sources=sources,
                       asked=asked)  # fmt: skip
    else:
        conn.execute(
            "update entries set approved_at = coalesce(approved_at, now()),"
            " approved_by = coalesce(approved_by, %s), priority = greatest(priority, %s),"
            " updated_at = now() where key = %s",
            (decided_by, ASKED_PRIORITY if asked else 0, key),
        )
    conn.execute(
        "update word_requests set status = 'approved', lemma = %s, pos = %s, kind = %s,"
        " entry_key = %s, decided_at = now(), decided_by = %s, note = %s where id = %s",
        (lemma, pos, kind, key, decided_by, note, request["id"]),
    )
    shown = existing is not None and existing[1]
    _reopen(conn, request, key, shown)
    write_now = asked and not shown and (existing is None or existing[0] in ("candidate", "queued"))
    if write_now:
        conn.execute(
            "insert into misses (tenant_id, language, query_norm, hits) values (%s, 'en', %s, 0)"
            " on conflict (tenant_id, language, query_norm) do update set status = 'pending',"
            " attempts = 0, running_since = null, last_attempt_at = null, last_error = null",
            (tenant, norm(lemma)),
        )
    return Approved(key, write_now, decided_by)


def queue_approved(
    conn: psycopg.Connection,
    key: str,
    lemma: str,
    pos: str,
    kind: str,
    *,
    approved_by: str,
    sources: list[str],
    asked: bool = True,
) -> None:
    """A new entry the owner chose, queued first in the factory's queue, or (`asked=False`)
    where its level and frequency put it. Conflict: the same multiword expression has an
    entry (an idiom beside a phrase, last straw beside the last straw)."""
    twin = conn.execute(TWIN, {"lemma": norm(lemma), "key": key, "kind": kind}).fetchone()
    if twin is not None:
        raise Conflict(f"{twin[0]} is the same expression")
    facts = conn.execute(
        "select (select max(zipf) from ref_freq where language = 'en' and word = %(w)s),"
        " array(select distinct level from ref_lists where lemma_norm = %(w)s"
        " and level is not null)",
        {"w": norm(lemma)},
    ).fetchone()
    zipf, levels = facts if facts else (None, [])
    rank = ASKED_PRIORITY if asked else priority(levels, None, zipf)
    entry_id = new_id()
    conn.execute(
        "insert into entries (id, key, language, lemma, lemma_norm, pos, kind, homograph, cefr,"
        " freq_zipf, priority, status, sources, approved_at, approved_by)"
        " values (%s, %s, 'en', %s, %s, %s, %s, 1, %s, %s, %s, 'queued', %s, now(), %s)",
        (entry_id, key, lemma, norm(lemma), pos, kind, easiest(levels), zipf, rank, sources,
         approved_by),
    )  # fmt: skip
    # A seed for the same word that was never queued (it waited for curation) is this entry's.
    conn.execute(
        "update seed_candidates set entry_id = %s, decision = 'accept', updated_at = now()"
        " where entry_id is null and lemma_norm = %s and pos = %s and kind = %s::entry_kind"
        " and homograph = 1 and coalesce(decision, 'accept') = 'accept'",
        (entry_id, norm(lemma), pos, kind),
    )


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


# Approved at once ---------------------------------------------------------------------------

LISTS_BY = "auto:lists"
AI_BY = "ai:related"
# A word chosen through a link must be this common on wordfreq's Zipf scale (3 is about once
# per million words), every word of it for a multiword one, so links can't bring in rarities.
MIN_LINKED_ZIPF = 3.0

# What decides an approval at once: the lists that name the headword (and the parts of speech
# the CEFR lists give it), its seeds, Wiktionary's parts of speech, any entry it has, how
# common its rarest word is, and the links to it from entries the owner chose (not AI).
EVIDENCE = """
select array(select distinct l.dataset_id from ref_lists l
             where l.lemma_norm = %(lemma)s order by 1) as lists,
       array(select distinct l.pos from ref_lists l
             where l.lemma_norm = %(lemma)s and l.pos is not null) as list_pos,
       exists (select from seed_candidates c where c.lemma_norm = %(lemma)s
               and coalesce(c.decision, 'accept') = 'accept') as seeded,
       array(select distinct c.pos || '/' || c.kind from seed_candidates c
             where c.lemma_norm = %(lemma)s and c.pos is not null and c.kind is not null
               and coalesce(c.decision, 'accept') = 'accept') as seeds,
       array(select distinct k.pos || '/' || k.kind from ref_wiktionary k
             where k.word_norm = %(lemma)s) as wiktionary,
       exists (select from entries e where e.language = 'en'
               and expression_of(e.lemma_norm) = expression_of(%(lemma)s)) as has_entry,
       (select min(coalesce((select max(f.zipf) from ref_freq f
                             where f.language = 'en' and f.word = w), 0))
        from unnest(string_to_array(%(lemma)s, ' ')) as w)::float as rarest,
       array(select l.entry_key || ' ' || l.type from word_request_links l
             join entries e on e.key = l.entry_key
             where l.request_id = %(id)s and e.approved_at is not null
               and left(coalesce(e.approved_by, ''), 3) <> 'ai:'
             order by l.created_at, l.entry_key) as owner_links,
       array(select distinct l.target_key from word_request_links l
             where l.request_id = %(id)s and l.target_key is not null) as targets
"""


def auto_approve(conn: psycopg.Connection, request_id: Any, *, tenant: str) -> Approved | None:
    """Approve a pending student or related request at once when one of the owner's rules
    allows it. Both need a word the dictionary has no entry for (any part of speech, retired
    included) with a clear part of speech and kind; then:
    - **the lists' rule** (`auto:lists`, setting `auto_approve_listed`): a curated list or a
      seed names it;
    - **chosen by AI** (`ai:related`, setting `auto_approve_related`): an entry the owner
      chose links to it (an entry AI chose doesn't count, so links can't chain), and its
      rarest word is at least MIN_LINKED_ZIPF.

    None: it waits for the owner. The caller commits; a refusal changes nothing."""
    request = _locked(conn, request_id)
    if request["status"] != "pending" or request["origin"] == "ai_entry":
        return None
    if BLOCKED.intersection(request["lemma_norm"].split()):
        return None
    with conn.cursor(row_factory=dict_row) as cursor:
        found = cursor.execute(
            EVIDENCE, {"lemma": request["lemma_norm"], "id": request["id"]}
        ).fetchone()
    if found is None or found["has_entry"]:
        return None
    hints = [hint for target in found["targets"] if (hint := link_hint(target))]
    clear = _clear_pos(found["seeds"], hints, found["list_pos"], found["wiktionary"])
    if clear is None:
        return None
    pos, kind, why = clear
    settings, overrides = get_settings(), tunables.overrides(conn)
    if (found["lists"] or found["seeded"]) and overrides.get(
        "auto_approve_listed", settings.auto_approve_listed
    ):
        named = ", ".join(found["lists"]) or "the seed lists"
        decided_by, note = LISTS_BY, f"auto: on {named}; {pos}/{kind} {why}"
    elif (
        found["owner_links"]
        and found["rarest"] >= MIN_LINKED_ZIPF
        and overrides.get("auto_approve_related", settings.auto_approve_related)
    ):
        decided_by = AI_BY
        note = (f"ai: linked from {found['owner_links'][0]}; {pos}/{kind} {why};"
                f" Zipf {found['rarest']:.1f}")  # fmt: skip
    else:
        return None
    try:
        with conn.transaction():  # a savepoint: a refusal leaves the request as it was
            return approve(conn, request_id, lemma=request["lemma"], pos=pos, kind=kind,
                           decided_by=decided_by, note=note, tenant=tenant,
                           asked=request["students"] > 0)  # fmt: skip
    except (Conflict, ValueError):
        return None


def confirm(conn: psycopg.Connection, request_ids: list[Any], *, decided_by: str) -> list[str]:
    """The owner checked these words approved at once and keeps them; returns their
    headwords. Others (pending, or decided by the owner) are left as they are."""
    return [
        row[0]
        for row in conn.execute(
            "update word_requests set confirmed_at = now(), confirmed_by = %s"
            " where id = any(%s) and status = 'approved' and confirmed_at is null"
            " and (left(decided_by, 5) = 'auto:' or left(decided_by, 3) = 'ai:')"
            " returning lemma",
            (decided_by, request_ids),
        )
    ]


def link_hint(key: str) -> tuple[str, str] | None:
    """The part of speech and kind an English entry key names: en:run_into:phrv → verb,
    phrasal_verb."""
    try:
        parsed = parse_key(key)
    except ValueError:
        return None
    if parsed.level != "entry" or parsed.language != "en":
        return None
    return CODE_PARTS[parsed.code]


def _clear_pos(
    seeds: list[str], hints: list[tuple[str, str]], list_pos: list[str], wiktionary: list[str]
) -> tuple[str, str, str] | None:
    """The part of speech and kind, with where they came from, when only one fits: the seed's,
    else the links' when Wiktionary agrees, else Wiktionary's one option under the part of
    speech the CEFR lists give, else Wiktionary's only option."""
    seeded = {pair for seed in seeds if (pair := _pair(seed)) is not None}
    if len(seeded) == 1:
        [(pos, kind)] = seeded
        return pos, kind, "from its seed"
    options = [(option["pos"], option["kind"]) for option in _options(wiktionary)]
    options = [option for option in options if _forms_entry(*option)]
    hinted = {hint for hint in hints if hint in options}
    if len(hinted) == 1:
        [(pos, kind)] = hinted
        return pos, kind, "from the link, as Wiktionary has it"
    listed = [option for option in options if option[0] in list_pos]
    if len(listed) == 1:
        return listed[0][0], listed[0][1], "from the lists and Wiktionary"
    if len(options) == 1:
        return options[0][0], options[0][1], "from Wiktionary, its only one"
    return None


def _pair(value: str) -> tuple[str, str] | None:
    pos, _, kind = value.partition("/")
    pos = KIND_POS.get(kind, pos)
    return (pos, kind) if _forms_entry(pos, kind) else None


def _forms_entry(pos: str, kind: str) -> bool:
    """The part of speech and kind can make an entry, as the seed queue requires."""
    if kind == "word":
        return pos != "phrase"
    return pos == ("verb" if kind == "phrasal_verb" else "phrase")


def listing(
    conn: psycopg.Connection,
    *,
    status: str | None,
    origin: str | None,
    q: str | None,
    page: int,
    per_page: int,
    request_id: Any = None,
    to_verify: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """One page of requests, the most asked first, with the evidence for each decision.
    `to_verify`: the words approved at once that the owner hasn't confirmed."""
    params = {"status": status, "origin": origin, "q": q, "id": request_id,
              "to_verify": to_verify}  # fmt: skip
    total = conn.execute("select count(*)" + LIST_FILTER, params).fetchone()
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            LIST_COLUMNS
            + LIST_FILTER
            + " order by r.status = 'pending' desc, r.students desc, searches desc,"
            " r.decided_at desc nulls last, r.last_asked_at desc"
            " limit %(limit)s offset %(offset)s",
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
  and (not %(to_verify)s or (r.status = 'approved' and r.confirmed_at is null
       and (left(r.decided_by, 5) = 'auto:' or left(r.decided_by, 3) = 'ai:')))
"""
LIST_COLUMNS = f"""
select r.id, r.lemma, r.pos, r.kind::text as kind, r.origin, r.status, r.entry_key, r.students,
       r.first_asked_at, r.last_asked_at, r.decided_at, r.decided_by, r.note, r.confirmed_at,
       r.confirmed_by,
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
       (select {live()} from entries e where e.key = r.entry_key) as shown,
       array(select e.key from entries e
             where e.language = 'en' and e.status <> 'retired'
               and expression_of(e.lemma_norm) = expression_of(r.lemma_norm)
               and e.key is distinct from r.entry_key
             order by e.key) as twins,
       array(select l.entry_key || ' ' || l.type from word_request_links l
             where l.request_id = r.id order by l.created_at, l.entry_key limit 10) as linked_from,
       (select e.audited_at from entries e where e.key = r.entry_key) as audited_at,
       (select count(*) from audit_findings f
        where f.entry_key = r.entry_key and f.status = 'open') as open_findings
"""


def _locked(conn: psycopg.Connection, request_id: Any) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cursor:
        request = cursor.execute(
            "select id, lemma, lemma_norm, status, origin, entry_key, students"
            " from word_requests where id = %s for update",
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


def candidate(lemmas: list[str], rows: Any) -> Candidate | None:
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
