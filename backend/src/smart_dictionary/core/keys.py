"""Content keys: stable, human-readable IDs (system design §3.1, ADR-005).

    entry    {lang}:{slug}:{code}[~{homograph}]   en:run_into:phrv   en:bow:n~2
    sense    {entry}#{n}                          en:actually:adv#1
    example  {sense}.ex{n}                        en:actually:adv#1.ex2
    note     {sense}.note{n}                      en:actually:adv#1.note1

A key never changes once published, and its numbers are never reused. Sense
and example numbers are identities, not display order (that is `ord`).
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from smart_dictionary.core.text import APOSTROPHES, DASHES

POS_CODES = {
    "noun": "n",
    "verb": "v",
    "adjective": "adj",
    "adverb": "adv",
    "pronoun": "pron",
    "determiner": "det",
    "preposition": "prep",
    "conjunction": "conj",
    "interjection": "interj",
    "numeral": "num",
}
KIND_CODES = {"phrasal_verb": "phrv", "idiom": "idiom", "phrase": "phr"}

_CODE = "|".join(sorted({*POS_CODES.values(), *KIND_CODES.values()}, key=len, reverse=True))
_KEY = re.compile(
    r"^(?P<language>[a-z]{2}):(?P<slug>[\w'.\-]+):(?P<code>" + _CODE + r")"
    r"(?:~(?P<homograph>[2-9]|[1-9]\d+))?"
    r"(?:#(?P<sense>[1-9]\d*)(?:\.(?P<part>ex|note)(?P<part_number>[1-9]\d*))?)?$"
)

Level = Literal["entry", "sense", "example", "note"]


@dataclass(frozen=True)
class ParsedKey:
    language: str
    slug: str
    code: str
    homograph: int
    sense: int | None
    part: Literal["ex", "note"] | None
    part_number: int | None

    @property
    def level(self) -> Level:
        if self.sense is None:
            return "entry"
        if self.part is None:
            return "sense"
        return "example" if self.part == "ex" else "note"

    @property
    def entry_key(self) -> str:
        suffix = f"~{self.homograph}" if self.homograph > 1 else ""
        return f"{self.language}:{self.slug}:{self.code}{suffix}"

    @property
    def sense_key(self) -> str | None:
        return None if self.sense is None else f"{self.entry_key}#{self.sense}"


def slug(lemma: str) -> str:
    """Lowercase lemma with spaces as underscores; accents are kept (résumé ≠ resume)."""
    value = unicodedata.normalize("NFC", lemma).translate(APOSTROPHES).translate(DASHES)
    value = re.sub(r"\s+", "_", value.casefold().strip())
    value = re.sub(r"[^\w'.\-]", "", value)
    if not value.strip("_'.-"):
        raise ValueError(f"lemma {lemma!r} has no usable characters for a key")
    return value


def code_for(pos: str, kind: str) -> str:
    if kind == "word":
        return POS_CODES[pos]
    return KIND_CODES[kind]


def entry_key(language: str, lemma: str, pos: str, kind: str = "word", homograph: int = 1) -> str:
    if homograph < 1:
        raise ValueError("homograph numbers start at 1")
    suffix = f"~{homograph}" if homograph > 1 else ""
    return f"{language}:{slug(lemma)}:{code_for(pos, kind)}{suffix}"


def sense_key(entry: str, number: int) -> str:
    return _child(entry, "entry", f"#{number}", number)


def example_key(sense: str, number: int) -> str:
    return _child(sense, "sense", f".ex{number}", number)


def note_key(sense: str, number: int) -> str:
    return _child(sense, "sense", f".note{number}", number)


def parse_key(key: str) -> ParsedKey:
    match = _KEY.match(key)
    if match is None:
        raise ValueError(f"not a valid content key: {key!r}")
    part = match["part"]
    return ParsedKey(
        language=match["language"],
        slug=match["slug"],
        code=match["code"],
        homograph=int(match["homograph"] or 1),
        sense=int(match["sense"]) if match["sense"] else None,
        part="ex" if part == "ex" else "note" if part == "note" else None,
        part_number=int(match["part_number"]) if match["part_number"] else None,
    )


def _child(parent: str, parent_level: Level, suffix: str, number: int) -> str:
    if number < 1:
        raise ValueError("key numbers start at 1")
    if parse_key(parent).level != parent_level:
        raise ValueError(f"{parent!r} is not an {parent_level} key")
    return parent + suffix
