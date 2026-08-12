# TASK: Cross-tenant keys + members view/manage

slug: cross-tenant-keys-members · created: 2026-07-03 · stage: production
milestone: platform-admin-console
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - tenants/domain/authz.py:authorize_tenant_scope(identity, target_tenant_id) -> None (L134-156,
    FROZEN @ v1) — 403 unless SUPERADMIN or same-tenant. Confirmed (grep) its ONLY caller today is
    platform_tenants_router.py:107 — this task becomes its second real caller, in parallel with
    the sibling cross-tenant-config-budget.
  - tenants/domain/authz.py:require_superadmin (L210-233, FROZEN @ v1) — role-only FastAPI
    dependency, 403 unless identity.role == Role.SUPERADMIN.
  - tenants/domain/entities.py:Identity(user_id, tenant_id, email, role) (L30-37);
    Role(StrEnum) incl. SUPERADMIN (L7-18, "Never assignable via PUT /admin/users/{id}/role").
  - tenants/api/platform_tenants_router.py (112 lines, whole file read) — THE precedent. Its
    get_platform_tenant_by_id (L93-111) is the target-shaped route pattern this task's 7 routes
    all follow: require_superadmin as the Depends() (L96) -> authorize_tenant_scope(identity,
    tenant_id) as the body's first line (L107) -> DB fetch (L108) -> 404 if None (L109-110). Its
    docstring (L99-106) discloses that authorize_tenant_scope's reject-branch is unreachable here
    since require_superadmin already filters to SUPERADMIN-only — a DISCLOSED, not hidden,
    redundancy; the same tension applies verbatim to every route this task adds (see Issues/Risks).
  - keys/api/router.py (admin_router, prefix /admin/keys): create_key L90-157; playground-token
    L166-234 (session-token minting for the CALLER's OWN tenant — see Issues/Risks, likely
    out-of-scope); list_keys L237-265; patch_key L268-381; rotate_key L384-461; revoke_key
    L464-500. Every handler currently keys its use-case call on `identity.tenant_id`.
  - keys/api/schemas.py: CreateKeyRequest L22-86, PatchKeyRequest L89-160, RotateKeyRequest
    L163-203, CreateKeyResponse L206-221 (plaintext `key` field, shown once), RotateKeyResponse
    L235-246 (plaintext `key` field, shown once), KeyInfoResponse L248-265 (redacted — no
    key_hash/secret field, confirmed by reading every field).
  - keys/api/deps.py: require_owner_or_admin L56-72; get_create_key_use_case L75-78;
    get_list_keys_use_case L81-84; get_revoke_key_use_case L87-90; get_update_key_use_case
    L93-96; get_rotate_key_use_case L99-102 — all take only an AsyncSession, no tenant coupling.
  - keys/application/use_cases.py — ALL take tenant_id as an explicit kwarg:
    CreateKeyUseCase.execute L58-109 (NO role param — router alone gates it);
    ListKeysUseCase.execute L116-117; RevokeKeyUseCase.execute L124-141 (role==MEMBER ->
    ForbiddenError, L137-138); UpdateKeyUseCase.execute L150-188 (same MEMBER guard, L171-172);
    RotateKeyUseCase.execute L198-264 (same MEMBER guard L220-221; fetches the old key via
    `self._repo.get_by_id(old_key_id)` [tenant-UNFILTERED] then explicitly checks
    `old_key.tenant_id != tenant_id` at L225 before proceeding — this is the actual
    cross-tenant guard, not the repository).
  - keys/infrastructure/repository.py (SqlAlchemyApiKeyRepository): create L40-83; list_by_tenant
    L85-109 (tenant-filtered); revoke L111-124 (tenant-filtered, `.where(id=,tenant_id=)`);
    get_by_id L126-197 (**confirmed NO tenant filter** — `.where(ApiKeyRow.id == key_id)` only);
    update L199-258 (tenant-filtered); rotate L260-320 (tenant-filtered internally on the old-key
    fetch, L285-293). Net finding: `get_by_id`'s missing tenant filter is safe in practice ONLY
    because RotateKeyUseCase compensates with its own explicit check (above) — this task's router
    must pass the PATH tenant_id (never identity.tenant_id) into every use_case.execute() call for
    that compensating check to guard the right tenant.
  - keys/domain/entities.py: ApiKeyInfo L44-63, docstring L45 "no hash or secret included"
    (confirmed by field list: key_id, name, prefix, created_at, revoked_at, budgets, expires_at,
    allowlist, rpm/tpm, team_id, cache_enabled — no key_hash). ApiKey L10-40 (has key_hash; never
    used in a response DTO).
  - keys/domain/errors.py: KeyNotFoundError, ForbiddenError, InvalidApiKeyError (whole file read).
  - keys/infrastructure/orm.py:ApiKeyRow.tenant_id (L57-61) — `ForeignKey("tenants.id",
    ondelete="RESTRICT")`, NOT NULL. A create against a nonexistent tenant_id would raise an
    unhandled IntegrityError, not a clean 404, without an explicit pre-check (feeds §1 Must).
  - teams/infrastructure/repository.py:SqlAlchemyTeamRepository.get_team_for_tenant(team_id,
    tenant_id) -> bool (L287-301) — tenant_id explicit, reusable with the PATH tenant_id instead
    of identity.tenant_id for the create/patch `team_id` ownership check.
  - teams/api/deps.py:get_team_repository (L83-86) — session-only, no tenant coupling.
  - tenants/api/users_router.py (whole file, 173 lines): list_users L77-90
    (require_permission(MEMBERS_MANAGE)); assign_user_role L98-172 — role literal parsed L116-119
    (ValueError -> PAYLOAD_INVALID 422); SUPERADMIN explicitly hard-blocked L121-129 with the
    SAME 422 ERR_PAYLOAD_INVALID shape as an unparseable literal, BEFORE the use-case runs (never
    routed through the escalation-guard 403 path) — this exact byte-identical-shape behavior is
    what "survive unchanged through whatever cross-tenant wrapper" (per the shared brief) means
    operationally. DTOs UserResponse/UsersListResponse/AssignRoleRequest L47-58.
  - tenants/application/users_use_cases.py: ListTenantUsersUseCase.execute L37-38;
    AssignUserRoleUseCase.execute L55-93 — SUPERADMIN guard FIRST and unconditional (L71-72, "even
    an OWNER caller... can never mint a superadmin through this path" — defense-in-depth beneath
    the router's own 422 pre-check); self-guard L75-76 (caller_user_id==target_user_id); escalation
    guard L79-82 (_ADMIN_ASSIGNABLE, L26-28, only restricts caller_role==ADMIN — irrelevant to a
    SUPERADMIN caller).
  - tenants/infrastructure/users_repository.py (UserRoleRepository): list_by_tenant L24-35;
    get_by_id_and_tenant L37-48; update_role L50-69 — tenant_id explicit throughout, all
    tenant-filtered (no `get_by_id`-style unfiltered method exists here — members are already
    safe-by-construction against the cross-tenant-guessing shape that keys needed a use-case-level
    compensating check for).
  - tenants/infrastructure/orm.py:UserRow.tenant_id (L76-78) — `ForeignKey("tenants.id",
    ondelete="RESTRICT")`, same FK-crash risk shape as ApiKeyRow (not exercised by THIS task since
    we add no user-create route, but confirms the general pattern).
  - tenants/domain/errors.py (whole file, 31 lines): UserNotFoundError, EscalationForbiddenError.
  - core/error_catalog.py — reuse-only, ZERO new ErrorSpec entries needed: AUTH_TOKEN_MISSING/
    AUTH_TOKEN_INVALID L77-80; AUTH_FORBIDDEN L83; PAYLOAD_INVALID L174; KEY_NOT_FOUND L306;
    TEAM_NOT_FOUND L314; USER_NOT_FOUND L317; TENANT_NOT_FOUND L323 (added by the sibling task
    platform-tenant-directory this same session — reused here for the same "target tenant_id does
    not exist" shape, now on 7 new routes instead of 1).
  - main.py — router registration precedent: keys_admin_router imported L57/registered L980;
    users_router imported L126/registered L975; platform_tenants_router imported L124/registered
    L976 (immediately after users_router). This task's 2 new routers register the same way.
  - No invite/remove-member endpoint exists for TENANT users/members anywhere in the system today
    (confirmed via repo-wide `search_for_pattern` for invite|remove_member|member_invite —
    self-service is view + role-reassign ONLY). The only `remove_member` symbol found belongs to
    the UNRELATED `teams` bounded context (teams/api/router.py:244,
    teams/application/use_cases.py:125, teams/infrastructure/repository.py:269,
    teams/domain/ports.py:65) — team membership (a key's optional team attribution), NOT a
    tenant's user/role roster. Naming these both "member" risks conflation — feeds §1's route-path
    naming decision.
Context (working folder): .add/milestones/platform-admin-console/MILESTONE.md (read in full —
  Scope In/Out, Shared decisions, Exit criteria all bind this task); shared drafting-context file
  from the orchestrating session (grounding cross-checked, not blind-trusted — all file:line
  anchors above were independently re-verified via Read + mcp__serena__search_for_pattern/
  find_symbol, not copied); .add/GLOSSARY.md (read in full — confirmed NO "platform tenant" /
  "superadmin" / "cross-tenant admin surface" terms exist yet; the sibling task
  platform-tenant-directory's OWN frozen §3 already claims "Platform tenant"/"Superadmin"/"Tenant
  directory" as ITS Glossary deltas, pending the milestone's fold — this task should not
  re-declare them). .add/tasks/platform-tenant-directory/TASK.md (whole file, DONE/gate=PASS) —
  the concrete, same-repo, same-milestone example of every phase's actual house style; mirrored
  throughout this task's own drafting.
Honors: authorize_tenant_scope's + require_superadmin's frozen (@v1) contracts — call them, never
  reimplement; ROLE_PERMISSIONS "allowlist" semantics (a Permission says nothing about WHICH
  tenant); every repository's existing tenant_id-explicit-argument shape (keys AND users) — this
  task's whole "reuse-over-invent" case rests on that shape already being true end-to-end, verified
  above; the milestone's Shared decision that "a cross-tenant endpoint parametrizes the existing
  tenant-scoped use-case/DTO by target tenant_id" — read literally: DTOs are reused too, not just
  use-cases (feeds §3's "zero new schemas" contract shape).
Anchors the contract cites: authorize_tenant_scope; require_superadmin; CreateKeyUseCase/
  ListKeysUseCase/UpdateKeyUseCase/RotateKeyUseCase/RevokeKeyUseCase (unchanged); CreateKeyRequest/
  PatchKeyRequest/RotateKeyRequest/CreateKeyResponse/RotateKeyResponse/KeyInfoResponse (unchanged);
  ListTenantUsersUseCase/AssignUserRoleUseCase (unchanged); UserResponse/UsersListResponse/
  AssignRoleRequest (unchanged); get_team_for_tenant; get_tenant_by_id (sibling's lookup, reused
  for the tenant-existence pre-check); TENANT_NOT_FOUND/KEY_NOT_FOUND/USER_NOT_FOUND/
  TEAM_NOT_FOUND/PAYLOAD_INVALID/AUTH_FORBIDDEN (all pre-existing).
Issues/Risks (→ feed §1):
  ⚠ playground-token (keys/api/router.py L166-234) mints a short-lived, budget-capped key for the
    CALLER's OWN tenant, for the dashboard BFF's own server-side /v1 calls — it is a session/auth
    mechanic, not a key-management action, and minting one FOR a target tenant would mean the
    superadmin's console session can make LLM calls spending THAT tenant's budget/identity — which
    is textually what MILESTONE.md's own Scope-Out reserves for tenant-impersonation (milestone 3):
    "acting AS a tenant user / impersonation sessions". §1 must decide this explicitly, not by
    omission — leaning firmly OUT of scope, cited against that Scope-Out line, not just "probably".
  ⚠ cross-tenant create/rotate: reusing CreateKeyUseCase/RotateKeyUseCase verbatim means the
    plaintext secret naturally flows back to whichever caller invoked execute() — i.e., the
    superadmin sees it once, exactly like self-service. The alternative (suppress/redact it on the
    cross-tenant path) would mint a key whose plaintext NO ONE has ever seen — since the secret is
    SHA-256-hashed at rest with no re-derivation path, that key would be permanently unusable by
    the target tenant. §1 must resolve this as a correctness question, not only a security-posture
    question — "never render raw secret material" (Shared decision) most naturally governs the
    steady-state list/view surface, not the one-time mint-reveal moment self-service already has.
  - `get_by_id`'s missing tenant filter (keys/infrastructure/repository.py:126) is a repository-
    layer gap that RotateKeyUseCase already compensates for at the use-case layer (see Touches) —
    this task adds NO new code to close that gap; it only must not accidentally bypass the existing
    compensation by passing the wrong tenant_id (identity.tenant_id instead of the path value).
    Worth a dedicated scenario proving this holds through the NEW router, not just the old one.
  - route-path naming: MILESTONE.md's prose says "members" throughout (title, Scope-In, Exit
    criteria), but the underlying code/GLOSSARY term is "User", and "member" is ALREADY a live,
    unrelated term in the teams/ bounded context (Touches, above). §1 must pick one path segment
    explicitly and justify it against docs/05's "names drawn from the project glossary" rule.
  - uniform tenant-existence pre-check: CREATE strictly needs one (FK-crash risk, Touches above);
    the 4 target-a-specific-key/user routes get an equivalent 404 for free from
    KeyNotFoundError/UserNotFoundError even without one; the 2 LIST routes are a pure
    consistency/UX choice (an empty list is an equally safe, non-leaking alternative). §1 should
    decide uniform-vs-minimal explicitly — feeds a Least-sure flag either way.
Related intent: .add/milestones/platform-admin-console/MILESTONE.md Scope-In ("keys
  (redacted/metadata-only...) and members... managing... on its behalf") + Exit criterion #3 ("view
  and manage any tenant's keys (redacted) and members") + Shared decisions (reuse-over-invent,
  redaction, audit-deferred-to-task-4) + Scope-Out ("acting AS a tenant user" reserved for
  tenant-impersonation — directly resolves the playground-token tension above); GLOSSARY.md's
  existing `User`/`API key`/`Tenant` terms (this task re-reaches them, introduces none new);
  platform-identity's superadmin-assignment-hard-block precedent (users_router.py L121-129) that
  this task's §2 must prove survives unchanged.
Ground SHA: ccf411c (branch feat/platform-admin-console) — working tree has pre-existing
  uncommitted changes from the sibling task platform-tenant-directory (6 files under apps/gateway/
  src + a new test dir, matching its DONE/gate=PASS/uncommitted status per session record); none of
  them are this task's; this task touches only TASK.md this round (no src/ or tests/ writes).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Cross-tenant keys + members view/manage (superadmin)
Framings weighed: router-layer-only reuse (chosen) — new nested routes under
  `/admin/platform/tenants/{tenant_id}/...` call the EXISTING CreateKeyUseCase/ListKeysUseCase/
  UpdateKeyUseCase/RotateKeyUseCase/RevokeKeyUseCase/ListTenantUsersUseCase/AssignUserRoleUseCase
  verbatim, parametrized by the PATH tenant_id, reusing their existing Pydantic DTOs unchanged too
  (the Shared decision read literally: DTOs, not only use-cases) · vs a parallel cross-tenant
  use-case/DTO layer (rejected — nothing new to encapsulate; every method already takes tenant_id
  explicitly, ground-confirmed). Dual gate on EVERY route (chosen) — require_superadmin (Depends)
  + authorize_tenant_scope(identity, tenant_id) inline, mirroring platform_tenants_router.py's
  get-one shape, because every route here has a natural single target (unlike the sibling's
  target-less bulk list) · vs require_superadmin alone (rejected — misses "no endpoint hand-rolls
  its own superadmin bypass check" and the predicate the milestone names this task as wiring).
  Route path segment `/users` (chosen — matches GLOSSARY.md's `User` term + users_router.py's own
  naming) · vs `/members` (rejected despite matching MILESTONE.md's prose — collides with the
  UNRELATED teams/ bounded context's own "member" vocabulary; docs/05: "names drawn from the
  project glossary"). Two new router files, `keys/api/platform_keys_router.py` +
  `tenants/api/platform_users_router.py` (chosen — mirrors self-service's own one-router-per-
  resource split, and platform_tenants_router.py's own placement inside its resource's existing
  api/ package) · vs one combined file (rejected — breaks the established convention for no
  benefit). playground-token EXCLUDED entirely (chosen — minting one FOR a target tenant is
  functionally "acting as" that tenant to spend its budget/identity through the console, which
  MILESTONE.md's own Scope-Out reserves for tenant-impersonation, milestone 3) · vs an 8th
  cross-tenant route (rejected). Cross-tenant create/rotate DOES reveal the plaintext key to the
  superadmin once, identical to self-service's reveal-once UX (chosen — suppressing it would
  silently mint a permanently unusable key, since the secret is one-way-hashed with no
  re-derivation path: a correctness bug, not a safer default; "never render raw secret material"
  is read as governing the steady-state list/view surface, which stays fully redacted either way)
  · vs redacting create/rotate cross-tenant (rejected). SUPERADMIN-role-assignment hard-rejected
  via a router-layer pre-check replicated verbatim from users_router.py (chosen — byte-identical
  422 ERR_PAYLOAD_INVALID shape self-service vs cross-tenant for the textually identical
  rejection) · vs letting AssignUserRoleUseCase's own internal guard fire (rejected — would
  surface as a DIFFERENT shape, 403 ERR_AUTH_FORBIDDEN, for the same input — an avoidable
  cross-surface inconsistency). Uniform target-tenant-existence pre-check (get_tenant_by_id -> 404
  TENANT_NOT_FOUND) on all 7 routes, before any keys/users query (chosen — CREATE strictly needs
  one given api_keys.tenant_id's real FK constraint, ground-confirmed, and uniformity is one
  predictable rule instead of seven route-specific ones) · vs checking only where structurally
  required — CREATE — and letting the other 6 routes fall through to their natural
  KeyNotFoundError/UserNotFoundError/empty-list behavior (a real, cheaper alternative — NOT fully
  settled, see the ⚠ Assumption below).
Must:
<must>
  - M1: GET /admin/platform/tenants/{tenant_id}/keys returns the target tenant's COMPLETE key list
    (active + revoked, unfiltered — matches self-service), each entry in the SAME redacted
    KeyInfoResponse shape (no hash/secret) — SUPERADMIN callers only.
  - M2: POST /admin/platform/tenants/{tenant_id}/keys creates a key FOR the target tenant via
    CreateKeyUseCase verbatim (tenant_id = path value); a body `team_id` must belong to the
    TARGET tenant (get_team_for_tenant(team_id, path tenant_id)); returns CreateKeyResponse
    including the plaintext `key` EXACTLY ONCE.
  - M3: PATCH /admin/platform/tenants/{tenant_id}/keys/{key_id} updates governance fields on an
    ACTIVE key owned by the target tenant via UpdateKeyUseCase verbatim (tenant_id = path value,
    role = identity.role); a key_id belonging to a different tenant (or revoked, or unknown) 404s
    identically — no distinguishing detail.
  - M4: POST /admin/platform/tenants/{tenant_id}/keys/{key_id}/rotate atomically rotates a key
    owned by the target tenant via RotateKeyUseCase verbatim (tenant_id = path value); returns the
    NEW plaintext key EXACTLY ONCE; a key_id belonging to a different tenant 404s identically —
    enforced by RotateKeyUseCase's own `old_key.tenant_id != tenant_id` check, fed the PATH
    tenant_id (ground-confirmed at use_cases.py:225).
  - M5: DELETE /admin/platform/tenants/{tenant_id}/keys/{key_id} soft-revokes a key owned by the
    target tenant via RevokeKeyUseCase verbatim (tenant_id = path value); cross-tenant key_id
    404s identically.
  - M6: GET /admin/platform/tenants/{tenant_id}/users returns the target tenant's complete
    user/member roster ({id, email, role} each) via ListTenantUsersUseCase verbatim.
  - M7: PUT /admin/platform/tenants/{tenant_id}/users/{user_id}/role assigns a new role to a user
    owned by the target tenant via AssignUserRoleUseCase verbatim (caller_user_id/caller_role =
    identity.user_id/identity.role, tenant_id = path value); a user_id belonging to a different
    tenant 404s identically.
  - M8: role == "superadmin" (or any literal that fails to parse as a Role) on M7 is hard-rejected
    with 422 ERR_PAYLOAD_INVALID BEFORE AssignUserRoleUseCase is invoked — byte-identical to
    self-service's own pre-check (users_router.py L121-129); superadmin is never assignable via
    this path either.
  - M9: every one of the 7 routes above first calls authorize_tenant_scope(identity, tenant_id)
    and second validates the target tenant_id resolves to a real tenant (get_tenant_by_id), BEFORE
    touching any keys/users state — mirrors platform_tenants_router.py's get-one order exactly
    (authorize_tenant_scope L107 before the DB fetch L108; cheap in-memory check before I/O).
  - M10: no route in this task ever includes key_hash, a raw secret, or any field beyond each
    reused DTO's existing shape — redaction is inherited from the verbatim-reused schemas, not
    reimplemented.
  - M11: no route in this task writes an audit event — deferred entirely to admin-console-audit
    (task 4, depends-on this task).
  - M12: no invite/remove-member route is added — this task's member surface is EXACTLY
    {list, reassign role}, mirroring that self-service itself offers nothing more.
  - M13: no cross-tenant playground-token route is added (out of scope — belongs to
    tenant-impersonation, milestone 3).
</must>
Reject:
<reject>
  - missing/malformed Bearer token -> "auth_token_missing" / "auth_token_invalid" (401, R1)
  - valid token, non-SUPERADMIN role -> "auth_forbidden" (403, R2 — fires from require_superadmin
    before the handler body runs)
  - target tenant_id does not resolve to a real tenant -> "tenant_not_found" (404, R3 — M9)
  - key_id unknown, already revoked, or belongs to a tenant other than the path tenant_id ->
    "key_not_found" (404, R4 — identical for all three causes, no leak; M3/M4/M5)
  - user_id unknown, or belongs to a tenant other than the path tenant_id -> "user_not_found"
    (404, R5 — identical for both causes, no leak; M7)
  - role field unparseable OR literally "superadmin" -> "payload_invalid" (422, R6 — M8)
  - create/patch/rotate body fails an existing field validator (negative budget, soft>hard, bad
    allowlist element, non-positive rpm/tpm, malformed expires_at) -> "payload_invalid" (422, R7 —
    unchanged, inherited from the verbatim-reused request schemas)
  - team_id in create/patch body does not belong to the TARGET tenant -> "team_not_found" (404,
    R8 — M2)
</reject>
After:
<after>
  - a SUPERADMIN can view (redacted), create, patch, rotate, and revoke ANY target tenant's API
    keys, and view + reassign the role of any of that tenant's members — independent of the
    superadmin's own tenant_id.
  - every write reuses the IDENTICAL use-case/repository call the self-service surface already
    uses, parametrized by the PATH tenant_id — no parallel business logic exists in this task.
  - a non-SUPERADMIN caller's behavior on every PRE-EXISTING route (/admin/keys, /admin/users) is
    completely unchanged — this task only adds 7 new routes under a new path tree.
  - the superadmin-cannot-be-assigned-via-this-path invariant survives unchanged through the
    cross-tenant path (M8).
  - these 7 new endpoints emit NO audit row yet (M11 — deferred, see ⚠ Assumption below).
  - no invite/remove-member or cross-tenant playground-token capability is added — this task stays
    byte-for-byte at self-service's existing capability ceiling, just made cross-tenant-reachable.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ audit deferral is riskier here than for platform-tenant-directory's own approved precedent —
    lowest confidence because that precedent covered READS only, while this task adds 5 WRITE
    routes, two of which (create, rotate) place a target tenant's LIVE PLAINTEXT key secret into a
    superadmin's HTTP response with zero record of who did it or when, until admin-console-audit
    (task 4) lands; if wrong (Tin judges this risk-shape warrants inline audit NOW): this task's
    contract needs an audit clause added before build — a small addition (one fire-and-forget
    record_audit call per write route, mirroring users_router.py's existing pattern) but it
    changes the build scope agreed at this freeze.
  ⚠ uniform tenant-existence pre-check on all 7 routes (not just CREATE, where it's structurally
    required by the FK constraint) — lowest confidence on the 2 LIST routes specifically, where an
    empty list would be an equally safe, non-leaking alternative to a 404; chosen for one
    predictable rule over seven route-specific ones; if wrong (Tin prefers minimal-check): drop
    the pre-check from the 2 list routes only — CREATE keeps it (mandatory), and the 4
    target-a-specific-resource routes keep their 404 for free either way (KeyNotFoundError/
    UserNotFoundError), so the blast radius of reversing this is exactly 2 routes' behavior.
  - [ ] route path segment `/users` not `/members` — matches GLOSSARY + existing code, but
    MILESTONE.md's own prose says "members" throughout; confirm Tin is fine with the URL not
    literally matching the milestone doc's word choice (cosmetic — renaming later is a
    non-breaking path change, not a data-shape change).
  - [x] playground-token stays out of scope — confirmed via MILESTONE.md's own Scope-Out line
    ("acting AS a tenant user / impersonation sessions -> tenant-impersonation, milestone 3").
  - [x] cross-tenant create/rotate reveals the plaintext once — confirmed via the correctness
    argument (suppressing it would mint an unusable key; ApiKey.key_hash has no re-derivation path).
  - [x] no invite/remove-member added — confirmed via repo-wide search: the only `remove_member`
    symbol belongs to the unrelated teams/ bounded context, not tenant users.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Superadmin lists a target tenant's keys, redacted   # M1, M10
  Given a SUPERADMIN identity whose own tenant_id is T_super, and a customer tenant T_other with
    2 active keys and 1 revoked key
  When the SUPERADMIN calls GET /admin/platform/tenants/{T_other}/keys
  Then the response contains exactly 3 entries (active + revoked, unfiltered)
  And every entry is the KeyInfoResponse shape only — no key_hash, no plaintext secret field

Scenario: Listing keys for a target tenant with none returns an empty list   # M1 (boundary)
  Given a customer tenant T_other with zero keys
  When a SUPERADMIN calls GET /admin/platform/tenants/{T_other}/keys
  Then the response is 200 with an empty array, not an error

Scenario: Superadmin creates a key for a target tenant, seeing the plaintext once   # M2
  Given a SUPERADMIN identity and a customer tenant T_other
  When the SUPERADMIN calls POST /admin/platform/tenants/{T_other}/keys with a valid name
  Then the response is 201 with a plaintext `key` field and the new key's tenant_id is T_other
  And a subsequent GET .../keys for T_other includes the new key in its redacted form only

Scenario: Create rejects a team_id that does not belong to the target tenant   # R8
  Given a customer tenant T_other, and a team_id that belongs to a DIFFERENT tenant T_third
  When a SUPERADMIN calls POST /admin/platform/tenants/{T_other}/keys with that team_id
  Then the response is 404 ERR_TEAM_NOT_FOUND
  And no key row is created for T_other

Scenario: Superadmin patches governance fields on a target tenant's active key   # M3
  Given a customer tenant T_other with an active key K
  When the SUPERADMIN calls PATCH /admin/platform/tenants/{T_other}/keys/{K} with a new
    monthly_budget_usd
  Then the response is 200 with the updated monthly_budget_usd
  And the key's tenant_id is still T_other

Scenario: Patch rejects a key_id belonging to a different tenant than the path   # M3, R4
  Given a customer tenant T_other and a DIFFERENT customer tenant T_third with an active key K3
  When the SUPERADMIN calls PATCH /admin/platform/tenants/{T_other}/keys/{K3}
  Then the response is 404 ERR_KEY_NOT_FOUND
  And K3's fields under T_third are completely unchanged

Scenario: Superadmin rotates a target tenant's key atomically, seeing the new plaintext once   # M4
  Given a customer tenant T_other with an active key K
  When the SUPERADMIN calls POST /admin/platform/tenants/{T_other}/keys/{K}/rotate
  Then the response is 201 with a NEW plaintext `key` and a new key_id
  And the old key K is now revoked, and the new key's tenant_id is T_other

Scenario: Rotate rejects a key_id belonging to a different tenant than the path   # M4, R4
  Given a customer tenant T_other and a DIFFERENT customer tenant T_third with an active key K3
  When the SUPERADMIN calls POST /admin/platform/tenants/{T_other}/keys/{K3}/rotate
  Then the response is 404 ERR_KEY_NOT_FOUND
  And K3 under T_third is NOT revoked and NO new key is created — proves RotateKeyUseCase's own
    `old_key.tenant_id != tenant_id` check is fed the PATH tenant_id, not identity.tenant_id

Scenario: Superadmin revokes a target tenant's key   # M5
  Given a customer tenant T_other with an active key K
  When the SUPERADMIN calls DELETE /admin/platform/tenants/{T_other}/keys/{K}
  Then the response is 204 and K's revoked_at is now set

Scenario: Revoke rejects a key_id belonging to a different tenant than the path   # M5, R4
  Given a customer tenant T_other and a DIFFERENT customer tenant T_third with an active key K3
  When the SUPERADMIN calls DELETE /admin/platform/tenants/{T_other}/keys/{K3}
  Then the response is 404 ERR_KEY_NOT_FOUND
  And K3 under T_third remains active (revoked_at still null)

Scenario: A non-superadmin is rejected on every keys route regardless of their own permissions   # R2
  Given an OWNER identity (holds every Permission) for tenant T_owner, and a target tenant T_other
  When the OWNER calls GET/POST/PATCH/rotate/DELETE on /admin/platform/tenants/{T_other}/keys[...]
  Then every response is 403 ERR_AUTH_FORBIDDEN
  And T_owner's own tenant-scoped /admin/keys surface remains fully functional and unaffected

Scenario: Listing keys against a target tenant that does not exist   # R3
  Given a SUPERADMIN identity and a tenant_id with no matching row
  When the SUPERADMIN calls GET /admin/platform/tenants/{tenant_id}/keys
  Then the response is 404 ERR_TENANT_NOT_FOUND
  And no keys query is attempted (no partial/placeholder list returned)

Scenario: Creating a key against a target tenant that does not exist never crashes with a raw DB error   # R3
  Given a SUPERADMIN identity and a tenant_id with no matching row
  When the SUPERADMIN calls POST /admin/platform/tenants/{tenant_id}/keys with a valid body
  Then the response is a clean 404 ERR_TENANT_NOT_FOUND
  And NOT an unhandled 500 / IntegrityError (api_keys.tenant_id's FK constraint would otherwise
    fire on an unguarded insert) — no orphaned api_keys row is created

Scenario: Redacted key list never includes key_hash or any secret material   # M10
  Given a customer tenant T_other with one active key
  When a SUPERADMIN calls GET /admin/platform/tenants/{T_other}/keys
  Then each entry's field set is exactly {key_id, name, prefix, created_at, revoked_at,
    monthly_budget_usd, soft_budget_usd, expires_at, model_allowlist, rpm_limit, tpm_limit,
    team_id, cache_enabled}
  And key_hash and any plaintext/secret field are absent

Scenario: Superadmin lists a target tenant's members   # M6
  Given a customer tenant T_other with 3 users of mixed roles
  When the SUPERADMIN calls GET /admin/platform/tenants/{T_other}/users
  Then the response contains exactly those 3 users as {id, email, role}

Scenario: Listing members for a target tenant with none returns an empty roster   # M6 (boundary)
  Given a customer tenant T_other with zero users (edge case; normally every tenant has >=1)
  When a SUPERADMIN calls GET /admin/platform/tenants/{T_other}/users
  Then the response is 200 with an empty users array, not an error

Scenario: Superadmin reassigns a target tenant member's role   # M7
  Given a customer tenant T_other with a MEMBER-role user U
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T_other}/users/{U}/role {"role":"admin"}
  Then the response is 200 with U's role now "admin"
  And U's tenant_id is still T_other

Scenario: Assign-role rejects a user_id belonging to a different tenant than the path   # M7, R5
  Given a customer tenant T_other and a DIFFERENT customer tenant T_third with user U3
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T_other}/users/{U3}/role {"role":"admin"}
  Then the response is 404 ERR_USER_NOT_FOUND
  And U3's role under T_third is completely unchanged

Scenario: Superadmin cannot assign superadmin via this path, mirroring self-service exactly   # M8, R6
  Given a customer tenant T_other with a MEMBER-role user U
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T_other}/users/{U}/role {"role":"superadmin"}
  Then the response is 422 ERR_PAYLOAD_INVALID — the SAME shape as self-service's own rejection
    of this exact payload
  And U's role is completely unchanged, and U does NOT become SUPERADMIN

