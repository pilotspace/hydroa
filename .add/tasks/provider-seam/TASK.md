# TASK: Provider-selection seam + catalog modality/provider + OpenAI direct adapter

slug: provider-seam · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Provider-selection routing seam — catalog (modality, provider) discriminator, OpenAI direct adapter interface, additive supersession of OpenRouter-as-sole-upstream

Framings weighed:
  - **Registry dispatch on (modality, provider) with chat-untouched boundary (chosen)**: the
    existing chat path (app.state.completion_upstream → BoundCircuitBreakerUpstream →
    FallbackModelRouter) is left byte-identical. A new ProviderRegistry (an explicit
    dict[str, UpstreamProvider] keyed by provider name) is consulted ONLY for non-chat
    modalities (embedding, image, audio_stt, audio_tts). Selection is server-decided from
    the catalog row's (modality, provider) fields. Blast radius is bounded: an error in the
    new seam cannot affect POST /v1/chat/completions.
  - **Unified UpstreamProvider protocol replacing CompletionUpstream (rejected)**: would
    require touching the frozen CompletionUpstream protocol and all frozen test fakes across
    11+ suites. The supersession framing (additive) is the milestone-mandated approach.
  - **Strategy seam routing chat AND non-chat through a single dispatcher (rejected)**:
    out of scope per MILESTONE.md "Out" section — routing strategies across providers for
    the SAME modality are deferred; a deterministic one-provider-per-modality approach is
    the v7 contract.

SUPERSESSION BLOCK (Key Decision — record at freeze):
  "OpenRouter as sole upstream" is superseded ADDITIVELY. OpenRouter remains the DEFAULT
  chat provider; v6 proxy/api/router.py → CompletionUseCase → BoundCircuitBreakerUpstream
  → OpenRouterCompletionUpstream path is byte-identical. Direct providers are additive,
  selected per modality by catalog metadata. OpenRouter-only deployments keep working:
  an unconfigured direct provider causes its modality endpoints to return 503
  (ERR_PROVIDER_UNAVAILABLE), never affecting chat. This mirrors the JwksKeyCache /
  retry-policy Key Decision precedent — the locked "sole upstream" decision is superseded
  here, not edited.

Must:
<must>
  - The `models` table MUST gain two columns additive-only:
      `modality` TEXT NOT NULL DEFAULT 'chat'      — every existing row becomes chat
      `provider` TEXT NOT NULL DEFAULT 'openrouter' — every existing row stays openrouter
    Implemented as a single Alembic migration with `ADD COLUMN ... DEFAULT ... NOT NULL`
    (PostgreSQL applies the default instantly for new columns; no full-table rewrite needed).
    The migration MUST have a documented downgrade (DROP COLUMN).
  - `ModelRow` (catalog/infrastructure/orm.py) MUST gain `modality: Mapped[str]` and
    `provider: Mapped[str]` with matching server defaults. The domain `ModelRow` and
    `CatalogModel` dataclasses (catalog/domain/entities.py) MUST gain the two fields,
    defaulting to ("chat", "openrouter") so callers that omit them are v6-compatible.
  - Modality MUST be one of the five frozen values: chat | embedding | image |
    audio_stt | audio_tts. This is enforced as a domain constraint (Literal type or
    validated Enum); invalid values are rejected at the catalog write path.
  - A typed port `UpstreamProvider` Protocol MUST be defined (proxy/domain/ports.py or
    a new provider_ports.py) exposing a thin HTTP adapter surface sufficient for all four
    non-chat modalities:
      `async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]`
      `async def post_multipart(self, path: str, files: dict[str, Any], data: dict[str, Any]) -> tuple[int, dict[str, Any]]`
      `async def stream_bytes(self, path: str, payload: dict[str, Any]) -> AsyncIterator[bytes]`
    All three methods MUST be present; endpoint tasks (embeddings, images, audio) plug into
    the appropriate method without further interface changes.
  - `ProviderRegistry` MUST be a typed class wrapping `dict[str, UpstreamProvider]`
    with a `get(provider_name: str) -> UpstreamProvider | None` method.
    `select_provider(modality: str, provider: str, registry: ProviderRegistry) -> UpstreamProvider`
    MUST be a pure function (or method) that returns the provider or raises
    ERR_PROVIDER_UNAVAILABLE (503) when the provider is absent from the registry.
  - `OpenAIDirectProvider` MUST implement `UpstreamProvider` with:
      constructor: `(api_key: str, *, base_url: str = "https://api.openai.com/v1",
                     metrics_registry: MetricsRegistry | None = None)`
      a per-provider circuit breaker (reuse `CircuitBreaker`, same pattern as
      `OpenRouterCompletionUpstream`) constructed in __init__.
      all three UpstreamProvider methods implemented (no retries by default — v6 conservative
      default; follow-up task may add openai_max_retries knob).
    The api_key is NEVER logged, echoed, or committed. The provider is absent when
    GATEWAY_OPENAI_API_KEY is empty (the registry simply will not contain an "openai" entry).
  - Settings MUST gain two new fields (GATEWAY_ prefix):
      `openai_api_key: str = ""`       — secret; empty = OpenAI provider absent from registry
      `openai_base_url: str = "https://api.openai.com/v1"`
    These fields follow the same handling as `openrouter_api_key` (never logged, echoed, or
    committed; treated as a secret in all log/metric/observability paths).
  - `create_app` (main.py) MUST wire a `ProviderRegistry` onto `app.state.provider_registry`:
      always contains "openrouter" → the existing `app.state.completion_upstream` adapter
      (an UpstreamProvider-compatible facade/wrapper — see contract for the adapter shape);
      additionally contains "openai" → `OpenAIDirectProvider(...)` ONLY when
      `settings.openai_api_key` is non-empty.
    The chat path (app.state.completion_upstream, app.state.model_router) MUST NOT be
    modified by this wiring.
  - The chat path is BYTE-IDENTICAL to v6: POST /v1/chat/completions still flows through
    proxy/api/router.py → CompletionUseCase → get_completion_upstream() (deps.py) →
    BoundCircuitBreakerUpstream(app.state.circuit_breaker, app.state.completion_upstream) →
    FallbackModelRouter. The provider registry is NEVER consulted on this path.
  - A new `ERR_PROVIDER_UNAVAILABLE` entry MUST be added to `gateway/core/error_catalog.py`:
      `PROVIDER_UNAVAILABLE = ErrorSpec(503, "ERR_PROVIDER_UNAVAILABLE",
       "Provider '{provider}' is not configured or unavailable")`
    All sites MUST call `.exc(provider=provider_name)` via this spec; no direct
    ProblemError construction with raw literals.
  - A catalog population path for OpenAI models MUST be provided: a small fixed-seed list
    (`OPENAI_SEED_MODELS`) of known OpenAI models with their modality + provider fields,
    usable by a catalog sync command. Full dynamic sync is a follow-up; this task contracts
    the seed only (catalog entries that downstream tasks depend on).
  - The v6 alias-aware entry catalog check (`SqlAlchemyModelChecker.check_for_tenant` +
    `is_active`) MUST work unchanged for non-chat model rows: the check queries
    `models.active` and `tenant_model_overrides.enabled` — it is modality-agnostic by
    construction. No change needed; this must is a confirmation boundary.
  - Every new `app.state` seam (`app.state.provider_registry`) MUST ship a paired
    production-wiring regression test (foundation v6 rule — pattern:
    tests/cooldown_circuit_wiring/, tests/retry_policy_wiring/).
  - The OpenAIDirectProvider MUST have a per-provider circuit breaker. Non-chat resilience
    stays basic (one breaker per provider instance; no per-path breaker, no retries by
    default). A follow-up may add `openai_max_retries` and per-path breakers; this is
    explicitly documented as a known gap.
  - A client MUST NOT be able to override the provider selection: any "provider" field
    in the request body is silently ignored; routing is determined solely by catalog
    metadata.
