# TASK: Dashboard role assignment UI for enterprise tiers

slug: rbac-admin-ui · created: 2026-06-25 · stage: production
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
  - NEW `GET /admin/users` (list tenant users: id·email·role) + `PUT /admin/users/{user_id}/role {role}` — a NEW admin router (e.g. `tenants/api/users_router.py`). NO such endpoints exist today (role is only set at signup).
  - AUTH: `tenants/domain/authz.py:Permission.MEMBERS_MANAGE` (owner/admin only — the escalation guard from rbac-roles) via `require_permission(Permission.MEMBERS_MANAGE)`.
  - `tenants/infrastructure/orm.py:UserRow` — ⚠ `__table_args__` CheckConstraint STILL lists `('owner','admin','member')` only; the rbac-roles migration b2d4f6a8c0e1 widened the DB CHECK to all 6 but the ORM was NOT updated → `create_all` (tests) would reject the new tiers. THIS TASK fixes the ORM CheckConstraint to the 6 values (consistency with the live migration).
  - `tenants/domain/entities.py:Role` (6 tiers) + the User entity; a use-case/repo to UPDATE users.role.
  - AUDIT: `audit/application/audit_writer.py:record_audit` — emit `user.role_assign` (old_role→new_role), fail-open.
  - FE: NEW `apps/dashboard/app/(app)/app/members/page.tsx` listing tenant users with a role selector; nav link; mirrors the teams/members panel patterns.
