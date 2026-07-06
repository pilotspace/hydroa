# TASK: Plan/tier data model — resolves platform-tenant-backed-usage scope

slug: plan-catalog · created: 2026-07-04 · stage: production
milestone: platform-access-plan
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `TenantRow` (`tenants/infrastructure/orm.py:61` `kind` server_default="customer"; `markup_pct`;
    `budget_usd_monthly` nullable=unlimited) — this task adds the plan/tier association; no existing
    column is renamed or repurposed, only additively joined/extended.
  - `ApiKeyRow.rpm_limit`/`tpm_limit` (`keys/infrastructure/orm.py:84-85`, nullable Integer, CHECK
    `>0` if set) — the existing PER-KEY rate-ceiling primitive (`rate-limits` FROZEN@v3) a plan's
    rate tier must compose with, not replace; enforced today at the key level only, no tenant-level
    rate concept exists yet.
  - `Settings.bandwidth_tokens_per_sec`/`bandwidth_burst_tokens`/`bandwidth_max_wait_seconds`
    (`core/config.py:471-478`, v36) — a GLOBAL (not per-tenant, not per-key) Redis token-bucket,
    default-OFF (0 = disabled). Confirmed NOT the same enforcement dimension as rpm/tpm; a plan's
    "rate" dimension maps to rpm/tpm, never to this global pacing knob (per MILESTONE.md's own
    Shared decision).
  - `credential-resolution-seam` TASK.md §3 (FROZEN@v1) — literal frozen text: "served-model
    provider with no configured tenant credential → reject ERR_PROVIDER_KEY_MISSING (4xx), NO
    platform-key fallback." Re-confirmed this session, verbatim. This is the load-bearing fact for
    the scope call below: no tenant proxy usage rides on the platform tenant's own credential today
    — that surface doesn't exist yet (would be `platform-key-default`'s to build).
  - `apps/dashboard/app/(marketing)/pricing/page.tsx` (re-confirmed) — Server Component, frozen §3
    v1 "PUBLIC — no cookie check, no authed fetch, no redirect... Prices are representative
    placeholders (no commercial model finalised) — copy, not a commitment." Static Starter/Team/
    Enterprise tier copy, completely unwired to any backend enforcement today.
Context (working folder): `.add/milestones/platform-access-plan/MILESTONE.md` (Scope/Shared
  decisions/the flagged scope-ambiguity note this task must resolve); `.add/tasks/rate-limits/
  TASK.md` §0 (the "key < tenant < model" hierarchy it anticipated as a follow-on — this task is
  that follow-on, for the tenant layer).
Honors (patterns / conventions):
  - Reuse-over-invent: `authorize_tenant_scope`/`emit_platform_audit` (both FROZEN@v1) for the
    superadmin cross-tenant assign/view surface — no parallel authz/audit primitive.
  - Additive-only column/table growth — mirrors how `budget_usd_monthly` itself was added to
    `TenantRow` without touching existing rows' behavior (nullable = no-op default).
Anchors the contract cites: `TenantRow` (orm.py:61), `ApiKeyRow.rpm_limit`/`tpm_limit`
  (orm.py:84-85), `credential-resolution-seam` TASK.md §3 (FROZEN@v1), `authorize_tenant_scope`/
  `emit_platform_audit` (authz.py / platform_audit.py:36).
