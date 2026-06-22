# TASK: Sign in with SSO entry on the login page

slug: sso-login-button · created: 2026-06-22 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

**RE-SCOPE FINDING (ground):** the "Sign in with SSO" button ALREADY EXISTS (`LoginForm.tsx:139`, `<Button asChild variant="outline"><a href="/api/auth/oidc/login">`) and the whole OIDC chain is wired (BFF login+callback relays, per-tenant DB config, admin OidcSettings). The real GAP = no domain/email field, so the button can't pass `?domain=` → per-tenant OIDC config (resolved by email domain) can't be triggered from /login; only a single env-level OIDC works. The milestone exit criterion ("a tenant with SSO configured logs in from /login without a manual URL") needs the domain field. So this task = **add a domain input that drives the existing SSO button's `?domain=`**, not "add a button".

Touches (files · symbols · signatures):
- `apps/dashboard/components/auth/LoginForm.tsx:LoginForm` — `"use client"`; Zod `LoginSchema` validate → `fetch("/api/auth/login")`; line 139 has the existing SSO `<Button asChild variant="outline">` → `/api/auth/oidc/login` (full-page nav, NO domain). THE file to change — add a controlled `domain` field + append `?domain=` to the SSO href (or a submit handler).
- `apps/dashboard/app/(auth)/login/page.tsx:LoginPage` — Server Component; reads `searchParams.sso_error` → `<ErrorState>`; renders `<AuthShell>` + `<LoginForm/>`. (the SSO-error surface already exists)
- `apps/dashboard/app/api/auth/oidc/login/route.ts:GET` — BFF relay; forwards ONLY `?domain` to `GATEWAY_URL/auth/oidc/login`, relays 3xx+cookies verbatim. The contract the UI must feed (`?domain=<email_domain>`).
- `apps/gateway/src/gateway/auth/api/oidc_router.py:oidc_login` — `GET /auth/oidc/login?domain=` → resolves per-tenant config (by email domain) → 302 to IdP, or 404 ERR_OIDC_NOT_CONFIGURED. (backend already done — NOT touched)

Context (working folder):
- Stack: `apps/dashboard` Next.js 16.2.9 App Router, shadcn/ui (Radix+CVA+Tailwind 4), tests vitest (`npm test`=`vitest run`; suites `tests/` legacy + `tests-bff/`; 80% line coverage over components/lib).
- `apps/dashboard/components/ui` — `Button` (variants incl. `outline`, `asChild` via Radix Slot), `Input`, `Card`, `ErrorState`/states.tsx. Existing SSO error path: `/login?sso_error=` → page → ErrorState.
- No new BFF/route/backend work — relay + resolver + callback all exist (verified at ground).

Honors (patterns / conventions):
- a11y: explicit `<label htmlFor>` per input (not aria-label); field errors `<p role="alert" aria-live="polite">`; form `aria-label`+`noValidate`; brand panel `aria-hidden`.
- data-slot: auth card `data-slot="auth-card"` (test/structural selector).
- forms: client Zod validate before navigation; `isSubmitting` disables the control.
- test gate: new behavior needs a vitest test (testing-library + user-event); the npm-test-only gate (see [[ui-restyle-recipe]]).
- UDD: a UI change → run the design-definition loop at specify (render the /login layout with the domain field, human-confirm before build).

Anchors the contract cites: `LoginForm` (the domain field + SSO href/submit) · the `?domain=` param passed to `/api/auth/oidc/login` · the existing `/login?sso_error=` error surface · `LoginSchema` (domain validation) · the a11y label/role pattern.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a work-email/domain field on /login that drives the EXISTING "Sign in with SSO" button's `?domain=`, so a tenant with per-tenant OIDC configured can start SSO from /login without a hand-typed URL. (UDD design-confirm: the layout was confirmed by Tin via the approved preview 2026-06-22 — email/password → "or" → domain field + SSO button.)

Framings weighed: **separate domain/email input beside the SSO button** (chosen — explicit, matches the approved layout, decoupled from password login) · reuse the top email field to derive the domain on SSO-click (rejected — couples password-login email to SSO intent, ambiguous UX) · a tenant-slug field (rejected — the backend resolves by email DOMAIN, not slug)

