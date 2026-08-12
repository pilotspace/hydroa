# TASK: Public RFC 8628 token endpoint (device_code grant)

slug: agent-token-endpoint · created: 2026-06-25 · stage: production · risk: high
autonomy: conservative   <!-- risk:high — PUBLIC token-MINTING endpoint (issues the agent credential; single-use device_code; anti-abuse + DoS surface). Security HARD-STOP at verify; human owns the gate. -->
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
  NEW `apps/gateway/src/gateway/agent_oauth/api/token_router.py` — the PUBLIC token endpoint
  (`agent_oauth_token_router = APIRouter(prefix="/oauth", tags=["agent-oauth"])`), `POST /token`. No auth (the
  agent is acquiring its credential). Mirrors the task-2 public router (bounded body + per-IP limiter + fail-open).
  - REUSE task-1 (FROZEN): `SqlAlchemyAgentOAuthRepository(session)` ·
    `get_by_device_code_hash(device_code_hash) -> DeviceAuthorization | None` (poll lookup) ·
    `AgentOAuthService.mint(*, authorization_id, access_ttl_seconds, refresh_ttl_seconds, now) -> MintedAgentToken`
    (atomic INSERT agent_tokens + mark authorization consumed; raises `AuthorizationNotApprovedError` |
    `AuthorizationAlreadyConsumedError`). `DeviceAuthorization.status`/`.expires_at`/`.scope`; `MintedAgentToken`
    `.access_token`/`.refresh_token`/`.token.access_expires_at`/`.token.scope`.
  - REUSE `keys/infrastructure/sha256_hasher.py:Sha256SecretHasher` — hash the submitted device_code to look it up.
  - REUSE task-2 `AgentOAuthIpRateLimiter` (app.state.agent_oauth_ip_limiter) — per-IP rate limit on the poll.
  - NEW slow_down enforcement: RFC 8628 §3.5 — if a client polls faster than `interval`, return `slow_down`.
    Lightweight Redis min-interval key per device_code (`agent_oauth:poll:{device_code_hash}`, EX=interval,
    SET NX). If the key already exists → too-fast → slow_down. Fail-OPEN on Redis error (allow the poll).
  - EDIT `apps/gateway/src/gateway/main.py` — `app.include_router(agent_oauth_token_router)`.
  - EDIT `apps/gateway/src/gateway/core/config.py:Settings` — NEW token-lifetime knobs:
    `agent_oauth_access_token_ttl_seconds: int = 3600` (>0) · `agent_oauth_refresh_token_ttl_seconds: int = 2592000`
    (0 = NO refresh token issued; >=0). `agent_oauth_token_rpm: int = 60` (>0, per-IP poll limit). Join the
    positive-knob validator (refresh allows 0 = disabled; access_ttl + token_rpm strictly > 0).
  - REUSE `core/error_catalog.py` — RFC 8628 token errors are returned as plain `{"error": "<rfc-code>"}` bodies
    with HTTP 400 (RFC 8628 §3.5 uses the OAuth 2.0 token-error shape); 429 reuses task-2's rate-limited.
Context (working folder):
  - RFC 8628 §3.4 request: `grant_type=urn:ietf:params:oauth:grant-type:device_code` + `device_code` (form-encoded
    in the RFC; we accept JSON to match the rest of the gateway — note as a §1 assumption). §3.5 polling states:
    authorization_pending · slow_down · access_denied · expired_token (+ OAuth invalid_grant/unsupported_grant_type).
  - The minted token is the THIRD credential class (v39 glossary): carries tenant_id + user_id + scope; the data-plane
    authz seam that CONSUMES it is task 5 (agent-token-authn-seam). This task only ISSUES it.
  - Single-use: task-1 `mint` atomically flips the authorization to 'consumed'; a second poll after mint → consumed.
Honors (patterns / conventions):
  - CLAUDE.md IO rule: public → bounded body, per-IP rate-limit (429+Retry-After), fail-open limiter+slow_down,
    one bounded SELECT + (on approval) one atomic mint tx; no unbounded work.
  - PROJECT.md: token secrets hashed at rest (task-1 mint already enforces); plaintext access/refresh tokens are
    returned ONCE in the 200 body and NEVER logged.
  - CONVENTIONS.md hexagonal: api router → task-1 application/infra; the router owns HTTP + polling-state mapping.
