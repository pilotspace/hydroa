"""Dependency providers for the images API endpoint.

Mirrors the pattern from proxy/api/embeddings_deps.py without modifying it.

Contract FROZEN @ images-endpoint (TASK.md §3 IMAGES USE CASE FLOW).
"""

from __future__ import annotations

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
    )
    return ImagesUseCase(governance=governance, session=session)


__all__ = ["get_images_use_case", "get_provider_registry"]
