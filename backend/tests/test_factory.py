"""The factory's server side: tokens, the queue, leases, grounding packs and the API."""

import uuid
from typing import Any, LiteralString

import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from smart_dictionary.api.app import create_app
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import lease, tokens
from smart_dictionary.factory.packs import build
from smart_dictionary.factory.queue import queue

GO_GLOSS = (
    "To move from one place to another, especially to a place farther away from the speaker"
    " or listener."
)


def _world(conn: psycopg.Connection) -> None:
    """Seed candidates for every queue rule, and the reference facts packs draw on."""
    for dataset in ("t-forms", "t-ipa", "t-wiki"):
        conn.execute(
            "insert into ref_datasets (id, name, version, license, attribution, source_url)"
            " values (%s, %s, '1', 'test', 'test', 'file:///test')",
            (dataset, dataset),
        )
    for form, features in (
        ("went", "V;PST"),
        ("gone", "V;V.PTCP;PST"),
        ("goes", "V;3;SG;PRS"),
        ("gos", "N;PL"),  # go the noun: not in the verb's pack
        ("uncountable", "N;PL"),  # UniMorph annotation, not a form
    ):
        conn.execute(
            "insert into ref_forms (dataset_id, language, lemma, lemma_norm, form, form_norm,"
            " features) values ('t-forms', 'en', 'go', 'go', %s, %s, %s)",
            (form, form, features),
        )
    for variety, ipa in (("en-US", "goʊ"), ("en-GB", "ɡəʊ")):
        conn.execute(
            "insert into ref_ipa (dataset_id, word, word_norm, variety, ipa)"
            " values ('t-ipa', 'go', 'go', %s, %s)",
            (variety, ipa),
        )
    for word, pos, kind, glosses, spanish in (
        ("go", "verb", "word", [GO_GLOSS, "To leave; to depart."], ["ir", "irse", "ir"]),
        ("go", "noun", "word", ["A board game for two players."], ["go"]),
        ("run into", "verb", "phrasal_verb", ["To meet someone unexpectedly."], ["toparse con"]),
    ):
        conn.execute(
            "insert into ref_wiktionary (dataset_id, word, word_norm, pos, pos_raw, kind, glosses,"
            " translations) values ('t-wiki', %s, %s, %s, %s, %s, %s, %s)",
            (word, word, pos, pos, kind, glosses, Jsonb({"es": spanish})),
        )
    candidates: list[tuple[Any, ...]] = [
        # lemma, pos, kind, tier, zipf, levels, sources, decision, variants
        ("go", "verb", "word", 0, 6.70, ["A1"], ["owner:list.txt", "cefrj"], None, []),
        ("these", "pronoun", "word", 1, 6.04, ["A1"], ["cefrj"], None, []),
        ("realize", "verb", "word", 0, 4.83, ["A2"], ["owner:list.txt"], None,
         [{"form": "realise", "regions": ["UK"]}]),
        ("house", "verb", "word", 0, 5.71, ["C1"], ["owner:list.txt"], None, []),
        ("run into", "verb", "phrasal_verb", 0, None, [], ["owner:phrasal.txt"], None, []),
        ("zzword", None, None, 0, 3.0, [], ["owner:list.txt"], None, []),  # uncurated
        ("DNA", "phrase", "word", 0, 4.0, [], ["owner:list.txt"], None, []),  # no valid entry
        ("nope", "noun", "word", 0, 5.0, ["A1"], ["owner:list.txt"], "reject", []),
    ]  # fmt: skip
    for lemma, pos, kind, tier, zipf, levels, sources, decision, variants in candidates:
        conn.execute(
            "insert into seed_candidates (lemma, lemma_norm, pos, kind, tier, zipf, levels,"
            " sources, decision, variants, originals)"
            " values (%s, %s, %s, %s::entry_kind, %s, %s, %s, %s, %s, %s, %s)",
            (lemma, lemma, pos, kind, tier, zipf, levels, sources, decision, Jsonb(variants),
             [lemma]),
        )  # fmt: skip


def _scalar(conn: psycopg.Connection, query: LiteralString) -> Any:
    row = conn.execute(query).fetchone()
    assert row is not None
    return row[0]


def test_queue_orders_by_level_then_owner_lists_then_frequency(migrated_database_url):
    with connect() as conn:
        _world(conn)
        preview = queue(conn, dry_run=True)
        # A1 first (the owner's go before CEFR-J's these), then A2, C1, and unleveled last;
        # the uncurated and the rejected candidates never queue.
        assert [item.key for item in preview] == [
            "en:go:v", "en:these:pron", "en:realize:v", "en:house:v", "en:run_into:phrv",
        ]  # fmt: skip
        assert _scalar(conn, "select count(*) from entries") == 0

        assert len(queue(conn)) == 5
        first = conn.execute(
            "select key, status::text, cefr, priority from entries order by priority desc"
        ).fetchone()
        assert first == ("en:go:v", "queued", "A1", 61_670)
        linked = "select count(*) from seed_candidates where entry_id is not null"
        assert _scalar(conn, linked) == 5
        assert queue(conn) == []


def test_queue_picks_headwords_levels_and_a_limit(migrated_database_url):
    with connect() as conn:
        _world(conn)
        assert [item.key for item in queue(conn, lemmas=["realize"], dry_run=True)] == [
            "en:realize:v"
        ]
        assert [item.key for item in queue(conn, levels=["A1"], dry_run=True)] == [
            "en:go:v",
            "en:these:pron",
        ]
        assert len(queue(conn, limit=2, dry_run=True)) == 2


