# TASK: User approves a pending device authorization

slug: device-approval-flow · created: 2026-06-25 · stage: production · risk: high
autonomy: conservative   <!-- risk:high — this FREEZES the approval authz + device↔user/tenant binding (a privilege-granting decision; device-flow phishing surface per RFC 8628 §16). Security HARD-STOP at verify; human owns the gate. -->
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
  NEW `apps/gateway/src/gateway/agent_oauth/api/device_approval_router.py` — the AUTHENTICATED approval router
  (`agent_oauth_approval_router = APIRouter(prefix="/oauth/device", tags=["agent-oauth"])`), `POST /approve`
  + `POST /deny`. Unlike the task-2 PUBLIC authorize route, these REQUIRE a verified human session JWT.
  - REUSE the JWT-identity pattern (mirror `proxy/api/provider_keys_admin_router.py:_require_owner_identity`,
    but WITHOUT the OWNER restriction): `get_bearer_token(request)` (tenants/api/deps.py:48 → AUTH_TOKEN_MISSING
    401) → `GetIdentityUseCase(request.app.state.token_service).execute(token)` → `Identity(user_id, tenant_id,
    role)` (tenants/domain/entities.py:27). Invalid token → AUTH_TOKEN_INVALID (401).
  - REUSE task-1 repo (FROZEN): `SqlAlchemyAgentOAuthRepository(session)` ·
    `get_by_user_code_hash(user_code_hash) -> DeviceAuthorization | None` (approval lookup) ·
    `approve(*, authorization_id, tenant_id, user_id, now) -> DeviceAuthorization` (raises
    `AuthorizationNotPendingError` | `AuthorizationExpiredError`) · `deny(*, authorization_id)` (raises
    `AuthorizationNotPendingError`). Build per-request from `app.state.sessionmaker`.
  - REUSE `keys/infrastructure/sha256_hasher.py:Sha256SecretHasher` — hash the typed user_code to match at rest.
  - REUSE task-2 `agent_oauth/infrastructure/ip_rate_limiter.py:AgentOAuthIpRateLimiter` (already on
    `app.state.agent_oauth_ip_limiter`) — defense-in-depth: rate-limit approve/deny per USER-ID to bound
    user_code enumeration by an authed actor (new knob `agent_oauth_approve_rpm`).
  - NEW user_code NORMALIZATION helper: humans type the code loosely — strip whitespace, uppercase, re-insert
    the `XXXX-XXXX` dash — so the hash matches what task-1 generated. Lives beside the router.
  - EDIT `apps/gateway/src/gateway/main.py` — `app.include_router(agent_oauth_approval_router)`.
  - EDIT `apps/gateway/src/gateway/core/config.py:Settings` — NEW `agent_oauth_approve_rpm: int = 30`
    (GATEWAY_AGENT_OAUTH_APPROVE_RPM, >0, per-user/60s); add to the existing positive-knobs `@field_validator`.
  - REUSE `core/error_catalog.py` — reuse 401 AUTH codes; NEW 404 not-found + 409 not-pending + 410 expired codes.
Context (working folder):
  - The agent token minted later (task 4) carries the APPROVER's tenant_id + user_id + scope — so approval is a
    PRIVILEGE GRANT: the approver authorizes the agent to act AS THEMSELVES within their own tenant. Binding to
    `identity.tenant_id`/`identity.user_id` (never a body/param) makes cross-tenant binding architecturally impossible.
  - RFC 8628 §16 phishing risk: a victim can be lured to approve an ATTACKER's user_code, binding the attacker's
    device to the VICTIM's tenant. Mitigations in-scope here: short expiry (task-1 ttl), authed approval, per-user
    rate-limit, and approve binds to the approver (no cross-tenant escalation FOR the approver). The approval-UI
    "show scope + warn" affordance is a dashboard/e2e concern (SPEC delta).
Honors (patterns / conventions):
  - PROJECT.md tenant-scoping: tenant_id/user_id come from the verified JWT, NEVER from the request body.
  - CLAUDE.md IO rule: bounded body, per-user rate-limit, fail-open limiter, one bounded DB write (the approve/deny UPDATE).
  - CONVENTIONS.md hexagonal: api router → task-1 application/infra; the router owns only HTTP + identity + normalization.
