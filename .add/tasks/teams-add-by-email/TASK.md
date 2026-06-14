# TASK: Teams add member by email (gateway CR)

slug: teams-add-by-email · created: 2026-06-14 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): the Tin-approved gateway CR — `POST /admin/teams/{id}/members` accepts an `email` (server resolves to a user_id, tenant-scoped) in ADDITION to the current `user_id`. Verified the exact call chain + the reuse points:
- Schema `apps/gateway/src/gateway/teams/api/schemas.py:73-77` `AddMemberRequest { user_id: uuid.UUID; role: Literal["lead","member"] }` (frozen, strict=False → extra fields IGNORED today). → make `user_id` optional, add `email: str | None`, add an exactly-one-of `model_validator`.
- Router `teams/api/router.py:185-212` `add_member(team_id, body, identity, use_case)` passes `user_id=body.user_id`; maps `UserNotFoundError → USER_NOT_FOUND.exc()` (404 `ERR_USER_NOT_FOUND`), `TeamNotFoundError → TEAM_NOT_FOUND`, `MemberExistsError → MEMBER_EXISTS`. → also pass `email=body.email`.
- Use case `teams/application/use_cases.py:79-100` `AddMemberUseCase.execute(*, team_id, tenant_id, user_id, role)` is a thin pass-through to `repo.add_member`. → add `email` param, forward it.
- Repository `teams/infrastructure/repository.py:189-247` `add_member(*, team_id, tenant_id, user_id, role)` — inside `async with self._session.begin()`: team check (`:207-216`) → user-in-tenant check via `UserRow` (imported inline `:203` from `gateway.tenants.infrastructure.orm`, `:218-228`) → insert (`:231-239`, IntegrityError→MemberExistsError). → resolve `email.lower()`+`tenant_id` to a `UserRow` BEFORE the existing checks when `user_id is None`; no row → `UserNotFoundError`.
- Reuse: the users table is the tenants ORM `UserRow` (email column `unique=True`, lowercase-checked — `tenants/infrastructure/orm.py:58`); login resolves via `.lower()` (`tenants/application/use_cases.py:34`). Tenant-scope is enforced by the `UserRow.tenant_id == tenant_id` filter (cross-tenant email → no row → 404), so NO separate identity-repo dependency is needed — the teams session already queries `UserRow`.
- Errors: existing `UserNotFoundError` (`teams/domain/errors.py`) → router maps to `USER_NOT_FOUND` (404 `ERR_USER_NOT_FOUND`, `core/error_catalog.py:281`); request-validation failures → FastAPI handler → 422 `ERR_PAYLOAD_INVALID` (`core/errors.py:49`). No new error type.
- Tests (`apps/gateway/tests/teams/test_teams_core.py`): live Postgres(5433/`gateway_test`)+Redis(6380) via `infra/docker-compose.dev.yml` (now UP); `signup_and_login(client, tenant_name=, email=)` → (jwt, tenant_id); `create_team(client, jwt, name=)`; `auth_jwt(token)`; `assert_problem(resp, status, code)`; secondary users seeded via raw `INSERT INTO users`. `test_add_member_to_team:595` / `test_add_unknown_user_returns_404:650` / `test_invalid_member_role_rejected:1018` (422 ERR_PAYLOAD_INVALID) are the mirrors. Run: `uv run pytest tests/teams/test_teams_add_by_email.py --no-cov` (full gate: `make test`).

Context (working folder): the v15 MILESTONE.md add-by-email CR (Tin-approved 2026-06-14 exception to "no gateway change"); the teams module (DDD layering: api → application → infrastructure/domain). NO migration (the email column exists; resolution is application-layer, input-only).

Honors (patterns / conventions): CLAUDE.md (design for failure — atomic resolution inside the existing transaction; red/green TDD); CONVENTIONS.md + the teams module's clean layering (schema validates shape, use case orchestrates, repository owns the SQL + the tenant-scope); the existing add-member error mapping (reuse `UserNotFoundError`/`USER_NOT_FOUND`, no new code path in the router's except-ladder).

