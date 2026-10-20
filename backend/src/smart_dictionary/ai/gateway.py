"""The AI gateway (design §7, ADR-009): the one way request-time AI reaches a model.

- **Providers in order** (AI_PROVIDER_ORDER): DeepSeek, then Claude Haiku, Gemini and Groq by
  default, each skipped without a key. The owner's order from the admin API wins.
- **Retries:** one more try after a 429 or 5xx. A timeout, a refusal or an answer that fails
  validation moves on to the next provider.
- **A circuit breaker per provider:** 5 failures in a minute keep it out for 2 minutes.
- **Validation:** every answer must match the task's Pydantic model before it's used.
- **The budget [OP-2]:** every call is logged in llm_calls with its cost. At the monthly cap
  the paid providers are skipped, and from 80% each call logs a warning for the owner. The
  admin API can change the cap, within bounds (core.tunables).
- **The cache:** a task's caller can keep an answer in ai_cache by its input.
"""

import hashlib
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from smart_dictionary.ai.providers import Provider, ProviderError, Reply, api_schema, keyed
from smart_dictionary.core import tunables
from smart_dictionary.core.settings import get_settings

log = logging.getLogger(__name__)

# USD per million tokens, (input, output). The cap is only as good as these numbers: check
# them against the providers' price pages when a model changes.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),  # prompts up to 100K tokens; ours are ~1K
    "deepseek-chat": (0.28, 0.42),
    "gemini-3.5-flash-lite": (0.30, 2.50),  # output includes thinking; Google, 2026-10-16
    "openai/gpt-oss-120b": (0.0, 0.0),  # Groq's free tier
}
ALERT_SHARE = 0.8
BREAKER_FAILURES = 5
BREAKER_WINDOW = 60.0  # seconds
BREAKER_OPEN = 120.0


@dataclass(frozen=True)
class Task[T: BaseModel]:
    name: str  # as logged in llm_calls.task
    prompt_version: str  # the prompt file's name, logged with every call
    system: str
    output: type[T]
    max_tokens: int


@dataclass(frozen=True)
class Answer[T: BaseModel]:
    value: T
    provider: str
    model: str
    cached: bool = False


class Unavailable(Exception):
    """No provider answered: none is configured, the budget allows none, or all failed."""


