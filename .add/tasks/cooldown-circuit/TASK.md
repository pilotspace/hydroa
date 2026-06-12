# TASK: Per-model cooldown circuit breaker in Redis with half-open probe

slug: cooldown-circuit · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-model distributed cooldown circuit breaker backed by Redis with half-open probe semantics

Framings weighed:
  - **Redis-TTL per-model gate (chosen)**: use Redis key TTL as the authoritative cooldown clock across replicas; no in-process state; a single Redis unavailability drains gracefully (fail-OPEN). Additive default (threshold=0 = disabled) preserves v5 routing behavior until explicitly configured.
  - **In-process per-model breaker (rejected)**: would duplicate the existing global `CircuitBreaker` logic but at per-model granularity with no cross-replica coordination. Replicas would independently cool down the same model, creating split-brain: one replica serves while another blocks. Not suitable for a multi-replica gateway.
  - **Centralized health-state DB table (rejected)**: durable but synchronous DB writes on every completion path would add P99 latency risk and create a new SPOF. TTL-based Redis ephemeral state is the milestone-mandated approach (§Shared decisions).

Must:
<must>
  - `RedisCooldownGate` MUST implement the `ModelHealthGate` protocol: `async def is_available(self, model_id: str) -> bool` returning True when the model is not cooled down, False otherwise.
  - `RedisCooldownGate` MUST expose `async def record_failure(self, model_id: str) -> None` (called on retry-exhausted `UpstreamUnavailableError` for that candidate) and `async def record_success(self, model_id: str) -> None` (called on a served completion). The CALLER of record_* in production is `FallbackModelRouter` (per candidate attempt on the alias path), NOT the use case.
  - When `cooldown_failure_threshold == 0` (default), the gate MUST be disabled: `is_available` always returns True, `record_failure` and `record_success` are no-ops, and ZERO Redis commands must be issued.
  - When `cooldown_failure_threshold > 0`, `record_failure` MUST INCR the failure counter key `gateway:cooldown:fails:{model_id}`, set its EXPIRE to `cooldown_window_s` on the first increment (SET NX EXPIRE pattern), and trip the cooldown when the counter reaches the threshold: SET `gateway:cooldown:open:{model_id}` with TTL `cooldown_ttl_s`, SET `gateway:cooldown:half:{model_id}` "1" EX (2 * cooldown_ttl_s), DEL the counter key, emit `gateway_cooldown_transitions_total{model, transition="tripped"}`.
  - `record_success` MUST DEL `gateway:cooldown:fails:{model_id}`, `gateway:cooldown:probe:{model_id}`, AND `gateway:cooldown:half:{model_id}` (full close). Emit `transition="closed"` ONLY when the half marker was actually present (i.e., a probe/half-open state was being cleared). Plain successes on a CLOSED model (no half marker) emit no transition.
  - `is_available` MUST evaluate state in authoritative order:
      1. threshold == 0 → True (zero Redis commands).
      2. open key present → False (OPEN state).
      3. half marker present (`gateway:cooldown:half:{model_id}`) → HALF_OPEN: attempt `SET gateway:cooldown:probe:{model_id} 1 NX EX cooldown_ttl_s`; if SET NX succeeds return True + emit `transition="probe"`; otherwise return False.
      4. otherwise (no open key, no half marker) → CLOSED → True, NO probe machinery, NO Redis writes (read-only check).
  - The half marker `gateway:cooldown:half:{model_id}` is SET "1" EX (2 * cooldown_ttl_s) at TRIP time (alongside the open key) and REFRESHED (re-SET with the same TTL) on re-trip. It outlives the open key by cooldown_ttl_s, defining the half-open window. When the half marker expires with no traffic the model silently returns to CLOSED — no probe is needed without evidence of a caller.
  - Concurrent callers in HALF_OPEN that find the probe token already claimed MUST return False until the probe resolves. (The probe-race note in Assumptions applies only within the half-open window, not to CLOSED models.)
  - A probe that calls `record_failure` MUST immediately re-trip: SET `gateway:cooldown:open:{model_id}` with full TTL, REFRESH `gateway:cooldown:half:{model_id}` EX (2 * cooldown_ttl_s), DEL probe key, emit `transition="reopened"`. No threshold accumulation during a probe failure.
  - A probe that calls `record_success` MUST fully close: DEL probe key + fails key + half marker, emit `transition="closed"`; subsequent `is_available` returns True for all callers.
  - Fail-OPEN on ANY Redis error in `is_available`, `record_failure`, or `record_success`: log a WARNING via structlog (no Redis key string, no payload, no credential material) and behave as if available / no-op. Resilience MUST NOT amplify an outage.
  - Observability: `gateway_cooldown_transitions_total{model, transition}` counter; transition ∈ {tripped, probe, closed, reopened}. Span event on trip and close. All labels secrets-free.
  - Settings (GATEWAY_ prefix; defaults preserve v5 = feature OFF):
    - `cooldown_failure_threshold: int = Field(default=0, ge=0, le=100)` — 0 = disabled.
    - `cooldown_ttl_s: int = Field(default=60, ge=1, le=3600)` — how long cooldown lasts.
    - `cooldown_window_s: int = Field(default=60, ge=1, le=3600)` — failure counter expiry window.
  - Wiring: `RedisCooldownGate` MUST be constructed in `create_app` ONLY when `cooldown_failure_threshold > 0`, stored as `app.state.cooldown_gate`; when disabled, `app.state.cooldown_gate = None`. A paired production-wiring regression test is required (foundation v6 rule).
  - Placement: `gateway/proxy/infrastructure/redis_cooldown_gate.py` (routing-infrastructure layer, co-located with other Redis infrastructure adapters).
