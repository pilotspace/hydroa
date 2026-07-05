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

from gateway.tenants.domain.entities import Invite, InviteStatus, Role
from gateway.tenants.domain.errors import InviteNotFoundError, InviteNotPendingError
from gateway.tenants.infrastructure.orm import InviteRow, UserRow


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
