"""Wiktionary facts from the kaikki.org extract (JSON Lines, one entry per line).

Kept: frequent single words and listed headwords (decided by `keep_word`), every
phrasal verb, idiom and phrase, and multiword words common enough to pass
`keep_word`. Entries whose senses are all inflected-form pointers, or all
obsolete or archaic, are skipped. Glosses stay internal (CC BY-SA).
"""

import gzip
import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from smart_dictionary.core.text import norm
from smart_dictionary.importers.pos import WIKTEXTRACT_POS

COLUMNS = ["dataset_id", "word", "word_norm", "pos", "pos_raw", "kind", "glosses",
           "translations", "ipa", "forms", "tags"]  # fmt: skip
L1_LANGUAGES = ("es", "pt", "ar")
MAX_GLOSSES, MAX_TRANSLATIONS, MAX_FORMS, MAX_IPA, MAX_TAGS = 8, 10, 12, 3, 20

KeepWord = Callable[[str, bool], bool]  # (normalized word, is multiword) -> keep?

_POINTER_TAGS = {"form-of", "alt-of"}
_DEAD_TAGS = {"obsolete", "archaic"}
_SKIP_FORM_TAGS = {"alternative", "obsolete", "archaic", "table-tags", "inflection-template",
                   "class", "romanization", "nonstandard", "misspelling", "dialectal",
                   "canonical", "rare"}  # fmt: skip
_US = {"US", "General-American"}
_UK = {"UK", "Received-Pronunciation"}

log = logging.getLogger(__name__)


def extract(entry: dict[str, Any], keep_word: KeepWord) -> dict[str, Any] | None:
    """The facts worth keeping from one entry, or None to skip it."""
    if entry.get("lang_code") != "en":
        return None
    pos_raw = entry.get("pos") or ""
    pos = WIKTEXTRACT_POS.get(pos_raw)
    word = (entry.get("word") or "").strip()
    if pos is None or not word:
        return None
    senses = [
        sense
        for sense in entry.get("senses") or []
        if sense.get("glosses") and not _POINTER_TAGS & set(sense.get("tags") or [])
    ]
    if not senses:
        return None
    sense_tags = [set(sense.get("tags") or []) for sense in senses]
    if all(tags & _DEAD_TAGS for tags in sense_tags):
        return None

    all_tags: set[str] = set().union(*sense_tags)
    categories = {_category_name(c) for sense in senses for c in sense.get("categories") or []}
    multiword = " " in word
    kind = _kind(pos, multiword, categories, all_tags)
    word_norm = norm(word)
    if kind == "word" and not keep_word(word_norm, multiword):
        return None
    return {
        "word": word,
        "word_norm": word_norm,
        "pos": pos,
        "pos_raw": pos_raw,
        "kind": kind,
        "glosses": [sense["glosses"][0] for sense in senses[:MAX_GLOSSES]],
        "translations": _translations(entry, senses),
        "ipa": _ipa(entry.get("sounds") or []),
        "forms": _forms(entry.get("forms") or [], word),
        "tags": sorted(all_tags)[:MAX_TAGS],
    }


def read_entries(path: Path) -> Iterator[dict[str, Any]]:
    with (
        gzip.open(path, "rt", encoding="utf-8")
        if path.suffix == ".gz"
        else path.open(encoding="utf-8")
    ) as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def rows(path: Path, dataset_id: str, keep_word: KeepWord) -> Iterator[tuple]:
    for count, entry in enumerate(read_entries(path), start=1):
        if count % 200_000 == 0:
            log.info("kaikki: %d entries read", count)
        item = extract(entry, keep_word)
        if item is not None:
            yield (
                dataset_id,
                item["word"],
                item["word_norm"],
                item["pos"],
                item["pos_raw"],
                item["kind"],
                item["glosses"],
                Jsonb(item["translations"]),
                Jsonb(item["ipa"]),
                item["forms"],
                item["tags"],
            )


def _category_name(category: Any) -> str:
    return category.get("name", "") if isinstance(category, dict) else str(category)


def _kind(pos: str, multiword: bool, categories: set[str], tags: set[str]) -> str:
    if not multiword:
        return "word"
    # Wiktionary files them per particle: 'English phrasal verbs formed with "up"'.
    if pos == "verb" and any(c.startswith("English phrasal verbs") for c in categories):
        return "phrasal_verb"
    if "English idioms" in categories or "idiomatic" in tags:
        return "idiom"
    if pos == "phrase":
        return "phrase"
    return "word"


def _translations(entry: dict[str, Any], senses: list[dict[str, Any]]) -> dict[str, list[str]]:
    items = list(entry.get("translations") or [])
    for sense in senses:
        items.extend(sense.get("translations") or [])
    found: dict[str, list[str]] = {}
    for item in items:
        language = item.get("lang_code") or item.get("code")
        term = (item.get("word") or "").strip()
        if language in L1_LANGUAGES and term:
            bucket = found.setdefault(language, [])
            if term not in bucket and len(bucket) < MAX_TRANSLATIONS:
                bucket.append(term)
    return found


def _ipa(sounds: list[dict[str, Any]]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for sound in sounds:
        ipa = (sound.get("ipa") or "").strip().strip("/[]")
        if not ipa:
            continue
        tags = set(sound.get("tags") or [])
        variety = "en-US" if tags & _US else "en-GB" if tags & _UK else "unspecified"
        bucket = found.setdefault(variety, [])
        if ipa not in bucket and len(bucket) < MAX_IPA:
            bucket.append(ipa)
    return found


def _forms(forms: list[dict[str, Any]], word: str) -> list[str]:
    found: list[str] = []
    for item in forms:
        form = (item.get("form") or "").strip()
        if not form or form == word or _SKIP_FORM_TAGS & set(item.get("tags") or []):
            continue
        if form not in found:
            found.append(form)
        if len(found) >= MAX_FORMS:
            break
    return found
