"""Missing words become provisional entries (design §6.5) [LK-10]: the lookup answers at once,
and the entry it schedules is there when the student asks again. A search in Spanish finds the
English word it means."""

import copy
from dataclasses import dataclass, field
from typing import Any, LiteralString

import pytest
from fastapi.testclient import TestClient

from factory_docs import world
from smart_dictionary.ai.gateway import Gateway, get_gateway
from smart_dictionary.ai.providers import ProviderError, Reply
from smart_dictionary.api.app import create_app
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import progress

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
TETERA = {"verdict": "found", "headwords": [{"lemma": "kettle", "pos": "noun", "kind": "word"}]}
# Drafts that can't publish, one per way of failing.
FAILS_CHECKS = copy.deepcopy(KETTLE)
FAILS_CHECKS["senses"][0]["example"] = "Diego boiled some water to make a cup of tea."  # no kettle
NO_KEY = {**KETTLE, "pos": "phrase"}  # a single word can't be a phrase: no key
ANOTHER_WORD = copy.deepcopy(KETTLE)
ANOTHER_WORD["lemma"] = "pot"
ANOTHER_WORD["senses"][0]["definition"] = "a deep round container used for cooking food"
ANOTHER_WORD["senses"][0]["example"] = "Diego put the pot on the stove to boil some water."


