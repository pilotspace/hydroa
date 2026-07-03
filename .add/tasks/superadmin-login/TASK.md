# TASK: Superadmin login via /admin/auth JWT flow

slug: superadmin-login · created: 2026-07-03 · stage: production
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/api/router.py:36` — `router = APIRouter(prefix="/admin/auth", ...)`; `signup` (38-51), `login` (54-63), `me` (66-80). Existing, unmodified endpoints — this task's login mechanics ride entirely on `login`+`me`, read but not changed.
- `apps/gateway/src/gateway/tenants/application/use_cases.py:25-49` — `LoginUseCase.execute`: `user = await self._repository.get_user_by_email(email.lower())`, verifies via `hasher.verify(...)`, then `self._tokens.issue(user_id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email)` — fully generic over `role`, zero branching on role value. `GetIdentityUseCase.execute` (44-49) is a one-line `self._tokens.decode(token)`. Read in full; confirms independently (not just trusted from the prior sibling task's note) that zero code change is needed here.
- `apps/gateway/src/gateway/tenants/infrastructure/jwt_service.py:11-54` — `JwtTokenService.issue()`/`.decode()`: claims `{sub, tenant_id, role, email, iat, exp, iss}`; `role` round-trips via `str(role)` / `Role(claims["role"])`, no per-role logic anywhere. `Role.SUPERADMIN` (shipped by `superadmin-role`) is already a valid member, so issue/decode already work for it today.
- `apps/gateway/src/gateway/auth/api/oidc_admin_router.py:130-143` — `_get_owner_identity`: `if identity.role != Role.OWNER: raise AUTH_FORBIDDEN_OWNER_REQUIRED.exc()` (403 `ERR_AUTH_FORBIDDEN`). Literal equality, not permission-based. Read in full — confirmed a superadmin identity (`role == Role.SUPERADMIN`) is rejected here today, with zero change needed.
- `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py:97-112` — `_require_owner_identity`: identical `identity.role != Role.OWNER` literal gate, same rejection, same conclusion.
- `apps/gateway/src/gateway/core/error_catalog.py:80,86,126` — `AUTH_TOKEN_INVALID` (401 `ERR_AUTH_INVALID_TOKEN`), `AUTH_FORBIDDEN_OWNER_REQUIRED` (403 `ERR_AUTH_FORBIDDEN`), `AUTH_CREDENTIALS_INVALID` (401 `ERR_AUTH_INVALID_CREDENTIALS`) — the three existing error codes this task's scenarios exercise; none change.
- `apps/gateway/src/gateway/tenants/infrastructure/repository.py:14-28` — `get_platform_tenant(session) -> TenantRow | None` (shipped by `platform-tenant-seed`) — the one sanctioned way to resolve the platform tenant's id for provisioning a test superadmin row; returns `None`, never raises, if unmigrated/unseeded.
- `apps/gateway/tests/platform_tenant_seed/test_platform_tenant_seed.py:203-219` — `test_seeded_platform_tenant_has_no_owner_user`: asserts `count(*) FROM users WHERE tenant_id = <platform_id>` is `0` immediately after the seed migration runs — a DELIBERATE, already-tested guarantee that provisioning a platform-tenant user was explicitly kept OUT of that task. Directly informs this task's own scope position — see §1's flagged assumption.
- `apps/gateway/tests/test_users_role.py:75-139` — `seeded_users` fixture: signup+login creates a real tenant+owner via the HTTP API, then a SECOND user row is inserted directly via raw SQL (`INSERT INTO users (id, tenant_id, email, password_hash, role) VALUES (...)`) into that same tenant, with a placeholder `password_hash` (`'$argon2id$v=dummy'`) — sufficient there because that row is only ever used with `token_service.issue()` (a bypass), never a real `POST /login`. The idiomatic direct-SQL-insert pattern this task's own fixture will reuse, with two deltas: platform tenant instead of a signed-up one, and a REAL hash (below) since this task must exercise real login.
- `apps/gateway/tests/rbac_roles/test_rbac_roles.py:47-62` — `_issue_token(app, role_str)`: an even more minimal bypass — issues a JWT directly via `app.state.token_service.issue(...)` with random UUIDs, no DB row at all ("we only care about role-based auth"). Useful precedent for the reject-side scenarios (the two owner-literal gates), which need a valid superadmin JWT but no real login round-trip.
- `apps/gateway/src/gateway/main.py:631-634` — `app.state.password_hasher = Argon2PasswordHasher()`, `app.state.token_service = JwtTokenService(settings)`, both reachable from tests the same way (`app.state.X`) — confirms a REAL, loginable superadmin row can be built with `app.state.password_hasher.hash(<password>)`, unlike `seeded_users`'s dummy-hash shortcut.
- `apps/gateway/src/gateway/tenants/infrastructure/argon2_hasher.py:5-15` — `Argon2PasswordHasher.hash(password) -> str` — concrete impl backing the port above.
- `apps/gateway/src/gateway/tenants/domain/entities.py` — `Role.SUPERADMIN = "superadmin"` (shipped, `superadmin-role`); `Identity(user_id, tenant_id, email, role)` — unchanged, read to confirm shape.

Context (working folder):
- `.add/milestones/platform-identity/MILESTONE.md` — Scope In/Out, Shared decisions (byte-identical JWT claim-shape invariant), and this task's own Exit-criterion line, read in full.
- `.add/tasks/superadmin-role/TASK.md` §7 OBSERVE spec delta (~line 523-528) — names `users.email`'s global uniqueness / no domain-ownership check as relevant to "whichever task builds the actual first-superadmin bootstrap flow (`superadmin-login` per the roadmap)" — read in full; directly addressed by §1's flagged assumption below rather than silently dropped.
- `.add/GLOSSARY.md:28-29` — `platform operator`/`ops-auth` definitions, quoted verbatim in `superadmin-role`'s own naming-distinction note; confirmed `platform tenant`/`superadmin` are NOT YET synced into GLOSSARY.md itself (MILESTONE.md's Shared-decisions section is the current authoritative source for those two terms) — a pre-existing gap, not this task's to fix.

Honors (patterns / conventions):
- `PROJECT.md:25` — "Every tenant-owned row carries `tenant_id`; every query is tenant-scoped" — this task adds no new query path; `login`/`me`/the two owner-literal gates are unchanged code, exercised only with an additional role value.
- One test directory per task (`superadmin_role/` precedent) → `apps/gateway/tests/superadmin_login/`.
- `pyproject.toml:164` — `asyncio_mode = "auto"`, no `@pytest.mark.asyncio` needed (reused from `superadmin-role`'s grounding, re-confirmed still current).
- CLAUDE.md project rule — never weaken a test to make a build pass: not exercised here (grounding found no existing test needs to change), but binding if that changes.

Anchors the contract cites: `login`/`me` (router.py) · `LoginUseCase.execute`/`GetIdentityUseCase.execute` (use_cases.py) · `JwtTokenService.issue`/`.decode` (jwt_service.py) · `_get_owner_identity` (oidc_admin_router.py) · `_require_owner_identity` (provider_keys_admin_router.py) · `AUTH_FORBIDDEN_OWNER_REQUIRED`/`AUTH_TOKEN_INVALID`/`AUTH_CREDENTIALS_INVALID` (error_catalog.py) · `get_platform_tenant` (repository.py) · `Role.SUPERADMIN` (entities.py) · `Argon2PasswordHasher.hash` (argon2_hasher.py).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Superadmin login via the existing /admin/auth JWT flow — mechanics-only: prove the
already-generic login/JWT/me path round-trips `role=superadmin` correctly and that the two
existing owner-literal admin gates keep rejecting it. Production bootstrap of the FIRST
superadmin row is explicitly excluded — see the flagged assumption below.

Framings weighed: login-mechanics-only, test-fixture-provisioned row, zero production code
changes **(chosen)** · login-mechanics + a production bootstrap mechanism for the first
superadmin row (ops-mTLS-gated endpoint, or a migration-time seed) bundled into this same task
**(rejected for THIS task** — a security-sensitive, one-time/break-glass concern that deserves
its own deliberate Specify pass, not an afterthought riding on "login"; see flag**)** ·
login-mechanics + reopening the two owner-literal gates (`oidc_admin_router.py`,
`provider_keys_admin_router.py`) to also accept SUPERADMIN **(rejected** — MILESTONE.md's Out
section + this milestone's own Scope-In phrase "rejected on non-superadmin-gated surfaces" keep
this milestone's blast radius at zero new capability; granting superadmin real access to those
surfaces is `platform-admin-console`'s job, not this task's**)**.

Must:
<must>
  - `POST /admin/auth/login` with a superadmin user's correct email+password returns 200 with
    an `access_token`, using the byte-identical `LoginResponse` shape as every other role — zero
    new branching in `LoginUseCase`/`router.py::login`.
  - The returned JWT, decoded via `JwtTokenService.decode` / `GET /admin/auth/me`, round-trips
    `role="superadmin"` and a `tenant_id` equal to the platform tenant's id, with the claim set
    exactly `{sub, tenant_id, role, email, exp, iat, iss}` — the milestone's byte-identical
    claim-shape invariant holds for this role too, proven not assumed.
  - A valid superadmin JWT presented to `oidc_admin_router.py`'s OIDC-config endpoints and
    `provider_keys_admin_router.py`'s provider-key endpoints is rejected with 403
    `ERR_AUTH_FORBIDDEN` — pinning TODAY's already-true behavior (`identity.role != Role.OWNER`)
    as an explicit, tested regression guard, not new code.
  - `POST /admin/auth/login` with a superadmin email but the wrong password returns the same 401
    `ERR_AUTH_INVALID_CREDENTIALS` as any other role — indistinguishable in shape from a
    non-superadmin failed login (no role-existence signal leaked).
  - This task's own test suite provisions its superadmin test row directly (SQL insert under the
    platform tenant's id via `get_platform_tenant`, with a REAL Argon2 hash via
    `app.state.password_hasher.hash(...)`, mirroring `seeded_users`) — never via a production
    HTTP path, because none exists and none is built here (see flag).
  - The full existing auth/RBAC-adjacent suite (`test_users_role.py`, `rbac_roles/`,
    `platform_tenant_seed/`, `superadmin_role/`) stays green, 0 regressions.
</must>
Reject:
<reject>
  - `POST /admin/auth/login` with a superadmin email and the wrong password -> 401
    "ERR_AUTH_INVALID_CREDENTIALS" (byte-identical shape to any other role's wrong-password
    rejection; existing code, unchanged)
  - A valid superadmin JWT on `GET`/`PUT /admin/auth/oidc-config` -> 403 "ERR_AUTH_FORBIDDEN"
    (`AUTH_FORBIDDEN_OWNER_REQUIRED`; owner-literal gate, unchanged by this task)
  - A valid superadmin JWT on any provider-keys admin endpoint
    (`provider_keys_admin_router.py`) -> 403 "ERR_AUTH_FORBIDDEN" (same gate, same code)
</reject>
After:
<after>
  - A superadmin User row (however provisioned — test fixture here; production bootstrap
    explicitly deferred) can log in via the unmodified `/admin/auth/login` and receive a JWT
    whose role claim decodes to `superadmin`, with the claim shape byte-identical to every other
    role.
  - The two existing owner-literal gates continue to reject a superadmin identity exactly as
    they reject any non-owner role today — proven by an explicit test, not merely assumed.
  - Zero production source files change; this task's diff is tests-only (plus a new fixture
    module under `apps/gateway/tests/superadmin_login/`) — confirmed once §0's anchors are read
    as PROOF, not build targets.
  - The milestone's own Exit criterion for this task ("A superadmin logs in via /admin/auth and
    receives a JWT whose role claim decodes to superadmin; a non-superadmin JWT is rejected from
    any surface gated on the new role") is satisfied and evidenced by this task's tests.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Bootstrap/provisioning of the FIRST real superadmin User row in production is OUT of scope
  for this task — lowest confidence because it directly contradicts a named, on-the-record
  expectation from `superadmin-role`'s own §7 OBSERVE spec delta: "whichever task builds the
  actual first-superadmin bootstrap flow (`superadmin-login` per the roadmap) must also address"
  the `users.email` squatting risk (`.add/tasks/superadmin-role/TASK.md`, ~line 523). My position,
  reasoned independently from the code rather than deferring to that parenthetical: (a)
  `platform-tenant-seed`'s own test (`test_seeded_platform_tenant_has_no_owner_user`) proves the
  platform tenant was DELIBERATELY seeded with zero users — a prior, tested design choice to keep
  user-provisioning out of that task, reading as consistent milestone-wide intent rather than an
  oversight; (b) MILESTONE.md's Out section frames this whole milestone as
  "backend-identity-only... no new cross-tenant capability granted yet" and reserves
  admin-capability surfaces for `platform-admin-console` (the very next milestone); (c) login
  mechanics need genuinely ZERO production code (re-confirmed by reading `LoginUseCase`/
  `JwtTokenService` myself — already fully generic over `Role`); a bootstrap mechanism is a wholly
  separate, security-sensitive concern (one-time/break-glass semantics, who's authorized to run
  it, how the intended email is protected from squatting) that deserves its own deliberate
  Specify pass, not an afterthought bolted onto "login". If wrong: this task's build stays valid
  either way (login mechanics are needed regardless of who provisions the row), but is INCOMPLETE
  against the milestone's practical intent — cost is a follow-up task (or reopening this one's
  Specify) to add the actual provisioning mechanism plus the email-squatting mitigation carried
  forward from that spec delta; not a rework of anything built here.
  - [ ] The two owner-literal gates (`oidc_admin_router.py`, `provider_keys_admin_router.py`) are
    the ONLY "non-superadmin-gated surfaces" this task needs to pin — confirm; MILESTONE.md and
    the orchestrating session's brief name exactly these two, and no third owner-literal gate
    turned up in this session's grounding, but I reused `superadmin-role`'s codebase-wide `Role`
    reference sweep rather than re-running it myself — low cost either way, one more test if a
    third surface is named.
  - [ ] A test-fixture-provisioned superadmin row (direct SQL insert under the platform tenant,
    real Argon2 hash) is an acceptable stand-in for "a superadmin logs in" in the milestone's
    Exit-criteria sense, vs. requiring a live/production demonstration — confirm; consistent with
    how every sibling task in this milestone proves behavior, but the Exit criterion's wording
    doesn't explicitly say "test-provisioned is sufficient."
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: a superadmin logs in and receives a correctly-shaped JWT
  Given a User row exists with role=superadmin under the platform tenant, with a known password
    (provisioned directly by this task's own fixture, not via any production HTTP path)
  When POST /admin/auth/login is called with that email and the correct password
  Then the response is 200 with an access_token
  And decoding the token's claims yields role="superadmin" and tenant_id equal to the platform
    tenant's id
  And the claim set is exactly {sub, tenant_id, role, email, exp, iat, iss} — unchanged shape,
    byte-identical to every other role

Scenario: GET /admin/auth/me reflects the superadmin role
  Given a valid superadmin JWT from the scenario above
  When GET /admin/auth/me is called with that bearer token
  Then the response is 200 with role="superadmin" and tenant_id equal to the platform tenant's id

Scenario: a superadmin's wrong password is rejected identically to any other role
  Given a User row exists with role=superadmin under the platform tenant, with a known password
  When POST /admin/auth/login is called with that email and an incorrect password
  Then the response is 401 "ERR_AUTH_INVALID_CREDENTIALS"
  And no access_token is present in the response
  And the response shape is indistinguishable from a wrong-password attempt against any other
    role — no signal that the "superadmin" email exists or holds an elevated role

Scenario: a superadmin JWT is rejected by the OIDC admin config endpoint
  Given a valid superadmin JWT
  When GET /admin/auth/oidc-config is called with that bearer token
  Then the response is 403 "ERR_AUTH_FORBIDDEN"
  And no OIDC configuration is returned or changed
  And the existing owner-literal gate (_get_owner_identity) is unmodified by this task

Scenario: a superadmin JWT is rejected by the provider-keys admin endpoint
  Given a valid superadmin JWT
  When the list-provider-keys endpoint is called with that bearer token
  Then the response is 403 "ERR_AUTH_FORBIDDEN"
  And no provider key data is returned or changed
  And the existing owner-literal gate (_require_owner_identity) is unmodified by this task
```