</must>

Reject:
<reject>
  - modality value outside {"chat","embedding","image","audio_stt","audio_tts"}
    → ValidationError at the catalog write path — "ERR_PAYLOAD_INVALID"
  - non-chat model request when OpenAI provider is absent from registry (openai_api_key empty)
    → 503 with code "ERR_PROVIDER_UNAVAILABLE"
  - client request body carrying a "provider" field
    → field is silently stripped; routing is catalog-only (no error, but provider is ignored)
  - inspect.signature / hasattr dispatch for UpstreamProvider selection
    → "TYPED_EXTRAS_NO_DISPATCH" (explicit typed registry dict only — foundation rule)
  - openai_api_key or openai_base_url appearing in any log field, metric label, or
    span attribute
    → "NO_KEY_MATERIAL_IN_LOGS" (same rule as openrouter_api_key)
</reject>

After:
<after>
  - A catalog row with modality="embedding", provider="openai" causes requests to that
    model to be routed to OpenAIDirectProvider.post_json("/embeddings", payload) when
    the OpenAI key is configured; ERR_PROVIDER_UNAVAILABLE (503) otherwise.
  - A catalog row with modality="chat" (default) or provider="openrouter" (default)
    causes the request to flow through the v6 CompletionUpstream path — no registry
    interaction.
  - The `models` table has `modality` and `provider` columns; every pre-existing row has
    modality="chat" and provider="openrouter" (migration default).
  - `app.state.provider_registry` always exists after create_app(); contains "openrouter"
    always; contains "openai" iff GATEWAY_OPENAI_API_KEY is non-empty.
  - Chat completions are byte-identical to v6 regardless of whether OpenAI is configured.
  - A non-chat active model passes check_for_tenant / is_active unchanged.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE [seam shape]: The three-method `UpstreamProvider` surface
    (post_json / post_multipart / stream_bytes) is designed to cover all four non-chat
    modalities. However, the exact per-endpoint request shapes are owned by the three
    endpoint tasks (embeddings/images/audio). If those tasks discover a modality needs
    a fourth method (e.g. a streaming JSON response rather than raw bytes for a future
    endpoint), the UpstreamProvider protocol must be extended additively — which is a
    change-request back to this contract. Cost: rework of OpenAIDirectProvider plus
    all endpoint tasks that depend on the port. Mitigation: the three methods were chosen
    after surveying the OpenAI API surface for the four target modalities; the risk is
    low but non-zero for audio_tts (which streams bytes — stream_bytes covers it) and
    audio_stt (which sends multipart — post_multipart covers it).

  ⚠ SECOND-LOWEST [chat-untouched boundary]: the contract asserts that the chat path
    is byte-identical to v6 and that the provider registry is NEVER consulted for chat
    requests. This relies on the non-chat endpoint tasks correctly routing through the
    registry and NOT accidentally using the chat path. If an endpoint task calls
    get_completion_upstream() (deps.py) instead of the registry, chat behavior is
    unaffected but the seam contract is violated. Cost: routing bug on non-chat endpoints
    (not a chat regression). Mitigation: the wiring regression test (PS10) pins that
    chat still uses app.state.completion_upstream and the registry is a separate seam;
    endpoint task reviews must confirm they use the registry path.

  - [x] OpenAI per-provider circuit breaker uses the same CircuitBreaker class as
        OpenRouter. This is safe (same failure thresholds, no cross-contamination) but
        means circuit state is per-provider-instance, not per-path or per-model. A future
        per-model breaker for OpenAI is a follow-up; acceptable for v7.
  - [x] The ProviderRegistry wraps "openrouter" with an adapter facade over the existing
        completion_upstream. This facade MUST implement UpstreamProvider (all three
        methods) but its post_json maps to completion_upstream.complete() and its
        stream_bytes maps to completion_upstream.stream(). post_multipart is not used for
        chat, but MUST be present (raises UpstreamUnavailableError or NotImplementedError
        so tests that accidentally call it fail loudly). The facade is thin and simple.
  - [x] The seed OpenAI model list is a small fixed set (e.g. text-embedding-3-small,
        text-embedding-3-large, dall-e-3, whisper-1, tts-1, tts-1-hd). Full dynamic sync
        is a follow-up. The seed list is a constant in a new openai_seed.py file, not
        in main.py. Tests that need specific OpenAI model rows seed them manually.
  - [x] Modality is stored as TEXT with a Python Literal type constraint — not a PostgreSQL
        ENUM type. This avoids ALTER TYPE migrations for future modality additions and
        mirrors the "additive" convention. The domain layer validates modality membership
        at the application boundary; the database stores raw text with no DB-level CHECK
        constraint (a CHECK constraint addition is a follow-up migration).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence
     first, the top two ⚠-flagged with why + cost; all five modality values named. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: PS1 — embedding model routes to OpenAI adapter (not OpenRouter)
  Given a ProviderRegistry with OpenAI configured
  And a catalog model with modality="embedding", provider="openai"
  When select_provider("embedding", "openai", registry) is called
  Then the returned adapter is the OpenAIDirectProvider instance
  And the returned adapter is NOT the OpenRouter/chat adapter

