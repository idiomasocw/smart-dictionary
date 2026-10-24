"""The entry document (system design §3.5): one validated JSON document per entry.

These models are the single source of truth. The JSON Schema handed to the
content factory (factory/schema/entry.v1.json), the validators and the API
types are all generated from them.

Structural rules live here: shapes, closed vocabularies, the key hierarchy and
the required Spanish layer. Editorial rules (lengths, counts, wording, language
detection) belong to the factory validators, because structured outputs can't
express them and they need context such as word lists.
"""

import json
from collections.abc import Iterator
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from smart_dictionary.core.keys import code_for, parse_key

SCHEMA_ID = "urn:smart-dictionary:entry:v1"

CEFR = Literal["A1", "A2", "B1", "B2", "C1", "C2"]
POS = Literal["noun", "verb", "adjective", "adverb", "pronoun", "determiner", "preposition",
              "conjunction", "interjection", "numeral", "phrase"]  # fmt: skip
Kind = Literal["word", "phrasal_verb", "idiom", "phrase"]
EnglishVariety = Literal["en-US", "en-GB"]
EnglishRegion = Literal["US", "UK", "AU", "CA", "IE", "NZ"]
Register = Literal["formal", "informal", "slang", "offensive", "literary", "old-fashioned",
                   "technical"]  # fmt: skip
NoteKind = Literal["false_friend", "common_mistake", "pronunciation", "grammar", "usage", "culture"]
RelationType = Literal["synonym", "antonym", "family", "confusable", "see_also"]
# Open positions in a multiword pattern. "one's" and "oneself" refer back to the subject
# (keep one's word, kick oneself); "sb's" is someone else's (pull sb's leg).
Slot = Literal["sb", "sth", "sb/sth", "sb's", "one's", "oneself", "somewhere", "doing sth"]

# The kind whose part of speech is fixed: a phrasal verb is a verb. An idiom or a phrase takes
# the word class it works as in a sentence (as long as: conjunction; break the ice: verb), or
# "phrase" for a whole sentence with no single function (a proverb, a formula). The category
# and the function together: the policy of 2026-10-24, docs/policies/categories-and-headwords.md.
KIND_POS: dict[str, str] = {"phrasal_verb": "verb"}


def valid_pos(pos: str, kind: str) -> bool:
    """The part of speech can go with the kind: a word takes any word class, a phrasal verb is
    a verb, and an idiom or a phrase takes its function, or "phrase"."""
    if kind == "word":
        return pos != "phrase"
    return pos == KIND_POS.get(kind, pos)


class _Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )


class Pronunciation(_Model):
    variety: EnglishVariety
    ipa: str
    sense_keys: list[str] = Field(
        default_factory=list, description="Only for homographs pronounced differently per sense"
    )


class Variant(_Model):
    form: str = Field(description="Spelling variant, e.g. 'colour'")
    regions: list[EnglishRegion] = Field(default_factory=list)


class IrregularForm(_Model):
    form: str
    features: str = Field(description="UniMorph-style features, e.g. 'V;PST' or 'N;PL'")


class Equivalent(_Model):
    term: str
    match: Literal["exact", "near", "approx", "paraphrase"]
    regions: list[str] = Field(
        default_factory=list,
        description="ISO 3166-1 alpha-2 codes where the term is regional; empty means neutral",
    )
    note: str | None = None


class L1Layer(_Model):
    gloss: str
    equivalents: list[Equivalent]


class L1Layers(_Model):
    """One layer per student language: es-419 is required in v1; pt-BR and ar come later."""

    es_419: L1Layer | None = Field(default=None, alias="es-419")
    pt_br: L1Layer | None = Field(default=None, alias="pt-BR")
    ar: L1Layer | None = None


class Translations(_Model):
    es_419: str | None = Field(default=None, alias="es-419")
    pt_br: str | None = Field(default=None, alias="pt-BR")
    ar: str | None = None


class Example(_Model):
    key: str
    text: str
    cefr: CEFR | None = None
    translations: Translations


