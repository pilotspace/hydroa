# TASK: POST /v1/images/generations — per-image priced, via provider seam, reuses NonChatGovernance

slug: images-endpoint · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: POST /v1/images/generations — OpenAI-compatible image generation endpoint, priced
per returned image, routed to the direct provider via the provider seam, governed by the ALREADY-BUILT
NonChatGovernance helper (estimated_tokens=None → TPM skipped). Billed ONCE per request with
quantity = number of images in the upstream response data array (len(data); 0 when the data
array is absent or empty — never over-bill a failed/empty response).

Framings weighed:

  - **ImagesUseCase mirroring EmbeddingsUseCase — additive, chat path byte-identical (chosen)**:
    A new application-layer class `ImagesUseCase` in a new module
    `proxy/application/images_use_case.py`. Constructed per-request from the same five collaborators
    NonChatGovernance uses. Reuses the FROZEN `NonChatGovernance` interface exactly
    (estimated_tokens=None → TPM step skipped — images have no token dimension). Reuses
    `_fire_record_with_raw` from use_cases.py with pricing_unit="per_image" and
    quantity=Decimal(n_images). The chat path (CompletionUseCase) and embeddings path
    (EmbeddingsUseCase / NonChatGovernance) are NEVER modified.
    Chosen: mirrors the established embeddings pattern precisely; risk bounded; NonChatGovernance
    already handles the None-estimated_tokens TPM-skip; no new abstraction needed.

  - **Inline handler (no use case class) — rejected**:
    Keeps logic in the router. Violates the layering convention (api layer must not contain
    application logic) established by embeddings-endpoint. Harder to unit-test the billed-quantity
    resolution logic in isolation. Rejected in favor of consistency with the embeddings shape.

  - **Reuse EmbeddingsUseCase with modality dispatch — rejected**:
    Would require adding a modality branch inside EmbeddingsUseCase, adding coupling to an already-
    frozen class. The recorder call for images differs from embeddings (per_image + quantity vs
    per_token + usage dict). Rejected: the frozen EmbeddingsUseCase contract must not be touched.

Billed-quantity resolution (BINDING — resolved at freeze, NO fallback):
  The quantity billed is exactly the number of images the upstream returned:
    n_images = len(resp_body.get("data", []))
  Present, non-empty data → bill that count. Absent or length-0 → bill 0 (the recorder yields
  cost 0). The drafted requested-n fallback was DROPPED at freeze: charging requested-n on an
  empty/absent data array bills for images that were never produced (over-bill on failure). On a
  real OpenAI success the data array always carries n entries, so accurate billing is preserved.
  This is consistent with chat's "bill what was consumed" (a no-usage chat records cost 0).

