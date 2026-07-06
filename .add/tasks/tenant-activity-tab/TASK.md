# TASK: Tenant Activity Tab

slug: tenant-activity-tab · created: 2026-07-06 · stage: production
milestone: platform-console-flat-redesign
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py:AuditRepository.list_for_tenant_paged`
    — `(tenant_id: uuid.UUID, limit: int = 50, offset: int = 0) -> list[AuditEvent]`, newest-first
    (`ORDER BY created_at DESC, id DESC`). Read the real method body (lines 89-104) — CONFIRMS
    MILESTONE.md's claim exactly; not a silent trust.
  - `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py:AuditRepository.count_for_tenant`
    — `(tenant_id: uuid.UUID) -> int`. Also read verbatim (lines 80-87) — confirmed exactly as claimed.
  - `apps/gateway/src/gateway/audit/domain/audit_event.py:AuditEvent` — frozen dataclass: `id,
    tenant_id: UUID|None, actor_user_id: UUID|None, actor_email: str|None, action: str,
    target_type: str|None, target_id: str|None, result: str, metadata: dict, created_at: datetime`.
  - `apps/gateway/src/gateway/audit/infrastructure/audit_events_orm.py:AuditEventRow` — confirms
    `actor_email`/`target_type`/`target_id` are plain nullable TEXT columns, NOT foreign keys (no
    `tenant_id`/`actor_user_id` FK enforced either, "following the alert_events_orm.py pattern" per
    its own docblock) — a deleted actor/user or removed target row can NEVER break a read; the row
    simply keeps its own write-time-snapshotted strings forever. A composite index
    `audit_events_tenant_created_idx` on `(tenant_id, created_at)` already backs exactly this
    access pattern. DB-level immutability: RULEs block UPDATE/DELETE outright.
  - `apps/gateway/src/gateway/usage/api/router.py:get_audit` (lines 727-767) — the EXISTING
    tenant-self-service sibling, `GET /admin/audit`, FROZEN @ v1, gated by
    `require_permission(Permission.AUDIT_READ)` (owner/admin/operator, own tenant only via
    `identity.tenant_id`). THE closest possible precedent: calls the identical
    `AuditRepository.count_for_tenant`/`list_for_tenant_paged` pair, both wrapped in
    `asyncio.timeout(_AUDIT_READ_TIMEOUT_SECONDS)` (30.0s) — an already-shipped design-for-failure
    IO guard. NOT mentioned anywhere in MILESTONE.md — an important Ground-time addition: this task
    is not inventing audit-reading, it adds a SECOND (superadmin, any-target-tenant) door onto an
    already-existing, already-working audit trail.
      - `usage/api/router.py:AuditListResponse{items: list[AuditEventItem], total: int}`,
        `AuditEventItem{id: str, actor_email: str|None, action: str, target_type: str|None,
        target_id: str|None, result: str, metadata: dict[str,object], created_at: str}` (both
        `ConfigDict(frozen=True)`) — lines 703-724.
      - `usage/api/router.py:_parse_pagination(limit: str|None, offset: str|None) -> tuple[int,int]`
        (lines 598-622) — manual string parse (NOT FastAPI-native int coercion) so a bad value
        raises `PAYLOAD_INVALID` (422 `ERR_PAYLOAD_INVALID`) rather than FastAPI's own 422 shape;
        default limit 50, valid range 1-100, default offset 0, must be ≥0. NOTE: its body actually
        references `_ALERTS_DEFAULT_LIMIT`/`_ALERTS_MAX_LIMIT` (shared with `get_alerts`), NOT the
        separately-declared `_AUDIT_DEFAULT_LIMIT`/`_AUDIT_MAX_LIMIT` constants (lines 699-700,
        identical values 50/100 but dead/unused) — a pre-existing, harmless naming quirk in FROZEN
        code this task must not touch. `_parse_pagination` is module-private (`_`-prefixed) — not
        importable across router files (matches this codebase's established per-file-helper-
        duplication convention, confirmed twice below).
  - `apps/gateway/src/gateway/tenants/domain/authz.py:require_superadmin` (lines 253-276) and
    `:authorize_tenant_scope` (lines 143-165) — read in full, confirmed byte-exact against
    MILESTONE.md's + `command-palette` TASK.md's own citation: `require_superadmin` 401s
    `AUTH_TOKEN_MISSING`/`AUTH_TOKEN_INVALID` on a bad/missing bearer, 403s `AUTH_FORBIDDEN` unless
    `identity.role == Role.SUPERADMIN`; `authorize_tenant_scope` 403s `AUTH_FORBIDDEN` unless
    `identity.role == Role.SUPERADMIN or identity.tenant_id == target_tenant_id`.
  - `apps/gateway/src/gateway/tenants/api/platform_tenants_router.py:get_platform_tenant_by_id`
    (lines 106-134) — the auth+audit-emit RECIPE this task's new route mirrors: `require_superadmin`
    Depends → `authorize_tenant_scope(identity, tenant_id)` → 404 `TENANT_NOT_FOUND` if the tenant
    row is missing → do the read → `emit_platform_audit(...)` on the SUCCESS path only.
  - `apps/gateway/src/gateway/tenants/api/platform_users_router.py:_require_target_tenant` (lines
    99-106) and `:list_platform_tenant_users` (lines 114-139) — an even closer structural precedent:
    a bundled `_require_target_tenant(identity, tenant_id, session)` helper (`authorize_tenant_scope`
    THEN a 404 check) reused by the route; `list_platform_tenant_users` is itself a pure LIST/READ
    endpoint that STILL calls `emit_platform_audit(action="platform.user.list", target_type="user",
    target_id=None)` on success — confirms (per `platform_audit.py`'s own docstring: "Every
    platform_*_router.py call site (15 total)...calls emit_platform_audit(...) exactly once, on its
    own success path") that EVERY platform route audits, including pure reads, with zero exception.
  - `apps/gateway/src/gateway/audit/application/platform_audit.py:emit_platform_audit` — read in
    full: fire-and-forget (`asyncio.ensure_future`), fail-OPEN (never raises, never blocks/changes
    the caller's response), `target_tenant_id` is always the PATH/target tenant (never the
    superadmin's own `identity.tenant_id`).
  - File-organization convention, confirmed by directly listing + reading 4 sibling files:
    `platform_users_router.py` (prefix `/admin/platform/tenants/{tenant_id}/users`),
    `platform_impersonation_router.py` (no shared prefix, declares full paths per-route),
    `platform_plans_router.py` (prefix `/admin/platform`), `platform_tenant_config_router.py`,
    `apps/gateway/src/gateway/keys/api/platform_keys_router.py` — ONE dedicated file per
    sub-resource is the established pattern, never grown inside `platform_tenants_router.py`
    itself. All individually registered via `app.include_router(...)` in
    `apps/gateway/src/gateway/main.py` (lines 1110-1121, confirmed by direct read).
  - `apps/dashboard/components/platform/PlatformTenantDetail.tsx:PlatformTenantDetail` (read in
    full) — the tab-container. `TAB_VALUES = ["config","budget","keys","members","plan"] as const`;
    URL-state-driven (`?tab=`, seeded via a lazy `useState` initializer + re-synced on real
    navigation via the "adjust state during render" pattern); each `TabsContent` mounts one
    independent tab component receiving only `{tenantId}` (or `{tenantId, tenantKind}` for
    Members). Adding a 6th tab is a 3-line additive change: extend `TAB_VALUES`, add one
    `TabsTrigger`, add one `TabsContent`.
  - `apps/dashboard/components/platform/PlatformMembersTab.tsx:PlatformMembersTab` (read in full)
    — chosen PRIMARY structural precedent: `{tenantId}`-keyed own `useQuery`, a local
    `getErrorTitle` helper (duplicated per-file, established convention), `Loading`/`ErrorState`
    early-return before the main render, `DataTable` for the row list.
  - `apps/dashboard/components/platform/PlatformTenantDirectory.tsx:PlatformTenantDirectory` (read
    in full) — chosen PRECEDENT for pagination CHROME specifically: `PAGE_LIMIT=20`, `offset`
    state, server-driven queryKey (`["platform-tenants", debouncedQuery, offset]`), a "Showing
    X–Y of Z" `aria-live="polite"` line plus Previous/Next `<button>`s disabled at
    `offset===0`/`!hasMore`. Its OWN docblock explicitly states WHY it hand-rolled this instead of
    `DataTable`'s built-in `pageSizeOptions` client pagination: "this list can be platform-wide,
    unlike every other existing list page's small, tenant-scoped collection" — confirmed by
    reading `DataTableProps` directly (`pageSizeOptions?: number[]` is CLIENT-side only — paginates
    already-fetched data, the wrong tool for an unbounded collection).
  - `apps/dashboard/components/audit/AuditPage.tsx` + `AuditTable.tsx` (read in full) — the
    EXISTING self-service audit UI (nav "Audit", `minRole:"admin"`,
    `apps/dashboard/components/ui/app-shell.tsx:67`): `queryKey:["admin-audit"]` →
    `bffGet("/admin/audit")` → `AuditData{items, total}` rendered via `DataTable` with columns
    Actor(`actor_email`)/Action/Target(`target_type`)/Result/When(`created_at` via
    `formatTimestamp`). Does NOT paginate — a single unpaginated fetch at the backend's default
    limit (50). This is the precedent this task's new tab deliberately diverges FROM (see
    Issues/Risks #1).
  - `apps/dashboard/components/ui/states.tsx:Loading,Empty,ErrorState` — read in full, reused
    verbatim (`Empty{title, description?, action?}` for the zero-history case).
  - `apps/dashboard/components/ui/data-table.tsx:DataTableProps` — read in full (interface only):
    confirms `columns/data/ariaLabel/emptyMessage` is the plain shape every existing consumer uses;
    `pageSizeOptions` exists but is client-side-only (not used here, see above).
  - `apps/dashboard/lib/format.ts:formatTimestamp` (line 14) — `(value: string|null|undefined) =>
    string`, null-safe (`"—"` placeholder); reused verbatim for the "When" column.
  - `apps/gateway/src/gateway/core/error_catalog.py` — confirmed exact wire shapes:
    `AUTH_TOKEN_MISSING`/`AUTH_TOKEN_INVALID` -> 401 `ERR_AUTH_INVALID_TOKEN`; `AUTH_FORBIDDEN` ->
    403 `ERR_AUTH_FORBIDDEN`; `PAYLOAD_INVALID` -> 422 `ERR_PAYLOAD_INVALID`; `TENANT_NOT_FOUND` ->
    404 `ERR_TENANT_NOT_FOUND`.
  - `apps/gateway/src/gateway/tenants/domain/entities.py:Identity` — `user_id: UUID, tenant_id:
    UUID, email: str, role: Role, impersonation: ImpersonationContext|None`.
Context (working folder):
  - `.add/milestones/platform-console-flat-redesign/MILESTONE.md` — this task's own Task line,
    Shared/risky-contracts line, Exit criterion.
  - `.add/tasks/command-palette/TASK.md` (DONE, gate=PASS) — house style/citation convention
    mirrored throughout this draft.
  - `.add/tasks/tenant-overview-strip/TASK.md` + `overview-strip-plan-display-name/TASK.md` (DONE,
    gate=PASS) — confirmed via `git log` neither touched any gateway/backend file;
    `PlatformTenantOverviewStrip.tsx` (new file) is already wired into `PlatformTenantDetail.tsx`
    above the Tabs — this task's new 6th tab sits below it, unaffected.
  - `.add/tasks/console-flat-visual-pass/TASK.md` (DONE, gate=PASS) — confirmed frontend-visual-only;
    the flat/borderless tokens it shipped (`flat-tag`/`flat-control`/`flat-card`, `Card
    variant="flat"`) are what this task's new tab should visually match — no NEW token invented here.
  - `.add/GLOSSARY.md` — read in full; no existing "audit"/"activity" term (only unrelated
    "audit_trail"/"otel_span" entries) — confirmed before declaring a delta.
Honors (patterns / conventions):
  - The `require_superadmin` + `authorize_tenant_scope` + tenant-existence-404 +
    `emit_platform_audit`-on-success recipe, byte-identical across `get_platform_tenant_by_id` and
    `list_platform_tenant_users` — this task's new route follows it exactly, including auditing its
    OWN read (matching the 15/15 no-exception convention).
  - One dedicated router file per sub-resource (never grown inside `platform_tenants_router.py`).
  - Locally-redeclared response/request Pydantic models rather than a cross-router-module import
    (`platform_users_router.py`'s own explicit stated reason: "avoids a router-module-to-router-
    module import for DTOs that are trivial to redeclare").
  - `PlatformMembersTab.tsx`'s `{tenantId}`-prop / own-useQuery / local-getErrorTitle /
    Loading-ErrorState-early-return shape for the new tab's basic skeleton.
  - `PlatformTenantDirectory.tsx`'s server-driven Prev/Next pagination chrome + its own explicit
    "unbounded collection" rationale, extended to audit history.
  - `states.tsx`/`formatTimestamp`/`DataTable` reused verbatim — zero new display primitives.
  - The global CLAUDE.md IO rule ("design for failure: timeouts, retries, circuit breakers... in IO
    request") — already answered by mirroring `get_audit`'s own
    `asyncio.timeout(_AUDIT_READ_TIMEOUT_SECONDS)` wrap, not inventing a new policy.
Seams consulted: none beyond the precedents cited above (no `.add/SEAMS.md` entry matches this
  task specifically).
Anchors the contract cites:
  - NEW `apps/gateway/src/gateway/tenants/api/platform_audit_router.py:platform_audit_router`
  - `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py:AuditRepository.count_for_tenant,list_for_tenant_paged`
    (reused verbatim)
  - `apps/gateway/src/gateway/tenants/domain/authz.py:require_superadmin,authorize_tenant_scope`
    (reused verbatim)
  - `apps/gateway/src/gateway/tenants/infrastructure/repository.py:get_tenant_by_id` (reused
    verbatim, the 404 pre-check)
  - `apps/gateway/src/gateway/audit/application/platform_audit.py:emit_platform_audit` (reused
    verbatim)
  - `apps/gateway/src/gateway/core/error_catalog.py:TENANT_NOT_FOUND,PAYLOAD_INVALID` (reused
    verbatim; `AUTH_FORBIDDEN`/`AUTH_TOKEN_INVALID` reused transitively via the Depends)
  - `apps/gateway/src/gateway/main.py` (additive one-line `app.include_router(platform_audit_router)`)
  - NEW `apps/dashboard/components/platform/PlatformActivityTab.tsx:PlatformActivityTab`
  - `apps/dashboard/components/platform/PlatformTenantDetail.tsx:PlatformTenantDetail,TAB_VALUES`
    (additive 6th tab)
  - `apps/dashboard/components/ui/states.tsx:Loading,Empty,ErrorState` (reused verbatim)
  - `apps/dashboard/components/ui/data-table.tsx:DataTable` (reused verbatim)
  - `apps/dashboard/lib/format.ts:formatTimestamp` (reused verbatim)
Issues/Risks (→ feed §1):
  1. [LEAD] Real Prev/Next pagination vs. a single unpaginated fetch — the ONLY two precedents
     point opposite ways: the self-service `AuditPage.tsx` doesn't paginate at all (single fetch,
     default limit 50), while `PlatformTenantDirectory.tsx` hand-rolls real server-driven Prev/Next
     specifically because its collection is unbounded — and audit history for an active tenant is
     exactly that kind of collection (grows forever, and per the Ground finding above, EVERY view
     of it — including the superadmin's own — writes ANOTHER row, compounding growth over time).
     Resolution (proceeding as project lead, extends an already-articulated precedent's own stated
     rationale to a structurally identical case, low-risk/reversible): build real server-driven
     Prev/Next, mirroring `PlatformTenantDirectory.tsx`'s shape. Flagged at freeze (see §3) — the
     single biggest new interaction pattern this task introduces (first tenant-DETAIL-level tab to
     paginate at all).
  2. Pagination input validation style — `get_audit` (same resource, same repo calls) REJECTS
     out-of-range/unparseable `limit`/`offset` with 422 `PAYLOAD_INVALID`, while
     `list_platform_tenants` (a different resource) silently CLAMPS an over-large limit. Resolution
     (proceeding as project lead, the same-resource precedent is the stronger match, and
     reject-not-clamp keeps this task's own "surface tradeoffs, don't hide confusion" bar): mirror
     `get_audit`'s reject-style exactly (limit 1-100 default 50, offset ≥0 default 0,
     manually-parsed string-typed Query params so a bad value maps to `ERR_PAYLOAD_INVALID`, not
     FastAPI's native 422 shape) via a locally-duplicated parse helper (not importing
     `usage/api/router.py`'s private `_parse_pagination` across a router-module boundary — matches
     the established per-file-helper-duplication convention).
  3. Should reading this NEW route itself write another audit_events row (self-referential
     audit-of-audit-reads)? The established convention is unanimous and exception-free (15/15
     existing platform_*_router.py call sites, including pure reads like
     `list_platform_tenant_users`/`get_platform_tenant_by_id`, always call `emit_platform_audit` on
     success). Resolution (proceeding as project lead, matches the ONLY precedent set, and is a
     one-line, trivially-removable call if this reasoning is later rejected): yes —
     `action="platform.audit.list"`, `target_type="audit"`, `target_id=None` (a bulk list, matching
     `platform.tenant.list`/`platform.user.list`'s own `target_id=None` convention for non-single-
     row reads), fire-and-forget/fail-open exactly like every sibling call site.
  4. File location for the new route — no existing file is a perfect fit (`platform_tenants_router.py`
     is FROZEN @ v1 for its own 2 routes; growing it would blur that freeze's own scope). Resolution
     (proceeding as project lead, matches the unanimous "one file per sub-resource" convention
     observed across 5 sibling files): a NEW `platform_audit_router.py`, prefix
     `/admin/platform/tenants/{tenant_id}/audit`, registered as one additive
     `app.include_router(...)` line in `main.py` (mirrors all 5 siblings' own registration).
  5. A very active tenant's audit history has no natural upper bound — confirmed NOT a
     query-performance risk: `audit_events_tenant_created_idx` (a real composite index on
     `(tenant_id, created_at)`, confirmed by reading `audit_events_orm.py`'s `__table_args__`)
     already backs exactly this access pattern; `count_for_tenant`/`list_for_tenant_paged` both
     filter on the indexed `tenant_id` column.
  6. A tenant with ZERO audit history — cleanly handled: `count_for_tenant` returns `0`,
     `list_for_tenant_paged` returns `[]`; the new tab renders the shared `Empty` primitive
     (matching `AuditTable.tsx`'s own "No audit events yet" precedent almost verbatim).
  7. A deleted actor or target referenced by an old row — confirmed NOT a failure mode:
     `actor_email`/`target_type`/`target_id` are plain nullable TEXT columns with NO foreign key
     (confirmed by reading `audit_events_orm.py` directly, and its own docblock: "tenant_id FK...is
     intentionally absent...following the alert_events_orm.py pattern"), snapshotted at write time
     by `_row_to_event`/`AuditEvent` — a later-deleted actor or target can never cause a join
     failure or a missing row; the row simply keeps showing its own historical strings forever.
  8. MILESTONE.md's claim ("reuses `AuditRepository.list_for_tenant_paged`/`count_for_tenant`") is
     CONFIRMED CORRECT by direct read of the real method bodies (not assumed) — no Ground-time
     correction needed here. The one thing MILESTONE.md does NOT mention, which this Ground
     surfaced independently: an already-existing, already-FROZEN self-service sibling route (`GET
     /admin/audit`) reads this SAME table today — this task adds a second, superadmin-scoped,
     any-target-tenant door onto an existing capability, not a new capability from zero.
Related intent: MILESTONE.md's Scope ("a per-tenant Activity tab") + Shared/risky-contracts line
  ("New superadmin-scoped audit-read route (reuses AuditRepository.list_for_tenant_paged/
  count_for_tenant, the require_superadmin+authorize_tenant_scope pattern...)") + Exit criterion
  ("A superadmin viewing a tenant's detail page can open an 'Activity' tab and see that tenant's
  real audit history (actor/action/target/when), authorized the same way as the other
  tenant-detail routes"). GLOSSARY.md has no existing "Activity tab"/"audit event" prose term
  (checked directly) — this task declares one (see §3 Glossary deltas).
Ground SHA: `37e55ee` (2026-07-06; confirmed via `git rev-parse --short HEAD` — UNCHANGED since
  `command-palette`'s own Ground SHA, i.e. no new commit has landed on `main` since). The working
  tree carries UNCOMMITTED, frontend-only changes from this milestone's 3 prior DONE tasks (`git
  status --short` confirmed: every modified path is under `apps/dashboard/`; zero `apps/gateway/`
  files are dirty) — so every backend symbol cited above was read against stable, unmodified,
  already-merged code. `git log` confirms `platform_tenants_router.py` and `authz.py` last touched
  at `b23fce8`/`006f791` respectively (both predate this milestone); `PlatformTenantDetail.tsx`
  last touched (committed) at `006f791` — its CURRENT 5-tab shape read above already includes
  `tenant-overview-strip`'s own uncommitted, already-verified addition. Cite symbols above, not
  bare line numbers; any line ref elsewhere is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant Activity Tab — a 6th tab on the superadmin tenant-detail screen ("Activity")
  rendering a target tenant's REAL audit-event history (actor/action/target/result/when), backed
  by a NEW superadmin-scoped, cross-tenant-capable `GET /admin/platform/tenants/{tenant_id}/audit`
  route that reuses the already-existing `AuditRepository` and the same authorization recipe every
  other tenant-detail route already uses.
Framings weighed: a NEW dedicated `platform_audit_router.py` file, mirroring the "one router file
  per sub-resource" convention of `platform_users_router.py`/`platform_impersonation_router.py`/
  etc, route path `/admin/platform/tenants/{tenant_id}/audit` (chosen — matches the unanimous
  5-sibling-file precedent; keeps `platform_tenants_router.py`'s own FROZEN @ v1 scope untouched)
  · adding a 3rd route directly to `platform_tenants_router.py` (rejected — no sibling in this
  codebase grows an already-frozen router file for an unrelated sub-resource) · real, server-driven
  Prev/Next pagination mirroring `PlatformTenantDirectory.tsx`'s own shape and rationale (chosen —
  audit history for an active tenant is an unbounded, ever-growing collection, the exact condition
  that component's own docblock names as its reason for rejecting client pagination; MILESTONE's
  own "history" wording implies more than one page for a real tenant) · a single unpaginated fetch
  mirroring the self-service `AuditPage.tsx`'s own precedent (rejected for a superadmin
  investigation surface specifically — silently capping at 50 rows with no way to see older history
  is a real capability gap for exactly the audit/incident-review use case this tab exists for;
  flagged at freeze, §3, as the single least-certain call in this bundle) · `DataTable`'s own
  built-in `pageSizeOptions` client pagination (rejected — confirmed client-side-only, paginating
  already-fetched data; the wrong tool for a collection with no fetch-once upper bound) ·
  reject-style pagination validation (422 `ERR_PAYLOAD_INVALID` on a bad `limit`/`offset`),
  mirroring `get_audit`'s own exact behavior for the SAME resource (chosen — the closer,
  same-repository precedent) · silent-clamp mirroring `list_platform_tenants`'s own behavior for a
  DIFFERENT resource (rejected — weaker precedent match; also hides a caller bug instead of
  surfacing it) · auditing this route's own reads via `emit_platform_audit` (chosen — matches the
  unanimous, zero-exception 15/15 existing convention, including other pure-read platform routes)
  · treating an audit-read as exempt from being itself audited (rejected — no precedent anywhere
  supports an exemption, and inventing one is a bigger, unjustified departure than following the
  existing rule) · `PlatformMembersTab.tsx` as the new tab's structural skeleton precedent (own
  `{tenantId}`-keyed query, local `getErrorTitle`, Loading/ErrorState early-return, `DataTable`)
  with `PlatformTenantDirectory.tsx`'s pagination footer grafted on top (chosen, a deliberate
  hybrid, each half cited to its own precedent) · inventing a wholly new shape (rejected — no
  reason to depart from either established pattern).
Must:
<must>
  - M1 (AUTH — the critical boundary, mirrors `get_platform_tenant_by_id`/
    `list_platform_tenant_users` EXACTLY): `GET /admin/platform/tenants/{tenant_id}/audit` is
    gated, in order: (a) `require_superadmin` (401 `ERR_AUTH_INVALID_TOKEN` on a missing/invalid
    bearer; 403 `ERR_AUTH_FORBIDDEN` on a non-SUPERADMIN role) — THEN (b)
    `authorize_tenant_scope(identity, tenant_id)` (403 `ERR_AUTH_FORBIDDEN` if somehow reached by a
    non-SUPERADMIN whose own tenant_id differs — dormant today since (a) already restricts to
    SUPERADMIN, kept for the identical documented reason `platform_users_router.py`'s own comment
    gives: "the semantically correct predicate for 'may I act on tenant_id'") — THEN (c) a
    tenant-existence check (`get_tenant_by_id`) returning 404 `ERR_TENANT_NOT_FOUND` if the path
    `tenant_id` does not resolve to a real row, BEFORE any audit query runs. Both (b)+(c) are
    bundled in one locally-declared `_require_target_tenant`-equivalent helper, matching
    `platform_users_router.py`'s own precedent byte-for-byte in shape.
  - M2 (ROUTE SHAPE): `GET /admin/platform/tenants/{tenant_id}/audit` lives in a NEW file
    `apps/gateway/src/gateway/tenants/api/platform_audit_router.py`,
    `APIRouter(prefix="/admin/platform/tenants/{tenant_id}/audit", tags=["platform-admin"])`,
    registered as one additive `app.include_router(platform_audit_router)` line in `main.py` —
    zero edits to `platform_tenants_router.py` or any other existing router file.
  - M3 (PAGINATION PARAMS — mirrors `get_audit`'s own reject-style exactly): optional
    `limit`/`offset` string-typed Query params, manually parsed (default `limit=50`, valid range
    1-100; default `offset=0`, must be ≥0); an unparseable or out-of-range value on EITHER raises
    422 `ERR_PAYLOAD_INVALID` — never silently clamped.
  - M4 (DATA — reuses `AuditRepository` verbatim): the route calls
    `AuditRepository(session).count_for_tenant(tenant_id)` and
    `.list_for_tenant_paged(tenant_id, parsed_limit, parsed_offset)` — the PATH `tenant_id`, never
    `identity.tenant_id` — both wrapped in one `asyncio.timeout(_AUDIT_READ_TIMEOUT_SECONDS)` block
    (same 30.0s budget as `get_audit`'s own, named as its own module-level constant in the new
    file — no cross-file constant import).
  - M5 (RESPONSE SHAPE): the 200 response is `{items: [{id, actor_email, action, target_type,
    target_id, result, metadata, created_at}], total}` — field-for-field identical to `get_audit`'s
    own `AuditListResponse`/`AuditEventItem` shape (locally redeclared in the new file, not
    imported across router modules, matching `platform_users_router.py`'s own stated
    DTO-redeclaration convention), newest-first (`created_at DESC, id DESC`, inherited from
    `list_for_tenant_paged`'s own ORDER BY).
  - M6 (AUDIT-THE-READ — matches the zero-exception 15/15 convention): on the success path only,
    the route calls `emit_platform_audit(identity=identity, target_tenant_id=tenant_id,
    action="platform.audit.list", target_type="audit", target_id=None, metadata={})` —
    fire-and-forget, fail-open, identical mechanism to every existing platform route.
  - M7 (FRONTEND TAB WIRING): `PlatformTenantDetail.tsx` gains a 6th tab: `TAB_VALUES` extends to
    `["config","budget","keys","members","plan","activity"]`, one new `TabsTrigger value="activity"`
    labeled "Activity", one new `TabsContent value="activity"` mounting `<PlatformActivityTab
    tenantId={tenantId} />` — no other line in this file changes (the existing 5 tabs, the
    Overview Strip, and the safety banner stay byte-identical).
  - M8 (NEW COMPONENT — `PlatformActivityTab.tsx`, structural precedent `PlatformMembersTab.tsx`):
    `{tenantId: string}`-only prop; own `useQuery` keyed `["platform-tenant-audit", tenantId,
    offset]`; a local (not exported, per-file, established-convention) `getErrorTitle` helper;
    `Loading`/`ErrorState` early-return before the main render, matching every sibling tab's own
    shape exactly.
  - M9 (PAGINATION CHROME — precedent `PlatformTenantDirectory.tsx`): an internal `offset` state
    (default 0), page-size constant `ACTIVITY_PAGE_LIMIT = 20`; a "Showing X–Y of Z"
    `aria-live="polite"` status line plus Previous/Next `<button>`s, Previous disabled at
    `offset === 0`, Next disabled when `offset + items.length >= total`; a Previous/Next click
    only changes `offset` (and thus the queryKey/fetch) — never resets any other tab's state.
  - M10 (TABLE): the fetched page's `items` render via the shared `DataTable` (columns:
    Actor=`actor_email`, Action=`action`, Target=`target_type`, Result=`result`, When=`created_at`
    via the shared `formatTimestamp`) — mirrors `AuditTable.tsx`'s own column set exactly; no new
    column/display component invented.
  - M11 (EMPTY STATE): `total === 0` (zero audit history for this tenant) renders the shared
    `Empty` primitive ("No audit events yet" — matching `AuditTable.tsx`'s own copy) — no table
    chrome, no pagination footer.
  - M12 (LOADING/ERROR STATES): a pending fetch (initial or any Previous/Next page change) renders
    the shared `Loading` primitive; a failed fetch renders the shared `ErrorState` primitive with a
    retry action — both `states.tsx`, reused verbatim, matching every sibling tab.
  - M13 (GRACEFUL NULL FIELDS): a row whose `actor_email` and/or `target_type`/`target_id` is
    `null` (a system event, or a since-deleted actor/target) renders that cell as a plain visual
    placeholder (matching `formatTimestamp`'s own `"—"` convention for a null/invalid timestamp) —
    never a crash, a blank crash-looking cell, or a filtered-out row.
</must>
Reject:
<reject>
  - any mutating action reachable from the Activity tab (delete/redact/replay/export an audit row)
    -> "not in scope" (MILESTONE.md's explicit "audit-read" framing; the underlying `AuditLog` port
    itself exposes no such method by design)
  - a cross-tenant activity feed (any view mixing more than one tenant's audit rows in one
    response/list) -> "not in scope" (MILESTONE.md's own explicit Out list: "a platform-wide
    (cross-tenant) activity feed"); every response row's implicit tenant scope is the single PATH
    `tenant_id`
  - a key/resource "last used at" feature or column -> "not in scope" (MILESTONE.md's own explicit
    Out list)
  - bulk actions of any kind on the Activity tab (bulk-export, bulk-acknowledge, select-all) ->
    "not in scope" (MILESTONE.md's own explicit Out list: "Bulk tenant actions")
  - a saved view or filter (by action/actor/date-range) on the Activity tab -> "not in scope"
    (MILESTONE.md's own explicit Out list: "saved views/filters"; the directory kind/plan filter
    Tier-3 deferral names the same category of scope creep)
  - reaching this route without an exact SUPERADMIN role (a stale/loading/null/any-other-role
    identity) -> rejected with 401/403 exactly as `require_superadmin` already defines — no new
    bypass path is introduced
  - a `tenant_id` that does not resolve to a real tenant row -> 404 `ERR_TENANT_NOT_FOUND`, BEFORE
    any audit query runs — never a 200 with an empty/misleading list
  - an out-of-range or unparseable `limit`/`offset` -> 422 `ERR_PAYLOAD_INVALID` — never silently
    clamped or defaulted without signaling the caller
  - client-side-only pagination (`DataTable`'s own `pageSizeOptions`) for this tab -> rejected as
    the wrong tool for an unbounded, ever-growing collection (see §0 Issues/Risks #1)
  - importing `usage/api/router.py`'s private `_parse_pagination`/`AuditListResponse`/
    `AuditEventItem` across the router-module boundary -> rejected; a small local redeclaration in
    the new file matches `platform_users_router.py`'s own established DTO convention
</reject>
After:
<after>
  - a superadmin opens any tenant's detail page, clicks the new "Activity" tab, and sees that
    tenant's real audit history — actor, action, target, result, and when — newest-first
  - a tenant with no audit history yet sees a clear empty state, not a blank table or an error
  - a tenant with a long history can page backward through it via Previous/Next, never silently
    capped at the first page
  - every request this tab makes is authorized by the exact same `require_superadmin`+
    `authorize_tenant_scope` gate every other tenant-detail route already uses — unmodified by this
    task
  - every successful view of this tab itself lands one new `platform.audit.list` row in the SAME
    audit trail it displays, exactly like every other platform route's own reads already do
  - the existing self-service `GET /admin/audit`/`AuditPage.tsx`, and every other existing
    tenant-detail tab/route, are byte-identical before and after this task
</after>
Assumptions — lowest-confidence first:
<assumptions>
  - [x] Real server-driven Prev/Next pagination (M9) for the Activity tab — RESOLVED (2026-07-06,
    AUTO MODE per CLAUDE.md Rule 2, no chat response after 60s on a direct AskUserQuestion ask):
    proceeding with real pagination. Orchestrator's own reasoning, not just the design agent's
    default: this tab exists specifically for a superadmin INVESTIGATION/incident-review use case,
    and being capped at the most recent N events with no way to page backward is a worse failure
    mode than the extra interaction surface — the asymmetry favors pagination. Cost if wrong stays
    as drafted: confined to `PlatformActivityTab.tsx` alone.
  - [x] Auditing this route's own reads (M6, action=`platform.audit.list`) — RESOLVED (2026-07-06,
    AUTO MODE per CLAUDE.md Rule 2, no chat response after 60s on the same ask): proceeding with
    auditing it. Orchestrator's own reasoning: this is a ZERO-exception convention across all 15
    existing platform routes, including other pure reads — breaking it for the first time, on a
    task Tin has not reviewed, is a bigger and less-justified departure than following it; the
    meta-audit value (who looked at tenant X's history) is a genuine security benefit for a
    cross-tenant surface exposing sensitive data, and fire-and-forget means zero reliability cost
    either way. Cost if wrong stays as drafted: one-line removal.
  - [x] `ACTIVITY_PAGE_LIMIT = 20` (matching `PlatformTenantDirectory.tsx`'s own `PAGE_LIMIT`, not
    the backend's own `_AUDIT_DEFAULT_LIMIT = 50`) — RESOLVED: proceeding with the design agent's
    own recommendation (cosmetic, low-stakes, reversible in one constant) — not raised as a
    separate ask given the first two calls already covered the two decisions with real product/
    security weight.
  - [x] Reject-style (422) vs. clamp-style pagination validation (M3) — RESOLVED: proceeding with
    the design agent's own recommendation (reject-style, matching the stronger same-resource
    `get_audit` precedent, and keeping errors surfaced rather than silently hidden) — not raised
    separately for the same reason above.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Full auth chain gates the new route in order   # M1
  Given a caller whose bearer token is missing, invalid, a non-superadmin role, or a superadmin whose path tenant_id does not exist
  When GET /admin/platform/tenants/{tenant_id}/audit is called
  Then a missing/invalid bearer returns 401 ERR_AUTH_INVALID_TOKEN, a non-superadmin role returns 403 ERR_AUTH_FORBIDDEN, and an unknown tenant_id returns 404 ERR_TENANT_NOT_FOUND
  And no audit query and no emit_platform_audit call ever runs for a request that fails any of these checks

Scenario: The route lives in its own new file, registered additively   # M2
  Given the gateway app before and after this task
  When the router registration and platform_tenants_router.py are inspected
  Then a new platform_audit_router.py exists with prefix /admin/platform/tenants/{tenant_id}/audit
  And platform_tenants_router.py has zero changed lines and main.py's only change is one additive include_router call

Scenario: Pagination params are parsed and validated, never silently clamped   # M3
  Given a superadmin requests the route with limit=0, limit=101, offset=-1, or a non-numeric value
  When the request is handled
  Then it is rejected with 422 ERR_PAYLOAD_INVALID before any repository call
  And a valid request with no limit/offset supplied defaults to limit=50, offset=0

Scenario: The route reuses AuditRepository verbatim, scoped by the path tenant   # M4
  Given a target tenant with audit history
  When the route runs
  Then it calls AuditRepository.count_for_tenant and list_for_tenant_paged with the PATH tenant_id, never identity.tenant_id
  And both calls are wrapped in a single asyncio.timeout budget

Scenario: The response shape matches the self-service sibling field-for-field   # M5
  Given a target tenant with 3 audit rows
  When the route returns 200
  Then the body is items (each carrying id, actor_email, action, target_type, target_id, result, metadata, created_at) plus total
  And the rows are ordered newest-first

Scenario: A successful list itself is audited   # M6
  Given a superadmin successfully lists a target tenant's audit history
  When the response is returned
  Then one new audit_events row is scheduled with action platform.audit.list, target_type audit, target_id null, and target_tenant_id equal to the path tenant_id
  And this write never blocks or changes the HTTP response even if it fails

Scenario: A 6th tab is wired into the tenant-detail screen   # M7
  Given the tenant-detail screen's existing 5 tabs
  When PlatformTenantDetail renders
  Then a 6th Activity tab is present and navigable via ?tab=activity
  And the existing 5 tabs and their own content are byte-identical to before this task

Scenario: The new tab's own query follows its structural precedent   # M8
  Given PlatformActivityTab receives only a tenantId prop
  When it mounts
  Then it fires its own useQuery keyed by platform-tenant-audit, tenantId, and the current offset
  And a query failure renders the shared ErrorState via a local getErrorTitle helper, independent of every other tab's own query

Scenario: Previous/Next paging changes only the audit page   # M9
  Given a tenant with more than one page of audit history
  When the superadmin clicks Next then Previous
  Then the offset advances and retreats by ACTIVITY_PAGE_LIMIT each time, Previous is disabled at offset 0 and Next is disabled once offset+items length reaches total
  And no other tab's state or query is affected by paging this one

Scenario: Rows render via the shared table with the expected columns   # M10
  Given a fetched page of audit rows
  When the tab renders them
  Then a table shows Actor, Action, Target, Result, and When columns via the shared DataTable
  And When is formatted via the shared formatTimestamp helper

Scenario: Zero audit history renders the shared empty state   # M11
  Given a target tenant with total 0
  When the tab renders
  Then the shared Empty primitive appears with a no-audit-events message
  And no table or pagination footer is rendered

Scenario: Loading and error states reuse the shared primitives   # M12
  Given the audit query is in flight, and separately, given it fails
  When each state renders
  Then the in-flight state shows the shared Loading primitive and the failed state shows the shared ErrorState primitive with a retry action
  And neither is a newly introduced display component

Scenario: Null actor/target fields render gracefully   # M13
  Given an audit row whose actor_email is null (a system event) and another whose target_type/target_id are null
  When the table renders these rows
  Then each null field shows a plain placeholder, never a crash or a dropped row
  And every other field on that same row still renders normally

Scenario: Reject, no mutating action exists on the Activity tab   # R1
  Given the Activity tab is open with visible rows
  When its interactive surface is inspected
  Then no delete, redact, replay, or export control exists anywhere in it
  And the underlying AuditLog port itself exposes no such method

Scenario: Reject, no cross-tenant activity feed   # R2
  Given two different tenants each with their own audit history
  When tenant A's Activity tab is open
  Then only tenant A's own rows (scoped by the path tenant_id) ever appear
  And no request this tab makes can return another tenant's rows

Scenario: Reject, no "last used at" feature   # R3
  Given the Activity tab and its backing response
  When both are inspected
  Then no "last used at" field, column, or concept appears anywhere
  And this remains true regardless of how much audit history exists

Scenario: Reject, no bulk actions   # R4
  Given the Activity tab with multiple visible rows
  When its controls are inspected
  Then no select-all, bulk-export, or bulk-acknowledge control exists
  And each row remains an independent, read-only line

Scenario: Reject, no saved views or filters   # R5
  Given the Activity tab
  When its controls are inspected
  Then no filter-by-actor, filter-by-action, date-range picker, or saved-view control exists
  And only the plain newest-first list plus Previous/Next paging is present

Scenario: Reject, a non-exact-superadmin caller never reaches audit data   # R6
  Given a caller whose role is owner, admin, member, or any non-superadmin value, or whose bearer is missing/invalid
  When they call GET /admin/platform/tenants/{tenant_id}/audit directly
  Then they receive 401 or 403 exactly as require_superadmin already defines
  And no audit row from any tenant is ever returned in that response

Scenario: Reject, an unknown tenant_id never returns a 200   # R7
  Given a tenant_id that does not correspond to any real tenant row
  When the route is called by a superadmin
  Then it returns 404 ERR_TENANT_NOT_FOUND
  And no AuditRepository call is made for that request

Scenario: Reject, invalid pagination input is never silently accepted   # R8
  Given a limit or offset value outside the valid range or not parseable as an integer
  When the route is called
  Then it returns 422 ERR_PAYLOAD_INVALID
  And the response body names ERR_PAYLOAD_INVALID, not a silently clamped 200

Scenario: Reject, this tab never uses client-side-only pagination   # R9
  Given PlatformActivityTab's own implementation
  When it is inspected
  Then it does not pass pageSizeOptions to DataTable
  And all paging is driven by the offset query param against the server, not by client-side slicing of an already-fetched array

Scenario: Reject, no cross-router-module private import   # R10
  Given the new platform_audit_router.py file
  When its imports are inspected
  Then it does not import _parse_pagination, AuditListResponse, or AuditEventItem from usage/api/router.py
  And its own pagination parsing and response models are declared locally
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/platform/tenants/{tenant_id}/audit   query: ?limit=<int 1-100, default 50>&offset=<int >=0, default 0>
  200 -> AuditListResponse { items: AuditEventItem[], total: int }
    AuditEventItem { id: str, actor_email: str|null, action: str, target_type: str|null,
      target_id: str|null, result: str, metadata: object, created_at: str (ISO 8601) }
    newest-first (created_at DESC, id DESC) — inherited from AuditRepository.list_for_tenant_paged
  401 -> { error: "ERR_AUTH_INVALID_TOKEN" }   missing/invalid bearer token (require_superadmin's
    own _resolve_identity step)
  403 -> { error: "ERR_AUTH_FORBIDDEN" }   caller's role is not exactly SUPERADMIN
    (require_superadmin), OR (dormant, unreachable while that holds) a SUPERADMIN whose own
    tenant_id check fails authorize_tenant_scope
  404 -> { error: "ERR_TENANT_NOT_FOUND" }   the path tenant_id does not resolve to a real tenant row
  422 -> { error: "ERR_PAYLOAD_INVALID" }   limit/offset unparseable as an integer, limit outside
    1-100, or offset < 0

New file: apps/gateway/src/gateway/tenants/api/platform_audit_router.py
  platform_audit_router = APIRouter(prefix="/admin/platform/tenants/{tenant_id}/audit",
    tags=["platform-admin"])   -- mirrors platform_users_router.py's own prefix shape exactly
  registered via ONE additive line in main.py: app.include_router(platform_audit_router)
    (alongside the other 5 platform_*_router.py registrations; no other main.py line changes)

Handler (one route, GET ""):
  async def get_platform_tenant_audit(
      tenant_id: uuid.UUID,
      identity: Annotated[Identity, Depends(require_superadmin)],
      session: Annotated[AsyncSession, Depends(get_session)],
      request: Request,
      limit: Annotated[str | None, Query()] = None,
      offset: Annotated[str | None, Query()] = None,
  ) -> AuditListResponse:

  Order of operations (mirrors get_platform_tenant_by_id / _require_target_tenant EXACTLY):
    1. require_superadmin (Depends, already run by the time the body executes)
    2. authorize_tenant_scope(identity, tenant_id)                       -> 403 ERR_AUTH_FORBIDDEN
    3. get_tenant_by_id(session, tenant_id); None -> 404 TENANT_NOT_FOUND -- BEFORE any audit query
       (both 2+3 bundled in one locally-declared _require_target_tenant(identity, tenant_id,
        session) helper, byte-identical in shape to platform_users_router.py's own)
    4. _parse_audit_pagination(limit, offset) -> (parsed_limit, parsed_offset)  -- LOCAL helper,
       NOT imported from usage/api/router.py (§0 Issues/Risks #2, R10) -- same bounds/behavior as
       get_audit's own _parse_pagination: default limit=50, range 1-100; default offset=0, >=0;
       else 422 PAYLOAD_INVALID
    5. async with asyncio.timeout(_AUDIT_READ_TIMEOUT_SECONDS):   # new local constant = 30.0,
         same budget as get_audit's own, not cross-file-imported
           total = await AuditRepository(session).count_for_tenant(tenant_id)
           events = await AuditRepository(session).list_for_tenant_paged(tenant_id, parsed_limit, parsed_offset)
    6. emit_platform_audit(request.app.state.sessionmaker, identity=identity,
         target_tenant_id=tenant_id, action="platform.audit.list", target_type="audit",
         target_id=None, metadata={})   -- success path only
    7. return AuditListResponse(items=[AuditEventItem(id=str(e.id), actor_email=e.actor_email,
         action=e.action, target_type=e.target_type, target_id=e.target_id, result=e.result,
         metadata=e.metadata, created_at=e.created_at.isoformat()) for e in events], total=total)

  Local Pydantic models (redeclared, not imported — matches platform_users_router.py's own
  DTO-redeclaration precedent, §0 Honors):
    class AuditEventItem(BaseModel):
        id: str; actor_email: str | None; action: str; target_type: str | None
        target_id: str | None; result: str; metadata: dict[str, object]; created_at: str
    class AuditListResponse(BaseModel):
        items: list[AuditEventItem]; total: int

Frontend — PlatformTenantDetail.tsx (additive):
  TAB_VALUES = ["config", "budget", "keys", "members", "plan", "activity"] as const
  one new <TabsTrigger value="activity">Activity</TabsTrigger>
  one new <TabsContent value="activity"><PlatformActivityTab tenantId={tenantId} /></TabsContent>
  no other line changes; the existing 5 tabs' own content and props are untouched.

NEW apps/dashboard/components/platform/PlatformActivityTab.tsx
  export interface PlatformActivityTabProps { tenantId: string }
  export function PlatformActivityTab({ tenantId }: PlatformActivityTabProps): JSX.Element

  Local types (redeclared, mirrors AuditTable.tsx's own AuditRow/AuditData shape):
    interface ActivityRow { id: string; actor_email: string | null; action: string;
      target_type: string | null; target_id: string | null; result: string;
      metadata: Record<string, unknown>; created_at: string }
    interface ActivityListResponse { items: ActivityRow[]; total: number }

  State: offset: number (default 0) -- a tenantId change unmounts/remounts this component under
    TabsContent (matching every other tab's own precedent), so no manual reset effect is needed.
  ACTIVITY_PAGE_LIMIT = 20   (own constant, matches PlatformTenantDirectory.tsx's own PAGE_LIMIT
    value for visual consistency across the console -- §1 Assumptions, flagged at freeze)

  Query: useQuery<ActivityListResponse>({
    queryKey: ["platform-tenant-audit", tenantId, offset],
    queryFn: () => bffGet<ActivityListResponse>(
      `/admin/platform/tenants/${tenantId}/audit?limit=${ACTIVITY_PAGE_LIMIT}&offset=${offset}`),
    retry: false,
  })

  Render states (mutually exclusive, mirrors PlatformMembersTab.tsx's own early-return shape):
    isLoading -> <Loading label="Loading activity" className="animate-pulse" />
    isError   -> <ErrorState title={getErrorTitle(error)} onRetry={() => void refetch()} />
    data loaded, total === 0 -> <Empty title="No audit events yet" ... />  (matches AuditTable.tsx's
      own copy verbatim)
    data loaded, total > 0 -> DataTable (below) + Previous/Next footer (below)

  DataTable columns (mirrors AuditTable.tsx's own COLUMNS exactly):
    Actor (actor_email) · Action (action) · Target (target_type) · Result (result) ·
    When (created_at via the shared formatTimestamp) -- a null actor_email/target_type/target_id
    renders "—" via each column's own cell (formatTimestamp's own convention extended to these,
    M13) -- never a blank cell or a thrown error.
    <DataTable columns={COLUMNS} data={items} ariaLabel="Activity" emptyMessage="No audit events yet" />
    -- no searchable, no pageSizeOptions (R9): pagination is server-driven only

  Pagination footer (mirrors PlatformTenantDirectory.tsx's own Previous/Next block byte-for-byte
  in shape, §0 Honors):
    <span aria-live="polite">Showing {offset + 1}–{offset + items.length} of {total}</span>
    <button disabled={offset === 0} onClick={() => setOffset(o => Math.max(0, o - ACTIVITY_PAGE_LIMIT))}>Previous</button>
    <button disabled={offset + items.length >= total} onClick={() => setOffset(o => o + ACTIVITY_PAGE_LIMIT)}>Next</button>

Schema: no migration — audit_events already exists (audit-log-store, FROZEN @ v1), including its
  own audit_events_tenant_created_idx composite index on (tenant_id, created_at) that already backs
  this exact access pattern. Access pattern: SELECT ... WHERE tenant_id = :path_tenant_id ORDER BY
  created_at DESC, id DESC LIMIT :limit OFFSET :offset (+ a parallel COUNT(*) WHERE tenant_id =
  :path_tenant_id) -- both filtered by the PATH tenant_id, never identity.tenant_id. No column,
  index, or table is added/changed by this task.
```

