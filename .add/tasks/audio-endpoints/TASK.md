# TASK: POST /v1/audio/transcriptions (STT, per-second) + /v1/audio/speech (TTS, per-character streaming), reuses NonChatGovernance

slug: audio-endpoints · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: POST /v1/audio/transcriptions (STT, multipart/form-data, per_second billing) and
POST /v1/audio/speech (TTS, JSON body, streaming response, per_character billing). Both routes
live in a new module `proxy/api/audio_router.py`, governed by the ALREADY-BUILT frozen
NonChatGovernance, billed exactly once per request, and routed exclusively via the provider seam
(app.state.provider_registry). Neither touches the chat path nor the embeddings path.

Framings weighed:

  - **Two routes in one audio_router module, each with its own use case — additive, all frozen
    paths byte-identical (chosen)**:
    A single new `APIRouter` in `proxy/api/audio_router.py` hosts both endpoints. Each endpoint
    has a dedicated application-layer class (`TranscriptionUseCase`, `SpeechUseCase`) in a new
    module `proxy/application/audio_use_case.py`. Both reuse `NonChatGovernance` identically to
    `EmbeddingsUseCase` — same five constructor arguments, same `authorize(raw_key, model_id,
    estimated_tokens=None)` call. A new deps module `proxy/api/audio_deps.py` provides
    `get_transcription_use_case` and `get_speech_use_case`. The chat router, embeddings router,
    governance.py, and all frozen upstream files are NEVER modified. This is additive-only.

  - **Inline handler functions without use-case classes (rejected)**:
    The STT path is non-streaming and the TTS path is streaming with a critical bill-before-stream
    invariant. Inline handlers would blur the contract boundary between routing and application
    logic, making it harder to unit-test the billing timing and to verify the single-bill invariant
    without importing the router. Use-case classes isolate the testable flow.

  - **One combined AudioUseCase for both endpoints (rejected)**:
    STT and TTS have fundamentally different return types: TranscriptionUseCase returns
    `tuple[int, dict[str, Any]]` (non-streaming, like EmbeddingsUseCase); SpeechUseCase returns
    `tuple[AsyncIterator[bytes], str]` (streaming + media_type). Merging them into one class
    would require a discriminated union return type and modality dispatch inside the use case,
    violating single-responsibility.

STT duration sourcing decision (surface in §3 as the primary [contract] flag):
  The OpenAI `/audio/transcriptions` endpoint only returns a `"duration"` field when
  `response_format=verbose_json`. For other response_format values (or when the field is absent),
  `body.get("duration")` returns `None`/absent. The STT use case reads `duration_s =
  float(body.get("duration") or 0.0)` and bills `quantity=Decimal(str(duration_s))`. When
  `duration_s` is 0.0 (absent), the recorder yields cost 0 and emits a WARN — it NEVER raises
  into the proxy path. This is explicitly NOT a silent-best-guess: the caller must pass
  `response_format=verbose_json` to receive a non-zero bill. This decision is surfaced verbatim
  in §3 as the lowest-confidence flag.

TTS bill-at-start decision (surface in §3 as the secondary [contract] flag):
  TTS responses are streaming (bytes). The character count of `input` is known from the request
  body BEFORE streaming begins. Two options exist: (a) bill-at-start (record BEFORE returning
  StreamingResponse) or (b) bill-on-completion (record after the generator is exhausted, inside
  a callback). Option (a) is chosen because: the char count is known pre-stream; once
  `StreamingResponse` returns to the ASGI layer, the HTTP status 200 is committed and mid-stream
  errors cannot change it; billing "as if the full response will complete" matches OpenAI's own
  billing model; and no mid-stream callback machinery is needed. This is the single-bill
  invariant for TTS: record fires ONCE, AFTER governance+provider-select succeed and BEFORE
  `StreamingResponse` is constructed. The risk is over-billing if the stream fails after
  status 200 is committed — this is acceptable and matches the no-retry-after-first-byte rule.

