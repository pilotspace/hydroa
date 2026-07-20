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
from gateway.domain_capture.domain.entities import ClaimStatus, DomainClaim, MemberVerifyState
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
        notify_requested_at=row.notify_requested_at,
        notified_at=row.notified_at,
        member_verified_at=row.member_verified_at,
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

    async def get_own(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> DomainClaim | None:
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

    # ── domain-verify-notify TASK.md §3 (FROZEN @ v1, SECURITY) — additive ──────────

    async def request_notify(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> DomainClaim:
        """Idempotent opt-in: COALESCE preserves the ORIGINAL notify_requested_at on a
        repeat call — a true no-op, not merely "still set" (M1)."""
        stmt = (
            update(TenantDomainClaimRow)
            .where(
                TenantDomainClaimRow.id == claim_id,
                TenantDomainClaimRow.tenant_id == tenant_id,
            )
            .values(
                notify_requested_at=func.coalesce(
                    TenantDomainClaimRow.notify_requested_at, func.now()
                )
            )
            .returning(TenantDomainClaimRow)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        row = result.scalar_one_or_none()
        if row is None:
            raise DomainClaimNotFoundError
        return _to_entity(row)

    async def clear_notify(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        await self._session.execute(
            update(TenantDomainClaimRow)
            .where(
                TenantDomainClaimRow.id == claim_id,
                TenantDomainClaimRow.tenant_id == tenant_id,
            )
            .values(notify_requested_at=None)
        )
        await self._session.commit()

    async def mark_notified(self, *, claim_id: uuid.UUID) -> bool:
        """Atomic conditional claim (R-sec-3) — the ONLY caller that gets True back may
        dispatch the email; safe under overlapping ticks/replicas regardless of count."""
        stmt = (
            update(TenantDomainClaimRow)
            .where(
                TenantDomainClaimRow.id == claim_id,
                TenantDomainClaimRow.notified_at.is_(None),
            )
            .values(notified_at=func.now())
            .returning(TenantDomainClaimRow.id)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def list_notify_candidates(self, now: datetime) -> list[DomainClaim]:
        rows = (
            (
                await self._session.execute(
                    select(TenantDomainClaimRow).where(
                        TenantDomainClaimRow.notify_requested_at.is_not(None),
                        TenantDomainClaimRow.status == ClaimStatus.PENDING.value,
                        TenantDomainClaimRow.notified_at.is_(None),
                        TenantDomainClaimRow.expires_at > now,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_to_entity(row) for row in rows]

    # ── member-verified-recognition TASK.md §3 (FROZEN @ v1, SECURITY) — additive ──────

    async def issue_member_verify_code(
        self,
        *,
        claim_id: uuid.UUID,
        tenant_id: uuid.UUID,
        code_hash: str,
        expires_at: datetime,
    ) -> DomainClaim:
        """Store hash+expiry, reset attempt_count=0 (tenant-scoped). status/verified_at/
        member_verified_at UNTOUCHED — this only arms a fresh in-flight code."""
        stmt = (
            update(TenantDomainClaimRow)
            .where(
                TenantDomainClaimRow.id == claim_id,
                TenantDomainClaimRow.tenant_id == tenant_id,
            )
            .values(
                member_verify_code_hash=code_hash,
                member_verify_code_expires_at=expires_at,
                member_verify_attempt_count=0,
            )
            .returning(TenantDomainClaimRow)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        row = result.scalar_one_or_none()
        if row is None:
            raise DomainClaimNotFoundError
        return _to_entity(row)

    async def load_member_verify_row_for_update(
        self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> MemberVerifyState | None:
        """SELECT … FOR UPDATE the tenant-scoped row (serializes concurrent guesses so the
        cap is exact) — the lock is held on this session until it commits/rolls back."""
        row = await self._session.scalar(
            select(TenantDomainClaimRow)
            .where(
                TenantDomainClaimRow.id == claim_id,
                TenantDomainClaimRow.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if row is None:
            return None
        return MemberVerifyState(
            domain=row.domain,
            status=ClaimStatus(row.status),
            member_verified_at=row.member_verified_at,
            code_hash=row.member_verify_code_hash,
            code_expires_at=row.member_verify_code_expires_at,
            attempt_count=row.member_verify_attempt_count,
        )

    async def mark_member_verified(self, *, claim_id: uuid.UUID) -> DomainClaim:
        """SET member_verified_at=now(), CLEAR the 3 code columns (single-use). status,
        verified_at, and both unique indexes are UNTOUCHED (M7 — auto-join never fires)."""
        stmt = (
            update(TenantDomainClaimRow)
            .where(TenantDomainClaimRow.id == claim_id)
            .values(
                member_verified_at=func.now(),
                member_verify_code_hash=None,
                member_verify_code_expires_at=None,
                member_verify_attempt_count=0,
            )
            .returning(TenantDomainClaimRow)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        row = result.scalar_one_or_none()
        if row is None:
            raise DomainClaimNotFoundError
        return _to_entity(row)

    async def bump_member_verify_attempt(self, *, claim_id: uuid.UUID, invalidate: bool) -> int:
        """Atomic +1; when `invalidate` also clears hash+expiry (single-use invalidation at
        the cap/expiry). Runs in the SAME transaction as the FOR-UPDATE load above, so the
        ≤5 cap holds EXACTLY under concurrent guesses. Returns the new attempt count."""
        values: dict[str, object] = {
            "member_verify_attempt_count": TenantDomainClaimRow.member_verify_attempt_count + 1,
        }
        if invalidate:
            values["member_verify_code_hash"] = None
            values["member_verify_code_expires_at"] = None
        stmt = (
            update(TenantDomainClaimRow)
            .where(TenantDomainClaimRow.id == claim_id)
            .values(**values)
            .returning(TenantDomainClaimRow.member_verify_attempt_count)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        new_count = result.scalar_one_or_none()
        if new_count is None:
            raise DomainClaimNotFoundError
        return int(new_count)