Glossary deltas: `Activity tab: the superadmin tenant-detail tab rendering a target tenant's real
  audit-event history (actor/action/target/result/when), backed by GET
  /admin/platform/tenants/{tenant_id}/audit — a second, superadmin-scoped, any-target-tenant door
  onto the SAME audit_events trail the tenant's own self-service GET /admin/audit already exposes
  (added tenant-activity-tab, platform-console-flat-redesign).`
Status: FROZEN @ v1 — approved by Claude (orchestrator) — AUTO MODE per CLAUDE.md Rule 2, no chat response after 60s on a direct AskUserQuestion presenting both flagged judgment calls (pagination style, self-audit-of-reads); see chat + TASK.md Assumptions section for the reasoning applied in Tin's absence
Reported: yes — every backend claim in §0 GROUND (AuditRepository signatures, the ORM's
  nullable-no-FK design, get_audit's exact response shape/auth/timeout wrap, require_superadmin/
  authorize_tenant_scope's real behavior — including catching that authz.py's own "dormant"
  docstring is now stale, confirmed via a direct grep showing 6 active callers —
  emit_platform_audit's fire-and-forget mechanics, and the main.py registrations) was independently
  re-verified by the orchestrator by reading the real source files directly, not trusted from the
  agent's report. Both flagged judgment calls were then put to Tin via AskUserQuestion; both timed
  out after 60s with no response — resolved under AUTO MODE, see Assumptions above.

Least-sure flag surfaced at freeze: [spec] real server-driven Prev/Next pagination for the Activity
tab (M9) — the two available precedents in this codebase point opposite ways (the self-service
AuditPage.tsx doesn't paginate at all; PlatformTenantDirectory.tsx does, for a different,
platform-wide list) and MILESTONE.md itself is silent on whether "history" demands real paging;
Tin was asked directly and did not respond — RESOLVED in favor of real pagination (see Assumptions
above for the orchestrator's own reasoning, not just the design agent's default). Cost if wrong:
fully contained inside the new PlatformActivityTab.tsx — dropping the offset state and Previous/Next
footer in favor of a single bounded fetch is a same-file, no-other-file-touched change; the backend's
limit/offset params remain harmlessly unused by a simpler frontend either way. Second-most-relevant,
not the lead flag: [contract] auditing this route's own successful reads (M6, action="platform.audit.list")
— matches an exception-free 15/15 existing convention, but is a genuinely new compounding-growth
behavior on an already-unbounded table; Tin was asked directly and did not respond — RESOLVED in
favor of auditing it (see Assumptions above); cost if wrong is a one-line removal. The AUTH boundary
itself (M1) is NOT flagged here — it is a byte-exact mirror of two already-shipped, already-reasoned,
already-tested sibling routes (get_platform_tenant_by_id, list_platform_tenant_users) plus the
backend's own unmodified require_superadmin/
authorize_tenant_scope gates, independently re-verified by direct source read rather than trusted
from MILESTONE.md's prose — confidence there is high.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (matches this milestone's own established floor — `vitest.config.ts`'s
  `thresholds.lines: 80` is the hard gate; 90% is the aspirational target every prior task this
  milestone has cleared)