Must:
<must>
  - POST /v1/audio/transcriptions MUST be a new FastAPI route in a new module
    `proxy/api/audio_router.py` using `audio_router = APIRouter(tags=["proxy"])`. No prefix.
    The route MUST read multipart via `form = await request.form()`. Required fields:
    `file` (UploadFile, from form["file"]) and `model` (str, from form.get("model")).
    Missing `file` → PAYLOAD_FILE_REQUIRED (422 ERR_PAYLOAD_INVALID).
    Missing/empty `model` → PAYLOAD_MODEL_REQUIRED (422 ERR_PAYLOAD_INVALID).
    Passthrough fields (forwarded if present): language, prompt, response_format, temperature.
  - POST /v1/audio/speech MUST be a new route in the same audio_router.
    Body read as `body = await request.json()`.
    Required fields: `model` (str), `input` (str, non-empty — REUSE PAYLOAD_INPUT_REQUIRED),
    `voice` (str, non-empty — NEW PAYLOAD_VOICE_REQUIRED 422 ERR_PAYLOAD_INVALID).
    Passthrough fields: response_format, speed.
  - Both routes MUST NOT call `get_completion_upstream()`, `get_completion_use_case()`, or
    `get_embeddings_use_case()`. They route exclusively via `app.state.provider_registry`.
  - The STT flow MUST:
    1. Validate file + model fields (PAYLOAD_FILE_REQUIRED / PAYLOAD_MODEL_REQUIRED).
    2. Call `governance.authorize(raw_key, model_id, estimated_tokens=None)`.
    3. Query `ModelRow.modality` + `ModelRow.provider` where `id == model_id AND active`.
       Missing row → MODEL_UNKNOWN.
    4. `select_provider(modality, provider, registry)` → UpstreamProvider.
    5. `status, body = await provider.post_multipart("/audio/transcriptions",
       files={"file": (filename, file_bytes, content_type)}, data={"model": ..., ...})`.
       Except UpstreamUnavailableError | CircuitOpenError → UPSTREAM_UNAVAILABLE (502).
    6. `duration_s = float(body.get("duration") or 0.0)`.
    7. `_fire_record_with_raw(usage_recorder, tenant_id=authz.tenant_id, key_id=authz.key_id,
       model=model_id, usage=None, status=status, team_id=authz.team_id,
       pricing_unit="per_second", quantity=Decimal(str(duration_s)))` — EXACTLY ONCE.
    8. Return `JSONResponse(body, status_code=status)`.
  - The TTS flow MUST (governance MUST raise PRE-stream; status 200 is not committed
    until StreamingResponse returns):
    1. Validate model + input + voice fields.
    2. Call `governance.authorize(raw_key, model_id, estimated_tokens=None)`.
    3. Query ModelRow modality+provider. Missing → MODEL_UNKNOWN.
    4. `select_provider(...)` → UpstreamProvider. Absent → 503 pre-stream.
    5. `_fire_record_with_raw(usage_recorder, ..., pricing_unit="per_character",
       quantity=Decimal(len(input_text)))` — EXACTLY ONCE, BEFORE StreamingResponse.
    6. `gen = provider.stream_bytes("/audio/speech", body_payload)`.
    7. Return `StreamingResponse(gen, media_type=<resolved_media_type>)`.
    The media_type MUST be resolved from the requested `response_format` field:
    {"mp3": "audio/mpeg", "opus": "audio/opus", "aac": "audio/aac",
     "flac": "audio/flac", "wav": "audio/wav", "pcm": "audio/pcm"}; default "audio/mpeg".
  - `TranscriptionUseCase.execute` MUST return `tuple[int, dict[str, Any]]` (non-streaming).
  - `SpeechUseCase.execute` MUST return `tuple[AsyncIterator[bytes], str]` where str is the
    resolved media_type. The billing record fires INSIDE execute() before returning.
  - Both use cases MUST import `_fire_record_with_raw` with
    `# pyright: ignore[reportPrivateUsage]` (same pattern as EmbeddingsUseCase).
  - `get_transcription_use_case` and `get_speech_use_case` in `proxy/api/audio_deps.py` MUST
    build `NonChatGovernance` with the same five constructor arguments as
    `get_embeddings_use_case` in embeddings_deps.py.
  - Provider absent (select_provider raises PROVIDER_UNAVAILABLE 503) MUST propagate pre-stream
    for TTS — the handler must NOT have started StreamingResponse before this check.
  - Upstream 5xx / CircuitOpenError for STT: map to 502 UPSTREAM_UNAVAILABLE.
  - Upstream 4xx for STT: pass through verbatim.
  - SINGLE-BILL: exactly ONE `_fire_record_with_raw` call per request path. Governance errors
    do NOT record. Provider-select errors do NOT record. TTS record fires before stream starts.
  - The existing chat suite, embeddings suite, and all other test suites MUST remain green.
    AU/AT regression scenario proves this.
  - audio_router MUST be registered in main.py via `app.include_router(audio_router)` — this
    is wiring applied by the orchestrator (see WIRING DECLARATION in §3).
</must>

Reject:
<reject>
  - STT: missing `file` field in multipart form           → 422 ERR_PAYLOAD_INVALID (PAYLOAD_FILE_REQUIRED)
  - STT: missing/empty `model` field in multipart form    → 422 ERR_PAYLOAD_INVALID (PAYLOAD_MODEL_REQUIRED)
  - TTS: missing/empty `model` field in JSON body         → 422 ERR_PAYLOAD_INVALID (PAYLOAD_MODEL_REQUIRED)
  - TTS: missing/empty `input` field in JSON body         → 422 ERR_PAYLOAD_INVALID (PAYLOAD_INPUT_REQUIRED)
  - TTS: missing/empty `voice` field in JSON body         → 422 ERR_PAYLOAD_INVALID (PAYLOAD_VOICE_REQUIRED)
  - Both: missing or invalid API key                      → 401 ERR_AUTH_INVALID_KEY
  - Both: expired API key                                 → 401 ERR_AUTH_KEY_EXPIRED
  - Both: model not in key allowlist                      → 403 ERR_MODEL_NOT_ALLOWED
  - Both: model unknown/inactive                          → 400 ERR_MODEL_UNKNOWN
  - Both: model disabled for tenant                       → 403 ERR_MODEL_DISABLED
  - Both: tenant/key/team budget exceeded                 → 402 ERR_BUDGET_EXCEEDED
  - Both: RPM over limit                                  → 429 ERR_RATE_LIMITED + Retry-After
  - Both: provider absent from registry                   → 503 ERR_PROVIDER_UNAVAILABLE (pre-stream for TTS)
  - STT: upstream 5xx / CircuitOpenError                  → 502 ERR_UPSTREAM_UNAVAILABLE
</reject>

