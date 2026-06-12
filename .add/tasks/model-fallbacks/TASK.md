# TASK: Model-group aliases with ordered candidate fallbacks; served-model billing

slug: model-fallbacks · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Model-group alias resolution with ordered candidate fallback and served-model billing correctness

Framings weighed:
  - **Alias router above upstream (chosen)**: a thin `FallbackModelRouter` layer sits above
    `upstream.complete()` in the use-case, iterating candidate list on `UpstreamUnavailableError`.
    Billing fires once after the candidate that actually answered — single ledger row by construction,
    same as retry-policy's single-call-site invariant. Aliases are purely additive; no existing
    wire behavior changes.
  - **Rewrite model at HTTP layer / middleware (rejected)**: would skip governance checks per
    candidate (budget/allowlist must run against the served model, not the alias); also loses
    the circuit-breaker interplay that lives inside `upstream.complete()`.
  - **Router integrated into upstream.complete() (rejected)**: would blur the boundary between
    infrastructure (HTTP transport) and application orchestration; MILESTONE shared decision
    says resilience wraps the upstream call site at the use-case layer.

Must:
<must>
  - A request whose `"model"` field matches an alias in `GATEWAY_MODEL_GROUPS` MUST be resolved
    to an ordered candidate list before budget/rate-limit/cache checks.
  - A request whose `"model"` field does NOT match any alias MUST behave exactly as v5 —
    exactly one upstream call with the original model string, zero new code path side effects.
  - The fallback router MUST iterate candidates in declared order; for each candidate it MUST
    call `upstream.complete()` with `payload["model"]` rewritten to that candidate's id.
  - Fall-through to the next candidate MUST occur ONLY on `UpstreamUnavailableError`
    (retryable exhaustion). All other outcomes abort the loop immediately.
  - 4xx passthrough (other than 429-exhaustion which surfaces as `UpstreamUnavailableError`)
    MUST be returned immediately with no fallback — the error is model-independent.
  - `CircuitOpenError` from any candidate MUST abort the loop immediately with no fallback —
    the global per-replica breaker being open means the OpenRouter upstream itself is unhealthy;
    all candidates share the same transport/breaker.
  - When all candidates are exhausted (all raised `UpstreamUnavailableError`), the router
    MUST raise `UpstreamUnavailableError` — mapping to 502 ERR_UPSTREAM_UNAVAILABLE (v5 semantics).
  - Served-model billing: budget check, rate-limit, usage recording, ledger row, and pricing
    snapshot MUST all use the `served_model_id` (3rd element of `router.complete()`'s 3-tuple
    return), NOT `body["model"]` and NOT the alias string. The response body returned to the
    client carries whatever the upstream returned, untouched.
  - Cache key for pre-flight lookup MUST use the alias string as-is (tenant-visible
    request-level concern — same alias+prompt yields the same cached answer). See §3 rationale.
  - The entry catalog access check MUST be alias-aware: for an alias, ALL candidates are
    validated via check_for_tenant + is_active (any failure ⇒ MODEL_UNKNOWN for the request);
    candidate lists are capped at 5 entries by Settings validation. See §3 CATALOG INTERACTION.
  - Streaming: `stream()` with an alias MUST resolve to the FIRST candidate only. No fallback
    on stream failure is in scope for this task. First-candidate resolution keeps aliases usable
    on stream path.
  - The `ModelHealthGate` protocol MUST be defined in this task's domain layer with three
    methods: `async def is_available(self, model_id: str) -> bool`,
    `async def record_failure(self, model_id: str) -> None`, and
    `async def record_success(self, model_id: str) -> None`. The fallback router MUST accept
    `health_gate: ModelHealthGate | None = None`; `None` means all candidates available.
  - A candidate for which `health_gate.is_available(candidate_id)` returns `False` MUST be
    skipped without an upstream call, exactly as if it raised `UpstreamUnavailableError`.
  - If ALL candidates are skipped by the health gate, the router MUST raise
    `UpstreamUnavailableError` with zero upstream calls.
  - When a gate is wired (alias path only): after a candidate raises `UpstreamUnavailableError`,
    the router MUST call `await gate.record_failure(candidate)`; after a candidate returns any
    `(status, body)` (including 4xx), the router MUST call `await gate.record_success(candidate)`.
    `CircuitOpenError` and other re-raised exceptions MUST NOT trigger any gate recording call.
  - Gate recording calls MUST never raise out of the router (the gate itself is fail-open).
  - Served-model billing: budget check, rate-limit, usage recording, ledger row, and pricing
    snapshot MUST all use the `served_model_id` from the 3rd element of the router's return
    tuple, NOT `body["model"]` and NOT the alias string. The response body is returned
    untouched/tenant-visible.
  - The `GATEWAY_MODEL_GROUPS` Settings field MUST default to `{}` (feature off, v5 byte-identical).
  - Pydantic `ValidationError` MUST be raised at startup if: (a) an alias key collides with any
    candidate id appearing in ANY group; (b) any candidate list is empty.
  - New env knobs MUST use `GATEWAY_` prefix; defaults MUST preserve v5 exactly.
  - Observability: Prometheus counter `gateway_model_fallbacks_total{alias, from_model, to_model, outcome}`
    where `outcome ∈ {fell_through, served, exhausted}` MUST be incremented on each fall-through
    and on the final resolution. Label cardinality is bounded to model ids from config only.
    `structlog.WARNING` per fall-through (no payload or key material). Span event on fallback.
  - Every new `app.state` seam introduced MUST have a paired production-wiring regression test
    in a separate non-frozen suite (foundation v6 rule, pattern: `tests/retry_policy_wiring/`).
  - Typed-extras rule: NO `inspect.signature` / `hasattr` dispatch — use TypedDict/explicit
    declared capabilities (foundation rule); `ModelHealthGate` is a `typing.Protocol`.
