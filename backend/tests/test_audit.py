"""The audit routine (factory.audit, design §6.6): the server keeps the audit's place, its rules
raise what data can show, the auditor answers and adds findings, high-confidence content fixes
go to repair at once, and the owner applies or rejects the rest."""

import importlib
import json
import sys
from typing import Any

from fastapi.testclient import TestClient

from factory_docs import go_doc, world
from smart_dictionary.api.app import create_app
from smart_dictionary.content.publish import publish
from smart_dictionary.core import tunables
from smart_dictionary.core.entrydoc import EntryDoc
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import audit, lease, tokens
from smart_dictionary.factory.packs import build
from smart_dictionary.paths import REPO_DIR
from test_factory_scripts import BASE, through, write
from test_provisional import row
from test_related import entry, listed, rows, wiktionary
from test_word_requests import PERSON, admin

sys.path.insert(0, str(REPO_DIR / "factory" / "bin"))
client_module = importlib.import_module("_api")
pull_audit = importlib.import_module("pull_audit")
submit_audit = importlib.import_module("submit_audit")

VERSION = "audit-test"


def bearer(scope: str) -> dict[str, str]:
    with connect() as conn:
        token = tokens.create(conn, f"{scope}-routine", [scope])
    return {"Authorization": f"Bearer {token}"}


def audit_world() -> None:
    """The go verb, written, with a level the CEFR lists disagree with, a part of speech they
    give that has no entry, and a related link to no word; and around it, words for each
    other rule."""
    with connect() as conn:
        world(conn)
        listed(conn, "go", "cefrj", "verb")
        conn.execute("update ref_lists set level = 'B2' where lemma_norm = 'go'")
        listed(conn, "go", "cefrj", "noun")
        doc = go_doc()
        doc["related"] = [{"type": "synonym", "key": "en:recieve:v"}]
        publish(conn, EntryDoc.model_validate(doc), produced_by={"source": "test"})
        conn.execute(
            "insert into lookups (tenant_id, query, entry_key, result)"
            " values ('ocw', 'went', 'en:go:v', 'hit')"
        )  # students look it up
        entry(conn, "en:put_up_with:phrv", "put up with", "phrase", "phrasal_verb",
              status="queued", written=False)  # fmt: skip
        conn.execute("update entries set approved_by = 'ai:related', pos = 'verb'"
                     " where key = 'en:put_up_with:phrv'")  # fmt: skip
        entry(conn, "en:by_the_way:idiom", "by the way", "phrase", "idiom")
        entry(conn, "en:by_the_way:phr", "by the way", "phrase", "phrase", status="queued",
              written=False)  # fmt: skip
        entry(conn, "en:run_into:v", "run into", "verb", status="queued", written=False)
        entry(conn, "en:flibber:n", "flibber", "noun", status="queued", written=False)
        for word, pos, kind in (("put up with", "verb", "phrasal_verb"),
                                ("by the way", "phrase", "idiom")):  # fmt: skip
            wiktionary(conn, word, pos, kind)


