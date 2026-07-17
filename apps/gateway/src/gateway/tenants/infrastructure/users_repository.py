"""SQLAlchemy repository for tenant user role operations (rbac-admin-ui TASK.md §3).

Provides read + update operations on the users table scoped to a tenant.
All queries are tenant-scoped — cross-tenant isolation is enforced here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role, User
from gateway.tenants.infrastructure.orm import TenantRow, UserRow


class UserRoleRepository:
    """Tenant-scoped read + role-update operations on the users table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_tenant(self, *, tenant_id: uuid.UUID) -> list[User]:
        """Return all users in the tenant, ordered by email."""
        rows = (
            (
                await self._session.execute(
                    select(UserRow).where(UserRow.tenant_id == tenant_id).order_by(UserRow.email)
                )
            )
            .scalars()
            .all()
        )
        return [_row_to_user(r) for r in rows]

    async def get_by_id_and_tenant(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> User | None:
        """Return a user only if they belong to the given tenant."""
        row = (
            await self._session.execute(
                select(UserRow).where(UserRow.id == user_id, UserRow.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return _row_to_user(row)

    async def update_role(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, new_role: Role
    ) -> User:
        """Update a user's role (tenant-scoped) and return the updated User.

        Caller is responsible for ensuring the user exists (call get_by_id_and_tenant first).
        """
        await self._session.execute(
            update(UserRow)
            .where(UserRow.id == user_id, UserRow.tenant_id == tenant_id)
            .values(role=new_role.value)
        )
        await self._session.flush()

        row = (
            await self._session.execute(
                select(UserRow).where(UserRow.id == user_id, UserRow.tenant_id == tenant_id)
            )
        ).scalar_one()
        await self._session.commit()
        return _row_to_user(row)

    # -----------------------------------------------------------------------
    # billing-owner-of-record TASK.md §3 (FROZEN @ v1) — M2/M4/M5 support.
    # -----------------------------------------------------------------------

    async def lock_and_get_billing_owner_user_id(self, *, tenant_id: uuid.UUID) -> uuid.UUID | None:
        """``SELECT billing_owner_user_id FROM tenants WHERE id=:t FOR UPDATE`` — the M4
        lock shared by HOOK 1 (role-change), HOOK 2 (deactivation, via the SAME-shaped
        method on ScimUserRepository), and the reassignment endpoint's own write, closing
        the R9 race: whichever of two concurrent operations acquires this lock first
        commits; the other re-evaluates against the POST-commit state.
        """
        return (
            await self._session.execute(
                select(TenantRow.billing_owner_user_id)
                .where(TenantRow.id == tenant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def get_billing_owner_user_id(self, *, tenant_id: uuid.UUID) -> uuid.UUID | None:
        """Unlocked read of the tenant's current billing_owner_user_id (GET /admin/billing-owner,
        M6 — a read-only endpoint never needs the M4 write-lock)."""
        return (
            await self._session.execute(
                select(TenantRow.billing_owner_user_id).where(TenantRow.id == tenant_id)
            )
        ).scalar_one_or_none()

    async def set_billing_owner_user_id(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Write the new designation (M5) — caller is responsible for holding the M4 lock
        (``lock_and_get_billing_owner_user_id``) in the SAME transaction first."""
        await self._session.execute(
            update(TenantRow).where(TenantRow.id == tenant_id).values(billing_owner_user_id=user_id)
        )
        await self._session.commit()


def _row_to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        tenant_id=row.tenant_id,
        email=row.email,
        password_hash=row.password_hash,
        role=Role(row.role),
        # scim-provisioning TASK.md §3 added User.deactivated_at, but this repo's own
        # _row_to_user never populated it (silently defaulted to None regardless of the
        # row's real value) — a landmine for billing-owner-of-record's M5 eligibility
        # check (target.deactivated_at IS NULL), fixed here since nothing upstream of
        # this repo ever depended on the field being force-None (SANCTIONED EDIT,
        # discovered during this task's build — see TASK.md §7 OBSERVE).
        deactivated_at=row.deactivated_at,
    )
