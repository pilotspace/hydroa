# TASK: Public RFC 8628 device-authorization endpoint

slug: device-authorization-endpoint · created: 2026-06-25 · stage: production · risk: high
autonomy: conservative   <!-- risk:high — public PRE-AUTH endpoint (anti-abuse + DoS surface); security HARD-STOP at verify per the v39 milestone rule. Human owns the gate. -->
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
  NEW `apps/gateway/src/gateway/agent_oauth/api/device_authorize_router.py` — the public router
  (`agent_oauth_device_router = APIRouter(prefix="/oauth/device", tags=["agent-oauth"])`), `POST /authorize`.
  Mirrors `proxy/api/provider_keys_admin_router.py` structure (APIRouter + Pydantic body + Request) but is
  PUBLIC: NO `get_bearer_token` / identity dependency — anyone may start a device flow (RFC 8628 §3.1).
  - REUSE `agent_oauth/application/use_cases.py:AgentOAuthService.start_device_authorization(*, scope,
    interval_seconds, ttl_seconds, now) -> MintedDeviceCodes` (task 1, FROZEN) — returns plaintext
    device_code + user_code once + the persisted `DeviceAuthorization`.
  - REUSE `agent_oauth/infrastructure/repository.py:SqlAlchemyAgentOAuthRepository(session)` (task 1) — built
    per-request from a session; `DeviceCodeCollisionError` is its CSPRNG-collision signal (→ 503 retry, not 500).
  - REUSE `keys/infrastructure/sha256_hasher.py:Sha256SecretHasher` — the service's hasher (no plaintext at rest).
  - NEW anti-abuse limiter: the existing `rate_limits/infrastructure/redis_lua_limiter.py:RedisLuaRateLimiter`
    is keyed by an api-key `UUID` (RPM/TPM) — it does NOT fit an UNauthenticated caller. This endpoint needs a
    NEW per-client (IP) fixed-window limiter (Redis INCR+EXPIRE, fail-OPEN on Redis outage like the existing
    limiter), keyed by the Envoy-forwarded client IP. Small new helper in `agent_oauth/infrastructure/`.
  - EDIT `apps/gateway/src/gateway/main.py` — `app.include_router(agent_oauth_device_router)` + (if a
    service singleton is wired) `app.state.agent_oauth_service`/hasher; per-request session via
    `app.state.sessionmaker` (the established store pattern, e.g. `record_audit(app.state.sessionmaker, …)`).
  - EDIT `apps/gateway/src/gateway/core/config.py:Settings` — NEW `GATEWAY_AGENT_OAUTH_*` knobs (env_prefix
    `GATEWAY_`): `agent_oauth_verification_uri` (str, the dashboard approve URL returned to the agent),
    `agent_oauth_device_code_ttl_seconds` (int, default 600), `agent_oauth_poll_interval_seconds` (int,
    default 5), `agent_oauth_default_scope` (str, default "proxy"), `agent_oauth_authorize_rpm` (int,
    per-IP limit, default 12). Validation mirrors the reconciliation knobs' `@field_validator` (>0 guards).
Context (working folder):
  - Edge/Envoy auth model (memory e2e-edge-stack-ops): `/v1` = ext_authz, `/admin` = JWT, `/internal` = blocked.
    A PUBLIC `/oauth/device/authorize` must be reachable WITHOUT auth through Envoy — the Envoy allow-rule is an
    e2e-task (agent-oauth-harness-e2e) concern; the gateway app itself simply attaches no auth dependency here.
  - Client IP behind Envoy arrives via `X-Forwarded-For`/`X-Envoy-External-Address`; the limiter reads the
    left-most forwarded IP (and falls back to `request.client.host` in direct/test calls).
  - `core/error_catalog.py` — structured `ErrCode.exc()` pattern (e.g. `AUTH_TOKEN_INVALID`); NEW codes for this
    endpoint (rate-limited → 429, transient collision → 503) live there.
