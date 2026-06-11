# TASK: Teams within a tenant: CRUD, membership, roles, key attribution

slug: teams-core · created: 2026-06-11 · stage: production
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Teams within a tenant (CRUD, membership, roles, key→team attribution)

Framings weighed:
- **teams-as-governance-groups** (chosen): teams are an optional grouping layer inside a
  tenant for access organisation and upcoming budget governance; tenant stays the isolation
  boundary; team membership carries a `lead`|`member` role used by the upcoming
  team-governance task; key attribution is additive (nullable team_id on api_keys);
  budget precedence enforcement is OWNED BY team-governance, NOT by this task
- teams-as-isolation-units (rejected): would require cross-team authz checks in the proxy
  data path — violates the tenant-first isolation invariant and creates a cross-cutting
  dependency that blocks every downstream task

Must:
<must>
  - POST /admin/teams {name} creates a team scoped to the caller's tenant; returns 201 + team object
  - GET /admin/teams returns all teams for the caller's tenant with member_count and key_count aggregates
  - GET /admin/teams/{team_id} returns the team object plus a members list (each member: user_id, role, added_at)
  - DELETE /admin/teams/{team_id} soft-deletes the team (hard delete acceptable as members/keys cascade); keys attributed to the team get team_id NULL (ON DELETE SET NULL); members cascade; returns 204
  - POST /admin/teams/{team_id}/members {user_id, role} adds a user to a team with role "lead"|"member"; returns 201
  - DELETE /admin/teams/{team_id}/members/{user_id} removes a user from the team; returns 204
  - POST /admin/keys body accepts optional team_id; key is created attributed to that team; team_id appears in every key response (create/list/patch/rotate)
  - PATCH /admin/keys/{id} body accepts optional team_id to set or clear (null = un-attribute); returns updated key with team_id
  - All endpoints require owner or admin JWT; member role receives 403 ERR_AUTH_FORBIDDEN
  - Tenant isolation: team_id from a foreign tenant is invisible — responds 404 (not 403 — no existence leak) on attribution attempts or team lookups
  - Keys with team_id=null are fully backward compatible — all existing flows unaffected
  - A key attributed to a team whose team is deleted remains usable (team_id becomes NULL via SET NULL); proxy completion must succeed after team deletion
</must>

Reject:
<reject>
  - Duplicate team name within the same tenant -> "ERR_TEAM_EXISTS" (409)
  - Member-role JWT on any /admin/teams endpoint -> "ERR_AUTH_FORBIDDEN" (403)
  - Member-role JWT on POST /admin/keys or PATCH /admin/keys/{id} with team_id -> "ERR_AUTH_FORBIDDEN" (403)  [N.B. member JWT is already rejected by the existing require_owner_or_admin dep on keys create; no new enforcement needed — tested as confirmation]
  - team_id from a different tenant on POST /admin/keys or PATCH /admin/keys/{id} -> "ERR_TEAM_NOT_FOUND" (404)
  - team_id that does not exist (not just cross-tenant) on POST /admin/keys or PATCH /admin/keys/{id} -> "ERR_TEAM_NOT_FOUND" (404)
  - Adding a user_id not present in the tenant's users table -> "ERR_USER_NOT_FOUND" (404)
  - Adding the same user to the same team twice -> "ERR_MEMBER_EXISTS" (409)
  - Role value not in {"lead","member"} on member add -> "ERR_PAYLOAD_INVALID" (422)
  - GET/DELETE on a team_id that belongs to a different tenant -> 404 not 403 (no existence leak)
</reject>

