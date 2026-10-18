"""Projections: the queryable tables derived from an entry's document (design §3.4, §6.4).

entry_forms, expression_patterns, senses and equivalents are rebuilt on every
publish and never edited by hand. Senses are upserted by key and soft-retired,
never deleted, because saved words and clips point at them (ADR-005).
"""

import uuid
from typing import Any

import psycopg
from lemminflect import getAllInflections
from psycopg.types.json import Jsonb

from smart_dictionary.core.entrydoc import EntryDoc
from smart_dictionary.core.text import norm, spacing_variants

UPOS = {"noun": "NOUN", "verb": "VERB", "adjective": "ADJ", "adverb": "ADV"}
VARIETY_LANGUAGE = {"es-419": "es", "pt-BR": "pt", "ar": "ar"}

Form = tuple[str, str | None, str]  # (display form, features, source)


def english_forms(doc: EntryDoc) -> dict[str, Form]:
    """Every normalized form that should find this entry, keyed by the normalized text."""
    forms: dict[str, Form] = {}

    def add(form: str, features: str | None, source: str) -> None:
        for variant in spacing_variants(form):
            forms.setdefault(variant, (form, features, source))

    def inflect(lemma: str) -> None:
        if doc.kind == "word" and doc.pos in UPOS and " " not in lemma:
            for tag, values in getAllInflections(lemma, upos=UPOS[doc.pos]).items():
                for value in values:
                    add(value, tag, "lemminflect")
        elif doc.kind == "phrasal_verb":
            head, _, rest = lemma.partition(" ")
            for tag, values in getAllInflections(head, upos="VERB").items():
                for value in values:
                    add(f"{value} {rest}", tag, "lemminflect")

    add(doc.lemma, None, "doc")
    for variant in doc.variants:
        add(variant.form, None, "variant")
    for irregular in doc.irregular_forms:
        add(irregular.form, irregular.features, "doc")
    inflect(doc.lemma)
    # A spelling variant inflects in its own spelling: realise -> realised, colour -> colours.
    for variant in doc.variants:
        inflect(variant.form)
    return forms


def rebuild(conn: psycopg.Connection, entry_id: uuid.UUID, doc: EntryDoc) -> None:
    _forms(conn, entry_id, doc)
    _pattern(conn, entry_id, doc)
    _senses(conn, entry_id, doc)
    _equivalents(conn, doc)


def _forms(conn: psycopg.Connection, entry_id: uuid.UUID, doc: EntryDoc) -> None:
    conn.execute("delete from entry_forms where entry_id = %s", (entry_id,))
    rows = [
        (entry_id, form, form_norm, features, source)
        for form_norm, (form, features, source) in english_forms(doc).items()
    ]
    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into entry_forms (entry_id, form, form_norm, phonetic, features, source)"
            " values (%s, %s, %s, case when position(' ' in %s) = 0"
            " then extensions.dmetaphone(%s) end, %s, %s)",
            [(eid, form, fn, fn, fn, feats, src) for eid, form, fn, feats, src in rows],
        )


def _pattern(conn: psycopg.Connection, entry_id: uuid.UUID, doc: EntryDoc) -> None:
    if doc.pattern is None:
        conn.execute("delete from expression_patterns where entry_id = %s", (entry_id,))
        return
    tokens: list[dict[str, Any]] = [token.model_dump() for token in doc.pattern.tokens]
    conn.execute(
        "insert into expression_patterns (entry_id, tokens, separable, max_gap)"
        " values (%s, %s, %s, %s) on conflict (entry_id) do update"
        " set tokens = excluded.tokens, separable = excluded.separable, max_gap = excluded.max_gap",
        (entry_id, Jsonb(tokens), doc.pattern.separable, doc.pattern.max_gap),
    )


def _senses(conn: psycopg.Connection, entry_id: uuid.UUID, doc: EntryDoc) -> None:
    keys: list[str] = []
    with conn.cursor() as cursor:
        for order, sense in enumerate(doc.senses, start=1):
            keys.append(sense.key)
            cursor.execute(
                "insert into senses"
                " (key, entry_id, ord, cefr, registers, domains, short_gloss, retired_at)"
                " values (%s, %s, %s, %s, %s, %s, %s, case when %s then now() end)"
                " on conflict (key) do update set ord = excluded.ord, cefr = excluded.cefr,"
                " registers = excluded.registers, domains = excluded.domains,"
                " short_gloss = excluded.short_gloss,"
                " retired_at = case when %s then coalesce(senses.retired_at, now()) end",
                (
                    sense.key,
                    entry_id,
                    order,
                    sense.cefr,
                    list(sense.registers),
                    sense.domains,
                    sense.short_gloss,
                    sense.retired,
                    sense.retired,
                ),
            )
    # Senses the document no longer lists keep their key and are retired.
    conn.execute(
        "update senses set retired_at = coalesce(retired_at, now())"
        " where entry_id = %s and not (key = any(%s))",
        (entry_id, keys),
    )


def _equivalents(conn: psycopg.Connection, doc: EntryDoc) -> None:
    keys = [sense.key for sense in doc.senses]
    conn.execute("delete from equivalents where sense_key = any(%s)", (keys,))
    rows: list[tuple] = []
    for sense in doc.senses:
        layers = sense.l1.model_dump(exclude_none=True)
        for variety, layer in layers.items():
            language = VARIETY_LANGUAGE[variety]
            seen: set[str] = set()
            for rank, equivalent in enumerate(layer["equivalents"], start=1):
                term_norm = norm(equivalent["term"], language)
                if not term_norm or term_norm in seen:
                    continue
                seen.add(term_norm)
                rows.append(
                    (
                        sense.key,
                        variety,
                        equivalent["term"],
                        term_norm,
                        rank,
                        equivalent["match"],
                        equivalent.get("regions") or [],
                    )
                )
    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into equivalents (sense_key, variety, term, term_norm, rank, match, regions)"
            " values (%s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