After:
<after>
  - A valid STT request returns 200 with the upstream provider's verbatim JSON body.
  - Exactly one usage_records ledger row per STT request, priced per_second, with quantity from
    body["duration"] (0.0 if absent — WARN but no raise).
  - A valid TTS request returns 200 StreamingResponse with audio bytes.
  - Exactly one usage_records ledger row per TTS request, priced per_character, with
    quantity == Decimal(len(input)), billed BEFORE the stream bytes are read.
  - Both endpoints enforce the same nine governance checks as the chat and embeddings paths,
    using NonChatGovernance without modification.
  - The chat path (POST /v1/chat/completions), embeddings path, and all existing test suites
    remain byte-identical to their pre-task behavior.
  - NonChatGovernance (governance.py) is NEVER modified.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE [STT duration source — verbose_json dependency]:
    The `"duration"` field is present in upstream response bodies ONLY when the client passes
    `response_format=verbose_json` (OpenAI API contract). For all other response_format values,
    `body.get("duration")` returns None/absent → duration_s=0.0 → cost 0 + WARN.
    This means non-verbose_json STT calls are effectively unpriced. If OpenAI returns duration
    in other formats in the future, the code auto-benefits. If the project needs accurate billing
    for all response_format values, a workaround (e.g., probe audio file duration from bytes)
    would be required. Cost if wrong: STT calls with response_format != verbose_json are billed
    at $0 silently until the WARN is noticed. Mitigation: the CONTRACT states this verbatim;
    AU2b test covers the absent-duration → quantity=0 path explicitly.

  ⚠ SECOND LOWEST CONFIDENCE [TTS bill-at-start over-billing on stream failure]:
    Recording per_character BEFORE the stream starts means a client that disconnects after the
    200 header is committed (but before receiving bytes) is charged for the full input length.
    This matches OpenAI's billing model (they charge on generation start) and the
    no-retry-after-first-byte rule, but it is an intentional over-billing risk for stream
    failures. Cost if wrong: customers are charged for failed TTS streams. Mitigation: the
    CONTRACT names this explicitly; it matches the existing chat streaming pattern; any future
    refund logic would be a separate billing-correction task, not a contract change.

  - [x] NonChatGovernance is ALREADY BUILT at proxy/application/governance.py (frozen
    @ embeddings-endpoint §3). Its constructor + authorize() interface MUST NOT be changed.
    Confirmed by reading governance.py.

  - [x] `_fire_record_with_raw` accepts `pricing_unit: str | None` and `quantity: Decimal | None`
    (pricing-units TASK.md §3, BUILD done). STT passes pricing_unit="per_second",
    quantity=Decimal(str(duration_s)). TTS passes pricing_unit="per_character",
    quantity=Decimal(len(input_text)). Confirmed by reading use_cases.py + endpoint-pipeline-map.md §4.

  - [x] `OpenAIDirectProvider.post_multipart(path, files, data) -> tuple[int, dict[str, Any]]`
    and `OpenAIDirectProvider.stream_bytes(path, payload) -> AsyncIterator[bytes]` are ALREADY
    BUILT (provider-seam BUILD done). Confirmed by reading openai_provider.py.

  - [x] `python-multipart 0.0.32` is ALREADY INSTALLED (import name `python_multipart`).
    `await request.form()` works server-side without any new dependency. Confirmed by brief.

  - [x] ModelRow has `modality` and `provider` columns. `modality='audio_stt'` for Whisper-1,
    `modality='audio_tts'` for tts-1/tts-1-hd. Confirmed by endpoint-pipeline-map.md §8.

  - [x] `select_provider(modality, provider, registry)` raises `PROVIDER_UNAVAILABLE.exc(provider=)`
    (503) when absent. This propagates naturally to ProblemError pre-stream for TTS.
    Confirmed by reading provider_registry.py + endpoint-pipeline-map.md §5.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ─── STT scenarios ───────────────────────────────────────────────────────────

Scenario: AU1 — STT happy path: valid key + active audio_stt model → 200, provider body
  Given a valid API key with no allowlist restriction
  And an active model row (id="whisper-1", modality="audio_stt", provider="openai") in the catalog
  And a per_second pricing snapshot for the model
  And a FakeUpstreamProvider injected as "openai" in app.state.provider_registry
  And the provider's post_multipart returns (200, {"text": "hello", "duration": 12.5})
  When POST /v1/audio/transcriptions with multipart file="audio.mp3" model="whisper-1"
  Then response status is 200
  And response body equals the provider's body verbatim
  And provider.post_multipart was called with path="/audio/transcriptions"
  And the files dict contains the "file" key with (filename, bytes, content_type)

Scenario: AU2 — STT billing: duration from body["duration"] → quantity=Decimal("12.5")
  Given the same setup as AU1 with a SpyRecorder injected on app.state.usage_recorder
  And the FakeUpstreamProvider returns {"text": "hello", "duration": 12.5}
  When POST /v1/audio/transcriptions with a valid multipart request
  Then SpyRecorder.record() was called exactly once
  And the record carries pricing_unit="per_second" and quantity==Decimal("12.5")
  And the record carries model="whisper-1" and status=200

Scenario: AU2b — STT duration absent → quantity=0, no raise
  Given the same setup as AU1 with a SpyRecorder injected on app.state.usage_recorder
  And the FakeUpstreamProvider returns {"text": "hello"} (no "duration" key)
  When POST /v1/audio/transcriptions with a valid multipart request
  Then response status is 200
  And SpyRecorder.record() was called exactly once
  And the record carries pricing_unit="per_second" and quantity==Decimal("0")
  And no error is raised (billing records $0, recorder emits WARN)

Scenario: AU3 — STT bad API key → 401 ERR_AUTH_INVALID_KEY
  Given no Authorization header
  When POST /v1/audio/transcriptions (any valid multipart form)
  Then response status is 401
  And response body code is "ERR_AUTH_INVALID_KEY"

Scenario: AU4 — STT model not in key allowlist → 403 ERR_MODEL_NOT_ALLOWED
  Given a valid API key with model_allowlist=["other-model"]
  And an active audio_stt model "whisper-1" NOT in the allowlist
  When POST /v1/audio/transcriptions with model="whisper-1"
  Then response status is 403
  And response body code is "ERR_MODEL_NOT_ALLOWED"

Scenario: AU5 — STT unknown model → 400 ERR_MODEL_UNKNOWN
  Given a valid API key
  And no active model row for "nonexistent-stt-model"
  When POST /v1/audio/transcriptions with model="nonexistent-stt-model"
  Then response status is 400
  And response body code is "ERR_MODEL_UNKNOWN"

Scenario: AU6 — STT budget exceeded → 402 ERR_BUDGET_EXCEEDED
  Given a valid API key with monthly_budget_usd=0.01
  And a Redis spend counter seeded above the key's budget for the current month
  When POST /v1/audio/transcriptions (valid multipart)
  Then response status is 402
  And response body code is "ERR_BUDGET_EXCEEDED"

Scenario: AU7 — STT RPM exceeded → 429 ERR_RATE_LIMITED + Retry-After
  Given a valid API key with rpm_limit=1
  And the key's RPM sliding window is at its limit in Redis
  When POST /v1/audio/transcriptions (valid multipart)
  Then response status is 429
  And response body code is "ERR_RATE_LIMITED"
  And response header Retry-After is present

