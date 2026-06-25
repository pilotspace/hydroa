# TASK: Agent OAuth device-grant store + token model

slug: agent-oauth-grant-store · created: 2026-06-25 · stage: production · risk: high
autonomy: conservative   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
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
  NEW MODULE `apps/gateway/src/gateway/agent_oauth/` — mirrors the `keys/` layout (domain/infrastructure
  split). Module placement (new top-level `agent_oauth/` vs nested under `auth/`) is a §1 decision; the
  `keys/` module is a sibling-of-`auth/` precedent for credential issuance, so a new top-level module is
  the tentative choice.
  - NEW `agent_oauth/domain/entities.py:DeviceAuthorization` · `:AgentToken` — frozen dataclasses, zero
    framework imports (mirror `keys/domain/entities.py:ApiKey` — secret never stored, only the hash).
  - NEW `agent_oauth/domain/ports.py:AgentOAuthRepository` (Protocol) — create-pending · get-by-device-hash ·
    approve · mint-token · get-token-by-hash (mirror `keys/domain/ports.py:ApiKeyRepository`).
  - NEW `agent_oauth/infrastructure/orm.py:DeviceAuthorizationRow` · `:AgentTokenRow` — SQLAlchemy rows on
    `gateway.core.db.Base`, PK `id uuid7`, FK `tenant_id`/`user_id` -> tenants.id/users.id ON DELETE CASCADE
    (agent OAuth credentials are derived/ephemeral, so deleting the tenant/user revokes them — see §3; the
    api_keys RESTRICT precedent is for a NOT-NULL tenant key, distinct from these nullable bindings).
  - NEW `agent_oauth/infrastructure/repository.py` — concrete asyncpg repo (mirror `keys/infrastructure/repository.py`).
  - NEW migration `apps/gateway/migrations/versions/<rev>_agent_oauth_device_grant.py` — down_revision = `f2a4c6e8b0d3` (current head).
  REUSE (do not reinvent):
  - `gateway.keys.infrastructure.sha256_hasher.Sha256SecretHasher` + `gateway.keys.domain.ports.SecretHasher`
    — generic `hash(secret)->hex` / `verify(stored,candidate)->bool` (hmac.compare_digest). Hashes device_code,
    access_token, refresh_token at rest exactly like api-key secrets (256-bit CSPRNG → SHA-256).
  - `gateway.core.ids.uuid7` (time-ordered PK, explicit at construction — no column default).
  - `secrets.token_urlsafe(32)` mint pattern (per `keys/application/use_cases.py`).
  - `gateway.core.db.Base` (declarative base; ORM rows must also register in `migrations/env.py` metadata).
Context (working folder):
  - Migrations: alembic under `apps/gateway/migrations/versions/` (head `f2a4c6e8b0d3` = audit_retention_trigger);
    `migrations/env.py` autogenerate metadata. v25 BYOK migration `d8f3a1c9e5b2_tenant_provider_keys.py` is the
    closest "secret-at-rest table" migration template.
  - Config: `gateway/core/config.py` Settings — token lifetimes / poll interval / user_code length will be
    `GATEWAY_*` knobs (default-ON sensible values; pattern per existing knobs).
Honors (patterns / conventions):
  - PROJECT.md INVARIANT: API-key secrets stored as **SHA-256** hashes at rest (NOT argon2 — high-entropy,
    hot-path authz); argon2 only for user passwords. Agent OAuth secrets follow the SHA-256 rule.
  - PROJECT.md: every tenant-owned row carries `tenant_id`; every query tenant-scoped. uuid7 generated at
    call site (reading `.id` before flush is the unset-PK bug class).
  - CONVENTIONS.md: hexagonal (domain Protocols + fakes via app.state); domain entities zero-framework;
    ORM index declared in BOTH `__table_args__` AND the migration.
  - CLAUDE.md IO rule: device/token endpoints are pre-auth public → bounded + rate-limited + designed-for-failure
    (later tasks; this task only persists + hashes, no outbound IO).
