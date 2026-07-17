"""Dependency providers for the proxy API endpoints.

CRITICAL: completion_upstream and usage_recorder are resolved from
request.app.state per-request so tests can inject fakes via app.state
without affecting the shared singleton adapters.

The circuit breaker registry (app.state.provider_circuit_breakers) is a stable
per-app-instance dict[provider, CircuitBreaker>. Each request gets a
ProviderScopedCircuitBreakerUpstream that resolves the target provider (via
app.state.provider_resolver — the SAME resolver the dispatch wrapper uses) and
counts consecutive 5xx returns against THAT provider's own breaker only, so a
provider tripping its breaker never blocks any other provider (audit-remediation
package C1 — MED proxy global breaker). app.state.circuit_breaker (the legacy
single stable CircuitBreaker) is kept for backward compatibility with callers
outside this module (e.g. the realtime websocket path) and as a fail-safe
fallback here when the per-provider registry isn't wired on app.state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
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
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.ports import (
    CompletionUpstream,
    ProviderResolver,
    UsageRecorder,
    VectorCache,
)
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
from gateway.proxy.infrastructure.tier_capacity_guard import PassthroughTierCapacityGuard
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


class ProviderScopedCircuitBreakerUpstream:
    """Per-request view: a per-provider CircuitBreaker registry + delegate.

    audit-remediation package C1 (MED proxy global breaker): the prior
    BoundCircuitBreakerUpstream wrapped EVERY provider in the SAME
    app.state.circuit_breaker instance, so 5 consecutive failures from one
    provider tripped a breaker that then blocked every OTHER provider too. This
    wrapper resolves the request's catalog provider (via the same
    ProviderResolver the ProviderAwareCompletionUpstream dispatch wrapper uses
    to select the adapter) and looks up — or lazily creates — a dedicated
    CircuitBreaker for THAT provider in `breakers` (app.state.provider_circuit_
    breakers), so a trip on provider A never blocks provider B. Consecutive-
    failure-reset semantics are unchanged (mirrors BoundCircuitBreakerUpstream
    exactly); only the breaker SCOPE changed. A success on provider X is never
    needed to unblock provider Y.

    Design-for-failure: resolver.provider_for() is contracted to never raise
    (CatalogProviderResolver — in-memory only, NEVER touches the DB on this hot
    path), but a broad except is kept anyway so a misbehaving custom resolver
    can never crash the request; it is treated as an unknown provider and
    bucketed accordingly. dict.setdefault guarantees a fresh CLOSED breaker for
    any never-seen provider key — never a KeyError.
    """

    def __init__(
        self,
        *,
        breakers: dict[str, CircuitBreaker],
        resolver: ProviderResolver,
        delegate: CompletionUpstream,
    ) -> None:
        self._breakers = breakers
        self._resolver = resolver
        self._delegate = delegate

    async def _breaker_for(self, payload: dict[str, Any]) -> CircuitBreaker:
        model = str(payload.get("model", ""))
        try:
            provider = await self._resolver.provider_for(model)
        except Exception:
            provider = "openrouter"
        return self._breakers.setdefault(provider, CircuitBreaker())

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Guard → delegate.complete → count outcome against the resolved provider's breaker.

        Raises CircuitOpenError if that provider's breaker is open (no upstream call).
        Raises UpstreamUnavailableError on 5xx (increments that provider's failure count).
        """
        breaker = await self._breaker_for(payload)
        if not breaker.call_allowed():
            raise CircuitOpenError("Circuit breaker is open")

        try:
            status, body = await self._delegate.complete(payload)
        except (UpstreamUnavailableError, CircuitOpenError):
            breaker.on_upstream_error()
            raise

        if status >= 500:
            breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Upstream returned {status}")

        breaker.record_success()
        return status, body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Guard → delegate.stream — byte-identical pass-through, provider-scoped breaker.

        Raises CircuitOpenError immediately if the resolved provider's breaker is open.
        On streaming, success is recorded at stream-start (usage reconciled later) —
        same semantics as BoundCircuitBreakerUpstream, just scoped per provider.
        """
        delegate = self._delegate

        async def _gen() -> AsyncIterator[bytes]:
            breaker = await self._breaker_for(payload)
            if not breaker.call_allowed():
                raise CircuitOpenError("Circuit breaker is open")
            breaker.record_success()
            async for chunk in delegate.stream(payload):
                yield chunk

        return _gen()


def get_completion_upstream(request: Request) -> CompletionUpstream:
    """Build a per-request, provider-scoped circuit-breaker-wrapped upstream.

    The per-provider breaker registry (app.state.provider_circuit_breakers) and
    the ProviderResolver (app.state.provider_resolver) are stable, per-app-
    instance singletons. The delegate (inner upstream) is also from app.state so
    tests can inject FakeCompletionUpstream freely.

    Fail-safe fallback: if either the registry or the resolver isn't wired on
    app.state (e.g. a stripped-down test double), falls back to the legacy
    single stable app.state.circuit_breaker via BoundCircuitBreakerUpstream —
    preserving prior behavior exactly rather than crashing.
    """
    delegate: CompletionUpstream = request.app.state.completion_upstream
    resolver = getattr(request.app.state, "provider_resolver", None)
    breakers: dict[str, CircuitBreaker] | None = getattr(
        request.app.state, "provider_circuit_breakers", None
    )
    if resolver is None or breakers is None:
        legacy_breaker: CircuitBreaker = request.app.state.circuit_breaker
        return BoundCircuitBreakerUpstream(legacy_breaker, delegate)
    return ProviderScopedCircuitBreakerUpstream(
        breakers=breakers, resolver=resolver, delegate=delegate
    )


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


def get_raw_key_ingress(request: Request) -> str | None:
    """Extract the raw API key for /v1/messages (anthropic-messages-ingress §3 M3).

    Additive sibling of get_raw_api_key — does NOT change get_raw_api_key's own
    behavior (that dependency, and /v1/chat/completions, are untouched).

    Priority mirrors keys/api/router.py::_extract_raw_key's documented contract
    EXACTLY, so both authn seams (Envoy ext_authz + this in-process one) accept
    identical credentials and reject identical failures with identical opacity:
      1. Authorization: Bearer <raw-key>  — checked first; any non-Bearer scheme
         is treated as absent and falls through to x-api-key.
      2. x-api-key: <raw-key>             — fallback when Authorization is absent
         or non-Bearer.
    Returns None when neither header yields a usable token (CompletionUseCase's
    private _authenticate — reused unmodified — raises AUTH_KEY_INVALID for a
    falsy raw_key, the SAME 401 get_raw_api_key's None already produces today).
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header:
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() == "bearer":
            return token or None
        # Non-Bearer scheme: treat as absent, fall through to x-api-key.
    api_key = request.headers.get("x-api-key", "")
    return api_key or None


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
    # output-schema-validation: operator kill-switch mirrors web_search_enabled's
    # getattr pattern exactly. Off (default) ⇒ the use-case pops validate_output
    # unconditionally and never engages ⇒ byte-identical to today.
    output_validation_enabled: bool = (
        bool(getattr(_settings, "output_validation_enabled", False)) if _settings else False
    )
    # payload-capture-store (§3): stable app.state-boot singleton — same getattr
    # pattern as tenant_credential_resolver/batch_diversion above. None ⇒ feature off
    # ⇒ byte-identical (_dispatch_capture no-ops at every hook site).
    payload_capture = getattr(request.app.state, "payload_capture", None)
    # credits-ledger TASK.md §3: app.state-boot singleton (PostgresCreditGuard in
    # production, PassthroughCreditGuard default). Same getattr pattern as the other
    # optional app.state singletons above — absent ⇒ PassthroughCreditGuard ⇒
    # check_and_hold is a no-op ⇒ byte-identical to today.
    from gateway.credits.domain.ports import PassthroughCreditGuard

    credit_guard = getattr(request.app.state, "credit_guard", None) or PassthroughCreditGuard()
    hold_estimate_usd = _settings.credits_hold_estimate_usd if _settings else Decimal("0.50")
    # residency-policy TASK.md §3 (FROZEN @ v2): app.state-boot singleton, same getattr
    # pattern as tenant_credential_resolver above. None ⇒ feature off ⇒ byte-identical.
    residency_lookup = getattr(request.app.state, "residency_lookup", None)
    # service-tiers TASK.md §3 (FROZEN @ v1): app.state-boot singleton (RedisTierCapacityGuard
    # in production, PassthroughTierCapacityGuard default). Same getattr pattern as
    # credit_guard immediately above — absent ⇒ PassthroughTierCapacityGuard ⇒
    # check_and_hold returns an undegraded hold at the tenant's default tier ⇒
    # byte-identical to today.
    tier_capacity_guard = getattr(request.app.state, "tier_capacity_guard", None) or (
        PassthroughTierCapacityGuard()
    )
    # plan-rate-enforcement TASK.md §3 (FROZEN @ v1): app.state-boot singleton, same
    # getattr pattern as tier_capacity_guard/credit_guard/residency_lookup above. None
    # (default — no operator wiring yet) ⇒ enforce_tenant_rate_limit() no-ops ⇒
    # _enforce_rate_limits stays byte-identical to today (per-key enforcement only).
    plan_rate_limit_resolver = getattr(request.app.state, "plan_rate_limit_resolver", None)
    # platform-key-default: same stable getattr pattern. None ⇒ no fallback wired ⇒
    # resolve_provider_credential's fallback branch is inert ⇒ byte-identical fail-closed 402.
    platform_credential_fallback = getattr(request.app.state, "platform_credential_fallback", None)
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
        output_validation_enabled=output_validation_enabled,
        payload_capture=payload_capture,
        credit_guard=credit_guard,
        hold_estimate_usd=hold_estimate_usd,
        residency_lookup=residency_lookup,
        tier_capacity_guard=tier_capacity_guard,
        plan_rate_limit_resolver=plan_rate_limit_resolver,
        platform_credential_fallback=platform_credential_fallback,
    )
