# TASK: SSO login: persist domain + clearer error

slug: sso-login-polish · created: 2026-06-24 · stage: production
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

Touches (files · symbols · signatures):
  CHANGES (this task owns — apps/dashboard/components/auth/LoginForm.tsx, 223 lines):
  - `LoginForm()` state (52-58): ssoDomain/ssoError already exist. DELTA-1 (persist): on mount, seed
    `ssoDomain` from `localStorage.getItem("sso_domain")`; on a SUCCESSFUL sso initiation, write the
    resolved domain back. DELTA-2 (clearer not-configured): handleSso currently `window.location.assign(
    OIDC_LOGIN_PATH?domain=)` for a valid domain — a full-page nav that CANNOT catch the relay's 404
    (the unconfigured-domain case renders as raw 404 JSON). Add a pre-flight to detect the 404 and show
    an inline ssoError instead of navigating into a dead page.
  - `handleSso()` (60-77): the SSO initiation handler — the single edit point for both deltas.

  READ (mirror / unchanged):
  - `resolveSsoDomain` (28) / `validateSsoDomain` (43) — domain normalization + lenient shape check;
    UNCHANGED (the gateway stays the authority for "configured or not").
  - existing ssoError `role=alert` render — reuse for the not-configured message.

  CONSUMES (existing, do NOT change):
  - BFF relay `GET /api/auth/oidc/login?domain=` (app/api/auth/oidc/login/route.ts): 3xx (configured) →
    302 + Set-Cookie to the IdP; **4xx (e.g. 404 ERR_OIDC_NOT_CONFIGURED) relayed verbatim** (line 79).
    THIS 4xx is what a pre-flight `fetch(..., {redirect:"manual"})` can read to detect an unconfigured domain.
  - the callback relay's `/login?sso_error=<token>` surface (app/(auth)/login/page.tsx) → a GENERIC
    ErrorState today — a SEPARATE failure surface (callback, not initiation); see §1 framings.

  OUT OF SCOPE:
  - No backend / gateway / relay-route change (the relay already returns the 404 we read).
  - No new auth mechanism; the configured-domain happy path STILL navigates via window.location.assign
    (so the browser follows the relay's 302 + cookies — a fetch alone can't complete the IdP redirect).

Context (working folder):
  - v31 shipped the SSO domain field (sso-login-button) → ?domain=. Two follow-up deltas: (1) persist the
    last-used domain for returning users; (2) the wrong-domain case is "a likely path" (the free-text
    field) and today dead-ends on a raw 404 → make it a clear inline message.
  - SECURITY note: the OIDC initiation is PRE-auth; the relay forwards ONLY `domain` and the cookies are
    CSRF tokens (oidc_state/nonce/tenant), no secret. A pre-flight fetch re-issues state/nonce that the
    real nav overwrites — harmless (per-request CSRF tokens). localStorage holds only a non-secret domain.

Honors (patterns / conventions):
  - Reuse the existing ssoError role=alert pattern; design-for-failure: the pre-flight fetch is bounded
    (timeout) and DEGRADES to the current full-page nav on any network error (never blocks a real login).
  - localStorage is the established "non-secret client preference" store; never put a token/secret there.

Anchors the contract cites:
  - `LoginForm.handleSso` · `localStorage["sso_domain"]` persist+seed · the pre-flight `fetch(
    /api/auth/oidc/login?domain=, {redirect:"manual"})` 4xx→inline-error / else→window.location.assign.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: SSO login polish — persist the last-used domain + a clear inline "domain not configured" message
Framings weighed:
  - DELTA-2 via a PRE-FLIGHT fetch that reads the relay's 404 then shows an inline message, navigating
    only when configured (CHOSEN) — fixes the ACTUAL gap (wrong domain dead-ends on raw 404 JSON today),
    pre-auth + no secret, degrades to today's nav on any fetch error.
  - DELTA-2 via mapping the /login?sso_error token to a clearer message (alternative) — rejected as the
    sole fix: that surface is the CALLBACK failure, not the initiation 404; it would NOT fix the
    wrong-domain dead-end the delta is about.
  - DELTA-1 persist via a cookie / server pref (alternative) — rejected: a non-secret UI preference
    belongs in localStorage (no round-trip, no server change); the established client-pref store.