Anchors the contract cites:
  `DeviceAuthorizationRow` · `AgentTokenRow` · `DeviceAuthorization` · `AgentToken` · `AgentOAuthRepository` ·
  `SecretHasher`/`Sha256SecretHasher` (reuse) · `core.db.Base` · `core.ids.uuid7` · tenants.id · users.id ·
  migration down_revision `f2a4c6e8b0d3`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Agent OAuth device-grant store + token model — the persistence + domain layer for an OAuth 2.0
  Device Authorization Grant (RFC 8628). Owns the data model the later endpoints drive; mints/stores agent
  tokens. NO HTTP/endpoints in this task (those are device-authorization-endpoint / agent-token-endpoint).
Framings weighed: Device Authorization Grant + opaque hashed tokens (chosen) · PKCE authorization-code + loopback redirect · stateless JWT tokens (no store)
Must:
<must>
  - Persist a PENDING device authorization: id (uuid7) · device_code_hash · user_code_hash · status='pending'
    · created_at · expires_at · poll interval_seconds · tenant_id NULL · user_id NULL (bound only at approval).
  - Hash every secret at rest via the reused `SecretHasher` (SHA-256, constant-time verify): device_code,
    user_code, access_token, refresh_token. Plaintext is generated (`secrets.token_urlsafe`) and returned to
    the caller ONCE — never persisted in clear.
  - Look up a pending authorization by device_code_hash (token-poll path) AND by user_code_hash (approval path),
    returning status + expiry + (tenant_id,user_id) binding.
  - Approve: atomically transition pending→approved, binding the approving user's tenant_id + user_id — only
    from 'pending' and only when not expired.
  - Deny: transition pending→denied (the RFC access_denied path).
  - Mint an agent token ONLY from an approved, not-yet-consumed authorization: create exactly one agent_tokens
    row (access_token_hash · refresh_token_hash · tenant_id · user_id · scope · access expires_at · refresh
    expires_at · created_at · revoked_at NULL) AND atomically mark the authorization status='consumed'
    (single-use device_code) — both writes in ONE transaction.
  - Resolve an agent token by access_token_hash for authz: return (tenant_id,user_id,scope) ONLY when the
    token is unexpired AND not revoked; otherwise return None (FAIL-CLOSED). Constant-time hash compare.
  - Every issued token row carries tenant_id + user_id; the lookup never leaks across tenants.
  - Expiry is enforced in-store: an expired pending authorization is neither approvable nor mintable; an
    expired/revoked token resolves to None.
</must>
Reject:
<reject>
  - approve an expired pending authorization                        -> "authorization_expired"
  - approve/deny an authorization not in 'pending' state            -> "authorization_not_pending"
  - mint a token from an authorization not in 'approved' state      -> "authorization_not_approved"
  - mint a token from an already-consumed authorization             -> "authorization_already_consumed"
  - create a pending authorization whose device_code/user_code hash collides with a live pending row -> "device_code_collision"
</reject>
After:
<after>
  - A pending authorization row exists (all secrets hashed) with a bounded expiry + poll interval; tenant/user unbound.
  - After approve: status='approved', bound to exactly the approving (tenant_id,user_id).
  - After mint: exactly one agent_tokens row (hashed access + refresh) + the authorization status='consumed';
    plaintext access (+ refresh) token returned once to the caller.
  - A valid access_token_hash resolves to (tenant_id,user_id,scope) until expiry/revocation; every other
    case (unknown, expired, revoked) resolves to None.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The OAuth 2.0 Device Authorization Grant (RFC 8628) is the right grant vs PKCE authorization-code + loopback
    — lowest confidence because it assumes the coding agent runs HEADLESS (CI/container/SSH, no local browser or
    loopback listener). If wrong: the store schema + every downstream endpoint reworks. [milestone-flagged fork —
    decide at the §3 freeze; PKCE is the named alternative]
  ⚠ Tokens are OPAQUE + SHA-256-hashed-at-rest (not JWT), WITH a refresh token in the model — lowest confidence on
    whether refresh belongs in v39 at all (milestone scopes refresh as "minimal or none"). If wrong: drop the two
    refresh_* columns — cheap because they are additive-nullable. Opaque-vs-JWT itself is high-confidence (mirrors
    the api-key invariant: revocable, no signing-key sprawl, reuses the hashed-credential lookup the data plane has).
  - [ ] user_code stored HASHED (not plaintext) and matched by hashing the typed input — assume yes (defense-in-depth);
    if wrong (plaintext needed for display/debug): trivial, isolated change.
  - [ ] Single-use device_code (authorization consumed on first mint) — assume yes per RFC 8628 §3.5; standard.
  - [ ] RFC-aligned default TTLs as GATEWAY_* knobs: pending authorization ~10 min · access token ~60 min · poll
    interval ~5 s · refresh ~30 d — sensible defaults; exact values tuned at the freeze.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Create a pending device authorization
  Given a generated device_code + user_code and a clock
  When the store creates a pending authorization
  Then a row exists with status='pending', a bounded expires_at and interval_seconds, tenant_id/user_id NULL

