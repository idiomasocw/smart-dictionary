"""Application settings, read from the environment (and backend/.env in development).

Required values have no defaults: a missing one stops the process at startup
instead of surfacing later as a runtime error against a placeholder.
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from smart_dictionary.paths import DATA_DIR

DATABASE_SCHEME = "postgresql+psycopg://"

L1Variety = Literal["es-419", "pt-BR", "ar"]
# The request-time AI providers, by the names llm_calls logs (ai.providers).
AI_PROVIDERS = ("deepseek", "anthropic", "gemini", "groq")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    database_url: str = Field(description="SQLAlchemy URL using the psycopg 3 driver")
    # Shared with the nginx identity proxy on the BBB server (design §9.4).
    bbb_proxy_secret: SecretStr | None = None
    default_tenant: str = "ocw"
    default_variety: L1Variety = "es-419"
    # A submitted entry publishes when it has no errors and scores at least this (design
    # §6.4); calibrated on the golden set in the pilot.
    factory_publish_threshold: float = Field(default=0.7, ge=0, le=1)
    # Request-time AI (design §7). A provider without a key is skipped, and with no key at
    # all the AI features answer "unavailable"; lookups never depend on them.
    anthropic_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    # The order the gateway tries them in, comma-separated; a provider left out isn't used.
    # Speed first: a provisional entry is a stand-in the routine rewrites, and DeepSeek
    # answers fastest in the owner's experience (owner's choice, 2026-10-20).
    ai_provider_order: str = ",".join(AI_PROVIDERS)
    # The monthly cap on paid providers [OP-2]: an alert at 80%, and at 100% only the free
    # provider answers until the month rolls over.
    ai_monthly_budget_usd: float = Field(default=5.0, ge=0)
    # Missed searches a student can send to AI in 24 hours (design §7.1); a Spanish search
    # counts once. 30 since DeepSeek answers first (owner's choice, 2026-10-20).
    ai_searches_per_day: int = Field(default=30, ge=0)
    # Words each student can request in 24 hours, for the owner to review (design §6.7).
    word_requests_per_day: int = Field(default=10, ge=0)
    # A requested or related word is approved at once when a curated list names it and its
    # part of speech is clear (design §6.7); off, every one waits for the owner. On by
    # default (owner's choice, 2026-10-20).
    auto_approve_listed: bool = True
    # Audio (design §10.4). Cards link uploaded files under audio_base_url (the CDN, from
    # SSM). The audio job reads Kokoro from audio_model_dir, writes files to audio_outbox for
    # the host's uploader, and can be held to a few CPU threads.
    audio_base_url: str | None = None
    audio_model_dir: Path = DATA_DIR / "cache" / "kokoro"
    audio_outbox: Path = DATA_DIR / "cache" / "audio-outbox"
    audio_threads: int | None = Field(default=None, ge=1)

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, value: str) -> str:
        # One driver serves both the async API and the sync migrations.
        if not value.startswith(DATABASE_SCHEME):
            raise ValueError(f"DATABASE_URL must start with {DATABASE_SCHEME}")
        return value

    @field_validator("ai_provider_order")
    @classmethod
    def _known_providers(cls, value: str) -> str:
        """The known names, in order, once each. Unlike other settings, a mistake here doesn't
        stop the API: a failed deploy rolls back to the previous image with the same settings,
        so a typo would take lookups down with it. It drops that name and logs a warning."""
        names = [name.strip().lower() for name in value.split(",") if name.strip()]
        kept = list(dict.fromkeys(name for name in names if name in AI_PROVIDERS))
        if kept != names:
            logging.getLogger(__name__).warning(
                "AI_PROVIDER_ORDER %r: using %r; known providers: %s",
                value, ",".join(kept), ", ".join(AI_PROVIDERS),
            )  # fmt: skip
        return ",".join(kept)

    @property
    def ai_providers(self) -> list[str]:
        return self.ai_provider_order.split(",") if self.ai_provider_order else []

    @model_validator(mode="after")
    def _production_needs_the_proxy_secret(self) -> Self:
        if self.environment == "prod" and self.bbb_proxy_secret is None:
            raise ValueError("BBB_PROXY_SECRET is required in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment
