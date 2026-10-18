"""Who is calling: every request resolves to a Principal (design §5.1, ADR-019).

The BBB server's nginx proxy checks the student's BBB session and forwards the
verified identity in X-BBB-* headers along with X-Dict-Secret. Without a configured
secret (development and tests only; production refuses to start) the headers are
taken as given.
"""

import hmac
from typing import Annotated

from fastapi import Depends, Request

from smart_dictionary.api.errors import ApiError
from smart_dictionary.core.principal import Principal
from smart_dictionary.core.settings import Settings, get_settings


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
