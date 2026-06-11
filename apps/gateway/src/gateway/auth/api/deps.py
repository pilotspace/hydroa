"""Dependency wiring for the OIDC auth API.

Mirrors the guardrail_evaluator override seam in proxy/api/deps.py:
  - Read app.state.oidc_exchanger first (tests inject FakeOidcExchanger)
  - Else construct the production HttpxOidcExchanger

For JWKS verification (v5):
  - app.state.jwks_client is the test injection seam (presence ACTIVATES verification).
  - If not set, construct HttpxJwksClient from settings.oidc_jwks_url when non-empty.
  - If oidc_jwks_url is empty AND no seam: returns None → v4 TLS-channel mode.

For per-tenant config (oidc-tenant-config v6):
  - app.state.oidc_config_resolver is the test injection seam.
  - Production wires DbOidcConfigResolver (session-scoped).
  - oidc_config is resolved at the router layer and passed to get_oidc_use_case_with_config.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.application.use_cases import OidcLoginUseCase
from gateway.auth.domain.ports import OidcTokenExchanger

if TYPE_CHECKING:
    from gateway.auth.application.jwks_key_cache import JwksKeyCache
    from gateway.auth.domain.entities import OidcProviderConfig
    from gateway.auth.domain.ports import JwksClient


def get_oidc_exchanger(request: Request) -> OidcTokenExchanger:
    """Resolve OidcTokenExchanger — test override seam via app.state.oidc_exchanger."""
    exchanger: OidcTokenExchanger | None = getattr(request.app.state, "oidc_exchanger", None)
    if exchanger is None:
        from gateway.auth.infrastructure.httpx_oidc_exchanger import HttpxOidcExchanger

        exchanger = HttpxOidcExchanger(settings=request.app.state.settings)
    return exchanger


def get_jwks_client(
    request: Request, oidc_config: OidcProviderConfig | None = None
) -> JwksClient | None:
    """Resolve JwksClient — test injection seam via app.state.jwks_client.

    Resolution order (§3 CONTRACT):
      1. app.state.jwks_client if set → use directly (tests / manual injection).
      2. Else if per-tenant oidc_config.jwks_url non-empty → construct HttpxJwksClient.
      3. Else if settings.oidc_jwks_url non-empty → construct HttpxJwksClient.
      4. Else → None (v4 TLS-channel mode; skip WARNING already emitted in use case).
    """
    injected: JwksClient | None = getattr(request.app.state, "jwks_client", None)
    if injected is not None:
        return injected

    settings = request.app.state.settings

    # Per-tenant config takes precedence over env settings
    if oidc_config is not None and oidc_config.jwks_url:
        from gateway.auth.infrastructure.httpx_jwks_client import HttpxJwksClient

        return HttpxJwksClient(jwks_url=oidc_config.jwks_url)

    if settings.oidc_jwks_url:
        from gateway.auth.infrastructure.httpx_jwks_client import HttpxJwksClient

        return HttpxJwksClient(jwks_url=settings.oidc_jwks_url)

    return None


def get_oidc_use_case(request: Request, session: AsyncSession) -> OidcLoginUseCase:
    """Build OidcLoginUseCase with per-request dependencies (env-path / legacy).

    Used by the env-fallback path where no per-tenant config has been resolved.
    """
    return get_oidc_use_case_with_config(request, session, oidc_config=None)


def get_oidc_use_case_with_config(
    request: Request,
    session: AsyncSession,
    *,
    oidc_config: OidcProviderConfig | None,
) -> OidcLoginUseCase:
    """Build OidcLoginUseCase with a resolved per-tenant config (or None for env path).

    When oidc_config is not None, the use case validates against the per-tenant
    issuer/client_id and uses the per-tenant jwks_url for JWKS key resolution.
    """
    from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository

    exchanger = get_oidc_exchanger(request)
    jwks_client = get_jwks_client(request, oidc_config=oidc_config)
    jwks_key_cache: JwksKeyCache | None = getattr(request.app.state, "jwks_key_cache", None)
    repository = SqlAlchemyIdentityRepository(session)
    tokens = request.app.state.token_service
    settings = request.app.state.settings

    return OidcLoginUseCase(
        exchanger=exchanger,
        repository=repository,
        tokens=tokens,
        settings=settings,
        jwks_client=jwks_client,
        jwks_key_cache=jwks_key_cache,
        oidc_config=oidc_config,
    )
