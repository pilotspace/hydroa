# TASK: S1: signup invite-only default + routing-write ops permission

slug: signup-and-routing-authz · created: 2026-07-02 · stage: production · risk: high
autonomy: manual   <!-- SECURITY task, HARD-STOP verify reserved for Tin (tmp/eh-remaining-context.md) —
     lowered from the project-default `auto` per CLAUDE.md's non-negotiable ("a security finding is
     HARD-STOP — never auto-passed") and the run.md guard (`unguarded_high_risk_auto`) for a
     `risk: high` task. Design (this bundle) drafts only; Tin freezes, then Build/Verify stay
     human-gated per this line. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/tenants/api/router.py:39-52` `signup()` — `POST /admin/auth/signup`,
    PUBLIC, zero auth dependency, zero rate limit. Delegates to `SignupUseCase.execute`
    (`tenants/application/use_cases.py:21-35`), which calls
    `IdentityRepository.create_tenant_with_owner` (`tenants/infrastructure/repository.py:67-85`) —
    inserts a BRAND NEW `TenantRow` + a `UserRow` with `role=Role.OWNER` in ONE transaction, no
    invite, no approval, no rate limit. This is the ONLY path in the codebase that creates a new
    tenant; nothing else does.
  - `apps/gateway/src/gateway/tenants/api/schemas.py:6-14` `SignupRequest`/`SignupResponse` —
    `{tenant_name, email: EmailStr, password}` -> `{tenant_id, user_id}`. UNCHANGED by this task.
  - `apps/gateway/src/gateway/tenants/domain/authz.py:54-70` `Permission` (11 members, incl.
    `ROUTING_MANAGE = "routing_manage"`) + `:76-121` `ROLE_PERMISSIONS` (rbac-roles TASK.md §3,
    FROZEN @ v1, approved 2026-06-25) — an ALLOWLIST matrix with a HARD import-time completeness
    guard (`:126-138`): the app refuses to boot unless `ROLE_PERMISSIONS[Role.OWNER] ==
    frozenset(Permission)` — literally EVERY permission, always. **Structural finding**: this means
    no `Permission` can ever be scoped to exclude a tenant OWNER — adding a new enum member
    automatically grants it to every tenant's OWNER (and to SUPERADMIN, same clause) the instant it
    exists. A tenant-scoped `Permission` cannot express "platform-staff-only."
  - `apps/gateway/src/gateway/tenants/domain/authz.py:230-279` `require_permission(perm)` (a
    Permission-shaped, tenant-scoped gate) vs. `require_superadmin` (a ROLE-ONLY gate, deliberately
    NOT a `Permission` — see `platform_tenants_router.py:9-11`'s own docstring: "Bulk list has no
    single target_tenant_id, so it is gated by require_superadmin — a role-only check, deliberately
    NOT authorize_tenant_scope and NOT a Permission"). `require_superadmin` is the EXISTING,
    precedented gate for genuinely operator-wide (not tenant-scoped) resources; it raises the same
    `AUTH_FORBIDDEN.exc()` (403 `ERR_AUTH_FORBIDDEN`) as `require_permission`, byte-identical shape.
  - `apps/gateway/src/gateway/proxy/api/routing_admin_router.py:161-213` `GET`/`PUT /admin/routing`
    — BOTH gated by `require_permission(Permission.ROUTING_MANAGE)` (`:164`, `:175`), held by
    OWNER/ADMIN/OPERATOR of **every** tenant on the gateway (`authz.py:76-100`).
  - `apps/gateway/src/gateway/proxy/infrastructure/routing_config_orm.py` `RoutingConfigRow` — a
    genuine OPERATOR-WIDE SINGLETON: `id: Mapped[bool]` PK constrained `CHECK (id IS TRUE)`
    (`routing_config_singleton_check`), at most one row ever, upserted via `INSERT ... ON CONFLICT
    (id) DO UPDATE`. NOT tenant-scoped — there is no `tenant_id` column, no per-tenant view. Applied
    over `Settings` at NEXT BOOT for the entire gateway (`routing_admin_router.py:1-19`'s own module
    docstring: "restart-to-apply... any owner/admin may write the operator-wide routing config").
  - **The confirmed S1 chain** (matches memory's "S1 signup→routing-takeover" verbatim): an
    anonymous caller `POST /admin/auth/signup` (zero auth, zero rate limit) -> instantly `Role.OWNER`
    of a brand-new tenant -> OWNER holds `ROUTING_MANAGE` (forced by the completeness guard above)
    -> `PUT /admin/routing` rewrites the ONE shared config every other tenant's traffic routes
    through (model aliases, weights, retry/cooldown/loadbal knobs) — two unauthenticated-adjacent
    HTTP calls, no privilege escalation needed, from a self-created account seconds old.
  - `.add/tasks/routing-config-write/TASK.md` (FROZEN @ v1, `phase: done`) — the routing-write
    surface's OWN prior ground note is the smoking gun: *"SECURITY — AUTHZ FROZEN (Tin-approved
    2026-06-23, AskUserQuestion): `PUT /admin/routing` uses `require_owner_or_admin`, **always-on**
    (no feature flag, no /ops boundary). The role model is tenant-scoped... Tin explicitly accepted
    that any owner/admin may write the operator-wide routing config — the deployment is treated as
    **single-operator/trusted-owner**... This was a HARD-STOP security freeze; Tin's selection IS the
    approval. (Rejected: default-OFF flag; /ops mTLS+XFCC boundary.)"* That decision's own stated
    premise — "single-operator/trusted-owner deployment" — is exactly what public self-signup
    breaks: the codebase has since shipped tenants + plans/billing + member-invite + a superadmin
    platform console (all merged to `main` this session per `tmp/eh-remaining-context.md`), i.e. a
    genuine multi-tenant SaaS, not a single trusted operator. **This task does not silently
    contradict that freeze — it names the stale premise and proposes an explicit SUPERSESSION**, the
    pattern PROJECT.md's own v6 fold names: "a frozen behavioral pin... is changed by the
    SUPERSESSION pattern — record the supersession at the new task's freeze, leave the frozen file
    untouched."
  - `apps/gateway/src/gateway/tenants/infrastructure/invite_repository.py`,
    `tenants/application/invite_use_cases.py` (`CreateInviteUseCase`/`ListPendingInvitesUseCase`/
    `RevokeInviteUseCase`, member-invite-issuance TASK.md, FROZEN @ v1) + `POST/GET/DELETE
    /admin/invites` (`invites_router.py`, MEMBERS_MANAGE-gated, owner/admin-of-that-tenant only) +
    `tenants/application/invite_accept_use_cases.py` + PUBLIC `GET/POST /invites/{token}[/accept]`
    (`invite_accept_router.py`, member-invite-acceptance TASK.md, FROZEN @ v1) — the SHIPPED invite
    machinery. **Critical scoping finding**: every existing invite is keyed to an EXISTING
    `tenant_id` — `CreateInviteUseCase` issues an offer to join the CALLER's OWN tenant as a
    specific role; `AcceptInviteUseCase` provisions a `UserRow` bound to THAT invite's `tenant_id`.
    Nothing in the shipped invite schema/use-cases creates a NEW tenant. So "invite-only signup"
    cannot be a thin reuse of the existing invite TOKEN machinery to gate `POST /admin/auth/signup`
    (new-tenant creation) — the two are structurally different operations (join an existing tenant
    vs. mint a new one). What DOES carry over cleanly: the existing invite flows need ZERO changes —
    they already require an existing owner/admin's consent to add anyone to a tenant; the actual gap
    is narrower and shallower than "invite-only signup" first suggests — see §1 Framings weighed.
  - `apps/gateway/src/gateway/tenants/application/users_use_cases.py:26-68`
    `assert_role_within_ceiling`/`_ADMIN_ASSIGNABLE` — the ONE shared escalation-ceiling predicate
    (member-invite-issuance TASK.md §3, FROZEN @ v1). Not directly touched by this task (neither new
    Must creates or reassigns a role), cited here only because the appsec persona's Critical Rule #1
    ("never fork a second hand-rolled escalation table") is the same discipline this task's routing
    gate must honor: REUSE `require_superadmin` verbatim, do not write a second
    `identity.role == Role.SUPERADMIN` check under a new name.
  - `apps/gateway/src/gateway/tenants/api/platform_tenants_router.py` — the ONLY existing
    superadmin-only router; READ-ONLY (`GET /admin/platform/tenants[..]`, list/view). **No superadmin
    "create tenant" (POST) endpoint exists anywhere** — confirms there is no existing ops-provisioning
    primitive to reuse for a fresh-deploy bootstrap; see §1 Assumptions.
  - `apps/gateway/migrations/versions/3fc2328e5e82_platform_tenant_seed.py` — the ONE seeded
    `kind='platform'` tenant row has **no owner user** (`name='Platform', no owner`). Combined with
    "`Role.SUPERADMIN` never assignable via `PUT /admin/users/{id}/role`"
    (`users_router.py:128`/`platform_users_router.py:178`, both pre-check-reject it) — the FIRST
    superadmin account on any deployment is, today, created out-of-band (a manual DB operation /
    ops runbook step, not via any HTTP surface). This task does not change that; it is the existing,
    accepted bootstrap story for SUPERADMIN specifically.
  - `apps/gateway/src/gateway/core/config.py:83-84` `Settings(BaseSettings)`,
    `model_config = SettingsConfigDict(env_prefix="GATEWAY_", ...)` — the `bool = False` Field
    pattern this task's new knob follows (`otel_enabled`, `oidc_enabled`, `web_search_enabled`, etc.,
    all `GATEWAY_<NAME>_ENABLED`, default `False`).
  - `charts/ai-proxy/values.yaml` (fresh-install template) / `values-prod.yaml` (THIS repo's own
    running production values) / `values-kind.yaml` — `extraEnv: []` escape hatch exists
    (`values.yaml:104-106`) for any `GATEWAY_*` knob without per-field chart templating; no existing
    boolean knob (`otelEnabled`-style) is templated explicitly in these particular files today for
    comparison, so this task's own chart change (§1 M5) sets a first precedent for this exact area —
    flagged as a design choice, not a mechanical mirror of prior art.
  - `apps/gateway/src/gateway/core/error_catalog.py:126-136` — `AUTH_CREDENTIALS_INVALID` /
    `AUTH_EMAIL_TAKEN` (409) / `AUTH_PASSWORD_WEAK` (400) live in the "tenant identity" section;
    `:83-86` `AUTH_FORBIDDEN` (403, "Insufficient role for this operation") is REUSED verbatim by
    `require_superadmin` — no new error code needed for the routing half of this task.

Context (working folder): `tmp/eh-remaining-context.md` (this session's shared brief — S1 named
  "signup→routing-takeover", HARD-STOP verify reserved for Tin); `.add/tasks/member-invite-issuance/
  TASK.md` + `.add/tasks/member-invite-acceptance/TASK.md` (FROZEN @ v1, the invite machinery this
  task must not duplicate or contradict — both fully read); `.add/tasks/routing-config-write/
  TASK.md` (FROZEN @ v1, `phase: done` — the prior HARD-STOP security freeze this task proposes to
  supersede, fully read, quoted above); `.add/PROJECT.md` (SUPERSESSION pattern, "secure-by-default
  flip" precedent, both folded lessons).

Honors (patterns / conventions):
  - REUSE the ONE shared gate rather than fork a second copy — `require_superadmin` already exists
    and already means exactly "platform-tenant-only, Role.SUPERADMIN" (appsec persona Critical
    Rule #1, mirrored from `assert_role_within_ceiling`'s own "never a second hand-rolled copy"
    precedent).
  - Cheapest/no-IO check runs FIRST, before any DB touch — member-invite-acceptance's own
    password-check-before-lock ordering (`AcceptInviteUseCase`); this task's signup-disabled check
    must run before `WeakPasswordError`/`EmailAlreadyRegisteredError`, so a disabled deployment never
    leaks "is this email taken" even incidentally.
  - `GATEWAY_<NAME>_ENABLED: bool = False` Settings knob shape (`otel_enabled`, `oidc_enabled`,
    `web_search_enabled`, `input_modality_guard_enabled` — config.py, all consistent).
  - Byte-identical reuse of an EXISTING `ErrorSpec` wherever the situation is truly the same
    (`AUTH_FORBIDDEN`, `AUTH_PASSWORD_WEAK`, `AUTH_EMAIL_TAKEN`) — mint a NEW code only where the
    situation is genuinely new (`ERR_SIGNUP_INVITE_ONLY` has no existing analog), mirroring
    member-invite-acceptance's own "reuse where identical, mint only where new" discipline
    (`ERR_INVITE_EXPIRED` was its one new code).
  - SUPERSESSION, not silent edit, for a previously frozen behavioral pin — PROJECT.md v6 fold:
    "record the supersession at the new task's freeze, leave the frozen file untouched."

Anchors the contract cites: `SignupUseCase.execute`/`router.py:signup()` (`tenants/application/
  use_cases.py:21-35`, `tenants/api/router.py:39-52`) · `Settings` (`core/config.py:83`) ·
  `require_superadmin`/`require_permission`/`Permission.ROUTING_MANAGE` (`tenants/domain/authz.py`)
  · `routing_admin_router.get_routing_admin`/`put_routing_admin` (`proxy/api/
  routing_admin_router.py:161-213`) · `AUTH_FORBIDDEN`/`AUTH_EMAIL_TAKEN`/`AUTH_PASSWORD_WEAK`
  (`core/error_catalog.py`) · `RoutingConfigRow`/`routing_config_singleton_check`
  (`proxy/infrastructure/routing_config_orm.py`).

Issues/Risks (→ feed §1):
  - **Bootstrap paradox**: `/admin/invites` (issuance) requires an EXISTING owner/admin of SOME
    tenant to call it; a brand-new deployment has zero tenants beyond the ownerless `kind='platform'`
    seed row. If `public_signup_enabled` defaults OFF from the very first boot, there is literally no
    HTTP path to create the FIRST tenant — a genuine, self-inflicted lockout, not a hypothetical.
    See §1 Assumptions ⚠.
  - **Existing-production-behavior risk**: this repo's OWN running deployment already has ≥1 tenant
    that presumably reached OWNER exactly via today's always-on public signup. Defaulting the new
    flag OFF in `values-prod.yaml` on upgrade would silently stop any NEW customer self-registration
    the moment the deploy rolls out — a genuine product/business call, not a code-only decision. See
    §1 Assumptions ⚠ (top flag).
  - **Supersession risk**: reversing a HARD-STOP-cleared, Tin-approved decision (`routing-config-write`
    §0, "Tin's selection IS the approval... Rejected: default-OFF flag") needs Tin's conscious
    re-confirmation, not an inference from "circumstances changed" alone — see §1 Assumptions ⚠.
  - **Scope-boundary risk**: `Permission.CATALOG_SYNC` gates `/internal/catalog/sync`
    (`catalog/api/deps.py:99-100`, "owner/admin/operator") over what may be a SIMILARLY global
    catalog resource — the SAME shape of defect this task fixes for routing may recur elsewhere.
    Out of THIS task's declared scope (S1 names signup + routing only); flagged forward as an
    OBSERVE spec-delta, not investigated or fixed here.
  - **Dashboard blast radius**: the `/routing` dashboard page (routing-config-write's own ground
    note: "READ-ONLY... imports only bffGet") is used today by any tenant owner/admin/operator; after
    this task, only a superadmin's JWT can successfully call `GET /admin/routing`, so that dashboard
    page will start 403ing for every non-superadmin viewer. This task's own scope is the GATEWAY API
    only (per its Scope declaration below) — the dashboard fix is a named, separate follow-up, not
    silently assumed away.

Related intent: `tmp/eh-remaining-context.md` §"S1 — signup-and-routing-authz" (task brief, HARD-STOP
  verify reserved for Tin) · `enterprise-readiness-diagnostic` memory (S1 "signup→routing-takeover"
  named as one of 4 security HARD-STOPs from the original 5-lens audit) · `.add/tasks/
  routing-config-write/TASK.md` (the frozen decision this task proposes to supersede).

Ground SHA: b0918f2 (branch `feat/enterprise-hardening`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Two independently-freezable but jointly-necessary security corrections closing the S1
  "signup→routing-takeover" chain: (A) `POST /admin/auth/signup` (the ONLY new-tenant-creation path)
  becomes gate-able behind a `GATEWAY_PUBLIC_SIGNUP_ENABLED` operator knob, default OFF for a fresh
  deployment — closing "anyone can instantly become a tenant OWNER for free"; (B) `GET`/`PUT
  /admin/routing` (the operator-wide `routing_config` singleton, shared by every tenant) moves from
  a tenant-scoped `Permission` (which the frozen RBAC matrix structurally forces onto every tenant's
  OWNER) to the existing role-only `require_superadmin` gate — closing "any tenant's owner/admin/
  operator can rewrite the ONE routing config every other tenant depends on," independent of how
  that owner got their tenant. (A) alone does not fully close the vector (an INVITED, legitimate
  tenant owner would still hold `ROUTING_MANAGE` over the shared global resource); (B) alone does not
  either (the self-signup front door would stay open even if routing itself were superadmin-only —
  and other future OWNER-held admin surfaces could reopen an analogous vector). Both are required.

Framings weighed:
  Part A — WHAT "invite-only signup" gates:
    Gate ONLY `POST /admin/auth/signup` (new-tenant self-creation) behind a new operator flag,
    leaving the shipped per-tenant member-invite (join an EXISTING tenant) completely untouched
    (CHOSEN — the shipped `invites` table/use-cases are keyed to an existing `tenant_id`; nothing in
    them creates a tenant, so they are not a fit substrate to gate signup itself; this is the
    smallest change that closes the actual named vector — self-service OWNER minting — without
    inventing new invite semantics) · build a NEW "instance/tenant-creation invite" concept
    (an operator or an existing superadmin pre-authorizes a specific new tenant/email) (REJECTED for
    THIS task — genuinely larger scope: a new table or a nullable-tenant_id reinterpretation of
    `invites`, a new use case, a new public endpoint; not what "the invite-only default can build on
    that machinery" in the shared task brief most plausibly means, and not needed to close the named
    S1 vector) · require domain-verified SSO/OIDC for all new tenants (REJECTED — orthogonal, a much
    larger identity-provider dependency, and the codebase's OIDC support is per-tenant config that
    itself presupposes a tenant already exists).
  Part A — WHAT the default should be:
    `Settings.public_signup_enabled: bool = False` (secure-by-default for the Settings class itself,
    matching every other `GATEWAY_<X>_ENABLED` knob's off-by-default convention) — but see the ⚠
    top-flagged Assumption below for whether `values-prod.yaml` (THIS repo's own live deployment)
    ships `true` (preserve current behavior across the upgrade) or `false` (close the BLOCKER
    immediately, accept a behavior change) — a product call this draft does NOT resolve unilaterally.
  Part A — WHERE the check runs:
    First, before `WeakPasswordError`/`EmailAlreadyRegisteredError`, zero DB IO when disabled
    (CHOSEN — cheapest-check-first precedent, and prevents any incidental email-enumeration signal
    leaking through a disabled endpoint) · after the existing validation, so a disabled deployment
    still surfaces "your password was too weak" style feedback (REJECTED — inconsistent with the
    project's anti-enumeration discipline elsewhere, and pointless: the caller cannot proceed either
    way).
  Part B — HOW to gate the operator-wide routing surface:
    Swap `require_permission(Permission.ROUTING_MANAGE)` for the EXISTING `require_superadmin` on
    both `GET`/`PUT /admin/routing` (CHOSEN — zero new symbol, zero touch to the FROZEN `Permission`/
    `ROLE_PERMISSIONS` matrix, reuses the exact precedent `platform_tenants_router.py` already
    established for "no single target_tenant_id, not tenant-scoped -> require_superadmin, not a
    Permission") · mint a NEW `Permission.ROUTING_WRITE` enum member (REJECTED — structurally
    impossible to scope away from tenant OWNER without also editing the frozen `ROLE_PERMISSIONS[
    Role.OWNER] == frozenset(Permission)` completeness guard, since OWNER is defined as holding
    LITERALLY EVERY Permission that exists; a "tenant-excluding Permission" is a contradiction in
    the current matrix, not an achievable additive amendment — see the ⚠ Assumption below, which
    surfaces this reframe explicitly for Tin rather than silently substituting one gate for another
    under the task's literal "permission" wording) · gate via `require_permission(ROUTING_MANAGE) +
    an inline `identity.tenant_id == <the platform tenant's id>` check (REJECTED as the primary
    choice, kept as the documented fallback in the ⚠ Assumption below — functionally equivalent to
    require_superadmin but reimplements a check that already exists under a different name, and adds
    an extra DB/identity lookup for no behavioral gain) · leave `ROUTING_MANAGE` as-is and add a
    NEW, SEPARATE `/ops`-boundary (mTLS+XFCC) restriction (REJECTED — this exact option was already
    considered and rejected at the PRIOR freeze, `routing-config-write` §0: "Rejected:... /ops
    mTLS+XFCC boundary" — re-litigating it here would be scope creep beyond what closes S1).
  Part B — WHETHER GET moves too, not just PUT:
    Move both GET and PUT (CHOSEN, but flagged as MY OWN reasoned extension beyond the task's
    literal "routing-write permission" wording — see ⚠ Assumption) — a resource not meant to be
    globally writable by every tenant's admin is also not obviously meant to be globally READABLE by
    them (model aliases/weights/retry/cooldown knobs are internal ops detail, not a customer-facing
    billing or usage fact); restricting only PUT while leaving GET wide open is an inconsistent half
    -measure and the dashboard's `/routing` page was already documented as read-only, so the
    practical loss for legitimate tenant admins is symmetric either way · restrict PUT only, leave
    GET on `ROUTING_MANAGE` (the literal, narrower reading — kept as the reversible alternative if
    Tin disagrees; a one-line diff either way, does not reshape anything else in this contract).
Must:
<must>
  - **[M1]** NEW `Settings.public_signup_enabled: bool = False` (`GATEWAY_PUBLIC_SIGNUP_ENABLED`,
    `core/config.py`, sibling to `otel_enabled`/`oidc_enabled`) — the ONE source of truth for whether
    `POST /admin/auth/signup` may create a new tenant.
  - **[M2]** `signup()` (`tenants/api/router.py`) checks `request.app.state.settings.
    public_signup_enabled` FIRST — before any password-strength check, before any DB read/write.
    `False` -> 403 `ERR_SIGNUP_INVITE_ONLY`, zero rows touched, zero use-case invocation.
  - **[M3]** `public_signup_enabled == True` -> `signup()` is BYTE-IDENTICAL to its current shipped
    behavior in every observable respect (request shape, success shape, `WeakPasswordError` ->  400,
    `EmailAlreadyRegisteredError` -> 409, both reused verbatim) — this task adds a gate in front of
    the existing use case, it does not alter the use case itself.
  - **[M4]** The existing invite issuance (`POST/GET/DELETE /admin/invites`) and invite acceptance
    (`GET/POST /invites/{token}[/accept]`) endpoints are COMPLETELY UNCHANGED by this task and by
    the new flag's value in either direction — joining an EXISTING tenant via invite has never
    depended on, and continues to not depend on, whether new-tenant self-signup is enabled.
  - **[M5]** Chart default split (an explicit, operator-visible, non-silent choice — NOT buried in
    `extraEnv`): `charts/ai-proxy/values.yaml` (the fresh-install template) sets
    `gateway.publicSignupEnabled: false` with an inline comment explaining the invite-only default
    and pointing at the one-time bootstrap step (M6); `charts/ai-proxy/values-prod.yaml` (THIS
    repo's own already-running production values) sets it per the ⚠ Assumption below — Tin's call at
    freeze, not a code default this draft picks unilaterally.
  - **[M6]** Documented one-time bootstrap path for a deployment that starts with the flag OFF:
    the operator sets `GATEWAY_PUBLIC_SIGNUP_ENABLED=true` for the FIRST boot only, creates the
    first tenant via the existing (unmodified) `POST /admin/auth/signup`, then sets it back to
    `false` — a runbook/README note, zero new code path. (A dedicated bootstrap CLI is a larger,
    separate follow-up — see §1 Assumptions.)
  - **[M7]** `GET /admin/routing` and `PUT /admin/routing` (`proxy/api/routing_admin_router.py`) both
    switch their FastAPI dependency from `require_permission(Permission.ROUTING_MANAGE)` to the
    EXISTING `require_superadmin` (`tenants/domain/authz.py`) — zero new symbol, zero new Permission,
    zero edit to the FROZEN `Permission`/`ROLE_PERMISSIONS` matrix in `authz.py`.
  - **[M8]** `Permission.ROUTING_MANAGE` remains declared in the `Permission` enum and in every
    role's `ROLE_PERMISSIONS` entry, UNCHANGED — this task does not edit, remove, or repurpose it
    (it becomes unused/dead at its only 2 former call sites; flagged forward at §7 OBSERVE, not
    acted on here — removing an enum member is itself a frozen-matrix edit this task does not need
    and should not casually take on).
  - **[M9]** This is an explicit, named SUPERSESSION of `routing-config-write` TASK.md's frozen
    decision ("any owner/admin may write the operator-wide routing config, always-on, single-
    operator/trusted-owner assumption," Tin-approved 2026-06-23 HARD-STOP) — recorded here per
    PROJECT.md's SUPERSESSION pattern; that file is left untouched, its `phase: done` and Status
    line unedited.
  - **[M10]** Both routes' rejection shapes are reused verbatim: missing/invalid bearer ->
    `ERR_AUTH_INVALID_TOKEN` (401, unchanged); caller authenticated but not SUPERADMIN ->
    `ERR_AUTH_FORBIDDEN` (403) — `require_superadmin` already raises this exact `ErrorSpec`, so this
    is truly a zero-new-error-code change for Part B.
  - **[M11]** `routing_config`'s schema, `RoutingConfigRepository.get/upsert`, `merge_routing_config`,
    the boot-merge/restart-to-apply behavior, and the PUT validation round-trip (Settings/Deployment
    validator parity, `ROUTING_CONFIG_INVALID` 422) are ALL unchanged — this task edits ONLY the
    `Depends(...)` on 2 route functions; nothing about persistence, validation, or the response shape
    moves.
</must>
Reject:
<reject>
  - **[R1]** `public_signup_enabled == False` and any signup attempt (valid or invalid body alike)
    -> "ERR_SIGNUP_INVITE_ONLY" (403, NEW) — checked before password/email validation, zero DB IO
  - **[R2]** `public_signup_enabled == True` and `password` shorter than `MIN_PASSWORD_LENGTH` ->
    "ERR_AUTH_PASSWORD_WEAK" (400, reused verbatim, unchanged from today)
  - **[R3]** `public_signup_enabled == True` and `email` already registered -> "ERR_AUTH_EMAIL_TAKEN"
    (409, reused verbatim, unchanged from today)
  - **[R4]** `GET`/`PUT /admin/routing`: missing/invalid bearer JWT -> "ERR_AUTH_INVALID_TOKEN" (401,
    reused, unchanged)
  - **[R5]** `GET`/`PUT /admin/routing`: caller's role is anything other than SUPERADMIN (OWNER,
    ADMIN, OPERATOR, BILLING_ADMIN, VIEWER, MEMBER all now rejected — this is the deliberate,
    named regression relative to the prior freeze) -> "ERR_AUTH_FORBIDDEN" (403, reused)
  - **[R6]** `PUT /admin/routing`: caller IS SUPERADMIN but the body fails Settings/Deployment
    validator parity -> "ERR_ROUTING_CONFIG_INVALID" / the specific validator code (422, reused,
    unchanged — R6 exists today, restated here only for completeness of the frozen shape)
</reject>
After:
<after>
  - Signup, disabled: no `TenantRow`, no `UserRow` created; no password/email validation performed;
    the response is 403 `ERR_SIGNUP_INVITE_ONLY`; the existing `invites`/`users`/`tenants` tables are
    completely unchanged.
  - Signup, enabled: byte-for-byte identical outcome to today's shipped behavior for every input.
  - Routing, non-superadmin caller (any of the other 6 roles, any tenant): the `routing_config`
    singleton is completely unchanged; the response is 403 `ERR_AUTH_FORBIDDEN`; no audit event is
    written for the rejected attempt (the existing `routing.update` audit event only ever fired on
    the PUT success path, unchanged).
  - Routing, superadmin caller: byte-for-byte identical outcome (response shape, persistence,
    `routing.update` audit event) to today's shipped `ROUTING_MANAGE`-gated behavior — only WHO may
    reach that behavior changed, not what the behavior itself does.
  - The named S1 chain is closed end-to-end: a caller who self-signs-up (even during the M6
    bootstrap window) can never subsequently `PUT /admin/routing`, because OWNER (or any non-
    SUPERADMIN role) no longer passes `require_superadmin` regardless of how their tenant/account
    came to exist.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **`values-prod.yaml`'s default for `publicSignupEnabled` (true = preserve current prod signup
    availability across the upgrade; false = close the BLOCKER immediately, accept an intentional
    breaking change to a live capability)** — lowest confidence because this is a product/business
    call this draft cannot resolve from code alone: I have no visibility into whether THIS repo's
    real production deployment currently relies on public self-serve signup as a working
    customer-acquisition path, or whether it is inconsequential/already superseded by invites in
    practice. If wrong: a ONE-LINE `values-prod.yaml` flip either direction — zero code change,
    zero contract-shape change — but picking wrong risks either (a) leaving a live, diagnostic-named
    security BLOCKER open in production if defaulted `true`, or (b) silently breaking a working
    signup funnel with no warning if defaulted `false` without Tin's explicit sign-off. This MUST be
    Tin's own choice at freeze, not inferred.
  ⚠ **Superseding `routing-config-write`'s HARD-STOP-cleared, Tin-approved "any owner/admin,
    always-on" decision** — lowest confidence because reversing a PRIOR, direct AskUserQuestion
    outcome (not merely filling an unaddressed gap) needs Tin's conscious re-confirmation that the
    stale premise ("single-operator/trusted-owner deployment") is indeed what changed, rather than
    some other still-valid reason for the original call that this draft has not surfaced (e.g. an
    existing internal ops workflow where a tenant-scoped OPERATOR legitimately tunes routing without
    superadmin access — no such workflow was found in code, but absence-of-evidence is not proof).
    If wrong: fall back to the documented alternative in §1 Framings weighed — keep
    `require_permission(ROUTING_MANAGE)` but AND it with an inline `identity.tenant_id == <the
    platform tenant id>` check (functionally equivalent to `require_superadmin`, more code, same
    external shape/error codes) — a Build-time substitution, not a contract-shape change.
  ⚠ **The "invite-only signup" reading itself** — gating ONLY `POST /admin/auth/signup` (new-tenant
    creation) vs. building a new tenant-creation-invite concept — lowest confidence because it is
    this draft's own interpretation of an intentionally terse task brief ("the invite-only default
    can build on that machinery"), not a verbatim instruction. If wrong (Tin wants a genuine
    "someone must specifically invite you to create a brand-new tenant" flow): this is materially
    larger scope — a new table or schema reinterpretation, a new use case, a new public endpoint —
    a change request back to Specify, not a small revision of what is drafted here.
  - [ ] Chart precedent for M5's explicit `values.yaml` field (vs. the `extraEnv` escape hatch) —
    no existing boolean knob in this area is templated explicitly for direct comparison; chosen
    because a security-relevant default deserves to be visible in the file operators actually read,
    not buried in a generic list — low-stakes, reversible (an `extraEnv` entry works identically at
    runtime; this is a documentation/visibility preference, not a behavioral difference).
  - [ ] GET (not just PUT) moving to `require_superadmin` — see §1 Framings weighed Part B; low
    confidence in the sense that it goes beyond the task's literal "routing-write permission"
    wording, but low STAKES (strictly more restrictive, a one-line revert to `ROUTING_MANAGE` for
    GET only if Tin prefers the narrower reading — nothing else in this contract depends on which
    way GET goes).
  - [ ] `CATALOG_SYNC`/`/internal/catalog/sync` possibly sharing the SAME defect shape (a global
    resource gated by a tenant-scoped Permission that every tenant's OWNER structurally holds) is
    named in §0 Issues/Risks but NOT investigated or fixed here — out of S1's declared scope
    (signup + routing only); flagged forward as an OBSERVE spec-delta candidate.
  - [ ] No new rate limit on `POST /admin/auth/signup` itself (today it has none, unlike invite
    accept's per-IP limiter) — out of this task's declared scope; the invite-only default flag is
    the closure mechanism for the self-signup vector, not a rate limit; flagged forward, not acted
    on, since adding one is a legitimate but separate defense-in-depth layer.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the
     top one or two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Public signup is rejected while invite-only (flag off)                          # M1, M2, R1
  Given GATEWAY_PUBLIC_SIGNUP_ENABLED=false
  When a client POSTs /admin/auth/signup {tenant_name: "Acme", email: "new@acme.io", password: "correct horse battery staple"}
  Then the response is 403 "ERR_SIGNUP_INVITE_ONLY"
  And no tenants row and no users row was created
  And the use case (SignupUseCase.execute) was never invoked — the DB was never queried or written

Scenario: The invite-only rejection runs before any input validation (no incidental leak)  # M2
  Given GATEWAY_PUBLIC_SIGNUP_ENABLED=false, and a real user "existing@acme.io" already exists in some tenant
  When a client POSTs /admin/auth/signup {tenant_name: "X", email: "existing@acme.io", password: "short"}
  Then the response is 403 "ERR_SIGNUP_INVITE_ONLY" — NEVER 409 "ERR_AUTH_EMAIL_TAKEN" and NEVER 400
    "ERR_AUTH_PASSWORD_WEAK", regardless of how invalid the body is
  And no email-uniqueness query and no password-strength check ran

Scenario: Public signup succeeds, byte-identical to today, when explicitly enabled          # M3
  Given GATEWAY_PUBLIC_SIGNUP_ENABLED=true
  When a client POSTs /admin/auth/signup {tenant_name: "Acme", email: "new@acme.io", password: "correct horse battery staple"}
  Then the response is 201 SignupResponse {tenant_id, user_id} — identical shape to pre-change signup
  And exactly one new tenants row and one new users row (role=owner) exist

Scenario: Weak password is still rejected when signup is enabled (regression)               # R2
  Given GATEWAY_PUBLIC_SIGNUP_ENABLED=true
  When a client POSTs /admin/auth/signup {tenant_name: "Acme", email: "new@acme.io", password: "short"}
  Then the response is 400 "ERR_AUTH_PASSWORD_WEAK" — unchanged from today
  And no tenants/users row was created

Scenario: Duplicate email is still rejected when signup is enabled (regression)             # R3
  Given GATEWAY_PUBLIC_SIGNUP_ENABLED=true, and "taken@acme.io" is already registered
  When a client POSTs /admin/auth/signup {tenant_name: "Acme2", email: "taken@acme.io", password: "correct horse battery staple"}
  Then the response is 409 "ERR_AUTH_EMAIL_TAKEN" — unchanged from today
  And no NEW tenants/users row was created

Scenario: Existing member-invite issuance is unaffected by invite-only signup being off     # M4
  Given GATEWAY_PUBLIC_SIGNUP_ENABLED=false, and a logged-in OWNER of an EXISTING tenant T
  When they POST /admin/invites {email: "colleague@t.io", role: "member"} (the shipped, frozen flow)
  Then the response is 201, exactly as member-invite-issuance's own frozen contract describes —
    completely unaffected by the new signup flag

Scenario: Existing invite acceptance is unaffected by invite-only signup being off          # M4
  Given GATEWAY_PUBLIC_SIGNUP_ENABLED=false, and a pending invite for tenant T with token X
  When a client POSTs /invites/X/accept {password: "correct horse battery staple"}
  Then the response is 200, exactly as member-invite-acceptance's own frozen contract describes —
    completely unaffected by the new signup flag

Scenario: Fresh-deploy bootstrap — flip on, create the first tenant, flip back off          # M6
  Given a brand-new deployment with GATEWAY_PUBLIC_SIGNUP_ENABLED=false from first boot (no tenant
    exists yet beyond the ownerless 'Platform' seed row)
  When the operator temporarily sets GATEWAY_PUBLIC_SIGNUP_ENABLED=true, a client POSTs
    /admin/auth/signup successfully (201, first customer tenant created), and the operator then sets
    the flag back to false
  Then a SECOND signup attempt after the flag is reset returns 403 "ERR_SIGNUP_INVITE_ONLY"
  And the first tenant's OWNER can still log in and use the gateway normally (login/JWT issuance is
    completely untouched by this task)

Scenario: A superadmin reads and writes routing config exactly as before (regression)       # M7, M10, M11
  Given a logged-in SUPERADMIN (platform-tenant-only)
  When they GET /admin/routing, then PUT /admin/routing {model_groups: {...valid...}}
  Then GET returns 200 with the same shape as today (retry_policy, cooldown, model_groups,
    candidates, routing_strategy, deployments)
  And PUT returns 200, persists via the SAME RoutingConfigRepository.upsert, and fires the SAME
    "routing.update" audit event — byte-identical to pre-change behavior for this role

Scenario: A tenant OWNER can no longer read the operator-wide routing config (the named fix) # M7, R5
  Given a logged-in OWNER of an ordinary customer tenant (NOT superadmin)
  When they GET /admin/routing
  Then the response is 403 "ERR_AUTH_FORBIDDEN" — a deliberate regression from today's ROUTING_MANAGE
    -gated 200
  And the routing_config table is untouched (a read attempt, nothing to change anyway)

Scenario: A tenant OWNER can no longer write the operator-wide routing config (closes S1)    # M7, R5
  Given a logged-in OWNER of an ordinary customer tenant (NOT superadmin)
  When they PUT /admin/routing {model_groups: {"chat": ["attacker-controlled-model"]}}
  Then the response is 403 "ERR_AUTH_FORBIDDEN"
  And the routing_config singleton row is COMPLETELY UNCHANGED — no partial write, no audit event
  And this holds even if that OWNER's tenant was just self-created seconds earlier during the M6
    bootstrap window — the block is role-based (SUPERADMIN only), not history-based

Scenario: ADMIN and OPERATOR roles (previously ROUTING_MANAGE-holding) are also now rejected  # M7, R5
  Given a logged-in ADMIN of tenant T, and separately a logged-in OPERATOR of tenant T
  When each GETs and PUTs /admin/routing
  Then every response is 403 "ERR_AUTH_FORBIDDEN" for both roles, both verbs
  And the routing_config table is untouched by either attempt

Scenario: Missing or invalid bearer token is still rejected for routing, unchanged           # R4
  Given no Authorization header (or an invalid/expired one)
  When a client GETs or PUTs /admin/routing
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN" — unchanged from today

Scenario: A superadmin's invalid routing body is still rejected by validator parity (regression) # R6
  Given a logged-in SUPERADMIN
  When they PUT /admin/routing {model_groups: {"chat": []}}  (an empty candidate list)
  Then the response is 422 with the SAME "EMPTY_CANDIDATE_LIST"-derived code as today
  And nothing is persisted — unchanged from routing-config-write's own frozen validator behavior

Scenario: The full S1 chain is provably closed end-to-end                                    # M2, M7 (integration)
  Given GATEWAY_PUBLIC_SIGNUP_ENABLED=true (bootstrap window, or an operator who left it on)
  When an anonymous caller signs up (201, becomes OWNER of a brand-new tenant), then immediately
    attempts PUT /admin/routing {model_groups: {"chat": ["attacker-model"]}} with that new OWNER's
    freshly-issued JWT
  Then the PUT response is 403 "ERR_AUTH_FORBIDDEN" — the routing_config singleton is unreachable to
    this caller regardless of the signup flag's state, closing the named diagnostic vector
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/auth/signup                              (PUBLIC — no auth; NEW gate: Settings.public_signup_enabled)
  body: SignupRequest { tenant_name: str, email: EmailStr, password: str }        # UNCHANGED
  201 -> SignupResponse { tenant_id: uuid, user_id: uuid }                        # UNCHANGED, only reachable when enabled
  400 -> { code: "ERR_AUTH_PASSWORD_WEAK" }        # R2 — reused, unchanged, only reachable when enabled
  403 -> { code: "ERR_SIGNUP_INVITE_ONLY" }        # R1 — NEW; checked FIRST, zero DB IO, regardless of body validity
  409 -> { code: "ERR_AUTH_EMAIL_TAKEN" }          # R3 — reused, unchanged, only reachable when enabled

GET /admin/routing                                    (AUTH: require_superadmin — SUPERSEDES Permission.ROUTING_MANAGE, M7/M9)
  200 -> (UNCHANGED body shape: retry_policy, cooldown, model_groups, candidates, routing_strategy, deployments)
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }        # R4
  403 -> { code: "ERR_AUTH_FORBIDDEN" }            # R5 — NOW fires for OWNER/ADMIN/OPERATOR/BILLING_ADMIN/VIEWER/MEMBER of every tenant

