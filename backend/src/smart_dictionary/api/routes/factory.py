"""The factory's API (design §5.2) [CG-2, CG-3]: lease a batch with its grounding packs, check
documents without saving them, submit them, and read a batch.

Token-only, scope `factory`. The handlers are plain functions, so FastAPI runs them in a
worker thread, and they share the synchronous code the CLI and the tests use.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from smart_dictionary.api.auth import FactoryClient
from smart_dictionary.api.deps import SyncConnection
from smart_dictionary.api.errors import ApiError
from smart_dictionary.core import tunables
from smart_dictionary.core.settings import Settings, get_settings
from smart_dictionary.factory import lease, submit
from smart_dictionary.factory.packs import Pack, build

router = APIRouter(prefix="/v1/factory", tags=["factory"])


class LeaseRequest(BaseModel):
    size: int = Field(default=40, ge=1, le=100)
    label: str | None = Field(default=None, max_length=100)


class LeaseResponse(BaseModel):
    batch_id: uuid.UUID | None = Field(description="Null when nothing is left to lease")
    lease_expires_at: datetime | None
    entries: list[Pack]


class BatchEntry(BaseModel):
    key: str
    status: str


class BatchStatus(BaseModel):
    batch_id: uuid.UUID
    status: str
    run_label: str | None
    requested_size: int
    leased_at: datetime
    submitted_at: datetime | None
    entries: list[BatchEntry]


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    check: str
    severity: Literal["error", "warn"]
    where: str
    message: str


class Review(BaseModel):
    verdict: Literal["pass", "fail", "uncertain"]
    notes: list[str] = Field(default_factory=list, max_length=20)


class SubmittedEntry(BaseModel):
    key: str
    doc: dict[str, Any]
    review: Review | None = None
    needs_repair: bool = Field(default=False, description="The reviewer couldn't fix it")


class SubmitRequest(BaseModel):
    model: str | None = Field(default=None, max_length=100)
    prompt_version: str | None = Field(default=None, max_length=100)
    entries: list[SubmittedEntry] = Field(min_length=1, max_length=100)


class EntryResult(BaseModel):
    key: str
    outcome: Literal["published", "needs_repair", "quarantined", "not_leased"]
    score: float | None
    errors: list[IssueOut]
    warnings: list[IssueOut]


class SubmitResponse(BaseModel):
    batch_id: uuid.UUID
    status: str
    results: list[EntryResult]


class Document(BaseModel):
    key: str
    doc: dict[str, Any]


class ValidateRequest(BaseModel):
    entries: list[Document] = Field(min_length=1, max_length=100)


class CheckResult(BaseModel):
    key: str
    valid: bool = Field(description="True when there are no errors")
    score: float | None
    errors: list[IssueOut]
    warnings: list[IssueOut]


class ValidateResponse(BaseModel):
    results: list[CheckResult]


@router.post("/batches", response_model=LeaseResponse)
def lease_batch(body: LeaseRequest, conn: SyncConnection, _client: FactoryClient) -> LeaseResponse:
    batch = lease.lease(conn, body.size, body.label)
    packs = build(conn, batch.entry_ids) if batch.entry_ids else []
    # The lease must be durable before the routine starts writing.
    conn.commit()
    return LeaseResponse(batch_id=batch.id, lease_expires_at=batch.lease_expires_at, entries=packs)


@router.post("/batches/{batch_id}/entries", response_model=SubmitResponse)
def submit_entries(
    batch_id: uuid.UUID,
    body: SubmitRequest,
    conn: SyncConnection,
    client: FactoryClient,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SubmitResponse:
    items = [
        submit.Submission(
            key=entry.key,
            doc=entry.doc,
            verdict=entry.review.verdict if entry.review else None,
            review_notes=entry.review.notes if entry.review else [],
            needs_repair=entry.needs_repair,
        )
        for entry in body.entries
    ]
    produced_by = {"client": client.name, "model": body.model, "prompt": body.prompt_version}
    try:
        status, results = submit.submit(
            conn,
            batch_id,
            items,
            produced_by=produced_by,
            threshold=tunables.overrides(conn).get(
                "factory_publish_threshold", settings.factory_publish_threshold
            ),
        )
    except LookupError:
        raise ApiError(404, "not_found", "No such batch") from None
    except submit.BatchClosed as closed:
        detail = f"Its status is {closed}; lease a new batch"
        raise ApiError(409, "batch_closed", "The batch takes no more entries", detail) from None
    conn.commit()
    return SubmitResponse(
        batch_id=batch_id,
        status=status,
        results=[
            EntryResult(
                key=result.key,
                outcome=result.outcome,
                score=result.score,
                errors=[IssueOut.model_validate(issue) for issue in result.errors],
                warnings=[IssueOut.model_validate(issue) for issue in result.warnings],
            )
            for result in results
        ],
    )


@router.post("/validate", response_model=ValidateResponse)
def validate_entries(
    body: ValidateRequest, conn: SyncConnection, _client: FactoryClient
) -> ValidateResponse:
    """The submit step's checks, without saving anything: the routine runs them while it
    writes."""
    checked = submit.check(conn, [(entry.key, entry.doc) for entry in body.entries])
    return ValidateResponse(
        results=[
            CheckResult(
                key=result.key,
                valid=not result.errors,
                score=result.score,
                errors=[IssueOut.model_validate(issue) for issue in result.errors],
                warnings=[IssueOut.model_validate(issue) for issue in result.warnings],
            )
            for result in checked
        ]
    )


@router.get("/batches/{batch_id}", response_model=BatchStatus)
def read_batch(batch_id: uuid.UUID, conn: SyncConnection, _client: FactoryClient) -> BatchStatus:
    found = lease.status(conn, batch_id)
    if found is None:
        raise ApiError(404, "not_found", "No such batch")
    return BatchStatus.model_validate(found)
