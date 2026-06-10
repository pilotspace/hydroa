# TASK: /v1/chat/completions SSE pass-through with failure design

slug: proxy-completions · created: 2026-06-10 · stage: mvp · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: OpenAI-compatible /v1/chat/completions proxy — streaming and non-streaming pass-through to OpenRouter with in-gateway key auth, model validation, circuit breaker, and usage-recorder seam
Framings weighed: in-gateway-auth (chosen) · edge-only-auth (rejected: ext_authz is for Envoy→Gateway; direct API-key clients hit the gateway directly in dev and test) · async-polling (rejected: SSE is the OpenAI standard, streaming is in-scope)
Must:
<must>
  - POST /v1/chat/completions with a valid `Authorization: Bearer sk-...` key, a known+active model, and a non-empty messages list forwards the request to OpenRouter and returns its response verbatim
  - Non-streaming (stream absent or false): respond 200 with OpenRouter's JSON body forwarded byte-for-byte including the upstream `usage` field; Content-Type mirrors upstream
  - Streaming (stream: true): respond with StreamingResponse, Content-Type text/event-stream; pass through every raw SSE byte chunk from OpenRouter unchanged, including the terminal `data: [DONE]\n\n` chunk
  - After every completion (streaming or non-streaming, success or upstream error) the UsageRecorder port is invoked fire-and-forget with: tenant_id, key_id, model, raw upstream usage payload (may be None for streams until upstream sends it), status code
  - NoopUsageRecorder is wired in main.py by default; the usage-metering task replaces it
  - Key authentication is delegated to the existing keys domain through a proxy-domain port KeyAuthenticator (Protocol); the infrastructure adapter calls AuthzUseCase from gateway.keys; missing or invalid or revoked key → 401 ERR_AUTH_INVALID_KEY; no upstream call is made
  - Model validation via a proxy-domain port ModelChecker (Protocol); the infrastructure adapter queries the catalog ORM; model field absent or empty → 422 ERR_PAYLOAD_INVALID; model present but not in catalog or not active → 400 ERR_MODEL_UNKNOWN; no upstream call is made
  - Upstream communication via domain port CompletionUpstream (Protocol) with two methods: `complete(payload) -> (int, dict)` and `stream(payload) -> AsyncIterator[bytes]`; infrastructure adapter OpenRouterCompletionUpstream uses httpx.AsyncClient with base URL https://openrouter.ai/api/v1, platform key from settings GATEWAY_OPENROUTER_API_KEY, connect timeout 10 s, non-stream total timeout 120 s, streaming read timeout 300 s
  - NEVER retry a completion (non-idempotent)
  - Circuit breaker on OpenRouterCompletionUpstream: 5 consecutive upstream failures (5xx / timeout / network error) → open state for 30 s → 502 ERR_UPSTREAM_UNAVAILABLE without calling upstream; after 30 s cooldown (half-open): next request tries upstream — success closes the breaker, failure extends open 30 s more
  - Upstream HTTP 4xx response: pass through verbatim (status code + body) — do not convert to problem+json
  - Upstream HTTP 5xx, connection error, or read timeout: return 502 ERR_UPSTREAM_UNAVAILABLE problem+json; do NOT expose upstream error detail
  - messages list empty or absent → 422 ERR_PAYLOAD_INVALID (same code as model-absent)
  - All gateway-generated error responses are RFC 9457 problem+json (gateway.core.errors)
  - Clean architecture: gateway/proxy/{domain,application,infrastructure,api} mirroring gateway/tenants structure; domain has zero framework imports
</must>
Reject:
<reject>
  - Missing or empty Authorization header → "ERR_AUTH_INVALID_KEY" (401)
  - Authorization header present but key unknown, revoked, or malformed → "ERR_AUTH_INVALID_KEY" (401)
  - Body missing `model` field or model is empty string → "ERR_PAYLOAD_INVALID" (422)
  - Body missing `messages` field or messages is empty list → "ERR_PAYLOAD_INVALID" (422)
  - `model` value not in catalog or model.active=false → "ERR_MODEL_UNKNOWN" (400)
  - Upstream HTTP 5xx, connection error, or read timeout (below circuit-breaker threshold) → "ERR_UPSTREAM_UNAVAILABLE" (502)
  - Circuit breaker is open (5+ consecutive failures within cooldown window) → "ERR_UPSTREAM_UNAVAILABLE" (502) without calling upstream
