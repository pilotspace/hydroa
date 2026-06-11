"""Dependency wiring for the OIDC auth API.

Mirrors the guardrail_evaluator override seam in proxy/api/deps.py:
  - Read app.state.oidc_exchanger first (tests inject FakeOidcExchanger)
  - Else construct the production HttpxOidcExchanger
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.application.use_cases import OidcLoginUseCase
from gateway.auth.domain.ports import OidcTokenExchanger


def get_oidc_exchanger(request: Request) -> OidcTokenExchanger:
    """Resolve OidcTokenExchanger — test override seam via app.state.oidc_exchanger."""
    exchanger: OidcTokenExchanger | None = getattr(request.app.state, "oidc_exchanger", None)
    if exchanger is None:
        from gateway.auth.infrastructure.httpx_oidc_exchanger import HttpxOidcExchanger

        exchanger = HttpxOidcExchanger(settings=request.app.state.settings)
    return exchanger


def get_oidc_use_case(request: Request, session: AsyncSession) -> OidcLoginUseCase:
    """Build OidcLoginUseCase with per-request dependencies."""
    from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository

    exchanger = get_oidc_exchanger(request)
    repository = SqlAlchemyIdentityRepository(session)
    tokens = request.app.state.token_service
    settings = request.app.state.settings

    return OidcLoginUseCase(
        exchanger=exchanger,
        repository=repository,
        tokens=tokens,
        settings=settings,
    )
