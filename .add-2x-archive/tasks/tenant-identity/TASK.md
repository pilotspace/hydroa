# TASK: Signup (tenant+owner atomic), login → JWT, roles

slug: tenant-identity · created: 2026-06-10 · stage: mvp · autonomy: auto
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant identity — self-serve signup, login → JWT, roles
Framings weighed: identity-as-gateway-module (chosen) · external IdP delegation (rejected at setup: D3 self-hosted) · signup-as-invite-flow (rejected at scope: v1 is self-serve)
Must:
<must>
  - POST /admin/auth/signup with {tenant_name, email, password} creates one Tenant and its owner User ATOMICALLY (both rows or neither)
  - Password is stored only as an argon2 hash; plaintext never persisted or logged
  - POST /admin/auth/login with valid credentials returns a JWT (HS256, TTL 24h) whose claims carry sub=user_id, tenant_id, role, iat, exp, iss="ai-proxy"
  - GET /admin/auth/me with a valid Bearer JWT returns {user_id, tenant_id, email, role}
  - The first user of a tenant has role "owner"; role is one of owner|admin|member (enum)
  - Email comparison is case-insensitive; one account per email across the platform
  - All error responses are RFC 9457 problem+json carrying a machine-readable `code`
</must>
Reject:
<reject>
  - signup with an email that already exists -> "ERR_TENANT_EMAIL_TAKEN" (409)
  - signup with password shorter than 10 chars -> "ERR_AUTH_PASSWORD_WEAK" (400)
  - signup/login with malformed payload (bad email, missing/empty fields) -> "ERR_PAYLOAD_INVALID" (422)
  - login with unknown email OR wrong password -> "ERR_AUTH_INVALID_CREDENTIALS" (401) — identical response both cases, no user enumeration
  - /me with missing, malformed, expired, or wrong-signature token -> "ERR_AUTH_INVALID_TOKEN" (401)
</reject>
After:
<after>
  - A Tenant row and its owner User row exist (created in one transaction); the owner can log in and the JWT identifies their tenant and role; /me echoes that identity; the platform has gained exactly one new tenant
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ HS256 (shared secret) JWT for MVP instead of RS256+JWKS — lowest confidence because Envoy jwt_authn will need the signing secret in its config (edge-envoy task); if wrong: swap to RS256 needs the `cryptography` package (allowlist addition) + a JWKS endpoint — claims unchanged, contained contract change
  ⚠ Tests run against a real PostgreSQL (CI service container + local docker compose) — lowest confidence because it adds infra to the pipeline; if wrong (slow/flaky): fall back to per-test transaction rollback fixtures — small rework, no contract impact
  - [x] Password policy = min 10 chars only (no complexity rules); email verification is explicitly OUT (milestone Out list)
  - [x] No invite/member-creation flow in this task — only the owner via signup (dashboard-shell needs nothing more)
  - [x] JWT TTL 24h, no refresh token in v1 (dashboard re-login daily is acceptable at MVP)
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: signup creates tenant and owner atomically
  Given no account exists for "ada@acme.io"
  When she signs up with tenant_name "Acme", email "ada@acme.io" and a valid password
  Then the response is 201 with tenant_id and user_id
  And exactly one tenant named "Acme" and one user with role "owner" exist for it

Scenario: signup with taken email is rejected
  Given an account already exists for "ada@acme.io"
  When someone signs up again with "ada@acme.io" (any case variant)
  Then the response is 409 with code "ERR_TENANT_EMAIL_TAKEN"
  And no new tenant row and no new user row were created

Scenario: signup with weak password is rejected
  Given no account exists for "bob@acme.io"
  When he signs up with a 9-character password
  Then the response is 400 with code "ERR_AUTH_PASSWORD_WEAK"
  And no tenant row and no user row were created

Scenario: signup with malformed payload is rejected
  Given any starting state
  When a signup is posted with an invalid email or missing field
  Then the response is 422 with code "ERR_PAYLOAD_INVALID"
  And no tenant row and no user row were created

Scenario: login returns a tenant-scoped JWT
  Given "ada@acme.io" signed up with tenant "Acme"
  When she logs in with the right password
  Then the response is 200 with an access_token whose claims carry her user_id (sub), her tenant_id, role "owner", iss "ai-proxy" and a 24h expiry
  And token_type is "bearer"

Scenario: login with wrong password is rejected without enumeration
  Given "ada@acme.io" exists
  When she logs in with a wrong password
  Then the response is 401 with code "ERR_AUTH_INVALID_CREDENTIALS"
  And the response body is byte-identical to the unknown-email case
  And no token was issued

Scenario: login with unknown email is rejected identically
  Given no account exists for "ghost@acme.io"
  When a login is attempted for it
  Then the response is 401 with code "ERR_AUTH_INVALID_CREDENTIALS"
  And the response body is byte-identical to the wrong-password case

Scenario: me returns the authenticated identity
  Given Ada holds a valid JWT
  When she calls GET /admin/auth/me with it
  Then the response is 200 with her user_id, tenant_id, email and role

Scenario: me with an invalid token is rejected
  Given a missing, expired, or wrong-signature token
  When GET /admin/auth/me is called with it
  Then the response is 401 with code "ERR_AUTH_INVALID_TOKEN"
  And no identity information is leaked in the response
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/auth/signup   body: { tenant_name: str(1..120), email: EmailStr, password: str }
  201 -> { tenant_id: uuid, user_id: uuid }
  409 -> problem+json { code: "ERR_TENANT_EMAIL_TAKEN" }
  400 -> problem+json { code: "ERR_AUTH_PASSWORD_WEAK" }
  422 -> problem+json { code: "ERR_PAYLOAD_INVALID" }

