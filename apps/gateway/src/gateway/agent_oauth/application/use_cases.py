"""Application service for agent OAuth — owns secret generation + hashing.

Generation lives in ONE place so plaintext secrets never leak past this layer: the
service generates a CSPRNG secret, hands the repo only its SHA-256 hash, and returns
the plaintext to the caller exactly once (Minted* value objects).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from gateway.agent_oauth.domain.entities import MintedAgentToken, MintedDeviceCodes
from gateway.agent_oauth.domain.ports import AgentOAuthRepository
from gateway.keys.domain.ports import SecretHasher

# RFC 8628 §6.1: a user_code from an unambiguous alphabet (no vowels → no accidental words,
# no 0/O/1/I/L confusion). 8 chars rendered as "XXXX-XXXX" for easy human entry.
_USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
_USER_CODE_LEN = 8


def _generate_user_code() -> str:
    chars = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(_USER_CODE_LEN))
    return f"{chars[:4]}-{chars[4:]}"


class AgentOAuthService:
    def __init__(self, *, repo: AgentOAuthRepository, hasher: SecretHasher) -> None:
        self._repo = repo
        self._hasher = hasher

    async def start_device_authorization(
        self, *, scope: str, interval_seconds: int, ttl_seconds: int, now: datetime
    ) -> MintedDeviceCodes:
        """Generate a device_code + user_code, persist their hashes, return plaintext once."""
        device_code = secrets.token_urlsafe(32)
        user_code = _generate_user_code()
        expires_at = now + timedelta(seconds=ttl_seconds)
        authorization = await self._repo.create_pending(
            device_code_hash=self._hasher.hash(device_code),
            user_code_hash=self._hasher.hash(user_code),
            scope=scope,
            interval_seconds=interval_seconds,
            expires_at=expires_at,
        )
        return MintedDeviceCodes(
            device_code=device_code, user_code=user_code, authorization=authorization
        )

    async def mint(
        self,
        *,
        authorization_id: uuid.UUID,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int | None,
        now: datetime,
    ) -> MintedAgentToken:
        """Mint an agent token from an approved authorization; plaintext returned once."""
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32) if refresh_ttl_seconds is not None else None
        access_expires_at = now + timedelta(seconds=access_ttl_seconds)
        refresh_expires_at = (
            now + timedelta(seconds=refresh_ttl_seconds)
            if refresh_ttl_seconds is not None
            else None
        )
        token = await self._repo.mint_token(
            authorization_id=authorization_id,
            access_token_hash=self._hasher.hash(access_token),
            refresh_token_hash=self._hasher.hash(refresh_token) if refresh_token else None,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
            now=now,
        )
        return MintedAgentToken(access_token=access_token, refresh_token=refresh_token, token=token)


class NotPreviewableError(Exception):
    """The grant is not in a previewable state (pending AND non-expired).

    Raised as ONE uniform outcome for EVERY non-pending / expired / unknown case
    (device-activate-page §3 M7, R-B): preview must not be a validity oracle over the
    short user_code space, so unknown / expired / approved / denied / consumed all map
    here and the router returns one byte-identical 404. Carries no distinguishing detail.
    """


@dataclass(frozen=True, slots=True)
class DeviceAuthorizationPreview:
    """Server-known facts about a PENDING grant, shown to the human before approve/deny.

    Only facts the server actually knows and can vouch for: scope, seconds-to-expiry,
    the poll interval, and the SYSTEM default budget cap that will apply to a minted
    token (agent_oauth_default_budget_usd — NOT an agent-specific budget; the data model
    carries no per-agent identity/budget on a pending grant). No client-declared string
    is ever surfaced (consent-phishing surface — device-activate-page §1 A1).
    """

    scope: str
    expires_in: int
    interval: int
    default_budget_usd: str  # 2dp string, a system default cap (not agent-specific)


class PreviewDeviceAuthorizationUseCase:
    """Read-only, non-leaky peek at a PENDING device-authorization grant.

    Reuses ``repository.get_by_user_code_hash`` (returns the row regardless of status) and
    decides previewability HERE: facts are returned ONLY when status == 'pending' AND the
    grant has not expired; every other state raises the single ``NotPreviewableError`` so
    no distinction leaks. The failure direction is CLOSED (any non-pending → uniform error).
    """

    def __init__(
        self, *, repo: AgentOAuthRepository, hasher: SecretHasher, default_budget_usd: Decimal
    ) -> None:
        self._repo = repo
        self._hasher = hasher
        self._default_budget_usd = default_budget_usd

    async def execute(
        self, *, user_code_normalized: str, now: datetime
    ) -> DeviceAuthorizationPreview:
        authorization = await self._repo.get_by_user_code_hash(
            self._hasher.hash(user_code_normalized)
        )
        if authorization is None or authorization.status != "pending":
            raise NotPreviewableError()
        expires_in = int((authorization.expires_at - now).total_seconds())
        if expires_in <= 0:
            # Pending but past expiry — indistinguishable from every other non-pending state.
            raise NotPreviewableError()
        return DeviceAuthorizationPreview(
            scope=authorization.scope,
            expires_in=expires_in,
            interval=authorization.interval_seconds,
            default_budget_usd=f"{self._default_budget_usd:.2f}",
        )
