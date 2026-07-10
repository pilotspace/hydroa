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

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.infrastructure.orm import ModelRow
from gateway.core.db import get_session
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.ports import CompletionUpstream, UsageRecorder, VectorCache
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.circuit_breaker_proxy import BoundCircuitBreakerUpstream
from gateway.proxy.infrastructure.composite_key_authenticator import CompositeKeyAuthenticator
from gateway.proxy.infrastructure.guardrail_evaluator import RegexGuardrailEvaluator
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.proxy.infrastructure.ml_moderation_evaluator import (
    CompositeGuardrailEvaluator,
    MlModerationGuardrailEvaluator,
)
from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker
from gateway.proxy.infrastructure.provider_registry import select_provider
from gateway.proxy.infrastructure.response_cache import RedisResponseCache
from gateway.proxy.infrastructure.vector_cache import RedisVectorCache

# Singleton stateless hasher — safe to share
_hasher = Sha256SecretHasher()


def build_embedding_adapter(
    *,
    session_factory: Callable[[], Any],
    registry: Any,
    embed_model: str,
) -> Callable[[str], Awaitable[list[float] | None]]:
    """Build the embedder the vector cache uses to vectorize prompts (semantic-cache v19).

    Routes the configured embed model through the gateway's own embedding upstream. Uses its OWN
    short-lived session (via session_factory) — NOT the request session — so the fire-and-forget
    store path (which runs AFTER the response, when the request session is closed) works reliably.
    Returns None on any miss/empty-shape; exceptions propagate to RedisVectorCache's fail-safe wrap.
    The embedding call is INTERNAL — never billed to the served request.
    """

    async def _embed(text: str) -> list[float] | None:
        async with session_factory() as session:
            stmt = select(ModelRow.modality, ModelRow.provider).where(
                ModelRow.id == embed_model,
                ModelRow.active.is_(True),
            )
            row = (await session.execute(stmt)).one_or_none()
        if row is None:
            return None
        adapter = select_provider(row.modality, row.provider, registry)
        status, resp = await adapter.post_json("/embeddings", {"model": embed_model, "input": text})
        if status != 200:
            return None
        data = resp.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            return None
        emb = data[0].get("embedding")
        return emb if isinstance(emb, list) else None

    return _embed


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
    # agent-token-authn-seam §3: wrap in CompositeKeyAuthenticator so /v1/chat
    # accepts both sk- API keys (delegated, byte-identical) and minted agent tokens.
    _settings = getattr(request.app.state, "settings", None)
    from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository

    authenticator = CompositeKeyAuthenticator(
        api_key_authenticator=SqlAlchemyKeyAuthenticator(authz_use_case),
        agent_token_repo=SqlAlchemyAgentOAuthRepository(session),
        hasher=_hasher,
        settings=_settings,
    )
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
        regex_evaluator = RegexGuardrailEvaluator()
        # ml-moderation-layer §3 CONTRACT (FROZEN @ v1, M9): additive-only wiring. The
        # composite is constructed ONLY when app.state.ml_moderation_provider is
        # present (boot-wired conditionally in main.py); absent it, this branch stays
        # byte-identical RegexGuardrailEvaluator() — zero behavior change.
        ml_provider = getattr(request.app.state, "ml_moderation_provider", None)
        if ml_provider is not None:
            guardrail_evaluator = CompositeGuardrailEvaluator(
                regex_evaluator,
                MlModerationGuardrailEvaluator(
                    ml_provider,
                    getattr(request.app.state, "tenant_credential_resolver", None),
                ),
            )
        else:
            guardrail_evaluator = regex_evaluator
    # OTel span emitter seam — only wired when otel_enabled=True in settings.
    # When otel_enabled=False (default): span_emitter is always None regardless of
    # what may be set on app.state. This enforces the §3 CONTRACT inviolable:
    # "otel_enabled=False → zero spans, zero behavior change."
    # Tests that need span capture must use a settings fixture with otel_enabled=True.
    _otel_enabled: bool = getattr(_settings, "otel_enabled", False) if _settings else False
    span_emitter = getattr(request.app.state, "span_emitter", None) if _otel_enabled else None
    # Pre-first-byte streaming resilience flag (streaming-resilience v19) — default-off.
    stream_resilience_enabled: bool = getattr(request.app.state, "stream_resilience_enabled", False)
    # Embedding-similarity "vector" cache (semantic-cache v19) — default-off.
    # Wired only when enabled AND redis AND a non-empty embed model AND a provider registry are
    # present; otherwise None ⇒ the complete() path is byte-identical to today. The embedder routes
    # the configured embed model through the gateway's own embedding upstream; its cost is internal
    # (never billed). Any embedder/redis failure is contained by RedisVectorCache (→ MISS / no-op).
    vector_cache: VectorCache | None = None
    _vc_enabled: bool = getattr(_settings, "vector_cache_enabled", False) if _settings else False
    _embed_model: str = getattr(_settings, "vector_cache_embed_model", "") if _settings else ""
    _registry = getattr(request.app.state, "provider_registry", None)
    _session_factory = getattr(request.app.state, "sessionmaker", None)
    if (
        _vc_enabled
        and redis_client is not None
        and _embed_model
        and _registry is not None
        and _session_factory is not None
    ):
        embedder = build_embedding_adapter(
            session_factory=_session_factory,
            registry=_registry,
            embed_model=_embed_model,
        )
        vector_cache = RedisVectorCache(
            redis_client,
            embedder=embedder,
            threshold=float(getattr(_settings, "vector_cache_threshold", 0.95)),
            max_candidates=int(getattr(_settings, "vector_cache_max_candidates", 100)),
        )
    # Credential resolution seam (credential-resolution-seam §3).
    # Resolve per-tenant provider credential from app.state — tests override via
    # app.state.tenant_credential_resolver = FakeResolver().
    tenant_credential_resolver = getattr(request.app.state, "tenant_credential_resolver", None)
    provider_resolver = getattr(request.app.state, "provider_resolver", None)
    # preset-resolution-ingress (v56): resolve a per-tenant `<preset>:<alias>` model
    # selector at ingress. Absent / None ⇒ feature off ⇒ byte-identical (mirrors the
    # tenant_credential_resolver getattr pattern immediately above — same stable,
    # app.state-boot singleton shape; DbTenantModelPresetStore opens its own sessions).
    tenant_model_preset_store = getattr(request.app.state, "tenant_model_preset_store", None)
    # openrouter-cost-recovery-wiring (v30 t6.2c): optional inline recovery service.
    # Absent / None ⇒ feature off ⇒ byte-identical (tests override via app.state).
    cost_recovery = getattr(request.app.state, "cost_recovery_service", None)
    # bandwidth-pacing (v36): per-key throughput bucket. Absent / None ⇒ the use-case
    # defaults to PassthroughBandwidthBucket ⇒ byte-identical (tests override via app.state).
    bandwidth_bucket = getattr(request.app.state, "bandwidth_bucket", None)
    bandwidth_max_wait_s: float = (
        float(getattr(_settings, "bandwidth_max_wait_seconds", 0.0)) if _settings else 0.0
    )
    # web-search-grounding (v41): when GATEWAY_WEB_SEARCH_ENABLED is on, the use-case
    # KEEPS the client's web_search flag so adapters can inject native grounding; when
    # off (default) it strips the flag centrally → byte-identical to today. Wiring the
    # knob here is what makes the feature reachable on the real request path.
    web_search_enabled: bool = (
        bool(getattr(_settings, "web_search_enabled", False)) if _settings else False
    )
    # unsupported-input-guard (v55): lightweight per-request lookup over the catalog
    # models table. Built from the same session as the model checker (zero extra connections).
    # When the flag is OFF (default), the use-case's guard is a no-op and the lookup is
    # never called — wiring it unconditionally keeps the DI seam clean.
    from gateway.proxy.infrastructure.input_modality_lookup import SqlAlchemyInputModalityLookup

    input_modality_lookup = SqlAlchemyInputModalityLookup(session)
    input_modality_guard_enabled: bool = (
        bool(getattr(_settings, "input_modality_guard_enabled", False)) if _settings else False
    )
    # batch-auto-grouping (v57): stable app.state-boot singleton — same getattr pattern
    # as tenant_credential_resolver above. None ⇒ feature off ⇒ byte-identical.
    batch_diversion = getattr(request.app.state, "batch_diversion", None)
    return CompletionUseCase(
        authenticator,
        model_checker,
        budget_guard,
        rate_limiter,
        response_cache,
        guardrail_evaluator,
        span_emitter,
        stream_resilience_enabled=stream_resilience_enabled,
        vector_cache=vector_cache,
        tenant_credential_resolver=tenant_credential_resolver,
        provider_resolver=provider_resolver,
        cost_recovery=cost_recovery,
        bandwidth_bucket=bandwidth_bucket,
        bandwidth_max_wait_s=bandwidth_max_wait_s,
        web_search_enabled=web_search_enabled,
        input_modality_lookup=input_modality_lookup,
        input_modality_guard_enabled=input_modality_guard_enabled,
        tenant_model_preset_store=tenant_model_preset_store,
        # chat-modality-guard (v56): reuses the SAME provider_resolver singleton fetched
        # above — zero new app.state attribute, zero new instance. None ⇒ feature off.
        chat_modality_lookup=provider_resolver,
        batch_diversion=batch_diversion,
    )
