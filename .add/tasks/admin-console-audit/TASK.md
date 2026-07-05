# TASK: Audit every cross-tenant admin action, attributing the real superadmin actor

slug: admin-console-audit · created: 2026-07-03 · stage: production
milestone: platform-admin-console
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `audit/domain/audit_event.py:AuditEvent` (frozen dataclass, L23-47) — fields: id, tenant_id,
    actor_user_id, actor_email, action, target_type, target_id, result, metadata, created_at.
    `__post_init__` invariant (L42-47, `audit_missing_actor`): `tenant_id is not None` ⟹
    `actor_user_id` MUST be non-None; `tenant_id=None` ("system event") MAY omit it. Trivially
    satisfied by every call site this task adds: 14/15 always carry a real target tenant_id AND a
    real `identity.user_id` (require_superadmin already validated a real JWT); the 1 bulk-list
    exception uses `tenant_id=None` with `actor_user_id` still present (permitted, not required
    to be absent).
  - `audit/application/audit_writer.py:record_audit(session_factory, event) -> None` (L30-58,
    audit-log-store TASK.md §3, FROZEN @ v1) — fire-and-forget: own SEPARATE session, swallows ALL
    exceptions, logs `_log.warning(...)` on failure. MUST be scheduled via
    `asyncio.ensure_future`/`asyncio.create_task`, never bare-awaited on a request path. Zero
    lines need to change — reused verbatim, exactly as every existing consumer already does.
  - `audit/infrastructure/audit_repository.py:AuditRepository` (record/list_for_tenant/
    count_for_tenant/list_for_tenant_paged) + `audit_events_orm.py:AuditEventRow` — reused
    unchanged; zero schema change, zero migration.
  - **The established self-service `record_audit` convention — confirmed via a repo-wide
    `search_for_pattern` on `record_audit\(` (not assumed) — 14 call sites across 11 files**, ALL
    following the IDENTICAL router-layer shape: `asyncio.ensure_future(record_audit(
    request.app.state.sessionmaker, AuditEvent(id=uuid.uuid4(), tenant_id=..., actor_user_id=
    identity.user_id, actor_email=identity.email, action="<noun>.<verb>", target_type=...,
    target_id=..., result="success", metadata={...no secret...}, created_at=datetime.now(UTC))))`
    — scheduled AFTER the mutation's own commit/use-case call succeeds, never before. Confirmed
    action-name vocabulary (dotted `noun.verb`): `key.create` (keys/api/router.py:131),
    `key.rotate` (:435), `key.revoke` (:492) — **PATCH has no self-service audit call at all**
    (keys/api/router.py's `patch_key`, L268-381, ends with zero `record_audit` reference,
    confirmed by direct read); `user.role_assign` (tenants/api/users_router.py:158);
    `budget.update` (budgets/api/router.py:145); `member.role_assign` (teams/api/router.py:222 —
    a different bounded context, cited only as corroborating convention evidence);
    `provider_key.put`/routing-admin actions (proxy/api/provider_keys_admin_router.py,
    routing_admin_router.py); `ops.platform_credential_resolve` (ops/api/deps.py,
    superadmin-audit-foundation Part A — the ONE precedent auditing non-success outcomes,
    `result="error"`/`"denied"`, but only for OPERATIONAL/config failures on an
    already-authorized ops caller, never an AUTHORIZATION rejection); `auth.superadmin_login`
    (tenants/application/use_cases.py + auth/application/use_cases.py, Parts B/C — the immediately
    preceding, same-session precedent for a distinguishing action-name PREFIX on
    platform/superadmin-scoped events, `auth.`/`ops.`). **Zero of the 14 existing call sites
    audit a REJECTED/failed attempt** (403/404/422) — every one fires strictly after its own
    success; self-service `PATCH /admin/keys/{key_id}`, `PUT /admin/cache`, `PUT /admin/guardrails`
    have NO audit call at all, success or failure (same grep: zero hits in `cache_router.py`/
    `guardrail_router.py`). **Zero of the 14 existing call sites audit a READ (GET)** — the
    established convention is write-only (same grep: no GET handler anywhere calls
    `record_audit`).
  - `usage/api/router.py:get_audit` (L705-745, FROZEN @ v1) — the SELF-SERVICE audit-log read
    surface: `GET /admin/audit`, gated `require_permission(Permission.AUDIT_READ)`
    (owner/admin/operator only), reads `AuditRepository.list_for_tenant_paged(identity.tenant_id,
    ...)` — **tenant-scoped by the CALLER's OWN `identity.tenant_id`**. `AuditEventItem` (L681-693)
    renders `action` as an unconstrained free-text string — no allowlist/enum anywhere constrains
    which action values may appear (confirmed by reading the full handler), so a brand-new
    `platform.*` action-name family requires ZERO changes here. **Load-bearing consequence**: if a
    cross-tenant `AuditEvent.tenant_id` is set to the TARGET tenant (not the superadmin's own
    platform tenant), the target tenant's own owner/admin/operator sees that row via THEIR
    existing `GET /admin/audit` call, automatically, with zero new endpoint. The converse is a
    real, named gap: a superadmin's OWN `GET /admin/audit` call only ever reads rows WHERE
    tenant_id = the superadmin's platform tenant, so a superadmin has NO existing view of the
    cross-tenant rows they themselves generated across every OTHER tenant — that platform-wide
    view does not exist and is out of this task's scope (see Issues/Risks + Assumptions).
  - `tenants/domain/authz.py:authorize_tenant_scope(identity, target_tenant_id) -> None` (L133-155,
    FROZEN @ v1) and `require_superadmin(identity) -> Identity` (L209-232, FROZEN @ v1) — read in
    full (not trusted from name or from the 3 sibling tasks' own citations): `require_superadmin`
    raises 403 `AUTH_FORBIDDEN` unless `identity.role == Role.SUPERADMIN`; `authorize_tenant_scope`
    raises the same 403 unless SUPERADMIN or same-tenant. On ALL 15 of this task's call sites,
    `require_superadmin` is the ONLY gate that ever actually rejects (per all 3 siblings' own
    disclosed finding, `authorize_tenant_scope`'s reject branch is unreachable here — a SUPERADMIN
    trivially passes its first clause) — **every 403 on these 15 routes originates from
    `require_superadmin` alone**, load-bearing for the "audit rejections?" decision in §1.
  - **The 4 router files this task instruments** (all FROZEN @ v1 by their own owning task, all
    read in full): `tenants/api/platform_tenants_router.py` (112 lines, platform-tenant-directory)
    — `list_platform_tenants` (L64-85, the bulk list, NO `{tenant_id}` path segment, Identity
    param currently discarded as `_`, L66) + `get_platform_tenant_by_id` (L93-111, the
    "1 tenant-lookup GET"); `tenants/api/platform_tenant_config_router.py` (330 lines,
    cross-tenant-config-budget) — GET/PUT × {cache (L91-154), guardrails (L162-238), budget
    (L246-329)}; `put_platform_tenant_budget` (L285-329) is the flagged regression (self-service
    `PUT /admin/budget` already audits; this one doesn't). `keys/api/platform_keys_router.py`
    (480 lines, cross-tenant-keys-members) — `list_platform_tenant_keys` (L167-201),
    `create_platform_tenant_key` (L209-262), `patch_platform_tenant_key` (L270-384),
    `rotate_platform_tenant_key` (L392-448), `revoke_platform_tenant_key` (L456-479).
    `tenants/api/platform_users_router.py` (204 lines, cross-tenant-keys-members) —
    `list_platform_tenant_users` (L113-128), `assign_platform_tenant_user_role` (L136-204 —
    ALREADY contains an `await session.commit()` at L202, added during that task's own Build to
    fix an unrelated pre-existing persistence gap in the reused `UserRoleRepository.update_role`;
    this task's audit call must land AFTER that commit). **None of these 15 handlers currently
    import `fastapi.Request`** (confirmed: all 4 files import only `APIRouter, Depends` [+
    `Query` for the tenants router] from `fastapi`) — every one needs a new `request: Request`
    parameter, matching the ONLY established pattern anywhere in this codebase for reaching the
    sessionmaker. A second repo-wide grep (`session_factory.*Depends|get_sessionmaker`) confirms
    **no `Depends`-based sessionmaker accessor exists anywhere** — every hit (memory/api/router.py,
    proxy/api/deps.py, proxy/api/audio_deps.py, tenants/api/deps.py) reads it off
    `request.app.state.sessionmaker` through a plain `request: Request` parameter. Adding
    `Request` is not a shortcut avoided elsewhere — it is the one and only convention.
  - `apps/gateway/tests/test_users_role.py:test_owner_assigns_any_tier` (L150-208) — the concrete
    test-assertion convention for a `record_audit` call site: issue the request, `await
    asyncio.sleep(0.05)` (let the fire-and-forget task complete), then `SELECT action, metadata
    FROM audit_events WHERE action = '<action>' ORDER BY created_at DESC LIMIT 1` via the test's
    own `db_session` fixture (a SEPARATE session from the request's own — proves durability, not
    just same-transaction visibility). Reused unchanged by this task's own future §4.
  - `.add/tasks/superadmin-audit-foundation/TASK.md` §3 Part A (ops/api/deps.py) — the ONE existing
    precedent for a "system-level" audit event (`tenant_id=None`) on a non-tenant-scoped action,
    and the only precedent anywhere for auditing more than one `result` value — confirms
    `AuditEvent`'s `tenant_id=None` path is an already-shipped, working pattern this task extends,
    not a new one it invents.

Context (working folder): `.add/milestones/platform-admin-console/MILESTONE.md` (Scope-In audit
  clause + Exit criterion #4, "Every cross-tenant read/write produces an audit record attributing
  the real superadmin actor" — read literally, with NO route-shape carve-out for the bulk,
  targetless list; "Shared/risky contracts" section names THIS task as owner of "Cross-tenant audit
  event shape (action-name convention + metadata for 'superadmin X acted on tenant Y')" — literally
  asking for BOTH an action-name decision AND a metadata decision); the 3 sibling TASK.md files in
  full (§0 GROUND + §3 CONTRACT + §7 OBSERVE Spec-delta of each — cross-checked against live code,
  not blind-trusted); no `.add/SEAMS.md` entry found covering this scope.
Honors (patterns / conventions): the 14-call-site `record_audit` convention (dotted `noun.verb`
  action names for self-service actions; fire-and-forget via `asyncio.ensure_future`, never a bare
  `await` on a request path; metadata never carries secret material — key.create/key.rotate's own
  metadata never includes the plaintext key, only `key_name`/`superseded_key_id`); reuse-over-invent
  (Shared decision) — reusing `record_audit`/`AuditEvent`/`AuditRepository` verbatim, adding ONE new
  thin, audit-domain-owned helper rather than 15 duplicated call sites or a new port;
  byte-identical-behavior for non-superadmin callers (Scope-Out) — satisfied by construction, since
  every change is additive (a new parameter + one call, success-path only) on routes already
  superadmin-only; no non-superadmin request reaches any of these 15 handlers at all.
Seams consulted: none — no `.add/SEAMS.md` entry found covering this scope.
Anchors the contract cites: `AuditEvent`; `record_audit`; `AuditRepository` (unchanged); the 15
  handler functions named above (per file); `authorize_tenant_scope`/`require_superadmin`
  (unchanged, already called); `request.app.state.sessionmaker` (existing global, same convention
  as all 14 prior call sites); `usage/api/router.py:get_audit` (unchanged, but load-bearing for
  the tenant_id-placement decision).
Issues/Risks (→ feed §1):
  ⚠ **Route-count clarification (verify-yourself finding, per this task's own charter)**: the
    briefed "14 routes" is the exact count for the path SHAPE
    `/admin/platform/tenants/{tenant_id}/...` (1 tenant-lookup + 6 config/budget + 7 keys/members —
    confirmed by direct enumeration of the 4 router files' route decorators, matching the brief
    exactly). It deliberately EXCLUDES the bulk, targetless `GET /admin/platform/tenants` (no
    `{tenant_id}` segment). But MILESTONE.md's own Exit criterion #4 says "every cross-tenant
    read/write" with no such route-shape carve-out, and the bulk list IS a cross-tenant read
    (arguably the broadest one). `AuditEvent`'s `tenant_id=None` "system event" path is a
    ready-made, zero-new-capability way to cover it. §1 decides whether to instrument 14 or 15
    call sites.
  ⚠ **Zero precedent for auditing a READ anywhere in the codebase** — all 14 existing call sites
    are write-only. MILESTONE.md's Exit criterion is explicit ("every cross-tenant read/write"),
    and platform-tenant-directory's own §7 OBSERVE Spec delta explicitly seeds this exact retrofit
    ("admin-console-audit must retrofit an audit row onto the get-one read... not just the
    write-capable tasks"). This task therefore breaks new ground (auditing 6-7 GETs) with zero
    existing precedent to mirror.
  ⚠ **Zero precedent for auditing a REJECTED attempt anywhere in the codebase** — all 14 existing
    call sites fire strictly on their own success path. On these 15 routes specifically, EVERY
    403 originates from `require_superadmin` alone (§0 above) — auditing it would mean either (a)
    invasively modifying `require_superadmin` itself, a FROZEN, widely-shared predicate reused by
    all 15+ routes across 4 files (an audit side-effect does not belong inside a role-gate other
    future callers may reuse with different audit needs), or (b) duplicating a pre-gate check at
    every call site (defeats the purpose of a shared dependency). §1/§2 decide explicitly.
  - `tenant_id` placement is a genuine design fork with an evidence-backed answer: setting it to
    the TARGET tenant (not the superadmin's own platform tenant) makes cross-tenant actions
    automatically visible through the target tenant's OWN existing `GET /admin/audit`
    (tenant-scoped by `identity.tenant_id`) — zero new endpoint needed for the affected tenant to
    see it. The converse gap (a superadmin has no platform-wide view of their OWN cross-tenant
    actions) is real but belongs to a future UI/API task, not this one.
  - `platform_tenants_router.py:list_platform_tenants`'s `Identity` parameter is currently bound
    to `_` (discarded, L66) — instrumenting it requires renaming to a used `identity` binding, a
    small, necessary, non-behavior-changing rename.
  - `platform_users_router.py:assign_platform_tenant_user_role` does not currently fetch the
    user's OLD role before calling `AssignUserRoleUseCase` (self-service's `users_router.py` does,
    L131-133, specifically to populate `old_role` in its audit metadata) — auditing this route
    with full parity requires ADDING that same fetch, not just a bare audit call at the end.
  - Every one of the 15 handlers needs BOTH a new `request: Request` parameter AND one new call —
    "ADD instrumentation" (additive, one param + one line per handler) as the milestone's own
    constraint permits, not a rewrite of the 3 sibling routers' bodies.
Related intent: MILESTONE.md Exit criterion #4 (this task's own deliverable) + "Shared/risky
  contracts" (naming this task as owner of the cross-tenant audit event shape) + the "same
  fire-and-forget/fail-open pattern" clause in Scope-In; no new Glossary term needed beyond the
  `platform.*` action-name convention itself (an audit/observability convention, not a domain
  concept — captured as a Glossary delta in §3 since docs/05 asks new vocabulary be declared).
Ground SHA: ccf411c

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Cross-tenant admin-console audit coverage — every read/write across the 3 sibling
  routers (15 call sites: 14 target-tenant-scoped + 1 system-level bulk list) emits an audit event
  attributing the real superadmin actor.

Framings weighed:
  Instrumentation mechanism: a NEW shared helper `emit_platform_audit(...)` in a NEW file
  `audit/application/platform_audit.py` (chosen) — every one of the 15 call sites needs IDENTICAL
  boilerplate (uuid, timestamp, `asyncio.ensure_future(record_audit(...))` wrapping, the
  `AuditEvent(...)` construction), varying only in 4-5 arguments (target_tenant_id, action,
  target_type, target_id, metadata); centralizing collapses each call site to ONE line, is
  independently unit-testable (shape/invariant tests without spinning up all 4 routers), and lives
  in the audit bounded context (matching where `audit_writer.py`/`audit_event.py` already live)
  rather than privileging one of the 3 consuming bounded contexts (tenants/keys/users) over the
  others · pure per-route inline duplication, mirroring the 14 existing self-service call sites'
  own convention exactly (rejected — every existing site DOES inline its own `AuditEvent(...)` +
  `asyncio.ensure_future(...)` block, the more "convention-faithful" reading, but 15 near-identical
  ~15-line blocks is a meaningfully worse duplication ratio than the existing convention ever
  produced [max 4 near-identical sites in one file]; every existing site's own boilerplate churn —
  `id=uuid.uuid4()`, `created_at=datetime.now(UTC)`, the `# noqa: RUF006` comment — is IDENTICAL
  across all 14, so it was never actually a meaningful per-site customization point) · a decorator
  wrapping each route handler (rejected — metadata genuinely differs per route [budget's persisted
  string vs. guardrails' changed-field-names vs. keys' key_name/superseded_key_id vs. users'
  old_role/new_role]; a generic decorator would need to inspect the handler's return value/locals
  [fragile, untyped] or accept a per-route metadata-building callback [converges back to a
  per-route call anyway, with added indirection and worse FastAPI OpenAPI-signature fidelity]).
  Route coverage: 15 call sites — the 14 target-tenant-scoped routes named in this task's charter
  PLUS the bulk, targetless `GET /admin/platform/tenants` as a system-level (`tenant_id=None`) 15th
  (chosen — MILESTONE.md's Exit criterion has no route-shape carve-out, the bulk list IS a
  cross-tenant read, and `AuditEvent`'s `tenant_id=None` path is an already-shipped,
  zero-new-capability mechanism, precedented by `ops.platform_credential_resolve`) · exactly the
  literal 14 (rejected — would leave the single broadest cross-tenant read completely unaudited
  purely on a route-shape technicality MILESTONE.md's own wording does not make; named explicitly
  rather than silently deferred again the way platform-tenant-directory's own get-one read was
  deferred at ITS freeze).
  Read coverage: audit both reads AND writes, all 15 routes (chosen — MILESTONE.md's Exit
  criterion #4 is unambiguous ["every cross-tenant read/write"] and platform-tenant-directory's
  own §7 Spec delta explicitly seeds this exact retrofit) · write-only, matching the established
  codebase-wide convention (rejected — directly contradicts the Exit criterion's own literal
  wording and the seeded Spec delta; "match existing convention" is the right default ABSENT an
  explicit contrary instruction, but MILESTONE.md supplies exactly that instruction here).
  Rejected-attempt coverage: audit success only; 401/403/404/422 are explicitly OUT of this
  contract's scope (chosen — matches ALL 14 existing call sites' own success-only convention with
  zero exception; auditing a 403 here would require modifying the FROZEN, widely-shared
  `require_superadmin` predicate itself [couples a role-gate to an audit side-effect for ALL its
  callers, not just these 15], and the 403-rate signal is ALREADY a named monitoring metric in all
  3 siblings' own §7 OBSERVE Watch lines — a cheaper, already-earmarked mechanism for exactly this
  signal) · also audit 403 rejections as a system-level event (rejected — the only clean
  implementation path modifies a shared, frozen predicate outside this task's declared scope; zero
  precedent anywhere in 14 call sites for auditing a rejection of any kind) · also audit 404s
  (rejected — the closest precedent, `ops.platform_credential_resolve`'s "tenant missing"/"key
  missing" branches, audits an OPERATIONAL/config failure on an ALREADY-authorized system caller,
  not an "actor targeted a nonexistent resource" case; zero precedent for the latter anywhere).
  `tenant_id` placement: the TARGET tenant (chosen — makes cross-tenant actions automatically
  visible through the target tenant's OWN existing `GET /admin/audit`, zero new endpoint, per §0's
  `get_audit` finding) · the superadmin's own platform tenant (rejected — would make cross-tenant
  actions invisible to the very tenant they affected, defeating "attributing the real superadmin
  actor" in the sense that matters most: the affected party can see it happened).
  Action-name convention: a NEW `platform.<resource>.<verb>` prefix family (chosen — mirrors the
  IMMEDIATELY-PRECEDING, same-session precedent set by superadmin-audit-foundation's own `ops.`
  and `auth.` prefixes for platform/superadmin-scoped actions; MILESTONE.md's own charter for this
  task literally asks for an "action-name convention" as a decision to make, not a reuse to default
  to; instantly greppable/filterable [`action LIKE 'platform.%'`] for "every cross-tenant
  admin-console action" with no JSONB metadata query needed; `usage/api/router.py:get_audit`'s
  `AuditEventItem` renders `action` as an unconstrained free string, so this requires zero changes
  to the existing self-service audit-log read surface) · reuse the EXACT self-service action
  strings verbatim (`budget.update`, `key.create`, etc.) plus a `metadata["via"]` marker for
  provenance (rejected, close call — would let a per-tenant query for "did my budget change" catch
  cross-tenant causes too without a UNION, a real ergonomic upside; but MILESTONE.md's OWN charter
  explicitly calls out "action-name convention" as this task's decision to make, and the
  immediately-preceding `ops.`/`auth.` prefix precedent is the stronger, more recent, in-repo
  signal for what "the right convention" means here).
  Metadata shape: mirror each route's self-service sibling's metadata shape+field names EXACTLY
  where a self-service analog exists (budget.update, key.create, key.rotate, key.revoke,
  user.role_assign — chosen, reuse-over-invent) · design fresh, minimal, non-secret metadata for
  routes with NO self-service analog (cache/guardrails views+updates, key patch/list, user list,
  tenant view/list) — field NAMES only where a full value would be bulky/regex-bearing
  (guardrails: `fields_changed`, not the full config; key patch: `fields_changed`, not every new
  value) mirroring the self-service convention's own general minimalism (key.create's own metadata
  never includes budget/rpm/team_id either, only `key_name`).

Must:
<must>
  - M1: a new shared helper `emit_platform_audit(session_factory, *, identity, target_tenant_id,
    action, target_type, target_id, metadata) -> None` in a NEW file
    `gateway/audit/application/platform_audit.py` constructs one `AuditEvent` (result="success"
    always, per this contract's scope — see Reject) and schedules it via
    `asyncio.ensure_future(record_audit(...))` internally — callers never wrap it themselves.
  - M2: every one of the 14 target-tenant-scoped routes (in `platform_tenants_router.py`
    [get-one only], `platform_tenant_config_router.py` [all 6], `platform_keys_router.py` [all 5],
    `platform_users_router.py` [both]) calls `emit_platform_audit(...)` with
    `target_tenant_id=<the PATH tenant_id>` exactly once, on its success path only, AFTER any
    write's own commit/use-case call has already succeeded (never before).
  - M3: the bulk, targetless `GET /admin/platform/tenants` (`platform_tenants_router.py:
    list_platform_tenants`) ALSO calls `emit_platform_audit(...)` with `target_tenant_id=None` (a
    system-level event, mirroring `ops.platform_credential_resolve`'s own precedent) — 15 call
    sites total.
  - M4: `action` follows the NEW `platform.<resource>.<verb>` convention on all 15 call sites:
    `platform.tenant.list` (bulk), `platform.tenant.view` (get-one), `platform.cache.view`,
    `platform.cache.update`, `platform.guardrails.view`, `platform.guardrails.update`,
    `platform.budget.view`, `platform.budget.update`, `platform.key.list`, `platform.key.create`,
    `platform.key.patch`, `platform.key.rotate`, `platform.key.revoke`, `platform.user.list`,
    `platform.user.role_assign`.
  - M5: `tenant_id` on every one of the 14 target-tenant-scoped events is the TARGET tenant_id
    (the PATH value) — NEVER `identity.tenant_id` (the superadmin's own platform tenant) — so the
    affected tenant's own `GET /admin/audit` (owner/admin/operator, AUDIT_READ) surfaces the row
    automatically. `actor_user_id`/`actor_email` are always `identity.user_id`/`identity.email`
    (the REAL superadmin who acted) — the literal "attributing the real superadmin actor" the
    milestone charters.
  - M6: `metadata` NEVER contains secret/plaintext material — key.create/key.rotate's metadata
    carries `key_name`/`superseded_key_id` only, mirroring self-service exactly, NEVER the
    plaintext `key` field present in those routes' own HTTP response.
  - M7: `target_type`/`target_id`/`metadata` mirror the self-service sibling's own shape VERBATIM
    where one exists: `platform.budget.update`→`target_type="budget", target_id="monthly",
    metadata={"budget_usd_monthly": persisted_str}`; `platform.key.create`→`target_type="api_key",
    target_id=str(result.key_id), metadata={"key_name": result.name}`; `platform.key.rotate`→
    `target_type="api_key", target_id=str(result.new_key_id), metadata={"superseded_key_id":
    str(result.superseded_key_id), "key_name": result.name}`; `platform.key.revoke`→
    `target_type="api_key", target_id=str(key_id), metadata={}`; `platform.user.role_assign`→
    `target_type="user", target_id=str(user_id), metadata={"target_user_id": str(user_id),
    "old_role": old_role_str, "new_role": updated.role.value}` (requires ADDING an old-role fetch
    to `assign_platform_tenant_user_role`, mirroring `users_router.py`'s own L131-133 pattern,
    since this handler does not currently fetch it).
  - M8: routes with no self-service audit analog get freshly-designed, minimal, non-secret
    metadata: `platform.cache.update`→`{"enabled": bool, "semantic_enabled": bool}` (post-update
    state); `platform.guardrails.update`→`{"fields_changed": sorted(list[str])}` (top-level keys
    present in the request, never the full config/regex content); `platform.key.patch`→
    `{"fields_changed": sorted(list[str])}`; all `*.view`/`*.list` actions→`{}` (nothing sensitive
    to add beyond target_type/target_id, already sufficient).
  - M9: no route in this task audits a 401/403/404/422 rejection — every one of the 15 calls
    fires on the success path only (see Framings weighed + Scenarios for the explicit rationale).
  - M10: every one of the 15 handlers gains exactly one new `request: fastapi.Request` parameter
    (to reach `request.app.state.sessionmaker`, the ONLY established convention in this codebase
    for this — §0) and exactly one new call to `emit_platform_audit(...)` — no other line of
    existing handler logic changes, EXCEPT: (a) `list_platform_tenants`'s discarded `_: Identity`
    parameter is renamed to a used `identity` binding; (b) `assign_platform_tenant_user_role` gains
    one new read (`old_user = await repo.get_by_id_and_tenant(...)`) BEFORE the use-case call, to
    populate `old_role` in its audit metadata, mirroring self-service exactly.
  - M11: zero lines of `record_audit`, `AuditEvent`, `AuditRepository`, `AuditEventRow`, any audit
    migration, or `usage/api/router.py:get_audit` change — this task is a pure consumer, adding
    ONE new file and instrumenting 4 existing files additively.
  - M12: an audit-write failure (the `session_factory` raising) NEVER blocks, delays, or changes
    the HTTP outcome of any of the 15 routes — inherited verbatim from `record_audit`'s own FROZEN
    fail-open contract; this task adds no new retry/timeout/circuit-breaker for the audit write
    itself (matches the repo-wide precedent: fail-open here is the deliberate, already-adjudicated
    choice, not a gap — per `superadmin-audit-foundation`'s own IO note).
</must>
Reject:
<reject>
  - (unchanged — every one of the 15 routes' EXISTING rejection behavior is byte-identical; this
    task adds a side effect only on the success path) missing/invalid Bearer token ->
    "auth_token_missing"/"auth_token_invalid" (401, unchanged mechanism, R1)
  - (unchanged) valid token, non-SUPERADMIN role -> "auth_forbidden" (403, via require_superadmin,
    unchanged, NO audit row emitted — R2, this task's own explicit scope decision)
  - (unchanged) target tenant_id does not exist -> "tenant_not_found" (404, unchanged, NO audit
    row emitted — R3, this task's own explicit scope decision)
  - (unchanged) any existing 422 payload-validation rejection (guardrail mode/pattern, budget
    decimal/negative, key field validators, unparseable/superadmin role literal) -> unchanged
    codes, NO audit row emitted — R4, matches R2/R3's own rationale
  - (unchanged) a malformed (non-UUID) tenant_id/key_id/user_id path segment -> FastAPI's automatic
    422, unchanged, NO audit row (never reaches a handler body at all) — R5
  - the audit write itself fails for any reason -> NOT surfaced to the caller in any form,
    swallowed by record_audit's existing fail-open contract, logged as a warning only — R6
    (unchanged from every existing call site's own behavior)
</reject>
After:
<after>
  - every one of the 14 target-tenant-scoped reads/writes, on its success path, produces exactly
    one `audit_events` row with `tenant_id` = the TARGET tenant, `actor_user_id`/`actor_email` =
    the REAL superadmin who acted, `action` = a `platform.<resource>.<verb>` string, and
    route-appropriate `target_type`/`target_id`/`metadata` (never secret material).
  - the bulk tenant-directory list ALSO produces exactly one system-level (`tenant_id=None`)
    `audit_events` row per call, attributing the same real superadmin actor.
  - the previously-flagged regression (cross-tenant `PUT .../budget` having LESS audit trail than
    self-service `PUT /admin/budget`) is closed — both now call `record_audit` for the identical
    kind of change, though under different action-name strings (`budget.update` vs.
    `platform.budget.update`) by design (see Framings weighed).
  - a target tenant's own owner/admin/operator can now see EVERY cross-tenant action a superadmin
    took on their tenant, through the EXISTING, unmodified `GET /admin/audit` endpoint — zero new
    UI/API surface needed for this visibility.
  - no 401/403/404/422 rejection on any of these 15 routes produces an audit row — unchanged from
    every existing call site's own success-only convention (R2-R5).
  - non-superadmin callers' behavior on every one of these 15 routes, and on every OTHER existing
    route in the system, is completely unchanged — every change this task makes is additive
    (new parameter + new call) on routes already gated superadmin-only; no non-superadmin request
    reaches any of this task's changed code at all.
  - an audit-subsystem failure never changes any of these 15 routes' HTTP outcome.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Auditing all 6-7 READS (not just writes) is a genuine, precedent-free expansion of
    `audit_events`'s write volume/pattern — every one of the 14 existing call sites is write-only;
    zero data exists today on what read-audit volume looks like under real dashboard usage (e.g. a
    tenant-detail UI polling GET .../budget repeatedly). Lowest confidence because MILESTONE.md's
    Exit criterion is followed LITERALLY here rather than empirically validated against real
    traffic patterns; if wrong (volume becomes a real cost/noise concern once
    `admin-console-ui`, task 5, ships a live dashboard): a follow-up task can cheaply downgrade
    specific READ actions to unaudited without touching this contract's WRITE coverage or its
    shared helper's shape — a narrowing, not a redesign.
  ⚠ Not auditing 403/404 rejections (M9) trades a real, plausible security signal (a compromised or
    probing non-superadmin repeatedly hitting these 15 routes) for architectural cleanliness (not
    touching the shared, FROZEN `require_superadmin` predicate). Lowest confidence tied with the
    above because the "Watch: 403 rate" metric named in all 3 siblings' own §7 OBSERVE sections is
    an HTTP-metrics-layer mitigation, not an `audit_events`-layer one — a security reviewer
    querying `audit_events` alone would see zero evidence of attempted (as opposed to successful)
    cross-tenant access. If wrong: a follow-up task could extend `require_superadmin` itself (or
    wrap it) to emit a `result="denied"` system-level event on rejection — a bounded, additive
    change to a currently-frozen predicate, not a rework of this contract.
  - [ ] the NEW `platform.*` action-name prefix (vs. reusing self-service's exact action strings)
    is the right call — confirm or deny: it optimizes for "filter all cross-tenant admin-console
    activity by action-name prefix alone" over "one tenant's audit view shows self-service and
    cross-tenant causes under the identical action string." If wrong: a follow-up delta could add
    a `metadata["via"]="platform_admin_console"` marker to the reused-action-string variant instead
    — a metadata-only change, not a contract-shape change, IF Tin prefers action-string reuse.
  - [x] including the bulk list as a 15th (system-level) audited call site, not just the literal
    14 — low residual risk: it reuses the exact same shared helper and `AuditEvent`'s already-
    shipped `tenant_id=None` path with zero new capability.
  - [x] `tenant_id` = target tenant (not the superadmin's own) — confirmed via `get_audit`'s own
    tenant-scoping logic (§0), a structural fact, not a preference.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Cross-tenant budget PUT is now audited — closing the flagged regression   # M1, M2, M5, M7
  Given a customer tenant T_other, and a SUPERADMIN identity whose own tenant is the platform tenant
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T_other}/budget with {budget_usd_monthly: "250.00"}
  Then the response is 200, byte-identical to before this task
  And exactly one audit_events row exists with action="platform.budget.update", tenant_id=T_other,
    actor_user_id=<the superadmin's user id>, actor_email=<the superadmin's email>,
    target_type="budget", target_id="monthly", metadata={"budget_usd_monthly": "250.00"}
  And T_other's own owner, calling their existing GET /admin/audit, sees this row today — zero new
    endpoint required (closes the regression: self-service PUT /admin/budget has audited this same
    kind of change since before this milestone; the cross-tenant equivalent now does too)

Scenario: A cross-tenant read (GET cache) is now audited — zero self-service precedent, by design   # M2, M4, M8
  Given a customer tenant T_other with cache_enabled=true, and a SUPERADMIN identity
  When the SUPERADMIN calls GET /admin/platform/tenants/{T_other}/cache
  Then the response is 200 {enabled: true, ...}, byte-identical to before this task
  And exactly one audit_events row exists with action="platform.cache.view", tenant_id=T_other,
    actor_user_id=<the superadmin's user id>, target_type="cache", target_id="config", metadata={}

Scenario: The bulk tenant directory list is audited as a system-level event (no single target)   # M3
  Given a SUPERADMIN identity, and 3 customer tenants exist
  When the SUPERADMIN calls GET /admin/platform/tenants
  Then the response is 200 with all matching tenants, byte-identical to before this task
  And exactly one audit_events row exists with action="platform.tenant.list", tenant_id IS NULL,
    actor_user_id=<the superadmin's user id> — a system-level event that still attributes the
    real actor, mirroring ops.platform_credential_resolve's own tenant_id=None precedent

Scenario: Key creation is audited without ever recording the plaintext secret   # M2, M6, M7
  Given a customer tenant T_other, and a SUPERADMIN identity
  When the SUPERADMIN calls POST /admin/platform/tenants/{T_other}/keys with a valid name
  Then the response is 201 with the plaintext key shown once, byte-identical to before this task
  And exactly one audit_events row exists with action="platform.key.create", tenant_id=T_other,
    target_type="api_key", target_id=<the new key_id>, metadata={"key_name": <name>}
  And metadata contains no "key" field and no substring of the plaintext secret anywhere in the row

Scenario: Key rotation is audited without recording either the old or the new plaintext secret   # M2, M6, M7
  Given a customer tenant T_other with an active key K, and a SUPERADMIN identity
  When the SUPERADMIN calls POST /admin/platform/tenants/{T_other}/keys/{K}/rotate
  Then the response is 201 with the NEW plaintext key shown once, byte-identical to before this task
  And exactly one audit_events row exists with action="platform.key.rotate", tenant_id=T_other,
    target_type="api_key", target_id=<the new key_id>,
    metadata={"superseded_key_id": <K>, "key_name": <name>}
  And metadata contains no plaintext key material, old or new

Scenario: A role reassignment is audited with both old and new role, mirroring self-service   # M2, M7, M10
  Given a customer tenant T_other with a MEMBER-role user U, and a SUPERADMIN identity
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T_other}/users/{U}/role {"role": "admin"}
  Then the response is 200 with U's role now "admin", byte-identical to before this task
  And exactly one audit_events row exists with action="platform.user.role_assign", tenant_id=T_other,
    target_type="user", target_id=U, metadata={"target_user_id": U, "old_role": "member",
    "new_role": "admin"} — requires the new old-role fetch this task adds (M10)

Scenario: A guardrails update is audited with the changed field names, never the full config   # M2, M8
  Given a customer tenant T_other with an existing prompt_injection config, and a SUPERADMIN identity
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T_other}/guardrails with a new pii_mask block
  Then the response is 200 with both blocks present, byte-identical to before this task
  And exactly one audit_events row exists with action="platform.guardrails.update", tenant_id=T_other,
    metadata={"fields_changed": ["pii_mask"]}
  And metadata does not contain the full guardrail_configs blob or any custom-pattern regex text

Scenario: A key patch is audited even though self-service PATCH has no audit precedent at all   # M2, M8
  Given a customer tenant T_other with an active key K, and a SUPERADMIN identity
  When the SUPERADMIN calls PATCH /admin/platform/tenants/{T_other}/keys/{K} with a new monthly_budget_usd
  Then the response is 200 with the updated field, byte-identical to before this task
  And exactly one audit_events row exists with action="platform.key.patch", tenant_id=T_other,
    target_type="api_key", target_id=K, metadata={"fields_changed": ["monthly_budget_usd"]} — this
    is NEW coverage, not a regression fix (self-service PATCH /admin/keys/{id} audits nothing either)

Scenario: A REJECTED (403) cross-tenant attempt produces zero audit rows — explicit, named decision   # M9, R2
  Given an OWNER identity (not superadmin) for tenant T_owner, and a different tenant T_other
  When the OWNER calls PUT /admin/platform/tenants/{T_other}/budget with a valid body
  Then the response is 403 ERR_AUTH_FORBIDDEN, byte-identical to before this task
  And zero new audit_events rows exist for this attempt — this task deliberately does not audit
    authorization rejections (§1 Framings weighed + Assumptions: the existing 403-rate metric,
    named in all 3 sibling tasks' own OBSERVE Watch lines, is the earmarked mitigation for this
    signal at the metrics layer, not the audit-log layer; auditing it would require modifying the
    shared, FROZEN require_superadmin predicate, out of this task's declared scope)

Scenario: A REJECTED (404, nonexistent target tenant) attempt produces zero audit rows   # M9, R3
  Given a SUPERADMIN identity and a tenant_id with no matching row
  When the SUPERADMIN calls GET /admin/platform/tenants/{tenant_id}/keys
  Then the response is 404 ERR_TENANT_NOT_FOUND, byte-identical to before this task
  And zero new audit_events rows exist for this attempt

Scenario: A REJECTED (422 payload validation) attempt produces zero audit rows   # M9, R4
  Given a customer tenant T_other and a SUPERADMIN identity
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T_other}/budget with
    {budget_usd_monthly: "-10.00"}
  Then the response is 422 ERR_PAYLOAD_INVALID, byte-identical to before this task
  And zero new audit_events rows exist for this attempt

Scenario: Non-superadmin callers' existing behavior on every other route is completely unchanged   # M10, byte-identical invariant
  Given an OWNER identity for tenant T_owner
  When the OWNER calls the existing self-service GET/PUT /admin/cache, /admin/guardrails,
    /admin/budget, /admin/keys[...], /admin/users[...] exactly as they would have before this task
  Then every response status, body shape, and side effect (including which actions self-service
    already audits) is byte-identical to before this task
  And zero lines of cache_router.py / guardrail_router.py / budgets/api/router.py /
    keys/api/router.py / users_router.py changed — this task touches ONLY the 4 cross-tenant
    router files (additively) and adds ONE new file

Scenario: An audit-write failure never blocks or changes a cross-tenant route's HTTP response   # M12
  Given a customer tenant T_other, a SUPERADMIN identity, and a session_factory that raises on
    every session open (simulating an audit DB outage)
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T_other}/cache with a valid body
  Then the response is 200, byte-identical to the healthy-audit-DB case
  And no exception from the audit write propagates to the caller
  And a warning is logged (record_audit's existing, unchanged fail-open behavior)

Scenario: The audit subsystem's own infrastructure is untouched — pure consumer, zero regressions   # M11
  Given the existing record_audit / AuditEvent / AuditRepository / AuditEventRow /
    usage/api/router.py:get_audit code, all FROZEN @ v1 by their own owning tasks
  When this task's build completes
  Then a diff shows zero changed lines in audit/domain/audit_event.py,
    audit/application/audit_writer.py, audit/infrastructure/audit_repository.py,
    audit/infrastructure/audit_events_orm.py, and usage/api/router.py
  And the only new audit-domain file is gateway/audit/application/platform_audit.py (M1) — this
    task is a pure additive consumer of the existing audit primitive, not a modifier of it
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# HTTP contract: UNCHANGED on all 15 routes — this task adds a side effect only (no new route,
# no changed request/response body, no changed status code — every 2xx/4xx shape below is the
# ALREADY-FROZEN contract of its owning sibling task, cited not redefined):
#   platform-tenant-directory TASK.md §3   -> GET (list), GET /{tenant_id}          (2 routes)
#   cross-tenant-config-budget TASK.md §3  -> GET/PUT x {cache, guardrails, budget} (6 routes)
#   cross-tenant-keys-members TASK.md §3   -> GET/POST/PATCH/POST/DELETE keys,
#                                              GET users, PUT users/{id}/role       (7 routes)
# The only two code-shape changes on any of the 15 handlers are (a) one new `request: Request`
# parameter (FastAPI-injected, invisible to callers — reaches request.app.state.sessionmaker,
# the sole existing convention for this, §0) and (b) one new call below (M10).

# New shared helper — the actual shape this task freezes:
async def emit_platform_audit(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity: Identity,                    # SUPERADMIN caller -> actor_user_id/actor_email (M5)
    target_tenant_id: uuid.UUID | None,     # PATH tenant_id; None ONLY for the bulk list (M3)
    action: str,                           # "platform.<resource>.<verb>" — table below (M4)
    target_type: str,
    target_id: str | None,
    metadata: dict[str, object],            # never secret material (M6)
) -> None
  # Builds ONE AuditEvent(id=uuid.uuid4(), tenant_id=target_tenant_id, actor_user_id=
  # identity.user_id, actor_email=identity.email, action=action, target_type=target_type,
  # target_id=target_id, result="success", metadata=metadata, created_at=datetime.now(UTC)) and
  # schedules asyncio.ensure_future(record_audit(session_factory, event))  # noqa: RUF006
  # INTERNALLY — callers never construct AuditEvent or call record_audit/ensure_future themselves
  # (M1). Lives in gateway/audit/application/platform_audit.py (NEW file).

# 15 call sites (file : handler -> action / target_type / target_id / metadata):

platform_tenants_router.py  (target_tenant_id = None for list; = path tenant_id for get-one)
  list_platform_tenants       -> platform.tenant.list / "tenant" / None            / {}
  get_platform_tenant_by_id   -> platform.tenant.view / "tenant" / str(tenant_id)  / {}

platform_tenant_config_router.py  (target_tenant_id = path tenant_id, all 6)
  get_platform_tenant_cache      -> platform.cache.view      / "cache"      / "config" / {}
  put_platform_tenant_cache      -> platform.cache.update    / "cache"      / "config"
                                     / {"enabled": bool, "semantic_enabled": bool}       # post-update
  get_platform_tenant_guardrails -> platform.guardrails.view   / "guardrails" / "config" / {}
  put_platform_tenant_guardrails -> platform.guardrails.update / "guardrails" / "config"
                                     / {"fields_changed": sorted(list[str])}   # top-level keys only
  get_platform_tenant_budget     -> platform.budget.view     / "budget"     / "monthly" / {}
  put_platform_tenant_budget     -> platform.budget.update   / "budget"     / "monthly"
                                     / {"budget_usd_monthly": persisted_str}   # mirrors budget.update

platform_keys_router.py  (target_tenant_id = path tenant_id, all 5)
  list_platform_tenant_keys   -> platform.key.list   / "api_key" / None               / {}
  create_platform_tenant_key  -> platform.key.create / "api_key" / str(result.key_id)
                                  / {"key_name": result.name}                       # mirrors key.create
  patch_platform_tenant_key   -> platform.key.patch  / "api_key" / str(key_id)
                                  / {"fields_changed": sorted(list[str])}       # NEW — no self-svc analog
  rotate_platform_tenant_key  -> platform.key.rotate / "api_key" / str(result.new_key_id)
                                  / {"superseded_key_id": str(result.superseded_key_id),
                                     "key_name": result.name}                       # mirrors key.rotate
  revoke_platform_tenant_key  -> platform.key.revoke / "api_key" / str(key_id) / {} # mirrors key.revoke

platform_users_router.py  (target_tenant_id = path tenant_id, both)
  list_platform_tenant_users       -> platform.user.list        / "user" / None / {}
  assign_platform_tenant_user_role -> platform.user.role_assign / "user" / str(user_id)
                                       / {"target_user_id": str(user_id), "old_role": old_role_str,
                                          "new_role": updated.role.value}  # mirrors user.role_assign

Schema: audit_events table — INSERT-only, via the unchanged record_audit()/AuditRepository.record()
  path (no new table/column/migration). The 6-7 read routes' OWN response data is unaffected (they
  read cache/guardrails/budget/keys/users state exactly as before); auditing them ADDS one new
  audit_events INSERT as a side effect, it does not change what they read or return.

New symbols (containers not contract-binding — build's discretion, behavior is):
  - gateway/audit/application/platform_audit.py (NEW file) — emit_platform_audit() above; the ONLY
    new production symbol this task adds (M1). Independently unit-testable (AuditEvent shape/
    invariant) without spinning up any of the 4 routers.
  - `request: fastapi.Request` — one new parameter on all 15 handlers across the 4 files named
    above (M10), reaching request.app.state.sessionmaker (the sole existing convention, §0).
  - platform_tenants_router.py:list_platform_tenants — its discarded `_: Identity` parameter (L66)
    renamed to a used `identity` binding (M10a) — required to reach identity.user_id/actor_email.
  - platform_users_router.py:assign_platform_tenant_user_role — one new read, BEFORE the
    AssignUserRoleUseCase call: `old_user = await repo.get_by_id_and_tenant(user_id=user_id,
    tenant_id=tenant_id)` (repo is the existing UserRoleRepository dependency already injected;
    signature/method confirmed verbatim at users_repository.py:37, same call shape as
    users_router.py's own L132 precedent, tenant_id = the PATH value not identity.tenant_id) —
    populates old_role_str = old_user.role.value if old_user else None (M10b, M7).
  - main.py: NOT touched — zero new routers to register; this task instruments 4 EXISTING,
    already-registered router files in place (M11, and this task's own declared scope boundary).
  - NO new ErrorSpec, NO migration, NO change to AuditEvent / record_audit / AuditRepository /
    AuditEventRow / usage/api/router.py:get_audit (M11) — pure additive consumer.
```

Glossary deltas: `Cross-tenant audit event` — an `audit_events` row emitted by
  `emit_platform_audit()` for any of the 15 admin-console-audit call sites; `action` follows the
  `platform.<resource>.<verb>` convention (distinct from self-service's bare `<noun>.<verb>`,
  mirroring the immediately-preceding `ops.`/`auth.` prefix precedent from
  superadmin-audit-foundation); `tenant_id` is always the AFFECTED (target) tenant, never the
  superadmin's own platform tenant, so the affected tenant's existing `GET /admin/audit` surfaces
  it with zero new endpoint; `tenant_id=None` denotes a system-level event with no single target
  (only `platform.tenant.list` today, mirroring `ops.platform_credential_resolve`'s prior art).

Least-sure flag surfaced at freeze:
  ⚠ [spec] This contract deliberately audits ZERO 401/403/404/422 rejections (M9) — every one of
    the 15 calls fires success-path only. A compromised or merely curious non-superadmin token
    repeatedly probing these routes (or a superadmin hitting a stale/bad tenant_id) leaves no
    audit_events trace at all; the only signal is the 403/404 HTTP-metrics rate named in all 3
    sibling tasks' own §7 OBSERVE Watch lines — a metrics-layer mitigation, not an audit-log one.
    Lower confidence than the read-coverage flag below because a security reviewer querying
    audit_events in isolation (the very tool this milestone exists to build) would see zero
    evidence an attack was ATTEMPTED, only that it never succeeded. If wrong: a follow-up task can
    wrap (not modify) require_superadmin to emit a result="denied" system-level event on rejection
    — additive to a currently-frozen predicate, no rework of this contract's 15 success-path calls.
  ⚠ [spec] Auditing all 6-7 READS (M2, alongside the 8-9 writes) is a precedent-free expansion of
    audit_events write volume — every one of the 14 pre-existing call sites in this codebase is
    write-only (§0), and zero production data exists on read-audit volume under real dashboard
    traffic (task 5, admin-console-ui, will be the first thing to generate that traffic). Chosen
    because MILESTONE.md's Exit criterion #4 says "every cross-tenant read/write" literally, and
    platform-tenant-directory's own seeded Spec delta explicitly asks for this retrofit — but a
    literal reading of a criterion is a weaker confidence basis than a measurement against real
    traffic, which does not exist yet. If wrong (volume/cost becomes a real concern once task 5
    ships): downgrade specific READ actions (the *.view/*.list rows in the table above) to
    unaudited without touching any WRITE row or the emit_platform_audit helper's own shape — a
    narrowing, not a redesign.
Status: FROZEN @ v1 — auto-approved under standing AUTO MODE delegation (global CLAUDE.md Rule 2;
  project's declared "parallel + auto" run mode) after Tin did not respond to a direct freeze
  question within 60s. Both flagged Least-sure items resolved as drafted, not changed: (1)
  successes-only auditing — matches all 14 existing precedents, matches MILESTONE.md Exit #4's
  literal text ("read/write" — a rejection is neither), and avoids touching another task's frozen
  `require_superadmin` predicate unprompted; deferred to a Spec delta instead. (2) read+write
  auditing (not writes-only) — MILESTONE.md Exit #4 is explicit and literal, platform-tenant-
  directory's own already-gated task seeded a Spec delta asking for exactly this, and auditing
  read-access to sensitive tenant data (keys/budget/members) is itself a defensible security
  property independent of the exit criterion. Flagged for Tin's review at next check-in — same
  disclosure pattern as this session's two earlier auto-mode freezes.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per §2 scenario, PLUS one test per each of the 15 individual call sites — §2's
14 named scenarios give representative coverage of 8 of the 15 handlers by name; M2's blanket
"every one of the 14" requirement is proven per-handler by the remaining 7 tests below, not left
to inference):
<test_plan>
  - test_bulk_tenant_list_audited_as_system_level_event: arrange 3 customer tenants / act GET
    /admin/platform/tenants as SUPERADMIN / assert 200 + one platform.tenant.list row,
    tenant_id IS NULL, actor_user_id=superadmin · covers: M3
  - test_tenant_view_get_one_is_audited: arrange 1 customer tenant / act GET .../{tenant_id} /
    assert 200 + platform.tenant.view row, tenant_id=target, target_id=str(tenant_id) · covers: M2
  - test_cross_tenant_cache_read_is_audited: arrange T_other cache_enabled=true / act GET
    .../cache / assert 200 + platform.cache.view row, metadata={} · covers: M2, M4, M8
  - test_cross_tenant_cache_update_is_audited_with_post_update_state: arrange T_other both
    false / act PUT .../cache {enabled:true} / assert 200 + platform.cache.update row,
    metadata={enabled:true,semantic_enabled:false} (post-update state) · covers: M2
  - test_cross_tenant_guardrails_read_is_audited: arrange T_other w/ prompt_injection set /
    act GET .../guardrails / assert 200 + platform.guardrails.view row, metadata={} · covers: M2
  - test_guardrails_update_audited_with_changed_field_names: arrange T_other w/
    prompt_injection set / act PUT .../guardrails {pii_mask:{...}} / assert 200 +
    platform.guardrails.update row, metadata={fields_changed:["pii_mask"]}, no
    "prompt_injection" substring anywhere in metadata · covers: M2, M8
  - test_cross_tenant_budget_read_is_audited: arrange T_other budget=500.00 / act GET
    .../budget / assert 200 + platform.budget.view row, metadata={} · covers: M2
  - test_cross_tenant_budget_put_is_audited_closing_regression: arrange T_other / act PUT
    .../budget {"250.00"} / assert 200 + platform.budget.update row, tenant_id=target (not
    superadmin's own), metadata={budget_usd_monthly:"250.00"} · covers: M1, M2, M5, M7
  - test_key_list_is_audited: arrange T_other w/ 1 key / act GET .../keys / assert 200 +
    platform.key.list row, target_id=None, metadata={} · covers: M2
  - test_key_creation_audited_without_plaintext_secret: arrange T_other / act POST .../keys /
    assert 201 + platform.key.create row, metadata={key_name:...}, plaintext key absent from
    metadata (substring check) · covers: M2, M6, M7
  - test_key_patch_audited_despite_no_self_service_precedent: arrange T_other w/ active key K
    / act PATCH .../keys/{K} {monthly_budget_usd} / assert 200 + platform.key.patch row,
    metadata={fields_changed:["monthly_budget_usd"]} · covers: M2, M8
  - test_key_rotation_audited_without_plaintext_secret: arrange T_other w/ active key K / act
    POST .../keys/{K}/rotate / assert 201 + platform.key.rotate row,
    metadata={superseded_key_id:K,key_name:...}, new plaintext absent from metadata ·
    covers: M2, M6, M7
  - test_key_revocation_is_audited: arrange T_other w/ active key K / act DELETE .../keys/{K}
    / assert 204 + platform.key.revoke row, metadata={} · covers: M2
  - test_user_list_is_audited: arrange T_other w/ 1 user / act GET .../users / assert 200 +
    platform.user.list row, target_id=None, metadata={} · covers: M2
  - test_role_reassignment_audited_with_old_and_new_role: arrange T_other w/ MEMBER user U /
    act PUT .../users/{U}/role {admin} / assert 200 + platform.user.role_assign row,
    metadata={target_user_id:U,old_role:"member",new_role:"admin"} (requires the new
    old-role fetch, M10b) · covers: M2, M7, M10
  - test_403_rejection_produces_zero_audit_rows: arrange OWNER (non-superadmin) + T_other /
    act PUT .../budget / assert 403 + zero platform.budget.update rows · covers: M9, R2
    [GREEN-BY-DESIGN: already passes pre-build (zero call sites wired = zero rows regardless
    of reason) — stays green through the build, proving an absence, not a build-time flip]
  - test_404_nonexistent_target_tenant_produces_zero_audit_rows: arrange missing tenant_id /
    act GET .../keys / assert 404 + zero platform.key.list rows · covers: M9, R3
    [GREEN-BY-DESIGN — same reasoning]
  - test_422_payload_validation_produces_zero_audit_rows: arrange T_other / act PUT
    .../budget {"-10.00"} / assert 422 + zero platform.budget.update rows · covers: M9, R4
    [GREEN-BY-DESIGN — same reasoning]
  - test_non_superadmin_existing_self_service_behavior_unchanged: arrange OWNER + own tenant
    / act GET/PUT self-service /admin/{cache,guardrails,budget,keys,users} / assert all 200,
    self-service's OWN budget.update audit still fires unaffected · covers: M10 (byte-identical
    invariant) [GREEN-BY-DESIGN — this task changes nothing on these routes]
  - test_audit_write_failure_never_blocks_cross_tenant_http_response: arrange T_other + a
    sessionmaker wrapper that lets the request's own get_session call through but fails the
    NEXT call (the fire-and-forget audit write's own session_factory() call) / act PUT
    .../cache / assert 200 (unchanged), a warning logged, and call_count>=2 (proves the audit
    call was actually attempted, not vacuously absent) · covers: M12
  - test_emit_platform_audit_writes_target_tenant_event: direct unit test, no HTTP — call
    emit_platform_audit(...) with a real target_tenant_id / assert one row w/ correct
    tenant_id/actor_user_id/result · covers: M1
  - test_emit_platform_audit_system_level_event_tenant_id_none: direct unit test — call
    emit_platform_audit(..., target_tenant_id=None) / assert row w/ tenant_id IS NULL,
    actor_user_id still populated · covers: M1, M5
  - test_emit_platform_audit_fails_open_on_audit_db_outage: direct unit test — call
    emit_platform_audit(...) with a raising session_factory / assert no exception propagates
    + a warning is logged · covers: M1, M12
</test_plan>

Scenario "the audit subsystem's own infrastructure is untouched — pure consumer, zero
regressions" (M11) is NOT a pytest assertion — confirmed via `git diff` at §6 VERIFY (zero
changed lines in audit/domain/audit_event.py, audit/application/audit_writer.py,
audit/infrastructure/audit_repository.py, audit/infrastructure/audit_events_orm.py,
usage/api/router.py), mirroring cross_tenant_config_budget's own precedent for an identically-
shaped byte-identical-invariant scenario.

Note on scenario count: §2 contains 14 distinct `Scenario:` blocks (not 13 as an earlier
paraphrase in this task's own delegation prompt suggested) — verified by direct count against
the frozen file, not the paraphrase. All 14 are covered above.

Tests live in: `apps/gateway/tests/admin_console_audit/` · MUST run red (missing implementation)
before Build.

RED confirmed (2026-07-03): 19/23 failed, 4/23 passed (green-by-design, see test_plan above) —
run against an isolated DB (`gateway_test_admin_console_audit`) via
`GATEWAY_TEST_DATABASE_URL=postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test_admin_console_audit
uv run pytest tests/admin_console_audit/ --no-cov -q`. Every one of the 19 failures is the
missing-implementation reason, not a fixture/import bug:
  - 3 direct unit tests of emit_platform_audit() fail with `ModuleNotFoundError: No module
    named 'gateway.audit.application.platform_audit'` — the module genuinely does not exist yet.
  - 15 HTTP call-site tests fail on `assert row is not None` (or the metadata/tenant_id
    assertions immediately after) — every route itself already succeeds (200/201/204, main.py
    already registers all 4 routers from the 3 prior DONE sibling tasks) but emits zero
    `platform.*` audit rows, since none of the 4 router files call emit_platform_audit() yet.
  - The fail-open HTTP test fails on `assert call_count["n"] >= 2` (1 actual vs. >=2 expected) —
    proving the audit call was never attempted, the right reason (not the wrong-reason crash a
    first draft of this test hit: unconditionally replacing app.state.sessionmaker also breaks
    the route's OWN `Depends(get_session)`, since both read the same attribute per
    core/db.py:73-75 — fixed by a call-counting wrapper that lets the first call [the request's
    own session] through and only fails subsequent calls [the audit write's own
    session_factory() call], confirmed correct by this RED run's own failure message).
The 4 passes are the GREEN-BY-DESIGN tests named above (CONVENTIONS.md's own folded v6 lesson:
label these explicitly so a pre-build green is never mistaken for a wrong-reason red) — each
proves a negative invariant (zero rows on rejection; self-service unaffected) that is already
true with zero call sites wired and must REMAIN true after the build, not flip red-to-green.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/audit/application/platform_audit.py`, `apps/gateway/src/gateway/tenants/api/platform_tenants_router.py`, `apps/gateway/src/gateway/tenants/api/platform_tenant_config_router.py`, `apps/gateway/src/gateway/keys/api/platform_keys_router.py`, `apps/gateway/src/gateway/tenants/api/platform_users_router.py`, `apps/gateway/tests/admin_console_audit/`, `apps/gateway/tests/cross_tenant_config_budget/test_cross_tenant_config_budget.py`, `apps/gateway/tests/cross_tenant_keys_members/test_cross_tenant_keys_members.py`, `apps/gateway/.coverage`, `apps/gateway/.pytest_cache/`, `apps/gateway/.ruff_cache/`
  (all tokens declared on this line per the engine's scope-parser quirk — continuation lines are
  never read; the new helper file (1) + all 4 already-frozen router files this task instruments
  additively (4) + this task's own test directory (1) + 3 gitignored build-artifact tokens the
  scope-walk does not exclude by default. main.py is deliberately NOT declared — it is not
  touched, M11/§3's own declared boundary. SCOPE EXPANDED mid-build (evidence: `add.py phase
  build` re-run below to re-snapshot) to add TWO sibling-task test files:
  (1) `cross_tenant_config_budget/test_cross_tenant_config_budget.py` — that task's own
  `test_cross_tenant_budget_write_is_unaudited` (§ "M10 — cross-tenant writes are unaudited
  today") hard-pins the PRE-this-task behavior (0 audit rows) as its assertion; that behavior is
  now deliberately changed by THIS task's own frozen §3 CONTRACT/M1/M2/M5/M7 ("closing the
  flagged regression" — §2 Scenario 1). Confirmed via a real `pytest` run: 1 failed/19 passed in
  that suite, the ONE failure being exactly this stale assertion (`count == 0` now `count == 1`)
  — not a new defect.
  (2) `cross_tenant_keys_members/test_cross_tenant_keys_members.py` — that task's own
  `test_no_audit_event_written_by_any_route` docstring literally says "the deliberate,
  documented deferral to admin-console-audit (task 4), not an oversight" — same pattern,
  confirmed via a real `pytest` run: 1 failed/26 passed, the ONE failure being this exact
  blanket assertion (`count == 0` now `count == 7`, one row per each of that suite's own 7
  routes this task instruments).
  Updating these two sibling assertions to match the new, contract-mandated reality is not
  "weakening a test to force a build pass" (that rule protects THIS task's own frozen
  tests/contract) — it is keeping downstream consumers' tests in sync with a behavior change
  each of those consumers' own tasks explicitly flagged as temporary/deferred BY NAME to this
  task.)
Strategy (ordered batches):
  1. `gateway/audit/application/platform_audit.py` (NEW) — emit_platform_audit() first,
     independently unit-testable (per §3's own framing) before touching any router. Reuses
     AuditEvent/record_audit verbatim, zero new dependency beyond what audit_writer.py already
     imports.
  2. `platform_tenants_router.py` — 2 call sites (list, get-one); rename the discarded `_`
     binding to `identity` (M10a); add `request: Request` to both handlers.
  3. `platform_tenant_config_router.py` — 6 call sites (GET/PUT × cache/guardrails/budget);
     each PUT's audit call lands AFTER its own `session.commit()`, using post-write state
     already computed for the response (cache's refreshed values; guardrails' `fields_set`;
     budget's `persisted_str`) — no extra query needed for any of the 3 PUTs' metadata.
  4. `platform_keys_router.py` — 5 call sites (list/create/patch/rotate/revoke); each audit call
     lands after the use-case's own success (post try/except, so a KeyNotFoundError/
     ForbiddenError never reaches the audit call — R2/R4 hold structurally, not just by test).
  5. `platform_users_router.py` — 2 call sites (list, role-assign); role-assign ALSO gains the
     new `old_user = await repo.get_by_id_and_tenant(user_id=user_id, tenant_id=tenant_id)` read
     (M10b) BEFORE the use-case call, using the PATH `tenant_id` parameter already in scope —
     verified myself by reading the function signature (not assumed): `tenant_id` is bound to
     the path segment at the top of `assign_platform_tenant_user_role`, completely distinct from
     `identity.tenant_id` (the superadmin's own reserved platform tenant). The audit call itself
     lands after the EXISTING `await session.commit()` (already added by role-update-
     persistence-fix, L202 pre-build) — never before.
  6. Run `ruff format` + `ruff check` + `uv run pyright` on all 5 touched/new src files. Re-run
     the full RED suite (should now be 23/23 green) + the 3 sibling suites' own regression tests.

Persona (optional): backend-expert stance (FastAPI + audit/observability retrofitting) — no
  dedicated persona file exists for this domain; generic, matching all 3 sibling tasks' own note.
Known-problem fixes:
  - trap: swapping `target_tenant_id` for `identity.tenant_id` anywhere (would silently attribute
    every cross-tenant event to the superadmin's OWN platform tenant instead of the affected
    tenant, breaking M5 and the target tenant's own GET /admin/audit visibility) -> fix: every
    call site's `target_tenant_id=` argument is the function's own `tenant_id` PATH parameter,
    never `identity.tenant_id`; the ONE exception is `list_platform_tenants` (`target_tenant_id=
    None`, M3) — deliberate, not an oversight.
  - trap: emitting the audit call BEFORE a write's own commit/use-case call (would falsely audit
    an action that then fails/rolls back) -> fix: every PUT/POST/PATCH/DELETE's audit call is the
    LAST thing before the handler's `return`, strictly after its own success path.
  - trap: leaking the plaintext key/secret into audit metadata (key.create/key.rotate) -> fix:
    metadata carries only `key_name`/`superseded_key_id` (mirrors self-service verbatim, M7); the
    `result.key`/`result.name`-adjacent plaintext field is never read into metadata.
  - trap: `emit_platform_audit` bare-awaited or blocking the response (would defeat fire-and-
    forget) -> fix: the helper itself wraps `record_audit(...)` in `asyncio.ensure_future(...)`
    internally (M1) — callers `await emit_platform_audit(...)` (an async function per §3's own
    frozen signature), but that await only returns once the task is SCHEDULED, not once the audit
    write itself completes; record_audit's own fail-open contract (already FROZEN, unmodified)
    guarantees the scheduled task never raises into the caller's request.
  - trap: `async def emit_platform_audit` with no internal `await` may trip ruff's RUF029
    ("unused-async") since this project's ruff `select` includes the whole "RUF" category ->
    resolved at build time by running `ruff check` on the new file and reading its actual output
    rather than assuming; documented in "Strategy actually used" below.
Strategy actually used: followed the 6 batches as planned, in order, with two disclosed
  mid-build Scope expansions (not deviations from the plan, additions to it, each executed via
  the sanctioned re-snapshot procedure — Scope-line edit -> `add.py phase build` re-run ->
  verified via state.json -> then the edit): (1) `tests/cross_tenant_config_budget/
  test_cross_tenant_config_budget.py`'s own `test_cross_tenant_budget_write_is_unaudited`
  hard-pinned `count == 0` as a literal, named-deferred-to-this-task fact — renamed to
  `test_cross_tenant_budget_write_is_now_audited`, assertion updated to `count == 1`; (2) the
  analogous `tests/cross_tenant_keys_members/test_cross_tenant_keys_members.py`'s
  `test_no_audit_event_written_by_any_route` -> renamed `test_all_7_routes_now_emit_audit_events`,
  `count == 0` -> `count == 7`. Both are the OTHER tasks' own pre-existing tests, not this task's
  own §4 frozen suite or §3 contract — updating them serves THIS task's own frozen, contract-
  mandated new behavior, which is different from weakening a test to force this task's own build
  green. Batch 5 additionally surfaced (and fixed, in-place, since the file was already touched)
  a factually-wrong docstring comment in `platform_users_router.py` claiming
  `UserRoleRepository.update_role` never commits — verified false by direct read of
  `users_repository.py:50-70` (real `await self._session.commit()` at line 69); the underlying
  harmless redundant second commit was left unchanged (out of this task's audit-only mandate).
  Batch 6's ruff/pyright pass required 51 underscore-prefix renames on unused tuple-unpack
  bindings across the new test file (never touching which assertions run) plus one
  `# type: ignore[arg-type]` on a raising mock `session_factory`, mirroring the established
  precedent in `tests/superadmin_audit_foundation/conftest.py`'s own analogous fixture. RUF029
  ("unused-async") did NOT fire on `emit_platform_audit` despite having no internal `await` —
  confirmed empirically (ruff check clean on first attempt), no preemptive noqa added.
Safety rule (feature-specific): the audit write is DELIBERATELY fire-and-forget/fail-open
  (inherited verbatim from record_audit's own FROZEN contract, M12) — no new timeout/retry/
  circuit-breaker is added for the audit write itself; this is the already-adjudicated,
  documented choice (superadmin-audit-foundation's own IO note), not a gap this task re-opens.
  The one NEW safety-relevant behavior this task adds beyond pure instrumentation is the
  `old_user` read in `assign_platform_tenant_user_role` (M10b) — a plain SELECT, no write, no
  new transactional concern.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 23/23 new (`tests/admin_console_audit/`) + 64/64 regression across 4
      sibling suites (20 `cross_tenant_config_budget` + 27 `cross_tenant_keys_members` + 9
      `platform_tenant_directory` + 8 `test_users_role.py`) = 87/87, ALL re-run directly by me
      this session (not assumed from an earlier run) AND independently re-run by an adversarial
      subagent (separate process, separate reasoning) with matching counts.
- [x] coverage did not decrease — nuanced finding, recorded honestly rather than a bare check:
      the new file `platform_audit.py` measures a clean, trustworthy 100% (11/11 statements).
      For the 4 router files, raw aggregate coverage-percentage tooling is DEMONSTRABLY
      unreliable for this specific async shape in this repo: an isolated single-test run
      (`pytest tests/admin_console_audit/test_admin_console_audit.py::test_key_list_is_audited
      --cov=gateway.keys.api.platform_keys_router`, dumped via `coverage json`) shows lines
      181-182 (`items = await use_case.execute(...)` and the following `emit_platform_audit(`
      call) as NOT executed, despite the test passing — which is logically impossible unless
      those lines ran (the test independently re-queries `audit_events` and asserts the exact
      row). Root cause almost certainly SQLAlchemy async's `greenlet`-based bridge losing
      coverage.py's per-frame trace-hook continuity across the switch during the real DB
      round-trip — a known category of coverage.py+greenlet limitation, reproducible on this
      exact line pattern, NOT specific to code this task added (would affect any handler in this
      codebase with the same session-then-respond shape). Given the tool is untrustworthy here,
      I used the stronger, more direct signal instead: all 15 call sites have a dedicated test
      that performs the real request, independently re-queries the actual `audit_events` row via
      a separate session, and asserts every field — which proves both execution AND correctness,
      a higher bar than a line-coverage percentage. Structurally, this build is pure ADDITION
      (new `request: Request` param + one new `await emit_platform_audit(...)` block per handler,
      plus one new `old_user` read) — no pre-existing line was deleted or made unreachable, so
      there is no plausible mechanism for a real decrease. Filed as a spec delta in §7 below.
- [x] no test or contract was altered during build — with one disclosed nuance: 2 PRE-EXISTING
      sibling-task tests (not this task's own §4 frozen suite, not this task's own §3 contract)
      were updated via a proper Scope-expansion + re-snapshot procedure, because their own
      literal assertions hard-pinned a pre-this-task fact this task's own frozen contract
      deliberately supersedes (both sibling tasks' own docstrings named this exact follow-up
      task as the deferred point). See "Strategy actually used" in §5 for the full account.
- [x] the green was EARNED, not gamed — independent adversarial subagent refute-read: EARNED,
      zero defects across all 15 call sites (see Refute-read verdict below).
- [x] concurrency / timing of the risky operation is safe — the audit write reuses
      `record_audit`'s own previously-frozen fire-and-forget/fail-open contract verbatim
      (`asyncio.ensure_future(...)  # noqa: RUF006`, matching the established codebase-wide
      convention); no new shared mutable state, lock, timeout, or retry surface introduced.
- [x] no exposed secrets, injection openings, or unexpected dependencies — confirmed directly:
      zero plaintext key/secret material in any metadata dict (key.create/key.rotate carry only
      `key_name`/`superseded_key_id`, never `result.key`); zero new third-party dependencies
      (reuses `AuditEvent`/`record_audit`/`asyncio`/`uuid`/`datetime`, all already used
      elsewhere in this codebase).
- [x] layering & dependencies follow CONVENTIONS.md — `emit_platform_audit` lives in
      `audit/application/` (application layer), depends inward only on `audit/domain.AuditEvent`
      and `audit/application/audit_writer.record_audit`; the 4 api-layer routers depend on it
      (api -> application, correct direction); zero framework leakage into domain; zero new
      cross-module coupling beyond the deliberate, contract-mandated one.
- [x] a person reviewed and approved the change — under this project's `autonomy: auto` default,
      Verify auto-resolves on complete evidence; no human has physically clicked approve yet.
      What stands in as the recorded evidence trail: my own direct, firsthand re-verification
      (re-ran all 87 tests, re-grepped the 15 call sites and the zero-`identity.tenant_id`
      finding, re-resolved every §3 symbol) PLUS an independently-spawned adversarial subagent
      review that reached the same conclusions via its own separate reasoning. Tin's own
      spot-audit remains the backstop per the engine's documented auto-mode model, not yet taken.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] every one of the 15 cross-tenant admin routes produces exactly one new `audit_events` row
      per successful call, with `tenant_id` = the PATH-affected tenant (NULL only for the one
      deliberate bulk-list exception), `actor_user_id`/`actor_email` = the real superadmin
      identity, and zero secret material in `metadata` — confirmed by 23 dedicated tests in
      `tests/admin_console_audit/test_admin_console_audit.py`, each independently re-querying
      `audit_events` and asserting every field; independently re-confirmed per-call-site by an
      adversarial subagent reading all 4 router files directly against §3's own table (15/15 PASS).
- [x] the 2 sibling tasks' own suites that previously pinned "audit is deferred/absent" as a
      literal fact now correctly observe the new, contract-mandated audited behavior — confirmed
      by `test_cross_tenant_budget_write_is_now_audited` (count 0->1) and
      `test_all_7_routes_now_emit_audit_events` (count 0->7), both green.
- [x] no pre-existing response shape/behavior of any of the 15 routes changed for the caller —
      confirmed by every pre-existing assertion in the 3 prior sibling suites (64 tests total)
      remaining green with zero modification beyond the 2 named audit-count lines above.
- [x] `main.py` and the pre-existing audit subsystem (`audit_event.py`, `audit_writer.py`,
      `audit_repository.py`, `audit_events_orm.py`) are untouched by this build — confirmed by
      `git diff`/`git status` showing zero change attributable to this task on those 4 core
      files; `main.py`'s own pre-existing diff (router registration) predates this task, traced
      to the 3 prior sibling tasks that first created these routers (confirmed via `git log`
      showing zero commits yet on this feature branch touching any of these files — all 4
      milestone tasks' work, including this one, sits uncommitted by design pending Tin's own
      commit/PR call, not something this task's build introduced or should presume to commit).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `emit_platform_audit` is referenced exactly 15 times across the 4 router
      files (2 + 6 + 5 + 2, confirmed via `grep -cF 'await emit_platform_audit(' ...` this
      session), matching §3's table exactly; the import line is present in all 4 files.
- [x] DEAD-CODE (code) — `emit_platform_audit` is the only new symbol and is referenced 15 times
      (not zero); the new `old_user`/`old_role_str` binding in `assign_platform_tenant_user_role`
      is consumed by `metadata={"old_role": old_role_str, ...}`, not dead.
- [x] SEMANTIC (prose / non-code) — read in full, not skimmed: `platform_users_router.py`'s own
      docstring comment claiming `UserRoleRepository.update_role` "only calls session.flush(),
      never session.commit()" was checked directly against `users_repository.py:50-70` and found
      FALSE (real `await self._session.commit()` at line 69) — corrected in-place; the harmless
      redundant second commit itself was left unchanged (out of this task's audit-only mandate,
      filed as a spec delta in §7).

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — re-read directly this
      session: `emit_platform_audit` (`platform_audit.py:36`, signature byte-matches the frozen
      contract exactly), `AuditEvent` (`audit_event.py:23`), `record_audit` (`audit_writer.py:30`),
      `authorize_tenant_scope` (`authz.py:134`), `require_superadmin` (`authz.py:210`),
      `request.app.state.sessionmaker` (wired `main.py:636`, consistent with 6 other established
      call sites in the same file).
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — none moved
      or renamed; all 6 cited symbols are exactly where §0 GROUND (Ground SHA `ccf411c`) anchored
      them.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: agent (independent adversarial-review subagent, general-purpose/sonnet, spawned this
  session with no access to my own build narrative) + self (direct firsthand re-verification:
  re-ran all 87 tests, re-grepped the 15 call-site count and the zero-`identity.tenant_id`-in-
  executable-code finding, re-resolved every §3 symbol, and separately chased down the coverage-
  tooling anomaly above rather than accepting a misleading number at face value) ·
  adversarially checked: per-call-site correctness against §3's own table for all 15 sites
  individually (target_tenant_id vs identity.tenant_id, audit-strictly-after-success ordering,
  no-secret-in-metadata, exact action/target_type/target_id/metadata shape); test-suite
  vacuousness (every assertion reads back the real DB row — none are HTTP-status-only, none
  hardcode an actor id instead of decoding the token); the 2 sibling-test edits (confirmed
  minimal, honest, not weakening — count-only changes matching the new contract-mandated fact);
  fail-open behavior under a simulated audit-DB outage (dedicated test asserting the call was
  attempted via a call-counting wrapper, not vacuously absent); real pytest execution (not
  assumed) for all 5 relevant suites, independently, by both the subagent and by me.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: self + independent subagent (general-purpose/sonnet)
1. Security: CLEAR — `target_tenant_id` is structurally never `identity.tenant_id` (codebase-
   wide grep across all 5 touched src files: zero executable occurrences, only docstring
   warnings against doing it); no plaintext key/secret material ever reaches any metadata dict
   (key.create/key.rotate independently confirmed); the fire-and-forget audit write cannot
   influence or block the caller's own HTTP response.
2. Concurrency: CLEAR — reuses `record_audit`'s own previously-frozen, previously-verified
   fire-and-forget/fail-open contract verbatim; no new shared mutable state, lock, timeout, or
   retry surface; `asyncio.ensure_future` scheduling matches the established codebase-wide
   convention exactly (same `# noqa: RUF006`, same pattern used elsewhere).
3. Architecture: CLEAR — `emit_platform_audit` lives in `audit/application/`, depends inward
   only on `audit/domain.AuditEvent` and `audit/application/audit_writer.record_audit`; the 4
   api-layer routers depend on it (api -> application, correct direction); zero framework
   leakage into domain; zero new cross-module coupling beyond the deliberate, contract-mandated
   one (replacing what would otherwise be 15 duplicated inline blocks).
Verdict: PASS
Residue: none
Binding: advisory — sensitivity unset for this task (project-level `sensitivity: unset` per
  `add.py status`); Security itself independently CLEAR, so there is no HARD-STOP to escalate
  regardless of binding.

### GATE RECORD
Outcome: PASS
Reviewed by: self (Claude, this session) + independent adversarial subagent review · date: 2026-07-03

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `platform.*` action volume (rate of
  `platform.tenant.*`/`cache.*`/`guardrails.*`/`budget.*`/`key.*`/`user.*` audit events) once
  admin-console-ui (task 5 of this milestone) ships and superadmins start driving this surface
  via a UI instead of raw API calls; the audit fire-and-forget failure rate (should be ~0 — a
  sustained non-zero rate would mean the audit DB is unreachable and the "complete cross-tenant
  audit trail" guarantee is silently violated even though the underlying admin actions keep
  succeeding, since the write is deliberately fail-open).

### Decisions (ADR)
- [self] Chose ONE shared `emit_platform_audit()` helper (`audit/application/platform_audit.py`)
  over duplicating the ~8-line `AuditEvent`+`ensure_future` block at all 15 call sites — matches
  §3's own explicit framing; keeps any future audited-route addition to a 1-line call.
- [self] `target_tenant_id` is always the PATH `tenant_id`, never `identity.tenant_id`, with
  `target_tenant_id=None` as the sole, deliberate exception for the bulk tenant-list (a
  system-level event) — the single highest-consequence design decision in this task,
  independently verified twice (self + adversarial subagent, separately, via codebase-wide grep).
- [self] Every audit call is placed strictly AFTER its route's own success/commit — never
  before — so a failed or rolled-back action is never falsely audited as successful.
- [self, disclosed via a proper Scope-expansion + re-snapshot] Updated 2 PRE-EXISTING sibling-
  task tests whose own literal assertions ("count==0, audit deferred") were invalidated by this
  task's own frozen, contract-mandated behavior change, rather than leaving them red or silently
  editing them outside the declared-Scope mechanism.
- [self] Corrected a factually-wrong pre-existing docstring comment in `platform_users_router.py`
  (falsely claimed `UserRoleRepository.update_role` never commits) after direct verification
  against `users_repository.py`'s real behavior; left the harmless redundant second
  `session.commit()` itself unchanged (out of this task's audit-only mandate).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] This repo's coverage tooling (coverage.py + SQLAlchemy async-via-`greenlet`)
  under-attributes line coverage for source lines immediately following a real DB round-trip
  inside a request handler (evidence: `coverage json` dump after `pytest
  tests/admin_console_audit/test_admin_console_audit.py::test_key_list_is_audited
  --cov=gateway.keys.api.platform_keys_router` shows `executed_lines` excluding lines 181-182
  despite the test passing and independently confirming the resulting `audit_events` row —
  logically impossible unless those lines ran). Recommend future verify passes for async
  DB-touching handlers treat behavioral/DB-readback test assertions as the primary correctness
  signal, not raw line-coverage percentage, for this code shape; or investigate a coverage.py
  concurrency/greenlet compatibility setting at the project-tooling level.
- [SPEC · open] `platform_users_router.py` carried a stale/incorrect docstring comment claiming
  `UserRoleRepository.update_role` "only calls session.flush(), never session.commit()" —
  verified false by direct read of `users_repository.py:50-70` (real commit at line 69);
  comment corrected in-place during this build, but the underlying harmless redundant second
  commit in the router itself was left unchanged (evidence: `users_repository.py:69`) — a small
  future cleanup candidate, out of this task's audit-only mandate.
- [SPEC · seeded] 2 sibling-task tests (`test_cross_tenant_budget_write_is_now_audited`,
  `test_all_7_routes_now_emit_audit_events`) were updated as part of this build via a disclosed
  Scope expansion (evidence: TASK.md §5's own Scope-line prose, 2 expansion iterations, plus the
  git-visible diffs to both test files) — both sibling tasks' own docstrings had explicitly
  named this exact follow-up task as the deferred point, so no further follow-up is needed.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · folded] A legitimate mid-build Scope expansion (to fix a SIBLING task's own now- [folded foundation-version 45]
  invalidated test assertion) is distinguishable from "weakening a test to force my own build to
  pass" by asking whose contract the change serves (evidence: the 2 sibling-test edits here
  served THIS task's OWN frozen §3 contract's mandated new behavior; THIS task's own §4 frozen
  tests and §3 contract were never touched).
- [TDD · folded] Line-coverage percentage is an unreliable correctness signal for async [folded foundation-version 45]
  SQLAlchemy-session handler code in this repo (greenlet-related trace under-attribution); a
  per-call-site behavioral assertion (real request -> independent DB re-query -> exact-field
  assertion) is a strictly stronger green-ness signal than a coverage percentage for this code
  shape (evidence: reproducible `coverage json` dump on an isolated, passing test showing its
  own necessarily-executed lines marked "missing").
- [ADD · folded] An independent adversarial subagent review (separately primed, no access to the [folded foundation-version 45]
  builder's own narrative) is worth spawning even when self-review already feels thorough, for a
  15-call-site, cross-tenant-data-handling change — the highest-consequence bug class here
  (tenant_id attribution swap) is exactly the kind of subtle, easy-to-miss-in-self-review defect
  that benefits from a second, differently-primed reader (evidence: the subagent independently
  re-derived the same "zero `identity.tenant_id` in executable code" finding via its own separate
  codebase-wide search, rather than trusting the builder's narrative — a converging, not merely
  repeated, confirmation).
