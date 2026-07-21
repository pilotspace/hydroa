"""CreateAccessRequestUseCase (signup-refusal-router TASK.md §3 M4/M5/M6 — FROZEN @ v1,
SECURITY task).

SAFETY RULE (binding, verbatim from §3): "No code path in the request-access endpoint
reads resolve_verified_tenant, any user/tenant/domain-claim table, or any account-
existence signal — enforced by construction: the handler only validates the email shape
and INSERTs." This use case is that construction: it derives the domain, calls the
repository's ONE `create` method, and returns — no lookup, no branch, no conditional
anywhere in this file that could vary by what the server already knows about the email.
"""

from __future__ import annotations

from datetime import datetime

from gateway.access_requests.domain.entities import AccessRequest
from gateway.access_requests.domain.ports import AccessRequestRepository


class CreateAccessRequestUseCase:
    def __init__(self, repository: AccessRequestRepository) -> None:
        self._repository = repository

    async def execute(self, *, email_raw: str, now: datetime) -> AccessRequest:
        # Derived server-side, lower-cased apex — mirrors tenants/api/router.py:signup's
        # own `body.email.lower().rsplit("@", 1)[-1]` domain-derivation idiom exactly
        # (§0 GROUND anchor). The BFF/gateway pydantic EmailStr layer is the format
        # authority (R1) — by the time execute() runs, `email_raw` is a validated email,
        # so a plain split is safe (no further validation performed here — M6: this use
        # case ONLY validates shape via the caller's EmailStr and INSERTs).
        email = email_raw.strip().lower()
        domain = email.rsplit("@", 1)[-1]
        return await self._repository.create(email=email, domain=domain, created_at=now)