</must>

Reject:
<reject>
  - threshold=101 → ValidationError at Settings construction — "COOLDOWN_THRESHOLD_OUT_OF_RANGE"
  - cooldown_ttl_s=0 → ValidationError at Settings construction — "COOLDOWN_TTL_OUT_OF_RANGE"
  - cooldown_window_s=0 → ValidationError at Settings construction — "COOLDOWN_WINDOW_OUT_OF_RANGE"
  - cooldown_failure_threshold < 0 → ValidationError at Settings construction — "COOLDOWN_THRESHOLD_OUT_OF_RANGE"
  - inspect.signature / hasattr dispatch for protocol — "TYPED_EXTRAS_NO_DISPATCH" (explicit protocols only per typed-extras rule)
  - Redis key strings, payload content, or credential material in log fields — "NO_KEY_MATERIAL_IN_LOGS" (model_id MAY appear in log fields as it is a public catalog id already present in metrics/spans)
</reject>

After:
<after>
  - A model that has accumulated `cooldown_failure_threshold` consecutive failures: `is_available` returns False for all callers until TTL expires; the probe key pattern governs single-probe recovery.
  - A fully-closed model (after probe success): `is_available` returns True; no cooldown or probe keys exist; failure counter is zero.
  - Redis unavailability at any point: routing continues normally as if all models are available; a structlog WARNING is emitted (once per incident window — implementation may throttle).
  - With default settings (threshold=0): behavior is byte-identical to v5 — no Redis traffic, gate is logically absent.
  - The Prometheus counter `gateway_cooldown_transitions_total` reflects cumulative transition events per model across the process lifetime.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE: Concurrent probe race under tight TTL — if the cooldown TTL expires and N callers check `is_available` between the probe token SET NX and its propagation through Redis, all N callers execute `SET NX` concurrently. Redis SET NX is atomic, so exactly one succeeds. HOWEVER: if the probe token TTL (`cooldown_ttl_s`) is very short AND the probe request itself is slow, the probe token may expire before `record_success/failure` is called, allowing a second probe to be claimed while the first is in flight. This creates a brief window where two concurrent probes can both run. The contract accepts this race as safe (both probe outcomes converge to the correct state — a second probe success simply DELs already-DELed keys; a second probe failure re-trips which is also correct) but it is NOT a strict single-probe guarantee under clock-skew or very short TTLs. Cost: in high-concurrency + very short TTL configurations, "exactly one probe" becomes "at most a few probes" — acceptable but worth flagging. A future hardening: use a longer probe token TTL (e.g. 2× cooldown_ttl_s).
  ⚠ SECOND-LOWEST: The probe key TTL is set to `cooldown_ttl_s` (same as the cooldown flag TTL). This means a probe token that is never resolved (e.g. the probe request hangs indefinitely) will self-expire after `cooldown_ttl_s` seconds, unblocking the next probe. This is the intended self-healing behavior but relies on the probe request completing within `cooldown_ttl_s`. If a probe request takes longer than `cooldown_ttl_s` (e.g. > 60s for the default), a second probe fires before the first resolves. The cost is benign (same convergence argument above) but confirms the TTL-as-safety-net design.
  - [x] Redis INCR + EXPIRE is not atomic in this implementation — INCR then conditional EXPIRE is used (not GETSET or Lua). Race: two concurrent record_failure calls may both INCR then both EXPIRE, or one INCR + no EXPIRE. The fix: use `EXPIRE key window_s XX` (only if exists) after INCR to avoid resetting an already-running window. Alternatively, use EXPIRE NX (only if not set). The contract mandates EXPIRE on first increment (SET NX EXPIRE pattern). Implementation note: after INCR, call `EXPIRE key window_s NX` so only the first caller sets the expiry. This is atomically safe with redis.asyncio single-connection semantics. Flag this as an implementation detail to verify in BUILD.
  - [x] The `ModelHealthGate` protocol lives in the model-fallbacks parallel task (not yet in source). This task defines its own duck-typed version in the contract and tests against the adapter class directly. No import from model-fallbacks code.
  - [x] Tenant-agnostic key space (per upstream model id, not per tenant) is the milestone decision. A tenant-specific prefix is explicitly out of scope for v6.
  - [x] The counter `gateway_cooldown_transitions_total` is registered in the per-app MetricsRegistry (same pattern as upstream_retries_total). NOT persisted across restarts.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: CC1 — consecutive failures trip cooldown; model becomes unavailable
  Given cooldown_failure_threshold=3, cooldown_ttl_s=60, cooldown_window_s=60
  And a RedisCooldownGate with fake Redis
  When record_failure is called 3 times for model_id "openai/gpt-4o"
  Then is_available("openai/gpt-4o") returns False
  And the cooldown open key exists in fake Redis
  And the transitions counter shows tripped=1 for model "openai/gpt-4o"