PUT /admin/routing                                    (AUTH: require_superadmin — SUPERSEDES Permission.ROUTING_MANAGE, M7/M9)
  body: (UNCHANGED — model_groups + ROUTING_OVERRIDE_KEYS)
  200 -> (UNCHANGED body shape, same as GET's effective-config render)
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }        # R4
  403 -> { code: "ERR_AUTH_FORBIDDEN" }            # R5 — the closed S1 vector
  422 -> { code: "ERR_ROUTING_CONFIG_INVALID" | "<VALIDATOR_CODE>" }   # R6 — reused, unchanged

Schema:
  NO migration for `routing_config` — table, columns, singleton constraint all byte-for-byte
    unchanged (M11).
  NEW `Settings` field (`core/config.py`, additive, no migration — Settings is not persisted):
    public_signup_enabled: bool = False   # GATEWAY_PUBLIC_SIGNUP_ENABLED (M1)

Access pattern:
  SIGNUP (tenants/api/router.py:signup, MODIFIED — one new guard clause, use case body untouched):
    1. IF NOT request.app.state.settings.public_signup_enabled:
         raise SIGNUP_INVITE_ONLY.exc()   -- BEFORE constructing/calling SignupUseCase; zero DB IO (M2)
    2. (UNCHANGED) SignupUseCase.execute(...) -> WeakPasswordError (400) | EmailAlreadyRegisteredError
       (409) | success (201) — byte-identical to today (M3)

  ROUTING ADMIN (proxy/api/routing_admin_router.py, MODIFIED — dependency swap only, 2 lines):
    get_routing_admin(request, _identity: Annotated[Identity, require_superadmin])          # was require_permission(Permission.ROUTING_MANAGE)
    put_routing_admin(request, body, identity: Annotated[Identity, require_superadmin])     # was require_permission(Permission.ROUTING_MANAGE)
    Every line of body logic (validation, persistence, audit emit, response render) is UNCHANGED.

