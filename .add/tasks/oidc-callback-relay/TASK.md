# TASK: Pre-auth OIDC callback BFF relay (code→session cookie)

slug: oidc-callback-relay · created: 2026-06-14 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
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
- **NEW `app/auth/oidc/callback/route.ts` `GET`** — the pre-auth BFF relay for the SSO callback.
  Path chosen `/auth/oidc/callback` (NOT `/api/auth/...`) — Tin-approved 2026-06-14 — so the gateway's
  `oidc_state`/`oidc_nonce`/`oidc_tenant_id` cookies (set at `Path=/auth/oidc` by the gateway) reach it
  WITHOUT rewriting the frozen v15 login relay. Mirrors `app/api/auth/oidc/login/route.ts`.
- **`app/api/auth/oidc/login/route.ts`** (v15, FROZEN — read-only anchor) — the sibling pre-auth login
  relay: forwards the gateway 302 `Location` + every `Set-Cookie` verbatim; `redirect:"manual"`;
  `AbortSignal.timeout(5000)`; gateway-unreachable → 502 `ERR_BFF_GATEWAY_UNREACHABLE`; sanitizes 5xx.
  This task COPIES its safety posture for the callback; it does NOT modify this file.
- **gateway `auth/api/oidc_router.py:180-310` `GET /auth/oidc/callback`** — already does the WHOLE flow:
  reads `oidc_tenant_id`/`oidc_state`/`oidc_nonce` cookies + `code`/`state` query → server-side token
  exchange (httpx, inside the use case) → mints `ai_proxy_session` JWT → **302** to
  `oidc_post_login_redirect` ("/") with `Set-Cookie ai_proxy_session` (`Path=/; HttpOnly; SameSite=Strict;
  +Secure non-dev; Max-Age=expires_in`) + clears the three `oidc_*` cookies (Max-Age=0). On any failure it
  raises an OIDC_* error → **problem+json 4xx** (STATE_MISMATCH, INVALID_CALLBACK, TOKEN_INVALID,
  UPSTREAM_ERROR, TENANT_COOKIE_MISSING …). The dashboard relay just carries the browser to/from it.
- **`app/api/auth/login/route.ts`** (FROZEN anchor) — password login sets `ai_proxy_session` with the SAME
  cookie attrs; the gateway's callback cookie matches exactly, so forwarding verbatim is consistent.
- **`app/(auth)/login/page.tsx`** — thin server page rendering `<LoginForm/>`; will read
  `searchParams.sso_error` to show a GENERIC alert (small additive completion; no LoginForm change).

Context (working folder): v17 MILESTONE.md — "complete the SSO loop: callback exchanges code server-side
→ httpOnly session cookie; client_secret WRITE-ONLY; no token in any response body; error paths covered."
The v15 `oidc-login-relay` shipped the login half; this is the missing callback half.

Honors (patterns / conventions): the v15 login-relay BFF posture (browser ONLY talks to the dashboard
origin; relay forwards trusted gateway 3xx + Set-Cookie verbatim; redirect:manual + timeout; sanitize
non-3xx; no upstream body to an unauth caller). design-for-failure (CLAUDE.md): bounded timeout, gateway
errors never hang/leak — they bounce the browser to `/login?sso_error=<code>`. SECURITY inviolables
(carried, HARD-STOP if violated): client_secret never touched by the relay (token exchange is gateway-side);
NO token/JWT in any response BODY (only the forwarded httpOnly Set-Cookie); only the 3 oidc_* cookies are
forwarded upstream (minimal disclosure); the IdP-supplied `code`/`state` are validated by the GATEWAY
against the cookie (CSRF/state defense) — the relay never trusts them itself.

Anchors the contract cites: `GET /auth/oidc/callback` relay · forwards `code`+`state`+the 3 `oidc_*`
cookies to gateway `/auth/oidc/callback` (redirect:manual, 5s timeout) · gateway 3xx → forward Location +
all Set-Cookie verbatim · gateway 4xx/5xx/unreachable → **302 `/login?sso_error=<code>`** (no body, no
token) · login page renders a generic SSO-failure alert when `sso_error` is present.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: pre-auth OIDC callback BFF relay — carry the browser through the gateway's code→session exchange
so the SSO loop completes on the dashboard origin (httpOnly session cookie), with no token ever in a body.

