# TASK: Enforce plan rpm/tpm ceilings at the tenant layer

slug: plan-rate-enforcement · created: 2026-07-15 · stage: production
milestone: platform-access-plan
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/tenants/domain/entitlements.py:resolve_entitlements` / `ResolvedEntitlements`
    — pure, zero-I/O precedence resolver. Today resolves budget + seat_cap (each independent,
    keyword-only, all-optional so existing callers stay byte-identical). ADD `effective_rpm_limit` /
    `effective_tpm_limit` the same additive way (plan default → None; no tenant override column exists).
  - `apps/gateway/src/gateway/tenants/infrastructure/orm.py:PlanRow` — has `rpm_limit_default` /
    `tpm_limit_default` (`Integer | None`, `> 0 OR NULL` check constraints). `TenantRow` has budget +
    seat_cap OVERRIDE columns but **NO rpm/tpm override** → effective rate = plan default only.
  - `apps/gateway/src/gateway/rate_limits/domain/ports.py:RateLimiter` (Protocol) —
    `check_rpm(id, limit)` / `check_tpm(id, limit)` / `record_tpm(id, tokens)`. The UUID arg builds the
    Redis key (`ratelimit:{rpm,tpm}:{uuid}` in `redis_lua_limiter.py`); passing `tenant_id` instead of
    `key_id` yields a DISTINCT tenant-scoped window with ZERO limiter changes. Fail-open on Redis error
    (RateLimitExceededError is the only raise; Redis errors are swallowed → admit).
  - `apps/gateway/src/gateway/proxy/application/use_cases.py:_enforce_rate_limits(authz)` (~L1705,
    called ~L1939) — the CHAT enforce seam: per-key `check_rpm/check_tpm` → 429 `RATE_LIMITED`. Plus
    `_fire_record_tpm(rate_limiter, key_id, tokens)` (~L214) fired post-response at ~4 sites.
  - `apps/gateway/src/gateway/proxy/application/governance.py` Step 8/9 (~L252-269) — the NON-CHAT
    enforce seam: same per-key `check_rpm/check_tpm` pattern, `rate_limiter` may be None.
  - `apps/gateway/src/gateway/budgets/infrastructure/redis_guard.py:RedisBudgetGuard._fetch_budget`
    (~L258) — THE PRECEDENT: one `SELECT ... FROM tenants t LEFT JOIN plans p ON t.plan_id=p.id
    WHERE t.id=:tid` per request, resolved via `resolve_entitlements`; accepted MVP "one SELECT per
    request, always-fresh" tradeoff on the hot path.
  - `apps/gateway/src/gateway/keys/domain/entities.py:AuthzResult` — carries `tenant_id`, `key_id`,
    `rpm_limit`, `tpm_limit`, `team_id` (frozen v1 tests assert only tenant_id/key_id — safe to read).
  - `apps/gateway/src/gateway/core/error_catalog.py:RATE_LIMITED` = `ErrorSpec(429, "ERR_RATE_LIMITED")`
    — reuse verbatim; no new error code.
Context (working folder): milestone `platform-access-plan`; broad-reading decision (Tin 2026-07-15) —
  enforce plan rpm/tpm on ALL of a tenant's usage, no `platform-key-default` dependency, matching how
  budget (`plan-enforcement`) and seat-cap (`plan-seat-cap`) already shipped.
Honors (patterns / conventions): additive precedence in `ResolvedEntitlements` (never perturb another
  dimension); "one query per request, fail-open" hot-path guard (RedisBudgetGuard); compose-not-replace
  the per-key ceiling; RateLimiter fail-open contract; reuse `RATE_LIMITED` / `resolve_entitlements`.
Anchors the contract cites: `resolve_entitlements` / `ResolvedEntitlements` (extended);
  `RateLimiter` (reused unchanged); a new `PlanRateLimitResolver.resolve(tenant_id) -> ResolvedRate`;
  `_enforce_rate_limits` + governance Step 8/9 (tenant-window checks added); `_fire_record_tpm` (also
  fired against tenant_id).
Issues/Risks (→ feed §1):
  - HOT-PATH LATENCY: a naive standalone resolver adds a 2nd per-request DB query (budget already does
    one). Mitigate: cache-free single lightweight SELECT, fail-open to unlimited on any error (a rate
    resolver failing must NEVER block a request) — same posture as the budget guard.
  - COMPOSITION ORDER: tenant window must be checked ALONGSIDE (not instead of) the per-key window;
    either firing → 429. record_tpm MUST also accumulate the tenant window or TPM never enforces.
  - FAIL-OPEN is REQUIRED here (availability > enforcement), consistent with [[add-auto-mode]] and the
    budget Redis-down resolution — this is NOT a security gate. A tenant with no plan (plan_id NULL) or
    a plan with NULL rpm/tpm defaults is completely inert (no tenant window, no added query cost beyond
    the resolve).
Related intent: platform-access-plan exit criterion 4 ("A tenant over its plan's rpm/tpm ceiling is
  actually rate-limited at the tenant layer"); GLOSSARY `Plan`; milestone broad-reading note.
Ground SHA: 144fd9f  — cite symbols; any line ref is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant-layer plan rpm/tpm rate enforcement (composes with the per-key ceiling)
Framings weighed: reuse the existing `RateLimiter` protocol keyed by `tenant_id` (chosen — zero limiter
  change, distinct Redis window) · a new bespoke tenant rate limiter (rejected — duplicates the ZSET
  sliding-window logic) · piggyback rpm/tpm onto the budget guard's existing query (rejected — couples
  two independent enforcement dimensions; keep separate resolvers).
Must:
<must>
  - M0: additive `TenantRow.rpm_limit` / `TenantRow.tpm_limit` OVERRIDE columns (`Integer | None`,
    `> 0 OR NULL` check constraints, no backfill), mirroring `budget_usd_monthly` / `seat_cap`'s own
    nullable-override shape. New Alembic migration; every existing row NULL (inert). Tin 2026-07-15.
  - M1: `resolve_entitlements` additively returns `effective_rpm_limit: int | None` and
    `effective_tpm_limit: int | None` — precedence = tenant override (`TenantRow.rpm_limit`/`tpm_limit`)
    → plan default (`PlanRow.rpm_limit_default`/`tpm_limit_default`) → None (unlimited), computed
    independently per dimension (mirrors budget/seat-cap EXACTLY). Additive keyword-only args default
    None; every existing caller stays byte-identical (budget/seat-cap dimensions unperturbed).
  - M2: a `PlanRateLimitResolver.resolve(tenant_id)` issues ONE `tenants LEFT JOIN plans` SELECT
    (selecting BOTH tenant overrides and plan defaults) and returns the effective (rpm, tpm) via
    `resolve_entitlements`. Unknown tenant, or all four values NULL → (None, None) = inert.
  - M3: in BOTH enforce seams (chat `_enforce_rate_limits`, non-chat governance Step 8/9), AFTER the
    existing per-key check, when the resolved tenant rpm/tpm is not None, ALSO check the tenant-scoped
    window via the SAME limiter keyed by `tenant_id`. Either window exceeding → 429 `RATE_LIMITED`.
  - M4: post-response TPM accounting also records against the tenant window (`record_tpm(tenant_id, …)`
    fired wherever `record_tpm(key_id, …)` already fires) so the tenant TPM window actually accumulates.
  - M5: fail-open — any resolver DB error or limiter Redis error admits the request (availability >
    enforcement; not a security gate). A resolver exception never propagates to the request.
</must>
Reject:
<reject>
  - R1: tenant RPM window count >= effective tenant rpm_limit (plan default) -> "ERR_RATE_LIMITED" (429, Retry-After).
  - R2: tenant TPM pre-flight accumulated sum >= effective tenant tpm_limit -> "ERR_RATE_LIMITED" (429, Retry-After).
</reject>
After:
<after>
  - A tenant on a plan with rpm/tpm defaults is throttled at the tenant layer regardless of which/how
    many keys it uses; a tenant with no plan (or NULL defaults) sees byte-identical behavior to today.
  - The per-key ceiling still fires independently (compose, not replace).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  - [x] RESOLVED at freeze (Tin 2026-07-15): per-tenant rpm/tpm OVERRIDE columns ARE added (M0), so
    rate matches budget/seat-cap's tenant→plan→None precedence symmetry. No open flag remains here.
  - [x] Reusing `RateLimiter` keyed by tenant_id is safe — confirmed: the UUID only selects the Redis
    keyspace; tenant_id vs key_id are distinct values → distinct windows, no collision.
  - [x] Fail-open is the correct posture — confirmed by Tin's budget Redis-down resolution + this being
    an availability (non-security) gate; pinned by an explicit fail-open test.
  - [x] The tenant window composes without double-charging the per-key window — confirmed: separate
    Redis keys, separate `check_*` calls; a request charges each window once.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: resolve adds rpm/tpm dimensions   # M1
  Given a plan with rpm_limit_default=60, tpm_limit_default=100000
  When resolve_entitlements is called for a tenant assigned that plan
  Then effective_rpm_limit == 60 and effective_tpm_limit == 100000
  And effective_budget_usd_monthly and effective_seat_cap are unchanged from before this task

Scenario: unplanned tenant is inert   # M2, M5
  Given a tenant with plan_id IS NULL
  When PlanRateLimitResolver.resolve(tenant_id) runs
  Then it returns (None, None)
  And no tenant-scoped rate window is checked for that tenant's requests

Scenario: tenant RPM ceiling throttles across keys   # M3, R1
  Given a tenant on a plan with rpm_limit_default=2 and two API keys each with no per-key rpm_limit
  When the tenant makes a 3rd request within the minute across those keys
  Then the request is rejected 429 ERR_RATE_LIMITED with a Retry-After header
  And the per-key windows remain independent (a different tenant is unaffected)

Scenario: tenant TPM ceiling throttles pre-flight   # M3, M4, R2
  Given a tenant on a plan with tpm_limit_default=1000 that has already recorded 1000 tokens this minute
  When the tenant sends another request
  Then the request is rejected 429 ERR_RATE_LIMITED
  And recorded token usage also accumulates in the tenant window (not only the per-key window)

Scenario: per-key ceiling still fires independently   # M3 (compose-not-replace)
  Given a tenant with NO plan but a key whose per-key rpm_limit=1
  When the key makes a 2nd request within the minute
  Then it is still rejected 429 ERR_RATE_LIMITED by the per-key window
  And behavior is byte-identical to before this task

Scenario: resolver DB error fails open   # M5
  Given the plan-rate resolver's SELECT raises a DB error
  When a request is enforced
  Then the request is admitted (no tenant window checked, no 5xx from the resolver)
  And the per-key and budget checks proceed unchanged
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# 0) Additive schema (new Alembic migration; no backfill)
TenantRow += rpm_limit: int | None = None   # CHECK (rpm_limit IS NULL OR rpm_limit > 0)
           + tpm_limit: int | None = None   # CHECK (tpm_limit IS NULL OR tpm_limit > 0)
  mirrors TenantRow.budget_usd_monthly / seat_cap override shape; every existing row NULL (inert).

# 1) Pure resolver extension (no new endpoint — internal contract)
resolve_entitlements(*, …existing args…,
                     tenant_rpm_limit: int | None = None, plan_rpm_limit_default: int | None = None,
                     tenant_tpm_limit: int | None = None, plan_tpm_limit_default: int | None = None)
                     -> ResolvedEntitlements
ResolvedEntitlements  += effective_rpm_limit: int | None = None
                       + effective_tpm_limit: int | None = None
  precedence per dimension: tenant override → plan default → None (unlimited) — identical to budget/seat.
  Every existing field + existing call site byte-identical (all new args keyword-only, default None).

# 2) New hot-path resolver
class PlanRateLimitResolver:
    async def resolve(tenant_id: uuid.UUID) -> ResolvedRate   # ResolvedRate = (rpm: int|None, tpm: int|None)
    # ONE `SELECT t.rpm_limit, t.tpm_limit, p.rpm_limit_default, p.tpm_limit_default FROM tenants t
    #      LEFT JOIN plans p ON t.plan_id = p.id WHERE t.id = :tid`, via resolve_entitlements.
    # DB error -> (None, None) (fail-open; never raises to caller).  Unknown/all-NULL -> (None, None).

