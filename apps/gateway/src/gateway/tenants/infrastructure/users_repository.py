"""SQLAlchemy repository for tenant user role operations (rbac-admin-ui TASK.md §3).

Provides read + update operations on the users table scoped to a tenant.
All queries are tenant-scoped — cross-tenant isolation is enforced here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role, User
from gateway.tenants.infrastructure.orm import UserRow


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


def _row_to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        tenant_id=row.tenant_id,
        email=row.email,
        password_hash=row.password_hash,
        role=Role(row.role),
    )
