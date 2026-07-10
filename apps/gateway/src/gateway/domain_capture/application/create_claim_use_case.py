"""CreateDomainClaimUseCase (domain-capture TASK.md §3 M2/M3/M4 — FROZEN @ v1)."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from gateway.domain_capture.domain.domain_validation import normalize_domain
from gateway.domain_capture.domain.entities import DomainClaim
from gateway.domain_capture.domain.errors import DomainAlreadyVerifiedError
from gateway.domain_capture.domain.ports import DomainClaimRepository

_TOKEN_BYTES = 32
_CLAIM_TTL_DAYS = 7


class CreateDomainClaimUseCase:
    def __init__(self, repository: DomainClaimRepository) -> None:
        self._repository = repository

    async def execute(
        self, *, tenant_id: uuid.UUID, domain_raw: str, created_by_user_id: uuid.UUID
    ) -> DomainClaim:
        # M3: validate+normalize BEFORE any DB IO — raises DomainInvalidError (400, R1).
        domain = normalize_domain(domain_raw)

        # M4: UX-level fail-fast pre-check — the partial unique index (M1) is the actual
        # structural backstop, not this check alone (see mark_verified's own race handling).
        if await self._repository.has_verified_claim_by_other_tenant(
            domain=domain, tenant_id=tenant_id
        ):
            raise DomainAlreadyVerifiedError

        token = secrets.token_urlsafe(_TOKEN_BYTES)
        expires_at = datetime.now(UTC) + timedelta(days=_CLAIM_TTL_DAYS)
        return await self._repository.create_or_reissue(
            tenant_id=tenant_id,
            domain=domain,
            verification_token=token,
            expires_at=expires_at,
            created_by_user_id=created_by_user_id,
        )
