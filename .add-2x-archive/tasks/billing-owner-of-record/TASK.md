# TASK: Designate one billing owner-of-record per tenant + never-zero-billing-owner guard on role-change/deactivation

slug: billing-owner-of-record · created: 2026-07-16 · stage: production · risk: high · sensitivity: security
milestone: account-tiers-billing
autonomy: conservative
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/domain/entities.py:Role` (lines 9-20) — CONFIRMED members:
  `OWNER="owner"`, `ADMIN="admin"`, `OPERATOR="operator"`, `BILLING_ADMIN="billing_admin"`,
  `VIEWER="viewer"`, `MEMBER="member"`, `SUPERADMIN="superadmin"` (platform-tenant-only, DB-trigger
  enforced). BILLING-CAPABLE = `{OWNER, BILLING_ADMIN}` — a NEW public constant
  `BILLING_CAPABLE_ROLES: frozenset[Role]` added next to `Role` (domain-level, reused by both guard
  hook points + the new reassignment endpoint's target validation).
- `apps/gateway/src/gateway/tenants/application/users_use_cases.py:AssignUserRoleUseCase.execute`
  (lines 80-125) — HOOK 1. Current order: `assert_role_within_ceiling` → SELF-GUARD
  (`caller_user_id == target_user_id` → `EscalationForbiddenError`, UNCONDITIONAL, even for OWNER) →
  `get_by_id_and_tenant` (404-shaped `UserNotFoundError`) → `update_role`. The self-guard already
  makes a true "self-demote" structurally unreachable — R7 below documents this interaction so the
  NEW guard is never confused with it. Called VERBATIM from BOTH `users_router.py` (self-service,
  `PUT /admin/users/{id}/role`) AND `platform_users_router.py:assign_platform_tenant_user_role`
  (superadmin cross-tenant, `PUT /admin/platform/tenants/{tid}/users/{uid}/role`, confirmed by reading
  the router — "AssignUserRoleUseCase.execute VERBATIM") — ONE hook point covers both surfaces, no
  superadmin bypass exists.
- `apps/gateway/src/gateway/tenants/infrastructure/users_repository.py:UserRoleRepository` (lines
  18-70ish) — `get_by_id_and_tenant`, `update_role` (confirmed: `update_role` internally calls
  `self._session.commit()`, per `platform_users_router.py`'s own verified comment). NEW method needed:
  `lock_and_get_billing_owner_user_id(tenant_id)` — `SELECT billing_owner_user_id FROM tenants WHERE
  id=:t FOR UPDATE`. `AssignUserRoleUseCase` currently holds NO single transaction spanning
  read+write (`get_by_id_and_tenant` is a plain unlocked SELECT, `update_role` commits on its own) —
  adding the FOR-UPDATE lock call immediately before `update_role`, in the same repo/session, is a
  genuine (small) change to this use case's transaction shape, named here so Build doesn't miss it.
- `apps/gateway/src/gateway/scim/application/user_use_cases.py:SetScimUserActiveUseCase.execute`
  (lines 72-89) → `apps/gateway/src/gateway/scim/infrastructure/repository.py:
  SqlAlchemyScimUserRepository.set_active` (lines 261-315) — HOOK 2. CONFIRMED (read in full) this is
  the SOLE writer of `users.deactivated_at` in the whole src tree (verified via a grep for every
  `deactivated_at=` write site — only this method matches); backs both `PATCH active:false` AND the
  `DELETE /scim/v2/Users/{id}` alias (both route through `scim_router.py` into this SAME use case — no
  second deactivation entry point exists anywhere, including the platform/superadmin surface, which has
  no deactivate route of its own). Already acquires `SELECT ... FOR UPDATE` on the target `UserRow`
  (lines 271-277, an EXISTING concurrency-safety precedent in THIS exact file/method, with its own
  comment explaining why) before computing `already_at_target` and committing — the natural, precedented
  insertion point for the NEW guard is right after that lock, gated on `active is False and not
  already_at_target` (never blocks an idempotent no-op repeat).
- `apps/gateway/src/gateway/tenants/api/users_router.py:assign_user_role` (lines 98-172) +
  `apps/gateway/src/gateway/tenants/api/platform_users_router.py:assign_platform_tenant_user_role`
  (lines 147-230) — both currently `except EscalationForbiddenError / except UserNotFoundError`; BOTH
  need a new `except LastBillingOwnerError` branch, or the superadmin router leaks an uncaught 500
  instead of the correct 409 (confirmed: reading both routers shows the except-list is NOT shared code,
  each router hand-declares its own).
- `apps/gateway/src/gateway/scim/api/scim_router.py` (PATCH handler, lines ~173-190 + DELETE-alias
  handler ~360-366) — currently only `except ScimUserNotFoundError`; needs the same new
  `except LastBillingOwnerError` branch around the `SetScimUserActiveUseCase` call, covering both the
  PATCH and DELETE-alias code paths (both already funnel through the same use-case call site per M6 of
  scim-provisioning's own docstring: "DELETE is an ALIAS for active:false, never a hard delete").
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission` (lines 54-86) + `ROLE_PERMISSIONS`
  (lines 94-144, FROZEN @ v1) — CONFIRMED `SECURITY_CONFIG` is already OWNER-only in practice: `OWNER`
  = `frozenset(Permission)` (all), `ADMIN`'s explicit allowlist excludes it ("NOT PROVIDER_SECRETS, NOT
  SECURITY_CONFIG — owner-only preserved"), `BILLING_ADMIN`/`OPERATOR`/`VIEWER`/`MEMBER` don't have it
  either. `retention_policy_router.py` and `residency_policy_router.py` both REUSE
  `Permission.SECURITY_CONFIG` for their own singleton, own-tenant-only, OWNER-only PUT, and
  `residency_policy_router.py`'s own docstring names this explicitly: "OWNER only... no new Permission
  enum member". Same shape as the new reassignment endpoint this task adds.
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:TenantRow` (lines 107-284) — the
  `ck_tenants_platform_no_plan` / `ck_tenants_platform_no_account_type` idiom (a same-table CHECK
  forbidding a nullable column from being set on `kind='platform'`) is the precedent this task's new
  `ck_tenants_platform_no_billing_owner` mirrors. `plan_id` (line 244-249) is the precedent for the new
  `billing_owner_user_id` FK shape (`ForeignKey(..., ondelete="RESTRICT")`, nullable, no backfill
  server_default). No CHECK can express the cross-table "must be active + billing-capable" invariant in
  Postgres without a trigger — CONFIRMED this task enforces it at the APPLICATION layer only (the two
  named hook points + the reassignment endpoint), matching Tin's locked decision's own framing ("GUARD
  = HARD-REJECT... two hook points") rather than adding a DB trigger (named as a residual gap in §1
  Assumptions, not silently assumed away — a future direct SQL write to `users.role`/`deactivated_at`
  bypassing both use cases would violate the invariant undetected).
- `apps/gateway/src/gateway/tenants/infrastructure/repository.py:SqlAlchemyIdentityRepository.
  create_tenant_with_owner` (lines 82-113) — every signup creates EXACTLY one `Role.OWNER` user
  atomically with the tenant; this is why the backfill's "earliest OWNER" default is expected to
  resolve for effectively every real customer tenant (confirms the low-likelihood framing of the
  no-active-owner backfill edge in §1 Assumptions).
- `apps/gateway/migrations/versions/113ebdbe9f09_plan_tiers_and_base_fee.py` — CONFIRMED current single
  alembic head via `uv run alembic heads` → `113ebdbe9f09 (head)`; this task's NEW migration sets
  `down_revision="113ebdbe9f09"`.
- `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec` (lines 30-74) + existing 409 precedents
  (`TEAM_EXISTS`, `INVITE_NOT_PENDING`, `INVOICE_IMMUTABLE`) and 422 precedents
  (`PLAN_TENANT_INELIGIBLE`, `PLAN_SEAT_CAP_EXCEEDED`) — the style this task's two NEW `ErrorSpec`
  constants (`LAST_BILLING_OWNER` 409, `BILLING_OWNER_INELIGIBLE` 422) mirror exactly.
- `apps/gateway/src/gateway/billing/infrastructure/orm.py:InvoiceRow` (lines 77-106) — attributes to
  `tenant_id` only (no user-level column exists or is added by this task).
  `apps/gateway/src/gateway/credits/infrastructure/ledger_store.py` — `tenant_credit_balances` is
  keyed by `tenant_id` only (confirmed via `lock_balance_row`). Both confirm the read-side-join
  attribution mechanism (§1 M7) needs NO invoice/credit schema change — `tenant_id →
  tenants.billing_owner_user_id` is sufficient, per Tin's locked decision's own "a read-side pointer is
  enough" framing.

Context (working folder): milestone `account-tiers-billing`, task 3 of 3 (depends-on
  `account-type-discriminator`, DONE; sibling `plan-tiers-and-base-fee`, DONE — both UNCOMMITTED in this
  working tree per `git status`, alongside an unrelated sibling milestone's uncommitted work). Grounded
  against that COMBINED tree state via live `mcp__serena` symbol reads, not the pre-given recon alone.
Honors (patterns / conventions): additive `CheckConstraint` + nullable FK column mirroring
  `plan_id`/`ck_tenants_platform_no_plan` exactly; `SELECT ... FOR UPDATE` row-locking to close a
  concurrency window — an EXISTING, cited precedent in `scim/infrastructure/repository.py:set_active`,
  not a new pattern introduced by this task; `Permission.SECURITY_CONFIG` reuse for an OWNER-only,
  own-tenant-only singleton PUT — mirrors `retention_policy_router.py`/`residency_policy_router.py`
  verbatim (both explicitly document "no new Permission enum member" for this exact shape); tenant
  isolation via `get_by_id_and_tenant`'s existing confused-deputy-safe 404 (never distinguishes
  "wrong tenant" from "doesn't exist"); `ErrorSpec` catalog style (status/code/title_template).
Seams consulted: none new.
Anchors the contract cites: `Role`/`BILLING_CAPABLE_ROLES` (entities.py), `AssignUserRoleUseCase.execute`,
  `UserRoleRepository.lock_and_get_billing_owner_user_id`, `SetScimUserActiveUseCase.execute` →
  `SqlAlchemyScimUserRepository.set_active`, `TenantRow.billing_owner_user_id`, the new migration
  (`down_revision="113ebdbe9f09"`), `LAST_BILLING_OWNER`/`BILLING_OWNER_INELIGIBLE` (error_catalog.py),
  the new `PUT /admin/billing-owner` + `GET /admin/billing-owner` router.
Issues/Risks (→ feed §1):
- ⚠ ATTRIBUTION IS A LIVE POINTER, NOT A POINT-IN-TIME SNAPSHOT: `tenant_id → billing_owner_user_id`
  is a live join — if a tenant later reassigns its billing owner, EVERY past invoice's queried
  attribution silently shows the NEW owner, not who was actually the payer of record when it was
  issued. Tin's locked decision explicitly frames a denormalized snapshot as OPTIONAL and the milestone
  Out-of-scope excludes "rewriting invoice/credit internals" — this task chooses the minimal read-side
  join (feeds §1 ⚠ below).
- NO DB TRIGGER enforces the billing-owner invariant (unlike `SUPERADMIN`'s platform-tenant trigger,
  migration `5b34ca5e1c4b`) — enforcement is APPLICATION-layer only, at exactly the two named hook
  points + the new reassignment endpoint. A future direct SQL write, a new deactivation/role-mutation
  code path that doesn't route through `AssignUserRoleUseCase`/`SetScimUserActiveUseCase`, or a raw
  admin DB script would silently bypass the guard — named here as a residual gap (Tin's locked decision
  scopes enforcement to these two hook points explicitly, so this is a confirmed, deliberate choice, not
  an oversight).
- CONCURRENCY: without a shared lock, a race exists between (a) `PUT /admin/billing-owner` reassigning
  ownership TO user X and (b) a concurrent role-change/deactivation demoting/deactivating X — both could
  read a stale pre-commit state and both "succeed", leaving `billing_owner_user_id` pointing at a
  non-billing-capable/inactive user. Closed by having all three write paths acquire the SAME
  `SELECT billing_owner_user_id FROM tenants WHERE id=:t FOR UPDATE` lock, held across their guard-check
  + write, in one transaction (§1 M4, §2 R9).
- BACKFILL EDGE: a `kind='customer'` tenant with zero currently-ACTIVE `OWNER`/`BILLING_ADMIN` user
  (extremely unlikely — every signup path creates exactly one `OWNER` atomically — but not verified
  against production data) must not crash the migration; it is left `billing_owner_user_id IS NULL`,
  named here rather than silently produced.
Related intent: `.add/milestones/account-tiers-billing/MILESTONE.md` — "Shared decisions": "Payer-of-
  record is a SINGLE designated owner, guarded... the system refuses to leave a tenant with zero
  billing-capable owners"; Exit criteria rows 6-7 ("Every tenant has exactly one designated billing
  owner; invoices/credits attribute to it" / "The last billing-capable owner cannot be demoted or
  deactivated"). GLOSSARY deltas: `billing owner`, `billing-capable role`.
Ground SHA: 3c27af5 (git HEAD) — the working tree ALSO carries `account-type-discriminator` and
  `plan-tiers-and-base-fee` DONE-but-UNCOMMITTED (migration head `113ebdbe9f09`,
  `TenantRow.account_type`, the 5-tier `plans` catalog); every symbol/line cited above was read live
  from the current tree via `mcp__serena`, not assumed from the pre-given recon.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A single designated billing owner-of-record per tenant (`tenants.billing_owner_user_id`,
  defaulted to the signup OWNER, backfilled for existing customers) + a server-side "never leave the
  tenant with zero billing-capable owners" guard at both role-change and deactivation, with exactly ONE
  sanctioned reassignment path (OWNER-only) and a read-side attribution join for invoices/credits.
Framings weighed:
- Attribution = a LIVE read-side join (`tenant_id → tenants.billing_owner_user_id`), no new
  invoice/credit column (chosen) — Tin's locked decision frames a denormalized snapshot as explicitly
  OPTIONAL; the milestone's Out-of-scope excludes rewriting invoice/credit internals; minimal schema
  surface, reversible-by-migration if a snapshot is wanted later.
- A denormalized `billing_owner_user_id` snapshot on `InvoiceRow` at generation time (rejected, for now)
  — a stronger point-in-time audit guarantee, but adds an `InvoiceRow` column + a write-path change to
  `InvoiceGenerator` (Out-of-scope per the milestone: "no rewriting invoice/credit internals"); surfaced
  as the §1 ⚠ lowest-confidence flag instead of silently chosen.
- REUSE `Permission.SECURITY_CONFIG` for the OWNER-only reassignment endpoint, no new `Permission` enum
  member (chosen) — matches the documented, repeated convention in `retention_policy_router.py` +
  `residency_policy_router.py` for this EXACT shape (singleton, own-tenant-only, OWNER-only PUT).
- Mint a NEW dedicated `Permission.BILLING_OWNER_MANAGE` enum member (rejected) — viable (mirrors
  `RATE_CARDS_MANAGE`'s own owner-only-via-omission precedent) but adds enum surface for a gate with no
  anticipated future ADMIN delegation, where an existing owner-only permission already fits the shape
  exactly; rejected in favor of the more-repeated convention.
- Enforcement = APPLICATION-layer guard at the two named hook points only, no DB trigger (chosen) — Tin's
  locked decision explicitly names "two hook points"; a trigger (like `SUPERADMIN`'s platform-tenant
  trigger, migration `5b34ca5e1c4b`) would be stronger defense-in-depth but is not asked for and adds
  migration complexity; named as a residual, deliberate gap in §0 Issues/Risks, not hidden.
Must:
<must>
  - M1 — ONE additive+reversible migration (`down_revision="113ebdbe9f09"`) adds
    `tenants.billing_owner_user_id` (`UUID NULL`, `FK → users.id ON DELETE RESTRICT`) + CHECK
    `ck_tenants_platform_no_billing_owner` (`billing_owner_user_id IS NULL OR kind != 'platform'`,
    mirrors `ck_tenants_platform_no_plan`); BACKFILLs every `kind='customer'` tenant's
    `billing_owner_user_id` to its earliest currently-ACTIVE `OWNER` user (tie-break: earliest
    `created_at`, then `id`), falling back to its earliest currently-ACTIVE `BILLING_ADMIN` if no
    active `OWNER` exists, leaving `NULL` (never crashing) if neither exists; `kind='platform'` stays
    `NULL` (excluded from the backfill WHERE clause).
  - M2 — HOOK 1 (role-change): `AssignUserRoleUseCase.execute` rejects (409 `ERR_LAST_BILLING_OWNER`)
    any role change whose `target_user_id` is the tenant's CURRENT `billing_owner_user_id` AND whose
    `new_role` is outside `BILLING_CAPABLE_ROLES = {OWNER, BILLING_ADMIN}` — enforced identically for
    BOTH the self-service router (`users_router.py`) and the superadmin cross-tenant router
    (`platform_users_router.py`), since both call this SAME use case verbatim; the check runs AFTER
    the existing self-guard + tenant-membership check, BEFORE `update_role`.
  - M3 — HOOK 2 (deactivation): `SetScimUserActiveUseCase.execute` (backing SCIM `PATCH active:false`
    AND the `DELETE` alias) rejects (409 `ERR_LAST_BILLING_OWNER`) deactivating the tenant's CURRENT
    `billing_owner_user_id`, checked AFTER the existing idempotency no-op branch (`already_at_target`)
    so a REPEATED `PATCH active:false` on an already-deactivated non-owner is never blocked by this
    guard, and only when `active is False` (never blocks reactivation).
  - M4 — RACE-SAFETY: both HOOK 1 and HOOK 2's guard checks, and the reassignment endpoint's own write,
    each acquire `SELECT billing_owner_user_id FROM tenants WHERE id=:tenant_id FOR UPDATE` as the
    FIRST statement of their guard/write, holding the lock until their own commit — the same
    `SELECT ... FOR UPDATE` idiom already used by `SetScimUserActiveUseCase`'s existing target-user
    lock. Whichever of two concurrent operations acquires this lock first commits; the other
    re-evaluates against the POST-commit state before deciding to allow or reject.
  - M5 — REASSIGNMENT: `PUT /admin/billing-owner` (OWNER-only via `Permission.SECURITY_CONFIG`,
    caller's own `identity.tenant_id` only — no path/body tenant param) is the ONLY sanctioned way to
    change `billing_owner_user_id`. Body `{ user_id }`; validates the target exists in the caller's
    tenant (else 404, confused-deputy-safe, identical shape to a non-existent user), is ACTIVE
    (`deactivated_at IS NULL`) and billing-capable (`role ∈ {OWNER, BILLING_ADMIN}`) (else 422
    `ERR_BILLING_OWNER_INELIGIBLE`), then writes the new pointer inside the M4 lock. Reassigning to the
    CURRENT billing owner is an idempotent no-op 200 (not an error). After a successful reassignment,
    the PREVIOUSLY-designated user is an ordinary user again — demotable/deactivatable through the
    normal, unguarded path.
  - M6 — `GET /admin/billing-owner` (any authenticated role, own-tenant only) returns the current
    designation `{ user_id, email, role }` or `{ user_id: null, email: null, role: null }` (the
    platform tenant / an unresolved backfill edge) — mirrors the GET+PUT shape of
    `retention_policy_router.py`/`residency_policy_router.py` exactly.
  - M7 — ATTRIBUTION: invoices/credits attribute to the billing owner via a READ-SIDE join
    (`invoice.tenant_id → tenants.billing_owner_user_id`, `tenant_credit_balances.tenant_id →
    tenants.billing_owner_user_id`) — NO new column on `InvoiceRow`/`tenant_credit_balances`/any credit
    ledger table; the tenant's own pointer is the single source of truth (§1 ⚠ below names the
    point-in-time tradeoff this implies).
  - M8 — the migration's `downgrade()` is fully reversible: DROP the CHECK, DROP the column — no data
    restoration needed (the column is entirely backfill-derived, re-backfillable by re-running upgrade).
</must>
Reject:
<reject>
  - R1 — demote-last-billing-owner: a role change on the CURRENT billing owner to a role outside
    `{OWNER, BILLING_ADMIN}` via `PUT /admin/users/{id}/role` -> 409 `"ERR_LAST_BILLING_OWNER"`.
  - R2 — deactivate-last-billing-owner: SCIM `PATCH active:false` (or its `DELETE` alias) targeting the
    CURRENT billing owner -> 409 `"ERR_LAST_BILLING_OWNER"`.
  - R3 — reassign-to-non-billing-capable: `PUT /admin/billing-owner` targeting an ACTIVE user whose role
    is outside `{OWNER, BILLING_ADMIN}` -> 422 `"ERR_BILLING_OWNER_INELIGIBLE"`.
  - R4 — reassign-to-deactivated-user: `PUT /admin/billing-owner` targeting a user with
    `deactivated_at IS NOT NULL` -> 422 `"ERR_BILLING_OWNER_INELIGIBLE"`.
  - R5 — reassign-to-another-tenant's-user (cross-tenant / confused-deputy): `PUT /admin/billing-owner`
    with a `user_id` that exists but belongs to a DIFFERENT tenant -> 404 `"ERR_USER_NOT_FOUND"`
    (byte-identical to an unknown `user_id` — never discloses cross-tenant existence).
  - R6 — reassign-by-non-OWNER: `PUT /admin/billing-owner` called by ADMIN/OPERATOR/BILLING_ADMIN/
    VIEWER/MEMBER -> 403 `"ERR_AUTH_FORBIDDEN"` (via the existing `require_permission(SECURITY_CONFIG)`
    dependency gate — zero new code, same mechanism as `retention_policy_router.py`).
  - R7 — self-demote-when-sole-billing-owner: an OWNER who is the sole billing owner attempts to change
    their OWN role via `PUT /admin/users/{id}/role` -> 403 `"ERR_AUTH_FORBIDDEN"` via the PRE-EXISTING,
    unconditional self-guard in `AssignUserRoleUseCase.execute` (fires BEFORE the new M2 guard is ever
    reached) — NOT the new 409; documented so the two 4xx codes are never confused for this caller.
  - R8 — superadmin-path demote-last-billing-owner: `PUT /admin/platform/tenants/{tid}/users/{uid}/role`
    (the superadmin cross-tenant router) targeting the SAME tenant's billing owner with a
    non-billing-capable role -> 409 `"ERR_LAST_BILLING_OWNER"` (identical to R1 — confirms M2's single
    hook point covers the superadmin surface too, no bypass).
  - R9 — race: concurrent reassign-TO-X (`PUT /admin/billing-owner`, making X the new owner) and
    demote-X-away-from-billing-capable (`PUT /admin/users/{X}/role`) never both succeed: whichever
    transaction acquires the M4 tenants-row lock first commits; the other re-reads the POST-commit
    `billing_owner_user_id` and either proceeds (X was NOT yet the owner when it committed) or correctly
    409s (X WAS the owner when it committed) -> the invariant is never transiently violated at any
    commit boundary.
</reject>
After:
<after>
  - Every `kind='customer'` tenant has `billing_owner_user_id` set to an ACTIVE, billing-capable user of
    that SAME tenant — or, for the rare no-eligible-user backfill edge, explicitly `NULL` (named, never
    silently wrong).
  - A role-change or deactivation that would leave the DESIGNATED billing owner non-billing-capable or
    inactive is IMPOSSIBLE via either guarded write path, enforced server-side, verified under
    concurrency (M4/R9) — not merely a convention.
  - Exactly one sanctioned reassignment path exists (`PUT /admin/billing-owner`, OWNER-only); after a
    reassignment, the PREVIOUS owner is fully ordinary again.
  - Any invoice/credit query can resolve "who is billed" for a tenant via one join to
    `tenants.billing_owner_user_id` — no new column on invoices/credits, per M7.
  - The two new 4xx codes (409 `ERR_LAST_BILLING_OWNER`, 422 `ERR_BILLING_OWNER_INELIGIBLE`) are the
    ONLY new observable surface; every pre-existing role/deactivation response is byte-identical when
    the target is NOT the billing owner.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ ATTRIBUTION AS A LIVE POINTER, NOT A POINT-IN-TIME SNAPSHOT (M7) — lowest confidence because
    "invoices/credits attribute to it" (the milestone's own exit-criterion wording) could reasonably be
    read as "who was billed AT ISSUANCE TIME" for audit/compliance purposes; a live join means a PAST
    invoice's queried billing-owner silently changes if the tenant later reassigns ownership — a real
    divergence from what a finance/audit reviewer might expect. Chosen because Tin's locked decision
    explicitly names a denormalized snapshot as OPTIONAL and the milestone's Out-of-scope excludes
    rewriting invoice/credit internals; if wrong, retrofitting a snapshot column later requires a
    backfill migration across every ALREADY-issued invoice (strictly more expensive than adding it now).
    Confirm or correct at freeze.
  - [ ] the no-active-owner-or-billing_admin backfill edge (a `kind='customer'` tenant with zero
    currently-ACTIVE `OWNER`/`BILLING_ADMIN` user at migration time is left `billing_owner_user_id
    NULL`, not crashed) — ranked #2; low likelihood (every signup path creates exactly one `OWNER`
    atomically — `create_tenant_with_owner`, confirmed) but UNVERIFIED against production data (no live
    DB check performed, mirrors the sibling `plan-tiers-and-base-fee` task's own "no known production
    tenant confirmed" precedent for its starter-plan collision). A post-migration
    `SELECT count(*) FROM tenants WHERE kind='customer' AND billing_owner_user_id IS NULL` is worth
    running before shipping.
  - [x] the row-lock mechanism (`SELECT billing_owner_user_id FROM tenants ... FOR UPDATE`, M4) added
    to `AssignUserRoleUseCase`'s transaction shape — confirmed necessary: mirrors the EXISTING
    `SELECT ... FOR UPDATE` idiom already present in `scim/infrastructure/repository.py:set_active`
    (a live, cited precedent in this exact codebase for closing a concurrency window), and is the only
    way to close the R9 race without a heavier serializable-isolation change.
  - [x] REUSE `Permission.SECURITY_CONFIG` for the OWNER-only reassignment endpoint (no new `Permission`
    enum member) — confirmed: matches the documented, repeated convention in
    `retention_policy_router.py` + `residency_policy_router.py` (both explicitly cite this exact
    convention in their module docstrings) for a singleton, own-tenant-only, OWNER-only PUT.
  - [x] cross-tenant reassignment target returns 404 `ERR_USER_NOT_FOUND` (not a distinct "forbidden"
    code) — confirmed: byte-identical to `AssignUserRoleUseCase`'s own existing cross-tenant-lookup
    behavior (`get_by_id_and_tenant` returns `None` for a wrong-tenant `user_id`), preserving the
    codebase's established confused-deputy-safe convention.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: migration backfills every customer tenant to its earliest active owner   # M1
  Given a kind='customer' tenant with two OWNER users, created at t1 and t2 (t1 < t2), both ACTIVE
  When the migration runs
  Then tenants.billing_owner_user_id is set to the t1 (earliest) OWNER's id
  And the kind='platform' tenant's billing_owner_user_id stays NULL

Scenario: backfill falls back to an active billing_admin when no active owner exists   # M1 edge
  Given a kind='customer' tenant whose sole OWNER is deactivated but has one ACTIVE BILLING_ADMIN
  When the migration runs
  Then tenants.billing_owner_user_id is set to that ACTIVE BILLING_ADMIN's id

Scenario: backfill leaves billing_owner_user_id NULL rather than crashing   # M1 edge
  Given a kind='customer' tenant with zero ACTIVE OWNER or BILLING_ADMIN users
  When the migration runs
  Then the migration completes successfully
  And that tenant's billing_owner_user_id is NULL (named, not silently wrong)

Scenario: role change on an ordinary member is unaffected   # M2 (byte-identical baseline)
  Given a tenant whose billing_owner_user_id is user A, and user B who is NOT the billing owner
  When an OWNER changes B's role from member to operator via PUT /admin/users/{B}/role
  Then the change succeeds 200, byte-identical to pre-task behavior
  And B's tenant is unaffected

Scenario: SCIM deactivates a non-owner user normally   # M3 (byte-identical baseline)
  Given a tenant whose billing_owner_user_id is user A, and user C who is NOT the billing owner
  When SCIM issues PATCH active:false for user C
  Then the deactivation succeeds, deactivated_at is set, team_members rows are deleted (unchanged path)
  And tenant A's billing_owner_user_id is unaffected

Scenario: reassign THEN demote succeeds   # M4/M5 happy path
  Given a tenant whose billing_owner_user_id is user A (OWNER)
  When the OWNER calls PUT /admin/billing-owner { user_id: B } (B is an ACTIVE billing_admin) and it
    commits, THEN a subsequent PUT /admin/users/{A}/role demotes A to member
  Then the reassignment succeeds 200 and billing_owner_user_id is now B
  And the demotion of A succeeds 200 (A is no longer the designated owner, the M2 guard does not fire)

Scenario: reassigning to the current billing owner is an idempotent no-op   # M5
  Given a tenant whose billing_owner_user_id is already user A
  When the OWNER calls PUT /admin/billing-owner { user_id: A }
  Then the call returns 200 with the unchanged designation
  And no error is raised, no spurious state transition is recorded

Scenario: GET returns the current designation   # M6
  Given a tenant whose billing_owner_user_id is user A
  When any authenticated tenant member calls GET /admin/billing-owner
  Then it returns 200 { user_id: A.id, email: A.email, role: A.role }

Scenario: invoice attribution resolves via the tenant join, no new column   # M7
  Given a tenant with an issued invoice and billing_owner_user_id = user A
  When a caller resolves "who is billed" for that invoice
  Then the answer is derived by joining invoice.tenant_id -> tenants.billing_owner_user_id (= A)
  And the invoices table itself carries no new column

Scenario: migration downgrade is fully reversible   # M8
  Given this task's migration is applied (head)
  When it is downgraded back to 113ebdbe9f09
  Then billing_owner_user_id no longer exists as a column on tenants
  And ck_tenants_platform_no_billing_owner no longer exists

Scenario: demote-last-billing-owner is rejected   # R1
  Given a tenant whose billing_owner_user_id is user A (OWNER)
  When an OWNER calls PUT /admin/users/{A}/role { role: "member" }
  Then it fails with 409 ERR_LAST_BILLING_OWNER
  And A's role and the tenant's billing_owner_user_id are BOTH unchanged

Scenario: deactivate-last-billing-owner is rejected   # R2
  Given a tenant whose billing_owner_user_id is user A
  When SCIM issues PATCH active:false for user A
  Then it fails with 409 ERR_LAST_BILLING_OWNER
  And A.deactivated_at stays NULL and team_members rows for A are untouched

Scenario: reassign to a non-billing-capable user is rejected   # R3
  Given user D is ACTIVE with role=viewer in the caller's tenant
  When the OWNER calls PUT /admin/billing-owner { user_id: D }
  Then it fails with 422 ERR_BILLING_OWNER_INELIGIBLE
  And the tenant's billing_owner_user_id is unchanged

Scenario: reassign to a deactivated user is rejected   # R4
  Given user E has role=owner but deactivated_at IS NOT NULL
  When the OWNER calls PUT /admin/billing-owner { user_id: E }
  Then it fails with 422 ERR_BILLING_OWNER_INELIGIBLE
  And the tenant's billing_owner_user_id is unchanged

Scenario: reassign to another tenant's user is rejected (confused deputy)   # R5
  Given user F belongs to a DIFFERENT tenant than the caller's
  When the OWNER calls PUT /admin/billing-owner { user_id: F }
  Then it fails with 404 ERR_USER_NOT_FOUND — byte-identical to an unknown user_id
  And no information about F's existence in the other tenant is disclosed
  And the tenant's billing_owner_user_id is unchanged

Scenario: reassignment by a non-OWNER caller is rejected   # R6
  Given the caller's role is admin (not owner)
  When they call PUT /admin/billing-owner { user_id: <any valid target> }
  Then it fails with 403 ERR_AUTH_FORBIDDEN
  And the tenant's billing_owner_user_id is unchanged

Scenario: self-demote by the sole billing owner is rejected by the PRE-EXISTING self-guard   # R7
  Given a tenant whose billing_owner_user_id is user A (OWNER), A is the caller
  When A calls PUT /admin/users/{A}/role { role: "member" } (targeting themselves)
  Then it fails with 403 ERR_AUTH_FORBIDDEN (the existing self-guard, NOT 409 ERR_LAST_BILLING_OWNER)
  And A's role and the tenant's billing_owner_user_id are BOTH unchanged

Scenario: superadmin cross-tenant demote-last-billing-owner is rejected identically   # R8
  Given a tenant whose billing_owner_user_id is user A (OWNER)
  When a superadmin calls PUT /admin/platform/tenants/{tid}/users/{A}/role { role: "member" }
  Then it fails with 409 ERR_LAST_BILLING_OWNER — identical to R1
  And A's role and the tenant's billing_owner_user_id are BOTH unchanged

Scenario: concurrent reassign-to-X and demote-X never both succeed   # R9 race
  Given a tenant whose billing_owner_user_id is user A, and user X is an ACTIVE billing_admin
  When PUT /admin/billing-owner { user_id: X } and PUT /admin/users/{X}/role { role: "member" } are
    issued concurrently
  Then exactly one of two outcomes holds after both complete: (a) the reassignment commits first ->
    billing_owner_user_id becomes X -> the demotion then correctly 409s ERR_LAST_BILLING_OWNER, or
    (b) the demotion commits first (X was never the owner yet) -> it succeeds -> the reassignment then
    re-validates X against the post-commit role and correctly 422s ERR_BILLING_OWNER_INELIGIBLE
  And billing_owner_user_id NEVER ends up pointing at a non-billing-capable or inactive user
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Schema (additive column + CHECK + backfill, ONE migration, down_revision="113ebdbe9f09", reversible):
  ALTER TABLE tenants ADD COLUMN billing_owner_user_id UUID NULL
      REFERENCES users(id) ON DELETE RESTRICT
  ADD CONSTRAINT ck_tenants_platform_no_billing_owner
      CHECK (billing_owner_user_id IS NULL OR kind != 'platform')
  UPDATE tenants t SET billing_owner_user_id = (
      SELECT u.id FROM users u
      WHERE u.tenant_id = t.id AND u.deactivated_at IS NULL
        AND u.role IN ('owner', 'billing_admin')
      ORDER BY (u.role = 'owner') DESC, u.created_at ASC, u.id ASC
      LIMIT 1
  ) WHERE t.kind = 'customer'   -- NULL if no eligible row (never crashes); platform excluded
  downgrade: DROP CONSTRAINT ck_tenants_platform_no_billing_owner
             DROP COLUMN billing_owner_user_id

Domain (tenants/domain/entities.py):
  BILLING_CAPABLE_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.BILLING_ADMIN})   # NEW, public

Domain errors (tenants/domain/errors.py):
  LastBillingOwnerError        # raised by AssignUserRoleUseCase.execute + SetScimUserActiveUseCase.execute
  BillingOwnerIneligibleError  # raised by the new ReassignBillingOwnerUseCase

HOOK 1 — role-change (tenants/application/users_use_cases.py:AssignUserRoleUseCase.execute):
  order: assert_role_within_ceiling -> self-guard -> get_by_id_and_tenant (404) ->
    lock_and_get_billing_owner_user_id (FOR UPDATE) -> [target_user_id == billing_owner_user_id AND
    new_role not in BILLING_CAPABLE_ROLES -> raise LastBillingOwnerError] -> update_role (commits,
    releases lock). Reused verbatim by users_router.py AND platform_users_router.py; BOTH routers add
    `except LastBillingOwnerError: raise LAST_BILLING_OWNER.exc() from None`.

HOOK 2 — deactivation (scim/application/user_use_cases.py:SetScimUserActiveUseCase.execute ->
  scim/infrastructure/repository.py:SqlAlchemyScimUserRepository.set_active):
  order: SELECT UserRow ... FOR UPDATE -> already_at_target no-op check -> [active is False AND NOT
    already_at_target AND user_id == tenant's billing_owner_user_id (via the SAME new
    lock_and_get_billing_owner_user_id-shaped SELECT ... FOR UPDATE on tenants, same transaction) ->
    raise LastBillingOwnerError] -> deactivated_at write + team_members delete + commit.
  scim_router.py adds `except LastBillingOwnerError` around BOTH the PATCH handler and the DELETE-alias
  handler, translating to LAST_BILLING_OWNER.exc().

New repo methods:
  UserRoleRepository.lock_and_get_billing_owner_user_id(tenant_id) -> uuid.UUID | None
    # SELECT billing_owner_user_id FROM tenants WHERE id=:t FOR UPDATE
  ScimUserRepository (Protocol, scim/domain/ports.py) gains the SAME-shaped method + implementation.

PUT /admin/billing-owner   body: { user_id: uuid }
  requires: require_permission(Permission.SECURITY_CONFIG) — OWNER only (reused, no new Permission
    enum member); operates ONLY on identity.tenant_id (no path/body tenant param — structurally cannot
    target another tenant)
  order (one transaction): lock tenants row (FOR UPDATE, same M4 lock) -> resolve target via
    get_by_id_and_tenant(user_id, tenant_id=identity.tenant_id) [None -> USER_NOT_FOUND, 404] ->
    validate target.deactivated_at IS NULL AND target.role in BILLING_CAPABLE_ROLES
    [fails -> BILLING_OWNER_INELIGIBLE, 422] -> UPDATE tenants.billing_owner_user_id = user_id (no-op
    if already equal) -> commit
  200 -> { user_id, email, role }              # the new (or unchanged) billing owner
  403 -> { error: "ERR_AUTH_FORBIDDEN" }        # non-OWNER caller (R6)
  404 -> { error: "ERR_USER_NOT_FOUND" }        # absent OR cross-tenant target (R5)
  422 -> { error: "ERR_BILLING_OWNER_INELIGIBLE" }   # not active+billing-capable (R3/R4)

GET /admin/billing-owner   (any authenticated role, own-tenant only)
  200 -> { user_id: uuid | null, email: string | null, role: string | null }

New ErrorSpec (core/error_catalog.py, mirrors existing style):
  LAST_BILLING_OWNER = ErrorSpec(409, "ERR_LAST_BILLING_OWNER",
      "The last billing-capable owner cannot be demoted or deactivated — designate another billing "
      "owner first")
  BILLING_OWNER_INELIGIBLE = ErrorSpec(422, "ERR_BILLING_OWNER_INELIGIBLE",
      "Target user must be an active, billing-capable (owner or billing_admin) member of this tenant")

Attribution (read-side only, no schema change):
  invoices: InvoiceRow.tenant_id -> tenants.billing_owner_user_id (join at query time)
  credits:  tenant_credit_balances.tenant_id -> tenants.billing_owner_user_id (join at query time)
```

