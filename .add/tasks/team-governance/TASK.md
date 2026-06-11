# TASK: Team budget enforcement + team spend rollup

slug: team-governance · created: 2026-06-11 · stage: production
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Team budget enforcement on the proxy path + team spend rollup in /admin/spend

Framings weighed:
- **team-budget-as-governance-layer** (chosen): the team budget is an advisory Redis counter
  check inserted in the existing most-specific-wins precedence chain (key < team < tenant),
  fail-OPEN on Redis errors, same pattern as key and tenant guards; team spend is a second
  advisory counter incremented at the same write-point as the per-key counter; /admin/spend
  gains an additive group_by=team_id breakdown via JOIN on api_keys (current-team attribution
  — see Assumptions); DDL is a single additive column on the teams table.
- team-budget-as-hard-DB-limit (rejected): synchronous DB budget check per request adds
  latency and a DB round-trip on the hot path; the advisory counter pattern (fail-OPEN) is
  the v3 precedent and is explicitly carried in the v4 shared decisions.
- separate team-spend table (rejected): a team_spend_records table would add a DB write on
  every completion; the Redis counter pattern (same as key/tenant) is additive, consistent,
  and already proven fail-OPEN.

Must:
<must>
  - PATCH /admin/teams/{team_id} body {"team_budget_usd": string|null} sets or clears the team
    monthly budget; returns 200 with the full team object including team_budget_usd; requires
    owner or admin role (403 for member).
  - GET /admin/teams and GET /admin/teams/{team_id} responses include team_budget_usd (null when
    not set).
  - AuthzResult gains team_id: UUID|None and team_budget_usd: Decimal|None; the AuthzUseCase
    populates them in the same DB query via LEFT JOIN teams ON api_keys.team_id = teams.id —
    zero extra DB reads on the hot path (v3 zero-extra-DB-reads pattern, M12).
  - _enforce_governance inserts a team budget check AFTER the per-key budget check and BEFORE
    the tenant budget check (full enforcement order: expiry → allowlist → catalog → per-key
    budget → team budget → tenant budget → rate limits).
  - Team budget check: if authz.team_id is set AND authz.team_budget_usd is set, read Redis
    counter usage:spend:team:{team_id}:{YYYYMM}; if spent >= team_budget_usd → 402
    ERR_BUDGET_EXCEEDED with detail naming the team scope; fail-OPEN on Redis errors.
  - Enforcement semantics: a key with team_id set is ALWAYS subject to team budget enforcement
    if the team has team_budget_usd set, regardless of whether the key itself has a monthly_budget_usd.
    A key hard budget still fires FIRST (most-specific-wins tightest cap rejects); a key that
    only has soft_budget_usd hits team and then tenant checks. Un-teamed keys (team_id=null)
    are completely unaffected by team budget enforcement.
  - Counter increment: wherever usage:spend:key:{key_id}:{YYYYMM} is INCRBYFLOAT'd, also
    INCRBYFLOAT usage:spend:team:{team_id}:{YYYYMM} when the key has a team (same TTL/format
    conventions, same fail-OPEN guarantees).
  - /admin/spend?group_by=team_id returns a breakdown list with items of shape:
    {team_id: UUID|null, team_name: str|null, requests: int, prompt_tokens: int,
     completion_tokens: int, cost_usd: str}. Un-teamed keys (api_keys.team_id IS NULL) roll
    into a single NULL-team bucket (team_id null, team_name null). Results ordered by
    cost_usd DESC, NULL-team bucket last.
  - The /admin/spend group_by=team_id rollup reconciles exactly with usage_records via
    JOIN api_keys ON usage_records.key_id = api_keys.id (current-team attribution).
  - Invalid group_by values (other than "key_id" or "team_id") → 422 ERR_PAYLOAD_INVALID.
  - Teams from other tenants cannot be PATCH'd — 404 (no existence leak), consistent with
    teams-core.
  - team_budget_usd must be a valid positive numeric string when set; negative or non-numeric
    values → 422 ERR_PAYLOAD_INVALID.
</must>

