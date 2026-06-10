# TASK: Next.js app: signup/login, key management

slug: dashboard-shell · created: 2026-06-10 · stage: mvp
phase: tests   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Dashboard shell — browser-side tenant signup, JWT login, and API key management
Framings weighed: pure-SPA with localStorage JWT (chosen for MVP — zero BFF complexity, matches stated decision) · httpOnly-cookie BFF (rejected for MVP — adds a BFF route layer and CORS complexity; flagged as the production path in §3) · separate auth service / OAuth delegation (rejected — out of scope, v1 is self-serve password auth)
Must:
<must>
  - /signup page renders a form (tenant_name, email, password); on submit POSTs {tenant_name, email, password} to NEXT_PUBLIC_GATEWAY_URL/admin/auth/signup; on 201 auto-logs in by POSTing the same credentials to /admin/auth/login, stores the returned JWT in localStorage under key "ai_proxy_token", then redirects to /keys
  - /login page renders a form (email, password); on submit POSTs {email, password} to /admin/auth/login; on 200 stores the JWT in localStorage under key "ai_proxy_token" and redirects to /keys
  - All authenticated fetch requests include header Authorization: Bearer <token> read from localStorage
  - /keys page (authenticated): on mount calls GET /admin/keys with Bearer token; renders the key list with columns key_id (truncated), name, prefix, created_at, revoked_at (or "active"); shows a "Create key" dialog (key name input → POST /admin/keys); on 201 shows the full plaintext key exactly once in a highlighted banner with a copy-to-clipboard button and the warning "You won't see this key again" — the key does NOT appear in the list subsequently; shows a "Revoke" button per active key (confirm dialog → DELETE /admin/keys/{key_id} → 204 → removes row from the list)
  - Route guard: any navigation to /keys (or any future authenticated route) when localStorage "ai_proxy_token" is absent or the token is expired (decode exp claim client-side) redirects to /login immediately, before rendering
  - Logout: clears localStorage "ai_proxy_token" and redirects to /login
  - Every screen handles four UI states: loading (skeleton/spinner), empty (no keys yet copy), error (displays the problem+json `title` field from the API response), success (normal content)
  - API errors surface the problem+json `title` field to the user — never raw status codes or opaque messages
</must>
Reject:
<reject>
  - signup with email that fails RFC 5322 format → inline field error, no API call (client-side Zod: z.string().email())
  - signup with password shorter than 10 chars → inline field error, no API call (client-side Zod: z.string().min(10))
  - signup with tenant_name empty or longer than 120 chars → inline field error, no API call (client-side Zod: z.string().min(1).max(120))
  - signup with key name empty or longer than 120 chars → inline field error, no API call (client-side Zod: z.string().min(1).max(120)); gateway allows 200 chars — dashboard uses stricter 120 to keep UI comfortable
  - 409 from signup API (ERR_TENANT_EMAIL_TAKEN) → inline error on the email field: "An account with this email already exists"
  - 401 from any authenticated API call (ERR_AUTH_INVALID_TOKEN, token expiry) → clears localStorage token and redirects to /login
  - create key 422 (ERR_PAYLOAD_INVALID) → inline error on the key name field in the dialog
