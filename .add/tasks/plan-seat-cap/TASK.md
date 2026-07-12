# TASK: Plan seat cap enforcement

slug: plan-seat-cap · created: 2026-07-05 · stage: production
sensitivity: data
milestone: platform-access-plan
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `PlanRow.seat_cap` / `TenantRow.seat_cap` (`apps/gateway/src/gateway/tenants/infrastructure/
    orm.py:55,174`) — BOTH columns already exist, shipped by `plan-catalog` (FROZEN@v1, DONE):
    `plans.seat_cap` (per-tier default, nullable=unlimited, `ck_plans_seat_cap_positive`) and
    `tenants.seat_cap` (per-tenant override, nullable, `ck_tenants_seat_cap_positive`). Neither
    column is read by any application code path today — confirmed by grep: zero non-migration,
    non-ORM reference to `.seat_cap` anywhere in `apps/gateway/src/gateway/` outside `orm.py`
    itself and `plan_admin_ui`'s read-only response DTOs. This task is the FIRST to read them for
    an enforcement decision. No new column/migration needed for this task (confirmed below).
  - `ResolvedEntitlements` / `resolve_entitlements` (`gateway/tenants/domain/entitlements.py`,
    plan-enforcement TASK.md §3, FROZEN@v1, DONE) — the pure, zero-I/O precedence function
    (explicit tenant override > plan default > unlimited) already governing the BUDGET dimension
    identically to how a seat cap must behave. Has ZERO seat-cap field today (plan-enforcement's
    own milestone binding rule 5 explicitly excluded seats). `SqlAlchemyPlanEntitlementResolver`
    (`gateway/tenants/infrastructure/plan_entitlement_resolver.py`) is its READ-ONLY, un-locked,
    in-process adapter (M8, named future consumer: `seat-billing`). This is the resolution path
    this task's own dispatch instructs to REUSE — read in full this session; extended additively
    below (2 new optional kwargs + 1 new field), never a second precedence implementation.
  - `check_plan_feature` (`gateway/tenants/application/entitlements.py`, plan-enforcement §3,
    FROZEN@v1) — the sibling module this task's own new helper lives beside; established the
    "own tailored inline SQL query per call site, sharing only the precedence LOGIC" idiom this
    codebase already runs (NOT "every consult must route through the one general resolver" — three
    separate query shapes already coexist: `RedisBudgetGuard._fetch_budget`'s own query,
    `check_plan_feature`'s own query, `SqlAlchemyPlanEntitlementResolver.resolve`'s own query — all
    against the same `tenants LEFT JOIN plans` shape, each fetching only the columns it needs).
    This task's own `assert_seat_available` follows the SAME idiom — a dedicated, LOCKED query
    (`FOR UPDATE OF t`, unlike any of the 3 existing ones, none of which lock), reusing
    `resolve_entitlements()` for the actual precedence arithmetic on the values it fetches.
  - `error_catalog.py:858-880` — `PLAN_TENANT_INELIGIBLE` / `PLAN_MODEL_NOT_ALLOWED` /
    `PLAN_FEATURE_NOT_ENABLED`, all `ErrorSpec(403, "ERR_PLAN_*", ...)` — the established "plan
    says no" 403 family this task's own new code joins (`ERR_PLAN_SEAT_CAP_EXCEEDED`).
    `ProblemError`/`ErrorSpec.exc()` (`gateway/core/errors.py`, `error_catalog.py:32-70`) — the
    RFC 9457 envelope registered app-wide via `register_error_handlers` (`core/errors.py:52`);
    confirmed live (not SCIM-scoped) by reading both OIDC/SAML callback routers' own
    `except <DomainError>: raise <ERR>.exc() from None` chains (below).
  - **FOUR real INSERT call sites materialize a new tenant member today** (the milestone's own
    text names only 2 — "OIDC auto-provision + invite-accept" — written before `domain-capture`
    and `scim-provisioning` existed; corrected here from direct grep, not the stale milestone
    prose):
    1. `InviteRepository.accept` (`gateway/tenants/infrastructure/invite_repository.py:237-314`,
       member-invite-acceptance §3 FROZEN@v1, DONE) — ONE locked transaction: `SELECT invites ...
       FOR UPDATE` → validate pending/not-expired → `INSERT users` → `UPDATE invites SET
       status='accepted'` → COMMIT; any exception rolls back EVERYTHING (invite stays pending,
       unflipped — the exact "no orphaned row, no partial write" discipline this task's own new
       check must preserve). Called from `AcceptInviteUseCase.execute`
       (`tenants/application/invite_accept_use_cases.py`) ← `POST /invites/{token}/accept`
       (PUBLIC, no auth — `tenants/api/invite_accept_router.py`).
    2. `_get_or_provision_sso_user` (`gateway/tenants/infrastructure/repository.py:174-232`) — the
       SHARED helper behind BOTH `get_or_provision_oidc_user` and `get_or_provision_saml_user`
       (:129-172). GET-OR-CREATE: an EXISTING user (matched by email) returns immediately at
       line 199-206, BEFORE any seat consideration — re-authenticating an existing member must
       NEVER be blocked by a cap that only governs NEW admissions. Only the "provision new user"
       branch (:208-232) — `SELECT users` already auto-begins the session (explicit comment,
       :219-221) → `session.add(new_user)` → `flush()` → `commit()` — actually materializes a
       seat. Reached from `GET /admin/auth/oidc/callback` (`auth/api/oidc_router.py:182-291`,
       `except OidcTenantConflictError as exc: raise OIDC_TENANT_CONFLICT.exc() from exc` at
       line 288-289 is the exact pattern this task's new reject joins) and
       `POST /admin/auth/saml/acs` (`auth/api/saml_router.py:85-140`, identical except-chain
       shape at line 136).
    3. `join_verified_tenant_domain` (`gateway/tenants/infrastructure/repository.py:88-112`,
       domain-capture §3 M9 FROZEN@v1, DONE) — INSERT-only (mirrors `create_tenant_with_owner`'s
       own shape, deliberately NOT the get-or-provision shape — domain-capture's own §0 flags
       `_get_or_provision_sso_user`'s existing-user branch as an account-enumeration risk for a
       SIGNUP-shaped call). `async with self._session.begin(): self._session.add(user)` —
       IntegrityError -> `EmailAlreadyRegisteredError`. Reached from `JoinTenantByDomainUseCase.
       execute` (`domain_capture/application/join_tenant_use_case.py`) ← `POST /admin/auth/signup`
       (`tenants/api/router.py:43-98`), the `claimed_tenant_id is not None` branch (:67-81) — a
       verified-domain signup that auto-joins an EXISTING tenant (this is "direct signup routing
       via domain-capture" from the dispatch). The OTHER branch of the same endpoint
       (`create_tenant_with_owner`, :90-98) creates a BRAND-NEW tenant — always `plan_id=NULL` at
       birth (plan-catalog M2, no auto-assignment) — uncapped by construction, out of scope,
       confirmed below (ruled out, not silently).
    4. `SqlAlchemyScimUserRepository.create_user` (`gateway/scim/infrastructure/repository.py:
       147-166`, scim-provisioning §3 Part B FROZEN@v1, DONE) — `self._session.add(row); await
       self._session.commit()` (NO explicit `begin()` block today — implicit autobegin — this
       task's own change must restructure this into an explicit transaction to hold the lock
       through the INSERT, see Issues/Risks). Reached from `CreateScimUserUseCase.execute`
       (`scim/application/user_use_cases.py:13-27`) ← `POST /scim/v2/Users`
       (`scim/api/scim_router.py:272-295`, machine-bearer-authenticated, RFC 7644 envelope — a
       DELIBERATE, documented exception to the project-wide RFC 9457 convention, confirmed by
       reading `scim/api/errors.py:1-8` this session).
  - `UserRow` (`tenants/infrastructure/orm.py:177-207`) — `deactivated_at: datetime | None`
    (additive, scim-provisioning migration `010e6f83a709`; NULL = active, default; set/cleared
    ONLY by SCIM `PATCH .../active`). No separate "seat" table or column exists anywhere — a
    "seat" has no prior codebase definition; this task is the FIRST to need one (see §1 ⚠).
  - `tenants_platform_kind_uidx` / `ck_tenants_platform_no_plan` (`orm.py:82-94`) — the platform
    tenant (`kind='platform'`) can NEVER hold a `plan_id` (plan-catalog M3, DB-level defense in
    depth) — so `assert_seat_available` is a guaranteed no-op for any admission into the platform
    tenant (superadmin provisioning is never blockable by this task, confirmed by construction,
    not by a special-cased check).
  - Current alembic head (confirmed by walking the `down_revision` chain this session, single
    linear branch, most-recent-first): `0b5527920450` (invoice-generation) <-
    `d3f7a9c1b5e8` (credits-ledger) <- `f70309062df0` (plan-enforcement) <-
    `fddae7074590` (cost-attribution-tags) <- `69cfdc584129`. **No migration is needed for this
    task** — both `seat_cap` columns and `users.deactivated_at` already exist; this is
    application-code-only wiring, the exact same "Schema: NO migration" shape
    member-invite-acceptance's own §3 already set precedent for.
Context (working folder): `.add/milestones/platform-access-plan/MILESTONE.md` (Tasks list —
  stale 2-seam description, corrected above) and `.add/milestones/monetization-core/MILESTONE.md`
  + `tmp/monetization-core-design-context.md` (binding rule 5: seat CAPS are this task's own,
  `plan-enforcement` never duplicates them — re-confirmed by reading `plan-enforcement/TASK.md`
  §0 in full this session, which independently notes plan-seat-cap's own file was still
  template-empty at ITS ground time); `.add/tasks/plan-catalog/TASK.md` §1 (frozen M6-M9 —
  the assign/change endpoint does NOT consult current headcount before a downgrade, explicitly
  named as THIS task's own concern); `.add/tasks/seat-billing/TASK.md` (sibling, milestone
  `monetization-core`, `phase: ground`, body STILL template-empty as of this session — no seat
  COUNT definition exists there to align against; see §1 ⚠, the top flag).
Honors (patterns / conventions):
  - Reuse-over-invent — extends `ResolvedEntitlements`/`resolve_entitlements` additively (2 new
    optional kwargs, 1 new field, zero existing call site touched) rather than a parallel
    precedence implementation; mirrors plan-enforcement's own M1 "each dimension computed
    independently" design exactly.
  - Cap enforced at the moment of admission, never retroactive lockout (this task's own persona
    stance) — mirrors plan-enforcement's own M7 "grandfathered, never retroactively capped" DECIDED
    precedent for the budget dimension, applied here to seats.
  - Frozen-contract supersession only (backend-architect persona convention, reused across
    plan-enforcement) — `member-invite-acceptance`, `domain-capture`, `scim-provisioning`'s own
    frozen TASK.md files are never edited; every additive change to their shipped code is recorded
    HERE, in this task's own TASK.md, as a superseding addition.
  - Design-for-failure (project-wide non-negotiable) — every admission seam gets a row-level lock
    (`FOR UPDATE OF t`) held for the SAME transaction as its INSERT, not a read-then-write race;
    see Issues/Risks below for the per-call-site transaction-mechanics risk this introduces.
Seams consulted: none in `.add/SEAMS.md` for entitlement/seat resolution yet — plan-enforcement's
  own §0 already flagged the SAME gap ("first task to establish this seam"); this task's own
  `assert_seat_available` is a second data point for a future `.add/SEAMS.md#entitlement-
  resolution` entry, not written here (BUILD's job per that task's own note).
Anchors the contract cites: `ResolvedEntitlements`/`resolve_entitlements`
  (`tenants/domain/entitlements.py`), `SeatCapExceededError` (NEW, `tenants/domain/errors.py`),
  `assert_seat_available` (NEW, `tenants/application/entitlements.py`), `PLAN_SEAT_CAP_EXCEEDED`
  (NEW, `core/error_catalog.py`), `InviteRepository.accept`, `_get_or_provision_sso_user`,
  `join_verified_tenant_domain`, `SqlAlchemyScimUserRepository.create_user`, `scim_seat_cap_
  exceeded` (NEW, `scim/api/errors.py`).
Issues/Risks (→ feed §1):
  - **[Major, feeds the ⚠ top flag]** No prior "seat" definition exists anywhere in this
    codebase, and `seat-billing` (the sibling that will actually BILL per seat, same milestone)
    has not drafted one — its TASK.md is still template-empty. This task must PROPOSE a count
    definition (active `UserRow` per tenant, `deactivated_at IS NULL` — the only existing
    per-member lifecycle signal) to unblock its own enforcement, but cannot CONFIRM it matches
    what `seat-billing` will bill against — a real coordination gap, not a manufactured one: if
    `seat-billing` later bills a DIFFERENT count (e.g. excluding SCIM-provisioned or specific
    roles), a tenant could be capped at N but invoiced for M≠N seats.
  - **[Major]** None of the 4 admission call sites take a `tenants`-row lock today; 3 different
    pre-existing transaction shapes must each be extended correctly for the lock to actually hold
    through the INSERT: (a) `InviteRepository.accept` already opens its OWN `FOR UPDATE` on
    `invites` — the new tenant-row lock composes safely (no deadlock: only this ONE method ever
    holds both locks at once, so concurrent instances of it targeting the same tenant merely
    serialize, never cycle); (b) `_get_or_provision_sso_user` has an explicit, commented
    SQLAlchemy autobegin quirk ("calling `begin()` again raises `InvalidRequestError`") — the new
    check must reuse the ALREADY-open transaction, not call `begin()` a second time; (c)
    `SqlAlchemyScimUserRepository.create_user` has NO explicit transaction today at all (bare
    `add()` + `commit()`) — this task's own change must introduce one, the first structural change
    to that method since scim-provisioning shipped. Flagged for BUILD, not resolved here (⚠ #2).
  - **[Minor]** SCIM's RFC 7644 vocabulary (`invalidFilter|tooMany|uniqueness|mutability|
    invalidSyntax|invalidPath|noTarget|invalidValue|invalidVers|sensitive`) has no clean fit for
    "business-quota exceeded" — `scim_invalid_token()`'s own existing precedent (401, no
    `scimType` at all) is followed rather than forcing an awkward vocabulary match.
  - **[Ruled out, not silently]** SCIM `PATCH /scim/v2/Users/{id}` `active:true` (reactivation)
    ALSO increases the active-seat count by one — functionally an admission event by this task's
    own persona's definition — but is NOT one of the 4 seams this task's dispatch names, and
    scim-provisioning's own frozen §3 contract for that endpoint says nothing about a cap
    consultation. Gating it would mean superseding a 5th call site never named in scope. Left
    OUT of this task's Must list deliberately — named here as a real gap, not dropped silently —
    flagged forward as a §7 OBSERVE spec-delta candidate.
  - **[Ruled out, not silently]** Tenant SIGNUP itself (`SignupUseCase.execute` ->
    `create_tenant_with_owner`, the OTHER branch of `POST /admin/auth/signup`) is untouched —
    every brand-new tenant starts with `plan_id=NULL` (plan-catalog M2, no auto-assignment), so
    `assert_seat_available` resolves to uncapped by construction; no special case needed.
Related intent: `.add/milestones/platform-access-plan/MILESTONE.md` Exit criterion "Adding a
  member beyond a tenant's seat cap is rejected at both provisioning entry points" (text
  corrected above from 2 to 4 real seams) + `.add/milestones/monetization-core/MILESTONE.md`
  binding rule 5 (seat caps are THIS task's own, `plan-enforcement` never duplicates them) +
  GLOSSARY delta "seat" (proposed, §3) — this task turns an inert override column
  (`tenants.seat_cap`, shipped but unread since plan-catalog) into a real, enforced ceiling, the
  same "catalog defines it, a sibling task enforces it" shape plan-enforcement already delivered
  for budget/allowlist/features.
Ground SHA: 71641a9

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Plan seat-cap enforcement — block a NEW active member from materializing at any of the 4
real provisioning INSERT sites once a tenant's effective seat cap (explicit `tenants.seat_cap`
override > assigned plan's `plans.seat_cap` default > unlimited) is already met, via one shared,
lock-guarded helper reusing plan-enforcement's own precedence function; never touches an
already-existing member, ever, regardless of a later plan/cap change.

Framings weighed:
  - **Invite enforcement point: ACCEPT, not ISSUANCE** **(CHOSEN)** — vs. also/instead blocking
    `POST /admin/invites` (issuance) once outstanding-pending-invites + current headcount would
    exceed the cap **(REJECTED)**. An issued invite is provisional intent, not a consumed seat —
    it can expire, be revoked, or simply never be accepted; blocking issuance would require a
    SECOND, weaker heuristic (forecasting against pending invites) that itself races against
    concurrent accepts/revokes and is redundant once accept-time enforcement is authoritative.
    This is also the corrected reading of the milestone's own intent (its Tasks list originally
    cited `member-invite-issuance`, corrected 2026-07-05 to `member-invite-acceptance` once
    accept was scoped as its own task — re-confirmed directly against both frozen TASK.md files
    this session). Matches this task's own persona stance: a cap is enforced at the moment a seat
    actually materializes, never a moment earlier that is merely provisional.
  - **Seat count definition: active `UserRow` per tenant** (`deactivated_at IS NULL`) **(CHOSEN,
    provisional — see ⚠ below)** — vs. counting only `role='member'` rows (excluding
    owner/admin/etc.) **(REJECTED)** or counting `team_members` rows **(REJECTED)**. Every
    existing per-member lifecycle signal in this codebase is `UserRow.deactivated_at`
    (scim-provisioning's own soft-deactivate column) — there is no role-based precedent anywhere
    for treating an owner/admin differently from a member for headcount purposes, and
    `team_members` is an orthogonal WITHIN-tenant grouping (a user can belong to 0/1/many teams
    without changing tenant headcount) plan-catalog itself never named as the seat dimension.
    Lowest-confidence pick in this whole bundle — `seat-billing` has not drafted its own
    definition to align against (see ⚠).
  - **Where the lock lives: a NEW dedicated locked query in a NEW shared helper
    (`assert_seat_available`, sibling to `check_plan_feature`)** **(CHOSEN)** — vs. widening
    `SqlAlchemyPlanEntitlementResolver.resolve()` itself to take a lock **(REJECTED)**. That
    resolver is READ-ONLY by its own frozen contract (M8: "read-only, ZERO new HTTP surface"),
    consumed in-process by `seat-billing` (wave-2) for DISPLAY purposes — silently making its one
    query take a row lock would be a correctness surprise for every future caller that never
    expected to block on it. `assert_seat_available` still REUSES `resolve_entitlements()` itself
    (the pure precedence function) for the actual explicit-beats-default arithmetic — only the
    I/O (a locked, count-aware query) is new, never the precedence logic.
  - **Composition with an existing member's re-login: NEVER gated** **(CHOSEN, explicit)** — the
    cap only ever gates the "provision NEW user" branch of `_get_or_provision_sso_user`; an
    EXISTING user's re-authentication returns before any seat check is even reached. Not gating
    this is not a relaxation — it is the same principle as M-downgrade below, applied to
    authentication instead of admin action: an existing occupant of a seat is never re-evaluated
    against a cap that governs NEW admissions only.
  - **Downgrade / cap-lowering: no retroactive effect, ever** **(CHOSEN, explicit, persona-load-
    bearing)** — vs. deactivating/soft-locking the newest N-over-cap members on a downgrade
    **(REJECTED)**. `plan-catalog`'s own M6/M9 assign/change endpoint already does not consult
    headcount before permitting a change (its own §1 assumptions name this as explicitly THIS
    task's concern) — this task answers it by NEVER scanning or touching existing rows at all;
    `assert_seat_available` is consulted ONLY at the 4 admission INSERT sites, never on a
    plan-change write, never on a scheduled sweep. Mirrors plan-enforcement's own M7
    grandfathered-unlimited precedent for the budget dimension exactly, applied to seats.
Must:
<must>
  - **[M1]** `ResolvedEntitlements` (`tenants/domain/entitlements.py`) gains one additive field,
    `effective_seat_cap: int | None`; `resolve_entitlements()` gains two additive, OPTIONAL
    keyword args (`tenant_seat_cap: int | None = None`, `plan_seat_cap_default: int | None =
    None`, both defaulted so the 3 existing call sites — `RedisBudgetGuard`, both branches of
    `SqlAlchemyPlanEntitlementResolver.resolve` — are byte-identical, untouched). Precedence
    identical in SHAPE to the budget dimension: `tenant_seat_cap` if non-null, else
    `plan_seat_cap_default`, else `None` (unlimited) — computed independently, never perturbing
    any other dimension's own resolution.
  - **[M2]** A NEW shared helper `assert_seat_available(session, tenant_id) -> None`
    (`tenants/application/entitlements.py`, sibling to `check_plan_feature`) — ONE locked query
    (`SELECT t.seat_cap, t.plan_id, p.seat_cap, p.name FROM tenants t LEFT JOIN plans p ON
    t.plan_id = p.id WHERE t.id = :tid FOR UPDATE OF t`), resolves the effective cap via M1's
    extended `resolve_entitlements()`; if `None` (uncapped/unplanned — grandfathered), returns
    immediately with NO further query. Otherwise issues ONE count query (`SELECT COUNT(*) FROM
    users WHERE tenant_id = :tid AND deactivated_at IS NULL`) inside the SAME transaction (the
    `FOR UPDATE OF t` lock is still held) and raises `SeatCapExceededError` (NEW,
    `tenants/domain/errors.py`, carries `plan_id`/`plan_name`/`seat_cap`/`current_seats` — unlike
    every OTHER plain-marker `IdentityError`, it carries structured data because its 5 call sites
    span TWO incompatible error envelopes, RFC 9457 and RFC 7644, and each must build its OWN
    shape without a second query) iff `current_seats >= effective_seat_cap`.
  - **[M3]** The CALLER contract is explicit and load-bearing: `assert_seat_available` MUST be
    invoked inside the caller's OWN already-open transaction, strictly BEFORE the member-creating
    INSERT, never after and never in a separate transaction — the `FOR UPDATE OF t` lock only
    serializes concurrent admissions if held continuously from the cap check through the INSERT.
  - **[M4]** All 4 real admission seams call `assert_seat_available` exactly once, at the point
    immediately before their own INSERT, inside their own existing (or newly-introduced, for
    SCIM) transaction: `InviteRepository.accept` (after the pending/not-expired checks, before
    `INSERT users`), `_get_or_provision_sso_user` (in the "provision new user" branch ONLY —
    never the existing-user branch), `join_verified_tenant_domain` (first statement inside its
    existing `async with self._session.begin():` block), `SqlAlchemyScimUserRepository.
    create_user` (this task's own change wraps the method's existing bare `add()`+`commit()` in
    an explicit `async with self._session.begin():` block for the first time).
  - **[M5]** Every one of the 4 seams' own routers translates `SeatCapExceededError` into ITS
    envelope, never a bare 500: the 3 RFC-9457 seams (`invite_accept_router`, `oidc_router`,
    `saml_router`, `tenants/api/router.py`'s domain-capture branch — 4 routers, 3 of them auth-
    adjacent) raise `PLAN_SEAT_CAP_EXCEEDED.exc(extra={"upgrade_hint": {...}})`; the SCIM router
    raises a NEW `scim_seat_cap_exceeded()` (RFC 7644 envelope, `scim/api/errors.py`, mirrors
    `scim_uniqueness()`'s shape, 403, no `scimType` — RFC 7644's vocabulary has no clean fit,
    mirrors `scim_invalid_token()`'s own "no scimType" precedent rather than forcing one).
  - **[M6]** A REJECTED admission at any of the 4 seams leaves the tenant's `users` table, the
    triggering invite/domain-claim/SCIM-token row, and every OTHER tenant's data byte-identical to
    immediately before the attempt — no partial write, no orphaned row, no audit event fired
    (mirrors `InviteRepository.accept`'s own existing "IntegrityError -> rollback everything"
    discipline, extended to this task's own new rejection).
  - **[M7]** An existing, ALREADY-active member is NEVER re-evaluated against the seat cap for any
    reason — not on re-login (M4's SSO existing-user branch), not on a plan downgrade, not on a
    seat_cap lowering via `PUT /admin/platform/tenants/{tenant_id}/plan`, not on any scheduled
    process (none exists, and this task introduces none). `assert_seat_available` is consulted
    ONLY at the 4 named INSERT sites — this is the single behavioral guarantee this task's whole
    persona stance rests on: a cap is enforced at admission, never by retroactive lockout.
  - **[M8]** An unassigned tenant (`plan_id IS NULL`) OR a planned tenant with both
    `tenants.seat_cap IS NULL` and its plan's `seat_cap IS NULL` is completely uncapped
    (`effective_seat_cap` resolves to `None`) — `assert_seat_available` returns after its ONE
    locked SELECT, with NO count query ever issued; byte-identical to every tenant's behavior
    before this task ships (grandfathered-unlimited, mirrors plan-enforcement's own M7 DECIDED
    precedent for the budget dimension).
  - **[M9]** The platform tenant (`kind='platform'`) is unconditionally uncapped by construction
    (its `plan_id` is permanently NULL, DB-enforced by `ck_tenants_platform_no_plan` — no special
    case is written for it; M8 already covers it).
</must>
Reject:
<reject>
  - **[R1]** `POST /invites/{token}/accept` when accepting would put the tenant's active-member
    count at or above its effective seat cap -> "ERR_PLAN_SEAT_CAP_EXCEEDED" (403, NEW) — invite
    stays `pending`, unflipped; no `users` row inserted.
  - **[R2]** `GET /admin/auth/oidc/callback` OR `POST /admin/auth/saml/acs`, in the "provision new
    user" branch only, same cap condition -> "ERR_PLAN_SEAT_CAP_EXCEEDED" (403, NEW) — no `users`
    row inserted; an EXISTING user's re-login is never subject to this reject (M7).
  - **[R3]** `POST /admin/auth/signup` (verified-domain auto-join branch), same cap condition ->
    "ERR_PLAN_SEAT_CAP_EXCEEDED" (403, NEW) — no `users` row inserted; the target tenant's
    `plan_id`/`seat_cap` unchanged.
  - **[R4]** `POST /scim/v2/Users`, same cap condition -> RFC 7644 SCIM Error, `status: "403"`, no
    `scimType`, `detail: "Seat cap exceeded for this tenant's plan"` (NEW) — no `users` row
    inserted; SCIM token stays valid, unaffected.
</reject>
After:
<after>
  - After M2 (any seam, at-cap rejection): the tenant's active-member count is EXACTLY
    unchanged from immediately before the attempt; the tenant row itself (including
    `plan_id`/`seat_cap`) is unchanged; no audit event fired by the rejecting call site.
  - After M2 (under-cap, or uncapped): the request proceeds exactly as it would have before this
    task shipped — this task adds a precondition, never changes a success-path response shape.
  - After M4/concurrency (two admissions racing for the LAST remaining seat): exactly ONE
    succeeds; the loser observes R1/R2/R3/R4 as if it had been evaluated strictly after the
    winner's commit — never both succeeding, never both failing.
  - After M7 (a downgrade or seat_cap lowering below current headcount via the existing
    plan-catalog assign/change endpoint): every currently-active member stays active,
    unauthenticated by nothing new, indefinitely — until/unless a FUTURE admission at one of the
    4 seams is attempted, which is then evaluated against the NEW, lower cap.
  - After M9: a superadmin (platform tenant) can always be provisioned via any of the 4 seams that
    apply to it, regardless of any `plans`/`seat_cap` state — this task introduces no path that
    could ever block it.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **Seat count definition** (active `UserRow` per tenant, `deactivated_at IS NULL`) — LOWEST
    confidence in this bundle because `seat-billing` (same milestone, the sibling that will
    actually INVOICE per seat) has not drafted its own count definition — its TASK.md is still
    template-empty as of this session, so there is nothing to align against, only to propose.
    This task's own dispatch explicitly instructs "flag any divergence rather than resolving
    unilaterally" — this IS that flag: if `seat-billing` later bills a DIFFERENT count (e.g.
    excludes SCIM-provisioned rows, or counts only specific roles), a tenant could be capped at
    N but invoiced for M≠N seats, a real product inconsistency, not a cosmetic one. If wrong: the
    fix is additive and CONTAINED to this task alone — `assert_seat_available`'s own count query
    changes its WHERE clause; no contract shape, error code, or call-site wiring changes; flagged
    forward as this task's own §7 OBSERVE spec-delta regardless of which way the freeze goes.
  - [ ] Per-call-site transaction-mechanics risk (§0 Issues/Risks — 3 different pre-existing
    transaction shapes, one of which, SCIM's `create_user`, has NO explicit transaction today and
    needs one introduced for the first time) — medium confidence the SHAPE described in M2-M4 is
    right, lower confidence BUILD gets every call site's exact SQLAlchemy mechanics correct on the
    first pass (the `_get_or_provision_sso_user` autobegin quirk is explicitly commented as a
    trap in the existing code). If wrong: a build-time bug (lock not actually held through the
    INSERT, or an `InvalidRequestError`), caught by the concurrency scenario's own test — contained
    to this task, no contract change.
  - [ ] 403 (not 409) for `ERR_PLAN_SEAT_CAP_EXCEEDED` — medium-high confidence: mirrors the
    existing `ERR_PLAN_TENANT_INELIGIBLE`/`ERR_PLAN_MODEL_NOT_ALLOWED`/`ERR_PLAN_FEATURE_NOT_
    ENABLED` "plan says no" 403 family exactly (all 3 are refusals of an otherwise-valid request
    based on plan state, the same shape as this one); a 409 (resource-conflict) reading is
    defensible but has no precedent in this specific error family. Cheap to fix if wrong — a
    status-code-only change, no shape/route change.
  - [ ] SCIM reactivation (`PATCH .../active:true`) left un-gated (§0 Issues/Risks, ruled out not
    silently) — medium-high confidence this is a legitimate, disclosed scope boundary (not named
    in the dispatch's own seam list, and superseding a 5th call site never asked for would be
    scope creep), but it IS a real gap against this task's own persona stance ("cap enforced at
    the moment of admission" — reactivation is structurally an admission). Flagged forward as a
    §7 OBSERVE spec-delta candidate, not silently dropped.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── M1: pure precedence extension (seat dimension) ───────────────────────────

Scenario: Explicit tenant seat_cap override beats the plan's default   # M1
  Given a tenant assigned plan "team" (plans.seat_cap=50) with its own explicit
    tenants.seat_cap=10
  When resolve_entitlements is called for this tenant
  Then the resolved effective_seat_cap is 10, not 50

Scenario: Plan's seat_cap default fills the gap when no tenant override is set   # M1
  Given a tenant assigned plan "starter" (plans.seat_cap=5) and tenants.seat_cap=NULL
  When resolve_entitlements is called for this tenant
  Then the resolved effective_seat_cap is 5

Scenario: An existing resolve_entitlements caller is unaffected by the new kwargs   # M1 (unchanged)
  Given RedisBudgetGuard's own existing call to resolve_entitlements, which never supplies
    tenant_seat_cap or plan_seat_cap_default
  When resolve_entitlements is called exactly as it was before this task
  Then effective_budget_usd_monthly resolves identically to pre-task behavior
  And effective_seat_cap resolves to None — this never perturbs the budget dimension

# ── M2/M8/M9: uncapped short-circuit — no count query ever issued ────────────

Scenario: An unplanned tenant is uncapped, no count query issued   # M2, M8
  Given a tenant with plan_id=NULL and tenants.seat_cap=NULL
  When assert_seat_available is called for this tenant
  Then it returns immediately after its one locked SELECT
  And no COUNT(*) query is issued against users

Scenario: A planned tenant whose plan and tenant seat_cap are both NULL is uncapped   # M2, M8
  Given a tenant assigned plan "enterprise" (plans.seat_cap=NULL) with tenants.seat_cap=NULL
  When assert_seat_available is called for this tenant
  Then it returns immediately — unlimited, byte-identical to an unplanned tenant

Scenario: The platform tenant is unconditionally uncapped   # M9
  Given the platform tenant (kind='platform', plan_id permanently NULL by DB constraint)
  When assert_seat_available is called for this tenant
  Then it returns immediately — no plan/seat_cap state could ever change this

# ── R1: invite-accept, the honest ACCEPT-not-ISSUANCE seam ───────────────────

Scenario: Accepting an invite at the tenant's seat cap is rejected   # R1
  Given a tenant assigned plan "starter" (seat_cap=5) with exactly 5 active users, and a
    pending, unexpired invite for a 6th member
  When the invitee calls POST /invites/{token}/accept {password}
  Then the response is 403 ERR_PLAN_SEAT_CAP_EXCEEDED
  And the invite's status remains 'pending', unflipped, and no users row was inserted

Scenario: Accepting an invite under the seat cap succeeds   # M2 (positive)
  Given the same tenant with 4 active users (one below its cap of 5)
  When the invitee calls POST /invites/{token}/accept {password}
  Then the response is 200, a new users row exists, and the invite is 'accepted'

Scenario: Issuing an invite is never gated by the seat cap, even already at cap   # framing (M2)
  Given a tenant already at its seat cap of 5
  When an owner calls POST /admin/invites to issue a 6th invite
  Then the invite is issued successfully (201) — the cap is consulted only at ACCEPT, never
    at issuance

# ── R2: OIDC/SAML JIT-provisioning — new-user branch only ────────────────────

Scenario: A brand-new SSO login at the tenant's seat cap is rejected   # R2
  Given a tenant assigned plan "team" (seat_cap=20) with exactly 20 active users, and an OIDC
    assertion for an email with NO existing users row
  When the identity provider redirects to GET /admin/auth/oidc/callback
  Then the response is 403 ERR_PLAN_SEAT_CAP_EXCEEDED
  And no users row was inserted for that email

Scenario: An EXISTING member's SSO re-login is never gated by the seat cap   # M7
  Given the same at-cap tenant, and an OIDC assertion for an email that ALREADY has a users
    row in this tenant
  When the identity provider redirects to GET /admin/auth/oidc/callback
  Then the response is 200 (login succeeds) — assert_seat_available is never even called,
    because the existing-user branch returns before it

Scenario: A brand-new SAML login at the tenant's seat cap is rejected identically   # R2
  Given the same at-cap tenant and a SAML assertion for an email with no existing users row
  When POST /admin/auth/saml/acs is called
  Then the response is 403 ERR_PLAN_SEAT_CAP_EXCEEDED, identical in shape to the OIDC case

# ── R3: domain-capture auto-join signup ───────────────────────────────────────

Scenario: A verified-domain signup at the tenant's seat cap is rejected   # R3
  Given a tenant with a verified domain claim for "corp.example", assigned plan "starter"
    (seat_cap=5), already at 5 active users
  When a new user signs up via POST /admin/auth/signup with email "new@corp.example"
  Then the response is 403 ERR_PLAN_SEAT_CAP_EXCEEDED
  And no users row was inserted, and the tenant's plan_id/seat_cap are unchanged

Scenario: Brand-new tenant signup (no verified domain match) is never capped   # ruled out, M2
  Given a signup email with no verified domain claim anywhere
  When POST /admin/auth/signup creates a BRAND-NEW tenant + owner
  Then the response is 201 — the new tenant's plan_id is NULL (plan-catalog M2), uncapped by
    construction; assert_seat_available is never even consulted on this branch

# ── R4: SCIM provisioning ─────────────────────────────────────────────────────

Scenario: SCIM user creation at the tenant's seat cap is rejected   # R4
  Given a tenant assigned plan "team" (seat_cap=20) with exactly 20 active users
  When an IdP calls POST /scim/v2/Users {"userName": "new@corp.example", "active": true}
    bearing that tenant's SCIM token
  Then the response is a 403 SCIM Error, status:"403", no scimType, detail naming the seat cap
  And no users row was inserted, and the SCIM token remains valid and unaffected

Scenario: SCIM user creation under the seat cap succeeds exactly as before this task   # M2
  Given the same tenant with 19 active users
  When the same POST /scim/v2/Users call is made
  Then the response is 201 SCIM User — byte-identical to pre-task behavior

# ── M6: rejection leaves everything else byte-identical ──────────────────────

Scenario: A rejected admission at any seam never fires an audit event   # M6
  Given any of the 4 at-cap rejection scenarios above
  When the rejection is returned
  Then no audit_events row was written by the rejecting call site
  And every OTHER tenant's users/plans/tenants rows are completely unaffected

# ── M4/concurrency: the lock actually serializes admissions ──────────────────

Scenario: Two concurrent admissions racing for the last remaining seat — exactly one wins   # M4
  Given a tenant assigned plan "starter" (seat_cap=5) with exactly 4 active users (one seat
    remaining), a pending invite AND a fresh OIDC assertion for two DIFFERENT new emails,
    both attempted at the same instant
  When POST /invites/{token}/accept and GET /admin/auth/oidc/callback are issued concurrently
  Then exactly ONE succeeds (200, a new users row for that email) and the other is rejected
    (403 ERR_PLAN_SEAT_CAP_EXCEEDED) — never both succeeding (6 active users, over cap) and
    never both failing (a real seat left unfilled while both callers saw a false rejection)

# ── M7: no retroactive lockout, ever ──────────────────────────────────────────

Scenario: A plan downgrade below current headcount does not deactivate anyone   # M7
  Given a tenant assigned plan "team" (seat_cap=20) with 15 active users
  When a superadmin calls PUT /admin/platform/tenants/{tenant_id}/plan to downgrade to
    "starter" (seat_cap=5)
  Then the downgrade succeeds (plan-catalog's own M6, unmodified by this task) and all 15
    users remain active, unauthenticated by nothing new
  And the tenant's active-member count (15) now EXCEEDS its new effective cap (5) — but no
    scan, sweep, or deactivation runs; this state persists indefinitely

Scenario: The over-cap tenant from above is blocked from admitting a 16th member   # M7, R1-R4
  Given the same over-cap tenant (15 active users, effective seat_cap now 5)
  When ANY of the 4 admission seams is attempted for a brand-new member
  Then it is rejected (the appropriate one of R1-R4) — the cap applies to the NEXT admission,
    never retroactively to the 15 already present

Scenario: Raising the seat cap unblocks the next admission immediately   # M2 (positive, live-read)
  Given the over-cap tenant from above (15 active users, seat_cap=5)
  When a superadmin raises tenants.seat_cap to 20 via PUT .../plan
  Then the very next admission attempt at any of the 4 seams is evaluated against 20, not 5 —
    no caching layer, always-live read (mirrors plan-enforcement's own always-live-read property)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: FROZEN @ v1 — approved by Tin Dang
Least-sure flag surfaced at freeze: [spec] §1's top ⚠ — the seat COUNT DEFINITION (active
  `UserRow` per tenant, `deactivated_at IS NULL`) is this task's own PROPOSAL, not a confirmed
  match to `seat-billing`'s own definition (its TASK.md is still template-empty — nothing exists
  yet to align against). If `seat-billing` lands on a different count, the fix is additive and
  fully contained to `assert_seat_available`'s own WHERE clause — no route, error code, or
  call-site wiring in this contract changes. Second-tier flag: the per-call-site transaction
  mechanics (§0 Issues/Risks, §1 second ⚠) — 3 different pre-existing transaction shapes, one
  (SCIM's `create_user`) with no explicit transaction today — is a build-time risk, not a shape
  risk; contained to this task, no contract exposure either way.

DECIDED at freeze review (2026-07-12, Tin + orchestrator): seat COUNT DEFINITION CONFIRMED as active
`UserRow` per tenant (`deactivated_at IS NULL`) — now aligned with `seat-billing`'s frozen-same-day
design: the `seat_membership_events` ledger (Tin-approved) is the BILLING history; the cap gates
ADMISSION on the current active count, same "active member = one seat" unit, no conflict. 403 for
`ERR_PLAN_SEAT_CAP_EXCEEDED` confirmed.

```
# ── M1: pure precedence extension (domain layer, zero infra imports) ─────────
# MODIFIED (additive only): gateway/tenants/domain/entitlements.py

@dataclass(frozen=True, slots=True)
class ResolvedEntitlements:
    effective_budget_usd_monthly: Decimal | None
    plan_model_allowlist: list[str] | None
    plan_feature_flags: frozenset[str]
    plan_id: uuid.UUID | None
    effective_seat_cap: int | None          # NEW (M1) — same precedence shape as budget

def resolve_entitlements(
    *,
    tenant_budget_usd_monthly: Decimal | None,
    plan_id: uuid.UUID | None,
    plan_budget_usd_monthly_default: Decimal | None,
    plan_model_allowlist: list[str] | None,
    plan_feature_flags: list[str] | None,
    tenant_seat_cap: int | None = None,             # NEW (M1), optional — 3 existing callers
    plan_seat_cap_default: int | None = None,        # NEW (M1), optional — unaffected untouched
) -> ResolvedEntitlements:
    """... existing docstring, plus: effective_seat_cap precedence mirrors budget exactly —
    tenant_seat_cap if not None, else plan_seat_cap_default, else None (unlimited)."""

# ── M2: the admission-time check (application layer — NEW file addition) ─────
# MODIFIED (additive): gateway/tenants/application/entitlements.py (sibling to check_plan_feature)

async def assert_seat_available(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Raise SeatCapExceededError iff admitting one more ACTIVE member would meet-or-exceed
    the tenant's effective seat cap. MUST be called inside the caller's OWN open transaction,
    strictly before its member-creating INSERT (M3) — the FOR UPDATE OF t lock is held only
    for the remainder of THAT transaction.
    Query 1 (always): SELECT t.seat_cap, t.plan_id, p.seat_cap, p.name
                       FROM tenants t LEFT JOIN plans p ON t.plan_id = p.id
                       WHERE t.id = :tid FOR UPDATE OF t
    -> resolve_entitlements(..., tenant_seat_cap=row.seat_cap, plan_seat_cap_default=row.p_seat_cap)
    -> effective_seat_cap is None: return (grandfathered-unlimited, M8/M9 — NO query 2 issued)
    Query 2 (only if capped): SELECT COUNT(*) FROM users
                              WHERE tenant_id = :tid AND deactivated_at IS NULL
    -> current_seats >= effective_seat_cap: raise SeatCapExceededError(plan_id, plan_name,
       seat_cap=effective_seat_cap, current_seats)
    -> else: return
    """

# ── M2: new domain error (carries structured data — 2 incompatible envelopes consume it) ─
# NEW: gateway/tenants/domain/errors.py (sibling to InviteExpiredError)
class SeatCapExceededError(IdentityError):
    def __init__(self, *, plan_id: uuid.UUID, plan_name: str, seat_cap: int, current_seats: int): ...
    # attributes: .plan_id  .plan_name  .seat_cap  .current_seats

# ── M5/R1-R3: RFC 9457 envelope (4 routers, reused ErrorSpec) ────────────────
# NEW: gateway/core/error_catalog.py (sibling to PLAN_MODEL_NOT_ALLOWED/PLAN_FEATURE_NOT_ENABLED)
PLAN_SEAT_CAP_EXCEEDED = ErrorSpec(
    403, "ERR_PLAN_SEAT_CAP_EXCEEDED", "Adding this member would exceed the tenant's seat cap"
)
  403 -> { code: "ERR_PLAN_SEAT_CAP_EXCEEDED",
           extra: { upgrade_hint: { plan_id: uuid, plan_name: string, seat_cap: int,
                                     current_seats: int } } }

# Call sites (each an ADDITIVE except-clause on an existing, already-shipped router — a
# superseding addition recorded HERE, no frozen sibling TASK.md is edited):
POST /invites/{token}/accept                     (tenants/api/invite_accept_router.py)
  ... existing except chain ...
  except SeatCapExceededError: raise PLAN_SEAT_CAP_EXCEEDED.exc(extra={upgrade_hint}) from None

GET  /admin/auth/oidc/callback                   (auth/api/oidc_router.py, after :288-289)
POST /admin/auth/saml/acs                        (auth/api/saml_router.py, after :136)
POST /admin/auth/signup                          (tenants/api/router.py, domain-capture branch,
                                                   after :76 `except EmailAlreadyRegisteredError`)
  ... each gets the identical new except clause above ...

# ── M5/R4: RFC 7644 envelope (SCIM router only — deliberate, documented exception) ────
# NEW: gateway/scim/api/errors.py (sibling to scim_uniqueness)
def scim_seat_cap_exceeded() -> ScimApiError:
    """403 — admitting this SCIM user would exceed the tenant's seat cap. No scimType —
    RFC 7644's vocabulary has no clean fit (mirrors scim_invalid_token()'s own precedent)."""
    return ScimApiError(403, "Seat cap exceeded for this tenant's plan")
  403 -> { schemas: ["urn:ietf:params:scim:api:messages:2.0:Error"], status: "403",
           detail: "Seat cap exceeded for this tenant's plan" }

POST /scim/v2/Users        (scim/api/scim_router.py:272-295)
  ... existing "except ScimUniquenessError: raise scim_uniqueness() from None" ...
  except SeatCapExceededError: raise scim_seat_cap_exceeded() from None

Schema: NO migration. `plans.seat_cap` / `tenants.seat_cap` (both shipped, plan-catalog
  FROZEN@v1) and `users.deactivated_at` (shipped, scim-provisioning) already exist and are
  reused verbatim — this task is application-code-only wiring.
  Access pattern (per admission attempt, only at the 4 named seams — never the hot proxy path):
    assert_seat_available: 1 locked SELECT (always) + 1 COUNT (only if the tenant is capped) —
    0 extra queries for the (currently 100%) uncapped/unplanned tenant population beyond the
    one locked SELECT itself.

# ── M4: caller integration points (additive edits to 4 already-shipped files) ─────────
InviteRepository.accept (tenants/infrastructure/invite_repository.py:237-314)
  -> insert `await assert_seat_available(self._session, invite.tenant_id)` after the
     pending/not-expired checks (steps 2-3), strictly before `INSERT users` (step 4)

_get_or_provision_sso_user (tenants/infrastructure/repository.py:174-232)
  -> insert the same call in the "provision new user" branch ONLY (:208-232), immediately
     before `self._session.add(new_user)` — reuses the branch's ALREADY-open (autobegin)
     transaction; on SeatCapExceededError, explicit `await self._session.rollback()` before
     re-raising (this branch has no existing try/except to piggyback on)

join_verified_tenant_domain (tenants/infrastructure/repository.py:88-112)
  -> insert the same call as the FIRST statement inside the existing
     `async with self._session.begin():` block, before `self._session.add(user)`

SqlAlchemyScimUserRepository.create_user (scim/infrastructure/repository.py:147-166)
  -> restructure into `async with self._session.begin(): await assert_seat_available(...);
     self._session.add(row)`, catching IntegrityError -> ScimUniquenessError as today,
     SeatCapExceededError propagating unchanged (caught by the router, M5)
```

Glossary deltas:
  - **Seat**: one active (`UserRow.deactivated_at IS NULL`) member account under a tenant —
    proposed here, NOT yet confirmed against `seat-billing`'s own eventual definition (§1 top ⚠,
    flagged for freeze).
  - **Effective seat cap**: the enforced ceiling on a tenant's active member count — explicit
    `tenants.seat_cap` override if set, else the assigned plan's `plans.seat_cap` default, else
    unlimited (`None`) — identical precedence shape to `plan-enforcement`'s own "plan default"
    Glossary term, applied to the seat dimension.
  - **Seat admission**: the moment a NEW active member materializes (one of the 4 named INSERT
    seams) — the ONLY moment this task's cap is ever consulted; distinct from any later state
    change to an already-admitted member (re-login, role change, plan downgrade — none of which
    re-trigger a cap check, per M7).
Reported: no — drafted for the design-span freeze review; Tin reviews this contract per the
  standard one-human-approval-at-freeze gate.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (mirrors plan-enforcement/domain-capture's own security/data-task bar)
Plan (one test per scenario, asserting behavior not internals; 23 tests total — 21 scenarios
+ 2 extra direct-unit tests on `assert_seat_available`'s own raise/no-raise core, since it is
a new shared helper with no prior test coverage of its own):
<test_plan>
  - test_explicit_tenant_seat_cap_beats_plan_default: pure resolve_entitlements precedence · M1
  - test_plan_seat_cap_default_fills_gap_when_no_tenant_override: pure precedence · M1
  - test_existing_caller_unaffected_by_the_new_kwargs: RedisBudgetGuard-shaped call byte-identical · M1
  - test_unplanned_tenant_uncapped_no_count_query: SQL-capture proves 0 COUNT query · M2, M8
  - test_planned_tenant_both_seat_caps_null_is_uncapped: SQL-capture, 0 COUNT query · M2, M8
  - test_platform_tenant_unconditionally_uncapped: SQL-capture, 0 COUNT query · M9
  - test_at_cap_raises_seat_cap_exceeded: direct assert_seat_available raise + structured fields · M2
  - test_under_cap_returns_none: direct assert_seat_available no-raise · M2
  - test_accepting_invite_at_seat_cap_is_rejected: 403 + invite stays pending + no row · R1
  - test_accepting_invite_under_seat_cap_succeeds: 200 + row + invite accepted · M2 (positive)
  - test_issuing_invite_never_gated_by_seat_cap: issuance at-cap still 201 · framing (M2)
  - test_new_oidc_login_at_seat_cap_is_rejected: 403 + no users row · R2
  - test_existing_member_oidc_relogin_never_gated_by_seat_cap: 302, count unchanged · M7
  - test_new_saml_login_at_seat_cap_is_rejected: 403 identical shape to OIDC · R2
  - test_verified_domain_signup_at_seat_cap_is_rejected: 403 + no row + tenant unchanged · R3
  - test_brand_new_tenant_signup_never_capped: 201, uncapped by construction · ruled out (M2)
  - test_scim_create_at_seat_cap_is_rejected: RFC 7644 403 no scimType + token still valid · R4
  - test_scim_create_under_seat_cap_succeeds_unchanged: 201 byte-identical · M2 (positive)
  - test_rejected_admission_fires_no_audit_event: 0 new invite.accept rows + other tenant untouched · M6
  - test_concurrent_admissions_racing_the_last_seat_exactly_one_wins: invite+OIDC asyncio.gather, exactly one 200/one 403, real Postgres FOR UPDATE OF t · M4/concurrency
  - test_plan_downgrade_below_headcount_does_not_deactivate_anyone: 15 stay active · M7
  - test_over_cap_tenant_blocked_from_admitting_a_16th_member: 403, still 15 · M7, R1-R4
  - test_raising_seat_cap_unblocks_the_next_admission_immediately: 403 then 200 after raise, live-read · M2 (positive)
</test_plan>

Tests live in: `apps/gateway/tests/plan_seat_cap/` · MUST run red (missing implementation)
before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/domain/entitlements.py`
  `apps/gateway/src/gateway/tenants/domain/errors.py`
  `apps/gateway/src/gateway/tenants/application/entitlements.py`
  `apps/gateway/src/gateway/tenants/infrastructure/invite_repository.py`
  `apps/gateway/src/gateway/tenants/infrastructure/repository.py`
  `apps/gateway/src/gateway/tenants/api/invite_accept_router.py`
  `apps/gateway/src/gateway/tenants/api/router.py`
  `apps/gateway/src/gateway/auth/api/oidc_router.py`
  `apps/gateway/src/gateway/auth/api/saml_router.py`
  `apps/gateway/src/gateway/scim/infrastructure/repository.py`
  `apps/gateway/src/gateway/scim/api/scim_router.py`
  `apps/gateway/src/gateway/scim/api/errors.py`
  `apps/gateway/src/gateway/core/error_catalog.py`
  `apps/gateway/tests/`
Strategy (ordered batches):
  1. Domain core first: extend `ResolvedEntitlements`/`resolve_entitlements` (M1) — pure, zero
     infra imports, unit-testable in isolation; add `SeatCapExceededError` to `domain/errors.py`.
     Confirm the 3 EXISTING callers of `resolve_entitlements` (RedisBudgetGuard,
     `SqlAlchemyPlanEntitlementResolver.resolve`'s 2 call sites) are byte-identical, unmodified,
     still green.
  2. `assert_seat_available` (M2) in `tenants/application/entitlements.py`, sibling to
     `check_plan_feature` — unit-test the locked-query + resolve + count-and-compare logic against
     a real Postgres (row locking is not meaningfully fakeable), BEFORE wiring any call site.
  3. `error_catalog.py`: `PLAN_SEAT_CAP_EXCEEDED` (mirrors the existing `PLAN_MODEL_NOT_ALLOWED`/
     `PLAN_FEATURE_NOT_ENABLED` section). `scim/api/errors.py`: `scim_seat_cap_exceeded()`.
  4. Wire the 4 call sites ONE AT A TIME, each with its own red->green scenario, in ascending
     order of transaction-mechanics risk (§0 Issues/Risks) — safest first: (a)
     `join_verified_tenant_domain` (already has a clean `async with begin():` block — lowest
     risk) -> (b) `InviteRepository.accept` (already locks a second row — composability, not
     novelty) -> (c) `_get_or_provision_sso_user` (the commented autobegin quirk — highest risk of
     the three infra-layer sites, test BOTH the OIDC and SAML call paths since they share this one
     method) -> (d) `SqlAlchemyScimUserRepository.create_user` (introduces the method's first-ever
     explicit transaction — write the concurrency scenario against THIS seam specifically, since
     it is the newest transaction shape and the likeliest to hide a mistake).
  5. Router-level except-clauses (M5) land in the SAME batch as each call site above, not deferred
     — a call site with no router translation is an unreachable/incomplete Must.
  6. Concurrency scenario (two admissions racing the last seat) LAST, against real Postgres row
     locking, spanning two of the wired seams (mirrors §2's own scenario pairing) — this is the
     proof the `FOR UPDATE OF t` design actually works, not merely compiles.

Persona (required): backend-architect (`.add/personas/backend-architect.md`) — Protocol-port /
  layering discipline (the domain-layer precedence extension in `entitlements.py` stays zero-infra;
  `assert_seat_available` lives in `application/`, never `domain/`, exactly mirroring
  `check_plan_feature`'s own placement) PLUS this task's own SaaS-entitlements-architect stance
  (no persona file yet exists for that narrower lens — backend-architect's layering discipline is
  the closest fit; the persona's OWN load-bearing rule for this task, stated in §1 M7, is "a cap is
  enforced at admission, never by retroactive lockout").
Spawn isolation (default): worktree — no stated reason to share the tree.
Known-problem fixes:
  - `_get_or_provision_sso_user`'s commented SQLAlchemy autobegin trap ("calling begin() again
    raises InvalidRequestError") -> reuse the branch's already-open transaction, never call
    `session.begin()` inside it.
  - `SqlAlchemyScimUserRepository.create_user` has no explicit transaction today -> introduce
    `async with self._session.begin():` for the FIRST time on this method; verify the existing
    `IntegrityError -> ScimUniquenessError` catch still fires correctly once wrapped.
  - Lock-then-count ordering: the `FOR UPDATE OF t` SELECT MUST run and complete BEFORE the COUNT
    query, and the COUNT MUST run before the INSERT — reordering any of the 3 defeats the
    serialization guarantee silently (no exception, just a race that only shows under the
    concurrency scenario).
Strategy actually used: followed the drafted §5 Strategy almost exactly, with ONE material
  deviation discovered at Build, not planned for: `SqlAlchemyScimUserRepository.create_user`
  could NOT use the contract's own illustrative `async with self._session.begin():` snippet
  verbatim — a real `sqlalchemy.exc.InvalidRequestError: A transaction is already begun on
  this Session` at first test run, because `get_scim_identity` (the SCIM bearer-auth
  dependency) already issues its OWN SELECT on the SAME request-scoped session before
  `create_user` runs, autobeginning a transaction. Fixed by reusing that already-open
  transaction (flush()+commit(), mirroring `InviteRepository.accept`'s own shape) instead of
  calling `begin()` a second time — functionally identical to the CONTRACT's own guarantee
  (M3: the `FOR UPDATE OF t` lock held continuously from the check through the INSERT, one
  transaction) since autobegin transactions persist across statements on one session exactly
  like an explicit one; only the literal begin()-call mechanism differs from the contract's
  own snippet. No route/error-code/shape change — recorded here per §1's own §0 Issues/Risks
  (c) flag ("a build-time bug ... caught by the concurrency scenario's own test — contained
  to this task, no contract change"), which correctly predicted a mechanics risk at exactly
  this call site. Order followed: (1) domain M1 + SeatCapExceededError, confirmed the 3
  existing resolve_entitlements callers green, (2) assert_seat_available unit-tested directly
  against real Postgres BEFORE any call site, (3) error_catalog + scim errors, (4) wired the 4
  seams in the drafted risk order (domain-capture -> invite-accept -> OIDC/SAML -> SCIM),
  router except-clause landing in the SAME batch as each seam, (5) concurrency scenario last,
  confirmed non-flaky across 4 repeated runs. Every sibling suite re-run green after each
  seam (member_invite_acceptance, member_invite_issuance, domain_capture, scim_provisioning,
  sso_oidc, saml_sso, plan_catalog, plan_enforcement — 236 tests total, zero regressions).
Safety rule (feature-specific): lock (`FOR UPDATE OF t`) + count + insert in ONE atomic
  transaction per admission attempt, at each of the 4 seams — no read-then-write gap, ever.
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new
  dependency — every symbol touched already exists in this codebase); do NOT edit
  `member-invite-acceptance`/`domain-capture`/`scim-provisioning`/`plan-enforcement`'s own frozen
  TASK.md files — every additive change to their shipped code is recorded in THIS file only; ask
  if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] A tenant AT its effective seat cap (explicit tenant.seat_cap, or inherited plan
      default) is rejected with 403 `ERR_PLAN_SEAT_CAP_EXCEEDED` (RFC 9457,
      `extra.upgrade_hint` carrying plan_id/plan_name/seat_cap/current_seats) at 3 seams —
      invite-accept, OIDC callback, SAML ACS, verified-domain signup — and a 403 RFC 7644
      SCIM error (no scimType) at `/scim/v2/Users` — confirmed by
      `tests/plan_seat_cap/test_invite_accept_seat_cap.py`,
      `test_sso_seat_cap.py`, `test_domain_capture_seat_cap.py`, `test_scim_seat_cap.py`.
- [x] A rejected admission leaves the `invites`/`tenants` rows byte-identical (no status
      flip, no plan/seat_cap mutation) and inserts ZERO `users` rows — confirmed by direct
      SQL assertions in every reject test + `test_seat_cap_audit_and_concurrency.py`'s own
      0-audit-event assertion.
- [x] An EXISTING member's OIDC/SAML re-login is NEVER gated by the cap (the existing-user
      branch returns before `assert_seat_available` is ever reached) — confirmed by
      `test_existing_member_oidc_relogin_never_gated_by_seat_cap` (302, headcount
      unchanged).
- [x] An uncapped/unplanned/platform tenant issues ZERO `COUNT(*) FROM users` queries
      (grandfathered-unlimited, M8/M9) — confirmed OBSERVABLY via a real
      `before_cursor_execute` SQL-capture hook on the live engine, not an internal mock
      (`test_assert_seat_available.py`'s 3 uncapped-path tests).
- [x] A plan downgrade / seat_cap lowering below current headcount NEVER deactivates or
      touches existing members, and the resulting over-cap state persists indefinitely
      until the NEXT admission attempt is rejected — confirmed by
      `test_seat_cap_no_retroactive_lockout.py`'s 3 tests (15 stay active post-downgrade;
      16th rejected; raising the cap unblocks the very next attempt, live-read).
- [x] Two concurrent admissions racing the LAST remaining seat (real Postgres, `FOR UPDATE
      OF t`, invite-accept vs. OIDC callback via `asyncio.gather`) resolve to EXACTLY one
      200 and one 403 — never both succeeding (over-cap), never both failing (a real seat
      left unfilled) — confirmed by
      `test_concurrent_admissions_racing_the_last_seat_exactly_one_wins`, re-run 4x
      non-flaky.
- [x] Zero migration added; `plans.seat_cap`/`tenants.seat_cap`/`users.deactivated_at`
      reused verbatim — confirmed by `git status` (no `alembic/versions/` diff) + every
      test running against `create_all()`-only schemas.
- [x] Every EXISTING sibling suite this task's changes touch stays green, unmodified —
      confirmed by re-running member_invite_acceptance, member_invite_issuance,
      domain_capture, scim_provisioning, sso_oidc, saml_sso, plan_catalog, plan_enforcement
      (236 tests) after each seam landed, zero regressions.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. The Advisor 3-lens verdict and the Refute-read verdict are both measured by `add.py audit` (`advisor_verdict_unrecorded` · `refute_unrecorded`) — neither is engine-blocked; a human spot-audit is the backstop for any finding the AI did not surface or record. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
