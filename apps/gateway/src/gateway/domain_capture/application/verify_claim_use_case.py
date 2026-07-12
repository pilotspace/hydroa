"""VerifyDomainClaimUseCase (domain-capture TASK.md §3 M6/M13 — FROZEN @ v1).

Concurrency-safety primitive (backend-architect discipline): the actual pending->verified
flip is ONE atomic `UPDATE ... WHERE status='pending'` round trip guarded by M1's partial
unique index — no read-modify-write window between this use case's checks and the write
(DomainClaimRepository.mark_verified owns that atomicity; this use case never re-implements
it as a separate SELECT-then-UPDATE).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from gateway.domain_capture.domain.entities import DomainClaim
from gateway.domain_capture.domain.errors import (
    DomainClaimExpiredError,
    DomainClaimNotFoundError,
    DomainVerificationFailedError,
)
from gateway.domain_capture.domain.ports import DnsTxtResolver, DomainClaimRepository

_CHALLENGE_LABEL_PREFIX = "_ai-proxy-challenge"
_VALUE_PREFIX = "ai-proxy-domain-verification"


class VerifyDomainClaimUseCase:
    def __init__(
        self,
        repository: DomainClaimRepository,
        dns_resolver: DnsTxtResolver,
        *,
        dns_timeout_seconds: float,
    ) -> None:
        self._repository = repository
        self._dns_resolver = dns_resolver
        self._dns_timeout_seconds = dns_timeout_seconds

    async def execute(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> DomainClaim:
        claim = await self._repository.get_own(claim_id=claim_id, tenant_id=tenant_id)
        if claim is None:
            raise DomainClaimNotFoundError

        if datetime.now(UTC) > claim.expires_at:
            raise DomainClaimExpiredError

        record_name = f"{_CHALLENGE_LABEL_PREFIX}.{claim.domain}"
        expected_value = f"{_VALUE_PREFIX}={claim.verification_token}"

        # DnsLookupFailedError (resolver error/NXDOMAIN/empty/timeout) propagates
        # uncaught — fail CLOSED, claim status is untouched (M13, R8).
        values = await self._dns_resolver.lookup_txt(record_name, timeout=self._dns_timeout_seconds)
        if expected_value not in values:
            raise DomainVerificationFailedError

        return await self._repository.mark_verified(claim_id=claim_id)
