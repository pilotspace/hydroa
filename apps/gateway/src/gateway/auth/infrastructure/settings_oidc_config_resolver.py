"""Settings-based OidcConfigResolver adapter (env-fallback wrapper).

Wraps the existing GATEWAY_OIDC_* env-var Settings config as an OidcProviderConfig.
Used as the FALLBACK when no DB row matches the requested domain.

This adapter is not directly injected via app.state.oidc_config_resolver
(which is always the DbOidcConfigResolver in production).
It is used internally by the login/callback handlers to build the env-config
OidcProviderConfig object when the DB resolver returns None.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.auth.domain.entities import OidcProviderConfig
    from gateway.core.config import Settings

# Sentinel UUID used when operating in env-config mode (no DB row).
# This is a fixed well-known value — not a real tenant UUID.
ENV_CONFIG_SENTINEL_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

# Cookie value sentinel for env-config path
ENV_CONFIG_COOKIE_VALUE = "env-config"


def build_env_config(settings: Settings) -> OidcProviderConfig | None:
    """Build an OidcProviderConfig from env Settings, or None if not configured.

    Returns None when settings.oidc_enabled is False.
    """
    from gateway.auth.domain.entities import OidcProviderConfig

    if not settings.oidc_enabled:
        return None

    return OidcProviderConfig(
        tenant_id=ENV_CONFIG_SENTINEL_TENANT_ID,
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        authorize_url=settings.oidc_authorize_url,
        token_url=f"{settings.oidc_issuer}/token",
        jwks_url=settings.oidc_jwks_url,
        email_domains=[],  # env-config uses domain_mapping instead
        enabled=True,
    )
