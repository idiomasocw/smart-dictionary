"""The owner overrules the AI (admin.curation): sees the draft the reviewer turned down,
publishes it over its review, or sends any entry to repair with notes of their own, which the
factory's writer follows first and the audit never argues with."""

import json
from typing import Any

from fastapi.testclient import TestClient

from factory_docs import GO_DOC, go_doc, world
from smart_dictionary.api.app import create_app
from smart_dictionary.content.publish import publish
from smart_dictionary.core.entrydoc import EntryDoc
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import audit, lease
from smart_dictionary.factory.packs import Pack, build
from smart_dictionary.factory.queue import queue
from smart_dictionary.factory.submit import Submission, submit
from test_provisional import STUDENT, row
from test_related import entry
from test_word_requests import PERSON, admin

REVIEW = ["en:go:v#1.ex2: «voy al trabajo en bus» is fine, but say «autobús»."]


def turned_down(doc: dict[str, Any]) -> None:
    """The routine wrote go, and its reviewer failed it: back to the queue as a draft."""
    with connect() as conn:
        world(conn)
        queue(conn, lemmas=["go"])
        batch = lease.lease(conn, 1)
        assert batch.id is not None
        submit(conn, batch.id, [Submission("en:go:v", doc, "fail", REVIEW)], produced_by={},
               threshold=0.7)  # fmt: skip


def repair_pack() -> Pack:
    """Lease the entry as the factory would, read its pack, then let the lease run out."""
    with connect() as conn:
        batch = lease.lease(conn, 1)
        [pack] = build(conn, batch.entry_ids)
        conn.execute("update entries set lease_expires_at = now() - interval '1 minute'")
        lease.expire(conn)
    return pack


def test_you_see_the_draft_the_reviewer_turned_down_and_can_publish_it(
    migrated_database_url, anyio_backend_options
):
    turned_down(GO_DOC)
    # The next writer starts from the draft the notes are about, not from nothing.
    pack = repair_pack()
    assert pack.repair is not None and pack.repair["draft"]["senses"] == GO_DOC["senses"]
    assert pack.repair["review"]["notes"] == REVIEW

    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        auth = admin()
        detail = client.get("/v1/admin/entries/en:go:v", headers=auth).json()
        assert (detail["status"], detail["doc"]) == ("needs_repair", None)
        assert detail["draft"]["senses"] == GO_DOC["senses"] and detail["draft_at"]
        assert client.get("/v1/entries/en:go:v", headers=STUDENT).status_code == 404

        # You disagree with the reviewer: the draft is fine.
        published = client.post("/v1/admin/entries/en:go:v/publish", headers=auth,
                                json={"note": "bus is fine in Latin America"})  # fmt: skip
        assert published.status_code == 200, published.text
        body = published.json()
        assert (body["status"], body["quality"], body["draft"]) == ("published", 1.0, None)
        assert client.get("/v1/entries/en:go:v", headers=STUDENT).status_code == 200
        again = client.post("/v1/admin/entries/en:go:v/publish", headers=auth, json={})
        assert again.status_code == 409  # nothing waits any more

    assert row("select action, actor, after->>'overruled' from admin_audit") == (
        "entry.publish", PERSON, "review",
    )  # fmt: skip
    # The audit sees your call, and leaves it alone.
    with connect() as conn:
        [item] = audit.lease(conn, 1, label=None, client="t", version="v").items
    assert item["owner_decisions"][0]["decision"] == "published the draft over its review"


def test_a_draft_that_fails_the_checks_cant_be_published(
    migrated_database_url, anyio_backend_options
):
    broken = go_doc()
    broken["senses"][0]["examples"] = broken["senses"][0]["examples"][:1]  # V11: too few
    turned_down(broken)
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        refused = client.post("/v1/admin/entries/en:go:v/publish", headers=admin(), json={})
    assert refused.status_code == 422
    assert refused.json()["code"] == "fails_checks" and "V11" in refused.json()["detail"]
    assert row("select status::text from entries where key = 'en:go:v'") == ("needs_repair",)


def test_your_notes_send_any_entry_to_repair_and_come_first(
    migrated_database_url, anyio_backend_options
):
    turned_down(GO_DOC)
    with connect() as conn:
        entry(conn, "en:kettle:n", "kettle", "noun", status="queued", written=False)
        noun = json.loads(json.dumps(go_doc()).replace("en:go:v", "en:go:n"))
        publish(conn, EntryDoc.model_validate({**noun, "pos": "noun"}),
                produced_by={"source": "test"})  # fmt: skip
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        auth = admin()
        # A draft the reviewer sent back: your note goes first, before the reviewer's.
        mine = {"notes": ["en:go:v#1: add the sense 'to leave' as #2."]}
        sent = client.post("/v1/admin/entries/en:go:v/repair", headers=auth, json=mine)
        assert sent.status_code == 200, sent.text
        pack = repair_pack()
        assert pack.repair is not None
        assert pack.repair["review"]["notes"] == ["owner: " + mine["notes"][0], *REVIEW]
        assert pack.repair["draft"]["senses"] == GO_DOC["senses"]
        # Only yours, when the reviewer's should go.
        only = {"notes": ["Keep «bus»."], "replace": True}
        assert client.post("/v1/admin/entries/en:go:v/repair", headers=auth,
                           json=only).status_code == 200  # fmt: skip
        assert repair_pack().repair["review"]["notes"] == ["owner: Keep «bus»."]  # pyright: ignore[reportOptionalSubscript]

        # A published entry: students keep seeing it until the rewrite publishes.
        fix = {"notes": ["en:go:n#1: wrong part of speech in the example."]}
        assert client.post("/v1/admin/entries/en:go:n/repair", headers=auth,
                           json=fix).status_code == 200  # fmt: skip
        assert client.get("/v1/entries/en:go:n", headers=STUDENT).status_code == 200
        # A word nobody wrote yet: the notes guide its first writing.
        first = {"notes": ["Cover the electric kettle first."]}
        assert client.post("/v1/admin/entries/en:kettle:n/repair", headers=auth,
                           json=first).status_code == 200  # fmt: skip
        with connect() as conn:
            conn.execute("update entries set status = 'generating' where key = 'en:go:v'")
        busy = client.post("/v1/admin/entries/en:go:v/repair", headers=auth, json=mine)
        assert busy.status_code == 409
    assert row("select count(*) from admin_audit where action = 'entry.repair'") == (4,)
    with connect() as conn:
        packs = {pack.key: pack for pack in build(conn, [
            found[0] for found in conn.execute(
                "update entries set leased_from = status, status = 'generating'"
                " where key in ('en:go:n', 'en:kettle:n') returning id")
        ])}  # fmt: skip
    assert packs["en:go:n"].repair["draft"]["key"] == "en:go:n"  # pyright: ignore[reportOptionalSubscript]
    assert packs["en:kettle:n"].repair["draft"] is None  # pyright: ignore[reportOptionalSubscript]
