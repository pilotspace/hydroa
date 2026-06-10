# TASK: Key issue/revoke (shown once, SHA-256), /internal/authz for Envoy

slug: api-keys · created: 2026-06-10 · stage: mvp · autonomy: auto
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant API keys — issue/revoke via JWT-authed admin API; validate via /internal/authz for Envoy ext_authz
Framings weighed: SHA-256 + O(1) key_id lookup (chosen — high-entropy secret makes slow hashing unnecessary; sub-millisecond authz path mandatory) · argon2 (rejected for API keys — argon2 is correct for low-entropy passwords but adds 50–200 ms per verify on the proxy hot path; GLOSSARY amended below) · opaque tokens with server-side lookup only (rejected — no embedded key_id means full-table scan per request)
Must:
<must>
  - POST /admin/keys with {name} (JWT, role owner|admin only) creates an API key for the caller's tenant; returns 201 {key_id, name, key} where key = "sk-<key_id_hex>.<secret>", secret is 32 bytes urlsafe-base64-encoded random; plaintext key shown EXACTLY ONCE in the create response and never stored
  - The secret is stored only as its SHA-256 hash (bytea/text); the format "sk-<key_id_hex>.<secret>" lets the server extract key_id for O(1) indexed lookup before comparing the hash in constant time
  - GET /admin/keys (JWT, any role) returns 200 with the list of keys for the caller's tenant: {key_id, name, prefix, created_at, revoked_at}; the secret and hash are NEVER included in any list/get response
  - DELETE /admin/keys/{key_id} (JWT, role owner|admin only) soft-revokes the key by setting revoked_at; returns 204; a revoked key fails authz immediately
  - POST /internal/authz with header X-Api-Key validates the raw key: extracts key_id, looks up the row, compares SHA-256(secret) in constant time; returns 200 {tenant_id, key_id} on success or 401 problem+json ERR_AUTH_INVALID_KEY for unknown, malformed, or revoked keys — identical response for all failure modes (no enumeration)
  - All key queries are scoped by tenant_id extracted from the JWT (admin endpoints) or derived from the looked-up row (/internal/authz); a key from tenant A is invisible to tenant B
  - All error responses are RFC 9457 problem+json carrying a machine-readable code
</must>
Reject:
<reject>
  - POST /admin/keys by a member role → ERR_AUTH_FORBIDDEN (403)
  - POST /admin/keys with missing or invalid JWT → ERR_AUTH_INVALID_TOKEN (401)
  - POST /admin/keys with missing or empty name (or name > 200 chars) → ERR_PAYLOAD_INVALID (422)
  - DELETE /admin/keys/{key_id} where key_id belongs to a different tenant or does not exist → ERR_KEY_NOT_FOUND (404) — identical response, no cross-tenant information leak
  - DELETE /admin/keys/{key_id} by a member role → ERR_AUTH_FORBIDDEN (403)
  - POST /internal/authz with a revoked key → ERR_AUTH_INVALID_KEY (401)
  - POST /internal/authz with a malformed key (wrong prefix, missing dot, non-hex key_id, bad secret length) → ERR_AUTH_INVALID_KEY (401)
  - POST /internal/authz with an unknown key_id → ERR_AUTH_INVALID_KEY (401)
  - POST /internal/authz with a valid key_id but wrong secret → ERR_AUTH_INVALID_KEY (401)
