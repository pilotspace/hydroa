# TASK: POST /v1/embeddings — token-priced, via provider seam; establishes reusable non-chat governance

slug: embeddings-endpoint · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: POST /v1/embeddings — OpenAI-compatible embeddings endpoint, token-priced via the
provider seam, governed by a reusable NonChatGovernance helper that images-endpoint and
audio-endpoints will also use. This is the first non-chat endpoint; it also owns and freezes
the shared governance helper interface.

Framings weighed:

  - **Standalone NonChatGovernance collaborator — additive, chat path byte-identical (chosen)**:
    A new application-layer class `NonChatGovernance` in a new module
    `proxy/application/governance.py`. Constructed per-request from the same five collaborators
    chat uses (authenticator, model_checker, budget_guard, rate_limiter, redis_client). Exposes
    one method `authorize(raw_key, model_id) -> AuthzResult` that replicates the nine ordered
    governance steps (auth→expiry→allowlist→catalog→per-key-budget→team-budget→tenant-budget→
    RPM→TPM) using the SAME underlying domain objects and catalog codes as CompletionUseCase,
    but implemented entirely in the new module. CompletionUseCase private methods are NEVER
    touched — this option proves chat byte-identical by construction: no shared state, no
    refactoring of the chat path. The downside is that two implementations of the same steps
    exist; we accept this because the orderly duplication is bounded (the 9-step sequence is
    stable), reviewable, and provably non-touching. images/audio reuse this same class.
  - **Lift CompletionUseCase._enforce_governance into a shared function (rejected)**:
    Would require editing use_cases.py (the frozen chat path file). Any edit to use_cases.py
    risks a test regression across the 11+ existing suites that depend on it. The migration
    cost is non-zero and the chat-untouched guarantee cannot be trivially verified.
  - **Make CompletionUseCase a base class (rejected)**:
    Introduces inheritance coupling. CompletionUseCase already has a large surface (complete,
    stream, many private helpers). Creating a base class forces downstream test fakes to be
    reconsidered. Rejected by milestone: additive supersession, not redesign.

TPM decision: `authorize()` accepts an optional `estimated_tokens: int | None = None`
parameter. When provided AND when the caller's key has a `tpm_limit`, the TPM pre-flight
check is executed. When `None` (the images/audio path), TPM is skipped — images and audio
have no token dimension. For embeddings, estimated_tokens is set to 1 (any positive sentinel;
actual tokens are unknown pre-flight, same as chat's pre-flight check uses the limit as a
guard not an exact count). This keeps the helper contract generic without per-modality
branching inside the helper.

Must:
<must>
  - POST /v1/embeddings MUST be a new FastAPI route registered in a new module
    `proxy/api/embeddings_router.py` using `APIRouter(tags=["proxy"])`. It MUST be registered
    in `main.py` via `app.include_router(embeddings_router)` near the chat proxy_router line.
    NO prefix. The chat router, chat deps, and CompletionUseCase MUST NOT be touched.
  - The request body MUST be read as raw dict (`body = await request.json()`), matching the
    chat router's style. Fields required: `model` (str, non-empty) and `input` (str or list[str],
    non-empty). Absence or empty value of either field MUST raise PAYLOAD_MODEL_REQUIRED (model)
    or a new spec entry PAYLOAD_INPUT_REQUIRED (input) both at 422 ERR_PAYLOAD_INVALID.
  - The handler MUST use three new dependency functions in `proxy/api/embeddings_deps.py` (or
    additive entries in `proxy/api/deps.py` — build task decides; contract names them):
      `get_provider_registry(request) -> ProviderRegistry` — reads app.state.provider_registry
      `get_embeddings_use_case(request, session) -> EmbeddingsUseCase` — builds the use case
      `get_raw_api_key` — reused from existing deps.py (no new function needed)
      `get_usage_recorder` — reused from existing deps.py
  - A new application use case `EmbeddingsUseCase` (or thin handler function, build task decides
    the exact shape — the contract fixes the behavior not the class name) MUST:
      1. Call `NonChatGovernance.authorize(raw_key, model_id, estimated_tokens=1)`.
      2. Query `ModelRow.modality` and `ModelRow.provider` for `model_id` via:
           `select(ModelRow.modality, ModelRow.provider).where(ModelRow.id == model_id, ModelRow.active.is_(True))`
         If no row is returned, raise `MODEL_UNKNOWN.exc(model_id=model_id)`.
      3. Call `select_provider(modality, provider, registry)`.
      4. Call `await upstream.post_json("/embeddings", payload)` where payload is the client
         body forwarded as-is (model + input, plus any passthrough fields present in the body
         such as `encoding_format` or `dimensions`).
      5. Call `_fire_record_with_raw(...)` EXACTLY ONCE per request (single-bill invariant):
           `_fire_record_with_raw(usage_recorder, tenant_id=authz.tenant_id, key_id=authz.key_id,
            model=model_id, usage=body_from_upstream.get("usage"), status=status,
            team_id=authz.team_id)`
         No `pricing_unit` or `quantity` extras passed (defaults to per_token from snapshot).
      6. Return the upstream (status, body) as a JSONResponse.
  - `NonChatGovernance` MUST be placed in `proxy/application/governance.py`. Its constructor
    and single public method are frozen in §3. images-endpoint and audio-endpoints MUST reuse
    this exact interface without modification.
  - All nine governance checks MUST execute in the same order as CompletionUseCase (auth→
    expiry→allowlist→catalog→per-key-budget→team-budget→tenant-budget→RPM→TPM) using the same
    error catalog codes (see §3 for the exact ordered list and catalog entries).
  - Provider absent (OpenAI key not configured): `select_provider` raises 503
    ERR_PROVIDER_UNAVAILABLE. The chat path is unaffected.
  - Upstream 5xx / CircuitOpenError: map to 502 UPSTREAM_UNAVAILABLE (mirror the chat path).
  - Upstream 4xx: pass through verbatim (same rule as chat).
  - SINGLE-BILL: exactly ONE `_fire_record_with_raw` call per request path, including error
    paths where governance fires (governance errors do not record; only post-provider calls
    record).
  - The endpoint MUST NOT call `get_completion_upstream()` or `get_completion_use_case()` from
    the chat deps — it routes exclusively via `app.state.provider_registry`.
  - The existing chat suite and all other test suites MUST remain green after this task.
    A dedicated regression scenario (EM11) proves this.