Scenario: CC2 — record_success clears failure counter; model never trips after success
  Given cooldown_failure_threshold=3, a gate with fake Redis
  When record_failure is called 2 times then record_success once then record_failure once more
  Then is_available returns True (third failure was after counter cleared; threshold not reached)
  And the cooldown open key does NOT exist in fake Redis

Scenario: CC3 — threshold=0 (default) gate always returns True; zero Redis commands
  Given cooldown_failure_threshold=0 (disabled gate)
  And a RedisCooldownGate backed by a fake Redis with command logging
  When is_available is called 10 times and record_failure is called 5 times
  Then is_available returns True every time
  And the fake Redis command log is empty (zero Redis commands issued)

Scenario: CC4 — half-open window: first caller claims probe token; second gets False
  Given a gate where the cooldown open key has expired (not present in fake Redis)
  And the half marker key (gateway:cooldown:half:{model_id}) IS present (half-open window active)
  And the probe key is not present
  When two concurrent is_available calls are made for the same model
  Then exactly one returns True (claimed probe token; transition="probe")
  And the other returns False (probe token already claimed)
  And the probe key exists in fake Redis

Scenario: CC5 — probe failure re-trips with full TTL; half marker refreshed; transitions shows reopened
  Given a model in half-open state (half marker present, probe token claimed, open key absent)
  When record_failure is called for that model
  Then the cooldown open key is SET with full cooldown_ttl_s TTL
  And the half marker is refreshed (re-SET with EX 2*cooldown_ttl_s)
  And the probe key is DELeted
  And transitions counter shows reopened=1