Issues/Risks (→ feed §1):
  - **THE scope call this task exists to make** (MILESTONE.md's own "⚠ GENUINELY OPEN" item):
    literal reading of the goal line's "platform-tenant-backed usage" (the reserved GLOSSARY term
    "platform tenant") would scope 2 of 6 sibling tasks (`plan-budget-enforcement`,
    `plan-rate-enforcement`) to a credential-fallback surface that is CONFIRMED not to exist yet
    (`platform-key-default`, still queued/unbuilt) — "nothing to meter otherwise" per the milestone
    doc's own words. DEFAULT ADOPTED HERE (disclosed, not silent, pending Tin's override): the LOOSE
    reading — "platform" = this SaaS platform generally, i.e. a plan governs a customer tenant's
    overall usage, independent of BYOK/credential source. Reasoning: (1) Tin's own explicit
    instruction to size/build this milestone NOW, in parallel with `tenant-impersonation` and out of
    the original roadmap order (`platform-key-default` was sequenced BEFORE `platform-access-plan`)
    only makes sense if this milestone has real, buildable substance today — the literal reading
    would leave its two enforcement tasks inert until a different, unstarted milestone ships; (2)
    the pricing page's own tier copy (rate/spend-analytics features) reads as governing usage
    generally, the ordinary industry meaning of a subscription "plan"; (3) the literal reading is a
    narrow technical accident of which GLOSSARY term the goal line happened to reuse, not a
    considered product decision. If Tin intended the literal reading, `plan-budget-enforcement`/
    `plan-rate-enforcement` need re-sequencing to depend on `platform-key-default` — a bounded,
    contained correction (this task's own schema/catalog shape is unaffected either way; only the
    two downstream enforcement tasks' applicability changes).
  - Enum-vs-plans-table (the milestone doc's second flagged risky contract): a hardcoded enum is
    cheaper but every tier change is a migration; a `plans` table is one join heavier but lets a
    superadmin adjust ceilings without a deploy. This task's own §1 must pick one, not defer it.
  - Seat-cap's cross-milestone dependency on `team-member-invite` (independently re-verified this
    session: `member-invite-issuance`'s accept-endpoint is the second provisioning entry point
    `plan-seat-cap` will need) is `plan-seat-cap`'s concern, not this task's — noted here only so
    this task's own catalog shape doesn't foreclose a seat-cap field later (additive column, no
    redesign needed).
Related intent: `.add/milestones/platform-access-plan/MILESTONE.md` goal + rationale + the
  "GENUINELY OPEN" shared/risky-contract note (drafted and independently verified this session);
  `.add/PROJECT.md` goal (a tenant can set up → log in → call any model → see accurate, billable
  cost tracking) — this task extends "accurate... cost tracking" into "governed, tiered... cost
  tracking."
Ground SHA: 9740f21

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Plan/tier catalog — a new `plans` reference table (Starter/Team/Enterprise, seeded via
migration) naming a customer tenant's usage-governance profile, an additive `TenantRow.plan_id`
(nullable FK) associating a tenant with one, an additive `TenantRow.seat_cap` per-tenant override
column, and a superadmin-only cross-tenant view/assign/change surface (`GET /admin/platform/plans`,
`GET`/`PUT /admin/platform/tenants/{tenant_id}/plan`) reusing `authorize_tenant_scope`/
`emit_platform_audit` verbatim. Resolves the milestone's "platform-tenant-backed usage" scope
question per §0 GROUND's disclosed DEFAULT (the LOOSE reading — a plan governs a customer tenant's
overall usage generally, independent of BYOK/credential source — adopted here as settled, pending
Tin's override, not re-litigated). This task is the CATALOG + ADMIN SURFACE ONLY: it defines what a
plan IS and lets a superadmin attach one to a tenant; it does NOT itself wire $ budget enforcement,
rate enforcement, or seat-cap enforcement into any proxy/provisioning code path — those remain
`plan-budget-enforcement`/`plan-rate-enforcement`/`plan-seat-cap`'s own jobs (see §3 Non-goals).

Framings weighed:
  - **Catalog shape: a `plans` reference table** (id/name/display_name/seat_cap/
    budget_usd_monthly_default/rpm_limit_default/tpm_limit_default), with `TenantRow.plan_id` FK +
    `TenantRow.seat_cap` as a per-tenant override column **(CHOSEN)** — vs. a CHECK-constrained enum
    column directly on `TenantRow` (`plan_tier TEXT CHECK IN ('starter','team','enterprise')`,
    mirroring `kind`'s own convention) with tier ceilings hardcoded in a Python constants dict
    **(REJECTED)**. The enum is cheaper today (no new table, no seed migration, zero joins ever) but
    every tier-ceiling tweak — and MILESTONE.md's own named "Enterprise: custom" case, a per-tenant
    NEGOTIATED ceiling — would need a schema migration under the enum design; that is precisely the
    "tier catalog so rigid every pricing tweak needs a deploy" failure mode this feature class is
    prone to, and pricing/packaging changes on a business cadence faster than code deploys. The
    table costs one extra join, but ONLY on the low-QPS superadmin admin path (view/assign) — no
    enforcement code path this task adds ever reads `plans` on any hot path (see below), so the "one
    join heavier" cost MILESTONE.md itself named is paid nowhere latency-sensitive. This is a
    DEPARTURE from MILESTONE.md's own stated Recommendation (which leaned enum+override-columns) —
    flagged explicitly for the freeze reviewer.
  - **Where do the $ budget / rate ceiling DEFAULTS live for a plan**: exposed READ-ONLY via
    `TenantRow.plan_id -> plans.budget_usd_monthly_default/rpm_limit_default/tpm_limit_default` (a
    join walked only by the admin view/assign path) **(CHOSEN)** — vs. copying those two dimensions
    down onto new `TenantRow`/`ApiKeyRow` override columns the same way `seat_cap` is copied
    **(REJECTED for this task)**. MILESTONE.md's own Shared decision is explicit that a plan's $
    ceiling maps onto the EXISTING `budget_usd_monthly` column and its rate ceiling onto the
    EXISTING per-key `rpm_limit`/`tpm_limit` columns — i.e. these two dimensions REUSE enforcement
    surfaces already governed by a DIFFERENT, explicitly-not-mine "ceiling vs default" decision
    (owned identically by `plan-budget-enforcement`/`plan-rate-enforcement`). Writing a copy into
    those existing columns myself — or inventing parallel override columns for them — would
    silently prejudge that question toward "one-time default seed" before either sibling task has
    decided. `seat_cap` has no existing column to reuse (a wholly new dimension this milestone
    introduces), so copying its default down onto a dedicated `TenantRow.seat_cap` override column
    prejudges nothing and directly serves the milestone's own named Enterprise/custom-negotiation
    need.
  - **Endpoint scope: build the superadmin view/assign/change endpoint AS PART OF this task**
    **(CHOSEN, per this session's explicit instruction)** — vs. shipping ONLY the schema+migration
    and leaving the endpoint entirely to the sibling `plan-assignment-admin` task MILESTONE.md's own
    task breakdown names separately (`depends-on: plan-catalog`) **(the MILESTONE.md-literal reading,
    not taken here)**. Musts M3/M7 (platform-tenant exemption, unassign) are not scenario-testable at
    all without SOME mutation surface to attempt and reject — building a minimal, real assign/view
    pair now (mirroring `platform_tenant_config_router.py`'s `GET/PUT
    /admin/platform/tenants/{tenant_id}/budget` shape almost exactly) makes this task's own Musts
    independently verifiable. This creates a real scope-overlap question for `plan-assignment-admin`'s
    own eventual TASK.md — flagged explicitly below, not silently resolved.
  - **HTTP verb for assign/change: PUT** (whole-resource set, mirrors `PUT /admin/budget` / `PUT
    .../role`) **(CHOSEN)** — vs. PATCH (mirrors the per-key governance fields' own partial-update
    idiom, whose "omit = no change" / "null = unlimited" three-state semantic this task's `seat_cap`
    field also borrows) **(considered, not chosen)**. The endpoint's PRIMARY field (`plan_id`) is a
    single categorical reassignment — closer in spirit to `PUT .../role` than to the multi-field
    governance PATCH; `seat_cap`'s omit/null/value semantic is a borrowed convenience, not evidence
    the endpoint is fundamentally partial-update-shaped.

Must:
<must>
  - **[M1]** A NEW `plans` table exists as the tier catalog, seeded via migration with exactly 3
    rows — `starter` / `team` / `enterprise` — each carrying `seat_cap`,
    `budget_usd_monthly_default`, `rpm_limit_default`, `tpm_limit_default` (independently nullable =
    unlimited/negotiated) and a human-readable `display_name`. No application code path creates,
    edits, or deletes a `plans` row in v1 (superadmin tier-DEFINITION CRUD is an explicit non-goal —
    §3) — the table exists so a later task can add that without a schema redesign.
  - **[M2]** `TenantRow` gains TWO new additive, nullable columns: `plan_id` (FK -> `plans.id`, ON
    DELETE RESTRICT) and `seat_cap` (INTEGER, CHECK `> 0` if set — mirrors `ApiKeyRow.rpm_limit`'s
    own CHECK convention). Both are NULL, with NO backfill, for every pre-existing tenant row AND
    for every newly-signed-up tenant (no auto-assignment of a default plan at signup) — unplanned is
    the universal starting state until a superadmin explicitly acts.
  - **[M3]** The platform tenant (`TenantRow.kind = 'platform'`) can never hold a plan — enforced
    BOTH by an application-level reject (`PUT .../plan` targeting the platform tenant's own
    `tenant_id` -> 403, before any write) AND, defense-in-depth, by a DB-level `CHECK` constraint on
    `tenants` itself (`plan_id IS NULL OR kind != 'platform'`) that holds even if application code
    were bypassed — mirrors this exact table's own existing `ck_tenants_kind` /
    `tenants_platform_kind_uidx` defense-in-depth precedent.
  - **[M4]** `GET /admin/platform/plans` returns the full plan catalog (all `plans` rows) —
    SUPERADMIN only, no `tenant_id` in the path (mirrors `platform_tenants_router.py`'s bulk
    `list_platform_tenants` shape); fires a targetless `emit_platform_audit` call
    (`target_tenant_id=None`, action `"platform.plan.list"` — mirrors `emit_platform_audit`'s own
    documented "bulk, targetless... system-level event" case).
  - **[M5]** `GET /admin/platform/tenants/{tenant_id}/plan` returns the target tenant's current plan
    (nested full `plans` row, or `null` if unplanned) plus the tenant's own resolved `seat_cap`
    override (or `null`) — gated `require_superadmin` -> `authorize_tenant_scope(identity,
    tenant_id)` -> `get_tenant_by_id` (404 if missing), mirroring `platform_users_router.py`'s
    `_require_target_tenant` helper / `platform_tenant_config_router.py`'s equivalent inline gate
    sequence exactly. Fires `emit_platform_audit` (action `"platform.plan.view"`, `target_tenant_id`
    = path `tenant_id`).
  - **[M6]** `PUT /admin/platform/tenants/{tenant_id}/plan` assigns, changes, or clears
    (`plan_id: null`) the target tenant's plan in one request. Gate order: `require_superadmin` ->
    `authorize_tenant_scope` -> `get_tenant_by_id` (404) -> target tenant is NOT `kind='platform'`
    (403, M3) -> (if `plan_id` non-null) it resolves to a real `plans` row (404) -> `seat_cap`
    cross-field validation (422) -> write -> `emit_platform_audit` (action `"platform.plan.assign"`,
    `target_tenant_id` = path `tenant_id`, metadata carries the OLD and NEW `plan_id`/`seat_cap`) ->
    200 with the updated view shape (M5's response body).
  - **[M7]** Setting `plan_id: null` clears BOTH `plan_id` AND `seat_cap` back to `null` atomically,
    in the same write — a full "unassign" byte-identical to a tenant that was never assigned a plan.
    A non-null `seat_cap` supplied alongside `plan_id: null` in the same request body is REJECTED
    (422) — never silently dropped or silently applied to a now-unplanned tenant.
  - **[M8]** Assigning a non-null `plan_id` with `seat_cap` OMITTED from the request body copies the
    tenant's `seat_cap` down FROM the target plan's own `seat_cap` value at that moment (a one-time
    copy, not a live join) — never silently left at a stale PRIOR plan's value when changing tiers.
  - **[M9]** Assigning a non-null `plan_id` WITH `seat_cap` explicitly supplied (any value, including
    `null` for explicitly-unlimited) overrides the plan's own default for THAT tenant only — must be
    a positive integer or literal `null`; zero or negative is rejected (422, mirrors
    `ApiKeyRow.rpm_limit`'s `> 0` convention).
</must>
Reject:
<reject>
  - **[R1]** Missing/invalid bearer JWT, on any of the 3 endpoints -> "ERR_AUTH_TOKEN_MISSING" /
    "ERR_AUTH_TOKEN_INVALID" (401, reused)
  - **[R2]** Caller's role is not SUPERADMIN, on any of the 3 endpoints -> "ERR_AUTH_FORBIDDEN" (403,
    reused) — identically covers every one of the 6 self-service roles, regardless of their own
    tenant's permissions
  - **[R3]** GET or PUT `.../plan` — `tenant_id` does not resolve to a real tenant ->
    "ERR_TENANT_NOT_FOUND" (404, reused)
  - **[R4]** PUT `.../plan` — target tenant's `kind == 'platform'` -> "ERR_PLAN_TENANT_INELIGIBLE"
    (403, NEW)
  - **[R5]** PUT `.../plan` — `plan_id` (non-null) does not resolve to a real `plans` row ->
    "ERR_PLAN_NOT_FOUND" (404, NEW)
  - **[R6]** PUT `.../plan` — `seat_cap` is zero or negative -> "ERR_PAYLOAD_INVALID" (422, reused)
  - **[R7]** PUT `.../plan` — `plan_id: null` with a non-null `seat_cap` in the same request ->
    "ERR_PAYLOAD_INVALID" (422, reused)
  - **[R8]** (schema-level, not an HTTP case) a direct INSERT/UPDATE on `tenants` setting a non-null
    `plan_id` on a `kind='platform'` row, bypassing application code entirely -> rejected by the DB
    CHECK constraint itself (exercised by a migration/model-level test, not a Gherkin HTTP scenario)
</reject>
After:
<after>
  - After M1: exactly 3 `plans` rows exist post-migration (`starter`/`team`/`enterprise`), each with
    its seeded ceiling defaults.
  - After M2, before any assignment: every existing tenant AND every newly-signed-up tenant has
    `plan_id=NULL`, `seat_cap=NULL`; every proxy/budget/rate/provisioning code path is COMPLETELY
    unchanged (no enforcement code reads these two new columns yet — no sibling task has wired them).
  - After M3: the platform tenant's `plan_id` is NULL, permanently; no superadmin action can change
    that (403 on any attempt); the DB CHECK constraint independently guarantees this even if
    application code were bypassed.
  - After M6 (successful assign/change): the target tenant's `plan_id`/`seat_cap` durably reflect the
    new values; exactly one `platform.plan.assign` audit row was written attributing the REAL calling
    superadmin as actor; no other tenant's row was read or written.
  - After ANY reject (R3-R7): the target tenant's `plan_id`/`seat_cap` are COMPLETELY unchanged from
    immediately before the request — no partial write, no audit event fired.
  - After M7 (unassign): `plan_id=NULL`, `seat_cap=NULL` — indistinguishable from a tenant that was
    never assigned a plan.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The exact tier NAMES/COUNT (`starter`/`team`/`enterprise`, exactly 3) and every seeded ceiling
    NUMBER (`seat_cap`, `budget_usd_monthly_default`, `rpm_limit_default`, `tpm_limit_default` per
    tier) are INVENTED by this draft, grounded only loosely in `apps/dashboard/.../pricing/page.tsx`'s
    own tier copy — itself explicitly disclosed there as "representative placeholders (no commercial
    model finalised) — copy, not a commitment." Lowest confidence because Tin has never confirmed a
    real commercial model anywhere in this codebase. If wrong: cheap to fix — these are DATA rows in
    a migration seed, not a contract SHAPE; a follow-up data-only migration (or a future superadmin
    tier-CRUD endpoint) changes the numbers without touching any Must/Reject/route in this contract.
  ⚠ Building the superadmin view/assign/change endpoint (M4-M9) AS PART OF `plan-catalog`, rather
    than leaving it entirely to the sibling `plan-assignment-admin` task MILESTONE.md's own task
    breakdown names separately (`depends-on: plan-catalog`) — this draft follows this session's
    explicit instruction (needed to make M3/M7's platform-tenant-exemption and unassign behavior
    scenario-testable at all). If wrong (Tin wants a strict split): the fix is a scope-only move (M4-
    M9 verbatim into `plan-assignment-admin`'s own TASK.md at ITS freeze) — no schema change here, and
    `plan-assignment-admin`'s own remaining scope shrinks to "confirm/extend what already shipped" —
    flagged for milestone-level housekeeping, not silently decided.
  - Enum-vs-table departs from MILESTONE.md's own stated Recommendation (which leaned enum +
    override-columns) — see Framings weighed for the full reasoning; medium confidence this is what
    Tin would pick if asked directly, since the milestone doc's own author leaned the other way.
  - `plan-budget-enforcement`/`plan-rate-enforcement` will read `plans.budget_usd_monthly_default`/
    `rpm_limit_default`/`tpm_limit_default` via the live `plan_id` FK join (this draft's assumption)
    rather than wanting those two dimensions copied down as override columns the same way `seat_cap`
    is — if the sibling tasks would have preferred a copied-down column instead, that is an additive
    schema change for them to request, not a redesign of what ships here.
  - A tier-to-tier DOWNGRADE (e.g. `team` -> `starter`) is just an ordinary PUT with a different
    `plan_id` — no special-cased "downgrade" logic exists at this layer. This endpoint does NOT check
    the tenant's CURRENT user headcount against the new plan's seat_cap before permitting the change
    (that consultation, and the milestone's own "don't retroactively strand existing users" policy,
    is `plan-seat-cap`'s enforcement concern, not this catalog/assignment task's).
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── M1: seed migration creates the tier catalog ──────────────────────────────

Scenario: Migration seeds exactly 3 named plan tiers   # M1
  Given a fresh database with this task's migration applied
  When the plans table is queried
  Then exactly 3 rows exist with name in ('starter', 'team', 'enterprise')
  And each row carries its own seat_cap / budget_usd_monthly_default / rpm_limit_default /
    tpm_limit_default (independently nullable) and a non-null display_name

# ── M2: unplanned is the universal starting state ────────────────────────────

Scenario: A pre-existing tenant is unaffected by the migration   # M2
  Given a tenant row that existed before this migration
  When the migration is applied
  Then that tenant's plan_id is NULL and seat_cap is NULL
  And every proxy/budget/rate/provisioning behavior for that tenant is unchanged

Scenario: A newly-signed-up tenant starts unplanned, with no auto-assignment   # M2
  Given a brand-new signup creating a fresh tenant
  When the tenant row is created
  Then plan_id is NULL and seat_cap is NULL — no plan is auto-assigned
  And this is byte-identical to a pre-existing, never-assigned tenant

# ── M3 / R4 / R8: platform tenant permanently exempt ─────────────────────────

Scenario: Assigning a plan to the platform tenant is rejected   # M3, R4
  Given a SUPERADMIN identity and the platform tenant's own tenant_id P
  When the SUPERADMIN calls PUT /admin/platform/tenants/{P}/plan { plan_id: <a real plan's id> }
  Then the response is 403 ERR_PLAN_TENANT_INELIGIBLE
  And the platform tenant's plan_id remains NULL, and no audit event was fired

Scenario: The DB itself refuses a plan_id on a platform-kind row, bypassing application code   # R8
  Given a direct SQL UPDATE attempting to set a non-null plan_id on the platform tenant's row
  When that UPDATE is executed against the database directly
  Then it is rejected by the ck_tenants_platform_no_plan CHECK constraint
  And the platform tenant's row is unchanged

# ── M4: catalog list ──────────────────────────────────────────────────────────

Scenario: A superadmin lists the full plan catalog   # M4
  Given a SUPERADMIN identity and the 3 seeded plan rows
  When the SUPERADMIN calls GET /admin/platform/plans
  Then the response is 200 with all 3 plans, each showing its own ceilings
  And a targetless "platform.plan.list" audit event was fired (target_tenant_id=None)

# ── M5: view a tenant's plan ──────────────────────────────────────────────────

Scenario: Viewing an unplanned tenant's plan shows null   # M5
  Given a SUPERADMIN identity and a customer tenant T with no plan assigned
  When the SUPERADMIN calls GET /admin/platform/tenants/{T}/plan
  Then the response is 200 with plan=null and seat_cap=null
  And a "platform.plan.view" audit event was fired for target_tenant_id=T

Scenario: Viewing an assigned tenant's plan shows the full plan and resolved seat_cap   # M5
  Given a SUPERADMIN identity and a customer tenant T assigned the "team" plan
  When the SUPERADMIN calls GET /admin/platform/tenants/{T}/plan
  Then the response is 200 with plan.name="team" (and its ceilings) and T's own resolved seat_cap

# ── M6: assign / change a plan ────────────────────────────────────────────────

Scenario: A superadmin assigns a plan to an unplanned customer tenant   # M6
  Given a SUPERADMIN identity and a customer tenant T with no plan assigned
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T}/plan { plan_id: <"team" plan's id> }
  Then the response is 200 with plan.name="team"
  And T's plan_id durably equals the "team" plan's id
  And a "platform.plan.assign" audit event was fired attributing the REAL superadmin as actor,
    with old_plan_id=null and new_plan_id=<"team" plan's id> in its metadata

Scenario: A superadmin changes an already-assigned tenant to a different plan   # M6
  Given a SUPERADMIN identity and a customer tenant T currently assigned the "starter" plan
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T}/plan { plan_id: <"enterprise" plan's id> }
  Then the response is 200 with plan.name="enterprise"
  And the audit metadata's old_plan_id is the "starter" plan's id and new_plan_id is "enterprise"'s

# ── M7 / R7: unassign clears both fields atomically ──────────────────────────

Scenario: Setting plan_id to null unassigns a tenant, clearing seat_cap too   # M7
  Given a SUPERADMIN identity and a customer tenant T assigned the "team" plan with seat_cap=25
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T}/plan { plan_id: null }
  Then the response is 200 with plan=null and seat_cap=null
  And T is now indistinguishable from a tenant that was never assigned a plan

Scenario: Supplying a seat_cap alongside plan_id:null is rejected   # R7
  Given a SUPERADMIN identity and a customer tenant T assigned a plan
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T}/plan { plan_id: null, seat_cap: 10 }
  Then the response is 422 ERR_PAYLOAD_INVALID
  And T's plan_id and seat_cap are COMPLETELY unchanged from before the request

# ── M8: omitted seat_cap copies down from the plan's own default ─────────────

Scenario: Assigning a plan with seat_cap omitted copies the plan's own default   # M8
  Given a SUPERADMIN identity, a customer tenant T with no plan, and the "starter" plan has seat_cap=3
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T}/plan { plan_id: <"starter" plan's id> }
    (seat_cap key omitted entirely)
  Then the response is 200 with seat_cap=3
  And T's own seat_cap column durably equals 3 (a one-time copy, not a live join)

Scenario: Changing tiers with seat_cap omitted re-copies from the NEW plan, not the stale old value   # M8
  Given a SUPERADMIN identity and a customer tenant T assigned "starter" (its own seat_cap override
    happens to be 3) and the "team" plan has seat_cap=null (unlimited)
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T}/plan { plan_id: <"team" plan's id> }
    (seat_cap key omitted)
  Then the response is 200 with seat_cap=null
  And T's seat_cap is NOT left at the stale value 3 from the prior "starter" assignment

# ── M9 / R6: explicit seat_cap override ───────────────────────────────────────

Scenario: A superadmin negotiates a custom seat_cap that overrides the plan's own default   # M9
  Given a SUPERADMIN identity, a customer tenant T with no plan, and the "enterprise" plan has
    seat_cap=null (unlimited)
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T}/plan
    { plan_id: <"enterprise" plan's id>, seat_cap: 47 }
  Then the response is 200 with seat_cap=47 (NOT null, despite the plan's own default being unlimited)
  And T's own seat_cap column durably equals 47

Scenario: A zero or negative explicit seat_cap is rejected   # R6
  Given a SUPERADMIN identity and a customer tenant T with no plan
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T}/plan
    { plan_id: <a real plan's id>, seat_cap: 0 }, and separately with seat_cap: -5
  Then each response is 422 ERR_PAYLOAD_INVALID
  And T's plan_id and seat_cap remain COMPLETELY unchanged from before each request

# ── R1 / R2: auth gates apply identically to all 3 endpoints ─────────────────

Scenario: Missing or invalid bearer token is rejected on every plan endpoint   # R1
  Given no Authorization header (or an invalid/expired one)
  When a client calls GET /admin/platform/plans, GET .../plan, or PUT .../plan
  Then each response is 401 ERR_AUTH_TOKEN_MISSING or ERR_AUTH_TOKEN_INVALID
  And no plans row or tenants row is read or written

Scenario: A non-superadmin caller is rejected on every plan endpoint, regardless of their own role   # R2
  Given a logged-in OWNER (holding every non-superadmin permission) of some tenant
  When that OWNER calls GET /admin/platform/plans, GET .../plan, or PUT .../plan for ANY tenant_id
    including their own
  Then each response is 403 ERR_AUTH_FORBIDDEN
  And no plans row or tenants row is written

# ── R3: unknown tenant_id ──────────────────────────────────────────────────────

Scenario: GET or PUT against an unknown tenant_id is rejected   # R3
  Given a SUPERADMIN identity and a tenant_id with no matching row
  When the SUPERADMIN calls GET /admin/platform/tenants/{tenant_id}/plan, and separately PUT
    .../plan { plan_id: <a real plan's id> }
  Then each response is 404 ERR_TENANT_NOT_FOUND
  And no row is written

# ── R5: unknown plan_id ────────────────────────────────────────────────────────

Scenario: Assigning an unknown plan_id is rejected   # R5
  Given a SUPERADMIN identity, a customer tenant T, and a plan_id that matches no plans row
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T}/plan { plan_id: <the unknown id> }
  Then the response is 404 ERR_PLAN_NOT_FOUND
  And T's plan_id and seat_cap remain COMPLETELY unchanged from before the request
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/platform/plans                                    (AUTH: SUPERADMIN only, M4)
  200 -> PlansListResponse { plans: PlanResponse[] }
    PlanResponse { id: uuid, name: string, display_name: string, seat_cap: int | null,
                   budget_usd_monthly_default: string | null,   # Decimal-as-string — mirrors
                                                                 #   BudgetGetResponse's own
                                                                 #   str(Decimal(str(...))) convention
                   rpm_limit_default: int | null, tpm_limit_default: int | null }
  401 -> { code: "ERR_AUTH_TOKEN_MISSING" | "ERR_AUTH_TOKEN_INVALID" }   # R1
  403 -> { code: "ERR_AUTH_FORBIDDEN" }                                  # R2

GET /admin/platform/tenants/{tenant_id}/plan                  (AUTH: SUPERADMIN only, M5)
  200 -> TenantPlanResponse { tenant_id: uuid, plan: PlanResponse | null, seat_cap: int | null }
    # plan=null + seat_cap=null together <=> unplanned (includes the platform tenant itself, M3 —
    # no special-cased reject on a GET, only on the PUT below: a read that truthfully reports "no
    # plan" needs no guard)
  401 -> { code: "ERR_AUTH_TOKEN_MISSING" | "ERR_AUTH_TOKEN_INVALID" }   # R1
  403 -> { code: "ERR_AUTH_FORBIDDEN" }                                  # R2
  404 -> { code: "ERR_TENANT_NOT_FOUND" }                                # R3

PUT /admin/platform/tenants/{tenant_id}/plan                  (AUTH: SUPERADMIN only, M6-M9)
  body: TenantPlanPutRequest {
    plan_id: uuid | null,
    seat_cap: int | null    # OPTIONAL key — 3-state semantic mirrors the key-governance PATCH
                             #   precedent exactly: omitted = inherit the plan's own seat_cap at
                             #   write time (M8) · null = explicitly unlimited for this tenant (M9)
                             #   · positive int = explicit negotiated cap for this tenant (M9).
                             #   MUST be omitted or null when plan_id is null (R7) — never a
                             #   positive int alongside an unassign.
  }
  200 -> TenantPlanResponse   (same shape as GET, reflecting the newly-written state)
  401 -> { code: "ERR_AUTH_TOKEN_MISSING" | "ERR_AUTH_TOKEN_INVALID" }   # R1
  403 -> { code: "ERR_AUTH_FORBIDDEN" | "ERR_PLAN_TENANT_INELIGIBLE" }   # R2 · R4
  404 -> { code: "ERR_TENANT_NOT_FOUND" | "ERR_PLAN_NOT_FOUND" }         # R3 · R5
  422 -> { code: "ERR_PAYLOAD_INVALID" }        # R6 (seat_cap <= 0) · R7 (seat_cap set + plan_id null)

Gate order (all 3 routes): require_superadmin (Depends) FIRST — mirrors platform_users_router.py's
  _require_target_tenant / platform_tenant_config_router.py's inline dual-gate exactly. The 2
  tenant-scoped routes additionally run authorize_tenant_scope(identity, tenant_id) THEN
  get_tenant_by_id (404) BEFORE any plan-specific check; PUT additionally runs the platform-kind
  check (M3/R4) THEN the plan_id-resolves check (R5) THEN the seat_cap cross-field check (R6/R7),
  all BEFORE any write — mirrors platform_tenant_config_router.py's own "PUT checks existence
  before writing, never a silent no-op" precedent.

Schema (additive migration, revises 5b34ca5e1c4b — confirmed current head via `alembic heads`
  this session):

  NEW TABLE plans:
    id                          UUID PK (default uuid7())
    name                        TEXT NOT NULL UNIQUE    -- lowercase slug: 'starter'|'team'|
                                                         --   'enterprise'; deliberately NOT a
                                                         --   CHECK-constrained value set — the
                                                         --   whole point of a table over an enum
                                                         --   is that a 4th row needs no migration
    display_name                TEXT NOT NULL           -- e.g. 'Starter' — admin-UI label
    seat_cap                    INTEGER NULL
      CHECK (seat_cap IS NULL OR seat_cap > 0)                            -- ck_plans_seat_cap_positive
    budget_usd_monthly_default  NUMERIC(12,2) NULL
      CHECK (budget_usd_monthly_default IS NULL OR budget_usd_monthly_default > 0)
                                                                   -- ck_plans_budget_default_positive
    rpm_limit_default           INTEGER NULL
      CHECK (rpm_limit_default IS NULL OR rpm_limit_default > 0)          -- ck_plans_rpm_default_positive
    tpm_limit_default           INTEGER NULL
      CHECK (tpm_limit_default IS NULL OR tpm_limit_default > 0)          -- ck_plans_tpm_default_positive
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()   -- server_default only, no
                                                                     --   onupdate; mirrors
                                                                     --   TenantRow's own convention.
                                                                     --   Inert in v1 (no write path
                                                                     --   updates a `plans` row after
                                                                     --   seeding) — kept for the
                                                                     --   future tier-CRUD endpoint.

    Seed data (op.bulk_insert in the SAME migration, mirrors the platform-tenant-seed migration's
    own seed-via-migration precedent) — ⚠ INVENTED placeholder numbers, see §1's top ⚠ assumption:
      ('starter',    'Starter',    seat_cap=3,    budget_default=50.00,  rpm_default=60,  tpm_default=40000)
      ('team',       'Team',       seat_cap=NULL, budget_default=500.00, rpm_default=600, tpm_default=400000)
      ('enterprise', 'Enterprise', seat_cap=NULL, budget_default=NULL,   rpm_default=NULL, tpm_default=NULL)
    (starter's seat_cap=3 and team's seat_cap=NULL trace EXACTLY to /pricing's own "up to 3 users" /
    "Unlimited users" copy; the $ and rpm/tpm numbers are this draft's own invention, not sourced
    from any confirmed commercial decision — enterprise is fully NULL/negotiated, matching its own
    "Contact us" / "custom" positioning.)

  TenantRow additive columns (tenants/infrastructure/orm.py — mirrors budget_usd_monthly's own
    nullable/no-backfill/no-onupdate convention exactly):
    plan_id    UUID NULL REFERENCES plans(id) ON DELETE RESTRICT
    seat_cap   INTEGER NULL
      CHECK (seat_cap IS NULL OR seat_cap > 0)                            -- ck_tenants_seat_cap_positive
    NEW __table_args__ addition (defense-in-depth, M3/R8):
      CheckConstraint("plan_id IS NULL OR kind != 'platform'",
                       name="ck_tenants_platform_no_plan")

  Access pattern:
    GET (catalog list): SELECT * FROM plans ORDER BY created_at ASC
    GET (tenant view):  get_tenant_by_id(tenant_id) (existing helper) ; IF plan_id IS NOT NULL:
      SELECT * FROM plans WHERE id=:plan_id (single PK lookup — the ONLY place this task ever
      queries `plans` from a tenant-scoped path; never on any proxy hot path)
    PUT (assign/change/unassign) — one transaction:
      1. get_tenant_by_id(tenant_id) -> 404 if missing (R3)
      2. row.kind == 'platform' -> 403 ERR_PLAN_TENANT_INELIGIBLE (R4)
      3. IF body.plan_id is not None: SELECT * FROM plans WHERE id=:plan_id -> 404 if missing (R5)
      4. cross-field seat_cap validation (R6/R7)
      5. UPDATE tenants SET plan_id=:plan_id, seat_cap=:resolved_seat_cap WHERE id=:tenant_id
         (resolved_seat_cap = NULL if plan_id is NULL (M7) ; body.seat_cap if the key was explicitly
         present in the request (M9) ; else the freshly-read plan row's OWN seat_cap (M8))
      6. commit -> emit_platform_audit(..., action="platform.plan.assign",
         metadata={old_plan_id, new_plan_id, old_seat_cap, new_seat_cap})

NEW domain symbols (gateway.tenants.domain.entities, sibling to Role/Identity — or a new
  gateway.plans.domain.entities module; container placement is Build's discretion, not
  contract-binding, mirrors impersonation-session-lifecycle's own note):
  @dataclass(frozen=True, slots=True)
  class Plan:
      id: uuid.UUID
      name: str
      display_name: str
      seat_cap: int | None
      budget_usd_monthly_default: Decimal | None
      rpm_limit_default: int | None
      tpm_limit_default: int | None
      created_at: datetime
      updated_at: datetime

NEW ORM row (gateway.tenants.infrastructure.orm — sibling to TenantRow/ApiKeyRow):
  class PlanRow(Base):
      __tablename__ = "plans"
      # columns + CHECK constraints exactly per Schema above, mirroring ApiKeyRow.rpm_limit's
      # `> 0` CHECK convention.

NEW error_catalog.py entries (sibling to TENANT_NOT_FOUND / IMPERSONATION_TARGET_INVALID — mirrors
  the ErrorSpec(status, code, title_template) shape exactly):
  PLAN_NOT_FOUND = ErrorSpec(404, "ERR_PLAN_NOT_FOUND", "Plan not found")
  PLAN_TENANT_INELIGIBLE = ErrorSpec(403, "ERR_PLAN_TENANT_INELIGIBLE",
      "This tenant is not eligible for plan assignment")
  (REUSED, not new: AUTH_TOKEN_MISSING · AUTH_TOKEN_INVALID · AUTH_FORBIDDEN · TENANT_NOT_FOUND ·
   PAYLOAD_INVALID — status-code precedent for PLAN_NOT_FOUND deliberately follows TENANT_NOT_FOUND/
   USER_NOT_FOUND's "admin action references an unknown id -> 404" idiom, NOT the existing
   PRESET_NOT_FOUND's 400 (that code fires on a proxy-hot-path RUNTIME resolution failure — a
   different situation entirely; confirmed by reading its own error_catalog.py comment this
   session).)

NEW audit actions (fire-and-forget via emit_platform_audit, mirroring platform_tenant_config_
  router.py's "platform.budget.update" resource-first naming — M4/M5/M6):
  "platform.plan.list" (target_tenant_id=None, the targetless/system-level case) ·
  "platform.plan.view" (target_tenant_id=path tenant_id) ·
  "platform.plan.assign" (target_tenant_id=path tenant_id, metadata: old/new plan_id + seat_cap)

Migration revision: <next Alembic hash — generated at build time, revises 5b34ca5e1c4b (confirmed
  current head via `alembic heads` this session)>
Downgrade: DROP the ck_tenants_platform_no_plan CHECK, DROP tenants.seat_cap, DROP tenants.plan_id
  (and its FK), DROP TABLE plans. Safe: additive-only, no pre-existing data depends on either new
  tenants column or the new table at migration time (mirrors tenant_model_presets's own
  additive-migration downgrade note: "no existing data depends on this table at migration time").

Non-goals (explicit — this task is CATALOG + ADMIN SURFACE ONLY):
  - $ budget ENFORCEMENT — no code path compares a tenant's plan-derived budget default against
    `budget_usd_monthly` or gates `PUT /admin/budget`'s self-service writes; `plans.budget_usd_
    monthly_default` is readable but consulted by nobody yet. (`plan-budget-enforcement`)
  - Rate ENFORCEMENT — no code path seeds or ceilings `ApiKeyRow.rpm_limit`/`tpm_limit` from a
    tenant's plan; `plans.rpm_limit_default`/`tpm_limit_default` are readable but consulted by
    nobody yet. (`plan-rate-enforcement`)
  - Seat-cap ENFORCEMENT — no user-provisioning entry point (today's OIDC auto-provision, nor the
    sibling milestone's future invite-accept) consults `TenantRow.seat_cap` yet; a tenant already
    over a newly-assigned lower seat_cap is NOT retroactively touched by this task (no enforcement
    exists yet to retroactively trigger). (`plan-seat-cap`)
  - Superadmin tier-DEFINITION CRUD — creating/editing/deleting a `plans` ROW itself (as opposed to
    assigning an EXISTING one to a tenant) has no HTTP surface in v1; the table's shape supports it
    without a redesign, but building it is explicitly out of scope here.
  - Any self-service tenant-owner plan upgrade/downgrade — matches MILESTONE.md's own Scope
    ("v1 plan assignment is a superadmin-only manual lever").
  - Tier-based FEATURE gating (e.g. gating BYOK/SSO behind a tier) — matches MILESTONE.md's own
    Scope exclusion verbatim.
```

Glossary deltas (proposed here; pending this milestone's fold, mirrors impersonation-session-
lifecycle's own "proposed... pending fold" convention):
  - `plan` / `plan tier`: a named, superadmin-assignable governance profile (a `plans` table row)
    for a customer tenant's usage ceilings — seat headcount, $ budget default, rate-limit defaults.
    A tenant's `plan_id` (nullable) associates it with at most one; `NULL` = unplanned, the universal
    starting state and byte-identical to today's behavior. The reserved platform tenant can never
    hold one. Distinct from a plan's per-tenant `seat_cap` OVERRIDE, which may diverge from the
    assigned plan's own `seat_cap` once a superadmin individually tunes it (Enterprise/custom
    negotiation).
  - `seat cap`: a plan's (or a tenant's individually-overridden) maximum User headcount, counting
    every role — `NULL` = unlimited. Enforced at every user-provisioning entry point by the sibling
    `plan-seat-cap` task, not by this one.

Status: FROZEN @ v1 — approved by Tin Dang 2026-07-04 ("confirm"). Both flags below accepted
  as-drafted: the `plans`-table departure from MILESTONE.md's original enum recommendation stands
  (Enterprise/custom negotiated ceilings + avoiding a redeploy-per-pricing-tweak), and the
  plan-assignment-admin merge stands (MILESTONE.md already updated 2026-07-04 to retract that task
  and repoint plan-admin-ui's dependency + the Exit-criteria mapping at plan-catalog).

**Least-sure flag surfaced at freeze:** (bundle lowest-confidence, ranked)
  ⚠ [spec] The plan/tier catalog shape (a `plans` TABLE) departs from MILESTONE.md's own stated
    Recommendation (enum column + override columns) — see §1 Framings weighed for the full reasoning
    (Enterprise/custom negotiated ceilings + avoiding a catalog too rigid for routine pricing
    changes). Cost if Tin prefers the enum after all: a genuine redesign (drop the `plans` table, add
    a CHECK-constrained `plan_tier` enum column + a Python constants dict) — NOT a cheap follow-up,
    the single biggest risk in this draft.
  ⚠ [spec/scope] This draft builds the superadmin view/assign/change endpoint (M4-M9) inside
    `plan-catalog` itself, even though MILESTONE.md's own task breakdown names a separate sibling
    `plan-assignment-admin` task for exactly that surface. Needed to make M3/M7 scenario-testable;
    creates a real scope-overlap question for that sibling task's own eventual TASK.md — flagged for
    milestone-level resolution, not silently decided here.
  ⚠ [contract] Every seeded tier NAME/COUNT and ceiling NUMBER is this draft's own invention, loosely
    traced to the `/pricing` page's own admittedly-placeholder copy (seat_cap values trace exactly;
    $/rpm/tpm numbers do not trace to any confirmed source). Cheap to fix (a data-only change), but
    flagged because it's the most likely single line item Tin corrects at freeze.

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

Scope (may touch):
  `apps/gateway/src/gateway/tenants/domain/entities.py` (add Plan domain entity — SHARED file, other
    parallel builds also touch this; expect a merge reconciliation, not a conflict-free apply)
  `apps/gateway/src/gateway/tenants/infrastructure/orm.py` (add PlanRow + TenantRow.plan_id/seat_cap
    columns — SHARED file, same merge caveat)
  `apps/gateway/src/gateway/tenants/api/platform_plans_router.py` (NEW — list/view/assign/change routes)
  `apps/gateway/src/gateway/core/error_catalog.py` (add 2 new ErrorSpec entries — SHARED file, same
    merge caveat)
  `apps/gateway/src/gateway/main.py` (register platform_plans_router — SHARED file, same merge caveat)
  `apps/gateway/migrations/versions/` (one new migration, revises current head — SHARED directory;
    down_revision chain across the 3 parallel builds is reconciled by the orchestrator after all 3
    land, not by this build)
  `apps/gateway/tests/plan_catalog/` (this task's own test directory)
Strategy (ordered batches): 1. domain (Plan entity) 2. infrastructure (PlanRow + TenantRow additive
  columns + migration incl. seed data + ck_tenants_platform_no_plan CHECK) 3. API (GET catalog list,
  GET/PUT tenant plan — gate order require_superadmin -> authorize_tenant_scope -> get_tenant_by_id
  -> platform-kind check -> plan_id-resolves check -> seat_cap cross-field check -> write ->
  emit_platform_audit) 4. main.py registration + error_catalog.py entries 5. tests per scenario.

Persona (optional): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; absent = generic>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 20/20 `tests/plan_catalog/` + 6/6 `tests/migrations/` (incl. the real ORM<->migration parity test), independently re-run by the verify agent
- [x] coverage did not decrease — every M1-M9/R1-R8 traces to a non-vacuous test (see refute-read); per-directory `--cov` numbers are a confirmed pre-existing tooling artifact (reproduces identically on an untouched precedent file) — full-suite coverage is the trustworthy signal, logged as a TDD delta
- [x] no test or contract was altered during build — `git diff` shows only 2 pre-existing SHARED manifest files touched (additive "SANCTIONED EDIT" entries, 9+ prior precedent) + 1 unrelated sibling-task test file independently attributed away from this task
- [x] the green was EARNED, not gamed — independent add-verify subagent: EARNED (one disclosed, non-blocking scenario-coverage gap — see below)
- [x] concurrency / timing of the risky operation is safe — independently reasoned through the SQLAlchemy unit-of-work single-UPDATE-at-flush mechanics, confirmed no torn-write is possible
- [x] no exposed secrets, injection openings, or unexpected dependencies — all queries parameterized/ORM
- [x] layering & dependencies follow CONVENTIONS.md — router-direct-ORM confirmed as a consistent continuation of the exact precedent (`platform_tenant_config_router.py`) it claims to mirror, not a new departure
- [x] a person reviewed and approved the change — Tin Dang, via the orchestrator's report following this gate record

### Build expectations — what "correct" looks like
- [x] All 3 routes are unreachable without `require_superadmin` passing FIRST — confirmed both by code (`Depends` on every handler) and empirically (adversarial probe: no-auth + invalid-tenant + garbage-body simultaneously → 401 in every combination, never a validation error leaking first)
- [x] Platform tenant can never hold a `plan_id`, even bypassing the app — confirmed via a real raw-SQL bypass test asserting Postgres `IntegrityError` sqlstate `23514` (check_violation) on `ck_tenants_platform_no_plan`
- [x] PUT's seat_cap 3-state semantic (omit/null/positive-int) is presence-based, not value-based — confirmed via `body.model_fields_set`, cannot conflate "given as null" with "omitted"
- [x] R7 (non-null seat_cap rejected when plan_id is null) rejects BEFORE any mutation — confirmed at `platform_plans_router.py:252-258`, row byte-unchanged after a rejected request
- [x] Audit fires on all 3 routes' success paths with the exact contracted metadata shape — confirmed via tests reading real `audit_events` rows (fire-and-forget, separate session)

### Deep checks
- [x] WIRING (code) — every new symbol referenced; confirmed via live-verify below
- [x] DEAD-CODE (code) — none found
- [ ] SEMANTIC (prose / non-code) — n/a, no prose-only deliverable in this task

### Live-verify evidence
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — independently confirmed by the verify agent reading each directly (`TenantRow`, `ApiKeyRow.rpm_limit`/`tpm_limit`, `Settings.bandwidth_*`, `authorize_tenant_scope`/`require_superadmin`, `emit_platform_audit`, `get_tenant_by_id`, all 4 error_catalog entries)
- [x] no anchor moved/renamed since Ground SHA — none named

### Refute-read verdict — the earned-green check
Verdict: EARNED
By: independent add-verify subagent (appsec-engineer + backend-architect personas) · adversarially checked: every M/R item traced to a test with real HTTP+DB assertions (not vacuous — every test targets a `{"code": ...}` shape that a deleted/unregistered router would fail); empirically probed auth-vs-validation ordering; found and disclosed ONE non-blocking scenario-coverage gap — M9's "seat_cap null overrides a non-null plan default" direction has no test (only the inverse, non-null-overrides-null-default, is tested). This traces to §2 SCENARIOS never drafting that specific combination, not to Build skipping something scenario'd; the verify agent manually confirmed the code is correct on this path anyway (`model_fields_set`-based presence check). Logged as a SPEC delta below, not blocking.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: independent add-verify subagent (agent a4cb2362defdade6d)
1. Security: CLEAR — require_superadmin gate empirically confirmed un-bypassable; DB-level defense-in-depth (`ck_tenants_platform_no_plan`) confirmed via a real bypass test, not just read; no enumeration oracle (structurally guaranteed — require_superadmin takes no tenant_id parameter)
2. Concurrency: CLEAR — single-UPDATE-at-flush mechanics confirmed no torn write possible; 💭 non-blocking note: audit metadata's captured "old" values could show stale data under a two-superadmin race, but this doesn't affect tenant-row integrity and is an identical pre-existing pattern already used by `platform_users_router.py`
3. Architecture: CLEAR — router-direct-ORM confirmed consistent with the exact precedent it claims to mirror, not a new departure
Verdict: PASS
Residue: none blocking — 3 non-blocking deltas recorded in §7 OBSERVE (M9 scenario-coverage gap; repo-wide per-directory coverage tooling artifact; ADD phase-marker bookkeeping)
Binding: advisory — sensitivity not declared on this task's header

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (via orchestrator report) · date: 2026-07-04

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang 2026-07-04 ("confirm"). Both flags below accepted)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang (via orchestrator report))

### Spec delta
- [SPEC · open] Add a test for `PUT .../plan` with `plan_id: <a plan whose own seat_cap default
  is non-null>` + explicit `seat_cap: null` → expect `seat_cap: null` in the response, not the
  plan's default (M9's "including null for explicitly-unlimited" sub-clause is currently
  untested; only the inverse direction — non-null override beats a null plan default — has a
  test. Traces to §2 SCENARIOS never drafting this exact combination, not a Build omission; the
  independent verify pass manually confirmed the code is correct on this path anyway via its
  `model_fields_set`-based presence check).

### Competency deltas
- [TDD · folded] Per-directory `pytest --cov` readings are unreliable for this repo's async route [folded foundation-version 48]
  handlers (evidence: `platform_plans_router.py` showed 58% with the entire PUT handler body
  "missing" despite the covering tests passing with real DB-state assertions; the identical
  artifact was confirmed to reproduce on `platform_users_router.py`, a file this build never
  touched). Rely on full-suite coverage numbers, not per-directory ones, when judging "coverage
  did not decrease" for async code in this repo.
- [ADD · folded] A build agent dispatched to independently verify its own long-running background [folded foundation-version 48]
  regression suite twice ended its turn to "wait" rather than actively blocking until the suite
  finished, requiring the orchestrator to resume it via SendMessage and, ultimately, take over
  verification directly rather than continue a resume-and-wait cycle. Future dispatches that
  depend on a long-running background check should be told explicitly to block/poll internally
  until that check truly completes before returning, not to end their turn mid-wait.