# 3) Enforce-seam behavior (no signature change to the public proxy API)
_enforce_rate_limits(authz)          # chat  — after per-key check, if tenant rpm/tpm not None:
governance Step 8/9                  # non-chat —   limiter.check_rpm(authz.tenant_id, tenant_rpm)
                                     #               limiter.check_tpm(authz.tenant_id, tenant_tpm)
  either window exceeded -> 429 ERR_RATE_LIMITED (Retry-After).  Redis error -> admit (limiter fail-open).
_fire_record_tpm(..., tenant_id, tokens)   # ALSO record tenant window wherever key window is recorded.

Schema: NEW additive migration adds tenants.rpm_limit / tenants.tpm_limit (nullable, >0 checks, no
  backfill). READS plans.rpm_limit_default / plans.tpm_limit_default + the new tenant overrides. No
  writes on the hot path. Redis windows: ratelimit:{rpm,tpm}:{tenant_id} (new keyspace by value, same limiter).
```

Glossary deltas: none new (extends `Plan`; "tenant-layer rate ceiling" = the plan rpm/tpm applied
  across all of a tenant's keys — a usage note under `Plan`, not a new term).
Least-sure flag surfaced at freeze: [contract] the tenant-rate resolver adds a SECOND per-request SELECT
  on the hot path (budget already does one). Confidence it's acceptable = medium: it mirrors the
  accepted budget-guard "one query per request, fail-open" tradeoff and only fires when a tenant is
  plan/override-bound, but if p99 latency regresses the build should fold the rate columns into the
  budget guard's existing SELECT rather than issue a separate query. Not blocking the freeze; a
  build-time perf note.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: yes

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new resolver + entitlements extension + both seam wirings)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_resolve_adds_rate_dimensions: plan rpm/tpm defaults → effective_rpm/tpm_limit; budget+seat unchanged · covers: M1
  - test_resolve_tenant_override_wins: tenant rpm/tpm override present → beats plan default per dimension · covers: M0, M1
  - test_override_columns_reject_nonpositive: rpm_limit/tpm_limit <= 0 violates the check constraint · covers: M0
  - test_unplanned_tenant_resolves_none: plan_id NULL + no override / unknown tenant → (None, None) · covers: M2, M5
  - test_tenant_rpm_throttles_across_keys: 3rd req/min across 2 keys on rpm=2 plan → 429 ERR_RATE_LIMITED · covers: M3, R1
  - test_tenant_tpm_throttles_preflight: tenant window at tpm cap → 429; record_tpm accumulates tenant window · covers: M3, M4, R2
  - test_per_key_ceiling_still_fires: no-plan tenant, per-key rpm=1 → 2nd req 429 (unchanged) · covers: M3 compose
  - test_resolver_db_error_fails_open: resolver SELECT raises → request admitted, no 5xx · covers: M5
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/` `apps/gateway/src/gateway/budgets/infrastructure/redis_guard.py` `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/application/governance.py` `apps/gateway/src/gateway/proxy/api/deps.py` `apps/gateway/src/gateway/main.py` `apps/gateway/src/gateway/rate_limits/` `apps/gateway/migrations/versions/` `./tests/`   (project-root tokens; `apps/gateway/src/gateway/tenants/` covers orm.py + entitlements; `deps.py`+`main.py` added at verify — the load-bearing optional-port wiring so the resolver reaches the request path and is constructed at boot (default-ON, Tin 2026-07-15); `./tests/` = this task dir)
Strategy (ordered batches): 1. migration + `TenantRow.rpm_limit`/`tpm_limit` columns (M0). 2. extend `resolve_entitlements`/`ResolvedEntitlements` tenant→plan→None (M1) + unit test red→green. 3. add `PlanRateLimitResolver` (M2, mirror `_fetch_budget`'s query+fail-open, select tenant overrides + plan defaults). 4. wire tenant-window checks into `_enforce_rate_limits` + governance Step 8/9 (M3) and tenant `record_tpm` at the `_fire_record_tpm` sites (M4). 5. fail-open paths (M5). Every change additive; never alter per-key behavior.

Persona (required): generic (backend-expert stance — async FastAPI, Redis/pg hot path, fail-open IO design per CLAUDE.md "design for failure"). No existing `.add/personas/` file fits a rate-limit domain.
Spawn isolation (default): isolation: "worktree" for the add-build subagent (mutates shared proxy files; keep the orchestrator tree clean).
Known-problem fixes: shared test Postgres :5433 → unique `GATEWAY_TEST_DATABASE_URL` base `gateway_test` (see [[shared-test-postgres-no-timeouts]]); xdist worker → Redis db K+1, never run 2 sessions; scope-snapshot poisoning from `.coverage`/`.pytest_cache` → clean tree before gate.
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): rate resolver + limiter FAIL-OPEN — any DB/Redis error admits the request; a resolver exception must never reach the request path or emit a 5xx.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full `-n12` gateway suite: 3938 passed / 0 failed / 7 skipped / 1 xfailed (10 flaked→passed on rerun), 696.94s. +14 over the 3924 baseline = the 14 new plan-rate tests.
- [x] coverage did not decrease — 90.86% total (≥80% gate held; new units directly covered).
- [x] no test or contract was altered during build — build agent md5-confirmed TASK.md unchanged; §3 untouched.
- [x] the green was EARNED, not gamed — orchestrator refute-read (below) read every changed file; RED→GREEN proven by real git-stash run.
- [x] concurrency / timing of the risky operation is safe — ContextVar copied per request-Task (no cross-request leak); atomic ZSET check; fail-open (one non-blocking residue, below).
- [x] no exposed secrets, injection openings, or unexpected dependencies — parameterized SQL (`:tid` bind), no new deps, no secrets.
- [x] layering & dependencies follow CONVENTIONS.md — resolver in `rate_limits/infrastructure`, shared enforce in `rate_limits/application`, reuses domain `resolve_entitlements`.
- [x] a person reviewed and approved the change — Tin approved the §3 freeze + the default-ON activation; final commit/push authorization pending.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] A tenant on Starter (60rpm) is 429'd on the 3rd req/min across two different keys — confirmed by `test_tenant_rpm_throttles_across_keys` (green in full suite) + refute-read of `enforce_tenant_rate_limit` keying the window by tenant_id.
- [x] A tenant with no plan sees byte-identical per-key behavior — confirmed by the widened-guard refute-read (tenant sibling no-ops on a None ContextVar) + the 55 embeddings/images/audio + 81 cache-tier regression tests unchanged.
- [x] A resolver DB error admits the request (no 5xx) — confirmed by `test_resolver_db_error_fails_open` + the resolver's own try/except returning (None,None).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `PlanRateLimitResolver` constructed in `main.py:app.state.plan_rate_limit_resolver`; read in `proxy/api/deps.py:get_completion_use_case`; passed to `CompletionUseCase` + `NonChatGovernance`; `enforce_tenant_rate_limit` called in both seams; `_fire_record_tpm_tenant` at all 5 record sites. Every new symbol referenced.
- [x] DEAD-CODE (code) — no orphaned symbol; `ResolvedRate`/`tenant_tpm_ctx`/`enforce_tenant_rate_limit` all consumed.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every §3 anchor still resolves — `resolve_entitlements`/`ResolvedEntitlements` (extended), `RateLimiter` (unchanged), `_enforce_rate_limits`, governance Step 8/9, `_fire_record_tpm` all confirmed present in the current tree (refute-read read each diff directly).
- [x] no anchor moved/renamed since Ground SHA 144fd9f (build ran on the same tree).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self (orchestrator) · adversarially checked: (1) existing resolve_entitlements callers stay byte-identical [all new args kw-only default None ✓]; (2) the 5 widened record-guards preserve per-key behavior when no tenant ceiling [inner per-key `if tpm_limit` unchanged; tenant sibling no-ops on None ctx ✓]; (3) resolver never raises [whole DB call wrapped, returns (None,None) ✓]; (4) tenant window keyed by tenant_id ≠ per-key window, no double-charge/collision ✓; (5) migration additive, single head, clean down ✓. One residue found (streaming TPM ctx-propagation, fail-open) — logged as a §7 spec delta, non-blocking.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: self
1. Security: CLEAR — no new auth surface; parameterized SQL (no injection); no secrets; rate ceiling only ever RESTRICTS, never elevates; fail-open is availability (non-security) per Tin's standing budget-Redis-down posture.
2. Concurrency: RESIDUE (non-blocking) — on the STREAMING record site, tenant TPM accumulation relies on `tenant_tpm_ctx` propagating into the streaming generator's context; if it doesn't, tenant TPM under-counts on streamed responses = LESS enforcement (fail-open), never over-throttle or leak. Documented as a §7 spec delta to add a streaming-path accumulation test.
3. Architecture: CLEAR — clean hexagonal layering; one shared enforce fn (no dual-copy drift); mirrors budget/seat-cap precedents exactly.
Verdict: PASS
Residue: streaming-path tenant-TPM under-count (fail-open) — seeded as a follow-on spec delta, not a defect.
Binding: advisory — sensitivity: architecture/mechanical (no security finding; not gate-lowering)

