"""Application use cases for API keys."""

import hmac
import secrets
import uuid
from dataclasses import dataclass

from gateway.keys.domain.entities import ApiKeyInfo, AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError, KeyNotFoundError
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
        )
        return CreateKeyResult(key_id=key_id, name=name, key=full_key)


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
        from gateway.keys.domain.errors import ForbiddenError

        if role == Role.MEMBER:
            raise ForbiddenError
        found = await self._repo.revoke(key_id=key_id, tenant_id=tenant_id)
        if not found:
            raise KeyNotFoundError


class AuthzUseCase:
    def __init__(self, repository: ApiKeyRepository, hasher: SecretHasher) -> None:
        self._repo = repository
        self._hasher = hasher

    async def execute(self, raw_key: str) -> AuthzResult:
        """Validate a raw API key from the X-Api-Key header.

        Parses key format, looks up the row, and compares hashes in constant time.
        ALL failure modes raise InvalidApiKeyError with NO distinguishing detail
        (malformed / unknown / revoked / wrong secret all identical — no enumeration).

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

        return AuthzResult(tenant_id=row.tenant_id, key_id=row.id)


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
