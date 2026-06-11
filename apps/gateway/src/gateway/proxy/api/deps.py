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
from gateway.proxy.infrastructure.guardrail_evaluator import RegexGuardrailEvaluator
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker
from gateway.proxy.infrastructure.response_cache import RedisResponseCache

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
    rate_limiter = getattr(request.app.state, "rate_limiter", None)
    redis_client = getattr(request.app.state, "redis_client", None)
    response_cache = RedisResponseCache(redis_client) if redis_client is not None else None
    # PINNED override seam (guardrails-core §3 CONTRACT):
    # Read app.state.guardrail_evaluator first (tests inject ErrorGuardrailEvaluator this way),
    # else construct the default RegexGuardrailEvaluator — same app.state pattern as
    # completion_upstream. This allows S13/S14 to inject a failing evaluator without
    # modifying the frozen test suite.
    guardrail_evaluator = getattr(request.app.state, "guardrail_evaluator", None)
    if guardrail_evaluator is None:
        guardrail_evaluator = RegexGuardrailEvaluator()
    # OTel span emitter seam — only wired when otel_enabled=True in settings.
    # When otel_enabled=False (default): span_emitter is always None regardless of
    # what may be set on app.state. This enforces the §3 CONTRACT inviolable:
    # "otel_enabled=False → zero spans, zero behavior change."
    # Tests that need span capture must use a settings fixture with otel_enabled=True.
    _settings = getattr(request.app.state, "settings", None)
    _otel_enabled: bool = getattr(_settings, "otel_enabled", False) if _settings else False
    span_emitter = getattr(request.app.state, "span_emitter", None) if _otel_enabled else None
    return CompletionUseCase(
        authenticator,
        model_checker,
        budget_guard,
        rate_limiter,
        response_cache,
        guardrail_evaluator,
        span_emitter,
    )
