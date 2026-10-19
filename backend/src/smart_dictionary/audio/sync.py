"""The audio job (design §10.4): give every published entry its audio, and nothing twice.

Each entry should have:
- a headword pronunciation for each variety in its document (per sense for a homograph that
  sounds different per sense), spoken from its IPA;
- every example of its active senses, in the US voice, spoken from the text.

A file is made when its audio_assets row is missing or names another file. That happens
when the IPA, the text, the voice or the engine changed, because a file's name holds the hash
of all four. Provisional entries wait: the routine rewrites them within the hour.

Files go to an outbox folder, never straight to S3, so nothing here needs AWS credentials.
The host's uploader (deploy/audio-upload.sh) sends them and leaves a `.done` receipt per
file; the next sync turns each receipt into the row's `uploaded_at`, and cards link only
uploaded files.
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import psycopg

from smart_dictionary.audio import layout
from smart_dictionary.audio.engine import EXAMPLE_VARIETY, VOICES, encode
from smart_dictionary.audio.phonemes import UnsupportedPhonemes, from_ipa

log = logging.getLogger(__name__)

CHUNK = 200  # entries read per query
LOST_AFTER = "1 day"  # a file neither uploaded nor waiting by then is made again
RECEIPT = ".done"


class Voice(Protocol):
    """What the job needs from an engine (audio.engine.Speaker); tests use a fake."""

    engine: str

    def phonemize(self, text: str, variety: str) -> str: ...

    def say(self, phonemes: str, voice: str) -> Any: ...


@dataclass(frozen=True)
class Item:
    owner_key: str  # the entry or a sense (pronunciations), or an example
    variety: str
    voice: str
    s3_key: str
    phonemes: str | None = None  # a headword's, from its IPA
    text: str | None = None  # an example's
    ipa: str | None = None


@dataclass
class Report:
    made: int = 0
    audio_ms: int = 0  # the length of the audio made
    confirmed: int = 0
    lost: int = 0
    problems: list[str] = field(default_factory=list)


class Outbox:
    """The folder shared with the host's uploader."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, key: str, data: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")  # the uploader skips these
        partial.write_bytes(data)
        partial.replace(path)

    def receipts(self) -> list[Path]:
        return sorted(self.root.rglob(f"*{layout.SUFFIX}{RECEIPT}")) if self.root.exists() else []

    def key_of(self, receipt: Path) -> str:
        return receipt.relative_to(self.root).as_posix().removesuffix(RECEIPT)

    def waiting(self, key: str) -> bool:
        return (self.root / key).exists()


def wanted(doc: dict[str, Any], engine: str) -> tuple[list[Item], list[str]]:
    """The audio an entry's document calls for, and what stops some of it (reported)."""
    items: list[Item] = []
    problems: list[str] = []
    active = {sense["key"] for sense in doc.get("senses", []) if not sense.get("retired")}
    for pronunciation in doc.get("pronunciations", []):
        variety = pronunciation["variety"]
        voice = VOICES.get(variety)
        if voice is None:
            continue
        try:
            phonemes = from_ipa(pronunciation["ipa"], variety)
        except UnsupportedPhonemes as error:
            problems.append(f"{doc['key']} {variety}: {error}")
            continue
        owners = [key for key in pronunciation.get("sense_keys") or [] if key in active]
        for owner in owners or ([] if pronunciation.get("sense_keys") else [doc["key"]]):
            key = layout.s3_key(owner, variety, voice, engine, phonemes)
            items.append(Item(owner, variety, voice, key, phonemes=phonemes,
                              ipa=pronunciation["ipa"]))  # fmt: skip
    voice = VOICES[EXAMPLE_VARIETY]
    for sense in doc.get("senses", []):
        if sense.get("retired"):
            continue
        for example in sense.get("examples", []):
            key = layout.s3_key(example["key"], EXAMPLE_VARIETY, voice, engine, example["text"])
            items.append(Item(example["key"], EXAMPLE_VARIETY, voice, key, text=example["text"]))
    return items, problems


