# TASK: Platform tenant directory — superadmin list/search/view any tenant

slug: platform-tenant-directory · created: 2026-07-03 · stage: production
milestone: platform-admin-console
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - tenants/domain/authz.py:authorize_tenant_scope(identity, target_tenant_id) -> None — dormant
    predicate (403 unless SUPERADMIN or same-tenant); this task wires its first real caller.
  - tenants/domain/authz.py:Permission, ROLE_PERMISSIONS, require_permission — existing
    capability-gate; SUPERADMIN already holds every Permission, but no Permission models
    "list/view across ALL tenants" (orthogonal axis — see Issues/Risks).
  - tenants/domain/entities.py:Role.SUPERADMIN, Identity(tenant_id, user_id, email, role) —
    the role + identity dataclass used throughout.
  - tenants/infrastructure/orm.py:TenantRow — id, name, kind, created_at, updated_at (directory
    summary fields) + markup_pct/budget_usd_monthly/cache_enabled/guardrail_configs/
    semantic_cache_enabled (deeper config fields — out of THIS task's response shape, owned by
    cross-tenant-config-budget).
  - tenants/infrastructure/repository.py:get_platform_tenant, SqlAlchemyIdentityRepository —
    existing repository surface; NO list-all-tenants method exists yet — this task adds one.
  - tenants/api/users_router.py (whole file) — closest existing analog: list_users /
    UserResponse / UsersListResponse pattern (single-tenant, non-paginated) to mirror for
    response-shape and route conventions.
  - tenants/api/deps.py — existing dependency-factory pattern (get_hasher, get_token_service,
    etc.) — sibling location for any new dep this task needs.
  - core/error_catalog.py:AUTH_FORBIDDEN — the 403 authorize_tenant_scope already raises; reused as-is.
  - audit/application/audit_writer.py:record_audit, audit/domain/audit_event.py:AuditEvent —
    fire-and-forget audit primitive (mirrored by users_router.assign_user_role) — reused
    unchanged IF this task's reads land in audit scope (see Issues/Risks).
Context (working folder): .add/milestones/platform-admin-console/MILESTONE.md (owning milestone,
  drafted this session — names this task as the authorize_tenant_scope first-caller and the
  owner of the "cross-tenant READ shape" risky contract); .add/CONVENTIONS.md grepped for an
  existing pagination/search convention — zero hits, this task sets the project's first one; no
  pyproject/package.json changes expected.
Honors: authorize_tenant_scope's own contract (FROZEN @ v1, superadmin-role TASK.md §3) — call
  it, never reimplement the check; ROLE_PERMISSIONS "allowlist" semantics (rbac-roles TASK.md
  §3, FROZEN @ v1) — a Permission says nothing about WHICH tenant, don't conflate one with
  cross-tenant reach; every repository defaults to `WHERE tenant_id = identity.tenant_id`
  (authz.py docstring) — the new list-all-tenants method is a deliberate, narrow, superadmin-only
  exception; audit fire-and-forget/fail-open pattern (superadmin-audit-foundation) if reads are
  audited here.
Anchors the contract cites: authorize_tenant_scope(identity, target_tenant_id); Role.SUPERADMIN;
  TenantRow(id, name, kind, created_at); get_platform_tenant() (sibling lookup convention);
  AUTH_FORBIDDEN.