Scenario: AU8 — STT provider absent → 503 ERR_PROVIDER_UNAVAILABLE
  Given a valid API key
  And app.state.provider_registry has NO "openai" entry
  And an active audio_stt model with provider="openai" in the catalog
  When POST /v1/audio/transcriptions (valid multipart)
  Then response status is 503
  And response body code is "ERR_PROVIDER_UNAVAILABLE"

Scenario: AU9 — STT missing file field → 422 ERR_PAYLOAD_INVALID
  Given a valid API key
  When POST /v1/audio/transcriptions with multipart model="whisper-1" but NO file field
  Then response status is 422
  And response body code is "ERR_PAYLOAD_INVALID"

Scenario: AU9b — STT missing model field → 422 ERR_PAYLOAD_INVALID
  Given a valid API key
  When POST /v1/audio/transcriptions with a file but NO model field in the form
  Then response status is 422
  And response body code is "ERR_PAYLOAD_INVALID"

# ─── TTS scenarios ───────────────────────────────────────────────────────────

Scenario: AT1 — TTS happy path: valid key + active audio_tts model → 200 StreamingResponse
  Given a valid API key with no allowlist restriction
  And an active model row (id="tts-1", modality="audio_tts", provider="openai") in the catalog
  And a per_character pricing snapshot for the model
  And a FakeUpstreamProvider injected as "openai" in app.state.provider_registry
  And the provider's stream_bytes yields b"audio-bytes-chunk"
  When POST /v1/audio/speech {"model": "tts-1", "input": "Hello world", "voice": "alloy"}
  Then response status is 200
  And response headers Content-Type matches "audio/mpeg"
  And response body contains the streamed bytes from the provider
  And provider.stream_bytes was called with path="/audio/speech"

Scenario: AT2 — TTS billing: per_character, quantity=Decimal(len(input)), billed before stream
  Given the same setup as AT1 with a SpyRecorder injected on app.state.usage_recorder
  When POST /v1/audio/speech {"model": "tts-1", "input": "Hello world", "voice": "alloy"}
    (input has 11 characters)
  Then SpyRecorder.record() was called exactly once
  And the record carries pricing_unit="per_character" and quantity==Decimal(11)
  And the record carries model="tts-1" and status=200
  And the record fires BEFORE the streaming response bytes are consumed

Scenario: AT3 — TTS governance raises PRE-stream: bad key → 401 body is problem+json, not audio
  Given no Authorization header (or invalid bearer)
  When POST /v1/audio/speech {"model": "tts-1", "input": "hi", "voice": "alloy"}
  Then response status is 401
  And response body is RFC 9457 problem+json (Content-Type application/json)
  And response body code is "ERR_AUTH_INVALID_KEY"
  And response is NOT a StreamingResponse (not audio bytes)

Scenario: AT4 — TTS missing voice field → 422 ERR_PAYLOAD_INVALID
  Given a valid API key
  When POST /v1/audio/speech {"model": "tts-1", "input": "hi"} (no voice field)
  Then response status is 422
  And response body code is "ERR_PAYLOAD_INVALID"

Scenario: AT5 — TTS missing input field → 422 ERR_PAYLOAD_INVALID
  Given a valid API key
  When POST /v1/audio/speech {"model": "tts-1", "voice": "alloy"} (no input field)
  Then response status is 422
  And response body code is "ERR_PAYLOAD_INVALID"

Scenario: AT6 — TTS provider absent → 503 ERR_PROVIDER_UNAVAILABLE (pre-stream)
  Given a valid API key
  And app.state.provider_registry has NO "openai" entry
  And an active audio_tts model with provider="openai" in the catalog
  When POST /v1/audio/speech {"model": "tts-1", "input": "hi", "voice": "alloy"}
  Then response status is 503
  And response body code is "ERR_PROVIDER_UNAVAILABLE"
  And response is NOT a StreamingResponse (returned before any streaming begins)

Scenario: AU/AT regression — chat and embeddings still 200s; audio task did not touch them
  Given the audio-endpoints task is built (audio_router, audio_use_case exist)
  And a valid API key and active chat model seeded in the test DB
  And a FakeCompletionUpstream injected on app.state.completion_upstream
  When POST /v1/chat/completions {"model": "<chat_model>", "messages": [{"role":"user","content":"hi"}]}
  Then response status is 200
  And FakeCompletionUpstream.complete() was called (chat path unaffected)
  And proxy/api/router.py is byte-identical (governance.py, deps.py, use_cases.py unchanged)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LOWEST-CONFIDENCE FLAGS AT DRAFT

  ⚠ [contract] STT DURATION SOURCE — verbose_json dependency:
    `body.get("duration")` returns a float ONLY when the upstream response is verbose_json
    format (OpenAI API guarantee). For json/text/srt/vtt response_format values, "duration"
    is absent → duration_s=0.0 → quantity=Decimal("0") → billing cost $0 + recorder WARN.
    Risk: STT calls with non-verbose_json format are never accurately billed.
    Cost if wrong: systematic under-billing for non-verbose_json STT; WARN in logs is the
    only signal. Mitigation: AU2b test covers the absent-duration path; the recorder already
    handles 0-quantity (per pricing-units contract); caller documentation must specify verbose_json.
    This is accepted for v7. A follow-up could probe audio file duration from uploaded bytes.

  ⚠ [contract] TTS BILL-AT-START TIMING — over-billing on stream failure:
    The usage record for TTS fires AFTER governance+provider-select succeed and BEFORE
    StreamingResponse is constructed. Once 200 is committed to the ASGI layer, mid-stream
    failures cannot change the status or trigger a billing correction. Customers are charged
    for failed/disconnected TTS streams.
    Risk: over-billing when the stream fails after 200 is committed.
    Cost if wrong: customer billing complaints for failed TTS calls; refund requires a
    separate billing-correction task. Mitigation: this matches OpenAI's own billing model
    and the chat streaming pattern (no-retry-after-first-byte); AT2 test verifies single-bill;
    AT3 test verifies governance errors are pre-stream. Accepted for v7.

─────────────────────────────────────────────────────────────────────────────

