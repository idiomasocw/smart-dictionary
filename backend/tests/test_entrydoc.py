import copy
from typing import Any

import pytest
from pydantic import ValidationError

from smart_dictionary.cli import SCHEMA_PATH
from smart_dictionary.core.entrydoc import EntryDoc, schema_text

ACTUALLY: dict[str, Any] = {
    "schema_version": 1,
    "key": "en:actually:adv",
    "language": "en",
    "lemma": "actually",
    "pos": "adverb",
    "kind": "word",
    "cefr": "A2",
    "pronunciations": [
        {"variety": "en-US", "ipa": "ˈæk.tʃu.ə.li"},
        {"variety": "en-GB", "ipa": "ˈæk.tʃu.ə.li"},
    ],
    "senses": [
        {
            "key": "en:actually:adv#1",
            "cefr": "A2",
            "definition": "used to say what is really true, especially when it is "
            "different from what someone thinks",
            "short_gloss": "in fact; really",
            "registers": [],
            "patterns": ["actually + verb", "Actually, + clause"],
            "l1": {
                "es-419": {
                    "gloss": "se usa para decir lo que es verdad, sobre todo cuando es "
                    "distinto de lo que alguien cree",
                    "equivalents": [
                        {"term": "en realidad", "match": "exact"},
                        {"term": "de hecho", "match": "near"},
                    ],
                },
                "pt-BR": {
                    "gloss": "usado para dizer o que é verdade",
                    "equivalents": [{"term": "na verdade", "match": "exact"}],
                },
            },
            "examples": [
                {
                    "key": "en:actually:adv#1.ex1",
                    "text": "He looks young, but he's actually 45.",
                    "cefr": "A2",
                    "translations": {
                        "es-419": "Parece joven, pero en realidad tiene 45 años.",
                        "pt-BR": "Ele parece jovem, mas na verdade tem 45 anos.",
                    },
                },
                {
                    "key": "en:actually:adv#1.ex2",
                    "text": "Actually, I don't agree with you.",
                    "translations": {"es-419": "La verdad es que no estoy de acuerdo contigo."},
                },
            ],
            "collocations": ["what actually happened"],
            "notes": [
                {
                    "key": "en:actually:adv#1.note1",
                    "kind": "false_friend",
                    "l1": "es",
                    "text": "«Actually» no significa «actualmente».",
                    "see": ["en:currently:adv"],
                }
            ],
        }
    ],
    "related": [{"type": "confusable", "key": "en:currently:adv"}],
}

RUN_INTO: dict[str, Any] = {
    "schema_version": 1,
    "key": "en:run_into:phrv",
    "language": "en",
    "lemma": "run into",
    "pos": "verb",
    "kind": "phrasal_verb",
    "cefr": "B1",
    "pattern": {"tokens": [{"lemma": "run"}, {"lemma": "into"}, {"slot": "sb"}]},
    "senses": [
        {
            "key": "en:run_into:phrv#1",
            "cefr": "B1",
            "definition": "to meet someone you know without planning to",
            "short_gloss": "meet by chance",
            "l1": {
                "es-419": {
                    "gloss": "encontrarse con alguien por casualidad",
                    "equivalents": [{"term": "toparse con", "match": "exact"}],
                }
            },
            "examples": [
                {
                    "key": "en:run_into:phrv#1.ex1",
                    "text": "I ran into my old teacher at the airport.",
                    "translations": {
                        "es-419": "Me topé con mi antigua profesora en el aeropuerto."
                    },
                }
            ],
        }
    ],
}


def broken(base: dict[str, Any], change) -> dict[str, Any]:
    doc = copy.deepcopy(base)
    change(doc)
    return doc


def test_sample_entries_validate_and_round_trip():
    for sample in (ACTUALLY, RUN_INTO):
        doc = EntryDoc.model_validate(sample)
        dumped = doc.model_dump(exclude_defaults=True)
        assert EntryDoc.model_validate(dumped) == doc
    assert "es-419" in EntryDoc.model_validate(ACTUALLY).model_dump()["senses"][0]["l1"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(pos="verb"), "does not match pos"),
        (lambda d: d["senses"][0].update(key="en:actual:adj#1"), "does not belong to"),
        (
            lambda d: d["senses"][0]["examples"][1].update(key="en:actually:adv#1.ex1"),
            "duplicate key",
        ),
        (lambda d: d["senses"][0]["l1"].pop("es-419"), "es-419 layer is required"),
        (
            lambda d: d["senses"][0]["examples"][0]["translations"].pop("es-419"),
            "translation is required",
        ),
        (
            lambda d: d["senses"][0]["notes"][0].update(key="en:actually:adv#1.ex9"),
            "is not a note key",
        ),
        (
            lambda d: d["pronunciations"][0].update(sense_keys=["en:actually:adv#7"]),
            "unknown sense",
        ),
        (lambda d: d.update(pattern={"tokens": [{"lemma": "actually"}]}), "no pattern"),
        (lambda d: d["senses"][0].update(retired=True), "at least one sense"),
        (
            lambda d: d["senses"][0]["l1"].update({"es-149": {"gloss": "x", "equivalents": []}}),
            "es-149",
        ),
    ],
)
def test_structural_problems_are_rejected(change, message):
    with pytest.raises(ValidationError, match=message):
        EntryDoc.model_validate(broken(ACTUALLY, change))


def test_multiword_entries_need_a_pattern_and_matching_pos():
    with pytest.raises(ValidationError, match="need a pattern"):
        EntryDoc.model_validate(broken(RUN_INTO, lambda d: d.pop("pattern")))
    with pytest.raises(ValidationError, match="use pos 'verb'"):
        EntryDoc.model_validate(broken(RUN_INTO, lambda d: d.update(pos="phrase")))


def test_schema_file_is_current():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == schema_text(), (
        "run `uv run smart-dictionary schema export` and commit factory/schema/entry.v1.json"
    )
