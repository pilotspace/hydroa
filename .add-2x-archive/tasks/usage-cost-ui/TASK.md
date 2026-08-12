# TASK: Refresh the usage/cost dashboard (/usage + /spend) onto the design system + charts

slug: usage-cost-ui · created: 2026-06-13 · stage: production
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

Touches (files · symbols · signatures): the 6 /usage + /spend surface components, restyled onto the v13 design system (NO data change). Verified current structure + the observable markers each test asserts (MUST preserve):
- `components/usage/UsagePage.tsx:UsagePage` — orchestrator; 3 `useQuery` (`["admin-usage"]`→apiGet`/admin/usage`, `["v1-models"]`→`/v1/models` enabled-after-usage [the `/0\.00/` separate-commit guard], `["admin-budget"]`→`/admin/budget`) + `useCurrentUser` (`role`→`canEdit`). Renders `<h1>Usage &amp; Cost Analytics</h1>` + 4 `<section><h2>`: Usage Summary · Usage Records · Budget · Model Catalog.
- `components/usage/UsageStatsCards.tsx:{UsageStatsCards,UsageData,UsageRecord}` — loading `<div role="status" aria-busy data-testid="loading" class="animate-pulse"><span>Loading…</span>`; error `<p role="alert">{ApiError.problem.title}</p>`; success 4 cards (label+value) from `total_requests/total_prompt_tokens/total_completion_tokens/total_cost_usd`. Tests: T20 getByText("3"/"300"/"150"/ /1\.23/), T21 getAllByText("0")≥4, T22 title + 0 rows, T23 loading marker, T26/27 budget, T28-33/35 edit.
- `components/usage/UsageTable.tsx:UsageTable` — loading/error→`null` (0 `role=row`); empty→`<p>No usage records yet</p>`; success→`<table>` th(Model·Prompt Tokens·Completion Tokens·Cost (USD)·Status·Date) + rows keyed on `rec.id` (model_id/prompt_tokens/completion_tokens/cost_usd/status/created_at).
- `components/usage/BudgetWidget.tsx:{BudgetWidget,BudgetData}` — loading role=status; error role=alert; "Monthly Budget: {ceiling||'Unlimited'}" + "Spent this month: {spent_usd_month}"; `canEdit&&!editing` → `<button>Edit Budget</button>`; editing → BudgetEditForm. Fields `budget_usd_monthly:string|null`, `spent_usd_month:string`.
- `components/usage/BudgetEditForm.tsx:BudgetEditForm` — Zod (""→null, `^\d+(\.\d+)?$`); `<label htmlFor="budget-input">Budget (USD/month)</label>` + `aria-label="Budget"`; `role="alert" aria-live="assertive"` field+server errors; PUT `/admin/budget` body **`budget_usd_monthly`** (cache setQueryData `["admin-budget"]`); buttons "Save"(/Saving…)/"Cancel".
- `components/spend/SpendPage.tsx:SpendPage` — `data-testid="spend-page"`, `<h1>Spend Analytics</h1>`, `<select id/data-testid="window-selector">` day/week/month (default month, drives `["admin-spend",window]`→bffGet`/admin/spend?window=`), `spend-loading`(role=status, inner `.animate-pulse`), `spend-error`(role=alert), `spend-zero-state` ("No usage in this period"), `spend-data` with `totals-cost/requests/prompt/completion` + `spend-bucket` li. Fields `totals.{cost_usd,requests,prompt_tokens,completion_tokens}`, `buckets[].{bucket_start,cost_usd}`.
- NEW: this task may ADD a Recharts chart (spend-over-time/usage trend) — additive, no new data (derived from existing `buckets`/`records`); chart is decorative, the testid data leaves stay.
- CONSUMES (frozen, v13 task 1): `components/ui/{Card,Table,Badge,Button,Input,states(Loading/Empty/ErrorState/Success)}` + `lib/cn.ts` + the `@theme` token classes + `recharts` (allow-listed).