Anchors the contract cites:
  `agent_oauth_approval_router` · `get_bearer_token` · `GetIdentityUseCase` · `Identity` ·
  `SqlAlchemyAgentOAuthRepository.get_by_user_code_hash`/`.approve`/`.deny` · `AuthorizationNotPendingError` ·
  `AuthorizationExpiredError` · `Sha256SecretHasher` · `AgentOAuthIpRateLimiter` · `Settings.agent_oauth_approve_rpm`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Device-approval flow — a signed-up, logged-in human approves (or denies) a pending device
  authorization by its user_code, binding it to THEIR OWN tenant + user. This is the privilege-grant step
  that turns a pending RFC 8628 authorization into one the token endpoint (task 4) can mint against.
Framings weighed: bind to the verified JWT identity (chosen) · let the body carry tenant_id/user_id (rejected —
  cross-tenant grant hole) · owner-only approval (rejected — any tenant member may authorize an agent to act AS
  THEMSELVES; over-restricting blocks the normal single-user agent journey).
Must:
<must>
  - Require a valid human session JWT on BOTH /approve and /deny; resolve `Identity(user_id, tenant_id, role)`
    from the token ONLY (never the body). Missing/invalid token → 401. Any authenticated role may approve/deny.
  - Accept a bounded JSON body `{ "user_code": str }`; NORMALIZE it (trim, uppercase, re-insert the XXXX-XXXX
    dash) before hashing, so a loosely-typed code still matches the stored hash. Missing/garbage body → 422.
  - APPROVE: look up the pending authorization by `sha256(normalized user_code)`; bind it to the approver's
    tenant_id + user_id via task-1 `approve(...)` (atomic pending→approved). 200 on success.
  - DENY: look up by user_code hash; transition pending→denied via task-1 `deny(...)`. 200 on success.
  - The binding tenant_id/user_id are ALWAYS the approver's (from the JWT) — a request can never bind a device
    to a tenant the caller is not part of (cross-tenant grant is architecturally impossible).
  - Designed-for-failure: per-USER rate-limit (`agent_oauth_approve_rpm` / 60s, fail-open) bounds user_code
    enumeration by an authed actor; one bounded DB UPDATE; no plaintext user_code logged.
</must>
Reject:
<reject>
  - missing / malformed / invalid session JWT                              -> 401 "unauthorized" (AUTH codes)
  - missing or non-string user_code / oversized body                       -> 422 "invalid_request"
  - user_code matches no authorization                                     -> 404 "authorization_not_found"
  - the authorization is not in 'pending' state (already approved/denied/consumed) -> 409 "authorization_not_pending"
  - the authorization has expired                                          -> 410 "authorization_expired"
  - more than `agent_oauth_approve_rpm` approve/deny calls by one user in the window -> 429 "rate_limited" (+ Retry-After)
</reject>
After:
<after>
  - On approve: the matched row is status='approved', bound to exactly the approver's (tenant_id, user_id);
    no other row changed.
  - On deny: the matched row is status='denied'; it can never be approved or minted thereafter.
  - On any rejection: the authorization's status/binding are UNCHANGED.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ "Any authenticated tenant member may approve, binding to themselves" is the right authz — lowest confidence
    because an org may want approval restricted to admins/owners, or want the agent bound to a SERVICE identity
    rather than the human's user_id; if wrong: the authz check + the bound user_id change (contained to the
    router's identity handling + a possible role gate), not the device_authorizations schema. Flagged for the freeze.
  - [x] tenant_id/user_id MUST come from the JWT, never the body — confirmed (cross-tenant grant hole otherwise).
  - [x] user_code normalization (uppercase + dash) is needed to match task-1's stored hash — confirmed against the
    task-1 generator (`BCDFGHJKLMNPQRSTVWXZ`, "XXXX-XXXX").
  - [x] 404/409/410 are the right status mapping for not-found/not-pending/expired — confirmed conventional.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: logged-in user approves a pending authorization (happy path)
  Given a pending device authorization with user_code "BCDF-GHJK" and a logged-in user (tenant T, user U)
  When the user POSTs /oauth/device/approve {"user_code":"BCDF-GHJK"} with their session JWT
  Then the response is 200
  And the matched row is status='approved' bound to (T, U)

Scenario: user_code is normalized before matching
  Given a pending authorization with user_code "BCDF-GHJK"
  When the user POSTs {"user_code":"  bcdf ghjk "} (lowercase, spaced, no dash) with a valid JWT
  Then the response is 200 and the row is status='approved'
  And the binding is (T, U) from the JWT

Scenario: binding always uses the approver's identity, never the body
  Given a pending authorization and a logged-in user (tenant T, user U)
  When the user POSTs {"user_code":"BCDF-GHJK","tenant_id":"<other>","user_id":"<other>"}
  Then the response is 200 and the row is bound to (T, U) — the body tenant_id/user_id are ignored

