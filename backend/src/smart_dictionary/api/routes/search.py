"""GET /v1/lookup and /v1/suggest [LK-1…LK-6, LK-10]."""

import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from smart_dictionary.api.auth import CurrentPrincipal
from smart_dictionary.api.deps import Connection
from smart_dictionary.api.errors import ApiError
from smart_dictionary.core.settings import L1Variety, Settings, get_settings
from smart_dictionary.search import service
from smart_dictionary.search.service import Direction, LookupGroup, Suggestion

router = APIRouter(prefix="/v1", tags=["search"])

QueryText = Annotated[str, Query(min_length=1, max_length=100)]
DirectionParam = Annotated[Direction, Query(alias="dir")]


class QueryEcho(BaseModel):
    q: str
    norm: str
    direction: Literal["auto", "en", "l1"]
    variety: str


class LookupResponse(BaseModel):
    query: QueryEcho
    groups: list[LookupGroup]
    did_you_mean: list[Suggestion]


class SuggestResponse(BaseModel):
    query: QueryEcho
    items: list[Suggestion]


@router.get("/lookup", response_model=LookupResponse)
async def lookup(
    conn: Connection,
    principal: CurrentPrincipal,
    settings: Annotated[Settings, Depends(get_settings)],
    q: QueryText,
    direction: DirectionParam = "auto",
    variety: L1Variety | None = None,
) -> LookupResponse:
    started = time.perf_counter()
    query = _query(q, direction, variety or settings.default_variety)
    groups, did_you_mean = await service.lookup(conn, query)
    latency_ms = round((time.perf_counter() - started) * 1000)
    await service.log_lookup(conn, principal, query, groups, did_you_mean, latency_ms)
    return LookupResponse(query=_echo(query), groups=groups, did_you_mean=did_you_mean)


@router.get("/suggest", response_model=SuggestResponse)
async def suggest(
    conn: Connection,
    _principal: CurrentPrincipal,
    settings: Annotated[Settings, Depends(get_settings)],
    q: Annotated[str, Query(min_length=2, max_length=100)],
    direction: DirectionParam = "auto",
    variety: L1Variety | None = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> SuggestResponse:
    query = _query(q, direction, variety or settings.default_variety)
    return SuggestResponse(
        query=_echo(query), items=await service.suggest(conn, query, limit=limit)
    )


def _query(q: str, direction: Direction, variety: str) -> service.Query:
    if any(ord(ch) < 32 for ch in q):
        raise ApiError(400, "invalid_query", "The query contains control characters")
    return service.Query(raw=q.strip(), direction=direction, variety=variety)


def _echo(query: service.Query) -> QueryEcho:
    normalized = query.norm_l1 if query.direction == "l1" else query.norm_en
    return QueryEcho(q=query.raw, norm=normalized, direction=query.direction, variety=query.variety)