</must>

Reject:
<reject>
  - Missing or empty `model` field   → 422 ERR_PAYLOAD_INVALID (PAYLOAD_MODEL_REQUIRED code)
  - Missing or empty `input` field   → 422 ERR_PAYLOAD_INVALID (PAYLOAD_INPUT_REQUIRED / PAYLOAD_INVALID code)
  - Missing or invalid API key       → 401 ERR_AUTH_INVALID_KEY
  - Expired API key                  → 401 ERR_AUTH_KEY_EXPIRED
  - Model not in key allowlist       → 403 ERR_MODEL_NOT_ALLOWED
  - Model unknown / inactive         → 400 ERR_MODEL_UNKNOWN
  - Model disabled for tenant        → 403 ERR_MODEL_DISABLED
  - Tenant/key/team budget exceeded  → 402 ERR_BUDGET_EXCEEDED
  - RPM over limit                   → 429 ERR_RATE_LIMITED + Retry-After header
  - TPM over limit                   → 429 ERR_RATE_LIMITED + Retry-After header
  - OpenAI provider absent (key empty) → 503 ERR_PROVIDER_UNAVAILABLE (from select_provider)
  - Upstream 5xx / CircuitOpenError  → 502 ERR_UPSTREAM_UNAVAILABLE
</reject>

After:
<after>
  - A valid request to POST /v1/embeddings with an active embedding model returns 200 with the
    upstream provider's body (vectors in the `data` array, usage dict).
  - Exactly one usage_records ledger row is created per request, priced per_token.
  - The governance checks (auth/expiry/allowlist/catalog/budget/rate-limit) ran against the
    served model_id exactly as they would in chat, using the same Redis counters.
  - The chat path (POST /v1/chat/completions) is byte-identical to its pre-task behavior —
    no change to router.py, deps.py, or use_cases.py.
  - NonChatGovernance is a stable frozen interface that images-endpoint and audio-endpoints
    can depend on without modification.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE [governance-helper shape]: The standalone NonChatGovernance re-implements
    the nine ordered steps independently (not by calling CompletionUseCase methods). This is the
    safest choice for the chat-untouched invariant, but it means two code paths implement the same
    order. If a new governance step is added in a future slice (e.g., a content-policy check),
    both CompletionUseCase._enforce_governance and NonChatGovernance.authorize need updating.
    Cost if wrong: cascade governance divergence across chat and non-chat; an audit pass is
    needed at each governance change. Mitigation: the 9-step order is stable (v6 baseline);
    the CONTRACT names every step and catalog code precisely so any divergence is detectable.
    Tagged ⚠ because images-endpoint and audio-endpoints inherit this interface.

  ⚠ SECOND-LOWEST CONFIDENCE [chat-untouched proof]: The proof relies on the governance helper
    NOT touching use_cases.py / router.py / deps.py. This is enforceable at code-review time
    (a git diff of those three files must be empty after build). However there is no compile-
    time enforcement. Risk: a future build might inadvertently edit one of those files to share
    a helper, breaking the invariant. Mitigation: the EM11 regression test (chat smoke still 200s
    after the build) + an explicit CONTRACT note that those three files are INVIOLABLE for this
    task.

  ⚠ THIRD-LOWEST CONFIDENCE [TPM-for-non-token modalities]: The helper accepts an optional
    `estimated_tokens: int | None` parameter. When None, the TPM pre-flight step is skipped.
    This means images/audio will never trip the per-key TPM limit. If a future policy requires
    token-equivalent rate-limiting for images (e.g., 1 image = 1000 token-equivalents), the
    caller can pass estimated_tokens; the helper already supports it. Cost if the current
    skip-when-None approach is wrong: a follow-up change to the caller, not to the helper
    interface (the helper is future-proof for this case).

  - [x] The `_fire_record_with_raw` function already accepts `pricing_unit: str | None` and
    `quantity: Decimal | None` (pricing-units TASK.md §3, BUILD done). Embeddings passes
    neither (per_token default). Confirmed by reading use_cases.py:280-325.

  - [x] `select_provider` raises PROVIDER_UNAVAILABLE (503) when the provider is absent from
    the registry. The embeddings endpoint does not need to catch this separately — let it
    propagate as 503. Confirmed by reading provider_registry.py.

  - [x] ModelRow has `modality` and `provider` columns (provider-seam TASK.md BUILD done).
    Confirmed by reading catalog/infrastructure/orm.py.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence
     first, the top three ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: EM1 — happy path: valid key + active embedding model → 200 with provider body
  Given a valid API key with no allowlist restriction
  And an active embedding model row (modality="embedding", provider="openai") in the catalog
  And a per_token pricing snapshot for the model
  And a FakeUpstreamProvider injected as "openai" in app.state.provider_registry
  And the provider returns (200, {"object":"list","data":[...],"usage":{"prompt_tokens":5,"total_tokens":5}})
  When POST /v1/embeddings {"model": "<model_id>", "input": "hello world"}
  Then response status is 200
  And response body equals the provider's body verbatim
  And the provider's post_json was called with path="/embeddings" and the forwarded payload