</scenarios>

Structural claims (verified by code/test-suite review, not a runtime Gherkin scenario — same
convention `superadmin-role` used for its non-scenario Musts):
- This task's superadmin test row is provisioned exclusively by its own fixture (direct SQL
  insert under the platform tenant, real Argon2 hash) — confirmed by reading the fixture module
  itself at Build/Verify, not by a pass/fail HTTP scenario; there is no production path to assert
  against, by design (see §1 flag).
- The full existing auth/RBAC-adjacent suite (`test_users_role.py`, `rbac_roles/`,
  `platform_tenant_seed/`, `superadmin_role/`) stays green, 0 regressions — confirmed by running
  the full suite at Build/Verify, not a scenario of its own.

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new HTTP endpoint, no new domain/schema/application/infrastructure code. This task PINS
already-shipped, already-generic behavior for Role.SUPERADMIN across 3 existing surfaces,
purely via a new test suite (apps/gateway/tests/superadmin_login/) provisioning its own fixture
row. Zero src/ files are touched by this task's Build (see §1 flag for what is deliberately
excluded).

Existing endpoint (unchanged) — POST /admin/auth/login   body: { email, password }
  200 -> { access_token: <JWT>, expires_in: <int> }
    -- decoded claims: { sub, tenant_id, role: "superadmin", email, iat, exp, iss }
       (byte-identical shape to every other role; tenant_id == the platform tenant's id)
  401 -> { code: "ERR_AUTH_INVALID_CREDENTIALS" }
    -- wrong password for a superadmin email; identical shape to any other role's rejection

Existing endpoint (unchanged) — GET /admin/auth/me   bearer: <superadmin JWT>
  200 -> { user_id, tenant_id, email, role: "superadmin" }

Existing endpoint (unchanged, owner-literal gate) — GET/PUT /admin/auth/oidc-config
  bearer: <superadmin JWT>
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
    -- _get_owner_identity's `identity.role != Role.OWNER` check
       (oidc_admin_router.py:130-143); a superadmin is not literally OWNER, so this is TODAY's
       behavior, pinned by this task's tests, not built by it

Existing endpoint (unchanged, owner-literal gate) — provider-keys admin endpoints
  bearer: <superadmin JWT>
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
    -- _require_owner_identity's identical check (provider_keys_admin_router.py:97-112)

Schema: no new table, column, or migration. Test-fixture data only —
  INSERT INTO users (id, tenant_id, email, password_hash, role)
  VALUES (<uuid>, <platform tenant id from get_platform_tenant(session)>, <test email>,
          <real Argon2 hash via app.state.password_hasher.hash(...)>, 'superadmin')
  -- requires a platform tenant row to already exist (get_platform_tenant returns None,
     never raises, if unmigrated/unseeded — the fixture must handle that case, e.g. by seeding
     one directly for the fast create_all test schema, mirroring superadmin_role/'s
     superadmin_guard_session precedent of applying migration-only DDL/data manually)

Reject:
  POST /admin/auth/login {email: <superadmin>, password: <wrong>}
    -> 401 ERR_AUTH_INVALID_CREDENTIALS (existing, unchanged)
  GET/PUT /admin/auth/oidc-config with a valid superadmin bearer token
    -> 403 ERR_AUTH_FORBIDDEN (existing, unchanged)
  provider-keys admin endpoints with a valid superadmin bearer token
    -> 403 ERR_AUTH_FORBIDDEN (existing, unchanged)

Out of contract (explicitly, per the flag below): any endpoint or mechanism that CREATES a
production superadmin User row. None is specified, mocked, or frozen here — a deliberate scope
boundary, not an oversight.
```

Status: FROZEN @ v1 — approved by Tin Dang
Least-sure flag surfaced at freeze:
⚠ [spec] Bootstrap/provisioning of the first production superadmin User row is treated as OUT of
scope for this task (login/JWT mechanics only, against a test-fixture-provisioned row) — the
single lowest-confidence call in this bundle. It directly contradicts a named, on-the-record
expectation written by `superadmin-role`'s own adversarial-review §7 OBSERVE spec delta, which
calls superadmin-login "the actual first-superadmin bootstrap flow... per the roadmap"
(`.add/tasks/superadmin-role/TASK.md`, ~line 523). My independent read of the code favors
excluding it here: `platform-tenant-seed` deliberately seeded the platform tenant with ZERO users
(`test_seeded_platform_tenant_has_no_owner_user`, already tested and frozen), MILESTONE.md's Out
section reserves admin-capability surfaces for `platform-admin-console`, and login mechanics need
genuinely zero production code (confirmed: `LoginUseCase`/`JwtTokenService` are already fully
generic over `Role`). Cost if wrong: this task's build stays valid and useful either way (login
mechanics are needed regardless of who provisions the row), but ships INCOMPLETE against the
milestone's practical intent — the fix is a follow-up task (or reopening this one's Specify) to
add the actual provisioning mechanism, which must also carry forward superadmin-role's
email-squatting finding (`users.email` is globally unique, no domain-ownership check —
`orm.py:79` / `POST /admin/auth/signup`) so it isn't silently dropped. This needs Tin's explicit
call at freeze, not a default.
⚠ [contract] The two owner-literal gates named in this contract (`oidc_admin_router.py`,
`provider_keys_admin_router.py`) are assumed to be the ONLY "non-superadmin-gated surfaces" in
scope — inherited from `superadmin-role`'s codebase-wide `Role`-reference sweep, not
independently re-run by me this round. If a third owner-literal gate exists, it's a cheap
addition (one more scenario + test), not a rework.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: N/A in the usual src/-line sense (§3: zero src/ files change) — the real coverage
  target is SCENARIO coverage: one test per §2 scenario + both structural claims, 7/7.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_superadmin_login_returns_correctly_shaped_jwt: arrange a fixture-provisioned superadmin
    row / act POST /admin/auth/login with correct password / assert 200 + functional decode
    (role/tenant_id/email) + raw claim-set is exactly {sub,tenant_id,role,email,exp,iat,iss}
  - test_me_endpoint_reflects_superadmin_role: arrange a real login / act GET /admin/auth/me /
    assert 200 + role="superadmin" + tenant_id/email/user_id match
  - test_superadmin_wrong_password_rejected_identically: arrange 3 oracles (superadmin wrong pw,
    ordinary owner wrong pw, wholly unknown email) / act POST /admin/auth/login ×3 / assert all
    three return byte-identical 401 status+body — no role-existence signal leaked
  - test_superadmin_jwt_rejected_by_oidc_admin_config: arrange a superadmin JWT via issue()
    bypass / act GET+PUT /admin/oidc / assert both 403 ERR_AUTH_FORBIDDEN
  - test_superadmin_jwt_rejected_by_provider_keys_admin: arrange a superadmin JWT via issue()
    bypass / act GET /admin/provider-keys / assert 403 ERR_AUTH_FORBIDDEN
  - test_fixture_provisions_superadmin_row_via_direct_sql_under_platform_tenant (structural):
    assert the fixture's own INSERT statement is a bound-param direct-SQL insert, never a
    production HTTP path
  - test_fixture_uses_real_argon2_hash_not_dummy_placeholder (structural): assert a real
    Argon2PasswordHasher round-trips correctly and a dummy placeholder can never verify —
    proves WHY scenario 1's 200 is only reachable via a real hash, not merely asserts it
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./tests/`   <-- zero `src/` files; see §3, this task pins existing behavior only
Strategy (ordered batches): 1. write `apps/gateway/tests/superadmin_login/` fixture module
  (direct-SQL superadmin row under the platform tenant, real Argon2 hash via
  `app.state.password_hasher`); 2. write the 5 scenario tests + the 2 structural-claim checks;
  3. confirm red for the right reason (no fixture/tests exist yet); 4. confirm green — zero src/
  changes required, since the surfaces under test are already correct; 5. run the named regression
  suites (`test_users_role.py`, `rbac_roles/`, `platform_tenant_seed/`, `superadmin_role/`).
Known-problem fixes: `get_platform_tenant` returns `None` on the fast `create_all` test schema
  unless the platform tenant row is seeded manually first (mirror `superadmin_role/`'s
  `superadmin_guard_session` precedent of applying migration-only data manually in the fixture).
Strategy actually used: [AI] As planned (§5's ordered batches followed exactly), with two
  self-caught test-CONSTRUCTION bugs found on the first red→green run, neither a feature gap:
  (1) the wrong-password "unknown email" oracle used a `.invalid` TLD, which `LoginRequest.email`
  (Pydantic `EmailStr`) rejects as a reserved/non-deliverable domain — 422 before ever reaching
  `LoginUseCase`; switched to `.io`, consistent with every other test file in this codebase.
  (2) the OIDC-gate PUT body omitted the required `email_domains` field — FastAPI validates the
  Pydantic body before the handler runs, so this 422'd before `_get_owner_identity` was ever
  reached, proving nothing about the gate; added the field. Both fixes made the test assert what
  it always claimed to, never weakened an assertion. 7/7 green after; zero `src/` changes, exactly
  as §3 claimed. One contract-prose inaccuracy found and worked around, not silently: §2/§3 name
  the OIDC gate's path as `/admin/auth/oidc-config`; the router's real mounted prefix
  (`oidc_admin_router.py:45`, independently re-confirmed by the orchestrator via direct grep) is
  `/admin/oidc` — tests target the real route (the unambiguous §0 anchor), not the prose string.
  See §7 Spec delta.
Safety rule (feature-specific): N/A — no new IO/mutation path; this task only adds tests against
  already-shipped, already-atomic code.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 7/7 new (`apps/gateway/tests/superadmin_login/`) + 43/43 combined with
  the four named regression suites, re-run 3x for determinism, all green
- [x] coverage did not decrease — trivially true: zero `src/` lines changed, nothing to decrease
- [x] no test or contract was altered during build — the two fixes recorded in §5 "Strategy
  actually used" were to the build agent's OWN draft tests before any green run (fixing test
  CONSTRUCTION bugs — wrong TLD, missing required field — that made a test fail/422 for a reason
  unrelated to the feature under test), never a weakening of an already-passing assertion; the
  frozen §2/§3 text itself is untouched
- [x] the green was EARNED, not gamed — orchestrator manual review (not a subagent this round:
  zero production code changed, so the risk bar is lower than a src/ change): read the full test
  file directly, confirmed Scenario 3's triple-oracle byte-identical-response comparison and both
  structural-claim tests (esp. proving a dummy hash literally cannot verify, not just asserting
  it) are substantive, not vacuous; independently re-grepped both router prefixes myself rather
  than trusting the agent's self-reported discrepancy
- [x] concurrency / timing of the risky operation is safe — N/A, no new IO/mutation path; this
  task only adds read-path tests against already-shipped, already-atomic code (§5's own claim,
  confirmed true post-build)
- [x] no exposed secrets, injection openings, or unexpected dependencies — the fixture password
  is a `# noqa: S105`-annotated test constant, not a real secret; the SQL insert uses bound
  params throughout (`:id`/`:tid`/`:email`/`:password_hash`, confirmed by reading
  `_SUPERADMIN_INSERT_SQL` directly — no string interpolation); zero new third-party deps, only
  already-vendored `httpx`/`jwt`/`pytest`/`sqlalchemy`
- [x] layering & dependencies follow CONVENTIONS.md — test-only, one dir per task, mirrors the
  `superadmin_role/`/`rbac_roles/` precedent exactly
- [x] a person reviewed and approved the change — orchestrator (AI) review recorded above under
  `autonomy: auto`'s auto-gate-on-evidence model; Tin approved the contract itself at freeze
  ("Freeze as drafted") and is informed of this Build+Verify outcome in the same report as
  `superadmin-audit-foundation`'s

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] `POST /admin/auth/login` returns 200 + a correctly-shaped JWT for a superadmin — confirmed
  by `test_superadmin_login_returns_correctly_shaped_jwt`'s functional decode AND raw claim-set
  check (exactly `{sub,tenant_id,role,email,exp,iat,iss}`)