Reject:
<reject>
  - PATCH /admin/teams/{team_id} with member-role JWT → "ERR_AUTH_FORBIDDEN" (403)
  - PATCH /admin/teams/{team_id} with team_id belonging to a different tenant → 404 (no leak)
  - PATCH /admin/teams/{team_id} with team_budget_usd = "-5.00" (negative) → "ERR_PAYLOAD_INVALID" (422)
  - PATCH /admin/teams/{team_id} with team_budget_usd = "abc" (non-numeric) → "ERR_PAYLOAD_INVALID" (422)
  - GET /admin/spend with group_by="user_id" (not in whitelist) → "ERR_PAYLOAD_INVALID" (422)
  - Proxy completion with a key attributed to a team whose team counter >= team_budget_usd → "ERR_BUDGET_EXCEEDED" (402)
</reject>

After:
<after>
  - teams.team_budget_usd column exists and is set to the supplied value when PATCH succeeds;
    null when cleared.
  - GET /admin/teams and GET /admin/teams/{team_id} responses carry team_budget_usd.
  - AuthzResult populated with team_id and team_budget_usd in the same DB query (no second
    SELECT).
  - A completion request using a key in a team with a full counter returns 402 with
    ERR_BUDGET_EXCEEDED; an un-teamed sibling key in the same tenant returns 200.
  - A successful completion increments BOTH usage:spend:key:{key_id}:{YYYYMM} AND
    usage:spend:team:{team_id}:{YYYYMM} when the key has team_id set.
  - A key with a harder per-key budget still fires the per-key 402 before the team check.
  - /admin/spend?group_by=team_id returns breakdown items reconciled exactly with
    usage_records via current-team JOIN; un-teamed keys appear in the NULL-team bucket.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ CURRENT-TEAM ATTRIBUTION TRADEOFF (load-bearing decision): /admin/spend?group_by=team_id
    reconciles via JOIN api_keys ON usage_records.key_id = api_keys.id, using the key's
    CURRENT team_id at query time. This means: if a key is reassigned to a different team
    (or un-teamed) after a completion, historical rows re-attribute to the new team. This is
    the simpler, no-schema-change option chosen for v4 (no usage_records.team_id column added
    to the hot ledger). The deferred alternative (historical snapshot: add a team_id column to
    usage_records at write time) would require a schema change to a high-write table and is
    explicitly deferred to v5 or a dedicated migration if attribution accuracy becomes a
    product requirement. DECISION: current-team attribution via JOIN for v4. Confidence: 0.90;
    if wrong: add usage_records.team_id column in v5 with backfill. [spec]

  ⚠ AuthzResult extension pattern: adding team_id + team_budget_usd to AuthzResult via the
    additive default=None pattern (v3 precedent) is safe only if the AuthzUseCase can populate
    them in the SAME SELECT that resolves the api_keys row. This requires a LEFT JOIN teams
    ON api_keys.team_id = teams.id in the authz query. The current AuthzUseCase uses
    get_by_id() on the repository (a simple SELECT on api_keys). Populating team_budget_usd
    requires either (a) extending get_by_id() to LEFT JOIN teams, or (b) a second SELECT.
    The contract mandates (a) — zero extra DB reads. This means the ApiKeyRepository
    get_by_id() must be extended with a JOIN. Confidence: 0.92; load-bearing for the
    zero-extra-DB-reads constraint. [contract]

  - The spending counter TTL for usage:spend:team: follows the same convention as
    usage:spend:key: — no explicit TTL set (data lives indefinitely unless evicted by Redis
    policy). This matches the existing key and tenant counter behavior. [spec]

  - PATCH /admin/teams/{team_id} is an additive PATCH endpoint on the teams resource.
    teams-core only implemented GET/POST/DELETE; this task adds PATCH. The PATCH body
    is specifically limited to {"team_budget_usd": str|null} — other team fields (name)
    are not patchable here (separate concern). Confidence: 0.95. [contract]

  - No new table is created by this task. The only DDL is:
    ALTER TABLE teams ADD COLUMN team_budget_usd NUMERIC(12,2) NULL;
    No manifest edit to EXPECTED_TABLES is needed (the teams table already exists).
    [contract]

  - team_budget_usd validation: the API accepts a Decimal-parseable string or null.
    Validation is a Pydantic field constraint; negative values are rejected by a
    gt=0 constraint. [spec]
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S1 — PATCH team sets team_budget_usd
  Given owner JWT for tenant A, team T
  When PATCH /admin/teams/{T.id} {"team_budget_usd": "100.00"}
  Then 200 + body has team_budget_usd = "100.00" + id, name, tenant_id, created_at, member_count, key_count
  And teams.team_budget_usd = 100.00 in DB