</must>

Reject:
<reject>
  - Alias key collides with any candidate id → ValidationError at startup — "ALIAS_COLLIDES_WITH_CANDIDATE"
  - Candidate list is empty → ValidationError at startup — "EMPTY_CANDIDATE_LIST"
  - CircuitOpenError during fallback loop → abort immediately, no fallback — "BREAKER_OPEN_ABORT"
  - 4xx (non-retryable) from any candidate → returned immediately, no fallback — "PASSTHROUGH_4XX_NO_FALLBACK"
  - All candidates exhausted → UpstreamUnavailableError → 502 — "ALL_CANDIDATES_EXHAUSTED"
  - All candidates gated unavailable → UpstreamUnavailableError, zero upstream calls — "ALL_CANDIDATES_GATED"
  - stream() with alias → only first candidate resolved, no fallback on failure — "STREAM_NO_FALLBACK"
</reject>

After:
<after>
  - An alias request whose first candidate answers: the use case returns exactly as if the
    first candidate was the requested model; recorder fires with model=first_candidate_id.
  - An alias request where first candidate exhausts and second answers: the use case returns
    the second candidate's response; recorder fires with model=second_candidate_id;
    gateway_model_fallbacks_total{outcome=fell_through} incremented once (for the first
    candidate falling through) and {outcome=served} incremented once (for the second serving).
  - An alias request where all candidates exhaust: UpstreamUnavailableError propagates to
    use case → 502; gateway_model_fallbacks_total{outcome=exhausted} incremented.
  - A plain model id (no alias match): no change to use case flow; router is bypassed entirely;
    exactly 1 upstream call with the original model string.
  - With `GATEWAY_MODEL_GROUPS={}` (default): behavior is byte-identical to v5 for every request.
  - health_gate skipping: skipped candidates are not called; if all are skipped, UpstreamUnavailableError
    with zero upstream transport calls.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Cache key uses alias string (not resolved candidate): the decision that pre-flight cache
    lookup uses the alias as-is means a cache hit for alias "fast" → "gpt-4o" (from a prior
    request that resolved to gpt-4o) will serve the same cached body even if the next fallback
    resolution would go to candidate B. This is INTENTIONAL per binding decision #4 (context
    file) but is the assumption most likely to be revisited: if tenants observe "stale model"
    responses when a cached alias response is served but the candidate that originally generated
    it is now different from the one that would be selected, they may demand cache invalidation
    on alias re-assignment. If wrong: the cache must key on (alias, first-available-candidate),
    adding cache invalidation complexity. Cost of current decision: possible stale-model
    mismatch between cached body's "model" field and the alias intent. The alias is purely
    an operator routing concern; the tenant sees whatever the upstream returned in the body
    (which carries the concrete model id). SURFACE AT FREEZE.

  ⚠ CircuitOpenError aborts the loop globally (not per-candidate): the shared breaker means
    ALL candidates share one failure signal. If future work introduces per-model circuit breakers
    (post v6), CircuitOpenError semantics change. If wrong in v6: a healthy candidate B could
    be unreachable due to A tripping the shared breaker. Cost: over-blocking in failure
    conditions; the conservative position is acceptable for a shared-breaker architecture.

  - [x] Governance checks (allowlist, budget, rate-limit) run against the ALIAS at the
    use-case entry; served-model billing uses the resolved candidate. The allowlist check
    runs against the alias (before resolution), which means an alias string that does not
    appear in the model_allowlist would be rejected. If a tenant's model_allowlist specifies
    candidates but not the alias, all alias requests are rejected. OPERATORS must add the
    alias string to model_allowlist if using it. Document in §3 CONTRACT.

  - [x] The fallback router is a new domain/application-layer class (not an infrastructure
    adapter); it depends only on `CompletionUpstream` and `ModelHealthGate` protocols.
    The `FallbackModelRouter` wraps `upstream.complete()` calls — it does NOT extend
    `OpenRouterCompletionUpstream` or any infrastructure class.

  - [x] No Redis logic is in this task — cooldown-circuit (parallel task) implements the
    Redis gate. The `ModelHealthGate` protocol is defined here with exactly:
    `async def is_available(self, model_id: str) -> bool`.

  - [x] Stream resolution calls `upstream.stream(payload_with_candidate)` — it does NOT
    call `complete()`. The stream path rewrites `payload["model"]` to `candidates[0]` and
    delegates directly. No health gate check on stream path in this task.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: F1 — first candidate exhausts, second candidate serves
  Given GATEWAY_MODEL_GROUPS={"fast": ["model-A", "model-B"]}
  And upstream returns UpstreamUnavailableError for model-A
  And upstream returns (200, body) for model-B
  When the proxy use case calls the fallback router with model="fast"
  Then response is (200, body_from_model_B, "model-B") [3-tuple]
  And upstream.complete() was called exactly twice: first with model-A, then with model-B
  And usage recorder fires with served_model_id="model-B" (3rd tuple element)
  And gate.record_failure("model-A") was awaited exactly once
  And gate.record_success("model-B") was awaited exactly once