After:
<after>
  - teams row exists with correct tenant_id, name, created_at
  - team_members row exists with correct team_id, user_id, role, added_at
  - api_keys.team_id column is set to the team's UUID when attributed, NULL when cleared or when team deleted
  - Deleting a team cascades member rows; keys attributed to it get team_id = NULL (not deleted)
  - GET /admin/teams lists the new team with correct member_count and key_count
  - GET /admin/teams/{team_id} returns the team plus members array
  - A proxy completion using a key whose team was deleted succeeds (key still active, team_id NULL)
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ key-governance PATCH body shape is FROZEN (fields: monthly_budget_usd, soft_budget_usd,
    expires_at, model_allowlist, rpm_limit, tpm_limit). Adding team_id to PATCH /admin/keys/{id}
    is ADDITIVE per the v3 pattern (key-governance extended the v1 keys contract additively).
    This assumption is load-bearing: if the key-governance contract is re-interpreted as
    "closed to extension", we need a separate endpoint — but CONVENTIONS.md §folded-v3 confirms
    the additive pattern. Confidence: 0.92; if wrong: introduce POST /admin/keys/{id}/team
    sub-resource instead. [spec]

  ⚠ user_id in POST /admin/teams/{team_id}/members must exist in the tenant's users table.
    The users table is tenant-scoped (tenant_id FK on UserRow). The test must arrange the
    user via signup (canonical route) or verify 404 when a random UUID is supplied.
    Confidence: 0.95; validation at DB constraint level catches cross-tenant UUID reuse because
    users.email is globally unique. [spec]

  - The team-governance task will add team_budget_usd column later; the teams table schema
    defined here must be forward-compatible (no constraints that block the additive column).
    Covered by the DDL having no structural blocker. [contract]

  - No dashboard surface for teams in v4 (declared out-of-scope in MILESTONE.md).
    Deferred: dashboard team management UI. [spec]

  - NO proxy-path enforcement in this task. team-governance owns: team_budget_usd, 402 semantics,
    spend rollup. This task installs the attribution column only. [spec]
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S1 — create team
  Given owner JWT for tenant A
  When POST /admin/teams {"name": "platform"}
  Then 201 + body has id, name="platform", tenant_id, created_at, member_count=0, key_count=0
  And teams row exists in DB with correct tenant_id

Scenario: S2 — duplicate team name within tenant rejected
  Given owner JWT for tenant A, team "platform" already exists
  When POST /admin/teams {"name": "platform"}
  Then 409 ERR_TEAM_EXISTS
  And teams table still has exactly one row named "platform" for tenant A

Scenario: S3 — list teams with aggregates
  Given owner JWT for tenant A, team "alpha" with 2 members and 1 attributed key
  When GET /admin/teams
  Then 200 + list contains team "alpha" with member_count=2, key_count=1

Scenario: S4 — get team with members
  Given owner JWT for tenant A, team "alpha" with 1 member (role="lead")
  When GET /admin/teams/{team_id}
  Then 200 + body has team fields + members=[{user_id, role="lead", added_at}]

Scenario: S5 — delete team cascades members, nulls key attribution
  Given owner JWT for tenant A, team with 1 member and 1 attributed key
  When DELETE /admin/teams/{team_id}
  Then 204
  And team_members row is gone
  And api_keys.team_id for the attributed key is NULL
  And the key's /internal/authz still returns 200 (key still active)

Scenario: S6 — key completes successfully after team deletion
  Given a key attributed to team T, a fake upstream returning 200
  When team T is deleted then POST /v1/chat/completions with that key
  Then 200 from proxy (key works; team_id is null; no budget check from deleted team)

Scenario: S7 — member role forbidden on team endpoints
  Given member-role JWT for tenant A
  When POST /admin/teams {"name": "x"}
  Then 403 ERR_AUTH_FORBIDDEN
  And no teams row created

Scenario: S8 — cross-tenant team invisible (create endpoint returns 404, not 403)
  Given owner JWT for tenant A; tenant B has team "b-team"
  When tenant A's JWT calls GET /admin/teams/{b_team_id}
  Then 404 not 403 (no existence leak)

Scenario: S9 — add member to team
  Given owner JWT for tenant A, team T, user U in tenant A
  When POST /admin/teams/{team_id}/members {"user_id": U, "role": "lead"}
  Then 201 + body has team_id, user_id, role="lead", added_at
  And team_members row exists

Scenario: S10 — add unknown user returns 404
  Given owner JWT for tenant A, team T
  When POST /admin/teams/{team_id}/members {"user_id": <random UUID>, "role": "member"}
  Then 404 ERR_USER_NOT_FOUND
  And no team_members row created

Scenario: S11 — duplicate member returns 409
  Given owner JWT for tenant A, team T, user U already a member
  When POST /admin/teams/{team_id}/members {"user_id": U, "role": "member"}
  Then 409 ERR_MEMBER_EXISTS
  And team_members table still has exactly one row for (T, U)

Scenario: S12 — remove member
  Given owner JWT for tenant A, team T, user U a member
  When DELETE /admin/teams/{team_id}/members/{user_id}
  Then 204
  And team_members row for (T, U) is gone

