"""GET /v1/entries/{key}: one entry card, projected to the student's language [EC-*, LK-9]."""

import hashlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text

from smart_dictionary.api.auth import current_principal
from smart_dictionary.api.deps import Connection
from smart_dictionary.api.errors import ApiError
from smart_dictionary.content import related
from smart_dictionary.content.cards import AudioLinks, EntryCard, project_card
from smart_dictionary.content.visibility import live
from smart_dictionary.core.keys import parse_key
from smart_dictionary.core.settings import L1Variety, Settings, get_settings
from smart_dictionary.search.service import escape_like

router = APIRouter(prefix="/v1", tags=["entries"], dependencies=[Depends(current_principal)])

ENTRY = text(
    f"select doc, content_version, freq_zipf from entries e where e.key = :key and {live()}"
)
# Only uploaded files: a link must never point at a file the CDN doesn't have yet.
AUDIO = text(
    "select owner_key, variety, s3_key from audio_assets"
    " where split_part(owner_key, '#', 1) = :key and uploaded_at is not null"
)
HIDDEN = text(
    "select target_key from content_flags"
    " where tenant_id = :tenant and action = 'hide' and undone_at is null"
    " and target_key like :prefix escape '\\'"
)


@router.get("/entries/{key}", response_model=EntryCard)
async def get_entry(
    key: str,
    request: Request,
    response: Response,
    conn: Connection,
    settings: Annotated[Settings, Depends(get_settings)],
    variety: L1Variety | None = None,
) -> Any:
    try:
        parsed = parse_key(key)
    except ValueError:
        raise ApiError(
            400, "invalid_key", "Not a valid entry key", "Example: en:actually:adv"
        ) from None
    if parsed.level != "entry":
        raise ApiError(400, "invalid_key", "Use an entry key", f"Try {parsed.entry_key}")
    row = (await conn.execute(ENTRY, {"key": key})).first()
    if row is None:
        raise ApiError(404, "not_found", "No such entry")

    chosen = variety or settings.default_variety
    params = {"tenant": settings.default_tenant, "prefix": escape_like(key) + "%"}
    hidden = {target for (target,) in await conn.execute(HIDDEN, params)}
    audio: AudioLinks = {}
    if settings.audio_base_url:
        base = settings.audio_base_url.rstrip("/")
        for owner, spoken, s3_key in await conn.execute(AUDIO, {"key": key}):
            audio.setdefault(owner, {})[spoken] = f"{base}/{s3_key}"
    opens = await related.opens(conn, row.doc)
    etag = _etag(row.content_version, hidden, chosen, audio, opens)
    headers = {"ETag": etag, "Cache-Control": "private, max-age=300", "Vary": "Accept-Encoding"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return project_card(row.doc, variety=chosen, hidden=hidden, zipf=row.freq_zipf,
                        opens=opens, audio=audio)  # fmt: skip


def _etag(
    content_version: int,
    hidden: set[str],
    variety: str,
    audio: AudioLinks,
    opens: dict[str, str],
) -> str:
    """Changes with the content, the hidden parts, the variety, the uploaded audio and the
    related entries a student can open: the last two change after publishing without
    changing the content version."""
    files = sorted(url for links in audio.values() for url in links.values())
    links = sorted(f"{key}>{opened}" for key, opened in opens.items())
    parts = [*sorted(hidden), "", *files, "", *links]
    state = hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]
    return f'"{content_version}.{state}.{variety}"'