Scenario: F2 — first candidate serves immediately, no fallback
  Given GATEWAY_MODEL_GROUPS={"fast": ["model-A", "model-B"]}
  And upstream returns (200, body) for model-A
  When the proxy use case calls the fallback router with model="fast"
  Then response is (200, body_from_model_A, "model-A") [3-tuple]
  And upstream.complete() was called exactly once (with model-A)
  And no fallback counter is incremented

Scenario: F3 — plain model id (not an alias) bypasses fallback router
  Given GATEWAY_MODEL_GROUPS={"fast": ["model-A", "model-B"]}
  And upstream returns (200, body) for any model
  When the proxy use case calls with model="openai/gpt-4o" (not an alias)
  Then router.complete() returns (200, body, "openai/gpt-4o") [3-tuple; served_model_id = original payload model string]
  And upstream.complete() was called exactly once with model="openai/gpt-4o"
  And the call is byte-identical to v5 behavior (zero new code paths triggered)

Scenario: F4 — all candidates exhaust, UpstreamUnavailableError raised
  Given GATEWAY_MODEL_GROUPS={"fast": ["model-A", "model-B"]}
  And upstream returns UpstreamUnavailableError for both model-A and model-B
  When the proxy use case calls the fallback router with model="fast"
  Then UpstreamUnavailableError is raised after exactly 2 upstream calls
  And gateway_model_fallbacks_total{outcome="exhausted"} is incremented

Scenario: F5 — 4xx passthrough aborts immediately, no fallback
  Given GATEWAY_MODEL_GROUPS={"fast": ["model-A", "model-B"]}
  And upstream returns (400, error_body) for model-A
  When the proxy use case calls the fallback router with model="fast"
  Then (400, error_body, "model-A") is returned after exactly 1 upstream call [3-tuple]
  And model-B was never called
  And gate.record_success("model-A") was awaited (4xx proves the upstream path is healthy)

Scenario: F6 — CircuitOpenError aborts immediately, no fallback
  Given GATEWAY_MODEL_GROUPS={"fast": ["model-A", "model-B"]}
  And upstream raises CircuitOpenError for model-A
  When the proxy use case calls the fallback router with model="fast"
  Then CircuitOpenError propagates after exactly 1 upstream call (or 0 if breaker at guard)
  And model-B was never called

Scenario: F7 — served-model billing uses the candidate that answered, not the alias
  Given GATEWAY_MODEL_GROUPS={"fast": ["model-A", "model-B"]}
  And upstream returns UpstreamUnavailableError for model-A
  And upstream returns (200, body) for model-B
  When the fallback router routes model="fast"
  Then router.complete() returns a 3-tuple where served_model_id (3rd element) == "model-B"
  And body is returned untouched (body["model"] field carries whatever the upstream set)
  And the use case calls _fire_record_with_raw() with served_model_id="model-B" (3rd element)
  And it is NOT called with model="fast" or model="model-A"

Scenario: F8 — health_gate marks first candidate unavailable, skipped without attempt
  Given GATEWAY_MODEL_GROUPS={"fast": ["model-A", "model-B"]}
  And health_gate.is_available("model-A") returns False
  And health_gate.is_available("model-B") returns True
  And upstream returns (200, body) for model-B
  When the fallback router routes model="fast" with the health gate wired
  Then upstream.complete() was called exactly once, with model-B (not model-A)
  And gate.record_success("model-B") was awaited (model-B returned a result)
  And with health_gate=None, model-A is attempted first (default-off pin)

Scenario: F9 — all candidates gated unavailable, UpstreamUnavailableError, zero upstream calls
  Given GATEWAY_MODEL_GROUPS={"fast": ["model-A", "model-B"]}
  And health_gate.is_available() returns False for both model-A and model-B
  When the fallback router routes model="fast" with the health gate wired
  Then UpstreamUnavailableError is raised
  And upstream.complete() was never called (zero transport calls)

Scenario: F10 — Settings validation rejects alias collision and empty candidate list
  Given GATEWAY_MODEL_GROUPS={"model-A": ["model-A", "model-B"]}  [alias collides with candidate]
  When Settings is constructed
  Then pydantic ValidationError is raised referencing alias collision
  And given GATEWAY_MODEL_GROUPS={"fast": []}
  When Settings is constructed
  Then pydantic ValidationError is raised referencing empty candidate list