Scenario: S13 — create key with team attribution
  Given owner JWT for tenant A, team T
  When POST /admin/keys {"name": "k", "team_id": T_id}
  Then 201 + response body has team_id = T_id
  And api_keys.team_id = T_id in DB

Scenario: S14 — PATCH key sets team attribution
  Given owner JWT for tenant A, key K (team_id=null), team T
  When PATCH /admin/keys/{key_id} {"team_id": T_id}
  Then 200 + response body has team_id = T_id
  And api_keys.team_id = T_id in DB

Scenario: S15 — PATCH key clears team attribution
  Given owner JWT for tenant A, key K attributed to team T
  When PATCH /admin/keys/{key_id} {"team_id": null}
  Then 200 + response body has team_id = null
  And api_keys.team_id IS NULL in DB

Scenario: S16 — key attribution with foreign tenant team returns 404
  Given owner JWT for tenant A; tenant B has team T_B
  When POST /admin/keys {"name": "k", "team_id": T_B_id} with tenant A's JWT
  Then 404 ERR_TEAM_NOT_FOUND
  And no api_keys row created

Scenario: S17 — PATCH key attribution with nonexistent team returns 404
  Given owner JWT for tenant A, key K
  When PATCH /admin/keys/{key_id} {"team_id": <random UUID>}
  Then 404 ERR_TEAM_NOT_FOUND
  And api_keys.team_id unchanged

Scenario: S18 — invalid member role rejected
  Given owner JWT for tenant A, team T, user U
  When POST /admin/teams/{team_id}/members {"user_id": U, "role": "manager"}
  Then 422 ERR_PAYLOAD_INVALID
  And no team_members row created

Scenario: S19 — GET /admin/keys list carries team_id
  Given owner JWT for tenant A, key K attributed to team T
  When GET /admin/keys
  Then 200 + list item for K has team_id = T_id

