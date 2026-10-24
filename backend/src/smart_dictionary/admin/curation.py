"""The owner's hands on single entries (design §5.5, §3.6, §6.4): add a word, retire one,
requeue a quarantined one, and overrule the AI that judged one. Each is audited by the route
that calls it.

- **Add:** a word the owner wants, queued approved and first in the factory's queue. A pending
  request for it is approved with it.
- **Retire:** a duplicate or an unwanted word leaves the dictionary; its key is kept forever,
  and searches that pointed to it answer "not found". Not while the routine is writing it.
- **Requeue:** a quarantined entry, after the cause was fixed (a check, the style guide),
  gets its attempts back and returns to the queue: as a Beta rewrite when it has one.
- **Publish the draft:** the owner disagrees with the factory's reviewer and publishes the
  draft it turned down. The owner overrules the review, never the server's checks: a draft
  with an error still can't publish.
- **Set an idiom's or a phrase's function** (`set_pos`): its key holds its kind, not its
  function, so the part of speech changes in place, in the written document too.
- **Send to repair with the owner's notes:** the owner's own critique, for a published entry,
  a draft, or a word nobody has written yet. The notes start with "owner:" and win over a
  reviewer's or an audit's, in the factory and in the audit.
"""

from dataclasses import asdict
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from smart_dictionary.admin.browse import DRAFT
from smart_dictionary.content import word_requests
from smart_dictionary.content.publish import publish
from smart_dictionary.content.word_requests import Conflict
from smart_dictionary.core import headwords
from smart_dictionary.core.entrydoc import KIND_POS, EntryDoc, valid_pos
from smart_dictionary.core.ids import new_id
from smart_dictionary.core.keys import entry_key
from smart_dictionary.core.text import norm
from smart_dictionary.factory import submit


