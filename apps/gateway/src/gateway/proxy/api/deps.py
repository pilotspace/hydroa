"""Dependency providers for the proxy API endpoints.

CRITICAL: completion_upstream and usage_recorder are resolved from
request.app.state per-request so tests can inject fakes via app.state
without affecting the shared singleton adapters.

The circuit breaker (app.state.circuit_breaker) is a stable per-app-instance
CircuitBreaker; each request gets a BoundCircuitBreakerUpstream that wraps
whatever delegate is currently on app.state.completion_upstream. This lets
tests inject FakeCompletionUpstream while the real breaker still counts
consecutive 5xx returns.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.ports import CompletionUpstream, UsageRecorder
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.circuit_breaker_proxy import BoundCircuitBreakerUpstream
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker

# Singleton stateless hasher — safe to share
_hasher = Sha256SecretHasher()


def get_completion_upstream(request: Request) -> CompletionUpstream:
    """Build a per-request BoundCircuitBreakerUpstream.

    The CircuitBreaker lives on app.state (stable, per-app-instance).
    The delegate (inner upstream) is also from app.state so tests can inject
    FakeCompletionUpstream freely.
    """
    breaker: CircuitBreaker = request.app.state.circuit_breaker
    delegate: CompletionUpstream = request.app.state.completion_upstream
    return BoundCircuitBreakerUpstream(breaker, delegate)


def get_usage_recorder(request: Request) -> UsageRecorder:
    """Resolve UsageRecorder from app.state — allows test injection."""
    recorder: UsageRecorder = request.app.state.usage_recorder
    return recorder


def get_raw_api_key(request: Request) -> str | None:
    """Extract the raw Bearer token from Authorization header, or None."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return None


def get_completion_use_case(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CompletionUseCase:
    """Build CompletionUseCase with session-scoped adapters."""
    repo = SqlAlchemyApiKeyRepository(session)
    authz_use_case = AuthzUseCase(repo, _hasher)
    authenticator = SqlAlchemyKeyAuthenticator(authz_use_case)
    model_checker = SqlAlchemyModelChecker(session)
    budget_guard = request.app.state.budget_guard
    return CompletionUseCase(authenticator, model_checker, budget_guard)