Must:
<must>
  - POST /v1/images/generations MUST be a new FastAPI route registered in a new module
    `proxy/api/images_router.py` using `APIRouter(tags=["proxy"])`. It MUST be registered
    in `main.py` via `app.include_router(images_router)` near the chat proxy_router line.
    NO prefix. The chat router, chat deps, embeddings router, embeddings deps, CompletionUseCase,
    EmbeddingsUseCase, and NonChatGovernance MUST NOT be touched.
  - The request body MUST be read as raw dict (`body = await request.json()`), matching the
    chat/embeddings router style. Fields required: `model` (str, non-empty) and `prompt`
    (str, non-empty). Absence or empty value of `model` → PAYLOAD_MODEL_REQUIRED (422,
    ERR_PAYLOAD_INVALID). Absence or empty value of `prompt` → PAYLOAD_PROMPT_REQUIRED (422,
    ERR_PAYLOAD_INVALID) — a NEW catalog entry declared in the WIRING DECLARATION block; the
    BUILD task adds it to error_catalog.py.
    Passthrough fields forwarded as-is if present: n, size, quality, style,
    response_format, user.
  - The handler MUST use dependency functions in a new `proxy/api/images_deps.py`:
      `get_provider_registry(request) -> ProviderRegistry` — reads app.state.provider_registry
        (MAY import+reuse from embeddings_deps.py OR define a new identical function; build decides)
      `get_images_use_case(request, session) -> ImagesUseCase` — builds the use case
      `get_raw_api_key` — reused from existing deps.py (no new function needed)
      `get_usage_recorder` — reused from existing deps.py
  - A new application use case `ImagesUseCase` MUST:
      1. Validate model field → PAYLOAD_MODEL_REQUIRED (422) if absent/empty.
      2. Validate prompt field → PAYLOAD_PROMPT_REQUIRED (422) if absent/empty.
      3. Call `await governance.authorize(raw_key, model_id, estimated_tokens=None)` — NO TPM.
      4. Query `ModelRow.modality` and `ModelRow.provider` for `model_id` via:
           `select(ModelRow.modality, ModelRow.provider).where(ModelRow.id == model_id, ModelRow.active.is_(True))`
         If no row returned → MODEL_UNKNOWN.
      5. Call `select_provider(modality, provider, registry)` → 503 if absent.
      6. Call `await upstream.post_json("/images/generations", body)`.
         Catch (UpstreamUnavailableError, CircuitOpenError) → UPSTREAM_UNAVAILABLE (502).
      7. Compute n_images:
           n_images = len(body_from_upstream.get("data", []))
         (exactly the images the upstream returned; 0 on absent/empty data — no requested-n fallback)
      8. Call `_fire_record_with_raw` EXACTLY ONCE (single-bill invariant):
           `_fire_record_with_raw(usage_recorder, tenant_id=authz.tenant_id, key_id=authz.key_id,
            model=model_id, usage=None, status=status, team_id=authz.team_id,
            pricing_unit="per_image", quantity=Decimal(n_images))`
      9. Return JSONResponse(body, status_code=status).
  - `get_images_use_case` MUST construct NonChatGovernance with the IDENTICAL five-collaborator
    pattern as `get_embeddings_use_case` (same app.state resolution for budget_guard, rate_limiter,
    redis_client). NonChatGovernance is imported from governance.py without modification.
  - `_fire_record_with_raw` MUST be imported from `gateway.proxy.application.use_cases` with
    `# pyright: ignore[reportPrivateUsage]` on that import line (use_cases.py is INVIOLABLE).
  - Provider absent (registry missing "openai") → select_provider raises 503
    ERR_PROVIDER_UNAVAILABLE. The chat path and embeddings path are unaffected.
  - Upstream 5xx / CircuitOpenError → 502 UPSTREAM_UNAVAILABLE (mirror chat and embeddings).
  - Upstream 4xx: pass through verbatim.
  - SINGLE-BILL: exactly ONE `_fire_record_with_raw` call per request path. Governance errors
    do not record; only post-provider calls record.
  - The endpoint MUST NOT call `get_completion_upstream()`, `get_completion_use_case()`, or
    `get_embeddings_use_case()` — it routes exclusively via `app.state.provider_registry`.
  - The existing chat suite, embeddings suite, and all other test suites MUST remain green
    after this task. A dedicated regression scenario (IM10) proves this.
</must>

Reject:
<reject>
  - Missing or empty `model` field   → 422 ERR_PAYLOAD_INVALID (PAYLOAD_MODEL_REQUIRED)
  - Missing or empty `prompt` field  → 422 ERR_PAYLOAD_INVALID (PAYLOAD_PROMPT_REQUIRED)
  - Missing or invalid API key       → 401 ERR_AUTH_INVALID_KEY
  - Expired API key                  → 401 ERR_AUTH_KEY_EXPIRED
  - Model not in key allowlist       → 403 ERR_MODEL_NOT_ALLOWED
  - Model unknown / inactive         → 400 ERR_MODEL_UNKNOWN
  - Model disabled for tenant        → 403 ERR_MODEL_DISABLED
  - Tenant/key/team budget exceeded  → 402 ERR_BUDGET_EXCEEDED
  - RPM over limit                   → 429 ERR_RATE_LIMITED + Retry-After header
  - OpenAI provider absent           → 503 ERR_PROVIDER_UNAVAILABLE (from select_provider)
  - Upstream 5xx / CircuitOpenError  → 502 ERR_UPSTREAM_UNAVAILABLE
</reject>

