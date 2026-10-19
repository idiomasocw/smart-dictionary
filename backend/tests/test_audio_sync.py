"""The audio job and the cards' audio links, with a stand-in for Kokoro (no model needed)."""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from factory_docs import go_doc
from smart_dictionary.api.app import create_app
from smart_dictionary.audio import sync as audio_sync
from smart_dictionary.audio.sync import Outbox, sync, wanted
from smart_dictionary.content.publish import publish
from smart_dictionary.core.entrydoc import EntryDoc
from smart_dictionary.core.settings import get_settings
from smart_dictionary.db.sync import connect


class FakeVoice:
    engine = "fake@1"

    def phonemize(self, text: str, variety: str) -> str:
        return text

    def say(self, phonemes: str, voice: str) -> Any:
        return phonemes


def upload_everything(outbox: Path) -> int:
    """What deploy/audio-upload.sh does: send each finished file, leave a receipt."""
    files = [path for path in outbox.rglob("*.m4a") if path.is_file()]
    for path in files:
        path.with_name(path.name + ".done").touch()
        path.unlink()
    return len(files)


def published(conn: Any, doc: dict[str, Any]) -> None:
    publish(conn, EntryDoc.model_validate(doc), produced_by={"source": "test"})
    conn.commit()


def test_what_an_entry_needs():
    doc = go_doc()
    items, problems = wanted(doc, "fake@1")
    assert problems == []
    assert sorted((item.owner_key, item.variety) for item in items) == [
        ("en:go:v", "en-GB"),
        ("en:go:v", "en-US"),
        ("en:go:v#1.ex1", "en-US"),
        ("en:go:v#1.ex2", "en-US"),
    ]
    # A sense-specific pronunciation belongs to its sense; retired senses get nothing.
    doc["pronunciations"] = [{"variety": "en-US", "ipa": "goʊ", "sense_keys": ["en:go:v#1"]}]
    assert next(item.owner_key for item in wanted(doc, "fake@1")[0]) == "en:go:v#1"
    doc["senses"][0]["retired"] = True
    assert wanted(doc, "fake@1")[0] == []
    # An IPA the converter doesn't understand is reported, not voiced.
    doc = go_doc()
    doc["pronunciations"] = [{"variety": "en-US", "ipa": "ʘoʊ"}]
    items, problems = wanted(doc, "fake@1")
    assert len(items) == 2 and problems and "ʘ" in problems[0]


def test_the_job_voices_once_confirms_uploads_and_remakes_what_changed(
    migrated_database_url, anyio_backend_options, tmp_path, monkeypatch
):
    monkeypatch.setattr(audio_sync, "encode", lambda samples: (b"audio", 1200))
    outbox = Outbox(tmp_path)
    with connect() as conn:
        published(conn, go_doc())
        first = sync(conn, FakeVoice(), outbox)
        assert first.made == 4 and first.problems == []
        assert sync(conn, FakeVoice(), outbox).made == 0  # nothing twice
        files = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.m4a"))
        assert files[0].startswith("v1/en/go/v/pron.en-gb.bf_emma.")
        assert files[-1].startswith("v1/en/go/v/s1-ex2.en-us.af_heart.")

    # Before the upload, the card has no audio; after it, every part has a link.
    monkeypatch.setenv("AUDIO_BASE_URL", "https://cdn.test/")
    get_settings.cache_clear()
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        card = client.get("/v1/entries/en:go:v").json()
        assert [p["audio_url"] for p in card["pronunciations"]] == [None, None]
        etag_before = client.get("/v1/entries/en:go:v").headers["etag"]

        assert upload_everything(tmp_path) == 4
        with connect() as conn:
            assert sync(conn, FakeVoice(), outbox).confirmed == 4
        card = client.get("/v1/entries/en:go:v").json()
        urls = [p["audio_url"] for p in card["pronunciations"]]
        urls += [example["audio_url"] for example in card["senses"][0]["examples"]]
        assert all(url and url.startswith("https://cdn.test/v1/en/go/v/") for url in urls)
        assert client.get("/v1/entries/en:go:v").headers["etag"] != etag_before
    assert list(tmp_path.rglob("*.done")) == []  # receipts are cleared

    # A changed example is voiced again; the rest stays.
    doc = go_doc()
    doc["senses"][0]["examples"][1]["text"] = "I usually go to work on foot."
    with connect() as conn:
        published(conn, doc)
        assert sync(conn, FakeVoice(), outbox).made == 1
        # A file that never reached the bucket, nor waits in the outbox, is forgotten after
        # a day, so the next run makes it again.
        for path in tmp_path.rglob("*.m4a"):
            path.unlink()
        conn.execute(
            "update audio_assets set created_at = now() - interval '2 days'"
            " where uploaded_at is null"
        )
        conn.commit()
        assert sync(conn, FakeVoice(), outbox, limit=0).lost == 1
        assert sync(conn, FakeVoice(), outbox).made == 1
    get_settings.cache_clear()
