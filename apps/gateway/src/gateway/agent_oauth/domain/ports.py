"""Domain ports (Protocol) for the agent OAuth device-grant store."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from gateway.agent_oauth.domain.entities import (
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
