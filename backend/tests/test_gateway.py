"""The AI gateway (design §7): fallbacks, retries, the breaker, the budget, logs and the cache."""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, Field, SecretStr

from smart_dictionary.ai.gateway import Breaker, Gateway, Task, Unavailable, cost
from smart_dictionary.ai.providers import (
    GEMINI_MODEL,
    Claude,
    OpenAICompatible,
    ProviderError,
    Reply,
    api_schema,
    chain,
)
from smart_dictionary.core import tunables
from smart_dictionary.core.settings import Settings, get_settings
from smart_dictionary.db.sync import connect


class Word(BaseModel):
    word: str = Field(max_length=10)


TASK = Task(name="test_task", prompt_version="test.v1", system="Answer.", output=Word,
            max_tokens=50)  # fmt: skip


@dataclass
class Fake:
    """Answers in order: a dict is the reply's data, an exception is raised."""

    name: str
    answers: list[Any]
    paid: bool = True
    model: str = "claude-haiku-5-5"
    calls: int = field(default=0)

    def complete(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> Reply:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Reply(answer, self.model, 100, 50)


def busy() -> ProviderError:
    return ProviderError("error", "answered 503", retry=True)


def outcomes(conn: Any) -> list[tuple[str, str]]:
    return conn.execute("select provider, outcome from llm_calls order by id").fetchall()


def test_the_next_provider_answers_when_one_fails(migrated_database_url):
    first, second = Fake("first", [busy(), busy()]), Fake("second", [{"word": "go"}])
    with connect() as conn:
        answer = Gateway([first, second], 5.0).run(conn, TASK, "go", tenant="ocw")
        assert (answer.value, answer.provider, answer.cached) == (Word(word="go"), "second", False)
        # One retry after a 503, then the next provider; every attempt is logged.
        assert outcomes(conn) == [("first", "error"), ("first", "error"), ("second", "fallback")]
        sql = "select cost_usd, prompt_version from llm_calls order by id desc"
        row = conn.execute(sql).fetchone()
    assert row is not None
    assert float(row[0]) == cost("claude-haiku-5-5", 100, 50) == 0.000035
    assert row[1] == "test.v1"


def test_a_retry_that_succeeds_is_ok_and_bad_answers_move_on(migrated_database_url):
    with connect() as conn:
        retried = Fake("only", [busy(), {"word": "go"}])
        assert Gateway([retried], 5.0).run(conn, TASK, "go").provider == "only"
        assert outcomes(conn) == [("only", "error"), ("only", "ok")]

        # A timeout and an answer that fails validation get no retry.
        slow = Fake("slow", [ProviderError("timeout", "no answer")])
        wrong = Fake("wrong", [{"word": "far too long for the model"}])
        right = Fake("right", [{"word": "go"}])
        assert Gateway([slow, wrong, right], 5.0).run(conn, TASK, "go").provider == "right"
        assert (slow.calls, wrong.calls, right.calls) == (1, 1, 1)

        refusing = Fake("down", [ProviderError("refused", "declined")])
        with pytest.raises(Unavailable):
            Gateway([refusing], 5.0).run(conn, TASK, "x")
        with pytest.raises(Unavailable):
            Gateway([], 5.0).run(conn, TASK, "x")


def test_the_breaker_keeps_a_failing_provider_out_for_a_while():
    now = [0.0]
    breaker = Breaker(clock=lambda: now[0])
    for _ in range(4):
        breaker.failed("p")
    assert not breaker.is_open("p")
    breaker.failed("p")
    assert breaker.is_open("p")
    now[0] += 121
    assert not breaker.is_open("p")
    # Failures spread over more than a minute don't add up.
    for _ in range(5):
        now[0] += 20
        breaker.failed("q")
    assert not breaker.is_open("q")


def test_at_the_monthly_cap_only_the_free_provider_answers(migrated_database_url):
    with connect() as conn:
        conn.execute(
            "insert into llm_calls (task, provider, model, prompt_version, cost_usd, outcome)"
            " values ('earlier', 'anthropic', 'claude-haiku-5-5', 'x', 5.0, 'ok')"
        )
        paid, free = Fake("paid", [{"word": "go"}]), Fake("free", [{"word": "go"}], paid=False)
        assert Gateway([paid, free], 5.0).run(conn, TASK, "go").provider == "free"
        assert paid.calls == 0
        with pytest.raises(Unavailable):
            Gateway([Fake("paid", [{"word": "go"}])], 5.0).run(conn, TASK, "go")


def test_an_answer_can_be_kept_and_forgotten(migrated_database_url):
    provider = Fake("only", [{"word": "go"}, {"word": "went"}])
    gateway = Gateway([provider], 5.0)
    with connect() as conn:
        answer = gateway.run(conn, TASK, "go", cache_key="go")
        gateway.remember(conn, TASK, "go", answer)
        again = gateway.run(conn, TASK, "go", cache_key="go")
        assert (again.value, again.cached, provider.calls) == (Word(word="go"), True, 1)
        gateway.forget(conn, TASK, "go")
        assert gateway.run(conn, TASK, "go", cache_key="go").value == Word(word="went")


def test_the_schema_sent_is_one_structured_outputs_accept():
    class Inner(BaseModel):
        title: str = Field(max_length=5)  # a property named like a keyword stays

    class Outer(BaseModel):
        items: list[Inner] = Field(max_length=2)
        note: str | None = None

    schema = api_schema(Outer)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            assert not {"maxLength", "maxItems", "default"} & set(node)
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert schema["required"] == ["items", "note"]  # optional fields come back as null
    assert "title" in schema["$defs"]["Inner"]["properties"]


def test_claude_reads_the_structured_answer_and_maps_failures():
    sent: dict[str, Any] = {}

    def create(**request: Any) -> Any:
        sent.update(request)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text",
                                                                       text='{"word": "go"}')],
            model="claude-haiku-5-5",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )  # fmt: skip

    reply = Claude(key="k", create=create).complete("Answer.", "go", {"type": "object"}, 50)
    assert (reply.data, reply.in_tokens, reply.out_tokens) == ({"word": "go"}, 10, 5)
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    }

    def refusing(**request: Any) -> Any:
        return SimpleNamespace(stop_reason="refusal", content=[], model="m", usage=None)

    with pytest.raises(ProviderError) as refused:
        Claude(key="k", create=refusing).complete("s", "u", {}, 50)
    assert refused.value.failure == "refused"

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def failing(error: Exception) -> Claude:
        def create(**request: Any) -> Any:
            raise error

        return Claude(key="k", create=create)

    cases = [
        (anthropic.APITimeoutError(request=request), "timeout", False),
        (anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=request),
                                  body=None), "error", True),
        (anthropic.InternalServerError("boom", response=httpx2.Response(500, request=request),
                                       body=None), "error", True),
        (anthropic.BadRequestError("bad", response=httpx2.Response(400, request=request),
                                   body=None), "error", False),
    ]  # fmt: skip
    for error, failure, retry in cases:
        with pytest.raises(ProviderError) as raised:
            failing(error).complete("s", "u", {}, 50)
        assert (raised.value.failure, raised.value.retry) == (failure, retry)