Honors (patterns / conventions):
  - CLAUDE.md IO rule (design for failure): public pre-auth surface → bounded body, per-IP rate-limit (429 +
    Retry-After), Redis fail-OPEN, no unbounded work; the only IO is one short DB write (collision → 503 + retry).
  - PROJECT.md: secrets hashed at rest (task 1 already enforces); the endpoint returns plaintext codes ONCE in
    the response body and never logs them.
  - CONVENTIONS.md hexagonal: router (api) → service (application) → repo (infrastructure); router builds the
    per-request repo+service from a session, mirrors provider_keys_admin_router.
  - RFC 8628 §3.2 response shape: `device_code · user_code · verification_uri · verification_uri_complete
    (optional) · expires_in · interval`.
Anchors the contract cites:
  `agent_oauth_device_router` · `AgentOAuthService.start_device_authorization` · `MintedDeviceCodes` ·
  `SqlAlchemyAgentOAuthRepository` · `DeviceCodeCollisionError` · `Sha256SecretHasher` · the per-IP limiter ·
  `Settings.agent_oauth_*` knobs · `app.state.sessionmaker` · RFC 8628 §3.2 fields · error_catalog codes.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Public RFC 8628 device-authorization endpoint — `POST /oauth/device/authorize`. An UNauthenticated
  coding agent starts a device flow; the gateway mints a device_code + user_code (task-1 store), persists the
  pending authorization, and returns the RFC 8628 §3.2 payload so the agent can poll + a human can approve.
Framings weighed: thin HTTP adapter over the frozen task-1 service (chosen) · re-implement generation in the
  router (rejected — duplicates the hashing/CSPRNG seam) · authenticated endpoint (rejected — RFC 8628 §3.1 is
  pre-auth by definition; the WHOLE point is the agent has no credential yet).
Must:
<must>
  - Accept an UNauthenticated POST with an OPTIONAL `scope` (default `Settings.agent_oauth_default_scope`); no
    bearer/identity required. Body is bounded (reject oversized/garbage with 422; unknown fields ignored).
  - Generate via `AgentOAuthService.start_device_authorization` using server-side knobs only: ttl =
    `agent_oauth_device_code_ttl_seconds`, interval = `agent_oauth_poll_interval_seconds`, `now` = server UTC.
    The client may NOT influence ttl/interval (anti-abuse).
  - Return RFC 8628 §3.2 200 JSON: `device_code` · `user_code` · `verification_uri` · `expires_in` (seconds,
    derived from ttl) · `interval`; include `verification_uri_complete` ONLY when the configured
    verification_uri is non-empty (uri + user_code). Plaintext codes appear ONCE here and are NEVER logged.
  - Rate-limit per client IP (`agent_oauth_authorize_rpm` / 60s fixed window); over-limit → 429 + Retry-After.
    The limiter is FAIL-OPEN: a Redis outage must not break the endpoint (log + allow), per the existing limiter.
  - Be designed-for-failure: the only IO is one bounded DB insert; a transient `DeviceCodeCollisionError`
    (astronomically rare CSPRNG clash) maps to 503 + Retry-After (retryable), NOT a 500.
  - Validate the `GATEWAY_AGENT_OAUTH_*` knobs at startup: ttl, interval, rpm must be > 0 (mirror the
    reconciliation knob validators) — a misconfig fails fast at boot, not per-request.
</must>
Reject:
<reject>
  - body exceeds the bounded size / malformed JSON / wrong content-type        -> 422 "invalid_request"
  - more than `agent_oauth_authorize_rpm` requests from one IP within the window-> 429 "rate_limited" (+ Retry-After)
  - device_code/user_code hash collides with a live pending row (transient)    -> 503 "temporarily_unavailable" (+ Retry-After)
