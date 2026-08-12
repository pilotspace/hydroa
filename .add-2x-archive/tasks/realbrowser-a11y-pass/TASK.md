# TASK: Real-browser (Playwright+viewport) axe pass over primary surfaces

slug: realbrowser-a11y-pass · created: 2026-06-15 · stage: production
autonomy: auto   <!-- additive test infra; the only shipped-code risk is a small design-token contrast fix, which Tin pre-authorized ("fix findings even if it touches v13 tokens; flag if large"). A large/structural token change is surfaced, not auto-applied. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- **NEW `apps/dashboard/playwright.config.ts`** — a standalone Playwright config (NOT wired into the vitest
  floor): one headless chromium project at a desktop viewport (1280×800); `webServer` runs the prod app
  (`next build && next start -p <port>`) on 127.0.0.1; `testDir: ./e2e-a11y`.
- **NEW `apps/dashboard/e2e-a11y/a11y.spec.ts`** — the single real-browser axe pass over the PRIMARY
  surfaces: `/login` (public, no backend) + the 4 authed pages `/usage` `/keys` `/spend` `/settings`,
  rendered WITHOUT a gateway via Playwright cookie-seed + `page.route()` interception of the same-origin
  `/api/**` fetches. Asserts ZERO serious/critical axe violations — crucially incl. `color-contrast`, which
  jsdom-axe CANNOT evaluate (the standing v13/v15 residue this discharges).
- **NEW devDeps `@playwright/test` + `@axe-core/playwright`** (+ added to `tests/design-system/allowlist.json`
  so the subset guard in tokens.test.ts stays green). `playwright install chromium` provides the browser
  (cached at ~/Library/Caches/ms-playwright; NOT committed). A new npm script `test:a11y` runs it.
- **Auth guard `apps/dashboard/proxy.ts`** (read-only) — guards `/keys` + `/usage` via a PRESENCE-ONLY
  regex on `ai_proxy_session` (no signature verify); `/spend` + `/settings` are unguarded. So a fake cookie
  value passes; `/api/auth/me` (the role source) is intercepted at the browser, so no server-side decode runs.
- **Data shapes (read-only, reused from tests-bff fixtures)** — `/api/auth/me`→CurrentUser{role}; `/api/gw/`
  {admin/usage→UsageData, admin/budget→BudgetData, v1/models→ModelsData, admin/keys→ApiKey[],
  admin/spend?window=…→SpendWindowResponse, admin/cache→CacheConfig}. Sources: `tests-bff/mocks/handlers.ts`,
  `tests-bff/spend-chart.test.tsx`, `tests-bff/tenant-settings.test.tsx`.
- **Possible (only if axe finds a real contrast issue): a design-token / CSS fix** in
  `apps/dashboard/app/globals.css` (or the token source) — surgical, Tin-pre-authorized; flagged if large.

Context (working folder): v17 MILESTONE.md exit #6 — "a real-browser (Playwright + viewport) axe pass runs
green over the primary surfaces, proving color-contrast + true-layout a11y." Scoped "minimal single pass"
(milestone OUT: a broad E2E/Playwright suite). Tin chose "primary surfaces + fix findings" 2026-06-15.

Honors (patterns / conventions): the v13 UDD a11y baseline (jsdom-axe serious/critical = 0) — this is its
real-browser superset (adds color-contrast + true layout). v14 precedent: an infra-dependent gate with CI
billing-blocked is proven by FRESH LOCAL evidence captured verbatim. Standalone harness so a missing
Chromium never breaks the committed vitest floor. design-for-failure: bounded webServer timeout; the run
fails CLOSED (non-zero) on any serious/critical violation.

Anchors the contract cites: `playwright.config.ts` (chromium, 1280×800, webServer prod) · `e2e-a11y/a11y.spec.ts`
(5 surfaces, cookie-seed + `/api/**` route-interception, axe serious/critical == 0 incl. color-contrast) ·
npm script `test:a11y` · allow-list += {@playwright/test, @axe-core/playwright} · vitest floor untouched.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a single, minimal real-browser (headless Chromium, desktop viewport) axe pass that proves the
primary surfaces have zero serious/critical a11y violations — including the color-contrast + true-layout
rules jsdom-axe cannot evaluate — without requiring a live gateway.