</reject>
After:
<after>
  - After signup: one Tenant + owner User exist in the gateway DB (created by the gateway); the owner holds a valid JWT in localStorage and lands on /keys
  - After login: a valid JWT is in localStorage and the user is on /keys
  - After create key: the plaintext key was shown once in the UI and is now gone; the key appears in the list without its secret; the gateway has a non-revoked api_keys row
  - After revoke: the key row shows a non-null revoked_at and is visually marked as revoked; the gateway row has revoked_at set
  - After logout: localStorage is clear; user is on /login; /keys is inaccessible without re-login
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ JWT stored in localStorage (XSS risk) — lowest confidence because this is the stated MVP decision, but it is a known security tradeoff: any injected script can read the token. This is flagged here as the top risk; the production path is an httpOnly-cookie BFF (e.g., a Next.js route handler that sets the cookie and proxies authenticated requests). If wrong or if security posture changes: replace the fetch wrapper + localStorage calls with a BFF /api/auth route handler and remove all client-side token reads — the UI contract (forms, redirects, key display) is unchanged.
  ⚠ Client-side token expiry check decodes the JWT (base64-split, no signature verification) — lowest confidence because a tampered exp field could bypass the guard; full verification requires the HMAC secret in the browser (worse). If wrong: rely solely on the 401→redirect path for expiry enforcement — marginally weaker UX (user reaches /keys then immediately bounces) but no security regression since the gateway always validates.
  - [x] NEXT_PUBLIC_GATEWAY_URL defaults to http://localhost:8080 (Envoy) in .env.local; injected at build time
  - [x] shadcn/ui components are copy-in (no registry network fetch needed in CI); exact component file list declared in §3
  - [x] TanStack Query v5 used for server state (keys list); React Hook Form + Zod for form validation
  - [x] Dark-mode-first; Tailwind v4; WCAG 2.2 AA minimum contrast; no Tremor in this task (usage charts are dashboard-usage task)
  - [x] CI "dashboard" job extends .github/workflows/ci.yml (Build task deliverable, not spec task)
  - [x] The dashboard is a pure client app for the MVP (no Next.js Server Actions, no server-side data fetching beyond the placeholder layout) — all data fetching is client-side TanStack Query with the JWT fetched from localStorage
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: signup happy path — redirects to /keys
  Given the gateway responds 201 to POST /admin/auth/signup
  And the gateway responds 200 to POST /admin/auth/login with a valid JWT
  When the user fills tenant_name "Acme", email "ada@acme.io", password "hunter12345" and submits
  Then the JWT is stored in localStorage under "ai_proxy_token"
  And the browser navigates to /keys

Scenario: signup 409 — inline email error, no navigation
  Given the gateway responds 409 { code: "ERR_TENANT_EMAIL_TAKEN", title: "Email already registered" }
  When the user submits the signup form
  Then an inline error message appears on the email field
  And the browser does NOT navigate away from /signup

Scenario: signup client-side validation — invalid email, no API call
  Given the signup form is rendered
  When the user submits with email "not-an-email" and a valid password and tenant_name
  Then an inline field error appears on the email field
  And no HTTP request is made to the gateway

Scenario: signup client-side validation — weak password, no API call
  Given the signup form is rendered
  When the user submits with a valid email, tenant_name, and a 9-character password
  Then an inline field error appears on the password field
  And no HTTP request is made to the gateway

Scenario: login happy path — stores token and redirects to /keys
  Given the gateway responds 200 { access_token: "<jwt>", token_type: "bearer", expires_in: 86400 }
  When the user fills email "ada@acme.io", password "hunter12345" and submits the login form
  Then localStorage "ai_proxy_token" equals the JWT from the response
  And the browser navigates to /keys

Scenario: login 401 — shows error message, no navigation
  Given the gateway responds 401 { code: "ERR_AUTH_INVALID_CREDENTIALS", title: "Invalid credentials" }
  When the user submits the login form
  Then an error message displaying "Invalid credentials" appears on the page
  And the browser does NOT navigate away from /login
  And localStorage "ai_proxy_token" is not set

Scenario: keys list renders rows from GET /admin/keys
  Given the user is authenticated (valid JWT in localStorage)
  And the gateway responds 200 [ { key_id: "...", name: "prod-key", prefix: "sk-1a2b3c", created_at: "...", revoked_at: null } ]
  When /keys mounts
  Then the page renders a row showing "prod-key" and prefix "sk-1a2b3c"
  And no "key" or "secret" field value appears in the rendered output

Scenario: keys list empty state
  Given the user is authenticated
  And the gateway responds 200 []
  When /keys mounts
  Then the page renders an empty-state message (e.g. "No API keys yet")
  And no key rows are shown

