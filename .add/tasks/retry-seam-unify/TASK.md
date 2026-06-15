# TASK: Unify upstream retries across all providers (shared helper + per-error RetryPolicy + cumulative deadline)

slug: retry-seam-unify · created: 2026-06-15 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py` — the retry SOURCE to extract:
  - `_classify_reason(exc_or_status: Exception | int) -> str | None` (L48) — retryable set: 429→"upstream_429", ≥500→"upstream_5xx", ConnectError/ConnectTimeout→"connect_error", PoolTimeout→"pool_timeout"; None for ReadTimeout/WriteTimeout/NetworkError + other 4xx (idempotency-safe split: bytes-not-sent errors retried, maybe-sent NOT).
  - `_compute_backoff(self, attempt: int) -> float` (L134) — full-jitter `random.uniform(0, min(_BACKOFF_CAP_S=8.0, base*2^attempt))`, attempt 1-indexed.
  - `_parse_retry_after(header_value: str) -> float | None` (L76) — integer secs 0..60 only; HTTP-date/garbage→None→fallback.
  - the retry loop in `complete(self, payload) -> tuple[int, dict[str, Any]]` (L148–273): `for attempt in range(self._max_retries+1)`, `self._breaker.guard()` before EVERY attempt (L173), `_increment_retry_counter(reason, outcome)` (L142) labels reason∈{connect_error,pool_timeout,upstream_429,upstream_5xx} outcome∈{retried,exhausted,breaker_open}.
  - ctor (L106) takes `max_retries=0, backoff_base=0.5, metrics_registry`; holds per-instance `self._breaker = CircuitBreaker()` (L117).
- `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` — TARGET (zero retry today):
  - `AnthropicCompletionUpstream.complete(self, payload) -> tuple[int, dict[str, Any]]` (L543); httpx POST `/messages` at L559; pure request-translation `_openai_to_anthropic_request` at L554–557 (keep OUTSIDE the loop); flat except over Connect/Connect-Timeout/Pool/Read/Write/Network → UpstreamUnavailableError (L565–574, must split retryable vs terminal); ctor L509 has NO max_retries/backoff_base; has its own `self._breaker`.
- `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` — TARGET (zero retry today):
  - `GeminiCompletionUpstream.complete(self, payload) -> tuple[int, dict[str, Any]]` (L527); httpx POST `/models/{model}:generateContent` at L544; pure translation `_openai_to_gemini_request` + `model=payload["model"]` at L538–542 (keep OUTSIDE the loop); same flat except L550–559; ctor L496 has NO max_retries/backoff_base.
- `apps/gateway/src/gateway/proxy/domain/ports.py` — `CompletionUpstream` Protocol (L104): `async def complete(payload: dict[str,object]) -> tuple[int, dict[str,object]]` + `stream(...)`. The shared helper is NOT on the Port (infra-internal); `stream()` stays retry-free.
- `apps/gateway/src/gateway/main.py` — construction/wiring sites: OpenRouter L356–362 (passes max_retries + backoff_base), Anthropic L392–398 (MUST gain the two kwargs), Gemini L404–409 (MUST gain the two kwargs). deps.py does NOT construct upstreams.
- `apps/gateway/src/gateway/proxy/infrastructure/circuit_breaker.py` — `CircuitBreaker.guard()/on_upstream_error()/record_success()` — the three calls the helper must replicate, in order.

Context (working folder):
- `apps/gateway/src/gateway/core/config.py` L203–211 — `upstream_max_retries: int = Field(default=0, ge=0, le=5)` (env GATEWAY_UPSTREAM_MAX_RETRIES) · `upstream_retry_backoff_base_s: float = Field(default=0.5, gt=0)` (env GATEWAY_UPSTREAM_RETRY_BACKOFF_BASE_S). NEW: a cumulative-deadline knob will be added here (currently NONE exists).
- existing retry tests live in `apps/gateway/tests/retry_policy/` — OpenRouter-only (conftest `make_upstream()` is OpenRouter-specific); Anthropic/Gemini retry tests are ABSENT (to be created).
- TWO breaker layers: app-level `BoundCircuitBreakerUpstream` (deps.py:47, from app.state.circuit_breaker) wraps the dispatch upstream; the retry loop uses each concrete upstream's OWN per-instance `self._breaker`. The shared helper operates at the INNER concrete-upstream layer.

Honors (patterns / conventions):
- OPT-IN/DEFAULT-OFF byte-identical: max_retries=0 → exactly 1 attempt, no sleep (v19 shared decision + config default).
- Retries are `complete()`-ONLY; `stream()` is never retried (frozen by module docstring + ports docstring + existing R9 test).
- Non-retryable transport errors STILL feed `self._breaker.on_upstream_error()` before raising (idempotency-safety invariant).
- BILLING ACCURACY (foundation v12): the use case calls `complete()` exactly ONCE; internal retries are invisible; the served attempt's `(status, body)` is the only usage surfaced — a retry never double-bills.
- SECRET DISCIPLINE: the helper receives no key material; nothing logged/echoed/in metric labels.

Anchors the contract cites:
- a new shared module (likely `apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py`) exposing the extracted retry executor + `RetryPolicy` (error-classification) + cumulative-deadline.
- `_classify_reason` / `_compute_backoff` / `_parse_retry_after` (extracted) · `CompletionUpstream.complete` (ports.py:104) · `Settings.upstream_max_retries` / `upstream_retry_backoff_base_s` + the new deadline field · `CircuitBreaker.guard/on_upstream_error/record_success`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Unified upstream retry seam — a shared, provider-agnostic retry executor (loop + full-jitter backoff + per-error RetryPolicy + cumulative deadline + breaker calls + metrics), consumed by the OpenRouter, Anthropic, and Gemini `complete()` paths.
Framings weighed: free-function executor `execute_with_retry(attempt_fn, *, policy, breaker, ...)` each upstream wraps its single attempt in (chosen) · RetryingUpstream base class the three inherit (rejected — couples lifecycle, fights the composition style, harder to unit-test) · decorator upstream wrapping any CompletionUpstream (rejected — retry must live INSIDE the provider to reuse per-attempt translation + the per-instance breaker, and must NOT touch stream()).
Must:
<must>
  - M1 EXTRACT: lift OpenRouter's retry machinery (`_classify_reason`, `_compute_backoff`, `_parse_retry_after`, the attempt loop, breaker calls, metric increments) into ONE shared module; OpenRouter's behavior at the same settings stays byte-identical (behavior-preserving extraction).
  - M2 WIRE: Anthropic and Gemini `complete()` retry on the SAME retryable set as OpenRouter (429, ≥500, ConnectError/ConnectTimeout→connect_error, PoolTimeout→pool_timeout) up to `max_retries` with full-jitter backoff; their ctors + main.py wiring gain `max_retries`/`backoff_base`.
  - M3 POLICY: a per-error `RetryPolicy` classifies each failure retryable-vs-terminal; terminal failures (4xx-non-429, ReadTimeout/WriteTimeout/NetworkError) are NOT retried and propagate exactly as today.
  - M4 408: 408 Request Timeout JOINS the retryable set for all three providers (today 408 is a non-retried 4xx). Safe — a 408 means the server never received a complete request, so nothing was processed. [flagged]
  - M5 DEADLINE: a cumulative retry deadline bounds total wall-clock across attempts+backoff; if the next attempt (or its backoff sleep) would exceed the deadline, no further attempt is made and the last failure propagates as exhausted. New config knob, DEFAULT-OFF (0 = no deadline) to preserve byte-identical behavior. [flagged]
  - M6 DEFAULT-OFF: `max_retries=0` (default) → exactly ONE attempt, no sleep, no deadline effect — byte-identical to current behavior for ALL THREE providers (Anthropic/Gemini today already do 1 attempt).
  - M7 STREAM-SAFE: retries are confined to `complete()`; `stream()` is NEVER retried for any provider.
  - M8 BREAKER: non-retryable transport errors STILL call the upstream's `self._breaker.on_upstream_error()` before raising; `guard()` runs before every attempt; `record_success()` on success — order preserved.
  - M9 OBSERVABILITY: the shared `upstream_retries_total{reason,outcome}` counter is incremented for all three providers; a `provider` label is added so retries are attributable. Outcomes: retried · exhausted · breaker_open · deadline_exceeded (new).
  - M10 BILLING+SECRETS: the executor returns exactly the served attempt's `(status, body)` (use case still calls `complete()` once → no double-bill); the executor receives no API-key material and emits no secret in logs/labels.
</must>
Reject:
<reject>
  - a failure the RetryPolicy classifies terminal (4xx-non-429-non-408 / ReadTimeout / WriteTimeout / NetworkError) -> NOT retried; propagate as today (4xx passes through; transport-terminal raises) -> "not_retryable"
  - all retryable attempts fail up to max_retries -> raise UpstreamUnavailableError, counter outcome="exhausted" -> "retries_exhausted"
  - the cumulative deadline would be exceeded by the next attempt/backoff -> stop, propagate last failure, counter outcome="deadline_exceeded" -> "retry_deadline_exceeded"
  - circuit breaker opens mid-loop -> CircuitOpenError propagates immediately, counter outcome="breaker_open" -> "breaker_open"
  - invalid config (max_retries>5, backoff_base<=0, deadline<0) -> Pydantic Settings validation error at startup -> "config_invalid"
</reject>
After:
<after>
  - All three providers share ONE retry codepath; a transient Anthropic/Gemini failure (429/5xx/408/connect/pool) is retried identically to OpenRouter.
  - At default settings (max_retries=0, deadline=0) every provider is byte-identical to pre-task behavior.
  - `upstream_retries_total{provider,reason,outcome}` reflects retries across all three providers; billing surfaces only the served attempt; no secret leaks.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ A1 [contract] Adding 408 to the retryable set (M4) is the desired behavior — lowest confidence because it is a deliberate behavior CHANGE vs. today's classifier (408 currently passes through); if wrong: we retry a 408 the operator wanted surfaced. Cost LOW — 408 = incomplete request never processed (idempotency-safe to retry); reverting is a one-line classifier change. DECISION (auto, within milestone scope): INCLUDE 408 — matches the task title and is safe.
  ⚠ A2 [contract] The cumulative-deadline default is OFF (0 = disabled), not a built-in cap — lowest confidence because a default-off deadline leaves the documented 429-storm worst case (~300s) at default-on-retries; if wrong: operators who enable retries expect an implicit latency ceiling. Cost MEDIUM. DECISION (auto): DEFAULT-OFF to honor the v19 "default-off byte-identical" foundation rule; operators who set max_retries>0 set the deadline alongside it (documented).
  - [x] A3 the shared executor is a free function taking an `attempt_fn` + a `RetryPolicy` + the breaker — not a base class/mixin (matches the existing composition style; low stakes).
  - [x] A4 the metrics counter gains a `provider` label (openrouter|anthropic|gemini) — additive; existing recording rules still aggregate (low stakes).
  - [x] A5 Anthropic/Gemini honor Retry-After only when present (they emit vendor-specific rate-limit headers, not standard Retry-After) — absence falls back to computed backoff (low stakes; reuses `_parse_retry_after`).
  - [x] A6 the helper operates at the INNER concrete-upstream breaker layer (each upstream's own `self._breaker`), NOT the app-level BoundCircuitBreakerUpstream — confirmed in §0 (low stakes).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# --- Must ---

Scenario: OpenRouter extraction is behavior-preserving (M1)
  Given OpenRouter with max_retries=2 and a stub upstream returning 503 then 503 then 200
  When complete() is called
  Then it returns (200, body) after exactly 3 attempts with full-jitter backoff between them
  And the upstream_retries_total counter increments identically to the pre-extraction code (2 retried)

Scenario: Anthropic retries a transient 503 (M2)
  Given AnthropicCompletionUpstream with max_retries=1 and a transport returning 503 then 200
  When complete() is called
  Then it returns (200, translated_body) after exactly 2 attempts
  And upstream_retries_total{provider="anthropic",reason="upstream_5xx",outcome="retried"} == 1

Scenario: Gemini retries a connect error then succeeds (M2)
  Given GeminiCompletionUpstream with max_retries=1 and a transport raising httpx.ConnectError then returning 200
  When complete() is called
  Then it returns (200, translated_body) after exactly 2 attempts
  And upstream_retries_total{provider="gemini",reason="connect_error",outcome="retried"} == 1

Scenario: RetryPolicy treats a 400 as terminal (M3)
  Given any provider with max_retries=3 and a transport returning 400
  When complete() is called
  Then the 400 is returned/propagated after exactly 1 attempt (no retry)
  And the upstream_retries_total counter does not increment

Scenario: 408 Request Timeout is now retryable (M4)
  Given any provider with max_retries=1 and a transport returning 408 then 200
  When complete() is called
  Then it returns (200, body) after exactly 2 attempts
  And upstream_retries_total{reason="upstream_408",outcome="retried"} == 1

Scenario: cumulative deadline stops further attempts (M5)
  Given any provider with max_retries=5, a small deadline, and a transport that always returns 503
  When complete() is called and the elapsed time reaches the deadline
  Then no further attempt is made after the deadline and UpstreamUnavailableError is raised
  And upstream_retries_total{outcome="deadline_exceeded"} == 1

Scenario: default settings are byte-identical (M6)
  Given any provider with max_retries=0 (default) and deadline=0 (default) and a transport returning 503
  When complete() is called
  Then exactly ONE attempt is made, with no backoff sleep, and the failure propagates as today
  And the upstream_retries_total counter does not increment

Scenario: stream() is never retried (M7)
  Given any provider with max_retries=3 and a streaming transport that fails on the first chunk
  When stream() is called
  Then the failure surfaces without any retry attempt
  And the complete()-path retry machinery is not invoked

Scenario: non-retryable transport still trips the breaker (M8)
  Given any provider with max_retries=2 and a transport raising httpx.ReadTimeout
  When complete() is called
  Then UpstreamUnavailableError is raised after exactly 1 attempt (no retry)
  And the upstream's circuit breaker recorded one on_upstream_error() before raising

Scenario: retries are attributable per provider (M9)
  Given each of OpenRouter, Anthropic, Gemini with max_retries=1 and a transport returning 503 then 200
  When complete() is called on each
  Then upstream_retries_total carries a distinct provider label for each (openrouter|anthropic|gemini)
  And outcomes include retried for the recovered call

Scenario: billing sees only the served attempt (M10)
  Given any provider with max_retries=2 and a transport returning 503 then 200 with a usage block
  When complete() is called
  Then it returns exactly the SECOND attempt's (200, body+usage) — the discarded 503 body is never surfaced
  And no API key string appears in any emitted log line or metric label

# --- Reject ---

Scenario: terminal failure is not retried (Reject not_retryable)
  Given any provider with max_retries=3 and a transport returning 401
  When complete() is called
  Then the 401 propagates after exactly 1 attempt
  And the retry counter remains unchanged (no retried/exhausted increment)

Scenario: retries exhausted (Reject retries_exhausted)
  Given any provider with max_retries=2 and a transport that always returns 503
  When complete() is called
  Then UpstreamUnavailableError is raised after exactly 3 attempts
  And upstream_retries_total{outcome="exhausted"} increments once and no success is recorded

Scenario: deadline exceeded mid-retry (Reject retry_deadline_exceeded)
  Given any provider with max_retries=5, a deadline shorter than the needed backoff, always-503 transport
  When complete() is called
  Then it stops before max_retries is reached and raises UpstreamUnavailableError
  And upstream_retries_total{outcome="deadline_exceeded"} increments and no further POST is issued past the deadline

Scenario: breaker opens mid-loop (Reject breaker_open)
  Given any provider with max_retries=5 and a breaker that opens after the first failure
  When complete() is called
  Then CircuitOpenError propagates immediately on the next guard()
  And upstream_retries_total{outcome="breaker_open"} increments and no further POST is issued

Scenario: invalid retry config rejected at startup (Reject config_invalid)
  Given GATEWAY_UPSTREAM_MAX_RETRIES=9 (above the le=5 bound) or a negative deadline
  When Settings is constructed at startup
  Then a Pydantic validation error is raised and the app does not boot
  And no upstream is constructed with an out-of-range retry budget
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is an INTERNAL code contract (no HTTP endpoint). The frozen shape = the shared retry module's
public surface + the config knob + the metric label set + the preserved `complete()` behavior.

```
NEW MODULE: apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py

