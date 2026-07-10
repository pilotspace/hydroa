"""Domain entities for SCIM 2.0 provisioning — zero framework imports."""

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ScimToken:
    """A per-tenant bearer credential authorizing unattended write access to that
    tenant's user lifecycle via /scim/v2/* (scim-provisioning TASK.md §3 Part A).

    token_hash is intentionally NOT exposed past the infrastructure boundary — only the
    plaintext token (returned once, at creation/rotation) and this row's metadata are
    domain-visible, mirroring the api_keys / invites precedent.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    token_hash: str
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    revoked_at: datetime | None
