# TASK: BFF verifies session JWT via gateway relay + true 0-leak harness

slug: auth-me-session-verify · created: 2026-06-15 · stage: production · risk: high
autonomy: conservative   <!-- SECURITY task (session-JWT trust). The §3 contract freeze already auto-resolved per Tin's standing mandate (freezes delegated to auto). The VERIFY gate is the security HARD-STOP: lowered to conservative so the completing gate is HUMAN-recorded (Tin), never an auto-PASS. A security finding is ALWAYS HARD-STOP. -->
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
- `apps/dashboard/app/api/auth/me/route.ts` — the BFF handler to harden. TODAY: reads the
  `ai_proxy_session` cookie, `decodeJwtPayload()` BASE64-decodes the payload with NO signature
  verification (its own docstring says "no signature verification"), returns
  `{user_id, tenant_id, email, role, exp}`. THIS is the gap Tin escalated.
- `apps/dashboard/lib/hooks/use-current-user.ts` — `useCurrentUser()` (queryKey `["current-user"]`,
  `retry:false`, `staleTime 5min`) → `fetchCurrentUser()` GET `/api/auth/me`; `interface CurrentUser
  { user_id, tenant_id, email, role, exp }`. NOTE: `exp` is declared in the type but NO consumer
  reads it (sole `.exp` reference is the route itself) — so a relayed `exp: null` is consumer-safe.
- `apps/gateway/src/gateway/tenants/api/router.py:67` — `GET /admin/auth/me` ALREADY EXISTS:
  `Depends(get_bearer_token)` → `GetIdentityUseCase.execute(token)` → returns
  `MeResponse{user_id, tenant_id, email, role}` (NO exp); raises `AUTH_TOKEN_INVALID` (401
  `ERR_AUTH_TOKEN_INVALID`) on `InvalidTokenError`. This is the authoritative verifier to relay to.
- `apps/gateway/src/gateway/tenants/infrastructure/jwt_service.py:33` — the session token is
  HS256, signed with `settings.jwt_secret`, issuer-bound (`iss`), claims
  `{sub→user_id, tenant_id, role, email, iat, exp, iss}`; `decode()` enforces sig + issuer +
  required-claims + exp. The gateway holds the secret — the BFF must NOT (no secret sprawl).

Context (working folder): the dashboard BFF env already resolves `GATEWAY_URL` (login/route.ts:11);
no new secret is introduced by the relay design. The carried v17 test-harness 0-leak (UsagePage +
dashboard-shell render `useCurrentUser` with no per-test `/api/auth/me` stub) couples here: once the
route is a verifying relay, every such test must stub it → reaching a true 0 unhandled-request count.

Honors (patterns / conventions):
- BFF RELAY precedent: `app/api/auth/login/route.ts` (POST → gateway, sets HttpOnly cookie) and
  `app/auth/oidc/callback/route.ts` (manual-redirect relay, `AbortSignal.timeout`, sanitized error).
  The relay design reuses this exact shape — forward the cookie token as `Authorization: Bearer …`.
- CLAUDE.md IO rule "design for failure": the gateway hop gets a timeout + fail-CLOSED behavior
  (network/5xx/timeout → never a trusted identity; surface 401/503, never silently pass claims).
- v17 fold (CONVENTIONS): reach a TRUE 0-leak by stubbing every `useCurrentUser` render; PROJECT
  §Spec records this escalation; the gateway stays the authoritative RBAC enforcer (UDD nav fold).

Anchors the contract cites: `GET /api/auth/me` (BFF), the upstream `GET {GATEWAY_URL}/admin/auth/me`,
the `ai_proxy_session` cookie, the `CurrentUser{user_id,tenant_id,email,role,exp}` response shape,
and the error codes `ERR_AUTH_NO_SESSION` (401, no/empty cookie) + `ERR_AUTH_INVALID_SESSION` (401,
gateway rejected) + a fail-closed upstream-unreachable response.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: BFF `GET /api/auth/me` verifies the session JWT before returning any identity claim,
by relaying the cookie token to the gateway's authoritative verifier.