</reject>
After:
<after>
  - A new api_keys row exists (id, tenant_id, name, key_hash, created_at, revoked_at NULL); the plaintext key is in the 201 response body only; subsequent GET /admin/keys shows the key without hash or secret; DELETE sets revoked_at and the key is immediately rejected by /internal/authz; the platform's Envoy edge can validate any active key sub-millisecond via /internal/authz
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ SHA-256 instead of argon2 for API key storage — lowest confidence because GLOSSARY.md currently reads "stored as argon2 hash" and MILESTONE.md repeats the same; this task deliberately amends that for API keys only (passwords remain argon2). Rationale: API key secrets are 32 bytes of CSPRNG output (256 bits entropy), making dictionary/brute-force attacks computationally infeasible regardless of hash speed; argon2 at a sensible cost factor adds 50–200 ms per request on the /internal/authz hot path, which violates the sub-millisecond authz requirement. If wrong (team prefers consistency over latency): switch to argon2-cffi with time_cost=1, memory_cost=65536 — contained infrastructure change, contract shape unchanged, authz p99 will rise to ~80 ms (acceptable if not on the streaming fast path).
  ⚠ Soft-delete (revoked_at timestamp) rather than hard-delete of revoked keys — lowest confidence because hard-delete is simpler but destroys audit trail; if wrong (hard-delete preferred): remove revoked_at column, add a separate audit_log table — migration required, contract for the list endpoint changes (no revoked_at field in response).
  - [x] key_id is a uuidv7 (reuses core/ids.uuid7); embedded in the plaintext key as hex for O(1) lookup without an index scan
  - [x] /internal/authz is an internal endpoint (no JWT); Envoy calls it directly; it must not require database schema beyond api_keys
  - [x] The prefix field in GET /admin/keys response is the first 8 characters of the full key string (e.g. "sk-1a2b3c") for UI display; it does not expose the secret
  - [x] hashlib.sha256 and secrets are Python stdlib — no new allowlist entries needed for the hashing path
  - [x] Member role users MAY list keys (GET /admin/keys) — they work with the proxy and need to know which keys exist; they may not create or revoke
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: owner creates an API key — plaintext shown once
  Given owner Ada is authenticated (JWT, role owner)
  When she POSTs /admin/keys with name "prod-key"
  Then the response is 201 with key_id, name "prod-key", and key matching "sk-<hex>.<secret>"
  And exactly one api_keys row exists for Ada's tenant with key_hash = SHA-256(secret) and revoked_at NULL
  And the plaintext secret does not appear in any subsequent response

Scenario: member cannot create a key
  Given member Bob is authenticated (JWT, role member)
  When he POSTs /admin/keys with name "any"
  Then the response is 403 with code "ERR_AUTH_FORBIDDEN"
  And no api_keys row was created

Scenario: create key with missing JWT is rejected
  Given no Authorization header
  When POST /admin/keys is called
  Then the response is 401 with code "ERR_AUTH_INVALID_TOKEN"
  And no api_keys row was created

Scenario: create key with empty name is rejected
  Given owner Ada is authenticated
  When she POSTs /admin/keys with an empty string name
  Then the response is 422 with code "ERR_PAYLOAD_INVALID"
  And no api_keys row was created

Scenario: list keys returns all non-revoked and revoked keys without secrets
  Given Ada has created two keys, one of which has been revoked
  When she GETs /admin/keys
  Then the response is 200 with both keys listed
  And each item has key_id, name, prefix, created_at, revoked_at (null or timestamp)
  And no item contains a "key", "key_hash", or "secret" field

Scenario: list keys is tenant-scoped — other tenant's keys not visible
  Given tenant Acme (Ada) has one key and tenant Beta (Eve) has one key
  When Ada GETs /admin/keys
  Then she sees exactly one key (her own)
  And Eve's key_id does not appear in the response

Scenario: owner revokes a key — authz fails immediately
  Given Ada has an active key K
  When she DELETEs /admin/keys/{K.key_id}
  Then the response is 204
  And a subsequent POST /internal/authz with key K returns 401 ERR_AUTH_INVALID_KEY

Scenario: delete key from another tenant returns 404 not 403
  Given Ada (tenant Acme) has key K_acme and Eve (tenant Beta) has key K_beta
  When Ada attempts DELETE /admin/keys/{K_beta.key_id}
  Then the response is 404 with code "ERR_KEY_NOT_FOUND"
  And K_beta is still active in tenant Beta

Scenario: member cannot revoke a key
  Given member Bob is authenticated and key K exists in his tenant
  When he DELETEs /admin/keys/{K.key_id}
  Then the response is 403 with code "ERR_AUTH_FORBIDDEN"
  And K is still active (revoked_at remains NULL)

Scenario: /internal/authz validates a valid active key
  Given Ada created key K (plaintext "sk-<hex>.<secret>")
  When POST /internal/authz is called with header X-Api-Key: sk-<hex>.<secret>
  Then the response is 200 with tenant_id = Ada's tenant and key_id = K.key_id