Scenario: S20 — cross-tenant team on member add returns 404
  Given owner JWT for tenant A; team T_A in tenant A; owner JWT for tenant B calls add-member on T_A
  When POST /admin/teams/{T_A_id}/members with tenant B's JWT
  Then 404 (team not found in tenant B's scope — no leak)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/teams
  body: { "name": string (1–200 chars) }
  201 -> { "id": UUID, "name": string, "tenant_id": UUID, "created_at": ISO8601,
           "member_count": int, "key_count": int }
  409 -> { code: "ERR_TEAM_EXISTS",  status: 409, title: string }
  403 -> { code: "ERR_AUTH_FORBIDDEN", status: 403, title: string }
  422 -> { code: "ERR_PAYLOAD_INVALID", status: 422, title: string }

GET /admin/teams
  (no body)
  200 -> [ { "id": UUID, "name": string, "tenant_id": UUID, "created_at": ISO8601,
             "member_count": int, "key_count": int } ]
  403 -> { code: "ERR_AUTH_FORBIDDEN", status: 403 }

GET /admin/teams/{team_id}
  200 -> { "id": UUID, "name": string, "tenant_id": UUID, "created_at": ISO8601,
           "member_count": int, "key_count": int,
           "members": [ { "user_id": UUID, "role": "lead"|"member", "added_at": ISO8601 } ] }
  404 -> { code: "ERR_TEAM_NOT_FOUND", status: 404 }
  403 -> { code: "ERR_AUTH_FORBIDDEN", status: 403 }

DELETE /admin/teams/{team_id}
  204 (no body)
  404 -> { code: "ERR_TEAM_NOT_FOUND", status: 404 }
  403 -> { code: "ERR_AUTH_FORBIDDEN", status: 403 }

POST /admin/teams/{team_id}/members
  body: { "user_id": UUID, "role": "lead"|"member" }
  201 -> { "team_id": UUID, "user_id": UUID, "role": string, "added_at": ISO8601 }
  404 -> { code: "ERR_TEAM_NOT_FOUND" | "ERR_USER_NOT_FOUND", status: 404 }
  409 -> { code: "ERR_MEMBER_EXISTS", status: 409 }
  422 -> { code: "ERR_PAYLOAD_INVALID", status: 422 }
  403 -> { code: "ERR_AUTH_FORBIDDEN", status: 403 }

DELETE /admin/teams/{team_id}/members/{user_id}
  204 (no body)
  404 -> { code: "ERR_TEAM_NOT_FOUND" | "ERR_MEMBER_NOT_FOUND", status: 404 }
  403 -> { code: "ERR_AUTH_FORBIDDEN", status: 403 }

POST /admin/keys (ADDITIVE extension — existing fields unchanged)
  body: { ...(existing fields)..., "team_id"?: UUID | null }
  201 -> { ...(existing fields)..., "team_id": UUID | null }
  404 -> { code: "ERR_TEAM_NOT_FOUND", status: 404 }  (new rejection; existing codes unchanged)

PATCH /admin/keys/{id} (ADDITIVE extension — existing fields unchanged)
  body: { ...(existing fields)..., "team_id"?: UUID | null }
  200 -> { ...(existing fields)..., "team_id": UUID | null }
  404 -> { code: "ERR_TEAM_NOT_FOUND" | "ERR_KEY_NOT_FOUND", status: 404 }
```

DDL (additive, chaining after e7f3b2a9c4d1):

```sql
-- NEW tables
CREATE TABLE teams (
  id         UUID        PRIMARY KEY,
  tenant_id  UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name       TEXT        NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);

CREATE TABLE team_members (
  team_id   UUID        NOT NULL REFERENCES teams(id)  ON DELETE CASCADE,
  user_id   UUID        NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
  role      TEXT        NOT NULL CHECK(role IN ('lead', 'member')),
  added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (team_id, user_id)
);

-- ADDITIVE column on existing table
ALTER TABLE api_keys
  ADD COLUMN IF NOT EXISTS team_id UUID
    REFERENCES teams(id) ON DELETE SET NULL;
-- No NOT NULL, no server_default — existing rows/flows fully backward-compatible (null = un-teamed)
```

Rollback: DROP TABLE team_members, DROP TABLE teams,
          ALTER TABLE api_keys DROP COLUMN team_id
          (safe — all additive; no existing rows reference these objects pre-migration)

Modules touched (hard boundary):
  - NEW: `src/gateway/teams/` — clean-architecture module:
      `domain/` (entities: Team, TeamMember; errors: TeamNotFoundError, TeamExistsError,
                  MemberExistsError, MemberNotFoundError, UserNotFoundError; ports: TeamRepository)
      `application/` (use cases: CreateTeam, ListTeams, GetTeam, DeleteTeam,
                      AddMember, RemoveMember)
      `infrastructure/` (ORM: TeamRow, TeamMemberRow; repository: SqlAlchemyTeamRepository)
      `api/` (router: teams_router; schemas: CreateTeamRequest, TeamResponse,
              TeamDetailResponse, AddMemberRequest, MemberResponse; deps)
  - MODIFIED: `src/gateway/keys/infrastructure/orm.py` — add nullable team_id column
  - MODIFIED: `src/gateway/keys/api/schemas.py` — add optional team_id to
    CreateKeyRequest, PatchKeyRequest, CreateKeyResponse, KeyInfoResponse, RotateKeyResponse
  - MODIFIED: `src/gateway/keys/api/router.py` — pass team_id through create/patch
  - MODIFIED: `src/gateway/keys/application/use_cases.py` — accept + persist team_id;
    validate team belongs to tenant (hasattr seam for team repo lookup)
  - MODIFIED: `src/gateway/keys/api/deps.py` — thread team_repo into create/update use cases
  - MODIFIED: `src/gateway/main.py` — include teams_router
  - NEW: `migrations/versions/<rev>_teams_core.py` — additive migration (down_revision=e7f3b2a9c4d1)

Out of scope (boundary):
  - team_budget_usd column: team-governance task adds this additively; DDL here has no
    constraint that blocks it — forward-compatible confirmed
  - proxy-path budget enforcement: team-governance owns 402 semantics
  - /admin/spend rollup by team: team-governance
  - dashboard team UI: deferred (no v4 dashboard task for teams)
  - AuthzResult / proxy internals: untouched

Migration chain note:
  New revision down_revision = "e7f3b2a9c4d1" (tenant_model_overrides, current HEAD).

SANCTIONED FROZEN-TEST DISPOSITION (manifest maintenance — spend-windows/model-mgmt precedent):
  tests/migrations EXPECTED_TABLES manifest gains exactly two lines: "teams" and
  "team_members", each with an inline disposition comment referencing this §3 block.
  This is the ONLY permitted frozen-test edit; no other frozen test may be touched.

Forward-compat note for team-governance:
  teams table has no structural constraint blocking an additive
  `team_budget_usd NUMERIC(12,2) NULL` column. TeamRow ORM in this task must not
  declare __table_args__ that would conflict with that future column.

Status: FROZEN @ v4 — approved by Tin Dang (delegated auto mode, 2026-06-11)

Least-sure flag surfaced at freeze:
  ⚠ [contract] team_id rides the EXISTING /admin/keys create/PATCH bodies as an additive
    field (the settled v3 pattern: key-governance extended the v1 keys surface additively
    under its own frozen contract; this task does the same over key-governance). RESOLVED at
    freeze: additive extension approved — existing fields, codes, and frozen tests untouched;
    the new ERR_TEAM_NOT_FOUND rejection only fires when team_id is supplied. Cost if wrong
    (a frozen keys test turns out to pin the body as closed): change request back to SPECIFY
    for a POST /admin/keys/{id}/team sub-resource instead — never an in-build test edit.
  ⚠ [test] Cross-tenant isolation scenarios prove invisibility via 404 (no existence leak).
    If the build accidentally returns 403 for foreign-tenant team ids, an attacker could
    enumerate team existence across tenants; the suite pins 404 explicitly. Cost if wrong:
    tenant-isolation information leak — highest-severity failure class for this task.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of new `src/gateway/teams/` lines; existing suite must stay green.

Plan (one test per scenario):
<test_plan>
  - test_create_team: arrange signup+login / act POST /admin/teams / assert 201 + shape + DB row
  - test_create_team_duplicate_rejected: arrange existing team / act same name / assert 409 ERR_TEAM_EXISTS + no extra row
  - test_list_teams_with_aggregates: arrange team + member + attributed key / act GET / assert member_count=2 key_count=1
  - test_get_team_with_members: arrange team + member / act GET /{id} / assert members list shape
  - test_delete_team_cascades_and_nulls_keys: arrange team + member + attributed key / act DELETE / assert 204 + member gone + key.team_id NULL + key still authz-passes
  - test_key_completion_after_team_deletion: arrange attributed key + fake upstream / act delete team then POST /v1/chat/completions / assert 200 from proxy
  - test_member_role_forbidden: arrange member JWT / act POST /admin/teams / assert 403 ERR_AUTH_FORBIDDEN + no row
  - test_cross_tenant_team_returns_404: arrange two tenants / act tenant A GET tenant B's team / assert 404
  - test_add_member_to_team: arrange team + user / act POST members / assert 201 + shape + DB row
  - test_add_unknown_user_returns_404: arrange team / act with random UUID user / assert 404 ERR_USER_NOT_FOUND + no row
  - test_duplicate_member_returns_409: arrange member already added / act add again / assert 409 ERR_MEMBER_EXISTS + single row
  - test_remove_member: arrange member / act DELETE members/{user_id} / assert 204 + row gone
  - test_create_key_with_team_attribution: arrange team / act POST /admin/keys with team_id / assert 201 + team_id in response + DB
  - test_patch_key_sets_team_attribution: arrange key + team / act PATCH with team_id / assert 200 + team_id in response + DB
  - test_patch_key_clears_team_attribution: arrange attributed key / act PATCH team_id=null / assert 200 + team_id=null + DB
  - test_key_attribution_foreign_tenant_team_returns_404: arrange two tenants / act POST /admin/keys with cross-tenant team_id / assert 404 ERR_TEAM_NOT_FOUND + no row
  - test_patch_key_nonexistent_team_returns_404: arrange key / act PATCH with random team UUID / assert 404 ERR_TEAM_NOT_FOUND + team_id unchanged
  - test_invalid_member_role_rejected: arrange team + user / act add with role="manager" / assert 422 + no row
  - test_list_keys_carries_team_id: arrange attributed key / act GET /admin/keys / assert team_id in list item
  - test_cross_tenant_add_member_returns_404: arrange two tenants / act tenant B calls add-member on tenant A's team / assert 404
</test_plan>

Tests live in: `apps/gateway/tests/teams/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): team create + member add must be atomic in a single
transaction; team delete must atomically cascade members and SET NULL on keys within
the same transaction; key attribution validation (team belongs to tenant) must happen
inside the same transaction as the key write to avoid TOCTOU.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): ERR_TEAM_EXISTS rate · ERR_TEAM_NOT_FOUND rate on
attribution (indicates stale client team_id) · team member_count drift vs DB count ·
key null-team_id spike after mass deletion events

Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
