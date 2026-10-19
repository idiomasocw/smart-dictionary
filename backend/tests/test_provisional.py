"""Missing words become provisional entries (design §6.5) [LK-10]: the lookup answers at once,
and the entry it schedules is there when the student asks again."""

import copy
from dataclasses import dataclass, field
from typing import Any, LiteralString

from fastapi.testclient import TestClient

from factory_docs import world
from smart_dictionary.ai.gateway import Gateway, get_gateway
from smart_dictionary.ai.providers import Reply
from smart_dictionary.api.app import create_app
from smart_dictionary.db.sync import connect

STUDENT = {"x-bbb-ext-user": "person-1"}

KETTLE: dict[str, Any] = {
    "verdict": "entry",
    "lemma": "kettle",
    "pos": "noun",
    "kind": "word",
    "senses": [
        {
            "cefr": "A2",
            "definition": "a metal or plastic pot with a lid, used for boiling water",
            "short_gloss": "pot for boiling water",
            "gloss_es": "recipiente con tapa que se usa para hervir agua",
            "equivalents": [{"term": "hervidor", "match": "exact"}],
            "example": "Diego put the kettle on to make some tea.",
            "example_es": "Diego puso el hervidor para hacer té.",
        }
    ],
}
NOT_A_WORD = {"verdict": "not_a_word", "lemma": None, "pos": None, "kind": None, "senses": []}


@dataclass
class Model:
    """A stand-in for the AI: every call returns the same draft."""

    draft: dict[str, Any]
    name: str = "anthropic"
    model: str = "claude-haiku-5-5"
    paid: bool = True
    calls: int = field(default=0)

    def complete(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> Reply:
        self.calls += 1
        return Reply(copy.deepcopy(self.draft), self.model, 900, 300)


def kettle_world() -> None:
    with connect() as conn:
        world(conn)
        for word, zipf in (("kettle", 3.4), ("kettles", 2.6)):
            conn.execute(
                "insert into ref_freq (dataset_id, language, word, zipf)"
                " values ('t-freq', 'en', %s, %s)",
                (word, zipf),
            )
        for variety, ipa in (("en-US", "ˈkɛtəl"), ("en-GB", "ˈketl")):
            conn.execute(
                "insert into ref_ipa (dataset_id, word, word_norm, variety, ipa)"
                " values ('t-ipa', 'kettle', 'kettle', %s, %s)",
                (variety, ipa),
            )


def client(options: dict, gateway: Gateway) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_gateway] = lambda: gateway
    return TestClient(app, backend_options=options)


def lookup(api: TestClient, q: str, **params: str) -> dict[str, Any]:
    response = api.get("/v1/lookup", params={"q": q, **params}, headers=STUDENT)
    assert response.status_code == 200, response.text
    return response.json()


def row(sql: LiteralString, *params: Any) -> Any:
    with connect() as conn:
        return conn.execute(sql, params).fetchone()


def test_a_missing_word_becomes_a_provisional_entry(migrated_database_url, anyio_backend_options):
    kettle_world()
    model = Model(KETTLE)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        first = lookup(api, "kettles")
        assert first["groups"] == []
        assert first["provisional"] == {
            "status": "generating", "entry_key": None, "retry_after_ms": 1500,
        }  # fmt: skip

        # The generation ran after the response; asking again finds the entry.
        again = lookup(api, "kettles")
        [item] = again["groups"][0]["items"]
        assert (item["entry_key"], item["provisional"]) == ("en:kettle:n", True)
        assert again["provisional"] is None
        card = api.get("/v1/entries/en:kettle:n", headers=STUDENT).json()
        assert card["provisional"] is True
        assert card["pronunciations"][0]["ipa"] == "ˈkɛtəl"  # the reference IPA
    assert model.calls == 1

    entry = row("select status::text, quality, sources, doc->>'provisional' from entries"
                " where key = 'en:kettle:n'")  # fmt: skip
    assert entry[0] == "provisional" and entry[2] == ["provisional"] and entry[3] == "true"
    assert 0 < float(entry[1]) < 1  # warnings only: the test world's word lists are tiny
    assert row("select status, entry_key from misses where query_norm = 'kettles'") == (
        "provisional", "en:kettle:n",
    )  # fmt: skip
    call = row("select l.outcome, u.external_id from llm_calls l join users u on u.id = l.user_id")
    assert call == ("ok", "person-1")
    produced = row("select produced_by from entry_revisions")[0]
    assert produced["source"] == "provisional" and produced["prompt"] == "provisional_entry.v1"


