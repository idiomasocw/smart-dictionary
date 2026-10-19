"""Grounding packs: what the routine receives for each leased entry (design §6.2).

Facts, not prose. Wiktionary's glosses are CC BY-SA, so a pack carries at most HINT_WORDS
words of each, only to show which meanings exist; validator V9 keeps their wording out of
entries. Packs are built for the whole batch with one query per source.
"""

import uuid
from collections import defaultdict
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel

from smart_dictionary.content.projections import inflections

HINT_WORDS = 12
MAX_HINTS = 8
MAX_ES_CANDIDATES = 10
MAX_FORMS = 24

# Forms come from LemmInflect, as search's do (content.projections), with UniMorph adding
# any irregular forms it knows beyond those. UniMorph alone isn't enough: it has no plural
# for "house", only an annotation ("uncountable") in its place. Its features start with
# the part of speech, so a noun gets only noun forms, and a form must look like a word.
UNIMORPH_POS = {"noun": "N", "verb": "V", "adjective": "ADJ"}
NOT_FORMS = ["countable", "uncountable", "plural", "singular", "none"]
WORD_SHAPE = r"^[a-z][a-z' .-]*$"
# LemmInflect's Penn tags in the UniMorph-style notation entries use (irregular_forms).
PENN_FEATURES = {
    "NNS": "N;PL",
    "VBD": "V;PST",
    "VBN": "V;V.PTCP;PST",
    "VBG": "V;V.PTCP;PRS",
    "VBZ": "V;3;SG;PRS",
    "JJR": "ADJ;CMPR",
    "JJS": "ADJ;SPRL",
    "RBR": "ADV;CMPR",
    "RBS": "ADV;SPRL",
}


class Priors(BaseModel):
    cefr: str | None
    zipf: float | None
    lists: list[str]


class Form(BaseModel):
    form: str
    features: str | None


class WiktionaryHints(BaseModel):
    senses: list[str]
    es_candidates: list[str]


class Pack(BaseModel):
    key: str
    lemma: str
    pos: str
    kind: str
    priors: Priors
    variants: list[dict[str, Any]]
    patterns: list[str]
    flags: list[str]
    originals: list[str]
    forms: list[Form]
    ipa: dict[str, list[str]]
    wiktionary_hints: WiktionaryHints
    related_published: list[str]
    existing: dict[str, Any] | None
    repair: dict[str, Any] | None


def build(conn: psycopg.Connection, entry_ids: list[uuid.UUID]) -> list[Pack]:
    """One pack per entry, in queue order."""
    with conn.cursor(row_factory=dict_row) as cursor:
        entries = cursor.execute(
            "select id, key, lemma, lemma_norm, pos, kind::text as kind, cefr, freq_zipf,"
            " sources, leased_from::text as leased_from, doc"
            " from entries where id = any(%s) order by priority desc, key",
            (entry_ids,),
        ).fetchall()
        lemmas = sorted({entry["lemma_norm"] for entry in entries})
        seeds = {
            row["entry_id"]: row
            for row in cursor.execute(
                "select entry_id, variants, patterns, flags, originals from seed_candidates"
                " where entry_id = any(%s)",
                (entry_ids,),
            )
        }
        forms = _grouped(
            cursor.execute(
                "select lemma_norm as word, form, features, split_part(features, ';', 1) as head"
                " from ref_forms where language = 'en' and lemma_norm = any(%s)"
                " and not form_norm = any(%s) and form_norm ~ %s order by id",
                (lemmas, NOT_FORMS, WORD_SHAPE),
            )
        )
        ipa = _grouped(
            cursor.execute(
                "select word_norm as word, variety, ipa from ref_ipa"
                " where word_norm = any(%s) order by id",
                (lemmas,),
            )
        )
        wiktionary = _grouped(
            cursor.execute(
                "select word_norm as word, pos, kind, glosses, translations from ref_wiktionary"
                " where word_norm = any(%s) order by id",
                (lemmas,),
            )
        )
        published = _grouped(
            cursor.execute(
                "select lemma_norm as word, key from entries"
                " where lemma_norm = any(%s) and status in ('published', 'provisional')"
                " order by key",
                (lemmas,),
            )
        )
        repairs = {
            row["entry_id"]: row["qa"]
            for row in cursor.execute(
                "select distinct on (entry_id) entry_id, qa from entry_revisions"
                " where entry_id = any(%s) and outcome = 'needs_repair'"
                " order by entry_id, created_at desc",
                (entry_ids,),
            )
        }
    return [
        _pack(entry, seeds.get(entry["id"]), forms, ipa, wiktionary, published, repairs)
        for entry in entries
    ]


