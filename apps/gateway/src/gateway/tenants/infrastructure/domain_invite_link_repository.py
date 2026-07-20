"""SQLAlchemy repository for the domain-restricted shareable invite link
(invite-by-domain TASK.md §3, FROZEN @ v1, SECURITY, task 6a).

Two surfaces share this repo:
  - AUTHENTICATED admin: eligibility read + create-supersede + list-active + revoke.
  - PUBLIC redeem: resolve link by token_hash, UPSERT a per-(link,email) code, and the
    one-transaction verify+provision choke point that MIRRORS InviteRepository.accept
    (SELECT … FOR UPDATE → capture attrs BEFORE commit → assert_seat_available →
    UserRow(MEMBER)+flush → SeatMembershipEventRow 'joined' → consume redemption → commit).

Sibling-consistency: imports the concrete ORM rows directly (no new Protocol port), matching
invite_repository.py's own style.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.ids import uuid7
from gateway.tenants.application.entitlements import assert_seat_available
from gateway.tenants.domain.entities import DomainInviteLink, DomainInviteLinkStatus
from gateway.tenants.domain.errors import (
    DomainInviteLinkInactiveError,
    EmailAlreadyRegisteredError,
    InviteExpiredError,
    InviteNotFoundError,
    SeatCapExceededError,
)
from gateway.tenants.infrastructure.orm import (
    DomainInviteLinkRow,
    DomainInviteRedemptionRow,
    SeatMembershipEventRow,
    UserRow,
)


def _row_to_link(row: DomainInviteLinkRow) -> DomainInviteLink:
    return DomainInviteLink(
        id=row.id,
        tenant_id=row.tenant_id,
        domain=row.domain,
        status=DomainInviteLinkStatus(row.status),
        expires_at=row.expires_at,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


@dataclass(frozen=True, slots=True)
class RedeemState:
    """A snapshot of the FOR-UPDATE-locked link + its (link, email) redemption row, captured
    BEFORE any commit/rollback (the MissingGreenlet trap)."""

    link_id: uuid.UUID
    tenant_id: uuid.UUID
    domain: str
    status: DomainInviteLinkStatus
    link_expires_at: datetime
    redemption_id: uuid.UUID | None
    code_hash: str | None
    code_expires_at: datetime | None
    attempt_count: int


class DomainInviteLinkRepository:
    """Tenant-scoped create/list/revoke + public resolve/redeem/provision on the two
    domain-invite tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -----------------------------------------------------------------------
    # AUTHENTICATED admin surface
    # -----------------------------------------------------------------------

    async def is_domain_eligible(self, *, tenant_id: uuid.UUID, domain: str) -> bool:
        """True iff the caller's OWN tenant holds a claim on `domain` that is member-verified
        (member_verified_at IS NOT NULL) OR owner-verified (status='verified') (M1). Tenant-
        scoped read — never a cross-tenant query (anti-confused-deputy)."""
        row = await self._session.scalar(
            text(
                "SELECT 1 FROM tenant_domain_claims "
                "WHERE tenant_id = :tid AND domain = :domain "
                "AND (member_verified_at IS NOT NULL OR status = 'verified') LIMIT 1"
            ),
            {"tid": str(tenant_id), "domain": domain},
        )
        return row is not None

    async def create_link(
        self,
        *,
        tenant_id: uuid.UUID,
        domain: str,
        token_hash: str,
        expires_at: datetime,
        created_by_user_id: uuid.UUID,
    ) -> DomainInviteLink:
        """Atomically supersede (revoke) any existing active link for (tenant, domain) then
        INSERT the fresh one — at most ONE active row per (tenant, domain) (M2). Retries
        ONCE on the rare concurrent-INSERT IntegrityError (the partial unique index catches
        a true double-submit), mirroring InviteRepository.create_or_replace."""
        last_exc: IntegrityError | None = None
        for _attempt in range(2):
            try:
                return await self._create_link_once(
                    tenant_id=tenant_id,
                    domain=domain,
                    token_hash=token_hash,
                    expires_at=expires_at,
                    created_by_user_id=created_by_user_id,
                )
            except IntegrityError as exc:
                await self._session.rollback()
                last_exc = exc
                continue
        assert last_exc is not None
        raise last_exc

    async def _create_link_once(
        self,
        *,
        tenant_id: uuid.UUID,
        domain: str,
        token_hash: str,
        expires_at: datetime,
        created_by_user_id: uuid.UUID,
    ) -> DomainInviteLink:
        existing = await self._session.scalar(
            select(DomainInviteLinkRow)
            .where(
                DomainInviteLinkRow.tenant_id == tenant_id,
                DomainInviteLinkRow.domain == domain,
                DomainInviteLinkRow.status == DomainInviteLinkStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        if existing is not None:
            existing.status = DomainInviteLinkStatus.REVOKED.value

        row = DomainInviteLinkRow(
            tenant_id=tenant_id,
            domain=domain,
            token_hash=token_hash,
            status=DomainInviteLinkStatus.ACTIVE.value,
            expires_at=expires_at,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(row)
        return _row_to_link(row)

    async def list_active(self, *, tenant_id: uuid.UUID) -> list[DomainInviteLink]:
        """Active links in the caller's tenant, newest-first (M4)."""
        rows = (
            (
                await self._session.execute(
                    select(DomainInviteLinkRow)
                    .where(
                        DomainInviteLinkRow.tenant_id == tenant_id,
                        DomainInviteLinkRow.status == DomainInviteLinkStatus.ACTIVE.value,
                    )
                    .order_by(DomainInviteLinkRow.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_row_to_link(r) for r in rows]

    async def revoke(
        self, *, link_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> DomainInviteLink:
        """Lock, validate tenant-scoped, flip to revoked (idempotent target state), commit
        (M5). Unknown id OR cross-tenant id → InviteNotFoundError (no oracle, R13)."""
        row = await self._session.scalar(
            select(DomainInviteLinkRow)
            .where(
                DomainInviteLinkRow.id == link_id,
                DomainInviteLinkRow.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if row is None:
            await self._session.rollback()
            raise InviteNotFoundError(f"Domain invite link {link_id} not found in tenant {tenant_id}")
        row.status = DomainInviteLinkStatus.REVOKED.value
        await self._session.commit()
        await self._session.refresh(row)
        return _row_to_link(row)

    # -----------------------------------------------------------------------
    # PUBLIC redeem — step 1 (issue)
    # -----------------------------------------------------------------------

    async def resolve_active_link_by_token_hash(
        self, *, token_hash: str, now: datetime
    ) -> DomainInviteLink:
        """Read-only resolution + active/expiry gate for step-1 (no lock, M6).

        Raises:
            InviteNotFoundError: no link has this token_hash (R11 — no oracle).
            DomainInviteLinkInactiveError: the link is revoked (R9).
            InviteExpiredError: active but past expires_at (R10).
        """
        row = await self._session.scalar(
            select(DomainInviteLinkRow).where(DomainInviteLinkRow.token_hash == token_hash)
        )
        if row is None:
            raise InviteNotFoundError("No domain invite link for the given token")
        if row.status != DomainInviteLinkStatus.ACTIVE.value:
            raise DomainInviteLinkInactiveError(f"Domain invite link {row.id} is not active")
        if row.expires_at < now:
            raise InviteExpiredError(f"Domain invite link {row.id} expired at {row.expires_at}")
        return _row_to_link(row)

    async def upsert_redemption(
        self,
        *,
        link_id: uuid.UUID,
        email: str,
        code_hash: str,
        code_expires_at: datetime,
    ) -> None:
        """Insert-or-replace the (link, email) code row: fresh hash + expiry + attempt_count
        reset to 0 (M6). UPSERT target = uq_domain_invite_redemptions_link_email."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(DomainInviteRedemptionRow).values(
            id=uuid7(),
            link_id=link_id,
            email=email,
            code_hash=code_hash,
            code_expires_at=code_expires_at,
            attempt_count=0,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DomainInviteRedemptionRow.link_id, DomainInviteRedemptionRow.email],
            set_={
                "code_hash": code_hash,
                "code_expires_at": code_expires_at,
                "attempt_count": 0,
                "updated_at": text("now()"),
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()

    # -----------------------------------------------------------------------
    # PUBLIC redeem — step 2 (verify + provision), one transaction under FOR UPDATE
    # -----------------------------------------------------------------------

    async def load_link_and_redemption_for_update(
        self, *, token_hash: str, email: str
    ) -> RedeemState | None:
        """SELECT the link row FOR UPDATE (serializes concurrent verifies so the code is
        single-use / exactly-once), then its (link, email) redemption row FOR UPDATE. The
        locks are held on THIS session until it commits/rolls back. Returns None iff no link
        resolves (→ InviteNotFoundError in the use case). All attrs captured eagerly."""
        link = await self._session.scalar(
            select(DomainInviteLinkRow)
            .where(DomainInviteLinkRow.token_hash == token_hash)
            .with_for_update()
        )
        if link is None:
            return None
        link_id = link.id
        tenant_id = link.tenant_id
        domain = link.domain
        status = DomainInviteLinkStatus(link.status)
        link_expires_at = link.expires_at

        redemption = await self._session.scalar(
            select(DomainInviteRedemptionRow)
            .where(
                DomainInviteRedemptionRow.link_id == link_id,
                DomainInviteRedemptionRow.email == email,
            )
            .with_for_update()
        )
        return RedeemState(
            link_id=link_id,
            tenant_id=tenant_id,
            domain=domain,
            status=status,
            link_expires_at=link_expires_at,
            redemption_id=redemption.id if redemption is not None else None,
            code_hash=redemption.code_hash if redemption is not None else None,
            code_expires_at=redemption.code_expires_at if redemption is not None else None,
            attempt_count=redemption.attempt_count if redemption is not None else 0,
        )

    async def bump_redemption_attempt(
        self, *, redemption_id: uuid.UUID, invalidate: bool
    ) -> None:
        """Persist a wrong-guess: increment attempt_count, OR (at the cap) DELETE the row to
        invalidate the code. Committed BEFORE the use case raises so concurrent guesses
        serialize (safety rule)."""
        if invalidate:
            await self._session.execute(
                delete(DomainInviteRedemptionRow).where(
                    DomainInviteRedemptionRow.id == redemption_id
                )
            )
        else:
            await self._session.execute(
                update(DomainInviteRedemptionRow)
                .where(DomainInviteRedemptionRow.id == redemption_id)
                .values(
                    attempt_count=DomainInviteRedemptionRow.attempt_count + 1,
                    updated_at=text("now()"),
                )
            )
        await self._session.commit()

    async def invalidate_redemption(self, *, redemption_id: uuid.UUID) -> None:
        """DELETE the (expired) code row (single-use) and commit."""
        await self._session.execute(
            delete(DomainInviteRedemptionRow).where(DomainInviteRedemptionRow.id == redemption_id)
        )
        await self._session.commit()

    async def provision_member_and_consume(
        self,
        *,
        tenant_id: uuid.UUID,
        redemption_id: uuid.UUID,
        email: str,
        password_hash: str,
        now: datetime,
    ) -> tuple[uuid.UUID, uuid.UUID, str]:
        """Provision exactly ONE MEMBER + append a 'joined' seat event + consume the
        redemption row, ALL in the transaction whose FOR UPDATE link/redemption locks are
        still held from load_link_and_redemption_for_update (M7). Mirrors
        InviteRepository.accept.

        Raises:
            SeatCapExceededError: admitting one more member meets/exceeds the cap (R12) —
                rolls back, no user created.
            EmailAlreadyRegisteredError: GLOBAL users.email collision (R8) — rolls back
                everything; the link + redemption row stay intact.
        """
        try:
            await assert_seat_available(self._session, tenant_id)
        except SeatCapExceededError:
            await self._session.rollback()
            raise

        new_user_id = uuid7()
        self._session.add(
            UserRow(
                id=new_user_id,
                tenant_id=tenant_id,
                email=email,
                password_hash=password_hash,
                role="member",
                auth_method="password",
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise EmailAlreadyRegisteredError from exc

        self._session.add(
            SeatMembershipEventRow(
                id=uuid7(),
                tenant_id=tenant_id,
                user_id=new_user_id,
                event_type="joined",
                occurred_at=now,
            )
        )
        await self._session.execute(
            delete(DomainInviteRedemptionRow).where(DomainInviteRedemptionRow.id == redemption_id)
        )
        await self._session.commit()
        return tenant_id, new_user_id, email