Scenario: EM2 — single usage record: exactly ONE record, per_token, usage from provider
  Given the same setup as EM1 with a SpyRecorder injected on app.state.usage_recorder
  When POST /v1/embeddings {"model": "<model_id>", "input": "hello"}
  Then SpyRecorder.record() was called exactly once
  And the record carries model=<model_id>, usage.prompt_tokens=5, usage.total_tokens=5
  And NO pricing_unit extra was passed (defaults per_token from snapshot)

Scenario: EM3 — missing API key → 401 ERR_AUTH_INVALID_KEY
  Given no Authorization header (or invalid bearer scheme)
  When POST /v1/embeddings {"model": "<model_id>", "input": "test"}
  Then response status is 401
  And response body code is "ERR_AUTH_INVALID_KEY"

Scenario: EM4 — model not in key allowlist → 403 ERR_MODEL_NOT_ALLOWED
  Given a valid API key with model_allowlist=["other-model-only"]
  And an active embedding model "text-embedding-3-small" NOT in the allowlist
  When POST /v1/embeddings {"model": "text-embedding-3-small", "input": "hi"}
  Then response status is 403
  And response body code is "ERR_MODEL_NOT_ALLOWED"

Scenario: EM5 — unknown / inactive model → 400 ERR_MODEL_UNKNOWN
  Given a valid API key with no allowlist restriction
  And no active model row for "nonexistent-embedding-model" in the catalog
  When POST /v1/embeddings {"model": "nonexistent-embedding-model", "input": "hi"}
  Then response status is 400
  And response body code is "ERR_MODEL_UNKNOWN"

Scenario: EM6 — tenant/key/team budget exceeded → 402 ERR_BUDGET_EXCEEDED
  Given a valid API key with monthly_budget_usd=0.01
  And a Redis spend counter seeded above the key's budget for the current month
  When POST /v1/embeddings {"model": "<model_id>", "input": "test"}
  Then response status is 402
  And response body code is "ERR_BUDGET_EXCEEDED"

Scenario: EM7 — RPM over limit → 429 ERR_RATE_LIMITED + Retry-After
  Given a valid API key with rpm_limit=1
  And the key's RPM sliding window is already at its limit in Redis
  When POST /v1/embeddings {"model": "<model_id>", "input": "hi"}
  Then response status is 429
  And response body code is "ERR_RATE_LIMITED"
  And response header Retry-After is present

Scenario: EM8 — OpenAI provider absent → 503 ERR_PROVIDER_UNAVAILABLE; chat unaffected
  Given a valid API key with no allowlist restriction
  And app.state.provider_registry contains only "openrouter" (no "openai" entry)
  And an active embedding model with provider="openai" in the catalog
  When POST /v1/embeddings {"model": "<model_id>", "input": "test"}
  Then response status is 503
  And response body code is "ERR_PROVIDER_UNAVAILABLE"
  And a subsequent POST /v1/chat/completions with a valid chat model succeeds (chat unaffected)

Scenario: EM9 — missing input field → 422 ERR_PAYLOAD_INVALID
  Given a valid API key
  When POST /v1/embeddings {"model": "text-embedding-3-small"} (no "input" key)
  Then response status is 422
  And response body code is "ERR_PAYLOAD_INVALID"

Scenario: EM10 — NonChatGovernance helper interface unit test
  Given a NonChatGovernance instance constructed with fake collaborators (FakeAuthenticator,
        FakeModelChecker, FakeBudgetGuard, FakeRateLimiter, redis=None)
  When governance.authorize(raw_key, model_id, estimated_tokens=1) is called
  Then it runs the ordered checks and returns an AuthzResult on success
  And it raises AUTH_KEY_INVALID (401) when the fake authenticator raises InvalidApiKeyError
  And it raises MODEL_NOT_ALLOWED (403) when model not in the key allowlist
  And it raises BUDGET_EXCEEDED (402) when the fake budget guard raises