Scenario: keys list error state
  Given the user is authenticated
  And the gateway responds 500 { title: "Internal server error", status: 500 }
  When /keys mounts
  Then the page renders the error title "Internal server error"
  And no key rows are shown

Scenario: create key — shows plaintext once, not in list afterwards
  Given the user is authenticated and on /keys
  And GET /admin/keys initially returns []
  And POST /admin/keys responds 201 { key_id: "...", name: "ci-key", key: "sk-abc123.SECRETVALUE" }
  And GET /admin/keys (after create) returns [ { key_id: "...", name: "ci-key", prefix: "sk-abc123", revoked_at: null } ]
  When the user opens the "Create key" dialog, enters name "ci-key", and submits
  Then the plaintext key "sk-abc123.SECRETVALUE" is displayed in a one-time banner
  And the banner includes a copy button
  And after the banner is dismissed the list renders "ci-key" WITHOUT "SECRETVALUE" visible anywhere

Scenario: revoke key — removes from active list
  Given the user is authenticated and /keys shows one active key with key_id "kid-1"
  And DELETE /admin/keys/kid-1 responds 204
  And GET /admin/keys (after revoke) returns [ { key_id: "kid-1", ..., revoked_at: "2026-06-10T00:00:00Z" } ]
  When the user clicks "Revoke" on "kid-1" and confirms
  Then the key row is updated to show revoked_at as non-null (visually marked revoked)
  And DELETE /admin/keys/kid-1 was called exactly once

Scenario: unauthenticated access to /keys redirects to /login
  Given localStorage "ai_proxy_token" is absent
  When the user navigates to /keys
  Then the browser is redirected to /login
  And the /keys content is never rendered

Scenario: expired token on /keys redirects to /login
  Given localStorage "ai_proxy_token" contains a JWT whose exp claim is in the past
  When the user navigates to /keys
  Then the browser is redirected to /login

Scenario: keys list loading state
  Given the user is authenticated
  And the gateway GET /admin/keys is pending (not yet resolved)
  When /keys mounts
  Then a loading indicator (skeleton or spinner) is visible
  And no key rows and no error message are shown
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
UI consumes (does NOT own) these gateway contracts — frozen in dependent tasks:

POST NEXT_PUBLIC_GATEWAY_URL/admin/auth/signup
  body: { tenant_name: str(1..120), email: EmailStr, password: str(≥10) }
  201 -> { tenant_id: uuid, user_id: uuid }
  409 -> problem+json { type: "about:blank", title: str, status: 409, code: "ERR_TENANT_EMAIL_TAKEN" }
  400 -> problem+json { type: "about:blank", title: str, status: 400, code: "ERR_AUTH_PASSWORD_WEAK" }
  422 -> problem+json { type: "about:blank", title: str, status: 422, code: "ERR_PAYLOAD_INVALID" }

POST NEXT_PUBLIC_GATEWAY_URL/admin/auth/login
  body: { email: EmailStr, password: str }
  200 -> { access_token: str(JWT), token_type: "bearer", expires_in: 86400 }
  401 -> problem+json { type: "about:blank", title: str, status: 401, code: "ERR_AUTH_INVALID_CREDENTIALS" }
  422 -> problem+json { type: "about:blank", title: str, status: 422, code: "ERR_PAYLOAD_INVALID" }

GET NEXT_PUBLIC_GATEWAY_URL/admin/keys   header: Authorization: Bearer <jwt>
  200 -> [ { key_id: uuid, name: str, prefix: str, created_at: datetime, revoked_at: datetime|null } ]
  401 -> problem+json { type: "about:blank", title: str, status: 401, code: "ERR_AUTH_INVALID_TOKEN" }

