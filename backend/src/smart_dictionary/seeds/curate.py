"""Curation files (design §6.1 step 7): decisions on seeds and entries that a model made and
the owner reviewed, applied by `smart-dictionary seeds curate --file <csv>`.

A file is a CSV the owner reads before it's applied, and `--dry-run` shows what would change
without changing anything. The header says which shape it is:
- **Seeds** (`lemma, pos, kind, decision, confidence, reason`, and optionally `variant_of,
  regions`): a seed nobody queued because it lacked a part of speech or kind gets them
  (`accept`), or is declined (`reject`). A declined spelling learners search for (*TV show*,
  *pyjama*) can name the seed that keeps the meaning (`variant_of`): it joins that seed's
  variants, with its `regions` (`UK`), so the factory lists it and search finds the entry.
  Only seeds still waiting are touched; `seeds queue` then queues the accepted ones, and
  links any whose word has an entry already.
- **Entries** (`key, decision, pos, kind, confidence, reason`, and optionally `pattern_of,
  pattern`): an entry `keep`s its part of speech and kind, `change`s them, or is `retire`d.
  - **A change of function alone** (an idiom or a phrase: *as long as* becomes a conjunction)
    happens in place, written or not: the key holds the kind, not the function.
  - **Any other change, and a retirement,** applies to unwritten entries only. A change
    retires the key (keys are never reused) and queues the right one in its place, with its
    seed, priority and approval, and the searches that pointed at it. A written entry isn't
    touched; the decision becomes an audit finding (design §6.6), for the owner to apply in
    the console.
  - **A retired longer form can hand its pattern to the entry that keeps the meaning**
    (`pattern_of`, `pattern`): *look back on* retires, and *look back* takes "look back (on
    sth)", so the factory writes it.

Every row is all or nothing, and each row's outcome is reported.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from smart_dictionary.admin.curation import add_pattern, set_pos
from smart_dictionary.content.word_requests import TWIN
from smart_dictionary.core import headwords
from smart_dictionary.core.entrydoc import POS, EnglishRegion, Kind, valid_pos
from smart_dictionary.core.ids import new_id
from smart_dictionary.core.keys import entry_key
from smart_dictionary.core.text import norm

SEED_COLUMNS = ["lemma", "pos", "kind", "decision", "confidence", "reason"]
VARIANT_COLUMNS = ["variant_of", "regions"]
ENTRY_COLUMNS = ["key", "decision", "pos", "kind", "confidence", "reason"]
PATTERN_COLUMNS = ["pattern_of", "pattern"]
CONFIDENCES = ("high", "medium", "low")

Shape = Literal["seeds", "entries"]


class Invalid(Exception):
    """The file can't be applied: a wrong header or row. Nothing was changed."""


@dataclass(frozen=True)
class Outcome:
    target: str  # the seed's lemma or the entry's key
    action: str  # what happened, or would: "accepted as noun/word", "skipped: …"
    changed: bool