NEW error_catalog.py entry (sibling to AUTH_EMAIL_TAKEN/AUTH_PASSWORD_WEAK, core/error_catalog.py
  "tenant identity" section, ~line 136):
  SIGNUP_INVITE_ONLY = ErrorSpec(403, "ERR_SIGNUP_INVITE_ONLY",
      "Public signup is disabled; ask an existing member for an invite")
  (REUSED, not new: AUTH_FORBIDDEN · AUTH_TOKEN_MISSING · AUTH_TOKEN_INVALID · AUTH_PASSWORD_WEAK ·
   AUTH_EMAIL_TAKEN · ROUTING_CONFIG_INVALID — every other rejection in this contract is verbatim.)

NEW Settings field (core/config.py, sibling to otel_enabled/oidc_enabled — same GATEWAY_<X>_ENABLED
  bool-default-False shape):
  public_signup_enabled: bool = False   # GATEWAY_PUBLIC_SIGNUP_ENABLED

NO new Permission, NO edit to Permission or ROLE_PERMISSIONS (tenants/domain/authz.py) — the FROZEN
  rbac-roles matrix (FROZEN @ v1) is untouched by this contract (M7, M8, M9). `Permission.
  ROUTING_MANAGE` remains declared, now unused at its former 2 call sites — see §7 OBSERVE.