Framings weighed:
  - Gateway-relay verify (CHOSEN) — forward the `ai_proxy_session` cookie value as
    `Authorization: Bearer <token>` to `GET {GATEWAY_URL}/admin/auth/me`; trust ONLY the gateway's
    cryptographically-verified `MeResponse`. The BFF holds no signing secret; one authoritative
    verifier (sig + issuer + required-claims + exp), reuses the login/oidc relay shape.
  - Local HS256 verify — the BFF holds `jwt_secret` and verifies the signature itself. REJECTED:
    secret sprawl into the dashboard env + duplicate verify logic that can DRIFT from the gateway's
    (issuer/required-claims/exp), for a marginal latency gain.
  - Status quo (base64 decode, no verify) — REJECTED: this is exactly the escalated gap.

Must:
<must>
  - Read the `ai_proxy_session` cookie and forward its value as `Authorization: Bearer <token>` to
    `GET {GATEWAY_URL}/admin/auth/me`, bounded by an `AbortSignal.timeout` (designed-for-failure).
  - On gateway 200: return `{user_id, tenant_id, email, role, exp: null}` mapped from the verified
    `MeResponse` — the response JSON SHAPE is byte-stable vs today (exp present, value null; the
    gateway already enforces expiry, so no consumer needs the number).
  - Trust an identity ONLY when the gateway verified it; on ANY failure return NO claims (fail-closed).
  - Never place the raw JWT in the response body (preserve today's guarantee) and never log the token.
  - Introduce NO new secret/env in the dashboard — reuse the existing `GATEWAY_URL` resolution.
</must>
Reject:
<reject>
  - No `ai_proxy_session` cookie, or empty token -> 401 "ERR_AUTH_NO_SESSION"  (preserves today)
  - Gateway responds 401 (token invalid / expired / tampered / bad signature) -> 401 "ERR_AUTH_INVALID_SESSION"
  - Gateway unreachable / timeout / 5xx -> 503 "ERR_AUTH_UPSTREAM"  (fail-closed; NEVER trusted claims)
</reject>
After:
<after>
  - An identity is returned by `/api/auth/me` only for a gateway-verified token; an unsigned or
    forged-payload cookie no longer yields trusted nav claims (it 401s → client isError → base nav).
  - The dashboard process holds no JWT signing secret (verification stays the gateway's job).
  - Every test that renders `useCurrentUser` (UsagePage, dashboard-shell, nav suites) stubs
    `/api/auth/me` → the suite's unhandled-request count is a TRUE 0 (the carried v17 0-leak closed).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Dropping the numeric `exp` (returning `exp: null`) is consumer-safe — lowest confidence because
    a dynamic/string-key read could exist that a static grep misses; if wrong: a UI element keyed on
    exp (e.g. a session countdown) silently shows null. Cost: low/cosmetic. Mitigation: the field is
    KEPT (value null) so `?.exp` yields null not undefined; static grep found the route as the sole
    `.exp` site; any govern test asserting exp would go red and surface it.
  - [ ] Fail-FAST (no retry) is the right IO posture for an auth check on the render path — the
    client query is already `retry:false` and a transient blip recovers on the next staleTime
    refetch; a retry storm on identity is worse than a momentary base-nav. (Timeout is mandatory;
    retry/circuit-breaker deliberately omitted on the hot path — confirm at freeze.)
  - [ ] The gateway returns `role` as a plain lowercase string (`str(identity.role)` → "owner"/
    "member"/…) that nav-role-filter's `minRole` comparison already accepts (router.py:80).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Verified session returns the gateway's identity
  Given a valid ai_proxy_session cookie that the gateway will verify
  When the client GETs /api/auth/me
  Then the BFF calls GET {GATEWAY_URL}/admin/auth/me with header Authorization: Bearer <token>
  And it returns 200 { user_id, tenant_id, email, role, exp: null } from the verified MeResponse
  And the raw JWT string never appears anywhere in the response body

Scenario: Forged / unsigned token is rejected (the escalated gap)
  Given an ai_proxy_session cookie whose payload was tampered so the gateway 401s it
  When the client GETs /api/auth/me
  Then the BFF returns 401 { code: "ERR_AUTH_INVALID_SESSION" }
  And no identity claim (user_id / tenant_id / role / email) appears in the body  # fail-closed

Scenario: No session cookie
  Given the request carries no ai_proxy_session cookie
  When the client GETs /api/auth/me
  Then the BFF returns 401 { code: "ERR_AUTH_NO_SESSION" }
  And it makes NO call to the gateway                                  # unchanged: no token, no upstream

Scenario: Empty token value
  Given an ai_proxy_session= cookie whose value is empty
  When the client GETs /api/auth/me
  Then the BFF returns 401 { code: "ERR_AUTH_NO_SESSION" }
  And it makes NO call to the gateway                                  # unchanged: no token, no upstream

Scenario: Gateway unreachable / timeout / 5xx is fail-closed
  Given the gateway verify endpoint is unreachable, times out, or returns 5xx
  When the client GETs /api/auth/me
  Then the BFF returns 503 { code: "ERR_AUTH_UPSTREAM" }
  And no identity claim appears in the body                            # unchanged: never trust on error

Scenario: Response shape is byte-stable (exp preserved as null)
  Given a verified session
  When the client GETs /api/auth/me
  Then the 200 body has exactly the keys { user_id, tenant_id, email, role, exp }
  And exp is null                                                     # unchanged: CurrentUser shape

Scenario: The BFF holds no signing secret (no secret sprawl)
  Given the hardened /api/auth/me route source
  When it is inspected
  Then it references no JWT signing secret and reads no *_SECRET env var
  And verification is delegated entirely to the gateway              # structural invariant

Scenario: True 0-leak test harness (carried v17 follow-up)
  Given the dashboard suites render components that call useCurrentUser (UsagePage, dashboard-shell, nav)
  When the full vitest suite runs
  Then every such render has a stubbed /api/auth/me handler
  And the suite's unhandled-request count for /api/auth/me is 0
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /api/auth/me      (cookie: ai_proxy_session=<jwt>)   — no request body
  relays → GET {GATEWAY_URL}/admin/auth/me   header: Authorization: Bearer <jwt>
           bounded by AbortSignal.timeout (fail-fast, NO retry on the hot path)
  200 -> { user_id: string|null, tenant_id: string|null, email: string|null, role: string|null, exp: null }
         # mapped 1:1 from the gateway's verified MeResponse{user_id,tenant_id,email,role}; exp always null
  401 -> { code: "ERR_AUTH_NO_SESSION" }       # no / empty ai_proxy_session cookie — NO upstream call made
  401 -> { code: "ERR_AUTH_INVALID_SESSION" }  # gateway returned 401 (bad signature / expired / claims invalid)
  503 -> { code: "ERR_AUTH_UPSTREAM" }         # gateway unreachable / timeout / 5xx — fail-CLOSED, no claims

Upstream (already exists, unchanged): GET {GATEWAY_URL}/admin/auth/me
  200 -> MeResponse { user_id, tenant_id, email, role }     # JwtTokenService.decode: HS256 sig + iss + require + exp
  401 -> { ... ERR_AUTH_TOKEN_INVALID }
Schema: no DB. No new env/secret in the dashboard (reuses GATEWAY_URL). The raw JWT never appears
in any /api/auth/me response body and is never logged. Error responses carry NO identity claim.
```

Status: FROZEN @ v1 — approved by auto-mode (Tin standing mandate: contract freezes delegated to auto; the security HARD-STOP is the verify gate) · 2026-06-15
Least-sure flag surfaced at freeze: [contract] returning `exp: null` (dropping the numeric expiry the
old route surfaced) — confidence is high (static grep found NO consumer of `.exp`; the field is kept as
null so `?.exp` stays null-not-undefined; the gateway enforces expiry server-side so the number is dead
data), but a dynamic/string-key read could exist that the grep missed; cost if wrong is low/cosmetic
(an exp-keyed UI shows null) and a govern test asserting exp would go red and surface it. Secondary
[spec] flag: fail-FAST with NO retry on the gateway hop (timeout only) — chosen because the client query
is already retry:false and a retry storm on an identity check is worse than a momentary base-nav.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% lines on the new route; the committed dashboard floor (88.35% lines / 255 tests) must not decrease.
Plan (one test per scenario, asserting behavior — the new relay route tests mock the GATEWAY fetch, never a real upstream):
<test_plan>
  - test_verified_session_relays_and_maps: arrange a cookie token + a gateway 200 MeResponse stub /
    act GET /api/auth/me / assert 200 body {user_id,tenant_id,email,role,exp:null} maps the gateway's
    response AND the outgoing request carried `Authorization: Bearer <token>` AND the raw JWT is absent.
  - test_forged_token_rejected: arrange a cookie + gateway 401 / act GET / assert 401
    {code:"ERR_AUTH_INVALID_SESSION"} AND NO identity claim key appears in the body (fail-closed).
  - test_no_cookie: arrange no cookie / act GET / assert 401 {code:"ERR_AUTH_NO_SESSION"} AND the
    gateway fetch was NEVER called (assert mock 0 calls).
  - test_empty_token: arrange `ai_proxy_session=` empty / act GET / assert 401 ERR_AUTH_NO_SESSION
    AND gateway fetch never called.
  - test_upstream_unreachable_failclosed: arrange the gateway fetch rejects / times out / 5xx / act
    GET / assert 503 {code:"ERR_AUTH_UPSTREAM"} AND no identity claim in body.
  - test_response_shape_stable: arrange verified session / act GET / assert the 200 body keys are
    exactly {user_id,tenant_id,email,role,exp} AND exp === null.
  - test_no_signing_secret (structural): assert app/api/auth/me/route.ts source references no
    *_SECRET / jwt_secret / signing key and performs no local signature verification.
  - test_true_zero_leak (harness, verified by evidence + a shared default handler): every suite that
    renders useCurrentUser stubs /api/auth/me; the full-suite stderr unhandled-request count for
    /api/auth/me is 0 (the v17 monitor is the stderr COUNT, recorded in §6 evidence).
</test_plan>

Tests live in: `apps/dashboard/tests-bff/auth-me-verify.test.ts` `apps/dashboard/tests-bff/route-handlers.test.ts` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/api/auth/me/route.ts` `apps/dashboard/tests-bff/auth-me-verify.test.ts` `apps/dashboard/tests-bff/route-handlers.test.ts` `apps/dashboard/tests-bff/mocks/handlers.ts` `apps/dashboard/tests/mocks/handlers.ts` `apps/dashboard/test-support/legacy-bff-compat.ts`
  (NARROWED after grounding: the tests-bff project already stubs /api/auth/me as a PERSISTENT initial
   handler — only the LEGACY project leaks, because its /api/auth/me default is a RUNTIME server.use()
   in legacy-bff-compat that afterEach resetHandlers() wipes after test #1. Component suites mock at the
   /api/auth/me fetch boundary and are insensitive to the route internals → not touched.
   ADDED at verify (Tin, 2026-06-15): `tests-bff/mocks/handlers.ts` — fix the pre-existing bff
   /api/auth/me default's numeric `exp` to `null` so the mock matches the relay's frozen shape exactly,
   leaving v18 with zero contract-fidelity residue.)
Strategy (ordered batches):
  1. RED (done): `tests-bff/auth-me-verify.test.ts` (8 relay scenarios, gateway fetch mocked) + removed
     the stale base64-decode /api/auth/me cases from `route-handlers.test.ts` → 5 relay tests red.
  2. GREEN-core: rewrite `app/api/auth/me/route.ts` as the gateway relay (Bearer forward, AbortSignal
     timeout, fail-closed 401→ERR_AUTH_INVALID_SESSION / 5xx|network→503 ERR_AUTH_UPSTREAM, exp:null).
  3. 0-LEAK (root-cause fix): MOVE the /api/auth/me default from the runtime `server.use()` in
     `test-support/legacy-bff-compat.ts` INTO the legacy INITIAL handlers `tests/mocks/handlers.ts` so
     `resetHandlers()` preserves it across every test (per-test role overrides via server.use still win,
     LIFO). Run the FULL suite (incl. under load) and confirm 0 unhandled /api/auth/me.
Safety rule (feature-specific): the route is FAIL-CLOSED — on ANY error path (no token, gateway 401,
timeout, 5xx, network) it returns an error code and ZERO identity claims; it never returns a claim it
did not receive from a gateway 200, never logs/echoes the token, and holds no signing secret.
Code lives in: `apps/dashboard/app/api/auth/me/route.ts` (+ the declared test files)
Constraints: do NOT change any test or the contract; allow-list packages only (NO new dependency — the
relay uses the global `fetch` + `AbortSignal.timeout`, matching the login/oidc relays); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full suite 36 files / 263 tests green (`vitest run --testTimeout=20000`), incl. 11 in auth-me-verify.test.ts; run twice for stability.
- [x] coverage did not decrease — `All files 88.35% lines` (the committed floor, held exactly; threshold 80%). The route is under `app/**`, outside the coverage `include` glob (`components/**`,`lib/**`), but its behavior is exercised by all 11 relay tests.
- [x] no test or contract was altered during build to GAME a pass — the frozen §3 contract is untouched; the only test edits were (a) removing the OBSOLETE base64-decode /api/auth/me cases (removed behavior, superseded by the relay suite — not weakened), (b) a TEST-PRECISION fix to the over-broad `/SECRET/i` structural assert (false-positived on an explanatory comment; replaced with precise secret-env/jwt-lib/verify-call patterns — STRENGTHENED, documented), (c) post-review STRENGTHENING (wider secret-env regex + behavioral redirect→503 + whitespace-token + io-bound tests).
- [x] the green was EARNED — adversarial refute-read by a sonnet subagent (security persona): VERDICT EARNED-WITH-GAPS. Confirmed: a forged/unsigned/expired token CANNOT yield trusted claims (all verification delegated to the gateway; every non-200 → fail-closed 401/503 with zero claims); no raw-JWT/secret leak or log; the 8→11 tests are non-vacuous; the old base64 tests were truly removed; the 0-leak fix is structurally correct (initial handler survives resetHandlers; LIFO overrides intact); exp:null is consumer-safe.
- [x] concurrency / timing safe — the gateway hop is bounded by `AbortSignal.timeout(5000)`; fail-FAST no-retry (client query is retry:false); a thrown fetch (network/timeout) → 503, zero claims. No shared mutable state.
- [x] no exposed secrets, injection openings, or unexpected dependencies — NO new dependency (global fetch + AbortSignal, matching login/oidc relays); the BFF holds no signing secret; the raw JWT never appears in any response body or log (asserted). `npm audit --omit=dev` unchanged (no dep delta).
- [x] layering & dependencies follow CONVENTIONS.md — reuses the established BFF-relay pattern (login/oidc-callback); the gateway stays the authoritative verifier/RBAC enforcer (UDD nav fold).
- [x] a person reviewed and approved the change — **Tin, 2026-06-15** (risk:high security gate, autonomy:conservative). Approved as PASS, conditioned on fixing the exp residue first (done — see below).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `GET` is the route's public handler (Next.js route convention; reached by `useCurrentUser`→`fetchCurrentUser` GET /api/auth/me and by the 11 direct tests). The legacy initial `/api/auth/me` handler is wired via `setupServer(...defaultHandlers)` (tests/mocks/server.ts).
- [x] DEAD-CODE (code) — removed the now-unused `meHandler` import + obsolete describe block from route-handlers.test.ts; `legacy-bff-compat.ts` reduced to `export {}` (no orphaned imports). eslint 0 / tsc 0 confirm no unused symbols.
- [x] SEMANTIC (prose / non-code) — read the gateway `GET /admin/auth/me` (router.py:67) + `JwtTokenService` (jwt_service.py) IN FULL to confirm it is the authoritative HS256+iss+exp verifier the relay delegates to; confirmed `MeResponse` has no `exp` (→ relay maps exp:null).

Residue: RESOLVED (Tin asked to clear it before the gate). The tests-bff `bffHandlers` /api/auth/me
default now returns `exp: null`, matching the relay's frozen shape — `tests-bff/mocks/handlers.ts` was
added to §5 and the scope re-snapshotted via a tests→build re-cross. v18 leaves ZERO known residue.

### GATE RECORD
Outcome: PASS — security gate cleared by Tin (risk:high, human-reviewed). The escalated gap is closed:
the BFF no longer trusts an unverified cookie payload; verification is delegated to the gateway's
authoritative /admin/auth/me; the route is fail-closed on every path; adversarial refute-read returned
EARNED-WITH-GAPS with the 2 hardening gaps (redirect-follow + cookie-trim) FIXED in-scope and the exp
residue resolved. No open security finding.
If RISK-ACCEPTED -> n/a — security task, never risk-accepted.
Reviewed by: Tin · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the per-rejection rate of `/api/auth/me` — a spike in
`ERR_AUTH_UPSTREAM` (503) signals a gateway-reachability problem (now visible because identity is a
relay); a spike in `ERR_AUTH_INVALID_SESSION` (401) signals token/clock/issuer drift. Latency: the
added gateway round-trip per identity check (bounded 5s, cached 5min via staleTime).
Spec delta for the next loop: a same-origin BFF surface that exposes identity claims is itself a TRUST
BOUNDARY — "the gateway enforces on proxied requests" does NOT cover a BFF endpoint that hands claims to
the client UI. Every such surface must verify (or delegate verification) before trusting the token.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [SDD · folded] (v18, 2026-06-15) a "UX-only" BFF endpoint that returns identity claims is a TRUST BOUNDARY: it must VERIFY
  the session token's signature (not base64-decode an unverified payload), even when the gateway enforces
  RBAC on proxied requests — the dashboard nav/role still derives from these claims (evidence: the
  escalated /api/auth/me gap; forged-token test now 401s fail-closed).
- [SDD · folded] (v18, 2026-06-15) BFF-relay-to-the-authoritative-verifier beats local secret verification: forward the
  cookie as `Authorization: Bearer` to the gateway's existing `GET /admin/auth/me` (HS256+iss+exp) — no
  secret sprawl into the dashboard, ONE verifier that can't drift. Reusable for any BFF-trusts-a-token
  surface (evidence: route is a relay holding no signing secret; reuses the login/oidc relay shape).
- [TDD · folded] (v18, 2026-06-15) an msw default handler must be an INITIAL handler passed to `setupServer(...)`, NEVER a
  runtime `server.use()` in a setupFile — `afterEach(resetHandlers())` wipes runtime handlers after test
  #1, so the default vanishes and later renders leak (load-dependent "0 unloaded / N loaded"). This was
  the ROOT CAUSE of the carried v17 /api/auth/me 0-leak (evidence: moved the legacy default to initial
  handlers → 0 unhandled across the full suite, run twice).
- [ADD · folded] (v18, 2026-06-15) a server-side fetch RELAY must set `redirect: "manual"` + treat every non-200 as
  fail-closed: a followed 3xx can chain to a trusted 200 from another origin (a fail-OPEN identity
  bypass) — caught by the adversarial refute-read, fixed in-scope (evidence: redirect→503 test).
- [ADD · folded] (v18, 2026-06-15) a structural source-grep guard must be PRECISE, not a bare keyword: `/SECRET/i`
  false-positived on a comment that EXPLAINS the absence of a secret; the precise form matches
  `process.env.*(secret|key|hmac|…)` + jwt-lib imports + verify-call names (evidence: the test-precision
  fix during build; recurring "over-broad assert" smell from the v15/v17 TDD folds).