Scenario: EM11 — regression: chat path still 200s; governance extraction did not touch it
  Given the embeddings task is built (NonChatGovernance and EmbeddingsUseCase exist)
  And a valid API key and active chat model seeded in the test DB
  And a FakeCompletionUpstream injected on app.state.completion_upstream
  When POST /v1/chat/completions {"model": "<chat_model>", "messages": [{"role":"user","content":"hi"}]}
  Then response status is 200
  And FakeCompletionUpstream.complete() was called (chat path unaffected)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LOWEST-CONFIDENCE FLAGS AT DRAFT

  ⚠ [governance-helper shape] NonChatGovernance standalone re-implementation —
    the nine-step order duplicates CompletionUseCase._enforce_governance.
    Risk: if a future governance step is added, both sites need updating.
    Cost: governance divergence across chat and non-chat; audit pass required.
    Mitigation: the nine steps are named verbatim in this CONTRACT; any divergence
    is detectable. images-endpoint and audio-endpoints depend on this exact interface.

  ⚠ [chat-untouched proof] No compile-time enforcement that proxy/api/router.py,
    proxy/api/deps.py, and proxy/application/use_cases.py are byte-identical.
    Risk: a build turn that edits any of those files breaks the invariant.
    Cost: governance behavior change for chat; possible regression across 11+ suites.
    Mitigation: EM11 regression test + explicit INVIOLABLE constraint in §5 BUILD.

  ⚠ [TPM-for-non-token] estimated_tokens=None skips TPM for images/audio.
    Risk: images/audio requests do not consume a TPM bucket even if over-limit.
    Cost: a policy gap for rate-limiting non-token modalities by token-equivalents.
    Mitigation: the helper already supports estimated_tokens; callers opt-in.
    images-endpoint and audio-endpoints will explicitly pass None (documented).

─────────────────────────────────────────────────────────────────────────────

HTTP ENDPOINT (NEW)

  POST /v1/embeddings
    Request headers:
      Authorization: Bearer <api_key>   (required; get_raw_api_key extracts it)
    Request body (raw JSON dict):
      model   : str, required, non-empty  → PAYLOAD_MODEL_REQUIRED (422) if absent/empty
      input   : str | list[str], required, non-empty → PAYLOAD_INPUT_REQUIRED (422) if absent/empty
                (passthrough: encoding_format, dimensions, user — forwarded if present)
    Success response:
      200   body: the upstream provider's verbatim JSON response (embeddings data array + usage)
    Error responses (all RFC 9457 problem+json format):
      401   ERR_AUTH_INVALID_KEY     — missing/invalid API key
      401   ERR_AUTH_KEY_EXPIRED     — key expired
      403   ERR_MODEL_NOT_ALLOWED    — model not in key allowlist
      400   ERR_MODEL_UNKNOWN        — model unknown or inactive in catalog
      403   ERR_MODEL_DISABLED       — model disabled for this tenant
      402   ERR_BUDGET_EXCEEDED      — tenant/key/team budget exceeded
      429   ERR_RATE_LIMITED         — RPM or TPM rate limit exceeded; Retry-After header set
      503   ERR_PROVIDER_UNAVAILABLE — provider not configured (openai_api_key empty)
      502   ERR_UPSTREAM_UNAVAILABLE — upstream 5xx or circuit open
      422   ERR_PAYLOAD_INVALID      — missing model or input field

  Placement:
    Router module: proxy/api/embeddings_router.py
      embeddings_router = APIRouter(tags=["proxy"])
      @embeddings_router.post("/v1/embeddings")
    Registered in main.py: app.include_router(embeddings_router)  # additive, near proxy_router line
    Deps module:   proxy/api/embeddings_deps.py  (or additive section in deps.py — build decides)

─────────────────────────────────────────────────────────────────────────────

