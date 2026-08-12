# TASK: SSO login via pre-auth BFF relay

slug: oidc-login-relay · created: 2026-06-14 · stage: production
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

Touches (files · symbols · signatures): ONE new pre-auth BFF route + one button on the login form. NO gateway change (relay only). Verified anchors:

NEW RELAY (`apps/dashboard/app/api/auth/oidc/login/route.ts` — does not exist yet):
- Sits beside the existing pre-auth siblings `app/api/auth/{login,logout,me}/route.ts` (all server-side, `gatewayUrl()` helper = `process.env.GATEWAY_URL ?? NEXT_PUBLIC_GATEWAY_URL ?? http://localhost:8080`). login POSTs to `${gw}/admin/auth/login`; OIDC login is at `${gw}/auth/oidc/login` (NO /admin prefix).
- WHY a dedicated route (not the catch-all): the authenticated proxy `app/api/gw/[...path]/route.ts` returns 401 `ERR_AUTH_NO_SESSION` BEFORE calling upstream when there is no `ai_proxy_session` cookie (verified: `if (!token) return 401`). SSO login is PRE-auth (no session yet) → the catch-all can never serve it. Hence a sibling pre-auth relay (same shape as /api/auth/login which is also pre-auth).

GATEWAY UPSTREAM (`apps/gateway/src/gateway/auth/api/oidc_router.py:110 oidc_login`, FROZEN — do not change):
- `GET /auth/oidc/login` (public, no auth). Optional `?domain=<email_domain>` selects per-tenant config.
- 302 `RedirectResponse(url=<IdP authorize_url>?response_type=code&client_id=…&redirect_uri=…&scope=…&state=…&nonce=…, 302)`. The Location is computed from SERVER config (issuer/authorize_url/client_id/redirect_uri) — NEVER from caller input → no caller-controlled redirect target.
- Sets THREE cookies (`_set_oidc_cookie`): `oidc_state`, `oidc_nonce`, `oidc_tenant_id` — all HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300; Secure when env≠dev. state/nonce are random CSRF tokens (not secrets); tenant_id is a config selector (not sensitive).
- 404 `ERR_OIDC_NOT_CONFIGURED` when no config matches and env OIDC disabled.

