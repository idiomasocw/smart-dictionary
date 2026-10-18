"""Wiktionary facts from the kaikki.org extract (JSON Lines, one entry per line).

Kept: frequent single words and listed headwords (decided by `keep_word`), every
phrasal verb, idiom and phrase, multiword words common enough to pass `keep_word`,
and proper nouns whose exact spelling is listed (April, Monday). Spelling variants,
the US spellings and alternative forms that Wiktionary files as pointers (catalog,
okay), are kept with `variant_of` naming the main spelling. That is recorded per entry
(one per part of speech and etymology), so "by" can have a noun entry that is a variant
of "bye" while its preposition entry is a word in its own right. Entries whose senses
are all inflected-form pointers are skipped, and so are entries whose senses are all
obsolete or archaic, unless a list includes the word. Glosses stay internal (CC BY-SA).
"""

import gzip
import json
import logging
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal

from psycopg.types.json import Jsonb

from smart_dictionary.core.text import norm
from smart_dictionary.importers.pos import WIKTEXTRACT_POS

COLUMNS = ["dataset_id", "word", "word_norm", "pos", "pos_raw", "kind", "glosses",
           "translations", "ipa", "forms", "tags", "variant_of"]  # fmt: skip
L1_LANGUAGES = ("es", "pt", "ar")
MAX_GLOSSES, MAX_TRANSLATIONS, MAX_FORMS, MAX_IPA, MAX_TAGS = 8, 10, 12, 3, 20

# What `keep_word` is asked about: a single word, a multiword item, a proper noun, or a
# word whose senses Wiktionary marks all obsolete or archaic.
Candidate = Literal["word", "multiword", "name", "archaic"]
KeepWord = Callable[[str, str, Candidate], bool]  # (word, normalized word, candidate) -> keep?

_POINTER_TAGS = {"form-of", "alt-of"}
_DEAD_TAGS = {"obsolete", "archaic"}
# A spelling variant is an alt-of sense marked as a regional spelling or an alternative form
# and carrying no other tags than these. Register and dialect tags (colloquial, Geordie),
# abbreviations, misspellings and pronunciation spellings all rule a sense out.
_REGIONAL = {"US", "UK", "Canada", "Commonwealth", "Ireland", "Australia", "New-Zealand"}
_VARIANT_MARKS = {"alternative", *_REGIONAL}
_VARIANT_TAGS = {"alt-of", "standard", "uncommon", "idiomatic", "countable", "uncountable",
                 "not-comparable", "comparative-only", "plural", "plural-only",
                 "plural-normally", "no-plural", "singular", "collective", "transitive",
                 "intransitive", "attributive", "invariable", "usually", "often", "sometimes",
                 "especially", *_VARIANT_MARKS}  # fmt: skip
# A variant's target is a spelling: letters and the punctuation words carry, at most three
# words longer than the entry. Anything else, such as a whole description, is a parsing
# artifact.
_SPELLING = re.compile(r"[^\W\d_](?:[^\W\d_]|[ .,'-])*")
_MAX_EXTRA_WORDS = 3
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
    word_norm = norm(word)
    glossed = [sense for sense in entry.get("senses") or [] if sense.get("glosses")]
    senses = [sense for sense in glossed if not _POINTER_TAGS & _tags(sense)]
    variant_senses, variant_of = _variants(glossed, word_norm, keep_word)
    if not senses and not variant_of:
        return None
    archaic = not variant_of and all(_tags(sense) & _DEAD_TAGS for sense in senses)

    described = [*senses, *variant_senses]
    all_tags: set[str] = set().union(*map(_tags, described)) - _POINTER_TAGS
    categories = {_category_name(c) for sense in described for c in sense.get("categories") or []}
    multiword = " " in word
    kind = _kind(pos, multiword, categories, all_tags)
    candidate = _candidate(pos_raw, archaic, multiword)
    gated = kind == "word" or candidate in ("name", "archaic")
    if gated and not keep_word(word, word_norm, candidate):
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
        "variant_of": variant_of,
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
                item["variant_of"],
            )


def _tags(sense: dict[str, Any]) -> set[str]:
    return set(sense.get("tags") or [])


def _category_name(category: Any) -> str:
    return category.get("name", "") if isinstance(category, dict) else str(category)


def _candidate(pos_raw: str, archaic: bool, multiword: bool) -> Candidate:
    if pos_raw == "name":
        return "name"
    if archaic:
        return "archaic"
    return "multiword" if multiword else "word"


def _variants(
    senses: list[dict[str, Any]], word_norm: str, keep_word: KeepWord
) -> tuple[list[dict[str, Any]], list[str]]:
    """The senses that make this entry a spelling variant, and the normalized main spellings
    they name.

    A regional spelling always counts. A plain alternative form counts only when its target
    is a word the subset keeps too, which rules out rare homographs such as "with" as an
    alternative form of "withe".
    """
    found: list[dict[str, Any]] = []
    targets: list[str] = []
    for sense in senses:
        tags = _tags(sense)
        if "alt-of" not in tags or not tags & _VARIANT_MARKS or not tags <= _VARIANT_TAGS:
            continue
        pointers = sense.get("alt_of") or []
        first = pointers[0] if pointers and isinstance(pointers[0], dict) else {}
        target = (first.get("word") or "").strip()
        target_norm = norm(target)
        if target_norm == word_norm or not _SPELLING.fullmatch(target_norm):
            continue
        if len(target_norm.split()) > len(word_norm.split()) + _MAX_EXTRA_WORDS:
            continue
        if not tags & _REGIONAL:
            candidate: Candidate = "multiword" if " " in target else "word"
            if not keep_word(target, target_norm, candidate):
                continue
        found.append(sense)
        if target_norm not in targets:
            targets.append(target_norm)
    return found, targets


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