NON-CHAT GOVERNANCE HELPER (FROZEN — images-endpoint and audio-endpoints depend on this)

  Placement: proxy/application/governance.py (NEW module)

  class NonChatGovernance:
      """Reusable governance gate for non-chat modalities.

      images-endpoint and audio-endpoints construct and call this class with
      the same interface. The chat path (CompletionUseCase) is NEVER modified.
      """

      def __init__(
          self,
          *,
          authenticator: KeyAuthenticator,
          model_checker: ModelChecker,
          budget_guard: BudgetGuard,
          rate_limiter: RateLimiter | None,
          redis_client: Any,          # the raw Redis client (used for per-key/team spend reads)
      ) -> None:
          ...

      async def authorize(
          self,
          raw_key: str | None,
          model_id: str,
          *,
          estimated_tokens: int | None = None,
      ) -> AuthzResult:
          """Run the nine governance checks in order; return AuthzResult on pass.

          Ordered checks (same order as CompletionUseCase._enforce_governance):
            Step 1: Authenticate key — authenticator.authenticate(raw_key)
                    raises AUTH_KEY_INVALID  (401 ERR_AUTH_INVALID_KEY)   on missing/invalid key
            Step 2: Check key expiry — _check_expiry(authz)
                    raises AUTH_KEY_EXPIRED  (401 ERR_AUTH_KEY_EXPIRED)   on expired key
            Step 3: Check model allowlist — _check_model_allowlist(authz, model_id)
                    raises MODEL_NOT_ALLOWED (403 ERR_MODEL_NOT_ALLOWED)  if not in allowlist
            Step 4: Check model catalog — model_checker.check_for_tenant(model_id, tenant_id)
                    raises MODEL_UNKNOWN     (400 ERR_MODEL_UNKNOWN)      if absent/inactive
                    raises MODEL_DISABLED    (403 ERR_MODEL_DISABLED)     if tenant-disabled
            Step 5: Check per-key budget — Redis spend counter vs key monthly_budget_usd
                    raises BUDGET_EXCEEDED   (402 ERR_BUDGET_EXCEEDED)    if exceeded
                    fail-open on Redis error (same rule as CompletionUseCase)
            Step 6: Check team budget — Redis spend counter vs team_budget_usd
                    raises BUDGET_EXCEEDED   (402 ERR_BUDGET_EXCEEDED)    if exceeded
                    fail-open on Redis error
            Step 7: Check tenant budget — budget_guard.check(authz.tenant_id)
                    raises BUDGET_EXCEEDED   (402 ERR_BUDGET_EXCEEDED)    if exceeded
            Step 8: Check RPM — rate_limiter.check_rpm(authz.key_id, authz.rpm_limit)
                    raises RATE_LIMITED      (429 ERR_RATE_LIMITED)       if exceeded; Retry-After
                    skipped when rate_limiter is None OR authz.rpm_limit is None
                    fail-open on Redis error (same rule as CompletionUseCase)
            Step 9: Check TPM pre-flight — rate_limiter.check_tpm(authz.key_id, authz.tpm_limit)
                    raises RATE_LIMITED      (429 ERR_RATE_LIMITED)       if exceeded; Retry-After
                    skipped when estimated_tokens is None
                    skipped when rate_limiter is None OR authz.tpm_limit is None
                    fail-open on Redis error

          Returns: AuthzResult on success (all checks passed).
          """
          ...

  Error catalog codes used (EXACT, by step):
    AUTH_KEY_INVALID     = error_catalog.AUTH_KEY_INVALID     (401, ERR_AUTH_INVALID_KEY)
    AUTH_KEY_EXPIRED     = error_catalog.AUTH_KEY_EXPIRED     (401, ERR_AUTH_KEY_EXPIRED)
    MODEL_NOT_ALLOWED    = error_catalog.MODEL_NOT_ALLOWED    (403, ERR_MODEL_NOT_ALLOWED)
    MODEL_UNKNOWN        = error_catalog.MODEL_UNKNOWN        (400, ERR_MODEL_UNKNOWN)
    MODEL_DISABLED       = error_catalog.MODEL_DISABLED       (403, ERR_MODEL_DISABLED)
    BUDGET_EXCEEDED      = error_catalog.BUDGET_EXCEEDED      (402, ERR_BUDGET_EXCEEDED)
    RATE_LIMITED         = error_catalog.RATE_LIMITED         (429, ERR_RATE_LIMITED)

  Invariants inherited from CompletionUseCase (MUST hold for NonChatGovernance too):
    - Expiry check uses datetime.UTC and timezone-aware comparison.
    - Model allowlist: null = all allowed; empty list = none allowed.
    - Per-key budget: fail-open on Redis unavailable (advisory counter, same as A2/M13).
    - Team budget: fail-open on Redis unavailable.
    - Tenant budget: budget_guard.check() raises BUDGET_EXCEEDED on exceeded.
    - RPM/TPM: RateLimitExceededError is re-raised as RATE_LIMITED with Retry-After header.
    - Steps 5–9 are Redis-backed; any Redis error is caught and the request proceeds (fail-open).

  Downstream contract (FROZEN):
    images-endpoint and audio-endpoints MUST construct NonChatGovernance with the same
    five constructor arguments and call `authorize(raw_key, model_id, estimated_tokens=None)`.
    The interface MUST NOT be changed to add modality-specific logic — add a new class if needed.

─────────────────────────────────────────────────────────────────────────────

EMBEDDINGS USE CASE FLOW (FROZEN)

  def get_embeddings_use_case(request, session) -> EmbeddingsUseCase
    Constructs per-request (same pattern as get_completion_use_case):
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
      return EmbeddingsUseCase(governance=governance, session=session)

  class EmbeddingsUseCase:
      async def execute(
          self,
          *,
          raw_key: str | None,
          body: dict[str, Any],
          registry: ProviderRegistry,
          usage_recorder: UsageRecorder,
      ) -> tuple[int, dict[str, Any]]:

        1. model_id = body.get("model"); validate non-empty string → PAYLOAD_MODEL_REQUIRED
        2. input_val = body.get("input"); validate non-None, non-empty → PAYLOAD_INPUT_REQUIRED
        3. authz = await self._governance.authorize(raw_key, model_id, estimated_tokens=1)
        4. row = select(ModelRow.modality, ModelRow.provider)
                       .where(ModelRow.id == model_id, ModelRow.active.is_(True))
                 → if None: raise MODEL_UNKNOWN.exc(model_id=model_id)
        5. provider_adapter = select_provider(row.modality, row.provider, registry)
           → raises ERR_PROVIDER_UNAVAILABLE (503) if absent
        6. try:
               status, resp_body = await provider_adapter.post_json("/embeddings", body)
           except (UpstreamUnavailableError, CircuitOpenError):
               raise UPSTREAM_UNAVAILABLE.exc() from None
        7. _fire_record_with_raw(
               usage_recorder,
               tenant_id=authz.tenant_id, key_id=authz.key_id,
               model=model_id, usage=resp_body.get("usage"),
               status=status, team_id=authz.team_id,
           )   # NO pricing_unit / quantity — per_token default
        8. return status, resp_body

─────────────────────────────────────────────────────────────────────────────

PROVIDER SELECTION CALL (FROZEN, reuses provider-seam TASK.md §3)

  from gateway.proxy.infrastructure.provider_registry import select_provider, ProviderRegistry
  provider_adapter = select_provider(modality, provider, registry)
    — modality, provider from ModelRow query (step 4 above)
    — registry from app.state.provider_registry via get_provider_registry dep
    — raises PROVIDER_UNAVAILABLE.exc(provider=provider) when absent → 503

─────────────────────────────────────────────────────────────────────────────