HTTP ENDPOINTS (NEW)

  POST /v1/audio/transcriptions  (STT — multipart/form-data)
    Request headers:
      Authorization: Bearer <api_key>   (required; get_raw_api_key extracts it)
      Content-Type: multipart/form-data
    Multipart form fields:
      file    : UploadFile, required          → PAYLOAD_FILE_REQUIRED (422) if absent
      model   : str, required, non-empty      → PAYLOAD_MODEL_REQUIRED (422) if absent/empty
      language: str, optional (passthrough)
      prompt  : str, optional (passthrough)
      response_format: str, optional (passthrough; "verbose_json" required for duration billing)
      temperature: float, optional (passthrough)
    Success response:
      200  body: the upstream provider's verbatim JSON response
    Error responses (all RFC 9457 problem+json):
      401  ERR_AUTH_INVALID_KEY     — missing/invalid API key
      401  ERR_AUTH_KEY_EXPIRED     — key expired
      403  ERR_MODEL_NOT_ALLOWED    — model not in key allowlist
      400  ERR_MODEL_UNKNOWN        — model unknown or inactive in catalog
      403  ERR_MODEL_DISABLED       — model disabled for this tenant
      402  ERR_BUDGET_EXCEEDED      — tenant/key/team budget exceeded
      429  ERR_RATE_LIMITED         — RPM rate limit exceeded; Retry-After header set
      503  ERR_PROVIDER_UNAVAILABLE — provider not configured
      502  ERR_UPSTREAM_UNAVAILABLE — upstream 5xx or circuit open
      422  ERR_PAYLOAD_INVALID      — missing file or model field

  POST /v1/audio/speech  (TTS — JSON body, streaming)
    Request headers:
      Authorization: Bearer <api_key>   (required)
      Content-Type: application/json
    Request body (raw JSON dict):
      model   : str, required, non-empty      → PAYLOAD_MODEL_REQUIRED (422) if absent/empty
      input   : str, required, non-empty      → PAYLOAD_INPUT_REQUIRED (422) if absent/empty
      voice   : str, required, non-empty      → PAYLOAD_VOICE_REQUIRED (422) if absent/empty
      response_format: str, optional (passthrough; default "mp3")
      speed   : float, optional (passthrough)
    Success response:
      200  StreamingResponse; Content-Type resolved from response_format:
           "mp3"   → "audio/mpeg"  (default)
           "opus"  → "audio/opus"
           "aac"   → "audio/aac"
           "flac"  → "audio/flac"
           "wav"   → "audio/wav"
           "pcm"   → "audio/pcm"
    Error responses (all RFC 9457 problem+json; ALL raised PRE-stream):
      401  ERR_AUTH_INVALID_KEY     — missing/invalid API key
      401  ERR_AUTH_KEY_EXPIRED     — key expired
      403  ERR_MODEL_NOT_ALLOWED    — model not in key allowlist
      400  ERR_MODEL_UNKNOWN        — model unknown or inactive in catalog
      403  ERR_MODEL_DISABLED       — model disabled for this tenant
      402  ERR_BUDGET_EXCEEDED      — tenant/key/team budget exceeded
      429  ERR_RATE_LIMITED         — RPM rate limit exceeded; Retry-After header set
      503  ERR_PROVIDER_UNAVAILABLE — provider not configured (pre-stream)
      422  ERR_PAYLOAD_INVALID      — missing model, input, or voice field

  Placement:
    Router module: proxy/api/audio_router.py
      audio_router = APIRouter(tags=["proxy"])
      @audio_router.post("/v1/audio/transcriptions")
      @audio_router.post("/v1/audio/speech")
    Deps module: proxy/api/audio_deps.py
      get_transcription_use_case(request, session) -> TranscriptionUseCase
      get_speech_use_case(request, session) -> SpeechUseCase
      get_provider_registry  — REUSE from embeddings_deps.py (or re-declare identically)

─────────────────────────────────────────────────────────────────────────────

TRANSCRIPTION USE CASE FLOW (FROZEN)

  class TranscriptionUseCase:
      def __init__(self, *, governance: NonChatGovernance, session: AsyncSession) -> None: ...

      async def execute(
          self,
          *,
          raw_key: str | None,
          form: Any,                    # starlette.datastructures.FormData
          registry: ProviderRegistry,
          usage_recorder: UsageRecorder,
      ) -> tuple[int, dict[str, Any]]:

        1. file_field = form.get("file")
           if not file_field:
               raise PAYLOAD_FILE_REQUIRED.exc()
        2. model_id = form.get("model")
           if not model_id or not model_id.strip():
               raise PAYLOAD_MODEL_REQUIRED.exc()
        3. authz = await self._governance.authorize(raw_key, model_id, estimated_tokens=None)
        4. row = select(ModelRow.modality, ModelRow.provider)
                       .where(ModelRow.id == model_id, ModelRow.active.is_(True))
                 → if None: raise MODEL_UNKNOWN.exc(model_id=model_id)
        5. provider_adapter = select_provider(row.modality, row.provider, registry)
           → raises ERR_PROVIDER_UNAVAILABLE (503) if absent
        6. Read file bytes: file_bytes = await file_field.read()
           Collect passthrough: data = {"model": model_id} + any of
           (language, prompt, response_format, temperature) present in form.
           files = {"file": (file_field.filename or "audio", file_bytes,
                             file_field.content_type or "application/octet-stream")}
        7. try:
               status, resp_body = await provider_adapter.post_multipart(
                   "/audio/transcriptions", files=files, data=data)
           except (UpstreamUnavailableError, CircuitOpenError):
               raise UPSTREAM_UNAVAILABLE.exc() from None
        8. duration_s = float(resp_body.get("duration") or 0.0)
        9. _fire_record_with_raw(
               usage_recorder,
               tenant_id=authz.tenant_id, key_id=authz.key_id,
               model=model_id, usage=None, status=status, team_id=authz.team_id,
               pricing_unit="per_second",
               quantity=Decimal(str(duration_s)),
           )   # SINGLE-BILL; fire-and-forget; quantity=0 → cost $0 + WARN (no raise)
        10. return status, resp_body

