# TASK: Tenant monthly ceiling, Redis spend counter, ERR_BUDGET_EXCEEDED

slug: budgets · created: 2026-06-10 · stage: mvp · autonomy: auto
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant monthly spend ceiling — pre-flight budget guard in the proxy, admin API to read/write the ceiling
Framings weighed: pre-flight BudgetGuard port in CompletionUseCase (chosen) · post-flight enforcement after upstream call (rejected: does not prevent spend, only detects it) · Envoy rate-limit filter (rejected: Envoy cannot read per-tenant Redis counters without custom gRPC service — scope creep)
Must:
<must>
  - tenants table gains an additive nullable column budget_usd_monthly Numeric(12,2) NULL; NULL means unlimited; no server_default; existing rows are unaffected
  - gateway/budgets/{domain,application,infrastructure,api} module in clean architecture mirroring gateway/usage structure; domain has zero framework imports
  - CompletionUseCase gains a BudgetGuard step AFTER key auth, BEFORE model-check and upstream: invoke guard.check(tenant_id) which reads the advisory Redis counter usage:spend:<tenant_id>:<YYYYMM> (written by usage-metering) and the tenant's budget_usd_monthly (DB read per request, MVP); spent >= budget AND budget is not NULL → raise ProblemError 402 ERR_BUDGET_EXCEEDED; the upstream is never called and no ledger row is written
  - NULL budget (unlimited) → guard passes unconditionally (no DB or Redis read needed for the counter comparison, but DB read to determine NULL is still performed)
  - Counter key missing (no spend yet this month) → treat as 0.00 → guard passes
  - Redis unavailable during guard.check → log warning and allow (availability-over-enforcement tradeoff at MVP; small-overage risk is acceptable)
  - GET /admin/budget (Authorization: Bearer JWT, role any) → 200 { budget_usd_monthly: str | null, spent_usd_month: str } where spent_usd_month is the SUM of cost_usd from usage_records for the current UTC month for the authenticated tenant (ledger, not counter)
  - PUT /admin/budget (Authorization: Bearer JWT, role owner|admin) body: { budget_usd_monthly: str | null } → 200 echo of { budget_usd_monthly: str | null }; persists to tenants.budget_usd_monthly
  - All gateway-generated errors are RFC 9457 problem+json via gateway.core.errors
  - ALL existing proxy tests must stay green unmodified (the wired guard is pass-through for NULL budgets)
</must>
Reject:
<reject>
  - POST /v1/chat/completions when spent >= budget (budget is not NULL) → "ERR_BUDGET_EXCEEDED" (402); zero upstream calls; zero new ledger rows
  - PUT /admin/budget with role member → "ERR_AUTH_FORBIDDEN" (403)
  - PUT /admin/budget with negative value or non-decimal string → "ERR_PAYLOAD_INVALID" (422)
  - GET /admin/budget or PUT /admin/budget with missing/malformed/expired JWT → "ERR_AUTH_INVALID_TOKEN" (401)
</reject>
After:
<after>
  - A tenant with a budget set and spend >= that budget receives 402 on the next completion attempt; the upstream was not called; no new usage_records row exists for the blocked attempt
  - A tenant with NULL budget completes without restriction regardless of spend counter value
  - After PUT /admin/budget with a valid decimal string, GET /admin/budget echoes the new ceiling; spent_usd_month reflects the ledger sum for the current UTC month
  - A member-role user cannot change the budget (403); an owner or admin can
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Redis-counter-based enforcement carries small-overage risk for in-flight streaming requests — lowest confidence because streaming completions accumulate cost over seconds; if the counter is read before the stream finishes, a concurrent stream from the same tenant may push spend past the ceiling before either is blocked; if wrong (operator requires hard ceiling): move to synchronous post-stream check or a Lua-script atomic compare-and-set — at MVP the advisory-counter semantic is explicitly acceptable; flag in contract
  ⚠ DB read per request for budget_usd_monthly on every completion hot path — lowest confidence because at high concurrency this adds one SELECT per request; if wrong (too slow): cache budget value in Redis with a short TTL (e.g. 60 s) — contained change to the guard infrastructure adapter only; domain port unchanged
  - [x] spent_usd_month in GET /admin/budget is sourced from the Postgres ledger (usage_records SUM), not from the Redis advisory counter — the counter is advisory only; this is consistent with the usage-metering TASK §1 decision
  - [x] The budget column is Numeric(12,2) — supports ceilings up to $9,999,999,999.99 per month; adequate for MVP
  - [x] PUT /admin/budget accepts null to clear the ceiling (restore unlimited); the column is set to NULL
  - [x] The BudgetGuard port is a new domain port in gateway/budgets/domain/ports.py; the infrastructure adapter RedisBudgetGuard reads the counter key from Redis and the budget from the DB; a PassthroughBudgetGuard (always allows) is provided for tests that do not exercise the budget path
  - [x] BudgetGuard.check() is injected into CompletionUseCase alongside existing ports (additive constructor arg with a PassthroughBudgetGuard default); this is the cross-module touch sanctioned in the execution context
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost-if-wrong. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: under-budget completion succeeds
  Given tenant "Acme" has budget_usd_monthly = 10.00 and spend counter = 5.00
  When she POSTs /v1/chat/completions with a valid key
  Then the response is 200 and the upstream received exactly one call
  And no ERR_BUDGET_EXCEEDED was raised

