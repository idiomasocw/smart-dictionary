"""The admin API (design §5.5) [OP-1]: the owner's console in the LMS reads the dictionary's
state and changes a few settings, with a token of its own, and every change is audited."""

from typing import Any

from fastapi.testclient import TestClient

from factory_docs import GO_DOC, go_doc, world
from smart_dictionary.api.app import create_app
from smart_dictionary.core import tunables
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import lease, tokens
from smart_dictionary.factory.queue import queue
from smart_dictionary.factory.submit import Submission, submit

PERSON = "3f2c9a0e-5b7d-4c1a-9e2f-8d6b1a4c7e90"


def admin_token(name: str = "lms") -> str:
    with connect() as conn:
        return tokens.create(conn, name, ["admin"])


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def api(options: dict) -> TestClient:
    return TestClient(create_app(), backend_options=options)


def ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


def send_go_back_for_repair() -> None:
    """Queue the world's words and submit `go` with one example too few (V11)."""
    bad = go_doc()
    bad["senses"][0]["examples"] = bad["senses"][0]["examples"][:1]
    with connect() as conn:
        world(conn)
        queue(conn)
        batch = lease.lease(conn, 1)
        assert batch.id is not None
        submit(conn, batch.id, [Submission("en:go:v", bad)], produced_by={}, threshold=0.7)


def publish_go() -> None:
    with connect() as conn:
        batch = lease.lease(conn, 1)
        assert batch.id is not None and batch.entry_ids
        submit(conn, batch.id, [Submission("en:go:v", GO_DOC, "pass")], produced_by={},
               threshold=0.7)  # fmt: skip


def test_only_an_admin_token_opens_it(migrated_database_url, anyio_backend_options):
    token = admin_token()
    assert token.startswith(tokens.ADMIN_PREFIX)
    with connect() as conn:
        factory = tokens.create(conn, "routine", ["factory"])
        assert tokens.find(conn, token, "factory") is None
    with api(anyio_backend_options) as client:
        assert client.get("/v1/admin/stats").status_code == 401
        # Neither the routine's token nor the BBB proxy's headers are enough.
        assert client.get("/v1/admin/stats", headers=bearer(factory)).status_code == 401
        proxied = {"X-Dict-Secret": "anything", "X-BBB-Ext-User": PERSON, "X-BBB-Is-Mod": "true"}
        assert client.get("/v1/admin/settings", headers=proxied).status_code == 401
        assert client.get("/v1/admin/stats", headers=bearer("sda_guess")).status_code == 401

        answer = client.get("/v1/admin/stats", headers=bearer(token))
        assert answer.status_code == 200, answer.text
        assert answer.headers["cache-control"] == "no-store"

        with connect() as conn:
            assert tokens.revoke(conn, "lms", "factory") == 0  # another scope's
            assert tokens.revoke(conn, "lms", "admin") == 1
        assert client.get("/v1/admin/stats", headers=bearer(token)).status_code == 401


def test_entries_page_by_letter_and_status_with_why_one_was_sent_back(
    migrated_database_url, anyio_backend_options
):
    send_go_back_for_repair()
    with connect() as conn:
        lemmas = sorted(row[0] for row in conn.execute("select lemma_norm from entries"))
    auth = bearer(admin_token())
    with api(anyio_backend_options) as client:
        everything = ok(client.get("/v1/admin/entries", headers=auth))
        assert everything["total"] == len(lemmas) and everything["pages"] == 1
        assert [item["lemma"] for item in everything["items"]] == lemmas

        paged = ok(client.get("/v1/admin/entries", params={"per_page": 1, "page": 2}, headers=auth))
        assert paged["pages"] == len(lemmas) and [i["lemma"] for i in paged["items"]] == lemmas[1:2]

        by_letter = ok(client.get("/v1/admin/entries", params={"letter": "g"}, headers=auth))
        assert {item["lemma"] for item in by_letter["items"]} == {"go"}
        repair = ok(
            client.get("/v1/admin/entries", params={"status": "needs_repair"}, headers=auth)
        )
        [go] = repair["items"]
        assert go["key"] == "en:go:v" and go["attempts"] == 1 and go["definition"] is None
        assert go["problems"] and go["problems"][0].startswith("V11 ")
        assert ok(client.get("/v1/admin/entries", params={"q": "GO"}, headers=auth))["total"] == 1

        letters = ok(client.get("/v1/admin/entries/letters", headers=auth))
        assert len(letters) == 27 and letters[-1] == {"letter": "other", "count": 0}
        counts = {row["letter"]: row["count"] for row in letters}
        assert counts["g"] == 1 and sum(counts.values()) == len(lemmas)

        for wrong in ({"letter": "ab"}, {"status": "lost"}, {"per_page": 500}, {"page": 0}):
            assert client.get("/v1/admin/entries", params=wrong, headers=auth).status_code == 422

        detail = ok(client.get("/v1/admin/entries/en:go:v", headers=auth))
        assert detail["status"] == "needs_repair" and detail["doc"] is None
        [sent_back] = detail["revisions"]
        assert sent_back["outcome"] == "needs_repair" and sent_back["problems"][0].startswith("V11")

        publish_go()
        detail = ok(client.get("/v1/admin/entries/en:go:v", headers=auth))
        assert detail["status"] == "published" and detail["problems"] == []
        assert detail["definition"] == GO_DOC["senses"][0]["definition"]
        assert detail["senses"] == len(GO_DOC["senses"]) and detail["doc"]["key"] == "en:go:v"
        assert [revision["outcome"] for revision in detail["revisions"]] == [
            "published", "needs_repair",
        ]  # fmt: skip
        assert client.get("/v1/admin/entries/en:nothing:n", headers=auth).status_code == 404


