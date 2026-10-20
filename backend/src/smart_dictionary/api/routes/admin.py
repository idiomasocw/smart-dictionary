"""The admin API (design §5.5) [OP-1]: the owner's console in the LMS reads the dictionary's
state and changes a few settings.

- **Who:** a bearer token with the `admin` scope, required by the router itself, so no route
  can be added without it. The BBB proxy secret and factory tokens don't open it.
- **What it never does:** name a student, show a secret, create a token, or change anything
  but the settings in core.tunables.
- **Every change** is audited with the token's name and `X-Admin-Actor`, the LMS's person ID
  of whoever was signed in. The LMS vouches for that person; the token is what's checked.
- **Responses** aren't cached anywhere, and each request's statements stop after 15 seconds.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from smart_dictionary.admin import browse, overview
from smart_dictionary.admin import settings as admin_settings
from smart_dictionary.api.auth import AdminClient, admin_client
from smart_dictionary.api.deps import SyncConnection
from smart_dictionary.api.errors import ApiError
from smart_dictionary.core import tunables
from smart_dictionary.core.settings import AI_PROVIDERS, Settings, get_settings
from smart_dictionary.core.text import norm


def _guard(conn: SyncConnection, response: Response) -> None:
    conn.execute("set local statement_timeout = '15s'")
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/v1/admin", tags=["admin"], dependencies=[Depends(admin_client), Depends(_guard)]
)

EntryStatus = Literal["published", "provisional", "generating", "validating", "queued",
                      "needs_repair", "quarantined", "candidate", "retired"]  # fmt: skip
MissStatus = Literal["pending", "provisional", "published", "queued", "invalid"]
# The LMS's person ID (a UUID): no names or emails, which the dictionary never stores.
Actor = Annotated[
    str | None,
    Header(
        alias="X-Admin-Actor",
        pattern=r"^[A-Za-z0-9._:-]{1,64}$",
        description="The LMS person ID of whoever made the change, for the audit",
    ),
]
Page = Annotated[int, Query(ge=1, le=100_000)]
PerPage = Annotated[int, Query(ge=1, le=200)]
Prefix = Annotated[str | None, Query(max_length=80, description="The start of the word")]


# Models -----------------------------------------------------------------------------------


class StatusCount(BaseModel):
    status: str
    count: int
    meaning: str


class EntryStats(BaseModel):
    by_status: list[StatusCount]
    total: int
    published_last_24h: int
    waiting: int = Field(description="queued + needs_repair + provisional")
    days_to_clear: float | None = Field(description="waiting at the last 24 hours' pace")


class MissStats(BaseModel):
    by_status: dict[str, int]
    stuck: int = Field(description="AI failed every attempt and no seed covers the word")


class ProviderCalls(BaseModel):
    provider: str
    outcome: str
    calls: int
    avg_latency_ms: int | None
    cost_usd: float


class AiStats(BaseModel):
    month_spend_usd: float
    monthly_budget_usd: float
    searches_per_day: int = Field(description="each student's daily cap")
    searches_last_24h: int
    students_at_cap: int = Field(description="how many, never which")
    provider_order: list[str]
    providers_with_keys: list[str]
    active_providers: list[str] = Field(description="the order, less providers without a key")
    calls_last_24h: list[ProviderCalls]


class AudioStats(BaseModel):
    uploaded: int
    waiting: int


class Run(BaseModel):
    label: str | None
    leased_at: datetime
    submitted_at: datetime | None
    state: str
    words: int
    minutes: int | None
    results: dict[str, int]


class Stats(BaseModel):
    entries: EntryStats
    misses: MissStats
    ai: AiStats
    audio: AudioStats
    recent_runs: list[Run]


class Paged(BaseModel):
    page: int
    per_page: int
    total: int
    pages: int


class EntryRow(BaseModel):
    key: str
    lemma: str
    pos: str
    kind: str
    homograph: int
    cefr: str | None
    status: str
    quality: float | None
    attempts: int
    definition: str | None = Field(description="the first sense's, when written")
    senses: int
    problems: list[str] = Field(description="why the latest submission was sent back")
    updated_at: datetime
    published_at: datetime | None


class EntryPage(Paged):
    items: list[EntryRow]


class LetterCount(BaseModel):
    letter: str = Field(description="a-z, or 'other' for digits and symbols")
    count: int


class Revision(BaseModel):
    created_at: datetime
    outcome: str
    score: float | None
    problems: list[str]
    warnings: list[str]
    produced_by: dict[str, Any]
    qa: dict[str, Any]


class EntryDetail(EntryRow):
    priority: int
    freq_zipf: float | None
    sources: list[str]
    created_at: datetime
    lease_expires_at: datetime | None
    doc: dict[str, Any] | None
    revisions: list[Revision]


class MissRow(BaseModel):
    tenant: str
    language: str
    query: str
    hits: int
    status: str
    entry_key: str | None
    attempts: int
    stuck: bool
    last_error: str | None
    first_seen: datetime
    last_seen: datetime
    last_attempt_at: datetime | None


class MissPage(Paged):
    items: list[MissRow]


class SettingOut(BaseModel):
    name: str
    kind: Literal["providers", "integer", "number"]
    description: str
    minimum: float | None
    maximum: float | None
    value: Any
    default: Any = Field(description="the environment's value, used without an override")
    overridden: bool
    updated_at: datetime | None
    updated_by: str | None


class SettingsOut(BaseModel):
    settings: list[SettingOut]
    known_providers: list[str]
    providers_with_keys: list[str]
    applies_within_seconds: float


class SettingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any


class AuditRow(BaseModel):
    created_at: datetime
    client: str
    actor: str | None
    action: str
    target: str
    before: Any
    after: Any


class AuditPage(Paged):
    items: list[AuditRow]


# Routes -----------------------------------------------------------------------------------


@router.get("/stats")
def stats(conn: SyncConnection, settings: Annotated[Settings, Depends(get_settings)]) -> Stats:
    return Stats.model_validate(overview.stats(conn, settings, tunables.overrides(conn)))


@router.get("/entries")
def entries(
    conn: SyncConnection,
    letter: Annotated[str | None, Query(pattern=r"^([a-z]|other)$")] = None,
    status: EntryStatus | None = None,
    q: Prefix = None,
    sort: Literal["lemma", "updated"] = "lemma",
    page: Page = 1,
    per_page: PerPage = 50,
) -> EntryPage:
    rows, total = browse.entries(conn, letter=letter, status=status, q=_prefix(q), sort=sort,
                                 page=page, per_page=per_page)  # fmt: skip
    return EntryPage(items=[EntryRow.model_validate(row) for row in rows],
                     **_paging(page, per_page, total))  # fmt: skip


@router.get("/entries/letters")
def letters(conn: SyncConnection, status: EntryStatus | None = None) -> list[LetterCount]:
    return [LetterCount.model_validate(row) for row in browse.letters(conn, status)]


@router.get("/entries/{key}")
def entry(conn: SyncConnection, key: str) -> EntryDetail:
    found = browse.entry(conn, key)
    if found is None:
        raise ApiError(404, "not_found", "No such entry")
    return EntryDetail.model_validate(found)


@router.get("/misses")
def misses(
    conn: SyncConnection,
    status: MissStatus | None = None,
    stuck: bool = False,
    q: Prefix = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> MissPage:
    rows, total = browse.misses(conn, status=status, stuck=stuck, q=_prefix(q), page=page,
                                per_page=per_page)  # fmt: skip
    return MissPage(items=[MissRow.model_validate(row) for row in rows],
                    **_paging(page, per_page, total))  # fmt: skip


@router.get("/settings")
def settings_list(
    conn: SyncConnection, settings: Annotated[Settings, Depends(get_settings)]
) -> SettingsOut:
    return SettingsOut(
        settings=[SettingOut.model_validate(row) for row in admin_settings.listing(conn, settings)],
        known_providers=list(AI_PROVIDERS),
        providers_with_keys=overview.providers_with_keys(settings),
        applies_within_seconds=tunables.CACHE_SECONDS,
    )


@router.put("/settings/{name}")
def setting_change(
    conn: SyncConnection,
    client: AdminClient,
    settings: Annotated[Settings, Depends(get_settings)],
    name: str,
    body: SettingIn,
    actor: Actor = None,
) -> SettingOut:
    tunable = _tunable(name)
    try:
        admin_settings.change(conn, client, actor, tunable, body.value)
    except ValueError as error:
        raise ApiError(422, "invalid_value", f"Not a valid {name}", str(error)) from None
    conn.commit()
    tunables.forget()
    return SettingOut.model_validate(admin_settings.describe(conn, settings, tunable))


@router.delete("/settings/{name}")
def setting_reset(
    conn: SyncConnection,
    client: AdminClient,
    settings: Annotated[Settings, Depends(get_settings)],
    name: str,
    actor: Actor = None,
) -> SettingOut:
    tunable = _tunable(name)
    admin_settings.reset(conn, client, actor, tunable)
    conn.commit()
    tunables.forget()
    return SettingOut.model_validate(admin_settings.describe(conn, settings, tunable))


@router.get("/audit")
def audit(conn: SyncConnection, page: Page = 1, per_page: PerPage = 50) -> AuditPage:
    rows, total = browse.audit(conn, page=page, per_page=per_page)
    return AuditPage(items=[AuditRow.model_validate(row) for row in rows],
                     **_paging(page, per_page, total))  # fmt: skip


def _tunable(name: str) -> tunables.Tunable:
    tunable = tunables.TUNABLES.get(name)
    if tunable is None:
        known = ", ".join(tunables.TUNABLES)
        raise ApiError(404, "not_found", "No such setting", f"The settings are {known}")
    return tunable


def _prefix(q: str | None) -> str | None:
    return norm(q) or None if q else None


def _paging(page: int, per_page: int, total: int) -> dict[str, int]:
    return {"page": page, "per_page": per_page, "total": total,
            "pages": max(1, -(-total // per_page))}  # fmt: skip
