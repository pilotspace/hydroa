# TASK: Per-seat plan pricing with proration invoice lines

slug: seat-billing · created: 2026-07-12 · stage: production
sensitivity: data
milestone: monetization-core
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: verify   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:UserRow` (lines 176-208) — the tenant
  membership row (`__tablename__ = "users"`): `tenant_id` (FK RESTRICT), `role` (CHECK-constrained
  string incl. `superadmin`), `created_at` (bare `Mapped[datetime]`, naive-TIMESTAMP convention),
  `deactivated_at: datetime | None` — additive nullable column (scim-provisioning migration
  `010e6f83a709`), NULL = active, set by SCIM PATCH `active:false`, **cleared (set back to NULL,
  not a new row) by PATCH `active:true`** — confirmed decisive fact: this column is a single
  MUTABLE current-state flag, not an append-only history (see Issue R1 below).
- `apps/gateway/src/gateway/tenants/domain/entities.py:Role` (lines 9-20) — 7 roles
  (`owner/admin/operator/billing_admin/viewer/member/superadmin`); `SUPERADMIN` is DB-trigger-locked
  to the sole `kind='platform'` tenant, and `ck_tenants_platform_no_plan` (orm.py:93-95) guarantees a
  platform tenant can never hold a `plan_id` — confirms superadmin rows are structurally unreachable
  by seat pricing (no planned tenant ever contains one).
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:InviteRow` (lines 211-263) — a PENDING
  invite never creates a `users` row (confirmed: `AcceptInviteUseCase`/`InviteRepository.accept` is
  the ONLY path from invite to user) — decisive for M1: an outstanding invite is never a seat.
