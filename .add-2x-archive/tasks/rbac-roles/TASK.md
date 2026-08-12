# TASK: Enterprise RBAC role tiers + authorization matrix

slug: rbac-roles · created: 2026-06-24 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/tenants/domain/entities.py:Role` — StrEnum {OWNER,ADMIN,MEMBER}; `User.role`, `Identity.role`. EXTEND additively with enterprise tiers; the existing 3 values MUST keep current semantics.
  - `apps/gateway/src/gateway/keys/api/deps.py:require_owner_or_admin` — canonical authz dep: 403 if role==MEMBER else pass. Reused widely.
  - OWNER-only checks: `proxy/api/provider_keys_admin_router.py:_require_owner_tenant_id` (secrets) + `auth/api/oidc_admin_router.py` (`role != Role.OWNER` → 403). Security-sensitive surfaces.
  - Per-surface `require_owner_or_admin` consumers (the matrix surfaces): `catalog/api/{deps,router}.py` · `usage/api/router.py` · `budgets/api/router.py` · `teams/api/{deps,router}.py` · `tenants/api/{cache,guardrail}_router.py` · `proxy/api/routing_admin_router.py` · `ops/api/deps.py`.
  - `core/error_catalog.py:AUTH_FORBIDDEN` (403 insufficient role) + the owner-only spec (line ~85).
  - DB: `users.role` column (TEXT/enum) — a migration is needed if the stored value set grows (Alembic under `apps/gateway/migrations/`).
Context (working folder): PROJECT.md invariants (tenant-scoping; authz on the gateway); CONVENTIONS.md (DDD ports, error catalog); gateway test DB needs docker (`infra/docker-compose.dev.yml`, :5433) per [[v35-milestone-status]]; pytest ONE process at a time.
Honors: additive/back-compat (OWNER/ADMIN/MEMBER byte-identical); authz enforced on the gateway only; Role is domain vocabulary (StrEnum, like the modality Literal); every tenant-scoped query keeps tenant_id.
Anchors the contract cites: `Role` enum (extended values) · a NEW permission/authorization seam (e.g. `Permission` + `require_permission(...)` or a role→permissions matrix) · `require_owner_or_admin` (preserved) · the `users.role` migration.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Enterprise RBAC role tiers + permission matrix (allowlist authorization)
Framings weighed: PERMISSION-MATRIX allowlist — Role→frozenset[Permission], deps check a Permission (chosen) · keep role denylists + add ad-hoc role checks (rejected: privilege-escalation trap — new roles silently pass `!= MEMBER`) · external policy engine/OPA (rejected: out of scope)
Must:
<must>
  - Add enterprise tiers to `Role` ADDITIVELY: VIEWER · BILLING_ADMIN · OPERATOR (existing OWNER/ADMIN/MEMBER unchanged).
  - Introduce a `Permission` vocabulary + a `ROLE_PERMISSIONS: dict[Role, frozenset[Permission]]` matrix and a
    `require_permission(perm)` dependency. Authorization becomes an ALLOWLIST (caller's role must HOLD the permission),
    not a member-denylist — so a new role defaults to LEAST privilege (holds nothing it is not granted).
  - BACK-COMPAT INVARIANT (proven by test): for OWNER/ADMIN/MEMBER every existing endpoint returns the SAME status as
    today — OWNER+ADMIN pass `require_owner_or_admin` surfaces, MEMBER gets 403; OWNER-only surfaces (provider-keys, OIDC)
    stay OWNER-only. `require_owner_or_admin` is re-expressed over the matrix without changing its observable behavior.
  - Each new tier is enforced per-surface per the approved MATRIX (see §3): OPERATOR=ops/routing/catalog/keys + reads;
    BILLING_ADMIN=budgets + spend/usage reads; VIEWER=read-only dashboards; none of the three can touch provider
    secrets, OIDC/security, or MEMBER ROLE ASSIGNMENT (role assignment stays OWNER/ADMIN — escalation guard).
  - `users.role` persists the new values (Alembic migration; no CHECK that rejects them); Identity/JWT carries the role.
  - 403 on insufficient permission returns the existing AUTH_FORBIDDEN error shape.
</must>
Reject:
<reject>
  - A caller whose role lacks the surface's required Permission -> 403 "ERR_AUTH_FORBIDDEN"
  - A non-OWNER/ADMIN attempting to assign/elevate a member's role -> 403 (escalation guard) "ERR_AUTH_FORBIDDEN"
  - Provider-secret / OIDC surfaces accessed by anyone but OWNER -> 403 (unchanged owner-only) "ERR_AUTH_FORBIDDEN"
  - A new role added to the enum but NOT present in ROLE_PERMISSIONS -> startup/test failure "incomplete_matrix" (no silent full-access)
</reject>
After:
<after>
  - OWNER/ADMIN/MEMBER behavior is byte-identical to pre-task (full existing suite green).
  - VIEWER/BILLING_ADMIN/OPERATOR are enforced exactly per the approved matrix; least-privilege by default.
  - Every Role has an explicit ROLE_PERMISSIONS entry; `require_owner_or_admin` preserved; migration applies cleanly.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The exact PERMISSION MATRIX (which tier may touch which surface) is a SECURITY decision — Tin must approve it at the
    §3 freeze (NOT auto-frozen). Lowest confidence: OPERATOR-manages-keys? audit-read for BILLING_ADMIN/VIEWER? If wrong:
    over/under-grant = a security defect. MITIGATION: present the matrix; freeze only on Tin's explicit approval.
  - [ ] Role assignment stays OWNER+ADMIN only (escalation guard) — assumed; confirm at freeze.
  - [ ] Allowlist refactor of `require_owner_or_admin` preserves behavior — proven by a back-compat test, not assumed.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Back-compat — owner/admin/member unchanged
  Given the existing endpoints and an owner, an admin, and a member caller
  When each calls every existing admin endpoint
  Then owner and admin get the SAME status as before, and member still gets 403 (byte-identical behavior)

Scenario: Operator can manage routing/catalog/keys, read ops, read audit
  Given an operator caller
  When they call routing/catalog/keys-admin/ops-read/audit-read endpoints
  Then each succeeds (holds the permission)
  And budgets/provider-secrets/oidc/member-management return 403

Scenario: Billing admin can manage budgets and read usage only
  Given a billing_admin caller
  When they call budgets and usage/spend reads
  Then each succeeds
  And routing/keys/catalog/provider-secrets/oidc/members/audit return 403

Scenario: Viewer is read-only
  Given a viewer caller
  When they call any write/admin endpoint
  Then it returns 403
  And usage/spend and ops reads succeed

Scenario: Escalation guard — only owner/admin assign roles
  Given an operator/billing_admin/viewer caller
  When they attempt to assign or elevate a member's role
  Then it returns 403 (MEMBERS_MANAGE not held)
  And owner/admin can still assign roles

Scenario: Owner-only secrets unchanged
  Given a non-owner caller (admin/operator/billing_admin/viewer/member)
  When they call /admin/provider-keys or /admin/oidc
  Then it returns 403 (owner-only preserved)

Scenario: Reject — incomplete matrix
  Given a Role value with no ROLE_PERMISSIONS entry
  When the matrix is validated (import/test time)
  Then it fails fast ("incomplete_matrix") — never silent full access
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Role (extended StrEnum): owner · admin · operator · billing_admin · viewer · member

Permission (enum): KEYS_MANAGE · ROUTING_MANAGE · CATALOG_SYNC · BUDGETS_MANAGE · USAGE_READ ·
  OPS_READ (health/ratelimits/alerts) · MEMBERS_MANAGE (incl role assignment) · PROVIDER_SECRETS ·
  SECURITY_CONFIG (OIDC/SSO) · AUDIT_READ

ROLE_PERMISSIONS  (allowlist; a role holds ONLY what is listed — least privilege by default):
  owner         : ALL permissions
  admin         : KEYS_MANAGE · ROUTING_MANAGE · CATALOG_SYNC · BUDGETS_MANAGE · USAGE_READ · OPS_READ · MEMBERS_MANAGE · AUDIT_READ
                  (NOT PROVIDER_SECRETS, NOT SECURITY_CONFIG  ← preserves today's owner-only)
  operator      : ROUTING_MANAGE · CATALOG_SYNC · KEYS_MANAGE · USAGE_READ · OPS_READ · AUDIT_READ
  billing_admin : BUDGETS_MANAGE · USAGE_READ · OPS_READ
  viewer        : USAGE_READ · OPS_READ
  member        : (none of the admin permissions — unchanged: own keys + own usage via existing non-admin paths)

ENDPOINT BINDINGS (require_permission replaces the role check; behavior identical for owner/admin/member):
  KEYS_MANAGE      -> keys admin · catalog deps · (operator+admin+owner)
  ROUTING_MANAGE   -> PUT/GET /admin/routing
  CATALOG_SYNC     -> POST /admin/catalog/sync
  BUDGETS_MANAGE   -> budgets router
  USAGE_READ       -> usage/spend read
  OPS_READ         -> health · ratelimits · alerts read
  MEMBERS_MANAGE   -> teams router incl role assignment  (owner+admin ONLY — escalation guard)
  PROVIDER_SECRETS -> /admin/provider-keys  (owner ONLY — unchanged)
  SECURITY_CONFIG  -> /admin/oidc           (owner ONLY — unchanged)
  AUDIT_READ       -> GET /admin/audit       (consumed by the later audit-log-surface task)

require_owner_or_admin  := require_permission over the set {perms admin+owner share} — re-expressed, behavior preserved.

Rejections: ERR_AUTH_FORBIDDEN (403) for any role lacking the surface permission; incomplete_matrix (a Role with no
  ROLE_PERMISSIONS entry) fails fast at import/test — never silent full access.
Schema: users.role TEXT keeps storing the role string; Alembic migration widens the allowed set (no CHECK rejects
  the 3 new values); NO other table change. Identity/JWT already carry role (string) — no token-format change.
Least-sure flag surfaced at freeze: [contract] the MATRIX rows for the 3 new tiers (esp. operator-holds-KEYS_MANAGE
  and who-holds-AUDIT_READ) are a SECURITY judgment — over-grant = escalation, under-grant = useless role. Cost if
  wrong: a security defect or a follow-up re-grant. This is why the freeze needs Tin's explicit approval.
```

