"""Dependency providers for the audio API endpoints (STT + TTS).

Mirrors the pattern from proxy/api/embeddings_deps.py without modifying it.

Contract FROZEN @ audio-endpoints (TASK.md §3 DEPS FLOW).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.application.audio_use_case import SpeechUseCase, TranscriptionUseCase
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

# Singleton stateless hasher — safe to share across requests
_hasher = Sha256SecretHasher()


def get_provider_registry(request: Request) -> ProviderRegistry:
    """Resolve ProviderRegistry from app.state — allows test injection."""
    registry: ProviderRegistry = request.app.state.provider_registry
    return registry


def get_transcription_use_case(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TranscriptionUseCase:
    """Build TranscriptionUseCase with session-scoped adapters.

    Constructs per-request (same pattern as get_embeddings_use_case in embeddings_deps.py):
      repo → authz_use_case → authenticator → model_checker
      budget_guard + rate_limiter + redis_client resolved from app.state
      NonChatGovernance wraps all five collaborators
      TranscriptionUseCase wraps governance + session
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
    # credential-resolution-seam §3: per-tenant provider key resolver from app.state.
    tenant_credential_resolver = getattr(request.app.state, "tenant_credential_resolver", None)
    return TranscriptionUseCase(
        governance=governance,
        session=session,
        tenant_credential_resolver=tenant_credential_resolver,
        # stt-duration-cap §3: bound the billed per_second duration (GATEWAY_STT_MAX_DURATION_SECONDS).
        max_duration_seconds=request.app.state.settings.stt_max_duration_seconds,
    )


def get_speech_use_case(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpeechUseCase:
    """Build SpeechUseCase with session-scoped adapters.

    Identical construction pattern to get_transcription_use_case; builds SpeechUseCase instead.
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
    # credential-resolution-seam §3: per-tenant provider key resolver from app.state.
    tenant_credential_resolver = getattr(request.app.state, "tenant_credential_resolver", None)
    return SpeechUseCase(
        governance=governance,
        session=session,
        tenant_credential_resolver=tenant_credential_resolver,
    )
