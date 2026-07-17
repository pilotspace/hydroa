"""Domain ports (Protocol interfaces) for SCIM provisioning."""

import uuid
from typing import Protocol

from gateway.scim.domain.entities import ScimToken
from gateway.tenants.domain.entities import User


class ScimTokenRepository(Protocol):
    async def create(
        self,
        *,
        token_id: uuid.UUID,
        tenant_id: uuid.UUID,
        name: str,
        token_hash: str,
        created_by_user_id: uuid.UUID | None,
    ) -> ScimToken:
        """Insert a new scim_tokens row; token_id is pre-generated (no column default)."""
        ...

    async def list_by_tenant(self, tenant_id: uuid.UUID) -> list[ScimToken]:
        """Return all tokens for a tenant ordered by created_at DESC."""
        ...

    async def get_by_id(self, token_id: uuid.UUID) -> ScimToken | None:
        """Fetch a token row by id — no tenant filter (authn path owns scoping)."""
        ...

    async def revoke(self, *, token_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """Soft-revoke by setting revoked_at = now().

        Returns True on success, False when no matching LIVE row (unknown id, already
        revoked, or cross-tenant — all indistinguishable, no leak).
        """
        ...

    async def rotate(
        self,
        *,
        old_token_id: uuid.UUID,
        tenant_id: uuid.UUID,
        new_token_id: uuid.UUID,
        new_token_hash: str,
        new_name: str,
    ) -> ScimToken | None:
        """Atomically revoke old_token_id + insert a new row in ONE transaction.

        Returns the new ScimToken on success. Returns None when the old token is not
        found, already revoked, or belongs to another tenant.
        """
        ...


class ScimUserRepository(Protocol):
    async def create_user(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        email: str,
        password_hash: str,
    ) -> User:
        """Insert a new UserRow (role=MEMBER, auth_method='scim').

        Raises ScimUniquenessError if the (globally-unique) email already exists in ANY
        tenant — SCIM Create is spec-non-idempotent, unlike OIDC's get-or-create.
        """
        ...

    async def get_by_id(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> User | None:
        """Fetch a user by id, tenant-scoped in the SAME query as existence (defense in
        depth). Returns None for unknown id OR cross-tenant id — indistinguishable."""
        ...

    async def list_users(
        self,
        *,
        tenant_id: uuid.UUID,
        username_filter: str | None,
        start_index: int,
        count: int,
    ) -> tuple[list[User], int]:
        """Return (page of users, total matching count) for tenant_id, optionally
        filtered by exact userName (email) match. 1-indexed start_index (SCIM convention).
        """
        ...

    async def update_email(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        email: str,
    ) -> User | None:
        """Update email; tenant-scoped. Returns None for unknown/cross-tenant id."""
        ...

    async def set_active(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        active: bool,
    ) -> tuple[User, bool] | None:
        """Set/clear deactivated_at, tenant-scoped. When deactivating (active=False), the
        write is committed in the SAME transaction as deleting every team_members row for
        this user (Safety rule, TASK §5) — a partial failure must never leave a user
        deactivated-but-still-team-attributed or vice versa. Idempotent: repeating the
        same target state is a no-op (no write, no team_members touch) that still returns
        the unchanged row, never an error — the returned bool is False so callers (the
        audit-writing router) never misrepresent a no-op repeat as a fresh transition.
        Returns None for unknown/cross-tenant id.
        """
        ...

    async def lock_and_get_billing_owner_user_id(self, *, tenant_id: uuid.UUID) -> uuid.UUID | None:
        """``SELECT billing_owner_user_id FROM tenants WHERE id=:t FOR UPDATE``
        (billing-owner-of-record TASK.md §3 M4) — the SAME-shaped lock as
        ``UserRoleRepository.lock_and_get_billing_owner_user_id``, held in the SAME
        transaction as ``set_active``'s own target-user lock so HOOK 2 (deactivation)
        serializes against HOOK 1 (role-change) and the reassignment endpoint's own write
        (closes the R9 race).
        """
        ...
