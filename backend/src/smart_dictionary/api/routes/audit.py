"""The audit routine's API (design §6.6) [CG-7]: lease the entries due for an audit, with their
facts and the server's own findings, and submit what the auditor found.

Token-only, scope `audit`: a token that can't lease or publish entries, and a factory token
can't audit. The auditor never edits an entry; what it submits waits in the owner's console,
except high-confidence content fixes while `audit_auto_repair` is on.
"""

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from smart_dictionary.api.auth import AuditClient
from smart_dictionary.api.deps import SyncConnection
from smart_dictionary.api.errors import ApiError
from smart_dictionary.core import tunables
from smart_dictionary.core.settings import Settings, get_settings
from smart_dictionary.factory import audit as auditing

router = APIRouter(prefix="/v1/audit", tags=["audit"])


class LeaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: int = Field(default=100, ge=1, le=200)
    label: str | None = Field(default=None, max_length=100)
    audit_version: str = Field(
        min_length=1, max_length=100, description="a hash of the instructions the run follows"
    )


class LeaseOut(BaseModel):
    batch_id: uuid.UUID | None = Field(description="null when nothing is due")
    expires_at: datetime | None
    items: list[dict[str, Any]] = Field(
        description="per entry: key, lemma, pos, kind, status, written, chosen_by, doc, facts,"
        " past_decisions and rule_findings"
    )


class RuleAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=10, description="the rule finding's id, R1, R2…")
    verdict: Literal["confirm", "dismiss"]
    confidence: auditing.Confidence = "medium"
    note: str | None = Field(default=None, max_length=500)
    proposed: dict[str, Any] | None = Field(
        default=None, description="confirm only: a better fix than the rule's"
    )


class FindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: auditing.FindingType
    target_key: str = Field(max_length=200)
    confidence: auditing.Confidence
    problem: str = Field(min_length=1, max_length=500)
    proposed: dict[str, Any] = Field(default_factory=dict)


class AuditedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    rules: list[RuleAnswerIn] = Field(default_factory=list, max_length=20)
    findings: list[FindingIn] = Field(default_factory=list, max_length=auditing.MAX_FINDINGS)


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, max_length=100)
    audit_version: str = Field(min_length=1, max_length=100)
    entries: list[AuditedIn] = Field(min_length=1, max_length=200)


class OutcomeOut(BaseModel):
    key: str
    outcome: Literal["audited", "not_leased"]
    findings: int = Field(description="open findings recorded, for the owner")
    applied: int = Field(description="applied at once: high-confidence content fixes")
    errors: list[str] = Field(description="findings that couldn't be recorded, and why")


class SubmitOut(BaseModel):
    batch_id: uuid.UUID
    results: list[OutcomeOut]


@router.post("/batches")
def lease_batch(body: LeaseIn, conn: SyncConnection, client: AuditClient) -> LeaseOut:
    found = auditing.lease(conn, body.size, label=body.label, client=client.name,
                           version=body.audit_version)  # fmt: skip
    conn.commit()
    return LeaseOut(batch_id=found.id, expires_at=found.expires_at, items=found.items)


@router.post("/batches/{batch_id}/results")
def submit_results(
    batch_id: uuid.UUID,
    body: SubmitIn,
    conn: SyncConnection,
    _client: AuditClient,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SubmitOut:
    items = [
        auditing.Audited(
            key=entry.key,
            rules=[auditing.RuleAnswer(**answer.model_dump()) for answer in entry.rules],
            findings=[auditing.Finding(**finding.model_dump()) for finding in entry.findings],
        )
        for entry in body.entries
    ]
    auto = tunables.overrides(conn).get("audit_auto_repair", settings.audit_auto_repair)
    try:
        outcomes = auditing.submit(conn, batch_id, items, model=body.model,
                                   version=body.audit_version, auto_repair=auto,
                                   tenant=settings.default_tenant)  # fmt: skip
    except LookupError:
        raise ApiError(404, "not_found", "No such batch") from None
    except auditing.BatchClosed as closed:
        detail = f"Its status is {closed}; lease a new batch"
        raise ApiError(409, "batch_closed", "The batch takes no more results", detail) from None
    conn.commit()
    return SubmitOut(batch_id=batch_id, results=[OutcomeOut(**asdict(o)) for o in outcomes])