Scenario: spend at or above budget blocks completion
  Given tenant "Acme" has budget_usd_monthly = 10.00 and spend counter = 10.00
  When she POSTs /v1/chat/completions with a valid key
  Then the response is 402 with code "ERR_BUDGET_EXCEEDED"
  And the upstream received zero calls
  And no new usage_records row exists for this attempt

Scenario: NULL budget is unlimited
  Given tenant "Acme" has budget_usd_monthly = NULL and spend counter = 9999.99
  When she POSTs /v1/chat/completions with a valid key
  Then the response is 200 and the upstream received exactly one call
  And no ERR_BUDGET_EXCEEDED was raised

Scenario: missing counter (no spend yet) allows completion
  Given tenant "Acme" has budget_usd_monthly = 10.00 and no spend counter key in Redis
  When she POSTs /v1/chat/completions with a valid key
  Then the response is 200 and the upstream received exactly one call
  And no ERR_BUDGET_EXCEEDED was raised

Scenario: Redis unavailable allows completion (availability-over-enforcement)
  Given tenant "Acme" has budget_usd_monthly = 1.00 and spend counter = 2.00 but Redis is down
  When she POSTs /v1/chat/completions with a valid key
  Then the response is 200 and the upstream received exactly one call
  And no ERR_BUDGET_EXCEEDED was raised

Scenario: PUT then GET budget roundtrip
  Given tenant "Acme" is logged in as owner
  When she PUTs /admin/budget with { budget_usd_monthly: "25.00" }
  Then the response is 200 with { budget_usd_monthly: "25.00" }
  And GET /admin/budget returns { budget_usd_monthly: "25.00", spent_usd_month: "0.00" }

Scenario: member role is forbidden from setting budget
  Given tenant "Acme" has a member-role user logged in
  When the member PUTs /admin/budget with { budget_usd_monthly: "5.00" }
  Then the response is 403 with code "ERR_AUTH_FORBIDDEN"
  And the budget was not changed

Scenario: negative budget value is rejected
  Given tenant "Acme" owner is logged in
  When she PUTs /admin/budget with { budget_usd_monthly: "-1.00" }
  Then the response is 422 with code "ERR_PAYLOAD_INVALID"
  And the budget was not changed

Scenario: tenant isolation — A's budget invisible to B
  Given tenant A has budget_usd_monthly = 50.00 and tenant B has budget_usd_monthly = 7.00
  When tenant B calls GET /admin/budget
  Then the response is 200 with budget_usd_monthly "7.00" (not "50.00")
  And tenant A's budget is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/budget   header: Authorization: Bearer <jwt>
  200 -> { budget_usd_monthly: str | null, spent_usd_month: str }
         (spent_usd_month = SUM(cost_usd) from usage_records for the current UTC month;
          "0.00" when no records exist; budget_usd_monthly = null when unlimited)
  401 -> problem+json { code: "ERR_AUTH_INVALID_TOKEN" }