─────────────────────────────────────────────────────────────────────────────

SPEECH USE CASE FLOW (FROZEN)

  class SpeechUseCase:
      def __init__(self, *, governance: NonChatGovernance, session: AsyncSession) -> None: ...

      async def execute(
          self,
          *,
          raw_key: str | None,
          body: dict[str, Any],
          registry: ProviderRegistry,
          usage_recorder: UsageRecorder,
      ) -> tuple[AsyncIterator[bytes], str]:
        """Returns (audio_generator, media_type); billing fires inside before return."""

        1. model_id = body.get("model")
           if not model_id or not isinstance(model_id, str) or not model_id.strip():
               raise PAYLOAD_MODEL_REQUIRED.exc()
        2. input_text = body.get("input")
           if not input_text or not isinstance(input_text, str) or not input_text.strip():
               raise PAYLOAD_INPUT_REQUIRED.exc()
        3. voice = body.get("voice")
           if not voice or not isinstance(voice, str) or not voice.strip():
               raise PAYLOAD_VOICE_REQUIRED.exc()
        4. authz = await self._governance.authorize(raw_key, model_id, estimated_tokens=None)
        5. row = select(ModelRow.modality, ModelRow.provider)
                       .where(ModelRow.id == model_id, ModelRow.active.is_(True))
                 → if None: raise MODEL_UNKNOWN.exc(model_id=model_id)
        6. provider_adapter = select_provider(row.modality, row.provider, registry)
           → raises ERR_PROVIDER_UNAVAILABLE (503) if absent  ← PRE-STREAM
        7. _fire_record_with_raw(
               usage_recorder,
               tenant_id=authz.tenant_id, key_id=authz.key_id,
               model=model_id, usage=None, status=200, team_id=authz.team_id,
               pricing_unit="per_character",
               quantity=Decimal(len(input_text)),
           )   # SINGLE-BILL; fires BEFORE generator is consumed; status=200 (billing commit)
        8. media_type = _RESPONSE_FORMAT_MEDIA_TYPES.get(
               body.get("response_format", "mp3"), "audio/mpeg")
        9. gen = provider_adapter.stream_bytes("/audio/speech", body)
        10. return gen, media_type

  _RESPONSE_FORMAT_MEDIA_TYPES: dict[str, str] = {
      "mp3":  "audio/mpeg",
      "opus": "audio/opus",
      "aac":  "audio/aac",
      "flac": "audio/flac",
      "wav":  "audio/wav",
      "pcm":  "audio/pcm",
  }

─────────────────────────────────────────────────────────────────────────────

AUDIO ROUTER FLOW (FROZEN)

  @audio_router.post("/v1/audio/transcriptions")
  async def audio_transcriptions(
      request: Request,
      use_case: Annotated[TranscriptionUseCase, Depends(get_transcription_use_case)],
      registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
      usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
      raw_key: Annotated[str | None, Depends(get_raw_api_key)],
  ) -> Any:
      form = await request.form()
      status, response_body = await use_case.execute(
          raw_key=raw_key, form=form, registry=registry, usage_recorder=usage_recorder)
      return JSONResponse(content=response_body, status_code=status)

  @audio_router.post("/v1/audio/speech")
  async def audio_speech(
      request: Request,
      use_case: Annotated[SpeechUseCase, Depends(get_speech_use_case)],
      registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
      usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
      raw_key: Annotated[str | None, Depends(get_raw_api_key)],
  ) -> Any:
      body: dict[str, Any] = await request.json()
      gen, media_type = await use_case.execute(
          raw_key=raw_key, body=body, registry=registry, usage_recorder=usage_recorder)
      return StreamingResponse(gen, media_type=media_type)

─────────────────────────────────────────────────────────────────────────────

DEPS FLOW (FROZEN, mirrors embeddings_deps.py exactly)

  def get_transcription_use_case(
      request: Request,
      session: Annotated[AsyncSession, Depends(get_session)],
  ) -> TranscriptionUseCase:
      # Same construction as get_embeddings_use_case:
      #   repo → authz_use_case → authenticator → model_checker
      #   budget_guard + rate_limiter + redis_client from app.state
      #   NonChatGovernance(authenticator, model_checker, budget_guard, rate_limiter, redis_client)
      return TranscriptionUseCase(governance=governance, session=session)

  def get_speech_use_case(
      request: Request,
      session: Annotated[AsyncSession, Depends(get_session)],
  ) -> SpeechUseCase:
      # Identical construction pattern; builds SpeechUseCase instead
      return SpeechUseCase(governance=governance, session=session)

─────────────────────────────────────────────────────────────────────────────

RECORDER CALL (FROZEN, extends pricing-units TASK.md §3)

  STT:
    _fire_record_with_raw(
        usage_recorder,
        tenant_id=authz.tenant_id, key_id=authz.key_id,
        model=model_id, usage=None, status=status, team_id=authz.team_id,
        pricing_unit="per_second",
        quantity=Decimal(str(duration_s)),   # 0 when duration absent → cost $0 + WARN
    )

  TTS:
    _fire_record_with_raw(
        usage_recorder,
        tenant_id=authz.tenant_id, key_id=authz.key_id,
        model=model_id, usage=None, status=200, team_id=authz.team_id,
        pricing_unit="per_character",
        quantity=Decimal(len(input_text)),   # known from request body pre-stream
    )

  Both: SINGLE call per request path. No recording on governance error paths.
  TTS fires BEFORE StreamingResponse is constructed (bill-at-start).

─────────────────────────────────────────────────────────────────────────────

PROVIDER SELECTION CALL (FROZEN, same as embeddings)

  from gateway.proxy.infrastructure.provider_registry import select_provider, ProviderRegistry
  provider_adapter = select_provider(modality, provider, registry)
    — modality: "audio_stt" (STT) / "audio_tts" (TTS) from ModelRow query
    — provider: from ModelRow query (expected "openai" for Whisper/TTS)
    — registry: from app.state.provider_registry via get_provider_registry dep
    — raises PROVIDER_UNAVAILABLE.exc(provider=provider) when absent → 503

