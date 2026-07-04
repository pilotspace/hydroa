# MILESTONE: Platform Identity Foundation

goal: A reserved platform tenant and a superadmin role exist and can authenticate via a new JWT role plus the existing ops-mTLS mechanism, with no new cross-tenant capability granted yet
rationale: new-major — a new product pillar (cross-tenant platform administration) that no active milestone's goal covers; resolves the open SPEC delta recorded 2026-07-01 in minimax-catalog-seed/TASK.md:743 ("introduce a platform-wide superadmin role / system-or-platform tenant concept") and unblocks its dependent MiniMax-refetch delta; first (admin-first order) of a confirmed 5-milestone roadmap: platform-identity -> platform-admin-console -> tenant-impersonation -> platform-key-default -> platform-access-plan. Related to but distinct from the separate, unmerged enterprise-hardening branch work (margin/circuit-breaker/monetization hardening) — no overlap in scope.
stage: production · status: active · created: 2026-07-02T15:53:53+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  a reserved platform-tenant row (schema + migration + seed) with a discriminator so it is
     programmatically identifiable; a superadmin Role value scoped to Users of the platform tenant
     only, whose authz check may target any tenant_id (a new ROLE_PERMISSIONS entry); superadmin
     login via the existing /admin/auth JWT flow with the JWT claim shape UNCHANGED (still always
     carries a real tenant_id — the platform tenant's); the existing ops-mTLS/XFCC mechanism
     extended so a cert-authenticated platform job can resolve the platform tenant's own stored
     tenant_provider_keys; a shared audit-event primitive for platform-level actions that
     platform-admin-console / tenant-impersonation / platform-key-default / platform-access-plan
     will all reuse.
Out: any actual cross-tenant read/write admin endpoint (→ platform-admin-console); impersonation
     (→ tenant-impersonation); credential-resolution reordering / platform-key-as-default
     (→ platform-key-default); subscription/plan/rate-limiter (→ platform-access-plan); any
     dashboard UI (→ platform-admin-console — this milestone is backend-identity-only); reshaping
     the JWT claim shape or introducing a nullable/tenant-less identity (deliberately avoided —
     see Shared decisions below).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Platform tenant** (NEW glossary term): the one reserved `Tenant` row representing the platform
  operator itself; owns its own BYOK provider credentials; never a customer; identified by a
  discriminator column, never by convention/position (e.g. "first row").
- **Superadmin** (NEW glossary term / NEW Role value): a User belonging ONLY to the platform
  tenant, whose permission check is allowed to target any tenant_id — an AUTHORIZATION-layer
  special case (authz.py + the ~15 tenant-scoped repositories), NOT an authentication-layer one.
  The JWT shape stays byte-identical to today's: `issue()`/`decode()` and the required-claims set
  (sub, tenant_id, role, email, exp, iat, iss) are UNCHANGED — a superadmin's token still always
  carries a real tenant_id (the platform tenant's).
- Platform-initiated background jobs (e.g. catalog model-refetch) authenticate via the EXISTING
  ops-mTLS/XFCC pattern (`OpsCertVerifier` / `require_ops`), extended to resolve the platform
  tenant's credentials — not a new auth mechanism.
- Audit rows for platform-level actions reuse the shipped `AuditEvent` nullable-tenant_id
  precedent's SHAPE (`tenant_id | None` with an enforced actor invariant) as prior art, but this
  milestone's audit primitive is scoped to platform/superadmin actions specifically, not a
  general port change.

## Shared / risky contracts (freeze these first)
- superadmin-bypasses-tenant-scoping semantics (the exact rule authz.py + repositories use to let
  a superadmin-role caller target any tenant_id) -> owning task superadmin-role. The single
  riskiest decision point in this milestone: too loose and a normal role inherits cross-tenant
  reach; too narrow and superadmin cannot do its job in the milestones that build on this one.
- platform-tenant discriminator shape + the exactly-one-row invariant -> owning task
  platform-tenant-seed.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] platform-tenant-seed        depends-on: none                  — reserved platform Tenant row: schema, migration, seed, exactly-one-row invariant (done, gate=PASS, 2026-07-03)
- [x] superadmin-role             depends-on: platform-tenant-seed  — Role.SUPERADMIN + ROLE_PERMISSIONS entry + DB CheckConstraint + the cross-tenant authz rule (done, gate=PASS, 2026-07-03)
- [x] superadmin-login            depends-on: superadmin-role       — /admin/auth JWT flow recognizes superadmin; JWT shape unchanged; rejected on non-superadmin-gated surfaces (done, gate=PASS, 2026-07-03)
- [x] ops-platform-job-identity   depends-on: platform-tenant-seed  — ops-mTLS path resolves the platform tenant's own tenant_provider_keys for background jobs (done, gate=PASS, 2026-07-03)
- [x] superadmin-audit-foundation depends-on: superadmin-role       — shared audit-event primitive for platform-level actions (done, gate=PASS, 2026-07-03; 3 parts — ops-credential retrofit + password-login + OIDC/SSO login, widened at freeze per Tin's decision)

## Exit criteria (observable; map each to the task that delivers it)
- [x] Exactly one Tenant row carries the platform discriminator, enforced so a second can never be created        (← platform-tenant-seed) (verify: `tests/platform_tenant_seed/test_platform_tenant_seed.py::test_second_platform_tenant_insert_rejected`, partial unique index `WHERE kind='platform'`)
- [x] A User with role=superadmin can only be created under the platform tenant; the full existing RBAC test suite stays green, 0 regressions        (← superadmin-role) (verify: `tests/superadmin_role/test_superadmin_role.py` 12/12 + `tests/test_users_role.py`+`tests/rbac_roles/`+`tests/platform_tenant_seed/` 24/24 regression, orchestrator-reproduced independently 2026-07-03)
- [x] A superadmin logs in via /admin/auth and receives a JWT whose role claim decodes to superadmin; a non-superadmin JWT is rejected from any surface gated on the new role        (← superadmin-login) (verify: `tests/superadmin_login/test_superadmin_login.py` 7/7 — 5 scenario + 2 structural, gate=PASS 2026-07-03)
- [x] An ops-mTLS-authenticated request resolves the platform tenant's stored provider credential; a request without a valid ops cert cannot        (← ops-platform-job-identity) (verify: `tests/ops_platform_job_identity/` 5/5 target + 9/9 regression, gate=PASS 2026-07-03)
- [x] Every superadmin JWT issuance and every ops-authenticated platform-job credential resolution writes a distinguishable audit row        (← superadmin-audit-foundation) (verify: `tests/superadmin_audit_foundation/` 13/13 — 5 ops-side + 4 password-login + 4 OIDC/SSO, gate=PASS 2026-07-03; full gateway suite re-run, all non-passing tests independently triaged as pre-existing/unrelated)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched — no `add.py` / `state.json` engine change from this milestone itself
  (the engine was separately updated to the latest marketplace version mid-milestone; unrelated
  to this milestone's own scope)
- skill   : untouched
- book    : untouched
- backend (`apps/gateway/src/gateway/`) : new `Tenant.kind` discriminator + partial unique index
  (platform-tenant-seed); `Role.SUPERADMIN` + `ROLE_PERMISSIONS` entry + DB `CheckConstraint` +
  cross-tenant authz bypass rule (superadmin-role); `resolve_platform_credential` ops-mTLS
  retrofit (ops-platform-job-identity — built, no HTTP consumer yet, by design); `/admin/auth`
  password login recognizes `Role.SUPERADMIN` with byte-identical JWT shape (superadmin-login,
  zero `src/` changes — pure test-suite proof against already-generic code); 3-call-site audit
  wiring (`resolve_platform_credential`, `LoginUseCase`, `OidcLoginUseCase`) reusing the existing
  `AuditEvent`/`record_audit`/`AuditRepository` primitive verbatim, zero lines under
  `gateway/audit/**` (superadmin-audit-foundation). Zero new HTTP endpoints, zero new
  tables/ports, zero new error codes across all 5 tasks.

### Cross-task evidence   (one row per task)
- platform-tenant-seed : gate=PASS · tests=9 green · residue=none
- superadmin-role : gate=PASS · tests=12 target + 24 regression green · residue=none (adversarial
  security review of the cross-tenant authz guard run separately, clean)
- superadmin-login : gate=PASS · tests=7 green (5 scenario + 2 structural) · residue=none — zero
  `src/` changes; proved `LoginUseCase`/`JwtTokenService` were already fully role-generic
- ops-platform-job-identity : gate=PASS · tests=5 target + 9 regression green · residue=note — no
  HTTP endpoint consumes `resolve_platform_credential` yet; a deliberately-unconsumed seam
  awaiting a future platform-job caller (documented in its own TASK.md, not a gap in this task)
- superadmin-audit-foundation : gate=PASS · tests=13 green (5 ops-side + 4 password-login + 4
  OIDC/SSO) + 47/47 named-regression + full-suite re-run (2242 passed; every non-passing test
  independently triaged as pre-existing/unrelated — see its own TASK.md §6) · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
  1. exactly-one-platform-tenant-row — platform-tenant-seed row above
  2. superadmin-only-under-platform-tenant + 0 RBAC regressions — superadmin-role row above
  3. superadmin JWT login + non-superadmin rejection — superadmin-login row above
  4. ops-mTLS resolves platform credential — ops-platform-job-identity row above
  5. every superadmin issuance + platform-job resolution audited — superadmin-audit-foundation row above
- goal: "A reserved platform tenant and a superadmin role exist and can authenticate via a new
  JWT role plus the existing ops-mTLS mechanism, with no new cross-tenant capability granted
  yet" — met: the platform tenant is seeded and structurally singleton-enforced; `Role.SUPERADMIN`
  authenticates via both `/admin/auth` password login and OIDC/SSO with a byte-identical JWT
  shape; the ops-mTLS path resolves the platform tenant's own credentials; every one of those
  four authentication/resolution events now writes an audit row. Explicitly OUT of scope and
  NOT granted by this milestone (by design, confirmed by re-reading Scope/Out above against the
  actual diffs): no cross-tenant read/write admin endpoint exists yet, no impersonation, no
  credential-resolution reordering, no dashboard UI — all deferred to the next 4 milestones in
  the confirmed roadmap.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] All 5 tasks' work is currently UNCOMMITTED on local `main` (no feature branch cut this
  milestone) — commit it, most naturally as 5 logical commits (one per task, matching this
  milestone's own breadth-first decomposition), following CLAUDE.md's commit-message format
- [ ] Cut a feature branch (e.g. `feat/platform-identity`) and push it — human confirms first,
  per this session's standing "confirm before push" instruction
- [ ] Open a PR from the branch with this Close — ship review as the description; human reviews
  + merges
- [ ] ADD housekeeping: `add.py archive-milestone platform-identity` + `add.py fold` to
  consolidate this milestone's 13 open Competency/Spec deltas into the foundation docs
- [ ] Separately (not part of this milestone's own scope, flag to Tin): the 2 open SPEC deltas
  this milestone surfaced but did not fix — `test_audit_store.py::test_audit_write_fail_open`'s
  vacuous AsyncMock pattern (audit-log-store, already-shipped, different milestone) and the
  `chat-playground`/`chat-workspace-page` design-prototype schema drift (82 `add.py check`
  failures, unrelated to this milestone, found incidentally while gating)
- [ ] Tag / publish / deploy as part of a future `add.py release` cut — this milestone alone
  doesn't need its own release; `status` already shows 1 milestone releasable since the last
  0.7.0 cut (human-run, per release.md)
