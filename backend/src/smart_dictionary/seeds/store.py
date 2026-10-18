"""Seed candidates in the database: replacing them on import, exporting them for review."""

import csv
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from smart_dictionary.importers.db import copy_rows
from smart_dictionary.seeds.builder import Candidate

COLUMNS = ["lemma", "lemma_norm", "pos", "kind", "homograph", "sources", "levels", "zipf", "tier",
           "variants", "patterns", "flags", "originals"]  # fmt: skip
EXPORT_COLUMNS = ["tier", "zipf", "lemma", "pos", "kind", "homograph", "levels", "variants",
                  "patterns", "sources", "flags", "decision"]  # fmt: skip


def store(conn: psycopg.Connection, candidates: list[Candidate]) -> tuple[int, int]:
    """Replace the undecided candidates; the ones someone decided on, or queued as entries,
    keep their decision, entry and homograph number. Returns (inserted, updated)."""
    decided = {
        (lemma, pos, kind): (row_id, (lemma_norm, pos, kind, homograph))
        for row_id, lemma, lemma_norm, pos, kind, homograph in conn.execute(
            "select id, lemma, lemma_norm, pos, kind::text, homograph from seed_candidates"
            " where decision is not null or entry_id is not null"
        )
    }
    conn.execute("delete from seed_candidates where decision is null and entry_id is null")
    taken = {identity for _, identity in decided.values()}
    fresh: list[tuple[Any, ...]] = []
    updated = 0
    for candidate in candidates:
        known = decided.get((candidate.lemma, candidate.pos, candidate.kind))
        if known is not None:
            conn.execute(
                "update seed_candidates set sources = %s, levels = %s, zipf = %s, tier = %s,"
                " variants = %s, patterns = %s, flags = %s, originals = %s, updated_at = now()"
                " where id = %s",
                (*_values(candidate, candidate.homograph)[5:], known[0]),
            )
            updated += 1
            continue
        homograph = candidate.homograph
        while (candidate.lemma_norm, candidate.pos, candidate.kind, homograph) in taken:
            homograph += 1
        taken.add((candidate.lemma_norm, candidate.pos, candidate.kind, homograph))
        fresh.append(_values(candidate, homograph))
    return copy_rows(conn, "seed_candidates", COLUMNS, fresh), updated


def export(conn: psycopg.Connection, path: Path) -> int:
    """Write the candidates, ranked as the factory will take them, to a CSV for review."""
    rows = conn.execute(
        "select tier, zipf, lemma, pos, kind::text, homograph, levels, variants, patterns,"
        " sources, flags, decision from seed_candidates"
        " order by tier, zipf desc nulls last, lemma, pos nulls last, homograph"
    ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(EXPORT_COLUMNS)
        for tier, zipf, lemma, pos, kind, homograph, *lists, decision in rows:
            levels, variants, patterns, sources, flags = lists
            writer.writerow(
                [
                    tier,
                    "" if zipf is None else zipf,
                    lemma,
                    pos or "",
                    kind or "",
                    homograph,
                    " | ".join(levels),
                    " | ".join(_variant_text(variant) for variant in variants),
                    " | ".join(patterns),
                    " | ".join(sources),
                    " | ".join(flags),
                    decision or "",
                ]
            )
    return len(rows)


def _values(candidate: Candidate, homograph: int) -> tuple[Any, ...]:
    variants = [{"form": v.form, "regions": list(v.regions)} for v in candidate.variants]
    return (
        candidate.lemma,
        candidate.lemma_norm,
        candidate.pos,
        candidate.kind,
        homograph,
        candidate.sources,
        sorted(candidate.levels),
        None if candidate.zipf is None else round(candidate.zipf, 2),
        candidate.tier,
        Jsonb(variants),
        candidate.patterns,
        candidate.flags,
        candidate.originals,
    )


def _variant_text(variant: dict[str, Any]) -> str:
    regions = variant.get("regions") or []
    return f"{variant['form']} [{', '.join(regions)}]" if regions else variant["form"]
