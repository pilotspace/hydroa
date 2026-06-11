# TASK: Runtime per-tenant model management

slug: model-mgmt · created: 2026-06-11 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: runtime per-tenant model enable/disable over the synced catalog — no gateway restart

Framings weighed:
  - per-request DB read piggybacked on the existing catalog check (chosen — see §3 read-path decision)
  - short-TTL in-process cache (rejected — a TTL delay violates "takes effect on the NEXT request"
    literally; the exit criterion says no restart, not "within N seconds")
  - Redis-backed toggle cache with subscribe-on-change (rejected — adds Redis as a hard dependency
    on the hot path for this feature; the per-request DB read is already present via ModelChecker
    which hits Postgres on every request — this task adds one column to that same query, zero extra
    round-trips)

Must:
<must>
  - M1  GET /admin/models (JWT owner/admin) returns the full synced catalog with each model carrying
        an `enabled` boolean reflecting the tenant's override (no override row = true; override
        row with enabled=false = false; override row with enabled=true = true)
  - M2  PUT /admin/models/{model_id} body {"enabled": bool} (JWT owner/admin) upserts a
        tenant_model_overrides row; returns 200 with the updated model object including `enabled`
  - M3  A model disabled for a tenant (enabled=false override) MUST be rejected with 403
        ERR_MODEL_DISABLED on the VERY NEXT /v1/chat/completions request — no gateway restart
  - M4  A model re-enabled (override flipped to true, or override row deleted) MUST be accepted
        (200) on the very next request — no gateway restart
  - M5  The default posture for any catalog model with no override row is enabled=true — no
        pre-populated rows needed; "open by default" matches the v1 decision
        "All catalog models available to every tenant"
  - M6  Tenant isolation: an override for tenant A MUST NOT affect any request from tenant B
  - M7  Key-level model_allowlist check remains enforced independently and BEFORE the
        tenant-disabled check; both gates compose (a model blocked by the key allowlist is rejected
        with ERR_MODEL_NOT_ALLOWED regardless of the tenant override; a model allowed by the key
        allowlist but disabled at the tenant level is rejected with ERR_MODEL_DISABLED)
  - M8  PUT with an unknown catalog model_id (not present in models table) returns 404
        ERR_MODEL_NOT_FOUND
  - M9  GET /admin/models reflects the same catalog list as the synced models table joined with the
        caller's tenant overrides — inactive catalog models (active=false) are excluded (matching
        the existing /v1/models behavior)
</must>

Reject:
<reject>
  - A tenant member role calling PUT /admin/models/{model_id} → "ERR_AUTH_FORBIDDEN"
  - A tenant member role calling GET /admin/models → "ERR_AUTH_FORBIDDEN"
  - PUT /admin/models/{model_id} where model_id not in models table → "ERR_MODEL_NOT_FOUND"
  - /v1/chat/completions for a model that is tenant-disabled → "ERR_MODEL_DISABLED"
  - /v1/chat/completions with a key whose model_allowlist excludes the model (even if
    tenant has it enabled) → "ERR_MODEL_NOT_ALLOWED"  [existing enforcement, must remain]
</reject>