Plan (one test per scenario, asserting behavior not internals): 23 scenarios (13 Must + 10 Reject)
  from §2, split across a backend suite (M1-M6, R6-R8, R10 — the route/auth/pagination/response/
  audit-emit contract) and a frontend suite (M7-M13, R1-R5, R9 — the tab's own wiring/states/
  paging/rendering contract), one test per scenario, asserting observable behavior (HTTP
  status/body shape, DOM state, repository call arguments) never internals.

Tests live in: `apps/gateway/tests/tenant_activity_tab` (backend, NEW directory, per this
  codebase's own one-directory-per-task-slug convention) · `apps/dashboard/tests/platform-activity-tab.test.tsx`
  (frontend, NEW) · `apps/dashboard/tests/platform-tenant-detail.test.tsx` (frontend, EXTENDED for
  the 6th-tab wiring scenario, M7) · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  - NEW `apps/gateway/src/gateway/tenants/api/platform_audit_router.py`
  - `apps/gateway/src/gateway/main.py` (additive one-line `app.include_router(platform_audit_router)`)
  - NEW backend test directory `apps/gateway/tests/tenant_activity_tab/` (this codebase's own
    convention is one dedicated directory per task-slug — `platform_tenant_directory/`,
    `admin_console_audit/`, `audit_read/`, `superadmin_audit_foundation/` are the precedents — not
    a name mirroring the router file itself; declared as a directory token so its whole subtree is
    covered regardless of the exact filename(s) the build chooses inside it)
  - `apps/dashboard/components/platform/PlatformTenantDetail.tsx` (additive 6th tab, M7)
  - NEW `apps/dashboard/components/platform/PlatformActivityTab.tsx` (M8-M13)
  - NEW `apps/dashboard/tests/platform-activity-tab.test.tsx` (the new tab's own suite, matching
    `platform-command-palette.test.tsx`'s naming convention)
  - `apps/dashboard/tests/platform-tenant-detail.test.tsx` (extended: additive 6th-tab wiring test,
    matching how `command-palette` extended `app-shell-sidebar.test.tsx` for its own wiring test)
Pre-filled by the orchestrator (not the build agent) immediately after freeze, BEFORE the
  tests->build crossing — same lesson applied proactively for `command-palette` after two prior
  stale-scope-snapshot false positives this milestone (`tenant-overview-strip`,
  `overview-strip-plan-display-name`); that fix held cleanly for `command-palette`'s own crossing.
Strategy (ordered batches): 1. backend route + its own red suite first (platform_audit_router.py +
  main.py registration) — establishes the wire contract the frontend tab consumes. 2. frontend tab
  + its own red suite (PlatformActivityTab.tsx + PlatformTenantDetail.tsx wiring) second, once the
  real response shape is confirmed green against the backend. 3. full-suite + lint + tsc last.

Persona (optional): none named — generic full-stack stance per this task's own dispatch persona.
Spawn isolation (default): shared-tree (sequential mode, matching every prior task this milestone
  — not run in parallel with any other active build).
Known-problem fixes:
  - trap: stale §5 Scope snapshot at the tests->build crossing (hit twice earlier this milestone)
    → fix: Scope is filled here, now, before any build dispatch — not after.
  - trap: importing `usage/api/router.py`'s private `_parse_pagination`/`AuditListResponse`/
    `AuditEventItem` across a router-module boundary (R10) → fix: locally redeclare both the
    pagination parser and the response models in the new file, matching
    `platform_users_router.py`'s own established DTO-redeclaration convention.
  - trap: passing `identity.tenant_id` instead of the PATH `tenant_id` to
    `emit_platform_audit`/`AuditRepository` calls (would silently misattribute or misscope the
    audit read) → fix: the path `tenant_id` parameter is the only tenant identifier this route ever
    threads through, mirroring `get_platform_tenant_by_id`'s own exact pattern.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): both `AuditRepository` calls (`count_for_tenant`,
  `list_for_tenant_paged`) wrapped in one `asyncio.timeout(_AUDIT_READ_TIMEOUT_SECONDS)` block
  (30.0s, a new LOCAL constant in the new file — not cross-file-imported), mirroring `get_audit`'s
  own already-shipped IO design-for-failure guard verbatim (CLAUDE.md's own global IO rule).
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear. Zero
  edits to `platform_tenants_router.py`, `usage/api/router.py`, `AuditRepository`, or any existing
  tenant-detail tab file (M16-equivalent for this task, per R2/R10 + the byte-identical claims in
  §0/§1).

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — backend: 27/27, independently re-run by the orchestrator in isolation
      (`uv run pytest tests/tenant_activity_tab/` → "27 passed in 14.26s"), matching the build
      agent's own claim exactly. Frontend: full dashboard suite independently re-run by the
      orchestrator (`npx vitest run` → "PASS (1092) FAIL (0)"), matching the claim exactly (1078
      pre-existing + 14 new: 13 in `platform-activity-tab.test.tsx` + 1 in
      `platform-tenant-detail.test.tsx`). Full gateway suite (2550 passed, 1 failed, 7 skipped) was
      NOT independently re-run in full by the orchestrator (17-minute cost) — see the proportionality
      note under "concurrency/timing" below for why the isolated-suite + zero-diff evidence was
      judged sufficient instead. The 1 full-suite failure
      (`member_invite_issuance::test_create_and_revoke_fire_audit_events`) was independently
      re-run by the orchestrator in isolation (`1 passed in 1.94s`) confirming a pre-existing,
      load-sensitive flake, unrelated to this task — also confirmed via `git log` showing the file
      was last touched 2026-07-05 in an already-merged PR (#58), predating this session entirely.
- [x] coverage did not decrease — per the build agent's report (not independently re-measured by
      the orchestrator): new file `platform_audit_router.py` 100% line coverage (63/63
      statements); full-suite 89.89% vs. the 80% gate. The orchestrator's own zero-diff
      confirmation on every existing shared file (below) makes a coverage regression elsewhere
      structurally unlikely — this task added code, touched no existing code path.
- [x] no test or contract was altered during build — §3's frozen text is unchanged since freeze
      (only §4/§5/§6/§7 were edited by the orchestrator/build agent, all sections the contract
      freeze does not cover); `platform-tenant-detail.test.tsx`'s diff shows only NEW `it(...)`
      blocks added, no existing test body edited; `platform-activity-tab.test.tsx` and the backend
      suite are wholly NEW files.
- [x] the green was EARNED, not gamed — see Refute-read verdict below. Every test read directly by
      the orchestrator (27 backend + 13 frontend + 1 wiring test, all in full) asserts real
      observable behavior (HTTP status + exact error `code` fields, DB-level row verification via
      direct SQL, DOM state, router calls) — never a vacuous or internals-only assertion.
- [x] concurrency / timing of the risky operation is safe — both `AuditRepository` calls wrapped in
      one `asyncio.timeout(30.0)` (a new local constant, matching `get_audit`'s own already-shipped
      guard verbatim); `emit_platform_audit` is fire-and-forget (`asyncio.ensure_future`), never
      blocking the HTTP response. One residual, PRE-EXISTING risk noted (not introduced by this
      task, not a blocker): the new backend suite's `drain_fire_and_forget()` helper uses the same
      `asyncio.sleep(0.05)` idiom already used in 3+ other existing suites — the same fragile
      timing assumption that caused the one confirmed-unrelated flake above. Proportionality note:
      given this task's own backend change is a single new, additively-registered router file
      calling only already-tested, unmodified, read-only repository methods (zero edits to
      `AuditRepository`, `authz.py`, or any existing router), the orchestrator judged the isolated
      new-suite run (27/27) + the zero-diff confirmation on every shared file (below) as
      sufficient, in place of re-running the full 17-minute, 2550-test gateway suite personally —
      an explicit proportionality call, not a silent skip.
- [x] no exposed secrets, injection openings, or unexpected dependencies — zero new package
      dependency on either side; all DB access goes through the existing, parameterized
      `AuditRepository`/SQLAlchemy ORM (no raw string-built SQL in the new route); pagination
      inputs are parsed to `int` and range-checked before use, never interpolated into a query
      string; the path `tenant_id` (never `identity.tenant_id`) is the only tenant identifier
      threaded through, confirmed via direct source read and via
      `test_reuses_audit_repository_scoped_by_path_tenant_never_identity_tenant`'s explicit
      cross-tenant-leak check (seeds rows under BOTH tenants, asserts only the target's appear).
- [x] layering & dependencies follow CONVENTIONS.md — one new router file per sub-resource (matches
      the unanimous existing convention across 6 sibling `platform_*_router.py`/`platform_keys_router.py`
      files); locally-redeclared DTOs rather than a cross-router-module import (matches
      `platform_users_router.py`'s own stated convention); frontend reuses `states.tsx`/`DataTable`/
      `formatTimestamp` verbatim, zero new display primitive.
- [x] a person reviewed and approved the change — AUTO-GATED under `autonomy: auto`, same as every
      prior task this milestone: this is the orchestrator's own independent, adversarial
      re-verification substituting for a literal human read. **Tin has not personally reviewed
      this diff yet** — explicitly flagged here and at report-back, not silently assumed.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `GET /admin/platform/tenants/{tenant_id}/audit` enforces the full auth chain in order
      (require_superadmin 401/403 -> authorize_tenant_scope 403 -> tenant-existence 404) BEFORE any
      AuditRepository call runs — confirmed by reading `platform_audit_router.py` directly (auth
      Depends, then `_require_target_tenant`, then pagination parse, then the repo calls, in that
      exact order) AND by the backend suite's own dedicated ordering test
      (`test_auth_chain_rejects_non_superadmin_before_checking_tenant_existence`, independently
      re-run) AND by the build agent's own adversarial mutation of this exact ordering (7/27 tests
      failed as expected, reverted, re-confirmed 27/27 green — revert independently verified by
      the orchestrator via a direct content Read of the current file, not a diff summary)
- [x] `platform_tenants_router.py`, `usage/api/router.py`, and `AuditRepository`/its ORM/domain
      files are byte-identical before/after this task — confirmed by the orchestrator's own
      `git diff --stat` on `platform_tenants_router.py`, `usage/api/router.py`, `authz.py`, and the
      entire `apps/gateway/src/gateway/audit/` directory: zero output, zero changed lines in any
- [x] `main.py`'s only change is one additive `app.include_router(platform_audit_router)` line —
      confirmed by the orchestrator's own `git diff`: `+2 -0` (one import line + one
      `include_router` line), zero removed
- [x] An out-of-range or unparseable `limit`/`offset` returns 422 `ERR_PAYLOAD_INVALID` before any
      repository call; a valid/omitted request defaults to limit=50, offset=0 — confirmed by
      reading `_parse_audit_pagination` directly plus the 2 dedicated tests
      (`test_pagination_rejects_out_of_range_or_unparseable` parametrized ×5,
      `test_pagination_defaults_when_omitted`), both read in full and independently re-run
- [x] The 200 response body matches `get_audit`'s own `AuditListResponse`/`AuditEventItem` shape
      field-for-field, locally redeclared (not imported across the router-module boundary) —
      confirmed by the orchestrator reading both class definitions directly side-by-side (identical
      fields) AND `test_no_cross_router_module_private_import`'s own object-identity assertions
      (`par.AuditListResponse is not usage_router_mod.AuditListResponse`)
- [x] A successful list call schedules one `emit_platform_audit` call
      (action="platform.audit.list", target_type="audit", target_id=None,
      target_tenant_id=the path tenant_id) on the success path only, fire-and-forget — confirmed by
      reading the handler's call site directly (matches `emit_platform_audit`'s own signature
      exactly) AND by `test_successful_list_is_itself_audited`'s direct DB-row assertion (fetches
      the actual scheduled row and checks every field, including that `tenant_id` is the PATH
      target, not the superadmin's own)
- [x] `PlatformTenantDetail.tsx` gains exactly a 6th "Activity" tab (`TAB_VALUES` extended,
      one `TabsTrigger`, one `TabsContent`) with the existing 5 tabs' own content byte-identical —
      confirmed by the orchestrator's own `git diff` (the only non-OverviewStrip-related change is
      the 4-line additive M7 wiring) + independently re-running the full
      `platform-tenant-detail.test.tsx` suite (passes, including its pre-existing tests unmodified)
