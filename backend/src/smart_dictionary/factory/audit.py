"""The audit (design §6.6) [CG-7]: a second routine re-reads entries in batches, and the owner
decides what it finds.

- **The server keeps the audit's place,** not the routine: each entry records when it was
  audited and under which instructions (`audit_version`, a hash of AUDIT.md and the house
  standard). A lease hands out the entries due next, for three hours, so a crashed run loses
  nothing and changed instructions make every entry due again.
- **What's due first:** words approved at once (chosen by AI or by the lists' rule), then
  written entries never audited, the most looked-up first, then unwritten ones (their facts
  only, before the factory spends a run on them), then entries audited under older
  instructions, then those audited over REAUDIT_AFTER ago. Betas, entries in repair and
  entries with an open finding wait.
- **Two layers.** The server's rules (free, the same answer every time) raise what data can
  show: a CEFR level two or more steps from the CEFR lists', a part of speech the lists or
  Wiktionary give that has no entry, an idiom stored as a plain word, a twin, a headword
  Wiktionary lacks, a related link to no word, and what breaks the policy of 2026-10-24 on
  categories and headwords: a headword ending in an object slot or spelling one another way,
  an idiom or a phrase without its function, and a longer form that may only be a pattern of
  a shorter entry (*look back on*, *look back*). The auditor confirms or dismisses each, and
  adds what needs judgment: meanings, definitions, examples, Spanish, levels with no list,
  and whether a multiword item is a compound word or a phrase.
- **The auditor proposes, never edits.** Each finding has a type, the key it's about, the
  evidence and a proposed fix. Applying one goes through the paths that exist: a content fix
  sends the entry to repair, where the factory rewrites it with every check and keeps its
  keys; a missing part of speech is added as the owner's word; a twin, a wrong kind or a
  word to remove is retired.
- **Trust, verified:** with `audit_auto_repair` on (the default), a high-confidence content
  fix is applied at once (`auto:audit`); everything else waits for the owner, who sees both.
  Rejecting one applied at once calls its repair off while the factory hasn't started it.
- **The owner has the last word:** each item carries the owner's decisions on its entry (a
  draft published over its review, the owner's repair notes), and the auditor never raises a
  finding against them.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, get_args

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from smart_dictionary.admin import curation
from smart_dictionary.content import related
from smart_dictionary.content.word_requests import Conflict
from smart_dictionary.core import headwords as headword_rules
from smart_dictionary.core.entrydoc import CEFR, POS, Kind, valid_pos
from smart_dictionary.core.ids import new_id
from smart_dictionary.core.keys import entry_key
from smart_dictionary.core.text import norm
from smart_dictionary.factory.packs import MAX_HINTS, hint
from smart_dictionary.factory.validate import _keys
from smart_dictionary.seeds.builder import MIN_SENSES, MIN_SHARE

LEASE = timedelta(hours=3)
REAUDIT_AFTER = "180 days"
LEVELS = list(get_args(CEFR))
CEFR_LISTS = ("cefrj", "octanove-c1c2")
MAX_FINDINGS = 10
AUTO_BY = "auto:audit"

FindingType = Literal["cefr", "meaning", "definition", "example", "spanish", "content",
                      "part_of_speech", "kind", "duplicate", "remove", "headword"]  # fmt: skip
Confidence = Literal["high", "medium", "low"]
# Findings about the content: applied by sending the entry to repair, so the factory rewrites
# it. The others change which entries exist, so they always wait for the owner.
REPAIRS = ("cefr", "meaning", "definition", "example", "spanish", "content")
# The word classes an idiom or a phrase can work as: *break the ice* (verb), *as long as*
# (conjunction).
FUNCTIONS = ("noun", "verb", "adjective", "adverb", "preposition", "conjunction",
             "interjection", "pronoun", "determiner", "numeral")  # fmt: skip
# The preposition that can end a longer form of an entry: *look back on*, *comply with*.
PREPOSITIONS = ("on", "with", "to", "for", "of", "in", "at", "about", "from", "into", "over",
                "against", "by", "upon")  # fmt: skip


class BatchClosed(Exception):
    """The batch was submitted already, or its lease expired."""


# 1. Leasing -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Lease:
    id: uuid.UUID | None
    expires_at: datetime | None
    items: list[dict[str, Any]]


DUE = """
with busy as (
  select unnest(entry_ids) as id from audit_batches
  where status = 'leased' and expires_at > now()
), used as (
  select entry_key, count(*) as n from lookups
  where created_at > now() - interval '30 days' and entry_key is not null group by 1
)
select e.id
from entries e left join used u on u.entry_key = e.key
where e.language = 'en' and e.approved_at is not null
  and e.status::text in ('published', 'queued', 'candidate')
  and not exists (select from busy b where b.id = e.id)
  and not exists (select from audit_findings f where f.entry_id = e.id and f.status = 'open')
  and (e.audited_at is null or e.audit_version is distinct from %(version)s
       or e.audited_at < now() - cast(%(again)s as interval))
order by
  case when e.audited_at is null
            and (left(e.approved_by, 3) = 'ai:' or left(e.approved_by, 5) = 'auto:') then 0
       when e.audited_at is null and e.published_at is not null then 1
       when e.audited_at is null then 2
       when e.audit_version is distinct from %(version)s then 3
       else 4 end,
  coalesce(u.n, 0) desc, e.priority desc, e.audited_at nulls first, e.key