Anchors the contract cites: the EXTENDED `AddMemberRequest` (`user_id?`/`email?` + exactly-one-of) · the `add_member` email→user_id resolution (tenant-scoped `UserRow` lookup, `.lower()`) · the reused `UserNotFoundError`→404 `ERR_USER_NOT_FOUND` + 422 `ERR_PAYLOAD_INVALID` validation · the NEW `tests/teams/test_teams_add_by_email.py`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Teams add-member by email — `POST /admin/teams/{id}/members` accepts an `email` (resolved server-side to a user_id, tenant-scoped) as an alternative to a raw `user_id`, so the dashboard can add teammates by their known email without enumerating UUIDs. Additive, backward-compatible gateway CR.
Framings weighed: Resolve email→user_id inside `add_member` reusing the teams session's existing `UserRow` query (chosen — atomic in the same transaction, tenant-scope reused, no new dependency/layer) · Add a `GET /admin/users` list endpoint for a picker (rejected — a bigger surface + enumerates users; add-by-email is leaner + privacy-preserving) · Inject the tenants IdentityRepository into the teams use case (rejected — cross-module dependency + extra wiring for a query the teams repo can already run).

Must:
<must>
  - `AddMemberRequest` accepts EITHER `user_id: uuid` OR `email: str` (plus `role`), validated exactly-one-of (both → invalid; neither → invalid); `user_id` becomes optional.
  - When `email` is given, `add_member` resolves it tenant-scoped (`UserRow.email == email.lower() AND UserRow.tenant_id == tenant_id`) to a user_id BEFORE adding; resolution happens inside the existing atomic transaction.
  - An unknown email, or an email belonging to a DIFFERENT tenant, raises `UserNotFoundError` → 404 `ERR_USER_NOT_FOUND` (no team_members row created); tenant isolation is preserved (an admin cannot add a user from another tenant).
  - The existing `user_id` path is UNCHANGED (backward-compatible) — same 201 + response shape + the same TeamNotFound/UserNotFound/MemberExists behavior.
  - Owner/admin-only (the existing `require_owner_or_admin` gate is untouched); response is the unchanged `AddMemberResponse` (resolved `user_id` echoed). NO migration, NO new error code, NO new dependency; the full gateway suite stays green.
</must>
Reject:
<reject>
  - Request with BOTH `user_id` and `email`, or NEITHER -> "exactly_one_of" (422 ERR_PAYLOAD_INVALID)
  - An `email` that does not resolve to a user in the CALLER's tenant (unknown or cross-tenant) -> "user_not_found" (404 ERR_USER_NOT_FOUND)
  - Resolving email globally without the tenant filter (cross-tenant add) -> "tenant_leak"
  - A new migration / new error code / new dependency / a change to the user_id path's behavior -> "scope_creep"
</reject>
After:
<after>
  - `POST /admin/teams/{id}/members` with `{email, role}` adds the matching tenant user (201, resolved user_id echoed); unknown/cross-tenant email → 404; both/neither → 422; the `user_id` path is unchanged; full gateway suite green, no migration, no new dependency.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ stored emails are lowercase so `.lower()` resolution matches — lowest confidence because a mixed-case stored email would miss; MITIGATED by `tenants/infrastructure/orm.py:58` (`users_email_lowercase_check` constraint) + login resolving via `.lower()` (`tenants/application/use_cases.py:34`), so emails ARE stored lowercase. If wrong: a case-insensitive `func.lower(UserRow.email)` compare, caught by the happy-path test. Cost: one line.
  - [ ] the tenant-scoped `UserRow.tenant_id == tenant_id` filter alone enforces isolation (no separate cross-tenant guard needed) — CONFIRMED: email is globally unique but the filter returns no row for a foreign-tenant email → UserNotFoundError (the cross-tenant test pins this).
  - [ ] the `model_validator(mode="after")` exactly-one-of raises → FastAPI 422 ERR_PAYLOAD_INVALID — CONFIRMED by `core/errors.py:49` + existing `test_invalid_member_role_rejected` (422 ERR_PAYLOAD_INVALID).
  - [ ] no `EmailStr`/email-validator dependency needed — CONFIRMED: `email` is plain `str`; an unresolvable/malformed email simply 404s (no format-validation dep added).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Add member by email (happy path)
  Given an owner of tenant T and a user u with email "u@t.io" in tenant T
  When the owner POSTs { email: "u@t.io", role: "lead" } to /admin/teams/{id}/members
  Then it returns 201 with user_id = u's id and role "lead", and a team_members row exists

