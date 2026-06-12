# TASK: Bounded upstream retries + backoff + timeout policy

slug: retry-policy · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Bounded upstream retries with exponential backoff + jitter and precise retryable classification on the non-streaming OpenRouter completion path

Framings weighed:
  - **Opt-in via env (chosen)**: default `GATEWAY_UPSTREAM_MAX_RETRIES=0` preserves byte-identical v5 behavior; operators enable retries by setting the knob. Additive rollout. No surprise latency increase in existing deployments.
  - **Always-on with safe defaults (rejected)**: would change observable latency behavior for every non-streaming call, violating the v6 milestone additive-rollout rule. Operators may depend on fail-fast semantics for their own client-side retry budgets.
  - **Middleware-level retry (rejected)**: retry logic at the HTTP middleware layer loses the circuit-breaker interplay invariant (breaker must be re-checked before every attempt) and cannot access structured retry metadata (Retry-After header, error classification). Must live inside `upstream.complete()`.

Must:
<must>
  - On a non-streaming completion that fails with a retryable error (connect error, pool timeout, upstream 503/504/429) and `GATEWAY_UPSTREAM_MAX_RETRIES > 0`, the upstream call MUST be retried up to `max_retries` additional attempts (total attempts = 1 + max_retries).
  - Retry attempts MUST use exponential backoff with full jitter: `delay = random(0, min(cap, base * 2^attempt))` where `base = GATEWAY_UPSTREAM_RETRY_BACKOFF_BASE_S` (default 0.5 s) and `cap = 8 s`.
  - Upstream 429 with a valid `Retry-After: <seconds>` header MUST use the Retry-After delay (not backoff) when the parsed value is ≥ 0 and ≤ 60 s; otherwise fall back to computed backoff.
  - The circuit breaker MUST be re-checked (`breaker.guard()`) before EVERY attempt including retry attempts; a `CircuitOpenError` MUST abort the retry loop immediately.
  - Every FAILED attempt MUST call `breaker.on_upstream_error()` exactly as it does today; a retry burst CAN trip the breaker (desired: the breaker aggregates real failure signal).
  - A successful retry MUST call `breaker.record_success()` and return its response; the use-case layer MUST see a single `(status, body)` outcome from `upstream.complete()`.
  - With `GATEWAY_UPSTREAM_MAX_RETRIES=0` (default) the behavior MUST be byte-identical to v5: exactly one attempt, no backoff, no delay.
  - Retries are confined to the NON-STREAMING path ONLY. The `stream()` method is unchanged; streaming has zero retry or fallback machinery in this task.
  - The 120 s non-stream timeout envelope applies to the ENTIRE `complete()` invocation including retries. The implementation MUST document that with max_retries=5 and base=0.5s the expected worst-case delay budget is ~18 s (sum of backoff caps), leaving ~102 s of actual request time — acceptable at the contracted cap of 5 retries.
  - Every retry attempt MUST emit a `structlog` event at WARNING level containing: attempt number, error class (reason), and the delay before the next attempt. Message contents and API keys MUST NOT appear in log fields.
  - A `gateway_upstream_retries_total` Prometheus counter MUST be incremented per retry attempt with labels `reason` (error classification: `connect_error`, `pool_timeout`, `upstream_5xx`, `upstream_429`) and `outcome` (`retried`, `exhausted`, `breaker_open`). The counter uses the existing per-app Prometheus registry.
  - Settings MUST validate `0 ≤ GATEWAY_UPSTREAM_MAX_RETRIES ≤ 5` via pydantic; values outside this range MUST raise `ValidationError` at startup.
  - Every new app.state seam MUST have a paired production-wiring regression test (v6 foundation rule).
</must>