def sync(
    conn: psycopg.Connection,
    speaker: Voice,
    outbox: Outbox,
    *,
    limit: int | None = None,
    shard: tuple[int, int] = (0, 1),
    progress: Callable[[Report], None] | None = None,
) -> Report:
    """Confirm uploads, then voice what's missing, committing per entry. `shard` (i, n)
    takes every n-th entry, so n processes can share a backfill."""
    report = Report()
    report.confirmed, report.lost = confirm(conn, outbox)
    for chunk in _published(conn, shard):
        current = _current(conn, [key for key, _ in chunk])
        for _key, doc in chunk:
            items, problems = wanted(doc, speaker.engine)
            report.problems += problems
            for item in items:
                if current.get((item.owner_key, item.variety, item.voice)) == item.s3_key:
                    continue
                if limit is not None and report.made >= limit:  # limit 0: confirm only
                    conn.commit()
                    return report
                try:
                    report.audio_ms += _make(conn, speaker, outbox, item)
                except Exception as error:  # one bad item never stops the run
                    report.problems.append(f"{item.owner_key} {item.variety}: {error}")
                    log.exception("audio for %s failed", item.owner_key)
                    continue
                report.made += 1
            conn.commit()
        if progress is not None:
            progress(report)
    return report


def confirm(conn: psycopg.Connection, outbox: Outbox) -> tuple[int, int]:
    """Turn the uploader's receipts into uploaded_at; forget files that went missing."""
    receipts = outbox.receipts()
    confirmed = 0
    if receipts:
        confirmed = conn.execute(
            "update audio_assets set uploaded_at = now()"
            " where s3_key = any(%s) and uploaded_at is null",
            ([outbox.key_of(receipt) for receipt in receipts],),
        ).rowcount
        conn.commit()
        for receipt in receipts:
            receipt.unlink(missing_ok=True)
    stale = conn.execute(
        "select owner_key, variety, voice, s3_key from audio_assets"
        " where uploaded_at is null and created_at < now() - %s::interval",
        (LOST_AFTER,),
    ).fetchall()
    lost = [row[:3] for row in stale if not outbox.waiting(row[3])]
    for owner_key, variety, voice in lost:
        conn.execute(
            "delete from audio_assets where owner_key = %s and variety = %s and voice = %s",
            (owner_key, variety, voice),
        )
    conn.commit()
    return confirmed, len(lost)


def _make(conn: psycopg.Connection, speaker: Voice, outbox: Outbox, item: Item) -> int:
    phonemes = item.phonemes or speaker.phonemize(item.text or "", item.variety)
    data, duration_ms = encode(speaker.say(phonemes, item.voice))
    outbox.put(item.s3_key, data)
    conn.execute(
        "insert into audio_assets (owner_key, variety, voice, ipa, engine, s3_key, duration_ms)"
        " values (%s, %s, %s, %s, %s, %s, %s)"
        " on conflict (owner_key, variety, voice) do update set ipa = excluded.ipa,"
        " engine = excluded.engine, s3_key = excluded.s3_key,"
        " duration_ms = excluded.duration_ms, uploaded_at = null, created_at = now()",
        (item.owner_key, item.variety, item.voice, item.ipa, speaker.engine, item.s3_key,
         duration_ms),
    )  # fmt: skip
    return duration_ms


def _published(
    conn: psycopg.Connection, shard: tuple[int, int]
) -> Iterator[list[tuple[str, dict[str, Any]]]]:
    index, count = shard
    keys = [
        row[0]
        for row in conn.execute(
            "select key from entries where status = 'published' and doc is not null"
            " and abs(hashtext(key)) %% %s = %s order by priority desc, key",
            (count, index),
        )
    ]
    for start in range(0, len(keys), CHUNK):
        rows = conn.execute(
            "select key, doc from entries where key = any(%s)", (keys[start : start + CHUNK],)
        ).fetchall()
        docs = dict(rows)
        yield [(key, docs[key]) for key in keys[start : start + CHUNK] if key in docs]


def _current(conn: psycopg.Connection, entry_keys: list[str]) -> dict[tuple[str, str, str], str]:
    rows = conn.execute(
        "select owner_key, variety, voice, s3_key from audio_assets"
        " where split_part(owner_key, '#', 1) = any(%s)",
        (entry_keys,),
    ).fetchall()
    return {(owner, variety, voice): key for owner, variety, voice, key in rows}
