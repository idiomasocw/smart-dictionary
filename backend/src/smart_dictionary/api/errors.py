"""Errors as RFC 9457 problem details (design §5.4), FastAPI's own 404, 405 and 422 included."""

from collections.abc import Mapping
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

PROBLEM_JSON = "application/problem+json"


class ApiError(Exception):
    """An error the client should see: a status, a stable machine code and a human title."""

    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.detail = status, code, title, detail


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, error: ApiError) -> JSONResponse:
        return _problem(request, error)

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # For example "query.q: String should have at least 1 character".
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
        return _problem(request, ApiError(422, "invalid_request", "Invalid request", detail))

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, error: HTTPException) -> JSONResponse:
        # Raised by the framework itself: an unknown path, a method the route doesn't take.
        status = HTTPStatus(error.status_code)
        detail = error.detail if error.detail != status.phrase else None
        problem = ApiError(error.status_code, status.name.lower(), status.phrase, detail)
        return _problem(request, problem, error.headers)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, _error: Exception) -> JSONResponse:
        # Starlette re-raises the error after sending this, so the server still logs it.
        return _problem(request, ApiError(500, "internal_error", "Internal server error"))


def _problem(
    request: Request, error: ApiError, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    body: dict[str, str | int] = {
        "type": "about:blank",
        "title": error.title,
        "status": error.status,
        "code": error.code,
    }
    if error.detail:
        body["detail"] = error.detail
    body["instance"] = request.url.path
    return JSONResponse(body, status_code=error.status, headers=headers, media_type=PROBLEM_JSON)