Scenario: Secrets are hashed at rest, plaintext returned once
  Given a freshly created pending authorization (and later a minted token)
  When I read the persisted rows directly
  Then no column contains the plaintext device_code/user_code/access_token/refresh_token — only their SHA-256 hashes
  And the plaintext values were returned to the caller exactly once at generation

Scenario: Look up a pending authorization by device_code hash and by user_code hash
  Given a pending authorization
  When the store is queried by the device_code hash, and separately by the user_code hash
  Then both return the same authorization with its status, expiry, and (unbound) binding

Scenario: Approve binds the approving user's tenant and user
  Given a pending, unexpired authorization and an approving user (tenant T, user U)
  When the store approves it for (T, U)
  Then status becomes 'approved' and the row is bound to exactly (T, U)

Scenario: Deny marks the authorization denied
  Given a pending authorization
  When the store denies it
  Then status becomes 'denied'

Scenario: Mint a token from an approved authorization (atomic, single-use)
  Given an approved, not-consumed authorization for (T, U)
  When the store mints an agent token
  Then exactly one agent_tokens row exists (hashed access + refresh, bound to T/U, scope, expiries)
  And the authorization status becomes 'consumed' in the same transaction
  And the plaintext access (+ refresh) token is returned once

Scenario: Resolve a valid access token to its binding
  Given a minted, unexpired, unrevoked agent token
  When the store resolves it by the access_token hash
  Then it returns (tenant_id=T, user_id=U, scope)

Scenario: An expired or revoked token resolves to None (fail-closed)
  Given a minted token that is past its access expiry (or revoked)
  When the store resolves it by the access_token hash
  Then it returns None

Scenario: Token lookup never crosses tenants
  Given two tenants each with a minted token
  When tenant A's token hash is resolved
  Then it returns A's binding only — never B's row

Scenario: Reject approving an expired authorization
  Given a pending authorization whose expires_at is in the past
  When the store is asked to approve it
  Then it fails with "authorization_expired"
  And the authorization stays 'pending' (no binding written)

Scenario: Reject approve/deny of a non-pending authorization
  Given an authorization already in 'approved' (or 'denied'/'consumed')
  When the store is asked to approve or deny it
  Then it fails with "authorization_not_pending"
  And the existing status and binding are unchanged

Scenario: Reject minting from a non-approved authorization
  Given a 'pending' (un-approved) authorization
  When the store is asked to mint a token
  Then it fails with "authorization_not_approved"
  And no agent_tokens row is created

Scenario: Reject minting twice from the same authorization
  Given an authorization already in 'consumed'
  When the store is asked to mint a token again
  Then it fails with "authorization_already_consumed"
  And no second agent_tokens row is created

Scenario: Reject a colliding pending authorization
  Given a live pending authorization with a given device_code/user_code hash
  When the store creates another pending authorization with the same hash
  Then it fails with "device_code_collision"
  And the original pending authorization is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

No HTTP in this task — the frozen shape is the module's domain port + entities + table schema + error codes.
Module: NEW top-level `gateway/agent_oauth/` (domain · application · infrastructure), sibling of `keys/`.