Scenario: PS2 — chat model (default) routes through OpenRouter path; registry not consulted
  Given a ProviderRegistry with both OpenRouter and OpenAI entries
  And a catalog model with modality="chat" (default), provider="openrouter" (default)
  When a POST /v1/chat/completions request is processed
  Then the request flows through app.state.completion_upstream (v6 chat path)
  And app.state.provider_registry is not consulted
  And the response is byte-identical to what v6 would have returned

Scenario: PS3 — Settings gains openai_api_key + openai_base_url; registry contains OpenAI when key set
  Given Settings with openai_api_key="sk-test-key" and openai_base_url="https://api.openai.com/v1"
  When create_app(settings) is called
  Then app.state.provider_registry.get("openai") is an OpenAIDirectProvider
  And the provider's base_url is "https://api.openai.com/v1"

Scenario: PS4 — openai_api_key unset → provider absent → non-chat request returns 503
  Given Settings with openai_api_key="" (empty / unset)
  And a catalog model with modality="embedding", provider="openai"
  When select_provider("embedding", "openai", registry) is called
  Then a ProblemError with status=503, code="ERR_PROVIDER_UNAVAILABLE" is raised
  And the chat path is unaffected

Scenario: PS5 — models table modality and provider columns exist with correct defaults
  Given the Alembic migration has been applied (dev/test schema bootstrap)
  When a new ModelRow is inserted without specifying modality or provider
  Then ModelRow.modality == "chat"
  And ModelRow.provider == "openrouter"
  And existing rows are unaffected (default applied at migration time)

Scenario: PS6 — client cannot override provider; "provider" in request body is silently ignored
  Given a valid API key and an active chat model
  And a request body containing {"model": "...", "messages": [...], "provider": "openai"}
  When the request is processed
  Then the "provider" field does not affect routing
  And routing is determined by the catalog row's provider column only
  And the response is successful (provider field ignored without error)

Scenario: PS7 — entry catalog check accepts non-chat active model id unchanged
  Given a ModelRow with modality="embedding", provider="openai", active=true
  And no tenant_model_overrides row for this model
  When SqlAlchemyModelChecker.check_for_tenant(model_id, tenant_id) is called
  Then ModelAccess.ACTIVE is returned
  And SqlAlchemyModelChecker.is_active(model_id) returns True
  And no change to the model_checker code is needed

Scenario: PS8 — OpenAIDirectProvider method surface exists and calls base_url + path
  Given an OpenAIDirectProvider constructed with a MockTransport
  And api_key="sk-test" and base_url="https://api.openai.com/v1"
  When post_json("/embeddings", {"input": "hello", "model": "text-embedding-3-small"}) is called
  Then an HTTP POST is made to "https://api.openai.com/v1/embeddings"
  And the Authorization header is "Bearer sk-test"
  And the response (status, body) is returned