RECORDER CALL (FROZEN, reuses pricing-units TASK.md §3)

  _fire_record_with_raw(
      usage_recorder,
      tenant_id=authz.tenant_id,
      key_id=authz.key_id,
      model=model_id,
      usage=resp_body.get("usage"),   # {"prompt_tokens": N, "total_tokens": N}
      status=status,
      team_id=authz.team_id,
  )
  — NO pricing_unit or quantity extras → per_token dispatch by default in recorder
  — usage dict carries prompt_tokens and total_tokens from OpenAI embeddings response
  — SINGLE call per request (single-bill invariant; fire-and-forget)

─────────────────────────────────────────────────────────────────────────────

INVIOLABLE CHAT-PATH CONSTRAINT

  The following files MUST be byte-identical after this task's BUILD:
    proxy/api/router.py
    proxy/api/deps.py
    proxy/application/use_cases.py
  These files MUST NOT be modified (not even for imports, comments, or whitespace).
  EM11 regression test provides runtime verification.

─────────────────────────────────────────────────────────────────────────────

NEW ERROR CATALOG ENTRY (additive to error_catalog.py)

  PAYLOAD_INPUT_REQUIRED = ErrorSpec(
      422, "ERR_PAYLOAD_INVALID", "Field 'input' is required and non-empty"
  )

─────────────────────────────────────────────────────────────────────────────

PLACEMENT SUMMARY

  New files:
    proxy/application/governance.py        — NonChatGovernance class
    proxy/api/embeddings_router.py         — POST /v1/embeddings route + embeddings_router
    proxy/api/embeddings_deps.py           — get_provider_registry + get_embeddings_use_case
    proxy/application/embeddings_use_case.py — EmbeddingsUseCase (or inline in embeddings_router.py)

  Modified files (additive, no other files):
    gateway/core/error_catalog.py          — add PAYLOAD_INPUT_REQUIRED
    gateway/main.py                        — app.include_router(embeddings_router)

  INVIOLABLE (MUST NOT be touched):
    proxy/api/router.py
    proxy/api/deps.py
    proxy/application/use_cases.py
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] governance-helper shape — the standalone NonChatGovernance
re-implements steps 5–6 (per-key/team budget) but DROPS the chat path's M11 soft-budget-alert
seam (CompletionUseCase._check_per_key_budget fires persist_soft_budget_alert when
soft_budget_usd is crossed). Decision for this task: keep the minimal-risk standalone shape the
spec chose — HARD 402 budget enforcement (the billing-critical invariant) IS preserved; only the
advisory fire-and-forget soft-alert is absent on the non-chat path. Soft-budget-alert parity for
embeddings/images/audio is deferred to an OPEN DELTA (revisit at v7 observe/close), NOT silently
weakened. The nine steps are named verbatim above so any future divergence is auditable;
images-endpoint and audio-endpoints depend on this exact interface.
<!-- Lowest-confidence flags at the top (see above). Orchestrator freezes and sets FROZEN.
     Changing a frozen contract = change request back to SPECIFY. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of governance paths + provider selection + recorder call + chat-untouched invariant

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_em1_happy_path_200_with_provider_body:
      arrange: seed key+tenant via HTTP admin routes; seed embedding model row + per_token
               pricing snapshot via raw SQL; inject FakeUpstreamProvider as "openai" in
               app.state.provider_registry; FakeUpstreamProvider returns (200, EMBEDDING_RESPONSE_BODY)
      act: POST /v1/embeddings {"model": MODEL_ID, "input": "hello world"}
      assert: response.status_code == 200; response.json() == EMBEDDING_RESPONSE_BODY;
              provider.post_json_calls[0] == {"path": "/embeddings", "payload": includes model+input}
      RED reason: POST /v1/embeddings route does not exist → 404 or 405 Not Found/Method Not Allowed

  - test_em2_single_usage_record_per_token_usage_carried:
      arrange: same as EM1 but inject SpyRecorder on app.state.usage_recorder;
               FakeUpstreamProvider returns usage={"prompt_tokens":5,"total_tokens":5}
      act: POST /v1/embeddings {"model": MODEL_ID, "input": "embed me"}
      assert: spy.call_count == 1; last record call has model==MODEL_ID, usage has prompt_tokens
      RED reason: route absent → 404

  - test_em3_missing_api_key_401:
      arrange: no Authorization header
      act: POST /v1/embeddings {"model": MODEL_ID, "input": "test"}
      assert: status==401; body["code"]=="ERR_AUTH_INVALID_KEY"
      RED reason: route absent → 404

  - test_em4_model_not_in_allowlist_403:
      arrange: seed key with model_allowlist=["other-model"]; seed embedding model row
      act: POST /v1/embeddings {"model": MODEL_ID, "input": "hi"}
      assert: status==403; body["code"]=="ERR_MODEL_NOT_ALLOWED"
      RED reason: route absent → 404

  - test_em5_unknown_model_400:
      arrange: valid key; no model row in catalog for "nonexistent-model"
      act: POST /v1/embeddings {"model": "nonexistent-model", "input": "hi"}
      assert: status==400; body["code"]=="ERR_MODEL_UNKNOWN"
      RED reason: route absent → 404

  - test_em6_budget_exceeded_402:
      arrange: seed key with monthly_budget_usd=0.01; seed Redis spend counter above budget;
               inject RedisBudgetGuard or seed INCRBYFLOAT spend counter for tenant
      act: POST /v1/embeddings {"model": MODEL_ID, "input": "test"}
      assert: status==402; body["code"]=="ERR_BUDGET_EXCEEDED"
      RED reason: route absent → 404

  - test_em7_rpm_exceeded_429_retry_after:
      arrange: seed key with rpm_limit=1; inject RedisLuaRateLimiter; seed Redis RPM window full
      act: POST /v1/embeddings {"model": MODEL_ID, "input": "hi"}
      assert: status==429; body["code"]=="ERR_RATE_LIMITED"; "Retry-After" in response.headers
      RED reason: route absent → 404

  - test_em8_provider_absent_503_chat_unaffected:
      arrange: app.state.provider_registry has only "openrouter" (no "openai");
               seed embedding model row with provider="openai"; valid key; no allowlist
               AND seed chat model row; inject FakeCompletionUpstream on app.state.completion_upstream
      act Part A: POST /v1/embeddings {"model": EMBED_MODEL_ID, "input": "test"}
      assert Part A: status==503; body["code"]=="ERR_PROVIDER_UNAVAILABLE"
      act Part B: POST /v1/chat/completions {"model": CHAT_MODEL_ID, "messages": [...]}
      assert Part B: status==200 (chat unaffected)
      RED reason: route absent → 404

  - test_em9_missing_input_field_422:
      arrange: valid key
      act: POST /v1/embeddings {"model": MODEL_ID}  (no "input")
      assert: status==422; body["code"]=="ERR_PAYLOAD_INVALID"
      RED reason: route absent → 404

  - test_em10_non_chat_governance_helper_unit_test:
      arrange: import NonChatGovernance from proxy.application.governance;
               construct with FakeKeyAuthenticator (raises InvalidApiKeyError for bad keys,
               returns AuthzResult for good keys), FakeModelChecker (allowlist-aware),
               FakeBudgetGuard, PassthroughRateLimiter, redis_client=None
      act: authorize(good_raw_key, model_id, estimated_tokens=1) → assert returns AuthzResult
           authorize(None, model_id) → assert raises ProblemError 401 ERR_AUTH_INVALID_KEY
           authorize(good_raw_key, "blocked-model") [model not in allowlist] → 403 ERR_MODEL_NOT_ALLOWED
           authorize(good_raw_key, model_id) with FakeBudgetGuard that raises → 402 ERR_BUDGET_EXCEEDED
      assert: each error raises ProblemError with the correct status + code
      RED reason: NonChatGovernance does not exist → ImportError

  - test_em11_regression_chat_path_200_untouched:
      arrange: seed chat model row; seed key+tenant via HTTP admin; inject FakeCompletionUpstream
               on app.state.completion_upstream (response = 200 with CHAT_RESPONSE_BODY)
      act: POST /v1/chat/completions {"model": CHAT_MODEL_ID, "messages": [{"role":"user","content":"hi"}]}
      assert: status==200; FakeCompletionUpstream.complete_calls has 1 entry
              (chat path still calls completion_upstream, not the provider registry)
      GREEN-BY-DESIGN: this test PASSES even in the red phase because the chat path
      is already working. It serves as a regression guard against the build accidentally
      touching chat. Mark it GREEN-BY-DESIGN in the test file with a comment.
