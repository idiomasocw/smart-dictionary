"""The AI gateway (design §7): fallbacks, retries, the breaker, the budget, logs and the cache."""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, Field

from smart_dictionary.ai.gateway import Breaker, Gateway, Task, Unavailable, cost
from smart_dictionary.ai.providers import Claude, ProviderError, Reply, api_schema
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
