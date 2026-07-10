"""Application use cases for SCIM user provisioning (/scim/v2/Users)."""

from __future__ import annotations

import uuid

from gateway.scim.domain.errors import ScimUserNotFoundError
from gateway.scim.domain.ports import ScimUserRepository
from gateway.scim.infrastructure.repository import SCIM_PASSWORD_HASH_SENTINEL
from gateway.tenants.domain.entities import User


class CreateScimUserUseCase:
    def __init__(self, repo: ScimUserRepository) -> None:
        self._repo = repo

    async def execute(self, *, tenant_id: uuid.UUID, email: str) -> User:
        """Create a UserRow scoped to tenant_id. Role is ALWAYS member (M3 — never read
        from any caller-supplied payload field, enforced by never accepting a role
        parameter here at all). Raises ScimUniquenessError (from the repo) if the email
        already exists in ANY tenant."""
        return await self._repo.create_user(
            user_id=uuid.uuid4(),
            tenant_id=tenant_id,
            email=email,
            password_hash=SCIM_PASSWORD_HASH_SENTINEL,
        )


class GetScimUserUseCase:
    def __init__(self, repo: ScimUserRepository) -> None:
        self._repo = repo

    async def execute(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
        user = await self._repo.get_by_id(user_id=user_id, tenant_id=tenant_id)
        if user is None:
            raise ScimUserNotFoundError
        return user


class ListScimUsersUseCase:
    def __init__(self, repo: ScimUserRepository) -> None:
        self._repo = repo

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        username_filter: str | None,
        start_index: int,
        count: int,
    ) -> tuple[list[User], int]:
        return await self._repo.list_users(
            tenant_id=tenant_id,
            username_filter=username_filter,
            start_index=start_index,
            count=count,
        )


class UpdateScimUserEmailUseCase:
    def __init__(self, repo: ScimUserRepository) -> None:
        self._repo = repo

    async def execute(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, email: str) -> User:
        updated = await self._repo.update_email(user_id=user_id, tenant_id=tenant_id, email=email)
        if updated is None:
            raise ScimUserNotFoundError
        return updated


class SetScimUserActiveUseCase:
    """Backs PATCH active:false (deactivate, M6/M7), PATCH active:true (reactivate, M5),
    and DELETE /scim/v2/Users/{id} (M6 — an ALIAS for active:false, never a hard delete).
    """

    def __init__(self, repo: ScimUserRepository) -> None:
        self._repo = repo

    async def execute(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, active: bool
    ) -> tuple[User, bool]:
        """Returns (user, changed). changed=False on an idempotent repeat (M5) — callers
        (the router's audit-write decision) must never represent a no-op repeat as a
        fresh deactivation/reactivation event."""
        result = await self._repo.set_active(user_id=user_id, tenant_id=tenant_id, active=active)
        if result is None:
            raise ScimUserNotFoundError
        return result