Scenario: Add member by user_id still works (backward compatible)
  Given an owner and a user u in tenant T
  When the owner POSTs { user_id: u.id, role: "member" }
  Then it returns 201 (the legacy path is unchanged)

Scenario: Unknown email is rejected
  Given an owner of tenant T
  When the owner POSTs { email: "ghost@nowhere.io", role: "member" }
  Then it returns 404 ERR_USER_NOT_FOUND and NO team_members row is created -> else "user_not_found"

Scenario: Cross-tenant email is rejected (isolation)
  Given an owner of tenant A and a user b with email "b@b.io" in a DIFFERENT tenant B
  When the owner of A POSTs { email: "b@b.io", role: "member" }
  Then it returns 404 ERR_USER_NOT_FOUND and b is NOT added to A's team -> else "tenant_leak"

Scenario: Both user_id and email is rejected
  Given an owner
  When the owner POSTs { user_id: <uuid>, email: "x@y.io", role: "member" }
  Then it returns 422 ERR_PAYLOAD_INVALID and NO row is created -> else "exactly_one_of"

Scenario: Neither user_id nor email is rejected
  Given an owner
  When the owner POSTs { role: "member" }
  Then it returns 422 ERR_PAYLOAD_INVALID -> else "exactly_one_of"
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# GATEWAY-API contract change (additive, backward-compatible). Freezes the new request shape
# + the resolution + error behavior of POST /admin/teams/{team_id}/members.

POST /admin/teams/{team_id}/members          (owner/admin-only — gate unchanged)
  body: AddMemberRequest {
    user_id?: uuid     # optional now
    email?:   string   # NEW — resolved server-side, tenant-scoped, lowercased
    role:     "lead" | "member"
  }
  VALIDATION: exactly one of {user_id, email} (model_validator) — both/neither -> 422 ERR_PAYLOAD_INVALID
  201 -> AddMemberResponse { team_id, user_id, role, added_at }   # UNCHANGED shape; resolved user_id echoed
  404 -> ERR_TEAM_NOT_FOUND   (team not in tenant)
  404 -> ERR_USER_NOT_FOUND   (user_id not in tenant, OR email unresolved / cross-tenant)
  409 -> ERR_MEMBER_EXISTS    (already a member)

RESOLUTION (in SqlAlchemyTeamRepository.add_member, inside the existing atomic transaction):
  if user_id is None:  # email path (schema guarantees exactly-one-of)
    row = SELECT UserRow WHERE email == :email.lower() AND tenant_id == :tenant_id
    if row is None: raise UserNotFoundError      # unknown OR cross-tenant
    user_id = row.id
  # then the EXISTING team-check / user-in-tenant-check / insert run unchanged

TOUCHED (4 files): teams/api/schemas.py (AddMemberRequest) · teams/api/router.py (pass email) ·
  teams/application/use_cases.py (AddMemberUseCase.execute +email) · teams/infrastructure/repository.py (add_member +email resolve)