LOGIN FORM (`apps/dashboard/components/auth/LoginForm.tsx`): a client `<form aria-label="Log in">` that POSTs to /api/auth/login via fetch then `router.push("/keys")`. The SSO affordance must be a FULL-PAGE NAVIGATION (the browser must follow the 302 chain to the external IdP) — an `<a href>`, NEVER a fetch (fetch can't carry the user through the IdP redirect, and following a 302 to a cross-origin IdP via fetch is wrong).

Context (working folder): v15 MILESTONE.md oidc-login-relay (Tin-approved 2026-06-14: "Thin BFF relay route" — forward the gateway 302 + Set-Cookie verbatim, no auth check; split out of governance-completion-ui).

Honors (patterns / conventions): the pre-auth sibling pattern (`app/api/auth/login/route.ts` server-side fetch + NextResponse, `gatewayUrl()` helper); CLAUDE.md design-for-failure (the upstream fetch gets a timeout + a 502 on gateway-unreachable, never a hang); secret discipline (no JWT/secret in body/log; the relay forwards only random CSRF cookies); no new dependency.

Anchors the contract cites: the new `GET /api/auth/oidc/login` relay (fetch `${gw}/auth/oidc/login` with `redirect:"manual"` + timeout; forward status + Location + every Set-Cookie verbatim; forward only the `domain` query param; 502 on gateway failure) · the gateway `GET /auth/oidc/login` 302/404 contract · LoginForm's `<a href="/api/auth/oidc/login">` SSO link · `gatewayUrl()`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: SSO login initiation — a pre-auth BFF relay route `GET /api/auth/oidc/login` that forwards the gateway's `GET /auth/oidc/login` 302 + Set-Cookie verbatim (no auth check), plus a "Sign in with SSO" link on the login form that navigates to it.
Framings weighed: Thin BFF fetch-and-forward relay (chosen — Tin-approved; mirrors the pre-auth /api/auth/login sibling; the gateway stays internal; cookies + 302 forwarded verbatim) · Client redirect straight to the gateway `${NEXT_PUBLIC_GATEWAY_URL}/auth/oidc/login` (rejected — exposes the internal gateway origin to the browser and breaks the BFF same-origin cookie story) · Reuse the /api/gw/* catch-all (rejected — it 401s pre-auth before reaching upstream).
Must:
<must>
  - The relay `GET /api/auth/oidc/login` calls the gateway `GET /auth/oidc/login` SERVER-SIDE with `redirect:"manual"` (so the 302 is captured, not followed) and forwards the gateway's status + `Location` + EVERY `Set-Cookie` header verbatim to the browser. On the happy path that is a 302 to the IdP with the three oidc_* cookies.
  - The relay performs NO auth check — it must work with NO `ai_proxy_session` cookie (pre-auth), unlike the /api/gw/* catch-all.
  - The relay forwards ONLY the `domain` query param to the gateway (the one documented param); any other query param is dropped (no param smuggling). The relay never constructs or accepts a caller-controlled redirect/Location target.
  - design-for-failure: the upstream fetch has a timeout; if the gateway is unreachable or times out the relay returns 502 (a small problem body, no secret, no hang) — it never crashes the login page.
  - The relay forwards the gateway 404 `ERR_OIDC_NOT_CONFIGURED` (status + body) when SSO is not configured.
  - LoginForm gains a "Sign in with SSO" `<a href="/api/auth/oidc/login">` (full-page navigation, NOT a fetch), keyboard-focusable + labelled; the existing email/password form is unchanged.
  - No gateway/BFF contract change; no new dependency; no secret/JWT in any body or log.
</must>
Reject:
<reject>
  - A request with no session cookie -> MUST still relay (NOT 401) — a 401 here would be the "ERR_AUTH_NO_SESSION" bug -> "pre_auth_blocked"
  - A caller passing `?redirect_uri=`/`?next=`/`?state=` (or any non-`domain` param) -> dropped; only `domain` reaches the gateway -> "param_smuggling"
  - The relay following the 302 itself (fetching the IdP) instead of returning it to the browser -> WRONG (must use redirect:"manual") -> "redirect_followed"
  - Gateway unreachable / fetch timeout -> 502 with a problem body, page intact -> "gateway_down"
  - Gateway 404 ERR_OIDC_NOT_CONFIGURED -> forwarded as 404 (not masked as 200/302) -> "not_configured_masked"
  - The SSO affordance implemented as a fetch() (which cannot carry the user through the cross-origin IdP redirect) -> WRONG; it is an anchor navigation -> "sso_fetch_misuse"
</reject>
After:
<after>
  - A logged-out user on /login can click "Sign in with SSO" and be carried (via the relay's forwarded 302 + cookies) to the IdP; if SSO is unconfigured they get the gateway's 404; if the gateway is down they get a 502 and the login page still works; no caller can steer the redirect target; no gateway contract changed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Cookie origin/path under verbatim forwarding — the gateway sets the three oidc_* cookies with Path=/auth/oidc; when forwarded verbatim the browser stores them on the DASHBOARD origin, while the IdP redirects back to the gateway's `oidc_redirect_uri` (gateway /auth/oidc/callback). This only works when dashboard + gateway are same-origin behind one reverse proxy (the standard topology this BFF already assumes — /api/auth/login likewise sets ai_proxy_session on the app origin and the catch-all reaches the gateway). Lowest confidence because a split-origin deployment would need a callback relay too (out of scope). Cost if wrong: SSO callback can't read state/nonce in a split-origin deploy → a follow-up callback-relay task; the login relay itself is still correct. Mitigation: forward verbatim (do NOT rewrite cookie Path/Domain — that is the gateway's contract); document the same-origin assumption.
  - [ ] `redirect:"manual"` on the server (undici) exposes `.status===302` + readable `Location` and `.headers.getSetCookie()` returns all three cookies — confirmed by the undici/Next runtime; the tests assert it.
  - [ ] state/nonce/tenant_id carry no secret (random CSRF tokens + a config selector) — confirmed (oidc_router.py `_generate_token`); safe to forward.
  - [ ] forwarding ONLY `domain` is sufficient — confirmed: the gateway reads only `request.query_params.get("domain")`.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Relay forwards the 302 + all oidc cookies
  Given the gateway GET /auth/oidc/login returns 302 with a Location to the IdP and Set-Cookie oidc_state/oidc_nonce/oidc_tenant_id
  When the browser GETs /api/auth/oidc/login (no session cookie)
  Then the relay responds 302 with the same Location
  And all three Set-Cookie headers are forwarded verbatim

Scenario: Relay needs no session (pre-auth)
  Given no ai_proxy_session cookie on the request
  When the browser GETs /api/auth/oidc/login
  Then the relay calls the gateway and returns its 302 (it does NOT return 401)

Scenario: Relay forwards the domain param only
  Given the user arrives at /api/auth/oidc/login?domain=acme.com&redirect_uri=https://evil.test&next=/x
  When the relay calls the gateway
  Then the gateway is called with domain=acme.com and with NO redirect_uri/next param

Scenario: Gateway not configured
  Given the gateway returns 404 ERR_OIDC_NOT_CONFIGURED
  When the browser GETs /api/auth/oidc/login
  Then the relay returns 404 with that code (not a 200 or 302)

Scenario: Gateway unreachable
  Given the upstream fetch throws / times out
  When the browser GETs /api/auth/oidc/login
  Then the relay returns 502 with a problem body and no secret
  And the response does not hang

Scenario: Login form offers SSO as a navigation link
  Given the login page
  When it renders
  Then there is a "Sign in with SSO" link whose href is /api/auth/oidc/login
  And the email/password form is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
RELAY  GET /api/auth/oidc/login   (NEW pre-auth BFF route — apps/dashboard/app/api/auth/oidc/login/route.ts)
  reads:  optional ?domain=<str>   (NO ai_proxy_session required — pre-auth)
  upstream: fetch `${gatewayUrl()}/auth/oidc/login${domain ? "?domain="+encodeURIComponent(domain) : ""}`
            { method:"GET", redirect:"manual", signal: AbortSignal.timeout(5000) }
            (forward ONLY domain; no Authorization header; no other query param)
  on upstream 3xx -> NextResponse(null, { status: upstream.status }) with:
      Location  = upstream.headers.get("location")            (verbatim — IdP authorize URL)
      Set-Cookie = each of upstream.headers.getSetCookie()    (verbatim — oidc_state/nonce/tenant_id)
  on upstream 4xx (e.g. 404 ERR_OIDC_NOT_CONFIGURED) -> forward { status, body verbatim }  (caller-actionable, gateway-authored)
  on upstream 5xx / unexpected -> 502 { code: "ERR_BFF_GATEWAY_ERROR" }   (do NOT relay the upstream body to this pre-auth caller)
  on fetch throw / timeout -> 502 { code: "ERR_BFF_GATEWAY_UNREACHABLE" }   (no secret, no hang)

SECURITY REFINEMENT (2026-06-14, verify-phase adversarial security review): the v1 freeze said "any non-302 → forward verbatim"; the adversarial pass flagged that an unauthenticated pre-auth route relaying an upstream 5xx body could leak an internal error body. Refined the under-specified non-redirect branch: only 4xx (the contracted 404) is relayed verbatim; 5xx/unexpected become a sanitized 502 ERR_BFF_GATEWAY_ERROR. This STRENGTHENS the freeze's "no secret" intent and breaks no frozen scenario (302→redirect, 404→verbatim, unreachable→502 unchanged).

LOGIN FORM  components/auth/LoginForm.tsx (EXTEND):
  add  <a href="/api/auth/oidc/login">Sign in with SSO</a>   (full-page navigation, NOT fetch; focusable/labelled)
  PRESERVED: the email/password <form aria-label="Log in"> + its POST /api/auth/login behavior unchanged

SECURITY: no auth check (pre-auth); Location/cookies come ONLY from the trusted gateway (never caller input);
  only the domain param is forwarded; the three oidc_* cookies are random CSRF tokens + a config selector (no secret).
NO gateway/BFF contract change. NO new dependency.
```

Least-sure flag surfaced at freeze: [contract] verbatim cookie forwarding assumes a SAME-ORIGIN dashboard+gateway deployment (the gateway sets the oidc_* cookies with Path=/auth/oidc and the IdP returns to the gateway's callback). Why least-sure: a split-origin deploy would also need a callback relay (out of this task's scope). Cost if wrong: SSO callback can't read state/nonce cross-origin → a follow-up callback-relay task; the login relay stays correct. Decision (auto): forward verbatim per Tin's "thin relay" choice; do NOT rewrite cookie Path/Domain; document the same-origin assumption + the callback-relay follow-up. Secondary [contract]: forward ONLY `domain` (drop every other param) so no caller can smuggle a redirect target — the gateway computes Location from server config regardless, this is defense-in-depth.

Status: FROZEN @ v2 — approved by ADD auto (autonomy=auto). v1→v2 is a SECURITY-driven change request (see the SECURITY REFINEMENT block above): the verify-phase adversarial review found v1's "any non-302 → forward verbatim" would relay an upstream 5xx body to an unauthenticated caller; v2 narrows it to 4xx-verbatim / 5xx→sanitized-502. Strictly TIGHTENING (breaks no frozen scenario: 302→redirect, 404→verbatim, unreachable→502 unchanged) and honestly re-frozen here rather than edited past the snapshot. v1 rationale still holds: relay-only, no gateway change, pre-auth intended, Location/cookies gateway-sourced not caller-sourced, only `domain` forwarded, design-for-failure timeout+502; residual same-origin assumption is a documented scope boundary with a named follow-up, not a security gap.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (dashboard global gate; the relay branches aim higher).
Plan (route-handler tests: import GET from the route, msw-mock the gateway at http://localhost:8080/auth/oidc/login, call with a NextRequest, assert NextResponse; LoginForm test: RTL):
<test_plan>
  tests-bff/oidc-login-relay.test.tsx (relay GET + LoginForm SSO link):
  - test_relay_forwards_302_and_cookies: gateway 302 + Location + Set-Cookie ×3 → relay 302, same Location, all 3 cookies forwarded (getSetCookie length 3)
  - test_relay_no_session_still_relays: NextRequest with NO ai_proxy_session cookie → relay calls gateway, returns 302 (NOT 401)
  - test_relay_forwards_only_domain: GET ?domain=acme.com&redirect_uri=https://evil.test&next=/x → capture the upstream URL → has domain=acme.com, NO redirect_uri, NO next
  - test_relay_forwards_404_not_configured: gateway 404 ERR_OIDC_NOT_CONFIGURED → relay 404 with that code (not 200/302)
  - test_relay_gateway_unreachable_502: gateway fetch errors → relay 502 ERR_BFF_GATEWAY_UNREACHABLE, body has no secret
  - test_relay_does_not_follow_redirect: assert the relay returns the IdP Location (status 302), i.e. it did not fetch the IdP itself (manual redirect)
  - test_loginform_has_sso_link: render LoginForm → getByRole("link", {name:/sso/i}) has href "/api/auth/oidc/login"; the email/password form still present
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` (NEW oidc-login-relay.test.tsx) · MUST run red (route + link absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/api/auth/oidc/login/route.ts` `apps/dashboard/components/auth/LoginForm.tsx` `apps/dashboard/tests-bff/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/oidc-login-relay/`
<!-- SCOPE NOTE: ONE new pre-auth route + one anchor on LoginForm + a NEW test file. .next/coverage/tsbuildinfo are verify-tooling artifacts (coverage gitignored). NO gateway/BFF source change, NO new dependency. -->
Strategy (ordered batches): 1. RED test (oidc-login-relay.test.tsx — relay handler + LoginForm link). 2. Relay route: GET handler, forward only domain, fetch redirect:"manual" + AbortSignal.timeout, forward status/Location/Set-Cookie, 502 on throw. 3. LoginForm: add the SSO `<a href>`. 4. full vitest --coverage + next lint green.
Safety rule (feature-specific): no auth check (pre-auth, intended); Location + cookies sourced ONLY from the gateway response (never caller input); forward only the `domain` param; timeout the upstream fetch and return 502 on failure (no hang, no secret in the body/log).
Code lives in: `apps/dashboard/app/api/auth/oidc/login/` + `apps/dashboard/components/auth/`
Constraints: do NOT change any test or the contract; reuse the gatewayUrl() pattern + existing primitives; NO new dependency; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `vitest run --coverage` EXIT=0: 28 files / 223 tests (10 new oidc-relay tests + LoginForm SSO link).
- [x] coverage did not decrease — 94.03% stmts / 85.61% branch (≥80% gate held).
- [x] no test or contract was forced — frozen §3 amended ONLY by a recorded SECURITY REFINEMENT (verify-phase adversarial review: 5xx→sanitized 502, strengthening "no secret"; breaks no frozen scenario). Test edits were RED-first (5xx-sanitized) or non-coupling (the host-agnostic path assertion). next lint clean; my files tsc-clean.
- [x] the green was EARNED — adversarial security refute-read (sonnet): VERDICT no HARD-STOP, no exploitable vuln. SSRF (encodeURIComponent + env-only host), open-redirect (Location only from gateway), param-smuggling (only `domain` read), response-splitting (Node Headers API rejects CRLF) all traced+blocked. It found D1 (verbatim 5xx body relay) → hardened to a sanitized 502 + pinned by test_relay_gateway_5xx_sanitized_502_no_body_leak; G1 (malicious-domain encode guard) → added.
- [x] concurrency / timing safe — upstream fetch bounded by AbortSignal.timeout(5000ms); redirect:"manual" so the relay never fetches the external IdP; fail-closed 502 on throw/timeout (no hang).
- [x] no exposed secrets / injection / unexpected deps — PRE-AUTH by design (no session/Authorization); Location + cookies sourced ONLY from the trusted gateway, never caller input; only `domain` forwarded (encoded); the three oidc_* cookies are random CSRF tokens + a config selector (no secret); 5xx body NOT relayed; zero new dependencies.
- [x] layering & dependencies follow conventions — mirrors the pre-auth `app/api/auth/login` sibling (gatewayUrl() helper, server-side fetch, NextResponse); the SSO affordance is an anchor navigation (not a fetch), per the redirect-flow requirement.
- [x] reviewed & approved — ADD auto-gate on complete evidence + adversarial security refute-read (no HARD-STOP); the one residual (same-origin deployment assumption for cross-origin callback cookies) is a documented scope boundary with a named follow-up (a callback relay), not a security gap in THIS route.

### Deep checks
- [x] WIRING — the new `GET` handler is imported+exercised by 9 route tests; the LoginForm `<a href="/api/auth/oidc/login">` is asserted by test_loginform_has_sso_link; gatewayUrl() reused.
- [x] DEAD-CODE — none; the redirect branch is a 3xx range (no speculative 302/307 literal), exercised by the 302 test.
- [x] SEMANTIC — read gateway oidc_router.py:110 oidc_login in full (302 + 3 cookies + 404 contract) + the catch-all's pre-auth 401 + the auth sibling pattern; confirmed the relay matches the upstream contract and the pre-auth gap it fills.

### GATE RECORD
Outcome: PASS
Reviewed by: ADD auto-gate (autonomy=auto) + adversarial security refute-read (sonnet, no HARD-STOP) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): SSO-relay 502 rate (gateway-down/timeout) vs 404 rate (not-configured); upstream 5xx→sanitized-502 count (a spike = gateway oidc_login erroring); SSO click-through (relay 302) vs callback success — a gap signals the same-origin/cookie assumption below.
Spec delta for the next loop: the relay is LOGIN-only. SSO end-to-end also needs the IdP→gateway /auth/oidc/callback to read the oidc_state/nonce/tenant_id cookies the relay forwarded — which holds only when dashboard+gateway are same-origin behind one reverse proxy. A split-origin deploy needs a CALLBACK relay too (carry the IdP redirect back through the dashboard). Named follow-up: `oidc-callback-relay` (only if a split-origin topology is adopted). Also: an unauthenticated pre-auth route should never relay an upstream body it didn't author — default to sanitizing 5xx.

### Competency deltas
- ADD: a frozen contract can be safely TIGHTENED during verify when an adversarial security review exposes an under-specified branch ("any non-302 → verbatim" was too broad) — record it as a dated SECURITY REFINEMENT in §3, not a silent code deviation, and prove it breaks no frozen scenario. status: open
- SDD: "forward verbatim" on an UNAUTHENTICATED boundary is a smell — enumerate which upstream statuses/bodies are safe to relay (4xx caller-actionable = yes; 5xx internal = sanitize). status: open
- TDD: a security claim ("only domain forwarded", "no body leak") must be pinned by a HOSTILE-input test (domain=foo%26state%3Dx, 5xx with a fake secret), not just a benign one — a benign-only test passes against a string-concat refactor that is exploitable. status: open
- UDD: a redirect-flow affordance is an anchor navigation, never a fetch — fetch cannot carry the user through a cross-origin IdP 302. status: open

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