def read(path: Path) -> tuple[Shape, list[dict[str, str]]]:
    """The file's shape and rows, checked before anything is applied."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        rows = [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]
    if header in (SEED_COLUMNS, SEED_COLUMNS + VARIANT_COLUMNS):
        shape: Shape = "seeds"
        allowed = ("accept", "reject")
    elif header in (ENTRY_COLUMNS, ENTRY_COLUMNS + PATTERN_COLUMNS):
        shape = "entries"
        allowed = ("keep", "change", "retire")
    else:
        raise Invalid(
            f"the header must be {SEED_COLUMNS} (optionally with {VARIANT_COLUMNS}) or"
            f" {ENTRY_COLUMNS}, not {header}"
        )
    for number, row in enumerate(rows, start=2):
        if row["decision"] not in allowed:
            raise Invalid(f"line {number}: decision must be one of {', '.join(allowed)}")
        if row["confidence"] not in CONFIDENCES:
            raise Invalid(f"line {number}: confidence must be high, medium or low")
        if row["decision"] in ("accept", "keep", "change") and not valid(row["pos"], row["kind"]):
            raise Invalid(f"line {number}: {row['pos']!r} can't go with {row['kind']!r}")
        if row.get("variant_of") and row["decision"] != "reject":
            raise Invalid(f"line {number}: only a declined seed can be another's variant")
        unknown = set(row.get("regions", "").split()) - set(get_args(EnglishRegion))
        if unknown:
            raise Invalid(f"line {number}: unknown regions {', '.join(sorted(unknown))}")
    return shape, rows


def valid(pos: str, kind: str) -> bool:
    """The part of speech and kind can make an entry: a phrasal verb is a verb, a word anything
    but a phrase, and an idiom or a phrase its function, or "phrase"."""
    if pos not in get_args(POS) or kind not in get_args(Kind):
        return False
    return valid_pos(pos, kind)


def apply(
    conn: psycopg.Connection, shape: Shape, rows: list[dict[str, str]], *, label: str
) -> list[Outcome]:
    """Apply each row in a savepoint; `label` names the file in what's recorded. The caller
    commits, or rolls back for a dry run."""
    outcomes = []
    for row in rows:
        try:
            with conn.transaction():
                if shape == "seeds":
                    outcomes.append(_seed(conn, row, label))
                else:
                    outcomes.append(_entry(conn, row, label))
        except _Twin as twin:
            action = f"skipped: {twin} is the same expression; retire one of them first"
            outcomes.append(Outcome(row["key"], action, False))
    return outcomes


def waiting(conn: psycopg.Connection, shape: Shape, rows: list[dict[str, str]]) -> list[str]:
    """What still waits for a decision that the file doesn't cover: seeds with no part of
    speech or kind, or, for a file about them, multiword verbs stored as plain words."""
    if shape == "seeds":
        found = conn.execute(
            "select lemma from seed_candidates where entry_id is null"
            " and coalesce(decision, 'accept') = 'accept'"
            " and (pos is null or kind is null or case kind when 'word' then pos = 'phrase'"
            "      when 'phrasal_verb' then pos <> 'verb' else false end)"
            " order by lemma_norm"
        ).fetchall()
        covered = {row["lemma"] for row in rows}
    else:
        found = conn.execute(
            "select key from entries where language = 'en' and strpos(lemma_norm, ' ') > 0"
            " and kind = 'word' and pos = 'verb' and status <> 'retired' order by key"
        ).fetchall()
        covered = {row["key"] for row in rows}
        if len(covered & {row[0] for row in found}) * 2 < len(covered):
            return []  # mostly another subject (the categories of 2026-10-24)
    return [row[0] for row in found if row[0] not in covered]


# Seeds ------------------------------------------------------------------------------------


def _seed(conn: psycopg.Connection, row: dict[str, str], label: str) -> Outcome:
    lemma = row["lemma"]
    seeds = conn.execute(
        "select id from seed_candidates where lemma = %s and entry_id is null"
        " and coalesce(decision, 'accept') = 'accept'"
        " and (pos is null or kind is null or case kind when 'word' then pos = 'phrase'"
        "      when 'phrasal_verb' then pos <> 'verb' else false end)"
        " order by homograph",
        (lemma,),
    ).fetchall()
    if not seeds:
        return Outcome(lemma, "skipped: no seed waits for it (queued or decided since)", False)
    reason = _reason(row, label)
    if row["decision"] == "reject":
        conn.execute(
            "update seed_candidates set decision = 'reject', reason = %s, updated_at = now()"
            " where id = any(%s)",
            (reason, [seed[0] for seed in seeds]),
        )
        if row.get("variant_of"):
            return _variant(conn, lemma, row["variant_of"], row.get("regions", "").split())
        return Outcome(lemma, "rejected", True)
    pos = row["pos"]
    headword, pattern = headwords.mended(lemma, pos)
    kind = headwords.mended_kind(headword, row["kind"])
    try:
        with conn.transaction():
            conn.execute(
                "update seed_candidates set pos = %s, kind = %s::entry_kind, decision = 'accept',"
                " reason = %s, flags = array_append(flags, %s), updated_at = now() where id = %s",
                (pos, kind, reason, f"part of speech and kind from {label}", seeds[0][0]),
            )
            if headword != lemma:
                conn.execute(
                    "update seed_candidates set lemma = %s, lemma_norm = %s,"
                    " patterns = case when %s::text is null or %s = any(patterns) then patterns"
                    " else array_append(patterns, %s) end where id = %s",
                    (headword, norm(headword), pattern, pattern, pattern, seeds[0][0]),
                )
    except psycopg.errors.UniqueViolation:
        conn.execute(
            "update seed_candidates set decision = 'merge', reason = %s, updated_at = now()"
            " where id = %s",
            (f"another seed is {headword} as {pos}/{kind}; {reason}"[:500], seeds[0][0]),
        )
        if pattern:  # the seed that stays takes the pattern this one carried
            conn.execute(
                "update seed_candidates set patterns = array_append(patterns, %s)"
                " where lemma_norm = %s and pos = %s and kind = %s::entry_kind"
                " and homograph = 1 and not (%s = any(patterns))",
                (pattern, norm(headword), pos, kind, pattern),
            )
        return Outcome(lemma, f"merged: another seed is {headword} as {pos}/{kind}", True)
    if headword != lemma:
        return Outcome(lemma, f"accepted as {pos}/{kind}, headword {headword!r}"
                       + (f" with the pattern {pattern!r}" if pattern else ""), True)  # fmt: skip
    return Outcome(lemma, f"accepted as {pos}/{kind}", True)


def _variant(conn: psycopg.Connection, form: str, of: str, regions: list[str]) -> Outcome:
    """Add a declined spelling to the variants of the seeds that keep its meaning."""
    kept = [
        row[0]
        for row in conn.execute(
            "select id from seed_candidates where lemma = %s"
            " and coalesce(decision, 'accept') = 'accept'",
            (of,),
        )
    ]
    if not kept:
        return Outcome(form, f"rejected; no seed {of} takes it as a variant", True)
    conn.execute(
        "update seed_candidates c set variants = c.variants || %s, updated_at = now()"
        " where c.id = any(%s) and not exists (select from jsonb_array_elements(c.variants) v"
        "                                      where lower(v->>'form') = lower(%s))",
        (Jsonb([{"form": form, "regions": regions}]), kept, form),
    )
    return Outcome(form, f"rejected: a variant of {of} now", True)


# Entries ----------------------------------------------------------------------------------


def _entry(conn: psycopg.Connection, row: dict[str, str], label: str) -> Outcome:
    key = row["key"]
    with conn.cursor(row_factory=dict_row) as cursor:
        entry = cursor.execute(
            "select id, key, lemma, lemma_norm, pos, kind::text as kind, status::text as status,"
            " cefr, freq_zipf, priority, sources, approved_at, approved_by,"
            " published_at is not null and doc is not null as written"
            " from entries where key = %s for update",
            (key,),
        ).fetchone()
    if entry is None:
        return Outcome(key, "skipped: no such entry", False)
    if row["decision"] == "keep":
        return Outcome(key, "kept", False)
    if entry["status"] == "retired":
        return Outcome(key, "skipped: retired already", False)
    if entry["status"] == "generating":
        return Outcome(key, "skipped: the routine is writing it; run the file again later", False)
    pos, kind = row["pos"], row["kind"]
    new_key = entry_key("en", entry["lemma"], pos, kind) if row["decision"] == "change" else None
    if new_key == key:
        if pos == entry["pos"]:
            return Outcome(key, "kept: that's its own part of speech and kind", False)
        set_pos(conn, key, pos, by=label, reason=row["reason"])
        return Outcome(key, f"function: {pos}", True)
    if entry["written"]:
        return _finding(conn, entry, row, label)
    reason = _reason(row, label)
    if row["decision"] == "retire":
        _retire(conn, entry, None)
        conn.execute(
            "update seed_candidates set decision = 'reject', reason = %s, updated_at = now()"
            " where entry_id = %s",
            (reason, entry["id"]),
        )
        if row.get("pattern_of"):
            return _pattern(conn, key, row["pattern_of"], row.get("pattern") or entry["lemma"])
        return Outcome(key, "retired", True)

    assert new_key is not None
    target = conn.execute(
        "select id, status::text from entries where key = %s for update", (new_key,)
    ).fetchone()
    if target is not None and target[1] == "retired":
        return Outcome(key, f"skipped: {new_key} was retired, and keys are never reused", False)
    if target is None:
        conn.execute("update entries set status = 'retired', updated_at = now() where id = %s",
                     (entry["id"],))  # fmt: skip
        twin = conn.execute(
            TWIN, {"lemma": entry["lemma_norm"], "key": new_key, "kind": kind}
        ).fetchone()
        if twin is not None:
            raise _Twin(twin[0])
        target_id = new_id()
        conn.execute(
            "insert into entries (id, key, language, lemma, lemma_norm, pos, kind, homograph,"
            " cefr, freq_zipf, priority, status, sources, approved_at, approved_by)"
            " values (%s, %s, 'en', %s, %s, %s, %s, 1, %s, %s, %s, 'queued', %s,"
            " coalesce(%s, now()), %s)",
            (target_id, new_key, entry["lemma"], entry["lemma_norm"], pos, kind, entry["cefr"],
             entry["freq_zipf"], entry["priority"], entry["sources"], entry["approved_at"],
             entry["approved_by"] or "seeds"),
        )  # fmt: skip
        action = f"requeued as {new_key}"
    else:
        target_id = target[0]
        action = f"retired: {new_key} has it already"
    _retire(conn, entry, (target_id, new_key))
    conn.execute(
        "update seed_candidates set entry_id = %s, reason = %s, updated_at = now()"
        " where entry_id = %s",
        (target_id, reason, entry["id"]),
    )
    return Outcome(key, action, True)


def _pattern(conn: psycopg.Connection, key: str, of: str, pattern: str) -> Outcome:
    """Give the entry that keeps the meaning the retired form as a pattern, through its seed:
    the factory writes it the next time it writes the entry."""
    try:
        added = add_pattern(conn, of, pattern, retired=key)
    except LookupError:
        return Outcome(key, f"retired; {of} isn't an entry to take the pattern", True)
    return Outcome(key, f"retired: a pattern of {of} now" + ("" if added else " (listed already)"),
                   True)  # fmt: skip


class _Twin(Exception):
    pass


def _retire(conn: psycopg.Connection, entry: dict[str, Any], moved: tuple[Any, str] | None) -> None:
    """Retire the key. The searches and pending requests that pointed at it follow the entry
    it moved to, or are closed when it moved nowhere."""
    conn.execute("update entries set status = 'retired', updated_at = now() where id = %s",
                 (entry["id"],))  # fmt: skip
    if moved is None:
        conn.execute(
            "update misses set status = 'invalid', entry_key = null, headword = null,"
            " running_since = null where entry_key = %s",
            (entry["key"],),
        )
        conn.execute(
            "update word_requests set status = 'rejected', decided_at = now(),"
            " note = coalesce(note, 'retired in curation') where entry_key = %s"
            " and status = 'pending'",
            (entry["key"],),
        )
        return
    _, new_key = moved
    conn.execute(
        "update misses set entry_key = %s, status = 'pending', attempts = 0,"
        " running_since = null, last_attempt_at = null, last_error = null where entry_key = %s",
        (new_key, entry["key"]),
    )
    conn.execute(
        "update word_requests set entry_key = %s where entry_key = %s", (new_key, entry["key"])
    )


def _finding(
    conn: psycopg.Connection, entry: dict[str, Any], row: dict[str, str], label: str
) -> Outcome:
    """A written entry isn't changed here: the decision waits in the console as an audit
    finding, unless an open one of the same type is there already."""
    kind = "remove" if row["decision"] == "retire" else "kind"
    open_one = conn.execute(
        "select 1 from audit_findings where entry_id = %s and type = %s and status = 'open'",
        (entry["id"], kind),
    ).fetchone()
    if open_one is not None:
        return Outcome(entry["key"], f"skipped: an open {kind} finding is waiting already", False)
    proposed = {"retire": entry["key"]} if kind == "remove" else {"pos": row["pos"],
                                                                  "kind": row["kind"]}  # fmt: skip
    conn.execute(
        "insert into audit_findings (id, entry_id, entry_key, target_key, type, source,"
        " confidence, problem, evidence, proposed)"
        " values (%s, %s, %s, %s, %s, 'ai', %s, %s, %s, %s)",
        (new_id(), entry["id"], entry["key"], entry["key"], kind, row["confidence"],
         row["reason"][:500] or f"{label} proposes it", Jsonb({"curation": label}),
         Jsonb(proposed)),
    )  # fmt: skip
    return Outcome(entry["key"], f"written already: a {kind} finding waits in the console", True)


def _reason(row: dict[str, str], label: str) -> str:
    return f"{row['reason']} ({row['confidence']} confidence; {label})"[:500]


# Functions from Wiktionary ------------------------------------------------------------------

# The word classes an idiom or a phrase can take from Wiktionary, which uses the same names.
FUNCTIONS = ("noun", "verb", "adjective", "adverb", "pronoun", "determiner", "preposition",
             "conjunction", "interjection", "numeral")  # fmt: skip
# Idioms and phrases still without a function, each with the one Wiktionary gives, if one.
ONE_FUNCTION = """
select e.key, f.found[1] from entries e
cross join lateral (
  select array(select distinct k.pos from ref_wiktionary k
               where k.word_norm = e.lemma_norm and k.pos = any(%(functions)s)) as found) f