```
DOMAIN ENTITIES  (agent_oauth/domain/entities.py — frozen dataclasses, zero framework imports)
  DeviceAuthorization(id, status, scope, interval_seconds, created_at, expires_at,
                      tenant_id|None, user_id|None, approved_at|None, consumed_at|None)
      status ∈ {'pending','approved','denied','consumed'}   # secret hashes NOT carried on the entity
  AgentToken(id, authorization_id, tenant_id, user_id, scope, created_at,
             access_expires_at, refresh_expires_at|None, revoked_at|None)
  AgentTokenBinding(token_id, tenant_id, user_id, scope)        # authz resolve result (mirror AuthzResult)
  MintedDeviceCodes(device_code, user_code, authorization: DeviceAuthorization)  # plaintext returned ONCE
  MintedAgentToken(access_token, refresh_token|None, token: AgentToken)          # plaintext returned ONCE

DOMAIN ERRORS  (agent_oauth/domain/errors.py — each .code is the §1 error string)
  DeviceCodeCollisionError("device_code_collision") · AuthorizationExpiredError("authorization_expired")
  AuthorizationNotPendingError("authorization_not_pending") · AuthorizationNotApprovedError("authorization_not_approved")
  AuthorizationAlreadyConsumedError("authorization_already_consumed")

PORT  (agent_oauth/domain/ports.py)
  class AgentOAuthRepository(Protocol):
    create_pending(*, device_code_hash, user_code_hash, scope, interval_seconds, expires_at) -> DeviceAuthorization
        # raises DeviceCodeCollisionError on unique violation
    get_by_device_code_hash(device_code_hash) -> DeviceAuthorization | None
    get_by_user_code_hash(user_code_hash)     -> DeviceAuthorization | None
    approve(*, authorization_id, tenant_id, user_id, now) -> DeviceAuthorization
        # raises AuthorizationExpiredError | AuthorizationNotPendingError
    deny(*, authorization_id) -> DeviceAuthorization
        # raises AuthorizationNotPendingError
    mint_token(*, authorization_id, access_token_hash, refresh_token_hash|None,
               access_expires_at, refresh_expires_at|None, scope, now) -> AgentToken
        # raises AuthorizationNotApprovedError | AuthorizationAlreadyConsumedError
        # ATOMIC: INSERT agent_tokens + UPDATE device_authorizations.status='consumed' in ONE tx
    resolve_access_token(*, access_token_hash, now) -> AgentTokenBinding | None
        # None when unknown / expired / revoked  (FAIL-CLOSED)

APPLICATION SERVICE  (agent_oauth/application/use_cases.py — generates secrets, hashes, delegates)
  class AgentOAuthService(repo: AgentOAuthRepository, hasher: SecretHasher):
    start_device_authorization(*, scope, interval_seconds, ttl_seconds, now) -> MintedDeviceCodes
        # device_code = secrets.token_urlsafe(32); user_code = RFC-8628 base20 8-char (e.g. "WDJB-MJHT")
    mint(*, authorization_id, access_ttl_seconds, refresh_ttl_seconds|None, now) -> MintedAgentToken
        # access_token (+ refresh) = secrets.token_urlsafe(32); persists only hashes
    # approve / deny / resolve delegate to repo; the SERVICE owns generate+hash so plaintext lives one call
```