def _pack(
    entry: dict[str, Any],
    seed: dict[str, Any] | None,
    forms: dict[str, list[dict[str, Any]]],
    ipa: dict[str, list[dict[str, Any]]],
    wiktionary: dict[str, list[dict[str, Any]]],
    published: dict[str, list[dict[str, Any]]],
    repairs: dict[uuid.UUID, dict[str, Any]],
) -> Pack:
    word = entry["lemma_norm"]
    matching = [row for row in wiktionary.get(word, []) if _same_entry(row, entry)]
    zipf = entry["freq_zipf"]
    repairing = entry["leased_from"] == "needs_repair"
    return Pack(
        key=entry["key"],
        lemma=entry["lemma"],
        pos=entry["pos"],
        kind=entry["kind"],
        priors=Priors(
            cefr=entry["cefr"],
            zipf=float(zipf) if zipf is not None else None,
            lists=list(entry["sources"]),
        ),
        variants=list(seed["variants"]) if seed else [],
        patterns=list(seed["patterns"]) if seed else [],
        flags=list(seed["flags"]) if seed else [],
        originals=list(seed["originals"]) if seed else [],
        forms=_forms(entry, forms.get(word, [])),
        ipa=_by_variety(ipa.get(word, [])),
        wiktionary_hints=WiktionaryHints(
            senses=_unique([hint(gloss) for row in matching for gloss in row["glosses"]])[
                :MAX_HINTS
            ],
            es_candidates=_unique(
                [term for row in matching for term in (row["translations"] or {}).get("es", [])]
            )[:MAX_ES_CANDIDATES],
        ),
        related_published=[
            row["key"] for row in published.get(word, []) if row["key"] != entry["key"]
        ],
        existing=entry["doc"],
        repair=repairs.get(entry["id"]) if repairing else None,
    )


def _forms(entry: dict[str, Any], unimorph: list[dict[str, Any]]) -> list[Form]:
    """Inflected forms, never the headword itself: LemmInflect's, then UniMorph's extras."""
    lemma, pos, kind = entry["lemma"], entry["pos"], entry["kind"]
    found = [
        Form(form=form, features=PENN_FEATURES[tag])
        for form, tag in inflections(lemma, pos, kind)
        if tag in PENN_FEATURES and form != lemma
    ]
    known = {lemma} | {form.form for form in found}
    for row in unimorph:
        if kind == "word" and row["head"] == UNIMORPH_POS.get(pos) and row["form"] not in known:
            found.append(Form(form=row["form"], features=row["features"]))
            known.add(row["form"])
    return _unique(found)[:MAX_FORMS]


def _same_entry(row: dict[str, Any], entry: dict[str, Any]) -> bool:
    """A Wiktionary row describes this entry when the kind matches, and for words the POS."""
    return row["kind"] == entry["kind"] and (entry["kind"] != "word" or row["pos"] == entry["pos"])


def hint(gloss: str) -> str:
    """A gloss cut to HINT_WORDS words: enough to show the meaning, too little to copy."""
    words = gloss.split()
    return " ".join(words[:HINT_WORDS]) + (" …" if len(words) > HINT_WORDS else "")


def _by_variety(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row["ipa"] not in found[row["variety"]]:
            found[row["variety"]].append(row["ipa"])
    return dict(found)


def _grouped(rows: Any) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["word"]].append(row)
    return grouped


def _unique[T](items: list[T]) -> list[T]:
    seen: list[T] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