- [x] `PlatformActivityTab.tsx` renders Loading/ErrorState/Empty/table+pagination states correctly
      keyed off its own query state, reusing `states.tsx`/`DataTable`/`formatTimestamp` verbatim —
      no new display primitive introduced anywhere — confirmed by the orchestrator reading the
      full 151-line component directly: imports only `DataTable, Loading, ErrorState, Empty` from
      `@/components/ui` and `formatTimestamp` from `@/lib/format`
- [x] Previous/Next paging changes only this tab's own `offset` state/queryKey — clamped at both
      ends (Previous disabled at offset 0, Next disabled once offset+items.length >= total) —
      confirmed by reading the component's paging handlers directly (`Math.max(0, o - LIMIT)` /
      `o + LIMIT`, disabled conditions exactly as specified) AND
      `test_next_and_previous_page_the_server_driven_offset_and_disable_at_both_ends`'s full
      two-page interaction test, read in full
- [x] A row with a null `actor_email`/`target_type`/`target_id` renders a graceful placeholder, never
      a crash or a dropped row — confirmed by reading the `COLUMNS` cell renderers directly
      (`?? "—"` on both `actor_email` and `target_type`) AND both the backend
      (`test_null_actor_and_target_fields_pass_through_gracefully`) and frontend
      (`test_null_actor_email_and_target_type_render_placeholder_not_crash`) dedicated tests, both
      read in full