Scenario: S2 — PATCH team clears team_budget_usd
  Given owner JWT for tenant A, team T with team_budget_usd set to "100.00"
  When PATCH /admin/teams/{T.id} {"team_budget_usd": null}
  Then 200 + body has team_budget_usd = null
  And teams.team_budget_usd IS NULL in DB

Scenario: S3 — GET /admin/teams includes team_budget_usd
  Given owner JWT for tenant A, team T with team_budget_usd = "50.00"
  When GET /admin/teams
  Then 200 + list item for T has team_budget_usd = "50.00"

Scenario: S4 — GET /admin/teams/{id} includes team_budget_usd
  Given owner JWT for tenant A, team T with team_budget_usd = "50.00"
  When GET /admin/teams/{T.id}
  Then 200 + body has team_budget_usd = "50.00"

Scenario: S5 — teamed key hits 402 when team counter >= cap; un-teamed sibling proceeds
  Given owner JWT for tenant A
    And team T with team_budget_usd = "10.00"
    And key K_team attributed to team T (no per-key budget)
    And key K_solo un-teamed (no per-key budget)
    And Redis counter usage:spend:team:{T.id}:{YYYYMM} = "10.00" (seeded directly)
    And a fake upstream returning 200
  When POST /v1/chat/completions with K_team
  Then 402 ERR_BUDGET_EXCEEDED (detail mentions team scope)
  When POST /v1/chat/completions with K_solo
  Then 200 from proxy

Scenario: S6 — per-key hard budget fires BEFORE team budget
  Given owner JWT for tenant A
    And team T with team_budget_usd = "100.00"
    And key K attributed to team T with monthly_budget_usd = "5.00"
    And Redis counter usage:spend:key:{K.id}:{YYYYMM} = "5.00" (seeded, key cap full)
    And Redis counter usage:spend:team:{T.id}:{YYYYMM} = "0.00" (team cap empty)
    And a fake upstream returning 200
  When POST /v1/chat/completions with K
  Then 402 ERR_BUDGET_EXCEEDED
  And the detail mentions key scope (not team scope)
  And upstream was NOT called

Scenario: S7 — team budget does not affect keys of OTHER teams or un-teamed keys in same tenant
  Given owner JWT for tenant A
    And team T1 with team_budget_usd = "10.00"
    And team T2 with team_budget_usd = "10.00"
    And key K1 attributed to T1
    And key K2 attributed to T2
    And Redis counter usage:spend:team:{T1.id}:{YYYYMM} = "10.00" (T1 cap full)
    And Redis counter usage:spend:team:{T2.id}:{YYYYMM} = "0.00"
    And a fake upstream returning 200
  When POST /v1/chat/completions with K2
  Then 200 from proxy (T1 cap has no effect on K2)

Scenario: S8 — team budget cross-tenant isolation
  Given tenant A (team T_A capped) and tenant B (key K_B un-teamed, no cap)
    And Redis counter usage:spend:team:{T_A.id}:{YYYYMM} = "10.00" (T_A capped)
    And a fake upstream returning 200
  When POST /v1/chat/completions with K_B
  Then 200 from proxy

Scenario: S9 — counter increment: successful completion increments BOTH key and team counters
  Given owner JWT for tenant A
    And team T with no budget cap
    And key K attributed to T
    And a fake upstream returning 200 with usage tokens
    And pricing_snapshots row for the model (non-zero cost)
    And Redis counters both start at 0
  When POST /v1/chat/completions with K
  Then usage:spend:key:{K.id}:{YYYYMM} > 0 in Redis
  And usage:spend:team:{T.id}:{YYYYMM} > 0 in Redis
  And both counters equal the same cost_usd computed by the recorder