After:
<after>
  - A PUT /admin/models/{model_id} {"enabled": false} followed by a completion request from any
    key in that tenant returns 403 ERR_MODEL_DISABLED without gateway restart
  - A subsequent PUT /admin/models/{model_id} {"enabled": true} followed by a completion request
    returns 200 (upstream forwarded) without restart
  - GET /admin/models for tenant A after tenant A disables model X shows enabled=false for X;
    GET /admin/models for tenant B (who made no change) still shows enabled=true for X
  - tenant_model_overrides has exactly one row per (tenant_id, model_id) pair (UNIQUE constraint)
    even after repeated PUTs (upsert semantics — no duplicates)
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The enforcement read-path (per-request DB read piggybacked on the existing ModelChecker query)
    adds no measurable extra latency because SqlAlchemyModelChecker already executes a Postgres
    SELECT on every request; extending that query to JOIN tenant_model_overrides adds at most one
    indexed lookup — lowest confidence because the existing checker does a trivially simple
    `SELECT active FROM models WHERE id = ?` and the JOIN adds complexity; if wrong: the hot-path
    p99 may increase; mitigation: the UNIQUE index on (tenant_id, model_id) ensures the join cost
    is O(log n), acceptable for this stage; this is the CORRECT tradeoff given "next request"
    semantics are non-negotiable per the exit criterion

  ⚠ Access GROUPS (named model bundles for tenants) are deferred to v4 — lowest confidence that
    this deferral is acceptable because the milestone task line says "access groups over the synced
    catalog"; if wrong: v4 will add a `model_groups` table and a FK from tenant_model_overrides;
    the schema is forward-compatible today because no group_id column exists yet (the UNIQUE on
    tenant_id+model_id does not conflict with a future group_id FK column)

  - [ ] The tenant_model_overrides table can safely use models.id (TEXT, OpenRouter model id
        string) as a FK target — confirmed: ModelRow.id is the PK (TEXT), and api_keys already
        has JSON model_allowlist storing these same string ids; the FK is safe

  - [ ] Enforcement order: tenant-disabled check must run AFTER key-level allowlist check
        (M7) — confirmed: CompletionUseCase._validate_payload calls model_checker.is_active
        AFTER _check_model_allowlist in _enforce_governance; the new tenant-disabled check
        replaces/extends is_active semantics (see §3 modules-touched)

  - [ ] No migration chain conflict: new migration chains after a1b2c3d4e5f6 (health_alerting)
        which is the current HEAD — confirmed by reading the migration files
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: disable model takes effect on next request no restart
  Given a tenant owner has signed up and created an API key with no model allowlist restriction
  And the model "openai/gpt-4o" is active in the catalog
  And the tenant has no override for "openai/gpt-4o" (default enabled=true)
  When the owner calls PUT /admin/models/openai%2Fgpt-4o {"enabled": false}
  And a completion request is sent immediately on the next call (no gateway restart)
  Then the completion request returns 403 ERR_MODEL_DISABLED
  And no upstream call is made

Scenario: re-enable model takes effect on next request
  Given a tenant owner has disabled "openai/gpt-4o" via PUT /admin/models/openai%2Fgpt-4o {"enabled": false}
  When the owner calls PUT /admin/models/openai%2Fgpt-4o {"enabled": true}
  And a completion request is sent on the next call (no gateway restart)
  Then the completion request returns 200
  And the upstream was called exactly once

Scenario: unknown model PUT returns 404
  Given a tenant owner is authenticated
  When the owner calls PUT /admin/models/fake%2Fnonexistent {"enabled": false}
  Then the response is 404 ERR_MODEL_NOT_FOUND
  And no tenant_model_overrides row is written

Scenario: member role PUT forbidden
  Given a tenant member is authenticated (role=member)
  When the member calls PUT /admin/models/openai%2Fgpt-4o {"enabled": false}
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And the model override is unchanged

Scenario: member role GET forbidden
  Given a tenant member is authenticated (role=member)
  When the member calls GET /admin/models
  Then the response is 403 ERR_AUTH_FORBIDDEN

Scenario: GET admin models shows enabled flags reflecting overrides
  Given a tenant owner has signed up
  And the catalog has two active models "openai/gpt-4o" and "anthropic/claude-opus-4"
  And the tenant disables "openai/gpt-4o" via PUT /admin/models/openai%2Fgpt-4o {"enabled": false}
  When the owner calls GET /admin/models
  Then the response contains "openai/gpt-4o" with enabled=false
  And the response contains "anthropic/claude-opus-4" with enabled=true

Scenario: default posture is enabled when no override row exists
  Given a tenant owner has signed up
  And the catalog has model "openai/gpt-4o" active
  And no tenant_model_overrides row exists for this tenant and model
  When the owner calls GET /admin/models
  Then the response contains "openai/gpt-4o" with enabled=true