Chart changes (additive, no schema/migration involved):
  charts/ai-proxy/values.yaml:        gateway.publicSignupEnabled: false   # NEW, fresh-install default (M5)
  charts/ai-proxy/values-prod.yaml:   gateway.publicSignupEnabled: <Tin's call — see ⚠ Assumption>   # M5
  Both map to GATEWAY_PUBLIC_SIGNUP_ENABLED via the existing env-templating convention (no NEW
  chart mechanism needed).

SUPERSESSION record (M9): `routing-config-write` TASK.md §0's frozen decision ("any owner/admin may
  write the operator-wide routing config, always-on, single-operator/trusted-owner assumption,"
  Tin-approved 2026-06-23 HARD-STOP, explicitly rejecting a default-OFF flag and an /ops boundary) is
  SUPERSEDED by this task's M7/M9 for the stated reason: the "single-operator/trusted-owner
  deployment" premise no longer holds now that tenants/plans/billing/member-invite/platform-console
  all ship on `main`. That file is left completely unedited — this TASK.md is the record of the
  supersession, per PROJECT.md's SUPERSESSION pattern.
```

Glossary deltas:
  - `public signup` (NEW term): the unauthenticated `POST /admin/auth/signup` new-tenant-creation
    path, gated by the operator knob `GATEWAY_PUBLIC_SIGNUP_ENABLED` (default OFF). Distinct from
    `invite (pending invite)` (existing GLOSSARY term) — an invite always targets an EXISTING
    tenant + role; public signup always MINTS a brand-new tenant. The two are independent
    capabilities that happen to both result in a new `UserRow`.
  - `operator-wide` (NEW term, retroactively naming an existing shape): a resource with no
    `tenant_id` at all — a true platform-level singleton (today: only `routing_config`) — as
    distinct from `platform-tenant-only`/superadmin-scoped resources that DO have a natural
    `tenant_id` (e.g. `platform_tenants_router`'s per-tenant GET). An operator-wide resource is
    gated by `require_superadmin` (role-only), never by a tenant-scoped `Permission` — the frozen
    `ROLE_PERMISSIONS[Role.OWNER] == frozenset(Permission)` completeness guard makes a
    tenant-excluding `Permission` structurally impossible, so `Permission` is the wrong tool for
    any future operator-wide surface, not just this one (see §7 OBSERVE `CATALOG_SYNC` flag).

Status: FROZEN @ v1 — Tin approved 2026-07-10 (AskUserQuestion). All three flags RESOLVED:
  ✅ [spec] `values-prod.yaml`'s `publicSignupEnabled` — RESOLVED: Tin chose invite-only ON by default,
    prod INCLUDED. `publicSignupEnabled: false` in BOTH `values.yaml` and `values-prod.yaml`; the BLOCKER
    is closed in prod at deploy. A documented bootstrap step (M6 flip-on/off) prevents a fresh deploy
    bricking with no path to a first tenant. `GATEWAY_PUBLIC_SIGNUP_ENABLED` code default = OFF.
  ✅ [contract] Routing gate — RESOLVED: Tin chose `require_superadmin` (role-only gate, reused
    verbatim). This SUPERSEDES the 2026-06-23 `routing-config-write` HARD-STOP-cleared "any owner/admin,
    always-on" decision (stale single-operator premise). Record as a named SUPERSESSION (PROJECT.md fold
    pattern) — do NOT silently edit the frozen routing-config-write file.
  ✅ [spec] "invite-only signup = gate only POST /admin/auth/signup, leave existing per-tenant invites
    untouched" — RESOLVED: the narrow reading is confirmed (no new tenant-creation-invite concept in v1
    scope). The shipped tenant-scoped invites are untouched.
SECURITY task: the VERIFY gate remains Tin's HARD-STOP (this freeze authorizes build, not verify).

Least-sure flag surfaced at freeze: [contract] the routing gate is `require_superadmin` (role-only),
SUPERSEDING the 2026-06-23 "any owner/admin, always-on" routing-config-write decision — the biggest
blast-radius call: it makes /admin/routing unreachable to every non-superadmin role and requires
reconciling the routing-config-write + routing-admin + rbac_roles suites that asserted the old
behavior. Cost if wrong: a legitimate operator workflow that relied on owner/admin routing writes
breaks. [spec] prod ships invite-only (`publicSignupEnabled: false` in values-prod.yaml) — a fresh
deploy cannot self-serve its first tenant without the documented M6 flip-on/off bootstrap. Cost if
wrong: a new prod deploy bricks with no path to a first tenant until the operator runs the bootstrap.

<!-- The freeze IS the one approval — Tin's decision, never this draft's. Approved -> Status: FROZEN
     @ vN — approved by <name>. Changing a frozen contract = change request back to SPECIFY. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (the 2 modified route functions in `routing_admin_router.py` + the 1 new guard
  clause in `tenants/api/router.py:signup` + the 1 new `Settings` field's default/override wiring)

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_signup_rejected_invite_only: arrange GATEWAY_PUBLIC_SIGNUP_ENABLED=false / act POST
    /admin/auth/signup {valid body} / assert 403 + code == ERR_SIGNUP_INVITE_ONLY + assert no
    tenants/users row created · covers: M1, M2, R1
  - test_signup_invite_only_checked_before_validation: arrange flag=false + an email that IS
    already registered elsewhere + a weak password (both would independently 400/409 if enabled) /
    act POST /admin/auth/signup / assert 403 ERR_SIGNUP_INVITE_ONLY specifically (never 400/409) ·
    covers: M2
  - test_signup_succeeds_when_enabled: arrange flag=true / act POST /admin/auth/signup {valid} /
    assert 201 + SignupResponse shape + one new tenants/users row · covers: M3
  - test_signup_weak_password_still_rejected_when_enabled: arrange flag=true / act POST {password:
    "short"} / assert 400 ERR_AUTH_PASSWORD_WEAK (regression) · covers: R2
  - test_signup_duplicate_email_still_rejected_when_enabled: arrange flag=true + existing email /
    act POST / assert 409 ERR_AUTH_EMAIL_TAKEN (regression) · covers: R3
  - test_invite_issuance_unaffected_by_signup_flag: arrange flag=false + an existing tenant/owner /
    act POST /admin/invites {valid} / assert 201 (regression, unaffected) · covers: M4
  - test_invite_acceptance_unaffected_by_signup_flag: arrange flag=false + a pending invite / act
    POST /invites/{token}/accept {valid} / assert 200 (regression, unaffected) · covers: M4
  - test_bootstrap_flip_on_then_off: arrange flag=false, no tenants / act flip flag true, signup
    (201), flip flag false, signup again / assert 2nd signup is 403 ERR_SIGNUP_INVITE_ONLY and the
    first tenant's owner can still login (200 LoginResponse) · covers: M6
  - test_superadmin_reads_routing_unchanged: arrange a superadmin identity / act GET /admin/routing
    / assert 200 + unchanged body shape (regression) · covers: M7, M10, M11
  - test_superadmin_writes_routing_unchanged: arrange a superadmin identity / act PUT /admin/routing
    {valid} / assert 200 + persisted via RoutingConfigRepository + routing.update audit event fired
    (regression) · covers: M7, M10, M11
  - test_tenant_owner_cannot_read_routing: arrange a tenant OWNER identity (not superadmin) / act
    GET /admin/routing / assert 403 ERR_AUTH_FORBIDDEN · covers: M7, R5
  - test_tenant_owner_cannot_write_routing: arrange a tenant OWNER identity / act PUT /admin/routing
    {valid} / assert 403 ERR_AUTH_FORBIDDEN + assert routing_config row completely unchanged (compare
    before/after) + assert no routing.update audit event fired · covers: M7, R5
  - test_admin_and_operator_cannot_access_routing: arrange ADMIN then OPERATOR identities / act GET
    and PUT for each / assert 403 ERR_AUTH_FORBIDDEN in all 4 combinations · covers: M7, R5
  - test_routing_missing_token_still_401: arrange no Authorization header / act GET and PUT
    /admin/routing / assert 401 ERR_AUTH_INVALID_TOKEN for both (regression) · covers: R4
  - test_superadmin_invalid_routing_body_still_422: arrange a superadmin identity / act PUT
    /admin/routing {model_groups: {"chat": []}} / assert 422 + validator code + nothing persisted
    (regression) · covers: R6
  - test_s1_chain_closed_end_to_end: arrange flag=true / act signup (201, new OWNER JWT), then PUT
    /admin/routing with that SAME JWT / assert the PUT is 403 ERR_AUTH_FORBIDDEN (integration proof
    the named vector is closed) · covers: M2, M7 (integration)
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/core/config.py` (add `public_signup_enabled: bool = False`)
  `apps/gateway/src/gateway/tenants/api/router.py` (add the flag-check guard clause in `signup()`)
  `apps/gateway/src/gateway/core/error_catalog.py` (add `SIGNUP_INVITE_ONLY` ErrorSpec)
  `apps/gateway/src/gateway/proxy/api/routing_admin_router.py` (swap `require_permission(Permission.
    ROUTING_MANAGE)` -> `require_superadmin` on both routes; update the module docstring's stale
    "any owner/admin" claim to reflect the new gate; drop the now-unused `Permission` import if
    nothing else in the file needs it)
  `charts/ai-proxy/values.yaml` (add `gateway.publicSignupEnabled: false` + comment)
  `charts/ai-proxy/values-prod.yaml` (add `gateway.publicSignupEnabled: <Tin's freeze decision>`)
  `apps/gateway/tests/signup_and_routing_authz/` (this task's own test directory)
Strategy (ordered batches): 1. Settings field + error_catalog entry (no behavior change yet, pure
  additions) 2. signup() guard clause + its tests (Part A complete, independently green) 3.
  routing_admin_router.py dependency swap + its tests (Part B complete, independently green) 4.
  chart values + the end-to-end S1-closure integration test 5. regression sweep: re-run
  `tests/rbac_roles/`, `tests/routing_admin/`, `tests/routing_config_write/`,
  `tests/member_invite_issuance/`, `tests/member_invite_acceptance/` (or their current directory
  names) to confirm zero unintended blast radius beyond the named, deliberate R5 regression.
Known-problem fixes: routing_admin_router.py's own module docstring (lines 1-19) currently asserts
  "any owner/admin may write the operator-wide routing config" — this MUST be updated in the same
  change, not left stale (mirrors PROJECT.md's own SDD-fold lesson about contract prose drifting
  from the real gate).

Persona (optional): appsec-engineer (`.add/personas/appsec-engineer.md`) — this build is exactly its
  named domain: privilege-boundary correctness, defense-in-depth, reusing the ONE shared ceiling/gate
  predicate rather than forking a second copy.
Known-problem fixes: <filled above>
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): the routing-admin dependency swap and the signup guard clause are
  each a single, small, reviewable diff — no batched/combined edit that could hide one inside the
  other; each must independently pass its own regression sweep before the next batch starts.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full gateway suite green (2703 collected: 2686 prior baseline + 17 new S1);
      70 transient failures during build were all reconciled (54 signup-flag flips + 16 routing-authz
      supersession updates), re-run to zero.