After:
<after>
  - A valid request to POST /v1/images/generations with an active image model returns 200 with the
    upstream provider's body (data array of image objects).
  - Exactly one usage_records ledger row is created per request, pricing_unit="per_image",
    quantity = number of images in the upstream data array (or requested n if data absent/empty).
  - The governance checks (auth/expiry/allowlist/catalog/budget/rate-limit) ran against the
    served model_id exactly as they would in chat/embeddings, using the same Redis counters.
    TPM is NOT consulted (estimated_tokens=None).
  - The chat path (POST /v1/chat/completions) and embeddings path (POST /v1/embeddings) are
    byte-identical to their pre-task behavior — no change to router.py, deps.py, use_cases.py,
    embeddings_router.py, embeddings_deps.py, embeddings_use_case.py, or governance.py.
  - NonChatGovernance (governance.py) is reused without modification.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE [billed-quantity resolution]: The fall-back from len(data) to requested-n
    is a billing policy decision: when the upstream returns data=[] or omits "data", bill the
    requested n rather than 0. This prevents a zero-charge surprise but could over-bill if the
    provider genuinely returned fewer images than requested (e.g., content policy refusal returns
    {} or {"data":[]}). The CONTRACT pins this resolution precisely: actual-returned wins when
    data is present and non-empty; requested-n (default 1) wins otherwise. If wrong: some
    requests are over-billed by at most n-1 images; a future refinement can inspect the upstream
    error body before billing. Mitigation: the resolution formula is stated verbatim in §3; IM2
    tests the 2-image actual-returned path AND the tests label the formula clearly.

  ⚠ SECOND-LOWEST CONFIDENCE [TPM-skip for images]: estimated_tokens=None is passed to
    NonChatGovernance.authorize, which skips Step 9 (TPM pre-flight). Image requests will
    never trip per-key TPM limits. If a future policy requires image-equivalent TPM limiting
    (e.g., 1 image = 1000 token-equivalents), the caller passes estimated_tokens; the helper
    already supports it. Cost if wrong: images are rate-limited only by RPM, not TPM.
    Mitigation: IM7 explicitly asserts TPM is NOT consulted; the §3 contract names this.

  - [x] NonChatGovernance is ALREADY BUILT at proxy/application/governance.py (embeddings-endpoint
    v6 done). Interface frozen: constructor(5 args) + authorize(raw_key, model_id,
    estimated_tokens=None) -> AuthzResult. Reuse without modification is safe. Confirmed.

  - [x] _fire_record_with_raw already accepts pricing_unit: str|None and quantity: Decimal|None
    (pricing-units TASK.md §3, BUILD done). Per_image path: usage=None, pricing_unit="per_image",
    quantity=Decimal(n_images). Confirmed by reading use_cases.py:280-325.

  - [x] select_provider raises PROVIDER_UNAVAILABLE (503) when the provider is absent from the
    registry. Images endpoint lets it propagate as 503. Confirmed by provider_registry.py.

  - [x] ModelRow has `modality` and `provider` columns. modality='image', provider='openai'
    for dall-e-3. Confirmed by catalog/infrastructure/orm.py and openai_seed.py.

  - [x] PAYLOAD_PROMPT_REQUIRED does not yet exist in error_catalog.py (confirmed by reading it).
    It must be added as a new entry — declared in WIRING DECLARATION; BUILD applies it.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence
     first, the top three ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: IM1 — happy path: valid key + active image model → 200 with provider body
  Given a valid API key with no allowlist restriction
  And an active image model row (modality="image", provider="openai") in the catalog
  And a per_image pricing snapshot for the model (pricing_unit="per_image", unit_usd_per_unit set)
  And a FakeUpstreamProvider injected as "openai" in app.state.provider_registry
  And the provider returns (200, {"created": 1234567890, "data": [{"url": "https://..."}]})
  When POST /v1/images/generations {"model": "<model_id>", "prompt": "a white cat"}
  Then response status is 200
  And response body equals the provider's body verbatim
  And the provider's post_json was called with path="/images/generations" and the forwarded payload

Scenario: IM2 — single usage record per_image, quantity == number of images returned
  Given the same setup as IM1 with a SpyRecorder injected on app.state.usage_recorder
  And the FakeUpstreamProvider is configured to return data with 2 image entries
  When POST /v1/images/generations {"model": "<model_id>", "prompt": "two cats", "n": 2}
  Then SpyRecorder.record() was called exactly once
  And the record carries pricing_unit="per_image" and quantity==Decimal(2)
  And usage=None (images have no token usage)
  And the record carries model=<model_id> and team_id from the authz result

Scenario: IM3 — missing/invalid API key → 401 ERR_AUTH_INVALID_KEY
  Given no Authorization header (or an invalid bearer scheme)
  When POST /v1/images/generations {"model": "<model_id>", "prompt": "test"}
  Then response status is 401
  And response body code is "ERR_AUTH_INVALID_KEY"

Scenario: IM4 — model not in key allowlist → 403 ERR_MODEL_NOT_ALLOWED
  Given a valid API key with model_allowlist=["some-other-model-only"]
  And an active image model not in the allowlist
  When POST /v1/images/generations {"model": "<model_id>", "prompt": "hi"}
  Then response status is 403
  And response body code is "ERR_MODEL_NOT_ALLOWED"

Scenario: IM5 — unknown / inactive model → 400 ERR_MODEL_UNKNOWN
  Given a valid API key with no allowlist restriction
  And no active model row for the requested model_id in the catalog
  When POST /v1/images/generations {"model": "nonexistent-image-model", "prompt": "hi"}
  Then response status is 400
  And response body code is "ERR_MODEL_UNKNOWN"

Scenario: IM6 — tenant/key budget exceeded → 402 ERR_BUDGET_EXCEEDED
  Given a valid API key with monthly_budget_usd=0.01
  And a Redis spend counter seeded above the key's budget for the current month
  When POST /v1/images/generations {"model": "<model_id>", "prompt": "test"}
  Then response status is 402
  And response body code is "ERR_BUDGET_EXCEEDED"

