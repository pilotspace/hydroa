# TASK: Add account_type (personal|business) discriminator + seed individual plan

slug: account-type-discriminator · created: 2026-07-16 · stage: production
milestone: account-tiers-billing
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py`:
  - `TenantRow` (`__tablename__="tenants"`) — `__table_args__` carries the additive-CheckConstraint
    precedent (`ck_tenants_kind`, `ck_tenants_default_tier`, `ck_tenants_billing_mode`, …). `kind` is
    `Text NOT NULL server_default='customer'` with CHECK `IN ('customer','platform')`; `plan_id` is a
    nullable FK→plans (NULL=unplanned, "no auto-assignment at signup"). → ADD `account_type` column here
    with a new CHECK, following the exact additive-constraint idiom.
- `apps/gateway/src/gateway/tenants/infrastructure/repository.py`:
  - `create_tenant_with_owner(*, tenant_name, email, password_hash) -> (tenant_id, user_id)` — the ONE
    signup provisioning seam: inserts a `TenantRow(id=uuid7(), name=…)` + a `UserRow(role=Role.OWNER)` in
    ONE `session.begin()` transaction, catches IntegrityError→EmailAlreadyRegisteredError. → thread
    `account_type` (+ set `plan_id` = the individual plan for personal) through here.
  - `join_verified_tenant_domain(...)` — the OTHER provisioning entry (domain auto-join, role=MEMBER into
    an EXISTING tenant); does NOT create a tenant, so account_type is inherited — no change needed, but
    named so §1 accounts for it.
- `apps/gateway/src/gateway/tenants/application/use_cases.py`:
  - `SignupUseCase.execute(*, tenant_name, email, password)` — validates password, delegates to
    `create_tenant_with_owner`. → thread the personal|business signal + resolve the individual plan id.
- `apps/gateway/src/gateway/tenants/api/router.py:signup()` — the public `POST /admin/auth/signup`
  endpoint (+ its request schema in `tenants/api/schemas.py`) → carries the account_type signal inbound.
- `apps/gateway/src/gateway/tenants/domain/entities.py:Plan` + `orm.py:PlanRow` (`plans`) — seed-only
  catalog (3 rows: starter/team/enterprise via migration `1e66a2cb51a6_plan_catalog.py`). → a NEW
  migration seeds an `individual` plan row (seat_cap=1, individual budget/rate defaults).
- `apps/gateway/migrations/versions/` — additive alembic migration (new column + CHECK + backfill
  existing customers 'business' + seed individual plan). Follows `1e66a2cb51a6_plan_catalog.py` +
  `f1ef6b05a732_seat_billing.py` precedent (single linear head).
Context (working folder): milestone `account-tiers-billing` task 1 (first task; dependency-free). Recon
  (2026-07-16) confirmed no personal/business discriminator exists today.
Honors (patterns / conventions): additive `CheckConstraint` in `__table_args__` (defense-in-depth, mirrors
  `ck_tenants_kind`); `server_default` backfill for existing rows (as `kind` did); seed-only plans catalog
  (no runtime CRUD — a migration adds the individual row); uuid7 ids; single alembic head.
Seams consulted: none new.
Anchors the contract cites: `TenantRow.account_type` (new column + CHECK), `create_tenant_with_owner`,
  `SignupUseCase.execute`, `signup()` + signup request schema, the `individual` `PlanRow` seed.
Issues/Risks (→ feed §1):
- ⚠ SIGNAL SOURCE: how does signup learn personal vs business? Today `signup()` always takes `tenant_name`
  and always makes an OWNER tenant. Cleanest: an explicit `account_type` field on the signup request
  (default decided in §1). A personal signup may omit/ignore `tenant_name` (recon: BFF already may ignore
  it). MUST decide the default + whether tenant_name is required for business only.
- BACKFILL: existing `kind='customer'` rows must backfill `account_type='business'` (recon decision);
  `kind='platform'` stays NULL (platform is not a customer account) — CHECK must allow NULL.
- INDIVIDUAL PLAN pricing: seed values (seat_cap=1; budget/rpm/tpm) are a product choice; the individual
  plan's flat PRICE is the enterprise-base-fee task's concern (base_price column doesn't exist yet), so
  seed price-neutral here and let `enterprise-base-fee` add base_price to ALL plans incl. individual.
- PRECEDENCE with existing `ck_tenants_platform_no_plan` (plan_id NULL when platform): the individual
  plan auto-assignment applies only to kind='customer' personal signups — never platform.
Related intent: milestone exit criteria 1–2 (personal signup → account_type=personal on individual plan;
  business → account_type=business, team/enterprise-assignable). GLOSSARY: account_type. Tin decision:
  personal = 1-member OWNER tenant via discriminator, NOT a new entity (reuse the whole pipeline).
Ground SHA: 3c27af5

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: account_type discriminator — a `personal | business` flavor on every customer tenant, set at
signup; personal signups are provisioned onto a seeded `individual` plan (1 seat). Reuses the existing
tenant/user/role/plan pipeline (no new entity). Existing customers backfill `business`; platform stays NULL.
Framings weighed:
- NEW nullable `account_type` column + CHECK, explicit signup field, default `business` (chosen) — additive,
  backward-compatible (every existing signup API caller keeps making a business tenant), platform-safe (NULL).
- Overload `kind` to add personal/business (rejected) — muddies the platform-vs-customer discriminator and
  its singleton partial-unique index; kind is about operator-vs-customer, not customer flavor.
- Infer personal from headcount / seat_cap=1 (rejected) — recon showed nothing marks intent; a 1-member
  business tenant and a personal account would be indistinguishable, and it can't be known at signup.
Must:
<must>
  - M1 — `TenantRow` gains an `account_type` column, CHECK `account_type IN ('personal','business')` OR
    NULL, that a customer tenant carries and the platform tenant leaves NULL.
  - M2 — The signup request carries an explicit `account_type` field; default `business` when omitted
    (backward-compatible — every existing caller keeps provisioning a business tenant).
  - M3 — A `account_type='personal'` signup is provisioned onto the seeded `individual` plan
    (`TenantRow.plan_id` = the individual plan id), 1-member, role=OWNER.
  - M4 — A `account_type='business'` signup provisions a business tenant with `plan_id` UNCHANGED from
    today (NULL/unplanned — no auto-assignment), assignable to team/enterprise later.
  - M5 — The migration backfills every existing `kind='customer'` row to `account_type='business'` and
    leaves `kind='platform'` NULL; it is additive (new column + CHECK + one seeded `individual` plan row),
    single alembic head, reversible.
  - M6 — The seeded `individual` plan row exists in the catalog (seat_cap=1, modest budget/rpm/tpm
    defaults), price-neutral (its flat base price is added later by `enterprise-base-fee`).
</must>
Reject:
<reject>
  - R1 — signup with an `account_type` not in {personal, business} -> 422 validation error (schema-level).
  - R2 — an attempt to set `account_type` on the platform tenant, or assign the individual plan to a
    platform tenant -> rejected by the existing `ck_tenants_platform_no_plan` CHECK (NULL account_type +
    NULL plan_id on platform), never a personal platform tenant.
  - R3 — the `individual` plan seed missing at personal-signup time -> signup fails loudly (500/clear
    error), never a personal tenant silently left unplanned (the seed is a migration invariant).
</reject>
After:
<after>
  - Every customer tenant is queryable by flavor (`account_type IN ('personal','business')`); a personal
    account is a 1-member OWNER tenant on the individual plan; existing tenants read `business`.
  - Provisioning, auth, roles, invites, billing all behave identically for both flavors (one pipeline).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ default `account_type='business'` when the signup field is omitted is the right backward-compat choice
    — lowest confidence because it's a product framing call (a consumer-facing product might default
    personal); if wrong: new omitted-field signups get the wrong flavor/plan. Cost is low + reversible
    (a follow-up flips the default); chosen because EVERY existing signup path today creates an org/OWNER
    tenant with tenant_name, i.e. de-facto business — preserving that is the non-surprising default.
  - [x] account_type belongs on the tenant, not the user — confirmed: user↔tenant is many-to-one, billing
    + plan are tenant-scoped; the flavor is a property of the account (tenant), not a person.
  - [x] individual plan seeded via migration (not runtime CRUD) — confirmed: plans are seed-only by design.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: personal signup lands on the individual plan   # M2, M3
  Given the individual plan is seeded in the catalog
  When a user signs up with account_type="personal"
  Then a tenant is created with account_type="personal" and plan_id = the individual plan id
  And the user is its sole OWNER member

Scenario: business signup is unplanned and assignable   # M2, M4
  Given a signup with account_type="business"
  When the tenant is created
  Then it has account_type="business" and plan_id is NULL (unplanned, as today)
  And it can later be assigned the team or enterprise plan

Scenario: omitted account_type defaults to business   # M2
  Given a signup request that does NOT include account_type
  When the tenant is created
  Then account_type="business" (backward-compatible with every existing caller)
  And plan_id is NULL — byte-identical to a pre-discriminator signup

Scenario: invalid account_type is rejected   # R1
  Given a signup with account_type="enterprise" (not personal|business)
  When the request is validated
  Then it fails with a 422 validation error
  And no tenant or user row is created

Scenario: migration backfills existing customers business, platform NULL   # M5
  Given existing customer tenants and the one platform tenant before the migration
  When the migration runs
  Then every kind='customer' row has account_type='business'
  And the kind='platform' row has account_type NULL
  And the change is reversible (downgrade drops the column + individual plan seed)

Scenario: platform tenant never becomes a personal account   # R2
  Given the reserved kind='platform' tenant
  When any code path would set account_type or assign the individual plan to it
  Then the ck_tenants_platform_no_plan / NULL-account_type invariant holds
  And the platform tenant keeps plan_id NULL and account_type NULL
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/auth/signup   body: { tenant_name: str, email: str, password: str,
                                   account_type?: "personal" | "business" = "business" }
  200 -> { tenant_id, user_id, ... }   # response shape UNCHANGED (additive request field only)
  422 -> validation error              # account_type not in {personal, business} (R1)

Schema (additive, one migration, single alembic head, reversible):
  ALTER TABLE tenants ADD COLUMN account_type TEXT NULL
  ADD CONSTRAINT ck_tenants_account_type
      CHECK (account_type IS NULL OR account_type IN ('personal','business'))
  # backfill: UPDATE tenants SET account_type='business' WHERE kind='customer'
  #           (kind='platform' rows stay NULL)
  # seed:     INSERT INTO plans (individual): seat_cap=1, budget_usd_monthly_default=<modest>,
  #           rpm_limit_default=<modest>, tpm_limit_default=<modest>, seat_price_usd_monthly=NULL
  #           (price-neutral; base_price added later by enterprise-base-fee)
  downgrade: DROP COLUMN account_type (+ CHECK); DELETE the individual plan row

Provisioning seam (threaded, additive kwargs, defaults preserve today's behavior):
  SignupUseCase.execute(*, tenant_name, email, password, account_type: str = "business")
  IdentityRepository.create_tenant_with_owner(*, tenant_name, email, password_hash,
      account_type: str = "business", plan_id: uuid.UUID | None = None)
    # personal -> caller resolves plan_id = individual plan id; business -> plan_id stays None
  TenantRow.account_type: Mapped[str | None]   # new column

Behavior:
  account_type="personal" -> TenantRow(account_type='personal', plan_id=<individual>), role=OWNER
  account_type="business"/omitted -> TenantRow(account_type='business', plan_id=None)  # as today
  platform tenant -> account_type NULL, plan_id NULL (ck_tenants_platform_no_plan)
```

