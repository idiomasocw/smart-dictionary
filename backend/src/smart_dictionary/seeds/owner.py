"""The owner's seed file, data/seeds/owner/owner-seeds.csv (scripts/normalize_owner_lists.py).

Columns: lemma, pos, kind, level, tags, variants, patterns, sources, original and flags.
Several values in one cell are separated by " | ", and a regional variant is written
"colour [UK]".
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

from smart_dictionary.core.entrydoc import CEFR, POS, EnglishRegion, Kind

COLUMNS = ("lemma", "pos", "kind", "level", "tags", "variants", "patterns", "sources",
           "original", "flags")  # fmt: skip
_REGIONAL = re.compile(r"^(?P<form>.+?)\s*\[(?P<regions>[^\]]+)\]$")


@dataclass(frozen=True)
class Variant:
    form: str
    regions: tuple[str, ...] = ()


@dataclass
class OwnerRow:
    lemma: str
    pos: str | None
    kind: str | None
    levels: list[str]
    tags: list[str]
    variants: list[Variant]
    patterns: list[str]
    sources: list[str]
    originals: list[str]
    flags: list[str]


def read_owner_seeds(path: Path) -> list[OwnerRow]:
    """Every row of the seed file, checked against the entry vocabulary."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name} has no {', '.join(sorted(missing))} column")
        rows = [_row(record, f"{path.name}:{line}") for line, record in enumerate(reader, 2)]
    return rows


def _row(record: dict[str, str], where: str) -> OwnerRow:
    lemma = (record["lemma"] or "").strip()
    if not lemma:
        raise ValueError(f"{where}: no lemma")
    pos = _one(record["pos"], get_args(POS), "part of speech", where)
    kind = _one(record["kind"], get_args(Kind), "kind", where)
    levels = _values(record["level"])
    if bad := set(levels) - set(get_args(CEFR)):
        raise ValueError(f"{where}: unknown level {', '.join(sorted(bad))}")
    return OwnerRow(
        lemma=lemma,
        pos=pos,
        kind=kind,
        levels=levels,
        tags=_values(record["tags"]),
        variants=[_variant(value, where) for value in _values(record["variants"])],
        patterns=_values(record["patterns"]),
        sources=_values(record["sources"]),
        originals=_values(record["original"]),
        flags=_values(record["flags"]),
    )


def _values(cell: str | None) -> list[str]:
    return [value.strip() for value in (cell or "").split(" | ") if value.strip()]


def _one(cell: str | None, allowed: tuple[str, ...], what: str, where: str) -> str | None:
    value = (cell or "").strip() or None
    if value is not None and value not in allowed:
        raise ValueError(f"{where}: unknown {what} {value!r}")
    return value


def _variant(text: str, where: str) -> Variant:
    match = _REGIONAL.match(text)
    if not match:
        return Variant(text)
    regions = tuple(region.strip() for region in match["regions"].split(","))
    if bad := set(regions) - set(get_args(EnglishRegion)):
        raise ValueError(f"{where}: unknown region {', '.join(sorted(bad))} in {text!r}")
    return Variant(match["form"], regions)
