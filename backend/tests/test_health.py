import pytest
from fastapi.testclient import TestClient

from smart_dictionary.api.app import create_app
from smart_dictionary.db import engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_healthz_never_touches_the_database(client, monkeypatch):
    async def must_not_be_called() -> None:
        raise AssertionError("/healthz must not query the database")

    monkeypatch.setattr(engine, "ping", must_not_be_called)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_is_503_when_the_database_is_down(client, monkeypatch):
    async def down() -> None:
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(engine, "ping", down)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "down"}


def test_readyz_against_the_real_database(live_database, anyio_backend_options):
    engine.get_engine.cache_clear()
    try:
        with TestClient(create_app(), backend_options=anyio_backend_options) as live_client:
            response = live_client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "up"}
    finally:
        engine.get_engine.cache_clear()


def test_readyz_is_200_when_the_database_answers(client, monkeypatch):
    async def up() -> None:
        return None

    monkeypatch.setattr(engine, "ping", up)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}
