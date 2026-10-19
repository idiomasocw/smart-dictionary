"""The factory's server side: tokens, the queue, leases, grounding packs and the API."""

import uuid
from decimal import Decimal
from typing import Any, LiteralString

import psycopg
import pytest
from fastapi.testclient import TestClient

from factory_docs import GO_DOC, go_doc, world
from smart_dictionary.api.app import create_app
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import lease, progress, tokens
from smart_dictionary.factory import queue as queue_module
from smart_dictionary.factory.packs import _forms, build
from smart_dictionary.factory.queue import queue
from smart_dictionary.factory.submit import BatchClosed, Submission, submit
from smart_dictionary.factory.validate import Context

THRESHOLD = 0.7


def _scalar(conn: psycopg.Connection, query: LiteralString) -> Any:
    row = conn.execute(query).fetchone()
    assert row is not None
    return row[0]


def test_queue_orders_by_level_then_owner_lists_then_frequency(migrated_database_url):
    with connect() as conn:
        world(conn)
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
        world(conn)
        assert [item.key for item in queue(conn, lemmas=["realize"], dry_run=True)] == [
            "en:realize:v"
        ]
        assert [item.key for item in queue(conn, levels=["A1"], dry_run=True)] == [
            "en:go:v",
            "en:these:pron",
        ]
        assert len(queue(conn, limit=2, dry_run=True)) == 2


def test_queueing_goes_in_chunks_and_status_reports_it(migrated_database_url, monkeypatch):
    monkeypatch.setattr(queue_module, "CHUNK", 2)
    seen: list[tuple[int, int]] = []
    with connect() as conn:
        world(conn)
        queued = queue(conn, progress=lambda done, total: seen.append((done, total)))
        assert len(queued) == 5 and seen == [(2, 5), (4, 5), (5, 5)]
        lease.lease(conn, 2, "test run")
        lines = progress.report(conn)
    assert "        3  queued        waiting for a run" in lines
    assert "        2  generating    in a run right now" in lines
    assert any("leased      2 words" in line and "test run" in line for line in lines)


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
        world(conn)
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
        world(conn)
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
        world(conn)
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


def test_pack_forms_skip_slips_in_unimorph():
    entry = {"lemma": "gray", "pos": "adjective", "kind": "word"}
    rows = [
        {"form": form, "features": features, "head": features.split(";")[0]}
        for form, features in [
            ("graying", "ADJ;CMPR"),  # really the verb's participle, listed below
            ("graying", "V;V.PTCP;PRS"),
            ("American spelling", "ADJ;SPRL"),  # an annotation
            ("Gray.", "ADJ"),  # the headword again
        ]
    ]
    assert [(form.form, form.features) for form in _forms(entry, rows)] == [
        ("grayer", "ADJ;CMPR"),
        ("grayest", "ADJ;SPRL"),
    ]


def test_the_spanish_lexicon_includes_wiktionary_translations(migrated_database_url):
    with connect() as conn:
        world(conn)
        conn.execute("refresh materialized view ref_spanish_words")
        doc = {"senses": [{"key": "en:x:n#1", "l1": {"es-419": {"gloss": "x", "equivalents": [
            {"term": "toparse con", "match": "exact"}]}}}]}  # fmt: skip
        assert {"toparse", "con"} <= Context.load(conn, [], [doc]).spanish


def test_factory_api_needs_its_token(migrated_database_url, anyio_backend_options):
    with connect() as conn:
        world(conn)
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


def test_a_valid_entry_publishes_and_closes_its_batch(migrated_database_url):
    with connect() as conn:
        world(conn)
        queue(conn, lemmas=["go"])
        batch = lease.lease(conn, 1)
        assert batch.id is not None
        status, results = submit(
            conn, batch.id, [Submission("en:go:v", GO_DOC, "pass")],
            produced_by={"model": "test"}, threshold=THRESHOLD,
        )  # fmt: skip
        assert status == "submitted"
        assert [(r.key, r.outcome, r.score, r.errors) for r in results] == [
            ("en:go:v", "published", 1.0, [])
        ]
        entry = conn.execute(
            "select status::text, quality, lease_batch_id, leased_from from entries"
            " where key = 'en:go:v'"
        ).fetchone()
        assert entry == ("published", Decimal("1.000"), None, None)
        revisions = "select outcome, produced_by->>'source' from entry_revisions"
        assert conn.execute(revisions).fetchall() == [("published", "routine")]
        forms = {row[0] for row in conn.execute("select form_norm from entry_forms")}
        assert {"go", "went", "gone", "going"} <= forms
        batch_row = lease.status(conn, batch.id)
        assert batch_row is not None and batch_row["status"] == "submitted"