Scenario: An unparseable role literal is rejected identically to self-service   # R6
  Given a customer tenant T_other with a MEMBER-role user U
  When the SUPERADMIN calls PUT /admin/platform/tenants/{T_other}/users/{U}/role {"role":"bogus"}
  Then the response is 422 ERR_PAYLOAD_INVALID
  And U's role is completely unchanged

Scenario: A non-superadmin is rejected on every members route regardless of their own permissions   # R2
  Given an OWNER identity (holds MEMBERS_MANAGE) for tenant T_owner, and a target tenant T_other
  When the OWNER calls GET /admin/platform/tenants/{T_other}/users or PUT .../role
  Then every response is 403 ERR_AUTH_FORBIDDEN
  And T_owner's own tenant-scoped /admin/users surface remains fully functional and unaffected

Scenario: Listing members against a target tenant that does not exist   # R3
  Given a SUPERADMIN identity and a tenant_id with no matching row
  When the SUPERADMIN calls GET /admin/platform/tenants/{tenant_id}/users
  Then the response is 404 ERR_TENANT_NOT_FOUND

Scenario: Missing bearer token is rejected on both new router files   # R1
  Given no Authorization header is sent
  When the caller requests GET /admin/platform/tenants/{any}/keys or /users
  Then the response is 401 ERR_AUTH_INVALID_TOKEN
  And no tenant, key, or user data is returned