Scenario: CC6 — probe success fully closes; half marker DELeted; all subsequent callers see True
  Given a model in half-open state (half marker present, probe token claimed, open key absent)
  When record_success is called for that model
  Then the probe key is DELeted
  And the fails key is DELeted
  And the half marker key is DELeted
  And is_available returns True for any caller immediately after
  And transitions counter shows closed=1

Scenario: CC7 — Redis ConnectionError on every command: fail-OPEN; WARNING logged
  Given a fake Redis that raises ConnectionError on every command
  And cooldown_failure_threshold=3
  When is_available is called, then record_failure is called 5 times
  Then is_available always returns True (fail-OPEN)
  And record_failure is a no-op (no exception raised)
  And a structlog WARNING event was emitted

Scenario: CC8 — Settings validation rejects out-of-range values
  Given cooldown_failure_threshold=101 OR cooldown_ttl_s=0
  When Settings is constructed with these values
  Then pydantic ValidationError is raised
  And the error references the invalid field

Scenario: CC9 (GREEN-BY-DESIGN) — two model ids cool independently
  Given cooldown_failure_threshold=2
  And two model ids: "openai/gpt-4o" and "anthropic/claude-3-5-sonnet"
  When record_failure is called 2 times for "openai/gpt-4o"
  And record_failure is called 1 time for "anthropic/claude-3-5-sonnet"
  Then is_available("openai/gpt-4o") returns False (tripped)
  And is_available("anthropic/claude-3-5-sonnet") returns True (below threshold)
  And the open key for "openai/gpt-4o" exists
  And the open key for "anthropic/claude-3-5-sonnet" does NOT exist
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LOWEST-CONFIDENCE FLAGS AT DRAFT
  ⚠ [contract] Concurrent probe race under tight TTL — SET NX is atomic so only
    one caller claims the probe token per expiry event, but if the probe request
    runs longer than cooldown_ttl_s the token self-expires and a second probe fires.
    The convergence argument (both probe outcomes are safe) makes this acceptable,
    but strict "exactly one probe" is not guaranteed. Cost: at most a brief
    thundering-herd of probe requests in adversarial timing (very short TTL + slow
    probes). Mitigations: keep cooldown_ttl_s >> probe request duration; a future
    hardening may use a separate probe_ttl_s knob larger than cooldown_ttl_s.
    Tagged [contract] because the state table below reflects the single-probe intent
    that the implementation approximates but cannot guarantee under all clock skews.

INTERNAL SEAM (not an HTTP endpoint)
  ModelHealthGate protocol (duck-typed; the model-fallbacks task owns the authoritative
  definition — this contract fixes the shape that RedisCooldownGate MUST satisfy):

    async def is_available(self, model_id: str) -> bool
      — returns True if the model may receive a request; False if cooled down
    async def record_failure(self, model_id: str) -> None
      — called on UpstreamUnavailableError (retry-exhausted) for this candidate
    async def record_success(self, model_id: str) -> None
      — called on a served completion for this model

  Caller: record_failure and record_success are called by FallbackModelRouter (per
  candidate attempt on the alias path), NOT by the use-case layer. The gate sees
  exactly one record_failure per retry-exhausted candidate and one record_success per
  candidate that returned any (status, body) — including 4xx passthrough, since a 4xx
  response proves upstream health.

REDIS KEY SHAPES (FROZEN)
  gateway:cooldown:fails:{model_id}    — INCR counter; EXPIRE cooldown_window_s (NX); DEL on success
  gateway:cooldown:open:{model_id}     — SET "1" EX cooldown_ttl_s on trip; DEL on probe+success
  gateway:cooldown:half:{model_id}     — SET "1" EX (2*cooldown_ttl_s) on trip; REFRESH on re-trip;
                                         DEL on probe+success; defines the half-open window
  gateway:cooldown:probe:{model_id}    — SET "1" NX EX cooldown_ttl_s on first caller in half-open window

  Namespace: tenant-agnostic (per upstream model id only — milestone decision)
  model_id: public catalog id; MAY appear in log fields; MUST NOT appear as a Redis key string in logs

