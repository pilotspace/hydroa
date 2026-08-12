# TASK: Tenant-scoped self-serve plans-list read + live UpgradePlanDialog wiring

slug: self-serve-plans-catalog · created: 2026-07-18 · stage: production · sensitivity: data
milestone: commercial-self-serve
component: gateway, dashboard
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/api/plan_router.py:plan_router` — `APIRouter(prefix="/admin/plan")`, `GET /admin/plan` = the tenant's CURRENT resolved plan, gated by `Depends(get_identity)` only (any authenticated role, mirrors `get_budget`). This task adds a SIBLING `GET /admin/plans` (plural, self-serve catalog) with the SAME auth idiom. Owns the endpoint.
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:PlanRow` — `plans` catalog. Relevant cols (added by self-serve-checkout, migration `b7e2c4a9f1d3`): `self_serve: bool` (seed free/starter/pro/team=true, enterprise=false), `audience: str|None` (seed free/starter/pro='personal', team/enterprise='business', NULL=no gate), `base_price_usd_monthly: Decimal|None` (free=NULL, starter=1, pro=20, team=99). `id`, `name` (unique), `display_name`.
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:TenantRow` — `account_type: 'personal'|'business'` (the audience filter), `plan_id` (the tenant's current plan — excluded from the upgrade list).
- `apps/gateway/src/gateway/keys/api/deps.py:get_identity` — tenant-scoped auth dep; `identity.tenant_id` is the ONLY tenant source, never a body/query field.
- `apps/dashboard/components/checkout/UpgradePlanDialog.tsx:UpgradePlanOption` (`{id, displayName}`) + `availablePlans` prop — the prop-driven menu this task now feeds live.
- `apps/dashboard/components/plan/PlanSeatsPage.tsx:77` — `const upgradeOptions: UpgradePlanOption[] = []` (the hardcoded-empty D6 gap this task replaces with a live query).
- `apps/dashboard/lib/checkout.ts` — `bffGet`-based read helper lives alongside `createPlanUpgrade`; gains `fetchSelfServePlans()`.
- `apps/dashboard/lib/bff-client.ts:bffGet` — the tenant-scoped GET proxy helper.

Context (working folder): `.add/milestones/commercial-self-serve/MILESTONE.md` Exit criterion 4 (live plan upgrade from /app/plan). The self-serve-checkout verify surfaced D6 (no tenant plans-list read → the live Upgrade dialog has no data source) as an open SPEC delta; this task closes it.
Honors (patterns / conventions): tenant-scoped read gated by `Depends(get_identity)` (mirror `GET /admin/plan`/`get_budget` RBAC exactly — no `require_permission`); every query filters by `identity.tenant_id`-derived `account_type`, never a client field; additive-only (no migration, no new column, no write path).
Seams consulted: none.
Anchors the contract cites: `plan_router` · `PlanRow` (`self_serve`/`audience`/`base_price_usd_monthly`/`display_name`) · `TenantRow` (`account_type`/`plan_id`) · `get_identity`/`Identity` · `UpgradePlanOption`/`availablePlans` · `bffGet`.
Issues/Risks (→ feed §1):
- I1 (audience gate): a personal tenant must NEVER see a business-audience plan (team/enterprise) and vice-versa — the filter is `self_serve=true AND (audience IS NULL OR audience = tenant.account_type)`. A wrong filter mis-offers a plan the checkout endpoint would then reject (`plan_account_type_mismatch`), a confusing but fail-closed dead-end.
- I2 (enterprise exclusion): enterprise is `self_serve=false` → excluded by the self_serve filter (contact-sales, never self-purchasable).
- I3 (current plan): the tenant's own `plan_id` is excluded so the menu shows only OTHER plans (no self-select no-op).
Related intent: MILESTONE.md Exit criterion 4 + GLOSSARY "5-tier catalog" self-serve-purchasable. Closes the upgrade leg of the milestone goal (zero-operator self-serve upgrade).
Ground SHA: `2e5dae2`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant-scoped self-serve plans catalog + live upgrade menu
Framings weighed: additive sibling GET /admin/plans (chosen) · reuse superadmin GET /admin/platform/plans (rejected — superadmin-gated, returns ALL plans incl. non-self-serve/other-audience) · embed the list in GET /admin/plan (rejected — overloads the frozen current-plan shape)
Must:
<must>
  - M1: GET /admin/plans returns the plans a tenant may self-serve upgrade to — `self_serve=true AND (audience IS NULL OR audience = tenant.account_type) AND id != tenant.plan_id` — ordered by base_price_usd_monthly ascending (NULL/free first), each as `{id, name, display_name, base_price_usd_monthly}` (price as a 2dp string or null).
  - M2: the endpoint is gated by `Depends(get_identity)` only (any authenticated tenant role), tenant resolved from `identity.tenant_id` — never a query/body field.
  - M3: a business tenant sees business-audience + no-audience self-serve plans and NEVER a personal-only plan; a personal tenant sees personal + no-audience and NEVER a business-only plan; enterprise (self_serve=false) is never returned to anyone.
  - M4: the dashboard /app/plan Upgrade dialog is fed live from this endpoint (replacing the hardcoded empty `upgradeOptions`); an empty list still renders the dialog with an honest "no upgrades available" empty state (never a crash).
</must>
Reject:
<reject>
  - unauthenticated request (no/invalid session) -> "unauthorized"   # 401, inherited from get_identity
</reject>
After:
<after>
  - no data mutated (pure read); the tenant's plan_id/account_type unchanged; response is a stable-ordered catalog subset.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ excluding the tenant's CURRENT plan_id (I3) is the right menu semantics — lowest confidence because a tenant on a mid-tier might expect to see all tiers incl. their own for context; if wrong: the menu omits the current tier (minor UX, not a correctness/security issue) — additive to re-include later.
  - [x] audience filter `audience IS NULL OR audience = account_type` matches the seeded convention (free/starter/pro=personal, team=business, enterprise excluded via self_serve=false) — confirmed against orm.py seed comments + migration b7e2c4a9f1d3.
  - [x] any authenticated role may read the catalog (not just BILLING_MANAGE) — confirmed: mirrors GET /admin/plan which reads the current plan with `Depends(get_identity)` only; the catalog is non-sensitive pricing already public on /pricing.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Personal tenant sees personal self-serve plans, not business or enterprise   # M1, M3
  Given a personal tenant on the free plan
  When it GETs /admin/plans
  Then the response lists starter and pro (personal, self_serve, price-ascending)
  And it never contains team (business audience) or enterprise (self_serve=false) or free (the current plan)

Scenario: Business tenant sees business self-serve plans, not personal-only   # M1, M3
  Given a business tenant on the team plan
  When it GETs /admin/plans
  Then the response contains only self_serve plans whose audience is business or null
  And it never contains a personal-only plan, enterprise, or team (the current plan)

Scenario: Endpoint is tenant-scoped and needs auth   # M2, R:unauthorized
  Given no valid session
  When a request hits GET /admin/plans
  Then the response is 401 unauthorized
  And no plan data is returned

Scenario: Live dialog is fed from the endpoint   # M4
  Given a tenant whose /admin/plans returns starter and pro
  When the /app/plan Upgrade dialog opens
  Then its target-plan menu lists exactly those plans (id + display_name)
  And an empty catalog still renders the dialog with an honest empty state, not a crash
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/plans        auth: Depends(get_identity) only (any authenticated tenant role)
  200 -> { plans: [ { id: str, name: str, display_name: str, base_price_usd_monthly: str | null } ] }
         # ordered by base_price_usd_monthly ASC (NULL first); subset = self_serve=true
         #   AND (audience IS NULL OR audience = tenant.account_type) AND id != tenant.plan_id
  401 -> { error: "unauthorized" }   # inherited from get_identity, unchanged shape

Backend:
  gateway/tenants/api/plan_router.py — add `get_self_serve_plans` handler on the existing
    plan_router (prefix "/admin/plan" → route path "s" → GET /admin/plans), Depends(get_identity).
  gateway/tenants/application/self_serve_plans.py (NEW) — `list_self_serve_plans(session_factory,
    *, account_type: str|None, current_plan_id) -> list[SelfServePlan]` pure read (SELECT PlanRow
    WHERE self_serve AND (audience IS NULL OR audience=account_type) AND id != current_plan_id
    ORDER BY base_price NULLS FIRST). `SelfServePlan` frozen dataclass {id,name,display_name,base_price}.
  Response model `SelfServePlansResponse` in the router (Pydantic), price serialized 2dp-or-null.
Schema: reads `plans` (self_serve, audience, base_price_usd_monthly, display_name, name, id) +
  `tenants` (account_type, plan_id) via the identity's tenant. NO write, NO migration, NO new column.

Frontend:
  lib/checkout.ts — `fetchSelfServePlans(): Promise<UpgradePlanOption[]>` via
    bffGet<{plans:[...]}>("/admin/plans"), mapping {id, display_name} -> {id, displayName}.
  components/plan/PlanSeatsPage.tsx — replace the hardcoded `upgradeOptions = []` with a
    useQuery(["self-serve-plans"], fetchSelfServePlans); pass data (or []) to availablePlans.
  UpgradePlanDialog — unchanged (already prop-driven + honest empty state).
```

