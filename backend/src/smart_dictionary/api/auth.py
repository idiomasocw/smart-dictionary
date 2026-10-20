"""Who is calling: every request resolves to a Principal (design §5.1, ADR-019).

The BBB server's nginx proxy checks the student's BBB session and forwards the
verified identity in X-BBB-* headers along with X-Dict-Secret. Without a configured
secret (development and tests only; production refuses to start) the headers are
taken as given.
"""

import hmac
from typing import Annotated

from fastapi import Depends, Header, Request

from smart_dictionary.api.deps import SyncConnection
from smart_dictionary.api.errors import ApiError
from smart_dictionary.core.principal import Principal
from smart_dictionary.core.settings import Settings, get_settings
from smart_dictionary.factory import tokens


def current_principal(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Principal:
    headers = request.headers
    secret = settings.bbb_proxy_secret
    if secret is not None:
        supplied = headers.get("x-dict-secret", "")
        if not hmac.compare_digest(supplied.encode(), secret.get_secret_value().encode()):
            raise ApiError(401, "unauthorized", "Missing or invalid proxy credentials")
    moderator = headers.get("x-bbb-is-mod", "").lower() == "true"
    return Principal(
        tenant=settings.default_tenant,
        subject=headers.get("x-bbb-ext-user") or None,
        meeting=headers.get("x-bbb-meeting") or None,
        roles=frozenset({"moderator"} if moderator else ()),
        via="bbb_proxy" if secret is not None else "dev",
    )


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def factory_client(
    conn: SyncConnection, authorization: Annotated[str | None, Header()] = None
) -> tokens.Client:
    """The routine, identified by `Authorization: Bearer <token>` with the `factory` scope."""
    return _bearer(conn, authorization, "factory")


FactoryClient = Annotated[tokens.Client, Depends(factory_client)]


def audit_client(
    conn: SyncConnection, authorization: Annotated[str | None, Header()] = None
) -> tokens.Client:
    """The audit routine, identified by a bearer token with the `audit` scope: it can lease
    entries for an audit and submit findings, nothing else."""
    return _bearer(conn, authorization, "audit")


AuditClient = Annotated[tokens.Client, Depends(audit_client)]


def admin_client(
    conn: SyncConnection, authorization: Annotated[str | None, Header()] = None
) -> tokens.Client:
    """The owner's console, identified by a bearer token with the `admin` scope. Neither the
    proxy secret nor a factory token is enough."""
    return _bearer(conn, authorization, "admin")


AdminClient = Annotated[tokens.Client, Depends(admin_client)]


def _bearer(conn: SyncConnection, authorization: str | None, scope: str) -> tokens.Client:
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    client = tokens.find(conn, token, scope) if scheme.lower() == "bearer" and token else None
    if client is None:
        raise ApiError(401, "unauthorized", f"Missing or invalid {scope} token")
    return client