Framings weighed:
- **Standalone Playwright harness; authed pages rendered via cookie-seed + `page.route()` interception**
  (chosen) — covers the primary surfaces in a REAL browser with NO backend (Playwright intercepts the
  same-origin `/api/**` fetches at the network layer), and stays OUT of the vitest floor so a missing
  browser never breaks `npm test`. Reuses the proven tests-bff fixture shapes.
- A live gateway + real auth — REJECTED: no gateway in this env; heavy; out of "minimal".
- MSW-in-browser (service worker) — REJECTED: Playwright `page.route()` is built-in and simpler.
- Login-only pass — REJECTED by Tin: too thin; "primary surfaces" wants the authed design-system pages too.

Must:
<must>
  - `npx playwright test` (script `test:a11y`) runs headless chromium at a 1280×800 viewport against the
    prod-built app on 127.0.0.1 and EXITS 0.
  - the pass covers `/login` + `/usage` + `/keys` + `/spend` + `/settings`; each asserts ZERO axe
    violations of impact serious OR critical (the color-contrast rule ENABLED — real CSS).
  - authed pages render populated layouts via a seeded `ai_proxy_session` cookie + `page.route()` stubs of
    `/api/auth/me` and `/api/gw/**` (valid fixture shapes; no error boundary).
  - the harness is standalone — it is NOT added to `npm test`/the vitest floor; the committed floor (255
    tests) stays green and independent of whether Chromium is installed.
  - any REAL serious/critical violation found is FIXED at the source (a surgical token/markup fix), never
    suppressed by disabling the rule; a large/structural fix is surfaced to the human, not auto-applied.
  - eslint . stays 0/0 and tsc --noEmit stays 0 with the new config + spec.
</must>
Reject:
<reject>
  - disabling color-contrast (or any rule) to force green when a real violation exists -> "rule_suppressed" (defeats the task)
  - adding the playwright spec to the vitest include so the floor needs Chromium -> "floor_coupled"
  - a stub returning a malformed shape that error-boundaries the page (axe then runs on an error state) -> "vacuous_surface"
  - committing the Chromium binary or large playwright artifacts (test-results/, report/) -> "artifact_bloat"
</reject>
After:
<after>
  - the real-browser axe pass is green over all 5 surfaces (serious/critical == 0, contrast included);
    evidence captured verbatim; the vitest floor still green; any contrast fix is documented.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the design-system surfaces have NO real color-contrast violation under real CSS — lowest confidence
    because this is the FIRST time contrast is actually evaluated (jsdom-axe always disabled it). If wrong:
    a surgical token fix (Tin-pre-authorized) and re-run; if the fix is large/structural, surface it (don't
    auto-apply). This is the task's whole point, so a finding is a SUCCESS (caught a real gap), not a failure.
  - [x] a fake cookie value passes the guard — confirmed: proxy.ts is presence-only; /api/auth/me is
    browser-intercepted (no server decode).
  - [x] Playwright + headless Chromium run in this sandbox — confirmed via a throwaway launch spike (Chrome
    Headless Shell 148 launched, set-content + evaluate worked).
  - [x] the fixture shapes render populated layouts — confirmed against tests-bff fixtures.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: login surface is axe-clean in a real browser
  Given the prod app is served on 127.0.0.1 and headless chromium at 1280×800
  When the spec navigates to /login and runs axe (color-contrast enabled)
  Then there are zero serious/critical violations

Scenario: each authed surface is axe-clean with stubbed data
  Given a seeded ai_proxy_session cookie and page.route() stubs for /api/auth/me + /api/gw/**
  When the spec navigates to /usage, /keys, /spend, /settings and runs axe
  Then each renders a populated layout (no error boundary) with zero serious/critical violations

Scenario: a real contrast violation is fixed, not suppressed
  Given axe reports a real serious/critical color-contrast violation
  When it is addressed
  Then the source token/markup is fixed (rule stays enabled); a large fix is surfaced to the human

Scenario: the vitest floor stays independent
  Given the new playwright harness
  When `npm test` (vitest) runs
  Then it does not pick up the playwright spec and the 255-test floor stays green without Chromium

Scenario: no binary/artifact bloat is committed
  Given the harness ran
  When changes are staged
  Then the Chromium binary + test-results/ + playwright-report/ are gitignored, not committed
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
NEW playwright.config.ts: testDir ./e2e-a11y ; projects [chromium] ; use.viewport 1280×800 ; headless ;
  webServer { command: "npm run build && npm run start -- -p <PORT>", url, timeout 180s, 127.0.0.1 } ;
  use.baseURL http://127.0.0.1:<PORT>

NEW e2e-a11y/a11y.spec.ts:
  surfaces = [/login, /usage, /keys, /spend, /settings]
  authed: context.addCookies(ai_proxy_session=<fake>) ; page.route("**/api/auth/me", ->CurrentUser{role:owner})
          ; page.route("**/api/gw/**", -> dispatch by URL to fixture {usage,budget,models,keys,spend,cache})
  assert: new AxeBuilder({page}).analyze() ; violations.filter(impact in {serious,critical}) === []  (contrast ENABLED)

