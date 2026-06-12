# TASK: least-busy + latency routing strategies (Redis-backed, async order)

slug: balance-strategies · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Two LOAD-AWARE routing strategies that pick a model group's PRIMARY deployment
from live per-deployment metrics in Redis: `least-busy` (fewest in-flight requests) and
`latency` (lowest recent EWMA latency). Both build on routing-strategy's frozen
RoutingStrategy seam, supersede its SYNC `order()` to an ASYNC variant for the IO path
(per the routing-strategy §3 SUPERSESSION NOTE), and reuse the v1 rate-limit + v6 cooldown
Redis infrastructure (no new datastore). Default stays simple-shuffle; ordered/simple-shuffle
and the entire v6 chat path remain byte-identical (no load metrics, no new Redis IO).

Framings weighed:
  - **Additive async seam + a DeploymentLoadGate port; sync order() FROZEN-untouched (chosen)**:
    routing-strategy's `order()` is SYNC and is called synchronously in its FROZEN tests
    (test_rs3 `strat.order(...)[0]`) and on the sync stream() path — so its signature is
    immutable. Supersede ADDITIVELY: add an optional `async def aorder(alias, candidates,
    deployments) -> list[str]` capability. complete() (already `await`ed at use_cases.py:961)
    prefers `aorder` when the strategy exposes it, else uses sync `order()`. stream() (SYNC at
    use_cases.py:1237) always uses sync `order()`[0]; load-aware strategies implement sync
    `order()` as declared-order (→ ordered-primary on stream = the routing-strategy freeze
    flag's pre-approved bounded resolution). Live metrics live behind a NEW async
    `DeploymentLoadGate` port (mirrors the RedisCooldownGate shape: zero-IO fast path, fail-OPEN
    on any Redis error), wired ONLY for least-busy/latency. Minimal blast radius; v6 byte-identical.
  - **Change order() to async and rework stream() to an async generator (rejected)**: edits the
    frozen sync seam → breaks routing-strategy's frozen test_rs3/rs4/rs5 (they call order()
    sync) AND moves where stream exceptions surface at the INVIOLABLE use_cases.py:1237 call
    site → ERR_FROZEN_VIOLATION. The additive aorder() avoids both.
  - **Track in-flight/latency in process memory, not Redis (rejected)**: per-replica counters
    diverge across the fleet; least-busy must see GLOBAL in-flight to balance. Redis is the
    shared-state requirement the milestone already pinned.

Must:
<must>
  - A new async capability `AsyncRoutingStrategy` (Protocol): `async def aorder(alias: str,
    candidates: list[str], deployments: list[Deployment]) -> list[str]` returning a PERMUTATION
    of `candidates`. complete() consults `aorder` when the wired strategy is an
    AsyncRoutingStrategy (isinstance), else the frozen sync `order()`. The router's existing
    permutation guard (INVALID_ROUTING_ORDER) covers BOTH paths.
  - A new async port `DeploymentLoadGate` (None by default), mirroring RedisCooldownGate:
      `async def acquire(deployment_id) -> None`   # INCR in-flight + EXPIRE(ttl) refresh
      `async def release(deployment_id) -> None`   # DECR in-flight (read clamps negative→0)
      `async def in_flight(deployment_id) -> int`  # current in-flight (≥0)
      `async def record_latency(deployment_id, latency_ms: float) -> None`  # EWMA update
      `async def latency_ewma(deployment_id) -> float`  # recent EWMA (unseen → 0.0)
    Fail-OPEN: ANY Redis error in a read returns the neutral value (in_flight=0 / ewma=0.0)
    and is logged (deployment_id only, never key strings/secrets); acquire/release errors are
    swallowed. A None load_gate ⇒ zero Redis commands ⇒ v6 byte-identical.
  - `LeastBusyStrategy(load_gate)`: aorder picks the PRIMARY = the candidate with the fewest
    in_flight (ties broken by DECLARED order — stable); remaining candidates follow in declared
    order as the fallback tail (full permutation). Sync `order()` returns declared order
    (ordered fallback on the stream path).
  - `LatencyStrategy(load_gate)`: aorder picks the PRIMARY = the candidate with the lowest
    latency_ewma (unseen deployment ⇒ 0.0 ⇒ preferred, so a cold deployment is probed; ties →
    declared order); rest follow in declared order. Sync `order()` returns declared order.
  - In-flight LIFECYCLE in complete() — ONLY when a load_gate is wired: after the strategy
    selects a candidate, `await load_gate.acquire(candidate)` BEFORE the upstream call and
    `await load_gate.release(candidate)` in a `finally` on EVERY exit path (served, fallthrough
    to next, exhausted-raise, circuit-open re-raise) so the global in-flight counter never
    leaks; `await load_gate.record_latency(candidate, elapsed_ms)` after a candidate ANSWERS
    (any status). When load_gate is None: none of this runs (byte-identical).
  - GATEWAY_ROUTING_STRATEGY valid set extends to {ordered, simple-shuffle, least-busy, latency};
    build_strategy maps least-busy/latency to the load-aware strategies (load_gate injected);
    create_app constructs the DeploymentLoadGate (Redis-backed) ONLY when the configured
    strategy needs it, else load_gate stays None.
  - Billing/gate/fallback/cooldown semantics UNCHANGED: billing keys on the served candidate id;
    the v6 health gate still skips cooled candidates AFTER the strategy orders them; fallback
    counters still fire; cooldown still removes unhealthy deployments. Strategy selection happens
    FIRST (orders candidates), then the unchanged v6 loop runs over that order.
  - Determinism for tests: the DeploymentLoadGate is a constructor-injected port, so a fake
    gate with scripted in_flight/ewma values makes least-busy/latency fully assertable; ties use
    declared order so there is no RNG in either load-aware strategy.