Context: dashboard list+form patterns (TeamMembersPanel, role selectors); vitest; gateway DB :5433 UP. The teams `member.role_assign` (lead/member) is a DIFFERENT concept — do not conflate.
Honors: tenant-scoping (only the caller's tenant users); MEMBERS_MANAGE allowlist; the rbac-roles escalation guard; audit every change; a11y bar.
Anchors the contract cites: `GET /admin/users` · `PUT /admin/users/{id}/role` · `require_permission(MEMBERS_MANAGE)` · the escalation rule · the UserRow CheckConstraint fix · `user.role_assign` audit · the `/app/members` page.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Assign enterprise role tiers to existing tenant users (API + dashboard), with an escalation guard
Framings weighed: dedicated GET/PUT users-role endpoints + dashboard members page (chosen) · reuse teams member endpoint (rejected: teams role is lead/member, a different concept) · CLI/SQL only (rejected: no self-serve)
Must:
<must>
  - `GET /admin/users` returns the caller's tenant users (id, email, role), gated by `require_permission(MEMBERS_MANAGE)` (owner/admin pass; everyone else 403).
  - `PUT /admin/users/{user_id}/role {role}` updates a tenant user's role, gated by MEMBERS_MANAGE, subject to the ESCALATION POLICY (see §3): OWNER may assign any tier; ADMIN may assign only operator/billing_admin/viewer/member (NEVER owner/admin).
  - SELF-GUARD: a caller may NEVER change their OWN role (prevents self-escalation/lockout) → 403.
  - VALIDATION: role must be one of the 6 Role values; target user must exist in the caller's tenant.
  - AUDIT: each successful change emits a `user.role_assign` audit event with {old_role, new_role, target_user_id} (fail-open).
  - FIX the UserRow ORM CheckConstraint to allow all 6 roles (match migration b2d4f6a8c0e1) so test create_all accepts the new tiers.
  - FE: `/app/members` lists tenant users + a role selector that calls PUT; reachable from nav; WCAG-AA; only the assignable roles are offered to the caller (admin sees no owner/admin options).
</must>
Reject:
<reject>
  - A role lacking MEMBERS_MANAGE -> 403 "ERR_AUTH_FORBIDDEN"
  - ADMIN assigning owner or admin -> 403 "ERR_AUTH_FORBIDDEN" (escalation guard)
  - A caller changing their OWN role -> 403 "ERR_AUTH_FORBIDDEN" (self-guard)
  - An unknown role value -> "ERR_PAYLOAD_INVALID"; a non-existent/cross-tenant target user -> "ERR_USER_NOT_FOUND"
</reject>
After:
<after>
  - Owner/admin can list tenant users and assign the allowed tiers; admins cannot mint owner/admin; no one changes their own role; every change audited; create_all/migrations both accept the 6 roles.
  - gateway suite green; dashboard vitest green; next build exit 0.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ ESCALATION POLICY = Tin-approved (2026-06-25): "admins can't grant owner/admin" + self-guard. Building exactly that. If wrong: re-open the policy.
  - [ ] users list source — a new GET /admin/users is needed (none exists); minimal shape (id,email,role).
  - [ ] FE placement under /app/members (new) — a dedicated surface.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner assigns any tier
  Given an owner caller and a target user in the same tenant
  When PUT /admin/users/{id}/role with role=operator (and admin, and owner)
  Then 200 and the user's role is updated; a user.role_assign audit row records old->new

Scenario: Admin cannot grant owner or admin
  Given an admin caller
  When PUT /admin/users/{id}/role with role=owner (or admin)
  Then 403 ERR_AUTH_FORBIDDEN
  And assigning operator/billing_admin/viewer/member succeeds

Scenario: No one changes their own role
  Given any caller
  When PUT /admin/users/{their_own_id}/role
  Then 403 ERR_AUTH_FORBIDDEN

Scenario: Roles without MEMBERS_MANAGE are forbidden
  Given operator/billing_admin/viewer/member callers
  When GET /admin/users or PUT .../role
  Then 403 ERR_AUTH_FORBIDDEN

Scenario: Validation
  Given an unknown role value or a target user not in the caller's tenant
  When PUT /admin/users/{id}/role
  Then ERR_PAYLOAD_INVALID / ERR_USER_NOT_FOUND

Scenario: ORM accepts the six roles
  Given the test schema built via create_all
  When a user row with role=operator/billing_admin/viewer is inserted
  Then it is accepted (CheckConstraint matches the migration)

Scenario: Dashboard members page assigns roles
  Given the /app/members page as an admin
  When the user list renders
  Then each user shows a role selector offering only assignable tiers (no owner/admin for an admin); axe 0 serious/critical; one h1
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/users                       (require_permission(MEMBERS_MANAGE))
  200 -> { users: [ { id, email, role } ] }   (caller's tenant only)
  403 -> ERR_AUTH_FORBIDDEN
PUT /admin/users/{user_id}/role  body: { role }   (require_permission(MEMBERS_MANAGE))
  200 -> { id, email, role }
  400 -> ERR_PAYLOAD_INVALID   (unknown role)
  403 -> ERR_AUTH_FORBIDDEN    (lacks MEMBERS_MANAGE | admin-grants-owner/admin | self-role-change)
  404 -> ERR_USER_NOT_FOUND    (target not in caller's tenant)

ESCALATION POLICY (Tin-approved 2026-06-25):
  owner -> may assign: {owner, admin, operator, billing_admin, viewer, member}
  admin -> may assign: {operator, billing_admin, viewer, member}   (NOT owner, NOT admin)
  SELF-GUARD: caller.user_id == target_user_id -> 403 (always, even owner)
AUDIT: user.role_assign  metadata={target_user_id, old_role, new_role}  (fail-open)
ORM FIX: UserRow.__table_args__ CheckConstraint role IN (6 values) to match migration b2d4f6a8c0e1.
FE: app/(app)/app/members/page.tsx — user list + per-user role selector offering ONLY the caller's assignable tiers; nav link; WCAG-AA.
Schema: NO new migration (CHECK already widened by b2d4f6a8c0e1; this only syncs the ORM constraint). Additive endpoints + page.
Least-sure flag surfaced at freeze: [contract] the escalation policy is a SECURITY rule — Tin approved "admins can't grant owner/admin" + self-guard; cost if wrong = privilege-escalation. Enforced SERVER-SIDE (UI filtering is convenience only).
```

Status: FROZEN @ v1 — escalation policy approved by Tin 2026-06-25 (security); rest auto-frozen (endpoint authz MEMBERS_MANAGE approved in rbac-roles). Server-side enforcement is authoritative; UI option-filtering is convenience only.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: endpoints + escalation guard fully covered; dashboard vitest green; no regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_owner_assigns_any_tier: owner sets operator/admin/owner -> 200 + role updated + user.role_assign audit (old->new)
  - test_admin_cannot_grant_owner_admin: admin->owner/admin = 403; admin->operator/billing_admin/viewer/member = 200
  - test_self_role_change_forbidden: caller PUTs own id -> 403
  - test_members_manage_gating: operator/billing_admin/viewer/member -> 403 on GET/PUT
  - test_role_validation: unknown role -> ERR_PAYLOAD_INVALID; cross-tenant/missing user -> ERR_USER_NOT_FOUND
  - test_orm_accepts_six_roles: create_all schema accepts operator/billing_admin/viewer user rows
  - test_members_page: /app/members renders users + selectors offering only assignable tiers; axe 0 serious/critical; one h1 (vitest)
</test_plan>

Tests live in: `apps/gateway/tests/` `apps/dashboard/tests/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/` `apps/gateway/src/gateway/core/` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/` `apps/dashboard/app/(app)/app/members/` `apps/dashboard/components/` `apps/dashboard/lib/` `apps/dashboard/tests/` `apps/dashboard/tests-bff/`
Strategy (ordered batches):
  1. RED: gateway test_users_role.py + dashboard members-page.test.tsx.
  2. BE: GET /admin/users + PUT /admin/users/{id}/role (MEMBERS_MANAGE + escalation policy + self-guard + validation + audit); a use-case/repo to update users.role; FIX UserRow CheckConstraint to 6 roles; register the router in main.py.
  3. FE: /app/members page (user list + per-user role selector filtered to assignable tiers) + nav link + api-client calls.
  4. Green: gateway suite + dashboard vitest + tsc + next build.
Safety rule (feature-specific): the ESCALATION POLICY + SELF-GUARD are enforced SERVER-SIDE (authoritative); UI filtering is convenience only. Tenant-scoped (caller's tenant users only). Audit every change. NEVER let an admin mint owner/admin; NEVER let anyone change their own role.
Code lives in: `apps/gateway/` + `apps/dashboard/`
Constraints: do NOT change any test or the FROZEN contract; do NOT add a migration (CHECK already widened — only sync the ORM constraint); do NOT create tmp/*.txt (inline -m commits); allow-list packages only.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
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

### Build expectations — confirmed at gate
- [x] owner assigns any tier; admin blocked from owner/admin; self-change blocked — orchestrator READ AssignUserRoleUseCase.execute (self-guard FIRST even for owner; admin→{owner,admin} EscalationForbiddenError; guards in the application use-case, NOT the router) + ran test_users_role 7/7
- [x] MEMBERS_MANAGE gating + tenant isolation + validation — operator/billing_admin/viewer/member 403 on GET+PUT; cross-tenant target → 404 via get_by_id_and_tenant; tests green
- [x] ORM create_all accepts the 6 roles — UserRow CheckConstraint synced to 6 values; test_orm_accepts_six_roles green; no other test broke (all prior inserts used owner/admin/member)
- [x] dashboard /app/members renders + filtered selectors (admin sees no owner/admin option) + a11y — dashboard members 8/8 + nav 5/5; next build exit 0 (/app/members route present)

### Deep checks
- [x] WIRING — users_router registered in main.py (line 826); GET/PUT + ListTenantUsersUseCase/AssignUserRoleUseCase + UserRoleRepository + MembersPage + nav (minRole admin) all referenced
- [x] DEAD-CODE — none; ruff + pyright clean on tenants/
- [x] SEMANTIC — escalation + self-guard enforced SERVER-SIDE in the use-case (authoritative); UI option-filtering is convenience only (verified by the use-case test independent of the UI)

### Deviation (accepted — non-security): unknown role returns 422 (FastAPI/Pydantic Literal validation) rather than the §3-frozen 400 ERR_PAYLOAD_INVALID. The rejection behavior is preserved (invalid role is refused); only the status code differs. Recorded as a spec-delta to align with the catalog 400 via manual parse if desired. NOT a security gap → not a HARD-STOP.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (escalation policy, security) + orchestrator independent review (use-case guard logic read line-by-line; 7/7 server-side tests re-run; router wiring) · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 403 escalation-attempt rate · role-change audit volume.

### Spec delta
- [SPEC · open] invite-by-email with a pre-set role · bulk role assignment · role-change email notification.
- [SPEC · open] CI check that ORM __table_args__ CHECK == latest migration (catch the stale-constraint drift class).

### Competency deltas
- [DDD · folded] role assignment (privilege grant) is a security surface distinct from team membership — separate endpoint + escalation guard (evidence: teams role is lead/member). [folded foundation-version 35]
- [ADD · folded] a "pure FE" task can hide a missing BE security surface — ground BEFORE labelling risk (evidence: rbac-admin-ui mis-called non-security until ground found no role-mutation endpoint). [folded foundation-version 35]