Glossary deltas: none (reuses "5-tier catalog", "self-serve", "audience" from the checkout freeze).
Least-sure flag surfaced at freeze: [contract] the menu EXCLUDES the tenant's current plan_id (`id != tenant.plan_id`) — least-sure because a tenant might expect their current tier shown for context; if wrong the cost is a cosmetic omission (not correctness/security), re-includable additively. [spec] the catalog read is gated by `Depends(get_identity)` only (any role), NOT `BILLING_MANAGE` — pricing is already public on /pricing, and this mirrors the frozen GET /admin/plan RBAC; only the checkout MUTATION stays BILLING_MANAGE-gated.
Status: FROZEN @ v1 — approved by orchestrator under Tin's standing full-auto directive ("kick off new milestone then implement all enhancement of it", 2026-07-17); closes the self-serve-checkout D6 open delta.
Reported: yes — freeze surfaced above; flags triaged in-session.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of §2 scenarios + the extra test_locations coverage items (ordering, current-plan-exclude, enterprise-never-returned) — 4 backend + 2 frontend tests, all new src lines exercised.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_personal_tenant_sees_personal_self_serve_plans_price_ascending_never_business_current_or_enterprise: arrange a personal tenant on `free` (own catalog seed incl. extra `team_plus` business tier) / act GET /admin/plans / assert `[starter, pro]` price-ascending, `free`/`team`/`team_plus`/`enterprise` absent · covers: M1, M3, I2, I3
  - test_business_tenant_sees_business_self_serve_plans_never_personal_current_or_enterprise: arrange a business tenant on `team` / act GET /admin/plans / assert `[team_plus]` only, `team`/`enterprise`/personal-only ids absent · covers: M1, M3, I2, I3
  - test_unauthenticated_request_returns_401_and_no_plan_data: arrange no bearer token / act GET /admin/plans / assert 401, no `plans` key in body · covers: M2, R:unauthorized
  - test_ordering_is_price_ascending_with_null_first: arrange a personal tenant on `pro` (so `free` is NOT the current plan) / act GET /admin/plans / assert `[free, starter]` with `free.base_price_usd_monthly == null` sorting first · covers: M1 (NULLS FIRST ordering)
  - (frontend) test_upgrade_dialog_menu_lists_exactly_the_plans_admin_plans_returns: arrange PlanSeatsPage + mocked GET /admin/plans returning 2 plans / act open Upgrade dialog / assert exactly those 2 options render · covers: M4
  - (frontend) test_empty_catalog_still_renders_dialog_with_honest_empty_state_not_a_crash: arrange mocked GET /admin/plans returning `{plans: []}` / act open Upgrade dialog / assert the existing honest "no upgrade options" empty state renders, no crash · covers: M4
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
RED confirmed 2026-07-18: backend suite (`apps/gateway/tests/self_serve_plans_catalog/`) failed 4/4 with `404 Not Found` (route not mounted) — the correct RED signature, not a broken harness (signup/login/seed all succeeded pre-Build). Frontend suite failed 1/2 on the live-menu assertion (dialog rendered zero options against the hardcoded-empty array) — the correct RED signature for M4; the empty-catalog scenario was already trivially satisfied by the pre-Build hardcoded-empty array (harmless overlap, not a weakened assertion — it stays true post-Build via the live empty-response path instead).

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/application/self_serve_plans.py` (NEW) · `apps/gateway/src/gateway/tenants/api/plan_router.py` · `apps/dashboard/lib/checkout.ts` · `apps/dashboard/components/plan/PlanSeatsPage.tsx` · `apps/dashboard/tests/mocks/handlers.ts` (shared test-infra default handler, not a scenario test) · `apps/gateway/tests/self_serve_plans_catalog/` (NEW) · `apps/dashboard/tests/plan-upgrade-live-catalog.test.tsx` (NEW)
Strategy (ordered batches): 1. Ground the exact ORM/router/entity shapes (PlanRow, TenantRow, Identity, get_identity, existing `get_plan` handler + self-serve-checkout's `CheckoutService._require_tenant`/`session_factory` DI precedent) via serena before writing anything. 2. Write the backend red suite first (own conftest seeding a 6-tier catalog — the shipped 5 tiers + one extra business self-serve tier `team_plus` so the business-tenant scenario is non-vacuous), confirm RED via 404 (route unmounted). 3. Write the frontend red suite (mirrors billing-plan.test.tsx's QueryClientProvider+msw style), confirm RED via the live-menu assertion. 4. Implement backend: pure-function `list_self_serve_plans` (mirrors the contract's exact signature) + router handler loading TenantRow then delegating. 5. Implement frontend: `fetchSelfServePlans` + PlanSeatsPage `useQuery` wiring + a new INITIAL msw handler for `GET /admin/plans` (empty-catalog default) so every pre-existing PlanSeatsPage-rendering test stays green untouched (the same established pattern as the residency-policy/service-tiers default handlers already in that file). 6. Full regression: self_serve_plans_catalog + self_serve_checkout + plans + credits_ledger (backend), full dashboard vitest run (frontend) + tsc --noEmit + ruff + pyright.

Persona (required): backend-architect (`.add/personas/backend-architect.md`) — primary domain stance for the new `application/` pure-read function + router wiring; cross-checked against frontend-engineer's BFF-trust-boundary discipline for the dashboard leg (no Protocol port was introduced for `list_self_serve_plans` — a plain async function, matching the frozen contract's own literal signature and this codebase's existing precedent for a side-effect-free single-query read with no cross-row invariant to protect, e.g. `ApiKeyRepository`-style plain reads).
Spawn isolation (default): none spawned — single build agent worked the main tree directly (small, additive, two-file-pair scope; no parallel subagents needed).
Known-problem fixes: shared :5433 test DB races (memory: shared-test-postgres-no-timeouts) → used a unique `GATEWAY_TEST_DATABASE_URL=.../gateway_test_plans_catalog`, pre-created once via psql before the first xdist run (the per-worker `_ensure_worker_database` fixture only creates the *derived* `_gwN` databases, not the base). msw `onUnhandledRequest:"error"` (memory: established INITIAL-handler pattern) → added a default `GET /admin/plans` handler to `tests/mocks/handlers.ts` rather than editing any test. Fire-and-forget audit test flake under `-n` (memory) → observed one unrelated flake (`test_superadmin_plan_endpoint_unchanged`) during a broad regression run; confirmed pre-existing and unrelated by rerunning it alone (passed) — not touched.
Strategy actually used: as planned (see ordered batches above) — no deviation.
Safety rule (feature-specific): none — pure read, no mutation, no migration; the only "safety" property is fail-closed audience/self_serve filtering (I1/I2), verified by dedicated tests rather than a runtime safety mechanism.
Code lives in: `apps/gateway/src/gateway/` · `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 4/4 backend (`tests/self_serve_plans_catalog`, unique DB, `-n 4`) + 2/2 FE (`plan-upgrade-live-catalog.test.tsx`, real vitest) green in the main tree; regression: 89 BE across self_serve_checkout+plans+credits_ledger, dashboard 1505/1518 (the 13 failures are pre-existing design-system/marketing-seo Airier drift, untouched).
- [x] coverage did not decrease — all new src lines exercised; no covered line removed; the one shared-infra edit (`tests/mocks/handlers.ts` default `GET /admin/plans` handler) keeps every pre-existing PlanSeatsPage test green.
- [x] no test or contract was altered during build — frozen §3 untouched; RED confirmed first (backend 404 route-unmounted; FE live-menu assertion) then made green; no test weakened.
- [x] the green was EARNED, not gamed — orchestrator refute-read: the business-tenant test needs the seeded extra `team_plus` tier to be non-vacuous (a filter that returned nothing would fail it); the ordering test puts the tenant on `pro` so `free` (NULL price) must sort first — not trivially satisfiable; the 401 test asserts no `plans` key. Read `self_serve_plans.py` in full — the filter is `self_serve AND (audience IS NULL OR audience=account_type) AND id!=current_plan_id`, fail-CLOSED on `account_type=None` (only no-audience rows, never widened).
- [x] concurrency / timing of the risky operation is safe — pure read, single SELECT, no mutation, no lock, no shared state; nothing to race.
- [x] no exposed secrets, injection openings, or unexpected dependencies — tenant scope resolved from `identity.tenant_id` only (TenantRow loaded server-side, never a query/body field); parameterized ORM query; returns display metadata only (id/name/display_name/price), no entitlement/secret data; no new deps.
- [x] layering & dependencies follow CONVENTIONS.md — `application/self_serve_plans.py` pure read (a plain async function, matching this codebase's side-effect-free single-read precedent; no Protocol port needed), router delegates, dependencies inward.
- [x] a person reviewed and approved the change — orchestrator recorded under Tin's standing full-auto directive (2026-07-17); sensitivity: data, single verify pass (refute-read above) — no dual-verify required (non-security).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] component green-bars met — gateway `pytest (Makefile:test / ci.yml 'Tests' step)`: 4/4 `tests/self_serve_plans_catalog` green via `uv run pytest -n 4`; dashboard `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`: 2/2 green via the real `apps/dashboard/node_modules/.bin/vitest run` — both the runners CI invokes.
- [x] GET /admin/plans returns the audience-filtered, self_serve, current-plan-excluded, price-ascending catalog — confirmed by the personal + business + ordering tests (personal-on-free sees [starter,pro]; business-on-team sees [team_plus]; NULLS FIRST proven with a pro tenant).
- [x] the /app/plan Upgrade dialog is fed live and degrades honestly — confirmed by the 2 FE tests (menu lists exactly the endpoint's plans; empty catalog renders the honest empty state, no crash) — the hardcoded `upgradeOptions=[]` is gone.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `get_self_serve_plans` mounted at `@plan_router.get("s")` (→ /admin/plans); `list_self_serve_plans` called by the handler; `fetchSelfServePlans` consumed by PlanSeatsPage's useQuery → availablePlans. All referenced.
- [x] DEAD-CODE (code) — no orphaned symbol; `SelfServePlan`/`SelfServePlanItem`/`SelfServePlansResponse` all consumed.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves — `plan_router`, `PlanRow.self_serve/audience/base_price_usd_monthly`, `TenantRow.account_type/plan_id`, `get_identity`, `UpgradePlanOption`/`availablePlans`, `bffGet` all resolve in the current tree (ruff+pyright+tsc clean).
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-18

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose additive sibling GET /admin/plans; rejected reuse superadmin GET /admin/platform/plans (rejected — superadmin-gated, returns ALL plans incl. non-self-serve/other-audience) · embed the list in GET /admin/plan (rejected — overloads the frozen current-plan shape)
- [human] freeze — froze §3 @ v1 (approved by orchestrator under Tin's standing full-auto directive ("kick off new milestone then implement all enhancement of it", 2026-07-17); closes the self-serve-checkout D6 open delta.)
- [AI] build — strategy used: as planned (see ordered batches above) — no deviation.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