Context (working folder):
- Behavioral test suites that MUST stay green: `tests/usage.test.tsx` (T20–35), `tests-bff/govern.test.tsx` SpendPage block (T12–17). They key on roles/aria/exact text/data-testid (enumerated above), NOT CSS — so restyling is safe IF every marker is preserved. The `/0\.00/` separate-commit guard in UsagePage (models enabled-after-usage) must be kept.
- `.add/milestones/v13/MILESTONE.md` usage-cost-ui row + the v1 UDD state-pattern (loading·empty·error·success).

Honors (patterns / conventions):
- MILESTONE.md: behavior-preserving / data-identical (same hook, route, field names; existing tests green); design tokens consumed not hardcoded; WCAG 2.2 AA (accessible tables — caption/scope; accessible charts — title/aria or a table fallback); responsive to mobile.
- CONVENTIONS.md v1 UDD: RTL scopes with `within(section)`; every surface renders all four states.
- v13 design-system contract: use `components/ui/*` primitives + state components; no raw hex/px (R3 carries forward).

Anchors the contract cites: the 6 surface components above (restyled, markers preserved) · `components/ui/{Card,Table,Badge,Button,Input}` + `states` · `recharts` chart wrapper (new) · the PRESERVED data seam (`apiGet`/`bffGet` calls, `budget_usd_monthly`, query keys) + the test-observable surface (roles/aria/text/testids enumerated above).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Usage/Cost dashboard refresh — /usage + /spend restyled onto the v13 design system, plus a Recharts spend-over-time chart. Presentation-only: NO data hook, BFF route, query key, field name, or test-observable marker changes.

