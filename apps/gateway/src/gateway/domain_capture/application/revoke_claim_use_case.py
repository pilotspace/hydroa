"""RevokeDomainClaimUseCase (domain-capture TASK.md §3 M7 — FROZEN @ v1)."""

from __future__ import annotations

import uuid

from gateway.domain_capture.domain.errors import DomainClaimNotFoundError
from gateway.domain_capture.domain.ports import DomainClaimRepository


class RevokeDomainClaimUseCase:
    def __init__(self, repository: DomainClaimRepository) -> None:
        self._repository = repository

    async def execute(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        deleted = await self._repository.revoke(claim_id=claim_id, tenant_id=tenant_id)
        if not deleted:
            raise DomainClaimNotFoundError
