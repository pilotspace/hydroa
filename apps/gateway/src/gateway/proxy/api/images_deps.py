"""Dependency providers for the images API endpoint.

Mirrors the pattern from proxy/api/embeddings_deps.py without modifying it.

Contract FROZEN @ images-endpoint (TASK.md §3 IMAGES USE CASE FLOW).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.api.embeddings_deps import get_provider_registry as get_provider_registry
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.application.images_use_case import ImagesUseCase
from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker
from gateway.proxy.infrastructure.tier_capacity_guard import PassthroughTierCapacityGuard

# Singleton stateless hasher — safe to share across requests
_hasher = Sha256SecretHasher()


def get_images_use_case(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImagesUseCase:
    """Build ImagesUseCase with session-scoped adapters.

    Constructs per-request (same pattern as get_embeddings_use_case in embeddings_deps.py):
      repo → authz_use_case → authenticator → model_checker
      budget_guard + rate_limiter + redis_client resolved from app.state
      NonChatGovernance wraps all five collaborators
      ImagesUseCase wraps governance + session
    """
    from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
    from gateway.proxy.infrastructure.composite_key_authenticator import CompositeKeyAuthenticator
    from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator

    repo = SqlAlchemyApiKeyRepository(session)
    authz_use_case = AuthzUseCase(repo, _hasher)
    # agent-token-authn-seam §3: wrap so /v1/images accepts both sk- keys and agent tokens.
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
    # credits-ledger TASK.md §3: same app.state-boot singleton pattern as deps.py's
    # get_completion_use_case. Absent ⇒ PassthroughCreditGuard ⇒ byte-identical.
    from gateway.credits.domain.ports import PassthroughCreditGuard

    credit_guard = getattr(request.app.state, "credit_guard", None) or PassthroughCreditGuard()
    hold_estimate_usd = _settings.credits_hold_estimate_usd if _settings else Decimal("0.50")
    # service-tiers TASK.md §3 (FROZEN @ v1): same app.state-boot singleton pattern as
    # credit_guard above. Absent ⇒ PassthroughTierCapacityGuard ⇒ byte-identical.
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
        # residency-policy TASK.md §3 (FROZEN @ v2): app.state-boot singleton, same
        # getattr pattern as tenant_credential_resolver below. None ⇒ byte-identical.
        residency_lookup=getattr(request.app.state, "residency_lookup", None),
        tier_capacity_guard=tier_capacity_guard,
    )
    # credential-resolution-seam §3: per-tenant provider key resolver from app.state.
    tenant_credential_resolver = getattr(request.app.state, "tenant_credential_resolver", None)
    # preset-resolution-ingress (v56): reuse the SAME `authenticator` instance already
    # wrapped into `governance` above (no second KeyAuthenticator construction) plus the
    # per-tenant preset store singleton from app.state. Absent ⇒ None ⇒ byte-identical.
    tenant_model_preset_store = getattr(request.app.state, "tenant_model_preset_store", None)
    return ImagesUseCase(
        governance=governance,
        session=session,
        tenant_credential_resolver=tenant_credential_resolver,
        authenticator=authenticator,
        tenant_model_preset_store=tenant_model_preset_store,
    )


__all__ = ["get_images_use_case", "get_provider_registry"]
