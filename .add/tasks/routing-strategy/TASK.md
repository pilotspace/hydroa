# TASK: RoutingStrategy seam + simple-shuffle weighted selection

slug: routing-strategy · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: RoutingStrategy seam — a pluggable policy that SELECTS the attempt order of a
model group's deployments before the v6 fallback loop runs. Ships two strategies:
`ordered` (declared order = v6 byte-identical, the DEFAULT) and `simple-shuffle`
(weighted-random primary selection by deployment weight, with the rest of the group as
the fallback tail). Selected by a new GATEWAY_ROUTING_STRATEGY setting. Builds directly
on deployment-model's frozen `Deployment.weight` + `router.deployments` view.

Framings weighed:
  - **Strategy seam consulted INSIDE FallbackModelRouter, returning an attempt order (chosen)**:
    a `RoutingStrategy` Protocol with `order(alias, candidates, deployments) -> list[str]`.
    The router calls it once at the top of complete()/stream() to get the candidate order,
    then runs the EXISTING fallback loop over that order. The default `OrderedStrategy`
    returns `candidates` unchanged → v6 byte-identical (same loop, same order, same gate/
    billing). `SimpleShuffleStrategy` does a weighted shuffle (primary picked ∝ weight,
    remaining appended as fallback). Minimal blast radius: the loop body is untouched;
    only the iteration order is produced by a seam.
  - **A separate router wrapper that pre-reorders then delegates (rejected)**: a second
    object duplicating alias resolution; two places that know about model groups; drift.
  - **Per-request client-specified strategy (rejected)**: routing policy is an operator
    concern (catalog/config), never client-controlled — matches the v7 provider-seam rule.

Must:
<must>
  - A `RoutingStrategy` Protocol: `order(alias: str, candidates: list[str],
    deployments: list[Deployment]) -> list[str]` returning a permutation of `candidates`
    (same set, no adds/drops) — the order the fallback loop attempts.
  - `OrderedStrategy` (default): returns `candidates` unchanged. With it wired, complete()
    and stream() are BYTE-IDENTICAL to v6 (same attempt order, gate calls, billing,
    fallback counters, streaming-first-candidate). This is the only behavior when
    GATEWAY_ROUTING_STRATEGY is unset/"ordered".
  - `SimpleShuffleStrategy`: selects the PRIMARY deployment by weighted-random choice
    (probability ∝ weight; equal weights → uniform), then appends the remaining
    deployments (declared order) as the fallback tail. Result is a full permutation so
    the existing fallback loop still covers every candidate.
  - GATEWAY_ROUTING_STRATEGY setting: "ordered" (default) | "simple-shuffle". An unknown
    value rejects at startup (fail-closed). Empty model_groups / plain (non-alias) model
    ids are UNAFFECTED (no strategy consulted on the plain path — v6 byte-identical).
  - The strategy applies to BOTH complete() (full attempt order) and stream() (the strategy
    picks the PRIMARY; stream still attempts only that one — v6 no-fallback-on-stream
    boundary preserved; for `ordered` that primary == candidates[0], byte-identical).
  - Billing/gate/fallback semantics are unchanged: billing keys on the served candidate id;
    the health gate still skips gated candidates; fallback counters still fire.
  - Determinism for tests: the RNG is injectable (constructor seam) so SimpleShuffle is
    assertable; production uses the default RNG.
</must>
Reject:
<reject>
  - GATEWAY_ROUTING_STRATEGY set to an unknown value -> "UNKNOWN_ROUTING_STRATEGY" (startup)
  - a strategy that returns a non-permutation (adds/drops/duplicates a candidate)
    -> "INVALID_ROUTING_ORDER" (defensive runtime guard in the router; abort, never
    silently serve a wrong/partial set)
  - any change that alters v6 attempt order / gate / billing for the default `ordered`
    strategy, or edits a frozen v6 test / the INVIOLABLE chat path -> "ERR_FROZEN_VIOLATION"
</reject>
After:
<after>
  - A model alias with ≥2 deployments under `simple-shuffle` distributes the PRIMARY pick
    across deployments by weight (not always candidates[0]); the rest remain as fallback.
  - The default `ordered` strategy keeps complete()/stream() byte-identical to v6 (frozen
    model_fallbacks + routing_admin + proxy suites green).
  - `RoutingStrategy` is the frozen seam balance-strategies (least-busy / latency) build on.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE: that consulting the strategy at the TOP of complete()/stream() and
    iterating its returned order leaves the v6 `ordered` path byte-identical — the fallback
    loop reads `candidates[i+1]` for the fallback counter's `to_model` label and uses
    candidates[0] on the stream path, so the seam must feed the loop a list with the SAME
    semantics. Lowest confidence because a subtle reorder or off-by-one in the counter
    labels would break frozen model_fallbacks assertions. If wrong cost: frozen-suite break
    → re-pin the seam so `ordered` returns the exact same list object semantics.
  - [ ] weighted-random with the stdlib `random` module is acceptable in the gateway
    (production), with an injected Random instance for deterministic tests — confirm no
    project rule forbids `random` in app code (the no-random rule is workflow-script-only).
  - [ ] the strategy needs only (candidates, deployments) — weight lives on Deployment;
    least-busy/latency (next task) will need Redis, so the Protocol signature must be wide
    enough now (async? sync?) to avoid a re-freeze. Decide sync-vs-async at the contract.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: RS1 — default ordered strategy is byte-identical to v6 (complete)
  Given GATEWAY_ROUTING_STRATEGY unset (default "ordered") and alias "fast"=[a,b,c]
  When a completion targets "fast" and a is available
  Then the router attempts a first (served_model_id == a), exactly as v6
  And the gate calls, fallback counters, and billing are unchanged from v6

Scenario: RS2 — ordered strategy preserves v6 fallback order
  Given "ordered" and alias "fast"=[a,b,c] where a raises UpstreamUnavailableError
  When a completion targets "fast"
  Then the router falls through a→b in declared order and serves b
  And the fallback counter to_model label sequence matches v6 (a→b, then b served)

Scenario: RS3 — simple-shuffle picks primary by weight (deterministic RNG)
  Given GATEWAY_ROUTING_STRATEGY="simple-shuffle", alias "fast"=[{a,weight:1},{b,weight:9}]
    and a seeded RNG that selects the high-weight bucket
  When 100 completions target "fast"
  Then b is chosen as primary far more often than a (≈9:1), not always candidates[0]
  And every request still serves successfully (the non-primary remains as fallback tail)

Scenario: RS4 — simple-shuffle returns a full permutation (fallback still covers all)
  Given "simple-shuffle", alias "fast"=[a,b] and the chosen primary (say b) then fails
  When a completion targets "fast"
  Then the router falls through to the other candidate (a) and serves it
  And no candidate is dropped or duplicated (the order is a permutation of [a,b])

Scenario: RS5 — stream path honors the strategy primary, no fallback (v6 boundary)
  Given "simple-shuffle", alias "fast"=[{a,weight:1},{b,weight:9}], seeded RNG → b
  When a streaming completion targets "fast"
  Then the router rewrites model=b (the strategy primary) and streams it only
  And for the default "ordered" strategy the stream primary == candidates[0] (v6 byte-identical)

Scenario: RS6 — plain (non-alias) model id is unaffected
  Given any strategy configured and a completion for a plain model id "vendor/x" (no alias)
  When it is routed
  Then the strategy is NOT consulted and the request passes through exactly as v6
  And no reordering or RNG draw occurs on the plain path

Scenario: RS7 — unknown strategy name rejected at startup
  Given GATEWAY_ROUTING_STRATEGY="round-robin" (not implemented this task)
  When the gateway boots
  Then startup raises ValidationError containing "UNKNOWN_ROUTING_STRATEGY"
  And no app starts (fail-closed)

Scenario: RS8 — a strategy returning a non-permutation is rejected at runtime
  Given a (test) strategy whose order() drops a candidate
  When the router routes an alias request
  Then the router raises an error referencing "INVALID_ROUTING_ORDER"
  And it never serves a partial/incorrect candidate set
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This task adds an application-layer seam (no HTTP endpoint of its own). No route is
added or changed; the strategy plugs into the existing FallbackModelRouter.

