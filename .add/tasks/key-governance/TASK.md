# TASK: Per-key budgets, expiry, model allowlist, rotation

slug: key-governance · created: 2026-06-11 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-key governance — hard/soft budget, expiry, model allowlist, rotation
Framings weighed:
  - Extend AuthzResult + enforce in CompletionUseCase (chosen) — authz DB read already happens;
    governance fields returned in the AuthzResult struct carry zero extra DB cost; enforcement
    then lives at the one natural chokepoint before upstream. Alternative considered:
    enforce in a separate middleware that re-reads the key row — rejected: adds a second DB
    roundtrip per request. Alternative considered: enforce entirely at Envoy ext_authz —
    rejected: expiry/allowlist/key-budget require data only Postgres holds; Envoy can only
    enforce what the authz response tells it, and we control the authz endpoint already.

<must>
  ### Lifecycle fields — storage & CRUD
  - M1  POST /admin/keys accepts optional fields: monthly_budget_usd (Numeric or null),
        soft_budget_usd (Numeric or null), expires_at (ISO-8601 timestamptz or null),
        model_allowlist (JSON array of non-empty string model IDs, or null).
  - M2  PATCH /admin/keys/{key_id} updates any combination of monthly_budget_usd,
        soft_budget_usd, model_allowlist, expires_at on an active (non-revoked) key
        owned by the caller's tenant; returns 200 with updated KeyInfoResponse.
  - M3  GET /admin/keys response items carry monthly_budget_usd, soft_budget_usd,
        expires_at, model_allowlist fields (nullable).
  - M4  The api_keys table gains ADDITIVE columns (new Alembic migration, additive only,
        downgrade drops only the new columns):
          monthly_budget_usd  NUMERIC(12,2)  NULL
          soft_budget_usd     NUMERIC(12,2)  NULL  CHECK soft <= hard when both non-null
          expires_at          TIMESTAMPTZ    NULL
          model_allowlist     JSONB          NULL   (array of text, or null = all models)
          rotated_from_key_id UUID           NULL   FK self-ref -> api_keys(id) ON DELETE SET NULL
        No backfill; all existing rows keep NULL in new columns (unlimited, never-expire, all-model).

  ### Rotation
  - M5  POST /admin/keys/{key_id}/rotate issues a new key row atomically:
        new row inherits name + tenant_id from the old row; new monthly_budget_usd,
        soft_budget_usd, expires_at, model_allowlist may be supplied in the request body
        (null = inherit from old row for each field independently); new row's
        rotated_from_key_id = old row's id; old row is revoked atomically in the same
        transaction; returns 201 with the new plaintext key (shown once) plus new_key_id
        and superseded_key_id; both operations are in a single DB transaction.
  - M6  After rotation the OLD key is rejected immediately by /internal/authz (next request
        that presents it receives 401 ERR_AUTH_INVALID_KEY — same byte-identical posture as
        all other authz failures for unknown/revoked keys). The NEW key works immediately.
  - M7  Rotation preserves usage continuity: usage_records keyed by key_id accumulate on the
        new row's id going forward; the old row's usage_records remain under its id (historical);
        the rotated_from_key_id column provides the lineage chain for spend-windows aggregation.
        Rationale: a UUID chain is queryable by the spend-windows task without requiring a
        mutable "logical key group" concept; spend-windows can JOIN on rotated_from_key_id to
        build the full lineage; this is documented as the seam for spend-windows to consume.

  ### Enforcement on the proxy hot path (/v1/chat/completions)
  - M8  Expired key (expires_at <= now() UTC): authz returns AuthzResult carrying the
        governance fields; the CompletionUseCase raises 401 ERR_AUTH_KEY_EXPIRED.
        Rationale for distinct code: expiry is a post-identification state (the key is
        authentically the caller's key; we simply refuse to serve it). Keeping a
        DISTINCT code from ERR_AUTH_INVALID_KEY is correct — expiry is not an
        enumeration risk since the key is already identified. Unknown-key failures
        REMAIN byte-identical per the v1 frozen rule (enforced by existing tests which
        must stay green); ERR_AUTH_KEY_EXPIRED is only reachable after hash-match succeeds.
  - M9  Disallowed model (model not in model_allowlist when model_allowlist is non-null and
        non-empty): 403 ERR_MODEL_NOT_ALLOWED. Null model_allowlist = all models allowed.
        Empty array [] = no models allowed (every model is rejected). This is a security-
        strict interpretation: an empty allowlist is a locked-down key, not an unlimited one.
  - M10 Per-key hard budget exceeded (key's monthly_budget_usd is non-null AND per-key
        Redis spend counter >= key's monthly_budget_usd): 402 ERR_BUDGET_EXCEEDED.
        Most-specific-wins precedence per MILESTONE shared decision:
        per-key budget checked first (when set); if key budget is null, tenant budget
        checked (existing RedisBudgetGuard behaviour unchanged). The per-key spend counter
        key is: usage:spend:key:{key_id}:{YYYYMM}  (distinct namespace from tenant counter
        usage:spend:{tenant_id}:{YYYYMM}).
  - M11 Per-key soft budget: when per-key Redis spend counter crosses soft_budget_usd, the
        state is DETECTABLE by reading the counter — no blocking, no HTTP error on this path.
        The seam: AuthzResult (or a companion struct) exposes soft_budget_exceeded: bool
        computed at enforcement time; the spend-windows and health-alerting tasks consume
        this seam. This task does NOT implement alerting; it only exposes the boolean.
  - M12 Enforcement (M8–M11) MUST NOT add a DB roundtrip beyond what authz already performs.
        Implementation: AuthzUseCase.execute() returns an extended AuthzResult that includes
        the governance fields (expires_at, model_allowlist, monthly_budget_usd,
        soft_budget_usd); the CompletionUseCase reads these from the AuthzResult struct and
        enforces them using the existing Redis spend counter (per-key counter extension to
        RedisBudgetGuard or inline in CompletionUseCase). No second SELECT on api_keys.
  - M13 Enforcement is fail-closed for hard limits (per-key budget, expiry, allowlist).
        Infrastructure failures (Redis down for per-key counter) on HARD enforcement:
        expiry and allowlist are DB-sourced (zero infra failure risk beyond the authz read
        already in-flight); per-key budget Redis failure → fail-open (same policy as tenant
        budget, per CONVENTIONS advisory-counter pattern, availability-over-enforcement).
</must>

<reject>
  - R1  POST /admin/keys with soft_budget_usd > monthly_budget_usd (both non-null) ->
        "ERR_PAYLOAD_INVALID" (422)
  - R2  POST /admin/keys with negative monthly_budget_usd -> "ERR_PAYLOAD_INVALID" (422)
  - R3  POST /admin/keys with negative soft_budget_usd -> "ERR_PAYLOAD_INVALID" (422)
  - R4  POST /admin/keys with model_allowlist containing empty-string elements ->
        "ERR_PAYLOAD_INVALID" (422)
  - R5  PATCH /admin/keys/{key_id} on a revoked key -> "ERR_KEY_NOT_FOUND" (404)
        (revoked keys are treated as non-existent for update purposes; no leak of state)
  - R6  PATCH /admin/keys/{key_id} on a key belonging to another tenant ->
        "ERR_KEY_NOT_FOUND" (404) (cross-tenant, no leak)
  - R7  POST /admin/keys/{key_id}/rotate by a member role ->
        "ERR_AUTH_FORBIDDEN" (403)
  - R8  POST /admin/keys/{key_id}/rotate on an already-revoked key ->
        "ERR_KEY_NOT_FOUND" (404)
  - R9  POST /v1/chat/completions with an expired key ->
        "ERR_AUTH_KEY_EXPIRED" (401)
  - R10 POST /v1/chat/completions with a model not in model_allowlist (non-null, non-empty) ->
        "ERR_MODEL_NOT_ALLOWED" (403)
  - R11 POST /v1/chat/completions with per-key spend >= monthly_budget_usd ->
        "ERR_BUDGET_EXCEEDED" (402)
</reject>

<after>
  - After M1/M5: api_keys row carries the governance fields; GET /admin/keys echoes them.
  - After M5 (rotation): old key row has revoked_at set; new key row has rotated_from_key_id
    pointing to old; both writes committed atomically; plaintext new key returned once.
  - After M6: /internal/authz with old key returns 401 ERR_AUTH_INVALID_KEY immediately.
  - After R9: expired key gets 401 without an upstream call and without a usage_record row.
  - After R10: disallowed model gets 403 without an upstream call and without a usage_record row.
  - After R11: per-key budget exhausted gets 402 without an upstream call (upstream.calls == 0).
  - After R11 with key budget null but tenant budget set: tenant budget check still applies
    (existing budgets suite behaviour unaffected — no regression).
  - Soft budget crossing (M11): no HTTP error; the soft_budget_exceeded seam is computable
    from the per-key counter without additional DB I/O.
</after>

<assumptions>
  ⚠ A1 [LOWEST CONFIDENCE — cost: wrong enforcement location, wasted build] AuthzResult
     extension carries governance fields to CompletionUseCase without a second DB query.
     This is the critical architectural assumption: the proxy's SqlAlchemyKeyAuthenticator
     wraps AuthzUseCase; AuthzUseCase.execute() must return the extended struct. The
     AuthzResult dataclass is currently imported by proxy/domain/ports.py (FROZEN @ v1).
     Changing AuthzResult is a BREAKING change to a frozen contract.
     RESOLUTION: define a NEW extended entity (e.g. AuthzResultV2 / GovernedAuthzResult)
     returned only from the governance-aware AuthzUseCase; proxy/domain/ports.py KeyAuthenticator
     Protocol is extended to return the richer type. Since Protocol structural matching is
     used, this is additive as long as AuthzResult remains in the entities module and the
     proxy's existing test fakes are updated. Alternatively: return the same AuthzResult
     but add the governance fields as Optional; the frozen contract test only asserts on
     tenant_id and key_id. DECISION: add governance fields to AuthzResult with defaults
     (None/empty) — the frozen contract tests pin only tenant_id and key_id, so additive
     fields are safe. Mark in contract. If wrong: governance fields would require a second
     DB read, adding latency and complexity.

  ⚠ A2 [HIGH CONCERN — cost: diverges from milestone "most-specific-wins" hierarchy]
     Per-key spend counter uses a separate Redis key namespace
     (usage:spend:key:{key_id}:{YYYYMM}) from the tenant counter. This means the
     usage_recorder must also increment this key counter (in addition to the tenant counter)
     to keep it accurate. The usage_recorder is currently in the usage module
     (RecordingUsageRecorder). Extending it to also increment per-key counters is an
     additive change. RISK: if usage_recorder is not extended, the per-key budget check
     always sees 0 and never enforces. This task's spec MUST declare the counter-increment
     extension as in-scope for the build phase (even though spend-windows is a later task).
     If wrong: per-key budget enforcement would be a no-op at runtime despite correct tests.

  - A3 [model_allowlist empty array semantics] Empty [] = all models blocked (security-strict).
     Alternative: empty [] = unlimited (permissive default). CHOSEN: security-strict, because
     an allowlist with zero entries is most naturally read as "no models permitted." If wrong:
     a misconfigured key would silently allow all models. Cost: low (easy to change before
     freeze).

  - A4 [Rotation inherits governance fields by default] Fields not supplied in rotate request
     body are inherited from the old row. Alternative: fields not supplied = null (cleared).
     CHOSEN: inherit, because rotation is typically a secret refresh, not a governance change.
     If wrong: callers could accidentally inherit stale budgets. Cost: medium (documented
     in API contract; callers can always supply explicit null to clear).

  - A5 [PATCH on a revoked key returns 404 not 409] The proxy treats revoked-key as effectively
     deleted from the operational perspective. 404 leaks no state (same as cross-tenant).
     Alternative: 409 Conflict. CHOSEN: 404 for consistency with the existing revoke-
     cross-tenant rule and the no-leak principle. Cost: low.

  - A6 [Rotation old-key revocation is atomic with new-key insert] Both inside a single DB
     transaction. In-flight requests holding a reference to the old row at the instant of
     rotation will have authenticated before the commit; they will complete normally. After
     commit, the old key is rejected at authz. This window is acceptable (matching LiteLLM
     behaviour). Cost of being wrong: stale old-key requests up to one request lifecycle
     in-flight — acceptable.

  - A7 [expires_at boundary semantics] expires_at <= now() UTC means expired (NOT strictly <).
     This means a key expires exactly at its expiry instant, not one nanosecond after.
     Consequence: clock skew between gateway replicas of < 1 second is acceptable; sub-second
     skew could cause one replica to allow and another to reject. Acceptable for production
     (NTP-synced replicas).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first,
     the top two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── M1: POST /admin/keys with governance fields ─────────────────────────────

Scenario: Create key with all governance fields persisted and echoed
  Given an owner-role JWT for tenant Acme
  When  POST /admin/keys with name="governed-key", monthly_budget_usd="50.00",
        soft_budget_usd="40.00", expires_at="2099-01-01T00:00:00Z",
        model_allowlist=["openai/gpt-4o","openai/gpt-3.5-turbo"]
  Then  201 with key_id, name, plaintext key, monthly_budget_usd="50.00",
        soft_budget_usd="40.00", expires_at="2099-01-01T00:00:00Z",
        model_allowlist=["openai/gpt-4o","openai/gpt-3.5-turbo"]
  And   GET /admin/keys returns the same governance fields on that key_id item
  And   api_keys DB row has the correct NUMERIC and JSONB values

Scenario: Create key with no governance fields defaults to unlimited/never-expire/all-models
  Given an owner-role JWT for tenant Acme
  When  POST /admin/keys with name="bare-key" (no governance fields)
  Then  201 and monthly_budget_usd=null, soft_budget_usd=null,
        expires_at=null, model_allowlist=null in the response
  And   GET /admin/keys shows the same null fields

# ── M2: PATCH /admin/keys/{key_id} ──────────────────────────────────────────

Scenario: PATCH updates budget and allowlist on an active key
  Given an owner-role JWT and an active key with monthly_budget_usd=null
  When  PATCH /admin/keys/{key_id} with monthly_budget_usd="25.00",
        model_allowlist=["openai/gpt-4o"]
  Then  200 with monthly_budget_usd="25.00", model_allowlist=["openai/gpt-4o"]
  And   GET /admin/keys echoes the updated values

# ── M5/M6: Rotation ──────────────────────────────────────────────────────────

Scenario: Rotate a key — new secret works, old rejected immediately
  Given an owner-role JWT and an active key (old_key_id, old_plaintext_key)
  When  POST /admin/keys/{old_key_id}/rotate (no body overrides)
  Then  201 with new_key_id != old_key_id, superseded_key_id == old_key_id,
        new plaintext key in body (shown once)
  And   new key row has rotated_from_key_id == old_key_id
  And   old key row has revoked_at set (non-null)
  And   POST /internal/authz with old_plaintext_key → 401 ERR_AUTH_INVALID_KEY
  And   POST /internal/authz with new plaintext key → 200

Scenario: Rotate inherits governance fields from old key when not overridden
  Given an active key with monthly_budget_usd="20.00", model_allowlist=["openai/gpt-4o"]
  When  POST /admin/keys/{key_id}/rotate (no body overrides)
  Then  201 and new key row has monthly_budget_usd="20.00",
        model_allowlist=["openai/gpt-4o"]

Scenario: Rotate with explicit override replaces only supplied fields
  Given an active key with monthly_budget_usd="20.00", model_allowlist=["openai/gpt-4o"],
        expires_at="2099-01-01T00:00:00Z"
  When  POST /admin/keys/{key_id}/rotate with monthly_budget_usd="30.00"
  Then  201 and new key row has monthly_budget_usd="30.00",
        model_allowlist=["openai/gpt-4o"] (inherited), expires_at="2099-01-01T00:00:00Z" (inherited)

# ── M8 / R9: Expiry enforcement ───────────────────────────────────────────────

Scenario: Expired key is rejected at the proxy hot path
  Given an active key with expires_at set 1 second in the past (already expired)
  And   a model in the catalog
  When  POST /v1/chat/completions with the expired key
  Then  401 ERR_AUTH_KEY_EXPIRED (problem+json)
  And   upstream is never called (upstream.calls == 0)
  And   no usage_record row is written

# ── M9 / R10: Model allowlist enforcement ────────────────────────────────────

Scenario: Model not in allowlist is rejected at the proxy hot path
  Given an active key with model_allowlist=["openai/gpt-4o"]
  And   models openai/gpt-4o and openai/gpt-3.5-turbo both active in the catalog
  When  POST /v1/chat/completions with model="openai/gpt-3.5-turbo" and the key
  Then  403 ERR_MODEL_NOT_ALLOWED (problem+json)
  And   upstream is never called
  And   no usage_record row is written

Scenario: Model in allowlist passes enforcement
  Given an active key with model_allowlist=["openai/gpt-4o"]
  And   model openai/gpt-4o active in the catalog
  When  POST /v1/chat/completions with model="openai/gpt-4o" and the key
  Then  200 (upstream called once)

Scenario: Null model_allowlist allows all models
  Given an active key with model_allowlist=null
  And   any model active in the catalog
  When  POST /v1/chat/completions with that model
  Then  200 (upstream called once)

Scenario: Empty model_allowlist blocks all models
  Given an active key with model_allowlist=[] (empty array)
  And   a model active in the catalog
  When  POST /v1/chat/completions with that model
  Then  403 ERR_MODEL_NOT_ALLOWED

# ── M10 / R11: Per-key budget enforcement ────────────────────────────────────

Scenario: Per-key hard budget exceeded blocks completion
  Given an active key with monthly_budget_usd="10.00"
  And   per-key Redis spend counter at "10.00" (spend == budget)
  When  POST /v1/chat/completions with that key
  Then  402 ERR_BUDGET_EXCEEDED
  And   upstream is never called (upstream.calls == 0)
  And   no usage_record row is written

Scenario: Per-key budget null falls back to tenant budget check
  Given an active key with monthly_budget_usd=null
  And   tenant budget_usd_monthly="5.00"
  And   tenant Redis spend counter at "5.00" (tenant spend == tenant budget)
  When  POST /v1/chat/completions with that key
  Then  402 ERR_BUDGET_EXCEEDED (tenant budget enforced)

Scenario: Per-key budget set below tenant budget — key budget wins (most-specific)
  Given an active key with monthly_budget_usd="3.00"
  And   tenant budget_usd_monthly="10.00"
  And   per-key Redis spend counter at "3.00" (key spend == key budget)
  And   tenant Redis spend counter at "1.00" (tenant spend < tenant budget)
  When  POST /v1/chat/completions with that key
  Then  402 ERR_BUDGET_EXCEEDED (key budget enforced, not tenant budget)

# ── R1–R8: Input / lifecycle rejections ──────────────────────────────────────

Scenario: Create key with soft_budget > monthly_budget rejected
  Given an owner JWT
  When  POST /admin/keys with monthly_budget_usd="10.00", soft_budget_usd="15.00"
  Then  422 ERR_PAYLOAD_INVALID
  And   no api_keys row created

Scenario: Create key with negative monthly_budget rejected
  Given an owner JWT
  When  POST /admin/keys with monthly_budget_usd="-1.00"
  Then  422 ERR_PAYLOAD_INVALID

Scenario: Create key with model_allowlist containing empty string rejected
  Given an owner JWT
  When  POST /admin/keys with model_allowlist=["openai/gpt-4o", ""]
  Then  422 ERR_PAYLOAD_INVALID

Scenario: PATCH revoked key returns 404
  Given an owner JWT and a key that has been revoked
  When  PATCH /admin/keys/{key_id} with monthly_budget_usd="5.00"
  Then  404 ERR_KEY_NOT_FOUND
  And   DB row is unchanged (revoked_at still set, budget still null)

Scenario: PATCH cross-tenant key returns 404
  Given owner JWTs for tenants A and B; key k belongs to B
  When  tenant A sends PATCH /admin/keys/{k.key_id}
  Then  404 ERR_KEY_NOT_FOUND (no cross-tenant leak)

Scenario: Member cannot rotate a key
  Given an active key and a member-role JWT for the same tenant
  When  POST /admin/keys/{key_id}/rotate
  Then  403 ERR_AUTH_FORBIDDEN
  And   old key is still active (/internal/authz still returns 200)

Scenario: Rotate already-revoked key returns 404
  Given an owner JWT and a key that has been revoked
  When  POST /admin/keys/{key_id}/rotate
  Then  404 ERR_KEY_NOT_FOUND
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/keys
  body: {
    "name": string (1–200 chars, required),
    "monthly_budget_usd": string | null  (optional; decimal string e.g. "50.00"; >= 0),
    "soft_budget_usd":    string | null  (optional; decimal string; >= 0; <= monthly_budget_usd when both set),
    "expires_at":         string | null  (optional; ISO-8601 UTC timestamptz),
    "model_allowlist":    [string] | null (optional; non-empty strings; null = all allowed)
  }
  201 -> {
    "key_id":             uuid,
    "name":               string,
    "key":                string,   -- plaintext "sk-<hex>.<secret>", shown ONCE
    "monthly_budget_usd": string | null,
    "soft_budget_usd":    string | null,
    "expires_at":         string | null,   -- ISO-8601 UTC
    "model_allowlist":    [string] | null
  }
  422 -> { "code": "ERR_PAYLOAD_INVALID" }   -- negative/invalid budget, soft>hard, empty allowlist element

PATCH /admin/keys/{key_id}
  body: {                                    -- all fields optional; omit = no change
    "monthly_budget_usd": string | null,
    "soft_budget_usd":    string | null,
    "expires_at":         string | null,
    "model_allowlist":    [string] | null
  }
  200 -> KeyInfoResponse (see GET /admin/keys item shape)
  403 -> { "code": "ERR_AUTH_FORBIDDEN" }    -- member role
  404 -> { "code": "ERR_KEY_NOT_FOUND" }     -- revoked or cross-tenant (identical response — no leak)
  422 -> { "code": "ERR_PAYLOAD_INVALID" }   -- same budget/allowlist validations

POST /admin/keys/{key_id}/rotate
  body: {                                    -- all optional; omit = inherit from old row
    "monthly_budget_usd": string | null,
    "soft_budget_usd":    string | null,
    "expires_at":         string | null,
    "model_allowlist":    [string] | null
  }
  201 -> {
    "new_key_id":        uuid,
    "superseded_key_id": uuid,
    "key":               string,   -- plaintext new secret, shown ONCE
    "name":              string,
    "monthly_budget_usd": string | null,
    "soft_budget_usd":    string | null,
    "expires_at":         string | null,
    "model_allowlist":    [string] | null
  }
  403 -> { "code": "ERR_AUTH_FORBIDDEN" }    -- member role
  404 -> { "code": "ERR_KEY_NOT_FOUND" }     -- revoked or cross-tenant

GET /admin/keys  (EXTENDED — additive, backward-compatible)
  200 -> [
    {
      "key_id":             uuid,
      "name":               string,
      "prefix":             string,
      "created_at":         string,
      "revoked_at":         string | null,
      "monthly_budget_usd": string | null,   -- NEW
      "soft_budget_usd":    string | null,   -- NEW
      "expires_at":         string | null,   -- NEW
      "model_allowlist":    [string] | null  -- NEW
    }
  ]

POST /v1/chat/completions  (ENFORCEMENT — hot path, no new endpoint)
  auth: Bearer sk-<hex>.<secret>
  401 -> { "code": "ERR_AUTH_KEY_EXPIRED" }     -- post-identification, distinct from INVALID_KEY
         IMPORTANT: ERR_AUTH_INVALID_KEY responses for unknown/revoked/malformed keys remain
         byte-identical per the v1 frozen rule; ERR_AUTH_KEY_EXPIRED is ONLY emitted when
         the key authenticates successfully (hash matches, not revoked) but has expired.
  403 -> { "code": "ERR_MODEL_NOT_ALLOWED" }    -- model not in model_allowlist
  402 -> { "code": "ERR_BUDGET_EXCEEDED" }      -- per-key budget enforced; tenant budget as fallback

Schema DDL (additive migration — revises: ad14442336db):
  ALTER TABLE api_keys
    ADD COLUMN monthly_budget_usd  NUMERIC(12,2)  NULL,
    ADD COLUMN soft_budget_usd     NUMERIC(12,2)  NULL,
    ADD COLUMN expires_at          TIMESTAMPTZ    NULL,
    ADD COLUMN model_allowlist     JSONB          NULL,
    ADD COLUMN rotated_from_key_id UUID           NULL
      REFERENCES api_keys(id) ON DELETE SET NULL;
  -- Partial check: soft_budget_usd <= monthly_budget_usd when both non-null
  -- Implemented as: CHECK (soft_budget_usd IS NULL OR monthly_budget_usd IS NULL
  --                        OR soft_budget_usd <= monthly_budget_usd)
  Downgrade: DROP COLUMN for each of the 5 columns; DROP CONSTRAINT.

Migration revision: <next Alembic hash — generated at build time, revises ad14442336db>

Redis key for per-key spend counter:
  usage:spend:key:{key_id}:{YYYYMM}   -- Decimal string; INCRBYFLOAT; same TTL policy as tenant counter

Soft-budget seam (expose, not alert):
  AuthzResult gains: soft_budget_usd: Decimal | None, key_spend_usd: Decimal | None
  CompletionUseCase computes soft_budget_exceeded = (
      soft_budget_usd is not None and key_spend_usd is not None
      and key_spend_usd >= soft_budget_usd
  )
  This boolean is available on the request context for spend-windows/health-alerting tasks.
  No HTTP error; no blocking on the proxy path.

Modules touched (hard boundary for the builder — no other modules):
  gateway/keys/domain/entities.py       -- extend ApiKey, ApiKeyInfo, AuthzResult
  gateway/keys/domain/ports.py          -- extend ApiKeyRepository (create, update, get_by_id)
  gateway/keys/domain/errors.py         -- add KeyExpiredError (domain), ModelNotAllowedError (domain)
  gateway/keys/application/use_cases.py -- extend CreateKeyUseCase, AuthzUseCase; add UpdateKeyUseCase, RotateKeyUseCase
  gateway/keys/infrastructure/orm.py    -- add 5 new mapped_columns to ApiKeyRow
  gateway/keys/infrastructure/repository.py -- extend create(), get_by_id(); add update(), rotate()
  gateway/keys/api/schemas.py           -- extend CreateKeyRequest/Response, KeyInfoResponse; add PatchKeyRequest, RotateKeyRequest/Response
  gateway/keys/api/router.py            -- add PATCH /admin/keys/{key_id} and POST /admin/keys/{key_id}/rotate
  gateway/keys/api/deps.py              -- add get_update_key_use_case, get_rotate_key_use_case
  gateway/proxy/application/use_cases.py -- extend _authenticate() to enforce expiry+allowlist+per-key budget
  gateway/proxy/infrastructure/key_authenticator.py -- pass-through (unchanged if AuthzResult extended)
  gateway/budgets/infrastructure/redis_guard.py     -- extend or companion for per-key counter check
  gateway/usage/application/recorder.py             -- extend to INCRBYFLOAT per-key counter
  apps/gateway/migrations/versions/<hash>_key_governance_fields.py  -- new additive migration
```

Status: FROZEN @ v3 — approved by Tin Dang (delegated auto mode, 2026-06-11; v3 roadmap confirmed "Proceed as drafted").
Least-sure flag surfaced at freeze:
⚠ [spec] (A1) governance fields ride on the existing AuthzResult struct — frozen v1 authz
  tests assert via HTTP body only (confirmed), so the extension is additive; cost if wrong:
  builder must introduce a wrapper struct and refactor proxy use_cases + key_authenticator.
⚠ [contract] (A2) per-key spend tests PRE-SEED the Redis counter, so the
  RecordingUsageRecorder per-key INCRBYFLOAT increment is NOT end-to-end validated here —
  the enforcement gate is; the increment path is a declared seam owned by spend-windows;
  cost if wrong: per-key budgets enforce against a counter nobody increments until
  spend-windows lands (verify phase must confirm the increment is at least wired).

⚠ FREEZE FLAG CANDIDATES (lowest-confidence first — block approval until resolved):

1. [spec] A1 — AuthzResult extension strategy: adding governance fields to AuthzResult
   (currently frozen @ v1 proxy-completions contract) with defaults is the proposed
   approach. Risk: the frozen proxy/domain/ports.py KeyAuthenticator Protocol pins
   return type as AuthzResult. Adding optional fields to the dataclass is backward-
   compatible in Python duck-typing but is technically a change to a frozen entity.
   MUST confirm: are any existing tests asserting on AuthzResult field count or doing
   dataclass equality with exhaustive keyword args? If yes, those tests would break —
   violating the no-test-edit rule on frozen suites. Cost if wrong: redesign to a
   wrapper struct, refactoring the proxy use case.

2. [spec/contract] A2 — Per-key spend counter increment: RecordingUsageRecorder
   (usage module) must also INCRBYFLOAT the per-key counter. This crosses module
   boundary (usage module depends on a key-governance decision). If the builder
   forgets this, per-key budget enforcement is silently a no-op at runtime despite
   all tests passing (tests inject fakes that simulate the counter). MUST call out
   explicitly in the Build safety rule.

<!-- EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY
     + the bundle's lowest-confidence flag was surfaced at the freeze. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of new code paths (enforcement branches, rotation atomicity, CRUD)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_create_key_with_all_governance_fields: arrange signup+login / act POST /admin/keys with all governance fields / assert 201 + fields in response + DB row values
  - test_create_key_defaults_to_unlimited: arrange signup+login / act POST /admin/keys bare / assert null governance fields in response + list echoes null
  - test_patch_key_updates_budget_and_allowlist: arrange active key / act PATCH / assert 200 + updated fields in GET
  - test_rotate_key_new_works_old_rejected: arrange active key / act POST rotate / assert 201 shape + old rejected 401 + new authenticated 200
  - test_rotate_inherits_governance_fields: arrange governed key / act rotate no overrides / assert new row inherits fields
  - test_rotate_with_override_replaces_only_supplied: arrange governed key / act rotate with partial override / assert only overridden field changed
  - test_expired_key_rejected_at_proxy: arrange expired key / act completions / assert 401 ERR_AUTH_KEY_EXPIRED + upstream.calls==0 + no usage_record
  - test_model_not_in_allowlist_rejected: arrange key with allowlist / act completions wrong model / assert 403 ERR_MODEL_NOT_ALLOWED + upstream.calls==0
  - test_model_in_allowlist_allowed: arrange key with allowlist / act completions allowed model / assert 200 + upstream.calls==1
  - test_null_allowlist_allows_all_models: arrange key with null allowlist / act completions / assert 200
  - test_empty_allowlist_blocks_all_models: arrange key with [] allowlist / act completions / assert 403 ERR_MODEL_NOT_ALLOWED
  - test_per_key_budget_exceeded_blocks_completion: arrange key with budget + Redis counter at budget / act completions / assert 402 + upstream.calls==0
  - test_per_key_budget_null_falls_back_to_tenant: arrange key budget=null + tenant budget at limit / act completions / assert 402
  - test_per_key_budget_wins_over_tenant_budget: arrange key budget exhausted + tenant budget ok / act completions / assert 402 (key wins)
  - test_soft_budget_not_soft_budget_exceeded_rejection: Completion still succeeds when per-key spend crosses soft_budget_usd (no HTTP error — seam only)
  - test_create_key_soft_greater_than_hard_rejected: act POST with soft>hard / assert 422 ERR_PAYLOAD_INVALID + no row
  - test_create_key_negative_budget_rejected: act POST with negative budget / assert 422
  - test_create_key_empty_allowlist_element_rejected: act POST with [""] / assert 422
  - test_patch_revoked_key_returns_404: arrange revoked key / act PATCH / assert 404 ERR_KEY_NOT_FOUND
  - test_patch_cross_tenant_key_returns_404: arrange key on tenant B / act PATCH as tenant A / assert 404
  - test_member_cannot_rotate: arrange member JWT / act rotate / assert 403 + old key still active
  - test_rotate_revoked_key_returns_404: arrange revoked key / act rotate / assert 404
</test_plan>

Tests live in: `apps/gateway/tests/key_governance/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `apps/gateway/tests/key_governance/` -->

Right-reason red evidence (confirmed 2026-06-11):
  18 FAILED / 4 PASSED — the 4 that pass are CORRECT baseline-preservation tests:
    - test_model_in_allowlist_allowed   — passes because current proxy has no enforcement (null=allow is the existing behavior); MUST stay green post-build when allowlist check is correct
    - test_null_allowlist_allows_all_models — same; null allowlist = unlimited (existing behavior)
    - test_per_key_budget_null_falls_back_to_tenant — passes because existing RedisBudgetGuard already enforces tenant budget; key budget=null should fall back (existing logic)
    - test_soft_budget_crossing_does_not_block — passes because no enforcement exists yet; explicitly tests that soft budget NEVER blocks
  18 failures sorted by right reason:
    - KeyError 'monthly_budget_usd' on POST response   → schema not extended (2 tests)
    - 405 Method Not Allowed on PATCH /admin/keys/{id} → route not registered (3 tests)
    - 404 Not Found on POST /admin/keys/{id}/rotate    → route not registered (4 tests)
    - expected 401 ERR_AUTH_KEY_EXPIRED got 200        → expiry enforcement absent (1 test)
    - expected 403 ERR_MODEL_NOT_ALLOWED got 200       → allowlist enforcement absent (2 tests)
    - expected 402 ERR_BUDGET_EXCEEDED got 200         → per-key budget enforcement absent (2 tests)
    - expected 422 got 201 (governance fields accepted without validation) → input validation absent (3 tests)
    - expected 404 ERR_KEY_NOT_FOUND got 405           → PATCH route not registered (2 tests)
  Existing suite: 142 passed (unaffected — confirmed same run)

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  - Rotation: old-key revoke + new-key insert MUST be in a single DB transaction; never
    allow both keys to be simultaneously active or simultaneously absent.
  - Enforcement: expiry and allowlist are fail-closed (no infrastructure fail-open);
    per-key budget Redis failure is fail-open (advisory counter, availability-over-enforcement).
  - RecordingUsageRecorder MUST increment usage:spend:key:{key_id}:{YYYYMM} in addition to
    the tenant counter — if this is omitted, per-key budget is silently a no-op.
  - Never expose key_hash, soft_budget_usd, or any spend counter value in any API response.
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

Watch (reuse scenarios as monitors): ERR_AUTH_KEY_EXPIRED rate · ERR_MODEL_NOT_ALLOWED rate ·
  ERR_BUDGET_EXCEEDED rate (per-key vs tenant) · rotation event count · PATCH latency
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