limit %(size)s
"""


def expire(conn: psycopg.Connection) -> None:
    conn.execute(
        "update audit_batches set status = 'expired' where status = 'leased' and expires_at < now()"
    )


def lease(
    conn: psycopg.Connection, size: int, *, label: str | None, client: str, version: str
) -> Lease:
    """Lease the `size` entries due next, each with its facts and the rules' findings. No
    batch is recorded when nothing is due."""
    expire(conn)
    # One lease at a time: two runs never get the same entry.
    conn.execute("select pg_advisory_xact_lock(hashtext('audit_lease'))")
    params = {"version": version, "size": size, "again": REAUDIT_AFTER}
    ids = [row[0] for row in conn.execute(DUE, params)]
    if not ids:
        return Lease(None, None, [])
    items = packs(conn, ids)
    rules = {item["key"]: item["rule_findings"] for item in items}
    row = conn.execute(
        "insert into audit_batches (id, client, run_label, audit_version, entry_ids, rules,"
        " expires_at) values (%s, %s, %s, %s, %s, %s, now() + %s) returning id, expires_at",
        (new_id(), client, label, version, ids, Jsonb(rules), LEASE),
    ).fetchone()
    assert row is not None
    return Lease(row[0], row[1], items)


# 2. What the auditor gets: facts and the rules' findings --------------------------------------


def packs(conn: psycopg.Connection, entry_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """One item per entry, in the order given."""
    with conn.cursor(row_factory=dict_row) as cursor:
        entries = cursor.execute(
            "select id, key, lemma, lemma_norm, expression_of(lemma_norm) as expression, pos,"
            " kind::text as kind, status::text as status, freq_zipf, approved_by, doc,"
            " published_at is not null and doc is not null as written"
            " from entries where id = any(%(ids)s) order by array_position(%(ids)s, id)",
            {"ids": entry_ids},
        ).fetchall()
        lemmas = sorted({entry["lemma_norm"] for entry in entries})
        expressions = sorted({entry["expression"] for entry in entries})
        lists = _grouped(cursor.execute(
            "select lemma_norm as word, dataset_id, pos, level from ref_lists"
            " where lemma_norm = any(%s) order by dataset_id", (lemmas,)
        ))  # fmt: skip
        wiktionary = _grouped(cursor.execute(
            "select word_norm as word, pos, kind, glosses from ref_wiktionary"
            " where word_norm = any(%s) order by id", (lemmas,)
        ))  # fmt: skip
        others = _grouped(cursor.execute(
            "select expression_of(lemma_norm) as word, key, lemma_norm, pos, kind::text as kind,"
            " status::text as status, approved_at is not null as approved,"
            " published_at is not null as written from entries"
            " where language = 'en' and status <> 'retired'"
            " and expression_of(lemma_norm) = any(%s) order by key", (expressions,)
        ))  # fmt: skip
        seeds = {
            (row["lemma_norm"], row["pos"])
            for row in cursor.execute(
                "select lemma_norm, pos from seed_candidates where lemma_norm = any(%s)",
                (lemmas,),
            )
        }
        known = {
            row["fingerprint"]
            for row in cursor.execute(
                "select distinct evidence->>'fingerprint' as fingerprint from audit_findings"
                " where evidence ? 'fingerprint' and entry_id = any(%s)"
                " union select evidence->>'fingerprint' from audit_findings"
                " where type in ('part_of_speech', 'duplicate') and evidence ? 'fingerprint'"
                " and evidence->>'lemma' = any(%s)",
                (entry_ids, lemmas),
            )
        }
        past = _grouped(cursor.execute(
            "select entry_id::text as word, type, target_key, problem, status, note"
            " from audit_findings where entry_id = any(%s) and status in ('rejected', 'dismissed')"
            " order by created_at desc", (entry_ids,)
        ))  # fmt: skip
        used = dict(conn.execute(
            "select entry_key, count(*) from lookups where entry_key = any(%s)"
            " and created_at > now() - interval '30 days' group by 1",
            ([entry["key"] for entry in entries],),
        ).fetchall())  # fmt: skip
        owner = _grouped(cursor.execute(
            "select entry_id::text as word, created_at, qa->'owner' as decision"
            " from entry_revisions where entry_id = any(%s)"
            " and produced_by->>'source' = 'owner' order by created_at desc", (entry_ids,)
        ))  # fmt: skip
        linked = _grouped(cursor.execute(
            "select r.entry_key as word, l.entry_key || ' ' || l.type as link"
            " from word_requests r join word_request_links l on l.request_id = r.id"
            " where r.entry_key = any(%s)", ([entry["key"] for entry in entries],)
        ))  # fmt: skip
        bases = sorted({base for entry in entries if (base := _base(entry["lemma_norm"]))})
        shorter = _grouped(cursor.execute(
            "select lemma_norm as word, key, pos from entries where language = 'en'"
            " and status <> 'retired' and lemma_norm = any(%s) order by priority desc, key",
            (bases,),
        ))  # fmt: skip
    dead = _dead_links(conn, [entry for entry in entries if entry["written"]])
    items = []
    for entry in entries:
        word = entry["lemma_norm"]
        base = _base(word)
        context = _Context(lists.get(word, []), wiktionary.get(word, []), others.get(
            entry["expression"], []), seeds, known, dead.get(entry["key"], []),
            shorter.get(base, []) if base else [])  # fmt: skip
        items.append({
            "key": entry["key"],
            "lemma": entry["lemma"],
            "pos": entry["pos"],
            "kind": entry["kind"],
            "status": entry["status"],
            "written": entry["written"],
            "chosen_by": entry["approved_by"],
            "doc": entry["doc"] if entry["written"] else None,
            "facts": {
                "zipf": float(entry["freq_zipf"]) if entry["freq_zipf"] is not None else None,
                "lists": [_listed(row) for row in context.lists],
                "wiktionary": _wiktionary(context.wiktionary),
                "other_entries": [f"{row['key']} ({row['status']})" for row in context.others
                                  if row["key"] != entry["key"]],
                "linked_from": [row["link"] for row in linked.get(entry["key"], [])][:10],
                "lookups_30d": used.get(entry["key"], 0),
            },
            "past_decisions": [
                {k: row[k] for k in ("type", "target_key", "problem", "status", "note")}
                for row in past.get(str(entry["id"]), [])
            ][:10],
            "owner_decisions": [
                {"at": row["created_at"].isoformat(), **(row["decision"] or {})}
                for row in owner.get(str(entry["id"]), [])
            ][:5],
            "rule_findings": _rules(entry, context),
        })  # fmt: skip
    return items


@dataclass
class _Context:
    lists: list[dict[str, Any]]
    wiktionary: list[dict[str, Any]]
    others: list[dict[str, Any]]
    seeds: set[tuple[str, str | None]]
    known: set[str]
    dead: list[str]
    shorter: list[dict[str, Any]]  # entries that are this one less its last preposition
    found: list[dict[str, Any]] = field(default_factory=list)

    def raise_(
        self,
        kind: FindingType,
        target: str,
        problem: str,
        evidence: dict[str, Any],
        proposed: dict[str, Any],
    ) -> None:
        """Add a finding unless the same one was raised before, whatever its decision."""
        if evidence["fingerprint"] in self.known:
            return
        self.known.add(evidence["fingerprint"])
        number = f"R{len(self.found) + 1}"
        self.found.append({"id": number, "type": kind, "target_key": target, "problem": problem,
                           "evidence": evidence, "proposed": proposed})  # fmt: skip


def _rules(entry: dict[str, Any], context: _Context) -> list[dict[str, Any]]:
    key, lemma, word = entry["key"], entry["lemma"], entry["lemma_norm"]
    multiword = " " in word

    # A level two or more steps from the CEFR lists' (one step is a judgment call).
    if entry["written"]:
        levels = sorted({row["level"] for row in context.lists if row["level"]
                         and row["dataset_id"] in CEFR_LISTS
                         and row["pos"] in (None, entry["pos"])}, key=LEVELS.index)  # fmt: skip
        level = (entry["doc"] or {}).get("cefr")
        if levels and level in LEVELS:
            closest = min(
                levels, key=lambda listed: abs(LEVELS.index(listed) - LEVELS.index(level))
            )
            if abs(LEVELS.index(closest) - LEVELS.index(level)) >= 2:
                context.raise_(
                    "cefr", key, f"The entry is {level}; the CEFR lists give {', '.join(levels)}.",
                    {"fingerprint": f"cefr:{key}:{closest}", "entry": level, "lists": levels},
                    {"cefr": closest, "fix": f"Check the main sense's level against {closest}."},
                )  # fmt: skip

    # A part of speech the CEFR lists or Wiktionary give that has no entry and no seed.
    if not multiword:
        have = {row["pos"] for row in context.others if row["lemma_norm"] == word}
        senses = {row["pos"]: len(row["glosses"] or []) for row in context.wiktionary
                  if row["kind"] == "word"}  # fmt: skip
        most = max(senses.values(), default=0)
        wanted = {row["pos"] for row in context.lists if row["pos"]
                  and row["dataset_id"] in CEFR_LISTS}  # fmt: skip
        wanted |= {pos for pos, count in senses.items()
                   if count >= MIN_SENSES and count >= MIN_SHARE * most}  # fmt: skip
        for pos in sorted(wanted - have - {"phrase"}):
            if (word, pos) in context.seeds:
                continue  # its seed waits for curation, or was declined
            evidence = {"fingerprint": f"part_of_speech:{word}:{pos}", "lemma": word,
                        "lists": [_listed(row) for row in context.lists if row["pos"] == pos],
                        "wiktionary_senses": senses.get(pos, 0)}  # fmt: skip
            context.raise_(
                "part_of_speech", key,
                f"{lemma} is also a {pos}, and the dictionary has no entry for it.",
                evidence, {"lemma": lemma, "pos": pos, "kind": "word"},
            )  # fmt: skip

    # An idiom, phrasal verb or phrase stored as a plain word.
    if multiword and entry["kind"] == "word":
        kinds = {row["kind"] for row in context.wiktionary}
        if kinds and "word" not in kinds and len(kinds) == 1:
            (kind,) = kinds
            functions = {row["pos"] for row in context.wiktionary} - {"phrase"}
            pos = "verb" if kind == "phrasal_verb" else (
                next(iter(functions)) if len(functions) == 1 else "phrase")  # fmt: skip
            context.raise_(
                "kind", key, f"Wiktionary has {lemma} only as a {kind.replace('_', ' ')}.",
                {"fingerprint": f"kind:{key}:{kind}"}, {"pos": pos, "kind": kind},
            )  # fmt: skip

    # A twin: the same expression under another kind, or with or without its article.
    twins = [row for row in context.others if row["key"] != key
             and (row["lemma_norm"] != word or row["kind"] != entry["kind"])]  # fmt: skip
    for twin in twins:
        pair = " ".join(sorted([key, twin["key"]]))
        proposed: dict[str, Any] = {}
        if entry["written"] != twin["written"]:
            keep, retire = (key, twin["key"]) if entry["written"] else (twin["key"], key)
            proposed = {"retire": retire, "keep": keep}
        context.raise_(
            "duplicate", key, f"{twin['key']} ({twin['status']}) is the same expression.",
            {"fingerprint": f"duplicate:{pair}", "lemma": word, "pair": pair}, proposed,
        )  # fmt: skip

    # The policy of 2026-10-24: the headword, the category and the function.
    broken = headword_rules.problems(lemma, entry["pos"])
    if broken:
        better, pattern = headword_rules.mended(lemma, entry["pos"])
        kind = headword_rules.mended_kind(better, entry["kind"])
        context.raise_(
            "headword", key, f"The headword {'; '.join(broken)}.",
            {"fingerprint": f"headword:{key}"},
            {"lemma": better, **({"pattern": pattern} if pattern else {}),
             **({"kind": kind} if kind != entry["kind"] else {})},
        )  # fmt: skip
    if entry["kind"] in ("idiom", "phrase") and entry["pos"] == "phrase":
        functions = {row["pos"] for row in context.wiktionary} & set(FUNCTIONS)
        if len(functions) == 1:
            (function,) = functions
            context.raise_(
                "kind", key, f"It works as a {function}, as Wiktionary has it; its part of"
                " speech says only 'phrase'.",
                {"fingerprint": f"kind:{key}:{function}"},
                {"pos": function, "kind": entry["kind"]},
            )  # fmt: skip
    for base in context.shorter:
        if base["pos"] != entry["pos"]:  # *long for*, a verb, is a pattern of the verb *long*
            continue
        pattern = f"{lemma} sth"
        context.raise_(
            "duplicate", key, f"It may be {base['key']} with its usual preposition, a pattern"
            f" ({pattern}) rather than an entry. Keep it only if its meaning is its own.",
            {"fingerprint": f"duplicate:{key}:{base['key']}", "lemma": word},
            {"retire": key, "keep": base["key"], "pattern": pattern},
        )  # fmt: skip
        break

    # A headword Wiktionary lacks: a real word it misses, or one to remove.
    if not context.wiktionary:
        context.raise_(
            "remove", key,
            f"{lemma} isn't a Wiktionary headword: check it's a real, current word or"
            " expression a learner needs.",
            {"fingerprint": f"remove:{key}"}, {"retire": key},
        )  # fmt: skip

    # Related links that name no dictionary word (typos), which no card will ever show.
    if context.dead:
        context.raise_(
            "content", key, f"Related links name no dictionary word: {', '.join(context.dead)}.",
            {"fingerprint": f"content:{key}:links:{','.join(context.dead)}"},
            {"fix": f"Correct or remove the related links {', '.join(context.dead)}."},
        )  # fmt: skip
    return context.found


def _dead_links(conn: psycopg.Connection, entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Each written entry's related keys that no entry, Wiktionary headword or variant names."""
    links = {
        entry["key"]: [found for item in (entry["doc"] or {}).get("related") or []
                       if (found := related.link(item["key"]))]
        for entry in entries
    }  # fmt: skip
    words = sorted({found.lemma for found_links in links.values() for found in found_links})
    if not words:
        return {}
    named = {
        row[0]
        for row in conn.execute(
            "select w from unnest(%(w)s::text[]) as w"
            " where exists (select from entries e where e.language = 'en'"
            "               and expression_of(e.lemma_norm) = expression_of(w))"
            " or exists (select from ref_wiktionary k where k.word_norm = w)"
            " or exists (select from entry_forms f where f.form_norm = w)",
            {"w": words},
        )
    }
    return {
        key: [found.key for found in found_links if found.lemma not in named]
        for key, found_links in links.items()
        if any(found.lemma not in named for found in found_links)
    }


