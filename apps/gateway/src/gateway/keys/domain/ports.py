"""Domain ports (Protocol interfaces) for API keys."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from gateway.keys.domain.entities import ApiKey, ApiKeyInfo


class ApiKeyRepository(Protocol):
    async def create(
        self,
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        name: str,
        key_hash: str,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
    ) -> ApiKey:
        """Insert a new api_keys row; key_id must be pre-generated (no column default)."""
        ...

    async def list_by_tenant(self, tenant_id: uuid.UUID) -> list[ApiKeyInfo]:
        """Return all keys for a tenant ordered by created_at DESC, no secrets."""
        ...

    async def revoke(self, *, key_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """Soft-revoke by setting revoked_at = now().

        Returns True on success, False when no matching row (unknown or cross-tenant).
        Covers both unknown-id and cross-tenant cases — both return False (no leak).
        """
        ...

    async def get_by_id(self, key_id: uuid.UUID) -> ApiKey | None:
        """Fetch a key row by id for authz — no tenant filter (authz path owns scoping)."""
        ...

    async def update(
        self,
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
        _fields_to_clear: set[str] | None = None,
    ) -> ApiKey | None:
        """Update governance fields on an active key owned by tenant_id.

        Returns the updated ApiKey on success.
        Returns None when key is not found, revoked, or belongs to another tenant.
        _fields_to_clear: set of field names to set to NULL (explicit null patch).
        """
        ...

    async def rotate(
        self,
        *,
        old_key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        new_key_id: uuid.UUID,
        new_key_hash: str,
        new_name: str,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
    ) -> ApiKey | None:
        """Atomically revoke old_key and insert new key row in one transaction.

        Returns the new ApiKey on success.
        Returns None when old_key is not found, revoked, or belongs to another tenant.
        Atomicity: both revoke + insert are committed together (M5 requirement).
        """
        ...


class SecretHasher(Protocol):
    def hash(self, secret: str) -> str:
        """Return the SHA-256 hex digest of the secret."""
        ...

    def verify(self, stored_hash: str, candidate_secret: str) -> bool:
        """Compare SHA-256(candidate_secret) == stored_hash in constant time.

        Uses hmac.compare_digest to prevent timing attacks.
        MUST NOT raise; returns False on any mismatch.
        """
        ...