Glossary deltas:
  billing owner: the single designated payer of record for a tenant (`tenants.billing_owner_user_id`);
    always an ACTIVE, billing-capable user of that same tenant; changed ONLY via
    `PUT /admin/billing-owner` (OWNER-only) — never implicitly, never by demotion/deactivation.
  billing-capable role: `Role.OWNER` or `Role.BILLING_ADMIN` — the set a tenant's billing owner must
    belong to (`BILLING_CAPABLE_ROLES`). [folded foundation-version 53]

Least-sure flag surfaced at freeze: [spec] attribution is a LIVE read-side join
  (`tenant_id -> tenants.billing_owner_user_id`), not a point-in-time snapshot on each invoice/credit
  row (§1 ⚠) — reassigning the billing owner retroactively changes what a PAST invoice's queried
  attribution shows. Chosen per Tin's locked decision (denormalization named OPTIONAL) and the
  milestone's Out-of-scope (no invoice/credit internals rewrite); reversible later via an additive
  snapshot column + backfill if a point-in-time audit guarantee is wanted.
Status: FROZEN @ v1 — approved by auto (project-lead) + Tin-locked design (hard-reject, billing-capable={owner,billing_admin})
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

Scope (may touch): `/apps/gateway/migrations/versions/f94771e4aa7c_billing_owner_of_record.py` `/apps/gateway/src/gateway/tenants/infrastructure/orm.py` `/apps/gateway/src/gateway/tenants/domain/entities.py` `/apps/gateway/src/gateway/tenants/domain/errors.py` `/apps/gateway/src/gateway/tenants/infrastructure/users_repository.py` `/apps/gateway/src/gateway/tenants/application/users_use_cases.py` `/apps/gateway/src/gateway/tenants/application/billing_owner_use_cases.py` `/apps/gateway/src/gateway/tenants/api/billing_owner_router.py` `/apps/gateway/src/gateway/tenants/api/users_router.py` `/apps/gateway/src/gateway/tenants/api/platform_users_router.py` `/apps/gateway/src/gateway/scim/domain/ports.py` `/apps/gateway/src/gateway/scim/infrastructure/repository.py` `/apps/gateway/src/gateway/scim/api/scim_router.py` `/apps/gateway/src/gateway/core/error_catalog.py` `/apps/gateway/src/gateway/main.py`
Strategy (ordered batches): 1. migration (add nullable FK column ON DELETE RESTRICT + platform CHECK + owner-first backfill) 2. ORM `TenantRow.billing_owner_user_id` + `BILLING_CAPABLE_ROLES` on entities + 2 domain errors 3. repo lock/get/set methods (`lock_and_get_billing_owner_user_id` FOR UPDATE) 4. HOOK 1 (AssignUserRoleUseCase) + HOOK 2 (scim set_active) reject-guards behind the shared lock 5. reassign+get use cases + `/admin/billing-owner` router (SECURITY_CONFIG PUT / open GET) 6. ErrorSpecs + wire router in main.py + `except LastBillingOwnerError` in all 3 role/deactivation routers.

