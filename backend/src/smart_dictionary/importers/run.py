"""Run dataset imports: fetch, parse and load, one transaction per dataset."""

import importlib.metadata
import logging
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import psycopg

from smart_dictionary.core.text import norm
from smart_dictionary.importers import db, kaikki, sources
from smart_dictionary.importers.fetch import fetch, sha256_of
from smart_dictionary.importers.manifest import Dataset

log = logging.getLogger(__name__)

# The Wiktionary filter reads the lists and frequencies, so it goes last.
IMPORTER_ORDER = [
    "cefr_list",
    "ranked_list",
    "word_family",
    "wordfreq",
    "unimorph",
    "cmudict",
    "britfone",
    "kaikki",
]

# A load whose connection drops starts over from the spool, waiting RETRY_DELAY seconds
# times the attempt number in between.
LOAD_ATTEMPTS = 3
RETRY_DELAY = 10


def in_import_order(datasets: Iterable[Dataset]) -> list[Dataset]:
    return sorted(datasets, key=lambda dataset: IMPORTER_ORDER.index(dataset.importer))


def import_dataset(dataset: Dataset, *, path: Path | None = None) -> int:
    """Replace the dataset's rows with a fresh import; returns the row count.

    Parsing happens with no transaction open: the plan reads what it needs (the Wiktionary
    keep set) and closes its connection, and the rows are spooled. Only then does the load
    open its transaction, copying in short chunks (importers.db)."""
    if path is None:
        path = fetch(dataset)
    version = dataset.version
    if dataset.importer == "wordfreq":
        version = importlib.metadata.version("wordfreq")
    sha256 = sha256_of(path) if path else None
    with db.connect() as conn:
        table, columns, rows = _plan(conn, dataset, path)
    started = time.monotonic()
    with db.spooled(rows) as replay:
        parsed = time.monotonic()
        count = _load(dataset, table, columns, replay, version=version, sha256=sha256)
    log.info(
        "%s: %d rows into %s (parsed in %.0fs, loaded in %.0fs)",
        dataset.id,
        count,
        table,
        parsed - started,
        time.monotonic() - parsed,
    )
    return count


def _load(
    dataset: Dataset,
    table: str,
    columns: list[str],
    replay: Callable[[], Iterator[tuple]],
    *,
    version: str,
    sha256: str | None,
) -> int:
    """Replace the dataset's rows in one transaction, starting over if the connection drops.

    Errors from the server carry a SQLSTATE and are raised at once; a dropped connection has
    none. Its transaction rolled back, and if what was lost was the COMMIT's reply, the next
    attempt replaces the rows again, so starting over is always safe."""
    attempt = 1
    while True:
        try:
            with db.connect() as conn:
                db.replace_dataset(conn, dataset, version=version, sha256=sha256)
                count = db.copy_rows(conn, table, columns, replay())
                db.finish_dataset(conn, dataset.id, count)
            return count
        except psycopg.OperationalError as error:
            if error.sqlstate is not None or attempt == LOAD_ATTEMPTS:
                raise
            log.warning("%s: the connection dropped (%s); loading again", dataset.id, error)
            time.sleep(RETRY_DELAY * attempt)
            attempt += 1


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
        case "ranked_list":
            rows = sources.ranked_list_rows(
                _file(path),
                dataset.id,
                lemma_column=options["lemma_column"],
                rank_column=options["rank_column"],
                encoding=options.get("encoding", sources.LIST_ENCODING),
            )
            return "ref_lists", sources.LIST_COLUMNS, rows
        case "word_family":
            encoding = options.get("encoding", sources.LIST_ENCODING)
            rows = sources.word_family_rows(_file(path), dataset.id, encoding=encoding)
            return "ref_lists", sources.LIST_COLUMNS, rows
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
    # Listed means on an open list or a seed candidate, with its variants: the owner's lists
    # override every filter, and a US headword needs its UK spelling's entry (catalogue).
    lists = conn.execute(
        "select lemma, lemma_norm from ref_lists"
        " union all select lemma, lemma_norm from seed_candidates"
    ).fetchall()
    variants = conn.execute(
        "select v->>'form' from seed_candidates, jsonb_array_elements(variants) v"
    ).fetchall()
    listed = {lemma_norm for _, lemma_norm in lists} | {norm(form) for (form,) in variants}
    # Proper nouns need a list's exact spelling: "April", but not the town "Accident".
    listed_spellings = {lemma for lemma, _ in lists} | {form for (form,) in variants}

    from wordfreq import zipf_frequency

    def keep(word: str, word_norm: str, candidate: kaikki.Candidate) -> bool:
        if candidate == "name":
            return word in listed_spellings
        if word_norm in listed:
            return True
        # Words whose senses are all obsolete or archaic stay only when a list includes them.
        if candidate == "archaic":
            return False
        if word_norm in frequent:
            return True
        return candidate == "multiword" and zipf_frequency(word_norm, "en") >= min_multiword_zipf

    return keep


def _file(path: Path | None) -> Path:
    if path is None:
        raise ValueError("this importer needs a downloaded file")
    return path
