import pytest
from pydantic import ValidationError

from smart_dictionary.core.ids import new_id
from smart_dictionary.core.settings import Settings


def test_ids_are_uuid7_with_non_decreasing_timestamps():
    ids = [new_id() for _ in range(200)]
    assert all(i.version == 7 for i in ids)
    millis = [i.int >> 80 for i in ids]  # the leading 48 bits are the Unix time in ms
    assert millis == sorted(millis)


def test_database_url_must_use_the_psycopg_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    with pytest.raises(ValidationError):
        Settings()  # pyright: ignore[reportCallIssue]