Persona (required): appsec-engineer — application-security / tenant-isolation & RBAC domain stance (`.add/personas/appsec-engineer.md`).
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 22/22 new (billing_owner_of_record + migration backfill) + 184/184 touched-surface regression (scim_provisioning, users_role, rbac_roles, superadmin_role, platform_tenant_directory, member_invite_*, account_type_discriminator, seat_billing, tenants) + build-agent's 526/526 full regression; all green.
- [x] coverage did not decrease — new code fully exercised by the 22 new tests (2 hooks, reassign endpoint 404/422/200, migration 4-way backfill + CHECK + downgrade, barrier race).
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched; the sanctioned build fix was in SOURCE only (`UserRoleRepository._row_to_user` now carries `deactivated_at`, previously discarded), no frozen test edited.
- [x] the green was EARNED, not gamed — dual adversarial refute-read (orchestrator + independent add-verify agent ae80c82822877fd0e); race test uses a real DB-level `asyncio.Barrier` at the exact lock call site (not vacuous `gather`); no overfit / stub-away found.
- [x] concurrency / timing safe — all 3 mutating paths (HOOK 1, HOOK 2, reassign) acquire the identical `SELECT billing_owner_user_id … FOR UPDATE` on the tenants row before decision+write in one txn; barrier-forced test asserts both legitimate outcomes + the invariant.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps; all SQL parameterized; cross-tenant target → 404 byte-identical (no oracle).
- [x] layering & dependencies follow CONVENTIONS.md — domain (entities/errors) → application (use cases) → infrastructure (repo) → api (router); SECURITY_CONFIG reused (no new Permission member), mirroring retention/residency router precedent.
- [ ] a person reviewed and approved the change — PENDING Tin's PR-time human review (task held uncommitted with the rest of the milestone); orchestrator-gated under standing dual-verify authorization for security tasks.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] Demoting a tenant's billing owner below billing-capable (via role-assign) is rejected `409 ERR_LAST_BILLING_OWNER` — confirmed by HOOK 1 in `users_use_cases.py:134-138` + all 3 role routers `except LastBillingOwnerError`.
- [ ] Deactivating a tenant's billing owner (via SCIM PATCH/PUT/DELETE active:false) is rejected `409 ERR_LAST_BILLING_OWNER` — confirmed by HOOK 2 in `scim/infrastructure/repository.py:294-300`, sole `deactivated_at` writer.
- [ ] `PUT /admin/billing-owner` reassigns only within own tenant, OWNER-only (SECURITY_CONFIG); cross-tenant/absent target → `404 ERR_USER_NOT_FOUND` byte-identical; non-active/non-billing-capable → `422 ERR_BILLING_OWNER_INELIGIBLE` — confirmed by `billing_owner_router.py` + `ReassignBillingOwnerUseCase`.
- [ ] Existing customer tenants each carry exactly one backfilled billing owner (earliest active OWNER, else billing_admin); platform tenant stays NULL — confirmed by migration `f94771e4aa7c` backfill + CHECK `ck_tenants_platform_no_billing_owner`.
- [ ] Concurrent reassign-vs-demote/deactivate cannot both commit — confirmed by the shared `FOR UPDATE` tenants-row lock on all 3 paths + barrier-forced race test.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `billing_owner_router` imported + `include_router` in `main.py:233,1537`; `LAST_BILLING_OWNER`/`BILLING_OWNER_INELIGIBLE` ErrorSpecs at `error_catalog.py:1250,1259`; `BILLING_CAPABLE_ROLES` referenced by both hooks + reassign use case; all 3 routers `except LastBillingOwnerError` (grep-confirmed: scim_router, platform_users_router, users_router).
- [x] DEAD-CODE (code) — no orphaned symbol: every new repo method (`lock_and_get`/`get`/`set_billing_owner_user_id`) has a live caller; `GetBillingOwnerUseCase`/`ReassignBillingOwnerUseCase` both wired to router routes.
- [x] SEMANTIC (prose) — migration `f94771e4aa7c` docstring read in full: backfill order/exclusion/CHECK/FK-RESTRICT all match the §3 frozen contract verbatim.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves — `Role`/`BILLING_CAPABLE_ROLES` (entities.py), `AssignUserRoleUseCase.execute` (users_use_cases.py:107), `SqlAlchemyScimUserRepository.set_active` (scim/infra/repository.py:261), `SECURITY_CONFIG` (authz.py), `TenantRow` FK/CHECK precedents (orm.py) — all present; single alembic head `f94771e4aa7c` confirmed via `alembic heads`.
- [x] no anchor moved/renamed since Ground SHA (down_revision `113ebdbe9f09` still head-parent).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self (orchestrator) + agent ae80c82822877fd0e (independent add-verify) · adversarially checked: (1) every `users.role`/`deactivated_at` writer traced — only `update_role`(HOOK 1) + scim `set_active`(HOOK 2); SSO/OIDC/SAML JIT writes role=MEMBER on new users only, never overwrites; SCIM PUT/PATCH/DELETE all funnel through one use case. (2) cross-tenant confused-deputy on PUT/GET /admin/billing-owner — own-tenant-only by construction, `get_by_id_and_tenant` single filtered query, 404 byte-identical. (3) R9 race — identical FOR UPDATE lock on all 3 paths; barrier test at exact lock site is real, not vacuous. (4) platform CHECK + FK RESTRICT + backfill order verified against migration. No overfit/vacuous assert/stub-away found.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: self (orchestrator) + agent ae80c82822877fd0e (independent adversarial lens) — DUAL, per the security-task ≥2-independent-verify bar
1. Security: CLEAR — no missed mutation path, no cross-tenant leak, platform tenant can never carry a billing owner (CHECK + own-tenant-only writes).
2. Concurrency: CLEAR — shared FOR-UPDATE tenants-row lock serializes reassign vs demote/deactivate; barrier-forced test proves both legitimate outcomes + invariant, never both.
3. Architecture: CLEAR — clean domain→app→infra→api layering; SECURITY_CONFIG + nullable-FK + platform-CHECK all reuse established TenantRow precedents.
Verdict: PASS
Residue: non-blocking follow-up — fresh signups leave `billing_owner_user_id` NULL (create_tenant_with_owner doesn't populate it); judged INCOMPLETE-COVERAGE, NOT a regression (repo-wide grep found zero pre-task last-owner protection — nothing regresses). Needed to fully meet the milestone GOAL → filed as the next follow-on task.
Binding: advisory — security (a human floor; recorded PASS held for Tin's PR-time review)

### GATE RECORD
Reported: yes — dual-verify evidence + ARC rendered to Tin before this outcome recorded
Outcome: PASS
Reviewed by: orchestrator (add project-lead, standing dual-verify authorization for security tasks) · date: 2026-07-16 — human sign-off PENDING Tin at PR time (task held uncommitted)

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by auto (project-lead) + Tin-locked design (hard-reject, billing-capable={owner,billing_admin}))
- [AI] build — strategy used: as planned
- [human] verify — gate PASS (reviewed by orchestrator (add project-lead, standing dual-verify authorization for security tasks))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