SETTINGS (gateway/core/config.py additions — GATEWAY_ prefix)
  cooldown_failure_threshold: int = Field(default=0, ge=0, le=100)
    — 0 = disabled (gate always available, zero Redis traffic); v5-off default
  cooldown_ttl_s:             int = Field(default=60, ge=1, le=3600)
    — seconds the open flag lives; also probe token TTL
  cooldown_window_s:          int = Field(default=60, ge=1, le=3600)
    — failure counter expiry window (sliding; NX-set on first INCR)

  Validation errors:
    cooldown_failure_threshold > 100 or < 0 → ValidationError "COOLDOWN_THRESHOLD_OUT_OF_RANGE"
    cooldown_ttl_s < 1 or > 3600            → ValidationError "COOLDOWN_TTL_OUT_OF_RANGE"
    cooldown_window_s < 1 or > 3600         → ValidationError "COOLDOWN_WINDOW_OUT_OF_RANGE"

HALF-OPEN STATE MACHINE (authoritative)

  State name      | open key present | half marker present | probe key present | is_available result
  ────────────────────────────────────────────────────────────────────────────────────────────────────
  CLOSED          |       no         |         no          |        no         | True — read-only; NO Redis writes
  OPEN (cooled)   |      yes         |        yes          |        no         | False (immediately)
  HALF_OPEN       |       no         |        yes          |    no (first)     | True — SET probe NX succeeds → emit probe
  HALF_OPEN       |       no         |        yes          |   yes (subseq)    | False — probe token claimed by another caller
  FULLY CLOSED    |       no         |         no          |        no         | True (after record_success DEL probe+fails+half)

  Note: CLOSED and FULLY CLOSED are operationally identical (no keys present); they are distinguished
  conceptually only (CLOSED = never tripped or counter reset; FULLY CLOSED = returned from probe).
  In both cases is_available is a read-only True with zero Redis writes.

  Transition table:
  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐
  │ From         │ Event            │ To           │ Side-effects                               │
  ├─────────────────────────────────────────────────────────────────────────────────────────────┤
  │ CLOSED       │ record_failure   │ CLOSED       │ INCR fails; EXPIRE NX                      │
  │ CLOSED       │ record_failure   │ OPEN         │ (if count >= threshold)                    │
  │              │                  │              │ SET open EX ttl_s;                         │
  │              │                  │              │ SET half EX 2*ttl_s;                       │
  │              │                  │              │ DEL fails; metric trip                     │
  │ CLOSED       │ record_success   │ CLOSED       │ DEL fails, probe (idempotent); no metric   │
  │ OPEN         │ is_available     │ OPEN         │ return False                               │
  │ OPEN(expired)│ is_available     │ HALF_OPEN    │ half marker present:                       │
  │              │  (1st caller)    │              │ SET probe NX EX ttl_s;                     │
  │              │                  │              │ return True; metric probe                  │
  │ OPEN(expired)│ is_available     │ HALF_OPEN    │ half marker present, probe already set;    │
  │              │  (2nd+ caller)   │              │ return False                               │
  │ HALF_OPEN    │ record_success   │ CLOSED       │ DEL probe, fails, half;                    │
  │              │                  │              │ metric closed                              │
  │ HALF_OPEN    │ record_failure   │ OPEN         │ SET open EX ttl_s;                         │
  │              │                  │              │ REFRESH half EX 2*ttl_s;                  │
  │              │                  │              │ DEL probe; metric reopened                 │
  └─────────────────────────────────────────────────────────────────────────────────────────────┘