def test_stats_and_misses_show_the_pipeline_without_naming_students(
    migrated_database_url, anyio_backend_options
):
    send_go_back_for_repair()
    with connect() as conn:
        queued = conn.execute("select count(*) from entries where status = 'queued'").fetchone()
        users = [
            conn.execute(
                "insert into users (tenant_id, external_id) values ('ocw', %s) returning id",
                (person,),
            ).fetchone()[0]  # pyright: ignore[reportOptionalSubscript]
            for person in ("person-1", "person-2")
        ]
        # person-1 sent 2 searches to AI (one took two calls), person-2 sent 1.
        for user, word, task, cost in (
            (users[0], "kettle", "provisional_entry", 0.002),
            (users[0], "tetera", "translate_query", 0.001),
            (users[0], "tetera", "provisional_entry", 0.002),
            (users[1], "kettle", "provisional_entry", 0.002),
        ):
            conn.execute(
                "insert into llm_calls (task, provider, model, prompt_version, outcome, user_id,"
                " ref_key, cost_usd, latency_ms) values (%s, 'deepseek', 'deepseek-chat', 'v',"
                " 'ok', %s, %s, %s, 900)",
                (task, user, word, cost),
            )
        conn.execute(
            "insert into misses (tenant_id, language, query_norm, status, attempts, last_error)"
            " values ('ocw', 'en', 'zorbify', 'pending', 3, 'NoEntry: unsuitable'),"
            " ('ocw', 'en', 'asdf', 'invalid', 0, null), ('ocw', 'es', 'tetera', 'provisional', 1,"
            " null)"
        )
    auth = bearer(admin_token())
    with api(anyio_backend_options) as client:
        ok(client.put("/v1/admin/settings/ai_searches_per_day", json={"value": 2}, headers=auth))
        stats = ok(client.get("/v1/admin/stats", headers=auth))
        statuses = {row["status"]: row["count"] for row in stats["entries"]["by_status"]}
        assert statuses["needs_repair"] == 1 and statuses["queued"] == queued[0]  # pyright: ignore[reportOptionalSubscript]
        assert stats["entries"]["waiting"] == 1 + queued[0]  # pyright: ignore[reportOptionalSubscript]
        assert stats["misses"] == {
            "by_status": {"pending": 1, "provisional": 1, "published": 0, "queued": 0,
                          "requestable": 0, "invalid": 1},
            "stuck": 1,
        }  # fmt: skip
        assert stats["requests"] == {"pending": 0, "pending_ai_entries": 0, "approved": 0,
                                     "rejected": 0}  # fmt: skip
        assert stats["entries"]["unapproved"] == 0
        ai = stats["ai"]
        assert (ai["searches_per_day"], ai["searches_last_24h"], ai["students_at_cap"]) == (2, 3, 1)
        assert ai["month_spend_usd"] == 0.007
        assert ai["calls_last_24h"] == [{"provider": "deepseek", "outcome": "ok", "calls": 4,
                                         "avg_latency_ms": 900, "cost_usd": 0.007}]  # fmt: skip
        assert ai["providers_with_keys"] == [] and ai["active_providers"] == []
        assert [run["state"] for run in stats["recent_runs"]] == ["submitted"]
        assert "person-1" not in str(stats)

        stuck = ok(client.get("/v1/admin/misses", params={"stuck": True}, headers=auth))
        [miss] = stuck["items"]
        assert (miss["query"], miss["stuck"], miss["last_error"]) == (
            "zorbify", True, "NoEntry: unsuitable",
        )  # fmt: skip
        spanish = ok(client.get("/v1/admin/misses", params={"q": "TETERA"}, headers=auth))
        assert [item["language"] for item in spanish["items"]] == ["es"]
        assert ok(client.get("/v1/admin/misses", headers=auth))["total"] == 3


