import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from conftest import clear_caches
from smart_dictionary.api.app import create_app
from smart_dictionary.core.settings import Settings


def test_the_proxy_secret_is_enforced(migrated_database_url, anyio_backend_options, monkeypatch):
    monkeypatch.setenv("BBB_PROXY_SECRET", "s3cret")
    clear_caches()
    try:
        with TestClient(create_app(), backend_options=anyio_backend_options) as client:
            missing = client.get("/v1/entries/en:actually:adv")
            assert missing.status_code == 401
            assert missing.json()["code"] == "unauthorized"
            wrong = client.get("/v1/entries/en:actually:adv", headers={"X-Dict-Secret": "nope"})
            assert wrong.status_code == 401
            # The right secret gets through to the handler: the empty database has no entry.
            right = client.get("/v1/entries/en:actually:adv", headers={"X-Dict-Secret": "s3cret"})
            assert right.status_code == 404
            assert client.get("/healthz").status_code == 200
    finally:
        monkeypatch.delenv("BBB_PROXY_SECRET")
        clear_caches()


def test_production_refuses_to_start_without_the_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.delenv("BBB_PROXY_SECRET", raising=False)
    with pytest.raises(ValidationError, match="BBB_PROXY_SECRET"):
        Settings()  # pyright: ignore[reportCallIssue]