Status: FROZEN @ v1 — approved by Tin 2026-06-25 (security HARD-STOP; matrix approved AS DRAFTED: operator holds KEYS_MANAGE; AUDIT_READ = owner/admin/operator; escalation guard owner/admin only).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: matrix + bindings fully covered; full gateway suite green (no regression).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_backcompat_owner_admin_member: parametrized over existing admin endpoints — owner/admin pass, member 403 (SAME as pre-task)
  - test_operator_permissions: operator passes routing/catalog/keys/ops/audit; 403 on budgets/provider-secrets/oidc/members
  - test_billing_admin_permissions: billing_admin passes budgets + usage read; 403 on routing/keys/catalog/secrets/oidc/members/audit
  - test_viewer_readonly: viewer passes usage/ops reads; 403 on every write/admin
  - test_escalation_guard: operator/billing_admin/viewer 403 on role-assignment; owner/admin succeed
  - test_owner_only_secrets: non-owner 403 on /admin/provider-keys and /admin/oidc (unchanged)
  - test_matrix_complete: every Role has a ROLE_PERMISSIONS entry (assert at import); a missing entry raises
  - test_require_permission_unit: require_permission(perm) passes iff perm in ROLE_PERMISSIONS[role]; 403 otherwise (AUTH_FORBIDDEN shape)
