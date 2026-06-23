# TASK: Graceful back-pressure under concurrent agent-coding load

slug: concurrency-load-guard · created: 2026-06-23 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

All paths under `apps/gateway/`. GOAL: under sustained CONCURRENT agent-coding load (many simultaneous streaming completions from Helios), the gateway must shed load GRACEFULLY (503 + Retry-After) instead of accepting unbounded concurrency until it collapses (FD/connection/memory exhaustion). Explore-verified finding: **NO global concurrency/back-pressure guard exists — gateway-level concurrency is UNBOUNDED.**

What EXISTS (and why it's NOT this):
- `RedisDeploymentLoadGate` (proxy/infrastructure/redis_load_gate.py) — per-deployment in-flight counter + latency EWMA, used ONLY by FallbackModelRouter least-busy/latency ROUTING to rank candidates. Never rejects; not on the stream path; only active for non-ordered strategies.
- Per-tenant/key rate limits `RedisLuaRateLimiter` (RPM/TPM → 429) + per-deployment `RedisDeploymentLimitGate` (opt-in rpm/tpm) + per-model `RedisCooldownGate` — all per-key/per-deployment THROUGHPUT/health, NOT global concurrency. A burst of N concurrent streams from un-limited keys is fully admitted.
- `CircuitBreaker` (proxy/infrastructure/circuit_breaker.py) — single global breaker, trips after 5 CONSECUTIVE upstream FAILURES; sheds on failure, not on overload.
- Each in-flight stream holds an ASGI conn + a NEW per-request httpx client (Anthropic/OpenRouter open `async with httpx.AsyncClient()` per stream — anthropic_upstream.py:925 / openrouter_upstream.py:273 — bypassing pool limits) + a SQLAlchemy session for the whole generation. So concurrency pressure is real and unbounded.

Touches (files · symbols · signatures):
- NEW `src/gateway/proxy/api/concurrency_guard.py` (or middleware module) — a global back-pressure ASGI middleware: acquire a slot before delegating, hold it across the WHOLE `await app(scope, receive, send)` (so a stream holds its slot until the generator is exhausted), release in finally; on saturation send 503 + Retry-After WITHOUT calling the app. Backed by an `asyncio.BoundedSemaphore` (per-worker) created at app startup.
- `src/gateway/main.py:~801` `create_app()` — register the middleware BEFORE `RequestIdMiddleware` (so it wraps every route: chat/embeddings/images/audio); wire the cap from Settings; store on app.state for tests/metrics.
- `src/gateway/core/config.py` — NEW knob `max_concurrent_requests: int = Field(default=0)` (env GATEWAY_MAX_CONCURRENT_REQUESTS; 0 = disabled = today's unbounded behavior) + `back_pressure_retry_after_seconds: int = Field(default=1)`.
- error shape mirrors existing 429/503: reuse the project's error-body convention (e.g. `{error: {...code...}}`) — match how `ERR_RATE_LIMITED` 429 + Retry-After is emitted (rate_limits) and how 503/502 is produced today.

Context (working folder):
- Existing middleware stack: ONLY `RequestIdMiddleware` (main.py:~801). App factory `create_app()` (main.py:241) is the single composition root.
- Existing concurrency tests: tests/rate_limits/test_rate_limits.py `test_concurrent_burst_admits_exactly_limit` (10 concurrent vs rpm=5, Lua atomicity) is the closest pattern to mirror for a load test. tests/cooldown_circuit uses asyncio.gather for concurrency. No global-overload/back-pressure/503 test exists.
- v34 harness can drive concurrent streams; a load test fires N concurrent requests against cap=K and asserts exactly K admitted + (N-K) get 503+Retry-After + in-flight returns to 0 after (semaphore released).

Honors (patterns / conventions):
- Design-for-failure: the guard itself must never deadlock/leak a slot — acquire/release symmetric in try/finally across ALL exit edges (success, error, client disconnect/GeneratorExit); a streaming response holds its slot until fully drained.
- Fail-safe default: knob default 0 = disabled (no behavior change unless an operator opts in) — matches the codebase's opt-in guard convention (cooldown/deployment-limit default off).
- Graceful shed: 503 + Retry-After (a retryable signal), never a hard 500; consistent error body with the existing 429 path.
- In-process (per-worker) semaphore — no new external dependency on the hot path (cf. Redis fail-open complexity); total cap = workers × per-worker cap (documented).

Anchors the contract cites: NEW back-pressure middleware (acquire/hold-across-app/release + 503+Retry-After) · `asyncio.BoundedSemaphore` · `create_app()` middleware registration order · Settings `max_concurrent_requests`/`back_pressure_retry_after_seconds` · existing 429+Retry-After error shape · v34 concurrent-load test pattern.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: global concurrency back-pressure — a per-worker admission guard that caps simultaneous in-flight requests and sheds excess load with 503 + Retry-After, so sustained concurrent Helios coding streams degrade gracefully instead of exhausting connections/FDs/memory. Opt-in (default disabled = today's unbounded behavior).

Framings weighed: in-process asyncio.BoundedSemaphore ASGI middleware (chosen — no hot-path external dependency, symmetric acquire/release across the full request incl. streaming, simple + robust; total cap = workers × per-worker cap) · Redis global cross-worker counter (rejected for now — accurate global cap but adds a per-request INCR/DECR on the hot path + fail-open complexity; seeded as a follow-up if a precise cluster-wide cap is needed) · per-route FastAPI dependency (rejected — misses non-chat routes + runs after auth/session setup).

Must:
<must>
  - ADMISSION CAP: when `GATEWAY_MAX_CONCURRENT_REQUESTS > 0`, the middleware admits at most that many simultaneous in-flight requests per worker; the slot is held for the ENTIRE request (for a streaming response, until the SSE generator is fully drained) and released on every exit edge.
  - SHED ON SATURATION: a request that cannot get a slot immediately is rejected with HTTP 503 + a `Retry-After: <back_pressure_retry_after_seconds>` header and the project's standard error body (a retryable signal), WITHOUT invoking the downstream app.
  - DISABLED DEFAULT (byte-identical): when the knob is 0 (default), the middleware is a pass-through — no slot accounting, no behavior change vs today.
  - SLOT SAFETY: acquire/release are symmetric in try/finally; a slot is NEVER leaked on success, error, client disconnect (GeneratorExit/CancelledError), or downstream exception. After a burst settles, in-flight returns to 0.
  - CROSS-ROUTE: the guard wraps ALL routes (chat/embeddings/images/audio), registered before RequestIdMiddleware.
  - OBSERVABLE: the current in-flight count + the cap are inspectable (app.state / metric) so the load test and ops can see saturation.
</must>
Reject:
<reject>
  - request arrives while all slots are taken (saturation) -> HTTP 503 + Retry-After, error code "ERR_OVERLOADED" (or the existing service-unavailable code) — graceful shed, never a 500, app NOT invoked
  - knob set to a negative/invalid value -> treated as 0 (disabled) + WARN at startup; never crash boot
</reject>
After:
<after>
  - Under a burst of N concurrent streams with cap=K, exactly K are admitted and run; the remaining N-K get 503+Retry-After; once the K complete, in-flight returns to 0 and new requests are admitted again. With the knob off, behavior is identical to today.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] an ASGI middleware that wraps `await self.app(scope, receive, send)` holds its semaphore slot for the FULL duration of a streaming response (the app coroutine returns only after the SSE generator is exhausted), so releasing in finally after that await correctly bounds CONCURRENT streams. Lowest confidence because if Starlette returns the app coroutine BEFORE the StreamingResponse body is fully sent (it does not, but must be verified), the slot would release early and the cap would bound request-starts not concurrent streams. If wrong: hold the slot via a wrapped `send` that releases on the final response message — isolated to the middleware. Verified by a load test asserting a slow stream keeps in-flight elevated until it completes.
  - [ ] [contract] in-process per-worker semaphore is acceptable (total cap = workers × cap); a precise cluster-wide cap would need Redis — confirmed acceptable for back-pressure (each worker self-protects).
  - [ ] [contract] default 0 = disabled preserves current behavior (opt-in) — matches the cooldown/deployment-limit opt-in convention.
  - [ ] [scenario] 503 + Retry-After is the right shed signal (retryable) vs 429 — 503 chosen because the cap is gateway capacity, not a per-tenant quota.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: burst beyond the cap sheds the excess
  Given GATEWAY_MAX_CONCURRENT_REQUESTS=K and K slow in-flight streams already running
  When request K+1 arrives
  Then it gets HTTP 503 + Retry-After and the downstream app is NOT invoked

Scenario: exactly cap admitted under a concurrent burst
  Given the knob = K and N>K requests fired concurrently
  When they hit the guard
  Then exactly K are admitted and run, N-K get 503 + Retry-After

Scenario: slot held for the whole stream then released
  Given a streaming response under the cap
  When the stream is in progress
  Then in-flight stays elevated until the SSE generator is fully drained, then returns to 0

Scenario: slot released on client disconnect
  Given an admitted streaming request that the client disconnects mid-stream
  When GeneratorExit/CancelledError propagates
  Then the slot is released (in-flight decremented) — no leak

Scenario: slot released on downstream error
  Given an admitted request whose handler raises
  When the exception propagates through the middleware
  Then the slot is released and the error response still flows

Scenario: disabled knob is byte-identical
  Given GATEWAY_MAX_CONCURRENT_REQUESTS=0 (default)
  When any number of requests arrive
  Then the middleware is a pass-through (no accounting, no 503) — behavior identical to today

Scenario: guard covers non-chat routes
  Given the cap is reached
  When a request to /v1/embeddings (or images/audio) arrives
  Then it is also shed with 503 + Retry-After

Scenario: REJECT an invalid knob value
  Given GATEWAY_MAX_CONCURRENT_REQUESTS set to a negative value
  When the app boots
  Then it is treated as 0 (disabled) + a startup WARN, and boot succeeds
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new HTTP route; a global ASGI middleware on ALL routes + 2 Settings knobs. No schema.

NEW middleware (proxy/api/concurrency_guard.py): GlobalBackPressureMiddleware(app, *, max_concurrent:int, retry_after_s:int)
  __init__: self._sem = asyncio.BoundedSemaphore(max_concurrent) if max_concurrent > 0 else None
            self._in_flight = 0 (observable)  ; self._cap = max_concurrent
  async def __call__(scope, receive, send):
      if scope["type"] != "http" or self._sem is None:       # pass-through when disabled / non-http
          return await self.app(scope, receive, send)
      if self._sem.locked() or not _try_acquire_nowait(self._sem):   # no slot immediately
          return await _send_503(send, retry_after_s)         # 503 + Retry-After, app NOT invoked
      self._in_flight += 1
      try:
          await self.app(scope, receive, send)                # held for the WHOLE request incl. stream drain
      finally:
          self._in_flight -= 1
          self._sem.release()
  _send_503: status 503, header Retry-After: <retry_after_s>, body = project standard error
             ({"error": {"message": "...", "type": "...", "code": "ERR_OVERLOADED"}} — match existing shape).

acquire semantics: non-blocking — if no slot is free RIGHT NOW, shed (do not queue/wait). Use a non-blocking
  acquire (e.g. sem.acquire with a 0 timeout via asyncio.wait_for, or check sem._value>0 guarded) — chosen so
  saturation sheds immediately rather than building an unbounded waiter queue.

main.py create_app(): app.add_middleware(GlobalBackPressureMiddleware,
                          max_concurrent=settings.max_concurrent_requests,
                          retry_after_s=settings.back_pressure_retry_after_seconds)
  registered BEFORE RequestIdMiddleware so it wraps every route. Expose the instance on app.state for tests/metrics.

config.py Settings:
  max_concurrent_requests: int = Field(default=0)            # env GATEWAY_MAX_CONCURRENT_REQUESTS; 0 = disabled
  back_pressure_retry_after_seconds: int = Field(default=1)  # env GATEWAY_BACK_PRESSURE_RETRY_AFTER_SECONDS
  negative max_concurrent → coerced to 0 + WARN (validator).

Behavior: knob 0 → pass-through (byte-identical). knob K → ≤K concurrent per worker; overflow → 503+Retry-After.
Slot held across the full app call (StreamingResponse drains before the app coroutine returns) → bounds CONCURRENT
streams; released in finally on success/error/disconnect. Schema: none.
```

Least-sure flag surfaced at freeze: [contract] does `await self.app(scope, receive, send)` return only AFTER a StreamingResponse body is fully sent? If Starlette returns early, the slot releases before the stream ends and the cap bounds request-STARTS not concurrent STREAMS. Mitigated by a load test asserting a slow in-flight stream keeps in-flight elevated until it completes; if early-return is observed, hold the slot via a wrapped `send` that releases on the final `http.response.body` (more_body=False) message — isolated to the middleware. Runner-up [scenario]: 503 vs 429 for the shed signal — 503 chosen (gateway capacity, not a per-tenant quota), and acquire is non-blocking (shed-now, no waiter queue).

Status: FROZEN @ v1 — approved by Tin (2026-06-23: in-process semaphore default-OFF + 503+Retry-After)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% of the new middleware branches.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_burst_beyond_cap_sheds_excess: cap=K, K slow streams held → request K+1 → 503 + Retry-After, downstream app NOT called (spy)
  - test_exactly_cap_admitted_concurrent: N concurrent vs cap=K → exactly K admitted, N-K get 503 (mirror tests/rate_limits concurrent-burst pattern with asyncio.gather)
  - test_slot_held_for_whole_stream: a slow SSE stream keeps in_flight elevated until the generator drains, then 0 (the least-sure-flag canary)
  - test_slot_released_on_disconnect: admitted stream disconnected mid-way (GeneratorExit) → in_flight decremented, no leak
  - test_slot_released_on_downstream_error: handler raises → slot released, error response still flows
  - test_disabled_knob_passthrough: knob=0 → no 503 ever, no accounting (in_flight stays 0), behavior == today
  - test_guard_covers_nonchat_route: cap reached → a non-chat route also sheds 503
  - test_reject_invalid_knob_coerced_to_zero: negative knob → treated as 0 + startup WARN, boot succeeds
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/api/concurrency_guard.py` `apps/gateway/src/gateway/main.py` `apps/gateway/src/gateway/core/config.py`
  — NEW concurrency_guard.py (GlobalBackPressureMiddleware); main.py create_app registers it before RequestIdMiddleware + exposes it on app.state; config.py two knobs + negative→0 validator. No schema, no new deps (stdlib asyncio).
Strategy (ordered batches): 1. config knobs + negative-coerce validator. 2. concurrency_guard.py middleware (BoundedSemaphore, non-blocking acquire, 503+Retry-After, hold-across-app, finally release, in_flight counter, pass-through when disabled/non-http). 3. main.py register before RequestIdMiddleware + app.state. 4. green the §4 suite incl. the slow-stream slot-hold canary.
Safety rule (feature-specific): acquire/release symmetric in try/finally — never leak a slot on success/error/disconnect; non-blocking acquire (shed-now, no unbounded waiter queue); disabled knob = pure pass-through (byte-identical); 503 (not 500), retryable, app not invoked on shed.
Code lives in: `apps/gateway/src/gateway/proxy/api/` (+ main.py wiring, core/config.py knobs)
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full gate `uv run pytest -m 'not e2e' --cov-fail-under=80` → 1495 passed, 19 deselected
- [x] coverage did not decrease — 87.40% (≥80%)
- [x] no test or contract was altered to pass — only NEW tests added; frozen §3 untouched; retry_after ge-coercion is a robustness add within the contracted knob (no §3 edit)
- [x] the green was EARNED, not gamed — adversarial refute-read (backend-expert) = EARNED-GREEN @ 0.93, CPython-level acquire-race + slot-leak analysis watertight; 5 non-blocking findings (retry_after coercion + 2 test tightenings + 2 comment fixes) all closed
- [x] concurrency / timing — non-blocking acquire via `locked()`+synchronous `acquire()` in one event-loop turn (no interleave, no oversubscription); slot held across full stream drain; symmetric try/finally release on success/error/disconnect; refute-read confirmed no leak/deadlock
- [x] no exposed secrets, injection openings, or unexpected dependencies — stdlib asyncio only
- [x] layering & dependencies follow CONVENTIONS.md — middleware in proxy/api; outermost user middleware (before RequestIdMiddleware/auth/routing); knobs in core/config
- [x] a person reviewed and approved the change — Tin chose scope (in-process semaphore default-OFF + 503) + froze the contract (2026-06-23); auto-gate on complete evidence

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] cap=K + K slow streams held → request K+1 sheds 503+Retry-After, app NOT invoked — spy test confirms downstream_calls==[]
- [x] N concurrent vs cap=K → exactly 1≤admitted≤K, rest 503 — asyncio.Barrier burst test
- [x] slot held for the WHOLE stream (the least-sure flag, RESOLVED) → in_flight stays elevated until the SSE drains then 0 — canary samples in_flight DURING the stream; Starlette StreamingResponse.__call__ drains before returning
- [x] disabled knob (default 0) byte-identical → whole 1495-test suite green with the middleware as pass-through (_sem is None)
- [x] slot never leaked on disconnect/error → GeneratorExit + handler-raise tests show in_flight→0

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — GlobalBackPressureMiddleware registered as outermost user middleware in create_app; wired from settings; exposed on app.state.back_pressure; both knobs read from Settings — refute-read traced the stack order
- [x] DEAD-CODE (code) — none; middleware + both validators referenced
- [x] SEMANTIC (n/a — code task; two misleading comments corrected per refute-read #2/#3)

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (scope + contract freeze 2026-06-23) + adversarial refute-read (backend-expert, EARNED-GREEN 0.93, 5 findings→strengthened) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 503 ERR_OVERLOADED shed rate · in_flight / cap saturation ratio per worker · stream-hold duration (slot residency) · post-burst in_flight returns to 0 (leak canary)

### Spec delta
- [SPEC · seeded] Redis cross-worker global concurrency cap (precise cluster-wide ceiling) — this task ships a per-worker in-process cap (total = workers × cap); add the Redis variant if a hard cluster-wide limit is needed (evidence: §1 rejected framing)
- [SPEC · seeded] configure httpx.Limits on the per-stream clients — Anthropic/OpenRouter open a NEW httpx.AsyncClient per stream (anthropic_upstream.py:925 / openrouter_upstream.py:273), bypassing the shared pool's max_connections; the back-pressure cap bounds this indirectly but a pool limit is the direct fix (evidence: §0 grounding)
- [SPEC · open] exercise the back-pressure guard under real concurrent load in the helios-live-smoke (task 7) — the in-process test proves the mechanism; live confirms shed behavior under the real Envoy/uvicorn worker model

### Competency deltas
- [TDD · open] a slot-hold canary that samples in_flight DURING a slow stream is the decisive test for an ASGI back-pressure middleware — it proves the slot bounds CONCURRENT streams (not request-starts) and validates the await-app-spans-stream assumption (evidence: the least-sure flag resolved by test_slot_held_for_whole_stream)
- [ADD · open] `asyncio.wait_for(sem.acquire(), timeout=0)` is NOT a reliable non-blocking acquire (fires TimeoutError even with free slots in 3.12); use `if not sem.locked(): await sem.acquire()` which acquires synchronously in one event-loop turn (no interleave) (evidence: build finding, refute-read CPython-level confirmation)