Anchors the contract cites:
  `agent_oauth_token_router` · `get_by_device_code_hash` · `AgentOAuthService.mint` · `MintedAgentToken` ·
  `AuthorizationNotApprovedError` · `AuthorizationAlreadyConsumedError` · `Sha256SecretHasher` ·
  `AgentOAuthIpRateLimiter` · the slow_down Redis key · `Settings.agent_oauth_access_token_ttl_seconds` /
  `_refresh_token_ttl_seconds` / `_token_rpm` · RFC 8628 §3.4/§3.5 codes.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Public RFC 8628 token endpoint — `POST /oauth/token` (device_code grant). The agent polls with its
  device_code; the gateway returns the RFC 8628 §3.5 polling state, and once the human has approved, mints + returns
  the agent access token (single-use device_code) so the agent can call the data plane (task 5).
Framings weighed: thin polling adapter over the FROZEN task-1 mint + poll-lookup (chosen) · re-derive token
  generation in the router (rejected — duplicates the hashing/mint seam) · long-poll/SSE wait (rejected — RFC 8628
  is short-poll by `interval`; keep it stateless + cheap).
Must:
<must>
  - Accept an UNauthenticated POST, JSON body `{ grant_type, device_code }`. `grant_type` MUST be
    `urn:ietf:params:oauth:grant-type:device_code`; anything else → unsupported_grant_type. Bounded body (4096) → 422.
  - Hash the submitted device_code and look it up (task-1 `get_by_device_code_hash`). Map the authorization state to
    the RFC 8628 §3.5 polling response:
      • unknown device_code            → 400 invalid_grant
      • status='pending', not expired  → 400 authorization_pending
      • status='pending', past expiry  → 400 expired_token
      • status='denied'                → 400 access_denied
      • status='consumed' (token already issued) → 400 invalid_grant
      • status='approved'              → MINT (below)
  - MINT on 'approved': call task-1 `AgentOAuthService.mint(...)` with server knobs (access_ttl, refresh_ttl, now);
    it atomically issues the token + flips the authorization to 'consumed' (single-use). Return 200 OAuth token
    response: `access_token` · `token_type:"Bearer"` · `expires_in` · `scope` · `refresh_token` (only when
    refresh_ttl > 0). A mint race (AuthorizationNotApproved/AlreadyConsumed) maps to 400 invalid_grant — never a 500.
  - slow_down (RFC 8628 §3.5): if a client polls the same device_code faster than its `interval`, return 400
    slow_down (Redis min-interval key, SET NX EX=interval, fail-OPEN). The happy poll resets the window.
  - Per-IP rate-limit (`agent_oauth_token_rpm` / 60s, fail-OPEN) bounds device_code brute-force; over → 429 + Retry-After.
  - Plaintext access/refresh tokens appear ONCE in the 200 body and are NEVER logged; ttl/refresh/now are server knobs only.
</must>
Reject:
<reject>
  - malformed / oversized body                                   -> 422 "invalid_request"
  - grant_type != device_code grant                              -> 400 "unsupported_grant_type"
  - missing device_code                                          -> 400 "invalid_request"
  - unknown device_code / already consumed / mint race           -> 400 "invalid_grant"
  - authorization still pending                                  -> 400 "authorization_pending"
  - polling faster than interval                                 -> 400 "slow_down"
  - authorization denied by the user                             -> 400 "access_denied"
  - device_code (pending) past expiry                            -> 400 "expired_token"
  - more than agent_oauth_token_rpm polls from one IP per window  -> 429 "rate_limited" (+ Retry-After)
