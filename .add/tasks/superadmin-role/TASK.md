# TASK: Superadmin role

slug: superadmin-role · created: 2026-07-02 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
depends-on: platform-tenant-seed (done, gate=PASS, 2026-07-03)
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/domain/entities.py:8-14` — `Role(StrEnum)`: OWNER, ADMIN, OPERATOR, BILLING_ADMIN, VIEWER, MEMBER (6 values). Adding `SUPERADMIN = "superadmin"` as the 7th. `User`/`Identity` dataclasses (lines 17-34) need zero changes — both already carry `tenant_id` + `role`.
- `apps/gateway/src/gateway/tenants/domain/authz.py` — `Permission` enum (10 capabilities) · `ROLE_PERMISSIONS: dict[Role, frozenset[Permission]]` · import-time completeness guard (fires `RuntimeError` if any `Role` lacks an entry) · `require_permission(perm)` FastAPI dependency factory whose inner check only asks "does this role hold this capability" — it never sees a target tenant_id. File currently has NO `import uuid`.
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py` — `UserRow.__table_args__` `CheckConstraint("role IN ('owner', 'admin', 'operator', 'billing_admin', 'viewer', 'member')", name="users_role_check")` — needs its 7th value. `TenantRow.kind` (platform-tenant-seed, now BUILT) is the column this task's trigger subquery depends on — this task's migration MUST chain after platform-tenant-seed's `3fc2328e5e82`.
- `apps/gateway/src/gateway/tenants/application/users_use_cases.py` — `AssignUserRoleUseCase.execute` (the `PUT /admin/users/{id}/role` handler's use case). Escalation policy: `caller_role == Role.ADMIN` is restricted to `_ADMIN_ASSIGNABLE = {OPERATOR, BILLING_ADMIN, VIEWER, MEMBER}` — but the OWNER branch has NO restriction at all. Real, exploitable-today-once-the-enum-value-exists hole: any customer tenant's OWNER could call this endpoint with `new_role=superadmin` on a user in their own tenant.
- `apps/gateway/src/gateway/tenants/api/users_router.py:98-162` — `assign_user_role`. Parses the request body via `try: new_role = Role(body.role) except ValueError: raise PAYLOAD_INVALID.exc(...)`. **Critical finding**: `apps/gateway/tests/test_users_role.py::test_role_validation` (existing, green, untouched) uses `{"role": "superadmin"}` as its "definitely-invalid-literal" case today, asserting 422 `ERR_PAYLOAD_INVALID`. The instant `Role.SUPERADMIN` exists, `Role("superadmin")` stops raising `ValueError` — this exact test breaks unless the router explicitly keeps rejecting that literal for a different reason. Per CLAUDE.md ("never weaken a test... to make a build pass"), designed around this rather than touching the test — see §1/§3.
- `apps/gateway/src/gateway/tenants/infrastructure/repository.py` — `SqlAlchemyIdentityRepository.create_tenant_with_owner` hardcodes `role=Role.OWNER` on a freshly created tenant (can never coincide with the platform tenant); `get_or_provision_oidc_user` hardcodes `role=Role.MEMBER`. Neither can ever produce `role=superadmin`; neither needs to change.
- `apps/gateway/src/gateway/tenants/infrastructure/jwt_service.py` — `issue()`/`decode()` are fully generic over `Role` (`Role(claims["role"])`), zero role-specific branching. Confirmed: zero code change needed anywhere in the JWT issuance/decode path or in `/admin/auth` signup/login/me for a superadmin to log in once (a) the enum has the value and (b) a User row exists.
- `apps/gateway/migrations/versions/b2d4f6a8c0e1_widen_users_role_check_for_enterprise_tiers.py` — exact precedent for widening this same CHECK: `op.drop_constraint` + `op.create_check_constraint`, plain downgrade with a WARNING docstring. `apps/gateway/migrations/versions/f2a4c6e8b0d3_audit_retention_trigger.py` — exact precedent for a guard TRIGGER: `CREATE OR REPLACE FUNCTION ... RETURNS trigger` + `CREATE TRIGGER`, raised via plain `RAISE EXCEPTION '<code>: <message>'`, no explicit SQLSTATE. Migration chain: current head `3fc2328e5e82` (platform-tenant-seed, BUILT) → this task's migration.
- `apps/gateway/src/gateway/audit/infrastructure/audit_events_orm.py` + `apps/gateway/tests/audit/test_audit_store.py` (`immutable_audit_session` fixture) — this codebase's established pattern for testing a trigger against the fast `create_all` schema: manually re-apply the `CREATE FUNCTION`/`CREATE TRIGGER` SQL in a local test fixture, because `Base.metadata.create_all` never runs migration-only `op.execute()` DDL. Confirmed via search that `event.listen`/`DDL(` is used nowhere in `apps/gateway/src` — triggers are ALWAYS migration-only, NEVER create_all-time, in this codebase.
- `apps/gateway/src/gateway/core/error_catalog.py` — `AUTH_FORBIDDEN = ErrorSpec(403, "ERR_AUTH_FORBIDDEN", ...)`, `PAYLOAD_INVALID = ErrorSpec(422, "ERR_PAYLOAD_INVALID", ...)`. `apps/gateway/src/gateway/core/errors.py` — `ProblemError(status, code, title, detail, headers)`, `.code` is the attribute tests assert on.
- `apps/gateway/src/gateway/tenants/infrastructure/ops_cert_verifier.py` + `.add/GLOSSARY.md:28-29` — `OpsIdentity(fingerprint: str)` is a completely disjoint type from `Identity` (no `tenant_id`, no `role`) — mechanically confirms `platform operator`/`ops-auth` (mTLS, machine, cross-tenant READ only) can never be conflated with `superadmin` (JWT, human, this task) at the type level.
- Codebase sweep of `Role` references: `keys/application/use_cases.py` (`role == Role.MEMBER` checks only — unaffected); `auth/api/oidc_admin_router.py` + `proxy/api/provider_keys_admin_router.py` (`identity.role != Role.OWNER` literal gates, bypassing the Permission system entirely) — a superadmin is NOT an OWNER and would be rejected by these two owner-literal gates. Traced deliberately, NOT touched — see §1 Reject/boundary.

Context (working folder):
- `.add/milestones/platform-identity/MILESTONE.md` — Scope In names this task's two deliverables precisely: the Role value + `ROLE_PERMISSIONS` entry, and "the exact rule authz.py + repositories use to let a superadmin-role caller target any tenant_id" is explicitly this task's owned "Shared / risky contract" — the single riskiest decision point in the milestone.
- `.add/tasks/platform-tenant-seed/TASK.md` — §3 FROZEN @ v1, §5 BUILT (`tenants.kind`, partial-unique-index, `get_platform_tenant(session)`). VERIFY still pending as of this task's drafting — treated as assumed-stable per the milestone's parallel-drafting instruction.
- `.add/GLOSSARY.md:28-29` — `platform operator` / `ops-auth` — quoted verbatim in §1.

Honors (patterns / conventions):
- PROJECT.md invariant "every tenant-owned row carries tenant_id; every query is tenant-scoped" — a superadmin's cross-tenant reach must be an explicit, narrow, auditable exception, never a blanket removal of scoping. This design changes ZERO existing repository's `WHERE tenant_id = ...` filter.
- CHECK-constrained enums declared in BOTH migration and ORM `__table_args__` (kind/role precedent) — followed.
- One test directory per task: `apps/gateway/tests/superadmin_role/`.
- `pyproject.toml:164` — `asyncio_mode = "auto"`, no `@pytest.mark.asyncio` needed.

Anchors the contract cites: `Role` (entities.py) · `ROLE_PERMISSIONS`/completeness guard/`require_permission` (authz.py) · `UserRow.__table_args__` (orm.py) · `AssignUserRoleUseCase.execute` (users_use_cases.py) · `assign_user_role` router (users_router.py) · `b2d4f6a8c0e1` (CHECK-widen precedent) · `f2a4c6e8b0d3`/`audit_events_immutable_guard_fn` (trigger precedent) · `immutable_audit_session` fixture (manual-trigger-on-create_all precedent) · GLOSSARY.md:28-29 (`platform operator`/`ops-auth`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Superadmin Role — a platform-tenant-only Role value whose authorization check may target any tenant_id

Naming distinction (never conflate — three lookalike concepts):
1. `platform operator` (existing, GLOSSARY.md:28) — an authority reading ACROSS tenants; today its only power is `GET /ops/reconciliation`; authenticates via `ops-auth` (mTLS/XFCC, `OpsCertVerifier`), "never a tenant JWT" — a MACHINE credential. Untouched by this task.
2. `Role.OPERATOR` (existing) — an ordinary PER-TENANT role (e.g. a customer's own ops-minded admin). Textually similar to #1, semantically unrelated. Untouched by this task.
3. `superadmin` (NEW, this task) — a `Role` value for a `User` belonging ONLY to the platform tenant; authenticates via the EXISTING `/admin/auth` JWT flow (human login, unchanged claim shape: sub, tenant_id, role, email, exp, iat, iss); its authz check MAY target any tenant_id. An AUTHORIZATION-layer special case, not an authentication-layer one.

Framings weighed:
The cross-tenant authz-bypass mechanism as a new pure function `authorize_tenant_scope(identity, target_tenant_id)` in authz.py **(chosen)** · a special case inside `require_permission`/its inner check itself **(rejected** — that function answers "does this role hold this capability", not "which tenant"; no route parametrizes one today; baking tenant-targeting in there would conflate two orthogonal questions and make the `ROLE_PERMISSIONS` matrix a lie for superadmin**)** · relying on `ROLE_PERMISSIONS[SUPERADMIN] = frozenset(Permission)` alone **(necessary but insufficient** — a `Permission` says nothing about which tenant; every repository still filters `WHERE tenant_id = identity.tenant_id` regardless of role**)**.
The "superadmin only exists under the platform tenant" data invariant as a `BEFORE INSERT OR UPDATE ON users` trigger checking `NEW.role='superadmin' ⟹ NEW.tenant_id` is the `kind='platform'` tenant **(chosen**, precedented by `audit_events_immutable_guard_fn`**)** · application-layer-only enforcement at the 3 known `User.role` write sites **(rejected as the SOLE mechanism** — bypassable by any future direct-SQL script or ops one-off; a Postgres CHECK constraint cannot express this at all since it's cross-table**)**. Kept as a SECOND layer too (typed error at the one real HTTP-adjacent write path), not as the sole ground truth — see §3.

Must:
<must>
  - Add `Role.SUPERADMIN = "superadmin"` as a 7th value on the `Role` StrEnum (entities.py) — zero change to `User`/`Identity`.
  - Add `ROLE_PERMISSIONS[Role.SUPERADMIN] = frozenset(Permission)` (authz.py) — full parity with OWNER; satisfies the existing import-time completeness guard with zero change to the guard itself.
  - Add `authorize_tenant_scope(identity: Identity, target_tenant_id: uuid.UUID) -> None` (authz.py) — the frozen cross-tenant-bypass predicate; raises `AUTH_FORBIDDEN.exc()` unless the caller is SUPERADMIN or the target equals the caller's own tenant_id.
  - Widen `UserRow.__table_args__`'s `users_role_check` CheckConstraint to 7 values (orm.py), appending `'superadmin'` after `'member'`.
  - Add a migration (chains after platform-tenant-seed's `3fc2328e5e82`) that (i) widens the DB-level `users_role_check` CHECK the same way, mirroring `b2d4f6a8c0e1`'s exact `drop_constraint`/`create_check_constraint` strategy, and (ii) creates a `users_superadmin_platform_tenant_guard` trigger enforcing the platform-tenant invariant, mirroring `f2a4c6e8b0d3`'s exact function+trigger strategy.
  - `PUT /admin/users/{user_id}/role` (existing endpoint) rejects a `{"role": "superadmin"}` payload with the SAME observable shape (422 `ERR_PAYLOAD_INVALID`) as any unparseable role literal — for EVERY caller, regardless of the caller's own role or tenant. Never assignable via this generic, unaudited endpoint.
  - `AssignUserRoleUseCase.execute` defensively rejects `new_role == Role.SUPERADMIN` too (raising the existing `EscalationForbiddenError`), unconditionally, before touching the repository — defense-in-depth for any future non-HTTP caller of the use case.
  - Existing tenant-creation paths (`create_tenant_with_owner`, `get_or_provision_oidc_user`) stay byte-identical — zero code change; both are traced to hardcode OWNER/MEMBER respectively and can never produce SUPERADMIN.
  - The full existing RBAC-adjacent test suite (`rbac_roles/`, `test_users_role.py`, `tenant_identity`, `platform_tenant_seed/`) stays green, 0 regressions — in particular the pre-existing `test_role_validation`'s `{"role": "superadmin"}` sub-case keeps returning exactly 422 `ERR_PAYLOAD_INVALID`.
</must>
Reject:
<reject>
  - `users` INSERT or UPDATE with `role='superadmin'` and `tenant_id` != the platform tenant's id -> DB exception `superadmin_requires_platform_tenant` (trigger-level; plain `RAISE EXCEPTION`, no explicit SQLSTATE — no new HTTP surface, a backstop, not a public API path)
  - `users` INSERT with `role` outside the 7 valid values (e.g. `'bogus_role'`) -> DB check-violation (23514) — unchanged from today; regression guard that widening to 7 values didn't accidentally loosen the constraint to unconstrained TEXT
  - `PUT /admin/users/{id}/role` body `{"role": "superadmin"}` -> 422 `ERR_PAYLOAD_INVALID` — for literally every caller; there is no self-service path to mint/promote a superadmin via this endpoint in this task
  - `AssignUserRoleUseCase.execute(new_role=Role.SUPERADMIN)` called directly (bypassing the router) -> `EscalationForbiddenError`
  - `authorize_tenant_scope(identity, target_tenant_id)` where `identity.role != Role.SUPERADMIN` and `target_tenant_id != identity.tenant_id` -> `AUTH_FORBIDDEN.exc()` (403 `ERR_AUTH_FORBIDDEN`) — the predicate's baseline (non-bypass) behavior, proven for every one of the other 6 roles
</reject>
After:
<after>
  - `Role.SUPERADMIN` is a valid enum member; `ROLE_PERMISSIONS[Role.SUPERADMIN] == frozenset(Permission)`
  - A `superadmin` User row can exist if and only if its `tenant_id` is the platform tenant's — enforced at the DB level (both INSERT and UPDATE), independent of which application code path attempts the write
  - `authorize_tenant_scope` exists, is fully unit-tested, and is ready for `platform-admin-console` to wire into its first cross-tenant repository call without re-litigating the rule
  - Zero regression anywhere in the existing RBAC/tenant-identity suites; `test_role_validation`'s existing assertion is untouched and still passes
  - A superadmin's mundane in-platform-tenant role changes already get an audit row for free via the pre-existing unconditional `user.role_assign` audit emission in `users_router.py` — noted, not built by this task; `superadmin-audit-foundation`'s job
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Shipping `authorize_tenant_scope` now as dormant/unwired code vs. freezing ONLY the rule as CONTRACT PROSE and deferring the actual Python symbol to `platform-admin-console` — lowest confidence because it's a bet on a not-yet-specified future task's needs, AND it creates real tension with the ADD Verify gate's DEAD-CODE check (zero production callers until that task lands). If wrong: delete the function + its 3 unit tests, replace with a prose-only rule statement in §3; cheap, contained.
  ⚠ A DB trigger as the primary (not just defense-in-depth) enforcement of "superadmin only under the platform tenant" vs. application-layer-only enforcement at the 3 enumerable write sites — the trigger is the heavier option. If wrong: drop the migration's trigger + function + the 3 tests exercising it, keep only the `AssignUserRoleUseCase` guard as the sole enforcement — a real, viable, smaller fallback.
  - [ ] Categorically blocking `Role.SUPERADMIN` from `PUT /admin/users/{id}/role` for EVERY caller (no self-service promotion path, even within the platform tenant) — confirm or redirect; low cost either way.
  - [ ] `ROLE_PERMISSIONS[Role.SUPERADMIN] = frozenset(Permission)` (full OWNER-parity within its own tenant) as the correct INITIAL grant — confirm; one-line change if narrower is wanted.
  - [ ] Leaving `oidc_admin_router.py`'s and `provider_keys_admin_router.py`'s `identity.role != Role.OWNER` literal gates untouched, so a superadmin cannot self-service OIDC/provider-key config even for the platform tenant's own resources — deliberate minimal-blast-radius choice; confirm acceptable to defer.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: superadmin role exists and holds full permissions
  Given the Role enum and ROLE_PERMISSIONS matrix
  When the authz module is imported (triggering the completeness guard)
  Then Role.SUPERADMIN is a member of Role
  And ROLE_PERMISSIONS[Role.SUPERADMIN] equals frozenset(Permission)
  And no RuntimeError is raised at import time
  And Role.OPERATOR remains a distinct, unaffected value

Scenario: the widened CHECK constraint accepts superadmin under the platform tenant
  Given a create_all test schema with the widened CHECK constraint and the guard trigger applied
  And a tenant row with kind='platform'
  When a user row is inserted with role='superadmin' and that tenant's id
  Then the insert succeeds
  And the row is readable back with role='superadmin'

Scenario: the widened CHECK constraint still rejects a wholly unknown role value
  Given the widened 7-value CHECK constraint
  When a user row is inserted with role='bogus_role'
  Then the database raises a check-violation (23514)
  And no row is inserted

Scenario: a superadmin row is rejected outside the platform tenant (INSERT)
  Given the guard trigger is applied
  And an ordinary kind='customer' tenant exists
  When a user row is inserted with role='superadmin' and that customer tenant's id
  Then the database raises the superadmin_requires_platform_tenant exception
  And no row is inserted

Scenario: an existing user cannot be promoted to superadmin outside the platform tenant (UPDATE)
  Given the guard trigger is applied
  And an ordinary member-role user exists in a customer tenant
  When that row is updated to role='superadmin'
  Then the database raises the superadmin_requires_platform_tenant exception
  And the row's role is unchanged

Scenario: the real migration creates the widened CHECK and the guard trigger end-to-end
  Given a clean database at the prior alembic head
  When the superadmin-role migration is applied
  Then inserting role='superadmin' under the platform tenant succeeds
  And inserting role='superadmin' under a customer tenant raises superadmin_requires_platform_tenant
  And exactly one superadmin row exists after both attempts

Scenario: the generic role-assignment endpoint never accepts superadmin as a payload value
  Given an authenticated tenant owner and a target user in the same tenant
  When PUT /admin/users/{id}/role is called with {"role": "superadmin"}
  Then the response is 422 ERR_PAYLOAD_INVALID
  And the target user's role in the database is unchanged
  And this is byte-identical in shape to the pre-existing unknown-role-literal rejection

Scenario: AssignUserRoleUseCase rejects superadmin before touching the repository
  Given a direct call to AssignUserRoleUseCase.execute with new_role=Role.SUPERADMIN
  When execute() runs
  Then EscalationForbiddenError is raised
  And the repository is never called

Scenario: authorize_tenant_scope allows a superadmin identity to target any tenant
  Given an Identity with role=Role.SUPERADMIN
  When authorize_tenant_scope is called with a target_tenant_id different from the identity's own
  Then no exception is raised

Scenario: authorize_tenant_scope rejects every non-superadmin role targeting another tenant
  Given an Identity with a non-SUPERADMIN role
  When authorize_tenant_scope is called with a target_tenant_id different from the identity's own
  Then AUTH_FORBIDDEN (403 ERR_AUTH_FORBIDDEN) is raised

Scenario: authorize_tenant_scope allows same-tenant access for every role unchanged
  Given an Identity of any role, including SUPERADMIN
  When authorize_tenant_scope is called with target_tenant_id equal to the identity's own tenant_id
  Then no exception is raised

Scenario: signup continues to create role=owner, never superadmin
  Given a new user signs up via POST /admin/auth/signup
  When create_tenant_with_owner executes
  Then the created user's role in the database is 'owner'
  And never 'superadmin'
```

</scenarios>

Note: two Musts (verbatim delegation shape / zero dependency on JWT reshape) are structural claims verified by code/import-diff review, not a runtime Gherkin scenario.

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new HTTP endpoint. This task widens one existing endpoint's rejection list and adds
domain/schema/authz primitives.

Domain (entities.py):
  Role.SUPERADMIN = "superadmin"   -- 7th Role value

Domain (authz.py — add `import uuid`):
  ROLE_PERMISSIONS[Role.SUPERADMIN] = frozenset(Permission)   -- full parity with OWNER

  def authorize_tenant_scope(identity: Identity, target_tenant_id: uuid.UUID) -> None
      -- raises AUTH_FORBIDDEN.exc() (403 ERR_AUTH_FORBIDDEN) unless
         identity.role == Role.SUPERADMIN or identity.tenant_id == target_tenant_id
      -- NOT called by any repository or endpoint yet (dormant by design — see §1 framing;
         platform-admin-console wires the first real caller)

Schema (orm.py + new migration, down_revision = 3fc2328e5e82 [platform-tenant-seed]):
  users.role CHECK widened:
    role IN ('owner','admin','operator','billing_admin','viewer','member','superadmin')

  NEW trigger `users_superadmin_platform_tenant_guard_fn` / `users_superadmin_platform_tenant_guard`
  (BEFORE INSERT OR UPDATE ON users, FOR EACH ROW — no column restriction, so a tenant_id-only
  UPDATE on an existing superadmin row is caught too, not just a role-column UPDATE):

    IF NEW.role = 'superadmin' THEN
      IF NOT EXISTS (SELECT 1 FROM tenants WHERE id = NEW.tenant_id AND kind = 'platform') THEN
        RAISE EXCEPTION 'superadmin_requires_platform_tenant: role=superadmin is only
                          permitted for the platform tenant';
      END IF;
    END IF;
    RETURN NEW;

HTTP (existing endpoint, rejection list widened — PUT /admin/users/{user_id}/role):
  body: { role: "superadmin" }
    422 -> { code: "ERR_PAYLOAD_INVALID" }
    -- same shape as any unparseable role literal, for EVERY caller regardless of role/tenant;
       never assignable via this generic, unaudited endpoint. All other existing behavior of
       this endpoint (owner/admin escalation policy, self-guard, the 6 pre-existing role
       values) is UNCHANGED.

Application (users_use_cases.py):
  AssignUserRoleUseCase.execute(new_role=Role.SUPERADMIN, ...) -> raises EscalationForbiddenError
    unconditionally, BEFORE the self-guard/escalation/repository calls.

Reject:
  users INSERT/UPDATE role='superadmin' with tenant_id != platform tenant
    -> DB exception 'superadmin_requires_platform_tenant' (trigger; no HTTP surface)
  users INSERT role NOT IN the 7 valid values -> DB check-violation (23514), unchanged
  PUT /admin/users/{id}/role {role:"superadmin"} -> 422 ERR_PAYLOAD_INVALID (every caller)
  AssignUserRoleUseCase.execute(new_role=SUPERADMIN) direct call -> EscalationForbiddenError
  authorize_tenant_scope(identity, target) where role != SUPERADMIN and target != identity.tenant_id
    -> AUTH_FORBIDDEN.exc() (403 ERR_AUTH_FORBIDDEN)
```

Status: FROZEN @ v1 — approved by Tin Dang
Least-sure flag surfaced at freeze:
⚠ [contract] `authorize_tenant_scope` ships now as dormant/unwired code (zero production callers
until `platform-admin-console` lands) rather than freezing only the rule as contract prose and
deferring the Python symbol to that future task — lowest confidence because it's a bet on a
not-yet-specified future task's needs, and it creates real tension with the ADD Verify gate's
DEAD-CODE check. Cost if wrong: delete the function + its 3 unit tests, replace with a prose-only
rule statement in §3 — cheap, contained; nothing downstream depends on the symbol yet.
⚠ [contract] A DB trigger is the PRIMARY (not just defense-in-depth) enforcement of "superadmin
only under the platform tenant" — the heavier of the two options weighed against application-
layer-only enforcement at the 3 enumerable write sites. Cost if wrong: drop the migration's
trigger + function + the 3 tests exercising it, keep only the `AssignUserRoleUseCase` guard as
sole enforcement — a real, viable, smaller fallback, not a rework.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of new code (one enum member, one permissions entry, one predicate
function, one CHECK-constraint widen, one trigger, two guard-clause edits)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_superadmin_role_exists_with_full_permissions: arrange none (pure) / act import authz /
    assert Role.SUPERADMIN in ROLE_PERMISSIONS + == frozenset(Permission) + Role.OPERATOR distinct
  - test_orm_accepts_superadmin_role_under_platform_tenant: arrange superadmin_guard_session +
    platform_tenant_id / act INSERT role='superadmin' at that tenant / assert commit succeeds,
    row reads back role='superadmin'
  - test_orm_rejects_unknown_role_value: arrange db_session + customer_tenant_id / act INSERT
    role='bogus_role' / assert IntegrityError sqlstate 23514 + zero rows
  - test_superadmin_insert_rejected_outside_platform_tenant: arrange superadmin_guard_session +
    customer_tenant_id / act INSERT role='superadmin' at that tenant / assert raises
    superadmin_requires_platform_tenant + zero rows
  - test_superadmin_update_rejected_outside_platform_tenant: arrange a member-role row in a
    customer tenant (guard applied) / act UPDATE role='superadmin' / assert raises
    superadmin_requires_platform_tenant + role still 'member'
  - test_migration_creates_widened_check_and_guard_trigger: arrange clean_migration_db / act
    alembic upgrade head, then raw-asyncpg INSERT superadmin@platform (succeeds) and
    INSERT superadmin@customer (raises) / assert both outcomes + exactly 1 superadmin row
  - test_assign_role_endpoint_rejects_superadmin_payload: arrange seeded owner + target user /
    act PUT .../role {"role":"superadmin"} / assert 422 ERR_PAYLOAD_INVALID + target role
    unchanged in DB
  - test_assign_user_role_use_case_rejects_superadmin_directly: arrange a repo stub that raises
    AssertionError if ever called / act AssignUserRoleUseCase.execute(new_role=SUPERADMIN) /
    assert EscalationForbiddenError, repo never reached
  - test_authorize_tenant_scope_allows_superadmin_cross_tenant: arrange a SUPERADMIN Identity
    (pure) / act call with a different target_tenant_id / assert no exception
  - test_authorize_tenant_scope_rejects_non_superadmin_cross_tenant: arrange each of the other
    6 roles (pure) / act call with a different target_tenant_id / assert ProblemError code
    ERR_AUTH_FORBIDDEN for each
  - test_authorize_tenant_scope_allows_same_tenant_for_any_role: arrange all 7 roles (pure) /
    act call with target_tenant_id == identity.tenant_id / assert no exception, for every role
  - test_signup_creates_owner_never_superadmin: arrange none / act POST /admin/auth/signup /
    assert created user's DB role == 'owner'
</test_plan>

Named regression guard (NOT a new test — the existing file, unmodified): `test_role_validation`
in `apps/gateway/tests/test_users_role.py` already asserts `{"role":"superadmin"}` -> 422
`ERR_PAYLOAD_INVALID`. This bundle's router-level Must is phrased specifically so that test keeps
passing byte-identically — re-confirmed by running the full suite at Build.

Tests live in: `apps/gateway/tests/superadmin_role/` · MUST run red (missing implementation)
before Build. RED reason: `Role.SUPERADMIN` does not exist yet (`ValueError`/`AttributeError` on
import), `authorize_tenant_scope` does not exist yet (`ImportError`), the CHECK constraint and
trigger do not exist yet.

RED CONFIRMED (2026-07-03, isolated DB): `uv run pytest tests/superadmin_role/ -v` → **7 failed,
5 passed, 1 error** — all 7 failures are the RIGHT reason (`AttributeError: Role.SUPERADMIN`
×2, `ImportError: authorize_tenant_scope` ×3, `CheckViolationError: users_role_check` on the
still-narrow CHECK ×1, missing migration ×1). The 5 passes are deliberate pre-existing-behavior
regression guards, not false greens: `test_orm_rejects_unknown_role_value` (the existing CHECK
already rejects bogus values), `test_superadmin_insert/update_rejected_outside_platform_tenant`
×2 (the `superadmin_guard_session` fixture creates the guard trigger directly via raw SQL — it
doesn't depend on Build, by design, so it already works), `test_assign_role_endpoint_rejects_
superadmin_payload` (the existing endpoint already 422s an unrecognized role literal). The 1
error is `tests/migrations/conftest.py`'s session-fixture teardown (`DROP DATABASE` on the
shared hardcoded migration-test-db name racing a concurrent worktree) — pure infra plumbing,
same documented class as platform-tenant-seed's Verify run, not a test-logic problem.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/domain/entities.py` ·
  `apps/gateway/src/gateway/tenants/domain/authz.py` ·
  `apps/gateway/src/gateway/tenants/infrastructure/orm.py` ·
  `apps/gateway/src/gateway/tenants/api/users_router.py` ·
  `apps/gateway/src/gateway/tenants/application/users_use_cases.py` ·
  `apps/gateway/migrations/versions/` · `apps/gateway/tests/superadmin_role/`
Strategy (ordered batches): 1. `entities.py`: add `Role.SUPERADMIN = "superadmin"`.
  2. `authz.py`: add `import uuid`; add `ROLE_PERMISSIONS[Role.SUPERADMIN] = frozenset(Permission)`;
  add `authorize_tenant_scope(identity, target_tenant_id) -> None`.
  3. `orm.py`: widen `users_role_check` to 7 values.
  4. New migration chaining after `3fc2328e5e82`: `drop_constraint`/`create_check_constraint`
  (mirror `b2d4f6a8c0e1`) + `CREATE OR REPLACE FUNCTION users_superadmin_platform_tenant_guard_fn`
  / `CREATE TRIGGER users_superadmin_platform_tenant_guard` (mirror `f2a4c6e8b0d3`); downgrade
  reverses both.
  5. `users_router.py::assign_user_role`: after the existing role-parse block, reject
  `new_role == Role.SUPERADMIN` with `PAYLOAD_INVALID.exc(...)`.
  6. `users_use_cases.py::AssignUserRoleUseCase.execute`: reject `new_role == Role.SUPERADMIN`
  as the FIRST guard clause.
  7. Write the red suite; confirm each test fails for the right reason before touching source.
  8. Run red to green; run the FULL existing suite (especially `test_users_role.py`,
  `rbac_roles/`, `platform_tenant_seed/`) to confirm zero regressions; run `alembic check`.
Known-problem fixes: Migration `down_revision` must point at `3fc2328e5e82` (platform-tenant-seed,
  now built) — the trigger's subquery needs `tenants.kind` to exist. CHECK-widen must use
  `op.drop_constraint`/`op.create_check_constraint` (not raw `ALTER TABLE`) to match Alembic's
  autogenerate parity. The router's new SUPERADMIN check must stay inside the 422-`ERR_PAYLOAD_INVALID`
  shape — if routed through `EscalationForbiddenError`/403 instead, `test_users_role.py::test_role_validation`
  (frozen, unmodified) breaks. `superadmin_guard_session` fixture must apply the trigger AFTER
  `create_all` has run. Trigger fires on `BEFORE INSERT OR UPDATE` with no column list.
Strategy actually used: exactly as planned, all 8 ordered batches, built by a background subagent
  (`backend-expert`, no worktree isolation — `platform-tenant-seed`'s dependency was still
  uncommitted on `main`, so a fresh worktree checkout would have lacked it) sharing a written
  context doc with the sibling `ops-platform-job-identity` build. Only deviation: the migration
  file's trigger-function SQL was extracted to a module-level `_TRIGGER_FN_SQL` constant (not in
  the strategy line, but harmless — improves downgrade-path readability, no behavior change).
  Every file independently re-reviewed line-by-line against §3 post-build (not just trusted from
  the agent's report): `entities.py`/`authz.py`/`orm.py` diffs read directly; the migration read in
  full (trigger fires BEFORE INSERT OR UPDATE with no column restriction, so a tenant_id-only
  UPDATE on an existing superadmin row is still caught; downgrade order is trigger→function→CHECK,
  matching the dependency order); `users_router.py`'s new guard confirmed BYTE-IDENTICAL
  (`grep`-verified) to the pre-existing `except ValueError` 422 shape, so `{"role": "superadmin"}`
  is indistinguishable from an unknown literal — no role-enumeration signal; `users_use_cases.py`'s
  guard confirmed as the FIRST clause, ahead of self-guard/escalation-guard, so even an OWNER
  acting on themselves can never mint a superadmin through this path.
Safety rule (feature-specific): the platform-tenant invariant is enforced at the DB level
  (trigger), independent of application code paths — never solely an app-layer guard.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

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

- [x] all tests pass — 12/12 target + 24/24 named regression, independently re-run by the orchestrator
- [x] coverage did not decrease — no lines removed from any pre-existing covered path; only new
  guard clauses (each hit by a dedicated test) and additive schema/authz surface added
- [x] no test or contract was altered during build — tripwire snapshot at tests→build crossing;
  `git diff apps/gateway/tests/` shows zero changes to any test file
- [x] the green was EARNED, not gamed — adversarial refute-read via dedicated subagent (security
  domain, given the elevated-privilege nature of this role) — see verdict below
- [x] concurrency / timing of the risky operation is safe — trigger is DB-level (BEFORE INSERT OR
  UPDATE, FOR EACH ROW), so enforcement is atomic within the inserting/updating transaction itself;
  no app-level race window between a role check and a write. TOCTOU/transaction-ordering vs.
  platform-tenant-seed's own migration explicitly covered by the adversarial subagent below.
- [x] no exposed secrets, injection openings, or unexpected dependencies — trigger SQL uses no
  string-interpolated user input (literal `'superadmin'`/`'platform'` only); `import uuid` is
  stdlib; no new third-party dependency
- [x] layering & dependencies follow CONVENTIONS.md — domain (`entities.py`/`authz.py`) stayed
  free of infra concerns; router → use-case → repository layering unchanged; guard duplicated
  at router+use-case intentionally (defense-in-depth, not a layering violation)
- [x] a person reviewed and approved the change — Tin Dang approved the §3 freeze; build diffs then
  independently reviewed line-by-line by the orchestrator (not just the build agent's self-report)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] all 12 new tests pass — confirmed TWICE: build agent's report, then independently re-run by
  the orchestrator (`GATEWAY_TEST_DATABASE_URL=...gateway_test_superadmin_verify uv run pytest
  tests/superadmin_role/ -v --no-cov` → `12 passed`), fresh isolated DB, not the agent's own DB
- [x] full suite 0 regressions, in particular `test_users_role.py::test_role_validation` still
  passes byte-identically — confirmed by orchestrator's own run of the 3 named adjacent suites:
  `tests/test_users_role.py tests/rbac_roles/ tests/platform_tenant_seed/` → `24 passed`
- [x] `uv run alembic check` reports no drift between ORM and migrated schema — confirmed by the
  orchestrator from a genuinely clean DB (`gateway_alembic_check_superadmin`, empty schema),
  `GATEWAY_DATABASE_URL=... alembic upgrade head` ran the FULL chain start-to-finish ending at
  `5b34ca5e1c4b`, then `alembic check` → "No new upgrade operations detected."
- [x] a live upgrade proves both outcomes: superadmin@platform succeeds, superadmin@customer
  raises `superadmin_requires_platform_tenant` — confirmed by reading
  `test_migration_creates_widened_check_and_guard_trigger` in full (not just its pass/fail): it
  runs the REAL migration chain, INSERTs via raw asyncpg (bypassing all app code), asserts the
  customer-tenant insert raises with `"superadmin_requires_platform_tenant"` in the message, and
  asserts a final `count(*) WHERE role='superadmin' == 1` (rules out silent partial success)
- [x] `authorize_tenant_scope` and `Role.SUPERADMIN`/`ROLE_PERMISSIONS[SUPERADMIN]` are
  reachable/importable and exercised by tests, even though `authorize_tenant_scope` has no
  production caller yet (dormant-by-design per §1 flag — recorded, not hidden) — exercised by
  `test_authorize_tenant_scope_allows_superadmin_cross_tenant`,
  `test_authorize_tenant_scope_rejects_non_superadmin_cross_tenant`,
  `test_authorize_tenant_scope_allows_same_tenant_for_any_role` (all 3 independently confirmed PASS)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `Role.SUPERADMIN` in the CHECK constraint, the
  trigger SQL literal, `ROLE_PERMISSIONS` (and required by the module's import-time completeness
  guard — import itself fails loudly if a `Role` member has no `ROLE_PERMISSIONS` entry), both
  guard clauses, and 9 of the 12 tests; `authorize_tenant_scope` referenced by 3 tests (see
  DEAD-CODE below for its production-caller status); the migration is the current alembic head
  (confirmed: `alembic upgrade head` from a clean DB ends at `5b34ca5e1c4b`)
- [x] DEAD-CODE (code) — `authorize_tenant_scope` has NO production caller yet — this is a
  DELIBERATE, flagged exception (§1 ⚠, carried into §3's freeze flag block), not an oversight;
  `platform-admin-console` (next milestone task) wires the first real caller
- [x] SEMANTIC (prose / non-code) — read in full: the migration's docstring (explains WHY the
  trigger has no `OF role` column restriction — an UPDATE that only touches `tenant_id` on an
  existing superadmin row must still be caught); the router guard's comment (explains why the 422
  shape must stay byte-identical to the unknown-literal case, naming the specific pre-existing test
  it would break otherwise); confirmed both explanations against the actual code and tests, not
  taken at face value

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self (orchestrator, independent re-run of all target+regression tests + full-chain alembic
  check from a clean DB — see Build Expectations above) AND a dedicated `security-expert` subagent
  (adversarial, read-only, no files modified — confirmed via post-review `git status`).
Adversarially checked: the subagent inventoried EVERY code path in `apps/gateway/src/` that can
  write `users.role` (via `mcp__serena` LSP reference-tracing on `UserRow`/`Role`, plus a repo-wide
  raw-SQL grep) — not just the two guarded paths named in the contract. Found: (1) both hardcoded
  role literals (`create_tenant_with_owner`→OWNER, OIDC auto-provision→MEMBER) have no `role`
  parameter in their signatures at all, so nothing can smuggle a value through; (2)
  `UserRoleRepository.update_role` — the sole production UPDATE of `users.role` — has exactly ONE
  caller in the whole codebase (`AssignUserRoleUseCase.execute`, confirmed via
  `find_referencing_symbols`), and that caller's SUPERADMIN guard is unconditional and first-in-
  function; (3) `team_members.role` is a same-named but separately CHECK-constrained column
  (`'lead'|'member'` only) — no overlap possible; (4) no CLI/Helm-hook/bootstrap script/second-
  service-with-DB-credentials exists anywhere that could mint the first superadmin outside the
  guarded paths; (5) TOCTOU between `platform-tenant-seed`'s migration and this task's trigger is
  unreachable — separate sequentially-committed Alembic revisions, a blocking `initContainer` runs
  the full chain before the app container starts, and the partial unique index on
  `kind='platform'` closes the "race a second platform tenant" variant even under same-transaction
  visibility. No REAL BYPASS found. Two THEORETICAL, non-blocking, out-of-this-task's-scope items
  surfaced and forwarded as spec deltas below (JWT role-claim trust; email-uniqueness squatting on
  a not-yet-built bootstrap flow). Confidence scores: completeness 0.93, correctness 0.95, trigger
  analysis 0.92 — all ≥0.9, no further probing required. Full report retained in session transcript.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract freeze, §3, "Freeze as drafted") + AI self-review (orchestrator,
  independent test re-runs + line-by-line diff review of all 6 changed files) + a `security-expert`
  subagent (adversarial refute-read, dedicated to this task given the elevated-privilege blast
  radius) · date: 2026-07-03

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): superadmin-role assignment attempts via the generic
endpoint (should be zero successes, ever) · trigger rejection rate on direct-SQL/ops paths

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: exactly as planned, all 8 ordered batches, built by a background subagent
- [AI] verify — gate PASS (reviewed by Tin Dang (contract freeze, §3, "Freeze as drafted") + AI self-review (orchestrator,)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] `platform-admin-console` (the next queued milestone task, which wires the first
  production caller of `authorize_tenant_scope`) must decide whether `Identity.role` needs to be
  re-validated against a live DB read rather than trusted from JWT claims alone at decode time
  (evidence: adversarial review of this task — `jwt_service.py`/`authz.py` construct `Identity`
  straight from signed-claim `role`, no DB check on the request path; issuance itself IS sound,
  decode-time trust is the open question; blast radius multiplies from one forged token = one
  tenant, to one forged token = every tenant, the moment a real caller exists).
- [SPEC · open] whichever task builds the actual first-superadmin bootstrap flow (`superadmin-login`
  per the roadmap) must also address that `users.email` is globally unique with no domain-ownership
  check, so the intended platform-admin email can be squatted by an ordinary signup before bootstrap
  ships (evidence: adversarial review of this task, confirmed `orm.py:79` + `POST /admin/auth/signup`
  have no protection against this; pre-existing design, unrelated to this task, but relevant to
  whoever ships the bootstrap).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [ADD · folded] §5's scope-lock snapshot is tree-wide, not per-task: running two sibling tasks' [folded foundation-version 44]
  Build phases concurrently in a shared, non-worktree-isolated tree causes each task's completing
  verify gate to flag the OTHER task's legitimate files as `scope_violation` (evidence: sibling
  `ops-platform-job-identity`'s `gate PASS` attempt was flagged for this task's migration +
  `users_router.py`, consumed 1/3 heal attempts). Recovery: pristine tree (clear build-artifact
  caches) then `add.py phase build <slug>` (re-snapshots current state) → `advance` → `gate PASS`,
  per task, back-to-back with no other file-touching activity between snapshot and gate. Matches
  the pre-existing `ADD scope-snapshot poisoning` memory gotcha — this is fresh, concrete evidence
  reinforcing it, worth folding into the foundation so future parallel-build waves plan around it
  upfront (either serialize the gate step, or accept the recovery cost knowingly).
- [TDD · folded] a build subagent's self-reported test/coverage numbers should be independently [folded foundation-version 44]
  reproduced, not just trusted, for any security-sensitive build — doing so here directly caught a
  tooling gotcha that would have gone unnoticed otherwise (evidence: `alembic.ini` hardcodes
  `sqlalchemy.url`; the real override env var is `GATEWAY_DATABASE_URL` not `DATABASE_URL`; my
  first two manual alembic-check attempts silently no-op'd against the wrong database and reported
  a false failure, caught only because the orchestrator re-ran every Build Expectations checkbox
  independently rather than transcribing the build agent's report)