- [x] `GET /admin/auth/me` reflects `role=superadmin` — confirmed by
  `test_me_endpoint_reflects_superadmin_role`
- [x] a superadmin's wrong password is byte-identical-shape rejected, no existence/role signal
  leaked — confirmed by `test_superadmin_wrong_password_rejected_identically`'s 3-oracle
  comparison (superadmin / ordinary owner / wholly unknown email, all byte-identical 401)
- [x] both owner-literal gates reject a superadmin JWT with 403 `ERR_AUTH_FORBIDDEN` — confirmed
  by the two rejection tests, targeting the REAL mounted routes (`/admin/oidc`,
  `/admin/provider-keys`, independently re-verified via `grep -n "prefix=" ...` against source),
  not the contract's inaccurate prose path
- [x] zero `src/` files changed — confirmed by `git status --short -- apps/gateway/src` showing
  only pre-existing modifications from sibling tasks built earlier this session, nothing new

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] SEMANTIC (prose / non-code) — read the full test file
  (`apps/gateway/tests/superadmin_login/test_superadmin_login.py`, 362 lines) in full, not
  skimmed; confirmed against source: both owner-literal gates' real routes (`oidc_admin_router.py:45`,
  `provider_keys_admin_router.py:57`), the `email_domains`-required-field ordering subtlety
  (Pydantic body validation precedes the handler's own owner-check), and that the fixture's
  claimed "real Argon2 hash, never the dummy placeholder" is independently proven, not assumed
- WIRING/DEAD-CODE (code): N/A — zero new `src/` symbols introduced by this task, by design

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self (orchestrator manual review of the build subagent's full report + the actual test file
  + independent re-verification of the flagged route-path discrepancy against source) ·
  adversarially checked: (1) the `/admin/oidc` vs. contract-prose `/admin/auth/oidc-config`
  discrepancy — re-grepped both router prefixes directly rather than trusting the self-report;
  (2) scanned for vacuous/overfit assertions — none found, Scenario 3 and both structural tests
  go beyond status-code-only checks; (3) `git status` scope confirmation — only the declared new
  test directory touched, no `src/`/TASK.md/`add.py` state mutated by the build agent

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract freeze, §3, "Freeze as drafted") + AI self-review (orchestrator,
  this Verify) · date: 2026-07-03

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rejection rate on the two owner-literal gates for a
  superadmin identity should stay 100% (any drop is a security regression); this task's own
  7-test suite IS the monitor, re-run on every future change to `router.py`/`oidc_admin_router.py`/
  `provider_keys_admin_router.py`/`jwt_service.py`.