INCR + EXPIRE ATOMICITY NOTE
  After INCR, use `EXPIRE key window_s NX` (set expiry only if NOT already set)
  so concurrent record_failure calls do not reset a running window. This is the
  "NX-set on first INCR" pattern. Not a Lua script — acceptable; two-command
  round-trip is safe because NX prevents double-reset.

OBSERVABILITY (FROZEN)
  Prometheus counter: gateway_cooldown_transitions_total{model, transition}
    model      = model_id label (the upstream model id string — public catalog id, NOT a secret)
    transition ∈ {tripped, probe, closed, reopened}
  Span event: emitted on transition ∈ {tripped, closed} (structlog event)
  Structlog WARNING on Redis error: message="cooldown_gate_redis_error"
    MAY include model_id field (public catalog id already present in metrics/spans)
    MUST NOT include Redis key string, payload content, or any credential material

WIRING (gateway/main.py — create_app)
  if settings.cooldown_failure_threshold > 0:
      app.state.cooldown_gate = RedisCooldownGate(
          redis=redis_client,
          metrics_registry=app.state.metrics_registry,
          threshold=settings.cooldown_failure_threshold,
          ttl_s=settings.cooldown_ttl_s,
          window_s=settings.cooldown_window_s,
      )
  else:
      app.state.cooldown_gate = None

  Wiring order: model-fallbacks task builds first with health_gate=None; THIS task's
  build updates main.py to pass app.state.cooldown_gate into the FallbackModelRouter
  construction when threshold > 0. Final integration is owned by this task.

ADAPTER PLACEMENT
  apps/gateway/src/gateway/proxy/infrastructure/redis_cooldown_gate.py

PRODUCTION-WIRING REGRESSION TEST
  tests/cooldown_circuit_wiring/ (paired suite; foundation v6 rule)
  — asserts app.state.cooldown_gate is None at default settings (threshold=0)
  — asserts app.state.cooldown_gate is a RedisCooldownGate at threshold>0
  (wiring suite is separate from the unit suite; the unit suite is what runs red now)

SINGLE-BILL INVARIANT (preserved)
  record_failure/record_success are called from FallbackModelRouter (per candidate
  attempt on the alias path), NOT from inside upstream.complete()'s retry loop and
  NOT from the use-case layer. The cooldown gate sees exactly one record_failure per
  retry-exhausted candidate (UpstreamUnavailableError) and one record_success per
  candidate that returned any (status, body) including 4xx passthrough. One outcome
  per candidate per request. The ledger write path is orthogonal.