Scenario: S10 — group_by=team_id rollup reconciles and has NULL bucket for un-teamed keys
  Given owner JWT for tenant A
    And team T with name "platform"
    And key K_team attributed to T
    And key K_solo un-teamed
    And usage_records rows seeded: 1 row for K_team (cost 5.00), 1 row for K_solo (cost 3.00)
  When GET /admin/spend?group_by=team_id&window=month
  Then 200 + breakdown has:
    - {team_id: T.id, team_name: "platform", requests: 1, cost_usd: "5.00"}
    - {team_id: null, team_name: null, requests: 1, cost_usd: "3.00"}
  And total reconciles (totals.cost_usd = "8.00")

Scenario: S11 — member role forbidden on PATCH /admin/teams/{id}
  Given member-role JWT for tenant A, team T
  When PATCH /admin/teams/{T.id} {"team_budget_usd": "50.00"}
  Then 403 ERR_AUTH_FORBIDDEN
  And teams.team_budget_usd is unchanged in DB

Scenario: S12 — invalid group_by value returns 422
  Given owner JWT for tenant A
  When GET /admin/spend?group_by=user_id
  Then 422 ERR_PAYLOAD_INVALID

Scenario: S13 — cross-tenant team PATCH returns 404
  Given owner JWT for tenant A and tenant B with team T_B
  When tenant A's JWT calls PATCH /admin/teams/{T_B.id} {"team_budget_usd": "50.00"}
  Then 404 (no existence leak)
  And T_B.team_budget_usd is unchanged in DB

Scenario: S14 — negative team_budget_usd rejected
  Given owner JWT for tenant A, team T
  When PATCH /admin/teams/{T.id} {"team_budget_usd": "-5.00"}
  Then 422 ERR_PAYLOAD_INVALID
  And teams.team_budget_usd is unchanged in DB
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PATCH /admin/teams/{team_id}   body: { "team_budget_usd": str|null }
  200 -> {
    id: UUID,
    name: str,
    tenant_id: UUID,
    created_at: datetime,
    member_count: int,
    key_count: int,
    team_budget_usd: str|null   # str(Decimal) — exact; null when cleared
  }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }    # member role
  404 -> { code: "ERR_TEAM_NOT_FOUND" }    # team not found or cross-tenant
  422 -> { code: "ERR_PAYLOAD_INVALID" }   # non-numeric, negative, or malformed body

GET /admin/teams   (ADDITIVE EXTENSION — existing shape + new field)
  200 -> [
    {
      id, name, tenant_id, created_at, member_count, key_count,
      team_budget_usd: str|null   # NEW — null when not set
    }
  ]

GET /admin/teams/{team_id}   (ADDITIVE EXTENSION — existing shape + new field)
  200 -> {
    id, name, tenant_id, created_at, member_count, key_count,
    members: [...],
    team_budget_usd: str|null   # NEW — null when not set
  }
  404 -> { code: "ERR_TEAM_NOT_FOUND" }

POST /v1/chat/completions   (enforcement order change — no wire shape change)
  402 -> { code: "ERR_BUDGET_EXCEEDED", detail: "<scope> spend >= budget" }
    scope detail naming: "Per-key spend..." | "Team spend..." | "Tenant spend..."

GET /admin/spend?group_by=team_id   (ADDITIVE EXTENSION — new enum value)
  200 -> SpendWindowResponse  (existing shape, breakdown field now populated)
    breakdown: [
      {
        team_id: UUID|null,     # NEW — null for un-teamed keys bucket
        team_name: str|null,    # NEW — null for un-teamed keys bucket
        requests: int,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: str           # str(Decimal) — exact, sorted cost_usd DESC (NULL bucket last)
      }
    ]
  422 -> { code: "ERR_PAYLOAD_INVALID" }   # group_by not in {"key_id", "team_id"}