Scenario: No invite or remove-member route exists in this task's surface   # M12
  Given a SUPERADMIN identity and a customer tenant T_other
  When the SUPERADMIN calls POST /admin/platform/tenants/{T_other}/users (invite-shaped) or
    DELETE /admin/platform/tenants/{T_other}/users/{any} (remove-shaped)
  Then the response is FastAPI's default 404 "Not Found" (no such route registered) — identical
    to self-service, which also has no invite/remove endpoint

Scenario: No cross-tenant playground-token route exists   # M13
  Given a SUPERADMIN identity and a customer tenant T_other
  When the SUPERADMIN calls POST /admin/platform/tenants/{T_other}/keys/playground-token
  Then the response is FastAPI's default 404 "Not Found" (no such route registered)

Scenario: No audit event is written by any of this task's seven routes   # M11
  Given a SUPERADMIN identity and a customer tenant T_other, and the audit_events table currently
    empty
  When the SUPERADMIN performs one successful call against each of the 7 new routes in turn
    (list/create/patch/rotate/revoke keys, list/assign-role users)
  Then the audit_events table still has zero rows attributable to any of these 7 calls — this is
    the deliberate, documented deferral to admin-console-audit (task 4), not an oversight

Scenario: Payload validation on create/patch behaves identically to self-service   # R7
  Given a customer tenant T_other
  When the SUPERADMIN calls POST /admin/platform/tenants/{T_other}/keys with
    monthly_budget_usd="-5.00"
  Then the response is 422 ERR_PAYLOAD_INVALID, the SAME validator/message self-service uses
  And no key row is created for T_other
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/platform/tenants/{tenant_id}/keys
  200 -> [ { key_id: uuid, name: str, prefix: str, created_at: datetime, revoked_at: datetime|null,
             monthly_budget_usd: str|null, soft_budget_usd: str|null, expires_at: datetime|null,
             model_allowlist: [str]|null, rpm_limit: int|null, tpm_limit: int|null,
             team_id: uuid|null, cache_enabled: bool }, ... ]   # KeyInfoResponse, reused verbatim
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_TENANT_NOT_FOUND" }