def _base(word: str) -> str | None:
    """A multiword headword less a preposition at its end: *look back on* → *look back*."""
    words = word.split()
    return " ".join(words[:-1]) if len(words) > 1 and words[-1] in PREPOSITIONS else None


def _listed(row: dict[str, Any]) -> str:
    level = f" {row['level']}" if row["level"] else ""
    pos = f" ({row['pos']})" if row["pos"] else ""
    return f"{row['dataset_id']}{level}{pos}"


def _wiktionary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each part of speech and kind with its number of senses and a few hints: facts, never
    wording to copy (CC BY-SA)."""
    return [
        {"pos": row["pos"], "kind": row["kind"], "senses": len(row["glosses"] or []),
         "hints": [hint(gloss) for gloss in (row["glosses"] or [])[:MAX_HINTS]]}
        for row in rows
    ]  # fmt: skip


def _grouped(rows: Any) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["word"]].append(row)
    return grouped


# 3. Submitting -----------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleAnswer:
    id: str
    verdict: Literal["confirm", "dismiss"]
    confidence: Confidence = "medium"
    note: str | None = None
    proposed: dict[str, Any] | None = None  # a better fix than the rule's, when confirming


@dataclass(frozen=True)
class Finding:
    type: FindingType
    target_key: str
    confidence: Confidence
    problem: str
    proposed: dict[str, Any]


@dataclass(frozen=True)
class Audited:
    key: str
    rules: list[RuleAnswer] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


@dataclass(frozen=True)
class Outcome:
    key: str
    outcome: Literal["audited", "not_leased"]
    findings: int = 0
    applied: int = 0
    errors: list[str] = field(default_factory=list)


INSERT = """
insert into audit_findings (id, batch_id, entry_id, entry_key, target_key, type, source,
                            confidence, problem, evidence, proposed, status, note, decided_at,
                            decided_by)
