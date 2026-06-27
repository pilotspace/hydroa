# TASK: Browser-driven dashboard UI e2e through the Envoy edge against the live kind cluster (login + a real authenticated surface)

slug: e2e-ui · created: 2026-06-27 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): a NEW browser-driven (Playwright) e2e that drives the dashboard UI through the LIVE Envoy edge against the running kind cluster — the visual analog of task-8's API e2e. NO dashboard `src/` change; reuse the shipped login + keys surfaces.
  - **A · PLAYWRIGHT ALREADY PRESENT** — `apps/dashboard/package.json` deps `@playwright/test ^1.60.0` + `@axe-core/playwright`; `apps/dashboard/playwright.config.ts` + `apps/dashboard/e2e-a11y/a11y.spec.ts` (the v17 a11y harness). **BUT that harness is NOT reusable for a live-edge test: it builds+serves a LOCAL prod Next server on 127.0.0.1:3100 and MOCKS the gateway — `seedSession()` adds a FAKE `ai_proxy_session=e30.e30.fakesig` cookie + `page.route("**/api/auth/me"|"**/api/gw/**")` fulfills fixtures (a11y.spec.ts:78-89). A live test needs the OPPOSITE: the in-cluster dashboard through the edge with a REAL session + REAL gateway. → §1 FORK: a NEW `playwright.kind.config.ts` (testDir `e2e-kind/`, `baseURL` = kind edge `https://127.0.0.1:8443`, `ignoreHTTPSErrors:true`, NO `webServer` — the dashboard runs in-cluster, headless) + a `make kind-e2e-ui` runner. Chromium is a ~92MiB download (`npx playwright install chromium`, NOT committed) — same constraint the a11y harness documents.**
  - **B · LOGIN FORM** — `app/(auth)/login/page.tsx` → `components/auth/LoginForm.tsx`: inputs `#login_email` (email) + `#login_password` (password), submit `<button>"Log in"` inside `<form aria-label="Log in">`; on submit `POST /api/auth/login {email,password}` → on 200 `router.push("/app/keys")` (LoginForm.tsx:155-176). Client zod: email valid + password min 1.
  - **C · SIGNUP FORM + BFF** — `components/auth/SignupForm.tsx`: `#tenant_name` + `#signup_email` + `#signup_password` (zod **password min 10**), submit "Sign up" → `POST /api/auth/signup {tenant_name,email,password}`; the BFF route does TWO gateway calls (`/admin/auth/signup` then `/admin/auth/login`) → 201 + sets the session cookie → `router.push("/app/keys")`. VERIFIED LIVE through the edge: `POST https://127.0.0.1:8443/api/auth/signup` → 201 + `set-cookie: ai_proxy_session=<JWT>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400` (JWT role=owner). Password "hunter2hunter" (13) satisfies the min-10.
  - **D · SESSION + GUARD** — cookie `ai_proxy_session` (HttpOnly, **Secure** since the kind dashboard runs NODE_ENV=production — fine over the edge's TLS; Playwright `ignoreHTTPSErrors` accepts the self-signed cert). `apps/dashboard/proxy.ts:30-43` (the renamed middleware) guards matcher `["/app","/app/:path*"]` — presence-only regex `/ai_proxy_session=/` on the Cookie header; absent → **307 redirect to `/login`**. VERIFIED LIVE: `GET https://127.0.0.1:8443/app/keys` with no cookie → 307. Real JWT validation happens at the gateway on each `/api/gw/**` BFF call.
  - **E · AUTHED SURFACE TO ASSERT** — `/app/keys` → `components/keys/KeysPage.tsx` (loads `GET /api/gw/admin/keys` → BFF → gateway `/admin/keys`). Stable selectors: `getByRole("heading",{level:1,name:/API Keys/i})` (KeysPage.tsx:184); fresh-tenant empty state `getByText("No API keys yet")` (:247); `getByRole("navigation")` (AppShell nav); `getByRole("button",{name:"Log out"})` (:192). A fresh signup → 0 keys → the empty-state is the deterministic assertion.
  - **F · EDGE ROUTING** — the Envoy catch-all `/` route → `dashboard_cluster` (task-4 split; `dashboard.enabled:true` default, VERIFIED `ai-proxy-dashboard` 2/2 Ready). So browser `/login`, `/app/**`, `/api/auth/**`, `/api/gw/**` ALL go to the dashboard pod via the edge; the dashboard BFF reaches the gateway server-to-server (GATEWAY_URL=http://ai-proxy-gateway:8000). `GET https://127.0.0.1:8443/login` → 200 VERIFIED. (Only `/v1/`,`/v1/realtime/`,`/admin/`,`/internal/` go to the gateway directly.)
Context (working folder): `.add/milestones/v53/MILESTONE.md` task line 37 (browser-driven UI e2e through the edge: load → log in → exercise a real authenticated surface e.g. a key/usage view) + exit criterion line 49. Sibling `e2e-platform-features` (task 8, committed 502e19e) + `e2e-core-flow` (task 7) = the API-side analog; this is the BROWSER analog. The kind cluster is UP + chart-reconciled.
Honors (patterns / conventions): E2E-THROUGH-THE-EDGE (drive the Envoy NodePort over TLS, real login → real gateway → real session) · ZERO-CLOUD-CREDS (the dashboard + gateway are in-cluster; no provider key needed to view keys) · REAL-WIRE-NOT-MOCKED (unlike the a11y harness — NO page.route, a real `ai_proxy_session`) · DESIGN-FOR-FAILURE (bounded waits via Playwright timeouts, unique tenant per run, idempotent) · ISOLATED-FROM-THE-FLOOR (a separate Playwright config + a kind marker; never gates `npm test` / `make test-fast`).
Anchors the contract cites: the kind edge `https://127.0.0.1:8443` + `ignoreHTTPSErrors` · the login form selectors (`#login_email`/`#login_password`/"Log in") + the signup BFF `POST /api/auth/signup` (the fresh-tenant seed) · the `ai_proxy_session` cookie + the `proxy.ts` /app→/login guard · the `/app/keys` surface selectors (`/API Keys/i` heading, "No API keys yet" empty state, navigation) · the new `playwright.kind.config.ts` + `e2e-kind/` testDir + `make kind-e2e-ui` runner.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: e2e-ui — a browser-driven (Playwright) e2e that proves the dashboard UI works through the REAL Envoy edge against the live kind cluster: a real LOGIN through the edge lands on a real authenticated surface that rendered real gateway data.
Framings weighed: login = API-SEED SIGNUP → UI LOGIN (chosen, Tin 2026-06-27 AskUserQuestion — Playwright's `request` fixture POSTs `/api/auth/signup` through the edge to mint a fresh tenant, then the BROWSER drives the real login form; the login form is the star, matching the exit criterion "log into the dashboard UI", and mirrors task-8's API-setup-then-assert) · full-UI-signup→logout→login (rejected — most UI but couples the login test to 3 forms, slower/brittler) · UI-signup→auto-login (rejected — never exercises the dedicated login form). surface = /app/keys EMPTY-STATE (chosen, Tin — a fresh tenant has 0 keys, so "No API keys yet" is a stable un-flaky assertion proving the BFF→gateway `/admin/keys` round-trip rendered real empty data) · /app/usage (rejected — fresh-tenant charts are zeroed, less crisp) · both (rejected — broader but slower/flakier).
Must:
<must>
  - M1 — REAL LOGIN THROUGH THE EDGE: with a fresh tenant API-seeded (`POST https://127.0.0.1:8443/api/auth/signup {tenant_name,email,password}` → 201, via Playwright `request`, `ignoreHTTPSErrors`), the BROWSER navigates to `https://127.0.0.1:8443/login`, fills `#login_email` + `#login_password` with the seeded creds, and clicks "Log in"; the dashboard BFF authenticates against the gateway (`/admin/auth/login`), sets the `ai_proxy_session` cookie, and the browser lands on `/app/keys`. Proves the real login form → BFF → gateway → session works end-to-end through the TLS edge.
  - M2 — AUTHED SURFACE RENDERS REAL DATA: on `/app/keys` the page shows the `/API Keys/i` level-1 heading, the fresh-tenant empty state `No API keys yet`, and the navigation shell — proving the authenticated `GET /api/gw/admin/keys` (BFF → gateway `/admin/keys`, Bearer from the session cookie) round-tripped and rendered the real (empty) key list for the logged-in tenant.
  - M3 — REPRODUCIBLE + ISOLATED FROM THE FLOOR: new spec(s) live in a NEW `apps/dashboard/e2e-kind/` testDir driven by a NEW `playwright.kind.config.ts` (`baseURL` = `KIND_EDGE_URL` default `https://127.0.0.1:8443`, `ignoreHTTPSErrors:true`, NO `webServer`, headless, retries 0); a `make kind-e2e-ui` runner (ensures Chromium + the cluster, then `playwright test --config playwright.kind.config.ts`). Unique tenant per run (idempotent, bounded by Playwright timeouts). The default `npm test` (vitest) + `make test-fast` are UNCHANGED — the kind config/dir is separate and never auto-collected.
</must>
Reject:
<reject>
  - R1 — GUARD REDIRECTS THE UNAUTHENTICATED BROWSER: navigating the browser directly to `https://127.0.0.1:8443/app/keys` with NO session cookie → the dashboard guard (`proxy.ts`) redirects to `/login` (the browser lands on `/login`; the keys surface never renders). Proves the UI auth guard holds at the edge -> redirect `/login`.
  - R2 — LOGIN FORM REJECTS A BAD PASSWORD: with the tenant seeded, filling the login form with the seeded email but a WRONG password and submitting → the dashboard does NOT navigate to `/app/keys` (stays on `/login`) and surfaces an auth error (the gateway 401 → BFF → form `role="alert"`). Proves a real auth failure is surfaced, not swallowed -> stay on `/login`.
</reject>
After:
<after>
  - The kind state holds: the seeded tenant exists with 0 keys; the browser run creates NO keys/usage rows; the cluster stays Ready. `npm test` (vitest) and `make test-fast` are unchanged (the `e2e-kind/` dir + `playwright.kind.config.ts` are separate from the a11y harness and the vitest floor).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The `Secure; SameSite=Strict` `ai_proxy_session` cookie the dashboard BFF sets over the edge's SELF-SIGNED TLS is STORED by Playwright's Chromium and re-sent on the subsequent `/app/keys` navigation — LOWEST confidence because the whole authed flow hinges on that cookie round-tripping on an `ignoreHTTPSErrors` https origin; a Secure cookie IS honored over https (even self-signed) so this should hold, but if Chromium drops it the login "succeeds" (form posts 200) yet the `/app/keys` nav bounces back to `/login` (guard sees no cookie). If wrong: M1/M2 fail at the redirect. Mitigation: the live run observes the post-login URL is `/app/keys` (not `/login`); if it bounces, capture the Set-Cookie and seed it into the context (or use `storageState`) + record a delta.
  - [ ] Chromium is installable/runnable in the env (`npx playwright install chromium`, ~92MiB, not committed) — the a11y harness already assumes it; CI (task 10) caches it. If absent the run ERRORS clearly (never a false green).
  - [ ] the `/app/keys` fresh-tenant empty-state copy is exactly `No API keys yet` (KeysPage.tsx:247) and the heading matches `/API Keys/i` (KeysPage.tsx:184) — confirm on the live run.
  - [ ] a wrong-password login surfaces as stay-on-`/login` + a visible error (not a crash / not a silent redirect) — confirm R2 live.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: a real login through the edge lands on the keys surface (M1)
  Given the kind stack is Ready and a fresh tenant has been API-seeded via POST https://127.0.0.1:8443/api/auth/signup (201)
  When the browser opens https://127.0.0.1:8443/login, fills #login_email + #login_password with the seeded creds, and clicks "Log in"
  Then the dashboard sets the ai_proxy_session cookie and the browser navigates to /app/keys (not back to /login)
  And the request went through the Envoy TLS edge to the in-cluster dashboard (not a local server)

Scenario: the authenticated keys surface renders real (empty) gateway data (M2)
  Given the browser is logged in as the fresh tenant and on /app/keys
  When the KeysPage loads GET /api/gw/admin/keys (BFF → gateway /admin/keys with the session bearer)
  Then the page shows the /API Keys/i heading, the empty state "No API keys yet", and the navigation shell
  And the empty state proves the round-trip returned the real (zero-key) list for that tenant

Scenario: the UI e2e is isolated from the floor (M3)
  Given the new e2e-kind/ specs run under playwright.kind.config.ts (baseURL the edge, ignoreHTTPSErrors, no webServer)
  When `npm test` (vitest) and `make test-fast` run with no cluster
  Then neither collects the e2e-kind specs and both pass unchanged
  And `make kind-e2e-ui` (cluster up + Chromium present) runs the live specs

Scenario: the guard redirects the unauthenticated browser (R1)
  Given the kind edge is up and the browser has NO session cookie
  When the browser navigates directly to https://127.0.0.1:8443/app/keys
  Then the dashboard guard redirects it to /login (the browser lands on /login)
  And the keys surface never renders (no "API Keys" heading is shown)

Scenario: the login form rejects a bad password (R2)
  Given the tenant is seeded and the browser is on /login
  When it fills #login_email with the seeded email but #login_password with a WRONG password and clicks "Log in"
  Then the browser stays on /login (does NOT navigate to /app/keys) and a visible auth error is shown
  And no session is established (the keys surface is not reachable)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This task ships NO dashboard/gateway src — it freezes the OBSERVABLE shape of browser-driven e2e
specs that drive the SHIPPED dashboard UI through the live kind edge, plus a new Playwright harness.

```
CONSTANTS (frozen)
  KIND_EDGE_URL = "https://127.0.0.1:8443"     # env-overridable; self-signed → ignoreHTTPSErrors:true
  LOGIN_PATH    = "/login"
  KEYS_PATH     = "/app/keys"
  SIGNUP_API    = "/api/auth/signup"            # body { tenant_name, email, password } → 201 + Set-Cookie ai_proxy_session
  PASSWORD      = "hunter2hunter"               # ≥10 chars (signup zod min-10); unique email/tenant per run
  EMAIL_DOMAIN  = "kind.example"                # v2: the email MUST satisfy BOTH the gateway AND the
                                                #   dashboard's client zod `.email()`. zod 3.25 REJECTS a
                                                #   digit-bearing TLD like `kind.e2e` ("Invalid email
                                                #   address") so the login form never submits — use a
                                                #   letters-only TLD. email = "ui-<sfx>@kind.example".
  SESSION_COOKIE = "ai_proxy_session"

HARNESS (frozen entrypoints — NEW, separate from the a11y harness + the vitest floor)
  apps/dashboard/playwright.kind.config.ts     # testDir ./e2e-kind, baseURL KIND_EDGE_URL (env KIND_EDGE_URL),
                                               #   use.ignoreHTTPSErrors:true, headless, retries:0, NO webServer,
                                               #   projects:[chromium]; timeouts bounded (design-for-failure)
  apps/dashboard/e2e-kind/ui_flow.spec.ts      # the live specs (M1, M2, R1, R2; M3 = config/dir isolation)
  make kind-e2e-ui → scripts/e2e_kind_ui.sh    # ensure cluster Ready + Chromium installed, then
                                               #   `npx playwright test --config playwright.kind.config.ts`

SEED (Playwright `request`, ignoreHTTPSErrors) — mint a fresh tenant out-of-band:
  POST {KIND_EDGE_URL}/api/auth/signup  { tenant_name:"E2eUI-<sfx>", email:"ui-<sfx>@"+EMAIL_DOMAIN, password:PASSWORD }
     -> 201   (creates tenant + owner; the spec then drives the browser login, NOT this cookie)

A · LOGIN THROUGH THE EDGE (M1) — drive the real form:
  page.goto(KIND_EDGE_URL + LOGIN_PATH)         -> 200, the login form renders
  fill #login_email = seeded email; fill #login_password = PASSWORD
  click button "Log in"
     -> browser navigates to KEYS_PATH (waitForURL "**/app/keys"); the ai_proxy_session cookie is set
     (NOT a bounce back to /login)

B · AUTHED SURFACE RENDERS REAL DATA (M2) — on KEYS_PATH:
  getByRole("heading",{ level:1, name:/API Keys/i })   visible
  getByText("No API keys yet")                          visible   (fresh tenant → 0 keys, real empty list)
  getByRole("navigation")                               visible   (the AppShell nav = authed shell)

R1 · GUARD REDIRECTS UNAUTHENTICATED — fresh context, no cookie:
  page.goto(KIND_EDGE_URL + KEYS_PATH)  -> browser lands on LOGIN_PATH (guard redirect); NO "API Keys" heading

R2 · LOGIN FORM REJECTS BAD PASSWORD — seeded email + WRONG password:
  fill #login_email = seeded email; fill #login_password = "wrong-" + PASSWORD; click "Log in"
     -> browser STAYS on LOGIN_PATH (no nav to /app/keys) AND a visible auth error appears (role="alert")

M3 · ISOLATION — `npm test` (vitest) + `make test-fast` do NOT collect e2e-kind/ (separate config + dir);
  the a11y harness (playwright.config.ts, e2e-a11y/) is untouched.

Schema touched: NONE in code — the seed creates ONE tenant + owner via the public signup BFF (read-side
  thereafter). NO migration, NO dashboard/gateway src, NO chart change.
```

Status: FROZEN @ v2 — re-frozen 2026-06-27 (v2 change-request, in-flight per ADD: the live red run CAUGHT a wrong fixture assumption the v1 freeze missed — the seed email `ui-<sfx>@kind.e2e` is accepted by the GATEWAY but REJECTED by the dashboard's client-side zod `.email()` [zod 3.25 rejects a digit-bearing TLD], so the login form never submitted [client alert "Invalid email address"] → M1/M2 timed out AND R2 passed for the WRONG reason [it saw the client-validation alert, not the gateway 401]. Fix: EMAIL_DOMAIN = "kind.example" [letters-only TLD], which satisfies BOTH the gateway [signup 201, bad-pw 401 VERIFIED] and zod. Behaviour shape unchanged; only the seed email domain. No new Tin decision needed — same forks). v1 freeze (forks: login=API-seed-signup→UI-login · surface=/app/keys empty-state) approved by Tin 2026-06-27.
Least-sure flag surfaced at freeze: [contract] RESOLVED at v2 — the v1 lowest-confidence flag (the `Secure;SameSite=Strict` cookie round-trip over self-signed TLS) HELD: the live curl repro proved login→200 sets the cookie and `/app/keys` WITH the cookie → 200 (no bounce), and R1/R2 ran green; the M1/M2 failure was the zod-email fixture bug above, NOT the cookie. Remaining secondary [test]: Chromium must be `playwright install`-ed — absent → the run ERRORS loudly (never a false green); the `e2e_kind_ui.sh` runner installs it.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: n/a — OUT-OF-PROCESS browser e2e against the live cluster (runs under `make kind-e2e-ui` via `playwright.kind.config.ts`, NOT vitest). The dashboard vitest coverage floor + `make test-fast` are unaffected (the `e2e-kind/` dir is collected only by the new kind config).
Plan (one spec per scenario, asserting observable browser behaviour not internals) — `apps/dashboard/e2e-kind/ui_flow.spec.ts`:
<test_plan>
  - test "real login through the edge lands on keys" (M1): arrange `request.post(SIGNUP_API,{tenant_name,email,password})`→201 / act `page.goto(LOGIN_PATH)`, fill `#login_email`+`#login_password`, click "Log in" / assert `await page.waitForURL("**"+KEYS_PATH)` (lands on /app/keys, not bounced to /login).
  - test "keys surface renders real empty data" (M2): arrange the logged-in page on KEYS_PATH (chain from M1 or re-login) / act await the page load / assert `getByRole("heading",{level:1,name:/API Keys/i})` + `getByText("No API keys yet")` + `getByRole("navigation")` all visible.
  - test "guard redirects the unauthenticated browser" (R1): arrange a fresh context with NO cookie / act `page.goto(KEYS_PATH)` / assert the browser lands on LOGIN_PATH (`await expect(page).toHaveURL(/\/login/)`) AND no `/API Keys/i` heading is visible.
  - test "login form rejects a bad password" (R2): arrange a seeded tenant on LOGIN_PATH / act fill seeded email + `"wrong-"+PASSWORD`, click "Log in" / assert the browser STAYS on LOGIN_PATH (no nav to KEYS_PATH within a bounded wait) AND a `role="alert"` error is visible.
  RED reason (harness-grounded): the specs are written FIRST; running `npx playwright test --config playwright.kind.config.ts` is RED because that config + the `make kind-e2e-ui`/`scripts/e2e_kind_ui.sh` runner do NOT exist yet (no testDir wiring to the edge) — the §5 BUILD adds the Playwright kind config + runner that point the specs at `https://127.0.0.1:8443` and make them green against the live cluster. (The UI itself is already shipped + deployed — the "implementation" this task builds is the live-edge harness, mirroring task-8 where the green came from the envoy/values wiring, not new app code.)
  M3 (isolation) is asserted structurally (the kind config's testDir is `e2e-kind/`, separate from the a11y `playwright.config.ts` + the vitest floor) + a `npm test`/`--collect` check, not a live browser test (mirrors task-7/8's M4).
</test_plan>

Tests live in: `apps/dashboard/e2e-kind/` · MUST run red (the kind Playwright config + runner not yet wired) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/e2e-kind/` `apps/dashboard/playwright.kind.config.ts` `apps/dashboard/package.json` `scripts/e2e_kind_ui.sh` `Makefile`   <!-- e2e-kind/ = the spec(s); playwright.kind.config.ts = the live-edge harness; package.json = a `test:kind` script (devDeps already have @playwright/test); e2e_kind_ui.sh + Makefile `kind-e2e-ui` target = the runner. NO dashboard/gateway src, NO chart. -->
Strategy (ordered batches): 1. `playwright.kind.config.ts` (testDir ./e2e-kind, baseURL env KIND_EDGE_URL default https://127.0.0.1:8443, use.ignoreHTTPSErrors:true, headless, retries:0, NO webServer, projects:[chromium], bounded timeouts) 2. `package.json` add `"test:kind": "playwright test --config playwright.kind.config.ts"` 3. `scripts/e2e_kind_ui.sh` (ensure cluster Ready via `make kind-up` unless --no-up; ensure Chromium via `npx playwright install chromium`; then `npm run test:kind`) + `Makefile` `kind-e2e-ui:` target 4. install Chromium + run live; if the post-login cookie doesn't round-trip (the freeze flag), capture Set-Cookie → context/storageState + record a delta.
Safety rule (feature-specific): unique tenant per run (idempotent vs the live DB); bounded Playwright timeouts (no unbounded wait); the run mutates only its own seeded tenant (creates no keys/usage); `npm test` (vitest) + `make test-fast` stay green + collect NO e2e-kind specs (separate config/dir).
Code lives in: `apps/dashboard/` (e2e-kind specs + the kind Playwright config) + `scripts/` + `Makefile` — a TEST/harness task, NO `src/` change.
Constraints: do NOT change any test or the contract; @playwright/test is ALREADY a dashboard devDep (no new dep); Chromium is a runtime download, not a committed artifact; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `make kind-e2e-ui` → 3 passed live against the real edge; dashboard vitest floor 688 passed (unchanged); eslint 0 errors.
- [x] coverage did not decrease — e2e-kind runs under Playwright (not vitest); the dashboard vitest coverage floor is untouched (e2e-kind not collected — `vitest list` shows 0 e2e-kind specs).
- [x] no test or contract was altered during build — §3 re-frozen @ v2 via a proper change-request (the email-domain fixture fix); the build added only the harness; no existing test weakened. The spec's R2 was STRENGTHENED (asserts the client "Invalid email address" alert is absent) — the opposite of weakening.
- [x] the green was EARNED, not gamed — independent adversarial refute-read (general-purpose subagent, 0.97) = CONCERNS/no-HARD-STOP: Q1 genuinely through the edge (no webServer/no page.route, unlike the a11y sibling) · Q2 real form login (no cookie shortcut) · Q3 non-vacuous (M2 empty-state only renders on the authed page; R1 two-sided; R2 server-error-not-client) · Q4 self-contained/bounded · Q5 vitest-isolated. The one "FAIL" it flagged (spec files untracked) is just pre-commit state — resolved by this task's commit. One NIT (R2 doesn't assert exact alert text) → [SPEC] delta (kept loose to avoid coupling to gateway error copy).
- [x] concurrency / timing safe — unique tenant per run (idempotent vs the live DB); fresh browser context per test; all waits are bounded Playwright web-first assertions (waitForURL/toHaveURL/toBeVisible, 15s expect timeout); no sleeps; workers:1.
- [x] no exposed secrets, injection openings, or unexpected dependencies — only FAKE test creds (`hunter2hunter`, `@kind.example`); `@playwright/test` is ALREADY a dashboard devDep (no new dep — the dep-allowlist vitest test stayed green); Chromium is a runtime download, not committed.
- [x] layering & dependencies follow CONVENTIONS.md — no `apps/dashboard/src`/gateway/chart change; the new harness is parallel to the existing a11y harness; the kind config/dir is separate from the vitest floor.
- [x] a person reviewed and approved the change — autonomy:auto AUTO-PASS (no security surface, no concurrency/architecture residue, autonomy not lowered → ADD auto-gate applies); Tin is informed in the wrap-up and can review. Independent refute-read stood in for the adversarial check.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] LIVE green: `make kind-e2e-ui` → `3 passed (3.5s)` against the real Envoy edge — Playwright run summary.
- [x] Real login through the edge (M1): the browser submitted the login form on `https://127.0.0.1:8443/login` and `waitForURL("**/app/keys")` resolved — the post-login nav reached `/app/keys` (NOT bounced). The v1 freeze flag (Secure-cookie round-trip over self-signed TLS) HELD — the live failure was the zod-email fixture (fixed @ v2), proven by a curl repro (login→200+cookie, `/app/keys` WITH cookie→200).
- [x] Authed surface rendered real data (M2): `/app/keys` showed the `/API Keys/i` heading + `No API keys yet` empty state + the nav — the assertions passed against the live in-cluster dashboard (BFF→gateway `/admin/keys` returned the real empty list; the empty-state only renders on the authed page, not an error/logged-out page).
- [x] Guard holds (R1): a no-cookie `goto /app/keys` landed on `/login` AND the keys heading had count 0 (two-sided) — R1 green.
- [x] Bad password rejected (R2): a wrong-password submit stayed on `/login` + showed a `role="alert"` server error AND the client `Invalid email address` alert was absent — R2 green (strengthened to prove the SERVER rejection, not client validation).
- [x] Isolated from the floor (M3): `vitest list` collects 0 e2e-kind specs (vitest include = `tests/`·`test-support/`·`tests-bff/` with `.test.`, not `e2e-kind/*.spec.ts`); the a11y `playwright.config.ts` (testDir `e2e-a11y`) is untouched; `npm test` 688 passed.
- [x] No dashboard/gateway src or chart touched: `git diff --stat` = `package.json` (+2/-1 script) + `Makefile` (target) + new `e2e-kind/ui_flow.spec.ts` + `playwright.kind.config.ts` + `scripts/e2e_kind_ui.sh`; NO `apps/dashboard` (or gateway) `src`/component change, no chart.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — the kind config's `testDir:"./e2e-kind"` + `baseURL` env wire the spec to the edge; the `test:kind` script + `e2e_kind_ui.sh` + the `kind-e2e-ui` make target invoke it; the live run executed all 3 specs (proof they're wired). The spec's selectors (`#login_email`/`#login_password`/"Log in", `/API Keys/i`, "No API keys yet") are confirmed real in LoginForm.tsx + KeysPage.tsx + states.tsx (refute-read Q2/Q3).
- [x] DEAD-CODE — every helper (`unique`, `seedTenant`, `submitLogin`, `EMAIL_DOMAIN`) is used by a test; no orphan.
- [x] SEMANTIC — read in full: the frozen §3 (v2), the spec, the kind config, and the a11y sibling (to confirm the new harness does NOT inherit its mocks). Confirmed: real edge, real login, non-vacuous asserts, vitest-isolated. Independent refute-read concurred (0.97).

### GATE RECORD
Outcome: PASS — autonomy:auto auto-gate (no security surface, no concurrency/architecture residue,
  autonomy not lowered → the ADD auto-PASS path applies). Evidence complete: `make kind-e2e-ui` 3
  passed live; dashboard vitest 688 passed (e2e-kind not collected); eslint 0; diff is test-harness
  only (no src/chart). Independent adversarial refute-read = CONCERNS/no-HARD-STOP (0.97) — green
  EARNED (real edge, real form login, non-vacuous asserts, vitest-isolated); its only "FAIL" was the
  pre-commit untracked state, resolved by this task's commit. v2 change-request (email-domain fixture)
  handled via a proper contract re-freeze. NO security finding.
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: auto-gate (autonomy:auto) + independent refute-read · date: 2026-06-27 · Tin informed in the wrap-up

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): login→/app/keys success rate (M1) · /app/keys empty-state render (M2) · guard-redirect rate for unauthenticated /app (R1) · login auth-error rate (R2).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] task 10 (ci-e2e-pipeline) must cache/install the Playwright Chromium browser in the runner (`npx playwright install --with-deps chromium`) and run BOTH `make kind-e2e` (API) + `make kind-e2e-ui` (browser) — the UI e2e needs a headed-capable Chromium that the API e2e does not (evidence: this task's harness is a separate config + needs the browser download).
- [SPEC · open] tighten R2 to assert the login alert's TEXT matches an auth-failure message (e.g. /invalid|credential|unauthorized/i) once the gateway's 401 problem+json `title` is stable — kept loose now to avoid coupling to server error copy (evidence: refute-read NIT, Q3).
- [SPEC · open] consider a tiny stable `data-testid` on the login global-error alert + the keys empty-state so the e2e selectors don't rely on copy/role heuristics (evidence: the v2 zod-email bug surfaced as a generic "alert" the test couldn't initially distinguish from the server error).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] a server-accepted fixture value can still fail a CLIENT validator — `@kind.e2e` passed the gateway signup but the dashboard's zod `.email()` rejected the digit-bearing TLD, so the form never submitted; pick e2e fixtures that satisfy EVERY layer they traverse (browser zod + BFF + gateway), not just the backend (evidence: the v2 red caught it live; M1/M2 timed out + R2 false-passed on the client alert). [folded foundation-version 39]
- [TDD · folded] a reject-case assertion (`alert visible` + `stay on URL`) can PASS for the WRONG reason when two different failures produce the same surface — R2 was strengthened to assert the client validation alert is ABSENT, pinning it to the SERVER rejection (evidence: R2 green even while M1 failed → the alert was client-side, not the gateway 401). [folded foundation-version 39]
- [ADD · folded] driving the REAL UI through the edge catches browser-layer contract gaps (client validation, cookie flags, guard redirects) that an API-only e2e (task 8) and a mocked a11y harness both miss — the third such live-catch in v53 after t7 (enc-key) and t8 (relay edge-auth) (evidence: the zod-email gap was invisible to the gateway-side curl that returned 201). [folded foundation-version 39]