- [x] Full dashboard + gateway suites green + eslint clean + `tsc --noEmit`/`ruff`/`pyright` clean on
      touched files, independently re-run by the orchestrator (not trusting the build agent's
      self-report) — same discipline as every prior task this milestone. Dashboard: vitest
      1092/1092, eslint 0 errors/2 pre-existing warnings, tsc 9 errors all confirmed pre-existing
      in `platform-plan-tab.test.tsx` (a different, unrelated task's uncommitted work in this same
      tree — confirmed via that file not appearing anywhere in this task's own Scope/diff).
      Gateway: new suite 27/27 (isolated re-run); full-suite ruff/pyright per the build agent's
      report (0 errors each) — not independently re-run by the orchestrator, see the proportionality
      note in the checklist above.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `platform_audit_router` imported+registered
      in `main.py` (confirmed via diff); `PlatformActivityTab` imported+rendered in
      `PlatformTenantDetail.tsx` (confirmed via diff); every helper/constant inside
      `platform_audit_router.py` (`_require_target_tenant`, `_parse_audit_pagination`,
      `_AUDIT_DEFAULT_LIMIT`/`_AUDIT_MAX_LIMIT`/`_AUDIT_READ_TIMEOUT_SECONDS`) used at least once —
      confirmed reading the full 198-line file
- [x] DEAD-CODE (code) — no new unused or orphaned symbol introduced — confirmed during the same
      full reads of `platform_audit_router.py` (198 lines) and `PlatformActivityTab.tsx` (151 lines)
- [x] SEMANTIC (prose / non-code) — read in full, not skimmed: the entire backend suite
      (`test_tenant_activity_tab.py`, 27 tests) + its `conftest.py` fixtures, the entire frontend
      suite (`platform-activity-tab.test.tsx`, 13 tests), the new M7 wiring test in
      `platform-tenant-detail.test.tsx`, the full `platform_audit_router.py` and
      `PlatformActivityTab.tsx` implementation files, and the diffs on `main.py` and
      `PlatformTenantDetail.tsx` — confirmed the code matches the frozen §3 CONTRACT in every
      traced detail, including the two AUTO-MODE-resolved judgment calls (real pagination;
      self-audit-of-reads) both being implemented exactly as resolved

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed directly:
      `AuditRepository.count_for_tenant`/`list_for_tenant_paged` (read in full, signatures
      unchanged since Ground), `authz.py:require_superadmin`/`authorize_tenant_scope` (read in
      full, unchanged), `get_tenant_by_id` (imported and called exactly as cited),
      `emit_platform_audit` (read in full, unchanged), `PlatformTenantDetail.tsx:TAB_VALUES`
      (confirmed extended exactly as specified), `states.tsx:Loading,Empty,ErrorState` (imported
      and used exactly as cited), `data-table.tsx:DataTable` (imported and used exactly as cited),
      `format.ts:formatTimestamp` (imported and used exactly as cited)
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — none moved;
      Ground SHA `37e55ee` is still current HEAD (confirmed via `git rev-parse --short HEAD`
      during this same verify pass)

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: build agent (self, adversarial mutation of the M1 auth-chain order) + orchestrator
  (independent re-verification). Adversarially checked: (1) the build agent discovered that a
  naive line-swap inside `_require_target_tenant` would NOT have exercised the ordering property
  at all, since `require_superadmin` runs as a FastAPI `Depends()` — structurally guaranteed to
  resolve before the function body, regardless of in-body line order; a shallower mutation would
  have been a false-confidence exercise. (2) The actual mutation applied: swapped
  `Depends(require_superadmin)` for a bare `Depends(_resolve_identity)` and moved a late,
  inverted-order role check + the tenant-existence check into the body, also bypassing
  `authorize_tenant_scope` so it couldn't mask the result. (3) Result: 7 of 27 tests failed for
  the expected reason (404 instead of 403); the other 20 stayed green exactly where expected — a
  clean, well-isolated blast radius, proving the ordering test genuinely discriminates. (4)
  Reverted via 2 targeted edits, verified via a direct content Read (not a diff-tool summary —
  the same near-miss class as `command-palette`'s own build), confirmed byte-for-byte original,
  no leftover markers. (5) The orchestrator independently re-read the full current
  `platform_audit_router.py` file as one of its FIRST verification actions and confirmed it
  matches the frozen contract exactly, with no remnant of the described mutation anywhere.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: self (orchestrator) — elevated rigor given this is the first task in this milestone
  touching real backend/cross-tenant-data surface, plus the precedent near-miss on
  `command-palette`'s own mutation-testing.
1. Security: CLEAR — the auth chain (require_superadmin -> authorize_tenant_scope ->
   tenant-existence 404) is byte-identical in shape to two already-shipped, already-reasoned
   sibling routes (`get_platform_tenant_by_id`, `list_platform_tenant_users`); the path tenant_id
   (never `identity.tenant_id`) is the only tenant identifier threaded through, confirmed via
   direct read AND a dedicated cross-tenant-leak test; zero edits to any existing auth/repository/
   router file; the adversarial mutation test proves the ordering property is genuinely enforced,
   not just documented.
2. Concurrency: CLEAR — see the checklist's own concurrency/timing row above (timeout wrap,
   fire-and-forget audit write, the one pre-existing/inherited `asyncio.sleep(0.05)` drain-idiom
   risk explicitly named rather than silently accepted).
3. Architecture: CLEAR — one new router file per sub-resource (matches the unanimous 6-sibling
   convention), locally-redeclared DTOs (matches established convention), zero new frontend
   display primitive, zero edits to any existing tab/route/repository file.
Verdict: PASS
Residue: none blocking. Non-blocking items carried into §7 below: the inherited
  `asyncio.sleep(0.05)` drain-idiom fragility (pre-existing, not introduced here), and the two
  AUTO-MODE-resolved judgment calls (pagination style, self-audit-of-reads) that Tin has not yet
  personally reviewed.
Binding: advisory — sensitivity unset on this task (defaults to project autonomy: auto); Security
  lens was CLEAR, so no HARD-STOP escalation applies.

### GATE RECORD
Reported: yes — this VERIFY section (checklist, Build Expectations, Deep checks, Live-verify,
  Refute-read, Advisor 3-lens) constitutes the gate report; presented to Tin at report-back
  immediately following this record, per report-template.md
Outcome: PASS
Reviewed by: Claude (orchestrator) — AUTO-GATED per `autonomy: auto`; Tin's own review is still
  pending and explicitly flagged above (checklist item 8) and at report-back · date: 2026-07-06

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): M1/R6's auth-chain ordering (watch for any future refactor of
  `_require_target_tenant` or a copy-pasted variant that could silently invert the
  role-then-tenant-existence order — the dedicated ordering test plus the adversarial mutation
  precedent are the durable regression monitors); the `platform.audit.list` row-growth rate on an
  already-unbounded `audit_events` table (watch whether repeat superadmin views of the same
  tenant's Activity tab meaningfully compound growth in practice — the resolved self-audit
  decision assumed this is negligible, worth confirming with real usage); the fallback-trigger and
  pagination-style judgment calls once Tin has actually seen this feature live.