Scenario: PS9 (GREEN-BY-DESIGN) — chat path byte-identical; provider registry not consulted
  Given create_app wired with both OpenRouter and OpenAI (key set)
  And a FakeCompletionUpstream injected on app.state.completion_upstream
  When POST /v1/chat/completions is called with a valid payload
  Then app.state.completion_upstream.complete() is called (FakeCompletionUpstream records it)
  And app.state.provider_registry is never consulted
  And the response is identical to a v6 baseline

Scenario: PS10 — production wiring: create_app wires provider_registry with correct entries
  Given Settings with openrouter_api_key="or-key" and openai_api_key="sk-key"
  When create_app(settings) is called
  Then app.state.provider_registry exists
  And app.state.provider_registry.get("openrouter") is not None
  And app.state.provider_registry.get("openai") is an OpenAIDirectProvider
  And app.state.completion_upstream is an OpenRouterCompletionUpstream (v6 path unchanged)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject (PS6 covers the client-override reject,
     PS4 covers the absent-provider reject); each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LOWEST-CONFIDENCE FLAGS AT DRAFT

  ⚠ [contract] UpstreamProvider three-method surface — post_json / post_multipart /
    stream_bytes is designed by inspection of the four target modalities:
    embeddings → post_json("/embeddings", ...) → (status, body)
    images     → post_json("/images/generations", ...) → (status, body)
    audio_stt  → post_multipart("/audio/transcriptions", files, data) → (status, body)
    audio_tts  → stream_bytes("/audio/speech", ...) → AsyncIterator[bytes]
    Risk: if an endpoint task discovers it needs a response shape not covered by these
    three methods (e.g. a streamed JSON response different from raw bytes), the protocol
    must be extended additively (a change-request back to this contract). Tagged [contract]
    because the three-method shape is the most load-bearing decision here. Cost: rework
    of OpenAIDirectProvider + dependent endpoint tasks.

  ⚠ [seam] Chat-untouched boundary — the chat path (deps.py → BoundCircuitBreakerUpstream
    → OpenRouterCompletionUpstream) is asserted byte-identical by construction (the
    provider registry is a new parallel seam). If an endpoint task accidentally calls
    get_completion_upstream() instead of the registry path, routing is wrong for that
    endpoint but chat is unaffected. The wiring regression (PS10) and an explicit
    "MUST NOT call get_completion_upstream() from non-chat endpoints" constraint bound
    this risk. Tagged [seam] because it is a boundary that cannot be enforced by the
    type system alone.

────────────────────────────────────────────────────────────────────────

INTERNAL SEAM (not an HTTP endpoint)

  UpstreamProvider Protocol
  (placement: apps/gateway/src/gateway/proxy/domain/ports.py — additive, below ModelHealthGate)

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]
      — POST path with JSON body; returns (status_code, json_body).
        Used by: embeddings (POST /embeddings), images (POST /images/generations).

    async def post_multipart(
        self,
        path: str,
        files: dict[str, Any],
        data: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]
      — POST path with multipart/form-data; returns (status_code, json_body).
        Used by: audio STT (POST /audio/transcriptions).

    def stream_bytes(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]
      — Return an async generator yielding raw bytes.
        Used by: audio TTS (POST /audio/speech).
        Raises UpstreamUnavailableError on first byte failure.
        Zero retry machinery (same rule as CompletionUpstream.stream()).

  ProviderRegistry
  (placement: apps/gateway/src/gateway/proxy/infrastructure/provider_registry.py)

    class ProviderRegistry:
        def __init__(self, providers: dict[str, UpstreamProvider]) -> None
        def get(self, provider_name: str) -> UpstreamProvider | None

    select_provider(
        modality: str,
        provider: str,
        registry: ProviderRegistry,
    ) -> UpstreamProvider:
      — Returns the adapter for provider_name.
        Raises ERR_PROVIDER_UNAVAILABLE (503) when provider absent from registry.
        Modality is passed for future routing strategies but is not used in v7
        (one deterministic provider per name).
        NEVER uses inspect.signature or hasattr dispatch.

  CHAT-UNTOUCHED BOUNDARY (inviolable, v6-identical):
    POST /v1/chat/completions still flows through:
      proxy/api/router.py
        → get_completion_use_case() (deps.py)
          → BoundCircuitBreakerUpstream(app.state.circuit_breaker, app.state.completion_upstream)
            → FallbackModelRouter (app.state.model_router)
              → OpenRouterCompletionUpstream
    app.state.provider_registry is NEVER consulted on this path.
    Non-chat endpoint tasks MUST route through app.state.provider_registry, NOT through
    get_completion_upstream() / CompletionUseCase.

  OpenAIDirectProvider
  (placement: apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py)

    class OpenAIDirectProvider:
        def __init__(
            self,
            api_key: str,
            *,
            base_url: str = "https://api.openai.com/v1",
            metrics_registry: MetricsRegistry | None = None,
        ) -> None:
            — Constructs httpx.AsyncClient(base_url=base_url, timeout=...)
              and a per-instance CircuitBreaker.
              api_key is NEVER stored in any attribute exposed to logging.
              timeout: connect 10s, non-stream 120s, stream read 300s (same as OpenRouter).

        async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]
        async def post_multipart(self, path: str, files: dict[str, Any], data: dict[str, Any]) -> tuple[int, dict[str, Any]]
        def stream_bytes(self, path: str, payload: dict[str, Any]) -> AsyncIterator[bytes]

        Circuit breaker: per-instance CircuitBreaker (same CircuitBreaker class as
          OpenRouterCompletionUpstream). No per-path breaker in v7.
          Fail behavior: 5xx → breaker.on_upstream_error(); success/4xx → breaker.record_success().
          CircuitOpenError propagates immediately (no catch — endpoint task handles it as 502).
        No retries in v7 (conservative default). A follow-up may add openai_max_retries.
        MUST implement all three UpstreamProvider methods.
        MUST satisfy isinstance(provider, UpstreamProvider) check at runtime
          (runtime_checkable Protocol).

  OpenRouterUpstreamProvider facade
  (placement: apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream_provider.py
   OR thin class in provider_registry.py — build task decides placement)

    class OpenRouterUpstreamFacade:
        """Wraps OpenRouterCompletionUpstream to satisfy UpstreamProvider for registry entry.

        post_json → delegates to completion_upstream.complete(payload)
        stream_bytes → delegates to completion_upstream.stream(payload)
        post_multipart → raises UpstreamUnavailableError("OpenRouter does not support multipart")
          (chat never calls post_multipart; a loud error on misuse is correct)
        """
        def __init__(self, upstream: CompletionUpstream) -> None