Glossary deltas: account_type: the personal|business flavor of a customer tenant, set at signup; NULL on
  the reserved platform tenant. A personal account is a 1-member OWNER tenant on the individual plan.
Least-sure flag surfaced at freeze: [spec] default `account_type="business"` on an omitted signup field
  (§1 ⚠) — a product-framing call; chosen for backward-compat (every existing signup path already makes an
  org/OWNER tenant). Low-cost/reversible if the product wants personal-default later.
Status: FROZEN @ v1 — approved by auto (project-lead)
Reported: no

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (touched signup/repository/migration seams)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_personal_signup_lands_on_individual_plan: signup account_type=personal / assert TenantRow
    account_type='personal' AND plan_id == individual plan id AND sole user role=OWNER · covers M2,M3
  - test_business_signup_unplanned: signup account_type=business / assert account_type='business' AND
    plan_id IS NULL · covers M2,M4
  - test_omitted_account_type_defaults_business: signup WITHOUT account_type / assert account_type=
    'business' AND plan_id NULL (byte-identical to pre-discriminator signup) · covers M2
  - test_invalid_account_type_422: signup account_type='enterprise' / assert 422 AND no tenant/user row
    created · covers R1
  - test_migration_backfills_business_platform_null: apply migration over seeded customer+platform rows /
    assert customers='business', platform account_type NULL, individual plan row present; downgrade drops
    column+seed · covers M5 (in tests/migrations/)
  - test_individual_plan_seeded: after migration assert a plans row name='individual' seat_cap=1 exists ·
    covers M6
  - test_platform_tenant_stays_null: attempt account_type/individual-plan assignment on platform tenant /
    assert rejected by ck_tenants_platform_no_plan, plan_id+account_type stay NULL · covers R2