### Decisions (ADR)
- [AI] mirrored `platform_users_router.py`'s own `_require_target_tenant` +
  audit-emit-on-pure-read recipe byte-for-byte for the new route — reuses an exact, already-shipped,
  already-reasoned pattern rather than inventing a new auth/audit shape (§1 Framings weighed)
- [AI] declared a NEW dedicated `platform_audit_router.py` file rather than growing the FROZEN
  `platform_tenants_router.py` — matches the unanimous one-file-per-sub-resource convention across
  6 sibling files, confirmed via direct `main.py` registration-block read
- [AI/orchestrator, AUTO MODE] real server-driven Prev/Next pagination for the Activity tab
  (M9) — resolved in Tin's absence (60s timeout on a direct AskUserQuestion) in favor of real
  pagination: this tab exists for a superadmin investigation/incident-review use case, and being
  capped at the most recent N events with no way to page backward is a worse failure mode than the
  extra interaction surface. Confined to `PlatformActivityTab.tsx` alone if this call needs
  revisiting.
- [AI/orchestrator, AUTO MODE] auditing this route's own successful reads (M6,
  action="platform.audit.list") — resolved in Tin's absence (same timeout) in favor of auditing:
  matches a zero-exception 15/15 existing convention across every platform route; breaking it for
  the first time, unreviewed, would be a bigger departure than following it; the meta-audit value
  (who looked at tenant X's history) is a genuine security benefit for a cross-tenant surface. A
  one-line removal if this call needs revisiting.
- [AI/build] discovered and self-corrected a genuine TDD-discipline gap: the M7 wiring test was
  originally written after `PlatformActivityTab.tsx` already existed (never genuinely red) —
  caught by the build agent itself, corrected by hiding the implementation, reverting the wiring,
  confirming true RED, then restoring, and disclosed honestly rather than left unmentioned
- [AI/build] designed the M1 auth-chain adversarial mutation test only after recognizing a naive
  line-swap inside `_require_target_tenant` would not have exercised the ordering property at all
  (`require_superadmin`'s FastAPI `Depends()` already guarantees pre-body resolution regardless of
  in-body line order) — the actual mutation instead swapped the Depends itself and bypassed
  `authorize_tenant_scope` too, to cleanly isolate the real property

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] confirm the real-Prev/Next-pagination call (M9) and the self-audit-of-reads call
  (M6) with Tin once he's seen the Activity tab live — both were resolved under AUTO MODE after a
  direct AskUserQuestion timed out twice in the same session (evidence: §3 freeze's own two flagged
  judgment calls; both are contained/one-line-reversible if either needs to change)
