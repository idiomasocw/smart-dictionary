"""Application settings, read from the environment (and backend/.env in development).

Required values have no defaults: a missing one stops the process at startup
instead of surfacing later as a runtime error against a placeholder.
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DATABASE_SCHEME = "postgresql+psycopg://"

L1Variety = Literal["es-419", "pt-BR", "ar"]


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

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, value: str) -> str:
        # One driver serves both the async API and the sync migrations.
        if not value.startswith(DATABASE_SCHEME):
            raise ValueError(f"DATABASE_URL must start with {DATABASE_SCHEME}")
        return value

    @model_validator(mode="after")
    def _production_needs_the_proxy_secret(self) -> Self:
        if self.environment == "prod" and self.bbb_proxy_secret is None:
            raise ValueError("BBB_PROXY_SECRET is required in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment
