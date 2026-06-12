# TASK: per-deployment TPM/RPM limits skip saturated deployment at selection (429 when all saturated)

slug: deployment-limits · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: tests   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: USAGE-BASED routing — a deployment that is over its per-deployment RPM or TPM limit
is SKIPPED at selection (another deployment serves); when EVERY deployment in the group is
saturated the request gets a clean 429 (ERR_RATE_LIMITED), never a 500. Builds on the frozen
Deployment.tpm_limit / Deployment.rpm_limit fields (deployment-model v8) and reuses the v1
per-minute Redis rate-limit idiom — but with SKIP-NOT-RAISE semantics (try the next deployment)
instead of the v1 per-API-key raise. Back-compat: a deployment with no limits (or a bare-string
group member = weight-1, no-limit) never saturates → v6 chat path byte-identical.

Framings weighed:
  - **A DeploymentLimitGate that FILTERS candidates UPSTREAM of the routing strategy (chosen)**:
    in complete(), before the strategy orders the group, drop every candidate whose deployment is
    saturated (read-only peek of its per-deployment RPM/TPM window). The strategy then orders the
    SURVIVORS; the v6 fallback loop runs over them. If NO survivors → raise a domain
    AllDeploymentsSaturatedError → the use case maps it to 429 ERR_RATE_LIMITED. The served
    deployment records its request (RPM) at selection and its tokens (TPM) after the response.
    Uniform across ALL strategies (ordered/simple-shuffle/least-busy/latency); minimal blast
    radius; None gate ⇒ no filter ⇒ byte-identical. The v6 cooldown skip stays INSIDE the loop
    (orthogonal: cooldown = unhealthy, limit = saturated).
  - **Per-API-key v1 limiter reuse, keyed per deployment, RAISE-on-exceed (rejected)**: v1
    check_rpm RAISES on the first over-limit deployment — but we must SKIP it and try the next,
    only 429-ing when ALL are saturated. A raise-based check can't express "skip one of N".
  - **Enforce in the strategy's aorder (rejected)**: ordered/simple-shuffle are SYNC and read no
    Redis; putting limit IO in the strategy would force them async and re-break the frozen seam.
    The filter belongs in the async router, above the strategy, exactly like the load_gate.

