"""POST /v1/requests (design §6.7): a student asks for a word the dictionary doesn't have.

Only after a lookup answered `requestable`: the server decides again, so a request can't add
a typo, a word that exists already, or one the owner rejected. Each student has a daily
number of requests (settings.word_requests_per_day, changeable in the console).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from smart_dictionary.ai import provisional
from smart_dictionary.api.auth import CurrentPrincipal
from smart_dictionary.api.deps import Connection
from smart_dictionary.api.errors import ApiError
from smart_dictionary.content import word_requests
from smart_dictionary.core import tunables
from smart_dictionary.core.settings import L1Variety, Settings, get_settings
from smart_dictionary.search.service import Direction, Query

router = APIRouter(prefix="/v1", tags=["requests"])


class RequestIn(BaseModel):
    q: str = Field(min_length=1, max_length=100, description="The search, as the student typed it")
    dir: Direction = "auto"
    variety: L1Variety | None = None


class RequestOut(BaseModel):
    status: Literal["requested", "exists"]
    headword: str | None = Field(default=None, description="requested: the word asked for")
    entry_key: str | None = Field(default=None, description="exists: open this entry")


@router.post("/requests", status_code=202)
async def request_word(
    body: RequestIn,
    response: Response,
    conn: Connection,
    principal: CurrentPrincipal,
    settings: Annotated[Settings, Depends(get_settings)],
) -> RequestOut:
    if not principal.subject:
        raise ApiError(403, "forbidden", "Only a signed-in student can request a word")
    if any(ord(ch) < 32 for ch in body.q):
        raise ApiError(400, "invalid_query", "The query contains control characters")
    query = Query(raw=body.q.strip(), direction=body.dir,
                  variety=body.variety or settings.default_variety)  # fmt: skip
    language = await provisional.searched_in(conn, query)
    if language is None:
        raise ApiError(422, "not_requestable", "This search can't become a request")
    found = await provisional.resolve(conn, query, language, principal.tenant)
    if found.outcome in ("shown", "writable", "factory"):
        response.status_code = 200
        return RequestOut(status="exists", entry_key=found.key)
    if found.outcome != "requestable" or found.candidate is None:
        detail = "Search for it first" if found.outcome == "translate" else None
        raise ApiError(422, "not_requestable", "This search can't become a request", detail)
    student = {"tenant": principal.tenant, "subject": principal.subject}
    user_id = (await conn.execute(provisional.STUDENT, student)).scalar_one()
    per_day = (await tunables.overrides_async(conn)).get(
        "word_requests_per_day", settings.word_requests_per_day
    )
    q = query.norm_l1 if language == "es" else query.norm_en
    outcome = await word_requests.vote(conn, user_id, found.candidate, language=language,
                                       query=q, per_day=per_day)  # fmt: skip
    if outcome == "quota_exceeded":
        await conn.rollback()
        raise ApiError(429, "quota_exceeded", "No requests left today", "Try again tomorrow")
    if outcome == "invalid":
        await conn.rollback()
        raise ApiError(422, "not_requestable", "This word was declined")
    await conn.commit()
    return RequestOut(status="requested", headword=found.candidate.lemma)
