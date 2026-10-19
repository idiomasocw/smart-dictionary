"""Publishing an entry document: the entry row, a revision and the projections, in one transaction.

This is the core of the publish step (design §6.4). In Phase 2 the factory's
validators and judge run before it; for now it serves the development fixtures.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from smart_dictionary.content import projections
from smart_dictionary.core.entrydoc import EntryDoc
from smart_dictionary.core.ids import new_id
from smart_dictionary.core.keys import parse_key
from smart_dictionary.core.text import norm


def publish(
    conn: psycopg.Connection,
    doc: EntryDoc,
    *,
    produced_by: dict[str, Any],
    qa: dict[str, Any] | None = None,
    priority: int = 0,
    sources: list[str] | None = None,
    quality: float | None = None,
) -> uuid.UUID:
    """Upsert the entry as published, record a revision and rebuild its projections."""
    payload = doc.model_dump(mode="json")
    homograph = parse_key(doc.key).homograph
    zipf_row = conn.execute(
        "select max(zipf) from ref_freq where language = 'en' and word = %s", (norm(doc.lemma),)
    ).fetchone()
    row = conn.execute(
        "insert into entries (id, key, language, lemma, lemma_norm, pos, kind, homograph, cefr,"
        " freq_zipf, priority, status, doc, sources, quality, published_at)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'published', %s, %s, %s, now())"
        " on conflict (key) do update set lemma = excluded.lemma, lemma_norm = excluded.lemma_norm,"
        " pos = excluded.pos, kind = excluded.kind, cefr = excluded.cefr,"
        " freq_zipf = coalesce(excluded.freq_zipf, entries.freq_zipf), status = 'published',"
        " doc = excluded.doc, quality = excluded.quality,"
        " content_version = entries.content_version + 1, attempts = 0, lease_batch_id = null,"
        " lease_expires_at = null, leased_from = null, published_at = now()"
        " returning id",
        (
            new_id(),
            doc.key,
            doc.language,
            doc.lemma,
            norm(doc.lemma),
            doc.pos,
            doc.kind,
            homograph,
            doc.cefr,
            zipf_row[0] if zipf_row else None,
            priority,
            Jsonb(payload),
            sources or [],
            quality,
        ),
    ).fetchone()
    assert row is not None
    entry_id: uuid.UUID = row[0]
    revision_id = new_id()
    conn.execute(
        "insert into entry_revisions (id, entry_id, doc, produced_by, qa, outcome)"
        " values (%s, %s, %s, %s, %s, 'published')",
        (revision_id, entry_id, Jsonb(payload), Jsonb(produced_by), Jsonb(qa or {})),
    )
    conn.execute(
        "update entries set current_revision_id = %s where id = %s", (revision_id, entry_id)
    )
    projections.rebuild(conn, entry_id, doc)
    return entry_id


def load_fixtures(conn: psycopg.Connection, directory: Path) -> list[str]:
    """Publish every fixture document in the directory; returns their keys."""
    keys: list[str] = []
    for path in sorted(directory.glob("*.json")):
        doc = EntryDoc.model_validate(json.loads(path.read_text(encoding="utf-8")))
        publish(
            conn, doc, produced_by={"source": "fixture", "file": path.name}, sources=["fixture"]
        )
        keys.append(doc.key)
    return keys
