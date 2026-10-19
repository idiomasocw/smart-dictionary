"""The factory's API (design §5.2) [CG-2]: lease a batch with its grounding packs, read a batch.

Token-only, scope `factory`. The handlers are plain functions, so FastAPI runs them in a
worker thread, and they share the synchronous code the CLI and the tests use.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from smart_dictionary.api.auth import FactoryClient
from smart_dictionary.api.deps import SyncConnection
from smart_dictionary.api.errors import ApiError
from smart_dictionary.factory import lease
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


@router.post("/batches", response_model=LeaseResponse)
def lease_batch(body: LeaseRequest, conn: SyncConnection, _client: FactoryClient) -> LeaseResponse:
    batch = lease.lease(conn, body.size, body.label)
    packs = build(conn, batch.entry_ids) if batch.entry_ids else []
    # The lease must be durable before the routine starts writing.
    conn.commit()
    return LeaseResponse(batch_id=batch.id, lease_expires_at=batch.lease_expires_at, entries=packs)


@router.get("/batches/{batch_id}", response_model=BatchStatus)
def read_batch(batch_id: uuid.UUID, conn: SyncConnection, _client: FactoryClient) -> BatchStatus:
    found = lease.status(conn, batch_id)
    if found is None:
        raise ApiError(404, "not_found", "No such batch")
    return BatchStatus.model_validate(found)