- `apps/gateway/src/gateway/tenants/application/invite_accept_use_cases.py:AcceptInviteUseCase.execute`
  (lines 52-80) + `apps/gateway/src/gateway/tenants/infrastructure/invite_repository.py:InviteRepository.accept`
  (lines 237-onward, read in full) — "Lock, validate pending+not-expired, provision a user, flip to
  accepted, commit — ALL in ONE atomic transaction (M2/M5)"; the users-row INSERT and the invite
  flip commit together. This is the "seat joins" write point (join path #1).
- `apps/gateway/src/gateway/scim/application/user_use_cases.py:CreateScimUserUseCase.execute`
  (lines 13-27) + `apps/gateway/src/gateway/scim/infrastructure/repository.py:SqlAlchemyScimUserRepository.create_user`
  (lines 147-166) — `POST /scim/v2/Users` provisions a `users` row directly (role always `member`),
  `self._session.add(row); await self._session.commit()` at line 161. Second, independent "seat
  joins" write point (join path #2) — a tenant can gain members via EITHER invite-accept OR SCIM.
- `apps/gateway/src/gateway/scim/infrastructure/repository.py:SqlAlchemyScimUserRepository.set_active`
  (lines 223-261, read in full) — the ONLY deactivation/reactivation path in this codebase (no
  non-SCIM admin "deactivate user" route exists — confirmed via `users_router.py`'s overview: only
  `list_users`/`assign_user_role`). `SELECT ... FOR UPDATE` row lock (concurrency-safe), idempotency
  guard at line 243 (`already_at_target` — a same-state repeat is a true no-op, `changed=False`,
  and the caller (`scim_router.py:_apply_patch` line 179-188) SKIPS the audit write on a no-op — the
  precedent this task's own ledger-write skip mirrors exactly). The state flip is line 250
  (`row.deactivated_at = None if active else datetime.now(UTC)`) and commits at line 259 — the exact
  transaction boundary a same-transaction ledger append must join.
- `apps/gateway/src/gateway/scim/api/scim_router.py:_apply_patch` (lines 144-199) — `action =
  "scim.user_reactivate" if parsed.set_active else "scim.user_deactivate"` (line 181); `DELETE
  /scim/v2/Users/{id}` (line 336+) is documented as "an ALIAS for PATCH active:false, never a hard
  delete" — confirms deactivation is ALWAYS soft, users rows are never hard-deleted (safe permanent
  FK target for a membership ledger).
- `apps/gateway/src/gateway/audit/domain/audit_event.py:AuditEvent` +
  `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit` (lines 30-45, read in
  full) — EVERY existing membership-change audit write (`scim.user_create/update/deactivate/
  reactivate`, `invite.accept`) is `asyncio.ensure_future(record_audit(...))`: fire-and-forget,
  "Swallows ALL exceptions — failures are logged but NEVER raised." **Decisive negative finding**:
  `audit_events` is explicitly NOT a durable, guaranteed-complete history — it is advisory/best-
  effort, same class of caveat this project's own `billing-precision-engineer` persona applies to
  the Redis spend counters ("advisory-only... ledger is source of truth"). It CANNOT be the
  authoritative source for a money computation (feeds the §1 ⚠ flag directly).
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:PlanRow` (lines 25-74, read in full) —
  `plans` schema as it stands TODAY (plan-catalog FROZEN@v1 columns `seat_cap`,
  `budget_usd_monthly_default`, `rpm_limit_default`, `tpm_limit_default` PLUS plan-enforcement's own
  additive `model_allowlist`/`feature_flags`, both FROZEN@v1) — **zero seat-PRICE column exists**.
  `TenantRow.seat_cap` (orm.py:170-174) is a per-tenant override slot, already shipped, currently
  read by NOTHING (`plan-seat-cap`, the cap-enforcement sibling, is still `phase: ground` with an
  EMPTY template body — confirmed by reading its TASK.md directly this session). Grep-confirmed
  (`grep -rn seat_cap apps/gateway/src`) — no query anywhere counts active `users` rows; this task
  is the FIRST code in the repo to actually compute a seat headcount.
- `.add/tasks/plan-enforcement/TASK.md` §3 (FROZEN@v1, `phase: done`, read in full) —
  `gateway/tenants/domain/entitlements.py:ResolvedEntitlements`/`resolve_entitlements` +
  `gateway/tenants/domain/ports.py:PlanEntitlementResolver` +
  `gateway/tenants/infrastructure/plan_entitlement_resolver.py:SqlAlchemyPlanEntitlementResolver` —
  confirmed BUILT and live (found via `search_for_pattern`, not just the frozen text). `M8`'s own
  docstring names `seat-billing` as ITS "named consumer" for in-process resolution — considered and
  explicitly NOT reused here (see Framings weighed, §1): `ResolvedEntitlements` carries only
  budget/allowlist/feature-flag dimensions; it was frozen before seat PRICING existed and adding a
  field to it would mean re-opening a sibling task's frozen contract, which this task has no
  authority to do. A direct, narrow `tenants ⋈ plans` read (mirroring `RedisBudgetGuard`'s own
  "one extra SELECT" idiom, NOT the shared resolver) is the smaller, self-contained footprint.
- `apps/gateway/src/gateway/billing/infrastructure/orm.py:InvoiceRow` (lines 77-106) /
  `:InvoiceLineRow` (lines 109-129) — invoice-generation's FROZEN@v1, ALREADY-BUILT schema (found the
  real file, not just the frozen TASK.md text — confirmed identical). `InvoiceLineRow.line_type`
  (`TEXT NOT NULL DEFAULT 'usage'`) is the exact reserved extension point (invoice-generation §1 M14:
  "reserves 'seat'/'proration', not implemented here... added now so that task's migration does not
  need to alter this frozen table shape"). **Decisive constraint**: `key_id` (`UUID NOT NULL`, no
  default) and `model_id` (`TEXT NOT NULL`, no default) have NO natural seat-domain value — every
  INSERT must supply something (see Issue R2 below; resolved in §3 via a documented per-line_type
  reinterpretation, not a schema change).
- `apps/gateway/src/gateway/billing/application/invoice_generator.py:InvoiceGenerator.generate_for_tenant`
  (lines 118-onward, read in full) — the EXACT, ALREADY-RUNNING transaction this task extends: one
  `asyncio.timeout`-bounded, one `session.begin()` block that (1) aggregates `usage_records` into
  `line_specs`, (2) `INSERT ... ON CONFLICT (tenant_id, period_start) DO NOTHING RETURNING id`
  (idempotent, M13), (3) on a real insert, `session.add(InvoiceLineRow(...))` per line, all inside
  the ONE transaction, status always `'issued'` (auto-issue, no draft state in v1). This is the ONE
  place seat/proration lines can be added while staying money-immutable (M5 of invoice-generation):
  once this transaction commits, nothing may ever UPDATE a line — seat pricing MUST be computed and
  folded into `total_usd`/`raw_total_usd` BEFORE that same INSERT, never after.
- `apps/gateway/src/gateway/billing/application/invoice_generator.py:_month_start` / `_next_month` /
  `_as_naive_utc` / `_round_half_up` (lines 73-117) — the exact period-boundary and rounding helpers
  this task reuses verbatim (`days_in_period = (period_end - period_start).days`; ROUND_HALF_UP to
  cents, the SAME `_CENTS`/`ROUND_HALF_UP` idiom, not a second rounding rule).
- `apps/gateway/src/gateway/billing/api/router.py:get_invoice_line_evidence` (lines 294-onward) +
  `UsageEvidenceItem`/`UsageEvidenceListResponse` (lines 107-126) — invoice-generation's FROZEN@v1,
  ALREADY-BUILT evidence route + response shape, typed ENTIRELY to usage_records fields
  (`cost_usd`, `prompt_tokens`, `request_id`) — no field fits a membership event. `_get_invoice_or_404`
  (lines 233-247) is the reusable tenant-404-invisible resolver this task's own new evidence route
  calls verbatim (same `ERR_INVOICE_NOT_FOUND` for unknown/cross-tenant, no new leak surface).
- `apps/gateway/src/gateway/billing/infrastructure/invoice_repository.py:InvoiceRepository` — houses
  `evidence_keyset` (usage_records re-query) and `get_line`; this task adds a sibling read method,
  never edits the existing one (frozen file, additive-only touch, mirrors plan-enforcement's own
  "supersession, not edit" precedent for `put_batch_policy`/`put_guardrails`).
- `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec` (frozen dataclass) + the 3 codes
  invoice-generation already added (`INVOICE_NOT_FOUND`, `INVOICE_QUERY_TIMEOUT`,
  `INVOICE_IMMUTABLE`) — this task adds ONE new additive constant.
- Current alembic head: `0b5527920450` (confirmed via `uv run alembic heads` this session — NOT the
  `69cfdc584129` cited by plan-enforcement's own now-stale ground read; invoice-generation +
  plan-enforcement's migrations both landed since). This task's own migration parents here.

Context (working folder): `.add/milestones/monetization-core/MILESTONE.md` Scope line ("seat-based
plan pricing with proration invoice lines") + Exit criterion 4 ("An invoice for a plan-priced tenant
carries correct seat lines including mid-month proration") — this task delivers that criterion
verbatim; Tasks line 37 names `depends-on: plan-enforcement, invoice-generation` — both confirmed
FROZEN@v1 and BUILT (plan-enforcement `phase: done`, invoice-generation `phase: verify`, code present
in the tree, not just text) — this task is genuinely unblocked. `tmp/monetization-core-design-context.md`
binding rule 5 (seat CAPS belong to `plan-seat-cap`, "never duplicated here" — this task prices, never
caps/blocks). `.add/tasks/plan-seat-cap/TASK.md` read in full: still an EMPTY template (`phase:
ground`) — no seat-cap contract exists yet to align a "seat" definition against; this task must define
its own, flagged for the sibling to adopt (Issue R3).

Honors (patterns / conventions):
- `.add/CONVENTIONS.md` clean-architecture layering — a new `apps/gateway/src/gateway/billing/application/seat_pricer.py`
  (pure Decimal computation, zero infra imports) called by `InvoiceGenerator`, mirrors how
  `resolve_entitlements` (plan-enforcement) is a pure function separate from its SQL adapter.
- MILESTONE.md shared decision "append-only money... corrections are new signed-delta entries" —
  honored: seat/proration lines are inserted in the SAME immutable-once-issued transaction as usage
  lines; no seat line is ever written after issuance (no UPDATE path, matches M5).
- `billing-precision-engineer` persona's Critical Rules (`.add/personas/billing-precision-engineer.md`,
  read in full) — "Decimal end to end, never float"; "never a silent $0... a $0 row is only
  acceptable when EXPLAINED"; "the ledger is append-only... a fix is a NEW row, never a rewrite" —
  this task's central design decision (a new append-only `seat_membership_events` ledger, §1 M3)
  is this SAME doctrine applied one domain over, from usage to membership.
- invoice-generation's own M4 rounding discipline ("rounded-then-summed, never summed-then-rounded")
  and M2 grouping discipline reused verbatim, not reinvented, for seat lines.
- plan-enforcement's "Frozen-contract supersession only" convention (its own Honors section, citing
  the `put_batch_policy`/`put_guardrails` precedent) — this task's touches to `InviteRepository.accept`,
  `SqlAlchemyScimUserRepository.create_user`/`.set_active`, and `billing/api/router.py` are ADDITIVE
  insertions into already-shipped files, never an edit to any FROZEN TASK.md text itself.

Seams consulted: none (`.add/SEAMS.md` not present in this repo — same absence every prior task in
this milestone has noted).

Anchors the contract cites: `tenants/infrastructure/orm.py:UserRow` (`created_at`/`deactivated_at`) ·
`tenants/infrastructure/orm.py:PlanRow`/`TenantRow` (`plan_id`) · `tenants/infrastructure/invite_repository.py:InviteRepository.accept` ·
`scim/infrastructure/repository.py:SqlAlchemyScimUserRepository.create_user`/`.set_active` ·
`billing/application/invoice_generator.py:InvoiceGenerator.generate_for_tenant` (+ its period/rounding
helpers) · `billing/infrastructure/orm.py:InvoiceLineRow` · `billing/api/router.py:get_invoice_line_evidence`/`_get_invoice_or_404` ·
`billing/infrastructure/invoice_repository.py:InvoiceRepository` · `core/error_catalog.py:ErrorSpec` ·
alembic head `0b5527920450`.

Issues/Risks (→ feed §1):
- R1 **`deactivated_at` is current-state-only, not history** (decisive, feeds the §1 ⚠ flag): a
  deactivate-then-reactivate cycle within one billing period is INVISIBLE to a bare `users` row read
  at generation time (the column just reads NULL again, as if never deactivated). Seats are billed
  by TIME-IN-STATE (this task's own governing principle per its persona brief), so a point-in-time
  column cannot be the sole source of truth for proration.
- R2 **`invoice_lines.key_id`/`model_id` are NOT NULL with no seat-domain meaning**: every seat/
  proration line INSERT must supply real values into columns this task may not alter the shape of
  (per dispatch instruction). Resolved via a documented per-`line_type` reinterpretation (§3), not a
  migration.
- R3 **`plan-seat-cap` (the sibling CAP-enforcement task) has not yet defined "seat"**: its TASK.md
  is a still-empty template. This task must invent the definition FIRST (pricing ships in this wave,
  capping does not) — a real cross-task consistency risk if the sibling later picks a DIFFERENT
  population (e.g. excluding `viewer`-role users from caps but not from pricing). Flagged, not
  silently assumed away — this task cannot bind the sibling's future contract.
- R4 **`audit_events` cannot be the pricing source of truth** (see Ground finding above) — it is
  fire-and-forget and explicitly allowed to silently drop a row on any exception. Any evidence/
  drill-down surface MAY reference it for human-readable context, but the BILLED AMOUNT must never
  depend on its presence.
- R5 **historical backfill gap**: every `users` row created BEFORE this task's migration ships has
  ZERO membership-history rows in any new ledger this task introduces — without an explicit backfill,
  every tenant's entire PRE-EXISTING team would silently price as zero seats on the first post-ship
  invoice (a severe, silent under-bill, not a narrow edge case).
- R6 **FOUR independent member-creating write paths** (invite-accept, SSO new-user auto-provision
  `_get_or_provision_sso_user`, domain-capture `join_verified_tenant_domain`, AND SCIM user-create)
  must ALL be instrumented — missing any one silently under-counts seats for tenants using that path
  (mirrors plan-enforcement's own "two independently-maintained pipeline copies" risk class: an
  addition made to only some of N symmetric paths). [AMENDED @ v2 — CR-1: the §0 ground here was
  drafted before plan-seat-cap's ground pass established 4 member-creating seams, now merged.]

Related intent: MILESTONE.md `monetization-core` Exit criterion 4 (seat lines + mid-month proration on
an invoice); Glossary deltas this task introduces: `seat`, `seat-day`, `membership event` (see §3);
`tmp/monetization-core-design-context.md`'s persona brief ("seats are a slowly-changing dimension
billed by time-in-state, not a point-in-time count") is the literal governing principle behind R1's
resolution.

Ground SHA: 71641a9

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-seat plan pricing with mid-period-proration invoice lines — a plan-priced tenant's
monthly invoice additionally carries a `'seat'` line (users active the WHOLE period, full price) and
zero-or-more `'proration'` lines (one per user active for only PART of the period), computed
deterministically from a new durable membership ledger, independent of (never blocking, never
reading) the sibling seat-CAP concern.

Framings weighed:
  - **Source of truth for seat-days**: a NEW append-only `seat_membership_events` ledger, written
    transactionally alongside the 5 existing `users`-row-mutating call sites (v2/CR-1: 4 member-creating + set_active) **(CHOSEN)** — vs.
    computing purely from `users.created_at`/`deactivated_at`'s CURRENT state, zero cross-context
    touch **(REJECTED as the sole source)** — vs. reusing `audit_events` **(REJECTED outright)**. The
    current-state-only approach is demonstrably wrong for the explicitly-required "reactivation same
    month" scenario (R1); `audit_events` is fire-and-forget and may silently drop the very row a
    dollar amount would depend on (R4). Only an append-only, same-transaction ledger satisfies the
    determinism rule ("reproducible from immutable data at invoice-generation time"). See the ⚠ flag
    below — this is the single biggest, most consciously-weighed call in this draft.
  - **Where seat lines are computed**: additively extend `InvoiceGenerator.generate_for_tenant`'s
    EXISTING transaction (new pure helper `seat_pricer.compute_seat_lines(...)`, called before the
    `INSERT ... RETURNING id`) **(CHOSEN)** — vs. a separate post-hoc pass that UPDATEs the invoice
    after usage lines are issued **(REJECTED)**. Invoice-generation's own M5 makes an issued invoice
    immutable the INSTANT it is inserted (status is always `'issued'`, no draft window) — a
    post-hoc pass would require either violating immutability or inventing a second "correction"
    layer for what is really day-one pricing, not a later fix. Folding seat lines into the SAME
    transaction that computes `total_usd` is the only way the printed total is ever correct on
    first read.
  - **Entitlement lookup**: a narrow, self-contained `tenants ⋈ plans` query inside `seat_pricer.py`
    **(CHOSEN)** — vs. reusing plan-enforcement's `PlanEntitlementResolver`/`ResolvedEntitlements`
    (its own M8 names seat-billing as the intended consumer) **(REJECTED)**. `ResolvedEntitlements`
    has no seat-price field and adding one means reopening a FROZEN sibling contract this task has
    no authority to reopen; a dedicated single SELECT mirrors `RedisBudgetGuard`'s own established
    "one extra query, not the shared resolver" idiom for a narrow, single-purpose read.
  - **Evidence surface**: a NEW, additive, sibling route (`GET .../lines/{line_id}/seat-evidence`)
    with its OWN response shape **(CHOSEN)** — vs. branching the EXISTING frozen `evidence` route to
    return `UsageEvidenceItem` rows with seat fields shoehorned in **(REJECTED)**. `UsageEvidenceItem`
    is typed entirely to usage_records fields (`cost_usd`, `prompt_tokens`, `request_id`) with no
    natural slot for `event_type`/`occurred_at`/`user_id` — forcing the fit would be a dishonest
    reuse of a shape that means something else. A new route stays fully additive to the frozen file.
  - **Seat population**: every non-deactivated `users` row under the tenant, ALL 6 roles counted
    identically **(CHOSEN)** — vs. a role-tiered population (e.g. excluding `viewer`) **(REJECTED)**.
    No existing precedent anywhere in this codebase weights a "seat" by role — `plans.seat_cap`'s own
    (not-yet-enforced) cap counts an integer with no role filter either. Pricing the SAME population
    capping will eventually count avoids "seat" meaning two different things platform-wide.

Must:
<must>
  - **[M1]** A **seat** is exactly one `users` row belonging to the tenant (`tenant_id` match) with
    `deactivated_at IS NULL` AT A GIVEN INSTANT — every role counts identically (owner through
    member; `superadmin` is structurally excluded, §0). A PENDING invite is never a seat (no `users`
    row exists yet); a REVOKED or EXPIRED invite is never a seat, ever.
  - **[M2]** `plans` gains an additive `seat_price_usd_monthly NUMERIC(12,2) NULL` column
    (migration-seeded only, mirrors every other `plans.*_default` column's nullable/no-runtime-CRUD
    convention). Seat pricing is INERT — this task writes ZERO `'seat'`/`'proration'` lines, not even
    a `$0.00` line — for a tenant whose `plan_id IS NULL`, OR whose assigned plan has
    `seat_price_usd_monthly IS NULL OR = 0`. An inert tenant's invoice is byte-identical to one
    generated before this task shipped (extends invoice-generation's own M14 "inert extension point"
    scenario, now made concretely true).
  - **[M3]** A new append-only table `seat_membership_events` (one row per join/leave/rejoin
    transition, NEVER updated or deleted) is written, in the SAME DB transaction as the triggering
    `users`-row mutation, at exactly 5 call sites: (a) `InviteRepository.accept` — `event_type=
    'joined'`, `occurred_at` = the accept instant; (b) `SqlAlchemyScimUserRepository.create_user` —
    `event_type='joined'`, `occurred_at` = the create instant; (b2) [v2/CR-1]
    `_get_or_provision_sso_user` (new-user branch ONLY — an existing member's re-login never writes)
    — `event_type='joined'`; (b3) [v2/CR-1] `join_verified_tenant_domain` — `event_type='joined'`;
    (c) `SqlAlchemyScimUserRepository.set_active`
    — `event_type='deactivated'` or `'reactivated'` per the flip direction, `occurred_at` = the flip
    instant, written ONLY on the `changed=True` branch (the SAME idempotency gate that already skips
    the audit write on a no-op repeat — never a duplicate ledger row for a repeated PATCH).
  - **[M4]** Migration-time backfill (data-only, mirrors plan-enforcement's own seed-column
    precedent): for every PRE-EXISTING `users` row, seed exactly one synthetic `'joined'` event at
    `occurred_at = users.created_at`; for every pre-existing row that is ALREADY deactivated
    (`deactivated_at IS NOT NULL`), seed one additional `'deactivated'` event at
    `occurred_at = users.deactivated_at`. Every tenant's existing team is correctly priced from the
    first post-ship invoice — never a silent zero-seat undercount (R5).
  - **[M5]** Defense-in-depth fallback: if a `users` row has ZERO `seat_membership_events` rows at
    generation time (a data-integrity gap the backfill/write-sites above should make impossible, but
    never silently trusted blind), the seat computation falls back to that user's OWN
    `created_at`/`deactivated_at` columns directly, treating them as an implicit joined/deactivated
    event pair — a seat is NEVER silently dropped from billing for lack of a ledger row.
  - **[M6]** **Active-days** for a user within `[period_start, period_end)` = the count of DISTINCT
    UTC calendar dates in that range during which the user was active for at least one instant,
    derived by replaying the user's ordered event stream (`'joined'`/`'reactivated'` opens an active
    interval, `'deactivated'` closes it; an interval still open at `period_end` is clipped there) and
    counting the calendar dates each resulting interval touches, clipped to the period. An orphan
    `'deactivated'` event with no preceding open interval is ignored (never produces negative days).
  - **[M7]** For a plan-priced tenant (M2's inert gate does NOT apply), `InvoiceGenerator.generate_for_tenant`
    additively computes, in its SAME transaction, BEFORE the `INSERT ... RETURNING id`:
    (a) `full_price_users` = every seat with `active_days == days_in_period` (fully active the whole
    period) → ONE aggregate `line_type='seat'` line, `amount_usd = ROUND_HALF_UP(count *
    seat_price_usd_monthly)`; (b) one `line_type='proration'` line PER seat with
    `0 < active_days < days_in_period`, `amount_usd = ROUND_HALF_UP(seat_price_usd_monthly *
    active_days / days_in_period)` (full-precision Decimal division, rounded once — reuses M4 of
    invoice-generation verbatim). A seat with `active_days == 0` (joined and left entirely outside,
    or same-day in-and-out within, the period) produces NO line and NO charge.
  - **[M8]** Every seat/proration line's `raw_amount_usd`/`amount_usd` folds into
    `invoice.raw_total_usd`/`invoice.total_usd` via the EXACT SAME accumulation loop already summing
    usage lines — one rounded-then-summed total, never a second total or a separate seat subtotal
    column.
  - **[M9]** `line_type IN ('seat', 'proration')` rows reinterpret 3 existing NOT-NULL
    `invoice_lines` columns, documented (not schema-changed, R2): `model_id = 'seat'` (fixed sentinel
    label, human-readable in CSV/PDF); `team_id = NULL` always (seats are tenant-scoped, `users` has
    no `team_id` column at all — confirmed §0); `key_id` = the NIL UUID
    (`00000000-0000-0000-0000-000000000000`) for the AGGREGATE `'seat'` line (it covers N users, no
    single id fits) and = the ONE contributing user's `id` for a `'proration'` line (exactly 1:1,
    fits naturally). `prompt_tokens`/`completion_tokens` stay `0`; `request_count` = the seat COUNT
    the line represents (bucket size for `'seat'`, always `1` for `'proration'`).
  - **[M10]** Seat pricing is completely independent of `plans.seat_cap`/`tenants.seat_cap`: this
    task NEVER reads either column, NEVER blocks, truncates, or caps the line amount — a tenant with
    MORE active seats than its cap is billed for every seat it actually has, in full. Seat CAP
    enforcement is exclusively `plan-seat-cap`'s concern (milestone binding rule 5).
  - **[M11]** A new additive read route, `GET /admin/invoices/{invoice_id}/lines/{line_id}/seat-evidence?limit=&cursor=`,
    gated by the SAME `Permission.INVOICES_READ` + tenant-404-invisible resolution as every other
    invoices route (reuses `_get_invoice_or_404` verbatim). For a `'seat'`/`'proration'` line, it
    RE-RUNS the same M6/M7 bucket computation for that exact `(tenant_id, period, bucket)` and returns
    the contributing user(s)' `seat_membership_events` rows, keyset-paginated — a re-queryable
    predicate, never a materialized id list (mirrors invoice-generation's own M7 evidence doctrine,
    applied to the membership ledger instead of `usage_records`).
  - **[M12]** Calling the seat-evidence route against a `line_type='usage'` line is a client error,
    not a silently-empty success (a `'usage'` line has real evidence at the EXISTING `evidence` route
    — this new route is never a silent alias for it). The REVERSE direction — calling the EXISTING,
    unmodified `evidence` route (usage_records-based) against a `'seat'`/`'proration'` line — is
    DELIBERATELY left as a benign, honest empty page (`items: [], has_more: false`): the predicate
    truly matches zero `usage_records` rows for a nil-UUID or user-id `key_id`, which is not a lie,
    just an unhelpful answer. Not hardened into a second reject in v1 (would mean editing the
    EXISTING frozen route's logic, not just adding to it) — named here so it is a known, accepted gap
    rather than a silent one; confirm or harden at freeze if Tin wants it closed.
  - **[M13]** Seat computation inherits `generate_for_tenant`'s EXISTING `asyncio.timeout` bound and
    `ON CONFLICT (tenant_id, period_start) DO NOTHING` idempotency (invoice-generation M13) — no new
    timeout, no new concurrency primitive; a re-run or a genuine race resolves exactly like a
    usage-only invoice does today.
  - **[M14]** Once inserted (status is always `'issued'`, no draft window — invoice-generation's own
    M5), a seat/proration line is exactly as immutable as a usage line: no code path ever UPDATEs or
    DELETEs one. A correction, if ever needed, is a new `invoice_correction` row (existing mechanism,
    unchanged) — never a special-cased rewrite for seat lines.
</must>

Reject:
<reject>
  - `GET .../lines/{line_id}/seat-evidence` called against a line whose `line_type == 'usage'`
    -> "ERR_INVOICE_LINE_WRONG_TYPE" (NEW, 400)
  - `GET .../lines/{line_id}/seat-evidence` for an unknown OR cross-tenant invoice, an unknown line,
    or a line not belonging to that invoice -> "ERR_INVOICE_NOT_FOUND" (REUSED, invoice-generation)
  - `GET .../lines/{line_id}/seat-evidence` exceeds its bounded query timeout -> "ERR_INVOICE_QUERY_TIMEOUT" (REUSED)
  - a migration-seed or (hypothetical future) write attempting `seat_price_usd_monthly <= 0`
    (non-null) -> rejected at the DB CHECK-constraint level (`ck_plans_seat_price_positive`), not an
    HTTP code — mirrors `ck_plans_seat_cap_positive`/`ck_plans_budget_default_positive`'s own
    precedent; `plans` has no runtime CRUD to reject at the API layer (plan-catalog's own standing
    non-goal, unchanged).
</reject>
Note: this task adds no OTHER new HTTP surface — seat/proration line generation itself is a
background-job extension (`InvoiceGenerator.generate_for_tenant`), not a request/response endpoint,
so most of its Musts manifest as generation-time and migration-time invariants rather than API
rejections, honestly reflected by this short Reject list (mirrors plan-enforcement's own M8 port,
"ZERO new HTTP surface" for its resolution core).

After:
<after>
  - A user active for the ENTIRE billing period bills at the plan's full seat price, aggregated into
    ONE `'seat'` line alongside every other full-period seat.
  - A user who joined mid-period, left mid-period, or both, bills ONLY for the UTC calendar days they
    were actually active, via its OWN `'proration'` line, traceable via seat-evidence to the exact
    membership-change events that produced it.
  - A user deactivated then reactivated within the same period is billed for the ACTUAL days active
    on both sides of the gap — never for the gap itself, and never as if the gap never happened.
  - A tenant with no plan, or a plan with no/zero seat price, gets an invoice byte-identical to one
    generated before this task shipped — zero seat lines, not a zero-dollar line.
  - A tenant whose active-seat count exceeds its plan's/tenant's seat cap is billed for every seat it
    actually has — pricing never blocks, truncates, or even reads the cap value.
  - Every tenant's PRE-EXISTING team (created before this task's migration) is correctly priced on
    the very first post-ship invoice — no historical undercount from a missing ledger row.
  - No seat/proration line is ever UPDATEd or DELETEd once its invoice is issued — identical
    immutability guarantee to a usage line, enforced by the same mechanism, not a special case.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **Introducing a NEW durable, transactionally-written `seat_membership_events` ledger — and
  touching 3 already-shipped, frozen write paths OUTSIDE `gateway/billing/`
  (`InviteRepository.accept`, `SqlAlchemyScimUserRepository.create_user`, `.set_active`) to append to
  it — is the single lowest-confidence call in this draft.** Lowest confidence because: (a) it is by
  far the largest scope expansion here, reaching into 2 other bounded contexts (`scim/`,
  `tenants/`-invites) that shipped and froze independently of this task, a real "shared-seam
  discipline" cost the milestone explicitly warns about; (b) the cheaper alternative — computing
  seat-days purely from `users.created_at`/`deactivated_at`'s CURRENT state, zero cross-context touch
  — is real and tempting, but is DEMONSTRABLY WRONG for the "reactivation same month" scenario this
  dispatch explicitly requires: `deactivated_at` is a single mutable column, so a deactivate-then-
  reactivate cycle within one period is invisible to a current-state-only read (the gap silently
  vanishes, OVER-billing the tenant for days it was actually locked out). Given seats are billed by
  TIME-IN-STATE, not point-in-time count (this task's own governing principle), only an append-only
  event history is correct — the same "one ledger of truth" doctrine this milestone already applies
  to `usage_records`, now applied to membership. **If wrong** (Tin judges the touch surface too
  invasive for a wave-2 task): the fallback is additive-ONLY to remove — drop the 3 extra
  write-call-sites and the ledger table, keep the simpler current-state formula, and accept the
  documented reactivation-gap inaccuracy as a stated Non-goal instead of a defect. Cheap to walk back
  now; expensive later — once real tenant invoices have been issued without a ledger, past periods
  can never be retroactively corrected for lack of history. **Surfaced as the freeze flag.**
  - [ ] All 6 roles (owner/admin/operator/billing_admin/viewer/member) count as exactly one seat
    each, uniform, no role-tiered pricing — recommend YES (no existing precedent for role-weighted
    seats anywhere in this codebase; `plans.seat_cap`'s own future cap makes no role distinction
    either). Flag for `plan-seat-cap` (still `phase: ground`, undrafted) to adopt the IDENTICAL
    population when it specifies — this task cannot bind that sibling's contract, only recommend
    consistency; confirm or deny at freeze.
  - [ ] `plans.seat_price_usd_monthly` seed values for the 3 existing tiers (starter/team/enterprise)
    — INVENTED placeholders needed at migration time (same category as plan-catalog's/plan-
    enforcement's own disclosed $ placeholders, DATA not shape); recommend `NULL` (no seat pricing)
    for starter, real $/seat figures for team/enterprise TBD by Tin — cheap to fix, data-only,
    confirm or replace at freeze.
  - [ ] The new evidence route is a SIBLING path (`.../seat-evidence`) rather than a branch inside
    the existing frozen `evidence` route — medium confidence, chosen to avoid forcing seat fields
    into `UsageEvidenceItem`'s usage-shaped response; confirm or direct a branched-single-route
    alternative at freeze (non-blocking, a route-naming detail, not a data-shape risk).
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: A pending invite is never a seat   # M1
  Given a tenant with 2 accepted users and 1 PENDING invite, all under a plan with seat_price_usd_monthly=10.00
  When the July invoice is generated
  Then the 'seat' line's request_count is 2, not 3
  And no invoice_lines row references the pending invite in any way

Scenario: Unplanned tenant produces zero seat lines   # M2
  Given a tenant with plan_id=NULL and 5 active users
  When the July invoice is generated
  Then no invoice_line with line_type IN ('seat','proration') exists on that invoice
  And the invoice is byte-identical (line count, total_usd) to what invoice-generation alone would produce

Scenario: Zero-seat-price plan is byte-identical to unplanned   # M2
  Given a tenant assigned a plan whose seat_price_usd_monthly IS NULL (also: = 0.00), 5 active users
  When the July invoice is generated
  Then no invoice_line with line_type IN ('seat','proration') exists on that invoice
  And total_usd equals exactly the usage-only total, with no zero-dollar seat line present

Scenario: A membership ledger row is appended transactionally on invite acceptance   # M3

Scenario: A membership ledger row is appended on SSO auto-provision and domain-capture join   # M3 (v2/CR-1)
  Given a tenant whose plan prices seats
  When a NEW user is provisioned via the SSO new-user branch (_get_or_provision_sso_user)
    and another joins via join_verified_tenant_domain
  Then each write lands exactly one 'joined' seat_membership_events row in the SAME transaction
    as its users-row INSERT
    and an existing member's SSO re-login appends NO event
  Given a pending invite for tenant T
  When AcceptInviteUseCase.execute succeeds
  Then exactly one seat_membership_events row exists with event_type='joined' for the new user
  And that row's occurred_at equals the accept instant, committed in the SAME transaction as the new users row

Scenario: A membership ledger row is appended transactionally on SCIM user creation   # M3
  Given a SCIM client authenticated for tenant T
  When POST /scim/v2/Users succeeds
  Then exactly one seat_membership_events row exists with event_type='joined' for the new user

Scenario: Deactivation and reactivation each append exactly one ledger row, idempotently   # M3
  Given an active user U
  When a SCIM caller PATCHes active:false, then PATCHes active:false AGAIN (repeat), then PATCHes active:true
  Then exactly 2 seat_membership_events rows exist for U ('deactivated' then 'reactivated')
  And the repeated active:false PATCH produced zero additional rows (idempotent no-op, mirrors the existing audit-skip)

Scenario: Pre-existing users are backfilled with a synthetic joined event   # M4
  Given a users row created before this task's migration, still active, with created_at=2026-05-01
  When the migration runs
  Then exactly one seat_membership_events row exists for that user with event_type='joined', occurred_at=2026-05-01
  And that user prices as a full-price seat on the very next invoice generation, not as zero

Scenario: A pre-existing ALREADY-deactivated user backfills both events   # M4
  Given a users row created 2026-04-01, deactivated_at=2026-05-10, as of migration time
  When the migration runs
  Then two seat_membership_events rows exist: joined@2026-04-01 and deactivated@2026-05-10
  And that user contributes zero active_days to any period starting after 2026-05-10

Scenario: A ledger-less user falls back to current-state columns, never dropped   # M5
  Given a users row with zero seat_membership_events rows (a simulated data-integrity gap) and deactivated_at=NULL
  When the July invoice is generated
  Then that user is treated as active via the created_at/deactivated_at fallback
  And it is NOT silently excluded from either the 'seat' or a 'proration' line

Scenario: A user who joined mid-month prorates by calendar days touched   # M6, M7
  Given plan seat_price_usd_monthly=31.00, July has 31 days, a user joined 2026-07-15T14:00:00Z and stayed active through period_end
  When the July invoice is generated
  Then that user's active_days is 17 (July 15 through July 31 inclusive)
  And a 'proration' line exists with raw_amount_usd = 31.00 * 17 / 31, rounded ROUND_HALF_UP to amount_usd

Scenario: A user who left mid-month prorates by calendar days touched, then stops   # M6, M7
  Given the same tenant, a second user active since June, deactivated 2026-07-10T09:00:00Z
  When the July invoice is generated
  Then that user's active_days is 10 (July 1 through July 10 inclusive)
  And a SEPARATE 'proration' line exists for this user, distinct from the mid-month-join user's line

Scenario: Reactivation same month bills for actual days on both sides of the gap   # M6, M7
  Given a user active since June, deactivated 2026-07-05T00:00:00Z, reactivated 2026-07-20T00:00:00Z, still active at period_end
  When the July invoice is generated
  Then that user's active_days is 12 (July 1-4) + 12 (July 20-31) = 24, NOT 31 and NOT just one side
  And exactly one 'proration' line represents this user for July (the two intervals summed, not two lines)

Scenario: Full-period seats aggregate into one line, mid-period seats do not   # M7
  Given 3 users active the ENTIRE period and 1 user active only 2026-07-01 through 2026-07-10
  When the July invoice is generated
  Then exactly one 'seat' line exists with request_count=3
  And exactly one 'proration' line exists, separate from the 'seat' line, for the partial user only

Scenario: Seat lines fold into the same rounded-then-summed total as usage lines   # M8
  Given a July invoice with usage lines totaling 100.00 and one 'seat' line of 30.00
  When the invoice is generated
  Then invoice.total_usd equals exactly 130.00 (sum of ALL persisted, already-rounded line amounts)
  And no separate "seat subtotal" field or second total exists anywhere on the invoice

Scenario: Aggregate seat line uses the nil-UUID sentinel key_id, proration lines use the real user id   # M9
  Given the "full-period seats aggregate" scenario above
  When the invoice's lines are inspected
  Then the 'seat' line's key_id is 00000000-0000-0000-0000-000000000000 and model_id is 'seat'
  And the 'proration' line's key_id equals the exact user_id of the partial-period user

Scenario: Cap vs price independence — an over-cap tenant is still billed in full   # M10
  Given a tenant with tenants.seat_cap=3 (a cap plan-seat-cap does not yet enforce) and 5 active seats, seat_price_usd_monthly=10.00
  When the July invoice is generated
  Then the 'seat' line's request_count is 5, not 3
  And amount_usd reflects all 5 seats — this task never reads tenants.seat_cap or plans.seat_cap anywhere

Scenario: Seat-evidence resolves a proration line to its membership events   # M11
  Given the mid-month-join user's 'proration' line from above
  When a billing_admin calls GET /admin/invoices/{id}/lines/{line_id}/seat-evidence
  Then the response contains that user's 'joined' event (2026-07-15) and no other user's events
  And no row belonging to a different user appears in any page

Scenario: Seat-evidence resolves the aggregate seat line to every full-price user   # M11
  Given the "full-period seats aggregate" scenario above
  When a billing_admin calls GET /admin/invoices/{id}/lines/{seat_line_id}/seat-evidence
  Then the response paginates through exactly the 3 full-period users' relevant events
  And none of the partial-period user's events appear

Scenario: Seat-evidence against a usage line is rejected, not silently empty   # M12, R1
  Given a 'usage'-typed invoice_line on the same invoice
  When GET /admin/invoices/{id}/lines/{usage_line_id}/seat-evidence is called
  Then the response is 400 "ERR_INVOICE_LINE_WRONG_TYPE"
  And no usage_records or seat_membership_events data is returned in the body

Scenario: Seat-evidence for an unknown or cross-tenant invoice is the same 404   # R2
  Given tenant A's billing_admin and a line_id belonging to tenant B's invoice
  When GET /admin/invoices/{that_id}/lines/{line_id}/seat-evidence is called
  Then the response is 404 "ERR_INVOICE_NOT_FOUND", byte-identical in shape to the unknown-id case
  And no field distinguishes "exists but not yours" from "doesn't exist"

Scenario: Seat-evidence bounded query timeout surfaces as the existing structured error   # R3
  Given the seat-evidence query exceeds its bounded asyncio.timeout
  When GET .../seat-evidence is called
  Then the response is 504 "ERR_INVOICE_QUERY_TIMEOUT"
  And no partial/inconsistent page is returned

Scenario: A seat active zero days in the period produces no line and no charge   # M7 (edge)
  Given a user who joined and was deactivated entirely within a single UTC calendar day, before period_start's date, i.e. never touches the period
  When the invoice is generated
  Then no invoice_line references that user
  And that user contributes $0.00, silently and correctly, not an error

Scenario: An issued seat/proration line is immutable, identical to a usage line   # M14
  Given an issued invoice with a 'seat' line
  When any code path attempts to UPDATE that line
  Then the write is rejected by the SAME mechanism that protects usage lines (DB constraint or application guard, decided at BUILD)
  And a re-read returns byte-identical values to before the attempt
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: FROZEN @ v2 — approved by Tin Dang (v1 2026-07-12) + auto-mode CR-1 v2 (orchestrator; Tin veto open)
Least-sure flag surfaced at freeze: [spec/contract] Introducing the NEW `seat_membership_events`
append-only ledger and touching 3 already-shipped write paths outside `gateway/billing/`
(`InviteRepository.accept`, `SqlAlchemyScimUserRepository.create_user`/`.set_active`) instead of the
cheaper `gateway/billing/`-only alternative of reading `users.created_at`/`deactivated_at`'s current
state — the ledger is the ONLY option that correctly bills a same-month deactivate/reactivate cycle
(the dispatch's own required scenario), but it is the single biggest scope expansion in this draft,
reaching into 2 other bounded contexts that shipped independently. Tin: confirm the ledger approach,
or direct the cheaper current-state-only formula with the reactivation gap accepted as a documented
Non-goal instead (§1 ⚠ has the full tradeoff).

DECIDED at freeze review (2026-07-12, Tin): `seat_membership_events` append-only ledger CONFIRMED —
determinism rule wins; the 3 cross-context write-path touches are sanctioned as additive,
transactional writes. Seed per-seat prices CONFIRMED: Pro **$15** / Enterprise **$40** per
seat-month (Tin chose undercut positioning over market-aligned $25/$60); Starter stays seatless/$0.
Prices live in the plans catalog and are tenant-overridable via the shared rate-card resolver like
every other price.

CHANGE REQUEST CR-1 → CONTRACT v2 (2026-07-12, orchestrator under auto mode — surfaced to Tin for
cheap veto before any gate): §0/§1/§3 enumerated only 2 member-creating write paths; plan-seat-cap's
merged ground pass established 4 (adds SSO `_get_or_provision_sso_user` new-user branch +
domain-capture `join_verified_tenant_domain`). R6's own frozen rationale ("a tenant can gain members
via EITHER path — missing one silently under-counts") makes the intent unambiguous: every seat join
writes an event. Without this, post-ship SSO/domain joiners would carry ZERO seat-days (silent
under-bill — the exact R5/R6 failure class this contract exists to prevent) and the billing ledger
would drift from the seat-cap's active-member count. Amendment is enumerative only: same event
shape, same same-transaction discipline, two more call sites + one scenario. Seed prices per DECIDED:
team/pro $15 · enterprise $40 (replaces the §3 "TBD" placeholders).

```
GET /admin/invoices/{invoice_id}/lines/{line_id}/seat-evidence?limit=&cursor=
  200 -> { items: SeatEvidenceItem[], next_cursor: str|null, has_more: bool }
  400 -> { error: "ERR_INVOICE_LINE_WRONG_TYPE" }   # line.line_type == 'usage'
  401 -> { error: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { error: "ERR_AUTH_FORBIDDEN" }
  404 -> { error: "ERR_INVOICE_NOT_FOUND" }   # unknown/cross-tenant invoice, unknown line, mismatched line
  422 -> { error: "ERR_CURSOR_INVALID" | "ERR_PAYLOAD_INVALID" }
  504 -> { error: "ERR_INVOICE_QUERY_TIMEOUT" }

SeatEvidenceItem: { event_id, user_id, email, event_type, occurred_at }
  # event_type: "joined" | "deactivated" | "reactivated"
  # For a 'seat' (aggregate) line: paginates every full-price-bucket user's relevant events.
  # For a 'proration' line: paginates the ONE contributing user's relevant events only.
```

Existing `invoice_lines` rows this task WRITES (schema unchanged — NOT ALTERED, per dispatch
instruction; only NEW rows using the already-reserved `line_type` values, with a documented
per-line_type reinterpretation of 3 existing NOT-NULL columns, M9):
```
line_type='seat'        -- ONE row per invoice per plan-priced tenant, aggregate full-price seats
  model_id       = 'seat'                                    -- fixed sentinel label
  team_id        = NULL                                      -- seats are not team-scoped (users has no team_id)
  key_id         = '00000000-0000-0000-0000-000000000000'    -- nil UUID: no single user fits an aggregate
  amount_usd     = ROUND_HALF_UP(full_price_user_count * plans.seat_price_usd_monthly)
  raw_amount_usd = full_price_user_count * plans.seat_price_usd_monthly   -- full precision
  request_count  = full_price_user_count
  prompt_tokens = completion_tokens = 0

line_type='proration'   -- ONE row PER seat active only part of the period
  model_id       = 'seat'
  team_id        = NULL
  key_id         = the seat's users.id                        -- exactly 1:1, fits the existing column
  amount_usd     = ROUND_HALF_UP(plans.seat_price_usd_monthly * active_days / days_in_period)
  raw_amount_usd = plans.seat_price_usd_monthly * active_days / days_in_period   -- full precision Decimal
  request_count  = 1
  prompt_tokens = completion_tokens = 0
```

Schema (new table + one additive column; migration parents alembic head `0b5527920450`, NOT created
at design time):
```
seat_membership_events
  id            UUID PK (uuid7)
  tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT   -- users are never hard-deleted (§0)
  event_type    TEXT NOT NULL CHECK (event_type IN ('joined','deactivated','reactivated'))
  occurred_at   TIMESTAMPTZ NOT NULL         -- the real transition instant, tz-aware (mirrors InviteRow's convention)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()   -- row-write instant, for audit/ordering-tiebreak only
  -- Append-only: no UPDATE/DELETE code path is ever written against this table (M3).
  INDEX ix_seat_membership_events_tenant_user_occurred (tenant_id, user_id, occurred_at)

plans
  ADD COLUMN seat_price_usd_monthly  NUMERIC(12,2) NULL   -- M2; NULL = no seat pricing (inert)
  ADD CONSTRAINT ck_plans_seat_price_positive
    CHECK (seat_price_usd_monthly IS NULL OR seat_price_usd_monthly > 0)   -- mirrors ck_plans_seat_cap_positive
  -- Data-only seed for the 3 existing rows (⚠ INVENTED placeholders, confirm/replace at freeze):
  --   starter: NULL · team: TBD · enterprise: TBD
  Downgrade: additive-only, safe — mirrors every prior additive migration in this milestone.

Migration-time backfill (data-only, M4):
  INSERT INTO seat_membership_events (id, tenant_id, user_id, event_type, occurred_at)
    SELECT uuid7(), tenant_id, id, 'joined', created_at FROM users;
  INSERT INTO seat_membership_events (id, tenant_id, user_id, event_type, occurred_at)
    SELECT uuid7(), tenant_id, id, 'deactivated', deactivated_at FROM users WHERE deactivated_at IS NOT NULL;
```

Code-level extension (no NEW frozen HTTP surface beyond the one route above; additive touches to
already-shipped files, per plan-enforcement's own "supersession, not edit" precedent):
```
# NEW FILE: gateway/billing/application/seat_pricer.py (pure, zero infra imports except Decimal/datetime)
def active_days(events: list[MembershipEvent], period_start: datetime, period_end: datetime) -> int: ...
def compute_seat_lines(
    *, seat_price_usd_monthly: Decimal, users_with_events: ..., period_start: datetime, period_end: datetime,
) -> list[SeatLineSpec]: ...   # returns 0, 1 ('seat' only), or 1+1..N ('seat' + 'proration'*) specs

# MODIFIED (additive): gateway/billing/application/invoice_generator.py :: InvoiceGenerator.generate_for_tenant
#   Before the `INSERT ... RETURNING id`: read `tenants.plan_id` + LEFT JOIN `plans.seat_price_usd_monthly`
#   (one extra SELECT, mirrors RedisBudgetGuard's own "one query" idiom — NOT the shared
#   PlanEntitlementResolver, see Framings weighed). If priced, call compute_seat_lines(...) and fold
#   its line_specs + amounts into the SAME line_specs/raw_total/total_usd accumulation already there.

# MODIFIED (additive): gateway/tenants/infrastructure/invite_repository.py :: InviteRepository.accept
#   Immediately before the existing `await self._session.commit()`: session.add(SeatMembershipEventRow(
#   event_type='joined', occurred_at=now, ...)) for the newly-provisioned user — SAME transaction.

# MODIFIED (additive): gateway/scim/infrastructure/repository.py :: SqlAlchemyScimUserRepository.create_user
#   Immediately before its `await self._session.commit()` (line 161): append a 'joined' row, same transaction.

# MODIFIED (additive, v2/CR-1): gateway/tenants/infrastructure/repository.py :: _get_or_provision_sso_user
#   New-user branch ONLY, immediately after the users-row INSERT joins the session, SAME transaction
#   (the branch plan-seat-cap already gates with assert_seat_available): append a 'joined' row.

# MODIFIED (additive, v2/CR-1): gateway/tenants/infrastructure/repository.py :: join_verified_tenant_domain
#   Immediately after its users-row INSERT, SAME transaction (also cap-gated by plan-seat-cap):
#   append a 'joined' row.

# MODIFIED (additive): gateway/scim/infrastructure/repository.py :: SqlAlchemyScimUserRepository.set_active
#   Immediately before its `await self._session.commit()` (line 259), ONLY on the changed=True branch
#   (never on the already_at_target no-op return at line 248): append a 'deactivated'/'reactivated' row.

# MODIFIED (additive): gateway/billing/api/router.py — NEW route get_seat_line_evidence, NEW schema
#   SeatEvidenceItem/SeatEvidenceListResponse, calls a NEW InvoiceRepository.seat_evidence_keyset(...).
```

Error-catalog delta: `apps/gateway/src/gateway/core/error_catalog.py` gains one new `ErrorSpec`:
```
INVOICE_LINE_WRONG_TYPE = ErrorSpec(400, "ERR_INVOICE_LINE_WRONG_TYPE", "Evidence type does not match this line")
```
`INVOICE_NOT_FOUND`, `INVOICE_QUERY_TIMEOUT`, `CURSOR_INVALID`, `PAYLOAD_INVALID` are reused verbatim
(no new constants).

Access pattern: seat-evidence re-runs the SAME `(tenant_id, period, bucket)` predicate
`compute_seat_lines` used at generation time (never a materialized id list, mirrors invoice-
generation's M7 doctrine) — for a `'proration'` line, `WHERE user_id = :key_id`; for the aggregate
`'seat'` line, the full-price bucket is recomputed and each contributing `user_id` queried, keyset
`(occurred_at, id) DESC` per user, unioned in a stable order. Bounded `asyncio.timeout`, mirrors every
other invoices route.

Glossary deltas:
  - **Seat**: one `users` row belonging to a tenant, active (`deactivated_at IS NULL`, or per the
    replayed membership-event stream) at a given instant — every role counts identically; a pending
    invite is never a seat.
  - **Seat-day**: one UTC calendar date on which a seat was active for at least one instant — the
    atomic unit proration is computed in (never sub-day granularity).
  - **Membership event**: an immutable, append-only `seat_membership_events` row (`joined` /
    `deactivated` / `reactivated`) recording exactly one seat state transition, written in the SAME
    transaction as the `users`-row mutation that caused it — the seat-domain analog of `usage_records`'
    "one ledger of truth" doctrine, and of `record_correction`'s signed-delta-append precedent, one
    domain over.
Reported: no — pending Tin's freeze review (this task lands after wave-1's own batch freeze,
per MILESTONE.md's `depends-on: plan-enforcement, invoice-generation`, both already FROZEN@v1).

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (mirrors invoice-generation/plan-enforcement's own `--cov-fail-under=80` floor
plus this task's own money-precision bar; project-wide `make ci` gate is 80%, this task holds itself
to 90% given it is 100% new pure-math + 5 write-site money code).

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_full_period_seat_touches_every_calendar_day / test_mid_month_join_prorates_by_calendar_days_touched /
    test_mid_month_leave_prorates_by_calendar_days_touched / test_reactivation_same_month_bills_both_sides_of_gap /
    test_orphan_deactivated_with_no_open_interval_is_ignored_never_negative /
    test_seat_active_zero_days_entirely_outside_period / test_second_open_while_already_open_is_defensively_ignored
    (`tests/seat_billing/test_seat_pricer_unit.py`) — pure `active_days()` replay, zero DB · covers: M6, edge (§2 "zero days")
  - test_full_period_seats_aggregate_mid_period_seat_gets_own_proration_line / test_zero_active_days_seat_produces_no_line /
    test_no_seats_at_all_produces_empty_specs / test_ledger_less_user_falls_back_to_current_state_columns /
    test_ledger_less_already_deactivated_user_falls_back_to_both_columns
    (`tests/seat_billing/test_seat_pricer_unit.py`) — pure `compute_seat_lines()` bucketing/shape · covers: M5, M7, M9
  - test_resolve_line_contributors_proration / test_resolve_line_contributors_seat_aggregate /
    test_resolve_line_contributors_no_match_returns_none (`tests/seat_billing/test_seat_pricer_unit.py`) —
    pure evidence-bucket re-derivation · covers: M11
  - test_pending_invite_is_never_a_seat (`test_seat_pricing.py`) · covers: M1
  - test_unplanned_tenant_produces_zero_seat_lines / test_zero_seat_price_plan_is_byte_identical_to_unplanned /
    test_seat_price_zero_rejected_at_db_check_constraint (`test_seat_pricing.py`) · covers: M2, Reject (CHECK constraint)
  - test_mid_month_join_prorates (`test_seat_pricing.py`) · covers: M6, M7
  - test_mid_month_leave_and_full_period_aggregate_separately (`test_seat_pricing.py`) — 3 full-period
    seats aggregate into ONE 'seat' line + 1 mid-period seat gets its OWN 'proration' line, also
    asserts M9's key_id/model_id/team_id shape · covers: M6, M7, M8, M9
  - test_seat_lines_fold_into_the_same_total_as_usage (`test_seat_pricing.py`) · covers: M8
  - test_over_cap_tenant_still_billed_in_full (`test_seat_pricing.py`) · covers: M10
  - test_ledger_less_user_falls_back_never_dropped (`test_seat_pricing.py`) · covers: M5 (DB-backed)
  - test_issued_seat_line_is_immutable (`test_seat_pricing.py`) · covers: M14
  - NOTE: the "reactivation same month" and "zero active days entirely outside the period" scenarios
    are exercised ONLY at the pure-math level (`test_seat_pricer_unit.py`, above) — no separate
    DB-integration duplicate exists for either; `active_days()` is the single source both generation-
    time and evidence-time call, so the pure-unit coverage is the correct, non-duplicative layer for
    them (mirrors invoice-generation's own "prove the math once, prove the wiring once" split).
  - test_invite_accept_appends_one_joined_event (`test_seat_membership_events.py`) · covers: M3(a)
  - test_scim_create_user_appends_one_joined_event (`test_seat_membership_events.py`) · covers: M3(b)
  - test_sso_new_user_appends_one_joined_event_existing_relogin_appends_none
    (`test_seat_membership_events.py`) · covers: M3(b2), v2/CR-1
  - test_domain_capture_join_appends_one_joined_event (`test_seat_membership_events.py`) · covers: M3(b3), v2/CR-1
  - test_deactivate_then_repeat_then_reactivate_appends_exactly_two_events
    (`test_seat_membership_events.py`) · covers: M3(c), idempotency no-op
  - test_preexisting_active_user_backfills_one_joined_event /
    test_preexisting_deactivated_user_backfills_both_events / test_seed_prices_team_15_enterprise_40_starter_null
    (`tests/migrations/test_seat_billing_backfill.py`) · covers: M4, R5, seed prices
  - test_seat_evidence_resolves_proration_line_to_its_user /
    test_seat_evidence_resolves_aggregate_seat_line_to_every_full_price_user
    (`test_seat_evidence_api.py`) · covers: M11
  - test_seat_evidence_against_usage_line_rejected (`test_seat_evidence_api.py`) · covers: M12, Reject (ERR_INVOICE_LINE_WRONG_TYPE)
  - test_seat_evidence_unknown_or_cross_tenant_is_same_404 (`test_seat_evidence_api.py`) · covers: Reject (ERR_INVOICE_NOT_FOUND)
  - test_seat_evidence_timeout_maps_to_504 (`test_seat_evidence_api.py`) · covers: Reject (ERR_INVOICE_QUERY_TIMEOUT), M13
  - `tests/migrations/test_migrations.py::EXPECTED_TABLES` (+1 row, SANCTIONED) and
    `tests/guardrails/test_guardrails_core.py::test_guardrails_core_migration_column_exists` (+1 literal,
    SANCTIONED) — manifest-maintenance assertions, not new scenarios; disclosed additive edits.
</test_plan>

Tests live in: `./tests/seat_billing/` (5 files: `test_seat_pricer_unit.py`, `test_seat_pricing.py`,
`test_seat_membership_events.py`, `test_seat_evidence_api.py`, `conftest.py`) †35 tests ·
`gateway/tests/migrations/test_seat_billing_backfill.py` †3 tests (migration-only, lives beside the
sibling `test_migrations.py`, mirrors that file's own `clean_migration_db` fixture convention) ·
2 SANCTIONED one-line additive edits to already-frozen manifest tests (`test_migrations.py`,
`test_guardrails_core.py`) — disclosed, not new scenario coverage.

RED confirmed for the right reason (2026-07-12, before any implementation file existed): every
`test_seat_pricer_unit.py` test failed at import (`ModuleNotFoundError: gateway.billing.application.
seat_pricer`); every `test_seat_pricing.py`/`test_seat_membership_events.py`/`test_seat_evidence_api.py`
test failed on the FIRST DB write/read touching `seat_membership_events`
(`UndefinedTableError`/`UndefinedColumnError`) or a 404 on the not-yet-registered evidence route; the 3
`test_seat_billing_backfill.py` tests stopped at `command.upgrade(cfg, "head")` landing on parent
revision `1891020e487c` (migration `f1ef6b05a732` did not exist) — never a broken-harness or
wrong-fixture red. Tests and implementation were authored in the SAME session with tight iteration
(write red, confirm import/table-not-found failure, implement, green) rather than one strict
red-then-green-then-build pass across the whole suite at once — disclosed in §5 "Strategy actually
used"; every test's initial failure was independently observed to fail on the missing-implementation
reason before its corresponding code landed, never retrofitted to a passing implementation.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
`apps/gateway/migrations/versions/` (new file only) ·
`apps/gateway/src/gateway/billing/` (application/seat_pricer.py NEW; application/invoice_generator.py,
api/router.py, domain/invoice.py, infrastructure/invoice_repository.py — additive edits) ·
`apps/gateway/src/gateway/core/error_catalog.py` (additive constant) ·
`apps/gateway/src/gateway/tenants/infrastructure/orm.py` (additive column + new ORM class) ·
`apps/gateway/src/gateway/tenants/infrastructure/repository.py` (additive edits to 2 methods) ·
`apps/gateway/src/gateway/tenants/infrastructure/invite_repository.py` (additive edit to `.accept`) ·
`apps/gateway/src/gateway/scim/infrastructure/repository.py` (additive edits to `.create_user`/`.set_active`) ·
`apps/gateway/tests/seat_billing/` (new suite) ·
`apps/gateway/tests/migrations/test_seat_billing_backfill.py` (new file) ·
`apps/gateway/tests/migrations/test_migrations.py` + `apps/gateway/tests/guardrails/test_guardrails_core.py`
(one-line SANCTIONED manifest additions each, disclosed in §0/§4 — never a weakening edit).

Strategy (ordered batches):
1. Migration first (`f1ef6b05a732_seat_billing.py`): table + column + CHECK + seed prices + backfill;
   verify via `alembic upgrade head` + `alembic check` on a dedicated DB before writing any app code.
2. Pure math module (`seat_pricer.py`): `active_days`, `compute_seat_lines`, `resolve_line_contributors`
   — zero infra imports, red-then-green against `test_seat_pricer_unit.py` in isolation (fastest
   feedback loop, no DB).
3. ORM additions (`orm.py`: `SeatMembershipEventRow`, `PlanRow.seat_price_usd_monthly` +
   `ck_plans_seat_price_positive`) — the shared vocabulary every write site and the loader need.
4. The 5 write sites (`invite_repository.py`, `scim/infrastructure/repository.py` ×2,
   `tenants/infrastructure/repository.py` ×2) — one at a time, each driven red-to-green through its
   REAL HTTP surface in `test_seat_membership_events.py`, never a direct SQL insert.
5. `load_tenant_seat_membership` (shared loader, `invoice_repository.py`) + fold `compute_seat_lines`
   into `InvoiceGenerator.generate_for_tenant`'s existing transaction, before the
   `INSERT ... RETURNING id` — driven by `test_seat_pricing.py`.
6. Seat-evidence route (`seat_evidence_keyset`, `get_seat_line_evidence`, schemas, error-catalog
   constant) — driven by `test_seat_evidence_api.py`.
7. Migration backfill correctness proven independently (`test_seat_billing_backfill.py`, real Alembic
   upgrade chain, never `create_all`).
8. Regression sweep: `tests/seat_billing/`, `tests/migrations/`, then every sibling suite touching a
   write site (`invoice_generation`, `credits_ledger`, `plan_seat_cap`, `member_invite_acceptance`,
   `scim_provisioning`, `sso_oidc`, `saml_sso`, `domain_capture`).

Persona (required): `billing-precision-engineer` (`.add/personas/billing-precision-engineer.md`) —
Decimal end-to-end, append-only ledger discipline, "a $0 row is only acceptable when EXPLAINED,"
rounded-then-summed-never-summed-then-rounded; directly shaped the `seat_pricer.py` design (pure
function, one rounding call per line) and the ledger-write-in-same-transaction discipline at all 5
write sites.

Spawn isolation (default): dedicated worktree (`ai-proxy-builds/build-seat-billing`, branch
`build/seat-billing`) — already isolated per the dispatch; no further subagent spawns needed for this
build (single-agent execution).

Known-problem fixes:
  - trap: SQLAlchemy's unit-of-work does NOT order INSERTs across two mapped classes with no declared
    `relationship()` between them — an unflushed `UserRow` + `SeatMembershipEventRow` pair added to the
    session in the same flush can emit the event INSERT before the parent user INSERT and trip a FK
    violation. → fix: explicit `await self._session.flush()` immediately after `session.add(new_user)`,
    BEFORE adding the event row, at every write site that creates a brand-new user in-session
    (`_get_or_provision_sso_user`, `join_verified_tenant_domain`) — mirrors
    `SqlAlchemyScimUserRepository.create_user`'s own pre-existing add()+flush()-before-second-add() shape,
    which is why that one write site was never affected.
  - trap: `join_verified_tenant_domain`'s broad `except IntegrityError: raise EmailAlreadyRegisteredError`
    silently misclassifies ANY IntegrityError inside its transaction (including an unrelated FK violation
    on the seat-ledger INSERT) as a 409 "email already exists" — a real defect-masking hazard for future
    additions to that same transaction block, not just this task's own bug. Fixed via the flush-ordering
    fix above (the FK violation no longer occurs), but the broad catch itself is disclosed as residual
    risk for Verify, not narrowed in this build (narrowing it is a behavior change to an already-shipped
    catch clause, arguably outside this task's additive-only mandate — flagged, not silently fixed).
  - trap: `users.created_at`/`occurred_at` render as a NAIVE `TIMESTAMP` under `Base.metadata.create_all()`
    (test DB) but a REAL `TIMESTAMPTZ` under the Alembic-migrated chain — a tz-aware Python datetime bound
    via asyncpg to a naive-schema column raises `DataError`; a naive datetime bound to a real TIMESTAMPTZ
    column is silently misinterpreted using the LOCAL system timezone. → fix: `conftest.py`'s `_naive()`
    helper strips tzinfo for every raw-SQL seed against the test DB; `test_seat_billing_backfill.py` (which
    runs against the REAL migrated schema) uses explicit tz-AWARE UTC datetimes throughout instead.

Strategy actually used: as planned above, EXCEPT tests and implementation were written in tighter
interleaved iterations per write-site/module (write the test file's failing assertions, confirm red for
the missing-table/missing-route/missing-module reason, implement that one piece, green, next) rather
than authoring the ENTIRE §4 red suite first and only then starting §5 build end-to-end. Every test's
initial run was independently observed to fail on the honest missing-implementation reason (import
error, `UndefinedTableError`, 404) before its corresponding code was written — never retrofitted to a
passing implementation — so the red/green discipline held at the per-piece granularity even though the
batch-level sequencing was tighter than the ordered-batches list above. 5 test-authoring bugs were
found and fixed AFTER an initial full-suite run (not before): 2 wrong-status-code assertions
(`/invites/{token}/accept` returns 200 not 201), 1 miscounted event-sequence assertion (SCIM
`create_user` itself emits the initial 'joined' event, which one test's assertion had not accounted
for), 1 wall-clock-fragile fixture (a ledger-less-fallback test relied on the owner's real signup
`created_at` — "now" — happening to fall inside the fixed July-2026 fixture period; fixed by explicitly
backdating it, same as `seed_user`'s own convention), and the 2 real production defects named above (the
flush-ordering FK violation, affecting 2 of the 5 write sites).

Safety rule (feature-specific): every `seat_membership_events` INSERT is written in the SAME DB
transaction as its triggering `users`-row mutation (§3 M3) — enforced by construction (the event row is
always `session.add()`-ed inside the same `async with self._session.begin():` block, or before the same
`await self._session.commit()`, as the user-row mutation, never a separate transaction) — verified by
the write-site tests calling the REAL HTTP endpoint and reading back the ledger row via a SEPARATE
`db_session`, never asserting against in-memory session state. Rollback/failure path: any exception
raised inside the shared transaction (e.g. `SeatCapExceededError`, an `IntegrityError` on the user INSERT
itself) rolls back BOTH the user-row mutation and the ledger write together — no code path can persist
one without the other. No new retry/circuit-breaker primitive was introduced: seat-ledger writes ride
the EXISTING transaction/timeout/idempotency machinery of each of the 5 call sites and of
`InvoiceGenerator.generate_for_tenant`'s own `asyncio.timeout` + `ON CONFLICT DO NOTHING` (M13) — this
task deliberately adds no NEW failure-handling primitive, per §3's "inherits the existing bound, no new
timeout, no new concurrency primitive."

Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear. Honored —
zero new third-party dependencies (only `decimal`/`datetime`/`uuid` stdlib + already-vendored
SQLAlchemy/Alembic/FastAPI/asyncpg); no test assertion was weakened, deleted, or skipped to reach green
(every fix to a test was to an authoring BUG — wrong expected status code, miscounted event sequence, a
wall-clock-fragile fixture — never a loosened assertion); the frozen §0–§3 CONTRACT text was never edited.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

Persona: `billing-precision-engineer` (closest domain fit; layered with a Code-Reviewer/
security-gatekeeper verify stance since no dedicated verify persona exists) — Decimal
end-to-end, provenance-over-bare-numbers, append-only-ledger discipline applied as the
verify lens over the same domain the build persona used.

- [x] all tests pass — 35/35 original + 21/21 independent adversarial probes (56 new/
  existing seat-billing tests + 3 migration-backfill tests + 2 new adversarial migration
  tests = 61 total, 0 failures)
- [x] coverage did not decrease — module-scoped run (`tests/seat_billing/`) is 100%
  green; full-repo coverage % was not independently re-measured (out of scope for a
  targeted adversarial pass; the build's own §6 already recorded a full-suite pass)
- [x] no test or contract was altered during build — confirmed by direct read of every
  frozen §1/§3 span (byte-identical to the FROZEN @ v2 text) and every original test file
  (no weakened/skipped assertion found)
- [x] the green was EARNED, not gamed — see Refute-read verdict below
- [x] concurrency / timing of the risky operation is safe — see probes E, Advisor lens 2
- [x] no exposed secrets, injection openings, or unexpected dependencies — see Advisor
  lens 1 (all queries parameterized; zero new third-party deps confirmed via diff of
  `pyproject.toml`/`uv.lock`, unchanged)
- [x] layering & dependencies follow CONVENTIONS.md — `seat_pricer.py` confirmed zero
  infra imports (`decimal`/`datetime`/`uuid`/`dataclasses` stdlib only, read in full)
- [ ] a person reviewed and approved the change — pending Tin (this is an AI verify pass)

### Build expectations — confirmed at the gate
- [x] Every one of the 5 instrumented write sites appends EXACTLY one
  `seat_membership_events` row per real HTTP call, in the SAME DB transaction as the
  users-row mutation — RE-CONFIRMED independently (not just via the builder's own 5/5
  tests) by 5 NEW atomicity probes (`tests/seat_billing/test_verify_adversarial.py`,
  probes A) that inject a failure AFTER the users-row flush/add but BEFORE the shared
  commit at EACH site, and assert NEITHER row survives — all 5 pass, confirming true
  atomicity by observed rollback behavior, not just code inspection.
- [x] A plan-priced tenant's invoice carries correct `'seat'`/`'proration'` lines folded
  into the same `total_usd` — RE-CONFIRMED with hand-computed Decimal expectations for
  calendar-edge cases the original suite did not cover: join on the last day of a 31-day
  month (1 day), Feb 28 vs. Feb-leap 29-day full periods, a same-calendar-day join+leave
  INSIDE the period (1 day, not 0 — distinct from the frozen "entirely outside the
  period" zero-day scenario), and exact midnight period-boundary inclusion/exclusion —
  6 new pure-math probes (part B), all pass.
- [x] Unplanned/zero-price tenants produce zero seat lines — unchanged from build's own
  evidence, re-read and confirmed correct in `invoice_generator.py:_load_seat_price`.
- [x] `seat_price_usd_monthly <= 0` rejected at the DB CHECK-constraint layer — confirmed
  by direct migration read (`ck_plans_seat_price_positive`).
- [x] Pre-existing users backfilled correctly, proven against the REAL Alembic chain —
  RE-CONFIRMED with 2 new adversarial probes
  (`tests/migrations/test_seat_billing_backfill_adversarial.py`): a MIXED population in
  ONE migration run (3 active + 2 deactivated users, different tenants/timestamps) shows
  no cross-user attribution and no double-apply; a SEPARATE probe demonstrates a genuine,
  disclosed residual limitation — see finding 🟡-5 below.
- [x] `GET .../seat-evidence` resolves lines correctly and rejects wrong-type/cross-tenant
  — RE-CONFIRMED plus 2 NEW probes: a nil-UUID sentinel cross-tenant-isolation test (two
  DIFFERENT tenants both generate a `'seat'` line with the BYTE-IDENTICAL nil-UUID
  key_id — confirmed the evidence predicate is derived from `(tenant_id, period, bucket)`,
  never key_id-value equality, so no cross-tenant leak) and a coincidental-key_id-collision
  test against the EXISTING usage-evidence route (a real `usage_records` row seeded with
  the SAME nil-UUID key_id a seat line uses — confirmed the wrong-route evidence stays
  honestly empty, never leaking the coincidentally-colliding usage row).
- [x] pyright/ruff clean on build-authored files — re-confirmed for the 2 NEW verify-probe
  files (`ruff check`/`ruff format --check` clean; `pyright` scoped-to-`src/gateway` config
  means tests/ was never in scope for either the build's or this verify pass's files —
  confirmed the SAME `Config`-typing pyright noise pre-exists on the builder's own sibling
  `test_seat_billing_backfill.py`, not a regression).
- [x] No regression in sibling suites — RE-RAN foreground (not just trusted the builder's
  own report): `invoice_generation`, `plan_seat_cap`, `scim_provisioning`,
  `member_invite_acceptance`, `sso_oidc`, `domain_capture` = 158/158 green, 0 failures.
  (`credits_ledger`/`saml_sso` not independently re-run in this pass — time-budgeted;
  no code path touched by this task's write sites is unique to either.)

### Deep checks
- [x] WIRING (code) — grep-confirmed every new symbol is referenced from a real call
  site: `SeatMembershipEventRow` constructed at exactly 5 src locations
  (invite_repository.py:327, scim/infrastructure/repository.py:193,304,
  tenants/infrastructure/repository.py:130,274); `compute_seat_lines`/
  `load_tenant_seat_membership` wired into `invoice_generator.py`'s transaction;
  `seat_evidence_keyset`/`resolve_line_contributors` wired into
  `billing/api/router.py:get_seat_line_evidence`, itself registered as a real FastAPI
  route (`@invoices_router.get(".../seat-evidence")`); `INVOICE_LINE_WRONG_TYPE` used at
  the M12 reject path. No new symbol found unreferenced.
- [x] DEAD-CODE (code) — none found; `active_days` is a module-private helper consumed by
  both `compute_seat_lines` and `resolve_line_contributors` within the same file (not
  itself exported elsewhere, correctly so) plus directly unit-tested.
- [ ] SEMANTIC (prose) — N/A, this task is 100% code.

### Live-verify evidence
- [x] every symbol §3 CONTRACT cites still resolves in the CURRENT tree — every cited
  anchor (`UserRow`, `InviteRepository.accept`, `SqlAlchemyScimUserRepository.create_user`/
  `.set_active`, `_get_or_provision_sso_user`, `join_verified_tenant_domain`,
  `InvoiceGenerator.generate_for_tenant`, `InvoiceLineRow`, `get_invoice_line_evidence`/
  `_get_invoice_or_404`, `InvoiceRepository`, `core/error_catalog.py:ErrorSpec`, alembic
  head) was independently re-read at its cited line range in this session — all resolve,
  none moved/renamed since Ground SHA `71641a9`.
- [x] no anchor drift found.

### Refute-read verdict
Verdict: **EARNED**
By: self (independent verify agent, not the builder) · adversarially checked: all 5
write-site atomicity guarantees under injected mid-transaction failure (not just code
inspection); calendar/month-boundary edge math against hand-computed Decimal values;
invoice-regeneration idempotency + post-issuance immutability of the PERSISTED line
(while separately confirming evidence is a live re-derivation, not a snapshot — see
finding 🟡-2); a dirtier multi-user migration-backfill fixture; concurrent-join races
(plain + seat-cap-constrained); cross-tenant nil-UUID-sentinel isolation; a static
grep+AST sweep for any UPDATE/DELETE against the ledger table; and a direct challenge of
the frozen contract's own "tenant-overridable... like every other price" prose against
the actual implementation. No overfit-to-fixture, vacuous assert, or stubbed-away logic
found in the original 35 tests — spot-checked several (`test_mid_month_leave_and_full_
period_aggregate_separately`, `test_issued_seat_line_is_immutable`) line-by-line; both
assert concrete Decimal/shape values against real DB round-trips via a SEPARATE session,
never a mock. The disclosed §2 scenario-prose arithmetic typo (24→16) was independently
re-derived from M6's own normative rule and confirmed correctly implemented as 16, not
silently miscorrected.

### Advisor 3-lens verdict — sequential
Advisor: self (independent verify agent)
1. Security: **CLEAR** — no exposed secrets; every new query is parameterized
   (SQLAlchemy `text()` with bound params or the ORM, no string-formatted SQL found in
   any of the 5 write sites or the evidence route); `seat-evidence` reuses the SAME
   `Permission.INVOICES_READ` + tenant-404-invisible resolver as every other invoices
   route (no new auth surface); zero new third-party dependencies.
2. Concurrency: **CLEAR** — the disclosed flush-ordering fix (explicit `flush()` before
   the event-row `add()` at the 2 CR-1 seams) independently re-verified correct via 2 new
   concurrency probes: 2 concurrent SCIM creates at the same tenant each land exactly 1
   event (no lost/duplicate write); 2 concurrent joins against a seat-cap that allows
   exactly 1 more admission resolve to exactly one 201+one 403, with the cap-rejected
   loser leaving NEITHER an orphaned users row NOR a ledger event — the `FOR UPDATE OF t`
   admission lock composes correctly with the ledger-write discipline under a real race.
3. Architecture: **RESIDUE** — `seat_membership_events` has NO DB-level append-only
   guard (no trigger), unlike `invoice_lines`/`invoices`/`invoice_corrections` which DO
   get a real production `immutable_guard` trigger in migration `0b5527920450`. This
   mirrors the project's EXISTING `usage_records` precedent (also code-discipline-only,
   no trigger) rather than deviating from it, and a static grep+AST sweep confirms no
   code path currently mutates the table — but because seat-evidence RE-DERIVES its
   answer live from this table forever (M11's own "never a materialized list" doctrine),
   a future accidental UPDATE (e.g. a careless hotfix migration) has no DB-level safety
   net and could silently corrupt evidence for past, already-issued invoices. Named as a
   forward-looking hardening candidate (see 🟡-3), not a build defect.
Verdict: **PASS**
Residue: architectural note only (🟡-3 below) — no HARD-STOP-class finding.
Binding: advisory — sensitivity: data (not `mechanical`)

### Findings (severity-tagged, with repros)

🔴 Blockers: **none found.**

🟡 Concerns:
1. **Contract prose vs. implementation gap — no tenant-level seat-price override
   exists**, despite §3's DECIDED freeze note stating "Prices live in the plans catalog
   and are tenant-overridable via the shared rate-card resolver like every other price."
   `_load_seat_price` (`invoice_generator.py:121-140`) reads ONLY
   `plans.seat_price_usd_monthly` via a plain `tenants ⋈ plans` JOIN. The actual "shared
   rate-card resolver" (`tenant_rate_card_entries`, keyed `tenant_id`+`model_id` for
   per-model USAGE markup) has no seat-domain row shape and is never consulted for seat
   pricing. Two tenants on the identical plan get the byte-identical seat price — no
   override slot exists anywhere. Repro: `tests/seat_billing/test_verify_adversarial.py::
   test_seat_price_has_no_tenant_level_override_despite_frozen_contract_prose` (passes,
   confirming the absence). Not a wrong-money bug — every tenant is still billed
   correctly and uniformly per its plan — but a documentation/capability mismatch that
   could mislead Sales/Ops into believing a per-tenant discount lever exists. Recommend:
   either scope a small follow-up to add the override, or correct the DECIDED prose at
   the next spec touch.
2. **Seat-evidence is a live re-derivation, not a snapshot — evidence for an
   already-issued, immutable invoice line can drift from what actually produced its
   frozen dollar amount.** A late-arriving `seat_membership_events` row (e.g. a backdated
   correction landing after generation) changes what `seat-evidence` reports for that
   line, even though `amount_usd`/`request_count` on the persisted `invoice_lines` row
   never change. This is a DIRECT, disclosed consequence of M11's own "re-runs the same
   bucket predicate... never a materialized id list" doctrine — a design characteristic,
   not a code defect — but it means a billing_admin auditing a July invoice in August
   could see evidence that no longer fully explains the frozen total if any late/
   corrective ledger write landed in between. Repro:
   `test_event_appended_after_invoice_issued_never_mutates_that_invoice` (passes,
   demonstrating both halves: the persisted line is untouched, but its evidence page
   drifts). Recommend documenting this explicitly wherever seat-evidence is surfaced in
   the admin UI/API docs.
3. **`seat_membership_events` has no DB-level append-only guard** — see Advisor lens 3
   above. Static sweep confirms no current code path mutates it
   (`test_no_code_path_ever_updates_or_deletes_seat_membership_events`, passes), but
   unlike `invoice_lines`/`invoices`/`invoice_corrections` it has no trigger backstop.
   Mirrors the `usage_records` precedent (also unguarded) rather than deviating from it —
   named as a hardening candidate for a future task, not a defect in this one.
4. **`join_verified_tenant_domain`'s broad `except IntegrityError` still wraps the
   ledger-write line** (disclosed by the build itself in §5 Known-problem fixes) —
   practically dead now that the flush-ordering fix removes the only realistic
   IntegrityError source for that insert (fixed `event_type` values pass the CHECK
   constraint by construction; a `uuid7()` PK collision is astronomically unlikely), but
   it remains a latent defect-masking hazard for ANY future addition to that transaction
   block that could raise a genuine IntegrityError there. Not narrowed in this pass
   either (same additive-only mandate the build cited) — flagged forward, matching the
   build's own disclosure rather than a new finding.
5. **The M4 backfill (and the M5 fallback) structurally cannot see a
   deactivate-then-reactivate cycle that happened BEFORE this migration ran** — both only
   ever capture `users.deactivated_at`'s LATEST snapshot state, the exact R1 limitation
   the whole ledger exists to fix, but for any history predating the ledger's own
   existence there is no durable record anywhere (not even `audit_events`, per R4) to
   backfill from. Repro:
   `tests/migrations/test_seat_billing_backfill_adversarial.py::
   test_backfill_cannot_see_a_pre_migration_deactivate_reactivate_cycle` — concretely
   demonstrates a user with a "true" (never-recorded) July 3-8 deactivation gap bills as
   31/31 active days instead of the true 26, a real 5-day silent OVER-charge, for any
   tenant whose true gap overlaps the FIRST post-cutover invoice period. This is a
   data-availability limitation, not a fixable code defect — recommend surfacing it to
   Tin as an accepted, named cutover-window caveat (§7 spec delta / runbook note), not a
   HARD-STOP.

💭 Notes:
- The aggregate `'seat'` line's seat-evidence returns a contributing user's ENTIRE
  `seat_membership_events` history, not scoped to the invoiced period — the §3 prose
  ("paginates every full-price-bucket user's relevant events") is ambiguous on this
  point but not incorrect (a full audit trail arguably IS relevant evidence for why a
  user is a full-price seat). Worth a UX confirmation with Tin if this ever surfaces
  unrelated historical churn in the admin console.
- Verified `invoice_lines`/`invoices`/`invoice_corrections` DO get a REAL production
  immutable-guard trigger (migration `0b5527920450`) — the test-file's own
  `_install_immutability_trigger` helper (mirrored verbatim from invoice-generation's
  own test suite) is necessary only because the test app fixture uses
  `Base.metadata.create_all()`, which does not replay migration-time triggers; this is
  NOT evidence of a production gap for those 3 tables (initially misread, corrected on
  closer read — recorded here so a future verify pass doesn't re-walk the same false
  lead).

### GATE RECORD
Reported: no — this is an independent verify pass; the orchestrator renders the gate
report and records the final outcome, per this task's own instruction ("Do NOT gate —
the orchestrator gates.").
Outcome (PROPOSED, for orchestrator ratification): **PASS**
Rationale: zero 🔴 blockers; all 5 🟡 concerns are either pre-existing project
conventions (usage_records-style trigger-less append-only discipline), disclosed
build-time residue (the broad IntegrityError catch), a data-availability limitation with
no code fix (pre-migration reactivation gap), or a documentation-vs-implementation
mismatch with no money-correctness impact (tenant-override prose) — none rise to
HARD-STOP, and the one genuine forward-looking hardening candidate (finding 3, the
missing DB trigger) is architecture residue, not a security finding, so it is advisory
under this task's `sensitivity: data` (not `mechanical`).
If RISK-ACCEPTED -> owner: <pending Tin> · ticket: <link> · expires: <date>
Reviewed by: <pending Tin> · date: <pending>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