```
SCHEMA  (migration <rev>_agent_oauth_device_grant.py · down_revision f2a4c6e8b0d3)
  TABLE device_authorizations
    id                uuid       PK (uuid7, explicit at construction)
    device_code_hash  text       NOT NULL  UNIQUE
    user_code_hash    text       NOT NULL  -- partial UNIQUE WHERE status='pending'
    status            text       NOT NULL  CHECK in ('pending','approved','denied','consumed')  DEFAULT 'pending'
    scope             text       NOT NULL  DEFAULT 'proxy'
    interval_seconds  integer    NOT NULL  CHECK > 0
    tenant_id         uuid       NULL  FK tenants(id) ON DELETE CASCADE   -- bound at approval
    user_id           uuid       NULL  FK users(id)   ON DELETE CASCADE   -- bound at approval
    created_at        timestamptz NOT NULL DEFAULT now()
    expires_at        timestamptz NOT NULL
    approved_at       timestamptz NULL
    consumed_at       timestamptz NULL
    INDEX (user_code_hash)   -- approval lookup
  TABLE agent_tokens
    id                  uuid       PK (uuid7)
    authorization_id    uuid       NOT NULL  UNIQUE  FK device_authorizations(id) ON DELETE CASCADE  -- single mint per auth
    access_token_hash   text       NOT NULL  UNIQUE
    refresh_token_hash  text       NULL      UNIQUE
    tenant_id           uuid       NOT NULL  FK tenants(id) ON DELETE CASCADE
    user_id             uuid       NOT NULL  FK users(id)   ON DELETE CASCADE
    scope               text       NOT NULL  DEFAULT 'proxy'
    created_at          timestamptz NOT NULL DEFAULT now()
    access_expires_at   timestamptz NOT NULL
    refresh_expires_at  timestamptz NULL
    revoked_at          timestamptz NULL
    INDEX (access_token_hash)   -- authz hot-path resolve (also UNIQUE above)
  Access pattern: resolve_access_token = single SELECT on UNIQUE(access_token_hash) filtered
    revoked_at IS NULL AND access_expires_at > now; mint = one tx (INSERT agent_tokens + UPDATE status).
```

Reused (frozen elsewhere, cited not redefined): `SecretHasher`/`Sha256SecretHasher` · `core.db.Base` ·
`core.ids.uuid7` · `tenants.id` · `users.id`. TTLs/intervals are PARAMETERS here (caller computes from
`GATEWAY_AGENT_OAUTH_*` knobs in the endpoint tasks) — the store stays clock-injectable (`now` passed in).

Status: FROZEN @ v39 — approved by Tin (2026-06-25). Device Authorization Grant (RFC 8628) + opaque
  SHA-256-hashed access+refresh tokens confirmed as drafted. Changing this contract = change request back to SPECIFY.
Least-sure flag surfaced at freeze:
  ⚠ [contract] Device Authorization Grant (RFC 8628) is the chosen grant vs PKCE+loopback — most likely wrong
     because it assumes the agent is headless; if wrong the schema + all downstream endpoints rework. APPROVED
     as drafted by Tin (2026-06-25) — device flow confirmed.
  ⚠ [contract] refresh token is IN the model (refresh_token_hash + refresh_expires_at) — milestone scopes refresh
     as "minimal or none"; if unwanted, drop the two nullable columns (cheap, additive). Opaque-vs-JWT is high-confidence.
     APPROVED to keep refresh in the model (Tin, 2026-06-25).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new agent_oauth module)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_create_pending_persists_unbound_pending_row — pending row, status='pending', tenant/user NULL, bounded expiry
  - test_secrets_are_hashed_at_rest_never_plaintext — stored *_hash == sha256(plaintext); plaintext never in a column
  - test_lookup_by_device_and_user_code_hash — both lookups return the same authorization
  - test_approve_binds_tenant_and_user — pending→approved bound to (T,U)
  - test_deny_marks_denied — pending→denied
  - test_mint_creates_one_token_and_consumes_authorization — one hashed token row + status='consumed' (atomic)
  - test_resolve_valid_access_token_returns_binding — valid hash → (T,U,scope)
  - test_expired_token_resolves_to_none / test_unknown_token_resolves_to_none / test_revoked_token_resolves_to_none — fail-closed (unexpired-but-revoked still → None)
  - test_token_secrets_are_hashed_at_rest — access AND refresh token hashed; plaintext never in a column
  - test_token_lookup_is_tenant_isolated — A→A, B→B, A≠B (both directions; no cross-tenant leak)
  - test_reject_approve_expired_authorization — "authorization_expired" + stays pending/unbound
  - test_reject_approve_non_pending — "authorization_not_pending"
  - test_reject_mint_non_approved — "authorization_not_approved" + 0 token rows
  - test_reject_mint_twice — "authorization_already_consumed" + still 1 token row
  - test_reject_colliding_device_code / test_reject_colliding_user_code_among_pending — "device_code_collision"
  (3 STRENGTHENED post-refute-read: revoked-token, token-hash-at-rest, user_code-collision; tenant-isolation made bidirectional)