where e.language = 'en' and e.status <> 'retired' and e.kind in ('idiom', 'phrase')
  and e.pos = 'phrase' and cardinality(f.found) = 1
order by e.key
"""
SEED_FUNCTIONS = """
update seed_candidates c set pos = f.found[1], updated_at = now()
from (select c2.id, array(select distinct k.pos from ref_wiktionary k
                          where k.word_norm = c2.lemma_norm and k.pos = any(%(functions)s)) as found
      from seed_candidates c2
      where c2.entry_id is null and c2.kind in ('idiom', 'phrase') and c2.pos = 'phrase') f
where c.id = f.id and cardinality(f.found) = 1
  and not exists (select from seed_candidates o where o.lemma_norm = c.lemma_norm
                  and o.pos = f.found[1] and o.kind = c.kind and o.homograph = c.homograph)
returning c.lemma
"""


def functions(conn: psycopg.Connection, *, label: str) -> tuple[list[Outcome], int, int]:
    """Give each idiom and phrase still marked `phrase` the one word class Wiktionary records
    for it (policy of 2026-10-24): in place, written or not, and its seed too. Returns what
    changed, how many seeds waiting to be queued changed, and how many idioms and phrases
    still have none (several functions, or none known): those need a curation file."""
    outcomes = []
    for key, pos in conn.execute(ONE_FUNCTION, {"functions": list(FUNCTIONS)}).fetchall():
        try:
            with conn.transaction():
                set_pos(conn, key, pos, by=label, reason="the one function Wiktionary gives it")
            outcomes.append(Outcome(key, f"function: {pos}", True))
        except Exception as error:  # a Conflict: it's being written; the next run takes it
            outcomes.append(Outcome(key, f"skipped: {error}", False))
    seeds = len(conn.execute(SEED_FUNCTIONS, {"functions": list(FUNCTIONS)}).fetchall())
    left = conn.execute(
        "select count(*) from entries where language = 'en' and status <> 'retired'"
        " and kind in ('idiom', 'phrase') and pos = 'phrase'"
    ).fetchone()
    return outcomes, seeds, left[0] if left else 0