def test_the_lookup_decides_without_ai(migrated_database_url, anyio_backend_options):
    kettle_world()
    model = Model(KETTLE)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        assert lookup(api, "zzqxv")["provisional"] == {
            "status": "invalid", "entry_key": None, "retry_after_ms": None,
        }  # fmt: skip
        assert lookup(api, "porn")["provisional"]["status"] == "invalid"
        five_words = "kettle kettle kettle kettle kettle"
        assert lookup(api, five_words)["provisional"]["status"] == "invalid"
        assert lookup(api, "kettle", dir="l1")["provisional"] is None
    with client(anyio_backend_options, Gateway([], 5.0)) as api:
        assert lookup(api, "kettles")["provisional"]["status"] == "unavailable"
    assert model.calls == 0


def test_each_student_starts_at_most_ten_a_day(migrated_database_url, anyio_backend_options):
    kettle_world()
    with connect() as conn:
        user = conn.execute(
            "insert into users (tenant_id, external_id) values ('ocw', 'person-1') returning id"
        ).fetchone()
        for _ in range(10):
            conn.execute(
                "insert into llm_calls (task, provider, model, prompt_version, outcome, user_id)"
                " values ('provisional_entry', 'anthropic', 'm', 'v', 'ok', %s)",
                (user[0] if user else None,),
            )
    model = Model(KETTLE)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        assert lookup(api, "kettles")["provisional"]["status"] == "quota_exceeded"
    assert model.calls == 0


def test_a_word_the_model_rejects_is_settled(migrated_database_url, anyio_backend_options):
    kettle_world()
    model = Model(NOT_A_WORD)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        assert lookup(api, "kettles")["provisional"]["status"] == "generating"
        assert lookup(api, "kettles")["provisional"]["status"] == "invalid"
    assert model.calls == 1
    assert row("select status from misses where query_norm = 'kettles'") == ("invalid",)


def test_a_draft_that_fails_the_checks_is_not_published(
    migrated_database_url, anyio_backend_options
):
    kettle_world()
    draft = copy.deepcopy(KETTLE)
    draft["senses"][0]["example"] = "Diego boiled some water to make a cup of tea."  # no kettle
    model = Model(draft)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        assert lookup(api, "kettles")["provisional"]["status"] == "generating"
        # Nothing was published or cached, so the next lookup tries again.
        assert lookup(api, "kettles")["provisional"]["status"] == "generating"
    assert model.calls == 2
    assert row("select count(*) from entries where key = 'en:kettle:n'") == (0,)
    assert row("select status from misses where query_norm = 'kettles'") == ("pending",)


def test_the_factorys_entries_are_never_replaced(migrated_database_url, anyio_backend_options):
    kettle_world()
    with connect() as conn:
        conn.execute(
            "insert into entries (key, language, lemma, lemma_norm, pos, kind, status, priority)"
            " values ('en:kettle:n', 'en', 'kettle', 'kettle', 'noun', 'word', 'generating', 7)"
        )
    with client(anyio_backend_options, Gateway([Model(KETTLE)], 5.0)) as api:
        lookup(api, "kettles")
        # The routine is writing it: the miss points there and the lease stays untouched.
        assert lookup(api, "kettles")["provisional"] == {
            "status": "queued", "entry_key": "en:kettle:n", "retry_after_ms": None,
        }  # fmt: skip
    assert row("select status::text, doc from entries where key = 'en:kettle:n'") == (
        "generating", None,
    )  # fmt: skip

    # A queued word nobody has written yet is taken over, keeping its place in the queue.
    with connect() as conn:
        conn.execute("update entries set status = 'queued' where key = 'en:kettle:n'")
        conn.execute("update misses set status = 'pending', entry_key = null")
    with client(anyio_backend_options, Gateway([Model(KETTLE)], 5.0)) as api:
        lookup(api, "kettles")
    assert row("select status::text, priority from entries where key = 'en:kettle:n'") == (
        "provisional", 7,
    )  # fmt: skip