- [x] coverage did not decrease — new S1 suite (17 tests) adds coverage of the two modified route
      functions + the signup guard; no source deleted.
- [x] no test or contract was altered during build — the frozen S1 contract is untouched; the
      routing_admin/routing_config_write/rbac_roles edits are the CONTRACTED M9 supersession
      (owner/admin → superadmin), not weakening.
- [x] the green was EARNED, not gamed — I reverted the fix and confirmed the 5 authz tests fail
      against the buggy form; an independent add-verify agent (aa0fd777) reproduced every role's
      status from real code. HARD-STOP reserved for Tin — recorded below.
- [x] concurrency / timing safe — routing_config upsert path is byte-unchanged; only the auth gate
      was swapped (a dependency, no new concurrency surface). Verifier confirmed.
- [x] no exposed secrets, injection openings, or unexpected dependencies — SIGNUP_INVITE_ONLY carries
      a static non-secret string; guard adds no IO before the deny.
- [x] layering & dependencies follow CONVENTIONS.md — `Depends(require_superadmin)` matches the
      repo's 20-site convention; port/adapter boundaries untouched.
- [x] a person reviewed and approved the change — Tin, 2026-07-10 (HARD-STOP gate approved).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] a self-signed-up OWNER's JWT cannot successfully PUT /admin/routing under any signup-flag
      state — confirmed by `test_s1_chain_closed_end_to_end` + verifier's independent real-code probe.