Reject codes: exactly_one_of · user_not_found · tenant_leak · scope_creep
Schema: NO migration — `users.email` exists (unique, lowercase-checked); resolution is input-only, persists nothing new; the `team_members` insert is unchanged.
```

Status: FROZEN @ v1 — approved by Tin (delegated auto mode; the explicit add-by-email gateway CR Tin chose 2026-06-14)

**Least-sure flag surfaced at freeze:** `[spec]` — case-sensitivity of email resolution (`.lower()` vs the stored
value). *Why it's the riskiest call:* the whole feature hinges on the resolution matching the stored email; a
mixed-case or untrimmed stored email would silently 404 a valid teammate. *Cost if wrong:* a one-line change to
`func.lower(UserRow.email)` (case-insensitive compare), caught by the happy-path test — no contract change.
Mitigation: `users_email_lowercase_check` (orm.py:58) + login's `.lower()` (use_cases.py:34) confirm emails are
stored lowercase, so `.lower()` on input is correct; the §4 happy-path + cross-tenant tests pin it.
Second-most unsure `[contract]`: that tenant-scope is enforced solely by the `tenant_id` filter (no extra guard) —
the cross-tenant §4 test asserts a foreign-tenant email 404s, proving isolation.
<!-- EXIT: frozen + every spec rejection has a contracted response + the lowest-confidence flag surfaced. -->
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥ 80% line on the full gateway suite (held by `make test`; per-file runs use `--no-cov`). TRUE-RED reason: today `AddMemberRequest` has no `email` field and `user_id` is required → posting `{email, role}` (no user_id) → 422; the happy-path test expects 201, the unknown/cross-tenant tests expect 404 — all fail until Build adds the field + resolution. (The "neither" case is already 422 — a regression guard, not a red anchor; noted honestly.)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  In `apps/gateway/tests/teams/test_teams_add_by_email.py` (live-DB teams suite; mirrors test_teams_core helpers):
  - test_add_member_by_email: signup_and_login → create_team → INSERT a user with email in the tenant → POST {email, role:"lead"} → assert 201 + body.user_id == that id + role + a team_members row exists (RED today: 422)
  - test_add_member_by_user_id_still_works: INSERT user → POST {user_id, role:"member"} → assert 201 (backward-compat guard; already green, pins no-regression)
  - test_add_member_unknown_email_404: POST {email:"ghost@nowhere.io", role} → assert_problem 404 ERR_USER_NOT_FOUND + no row (RED today: 422)
  - test_add_member_cross_tenant_email_404: create user in tenant B → owner of tenant A POSTs {email: B-user-email} → assert_problem 404 ERR_USER_NOT_FOUND + B not added (RED today: 422; ISOLATION)
  - test_add_member_both_user_id_and_email_422: POST {user_id, email, role} → assert_problem 422 ERR_PAYLOAD_INVALID + no row (RED today: email ignored → 404, not 422)
  - test_add_member_neither_422: POST {role} only → assert_problem 422 ERR_PAYLOAD_INVALID (guard; already 422)
</test_plan>

Tests live in: `test_teams_add_by_email.py` · MUST run red (no email field / resolution) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/teams/api/schemas.py` `apps/gateway/src/gateway/teams/api/router.py` `apps/gateway/src/gateway/teams/application/use_cases.py` `apps/gateway/src/gateway/teams/infrastructure/repository.py` `apps/gateway/src/gateway/teams/domain/ports.py` `apps/gateway/tests/teams/` `.add/tasks/teams-add-by-email/` `apps/gateway/.coverage` `apps/gateway/.ruff_cache/`
<!-- SCOPE NOTE (tooling artifacts): `apps/gateway/.coverage` + `apps/gateway/.ruff_cache/` are
     transient build/lint caches regenerated by the verify-step tooling (pytest-cov + ruff), not
     source. Declared so the §5 scope gate (touched ⊆ declared) does not red on the unavoidable
     side-effect of running the suite during verify. Same precedent as the dashboard's
     coverage/ / .next / tsbuildinfo. NOT committed (staging is path-specific; both are gitignored). -->

<!-- 5 teams files (schema + router + use_case + repository + the domain port) + the NEW test file.
     SCOPE NOTE (build-time correction): teams/domain/ports.py (the TeamRepository Protocol) also
     needed the email param — pyright reportCallIssue on the use-case call; the port must reflect the
     new capability. Added during build; NOT a contract change (§3 unchanged). NO migration, NO
     change to the tenants module, NO new error code, NO new dependency, NO change to the user_id
     path's behavior or the require_owner_or_admin gate — touching those is scope_creep. -->
