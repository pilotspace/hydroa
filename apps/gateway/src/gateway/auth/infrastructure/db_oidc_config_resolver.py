"""DB-backed OidcConfigResolver adapter.

Queries oidc_provider_configs WHERE email_domains @> ARRAY[domain] AND enabled=TRUE.
Decrypts client_secret_enc in-memory using Fernet key from Settings.

SECURITY INVARIANTS:
- client_secret is decrypted ONLY in resolve() and carried only in the returned
  OidcProviderConfig object (in-memory, never serialized).
- Fernet key from Settings.oidc_config_encryption_key; absent key → None returned
  (resolver returns None to signal env-fallback; writes fail at the admin layer).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from gateway.auth.domain.entities import OidcProviderConfig
    from gateway.core.config import Settings

logger = logging.getLogger(__name__)


class DbOidcConfigResolver:
    """Resolves per-tenant OIDC config from oidc_provider_configs table.

    Injected into the OIDC router via app.state.oidc_config_resolver.
    Tests inject FakeOidcConfigResolver instead.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def resolve(self, domain: str | None) -> OidcProviderConfig | None:
        """Return per-tenant OidcProviderConfig for the given email domain.

        Returns None when:
        - domain is None (no domain param supplied)
        - no enabled row matches the domain
        - Fernet key is absent (cannot decrypt; falls back to env path)

        The OidcConfigResolver protocol contract: None → try env Settings fallback.
        """
        from gateway.auth.domain.entities import OidcProviderConfig
        from gateway.auth.infrastructure.orm import OidcProviderConfigRow

        if domain is None:
            return None

        # Query: email_domains @> ARRAY[domain] AND enabled = TRUE.
        # ORDER BY created_at, tenant_id + LIMIT 1 (domain-routing-unification
        # TASK.md §3 M6 — FROZEN @ v1): pre-existing legacy data may hold more
        # than one enabled row for the same domain (the write-time gate could
        # never create this again); a bare .scalar_one_or_none() would raise an
        # unhandled MultipleResultsFound (login-crashing DoS). The earliest
        # claimant wins deterministically — mirrors DbSamlConfigResolver's own
        # already-hardened resolve() shape exactly.
        stmt = (
            select(OidcProviderConfigRow)
            .where(
                OidcProviderConfigRow.email_domains.contains([domain]),
                OidcProviderConfigRow.enabled.is_(True),
            )
            .order_by(
                OidcProviderConfigRow.created_at.asc(),
                OidcProviderConfigRow.tenant_id.asc(),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row: OidcProviderConfigRow | None = result.scalar_one_or_none()

        if row is None:
            return None

        # Decrypt client_secret — requires Fernet key
        enc_key = self._settings.oidc_config_encryption_key
        if not enc_key:
            # No key → cannot decrypt → return None (env-fallback signal)
            logger.warning(
                "oidc_provider_configs row found for domain %r but "
                "GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY is not set; "
                "falling back to env config",
                domain,
            )
            return None

        try:
            from cryptography.fernet import Fernet

            fernet = Fernet(enc_key.encode() if isinstance(enc_key, str) else enc_key)  # pyright: ignore[reportUnnecessaryIsInstance]  — defensive isinstance retained for runtime safety on the crypto path
            client_secret = fernet.decrypt(row.client_secret_enc).decode()
        except Exception as exc:
            logger.error(
                "Failed to decrypt client_secret_enc for domain %r: %s",
                domain,
                type(exc).__name__,  # never log the ciphertext or plaintext
            )
            return None

        return OidcProviderConfig(
            tenant_id=row.tenant_id,
            issuer=row.issuer,
            client_id=row.client_id,
            client_secret=client_secret,  # PLAINTEXT — in-memory only
            authorize_url=row.authorize_url,
            token_url=row.token_url,
            jwks_url=row.jwks_url,
            email_domains=list(row.email_domains or []),
            enabled=row.enabled,
        )

    async def resolve_by_tenant_id(self, tenant_id: str) -> OidcProviderConfig | None:
        """Resolve config by tenant_id hex string (used at /callback via oidc_tenant_id cookie).

        Returns None if no enabled row found or decryption fails.
        """
        import uuid as _uuid

        from gateway.auth.domain.entities import OidcProviderConfig
        from gateway.auth.infrastructure.orm import OidcProviderConfigRow

        try:
            tid = _uuid.UUID(tenant_id)
        except (ValueError, AttributeError):
            return None

        from sqlalchemy import select

        stmt = select(OidcProviderConfigRow).where(
            OidcProviderConfigRow.tenant_id == tid,
            OidcProviderConfigRow.enabled.is_(True),
        )
        result = await self._session.execute(stmt)
        row: OidcProviderConfigRow | None = result.scalar_one_or_none()

        if row is None:
            return None

        enc_key = self._settings.oidc_config_encryption_key
        if not enc_key:
            return None

        try:
            from cryptography.fernet import Fernet

            fernet = Fernet(enc_key.encode() if isinstance(enc_key, str) else enc_key)  # pyright: ignore[reportUnnecessaryIsInstance]  — defensive isinstance retained for runtime safety on the crypto path
            client_secret = fernet.decrypt(row.client_secret_enc).decode()
        except Exception as exc:
            logger.error(
                "Failed to decrypt client_secret_enc for tenant_id %r: %s",
                tenant_id,
                type(exc).__name__,
            )
            return None

        return OidcProviderConfig(
            tenant_id=row.tenant_id,
            issuer=row.issuer,
            client_id=row.client_id,
            client_secret=client_secret,
            authorize_url=row.authorize_url,
            token_url=row.token_url,
            jwks_url=row.jwks_url,
            email_domains=list(row.email_domains or []),
            enabled=row.enabled,
        )