Scenario: IM7 — RPM over limit → 429 ERR_RATE_LIMITED + Retry-After; TPM NOT consulted
  Given a valid API key with rpm_limit=1
  And the key's RPM sliding window is already at its limit in Redis
  When POST /v1/images/generations {"model": "<model_id>", "prompt": "hi"}
  Then response status is 429
  And response body code is "ERR_RATE_LIMITED"
  And response header Retry-After is present
  And the TPM check was NOT tripped (estimated_tokens=None → Step 9 skipped)

Scenario: IM8 — provider absent → 503 ERR_PROVIDER_UNAVAILABLE; chat path unaffected
  Given a valid API key with no allowlist restriction
  And app.state.provider_registry contains only "openrouter" (no "openai" entry)
  And an active image model with provider="openai" in the catalog
  When POST /v1/images/generations {"model": "<model_id>", "prompt": "test"}
  Then response status is 503
  And response body code is "ERR_PROVIDER_UNAVAILABLE"
  And a subsequent POST /v1/chat/completions with a valid chat model returns 200 (chat unaffected)

Scenario: IM9 — missing prompt field → 422 ERR_PAYLOAD_INVALID (PAYLOAD_PROMPT_REQUIRED)
  Given a valid API key
  When POST /v1/images/generations {"model": "<model_id>"} (no "prompt" key)
  Then response status is 422
  And response body code is "ERR_PAYLOAD_INVALID"

Scenario: IM9b — missing model field → 422 ERR_PAYLOAD_INVALID (PAYLOAD_MODEL_REQUIRED)
  Given a valid API key
  When POST /v1/images/generations {"prompt": "a cat"} (no "model" key)
  Then response status is 422
  And response body code is "ERR_PAYLOAD_INVALID"

Scenario: IM10 — regression: chat path still 200s; images task did not touch chat or embeddings
  Given the images task is built (ImagesUseCase and images_router exist)
  And a valid API key and active chat model seeded in the test DB
  And a FakeCompletionUpstream injected on app.state.completion_upstream
  When POST /v1/chat/completions {"model": "<chat_model>", "messages": [{"role":"user","content":"hi"}]}
  Then response status is 200
  And FakeCompletionUpstream.complete() was called (chat path unaffected)
  And governance.py was NOT modified (NonChatGovernance interface unchanged)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LOWEST-CONFIDENCE FLAGS AT DRAFT

  ⚠ [contract] BILLED-QUANTITY RESOLUTION — the formula:
      n_images = len(upstream_body.get("data", [])) or int(request_body.get("n", 1) or 1)
    bills the actual number of returned images when data is present and non-empty;
    falls back to requested n (default 1) when data is absent/empty.
    Risk HIGH: over-billing by at most n-1 images on provider error responses that return
    {} or {"data":[]}. An alternative (bill 0 on empty data) is safer for the customer but
    breaks the "always bill for service attempt" policy.
    Cost if wrong: customer dispute for over-billing on failed image requests.
    Mitigation: IM2 tests the 2-image actual-returned case; IM1 tests the single-image
    case; the formula is stated verbatim and the orchestrator may revise before FROZEN.
    If this formula is wrong it is a CHANGE REQUEST back to SPECIFY, not a build-time fix.

  ⚠ [contract] TPM-SKIP — estimated_tokens=None passed to NonChatGovernance.authorize.
    Step 9 (TPM pre-flight) is permanently skipped for all image requests.
    Risk: a per-key TPM limit is never enforced for images, even when set.
    Cost: rate-limiting gap for high-volume image tenants with TPM limits.
    Mitigation: IM7 verifies RPM IS enforced and optionally asserts TPM is not consulted;
    the CONTRACT names this explicitly so it is auditable.

─────────────────────────────────────────────────────────────────────────────

HTTP ENDPOINT (NEW)

  POST /v1/images/generations
    Request headers:
      Authorization: Bearer <api_key>   (required; get_raw_api_key extracts it)
    Request body (raw JSON dict):
      model   : str, required, non-empty  → PAYLOAD_MODEL_REQUIRED (422) if absent/empty
      prompt  : str, required, non-empty  → PAYLOAD_PROMPT_REQUIRED (422) if absent/empty
                (passthrough if present: n, size, quality, style, response_format, user
                 — forwarded as-is in the body dict to the upstream)
    Success response:
      200   body: the upstream provider's verbatim JSON response (data array of image objects)
    Error responses (all RFC 9457 problem+json format):
      401   ERR_AUTH_INVALID_KEY      — missing/invalid API key
      401   ERR_AUTH_KEY_EXPIRED      — key expired
      403   ERR_MODEL_NOT_ALLOWED     — model not in key allowlist
      400   ERR_MODEL_UNKNOWN         — model unknown or inactive in catalog
      403   ERR_MODEL_DISABLED        — model disabled for this tenant
      402   ERR_BUDGET_EXCEEDED       — tenant/key/team budget exceeded
      429   ERR_RATE_LIMITED          — RPM rate limit exceeded; Retry-After header set
                                        (TPM NOT checked — estimated_tokens=None)
      503   ERR_PROVIDER_UNAVAILABLE  — provider not configured (openai absent from registry)
      502   ERR_UPSTREAM_UNAVAILABLE  — upstream 5xx or circuit open
      422   ERR_PAYLOAD_INVALID       — missing model or prompt field

  Placement:
    Router module: proxy/api/images_router.py
      images_router = APIRouter(tags=["proxy"])
      @images_router.post("/v1/images/generations")
    Registered in main.py: app.include_router(images_router)  # additive, near proxy_router line
      [orchestrator applies — see WIRING DECLARATION below]
    Deps module: proxy/api/images_deps.py