</test_plan>

Tests live in: `apps/gateway/tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/` `apps/gateway/src/gateway/keys/api/deps.py` `apps/gateway/src/gateway/catalog/api/` `apps/gateway/src/gateway/usage/api/` `apps/gateway/src/gateway/budgets/api/` `apps/gateway/src/gateway/teams/api/` `apps/gateway/src/gateway/proxy/api/routing_admin_router.py` `apps/gateway/src/gateway/ops/api/` `apps/gateway/src/gateway/core/` `apps/gateway/migrations/` `apps/gateway/tests/`
Strategy (ordered batches):
  1. RED tests `apps/gateway/tests/test_rbac_roles.py` (8 per plan) — incl the back-compat matrix proof.
  2. Domain: extend `Role` (VIEWER/BILLING_ADMIN/OPERATOR); add `Permission` enum + `ROLE_PERMISSIONS` matrix + a completeness assertion; locate in `tenants/domain/` (authz vocabulary).
  3. Deps: add `require_permission(perm)`; re-express `require_owner_or_admin` over the matrix (behavior-preserving); add per-surface permission deps where surfaces currently use `require_owner_or_admin` / owner-only checks.
  4. Bind each router/endpoint to its Permission per the §3 ENDPOINT BINDINGS (owner-only secrets/OIDC unchanged).
  5. Alembic migration widening `users.role` accepted values (no CHECK rejecting the 3 new ones).
  6. Green: full gateway suite (docker DB up), ruff, pyright.
