"""The AI providers behind the gateway (design §7.2).

Each provider turns a system prompt, the user's text and a JSON Schema into a dict that
should match the schema; the gateway validates it against the task's model.
- **Claude** goes through the official SDK with structured outputs (`output_config.format`),
  so its answer is JSON that matches the schema.
- **DeepSeek, Gemini and Groq** are called through their OpenAI-compatible endpoints, over
  plain HTTPS. Their JSON mode only promises an object, so the schema goes into the prompt.

The calls are synchronous: the tasks that use them run in worker threads, never in a
lookup's request (NF-1).
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import anthropic
from pydantic import BaseModel

from smart_dictionary.core.settings import Settings

Failure = Literal["error", "timeout", "refused"]

# (url, headers, JSON body, timeout in seconds) -> (HTTP status, parsed JSON body)
Transport = Callable[[str, dict[str, str], dict[str, Any], float], tuple[int, Any]]

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# The newest Flash-Lite: stable, with no shutdown date announced (Google's deprecations page,
# 2026-10). Flash-Lite 3.1 costs a little less but may retire from May 2027.
GEMINI_MODEL = "gemini-3.5-flash-lite"

# Structured outputs don't take these (the SDK's own parse() strips them the same way); the
# gateway checks them when it validates the answer against the task's model.
UNSUPPORTED = {
    "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "default", "title",
}  # fmt: skip


@dataclass(frozen=True)
class Reply:
    data: dict[str, Any]
    model: str
    in_tokens: int | None
    out_tokens: int | None


class ProviderError(Exception):
    """A provider gave no usable answer. `retry` marks the ones worth one more try (429, 5xx)."""

    def __init__(self, failure: Failure, detail: str, *, retry: bool = False) -> None:
        super().__init__(detail)
        self.failure: Failure = failure
        self.retry = retry


class Provider(Protocol):
    name: str
    model: str
    paid: bool

    def complete(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> Reply: ...


def api_schema(output: type[BaseModel]) -> dict[str, Any]:
    """The task model's JSON Schema in the form structured outputs accept: every object
    closed (additionalProperties false) with every property required, so an optional field
    comes back as null rather than missing, and no unsupported constraints."""

    def clean(node: Any, names: bool = False) -> Any:
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if names:  # property or definition names, which may look like keywords ("title")
            return {name: clean(value) for name, value in node.items()}
        cleaned = {
            key: clean(value, names=key in ("properties", "$defs"))
            for key, value in node.items()
            if key not in UNSUPPORTED
        }
        if cleaned.get("type") == "object" and "properties" in cleaned:
            cleaned["additionalProperties"] = False
            cleaned["required"] = list(cleaned["properties"])
        return cleaned

    return clean(output.model_json_schema())


@dataclass
class Claude:
    """Claude through the official SDK. The SDK doesn't retry: the gateway decides that."""

    key: str = field(repr=False)
    model: str = "claude-haiku-5-5"
    # Design §7.2 planned 4 s; a whole entry takes longer, and the student isn't waiting on
    # the call (the lookup answers at once). To be measured once the key is live.
    timeout: float = 8.0
    effort: Literal["low", "medium", "high"] = "low"
    name: str = "anthropic"
    paid: bool = True
    # messages.create, replaceable in tests.
    create: Callable[..., Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.create is None:
            client = anthropic.Anthropic(api_key=self.key, timeout=self.timeout, max_retries=0)
            self.create = client.messages.create

    def complete(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> Reply:
        assert self.create is not None
        try:
            response = self.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
        except anthropic.APITimeoutError as error:
            raise ProviderError("timeout", f"no answer in {self.timeout}s") from error
        except anthropic.RateLimitError as error:
            raise ProviderError("error", "rate limited", retry=True) from error
        except anthropic.APIStatusError as error:
            detail = f"answered {error.status_code}: {error.message}"
            raise ProviderError("error", detail, retry=error.status_code >= 500) from error
        except anthropic.APIConnectionError as error:
            raise ProviderError("error", "unreachable") from error
        if response.stop_reason == "refusal":
            raise ProviderError("refused", "the model declined")
        if response.stop_reason == "max_tokens":
            raise ProviderError("error", "the answer was cut off at max_tokens")
        text = next((block.text for block in response.content if block.type == "text"), None)
        try:
            data = json.loads(text) if text is not None else None
        except ValueError as error:
            raise ProviderError("error", "the answer isn't JSON") from error
        if not isinstance(data, dict):
            raise ProviderError("error", "no JSON object in the answer")
        return Reply(
            data=data,
            model=response.model or self.model,
            in_tokens=response.usage.input_tokens,
            out_tokens=response.usage.output_tokens,
        )


def http_json(
    url: str, headers: dict[str, str], body: dict[str, Any], timeout: float
) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={**headers, "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"null")
        except ValueError:
            return error.code, None
    except TimeoutError as error:
        raise ProviderError("timeout", f"no answer in {timeout}s") from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise ProviderError("timeout", f"no answer in {timeout}s") from error
        raise ProviderError("error", f"unreachable: {error.reason}") from error


@dataclass
class OpenAICompatible:
    name: str
    url: str
    key: str = field(repr=False)
    model: str
    paid: bool
    # As long as Claude gets: a whole entry takes a few seconds, the student isn't waiting on
    # the call, and llm_calls.latency_ms then compares the providers fairly.
    timeout: float = 8.0
    # A reasoning model spends tokens before it answers, and they count toward max_tokens.
    token_factor: float = 1.0
    # Provider-specific request fields, such as Gemini's reasoning_effort.
    extra: dict[str, Any] = field(default_factory=dict)
    transport: Transport = http_json

    def complete(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> Reply:
        instructions = (
            f"{system}\n\nAnswer with one JSON object that matches this JSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        body = {
            "model": self.model,
            "max_tokens": round(max_tokens * self.token_factor),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user},
            ],
            **self.extra,
        }
        headers = {"authorization": f"Bearer {self.key}"}
        status, payload = self.transport(self.url, headers, body, self.timeout)
        if status == 429 or status >= 500:
            raise ProviderError("error", f"{self.name} answered {status}", retry=True)
        if status >= 400 or not isinstance(payload, dict):
            raise ProviderError("error", f"{self.name} answered {status}")
        try:
            data = json.loads(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError("error", "no JSON answer") from error
        if not isinstance(data, dict):
            raise ProviderError("error", "the answer isn't a JSON object")
        usage = payload.get("usage") or {}
        prompt, total = usage.get("prompt_tokens"), usage.get("total_tokens")
        out = usage.get("completion_tokens")
        if isinstance(prompt, int) and isinstance(total, int):
            # Gemini may leave its thinking out of completion_tokens but bills it as output,
            # and counts it in the total: so the output is whatever the prompt isn't.
            out = max(out or 0, total - prompt)
        return Reply(
            data=data,
            model=payload.get("model") or self.model,
            in_tokens=prompt,
            out_tokens=out,
        )


def chain(settings: Settings) -> list[Provider]:
    """The providers with a key, in the order the gateway tries them (AI_PROVIDER_ORDER)."""
    keyed: dict[str, Provider] = {}
    if settings.deepseek_api_key is not None:
        key = settings.deepseek_api_key.get_secret_value()
        keyed["deepseek"] = OpenAICompatible("deepseek", DEEPSEEK_URL, key, "deepseek-chat", True)
    if settings.anthropic_api_key is not None:
        keyed["anthropic"] = Claude(key=settings.anthropic_api_key.get_secret_value())
    if settings.gemini_api_key is not None:
        key = settings.gemini_api_key.get_secret_value()
        # Gemini 3 models always think; low effort keeps an entry fast and cheap, and the
        # factor leaves room in case the thinking counts toward max_tokens.
        gemini = OpenAICompatible("gemini", GEMINI_URL, key, GEMINI_MODEL, True, token_factor=3)
        gemini.extra["reasoning_effort"] = "low"
        keyed["gemini"] = gemini
    if settings.groq_api_key is not None:
        key = settings.groq_api_key.get_secret_value()
        keyed["groq"] = OpenAICompatible(
            "groq", GROQ_URL, key, "openai/gpt-oss-120b", False, token_factor=3
        )
    return [keyed[name] for name in settings.ai_providers if name in keyed]