─────────────────────────────────────────────────────────────────────────────

IMAGES USE CASE FLOW (FROZEN)

  def get_images_use_case(request, session) -> ImagesUseCase
    Constructs per-request (same pattern as get_embeddings_use_case in embeddings_deps.py):
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

  class ImagesUseCase:
      async def execute(
          self,
          *,
          raw_key: str | None,
          body: dict[str, Any],
          registry: ProviderRegistry,
          usage_recorder: UsageRecorder,
      ) -> tuple[int, dict[str, Any]]:

        1. model_id = body.get("model"); validate non-empty string → PAYLOAD_MODEL_REQUIRED
        2. prompt = body.get("prompt"); validate non-empty string → PAYLOAD_PROMPT_REQUIRED
        3. authz = await self._governance.authorize(raw_key, model_id, estimated_tokens=None)
           # estimated_tokens=None → Step 9 (TPM) is skipped — images have no token dimension
        4. row = select(ModelRow.modality, ModelRow.provider)
                       .where(ModelRow.id == model_id, ModelRow.active.is_(True))
                 → if None: raise MODEL_UNKNOWN.exc(model_id=model_id)
        5. provider_adapter = select_provider(row.modality, row.provider, registry)
           → raises ERR_PROVIDER_UNAVAILABLE (503) if absent
        6. try:
               status, resp_body = await provider_adapter.post_json("/images/generations", body)
           except (UpstreamUnavailableError, CircuitOpenError):
               raise UPSTREAM_UNAVAILABLE.exc() from None
        7. n_images = len(resp_body.get("data", []))
           # Bill EXACTLY the images the upstream returned (resp_body is the UPSTREAM response).
           # NO fallback to requested n: an empty/absent data array means zero images were
           # delivered → bill 0 (recorder yields cost 0). Never over-bill a failed/empty response.
           # Resolved at freeze (see [contract] flag): consistent with chat's "bill what was
           # consumed" (a no-usage chat bills 0); OpenAI success always populates data with n entries.
        8. _fire_record_with_raw(
               usage_recorder,
               tenant_id=authz.tenant_id, key_id=authz.key_id,
               model=model_id, usage=None, status=status, team_id=authz.team_id,
               pricing_unit="per_image", quantity=Decimal(n_images),
           )   # per_image billing — usage=None (no token dimension)
        9. return status, resp_body

─────────────────────────────────────────────────────────────────────────────

NON-CHAT GOVERNANCE HELPER (REUSED — NOT MODIFIED)

  Placement: proxy/application/governance.py (ALREADY EXISTS — FROZEN)

  Interface reused verbatim (MUST NOT be modified):
    governance = NonChatGovernance(
        authenticator=authenticator,
        model_checker=model_checker,
        budget_guard=budget_guard,
        rate_limiter=rate_limiter,
        redis_client=redis_client,
    )
    authz = await governance.authorize(raw_key, model_id, estimated_tokens=None)

  Step 9 (TPM pre-flight) is skipped when estimated_tokens is None.
  Steps 1–8 execute identically to the embeddings path.

─────────────────────────────────────────────────────────────────────────────

PROVIDER SELECTION CALL (FROZEN, reuses provider-seam TASK.md §3)

  from gateway.proxy.infrastructure.provider_registry import select_provider, ProviderRegistry
  provider_adapter = select_provider(modality, provider, registry)
    — modality="image", provider="openai" from ModelRow query (step 4 above)
    — registry from app.state.provider_registry via get_provider_registry dep
    — raises PROVIDER_UNAVAILABLE.exc(provider=provider) when absent → 503

─────────────────────────────────────────────────────────────────────────────