Scenario: tenant isolation override does not cross tenants
  Given tenant A has disabled "openai/gpt-4o" via PUT /admin/models/openai%2Fgpt-4o {"enabled": false}
  And tenant B has no override for "openai/gpt-4o"
  When a completion request from tenant B's key is sent for "openai/gpt-4o"
  Then the request returns 200 (upstream forwarded for tenant B)
  And GET /admin/models for tenant A still shows "openai/gpt-4o" with enabled=false
  And GET /admin/models for tenant B still shows "openai/gpt-4o" with enabled=true

Scenario: key allowlist still enforced independently composes with tenant override
  Given the tenant has "openai/gpt-4o" enabled at tenant level (no disable override)
  And the API key has model_allowlist=["anthropic/claude-opus-4"] (excludes gpt-4o)
  When a completion request is sent for "openai/gpt-4o"
  Then the response is 403 ERR_MODEL_NOT_ALLOWED
  And no upstream call is made

Scenario: PUT is idempotent upsert no duplicate rows
  Given a tenant owner calls PUT /admin/models/openai%2Fgpt-4o {"enabled": false} twice
  When querying tenant_model_overrides for this tenant and model
  Then exactly one row exists (UNIQUE constraint enforced, no duplicate rows)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/models
  Headers: Authorization: Bearer <jwt>   (owner or admin only)
  200 -> {
    "object": "list",
    "data": [
      {
        "id": "<model_id>",          // OpenRouter model id string
        "name": "<string>",
        "context_length": <int|null>,
        "enabled": <bool>            // true = available to this tenant; false = tenant-disabled
      }
    ]
  }
  401 -> { "code": "ERR_AUTH_INVALID_TOKEN" | "ERR_AUTH_INVALID_KEY" }
  403 -> { "code": "ERR_AUTH_FORBIDDEN" }    // member role

PUT /admin/models/{model_id}
  Headers: Authorization: Bearer <jwt>   (owner or admin only)
  Path:    model_id is the URL-encoded OpenRouter model id (e.g. "openai%2Fgpt-4o")
  Body:    { "enabled": <bool> }
  200 -> {
    "id": "<model_id>",
    "name": "<string>",
    "context_length": <int|null>,
    "enabled": <bool>
  }
  401 -> { "code": "ERR_AUTH_INVALID_TOKEN" }
  403 -> { "code": "ERR_AUTH_FORBIDDEN" }       // member role
  404 -> { "code": "ERR_MODEL_NOT_FOUND" }      // model_id not in models table
  422 -> { "code": "ERR_PAYLOAD_INVALID" }      // body missing "enabled" or wrong type

/v1/chat/completions  (enforcement — existing route, new rejection case)
  403 -> { "code": "ERR_MODEL_DISABLED" }       // model active in catalog but disabled for this tenant
                                                // distinct from ERR_MODEL_NOT_ALLOWED (key-level)
                                                // distinct from ERR_MODEL_UNKNOWN (not in catalog)

Schema — DDL (new table, additive migration chaining after a1b2c3d4e5f6):
  CREATE TABLE tenant_model_overrides (
    tenant_id   UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    model_id    TEXT        NOT NULL REFERENCES models(id)  ON DELETE CASCADE,
    enabled     BOOLEAN     NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, model_id)
  );
  -- No extra index beyond PK needed; PK (tenant_id, model_id) serves all queries.
  -- model_id FK ON DELETE CASCADE: if a catalog model is removed (hard delete, unlikely),
  --   override rows are cleaned up; no orphan risk.
  -- tenant_id FK ON DELETE CASCADE: standard tenant isolation cleanup.