─────────────────────────────────────────────────────────────────────────────

WIRING DECLARATION (orchestrator applies — build MUST NOT edit these files directly)

  1. error_catalog.py — add TWO new entries (additive only):
       PAYLOAD_FILE_REQUIRED = ErrorSpec(
           422, "ERR_PAYLOAD_INVALID", "Field 'file' is required"
       )
       PAYLOAD_VOICE_REQUIRED = ErrorSpec(
           422, "ERR_PAYLOAD_INVALID", "Field 'voice' is required and non-empty"
       )
     REUSE existing PAYLOAD_INPUT_REQUIRED for TTS input — DO NOT redeclare it.
     REUSE existing PAYLOAD_MODEL_REQUIRED for model field — DO NOT redeclare it.

  2. main.py — add (additive, near the proxy_router + embeddings_router lines):
       from gateway.proxy.api.audio_router import audio_router
       app.include_router(audio_router)

  Note: python-multipart 0.0.32 is ALREADY INSTALLED. No new pyproject.toml entries needed.

─────────────────────────────────────────────────────────────────────────────

INVIOLABLE (MUST NOT be touched by the build or any future refactor of this task)

  The following files MUST be byte-identical after this task's BUILD:
    proxy/api/router.py                    — chat path
    proxy/api/deps.py                      — chat deps
    proxy/application/use_cases.py         — chat use cases
    proxy/application/governance.py        — NonChatGovernance (FROZEN)
    proxy/api/embeddings_router.py         — embeddings path
    proxy/api/embeddings_deps.py           — embeddings deps
    proxy/application/embeddings_use_case.py — embeddings use case

  AU/AT regression test provides runtime verification.

─────────────────────────────────────────────────────────────────────────────

PLACEMENT SUMMARY

  New files (build creates only these):
    proxy/api/audio_router.py              — POST /v1/audio/transcriptions + /v1/audio/speech
    proxy/api/audio_deps.py                — get_transcription_use_case + get_speech_use_case
    proxy/application/audio_use_case.py    — TranscriptionUseCase + SpeechUseCase

  Modified files (orchestrator wiring only — not touched by build):
    gateway/core/error_catalog.py          — add PAYLOAD_FILE_REQUIRED + PAYLOAD_VOICE_REQUIRED
    gateway/main.py                        — app.include_router(audio_router)
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] two accepted billing tradeoffs. (1) STT per_second
quantity = float(resp_body.get("duration") or 0.0): OpenAI returns "duration" only for
response_format=verbose_json; other formats bill 0 + recorder WARN (honest under-bill — never
fabricate a duration). AU2b pins the absent-duration→0 path. (2) TTS bill-at-start: the
per_character record (quantity=len(input_text), status=200) fires AFTER governance+provider-select
succeed and BEFORE StreamingResponse — once 200 commits, a mid-stream failure cannot reverse the
bill. Matches OpenAI's model + the chat no-retry-after-first-byte pattern; char count is known from
the request input, independent of bytes streamed. AT2 pins single-bill, AT3 pins governance-raises-
pre-stream. Both endpoints reuse the FROZEN NonChatGovernance verbatim with estimated_tokens=None.
<!-- The freeze IS the one approval — led with the bundle's lowest-confidence flags. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of governance paths + provider selection + recorder call + billing timing + chat-untouched invariant

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_au1_happy_path_200_post_multipart_called:
      arrange: seed audio_stt model + per_second snapshot via raw SQL; inject FakeAudioProvider
               as "openai" with post_multipart returning (200, {"text":"hello","duration":12.5});
               create key via admin API
      act: POST /v1/audio/transcriptions multipart (file + model="whisper-1")
      assert: status==200; body == {"text":"hello","duration":12.5};
              provider.post_multipart_calls[0].path == "/audio/transcriptions"
      RED reason: route absent → 404

  - test_au2_single_record_per_second_quantity_from_duration:
      arrange: same as AU1; inject SpyRecorder on app.state.usage_recorder;
               FakeAudioProvider returns duration=12.5
      act: POST /v1/audio/transcriptions multipart
      assert: spy.call_count==1; last_call["pricing_unit"]=="per_second";
              last_call["quantity"]==Decimal("12.5")
      RED reason: route absent → 404

  - test_au2b_duration_absent_quantity_zero_no_raise:
      arrange: same as AU1; inject SpyRecorder; FakeAudioProvider returns {"text":"hello"}
               (no duration key)
      act: POST /v1/audio/transcriptions multipart
      assert: status==200; spy.call_count==1;
              last_call["quantity"]==Decimal("0"); last_call["pricing_unit"]=="per_second"
      RED reason: route absent → 404

  - test_au3_bad_key_401:
      arrange: no Authorization header
      act: POST /v1/audio/transcriptions multipart (file + model)
      assert: status==401; body["code"]=="ERR_AUTH_INVALID_KEY"
      RED reason: route absent → 404

  - test_au4_model_not_in_allowlist_403:
      arrange: seed key with model_allowlist=["other-model"]; seed audio_stt model
      act: POST /v1/audio/transcriptions with model="whisper-1"
      assert: status==403; body["code"]=="ERR_MODEL_NOT_ALLOWED"
      RED reason: route absent → 404

  - test_au5_unknown_model_400:
      arrange: valid key; no model row for "nonexistent-stt-model"
      act: POST /v1/audio/transcriptions with model="nonexistent-stt-model" + file
      assert: status==400; body["code"]=="ERR_MODEL_UNKNOWN"
      RED reason: route absent → 404

  - test_au6_budget_exceeded_402:
      arrange: key with monthly_budget_usd=0.01; Redis spend seeded above budget
      act: POST /v1/audio/transcriptions multipart
      assert: status==402; body["code"]=="ERR_BUDGET_EXCEEDED"
      RED reason: route absent → 404

  - test_au7_rpm_exceeded_429_retry_after:
      arrange: key with rpm_limit=1; Redis RPM window seeded full
      act: POST /v1/audio/transcriptions multipart
      assert: status==429; body["code"]=="ERR_RATE_LIMITED"; Retry-After header present
      RED reason: route absent → 404

  - test_au8_provider_absent_503:
      arrange: registry with NO "openai"; active audio_stt model with provider="openai"
      act: POST /v1/audio/transcriptions multipart
      assert: status==503; body["code"]=="ERR_PROVIDER_UNAVAILABLE"
      RED reason: route absent → 404

  - test_au9_missing_file_422:
      arrange: valid key
      act: POST /v1/audio/transcriptions multipart with model only, no file
      assert: status==422; body["code"]=="ERR_PAYLOAD_INVALID"
      RED reason: route absent → 404

  - test_au9b_missing_model_422:
      arrange: valid key
      act: POST /v1/audio/transcriptions multipart with file only, no model
      assert: status==422; body["code"]=="ERR_PAYLOAD_INVALID"
      RED reason: route absent → 404

  - test_at1_tts_happy_path_200_streaming:
      arrange: seed audio_tts model + per_character snapshot; inject FakeAudioProvider
               with stream_bytes yielding b"audio-bytes-chunk"; create key
      act: POST /v1/audio/speech {"model":"tts-1","input":"Hello world","voice":"alloy"}
      assert: status==200; "audio/mpeg" in content-type header;
              response content contains b"audio-bytes-chunk";
              provider.stream_bytes_calls[0].path == "/audio/speech"
      RED reason: route absent → 404

  - test_at2_tts_single_record_per_character_quantity_len_input:
      arrange: same as AT1; inject SpyRecorder; input="Hello world" (len=11)
      act: POST /v1/audio/speech {"model":"tts-1","input":"Hello world","voice":"alloy"}
      assert: spy.call_count==1; last_call["pricing_unit"]=="per_character";
              last_call["quantity"]==Decimal(11); last_call["status"]==200
      RED reason: route absent → 404

  - test_at3_governance_raises_pre_stream_bad_key_401:
      arrange: no Authorization header
      act: POST /v1/audio/speech {"model":"tts-1","input":"hi","voice":"alloy"}
      assert: status==401; body is JSON (not audio bytes); body["code"]=="ERR_AUTH_INVALID_KEY";
              "audio" NOT in content-type header
      RED reason: route absent → 404

  - test_at4_missing_voice_422:
      arrange: valid key
      act: POST /v1/audio/speech {"model":"tts-1","input":"hi"} (no voice)
      assert: status==422; body["code"]=="ERR_PAYLOAD_INVALID"
      RED reason: route absent → 404

  - test_at5_missing_input_422:
      arrange: valid key
      act: POST /v1/audio/speech {"model":"tts-1","voice":"alloy"} (no input)
      assert: status==422; body["code"]=="ERR_PAYLOAD_INVALID"
      RED reason: route absent → 404

  - test_at6_provider_absent_503_pre_stream:
      arrange: registry with NO "openai"; active audio_tts model with provider="openai"
      act: POST /v1/audio/speech {"model":"tts-1","input":"hi","voice":"alloy"}
      assert: status==503; body["code"]=="ERR_PROVIDER_UNAVAILABLE";
              response is problem+json, not audio
      RED reason: route absent → 404

  - test_au_at_regression_chat_still_200:
      arrange: seed chat model; inject FakeCompletionUpstream on app.state.completion_upstream
      act: POST /v1/chat/completions {"model":"<chat_model>","messages":[{"role":"user","content":"hi"}]}
      assert: status==200; FakeCompletionUpstream.complete_calls has 1 entry
      GREEN-BY-DESIGN: chat path already works; serves as regression guard
