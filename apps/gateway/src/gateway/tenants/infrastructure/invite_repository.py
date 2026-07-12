"""SQLAlchemy repository for tenant invite issuance (member-invite-issuance TASK.md §3).

Provides create/list/lookup/revoke operations on the invites table, all tenant-scoped.
Mirrors users_repository.py's shape; the row-lock + retry-on-collision pattern mirrors
agent_oauth/infrastructure/repository.py's DeviceAuthorizationRow handling.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.ids import uuid7
from gateway.tenants.application.entitlements import assert_seat_available
from gateway.tenants.domain.entities import Invite, InvitePreview, InviteStatus, Role
from gateway.tenants.domain.errors import (
    EmailAlreadyRegisteredError,
    InviteExpiredError,
    InviteNotFoundError,
    InviteNotPendingError,
    SeatCapExceededError,
)
from gateway.tenants.infrastructure.orm import InviteRow, SeatMembershipEventRow, TenantRow, UserRow


def _row_to_invite(row: InviteRow) -> Invite:
    return Invite(
        id=row.id,
        tenant_id=row.tenant_id,
        email=row.email,
        role=Role(row.role),
        status=InviteStatus(row.status),
        expires_at=row.expires_at,
        invited_by_user_id=row.invited_by_user_id,
        created_at=row.created_at,
    )


class InviteRepository:
    """Tenant-scoped create/read/revoke operations on the invites table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def user_exists_in_tenant(self, *, tenant_id: uuid.UUID, email: str) -> bool:
        """Tenant-SCOPED existence check ONLY — never a cross-tenant query (M6)."""
        row = await self._session.scalar(
            select(UserRow.id).where(UserRow.tenant_id == tenant_id, UserRow.email == email)
        )
        return row is not None

    async def create_or_replace(
        self,
        *,
        tenant_id: uuid.UUID,
        email: str,
        role: Role,
        token_hash: str,
        expires_at: datetime,
        invited_by_user_id: uuid.UUID,
    ) -> Invite:
        """Atomically supersede any existing pending invite for (tenant_id, email).

        Retries ONCE on a concurrent-INSERT IntegrityError — the rare race where no
        pending row existed yet for either of two concurrent callers to lock onto via
        `with_for_update()` below, so both proceed to INSERT and the partial unique index
        (uq_invites_tenant_email_pending) catches the true double-submit at flush time
        (CLAUDE.md design-for-failure: bounded retry, never a lost update, never two live
        pending rows — member-invite-issuance TASK.md §3 Access pattern step 5).
        """
        last_exc: IntegrityError | None = None
        for _attempt in range(2):
            try:
                return await self._create_or_replace_once(
                    tenant_id=tenant_id,
                    email=email,
                    role=role,
                    token_hash=token_hash,
                    expires_at=expires_at,
                    invited_by_user_id=invited_by_user_id,
                )
            except IntegrityError as exc:
                await self._session.rollback()
                last_exc = exc
                continue
        # Unreachable unless both attempts collide — the loop always returns or sets last_exc.
        assert last_exc is not None
        raise last_exc

    async def _create_or_replace_once(
        self,
        *,
        tenant_id: uuid.UUID,
        email: str,
        role: Role,
        token_hash: str,
        expires_at: datetime,
        invited_by_user_id: uuid.UUID,
    ) -> Invite:
        # Row-locked lookup: if a pending invite already exists for (tenant_id, email), a
        # concurrent second caller's SELECT ... FOR UPDATE blocks here until this
        # transaction commits/rolls back, then observes the fresh state — the common
        # re-invite race is serialized cleanly through this lock (no exception needed).
        existing = await self._session.scalar(
            select(InviteRow)
            .where(
                InviteRow.tenant_id == tenant_id,
                InviteRow.email == email,
                InviteRow.status == InviteStatus.PENDING.value,
            )
            .with_for_update()
        )
        if existing is not None:
            existing.status = InviteStatus.REVOKED.value

        row = InviteRow(
            tenant_id=tenant_id,
            email=email,
            role=role.value,
            token_hash=token_hash,
            status=InviteStatus.PENDING.value,
            expires_at=expires_at,
            invited_by_user_id=invited_by_user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(row)
        return _row_to_invite(row)

    async def list_pending_by_tenant(self, *, tenant_id: uuid.UUID) -> list[Invite]:
        """Return every pending invite in the tenant, newest-first (M8; no pagination —
        a bounded, ephemeral set, mirrors ListTenantUsersUseCase)."""
        rows = (
            (
                await self._session.execute(
                    select(InviteRow)
                    .where(
                        InviteRow.tenant_id == tenant_id,
                        InviteRow.status == InviteStatus.PENDING.value,
                    )
                    .order_by(InviteRow.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_row_to_invite(r) for r in rows]

    async def get_by_id_and_tenant(
        self, *, invite_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Invite | None:
        """Return an invite only if it belongs to the given tenant (plain read, no lock)."""
        row = (
            await self._session.execute(
                select(InviteRow).where(InviteRow.id == invite_id, InviteRow.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return _row_to_invite(row)

    async def revoke(self, *, invite_id: uuid.UUID, tenant_id: uuid.UUID) -> Invite:
        """Lock, validate tenant-scoped + pending, flip to revoked, commit — ALL in one
        transaction (M9). Tenant-scoped, not creator-scoped: any current owner/admin in the
        tenant may revoke any pending invite regardless of who created it.

        Raises:
            InviteNotFoundError: unknown id OR belongs to a different tenant — deliberately
                the SAME error for both (R8, no distinguishing oracle).
            InviteNotPendingError: the resolved invite's status is not 'pending' (R9) — the
                row lock guarantees this resolves cleanly against a concurrent accept/revoke.
        """
        row = await self._session.scalar(
            select(InviteRow)
            .where(InviteRow.id == invite_id, InviteRow.tenant_id == tenant_id)
            .with_for_update()
        )
        if row is None:
            await self._session.rollback()
            raise InviteNotFoundError(f"Invite {invite_id} not found in tenant {tenant_id}")
        if row.status != InviteStatus.PENDING.value:
            # Capture BEFORE rollback(): rollback() expires the row's attributes, and a
            # post-rollback attribute access would trigger a lazy (sync) reload that the
            # async driver can't service outside an awaited call (MissingGreenlet).
            current_status = row.status
            await self._session.rollback()
            raise InviteNotPendingError(
                f"Invite {invite_id} is not pending (status={current_status!r})"
            )
        row.status = InviteStatus.REVOKED.value
        await self._session.commit()
        await self._session.refresh(row)
        return _row_to_invite(row)

    # -----------------------------------------------------------------------
    # member-invite-acceptance TASK.md §3 — additive, public-token-keyed reads/writes.
    # BOTH methods below are keyed PURELY by token_hash — no tenant_id/invite_id input,
    # unlike get_by_id_and_tenant above (the caller here is unauthenticated and possesses
    # neither; §3's RESOLVED open question explains why get_by_id_and_tenant is NOT reused).
    # -----------------------------------------------------------------------

    async def get_preview_by_token_hash(self, *, token_hash: str, now: datetime) -> InvitePreview:
        """Read-only preview lookup — no lock, no mutation (M1).

        Raises:
            InviteNotFoundError: no invite has this token_hash.
            InviteNotPendingError: the resolved invite's status is not 'pending'.
            InviteExpiredError: status IS 'pending' but expires_at has passed (a COMPUTED
                check — never a status write, mirrors the accept() method's own check).
        """
        result = (
            await self._session.execute(
                select(InviteRow, TenantRow.name)
                .join(TenantRow, TenantRow.id == InviteRow.tenant_id)
                .where(InviteRow.token_hash == token_hash)
            )
        ).one_or_none()
        if result is None:
            raise InviteNotFoundError("No invite found for the given token")
        invite_row, tenant_name = result
        if invite_row.status != InviteStatus.PENDING.value:
            raise InviteNotPendingError(
                f"Invite {invite_row.id} is not pending (status={invite_row.status!r})"
            )
        if invite_row.expires_at < now:
            raise InviteExpiredError(f"Invite {invite_row.id} expired at {invite_row.expires_at}")
        return InvitePreview(
            tenant_name=tenant_name,
            email=invite_row.email,
            role=Role(invite_row.role),
            expires_at=invite_row.expires_at,
        )

    async def accept(
        self, *, token_hash: str, password_hash: str, now: datetime
    ) -> tuple[uuid.UUID, uuid.UUID, str]:
        """Lock, validate pending+not-expired, provision a user, flip to accepted, commit —
        ALL in ONE atomic transaction (M2/M5). Concurrency-safety primitive: the SAME
        `SELECT ... FOR UPDATE` row lock `.revoke()` uses above — a concurrent accept and a
        concurrent revoke of the SAME token can never both "win" (M5).

        NOTE (build-time spec-delta, not a silent contract edit — TASK.md §3 CONTRACT prose
        types this method's return as ``tuple[uuid.UUID, uuid.UUID]`` = (tenant_id,
        new_user_id), but AcceptInviteUseCase.execute's OWN frozen signature immediately
        above it needs a THIRD element (email) to build InviteAcceptResponse, and this
        method is the ONLY place in the call chain that has it without a second query. This
        is the SAME class of contract-prose gap CreateInviteUseCase.execute's own docstring
        already names for its sibling issuance file (`-> Invite` prose vs. the actually-
        needed `tuple[Invite, str]`) — resolved the same way: implement the shape that is
        actually needed, flag it here and in the build report, leave TASK.md untouched.

        Returns:
            (tenant_id, new_user_id, email) of the newly provisioned user.

        Raises:
            InviteNotFoundError: no invite has this token_hash.
            InviteNotPendingError: already accepted or already revoked.
            InviteExpiredError: pending but expires_at has passed.
            EmailAlreadyRegisteredError: the invited email collides with the GLOBAL
                users.email uniqueness constraint (M6) — rolls back EVERYTHING, the invite
                stays pending, unflipped.
        """
        row = await self._session.scalar(
            select(InviteRow).where(InviteRow.token_hash == token_hash).with_for_update()
        )
        if row is None:
            await self._session.rollback()
            raise InviteNotFoundError("No invite found for the given token")

        # Capture BEFORE any rollback()/commit(): both expire ORM attributes by default
        # (expire_on_commit), and a post-expiry attribute read triggers a lazy (sync)
        # reload the async driver can't service outside an awaited call (MissingGreenlet)
        # — the SAME trap .revoke() above already dodges.
        current_status = row.status
        invite_pk = row.id
        tenant_id = row.tenant_id
        email = row.email
        role = row.role
        expires_at = row.expires_at

        if current_status != InviteStatus.PENDING.value:
            await self._session.rollback()
            raise InviteNotPendingError(
                f"Invite {invite_pk} is not pending (status={current_status!r})"
            )
        if expires_at < now:
            await self._session.rollback()
            raise InviteExpiredError(f"Invite {invite_pk} expired at {expires_at}")

        # plan-seat-cap TASK.md §3 (FROZEN @ v1, M4): consulted AFTER the pending/not-
        # expired checks, strictly BEFORE the member-creating INSERT — composes safely
        # with this method's own `invites` row lock (only this method ever holds both
        # locks at once, so concurrent instances merely serialize, never cycle).
        try:
            await assert_seat_available(self._session, tenant_id)
        except SeatCapExceededError:
            await self._session.rollback()
            raise

        new_user_id = uuid7()
        new_user = UserRow(
            id=new_user_id,
            tenant_id=tenant_id,
            email=email,
            password_hash=password_hash,
            role=role,
            auth_method="password",
        )
        self._session.add(new_user)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # GLOBAL users.email collision (M6) — roll back EVERYTHING: the invite row
            # stays 'pending', unflipped, and no orphaned users row survives (M5).
            await self._session.rollback()
            raise EmailAlreadyRegisteredError from exc

        # seat-billing TASK.md §3 (FROZEN @ v2, M3(a)): append ONE 'joined' event in the
        # SAME transaction as the new users row — `now` is the accept instant, mirrors
        # every other timestamp this method already derives from its own parameter.
        self._session.add(
            SeatMembershipEventRow(
                id=uuid7(),
                tenant_id=tenant_id,
                user_id=new_user_id,
                event_type="joined",
                occurred_at=now,
            )
        )

        row.status = InviteStatus.ACCEPTED.value
        await self._session.commit()

        return tenant_id, new_user_id, email