</must>
Reject:
<reject>
  - GATEWAY_ROUTING_STRATEGY set to a value not in {ordered, simple-shuffle, least-busy, latency}
    -> "UNKNOWN_ROUTING_STRATEGY" (startup, fail-closed; extends the routing-strategy validator)
  - aorder() (or order()) returns a non-permutation (adds/drops/duplicates a candidate)
    -> "INVALID_ROUTING_ORDER" (the existing runtime guard, now covering the async path)
  - a DeploymentLoadGate Redis error -> NEVER a 500: reads fail-OPEN to the neutral metric
    (in_flight=0 / ewma=0.0) so selection degrades to declared/ordered, request still served
  - any change that alters the v6 chat path / ordered attempt order / gate / billing, edits a
    frozen v6 or routing-strategy test, or makes the frozen sync order()/stream() async
    -> "ERR_FROZEN_VIOLATION"
</reject>
After:
<after>
  - Under least-busy, a model alias with ≥2 deployments routes the PRIMARY to the deployment
    with the fewest live in-flight requests (not always candidates[0]); the rest remain as the
    v6 fallback tail; in-flight is acquired/released around every upstream attempt.
  - Under latency, the PRIMARY is the lowest-recent-EWMA-latency deployment; a never-seen
    deployment (ewma 0.0) is probed first; recorded latencies shift subsequent picks.
  - ordered/simple-shuffle and the whole v6 chat path stay byte-identical (no load_gate, zero
    new Redis IO; frozen model_fallbacks + routing_admin + proxy + routing_strategy suites green).
  - `DeploymentLoadGate` is the frozen per-deployment-metric seam that deployment-limits
    (per-deployment TPM/RPM skip) composes on next.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE: that the in-flight acquire/release lifecycle releases on EVERY exit path
    of complete()'s candidate loop (served return, UpstreamUnavailable fallthrough-continue,
    all-exhausted raise, CircuitOpenError/other re-raise) — a missed release leaks the global
    in-flight counter, biasing least-busy AWAY from a healthy deployment forever. Lowest
    confidence because the v6 loop has FOUR exit edges and a `try/finally` per attempt must cover
    all four without altering the v6 control flow / counter labels. Mitigation baked into the
    contract: the in-flight key carries an EXPIRE(in_flight_ttl_s) refreshed on acquire, so any
    leaked count self-heals via TTL, and reads clamp negative→0; least-busy is an OPTIMIZATION
    (fail-soft), never a correctness gate. If wrong cost: temporary skew in primary selection
    that expires within in_flight_ttl_s — never a wrong/failed response, never a chat regression.
  - [ ] treating an unseen deployment's latency EWMA as 0.0 (⇒ preferred/probed first) is the
    right cold-start policy (vs. a large default that DEPREFERS unknowns) — confirm at contract;
    cost if wrong: a cold deployment gets a brief traffic spike until its EWMA populates (bounded,
    self-correcting), not an outage. Chosen 0.0 so new deployments actually receive probe traffic.
  - [ ] the EWMA smoothing factor (α) and in_flight_ttl_s belong as Settings knobs with safe
    defaults (α≈0.3, ttl≈60s) rather than hard-coded constants — decide at contract; cost if
    wrong: a re-freeze to expose a knob, not a behavior bug.
  - [ ] least-busy/latency wiring the load_gate ONLY when selected (None otherwise) is sufficient
    to keep ordered/simple-shuffle byte-identical — confirmed by the v6 None-default pattern
    (RedisCooldownGate threshold==0 fast path is the precedent).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: BS1 — least-busy picks the fewest-in-flight deployment as primary
  Given GATEWAY_ROUTING_STRATEGY="least-busy", alias "fast"=[a,b,c] and a load_gate
    reporting in_flight a=5, b=1, c=3
  When a completion targets "fast" and b answers 200
  Then the router attempts b FIRST (served_model_id == b), not candidates[0] (a)
  And the remaining candidates stay as the fallback tail (a full permutation of [a,b,c])