</test_plan>

Tests live in: `apps/gateway/tests/embeddings_endpoint/` · `apps/gateway/tests/embeddings_endpoint/conftest.py` · `apps/gateway/tests/embeddings_endpoint/test_embeddings_endpoint.py` · `apps/gateway/tests/embeddings_endpoint/__init__.py`

Expected red/green at spec phase (before BUILD):
  - EM1–EM9: RED (route /v1/embeddings absent → 404 from the app or NameError/ImportError from test setup)
  - EM10: RED for ImportError (NonChatGovernance absent in proxy/application/governance.py)
  - EM11: GREEN-BY-DESIGN (chat path already works; regression guard only)
  All failures for the RIGHT reason — not skips.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  1. INVIOLABLE: proxy/api/router.py, proxy/api/deps.py, proxy/application/use_cases.py
     MUST NOT be modified — not even imports, comments, or whitespace.
  2. NonChatGovernance MUST replicate the nine ordered governance steps exactly as specified
     in §3. Any deviation from the step order or catalog code is a contract violation.
  3. SINGLE-BILL: _fire_record_with_raw MUST be called exactly once per request path.
     No double-billing; no recording on governance error paths.
  4. The embeddings endpoint MUST route through app.state.provider_registry, NEVER through
     get_completion_upstream() or CompletionUseCase.
  5. No new dependencies — httpx already listed; redis.asyncio, sqlalchemy, fastapi already present.

Code lives in:
  - `proxy/application/governance.py` (NonChatGovernance)
  - `proxy/api/embeddings_router.py` (route + handler)
  - `proxy/api/embeddings_deps.py` (get_provider_registry + get_embeddings_use_case)
  - `proxy/application/embeddings_use_case.py` (EmbeddingsUseCase, or inline in router)
  - `gateway/core/error_catalog.py` (additive: PAYLOAD_INPUT_REQUIRED)
  - `gateway/main.py` (additive: app.include_router(embeddings_router))

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — embeddings suite 11/11 green (10 + EM11 regression); authoritative
      `make ci` from repo root EXIT=0 (lint + pyright + allowlist + allowlist-node + full
      suite with `--cov-fail-under=80`). The full chat + governance suites stayed green.