Amendment history (orchestrator freeze review, 2026-06-12)
  B1 — CRITICAL: Added gateway:cooldown:half:{model_id} marker key (SET EX 2*ttl_s at
       trip, REFRESH on re-trip, DEL on close) to distinguish CLOSED from HALF_OPEN;
       rewrote is_available authoritative-order logic so CLOSED state is read-only True
       with zero Redis writes, eliminating the defect that throttled healthy traffic to
       ~1 request per TTL by claiming probe tokens for never-cooled models.
  B2 — Protocol/caller alignment: clarified that record_failure/record_success are
       called by FallbackModelRouter (not the use-case layer); added wiring-order note
       (model-fallbacks builds first with health_gate=None; this task wires the gate
       into FallbackModelRouter construction in main.py).
  B3 — Log-field correction: model_id is a public catalog id already present in
       metrics/spans; relaxed "NO_KEY_MATERIAL_IN_LOGS" to forbid only Redis key
       strings, payload content, and credential material — not model_id itself.
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] the concurrent-probe race under tight TTL — ACCEPTED:
SET NX guarantees one probe per expiry event; a probe outliving its token TTL admits a
second probe, but both outcomes converge to a correct state (re-trip or close). Operators
keep cooldown_ttl_s well above probe latency. Amendments B1–B3 applied at freeze (see
Amendment history in the contract fence).
<!-- The freeze IS the one approval — the lowest-confidence flag is the concurrent-probe
     race at the top. Approved → Status: FROZEN @ vN — approved by <name>.
     Changing a frozen contract = change request back to SPECIFY. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% of state-machine paths + Settings validation + fail-OPEN paths

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_cc1_three_failures_trip_cooldown:
      arrange: FakeRedis + gate(threshold=3, ttl_s=60, window_s=60); CommandLog
      act: await record_failure×3; await is_available
      assert: is_available returns False; open key exists in fake Redis; transitions "tripped" count == 1
      RED reason: RedisCooldownGate does not exist → ImportError

  - test_cc2_success_clears_counter_prevents_trip:
      arrange: FakeRedis; gate(threshold=3); record_failure×2 then record_success then record_failure
      act: await is_available
      assert: is_available returns True; open key absent
      RED reason: ImportError on RedisCooldownGate

  - test_cc3_threshold_zero_no_redis_traffic:
      arrange: CommandLoggingFakeRedis; gate(threshold=0) [disabled]
      act: is_available×10 + record_failure×5
      assert: all is_available True; command log empty (len == 0)
      RED reason: ImportError on RedisCooldownGate

  - test_cc4_half_open_first_caller_gets_probe_second_gets_false:
      arrange: FakeRedis where open key absent (TTL expired), half marker key SET (half-open window active), probe key absent
      act: two concurrent is_available calls (asyncio.gather)
      assert: one True + one False; probe key present in fake Redis; transitions "probe" count == 1
      RED reason: ImportError on RedisCooldownGate

  - test_cc5_probe_failure_retrips_with_full_ttl:
      arrange: FakeRedis in half-open state (half marker SET, probe key set, open key absent)
      act: await record_failure
      assert: open key SET with TTL == cooldown_ttl_s; half marker refreshed (still present); probe key DELeted; transitions "reopened" count == 1
      RED reason: ImportError on RedisCooldownGate

  - test_cc6_probe_success_fully_closes:
      arrange: FakeRedis in half-open state (half marker SET, probe key set, open key absent)
      act: await record_success; then is_available for any caller
      assert: probe key absent; fails key absent; half marker absent; is_available True; transitions "closed" count == 1
      RED reason: ImportError on RedisCooldownGate

  - test_cc7_redis_error_fail_open:
      arrange: ErrorFakeRedis (raises ConnectionError on every command); gate(threshold=3)
      act: 5 × record_failure; 3 × is_available
      assert: is_available always True; no exception raised; WARNING log captured once
      RED reason: ImportError on RedisCooldownGate

  - test_cc8_settings_validation_threshold_101:
      arrange: Settings construction with cooldown_failure_threshold=101
      act: Settings(cooldown_failure_threshold=101)
      assert: raises pydantic.ValidationError
      RED reason: Settings fields do not exist yet → ValidationError on unknown field OR passes with wrong behavior

  - test_cc8b_settings_validation_ttl_zero:
      arrange: Settings construction with cooldown_ttl_s=0
      act: Settings(cooldown_ttl_s=0)
      assert: raises pydantic.ValidationError
      RED reason: Settings fields do not exist yet

  - test_cc9_two_models_cool_independently:
      arrange: FakeRedis; gate(threshold=2)
      act: record_failure×2 for "openai/gpt-4o"; record_failure×1 for "anthropic/claude-3-5-sonnet"
      assert: is_available("openai/gpt-4o") == False; is_available("anthropic/claude-3-5-sonnet") == True
      GREEN-BY-DESIGN (passes once implementation exists; red only due to ImportError)

  - test_cc10_closed_model_no_probe_no_writes (B1 regression):
      arrange: FakeRedis with command logging; gate(threshold=3); NO keys set (model has no history)
      act: two concurrent is_available calls (asyncio.gather) for MODEL_A
      assert: both return True; probe key NOT created; half marker NOT created;
              zero SET commands appear in command_log (read-only check)
      RED reason: ImportError on RedisCooldownGate
      Note: this is the regression that catches the original B1 defect — a CLOSED model
            with no keys must never enter probe machinery; if the gate incorrectly treats
            "no open key" as HALF_OPEN it would claim a probe token and block one caller.