Must:
<must>
  - A new async port `DeploymentLimitGate` (None by default), reusing the v1 per-minute Redis
    rate-limit infra keyed PER DEPLOYMENT:
      `async def is_saturated(deployment_id, rpm_limit: int|None, tpm_limit: int|None) -> bool`
        # READ-ONLY peek: True iff (rpm_limit is not None AND current rpm-window ≥ rpm_limit)
        #   OR (tpm_limit is not None AND current tpm-window ≥ tpm_limit). Both None ⇒ never
        #   saturated, ZERO Redis commands (fast path). Fail-OPEN: any Redis error ⇒ False (admit).
      `async def record_request(deployment_id) -> None`   # INCR the per-minute RPM window (on serve)
      `async def record_tokens(deployment_id, tokens: int) -> None`  # INCRBY the TPM window (post-response)
    Errors fail-OPEN (logged with deployment_id only — never key strings/secrets).
  - complete() — ONLY when a limit_gate is wired — filters candidates BEFORE the strategy:
    survivors = [c for c in candidates if not await limit_gate.is_saturated(c, dep.rpm_limit,
    dep.tpm_limit)] (dep looked up from the router's frozen deployments view). The strategy orders
    the SURVIVORS; the v6 fallback loop runs over that order. When limit_gate is None: no filter,
    no recording → byte-identical to balance-strategies/v6.
  - When the filter leaves NO survivors (every deployment saturated): raise the domain
    `AllDeploymentsSaturatedError`; the use case catches it and raises RATE_LIMITED.exc(...) → a
    clean 429 ERR_RATE_LIMITED with a Retry-After (never a 500, never an upstream call).
  - Recording: the deployment that SERVES records one RPM hit at selection (before/at the upstream
    call) and its response tokens to TPM after the answer (usage.total_tokens; 0/absent ⇒ skip).
    A skipped (saturated) or merely-attempted-and-failed candidate records nothing extra beyond
    what the v6 loop already does.
  - Back-compat / byte-identical: a deployment with rpm_limit=None AND tpm_limit=None never
    saturates (is_saturated zero-Redis False); a model group of such deployments behaves exactly
    as balance-strategies/v6 (chat path unchanged). The limit_gate is wired ONLY when at least one
    configured deployment declares an rpm_limit or tpm_limit; otherwise it stays None.
  - Orthogonality preserved: billing keys on the SERVED deployment id; the v6 health gate
    (cooldown) still skips UNHEALTHY candidates inside the loop; fallback counters still fire;
    routing strategy still orders survivors. Saturation (429) and cooldown-exhaustion (503) are
    DISTINCT outcomes (all-saturated upfront → 429; all survivors cooled/failed in loop → 503).
  - Determinism for tests: the DeploymentLimitGate is a constructor-injected port; a fake gate
    with a scripted saturated-set + a record spy makes filtering/429/recording fully assertable.
</must>
Reject:
<reject>
  - every deployment in the group saturated (over RPM or TPM) -> AllDeploymentsSaturatedError ->
    429 "ERR_RATE_LIMITED" (clean, with Retry-After; no upstream call, no 500)
  - a DeploymentLimitGate Redis error -> NEVER a 500/false-429: is_saturated fails-OPEN to False
    (admit) so a Redis outage does not block traffic (availability over strict limiting)
  - any change that alters the v6 chat path / ordered attempt order / gate / billing for the
    no-limit (limit_gate=None) case, edits a frozen v6 / routing-strategy / balance-strategies
    test, or makes the frozen sync order()/stream() async -> "ERR_FROZEN_VIOLATION"
</reject>
After:
<after>
  - A model alias with ≥2 deployments where one is over its RPM/TPM is routed to a NON-saturated
    deployment; the saturated one is skipped at selection (not merely failed-over after a 429).
  - When all deployments are saturated the caller gets 429 ERR_RATE_LIMITED + Retry-After, no
    upstream call is made, and no 500 is ever produced.
  - A no-limit group (or bare-string v6 group) is byte-identical to balance-strategies/v6 — zero
    new Redis IO, no filtering, frozen suites green.
  - `DeploymentLimitGate` completes the v8 router surface: strategy (primary pick) + load_gate
    (in-flight/latency) + limit_gate (TPM/RPM saturation) + v6 fallback + cooldown, billing intact.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE: that a READ-ONLY peek-then-record (is_saturated filters; record_request
    increments only on serve) is acceptable for per-deployment RPM under concurrency — two
    requests can both peek under-limit and both serve before either records, overshooting the
    limit by up to (concurrency-1) within a window. Lowest confidence because a strict guarantee
    would need an atomic check-and-reserve (INCR-then-compare, decrement on skip) which complicates
    the filter and the four loop exit edges. Mitigation: per-deployment limits are PROTECTIVE
    (soft) not a security/billing gate; the overshoot is bounded by in-flight concurrency and
    self-corrects at the next per-minute window — the same soft behavior LiteLLM ships. If wrong
    cost: transient minor over-limit on a hot deployment, fixed later by switching the RPM path to
    atomic check-and-reserve — not a redesign of the gate or the router.
  - [ ] TPM is enforced by a PRE-flight peek of the accumulated per-minute token window + POST
    recording of actual usage tokens — so a single large request can push the window over TPM
    after the fact (you only know tokens once the response returns). Confirm this "soft TPM"
    matches intent (vs. rejecting by an ESTIMATE before the call). Chosen post-hoc recording to
    avoid an inaccurate pre-call token estimate; cost if wrong: a knob to estimate-and-reject up
    front, additive.
  - [ ] wiring the limit_gate ONLY when some deployment declares a limit (else None) is sufficient
    to keep no-limit configs byte-identical — confirmed by the load_gate "only when needed"
    precedent (balance-strategies) and the both-None zero-Redis fast path.
  - [ ] reusing 429 ERR_RATE_LIMITED (the existing catalog code) for all-saturated is correct vs.
    a NEW code (e.g. ERR_ALL_DEPLOYMENTS_SATURATED) — chosen reuse because it is semantically a
    rate limit and the 429+Retry-After mapping already exists; a distinct code is a one-line
    catalog add if operators need to disambiguate.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: DL1 — a saturated deployment is skipped at selection
  Given alias "fast"=[a,b] with a.rpm_limit=10 (a saturated) and b not saturated
  When a completion targets "fast"
  Then a is filtered out before ordering; b serves (served_model_id == b)
  And no upstream call is made to a (skipped at selection, not failed-over)

Scenario: DL2 — all deployments saturated → clean 429, no upstream call
  Given alias "fast"=[a,b] where both a and b are saturated (over RPM or TPM)
  When a completion targets "fast"
  Then the router raises AllDeploymentsSaturatedError → use case returns 429 ERR_RATE_LIMITED
  And no upstream completion is attempted; the response is NOT a 500

Scenario: DL3 — the served deployment records its RPM hit and response tokens
  Given alias "fast"=[a,b], a not saturated, a answers 200 with usage.total_tokens=42
  When a completion targets "fast"
  Then limit_gate.record_request(a) is called (RPM) and record_tokens(a, 42) after the answer
  And a saturated/un-served candidate records nothing

Scenario: DL4 — TPM-only saturation skips, RPM-only saturation skips (either dimension)
  Given alias "fast"=[a,b] with a over TPM (rpm fine) and b under both
  When a completion targets "fast"
  Then a is skipped (TPM saturation counts) and b serves

Scenario: DL5 — limit gate Redis error fails OPEN (admit, never false-429)
  Given alias "fast"=[a,b] and a limit_gate whose is_saturated() raises a Redis error
  When a completion targets "fast"
  Then the candidate is ADMITTED (treated as not saturated) and served normally (no 429, no 500)

Scenario: DL6 — no-limit group is byte-identical (limit_gate None; zero new IO)
  Given alias "fast"=[a,b] where a,b have rpm_limit=None and tpm_limit=None (limit_gate None)
  When a completion and a stream target "fast"
  Then behavior is byte-identical to balance-strategies/v6 (a first; no filter; gate never called)

Scenario: DL7 — saturation (429) is distinct from cooldown-exhaustion (503)
  Given alias "fast"=[a,b]: neither saturated, but both are cooled (health gate unavailable)
  When a completion targets "fast"
  Then the v6 loop exhausts and raises UpstreamUnavailableError → 503 (NOT 429)
  And the limit filter did not remove either candidate (they were healthy w.r.t. limits)

Scenario: DL8 — limit filter composes with the routing strategy over survivors
  Given "least-busy", alias "fast"=[a,b,c] with b saturated; in_flight a=5, c=1
  When a completion targets "fast"
  Then b is filtered out; the strategy orders survivors [a,c] → primary c (fewest in-flight); c serves

Scenario: DL9 — create_app wires the limit_gate only when a deployment declares a limit
  Given a config where some deployment has rpm_limit/tpm_limit vs a config with none
  When create_app builds each
  Then the first → router has a RedisDeploymentLimitGate; the second → limit_gate is None
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This task adds an application-layer filter + one infrastructure port + one domain error +
a 429 mapping. No HTTP endpoint of its own; it plugs into the existing FallbackModelRouter
and the CompletionUseCase error mapping. Returns 429 via the EXISTING RATE_LIMITED catalog spec.

```
PORT (gateway/proxy/domain/ports.py — NEW @runtime_checkable Protocol)
  class DeploymentLimitGate(Protocol):
      async def is_saturated(self, deployment_id: str,
                             rpm_limit: int | None, tpm_limit: int | None) -> bool
      async def record_request(self, deployment_id: str) -> None
      async def record_tokens(self, deployment_id: str, tokens: int) -> None
  # is_saturated: READ-ONLY peek. True iff (rpm_limit is not None AND rpm-window count ≥ rpm_limit)
  #   OR (tpm_limit is not None AND tpm-window sum ≥ tpm_limit). Both None ⇒ False, ZERO Redis.
  #   Fail-OPEN: any Redis error ⇒ False (admit). record_*: fail-soft, errors swallowed (log id only).

DOMAIN ERROR (gateway/proxy/domain/errors.py — NEW)
  class AllDeploymentsSaturatedError(Exception):
      def __init__(self, alias: str): self.alias = alias; super().__init__(...)
  # Raised by the router when the limit filter removes ALL candidates of an alias group.

INFRA (gateway/proxy/infrastructure/redis_limit_gate.py — NEW, mirrors redis_load_gate.py)
  class RedisDeploymentLimitGate:                  # implements DeploymentLimitGate
      def __init__(self, *, redis: Any, window_s: int = 60): ...
      # Fixed per-minute windows (bucket = floor(now/window_s)); keys (deployment_id only):
      #   gateway:deplimit:rpm:{deployment_id}:{bucket}  INT  (record_request: INCR + EXPIRE window_s)
      #   gateway:deplimit:tpm:{deployment_id}:{bucket}  INT  (record_tokens:  INCRBY tokens + EXPIRE)
      # is_saturated(): GET both current-bucket keys → int(or 0); compare ≥ limit per dimension;
      #   skip the GET for a dimension whose limit is None (both None ⇒ zero Redis). Fail-OPEN → False.
      # Constructor does NOT connect to Redis (safe without lifespan), same as the other gates.

ROUTER (gateway/proxy/application/fallback_router.py — FallbackModelRouter)
  __init__(... , limit_gate: DeploymentLimitGate | None = None)   # additive; None ⇒ no filter
  complete() alias path — BEFORE _strategy_order_async(...):
      if self._limit_gate is not None:
          deps = {d.model_id: d for d in self._deployments.get(alias, [])}
          survivors = []
          for c in candidates:
              d = deps.get(c)
              rpm = d.rpm_limit if d else None
              tpm = d.tpm_limit if d else None
              if not await self._limit_gate.is_saturated(c, rpm, tpm):
                  survivors.append(c)
          if not survivors:
              raise AllDeploymentsSaturatedError(alias)
          candidates = survivors        # strategy orders + v6 loop runs over survivors only
      # ... existing _strategy_order_async(alias, candidates) + the v6 fallback loop unchanged ...
      # On the SERVED candidate (the return path, after a candidate answers):
      #   if self._limit_gate is not None: await limit_gate.record_request(candidate)
      #     and, when body usage has total_tokens > 0: await limit_gate.record_tokens(candidate, n)
      # record happens ONLY for the served candidate (not skipped/failed ones).
  stream(): UNCHANGED — no limit filter on the stream path (v6 stream boundary; single-primary).

USE CASE (gateway/proxy/application/use_cases.py — at the router.complete() call site ~L961)
  except AllDeploymentsSaturatedError as exc:
      raise RATE_LIMITED.exc(detail=f"all deployments for '{exc.alias}' are rate-limited",
                             retry_after=<window seconds>)
  # ADDITIVE except clause around the existing `await model_router.complete(...)`; maps the domain
  # error to the EXISTING 429 ERR_RATE_LIMITED ProblemError. The chat success path is untouched.

WIRING (gateway/main.py create_app)
  limit_gate = RedisDeploymentLimitGate(redis=redis_client) IFF any d in settings.deployments
               values has d.rpm_limit is not None or d.tpm_limit is not None, else None.
  FallbackModelRouter(... , limit_gate=limit_gate)   # composes with strategy + load_gate
  The plain (non-alias) path and the model_router=None path are UNTOUCHED.

INVIOLABLE (byte-identical when limit_gate is None — no deployment declares a limit):
  - the v6 fallback loop body, gate calls, billing (served candidate id), fallback counters,
    the balance-strategies in-flight lifecycle, the strategy selection
  - the frozen sync order()/stream() and the routing-strategy / balance-strategies test suites
  - use_cases.py chat success path (only an ADDITIVE except clause is added)
```

GLOSSARY deltas (add at freeze): **DeploymentLimitGate** — async port exposing per-deployment
RPM/TPM saturation (read-only peek) + request/token recording, backed by per-minute Redis
windows; the seam that lets the router SKIP a saturated deployment at selection. **Saturated** —
a deployment whose current per-minute RPM or TPM window has reached its configured limit;
orthogonal to **cooled** (v6 cooldown = unhealthy) and to **in-flight/latency** (load_gate).
**AllDeploymentsSaturatedError** — every candidate of a group saturated → 429 ERR_RATE_LIMITED.

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] RPM uses a READ-ONLY peek at filter time +
INCR only on the SERVED candidate — NOT an atomic check-and-reserve. Under concurrency, K
simultaneous requests can each peek a deployment under-limit and all serve before any records,
overshooting that deployment's RPM by up to K-1 within the per-minute window. This is the
bundle's highest risk. Why it is acceptable (frozen rationale): per-deployment limits are
PROTECTIVE/soft (not a billing or security gate — those are the v1 per-API-key limiter, unchanged);
the overshoot is bounded by in-flight concurrency and self-corrects at the next 60 s bucket; this
matches the soft behavior LiteLLM ships. Cost if wrong: a hot deployment briefly exceeds its RPM,
remedied by switching the RPM path to an atomic INCR-then-compare-with-decrement-on-skip — a
localized change to RedisDeploymentLimitGate + the filter, NOT a redesign of the port or router.
Secondary flag: [contract] TPM is enforced post-hoc (peek accumulated tokens, record actual usage
after the answer) so a single large request can push a window over TPM after the fact — standard
soft-TPM; a pre-call estimate-and-reject knob is the additive future hardening if needed.

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on redis_limit_gate.py + the router limit-filter / 429 branch + the
use-case mapping. Fake DeploymentLimitGate with a scripted saturated-set + a record spy; reuse
the balance_strategies / routing_strategy fakes (_Upstream, _Gate, _deployment, FakeLoadGate).
<test_plan>
  - test_dl1_saturated_deployment_skipped: limit_gate saturated={a}; alias [a,b]; complete →
    served==b; assert NO upstream call carried model==a (a skipped at selection)
  - test_dl2_all_saturated_429: saturated={a,b}; complete → raises AllDeploymentsSaturatedError;
    assert NO upstream.complete call made at all
  - test_dl2b_use_case_maps_429: (integration) all-saturated alias via the use case / endpoint →
    HTTP 429 ERR_RATE_LIMITED (not 500); reuse proxy test harness idiom
  - test_dl3_served_records_rpm_and_tokens: a not saturated, answers 200 usage.total_tokens=42;
    assert record_request(a) called AND record_tokens(a,42); assert no record for the unserved b
  - test_dl4_either_dimension_saturates: a over TPM only (rpm ok); alias [a,b]; complete →
    a skipped, b served (TPM saturation alone is sufficient)
  - test_dl5_limit_gate_redis_error_fails_open: is_saturated raises; complete → candidate admitted,
    served normally (no 429, no 500)
  - test_dl6_no_limit_group_byte_identical: limit_gate=None; complete & stream → a first; assert
    gate spy never called; (regression: ordered behavior unchanged)
  - test_dl7_saturation_vs_cooldown_distinct: not saturated but health_gate cools both → router
    raises UpstreamUnavailableError (503 path), NOT AllDeploymentsSaturatedError; limit filter
    kept both candidates
  - test_dl8_filter_composes_with_strategy: LeastBusyStrategy + limit_gate saturated={b};
    in_flight a=5,c=1; complete → b filtered, strategy orders survivors [a,c] → primary c; served==c
  - test_dl9_create_app_wires_limit_gate_when_limit_declared: create_app with a deployment having
    rpm_limit → router._limit_gate is RedisDeploymentLimitGate; create_app with no limits →
    router._limit_gate is None
  - test_dl_limit_gate_roundtrip (RedisDeploymentLimitGate, fake redis): record_request×N →
    is_saturated True once window count ≥ rpm_limit; record_tokens accumulates → TPM saturates;
    both-None limits → is_saturated False with zero Redis GET; fail-OPEN on a raising redis
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