</reject>
After:
<after>
  - Exactly one new `device_authorizations` row exists, status='pending', tenant/user unbound, all codes hashed,
    bounded expiry + the configured poll interval.
  - The 200 body carries plaintext device_code + user_code returned exactly once; nothing plaintext is persisted or logged.
  - The per-IP counter for the caller is incremented (and expires with the window).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Per-IP fixed-window over X-Forwarded-For is the right anti-abuse key — lowest confidence because behind
    Envoy/NAT many agents can share an egress IP (false positives) and XFF is spoofable if the edge doesn't
    strip/append it correctly; if wrong: the limiter key changes (e.g. add a global ceiling or a per-/24 bucket)
    — contained to the limiter helper + its config, not the response contract. Edge XFF-trust is an e2e concern.
  - [x] RFC 8628 §3.2 response field names are exactly `device_code/user_code/verification_uri/
    verification_uri_complete/expires_in/interval` — confirmed against the RFC; snake_case JSON.
  - [x] The endpoint is anonymous (no auth dependency) and the Envoy public-allow rule is deferred to the e2e
    task — confirmed against the milestone scope + edge auth memory.
  - [x] Knob validation belongs at boot (Settings validator), not per-request — confirmed, mirrors reconciliation.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: anonymous agent starts a device flow (happy path)
  Given a running gateway with agent_oauth knobs set (ttl=600, interval=5, verification_uri set)
  When an UNauthenticated client POSTs /oauth/device/authorize with no body
  Then the response is 200 with device_code, user_code, verification_uri, verification_uri_complete,
       expires_in=600, interval=5
  And exactly one device_authorizations row exists with status='pending' and tenant_id/user_id NULL

Scenario: codes are hashed at rest, plaintext only in the response
  Given a fresh store
  When the client calls /oauth/device/authorize
  Then the device_code/user_code in the JSON body match sha256 == the stored *_hash columns
  And neither plaintext value appears in any persisted column or log line

Scenario: client cannot influence ttl/interval
  Given knobs ttl=600 interval=5
  When the client POSTs a body attempting expires_in=99999 / interval=0
  Then the response expires_in is 600 and interval is 5 (server knobs win; client fields ignored)
  And the stored row's expiry/interval reflect the server knobs

Scenario: optional scope is honored, default applied otherwise
  Given default_scope='proxy'
  When the client POSTs {"scope":"proxy"} and, separately, an empty body
  Then both create a pending row with scope='proxy'
  And the explicit-scope path stores exactly the requested scope

Scenario: verification_uri_complete omitted when unconfigured
  Given agent_oauth_verification_uri is empty
  When the client calls /oauth/device/authorize
  Then the 200 body still has verification_uri (empty) but OMITS verification_uri_complete
  And a pending row is still created

Scenario: per-IP rate limit returns 429
  Given agent_oauth_authorize_rpm=2 and a single client IP
  When the client makes a 3rd request within the 60s window
  Then the response is 429 "rate_limited" with a Retry-After header
  And NO new device_authorizations row is created for the rejected request

Scenario: limiter fails OPEN when Redis is down
  Given the rate limiter's Redis is unreachable
  When the client calls /oauth/device/authorize
  Then the request still succeeds 200 (fail-open) and a pending row is created
  And the outage is logged, not surfaced to the client

Scenario: transient device_code collision returns retryable 503
  Given the store raises DeviceCodeCollisionError for this insert
  When the client calls /oauth/device/authorize
  Then the response is 503 "temporarily_unavailable" with Retry-After
  And no partial/duplicate row is left behind (the failed insert rolled back)

Scenario: malformed body is rejected
  Given a running gateway
  When the client POSTs non-JSON / an oversized body / wrong content-type
  Then the response is 422 "invalid_request"
  And no device_authorizations row is created

Scenario: misconfigured knob fails fast at boot
  Given GATEWAY_AGENT_OAUTH_DEVICE_CODE_TTL_SECONDS=0 (or interval/rpm = 0)
  When the Settings are constructed at startup
  Then a validation error is raised at boot
  And the process does not start serving with an invalid knob
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /oauth/device/authorize        (PUBLIC — no auth)   body (optional): { "scope"?: string }
  200 -> {
    "device_code": string,                 # opaque, plaintext ONCE (sha256 stored)
    "user_code": string,                   # "XXXX-XXXX", plaintext ONCE
    "verification_uri": string,            # Settings.agent_oauth_verification_uri (may be "")
    "verification_uri_complete"?: string,  # present ONLY when verification_uri != "" → f"{uri}?user_code={user_code}"
    "expires_in": int,                     # = agent_oauth_device_code_ttl_seconds
    "interval": int                        # = agent_oauth_poll_interval_seconds
  }
  422 -> { "error": "invalid_request" }            # malformed / oversized body
  429 -> { "error": "rate_limited" }    + header Retry-After: <seconds>   # per-IP window exceeded
  503 -> { "error": "temporarily_unavailable" } + header Retry-After: <seconds>   # transient device_code collision