</test_plan>

Tests live in: `apps/gateway/tests/cooldown_circuit/` · `apps/gateway/tests/cooldown_circuit/conftest.py` · `apps/gateway/tests/cooldown_circuit/test_cooldown_circuit.py`

Expected red/green at spec phase (before BUILD):
  - C1–C7, C8, C8b: RED for ImportError (RedisCooldownGate class absent) or missing Settings fields
  - C9: RED for ImportError (also; GREEN-BY-DESIGN label means it passes once source exists)
  - C10: RED for ImportError (RedisCooldownGate absent) — B1 regression; must stay red until BUILD
  All failures are for the RIGHT reason — the module/fields simply don't exist yet.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the probe SET NX must use `cooldown_ttl_s` as the TTL (not zero); Redis errors in ANY path must be caught at the outermost await and logged before returning the fail-OPEN default. The counter INCR + EXPIRE NX must always use NX on EXPIRE to avoid resetting a running window.

Code lives in:
  - `apps/gateway/src/gateway/proxy/infrastructure/redis_cooldown_gate.py` (new file)
  - `apps/gateway/src/gateway/core/config.py` (three new Settings fields)
  - `apps/gateway/src/gateway/main.py` (wiring in create_app)
  - `apps/gateway/src/gateway/observability/metrics.py` (new cooldown_transitions_total counter)

Constraints: do NOT change any test or the contract; allow-list packages only (redis.asyncio is already a listed dependency); ask if unclear.

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
- [ ] WIRING (code) — `app.state.cooldown_gate` is None at threshold=0 and a RedisCooldownGate at threshold>0; paired regression test in tests/cooldown_circuit_wiring/ confirms this
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced; record_success path in CLOSED state (DEL is idempotent — no key to delete)
- [ ] SEMANTIC (prose) — half-open state table in §3 read in full; probe NX atomicity argument confirmed; EXPIRE NX pattern verified in implementation

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
  - `gateway_cooldown_transitions_total{transition="tripped"}` — spike means upstream models are failing; cross-reference with upstream_retries_total{outcome="exhausted"}
  - `gateway_cooldown_transitions_total{transition="probe"}` — probe rate; should match trip rate over a recovery period
  - `gateway_cooldown_transitions_total{transition="reopened"}` — probe failures; high rate means upstream is still degraded after cooldown TTL
  - `gateway_cooldown_transitions_total{transition="closed"}` — recovery confirmations
  - Absence of any "closed" transitions after a "tripped" wave → model is stuck in permanent cooldown (TTL may be too long)

Spec delta for the next loop:
  - If the probe TTL is too short relative to probe request duration, introduce a separate `cooldown_probe_ttl_s` knob (currently inherited from `cooldown_ttl_s`).
  - If tenant-specific cooldown isolation becomes required, introduce a `gateway:cooldown:{tenant_id}:fails:{model_id}` key variant; this is explicitly out of scope for v6.
  - If the INCR+EXPIRE NX pattern shows race conditions in production (counter never expires), consider a Lua script for atomic INCR+EXPIRE.

### Competency deltas
- [SDD · open] Half-open probe semantics in a distributed, TTL-keyed system require explicit state-table documentation — the "HALF_OPEN" state in the in-process breaker has no direct Redis analogue; evidence: needed a 5-row state table to express what the in-process breaker does with 3 enum values.
- [TDD · open] Fake Redis for concurrent SET NX tests must serialize asyncio tasks carefully — asyncio.gather does not guarantee interleaving order; the fake must process commands atomically (single-threaded asyncio = no actual concurrency in fake) which means the NX test requires task ordering discipline (one task yields before the other checks); evidence: C4 design.
- [ADD · open] The concurrent-probe race is the canonical example of a [contract]-level flag that cannot be fully resolved by spec alone — it requires acceptance criteria on the TTL relationship (probe request duration < probe TTL); this should become a BUILD constraint, not just a §3 flag.
