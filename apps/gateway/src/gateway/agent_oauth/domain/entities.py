"""Domain entities for agent OAuth (device authorization grant) — zero framework imports.

Mirrors `keys/domain/entities.py`: a secret is NEVER stored on an entity, only its hash
lives in the DB row; the plaintext is carried on a Minted* value object returned ONCE.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DeviceAuthorization:
    """A pending/approved/denied/consumed device-authorization request (RFC 8628)."""

    id: uuid.UUID
    status: str  # 'pending' | 'approved' | 'denied' | 'consumed'
    scope: str
    interval_seconds: int
    created_at: datetime
    expires_at: datetime
    tenant_id: uuid.UUID | None = None  # bound only at approval
    user_id: uuid.UUID | None = None  # bound only at approval
    approved_at: datetime | None = None
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AgentToken:
    """An issued agent token row (hashes never carried here)."""

    id: uuid.UUID
    authorization_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    scope: str
    created_at: datetime
    access_expires_at: datetime
    refresh_expires_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AgentTokenBinding:
    """The authz resolve result — what the data plane needs to scope a request."""

    token_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    scope: str


@dataclass(frozen=True, slots=True)
class MintedDeviceCodes:
    """Returned ONCE from start_device_authorization — plaintext codes + the row."""

    device_code: str
    user_code: str
    authorization: DeviceAuthorization


@dataclass(frozen=True, slots=True)
class MintedAgentToken:
    """Returned ONCE from mint — plaintext token(s) + the persisted token row."""

    access_token: str
    refresh_token: str | None
    token: AgentToken
