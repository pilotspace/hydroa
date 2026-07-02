# TASK: B3: complete() falls over on CircuitOpenError (per-provider fallback survives an open breaker)

slug: provider-circuit-breakers · created: 2026-07-02 · stage: production
autonomy: auto   <!-- inherited from project default; change-request to a frozen resilience test (F6) -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/proxy/application/fallback_router.py`
    - `FallbackModelRouter.complete` (L227-419) — the alias candidate loop. **L326** `except UpstreamUnavailableError as exc:` is the ONLY fallover edge; **L322-324** comment declares "CircuitOpenError … propagate naturally (abort loop)"; **L90** docstring step 6 "On CircuitOpenError / other: re-raise immediately". Loop exhaustion **L404-419** raises `UpstreamUnavailableError` (or `UpstreamRateLimitedError` if `saw_rate_limit`).
    - imports **L58-63** from `gateway.proxy.domain.errors` — `AllDeploymentsSaturatedError, UpstreamRateLimitedError, UpstreamUnavailableError` (NOTE: `CircuitOpenError` is referenced only in docstrings, NOT imported yet).
  - `apps/gateway/src/gateway/proxy/domain/errors.py:28` — `CircuitOpenError(ProxyError)`, sibling of `UpstreamUnavailableError`.
Context (working folder): the fix is one file + one frozen test re-spec. No config, no DB, no new symbol.
Honors (patterns / conventions):
  - `streaming_resilience.py:59` — the STREAM path ALREADY does the right thing: `except (UpstreamUnavailableError, CircuitOpenError):` → fall over to next attempt (pre-first-byte). B3 aligns `complete()` with this proven sibling behavior (mirror of the B1 stream↔complete asymmetry, opposite direction).
  - `circuit_breaker_proxy.py:42-49` — `BoundCircuitBreakerUpstream.complete`: guards the app-wide breaker, and on delegate `(UpstreamUnavailableError, CircuitOpenError)` calls `on_upstream_error()` then re-raises. So the router receives `CircuitOpenError` when EITHER the outer shared breaker OR an inner per-adapter breaker is open.
  - `use_cases.py:1377,1800,1906` — the use case's TOP-level handlers already catch `(UpstreamUnavailableError, CircuitOpenError)` identically → 503 / terminal SSE frame. So changing the router's terminal exception (CircuitOpenError → UpstreamUnavailableError on all-open) is client-invisible.
Anchors the contract cites: `FallbackModelRouter.complete`, `fallback_router.py:326` except clause, `CircuitOpenError`, `UpstreamUnavailableError`, `streaming_resilience.py` (justifying sibling), `tests/model_fallbacks/test_model_fallbacks.py::test_f6_circuit_open_aborts_no_fallback` (the change-request target).

### ⚠ Grounding-discovered SIBLING finding — NOT in this task's scope (surfaced for Tin, like B6↔B1)
The diagnostic's B3 headline "single app-wide circuit breaker" is actually TWO fixes:
  - **THIS task (fix #2):** the router aborts fallover on `CircuitOpenError`. Fixes the literal symptom for ALIAS traffic — a healthy sibling provider is reached. Successful fallovers call `record_success()` which resets the shared breaker, so the "abort → no reset → shared breaker climbs → all chat 502s" cascade never starts.
  - **SEPARATE follow-up (fix #1):** the outer `app.state.circuit_breaker` (`main.py:630`) is ONE shared instance across ALL providers. PLAIN-model-id (non-alias) traffic to a down provider has no fallover loop → no `record_success` resets → the shared breaker opens → EVERY provider's chat (even healthy, even other aliases) 502s = cross-provider contamination. Fix #2 gives plain-id traffic zero help. The real fix is per-provider keying of the outer breaker — which touches SHARED infra `BoundCircuitBreakerUpstream` (also constructed by `realtime_ws.py:261`, i.e. B2's realtime path) and the single `gateway_circuit_breaker_state` gauge. Recommend a dedicated task, NOT folded into B3 (would break wave-1 disjointness + widen blast radius). Proposed as a spec delta in §7.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `FallbackModelRouter.complete` treats an open circuit breaker (`CircuitOpenError`) as a fallover trigger — try the next candidate — instead of aborting the whole alias request.
Framings weighed:
  - **(chosen) Broaden the existing fallover handler** — add `CircuitOpenError` to the `except (UpstreamUnavailableError, …)` tuple at L326, so an open breaker is handled identically to a 5xx/transport failure. Minimal, surgical, mirrors `streaming_resilience.py`.
  - Separate `except CircuitOpenError` block — duplicates the fallover body (gate.record_failure, counter, continue); rejected as needless duplication.
  - Also make the outer breaker per-provider (fix #1) — rejected FROM THIS TASK: shared-infra blast radius + wave coupling (see §0 sibling finding); deferred to a follow-up task.
Must:
<must>
  - In the alias candidate loop, when `upstream.complete(candidate)` raises `CircuitOpenError`, the router MUST fall over to the next candidate (same as `UpstreamUnavailableError`): call `health_gate.record_failure(candidate)` if a gate is wired, increment the `model_fallbacks_total` counter with `outcome="fell_through"`, release the load-gate slot, and continue the loop.
  - When a later candidate answers (2xx/passthrough) after an earlier candidate's `CircuitOpenError`, the router MUST return that candidate's `(status, body, served_candidate_id)` — billing/health follow the served candidate (unchanged §4 served-model path).
  - When EVERY candidate raises `CircuitOpenError` (or a mix of open-breaker + unavailable) with no served return, the router MUST raise `UpstreamUnavailableError` (existing exhaustion raise, L419) — NOT `CircuitOpenError`.
  - `UpstreamRateLimitedError` handling MUST remain unchanged: a `CircuitOpenError` is NOT a rate-limit, so `saw_rate_limit`/`max_retry_after` are untouched by an open breaker (a rate-limited candidate still raises the 429 exhaustion path).
  - The PLAIN-model-id path (no alias match, L254-257) MUST remain byte-identical: a single `upstream.complete(payload)` with any exception propagating unchanged (no fallover — there is no candidate list).
  - Any exception that is neither `UpstreamUnavailableError` nor `CircuitOpenError` (e.g. a programming bug) MUST still propagate out of the loop unchanged (abort).
</must>
Reject:
<reject>
  - all candidates' breakers open (no healthy sibling) -> raise `UpstreamUnavailableError` -> use case maps to 503 `ERR_UPSTREAM_UNAVAILABLE`
  - a non-router exception raised inside the loop -> propagates (loop aborts; not swallowed)
</reject>
After:
<after>
  - A single provider's open breaker no longer 502s an alias whose other candidate is healthy.
  - The shared outer breaker stops cascading on alias traffic (successful fallovers reset it).
  - `complete()` and `stream_resilient()` now handle `CircuitOpenError` identically (symmetry restored).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Recording a health-gate FAILURE (cooldown) for a candidate whose breaker is already open is the RIGHT signal — lowest confidence because it double-marks an already-unhealthy model. Cost if wrong: a briefly-recovered model stays gated one cooldown longer. Judged correct: an open breaker IS an unhealthy signal, and the health gate is fail-open, so a spurious cooldown only delays re-admission, never blocks it. Confirm at freeze.
  - [ ] The outer `BoundCircuitBreakerUpstream` re-raises `CircuitOpenError` (does not translate it to `UpstreamUnavailableError`) so the router's new `except` actually catches it — CONFIRMED by read (`circuit_breaker_proxy.py:47-49` re-raises).
  - [ ] No frozen test other than F6 asserts "CircuitOpenError propagates from complete()" — the model_fallbacks suite is the router's contract suite; verified F6 is the only such assertion there (grep of tests/ for CircuitOpenError shows the rest are stream/retry/relay/observability paths, not complete()-fallover).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked, top ⚠-flagged with why+cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: open breaker on first candidate falls over to a healthy sibling
  Given an alias mapping to [model-A, model-B]
  And model-A's completion raises CircuitOpenError
  And model-B returns 200
  When the router completes the alias request
  Then it returns model-B's (200, body) with served_model_id = model-B
  And model-B WAS called (fallover happened, not abort)
  And the health gate recorded a failure for model-A and success for model-B

Scenario: all candidates' breakers open → clean exhaustion
  Given an alias mapping to [model-A, model-B]
  And both model-A and model-B raise CircuitOpenError
  When the router completes the alias request
  Then it raises UpstreamUnavailableError (NOT CircuitOpenError)
  And both candidates were attempted

Scenario: open breaker does not affect rate-limit exhaustion semantics
  Given an alias mapping to [model-A, model-B]
  And model-A raises CircuitOpenError
  And model-B raises UpstreamRateLimitedError with Retry-After=7
  When the router completes the alias request
  Then it raises UpstreamRateLimitedError with retry_after=7 (the 429 path, not a generic 502)
  And model-A's open breaker did not suppress the rate-limit signal

Scenario: plain model id is unchanged (no fallover, exception propagates)
  Given a plain model id (no alias match) whose completion raises CircuitOpenError
  When the router completes the request
  Then CircuitOpenError propagates unchanged
  And no second upstream call is made

Scenario: a non-router exception still aborts the loop
  Given an alias mapping to [model-A, model-B]
  And model-A raises a ValueError (a bug, not a resilience signal)
  When the router completes the alias request
  Then the ValueError propagates (loop aborts)
  And model-B is NOT called
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
FallbackModelRouter.complete(payload, upstream=None) -> tuple[int, dict, str]   # (status, body, served_model_id)

  Alias path, per candidate in strategy order:
    upstream.complete(rewritten) ->
      returns (status, body)                     -> served; return (status, body, candidate)
      raises UpstreamUnavailableError            -> fall over (record_failure, counter=fell_through, continue)   [unchanged]
      raises CircuitOpenError                    -> fall over (record_failure, counter=fell_through, continue)   [CHANGED: was re-raise/abort]
      raises UpstreamRateLimitedError            -> fall over + track max Retry-After                            [unchanged; CircuitOpenError never matches this]
      raises anything else                       -> propagate (abort loop)                                       [unchanged]
  Loop exhausted (no served return):
      saw_rate_limit -> raise UpstreamRateLimitedError(max_retry_after)   [unchanged]
      else           -> raise UpstreamUnavailableError                    [now also the terminal for all-breakers-open; was CircuitOpenError]

  Plain model id path: single upstream.complete(payload); all exceptions propagate.   [byte-identical]

Schema: none (no DB, no config, no wire-format change). Client-facing HTTP status unchanged
        (use_cases.py already maps both UpstreamUnavailableError and CircuitOpenError -> 503).
Imports: add CircuitOpenError to the fallback_router.py errors import.
```

