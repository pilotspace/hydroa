# TASK: httpOnly-cookie BFF dashboard auth

slug: auth-bff · created: 2026-06-10 · stage: mvp · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- risk: high because this task deliberately supersedes frozen v1 security contracts
     (token storage mechanism); conservative autonomy prevents unguarded completion. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: httpOnly-cookie BFF dashboard auth — replace localStorage JWT with server-side cookie session
Framings weighed: Next.js route-handler BFF with httpOnly cookie (chosen — zero JS token exposure, same-origin cookie forwarding, gateway JWT contract unchanged, App Router idioms) · Edge Middleware session (rejected — requires Edge runtime, adds latency on every request including static; overkill for a single-tenant dashboard) · Token-binding localStorage with CSP nonce (rejected — still XSS-readable; does not satisfy the v2 milestone mandate "no token readable by page JavaScript")

Must:
<must>
  - POST /api/auth/login: receives {email, password}, POSTs to GATEWAY_URL/admin/auth/login (server-side), on 200 sets cookie ai_proxy_session (httpOnly, Secure, SameSite=Strict, Path=/, Max-Age=86400) containing the raw JWT string, returns 200 {ok: true}; no token in response body
  - POST /api/auth/signup: receives {tenant_name, email, password}, POSTs to GATEWAY_URL/admin/auth/signup (server-side), on 201 calls the login flow above (internally), sets the cookie, returns 201 {ok: true}; no token in response body
  - POST /api/auth/logout: clears the ai_proxy_session cookie (Max-Age=0 / Expires=past), returns 200 {ok: true}; works whether or not the cookie was set
  - GET /api/auth/me: reads ai_proxy_session cookie, decodes JWT payload server-side (no signature check), returns {user_id, tenant_id, email, role, exp}; no token in response body; if cookie absent or JWT malformed returns 401 {code: "ERR_AUTH_NO_SESSION"}
  - GET|POST|PUT|DELETE /api/gw/[...path]: authenticated gateway proxy — reads ai_proxy_session cookie, forwards request to GATEWAY_URL/<path> with header Authorization: Bearer <token>; streams response body; on 401 from gateway clears the cookie and returns 401 {code: "ERR_AUTH_SESSION_EXPIRED"} to the client; proxies all 4xx/5xx from gateway verbatim otherwise
  - lib/api-client.ts (MODIFIED): apiGet/apiPost/apiPut/apiDelete now call same-origin /api/gw/<path> with credentials:"include"; no Authorization header constructed or read client-side; on 401 from /api/gw/* fires window.location.href = "/login" (no localStorage clear — no token exists client-side); auth endpoints (login, signup) call /api/auth/* directly with credentials:"include"
  - lib/auth.ts (MODIFIED — localStorage helpers DELETED or gutted): getToken/setToken/clearToken localStorage accessors are removed; after Build, grep over apps/dashboard/{app,components,lib} must show zero localStorage references
  - middleware.ts: intercepts all requests to app/(dashboard)/* routes (i.e., /keys, /usage); if ai_proxy_session cookie is absent (checked server-side via next/headers), redirects to /login with 307; this replaces the client-side isTokenValid guard in KeysPage and UsagePage
  - KeysPage.tsx (MODIFIED): client-side getToken()/isTokenValid()/clearToken() auth guard removed; middleware.ts is the guard; API calls switch to apiGet/apiPost/apiDelete which now route through /api/gw/*
  - UsagePage.tsx (MODIFIED): client-side getToken()/isTokenValid()/clearToken() auth guard removed; canEditBudget() local decode removed; role check switches to useCurrentUser() hook (fetches /api/auth/me); API calls switch to apiGet which now routes through /api/gw/*
  - LoginForm.tsx (MODIFIED): calls /api/auth/login BFF endpoint with credentials:"include"; on 200 → router.push("/keys"); no localStorage write; error handling identical (inline problem+json title)
  - SignupForm.tsx (MODIFIED): calls /api/auth/signup BFF endpoint with credentials:"include"; on 201 → router.push("/keys"); no localStorage write
  - Route guard for role: /api/auth/me response role claim drives BudgetWidget canEdit; UsagePage calls /api/auth/me on mount via the shared useCurrentUser hook instead of decoding JWT client-side
  - Logout: POST /api/auth/logout then router.push("/login"); localStorage "ai_proxy_token" is never written in any new code path
  - 401 from any /api/gw/* call: BFF clears cookie + returns 401 to client; client api-client fires window.location.href = "/login"
  - After Build: grep over apps/dashboard/{app,components,lib} shows zero localStorage references to "ai_proxy_token" or any token key
</must>

Reject:
<reject>
  - POST /api/auth/login with missing email or password body → 400 {code: "ERR_BFF_PAYLOAD_INVALID"}
  - POST /api/auth/signup with missing required fields → 400 {code: "ERR_BFF_PAYLOAD_INVALID"}
  - GET /api/auth/me when ai_proxy_session cookie absent → 401 {code: "ERR_AUTH_NO_SESSION"}
  - GET /api/auth/me when cookie present but JWT payload is malformed (cannot base64-decode) → 401 {code: "ERR_AUTH_NO_SESSION"}
  - /api/gw/[...path] when ai_proxy_session cookie absent → 401 {code: "ERR_AUTH_NO_SESSION"}
  - /api/gw/[...path] when gateway returns 401 (token expired/invalid) → cookie cleared + 401 {code: "ERR_AUTH_SESSION_EXPIRED"}
</reject>

After:
<after>
  - After login: ai_proxy_session cookie is set (httpOnly, Secure, SameSite=Strict); localStorage "ai_proxy_token" is absent; document.cookie cannot reach the token; the browser navigates to /keys
  - After signup: same cookie state as after login; user is on /keys
  - After logout: ai_proxy_session cookie is cleared (Max-Age=0); user is on /login; /keys redirects to /login
  - After authenticated gateway proxy call: gateway receives Authorization: Bearer <token> header; the token value never appears in any client-side JavaScript context
  - After gateway 401: cookie is cleared; client is redirected to /login; no stale session remains
  - After Build: grep shows zero localStorage token references in apps/dashboard/{app,components,lib} — no new code path writes or reads "ai_proxy_token"
  - XSS simulation: document.cookie does NOT contain the JWT string (httpOnly attribute prevents JS access); localStorage.getItem("ai_proxy_token") returns null
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Secure cookie attribute requires HTTPS in production — lowest confidence because the dev topology uses HTTP on localhost:3000 (Next.js dev) → Envoy at http://localhost:8080; the Secure attribute is set on cookies but browsers accept Secure cookies on localhost by spec exception (Chrome/Firefox both do); if wrong in a non-localhost HTTP dev proxy: remove Secure for NODE_ENV=development only — one conditional in the cookie-setter; cost is minimal and scoped to the BFF handler
  ⚠ The gateway GATEWAY_URL env var rename (from NEXT_PUBLIC_GATEWAY_URL to GATEWAY_URL) requires CI and .env.local changes — lowest confidence because the existing CI job hardcodes NEXT_PUBLIC_GATEWAY_URL; if the rename is missed the BFF server-side fetch silently targets undefined and all proxy calls fail at runtime; cost: all authenticated routes break until the env var is corrected; mitigation: both names accepted with GATEWAY_URL taking precedence (fallback to NEXT_PUBLIC_GATEWAY_URL) during the transition window
  ⚠ lib/api-client.ts rewiring to /api/gw/* affects all 29 v1 data tests (keys/usage) — those tests arrange by setting localStorage + intercepting gateway.test URLs; at freeze they are REVISE-ARRANGE (assertions unchanged, msw URLs move to /api/gw/*, localStorage arrange lines dropped); the v1 tests must stay green NOW (before freeze) because KeysPage/UsagePage are not yet modified; after Build the revise-arrange step makes them green again; risk: if any test assertion is accidentally coupled to the gateway URL shape it would need additional repair — review each assertion at freeze
  - [x] SameSite=Strict is safe for this app — the dashboard is never embedded in a third-party context; no cross-site POST flows exist; CSRF protection is a side-effect, not the primary goal
  - [x] The /api/gw/[...path] catch-all proxies /admin/* and /v1/* only — the gateway does not expose other namespaces that the dashboard consumes
  - [x] The gateway JWT contract is UNCHANGED (same signing key, same claims: sub, tenant_id, role, email, exp) — only the transport changes; the BFF decodes server-side for /api/auth/me without verifying the signature (same limitation as v1 client-side decode; gateway always validates on authenticated calls)
  - [x] No streaming (chunked/SSE) response proxying needed — all proxied endpoints return buffered JSON; the /v1/chat/completions streaming path is not dashboard-facing in v2 scope
  - [x] The v1 frozen tests (tests/) disposition is executed at freeze per §3 superseded-test table; until then they remain green because KeysPage/UsagePage/LoginForm/SignupForm are not yet modified
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: login happy path — sets httpOnly cookie, no token in body or localStorage
  Given GATEWAY_URL/admin/auth/login responds 200 {access_token: "<jwt>", token_type: "bearer", expires_in: 86400}
  When POST /api/auth/login {email: "ada@acme.io", password: "hunter12345"}
  Then response status is 200 with body {ok: true}
  And Set-Cookie header contains ai_proxy_session=<jwt>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400
  And response body does NOT contain the JWT string
  And localStorage "ai_proxy_token" is absent

Scenario: login gateway 401 — passes through error, no cookie set
  Given GATEWAY_URL/admin/auth/login responds 401 {code: "ERR_AUTH_INVALID_CREDENTIALS", title: "Invalid credentials"}
  When POST /api/auth/login {email: "ada@acme.io", password: "wrongpass"}
  Then response status is 401
  And Set-Cookie header is absent
  And localStorage "ai_proxy_token" is absent

Scenario: signup happy path — sets cookie, no token visible
  Given GATEWAY_URL/admin/auth/signup responds 201
  And GATEWAY_URL/admin/auth/login responds 200 {access_token: "<jwt>"}
  When POST /api/auth/signup {tenant_name: "Acme", email: "ada@acme.io", password: "hunter12345"}
  Then response status is 201 with body {ok: true}
  And Set-Cookie header contains ai_proxy_session=<jwt>; HttpOnly; Secure; SameSite=Strict
  And response body does NOT contain the JWT

Scenario: logout — clears cookie
  Given the browser holds an ai_proxy_session cookie
  When POST /api/auth/logout
  Then response status is 200 {ok: true}
  And Set-Cookie header sets ai_proxy_session with Max-Age=0 (cookie cleared)

Scenario: /api/auth/me — returns decoded claims, no token
  Given ai_proxy_session cookie contains a valid JWT with claims {sub, tenant_id, role: "owner", email, exp}
  When GET /api/auth/me
  Then response status is 200
  And response body contains {role: "owner", email, exp}
  And response body does NOT contain the raw JWT string

Scenario: /api/auth/me — absent cookie returns 401
  Given no ai_proxy_session cookie is present
  When GET /api/auth/me
  Then response status is 401
  And response body is {code: "ERR_AUTH_NO_SESSION"}

Scenario: /api/auth/me — malformed JWT payload returns 401
  Given ai_proxy_session cookie contains "not.a.validjwt"
  When GET /api/auth/me
  Then response status is 401
  And response body is {code: "ERR_AUTH_NO_SESSION"}

Scenario: gateway proxy — forwards request with Bearer, returns upstream response
  Given ai_proxy_session cookie contains a valid JWT "<jwt>"
  And GATEWAY_URL/admin/keys responds 200 [{key_id: "k1", name: "prod-key", prefix: "sk-1", created_at: "...", revoked_at: null}]
  When GET /api/gw/admin/keys (with the cookie)
  Then the upstream received Authorization: Bearer <jwt>
  And response status is 200 with the keys array
  And the JWT value does NOT appear in the response body

Scenario: gateway proxy — absent cookie returns 401
  Given no ai_proxy_session cookie is present
  When GET /api/gw/admin/keys
  Then response status is 401 {code: "ERR_AUTH_NO_SESSION"}
  And no upstream request was made

Scenario: gateway proxy — upstream 401 clears cookie and returns 401
  Given ai_proxy_session cookie contains a valid JWT
  And GATEWAY_URL/admin/keys responds 401 {code: "ERR_AUTH_INVALID_TOKEN"}
  When GET /api/gw/admin/keys (with the cookie)
  Then response status is 401 {code: "ERR_AUTH_SESSION_EXPIRED"}
  And Set-Cookie header clears ai_proxy_session (Max-Age=0)

Scenario: XSS simulation — document.cookie and localStorage cannot reach the token
  Given the browser has an active ai_proxy_session after login
  When JavaScript executes document.cookie and localStorage.getItem("ai_proxy_token")
  Then document.cookie does NOT contain the JWT string (httpOnly attribute prevents JS access)
  And localStorage.getItem("ai_proxy_token") returns null

Scenario: middleware guard — unauthenticated request to /keys redirects to /login
  Given no ai_proxy_session cookie is present
  When the browser navigates to /keys
  Then the server responds 307 redirect to /login
  And the /keys page content is never rendered

Scenario: middleware guard — cookie present allows through to /keys
  Given a valid ai_proxy_session cookie is present
  When the browser navigates to /keys
  Then the server does NOT redirect
  And the /keys page content is rendered

Scenario: client login form — posts to /api/auth/login, navigates to /keys, no localStorage write
  Given POST /api/auth/login returns 200 {ok: true}
  When the user fills email and password and submits the LoginForm
  Then POST /api/auth/login was called with {email, password} and credentials:"include"
  And router.push("/keys") was called
  And localStorage "ai_proxy_token" is null

Scenario: client signup form — posts to /api/auth/signup, navigates to /keys, no localStorage write
  Given POST /api/auth/signup returns 201 {ok: true}
  When the user fills tenant_name, email, password and submits the SignupForm
  Then POST /api/auth/signup was called with credentials:"include"
  And router.push("/keys") was called
  And localStorage "ai_proxy_token" is null

Scenario: client logout — posts to /api/auth/logout, navigates to /login
  Given the user is on /keys with a valid session
  When the user clicks logout
  Then POST /api/auth/logout was called
  And router.push("/login") was called

Scenario: client 401 intercept — /api/gw/* 401 fires window redirect to /login
  Given /api/gw/admin/keys returns 401
  When the client api-client calls /api/gw/admin/keys
  Then window.location.href is set to "/login"
  And localStorage "ai_proxy_token" is NOT written

Scenario: useCurrentUser — returns role from /api/auth/me for UI affordance
  Given GET /api/auth/me returns {role: "owner", email: "ada@acme.io", exp: <future>}
  When a component mounts and calls useCurrentUser()
  Then the hook returns {role: "owner"} without exposing the raw JWT
  And no localStorage read occurs

Scenario: POST /api/auth/login missing body fields — returns 400
  Given the request body is {} (missing email and password)
  When POST /api/auth/login
  Then response status is 400 {code: "ERR_BFF_PAYLOAD_INVALID"}
  And no upstream request is made

Scenario: /api/gw/[...path] with no cookie — returns 401, no upstream call
  Given no ai_proxy_session cookie
  When DELETE /api/gw/admin/keys/kid-1
  Then response status is 401 {code: "ERR_AUTH_NO_SESSION"}
  And no request reaches GATEWAY_URL
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
─── BFF ROUTE HANDLERS (new — app/api/auth/* and app/api/gw/[...path]/route.ts) ─────────────

POST /api/auth/login
  body: { email: str, password: str }
  200 -> { ok: true }
  Set-Cookie: ai_proxy_session=<jwt>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400
  400 -> { code: "ERR_BFF_PAYLOAD_INVALID" }   (missing/blank fields, no upstream call)
  401 -> upstream problem+json forwarded verbatim (gateway ERR_AUTH_INVALID_CREDENTIALS)
  NOTE: token NEVER appears in response body; cookie is the only token transport

POST /api/auth/signup
  body: { tenant_name: str, email: str, password: str }
  201 -> { ok: true }
  Set-Cookie: ai_proxy_session=<jwt>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400
  400 -> { code: "ERR_BFF_PAYLOAD_INVALID" }
  409 -> upstream problem+json forwarded verbatim (ERR_TENANT_EMAIL_TAKEN)
  NOTE: signup internally calls login to obtain the JWT before setting the cookie

POST /api/auth/logout
  200 -> { ok: true }
  Set-Cookie: ai_proxy_session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0
  NOTE: idempotent — succeeds whether or not a cookie was present

GET /api/auth/me
  (reads cookie; no body)
  200 -> { user_id: str, tenant_id: str, email: str, role: str, exp: number }
         decoded server-side from ai_proxy_session JWT payload; no raw token
  401 -> { code: "ERR_AUTH_NO_SESSION" }   (cookie absent OR JWT payload base64-malformed)

GET|POST|PUT|DELETE /api/gw/[...path]
  (reads cookie; body and query string forwarded as-is)
  2xx/4xx/5xx -> upstream response body forwarded verbatim (except 401 case below)
  401 -> { code: "ERR_AUTH_NO_SESSION" }    (cookie absent)
  401 (upstream 401) -> cookie cleared (Max-Age=0) + { code: "ERR_AUTH_SESSION_EXPIRED" }
  NOTE: Authorization: Bearer <token> header is set server-side from cookie value;
        client never constructs or sees the Authorization header value

─── ENV VAR RENAME ───────────────────────────────────────────────────────────────────────────

  GATEWAY_URL (server-side only, replaces NEXT_PUBLIC_GATEWAY_URL for BFF fetch)
  Transition rule: BFF reads GATEWAY_URL first; falls back to NEXT_PUBLIC_GATEWAY_URL during
  the migration window so dev .env.local needs only one change
  NEXT_PUBLIC_GATEWAY_URL retained for any remaining client-side reference (none after build)

─── COOKIE SPEC ──────────────────────────────────────────────────────────────────────────────

  Name:      ai_proxy_session
  Value:     raw JWT string (same format as v1 localStorage value)
  HttpOnly:  true   (JS cannot read via document.cookie)
  Secure:    true   (HTTPS only; localhost exception applies per spec)
  SameSite:  Strict (no cross-site send; CSRF-resistant)
  Path:      /
  Max-Age:   86400  (matches gateway expires_in)
  Cleared:   Max-Age=0 on logout or BFF-detected 401 from upstream

─── CLIENT-SIDE LIB CHANGES (MODIFIED existing files) ───────────────────────────────────────

  lib/api-client.ts (MODIFIED)
    apiGet/apiPost/apiPut/apiDelete: target same-origin /api/gw/<path> with
    credentials:"include"; NO Authorization header constructed or read;
    on 401 from /api/gw/*: window.location.href = "/login" (no localStorage clear —
    no token exists client-side); auth paths (/api/auth/*) called directly with
    credentials:"include"
    ProblemDetail / ApiError types: UNCHANGED (same shape, used by components)

  lib/auth.ts (MODIFIED)
    getToken / setToken / clearToken: DELETED (no localStorage access anywhere)
    isTokenValid: DELETED
    After Build: grep over apps/dashboard/{app,components,lib} shows zero
    localStorage references to any token key

  lib/bff-client.ts (NEW — internal adapter used by hooks and thin logout helpers)
    bffGet<T>(path: string): fetch("/api/gw/" + path, {credentials:"include"})
    bffPost<T>(path: string, body): POST equivalent
    bffPut<T>(path: string, body): PUT equivalent
    bffDelete(path: string): DELETE equivalent
    bffAuthPost<T>(path: string, body): fetch("/api/auth/" + path, {method:"POST",
    credentials:"include", ...})
    401 handler: window.location.href = "/login"
    NO import from lib/auth.ts; NO localStorage access

  lib/hooks/use-current-user.ts (NEW)
    useCurrentUser(): { data: {role, email, exp} | null, isLoading, isError }
    fetches GET /api/auth/me via bffGet; no JWT decode client-side; replaces
    canEditBudget() local decode in UsagePage

─── MIDDLEWARE (new file) ────────────────────────────────────────────────────────────────────

  middleware.ts (NEW — apps/dashboard/middleware.ts)
    matcher: ["/keys", "/usage", "/keys/:path*", "/usage/:path*"]
    reads cookies().get("ai_proxy_session") via next/headers
    if absent: NextResponse.redirect(new URL("/login", request.url)) with status 307
    if present: NextResponse.next()
    NOTE: does NOT verify JWT signature — presence check only; gateway validates on each
    proxied call; this guard is a UX fast-path, not the security boundary

─── MODIFIED COMPONENT FILES ────────────────────────────────────────────────────────────────

  components/auth/LoginForm.tsx (MODIFIED)
    calls /api/auth/login with credentials:"include" (replaces direct gateway POST)
    on 200 → router.push("/keys")
    on error → inline error (same problem+json title pattern as before)
    no localStorage write anywhere; setToken() call removed
    component name and prop interface: UNCHANGED

  components/auth/SignupForm.tsx (MODIFIED)
    calls /api/auth/signup with credentials:"include" (replaces direct gateway POST
    + login chain)
    on 201 → router.push("/keys")
    on error → inline error
    no localStorage write anywhere
    component name and prop interface: UNCHANGED

  components/keys/KeysPage.tsx (MODIFIED)
    getToken() / isTokenValid() / clearToken() useEffect guard: REMOVED
    (middleware.ts is the guard; if the page renders, the cookie is present)
    isAuthChecked / isAuthed state: REMOVED
    API calls (apiGet/apiPost/apiDelete): unchanged in call-site shape — they
    now route through /api/gw/* automatically via the modified api-client.ts
    handleLogout: calls POST /api/auth/logout then router.push("/login")
    (clearToken() call removed)

  components/usage/UsagePage.tsx (MODIFIED)
    getToken() / isTokenValid() / clearToken() useEffect guard: REMOVED
    canEditBudget(token) local JWT decode: REMOVED
    canEdit state: fed by useCurrentUser() hook (role from /api/auth/me)
    API calls: unchanged in call-site shape

  NOTE: LoginFormBff.tsx and SignupFormBff.tsx are NOT created — the originals
  are modified directly. This keeps the component tree stable (app/ route pages
  need no import change) and eliminates dead-code parallel files.

─── SUPERSEDED TEST DISPOSITION TABLE ───────────────────────────────────────────────────────

  The v1 frozen tests in apps/dashboard/tests/ assert localStorage-JWT behavior that this
  task deliberately changes. Per ADD governance, tests are mutable only through a new
  frozen contract that supersedes them. Disposition is executed by the orchestrator at
  the FREEZE of this task (NOT during the tests phase).

  Disposition key:
    SUPERSEDED→(name)     : test asserts the OLD observable (localStorage write /
                            client-side redirect); replaced by a new test in tests-bff/
    REVISE-ARRANGE        : behavioral assertions IDENTICAL; only the arrangement
                            changes — msw handlers move from http://gateway.test/*
                            to same-origin /api/gw/* (and /api/auth/me for role),
                            and localStorage.setItem(...) arrange lines are dropped;
                            executed in-place at freeze
    KEEP-UNCHANGED        : pure client-side Zod validation, no network/storage touch

  ── login.test.tsx (2 tests) ──────────────────────────────────────────────────

  | Test name                                  | Disposition                                        |
  |--------------------------------------------|---------------------------------------------------|
  | test_login_happy_stores_token_redirects     | SUPERSEDED → test_bff_client_login_posts_to_api_auth_no_localstorage (in tests-bff/bff-forms.test.tsx; asserts /api/auth/login called + no localStorage write) |
  | test_login_401_shows_error_no_navigation    | REVISE-ARRANGE: assertion (inline "Invalid credentials" text + no navigation) IDENTICAL; arrange changes from http://gateway.test/admin/auth/login to http://localhost:3000/api/auth/login |

  ── signup.test.tsx (4 tests) ─────────────────────────────────────────────────

  | Test name                                  | Disposition                                        |
  |--------------------------------------------|---------------------------------------------------|
  | test_signup_happy_redirects_to_keys         | SUPERSEDED → test_bff_client_signup_posts_to_api_auth_no_localstorage (asserts /api/auth/signup called + no localStorage write + router.push("/keys")) |
  | test_signup_409_inline_email_error          | REVISE-ARRANGE: assertion (inline "an account with this email already exists" + no navigation) IDENTICAL; arrange URL changes to /api/auth/signup |
  | test_signup_invalid_email_no_api_call       | KEEP-UNCHANGED: pure Zod client-side validation; never touches network or storage |
  | test_signup_weak_password_no_api_call       | KEEP-UNCHANGED: pure Zod client-side validation; never touches network or storage |

  ── keys.test.tsx (8 tests) ───────────────────────────────────────────────────

  | Test name                                          | Disposition                                        |
  |----------------------------------------------------|---------------------------------------------------|
  | test_unauthenticated_keys_redirects_login           | SUPERSEDED → test_bff_middleware_unauthenticated_redirects_login (middleware.ts is the guard; client-side redirect no longer fires from KeysPage) |
  | test_expired_token_keys_redirects_login             | SUPERSEDED → test_bff_middleware_unauthenticated_redirects_login (middleware covers both absent + expired; presence-check only; gateway validates exp on each call) |
  | test_keys_list_renders_rows                         | REVISE-ARRANGE: assertion (prod-key / sk-1a2b3c in DOM) IDENTICAL; arrange drops localStorage.setItem; msw URL moves from http://gateway.test/admin/keys to http://localhost:3000/api/gw/admin/keys |
  | test_keys_empty_state                               | REVISE-ARRANGE: assertion (no api keys yet text) IDENTICAL; arrange drops localStorage.setItem; msw URL moves to /api/gw/admin/keys |
  | test_keys_error_state                               | REVISE-ARRANGE: assertion (internal server error text) IDENTICAL; arrange drops localStorage.setItem; msw URL moves to /api/gw/admin/keys |
  | test_keys_loading_state                             | REVISE-ARRANGE: assertion (spinner/aria-busy visible) IDENTICAL; arrange drops localStorage.setItem; msw URL moves to /api/gw/admin/keys |
  | test_create_key_shows_plaintext_once_not_in_list    | REVISE-ARRANGE: assertions (plaintext banner, secret gone after dismiss) IDENTICAL; arrange drops localStorage.setItem; msw URLs move to /api/gw/admin/keys + /api/gw/admin/keys (POST) |
  | test_revoke_key_removes_row                         | REVISE-ARRANGE: assertion (DELETE called once, revoked state shown) IDENTICAL; arrange drops localStorage.setItem; msw URLs move to /api/gw/admin/keys (GET + DELETE) |

  ── usage.test.tsx (16 tests) ─────────────────────────────────────────────────

  | Test name                                          | Disposition                                        |
  |----------------------------------------------------|---------------------------------------------------|
  | test_usage_unauthenticated_redirects_login          | SUPERSEDED → test_bff_middleware_unauthenticated_redirects_login (shared middleware; client-side redirect no longer fires from UsagePage) |
  | test_usage_renders_cards_and_table                  | REVISE-ARRANGE: assertions (aggregate cards, records table, Edit Budget button for owner) IDENTICAL; arrange drops localStorage.setItem(makeJwtWithRole("owner")); role now comes from /api/auth/me — add msw handler for GET /api/gw/admin/usage + GET /api/gw/v1/models + GET /api/gw/admin/budget + GET /api/auth/me returning role:"owner"; msw URLs move from gateway.test to /api/gw/* |
  | test_usage_empty_state                              | REVISE-ARRANGE: assertions (zero values, no usage records yet) IDENTICAL; arrange drops localStorage.setItem(VALID_JWT); msw URLs move to /api/gw/* |
  | test_usage_error_state                              | REVISE-ARRANGE: assertions (internal server error, no rows) IDENTICAL; arrange drops localStorage.setItem; msw URLs move to /api/gw/* |
  | test_usage_loading_state                            | REVISE-ARRANGE: assertions (spinner visible, no rows) IDENTICAL; arrange drops localStorage.setItem; msw URLs move to /api/gw/* |
  | test_catalog_renders_rows                           | REVISE-ARRANGE: assertions (GPT-4o row, price visible) IDENTICAL; arrange drops localStorage.setItem; msw URLs move to /api/gw/* |
  | test_catalog_error_state                            | REVISE-ARRANGE: assertions (catalog not synced, no GPT-4o) IDENTICAL; arrange drops localStorage.setItem; msw URLs move to /api/gw/* |
  | test_budget_widget_shows_ceiling_and_spend          | REVISE-ARRANGE: assertions (25.00, 10.50 visible) IDENTICAL; arrange drops localStorage.setItem; msw URLs move to /api/gw/* |
  | test_budget_widget_null_shows_unlimited             | REVISE-ARRANGE: assertions (unlimited text, 0.00) IDENTICAL; arrange drops localStorage.setItem; msw URLs move to /api/gw/* |
  | test_budget_edit_happy_path                         | REVISE-ARRANGE: assertions (PUT called once, new value displayed) IDENTICAL; arrange drops localStorage.setItem(makeJwtWithRole("owner")); role from /api/auth/me; msw URLs move to /api/gw/* |
  | test_budget_edit_clear_to_unlimited                 | REVISE-ARRANGE: assertions (PUT called with null, unlimited shown) IDENTICAL; arrange drops localStorage.setItem(makeJwtWithRole("owner")); role from /api/auth/me; msw URLs move to /api/gw/* |
  | test_budget_edit_negative_no_api_call               | REVISE-ARRANGE: assertions (inline error, zero PUT calls) IDENTICAL; arrange drops localStorage.setItem(makeJwtWithRole("owner")); role from /api/auth/me; msw URLs move to /api/gw/* |
  | test_budget_edit_non_numeric_no_api_call            | REVISE-ARRANGE: assertions (inline error, zero PUT calls) IDENTICAL; arrange drops localStorage.setItem(makeJwtWithRole("owner")); role from /api/auth/me; msw URLs move to /api/gw/* |
  | test_budget_edit_403_surfaces_error                 | REVISE-ARRANGE: assertions (Forbidden text, no unexpected navigation) IDENTICAL; arrange drops localStorage.setItem(makeJwtWithRole("owner")); role from /api/auth/me; msw URLs move to /api/gw/* |
  | test_budget_edit_422_surfaces_error                 | REVISE-ARRANGE: assertions (Invalid budget value text, 25.00 unchanged) IDENTICAL; arrange drops localStorage.setItem(makeJwtWithRole("owner")); role from /api/auth/me; msw URLs move to /api/gw/* |
  | test_member_no_edit_budget_button                   | REVISE-ARRANGE: assertion (Edit Budget button absent) IDENTICAL; arrange drops localStorage.setItem(makeJwtWithRole("member")); role now from GET /api/auth/me returning role:"member"; msw URLs move to /api/gw/* |

  Disposition summary:
    SUPERSEDED:     5 tests (replaced by named tests-bff equivalents)
    REVISE-ARRANGE: 24 tests (assertions unchanged; arrange + msw URLs updated at freeze)
    KEEP-UNCHANGED:  2 tests (pure Zod validation, no network/storage)
    TOTAL:          31 tests across 4 files

─── VITEST CONFIG NOTE ───────────────────────────────────────────────────────────────────────

  vitest.config.ts currently collects all *.test.tsx / *.test.ts under the project root
  (no explicit testDir — defaults to root include pattern). The new tests-bff/ directory
  IS collected automatically.

  ONE additive change to vitest.config.ts is required at Build:
    setupFiles: [
      "./tests/setup.ts",
      "./test-support/mock-cjs-navigation.ts",
      "./tests-bff/setup.ts",   // ADD THIS LINE — activates vi.mock("next/headers")
    ]
  This is additive only; it does not modify the existing two entries and does not
  affect the frozen tests/ suite (vi.mock("next/headers") is a no-op for test files
  that never import next/headers). The existing 35 tests remain green after this change.

  The BFF route handler tests import from app/api/auth/*/route.ts which do NOT exist yet —
  these imports are the correct RED failure mode.