Server-owned (NOT client-settable): expires_in, interval, ttl, now → from Settings knobs only.

NEW config (Settings, env_prefix GATEWAY_):
  agent_oauth_verification_uri: str = ""           # GATEWAY_AGENT_OAUTH_VERIFICATION_URI
  agent_oauth_device_code_ttl_seconds: int = 600   # GATEWAY_AGENT_OAUTH_DEVICE_CODE_TTL_SECONDS  (>0)
  agent_oauth_poll_interval_seconds: int = 5       # GATEWAY_AGENT_OAUTH_POLL_INTERVAL_SECONDS    (>0)
  agent_oauth_default_scope: str = "proxy"         # GATEWAY_AGENT_OAUTH_DEFAULT_SCOPE
  agent_oauth_authorize_rpm: int = 12              # GATEWAY_AGENT_OAUTH_AUTHORIZE_RPM            (>0, per-IP/60s)

NEW symbols:
  agent_oauth/api/device_authorize_router.py: agent_oauth_device_router (APIRouter prefix="/oauth/device")
    · POST /authorize handler (builds per-request SqlAlchemyAgentOAuthRepository(session) + AgentOAuthService)
  agent_oauth/infrastructure/ip_rate_limiter.py: AgentOAuthIpRateLimiter(redis)
    · async check(ip: str, limit: int) -> None  (raises RateLimitedError(retry_after) | fail-open on redis error)
  core/error_catalog.py: AGENT_OAUTH_RATE_LIMITED (429) · AGENT_OAUTH_TEMPORARILY_UNAVAILABLE (503) · reuse a 422
  main.py: app.include_router(agent_oauth_device_router) ; app.state.agent_oauth_ip_limiter (built from redis_client)

Reuses (frozen elsewhere): AgentOAuthService.start_device_authorization · MintedDeviceCodes ·
  SqlAlchemyAgentOAuthRepository · DeviceCodeCollisionError · Sha256SecretHasher · app.state.sessionmaker · app.state.redis_client
Access pattern: one INSERT (create_pending) per request inside a request-scoped session; one Redis INCR+EXPIRE
  for the per-IP window. No reads. No outbound provider IO.
