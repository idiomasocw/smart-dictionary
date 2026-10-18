import json
from pathlib import Path

import pytest

from smart_dictionary.content.projections import english_forms
from smart_dictionary.content.publish import load_fixtures, publish
from smart_dictionary.core.entrydoc import EntryDoc, Variant
from smart_dictionary.db.sync import connect
from smart_dictionary.paths import FIXTURES_DIR

FIXTURE_FILES = sorted((FIXTURES_DIR / "entries").glob("*.json"))


def fixture(name: str) -> EntryDoc:
    path = FIXTURES_DIR / "entries" / f"{name}.json"
    return EntryDoc.model_validate(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_fixtures_are_valid_entry_documents(path: Path):
    EntryDoc.model_validate(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("go", {"go", "goes", "going", "went", "gone"}),
        ("leaf", {"leaf", "leaves"}),
        ("leave", {"leave", "leaves", "left", "leaving"}),
        ("run_into", {"run into", "ran into", "runs into", "running into"}),
        ("well-known", {"well-known", "well known"}),
        ("realize", {"realize", "realizes", "realized", "realise", "realised", "realising"}),
    ],
)
def test_english_forms(name, expected):
    assert expected <= set(english_forms(fixture(name)))


def test_spelling_variants_are_inflected_in_their_own_spelling():
    color = fixture("leaf").model_copy(
        update={"lemma": "color", "variants": [Variant(form="colour", regions=["UK"])]}
    )
    assert {"color", "colors", "colour", "colours"} <= set(english_forms(color))

    hark_back = fixture("run_into").model_copy(
        update={"lemma": "hark back", "variants": [Variant(form="harken back")]}
    )
    assert {"harked back", "harkened back", "harkens back"} <= set(english_forms(hark_back))


def test_publishing_builds_the_projections(migrated_database_url):
    with connect() as conn:
        keys = load_fixtures(conn, FIXTURES_DIR / "entries")
    assert len(keys) == len(FIXTURE_FILES)

    with connect() as conn:
        leaves = conn.execute(
            "select e.key from entry_forms f join entries e on e.id = f.entry_id"
            " where f.form_norm = 'leaves' order by e.key"
        ).fetchall()
        assert [k for (k,) in leaves] == ["en:leaf:n", "en:leave:v"]

        darse = conn.execute(
            "select sense_key from equivalents where term_norm = 'darse cuenta' order by sense_key"
        ).fetchall()
        assert [k for (k,) in darse] == ["en:notice:v#1", "en:realize:v#1"]

        phonetic = conn.execute(
            "select phonetic from entry_forms where form_norm = 'beautiful'"
        ).fetchone()
        assert phonetic is not None and phonetic[0]

        status = conn.execute("select status, content_version from entries where key = 'en:go:v'")
        assert status.fetchone() == ("published", 0)


def test_republishing_keeps_keys_and_retires_dropped_senses(migrated_database_url):
    leave = fixture("leave")
    with connect() as conn:
        publish(conn, leave, produced_by={"source": "test"})
    shorter = leave.model_copy(update={"senses": leave.senses[:1]})
    with connect() as conn:
        publish(conn, shorter, produced_by={"source": "test"})
        senses = conn.execute(
            "select key, retired_at is not null from senses order by key"
        ).fetchall()
        assert senses == [("en:leave:v#1", False), ("en:leave:v#2", True)]
        version = conn.execute("select content_version from entries where key = 'en:leave:v'")
        assert version.fetchone() == (1,)
        revisions = conn.execute("select count(*) from entry_revisions").fetchone()
        assert revisions == (2,)
