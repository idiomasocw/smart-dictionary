"""Who is calling (design §5.1, ADR-019). api.auth resolves one for every request."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Principal:
    tenant: str
    subject: str | None  # the external user ID (the LMS person ID for tenant "ocw")
    meeting: str | None
    roles: frozenset[str]
    via: Literal["bbb_proxy", "dev"]

    @property
    def is_moderator(self) -> bool:
        return "moderator" in self.roles
