"""A correct entry document for "go", shared by the validator and submit tests, and the words
its Spanish and its definitions use (the reference data they need to pass)."""

import copy
from typing import Any

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
