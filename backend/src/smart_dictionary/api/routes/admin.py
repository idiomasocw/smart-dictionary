"""The admin API (design §5.5) [OP-1]: the owner's console in the LMS reads the dictionary's
state, decides word requests (design §6.7) and changes a few settings.

- **Who:** a bearer token with the `admin` scope, required by the router itself, so no route
  can be added without it. The BBB proxy secret and factory tokens don't open it.
- **What it never does:** name a student, show a secret, or create a token. It changes only
  the settings in core.tunables, the decision on a word request, and single entries: add a
  word, retire one, requeue a quarantined one.
- **Every change and decision** is audited with the token's name and `X-Admin-Actor`, the
  LMS's person ID of whoever was signed in. The LMS vouches for that person; the token is
  what's checked.
- **Responses** aren't cached anywhere, and each request's statements stop after 15 seconds.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from smart_dictionary.admin import audit, browse, curation, overview
from smart_dictionary.admin import settings as admin_settings
from smart_dictionary.ai import provisional
from smart_dictionary.ai.gateway import Gateway, get_gateway
from smart_dictionary.api.auth import AdminClient, admin_client
from smart_dictionary.api.deps import SyncConnection
from smart_dictionary.api.errors import ApiError
from smart_dictionary.content import word_requests
from smart_dictionary.core import tunables
from smart_dictionary.core.entrydoc import POS, Kind
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
MissStatus = Literal["pending", "provisional", "published", "queued", "requestable", "invalid"]
RequestStatus = Literal["pending", "approved", "rejected"]
Origin = Literal["student", "ai_entry", "related"]
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
    unapproved: int = Field(description="AI entries hidden until you decide their requests")


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


class RequestStats(BaseModel):
    pending: int = Field(description="students' requests waiting for you")
    pending_ai_entries: int = Field(description="AI entries from before reviews, waiting")
    pending_related: int = Field(description="words entries link to, waiting")
    approved: int
    auto_approved: int = Field(description="of the approved, those approved at once (auto:lists)")
    rejected: int


class Stats(BaseModel):
    entries: EntryStats
    requests: RequestStats
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
    approved: bool = Field(description="false: an AI entry waiting for your decision, hidden")
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


class PosKind(BaseModel):
    pos: str
    kind: str


class RequestRow(BaseModel):
    id: uuid.UUID
    lemma: str
    pos: str | None = Field(description="the suggestion, or what was approved")
    kind: str | None
    origin: Origin = Field(
        description="student: asked for; ai_entry: a Beta AI wrote before reviews, now hidden;"
        " related: an entry links to it"
    )
    status: RequestStatus
    entry_key: str | None
    students: int = Field(description="how many asked, never which")
    searches: int
    queries: list[str] = Field(description="up to 5 of the searches that led here")
    zipf: float | None
    lists: list[str] = Field(description="open lists naming the word, with their level")
    seed_decisions: list[str] = Field(description="its seed candidates' curation decisions")
    options: list[PosKind] = Field(description="Wiktionary's parts of speech, likeliest first")
    definition: str | None = Field(description="ai_entry: the hidden Beta's first definition")
    twins: list[str] = Field(
        description="the dictionary's other entries for this headword: for a multiword one,"
        " under any kind or with or without its article. An approved one blocks approval"
    )
    linked_from: list[str] = Field(
        description="related: up to 10 of the entries that link to it, as 'key type'"
    )
    first_asked_at: datetime
    last_asked_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    note: str | None


class RequestPage(Paged):
    items: list[RequestRow]


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lemma: str = Field(min_length=1, max_length=80, description="as requested; casing may change")
    pos: POS
    kind: Kind
    note: str | None = Field(default=None, max_length=500)


class Rejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class NewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lemma: str = Field(min_length=1, max_length=80)
    pos: POS
    kind: Kind
    note: str | None = Field(default=None, max_length=500)


class EntryAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class SettingOut(BaseModel):
    name: str
    kind: Literal["providers", "integer", "number", "boolean"]
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


@router.post("/entries", status_code=201)
def entry_add(
    conn: SyncConnection,
    client: AdminClient,
    settings: Annotated[Settings, Depends(get_settings)],
    body: NewEntry,
    actor: Actor = None,
) -> EntryDetail:
    """Add a word you want: queued, approved and first in the factory's queue."""
    lemma = " ".join(body.lemma.split())
    try:
        added = curation.add(conn, lemma=lemma, pos=body.pos, kind=body.kind,
                             decided_by=audit.decided_by(client, actor), note=body.note,
                             tenant=settings.default_tenant)  # fmt: skip
    except ValueError as error:
        raise ApiError(422, "invalid_value", "Can't add it like this", str(error)) from None
    except word_requests.Conflict as error:
        raise ApiError(409, "conflict", "Can't add it", str(error)) from None
    after = {"lemma": lemma, "pos": body.pos, "kind": body.kind, "note": body.note}
    audit.record(conn, client, actor, "entry.add", added.key, None, after)
    conn.commit()
    return _entry_detail(conn, added.key)


