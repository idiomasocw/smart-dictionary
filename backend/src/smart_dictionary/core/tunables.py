"""Settings the owner can change while the API runs, from the admin API (design §5.5).

An override is a row in app_settings and wins over the environment's value; removing it goes
back to the environment. Each process keeps what it read for CACHE_SECONDS, so a change
reaches every worker within that time.

The bounds are tighter than the environment's on purpose: an admin token that leaked could
change these, so none of them can be pushed far. Secrets are never among them.
"""

import logging
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from smart_dictionary.core.settings import AI_PROVIDERS, Settings

log = logging.getLogger(__name__)

CACHE_SECONDS = 15.0
SELECT = "select name, value from app_settings"


@dataclass(frozen=True)
class Tunable:
    name: str
    kind: Literal["providers", "integer", "number"]
    description: str
    minimum: float = 0
    maximum: float = 0

    def parse(self, value: Any) -> Any:
        """The value as it's stored and used, or a ValueError that says what's wrong."""
        if self.kind == "providers":
            if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
                raise ValueError("expected a list of provider names")
            names = [name.strip().lower() for name in value]
            unknown = [name for name in names if name not in AI_PROVIDERS]
            if unknown:
                raise ValueError(
                    f"unknown provider {', '.join(unknown)}; known: {', '.join(AI_PROVIDERS)}"
                )
            if len(set(names)) < len(names):
                raise ValueError("a provider is listed twice")
            return names
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("expected a number")
        if self.kind == "integer" and value != int(value):
            raise ValueError("expected a whole number")
        number = int(value) if self.kind == "integer" else float(value)
        if not self.minimum <= number <= self.maximum:
            raise ValueError(f"expected a value from {self.minimum:g} to {self.maximum:g}")
        return number

    def default(self, settings: Settings) -> Any:
        """The environment's value, which applies while there's no override."""
        if self.kind == "providers":
            return settings.ai_providers
        return getattr(settings, self.name)


TUNABLES: dict[str, Tunable] = {
    tunable.name: tunable
    for tunable in (
        Tunable(
            "ai_provider_order",
            "providers",
            "The AI providers that write a missing word, first tried first. One without a key"
            " is skipped; an empty list turns request-time AI off.",
        ),
        Tunable(
            "ai_searches_per_day",
            "integer",
            "Missed searches each student can send to AI in 24 hours.",
            0,
            200,
        ),
        Tunable(
            "ai_monthly_budget_usd",
            "number",
            "The month's cap on paid AI providers, in USD. At the cap only free ones answer.",
            0,
            50,
        ),
        Tunable(
            "word_requests_per_day",
            "integer",
            "Words each student can request in 24 hours, for you to review.",
            0,
            50,
        ),
        Tunable(
            "factory_publish_threshold",
            "number",
            "The quality score a factory entry needs to publish.",
            0.5,
            1,
        ),
    )
}

_lock = threading.Lock()
_cached: tuple[float, Mapping[str, Any]] | None = None


def overrides(conn: psycopg.Connection) -> Mapping[str, Any]:
    """The stored overrides by name, parsed; a setting without one isn't in it."""
    values = _fresh()
    if values is None:
        values = _keep(conn.execute(SELECT).fetchall())
    return values


async def overrides_async(conn: AsyncConnection) -> Mapping[str, Any]:
    values = _fresh()
    if values is None:
        values = _keep((row[0], row[1]) for row in await conn.execute(text(SELECT)))
    return values


def forget() -> None:
    """Drop this process's copy, after a change or between tests."""
    global _cached
    with _lock:
        _cached = None


def _fresh() -> Mapping[str, Any] | None:
    with _lock:
        if _cached is not None and time.monotonic() - _cached[0] < CACHE_SECONDS:
            return _cached[1]
    return None


def _keep(rows: Iterable[tuple[str, Any]]) -> Mapping[str, Any]:
    global _cached
    values: dict[str, Any] = {}
    for name, value in rows:
        tunable = TUNABLES.get(name)
        if tunable is None:
            continue  # stored by a later version, or retired
        try:
            values[name] = tunable.parse(value)
        except ValueError as error:
            log.warning("ignoring the stored %s %r: %s", name, value, error)
    frozen = MappingProxyType(values)
    with _lock:
        _cached = (time.monotonic(), frozen)
    return frozen