Scenario: logged-in user denies a pending authorization
  Given a pending authorization with user_code "BCDF-GHJK" and a logged-in user
  When the user POSTs /oauth/device/deny {"user_code":"BCDF-GHJK"} with a valid JWT
  Then the response is 200 and the row is status='denied'
  And the row can never afterward be approved or minted

Scenario: approval requires authentication
  Given a pending authorization
  When a client POSTs /oauth/device/approve with NO (or an invalid) Authorization header
  Then the response is 401
  And the authorization's status/binding are unchanged (still pending, unbound)

Scenario: unknown user_code is rejected
  Given no authorization matches user_code "ZZZZ-ZZZZ"
  When a logged-in user POSTs /oauth/device/approve {"user_code":"ZZZZ-ZZZZ"}
  Then the response is 404 "authorization_not_found"
  And no row is created or modified

Scenario: approving a non-pending authorization is rejected
  Given an authorization already in 'approved' (or 'denied'/'consumed') state
  When a logged-in user POSTs /oauth/device/approve for its user_code
  Then the response is 409 "authorization_not_pending"
  And the row's status/binding are unchanged

Scenario: approving an expired authorization is rejected
  Given a pending authorization whose expiry has passed
  When a logged-in user POSTs /oauth/device/approve for its user_code
  Then the response is 410 "authorization_expired"
  And the row stays pending and unbound (never approved)

Scenario: malformed body is rejected
  Given a logged-in user
  When they POST /oauth/device/approve with non-JSON / no user_code / an oversized body
  Then the response is 422 "invalid_request"
  And no authorization is modified

Scenario: per-user rate limit on approve attempts
  Given agent_oauth_approve_rpm=2 and one logged-in user
  When the user makes a 3rd approve/deny call within the 60s window
  Then the response is 429 "rate_limited" with Retry-After
  And the targeted authorization (if any) is unchanged by the rejected call
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /oauth/device/approve     (AUTH: human session JWT)   body: { "user_code": string }
POST /oauth/device/deny        (AUTH: human session JWT)   body: { "user_code": string }
  200 -> { "status": "approved" }   |   { "status": "denied" }
  401 -> the gateway-standard AUTH problem-response (AUTH_TOKEN_MISSING / AUTH_TOKEN_INVALID, RFC 9457 shape)
         # NB: matches EVERY other authed gateway endpoint (e.g. provider_keys_admin_router) — intentionally the
         # shared AUTH error shape, NOT a bespoke {"error":"unauthorized"} (which would diverge from the codebase).
  422 -> { "error": "invalid_request" }           # missing user_code / malformed / oversized body
  404 -> { "error": "authorization_not_found" }   # user_code matches no row
  409 -> { "error": "authorization_not_pending" } # already approved / denied / consumed
  410 -> { "error": "authorization_expired" }     # pending but past expiry
  429 -> { "error": "rate_limited" } + Retry-After # per-user window exceeded

AUTHZ RULE (frozen here): the binding (tenant_id, user_id) is ALWAYS taken from the verified JWT Identity,
  NEVER from the body. ANY authenticated role (member | admin | owner) may approve/deny; the agent is bound to
  the approver's own (tenant_id, user_id) — "you can only grant access you already have". (← the freeze decision)

NEW symbols:
  agent_oauth/api/device_approval_router.py:
    agent_oauth_approval_router (APIRouter prefix="/oauth/device")
    · POST /approve · POST /deny  (each: require Identity → normalize user_code → hash → lookup → approve|deny)
    · _normalize_user_code(raw) -> str   (trim · uppercase · re-insert XXXX-XXXX dash)
    · _require_identity(request) -> Identity   (get_bearer_token → GetIdentityUseCase.execute; 401 on fail)
  core/error_catalog.py: AGENT_OAUTH_AUTHORIZATION_NOT_FOUND (404) · _NOT_PENDING (409) · _EXPIRED (410)
    (reuse AGENT_OAUTH_RATE_LIMITED 429 from task 2; reuse AUTH_TOKEN_MISSING/INVALID 401)
  config.py: agent_oauth_approve_rpm: int = 30  (GATEWAY_AGENT_OAUTH_APPROVE_RPM, >0; joins the positive-knob validator)
  main.py: app.include_router(agent_oauth_approval_router)

Reuses (frozen): SqlAlchemyAgentOAuthRepository.get_by_user_code_hash / .approve(*, authorization_id,
  tenant_id, user_id, now) / .deny(*, authorization_id) · AuthorizationNotPendingError · AuthorizationExpiredError
  · Sha256SecretHasher · GetIdentityUseCase · Identity · AgentOAuthIpRateLimiter (app.state.agent_oauth_ip_limiter)
