"""Application use cases for SCIM token management (/admin/scim/tokens, RFC 9457)."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from gateway.scim.domain.entities import ScimToken
from gateway.scim.domain.errors import ScimInvalidTokenError, ScimTokenNotFoundError
from gateway.scim.domain.ports import ScimTokenRepository

# Token format: scim-<token_id_hex>.<urlsafe_b64_secret> — mirrors keys' sk-<hex>.<secret>.
_TOKEN_PREFIX = "scim-"  # noqa: S105 — a format prefix, not a credential
_TOKEN_SEPARATOR = "."  # noqa: S105 — a delimiter, not a credential


@dataclass(frozen=True, slots=True)
class CreateScimTokenResult:
    """Result of a successful token creation — plaintext token shown exactly once."""

    id: uuid.UUID
    name: str
    token: str  # full plaintext "scim-<hex>.<secret>" — MUST NOT be persisted
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RotateScimTokenResult:
    """Result of a successful token rotation — new plaintext token shown exactly once."""

    id: uuid.UUID
    name: str
    token: str
    created_at: datetime
    superseded_token_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ScimIdentity:
    """Resolved from a validated SCIM bearer token — the (tenant_id, scim_token_id) pair
    every /scim/v2/* route authorizes against."""

    tenant_id: uuid.UUID
    scim_token_id: uuid.UUID


class SecretHasher:
    """Structural protocol satisfied by Sha256SecretHasher (reused unmodified, M1)."""


class CreateScimTokenUseCase:
    def __init__(self, repo: ScimTokenRepository, hasher: object) -> None:
        self._repo = repo
        self._hasher = hasher

    async def execute(
        self, *, tenant_id: uuid.UUID, name: str, created_by_user_id: uuid.UUID | None
    ) -> CreateScimTokenResult:
        token_id = uuid.uuid4()
        secret = secrets.token_urlsafe(32)
        token_hash = self._hasher.hash(secret)  # type: ignore[attr-defined]
        full_token = f"{_TOKEN_PREFIX}{token_id.hex}{_TOKEN_SEPARATOR}{secret}"

        row = await self._repo.create(
            token_id=token_id,
            tenant_id=tenant_id,
            name=name,
            token_hash=token_hash,
            created_by_user_id=created_by_user_id,
        )
        return CreateScimTokenResult(
            id=row.id, name=row.name, token=full_token, created_at=row.created_at
        )


class ListScimTokensUseCase:
    def __init__(self, repo: ScimTokenRepository) -> None:
        self._repo = repo

    async def execute(self, *, tenant_id: uuid.UUID) -> list[ScimToken]:
        return await self._repo.list_by_tenant(tenant_id)


class RevokeScimTokenUseCase:
    def __init__(self, repo: ScimTokenRepository) -> None:
        self._repo = repo

    async def execute(self, *, token_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        found = await self._repo.revoke(token_id=token_id, tenant_id=tenant_id)
        if not found:
            raise ScimTokenNotFoundError


class RotateScimTokenUseCase:
    def __init__(self, repo: ScimTokenRepository, hasher: object) -> None:
        self._repo = repo
        self._hasher = hasher

    async def execute(
        self, *, old_token_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> RotateScimTokenResult:
        old = await self._repo.get_by_id(old_token_id)
        if old is None or old.revoked_at is not None or old.tenant_id != tenant_id:
            raise ScimTokenNotFoundError

        new_token_id = uuid.uuid4()
        secret = secrets.token_urlsafe(32)
        new_token_hash = self._hasher.hash(secret)  # type: ignore[attr-defined]
        full_token = f"{_TOKEN_PREFIX}{new_token_id.hex}{_TOKEN_SEPARATOR}{secret}"

        new_row = await self._repo.rotate(
            old_token_id=old_token_id,
            tenant_id=tenant_id,
            new_token_id=new_token_id,
            new_token_hash=new_token_hash,
            new_name=old.name,
        )
        if new_row is None:
            raise ScimTokenNotFoundError

        return RotateScimTokenResult(
            id=new_row.id,
            name=new_row.name,
            token=full_token,
            created_at=new_row.created_at,
            superseded_token_id=old_token_id,
        )


class AuthenticateScimUseCase:
    """Resolves a raw `Authorization: Bearer scim-...` value to a ScimIdentity.

    ALL failure modes (missing/malformed/unknown/revoked token) raise the SAME
    ScimInvalidTokenError with no distinguishing detail — mirrors AuthzUseCase's own
    constant-shape failure contract (no enumeration oracle).
    """

    def __init__(self, repo: ScimTokenRepository, hasher: object) -> None:
        self._repo = repo
        self._hasher = hasher

    async def execute(self, raw_token: str) -> ScimIdentity:
        token_id, secret = _parse_token(raw_token)
        row = await self._repo.get_by_id(token_id)

        # Constant-shape comparison: always run hash compare against a dummy hash when
        # the row is missing/revoked, so the code path cost is equivalent (mirrors
        # AuthzUseCase's own anti-timing-oracle pattern).
        stored_hash = row.token_hash if (row is not None and row.revoked_at is None) else ""
        candidate_hash = self._hasher.hash(secret)  # type: ignore[attr-defined]
        match_ok = self._hasher.verify(stored_hash, secret) if stored_hash else False  # type: ignore[attr-defined]
        _ = candidate_hash  # computed unconditionally for timing parity

        if row is None or row.revoked_at is not None or not match_ok:
            raise ScimInvalidTokenError

        return ScimIdentity(tenant_id=row.tenant_id, scim_token_id=row.id)


def _parse_token(raw_token: str) -> tuple[uuid.UUID, str]:
    """Parse "scim-<token_id_hex>.<secret>" → (token_id, secret).

    Raises ScimInvalidTokenError on any parsing failure — no detail exposed.
    """
    if not raw_token.startswith(_TOKEN_PREFIX):
        raise ScimInvalidTokenError
    rest = raw_token[len(_TOKEN_PREFIX) :]
    dot_idx = rest.find(_TOKEN_SEPARATOR)
    if dot_idx < 0:
        raise ScimInvalidTokenError
    token_id_hex = rest[:dot_idx]
    secret = rest[dot_idx + 1 :]
    if not secret:
        raise ScimInvalidTokenError
    try:
        token_id = uuid.UUID(hex=token_id_hex)
    except ValueError:
        raise ScimInvalidTokenError from None
    return token_id, secret