Must:
<must>
  - PERSIST (delta-1): on mount, if `localStorage["sso_domain"]` is set, pre-fill the SSO domain field
    with it. On a SUCCESSFUL sso initiation (valid + configured), write the resolved domain to
    `localStorage["sso_domain"]`.
  - NOT-CONFIGURED (delta-2): on Save-SSO with a shape-valid domain, pre-flight
    `fetch(/api/auth/oidc/login?domain=, {redirect:"manual"})`; if the relay returns a 4xx, show an inline
    ssoError ("That domain isn’t set up for single sign-on. Check the spelling or contact your
    administrator.") and DO NOT navigate.
  - CONFIGURED happy path: a non-4xx pre-flight → proceed with the existing full-page
    `window.location.assign(OIDC_LOGIN_PATH?domain=)` (browser follows the relay's 302 + cookies) AND
    persist the domain.
  - DEGRADE: if the pre-flight fetch throws / times out, fall back to the existing window.location.assign
    (never block a real login on a flaky probe).
  - UNCHANGED: empty field → env-fallback nav (no ?domain=, no persist); shape-invalid domain → existing
    inline validation error, no nav, no fetch; password login path untouched.
</must>
Reject:
<reject>
  - Shape-valid but UNCONFIGURED domain (relay 4xx) -> inline ssoError (role=alert), NO navigation, NO
    persist of a known-bad domain; the field value is preserved.
  - Shape-invalid domain -> existing validateSsoDomain error (role=alert), NO fetch, NO nav (unchanged).
</reject>
After:
<after>
  - A returning user finds their last domain pre-filled; a user who mistypes a domain gets a clear inline
    message instead of a dead 404 page.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The pre-flight fetch does not corrupt the real OIDC flow — lowest confidence because the relay sets
    oidc_state/nonce cookies on BOTH the pre-flight 302 and the real-nav 302; the real nav's overwrite the
    pre-flight's, so the IdP round-trip uses a consistent state/nonce pair. If wrong (e.g. a relay that
    pins one-time state), a configured login could fail. Mitigation: pre-flight reads STATUS only +
    degrades to direct nav on error. THIS is the §3 freeze flag.
  - [x] localStorage holds only the non-secret domain string — confirmed (the domain is already sent in
    the URL; no secret introduced).
  - [x] the relay returns a readable same-origin 4xx for an unconfigured domain — confirmed
    (oidc/login/route.ts:79 relays 4xx verbatim as same-origin JSON).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: last-used domain is pre-filled
  Given localStorage["sso_domain"] = "acme.com"
  When the LoginForm mounts
  Then the "Work email or domain" field shows "acme.com"

Scenario: configured domain navigates and persists
  Given the relay pre-flight for "acme.com" returns a 302 (configured)
  When the user clicks Sign in with SSO
  Then window.location.assign("/api/auth/oidc/login?domain=acme.com") is called
  And localStorage["sso_domain"] === "acme.com"

Scenario: unconfigured domain shows inline message, no nav
  Given the relay pre-flight for "nope.com" returns a 404
  When the user clicks Sign in with SSO
  Then an inline ssoError (role=alert) about an unconfigured domain is shown
  And window.location.assign is NOT called
  And localStorage["sso_domain"] is NOT set to "nope.com"

Scenario: pre-flight error degrades to direct navigation
  Given the relay pre-flight throws / times out
  When the user clicks Sign in with SSO
  Then window.location.assign("/api/auth/oidc/login?domain=acme.com") is still called

Scenario: empty field keeps env fallback (unchanged)
  Given the SSO field is empty
  When the user clicks Sign in with SSO
  Then window.location.assign("/api/auth/oidc/login") is called with no ?domain=
  And no pre-flight fetch is made

Scenario: shape-invalid domain blocks before any fetch (unchanged)
  Given the SSO field is "notadomain" (no dot)
  When the user clicks Sign in with SSO
  Then a validation role=alert is shown
  And no pre-flight fetch and no navigation occur
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Component: LoginForm()  (apps/dashboard/components/auth/LoginForm.tsx — EDIT handleSso + mount seed)

  MOUNT (delta-1): seed ssoDomain from localStorage["sso_domain"] (useState initializer / effect).

  handleSso() (async):
    raw = ssoDomain.trim()
    - raw === ""           -> window.location.assign("/api/auth/oidc/login")   (unchanged; no persist)
    - validateSsoDomain bad -> setSsoError(validation msg); return            (unchanged; no fetch/nav)
    - else domain = resolveSsoDomain(raw):
        preflight = await fetch(`/api/auth/oidc/login?domain=${enc(domain)}`,
                                { redirect: "manual", signal: AbortSignal.timeout(5000) })
          preflight.status in [400,499] -> setSsoError("That domain isn’t set up for single sign-on.
                                            Check the spelling or contact your administrator."); return
          else (3xx/ok/opaqueredirect) -> localStorage.setItem("sso_domain", domain);
                                          window.location.assign(`/api/auth/oidc/login?domain=${enc(domain)}`)
        catch (network/timeout)        -> window.location.assign(`/api/auth/oidc/login?domain=${enc(domain)}`)
                                          (DEGRADE: never block a real login on a flaky probe)

  CONSUMES (existing, unchanged): the relay GET /api/auth/oidc/login?domain= (3xx configured / 4xx not).
Schema: none (FE-only; localStorage["sso_domain"] = non-secret domain string; no DB/endpoint/relay change).
```

Status: FROZEN @ v1 — approved by Tin 2026-06-24 (chose "freeze with pre-flight": detect the unconfigured-domain 404 inline + degrade to direct nav on error; over the message-only-safer alternative). Least-sure flag surfaced at freeze: [contract] the pre-flight fetch touches the pre-auth OIDC initiation (state/nonce re-issue overwritten by the real nav; pre-auth, no secret; reads status only + degrades on error) — Tin accepted this risk at freeze.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: the new handleSso behaviors (extend tests/sso-login.test.tsx — msw relay mock + localStorage)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_sso_prefills_last_domain: set localStorage["sso_domain"]="acme.com", render / assert the field
    shows "acme.com".
  - test_sso_configured_navigates_and_persists: relay GET → 302, click SSO "acme.com" / assert assign
    called with ?domain=acme.com AND localStorage["sso_domain"]==="acme.com".
  - test_sso_unconfigured_shows_message: relay GET → 404, click SSO "nope.com" / assert role=alert about
    unconfigured AND assign NOT called AND localStorage NOT set to nope.com.
  - test_sso_preflight_error_degrades: relay GET → network error, click SSO "acme.com" / assert assign
    STILL called with ?domain=acme.com.
  - test_sso_empty_keeps_env_fallback: empty field, click SSO / assert assign("/api/auth/oidc/login")
    with no ?domain= AND no relay fetch made (unchanged).
  - test_sso_malformed_blocks: "notadomain", click SSO / assert role=alert AND no fetch AND no assign
    (unchanged — keep the existing v31 test green).
</test_plan>

Tests live in: `dashboard/tests` · MUST run red (the new assertions fail on today's handleSso) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/auth/LoginForm.tsx` `dashboard/tests/sso-login.test.tsx`
Strategy (ordered batches):
  1. Extend tests/sso-login.test.tsx: add prefill / configured-persist / unconfigured-message /
     preflight-degrade tests (msw relay 302/404/error + localStorage) → run RED. Keep the 5 v31 tests green.
  2. LoginForm.tsx: seed ssoDomain from localStorage on mount; make handleSso async with the pre-flight
     fetch (4xx→inline error/no nav; else persist+assign; catch→assign).
  3. Run green; full dashboard vitest + lint + build.
Safety rule (feature-specific): the pre-flight reads STATUS ONLY and NEVER blocks a real login — any
  fetch error degrades to the existing window.location.assign. localStorage stores ONLY the non-secret
  domain. No secret/token ever touches localStorage; no relay/gateway change.
Code lives in: `apps/dashboard/components/auth/`
Constraints: do NOT change the v31 tests' meaning (only add); reuse existing deps; FE-only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — sso-login.test.tsx 9/9; full dashboard vitest 401 (52 files, incl. the |bff| project); tsc + eslint clean; real `next build` exit 0 (/login compiled)
- [x] coverage did not decrease — additive (3 new polish tests + 1 strengthened assert); the 5 v31 tests kept (2 made async-aware only, meaning unchanged)
- [x] no test or contract was altered to cheat — §3 frozen untouched; post-refute I TIGHTENED impl to the contract (persist only on verified-configured, not on degrade) + strengthened the degrade test; re-crossed
- [x] the green was EARNED — adversarial refute-read (sonnet) = UPHOLD, 0 blockers. 3 minors: (#1) degrade persisted unverified domain → FIXED (persist moved inside the non-4xx arm); (#2) degrade test didn't pin no-persist → FIXED (added `localStorage…toBeNull()`); (#3) cosmetic redundant relay handler — left.
- [x] concurrency / timing — pre-flight is awaited then nav; AbortSignal.timeout(5000) bounds a hung gateway; degrade-on-throw guarantees a real login never blocks on the probe
- [x] no exposed secrets / deps — FE-only, no new package; localStorage holds only the non-secret domain string; pre-flight sends no auth header; relay drops all params but `domain`
- [x] layering & dependencies follow CONVENTIONS.md — gateway stays the authority (client guard is fast-fail UX; the relay 4xx is the source of truth); no backend/relay change
- [ ] a person reviewed and approved the change — PENDING Tin (commit/PR held)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] Returning user's last domain pre-fills the field — confirmed by test_sso_prefills_last_domain (DOM input value) + readSsoDomain/useEffect seed
- [x] Unconfigured domain (relay 4xx) → inline role=alert, NO nav, NOT persisted — confirmed by test_sso_unconfigured_shows_message (3 asserts)
- [x] Configured domain → persist + window.location.assign(?domain=) — confirmed by test_sso_configured_navigates_and_persists
- [x] Pre-flight error → still navigates, no persist — confirmed by test_sso_preflight_error_degrades (assign called + localStorage null)
- [x] Empty / shape-invalid unchanged — confirmed by test_sso_empty_keeps_env_fallback + test_sso_malformed_blocks_navigation

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — readSsoDomain (mount useEffect) + persistSsoDomain (non-4xx arm) + SSO_DOMAIN_KEY/SSO_NOT_CONFIGURED_MSG/SSO_PREFLIGHT_TIMEOUT_MS all referenced; reached by the 4 new/strengthened tests
- [x] DEAD-CODE (code) — no orphan; every new constant/helper is on a live path
- [x] SEMANTIC — n/a (code task)

### GATE RECORD
Outcome: PASS
Evidence: sso-login.test.tsx 9/9 · full dashboard vitest 401 (52 files) · tsc clean · eslint clean ·
real `next build` exit 0 (/login route compiled) · refute-read (sonnet) UPHOLD 0-blockers, 3 minors
(2 FIXED, 1 cosmetic). FE-only; the relay's 4xx stays the source of truth for "configured".
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a
Reviewed by: AI auto-gate (autonomy:auto) · human approval (Tin) PENDING for commit/PR · date: 2026-06-24

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

Watch: SSO-not-configured inline-message rate · pre-flight degrade rate (probe errors) · prefilled-domain reuse.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] map the callback `/login?sso_error=<token>` surface to richer per-token messages (the OTHER failure surface — callback, not initiation) (evidence: §0 noted page.tsx shows one generic ErrorState for any token).
- [SPEC · open] a "clear saved domain" affordance / forget-me for the persisted SSO domain on a shared machine (evidence: localStorage seed persists across users on one browser).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · folded] `redirect:"manual"` surfaces a configured 3xx as opaqueredirect (status 0) in browsers but a readable 302 under node/undici+msw — gate on "NOT a 4xx" (`status>=400 && <500`), never `status===302`, so the same code is correct in both runtimes (evidence: §4 NOTE; tests mock a 302 arm). [folded foundation-version 34]
- [TDD · folded] a shared component tested by TWO vitest projects (`|bff|` lacks a full localStorage) forces defensive storage accessors (typeof-guard + try/catch) — a browser-only API touched at mount must degrade, not throw (evidence: bff project `localStorage.getItem is not a function` until guarded). [folded foundation-version 34]
- [UDD · folded] a localStorage seed must read in an effect (not a lazy useState initializer) to stay SSR-safe; the `react-hooks/set-state-in-effect` lint flags it → a single-line scoped disable directly above the setState (multi-line directive misses the target line) (evidence: directive on the comment-continuation line read as "unused"). [folded foundation-version 34]
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