</reject>
After:
<after>
  - On success: caller receives either the full JSON body (non-stream) or a complete SSE stream ending with [DONE]; the upstream response is forwarded without modification; the UsageRecorder port has been invoked once with correct identifiers
  - On gateway rejection (401/422/400): no upstream call was made; upstream call count is unchanged; UsageRecorder records the failed attempt
  - On upstream error: caller receives 502 problem+json; the circuit breaker failure counter has been incremented
  - On circuit breaker open: caller receives 502 problem+json; no upstream call was made
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The circuit breaker state is in-process (instance-level counter) for MVP — lowest confidence because with multiple gateway replicas each instance has independent state, so 5-failures-per-instance is the semantic (not 5 failures across the fleet); if wrong (operator wants fleet-level circuit breaking): need Redis-backed counter — contained change to the infrastructure adapter only, domain port unchanged
  ⚠ UsageRecorder is called fire-and-forget (asyncio.create_task) without awaiting — lowest confidence because if the event loop shuts down before the task completes the record may be lost; if wrong (strict at-least-once): switch to awaited call or a write-behind queue — the port signature is unchanged, only the invocation pattern changes
  - [x] Streaming usage: for SSE streams, the upstream `usage` field arrives in the final chunk or a synthetic `[DONE]` event; recording "None" usage initially and reconciling is the usage-metering task's responsibility — this task only forwards raw bytes
  - [x] The proxy does NOT set or inspect X-Forwarded-For or strip auth headers beyond extracting the Bearer token — Envoy handles edge concerns
  - [x] The platform OpenRouter API key is a single shared key from settings (GATEWAY_OPENROUTER_API_KEY) — per-tenant BYOK is explicitly Out of v1 scope
  - [x] Content-Type of non-streaming responses is forwarded from upstream (typically application/json); the gateway does not re-encode the body
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost-if-wrong. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: non-streaming completion forwarded verbatim
  Given a valid API key for tenant "Acme" and an active model "openai/gpt-4o"
  When POST /v1/chat/completions with stream: false (or absent) and a non-empty messages list
  Then the response is 200 with the upstream JSON body forwarded byte-for-byte including the usage field
  And the upstream received exactly one call

Scenario: streaming completion passes through SSE byte-for-byte
  Given a valid API key for tenant "Acme" and an active model "openai/gpt-4o"
  When POST /v1/chat/completions with stream: true
  Then the response status is 200, Content-Type is text/event-stream
  And every chunk received equals the byte sequence the fake upstream emitted
  And the stream ends with the upstream's data: [DONE] chunk

Scenario: usage recorder is invoked after completion
  Given a valid API key and active model, with a FakeUsageRecorder wired into app.state
  When POST /v1/chat/completions succeeds (non-streaming)
  Then the FakeUsageRecorder.record was called exactly once
  And the call carried the correct tenant_id, key_id, model, and status code

Scenario: missing Authorization header rejected — no upstream call
  Given no Authorization header
  When POST /v1/chat/completions
  Then the response is 401 problem+json with code "ERR_AUTH_INVALID_KEY"
  And the upstream received zero calls

Scenario: revoked API key rejected — no upstream call
  Given an API key that has been revoked
  When POST /v1/chat/completions with that key
  Then the response is 401 problem+json with code "ERR_AUTH_INVALID_KEY"
  And the upstream received zero calls

Scenario: malformed payload rejected — no upstream call
  Given a valid API key and active model
  When POST /v1/chat/completions with messages: [] (empty list)
  Then the response is 422 problem+json with code "ERR_PAYLOAD_INVALID"
  And the upstream received zero calls

Scenario: unknown model rejected — no upstream call
  Given a valid API key and a model id that does not exist in the catalog
  When POST /v1/chat/completions with that model
  Then the response is 400 problem+json with code "ERR_MODEL_UNKNOWN"
  And the upstream received zero calls

Scenario: upstream 4xx passes through verbatim
  Given a valid API key and active model, FakeCompletionUpstream returns 429 {"error": "rate_limited"}
  When POST /v1/chat/completions
  Then the response status is 429 and the body is {"error": "rate_limited"} byte-for-byte

Scenario: upstream 5xx returns 502 ERR_UPSTREAM_UNAVAILABLE
  Given a valid API key and active model, FakeCompletionUpstream returns 500 {"error": "upstream_fail"}
  When POST /v1/chat/completions
  Then the response is 502 problem+json with code "ERR_UPSTREAM_UNAVAILABLE"
  And the upstream error detail is NOT present in the gateway response

Scenario: circuit breaker opens after 5 consecutive upstream failures
  Given a valid API key, active model, and FakeCompletionUpstream that always returns 500
  When POST /v1/chat/completions is called 6 times (5 failures + 1 more)
  Then the first 5 calls return 502 ERR_UPSTREAM_UNAVAILABLE (each calling upstream)
  And the 6th call returns 502 ERR_UPSTREAM_UNAVAILABLE WITHOUT calling upstream (breaker open)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /v1/chat/completions
  header: Authorization: Bearer sk-<key_id_hex>.<secret>
  body (JSON): {
    model: str (required, non-empty),
    messages: list[{role: str, content: str}] (required, non-empty),
    stream?: bool (default false),
    ...passthrough fields forwarded verbatim to OpenRouter
  }

  200 (non-stream) -> upstream JSON body forwarded verbatim (includes usage field)
                      Content-Type: application/json (mirrored from upstream)

  200 (stream: true) -> StreamingResponse
                        Content-Type: text/event-stream
                        body: raw SSE byte chunks from OpenRouter, byte-identical
                        terminal chunk: data: [DONE]\n\n

  400 -> problem+json { type: "about:blank", title: str, status: 400, code: "ERR_MODEL_UNKNOWN" }
  401 -> problem+json { type: "about:blank", title: str, status: 401, code: "ERR_AUTH_INVALID_KEY" }
  4xx (upstream) -> upstream status + upstream body verbatim (no problem+json wrapping)
  422 -> problem+json { type: "about:blank", title: str, status: 422, code: "ERR_PAYLOAD_INVALID" }
  502 -> problem+json { type: "about:blank", title: str, status: 502, code: "ERR_UPSTREAM_UNAVAILABLE" }

