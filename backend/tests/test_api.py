"""The v1 read API against the development fixtures, on a throwaway database."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import database_reachable, scratch_database
from smart_dictionary.api.app import create_app
from smart_dictionary.content.publish import load_fixtures
from smart_dictionary.db.sync import connect
from smart_dictionary.paths import FIXTURES_DIR


@pytest.fixture(scope="module")
def client(anyio_backend_options) -> Iterator[TestClient]:
    if not database_reachable():
        pytest.skip("no database reachable")
    with scratch_database(migrate=True):
        with connect() as conn:
            load_fixtures(conn, FIXTURES_DIR / "entries")
        with TestClient(create_app(), backend_options=anyio_backend_options) as test_client:
            yield test_client


def lookup(client: TestClient, q: str, **params: str) -> dict[str, Any]:
    response = client.get("/v1/lookup", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


def keys(body: dict[str, Any], side: str) -> list[str]:
    groups = [group for group in body["groups"] if group["side"] == side]
    return [item["entry_key"] for group in groups for item in group["items"]]


def test_entry_card_in_spanish(client):
    response = client.get("/v1/entries/en:actually:adv")
    assert response.status_code == 200
    card = response.json()
    sense = card["senses"][0]
    assert card["variety"] == "es-419"
    assert sense["l1"]["equivalents"][0]["term"] == "en realidad"
    assert sense["examples"][0]["translation"].startswith("Parece joven")
    # Only the note for Spanish speakers; the Portuguese one is filtered out.
    assert [note["key"] for note in sense["notes"]] == ["en:actually:adv#1.note1"]


def test_entry_card_in_portuguese(client):
    card = client.get("/v1/entries/en:actually:adv", params={"variety": "pt-BR"}).json()
    sense = card["senses"][0]
    assert sense["l1"]["equivalents"][0]["term"] == "na verdade"
    assert [note["key"] for note in sense["notes"]] == ["en:actually:adv#1.note2"]


def test_entry_errors_are_problem_details(client):
    missing = client.get("/v1/entries/en:nothing:n")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["code"] == "not_found"
    assert client.get("/v1/entries/not-a-key").status_code == 400
    sense_key = client.get("/v1/entries/en:actually:adv%231")
    assert sense_key.status_code == 400 and "en:actually:adv" in sense_key.json()["detail"]


def test_etag_and_teacher_hides(client):
    first = client.get("/v1/entries/en:actually:adv")
    etag = first.headers["etag"]
    cached = client.get("/v1/entries/en:actually:adv", headers={"If-None-Match": etag})
    assert cached.status_code == 304 and cached.headers["etag"] == etag
    with connect() as conn:
        conn.execute(
            "insert into content_flags (tenant_id, target_key, action, reason)"
            " values ('ocw', 'en:actually:adv#1.ex2', 'hide', 'test')"
        )
    second = client.get("/v1/entries/en:actually:adv")
    assert second.headers["etag"] != etag
    assert [e["key"] for e in second.json()["senses"][0]["examples"]] == ["en:actually:adv#1.ex1"]


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("actually", "en:actually:adv"),
        ("went", "en:go:v"),
        ("Ran into", "en:run_into:phrv"),
        ("well known", "en:well-known:adj"),
        ("realise", "en:realize:v"),
    ],
)
def test_lookup_english_forms(client, q, expected):
    assert keys(lookup(client, q), "en")[0] == expected


def test_ambiguous_forms_show_every_lemma(client):
    assert set(keys(lookup(client, "leaves"), "en")) == {"en:leaf:n", "en:leave:v"}


def test_lookup_from_spanish(client):
    body = lookup(client, "darse cuenta")
    items = next(group["items"] for group in body["groups"] if group["side"] == "l1")
    assert [item["entry_key"] for item in items] == ["en:realize:v", "en:notice:v"]
    assert items[0]["sense_key"] == "en:realize:v#1"
    assert items[0]["matched"] == {"via": "equivalent", "text": "darse cuenta"}


def test_direction_limits_the_side(client):
    # Among the fixtures, "real" is only a Spanish equivalent (of "actual").
    assert lookup(client, "real", dir="en")["groups"] == []
    assert keys(lookup(client, "real", dir="l1"), "l1") == ["en:actual:adj"]
    assert lookup(client, "went", dir="l1")["groups"] == []


def test_typos_get_did_you_mean(client):
    body = lookup(client, "beatiful")
    assert body["groups"] == []
    assert body["did_you_mean"][0]["entry_key"] == "en:beautiful:adj"
    spanish = lookup(client, "darse quenta")["did_you_mean"]
    found = {(s["entry_key"], s["side"], s["matched"]) for s in spanish}
    assert ("en:realize:v", "l1", "darse cuenta") in found


def test_unknown_words_are_logged_as_misses(client):
    lookup(client, "qwxzvb")
    lookup(client, "qwxzvb")
    with connect() as conn:
        hits = conn.execute("select hits from misses where query_norm = 'qwxzvb'").fetchone()
        logged = conn.execute(
            "select count(*) from lookups where query = 'qwxzvb' and result = 'miss'"
        ).fetchone()
    assert hits == (2,) and logged == (2,)


def test_suggest(client):
    items = client.get("/v1/suggest", params={"q": "act"}).json()["items"]
    assert [item["entry_key"] for item in items] == ["en:actual:adj", "en:actually:adv"]
    assert items[0]["equivalent"] == "real"


def test_suggest_from_spanish(client):
    items = client.get("/v1/suggest", params={"q": "romp", "dir": "l1"}).json()["items"]
    assert items[0]["entry_key"] == "en:break_the_ice:idiom"
    assert items[0]["matched"] == "romper el hielo"


def test_invalid_requests_are_problem_details(client):
    empty = client.get("/v1/lookup", params={"q": ""})
    assert empty.status_code == 422
    assert empty.headers["content-type"].startswith("application/problem+json")
    assert empty.json()["code"] == "invalid_request" and "query.q" in empty.json()["detail"]
    assert client.get("/v1/lookup", params={"q": "a\x01b"}).json()["code"] == "invalid_query"
    assert client.get("/v1/suggest", params={"q": "a"}).status_code == 422
    unknown = client.get("/v1/nothing-here")
    assert unknown.status_code == 404 and unknown.json()["code"] == "not_found"