Reject:
<reject>
  - Upstream HTTP 4xx (≠ 429) → passthrough verbatim, never retried — "PASSTHROUGH_4XX_NOT_RETRIED"
  - httpx.ReadTimeout (read timeout after the request was sent) → raise UpstreamUnavailableError immediately, never retried — "READ_TIMEOUT_NOT_RETRIED" (the completion may have generated on the upstream; retrying risks double billing even though OpenRouter charges on 5xx only — the conservative position for v6 is to exclude read timeouts from the retryable set; this tradeoff is explicitly weighed: a failed read may have resulted in a generated completion that the client will never receive, so retrying would issue a second generation, double the cost. v6 does not retry read timeouts.)
  - stream() path → zero retries regardless of max_retries setting — "STREAM_NO_RETRY"
  - max_retries > 5 → ValidationError at Settings construction — "MAX_RETRIES_OUT_OF_RANGE"
  - max_retries < 0 → ValidationError at Settings construction — "MAX_RETRIES_OUT_OF_RANGE"
  - CircuitOpenError during retry loop → abort loop immediately with CircuitOpenError — "BREAKER_OPEN_ABORT"
</reject>

After:
<after>
  - A retried call that eventually succeeds: `upstream.complete()` returns `(200, body)` as if it were the first attempt; the use-case recorder is called exactly once for the successful outcome; the breaker records one success.
  - A retried call that exhausts all attempts: `upstream.complete()` raises `UpstreamUnavailableError`; the use-case maps it to 502 ERR_UPSTREAM_UNAVAILABLE; the breaker has been fed one `on_upstream_error()` per failed attempt.
  - A call aborted by breaker open: `CircuitOpenError` is raised immediately from whichever attempt triggered the guard; no further upstream calls are made.
  - A passthrough 4xx: returned verbatim after exactly 1 attempt, no retry, breaker not fed (success path).
  - With default settings (retries=0): behavior is identical to v5 — exactly 1 attempt, immediate failure propagation.
  - The Prometheus counter `gateway_upstream_retries_total` reflects cumulative retry events across the lifetime of the process.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Read timeout is NOT in the retryable set (v6 decision) — lowest confidence because OpenRouter's behavior on server-side processing errors is not contractually documented: it is plausible that a read timeout always means the upstream abandoned the request (not billed), in which case retrying would be safe. If wrong (OpenRouter never bills on read timeout): the set can be expanded in a follow-up task with a one-line change to the retryable error check. The cost of being wrong now is missed recovery from transient read timeouts; the cost of being wrong the other way (retry on potential generation) is double billing — v6 takes the conservative position.

  ⚠ The 120 s non-stream timeout covers the full `complete()` invocation (retries included) — lowest confidence because httpx's `Timeout` is per-attempt, not cumulative. If the implementation does not track elapsed time or reduce per-attempt timeout, a max_retries=5 run could theoretically take up to 5 × 120 s = 600 s before the connection-level timeouts fire. The implementation MUST document this and either (a) keep the per-attempt httpx timeout as-is and accept the theoretical overrun, or (b) track elapsed wall-time and skip remaining retries if budget exceeded. v6 accepts (a) with documentation given the 120 s timeout is itself already a conservative guard and the retry count cap of 5 limits exposure.

  - [x] Jitter uses Python's `random.uniform(0, cap)` — deterministic in tests via monkeypatching `random.uniform`; no external RNG dependency needed.
  - [x] The Retry-After cap of 60 s prevents a malicious or misconfigured upstream from forcing arbitrarily long delays inside `complete()`.
  - [x] The circuit breaker remains in-process (per-replica) as established in proxy-completions; the retry burst correctly feeds the in-process breaker, which is the desired behavior for replica-level circuit isolation.
  - [x] The `gateway_upstream_retries_total` counter is registered in the per-app Prometheus registry (same pattern as existing counters); it is NOT persisted across restarts.
  - [x] Streaming path: the `stream()` generator is not modified. The `breaker.guard()` call at stream entry is unchanged. No new retry code touches `stream()`.
</assumptions>

### Retryable failure classification — authoritative table