DDL (migration after 3a7f1c9e2b5d):
  ALTER TABLE teams ADD COLUMN team_budget_usd NUMERIC(12,2) NULL;
  -- No new tables; EXPECTED_TABLES manifest unchanged.

Redis counter key format:
  usage:spend:team:{team_id}:{YYYYMM}    # INCRBYFLOAT, same format as usage:spend:key:
  Read in: proxy/application/use_cases.py _check_team_budget (new method, analogous to _check_per_key_budget)
  Written in: usage/application/recorder.py _record_internal (alongside per-key INCRBYFLOAT)

AuthzResult additive extension (zero extra DB reads):
  Fields added (default None):
    team_id: uuid.UUID | None = None
    team_budget_usd: Decimal | None = None
  Populated in: keys/application/use_cases.py AuthzUseCase.execute()
    via LEFT JOIN teams ON api_keys.team_id = teams.id in get_by_id()
  Existing frozen tests (proxy, key-governance) only assert on known fields — additive defaults
  are safe (v3 precedent).

Enforcement order (updated §3):
  1. expiry check (_check_expiry)
  2. model allowlist check (_check_model_allowlist)
  3. catalog + tenant check (_check_model_catalog)
  4. per-key budget check (_check_per_key_budget) — if monthly_budget_usd or soft_budget_usd set
  5. team budget check (_check_team_budget) — NEW; if team_id set AND team_budget_usd set
  6. tenant budget check (budget_guard.check) — only when no hard per-key budget
  7. rate limits (_enforce_rate_limits)
  NOTE: Steps 4 + 5 are both enforcement steps with independent 402 gates.
  Step 6 (tenant guard) runs unconditionally when step 4 has no hard per-key budget — team
  budget (step 5) runs regardless; team + tenant can both fire on a soft-key-only key.

Modules touched (hard boundary):
  MODIFY:
    - apps/gateway/src/gateway/teams/domain/entities.py  — Team/TeamDetail: add team_budget_usd field (Decimal|None)
    - apps/gateway/src/gateway/teams/api/schemas.py       — TeamResponse/TeamDetailResponse: add team_budget_usd (str|None); new PatchTeamBudgetRequest
    - apps/gateway/src/gateway/teams/api/router.py        — add PATCH /admin/teams/{id} endpoint
    - apps/gateway/src/gateway/teams/application/use_cases.py — add UpdateTeamBudgetUseCase
    - apps/gateway/src/gateway/teams/domain/ports.py      — add update_budget() to TeamRepository port
    - apps/gateway/src/gateway/teams/infrastructure/orm.py — add team_budget_usd mapped_column
    - apps/gateway/src/gateway/teams/infrastructure/repository.py — implement update_budget()
    - apps/gateway/src/gateway/keys/domain/entities.py    — AuthzResult: add team_id + team_budget_usd fields
    - apps/gateway/src/gateway/keys/application/use_cases.py — AuthzUseCase.execute(): populate team fields via JOIN
    - apps/gateway/src/gateway/keys/infrastructure/repository.py — get_by_id() LEFT JOIN teams
    - apps/gateway/src/gateway/proxy/application/use_cases.py — _enforce_governance: insert _check_team_budget step
    - apps/gateway/src/gateway/usage/application/recorder.py — _record_internal: INCRBYFLOAT team counter
    - apps/gateway/src/gateway/usage/api/router.py        — get_spend: add group_by=team_id branch
    - apps/gateway/src/gateway/usage/api/schemas.py       — new TeamSpendBreakdownItem (team_id, team_name fields)
  ADD:
    - apps/gateway/migrations/versions/<hash>_team_governance.py — ALTER TABLE teams ADD team_budget_usd
  NOT TOUCHED:
    - usage_records table (no schema change — attribution via runtime JOIN)
    - tests/migrations/test_migrations.py EXPECTED_TABLES (no new table)
    - Any frozen contract tests (key-governance, spend-windows, teams-core suites)

