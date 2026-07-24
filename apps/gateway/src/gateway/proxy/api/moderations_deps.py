"""Dependency providers for the moderations API endpoint.

Mirrors the pattern from proxy/api/embeddings_deps.py without modifying it.

Contract FROZEN @ moderations-endpoint (PLAN.md §3).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.application.moderations_use_case import ModerationsUseCase
from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker
from gateway.proxy.infrastructure.tier_capacity_guard import PassthroughTierCapacityGuard

# Singleton stateless hasher — safe to share across requests
_hasher = Sha256SecretHasher()


def get_ml_moderation_provider(request: Request) -> Any:
    """Resolve app.state.ml_moderation_provider — allows test injection.

    None ⇒ platform never boot-configured ML-moderation infra ⇒ the use-case raises
    ERR_PROVIDER_UNAVAILABLE (503, M6) — never a fabricated 200.
    """
    return getattr(request.app.state, "ml_moderation_provider", None)


def get_moderations_use_case(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModerationsUseCase:
    """Build ModerationsUseCase with session-scoped adapters.

    Same DI shape as embeddings_deps.py::get_embeddings_use_case (repo -> authz_use_case
    -> authenticator -> model_checker; budget_guard + rate_limiter + redis_client from
    app.state; NonChatGovernance wraps them). Deliberately does NOT resolve a guardrail
    evaluator (M9) — this endpoint never routes its own input through
    evaluate_nonchat_request_guardrails.
    """
    from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
    from gateway.proxy.infrastructure.composite_key_authenticator import CompositeKeyAuthenticator
    from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator

    repo = SqlAlchemyApiKeyRepository(session)
    authz_use_case = AuthzUseCase(repo, _hasher)
    _settings = getattr(request.app.state, "settings", None)
    authenticator = CompositeKeyAuthenticator(
        api_key_authenticator=SqlAlchemyKeyAuthenticator(authz_use_case),
        agent_token_repo=SqlAlchemyAgentOAuthRepository(session),
        hasher=_hasher,
        settings=_settings,
    )
    model_checker = SqlAlchemyModelChecker(session)
    budget_guard = request.app.state.budget_guard
    rate_limiter = getattr(request.app.state, "rate_limiter", None)
    redis_client = getattr(budget_guard, "_redis", None)
    from gateway.credits.domain.ports import PassthroughCreditGuard

    credit_guard = getattr(request.app.state, "credit_guard", None) or PassthroughCreditGuard()
    from decimal import Decimal

    hold_estimate_usd = _settings.credits_hold_estimate_usd if _settings else Decimal("0.50")

    residency_lookup = getattr(request.app.state, "residency_lookup", None)
    tier_capacity_guard = getattr(request.app.state, "tier_capacity_guard", None) or (
        PassthroughTierCapacityGuard()
    )
    governance = NonChatGovernance(
        authenticator=authenticator,
        model_checker=model_checker,
        budget_guard=budget_guard,
        rate_limiter=rate_limiter,
        redis_client=redis_client,
        session_factory=request.app.state.sessionmaker,
        credit_guard=credit_guard,
        hold_estimate_usd=hold_estimate_usd,
        residency_lookup=residency_lookup,
        tier_capacity_guard=tier_capacity_guard,
    )
    tenant_credential_resolver = getattr(request.app.state, "tenant_credential_resolver", None)
    ml_moderation_provider = get_ml_moderation_provider(request)
    return ModerationsUseCase(
        governance=governance,
        session=session,
        ml_moderation_provider=ml_moderation_provider,
        tenant_credential_resolver=tenant_credential_resolver,
        platform_credential_fallback=getattr(
            request.app.state, "platform_credential_fallback", None
        ),
    )