def test_factory_tokens_are_stored_hashed_and_scoped(migrated_database_url):
    with connect() as conn:
        token = tokens.create(conn, "routine", ["factory"])
        assert token.startswith(tokens.PREFIX)
        client = tokens.find(conn, token, "factory")
        assert client is not None and client.name == "routine"
        assert tokens.find(conn, token, "admin") is None
        assert tokens.find(conn, token + "x", "factory") is None
        stored = conn.execute("select token_hash from api_clients").fetchone()
        assert stored is not None and token.encode() not in bytes(stored[0])
        assert tokens.revoke(conn, "routine") == 1
        assert tokens.find(conn, token, "factory") is None


def test_leases_follow_the_queue_and_never_overlap(migrated_database_url):
    with connect() as conn:
        _world(conn)
        queue(conn)
    dsn = migrated_database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as first, psycopg.connect(dsn) as second:
        # The first run's rows stay locked until it commits; the second skips them.
        run_a = lease.lease(first, 2, "run a")
        run_b = lease.lease(second, 2, "run b")
        second.commit()
        first.commit()
    with connect() as conn:
        key_of = dict(conn.execute("select id, key from entries").fetchall())
        assert {key_of[i] for i in run_a.entry_ids} == {"en:go:v", "en:these:pron"}
        assert {key_of[i] for i in run_b.entry_ids} == {"en:realize:v", "en:house:v"}
        last = lease.lease(conn, 10)
        assert {key_of[i] for i in last.entry_ids} == {"en:run_into:phrv"}
        assert lease.lease(conn, 10) == lease.Batch(None, None, [])
        states = conn.execute("select distinct status::text, leased_from::text from entries")
        assert states.fetchall() == [("generating", "queued")]


def test_an_expired_lease_returns_its_entries(migrated_database_url):
    with connect() as conn:
        _world(conn)
        queue(conn, lemmas=["go"])
        first = lease.lease(conn, 5)
        conn.execute("update entries set lease_expires_at = now() - interval '1 minute'")
        conn.execute("update factory_batches set leased_at = now() - interval '4 hours'")
        second = lease.lease(conn, 5)
        assert second.id != first.id and second.entry_ids == first.entry_ids
        assert first.id is not None and second.id is not None
        old = lease.status(conn, first.id)
        assert old is not None and old["status"] == "expired"
        new = lease.status(conn, second.id)
        assert new is not None
        assert new["entries"] == [{"key": "en:go:v", "status": "generating"}]


def test_packs_carry_the_facts(migrated_database_url):
    with connect() as conn:
        _world(conn)
        queue(conn)
        batch = lease.lease(conn, 10)
        packs = {pack.key: pack for pack in build(conn, batch.entry_ids)}
    go = packs["en:go:v"]
    assert go.priors.model_dump() == {
        "cefr": "A1",
        "zipf": 6.7,
        "lists": ["owner:list.txt", "cefrj"],
    }
    # LemmInflect's forms in entry notation; not go the noun's plural, nor UniMorph's junk.
    assert {(form.form, form.features) for form in go.forms} == {
        ("went", "V;PST"),
        ("gone", "V;V.PTCP;PST"),
        ("going", "V;V.PTCP;PRS"),
        ("goes", "V;3;SG;PRS"),
    }
    assert go.ipa == {"en-US": ["goʊ"], "en-GB": ["ɡəʊ"]}
    # Hints are cut to 12 words and only come from the verb, not from go the board game.
    assert go.wiktionary_hints.senses == [
        "To move from one place to another, especially to a place farther …",
        "To leave; to depart.",
    ]
    assert go.wiktionary_hints.es_candidates == ["ir", "irse"]
    assert go.originals == ["go"] and go.existing is None and go.repair is None
    assert packs["en:realize:v"].variants == [{"form": "realise", "regions": ["UK"]}]
    assert packs["en:run_into:phrv"].wiktionary_hints.senses == ["To meet someone unexpectedly."]


def test_factory_api_needs_its_token(migrated_database_url, anyio_backend_options):
    with connect() as conn:
        _world(conn)
        queue(conn)
        token = tokens.create(conn, "routine", ["factory"])
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        assert client.post("/v1/factory/batches", json={"size": 2}).status_code == 401
        wrong = client.post(
            "/v1/factory/batches", json={"size": 2}, headers={"Authorization": "Bearer nope"}
        )
        assert wrong.status_code == 401
        assert wrong.headers["content-type"].startswith("application/problem+json")

        auth = {"Authorization": f"Bearer {token}"}
        leased = client.post(
            "/v1/factory/batches", json={"size": 2, "label": "pilot"}, headers=auth
        )
        assert leased.status_code == 200, leased.text
        body = leased.json()
        assert [entry["key"] for entry in body["entries"]] == ["en:go:v", "en:these:pron"]
        assert body["entries"][0]["ipa"]["en-US"] == ["goʊ"]

        batch = client.get(f"/v1/factory/batches/{body['batch_id']}", headers=auth).json()
        assert batch["status"] == "leased" and batch["run_label"] == "pilot"
        assert {entry["status"] for entry in batch["entries"]} == {"generating"}
        missing = client.get(f"/v1/factory/batches/{uuid.uuid4()}", headers=auth)
        assert missing.status_code == 404