values (%(id)s, %(batch)s, %(entry)s, %(key)s, %(target)s, %(type)s, %(source)s, %(confidence)s,
        %(problem)s, %(evidence)s, %(proposed)s, %(status)s, %(note)s,
        case when %(status)s = 'dismissed' then now() end,
        case when %(status)s = 'dismissed' then 'auditor' end)
returning id
"""


def submit(
    conn: psycopg.Connection,
    batch_id: uuid.UUID,
    items: list[Audited],
    *,
    model: str | None,
    version: str,
    auto_repair: bool,
    tenant: str,
) -> list[Outcome]:
    """Record the auditor's answers, mark each entry audited, apply what may be applied at
    once, and close the batch. Entries left out stay due. LookupError: no such batch."""
    with conn.cursor(row_factory=dict_row) as cursor:
        batch = cursor.execute(
            "select id, status, entry_ids, rules from audit_batches where id = %s for update",
            (batch_id,),
        ).fetchone()
        if batch is None:
            raise LookupError(batch_id)
        if batch["status"] != "leased":
            raise BatchClosed(batch["status"])
        entries = {
            row["key"]: row
            for row in cursor.execute(
                "select id, key, lemma_norm, pos, kind::text as kind, doc,"
                " published_at is not null and doc is not null as written"
                " from entries where id = any(%s)",
                (batch["entry_ids"],),
            )
        }
    outcomes: list[Outcome] = []
    automatic: list[uuid.UUID] = []
    for item in items:
        entry = entries.get(item.key)
        if entry is None:
            outcomes.append(Outcome(item.key, "not_leased", errors=["not in this batch"]))
            continue
        errors: list[str] = []
        rows = _answered(entry, batch["rules"].get(item.key, []), item.rules, errors)
        for finding in item.findings[:MAX_FINDINGS]:
            problem = _invalid(conn, entry, finding.type, finding.target_key, finding.proposed)
            if problem:
                errors.append(f"{finding.type} {finding.target_key}: {problem}")
                continue
            rows.append({"type": finding.type, "target": finding.target_key, "source": "ai",
                         "confidence": finding.confidence, "problem": finding.problem[:500],
                         "evidence": {}, "proposed": finding.proposed, "status": "open",
                         "note": None})  # fmt: skip
        stored = 0
        for row in rows:
            params = {**row, "id": new_id(), "batch": batch_id, "entry": entry["id"],
                      "key": entry["key"], "evidence": Jsonb(row["evidence"]),
                      "proposed": Jsonb(row["proposed"])}  # fmt: skip
            found = conn.execute(INSERT, params).fetchone()
            assert found is not None
            stored += row["status"] == "open"
            if (auto_repair and row["status"] == "open" and row["type"] in REPAIRS
                    and row["confidence"] == "high"):  # fmt: skip
                automatic.append(found[0])
        conn.execute(
            "update entries set audited_at = now(), audit_version = %s where id = %s",
            (version, entry["id"]),
        )
        outcomes.append(Outcome(item.key, "audited", stored, 0, errors))
    applied: dict[str, int] = defaultdict(int)
    for result in apply(conn, automatic, decided_by=AUTO_BY, note="applied at once: high"
                        " confidence", tenant=tenant):  # fmt: skip
        if result.outcome == "applied":
            applied[result.entry_key] += 1
    outcomes = [Outcome(o.key, o.outcome, o.findings, applied.get(o.key, 0), o.errors)
                for o in outcomes]  # fmt: skip
    counts = {
        "audited": sum(o.outcome == "audited" for o in outcomes),
        "findings": sum(o.findings for o in outcomes),
        "applied": sum(o.applied for o in outcomes),
    }
    conn.execute(
        "update audit_batches set status = 'submitted', submitted_at = now(), model = %s,"
        " audit_version = %s, results = %s where id = %s",
        (model, version, Jsonb(counts), batch_id),
    )
    return outcomes


def _answered(
    entry: dict[str, Any],
    raised: list[dict[str, Any]],
    answers: list[RuleAnswer],
    errors: list[str],
) -> list[dict[str, Any]]:
    """The rules' findings as the auditor answered them: confirmed ones stay open, dismissed
    ones are kept for the record, and unanswered ones wait for the owner."""
    by_id = {rule["id"]: rule for rule in raised}
    given = {answer.id: answer for answer in answers}
    for unknown in sorted(set(given) - set(by_id)):
        errors.append(f"{unknown}: no such rule finding")
    rows = []
    for rule_id, rule in by_id.items():
        answer = given.get(rule_id)
        proposed = rule["proposed"]
        if answer is not None and answer.verdict == "confirm" and answer.proposed is not None:
            if _shape(entry, rule["type"], answer.proposed) is None:
                proposed = answer.proposed
            else:
                errors.append(f"{rule_id}: the proposed fix doesn't fit; the rule's stays")
        rows.append({
            "type": rule["type"], "target": rule["target_key"], "source": "rules",
            "confidence": answer.confidence if answer else "medium",
            "problem": rule["problem"], "evidence": rule["evidence"], "proposed": proposed,
            "status": "dismissed" if answer and answer.verdict == "dismiss" else "open",
            "note": (answer.note if answer else "the auditor didn't answer it"),
        })  # fmt: skip
    return rows


def _invalid(
    conn: psycopg.Connection, entry: dict[str, Any], kind: str, target: str, proposed: Any
) -> str | None:
    """Why a finding can't be recorded as it is, or None."""
    if kind in REPAIRS:
        if not entry["written"]:
            return "nobody has written it yet; only its facts can be audited"
        if target != entry["key"] and target not in _keys(entry["doc"] or {}):
            return "no such key in the entry"
    elif target != entry["key"]:
        return "a finding about which entries exist names the entry's key"
    shape = _shape(entry, kind, proposed)
    if shape is not None:
        return shape
    if kind == "duplicate":
        keys = {proposed["retire"], proposed["keep"]}
        found = conn.execute(
            "select count(*) from entries where key = any(%s) and status <> 'retired'",
            (sorted(keys),),
        ).fetchone()
        if found is None or found[0] != 2:
            return "both keys must be entries in the dictionary"
    return None