Safety rule (feature-specific): ALLOWLIST — a role holds only granted permissions; a new/unknown role defaults to NO admin permission. Owner/admin/member behavior MUST stay byte-identical (back-compat test is the proof). No token-format change.
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the FROZEN matrix; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — INDEPENDENTLY re-run: 1584 passed, 19 deselected, 87.82% coverage (4m15s, single process)
- [x] coverage did not decrease — 87.82% (≥80% gate); +8 rbac tests + 6 migration tests, no prior test removed
- [x] no test or contract was altered — FROZEN matrix unchanged; existing tests unchanged (back-compat proven, not patched)
- [x] the green was EARNED — orchestrator independently READ authz.py + re-expressed require_owner_or_admin + ran a standalone matrix-invariant assertion (not just the suite). Tests hit real endpoints with real role tokens. No vacuous asserts.
- [x] concurrency / timing — N/A (pure authorization checks; stateless matrix lookup; no shared mutable state)
- [x] no exposed secrets / injection / deps — no new dependency; no secrets; provider-secrets + OIDC stay owner-only (separately tested)
- [x] layering & dependencies follow CONVENTIONS.md — authz vocabulary in tenants/domain (DDD); deps in api layer; error via catalog
- [x] a person reviewed & approved — TIN approved the §3 matrix (security HARD-STOP, 2026-06-25) + orchestrator independent code+evidence review

### Build expectations — what "correct" looks like (confirmed at gate)
- [x] Matrix in code == approved matrix EXACTLY — read authz.py:ROLE_PERMISSIONS (owner=ALL, admin=all−secrets/oidc, operator=routing/catalog/keys/usage/ops/audit, billing_admin=budgets/usage/ops, viewer=usage/ops, member=∅)
- [x] LEAST-PRIVILEGE default — `ROLE_PERMISSIONS.get(role, frozenset())`: an unknown role gets NO access (verified by reading require_permission)
- [x] BACK-COMPAT byte-identical — require_owner_or_admin re-expressed over KEYS_MANAGE; owner/admin pass, member 403 (test_backcompat green; standalone assertion confirms billing_admin/viewer lack KEYS_MANAGE → correctly blocked)
- [x] Escalation guard — teams→MEMBERS_MANAGE (owner/admin only); operator/billing_admin/viewer 403 on role assignment (asserted)
- [x] Owner-only preserved — provider-keys + OIDC unchanged owner-only (test_owner_only_secrets green)
- [x] Migration b2d4f6a8c0e1 chains from head c3e5b7a9f1d2 and applies (alembic heads single; 6 migration tests green)

### Deep checks
- [x] WIRING — Permission/ROLE_PERMISSIONS/require_permission referenced by 10 rebinding sites (routing/catalog/budgets/usage/ops/teams + keys deps); completeness guard runs at import
- [x] DEAD-CODE — require_owner_or_admin kept (still used by catalog/cache/guardrail surfaces, intentionally → KEYS_MANAGE); no orphan (ruff/pyright clean on new files)
- [x] SEMANTIC — observation (not blocker): cache_router/guardrail_router PUT remain on require_owner_or_admin→KEYS_MANAGE, so OPERATOR can toggle tenant cache/guardrail. Consistent with the matrix (operational config) + back-compat for owner/admin/member; logged as a SPEC delta to bind them to an explicit permission if a tighter split is wanted.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (matrix approval, security gate) + orchestrator independent code + matrix-invariant + full-suite review · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