| Failure | Retried? | Reason |
|---|---|---|
| `httpx.ConnectError` | YES | Request never reached upstream; safe to retry |
| `httpx.ConnectTimeout` | YES | TCP handshake failed; no bytes sent upstream |
| `httpx.PoolTimeout` | YES | Connection not acquired from pool; no bytes sent |
| `httpx.WriteTimeout` | NO | Request body partially written; state unknown — conservative |
| `httpx.ReadTimeout` | NO | Request reached upstream; completion may have generated — double-bill risk |
| `httpx.NetworkError` (general) | NO | State unknown; treat conservatively like WriteTimeout |
| Upstream HTTP 429 | YES | Rate limited; no completion generated; Retry-After respected |
| Upstream HTTP 500/502/503/504 (5xx) | YES | No completion billed by OpenRouter on 5xx |
| Upstream HTTP 4xx (≠ 429) | NO | Passthrough semantics frozen; caller error, not upstream |
| `CircuitOpenError` | NO | Breaker is open; abort retry loop immediately |

> **Note on httpx exception hierarchy**: `httpx.ConnectError`, `httpx.ConnectTimeout`, and `httpx.PoolTimeout` are the safe subset of `httpx.TransportError`. `httpx.ReadTimeout` and `httpx.WriteTimeout` are excluded. `httpx.NetworkError` is the base for connection-layer errors and is excluded conservatively; in practice `ConnectError` covers the concrete network cases we want to retry.

### Read-timeout tradeoff (weighed explicitly per context)

A `ReadTimeout` means the HTTP request was fully sent to OpenRouter and we waited for a response byte that never came within 120 s. Two interpretations exist:
1. The request was queued but not yet processed (safe to retry).
2. The request was processed and a response was generated but the network dropped the reply (double-bill risk).

OpenRouter's error policy is not publicly documented at the level of "was this request billed before the timeout?" v6 takes the conservative position: **do not retry read timeouts**. The cost of being wrong is that some transient read timeouts are not recovered — the client gets a 502. The cost of retrying when wrong is a duplicate generation billed to the tenant. The tradeoff favors billing correctness (the milestone's primary invariant).

### Single-bill construction argument

Retries occur INSIDE `upstream.complete()`, below the use-case layer. The use-case calls `upstream.complete(body)` exactly once and awaits a single `(status, body)` tuple OR a single exception. The `_fire_record_with_raw` call in `use_cases.py` is triggered exactly once regardless of how many internal attempts occurred. The recorder/flusher path (the only ledger write path) therefore sees at most one outcome per request. Double billing by construction is impossible at the use-case boundary.

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: R1 — two transient 503s then success with retries=2
  Given GATEWAY_UPSTREAM_MAX_RETRIES=2 and a circuit breaker with threshold=5
  When upstream returns HTTP 503 twice then HTTP 200 on the third attempt
  Then complete() returns (200, body)
  And the transport received exactly 3 POST requests
  And breaker.on_upstream_error was called exactly 2 times
  And breaker.record_success was called exactly 1 time

Scenario: R2 — persistent 503 exhausts all retries
  Given GATEWAY_UPSTREAM_MAX_RETRIES=2 and upstream always returns HTTP 503
  When complete() is called
  Then UpstreamUnavailableError is raised after exactly 3 POST attempts (1 + 2 retries)
  And breaker.on_upstream_error was called exactly 3 times

Scenario: R3 — default settings (retries=0) preserves v5 one-shot behavior
  Given GATEWAY_UPSTREAM_MAX_RETRIES=0 (default) and upstream returns HTTP 503
  When complete() is called
  Then UpstreamUnavailableError is raised after exactly 1 POST attempt
  And no backoff delay was computed

Scenario: R4 — 429 with Retry-After header uses header delay not backoff
  Given GATEWAY_UPSTREAM_MAX_RETRIES=2 and upstream returns HTTP 429 with Retry-After: 1
  When complete() is called (monkeypatched sleep captures delay)
  Then the computed delay for the first retry is >= 1.0 s (from Retry-After)
  And the second attempt is made after that delay

Scenario: R5 — 400 upstream is passthrough, never retried
  Given GATEWAY_UPSTREAM_MAX_RETRIES=2 and upstream returns HTTP 400
  When complete() is called
  Then (400, body) is returned after exactly 1 POST attempt
  And no retry was attempted

