"""Shared factory test data: a correct entry document for "go", the reference data it needs
to pass the checks, and a small world of seed candidates covering every queue rule."""

import copy
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from smart_dictionary.core.text import norm

GO_GLOSS = (
    "To move from one place to another, especially to a place farther away from the speaker"
    " or listener."
)
DEFINING_WORDS = ["to", "move", "or", "travel", "from", "one", "place", "another"]
SPANISH_WORDS = [
    "moverse",
    "o",
    "viajar",
    "de",
    "un",
    "lugar",
    "a",
    "otro",
    "fuimos",
    "la",
    "playa",
    "el",
    "fin",
    "semana",
    "pasado",
    "normalmente",
    "voy",
    "al",
    "trabajo",
    "en",
    "bus",
    "ir",
]

GO_DOC: dict[str, Any] = {
    "schema_version": 1,
    "key": "en:go:v",
    "language": "en",
    "lemma": "go",
    "pos": "verb",
    "kind": "word",
    "cefr": "A1",
    "pronunciations": [
        {"variety": "en-US", "ipa": "goʊ"},
        {"variety": "en-GB", "ipa": "ɡəʊ"},
    ],
    "irregular_forms": [
        {"form": "went", "features": "V;PST"},
        {"form": "gone", "features": "V;V.PTCP;PST"},
    ],
    "senses": [
        {
            "key": "en:go:v#1",
            "cefr": "A1",
            "definition": "to move or travel from one place to another",
            "short_gloss": "move; travel",
            "grammar": {"transitivity": "intransitive"},
            "l1": {
                "es-419": {
                    "gloss": "moverse o viajar de un lugar a otro",
                    "equivalents": [{"term": "ir", "match": "exact"}],
                }
            },
            "examples": [
                {
                    "key": "en:go:v#1.ex1",
                    "text": "We went to the beach last weekend.",
                    "cefr": "A1",
                    "translations": {"es-419": "Fuimos a la playa el fin de semana pasado."},
                },
                {
                    "key": "en:go:v#1.ex2",
                    "text": "I usually go to work by bus.",
                    "cefr": "A1",
                    "translations": {"es-419": "Normalmente voy al trabajo en bus."},
                },
            ],
        }
    ],
}


def go_doc() -> dict[str, Any]:
    """A fresh copy to change in a test."""
    return copy.deepcopy(GO_DOC)


def world(conn: psycopg.Connection) -> None:
    """Seed candidates for every queue rule, and the reference facts packs and checks use."""
    for dataset in ("t-forms", "t-ipa", "t-wiki", "ngsl", "t-freq"):
        conn.execute(
            "insert into ref_datasets (id, name, version, license, attribution, source_url)"
            " values (%s, %s, '1', 'test', 'test', 'file:///test')",
            (dataset, dataset),
        )
    for word in DEFINING_WORDS:
        conn.execute(
            "insert into ref_lists (dataset_id, lemma, lemma_norm) values ('ngsl', %s, %s)",
            (word, word),
        )
    words = [("es", norm(word, "es")) for word in SPANISH_WORDS] + [
        ("en", word) for word in DEFINING_WORDS
    ]
    for language, word in sorted(set(words)):
        conn.execute(
            "insert into ref_freq (dataset_id, language, word, zipf) values ('t-freq', %s, %s, 4)",
            (language, word),
        )
    for form, features in (
        ("went", "V;PST"),
        ("gone", "V;V.PTCP;PST"),
        ("goes", "V;3;SG;PRS"),
        ("gos", "N;PL"),  # go the noun: not in the verb's pack
        ("uncountable", "N;PL"),  # UniMorph annotation, not a form
    ):
        conn.execute(
            "insert into ref_forms (dataset_id, language, lemma, lemma_norm, form, form_norm,"
            " features) values ('t-forms', 'en', 'go', 'go', %s, %s, %s)",
            (form, form, features),
        )
    for variety, ipa in (("en-US", "goʊ"), ("en-GB", "ɡəʊ")):
        conn.execute(
            "insert into ref_ipa (dataset_id, word, word_norm, variety, ipa)"
            " values ('t-ipa', 'go', 'go', %s, %s)",
            (variety, ipa),
        )
    for word, pos, kind, glosses, spanish in (
        ("go", "verb", "word", [GO_GLOSS, "To leave; to depart."], ["ir", "irse", "ir"]),
        ("go", "noun", "word", ["A board game for two players."], ["go"]),
        ("run into", "verb", "phrasal_verb", ["To meet someone unexpectedly."], ["toparse con"]),
    ):
        conn.execute(
            "insert into ref_wiktionary (dataset_id, word, word_norm, pos, pos_raw, kind, glosses,"
            " translations) values ('t-wiki', %s, %s, %s, %s, %s, %s, %s)",
            (word, word, pos, pos, kind, glosses, Jsonb({"es": spanish})),
        )
    candidates: list[tuple[Any, ...]] = [
        # lemma, pos, kind, tier, zipf, levels, sources, decision, variants
        ("go", "verb", "word", 0, 6.70, ["A1"], ["owner:list.txt", "cefrj"], None, []),
        ("these", "pronoun", "word", 1, 6.04, ["A1"], ["cefrj"], None, []),
        ("realize", "verb", "word", 0, 4.83, ["A2"], ["owner:list.txt"], None,
         [{"form": "realise", "regions": ["UK"]}]),
        ("house", "verb", "word", 0, 5.71, ["C1"], ["owner:list.txt"], None, []),
        ("run into", "verb", "phrasal_verb", 0, None, [], ["owner:phrasal.txt"], None, []),
        ("zzword", None, None, 0, 3.0, [], ["owner:list.txt"], None, []),  # uncurated
        ("DNA", "phrase", "word", 0, 4.0, [], ["owner:list.txt"], None, []),  # no valid entry
        ("nope", "noun", "word", 0, 5.0, ["A1"], ["owner:list.txt"], "reject", []),
    ]  # fmt: skip
    for lemma, pos, kind, tier, zipf, levels, sources, decision, variants in candidates:
        conn.execute(
            "insert into seed_candidates (lemma, lemma_norm, pos, kind, tier, zipf, levels,"
            " sources, decision, variants, originals)"
            " values (%s, %s, %s, %s::entry_kind, %s, %s, %s, %s, %s, %s, %s)",
            (lemma, lemma, pos, kind, tier, zipf, levels, sources, decision, Jsonb(variants),
             [lemma]),
        )  # fmt: skip