def test_a_failing_entry_is_repaired_twice_then_quarantined(migrated_database_url):
    bad = go_doc()
    bad["senses"][0]["examples"] = bad["senses"][0]["examples"][:1]  # V11: 2-3 examples
    with connect() as conn:
        world(conn)
        queue(conn, lemmas=["go"])
        outcomes = []
        for attempt in range(3):
            batch = lease.lease(conn, 1)
            assert batch.id is not None
            if attempt:
                # A repair's pack carries what failed last time.
                [pack] = build(conn, batch.entry_ids)
                assert pack.repair is not None
                assert pack.repair["errors"][0]["check"] == "V11"
            _, [result] = submit(
                conn, batch.id, [Submission("en:go:v", bad)], produced_by={}, threshold=THRESHOLD
            )
            outcomes.append(result.outcome)
        assert outcomes == ["needs_repair", "needs_repair", "quarantined"]
        assert lease.lease(conn, 1) == lease.Batch(None, None, [])
        assert _scalar(conn, "select doc is null from entries where key = 'en:go:v'")
        assert _scalar(conn, "select count(*) from entry_revisions") == 3


def test_only_the_batch_holding_an_entry_can_submit_it(migrated_database_url):
    with connect() as conn:
        world(conn)
        queue(conn, lemmas=["go", "realize"])
        first, second = lease.lease(conn, 1), lease.lease(conn, 1)
        assert first.id is not None and second.id is not None
        _, [stray] = submit(
            conn, second.id, [Submission("en:go:v", GO_DOC, "pass")],
            produced_by={}, threshold=THRESHOLD,
        )  # fmt: skip
        assert stray.outcome == "not_leased"
        assert _scalar(conn, "select status::text from entries where key = 'en:go:v'") == (
            "generating"
        )
        status, _ = submit(
            conn, first.id, [Submission("en:go:v", GO_DOC, "pass")],
            produced_by={}, threshold=THRESHOLD,
        )  # fmt: skip
        assert status == "submitted"
        with pytest.raises(BatchClosed):
            submit(conn, first.id, [Submission("en:go:v", GO_DOC)], produced_by={}, threshold=0)


def test_a_reviewer_verdict_can_hold_an_entry_back(migrated_database_url):
    with connect() as conn:
        world(conn)
        queue(conn, lemmas=["go"])
        batch = lease.lease(conn, 1)
        assert batch.id is not None
        _, [result] = submit(
            conn, batch.id, [Submission("en:go:v", GO_DOC, "fail", ["unnatural example"])],
            produced_by={}, threshold=THRESHOLD,
        )  # fmt: skip
        assert (result.outcome, result.score) == ("needs_repair", 0.5)


def test_factory_api_checks_and_submits(migrated_database_url, anyio_backend_options):
    with connect() as conn:
        world(conn)
        queue(conn, lemmas=["go"])
        token = tokens.create(conn, "routine", ["factory"])
    auth = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        batch = client.post("/v1/factory/batches", json={"size": 1}, headers=auth).json()

        bad = go_doc()
        bad["senses"][0]["definition"] = "to go from one place to another"
        checked = client.post(
            "/v1/factory/validate", json={"entries": [{"key": "en:go:v", "doc": bad}]}, headers=auth
        )
        assert checked.status_code == 200, checked.text
        [result] = checked.json()["results"]
        assert result["valid"] is False
        assert [error["check"] for error in result["errors"]] == ["V4"]

        payload = {
            "model": "test-model",
            "prompt_version": "style-1",
            "entries": [{"key": "en:go:v", "doc": GO_DOC, "review": {"verdict": "pass"}}],
        }
        url = f"/v1/factory/batches/{batch['batch_id']}/entries"
        done = client.post(url, json=payload, headers=auth)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "submitted"
        assert done.json()["results"][0]["outcome"] == "published"
        again = client.post(url, json=payload, headers=auth)
        assert again.status_code == 409
        assert again.json()["code"] == "batch_closed"
        # Published: the read API serves it.
        assert client.get("/v1/entries/en:go:v").status_code == 200