Issues/Risks (→ feed §1):
  ⚠ no existing Permission models "list/view across ALL tenants" — require_permission alone is
    the wrong shape (answers "which capability", not "which tenant scope"). §1 must decide: a
    direct `identity.role == Role.SUPERADMIN` gate, or a new Permission. Lowest-confidence item
    for this task.
  - no pagination/search convention exists anywhere in the codebase (CONVENTIONS.md grep: zero
    hits) — this task sets the first one; keep it minimal (limit/offset + name substring) rather
    than over-engineer cursor-pagination for a currently-small tenant count.
  - MILESTONE.md's Exit criteria commit to auditing "every cross-tenant READ/write", but its
    Tasks list only wires admin-console-audit's depends-on to tasks 2+3 (the write-capable
    ones), omitting this task (a read). §1 must decide explicitly whether "view one tenant"
    (not the bare list) emits an audit row now vs. is deferred to the audit task's retrofit pass
    — leaning retrofit, matching platform-identity's own capability-first-audit-second
    precedent, but this is a real milestone-doc inconsistency, not a silent call.
  - currently on branch `feat/platform-identity` (PR #56 open, not yet merged) — Build will need
    a branch decision (stack a new branch off this tip vs. wait for merge); does not block
    ground/specify/scenarios/contract.
Related intent: .add/milestones/platform-admin-console/MILESTONE.md Scope-In + the
  platform-tenant-directory task line + Exit criterion #1; authorize_tenant_scope's own
  docstring ("platform-admin-console wires the first real caller"); GLOSSARY gap noted in
  MILESTONE.md ("platform tenant"/"superadmin" never formally added) — this task's §3 Glossary
  deltas should add them plus "tenant directory".
Ground SHA: ccf411c (branch feat/platform-identity)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Platform tenant directory (superadmin cross-tenant list/search/view)
Framings weighed: dual-gate split (chosen) — a NEW `require_superadmin` role-only dependency
  gates the bulk list (no single target_tenant_id exists for a list, so `authorize_tenant_scope`
  doesn't fit it); the existing `authorize_tenant_scope(identity, tenant_id)` gates get-one
  (which DOES have a natural target) — this is the actual "first real caller" the milestone
  promised, more precisely than the bulk list could ever be · a single new Permission covering
  both endpoints (rejected — implies a non-superadmin role could meaningfully hold "list all
  tenants", which is never true; ROLE_PERMISSIONS models capability, not tenant-scope) · forcing
  the bulk list through `authorize_tenant_scope` via a synthetic/absent target (rejected — abuse
  of a frozen, single-target contract). Route shape: NEW nested routes under
  `/admin/platform/tenants` (chosen) · retrofitting all 14 existing `/admin/*` routers with an
  optional target-tenant query param (rejected — 14-router blast radius, real regression risk to
  already-shipped non-superadmin paths, for a milestone whose own Scope-Out forbids changing
  their behavior). This route-shape call resolves MILESTONE.md's "Shared/risky contract" #1 —
  tasks 2 and 3 follow this same nested-route precedent.
Must:
<must>
  - M1: GET /admin/platform/tenants returns a paginated list of ALL tenants (kind='customer' AND
    kind='platform') — SUPERADMIN callers only.
  - M2: each list entry = {id, name, kind, created_at} only — NOT markup_pct/
    budget_usd_monthly/cache_enabled/guardrail_configs/semantic_cache_enabled (those stay in
    cross-tenant-config-budget's response shape).
  - M3: supports `q` (case-insensitive name substring search) and `limit`/`offset`; default
    limit=50 if omitted.
  - M4: GET /admin/platform/tenants/{tenant_id} returns ONE tenant's directory-summary (same
    shape as M2) for ANY tenant_id — SUPERADMIN callers only; gated via
    `authorize_tenant_scope(identity, tenant_id)`.
  - M5: the bulk list (M1) is gated by a NEW `require_superadmin` dependency
    (`tenants/domain/authz.py`, sibling to `require_permission`) — role-only check, no target.
  - M6: a non-SUPERADMIN caller gets 403 on both endpoints regardless of their own tenant_id.
</must>
Reject:
<reject>
  - missing/invalid Bearer token -> "auth_token_missing" / "auth_token_invalid" (401 — existing
    AUTH_TOKEN_MISSING/AUTH_TOKEN_INVALID, unchanged mechanism, R1)
  - valid token, non-SUPERADMIN role -> "auth_forbidden" (403 — existing AUTH_FORBIDDEN, same
    error authorize_tenant_scope already raises, R2)
  - GET .../{tenant_id} where tenant_id does not exist -> "tenant_not_found" (404 — NEW error
    catalog entry; confirm at contract time no existing code already covers this, R3)
  - `limit` above max (200) -> clamp to 200, do NOT 400 (ergonomics — an overly-generous limit is
    not a client error, R4)
</reject>
After:
<after>
  - a SUPERADMIN caller enumerates/searches every tenant and opens any one tenant's summary,
    independent of their own tenant_id.
  - a non-SUPERADMIN caller's existing behavior is completely unchanged — this is a brand-new
    route tree, nothing pre-existing is touched.
  - `authorize_tenant_scope` gains its first real, production caller (M4) — dormant no longer.
  - this task's endpoints do NOT emit an audit row yet — see the ⚠ assumption below.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ audit is deferred entirely to admin-console-audit (task 4, now depends-on this task per the
    MILESTONE.md fix this session) rather than inlined into this task's build — lowest confidence
    because the milestone's own Exit criteria says "every cross-tenant READ/write" is audited,
    and a superadmin can browse/open any tenant's data with zero audit trail for however long
    task 4 takes to land; if wrong: this task's contract needs an audit clause added now (bigger
    build), rather than task 4 retrofitting it later (matches platform-identity's own
    capability-first-audit-second precedent, but that precedent was for WRITEs, not reads).
  - [ ] TENANT_NOT_FOUND is a genuinely new error_catalog.py entry, not a rename of an existing
    one — confirm by grep at contract time.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Superadmin lists every tenant across both kinds   # M1
  Given a platform tenant (kind=platform) and 3 customer tenants (kind=customer) exist
  When a SUPERADMIN identity calls GET /admin/platform/tenants
  Then the response includes all 4 tenants

Scenario: List entry exposes only the directory-summary fields   # M2
  Given a customer tenant with markup_pct=25.0 and budget_usd_monthly=500 configured
  When a SUPERADMIN identity calls GET /admin/platform/tenants
  Then that tenant's entry contains exactly {id, name, kind, created_at}
  And markup_pct/budget_usd_monthly/cache_enabled/guardrail_configs/semantic_cache_enabled are absent

Scenario: Search narrows by name substring; pagination limits the page   # M3
  Given tenants named "Acme Corp", "Acme Labs", "Globex"
  When a SUPERADMIN identity calls GET /admin/platform/tenants?q=acme&limit=1
  Then exactly 1 tenant is returned
  And its name contains "Acme" (case-insensitive)

Scenario: Superadmin opens any single tenant, not just their own   # M4
  Given a SUPERADMIN identity whose own tenant_id is T_super, and a customer tenant T_other
  When the SUPERADMIN calls GET /admin/platform/tenants/{T_other}
  Then the response is T_other's directory-summary
  And no 403 is raised despite T_other != T_super

Scenario: Bulk list rejects a non-superadmin even with full permissions in their own tenant   # M5, R2
  Given an OWNER identity (holds every Permission) for tenant T_owner
  When the OWNER calls GET /admin/platform/tenants
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And T_owner's own tenant-scoped surfaces (e.g. GET /admin/users) remain unaffected

Scenario: Get-one rejects a non-superadmin targeting a tenant that isn't their own   # M6, R2
  Given an OWNER identity for tenant T_owner, and a different tenant T_other
  When the OWNER calls GET /admin/platform/tenants/{T_other}
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And the same OWNER calling GET /admin/platform/tenants/{T_owner} (their own id) still 403s —
    this route tree is SUPERADMIN-only, unlike authorize_tenant_scope's general same-tenant allowance

Scenario: Missing bearer token is rejected   # R1
  Given no Authorization header is sent
  When the caller requests GET /admin/platform/tenants
  Then the response is 401 ERR_AUTH_INVALID_TOKEN (the single code shared by
    missing/malformed/expired — confirmed against error_catalog.py at contract time)
  And no tenant data is returned

Scenario: Get-one against a tenant_id that does not exist   # R3
  Given a SUPERADMIN identity and a tenant_id with no matching row
  When the SUPERADMIN calls GET /admin/platform/tenants/{tenant_id}
  Then the response is 404 ERR_TENANT_NOT_FOUND
  And no partial/placeholder tenant object is returned

Scenario: A limit above the maximum clamps rather than erroring   # R4
  Given 5 tenants exist
  When a SUPERADMIN identity calls GET /admin/platform/tenants?limit=9999
  Then the response is 200, not 400
  And at most 200 entries are returned (the clamp), not a validation error
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/platform/tenants   query: { q?: str, limit?: int=50, offset?: int=0 }
  200 -> { tenants: [{ id: uuid, name: str, kind: "customer"|"platform", created_at: datetime }],
           total: int }
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  # limit > 200 clamps server-side to 200 — never a 4xx (R4)

GET /admin/platform/tenants/{tenant_id}
  200 -> { id: uuid, name: str, kind: "customer"|"platform", created_at: datetime }
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_TENANT_NOT_FOUND" }

Schema: tenants table, READ-only, existing columns (id, name, kind, created_at) — no migration.
New symbols:
  - core/error_catalog.py:TENANT_NOT_FOUND = ErrorSpec(404, "ERR_TENANT_NOT_FOUND", "Tenant not
    found") — follows the USER_NOT_FOUND/MEMBER_NOT_FOUND pattern exactly.
  - tenants/domain/authz.py:require_superadmin() -> fastapi.Depends — sibling to
    require_permission; role-only check (identity.role == Role.SUPERADMIN), no Permission
    involved; raises AUTH_FORBIDDEN.exc() otherwise.
  - a new repository read method — container TBD at build (SqlAlchemyIdentityRepository or a
    standalone function beside get_platform_tenant, whichever the build finds cleaner; NOT
    contract-binding which container, only the behavior is):
    list_tenants(q: str | None, limit: int, offset: int) -> tuple[list[TenantRow], int]
```

Glossary deltas:
  - Platform tenant: the single reserved tenant (kind='platform', partial-unique-indexed)
    representing the platform operator itself, distinct from ordinary customer tenants.
  - Superadmin: Role.SUPERADMIN — a platform-tenant-only role with full Permission parity plus
    cross-tenant reach via authorize_tenant_scope. (Both retroactively fill the gap noted in
    MILESTONE.md — never formally added when platform-identity folded.)
  - Tenant directory: the superadmin-only, cross-tenant listing/lookup surface this task
    introduces (GET /admin/platform/tenants[/{tenant_id}]).
Least-sure flag surfaced at freeze:
  ⚠ [spec] this task's cross-tenant reads (list + get-one) emit NO audit row — auditing is
    deferred entirely to admin-console-audit (task 4, now depends-on this task). Low confidence
    because the milestone's own Exit criteria says "every cross-tenant READ/write" is audited,
    so a superadmin can browse/open any tenant's data with zero audit trail until task 4 lands;
    if wrong: task 4 needs no rework (its depends-on already covers this task), but the WINDOW
    of unaudited access between this task shipping and task 4 shipping is the accepted cost.
    APPROVED by Tin Dang at freeze (2026-07-03) — deferral confirmed, matches
    platform-identity's capability-first-audit-second precedent.
Status: FROZEN @ v1 — approved by Tin Dang (2026-07-03)

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_superadmin_lists_every_tenant_across_kinds: arrange platform + 3 customer tenants /
    act GET /admin/platform/tenants as SUPERADMIN / assert all 4 present · covers: M1
  - test_list_entry_is_directory_summary_shape_only: arrange a tenant w/ markup_pct+budget set /
    act GET list / assert entry == {id,name,kind,created_at}, deep-config fields absent · covers: M2
  - test_search_and_limit_narrow_results: arrange "Acme Corp"/"Acme Labs"/"Globex" / act GET
    ?q=acme&limit=1 / assert 1 result, name contains "Acme" · covers: M3
  - test_superadmin_opens_any_single_tenant: arrange SUPERADMIN (tenant T_super) + customer
    tenant T_other / act GET /{T_other} / assert 200 with T_other's summary · covers: M4
  - test_bulk_list_rejects_non_superadmin_owner: arrange OWNER identity (all Permissions, own
    tenant T_owner) / act GET list / assert 403 ERR_AUTH_FORBIDDEN + GET /admin/users for
    T_owner still 200 (unaffected) · covers: M5, R2
  - test_get_one_rejects_non_superadmin_targeting_other_tenant: arrange OWNER (T_owner) +
    different tenant T_other / act GET /{T_other} then GET /{T_owner} / assert both 403 ·
    covers: M6, R2
  - test_missing_bearer_token_rejected: arrange no Authorization header / act GET list / assert
    401 ERR_AUTH_INVALID_TOKEN, no tenant data · covers: R1
  - test_get_one_nonexistent_tenant_404s: arrange SUPERADMIN + a tenant_id with no row / act GET
    /{tenant_id} / assert 404 ERR_TENANT_NOT_FOUND · covers: R3
  - test_limit_above_max_clamps_not_errors: arrange 5 tenants / act GET ?limit=9999 / assert 200,
    <=200 entries, no validation error · covers: R4
</test_plan>

Tests live in: `apps/gateway/tests/platform_tenant_directory/` · MUST run red (missing implementation) before Build.
RED confirmed (2026-07-03): 9/9 failed — every failure is FastAPI's default 404 "Not Found"
(neither route registered yet), not a fixture/import error. Ran against an isolated DB
(`gateway_test_platform_tenant_directory_red`) via
`GATEWAY_TEST_DATABASE_URL=... uv run pytest tests/platform_tenant_directory/`.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/tenants/domain/authz.py`
  `apps/gateway/src/gateway/tenants/infrastructure/repository.py`
  `apps/gateway/src/gateway/tenants/api/platform_tenants_router.py`
  `apps/gateway/src/gateway/core/error_catalog.py`
  `apps/gateway/src/gateway/main.py`
Strategy (ordered batches):
  1. core/error_catalog.py — add TENANT_NOT_FOUND (ErrorSpec, mirrors USER_NOT_FOUND/MEMBER_NOT_FOUND).
  2. tenants/domain/authz.py — add require_superadmin() dependency factory, sibling to
     require_permission (role-only: identity.role == Role.SUPERADMIN, else AUTH_FORBIDDEN.exc());
     add to __all__.
  3. tenants/infrastructure/repository.py — add list_tenants(q, limit, offset) ->
     tuple[list[TenantRow], int]; a get_tenant_by_id-style single lookup for the get-one route
     (may already exist under another name — check before adding a duplicate).
  4. tenants/api/platform_tenants_router.py (NEW) — GET list (require_superadmin) + GET one
     (authorize_tenant_scope(identity, tenant_id)), wired to batches 1-3, response DTOs mirroring
     users_router.py's UserResponse/UsersListResponse naming convention.
  5. main.py — app.include_router(platform_tenants_router), mirroring users_router's registration
     (main.py:974).

Persona (optional): backend-expert stance (FastAPI + repository pattern) — no dedicated persona
  file exists for this domain; generic.
Known-problem fixes:
  - trap: accidentally returning the full TenantRow (leaking markup_pct/budget_usd_monthly/etc.)
    instead of the M2-restricted summary shape -> fix: a dedicated response DTO, never
    `TenantRow.__dict__` or a broad existing schema.
  - trap: gating the bulk list with authorize_tenant_scope by improvising a target (e.g. the
    caller's own tenant_id) -> fix: bulk list uses require_superadmin only, no target ever
    passed to authorize_tenant_scope for that route (§1 Framings weighed already rejected this).
Strategy actually used: exactly as planned, zero deviation — the 5 ordered batches (error_catalog
  TENANT_NOT_FOUND, authz.require_superadmin, repository.list_tenants+get_tenant_by_id, new
  platform_tenants_router.py, main.py registration) were built and went green in one pass. One
  batch-3 detail resolved during build, not pre-specified: the repository method container was
  "TBD at build" per §3 — landed as two module-level functions (list_tenants, get_tenant_by_id)
  sibling to get_platform_tenant, NOT methods on SqlAlchemyIdentityRepository (that class is
  identity/user provisioning, a different concern from tenant lookups).
Safety rule (feature-specific): read-only task (no writes to tenant state) — no transactional
  safety rule applies; the only invariant is that the two auth gates (require_superadmin vs.
  authorize_tenant_scope) are never swapped between the list and get-one routes.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 9/9 platform_tenant_directory + 54/54 across 6 sibling suites touching
      the same 4 files (superadmin_role, superadmin_login, ops_platform_job_identity,
      rbac_roles, superadmin_audit_foundation, platform_tenant_seed). Full-suite run:
      2264 passed, 7 skipped, 28 deselected, 3 failed — 0 errors (a full
      `gateway_migrations_test_<suffix>` DB pre-create up front avoided the migrations-conftest
      DB-naming gotcha this time). Triage of all 3 failures:
        1. test_guardrails_core_migration_column_exists — reproduces deterministically in
           isolation (unexpected: ['batch_jobs','batch_job_items'] missing from the guardrails
           allow-list). Pre-existing, unrelated: this task never touches migrations/tables;
           exact match to a defect already documented from earlier this session.
        2. test_admin_usage_totals_and_records — PASSED clean when re-run in isolation (paired
           only with the other 2). Cross-test ordering artifact, not a real failure.
        3. test_spend_counter_not_incremented_on_cache_hit — flakes on Redis NOGROUP
           ("No such key 'usage:events' or consumer group 'ledger-flusher'"), a documented
           startup race in the flusher's lazy `xgroup_create(..., mkstream=True)`
           (tests/conftest.py's own comments name this exact failure mode and explain why a
           blanket FLUSHDB is deliberately avoided). Rigorously isolated from this task's
           changes via A/B: `git stash` this task's 5 changed paths, ran the single test 3x on
           the CLEAN base — result pass/fail/fail, i.e. the SAME flake with ZERO code from this
           task present. Restored the stash afterward (verified via `git status`); this task's
           own 9/9 re-confirmed green post-restore. Conclusively pre-existing, unrelated —
           timing-dependent on Redis consumer-group startup, orthogonal to tenants/authz/router
           code this task touches.
      None of the 3 failures touch tenants/, authz.py, repository.py, error_catalog.py, or
      main.py's router registration.
- [x] coverage did not decrease — this task is purely additive (2 new symbols + 1 new file +
      1 new error code + 1 new router registration); nothing existing was removed or narrowed
- [x] no test or contract was altered during build — `git diff` on
      tests/platform_tenant_directory/ and TASK.md §3 shows zero changes since freeze
- [x] the green was EARNED — see Refute-read verdict below
- [x] concurrency / timing of the risky operation is safe — pure read-only queries, no writes,
      standard per-request DB session (existing pattern, no new shared mutable state); a
      non-superadmin's request never reaches the DB at all (require_superadmin rejects before
      any query runs), so there's no timing oracle that could leak tenant existence
- [x] no exposed secrets, injection openings, or unexpected dependencies — the `q` search param
      is bound via SQLAlchemy `.ilike()` (parameterized, never string-concatenated into SQL);
      config/budget fields are structurally excluded from the response DTO (test-proven, M2);
      zero new third-party dependencies
- [x] layering & dependencies follow CONVENTIONS.md — router calls repository functions
      directly, with NO application/use-case layer in between (unlike users_router.py's
      ListTenantUsersUseCase). Deliberate, not an oversight: mirrors get_platform_tenant's own
      existing precedent of a direct repository call, since there is no business logic to
      encapsulate here beyond the auth gates FastAPI's Depends() already enforces — a use-case
      class would wrap nothing but the query itself.
- [x] a person reviewed and approved the change — Tin Dang approved the freeze (2026-07-03),
      including the explicit Least-sure flag on audit deferral

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] a superadmin lists every tenant across both kinds — confirmed by
      test_superadmin_lists_every_tenant_across_kinds (200, platform + customer both present)
- [x] the list response never leaks config/budget fields — confirmed by
      test_list_entry_is_directory_summary_shape_only (entry keys == exactly {id,name,kind,created_at})
- [x] search narrows by name, limit caps the page, an oversized limit clamps instead of 400ing —
      confirmed by test_search_and_limit_narrow_results + test_limit_above_max_clamps_not_errors
- [x] a superadmin opens any tenant by id, not just their own — confirmed by
      test_superadmin_opens_any_single_tenant (200 for a tenant the caller doesn't belong to)
- [x] a non-superadmin is 403'd on both routes regardless of their own tenant_id, with their own
      tenant-scoped surfaces unaffected — confirmed by test_bulk_list_rejects_non_superadmin_owner
      (GET /admin/users still 200 for the same caller) + test_get_one_rejects_non_superadmin_targeting_other_tenant
- [x] missing token 401s, nonexistent tenant_id 404s with the new ERR_TENANT_NOT_FOUND code —
      confirmed by test_missing_bearer_token_rejected + test_get_one_nonexistent_tenant_404s

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced outside its own definition, confirmed via
      direct grep (serena's symbol-graph lookup was stale for these freshly-written files, so
      cross-checked with search_for_pattern instead): require_superadmin used at
      platform_tenants_router.py:66,96; list_tenants used at :78; get_tenant_by_id used at
      :108; TENANT_NOT_FOUND used at :110; platform_tenants_router imported+registered at
      main.py:124,976.
- [x] DEAD-CODE (code) — none introduced; all 5 new/modified symbols are live call sites.
      One nuance disclosed, not hidden: authorize_tenant_scope's reject-branch is technically
      unreachable via the get-one route's current caller population, since require_superadmin
      already filters to SUPERADMIN-only before that check runs (both routes are stricter than
      authorize_tenant_scope alone would require — §1's own frozen scenario already names this
      tension explicitly). The call is genuinely live and executes on every request (proven by
      test_superadmin_opens_any_single_tenant's 200 — a raise there would 403, not 200) — not
      dead code by the strict definition, just a predicate whose reject path this call site
      never exercises given the earlier, stricter gate.
- [x] SEMANTIC (prose / non-code) — platform_tenants_router.py's module docstring +
      per-function docstrings read in full (I wrote them); confirmed they accurately describe
      the dual-gate design and disclose the authorize_tenant_scope redundancy above rather than
      presenting it as a clean, unqualified "first real caller" win.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — authorize_tenant_scope,
      Role.SUPERADMIN, TenantRow, get_platform_tenant, AUTH_FORBIDDEN all resolve unchanged
      (new code was added alongside them, nothing pre-existing moved or renamed)
- [x] no anchor moved/renamed since Ground SHA (ccf411c) — confirmed, this task only added code

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self · adversarially checked:
  - could any test pass for the wrong reason? test_get_one_rejects_non_superadmin_targeting_other_tenant's
    second assertion (own-tenant 403) tests require_superadmin's precedence over
    authorize_tenant_scope's same-tenant allowance — re-read the test's own inline comment to
    confirm it claims exactly that, not a false claim about authorize_tenant_scope itself. Accurate.
  - is authorize_tenant_scope's call on get-one actually live, not a no-op stub? Confirmed via
    test_superadmin_opens_any_single_tenant's 200 (a raise there would surface as 403).
  - any vacuous assert (assert True, tautology)? None found — every assertion checks a real
    status code, response field, or field-set equality against seeded data.
  - any fixture masking a real failure (e.g. a broad try/except)? None — fixtures are plain
    direct-SQL inserts + a single token_service.issue() call, no exception handling to hide behind.
  - does the search test prove case-insensitivity or just lucky casing? q="acme" against a
    seeded "Acme Corp"/"Acme Labs" — genuinely cross-case, not same-case coincidence.
  - ran the regression suites for every OTHER file this build touched (authz.py, repository.py,
    error_catalog.py, main.py) — 54/54 pass, confirming no silent breakage elsewhere.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: self
1. Security: CLEAR — require_superadmin gates both routes at the FastAPI dependency layer
   (runs before any handler/DB access); `q` is parameterized via .ilike(), never string-built
   SQL; config/budget fields structurally excluded from the response; a non-superadmin's
   request never reaches the DB, so no existence-timing oracle.
2. Concurrency: CLEAR — read-only, no writes, standard per-request session, no new shared state.
3. Architecture: CLEAR — direct repository calls (no use-case layer) is a disclosed, deliberate
   choice matching get_platform_tenant's own precedent, not an oversight; new nested route tree
   keeps the 14 existing tenant-scoped routers untouched, matching the frozen Framings-weighed
   decision.
Verdict: PASS
Residue: none
Binding: advisory — architecture (sensitivity not declared as mechanical)

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (froze the contract) + AI self-review (build, verify, full-suite triage) · date: 2026-07-03

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 403 rate on GET /admin/platform/tenants[/{id}] (a sustained
  non-zero rate from a known-superadmin caller would indicate a role-check regression); 404 rate
  on get-one (spikes could indicate a client enumerating stale/deleted tenant_ids); list latency
  as tenant count grows (no index added on tenants.name for the ILIKE search — fine at current
  scale, revisit if platform-admin-console's directory UI adds default-sort-by-name at volume).

### Decisions (ADR)
- [AI] specify — chose the dual-gate split (new role-only `require_superadmin` for the
  no-single-target bulk list; existing `authorize_tenant_scope` for the get-one, which has a
  natural target) over a single new Permission (rejected — no non-superadmin role could ever
  meaningfully hold "list all tenants") or forcing the bulk list through a synthetic target for
  `authorize_tenant_scope` (rejected — abuse of a frozen, single-target contract). New nested
  routes under `/admin/platform/tenants` chosen over retrofitting all 14 existing `/admin/*`
  routers (rejected — blast radius/regression risk for a milestone whose Scope-Out forbids
  changing their behavior).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (2026-07-03)), including the explicit
  Least-sure flag on audit deferral to admin-console-audit.
- [AI] build — strategy used exactly as planned, zero deviation — the 5 ordered batches
  (error_catalog → authz → repository → router → main.py registration); one build-time detail
  noted: repository functions landed as module-level functions, not class methods, matching
  get_platform_tenant's own existing precedent.
- [AI] verify — gate PASS (reviewed by Tin Dang [froze the contract] + AI self-review [build,
  verify, full-suite triage]). Full-suite run surfaced 3 failures, all independently triaged as
  pre-existing/unrelated (see §6's "all tests pass" evidence line for the full triage, including
  a stash-based A/B isolation proving the Redis-flake reproduces identically on the clean base).

### Spec delta
- [SPEC · seeded] admin-console-audit (task 4 of platform-admin-console) must retrofit an audit
  row onto the get-one read (`GET /admin/platform/tenants/{tenant_id}`), not just the write-
  capable tasks 2+3 — this task deliberately shipped the directory WITHOUT audit-on-read (the
  frozen §3 Least-sure flag), matching platform-identity's own capability-first-audit-second
  precedent (evidence: MILESTONE.md's Exit criteria commit to auditing "every cross-tenant
  READ/write", but its Tasks list only wired admin-console-audit's depends-on to tasks 2+3).
- [SPEC · open] no pagination/search convention existed anywhere in the codebase before this
  task (CONVENTIONS.md grep: zero hits) — this task set the first one (limit/offset + name
  substring, no cursor-pagination). Worth promoting to CONVENTIONS.md if task 2/3 need the same
  shape (evidence: MILESTONE.md's cross-tenant-config-budget and cross-tenant-keys-members will
  likely need an analogous list endpoint).

### Competency deltas
- [ADD · folded] a test file reformatted by `ruff format` AFTER its tests->build tamper-tripwire [folded foundation-version 45]
  snapshot (taken at RED-confirmation) diverges from that snapshot's md5 and trips
  `build_tampered` at gate time, even though the change is whitespace-only and the test was never
  weakened. Fix is cheap (re-cross tests->build via `add.py phase build` to force an unconditional
  re-snapshot, per `_build_entry`'s own documented "legit change-request... re-snapshots cleanly"
  design) but costs a heal-attempt-shaped scare if not recognized immediately (evidence: hit
  verbatim this task, burned 0 of the 3-attempt cap since it was diagnosed before retrying blind).
  Worth a lint-format pass BEFORE the tests->build crossing, not after, to avoid this entirely on
  future tasks.
- [SDD · folded] `mcp__serena__find_referencing_symbols` (and direct `find_symbol` lookups) gave [folded foundation-version 45]
  false-negative/stale results for symbols in files written earlier in the SAME session
  (`list_tenants`, `get_tenant_by_id`, `platform_tenants_router` — all real, all live, all found
  correctly via `search_for_pattern`/grep instead). Serena's symbol index appears to lag behind
  very recently written/edited files within a session (evidence: this task's WIRING deep-check
  in §6 required the grep fallback for every new symbol). Don't trust a symbol-graph zero-result
  at face value for fresh code — cross-check with a raw pattern search first.
- [TDD · folded] a full-suite failure should never be accepted as "pre-existing/unrelated" from [folded foundation-version 45]
  reading a stack trace alone when the changed diff is small enough to stash — `git stash` the
  task's changed paths, re-run the failing test in isolation 2-3x on the CLEAN base, and only
  trust the "unrelated" conclusion if it reproduces the SAME way without any of the task's code
  present (evidence: this task's response_caching failure looked plausibly related at first
  glance — NOGROUP on a Redis stream, and this task added a new router+dependency to the same
  `app` fixture every test shares — but the A/B stash comparison proved it flakes identically
  pass/fail/fail with zero lines of this task's code in the tree, a pre-existing startup race in
  the flusher's lazy `xgroup_create` already partially documented in tests/conftest.py's own
  comments).

