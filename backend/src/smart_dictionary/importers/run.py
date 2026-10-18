"""Run dataset imports: fetch, parse and load, one transaction per dataset."""

import importlib.metadata
import logging
from collections.abc import Iterable
from pathlib import Path

import psycopg

from smart_dictionary.importers import db, kaikki, sources
from smart_dictionary.importers.fetch import fetch, sha256_of
from smart_dictionary.importers.manifest import Dataset

log = logging.getLogger(__name__)

# The Wiktionary filter reads the lists and frequencies, so it goes last.
IMPORTER_ORDER = ["cefr_list", "wordfreq", "unimorph", "cmudict", "britfone", "kaikki"]


def in_import_order(datasets: Iterable[Dataset]) -> list[Dataset]:
    return sorted(datasets, key=lambda dataset: IMPORTER_ORDER.index(dataset.importer))


def import_dataset(dataset: Dataset, *, path: Path | None = None) -> int:
    """Replace the dataset's rows with a fresh import; returns the row count."""
    if path is None:
        path = fetch(dataset)
    version = dataset.version
    if dataset.importer == "wordfreq":
        version = importlib.metadata.version("wordfreq")
    with db.connect() as conn:
        db.replace_dataset(conn, dataset, version=version, sha256=sha256_of(path) if path else None)
        table, columns, rows = _plan(conn, dataset, path)
        count = db.copy_rows(conn, table, columns, rows)
        db.finish_dataset(conn, dataset.id, count)
    log.info("%s: %d rows into %s", dataset.id, count, table)
    return count


def _plan(
    conn: psycopg.Connection, dataset: Dataset, path: Path | None
) -> tuple[str, list[str], Iterable[tuple]]:
    options = dataset.options
    match dataset.importer:
        case "cefr_list":
            return (
                "ref_lists",
                sources.LIST_COLUMNS,
                sources.cefr_list_rows(_file(path), dataset.id),
            )
        case "wordfreq":
            rows = sources.wordfreq_rows(dataset.id, options["language"], int(options["top_n"]))
            return "ref_freq", sources.FREQ_COLUMNS, rows
        case "unimorph":
            rows = sources.unimorph_rows(_file(path), dataset.id, options["language"])
            return "ref_forms", sources.FORM_COLUMNS, rows
        case "cmudict":
            return "ref_ipa", sources.IPA_COLUMNS, sources.cmudict_rows(_file(path), dataset.id)
        case "britfone":
            return "ref_ipa", sources.IPA_COLUMNS, sources.britfone_rows(_file(path), dataset.id)
        case "kaikki":
            keep = _keep_word(
                conn, float(options["min_zipf"]), float(options["min_multiword_zipf"])
            )
            return "ref_wiktionary", kaikki.COLUMNS, kaikki.rows(_file(path), dataset.id, keep)
        case other:
            raise ValueError(f"unknown importer {other!r}")


def _keep_word(
    conn: psycopg.Connection, min_zipf: float, min_multiword_zipf: float
) -> kaikki.KeepWord:
    frequent = {
        word
        for (word,) in conn.execute(
            "select word from ref_freq where language = 'en' and zipf >= %s", (min_zipf,)
        )
    }
    if not frequent:
        raise RuntimeError("import wordfreq-en before kaikki-en: the filter needs frequencies")
    known = frequent | {lemma for (lemma,) in conn.execute("select lemma_norm from ref_lists")}

    from wordfreq import zipf_frequency

    def keep(word_norm: str, multiword: bool) -> bool:
        if word_norm in known:
            return True
        return multiword and zipf_frequency(word_norm, "en") >= min_multiword_zipf

    return keep


def _file(path: Path | None) -> Path:
    if path is None:
        raise ValueError("this importer needs a downloaded file")
    return path