Must:
<must>
  - A labeled work-email/domain input is present on /login; clicking "Sign in with SSO" with a value navigates to `/api/auth/oidc/login?domain=<resolved-domain>` (the existing BFF relay).
  - The input accepts EITHER a full email (derive the domain after `@`) OR a bare domain; the resolved domain is what's sent.
  - When the field is EMPTY, the SSO button still navigates to `/api/auth/oidc/login` with NO `?domain=` — the existing env-level single-tenant SSO must not regress.
  - Client-side validation (Zod) before navigation; a11y per convention (explicit `<label htmlFor>`, error `role="alert" aria-live="polite"`).
  - Password login and the existing `/login?sso_error=` surface are UNCHANGED.
</must>
Reject:
<reject>
  - A non-empty but malformed value (no domain extractable, e.g. "abc" with no dot, or "@") -> inline field error (role="alert"), NO navigation -> "ERR_SSO_DOMAIN_INVALID" (client-side; surfaced as the field message, not an HTTP code)
  - (downstream, NOT this task's code) an unconfigured domain -> backend 404 ERR_OIDC_NOT_CONFIGURED -> existing `/login?sso_error=` surface
</reject>
After:
<after>
  - A tenant user types their work email/domain and starts per-tenant SSO from /login; the empty-field path still triggers env-level SSO; nothing else on /login changed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Accepting an EMAIL and extracting the domain (type `alice@acme.com` → send `acme.com`) is the expected UX, vs requiring a bare domain. Lowest confidence because user habit varies; if wrong: users type the wrong thing → backend 404. Mitigation: accept BOTH (contains `@` → take the part after the last `@`; else use the trimmed value as-is), then validate the result looks like a domain.
  - [ ] empty-field SSO keeps the no-`?domain=` fallback (do not regress env OIDC). — held by a Must + a test.
  - [ ] domain validation stays LENIENT (basic shape: has a dot, no spaces); the backend is the authority (404 if unconfigured). Over-strict regex risks rejecting valid IdP domains.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: SSO with a work email
  Given the /login page
  When the user types "alice@acme.com" in the SSO domain field and clicks "Sign in with SSO"
  Then the browser navigates to /api/auth/oidc/login?domain=acme.com

Scenario: SSO with a bare domain
  Given the /login page
  When the user types "acme.com" and clicks "Sign in with SSO"
  Then the browser navigates to /api/auth/oidc/login?domain=acme.com

Scenario: SSO with an empty field keeps the env fallback
  Given the /login page with the SSO domain field empty
  When the user clicks "Sign in with SSO"
  Then the browser navigates to /api/auth/oidc/login with NO domain param
  And the existing env-level SSO behavior is unchanged

Scenario: malformed domain blocks navigation
  Given the /login page
  When the user types "notadomain" (no dot) and clicks "Sign in with SSO"
  Then an inline field error (role="alert") is shown
  And the browser does NOT navigate   # no SSO start on invalid input

Scenario: password login is unaffected
  Given the /login page
  When the user submits email + password
  Then the existing /api/auth/login flow runs unchanged
  And the SSO domain field does not interfere
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
UI contract — LoginForm (apps/dashboard/components/auth/LoginForm.tsx), client component.

Adds a controlled "work email or domain" field + drives the EXISTING SSO button:
  <label htmlFor="sso_domain">Work email or domain</label>
  <Input id="sso_domain" name="sso_domain" autoComplete="email" />
  field error: <p role="alert" aria-live="polite"> on invalid (id wired via aria-describedby)
  <Button variant="outline"> "Sign in with SSO" — onClick handler (no longer a bare <a>)

Behavior (what the SSO control does on click):
  value present, valid   -> navigate to  /api/auth/oidc/login?domain=<resolved>
                            resolved = value.includes("@") ? value.split("@").at(-1).trim().toLowerCase()
                                                           : value.trim().toLowerCase()
  value empty            -> navigate to  /api/auth/oidc/login         (no ?domain= — env fallback)
  value present, invalid -> NO navigation; show field error (validateSsoDomain → message)
  navigation = window.location.assign(url) (full-page, mirrors today's <a href> behavior)

validateSsoDomain(raw): derive domain (above) then require /^[^\s@]+\.[^\s@]+$/ (has a dot, no
  spaces/@). Lenient by design — the gateway is the authority (404 ERR_OIDC_NOT_CONFIGURED →
  existing /login?sso_error= surface). Lives beside LoginSchema (Zod or a small pure helper).

Unchanged: password login (fetch /api/auth/login), the SSO-error surface, AuthShell, data-slot.
No HTTP contract change, no BFF/route/backend change (the ?domain= relay already exists).
```

Status: FROZEN @ v1 — approved by Tin (2026-06-22). Add a work-email/domain field driving the existing SSO button's `?domain=`; accept email or bare domain; empty→env fallback; lenient validation; no backend change. Changing this = change request back to SPECIFY.

Least-sure flag surfaced at freeze: [spec] the EMAIL→domain extraction UX (accept `alice@acme.com` and send `acme.com`, AND accept a bare `acme.com`) is the point most likely to mismatch user/Tin expectation — why: habit varies (some products want the full email, some a domain); cost if wrong: users mistype → backend 404 (recoverable via the sso_error surface, no security impact). Mitigation: accept BOTH forms + lenient validation; the empty-field env fallback is preserved so nothing regresses.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (project vitest threshold over components/**)
Plan (one test per scenario; assert behavior — the navigation URL + the error, not internals):
<test_plan>
  - test_sso_with_email_sends_domain: type "alice@acme.com" / click "Sign in with SSO" / assert window.location.assign("/api/auth/oidc/login?domain=acme.com")
  - test_sso_with_bare_domain: type "acme.com" / click SSO / assert assign(".../login?domain=acme.com")
  - test_sso_empty_keeps_env_fallback: leave field empty / click SSO / assert assign("/api/auth/oidc/login") (no ?domain=)
  - test_sso_malformed_blocks_navigation: type "notadomain" / click SSO / assert role=alert error shown AND assign NOT called
  - test_password_login_unaffected: with the SSO field present, submit email+password / assert POST /api/auth/login still runs (existing flow), SSO field does not interfere
</test_plan>

Tests live in: `apps/dashboard/tests/sso-login.test.tsx` · MUST run red (missing field/handler) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/auth/LoginForm.tsx`
Strategy (ordered batches): 1. add controlled `ssoDomain` state + a labeled "Work email or domain" Input (a11y label/error per convention). 2. add `resolveSsoDomain`/`validateSsoDomain` (inline pure helpers: split on `@`, lowercase/trim; require a dot, no spaces/@). 3. convert the SSO `<Button asChild><a>` to `<Button type="button" onClick>` that: empty→assign("/api/auth/oidc/login"); valid→assign("...?domain="+encodeURIComponent(domain)); invalid→preventDefault + setError. 4. keep password submit path byte-unchanged.
Safety rule (feature-specific): do NOT regress the empty-field env fallback (no ?domain=) and do NOT alter the password-login fetch; the SSO button MUST be type="button" so it never submits the login form.
Code lives in: `apps/dashboard/components/auth/LoginForm.tsx`
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

- [x] all tests pass — 5/5 new SSO tests; FULL dashboard suite 361 passed (46 files, both legacy+bff projects)
- [x] coverage did not decrease — new component logic (resolve/validate/handleSso) is exercised by the 5 scenarios; 80% threshold held
- [x] no test or contract was altered to GAME the build — the contract is untouched. Pre-existing tests WERE updated (login.test.tsx, bff-forms.test.tsx, auth-pages-redesign.test.tsx, oidc-login-relay.test.tsx) for two legit reasons: (a) the new "Work email or domain" label made `/email/i`/`/log in|sign in/i` selectors AMBIGUOUS → tightened to `/^email$/`/`/^log in$/` (more precise, NOT weaker); (b) the v24 "SSO is an <a>" assertions are SUPERSEDED by this task's frozen contract (SSO is now a button) → updated to the new design. Each annotated; re-crossed tests→build to re-baseline.
- [x] the green was EARNED — focused refute self-review: tests redefine window.location with a real mock `assign` that the component genuinely calls; asserts are substantive (exact `?domain=` URL, no-call on malformed, role=alert shown, password POST still fires); logic handles edges (`ALICE@ACME.COM`→`acme.com`, whitespace→empty→env fallback, `notadomain`→no dot→blocked). No overfit, no vacuous asserts, no stubbed logic.
- [x] concurrency / timing — N/A; synchronous click handler + full-page navigation (window.location.assign), no async race
- [x] no exposed secrets / injection — `encodeURIComponent(domain)` guards the URL; domain validated (no spaces/@); no secrets; no new dependency (reuses existing Button/Input/zod)
- [x] layering follows CONVENTIONS.md — a11y label (`<label htmlFor="sso_domain">`) + error `role="alert" aria-live="polite"` + `aria-describedby`; SSO button is `type="button"` (never submits the form); password path byte-unchanged; data-slot untouched
- [x] a person reviewed — AUTO-RESOLVED under autonomy:auto (low-risk UI change, 1 src file, no security/concurrency/architecture residue); UDD design-confirm satisfied by Tin's approved layout preview 2026-06-22

### Build expectations — what "correct" looks like
- [x] typing a work email + clicking SSO navigates to `/api/auth/oidc/login?domain=<domain-after-@>` — confirmed by test_sso_with_email_sends_domain (asserts assign URL)
- [x] empty field + SSO click navigates to `/api/auth/oidc/login` with NO `?domain=` (env fallback intact) — confirmed by test_sso_empty_keeps_env_fallback
- [x] malformed input shows an inline role=alert error and does NOT navigate — confirmed by test_sso_malformed_blocks_navigation
- [x] password login still POSTs /api/auth/login and is unaffected by the SSO field — confirmed by test_password_login_unaffected + the full existing login/bff suites green

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `handleSso` wired to the SSO `<Button onClick>`; `resolveSsoDomain`/`validateSsoDomain` called in `handleSso`; `ssoDomain`/`ssoError` state wired to the new Input + error `<p>`. All exercised by the green suite.
- [x] DEAD-CODE (code) — `resolveSsoDomain`/`validateSsoDomain` are exported pure helpers used by handleSso (and reusable/testable); no orphans (eslint clean, tsc clean)
- [x] SEMANTIC (behavior) — confirmed the empty-field env fallback is preserved and the password-login fetch path is byte-unchanged (only additive state + the SSO control swap)

### GATE RECORD
Outcome: PASS   (auto-resolved under autonomy:auto — low-risk UI, no security/residue)
If RISK-ACCEPTED -> owner: — · ticket: — · expires: —   (N/A)
Reviewed by: auto-resolved (autonomy:auto) · date: 2026-06-22

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

Watch (reuse scenarios as monitors): rate of SSO-domain validation errors (high = users confused by the field); rate of backend 404 ERR_OIDC_NOT_CONFIGURED after a domain submit (high = wrong-domain typos or unconfigured tenants → maybe surface a clearer hint).

### Spec delta
- [SPEC · open] persist the last-used SSO domain (localStorage) so returning users skip retyping — evidence: repeat-login UX.
- [SPEC · open] richer sso_error messaging on the /login page when the backend 404s an unconfigured domain (today: generic ErrorState) — evidence: the domain field makes wrong-domain a likely path.
- [SPEC · open] the 5 remaining v31 UI tasks (alerts-events-viewer, catalog-sync-trigger, upstream-health-view, ratelimit-counter-view, routing-config-write).

### Competency deltas
- [TDD · open] jsdom's `window.location.assign` is NON-configurable → `vi.spyOn` throws "Cannot redefine property"; redefine `window.location` WHOLESALE (save original, `Object.defineProperty(window,"location",{configurable,writable,value:{...orig,assign:vi.fn()}})`, restore in afterEach) — reusable harness pattern for any full-page-nav component test (evidence: this task's sso-login.test.tsx).
- [UDD · open] for a SMALL UI change, an AskUserQuestion `preview` (ASCII layout) served as the design-confirm — no full render-loop needed; the human picked the layout before build (evidence: Tin approved the /login layout preview 2026-06-22).
- [SDD · open] a "add X" task where X already EXISTS → ground re-scopes to the real adjacent gap (here: the SSO button existed; the gap was the domain field) BEFORE building the wrong thing — surface the re-scope to the human at ground/specify (evidence: this task's §0 RE-SCOPE FINDING).
- [TDD · open] adding a UI control with an overlapping accessible name/label silently makes SIBLING tests' loose selectors (`/email/i`, `/log in|sign in/i`) ambiguous → sweep ALL suites for the loose pattern, tighten to anchored regex (`/^email$/`), and update superseded design assertions to the new frozen contract, then re-cross (evidence: 4 sibling test files updated).