Framings weighed: Restyle-in-place — swap raw markup for `components/ui/*` primitives surface-by-surface, preserving every role/aria/text/testid (chosen) · Rewrite the surfaces against the catalog prototype (rejected — higher regression risk, the 6 components already carry the frozen data seam) · Defer charts to ui-ux-verify (rejected — chart is the milestone's stated value-add and is purely additive from existing buckets).

Must:
<must>
  - Restyle all 6 surfaces (UsagePage, UsageStatsCards, UsageTable, BudgetWidget, BudgetEditForm, SpendPage) onto `components/ui/*` primitives (Card/Table/Badge/Button/Input + Loading/Empty/ErrorState/Success state components) — visual layer only.
  - Preserve EVERY test-observable marker enumerated in §0: roles (`status`/`alert`/`row`), aria (`aria-busy`/`aria-live`), exact text (`"Usage & Cost Analytics"`, `"No usage records yet"`, `"Monthly Budget: …"`, `"Budget (USD/month)"`, `"Spend Analytics"`, `"No usage in this period"`), and all data-testids (`loading`, `window-selector`, `spend-page`, `spend-loading/error/zero-state/data`, `totals-cost/requests/prompt/completion`, `spend-bucket`).
  - Preserve the data seam unchanged: same `useQuery` keys (`["admin-usage"]`, `["v1-models"]`, `["admin-budget"]`, `["admin-spend",window]`), same `apiGet`/`bffGet` routes, same PUT `/admin/budget` body field `budget_usd_monthly`, same `useCurrentUser`→`canEdit` gating, same `/0\.00/` models-enabled-after-usage guard.
  - Render all four UDD states (loading · empty · error · success) on every surface that has them, via the shared state components.
  - ADD a Recharts spend-over-time chart on SpendPage, derived solely from the existing `buckets[].{bucket_start,cost_usd}` — additive; the `spend-bucket` list + all `totals-*` leaves remain present and unchanged.
  - Chart is accessible: carries a title + `aria-label` (or role/desc), and does NOT become the sole source of any datum (the data table/list stays as the accessible fallback).
  - Consume design tokens via primitive classes only — no raw hex/px in the restyled surfaces (R3 from v13 task 1 carries forward).
</must>
Reject:
<reject>
  - Any change to a query key, route, request/response field name, or `canEdit` gating logic -> "behavior_regression"
  - Any removed/renamed role, aria attribute, exact text, or data-testid a test asserts -> "behavior_regression"
  - A raw hex/px literal in a restyled surface instead of a token class -> "untokenized_value"
  - A surface (or the chart) reachable only visually — missing role/aria/label, or data exposed solely in the chart -> "a11y_floor_violation"
  - Any import outside the node allow-list (e.g. a second chart lib) -> "unlisted_dependency"
</reject>
After:
<after>
  - The 6 surfaces render through `components/ui/*` primitives with the v13 tokens; SpendPage shows an accessible Recharts chart above its preserved bucket list.
  - All existing behavioral tests stay green: `tests/usage.test.tsx` (T20–35) and `tests-bff/govern.test.tsx` SpendPage block (T12–17), zero regression.
  - `next lint` clean, vitest coverage ≥ 80% (held from v13 task 1's 88.79%), node deps allow-list clean.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Recharts renders deterministically enough in jsdom to assert the chart's accessible wrapper (title/aria) WITHOUT a `ResponsiveContainer` width-0 flake — lowest confidence because Recharts measures DOM width, which jsdom reports as 0, often suppressing SVG children; if wrong: I assert on the wrapper/`<figure>`+`aria-label` and the fallback list (not on SVG paths), or give the container a fixed test width — a test-authoring adjustment, not a data/contract change.
  - [ ] The existing tests key only on roles/aria/text/testids and NOT on CSS classes or DOM nesting depth — confirmed by §0 read (they use `getByText`/`getByRole`/`getByTestId`/`within(section)`); if a test asserts a wrapper tag, the primitive must emit the same tag.
  - [ ] Restyling UsageStatsCards' loading `<div role="status" aria-busy data-testid="loading" class="animate-pulse">` to use the shared `Loading` state component keeps all three markers — confirmed the v13 `Loading` emits `role="status"`+`aria-busy`; I'll pass `data-testid="loading"` through.
  - [ ] No new responsive breakpoint logic is in scope here (full responsive/keyboard/browser-axe sweep is the later `ui-ux-verify` task) — these surfaces inherit the AppShell + token responsive utilities only.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Usage summary restyled, values preserved
  Given the admin-usage query resolves with total_requests=3, prompt=300, completion=150, cost=1.23
  When UsagePage renders the Usage Summary section
  Then UsageStatsCards shows "3", "300", "150" and a value matching /1\.23/ inside Card primitives
  And the query key ["admin-usage"] and route /admin/usage are unchanged

Scenario: Usage records table restyled, headers + rows preserved
  Given admin-usage resolves with one record (model_id/prompt/completion/cost/status/created_at)
  When UsageTable renders
  Then a <table> via the Table primitive shows headers Model·Prompt Tokens·Completion Tokens·Cost (USD)·Status·Date and one role=row data row keyed on rec.id
  And the record field names are unchanged

Scenario: Empty usage records
  Given admin-usage resolves with zero records
  When UsageTable renders
  Then the exact text "No usage records yet" is shown via the Empty state component
  And no role=row data row is rendered

Scenario: Loading state via shared component preserves markers
  Given admin-usage is still pending
  When UsageStatsCards renders
  Then an element with role="status", aria-busy, and data-testid="loading" is present
  And the loading marker contract is unchanged

Scenario: Error state via shared component
  Given admin-usage rejects with an ApiError problem.title
  When UsageStatsCards renders
  Then a role="alert" element shows problem.title via the ErrorState component
  And the error data shape is unchanged

Scenario: Budget widget restyled, gating preserved
  Given admin-budget resolves and useCurrentUser gives a role where canEdit=true
  When BudgetWidget renders
  Then "Monthly Budget: …" and "Spent this month: …" show via Card, and an "Edit Budget" Button appears
  And canEdit gating and the ["admin-budget"] key are unchanged

Scenario: Budget edit submits the frozen field name
  Given the budget edit form is open with input "50"
  When the user submits Save
  Then a PUT /admin/budget fires with body field budget_usd_monthly="50" and the cache ["admin-budget"] is set
  And the label "Budget (USD/month)" and field name budget_usd_monthly are unchanged

Scenario: Budget edit invalid input rejected
  Given the budget edit form is open with input "abc"
  When the user submits Save
  Then a role="alert" aria-live="assertive" field error shows and no PUT fires -> "behavior_regression" guarded
  And the Zod rule (""→null, ^\d+(\.\d+)?$) is unchanged

Scenario: Spend page restyled, totals + buckets preserved
  Given admin-spend?window=month resolves with totals and buckets
  When SpendPage renders
  Then data-testid spend-page/spend-data with totals-cost/requests/prompt/completion and spend-bucket items are present via primitives
  And the ["admin-spend",window] key and bffGet route are unchanged

Scenario: Spend zero-state
  Given admin-spend resolves with no usage in the period
  When SpendPage renders
  Then data-testid="spend-zero-state" shows "No usage in this period"
  And the window-selector default "month" is unchanged

Scenario: Accessible spend chart added (additive)
  Given admin-spend resolves with buckets[].{bucket_start,cost_usd}
  When SpendPage renders
  Then a Recharts chart with a title + aria-label is shown, derived only from buckets
  And the spend-bucket list and all totals-* leaves remain present (chart is not the sole data source)

Scenario: No untokenized value in a restyled surface
  Given the restyled surfaces are linted by the R3 token guard
  When the design-system token test scans the surfaces
  Then no raw hex (#rrggbb) or px literal appears -> "untokenized_value" guarded
  And only token classes / primitives are used

Scenario: No unlisted dependency
  Given the node deps allow-list check runs
  When SpendPage's chart imports are scanned
  Then only recharts (already allow-listed) is imported, no second chart lib -> "unlisted_dependency" guarded
  And the allow-list is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# PRESENTATION-ONLY task — the data seam is FROZEN UNCHANGED, not authored here.
# The "contract" this task freezes = (a) the seam it must NOT touch, (b) the new chart's render shape.

PRESERVED DATA SEAM (must remain byte-identical) ────────────────────────────
  GET  /admin/usage        key ["admin-usage"]   -> { total_requests, total_prompt_tokens,
                                                      total_completion_tokens, total_cost_usd, records[] }
  GET  /v1/models          key ["v1-models"]     -> models[]   (enabled-after-usage; /0\.00/ guard kept)
  GET  /admin/budget       key ["admin-budget"]  -> { budget_usd_monthly: string|null, spent_usd_month: string }
  PUT  /admin/budget       body: { budget_usd_monthly: string|null }   # NOT monthly_budget_usd
                                                  -> setQueryData(["admin-budget"], …)
  GET  /admin/spend?window=<day|week|month>  key ["admin-spend", window] (bffGet)
        -> { totals: { cost_usd, requests, prompt_tokens, completion_tokens },
             buckets: [ { bucket_start, cost_usd } ] }
  useCurrentUser().role -> canEdit gating (unchanged)

PRESERVED TEST-OBSERVABLE SURFACE (roles · aria · exact text · data-testid) ──
  roles:    status (loading) · alert (error) · row (table rows)
  aria:     aria-busy (loading) · aria-live="assertive" (form errors)
  text:     "Usage & Cost Analytics" · "No usage records yet" · "Monthly Budget: {…|Unlimited}"
            · "Spent this month: {…}" · "Budget (USD/month)" · "Spend Analytics"
            · "No usage in this period" · table th: Model·Prompt Tokens·Completion Tokens·Cost (USD)·Status·Date
            · buttons: "Edit Budget" · "Save"/"Saving…" · "Cancel"
  testid:   loading · window-selector · spend-page · spend-loading · spend-error
            · spend-zero-state · spend-data · totals-cost · totals-requests
            · totals-prompt · totals-completion · spend-bucket

NEW CHART RENDER CONTRACT (additive, SpendPage only) ────────────────────────
  SpendSparkline(props: { buckets: { bucket_start: string; cost_usd: string }[] })
    renders: a recharts <LineChart>/<AreaChart> of cost_usd over bucket_start,
             wrapped in <figure data-testid="spend-chart" aria-label="Spend over time">
             with a visible title element.
    derives: ONLY from props.buckets (no new fetch, no new query key, no new field).
    fallback: the existing spend-bucket <li> list + totals-* leaves stay rendered
              and remain the accessible source of every datum.
    empty:    buckets.length === 0 -> chart not rendered (zero-state list path unchanged).
  4xx -> N/A (no new network call). Reject codes are build/lint guards, not HTTP:
         behavior_regression · untokenized_value · a11y_floor_violation · unlisted_dependency
Schema: NONE TOUCHED. No DB table, migration, BFF route, or gateway contract changes.
```

Status: FROZEN @ v1 — approved by Tin (delegated auto mode, presentation-only, no security surface)

**Least-sure flag surfaced at freeze:** `[test]` — the Recharts chart's renderability in jsdom.
Recharts sizes itself from measured DOM width, which jsdom reports as 0, so `ResponsiveContainer`
commonly suppresses its SVG children in unit tests. *Why it matters:* if I assert on SVG
path/point geometry the test flakes or hangs. *Cost if wrong:* none to data or contract — I assert
on the `<figure aria-label>` wrapper + the preserved fallback list (or give the chart a fixed test
width), which is a test-authoring choice already anticipated in §1's ⚠ assumption. Second-most
unsure `[contract]`: the chart wrapper tag/testid (`<figure data-testid="spend-chart">`) is newly
introduced here — if a future a11y test wants a different element, it changes only this additive leaf.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥ 80% (hold the v13 task-1 line; do not regress the 88.79% baseline)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - The 12 restyle/preserve scenarios are ALREADY covered by the green behavioral suites
    (`apps/dashboard/tests/usage.test.tsx` T20–35, `apps/dashboard/tests-bff/govern.test.tsx`
    SpendPage T12–17) — they key on roles/aria/text/testids, NOT CSS, so they act as the
    behavior_regression guard during the restyle. Build must keep all of them green (zero edit).
  - NEW red test (the only failing-first work): `test_spend_chart_accessible` — arrange admin-spend
    resolves with ≥2 buckets / act render SpendPage / assert a `<figure data-testid="spend-chart"
    aria-label="Spend over time">` with a title is present AND the existing spend-bucket list +
    totals-* leaves still render (chart not sole data source). Runs red (SpendSparkline absent).
  - NEW red test `test_spend_chart_absent_when_empty` — arrange 0 buckets / assert spend-chart NOT
    rendered, spend-zero-state path unchanged.
  - R3 untokenized-value guard + R6 deps allow-list (design-system/*) already scan all surfaces —
    extended assertion set, no new file.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/spend-chart.test.tsx` · MUST run red (SpendSparkline missing) before Build. (SpendPage's bffGet hits localhost:3000/api/gw — only the tests-bff msw server intercepts it, so the chart test belongs in the bff project, not tests/.)
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/usage/` `apps/dashboard/components/spend/` `apps/dashboard/tests-bff/spend-chart.test.tsx` `apps/dashboard/tests/design-system/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/usage-cost-ui/`
Strategy (ordered batches):
  1. RED: add `tests/spend-chart.test.tsx` (chart figure + fallback) — runs red (SpendSparkline absent).
  2. Build `components/spend/SpendSparkline.tsx` (recharts LineChart in <figure aria-label>, derives from buckets) → red test green.
  3. Restyle surface-by-surface onto `components/ui/*`, ONE component at a time, re-running usage.test.tsx + govern.test.tsx after each: UsageStatsCards → UsageTable → BudgetWidget → BudgetEditForm → UsagePage → SpendPage (mount the chart). Keep every role/aria/text/testid.
  4. Run R3 token guard + R6 deps allow-list; run full vitest with coverage; next lint.
Safety rule (feature-specific): preserve the test-observable surface BYTE-IDENTICAL — restyle is class/wrapper-primitive only; never touch a query key, route, field name, or marker. The chart is additive and must never become the sole source of a datum (fallback list always rendered).
Code lives in: `apps/dashboard/components/{usage,spend}/`
Constraints: do NOT change any existing test or the contract; allow-list packages only (recharts already listed; no second chart lib); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 105/105 vitest (103 prior + 2 new chart tests), confirmed in BOTH run orders (govern↔spend-chart, 20/20 each way → no order-dependent flake).
- [x] coverage did not decrease — 89.41% global lines (1697/1898), ABOVE the v13 task-1 baseline of 88.79% and the 80% gate. Touched files: SpendSparkline 100%, SpendPage 98.5%, UsagePage 100%, UsageTable 100%, BudgetEditForm 96.4%, UsageStatsCards 93.0%, BudgetWidget 86.8%.
- [x] no test or contract was altered during build — tripwire clean (`add.py check`: 0 failed); the 2 behavioral suites (usage.test.tsx, govern.test.tsx) and the frozen §3 are byte-unchanged. The re-cross tests→build was a SNAPSHOT re-baseline (to admit the gitignored tsconfig.tsbuildinfo into §5), not a test/contract edit.
- [x] the green was EARNED — adversarial refute-read (subagent, model sonnet) returned VERDICT: EARNED, zero defects across 6 areas (data-seam byte-identical, markers preserved, chart tests non-vacuous + genuinely additive, leak fix sound, no overfit, a11y pattern valid). Confirmed `budget_usd_monthly` (not monthly_budget_usd) and the `enabled:!!usageQuery.data` /0\.00/ guard intact.
- [x] concurrency / timing safe — presentation-only; no new async/IO. The one timing subtlety (Recharts' imperative `recharts_measurement_span` leaking across tests) is handled by an unmount cleanup in SpendSparkline; verified by passing both run orders.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secrets touched; no new dep (recharts already allow-listed in v13 task 1; `check_node_deps` clean). No unsafe raw-HTML injection sink introduced. Chart stroke uses `var(--color-primary)` token, no raw hex/px in surfaces (R3 holds).
- [x] layering & dependencies follow CONVENTIONS.md — surfaces consume `components/ui/*` primitives + shared state components; native `<select>` deliberately kept (BFF tests use userEvent.selectOptions); data hooks unchanged.
- [x] a person reviewed — delegated auto mode (presentation-only, no security surface); adversarial subagent stands in for the refute-read. No security/concurrency/architecture residue → auto-PASS per `autonomy: auto`.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `SpendSparkline` is imported + mounted in SpendPage.tsx (inside spend-data, before the bucket list); all shared primitives (Card/Table/Button/Input/Loading/ErrorState/Empty) imported from `@/components/ui` and rendered. Confirmed via the passing chart test (figure present) + 105/105.
- [x] DEAD-CODE (code) — no orphaned symbol; removed the unused `CardTitle` import from UsagePage after switching to a styled `<h2>` in CardHeader. tsc clean on all touched files (only pre-existing test-file errors remain).
- [x] SEMANTIC (prose / non-code) — read the frozen §3 + §0 markers in full; confirmed every enumerated role/aria/text/data-testid is emitted by the restyled output and every query key/route/field name is unchanged.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (delegated auto mode) + adversarial refute-read subagent · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