def test_settings_change_within_bounds_apply_and_are_audited(
    migrated_database_url, anyio_backend_options
):
    auth = bearer(admin_token())
    actor = {"X-Admin-Actor": PERSON}
    with api(anyio_backend_options) as client:
        listed = ok(client.get("/v1/admin/settings", headers=auth))
        by_name = {setting["name"]: setting for setting in listed["settings"]}
        assert list(by_name) == list(tunables.TUNABLES)
        assert by_name["ai_searches_per_day"]["value"] == 30
        assert by_name["ai_provider_order"]["value"] == ["deepseek", "anthropic", "gemini", "groq"]
        assert not any(setting["overridden"] for setting in listed["settings"])
        secret = ("key", "secret", "password", "url", "token")
        assert not any(word in name for name in by_name for word in secret)

        order = client.put("/v1/admin/settings/ai_provider_order",
                           json={"value": ["Groq", "deepseek"]}, headers=auth | actor)  # fmt: skip
        changed = ok(order)
        assert changed["value"] == ["groq", "deepseek"] and changed["overridden"]
        assert changed["updated_by"] == "lms"
        with connect() as conn:
            assert tunables.overrides(conn)["ai_provider_order"] == ["groq", "deepseek"]

        for name, value in (
            ("ai_provider_order", ["openai"]),
            ("ai_provider_order", ["groq", "groq"]),
            ("ai_provider_order", "groq"),
            ("ai_monthly_budget_usd", 500),
            ("ai_searches_per_day", 2.5),
            ("ai_searches_per_day", True),
            ("factory_publish_threshold", 0.1),
        ):
            wrong = client.put(f"/v1/admin/settings/{name}", json={"value": value}, headers=auth)
            assert wrong.status_code == 422, (name, value)
            assert wrong.json()["code"] == "invalid_value"
        unknown = client.put("/v1/admin/settings/database_url", json={"value": "x"}, headers=auth)
        assert unknown.status_code == 404
        extra = client.put("/v1/admin/settings/ai_searches_per_day",
                           json={"value": 5, "as": "root"}, headers=auth)  # fmt: skip
        assert extra.status_code == 422
        named = {"X-Admin-Actor": "diego@example.com"}  # IDs only, never emails
        assert client.put("/v1/admin/settings/ai_searches_per_day", json={"value": 5},
                          headers=auth | named).status_code == 422  # fmt: skip

        reset = ok(client.delete("/v1/admin/settings/ai_provider_order", headers=auth | actor))
        assert not reset["overridden"] and reset["value"] == reset["default"]
        with connect() as conn:
            assert "ai_provider_order" not in tunables.overrides(conn)

        audit = ok(client.get("/v1/admin/audit", headers=auth))
        assert audit["total"] == 2
        assert [(row["action"], row["actor"], row["before"], row["after"])
                for row in audit["items"]] == [
            ("setting.reset", PERSON, ["groq", "deepseek"], None),
            ("setting.set", PERSON, None, ["groq", "deepseek"]),
        ]  # fmt: skip
        assert {row["client"] for row in audit["items"]} == {"lms"}


def test_a_stored_value_this_version_rejects_is_ignored(migrated_database_url):
    with connect() as conn:
        conn.execute(
            "insert into app_settings (name, value, updated_by) values"
            " ('ai_searches_per_day', '9000', 'old'), ('retired_setting', '1', 'old'),"
            " ('ai_monthly_budget_usd', '2.5', 'lms')"
        )
    tunables.forget()
    with connect() as conn:
        assert dict(tunables.overrides(conn)) == {"ai_monthly_budget_usd": 2.5}