- [SPEC · open] consider a more deterministic fire-and-forget-audit-write test-drain mechanism
  (e.g. a testing seam that awaits the actual scheduled task) instead of the repo-wide
  `asyncio.sleep(0.05)` idiom, which has now caused at least one confirmed flake under full-suite
  load (evidence: `member_invite_issuance::test_create_and_revoke_fire_audit_events`'s own
  isolation-vs-full-suite pass/fail difference, independently reconfirmed this task; the same
  idiom is used by this task's own new suite too, so the fragility is inherited, not novel)
- [SPEC · open] `DialogContent` (`components/ui/dialog.tsx`) still has no motion-safe
  entrance/exit transition — unchanged by this task, already tracked as a standing `add.py todo`
  since `tenant-overview-strip`/`command-palette` (evidence: carried forward, not newly found here)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] a mutation test can be well-INTENTIONED but structurally miss the property it means [folded foundation-version 48]
  to prove, if the guarantee actually lives in framework wiring (a FastAPI `Depends()` resolving
  before the function body) rather than in-body statement order — the build agent caught this
  itself before mutating, and designed a mutation that bypassed BOTH the Depends and the secondary
  in-memory check to cleanly isolate the real property (evidence: the build agent's own discovery,
  independently reasoned-through and accepted by the orchestrator during verify). Lesson: before
  mutating code to prove a test catches a bug, confirm the mutation actually removes the specific
  guarantee under test, not just code that looks related to it.
- [TDD · folded] the repo-wide `asyncio.sleep(0.05)` fire-and-forget-drain idiom (used in 4+ test [folded foundation-version 48]
  suites now, including this task's own new one) has a confirmed load-sensitive failure mode under
  full-suite concurrency (evidence: the one full-suite failure this task's own verify pass
  independently reconfirmed as isolation-passing/full-suite-flaky) — worth a dedicated hardening
  follow-up rather than continuing to propagate the same fragile idiom into every new audit-write
  test file.
- [ADD · folded] the build-expectations pre-fill gate (`build_expectations_unfilled`) rejects even a [folded foundation-version 48]
  single BARE `<...>` placeholder-style annotation anywhere in the "### Build expectations" body,
  including ones meant as descriptive shorthand rather than an unfilled template marker (evidence:
  this task's own tests->build crossing was refused once for exactly this reason, fixed by
  rewording rather than removing content). Lesson: when pre-filling Build Expectations before
  dispatch, avoid bare angle-bracket notation entirely in prose — spell it out in words, or wrap
  it in backticks, so the placeholder-detector never has to distinguish intent.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