Scenario: R6 — connect error then success; breaker fed once
  Given GATEWAY_UPSTREAM_MAX_RETRIES=2 and upstream raises ConnectError then returns HTTP 200
  When complete() is called
  Then complete() returns (200, body)
  And the transport made exactly 2 POST requests
  And breaker.on_upstream_error was called exactly 1 time
  And breaker.record_success was called exactly 1 time

Scenario: R7 — breaker opens between attempts, loop aborts immediately
  Given GATEWAY_UPSTREAM_MAX_RETRIES=2 and a circuit breaker that is closed for attempt 1 but open for attempt 2
  When upstream returns HTTP 503 on attempt 1 (tripping the breaker)
  Then CircuitOpenError is raised before attempt 2 is made
  And exactly 1 POST was sent to the transport

Scenario: R8 — retried success records usage exactly once at use-case level
  Given GATEWAY_UPSTREAM_MAX_RETRIES=1 and upstream returns 503 then 200
  When the use case calls upstream.complete() once and a FakeUsageRecorder is wired
  Then the usage recorder.record() is called exactly once
  And the recorded status is 200

Scenario: R9 (GREEN) — stream() path has zero retry machinery with retries=2
  Given GATEWAY_UPSTREAM_MAX_RETRIES=2 and upstream returns HTTP 503 on stream
  When stream() is called and the generator is iterated
  Then UpstreamUnavailableError is raised after exactly 1 POST to the transport
  And no retry delay was applied

Scenario: Settings validation — max_retries=9 raises ValidationError
  Given GATEWAY_UPSTREAM_MAX_RETRIES=9
  When Settings is constructed
  Then pydantic ValidationError is raised
  And the error references max_retries out of range
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
INTERNAL SEAM (not an HTTP endpoint)
  upstream.complete(payload: dict) -> (int, dict)
    Success path (any attempt): (status_code, json_body) where status_code may be
      - 200 (or other 2xx): upstream success — returned verbatim
      - 4xx (≠ 429): passthrough — returned verbatim after exactly 1 attempt
    Error path (all retries exhausted or non-retryable): raises UpstreamUnavailableError
    Breaker abort: raises CircuitOpenError (re-raised from breaker.guard())

  upstream.stream(payload: dict) -> AsyncIterator[bytes]
    Unchanged: zero retry machinery; breaker guard at entry only.

SETTINGS (gateway/core/config.py additions)
  GATEWAY_UPSTREAM_MAX_RETRIES    int  default=0  validate: 0 ≤ x ≤ 5
  GATEWAY_UPSTREAM_RETRY_BACKOFF_BASE_S  float  default=0.5  validate: > 0
  (The backoff cap is a code constant: 8.0 s)

BACKOFF FORMULA
  attempt ∈ {1, 2, ..., max_retries}  (attempt=1 = first retry)
  window = min(cap, base * 2^attempt)
  delay = random.uniform(0, window)          # full jitter
  For 429 with valid Retry-After h (0 ≤ h ≤ 60): delay = h (overrides backoff)

RETRYABLE CLASSIFICATION (authoritative — supersedes any prior prose)
  ConnectError / ConnectTimeout / PoolTimeout → retried
  ReadTimeout / WriteTimeout / NetworkError   → NOT retried
  HTTP 5xx (≥ 500)                            → retried
  HTTP 429                                    → retried (Retry-After honored)
  HTTP 4xx (< 500, ≠ 429)                     → NOT retried (passthrough)

CIRCUIT BREAKER INTERPLAY
  - breaker.guard() BEFORE every attempt (including retries); CircuitOpenError aborts loop.
  - breaker.on_upstream_error() AFTER every failed attempt (retryable or not).
  - breaker.record_success() AFTER the first successful response (any attempt).
  - A retry burst CAN trip the breaker; this is correct — the breaker aggregates failure signal.

OBSERVABILITY
  Prometheus counter: gateway_upstream_retries_total{reason, outcome}
    reason  ∈ {connect_error, pool_timeout, upstream_5xx, upstream_429}
    outcome ∈ {retried, exhausted, breaker_open}
  Structlog WARNING event per retry: attempt=N, reason=<class>, delay=<float>
    MUST NOT include message contents or API key material.