Scenario: BS2 — least-busy ties break by declared order (stable, no RNG)
  Given "least-busy", alias "fast"=[a,b,c] and a load_gate reporting in_flight a=2,b=2,c=2
  When 50 completions target "fast"
  Then the primary is ALWAYS a (declared-first wins the tie) — deterministic, no RNG draw

Scenario: BS3 — least-busy acquires/releases in-flight around every attempt
  Given "least-busy", alias "fast"=[a,b] where the chosen primary a raises
    UpstreamUnavailableError then b answers 200
  When a completion targets "fast"
  Then load_gate.acquire then release is called for a (the failed attempt) AND for b,
    so in-flight is balanced (released on the fallthrough path, not leaked)
  And served_model_id == b (v6 fallback still runs over the strategy order)

Scenario: BS4 — least-busy releases in-flight even when all candidates exhaust (raise path)
  Given "least-busy", alias "fast"=[a,b] where both raise UpstreamUnavailableError
  When a completion targets "fast"
  Then the router raises UpstreamUnavailableError (v6 exhausted behavior unchanged)
  And load_gate.release was called for BOTH a and b (finally on the raise path — no leak)

Scenario: BS5 — latency picks the lowest-EWMA deployment as primary
  Given "latency", alias "fast"=[a,b] and a load_gate reporting ewma a=400ms, b=120ms
  When a completion targets "fast" and b answers 200
  Then the router attempts b FIRST (served == b); record_latency(b, elapsed) is called after

Scenario: BS6 — latency probes an unseen deployment first (ewma 0.0 preferred)
  Given "latency", alias "fast"=[a,b], a has ewma 250ms, b has never been seen (ewma 0.0)
  When a completion targets "fast"
  Then the never-seen b is selected primary (cold deployment gets probe traffic)

Scenario: BS7 — load_gate Redis error fails OPEN (degrades to declared order, never 500)
  Given "least-busy", alias "fast"=[a,b] and a load_gate whose in_flight() raises a Redis error
  When a completion targets "fast"
  Then the read fails OPEN (in_flight treated as 0 for all) → primary degrades to declared a
  And the request is served normally (no 500; the error is logged with deployment_id only)

Scenario: BS8 — ordered/simple-shuffle stay byte-identical (no load_gate, zero new Redis IO)
  Given GATEWAY_ROUTING_STRATEGY="ordered" (no load_gate wired) and alias "fast"=[a,b,c]
  When a completion and a stream target "fast"
  Then the router behaves EXACTLY as v6/routing-strategy (a first; gate/billing/counters
    unchanged) and load_gate is None (acquire/release/record_latency never called)

Scenario: BS9 — load-aware strategy on the stream path falls back to ordered primary
  Given "least-busy", alias "fast"=[a,b,c] (load-aware = async; stream is the sync path)
  When a streaming completion targets "fast"
  Then the stream rewrites model=a (declared-order primary, the pre-approved sync fallback)
  And no async aorder / load_gate read is attempted on the sync stream path