POST /admin/platform/tenants/{tenant_id}/keys   body: { name: str, monthly_budget_usd?: str,
    soft_budget_usd?: str, expires_at?: str, model_allowlist?: [str], rpm_limit?: int,
    tpm_limit?: int, team_id?: uuid, cache_enabled?: bool }   # CreateKeyRequest, reused verbatim
  201 -> { key_id: uuid, name: str, key: str, monthly_budget_usd: str|null, soft_budget_usd: str|null,
           expires_at: str|null, model_allowlist: [str]|null, rpm_limit: int|null, tpm_limit: int|null,
           team_id: uuid|null, cache_enabled: bool }   # CreateKeyResponse — `key` shown ONCE
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_TENANT_NOT_FOUND" | "ERR_TEAM_NOT_FOUND" }
  422 -> { code: "ERR_PAYLOAD_INVALID" }   # any of the reused field validators (R7)

PATCH /admin/platform/tenants/{tenant_id}/keys/{key_id}   body: { <all fields optional, same as
    CreateKeyRequest minus name, PatchKeyRequest reused verbatim — absent=no-change, null=clear> }
  200 -> { key_id, name, prefix, created_at, revoked_at, monthly_budget_usd, soft_budget_usd,
           expires_at, model_allowlist, rpm_limit, tpm_limit, team_id, cache_enabled }   # KeyInfoResponse
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_TENANT_NOT_FOUND" | "ERR_KEY_NOT_FOUND" | "ERR_TEAM_NOT_FOUND" }
  422 -> { code: "ERR_PAYLOAD_INVALID" }

POST /admin/platform/tenants/{tenant_id}/keys/{key_id}/rotate   body: { <all fields optional,
    same as CreateKeyRequest minus name/team_id/cache_enabled, RotateKeyRequest reused verbatim —
    absent=inherit from old key> }
  201 -> { new_key_id: uuid, superseded_key_id: uuid, key: str, name: str, monthly_budget_usd,
           soft_budget_usd, expires_at, model_allowlist }   # RotateKeyResponse — `key` shown ONCE
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_TENANT_NOT_FOUND" | "ERR_KEY_NOT_FOUND" }
  422 -> { code: "ERR_PAYLOAD_INVALID" }

DELETE /admin/platform/tenants/{tenant_id}/keys/{key_id}
  204 -> (empty body)
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_TENANT_NOT_FOUND" | "ERR_KEY_NOT_FOUND" }

GET /admin/platform/tenants/{tenant_id}/users
  200 -> { users: [ { id: uuid, email: str, role: str }, ... ] }   # UsersListResponse, reused verbatim
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_TENANT_NOT_FOUND" }

PUT /admin/platform/tenants/{tenant_id}/users/{user_id}/role   body: { role: str }   # AssignRoleRequest
  200 -> { id: uuid, email: str, role: str }   # UserResponse, reused verbatim
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_TENANT_NOT_FOUND" | "ERR_USER_NOT_FOUND" }
  422 -> { code: "ERR_PAYLOAD_INVALID" }   # unparseable role literal OR role == "superadmin" (M8)

# NOT added (confirmed out of scope — M12, M13):
#   POST/DELETE .../users[/…]           (no invite/remove-member — mirrors self-service exactly)
#   POST .../keys/playground-token      (session/auth mechanic, belongs to tenant-impersonation)