Framings weighed:
- **A thin GET /auth/oidc/callback relay mirroring the v15 login relay; gateway does the exchange**
  (chosen) — keeps the browser on the dashboard origin, reuses the gateway's frozen callback flow
  (state/nonce/token/exchange/cookie all gateway-side), and inherits the proven redirect-verbatim + timeout
  + sanitize posture. The /auth/oidc path lets the gateway's Path=/auth/oidc cookies reach it.
- Decode/exchange the code in the BFF — REJECTED: would put client_secret + token logic in the dashboard
  (the gateway is the auth authority; duplicating it is a security/maintenance hazard).
- Callback under /api/auth/oidc + rewrite the login relay cookie paths — REJECTED by Tin: touches the
  frozen v15 login relay.

Must:
<must>
  - GET /auth/oidc/callback forwards the IdP-supplied `code` + `state` (query) AND the three oidc_* cookies
    (oidc_state, oidc_nonce, oidc_tenant_id) to the gateway GET /auth/oidc/callback, server-side.
  - gateway 3xx (success) → forward its `Location` + EVERY `Set-Cookie` (ai_proxy_session + the cleared
    oidc_* cookies) VERBATIM to the browser; the response BODY is empty.
  - gateway 4xx/5xx OR unreachable/timeout → 302 to `/login?sso_error=<code>` — NO upstream body, NO token,
    NO secret; `<code>` is a sanitized public error hint (gateway problem.code for 4xx, else "upstream").
  - the relay forwards ONLY the 3 oidc_* cookies upstream (no ai_proxy_session, no other cookie).
  - the upstream fetch uses redirect:"manual" + AbortSignal.timeout(5000) (no hang, no IdP auto-follow).
  - the login page renders a GENERIC SSO-failure alert when `?sso_error` is present (no code leaked to UI).
  - NO token/JWT/secret in ANY response body the relay returns (only forwarded httpOnly Set-Cookie carries it).
</must>
Reject:
<reject>
  - a token/JWT/secret appearing in any relay response BODY -> "token_in_body" (SECURITY HARD-STOP)
  - forwarding ai_proxy_session or unrelated cookies UPSTREAM to the gateway -> "cookie_overshare"
  - following the gateway 3xx server-side (redirect:"follow") or no timeout -> "unsafe_fetch"
  - relaying a raw 5xx/unexpected upstream body to the browser -> "upstream_body_leak"