def _shape(entry: dict[str, Any], kind: str, proposed: Any) -> str | None:
    """Why a proposed fix doesn't fit its type, or None."""
    if not isinstance(proposed, dict):
        return "the proposed fix must be an object"
    if kind == "cefr" and proposed.get("cefr") not in LEVELS:
        return f"cefr must be one of {', '.join(LEVELS)}"
    if kind in REPAIRS and kind != "cefr":
        fix = proposed.get("fix")
        if not isinstance(fix, str) or not 0 < len(fix) <= 500:
            return "fix must say what to change, in at most 500 characters"
    if kind in ("part_of_speech", "kind"):
        pos, word_kind = str(proposed.get("pos")), str(proposed.get("kind"))
        if pos not in get_args(POS) or word_kind not in get_args(Kind):
            return "pos and kind must be the entry model's"
        if not valid_pos(pos, word_kind):
            return f"{pos} can't go with {word_kind}"
        if kind == "kind" and (pos, word_kind) == (entry["pos"], entry["kind"]):
            return "that's the entry's own part of speech and kind"
    if kind == "part_of_speech":
        lemma = proposed.get("lemma")
        if not isinstance(lemma, str) or norm(lemma) != entry["lemma_norm"]:
            return "lemma must be the entry's headword"
    if kind == "duplicate":
        retire, keep = proposed.get("retire"), proposed.get("keep")
        if not (isinstance(retire, str) and isinstance(keep, str)) or retire == keep:
            return "retire and keep must name two entries"
        if entry["key"] not in (retire, keep):
            return "one of the two must be this entry"
    if kind == "headword":
        lemma = proposed.get("lemma")
        if not isinstance(lemma, str) or not lemma.strip() or norm(lemma) == entry["lemma_norm"]:
            return "lemma must be the better headword"
        if headword_rules.problems(lemma, entry["pos"]):
            return "; ".join(headword_rules.problems(lemma, entry["pos"]))
    return None