@dataclass
class Model:
    """A stand-in for the AI: the same draft for every provisional_entry call, and the same
    translation for every translate_query call (told apart by the schema asked for)."""

    draft: dict[str, Any]
    translation: dict[str, Any] | None = None
    name: str = "anthropic"
    model: str = "claude-haiku-5-5"
    paid: bool = True
    asked: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.asked)

    def complete(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> Reply:
        translating = "headwords" in schema["properties"]
        self.asked.append("translate_query" if translating else "provisional_entry")
        self.questions.append(user)
        answer = self.translation if translating else self.draft
        assert answer is not None
        return Reply(copy.deepcopy(answer), self.model, 900, 300)


class Broken(Model):
    """A provider that crashes in a way the gateway doesn't expect."""

    def complete(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> Reply:
        self.asked.append("crash")
        raise RuntimeError("the model broke")


@dataclass
class Down:
    """A provider that never answers in time."""

    name: str = "anthropic"
    model: str = "claude-haiku-5-5"
    paid: bool = True
    calls: int = 0

    def complete(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> Reply:
        self.calls += 1
        raise ProviderError("timeout", "no answer in 8s")


def cool_down_over() -> None:
    """Move the last attempt back past the cool-down, instead of waiting."""
    with connect() as conn:
        conn.execute("update misses set last_attempt_at = now() - interval '11 minutes'")


def miss(query: str) -> Any:
    return row(
        "select status, attempts, running_since is not null, last_error, entry_key from misses"
        " where query_norm = %s",
        query,
    )


def kettle_world() -> None:
    with connect() as conn:
        world(conn)
        for language, word, zipf in (("en", "kettle", 3.4), ("en", "kettles", 2.6),
                                     ("es", "tetera", 2.6)):  # fmt: skip
            conn.execute(
                "insert into ref_freq (dataset_id, language, word, zipf)"
                " values ('t-freq', %s, %s, %s)",
                (language, word, zipf),
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
        # A search in Spanish takes the Spanish path, whose gate knows no "kettle".
        assert lookup(api, "kettle", dir="l1")["provisional"]["status"] == "invalid"
        # Only Spanish has a path so far.
        assert lookup(api, "kettle", dir="l1", variety="pt-BR")["provisional"] is None
    with client(anyio_backend_options, Gateway([], 5.0)) as api:
        assert lookup(api, "kettles")["provisional"]["status"] == "unavailable"
    assert model.calls == 0


def test_each_student_sends_at_most_ten_searches_a_day(
    migrated_database_url, anyio_backend_options
):
    kettle_world()
    with connect() as conn:
        user = conn.execute(
            "insert into users (tenant_id, external_id) values ('ocw', 'person-1') returning id"
        ).fetchone()
        # Searches count, not calls: these 12 calls are 10 searches, of both kinds.
        for number in range(12):
            task = "translate_query" if number % 2 else "provisional_entry"
            conn.execute(
                "insert into llm_calls (task, provider, model, prompt_version, outcome,"
                " user_id, ref_key) values (%s, 'anthropic', 'm', 'v', 'ok', %s, %s)",
                (task, user[0] if user else None, f"word{min(number, 9)}"),
            )
    model = Model(KETTLE, TETERA)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        assert lookup(api, "kettles")["provisional"]["status"] == "quota_exceeded"
        assert lookup(api, "tetera")["provisional"]["status"] == "quota_exceeded"
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
    model = Model(FAILS_CHECKS)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        assert lookup(api, "kettles")["provisional"]["status"] == "generating"
        # The attempt failed: "unavailable" until the cool-down ends, with no new call.
        assert lookup(api, "kettles")["provisional"]["status"] == "unavailable"
    assert model.calls == 1
    assert row("select count(*) from entries where key = 'en:kettle:n'") == (0,)
    status, attempts, running, error, _ = miss("kettles")
    assert (status, attempts, running) == ("pending", 1, False)
    assert error.startswith("the draft failed its checks: V3")


@pytest.mark.parametrize(
    ("provider", "query", "calls", "reason"),
    [
        (Down(), "kettles", 1, "no AI provider answered provisional_entry"),
        (Model(FAILS_CHECKS), "kettles", 1, "the draft failed its checks"),
        (Model(NO_KEY), "kettles", 1, "the draft can't make an entry"),
        (Model(ANOTHER_WORD), "kettles", 1, "the draft is about 'pot' instead"),
        (Broken(KETTLE), "kettles", 1, "error: RuntimeError: the model broke"),
        (Down(), "tetera", 1, "no AI provider answered translate_query"),
        (Model(NOT_A_WORD, TETERA), "tetera", 2, "which the draft turned down"),
        (Broken(KETTLE, TETERA), "tetera", 1, "error: RuntimeError: the model broke"),
    ],
    ids=["no-provider", "checks", "no-key", "another-word", "error",
         "es-no-provider", "es-turned-down", "es-error"],
)  # fmt: skip
def test_every_failure_answers_unavailable_through_the_plugins_polling(
    provider, query, calls, reason, migrated_database_url, anyio_backend_options
):
    kettle_world()
    with client(anyio_backend_options, Gateway([provider], 5.0)) as api:
        assert lookup(api, query)["provisional"]["status"] == "generating"
        # The plugin asks about 21 times in 30 s: after the failure, each answer comes at once
        # and starts nothing.
        for _ in range(20):
            assert lookup(api, query)["provisional"] == {
                "status": "unavailable", "entry_key": None, "retry_after_ms": None,
            }  # fmt: skip
    assert provider.calls == calls  # one attempt
    status, attempts, running, error, _ = miss(query)
    assert (status, attempts, running) == ("pending", 1, False)
    assert reason in error


def test_after_the_cool_down_one_new_attempt_and_after_three_the_factory(
    migrated_database_url, anyio_backend_options
):
    kettle_world()
    with connect() as conn:  # the owner queued the word; nobody has written it yet
        conn.execute(
            "insert into entries (key, language, lemma, lemma_norm, pos, kind, status, priority)"
            " values ('en:kettle:n', 'en', 'kettle', 'kettle', 'noun', 'word', 'queued', 7)"
        )
    model = Model(FAILS_CHECKS)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        for attempt in (1, 2, 3):
            assert lookup(api, "kettles")["provisional"]["status"] == "generating"
            assert model.calls == attempt  # exactly one new attempt per cool-down
            if attempt < 3:
                assert lookup(api, "kettles")["provisional"]["status"] == "unavailable"
                assert model.calls == attempt
                cool_down_over()
        # The third failure handed the word to the factory, first in its queue.
        queued = {"status": "queued", "entry_key": "en:kettle:n", "retry_after_ms": None}
        assert lookup(api, "kettles")["provisional"] == queued
        cool_down_over()
        assert lookup(api, "kettles")["provisional"] == queued
    assert model.calls == 3
    assert row("select status::text, priority from entries where key = 'en:kettle:n'") == (
        "queued", 100_000,
    )  # fmt: skip


def test_a_failing_word_is_queued_from_its_seed_candidate(
    migrated_database_url, anyio_backend_options
):
    kettle_world()
    with connect() as conn:  # "realize" is a seed candidate with no entry yet
        conn.execute("insert into ref_freq (dataset_id, language, word, zipf)"
                     " values ('t-freq', 'en', 'realize', 4.8)")  # fmt: skip
    down = Down()
    with client(anyio_backend_options, Gateway([down], 5.0)) as api:
        for _ in range(3):
            assert lookup(api, "realize")["provisional"]["status"] == "generating"
            cool_down_over()
        assert lookup(api, "realize")["provisional"] == {
            "status": "queued", "entry_key": "en:realize:v", "retry_after_ms": None,
        }  # fmt: skip
    assert down.calls == 3
    assert row("select status::text, priority from entries where key = 'en:realize:v'") == (
        "queued", 100_000,
    )  # fmt: skip


def test_a_word_the_factory_cannot_take_stays_unavailable_and_listed(
    migrated_database_url, anyio_backend_options
):
    kettle_world()  # no seed candidate for "kettle"
    down = Down()
    with client(anyio_backend_options, Gateway([down], 5.0)) as api:
        for _ in range(3):
            assert lookup(api, "kettles")["provisional"]["status"] == "generating"
            cool_down_over()
        # No more automatic attempts, however long the student waits.
        assert lookup(api, "kettles")["provisional"]["status"] == "unavailable"
    assert down.calls == 3
    assert miss("kettles")[:3] == ("pending", 3, False)
    with connect() as conn:
        lines = progress.report(conn)
    assert any("kettles" in line and "no AI provider answered" in line for line in lines)


def test_a_running_job_starts_no_second_one_and_its_claim_expires(
    migrated_database_url, anyio_backend_options
):
    kettle_world()
    with connect() as conn:  # a job claimed the word a moment ago and is still at work
        conn.execute(
            "insert into misses (tenant_id, language, query_norm, hits, running_since)"
            " values ('ocw', 'en', 'kettles', 0, now())"
        )
    model = Model(KETTLE)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        for _ in range(21):
            assert lookup(api, "kettles")["provisional"]["status"] == "generating"
        assert model.calls == 0
        # The job never finished (a crash): its claim expires, and the next lookup tries.
        with connect() as conn:
            conn.execute("update misses set running_since = now() - interval '4 minutes'")
        assert lookup(api, "kettles")["provisional"]["status"] == "generating"
        assert lookup(api, "kettles")["groups"]
    assert model.calls == 1


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


def test_a_spanish_search_finds_its_english_word(migrated_database_url, anyio_backend_options):
    kettle_world()
    model = Model(KETTLE, TETERA)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        assert lookup(api, "Tetera")["provisional"]["status"] == "generating"
        # The draft lists the student's Spanish, so the next search reaches it directly.
        [group] = lookup(api, "tetera")["groups"]
        [item] = group["items"]
        assert (group["side"], item["entry_key"], item["provisional"]) == (
            "l1", "en:kettle:n", True,
        )  # fmt: skip
        assert item["matched"] == {"via": "equivalent", "text": "tetera"}
    assert model.asked == ["translate_query", "provisional_entry"]
    assert "«Tetera»" in model.questions[1]  # the draft is asked for that meaning
    terms = row("select array_agg(term order by rank) from equivalents"
                " where sense_key = 'en:kettle:n#1'")  # fmt: skip
    assert terms == (["hervidor", "tetera"],)
    assert row("select status, entry_key from misses where language = 'es'") == (
        "provisional", "en:kettle:n",
    )  # fmt: skip
    # Two calls, one search.
    assert row("select count(*), count(distinct ref_key), min(ref_key) from llm_calls") == (
        2, 1, "tetera",
    )  # fmt: skip


def test_a_spanish_search_for_a_live_word_points_to_it(
    migrated_database_url, anyio_backend_options
):
    kettle_world()
    model = Model(KETTLE, TETERA)
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        lookup(api, "kettles")  # an English search drafts the entry first
        assert lookup(api, "tetera", dir="l1")["provisional"]["status"] == "generating"
        # Live already, without "tetera" among its equivalents: the miss points to it.
        assert lookup(api, "tetera", dir="l1")["provisional"] == {
            "status": "ready", "entry_key": "en:kettle:n", "retry_after_ms": None,
        }  # fmt: skip
    assert model.asked == ["provisional_entry", "translate_query"]


def test_misses_take_the_path_of_their_language(migrated_database_url, anyio_backend_options):
    kettle_world()
    model = Model(KETTLE, {"verdict": "none", "headwords": []})
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        lookup(api, "kettles")  # reads as English
        lookup(api, "tetera")  # reads as Spanish
        # Turned down: settled, so the next search answers without a call.
        assert lookup(api, "tetera")["provisional"]["status"] == "invalid"
    assert model.asked == ["provisional_entry", "translate_query"]
    with connect() as conn:
        misses = conn.execute("select language, query_norm, status from misses order by 1")
        assert misses.fetchall() == [
            ("en", "kettles", "provisional"), ("es", "tetera", "invalid"),
        ]  # fmt: skip


def test_one_job_per_query_at_a_time(migrated_database_url, anyio_backend_options):
    kettle_world()
    model = Model(KETTLE, TETERA)
    lock = "select pg_advisory_lock(hashtextextended('provisional:ocw:es:tetera', 0))"
    with connect() as holder:
        # Another request is handling "tetera": this one's job leaves it alone, and its claim
        # stands, so the next lookups start no job either.
        holder.execute(lock)
        with client(anyio_backend_options, Gateway([model], 5.0)) as api:
            assert lookup(api, "tetera")["provisional"]["status"] == "generating"
            assert lookup(api, "tetera")["provisional"]["status"] == "generating"
        assert model.calls == 0
    with connect() as conn:  # the other request never finished: the claim expires
        conn.execute("update misses set running_since = now() - interval '4 minutes'")
    with client(anyio_backend_options, Gateway([model], 5.0)) as api:
        assert lookup(api, "tetera")["provisional"]["status"] == "generating"
    assert model.asked == ["translate_query", "provisional_entry"]