RECORDER CALL (FROZEN, per pricing-units TASK.md §3 per_image path)

  _fire_record_with_raw(
      usage_recorder,
      tenant_id=authz.tenant_id,
      key_id=authz.key_id,
      model=model_id,
      usage=None,                     # images have no token dimension
      status=status,
      team_id=authz.team_id,
      pricing_unit="per_image",
      quantity=Decimal(n_images),     # exactly len(resp_body["data"]); no requested-n fallback
  )
  — SINGLE call per request (single-bill invariant; fire-and-forget)
  — pricing_unit="per_image" activates the per_image resolver in the recorder
  — quantity is a Decimal constructed from the integer n_images count

─────────────────────────────────────────────────────────────────────────────

BILLED-QUANTITY FORMULA (PINNED — [contract] flag RESOLVED at freeze: no fallback)

  Upstream response field: data (list of image objects; may be absent or empty)

  n_images = len(resp_body.get("data", []))

  Cases:
    data=[img1, img2]  → n_images=2  (bill exactly what was returned)
    data=[img1]        → n_images=1
    data=[]            → n_images=0  (no images delivered → bill 0; recorder cost 0)
    data absent        → n_images=0  (malformed/failed response → bill 0)

  Rationale: never over-bill a failed/empty response. Consistent with chat's "bill what
  was consumed" (a no-usage chat records cost 0). On a real OpenAI success the data array
  always carries n entries, so accurate billing is preserved; the requested-n fallback was
  dropped because it would charge for images that were never produced.

─────────────────────────────────────────────────────────────────────────────

WIRING DECLARATION (orchestrator applies — BUILD task MUST NOT apply these)

  1. error_catalog.py — add NEW entry (additive, after PAYLOAD_INPUT_REQUIRED):
       PAYLOAD_PROMPT_REQUIRED = ErrorSpec(
           422, "ERR_PAYLOAD_INVALID", "Field 'prompt' is required and non-empty"
       )

  2. main.py — register images_router (additive, near app.include_router(embeddings_router)):
       from gateway.proxy.api.images_router import images_router
       app.include_router(images_router)

─────────────────────────────────────────────────────────────────────────────

PLACEMENT SUMMARY

  New files (BUILD creates these):
    proxy/api/images_router.py          — POST /v1/images/generations route + images_router
    proxy/api/images_deps.py            — get_provider_registry + get_images_use_case
    proxy/application/images_use_case.py — ImagesUseCase

  Modified files (additive, orchestrator applies):
    gateway/core/error_catalog.py       — add PAYLOAD_PROMPT_REQUIRED
    gateway/main.py                     — app.include_router(images_router)

─────────────────────────────────────────────────────────────────────────────