# 4. Applying and rejecting -----------------------------------------------------------------


@dataclass(frozen=True)
class Applied:
    id: uuid.UUID
    entry_key: str
    outcome: Literal["applied", "conflict", "skipped"]
    detail: str | None = None


def apply(
    conn: psycopg.Connection,
    finding_ids: list[uuid.UUID],
    *,
    decided_by: str,
    note: str | None,
    tenant: str,
) -> list[Applied]:
    """Apply open findings: content fixes send their entry to repair (one repair per entry,
    with every finding's notes), a missing part of speech is added, a wrong kind is retired
    and added again, a twin or a word to remove is retired. Each entry's change is all or
    nothing; a finding that can't be applied stays open, with the reason."""
    if not finding_ids:
        return []
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "select f.id, f.type, f.target_key, f.problem, f.proposed, f.status, f.entry_id,"
            " e.key as entry_key, e.lemma, e.pos, e.kind::text as kind,"
            " e.status::text as entry_status, e.doc,"
            " e.published_at is not null and e.doc is not null as written"
            " from audit_findings f join entries e on e.id = f.entry_id"
            " where f.id = any(%s) order by e.key, f.created_at for update of f",
            (finding_ids,),
        ).fetchall()
    results: list[Applied] = []
    repairs: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["status"] != "open":
            results.append(Applied(row["id"], row["entry_key"], "skipped", f"it's {row['status']}"))
        elif row["type"] in REPAIRS:
            repairs[row["entry_id"]].append(row)
        else:
            results.append(_change(conn, row, decided_by=decided_by, note=note, tenant=tenant))
    for findings in repairs.values():
        try:
            with conn.transaction():
                _repair(conn, findings, decided_by)
                _decided(conn, [row["id"] for row in findings], "applied", decided_by, note)
            results += [Applied(row["id"], row["entry_key"], "applied") for row in findings]
        except Conflict as error:
            results += [Applied(row["id"], row["entry_key"], "conflict", str(error))
                        for row in findings]  # fmt: skip
    return results


@dataclass(frozen=True)
class Rejected:
    count: int
    undone: list[str]  # entries whose repair at once was called off
    kept: list[str]  # "<key>: why" for repairs at once that couldn't be called off


