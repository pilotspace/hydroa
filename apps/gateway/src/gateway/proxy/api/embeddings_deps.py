"""Dependency providers for the embeddings API endpoint.

Mirrors the pattern from proxy/api/deps.py without modifying it.

Contract FROZEN @ embeddings-endpoint (TASK.md §3 EMBEDDINGS USE CASE FLOW).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.application.embeddings_use_case import EmbeddingsUseCase
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.domain.ports import ResponseCache
from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry
from gateway.proxy.infrastructure.response_cache import RedisResponseCache

# Singleton stateless hasher — safe to share across requests
_hasher = Sha256SecretHasher()


def get_provider_registry(request: Request) -> ProviderRegistry:
    """Resolve ProviderRegistry from app.state — allows test injection."""
    registry: ProviderRegistry = request.app.state.provider_registry
    return registry


def get_response_cache(request: Request) -> ResponseCache | None:
    """Resolve the embeddings response cache from app.state (cache-controls task).

    A RedisResponseCache over the same Redis the rest of the embeddings path uses
    (app.state.redis_client, falling back to budget_guard._redis), or None when
    Redis is unwired — None ⇒ byte-identical to the pre-cache embeddings flow.
    """
    budget_guard = getattr(request.app.state, "budget_guard", None)
    redis_client = getattr(request.app.state, "redis_client", None) or getattr(
        budget_guard, "_redis", None
    )
    if redis_client is None:
        return None
    return RedisResponseCache(redis_client)


def get_embeddings_use_case(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EmbeddingsUseCase:
    """Build EmbeddingsUseCase with session-scoped adapters.

    Constructs per-request (same pattern as get_completion_use_case in deps.py):
      repo → authz_use_case → authenticator → model_checker
      budget_guard + rate_limiter + redis_client resolved from app.state
      NonChatGovernance wraps all five collaborators
      EmbeddingsUseCase wraps governance + session
    """
    from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator

    repo = SqlAlchemyApiKeyRepository(session)
    authz_use_case = AuthzUseCase(repo, _hasher)
    authenticator = SqlAlchemyKeyAuthenticator(authz_use_case)
    model_checker = SqlAlchemyModelChecker(session)
    budget_guard = request.app.state.budget_guard
    rate_limiter = getattr(request.app.state, "rate_limiter", None)
    redis_client = getattr(budget_guard, "_redis", None)

    governance = NonChatGovernance(
        authenticator=authenticator,
        model_checker=model_checker,
        budget_guard=budget_guard,
        rate_limiter=rate_limiter,
        redis_client=redis_client,
        session_factory=request.app.state.sessionmaker,
    )
    # credential-resolution-seam §3: resolve per-tenant provider key from app.state
    # (tests override app.state.tenant_credential_resolver with a fake). None ⇒ unwired.
    tenant_credential_resolver = getattr(request.app.state, "tenant_credential_resolver", None)
    return EmbeddingsUseCase(
        governance=governance,
        session=session,
        tenant_credential_resolver=tenant_credential_resolver,
    )
