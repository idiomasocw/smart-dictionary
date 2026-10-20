"""Curation files (seeds.curate): decisions a model made and the owner reviewed, applied by
`seeds curate`. Seeds that waited for a part of speech get one; unwritten entries move to the
right kind; written ones only get a finding for the owner."""

import csv
from pathlib import Path
from typing import Any

import pytest

from factory_docs import world
from smart_dictionary.cli import main
from smart_dictionary.db.sync import connect
from smart_dictionary.factory.queue import queue
from test_provisional import row
from test_related import entry, rows


def curation_file(path: Path, header: list[str], lines: list[list[str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(header)
        writer.writerows(lines)
    return path


SEEDS = ["lemma", "pos", "kind", "decision", "confidence", "reason", "variant_of", "regions"]
ENTRIES = ["key", "decision", "pos", "kind", "confidence", "reason"]


def seeded(conn: Any, key: str, lemma: str, pos: str, kind: str = "word") -> None:
    """The owner's seed, linked to its entry."""
    conn.execute(
        "insert into seed_candidates (lemma, lemma_norm, pos, kind, sources, entry_id, originals)"
        " select %s, %s, %s, %s::entry_kind, array['owner:list.txt'], id, array[%s]"
        " from entries where key = %s",
        (lemma, lemma, pos, kind, lemma, key),
    )


def test_waiting_seeds_get_their_part_of_speech_from_a_reviewed_file(
    migrated_database_url, tmp_path, capsys: pytest.CaptureFixture[str]
):
    with connect() as conn:
        world(conn)  # zzword has no part of speech; DNA is a "word" marked phrase
        conn.execute(
            "insert into seed_candidates (lemma, lemma_norm, pos, kind, sources, originals)"
            " values ('these', 'these', null, null, array['owner:list.txt'], array['these']),"
            " ('realise', 'realise', null, null, array['owner:list.txt'], array['realise']),"
            " ('house sit', 'house sit', null, null, array['owner:list.txt'], array['house sit'])"
        )  # the same as the pronoun seed; realize's UK spelling (listed already); a spacing
    path = curation_file(tmp_path / "seeds.csv", SEEDS, [
        ["zzword", "noun", "word", "accept", "high", "A test noun.", "", ""],
        ["DNA", "noun", "word", "accept", "high", "An abbreviation used as a noun.", "", ""],
        ["these", "pronoun", "word", "accept", "high", "The pronoun.", "", ""],
        ["gone soon", "", "", "reject", "medium", "Not a set expression.", "", ""],
        ["realise", "", "", "reject", "high", "UK spelling of realize.", "realize", "UK"],
        ["house sit", "", "", "reject", "high", "A spacing of house.", "house", ""],
    ])  # fmt: skip

    main(["seeds", "curate", "--file", str(path), "--dry-run"])
    out = capsys.readouterr().out
    assert "zzword: accepted as noun/word" in out and "Dry run: nothing was changed." in out
    assert row("select pos from seed_candidates where lemma = 'zzword'") == (None,)

    main(["seeds", "curate", "--file", str(path)])
    out = capsys.readouterr().out
    assert "these: merged: another seed is these as pronoun/word" in out
    assert "gone soon: skipped: no seed waits for it" in out
    assert "realise: rejected: a variant of realize now" in out
    assert row("select variants from seed_candidates where lemma = 'realize'") == (
        [{"form": "realise", "regions": ["UK"]}],  # listed once, as before
    )
    assert row("select variants from seed_candidates where lemma = 'house'") == (
        [{"form": "house sit", "regions": []}],
    )
    assert "Next: uv run smart-dictionary seeds queue" in out
    assert rows(
        "select lemma, pos, kind::text, decision from seed_candidates"
        " where lemma in ('zzword', 'DNA') order by lemma"
    ) == [("DNA", "noun", "word", "accept"), ("zzword", "noun", "word", "accept")]
    assert row("select reason from seed_candidates where lemma = 'zzword'")[0].startswith(
        "A test noun. (high confidence; seeds.csv)"
    )
    with connect() as conn:
        keys = {item.key for item in queue(conn)}
    assert {"en:zzword:n", "en:dna:n"} <= keys


def test_unwritten_entries_move_to_the_right_kind_and_written_ones_wait_for_you(
    migrated_database_url, tmp_path, capsys: pytest.CaptureFixture[str]
):
    with connect() as conn:
        world(conn)
        for key, lemma in (("en:hit_the_ceiling:v", "hit the ceiling"),
                           ("en:barn_dance:v", "barn dance"), ("en:take_hold:v", "take hold"),
                           ("en:roller_skate:v", "roller skate"),
                           ("en:summer_camp:v", "summer camp")):  # fmt: skip
            entry(conn, key, lemma, "verb", status="queued", written=False)
            seeded(conn, key, lemma, "verb")
        conn.execute("update entries set priority = 5000 where key = 'en:hit_the_ceiling:v'")
        conn.execute("insert into misses (tenant_id, language, query_norm, status, entry_key)"
                     " values ('ocw', 'en', 'hits the ceiling', 'queued',"
                     " 'en:hit_the_ceiling:v')")  # fmt: skip
        entry(conn, "en:barn_dance:n", "barn dance", "noun")
        entry(conn, "en:take_hold:phr", "take hold", "phrase", "phrase", status="queued",
              written=False)  # fmt: skip
        entry(conn, "en:summer_camp:n", "summer camp", "noun")
        entry(conn, "en:make_hay:v", "make hay", "verb")  # written already
    path = curation_file(tmp_path / "verbs.csv", ENTRIES, [
        ["en:hit_the_ceiling:v", "change", "phrase", "idiom", "high", "Figurative: get angry."],
        ["en:barn_dance:v", "retire", "", "", "high", "A noun only; covered by en:barn_dance:n."],
        ["en:take_hold:v", "change", "phrase", "phrase", "high", "A literal set phrase."],
        ["en:roller_skate:v", "keep", "verb", "word", "high", "A compound verb."],
        ["en:summer_camp:v", "change", "phrase", "phrase", "low", "Unsure."],
        ["en:make_hay:v", "change", "phrase", "idiom", "medium", "Make hay while the sun shines."],
    ])  # fmt: skip

    main(["seeds", "curate", "--file", str(path)])
    out = capsys.readouterr().out
    assert "en:hit_the_ceiling:v: requeued as en:hit_the_ceiling:idiom" in out
    assert "en:take_hold:v: retired: en:take_hold:phr has it already" in out
    assert "en:summer_camp:v: skipped: en:summer_camp:n is the same expression" in out
    assert "en:make_hay:v: written already: a kind finding waits in the console" in out

    assert rows(
        "select key, status::text from entries where key like 'en:hit_the_ceiling:%%'"
        " or key in ('en:barn_dance:v', 'en:take_hold:v', 'en:roller_skate:v',"
        " 'en:summer_camp:v', 'en:make_hay:v') order by key"
    ) == [
        ("en:barn_dance:v", "retired"),
        ("en:hit_the_ceiling:idiom", "queued"),
        ("en:hit_the_ceiling:v", "retired"),
        ("en:make_hay:v", "published"),
        ("en:roller_skate:v", "queued"),
        ("en:summer_camp:v", "queued"),
        ("en:take_hold:v", "retired"),
    ]  # fmt: skip
    # Same place in the queue, same approval, its seed, and the searches that pointed at it.
    assert row("select priority, approved_by from entries"
               " where key = 'en:hit_the_ceiling:idiom'") == (5000, "seeds")  # fmt: skip
    assert rows(
        "select c.lemma, e.key from seed_candidates c join entries e on e.id = c.entry_id"
        " where c.lemma in ('hit the ceiling', 'take hold') order by 1"
    ) == [("hit the ceiling", "en:hit_the_ceiling:idiom"), ("take hold", "en:take_hold:phr")]
    assert row("select entry_key, status from misses") == ("en:hit_the_ceiling:idiom", "pending")
    assert row("select decision from seed_candidates where lemma = 'barn dance'") == ("reject",)
    assert row("select type, source, confidence, proposed from audit_findings") == (
        "kind", "ai", "medium", {"pos": "phrase", "kind": "idiom"},
    )  # fmt: skip


def test_a_file_with_a_wrong_header_or_pair_changes_nothing(migrated_database_url, tmp_path):
    wrong = curation_file(tmp_path / "wrong.csv", ["word", "pos"], [["x", "noun"]])
    with pytest.raises(SystemExit, match="the header must be"):
        main(["seeds", "curate", "--file", str(wrong)])
    clash = curation_file(tmp_path / "clash.csv", SEEDS,
                          [["x", "phrase", "word", "accept", "high", "r", "", ""]])  # fmt: skip
    with pytest.raises(SystemExit, match="'phrase' can't go with 'word'"):
        main(["seeds", "curate", "--file", str(clash)])
    region = curation_file(tmp_path / "region.csv", SEEDS,
                           [["x", "", "", "reject", "high", "r", "y", "GB"]])  # fmt: skip
    with pytest.raises(SystemExit, match="unknown regions GB"):
        main(["seeds", "curate", "--file", str(region)])