- [x] `public_signup_enabled=false` produces ZERO DB queries on a signup attempt — the guard is the
      first statement in `signup()`, before the use case is constructed; `test_signup_invite_only_
      checked_before_validation` proves a registered-email + weak-password body still returns 403
      (never 400/409), i.e. no email-uniqueness/password check ran.
- [x] every existing invite issuance/acceptance test still passes unmodified — member-invite-issuance
      + member-invite-acceptance suites re-run green (the latter got a suite-local low-rpm-fixture
      flag flip only; its assertions are unchanged).
- [x] the routing_admin_router.py module docstring no longer claims "any owner/admin" — rewritten to
      "require_superadmin … SUPERSEDES the 2026-06-23 decision"; confirmed by direct read.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `from ...authz import require_superadmin` added; BOTH routes use
      `Annotated[Identity, Depends(require_superadmin)]`. NOTE: the build first shipped it WITHOUT
      `Depends()` (bare), which FastAPI silently ignores → the gate was structurally absent (422 for
      all). Caught by the build agent, fixed, and proven by reverting (5 authz tests fail bare / pass
      with Depends). `Permission`/`require_permission` imports fully removed from this file.
- [x] DEAD-CODE (code) — `Permission.ROUTING_MANAGE` remains referenced in ROLE_PERMISSIONS (owner/
      admin/operator still hold it); only its two route call sites are gone (declared-but-unused, as
      §7 records). Not orphaned.
