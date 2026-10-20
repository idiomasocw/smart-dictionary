"""Related entries (design §3.5, §6.7): a card links only to entries students can open, and a
link to a word the dictionary lacks becomes a suggestion for the owner.

The routine links by key, and a link may name an entry that doesn't exist yet (factory/STYLE.md
§13) or that exists under another key: *by the way* is seeded as an idiom, so a link to
`en:by_the_way:phr` means that entry.
- **Cards** show a link when an entry students see stands for it: the key itself, else its
  word under the same part of speech (another homograph: `en:tv:n` opens `en:tv:n~2`), else,
  for a multiword expression, its entry of any kind, with or without the article. Other
  links stay in the document, hidden, until their entry is shown.
- **Publishing** turns each link to a word with no entry at all (retired ones count: the owner
  decided) into a word request of origin `related`, with the link as its evidence. Typos,
  blocked words and words the owner declined are skipped, as for students' requests. The
  auto-approval may approve it at once (word_requests.auto_approve); it then waits in its
  natural place in the queue, since no student is waiting.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from smart_dictionary.content import word_requests
from smart_dictionary.content.visibility import live
from smart_dictionary.core.entrydoc import KIND_POS
from smart_dictionary.core.ids import new_id
from smart_dictionary.core.keys import KIND_CODES, POS_CODES, parse_key
from smart_dictionary.core.text import norm

log = logging.getLogger(__name__)

# Each key code's part of speech and kind.
CODE_PARTS = {
    **{code: (pos, "word") for pos, code in POS_CODES.items()},
    **{code: (KIND_POS[kind], kind) for kind, code in KIND_CODES.items()},
}


@dataclass(frozen=True)
class Link:
    key: str
    lemma: str  # normalized, from the key's slug
    pos: str
    kind: str

    @property
    def base(self) -> str:
        """The key without its homograph number."""
        return self.key.split("~")[0]

    @property
    def multiword(self) -> bool:
        return " " in self.lemma


def link(key: str) -> Link | None:
    """What an English entry key names; None for anything else."""
    try:
        parsed = parse_key(key)
    except ValueError:
        return None
    if parsed.level != "entry" or parsed.language != "en":
        return None
    pos, kind = CODE_PARTS[parsed.code]
    return Link(key, norm(parsed.slug.replace("_", " ")), pos, kind)


# 1. Cards -------------------------------------------------------------------------------------

OPENS = text(f"""
select r.key, (
  select e.key from entries e
  where e.language = 'en' and {live()}
    and (e.key = r.key
         or (e.lemma_norm = r.lemma and split_part(e.key, '~', 1) = r.base)
         or (r.multiword and expression_of(e.lemma_norm) = expression_of(r.lemma)))
  order by e.key = r.key desc, split_part(e.key, '~', 1) = r.base desc, e.priority desc, e.key
  limit 1)
from unnest(cast(:keys as text[]), cast(:lemmas as text[]), cast(:bases as text[]),
            cast(:multiword as boolean[])) as r(key, lemma, base, multiword)
""")


async def opens(conn: AsyncConnection, doc: Mapping[str, Any]) -> dict[str, str]:
    """Each of the document's links that a student can open, with the key it opens."""
    links = [found for item in doc.get("related") or [] if (found := link(item["key"]))]
    if not links:
        return {}
    params = {
        "keys": [found.key for found in links],
        "lemmas": [found.lemma for found in links],
        "bases": [found.base for found in links],
        "multiword": [found.multiword for found in links],
    }
    rows = await conn.execute(OPENS, params)
    return {key: opened for key, opened in rows if opened is not None}


# 2. Suggestions, at publishing ------------------------------------------------------------------

# The headwords no entry stands for, under any part of speech or kind, retired included.
MISSING = """
select w from unnest(%s::text[]) as w
where not exists (select from entries e
                  where e.language = 'en' and expression_of(e.lemma_norm) = expression_of(w))
"""
ADD = """
insert into word_requests (id, lemma, lemma_norm, pos, kind, origin, students)
values (%(id)s, %(lemma)s, %(lemma_norm)s, %(pos)s, %(kind)s::entry_kind, 'related', 0)
on conflict (lemma_norm) do nothing
"""
LINKED = """
insert into word_request_links (request_id, entry_key, type)
select id, %(entry)s, %(type)s from word_requests where lemma_norm = %(lemma)s
  and status = 'pending'
on conflict do nothing
returning request_id
"""


@dataclass(frozen=True)
class Suggested:
    lemma: str
    request_id: Any
    approved: str | None  # the key, when approved at once


def suggest(
    conn: psycopg.Connection,
    entries: Iterable[tuple[str, list[dict[str, Any]]]],
    *,
    tenant: str,
) -> list[Suggested]:
    """Turn the links of each (entry key, its `related`) to words the dictionary lacks into
    requests, and approve each one the rule allows. The caller commits."""
    pending: list[tuple[str, str, Link]] = []
    for source, related in entries:
        for item in related:
            found = link(item["key"])
            if found is not None and found.lemma:
                pending.append((source, item["type"], found))
    if not pending:
        return []
    missing = {
        row[0] for row in conn.execute(MISSING, (sorted({found.lemma for _, _, found in pending}),))
    }
    if not missing:
        return []
    rows = conn.execute(
        word_requests.REQUESTABLE_SYNC,
        {"lemmas": sorted(missing)},  # pyright: ignore[reportArgumentType]
    ).fetchall()
    suggested: dict[str, Suggested] = {}
    for source, kind_of_link, found in pending:
        if found.lemma not in missing:
            continue
        candidate = word_requests.candidate([found.lemma], rows)
        if candidate is None:
            continue  # a typo, a blocked word, or one the owner declined
        hint = (found.pos, found.kind)
        pos, kind = hint if hint in candidate.options else (candidate.pos, candidate.kind)
        request = {"id": new_id(), "lemma": candidate.lemma, "lemma_norm": candidate.lemma_norm,
                   "pos": pos, "kind": kind}  # fmt: skip
        conn.execute(ADD, request)
        added = conn.execute(LINKED, {"entry": source, "type": kind_of_link,
                                      "lemma": candidate.lemma_norm}).fetchone()  # fmt: skip
        if added is None or candidate.lemma_norm in suggested:
            continue  # decided already, or this link was known
        approved = word_requests.auto_approve(conn, added[0], tenant=tenant, hint=hint)
        suggested[candidate.lemma_norm] = Suggested(
            candidate.lemma, added[0], approved.key if approved else None
        )
    return list(suggested.values())