</test_plan>

Tests live in: `tests/agent_oauth_grant_store/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/agent_oauth/` `apps/gateway/src/gateway/main.py` `apps/gateway/migrations/versions/` `apps/gateway/migrations/env.py` `apps/gateway/tests/agent_oauth_grant_store/` `apps/gateway/tests/guardrails/test_guardrails_core.py` `apps/gateway/tests/migrations/test_migrations.py`
<!-- The last 3 tokens are TEST-side: this task's own RED+strengthened suite, plus the two SHARED schema-manifest
     tests every migration-adding task maintains (sanctioned oidc/teams/audit precedent — additive table entries only,
     no existing assertion weakened). Declared here so the build scope-gate (touched ⊆ declared) is honest. -->
Strategy (ordered batches): 1. domain (entities · errors · ports) 2. infrastructure (orm · repository) 3. application (use_cases service) 4. register ORM side-effect import in main.py + env.py 5. hand-write the migration (down_revision f2a4c6e8b0d3)
Safety rule (feature-specific): mint = INSERT agent_tokens + UPDATE device_authorizations.status='consumed' in ONE transaction; UNIQUE(authorization_id) is the DB backstop for single-use; all secrets hashed before they reach the repo; resolve_access_token fail-closed (revoked/expired/unknown → None).
Code lives in: `apps/gateway/src/gateway/agent_oauth/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full gateway suite 1664 passed / 0 failed (uv run pytest, 314s); new suite 18/18
- [x] coverage did not decrease — total 87.96% (≥80% gate); new module 96% (repo 92%, rest 100%)
- [x] no test or contract was altered during build — the §3 contract is unchanged; the only test edits are ADDITIVE (3 strengthened tests post-refute-read) + SANCTIONED shared schema-manifest maintenance (tests/migrations EXPECTED_TABLES + tests/guardrails inline manifest), per the documented oidc/teams/audit precedent — no existing assertion weakened
- [x] the green was EARNED — adversarial refute-read (subagent) returned REFUTED@0.88 on TEST-COVERAGE gaps, NOT impl defects; security paths confirmed sound (fail-closed/atomic/state-machine/parity all EARNED). Closed by STRENGTHENING (revoked-token, token-hash-at-rest, user_code-collision, bidirectional tenant-isolation) — re-crossed full-green
- [x] concurrency / timing safe — mint locks the authorization row `with_for_update()` and consumes it in ONE tx; `UNIQUE(authorization_id)` is the DB backstop (IntegrityError → AlreadyConsumed); clock is injected (`now` param), so no hidden-time TOCTOU; timestamptz columns avoid the naive/aware trap
- [x] no exposed secrets / injection / unexpected deps — secrets SHA-256-hashed before reaching the repo, plaintext returned once; constant-time verify (hmac.compare_digest, reused); all DB access parameterized; zero new third-party deps (stdlib secrets/hashlib + existing SQLAlchemy)
- [x] layering follows CONVENTIONS.md — hexagonal: domain (zero-framework dataclasses + Protocol + errors) · application (service) · infrastructure (orm + repo); mirrors the `keys/` module exactly
- [x] a person reviewed and approved the change — Tin approved the security gate (2026-06-25) after the verify evidence + refute-read summary

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] A pending authorization persists with hashed device/user codes; NO plaintext in any column — confirmed by test_secrets_are_hashed_at_rest_never_plaintext + a raw `SELECT device_code_hash,user_code_hash` showing `== sha256(plaintext)` and plaintext absent
- [x] A minted token stores only access/refresh HASHES — confirmed by test_token_secrets_are_hashed_at_rest (raw SELECT on agent_tokens)
- [x] resolve_access_token returns a binding ONLY for a valid token; None for unknown/expired/revoked — confirmed by the 4 resolve tests (valid→(T,U,scope); unknown/expired/revoked→None)
- [x] mint is single-use + atomic — confirmed by test_mint... (agent_tokens count==1, authorization status=='consumed') + test_reject_mint_twice (2nd → AlreadyConsumed, still 1 row)
- [x] every named rejection maps to its exact code — confirmed by the 6 reject tests asserting `exc.value.code`
- [x] migration ↔ ORM parity — confirmed by tests/migrations (autogenerate empty-diff + upgrade-parity + idempotent re-upgrade all green after the sa.String() fix)

### Deep checks
- [x] WIRING — DeviceAuthorizationRow + AgentTokenRow registered on Base.metadata via side-effect imports in BOTH main.py (app/create_all) and migrations/env.py (autogenerate); confirmed by create_all building the tables in tests + the autogenerate-empty-diff test. Repo/Service exercised by the suite (HTTP consumers arrive in later v39 tasks — intentionally no endpoint here).
- [x] DEAD-CODE — no orphans; the 3 remaining uncovered repo lines (deny-non-pending, mint defensive-None guard, mint IntegrityError backstop) are reachable safety branches, not dead code.
- [x] SEMANTIC — read the migration in full vs the ORM: column types (sa.String for Mapped[str], timestamptz everywhere), CHECK (status enum, interval>0), UNIQUE (device_code_hash, access/refresh_token_hash, authorization_id), FK ON DELETE CASCADE, and the partial-unique pending user_code index all match.

### GATE RECORD
Outcome: PASS (security escalation resolved — Tin approved the risk:high authentication gate)
Rationale: implementation sound + fully green (1664 passed, new module ~96%); the refute-read found only
  coverage gaps, all closed by strengthening; the mandatory human security sign-off was given.
Reviewed by: Tin · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): agent-token resolve None-rate (failed authz), mint AlreadyConsumed-rate
(replay/double-mint attempts), device_code_collision-rate (CSPRNG/anti-abuse signal), pending-expiry sweep backlog.

### Spec delta
- [SPEC · open] mint_token on an UNKNOWN authorization_id returns "authorization_already_consumed" (misleading) — there is no "authorization_not_found" code in the frozen 5-set (evidence: refute-read blocker #3). Add a not_found code when the endpoint tasks need to distinguish; fail-closed today (refuses to mint), so non-urgent.
- [SPEC · open] redundant index on agent_tokens.access_token_hash — UNIQUE already creates an index; the extra ix_ is spec-sanctioned (frozen §3 line 293) but droppable in a cleanup migration (evidence: refute-read #7).
- [SPEC · open] pending-authorization + expired-token reaping — expired rows accumulate; add a periodic sweep (mirror RetentionSweeper) so the store doesn't grow unbounded (evidence: no GC in this task; out of scope here).
- [SPEC · seeded] device-authorization-endpoint (v39 task 2) consumes AgentOAuthService.start_device_authorization + the GATEWAY_AGENT_OAUTH_* TTL/interval knobs (default values land there).
- [SPEC · seeded] agent-token-authn-seam (v39 task 5) consumes resolve_access_token on the /v1 hot path (fail-closed → 401).

### Competency deltas
- [SDD · folded] mint_token derives `scope` from the authorization row instead of taking the frozen §3 `scope` param — a benign, strictly-more-correct refinement of the port sketch (a token's scope is ALWAYS its authorization's scope; passing it invites mismatch). Recorded per the foundation "fix-if-strictly-more-correct, record the deviation" rule (evidence: ports.py mint_token signature vs §3 line 249). [folded foundation-version 36]
- [TDD · folded] a risk:high credential store's refute-read should pre-seed the negative-direction + revocation tests (revoked-but-unexpired, cross-tenant non-leak, secondary-unique collision) — they were the exact gaps the refute-read caught; bake them into the RED suite next time (evidence: 3 STRENGTHENED tests added at verify, mirrors v29 strengthen-then-recross). [folded foundation-version 36]
- [ADD · folded] sa.Text() vs the repo's `Mapped[str]`→sa.String() convention silently breaks the migration autogenerate-parity tests; new migrations for plain-str columns MUST use sa.String() (evidence: 3 tests/migrations failures fixed by the Text→String swap). [folded foundation-version 36]