</test_plan>

Tests live in: `apps/gateway/tests/audio_endpoints/`
  · `apps/gateway/tests/audio_endpoints/__init__.py`
  · `apps/gateway/tests/audio_endpoints/conftest.py`
  · `apps/gateway/tests/audio_endpoints/test_audio_endpoints.py`

Expected red/green at spec phase (before BUILD):
  - AU1–AU9b (11 STT tests): RED (route /v1/audio/transcriptions absent → 404)
  - AT1–AT6 (6 TTS tests):   RED (route /v1/audio/speech absent → 404)
  - AU/AT regression:         GREEN-BY-DESIGN (chat path already works)
  All RED failures for the RIGHT reason — not skips.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  1. INVIOLABLE: proxy/api/router.py, proxy/api/deps.py, proxy/application/use_cases.py,
     proxy/application/governance.py, proxy/api/embeddings_router.py,
     proxy/api/embeddings_deps.py, proxy/application/embeddings_use_case.py
     MUST NOT be modified — not even imports, comments, or whitespace.
  2. TTS SINGLE-BILL: _fire_record_with_raw MUST fire AFTER provider-select succeeds
     and BEFORE StreamingResponse is constructed — exactly once. No recording on any
     pre-stream error path.
  3. STT SINGLE-BILL: _fire_record_with_raw fires once after upstream returns; quantity=0
     when duration absent — never raise into proxy path.
  4. Provider-absent 503 MUST be a pre-stream error for TTS (ProblemError, not mid-stream).
  5. No new pyproject.toml dependencies (python-multipart already installed).
Code lives in: `./src/`

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
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
  - ERR_PROVIDER_UNAVAILABLE 503 rate on /v1/audio/transcriptions + /v1/audio/speech
  - per_second quantity=0 rate on /v1/audio/transcriptions → callers not using verbose_json
  - Single usage_records row per audio request (no double-billing monitor)
  - Chat + embeddings 5xx rate should not change after this milestone

Spec delta for the next loop:
  - If accurate STT billing for non-verbose_json is needed, add audio-duration-probe task
  - TTS over-billing on stream failure: revisit with a billing-correction task if complaint rate rises

### Competency deltas
- [SDD · open] STT duration source depends on verbose_json response_format — caller must explicitly
  request it for accurate billing; absent duration → $0 cost, WARN only.
  Evidence: AU2b test + [contract] flag in §3.
- [SDD · open] TTS bill-at-start: customers charged for stream failures after 200 is committed.
  Evidence: AT2 test + [contract] flag in §3. Matches OpenAI billing model.