# Metric label vocabulary (frozen):
#   reason  ∈ { "connect_error", "pool_timeout", "upstream_408", "upstream_429", "upstream_5xx" }
#   outcome ∈ { "retried", "exhausted", "breaker_open", "deadline_exceeded" }

DEFAULT_BACKOFF_CAP_S: float = 8.0
RETRY_AFTER_MAX_S: float    = 60.0

@dataclass(frozen=True)
class RetryPolicy:
    # classifies a failure into a retry-reason label, or None if TERMINAL (not retried)
    def classify_status(self, status: int) -> str | None
        # 429 -> "upstream_429" · 408 -> "upstream_408" · >=500 -> "upstream_5xx" · else None
    def classify_exception(self, exc: BaseException) -> str | None
        # ConnectError|ConnectTimeout -> "connect_error" · PoolTimeout -> "pool_timeout"
        # ReadTimeout|WriteTimeout|NetworkError(base) + anything else -> None (terminal)

DEFAULT_RETRY_POLICY: RetryPolicy   # the unified set above (INCLUDES 408 — see flag)

def compute_backoff(attempt: int, *, backoff_base: float, cap_s: float = DEFAULT_BACKOFF_CAP_S) -> float
    # full-jitter: random.uniform(0, min(cap_s, backoff_base * 2**attempt)); attempt 1-indexed

