"""Application service for agent OAuth — owns secret generation + hashing.

Generation lives in ONE place so plaintext secrets never leak past this layer: the
service generates a CSPRNG secret, hands the repo only its SHA-256 hash, and returns
the plaintext to the caller exactly once (Minted* value objects).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta

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