Scenario: F11 (GREEN) — stream() with alias resolves to first candidate, no fallback on failure
  Given GATEWAY_MODEL_GROUPS={"fast": ["model-A", "model-B"]}
  And upstream returns an error on stream for model-A
  When stream() is called with model="fast"
  Then upstream.stream() was called exactly once with model-A (first candidate)
  And no fallback attempt was made for model-B
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LOWEST-CONFIDENCE FLAGS AT FREEZE (surface before orchestrator approval):

  ⚠ [spec] CACHE KEY USES ALIAS STRING — decision rationale:
    Pre-flight cache lookup (step 4.5 in use_cases.py) happens BEFORE the fallback loop
    iterates candidates. At cache-check time we only know the alias, not the served model.
    BINDING DECISION: cache key remains the alias string (same alias+prompt → same cached
    answer is acceptable; avoids cache misses on fallback). This means a cache HIT for
    an alias returns the response from whatever candidate generated it in the original
    (non-cached) request, regardless of which candidate would be selected today. The
    "model" field in the cached body carries whatever OpenRouter set (the concrete model
    id) — tenant-visible. The operator re-assigning a group alias does NOT invalidate
    the cache; TTL-based expiry is the only invalidation mechanism.
    Cost if wrong: tenant receives a cached response whose "model" field names a candidate
    different from the current first-available candidate. Mitigation: TTL-based cache
    expiry; operators can flush Redis cache on alias re-assignment.
    SURFACE AT FREEZE — orchestrator must confirm or override.

  ⚠ [spec] MODEL ALLOWLIST CHECK RUNS AGAINST THE ALIAS — the use case validates
    `model_id` (alias string) against the key's model_allowlist BEFORE resolution.
    Operators using model_allowlist must include the alias string, NOT the candidate ids,
    in the allowlist. If the design is wrong (operators want to allowlist candidates),
    the allowlist check would need to move post-resolution or be alias-aware.
    Cost if wrong: tenants denied access to aliases because their allowlist names
    candidates but not the alias. Operators must be informed of this semantic.
    SURFACE AT FREEZE.

---

INTERNAL SEAM (not an HTTP endpoint)

ModelHealthGate protocol (gateway/proxy/domain/ports.py — additive):
  class ModelHealthGate(Protocol):
      async def is_available(self, model_id: str) -> bool:
          """Return True iff the model should be attempted. False = skip (cooled)."""
          ...
      async def record_failure(self, model_id: str) -> None:
          """Record that this candidate raised UpstreamUnavailableError."""
          ...
      async def record_success(self, model_id: str) -> None:
          """Record that this candidate returned any (status, body) — including 4xx."""
          ...
  # cooldown-circuit task implements the Redis-backed gate.
  # None (default) = all candidates available; zero gate interaction.

FallbackModelRouter seam (new class, gateway/proxy/application/fallback_router.py):
  class FallbackModelRouter:
      def __init__(
          self,
          upstream: CompletionUpstream,
          model_groups: dict[str, list[str]],   # from Settings.model_groups
          health_gate: ModelHealthGate | None = None,
          metrics_registry: MetricsRegistry | None = None,
      ) -> None: ...

      async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any], str]:
          """Resolve alias → candidates; iterate with fallback on UpstreamUnavailableError.

          Returns (status_code, json_body, served_model_id) — a 3-tuple.

          served_model_id:
            - Plain model id path: the original payload["model"] string, unchanged.
            - Alias path: the CANDIDATE id whose complete() returned (the attempt that
              answered). This is the id the router sent to upstream, NOT body["model"],
              which may differ (e.g. OpenRouter ":free" suffix variants). The use case
              MUST key billing/recording on this 3rd element, never on body["model"].

          Plain model id (not an alias): delegate directly to upstream.complete(),
          zero new code path side effects.

          Alias resolution:
            For each candidate in candidates (ordered):
              1. If health_gate is not None and not await health_gate.is_available(candidate):
                 skip (treat as fell_through without upstream call). No gate recording call.
              2. Rewrite payload["model"] = candidate.
              3. Call upstream.complete(rewritten_payload).
              4. On (status, body): if gate wired → await gate.record_success(candidate).
                 Return (status, body, candidate) immediately (served). Record served outcome.
              5. On UpstreamUnavailableError: if gate wired → await gate.record_failure(candidate).
                 Record fell_through, continue to next candidate.
              6. On CircuitOpenError: re-raise immediately (no gate call, no fallback).
              7. On any other exception: re-raise immediately (no gate call, no fallback).
            If all candidates exhausted (or gated): raise UpstreamUnavailableError.

          Gate calls MUST never raise out of the router (gate is fail-open by contract).
          body["model"] is returned untouched — tenant-visible, not used for billing.
          """

      def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
          """Resolve alias → first candidate only; delegate to upstream.stream().

          Plain model id: delegate directly.
          Alias: rewrite payload["model"] = candidates[0]; call upstream.stream().
          No health gate check on stream path (v6 scope boundary).
          No fallback on stream failure (deferred beyond v6).
          """