EXPECTED_TABLES note: no new table → no manifest edit needed.
Migration chain: baseline → ... → 3a7f1c9e2b5d (teams-core) → <this migration> (team-governance)
```

Status: FROZEN @ v4 — approved by Tin Dang (delegated auto mode, 2026-06-11)

Least-sure flag surfaced at freeze:
  ⚠ [spec] Current-team attribution: group_by=team_id rollup reflects the key's team at QUERY
    time (JOIN api_keys), not at completion time — historical rows re-attribute when a key
    changes teams. RESOLVED at freeze: accepted for v4 (no schema change to the hot ledger);
    historical-snapshot attribution (usage_records.team_id written at record time) deferred to
    v5 with a documented migration path. Cost if wrong: team spend reports are governance
    views, not immutable accounting — billing stays key/tenant-accurate via usage_records.
  ⚠ [contract] AuthzResult gains team fields via LEFT JOIN teams in get_by_id() — must be
    LEFT (un-teamed keys and keys whose team was deleted still authenticate; NULL team fields).
    Cost if wrong (INNER JOIN slips in): every un-teamed key 401s — total outage class.
  ⚠ [contract] group_by validation tightens: unknown values currently ignored silently; this
    contract makes group_by not in {key_id, team_id} a 422 (consistent with the window param's
    422 whitelist). RESOLVED at freeze: additive validation approved — the frozen spend-windows
    suite never pinned the silent-ignore behavior; cost if a hidden consumer relied on it:
    422 surfaces immediately and the fix is supplying a valid value.

Error envelope note (amendment at freeze): problem+json envelopes use "code" (platform-wide
shape asserted by assert_problem helpers) — the shapes above were normalized from "error" to
"code" at the freeze review.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_patch_team_sets_budget: arrange signup+login+team / act PATCH team_budget_usd="100.00" / assert 200 + team_budget_usd in response + DB row
  - test_patch_team_clears_budget: arrange team with budget set / act PATCH null / assert 200 + null + DB
  - test_get_teams_includes_team_budget_usd: arrange team with budget / act GET /admin/teams / assert field present
  - test_get_team_detail_includes_team_budget_usd: arrange team with budget / act GET /{id} / assert field present
  - test_teamed_key_402_unateamed_sibling_200: arrange 2 keys + seeded team counter / act both completions / assert 402 + 200
  - test_per_key_hard_budget_fires_before_team: arrange key with per-key cap + seeded key counter + empty team counter / act completion / assert 402 (key scope)
  - test_team_budget_does_not_affect_other_teams: arrange T1 capped + T2 not capped + keys in each / act K2 completion / assert 200
  - test_team_budget_cross_tenant_isolation: arrange tenant B key un-teamed + tenant A team capped / act B key completion / assert 200
  - test_completion_increments_both_counters: arrange key in team + pricing_snapshots / act completion / assert both Redis counters > 0 + equal
  - test_spend_group_by_team_id_rollup: arrange 2 keys (one teamed, one solo) + seeded usage_records / act GET /admin/spend?group_by=team_id / assert breakdown shape + NULL bucket + totals reconcile
  - test_patch_team_member_role_forbidden: arrange member JWT / act PATCH team_budget_usd / assert 403 + unchanged DB
  - test_spend_invalid_group_by: arrange owner JWT / act GET /admin/spend?group_by=user_id / assert 422
  - test_patch_team_cross_tenant_returns_404: arrange two tenants / act tenant A PATCH tenant B's team / assert 404 + unchanged
  - test_patch_team_negative_budget_422: arrange team / act PATCH team_budget_usd="-5.00" / assert 422 + unchanged
</test_plan>

Tests live in: `apps/gateway/tests/team_governance/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the team budget check MUST be fail-OPEN on any Redis error
(same guarantee as per-key budget and tenant budget checks); team counter INCRBYFLOAT MUST
be in the same try/except block as the key counter and must never raise into the proxy path;
the LEFT JOIN teams in get_by_id() must use a LEFT JOIN (not INNER JOIN) so un-teamed keys
and deleted-team keys are unaffected (returns NULL team fields, not missing rows).
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

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