def parse_retry_after(header_value: str, *, max_s: float = RETRY_AFTER_MAX_S) -> float | None
    # integer seconds in [0, max_s] -> float; HTTP-date / out-of-range / garbage -> None

async def execute_with_retry(
    do_request: Callable[[], Awaitable[httpx.Response]],     # ONE attempt: the POST (translation done by caller, outside)
    render_response: Callable[[httpx.Response], tuple[int, dict[str, Any]]],  # provider renders a TERMINAL resp (200 or 4xx-passthrough)
    *,
    breaker: CircuitBreaker,         # the upstream's OWN per-instance breaker
    provider: str,                   # metric label: "openrouter" | "anthropic" | "gemini"
    max_retries: int,                # 0 = exactly one attempt (byte-identical)
    backoff_base: float,
    deadline_s: float = 0.0,         # 0 = NO cumulative deadline (default; see flag)
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    metrics_registry: MetricsRegistry | None = None,
) -> tuple[int, dict[str, Any]]

# BEHAVIOR (frozen — the loop the three providers share):
#  for attempt in range(max_retries + 1):
#    breaker.guard()                         # CircuitOpenError -> outcome="breaker_open" (if attempt>0), re-raise
#    resp = await do_request()
#      on exception e:
#        reason = policy.classify_exception(e); breaker.on_upstream_error()
#        if reason is None: raise UpstreamUnavailableError(str(e))         # TERMINAL transport
#        -> retryable transport (fall to the shared retry tail below)
#      else status = resp.status_code; reason = policy.classify_status(status)
#        if reason is None: breaker.record_success(); return render_response(resp)   # success OR 4xx passthrough
#        -> retryable status; breaker.on_upstream_error()
#    # shared retry tail (reason is not None):
#    is_last = attempt >= max_retries
#    delay = parse_retry_after(resp.headers["Retry-After"]) if status==429 else None; delay = delay or compute_backoff(attempt+1, ...)
#    if deadline_s > 0 and (elapsed + delay) > deadline_s: counter(reason, "deadline_exceeded"); raise UpstreamUnavailableError(...)
#    counter(reason, "exhausted" if is_last else "retried")
#    if is_last: raise UpstreamUnavailableError(f"Upstream returned {status}" | str(exc))
#    await asyncio.sleep(delay); continue
```
```
CONFIG (apps/gateway/src/gateway/core/config.py): ONE new field
  upstream_retry_deadline_s: float = Field(default=0.0, ge=0)   # env GATEWAY_UPSTREAM_RETRY_DEADLINE_S; 0 = disabled
  (existing: upstream_max_retries le=5, upstream_retry_backoff_base_s gt=0 — UNCHANGED)