Read-path decision (CHOSEN: per-request DB read, piggyback on existing ModelChecker):
  SqlAlchemyModelChecker.is_active currently executes:
    SELECT active FROM models WHERE id = ?
  This task extends it to also check the tenant's override by passing tenant_id:
    SELECT m.active, COALESCE(tmo.enabled, true) AS tenant_enabled
    FROM models m
    LEFT JOIN tenant_model_overrides tmo
      ON tmo.model_id = m.id AND tmo.tenant_id = ?
    WHERE m.id = ?
  Port shape (ADDITIVE — resolves the [contract] flag; orchestrator decision at freeze):
    The frozen ModelChecker.is_active(model_id) signature is NOT changed. A NEW method is added
    to the Protocol:
      async def check_for_tenant(model_id: str, tenant_id: UUID) -> ModelAccess
    where ModelAccess is a tri-state domain enum: ACTIVE | UNKNOWN | TENANT_DISABLED.
      - not in catalog OR m.active = false → UNKNOWN  (use case raises existing ERR_MODEL_UNKNOWN path)
      - tenant_enabled = false             → TENANT_DISABLED (use case raises ModelDisabledError → 403 ERR_MODEL_DISABLED)
      - otherwise                          → ACTIVE
    The infrastructure checker returns the enum; the APPLICATION layer raises the domain error
    (infrastructure never raises HTTP/problem errors — CONVENTIONS layering).
  The application layer (CompletionUseCase._validate_payload) calls check_for_tenant with
    authz.tenant_id; the frozen is_active remains for backward-compatible fakes in frozen suites.
  Latency note: the existing ModelChecker hit is one Postgres SELECT per completion; adding a
    LEFT JOIN with a PK-indexed (tenant_id, model_id) lookup adds sub-millisecond to that query —
    acceptable and no extra DB round-trip.
  Alternative rejected: TTL cache — "takes effect on the next request" is the exit criterion;
    any TTL > 0 means "within TTL seconds", not "next request". Per-request read is the only
    semantically correct choice.

Error codes (new — extend gateway.proxy.domain.errors or gateway.core.errors):
  ERR_MODEL_DISABLED — 403  — tenant has explicitly disabled this model; fix: owner re-enables it
  ERR_MODEL_NOT_FOUND — 404 — model_id unknown in catalog; only on PUT /admin/models/{model_id}

Enforcement order (updated — within CompletionUseCase._validate_payload):
  1. model_id validation (non-empty string)                [existing]
  2. key-level model_allowlist check (_check_model_allowlist) → ERR_MODEL_NOT_ALLOWED  [existing M7]
  3. catalog active check + tenant-disabled check (new, piggyback) → ERR_MODEL_DISABLED [new M3]
     (ERR_MODEL_UNKNOWN still fires when catalog has no row or active=false)
  Note: tenant-disabled check is at _validate_payload stage, same DB query as catalog check.

Modules touched (hard boundary — builders must not touch anything outside this list):
  NEW:
    src/gateway/catalog/infrastructure/orm.py                  — add TenantModelOverrideRow ORM
    src/gateway/catalog/api/router.py                          — add admin_models_router (GET + PUT)
    src/gateway/catalog/api/deps.py                            — deps for admin models endpoints
    src/gateway/catalog/api/schemas.py                         — AdminModelItem, PutModelRequest, AdminModelsListResponse
    src/gateway/catalog/domain/errors.py                       — add ModelNotFoundError (domain)
    apps/gateway/migrations/versions/<rev>_tenant_model_overrides.py  — additive migration
  MODIFIED:
    src/gateway/proxy/domain/ports.py                          — ADD check_for_tenant + ModelAccess enum (is_active UNCHANGED)
    src/gateway/proxy/infrastructure/model_checker.py          — implement check_for_tenant with LEFT JOIN query
    src/gateway/proxy/application/use_cases.py                 — call check_for_tenant(model, authz.tenant_id); raise ModelDisabledError on TENANT_DISABLED
    src/gateway/main.py                                        — include admin_models_router
  SANCTIONED FROZEN-TEST DISPOSITION (manifest maintenance — same precedent as spend-windows):
    tests/migrations EXPECTED_TABLES manifest gains the single line "tenant_model_overrides".
    This is the ONLY permitted frozen-test edit; it must carry an inline disposition comment
    referencing this §3 block. No other frozen test may be touched.

Access GROUPS deferred to v4:
  Named model bundles (groups) would require a model_groups table and a FK from
  tenant_model_overrides. The current schema is forward-compatible: adding a group_id column
  later does not conflict with the (tenant_id, model_id) PK. Declared here so the v4 intake
  picks this up without a breaking migration.

Migration chain:
  down_revision: a1b2c3d4e5f6 (health_alerting_tenant_nullable — current HEAD)
  revision:      <new rev>_tenant_model_overrides
  Rollback: DROP TABLE tenant_model_overrides (safe — additive, no existing rows reference it)