class Breaker:
    """Keeps a failing provider out for a while, so a dead one doesn't cost every call a
    timeout. One per process, shared by the worker threads."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._open_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_open(self, name: str) -> bool:
        with self._lock:
            return self._clock() < self._open_until.get(name, 0.0)

    def failed(self, name: str) -> None:
        now = self._clock()
        with self._lock:
            failures = self._failures.setdefault(name, deque())
            failures.append(now)
            while failures and failures[0] < now - BREAKER_WINDOW:
                failures.popleft()
            if len(failures) >= BREAKER_FAILURES:
                self._open_until[name] = now + BREAKER_OPEN
                failures.clear()
                log.warning("AI provider %s is out for %.0f s after repeated failures",
                            name, BREAKER_OPEN)  # fmt: skip

    def succeeded(self, name: str) -> None:
        with self._lock:
            self._failures.pop(name, None)


class Gateway:
    def __init__(
        self,
        providers: list[Provider],
        budget_usd: float,
        breaker: Breaker | None = None,
        spares: list[Provider] | None = None,
    ) -> None:
        """`providers` in the environment's order; `spares` are the other providers with a
        key, which only the owner's order can bring in."""
        self.providers = providers
        self.budget_usd = budget_usd
        self.breaker = breaker or Breaker()
        self.keyed = {provider.name: provider for provider in [*(spares or []), *providers]}

    def chain(self, overrides: Mapping[str, Any]) -> list[Provider]:
        """The providers to try, first tried first: the owner's order when one is set, else
        the environment's. An empty chain means request-time AI is off."""
        order = overrides.get("ai_provider_order")
        if order is None:
            return self.providers
        return [self.keyed[name] for name in order if name in self.keyed]

    def run[T: BaseModel](
        self,
        conn: psycopg.Connection,
        task: Task[T],
        user_text: str,
        *,
        cache_key: str | None = None,
        tenant: str | None = None,
        user_id: uuid.UUID | None = None,
        ref_key: str | None = None,
    ) -> Answer[T]:
        """The task's answer, from the cache or the first provider that gives a valid one.
        Calls are logged on `conn`; the caller commits."""
        if cache_key is not None:
            cached = self._cached(conn, task, cache_key)
            if cached is not None:
                return cached
        overrides = tunables.overrides(conn)
        budget = overrides.get("ai_monthly_budget_usd", self.budget_usd)
        over_budget = self._spent(conn, budget) >= budget
        schema = api_schema(task.output)
        failed_before = False
        for provider in self.chain(overrides):
            if (provider.paid and over_budget) or self.breaker.is_open(provider.name):
                continue
            for attempt in range(2):
                started = time.perf_counter()
                reply: Reply | None = None
                try:
                    reply = provider.complete(task.system, user_text, schema, task.max_tokens)
                    value = task.output.model_validate(reply.data)
                except ProviderError as error:
                    self._log(conn, task, provider, reply, error.failure, started, tenant,
                              user_id, ref_key)  # fmt: skip
                    if error.retry and attempt == 0:
                        continue
                except ValidationError:
                    self._log(conn, task, provider, reply, "error", started, tenant, user_id,
                              ref_key)  # fmt: skip
                else:
                    outcome = "fallback" if failed_before else "ok"
                    self._log(conn, task, provider, reply, outcome, started, tenant, user_id,
                              ref_key)  # fmt: skip
                    self.breaker.succeeded(provider.name)
                    return Answer(value, provider.name, reply.model)
                self.breaker.failed(provider.name)
                failed_before = True
                break
        raise Unavailable(task.name)

    def remember(
        self, conn: psycopg.Connection, task: Task[Any], cache_key: str, answer: Answer[Any]
    ) -> None:
        conn.execute(
            "insert into ai_cache (task, input_hash, output, provider, model)"
            " values (%s, %s, %s, %s, %s) on conflict (task, input_hash) do update"
            " set output = excluded.output, provider = excluded.provider,"
            " model = excluded.model, created_at = now()",
            (task.name, _hash(task, cache_key), Jsonb(answer.value.model_dump(mode="json")),
             answer.provider, answer.model),
        )  # fmt: skip

    def forget(self, conn: psycopg.Connection, task: Task[Any], cache_key: str) -> None:
        conn.execute(
            "delete from ai_cache where task = %s and input_hash = %s",
            (task.name, _hash(task, cache_key)),
        )

    def _cached[T: BaseModel](
        self, conn: psycopg.Connection, task: Task[T], cache_key: str
    ) -> Answer[T] | None:
        row = conn.execute(
            "select output, provider, model from ai_cache where task = %s and input_hash = %s"
            " and (expires_at is null or expires_at > now())",
            (task.name, _hash(task, cache_key)),
        ).fetchone()
        if row is None:
            return None
        try:
            return Answer(task.output.model_validate(row[0]), row[1], row[2], cached=True)
        except ValidationError:
            return None  # written by an older version of the task's model

    def _spent(self, conn: psycopg.Connection, budget: float) -> float:
        row = conn.execute(
            "select coalesce(sum(cost_usd), 0) from llm_calls"
            " where created_at >= date_trunc('month', now())"
        ).fetchone()
        spent = float(row[0]) if row else 0.0
        if budget and spent >= ALERT_SHARE * budget:
            log.warning("AI spend this month is $%.2f of the $%.2f cap", spent, budget)
        return spent

    def _log(
        self,
        conn: psycopg.Connection,
        task: Task[Any],
        provider: Provider,
        reply: Reply | None,
        outcome: str,
        started: float,
        tenant: str | None,
        user_id: uuid.UUID | None,
        ref_key: str | None,
    ) -> None:
        model = reply.model if reply else provider.model
        in_tokens = reply.in_tokens if reply else None
        out_tokens = reply.out_tokens if reply else None
        conn.execute(
            "insert into llm_calls (tenant_id, task, provider, model, prompt_version, in_tokens,"
            " out_tokens, cost_usd, latency_ms, outcome, user_id, ref_key)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (tenant, task.name, provider.name, model, task.prompt_version, in_tokens,
             out_tokens, cost(model, in_tokens, out_tokens),
             round((time.perf_counter() - started) * 1000), outcome, user_id, ref_key),
        )  # fmt: skip


def cost(model: str, in_tokens: int | None, out_tokens: int | None) -> float:
    """USD for one call. An unknown model is priced as the most expensive known one, so a
    missing price can only make the cap stricter."""
    price_in, price_out = PRICES.get(model) or max(PRICES.values(), key=sum)
    return round(((in_tokens or 0) * price_in + (out_tokens or 0) * price_out) / 1_000_000, 6)


def _hash(task: Task[Any], cache_key: str) -> bytes:
    # The prompt version is part of the key, so a new prompt never serves old answers.
    return hashlib.sha256(f"{task.prompt_version}\n{cache_key}".encode()).digest()


@lru_cache
def default_gateway() -> Gateway:
    """The process's gateway, so every request shares its circuit breakers."""
    settings = get_settings()
    available = keyed(settings)
    ordered = [available[name] for name in settings.ai_providers if name in available]
    spares = [available[name] for name in available if name not in settings.ai_providers]
    return Gateway(ordered, settings.ai_monthly_budget_usd, spares=spares)


def get_gateway() -> Gateway:
    """FastAPI dependency; tests override it with a gateway of fake providers."""
    return default_gateway()