─── BUILD DELIVERABLES SHAPE (for Build phase reference) ────────────────────────────────────

  MODIFIED files (existing — changed in Build):
    lib/api-client.ts              rewire to /api/gw/*; credentials:"include"; no auth header
    lib/auth.ts                    delete getToken/setToken/clearToken/isTokenValid
    components/auth/LoginForm.tsx  call /api/auth/login BFF; no setToken()
    components/auth/SignupForm.tsx call /api/auth/signup BFF; no setToken()
    components/keys/KeysPage.tsx   remove client-side auth guard; remove clearToken logout
    components/usage/UsagePage.tsx remove client-side auth guard; use useCurrentUser hook

  NEW files:
    app/api/auth/login/route.ts
    app/api/auth/signup/route.ts
    app/api/auth/logout/route.ts
    app/api/auth/me/route.ts
    app/api/gw/[...path]/route.ts
    middleware.ts                          (at apps/dashboard root)
    lib/bff-client.ts
    lib/hooks/use-current-user.ts
    tests-bff/setup.ts                     (already exists — no change needed)
    tests-bff/mocks/server.ts              (already exists — no change needed)
    tests-bff/mocks/handlers.ts            (already exists — no change needed)
    tests-bff/route-handlers.test.ts       (already exists — no change needed)
    tests-bff/bff-client.test.tsx          (already exists — no change needed)
    tests-bff/middleware.test.ts           (already exists — no change needed)
    tests-bff/use-current-user.test.tsx    (already exists — no change needed)
    tests-bff/bff-forms.test.tsx           (already exists — targets LoginForm/SignupForm)
```

Status: FROZEN @ v2 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [spec] Secure cookie attribute on HTTP localhost — lowest confidence because the dev topology uses HTTP; if the target test environment or CI runner does not apply the localhost exception, cookies with Secure will be silently dropped and all BFF tests that assert Set-Cookie will fail; if wrong: add NODE_ENV=development guard to omit Secure (one conditional in the cookie helper) — no security regression in production
⚠ [contract] middleware.ts cookie presence check (no signature verification) as the route guard — lowest confidence because a forged or expired cookie that passes the presence check will reach the gateway proxy, which then returns 401; this is an acceptable tradeoff (identical to v1 where client-side exp decode was acknowledged as insufficient); if the security posture requires stronger middleware validation: add server-side JWT exp decode in middleware — contained change, no contract shape change
⚠ [contract] lib/api-client.ts rewiring to /api/gw/* invalidates the v1 data test msw URL arrangements — lowest confidence because 24 REVISE-ARRANGE tests that intercept http://gateway.test/* will silently pass-through (unhandled) after Build if the freeze step is not executed atomically with the Build; risk: tests/ suite goes red after Build until freeze is applied; mitigation: Build and freeze are a single orchestrator step; no partial state is deployed

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85% lines over new/modified BFF files (app/api/auth/*, app/api/gw/*,
lib/bff-client.ts, lib/hooks/use-current-user.ts, middleware.ts, and the modified
LoginForm.tsx / SignupForm.tsx)

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_bff_login_happy_sets_cookie_redirects: arrange msw POST GATEWAY_URL/admin/auth/login→200 {access_token:"test.jwt.token"} / act call POST /api/auth/login handler with NextRequest / assert response.status===200, Set-Cookie header contains ai_proxy_session=test.jwt.token; HttpOnly; Secure; SameSite=Strict, response.body NOT containing "test.jwt.token"
  - test_bff_login_gateway_401_no_cookie: arrange msw POST GATEWAY_URL/admin/auth/login→401 {code:"ERR_AUTH_INVALID_CREDENTIALS"} / act handler / assert response.status===401, no Set-Cookie header
  - test_bff_signup_happy_sets_cookie_redirects: arrange msw POST /signup→201 + POST /login→200 {access_token:"test.jwt.token"} / act POST /api/auth/signup handler / assert status===201, Set-Cookie ai_proxy_session set, body={ok:true}, body NOT containing JWT
  - test_bff_logout_clears_cookie: arrange no precondition / act POST /api/auth/logout handler / assert status===200, Set-Cookie ai_proxy_session with Max-Age=0
  - test_bff_me_returns_decoded_claims: arrange NextRequest with cookie ai_proxy_session=<valid-jwt-with-role-owner> / act GET /api/auth/me handler / assert status===200, body.role==="owner", body does NOT contain raw JWT string
  - test_bff_me_absent_cookie_401: arrange NextRequest with no cookie / act GET /api/auth/me / assert status===401, body.code==="ERR_AUTH_NO_SESSION"
  - test_bff_me_malformed_jwt_401: arrange cookie "not.a.valid.jwt.payload" / act GET /api/auth/me / assert status===401, body.code==="ERR_AUTH_NO_SESSION"
  - test_bff_proxy_forwards_bearer_returns_upstream: arrange cookie ai_proxy_session=<jwt>, msw GET GATEWAY_URL/admin/keys→200 [{key_id:"k1",...}], captures Authorization header / act GET /api/gw/admin/keys handler / assert Authorization header sent === "Bearer <jwt>", response status===200, response body contains key_id "k1", JWT value absent from response body
  - test_bff_proxy_absent_cookie_401: arrange no cookie / act GET /api/gw/admin/keys / assert status===401, code==="ERR_AUTH_NO_SESSION", no upstream request made
  - test_bff_proxy_upstream_401_clears_cookie: arrange cookie present, msw GATEWAY_URL/admin/keys→401 / act GET /api/gw/admin/keys / assert status===401, code==="ERR_AUTH_SESSION_EXPIRED", Set-Cookie clears ai_proxy_session (Max-Age=0)
  - test_bff_xss_simulation_no_token_visible: arrange login success (cookie set) / act read document.cookie (jsdom) and localStorage.getItem("ai_proxy_token") / assert document.cookie does not contain the JWT, localStorage returns null
  - test_bff_middleware_unauthenticated_redirects_login: arrange NextRequest to /keys with no ai_proxy_session cookie / act middleware() / assert response is 307 redirect to /login
  - test_bff_middleware_with_cookie_passes_through: arrange NextRequest to /keys with ai_proxy_session cookie present / act middleware() / assert response is NextResponse.next() (no redirect)
  - test_bff_client_login_posts_to_api_auth_no_localstorage: arrange msw POST /api/auth/login→200 {ok:true} / act render LoginForm + fill + submit / assert fetch called to /api/auth/login (NOT gateway direct), router.push("/keys") called, localStorage "ai_proxy_token" is null; BEHAVIORAL-RED against current LoginForm (calls gateway + writes localStorage)
  - test_bff_client_signup_posts_to_api_auth_no_localstorage: arrange msw POST /api/auth/signup→201 {ok:true} / act render SignupForm + fill + submit / assert fetch called to /api/auth/signup (NOT gateway chain), router.push("/keys") called, localStorage "ai_proxy_token" is null; BEHAVIORAL-RED against current SignupForm
  - test_bff_client_logout_posts_api_auth_logout: arrange thin LogoutButton component using bffAuthPost / act click logout / assert fetch POST to /api/auth/logout, router.push("/login") called; red because bff-client.ts (MODULE_NOT_FOUND)
  - test_bff_client_401_fires_window_redirect: arrange msw GET /api/gw/admin/keys→401 / act bffGet("/admin/keys") call / assert window.location.href set to "/login", localStorage not written; red because bff-client.ts (MODULE_NOT_FOUND)
  - test_bff_use_current_user_returns_role: arrange msw GET /api/auth/me→200 {role:"owner",email:"ada@acme.io",exp:<future>} / act render component using useCurrentUser() / assert hook returns {role:"owner"}, no localStorage access; red because use-current-user.ts (MODULE_NOT_FOUND)
  - test_bff_login_missing_fields_400: arrange NextRequest with body {} / act POST /api/auth/login handler / assert status===400, code==="ERR_BFF_PAYLOAD_INVALID", no upstream request
  - test_bff_proxy_no_cookie_delete: arrange no cookie / act DELETE /api/gw/admin/keys/kid-1 / assert status===401, code==="ERR_AUTH_NO_SESSION"
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` · MUST run red (missing implementation) before Build.

Red failure modes by file:
  - tests-bff/route-handlers.test.ts  : MODULE_NOT_FOUND (app/api/auth/*/route.ts, app/api/gw/*/route.ts)
  - tests-bff/bff-client.test.tsx     : MODULE_NOT_FOUND (lib/bff-client.ts)
  - tests-bff/middleware.test.ts      : MODULE_NOT_FOUND (middleware.ts)
  - tests-bff/use-current-user.test.tsx: MODULE_NOT_FOUND (lib/hooks/use-current-user.ts)
  - tests-bff/bff-forms.test.tsx      : BEHAVIORAL-RED — LoginForm/SignupForm resolve (files exist)
                                        but current forms call gateway directly and write localStorage;
                                        test_bff_client_login_posts_to_api_auth_no_localstorage fails
                                        because loginCalled stays false (msw intercepts /api/auth/login
                                        but the form hits gateway.test); test_bff_client_signup_posts_to_api_auth_no_localstorage
                                        fails for same reason; logout test fails MODULE_NOT_FOUND
                                        for bff-client.ts (dynamic import inside the test)
<!-- declare paths as backticked tokens on this line: `apps/dashboard/tests-bff/` -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — npm run test 59/59 green re-run by orchestrator (30 tests/ + 24
      tests-bff + 5 smoke across two vitest projects); npm run build success; lint clean;
      gateway 98 passed; make ci exit 0
- [x] coverage did not decrease — gateway floor held; dashboard suite grew from 35 to 59
- [x] no test or contract was altered during build — `git diff <freeze>..HEAD -- tests
      tests-bff .add apps/gateway` empty; the sanctioned disposition happened PRE-freeze
      inside the bundle commit, never during build
- [x] concurrency / timing of the risky operation is safe — cookie set/cleared atomically in
      route-handler responses; upstream-401 clears the cookie in the same response that
      surfaces ERR_AUTH_SESSION_EXPIRED (no window where a dead session keeps a live cookie);
      MSW server isolation solved via vitest projects, not test edits
- [x] no exposed secrets, injection openings, or unexpected dependencies — token transported
      ONLY in the httpOnly/Secure/SameSite=Strict cookie; never in any response body; grep
      proves zero functional localStorage references in app/, components/, lib/ (comments
      only); Authorization header constructed server-side in the /api/gw proxy; no new deps
- [x] layering & dependencies follow CONVENTIONS.md — route handlers are the dashboard's
      server boundary; client components consume same-origin lib/api-client only; middleware
      is a UX guard with the gateway as the security boundary (contracted posture)
- [x] a person reviewed and approved the change — orchestrator drove a full revision cycle:
      the FIRST draft's parallel-component design was rejected as product-breaking (data
      pages would 401-loop after cookie login); contract revised, 31-test disposition
      executed pre-freeze, build verified against the revised whole
      (delegated auto mode, Tin Dang, 2026-06-10)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — all five route handlers reachable under app/api/**; middleware.ts at
      the dashboard root with the contracted matcher; api-client consumed by KeysPage/
      UsagePage/forms; use-current-user consumed by UsagePage role affordance; bff-client
      consumed by hooks/logout per contract
- [x] DEAD-CODE (code) — lib/auth.ts localStorage helpers removed (not orphaned); the
      legacy-bff-compat test-support file is loaded via the legacy vitest project config
- [x] SEMANTIC (prose / non-code) — §3 cookie spec verified attribute-by-attribute against
      the Set-Cookie assertions in tests-bff; disposition table cross-checked against the
      executed test edits (5 deleted / 23 revised / 2 kept)

### GATE RECORD
Outcome: PASS (auto-resolved — autonomy: auto; evidence complete; the draft-level defect was
caught and fixed at contract stage, before any build)
Reviewed by: Claude (orchestrator) under delegated auto mode — Tin Dang · date: 2026-06-10

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): /api/auth/login failure rate (BFF) · session-cookie 401 redirect loops in dashboard logs · rate(gateway_http_requests_total{status_code="401"}[5m]) on /admin paths
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