### Decisions (ADR)
- [AI] Build followed §5's planned batch order exactly; only deviation was fixing two
  self-caught test-construction bugs before the first green run (see §5 "Strategy actually used").
- [AI] Verify done via orchestrator manual review, not a dedicated adversarial-review subagent —
  judged proportionate since zero production code changed (nothing new could have introduced a
  vulnerability; this task only adds tests against already-shipped, unchanged surfaces).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] `platform-admin-console` (or whichever task next builds real superadmin
  provisioning) must carry forward the deferred first-superadmin bootstrap mechanism AND the
  `users.email` global-uniqueness/squatting risk this task's own §1 flag inherited from
  `superadmin-role`'s §7 — neither is addressed by this task, both remain genuinely open
  (evidence: §1 flag, this task's Out-of-contract clause).
- [SPEC · open] `superadmin-audit-foundation`'s Part B (the `/admin/auth` login-side audit hook,
  still pending) should target the REAL owner-gate routes (`/admin/oidc`, `/admin/provider-keys`)
  if it ever needs to reference them, not the prose path names used in earlier drafts (evidence:
  the discrepancy found and fixed by this task, see Competency delta below).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [SDD · folded] a frozen §2/§3's prose path string (`/admin/auth/oidc-config`) drifted from the [folded foundation-version 44]
  real mounted route (`/admin/oidc`, `oidc_admin_router.py:45`) even though the §0 GROUND anchor
  citing the exact file:line was correct throughout — the concrete anchor should be treated as
  more authoritative than a restated path string when drafting contract prose, and ideally the
  restated string should be generated FROM the anchor, not typed independently (evidence: this
  task's build agent caught it by cross-checking against `rbac_roles/test_rbac_roles.py`'s
  existing real-route usage, not by the contract text alone).
- [TDD · folded] two test-construction bugs (an `EmailStr`-invalid TLD; a required Pydantic field [folded foundation-version 44]
  omitted from a PUT body) both manifested as a 422 that would have made the test pass for the
  WRONG reason (validation failure, not the actual 403 gate check) had the assertion been looser
  (e.g. `assert resp.status_code != 200`) — writing the exact expected status+code catches this
  class of bug immediately; a looser assertion would have silently certified nothing (evidence:
  §5 "Strategy actually used", both fixes found on the first red→green run via exact-code asserts).