@router.post("/entries/{key}/retire")
def entry_retire(
    conn: SyncConnection, client: AdminClient, key: str, body: EntryAction, actor: Actor = None
) -> EntryDetail:
    """Take a duplicate or an unwanted word out of the dictionary; its key is never reused."""
    try:
        before = curation.retire(conn, key)
    except LookupError:
        raise ApiError(404, "not_found", "No such entry") from None
    except word_requests.Conflict as error:
        raise ApiError(409, "conflict", "Can't retire it", str(error)) from None
    audit.record(conn, client, actor, "entry.retire", key, {"status": before},
                 {"status": "retired", "note": body.note})  # fmt: skip
    conn.commit()
    return _entry_detail(conn, key)


@router.post("/entries/{key}/requeue")
def entry_requeue(
    conn: SyncConnection, client: AdminClient, key: str, body: EntryAction, actor: Actor = None
) -> EntryDetail:
    """Give a quarantined entry another go, once its cause is fixed."""
    try:
        status = curation.requeue(conn, key)
    except LookupError:
        raise ApiError(404, "not_found", "No such entry") from None
    except word_requests.Conflict as error:
        raise ApiError(409, "conflict", "Can't requeue it", str(error)) from None
    audit.record(conn, client, actor, "entry.requeue", key, {"status": "quarantined"},
                 {"status": status, "note": body.note})  # fmt: skip
    conn.commit()
    return _entry_detail(conn, key)


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


@router.get("/requests")
def requests_list(
    conn: SyncConnection,
    status: RequestStatus | None = "pending",
    origin: Origin | None = None,
    q: Prefix = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> RequestPage:
    rows, total = word_requests.listing(conn, status=status, origin=origin, q=_prefix(q),
                                        page=page, per_page=per_page)  # fmt: skip
    return RequestPage(items=[RequestRow.model_validate(row) for row in rows],
                       **_paging(page, per_page, total))  # fmt: skip


@router.post("/requests/{request_id}/approve")
def request_approve(
    conn: SyncConnection,
    client: AdminClient,
    settings: Annotated[Settings, Depends(get_settings)],
    gateway: Annotated[Gateway, Depends(get_gateway)],
    background: BackgroundTasks,
    request_id: uuid.UUID,
    body: Approval,
    actor: Actor = None,
) -> RequestRow:
    """Queue the word, approved and first in the factory's queue; AI writes its Beta now."""
    try:
        approved = word_requests.approve(
            conn, request_id, lemma=body.lemma.strip(), pos=body.pos, kind=body.kind,
            decided_by=audit.decided_by(client, actor), note=body.note,
            tenant=settings.default_tenant,
        )  # fmt: skip
    except LookupError:
        raise ApiError(404, "not_found", "No such request") from None
    except ValueError as error:
        raise ApiError(422, "invalid_value", "Can't approve it like this", str(error)) from None
    except word_requests.Conflict as error:
        raise ApiError(409, "conflict", "Can't approve it", str(error)) from None
    after = {"status": "approved", "entry_key": approved.key, "pos": body.pos, "kind": body.kind}
    audit.record(conn, client, actor, "request.approve", body.lemma, {"status": "pending"},
                 after)  # fmt: skip
    conn.commit()
    if approved.write_now and gateway.chain(tunables.overrides(conn)):
        job = provisional.Job(query=norm(body.lemma), tenant=settings.default_tenant,
                              user_id=None, key=approved.key)  # fmt: skip
        background.add_task(provisional.generate, job, gateway)
    return _request_row(conn, request_id)


@router.post("/requests/{request_id}/reject")
def request_reject(
    conn: SyncConnection,
    client: AdminClient,
    request_id: uuid.UUID,
    body: Rejection,
    actor: Actor = None,
) -> RequestRow:
    """Close the word for good; an AI entry made for it before reviews is retired."""
    decided_by = audit.decided_by(client, actor)
    try:
        retired = word_requests.reject(conn, request_id, decided_by=decided_by, note=body.note)
    except LookupError:
        raise ApiError(404, "not_found", "No such request") from None
    except word_requests.Conflict as error:
        raise ApiError(409, "conflict", "Can't reject it", str(error)) from None
    row = _request_row(conn, request_id)
    audit.record(conn, client, actor, "request.reject", row.lemma, {"status": "pending"},
                 {"status": "rejected", "retired": retired})  # fmt: skip
    conn.commit()
    return row


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
def audit_log(conn: SyncConnection, page: Page = 1, per_page: PerPage = 50) -> AuditPage:
    rows, total = browse.audit(conn, page=page, per_page=per_page)
    return AuditPage(items=[AuditRow.model_validate(row) for row in rows],
                     **_paging(page, per_page, total))  # fmt: skip


def _entry_detail(conn: SyncConnection, key: str) -> EntryDetail:
    return EntryDetail.model_validate(browse.entry(conn, key))


def _request_row(conn: SyncConnection, request_id: uuid.UUID) -> RequestRow:
    rows, _ = word_requests.listing(conn, status=None, origin=None, q=None, page=1,
                                    per_page=1, request_id=request_id)  # fmt: skip
    return RequestRow.model_validate(rows[0])


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
