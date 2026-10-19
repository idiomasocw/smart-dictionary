"""The factory's checks (design §6.4), one change at a time to a correct entry."""

from typing import Any

from factory_docs import DEFINING_WORDS, GO_DOC, GO_GLOSS, SPANISH_WORDS, go_doc
from smart_dictionary.core.text import norm
from smart_dictionary.factory.validate import (
    Context,
    Entry,
    Issue,
    _english_tokens,
    _ngrams,
    score,
    validate,
)

GO = Entry(key="en:go:v", lemma="go", pos="verb", kind="word")
CONTEXT = Context(
    vocab=frozenset(DEFINING_WORDS),
    spanish=frozenset(norm(word, "es") for word in SPANISH_WORDS),
    zipf={("es", norm(word, "es")): 4.0 for word in SPANISH_WORDS}
    | {("en", word): 5.0 for word in DEFINING_WORDS},
    ipa={"go": {"en-US": ["ˈgoʊ"], "en-GB": ["ˈɡəʊ"]}},
    glosses={"go": frozenset(_ngrams(_english_tokens(GO_GLOSS)))},
)


def found(doc: dict[str, Any], entry: Entry = GO) -> dict[str, set[str]]:
    """{check: severities} for the document's issues."""
    issues: dict[str, set[str]] = {}
    for issue in validate(entry, doc, CONTEXT):
        issues.setdefault(issue.check, set()).add(issue.severity)
    return issues


def sense(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["senses"][0]


def test_a_correct_entry_has_no_issues():
    assert validate(GO, GO_DOC, CONTEXT) == []


def test_v1_the_schema_and_its_closed_vocabularies():
    doc = go_doc()
    sense(doc)["registers"] = ["casual"]
    assert found(doc) == {"V1": {"error"}}


def test_v2_the_leased_facts_and_published_keys_stay():
    doc = go_doc()
    doc["lemma"] = "Go"  # the same forms, but not the leased headword
    assert found(doc) == {"V2": {"error"}}
    # Repairing a published entry: dropping an example's key is an error.
    published = Entry(**{**GO.__dict__, "doc": GO_DOC})
    repaired = go_doc()
    sense(repaired)["examples"][1]["key"] = "en:go:v#1.ex3"
    assert found(repaired, published) == {"V2": {"error"}}


def test_v3_every_example_shows_the_headword():
    doc = go_doc()
    sense(doc)["examples"][0]["text"] = "We traveled to the beach last weekend."
    assert found(doc) == {"V3": {"error"}}


def test_v3_phrasal_verbs_match_when_split():
    turn_off = Entry(key="en:turn_off:phrv", lemma="turn off", pos="verb", kind="phrasal_verb")
    doc = go_doc()
    doc.update(key="en:turn_off:phrv", lemma="turn off", kind="phrasal_verb", irregular_forms=[],
               pronunciations=[], pattern={"tokens": [{"lemma": "turn"}, {"text": "off"}],
                                           "separable": True, "max_gap": 3})  # fmt: skip
    for number, example in enumerate(sense(doc)["examples"], start=1):
        example["key"] = f"en:turn_off:phrv#1.ex{number}"
    sense(doc)["key"] = "en:turn_off:phrv#1"
    sense(doc)["examples"][0]["text"] = "Please turn off the lights when you leave."
    sense(doc)["examples"][1]["text"] = "She turned the old radio off before bed."
    assert "V3" not in found(doc, turn_off)
    sense(doc)["examples"][1]["text"] = "She turned the very old and noisy radio off."
    assert found(doc, turn_off)["V3"] == {"error"}


def test_v4_definitions():
    doc = go_doc()
    sense(doc)["definition"] = "to go from one place to another"
    assert found(doc) == {"V4": {"error"}}
    sense(doc)["definition"] = "to relocate oneself between distinct geographical locations"
    assert found(doc) == {"V4": {"warn"}}
    sense(doc)["definition"] = " ".join(["to move or travel from one place to another"] * 3)
    assert "error" in found(doc)["V4"]


def test_v5_spanish_text_reads_as_spanish():
    doc = go_doc()
    sense(doc)["l1"]["es-419"]["gloss"] = "to move or travel from one place to another"
    assert found(doc) == {"V5": {"error"}}


def test_v6_equivalents_are_real_spanish():
    doc = go_doc()
    equivalents = sense(doc)["l1"]["es-419"]["equivalents"]
    equivalents.append({"term": "irzar", "match": "near"})
    assert found(doc) == {"V6": {"warn"}}
    equivalents.append({"term": "movicar fluvar", "match": "near"})
    assert found(doc) == {"V6": {"error"}}


def test_v7_ipa():
    doc = go_doc()
    doc["pronunciations"][0]["ipa"] = "g0ʊ"
    assert found(doc) == {"V7": {"warn"}}
    doc["pronunciations"][0]["ipa"] = "ˈstrit"
    assert found(doc) == {"V7": {"warn"}}


def test_v8_two_senses_that_mean_the_same():
    doc = go_doc()
    second = go_doc()["senses"][0]
    second["key"] = "en:go:v#2"
    for number, example in enumerate(second["examples"], start=1):
        example["key"] = f"en:go:v#2.ex{number}"
    doc["senses"].append(second)
    assert found(doc) == {"V8": {"warn"}}


def test_v9_nothing_copied_from_wiktionary():
    doc = go_doc()
    sense(doc)["examples"][1]["text"] = (
        "They move from one place to another, especially to go shopping."
    )
    assert found(doc) == {"V9": {"error"}}


def test_v10_safety():
    doc = go_doc()
    sense(doc)["examples"][1]["text"] = "I usually go to work and kill time."
    assert found(doc) == {"V10": {"error"}}
    doc = go_doc()
    sense(doc)["definition"] = "an offensive way to move or travel from one place"
    assert found(doc)["V10"] == {"error"}


def test_v11_counts_and_the_style_guide_lengths():
    doc = go_doc()
    sense(doc)["examples"] = sense(doc)["examples"][:1]
    assert found(doc) == {"V11": {"error"}}
    doc = go_doc()
    sense(doc)["short_gloss"] = "move or travel from one place"
    sense(doc)["examples"][1]["text"] = "I go by bus."
    assert found(doc) == {"length": {"error"}}


def test_score_counts_warnings_and_the_reviewer():
    warning = Issue("V7", "warn", "en-US IPA", "far")
    assert score([], "pass") == 1.0
    assert score([warning], None) == 0.95
    assert score([warning, Issue("V8", "warn", "#2", "same")], "uncertain") == 0.65
    assert score([], "fail") == 0.5