Scenario: /internal/authz rejects a revoked key
  Given Ada revoked key K
  When POST /internal/authz is called with X-Api-Key: <K's original plaintext>
  Then the response is 401 with code "ERR_AUTH_INVALID_KEY"

Scenario: /internal/authz rejects a malformed key without leaking information
  Given any state
  When POST /internal/authz is called with X-Api-Key values that are malformed (wrong prefix, no dot, truncated)
  Then every response is 401 with code "ERR_AUTH_INVALID_KEY"
  And the response body is byte-identical across all malformed variants

Scenario: /internal/authz rejects a key with valid key_id but wrong secret
  Given Ada has active key K
  When POST /internal/authz is called with X-Api-Key: sk-<K.key_id_hex>.<wrong_secret>
  Then the response is 401 with code "ERR_AUTH_INVALID_KEY"
  And the response is byte-identical to the unknown-key-id case (constant-time, no enumeration)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/keys            header: Authorization: Bearer <jwt> (role: owner|admin)
                            body: { name: str(1..200) }
  201 -> { key_id: uuid, name: str, key: "sk-<key_id_hex>.<urlsafe_b64_secret>" }
  401 -> problem+json { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> problem+json { code: "ERR_AUTH_FORBIDDEN" }
  422 -> problem+json { code: "ERR_PAYLOAD_INVALID" }

GET /admin/keys             header: Authorization: Bearer <jwt> (any role)
  200 -> [ { key_id: uuid, name: str, prefix: str, created_at: datetime, revoked_at: datetime|null } ]
  401 -> problem+json { code: "ERR_AUTH_INVALID_TOKEN" }

DELETE /admin/keys/{key_id} header: Authorization: Bearer <jwt> (role: owner|admin)
  204 -> (no body)
  401 -> problem+json { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> problem+json { code: "ERR_AUTH_FORBIDDEN" }
  404 -> problem+json { code: "ERR_KEY_NOT_FOUND" }

POST /internal/authz        header: X-Api-Key: sk-<key_id_hex>.<secret>
  200 -> { tenant_id: uuid, key_id: uuid }
  401 -> problem+json { code: "ERR_AUTH_INVALID_KEY" }

problem+json shape (RFC 9457, platform-wide):
  { type: "about:blank", title: str, status: int, code: "ERR_*", detail?: str }

Key format: "sk-" + key_id.hex + "." + base64url(32 random bytes, no padding)
  key_id: uuidv7 (reuses core.ids.uuid7)
  secret: secrets.token_urlsafe(32) — 32 bytes = 43 chars urlsafe-base64 (no padding)
  key_hash: hashlib.sha256(secret.encode()).hexdigest() stored as TEXT (or bytea)

Schema: api_keys(id uuid PK DEFAULT uuid7,
                 tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
                 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
                 key_hash text NOT NULL,
                 created_at timestamptz NOT NULL DEFAULT now(),
                 revoked_at timestamptz NULL)
Index: (id) — primary; (tenant_id) — for scoped list; (id, tenant_id) — for scoped delete/revoke

Access:
  create  — INSERT api_keys row; key_hash = sha256(secret); return plaintext once
  list    — SELECT WHERE tenant_id = <from_jwt> ORDER BY created_at DESC
  revoke  — UPDATE SET revoked_at = now() WHERE id = <key_id> AND tenant_id = <from_jwt>
            — returns 0 rows affected → 404 ERR_KEY_NOT_FOUND (covers both unknown and cross-tenant)
  authz   — parse key_id from header; SELECT WHERE id = <key_id>; if not found or revoked → 401;
            compare sha256(submitted_secret) == stored key_hash in constant time (hmac.compare_digest)

GLOSSARY amendment: for API keys specifically, "stored as SHA-256 hash" replaces "stored as argon2
hash" (GLOSSARY line 5). Passwords (users.password_hash) remain argon2. The distinction:
  passwords — low entropy (user-chosen), must resist offline brute-force → argon2
  API key secrets — 256-bit CSPRNG output, brute-force infeasible → SHA-256 sufficient, sub-ms verify
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [spec] SHA-256 instead of argon2 for API key secrets — GLOSSARY.md currently mandates argon2 for all keys; this contract deliberately amends that for API keys only because the /internal/authz hot path requires sub-millisecond verification and 32-byte random secrets make offline attacks infeasible regardless of hash speed. If the team rejects this amendment: switch to argon2 (time_cost=1) — contract shape unchanged, authz p99 rises to ~80 ms, may need async thread pool offload to avoid blocking the event loop.
⚠ [contract] soft-delete via revoked_at timestamp — simpler than hard-delete but adds a nullable column to every list row; if the team prefers hard-delete: remove revoked_at, add separate audit table, list endpoint drops the revoked_at field; migration required.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_owner_creates_key_plaintext_shown_once: arrange owner JWT / act POST /admin/keys {name} / assert 201, key matches sk-hex.secret format, one api_keys row exists with key_hash = sha256(secret), revoked_at NULL; GET list does not contain the secret
  - test_member_cannot_create_key: arrange member JWT / act POST /admin/keys / assert 403 ERR_AUTH_FORBIDDEN + row count unchanged
  - test_create_key_no_jwt_rejected: act POST /admin/keys without Authorization / assert 401 ERR_AUTH_INVALID_TOKEN
  - test_create_key_empty_name_rejected: arrange owner JWT / act POST {name: ""} / assert 422 ERR_PAYLOAD_INVALID + no row created
  - test_list_keys_returns_all_without_secrets: arrange owner with two keys (one revoked) / act GET /admin/keys / assert 200, two items, each has key_id/name/prefix/created_at/revoked_at, no "key"/"key_hash"/"secret" field in any item
  - test_list_keys_tenant_scoped: arrange two tenants each with one key / act GET /admin/keys as tenant A / assert only one key visible, tenant B's key_id absent
  - test_revoke_key_fails_authz_immediately: arrange owner + active key / act DELETE /admin/keys/{key_id} / assert 204; then POST /internal/authz with that key / assert 401 ERR_AUTH_INVALID_KEY
  - test_delete_cross_tenant_key_returns_404: arrange two tenants / act tenant A deletes tenant B's key / assert 404 ERR_KEY_NOT_FOUND + tenant B's key still active in authz
  - test_member_cannot_revoke_key: arrange member JWT + key in same tenant / act DELETE /admin/keys/{key_id} / assert 403 ERR_AUTH_FORBIDDEN + key still passes authz
  - test_authz_valid_active_key: arrange active key / act POST /internal/authz X-Api-Key: <plaintext> / assert 200 {tenant_id, key_id}
  - test_authz_revoked_key_rejected: arrange revoked key / act POST /internal/authz / assert 401 ERR_AUTH_INVALID_KEY
  - test_authz_malformed_key_rejected: act POST /internal/authz with wrong-prefix / no-dot / truncated variants / assert all 401 ERR_AUTH_INVALID_KEY, response bodies byte-identical
  - test_authz_wrong_secret_rejected_constant_time: arrange active key K / act POST /internal/authz with K's key_id but wrong secret / assert 401 ERR_AUTH_INVALID_KEY, body byte-identical to unknown-key-id case
</test_plan>

Tests live in: `apps/gateway/tests/keys/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): plaintext key material MUST NOT appear in logs, exception messages, or any response beyond the initial 201 create; sha256 compare MUST use hmac.compare_digest (constant-time) to prevent timing-based secret enumeration; every /internal/authz failure path returns byte-identical responses regardless of failure reason.
Code lives in: `apps/gateway/src/gateway/keys/` (domain/ application/ infrastructure/ api/)
Constraints: do NOT change any test or the contract; allow-list packages only (hashlib, secrets, hmac are stdlib — no new entries needed); ask if unclear.

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

Watch (reuse scenarios as monitors): 401 rate on /internal/authz (credential stuffing / leaked key signal) · key creation rate per tenant (anomaly detection) · p99 /internal/authz latency (must stay sub-millisecond; regression = hash algo change or index miss)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [DDD · open] GLOSSARY "argon2 for all keys" conflicts with hot-path latency requirements for high-entropy API key secrets — evidence: §1 assumption ⚠ flag; amend GLOSSARY when frozen.
- [ADD · open] lowest-confidence flag surfaced a spec/GLOSSARY inconsistency before any code was written — confirms freeze as the right gate for cross-artifact consistency.