def reject(
    conn: psycopg.Connection, finding_ids: list[uuid.UUID], *, decided_by: str, note: str | None
) -> Rejected:
    """The owner disagrees: the findings close, and the rules never raise them again. A
    finding applied at once (`auto:audit`) is rejected too, and its repair is called off while
    the factory hasn't started it: the entry is published again as it was."""
    count = conn.execute(
        "update audit_findings set status = 'rejected', decided_at = now(), decided_by = %s,"
        " note = coalesce(%s, note) where id = any(%s) and status = 'open'",
        (decided_by, note, finding_ids),
    ).rowcount
    with conn.cursor(row_factory=dict_row) as cursor:
        applied = cursor.execute(
            "select f.id, f.entry_id, e.key, e.status::text as status from audit_findings f"
            " join entries e on e.id = f.entry_id where f.id = any(%s) and f.status = 'applied'"
            " and f.decided_by = %s order by e.key for update of f",
            (finding_ids, AUTO_BY),
        ).fetchall()
    by_entry: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in applied:
        by_entry[row["entry_id"]].append(row)
    undone: list[str] = []
    kept: list[str] = []
    for entry_id, rows in by_entry.items():
        key, ids = rows[0]["key"], [row["id"] for row in rows]
        why = _cannot_undo(conn, entry_id, rows[0]["status"], {str(i) for i in ids})
        if why:
            kept.append(f"{key}: {why}")
            continue
        conn.execute(
            "update entries set status = 'published', updated_at = now() where id = %s",
            (entry_id,),
        )
        _decided(conn, ids, "rejected", decided_by, note)
        count += len(ids)
        undone.append(key)
    return Rejected(count, undone, kept)


def _cannot_undo(
    conn: psycopg.Connection, entry_id: uuid.UUID, status: str, ids: set[str]
) -> str | None:
    """Why a repair at once can't be called off, or None: the factory is on it or has
    rewritten the entry, or the repair carries other notes too."""
    if status == "generating":
        return "the routine is rewriting it now; send it to repair with your notes after its run"
    latest = conn.execute(
        "select produced_by->>'source', qa from entry_revisions where entry_id = %s"
        " order by created_at desc limit 1",
        (entry_id,),
    ).fetchone()
    if status != "needs_repair" or latest is None or latest[0] != "audit":
        return "rewritten already; send it to repair with your notes if the rewrite is wrong"
    qa = latest[1] or {}
    carried = {note.get("finding") for note in qa.get("audit") or []}
    if not carried <= ids or len((qa.get("review") or {}).get("notes") or []) != len(carried):
        return "its repair carries other notes too; send it to repair with your notes instead"
    return None


def _change(
    conn: psycopg.Connection, row: dict[str, Any], *, decided_by: str, note: str | None, tenant: str
) -> Applied:
    """A finding that changes which entries exist, applied in a savepoint."""
    proposed = row["proposed"]
    try:
        with conn.transaction():
            if row["type"] == "part_of_speech":
                curation.add(conn, lemma=proposed["lemma"], pos=proposed["pos"],
                             kind=proposed["kind"], decided_by=decided_by,
                             note=f"audit: {row['problem']}"[:500], tenant=tenant)  # fmt: skip
            elif (
                row["type"] == "kind"
                and entry_key("en", row["lemma"], proposed["pos"], proposed["kind"])
                == row["entry_key"]
            ):
                curation.set_pos(conn, row["entry_key"], proposed["pos"], by=decided_by,
                                 reason=row["problem"])  # fmt: skip
            elif row["type"] == "kind":
                curation.retire(conn, row["entry_key"])
                curation.add(conn, lemma=row["lemma"], pos=proposed["pos"], kind=proposed["kind"],
                             decided_by=decided_by, note=f"audit: {row['problem']}"[:500],
                             tenant=tenant)  # fmt: skip
            elif row["type"] == "duplicate":
                if not proposed.get("retire"):
                    raise Conflict("say which entry to retire first")
                curation.retire(conn, proposed["retire"])
                if proposed.get("pattern") and proposed.get("keep"):
                    curation.add_pattern(conn, proposed["keep"], proposed["pattern"],
                                         retired=proposed["retire"])  # fmt: skip
            elif row["type"] == "headword":
                curation.retire(conn, row["entry_key"])
                added = curation.add(conn, lemma=proposed["lemma"], pos=row["pos"],
                                     kind=proposed.get("kind", row["kind"]),
                                     decided_by=decided_by, tenant=tenant,
                                     note=f"audit: {row['problem']}"[:500])  # fmt: skip
                if proposed.get("pattern"):
                    curation.add_pattern(conn, added.key, proposed["pattern"])
            else:  # remove
                curation.retire(conn, row["entry_key"])
            _decided(conn, [row["id"]], "applied", decided_by, note)
    except (Conflict, LookupError, ValueError) as error:
        return Applied(row["id"], row["entry_key"], "conflict", str(error) or "no such entry")
    return Applied(row["id"], row["entry_key"], "applied")


def _repair(conn: psycopg.Connection, findings: list[dict[str, Any]], decided_by: str) -> None:
    """Send the entry to repair with the findings as notes: the factory rewrites it with every
    check, keeping its keys, and the published document stays shown meanwhile."""
    first = findings[0]
    if not first["written"]:
        raise Conflict("nobody has written it yet")
    if first["entry_status"] not in ("published", "needs_repair", "quarantined"):
        raise Conflict(f"it's {first['entry_status']}; apply it after the routine's run")
    notes = [_note(row) for row in findings]
    audit_notes = [
        {"finding": str(row["id"]), "type": row["type"], "where": row["target_key"],
         "problem": row["problem"], "proposed": row["proposed"]}
        for row in findings
    ]  # fmt: skip
    if first["entry_status"] == "needs_repair":  # keep what an earlier repair asked for
        earlier = conn.execute(
            "select qa from entry_revisions where entry_id = %s and outcome = 'needs_repair'"
            " order by created_at desc limit 1",
            (first["entry_id"],),
        ).fetchone()
        qa = earlier[0] if earlier else {}
        notes = list((qa.get("review") or {}).get("notes") or []) + notes
        audit_notes = list(qa.get("audit") or []) + audit_notes
    qa = {"errors": [], "warnings": [], "review": {"verdict": "fail", "notes": notes},
          "audit": audit_notes}  # fmt: skip
    conn.execute(
        "insert into entry_revisions (id, entry_id, doc, produced_by, qa, outcome)"
        " values (%s, %s, %s, %s, %s, 'needs_repair')",
        (new_id(), first["entry_id"], Jsonb(first["doc"]),
         Jsonb({"source": "audit", "decided_by": decided_by}), Jsonb(qa)),
    )  # fmt: skip
    conn.execute(
        "update entries set status = 'needs_repair', attempts = 0, updated_at = now()"
        " where id = %s",
        (first["entry_id"],),
    )