```
TYPES (new — gateway/proxy/application/routing_strategy.py)
  class RoutingStrategy(Protocol):
      def order(self, alias: str, candidates: list[str],
                deployments: list[Deployment]) -> list[str]
      # SYNC this task (both strategies are pure/no-IO). Returns a PERMUTATION of
      # `candidates` — the attempt order. complete() iterates it; stream() uses [0].
      # SUPERSESSION NOTE: balance-strategies (least-busy/latency, Redis) will supersede
      # this to an async variant per the v6 "frozen behavioral pin → supersession" pattern
      # (record at that task's freeze; default stays behavior-preserving). order() is sync
      # NOW because stream() is sync-returning-AsyncIterator and executes synchronously at
      # a frozen call site (use_cases.py:1237 `gen = model_router.stream(...)` inside a
      # try/except) — making it await would move exception timing and break frozen stream tests.

  class OrderedStrategy:   # DEFAULT
      def order(...) -> list[str]:  return list(candidates)        # v6 byte-identical

  class SimpleShuffleStrategy:
      def __init__(self, rng: random.Random | None = None): ...    # injectable RNG for tests
      def order(...) -> list[str]:
          # weighted-random pick of the PRIMARY (P(d) ∝ d.weight; missing/empty deployments
          # ⇒ uniform weight 1), then append the remaining candidates in declared order.
          # Returns a full permutation so the fallback loop still covers every candidate.

CONFIG (gateway/core/config.py — Settings)
  env GATEWAY_ROUTING_STRATEGY : str = "ordered"   # "ordered" | "simple-shuffle"
  startup validator: value not in {"ordered","simple-shuffle"} -> ValueError "UNKNOWN_ROUTING_STRATEGY"

ROUTER (gateway/proxy/application/fallback_router.py — FallbackModelRouter)
  __init__(... , strategy: RoutingStrategy | None = None)   # None ⇒ OrderedStrategy()
  complete(): after resolving `candidates = self._model_groups.get(model_id)` (alias path):
      deployments = self._deployments.get(alias, [])
      order = self._strategy.order(alias, candidates, deployments)
      if len(order) != len(candidates) or set(order) != set(candidates):
          raise ValueError("INVALID_ROUTING_ORDER: strategy returned a non-permutation")
      # then iterate `order` (was `candidates`) through the UNCHANGED v6 fallback loop;
      # next_model label uses order[i+1]. For OrderedStrategy order==candidates ⇒ byte-identical.
  stream(): primary = self._strategy.order(alias, candidates, deployments)[0]
      rewritten = {**payload, "model": primary}    # ordered ⇒ candidates[0], v6 byte-identical

WIRING (gateway/main.py create_app)
  build the strategy from settings.routing_strategy ("ordered"→OrderedStrategy(),
  "simple-shuffle"→SimpleShuffleStrategy()); pass strategy=... to FallbackModelRouter.
  The plain (non-alias) path and the model_router=None path are UNTOUCHED.

INVIOLABLE (byte-identical for the default "ordered" strategy):
  - the v6 fallback loop body, gate calls, billing (served candidate id), fallback counters
  - the stream no-fallback-on-first-candidate boundary
  - use_cases.py stream/complete call sites (router method signatures unchanged except the
    additive `strategy` ctor kwarg)
```

GLOSSARY deltas (add at freeze): **Routing strategy** amended — now a concrete seam
(`order()`), with `ordered` (default, v6) and `simple-shuffle` (weighted-random primary)
implementations; selected by GATEWAY_ROUTING_STRATEGY. Orthogonal to fallback (post-failure
traversal) and cooldown (health removal).

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] order() is SYNC this task and will be
SUPERSEDED to async by balance-strategies (Redis least-busy/latency) — chosen because
stream() is sync-returning-AsyncIterator and runs synchronously at a frozen call site
(use_cases.py:1237 inside a try/except); making order() async would force stream() into an
async generator, moving where exceptions surface and breaking frozen stream tests. Why this
is the risk: if balance-strategies later needs async ordering ON THE STREAM PATH, it must
rework stream() (or fall back to `ordered` on streams) — a known, bounded follow-up. Cost if
wrong: a stream-path rework in the next task, not a redesign of this seam. Secondary flag:
[contract] simple-shuffle uses stdlib `random` in app code (injectable Random for tests) —
the no-random rule is workflow-script-only, so this is allowed; flagged so a reviewer
confirms determinism is achieved via the injected RNG, not by avoiding random.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on routing_strategy.py + the router order/permutation paths.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_rs1_ordered_default_byte_identical_complete: router with OrderedStrategy (default),
    alias [a,b,c], fake upstream a→200; assert served_model_id==a and the gate/billing path
    matches v6 (reuse model_fallbacks fakes/conftest idioms)
  - test_rs2_ordered_preserves_fallback_order: OrderedStrategy, a raises UpstreamUnavailable;
    assert fall-through a→b, served==b, fallback counter to_model labels a→b then served
  - test_rs3_simple_shuffle_weighted_primary: SimpleShuffleStrategy(rng=seeded), alias
    [{a,w1},{b,w9}]; call order() 1000x; assert b is primary ≈90% (binomial tolerance),
    not always candidates[0]
  - test_rs4_simple_shuffle_full_permutation_fallback: SimpleShuffle seeded→primary b; b fails;
    assert router falls through to a and serves it; assert set(order)==set(candidates), len equal
  - test_rs5_stream_uses_strategy_primary: SimpleShuffle seeded→b; router.stream(alias) →
    assert upstream.stream called with model==b; with OrderedStrategy assert model==candidates[0]
  - test_rs6_plain_model_id_unaffected: any strategy; plain id "vendor/x"; assert pass-through,
    strategy.order NOT called (spy), no RNG draw
  - test_rs7_unknown_strategy_rejected: Settings(routing_strategy="round-robin") raises
    ValidationError matching "UNKNOWN_ROUTING_STRATEGY"; Settings(routing_strategy default) →
    "ordered"
  - test_rs8_non_permutation_rejected: a fake strategy whose order() drops a candidate; router
    .complete(alias) raises an error referencing "INVALID_ROUTING_ORDER"
  - test_rs_settings_default_ordered: Settings() default routing_strategy=="ordered"
  - test_rs_create_app_wires_strategy: create_app(Settings(routing_strategy="simple-shuffle"))
    → app.state.model_router strategy is a SimpleShuffleStrategy (wiring regression)
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