CATALOG SCHEMA DELTA (additive migration)

  Migration file: apps/gateway/migrations/versions/<rev>_catalog_modality_provider.py
  (build task creates the file; the contract DESCRIBES it)

  Revision: <new_rev> — depends_on: latest current revision
  Description: "catalog_modality_provider — add modality and provider columns to models table"

  upgrade():
    op.add_column("models", sa.Column(
        "modality", sa.Text(), nullable=False, server_default="chat"
    ))
    op.add_column("models", sa.Column(
        "provider", sa.Text(), nullable=False, server_default="openrouter"
    ))
    # No index needed in v7 — full-table scans on a small catalog table are acceptable.
    # A partial index on (modality, provider, active) is a follow-up optimization.

  downgrade():
    op.drop_column("models", "provider")
    op.drop_column("models", "modality")

  Safety: both columns are NOT NULL with server_default — PostgreSQL adds them in a single
  DDL statement without a full rewrite; existing rows immediately have the default values.
  The downgrade is safe (additive-only; no data depends on these columns at migration time).

ORM / DOMAIN CHANGES (build task writes these; contract DESCRIBES shape)

  catalog/infrastructure/orm.py — ModelRow additions:
    modality: Mapped[str] = mapped_column(Text, nullable=False, server_default="chat")
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="openrouter")

  catalog/domain/entities.py — CatalogModel additions:
    modality: str = "chat"
    provider: str = "openrouter"

  catalog/domain/entities.py — ModelRow domain class additions:
    modality: str = "chat"
    provider: str = "openrouter"

  Modality validation: a Literal type alias is defined in catalog/domain/entities.py:
    Modality = Literal["chat", "embedding", "image", "audio_stt", "audio_tts"]
  Used by CatalogModel.modality field type annotation. The write path
  (catalog application layer) validates modality membership at insert time.
  Invalid values raise ERR_PAYLOAD_INVALID (422).

SETTINGS (gateway/core/config.py additions — GATEWAY_ prefix)

  openai_api_key: str = ""
    — GATEWAY_OPENAI_API_KEY. Empty = OpenAI provider absent from registry.
      Treated as a secret: NEVER logged, echoed, committed, or placed in metric labels.
      Follows the same handling as openrouter_api_key.

  openai_base_url: str = "https://api.openai.com/v1"
    — GATEWAY_OPENAI_BASE_URL. Override in e2e overlays to point at a stub.
      NEVER set to a non-https URL in production deployments.

  No validator needed beyond the existing "not logged / committed" convention.
  The absence of openai_api_key (empty string) is the "provider disabled" state —
  no ValidationError is raised; the registry simply excludes the OpenAI entry.

ERROR CATALOG (gateway/core/error_catalog.py addition)

  PROVIDER_UNAVAILABLE = ErrorSpec(
      503,
      "ERR_PROVIDER_UNAVAILABLE",
      "Provider '{provider}' is not configured or unavailable",
  )

  Usage: raise PROVIDER_UNAVAILABLE.exc(provider=provider_name)
  ALL sites MUST use this entry; direct ProblemError construction is forbidden.
  The {provider} placeholder is the provider name string from the catalog row (public metadata).

WIRING (gateway/main.py — create_app additions)

  # After the existing completion_upstream and model_router wiring:

  _openrouter_facade = OpenRouterUpstreamFacade(upstream=app.state.completion_upstream)
  _providers: dict[str, UpstreamProvider] = {"openrouter": _openrouter_facade}
  if settings.openai_api_key:
      _providers["openai"] = OpenAIDirectProvider(
          api_key=settings.openai_api_key,
          base_url=settings.openai_base_url,
          metrics_registry=app.state.metrics_registry,
      )
  app.state.provider_registry = ProviderRegistry(_providers)

  The chat path (app.state.completion_upstream, app.state.circuit_breaker,
  app.state.model_router) is NOT modified.