```

Status: FROZEN @ v3 — approved by Tin Dang (delegated auto mode, 2026-06-11)

Least-sure flag surfaced at freeze:
  ⚠ [contract] The enforcement read-path extends the per-request hot-path query with a LEFT JOIN
    on tenant_model_overrides. The frozen ModelChecker.is_active port is NOT modified — the
    additive check_for_tenant method + ModelAccess tri-state enum (orchestrator decision,
    resolving the draft's option (a)) keeps frozen proxy-completions fakes untouched. Cost if
    wrong (the additive method proves insufficient and is_active must change): frozen test
    suite amendment — a change request back to SPECIFY, never an in-build edit.
  ⚠ [test] Tenant isolation test requires TWO distinct tenants in one test; if the second
    signup collided (email reuse under per-test schema reset) the isolation scenario could
    false-green. Mitigation in the suite: distinct emails per tenant per test; the shared
    `app` fixture drops/recreates the schema per test. Cost if wrong: cross-tenant override
    bleed ships undetected — the highest-severity failure this task can have.

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_disable_model_takes_effect_next_request:
      arrange tenant+key+catalog model "openai/gpt-4o" / act PUT disable then POST completions /
      assert 403 ERR_MODEL_DISABLED, upstream.calls == 0
  - test_reenable_model_takes_effect_next_request:
      arrange disable + re-enable via PUT / act POST completions /
      assert 200, upstream.calls == 1
  - test_put_unknown_model_returns_404:
      arrange owner jwt / act PUT /admin/models/fake%2Fnonexistent / assert 404 ERR_MODEL_NOT_FOUND
      assert no override row written (query tenant_model_overrides)
  - test_put_member_role_forbidden:
      arrange member user jwt / act PUT /admin/models/openai%2Fgpt-4o /
      assert 403 ERR_AUTH_FORBIDDEN, override row unchanged
  - test_get_admin_models_member_forbidden:
      arrange member jwt / act GET /admin/models / assert 403 ERR_AUTH_FORBIDDEN
  - test_get_admin_models_shows_enabled_flags:
      arrange two catalog models, disable one / act GET /admin/models /
      assert disabled=false for that model, enabled=true for other
  - test_default_posture_enabled_when_no_override:
      arrange catalog model, no PUT called / act GET /admin/models /
      assert enabled=true
  - test_tenant_isolation_a_disable_does_not_affect_b:
      arrange tenant A disables model, tenant B no override /
      act completion from tenant B's key /
      assert 200 for B; GET /admin/models for A shows false; GET for B shows true
  - test_key_allowlist_enforced_independently_composes:
      arrange key with model_allowlist=["anthropic/claude-opus-4"], tenant has gpt-4o enabled /
      act POST completions for "openai/gpt-4o" / assert 403 ERR_MODEL_NOT_ALLOWED (not DISABLED)
  - test_put_idempotent_upsert_no_duplicate_rows:
      arrange two PUT {"enabled": false} calls for same model /
      act query tenant_model_overrides / assert count == 1
</test_plan>

Tests live in: `apps/gateway/tests/model_mgmt/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): tenant_model_overrides upsert must be atomic
  (INSERT ... ON CONFLICT (tenant_id, model_id) DO UPDATE) — never two rows, never lost update;
  the catalog active=false check and the tenant override check must be in one query to avoid TOCTOU.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — tests/model_mgmt 10/10 (9 red->green + 1 guard stayed green);
      full suite 220 passed, 19 deselected; orchestrator authoritative re-run after review fixes
- [x] coverage did not decrease — 80.27% vs 80% floor (was 80.61% pre-task; new admin router +
      checker branches covered by the suite; margin remains tight — carry the watch item forward)
- [x] no test or contract was altered during build — single sanctioned §3 disposition:
      tests/migrations EXPECTED_TABLES += "tenant_model_overrides" with inline disposition comment
      (manifest maintenance, spend-windows precedent); frozen is_active signature untouched