Status: DRAFT

**Least-sure flag surfaced at freeze:**
- [contract] The single biggest decision is **SCOPE, not shape**: this task fixes fix #2 (router fallover) ONLY. It leaves the grounding-discovered sibling (fix #1: plain-id cross-provider contamination via the app-wide `app.state.circuit_breaker`) LIVE. Why flagged: a reader could reasonably expect "fix the circuit breaker" to mean per-provider isolation too. Cost of the split: plain-id traffic to a sustained-down provider can still open the shared breaker and 502 other providers until a follow-up task lands. Recommendation: SPLIT (this task = fix #2, surgical + wave-1-disjoint; fix #1 = a separate task touching shared `BoundCircuitBreakerUpstream` infra that B2's realtime path also uses). **Tin decides split-vs-combine at this freeze.**
- [test] This is a genuine CHANGE-REQUEST: it re-specifies frozen test `test_f6_circuit_open_aborts_no_fallback` (`tests/model_fallbacks/test_model_fallbacks.py:272`), which currently asserts the OPPOSITE ("model-B must not be called on CircuitOpenError"). Per ADD rules this is a change request back to Specify, NOT weakening a test to pass a build — F6 is rewritten to assert fallover, and its name/intent flips. Tin must approve the invariant flip.

<!-- Approved -> Status: FROZEN @ vN — approved by <name>. EXIT: frozen + every rejection has a contracted response + names match + lowest-confidence flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the changed branch (the new CircuitOpenError fallover edge).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_circuit_open_first_candidate_falls_over_to_healthy_sibling: arrange alias [A,B], A raises CircuitOpenError, B returns 200 / act complete / assert returns B's (200, body, B) AND B was called AND gate.failure_calls==[A], success==[B]  (REPLACES F6: re-spec abort→fallover)
  - test_all_candidates_circuit_open_raises_upstream_unavailable: arrange alias [A,B] both raise CircuitOpenError / act complete / assert raises UpstreamUnavailableError (not CircuitOpenError) AND both attempted
  - test_circuit_open_then_rate_limited_raises_rate_limited: arrange alias [A,B], A CircuitOpenError, B UpstreamRateLimitedError(retry_after=7) / act / assert raises UpstreamRateLimitedError retry_after==7
  - test_plain_model_id_circuit_open_propagates: arrange plain id raises CircuitOpenError / act / assert CircuitOpenError propagates AND exactly one upstream call
  - test_non_router_exception_aborts_loop: arrange alias [A,B], A raises ValueError / act / assert ValueError propagates AND B not called
