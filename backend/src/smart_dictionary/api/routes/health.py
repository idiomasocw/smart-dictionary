"""Liveness and readiness probes, used by Caddy, Uptime Kuma and the deploy script."""

import logging

from fastapi import APIRouter, Response, status

from smart_dictionary.db import engine

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """The process is up. Never touches the database."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(response: Response) -> dict[str, str]:
    """The process can serve traffic: the database answers."""
    try:
        await engine.ping()
    except Exception:
        log.exception("readiness check failed: database unreachable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "down"}
    return {"status": "ok", "database": "up"}
