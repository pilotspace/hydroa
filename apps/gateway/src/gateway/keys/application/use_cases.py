"""Application use cases for API keys."""

import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from gateway.keys.domain.entities import ApiKey, ApiKeyInfo, AuthzResult
from gateway.keys.domain.errors import ForbiddenError, InvalidApiKeyError, KeyNotFoundError
from gateway.keys.domain.ports import ApiKeyRepository, SecretHasher
from gateway.tenants.domain.entities import Role

# Key format: sk-<key_id_hex>.<urlsafe_b64_secret>
_KEY_PREFIX = "sk-"
_KEY_SEPARATOR = "."


@dataclass(frozen=True, slots=True)
class CreateKeyResult:
    """Result of a successful key creation — plaintext key shown exactly once."""

    key_id: uuid.UUID
    name: str
    key: str  # full plaintext key "sk-<hex>.<secret>" — MUST NOT be persisted
    monthly_budget_usd: Decimal | None = None
    soft_budget_usd: Decimal | None = None
    expires_at: datetime | None = None
    model_allowlist: list[str] | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    # teams-core additive field
    team_id: uuid.UUID | None = None
    # response-caching additive field
    cache_enabled: bool = False
    # service-tiers additive field (TASK.md §3, FROZEN @ v1) — raw key-level override.
    tier: str | None = None


@dataclass(frozen=True, slots=True)
class RotateKeyResult:
    """Result of a successful key rotation — new plaintext key shown exactly once."""

    new_key_id: uuid.UUID
    superseded_key_id: uuid.UUID
    key: str  # full plaintext new key — MUST NOT be persisted
    name: str
    monthly_budget_usd: Decimal | None = None
    soft_budget_usd: Decimal | None = None
    expires_at: datetime | None = None
    model_allowlist: list[str] | None = None


class CreateKeyUseCase:
    def __init__(self, repository: ApiKeyRepository, hasher: SecretHasher) -> None:
        self._repo = repository
        self._hasher = hasher

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
        key_id: uuid.UUID,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
        team_id: uuid.UUID | None = None,
        cache_enabled: bool = False,
        tier: str | None = None,
    ) -> CreateKeyResult:
        """Issue a new API key.

        key_id is caller-supplied (pre-generated in the router) so the hex
        embedding in the plaintext key is consistent with the stored row.
        Safety: secret is never logged or stored; only its SHA-256 hash persists.
        """
        secret = secrets.token_urlsafe(32)
        key_hash = self._hasher.hash(secret)
        full_key = f"{_KEY_PREFIX}{key_id.hex}{_KEY_SEPARATOR}{secret}"

        await self._repo.create(
            key_id=key_id,
            tenant_id=tenant_id,
            name=name,
            key_hash=key_hash,
            monthly_budget_usd=monthly_budget_usd,
            soft_budget_usd=soft_budget_usd,
            expires_at=expires_at,
            model_allowlist=model_allowlist,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            team_id=team_id,
            cache_enabled=cache_enabled,
            tier=tier,
        )
        return CreateKeyResult(
            key_id=key_id,
            name=name,
            key=full_key,
            monthly_budget_usd=monthly_budget_usd,
            soft_budget_usd=soft_budget_usd,
            expires_at=expires_at,
            model_allowlist=model_allowlist,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            team_id=team_id,
            cache_enabled=cache_enabled,
            tier=tier,
        )


class ListKeysUseCase:
    def __init__(self, repository: ApiKeyRepository) -> None:
        self._repo = repository

    async def execute(self, *, tenant_id: uuid.UUID) -> list[ApiKeyInfo]:
        return await self._repo.list_by_tenant(tenant_id)


class RevokeKeyUseCase:
    def __init__(self, repository: ApiKeyRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        role: Role,
    ) -> None:
        """Soft-revoke a key.

        Raises ForbiddenError if role is member.
        Raises KeyNotFoundError if key_id not found or belongs to another tenant
        (identical 404 for both cases — no cross-tenant information leak).
        """
        if role == Role.MEMBER:
            raise ForbiddenError
        found = await self._repo.revoke(key_id=key_id, tenant_id=tenant_id)
        if not found:
            raise KeyNotFoundError