def _note(row: dict[str, Any]) -> str:
    proposed = row["proposed"] or {}
    fix = proposed.get("fix") or (f"Set the level to {proposed['cefr']}." if "cefr" in proposed
                                  else "")  # fmt: skip
    return f"{row['target_key']}: {row['problem']} {fix}".strip()


def _decided(
    conn: psycopg.Connection, ids: list[uuid.UUID], status: str, decided_by: str, note: str | None
) -> None:
    conn.execute(
        "update audit_findings set status = %s, decided_at = now(), decided_by = %s,"
        " note = coalesce(%s, note) where id = any(%s)",
        (status, decided_by, note, ids),
    )


# 5. The owner's view -----------------------------------------------------------------------

FINDINGS = """
from audit_findings f join entries e on e.id = f.entry_id
left join audit_batches b on b.id = f.batch_id
where (cast(%(status)s as text) is null or f.status = %(status)s)
  and (cast(%(type)s as text) is null or f.type = %(type)s)
  and (cast(%(source)s as text) is null or f.source = %(source)s)
  and (cast(%(confidence)s as text) is null or f.confidence = %(confidence)s)
  and (cast(%(q)s as text) is null or starts_with(e.lemma_norm, %(q)s))
  and (cast(%(key)s as text) is null or f.entry_key = %(key)s)
"""
FINDING_COLUMNS = """
select f.id, f.entry_key, e.lemma, e.status::text as entry_status,
       e.published_at is not null and e.doc is not null as written, e.approved_by as chosen_by,
       f.type, f.source, f.confidence, f.target_key, f.problem,
       f.evidence - 'fingerprint' as evidence, f.proposed, f.status, f.created_at,
       f.decided_at, f.decided_by, f.note, b.run_label as run
"""


def listing(
    conn: psycopg.Connection,
    *,
    status: str | None,
    kind: str | None,
    source: str | None,
    confidence: str | None,
    q: str | None,
    key: str | None,
    page: int,
    per_page: int,
) -> tuple[list[dict[str, Any]], int]:
    """One page of findings: open ones first, the most confident first, then the newest."""
    params = {"status": status, "type": kind, "source": source, "confidence": confidence,
              "q": q, "key": key}  # fmt: skip
    total = conn.execute("select count(*)" + FINDINGS, params).fetchone()
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            FINDING_COLUMNS + FINDINGS + " order by f.status = 'open' desc,"
            " case f.confidence when 'high' then 0 when 'medium' then 1 else 2 end,"
            " f.created_at desc, f.entry_key limit %(limit)s offset %(offset)s",
            params | {"limit": per_page, "offset": (page - 1) * per_page},
        ).fetchall()
    return rows, total[0] if total else 0


def stats(conn: psycopg.Connection) -> dict[str, Any]:
    """Where the audit stands: open findings by type, what was applied at once, how far the
    audit has come, the last run, and how often the owner agreed with each type of finding
    (the share applied of those the owner decided, over 90 days): trust, measured."""
    open_by_type = dict(conn.execute(
        "select type, count(*) from audit_findings where status = 'open' group by 1"
    ).fetchall())  # fmt: skip
    counts = conn.execute(
        "select (select count(*) from audit_findings where decided_by = %(auto)s"
        "        and decided_at > now() - interval '1 day'),"
        " (select count(*) from entries where audited_at is not null),"
        " (select count(*) from entries where audited_at is null and approved_at is not null"
        "        and status = 'published')",
        {"auto": AUTO_BY},
    ).fetchone() or (0, 0, 0)
    with conn.cursor(row_factory=dict_row) as cursor:
        last = cursor.execute(
            "select run_label as label, model, submitted_at,"
            " coalesce((results->>'audited')::int, 0) as audited,"
            " coalesce((results->>'findings')::int, 0) as findings,"
            " coalesce((results->>'applied')::int, 0) as applied"
            " from audit_batches where status = 'submitted' order by submitted_at desc limit 1"
        ).fetchone()
        decisions = cursor.execute(
            "select type, count(*) filter (where status = 'applied') as applied,"
            " count(*) filter (where status = 'rejected') as rejected from audit_findings"
            " where status in ('applied', 'rejected') and decided_by not in (%s, 'auditor')"
            " and decided_at > now() - interval '90 days' group by 1 order by 1",
            (AUTO_BY,),
        ).fetchall()
    return {
        "open": {kind: open_by_type.get(kind, 0) for kind in get_args(FindingType)},
        "applied_at_once_24h": counts[0],
        "entries_audited": counts[1],
        "published_never_audited": counts[2],
        "last_run": last,
        "your_decisions_90d": decisions,
    }
