"""SqlAlchemyDomainClaimRepository (domain-capture TASK.md §3 — FROZEN @ v1).

Implements BOTH DomainClaimRepository (CRUD) and DomainClaimResolver (the signup-time
indexed point lookup) — one repository class serving two Protocol ports via structural
typing, mirrors SqlAlchemyIdentityRepository serving multiple IdentityRepository methods.

Concurrency-safety primitives (backend-architect discipline, named explicitly):
  - create_or_reissue: ONE `INSERT ... ON CONFLICT (tenant_id, domain) DO UPDATE` — no
    read-modify-write window between checking for an existing pending row and writing the
    reissued token/expiry (mirrors InviteRepository.create_or_replace's upsert shape).
  - mark_verified: ONE `UPDATE ... WHERE id = :id AND status = 'pending'` guarded by M1's
    partial unique index (`uq_domain_claims_domain_verified`) — a concurrent winner
    elsewhere raises IntegrityError -> DomainAlreadyVerifiedError (R7), never a separate
    SELECT-then-write TOCTOU window.
  - revoke: ONE `DELETE ... WHERE id = :id AND tenant_id = :tenant_id` — tenant-scoped in
    the SAME statement that checks existence (appsec-engineer discipline).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.ids import uuid7
from gateway.domain_capture.domain.entities import ClaimStatus, DomainClaim
from gateway.domain_capture.domain.errors import (
    DomainAlreadyVerifiedError,
    DomainClaimNotFoundError,
)
from gateway.domain_capture.infrastructure.orm import TenantDomainClaimRow


def _to_entity(row: TenantDomainClaimRow) -> DomainClaim:
    return DomainClaim(
        id=row.id,
        tenant_id=row.tenant_id,
        domain=row.domain,
        verification_token=row.verification_token,
        status=ClaimStatus(row.status),
        created_at=row.created_at,
        verified_at=row.verified_at,
        expires_at=row.expires_at,
        created_by_user_id=row.created_by_user_id,
    )


class SqlAlchemyDomainClaimRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_or_reissue(
        self,
        *,
        tenant_id: uuid.UUID,
        domain: str,
        verification_token: str,
        expires_at: datetime,
        created_by_user_id: uuid.UUID,
    ) -> DomainClaim:
        stmt = (
            pg_insert(TenantDomainClaimRow)
            .values(
                id=uuid7(),
                tenant_id=tenant_id,
                domain=domain,
                verification_token=verification_token,
                status=ClaimStatus.PENDING.value,
                expires_at=expires_at,
                created_by_user_id=created_by_user_id,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "domain"],
                set_={
                    "verification_token": verification_token,
                    "expires_at": expires_at,
                    # A reissue attempt NEVER regresses an already-verified row back to
                    # pending (M2) — bare `status` in an ON CONFLICT DO UPDATE SET clause
                    # refers to the EXISTING (pre-update) target row, not the proposed
                    # insert values.
                    "status": case(
                        (
                            TenantDomainClaimRow.status == ClaimStatus.VERIFIED.value,
                            ClaimStatus.VERIFIED.value,
                        ),
                        else_=ClaimStatus.PENDING.value,
                    ),
                },
            )
            .returning(TenantDomainClaimRow)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return _to_entity(result.scalar_one())

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[DomainClaim]:
        rows = (
            (
                await self._session.execute(
                    select(TenantDomainClaimRow)
                    .where(TenantDomainClaimRow.tenant_id == tenant_id)
                    .order_by(TenantDomainClaimRow.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_to_entity(row) for row in rows]

    async def get_own(
        self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> DomainClaim | None:
        row = await self._session.scalar(
            select(TenantDomainClaimRow).where(
                TenantDomainClaimRow.id == claim_id,
                TenantDomainClaimRow.tenant_id == tenant_id,
            )
        )
        return _to_entity(row) if row is not None else None

    async def mark_verified(self, *, claim_id: uuid.UUID) -> DomainClaim:
        stmt = (
            update(TenantDomainClaimRow)
            .where(
                TenantDomainClaimRow.id == claim_id,
                TenantDomainClaimRow.status == ClaimStatus.PENDING.value,
            )
            .values(status=ClaimStatus.VERIFIED.value, verified_at=func.now())
            .returning(TenantDomainClaimRow)
        )
        try:
            result = await self._session.execute(stmt)
            await self._session.commit()
        except IntegrityError:
            # M1's partial unique index (uq_domain_claims_domain_verified) rejected the
            # UPDATE — a DIFFERENT tenant's claim on the SAME domain verified first (R7).
            await self._session.rollback()
            raise DomainAlreadyVerifiedError from None

        row = result.scalar_one_or_none()
        if row is not None:
            return _to_entity(row)

        # Zero rows affected without an IntegrityError: either this exact claim was
        # already verified (a duplicate verify call for the SAME id — treat idempotently)
        # or the row no longer exists (revoked concurrently).
        refetched = await self._session.scalar(
            select(TenantDomainClaimRow).where(TenantDomainClaimRow.id == claim_id)
        )
        if refetched is not None and refetched.status == ClaimStatus.VERIFIED.value:
            return _to_entity(refetched)
        raise DomainClaimNotFoundError

    async def revoke(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(TenantDomainClaimRow)
            .where(
                TenantDomainClaimRow.id == claim_id,
                TenantDomainClaimRow.tenant_id == tenant_id,
            )
            .returning(TenantDomainClaimRow.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def has_verified_claim_by_other_tenant(
        self, *, domain: str, tenant_id: uuid.UUID
    ) -> bool:
        row = await self._session.scalar(
            select(TenantDomainClaimRow.id).where(
                TenantDomainClaimRow.domain == domain,
                TenantDomainClaimRow.status == ClaimStatus.VERIFIED.value,
                TenantDomainClaimRow.tenant_id != tenant_id,
            )
        )
        return row is not None

    async def resolve_verified_tenant(self, domain: str) -> uuid.UUID | None:
        return await self._session.scalar(
            select(TenantDomainClaimRow.tenant_id).where(
                TenantDomainClaimRow.domain == domain,
                TenantDomainClaimRow.status == ClaimStatus.VERIFIED.value,
            )
        )
