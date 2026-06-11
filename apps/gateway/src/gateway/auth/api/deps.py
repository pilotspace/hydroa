"""Dependency wiring for the OIDC auth API.

Mirrors the guardrail_evaluator override seam in proxy/api/deps.py:
  - Read app.state.oidc_exchanger first (tests inject FakeOidcExchanger)
  - Else construct the production HttpxOidcExchanger

For JWKS verification (v5):
  - app.state.jwks_client is the test injection seam (presence ACTIVATES verification).
  - If not set, construct HttpxJwksClient from settings.oidc_jwks_url when non-empty.
  - If oidc_jwks_url is empty AND no seam: returns None → v4 TLS-channel mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.application.use_cases import OidcLoginUseCase
from gateway.auth.domain.ports import OidcTokenExchanger

if TYPE_CHECKING:
    from gateway.auth.application.jwks_key_cache import JwksKeyCache
    from gateway.auth.domain.ports import JwksClient


def get_oidc_exchanger(request: Request) -> OidcTokenExchanger:
    """Resolve OidcTokenExchanger — test override seam via app.state.oidc_exchanger."""
    exchanger: OidcTokenExchanger | None = getattr(request.app.state, "oidc_exchanger", None)
    if exchanger is None:
        from gateway.auth.infrastructure.httpx_oidc_exchanger import HttpxOidcExchanger

        exchanger = HttpxOidcExchanger(settings=request.app.state.settings)
    return exchanger


def get_jwks_client(request: Request) -> JwksClient | None:
    """Resolve JwksClient — test injection seam via app.state.jwks_client.

    Resolution order (§3 CONTRACT):
      1. app.state.jwks_client if set → use directly (tests / manual injection).
      2. Else if settings.oidc_jwks_url non-empty → construct HttpxJwksClient.
      3. Else → None (v4 TLS-channel mode; skip WARNING already emitted in use case).
    """
    injected: JwksClient | None = getattr(request.app.state, "jwks_client", None)
    if injected is not None:
        return injected

    settings = request.app.state.settings
    if settings.oidc_jwks_url:
        from gateway.auth.infrastructure.httpx_jwks_client import HttpxJwksClient

        return HttpxJwksClient(jwks_url=settings.oidc_jwks_url)

    return None


def get_oidc_use_case(request: Request, session: AsyncSession) -> OidcLoginUseCase:
    """Build OidcLoginUseCase with per-request dependencies."""
    from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository

    exchanger = get_oidc_exchanger(request)
    jwks_client = get_jwks_client(request)
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
    )
