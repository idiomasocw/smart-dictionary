"""Parsers for each dataset format, yielding rows ready for COPY. No database access here."""

import csv
import re
from collections.abc import Iterator
from pathlib import Path

from psycopg.types.json import Jsonb

from smart_dictionary.core.text import norm
from smart_dictionary.importers.phonetics import arpabet_to_ipa, britfone_to_ipa
from smart_dictionary.importers.pos import CEFR_LIST_POS

CEFR_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}
LIST_COLUMNS = ["dataset_id", "lemma", "lemma_norm", "pos", "pos_raw", "level", "rank", "extra"]
FREQ_COLUMNS = ["dataset_id", "language", "word", "zipf"]
FORM_COLUMNS = ["dataset_id", "language", "lemma", "lemma_norm", "form", "form_norm", "features"]
IPA_COLUMNS = ["dataset_id", "word", "word_norm", "variety", "ipa", "raw"]

_VARIANT_NUMBER = re.compile(r"\(\d+\)$")
_LIST_CORE_COLUMNS = {"headword", "pos", "CEFR"}


def cefr_list_rows(path: Path, dataset_id: str) -> Iterator[tuple]:
    """Open Language Profiles CSV (headword,pos,CEFR,...); `a/b` headwords are spelling variants."""
    with path.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            headword = (record.get("headword") or "").strip()
            if not headword:
                continue
            pos_raw = (record.get("pos") or "").strip() or None
            level = (record.get("CEFR") or "").strip().upper()
            variants = [part.strip() for part in headword.split("/") if part.strip()]
            extra = {
                key: value.strip()
                for key, value in record.items()
                if key
                and key not in _LIST_CORE_COLUMNS
                and isinstance(value, str)
                and value.strip()
            }
            if len(variants) > 1:
                extra["headword"] = headword
            seen: set[str] = set()
            for variant in variants:
                key = norm(variant)
                if not key or key in seen:
                    continue
                seen.add(key)
                yield (
                    dataset_id,
                    variant,
                    key,
                    CEFR_LIST_POS.get(pos_raw or ""),
                    pos_raw,
                    level if level in CEFR_LEVELS else None,
                    None,
                    Jsonb(extra) if extra else None,
                )


def wordfreq_rows(dataset_id: str, language: str, top_n: int) -> Iterator[tuple]:
    from wordfreq import top_n_list, zipf_frequency

    seen: set[str] = set()
    for word in top_n_list(language, top_n):
        key = norm(word, language)
        if not key or key in seen:
            continue
        seen.add(key)
        yield (dataset_id, language, key, round(zipf_frequency(word, language), 2))


def unimorph_rows(path: Path, dataset_id: str, language: str) -> Iterator[tuple]:
    """UniMorph TSV: lemma, form, features."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[0] or not parts[1]:
                continue
            lemma, form, features = parts[0], parts[1], parts[2]
            yield (
                dataset_id,
                language,
                lemma,
                norm(lemma, language),
                form,
                norm(form, language),
                features,
            )


def cmudict_rows(path: Path, dataset_id: str) -> Iterator[tuple]:
    """CMUdict: `word ARPABET... # comment`; alternative pronunciations are `word(2)`."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            content = line.split("#", 1)[0].strip()
            word, _, arpabet = content.partition(" ")
            if not word or not arpabet:
                continue
            word = _VARIANT_NUMBER.sub("", word)
            try:
                ipa = arpabet_to_ipa(arpabet)
            except ValueError:
                continue
            yield (dataset_id, word, norm(word), "en-US", ipa, arpabet.strip())


def britfone_rows(path: Path, dataset_id: str) -> Iterator[tuple]:
    """Britfone CSV: `WORD(n), p h ˈo n e s`."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            word, separator, phones = line.strip().partition(",")
            if not separator or not phones.strip():
                continue
            word = _VARIANT_NUMBER.sub("", word.strip()).lower()
            key = norm(word)
            if not key:
                continue
            yield (dataset_id, word, key, "en-GB", britfone_to_ipa(phones), phones.strip())
