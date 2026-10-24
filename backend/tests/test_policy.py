"""The policy of 2026-10-24 on categories and headwords (docs/policies/categories-and-headwords.md):
idioms and phrases carry their function, headwords keep their objects in patterns, and the
system mends, refuses and audits what breaks it."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from factory_docs import world
from smart_dictionary.ai.provisional import TARGETS_SYNC
from smart_dictionary.api.app import create_app
from smart_dictionary.cli import main
from smart_dictionary.core.entrydoc import valid_pos
from smart_dictionary.core.headwords import mended, problems
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import audit
from smart_dictionary.seeds.builder import Reference, build
from smart_dictionary.seeds.owner import OwnerRow
from test_curate import ENTRIES, SEEDS, curation_file, seeded
from test_provisional import row
from test_related import entry, rows, wiktionary
from test_word_requests import admin


def test_each_kind_takes_its_parts_of_speech():
    assert valid_pos("conjunction", "phrase")  # as long as
    assert valid_pos("verb", "idiom")  # break the ice
    assert valid_pos("phrase", "idiom")  # a whole sentence: every cloud has a silver lining
    assert valid_pos("noun", "word") and not valid_pos("phrase", "word")
    assert valid_pos("verb", "phrasal_verb") and not valid_pos("noun", "phrasal_verb")


def test_a_headword_keeps_its_object_in_a_pattern():
    assert problems("put up with someone", "verb")
    assert mended("put up with someone", "verb") == ("put up with", "put up with sb")
    assert mended("keep away from someone or something", "verb") == (
        "keep away from", "keep away from sb/sth",
    )  # fmt: skip
    assert mended("drive somebody up the wall", "verb") == ("drive someone up the wall", None)
    assert mended("to break the ice", "verb") == ("break the ice", None)
    assert mended("be fed up", "adjective") == ("fed up", None)
    # The preposition *to* and a phrasal verb's *be* stay.
    assert problems("to date", "adverb") == [] and problems("be into", "verb") == []
    # A slot inside an expression stays; "or something" is a phrase, not an object.
    assert problems("pull someone's leg", "verb") == []
    assert problems("or something", "phrase") == []
    assert problems("someone", "pronoun") == []


def test_the_seed_builder_mends_a_headword():
    row = OwnerRow(lemma="put up with someone", pos="verb", kind="phrasal_verb", levels=[],
                   tags=[], variants=[], patterns=[], sources=["list.txt"],
                   originals=["put up with someone"], flags=[])  # fmt: skip
    [candidate] = build([row], Reference())
    assert (candidate.lemma, candidate.lemma_norm, candidate.patterns) == (
        "put up with", "put up with", ["put up with sb"],
    )  # fmt: skip
    assert "headword mended from 'put up with someone'" in candidate.flags


def test_idioms_and_phrases_take_the_one_function_wiktionary_gives(
    migrated_database_url, capsys: pytest.CaptureFixture[str]
):
    with connect() as conn:
        world(conn)
        wiktionary(conn, "break the ice", "verb", "idiom")
        wiktionary(conn, "as long as", "conjunction", "idiom")
        wiktionary(conn, "as long as", "preposition")  # two functions: for a person to judge
        entry(conn, "en:break_the_ice:idiom", "break the ice", "phrase", "idiom")
        conn.execute("update entries set doc = %s where key = 'en:break_the_ice:idiom'",
                     (Jsonb({"key": "en:break_the_ice:idiom", "pos": "phrase"}),))  # fmt: skip
        entry(conn, "en:as_long_as:phr", "as long as", "phrase", "phrase", status="queued",
              written=False)  # fmt: skip
    main(["seeds", "functions", "--dry-run"])
    assert "Dry run" in capsys.readouterr().out
    assert row("select pos from entries where key = 'en:break_the_ice:idiom'") == ("phrase",)
    main(["seeds", "functions"])
    out = capsys.readouterr().out
    assert "1 idioms and phrases got their function" in out and "1 still have none" in out
    # In place: the same key, the written document too (one version up), as a revision.
    assert row("select pos, doc->>'pos', content_version from entries"
               " where key = 'en:break_the_ice:idiom'") == ("verb", "verb", 1)  # fmt: skip
    assert row("select produced_by->>'source', qa->'changed'->'pos' from entry_revisions") == (
        "policy", ["phrase", "verb"],
    )  # fmt: skip
    assert row("select pos from entries where key = 'en:as_long_as:phr'") == ("phrase",)


def test_a_curation_file_sets_functions_in_place_and_hands_patterns_on(
    migrated_database_url, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    with connect() as conn:
        world(conn)
        entry(conn, "en:as_long_as:phr", "as long as", "phrase", "phrase")  # written already
        entry(conn, "en:look_back:phrv", "look back", "verb", "phrasal_verb", status="queued",
              written=False)  # fmt: skip
        seeded(conn, "en:look_back:phrv", "look back", "verb", "phrasal_verb")
        entry(conn, "en:look_back_on:phrv", "look back on", "verb", "phrasal_verb",
              status="queued", written=False)  # fmt: skip
        conn.execute(
            "insert into seed_candidates (lemma, lemma_norm, pos, kind, sources, originals)"
            " values ('hold out much hope for someone', 'hold out much hope for someone', null,"
            " null, array['owner:list.txt'], array['hold out much hope for someone'])"
        )
    verbs = curation_file(tmp_path / "entries.csv", [*ENTRIES, "pattern_of", "pattern"], [
        ["en:as_long_as:phr", "change", "conjunction", "phrase", "high", "A conjunction.", "",
         ""],
        ["en:look_back_on:phrv", "retire", "", "", "high", "look back with its preposition.",
         "en:look_back:phrv", "look back (on sth)"],
    ])  # fmt: skip
    main(["seeds", "curate", "--file", str(verbs)])
    out = capsys.readouterr().out
    assert "en:as_long_as:phr: function: conjunction" in out  # written, yet in place
    assert "en:look_back_on:phrv: retired: a pattern of en:look_back:phrv now" in out
    assert row("select pos, doc->>'pos' from entries where key = 'en:as_long_as:phr'") == (
        "conjunction", "conjunction",
    )  # fmt: skip
    assert row("select patterns from seed_candidates where lemma = 'look back'") == (
        ["look back (on sth)"],
    )
    # A search for the retired headword stands for the entry that carries it.
    with connect() as conn:
        found = conn.execute(TARGETS_SYNC, {"lemmas": ["look back on"], "key": None}).fetchall()
    assert [target[0] for target in found] == ["en:look_back:phrv"]

    seeds = curation_file(tmp_path / "seeds.csv", SEEDS, [
        ["hold out much hope for someone", "verb", "idiom", "accept", "high", "An idiom.", "",
         ""],
    ])  # fmt: skip
    main(["seeds", "curate", "--file", str(seeds)])
    assert "headword 'hold out much hope for' with the pattern" in capsys.readouterr().out
    mended = row("select lemma, lemma_norm, patterns from seed_candidates"
                 " where lemma like 'hold out%%'")  # fmt: skip
    assert mended == ("hold out much hope for", "hold out much hope for",
                      ["hold out much hope for sb"])  # fmt: skip


def test_the_console_refuses_a_headword_that_breaks_the_rules(
    migrated_database_url, anyio_backend_options
):
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        auth = admin()
        slot = client.post("/v1/admin/entries", headers=auth,
                           json={"lemma": "put up with someone", "pos": "verb",
                                 "kind": "phrasal_verb"})  # fmt: skip
        assert slot.status_code == 422 and "(put up with sb)" in slot.json()["detail"]
        spelled = client.post("/v1/admin/entries", headers=auth,
                              json={"lemma": "drive somebody up the wall", "pos": "verb",
                                    "kind": "idiom"})  # fmt: skip
        assert spelled.status_code == 422 and "'someone' for 'somebody'" in spelled.json()["detail"]
        good = client.post("/v1/admin/entries", headers=auth,
                           json={"lemma": "as long as", "pos": "conjunction",
                                 "kind": "phrase"})  # fmt: skip
        assert good.status_code == 201, good.text
        assert (good.json()["key"], good.json()["pos"]) == ("en:as_long_as:phr", "conjunction")


def test_the_audit_raises_what_breaks_the_policy_and_applies_it(migrated_database_url):
    with connect() as conn:
        world(conn)
        wiktionary(conn, "break the ice", "verb", "idiom")
        for key, lemma, pos, kind in (
            ("en:put_up_with_someone:phrv", "put up with someone", "verb", "phrasal_verb"),
            ("en:drop_someone:phrv", "drop someone", "verb", "phrasal_verb"),
            ("en:account:n", "account", "noun", "word"),
            ("en:account_for:phrv", "account for", "verb", "phrasal_verb"),
            ("en:break_the_ice:idiom", "break the ice", "phrase", "idiom"),
            ("en:comply:v", "comply", "verb", "word"),
            ("en:comply_with:phrv", "comply with", "verb", "phrasal_verb"),
        ):
            entry(conn, key, lemma, pos, kind, status="queued", written=False)
        ids = [found[0] for found in conn.execute("select id from entries order by key")]
        items = {item["key"]: item for item in audit.packs(conn, ids)}

    def rule(key: str, kind: str) -> dict:
        [found] = [r for r in items[key]["rule_findings"] if r["type"] == kind]
        return found

    assert rule("en:put_up_with_someone:phrv", "headword")["proposed"] == {
        "lemma": "put up with", "pattern": "put up with sb",
    }  # fmt: skip
    # Mended to one word, it's a plain verb.
    assert rule("en:drop_someone:phrv", "headword")["proposed"] == {
        "lemma": "drop", "pattern": "drop sb", "kind": "word",
    }  # fmt: skip
    # A pattern belongs to the same part of speech: the noun *account* takes no verb's.
    assert not [r for r in items["en:account_for:phrv"]["rule_findings"]
                if r["type"] == "duplicate"]  # fmt: skip
    assert rule("en:break_the_ice:idiom", "kind")["proposed"] == {"pos": "verb", "kind": "idiom"}
    assert rule("en:comply_with:phrv", "duplicate")["proposed"] == {
        "retire": "en:comply_with:phrv", "keep": "en:comply:v", "pattern": "comply with sth",
    }  # fmt: skip

    # Applied: a function in place, a headword under a new key, a longer form as a pattern.
    with connect() as conn:
        ids = {}
        for key, kind in (("en:break_the_ice:idiom", "kind"),
                          ("en:put_up_with_someone:phrv", "headword"),
                          ("en:drop_someone:phrv", "headword"),
                          ("en:comply_with:phrv", "duplicate")):  # fmt: skip
            found = rule(key, kind)
            ids[key] = conn.execute(
                "insert into audit_findings (entry_id, entry_key, target_key, type, source,"
                " confidence, problem, proposed) select id, key, key, %s, 'rules', 'high', %s, %s"
                " from entries where key = %s returning id",
                (kind, found["problem"], Jsonb(found["proposed"]), key),
            ).fetchone()[0]  # pyright: ignore[reportOptionalSubscript]
        results = audit.apply(conn, list(ids.values()), decided_by="lms", note=None, tenant="ocw")
    assert {r.outcome for r in results} == {"applied"}, results
    assert sorted(rows("select key, status::text, pos from entries")) == [
        ("en:account:n", "queued", "noun"),
        ("en:account_for:phrv", "queued", "verb"),
        ("en:break_the_ice:idiom", "queued", "verb"),
        ("en:comply:v", "queued", "verb"),
        ("en:comply_with:phrv", "retired", "verb"),
        ("en:drop:v", "queued", "verb"),
        ("en:drop_someone:phrv", "retired", "verb"),
        ("en:put_up_with:phrv", "queued", "verb"),
        ("en:put_up_with_someone:phrv", "retired", "verb"),
    ]
    # The patterns travel on seeds; entries that had none get one.
    assert row("select pattern_of from entries where key = 'en:comply_with:phrv'") == (
        "en:comply:v",
    )
    patterns = rows("select e.key, c.patterns from seed_candidates c"
                    " join entries e on e.id = c.entry_id")  # fmt: skip
    assert sorted(patterns) == [
        ("en:comply:v", ["comply with sth"]),
        ("en:drop:v", ["drop sb"]),
        ("en:put_up_with:phrv", ["put up with sb"]),
    ]
