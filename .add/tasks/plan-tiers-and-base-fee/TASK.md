# TASK: 5-tier plan catalog + base_price_usd_monthly + invoice base line + /pricing no-drift

slug: plan-tiers-and-base-fee · created: 2026-07-16 · stage: production
milestone: account-tiers-billing
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:PlanRow` (class body lines 26-88) — add
  `base_price_usd_monthly: Mapped[Decimal | None]` (`Numeric(12,2)`, nullable) + a new `CheckConstraint`
  `ck_plans_base_price_positive` (`base_price_usd_monthly IS NULL OR base_price_usd_monthly > 0`) in
  `__table_args__`, mirroring `seat_price_usd_monthly`'s own additive column+CHECK precedent in the SAME
  class/file.
- `apps/gateway/src/gateway/tenants/application/use_cases.py:SignupUseCase.execute` — the
  `if account_type == "personal": plan_id = await self._repository.get_plan_id_by_name("individual")`
  branch repoints its literal to `"free"`. The R3 fail-closed shape (missing plan → uncaught
  `IndividualPlanMissingError` → router 500) is UNCHANGED, just now keyed off `free`.
- `apps/gateway/src/gateway/tenants/domain/errors.py:IndividualPlanMissingError` (lines 18-24) +
  `apps/gateway/src/gateway/core/error_catalog.py:SIGNUP_PLAN_UNPROVISIONED` (lines 156-160) — class/
  ErrorSpec NAMES and the `500`/`ERR_SIGNUP_PLAN_UNPROVISIONED` response shape stay UNCHANGED (R1 reuses
  this exact contract); only the human-readable message text may be updated by Build to describe "the
  free plan" instead of "the individual plan" — cosmetic, non-contractual.
- `apps/gateway/src/gateway/billing/application/invoice_generator.py:InvoiceGenerator.generate_for_tenant`
  (lines 247-434) + module function `_load_seat_price` (lines 203-222) — the seat-fold pattern (seat
  price loaded lines 336-348, seat lines persisted lines 411-432) this task mirrors with a NEW
  `_load_base_price` + one `'base'` `InvoiceLineRow`, folded into `raw_total`/`rounded_sum` BEFORE
  `total_usd = _round_half_up(raw_total)` / `_reconcile_rounding_delta(...)` (lines 361-362) — an issued
  invoice is immutable the instant it is inserted, so the base amount MUST be in `total_usd` from the
  first write, never patched after.
- `apps/gateway/src/gateway/billing/application/seat_pricer.py:NIL_SEAT_KEY_ID` (line 35, all-zero UUID
  sentinel) — reused as the new `'base'` line's `key_id` (`InvoiceLineRow.key_id` is `NOT NULL`, no single
  user owns an aggregate base charge, exactly the same reasoning the `'seat'` aggregate line already
  established).
- `apps/gateway/src/gateway/billing/infrastructure/orm.py:InvoiceLineRow` (lines 109-129) — schema
  UNCHANGED; `line_type` is `Text, server_default='usage'`, NO CHECK — `'base'` is an additive,
  unconstrained value, reinterpreting `model_id='base'` sentinel / `team_id=NULL` / `key_id=NIL_SEAT_KEY_ID`
  exactly like the existing `'seat'` aggregate row does.
- `apps/gateway/migrations/versions/a7c3e9f1b2d4_account_type_discriminator.py` — CONFIRMED the current
  single alembic head (`uv run alembic heads` → `a7c3e9f1b2d4 (head)`); this task's NEW migration sets
  `down_revision = "a7c3e9f1b2d4"`.
- `apps/gateway/migrations/versions/1e66a2cb51a6_plan_catalog.py` (seeds starter/team/enterprise) +
  `f1ef6b05a732_seat_billing.py` (adds `seat_price_usd_monthly`, prices team=15/enterprise=40) — read-only
  precedent, their migration FILES are NOT touched; only the ROWS they seeded are further `UPDATE`d by
  this task's own new migration.
- `apps/dashboard/app/(marketing)/pricing/page.tsx:TIERS` (lines 41-85) + `PricingPage` (lines 87-196) —
  static 3-card page (Starter/Team/Enterprise). Team's hardcoded `price: "$99"` (line 57) is today's
  unwired drift this task fixes; Starter's `price: "Free"` (line 44) and Enterprise's `"Contact us"`
  (line 73) also become bound to a shared source-of-truth.
- `apps/dashboard/tests/pricing-page.test.tsx` — existing frozen a11y/shape suite (7 describe blocks);
  its assertions on visible copy ("Free"/"$99"/"Contact us") are unaffected (the copy doesn't change,
  only its source moves into a new constants module) — cited here so Build knows NOT to touch it.

Task-1 (and task-1-adjacent) test reconciliation anchors — every real-`alembic head` / ORM-fixture test
that names the plan literally `'individual'`, or hardcodes "exactly 3 plans" (already stale after task-1's
own uncommitted migration, further broken by this task's 5-row catalog):
- `apps/gateway/tests/account_type_discriminator/test_account_type_discriminator.py`:
  `_seed_individual_plan` (lines 30-46), `test_personal_signup_lands_on_individual_plan` (lines 77-90),
  `test_personal_signup_without_individual_plan_fails_loud` (lines 127-134) — seed/assert a plan literally
  named `'individual'`.
- `apps/gateway/tests/migrations/test_account_type_backfill.py:test_individual_plan_seeded_seat_cap_one`
  (lines 73-87) — upgrades to REAL `alembic head` (line 78) and asserts a `plans` row named `'individual'`
  exists with `seat_cap=1`; after this task's migration becomes the new head, no row is named `'individual'`
  anymore (renamed to `'pro'`). `test_downgrade_removes_column_and_seed` (lines 113-132) needs NO edit —
  it downgrades all the way to `PARENT_REVISION=22164094fd6b` (task-1's own parent), so it stays green
  PROVIDED this task's `downgrade()` is fully reversible (hands back a clean `'individual'` row for task-1's
  own untouched downgrade to delete).
- ⚠ NEW FINDING (not in the pre-given recon) — two further real-`alembic head` tests hardcode "exactly 3
  plans" and are ALREADY broken by task-1's own uncommitted migration (a 4th `'individual'` row),
  independent of this task; this task's 5-row restructure breaks them further:
  - `apps/gateway/tests/plan_catalog/test_plan_catalog.py:test_migration_seeds_exactly_3_named_plan_tiers`
    (lines 350-389) — `assert len(rows) == 3` / name-set `{"starter","team","enterprise"}` / per-row
    seat_cap/budget/rpm/tpm, all read from the REAL migrated table.
  - `apps/gateway/tests/plan_enforcement/test_plan_enforcement_migration.py:
    test_migration_seeds_feature_flags_and_leaves_model_allowlist_null` (lines 36-68) —
    `assert len(rows) == 3` at real `alembic head`; the per-row `model_allowlist`/`feature_flags` checks
    (keyed by name, lines 62-68) are UNAFFECTED and stay as-is.

Context (working folder): milestone `account-tiers-billing`, task 2 of 3 (depends-on `account-type-
  discriminator`, task 1 — DONE but UNCOMMITTED in this working tree; `git status` shows its migration
  `a7c3e9f1b2d4` + `TenantRow.account_type`/`SignupUseCase` edits as untracked/modified alongside an
  unrelated sibling milestone's uncommitted work — this task grounds against that COMBINED tree state).
  Confirmed via `mcp__serena` symbol reads + `uv run alembic heads` (single head `a7c3e9f1b2d4`), not
  assumed from the pre-given recon.
Honors (patterns / conventions): id-preserving `UPDATE` for a repurposed/renamed seed row (never
  DELETE+reinsert — `plan_id` is `ON DELETE RESTRICT`, task-1's own docstring: "do NOT delete it"); the
  seat-billing "one extra SELECT, INNER JOIN, fold before insert" idiom (`_load_seat_price` →
  `_load_base_price`); migration-seeded, no-runtime-CRUD `plans` rows (plan-catalog TASK.md's own explicit
  non-goal, still honored — this task is a migration-only catalog edit, no new CRUD surface); additive
  `CheckConstraint` in `__table_args__` (mirrors `ck_plans_seat_price_positive`).
Seams consulted: none new.
Anchors the contract cites: `PlanRow.base_price_usd_monthly`, `_load_base_price`, `NIL_SEAT_KEY_ID`,
  `InvoiceLineRow(line_type='base')`, `SignupUseCase.execute` (`"free"` literal), the new migration
  (`down_revision="a7c3e9f1b2d4"`), the dashboard `PRICING_CATALOG` constants module.
Issues/Risks (→ feed §1):
- ⚠ STARTER-PLAN REPURPOSE COLLISION: `plans.name='starter'` is not just a seed row — it is
  LIVE-ASSIGNABLE at runtime via the existing superadmin `PUT /admin/platform/tenants/{tenant_id}/plan`
  endpoint (`apps/gateway/src/gateway/tenants/api/platform_plans_router.py`). If any BUSINESS tenant is
  ALREADY assigned `plan_id` = the starter row, repurposing that SAME row's `display_name`/`seat_cap`/
  `base_price_usd_monthly` to "Starter, personal $1, seat_cap=1" silently relabels and re-caps that
  business tenant's plan too (its own `account_type` column is untouched — only the PLAN it points to
  changes shape). Tin's locked decision explicitly directs this in-place repurpose and names no separate
  reassignment migration — out of scope unless Tin says otherwise. No known production tenant is confirmed
  on `starter` today (unverified — no production DB check performed as part of this ground pass).
- MIGRATION FULL-REVERSIBILITY REQUIREMENT: confirmed via a real dependent test
  (`test_account_type_backfill.py::test_downgrade_removes_column_and_seed`) that this task's migration
  `downgrade()` MUST restore the catalog to EXACTLY task-1's post-`a7c3e9f1b2d4` state (`pro`→`individual`
  rename undone, `starter`'s `display_name`/`seat_cap` reverted to `'Starter'`/3, the `free` row deleted,
  `base_price_usd_monthly` column + its CHECK dropped) — not merely "additive-safe", a byte-exact
  round-trip, because a REAL test downgrades PAST this migration and re-checks state at the ancestor.
- `budget_usd_monthly_default`/`rpm_limit_default`/`tpm_limit_default` for `starter`/`pro`(renamed
  `individual`)/`team`/`enterprise` are UNCHANGED by this task — Tin's locked table only specifies
  base_price + seat_cap + account-tier; only the NEW `free` row's budget/rpm/tpm are invented placeholders
  (mirrors task-1's own "seed price-neutral, DATA not SHAPE" precedent for `individual`).
Related intent: `.add/milestones/account-tiers-billing/MILESTONE.md` — the "Pricing model — Tin-locked
  2026-07-16" table (5 tiers) this task encodes verbatim; the "Shared decisions" doctrine "BASE fee is
  composed like seat pricing, OUTSIDE usage... mirror `seat_pricer.compute_seat_lines`"; the "Shared/risky
  contracts" line naming this task as owner of "the `plans` schema extension... + the 5-tier catalog
  restructure + the new `line_type='base'` invoice-line shape"; Exit criteria rows 3-5. GLOSSARY deltas:
  `base fee`, extends existing `account_type`.
Ground SHA: 3c27af5 (git HEAD) — the working tree ALSO carries task-1's (`account-type-discriminator`)
  DONE-but-UNCOMMITTED build on top (migration `a7c3e9f1b2d4`, `TenantRow.account_type`,
  `SignupUseCase`/`IdentityRepository` account_type+plan_id threading) — this task grounds against that
  combined state; every symbol/line cited above was read live from the current tree, not assumed.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: 5-tier `plans` catalog restructure (free/starter/pro personal + team/enterprise business) +
  `base_price_usd_monthly` column + a mirrored `line_type="base"` invoice line (flat, usage+seat
  -independent) + personal-signup default repoint (`individual`→`free`) + a `/pricing` no-drift test
  binding the marketing page's displayed figures to the seeded backend catalog. Reconciles the (now-stale)
  task-1 tests that hardcoded `'individual'` as the personal-signup default plan name, plus 2 further
  real-migration tests independently found stale.
Framings weighed:
- RENAME/REPURPOSE existing rows in-place via id-preserving `UPDATE` (chosen) — Tin-locked; avoids the
  `plan_id` FK `RESTRICT` trap a delete+reinsert would spring (any tenant already pointing at `starter` or
  `individual` keeps resolving); one linear migration, fully reversible.
- DELETE+reinsert with fresh ids (rejected) — breaks the FK RESTRICT invariant task-1's own docstring
  names ("do NOT delete it — plan_id FK is RESTRICT"); would require a data migration to repoint any
  already-assigned tenant first, which Tin did not ask for and recon found no confirmed need for.
- A NEW `personal_tiers` table instead of repurposing `plans` rows (rejected) — the milestone's own
  "account_type is a DISCRIMINATOR, not a new entity" doctrine extends naturally to `plans`: one catalog
  table already serves both account tiers (`team`/`enterprise` sit alongside `starter`); a parallel table
  would fork the superadmin plan-assignment surface for no functional gain.
Must:
<must>
  - M1 — ONE additive+data migration (`down_revision="a7c3e9f1b2d4"`) restructures `plans` to the 5
    Tin-locked tiers: NEW column `base_price_usd_monthly NUMERIC(12,2) NULL` + CHECK
    `ck_plans_base_price_positive`; `UPDATE starter` (id unchanged) → `display_name='Starter'`,
    `seat_cap=1` (was 3), `base_price_usd_monthly=1.00` (budget/rpm/tpm UNCHANGED); `UPDATE individual` →
    `name='pro'`, `display_name='Pro'`, `base_price_usd_monthly=20.00` (seat_cap/budget/rpm/tpm
    UNCHANGED); `UPDATE team` → `base_price_usd_monthly=99.00` (all else unchanged); `enterprise`'s
    `base_price_usd_monthly` stays NULL; INSERT a NEW `free` row (`seat_cap=1`,
    `base_price_usd_monthly=NULL`, invented placeholder budget=5.00/rpm=30/tpm=20000 — DATA not SHAPE,
    mirrors task-1's own `individual` seed note).
  - M2 — `InvoiceGenerator` emits ONE `line_type='base'` line per (tenant, period) when the tenant's
    assigned plan's `base_price_usd_monthly` is non-NULL: a new `_load_base_price(session, tenant_id)`
    (mirrors `_load_seat_price`'s one-extra-SELECT/INNER-JOIN idiom, returns `None` for unplanned or
    NULL-base-price), folded into `raw_total`/`rounded_sum` BEFORE `total_usd`/`_reconcile_rounding_delta`
    (same immutable-before-insert ordering seat lines already use), persisted as
    `InvoiceLineRow(model_id='base', team_id=None, key_id=NIL_SEAT_KEY_ID, tags={},
    amount_usd=raw_amount_usd=base_price, prompt_tokens=completion_tokens=0, request_count=0,
    line_type='base')`. Inert (zero lines, zero total contribution) when `base_price_usd_monthly` is NULL
    — byte-identical to pre-task behavior for enterprise/unplanned tenants. A $0-usage tenant on a
    base-fee plan still gets an invoice inserted (the base line alone is sufficient; the existing
    "invoice inserted even with zero usage" row-insert path already covers this).
  - M3 — personal-signup default repoint: `SignupUseCase.execute`'s `account_type == "personal"` branch
    calls `get_plan_id_by_name("free")` (was `"individual"`); the R3/`SIGNUP_PLAN_UNPROVISIONED`
    fail-closed 500 shape is UNCHANGED, now triggered by `free`'s absence instead of `individual`'s.
  - M4 — `/pricing` no-drift: a NEW shared dashboard pricing-constants module holds the full 5-tier
    catalog figures (`name`, `displayName`, `basePriceUsd: number | null`) mirroring the backend seed
    EXACTLY; the existing 3-card `page.tsx` imports and renders its Starter/Team/Enterprise price text
    FROM this module (no page redesign — milestone Scope is explicitly "minimal", still 3 cards); a NEW
    test asserts the module's 5 values equal hardcoded expected figures matching the migration seed
    (free=NULL, starter=1.00, pro=20.00, team=99.00, enterprise=NULL) AND that the rendered page's price
    text derives from the module (not a re-hardcoded literal) — dashboard tests cannot hit the live DB, so
    this is a static, TEST-enforced no-drift, never a runtime fetch.
  - M5 — the migration is FULLY reversible: `downgrade()` restores the catalog to EXACTLY task-1's
    post-`a7c3e9f1b2d4` state — `pro`→`individual` rename undone, `starter`'s `display_name`/`seat_cap`
    reverted to `'Starter'`/3, the `free` row deleted, `base_price_usd_monthly` column + its CHECK
    dropped. Verified indirectly: `test_account_type_backfill.py::test_downgrade_removes_column_and_seed`
    downgrades PAST this migration to `22164094fd6b` and must still find zero `'individual'`-named rows
    there — which only holds if this migration's downgrade hands back a clean `'individual'` row for
    task-1's OWN (untouched) downgrade to then delete.
  - M6 — reconcile every stale/soon-stale test this catalog restructure touches, WITHOUT weakening any
    assertion (same behavior shape, updated names/counts/values only): `test_account_type_discriminator.py`
    (`_seed_individual_plan`→seeds `'free'`; `test_personal_signup_lands_on_individual_plan`→asserts
    `plan_id` == the seeded `'free'` plan's id; `test_personal_signup_without_individual_plan_fails_loud`→
    omits seeding `'free'`, same >=500 assertion); `test_account_type_backfill.py::
    test_individual_plan_seeded_seat_cap_one`→asserts the `'pro'` row (seat_cap=1,
    base_price_usd_monthly=20.00) at real `alembic head`; `test_plan_catalog.py::
    test_migration_seeds_exactly_3_named_plan_tiers`→asserts exactly 5 rows/names + this task's exact
    per-row values; `test_plan_enforcement_migration.py::
    test_migration_seeds_feature_flags_and_leaves_model_allowlist_null`→the `len(rows) == 3` assertion
    becomes 5 (name-set too); the per-row `model_allowlist`/`feature_flags` checks, keyed by name, stay
    untouched.
</must>
Reject:
<reject>
  - R1 — a personal signup when the `free` plan seed is absent -> the EXISTING `SIGNUP_PLAN_UNPROVISIONED`
    500 (`"ERR_SIGNUP_PLAN_UNPROVISIONED"`) — SAME shape as task-1's R3, now keyed off `free`.
  - R2 — an attempt to write `plans.base_price_usd_monthly <= 0` -> DB CHECK violation
    (`ck_plans_base_price_positive`, sqlstate `23514`), mirrors `ck_plans_seat_price_positive`'s own
    rejection shape.
  - R3 — invoice generation for a tenant on a plan with `base_price_usd_monthly IS NULL`
    (enterprise/unplanned) -> ZERO `'base'` lines emitted, ever — never a `$0.00` line row.
  - R4 — the `/pricing` page's rendered figures diverging from the backend catalog's seeded
    `base_price_usd_monthly` -> the no-drift test fails (build cannot go green; never silently shipped).
</reject>
After:
<after>
  - The `plans` table holds exactly the 5 Tin-locked tiers with the exact names/tiers/base prices/seat
    caps in the milestone table; every existing `plan_id` FK reference still resolves (no row deleted
    except by an explicit downgrade).
  - A team/enterprise/any-base-fee-plan tenant's monthly invoice always includes a `'base'` line equal to
    its plan's `base_price_usd_monthly`, independent of usage and seat count; a NULL-base-price tenant's
    invoice never does.
  - Personal signups land on `free` by default; `individual` no longer exists as a plan name (renamed to
    `pro`).
  - `/pricing`'s displayed figures are guaranteed by a red/green test — not manual eyeballing — to match
    the backend catalog.
  - Every task-1 test that referenced the `individual` plan by name, plus the 2 newly-found real-migration
    "exactly 3 plans" tests, pass again — same behavior asserted, names/counts/values updated.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ THE /pricing NO-DRIFT BINDING MECHANISM (M4) — lowest confidence because Tin's locked decision says
    "/pricing sync = a no-drift test... NOT a dynamic fetch" but does not dictate the EXACT binding shape;
    chosen: (a) a NEW shared TS constants module the page imports + (b) a test asserting the module's 5
    values equal HARDCODED expected figures mirroring the migration seed (dashboard tests cannot reach the
    live Postgres DB) — so the "no-drift" guarantee is only as strong as a human keeping the hardcoded test
    expectations in sync with the real migration seed by hand. A genuinely dynamic, backend-driven binding
    (e.g. a checked-in JSON fixture both the migration test and the dashboard test import) would be
    stronger but adds cross-app tooling outside this task's current scope. If wrong: a future data-only
    UPDATE to `base_price_usd_monthly` could silently drift from the marketing copy again without either
    suite catching it — low-frequency risk (plans are seed-only, no runtime CRUD), but real. Confirm or
    correct the exact module path/shape at freeze.
  - [ ] the invented placeholder budget/rpm/tpm figures for the NEW `free` plan (5.00/30/20000) — ranked
    #2; low cost/reversible (pure data, no schema impact — a follow-up data-only UPDATE if Tin wants
    different figures), mirrors task-1's own precedent for the `individual` seed.
  - [ ] the STARTER-PLAN REPURPOSE COLLISION (any business tenant already live-assigned to `starter` via
    the superadmin plan-assign endpoint silently becomes "personal $1, seat_cap=1") — ranked #3; Tin's
    locked decision directs the repurpose explicitly and recon found no confirmed tenant on `starter`
    today, but this is unverified (no production DB check performed); low likelihood, real if wrong — a
    one-query pre-deploy check (`SELECT count(*) FROM tenants WHERE plan_id = (SELECT id FROM plans WHERE
    name='starter')`) is worth running before shipping, named here so it isn't silently missed.
  - [x] id-preserving `UPDATE` (never DELETE+reinsert) for `starter`/`individual`→`pro` — confirmed:
    `plan_id` FK is `ON DELETE RESTRICT` (task-1's own docstring + `1e66a2cb51a6`), a delete would either
    fail (if referenced) or silently orphan; Tin's locked decision explicitly says "do NOT delete it".
  - [x] `InvoiceLineRow.key_id` reuses `NIL_SEAT_KEY_ID` (all-zero sentinel) for the aggregate base line —
    confirmed: the column is `NOT NULL` (`orm.py:117`), and the `'seat'` aggregate line already
    established this exact sentinel-reuse precedent for a line with no single owning user.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: 5-tier catalog seeded with exact figures   # M1
  Given the migration runs on top of a7c3e9f1b2d4
  When it completes
  Then plans holds exactly 5 rows: free(personal, base_price NULL, seat_cap 1) /
    starter(personal, base_price 1.00, seat_cap 1) / pro(personal, base_price 20.00, seat_cap 1) /
    team(business, base_price 99.00, seat_cap NULL) / enterprise(business, base_price NULL, seat_cap NULL)
  And starter/pro/team/enterprise keep their EXISTING ids (repurposed in place, never deleted+reinserted)

Scenario: a base-fee plan tenant is billed a flat base line even at $0 usage   # M2
  Given a tenant assigned to the team plan (base_price_usd_monthly=99.00) with zero usage_records for
    the period
  When generate_for_tenant runs for that period
  Then an invoice is inserted with exactly one 'base' invoice_lines row, amount_usd=99.00
  And total_usd includes the 99.00 base amount

Scenario: personal signup lands on the free plan by default   # M3
  Given the free plan is seeded
  When a user signs up with account_type="personal"
  Then the tenant's plan_id is the free plan's id
  And the tenant is NOT assigned the pro (formerly individual) plan

Scenario: /pricing figures match the seeded backend catalog   # M4
  Given the dashboard pricing-constants module's 5 tier figures
  When the no-drift test runs
  Then each figure equals the seeded backend base_price_usd_monthly for that plan
  And the rendered /pricing page's Starter/Team/Enterprise price text derives from that same module

Scenario: migration downgrade restores the exact pre-migration catalog   # M5
  Given this task's migration is applied (head)
  When it is downgraded back to a7c3e9f1b2d4
  Then pro is renamed back to individual, starter's display_name/seat_cap revert to Starter/3, the free
    row is gone, and base_price_usd_monthly no longer exists as a column
  And downgrading FURTHER to 22164094fd6b (task-1's own downgrade) still finds zero rows named individual

Scenario: task-1's stale plan-name tests are reconciled, not weakened   # M6
  Given the reconciled test suite (personal-signup-lands-on-free-plan, pro-seat_cap-one,
    exactly-5-plans x2)
  When the full gateway suite runs
  Then every reconciled test still asserts the SAME behavior shape it always did (personal signup lands
    on the seeded default plan; the migration seeds the complete/correct named catalog) — just against
    the new names/counts/values

Scenario: an already-issued invoice never gets a duplicate base line on re-run   # edge case (idempotency)
  Given an invoice already exists for (tenant_id, period_start) including its base line
  When generate_for_tenant is called again for the same period
  Then the ON CONFLICT DO NOTHING no-op fires and returns None
  And no second 'base' line is ever written

Scenario: personal signup rejected loudly when the free plan is missing   # R1
  Given the free plan is NOT seeded
  When a user signs up with account_type="personal"
  Then the request fails with >=500 (ERR_SIGNUP_PLAN_UNPROVISIONED)
  And no tenant is left silently unplanned

Scenario: a non-positive base_price_usd_monthly is rejected by the DB   # R2
  Given an attempt to UPDATE/INSERT a plans row with base_price_usd_monthly = 0 or negative
  When the write executes
  Then the ck_plans_base_price_positive CHECK raises a 23514 violation
  And the row is left unchanged

Scenario: a NULL-base-price tenant's invoice never carries a base line   # R3
  Given a tenant on the enterprise plan (base_price_usd_monthly NULL) or an unplanned tenant
  When generate_for_tenant runs
  Then zero 'base' invoice_lines rows are written for that invoice
  And total_usd is unaffected (byte-identical to pre-task behavior)

Scenario: a hand-edited /pricing price fails the no-drift test   # R4
  Given the page's Team card is hand-edited to show "$199" without updating the pricing-constants module
  When the no-drift test runs
  Then it fails
  And the build cannot go green until the figures are reconciled

Scenario: a business tenant already on the repurposed starter plan is not silently orphaned   # edge case
  Given a business tenant with plan_id = the starter plan's id, assigned BEFORE this migration via the
    superadmin plan-assign endpoint
  When this migration runs
  Then the tenant's plan_id FK still resolves (same row, id unchanged)
  And the tenant now reads its plan as "Starter, personal $1, seat_cap 1" even though its OWN
    account_type stays "business" — a documented, Tin-directed data-shape change, not a schema violation
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Schema (additive column + data restructure, ONE migration, down_revision="a7c3e9f1b2d4", reversible):
  ALTER TABLE plans ADD COLUMN base_price_usd_monthly NUMERIC(12,2) NULL
  ADD CONSTRAINT ck_plans_base_price_positive
      CHECK (base_price_usd_monthly IS NULL OR base_price_usd_monthly > 0)
  UPDATE plans SET display_name='Starter', seat_cap=1, base_price_usd_monthly=1.00 WHERE name='starter'
  UPDATE plans SET name='pro', display_name='Pro', base_price_usd_monthly=20.00 WHERE name='individual'
  UPDATE plans SET base_price_usd_monthly=99.00 WHERE name='team'
  -- enterprise: base_price_usd_monthly stays NULL, no UPDATE needed
  INSERT INTO plans (name='free', display_name='Free', seat_cap=1, base_price_usd_monthly=NULL,
                      budget_usd_monthly_default=5.00, rpm_limit_default=30, tpm_limit_default=20000)
  downgrade: DELETE FROM plans WHERE name='free'
             UPDATE plans SET name='individual', display_name='Individual', base_price_usd_monthly=NULL
                 WHERE name='pro'
             UPDATE plans SET display_name='Starter', seat_cap=3, base_price_usd_monthly=NULL
                 WHERE name='starter'
             UPDATE plans SET base_price_usd_monthly=NULL WHERE name='team'
             DROP CONSTRAINT ck_plans_base_price_positive
             DROP COLUMN base_price_usd_monthly

Final catalog (post-migration; id-preserving — starter/pro/team/enterprise keep their existing ids):
  free       | Free       | personal | base_price=NULL  | seat_cap=1    # NEW row, personal signup default
  starter    | Starter    | personal | base_price=1.00  | seat_cap=1    # repurposed (was business, cap 3)
  pro        | Pro        | personal | base_price=20.00 | seat_cap=1    # renamed from `individual`
  team       | Team       | business | base_price=99.00 | seat_cap=NULL
  enterprise | Enterprise | business | base_price=NULL  | seat_cap=NULL

ORM:
  PlanRow.base_price_usd_monthly: Mapped[Decimal | None]   # orm.py, mirrors seat_price_usd_monthly

Invoice generation (billing/application/invoice_generator.py):
  _load_base_price(session, tenant_id) -> Decimal | None   # mirrors _load_seat_price, INNER JOIN plans
  generate_for_tenant(): folds ONE 'base' InvoiceLineRow (model_id='base', team_id=None,
    key_id=NIL_SEAT_KEY_ID, tags={}, amount_usd=raw_amount_usd=base_price, prompt_tokens=
    completion_tokens=0, request_count=0, line_type='base') into raw_total/rounded_sum BEFORE
    total_usd/_reconcile_rounding_delta; inert (zero lines) when base_price is None.

Signup (tenants/application/use_cases.py):
  SignupUseCase.execute(..., account_type="personal") -> get_plan_id_by_name("free")   # was "individual"
  R1 unchanged: missing plan -> IndividualPlanMissingError -> router 500 SIGNUP_PLAN_UNPROVISIONED

Dashboard (apps/dashboard):
  lib/pricing-catalog.ts (NEW): PRICING_CATALOG: { name, displayName, basePriceUsd: number | null }[5]
    — free/starter/pro/team/enterprise, values equal the migration's seeded base_price_usd_monthly
  app/(marketing)/pricing/page.tsx: TIERS[Starter].price / TIERS[Team].price / TIERS[Enterprise].price
    derive from PRICING_CATALOG (still 3 rendered cards — no IA change, milestone Scope is "minimal")
  tests/pricing-catalog-no-drift.test.ts (NEW): asserts PRICING_CATALOG's 5 figures == the hardcoded
    expected seed figures AND the page's rendered price text == the catalog's formatted value

Reject responses:
  R1 -> 500 { error: "ERR_SIGNUP_PLAN_UNPROVISIONED" }        # unchanged shape, now keyed off 'free'
  R2 -> DB CHECK violation (sqlstate 23514, ck_plans_base_price_positive)
  R3 -> (no response — an absence: zero 'base' lines, verified by the invoice's own persisted line set)
  R4 -> pricing-catalog-no-drift test failure (CI-level rejection, not a runtime response)
```