```

Status: FROZEN @ v39 — lead-frozen under autonomy:auto (thin adapter over the FROZEN task-1 store; the one
  novel decision is the anti-abuse limiter keying, which is contained to ip_rate_limiter.py + its knob and
  reversible without touching the 200 contract). The security review of this public pre-auth endpoint is
  carried to the VERIFY gate as the milestone-mandated HARD-STOP for Tin's sign-off. Change = back to SPECIFY.
Least-sure flag surfaced at freeze:
  ⚠ [spec] Per-IP fixed-window rate-limit keyed on the Envoy-forwarded client IP is the anti-abuse design —
    most likely wrong because shared egress IPs (NAT/corp proxies) cause false 429s and XFF is only trustworthy
    if the edge strips client-supplied XFF; if wrong the limiter KEY/strategy changes (global ceiling or per-/24)
    — contained to `ip_rate_limiter.py` + its knob, the 200 response contract is unaffected. Edge XFF-trust is
    an e2e-task concern (agent-oauth-harness-e2e).
  ⚠ [contract] `verification_uri_complete` is conditionally present (only when configured) rather than always —
    chosen to avoid emitting a meaningless `?user_code=` with an empty base; low cost to flip if a client library
    expects it always present.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new agent_oauth/api + ip_rate_limiter)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_happy_path_200_with_rfc8628_fields — 200 + all §3.2 fields + 1 pending row (tenant/user NULL)
  - test_codes_hashed_at_rest_plaintext_in_response — stored *_hash == sha256(body code); plaintext not persisted
  - test_client_cannot_influence_ttl_or_interval — body expires_in/interval ignored; server knobs 600/5 win
  - test_optional_scope_honored_default_applied — explicit scope stored; empty body → default 'proxy'
  - test_verification_uri_complete_omitted_when_uri_empty — empty uri → field absent; row still created
  - test_per_ip_rate_limit_429_on_third_request — rpm=2, 3rd → 429 + Retry-After + only 2 rows
  - test_limiter_fails_open_when_redis_down — broken-redis limiter → still 200 + row (fail-open)
  - test_transient_collision_returns_503_with_retry_after — patched collision → 503 + Retry-After + 0 new rows
  - test_malformed_body_returns_422 — non-JSON → 422 invalid_request + no row
  - test_oversized_body_returns_422 — >4KB body → 422 + no row (STRENGTHENED post-review: bounded-body DoS guard)
  - test_misconfigured_knob_fails_fast_at_construction — ttl/interval/rpm=0 → ValidationError at boot
</test_plan>

Tests live in: `apps/gateway/tests/device_authorization_endpoint/` · ran RED (ImportError: router/limiter/knobs absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/agent_oauth/` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/`
<!-- `apps/gateway/tests/` (broad, per the task-1 / audit-log-store precedent) covers this task's own suite. -->
Strategy (ordered batches): 1. ip_rate_limiter (infra) 2. config knobs + boot validator 3. error_catalog codes 4. device_authorize_router (api) 5. wire main.py (limiter on app.state + include_router)
Safety rule (feature-specific): rate-limit BEFORE the bounded body read; limiter FAIL-OPEN on Redis error; bounded body (≤4KB → 422); collision → 503 + Retry-After (rollback, no 500); ttl/interval/now server-owned (never client-settable); plaintext codes only in the 200 body, never logged.
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

- [x] all tests pass — full gateway suite 1675 passed / 0 failed (uv run pytest, 313s); new suite 11/11
- [x] coverage did not decrease — total 88.07% (≥80% gate); new files: ip_rate_limiter 100%, device_authorize_router 94% (uncovered = defensive content-length-ValueError + lying-content-length post-read guard)
- [x] no test or contract was altered during build — §3 contract unchanged; the only post-build edits are ADDITIVE (the bounded-body DoS fix + test_oversized_body_returns_422 + a one-line unused-fixture cleanup) — no existing assertion weakened
- [x] the green was EARNED — adversarial refute-read (subagent, sonnet) returned UPHELD@0.91, ZERO blockers; confirmed fail-open/rate-limit/no-client-params/no-secret-leak/bounded-body/collision-503 all sound. Non-blocking residue → SPEC deltas (§7). I ALSO caught + fixed a real unbounded-body DoS gap during my own review (rate-limit reordered BEFORE the body read; 4KB cap; oversized test added) — the subagent verified the fix is correct + complete
- [x] concurrency / timing safe — limiter is per-IP fixed-window (INCR+EXPIRE); the only non-atomic edge (EXPIRE-fails-after-INCR) is fail-open + minute-bucketed (no lockout, at most a 1-key/min TTL-less leak) → SPEC delta for a Lua-atomic limiter. One bounded DB INSERT per request inside a request-scoped session.
- [x] no exposed secrets / injection / unexpected deps — plaintext device/user codes ONLY in the 200 body, never logged (collision warning carries no codes); body is parameterized JSON; zero new third-party deps (stdlib time/secrets + existing redis/fastapi/pydantic)
- [x] layering follows CONVENTIONS.md — hexagonal: api router → application service (task-1) → infrastructure repo+limiter; mirrors provider_keys_admin_router; public endpoint attaches no auth dependency by design (RFC 8628 §3.1)
- [x] a person reviewed and approved the change — Tin approved the security gate (2026-06-25) after the verify evidence + refute-read + the DoS-fix summary

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] anonymous POST → 200 with the exact RFC 8628 §3.2 fields + exactly one pending row (tenant/user NULL) — confirmed by test_happy_path + a DB row assertion
- [x] device/user codes stored ONLY as sha256 hashes; plaintext only in the body — confirmed by test_codes_hashed_at_rest (raw SELECT == hasher.hash(code))
- [x] ttl/interval are server-owned; client body cannot override — confirmed by test_client_cannot_influence (posts expires_in=99999/interval=0 → still 600/5, row interval=5)
- [x] over-limit → 429 + Retry-After and NO new row; Redis-down → fail-open 200 + row — confirmed by test_per_ip_rate_limit_429 (real Redis) + test_limiter_fails_open (real limiter, mocked-raising redis)
- [x] oversized/malformed body → 422 + no row, rejected before the DB write — confirmed by test_oversized_body_returns_422 (8KB → 422, row count unchanged) + test_malformed_body
- [x] transient collision → 503 + Retry-After, no partial row — confirmed by test_transient_collision; misconfig knob → boot ValidationError — confirmed by test_misconfigured_knob

### Deep checks
- [x] WIRING — `agent_oauth_device_router` is `include_router`'d in main.py; `app.state.agent_oauth_ip_limiter = AgentOAuthIpRateLimiter(redis_client)` wired beside the other app.state services; the router reads `request.app.state.settings`. All confirmed live by the 11 tests driving the real ASGI app end-to-end (404/ImportError would fail collection).
- [x] DEAD-CODE — no orphans; the limiter + router are both reached by the suite; the unused-fixture smell the refute-read flagged was removed.
- [x] SEMANTIC — read the router + limiter in full vs §1/§3: response field names + conditional verification_uri_complete + the three error shapes + Retry-After headers + the server-owned-knobs rule all match the frozen contract; config validator mirrors the reconciliation-knob style.

### GATE RECORD
Outcome: PASS (security escalation resolved — Tin approved the risk:high PUBLIC pre-auth endpoint gate)
Rationale: implementation sound + fully green (1675 passed); refute-read UPHELD@0.91 zero blockers; the one real
  defect (unbounded-body DoS) was caught + fixed + re-verified during review; mandatory human security sign-off given.
Reviewed by: Tin · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): /oauth/device/authorize 429-rate (anti-abuse pressure / false-positive NAT
sharing), 503 collision-rate (CSPRNG health — should be ~0), 422-rate (malformed/oversized probes), p99 latency.

### Spec delta
- [SPEC · open] make the per-IP limiter Lua-atomic (single round-trip INCR+EXPIRE) — eliminates the EXPIRE-after-INCR race that can leave a TTL-less minute-bucket key (small leak; no lockout) (evidence: refute-read non-blocking #1; mirror rate_limits/redis_lua_limiter).
- [SPEC · open] server-agnostic request-body cap (ASGI ContentSizeLimit middleware) — current 4KB guard buffers a chunked body with no Content-Length before rejecting; mitigated by Envoy in prod but not in-process (evidence: refute-read non-blocking #2).
- [SPEC · open] Envoy public-allow rule for /oauth/device/* (unauthenticated) + XFF-trust (strip client-supplied XFF so the left-most IP is trustworthy) — owned by agent-oauth-harness-e2e (evidence: §1 ⚠ flag + edge auth model).
- [SPEC · seeded] agent-token-endpoint (v39 task 4) reuses this limiter pattern for the public POST /oauth/token poll path + the RFC 8628 polling knobs (slow_down builds on `interval`).
- [SPEC · seeded] device-approval-flow (v39 task 3) consumes get_by_user_code_hash + approve to bind the pending user_code created here.

### Competency deltas
- [TDD · folded] a thin HTTP adapter still needs its OWN designed-for-failure tests, not just the happy path — my review caught an unbounded-body DoS the generated suite missed (its "oversized" assertion was docstring-only). For a public endpoint, pre-seed bounded-body + rate-limit-ordering tests in the RED suite (evidence: test_oversized_body_returns_422 added at verify). [folded foundation-version 36]
- [SDD · folded] when reusing a primitive that doesn't fit (the per-UUID RedisLuaRateLimiter vs an unauthenticated caller), spec a NEW fit-for-purpose seam rather than forcing the old one — the per-IP limiter is the right call but should be Lua-atomic like its sibling (evidence: §0 GROUND limiter note + SPEC delta above). [folded foundation-version 36]
- [ADD · folded] reviewing a subagent's "all green" build is non-optional: the suite was green AND the refute-read passes only AFTER I closed the DoS gap the subagent's own docstring overclaimed — manual review of generated code is where the real defect surfaced (evidence: CLAUDE.md Rule 5; the fix landed between subagent-green and gate). [folded foundation-version 36]
