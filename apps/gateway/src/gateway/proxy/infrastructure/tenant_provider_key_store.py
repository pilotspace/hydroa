"""Fernet-at-rest PostgreSQL implementation of TenantProviderKeyStore.

§3 CONTRACT (provider-credential-store TASK.md) — FROZEN @ v1.

SECURITY INVARIANTS:
- Fernet encryption/decryption happens only inside ``_encrypt`` / ``_decrypt``;
  plaintext is never assigned to a local variable outside those helpers.
- ``get`` raises ``ProviderCredentialError("ERR_PROVIDER_CREDENTIAL_CORRUPT")``
  **from None** on any decrypt failure so the Fernet ``InvalidToken`` chain
  (which can carry ciphertext/key material) cannot reach a crash reporter.
- ``list`` NEVER decrypts any ciphertext.
- The ORM row is NEVER serialized to JSON.
- ``from cryptography.fernet import Fernet`` is a lazy import at each call site
  (mirrors the OIDC admin router pattern; avoids a hard import at module load
  when the crypto path is inactive).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.proxy.domain.provider_credentials import (
    PROVIDER_VALUE_SET,
    AzureCredential,
    BearerCredential,
    BedrockCredential,
    ProviderCredential,
    ProviderCredentialError,
    ProviderKeyStatus,
)
from gateway.proxy.infrastructure.orm import TenantProviderKeyRow

# ---------------------------------------------------------------------------
# Internal helpers — encryption / decryption
# ---------------------------------------------------------------------------


def _fernet_key(raw_key: str) -> bytes:
    """Return the Fernet key as bytes."""
    return raw_key.encode()


def _encrypt(plaintext: str, raw_key: str) -> bytes:
    """Encrypt *plaintext* with Fernet and return ciphertext bytes."""
    from cryptography.fernet import Fernet  # lazy import — mirrors OIDC pattern

    fernet = Fernet(_fernet_key(raw_key))
    return fernet.encrypt(plaintext.encode())


def _decrypt(ciphertext: bytes, raw_key: str) -> str:
    """Decrypt *ciphertext* with Fernet and return plaintext string.

    Raises ``ProviderCredentialError("ERR_PROVIDER_CREDENTIAL_CORRUPT")`` from
    None on ANY failure — the ``from None`` strips the ``InvalidToken`` chain
    so no ciphertext/key material leaks to a crash reporter (v22 secret-chain
    floor).
    """
    from cryptography.fernet import Fernet  # lazy import — mirrors OIDC pattern

    try:
        fernet = Fernet(_fernet_key(raw_key))
        return fernet.decrypt(ciphertext).decode()
    except Exception:
        raise ProviderCredentialError("ERR_PROVIDER_CREDENTIAL_CORRUPT") from None


# ---------------------------------------------------------------------------
# Credential ↔ (secret_plaintext, extra_json, auth_mode) split/merge
# ---------------------------------------------------------------------------


def _credential_to_parts(
    credential: ProviderCredential,
) -> tuple[str, str | None, str | None]:
    """Split a ProviderCredential into (primary_secret, extra_json, auth_mode).

    Primary secret is encrypted into ``secret_enc``.
    Extra JSON (when present) is encrypted into ``extra_enc``.
    ``auth_mode`` is the plaintext discriminator (Azure only; else None).

    Contract split per shape (§3 CONTRACT Table A):
    - Bearer  → secret=bearer_token, extra=None, auth_mode=None
    - Bedrock → secret=secret_access_key, extra={access_key_id,region,session_token},
                auth_mode=None
    - Azure   → secret=(client_secret|api_key), extra={endpoint,...},
                auth_mode=("aad"|"api_key")
    """
    if isinstance(credential, BearerCredential):
        return credential.secret.get_secret_value(), None, None

    if isinstance(credential, BedrockCredential):
        extra = {
            "access_key_id": credential.access_key_id,
            "region": credential.region,
            "session_token": (
                credential.session_token.get_secret_value()
                if credential.session_token is not None
                else None
            ),
        }
        return credential.secret_access_key.get_secret_value(), json.dumps(extra), None

    # azure — only remaining member of ProviderCredential union at this point
    # Primary secret depends on mode
    if credential.mode == "api_key":
        primary = credential.api_key.get_secret_value() if credential.api_key else ""
    else:
        primary = credential.client_secret.get_secret_value() if credential.client_secret else ""

    extra_dict = {
        "endpoint": credential.endpoint,
        "api_version": credential.api_version,
        "deployment_map": dict(credential.deployment_map),
        "tenant_id": credential.tenant_id,
        "client_id": credential.client_id,
        "scope": credential.scope,
        "authority": credential.authority,
    }
    return primary, json.dumps(extra_dict), credential.mode


def _parts_to_credential(
    provider: str,
    secret_plain: str,
    extra_plain: str | None,
    auth_mode: str | None,
) -> ProviderCredential:
    """Reconstruct a typed ProviderCredential from decrypted parts.

    Inverse of ``_credential_to_parts``.  Called only inside ``get()`` after
    both ``secret_enc`` and ``extra_enc`` have been decrypted in memory.
    """
    if provider in ("openrouter", "openai", "anthropic", "google"):
        return BearerCredential(secret=SecretStr(secret_plain))

    if provider == "bedrock":
        extra = json.loads(extra_plain) if extra_plain else {}
        raw_token: str | None = extra.get("session_token")
        return BedrockCredential(
            access_key_id=extra.get("access_key_id", ""),
            secret_access_key=SecretStr(secret_plain),
            region=extra.get("region", ""),
            session_token=SecretStr(raw_token) if raw_token else None,
        )

    # azure — only remaining provider in PROVIDER_VALUE_SET at this point
    extra = json.loads(extra_plain) if extra_plain else {}
    mode = auth_mode or "api_key"
    deployment_map: dict[str, str] = extra.get("deployment_map") or {}
    if mode == "api_key":
        return AzureCredential(
            mode="api_key",
            endpoint=extra.get("endpoint", ""),
            api_version=extra.get("api_version", ""),
            deployment_map=deployment_map,
            api_key=SecretStr(secret_plain),
            scope=extra.get("scope", ""),
            authority=extra.get("authority", ""),
        )
    return AzureCredential(
        mode="aad",
        endpoint=extra.get("endpoint", ""),
        api_version=extra.get("api_version", ""),
        deployment_map=deployment_map,
        tenant_id=extra.get("tenant_id"),
        client_id=extra.get("client_id"),
        client_secret=SecretStr(secret_plain),
        scope=extra.get("scope", ""),
        authority=extra.get("authority", ""),
    )


# ---------------------------------------------------------------------------
# DbTenantProviderKeyStore
# ---------------------------------------------------------------------------


class DbTenantProviderKeyStore:
    """Fernet-at-rest PostgreSQL store implementing ``TenantProviderKeyStore``.

    Args:
        sessionmaker: An ``async_sessionmaker[AsyncSession]`` bound to the
            application database pool.
        settings: Application settings that must expose
            ``provider_key_encryption_key: str``.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Any,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings

    # ------------------------------------------------------------------
    # upsert
    # ------------------------------------------------------------------

    async def upsert(
        self,
        tenant_id: UUID,
        provider: str,
        credential: ProviderCredential,
        *,
        enabled: bool = True,
    ) -> None:
        """Encrypt and persist the credential in a single ON CONFLICT transaction.

        Guards (before any IO):
        1. ``provider`` not in value-set → ``ERR_PROVIDER_UNKNOWN``
        2. ``provider_key_encryption_key`` falsy → ``ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE``
        """
        # Guard 1: unknown provider — no row I/O
        if provider not in PROVIDER_VALUE_SET:
            raise ProviderCredentialError("ERR_PROVIDER_UNKNOWN")

        # Guard 2: encryption key missing — no row I/O
        enc_key: str = self._settings.provider_key_encryption_key or ""
        if not enc_key:
            raise ProviderCredentialError("ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE")

        # Split the credential into its encrypted parts
        secret_plain, extra_plain, auth_mode = _credential_to_parts(credential)
        secret_enc: bytes = _encrypt(secret_plain, enc_key)
        extra_enc: bytes | None = (
            _encrypt(extra_plain, enc_key) if extra_plain is not None else None
        )

        stmt = (
            pg_insert(TenantProviderKeyRow)
            .values(
                tenant_id=tenant_id,
                provider=provider,
                secret_enc=secret_enc,
                extra_enc=extra_enc,
                auth_mode=auth_mode,
                enabled=enabled,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "provider"],
                set_={
                    "secret_enc": secret_enc,
                    "extra_enc": extra_enc,
                    "auth_mode": auth_mode,
                    "enabled": enabled,
                    "updated_at": text("now()"),
                },
            )
        )

        async with self._sessionmaker() as session:
            await session.execute(stmt)
            await session.commit()

    # ------------------------------------------------------------------
    # get
    # ------------------------------------------------------------------

    async def get(
        self,
        tenant_id: UUID,
        provider: str,
    ) -> ProviderCredential | None:
        """Load, decrypt, and return the credential, or None if absent/disabled.

        Raises ``ProviderCredentialError("ERR_PROVIDER_CREDENTIAL_CORRUPT")``
        **from None** on any decryption failure (v22 secret-chain floor).
        """
        enc_key: str = self._settings.provider_key_encryption_key or ""
        if not enc_key:
            raise ProviderCredentialError("ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE")

        stmt = select(TenantProviderKeyRow).where(
            TenantProviderKeyRow.tenant_id == tenant_id,
            TenantProviderKeyRow.provider == provider,
        )

        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            row: TenantProviderKeyRow | None = result.scalar_one_or_none()

        if row is None:
            return None
        if not row.enabled:
            return None

        # Decrypt primary secret — raises ERR_PROVIDER_CREDENTIAL_CORRUPT from None on failure
        secret_plain = _decrypt(row.secret_enc, enc_key)

        # Decrypt extra JSON if present
        extra_plain: str | None = None
        if row.extra_enc is not None:
            extra_plain = _decrypt(row.extra_enc, enc_key)

        return _parts_to_credential(provider, secret_plain, extra_plain, row.auth_mode)

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    async def list(
        self,
        tenant_id: UUID,
    ) -> list[ProviderKeyStatus]:
        """Return per-provider status WITHOUT decrypting any ciphertext.

        Only reads (provider, enabled, auth_mode, updated_at) — never secret_enc
        or extra_enc.
        """
        stmt = select(
            TenantProviderKeyRow.provider,
            TenantProviderKeyRow.enabled,
            TenantProviderKeyRow.auth_mode,
            TenantProviderKeyRow.updated_at,
        ).where(TenantProviderKeyRow.tenant_id == tenant_id)

        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            rows = result.all()

        statuses: list[ProviderKeyStatus] = []
        for row in rows:
            provider_val, enabled_val, auth_mode_val, updated_at_val = row
            statuses.append(
                ProviderKeyStatus(
                    provider=provider_val,  # type: ignore[arg-type]
                    configured=True,
                    enabled=enabled_val,
                    auth_mode=auth_mode_val,
                    updated_at=updated_at_val,
                )
            )
        return statuses

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------

    async def delete(
        self,
        tenant_id: UUID,
        provider: str,
    ) -> bool:
        """Delete the row and return True iff a row existed."""
        stmt = (
            delete(TenantProviderKeyRow)
            .where(
                TenantProviderKeyRow.tenant_id == tenant_id,
                TenantProviderKeyRow.provider == provider,
            )
            .returning(TenantProviderKeyRow.provider)
        )

        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            await session.commit()
            return result.fetchone() is not None