INVIOLABLE (MUST NOT be touched by BUILD or orchestrator for this task)

  The following files MUST be byte-identical after this task's BUILD:
    proxy/api/router.py
    proxy/api/deps.py
    proxy/application/use_cases.py
    proxy/application/governance.py           ← NonChatGovernance REUSE not EDIT
    proxy/api/embeddings_router.py
    proxy/api/embeddings_deps.py
    proxy/application/embeddings_use_case.py
  IM10 regression test provides runtime verification that the chat path is unaffected.
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] billed-quantity resolution — RESOLVED to
n_images = len(resp_body.get("data", [])) with NO requested-n fallback. The drafted `or n`
fallback would charge for images never produced on an empty/absent data array; dropped at freeze
for the conservative, billing-correct rule (bill exactly what the upstream returned; 0 on
empty/failed). Consistent with chat's "bill what was consumed"; OpenAI success always populates
data with n entries so accurate billing is preserved. IM2 asserts quantity==len(data)==2.
Second flag [contract]: estimated_tokens=None permanently skips TPM for images (no token
dimension) — RPM still enforced (IM7); auditable and intended. images reuses the FROZEN
NonChatGovernance verbatim.
<!-- [contract] flag resolved at freeze; the requested-n fallback was dropped (no over-bill). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of governance paths + provider selection + recorder call + billing-quantity
resolution + chat-untouched invariant

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_im1_happy_path_200_with_provider_body:
      arrange: seed key+tenant via HTTP admin routes; seed image model row (modality="image",
               provider="openai") + per_image pricing snapshot via raw SQL;
               inject FakeUpstreamProvider as "openai" in app.state.provider_registry;
               fake returns (200, IMAGE_RESPONSE_BODY with data=[1 image])
      act: POST /v1/images/generations {"model": IMAGE_MODEL_ID, "prompt": "a white cat"}
      assert: response.status_code == 200; response.json() == IMAGE_RESPONSE_BODY;
              fake_provider.post_json_calls[0]["path"] == "/images/generations";
              fake_provider.post_json_calls[0]["payload"]["prompt"] == "a white cat"
      RED reason: POST /v1/images/generations route does not exist → 404 Not Found

  - test_im2_single_usage_record_per_image_quantity_actual_returned:
      arrange: same as IM1 but inject SpyRecorder on app.state.usage_recorder;
               FakeUpstreamProvider returns data with 2 entries; request includes "n": 2
      act: POST /v1/images/generations {"model": IMAGE_MODEL_ID, "prompt": "two cats", "n": 2}
      assert: spy.call_count == 1; last call pricing_unit=="per_image";
              last call quantity==Decimal(2); last call usage is None;
              last call model==IMAGE_MODEL_ID
      RED reason: route absent → 404

  - test_im3_missing_api_key_401:
      arrange: no Authorization header
      act: POST /v1/images/generations {"model": IMAGE_MODEL_ID, "prompt": "test"}
      assert: status==401; body["code"]=="ERR_AUTH_INVALID_KEY"
      RED reason: route absent → 404 instead of 401

  - test_im4_model_not_in_allowlist_403:
      arrange: seed key with model_allowlist=["other-model-only"]; seed image model row
      act: POST /v1/images/generations {"model": IMAGE_MODEL_ID, "prompt": "hi"}
      assert: status==403; body["code"]=="ERR_MODEL_NOT_ALLOWED"
      RED reason: route absent → 404 instead of 403

  - test_im5_unknown_model_400:
      arrange: valid key; no model row in catalog for "nonexistent-image-model"
      act: POST /v1/images/generations {"model": "nonexistent-image-model", "prompt": "hi"}
      assert: status==400; body["code"]=="ERR_MODEL_UNKNOWN"
      RED reason: route absent → 404 instead of 400

  - test_im6_budget_exceeded_402:
      arrange: seed key with monthly_budget_usd=0.01; seed Redis spend counter above budget;
               seed image model row + per_image pricing snapshot
      act: POST /v1/images/generations {"model": IMAGE_MODEL_ID, "prompt": "test"}
      assert: status==402; body["code"]=="ERR_BUDGET_EXCEEDED"
      RED reason: route absent → 404 instead of 402

  - test_im7_rpm_exceeded_429_retry_after:
      arrange: seed key with rpm_limit=1; seed Redis RPM zset window full; seed image model row
      act: POST /v1/images/generations {"model": IMAGE_MODEL_ID, "prompt": "hi"}
      assert: status==429; body["code"]=="ERR_RATE_LIMITED"; "Retry-After" in response.headers
      RED reason: route absent → 404 instead of 429

  - test_im8_provider_absent_503_chat_unaffected:
      arrange: app.state.provider_registry has only "openrouter" (no "openai");
               seed image model (provider="openai"); seed chat model; valid key;
               inject FakeCompletionUpstream on app.state.completion_upstream
      act Part A: POST /v1/images/generations {"model": IMAGE_MODEL_ID, "prompt": "test"}
      assert Part A: status==503; body["code"]=="ERR_PROVIDER_UNAVAILABLE"
      act Part B: POST /v1/chat/completions {"model": CHAT_MODEL_ID, "messages": [...]}
      assert Part B: status==200 (chat path unaffected)
      RED reason: route absent → 404

  - test_im9_missing_prompt_422:
      arrange: valid key; seed image model row
      act: POST /v1/images/generations {"model": IMAGE_MODEL_ID} (no "prompt")
      assert: status==422; body["code"]=="ERR_PAYLOAD_INVALID"
      RED reason: route absent → 404 instead of 422

  - test_im9b_missing_model_422:
      arrange: valid key
      act: POST /v1/images/generations {"prompt": "a cat"} (no "model")
      assert: status==422; body["code"]=="ERR_PAYLOAD_INVALID"
      RED reason: route absent → 404 instead of 422

  - test_im10_regression_chat_path_200_untouched:
      arrange: seed chat model row; seed key+tenant via HTTP admin; inject FakeCompletionUpstream
      act: POST /v1/chat/completions {"model": CHAT_MODEL_ID, "messages": [{"role":"user","content":"hi"}]}
      assert: status==200; FakeCompletionUpstream.complete_calls has 1 entry
      GREEN-BY-DESIGN: chat path already works; regression guard only
</test_plan>

Tests live in: `apps/gateway/tests/images_endpoint/` · `apps/gateway/tests/images_endpoint/conftest.py` · `apps/gateway/tests/images_endpoint/test_images_endpoint.py` · `apps/gateway/tests/images_endpoint/__init__.py`