KEYS = {name: SecretStr(name) for name in
        ("anthropic_api_key", "deepseek_api_key", "gemini_api_key", "groq_api_key")}  # fmt: skip


def test_the_providers_answer_in_the_order_the_owner_set():
    # Fastest first by default: DeepSeek leads, and Groq's free tier stays last.
    providers = chain(get_settings().model_copy(update=KEYS))
    assert [(provider.name, provider.paid) for provider in providers] == [
        ("deepseek", True), ("anthropic", True), ("gemini", True), ("groq", False),
    ]  # fmt: skip
    # Reordered, and a provider left out isn't used, though it has a key.
    settings = Settings.model_validate({"database_url": "postgresql+psycopg://x",
                                        "ai_provider_order": " Groq, gemini "})  # fmt: skip
    assert settings.ai_providers == ["groq", "gemini"]
    reordered = get_settings().model_copy(update={**KEYS, "ai_provider_order": "groq,gemini"})
    assert [provider.name for provider in chain(reordered)] == ["groq", "gemini"]
    # A typo or a repeat drops that name, with a warning, but never stops the API: a failed
    # deploy's rollback would read the same setting.
    for wrong, used in (("deepseek,openai", ["deepseek"]), ("groq,groq", ["groq"]),
                        ("dipseek", [])):  # fmt: skip
        settings = Settings.model_validate({"database_url": "postgresql+psycopg://x",
                                            "ai_provider_order": wrong})  # fmt: skip
        assert settings.ai_providers == used


