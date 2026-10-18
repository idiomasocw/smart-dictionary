"""Parsers for each dataset format, yielding rows ready for COPY. No database access here."""

import csv
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from smart_dictionary.core.text import norm
from smart_dictionary.importers.phonetics import arpabet_to_ipa, britfone_to_ipa
from smart_dictionary.importers.pos import CEFR_LIST_POS

CEFR_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}
LIST_COLUMNS = ["dataset_id", "lemma", "lemma_norm", "pos", "pos_raw", "level", "rank", "extra"]
FREQ_COLUMNS = ["dataset_id", "language", "word", "zipf"]
FORM_COLUMNS = ["dataset_id", "language", "lemma", "lemma_norm", "form", "form_norm", "features"]
IPA_COLUMNS = ["dataset_id", "word", "word_norm", "variety", "ipa", "raw"]
# Word lists default to UTF-8, with or without a byte-order mark.
LIST_ENCODING = "utf-8-sig"

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
            extra = _other_columns(record, _LIST_CORE_COLUMNS)
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


def ranked_list_rows(
    path: Path,
    dataset_id: str,
    *,
    lemma_column: str,
    rank_column: str,
    encoding: str = LIST_ENCODING,
) -> Iterator[tuple]:
    """A ranked headword CSV, such as the NGSL family's `*_stats.csv` (Lemma,Rank,SFI,U).

    Each list names its columns differently, so the manifest says which ones hold the
    lemma and the rank; the other columns (SFI, U, Band) are kept in `extra`.
    """
    with path.open(encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        missing = {lemma_column, rank_column} - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name} has no {', '.join(sorted(missing))} column")
        for record in reader:
            lemma = (record.get(lemma_column) or "").strip()
            key = norm(lemma)
            if not key:
                continue
            rank = (record.get(rank_column) or "").strip()
            extra = _other_columns(record, {lemma_column, rank_column})
            yield (
                dataset_id,
                lemma,
                key,
                None,
                None,
                None,
                int(rank) if rank.isdigit() else None,
                Jsonb(extra) if extra else None,
            )


def word_family_rows(
    path: Path, dataset_id: str, *, encoding: str = LIST_ENCODING
) -> Iterator[tuple]:
    """Headword-then-forms CSV, such as the NGSL family's `*_lemmatized_for_teaching.csv`.

    Lines starting with `##` are comments. The forms are kept in `extra`.
    """
    with path.open(encoding=encoding, newline="") as handle:
        lines = (line for line in handle if not line.startswith("##"))
        for fields in csv.reader(lines):
            words = [field.strip() for field in fields if field.strip()]
            key = norm(words[0]) if words else ""
            if not key:
                continue
            forms = words[1:]
            yield (
                dataset_id,
                words[0],
                key,
                None,
                None,
                None,
                None,
                Jsonb({"forms": forms}) if forms else None,
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


def _other_columns(record: Mapping[Any, Any], core: set[str]) -> dict[str, str]:
    """A CSV record's remaining non-empty columns, kept as given."""
    return {
        key: value.strip()
        for key, value in record.items()
        if key and key not in core and isinstance(value, str) and value.strip()
    }
