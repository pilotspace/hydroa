# TASK: SLO dashboard page (availability/error-rate/volume)

slug: slo-dashboard · created: 2026-06-25 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: build   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - SOURCE: `GET /admin/slo?window_hours=N` (built in slo-metrics) → { window_hours, total_requests, success_count, client_error_count, server_error_count, availability, error_rate, latency_ms:null }.
  - NEW `apps/dashboard/app/(app)/app/slo/page.tsx` + `apps/dashboard/components/slo/SloPage.tsx` — mirror `components/health/HealthPage.tsx` + `app/(app)/app/health/page.tsx` (read-only card page calling the admin API via the dashboard api-client / hooks).
  - `apps/dashboard/components/ui/app-shell.tsx` — ADD an "SLO" nav item (mirror the health/usage nav entries' minRole treatment).
  - `apps/dashboard/lib/api-client.ts` (or hooks/) — add the GET /admin/slo fetch.
Context: the health/alerts/usage dashboard pages are the exact analog (cards + a window/refresh control); vitest + axe; tests-bff nav-role-filter test counts nav links. Use real node_modules/.bin binaries, not npx.
Honors: read-only; reuse Card/Badge tokens; WCAG-AA (one h1, monotonic headings, status text-not-color-only); latency shown as "not available yet" (honest — the API returns null).
Anchors the contract cites: `/app/slo` page · `SloPage` · the GET /admin/slo fetch · the SLO nav item · the window selector.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A dashboard SLO view rendering availability, error-rate, and volume from GET /admin/slo
Framings weighed: read-only card page mirroring the health page (chosen) · embed in the existing health page (rejected: SLO is a distinct concern) · charts/graphs (deferred: cards first, charts a delta)
Must:
<must>
  - NEW `/app/slo` renders, for the selected window: availability (as a %), error-rate (%), total requests, and the success/client-error/server-error breakdown — fetched from GET /admin/slo.
  - A window selector (e.g. 24h / 7d / 30d → window_hours 24/168/720) that refetches.
  - Availability/error-rate conveyed by TEXT (a %), not color alone; a status badge may add color but the % text is authoritative (WCAG-AA).
  - Latency is shown as an explicit "not available yet" placeholder (the API returns latency_ms: null) — honest, not a fake 0.
  - Reachable from nav; read-only; one h1; axe 0 serious/critical.
  - Loading + error + empty (zero requests → availability 100%) states handled.
</must>
Reject:
<reject>
  - Rendering a fabricated latency value -> not allowed (show "not available yet")
  - Status by color alone (no text %) -> "status_color_only" (a11y)
  - More than one h1 / skipped heading -> "heading_order_violation"
</reject>
After:
<after>
  - /app/slo shows availability/error-rate/volume for the chosen window with honest latency placeholder; nav link resolves; axe clean; dashboard vitest green; next build exit 0.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The page is a thin render of GET /admin/slo (built + frozen). Lowest confidence: the window selector values (24h/7d/30d). Decision (auto): offer 24h/7d/30d (=24/168/720). If wrong: adjust the options.
  - [ ] nav minRole — mirror the health/usage entries (BE enforces OPS_READ regardless; nav visibility is UX only).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: SLO page renders metrics from the API
  Given GET /admin/slo returns availability 0.95, error_rate 0.05, total 100
  When /app/slo renders
  Then it shows "95%" availability (text), the error-rate %, total requests, and the 95/5/5 breakdown

Scenario: Window selector refetches
  Given the page with a 24h/7d/30d selector
  When the user picks 7d
  Then it refetches GET /admin/slo?window_hours=168 and re-renders

Scenario: Honest latency placeholder
  Given latency_ms is null
  When the page renders
  Then latency shows "not available yet" (no fabricated number)

Scenario: Empty window
  Given total_requests=0, availability=1.0
  When the page renders
  Then it shows 100% availability / 0 requests (no NaN)

Scenario: Accessible
  Given /app/slo
  When the a11y suite runs
  Then axe 0 serious/critical, one h1, status conveyed by text (not color alone), monotonic headings

Scenario: Nav link resolves
  Given the SLO nav item
  When clicked
  Then /app/slo loads
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PAGE app/(app)/app/slo/page.tsx → <SloPage/>  (read-only, inside the DashboardShell)
  h1 "Service levels" (or "SLO")
  WINDOW selector: 24h(24) · 7d(168) · 30d(720) → refetch GET /admin/slo?window_hours=
  CARDS: Availability (text "%"), Error rate (text "%"), Total requests, Breakdown (success/client/server)
  Latency: "not available yet" placeholder (API latency_ms is null — honest)
  A11Y: one h1; ordered headings; status text-labelled (not color-only); axe 0 serious/critical.
NAV: app-shell.tsx — add an SLO item (mirror health/usage minRole).
DATA: GET /admin/slo via the dashboard api-client/hook (OPS_READ enforced server-side).
Schema: NO BE change (consumes the frozen slo-metrics endpoint). 1 new page + components + nav link + fetch.
Least-sure flag surfaced at freeze: [contract] window options 24h/7d/30d; cost if wrong = trivially adjust the selector.
```

Status: FROZEN @ v1 — auto-frozen (autonomy: auto; non-security read-only FE rendering the frozen GET /admin/slo) 2026-06-25.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: page render + states covered; dashboard vitest green; no regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_slo_renders_metrics: mock GET /admin/slo (0.95/0.05/100) -> shows 95% / error % / total / breakdown
  - test_slo_window_selector: picking 7d refetches window_hours=168
  - test_slo_latency_placeholder: latency_ms null -> "not available yet"
  - test_slo_empty_window: total 0 / avail 1.0 -> 100% / 0 (no NaN)
  - test_slo_a11y: axe 0 serious/critical; one h1; status text-labelled
  - test_slo_nav_link: nav includes /app/slo (nav-role-filter test updated)
</test_plan>

Tests live in: `apps/dashboard/tests/` `apps/dashboard/tests-bff/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/(app)/app/slo/` `apps/dashboard/components/` `apps/dashboard/lib/` `apps/dashboard/tests/` `apps/dashboard/tests-bff/`
Strategy (ordered batches):
  1. RED: dashboard tests/slo-page.test.tsx + nav update.
  2. FE: SloPage (cards + window selector + honest latency placeholder + loading/error/empty states) + /app/slo page + nav item + api-client/hook fetch.
  3. Green: dashboard vitest (full) + tsc + next build.
Safety rule (feature-specific): READ-ONLY; HONEST latency placeholder (no fabricated number); status conveyed by TEXT not color alone; one h1; no BE change.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the FROZEN contract; do NOT create tmp/*.txt (inline -m commits); use real node_modules/.bin binaries not npx; allow-list packages only.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [ ] availability/error-rate/volume rendered from the API (95%/5%/100) — by vitest
- [ ] window selector refetches window_hours=168 on 7d — by vitest
- [ ] honest latency placeholder (null → "not available yet") + empty-window 100%/0 no NaN — by vitest
- [ ] a11y (one h1, text-not-color, axe clean) + nav link + next build — by vitest + build

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — page + SloPage + fetch + nav referenced
- [ ] DEAD-CODE (code) — no orphan
- [ ] SEMANTIC (prose / non-code) — honest latency placeholder; read-only

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): SLO page load errors · availability display.

### Spec delta
- [SPEC · open] time-series charts (availability/error-rate over time) once a latency_ms column + historical buckets exist · SLO target line + burn-rate · operator-wide SLO view.

### Competency deltas
- [UDD · open] honest placeholders for not-yet-available metrics (latency "not available yet") keep the UI truthful (mirrors /status + slo-metrics honesty).