POST NEXT_PUBLIC_GATEWAY_URL/admin/keys  header: Authorization: Bearer <jwt>
  body: { name: str(1..120) }
  201 -> { key_id: uuid, name: str, key: "sk-<key_id_hex>.<urlsafe_b64_secret>" }
  401 -> problem+json { type: "about:blank", title: str, status: 401, code: "ERR_AUTH_INVALID_TOKEN" }
  422 -> problem+json { type: "about:blank", title: str, status: 422, code: "ERR_PAYLOAD_INVALID" }

DELETE NEXT_PUBLIC_GATEWAY_URL/admin/keys/{key_id}  header: Authorization: Bearer <jwt>
  204 -> (no body)
  401 -> problem+json { type: "about:blank", title: str, status: 401, code: "ERR_AUTH_INVALID_TOKEN" }
  404 -> problem+json { type: "about:blank", title: str, status: 404, code: "ERR_KEY_NOT_FOUND" }

Client-side validation (Zod schemas — mirrors of gateway rules, stricter on key name):
  SignupSchema: { tenant_name: z.string().min(1).max(120), email: z.string().email(), password: z.string().min(10) }
  LoginSchema:  { email: z.string().email(), password: z.string().min(1) }
  CreateKeySchema: { name: z.string().min(1).max(120) }

Token storage:
  localStorage key: "ai_proxy_token"
  value: raw JWT string
  Expiry guard: decode base64url(payload) → check exp < Date.now()/1000 → redirect /login
  ⚠ XSS risk acknowledged — httpOnly-cookie BFF is the production upgrade path

Component tree (content-free placeholders in spec phase; real implementation in Build):
  app/layout.tsx            — root layout, QueryClientProvider, fonts
  app/page.tsx              — redirects to /login
  app/(auth)/signup/page.tsx
  app/(auth)/login/page.tsx
  app/(dashboard)/keys/page.tsx
  components/auth/SignupForm.tsx
  components/auth/LoginForm.tsx
  components/keys/KeysPage.tsx
  components/keys/CreateKeyDialog.tsx
  components/keys/KeyRow.tsx
  components/keys/PlaintextKeyBanner.tsx
  lib/api-client.ts         — fetch wrapper + Authorization header + 401→redirect
  lib/auth.ts               — localStorage helpers + token decode + expiry check
  lib/query-client.ts       — TanStack Query client singleton

CI job (Build deliverable — spec only here):
  .github/workflows/ci.yml  dashboard job:
    runs-on: ubuntu-latest
    steps: pnpm install → pnpm vitest run → pnpm build
    coverage threshold: 80% lines on implemented components