class Note(_Model):
    key: str
    kind: NoteKind
    l1: Literal["es", "pt", "ar"] | None = Field(
        default=None, description="Student language the note is for; null means everyone"
    )
    text: str
    see: list[str] = Field(default_factory=list)


class Grammar(_Model):
    countability: Literal["countable", "uncountable", "both"] | None = None
    transitivity: Literal["transitive", "intransitive", "both"] | None = None


class Sense(_Model):
    key: str
    cefr: CEFR
    definition: str
    short_gloss: str
    registers: list[Register] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    regions: list[EnglishRegion] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    grammar: Grammar | None = None
    l1: L1Layers
    examples: list[Example]
    collocations: list[str] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    retired: bool = False


class Related(_Model):
    type: RelationType
    key: str
    note: str | None = None


class LemmaToken(_Model):
    lemma: str


class TextToken(_Model):
    text: str


class SlotToken(_Model):
    slot: Slot


class Pattern(_Model):
    tokens: list[LemmaToken | TextToken | SlotToken]
    separable: bool = False
    max_gap: int = 0


class EntryDoc(_Model):
    schema_version: Literal[1]
    key: str
    language: Literal["en"]
    lemma: str
    pos: POS
    kind: Kind
    cefr: CEFR
    pronunciations: list[Pronunciation] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    irregular_forms: list[IrregularForm] = Field(default_factory=list)
    pattern: Pattern | None = None
    senses: list[Sense]
    related: list[Related] = Field(default_factory=list)
    provisional: bool = False

    @model_validator(mode="after")
    def _structure(self) -> Self:
        problems = list(structure_problems(self))
        if problems:
            raise ValueError("; ".join(problems))
        return self


def structure_problems(doc: EntryDoc) -> Iterator[str]:
    """Every structural problem in the document, so callers can report them all at once."""
    try:
        parsed = parse_key(doc.key)
    except ValueError as error:
        yield str(error)
        return
    if parsed.level != "entry":
        yield f"{doc.key!r} is not an entry key"
    if parsed.language != doc.language:
        yield f"key language {parsed.language!r} differs from {doc.language!r}"
    if parsed.code != code_for(doc.pos, doc.kind):
        yield f"key code {parsed.code!r} does not match pos {doc.pos!r} and kind {doc.kind!r}"

    if doc.kind == "word" and doc.pattern is not None:
        yield "single words have no pattern"
    if doc.kind != "word":
        if doc.pattern is None:
            yield f"{doc.kind} entries need a pattern"
        if not valid_pos(doc.pos, doc.kind):
            yield f"{doc.kind} entries use pos {KIND_POS[doc.kind]!r}"

    if not any(not sense.retired for sense in doc.senses):
        yield "at least one sense must be active"

    seen: set[str] = set()
    sense_keys: set[str] = set()
    for sense in doc.senses:
        yield from _child_problems(sense.key, "sense", doc.key, seen)
        sense_keys.add(sense.key)
        if sense.l1.es_419 is None:
            yield f"{sense.key}: the es-419 layer is required"
        for example in sense.examples:
            yield from _child_problems(example.key, "example", sense.key, seen)
            if example.translations.es_419 is None:
                yield f"{example.key}: the es-419 translation is required"
        for note in sense.notes:
            yield from _child_problems(note.key, "note", sense.key, seen)

    for pronunciation in doc.pronunciations:
        for key in pronunciation.sense_keys:
            if key not in sense_keys:
                yield f"pronunciation refers to unknown sense {key!r}"


def _child_problems(key: str, level: str, parent: str, seen: set[str]) -> Iterator[str]:
    if key in seen:
        yield f"duplicate key {key!r}"
    seen.add(key)
    try:
        parsed = parse_key(key)
    except ValueError as error:
        yield str(error)
        return
    if parsed.level != level:
        yield f"{key!r} is not a {level} key"
        return
    owner = parsed.entry_key if level == "sense" else parsed.sense_key
    if owner != parent:
        yield f"{key!r} does not belong to {parent!r}"


def json_schema() -> dict[str, Any]:
    schema = EntryDoc.model_json_schema(by_alias=True)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": SCHEMA_ID, **schema}


def schema_text() -> str:
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"