- [x] concurrency / timing safe — upsert is a single INSERT..ON CONFLICT (tenant_id, model_id)
      DO UPDATE statement (atomic, no TOCTOU, no duplicate rows — proven by
      test_put_idempotent_upsert_no_duplicate_rows); hot-path check is one LEFT JOIN SELECT
      (catalog active + tenant override in the same query per §3 safety rule)
- [x] no exposed secrets / injection / unexpected deps — all queries via SQLAlchemy bound
      parameters (no f-string SQL); no new dependencies; no key material touched
- [x] layering follows CONVENTIONS.md — ModelAccess enum + check_for_tenant on the domain port;
      infrastructure returns the enum and never raises HTTP errors; application maps
      TENANT_DISABLED -> 403 ERR_MODEL_DISABLED problem+json
- [x] reviewed — orchestrator line-by-line diff review (delegated auto mode); caught and fixed:
      (1) dead ModelDisabledError in proxy/domain/errors.py — file was outside the §3
      modules-touched boundary; reverted; (2) dead ModelNotFoundError in catalog/domain/errors.py
      — §3 prose listed the domain error, but the 404 condition maps directly to
      ProblemError(404, ERR_MODEL_NOT_FOUND) at the API layer per the existing catalog router
      idiom; the unused class was removed to satisfy the dead-code check. DISPOSITION: observable
      contract (status + code) unchanged; the §3 "ModelNotFoundError (domain)" line is satisfied
      semantically by the direct ProblemError mapping.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — admin_models_router included in main.py create_app; check_for_tenant called
      from CompletionUseCase._check_model_catalog (invoked by _enforce_governance after
      _check_model_allowlist — §3 M7 order); TenantModelOverrideRow imported by env.py for
      autogenerate parity and used by router + model_checker; ModelAccess exported in ports
      __all__ and consumed in use_cases; confirmed via grep + green behavior tests
- [x] DEAD-CODE (code) — two dead symbols found at review and REMOVED (ModelDisabledError,
      ModelNotFoundError — see review note above); post-fix grep shows no unreferenced new symbol
- [x] SEMANTIC (prose / non-code) — migration e7f3b2a9c4d1 read in full: DDL matches §3 exactly
      (composite PK, both FKs CASCADE, timestamptz defaults, downgrade drops table); make migrate
      + make migrate-check clean ("No new upgrade operations detected") — ORM/migration parity holds

### GATE RECORD
Outcome: PASS (auto-resolved under autonomy: auto)
Evidence: tests/model_mgmt 10/10 · full suite 220 passed · coverage 80.27% (floor 80%) ·
make ci exit 0 (lint+typecheck+allowlist+allowlist-node+test) · make migrate + migrate-check clean ·
frozen-fake seam verified (frozen proxy suites green, fakes lacking check_for_tenant fall back to is_active)
Residue (disclosed, non-blocking): coverage margin 0.27pt above floor — watch at dashboard-govern;
PUT allows setting an override for a catalog-inactive model (still invisible via GET active-only
list and still UNKNOWN on hot path — benign, becomes effective only if the model reactivates)
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-11

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): ERR_MODEL_DISABLED 403 rate per tenant ·
PUT /admin/models 4xx rate · hot-path p99 (LEFT JOIN added to the per-request catalog check)
Spec delta for the next loop: access groups (named model bundles) deferred to v4 — schema is
forward-compatible (no group_id column conflicts with the (tenant_id, model_id) PK); live
verification confirmed disable->403 on the very next request through TLS with no restart.

### Competency deltas
- [SDD · open] contract prose listing internal domain-error class names invites dead code —
  the observable surface (status+code) is the contract; name internal types only when a layer
  boundary needs them (evidence: ModelDisabledError/ModelNotFoundError both born dead at build,
  removed at review)
- [TDD · open] route params that contain "/" need the :path converter under ASGI decoded
  paths — encode this in the §3 contract when ids are slash-bearing (evidence: builder needed
  the test-driven hint; documented in §3 to avoid rediscovery)
- [ADD · open] the hasattr capability seam is now used twice (soft-budget, check_for_tenant)
  to keep frozen fakes valid across port extensions — candidate for CONVENTIONS.md at fold
  (evidence: zero frozen-test edits across two port-extending tasks)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