Strategy (ordered batches): 1. RED test `tests/teams/test_teams_add_by_email.py`. 2. schemas.py (optional user_id + email + exactly-one-of model_validator). 3. repository.add_member (email→user_id resolution inside the txn). 4. use_cases + router (thread `email` through). 5. run the teams suite + full `make test` green.
Safety rule (feature-specific): resolve email→user_id INSIDE the existing atomic transaction, tenant-scoped (`tenant_id` filter) + lowercased; never resolve globally (tenant isolation); the user_id path stays byte-for-byte unchanged.
Code lives in: `apps/gateway/src/gateway/teams/`
Constraints: do NOT change any test or the contract; allow-list packages only (none added); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 737 passed, 19 deselected (full gateway suite, 108s); the 7 add-by-email tests green in isolation (1.65s)
- [x] coverage did not decrease — 83.20% TOTAL, gate held at 80%
- [x] no test or contract was altered during build — §3 frozen; the tests→build tripwire re-snapshotted clean on each re-cross
- [x] the green was EARNED, not gamed — adversarial refute-read (subagent, sonnet) returned **EARNED-WITH-GAPS, ISOLATION-SAFE**; the two actionable gaps closed (whitespace-email boundary test + `_seed_user` lowercase guard); redundant resolution query kept by design (defense-in-depth)
- [x] concurrency / timing of the risky operation is safe — email→user_id resolution runs INSIDE the existing `async with self._session.begin()` atomic txn, tenant-scoped; no read-then-write race window introduced
- [x] no exposed secrets, injection openings, or unexpected dependencies — resolution uses a parameterized SQLAlchemy `select` (no string interpolation); no key/secret/JWT touched; zero new packages
- [x] layering & dependencies follow CONVENTIONS.md — api→application→infrastructure/domain preserved; the Protocol port (domain) updated to reflect the new capability; no module-boundary leak
- [x] reviewed under `autonomy: auto` — adversarial subagent + manual review; security clean (tenant isolation enforced by defense-in-depth, cross-tenant resolution returns 404)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `email` threaded end-to-end: router `add_member` passes `email=body.email` → `AddMemberUseCase.execute(email=...)` → `repo.add_member(email=...)` → tenant-scoped resolve; `AddMemberRequest.email` + `_exactly_one_identifier` validator exercised by the 422 tests; `TeamRepository.add_member` Protocol signature carries `email`
- [x] DEAD-CODE (code) — no orphaned symbol; every new field/param is referenced on the live request path and asserted by a test
- [x] SEMANTIC (prose / non-code) — n/a (code change)

### GATE RECORD
Outcome: PASS
Evidence: 737 passed / 83.20% cov · pyright 0 errors · ruff clean · adversarial refute-read EARNED-WITH-GAPS + ISOLATION-SAFE, both actionable findings closed
Reviewed by: ADD auto-gate (adversarial subagent + manual) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of 404 ERR_USER_NOT_FOUND on /members (typo'd or cross-tenant emails) vs 201; 422 ERR_PAYLOAD_INVALID rate (both/neither identifier — a UI-shape smell if non-trivial)
Spec delta for the next loop: the add-by-email path is the contract the `/teams` UI (teams-governance-ui) will drive — the member-add dialog sends `{email, role}`, never raw user_id; surface the 404 as "no user with that email in your tenant".

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [DDD · open] additive identity-resolution (email→user_id) belongs in the repository inside the existing txn, tenant-scoped, with defense-in-depth (resolve filter + team-membership check both enforce isolation) — evidence: cross-tenant email returns 404 even if either guard alone were removed
- [SDD · open] an "exactly-one-of" optional-identifier contract is cleanly expressed as a Pydantic `@model_validator(mode="after")` + `str_strip_whitespace`, so whitespace-only collapses to "absent" — evidence: test_add_member_whitespace_email_422, test_add_member_{both,neither}_422
- [ADD · open] a build-time port (Protocol) signature change is a legitimate scope correction (not a contract change) when pyright forces it to reflect a new capability — evidence: TeamRepository.add_member gained `email` to clear reportCallIssue; §3 unchanged