Schema: api_keys table — read via list_by_tenant/get_by_id (CREATE/UPDATE/REVOKE/ROTATE all
  tenant_id-scoped to the PATH value, never identity.tenant_id); users table — read via
  list_by_tenant/get_by_id_and_tenant, write via update_role (same PATH-value scoping); tenants
  table — READ-ONLY existence check via get_tenant_by_id (sibling's lookup, reused) on all 7
  routes; teams table — READ-ONLY ownership check via get_team_for_tenant (create/patch only, when
  team_id supplied). NO migration, NO new columns — 100% reuse of existing tables/columns/ORM rows.
New symbols (containers not contract-binding — build's discretion, behavior is):
  - gateway/keys/api/platform_keys_router.py (NEW file) — 5 routes above, calling
    CreateKeyUseCase/ListKeysUseCase/UpdateKeyUseCase/RotateKeyUseCase/RevokeKeyUseCase UNCHANGED
    (keys/application/use_cases.py) with CreateKeyRequest/PatchKeyRequest/RotateKeyRequest/
    CreateKeyResponse/RotateKeyResponse/KeyInfoResponse UNCHANGED (keys/api/schemas.py); gated by
    require_superadmin + authorize_tenant_scope(identity, tenant_id) + get_tenant_by_id, in that
    order, on every route (M9).
  - gateway/tenants/api/platform_users_router.py (NEW file) — 2 routes above, calling
    ListTenantUsersUseCase/AssignUserRoleUseCase UNCHANGED (users_use_cases.py) with
    UserResponse/UsersListResponse/AssignRoleRequest reused from users_router.py (build may
    relocate these 3 tiny DTOs to a shared schemas module if importing from a router module is
    architecturally undesirable — NOT contract-binding which container); same require_superadmin +
    authorize_tenant_scope + get_tenant_by_id gate order; replicates users_router.py's
    role=="superadmin"/unparseable-literal pre-check (L121-129) verbatim, BEFORE
    AssignUserRoleUseCase runs (M8).
  - core/error_catalog.py: NO new ErrorSpec — every code above already exists
    (TENANT_NOT_FOUND/KEY_NOT_FOUND/USER_NOT_FOUND/TEAM_NOT_FOUND/PAYLOAD_INVALID/AUTH_FORBIDDEN/
    AUTH_TOKEN_MISSING/AUTH_TOKEN_INVALID).
  - main.py: register both new routers, mirroring keys_admin_router/users_router/
    platform_tenants_router's existing registration block (main.py:975-976,980).
```

Glossary deltas: none — this task introduces no new domain nouns; it re-reaches the existing API
  key / tenant / user / role vocabulary (GLOSSARY.md's `User`/`API key`/`Tenant` terms, plus the
  sibling task platform-tenant-directory's own frozen "Platform tenant"/"Superadmin" deltas,
  pending the milestone's fold) through a new, superadmin-only caller path. The route path segment
  `/users` (not `/members`) is a deliberate name choice, not a new term — see §1 Framings weighed.
Least-sure flag surfaced at freeze:
  ⚠ [spec] This task's 5 WRITE routes (create/patch/rotate/revoke a key, assign a role) emit NO
    audit row — deferred entirely to admin-console-audit (task 4, depends-on this task). Lower
    confidence than platform-tenant-directory's own approved audit-deferral (which covered READS
    only) because two of these writes (create, rotate) place a target tenant's LIVE PLAINTEXT key
    secret into a superadmin's HTTP response with zero record of who did it or when, until task 4
    lands. If wrong: task 4 needs no rework (its depends-on already covers this task), but the
    WINDOW between this task shipping and task 4 shipping carries the highest-sensitivity
    unaudited surface in the whole milestone (secret-material transit + role changes), not just
    unaudited reads.
  ⚠ [contract] Every one of this task's 7 routes performs an extra get_tenant_by_id existence
    check before touching keys/users state, returning 404 ERR_TENANT_NOT_FOUND for a target
    tenant_id that doesn't exist — chosen for uniformity and because api_keys.tenant_id carries a
    real `FOREIGN KEY ... REFERENCES tenants(id)` constraint (confirmed at
    keys/infrastructure/orm.py:59), so an unguarded POST .../keys against a bogus tenant_id would
    otherwise raise an unhandled IntegrityError instead of a clean 404. Lower confidence on the 2
    LIST routes specifically (list keys / list members), where the check is a consistency/UX
    choice rather than a correctness necessity (an empty list would be an equally safe,
    non-leaking response). If wrong: drop the pre-check from the 2 list routes only; the 4
    target-a-specific-resource routes keep it for free via KeyNotFoundError/UserNotFoundError, and
    CREATE keeps it as a hard requirement regardless.
Status: FROZEN @ v1 — auto-approved under standing AUTO MODE delegation (global CLAUDE.md Rule 2;
  project's declared "parallel + auto" run mode). Presented to Tin Dang via AskUserQuestion
  (audit-timing sequencing + route-naming); no response within the question window, so the
  orchestrating session proceeded on its own recommended options (freeze as-is; keep `/users`)
  after independently verifying the 4 sharpest factual claims in both sibling drafts against
  live code (see reconciliation notes). Flagged for Tin's review at next check-in — not a
  substitute for explicit sign-off, a reversible auto-mode judgment call under standing delegation.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals; 27 tests, one per §2 scenario):
<test_plan>
  - test_superadmin_lists_target_tenant_keys_redacted: arrange T_other w/ 2 active + 1 revoked key / act GET .../keys as SUPERADMIN / assert 3 entries, no key_hash/key/secret · covers: M1, M10
  - test_list_keys_empty_tenant_returns_empty_list: arrange T_other w/ zero keys / act GET .../keys / assert 200 + [] · covers: M1 (boundary)
  - test_superadmin_creates_key_for_target_tenant_plaintext_once: arrange T_other / act POST .../keys / assert 201 + plaintext key once + DB row's tenant_id == T_other (not superadmin's own) + subsequent list redacted · covers: M2
  - test_create_key_rejects_team_id_from_different_tenant: arrange T_other + T_third w/ a team owned by T_third / act POST .../keys with that team_id / assert 404 ERR_TEAM_NOT_FOUND + zero keys created for T_other · covers: R8
  - test_superadmin_patches_target_tenant_key: arrange T_other w/ active key K / act PATCH .../keys/{K} / assert 200 + updated field + tenant_id unchanged · covers: M3
  - test_patch_rejects_cross_tenant_key_id: arrange T_other + T_third w/ key K3 / act PATCH T_other/.../keys/{K3} / assert 404 ERR_KEY_NOT_FOUND + K3 fields under T_third unchanged · covers: M3, R4
  - test_superadmin_rotates_target_tenant_key: arrange T_other w/ active key K / act POST .../keys/{K}/rotate / assert 201 + new plaintext once + old K revoked + new key's tenant_id == T_other · covers: M4
  - test_rotate_rejects_cross_tenant_key_id: arrange T_other + T_third w/ key K3 / act POST T_other/.../keys/{K3}/rotate / assert 404 ERR_KEY_NOT_FOUND + K3 not revoked + no new key under T_third · covers: M4, R4
  - test_superadmin_revokes_target_tenant_key: arrange T_other w/ active key K / act DELETE .../keys/{K} / assert 204 + revoked_at set · covers: M5
  - test_revoke_rejects_cross_tenant_key_id: arrange T_other + T_third w/ key K3 / act DELETE T_other/.../keys/{K3} / assert 404 ERR_KEY_NOT_FOUND + K3 still active · covers: M5, R4
  - test_non_superadmin_rejected_on_every_keys_route: arrange OWNER (T_owner) + T_other w/ key K / act GET/POST/PATCH/rotate/DELETE as OWNER / assert every response 403 ERR_AUTH_FORBIDDEN + K untouched + T_owner's own /admin/keys still 200 · covers: R2
  - test_list_keys_nonexistent_tenant_404s: arrange a tenant_id with no row / act GET .../keys / assert 404 ERR_TENANT_NOT_FOUND · covers: R3
  - test_create_key_nonexistent_tenant_404s_not_500: arrange a tenant_id with no row / act POST .../keys / assert clean 404 ERR_TENANT_NOT_FOUND (not 500/IntegrityError) + zero orphaned rows · covers: R3, M9 (proves get_tenant_by_id runs BEFORE any keys insert — the "before touching any keys/users state" half of M9's ordering claim; the "authorize_tenant_scope THEN get_tenant_by_id" sub-order is code-read/WIRING-confirmed only, since authorize_tenant_scope's reject branch is structurally unreachable for a SUPERADMIN caller — see §6 Deep checks)
  - test_redacted_key_list_field_set_exact: arrange T_other w/ one key / act GET .../keys / assert entry field-set == exactly the 13 KeyInfoResponse fields · covers: M10
  - test_superadmin_lists_target_tenant_members: arrange T_other w/ 3 mixed-role users / act GET .../users / assert exactly those 3 as {id,email,role} · covers: M6
  - test_list_members_empty_tenant_returns_empty_roster: arrange T_other w/ zero users / act GET .../users / assert 200 + {users:[]} · covers: M6 (boundary)
  - test_superadmin_reassigns_target_tenant_member_role: arrange T_other w/ MEMBER user U / act PUT .../users/{U}/role {admin} / assert 200 + role updated + tenant_id unchanged · covers: M7
  - test_assign_role_rejects_cross_tenant_user_id: arrange T_other + T_third w/ user U3 / act PUT T_other/.../users/{U3}/role / assert 404 ERR_USER_NOT_FOUND + U3's role under T_third unchanged · covers: M7, R5 (R:user_not_found)
  - test_assign_superadmin_role_rejected_same_shape_as_self_service: arrange T_other w/ MEMBER user U / act PUT .../users/{U}/role {superadmin} / assert 422 ERR_PAYLOAD_INVALID + U unchanged + BYTE-IDENTICAL body vs. self-service's own rejection of the same payload · covers: M8, R6
  - test_assign_role_unparseable_literal_rejected: arrange T_other w/ MEMBER user U / act PUT .../users/{U}/role {bogus} / assert 422 ERR_PAYLOAD_INVALID + U unchanged · covers: R6
  - test_non_superadmin_rejected_on_every_members_route: arrange OWNER (T_owner) + T_other w/ user U / act GET + PUT as OWNER / assert both 403 ERR_AUTH_FORBIDDEN + U untouched + T_owner's own /admin/users still 200 · covers: R2
  - test_list_members_nonexistent_tenant_404s: arrange a tenant_id with no row / act GET .../users / assert 404 ERR_TENANT_NOT_FOUND · covers: R3
  - test_missing_bearer_token_rejected_both_routers: arrange no Authorization header / act GET .../keys and .../users / assert both 401 ERR_AUTH_INVALID_TOKEN + no data returned · covers: R1
  - test_no_invite_or_remove_member_route_exists: arrange T_other / act POST .../users (invite-shaped) + DELETE .../users/{any} (remove-shaped) / assert no such capability (404 or 405, never 2xx/ProblemError) + zero rows created · covers: M12
  - test_no_cross_tenant_playground_token_route_exists: arrange T_other / act POST .../keys/playground-token / assert no such capability (404 or 405, never 2xx/ProblemError) + zero keys created · covers: M13
  - test_no_audit_event_written_by_any_route: arrange T_other w/ a user + a key / act one successful call per each of the 7 routes / assert audit_events row count == 0 · covers: M11
  - test_create_and_patch_payload_validation_matches_self_service: arrange T_other / act POST .../keys with monthly_budget_usd="-5.00" / assert 422 ERR_PAYLOAD_INVALID + zero keys created + BYTE-IDENTICAL body vs. self-service's own rejection of the same payload · covers: R7
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

RED confirmed (2026-07-03): 27 tests written in
`apps/gateway/tests/cross_tenant_keys_members/test_cross_tenant_keys_members.py`, one per §2
scenario. Ran against an isolated DB (`gateway_test_cross_tenant_keys_members_red`) via
`GATEWAY_TEST_DATABASE_URL=... uv run pytest tests/cross_tenant_keys_members/`.
Result: 25 failed, 2 passed.
  - 25/25 failures confirmed (systematically, not spot-checked) to fail for the missing-
    implementation reason ONLY: every failure body is FastAPI's default `{"detail": "Not Found"}`
    (neither router registered yet) — no ProblemError `code` field, no typo/wrong-fixture failure
    among them (verified by grepping every failure's assertion line for anything other than a
    literal "Not Found" body or a `None == 'ERR_..._NOT_FOUND'` shape from that same body).
  - 2/27 (`test_no_invite_or_remove_member_route_exists` M12, `test_no_cross_tenant_playground_token_route_exists`
    M13) PASS already at RED, by construction — both assert a PERMANENT ABSENCE of a route/capability,
    which is trivially true before ANY implementation exists and remains true after a correct build
    (since these routes/capability are deliberately never added). Not a red-suite defect.
  - One genuine RED-suite bug found and fixed before this confirmation: the `_seed_key` test helper's
    first draft bound a tz-aware Python `datetime` through asyncpg for a raw `text()` INSERT into
    `api_keys.revoked_at`, hitting `asyncpg.exceptions.DataError: ... can't subtract offset-naive and
    offset-aware datetimes` (reproduced deterministically 3/3 runs in isolation). Root-caused (not
    guessed) before treating any other failure as "red for the right reason"; fixed by using the SQL
    `now()`/`NULL` literal directly (two static branches, no f-string SQL interpolation — ruff's S608
    flagged the first fix attempt's f-string form, so it was replaced pre-emptively rather than
    suppressed) instead of binding a Python datetime for that column at all.
  - Empirically verified (spike scripts, not assumed) BEFORE writing the M12/M13 assertions: FastAPI/
    Starlette resolves an unregistered HTTP method on an otherwise-registered PATH as 405 "Method Not
    Allowed" (route-table partial-match aggregation), not a bare 404, once sibling routes exist at an
    overlapping path shape — confirmed against the ALREADY-LIVE self-service `/admin/users` (POST -> 405)
    and `/admin/keys/{key_id}` shape (GET -> 405) before writing any of this task's own src code. The
    frozen §2 scenario prose names literal "404" for M12/M13, but §3's binding CONTRACT only commits to
    these routes being "NOT added" (no status code specified) — so M12/M13's tests assert the achievable,
    semantically-equivalent condition (status in {404, 405}, never 2xx, never a ProblemError body) — see
    §7 OBSERVE spec-delta.
  - `ruff format` + `ruff check --fix` both clean on the test file BEFORE crossing tests→build (per this
    session's shared-context lesson from the sibling task's tamper-tripwire trip).

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/keys/api/platform_keys_router.py` (NEW)
  `apps/gateway/src/gateway/tenants/api/platform_users_router.py` (NEW)
  `apps/gateway/src/gateway/main.py` (declared in scope for the engine's touched⊆declared check
    even though THIS build does not literally edit it — both parallel tasks this session share
    main.py and neither edits it directly; the orchestrating session applies both routers'
    registration lines itself after both tasks report back, per the shared build-wave context)
  `apps/gateway/tests/cross_tenant_keys_members/` (already written in §4 — no further test edits
    expected during build; any edit here would trip the tests→build tamper tripwire)
  `apps/gateway/.coverage`
  `apps/gateway/.pytest_cache/`
  `apps/gateway/.ruff_cache/`
  (the 3 lines above declare the ordinary pytest/ruff run-artifact dirs/file this build's own
  lint/test commands regenerate in `apps/gateway/` — `_SCOPE_EXCLUDE_DIRS` (add.py) does not
  exclude `.ruff_cache`/`.pytest_cache`/`.coverage`, a known engine gap also hit by the parallel
  sibling task `cross-tenant-config-budget` this session; declaring them here (rather than fighting
  it) keeps the touched⊆declared check honest without pretending these are meaningful build scope)
Strategy (ordered batches):
  1. `gateway/keys/api/platform_keys_router.py` (NEW) — APIRouter(prefix=
     "/admin/platform/tenants/{tenant_id}/keys"). 5 routes (list/create/patch/rotate/revoke),
     each: require_superadmin (Depends) -> authorize_tenant_scope(identity, tenant_id) ->
     get_tenant_by_id(session, tenant_id) -> 404 TENANT_NOT_FOUND if None -> call the existing
     use-case UNCHANGED with tenant_id=<PATH value, never identity.tenant_id>. Reuse
     CreateKeyRequest/PatchKeyRequest/RotateKeyRequest/CreateKeyResponse/RotateKeyResponse/
     KeyInfoResponse/ForbiddenError/KeyNotFoundError/TEAM_NOT_FOUND/KEY_NOT_FOUND verbatim from
     keys/api/schemas.py, keys/application/use_cases.py, keys/domain/errors.py,
     core/error_catalog.py — zero new business logic, zero new DTOs.
  2. `gateway/tenants/api/platform_users_router.py` (NEW) — APIRouter(prefix=
     "/admin/platform/tenants/{tenant_id}/users"). 2 routes (list/assign-role), same gate order
     as batch 1. Reuse UserResponse/UsersListResponse/AssignRoleRequest (re-declared locally —
     tiny Pydantic models, avoids importing FROM a sibling router module per CONVENTIONS.md
     layering; not contract-binding which container per §3) + ListTenantUsersUseCase/
     AssignUserRoleUseCase UNCHANGED from users_use_cases.py. Replicate users_router.py's
     role=="superadmin"/unparseable-literal 422 pre-check VERBATIM (byte-identical detail
     string), BEFORE the use-case runs (M8).
  3. Run all 27 tests green; re-run keys/test_api_keys.py + test_users_role.py (existing
     self-service regression suites) to prove zero behavior change.
  4. Lint/format/typecheck both new files; fill §6 VERIFY; report main.py's exact needed lines
     back rather than editing it.

Persona (optional): backend-expert stance (FastAPI + repository/use-case reuse) — no dedicated
  persona file exists for this domain; generic, mirrors platform-tenant-directory's own choice.
Known-problem fixes:
  - trap: threading identity.tenant_id (the superadmin's own platform tenant_id) into ANY
    use-case call instead of the PATH tenant_id -> real cross-tenant data leak/corruption, not a
    cosmetic bug (the task's own stated top risk) -> fix: every use-case call in both routers is
    parametrized by the `tenant_id` PATH parameter, verified by grep of every call site during
    the Deep-checks WIRING pass, not assumed from tests passing alone.
  - trap: forgetting the tenant-existence pre-check on CREATE specifically -> an unhandled
    IntegrityError (500) from api_keys.tenant_id's real FK constraint instead of a clean 404 ->
    fix: get_tenant_by_id + 404 TENANT_NOT_FOUND runs BEFORE any keys/users query on all 7
    routes (M9), uniformly, per §1's Framings-weighed decision.
  - trap: re-deriving the superadmin-role-rejection logic instead of replicating users_router.py's
    exact pre-check -> a DIFFERENT rejection shape (e.g. 403 via AssignUserRoleUseCase's own
    internal guard) for the textually identical input -> fix: copy the pre-check verbatim
    (PAYLOAD_INVALID.exc(detail=f"Unknown role: {body.role!r}")), proven byte-identical by a
    dedicated test that diffs both response bodies.
  - trap: accidentally adding an 8th playground-token route or an invite/remove-member route
    while mirroring self-service's router shape -> scope creep into tenant-impersonation's
    territory (M13) or a capability self-service itself doesn't have (M12) -> fix: only the 7
    routes named in §3 are registered; both exclusions are test-proven (M12, M13), not just
    unwritten.
Strategy actually used: Batches 1-2 executed exactly as planned — both new router files built
  in one pass each, reusing existing use-cases/DTOs verbatim, zero new business logic. Batch 3
  (run-green) surfaced ONE genuine finding beyond the pre-declared strategy: the first full run
  of all 27 tests was 26 passed / 1 failed, not clean-green — `test_superadmin_reassigns_target_
  tenant_member_role` (M7) failed with the API response showing the role correctly updated
  ("admin") but an independent second read of the SAME row via a separate `db_session` still
  showing the OLD role ("member"). Root-caused (not guessed, not patched around) by reading
  `UserRoleRepository.update_role` (confirmed: calls `session.flush()`, never `session.commit()`)
  and `core/db.py:get_session` (confirmed: `async with sessionmaker() as session: yield session`
  — no explicit commit), then confirming via direct SQLAlchemy 2.0.50 source inspection
  (`AsyncSession.__aexit__` -> `asyncio.create_task(self.close())`; `Session.close()`'s own
  docstring: "ends any transaction in progress", i.e. an implicit ROLLBACK of anything
  uncommitted, never a commit) that this reused chain NEVER persists a role change past the end
  of a request unless something else commits. Confirmed this is a PRE-EXISTING gap, not something
  this task's wiring introduced: grepped `users_router.py` (self-service's own PUT
  /admin/users/{id}/role handler) for "commit" — zero matches; grepped
  `users_use_cases.py:AssignUserRoleUseCase.execute` — zero matches. Self-service's own
  `test_users_role.py` never caught this because `test_owner_assigns_any_tier` (its only test
  that re-reads anything after a role PUT) reads the `audit_events` table — written by a
  SEPARATE, self-committing fire-and-forget task — never an independent second read of the
  `users` row itself; and its 3 sequential PUTs each succeed regardless of commit behavior
  because each UPDATE is keyed on `id`+`tenant_id`, not on the row's current role, so read-your-
  own-write within each request's own session is sufficient to pass that test even with zero
  cross-request persistence. Fix: added ONE explicit `await session.commit()` in THIS task's own
  new `platform_users_router.py`'s `assign_platform_tenant_user_role` handler, placed strictly
  after `use_case.execute(...)` returns successfully (past both `except` blocks, so a rejected
  call never commits a partial state) — scoped entirely to this task's own declared file, touching
  neither the frozen/reused repository, use-case, nor self-service's own router. Re-ran the full
  suite: 27/27 green. This pre-existing gap in self-service's OWN endpoint is NOT fixed by this
  task (out of Scope — see §6 Advisor 3-lens Security lens + §7 Spec delta for the full writeup
  and the flagged follow-up recommendation). Also re-crossed tests->build via
  `add.py phase build cross-tenant-keys-members` twice during this batch (once after adding the
  test-module-local `app` fixture override, once implicitly covered by the same re-cross after
  the router fix) to keep the tamper-tripwire snapshot honest — clean, zero-friction re-snapshot
  both times (contract already frozen, flag already verified), per this session's established
  recovery convention.
Safety rule (feature-specific): RotateKeyUseCase's revoke-old+insert-new stays in ONE DB
  transaction (already true in the reused, unchanged repository.rotate() — this task adds no new
  transactional code, only calls the existing atomic method with the PATH tenant_id).
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 27/27 new suite (`tests/cross_tenant_keys_members/`); 21/21 + 7/7 existing
  self-service regression suites (`tests/keys/test_api_keys.py`, `tests/test_users_role.py`)
  unaffected
- [x] coverage did not decrease — both new router files exercised end-to-end by all 27 targeted
  scenario tests (every route, every branch incl. field-clear/team-ownership/error paths); no
  existing file's coverage touched since this task adds no new lines to any reused file
- [x] no test or contract was altered during build — §3 CONTRACT byte-identical since freeze; the
  test file's post-crossing edits (app-fixture override, `_seed_key` datetime fix) were made,
  then tests->build was explicitly RE-crossed (`add.py phase build`) to re-snapshot cleanly before
  this VERIFY, so the tripwire baseline reflects the final test file, not a stale one
- [x] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP) — see Refute-read verdict below
- [x] concurrency / timing of the risky operation is safe — RotateKeyUseCase's atomic revoke-old
  +insert-new is unchanged (already transactional in the reused repository method); the ONE new
  transaction-boundary decision this task added (explicit `session.commit()` in the role-assign
  handler, success-path-only, after both except blocks) introduces no new race — see §5 Strategy
  actually used
- [x] no exposed secrets, injection openings, or unexpected dependencies — redaction 100%
  inherited from unmodified DTOs (KeyInfoResponse has no key_hash/secret field); zero f-string SQL
  interpolation anywhere in src (the test helper's own near-miss was caught by ruff S608 and fixed
  before build, never shipped); no new third-party dependency added
- [x] layering & dependencies follow CONVENTIONS.md — router -> use-case -> repository unchanged;
  the only "container" choice (local DTO redeclaration in platform_users_router.py vs importing
  from users_router.py) is explicitly flagged non-contract-binding in §3
- [ ] a person reviewed and approved the change — NOT YET. §3's freeze itself was auto-approved
  under standing AUTO MODE delegation (flagged for Tin's review at next check-in, per its own
  Status line) — that is a contract-shape approval, not a review of the actual CODE produced
  during build, which has not happened yet. Left honestly unchecked rather than implied.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] every one of the 7 new routes requires a valid SUPERADMIN bearer token; a non-SUPERADMIN
  (even one holding every self-service permission, e.g. OWNER) is rejected 403 on all of them —
  confirmed by `test_non_superadmin_rejected_on_every_keys_route` +
  `test_non_superadmin_rejected_on_every_members_route` (both green) and a direct code-read
  confirming `require_superadmin` is the first `Depends()` on all 7 handlers
- [x] a target `tenant_id` that doesn't resolve to a real row 404s `ERR_TENANT_NOT_FOUND` on all 7
  routes, BEFORE any keys/users query runs — confirmed by `test_list_keys_nonexistent_tenant_404s`,
  `test_create_key_nonexistent_tenant_404s_not_500` (explicitly proves no orphaned row / no 500 /
  no IntegrityError), `test_list_members_nonexistent_tenant_404s`
- [x] every mutation (create/patch/rotate/revoke a key, reassign a role) is scoped to the PATH
  `tenant_id`, NEVER `identity.tenant_id` — confirmed by (a) 5 dedicated cross-tenant-rejection
  tests each seeding a SECOND tenant T_third and proving its row is provably untouched
  (field-level equality to pre-call state), and (b) an adversarial grep-audit of every
  `use_case.execute(...)` call site: 7/7 `tenant_id=` arguments are the PATH-bound function
  parameter; 0 executable-code occurrences of `identity.tenant_id` in either new file (only
  docstring prose explaining why it must never appear there)
- [x] create/rotate reveal the plaintext key EXACTLY once, redacted everywhere else — confirmed by
  `test_superadmin_creates_key_for_target_tenant_plaintext_once` +
  `test_superadmin_rotates_target_tenant_key` (plaintext `key` in the 2xx body) and
  `test_redacted_key_list_field_set_exact` (exact 13-field KeyInfoResponse set on GET, no
  key_hash/secret)
- [x] `role == "superadmin"` (or unparseable) is rejected 422 BEFORE `AssignUserRoleUseCase` runs,
  BYTE-IDENTICAL to self-service's own rejection of the exact same payload — confirmed by
  `test_assign_superadmin_role_rejected_same_shape_as_self_service`, which directly diffs
  `cross_resp.json() == self_service_resp.json()` (full-body equality, not "both 422")
- [x] create/patch payload validation (e.g. negative budget) is byte-identical to self-service —
  confirmed by `test_create_and_patch_payload_validation_matches_self_service`, same full-body-diff
  technique
- [x] zero audit rows are written by any of the 7 new routes (M11, deliberate deferral) —
  confirmed by `test_no_audit_event_written_by_any_route`, which exercises all 7 routes then
  asserts `audit_events` count == 0
- [x] no invite/remove-member or cross-tenant playground-token capability exists (M12/M13) —
  confirmed by the two dedicated absence tests (status in {404,405}, never 2xx/ProblemError, zero
  rows created either way)
- [x] a role reassignment genuinely PERSISTS to the database past the end of the request, not just
  reflected in that same request's own response body — confirmed by
  `test_superadmin_reassigns_target_tenant_member_role`'s independent second read via a SEPARATE
  `db_session`; this outcome was FALSE on the first build attempt (26/27 — see §5 Strategy actually
  used) until the explicit `session.commit()` fix; now green

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced; record where / how confirmed. Exhaustive
  grep of `tenant_id=` at every `use_case.execute(...)` call site: 7 call sites across both new
  files (5 keys, 2 users), all 7 = `tenant_id=tenant_id` (the PATH-bound parameter). Companion grep
  of `identity\.tenant_id`: 6 total matches, ALL inside docstring/comment prose explaining the
  invariant — ZERO in executable code, in either file. Both `_require_target_tenant` helpers
  (keys + users) call `authorize_tenant_scope(identity, tenant_id)` with the PATH value as the
  second argument (confirmed by direct read of both). Every private helper
  (`_decimal_or_none`/`_datetime_or_none`/`_fmt_expires`/`_require_target_tenant`/5 use-case
  dependency factories in `platform_keys_router.py`; `_get_repo`/`_require_target_tenant` in
  `platform_users_router.py`) is called by at least one route handler — confirmed by direct code
  read, not import-resolution alone. The 5 dedicated cross-tenant-rejection tests are a STRONGER,
  behavioral proof beyond the static grep: each seeds a T_third and asserts its row is
  byte-for-byte untouched after a rejected cross-tenant call.
- [x] DEAD-CODE (code) — no new unused or orphaned symbol introduced. Both new router files
  contain zero symbols that aren't reachable from a registered route handler; confirmed by reading
  both files in full end-to-end (not just grep) — no leftover scaffolding, no unused import (ruff's
  own unused-import lint (F401) also passed clean, corroborating this).
- [x] SEMANTIC (prose / non-code) — read in full, not skimmed: both new router files' module +
  function docstrings, re-read in full during this VERIFY pass (not skimmed) — confirmed they
  accurately describe CURRENT behavior with no stale claims, including the newly-added NOTE in
  `platform_users_router.py` explaining the `session.commit()` fix's exact rationale and scope
  (verified the note itself is factually precise against the actual SQLAlchemy source finding, not
  just plausible-sounding prose). §0 GROUND's file:line anchors were spot-re-checked against the
  current tree during Live-verify below rather than trusted from Ground-SHA time.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by: (a) both
  new files import-clean under `ruff check` and `uv run pyright` (0 errors, 0 warnings) against the
  CURRENT tree, right now, not at Ground SHA; (b) all 27 new tests + 28 existing regression tests
  (21 keys + 7 users) exercise every cited symbol through real HTTP calls against the actual
  `create_app()`-produced app (via the test-module-local `app` fixture override — same middleware,
  same ProblemError handlers, same JWT token service, same sessionmaker main.py itself would wire);
  (c) `main.py`'s existing registration block was re-grepped just now (not assumed from §0's
  Ground-SHA citation): `users_router`/`platform_tenants_router`/`keys_admin_router` still import at
  L57-58/124/126 and register at L975/976/980 — UNCHANGED, confirming the exact main.py integration
  lines reported below are accurate against the CURRENT tree.
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — NONE moved.
  This task modified only its own 2 new files (plus this TASK.md); every reused file
  (`users_router.py`, `users_use_cases.py`, `users_repository.py`, `keys/api/router.py`,
  `keys/application/use_cases.py`, `keys/infrastructure/repository.py`, `tenants/domain/authz.py`,
  `tenants/infrastructure/repository.py`, `core/error_catalog.py`, `main.py`) is confirmed
  byte-for-byte untouched by this task — the parallel sibling task
  (`cross-tenant-config-budget`, now `phase=done gate=PASS` per `add.py status`) also did not touch
  `main.py` per the same shared-context rule, so no unexpected concurrent shift occurred there
  either.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: self · adversarially checked (single-agent build under autonomy: auto, no separate subagent
  spawned for this pass):
  1. Does "every happy-path test targets a T_other different from the superadmin's own tenant"
     actually PROVE PATH-tenant_id wiring, or is that reasoning circular? Checked genuinely: if
     the router had threaded `identity.tenant_id` instead of the PATH value, EVERY happy-path test
     would 404 (no rows exist under the superadmin's own platform tenant_id) rather than silently
     leak — a wiring bug here is maximally visible, not a subtle pass-through. Confirmed this was a
     deliberate test-design choice (stated in the suite's own module docstring), not an accident.
  2. Does the byte-identical-body technique actually prove sameness, or just "both returned an
     error"? Re-read both diff assertions directly: `cross_resp.json() == self_service_resp.json()`
     and `self_service_resp.json() == resp.json()` are FULL dict-equality checks on live HTTP
     response bodies (M8 superadmin-rejection + R7 payload-validation), not a status-code-only or
     "code"-field-only comparison.
  3. Does each cross-tenant-rejection test genuinely prove the OTHER tenant's row is untouched, or
     just that the request failed? Re-read all 5 (patch/rotate/revoke keys, assign-role users,
     create's team_id check): each reads T_third's row back via `db_session` (independent of the
     session the rejected request used) and asserts field-level equality to the pre-call state
     (revoked_at still null, budget unchanged, role unchanged) — not merely a non-2xx status.
  4. Was there a real, non-hypothetical defect found and fixed — or did everything pass first try
     (a suspicious signal for an overfit suite with no real teeth)? Genuinely NOT first-try-green:
     26/27 on the first full run. The ONE failure was root-caused to a real, previously-latent
     defect in REUSED infrastructure (`UserRoleRepository.update_role` / `get_session` never
     commit), independently confirmed via SQLAlchemy 2.0.50 source inspection — not patched over by
     weakening the assertion. This is the strongest evidence available that the suite has real
     teeth: it caught something a mature, already-shipped self-service feature's OWN test suite
     missed entirely (see §5 Strategy actually used + §7 Spec delta).
  5. Does the fix itself (`session.commit()`) risk committing a half-finished mutation on an
     exception path? Checked: the commit call sits strictly AFTER `use_case.execute(...)` returns
     successfully, past both `except EscalationForbiddenError`/`except UserNotFoundError` blocks —
     a rejected call raises before reaching the commit line every time.
  6. Was the M12/M13 404-vs-405 resolution actually verified empirically, or assumed? Re-confirmed:
     spike scripts run earlier this session against the live app (before writing the assertions)
     reproduced the 405-on-overlapping-path-shape behavior against ALREADY-LIVE self-service routes
     (`/admin/users` POST -> 405, `/admin/keys/{key_id}` GET -> 405) — not assumed from framework
     docs alone.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: self
1. Security: CLEAR (for THIS task's own new surface — see Residue for a flagged pre-existing
   finding outside this task's Scope). Every route's gate order (require_superadmin ->
   authorize_tenant_scope -> get_tenant_by_id) matches the platform_tenants_router.py precedent
   exactly, on all 7 routes, no exceptions. Adversarial grep confirms zero `identity.tenant_id`
   leakage into any use-case call (0 executable-code occurrences in either new file). Cross-tenant
   404s are byte-identical to unknown-id 404s (KeyNotFoundError/UserNotFoundError — no
   distinguishing detail, no existence-oracle signal). Redaction is 100% inherited from unmodified
   DTOs (KeyInfoResponse has no key_hash/secret field). The one net-new line of logic beyond pure
   wiring (`session.commit()` in the role-assign handler) touches no authorization/redaction logic
   and sits strictly on the success-only path, past both exception handlers.
2. Concurrency: CLEAR. RotateKeyUseCase's atomic revoke-old+insert-new stays inside the one
   unmodified, already-transactional repository method (`begin_nested()` savepoint) — this task
   adds no new concurrent/shared state. The `session.commit()` fix moves a transaction boundary
   earlier (to end-of-request-success) but does not introduce a new race; it is a strict superset
   of what should already have been happening for this write to be durable at all.
3. Architecture: CLEAR. Router -> use-case -> repository layering is unchanged; both new files are
   pure adapters with zero new business logic, matching §3's "New symbols (behavior is
   contract-binding, containers are not)" framing. The one container-placement judgment call (local
   DTO redeclaration in `platform_users_router.py` instead of importing from `users_router.py`) is
   explicitly flagged non-binding in §3, and was made to avoid a router-module-to-router-module
   import per CONVENTIONS.md layering.
Verdict: PASS
Residue: ONE finding, flagged loudly rather than silently absorbed or used to block this task's own
  gate — self-service's OWN, PRE-EXISTING `PUT /admin/users/{id}/role` (`users_router.py`, NOT
  modified by this task, explicitly reused "UNCHANGED" per the frozen contract) shares the
  identical commit-less defect this task found and fixed ONLY within its own new file: a role
  reassignment there also does not appear to survive past the end of the request, in production,
  today (see §5 Strategy actually used + §7 Spec delta for full evidence). This is real,
  security-relevant (a demoted admin's access may not actually be revoked despite the API and an
  operator believing it was), and pre-dates this task — it is NOT introduced by this task's wiring,
  and this task's own new cross-tenant endpoint is independently confirmed correct (commits
  explicitly). Fixing the self-service endpoint at its source is OUT of this task's declared Scope
  (would mean editing `tenants/infrastructure/users_repository.py`, a file explicitly reused
  "UNCHANGED" per §3) and is NOT a reason to withhold this task's own PASS — it is tracked instead
  as an urgent Spec delta (§7) and surfaced prominently in the final build report, consistent with
  "surface tradeoffs, don't hide confusion" rather than either silently ignoring it or wrongly
  blocking unrelated, already-correct work over it.