class UpdateKeyUseCase:
    """Update governance fields on an active key (PATCH semantics)."""

    def __init__(self, repository: ApiKeyRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        role: Role,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
        team_id: uuid.UUID | None = None,
        cache_enabled: bool | None = None,
        capture_enabled: bool | None = None,
        tier: str | None = None,
        _fields_to_clear: set[str] | None = None,
    ) -> ApiKey:
        """Update governance fields.

        Raises ForbiddenError if role is member.
        Raises KeyNotFoundError if key not found, revoked, or cross-tenant.
        """
        if role == Role.MEMBER:
            raise ForbiddenError
        result = await self._repo.update(
            key_id=key_id,
            tenant_id=tenant_id,
            monthly_budget_usd=monthly_budget_usd,
            soft_budget_usd=soft_budget_usd,
            expires_at=expires_at,
            model_allowlist=model_allowlist,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            team_id=team_id,
            cache_enabled=cache_enabled,
            capture_enabled=capture_enabled,
            tier=tier,
            _fields_to_clear=_fields_to_clear,
        )
        if result is None:
            raise KeyNotFoundError
        return result


class RotateKeyUseCase:
    """Atomically rotate a key: revoke old, issue new in one transaction."""

    def __init__(self, repository: ApiKeyRepository, hasher: SecretHasher) -> None:
        self._repo = repository
        self._hasher = hasher

    async def execute(
        self,
        *,
        old_key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        role: Role,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
        # Sentinel flags: True = field was explicitly provided (even if None = inherit)
        _budget_provided: bool = False,
        _soft_budget_provided: bool = False,
        _expires_provided: bool = False,
        _allowlist_provided: bool = False,
    ) -> RotateKeyResult:
        """Rotate a key.

        Fields not provided in the request inherit from the old row.
        Raises ForbiddenError if role is member.
        Raises KeyNotFoundError if old key not found, revoked, or cross-tenant.
        """
        if role == Role.MEMBER:
            raise ForbiddenError

        # Fetch the old key to inherit governance fields
        old_key = await self._repo.get_by_id(old_key_id)
        if old_key is None or old_key.revoked_at is not None or old_key.tenant_id != tenant_id:
            raise KeyNotFoundError

        # Resolve governance fields: use provided values or inherit from old row
        resolved_budget = monthly_budget_usd if _budget_provided else old_key.monthly_budget_usd
        resolved_soft = soft_budget_usd if _soft_budget_provided else old_key.soft_budget_usd
        resolved_expires = expires_at if _expires_provided else old_key.expires_at
        resolved_allowlist = model_allowlist if _allowlist_provided else old_key.model_allowlist

        # Generate new key credentials
        new_key_id = uuid.uuid4()
        secret = secrets.token_urlsafe(32)
        new_key_hash = self._hasher.hash(secret)
        full_key = f"{_KEY_PREFIX}{new_key_id.hex}{_KEY_SEPARATOR}{secret}"

        # Atomically revoke old + insert new (single transaction in repository)
        new_row = await self._repo.rotate(
            old_key_id=old_key_id,
            tenant_id=tenant_id,
            new_key_id=new_key_id,
            new_key_hash=new_key_hash,
            new_name=old_key.name,
            monthly_budget_usd=resolved_budget,
            soft_budget_usd=resolved_soft,
            expires_at=resolved_expires,
            model_allowlist=resolved_allowlist,
        )
        if new_row is None:
            raise KeyNotFoundError

        return RotateKeyResult(
            new_key_id=new_key_id,
            superseded_key_id=old_key_id,
            key=full_key,
            name=old_key.name,
            monthly_budget_usd=resolved_budget,
            soft_budget_usd=resolved_soft,
            expires_at=resolved_expires,
            model_allowlist=resolved_allowlist,
        )