Expected red/green at spec phase (before BUILD):
  - IM1–IM9b (10 tests): RED (route /v1/images/generations absent → 404, or ImportError from
    the images_use_case / images_router import when the test harness wires them up)
  - IM10: GREEN-BY-DESIGN (chat path already works; regression guard only)
  All failures for the RIGHT reason — not skips.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  1. INVIOLABLE: proxy/api/router.py, proxy/api/deps.py, proxy/application/use_cases.py,
     proxy/application/governance.py, proxy/api/embeddings_router.py, proxy/api/embeddings_deps.py,
     proxy/application/embeddings_use_case.py MUST NOT be modified — not even imports or whitespace.
  2. BILLED-QUANTITY: n_images formula MUST be implemented verbatim:
       n_images = len(resp_body.get("data", [])) or int(body.get("n", 1) or 1)
     body is the REQUEST body; resp_body is the UPSTREAM response.
  3. SINGLE-BILL: _fire_record_with_raw MUST be called exactly once per request path.
     No double-billing; no recording on governance error paths.
  4. Images endpoint MUST route through app.state.provider_registry, NEVER through
     get_completion_upstream() or CompletionUseCase.
  5. estimated_tokens=None MUST be passed to governance.authorize — never pass a token count.
  6. No new dependencies — all packages already present.

Code lives in:
  - `proxy/api/images_router.py` (route + handler)
  - `proxy/api/images_deps.py` (get_provider_registry + get_images_use_case)
  - `proxy/application/images_use_case.py` (ImagesUseCase)
  Orchestrator applies (shared files, NOT BUILD):
  - `gateway/core/error_catalog.py` (additive: PAYLOAD_PROMPT_REQUIRED)
  - `gateway/main.py` (additive: app.include_router(images_router))

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — tests/images_endpoint/ 11/11 (IM1–IM10 + IM10 green-by-design regression);
      authoritative `make ci` EXIT=0 (lint + pyright + allowlist + allowlist-node + full suite,
      --cov-fail-under=80). Built sequentially with audio; the combined gate is green.
- [x] coverage did not decrease — `--cov-fail-under=80` enforced inside the passing gate.
- [x] no test or contract was altered during build — IM2 comment + §1/§3 prose were aligned to the
      freeze-time billing resolution (no requested-n fallback) BEFORE the build, by the orchestrator;
      no assertion changed (IM2 still asserts quantity==Decimal(2)). The build touched no tests.
- [x] concurrency / timing of the risky operation is safe — ImagesUseCase is a single async flow;
      one fire-and-forget _fire_record_with_raw (single-bill, IM2); governance fail-open inherited
      from NonChatGovernance; upstream errors → 502, provider-absent → 503. No shared mutable state.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secret touched/logged
      (OpenAIDirectProvider owns the key); parameterized ModelRow query; no new dependency.
- [x] layering & dependencies follow CONVENTIONS.md — api(images_router/deps) → application(
      images_use_case) → infrastructure(provider_registry/model_checker); identical to embeddings.
- [x] a person reviewed and approved the change — orchestrator read images_use_case.py line-by-line
      (len(data) billing, single-bill, error mapping) + both additive diffs + INVIOLABLE git diff
      (empty across chat/governance/embeddings). Tin Dang (delegated auto mode).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — images_router registered in main.py (app.include_router(images_router),
      additive after embeddings_router) + imported; get_images_use_case/get_provider_registry
      consumed by the router via Depends; ImagesUseCase built by the dep; NonChatGovernance reused
      unchanged; PAYLOAD_PROMPT_REQUIRED raised in the use case. IM1 exercises the full wired chain.
- [x] DEAD-CODE (code) — no orphaned symbol; images_deps reuses get_provider_registry from
      embeddings_deps (no duplicate). ruff F401/I001 clean in the passing gate.

### GATE RECORD
Outcome: PASS  (auto-resolved on complete evidence — delegated auto mode; no security finding)
Reviewed by: Tin Dang (delegated auto mode, 2026-06-12) · date: 2026-06-12

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
  - ERR_PROVIDER_UNAVAILABLE 503 rate on /v1/images/generations → openai key misconfiguration
  - ERR_BUDGET_EXCEEDED 402 rate → tenant/key budget exhaustion for images
  - Single usage_records row per images request (no double-billing monitor)
  - pricing_unit="per_image" rows in usage_records confirm the per-image billing path
  - Chat and embeddings 5xx rates should not change after this milestone

Spec delta for the next loop:
  - audio-endpoints will construct NonChatGovernance with estimated_tokens=None (same as images)
  - If the billed-quantity fallback policy changes (bill 0 on empty data), only images_use_case.py
    step 7 changes; the contract formula and IM2 test need updating
  - If a new governance step is added in a future slice, update NonChatGovernance.authorize
    in governance.py AND CompletionUseCase._enforce_governance in use_cases.py in the same PR

### Competency deltas
- [SDD · open] NonChatGovernance drops the chat M11 soft-budget-alert seam on the non-chat path
  (inherited from embeddings-endpoint disposition 1). HARD 402 is preserved; advisory alert absent.
  Soft-budget-alert parity for images should be revisited at v7 close together with embeddings/audio.
- [ADD · open] The billed-quantity fallback policy (actual-returned vs requested-n) is a business
  decision not a technical one. The [contract] flag at §3 top ensures it is surfaced before freeze.
</output>