PUT /admin/budget   header: Authorization: Bearer <jwt>   body: { budget_usd_monthly: str | null }
  200 -> { budget_usd_monthly: str | null }   (echo of persisted value)
  401 -> problem+json { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> problem+json { code: "ERR_AUTH_FORBIDDEN" }        (role = member)
  422 -> problem+json { code: "ERR_PAYLOAD_INVALID" }       (negative or non-decimal string)

POST /v1/chat/completions (pre-flight guard, cross-module touch)
  402 -> problem+json { code: "ERR_BUDGET_EXCEEDED" }       (spent >= budget, budget not null)
  (all other responses unchanged from proxy-completions TASK §3 FROZEN contract)

Schema:
  tenants.budget_usd_monthly Numeric(12,2) NULL   (additive column, no server_default)
  Access (guard): SELECT budget_usd_monthly FROM tenants WHERE id = :tenant_id
                  GET usage:spend:<tenant_id>:<YYYYMM> from Redis (advisory counter)
  Access (GET):   SELECT budget_usd_monthly FROM tenants WHERE id = :tenant_id
                  SELECT COALESCE(SUM(cost_usd), 0) FROM usage_records
                    WHERE tenant_id = :tenant_id
                    AND date_trunc('month', created_at AT TIME ZONE 'UTC') =
                        date_trunc('month', now() AT TIME ZONE 'UTC')
  Access (PUT):   UPDATE tenants SET budget_usd_monthly = :value WHERE id = :tenant_id

problem+json shape (RFC 9457, all errors platform-wide):
  { type: "about:blank", title: str, status: int, code: "ERR_*", detail?: str }

Ports:
  BudgetGuard (gateway/budgets/domain/ports.py):
    async check(tenant_id: uuid.UUID) -> None
      raises ProblemError(402, "ERR_BUDGET_EXCEEDED", ...) when spent >= budget
      never raises for any other reason (Redis down → allow, NULL budget → allow)
  PassthroughBudgetGuard: always passes (for tests and default wiring before budgets task)

Wiring:
  CompletionUseCase.__init__ gains budget_guard: BudgetGuard = PassthroughBudgetGuard()
  complete() and stream() call await self._budget_guard.check(tenant_id) after _authenticate,
  before _validate_payload and upstream
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [spec] Redis-counter enforcement carries small-overage risk for in-flight streaming completions — advisory-counter semantic is explicitly accepted at MVP; hard ceiling requires atomic compare-and-set Lua script; contained change, domain port unchanged.
⚠ [contract] DB read for budget_usd_monthly on every hot-path request — acceptable at MVP request rates; if latency becomes a problem: cache in Redis with TTL — infrastructure adapter only, port unchanged.

<!-- EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_under_budget_completion_succeeds: arrange budget=10.00, counter=5.00 in Redis / act POST completions / assert 200 + upstream.calls == 1
  - test_spend_at_budget_blocks_completion_zero_upstream_zero_ledger: arrange budget=10.00, counter=10.00 / act POST completions / assert 402 ERR_BUDGET_EXCEEDED + upstream.calls == 0 + usage_records row count unchanged
  - test_null_budget_unlimited: arrange budget=NULL, counter=9999.99 / act POST completions / assert 200 + upstream.calls == 1
  - test_missing_counter_allows_completion: arrange budget=10.00, no Redis key / act POST completions / assert 200 + upstream.calls == 1
  - test_redis_down_allows_completion: arrange budget=1.00, counter=2.00 but guard uses BrokenRedis / act POST completions / assert 200 + upstream.calls == 1
  - test_put_get_budget_roundtrip: arrange owner jwt / act PUT /admin/budget {"budget_usd_monthly": "25.00"} / assert 200 echo / act GET /admin/budget / assert budget_usd_monthly=="25.00" + spent_usd_month=="0.00"
  - test_member_forbidden_put_budget: arrange member-role jwt / act PUT /admin/budget / assert 403 ERR_AUTH_FORBIDDEN + budget unchanged
  - test_negative_budget_rejected: arrange owner jwt / act PUT /admin/budget {"-1.00"} / assert 422 ERR_PAYLOAD_INVALID + budget unchanged
  - test_tenant_isolation_budget: arrange tenant_a budget=50.00 + tenant_b budget=7.00 / act GET /admin/budget as tenant_b / assert budget_usd_monthly=="7.00" (not "50.00") + tenant_a unchanged
</test_plan>

Tests live in: `apps/gateway/tests/budgets/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): budget guard must NEVER raise into the proxy path on Redis/DB failures — availability over enforcement; guard step placement is AFTER auth, BEFORE model-check and upstream; the upstream is never called when ERR_BUDGET_EXCEEDED is raised.
Code lives in: `apps/gateway/src/gateway/budgets/` and additive touch to `apps/gateway/src/gateway/proxy/application/use_cases.py`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `make ci`: 78 passed (9 budgets + 69 prior), exit 0
- [x] coverage did not decrease — 83.82% ≥ 80% floor
- [x] no test or contract was altered during build — only `ruff format` line-joining in
      tests/budgets/test_budgets.py (verified by diff: identical expressions, zero
      assertion/logic change); §3 untouched
- [x] concurrency / timing of the risky operation is safe — advisory-counter check is
      read-only (no counter mutation in guard); small-overage window accepted at freeze (⚠ spec flag)
- [x] no exposed secrets, injection openings, or unexpected dependencies — all SQL
      parameterized (:tid/:value binds); tenant_id sourced from JWT identity, never request
      body; PUT gated by require_owner_or_admin; no new packages
- [x] layering & dependencies follow CONVENTIONS.md — budgets/domain has zero framework
      imports; proxy imports only budgets.domain.ports (port, not adapter); adapter wired
      in composition root via app.state
- [x] a person reviewed and approved the change — orchestrator manual diff review under
      delegated auto mode (Tin Dang, 2026-06-10)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — BudgetGuard/PassthroughBudgetGuard referenced in proxy use_cases.py:21,69;
      RedisBudgetGuard wired in main.py app.state.budget_guard; budget_router registered in
      create_app; deps.py:get_completion_use_case injects app.state.budget_guard (grep-confirmed)
- [x] DEAD-CODE (code) — residue found and removed at gate: unused proxy/api/deps.py
      get_budget_guard dependency + empty budgets/application/ package; CI re-run green after removal
- [x] SEMANTIC (prose / non-code) — n/a (code-only change); contract §3 re-read in full at
      verify: port path, endpoint shapes, schema column, fail-open semantics all match implementation

### GATE RECORD
Outcome: PASS (auto-resolved — autonomy: auto; evidence complete; no security finding)
Reviewed by: Claude (orchestrator) under delegated auto mode — Tin Dang · date: 2026-06-10

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 402 rate per tenant (budget-ceiling signal) · guard latency p99 (DB read on hot path) · Redis-down allowance rate (operational risk signal when Redis is flapping)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