</test_plan>

Tests live in: `model_fallbacks/test_model_fallbacks.py` (re-spec F6 + add cases). MUST run red before Build.
<!-- token with "/" = project root; this file lives at tests/model_fallbacks/ -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/fallback_router.py` `apps/gateway/tests/model_fallbacks/test_model_fallbacks.py`
Strategy (ordered batches):
  1. Import `CircuitOpenError` in fallback_router.py (add to L58-63 errors import).
  2. Broaden the loop handler L326 `except UpstreamUnavailableError as exc:` → `except (UpstreamUnavailableError, CircuitOpenError) as exc:`; the `isinstance(exc, UpstreamRateLimitedError)` guard inside is unaffected.
  3. Update the two docstrings (class docstring step 6 @L90; inline comment @L322-324) to state CircuitOpenError now falls over.
  4. Re-spec F6 + add the new red tests.
Known-problem fixes:
  - trap: catching CircuitOpenError too broadly (e.g. outside the loop) would swallow the terminal exhaustion raise → keep the change to the IN-LOOP handler only.
  - trap: changing the exhaustion raise → do NOT; L419 already raises UpstreamUnavailableError which is the correct all-open terminal.
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): the plain-model-id path and all non-(Unavailable|CircuitOpen) exceptions MUST remain propagate-unchanged.
Code lives in: `apps/gateway/src/gateway/proxy/application/fallback_router.py`
Constraints: do NOT change any OTHER test or contract; no new dependency.

<!-- EXIT: all green; coverage held; no unrelated test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test (other than the change-requested F6) or contract was altered during build
- [ ] the green was EARNED, not gamed (adversarial refute-read; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing safe (pure control-flow change; no shared state added)
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm at gate)
- [ ] An alias request whose first candidate's breaker is open returns the healthy sibling's 200 (not a 502) — confirmed by test_circuit_open_first_candidate_falls_over_to_healthy_sibling + the sibling model WAS called
- [ ] all-breakers-open raises UpstreamUnavailableError, never CircuitOpenError — confirmed by test_all_candidates_circuit_open_raises_upstream_unavailable
- [ ] rate-limit exhaustion semantics unchanged in the presence of an open breaker — confirmed by test_circuit_open_then_rate_limited_raises_rate_limited
- [ ] plain-id + non-router-exception paths byte-identical (propagate, no fallover) — confirmed by the two propagation tests
- [ ] `git diff` touches only fallback_router.py + test_model_fallbacks.py — confirmed by diff review (no shared-breaker infra touched → wave-1 disjoint)

### Deep checks
- [ ] WIRING (code) — the broadened except is reached by the outer breaker's re-raised CircuitOpenError; confirm via test that drives a real open-breaker path
- [ ] DEAD-CODE (code) — no new unused symbol
- [ ] SEMANTIC — docstrings updated to match new behavior

### Refute-read verdict — the earned-green check (required for an auto-PASS)
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `model_fallbacks_total{outcome="fell_through"}` following breaker-open events; ratio of alias 503s to total alias traffic.

### Decisions (ADR)
<harvested at done>

### Spec delta
- [SPEC · open] fix #1 — per-provider isolation of the app-wide `app.state.circuit_breaker`: plain-model-id traffic to a down provider can open the shared breaker and 502 healthy providers (cross-provider contamination). Touches shared `BoundCircuitBreakerUpstream` (also used by realtime_ws.py:261 / B2) + the single `gateway_circuit_breaker_state` gauge. (evidence: grounding trace during B3; sibling of B3 like B6↔B1)

### Competency deltas
- [ADD · open] a diagnostic "single X breaker" headline conflated two fixes with different blast radii; grounding split them — reinforces "ground before you size" (evidence: B3 fix#1/fix#2 split).