PRODUCTION-WIRING REGRESSION TEST

  tests/provider_seam_wiring/ (paired suite; foundation v6 rule)
  NOTE: in this task the wiring tests are co-located in tests/provider_seam/ (PS3, PS10)
  to keep the test count within the planned 10–12 range. If a separate suite is preferred
  by the orchestrator, it can be split at freeze.

  — asserts app.state.provider_registry exists
  — asserts app.state.provider_registry.get("openrouter") is not None at default settings
  — asserts app.state.provider_registry.get("openai") is None when openai_api_key=""
  — asserts app.state.provider_registry.get("openai") is an OpenAIDirectProvider
    when openai_api_key is non-empty
  — asserts app.state.completion_upstream is still OpenRouterCompletionUpstream (v6 chat path unchanged)

OPENAI SEED MODELS (catalog population)

  Placement: apps/gateway/src/gateway/catalog/infrastructure/openai_seed.py
  A module-level constant OPENAI_SEED_MODELS: list[CatalogModel] — a fixed list of
  known OpenAI model entries with their modality/provider set. Minimum set:
    text-embedding-3-small  (modality="embedding", provider="openai")
    text-embedding-3-large  (modality="embedding", provider="openai")
    dall-e-3               (modality="image",     provider="openai")
    whisper-1              (modality="audio_stt", provider="openai")
    tts-1                  (modality="audio_tts", provider="openai")
    tts-1-hd               (modality="audio_tts", provider="openai")
  Full dynamic sync is a follow-up. Endpoint tasks may seed additional rows in their tests.

RESILIENCE INTERPLAY

  Per-provider circuit breaker: one CircuitBreaker instance per OpenAIDirectProvider instance.
  This is constructed in OpenAIDirectProvider.__init__(); no sharing with the OpenRouter breaker.
  Non-chat upstream calls are guarded by this breaker:
    CircuitOpenError → propagates out of the UpstreamProvider methods → endpoint task
    catches and maps to 502 (same pattern as CompletionUseCase catches CircuitOpenError).
  No BoundCircuitBreakerUpstream wrapping for non-chat in v7 (the breaker is internal to
  OpenAIDirectProvider, not a composable wrapper). A future hardening may add the wrapper.
  No retries for non-chat in v7. Documented follow-up: openai_max_retries knob.