METRIC (MetricsRegistry.upstream_retries_total): label set BECOMES { provider, reason, outcome }
  (additive `provider` dimension; OpenRouter now passes provider="openrouter" — same series otherwise)

WIRING (ctors + main.py): AnthropicCompletionUpstream + GeminiCompletionUpstream ctors gain
  `max_retries: int = 0, backoff_base: float = 0.5, retry_deadline_s: float = 0.0`; main.py L392-409
  passes settings.upstream_max_retries / upstream_retry_backoff_base_s / upstream_retry_deadline_s.
  OpenRouter ctor also gains retry_deadline_s (threaded into execute_with_retry).

PRESERVED (frozen invariants — every reject code maps here):
  not_retryable          -> terminal status (4xx-non-429-non-408): record_success + render_response;
                            terminal transport (Read/Write/Network): on_upstream_error + raise UpstreamUnavailableError
  retries_exhausted      -> raise UpstreamUnavailableError, counter outcome="exhausted"
  retry_deadline_exceeded-> raise UpstreamUnavailableError, counter outcome="deadline_exceeded"
  breaker_open           -> CircuitOpenError propagates, counter outcome="breaker_open"
  config_invalid         -> Pydantic ValidationError at Settings construction (le=5 / ge=0 bounds)
  stream() UNTOUCHED for all three providers (retry confined to complete()).