Domain ports (new, in gateway/proxy/domain/ports.py):

  class KeyAuthenticator(Protocol):
    async def authenticate(self, raw_key: str) -> AuthzResult:
      """Raises InvalidApiKeyError (from gateway.keys.domain.errors) on failure."""

  class ModelChecker(Protocol):
    async def is_active(self, model_id: str) -> bool:
      """Returns True iff model exists and active=true in catalog."""

  class CompletionUpstream(Protocol):
    async def complete(self, payload: dict) -> tuple[int, dict]:
      """Forward non-streaming request. Returns (status_code, json_body)."""
    def stream(self, payload: dict) -> AsyncIterator[bytes]:
      """Yield raw SSE byte chunks from upstream."""

  class UsageRecorder(Protocol):
    async def record(
      self, *, tenant_id: UUID, key_id: UUID, model: str,
      usage: dict | None, status: int
    ) -> None:
      """Append a usage event. Called fire-and-forget; NoopUsageRecorder by default."""

Schema: no new tables (this task); reads models.id + models.active via ModelChecker port;
        reads api_keys via KeyAuthenticator port (delegates to AuthzUseCase).

app.state extensions (wired in main.py):
  app.state.completion_upstream  -> OpenRouterCompletionUpstream (or FakeCompletionUpstream in tests)
  app.state.usage_recorder       -> NoopUsageRecorder (replaced by usage-metering task)
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [spec] Circuit breaker state is in-process (per-replica) for MVP — lowest confidence because gateway runs multiple replicas; 5-failures-per-instance != 5-failures-fleet-wide; if wrong: Redis-backed counter needed — contained change to infrastructure adapter only, port/contract unchanged.
⚠ [spec] UsageRecorder invoked fire-and-forget (asyncio.create_task) — lowest confidence because task loss on event-loop shutdown is silently accepted; if wrong (strict at-least-once guarantee needed): switch to awaited call or write-behind queue — port signature unchanged.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_non_streaming_completion_forwarded_verbatim: arrange valid key + active model + FakeCompletionUpstream returning 200 JSON / act POST /v1/chat/completions stream:false / assert 200 + body byte-identical to fake response including usage field
  - test_streaming_completion_sse_byte_identical: arrange valid key + active model + FakeCompletionUpstream.stream yielding known SSE chunks / act POST stream:true / assert 200 + Content-Type text/event-stream + response body equals concatenated fake chunks
  - test_usage_recorder_invoked_after_completion: arrange FakeUsageRecorder on app.state + valid key + active model / act POST non-stream / assert recorder.record called once with correct tenant_id, key_id, model, status 200
  - test_missing_auth_rejected_no_upstream_call: arrange FakeCompletionUpstream with call counter / act POST with no Authorization header / assert 401 ERR_AUTH_INVALID_KEY + upstream call count == 0
  - test_revoked_key_rejected_no_upstream_call: arrange revoked key via DB + FakeCompletionUpstream / act POST / assert 401 ERR_AUTH_INVALID_KEY + upstream call count == 0
  - test_malformed_payload_rejected_no_upstream_call: arrange valid key + active model / act POST with messages:[] / assert 422 ERR_PAYLOAD_INVALID + upstream call count == 0
  - test_unknown_model_rejected_no_upstream_call: arrange valid key + model not in catalog / act POST / assert 400 ERR_MODEL_UNKNOWN + upstream call count == 0
  - test_upstream_4xx_passed_through_verbatim: arrange FakeCompletionUpstream returning 429 {"error": "rate_limited"} / act POST / assert response status 429 + body {"error": "rate_limited"} byte-identical
  - test_upstream_5xx_returns_502: arrange FakeCompletionUpstream returning 500 / act POST / assert 502 ERR_UPSTREAM_UNAVAILABLE + upstream detail absent from response body
  - test_circuit_breaker_opens_after_5_failures: arrange FakeCompletionUpstream always returning 500 / act POST x6 / assert calls 1–5 each hit upstream (call count increments) and return 502 / assert call 6 returns 502 and upstream NOT called (call count still 5)
</test_plan>

Tests live in: `apps/gateway/tests/proxy/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): NEVER retry a completion (non-idempotent); circuit breaker must protect against cascading upstream failure; UsageRecorder must be called even on upstream error so no request goes unrecorded; plaintext API key must never be forwarded to OpenRouter (only the platform key goes upstream).
Code lives in: `apps/gateway/src/gateway/` (new module `proxy/`); wiring additions in `main.py`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

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

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 401 rate (credential stuffing signal) · 400 ERR_MODEL_UNKNOWN rate (catalog drift) · 502 rate per error type (upstream health) · circuit-breaker trip events · p50/p99 non-stream latency · streaming TTFB (time to first byte)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