- [x] all tests pass — routing_strategy 10/10 green; full `make ci` 543 passed, 5 failed.
      The 5 reds are documented pre-existing flakes (1 guardrails async-write/redis-NOGROUP
      race + 4 health_alerting s07/s09/s10/s11 `asyncio.sleep(0.05)`→DB async-write race);
      ALL 5 re-run green in isolation (`pytest <5 ids> -o addopts="" → 5 passed in 1.74s`)
      and none touch routing_strategy.py / fallback_router.py / config.py / main.py.
- [x] coverage did not decrease — full `make ci` 81.56% (≥80% gate), same as the
      deployment-model baseline; routing_strategy.py covered by the 10-test suite (only the
      single-candidate `len<=1` early-return is unexercised — 2 of 6039 stmts).
- [x] no test or contract was altered during build — test file last modified in the FRONT
      commit 86a089f (untouched in the build commit); §3 FROZEN contract diff count 0.
- [x] concurrency / timing of the risky operation is safe — `order()` is pure-sync with NO
      `await`, so it runs atomically within one asyncio step; the single shared
      `SimpleShuffleStrategy._rng` never interleaves under the single-threaded event loop.
      No shared mutable state mutated across requests.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secrets touched;
      no eval/SQL/format injection; stdlib `random` only (non-crypto routing, documented
      `# noqa: S311`), zero new third-party dependency.
- [x] layering & dependencies follow CONVENTIONS.md — routing_strategy.py lives in
      proxy/application and imports core.config.Deployment under `TYPE_CHECKING` only;
      fallback_router (same layer) consumes it; main.py (composition root) wires it. No
      domain→infra or upward import added. ruff + pyright clean; allowlist OK.
- [x] a person reviewed and approved the change — Tin Dang (delegated auto mode, 2026-06-12),
      same delegation as the §3 freeze; security checked and clean (no HARD-STOP trigger).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced (grep-confirmed):
      `build_strategy` → main.py:40 import + main.py:393 create_app call;
      `OrderedStrategy` → fallback_router.py:100 default + routing_strategy.build_strategy;
      `SimpleShuffleStrategy` → build_strategy("simple-shuffle") + wiring test;
      `RoutingStrategy` (Protocol) → fallback_router.py:51/89/100 type;
      `routing_strategy` setting → config.py:201 validator + main.py:393 wiring;
      `_strategy_order` → complete():208 + stream():295.
- [x] DEAD-CODE (code) — no orphaned symbol: SimpleShuffleStrategy reachable via
      build_strategy("simple-shuffle"); the `len(candidates)<=1` branch is the legitimate
      single-deployment-group path; no unused import (ruff F401/RUF100 clean).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-12

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): primary-pick distribution per alias under simple-shuffle
(should track configured weights ≈ N:1); INVALID_ROUTING_ORDER occurrences (must stay 0 —
any non-zero is a strategy bug); UNKNOWN_ROUTING_STRATEGY boot rejections (config typos).
Spec delta for the next loop: balance-strategies (least-busy/latency, Redis) supersedes
`order()` to async — the §3 SUPERSESSION NOTE is the pin; the stream path (sync call site at
use_cases.py:1237) is the bounded follow-up risk to re-pin then. deployment-limits will add
per-deployment TPM/RPM candidate filtering UPSTREAM of the strategy (filter candidates →
strategy orders the survivors).

### Competency deltas
- [ADD · folded] A pure-sync seam with no `await` is the cleanest concurrency story under an
  asyncio event loop (atomic within one step) — but it pins the seam sync. When a known async
  successor exists, freeze the SUPERSESSION NOTE in the contract up front (done here) so the
  re-pin is a planned follow-up, not a surprise re-freeze. (evidence: §3 SUPERSESSION NOTE +
  the sync-vs-async least-sure flag surfaced at freeze.)
- [TDD · folded] Weighted-random behavior is assertable deterministically via an injected
  `random.Random(seed)` + a 1000-draw distribution band (0.80<b_share<0.98), not by mocking —
  keeps the test honest about the real algorithm. (evidence: test_rs3 green, stable across runs.)
- [SDD · folded] A default strategy that returns its input unchanged (`OrderedStrategy →
  list(candidates)`) is the byte-identical-preservation lever: the entire v6 fallback loop is
  reused verbatim and the frozen suites stay green with zero loop-body edits. (evidence:
  model_fallbacks + routing_admin + proxy suites green under the new seam.)