GLOSSARY DELTAS (from MILESTONE.md)
  modality         — one of {chat, embedding, image, audio_stt, audio_tts}; catalog column
  provider         — upstream provider name; catalog column; registry key
  direct_provider  — a provider adapter reached directly (not via OpenRouter)
  provider_selection — server-decided routing: catalog row (modality, provider) → adapter
  UpstreamProvider — the typed port (Protocol) for non-chat (and registry-facade for chat)
  ProviderRegistry — explicit dict[str, UpstreamProvider] wrapper; no dynamic dispatch
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] the UpstreamProvider three-method surface
(post_json / post_multipart / stream_bytes) is designed by inspection of the four non-chat
modalities; the per-endpoint request/response shapes are owned by the three endpoint tasks.
If any endpoint needs a fourth method, the Protocol extends additively — a change request back
to SPECIFY. Second flag [seam]: the chat-untouched boundary cannot be enforced by the type
system; PS9/PS10 wiring regressions + the explicit "non-chat endpoints MUST route through
app.state.provider_registry, NOT get_completion_upstream()" constraint bound it.
Orchestrator amendment at freeze: PS7 was rewritten from an in-memory `sqlite+aiosqlite`
engine to the shared Postgres `db_session` fixture — aiosqlite is not a project dependency and
the gateway ORM uses Postgres-only column types (PGUUID/JSONB) across 6 modules, so
`Base.metadata.create_all` on SQLite would fail for the wrong reason. PS7 now fails with the
intended TypeError on the modality/provider kwargs. Verified: OpenAIDirectProvider internal
attrs (_api_key/_breaker/_client) match the OpenRouterCompletionUpstream convention exactly
(PS8 manual transport injection); CircuitBreaker() no-arg + on_upstream_error()/guard() exist;
ErrorSpec/ProblemError (.status/.code/.title), CatalogModel/ModelRow signatures, and
SqlAlchemyModelChecker(session) all confirmed. 16-test red suite fails for the right reasons;
ruff clean.
<!-- LOWEST-CONFIDENCE FLAGS at the draft freeze review:
     ⚠ [contract] UpstreamProvider three-method surface — see top of §3 block above.
         Why: endpoint shapes are owned by downstream tasks; a mismatch forces rework here.
         Cost: OpenAIDirectProvider rewrite + endpoint task rework.
     ⚠ [seam] Chat-untouched boundary — see top of §3 block above.
         Why: cannot be enforced by the type system; relies on endpoint task discipline.
         Cost: wrong routing on non-chat endpoints (not a chat regression).
     [part] = [contract] for the seam shape (the highest-impact flag).
     Approved → Status: FROZEN @ vN — approved by <name>.
     Changing a frozen contract = change request back to SPECIFY. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of seam paths + Settings wiring + error path + chat-untouched invariant

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_ps1_embedding_model_routes_to_openai_adapter:
      arrange: ProviderRegistry({"embedding": OpenAIDirectProvider(...)}) — or keyed by
               provider name "openai"; select_provider("embedding", "openai", registry)
      act: call select_provider
      assert: returned object is the OpenAIDirectProvider; NOT the OpenRouter facade
      RED reason: ProviderRegistry and select_provider do not exist → ImportError

  - test_ps2_chat_model_returns_openrouter_facade:
      arrange: ProviderRegistry({"openrouter": fake_openrouter, "openai": fake_openai})
      act: select_provider("chat", "openrouter", registry)
      assert: returns the openrouter facade (not openai)
      RED reason: ImportError on ProviderRegistry / select_provider

  - test_ps3_settings_gains_openai_fields_wiring:
      arrange: Settings(openai_api_key="sk-test", openai_base_url="https://api.openai.com/v1")
      act: create_app(settings)
      assert: app.state.provider_registry.get("openai") is an OpenAIDirectProvider
      RED reason: Settings lacks openai_api_key field → ValidationError on unknown field

  - test_ps4_openai_key_unset_provider_absent_raises_503:
      arrange: ProviderRegistry({"openrouter": fake_openrouter})  (no "openai" entry)
      act: select_provider("embedding", "openai", registry)
      assert: raises ProblemError with status=503 and code="ERR_PROVIDER_UNAVAILABLE"
      RED reason: ImportError on select_provider; ERR_PROVIDER_UNAVAILABLE absent from error_catalog

  - test_ps5_models_table_modality_provider_columns_exist:
      arrange: create_app(test_settings) — dev/test schema bootstrap creates tables from ORM
      act: inspect ModelRow columns via SQLAlchemy metadata; or insert a ModelRow with
           defaults and read it back
      assert: ModelRow has .modality attribute defaulting to "chat"
              ModelRow has .provider attribute defaulting to "openrouter"
      RED reason: ModelRow ORM class lacks modality/provider columns → AttributeError

  - test_ps6_client_provider_field_ignored:
      arrange: app with FakeCompletionUpstream; valid API key; active chat model
      act: POST /v1/chat/completions with body {"model": "...", "messages": [...], "provider": "openai"}
      assert: response is 200 (or whatever the fake returns for success)
              FakeCompletionUpstream.complete() was called (not skipped due to "provider" field)
              routing was not affected by "provider" in body
      RED reason: chat route returns 200 today, BUT the test verifies "provider" field is ignored
                  — this test is GREEN-BY-DESIGN for the ignore behavior (FastAPI ignores unknown
                  fields by default). It is RED at this stage because the full app fixture
                  dependencies (FakeCompletionUpstream wiring pattern) are not yet confirmed for
                  the new suite; will become truly red only if the implementation incorrectly
                  acts on the "provider" field. Kept as a smoke test of the invariant.
      NOTE: if this test passes trivially (FastAPI ignores unknown body fields), it is
            still a valid regression guard — label it GREEN-BY-DESIGN in the test.

  - test_ps7_non_chat_active_model_passes_catalog_check:
      arrange: DB session with a ModelRow(id="text-embedding-3-small", modality="embedding",
               provider="openai", active=True, name="...", context_length=None)
               SqlAlchemyModelChecker(session)
      act: await checker.is_active("text-embedding-3-small")
           await checker.check_for_tenant("text-embedding-3-small", tenant_id)
      assert: is_active returns True; check_for_tenant returns ModelAccess.ACTIVE
      RED reason: ModelRow ORM lacks modality/provider → AttributeError at row insertion
                  (the check itself is modality-agnostic; the test fails at DB insert)

  - test_ps8_openai_provider_method_surface_calls_correct_url:
      arrange: httpx.MockTransport returning 200 {"data": []}; OpenAIDirectProvider with mock
      act: await provider.post_json("/embeddings", {"input": "hello"})
      assert: transport received a POST to "https://api.openai.com/v1/embeddings"
              Authorization header == "Bearer sk-test"
              returned (200, {"data": []})
      RED reason: OpenAIDirectProvider does not exist → ImportError

  - test_ps9_chat_path_byte_identical_provider_registry_not_consulted (GREEN-BY-DESIGN):
      arrange: app with FakeCompletionUpstream on app.state.completion_upstream
               FakeProviderRegistry on app.state.provider_registry (records calls)
      act: POST /v1/chat/completions with valid payload
      assert: FakeCompletionUpstream.complete() called; FakeProviderRegistry.get() NOT called
      RED reason: FakeProviderRegistry and app.state.provider_registry seam do not exist →
                  AttributeError when setting up the fake registry on app.state.
                  Once provider_registry seam exists, this test is GREEN-BY-DESIGN.

  - test_ps10_production_wiring_registry_entries:
      arrange: Settings(openrouter_api_key="or-key", openai_api_key="sk-key", environment="test")
      act: create_app(settings)
      assert: hasattr(app.state, "provider_registry")
              app.state.provider_registry.get("openrouter") is not None
              app.state.provider_registry.get("openai") is an OpenAIDirectProvider
              app.state.completion_upstream is an OpenRouterCompletionUpstream (v6 path unchanged)
      RED reason: ProviderRegistry / OpenAIDirectProvider / provider_registry wiring absent → ImportError

  - test_ps10b_openai_absent_when_key_empty:
      arrange: Settings(openai_api_key="", environment="test")
      act: create_app(settings)
      assert: app.state.provider_registry.get("openai") is None
              app.state.provider_registry.get("openrouter") is not None
      RED reason: same as PS10 — ImportError on ProviderRegistry