SETTINGS (gateway/core/config.py additions):
  model_groups: dict[str, list[str]] = Field(default_factory=dict)
  # GATEWAY_MODEL_GROUPS — JSON string; parsed by pydantic-settings.
  # e.g. GATEWAY_MODEL_GROUPS='{"fast": ["nvidia/nemotron-3-nano-30b-a3b:free", "meta-llama/llama-3.3-8b:free"]}'
  # Default {} = feature off, v5 byte-identical.
  # Validators (model_validator, mode="after"):
  #   1. All candidate lists must be non-empty → ValidationError "EMPTY_CANDIDATE_LIST"
  #   2. No alias key may appear as a candidate id in ANY group → ValidationError "ALIAS_COLLIDES_WITH_CANDIDATE"
  #   3. No candidate list may exceed 5 entries → ValidationError "TOO_MANY_CANDIDATES"
  #      (bounds the per-request catalog-validation cost — see CATALOG INTERACTION)

ALIAS RESOLUTION POINT:
  Injected into CompletionUseCase as app.state.model_router (FallbackModelRouter).
  The use case calls app.state.model_router.complete(body) INSTEAD OF upstream.complete(body)
  AFTER governance/guardrail/cache checks (same position as the existing upstream.complete call).
  Resolution happens INSIDE FallbackModelRouter.complete():
    - Plain model id → transparent pass-through to upstream.complete(); served_model_id = original
      payload model string.
    - Alias → iterate candidates; served_model_id = the CANDIDATE id whose complete() returned.
  The use case unpacks (status, body, served_model) and uses served_model for
  billing/recording (_fire_record_with_raw()), NEVER the alias and NEVER body["model"].

SERVED-MODEL SURFACING (A1 — billing correctness):
  FallbackModelRouter.complete() returns tuple[int, dict[str, Any], str] =
  (status_code, json_body, served_model_id).
  This is a NEW seam with its own 3-tuple contract; it does NOT satisfy the CompletionUpstream
  2-tuple shape. The router is NOT a drop-in CompletionUpstream replacement — it is a distinct
  application-layer orchestrator. The use case adapts at its single call site (exactly one
  router.complete() call per request) by unpacking 3 elements.
  Rationale: OpenRouter's body["model"] format is not guaranteed to match the catalog candidate
  id (e.g. ":free" suffix variants). Billing/pricing snapshot MUST key on the catalog candidate
  id we routed to. A shared-router side-channel attribute would race across concurrent requests.
  body["model"] stays untouched and tenant-visible.

FALL-THROUGH CLASSIFICATION TABLE:
  Outcome from upstream.complete()     | Router action                           | Gate call (if wired)
  -------------------------------------|------------------------------------------|---------------------
  UpstreamUnavailableError             | fall-through to next candidate           | record_failure(candidate)
  CircuitOpenError                     | abort loop, re-raise                     | NONE
  Any (status, body) incl. 4xx        | return (status, body, candidate)         | record_success(candidate)
  Other exception                      | abort loop, re-raise                     | NONE
  health_gate.is_available() → False  | skip candidate (no upstream call)        | NONE (skip, not failure)

OBSERVABILITY:
  Prometheus counter: gateway_model_fallbacks_total{alias, from_model, to_model, outcome}
    alias      — the alias string (bounded to alias keys in config)
    from_model — candidate that fell through OR was skipped
    to_model   — candidate that served (or "exhausted" sentinel — use literal "_exhausted")
    outcome    ∈ {fell_through, served, exhausted}
  structlog WARNING per fall-through: alias=<alias>, from_model=<candidate>, attempt=<N>
    MUST NOT include payload content or API key material.
  OtelSpan event attribute "fallback" added to span when any fallback occurred.

SINGLE-BILL INVARIANT:
  The use case calls router.complete(body) exactly once. The router may call upstream.complete()
  multiple times (once per candidate), but only one outcome reaches the use case.
  _fire_record_with_raw() in use_cases.py is reached exactly once per request.
  No ledger write path is inside the fallback loop.

MODEL_ALLOWLIST INTERACTION (documented, not blocking):
  Allowlist check runs against the alias string (or plain model id) BEFORE alias resolution.
  Operators must add alias strings to model_allowlist when using aliases AND per-key allowlists.
  Candidate ids do NOT need to be in the allowlist — they are internal routing artifacts.