def leased(client: TestClient, auth: dict[str, str], size: int = 10) -> dict[str, Any]:
    body = {"size": size, "audit_version": VERSION, "label": "test"}
    answer = client.post("/v1/audit/batches", headers=auth, json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()


def rule(item: dict[str, Any], kind: str) -> dict[str, Any]:
    [found] = [finding for finding in item["rule_findings"] if finding["type"] == kind]
    return found


def test_the_server_keeps_the_audits_place_and_its_rules_raise_what_data_shows(
    migrated_database_url, anyio_backend_options
):
    audit_world()
    auth = bearer("audit")
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        assert client.post("/v1/audit/batches", headers=bearer("factory"),
                           json={"audit_version": VERSION}).status_code == 401  # fmt: skip
        assert client.post("/v1/factory/batches", headers=auth, json={}).status_code == 401

        batch = leased(client, auth)
        items = {item["key"]: item for item in batch["items"]}
        # AI's choice first, then what students see (the most looked-up first), then the
        # words nobody wrote yet.
        assert [item["key"] for item in batch["items"]] == [
            "en:put_up_with:phrv", "en:go:v", "en:by_the_way:idiom", "en:by_the_way:phr",
            "en:flibber:n", "en:run_into:v",
        ]  # fmt: skip
        go = items["en:go:v"]
        assert go["written"] and go["doc"]["key"] == "en:go:v"
        assert "cefrj B2 (verb)" in go["facts"]["lists"]
        assert rule(go, "cefr")["proposed"]["cefr"] == "B2"  # A1 against B2: three steps
        assert rule(go, "part_of_speech")["proposed"] == {"lemma": "go", "pos": "noun",
                                                          "kind": "word"}  # fmt: skip
        assert "en:recieve:v" in rule(go, "content")["problem"]
        assert items["en:put_up_with:phrv"]["chosen_by"] == "ai:related"
        assert items["en:put_up_with:phrv"]["doc"] is None  # facts only, before it's written
        assert rule(items["en:by_the_way:idiom"], "duplicate")["proposed"] == {
            "retire": "en:by_the_way:phr", "keep": "en:by_the_way:idiom",
        }  # fmt: skip
        kind = rule(items["en:run_into:v"], "kind")["proposed"]
        assert kind == {"pos": "verb", "kind": "phrasal_verb"}
        assert rule(items["en:flibber:n"], "remove")["proposed"] == {"retire": "en:flibber:n"}
        # A twin pair is raised once, from the first of the two to be leased.
        assert not items["en:by_the_way:phr"]["rule_findings"]

        # Leased: no second run gets them, until the lease ends.
        assert leased(client, auth)["batch_id"] is None
        with connect() as conn:
            conn.execute("update audit_batches set expires_at = now() - interval '1 minute'")
        assert len(leased(client, auth)["items"]) == 6


def test_answers_are_recorded_and_confident_content_fixes_go_to_repair_at_once(
    migrated_database_url, anyio_backend_options
):
    audit_world()
    auth = bearer("audit")
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        batch = leased(client, auth)
        go = next(item for item in batch["items"] if item["key"] == "en:go:v")
        answers = [
            {"key": "en:go:v",
             "rules": [{"id": rule(go, "cefr")["id"], "verdict": "confirm", "confidence": "high"},
                       {"id": rule(go, "part_of_speech")["id"], "verdict": "dismiss",
                        "note": "go as a noun (have a go) is rare for learners"}],
             "findings": [
                 {"type": "example", "target_key": "en:go:v#1.ex2", "confidence": "medium",
                  "problem": "Fine, but shows the same pattern as ex1.",
                  "proposed": {"fix": "Use go with a time phrase."}},
                 {"type": "definition", "target_key": "en:go:v#7", "confidence": "high",
                  "problem": "No such sense.", "proposed": {"fix": "x"}},
             ]},
            {"key": "en:flibber:n", "findings": [
                {"type": "example", "target_key": "en:flibber:n", "confidence": "high",
                 "problem": "Unwritten.", "proposed": {"fix": "x"}}]},
            {"key": "en:nope:n"},
        ]  # fmt: skip
        result = client.post(f"/v1/audit/batches/{batch['batch_id']}/results", headers=auth,
                             json={"model": "claude-opus-5-5", "audit_version": VERSION,
                                   "entries": answers})  # fmt: skip
        assert result.status_code == 200, result.text
        outcomes = {item["key"]: item for item in result.json()["results"]}
        assert (outcomes["en:go:v"]["findings"], outcomes["en:go:v"]["applied"]) == (3, 1)
        assert outcomes["en:go:v"]["errors"] == ["definition en:go:v#7: no such key in the entry"]
        assert outcomes["en:flibber:n"]["errors"] == [
            "example en:flibber:n: nobody has written it yet; only its facts can be audited"
        ]
        assert outcomes["en:nope:n"]["outcome"] == "not_leased"
        again = client.post(f"/v1/audit/batches/{batch['batch_id']}/results", headers=auth,
                            json={"audit_version": VERSION, "entries": answers})  # fmt: skip
        assert again.status_code == 409

    assert rows(
        "select type, source, confidence, status, decided_by from audit_findings"
        " where entry_key = 'en:go:v' order by type"
    ) == [
        ("cefr", "rules", "high", "applied", "auto:audit"),
        ("content", "rules", "medium", "open", None),  # unanswered: the owner decides
        ("example", "ai", "medium", "open", None),
        ("part_of_speech", "rules", "medium", "dismissed", "auditor"),
    ]  # fmt: skip
    # Sent to repair: the factory leases it first and gets the finding as its repair notes,
    # and students keep seeing the published entry meanwhile.
    assert row("select status::text, attempts from entries where key = 'en:go:v'") == (
        "needs_repair", 0,
    )  # fmt: skip
    with connect() as conn:
        factory = lease.lease(conn, 1)
        [pack] = build(conn, factory.entry_ids)
    assert pack.key == "en:go:v" and pack.repair is not None
    assert pack.repair["audit"][0]["proposed"]["cefr"] == "B2"
    assert "en:go:v: The entry is A1" in pack.repair["review"]["notes"][0]
    # Audited entries wait until they're due again; the ones nobody answered are due now.
    assert row("select count(*) from entries where audited_at is not null") == (2,)
    assert row("select audit_version from entries where key = 'en:flibber:n'") == (VERSION,)


def test_the_owner_applies_or_rejects_the_rest_and_sees_how_far_to_trust_each_type(
    migrated_database_url, anyio_backend_options
):
    audit_world()
    auth = bearer("audit")
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        batch = leased(client, auth)
        items = {item["key"]: item for item in batch["items"]}
        go, twin = items["en:go:v"], items["en:by_the_way:idiom"]
        answers = [
            {"key": "en:go:v", "rules": [
                {"id": rule(go, "part_of_speech")["id"], "verdict": "confirm",
                 "confidence": "medium"},
                {"id": rule(go, "cefr")["id"], "verdict": "confirm", "confidence": "medium"}],
             "findings": [{"type": "example", "target_key": "en:go:v#1.ex1",
                           "confidence": "low", "problem": "Could be livelier.",
                           "proposed": {"fix": "Use a weekend plan."}}]},
            {"key": "en:by_the_way:idiom", "rules": [
                {"id": rule(twin, "duplicate")["id"], "verdict": "confirm",
                 "confidence": "high"}]},
        ]  # fmt: skip
        client.post(f"/v1/audit/batches/{batch['batch_id']}/results", headers=auth,
                    json={"audit_version": VERSION, "entries": answers})  # fmt: skip

        owner = admin()
        listing = client.get("/v1/admin/findings", headers=owner).json()
        assert listing["total"] == 5
        assert listing["items"][0]["confidence"] == "high"  # the most confident first
        by_type = {item["type"]: item for item in listing["items"]}
        assert by_type["duplicate"]["entry_status"] == "published"
        assert "fingerprint" not in by_type["duplicate"]["evidence"]

        chosen = [by_type[kind]["id"] for kind in ("part_of_speech", "duplicate", "cefr")]
        applied = client.post("/v1/admin/findings/apply", headers=owner,
                              json={"ids": chosen, "note": "agreed"}).json()  # fmt: skip
        assert {(r["entry_key"], r["outcome"]) for r in applied["results"]} == {
            ("en:go:v", "applied"), ("en:by_the_way:idiom", "applied"),
        }  # fmt: skip
        rejected = client.post("/v1/admin/findings/reject", headers=owner,
                               json={"ids": [by_type["example"]["id"]]}).json()  # fmt: skip
        assert rejected == {"rejected": 1}
        twice = client.post("/v1/admin/findings/apply", headers=owner,
                            json={"ids": [by_type["duplicate"]["id"]]}).json()  # fmt: skip
        assert twice["results"][0]["outcome"] == "skipped"

        stats = client.get("/v1/admin/stats", headers=owner).json()["audit"]
        assert stats["open"]["content"] == 1 and stats["open"]["duplicate"] == 0
        assert stats["last_run"]["audited"] == 2
        assert {(d["type"], d["applied"], d["rejected"])
                for d in stats["your_decisions_90d"]} == {
            ("cefr", 1, 0), ("duplicate", 1, 0), ("example", 0, 1), ("part_of_speech", 1, 0),
        }  # fmt: skip

    # The noun is the owner's word now, the twin is retired, and go goes to repair.
    assert row("select status::text, approved_by from entries where key = 'en:go:n'") == (
        "queued", f"lms:{PERSON}",
    )  # fmt: skip
    assert row("select status::text from entries where key = 'en:by_the_way:phr'") == ("retired",)
    assert row("select status::text from entries where key = 'en:go:v'") == ("needs_repair",)
    assert rows("select action from admin_audit order by id") == [
        ("finding.apply",), ("finding.reject",),
    ]  # fmt: skip

    # New instructions make every entry due again, but what was decided isn't raised again.
    with connect() as conn:
        conn.execute("update entries set status = 'published' where key = 'en:go:v'")
        conn.execute("update audit_findings set status = 'rejected' where status = 'open'")
        again = audit.lease(conn, 10, label=None, client="t", version="audit-new")
    go_again = next(item for item in again.items if item["key"] == "en:go:v")
    assert go_again["rule_findings"] == []
    assert {d["type"] for d in go_again["past_decisions"]} >= {"example"}


def test_with_the_switch_off_every_finding_waits(migrated_database_url, anyio_backend_options):
    audit_world()
    with connect() as conn:
        conn.execute("insert into app_settings (name, value, updated_by)"
                     " values ('audit_auto_repair', 'false', 'lms')")  # fmt: skip
    tunables.forget()
    auth = bearer("audit")
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        batch = leased(client, auth)
        go = next(item for item in batch["items"] if item["key"] == "en:go:v")
        answer = {
            "key": "en:go:v",
            "rules": [{"id": rule(go, "cefr")["id"], "verdict": "confirm", "confidence": "high"}],
        }
        client.post(f"/v1/audit/batches/{batch['batch_id']}/results", headers=auth,
                    json={"audit_version": VERSION, "entries": [answer]})  # fmt: skip
    assert row("select status from audit_findings where type = 'cefr'") == ("open",)
    assert row("select status::text from entries where key = 'en:go:v'") == ("published",)


def test_an_audit_runs_end_to_end_through_the_scripts(
    migrated_database_url, anyio_backend_options, tmp_path, capsys
):
    with connect() as conn:
        world(conn)
        publish(conn, EntryDoc.model_validate(go_doc()), produced_by={"source": "test"})
        token = tokens.create(conn, "audit", ["audit"])
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        api = client_module.Api(base=BASE, token=token, send=through(client))
        assert pull_audit.pull(api, 5, 2, "test run", tmp_path) == 0
        batch = json.loads((tmp_path / "batch.json").read_text(encoding="utf-8"))
        assert batch["audit_version"].startswith("audit-")
        [item] = json.loads((tmp_path / "groups" / "group-1.json").read_text(encoding="utf-8"))
        assert item["key"] == "en:go:v"
        finding = {"type": "spanish", "target_key": "en:go:v#1", "confidence": "medium",
                   "problem": "irse is a different meaning.",
                   "proposed": {"fix": "Drop irse."}}  # fmt: skip
        write(tmp_path / "out" / "group-1.json", [{"key": "en:go:v", "findings": [finding]}])
        assert submit_audit.submit(api, "test-model", tmp_path) == 0
        assert pull_audit.pull(api, 5, 2, None, tmp_path) == 2  # nothing due: go has a finding
    printed = capsys.readouterr().out
    assert "audited 1, findings 1, applied at once 0" in printed
    assert row("select model, audit_version like 'audit-%%' from audit_batches"
               " where status = 'submitted'") == ("test-model", True)  # fmt: skip