class AuthzUseCase:
    def __init__(self, repository: ApiKeyRepository, hasher: SecretHasher) -> None:
        self._repo = repository
        self._hasher = hasher

    async def execute(self, raw_key: str) -> AuthzResult:
        """Validate a raw API key from the X-Api-Key header.

        Parses key format, looks up the row, and compares hashes in constant time.
        ALL failure modes raise InvalidApiKeyError with NO distinguishing detail
        (malformed / unknown / revoked / wrong secret all identical — no enumeration).

        Governance fields (expires_at, model_allowlist, monthly_budget_usd,
        soft_budget_usd) are returned in the AuthzResult for hot-path enforcement
        in CompletionUseCase — zero extra DB queries (M12).

        Safety properties:
        - hmac.compare_digest used for constant-time comparison (in SecretHasher.verify)
        - Revoked keys are rejected before hash comparison (early exit, but same error)
        - Unknown key_ids return the same error as wrong-secret (no oracle)
        """
        key_id, secret = _parse_key(raw_key)

        row = await self._repo.get_by_id(key_id)

        # To prevent timing-based enumeration between "unknown key" and "wrong secret",
        # we always run the hash comparison — compare against a dummy hash if row is None
        # or revoked, so the code path cost is equivalent.
        stored_hash = row.key_hash if (row is not None and row.revoked_at is None) else ""
        candidate_hash = self._hasher.hash(secret)
        # constant-time compare
        match_ok = hmac.compare_digest(stored_hash, candidate_hash) if stored_hash else False

        if row is None or row.revoked_at is not None or not match_ok:
            raise InvalidApiKeyError

        return AuthzResult(
            tenant_id=row.tenant_id,
            key_id=row.id,
            expires_at=row.expires_at,
            model_allowlist=row.model_allowlist,
            monthly_budget_usd=row.monthly_budget_usd,
            soft_budget_usd=row.soft_budget_usd,
            rpm_limit=row.rpm_limit,
            tpm_limit=row.tpm_limit,
            team_id=row.team_id,
            team_budget_usd=row.team_budget_usd,
            cache_enabled=row.cache_enabled,
            guardrail_configs=getattr(row, "guardrail_configs", {}),
            semantic_cache_enabled=getattr(row, "semantic_cache_enabled", False),
            batch_grouping_enabled=getattr(row, "batch_grouping_enabled", False),
            policy_source=getattr(row, "guardrail_policy_source", "none"),
            zdr_enabled=getattr(row, "zdr_enabled", False),
            payload_capture_enabled=getattr(row, "capture_enabled", False),
            plan_id=getattr(row, "plan_id", None),
            plan_model_allowlist=getattr(row, "plan_model_allowlist", None),
            plan_name=getattr(row, "plan_name", None),
            tier=getattr(row, "tier", "standard"),
            tier_source=getattr(row, "tier_source", "tenant"),
            mcp_allowed_servers=getattr(row, "mcp_allowed_servers", []),
        )


def _parse_key(raw_key: str) -> tuple[uuid.UUID, str]:
    """Parse "sk-<key_id_hex>.<secret>" → (key_id, secret).

    Raises InvalidApiKeyError on any parsing failure — no detail exposed.
    """
    if not raw_key.startswith(_KEY_PREFIX):
        raise InvalidApiKeyError
    rest = raw_key[len(_KEY_PREFIX) :]
    dot_idx = rest.find(_KEY_SEPARATOR)
    if dot_idx < 0:
        raise InvalidApiKeyError
    key_id_hex = rest[:dot_idx]
    secret = rest[dot_idx + 1 :]
    if not secret:
        raise InvalidApiKeyError
    try:
        key_id = uuid.UUID(hex=key_id_hex)
    except ValueError:
        raise InvalidApiKeyError from None
    return key_id, secret