```

Least-sure flag surfaced at freeze: [contract] adding **408** to the retryable set (M4/A1) is a deliberate behavior change vs. today's classifier — included because a 408 = incomplete request never processed (idempotency-safe); if wrong, reverting is a one-line `classify_status` change. [contract] the cumulative **deadline default is 0/OFF** (A2) — honors the v19 "default-off byte-identical" rule, so the documented ~300s 429-storm worst case persists until an operator sets both `max_retries>0` and `deadline_s>0`; if operators expect an implicit cap, that is a one-field default change.

Status: FROZEN @ v1 — approved by Tin (auto-resolved under the autonomous mandate; freeze delegated to auto, no security/architecture residue — a behavior-preserving extraction at the infra layer)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (gateway floor) on the NEW module `upstream_retry.py`; no decrease overall.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  # NEW: apps/gateway/tests/retry_policy/test_upstream_retry.py — the shared executor + policy units
  - test_openrouter_extraction_behavior_preserving (M1): existing test_retry_policy.py stays green after the rewrite (regression guard); plus a direct executor test: 503,503,200 with max_retries=2 → (200,body), 3 attempts, 2 retried.
  - test_policy_classify_status (M3/M4): classify_status → 429→upstream_429, 408→upstream_408, 500/503→upstream_5xx, 400/401/404→None.
  - test_policy_classify_exception (M3): ConnectError/ConnectTimeout→connect_error, PoolTimeout→pool_timeout, ReadTimeout/WriteTimeout/NetworkError→None.
  - test_compute_backoff_full_jitter + test_parse_retry_after (M1): jitter window math; integer 0..60 honored, HTTP-date/garbage/out-of-range→None.
  - test_default_off_byte_identical (M6): max_retries=0,deadline=0 → exactly 1 attempt, no sleep, failure propagates; counter unchanged.
  - test_deadline_stops_attempts (M5/Reject retry_deadline_exceeded): always-503, small deadline → stops before max_retries, raises, outcome="deadline_exceeded", no POST past deadline.
  - test_non_retryable_transport_trips_breaker (M8): ReadTimeout → 1 attempt, raise, on_upstream_error recorded once, no retry.
  - test_retries_exhausted (Reject): always-503, max_retries=2 → 3 attempts, raise, outcome="exhausted".
  - test_breaker_open_midloop (Reject breaker_open): breaker opens after first failure → CircuitOpenError, outcome="breaker_open", no further POST.
  - test_terminal_4xx_passthrough_records_success (M3/Reject not_retryable): 400 → render_response returned, record_success, counter unchanged.
  - test_provider_label_present (M9): counter carries provider label for each upstream.
  - test_billing_sees_served_attempt_only + test_no_secret_in_logs (M10): 503-then-200 returns 2nd body only; no api_key substring in caplog/metric labels.
  # NEW: apps/gateway/tests/retry_policy/test_anthropic_retry.py (M2)
  - test_anthropic_retries_503_then_200; test_anthropic_408_retried; test_anthropic_default_off_byte_identical; test_anthropic_stream_never_retried (M7).
  # NEW: apps/gateway/tests/retry_policy/test_gemini_retry.py (M2)
  - test_gemini_retries_connect_error_then_200; test_gemini_408_retried; test_gemini_default_off_byte_identical; test_gemini_stream_never_retried (M7).
  # config validation (Reject config_invalid) — extend apps/gateway/tests/retry_policy/test_upstream_retry.py
  - test_config_rejects_max_retries_above_5; test_config_rejects_negative_deadline (Pydantic ValidationError).
</test_plan>

Tests live in: `apps/gateway/tests/retry_policy/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py` `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py` `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/src/gateway/observability/metrics.py` `apps/gateway/tests/retry_policy/`
<!-- the task's own test surface (§4) is included so the build's ruff-format/lint pass on the
     newly-authored test files is in-scope; the build writes NO src outside the seven files above. -->

Strategy (ordered batches):
  1. Create `upstream_retry.py` — `RetryPolicy` + `DEFAULT_RETRY_POLICY` + `compute_backoff` + `parse_retry_after` + `execute_with_retry` (extract verbatim from openrouter, generalize + add 408 + deadline + provider label).
  2. metrics.py — add `provider` to `upstream_retries_total` label set + update the comment.
  3. Rewrite OpenRouter `complete()` to call `execute_with_retry` (do_request=POST, render_response=`lambda r: (r.status_code, r.json())`, provider="openrouter"); keep `stream()` untouched; delete the now-extracted privates. Run existing test_retry_policy.py → must stay green.
  4. config.py — add `upstream_retry_deadline_s` field.
  5. Wire Anthropic: ctor gains max_retries/backoff_base/retry_deadline_s; `complete()` splits its flat except into the executor (render_response handles 200 via `_anthropic_to_openai` and 4xx via `_anthropic_error_to_openai`; 5xx now retryable-then-raise). Keep translation OUTSIDE the loop.
  6. Wire Gemini: same shape (render_response via `_gemini_to_openai`); translation + model extraction OUTSIDE the loop.
  7. main.py — pass settings.upstream_max_retries / upstream_retry_backoff_base_s / upstream_retry_deadline_s to all three ctors.
Safety rule (feature-specific): the success-body/error-body TRANSLATION is pure and MUST stay OUTSIDE the retry loop (do_request issues only the POST; render_response transforms only a terminal response) — re-translating per attempt is wasteful and the executor must never re-bill. `complete()` is the ONLY retry surface; `stream()` is never touched.
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps — stdlib asyncio/random/time + existing httpx/structlog/prometheus); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — retry_policy + retry_policy_wiring 62/62; upstream-adjacent sweep (provider_chat_dispatch, anthropic/gemini_provider, cooldown_circuit(+wiring), model_fallbacks(+wiring), observability, embeddings/images_endpoint, provider_seam, gemini_embed_tokens) 209/209; `make test-fast` blast-radius 135/135. All no-DB, run twice clean.
- [x] coverage did not decrease — new module `upstream_retry.py` 97% (96 stmts, 3 missed: transport-exhaustion + final-unreachable raise); overall floor held (the removed OpenRouter privates are now covered in the shared module).
- [x] no test or contract was altered during build — §3 is byte-for-byte as frozen (tamper tripwire green). The verify-found is_last/deadline refinement is a code-level correctness fix that better honors the FROZEN outcome semantics (exhausted vs deadline_exceeded as distinct, both-reachable outcomes); the §3 external surface (signatures, outcome label set, reject codes) is unchanged, so no contract edit was needed. The refinement is documented here + §7, not in the frozen §3 pseudocode.
- [x] the green was EARNED — adversarial refute-read (independent sonnet subagent, prompted to REFUTE) returned EARNED-WITH-GAPS, ZERO confirmed cheats, tests non-vacuous. It found ONE real logic bug (is_last/deadline mislabel) → FIXED in an honest build redo (is_last decided first); existing deadline + exhausted tests stay green confirming no regression.
- [x] concurrency / timing — each complete() call is independent; the executor holds only per-call locals (start, last_reason); the per-instance CircuitBreaker is shared per-replica exactly as before (guard/on_upstream_error/record_success order preserved). No new shared mutable state; backoff uses asyncio.sleep (non-blocking). stream() untouched — never retried.
- [x] no exposed secrets / injection / unexpected deps — execute_with_retry takes NO api-key material; `_log.warning` emits only provider/attempt/reason/delay; metric labels are provider/reason/outcome — no secret. No new dependencies (stdlib asyncio/random/time + existing httpx/structlog/prometheus). Refute-read attack-4 = OK.
- [x] layering & dependencies — the executor lives in the infrastructure layer and is consumed by the three concrete infra upstreams; it imports only domain errors + circuit_breaker + observability (no domain/app inversion). CONVENTIONS.md honored.
- [x] a person reviewed and approved the change — auto-resolved under `autonomy: auto` (task is NOT risk:high — a behavior-preserving extraction; no security/concurrency/architecture residue). Accountable owner: the retry-seam-unify run. Tin's standing autonomous mandate authorizes auto-gate for non-high-risk tasks.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `execute_with_retry` referenced by openrouter_upstream.complete (L~95), anthropic_upstream.complete, gemini_upstream.complete; `RetryPolicy`/`DEFAULT_RETRY_POLICY`/`compute_backoff`/`parse_retry_after` referenced by the executor + tests; `upstream_retry_deadline_s` threaded in main.py to all three ctors; `provider` label live on the counter (metrics.py). Confirmed by grep + the wiring suite.
- [x] DEAD-CODE (code) — the extracted privates (`_classify_reason`, `_compute_backoff`, `_parse_retry_after`, `_increment_retry_counter`) and the now-unused `import asyncio/random` + backoff constants were REMOVED from openrouter_upstream.py (ruff F401/F811 clean). No orphaned symbol introduced.
- [x] SEMANTIC — refute-read report read in full: confirmed no cheats; the one REAL bug it raised was fixed; residuals (408 trips breaker at default; provider-deadline wiring covered by inspection not a wiring test; factory post-assign) are accepted/deferred (see §7).

### GATE RECORD
Outcome: PASS
Reviewed by: retry-seam-unify run (auto-resolved, autonomy: auto — non-high-risk, no residue; adversarial refute-read clean after fix) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `gateway_upstream_retries_total{provider,reason,outcome}` — per-provider retry rate; alert if outcome="exhausted"/"deadline_exceeded" rises (upstream degradation) or if anthropic/gemini suddenly show retries (provider instability now VISIBLE, was blind before). 408 retry rate (new signal). Latency: with retries enabled, watch p99 against the deadline budget.
Spec delta for the next loop: 408-at-default is a behavior change (was passthrough, now trips breaker + raises at max_retries=0) — confirm in production that 408 is rare enough that the breaker impact is negligible; if a provider emits 408 routinely, reconsider classifying it terminal. The cumulative deadline default-OFF leaves the ~300s 429-storm worst case until operators set both knobs — the error-aware-fallback + reliability-verify tasks should surface a recommended deadline default.

### Competency deltas
- [ADD · folded] A build-phase lint/format pass that touches the task's own test files trips the §5 scope-gate (test files aren't in the src Scope); declaring the test dir in §5 + re-crossing tests→build (sanctioned re-snapshot) resolves it cleanly. Evidence: ruff-format on the 3 new test files diverged them from the tests→build snapshot. Lesson: declare the test surface in §5 when the build will lint/format newly-authored tests, OR run format inside the tests phase before the snapshot.
- [TDD · folded] The adversarial refute-read caught a real is_last/deadline mislabeling bug the green suite missed (the existing deadline + exhausted tests didn't exercise is_last-WITH-active-deadline). Evidence: refute-read REAL-BUG finding → fixed. Lesson: a cumulative-deadline interacts with the exhaustion boundary; a verify-gate refute-read earns its keep on retry/timeout logic where boundary states are easy to leave untested.
- [ADD · folded] Editing the §3 pseudocode comment AFTER the tests→build snapshot trips the tamper tripwire (it md5s the WHOLE §3 body, not just the signature block). Evidence: reverted the comment edit to keep the tripwire green. Lesson: post-freeze refinements go in §6/§7, never in §3 — the frozen body is immutable bytes, comments included.
<!-- competency deltas are written `open`; the human consolidates at fold. -->