SINGLE-BILL INVARIANT
  Retries are internal to upstream.complete(). The use-case calls complete() once.
  _fire_record_with_raw() in use_cases.py is reached exactly once per request.
  No ledger write path is exposed to retry iteration.

SUPERSESSION BLOCK
  This contract supersedes the "NEVER retry a completion (non-idempotent)" prose in:
    - apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py (module docstring)
    - .add/tasks/proxy-completions/TASK.md §1 Must bullet "NEVER retry a completion"
    - .add/tasks/proxy-completions/TASK.md §5 (docstring pin)
  Precedent: JwksKeyCache supersession in oidc-tenant-config (same pattern — record at
  freeze in the new task; do NOT edit the frozen file).
  Preservation mechanism: default max_retries=0 makes the new code's behavior
  byte-identical to the prior "NEVER retry" rule until an operator explicitly sets
  GATEWAY_UPSTREAM_MAX_RETRIES > 0. The "NEVER retry" prose therefore remains
  descriptively accurate for all deployments running default settings.

LOWEST-CONFIDENCE FLAGS AT FREEZE
  ⚠ [spec] Read timeout excluded from retryable set: if OpenRouter's billing model
    guarantees no charge on read timeouts, this exclusion is overly conservative and
    blocks recovery from a common failure mode. Evidence would require contractual
    documentation from OpenRouter. Cost of current decision: missed recovery on
    transient read timeouts. Cost of wrong direction: double billing.
  ⚠ [contract] Per-attempt vs. cumulative timeout — CORRECTED AT FREEZE (orchestrator):
    the 738s figure assumed every attempt can burn the full 120s read budget, but a
    ReadTimeout is NOT retryable under this very contract — a long stall consumes the
    read budget at most ONCE (the final/only attempt that gets that far). Retryable
    failures are fast-fail classes: connect-family ≤ connect timeout (10s), 5xx/429 are
    received responses. Realistic worst-case wall time ≈ retries × (10s connect + ≤8s
    backoff) + one 120s read ≈ 210s at max_retries=5; 429 Retry-After (capped 60s)
    can stretch a 429-storm to ≈ 300s of waiting — operator-opted via the retry knob.
    A cumulative-deadline guard remains a sensible future hardening, not a v6 blocker.