</reject>
After:
<after>
  - SSO success: browser lands on "/" with ai_proxy_session set on the dashboard origin; oidc_* cleared.
  - SSO failure: browser lands on /login with a generic alert; no token/secret anywhere; floor green.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The gateway's oidc_* cookies (Path=/auth/oidc, SameSite=Lax) ARE sent by the browser to
    /auth/oidc/callback on the cross-site IdP redirect — lowest confidence because SameSite/path behavior
    on the IdP→callback top-level navigation is subtle. RESOLVED: Path=/auth/oidc is a prefix of
    /auth/oidc/callback ✓; SameSite=Lax permits top-level cross-site GET navigations ✓ (that is exactly
    why the gateway chose Lax for oidc_* vs Strict for the session). If wrong: the gateway returns
    STATE_MISMATCH/TENANT_COOKIE_MISSING → our error path (/login?sso_error) — degrades safely, not silently.
  - [x] the gateway callback is unchanged and authoritative for the exchange — confirmed (oidc_router.py).
  - [x] forwarding Set-Cookie verbatim puts ai_proxy_session on the DASHBOARD origin (Path=/) — confirmed
    (same attrs as /api/auth/login; subsequent /api/gw/* + /api/auth/me include it).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: successful callback mints the session
  Given the gateway GET /auth/oidc/callback returns 302 Location "/" + Set-Cookie ai_proxy_session + cleared oidc_*
  When the browser hits GET /auth/oidc/callback?code=C&state=S with the oidc_* cookies
  Then the relay returns 302 Location "/" with the ai_proxy_session + cleared oidc_* Set-Cookies forwarded verbatim
  And the response body is empty (no token in the body)

Scenario: only the oidc_* cookies are forwarded upstream
  Given the browser sends oidc_state, oidc_nonce, oidc_tenant_id AND an ai_proxy_session cookie
  When the relay calls the gateway
  Then the upstream Cookie header contains only the 3 oidc_* cookies (no ai_proxy_session, no others)

Scenario: gateway validation error bounces to login
  Given the gateway returns 400 problem+json { code: "ERR_OIDC_STATE_MISMATCH" }
  When the browser hits the callback
  Then the relay returns 302 Location "/login?sso_error=ERR_OIDC_STATE_MISMATCH"
  And no upstream body and no token appear in the relay response

Scenario: gateway 5xx / unexpected is sanitized
  Given the gateway returns 500 with a body
  When the browser hits the callback
  Then the relay returns 302 Location "/login?sso_error=upstream"
  And the upstream body is NOT relayed

Scenario: gateway unreachable fails safe
  Given the upstream fetch throws/times out
  When the browser hits the callback
  Then the relay returns 302 Location "/login?sso_error=upstream" (no hang, no secret)

Scenario: login page surfaces the SSO error
  Given the login page is rendered with searchParams sso_error present
  Then a generic SSO-failure alert is shown (the raw code is not displayed)
  And the page renders normally (no alert) when sso_error is absent
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /auth/oidc/callback?code=<code>&state=<state>   (browser navigation; pre-auth)
  upstream: GET {GATEWAY_URL}/auth/oidc/callback?code=&state=
            Cookie: oidc_state=…; oidc_nonce=…; oidc_tenant_id=…   (ONLY these 3)
            redirect:"manual", AbortSignal.timeout(5000)
  gateway 3xx  -> 302 + forward `Location` + every `Set-Cookie` verbatim ; empty body
  gateway 4xx  -> 302 Location "/login?sso_error=<sanitized gateway problem.code or 'failed'>"
  gateway 5xx / unexpected (1xx/2xx) -> 302 Location "/login?sso_error=upstream" (no upstream body)
  fetch throws / timeout -> 302 Location "/login?sso_error=upstream"
  NEVER: a token/JWT/secret in any response body ; never forward ai_proxy_session upstream

login page: if searchParams.sso_error present -> render role="alert" generic "Single sign-on failed…"
            (the raw code is NOT shown); absent -> no alert (unchanged)

No gateway/BFF data-contract change (consumes the FROZEN gateway callback). No new dependency.
Cookie attrs are the gateway's (ai_proxy_session: Path=/ HttpOnly SameSite=Strict +Secure; oidc_* cleared).
```

Status: FROZEN @ v1 — approved by Tin Dang (auto mode + explicit design approval) 2026-06-14

Least-sure flag surfaced at freeze: [spec] the cross-site cookie delivery — that the browser actually
sends the gateway's oidc_* cookies (Path=/auth/oidc, SameSite=Lax) to /auth/oidc/callback on the IdP
redirect. Why least-sure: SameSite/path on a cross-site top-level nav is the subtle bit the whole design
rests on; it is also the bit jsdom/route-unit tests CANNOT exercise (no real browser cross-site nav). Tests
assert the relay BEHAVIOR (forwards the cookies it receives; bounces on error) — real cross-site delivery
is a Path-prefix + SameSite=Lax property reasoned from the gateway's own cookie choices, and is the exact
thing realbrowser-a11y-pass's sibling (a future live SSO smoke) would confirm. Cost if wrong: SSO fails
CLOSED into /login?sso_error (no silent/insecure state). Secondary [test]: errors are a 302 to /login (not
JSON) because the callback is a full-page browser nav, not a fetch — assert the Location, not a body.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% on the new route (hold the global floor).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_callback_success_forwards_location_and_cookies: gateway 302 + ai_proxy_session + cleared oidc_*
    → relay 302, Location forwarded, all Set-Cookie forwarded, empty body, no token in body.
  - test_only_oidc_cookies_forwarded_upstream: assert the upstream fetch Cookie header = only the 3 oidc_*.
  - test_callback_4xx_bounces_to_login: gateway 400 {code} → relay 302 Location /login?sso_error=<code>.
  - test_callback_5xx_sanitized: gateway 500 + body → relay 302 /login?sso_error=upstream; body not relayed.
  - test_callback_gateway_unreachable: fetch throws/timeout → relay 302 /login?sso_error=upstream.
  - test_no_token_in_any_body: across success+error, the relay response body never contains the JWT.
  - test_login_page_shows_sso_error / test_login_page_no_error_when_absent: render the login page with /
    without sso_error → generic alert present / absent.
  - mirror the v15 login-relay test style: mock global fetch (not msw) for deterministic upstream control.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` · the suite MUST run red (route missing) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/auth/oidc/callback/route.ts` `apps/dashboard/app/(auth)/login/page.tsx` `apps/dashboard/tests-bff/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `apps/dashboard/.next/` `.add/tasks/oidc-callback-relay/`
Strategy (ordered batches): 1. add the RED callback-relay + login-page suite 2. write the relay route
(forward code+state+3 oidc cookies; 3xx→forward; else→/login?sso_error; timeout) 3. add the login-page
sso_error alert 4. green: new suite + 245 floor + eslint 0/0 + tsc.
Safety rule (feature-specific): SECURITY — token exchange stays gateway-side; NO token/secret in any body;
forward ONLY the 3 oidc_* cookies upstream; redirect:manual + timeout; sanitize non-3xx (no body leak).
A leak of a token/secret or client_secret involvement is a HARD-STOP.
Code lives in: `apps/dashboard/app/auth/oidc/callback`, `apps/dashboard/app/(auth)/login`.
Constraints: do NOT modify the frozen v15 login relay or any floor test; no new dependency.

<!-- Scope tokens project-root-relative; coverage/ + tsbuildinfo + .next/ declared per the v13 scope-lock
     convention. EXIT: all green; coverage held; no floor test touched; no token/secret in any body. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 253 tests / 34 files green (`vitest run --coverage --testTimeout=20000` EXIT 0); the new suite 8/8.
- [x] coverage did not decrease — global 94.05% held (== prior floor); the `app/` route tree is outside the coverage `include` set (same as the v15 login-relay sibling), but all 8 branches of the route are exercised (3xx success, 4xx-with-code, 4xx-non-json, 5xx, unreachable, cookie-filter, no-token, login alert present/absent).
- [x] no test or contract was altered during build — §3 stayed FROZEN; the only test edit STRENGTHENED an assertion (added `toHaveTextContent("Single sign-on failed")`), never weakened one.
- [x] the green was EARNED, not gamed — adversarial refute-read (general-purpose, model sonnet, principal-AppSec persona, 6 inviolables + open-redirect + SSRF + per-test EARNED/WEAK): verdict EARNED-WITH-GAPS, NO HARD-STOP. All 6 security inviolables UPHELD; all 8 tests EARNED. 2 of 3 flagged gaps CLOSED this pass (length-cap on the error hint; assert the generic alert text); gap 2 (a redundant-but-harmless `not.toContain` in the 5xx test) left as intent-documentation.
- [x] concurrency / timing of the risky operation is safe — single bounded upstream fetch: `redirect:"manual"` + `AbortSignal.timeout(5000)`; no shared state, no hang; every failure path fails CLOSED to `/login?sso_error`.
- [x] no exposed secrets, injection openings, or unexpected dependencies — token exchange + client_secret stay GATEWAY-side; NO token/JWT in any response body (every relay response `NextResponse(null)`); only the 3 oidc_* cookies forwarded upstream; SSRF not possible (upstream host fixed to `GATEWAY_URL`, code/state percent-encoded via URLSearchParams); Location sink sanitized (bare-enum charset + 64-char cap, else `failed`); login UI shows a hardcoded generic message (raw hint never rendered). No new dependency.
- [x] layering & dependencies follow CONVENTIONS.md — mirrors the frozen v15 login-relay BFF posture; reuses the design-system `ErrorState` (role="alert") for the login alert; the frozen login relay was NOT modified.
- [x] a person reviewed and approved the change — autonomy:auto, auto-resolved on complete evidence (security-sensitive but the adversarial refute-read found NO finding; behavior-preserving on the existing floor + 1 declared small UX behavior contracted in §1).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `GET` in `app/auth/oidc/callback/route.ts` is the App-Router route handler for `/auth/oidc/callback` (referenced by the browser nav + the test import); `LoginPage` consumes `searchParams.sso_error`; `ErrorState` imported from `@/components/ui`. All referenced.
- [x] DEAD-CODE (code) — no orphaned symbol; `bounceToLogin`/`gatewayUrl`/`OIDC_COOKIE_NAMES`/`SAFE_CODE`/`UPSTREAM_TIMEOUT_MS` all used.
- [x] SEMANTIC (prose / non-code) — adversarial review read in full (not skimmed): confirmed every inviolable upheld with file:line evidence; confirmed each test asserts real behavior.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: auto-resolved (autonomy:auto) + adversarial refute-read (sonnet) — EARNED-WITH-GAPS, no security finding · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