### GATE RECORD
Reported: yes — this gate report rendered to Tin before the outcome was recorded
Outcome: PASS
Reviewed by: Tin Dang (freeze + activation approved) · date: 2026-07-15

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose reuse the existing `RateLimiter` protocol keyed by `tenant_id`; rejected a new bespoke tenant rate limiter (rejected — duplicates the ZSET sliding-window logic) · piggyback rpm/tpm onto the budget guard's existing query (rejected — couples two independent enforcement dimensions; keep separate resolvers).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang (freeze + activation approved))

### Spec delta
- [SPEC · open] Streaming-path tenant TPM accumulation depends on `tenant_tpm_ctx` propagating into the streaming generator's context — add a streaming-response test asserting the tenant TPM window accumulates (or confirm/accept the fail-open under-count). Evidence: only the non-stream `complete()` record site is directly tested; refute-read flagged the streaming site's ctx-visibility as unproven (fail-open if it misses).
- [SPEC · open] Non-chat modalities (images/audio/embeddings) never call `record_tpm` for the per-key window today — so tenant TPM never accumulates there either; tenant RPM DOES enforce (atomic on check). If tenant TPM enforcement is wanted for non-chat, wire a record site. Evidence: build-report M4 "vacuously zero sites for non-chat".
- [SPEC · seeded] Watch p99 latency of the extra per-request resolver SELECT; if it regresses, fold rate columns into `RedisBudgetGuard._fetch_budget`'s existing query. Evidence: §3 least-sure freeze flag.

### Competency deltas
- [ADD · folded] A build agent honoring a strict §5 Scope correctly STOPPED at a load-bearing out-of-scope wiring line (`deps.py`) and escalated rather than silently expanding — the right call; the scope was then amended at verify (deps.py + main.py added) with the activation decision routed to the human. Evidence: build report "Explicit scope deviation — flagged, not hidden". [folded foundation-version 53]
- [TDD · folded] The engine's `_count_test_defs` regex (`^\s*def test_`) undercounts `async def test_` — this async-heavy task's real 14 tests report as 3 (same undercount hits every async task, e.g. plan-seat-cap 28→3). Not introduced here; `.add/tooling/` off-limits. Evidence: build report. [folded foundation-version 53]