```

Least-sure flag surfaced at freeze: the [spec] read-timeout exclusion above — most
likely to be revisited; cost asymmetry (double-bill vs missed recovery) justifies the
conservative default until OpenRouter's no-charge-on-timeout behavior is documented.

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
<!-- The freeze IS the one approval — the orchestrator (Tin Dang) froze after review;
     envelope flag corrected at freeze (see above); supersession of proxy-completions
     never-retry prose recorded per JwksKeyCache precedent. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% of retry/backoff/breaker-interplay paths in the new retry logic seam

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_r1_two_503s_then_200_retried_success:
      arrange: MockTransport serving 503, 503, 200 in sequence; max_retries=2; fresh CircuitBreaker
      act: await upstream.complete(payload)
      assert: returns (200, body); transport call_count == 3; breaker error_calls == 2; breaker success_calls == 1

  - test_r2_persistent_503_exhausts_all_retries:
      arrange: MockTransport always 503; max_retries=2
      act: await upstream.complete(payload) → expect UpstreamUnavailableError
      assert: raises UpstreamUnavailableError; transport call_count == 3; breaker error_calls == 3

  - test_r3_default_retries_zero_one_shot:
      arrange: MockTransport always 503; max_retries=0 (default)
      act: await upstream.complete(payload) → expect UpstreamUnavailableError
      assert: raises UpstreamUnavailableError; transport call_count == 1

  - test_r4_429_retry_after_header_used_as_delay:
      arrange: MockTransport returns 429 + Retry-After:1 then 200; monkeypatch asyncio.sleep; max_retries=2
      act: await upstream.complete(payload)
      assert: sleep called with value >= 1.0; returns (200, body)

  - test_r5_upstream_400_passthrough_no_retry:
      arrange: MockTransport returns 400; max_retries=2
      act: await upstream.complete(payload)
      assert: returns (400, body); transport call_count == 1

  - test_r6_connect_error_then_success:
      arrange: MockTransport raises ConnectError on first call, returns 200 on second; max_retries=2
      act: await upstream.complete(payload)
      assert: returns (200, body); transport call_count == 2; breaker error_calls == 1; breaker success_calls == 1

  - test_r7_breaker_opens_mid_loop_aborts_retries:
      arrange: MockTransport returns 503 on first call; CircuitBreaker with threshold=1 (trips on 1 failure); max_retries=2
      act: await upstream.complete(payload) → expect CircuitOpenError
      assert: raises CircuitOpenError; transport call_count == 1 (only 1 attempt before breaker trips)

  - test_r8_retried_success_records_usage_exactly_once:
      arrange: RetryableUpstream (503 then 200); FakeUsageRecorder; max_retries=1
      act: call use_case.complete() with these fakes (bypass DB by direct upstream call path)
      assert: usage_recorder.record called exactly once; recorded status == 200
      (Note: this pins the single-call-site invariant by construction — the use case
       calls upstream.complete() once and the recorder fires once from that single outcome)

  - test_r9_stream_path_no_retry_green:
      arrange: MockTransport returns 503; max_retries=2; call stream() path
      act: async for chunk in upstream.stream(payload) → collect chunks, expect UpstreamUnavailableError
      assert: UpstreamUnavailableError raised; transport call_count == 1

  - test_settings_max_retries_out_of_range:
      arrange: attempt to construct Settings(upstream_max_retries=9)
      act: Settings construction
      assert: raises pydantic ValidationError
</test_plan>

Tests live in: `apps/gateway/tests/retry_policy/` · `apps/gateway/tests/retry_policy/conftest.py` · `apps/gateway/tests/retry_policy/test_retry_policy.py`

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): every retry attempt feeds the circuit breaker's `on_upstream_error()` BEFORE deciding to retry; the breaker guard is checked BEFORE every attempt including the first; no ledger write path is inside the retry loop.

Code lives in:
  - `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py` (retry loop inside `complete()`)
  - `apps/gateway/src/gateway/core/config.py` (two new Settings fields with validators)

Constraints: do NOT change any test or the contract; allow-list packages only (`tenacity` is already a listed dependency but prefer a hand-rolled loop for explicit breaker interplay control); ask if unclear.

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

### Deep checks — do not skim
- [ ] WIRING (code) — `upstream_max_retries` and `upstream_retry_backoff_base_s` are wired from Settings into `OpenRouterCompletionUpstream.__init__` in `main.py`; paired regression test asserts app.state.completion_upstream carries the configured values
- [ ] DEAD-CODE — no new unused symbols; the retry counter must be reachable from at least one code path
- [ ] SEMANTIC — retry loop cannot fire on the stream() path; confirm by code review + R9 green

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>
Reviewed by: <name> · date: <date>

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
  - `gateway_upstream_retries_total{outcome="exhausted"}` — spike means upstream is degraded
  - `gateway_upstream_retries_total{outcome="breaker_open"}` — indicates cascade suppression active
  - P99 latency of `/v1/chat/completions` (non-stream) — should increase by at most `max_retries * cap` in worst case

Spec delta for the next loop: model-fallbacks task inherits the retryable set from this contract; if read timeouts are reclassified after OpenRouter documentation becomes available, the retryable table change propagates to model-fallbacks as well.

### Competency deltas
- [SDD · open] The "NEVER retry" prose in a frozen task required a SUPERSESSION pattern rather than file edit — evidence: proxy-completions TASK.md §1 pin; JwksKeyCache precedent confirmed the approach works across milestones.
- [TDD · open] Full-jitter backoff requires monkeypatching both `random.uniform` AND `asyncio.sleep` to assert timing without wall-clock waits — evidence: R4 design captures the sleep call rather than measuring elapsed time.
- [ADD · open] Risk=high tasks need explicit retryable-classification tables in §1 to prevent ambiguous build-phase interpretation; the table format proved load-bearing here.