```

Status: DRAFT
Least-sure flag surfaced at freeze:
⚠ [spec] localStorage JWT storage — lowest confidence because it is the explicit MVP decision yet carries a known XSS risk; if the security posture is raised before Build: replace with an httpOnly-cookie BFF route handler; all form/navigation contracts remain unchanged; only lib/auth.ts and lib/api-client.ts change.
⚠ [contract] client-side exp-only token decode (no signature check) — lowest confidence because a tampered JWT with a future exp bypasses the client guard; if wrong: enforce the guard purely via 401-response redirect and remove client-side decode — marginally worse UX on expiry but no security regression (gateway always validates).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% lines (measured over implemented component files; enforced in Build CI job)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_signup_happy_redirects_to_keys: arrange msw handlers POST /admin/auth/signup→201 + POST /admin/auth/login→200 JWT / act render SignupForm + fill + submit / assert localStorage "ai_proxy_token" set + router.push called with "/keys"
  - test_signup_409_inline_email_error: arrange msw POST /admin/auth/signup→409 ERR_TENANT_EMAIL_TAKEN / act render SignupForm + submit / assert email field error message visible + no navigation
  - test_signup_invalid_email_no_api_call: arrange SignupForm rendered, msw intercept spy / act submit with "not-an-email" / assert inline email error visible + zero fetch calls to gateway
  - test_signup_weak_password_no_api_call: arrange SignupForm rendered / act submit with 9-char password / assert inline password error visible + zero fetch calls to gateway
  - test_login_happy_stores_token_redirects: arrange msw POST /admin/auth/login→200 {access_token:"test.jwt.here"} / act render LoginForm + fill + submit / assert localStorage["ai_proxy_token"] === "test.jwt.here" + router.push "/keys"
  - test_login_401_shows_error_no_navigation: arrange msw POST /admin/auth/login→401 {title:"Invalid credentials"} / act render LoginForm + submit / assert text "Invalid credentials" in document + no navigation + localStorage not set
  - test_keys_list_renders_rows: arrange localStorage JWT set + msw GET /admin/keys→200 [{key_id,name:"prod-key",prefix:"sk-1a2b3c",created_at,revoked_at:null}] / act render KeysPage / assert text "prod-key" and "sk-1a2b3c" visible + no raw secret visible
  - test_keys_empty_state: arrange localStorage JWT set + msw GET /admin/keys→200 [] / act render KeysPage / assert empty-state text present + no key rows
  - test_keys_error_state: arrange localhost JWT set + msw GET /admin/keys→500 {title:"Internal server error"} / act render KeysPage / assert "Internal server error" visible
  - test_keys_loading_state: arrange localStorage JWT set + msw GET /admin/keys deferred (never resolves during test window) / act render KeysPage immediately / assert loading indicator visible + no rows + no error
  - test_create_key_shows_plaintext_once_not_in_list: arrange localStorage JWT set + msw GET /admin/keys→[] then POST /admin/keys→201 {key:"sk-abc123.SECRETVALUE"} then GET /admin/keys→[{name:"ci-key",prefix:"sk-abc123",revoked_at:null}] / act render KeysPage + open CreateKeyDialog + enter "ci-key" + submit / assert "sk-abc123.SECRETVALUE" visible in banner + copy button present / act dismiss banner / assert "SECRETVALUE" NOT in document
  - test_revoke_key_removes_row: arrange localStorage JWT set + msw GET /admin/keys→[{key_id:"kid-1",name:"k1",revoked_at:null}] + DELETE /admin/keys/kid-1→204 + GET /admin/keys→[{key_id:"kid-1",revoked_at:"2026-06-10T00:00:00Z"}] / act render KeysPage + click Revoke on "kid-1" + confirm / assert DELETE called once + row now shows revoked_at non-null state
  - test_unauthenticated_keys_redirects_login: arrange localStorage "ai_proxy_token" absent / act render KeysPage (or navigate to /keys) / assert redirect to /login + /keys content not rendered
  - test_expired_token_keys_redirects_login: arrange localStorage "ai_proxy_token" = JWT with exp = Math.floor(Date.now()/1000) - 60 / act render KeysPage / assert redirect to /login
</test_plan>

Tests live in: `apps/dashboard/tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `apps/dashboard/tests/` -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): plaintext key material MUST be cleared from component state when the one-time banner is dismissed; the JWT MUST NOT be written to sessionStorage, cookies, or any log; 401 responses from the gateway MUST always clear the localStorage token and redirect to /login before any component re-render.
Code lives in: `apps/dashboard/` (app/, components/, lib/)
Constraints: do NOT change any test or the contract; allow-list packages only (node deps exempted per ADD delta §7); ask if unclear.

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

Watch (reuse scenarios as monitors): /login 401 rate (credential stuffing) · key creation rate per tenant · plaintext key copy event (analytics) · client-side JS error rate (Sentry or Vercel Analytics)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · open] node dependencies are not governed by dependencies.allowlist (Python gate only) — delta: document node dep governance separately or extend the allowlist format; evidence: §3 contract note "Python dependencies.allowlist does NOT govern node deps".
- [UDD · open] localStorage JWT XSS risk must be surfaced in the spec (not hidden in code) — evidence: §1 ⚠ assumption drives the freeze flag; production path (httpOnly-cookie BFF) documented in §3 contract.