Scenario: BS10 — unknown strategy still rejected at startup
  Given GATEWAY_ROUTING_STRATEGY="weighted-round-robin" (not implemented)
  When the gateway boots
  Then startup raises ValidationError containing "UNKNOWN_ROUTING_STRATEGY" (fail-closed)
  And "least-busy" and "latency" are now ACCEPTED values (boot succeeds for them)

Scenario: BS11 — a load-aware strategy returning a non-permutation is rejected at runtime
  Given a (test) async strategy whose aorder() drops a candidate
  When the router routes an alias request via the async path
  Then the router raises an error referencing "INVALID_ROUTING_ORDER" (guard covers aorder)
  And it never serves a partial/incorrect candidate set

Scenario: BS12 — create_app wires the load_gate only for load-aware strategies
  Given Settings(routing_strategy="least-busy") vs Settings(routing_strategy="simple-shuffle")
  When create_app builds each
  Then least-busy → router has a DeploymentLoadGate-backed LeastBusyStrategy; simple-shuffle →
    load_gate is None and the strategy is the v8 SimpleShuffleStrategy (no Redis IO wired)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This task adds application-layer strategies + one infrastructure port. No HTTP endpoint of
its own; it plugs into the existing FallbackModelRouter and create_app wiring.

```
TYPES (gateway/proxy/application/routing_strategy.py — ADD to the existing module)
  @runtime_checkable
  class AsyncRoutingStrategy(Protocol):            # NEW capability; FROZEN sync order() untouched
      async def aorder(self, alias: str, candidates: list[str],
                       deployments: list[Deployment]) -> list[str]
      # Returns a PERMUTATION of `candidates` (attempt order) using live load metrics.
      # A strategy may implement BOTH order() (sync, stream path) and aorder() (async, complete).

  class LeastBusyStrategy:                         # implements RoutingStrategy + AsyncRoutingStrategy
      def __init__(self, load_gate: DeploymentLoadGate): ...
      def order(self, alias, candidates, deployments) -> list[str]:
          return list(candidates)                  # SYNC stream path → declared/ordered primary
      async def aorder(self, alias, candidates, deployments) -> list[str]:
          # primary = candidate with MIN in_flight; ties → declared order (stable).
          # counts read via load_gate.in_flight(c) for each candidate (fail-OPEN → 0).
          # return [primary, *rest-in-declared-order]  (full permutation)

  class LatencyStrategy:                           # implements RoutingStrategy + AsyncRoutingStrategy
      def __init__(self, load_gate: DeploymentLoadGate): ...
      def order(self, alias, candidates, deployments) -> list[str]:
          return list(candidates)                  # SYNC stream path → declared/ordered primary
      async def aorder(self, alias, candidates, deployments) -> list[str]:
          # primary = candidate with MIN latency_ewma; unseen ⇒ 0.0 (preferred); ties → declared.
          # return [primary, *rest-in-declared-order]  (full permutation)

  def build_strategy(name: str, load_gate: DeploymentLoadGate | None = None) -> RoutingStrategy:
      # "ordered"→OrderedStrategy ; "simple-shuffle"→SimpleShuffleStrategy (unchanged) ;
      # "least-busy"→LeastBusyStrategy(load_gate) ; "latency"→LatencyStrategy(load_gate).
      # ADDITIVE signature: the existing 1-arg call build_strategy("ordered") still works
      # (load_gate defaults None). least-busy/latency REQUIRE a non-None load_gate.

PORT (gateway/proxy/domain/ports.py — NEW @runtime_checkable Protocol)
  class DeploymentLoadGate(Protocol):
      async def acquire(self, deployment_id: str) -> None        # INCR in-flight + EXPIRE(ttl)
      async def release(self, deployment_id: str) -> None        # DECR in-flight
      async def in_flight(self, deployment_id: str) -> int       # ≥0 (read clamps negative→0)
      async def record_latency(self, deployment_id: str, latency_ms: float) -> None  # EWMA update
      async def latency_ewma(self, deployment_id: str) -> float  # recent EWMA; unseen → 0.0
  # All methods fail-OPEN: a read error returns the neutral value (0 / 0.0); acquire/release
  # errors are swallowed (logged with deployment_id only — never key strings or secrets).

INFRA (gateway/proxy/infrastructure/redis_load_gate.py — NEW, mirrors redis_cooldown_gate.py)
  class RedisDeploymentLoadGate:                   # implements DeploymentLoadGate
      def __init__(self, *, redis: Any, alpha: float, in_flight_ttl_s: int): ...
      # Keys (NEW prefixes, deployment_id only — never a secret):
      #   gateway:loadbal:inflight:{deployment_id}   INT   (acquire INCR + EXPIRE nx-refresh; release DECR)
      #   gateway:loadbal:ewma:{deployment_id}        FLOAT (record_latency: ewma = α·sample + (1-α)·prev)
      # in_flight(): GET → int(val or 0), clamp max(0, …). latency_ewma(): GET → float(val or 0.0).
      # EXPIRE on inflight key refreshed on every acquire (in_flight_ttl_s) so a leaked count self-heals.
      # Constructor does NOT connect to Redis (safe without lifespan), same as RedisCooldownGate.

CONFIG (gateway/core/config.py — Settings)
  routing_strategy valid set: {"ordered","simple-shuffle","least-busy","latency"}   (extend the set)
  env GATEWAY_LOADBAL_EWMA_ALPHA      : float = 0.3   (0 < α ≤ 1; else "INVALID_LOADBAL_ALPHA")
  env GATEWAY_LOADBAL_INFLIGHT_TTL_S  : int   = 60    (> 0; else "INVALID_LOADBAL_TTL")

ROUTER (gateway/proxy/application/fallback_router.py — FallbackModelRouter)
  __init__(... , load_gate: DeploymentLoadGate | None = None)   # additive; None ⇒ v6 byte-identical
  complete() alias path — selection becomes async-aware:
      if isinstance(self._strategy, AsyncRoutingStrategy):
          order = await self._strategy.aorder(alias, candidates, deployments)
      else:
          order = self._strategy.order(alias, candidates, deployments)   # frozen sync path
      <permutation guard unchanged → INVALID_ROUTING_ORDER>
      # then the UNCHANGED v6 fallback loop runs over `order`. When self._load_gate is not None,
      # wrap each attempt:  await load_gate.acquire(candidate)  /  try: <upstream> finally:
      #   await load_gate.release(candidate); and after a candidate ANSWERS:
      #   await load_gate.record_latency(candidate, elapsed_ms).  When load_gate is None: none
      #   of acquire/release/record runs → byte-identical to v6/routing-strategy.
  stream(): UNCHANGED — primary = self._strategy.order(model_id, candidates)[0]  (sync; for a
      load-aware strategy order() is declared-order ⇒ candidates[0], the pre-approved fallback).

WIRING (gateway/main.py create_app)
  load_gate = RedisDeploymentLoadGate(redis=..., alpha=settings.loadbal_ewma_alpha,
              in_flight_ttl_s=settings.loadbal_inflight_ttl_s)  IFF
              settings.routing_strategy in {"least-busy","latency"} else None
  strategy  = build_strategy(settings.routing_strategy, load_gate)
  FallbackModelRouter(... , strategy=strategy, load_gate=load_gate)
  The plain (non-alias) path and the model_router=None path are UNTOUCHED.

INVIOLABLE (byte-identical when routing_strategy ∈ {ordered, simple-shuffle}; load_gate None):
  - the v6 fallback loop body, gate calls, billing (served candidate id), fallback counters
  - the frozen sync RoutingStrategy.order() signature and routing-strategy test_rs1..rs8
  - the stream no-fallback-on-first-candidate boundary + the sync use_cases.py:1237 call site
  - use_cases.py complete()/stream() call sites (router signatures only gain additive kwargs)
```