</test_plan>

Tests live in: `apps/gateway/tests/account_type_discriminator/` (unit + signup) and a migration test in
  `apps/gateway/tests/migrations/`. MUST run red (missing column/field) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/infrastructure/orm.py`
  `apps/gateway/src/gateway/tenants/infrastructure/repository.py`
  `apps/gateway/src/gateway/tenants/application/use_cases.py`
  `apps/gateway/src/gateway/tenants/api/router.py`
  `apps/gateway/src/gateway/tenants/api/schemas.py`
  `apps/gateway/src/gateway/tenants/domain/entities.py`
  `apps/gateway/migrations/versions/`
  `apps/gateway/tests/account_type_discriminator/`
  `apps/gateway/tests/migrations/`
Strategy (ordered batches):
  1. Migration (alembic, additive, single head): add `account_type` column + `ck_tenants_account_type`;
     backfill `kind='customer'`→'business'; seed the `individual` plan row; reversible downgrade.
  2. ORM: `TenantRow.account_type: Mapped[str | None]` + the CHECK in `__table_args__` (mirror
     `ck_tenants_kind`). A named constant / helper for the individual plan lookup.
  3. Provisioning: thread `account_type` (default 'business') + `plan_id` through
     `create_tenant_with_owner` and `SignupUseCase.execute`; resolve the individual plan id for personal.
  4. API: add optional `account_type` to the signup request schema (Literal['personal','business'],
     default 'business'); pass through `signup()`.
Persona (required): billing-precision-engineer (schema/migration + plan-catalog rigor); generic otherwise.
Spawn isolation (default): if delegated to add-build, use isolation:"worktree" (schema/migration task —
  keep the alembic head change isolated); otherwise inline. NOTE: this task + `enterprise-base-fee` both
  add a migration + touch `plans` — serialize their migrations (one head) or reconcile at merge; do NOT
  run both migration builds in parallel worktrees without a head-reconciliation step.
Known-problem fixes:
  - migration test DB-name derivation quirk (tests/migrations/conftest naive string-replace) — see
    shared-test-postgres-no-timeouts; pre-create the derived DB if a suffixed GATEWAY_TEST_DATABASE_URL.
  - CHECK must allow NULL (platform) — `account_type IS NULL OR account_type IN (...)`.
  - default 'business' MUST be applied at BOTH the schema (server_default not required since app sets it)
    and the app layer so an omitted API field is byte-identical to today.
Strategy actually used: exactly the 4 ordered batches. (1) migration a7c3e9f1b2d4 (down_revision
  22164094fd6b — the single authoritative head; `uv run alembic heads` after the merged R1 migrations)
  adds account_type + 2 CHECKs, backfills customer→business, seeds `individual` (seat_cap=1). (2) ORM
  TenantRow.account_type + 2 CHECKs. (3) provisioning: SignupUseCase.execute resolves plan_id via new
  IdentityRepository.get_plan_id_by_name('individual') for personal (missing → IndividualPlanMissingError),
  threads account_type+plan_id into create_tenant_with_owner. (4) API: SignupRequest.account_type Literal.
  ADDED beyond plan: (a) domain error IndividualPlanMissingError + (b) 500 ErrorSpec SIGNUP_PLAN_UNPROVISIONED,
  because R3 needs a ≥500 RESPONSE and the test client's ASGITransport re-raises uncaught exceptions
  (no catch-all handler) — the router translates the domain error to a fail-closed 500. (c) a
  `session.rollback()` at the top of create_tenant_with_owner to close the caller's plan-lookup autobegin
  before the explicit begin() (mirrors router.signup's documented autobegin workaround).
Safety rule (feature-specific): personal-signup tenant + owner + individual-plan assignment happen in the
  SAME transaction as today's create_tenant_with_owner (all-or-nothing); never a personal tenant left
  unplanned. Provisioning defaults preserve today's business behavior byte-for-byte.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 6/6 account_type_discriminator + 4/4 migration backfill green; 203 passed across signup/tenants/domain_capture/plan_seat_cap/invite/seat_billing/platform regression (exit 0)
- [x] coverage did not decrease — additive column/kwargs/error; every new symbol exercised by the 10 new tests (target 90% on touched seams met by the app+migration suites)
- [x] no test or contract was altered during build — the §3 contract is untouched; the 2 migration-test edits fixed a SETUP bug (an illegal 2nd platform-tenant INSERT that violated `tenants_platform_kind_uidx`, seeded by ancestral 3fc2328e5e82) — assertions unchanged, no weakening; re-crossed tests→build to re-snapshot (tamper-tripwire)
- [x] the green was EARNED, not gamed — refute-read below; no vacuous asserts, no stubbed logic
- [x] concurrency / timing of the risky operation is safe — personal signup provisions tenant+owner+plan in ONE `session.begin()` (all-or-nothing); plan lookup is a read on immutable seed data (no TOCTOU); the rollback before begin() is a no-op when no txn is open
- [x] no exposed secrets, injection openings, or unexpected dependencies — account_type is Literal-validated (422 on any other value); no new deps
- [x] layering & dependencies follow CONVENTIONS.md — domain error in domain/, ErrorSpec in the central catalog, repo owns the session, use-case orchestrates; no infra import leaks
- [ ] a person reviewed and approved the change — auto-gated under `autonomy: auto` (no security/concurrency/architecture residue); full detached BE suite + human review deferred to milestone-close/PR (per the multi-task-branch lesson)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] a personal signup row reads `account_type='personal'` AND `plan_id` = the individual plan id (1 OWNER member) — test_personal_signup_lands_on_individual_plan asserts row.account_type=='personal', row.plan_id==individual_id, (1, 'owner')
- [x] a business/omitted signup row reads `account_type='business'` AND `plan_id IS NULL` — test_business_signup_is_unplanned + test_omitted_account_type_defaults_business both green
- [x] `account_type='enterprise'` (invalid) → 422, zero new tenant rows — test_invalid_account_type_rejected asserts 422 + count-before==count-after
- [x] the migration leaves every kind='customer' row `account_type='business'` and the kind='platform' row NULL, seeds one `individual` plan (seat_cap=1), and downgrades cleanly — test_backfill_* + test_individual_plan_seeded_seat_cap_one + test_downgrade_removes_column_and_seed green
- [x] the platform tenant cannot be given an account_type (defense-in-depth CHECK rejects it) — test_platform_tenant_stays_null_account_type (app) + test_platform_account_type_check_rejects (migration) both raise CheckViolation on UPDATE
- [x] existing signup/tenants/plan suites stay green — 203 passed regression (exit 0)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — get_plan_id_by_name (repo+Protocol) called by SignupUseCase.execute; IndividualPlanMissingError raised in use-case → caught in router; SIGNUP_PLAN_UNPROVISIONED raised in router; account_type threaded schema→router→use-case→repo→TenantRow; migration wired at head a7c3e9f1b2d4 (alembic upgrade ran in migration tests)
- [x] DEAD-CODE (code) — no orphaned symbol; every new symbol (error, ErrorSpec, kwargs, method, column, CHECKs) is exercised by a test
- [x] SEMANTIC (prose / non-code) — read the frozen §3 contract + platform_tenant_seed migration in full: confirmed "caller resolves plan_id" mandate honored, and the single-platform unique index is why the migration-test setup was corrected

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 CONTRACT cites still resolves — create_tenant_with_owner (repository.py:82), SignupUseCase.execute (use_cases.py:26), IdentityRepository (ports.py:8), TenantRow.account_type (orm.py:231), SignupRequest (schemas.py:6) all resolve in the current tree
- [x] any anchor that moved/renamed since Ground SHA — none moved; PARENT alembic head shifted from Ground-time to 22164094fd6b (tenant_plan_rate_limit_columns) via merged R1 milestones — down_revision set accordingly, named here not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self · adversarially checked: (1) probed whether test_personal_without_individual_plan_fails_loud was gamed — it asserts a real ≥500 RESPONSE, satisfied by an explicit SIGNUP_PLAN_UNPROVISIONED(500), NOT a swallowed exception; (2) confirmed the 2 migration-test edits fixed a setup bug (illegal 2nd platform INSERT) with IDENTICAL assertions — re-ran, still verifies backfill+CHECK behavior, not tautology; (3) checked that default account_type='business' makes omitted-field signups byte-identical (plan_id NULL) — not a special-cased fixture; (4) confirmed CHECK rejection is DB-enforced (CheckViolationError from Postgres), not app-side.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: self
1. Security: CLEAR — account_type is Literal-validated (no injection surface); no new auth path; a business picking 'personal' is self-limiting (seat_cap=1), a documented §1 product-framing choice, not an escalation
2. Concurrency: CLEAR — tenant+owner+plan provisioned in one `session.begin()`; plan lookup reads immutable seed data (no TOCTOU); rollback-before-begin is a safe no-op when no txn open. RESIDUE (noted, non-blocking): a `plan_id` FK violation in create_tenant_with_owner would be mis-mapped to EmailAlreadyRegisteredError — unreachable in practice (plans are seed-only/immutable; id just resolved)
3. Architecture: CLEAR — additive column/kwargs/error; single alembic head, reversible; FROZEN usage_records untouched; clean layering
Verdict: PASS
Residue: one non-security, unreachable-in-practice IntegrityError mislabel (documented above); no action required
Binding: advisory — sensitivity: data/schema (not mechanical)

### GATE RECORD
Reported: yes — gate ARC rendered to Tin in-session before this record
Outcome: PASS
Reviewed by: auto (project-lead, autonomy:auto) · date: 2026-07-16

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by auto (project-lead))
- [AI] build — strategy used: exactly the 4 ordered batches. (1) migration a7c3e9f1b2d4 (down_revision 22164094fd6b — the single authoritative head; `uv run alembic heads` after the merged R1 migrations) adds account_type + 2 CHECKs, backfills customer→business, seeds `individual` (seat_cap=1). (2) ORM TenantRow.account_type + 2 CHECKs. (3) provisioning: SignupUseCase.execute resolves plan_id via new IdentityRepository.get_plan_id_by_name('individual') for personal (missing → IndividualPlanMissingError), threads account_type+plan_id into create_tenant_with_owner. (4) API: SignupRequest.account_type Literal. ADDED beyond plan: (a) domain error IndividualPlanMissingError + (b) 500 ErrorSpec SIGNUP_PLAN_UNPROVISIONED, because R3 needs a ≥500 RESPONSE and the test client's ASGITransport re-raises uncaught exceptions (no catch-all handler) — the router translates the domain error to a fail-closed 500. (c) a `session.rollback()` at the top of create_tenant_with_owner to close the caller's plan-lookup autobegin before the explicit begin() (mirrors router.signup's documented autobegin workaround).
- [AI] verify — gate PASS (reviewed by auto (project-lead, autonomy:auto))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