def test_the_owners_order_and_cap_from_the_admin_api_win(migrated_database_url):
    def run() -> str | None:
        """Which provider answers, with DeepSeek and Claude in the environment's order and
        Groq's free tier keyed but left out of it."""
        gateway = Gateway(
            [Fake("deepseek", [{"word": "go"}]), Fake("anthropic", [{"word": "go"}])],
            5.0,
            spares=[Fake("groq", [{"word": "go"}], paid=False)],
        )
        with connect() as conn:
            try:
                return gateway.run(conn, TASK, "go").provider
            except Unavailable:
                return None

    def override(name: str, value: str | None) -> None:
        with connect() as conn:
            conn.execute("delete from app_settings where name = %s", (name,))
            if value is not None:
                conn.execute(
                    "insert into app_settings (name, value, updated_by) values (%s, %s, 'lms')",
                    (name, value),
                )
        tunables.forget()

    assert run() == "deepseek"
    override("ai_provider_order", '["groq", "deepseek"]')
    assert run() == "groq"  # the owner's order brings in a keyed provider the environment left out
    override("ai_provider_order", "[]")
    assert run() is None  # turned off
    override("ai_provider_order", None)
    override("ai_monthly_budget_usd", "0")
    assert run() is None  # at the cap, and the free provider isn't in the order
    override("ai_provider_order", '["anthropic", "groq"]')
    assert run() == "groq"


def test_gemini_answers_through_its_openai_endpoint():
    providers = chain(get_settings().model_copy(update=KEYS))
    gemini = providers[2]
    assert isinstance(gemini, OpenAICompatible)

    sent: dict[str, Any] = {}

    def transport(
        url: str, headers: dict[str, str], body: dict[str, Any], timeout: float
    ) -> tuple[int, Any]:
        sent.update(url=url, headers=headers, body=body)
        return 200, {
            "model": GEMINI_MODEL,
            "choices": [{"message": {"content": '{"word": "go"}'}}],
            # The thinking is billed as output but left out of completion_tokens.
            "usage": {"prompt_tokens": 1000, "completion_tokens": 300, "total_tokens": 1800},
        }

    gemini.transport = transport
    reply = gemini.complete("Answer.", "go", api_schema(Word), 900)
    assert sent["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert sent["headers"]["authorization"] == "Bearer gemini_api_key"
    body = sent["body"]
    assert (body["model"], body["max_tokens"], body["reasoning_effort"]) == (
        "gemini-3.5-flash-lite", 2700, "low",
    )  # fmt: skip
    assert body["response_format"] == {"type": "json_object"}
    assert (reply.data, reply.in_tokens, reply.out_tokens) == ({"word": "go"}, 1000, 800)
    # 1,000 tokens in at $0.30 and 800 out at $2.50 per million.
    assert cost(reply.model, reply.in_tokens, reply.out_tokens) == 0.0023

    def busy(
        url: str, headers: dict[str, str], body: dict[str, Any], timeout: float
    ) -> tuple[int, Any]:
        return 503, None

    gemini.transport = busy
    with pytest.raises(ProviderError) as raised:
        gemini.complete("Answer.", "go", api_schema(Word), 900)
    assert raised.value.retry
