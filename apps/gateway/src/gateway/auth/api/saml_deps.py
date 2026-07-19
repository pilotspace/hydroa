"""Dependency wiring for the SAML auth API.

Mirrors auth/api/deps.py's OIDC wiring seams:
  - app.state.saml_config_resolver / saml_request_store / saml_replay_cache
    are the test-injection seams (tests inject fakes; production constructs
    the real adapters here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from gateway.auth.application.saml_use_cases import SamlAcsUseCase, SamlLoginInitUseCase
    from gateway.auth.domain.saml_ports import SamlConfigResolver, SamlReplayCache, SamlRequestStore


def get_saml_config_resolver(request: Request, session: AsyncSession) -> SamlConfigResolver:
    """Resolve SamlConfigResolver — test seam via app.state.saml_config_resolver."""
    injected: SamlConfigResolver | None = getattr(request.app.state, "saml_config_resolver", None)
    if injected is not None:
        return injected

    from gateway.auth.infrastructure.db_saml_config_resolver import DbSamlConfigResolver

    return DbSamlConfigResolver(session=session, settings=request.app.state.settings)


def get_saml_request_store(request: Request) -> SamlRequestStore:
    """Resolve SamlRequestStore — test seam via app.state.saml_request_store."""
    injected: SamlRequestStore | None = getattr(request.app.state, "saml_request_store", None)
    if injected is not None:
        return injected

    from gateway.auth.infrastructure.saml_request_store import RedisSamlRequestStore

    return RedisSamlRequestStore(redis=request.app.state.redis_client)


def get_saml_replay_cache(request: Request) -> SamlReplayCache:
    """Resolve SamlReplayCache — test seam via app.state.saml_replay_cache."""
    injected: SamlReplayCache | None = getattr(request.app.state, "saml_replay_cache", None)
    if injected is not None:
        return injected

    from gateway.auth.infrastructure.saml_replay_cache import RedisSamlReplayCache

    return RedisSamlReplayCache(redis=request.app.state.redis_client)


def get_saml_login_init_use_case(request: Request, session: AsyncSession) -> SamlLoginInitUseCase:
    from gateway.auth.application.saml_use_cases import SamlLoginInitUseCase
    from gateway.domain_capture.infrastructure.repository import (
        SqlAlchemyDomainClaimRepository,
    )

    return SamlLoginInitUseCase(
        config_resolver=get_saml_config_resolver(request, session),
        request_store=get_saml_request_store(request),
        # domain-routing-unification §3 M1/M3: login-init routes the tenant via
        # the verified tenant_domain_claims source of truth (the one predicate).
        claim_resolver=SqlAlchemyDomainClaimRepository(session),
    )


def get_saml_acs_use_case(request: Request, session: AsyncSession) -> SamlAcsUseCase:
    from gateway.auth.application.saml_use_cases import SamlAcsUseCase
    from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository

    return SamlAcsUseCase(
        config_resolver=get_saml_config_resolver(request, session),
        request_store=get_saml_request_store(request),
        replay_cache=get_saml_replay_cache(request),
        repository=SqlAlchemyIdentityRepository(session),
        tokens=request.app.state.token_service,
        settings=request.app.state.settings,
        session_factory=request.app.state.sessionmaker,
    )