POST /admin/auth/login    body: { email: EmailStr, password: str }
  200 -> { access_token: str(JWT), token_type: "bearer", expires_in: 86400 }
  401 -> problem+json { code: "ERR_AUTH_INVALID_CREDENTIALS" }
  422 -> problem+json { code: "ERR_PAYLOAD_INVALID" }

GET /admin/auth/me        header: Authorization: Bearer <jwt>
  200 -> { user_id: uuid, tenant_id: uuid, email: str, role: "owner"|"admin"|"member" }
  401 -> problem+json { code: "ERR_AUTH_INVALID_TOKEN" }

problem+json shape (RFC 9457, all errors platform-wide):
  { type: "about:blank", title: str, status: int, code: "ERR_*", detail?: str }

JWT: HS256 · secret from settings (env GATEWAY_JWT_SECRET) · TTL 86400s
  claims: { sub: user_id, tenant_id: uuid, role: str, iat, exp, iss: "ai-proxy" }

Schema: tenants(id uuidv7 PK, name text NOT NULL, created_at timestamptz)
        users(id uuidv7 PK, tenant_id FK->tenants ON DELETE RESTRICT,
              email text NOT NULL UNIQUE on lower(email), password_hash text NOT NULL,
              role text CHECK in (owner|admin|member), created_at timestamptz)
Access: signup = single transaction INSERT tenant + INSERT user; login = SELECT user
        by lower(email); /me = JWT decode only (no DB hit)
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [contract] HS256 shared-secret JWT instead of RS256+JWKS — lowest confidence because Envoy jwt_authn must hold the signing secret in edge config; if wrong: swap to RS256 needs the `cryptography` package + a JWKS endpoint — claims unchanged, contained change.
⚠ [test] tests bind to a real Postgres (CI service container + local compose) — adds pipeline infra; if wrong (slow/flaky): fall back to transaction-rollback fixtures — small rework, contract untouched.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_signup_creates_tenant_and_owner_atomically: arrange clean db / act POST signup / assert 201 + tenant&owner rows exist with role owner
  - test_signup_taken_email_rejected: arrange existing account / act POST signup same email (upper-cased) / assert 409 ERR_TENANT_EMAIL_TAKEN + row counts unchanged
  - test_signup_weak_password_rejected: act POST signup 9-char password / assert 400 ERR_AUTH_PASSWORD_WEAK + no rows created
  - test_signup_malformed_payload_rejected: act POST signup invalid email / assert 422 ERR_PAYLOAD_INVALID + no rows created
  - test_login_returns_tenant_scoped_jwt: arrange signup / act POST login / assert 200, decode JWT, claims sub/tenant_id/role/iss/exp≈24h, token_type bearer
  - test_login_wrong_password_no_enumeration: arrange signup / act login wrong pw + login ghost email / assert both 401 ERR_AUTH_INVALID_CREDENTIALS with identical bodies, no token
  - test_me_returns_identity: arrange signup+login / act GET /admin/auth/me / assert 200 user_id/tenant_id/email/role
  - test_me_invalid_token_rejected: act /me with none, expired, and wrong-signature tokens / assert 401 ERR_AUTH_INVALID_TOKEN each, no identity fields in body
</test_plan>

Tests live in: `apps/gateway/tests/tenants/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): tenant + owner INSERT happen in ONE database transaction — a failure of either rolls back both; password plaintext never touches a log line or an exception message.
Code lives in: `apps/gateway/src/gateway/` (modules `tenants/`, `core/`)
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 14 passed (12 scenario tests + 2 config-guard), `make ci` exit 0
- [x] coverage did not decrease — 94.6% vs 85% target / 80% floor
- [x] no test or contract was altered during build — contract untouched; `ruff format` reflowed
      whitespace in the test file (assertions byte-equivalent); one ADDITIVE test file
      (test_config.py) strengthens the suite, weakens nothing
- [x] concurrency / timing of the risky operation is safe — tenant+owner in one DB transaction
      (IntegrityError rolls back both, proven by test_signup_taken_email_rejected row counts);
      argon2 dummy-hash verify equalizes login timing for unknown emails
- [x] no exposed secrets, injection openings, or unexpected dependencies — ORM-parameterized
      queries only; plaintext password never logged/persisted; dev JWT secret fail-fasts outside
      dev/test (test_production_refuses_dev_jwt_secret); deps all on allowlist (gate OK)
- [x] layering & dependencies follow CONVENTIONS.md — clean architecture: domain (zero framework
      imports) ← application ← infrastructure/api; composition root in main.create_app
- [x] a person reviewed and approved the change — delegated auto mode (standing approval, Tin
      Dang 2026-06-10); bundle + evidence reported in chat

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: entities/ports ← use_cases ← deps.py wiring ←
      router ← main.create_app (composition root); uuid7 ← orm defaults + repository; confirmed
      via import graph + 94.6% coverage (no module below 77%)
- [x] DEAD-CODE (code) — flat-module files (models/security/schemas.py) DELETED in the clean-arch
      restructure; no orphaned symbol remains (ruff F401/F841 clean across 23 files)
- [x] SEMANTIC (prose / non-code) — n/a, code path applies

### GATE RECORD
Outcome: PASS  (auto-resolved under autonomy: auto — evidence complete: tests green ·
coverage held · contract untouched · security checks closed; no escalating residue)
Reviewed by: Claude (Fable 5) under delegated auto mode, standing approval Tin Dang · date: 2026-06-10

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): signup error rate · 401 rate on login (credential stuffing signal) · p99 login latency (argon2 cost)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