class Failing(Exception):
    """The draft fails the server's checks, which no decision overrules."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def add(
    conn: psycopg.Connection,
    *,
    lemma: str,
    pos: str,
    kind: str,
    decided_by: str,
    note: str | None,
    tenant: str,
) -> word_requests.Approved:
    """ValueError: the parts can't make an entry, or the headword breaks the rules (a slot at
    its end, a slot spelled another way); Conflict: it exists, or a twin does."""
    pos = KIND_POS.get(kind, pos)
    broken = headwords.problems(lemma, pos)
    if broken:
        raise ValueError("; ".join(broken))
    key = entry_key("en", lemma, pos, kind)
    existing = conn.execute("select status::text from entries where key = %s", (key,)).fetchone()
    if existing is not None:
        raise Conflict(f"{key} exists already ({existing[0]})")
    request = conn.execute(
        "select id from word_requests where lemma_norm = %s and status = 'pending'",
        (norm(lemma),),
    ).fetchone()
    if request is not None:
        return word_requests.approve(conn, request[0], lemma=lemma, pos=pos, kind=kind,
                                     decided_by=decided_by, note=note, tenant=tenant)  # fmt: skip
    word_requests.queue_approved(conn, key, lemma, pos, kind, approved_by=decided_by,
                                 sources=["owner:console"])  # fmt: skip
    return word_requests.Approved(key, write_now=False, decided_by=decided_by)


def retire(conn: psycopg.Connection, key: str) -> str:
    """Returns the status it had. LookupError: no such entry; Conflict: retired already, or
    the routine is writing it."""
    row = _locked(conn, key)
    if row["status"] == "retired":
        raise Conflict("it was retired already")
    if row["status"] == "generating":
        raise Conflict("the routine is writing it now; retire it after the run")
    conn.execute("update entries set status = 'retired', updated_at = now() where key = %s", (key,))
    conn.execute(
        "update misses set status = 'invalid', entry_key = null, headword = null,"
        " running_since = null where entry_key = %s",
        (key,),
    )
    conn.execute(
        "update word_requests set status = 'rejected', decided_at = now(),"
        " note = coalesce(note, 'retired in the console') where entry_key = %s"
        " and status = 'pending'",
        (key,),
    )
    return row["status"]


def requeue(conn: psycopg.Connection, key: str) -> str:
    """Returns the status it goes back to. LookupError: no such entry; Conflict: it isn't
    quarantined."""
    row = _locked(conn, key)
    if row["status"] != "quarantined":
        raise Conflict(f"it's {row['status']}; only a quarantined entry can be requeued")
    if not row["published"]:
        status = "queued"
    elif row["provisional"]:
        status = "provisional"  # its Beta stays shown, and the rewrite is leased first
    else:
        status = "needs_repair"
    conn.execute(
        "update entries set status = %s::entry_status, attempts = 0, updated_at = now()"
        " where key = %s",
        (status, key),
    )
    return status


def publish_draft(conn: psycopg.Connection, key: str, *, decided_by: str, note: str | None) -> str:
    """Publish the newest draft the routine wrote and the server sent back, overruling its
    review (the server's checks still apply). Returns the status it had. LookupError: no such
    entry; Conflict: no draft waits, or it's retired or being written; Failing: the draft
    has errors."""
    row = _locked(conn, key)
    if row["status"] in ("retired", "generating"):
        raise Conflict(f"it's {row['status']}")
    draft = conn.execute(DRAFT, (row["id"],)).fetchone()
    if draft is None:
        raise Conflict("no draft waits: nothing the routine wrote was sent back")
    [checked] = submit.check(conn, [(key, draft[0])])
    if checked.errors:
        raise Failing([f"{issue.check} {issue.where}: {issue.message}" for issue in checked.errors])
    doc = EntryDoc.model_validate(draft[0])
    produced_by = {"source": "owner", "decided_by": decided_by, "overruled": "review",
                   "draft_at": draft[1].isoformat()}  # fmt: skip
    qa = {"errors": [], "warnings": [asdict(issue) for issue in checked.warnings],
          "score": checked.score, "owner": {"decision": "published the draft over its review",
                                            "note": note}}  # fmt: skip
    publish(conn, doc, produced_by=produced_by, qa=qa, quality=checked.score)
    submit.suggest_related(conn, [(doc.key, [link.model_dump() for link in doc.related])])
    return row["status"]


def send_to_repair(
    conn: psycopg.Connection, key: str, *, notes: list[str], replace: bool, decided_by: str
) -> str:
    """Send the entry to the factory with the owner's notes. The writer starts from the draft
    the server sent back when there is one, else from the published document, else from
    nothing (a word nobody wrote: the notes guide its first writing). The published document
    stays shown meanwhile. `replace` drops the earlier notes (the reviewer's, an audit's), so
    only the owner's guide the writer. Returns the status it had. LookupError: no such entry;
    Conflict: retired, or the routine is writing it."""
    row = _locked(conn, key)
    if row["status"] in ("retired", "generating"):
        raise Conflict(f"it's {row['status']}; send it to repair after the routine's run"
                       if row["status"] == "generating" else "it's retired")  # fmt: skip
    draft = conn.execute(DRAFT, (row["id"],)).fetchone()
    doc = draft[0] if draft else (row["doc"] or {})
    earlier: dict[str, Any] = {}
    if row["status"] == "needs_repair" and not replace:
        found = conn.execute(
            "select qa from entry_revisions where entry_id = %s and outcome = 'needs_repair'"
            " order by created_at desc limit 1",
            (row["id"],),
        ).fetchone()
        earlier = found[0] if found else {}
    qa = {
        "errors": list(earlier.get("errors") or []),
        "warnings": list(earlier.get("warnings") or []),
        "review": {"verdict": "fail", "notes": [f"owner: {text}" for text in notes]
                   + list((earlier.get("review") or {}).get("notes") or [])},
        "audit": list(earlier.get("audit") or []),
        "owner": {"notes": notes, "by": decided_by, "replaced_earlier_notes": replace},
    }  # fmt: skip
    conn.execute(
        "insert into entry_revisions (id, entry_id, doc, produced_by, qa, outcome)"
        " values (%s, %s, %s, %s, %s, 'needs_repair')",
        (new_id(), row["id"], Jsonb(doc), Jsonb({"source": "owner", "decided_by": decided_by}),
         Jsonb(qa)),
    )  # fmt: skip
    conn.execute(
        "update entries set status = 'needs_repair', attempts = 0, updated_at = now()"
        " where id = %s",
        (row["id"],),
    )
    return row["status"]


def set_pos(conn: psycopg.Connection, key: str, pos: str, *, by: str, reason: str) -> str:
    """Give an idiom or a phrase its function (as long as: conjunction) in place: its key holds
    its kind, not its part of speech, so nothing else moves. A written document gets it too,
    as a revision: the server owns that fact (STYLE §2). Its seed follows. Returns the part of
    speech it had. LookupError: no such entry; Conflict: retired, being written, or not an
    idiom or a phrase; ValueError: not a part of speech for its kind."""
    row = conn.execute(
        "select id, pos, kind::text, status::text, doc,"
        " published_at is not null and doc is not null from entries where key = %s for update",
        (key,),
    ).fetchone()
    if row is None:
        raise LookupError(key)
    entry_id, before, kind, status, doc, written = row
    if status in ("retired", "generating"):
        raise Conflict(f"it's {status}")
    if kind not in ("idiom", "phrase"):
        raise Conflict("only an idiom's or a phrase's part of speech changes in place")
    if not valid_pos(pos, kind):
        raise ValueError(f"{pos} can't go with {kind}")
    if pos == before:
        return before
    conn.execute("update entries set pos = %s, updated_at = now() where id = %s", (pos, entry_id))
    if written:
        changed = {**doc, "pos": pos}
        conn.execute(
            "update entries set doc = %s, content_version = content_version + 1 where id = %s",
            (Jsonb(changed), entry_id),
        )
        conn.execute(
            "insert into entry_revisions (id, entry_id, doc, produced_by, qa, outcome)"
            " values (%s, %s, %s, %s, %s, 'published')",
            (new_id(), entry_id, Jsonb(changed), Jsonb({"source": "policy", "by": by}),
             Jsonb({"changed": {"pos": [before, pos]}, "reason": reason})),
        )  # fmt: skip
    try:
        with conn.transaction():
            conn.execute("update seed_candidates set pos = %s where entry_id = %s", (pos, entry_id))
    except psycopg.errors.UniqueViolation:
        pass  # another seed already names it so: the entry is what counts
    return before


def add_pattern(
    conn: psycopg.Connection, key: str, pattern: str, *, retired: str | None = None
) -> bool:
    """Give an entry a pattern (look back on sth) the factory writes the next time it writes
    the entry. Patterns travel on seeds, so an entry without one (the owner or a student asked
    for it) gets a seed for it. `retired`: the retired entry the pattern stands for, whose
    searches now find this one. Returns False when it had the pattern already. LookupError: no
    such entry."""
    if retired is not None:
        conn.execute(
            "update entries set pattern_of = %s where key = %s and status = 'retired'",
            (key, retired),
        )
    row = conn.execute(
        "select id, lemma, lemma_norm, pos, kind::text, homograph from entries where key = %s",
        (key,),
    ).fetchone()
    if row is None:
        raise LookupError(key)
    entry_id, lemma, lemma_norm, pos, kind, homograph = row
    seeds = conn.execute(
        "select %s = any(patterns) from seed_candidates where entry_id = %s", (pattern, entry_id)
    ).fetchall()
    if seeds:
        conn.execute(
            "update seed_candidates set patterns = array_append(patterns, %s), updated_at = now()"
            " where entry_id = %s and not (%s = any(patterns))",
            (pattern, entry_id, pattern),
        )
        return not all(had for (had,) in seeds)
    added = conn.execute(
        "insert into seed_candidates (lemma, lemma_norm, pos, kind, homograph, sources,"
        " decision, reason, entry_id, patterns) values (%s, %s, %s, %s::entry_kind, %s,"
        " array['owner:pattern'], 'accept', 'carries a pattern', %s, array[%s])"
        " on conflict on constraint seed_candidates_identity do update"
        " set entry_id = coalesce(seed_candidates.entry_id, excluded.entry_id),"
        " patterns = case when %s = any(seed_candidates.patterns) then seed_candidates.patterns"
        " else array_append(seed_candidates.patterns, %s) end, updated_at = now()"
        " where seed_candidates.entry_id is null or seed_candidates.entry_id = excluded.entry_id"
        " returning 1",
        (lemma, lemma_norm, pos, kind, homograph, entry_id, pattern, pattern, pattern),
    ).fetchone()
    return added is not None


def _locked(conn: psycopg.Connection, key: str) -> dict[str, Any]:
    row = conn.execute(
        "select status::text, published_at is not null and doc is not null,"
        " coalesce((doc->>'provisional')::boolean, false), id, doc"
        " from entries where key = %s for update",
        (key,),
    ).fetchone()
    if row is None:
        raise LookupError(key)
    return {"status": row[0], "published": row[1], "provisional": row[2], "id": row[3],
            "doc": row[4]}  # fmt: skip
