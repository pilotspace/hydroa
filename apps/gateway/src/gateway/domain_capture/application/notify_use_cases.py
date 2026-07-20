"""NotifyOptInUseCase / NotifyOptOutUseCase (domain-verify-notify TASK.md §3 — FROZEN
@ v1, SECURITY).

M1/M2: opt a pending, non-expired claim into (or out of) the background auto-verify +
email-me-when-live watch. The recipient email is NEVER decided here — these use cases
only toggle the opt-in timestamp; DomainVerifyNotifyScheduler resolves the recipient
server-side, at dispatch time, from the verified claim's own created_by_user_id (R-sec-1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from gateway.domain_capture.domain.entities import ClaimStatus, DomainClaim
from gateway.domain_capture.domain.errors import (
    DomainClaimExpiredError,
    DomainClaimNotFoundError,
    DomainClaimNotPendingError,
)
from gateway.domain_capture.domain.ports import DomainClaimRepository


class NotifyOptInUseCase:
    def __init__(self, repository: DomainClaimRepository) -> None:
        self._repository = repository

    async def execute(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> DomainClaim:
        claim = await self._repository.get_own(claim_id=claim_id, tenant_id=tenant_id)
        if claim is None:
            raise DomainClaimNotFoundError
        if claim.status != ClaimStatus.PENDING:
            raise DomainClaimNotPendingError
        if datetime.now(UTC) > claim.expires_at:
            raise DomainClaimExpiredError
        return await self._repository.request_notify(claim_id=claim_id, tenant_id=tenant_id)


class NotifyOptOutUseCase:
    def __init__(self, repository: DomainClaimRepository) -> None:
        self._repository = repository

    async def execute(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        claim = await self._repository.get_own(claim_id=claim_id, tenant_id=tenant_id)
        if claim is None:
            raise DomainClaimNotFoundError
        await self._repository.clear_notify(claim_id=claim_id, tenant_id=tenant_id)