- [x] SEMANTIC (prose) — `routing-config-write/TASK.md` confirmed UNTOUCHED (git diff = 0 lines); the
      supersession is recorded in THIS file + the router docstring, per PROJECT.md's pattern.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self + 2 independent add-verify/add-build agents (aa0fd777 verify PASS, a074d679 build — the
build agent CAUGHT the bare-require_superadmin auth-gate bug and refused to fix production, per its
brief) · adversarially checked: all 7 roles + no-token on GET+PUT /admin/routing (only SUPERADMIN
200, else 403, no-token 401); repo-wide sweep found 20 require_superadmin sites ALL using Depends
(zero bare); the sole writer of routing_config (RoutingConfigRepository.upsert) is the superadmin-
gated PUT; TenantRow is minted in exactly one place (SignupUseCase) — OIDC/invite-accept/platform
routes never create a new tenant, so no alternate path around either gate; signup guard proven
before-DB-IO; flag-flips confirmed additive (no skip/xfail/assert-removal).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-07-10

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `ERR_SIGNUP_INVITE_ONLY` 403s (abuse-probing signal on
  a locked-down deployment); rate of `ERR_AUTH_FORBIDDEN` on `/admin/routing` specifically (would
  spike once existing tenant owners/admins who relied on this surface start hitting it — an expected,
  named transition cost, worth watching for volume); any `routing.update` audit event whose
  `actor_email` is NOT a platform-tenant superadmin (should become impossible after this task; a
  hit would mean the gate has a bypass).

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled
only while this placeholder stands>

