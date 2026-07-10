"""SQLAlchemy implementations of the SCIM domain ports."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.scim.domain.entities import ScimToken
from gateway.scim.domain.errors import ScimUniquenessError
from gateway.scim.infrastructure.orm import ScimTokenRow
from gateway.teams.infrastructure.orm import TeamMemberRow
from gateway.tenants.domain.entities import Role, User
from gateway.tenants.infrastructure.orm import UserRow

# SCIM-provisioned users are never authenticatable by password (M3) — a distinct
# sentinel from SSO's own SSO_PASSWORD_HASH_SENTINEL so audit/debugging can tell
# provisioning origin apart from the hash alone (TASK.md §1 ⚠, decided at freeze).
SCIM_PASSWORD_HASH_SENTINEL = "!scim-no-password"  # noqa: S105 — sentinel, not a real password


def _token_row_to_entity(row: ScimTokenRow) -> ScimToken:
    return ScimToken(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        token_hash=row.token_hash,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


def _user_row_to_entity(row: UserRow) -> User:
    return User(
        id=row.id,
        tenant_id=row.tenant_id,
        email=row.email,
        password_hash=row.password_hash,
        role=Role(row.role),
        deactivated_at=row.deactivated_at,
    )


class SqlAlchemyScimTokenRepository:
    """Implements the ScimTokenRepository port."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        token_id: uuid.UUID,
        tenant_id: uuid.UUID,
        name: str,
        token_hash: str,
        created_by_user_id: uuid.UUID | None,
    ) -> ScimToken:
        row = ScimTokenRow(
            id=token_id,
            tenant_id=tenant_id,
            name=name,
            token_hash=token_hash,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return _token_row_to_entity(row)

    async def list_by_tenant(self, tenant_id: uuid.UUID) -> list[ScimToken]:
        result = await self._session.execute(
            select(ScimTokenRow)
            .where(ScimTokenRow.tenant_id == tenant_id)
            .order_by(ScimTokenRow.created_at.desc())
        )
        return [_token_row_to_entity(r) for r in result.scalars().all()]

    async def get_by_id(self, token_id: uuid.UUID) -> ScimToken | None:
        row = (
            await self._session.execute(select(ScimTokenRow).where(ScimTokenRow.id == token_id))
        ).scalar_one_or_none()
        return _token_row_to_entity(row) if row is not None else None

    async def revoke(self, *, token_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(ScimTokenRow).where(
                ScimTokenRow.id == token_id,
                ScimTokenRow.tenant_id == tenant_id,
                ScimTokenRow.revoked_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        row.revoked_at = datetime.now(UTC)
        await self._session.commit()
        return True

    async def rotate(
        self,
        *,
        old_token_id: uuid.UUID,
        tenant_id: uuid.UUID,
        new_token_id: uuid.UUID,
        new_token_hash: str,
        new_name: str,
    ) -> ScimToken | None:
        result = await self._session.execute(
            select(ScimTokenRow).where(
                ScimTokenRow.id == old_token_id,
                ScimTokenRow.tenant_id == tenant_id,
                ScimTokenRow.revoked_at.is_(None),
            )
        )
        old_row = result.scalar_one_or_none()
        if old_row is None:
            return None

        now = datetime.now(UTC)
        old_row.revoked_at = now
        new_row = ScimTokenRow(
            id=new_token_id,
            tenant_id=tenant_id,
            name=new_name,
            token_hash=new_token_hash,
            created_by_user_id=old_row.created_by_user_id,
        )
        self._session.add(new_row)
        # Safety rule: revoke-old + insert-new in ONE transaction (mirrors
        # RotateKeyUseCase's atomic-rotation shape).
        await self._session.commit()
        await self._session.refresh(new_row)
        return _token_row_to_entity(new_row)


class SqlAlchemyScimUserRepository:
    """Implements the ScimUserRepository port."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_user(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, email: str, password_hash: str
    ) -> User:
        """Insert a new UserRow. Role is ALWAYS member (never from any payload — M3)."""
        row = UserRow(
            id=user_id,
            tenant_id=tenant_id,
            email=email,
            password_hash=password_hash,
            role=Role.MEMBER,
            auth_method="scim",
        )
        try:
            self._session.add(row)
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ScimUniquenessError from exc
        await self._session.refresh(row)
        return _user_row_to_entity(row)

    async def get_by_id(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> User | None:
        row = (
            await self._session.execute(
                select(UserRow).where(UserRow.id == user_id, UserRow.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        return _user_row_to_entity(row) if row is not None else None

    async def list_users(
        self,
        *,
        tenant_id: uuid.UUID,
        username_filter: str | None,
        start_index: int,
        count: int,
    ) -> tuple[list[User], int]:
        stmt = select(UserRow).where(UserRow.tenant_id == tenant_id)
        count_stmt = select(func.count()).select_from(UserRow).where(UserRow.tenant_id == tenant_id)
        if username_filter is not None:
            stmt = stmt.where(UserRow.email == username_filter)
            count_stmt = count_stmt.where(UserRow.email == username_filter)

        total = (await self._session.execute(count_stmt)).scalar_one()
        # SCIM start_index is 1-indexed.
        offset = max(0, start_index - 1)
        rows = (
            (
                await self._session.execute(
                    stmt.order_by(UserRow.created_at).limit(count).offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return [_user_row_to_entity(r) for r in rows], int(total)

    async def update_email(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, email: str
    ) -> User | None:
        row = (
            await self._session.execute(
                select(UserRow).where(UserRow.id == user_id, UserRow.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        try:
            row.email = email
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ScimUniquenessError from exc
        await self._session.refresh(row)
        return _user_row_to_entity(row)

    async def set_active(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, active: bool
    ) -> tuple[User, bool] | None:
        row = (
            await self._session.execute(
                select(UserRow).where(UserRow.id == user_id, UserRow.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        already_at_target = (row.deactivated_at is None) == active
        if already_at_target:
            # Idempotent no-op (M5): same target state, no write, no team_members touch —
            # the `changed=False` return lets the router skip the audit write so a
            # repeated PATCH never misrepresents a second deactivation as new state.
            return _user_row_to_entity(row), False

        row.deactivated_at = None if active else datetime.now(UTC)
        if not active:
            # Safety rule (TASK §5): deactivation write + team_members delete-by-user_id
            # commit in ONE atomic transaction — a partial failure must never leave a
            # user deactivated-but-still-team-attributed or vice versa. M8: api_keys is
            # NEVER touched here — no user-attribution column exists to cascade from.
            await self._session.execute(
                delete(TeamMemberRow).where(TeamMemberRow.user_id == user_id)
            )
        await self._session.commit()
        await self._session.refresh(row)
        return _user_row_to_entity(row), True