Glossary deltas:
  base fee: a flat, recurring monthly charge (`plans.base_price_usd_monthly`) billed via a
    `line_type="base"` invoice line, independent of usage AND seat count; NULL = no base fee
    (enterprise/unplanned).
  5-tier catalog: free/starter/pro (personal) + team/enterprise (business) — supersedes the 3-tier
    starter/team/enterprise catalog and the transient post-task-1 4-row state (adds `free`, renames
    `individual`→`pro`). [folded foundation-version 53]

Least-sure flag surfaced at freeze: [spec] the /pricing no-drift binding mechanism (§1 ⚠) — a NEW shared
  TS constants module + a test asserting it against HARDCODED expected figures (dashboard tests can't
  reach the live DB), not a fully dynamic backend-driven binding. Low-cost to strengthen later (e.g. a
  shared JSON fixture) if Tin wants a tighter guarantee.
Status: FROZEN @ v1 — approved by auto (project-lead)
Reported: no

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/billing/application/invoice_generator.py`
  `apps/gateway/src/gateway/tenants/infrastructure/orm.py`
  `apps/gateway/src/gateway/tenants/application/use_cases.py`
  `apps/gateway/src/gateway/tenants/domain/errors.py`
  `apps/gateway/src/gateway/tenants/api/router.py`
  `apps/gateway/src/gateway/core/error_catalog.py`
  `apps/gateway/migrations/versions/`
  `apps/gateway/tests/plan_tiers_and_base_fee/`
  `apps/gateway/tests/migrations/`
  `apps/gateway/tests/account_type_discriminator/`
  `apps/gateway/tests/plan_catalog/`
  `apps/gateway/tests/plan_enforcement/`
  `apps/dashboard/lib/`
  `apps/dashboard/app/`
  `apps/dashboard/tests/`
Strategy (ordered batches): 1. migration (column+CHECK+restructure) + ORM; 2. invoice base-line fold
  (mirror seat fold); 3. signup repoint individual→free; 4. dashboard constants module + page wiring +
  no-drift test; 5. M6 reconcile the 4 sibling tests (no weakening).

Persona (required): billing-precision-engineer (schema/migration + invoice-line rigor); generic otherwise.
Spawn isolation (default): delegated to add-build inline (shared tree) — this task's migration is the sole
  new alembic head (no parallel migration build to isolate); the orchestrator verified the result.
Known-problem fixes: additive column+CHECK mirroring seat_price; id-preserving UPDATE (never DELETE — FK
  RESTRICT); base fold BEFORE _reconcile_rounding_delta; whole-dollar base = exact rounding.
Strategy actually used: as planned — one migration 113ebdbe9f09 (down_revision a7c3e9f1b2d4) restructures
  to 5 tiers + adds base_price_usd_monthly; _load_base_price + one 'base' InvoiceLineRow folded into
  raw_total/rounded_sum before reconciliation; signup personal→'free'; dashboard PRICING_CATALOG module +
  page derives from it + no-drift test; 4 sibling tests reconciled to 5-row catalog (intent preserved).
Safety rule (feature-specific): base line folded into total_usd from the FIRST invoice write (issued
  invoice is immutable); strict no-op when base_price is None (byte-identical for enterprise/unplanned).
Code lives in: `apps/gateway/src/`, `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 84 (task + reconciled) + 155 (invoice/seat/plan_seat_cap/signup/tiered) + 21 (full migrations dir) backend; 3 no-drift + 12 existing pricing-page dashboard vitest — all green
- [x] coverage did not decrease — additive column/method/line + new+reconciled tests exercise every new symbol
- [x] no test or contract was altered during build — §3 FROZEN untouched; the 4 sibling-test edits are the CONTRACTED M6 reconciliation (assertion intent preserved: personal→default plan, "exactly N tiers" → 5, `pro` replaces `individual`), NOT weakening
- [x] the green was EARNED — refute-read below (self); base-line math + no-drift binding + reconciliations probed
- [x] concurrency / timing safe — base line folded into raw_total/rounded_sum BEFORE total_usd/_reconcile_rounding_delta, persisted in the SAME `session.begin()` as the invoice insert (immutable-once-issued); `_load_base_price` reads immutable seed data (no TOCTOU); strict no-op when base_price None
- [x] no exposed secrets, injection openings, or unexpected dependencies — pure schema/data + SELECT; no new deps
- [x] layering & dependencies follow CONVENTIONS.md — `_load_base_price` mirrors `_load_seat_price`; migration additive+reversible; FE constants module is presentation-only
- [ ] a person reviewed and approved the change — auto-gated (autonomy:auto, no security/concurrency/arch residue); full detached BE suite + human review deferred to milestone-close/PR

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] after `alembic upgrade head`, `plans` holds EXACTLY 5 rows — test_plan_tiers_and_base_fee migration test + reconciled test_plan_catalog (len==5, name-set {free,starter,pro,team,enterprise}) green
- [x] a tenant on a $99 base-fee plan with ZERO usage gets an invoice with EXACTLY one `base` line == $99.00 and total_usd==$99.00 — test_base_fee_plan_zero_usage_gets_flat_base_line reads it back through the real invoice-detail endpoint
- [x] a tenant on a NULL-base plan gets ZERO `base` lines — test_null_base_price_plan_gets_zero_base_lines
- [x] a personal signup lands on `free`; `individual` no longer a plan name — reconciled test_personal_signup_lands_on_individual_plan (asserts free id) + migration test_plan_tiers (individual→pro rename)
- [x] the `/pricing` figures derive from the shared module and equal the seed base_prices — pricing-catalog-no-drift.test.ts (module==EXPECTED_SEED {free:null,starter:1,pro:20,team:99,ent:null} + page renders from module + drift-detection case)
- [x] migration downgrades cleanly — test_account_type_backfill::test_downgrade_removes_column_and_seed + the task's own downgrade test green (full migrations dir 21 passed)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `_load_base_price` called in generate_for_tenant; NIL_SEAT_KEY_ID imported from seat_pricer; base line persisted; `get_plan_id_by_name("free")` wired in SignupUseCase; PRICING_CATALOG imported by page.tsx + no-drift test; migration 113ebdbe9f09 at head
- [x] DEAD-CODE (code) — no orphaned symbol; every new symbol exercised by a test
- [x] SEMANTIC (prose) — read the frozen §3 + the invoice fold + the no-drift test in full: confirmed base fold mirrors seat fold, no-drift binds page↔catalog↔expected-seed, M6 reconciliations preserve intent

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 cites still resolves — _load_seat_price/NIL_SEAT_KEY_ID (seat_pricer), generate_for_tenant (invoice_generator:247), PlanRow (orm), SignupUseCase.execute, TIERS (pricing page) all resolve; alembic head is 113ebdbe9f09 (this task)
- [x] any anchor that moved/renamed — `individual` plan RENAMED → `pro` by this task's own migration (the intended change, named here); no other anchor moved

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self · adversarially checked: (1) base-line math — read invoice_generator: base_price folded into BOTH raw_total AND rounded_sum before total_usd/_reconcile, so total is correct and the reconcile delta never lands on the base line (delta only touches usage lines); the test reads the line back through the REAL invoice-detail endpoint (not a mock) and asserts one line==$99 + total==$99; (2) no-op path — confirmed `if base_price is not None` guards BOTH the fold and the persist, so enterprise/unplanned tenants are byte-identical to pre-task (R3 test asserts zero base lines); (3) no-drift is a REAL binding — EXPECTED_SEED figures are hardcoded and asserted against the module AND the page render, with an explicit drift-detection case; (4) M6 reconciliations preserve assertion intent (personal→default plan, exactly-5-tiers, pro-replaces-individual), verified by reading each edited test — no vacuous/weakened assert.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: self
1. Security: CLEAR — no new auth surface; base fee is server-set seed data (no user input); a personal user cannot self-assign a paid plan (signup always → free). The starter-repurpose collision (a live business tenant on old `starter` silently becomes personal $1) is a data-migration effect Tin directed explicitly; recon found no tenant on `starter` — a pre-deploy `SELECT count(*) ... plan_id=starter` check is flagged in §1 assumptions (operational, not a code security gap)
2. Concurrency: CLEAR — base line in the same atomic invoice txn; immutable-once-issued ordering respected; seed-data read, no TOCTOU
3. Architecture: CLEAR — additive column+CHECK; single reversible migration head; FROZEN usage_records untouched; clean layering (mirror of seat fold)
Verdict: PASS
Residue: the /pricing no-drift EXPECTED_SEED figures are hand-kept in sync with the migration seed (the §3 least-sure flag) — low risk (plans seed-only) + a drift-detection test; strengthen later with a shared fixture if desired
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
- [AI] build — strategy used: as planned — one migration 113ebdbe9f09 (down_revision a7c3e9f1b2d4) restructures to 5 tiers + adds base_price_usd_monthly; _load_base_price + one 'base' InvoiceLineRow folded into raw_total/rounded_sum before reconciliation; signup personal→'free'; dashboard PRICING_CATALOG module + page derives from it + no-drift test; 4 sibling tests reconciled to 5-row catalog (intent preserved).
- [AI] verify — gate PASS (reviewed by auto (project-lead, autonomy:auto))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