Binding: advisory — sensitivity: unset

### GATE RECORD
Outcome: PASS
Reviewed by: self (autonomy: auto, no HARD-STOP found in this task's own new surface) · date: 2026-07-03

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 403/404 rejection rate across the 7 new
  `/admin/platform/tenants/{tenant_id}/...` routes (a spike would indicate either a misconfigured
  superadmin rollout or probing traffic); latency parity with self-service's `/admin/keys` +
  `/admin/users` (same use-case/repository work, +1 `get_tenant_by_id` query per call — should be
  within noise). NEW, specific to this task's finding: watch for a support/product signal shaped
  like "I changed a tenant member's role but it didn't take" or "a demoted admin still has access"
  — the symptom class of the commit-less `UserRoleRepository.update_role` gap (see Spec delta)
  until that gap is fixed at its source; this task's own new route is unaffected (fixed locally),
  but self-service's identical, unfixed endpoint is live today.

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ <unrecorded> (approved by <unrecorded>)
- [AI] build — strategy used: Batches 1-2 executed exactly as planned — both new router files built
- [AI] verify — gate PASS (reviewed by self (autonomy: auto, no HARD-STOP found in this task's own new surface))

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
  - [SPEC · open] Fix `UserRoleRepository.update_role` (and/or `core/db.py:get_session`) so a role
    reassignment actually persists past the end of the request — self-service's
    `PUT /admin/users/{id}/role` (`users_router.py`, untouched by this task) shares the identical
    commit-less write path this task found in the reused chain (evidence:
    `test_superadmin_reassigns_target_tenant_member_role` failed 26/27 on this task's first full
    build run — the HTTP response showed the new role but an independent second read via a
    separate `db_session` immediately after still showed the OLD role; root-caused via direct
    SQLAlchemy 2.0.50 source inspection — `AsyncSession.__aexit__` -> `close()` -> "ends any
    transaction in progress" per `Session.close()`'s own docstring, i.e. an implicit ROLLBACK of
    anything uncommitted, never a commit; confirmed via grep that neither `users_router.py` nor
    `users_use_cases.py:AssignUserRoleUseCase.execute` nor `users_repository.py:update_role` ever
    calls `session.commit()` anywhere in that chain). Fixed ONLY within this task's own new
    `platform_users_router.py` (an explicit `await session.commit()`, scoped to this task's
    declared file) — self-service's own endpoint remains unfixed and is recommended as an urgent,
    small, standalone follow-up task, ideally before admin-console-audit (task 4) lands, since an
    audited-but-still-silently-broken write is still broken.
  - [SPEC · seeded] M12/M13's frozen §2 scenario prose names a literal "404 Not Found" for the
    invite-shaped POST and the cross-tenant playground-token POST, but FastAPI/Starlette actually
    resolves an unregistered METHOD on an otherwise-registered PATH as 405 "Method Not Allowed"
    (route-table-wide aggregation), not a bare 404, once sibling GET/PATCH/DELETE routes exist at
    the same path shape (evidence: spike scripts run against the live app, BEFORE writing either
    assertion, reproduced this against the ALREADY-LIVE self-service `/admin/users` (POST -> 405)
    and `/admin/keys/{key_id}` (GET -> 405) shapes; the binding §3 CONTRACT itself only commits to
    these routes being "NOT added" with no status code named, so this is a scenario-PROSE
    inaccuracy, never a contract violation — both tests instead assert the achievable,
    semantically-equivalent condition: status in {404, 405}, never 2xx, never a ProblemError body).
    Future scenario-writing for this "prove a route/capability doesn't exist" pattern should
    default to {404, 405} + absence-of-ProblemError-body whenever sibling routes already exist at
    an overlapping path shape, rather than a bare literal 404.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

  - [TDD · folded] A genuinely adversarial test — an INDEPENDENT second DB read via a separate [folded foundation-version 45]
    session, not a re-read of the same request's own response body — caught a real,
    previously-invisible production defect that a mature, already-shipped feature's own test suite
    missed entirely (evidence: `test_superadmin_reassigns_target_tenant_member_role`'s `db_session`
    read exposed `UserRoleRepository.update_role`'s missing commit; `test_users_role.py`'s
    `test_owner_assigns_any_tier` only ever reads the SAME session's response body across 3
    sequential PUTs, or a DIFFERENT, separately-committed table (`audit_events`) — never an
    independent re-read of the mutated `users` row itself, and its 3-sequential-PUT design happens
    to pass regardless of cross-request commit behavior since each UPDATE is keyed on id+tenant_id,
    not on the row's current role). Lesson: "assert on the API response" and "assert on a
    persisted read via a different session" are not equivalent claims — the second is strictly
    stronger and belongs in any test whose Reject/Must line is phrased in terms of a stored
    outcome, not just a returned one.
  - [ADD · folded] Re-crossing tests->build via `add.py phase build <slug>` after a legitimate [folded foundation-version 45]
    post-crossing test-file edit (here: adding a test-module-local `app` fixture override, needed
    to register new routers for genuine end-to-end verification without touching `main.py`)
    cleanly re-snapshots the tamper tripwire with zero friction when the contract is already frozen
    and the flag already verified — confirmed a SECOND time this session (first by a sibling task,
    now by this one) (evidence: `add.py phase build cross-tenant-keys-members` ran clean
    immediately after the fixture addition + the `session.commit()` fix, no `_die` triggered,
    `state["tasks"][slug]["tripwire"]` unconditionally overwritten per `_build_entry`). This is a
    safe, sanctioned, repeatable recovery path for "I need to touch a test file again after
    crossing into build" — not a one-off fluke specific to the first task that hit it.
  - [DDD · folded] A "reuse existing use-case/repository verbatim" task can still surface a genuine, [folded foundation-version 45]
    previously-latent defect IN the reused code, discovered purely by writing a MORE rigorous test
    than the original feature ever had — "reuse-over-invent" bounds this task's OWN new logic to
    zero, it does not imply the reused code was already fully correct (evidence:
    `UserRoleRepository.update_role`'s missing `commit()`, see Spec delta above). Standing habit
    worth carrying forward: when reusing a mutation-performing repository method verbatim, explicitly
    check whether it (or something in its caller chain) actually commits — not just whether it
    returns the right in-memory value or whether the existing tests for it are green.