### Spec delta
- [SPEC · open] `Permission.CATALOG_SYNC`/`/internal/catalog/sync` may share the SAME defect shape
  (a global-ish resource gated by a tenant-scoped Permission every tenant's OWNER structurally
  holds) — worth its own ground pass, not investigated here (evidence: §0 Issues/Risks, out of S1's
  declared scope).
- [SPEC · open] The `/routing` dashboard page (Next.js) will start 403ing for every non-superadmin
  viewer once this task ships — a named, separate follow-up to update that page's own RBAC-gated
  visibility/messaging, not fixed by this gateway-only task (evidence: §0 "Dashboard blast radius").
- [SPEC · seeded] A dedicated operator bootstrap CLI/script (rather than the flip-on/flip-off runbook
  step in M6) for provisioning the first tenant on a fresh, fully-locked-down deployment — a nicer,
  less error-prone onboarding path; not built here (evidence: §1 Assumptions, M6).
- [SPEC · seeded] `POST /admin/auth/signup` has no rate limit today (unlike invite-accept's per-IP
  limiter) — a legitimate, separate defense-in-depth layer worth adding regardless of the invite-only
  default, since an operator who re-enables public signup gets zero abuse protection today (evidence:
  §0 Touches, §1 Assumptions).
- [SPEC · open] Confirm whether `Permission.ROUTING_MANAGE`, now unused at both its former call
  sites, should be removed/repurposed in a future, separately-scoped task (a frozen-matrix edit this
  task deliberately declined to take on) — evidence: M8.

### Competency deltas
- [DDD · open] A `Permission`-shaped RBAC gate cannot express "excludes tenant OWNER" under this
  matrix's own completeness guard (`ROLE_PERMISSIONS[Role.OWNER] == frozenset(Permission)`) — any
  genuinely operator-wide (non-tenant-scoped) resource needs a role-only gate (`require_superadmin`
  or equivalent), never a new `Permission` enum member, no matter how the feature request is worded
  ("a dedicated permission"). Worth stating explicitly in CONVENTIONS.md or authz.py's own docstring
  so a future task doesn't attempt the structurally-impossible path this draft ruled out (evidence:
  §1 Framings weighed Part B).
- [ADD · open] A previously HARD-STOP-cleared, Tin-approved security freeze (`routing-config-write`)
  can still need reversal when its own STATED premise (here: "single-operator/trusted-owner
  deployment") is invalidated by later, unrelated shipped work (multi-tenant SaaS features landing
  over the following two weeks). The SUPERSESSION pattern handled this cleanly — record the reversal
  at the new task's freeze, never silently re-edit the old frozen file (evidence: §0/§3 SUPERSESSION
  record).