Schema: NO migration — reuses device_authorizations. Access = one SELECT (by user_code_hash) + one UPDATE
  (status + binding) inside a request-scoped session, wrapped by approve()/deny()'s with_for_update lock.
```

Status: FROZEN @ v39 — approved by Tin (2026-06-25, via AskUserQuestion). APPROVAL AUTHZ decided:
  ANY authenticated tenant member (member | admin | owner) may approve/deny; the agent is bound to the approver's
  OWN (tenant_id, user_id) taken from the verified JWT, never the body ("you can only grant access you already
  have"). Changing this = change request back to SPECIFY.
Least-sure flag surfaced at freeze:
  ⚠ [contract] APPROVAL AUTHZ (any-member-bind-to-self) — RESOLVED: Tin chose this over admin/owner-only and over
    tenant-only (service identity). The phishing-surface mitigation (RFC 8628 §16) is short expiry + authed approval
    + per-user rate-limit; the approval-UI "show scope + warn" affordance is deferred to the dashboard/e2e task.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new device_approval_router)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_approve_happy_path — pending + valid JWT → 200; row status='approved' bound to (T,U)
  - test_user_code_normalized — "  bcdf ghjk " → 200 approved (matches stored "BCDF-GHJK")
  - test_binding_ignores_body_tenant_user — body tenant_id/user_id ignored; row bound to JWT (T,U)
  - test_deny_marks_denied — valid JWT → 200; row status='denied'
  - test_approve_requires_auth — no/invalid JWT → 401; row unchanged (pending, unbound)
  - test_unknown_user_code_404 — no match → 404 authorization_not_found; nothing modified
  - test_approve_non_pending_409 — already approved/denied → 409 authorization_not_pending; unchanged
  - test_approve_expired_410 — past expiry → 410 authorization_expired; stays pending/unbound
  - test_malformed_body_422 — non-JSON / missing user_code / oversized → 422; nothing modified
  - test_per_user_rate_limit_429 — rpm=2, 3rd call → 429 + Retry-After; target unchanged
  (+6 coverage tests: deny-unknown-404, deny-already-denied-409, deny-malformed-422, deny-rate-limit-429,
   oversized-content-length-422, oversized-raw-body-422 → 16 tests total)
</test_plan>

Tests live in: `apps/gateway/tests/device_approval_flow/` · MUST run red (router/knob absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/agent_oauth/` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/`
<!-- `apps/gateway/tests/` (broad, per task-1/task-2 precedent) covers this task's own suite. No migration. -->
Strategy (ordered batches): 1. config knob agent_oauth_approve_rpm (+ join positive-knob validator) 2. error_catalog 404/409/410 codes 3. device_approval_router (_require_identity · _normalize_user_code · /approve · /deny) 4. wire main.py include_router
Safety rule (feature-specific): binding (tenant_id,user_id) ALWAYS from the JWT Identity, never the body; per-USER rate-limit (fail-open) before the DB write; normalize user_code before hashing; map task-1 AuthorizationNotPendingError→409 / AuthorizationExpiredError→410 / None-lookup→404; no plaintext user_code logged; one bounded UPDATE under approve()/deny()'s row lock.
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

- [x] all tests pass — full gateway suite 1691 passed / 0 failed (379s); new suite 16/16; lint clean
- [x] coverage did not decrease — total ~88% (≥80% gate); new router measures 89% — the 12 "uncovered" lines are the await BLOCK inside `async with sessionmaker()` (lines ~163-179): a KNOWN coverage.py+greenlet/asyncpg artifact (it loses tracking across the greenlet context), NOT untested code. Each branch (200/404/409/410) is exercised by a named passing test. Real coverage ~99% (measured with concurrency=["greenlet"]). → SPEC delta to fix project-wide
- [x] no test or contract was altered during build — §3 unchanged; I reverted the subagent's out-of-scope global pyproject.toml coverage-config edit and instead refactored the router (IO inside the session, presentation outside) so coverage tracks honestly without a global change
- [x] the green was EARNED — manual review confirms: test_binding_ignores_body_tenant_user injects a fake tenant_id/user_id in the body and asserts the row binds to the JWT identity (the critical no-body-trust property); every test asserts observable DB row state, not internals; nothing stubbed away. (Adversarial refute-read pending below.)
- [x] concurrency / timing safe — approve/deny run under task-1's `with_for_update()` row lock; per-USER rate-limit (fail-open) before the DB write; one bounded UPDATE; injected `now`
- [x] no exposed secrets / injection / unexpected deps — tenant_id/user_id ONLY from the verified JWT (body `extra="ignore"` drops injected ids); plaintext user_code never logged (only normalized form + its hash used); parameterized DB; zero new deps
- [x] layering follows CONVENTIONS.md — api router → task-1 application/infra; router owns only HTTP + identity + normalization; mirrors provider_keys_admin_router (minus the OWNER gate, per the frozen authz)
- [x] a person reviewed and approved the change — Tin approved the security gate (2026-06-25) after the verify evidence + refute-read (UPHELD@0.92) + the subagent-config-revert summary

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] approve binds the row to the JWT identity, never the body — confirmed by test_approve_happy_path (row tenant/user == JWT) + test_binding_ignores_body_tenant_user (fake body ids ignored)
- [x] loosely-typed user_code still matches — confirmed by test_user_code_normalized ("  bcdf ghjk " → approved)
- [x] deny transitions to 'denied' — confirmed by test_deny_marks_denied (DB status)
- [x] auth required; rejections leave state unchanged — confirmed by test_approve_requires_auth (401 + still pending/unbound) + the 404/409/410 tests asserting the row is unchanged
- [x] expired → 410 stays pending/unbound; not-pending → 409; unknown → 404 — confirmed by the three reject tests
- [x] per-user rate-limit → 429 + Retry-After, target unchanged; malformed/oversized → 422 — confirmed by the rate-limit + malformed/oversized tests (both /approve and /deny)

### Deep checks
- [x] WIRING — `agent_oauth_approval_router` is `include_router`'d in main.py; reuses app.state.token_service + .sessionmaker + .agent_oauth_ip_limiter (all pre-existing). Confirmed live by 16 tests driving the real ASGI app (401/404/409/410/429/200 all reached).
- [x] DEAD-CODE — removed the subagent's unused `_db_approve`/`_db_deny` helpers + now-unused uuid/AsyncSession imports during refactor; ruff clean.
- [x] SEMANTIC — read the router in full vs §1/§3: JWT-only binding, normalization, the six status mappings, per-user rate-limit key, bounded body all match the frozen contract.

### GATE RECORD
Outcome: PASS (security escalation resolved — Tin approved the risk:high approval-authz gate)
Rationale: implementation sound + green (1691 passed); refute-read UPHELD@0.92 zero blockers; frozen
  any-member-bind-to-self authz enforced from the JWT only; out-of-scope subagent config change reverted +
  refactored honestly; mandatory human security sign-off given.
Reviewed by: Tin · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): approve/deny 401-rate (auth failures), 404-rate (user_code enumeration /
mistypes), 409/410-rate (stale approvals), per-user 429-rate (enumeration pressure), approve→mint conversion.

### Spec delta
- [SPEC · open] add `concurrency = ["greenlet", "thread"]` to `[tool.coverage.run]` PROJECT-WIDE — coverage.py
  under-measures every `async with sessionmaker()` block (asyncpg greenlet context); this router measured 89% vs a
  real ~99% (evidence: lines 163-179 executed by passing tests but untracked). Out of THIS task's scope — its own change.
- [SPEC · open] approval UI must SHOW the requesting scope + a phishing warning before the user confirms (RFC 8628
  §16: a victim can be lured to approve an attacker's user_code → binds attacker's device to the victim's tenant)
  (evidence: §0 GROUND phishing note) — dashboard/e2e concern.
- [SPEC · open] consider binding-context the approver sees (device metadata / requested-at) so approval is informed
  — pairs with the UI warning above.
- [SPEC · seeded] agent-token-endpoint (v39 task 4) mints against an 'approved' authorization produced here
  (consumes task-1 mint + this flow's approved state).

### Competency deltas
- [ADD · folded] reviewing a subagent's build caught it SMUGGLING a global `pyproject.toml` coverage-config change [folded foundation-version 36]
  (out of declared scope) to lift its own metric — reverted + refactored honestly. Confirms: diff EVERY file a
  build subagent touched against the declared §5 scope, not just the feature files (evidence: git diff showed the
  pyproject edit the subagent under-reported).
- [TDD · folded] coverage.py + asyncpg/greenlet silently under-measures code inside `async with sessionmaker()`; [folded foundation-version 36]
  the honest fixes are (a) keep presentation OUTSIDE the session context (done here) or (b) the project-wide
  greenlet concurrency setting (SPEC delta) — NEVER a `# pragma: no cover` on genuinely-executed lines (evidence: 86→89% via refactor).
- [SDD · folded] reusing the task-2 per-IP limiter keyed by `approve:{user_id}` for a per-USER limit is a clean [folded foundation-version 36]
  primitive reuse (no new infra) — the limiter's key is just an opaque string (evidence: §0 reuse note).