CATALOG INTERACTION (A4 — aliases must survive the entry catalog check):
  use_cases.py validates model_id via checker.check_for_tenant() + checker.is_active()
  at entry (lines ~439/447) — an alias string is NOT a catalog model and would 404
  ERR_MODEL_UNKNOWN before ever reaching the router. The build MUST make this check
  alias-aware: when model_id is an alias (key in settings.model_groups), EVERY candidate
  in the group is validated with check_for_tenant + is_active instead of the alias string.
  Any candidate failing ⇒ MODEL_UNKNOWN for the whole request (conservative: never route
  to — or bill — a model the tenant cannot access; fallback may serve ANY candidate, so
  all must be authorized up front). Cost: ≤5 catalog lookups per alias request (bounded
  by validator #3). Plain model ids keep the existing single check, byte-identical.

STREAMING BOUNDARY:
  stream() resolves alias → candidates[0] only. No pre-stream health gate check in this task.
  No mid-stream or pre-stream fallback. Stream errors propagate as v5 (UpstreamUnavailableError).
  Stream fallback complexity is deferred per MILESTONE.md streaming-boundary decision.

WIRING (create_app / app.state):
  app.state.model_router = FallbackModelRouter(
      upstream=app.state.completion_upstream,
      model_groups=settings.model_groups,
      health_gate=None,  # cooldown-circuit task wires the Redis gate here
      metrics_registry=app.state.metrics_registry,
  )
  Paired production-wiring regression test suite: apps/gateway/tests/model_fallbacks_wiring/

SUPERSESSION:
  No prior contracts are superseded. This task adds a new seam above upstream.complete().
  The frozen CompletionUpstream protocol (2-tuple) and use_cases.py "calls complete() exactly
  once" invariant are preserved at the upstream layer. FallbackModelRouter.complete() returns a
  3-tuple (status, body, served_model_id) — it is a NEW seam, NOT a CompletionUpstream
  implementor. The use case has exactly one router.complete() call site and unpacks 3 elements.
  Wiring order: model-fallbacks builds FIRST with health_gate=None; cooldown-circuit's build
  then passes app.state.cooldown_gate into the router construction in main.py. This task does
  not wire any gate instance.

---

Amendment history (orchestrator freeze review, 2026-06-12):

  A1 — served-model surfacing becomes explicit 3-tuple (billing correctness):
    FallbackModelRouter.complete() now returns tuple[int, dict[str, Any], str] where the 3rd
    element is the catalog candidate id the router sent to upstream (plain path: original payload
    model string; alias path: winning candidate id). Rationale: OpenRouter body["model"] format
    is not guaranteed to match the catalog candidate id (e.g. ":free" suffix variants); billing
    MUST key on the candidate id we routed to; a side-channel attribute would race concurrently.
    body["model"] stays untouched/tenant-visible. The router is a NEW seam, not a
    CompletionUpstream 2-tuple drop-in; use case unpacks 3 elements at its single call site.

  A2 — ModelHealthGate protocol gains recording hooks (integration gap):
    Protocol extended with record_failure(model_id) and record_success(model_id). Router calls
    record_failure after UpstreamUnavailableError and record_success after any (status, body)
    return (including 4xx — proves upstream path healthy). CircuitOpenError and other re-raised
    exceptions trigger NO gate call. gate=None: zero gate interaction. Gate calls never raise
    out of the router (gate is fail-open by contract; no extra try/except needed in router).
    Rationale: the router is the only component observing per-candidate outcomes; the protocol
    must own the recording hooks so any gate implementation can track health without a seam gap.

  A3 — red discipline: skip → fail (no silent skips):
    _make_router() helper in the test suite now calls pytest.fail("RED: FallbackModelRouter not
    yet implemented — build pending") instead of pytest.skip(), so the suite reports FAILED red
    rather than skipping. Per-test deferred-import pattern preserved (collection still succeeds).

  A4 — alias-aware entry catalog check + candidate cap (orchestrator, applied by hand):
    Discovery: use_cases.py:439/447 run check_for_tenant/is_active on the raw model string —
    an alias would 404 MODEL_UNKNOWN before reaching the router. Contract now requires the
    check to validate ALL candidates of an alias (conservative: every potentially-served
    model must be tenant-authorized and active), with Settings validator #3 capping groups
    at 5 candidates to bound the cost. Plain model ids keep the v5 single check.
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [spec] cache key uses the ALIAS string (not the served
candidate) — CONFIRMED as designed: TTL expiry is the invalidation mechanism; body["model"]
remains the tenant-visible truth. Second flag (allowlist checks the alias, operators must
allowlist alias strings) — CONFIRMED; documented in §3 MODEL_ALLOWLIST INTERACTION.
Amendments A1–A4 applied at freeze (see Amendment history in the contract fence).
<!-- The freeze IS the one approval — orchestrator confirms cache-key decision + allowlist
     interaction before Status: FROZEN. The two ⚠ flags above must be explicitly confirmed
     or overridden at freeze. Approved -> Status: FROZEN @ vN — approved by <name>. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% of fallback-loop / alias-resolution / billing-seam / health-gate paths
in FallbackModelRouter and the Settings validation change.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_f1_first_candidate_exhausts_second_serves:
      arrange: model_groups={"fast": ["model-A", "model-B"]}; upstream returns
               UpstreamUnavailableError for model-A, (200, BODY_B) for model-B;
               RecordingFakeGate (records record_failure / record_success calls)
      act: status, body, served_model = await router.complete({"model": "fast", ...})
      assert: status==200; body==BODY_B; served_model=="model-B" (3rd element);
              exactly 2 complete() calls in order model-A then model-B;
              gate.record_failure("model-A") awaited exactly once;
              gate.record_success("model-B") awaited exactly once

  - test_f2_first_candidate_serves_no_fallback:
      arrange: model_groups={"fast": ["model-A", "model-B"]}; upstream returns (200, BODY_A) for model-A
      act: status, body, served_model = await router.complete({"model": "fast", ...})
      assert: status==200; body==BODY_A; served_model=="model-A" (3rd element);
              exactly 1 complete() call with model="model-A"

  - test_f3_plain_model_id_bypasses_router:
      arrange: model_groups={"fast": ["model-A", "model-B"]}; upstream returns (200, plain_body)
      act: status, body, served_model = await router.complete({"model": "openai/gpt-4o", ...})
      assert: exactly 1 complete() call with model="openai/gpt-4o" (unchanged);
              served_model=="openai/gpt-4o" (original payload model string, unchanged);
              zero alias router logic triggered

  - test_f4_all_candidates_exhaust_raises:
      arrange: model_groups={"fast": ["model-A", "model-B"]}; upstream always raises UpstreamUnavailableError
      act: await router.complete({"model": "fast", ...}) → expect UpstreamUnavailableError
      assert: raises UpstreamUnavailableError; exactly 2 complete() calls (model-A, model-B)
      [no return tuple to unpack — exception path]

  - test_f5_4xx_passthrough_no_fallback:
      arrange: model_groups={"fast": ["model-A", "model-B"]}; upstream returns (400, BODY_4XX) for model-A;
               RecordingFakeGate wired
      act: status, body, served_model = await router.complete({"model": "fast", ...})
      assert: status==400; body==BODY_4XX; served_model=="model-A" (3rd element);
              exactly 1 complete() call; model-B never called;
              gate.record_success("model-A") awaited (4xx proves upstream path healthy)

  - test_f6_circuit_open_aborts_no_fallback:
      arrange: model_groups={"fast": ["model-A", "model-B"]}; upstream raises CircuitOpenError for model-A
      act: await router.complete({"model": "fast", ...}) → expect CircuitOpenError
      assert: raises CircuitOpenError; model-B never called
      [no return tuple to unpack — exception path]

  - test_f7_served_model_billing_uses_candidate_not_alias:
      arrange: model_groups={"fast": ["model-A", "model-B"]}; upstream: model-A raises
               UpstreamUnavailableError, model-B returns (200, BODY_B)
      act: status, body, served_model = await router.complete({"model": "fast", ...})
      assert: served_model (3rd element) == "model-B"; NOT "fast" and NOT "model-A";
              body returned untouched (body["model"] == BODY_B["model"], whatever upstream set)

  - test_f8_health_gate_skips_unavailable_candidate:
      arrange: model_groups={"fast": ["model-A", "model-B"]}; RecordingFakeGate marks model-A
               unavailable, model-B available; upstream returns (200, BODY_B) for model-B;
               ALSO: same router with gate=None tries model-A first (default-off pin)
      act a (gate wired): status, body, served_model = await router.complete({"model": "fast", ...})
      assert: exactly 1 complete() call with model="model-B" (model-A skipped without attempt);
              gate.record_success("model-B") awaited once; gate.record_failure NOT called
      act b (gate=None): first complete() call uses model="model-A"

  - test_f9_all_candidates_gated_zero_upstream_calls:
      arrange: model_groups={"fast": ["model-A", "model-B"]}; gate returns False for both
      act: await router.complete({"model": "fast", ...}) → expect UpstreamUnavailableError
      assert: raises UpstreamUnavailableError; complete() was never called (call_count == 0)

  - test_f10_settings_validation_alias_collision_and_empty_list:
      arrange a: GATEWAY_MODEL_GROUPS='{"model-A": ["model-A", "model-B"]}'
      act a: Settings(..., model_groups={"model-A": ["model-A", "model-B"]}) or env parse
      assert a: pydantic ValidationError raised
      arrange b: GATEWAY_MODEL_GROUPS='{"fast": []}'
      act b: Settings(..., model_groups={"fast": []})
      assert b: pydantic ValidationError raised

  - test_f10_settings_validation_too_many_candidates (A4):
      arrange: GATEWAY_MODEL_GROUPS with a 6-candidate list
      act: Settings(..., model_groups={"fast": [6 ids]})
      assert: pydantic ValidationError referencing the 5-candidate cap

  - test_f11_stream_resolves_to_first_candidate_no_fallback_green:
      arrange: model_groups={"fast": ["model-A", "model-B"]}; upstream.stream raises
               UpstreamUnavailableError for model-A (to prove no retry)
      act: router.stream({"model": "fast", ...}) — obtain the generator, then iterate it
      assert: exactly 1 stream() call with model="model-A"; model-B never called;
              UpstreamUnavailableError raised after the single attempt
      RED-BY-DESIGN: this test should be GREEN when the router is built (it asserts
      the ABSENCE of stream fallback — which is the intended behavior). Marked
      GREEN-BY-DESIGN in the plan; it will pass even before build if the router simply
      delegates stream to upstream.stream() with first-candidate rewrite.
</test_plan>

Tests live in: `apps/gateway/tests/model_fallbacks/` · `apps/gateway/tests/model_fallbacks/conftest.py` · `apps/gateway/tests/model_fallbacks/test_model_fallbacks.py`

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): served-model billing — the use case MUST unpack the 3-tuple
returned by router.complete() as (status, body, served_model_id) and pass served_model_id (the
3rd element) to _fire_record_with_raw(), NEVER the alias string and NEVER body["model"] (which
may differ from the catalog candidate id due to OpenRouter format variants). Budget/rate-limit
checks run against the alias at entry (pre-resolution); the recorder fires with served_model_id.
The single-bill invariant is preserved by calling router.complete() exactly once from the use case.

Code lives in:
  - `apps/gateway/src/gateway/proxy/application/fallback_router.py` (new: FallbackModelRouter)
  - `apps/gateway/src/gateway/proxy/domain/ports.py` (additive: ModelHealthGate protocol)
  - `apps/gateway/src/gateway/core/config.py` (new: model_groups field + validators)
  - `apps/gateway/src/gateway/main.py` (wiring: app.state.model_router)
  - `apps/gateway/src/gateway/proxy/application/use_cases.py` (route through model_router,
    update model_id to served candidate for billing)

Constraints: do NOT change any test or the contract; allow-list packages only; no Redis logic
in this task; FallbackModelRouter must not inherit from any infrastructure class.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 435 passed, 19 deselected (e2e); frozen tests/model_fallbacks 14/14 + new tests/model_fallbacks_wiring 6/6 + all prior suites green (2026-06-12)
- [x] coverage did not decrease — 81.39% vs 81.24% pre-build (floor 80)
- [x] no test or contract was altered during build — frozen tests/model_fallbacks untouched post-freeze; §3 untouched; pyproject format-exclude additions are the frozen-suite convention
- [x] concurrency / timing safe — the router holds no per-request mutable state (loop variables are coroutine-local); served_model_id returned by value, no shared side-channel (A1 rationale); per-request upstream override avoids construction-time capture races
- [x] no exposed secrets / injection / new deps — structlog fall-through WARNINGs carry alias/from_model/attempt only; no payload or key material; zero new dependencies; alias strings validated at startup (collision/empty/cap)
- [x] layering follows CONVENTIONS.md — router is application-layer depending only on domain protocols (CompletionUpstream, ModelHealthGate); no infrastructure inheritance; config in core; counter on per-app MetricsRegistry
- [x] reviewed — orchestrator line-reviewed every diff under delegated auto mode (Tin Dang); review fixes applied: gate try/except shields removed (contract: gate is fail-open, no router shielding), contract-exact counter labels (incl. fell_through on gate-skips), dead except clause removed, public model_groups property, missing §3 span event implemented (additive OtelSpan.fallback)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — main.py constructs app.state.model_router with settings.model_groups, health_gate=None, metrics_registry; tests/model_fallbacks_wiring (6 tests) pins existence, groups, gate default, counter registration, and the upstream-override seam
- [x] DEAD-CODE — stream() exercised by F11 + the stream use-case path; health_gate=None exercised by F8 act-b and every plain-path test; candidates_for/model_groups used by use-case span attribution and governance; no unused symbols (ruff+mypy clean)
- [x] SEMANTIC — _fire_record_with_raw receives served_model_id (3-tuple 3rd element) at the single success-path call site; F7 green pins served==model-B after fallback; code review confirms no other ledger write path touches the alias

### GATE RECORD
Outcome: PASS (auto-resolved — complete evidence, no security finding, no concurrency/architecture residue)
Dispositions:
  - Builder agent died on session limit at ~95%; orchestrator completed mechanical lint/format
    residue and applied 5 review fixes (recorded above) — all verified by the authoritative re-run.
  - OtelSpan gained the additive `fallback` field (default False) — additive-field evolution
    precedent (cached/guardrail_blocked); all prior span shapes byte-identical; frozen
    observability suite 16/16 green.
  - Sibling DRAFT red suite tests/cooldown_circuit excluded from this gate's pytest run
    (its build is the next task; it gates itself).
Evidence: 435 passed / 19 deselected, coverage 81.39% (floor 80), ruff+format+mypy-strict+
allowlists EXIT=0 (2026-06-12).
Reviewed by: Tin Dang (delegated auto mode, orchestrator line review) · date: 2026-06-12

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
  - `gateway_model_fallbacks_total{outcome="exhausted"}` — spike means the group's candidates are all degraded
  - `gateway_model_fallbacks_total{outcome="fell_through"}` — nonzero means primary candidates are failing
  - P99 latency for requests to alias models (vs plain model ids) — excess latency = primary candidate degraded

Spec delta for the next loop:
  - cooldown-circuit task wires `ModelHealthGate` with Redis-backed TTL; the `health_gate` seam
    is already prepared here (app.state.model_router accepts it at construction; the build task
    upgrades the wiring when cooldown-circuit is merged).
  - If cache-key-uses-alias is revisited: change `build_cache_key()` call to use the
    first-available candidate id; adds a cache-warm-up round-trip per new alias resolution.

### Competency deltas
  - [SDD · open] Served-model billing required surfacing the concrete model id from the response
    body rather than passing the router's input — evidence: F7 scenario; response_body["model"]
    is the only authoritative served-model signal at the use-case boundary.
  - [TDD · open] GREEN-BY-DESIGN tests (F11) are a valid pattern for "absence of behavior" —
    marking them explicitly in the plan prevents confusion at red-phase verification.
  - [ADD · open] Parallel tasks sharing a protocol definition (ModelHealthGate) require the
    owning task to define the frozen interface before the consuming task builds against it.