</test_plan>

Tests live in: `apps/gateway/tests/provider_seam/` · `apps/gateway/tests/provider_seam/conftest.py` · `apps/gateway/tests/provider_seam/test_provider_seam.py`

Expected red/green at spec phase (before BUILD):
  - PS1, PS2, PS4: RED for ImportError (ProviderRegistry / select_provider absent)
  - PS3, PS10, PS10b: RED for Settings.openai_api_key field absent → pydantic ValidationError
                       OR ImportError on ProviderRegistry / OpenAIDirectProvider
  - PS5, PS7: RED for ModelRow missing modality/provider columns → AttributeError at insert/assertion
  - PS8: RED for ImportError (OpenAIDirectProvider absent)
  - PS6: may pass trivially (FastAPI ignores unknown body fields) — GREEN-BY-DESIGN smoke test
  - PS9: RED for AttributeError (app.state.provider_registry not set by create_app)
  All failures are for the RIGHT reason — the module/fields simply don't exist yet.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  1. NEVER log, echo, or store openai_api_key in any observable form (same rule as openrouter_api_key).
  2. Chat path MUST NOT be touched: no changes to proxy/api/router.py, proxy/api/deps.py,
     proxy/application/use_cases.py, or proxy/infrastructure/openrouter_upstream.py.
  3. Migration MUST be additive (ADD COLUMN with server_default); no UPDATE statements; no
     data migration; downgrade MUST drop the two columns only.
  4. UpstreamProvider Protocol MUST be runtime_checkable; OpenAIDirectProvider MUST satisfy
     isinstance(x, UpstreamProvider) at runtime.
  5. select_provider MUST be a pure function (or ProviderRegistry method) — explicit dict
     lookup; no inspect.signature, no hasattr dispatch.

Code lives in:
  - `apps/gateway/src/gateway/proxy/domain/ports.py` (UpstreamProvider Protocol — additive)
  - `apps/gateway/src/gateway/proxy/infrastructure/provider_registry.py` (ProviderRegistry + select_provider)
  - `apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py` (OpenAIDirectProvider)
  - `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream_provider.py` (OpenRouterUpstreamFacade)
  - `apps/gateway/src/gateway/catalog/infrastructure/orm.py` (ModelRow modality+provider)
  - `apps/gateway/src/gateway/catalog/domain/entities.py` (CatalogModel/ModelRow modality+provider)
  - `apps/gateway/src/gateway/catalog/infrastructure/openai_seed.py` (OPENAI_SEED_MODELS constant)
  - `apps/gateway/src/gateway/core/config.py` (openai_api_key + openai_base_url)
  - `apps/gateway/src/gateway/core/error_catalog.py` (PROVIDER_UNAVAILABLE)
  - `apps/gateway/src/gateway/main.py` (ProviderRegistry wiring in create_app)
  - `apps/gateway/migrations/versions/<new_rev>_catalog_modality_provider.py` (Alembic migration)

Constraints: do NOT change any test or the contract; allow-list packages only (httpx is already
listed; no new dependencies); ask if unclear. Do NOT touch proxy/api/router.py,
proxy/api/deps.py, proxy/application/use_cases.py, or proxy/infrastructure/openrouter_upstream.py.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — app.state.provider_registry set by create_app(); contains correct entries
      per settings; completion_upstream unchanged (v6 chat path verified byte-identical)
- [ ] DEAD-CODE (code) — all three UpstreamProvider methods exercised by endpoint task tests
      (PS8 covers post_json; post_multipart and stream_bytes covered at endpoint task build)
- [ ] SEMANTIC (prose) — §3 chat-untouched boundary, additive-supersession block, and
      PROVIDER_UNAVAILABLE error flow read in full and confirmed against implementation

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>
Reviewed by: <name> · date: <date>

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
  - `gateway_provider_unavailable_total{provider}` (if added in a follow-up) — spike means
    a key was removed from environment or provider is mis-configured
  - Chat path 5xx rate should not change after this milestone (registry is additive)
  - ERR_PROVIDER_UNAVAILABLE 503 rate on non-chat endpoints → indicates missing key config

Spec delta for the next loop:
  - If endpoint tasks need a fourth UpstreamProvider method, extend Protocol additively here.
  - If per-model circuit breakers are needed for OpenAI, introduce a ProviderCircuitBreakerRegistry.
  - If modality routing strategies (latency/cost across multiple providers) become needed,
    the select_provider function becomes a strategy seam — extend at that point.

### Competency deltas
- [DDD · open] Modality is a domain concept stored as TEXT with a Literal type alias —
  not a DB ENUM. This avoids ALTER TYPE migrations for future modality additions.
  Evidence: the five modality values are bounded but may grow (e.g. "video" in a future slice).
- [ADD · open] The chat-untouched boundary is a cross-cutting invariant that cannot be
  enforced by the type system alone — it requires explicit "do NOT use get_completion_upstream
  from non-chat endpoints" constraints in downstream task contracts.
  Evidence: §3 inviolable boundary note + PS9 wiring regression.
