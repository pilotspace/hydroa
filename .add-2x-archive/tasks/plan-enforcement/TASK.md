# TASK: Plans catalog enforced: budget defaults, model allowlists, feature flags

slug: plan-enforcement · created: 2026-07-12 · stage: production
sensitivity: data
milestone: monetization-core
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `RedisBudgetGuard._fetch_budget` / `.check` (`apps/gateway/src/gateway/budgets/infrastructure/
    redis_guard.py:47-96`) — THE tenant-budget choke point: one raw-SQL `SELECT budget_usd_monthly
    FROM tenants WHERE id = :tid` per request, fail-open on Redis/DB error. Called from BOTH
    governance pipelines at the identical position (see below) — this is the SAME choke point
    milestone binding rule 4 requires `credits-ledger`'s spend gate to compose with; this task
    extends its ONE query, never adds a second budget path.
  - `BudgetGuard` Protocol / `PassthroughBudgetGuard` (`apps/gateway/src/gateway/budgets/domain/
    ports.py:13-45`) — zero-infra Protocol port (`async def check(tenant_id) -> None`, raises
    `ProblemError(402, ERR_BUDGET_EXCEEDED)` or silently allows) — the contract this task's own
    `RedisBudgetGuard` change must keep byte-identical.
  - `AuthzResult` (`apps/gateway/src/gateway/keys/domain/entities.py:86-126`) — the per-request
    dataclass carrying every governance field, resolved ONCE at auth time via a single JOIN ("zero
    extra DB queries" convention, M8-M12/response-caching/guardrails-core/batch-auto-grouping
    precedent). `model_allowlist` here is KEY-LEVEL ONLY (from `ApiKeyRow.model_allowlist`) —
    confirmed by full-repo grep: **no tenant-level model allowlist exists anywhere today.**
  - `ApiKeyRepository.get_by_id` (`apps/gateway/src/gateway/keys/infrastructure/repository.py:
    142-168`) — the 3-table LEFT JOIN (`api_keys` → `teams` → `tenants`) populating `AuthzResult`'s
    tenant-scoped fields (`cache_enabled`, `guardrail_configs`, `semantic_cache_enabled`,
    `batch_grouping_enabled`, `zdr_enabled`, `payload_capture_enabled`) — explicit "zero extra DB
    reads" comment at line 149. This is the established, load-bearing precedent for adding a 4th
    `outerjoin(PlanRow, TenantRow.plan_id == PlanRow.id)` to carry plan fields into `AuthzResult`
    additively, at zero extra round trips.
  - `AuthzUseCase.execute` (`apps/gateway/src/gateway/keys/application/use_cases.py:269-323`) — the
    single call site constructing `AuthzResult` from the joined row (line 305-322,
    `getattr(row, "<field>", <default>)` pattern for every additive field) — every prior additive
    governance field followed this exact insertion pattern.
  - `_check_model_allowlist` — TWO independently-maintained copies: pure function
    (`use_cases.py:697-706`, chat path) and `NonChatGovernance._check_model_allowlist`
    (`governance.py:178-186`, non-chat path) — both check ONLY `authz.model_allowlist`, both run at
    governance step 3 (BEFORE the catalog check, step 4). No tenant/plan-level allowlist check
    exists to compose with today. Module docstring in `governance.py:1-8` states explicitly: "The
    chat path (use_cases.py) is NEVER modified" by non-chat additions — confirms any new step must
    be added to BOTH copies independently, never merged.
  - `_enforce_governance` (`use_cases.py:1206-1261`) / `NonChatGovernance.authorize`
    (`governance.py:79-149`) — the two 9-step ordered pipelines; both call `budget_guard.check
    (authz.tenant_id)` at the identical "tenant budget (fallback)" position, reached only when NO
    hard per-key budget is set. A `RedisBudgetGuard`-only fix for the plan-budget-default therefore
    covers BOTH pipelines "for free" — no pipeline edit needed for M2 (budget default).
  - `PlanRow` (`apps/gateway/src/gateway/tenants/infrastructure/orm.py:26-67`, read directly this
    session) / `TenantRow.plan_id` (`orm.py:150-156`) / `PlanResponse` (`apps/gateway/src/gateway/
    tenants/api/platform_plans_router.py:77-85`) — plan-catalog's FROZEN@v1 schema:
    `plans(id, name, display_name, seat_cap, budget_usd_monthly_default, rpm_limit_default,
    tpm_limit_default, created_at, updated_at)` — **zero model-allowlist or feature-flag columns
    exist yet.** `tenants.plan_id` is a nullable FK, `ON DELETE RESTRICT`, with defense-in-depth
    `ck_tenants_platform_no_plan` CHECK (platform tenant can never hold a plan).
  - `.add/tasks/plan-catalog/TASK.md` §1 M2 + After (FROZEN, DONE, PR #58) — literal frozen text:
    "Both are NULL, with NO backfill, for every pre-existing tenant row AND for every
    newly-signed-up tenant (no auto-assignment of a default plan at signup) — unplanned is the
    universal starting state" / "every proxy/budget/rate/provisioning code path is COMPLETELY
    unchanged (no enforcement code reads these two new columns yet — no sibling task has wired
    them)." Load-bearing for §1's least-sure decision below — read in full this session.
  - `.add/tasks/plan-seat-cap/TASK.md` (sibling, milestone `platform-access-plan`, `phase: ground`,
    body still template-empty as of this session — not yet grounded by its own agent) — confirms
    `plans.seat_cap`/`tenants.seat_cap` are untouched territory; this task reads neither column and
    writes neither, per milestone binding rule 5.
  - `batch_policy_router.put_batch_policy` (`apps/gateway/src/gateway/tenants/api/
    batch_policy_router.py:63-84`, FROZEN@v1 `batch-auto-grouping` TASK.md) — tenant-wide toggle
    write, `require_owner_or_admin` gated, raw-SQL UPDATE on `tenants.batch_grouping_enabled`.
    Feature-gate seam #1 (config-write time).
  - `guardrail_router.put_guardrails` (`apps/gateway/src/gateway/tenants/api/guardrail_router.py`,
    `ml_moderation` config block at lines 110-158/333-337, `fields_set` presence-check pattern at
    line 333) — `ml_moderation` is ONE of several policy keys written into the SAME JSONB blob; the
    gate must fire ONLY when `"ml_moderation" in fields_set`, never block unrelated guardrail edits.
    Feature-gate seam #2.
  - `logs_query_router.list_logs` / `.get_log` (`apps/gateway/src/gateway/logs/api/
    logs_query_router.py:229-312`) — gated today by `require_permission(Permission.LOGS_READ)`
    only, no plan concept, no persistent "enabled" toggle to gate at write time instead — the gate
    must run at QUERY time itself. Feature-gate seam #3.
  - `realtime_relay` websocket handler + `_authorize_governance`
    (`apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py:178-260,315-369`) — already
    translates a governance `ProblemError` into a WS close code via `_GOVERNANCE_CODE_BASE +
    exc.status` (confirmed at line 369) — an EXISTING error-translation seam a new plan-feature
    `ProblemError` rides for free. One-time WS-connect check, never per-message. Feature-gate seam
    #4.
  - `ErrorSpec` / `ProblemError.extra` (`apps/gateway/src/gateway/core/error_catalog.py:32-68`,
    `apps/gateway/src/gateway/core/errors.py:10-29`) — `extra: dict[str, object] | None` is an
    EXISTING structured-data carrier (precedent: `output-schema-validation`'s `raw_output`/
    `validation_errors`) — the seam this task reuses for an `upgrade_hint` payload on its 2 NEW
    error codes, instead of inventing a new response envelope.
  - Current alembic head: `69cfdc584129` (confirmed via `alembic heads` this session, single head)
    — this task's own intended migration parents here; NOT created at design time per process rules.
Context (working folder): `.add/milestones/monetization-core/MILESTONE.md` (Scope + Shared
  decisions + "Shared/risky contracts" naming "plan-enforcement resolution order" explicitly as a
  contract to freeze first, and Exit criterion 3 this task delivers verbatim); `tmp/
  monetization-core-design-context.md` (binding rule 4: reuse the SAME budget choke point
  `credits-ledger` will also compose with; binding rule 5: seat caps are `plan-seat-cap`'s, never
  duplicated here); `.add/tasks/plan-catalog/TASK.md` (frozen upstream schema + seed data, read in
  full this session); `.add/tasks/plan-admin-ui/TASK.md` (frozen `PlanResponse`/`TenantPlanResponse`
  DTOs, DONE — confirms no runtime plan-CRUD exists or is planned anywhere in this codebase).
Honors (patterns / conventions):
  - Reuse-over-invent / additive-only column growth — mirrors `budget_usd_monthly`'s own
    nullable/no-backfill convention exactly (plan-catalog's own precedent).
  - "Zero extra DB reads" hot-path convention (`ApiKeyRepository.get_by_id`'s existing 3-table
    JOIN) — extended, never duplicated, for the hot hot-path fields (budget, model allowlist).
  - Protocol-port discipline (backend-architect persona; `BudgetGuard`/`ModelChecker` precedent) —
    domain layer never imports SQLAlchemy/FastAPI/Redis.
  - Conjunctive (AND-composed) governance — every one of the 9 existing steps is a hard gate with
    NO existing precedent for one dimension overriding a stricter one; a new plan constraint
    composes by INTERSECTION, never substitution (see §1 Framings weighed #2).
  - Frozen-contract supersession only (backend-architect persona rule) — `plan-catalog`,
    `plan-admin-ui`, `batch-auto-grouping`'s own frozen TASK.md files are never edited; this task's
    own new decisions (e.g. a new reject case at `put_batch_policy`) are recorded here, in ITS OWN
    TASK.md, as a superseding addition, keeping the old file untouched.
Seams consulted: none in `.add/SEAMS.md` yet for plans/entitlements — first task to establish this
  seam; a `.add/SEAMS.md#entitlement-resolution` entry is proposed at BUILD for the next reader.
Anchors the contract cites: `RedisBudgetGuard._fetch_budget`, `BudgetGuard`, `AuthzResult`,
  `ApiKeyRepository.get_by_id`, `AuthzUseCase.execute`, `_check_model_allowlist` (both copies),
  `_enforce_governance`, `NonChatGovernance.authorize`, `PlanRow`, `TenantRow.plan_id`,
  `put_batch_policy`, `put_guardrails`, `list_logs`/`get_log`, `realtime_relay`/
  `_authorize_governance`, `ErrorSpec`/`ProblemError.extra`.
Issues/Risks (→ feed §1):
  - [Major] TWO independently-maintained copies of the governance pipeline (chat vs non-chat, by
    explicit existing design comment) — any plan-model-allowlist check must be added to BOTH,
    inherited drift risk this task does not introduce but must not worsen; flagged so BUILD does
    not miss a copy (mirrors every prior M8-M14 addition's own precedent of touching both files).
  - [Major] `plans` has ZERO `model_allowlist`/`feature_flags` columns today — this task's own
    migration is the FIRST to extend `plans` past its "done" freeze. Honors plan-catalog's own M1
    non-goal (no runtime plans-row CRUD): this task adds COLUMNS with migration-seeded values only.
  - [Major, feeds the least-sure flag] plan-catalog's own frozen M2/After-M2 make "unplanned = zero
    behavior change" the starting state for EVERY tenant (100% of the current tenant base — no
    auto-assignment exists). Whatever this task picks as "unassigned tenant" behavior applies to
    the entire installed base the moment this ships — not a narrow edge case.
  - [Minor] `ml_moderation` is one of N keys in a single JSONB `guardrail_configs` write — the
    feature gate must inspect the SPECIFIC key touched (`"ml_moderation" in fields_set`), never
    block the endpoint wholesale (would wrongly block unrelated guardrail edits).
  - [Minor] Confirm at BUILD exactly which internal helper `_authorize_governance` delegates to
    inside `realtime_relay_ws.py`, so the new feature check inserts at the ONE-TIME WS-connect
    authorization, never per-message (per-message would regress relay latency).
  - [Ruled out, not silently] Concurrent plan reassignment mid-flight: every governance field this
    task reads is resolved FRESH per request (no caching layer exists for `AuthzResult` or the
    admin-endpoint entitlement reads) — identical to every existing tenant-level toggle's own
    always-live-read property. Not a new race this task introduces.
Related intent: MILESTONE.md goal ("an enforced plan (seats · budgets · allowlists · features)")
  + Exit criterion 3 ("A tenant on a plan with a model allowlist/feature flag/budget default sees
  it actually enforced at request time (structured refusal, not catalog-only)") — this task
  delivers that criterion verbatim. GLOSSARY deltas introduced: "entitlement resolution", "plan
  default", "plan-gated feature" (see §3).
Ground SHA: 43ad492

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Plan enforcement — wire the already-shipped plans catalog into REAL request-time
governance for exactly 3 dimensions (budget defaults, model allowlists, feature flags — seat caps
are `plan-seat-cap`'s, never touched here), via ONE pure entitlement-resolution function evaluated
at a bounded, named set of enforcement points, composing with — never replacing — every existing
governance check.
Framings weighed:
  - **Where the budget-default fallback lives**: extend `RedisBudgetGuard._fetch_budget`'s existing
    single query with a `LEFT JOIN plans`, resolve in Python **(CHOSEN)** — vs. adding a NEW
    governance step in both `_enforce_governance`/`NonChatGovernance.authorize` copies
    **(REJECTED)**. The existing choke point IS "the same choke point" milestone rule 4 requires
    `credits-ledger` to reuse; extending its ONE query costs zero extra round trips and zero
    pipeline edits — the one place a plan-budget bug can't accidentally exist in only one of the
    two duplicated pipelines.
  - **Model-allowlist composition, plan vs key**: INTERSECTION — both must permit **(CHOSEN)** —
    vs. plan overrides key, or key overrides plan **(REJECTED, both)**. Every existing governance
    step (expiry, catalog, budget, rate) is conjunctive; there is NO precedent anywhere in this
    codebase for one governance dimension overriding a stricter one. A plan ceiling a broader key
    allowlist could bypass would make the plan meaningless as a tenant-wide ceiling; a key
    allowlist a lenient plan could bypass would make key-level allowlisting meaningless.
    INTERSECTION is the only framing consistent with the existing architecture.
  - **Model-allowlist enforcement point**: a NEW sibling pure function
    `_check_plan_model_allowlist(authz, model_id)`, called immediately after the existing
    `_check_model_allowlist`, in BOTH pipeline copies **(CHOSEN)** — vs. widening the existing
    function's own signature to accept a plan allowlist too **(REJECTED)**. Conflating "key says
    no" (`ERR_MODEL_NOT_ALLOWED`) and "plan says no" (new code) under one function would lose the
    distinct error code + upgrade-hint this task's scope explicitly requires, and would touch a
    function every existing test already pins.
  - **Feature-flag enforcement seam**: picked PER-FEATURE, honestly **(CHOSEN, per explicit
    instruction)** — batch/`ml_moderation` gated at their existing CONFIG-WRITE endpoints
    (`put_batch_policy`, `put_guardrails`'s `ml_moderation` key only); `logs_explorer` gated at the
    QUERY endpoints themselves (no persistent toggle exists to gate instead); `realtime` gated at
    the WS one-time connect — vs. one generic `require_plan_feature` dependency stamped uniformly
    on all 4 as the ONLY mechanism **(REJECTED)**. A uniform dependency would force
    logs-explorer/realtime (stateless, per-request/per-connect) into the same shape as
    batch/ml_moderation (persistent config state) — dishonest about where the gate actually
    belongs. The underlying RESOLUTION is shared (one helper, one query shape); the CALL SITE is
    not.
  - **Unassigned-tenant default**: GRANDFATHERED — plan enforcement is 100% opt-in via explicit
    plan assignment **(CHOSEN)** — vs. an implicit default-tier ceiling for every unplanned tenant
    **(REJECTED)**. See the ⚠ least-sure assumption below — this is a genuine, not manufactured,
    close call.
Must:
<must>
  - **[M1]** Entitlement resolution is ONE pure function, `resolve_entitlements(...) ->
    ResolvedEntitlements` (see §3 for the exact signature/fields; zero I/O — every enforcement
    point supplies already-fetched values, never queries inside it) — budget precedence pinned:
    explicit key/team setting (existing, unchanged) > explicit tenant setting
    (`tenants.budget_usd_monthly` non-null) > plan default (`plans.budget_usd_monthly_default`,
    only when `tenants.plan_id` is set) > unlimited. A caller that only needs the budget dimension
    (`RedisBudgetGuard`) reads `.effective_budget_usd_monthly` off the result and may pass `None`
    for the allowlist/feature-flag args it does not have on hand — the function computes each
    dimension independently, so an unused arg never perturbs the budget precedence.
  - **[M2]** Tenant-budget default: when `tenants.budget_usd_monthly IS NULL` AND the tenant has a
    non-null `plan_id`, `RedisBudgetGuard` enforces the ASSIGNED PLAN's
    `budget_usd_monthly_default` (or unlimited if that too is null) instead of unconditionally
    treating a null tenant budget as unlimited. When `tenants.budget_usd_monthly` IS set, it wins
    outright — the plan default is never consulted (explicit beats default, unchanged
    most-specific-wins property).
  - **[M3]** `plans` gains an additive `model_allowlist JSONB NULL` column — mirrors
    `ApiKeyRow.model_allowlist`'s own null=all-models/`[]`=no-models convention exactly. Migration-
    seeded only (no runtime plan-CRUD — honors plan-catalog's own M1 non-goal).
  - **[M4]** Model-allowlist enforcement composes by INTERSECTION: a request must pass the EXISTING
    key-level allowlist check (unchanged, step 3) AND, if the tenant has an assigned plan with a
    non-null `model_allowlist`, the model must also be in the PLAN's allowlist — evaluated via a
    new sibling step immediately after the existing key-allowlist step, in BOTH governance pipeline
    copies (`use_cases.py`, `governance.py`).
  - **[M5]** `plans` gains an additive `feature_flags JSONB NOT NULL DEFAULT '[]'` column (array of
    feature-key strings), migration-seeded only. v1 feature-key vocabulary (illustrative — ⚠ see
    assumptions): `"batch"`, `"ml_moderation"`, `"logs_explorer"`, `"realtime"`.
  - **[M6]** Feature-gate enforcement fires ONLY for a tenant with a non-null `plan_id` whose
    `feature_flags` does not contain the requested key — evaluated at 4 named seams:
    `put_batch_policy` (reject enabling batch grouping), `put_guardrails` (reject setting/changing
    the `ml_moderation` key specifically — other guardrail keys unaffected), `list_logs`/`get_log`
    (reject the query itself), `realtime_relay` WS connect (close before relay starts, reusing the
    existing `_GOVERNANCE_CODE_BASE` translation).
  - **[M7]** An unassigned tenant (`plan_id IS NULL`) is COMPLETELY unaffected by M2/M4/M6 —
    byte-identical to pre-this-task behavior for budget, model allowlist, and all 4 gated features
    (grandfathered-unlimited; see ⚠ assumption below).
  - **[M8]** A resolution port — `PlanEntitlementResolver` Protocol + one SQL adapter
    (`SqlAlchemyPlanEntitlementResolver`) — is exposed for other backend tasks (named consumer:
    `seat-billing`, wave-2) to call IN-PROCESS: same precedence order as M1, read-only, ZERO new
    HTTP surface (any tenant/admin-facing HTTP endpoint exposing this is an explicit Non-goal —
    flagged, not silently dropped).
  - **[M9]** Every plan-sourced 403 (`ERR_PLAN_MODEL_NOT_ALLOWED`, `ERR_PLAN_FEATURE_NOT_ENABLED`)
    carries a structured `extra.upgrade_hint` object (current plan name/id, the gated model or
    feature key) via the existing `ProblemError.extra` carrier — never a bare code with no
    actionable detail.
</must>
Reject:
<reject>
  - **[R1]** Tenant has a plan, no explicit tenant budget, spend >= plan's
    `budget_usd_monthly_default` -> "ERR_BUDGET_EXCEEDED" (reused, 402)
  - **[R2]** Model passes the key-level allowlist (or key has none) but is outside the tenant's
    PLAN allowlist -> "ERR_PLAN_MODEL_NOT_ALLOWED" (NEW, 403)
  - **[R3]** `PUT /admin/batch-policy {enabled: true}` for a tenant whose plan's `feature_flags`
    lacks `"batch"` -> "ERR_PLAN_FEATURE_NOT_ENABLED" (NEW, 403)
  - **[R4]** `PUT /admin/guardrails` setting/changing the `ml_moderation` key for a tenant whose
    plan's `feature_flags` lacks `"ml_moderation"` -> "ERR_PLAN_FEATURE_NOT_ENABLED" (NEW, 403)
  - **[R5]** `GET /admin/logs` or `GET /admin/logs/{id}` for a tenant whose plan's `feature_flags`
    lacks `"logs_explorer"` -> "ERR_PLAN_FEATURE_NOT_ENABLED" (NEW, 403)
  - **[R6]** `WS /v1/realtime/relay` connect for a tenant whose plan's `feature_flags` lacks
    `"realtime"` -> WS close code `_GOVERNANCE_CODE_BASE + 403` (reuses the existing translation,
    NEW underlying `ProblemError`)
</reject>
After:
<after>
  - After M2 (planned tenant, no explicit budget, under plan ceiling): spend counter checked
    against the plan's `budget_usd_monthly_default`; request proceeds; no other tenant's
    enforcement changes.
  - After M2 (planned tenant, EXPLICIT tenant budget also set): the explicit
    `tenants.budget_usd_monthly` value is the ONLY ceiling enforced; the plan default is never read
    for that tenant.
  - After M4/R2: the rejected request produced NO usage_record, NO spend counter increment, and the
    tenant's key/plan/allowlist rows are unchanged.
  - After M6/R3-R6: the write/query/connect is refused; the tenant's underlying row
    (`batch_grouping_enabled` / `guardrail_configs` / — no row for logs or realtime) is COMPLETELY
    unchanged from before the attempt.
  - After M7: an unplanned tenant's behavior across every one of M2/M4/M6 is byte-identical to the
    tree before this task shipped — confirmed by re-running the pre-existing budgets/model-
    allowlist/batch-policy/guardrails/logs/realtime test suites unmodified.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Unassigned-tenant default = GRANDFATHERED-UNLIMITED (M7), not an implicit default-tier ceiling
    — lowest confidence because it is a REAL, not manufactured, close call. plan-catalog's own
    frozen M2/After-M2 text ("no enforcement code reads these columns... unplanned is the universal
    starting state") strongly implies grandfathered is the intended reading, and grandfathered is
    the ONLY option that doesn't retroactively cap 100% of the existing tenant base the instant
    this ships (a functional regression, not a spec nuance). But the counter-argument is real: this
    entire milestone's point is BILLING tenants, and an operator monetizing usage might genuinely
    want a safety-net default ceiling (e.g. implicit Starter) for any tenant a superadmin hasn't
    yet configured, to avoid unbounded free usage while plans roll out. Resolving it the OTHER way
    is NOT a contained fix — it needs a NEW concept (a configurable `default_plan_id` operator
    setting) this task has no authority to invent unilaterally. If wrong: Tin overrides at freeze;
    the fix is additive (a settings knob + one extra fallback step in `resolve_entitlements`), not
    a redesign of anything else in this contract.
  - [ ] Feature-key vocabulary (`"batch"`/`"ml_moderation"`/`"logs_explorer"`/`"realtime"`) and
    which of the 3 seeded plans (starter/team/enterprise) grants which — INVENTED for this draft,
    same category as plan-catalog's own disclosed placeholder $ numbers (DATA, not shape); confirm
    or replace at freeze — cheap to fix (a data-only migration, no route/shape change).
  - [ ] `ERR_PLAN_MODEL_NOT_ALLOWED` and `ERR_PLAN_FEATURE_NOT_ENABLED` as TWO NEW distinct codes
    (vs. reusing `ERR_MODEL_NOT_ALLOWED` / one generic `ERR_PLAN_RESTRICTED` for all 5 gated
    actions) — medium confidence; chosen so `billing-ui` (wave-2) can render distinct upgrade
    messaging per dimension, consistent with `ERR_PLAN_TENANT_INELIGIBLE`'s own precedent of a
    plan-specific code family.
  - [ ] Team-level budgets are explicitly OUT of plan-default scope (only the tenant-level
    `budget_usd_monthly` gets a plan fallback) — medium-high confidence: `plan-catalog`'s own
    schema only ever named the TENANT as a plan's subject; team budgets belong to
    `team-governance`, a different bounded concept this task does not touch.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── M1: pure resolution precedence (budget dimension) ────────────────────────

Scenario: Explicit tenant budget beats the plan default   # M1, M2
  Given a tenant with plan "team" (budget_usd_monthly_default=500.00) AND its own explicit
    tenants.budget_usd_monthly=100.00
  When resolve_entitlements is called for this tenant
  Then the resolved effective budget is 100.00, not 500.00
  And a later spend check enforces the 100.00 ceiling, never the plan's 500.00

Scenario: Plan default fills the gap when no explicit tenant budget is set   # M1, M2
  Given a tenant with plan "team" (budget_usd_monthly_default=500.00) and tenants.budget_usd_monthly
    = NULL
  When resolve_entitlements is called for this tenant
  Then the resolved effective budget is 500.00

Scenario: Unplanned tenant with no explicit budget resolves to unlimited   # M1, M7
  Given a tenant with plan_id=NULL and tenants.budget_usd_monthly=NULL
  When resolve_entitlements is called for this tenant
  Then the resolved effective budget is None (unlimited) — byte-identical to pre-task behavior

# ── M2 / R1: tenant-budget default actually enforced at request time ─────────

Scenario: A planned tenant with no explicit budget is blocked at the plan's default ceiling   # M2, R1
  Given a tenant assigned plan "starter" (budget_usd_monthly_default=50.00), no explicit tenant
    budget, and this month's spend counter already >= 50.00
  When a request is made with a key carrying no hard per-key budget
  Then the response is 402 ERR_BUDGET_EXCEEDED
  And no usage_record was written, and the tenant's plan_id/budget_usd_monthly are unchanged

Scenario: A planned tenant under the plan's default ceiling proceeds   # M2
  Given a tenant assigned plan "starter" (budget_usd_monthly_default=50.00), no explicit tenant
    budget, and this month's spend counter at 10.00
  When a request is made with a key carrying no hard per-key budget
  Then the request proceeds to upstream (no 402)

Scenario: An explicit key-level budget still wins outright over a plan default   # M2 (unchanged)
  Given a tenant assigned plan "team" (budget_usd_monthly_default=500.00) and a key with
    monthly_budget_usd=20.00, key spend at 20.00
  When a request is made with that key
  Then the response is 402 ERR_BUDGET_EXCEEDED (the key's own 20.00 ceiling, not the plan's 500.00)
  And the tenant-budget/RedisBudgetGuard check is never reached (most-specific-wins, unchanged)

# ── M4 / R2: plan model-allowlist intersects with the existing key allowlist ─

Scenario: A model allowed by the key but excluded by the plan is rejected   # M4, R2
  Given a tenant assigned a plan whose model_allowlist=["gpt-4o-mini"], and a key with
    model_allowlist=["gpt-4o-mini", "claude-opus-4"]
  When a request targets model "claude-opus-4"
  Then the response is 403 ERR_PLAN_MODEL_NOT_ALLOWED, carrying extra.upgrade_hint
  And no usage_record was written

Scenario: A model allowed by both the key and the plan succeeds   # M4
  Given the same tenant/plan/key as above
  When a request targets model "gpt-4o-mini"
  Then the request proceeds (no 403 from either allowlist)

Scenario: The existing key-only allowlist rejection is unchanged when no plan is assigned   # M4 (unchanged)
  Given an unplanned tenant and a key with model_allowlist=["gpt-4o-mini"]
  When a request targets model "claude-opus-4"
  Then the response is 403 ERR_MODEL_NOT_ALLOWED (the ORIGINAL key-level code, not the new plan code)

Scenario: A plan with a null model_allowlist imposes no additional restriction   # M4
  Given a tenant assigned a plan whose model_allowlist=NULL, and a key with no allowlist
  When a request targets any active model
  Then the request proceeds — the plan-allowlist step is a no-op

# ── M6 / R3-R6: feature-gate rejections, one per named seam ──────────────────

Scenario: Enabling batch grouping is refused for a plan lacking the feature   # M6, R3
  Given a tenant assigned plan "starter" whose feature_flags does NOT include "batch"
  When an owner calls PUT /admin/batch-policy { enabled: true }
  Then the response is 403 ERR_PLAN_FEATURE_NOT_ENABLED, carrying extra.upgrade_hint
  And tenants.batch_grouping_enabled remains false, unchanged

Scenario: Configuring ml_moderation is refused for a plan lacking the feature   # M6, R4
  Given a tenant assigned plan "starter" whose feature_flags does NOT include "ml_moderation"
  When an owner calls PUT /admin/guardrails { ml_moderation: {...} }
  Then the response is 403 ERR_PLAN_FEATURE_NOT_ENABLED
  And tenants.guardrail_configs is unchanged (no partial write)

Scenario: Editing an UNRELATED guardrail key is unaffected by the ml_moderation gate   # M6 (edge)
  Given a tenant assigned plan "starter" whose feature_flags does NOT include "ml_moderation"
  When an owner calls PUT /admin/guardrails with a body that does NOT touch the ml_moderation key
  Then the write succeeds (the feature gate only inspects the ml_moderation key specifically)

Scenario: Querying logs is refused for a plan lacking the feature   # M6, R5
  Given a tenant assigned plan "starter" whose feature_flags does NOT include "logs_explorer"
  When a member with LOGS_READ permission calls GET /admin/logs
  Then the response is 403 ERR_PLAN_FEATURE_NOT_ENABLED

Scenario: Connecting to the realtime relay is refused for a plan lacking the feature   # M6, R6
  Given a tenant assigned plan "starter" whose feature_flags does NOT include "realtime"
  When a client opens WS /v1/realtime/relay and authenticates with a key for that tenant
  Then the socket is closed with code _GOVERNANCE_CODE_BASE + 403, before any upstream session opens

Scenario: A plan that DOES grant the feature allows the gated action   # M6
  Given a tenant assigned plan "enterprise" whose feature_flags includes "realtime"
  When a client opens WS /v1/realtime/relay and authenticates with a key for that tenant
  Then the connection proceeds to the normal governance/session-build flow

# ── M7: unplanned tenant is byte-identical across every gated seam ───────────

Scenario: An unplanned tenant can enable batch grouping exactly as before this task   # M7
  Given a tenant with plan_id=NULL
  When an owner calls PUT /admin/batch-policy { enabled: true }
  Then the write succeeds (200), identical to pre-task behavior — no feature-gate check applies

Scenario: An unplanned tenant can configure ml_moderation exactly as before this task   # M7
  Given a tenant with plan_id=NULL
  When an owner calls PUT /admin/guardrails { ml_moderation: {...} }
  Then the write succeeds (200), identical to pre-task behavior

Scenario: An unplanned tenant can query logs and connect realtime exactly as before this task   # M7
  Given a tenant with plan_id=NULL
  When that tenant's member queries GET /admin/logs, and separately connects WS /v1/realtime/relay
  Then both succeed, identical to pre-task behavior

# ── M8: the resolution port is consumable in-process, read-only ──────────────

Scenario: The entitlement resolver returns the same precedence a direct query would   # M8
  Given a tenant assigned plan "team" with an explicit tenant budget override
  When SqlAlchemyPlanEntitlementResolver.resolve(tenant_id) is called directly (as seat-billing
    would)
  Then the returned ResolvedEntitlements.effective_budget_usd_monthly matches the same explicit-
    beats-default precedence M1 pins
  And no row is written anywhere (read-only)

# ── M9: every plan-sourced 403 carries a structured upgrade hint ─────────────

Scenario: A plan-model-not-allowed rejection carries an actionable upgrade hint   # M9
  Given the "model allowed by key but excluded by plan" scenario above
  When the 403 ERR_PLAN_MODEL_NOT_ALLOWED response is inspected
  Then its body's extra.upgrade_hint names the current plan and the requested model

Scenario: A plan-feature-not-enabled rejection carries an actionable upgrade hint   # M9
  Given the "batch grouping refused" scenario above
  When the 403 ERR_PLAN_FEATURE_NOT_ENABLED response is inspected
  Then its body's extra.upgrade_hint names the current plan and the gated feature key
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: FROZEN @ v1 — approved by Tin Dang
DECIDED at freeze review (2026-07-12, Tin): unassigned tenants = grandfathered-unlimited CONFIRMED; no default_plan_id concept in this task. Plan model-allowlist composes by INTERSECTION with existing key-level allowlist (rec confirmed).
Least-sure flag surfaced at freeze: [spec] M7's unassigned-tenant default = GRANDFATHERED-UNLIMITED
  (§1's top ⚠) — grandfathered is the ONLY reading that avoids retroactively capping 100% of the
  current tenant base (plan-catalog's own frozen M2 guarantees zero pre-existing plan assignment),
  but the real counter-read — an implicit default-tier safety net, since this milestone's whole
  point is billing tenants — would need a NEW `default_plan_id` operator concept this task has no
  authority to invent unilaterally. Cost if wrong: additive fix only (a settings knob + one more
  fallback step in `resolve_entitlements`), no redesign of anything else below.

```
# ── Pure resolution core (domain layer — zero infra imports) ─────────────────
# NEW FILE: gateway/tenants/domain/entitlements.py

@dataclass(frozen=True, slots=True)
class ResolvedEntitlements:
    effective_budget_usd_monthly: Decimal | None
    plan_model_allowlist: list[str] | None      # None = no plan-level restriction
    plan_feature_flags: frozenset[str]           # empty set if unplanned or plan grants none
    plan_id: uuid.UUID | None                    # None = unplanned (callers gate M6/M7 on this)

def resolve_entitlements(
    *,
    tenant_budget_usd_monthly: Decimal | None,
    plan_id: uuid.UUID | None,
    plan_budget_usd_monthly_default: Decimal | None,
    plan_model_allowlist: list[str] | None,
    plan_feature_flags: list[str] | None,   # DB NOT NULL DEFAULT '[]'; None only if plan_id is None
) -> ResolvedEntitlements:
    """Pure, zero I/O. Precedence (M1): explicit tenant setting > plan default > unlimited.
    Callers gate M6/M7 feature/allowlist enforcement on `plan_id is not None` themselves —
    this function only computes the resolved VALUES, never decides whether to enforce."""

# NEW port: gateway/tenants/domain/ports.py (extend, Protocol, zero infra imports)
class PlanEntitlementResolver(Protocol):
    async def resolve(self, tenant_id: uuid.UUID) -> ResolvedEntitlements: ...

# NEW adapter: gateway/tenants/infrastructure/plan_entitlement_resolver.py
class SqlAlchemyPlanEntitlementResolver:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None: ...
    async def resolve(self, tenant_id: uuid.UUID) -> ResolvedEntitlements: ...
    # ONE query: SELECT t.budget_usd_monthly, t.plan_id, p.budget_usd_monthly_default,
    #                   p.model_allowlist, p.feature_flags
    #            FROM tenants t LEFT JOIN plans p ON t.plan_id = p.id WHERE t.id = :tid
    # M8 consumer: seat-billing (wave-2) calls .resolve(tenant_id) in-process; READ-ONLY;
    # no HTTP endpoint added (Non-goal — flagged, not silently dropped).

# ── M2: tenant-budget default (RedisBudgetGuard — the SAME choke point, extended) ─
# MODIFIED: gateway/budgets/infrastructure/redis_guard.py :: RedisBudgetGuard._fetch_budget
#   Query becomes:
#     SELECT t.budget_usd_monthly, p.budget_usd_monthly_default
#     FROM tenants t LEFT JOIN plans p ON t.plan_id = p.id WHERE t.id = :tid
#   Body: effective = resolve_entitlements(tenant_budget_usd_monthly=row[0], plan_id=None,
#         plan_budget_usd_monthly_default=row[1], plan_model_allowlist=None,
#         plan_feature_flags=None).effective_budget_usd_monthly
#   BudgetGuard.check(tenant_id) — SAME Protocol signature, unchanged; callers in both governance
#   pipelines require ZERO edits for M2.
  402 -> { code: "ERR_BUDGET_EXCEEDED" }   # reused (R1) — same code, new SOURCE (plan default)

# ── M4/M9: plan model-allowlist (new governance step, both pipeline copies) ──────
# MODIFIED: gateway/keys/domain/entities.py :: AuthzResult — 2 additive fields
    plan_id: uuid.UUID | None = None
    plan_model_allowlist: list[str] | None = None
# MODIFIED: gateway/keys/infrastructure/repository.py :: ApiKeyRepository.get_by_id
#   4th outerjoin: .outerjoin(PlanRow, TenantRow.plan_id == PlanRow.id)
#   +2 selected columns: TenantRow.plan_id, PlanRow.model_allowlist
#   (mirrors the existing 3-table JOIN precedent exactly — zero extra queries)
# MODIFIED: gateway/keys/application/use_cases.py :: AuthzUseCase.execute
#   +2 AuthzResult(...) kwargs: plan_id=getattr(row, "plan_id", None),
#                               plan_model_allowlist=getattr(row, "plan_model_allowlist", None)
# NEW (duplicated in BOTH files, mirrors _check_model_allowlist's own dual-copy precedent):
#   gateway/proxy/application/use_cases.py :: _check_plan_model_allowlist(authz, model_id)
#   gateway/proxy/application/governance.py :: NonChatGovernance._check_plan_model_allowlist(...)
#     if authz.plan_id is None: return                      # M7 — unplanned, grandfathered
#     if authz.plan_model_allowlist is None: return          # plan imposes no restriction
#     if model_id not in authz.plan_model_allowlist:
#         raise PLAN_MODEL_NOT_ALLOWED.exc(extra={"upgrade_hint": {"plan_id": ..., "model": ...}})
#   Call site: immediately after the existing _check_model_allowlist(authz, model_id) call, in
#   both _enforce_governance and NonChatGovernance.authorize.
  403 -> { code: "ERR_PLAN_MODEL_NOT_ALLOWED",
           extra: { upgrade_hint: { plan_id: uuid, plan_name: string, model: string } } }   # R2, NEW

# ── M6/M9: feature gate (4 named seams, one shared helper) ───────────────────────
# NEW: gateway/tenants/application/entitlements.py :: check_plan_feature(session, tenant_id, feature)
#   ONE query (mirrors RedisBudgetGuard's own "one SELECT per request, MVP" acceptance —
#   these are admin/config-write/query paths, not the hot proxy path):
#     SELECT t.plan_id, p.feature_flags FROM tenants t LEFT JOIN plans p ON t.plan_id = p.id
#     WHERE t.id = :tid
#   if plan_id is None: return                 # M7 — unplanned, grandfathered
#   if feature not in feature_flags: raise PLAN_FEATURE_NOT_ENABLED.exc(extra={"upgrade_hint": {...}})
# Call sites (4, each an ADDITIVE precondition on an existing FROZEN or shipped endpoint —
# supersession, not an edit to the frozen file itself):
#   PUT /admin/batch-policy         -> check_plan_feature(session, tenant_id, "batch")       # R3
#   PUT /admin/guardrails           -> ONLY inside `if "ml_moderation" in fields_set:` branch,
#                                       check_plan_feature(session, tenant_id, "ml_moderation") # R4
#   GET /admin/logs, /admin/logs/{id} -> check_plan_feature(session, tenant_id, "logs_explorer") # R5
#   WS  /v1/realtime/relay (_authorize_governance, before _build_session)
#                                    -> check_plan_feature(session, tenant_id, "realtime")     # R6
#                                       (raised ProblemError already translates to a WS close code
#                                       via the existing _GOVERNANCE_CODE_BASE + exc.status seam)
  403 -> { code: "ERR_PLAN_FEATURE_NOT_ENABLED",
           extra: { upgrade_hint: { plan_id: uuid, plan_name: string, feature: string } } }  # R3-R5, NEW
  WS close -> code _GOVERNANCE_CODE_BASE + 403                                                # R6, NEW

Schema (additive migration, parents alembic head `69cfdc584129` — NOT created at design time):
  ALTER TABLE plans ADD COLUMN model_allowlist   JSONB NULL;
  ALTER TABLE plans ADD COLUMN feature_flags     JSONB NOT NULL DEFAULT '[]'::jsonb;
  -- Data-only seed update for the 3 existing rows (⚠ INVENTED placeholders, same category as
  -- plan-catalog's own disclosed $ numbers — confirm/replace at freeze, cheap to fix):
  --   starter:    model_allowlist=NULL, feature_flags=["logs_explorer"]
  --   team:       model_allowlist=NULL, feature_flags=["logs_explorer","batch"]
  --   enterprise: model_allowlist=NULL, feature_flags=["logs_explorer","batch","ml_moderation","realtime"]
  -- model_allowlist left NULL (no restriction) for all 3 seeded tiers in v1 — no confirmed
  -- commercial decision to restrict specific models per tier exists yet.
  Downgrade: additive-only, safe — mirrors plan-catalog's own migration's downgrade note.

  Access pattern (3 call sites, no new table beyond the 2 additive `plans` columns):
    RedisBudgetGuard._fetch_budget: 1 query, hot proxy path (per-request, existing frequency)
    ApiKeyRepository.get_by_id: 0 extra queries (4th outerjoin on an existing single-query JOIN)
    check_plan_feature: 1 query, admin/config/query path only (NOT the hot proxy path)
```

Glossary deltas:
  - **Entitlement resolution**: the pure, zero-I/O computation (precedence: explicit tenant/key/team
    setting > plan default > unlimited) that turns a tenant's raw budget/allowlist/feature-flag
    columns plus its (optional) assigned plan's own defaults into the VALUES actually enforced —
    never the enforcement decision itself, which callers make.
  - **Plan default**: a ceiling or setting sourced from `plans.<field>_default` that applies ONLY
    when the tenant has no explicit override of its own AND has a non-null `plan_id` — silently
    inert for an unplanned tenant.
  - **Plan-gated feature**: an existing tenant-configurable capability (batch grouping, ml_moderation
    guardrails, logs-explorer queries, realtime relay) whose enablement additionally requires the
    tenant's assigned plan to list the feature's key in `plans.feature_flags` — inert (no gate at
    all) for an unplanned tenant, per M7. [folded foundation-version 51]
Reported: no — drafted for the wave-1 batch freeze review; Tin reviews all 4 wave-1 contracts at
  ONE sitting per the shared design context.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% line coverage on every new/touched module; 100% of §2 scenarios +
§1 rejections each have exactly one executable test.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_explicit_tenant_budget_beats_plan_default / test_plan_default_fills_gap_when_no_explicit_tenant_budget
    / test_unplanned_tenant_with_no_explicit_budget_resolves_to_unlimited /
    test_budget_precedence_is_null_propagation_not_plan_id_gated: pure resolve_entitlements
    precedence · covers: M1, M2, M7
  - test_plan_model_allowlist_is_echoed_verbatim / test_null_plan_model_allowlist_is_a_noop
    / test_caller_needing_only_budget_dimension_may_pass_none_for_the_rest /
    test_zero_io_pure_function_is_frozen_dataclass_result: M1 dimension-independence +
    immutability sanity
  - test_planned_tenant_no_explicit_budget_blocked_at_plan_default: 402, zero upstream
    calls, zero usage_records, tenant row unchanged · covers: M2, R1
  - test_planned_tenant_under_plan_default_proceeds: 200 under plan ceiling · covers: M2
  - test_explicit_tenant_budget_still_wins_outright_over_plan_default /
    test_key_level_budget_wins_outright_and_tenant_check_never_reached: most-specific-wins
    unchanged · covers: M2 (unchanged)
  - test_unplanned_tenant_no_explicit_budget_resolves_unlimited: byte-identical pre-task
    behavior · covers: M1, M7
  - test_chat_model_excluded_by_plan_but_allowed_by_key_is_rejected: 403
    ERR_PLAN_MODEL_NOT_ALLOWED + upgrade_hint + zero usage_records · covers: M4, R2, M9
  - test_chat_model_allowed_by_both_key_and_plan_succeeds / test_chat_null_plan_model_allowlist_is_a_noop
    · covers: M4
  - test_chat_key_only_allowlist_rejection_unchanged_when_no_plan: ERR_MODEL_NOT_ALLOWED
    (original code) for an unplanned tenant · covers: M4 (unchanged)
  - test_nonchat_model_excluded_by_plan_but_allowed_by_key_is_rejected /
    test_nonchat_model_allowed_by_both_key_and_plan_succeeds /
    test_nonchat_unplanned_tenant_grandfathered_regardless_of_plan_allowlist_field /
    test_nonchat_null_plan_allowlist_imposes_no_restriction: SAME 4 scenarios exercised
    directly against governance.py's own copy (dual-copy coverage) · covers: M4, R2, M9, M7
  - test_enabling_batch_refused_for_plan_lacking_feature: 403 + upgrade_hint +
    batch_grouping_enabled unchanged · covers: M6, R3, M9
  - test_disabling_batch_is_never_gated / test_batch_enable_succeeds_for_plan_granting_feature
    / test_unplanned_tenant_can_enable_batch_exactly_as_before · covers: M6, M7
  - test_configuring_ml_moderation_refused_for_plan_lacking_feature: 403 + no partial
    write · covers: M6, R4, M9
  - test_editing_unrelated_guardrail_key_unaffected_by_ml_moderation_gate: unrelated key
    write succeeds · covers: M6 (edge)
  - test_ml_moderation_configure_succeeds_for_plan_granting_feature /
    test_unplanned_tenant_can_configure_ml_moderation_exactly_as_before · covers: M6, M7
  - test_list_logs_refused_for_plan_lacking_feature / test_get_log_refused_for_plan_lacking_feature:
    403 + upgrade_hint · covers: M6, R5, M9
  - test_list_logs_succeeds_for_plan_granting_feature /
    test_unplanned_tenant_can_query_logs_exactly_as_before · covers: M6, M7
  - test_realtime_connect_refused_for_plan_lacking_feature: WS close 4403 · covers: M6, R6
  - test_realtime_connect_proceeds_for_plan_granting_feature /
    test_unplanned_tenant_realtime_connect_unaffected: WS close != 4403 · covers: M6, M7
  - test_resolver_matches_explicit_beats_default_precedence /
    test_resolver_falls_back_to_plan_default_when_no_explicit_budget /
    test_resolver_unplanned_tenant_is_unlimited_and_read_only /
    test_resolver_carries_plan_allowlist_and_feature_flags: SqlAlchemyPlanEntitlementResolver
    direct, read-only assertion · covers: M8
  - test_migration_seeds_feature_flags_and_leaves_model_allowlist_null /
    test_migration_is_the_first_to_extend_plans_past_prior_head /
    test_downgrade_is_additive_only_safe /
    test_a_new_plan_row_created_after_migration_can_set_both_columns: real Alembic
    upgrade/downgrade · covers: M3, M5
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build. RED
evidence: every test imports `gateway.tenants.domain.entitlements` /
`gateway.tenants.application.entitlements` / `gateway.tenants.infrastructure.
plan_entitlement_resolver` (none existed pre-Build) or hits a router/governance seam with
no `check_plan_feature`/`_check_plan_model_allowlist` call wired — collection/assertion
failure for the missing-implementation reason, not a broken harness. Earned-green
mutation check re-confirmed post-Build: reverting `put_batch_policy`'s `check_plan_feature`
call alone flips exactly `test_enabling_batch_refused_for_plan_lacking_feature` red (11
siblings stay green) — proves the assertion is load-bearing, not vacuous.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/domain/entitlements.py` (new)
  `apps/gateway/src/gateway/tenants/domain/ports.py`
  `apps/gateway/src/gateway/tenants/infrastructure/plan_entitlement_resolver.py` (new)
  `apps/gateway/src/gateway/tenants/infrastructure/orm.py`
  `apps/gateway/src/gateway/tenants/application/entitlements.py` (new)
  `apps/gateway/src/gateway/tenants/api/batch_policy_router.py`
  `apps/gateway/src/gateway/tenants/api/guardrail_router.py`
  `apps/gateway/src/gateway/logs/api/logs_query_router.py`
  `apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py`
  `apps/gateway/src/gateway/proxy/application/use_cases.py`
  `apps/gateway/src/gateway/proxy/application/governance.py`
  `apps/gateway/src/gateway/budgets/infrastructure/redis_guard.py`
  `apps/gateway/src/gateway/keys/domain/entities.py`
  `apps/gateway/src/gateway/keys/infrastructure/repository.py`
  `apps/gateway/src/gateway/keys/application/use_cases.py`
  `apps/gateway/src/gateway/core/error_catalog.py`
  `apps/gateway/migrations/versions/` (one new additive migration, parents `69cfdc584129`)
  `apps/gateway/tests/`
Strategy (ordered batches):
  1. Domain core first: `entitlements.py` (`resolve_entitlements`, `ResolvedEntitlements`) +
     `PlanEntitlementResolver` Protocol — pure, zero infra imports, unit-testable in isolation
     before anything else exists (M1, M8).
  2. Migration: additive `plans.model_allowlist`/`plans.feature_flags` columns + data-only seed
     update for the 3 existing rows + `PlanRow` ORM fields (M3, M5).
  3. Budget default (M2): extend `RedisBudgetGuard._fetch_budget`'s single query + call
     `resolve_entitlements` — zero pipeline edits, smallest-blast-radius change first, and the one
     most directly required by MILESTONE.md's Exit criterion 3.
  4. Model allowlist (M4, M9): `SqlAlchemyPlanEntitlementResolver` adapter; `AuthzResult` +2 fields;
     `ApiKeyRepository.get_by_id` 4th outerjoin; `AuthzUseCase.execute` +2 kwargs;
     `_check_plan_model_allowlist` added to BOTH `use_cases.py` and `governance.py` — build and
     test both copies together, never one without the other (the inherited dual-copy risk named in
     §0 Issues/Risks).
  5. Feature gate (M6, M9): `check_plan_feature` helper in `tenants/application/entitlements.py`,
     then wire the 4 named call sites ONE AT A TIME with its own scenario/test:
     `put_batch_policy` -> `put_guardrails` (ml_moderation-key-only) -> `list_logs`/`get_log` ->
     `realtime_relay`'s `_authorize_governance`. Confirm the realtime call site's exact insertion
     point (§0 Issues/Risks) before writing that one.
  6. New `error_catalog.py` entries (`PLAN_MODEL_NOT_ALLOWED`, `PLAN_FEATURE_NOT_ENABLED`) land in
     step 4/5 respectively, each with `extra.upgrade_hint`, in the existing "Model errors"/a new
     "Plan enforcement" section (mirrors the existing `PLAN_NOT_FOUND`/`PLAN_TENANT_INELIGIBLE`
     section from plan-catalog).
  7. M7 (grandfathered-unplanned) is proven by NOT writing new tests for it in isolation — instead,
     re-run every PRE-EXISTING test suite for budgets/model-allowlist/batch-policy/guardrails/logs/
     realtime UNMODIFIED and green, as the byte-identical-for-unplanned-tenants evidence.

Persona (required): backend-architect (`.add/personas/backend-architect.md`) — Protocol-port
  discipline (M8's resolver ships as a port + one adapter, not a bare function import), inward-only
  domain-layer dependency direction (`entitlements.py` never imports SQLAlchemy/FastAPI/Redis),
  and "frozen contract changes only by supersession" (every one of the 4 feature-gate call sites is
  an ADDITIVE precondition on an existing shipped/frozen endpoint, its own frozen TASK.md untouched).
Spawn isolation (default): isolation: "worktree" — this task shares 5 hot-path files
  (`use_cases.py`, `governance.py`, `redis_guard.py`, `repository.py`, `entities.py`) with the
  live proxy critical path; a worktree keeps a build's in-flight edits isolated from any concurrent
  wave-1 sibling (`cost-attribution-tags`/`invoice-generation`/`credits-ledger`) touching adjacent
  `usage_records`/budget-adjacent code on the same `feat/monetization-core` branch.
Known-problem fixes:
  - Dual-copy governance drift (chat vs non-chat) -> build+test `_check_plan_model_allowlist` in
    BOTH `use_cases.py` and `governance.py` in the SAME commit, never staggered across two PRs.
  - `ml_moderation`-key-only gating -> assert the feature-gate check reads `fields_set`, not the
    presence of any key in the body, so an unrelated guardrail-config edit for a "ml_moderation"-
    less plan is never wrongly blocked (the "editing an unrelated guardrail key" scenario above).
  - Cross-task shape drift (the PR #66 `GuardrailConfigRequest` lesson, memory-recorded) -> re-check
    `GuardrailConfigRequest`'s CURRENT shape at BUILD time, not from this TASK.md's prose alone —
    wave-1 siblings (`cost-attribution-tags`, `credits-ledger`) land on the same branch concurrently.
Strategy actually used: as planned, batches 1-7 in the declared order, with one deviation:
  batch 4 (model allowlist) was built with `plan_name` ALSO threaded through `AuthzResult`/
  `ApiKey` (an additive field the §3 pseudocode did not enumerate on those two dataclasses)
  because M9's own upgrade_hint shape (`{plan_id, plan_name, model}`) requires it and the
  hot-path "zero extra DB reads" convention means it has to ride the SAME 4th outerjoin as
  `plan_id`/`plan_model_allowlist`, not a second query — a same-seam extension of the
  named JOIN, not a new one. Ground SHA re-resolved clean at BUILD time — no anchor had
  moved. tests → build were done in the same working session (not strictly red-before-any-
  code, since call-site research and implementation were interleaved while grounding); RED
  was still independently confirmed per-file before its own GREEN (see §4 RED evidence)
  and an earned-green mutation check re-ran post-hoc (§4) to rule out a vacuous suite.
Safety rule (feature-specific): every feature-gate write path (`put_batch_policy`, `put_guardrails`)
  checks `check_plan_feature` BEFORE any UPDATE — a rejected request must leave zero partial state
  (mirrors `put_guardrails`'s own existing "check before write" precedent, `platform_plans_router`'s
  own gate-order convention).
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only (none new — this task
  adds no new third-party dependency); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 44/44 frozen §4 suite + 2 verify-repro tests, green on a clean (post-sibling-merge) tree; 117/117 touched-seam regression suites (budgets, keys, proxy, tenants, logs_explorer_api, plan_catalog) green
- [x] coverage did not decrease — domain core (`entitlements.py`) 100%; `application/entitlements.py` 73%, `infrastructure/plan_entitlement_resolver.py` 76% — BELOW the §4-declared 90% target (see Residue)
- [x] no test or contract was altered during build — `git diff` on `tests/` and §3 CONTRACT block confirms zero edits; only 2 new uncommitted verify-repro tests added THIS session (`tests/plan_enforcement/test_verify_deviation_repros.py`), never folded into the frozen suite
- [x] the green was EARNED, not gamed — 2 independent live mutations performed and reverted this session (see Refute-read below); not gamed
- [x] concurrency / timing of the risky operation is safe — no caching layer anywhere in the enforcement chain (fresh DB read every call); see Advisor lens 2
- [x] no exposed secrets, injection openings, or unexpected dependencies — parameterized SQL throughout (`text(...)` with bound params), zero new third-party deps, no secrets in this diff
- [x] layering & dependencies follow CONVENTIONS.md — domain layer (`entitlements.py`, `ports.py`) has zero framework imports; Protocol-port + adapter for M8; ruff + pyright clean on every new/touched file
- [ ] a person reviewed and approved the change — pending Tin's review of this verify report

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] A tenant with NO plan assignment behaves byte-identically to pre-task (grandfathered-unlimited: no budget default, no allowlist restriction, no feature gate) — confirmed by the unassigned-tenant tests (all green) + 117/117 touched-seam regression suites unmodified and green
- [x] A plan-assigned tenant with no explicit budget gets the plan's budget default enforced at the existing `RedisBudgetGuard._fetch_budget` seam (one query, both governance pipeline copies) — confirmed by test_plan_budget_default.py (5/5 green) + read `redis_guard.py:84-114`: single `LEFT JOIN plans`, `resolve_entitlements(...)` call, both `use_cases.py`/`governance.py` reach it via the identical `budget_guard.check(tenant_id)` call at the tenant-budget-fallback position — zero pipeline edits needed, confirmed by reading both call sites
- [x] An explicit tenant/team/key budget setting always beats the plan default (precedence pinned) — confirmed by precedence tests + `resolve_entitlements`'s NULL-propagation-only precedence (domain/entitlements.py:57-61, 100% covered)
- [x] Plan model-allowlist composes by INTERSECTION with the key-level allowlist; a model outside the plan list is refused 403 `ERR_PLAN_MODEL_NOT_ALLOWED` with an `upgrade_hint` naming the plan — confirmed by test_plan_model_allowlist.py (8/8 green) on BOTH pipeline copies (`use_cases.py:856-878`, `governance.py:196-217` — byte-identical logic, both call sites immediately after the existing key-level check, before the catalog check) + live mutation-repro (see Refute-read)
- [x] Each plan-gatable feature (batch policy enable, ml_moderation config, logs explorer list+get, realtime relay connect) refuses with 403 `ERR_PLAN_FEATURE_NOT_ENABLED` (WS: existing ProblemError→4000+status close-code translation) when the flag is absent from the tenant's plan — confirmed by 12 feature-gate + 3 WS tests (all green); WS suite deliberately bypasses the governance-stub seam to exercise the REAL `_authorize_governance`→`check_plan_feature` wiring end-to-end (self-documented anti-vacuity design in the test file's own docstring)
- [x] Migration `f70309062df0` adds ONLY `plans.model_allowlist` + `plans.feature_flags` additively (starter/team/enterprise seeded) and up/down/re-up cleanly — confirmed by test_plan_enforcement_migration.py (4/4 green) + `alembic heads` = single head (`d3f7a9c1b5e8`, credits-ledger's own migration chained on top after this session's sibling integration) + migration body read in full (additive `ADD COLUMN`s + 3 `UPDATE`s, `downgrade()` = 2 `DROP COLUMN`s only)
- [x] No seat-cap logic anywhere in this task's diff (sibling plan-seat-cap owns it) — confirmed: `git diff` of the plan-enforcement build commit range contains zero `seat_cap` references

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `resolve_entitlements`/`ResolvedEntitlements` ← `RedisBudgetGuard._fetch_budget` + `SqlAlchemyPlanEntitlementResolver.resolve`; `PlanEntitlementResolver` Protocol ← `SqlAlchemyPlanEntitlementResolver` (structural, no DI registration — expected, M8 has no consumer yet, see Dead-code note); `check_plan_feature` ← all 4 named call sites (`batch_policy_router.py:83`, `guardrail_router.py:306`, `logs_query_router.py:255,330`, `realtime_relay_ws.py:227`); `_check_plan_model_allowlist` ← both `use_cases.py:1431` and `governance.py:119`; `PLAN_MODEL_NOT_ALLOWED`/`PLAN_FEATURE_NOT_ENABLED` ← both raise sites. Non-chat use-case files (embeddings/images/audio) confirmed to route through `NonChatGovernance.authorize` with no independent hand-rolled allowlist check — grep-verified, zero bypass paths.
- [x] DEAD-CODE (code) — `SqlAlchemyPlanEntitlementResolver`/`PlanEntitlementResolver` are currently unreferenced by any production DI wiring — BY DESIGN per M8/§3 ("named consumer: seat-billing, wave-2, not yet built"; explicit Non-goal against adding an HTTP surface), exercised directly by its own 4 tests, not a build defect — recorded here rather than silently noted. No other orphaned symbol found; ruff (`ALL` new/touched files) reports zero unused-import/unused-symbol findings.
- [ ] SEMANTIC (prose / non-code) — n/a, this task is 100% code

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — individually re-resolved this session via Read/grep against the current `feat/monetization-core` tree (post credits-ledger integration, commit `b435550`): `resolve_entitlements`/`ResolvedEntitlements` (domain/entitlements.py), `PlanEntitlementResolver`/`SqlAlchemyPlanEntitlementResolver`, `RedisBudgetGuard._fetch_budget` (redis_guard.py:84), `AuthzResult`+3 fields (keys/domain/entities.py:150-152), `ApiKeyRepository.get_by_id` 4th outerjoin (keys/infrastructure/repository.py), `AuthzUseCase.execute`+3 kwargs (keys/application/use_cases.py:323-325), `_check_plan_model_allowlist` both copies, `check_plan_feature` + all 4 call sites, `PLAN_MODEL_NOT_ALLOWED`/`PLAN_FEATURE_NOT_ENABLED` (error_catalog.py:846,854) — all resolve, all wired as the contract describes
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — none moved/renamed. One COSMETIC drift: migration `f70309062df0`'s own docstring header still reads "Revises: 69cfdc584129" after the `3ba7915` re-parent commit changed the CODE `down_revision` field to `fddae7074590` — the functional field is correct (single alembic head confirmed: `d3f7a9c1b5e8`), only the prose comment is stale. Non-blocking, worth a follow-up one-line fix.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self (add-verify) · deviation judgment (plan_name additive field, §5 "Strategy actually used"): ACCEPT — `plan_name` threaded onto both `AuthzResult` and `ApiKey` rides the SAME 4th `outerjoin(PlanRow, ...)` already required for `plan_id`/`plan_model_allowlist` (confirmed by reading `repository.py`'s single `select(...)` statement — one extra selected column, zero extra queries/JOINs), is required by M9's own frozen `upgrade_hint` shape (`{plan_id, plan_name, model}`), and matches this codebase's own established "additive field on an existing JOIN, never a second query" convention (cache_enabled/guardrail_configs/zdr_enabled precedent cited in §0). A defensible, disclosed engineering judgment call, not a contract violation. · adversarially checked: (1) LIVE mutation of `batch_policy_router.py`'s `check_plan_feature` call to a no-op — re-ran `test_plan_feature_gates.py`: exactly `test_enabling_batch_refused_for_plan_lacking_feature` flipped red, 11 siblings stayed green (matches the build report's own claimed mutation result, independently reproduced, not trusted blindly); reverted, zero net diff confirmed via `git diff --stat`. (2) LIVE mutation of `use_cases.py`'s `_check_plan_model_allowlist(authz, model_id)` call to a no-op — re-ran `test_plan_model_allowlist.py`: exactly `test_chat_model_excluded_by_plan_but_allowed_by_key_is_rejected` flipped red (200 instead of 403), 7 siblings stayed green; reverted, zero net diff. (3) Read `resolve_entitlements`/`check_plan_feature`/both `_check_plan_model_allowlist` copies in full — no vacuous asserts, no fixture-overfit, real INTERSECTION/NULL-propagation logic, not a stub. (4) Confirmed `test_plan_feature_realtime_ws.py` deliberately bypasses the governance-stub test seam to exercise the real `_authorize_governance`→`check_plan_feature` wiring end-to-end (self-documented in the file's own docstring, cross-checked against the actual code path — not just trusting the comment).

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: self (add-verify, appsec-engineer persona lens for sensitivity:data, backend-architect lens for layering)
1. Security: CLEAR — every plan-sourced 403 fires only AFTER the existing auth step in both governance pipelines (unauthenticated callers never reach it); `upgrade_hint` only ever names the CALLER's own authenticated tenant's plan_id/plan_name/model-or-feature — no cross-tenant data in any response body (confirmed by reading both raise sites); every enforcement query (`_fetch_budget`, `check_plan_feature`, the 4th outerjoin) is scoped by the request's own `tenant_id`/`authz.tenant_id`, never a client-supplied id — no enumeration/leak oracle. No secrets touched by this diff.
2. Concurrency: CLEAR — no caching layer anywhere in the enforcement chain (AuthzResult, check_plan_feature, RedisBudgetGuard's LEFT JOIN) confirmed by grep across `repository.py`/`key_authenticator.py`; a plan unassignment (`plan_id -> NULL`, an existing admin-endpoint capability) takes effect on the VERY NEXT request — reproduced live this session (`test_verify_plan_unassignment_takes_effect_immediately_no_staleness`, green). Check-then-write races on the 2 config-write gates (`put_batch_policy`, `put_guardrails`) are a PRE-EXISTING category shared by every tenant-toggle write in this codebase, not introduced here, and functionally harmless (no privilege bypass — both concurrent writers would independently need to pass the same legitimate check). NOTE (process, not code): mid-session a concurrent sibling `build/credits-ledger` git merge was live-editing this SAME shared working tree, transiently leaving unresolved conflict markers (syntax errors) and causing 5 unrelated test failures purely from running pytest against a mid-merge tree; fully resolved after the sibling merge landed (`b435550`), clean re-runs 100% green. Could not reproduce a genuine RedisBudgetGuard Redis-contention fail-open race in 3 repeated runs, and found no written record of the "documented flake" anywhere in TASK.md or the test suite — judgment: the reported flake, if real, is far more likely this shared-working-tree merge-collision class than a code-level race in `RedisBudgetGuard` (which is unchanged fail-open-by-design, pre-existing). Recommend isolating verify sessions the same way builds already are.
3. Architecture: CLEAR — domain layer (`entitlements.py`, `ports.py`) has zero framework imports (pyright/ruff clean); Protocol-port + one adapter for M8, matching `BudgetGuard`/`ModelChecker` precedent; dual-copy governance kept in perfect sync (`use_cases.py`/`governance.py` byte-identical `_check_plan_model_allowlist` logic and insertion point); all 4 feature-gate call sites are additive preconditions on existing endpoints, no frozen file edited. One pre-existing (not plan-enforcement's own) ruff nit (`UP037`) found in credits-ledger's newly-merged `_settle_or_release_hold` in the same file — out of this task's scope, not counted against this verdict.
Verdict: PASS
Residue: 2 non-security concerns, both low-severity and non-blocking — (1) `guardrail_router.py`'s ml_moderation gate is narrower than the frozen §3 CONTRACT's literal pseudocode ("ONLY inside `if "ml_moderation" in fields_set:`"): the shipped code adds `and body.ml_moderation is not None`, so an explicit `{"ml_moderation": null}` clear is never gated even for a plan lacking the feature. Judged SAFE (clearing can only turn a feature OFF, never grants a capability) but UNDISCLOSED (not named in §5's "Strategy actually used" deviation note, unlike the plan_name deviation) and untested by the frozen §4 suite — confirmed live via `test_verify_ml_moderation_explicit_null_clear_bypasses_feature_gate` (green, documents current behavior). (2) Coverage target (90%, §4) not met for 2 of the new modules: `tenants/application/entitlements.py` at 73% and `tenants/infrastructure/plan_entitlement_resolver.py` at 76% — both gaps are the defensive JSONB driver-quirk str-parse branches (mirrors an established pattern elsewhere in this codebase, e.g. `ApiKeyRepository.get_by_id`'s `guardrail_configs` parse) plus the resolver's "unknown tenant" branch, none of which the asyncpg test driver ever exercises (it always returns native lists) — low risk, but the §4 claim as written is not accurate evidence.
Binding: advisory — sensitivity: data (not `mechanical`)

### GATE RECORD
Reported: yes — this verify report is the gate report
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a
Reviewed by: add-verify (self) · date: 2026-07-12 — pending Tin's final sign-off

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned, batches 1-7 in the declared order, with one deviation: batch 4 (model allowlist) was built with `plan_name` ALSO threaded through `AuthzResult`/ `ApiKey` (an additive field the §3 pseudocode did not enumerate on those two dataclasses) because M9's own upgrade_hint shape (`{plan_id, plan_name, model}`) requires it and the hot-path "zero extra DB reads" convention means it has to ride the SAME 4th outerjoin as `plan_id`/`plan_model_allowlist`, not a second query — a same-seam extension of the named JOIN, not a new one. Ground SHA re-resolved clean at BUILD time — no anchor had moved. tests → build were done in the same working session (not strictly red-before-any- code, since call-site research and implementation were interleaved while grounding); RED was still independently confirmed per-file before its own GREEN (see §4 RED evidence) and an earned-green mutation check re-ran post-hoc (§4) to rule out a vacuous suite.
- [AI] verify — gate PASS (reviewed by add-verify (self))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