package.json: devDeps += @playwright/test, @axe-core/playwright ; scripts.test:a11y = "playwright test"
allowlist.json devDependencies += "@playwright/test", "@axe-core/playwright"
.gitignore += test-results/ , playwright-report/ , (e2e auth state if any)

NO change to: vitest.config.ts include (floor stays decoupled) ; any app behavior — EXCEPT a surgical,
documented color-contrast token fix if axe finds a real one (rule never disabled to hide it).
```

Status: FROZEN @ v1 — approved by Tin Dang (scope "primary surfaces + fix findings", auto-mode delegation) 2026-06-15

Least-sure flag surfaced at freeze: [build] whether the real-CSS surfaces are already contrast-clean — this
is the first true evaluation (jsdom always disabled color-contrast), so a real finding is plausible. Why it
matters: the fix may touch the frozen v13 tokens; Tin pre-authorized a surgical fix and to flag a large one.
Cost if wrong (i.e. a finding): a small token tweak + re-run — caught a real a11y gap, which is the point.
Secondary [test]: stub shapes must be VALID or the page error-boundaries and axe runs on a degraded layout
(vacuous) — mitigated by reusing the exact tests-bff fixture shapes and asserting a known element renders.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: N/A (real-browser smoke; the assertion is serious/critical axe violations == 0 on 5 surfaces).
Plan:
<test_plan>
  - e2e-a11y/a11y.spec.ts is the suite. RED before build: `npx playwright test` fails because
    @playwright/test + @axe-core/playwright are not installed and the config/spec do not exist.
  - GREEN after build: the 5 surfaces pass (serious/critical == 0); any real contrast finding fixed at source.
</test_plan>

Tests live in: `apps/dashboard/e2e-a11y/` · MUST run red (deps + harness missing) before Build.

<!-- EXIT: the harness runs red for the right reason (missing deps/harness); the assertion is observable. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/playwright.config.ts` `apps/dashboard/e2e-a11y/` `apps/dashboard/package.json` `apps/dashboard/package-lock.json` `apps/dashboard/tests/design-system/allowlist.json` `apps/dashboard/app/globals.css` `apps/dashboard/app/(dashboard)/usage/page.tsx` `apps/dashboard/app/(dashboard)/spend/page.tsx` `apps/dashboard/components/spend/SpendSparkline.tsx` `apps/dashboard/.gitignore` `.gitignore` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `apps/dashboard/.next/` `apps/dashboard/test-results/` `apps/dashboard/playwright-report/` `.add/tasks/realbrowser-a11y-pass/`
<!-- §5 scope corrected during build (scope-gate caught the drift): the real axe findings were NOT color-contrast (those tokens are clean) but `document-title` (→ added metadata.title to the /usage + /spend route wrappers) and `aria-hidden-focus` (→ accessibilityLayer={false} on the decorative SpendSparkline). Both are the "surgical token/MARKUP fix at source" §1 authorizes for ANY real serious/critical violation, within Tin's "fix findings" scope. globals.css stayed untouched (no contrast finding). No frozen §3 contract clause was edited. -->
Honest deviation note: the §3 app-behavior exception named color-contrast specifically; the build instead found `document-title` + `aria-hidden-focus`. §1's "any REAL serious/critical violation is FIXED at the source (a surgical token/MARKUP fix)" is the governing rule and authorizes these; surfaced here rather than silently editing the frozen contract.
Strategy (ordered batches): 1. write playwright.config.ts + e2e-a11y/a11y.spec.ts (RED). 2. install
@playwright/test + @axe-core/playwright + `playwright install chromium`; add the test:a11y script; gitignore
artifacts; add the 2 deps to the allow-list. 3. run `test:a11y`; for each surface fix any real serious/
critical finding AT SOURCE (surgical token/markup; never disable a rule; flag a large fix). 4. green:
all 5 surfaces; then confirm the vitest floor (255) + eslint 0/0 + tsc 0 are untouched.
Safety rule (feature-specific): NEVER disable color-contrast (or any rule) to manufacture green; NEVER add
the spec to the vitest floor; NEVER commit the Chromium binary or playwright artifacts.
Code lives in: `apps/dashboard/e2e-a11y`, `apps/dashboard/playwright.config.ts`.
Constraints: standalone harness; reuse tests-bff fixture shapes; a large token fix is surfaced, not auto-applied.

