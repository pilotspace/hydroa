"""Domain ports (Protocol) for the agent OAuth device-grant store."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from gateway.agent_oauth.domain.entities import (
    AgentPrincipal,
    AgentToken,
    AgentTokenBinding,
    DeviceAuthorization,
)


class AgentOAuthRepository(Protocol):
    """Persistence port for device authorizations + issued agent tokens.

    The repo receives ALREADY-HASHED secrets (the application service owns
    generation + hashing) and an injected `now` clock (no hidden time).
    """

    async def create_pending(
        self,
        *,
        device_code_hash: str,
        user_code_hash: str,
        scope: str,
        interval_seconds: int,
        expires_at: datetime,
    ) -> DeviceAuthorization:
        """Insert a pending authorization. Raises DeviceCodeCollisionError on collision."""
        ...

    async def get_by_device_code_hash(self, device_code_hash: str) -> DeviceAuthorization | None:
        """Token-poll lookup; None when unknown."""
        ...

    async def get_by_user_code_hash(self, user_code_hash: str) -> DeviceAuthorization | None:
        """Approval lookup; None when unknown."""
        ...

    async def approve(
        self,
        *,
        authorization_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        now: datetime,
    ) -> DeviceAuthorization:
        """pending -> approved, bound to (tenant_id, user_id).

        Raises AuthorizationNotPendingError | AuthorizationExpiredError.
        """
        ...

    async def deny(self, *, authorization_id: uuid.UUID) -> DeviceAuthorization:
        """pending -> denied. Raises AuthorizationNotPendingError."""
        ...

    async def mint_token(
        self,
        *,
        authorization_id: uuid.UUID,
        access_token_hash: str,
        refresh_token_hash: str | None,
        access_expires_at: datetime,
        refresh_expires_at: datetime | None,
        now: datetime,
    ) -> AgentToken:
        """ATOMIC: INSERT agent_tokens + mark the authorization consumed, in ONE tx.

        The token inherits the authorization's scope. Raises
        AuthorizationNotApprovedError | AuthorizationAlreadyConsumedError.
        """
        ...

    async def resolve_access_token(
        self, *, access_token_hash: str, now: datetime
    ) -> AgentTokenBinding | None:
        """Authz resolve; None when unknown / expired / revoked (FAIL-CLOSED)."""
        ...

    # -----------------------------------------------------------------------
    # agent-identity-governance TASK.md §3 (FROZEN @ v1) — agent principals
    # -----------------------------------------------------------------------

    async def create_principal(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
        owner_user_id: uuid.UUID | None,
        monthly_budget_usd: Decimal | None,
        rpm_limit: int | None,
        tpm_limit: int | None,
    ) -> AgentPrincipal:
        """Insert a new agent_principals row. Raises AgentPrincipalNameConflictError
        on a (tenant_id, name) unique violation."""
        ...

    async def list_principals(self, *, tenant_id: uuid.UUID) -> list[AgentPrincipal]:
        """List every principal for a tenant, newest-first, with attached_token_count."""
        ...

    async def kill_principal(
        self, *, principal_id: uuid.UUID, tenant_id: uuid.UUID, now: datetime
    ) -> tuple[AgentPrincipal, bool]:
        """Atomically kill a principal + bulk-revoke every attached token.

        A SINGLE conditional UPDATE agent_principals SET killed_at=now() WHERE id=:id
        AND tenant_id=:tenant_id AND killed_at IS NULL RETURNING * decides — via its
        OWN row-count, not a prior SELECT — whether THIS call is the one that fires.
        Only when it affects a row does the SAME transaction run the bulk
        UPDATE agent_tokens SET revoked_at=now() WHERE principal_id=:id AND
        revoked_at IS NULL.

        Returns (principal, just_killed): just_killed is True only for the ONE call
        that won the race (schedules the audit + owns the token bulk-revoke); False
        means the principal was ALREADY killed (idempotent 200 no-op — same killed_at,
        no re-revoke, no second audit event).

        Raises AgentPrincipalNotFoundError if principal_id is unknown or belongs to
        another tenant (byte-identical — no enumeration leak).
        """
        ...

    async def attach_token(
        self, *, principal_id: uuid.UUID, token_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        """A SINGLE conditional UPDATE agent_tokens SET principal_id=:pid WHERE
        id=:token_id AND tenant_id=:tenant_id AND principal_id IS NULL AND the
        target principal exists/is tenant-scoped/is NOT killed (same statement's
        WHERE clause) — never a separate SELECT-then-UPDATE (closes the same TOCTOU
        race mint_token's with_for_update() closes for device_authorizations).

        Raises (in priority order, once 0 rows are updated):
            AgentPrincipalNotFoundError: principal_id unknown or cross-tenant.
            AgentTokenNotFoundError: token_id unknown or cross-tenant.
            AgentPrincipalKilledError: principal exists but is already killed.
            AgentTokenAlreadyAttachedError: token exists but already attached.
        """
        ...

    async def detach_token(
        self, *, principal_id: uuid.UUID, token_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        """A SINGLE conditional UPDATE agent_tokens SET principal_id=NULL WHERE
        id=:token_id AND tenant_id=:tenant_id AND principal_id=:principal_id.

        Raises AgentPrincipalNotFoundError (principal unknown/cross-tenant) or
        AgentTokenNotFoundError (token unknown/cross-tenant/not attached to this
        principal — byte-identical, no leak of the token's TRUE attachment state).
        """
        ...