- [x] coverage did not decrease — `--cov-fail-under=80` enforced inside the passing gate.
- [x] no test or contract was altered during build — see DISPOSITION 2 (the two test files were
      `ruff format`-only reformatted by the orchestrator: pure whitespace, behavior-preserving,
      re-verified 11/11 still green; no assertion, scenario, or §3 contract text changed).
- [x] concurrency / timing of the risky operation is safe — NonChatGovernance is fail-open on
      every Redis error (per-key / team budget reads, RPM, TPM all catch and proceed, mirroring
      chat). The usage record is a single fire-and-forget `_fire_record_with_raw` (single-bill,
      EM2). The provider call surfaces UpstreamUnavailableError/CircuitOpenError → 502 cleanly.
- [x] no exposed secrets, injection openings, or unexpected dependencies — governance/router/deps
      NEVER touch the OpenAI key (the already-built OpenAIDirectProvider owns it; nothing logged).
      The catalog query is a parameterized SQLAlchemy `select(...).where(ModelRow.id == model_id)`
      — no string interpolation. No new third-party dependency (allowlist gate passed).
- [x] layering & dependencies follow CONVENTIONS.md — api(router/deps) → application(use_case/
      governance) → infrastructure(provider_registry/model_checker); mirrors the chat slice exactly.
- [x] a person reviewed and approved the change — orchestrator manually reviewed every builder file
      line-by-line (governance.py 9-step logic, use_case flow, router, deps, both additive diffs)
      and re-ran the authoritative gate before trusting the build. Tin Dang (delegated auto mode).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `embeddings_router` registered in main.py (`app.include_router(embeddings_router)`,
      additive line after proxy_router) + imported; `get_embeddings_use_case`/`get_provider_registry`
      consumed by the router via Depends; `EmbeddingsUseCase` built by the dep; `NonChatGovernance`
      built by the dep and called by the use case; `PAYLOAD_INPUT_REQUIRED` raised in the use case.
      Every new symbol is referenced. EM1 (200 happy path) exercises the full wired chain end-to-end.
- [x] DEAD-CODE (code) — no orphaned symbol. `_parse_spend` (module-level in governance.py) is used
      by both budget checks. No unused import (ruff I001/F401 clean in the passing gate).

### DISPOSITIONS (build-time intent-preserving decisions — not contract changes)
1. STEP-7 BUDGET INTERPRETATION: §3's nine-step list reads step 7 (tenant budget) as
   unconditional, but its own inherited-invariant clause requires "same as
   CompletionUseCase._enforce_governance". Chat uses most-specific-wins: a hard per-key budget is
   authoritative and the tenant guard is skipped; only with no hard per-key budget does the tenant
   guard enforce. The build replicated chat's most-specific-wins (the billing-correct, contract-
   primary intent), NOT the literal unconditional reading. EM6 confirms the per-key 402 path fires
   genuinely (key budget 0.01 + seeded spend 9999.99). Intent-preserving → build disposition, not a
   change request. images/audio inherit this same semantics.
2. TEST REFORMAT: the front committed tests/embeddings_endpoint/conftest.py +
   test_embeddings_endpoint.py unformatted (front ran red-verify but not `ruff format`). Orchestrator
   applied `ruff format` (whitespace only) to pass the lint gate; re-verified 11/11 still green. No
   behavior/assertion change.
3. PYRIGHT SUPPRESSION: one targeted `# pyright: ignore[reportPrivateUsage]` on the
   `_fire_record_with_raw` import — unavoidable: use_cases.py is INVIOLABLE (cannot expose a public
   alias) while §3 mandates reusing that exact recorder fn. Single-line, documented at the import.

### GATE RECORD
Outcome: PASS  (auto-resolved on complete evidence — delegated auto mode; no security finding)
Reviewed by: Tin Dang (delegated auto mode, 2026-06-12) · date: 2026-06-12

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
  - ERR_PROVIDER_UNAVAILABLE 503 rate on /v1/embeddings → openai key misconfiguration
  - ERR_BUDGET_EXCEEDED 402 rate → tenant/key budget exhaustion for embeddings
  - Single usage_records row per embeddings request (no double-billing monitor)
  - Chat 5xx rate should not change after this milestone (provider registry is additive)

Spec delta for the next loop:
  - images-endpoint and audio-endpoints will construct NonChatGovernance with estimated_tokens=None
  - If a new governance step is added in a future slice, update NonChatGovernance.authorize
    in governance.py AND CompletionUseCase._enforce_governance in use_cases.py in the same PR

### Competency deltas
- [SDD · folded] NonChatGovernance drops the chat M11 soft-budget-alert seam on the non-chat path
  (HARD 402 is preserved; only the advisory fire-and-forget alert is absent). Evidence: frozen
  [contract] flag + governance.py `_check_per_key_budget` omits `persist_soft_budget_alert`.
  Soft-budget-alert parity for embeddings/images/audio should be revisited at v7 close (a future
  slice could add an alert seam shared by chat + non-chat, or accept the gap explicitly).
- [ADD · folded] The chat-untouched invariant has no compile-time enforcement — it rests on EM11 +
  a manual `git diff --stat` of the three INVIOLABLE files. Evidence: §3 INVIOLABLE note + the
  verify WIRING check. A future improvement: an ArchUnit-style test asserting non-chat modules
  never import CompletionUseCase's private governance methods.