<!-- Scope tokens project-root-relative; gitignored artifacts declared per the v13 scope-lock convention.
     EXIT: 5 surfaces green (serious/critical==0, contrast on); floor untouched; no artifact committed. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `npm run test:a11y` 5/5 surfaces green on real headless Chromium (Chrome Headless Shell 148, 1280×800), EXIT 0; the committed vitest floor stays 255/255 (EXIT 0) and INDEPENDENT of Chromium (spec lives in e2e-a11y/, outside the vitest include).
- [x] coverage did not decrease — vitest floor coverage unchanged (88.35% lines, == post-vitest4 baseline); the SpendSparkline + page-metadata edits did not drop any line (255 tests still cover them).
- [x] no test or contract was altered during build — the only test edit was STRENGTHENING the new spec (added per-page <h1> content markers so the authed pass is provably non-vacuous); no existing test assertion touched.
- [x] the green was EARNED, not gamed — (1) NO axe rule suppressed: both findings fixed AT SOURCE — `document-title` (added `metadata.title` to /usage + /spend route wrappers, matching the keys/settings convention) and `aria-hidden-focus` (recharts surface had aria-hidden + tabindex=0 → set `accessibilityLayer={false}` on the decorative chart so it leaves the tab order); (2) NON-vacuous: each authed page asserts its real <h1> ("Usage & Cost Analytics"/"API Keys"/"Spend Analytics"/"Settings") renders before axe runs, proving the populated layout (not a loading/error frame); (3) color-contrast was ACTUALLY evaluated (real CSS) and is clean — the v13 tokens pass.
- [x] concurrency / timing of the risky operation is safe — single-worker, headless, bounded webServer timeout (180s); each test waits for nav + heading + networkidle before asserting; fails CLOSED (non-zero) on any serious/critical violation.
- [x] no exposed secrets, injection openings, or unexpected dependencies — devDependency-only (@playwright/test, @axe-core/playwright; both allow-listed); `npm audit` still 0 critical / 0 high / 0 total; the seeded cookie is a FAKE non-JWT (the /api/** calls are browser-intercepted, never reach a gateway); GATEWAY_URL pinned to a dead loopback in the harness.
- [x] layering & dependencies follow CONVENTIONS.md — standalone harness (not wired into the floor); the design-system allow-list subset guard (tokens.test.ts) stays green with the 2 deps added; Chromium binary + test-results/ + playwright-report/ gitignored (git check-ignore confirmed; none in git status).
- [x] a person reviewed and approved the change — autonomy:auto, auto-resolved on complete evidence (behavior-preserving on the floor + 2 real a11y fixes Tin pre-authorized; the fixes are small/surgical, not a large/structural token change, so no human escalation was required). Tin scoped the task "primary surfaces + fix findings" 2026-06-15.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `playwright.config.ts` (testDir ./e2e-a11y, chromium 1280×800, webServer prod) drives `e2e-a11y/a11y.spec.ts`; the `test:a11y` script runs it; `AxeBuilder` + the route stubs + cookie seed are all exercised by the 5 passing tests. The 2 metadata exports + the chart prop are consumed by Next/recharts at render.
- [x] DEAD-CODE (code) — no orphaned symbol; every fixture/helper in the spec is used by a surface.
- [x] SEMANTIC (prose / non-code) — read the axe failure reports in full (not skimmed): confirmed both findings are real (document-title WCAG 2.4.2; aria-hidden-focus on the recharts surface) and the fixes resolve them with the rule still enabled.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: auto-resolved (autonomy:auto) — real-browser 5/5 green incl. color-contrast; 2 real findings fixed at source; floor + audit + lint + tsc clean · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