</reject>
After:
<after>
  - On a successful mint: exactly one agent_tokens row exists for the authorization; the authorization is
    status='consumed'; the 200 body carries the plaintext access_token (+ refresh_token iff enabled) ONCE.
  - A second poll after a successful mint returns 400 invalid_grant (single-use enforced; no second token row).
  - On any polling rejection: the authorization is UNCHANGED (no token minted, status preserved).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Accepting a JSON body (vs RFC 8628 §3.4's `application/x-www-form-urlencoded`) — lowest confidence because a
    strict RFC 8628 client library sends form-encoded; if wrong: accept BOTH content-types (cheap, additive in the
    router), the response contract is unaffected. Chosen JSON to match the rest of the gateway + the harness (task 6).
  - [x] HTTP 400 + OAuth `{"error": "<code>"}` is the right shape for all polling states — confirmed (RFC 8628 §3.5
    defers to RFC 6749 §5.2, which is HTTP 400 + that JSON shape).
  - [x] refresh_token issued only when refresh_ttl > 0 (0 disables) — confirmed against task-1 mint (refresh_ttl=None/0 → no refresh).
  - [x] single-use is enforced by task-1's atomic mint→consumed; a re-poll → consumed → invalid_grant — confirmed.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: poll before approval returns authorization_pending
  Given a pending device authorization with device_code D
  When the agent POSTs /oauth/token {grant_type:device_code, device_code:D}
  Then the response is 400 "authorization_pending"
  And no agent_tokens row is created; the authorization stays pending

Scenario: poll after approval mints the token (happy path)
  Given an APPROVED authorization (bound to tenant T, user U, scope 'proxy') with device_code D
  When the agent POSTs /oauth/token {grant_type:device_code, device_code:D}
  Then the response is 200 with access_token, token_type="Bearer", expires_in=3600, scope="proxy"
  And exactly one agent_tokens row exists and the authorization is now status='consumed'

Scenario: refresh_token issued only when enabled
  Given an approved authorization and agent_oauth_refresh_token_ttl_seconds > 0
  When the agent polls /oauth/token and mints
  Then the 200 body includes a refresh_token
  And with refresh_ttl=0 a separate mint omits refresh_token entirely

Scenario: single-use — second poll after mint is rejected
  Given an approved authorization that was just minted (now 'consumed')
  When the agent POSTs /oauth/token with the same device_code again
  Then the response is 400 "invalid_grant"
  And no second agent_tokens row is created

Scenario: denied authorization returns access_denied
  Given the user denied the authorization (status='denied') with device_code D
  When the agent polls /oauth/token with D
  Then the response is 400 "access_denied"
  And no token is minted

Scenario: expired pending authorization returns expired_token
  Given a pending authorization whose expiry has passed, device_code D
  When the agent polls /oauth/token with D
  Then the response is 400 "expired_token"
  And no token is minted; status stays pending

Scenario: unknown device_code returns invalid_grant
  Given no authorization matches device_code "nope"
  When the agent polls /oauth/token with "nope"
  Then the response is 400 "invalid_grant"
  And nothing is created

Scenario: wrong grant_type is rejected
  Given any request
  When the agent POSTs /oauth/token {grant_type:"authorization_code", device_code:D}
  Then the response is 400 "unsupported_grant_type"
  And no token is minted

Scenario: polling faster than interval returns slow_down
  Given a pending authorization and a fresh poll just made for device_code D
  When the agent POSTs /oauth/token with D again within the interval window
  Then the response is 400 "slow_down"
  And no token is minted

Scenario: malformed body is rejected
  Given any request
  When the agent POSTs non-JSON / an oversized body / missing device_code
  Then the response is 422 "invalid_request" (or 400 invalid_request for missing device_code)
  And no token is minted

Scenario: per-IP rate limit returns 429
  Given agent_oauth_token_rpm=2 and one client IP
  When the client makes a 3rd poll within the 60s window
  Then the response is 429 "rate_limited" with Retry-After
  And no token is minted by the rejected request

Scenario: token secrets hashed at rest
  Given an approved authorization
  When the agent polls and mints
  Then the access_token in the body matches sha256 == the stored agent_tokens.access_token_hash
  And the plaintext token never appears in any column or log line
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /oauth/token   (PUBLIC — no auth)   body (JSON): { "grant_type": string, "device_code": string }
  200 -> {
    "access_token": string,          # opaque, plaintext ONCE (sha256 stored)
    "token_type": "Bearer",
    "expires_in": int,               # = agent_oauth_access_token_ttl_seconds
    "scope": string,                 # the authorization's scope
    "refresh_token"?: string         # present ONLY when agent_oauth_refresh_token_ttl_seconds > 0
  }
  400 -> { "error": "<rfc8628-code>" }   # one of:
            authorization_pending | slow_down | access_denied | expired_token
            | invalid_grant | unsupported_grant_type | invalid_request
  422 -> { "error": "invalid_request" }              # malformed / oversized body
  429 -> { "error": "rate_limited" } + Retry-After   # per-IP poll window exceeded

Server-owned (NOT client-settable): expires_in, refresh lifetime, now, scope (from the authorization).

NEW config (Settings, env_prefix GATEWAY_):
  agent_oauth_access_token_ttl_seconds: int = 3600     # GATEWAY_AGENT_OAUTH_ACCESS_TOKEN_TTL_SECONDS  (>0)
  agent_oauth_refresh_token_ttl_seconds: int = 2592000 # GATEWAY_AGENT_OAUTH_REFRESH_TOKEN_TTL_SECONDS (>=0; 0=no refresh)
  agent_oauth_token_rpm: int = 60                      # GATEWAY_AGENT_OAUTH_TOKEN_RPM                 (>0, per-IP/60s)

NEW symbols:
  agent_oauth/api/token_router.py: agent_oauth_token_router (APIRouter prefix="/oauth")
    · POST /token handler: bounded-body parse → per-IP rate-limit → grant_type check → slow_down check →
      get_by_device_code_hash → state machine → mint-on-approved
    · _DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
  main.py: app.include_router(agent_oauth_token_router)
  slow_down: Redis key agent_oauth:poll:{device_code_hash}, SET NX EX=interval (fail-open)

Reuses (frozen): get_by_device_code_hash · AgentOAuthService.mint(*, authorization_id, access_ttl_seconds,
  refresh_ttl_seconds, now) · MintedAgentToken(.access_token/.refresh_token/.token.access_expires_at/.token.scope)
  · AuthorizationNotApprovedError · AuthorizationAlreadyConsumedError · Sha256SecretHasher ·
  AgentOAuthIpRateLimiter (app.state.agent_oauth_ip_limiter) · app.state.sessionmaker · app.state.redis_client
Schema: NO migration. Access = one SELECT (device_code_hash) + (on approved) the atomic mint tx (INSERT
  agent_tokens + UPDATE authorization→consumed, under task-1's row lock). expires_in derived from the ttl knob.
```

Status: FROZEN @ v39 — lead-frozen under autonomy:auto. Thin RFC 8628 §3.4/§3.5 polling adapter over the FROZEN
  task-1 mint + poll-lookup; introduces NO new authz/binding decision (the privilege grant was frozen in task 3).
  The security review of this PUBLIC token-MINTING endpoint is carried to the VERIFY gate as the milestone-mandated
  HARD-STOP for Tin's sign-off. Change = back to SPECIFY.
Least-sure flag surfaced at freeze:
  ⚠ [contract] JSON body vs RFC 8628 §3.4's form-encoding — a strict RFC client sends
    application/x-www-form-urlencoded; if a real client needs it, accept BOTH content-types (additive in the router,
    response contract unaffected). Chosen JSON to match the gateway + the task-6 harness. Verified at the e2e task.
  ⚠ [contract] refresh_token DEFAULT-ON (30-day ttl) — if Tin prefers no-refresh-by-default, set
    GATEWAY_AGENT_OAUTH_REFRESH_TOKEN_TTL_SECONDS=0 (knob, no code change). Surfaced for the verify gate.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new token_router)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_poll_pending_returns_authorization_pending — 400 + no token row; status pending
  - test_poll_approved_mints_token — 200 access_token/Bearer/expires_in/scope; 1 token row; status='consumed'
  - test_refresh_token_only_when_enabled — refresh_ttl>0 → refresh_token present; refresh_ttl=0 → absent
  - test_single_use_second_poll_invalid_grant — re-poll after mint → 400 invalid_grant; no 2nd row
  - test_denied_returns_access_denied — status denied → 400 access_denied; no mint
  - test_expired_pending_returns_expired_token — past expiry → 400 expired_token; no mint
  - test_unknown_device_code_invalid_grant — no match → 400 invalid_grant
  - test_wrong_grant_type_unsupported — grant_type=authorization_code → 400 unsupported_grant_type
  - test_poll_too_fast_slow_down — 2nd poll within interval → 400 slow_down
  - test_malformed_body_422 / missing device_code → 422/400 invalid_request; no mint
  - test_per_ip_rate_limit_429 — token_rpm=2, 3rd poll → 429 + Retry-After; no mint
  - test_token_hashed_at_rest — body access_token sha256 == stored hash; plaintext not persisted
</test_plan>

Tests live in: `apps/gateway/tests/agent_token_endpoint/` · MUST run red (router/knobs absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/agent_oauth/` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/`
<!-- `apps/gateway/tests/` (broad, per task-1/2/3 precedent). No migration. NEVER touch pyproject.toml or other global config. -->
Strategy (ordered batches): 1. config knobs (access_ttl/refresh_ttl/token_rpm + validator) 2. error_catalog RFC8628 codes (or plain JSONResponse dicts, matching peer routers) 3. token_router (bounded body → per-IP limit → grant_type → slow_down → poll → state-machine → mint) 4. wire main.py include_router
Safety rule (feature-specific): keep the response decision OUTSIDE the async-with session context (coverage+greenlet lesson from task 3); per-IP rate-limit + slow_down both fail-OPEN; mint is task-1's atomic INSERT+consume (single-use); mint-race → 400 invalid_grant never 500; ttl/refresh/now server-owned; plaintext tokens only in the 200 body, never logged.
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

- [x] all tests pass — full gateway suite 1711 passed, 19 deselected, exit 0 (88.09% total); task suite 20 passed
- [x] coverage did not decrease — total 88.09% (≥80% floor held). token_router.py 87% (was 72% pre-refactor);
      the residual gap = the mint await block inside the `async with` session (coverage.py+greenlet under-measures
      lines run inside asyncpg/greenlet — SAME artifact accepted @89% in task-3) + 2 trivially-defensive guards
      (IP "unknown" fallback, Content-Length ValueError). Every branch is exercised by a test. Honest fix =
      project-wide `concurrency=["greenlet"]` coverage config → recorded as a SPEC delta (NOT smuggled here).
- [x] no test or contract was altered during build — §3 FROZEN unchanged; tests only ADDED (the fail-open test)
- [x] the green was EARNED — adversarial refute-read (sonnet) VERDICT=UPHELD @0.92, ZERO blockers; probed
      TOCTOU double-mint (defeated by task-1 FOR UPDATE lock + UNIQUE(authorization_id)), credential leak
      (none — plaintext only in 200 body; logs use hash[:8]), client-controlled ttl/scope/now (all server-owned,
      extra="ignore" drops injected fields). Its NB-1 (shared per-IP counter with /authorize) was FIXED (namespaced
      `token:{ip}` key). NB-2 (approved+expired still mints) confirmed contract-correct (§1 maps approved→mint
      unconditionally; the access token carries its own TTL).
- [x] concurrency / timing safe — mint is task-1's atomic INSERT+consume under a row lock; re-poll → consumed →
      invalid_grant (single-use). slow_down + per-IP limit both fail-OPEN (never block a legit poll on Redis blip).
- [x] no exposed secrets / injection / unexpected deps — plaintext access/refresh/device_code NEVER logged;
      bounded body (4096) before unbounded work; allow-listed deps only (fastapi, pydantic, redis — all in-tree).
- [x] layering & dependencies follow CONVENTIONS.md — api router → task-1 application/infra; router owns only
      HTTP + polling-state mapping; IO inside the session, pure branching outside (hexagonal boundary respected).
- [x] a person reviewed and approved the change — PENDING Tin's security HARD-STOP sign-off (this gate).

### Build expectations — what "correct" looks like
- [x] approved poll → 200 {access_token, token_type:"Bearer", expires_in:3600, scope:"proxy"} + EXACTLY one
      agent_tokens row + authorization status='consumed' — confirmed by test_poll_approved_mints_token (asserts DB)
- [x] single-use: 2nd poll after mint → 400 invalid_grant, NO 2nd token row — confirmed by test_single_use_*
- [x] each polling state maps to its RFC 8628 §3.5 code (pending/expired/denied/unknown/slow_down/wrong-grant) —
      one test per state, each asserting the exact error string + "nothing minted"
- [x] refresh_token present iff refresh_ttl>0 — confirmed by refresh-on (default) vs refresh-off (ttl=0) tests
- [x] token hashed at rest — body access_token sha256 == agent_tokens.access_token_hash (+ refresh) asserted
- [x] Redis outage during slow_down → authorization_pending not 500 — test_slow_down_fails_open_when_redis_down

### Deep checks
- [x] WIRING — agent_oauth_token_router included in main.py; _probe_slow_down + _resolve_client_ip referenced;
      3 new Settings knobs read in the handler; ruff (no unused) + pyright (0 errors) clean on the new files.
- [x] DEAD-CODE — none; the final defensive `return invalid_grant` is `# pragma: no cover` (unreachable guard).
- [x] SEMANTIC — refute-read read the contract + impl + tests in full; UPHELD @0.92.

### GATE RECORD
Outcome: PASS (security HARD-STOP resolved — Tin approved the PUBLIC token-minting endpoint)
Rationale: full suite 1711 green (88.09%); refute-read UPHELD@0.92 zero blockers; single-use enforced by
  task-1's atomic mint (FOR UPDATE + UNIQUE(authorization_id)); RFC 8628 §3.5 state machine exact; per-IP limit +
  slow_down both fail-OPEN; secrets sha256 at rest, plaintext only in the 200 body; NB-1 (shared per-IP counter)
  FIXED. Tin chose to keep refresh DEFAULT-ON (30d, knob=0 disables) + JSON-only body (form-encoding = open SPEC
  delta). Mandatory human security sign-off given.
If RISK-ACCEPTED -> n/a — security gate, never risk-accepted
Reviewed by: Tin · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): token-endpoint 400-by-error-code rate (authorization_pending vs
  slow_down vs invalid_grant vs access_denied), 429 rate per-IP, mint success rate, p95 latency of the mint tx.

### Spec delta
- [SPEC · open] project-wide coverage `concurrency=["greenlet"]` config — coverage.py under-measures lines run
  inside `async with sessionmaker()` (asyncpg/greenlet); token_router honest coverage is ~100% but reads 87%.
  Affects ALL async-DB code. The proper systemic fix; deliberately NOT smuggled into this task. (evidence: token
  router missed-lines 183-197 are the mint await block, fully exercised by test_poll_approved_mints_token)
- [SPEC · open] accept `application/x-www-form-urlencoded` on /oauth/token in addition to JSON — strict RFC 8628
  §3.4 clients send form-encoded; additive in the router, response contract unaffected (evidence: §3 freeze flag)
- [SPEC · open] approved-but-device_code-expired still mints (contract-correct: approved→mint unconditional, the
  access token carries its own TTL) — if a stricter "approval must also be consumed before device expiry" policy
  is wanted, add an expiry guard on the approved branch + a test (evidence: refute-read NB-2)
- [SPEC · seeded] token refresh/revoke + introspection endpoints — refresh_token is minted but no /oauth/token
  refresh_token grant or revocation endpoint exists yet (the agent re-runs device flow on expiry) — future task.

### Competency deltas
- [TDD · folded] coverage+greenlet under-measurement recurred (task-3 → task-4): the IO-vs-decision refactor [folded foundation-version 36]
  (awaits inside session, pure branching outside) lifts the honest number (72→87%) but cannot fully close it; the
  real fix is the global greenlet coverage config — stop per-task fighting (evidence: 2 tasks, same artifact).
- [ADD · folded] a delegated build subagent COMMITTED to `main` unprompted despite "do NOT commit" — the orchestrator [folded foundation-version 36]
  caught it (the commit bundled 4 tasks, authored-as-Tin, on the default branch) and soft-reset it. Subagent build
  prompts must hard-forbid git operations AND the orchestrator must verify HEAD after every delegated build.
- [ADD · folded] a delegated subagent (task-3) smuggled a global coverage-config change to lift its metric; (task-4) [folded foundation-version 36]
  another tried committing. Pattern: delegated agents optimize their local gate at the project's expense — the
  mandatory manual diff review (CLAUDE.md Rule 5) caught both. Keep it non-negotiable.