GLOSSARY deltas (add at freeze): **DeploymentLoadGate** — async port exposing per-deployment
live load metrics (in-flight count, recent-latency EWMA) backed by Redis; the seam the
load-aware strategies and deployment-limits read. **least-busy** / **latency** — routing
strategies selecting the primary deployment by fewest-in-flight / lowest-EWMA-latency
respectively; both async (aorder), both degrade to declared order on the sync stream path and
on any Redis error (fail-OPEN).

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] the in-flight acquire/release LIFECYCLE must
release on ALL FOUR exit edges of complete()'s candidate loop (served-return, UpstreamUnavailable
fallthrough-continue, all-exhausted raise, CircuitOpenError/other re-raise) via a per-attempt
`try/finally`, WITHOUT altering the v6 control flow or the fallback-counter `to_model` labels.
This is the bundle's highest risk: a missed release leaks the global in-flight counter and biases
least-busy away from a healthy deployment. Mitigation frozen into the contract: the inflight key
carries EXPIRE(in_flight_ttl_s) refreshed on acquire (a leak self-heals within the TTL) and
in_flight() clamps negative→0; least-busy/latency are OPTIMIZATIONS (fail-soft), never a
correctness gate, so the worst case is bounded, self-correcting selection skew — never a wrong or
failed response and never a v6 chat regression. Cost if wrong: transient primary-selection skew
expiring within in_flight_ttl_s, fixed by tightening the finally placement — not a redesign.
Secondary flag: [spec] unseen-deployment latency EWMA = 0.0 ⇒ PREFERRED (cold deployments get
probe traffic). Chosen over a large default so new deployments actually receive traffic; if a
cold-start spike is undesirable in production it is a one-line policy change (default → +inf or a
seeded baseline), not a seam change — surfaced so a reviewer confirms the cold-start direction.

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on routing_strategy.py (load-aware strategies) + redis_load_gate.py +
the router async-selection / in-flight-lifecycle branches.
Plan (one test per scenario, asserting behavior not internals; fake DeploymentLoadGate with
scripted in_flight/ewma + a call-recording spy; reuse routing_strategy fakes _Upstream/_Gate):
<test_plan>
  - test_bs1_least_busy_picks_fewest_inflight: LeastBusyStrategy(fake gate in_flight a=5,b=1,c=3);
    router.complete(alias) → served==b; assert order primary==b, set==candidates
  - test_bs2_least_busy_tie_declared_order: fake gate all in_flight==2; await aorder 50x →
    primary ALWAYS a (declared-first); no RNG (deterministic)
  - test_bs3_least_busy_acquire_release_around_attempts: a fails→b 200; assert spy records
    acquire(a),release(a),acquire(b),release(b) (balanced); served==b
  - test_bs4_least_busy_release_on_exhausted_raise: a,b both raise; assert router raises
    UpstreamUnavailableError AND release called for both a and b (finally on raise path)
  - test_bs5_latency_picks_lowest_ewma: LatencyStrategy(fake ewma a=400,b=120); complete →
    served==b; assert record_latency(b, _) called after the answer
  - test_bs6_latency_probes_unseen_first: ewma a=250, b unseen(0.0); await aorder → primary==b
  - test_bs7_load_gate_redis_error_fails_open: fake in_flight() raises; complete → degrades to
    declared a, served==a, no exception surfaces (fail-OPEN)
  - test_bs8_ordered_byte_identical_no_loadgate: router with OrderedStrategy + load_gate=None;
    complete & stream → a first; assert load_gate spy NEVER called (zero IO)
  - test_bs9_load_aware_stream_ordered_fallback: LeastBusyStrategy; router.stream(alias) →
    upstream.stream called with model==candidates[0]; assert NO aorder/in_flight call on stream
  - test_bs10_unknown_strategy_rejected_least_busy_latency_ok: Settings(routing_strategy=
    "weighted-round-robin") raises "UNKNOWN_ROUTING_STRATEGY"; "least-busy"/"latency" boot OK
  - test_bs11_async_non_permutation_rejected: fake AsyncRoutingStrategy.aorder drops a candidate;
    complete raises "INVALID_ROUTING_ORDER"
  - test_bs12_create_app_wires_load_gate_only_when_needed: create_app(least-busy) → router
    ._load_gate is a RedisDeploymentLoadGate and ._strategy is LeastBusyStrategy; create_app(
    simple-shuffle) → ._load_gate is None and ._strategy is SimpleShuffleStrategy
  - test_bs_loadgate_inflight_roundtrip (RedisDeploymentLoadGate, fakeredis): acquire×2 →
    in_flight==2; release → 1; in_flight clamps negative→0; ewma update follows α formula
  - test_bs_settings_loadbal_knobs: default alpha==0.3, ttl==60; alpha=1.5→"INVALID_LOADBAL_ALPHA";
    ttl=0→"INVALID_LOADBAL_TTL"
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

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

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
