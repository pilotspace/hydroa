# TASK: Redesign monitoring pages (usage · spend · slo · health) to the refreshed standard

slug: monitoring-pages-redesign · created: 2026-06-28 · stage: production
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

Touches (files · symbols · signatures):
- `apps/dashboard/app/(app)/app/usage/page.tsx` · `app/(app)/app/spend/page.tsx` · `…/slo/page.tsx` · `…/health/page.tsx` — thin SERVER shells, each `return <XPage />` + `metadata`; presentation lives in the client components below (untouched seam-wise).
- `apps/dashboard/components/usage/UsagePage.tsx` — `"use client"`; bare `<h1>Usage & Cost Analytics</h1>` + 4 `<section>`s (summary StatCards · records table · budget · model catalog). Hooks: `["admin-usage"]`→`GET /api/gw/admin/usage`, `["catalog-models"]`, `["admin-budget"]`, `useCurrentUser()`. Loading/error via `UsageStatsCards`; empty via `DataTable emptyMessage`.
- `apps/dashboard/components/spend/SpendPage.tsx` — `"use client"`; `data-testid="spend-page"`, bare `<h1>Spend Analytics</h1>`, native `<select>` filter bar, all four states EXPLICIT (`spend-loading`/`spend-error`/`spend-zero-state`/`spend-data`). Hook `["admin-spend",window,groupBy,keyId]` + `["admin-keys"]`; `keepPreviousData`+`lastGood` → error is INLINE banner, never page-replacing. Frozen `valueTestId` totals-cost/requests/prompt/completion + `spend-chart`/`spend-bucket` + `<h2>Totals ({window})</h2>`.
- `apps/dashboard/components/slo/SloPage.tsx` — `"use client"`; `<section aria-labelledby="slo-heading">`, `<h1 id="slo-heading">Service levels</h1>`, window `role="group"` aria-pressed buttons (24/168/720h), `SloMetrics` sub: 3 StatCards (`slo-availability`/`slo-error-rate`/`slo-total-requests`) + breakdown `<dl>` + latency placeholder. Hook `["admin-slo",windowHours]`. NO distinct Empty — zero-window = valid success (test_slo_empty_window pins 100%/0%/0).
- `apps/dashboard/components/health/HealthPage.tsx` — `"use client"`; `<section aria-labelledby="health-heading">`, `<h1>Upstream Health</h1>`, `UpstreamsTable`→`DataTable` (Upstream|Status|Last event, `emptyMessage="No upstreams monitored"`). Hook `["admin-upstream-health"]`.
- SHARED primitives (consume, don't fork): `components/ui/stat-card.tsx` `StatCard{label,value,delta,icon,footer,valueTestId}` (`data-slot="stat-card"`, label CSS-`uppercase`, value `text-3xl`); `components/ui/states.tsx` `Loading`(role=status,aria-busy)/`ErrorState`(role=alert)/`Empty`/`Success`; `components/ui/app-shell.tsx` AppShell (fixed-viewport, owns scroll); `components/ui/card.tsx`, `components/ui/data-table.tsx` `DataTable{columns,data,ariaLabel,emptyMessage}`.
- NEW primitive to introduce: `apps/dashboard/components/ui/page-header.tsx` `PageHeader` — DOES NOT EXIST today; every page hand-rolls `<h1 className="text-2xl font-semibold tracking-tight">`. The structural-consistency lever for this task.
- Tests: `tests/usage.test.tsx`(13) · `tests/slo-page.test.tsx`(7) · `tests/health.test.tsx`(5) · `tests-bff/spend-chart.test.tsx` · `tests-bff/spend-breakdown.test.tsx` · `tests-bff/console-spend-redesign.test.tsx`; structural design-system tests in `tests/design-system/`.

Context (working folder): `.add/milestones/v54/MILESTONE.md` (Scope · Shared decisions · the exit criterion "monitoring pages … redesigned layout + four states") · `tmp/captures/` v54 admin captures · `apps/dashboard/vitest.config.ts` (two projects: `tests/` legacy + `tests-bff/`, both base http://localhost:3000, coverage ≥80% lines).

Honors (patterns / conventions): PROJECT.md UDD invariants — 3-layer DTCG tokens fail-closed · byte-identical data seams · four UI states · WCAG 2.2 AA · design-before-code. CONVENTIONS.md — exactly one `<h1>`/route · decorative icons `aria-hidden` · `role=status`/`role=alert` · scope assertions via `within(<section>)` · loading must RESOLVE (findBy + queryByRole status absent). v54 MILESTONE shared decisions — byte-identical seams (query keys/BFF paths/field names/frozen testids inviolable) · token-led no-hardcode (`add.py check` lints fail-closed) · four-state from `states.tsx` (reuse) · responsive as `sm:`/`lg:` presence-proxies in jsdom · native `<select>` on Spend preserved for `userEvent.selectOptions` · Spend error stays inline (keepPreviousData+lastGood).

Anchors the contract cites: `PageHeader` (new shared primitive) · `UsagePage` · `SpendPage` · `SloPage` · `HealthPage` · `StatCard` · `states.tsx`(`Loading`/`ErrorState`/`Empty`) · the frozen seam set (query keys `admin-usage`/`admin-spend`/`admin-slo`/`admin-upstream-health`; testids `spend-page`/`spend-loading`/`spend-error`/`spend-zero-state`/`spend-data`/`spend-chart`/`spend-bucket`/`totals-*`/`slo-*`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Monitoring pages (usage · spend · slo · health) redesigned to a refreshed, consistent layout standard — a shared PageHeader (title + description + actions), a per-page hero metric, a tabbed information architecture, and a real trend chart where the seam already carries time-series — with every data seam and frozen test hook byte-identical and all four UI states present.
Framings weighed: Full per-page rethink — shared PageHeader + hero + tabbed IA + charts-where-data-exists (chosen, Tin) · PageHeader + Card sections (lighter) · PageHeader only (lightest)
Must:
<must>
  - Introduce ONE shared `PageHeader` primitive: an h1 title + optional muted description + a right-aligned actions slot. All four pages render their heading through it (exactly one h1 per page; pinned heading text — "Spend Analytics", "Service levels", "Upstream Health", "Usage & Cost Analytics" — preserved).
  - Each page presents a hero metric (its headline number) and a tabbed IA (reuse `components/ui/tabs.tsx`), composed from the Aurora token kit (StatCard · Card · states.tsx · DataTable · recharts) with no hardcoded token-covered value.
  - Per-page IA: USAGE → Overview (4 StatCards + budget) │ Records (usage table) │ Catalog (model catalog) │ Trends (recharts per-day cost from records[].created_at). SPEND → Overview (totals StatCards + buckets sparkline) │ Breakdown (key/team table). SLO → Overview (3 StatCards + request breakdown) │ Latency. HEALTH → Overview (upstreams table) with an up/down summary hero.
  - Render a REAL trend chart only where the seam already provides series: Spend (`buckets[]`, retain its sparkline) and Usage (derive per-day cost from `records[].created_at` — a real field, confirmed). Use recharts (already a dependency) — no new dependency.
  - SLO + Health show the chart-ready hero + tabs but NO fabricated trend; the missing time-series is surfaced honestly ("not available yet", same pattern as SLO latency) and the backend seam is DEFERRED to new backend tasks (seeded as spec deltas).
  - All four UI states (loading · empty · error · success) on each page, composed from `states.tsx` (`Loading` role=status · `ErrorState` role=alert · `Empty`); SLO's zero-window stays a valid SUCCESS sub-state (no Empty), matching the frozen test.
  - Byte-identical seams: every query key (`admin-usage`/`admin-spend`/`admin-slo`/`admin-upstream-health`/`catalog-models`/`admin-budget`/`admin-keys`), BFF path, and response field name is unchanged; every frozen test hook (`spend-page`/`spend-loading`/`spend-error`/`spend-zero-state`/`spend-data`/`spend-chart`/`spend-bucket`/`totals-*`/`slo-*`, the `Totals ({window})` h2, the native `<select>`s on Spend) is preserved.
  - When the redesign relocates a frozen metric/region behind a non-default tab, the affected test is co-evolved to NAVIGATE to that tab (add a `userEvent.click(tab)`) — never weakened; seam + assertion target unchanged.
  - a11y: decorative icons `aria-hidden`; tablist via the WAI-ARIA `tabs.tsx` (roving tabindex, arrow-key nav); responsive asserted as `sm:`/`lg:` presence-proxy classes (real-viewport check defers to the standing Playwright residue).
</must>
Reject:
<reject>
  - Rendering a trend/metric from data the seam does not provide -> "fabricated_metric" (HONEST-DEGRADE: show "not available yet" instead of inventing a series).
  - Renaming or removing a frozen data seam or test hook -> "seam_broken" (the redesign must preserve every query key / BFF path / field name / testid).
  - Hardcoding a token-covered value in a page or primitive -> "untokened_value" (`add.py check` lints the 3-layer set fail-closed).
  - A page missing one of the required UI states -> "missing_ui_state".
  - Weakening (vs navigating) a test to absorb the new tabbed DOM -> "test_weakened" (a relocated assertion must click into its tab, not be deleted/loosened).
</reject>
After:
<after>
  - All four monitoring pages render the refreshed Aurora layout standard through the shared PageHeader + primitive kit; each handles loading/empty/error/success; Spend & Usage show a real trend chart, SLO & Health degrade honestly; every data seam + frozen hook is unchanged; the full vitest suite (legacy + bff + design-system) is green and `add.py check` is clean.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The tabbed IA can absorb the frozen structural tests via NAVIGATION alone (no weakening) — lowest confidence because `tabs.tsx` returns null for inactive panels, so every relocated frozen testid must be reached by a click and a few tests assert cross-region simultaneity (`test_usage_renders_cards_and_table`); if wrong: more tests need editing than expected, or a metric must stay on the default tab. Cost: medium (test rework, caught at the tests phase). Mitigation: keep each page's PRIMARY pinned metrics on the default Overview tab; relocate only secondary regions.
  - [x] `UsageRecord` carries a usable timestamp for a real trend — CONFIRMED: `created_at: string` (UsageStatsCards.tsx:10) → per-day cost trend is real, not fabricated.
  - [x] A Tabs primitive + a chart lib exist within the no-new-dependency rule — CONFIRMED: `components/ui/tabs.tsx` (custom WAI-ARIA, no dep) + `recharts ^3.8.1` already in package.json; `SpendSparkline.tsx` to reuse/generalize.
  - [x] Charts-everywhere needs backend seams for SLO/Health — CONFIRMED out of scope here (UI-only, byte-identical seams) → deferred to two new backend tasks (spec deltas), structure built chart-ready now.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Shared PageHeader on every monitoring page   # Must 1
  Given any of the four monitoring pages is rendered with data
  When the page mounts
  Then its heading is rendered through the shared PageHeader (one h1, the pinned title text, an optional muted description, an actions slot)
  And exactly one <h1> exists on the page

Scenario: Hero + tabbed IA composed from the token kit   # Must 2 & 3
  Given the SLO page is rendered with data
  When it mounts
  Then a hero metric region shows availability and a WAI-ARIA tablist (role=tablist) exposes the page's tabs (Overview default)
  And the StatCards carry data-slot="stat-card" and no element hardcodes a token-covered hex/px value

Scenario: Real trend chart where the seam carries series   # Must 4
  Given the Usage page has usage records with created_at timestamps
  When the user opens the Trends tab
  Then a recharts trend of per-day cost renders from records[].created_at
  And no new runtime dependency was added (recharts already present)

Scenario: Honest degrade where no time-series seam exists   # Must 5 & Reject fabricated_metric
  Given the SLO page (its seam returns only an aggregate snapshot)
  When the user looks for a trend
  Then the page shows an honest "not available yet" affordance and NO fabricated trend series is drawn
  And the availability/error-rate/requests StatCards remain accurate

Scenario: Four UI states on every page   # Must 6
  Given a monitoring page
  When its query is loading / errors / returns empty / returns data
  Then it renders Loading (role=status) / ErrorState (role=alert) / an empty affordance / the success layout respectively
  And SLO's zero-window renders the success layout (100%/0%/0), not an Empty

Scenario: Data seams and frozen hooks stay byte-identical   # Must 7 & Reject seam_broken
  Given the redesigned pages
  When the suite runs
  Then every query key, BFF path, response field name, and frozen testid (spend-page/spend-chart/totals-*/slo-*/…, the "Totals ({window})" h2, native <select>s) is unchanged
  And no behavioral/seam test was renamed or removed

Scenario: Relocated assertion navigates, never weakens   # Must 8 & Reject test_weakened
  Given a frozen metric moved behind a non-default tab (e.g. usage records → Records tab)
  When its existing test runs
  Then the test clicks into that tab (userEvent.click) before asserting the same target
  And the assertion target and seam are unchanged (navigation added, nothing loosened or deleted)

Scenario: a11y of the new structure   # Must 9
  Given any redesigned page
  When axe scans it
  Then there are 0 serious/critical violations (color-contrast disabled per the standing residue), decorative icons carry aria-hidden, and the tablist supports arrow-key roving focus

Scenario: No untokened value slips in   # Reject untokened_value
  Given the redesigned pages and the new PageHeader primitive
  When `add.py check` and the design-system token tests run
  Then no components/ui/* file contains a raw hex or 'NNpx' literal a token covers
  And the 3-layer DTCG set resolves fail-closed

Scenario: No page is missing a state   # Reject missing_ui_state
  Given each of the four pages
  When its four states are exercised
  Then loading, error, success are present on all four and empty is present where applicable (SLO zero-window = success by design)
  And a page that cannot reach a state documents why (SLO no-Empty)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
STRUCTURAL CONTRACT — UI-only; no HTTP method/schema changes (every data seam is byte-identical).

NEW PRIMITIVE — PageHeader (apps/dashboard/components/ui/page-header.tsx)
  <PageHeader title description? actions? className? />
    title:       string          -> the single <h1> (text-2xl font-semibold tracking-tight); pages pass their PINNED text
    description?: React.ReactNode -> muted one-line subtitle (text-sm text-muted-foreground), omitted when absent
    actions?:    React.ReactNode -> right-aligned slot (window selectors / Edit Budget / timestamp); responsive sm: row
    Renders: <header> with title row (h1 + actions) over an optional description. Token utilities only (R3-safe, no raw hex/px).

PER-PAGE TAB STRUCTURE (reuse components/ui/tabs.tsx; default tab listed first; ★ = frozen testid/region that MUST live on its tab)
  USAGE   tabs: Overview★(4 StatCards + BudgetWidget) │ Records(UsageTable) │ Catalog(ModelCatalogTable) │ Trends(recharts per-day cost)
          hero: total cost (admin-usage.total_cost_usd)
  SPEND   tabs: Overview★(Totals StatCards totals-* + SpendSparkline spend-chart + buckets spend-bucket) │ Breakdown(key/team DataTable)
          page-level (OUTSIDE tabs): spend-page, spend-loading, spend-error, spend-zero-state, spend-data, window-selector, group-by, key-filter
          hero: cost for window (totals.cost_usd)
  SLO     tabs: Overview★(3 StatCards slo-availability/slo-error-rate/slo-total-requests + request-breakdown dl) │ Latency("not available yet")
          hero: availability; window buttons (24/168/720h role=group) in PageHeader actions
  HEALTH  tabs: Overview★(UpstreamsTable DataTable) ; up/down summary hero; checked_at in PageHeader actions
          (HEALTH may stay single-region under the hero if a 2nd tab adds nothing real — History deferred)

FROZEN (byte-identical — the redesign preserves all):
  query keys:  admin-usage · admin-spend · admin-slo · admin-upstream-health · catalog-models · admin-budget · admin-keys
  BFF paths:   GET /api/gw/admin/{usage,spend,slo,health/upstreams,catalog/models,budget,keys}  (+ PUT budget)
  field names: every response field unchanged (UsageData/SpendWindowResponse/SloData/UpstreamHealthData)
  test hooks:  data-testid spend-page/spend-loading/spend-error/spend-zero-state/spend-data/spend-chart/spend-bucket/window-selector ·
               valueTestId totals-cost/totals-requests/totals-prompt/totals-completion/slo-availability/slo-error-rate/slo-total-requests ·
               native <select> on Spend (window/group-by/key) · the <h2>Totals ({window})</h2> heading · StatCard data-slot="stat-card"
  states:      Loading(role=status) · ErrorState(role=alert) · Empty ; SLO zero-window = SUCCESS sub-state (no Empty)
  invariants:  exactly one <h1> per page (now the PageHeader's) · decorative icons aria-hidden · Spend error stays INLINE (keepPreviousData+lastGood)

CHARTS: recharts (already a dep) — REAL series only. Spend retains SpendSparkline (buckets[]); Usage derives per-day cost from records[].created_at.
        SLO + Health: NO fabricated trend → honest "not available yet"; backend time-series seam DEFERRED to two new tasks (spec deltas).

ERROR CODES (build-time guard conditions, asserted by tests): fabricated_metric · seam_broken · untokened_value · missing_ui_state · test_weakened.

DEFERRED (seeded as §7 spec deltas — NOT built here):
  [SPEC] /admin/slo time-series buckets seam (backend) → enables the SLO availability/error-rate trend chart
  [SPEC] upstream-health history seam (backend)        → enables the Health uptime trend chart
```

Least-sure flag surfaced at freeze: ⚠ [test] the tabbed IA must absorb the frozen structural tests by NAVIGATION alone — `tabs.tsx` drops inactive panels from the DOM, so every relocated frozen testid (`slo-*`, `totals-*`, the usage records row) gets a `userEvent.click(tab)` added; seams + assertion targets stay byte-identical (never weakened). If wrong: a few more tests need editing than estimated (caught at the tests phase). Every other point — charts only where the seam carries series, honest "not available yet" for SLO/Health, recharts + tabs already deps, `records[].created_at` real — is confirmed against real code. Design-confirm: 4 captures approved by Tin (`.add/design/captures/monitoring-*.png`).

Status: FROZEN @ v1 — approved by Tin
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥80% lines (project gate); new components/pages at parity or better.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  NEW — design-system structural suite `tests/design-system/monitoring-redesign.test.tsx`:
  - test_page_header_primitive: render PageHeader{title,description,actions} → one <h1> w/ title, muted description node, actions slot rendered; no raw hex/px (R3). (Must 1)
  - test_each_page_uses_page_header: usage/spend/slo/health each render their PINNED h1 via PageHeader; exactly one <h1>. (Must 1)
  - test_each_page_has_tablist_and_hero: each page exposes role=tablist (Overview default selected) + a hero metric region. (Must 2/3)
  - test_usage_trends_tab_renders_recharts: click Trends → a chart (recharts container) from records[].created_at; no chart when records empty. (Must 4)
  - test_slo_health_no_fabricated_trend: SLO & Health show a "not available yet" trend affordance, NO chart svg in that region. (Must 5, Reject fabricated_metric)
  - test_no_untokened_value: PageHeader + page files contain no raw hex/'NNpx' a token covers. (Reject untokened_value)
  NEW — a11y: extend `tests/design-system/a11y` or add cases: tablist arrow-key roving + 0 serious/critical axe (color-contrast off). (Must 9)
  CO-EVOLVED (navigation added, seam/asserts unchanged — Must 8, Reject test_weakened):
  - tests/usage.test.tsx: records-table + catalog assertions → click Records/Catalog tab first; StatCards/budget stay on Overview.
  - tests/slo-page.test.tsx: breakdown/latency → ensure on Overview/Latency tab; slo-* StatCards stay default.
  - tests/health.test.tsx: table rows on Overview (default) — likely unchanged; empty/error/loading at page level.
  - tests-bff/spend-*.test.tsx: spend-chart/spend-bucket/breakdown → click Overview/Breakdown tab; spend-page/loading/error/zero-state stay page-level.
  - tests/ui-ux-verify.test.tsx: the usage axe-journey asserts a record's model_id → click the Records tab first (records moved behind the tab); the hero cost + axe scan stay. (Must 8 — found during build refute-read; the prior build leaned on hidden overview model-id spans, removed in favour of this honest navigation.)
  UNCHANGED FLOOR (must stay green verbatim): every query-key/BFF-path/field-name assertion; totals-*/slo-*/spend-* testids; the `Totals ({window})` h2; native <select>s; states role=status/role=alert; SLO zero-window=success.
</test_plan>

Tests live in: `tests/design-system/monitoring-redesign.test.tsx` `tests/usage.test.tsx` `tests/slo-page.test.tsx` `tests/health.test.tsx` `tests/ui-ux-verify.test.tsx` `tests-bff/spend-chart.test.tsx` `tests-bff/spend-breakdown.test.tsx` `tests-bff/console-spend-redesign.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/page-header.tsx` `apps/dashboard/components/usage/` `apps/dashboard/components/spend/` `apps/dashboard/components/slo/` `apps/dashboard/components/health/` `apps/dashboard/components/models/ModelCatalogTable.tsx` `apps/dashboard/tests/design-system/monitoring-redesign.test.tsx` `apps/dashboard/tests/design-system/a11y.test.tsx` `apps/dashboard/tests/design-system/components.test.tsx` `apps/dashboard/tests/usage.test.tsx` `apps/dashboard/tests/slo-page.test.tsx` `apps/dashboard/tests/health.test.tsx` `apps/dashboard/tests/ui-ux-verify.test.tsx` `apps/dashboard/tests-bff/spend-chart.test.tsx` `apps/dashboard/tests-bff/spend-breakdown.test.tsx` `apps/dashboard/tests-bff/console-spend-redesign.test.tsx`
Strategy (ordered batches): 1. NEW PageHeader primitive (token-only) + its red test → green. 2. USAGE: tabbed IA (Overview default = StatCards + budget; Records/Catalog/Trends) + UsageTrend recharts from records[].created_at; co-evolve usage.test.tsx (nav to Records/Catalog). 3. SPEND: PageHeader (selects in actions) + hero + Overview/Breakdown tabs; keep SpendSparkline + all spend-* testids; co-evolve spend-*.test. 4. SLO: PageHeader (window buttons in actions) + availability hero + Overview/Latency tabs + honest "not available yet"; co-evolve slo-page.test. 5. HEALTH: PageHeader (checked_at) + up/down hero + Overview table; honest "History not available". 6. green full suite (legacy+bff+design-system) + tsc + eslint + build + `add.py check`; capture real pages at verify.
Known-problem fixes: tabs.tsx inactive panel→null (relocated frozen testids → co-evolve test with userEvent.click(tab), NEVER weaken) · R3 no raw hex/px in components/ui/* (PageHeader token utilities only) · recharts in jsdom needs ResponsiveContainer width/height or a fixed size (mock ResizeObserver / give explicit dims) · honest-degrade: SLO/Health render NO chart svg, only a "not available yet" node · keep native <select> on Spend (no shadcn Select) · Spend error stays INLINE (keepPreviousData+lastGood) · SLO zero-window stays SUCCESS (no Empty).
Strategy actually used: As planned — NEW token-only `PageHeader` primitive; each of usage/spend/slo/health recomposed into PageHeader + a `data-testid="<page>-hero"` region + a tablist (Overview default). Usage gained a `UsageTrend` recharts line (fixed 560×220, no ResponsiveContainer, per the SpendSparkline jsdom pattern) summing records[].created_at × cost_usd per UTC day. SLO/Health render honest "not available yet" notes (NO svg) — their time-series seams are DEFERRED to §7 spec deltas. Co-evolution was navigation-only (userEvent.click(tab) before a relocated assertion); zero seams/testids changed. ONE deviation surfaced by the verify refute-read and corrected here: the first build pass had leaned on hidden `sr-only` model-id spans on the Usage overview tab purely so `ui-ux-verify.test.tsx` asserted a record id without a tab switch — an overfit crutch. Removed it and co-evolved `ui-ux-verify.test.tsx` to navigate to the Records tab (declared in §4/§5 + re-crossed). The `ModelCatalogTable` sr-only bare-id span is KEPT (it is the legitimate exact-match target for the catalog test, whose visible node reads "ID: <id>") with its rationale comment corrected.
Safety rule (feature-specific): every data seam (query key · BFF path · response field) and every frozen test hook stays BYTE-IDENTICAL; a relocated assertion is reached by navigation, never by loosening it.
Code lives in: `apps/dashboard/components/{ui/page-header,usage,spend,slo,health}`
Constraints: do NOT change a data seam or weaken a test; reuse the existing primitive kit (StatCard·Card·states·DataTable·tabs); recharts only (already a dep); no new dependency; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `npx vitest run` (both projects): PASS (774) FAIL (0).
- [x] coverage did not decrease — co-evolution was navigation-only and the overfit fix swapped hidden DOM for a tab click (no assertion removed); per-project ≥80% gate held (suite green includes the coverage thresholds).
- [x] no test or contract was altered during build — §3 frozen; the build wrote only source. The ONE test change (ui-ux-verify Records-tab nav) was made in the tests lane, declared in §4/§5, and re-crossed (`add.py phase build`) so the tripwire re-baselined; `add.py check` reports no contract/test tamper for this task.
- [x] the green was EARNED, not gamed — refute-read (0.91) flagged exactly one overfit: hidden `sr-only` model-id spans on the Usage overview tab existing only so `ui-ux-verify.test.tsx` skipped a tab switch. REMOVED + the test honestly co-evolved to navigate to the Records tab. The kept `ModelCatalogTable` sr-only span is a real exact-match target (visible node reads "ID: <id>"), not a crutch. No vacuous asserts; honest-degrade is real (SLO/Health render NO svg).
- [x] concurrency / timing of the risky operation is safe — UI-only; no new async/shared state. SpendPage's render-time tab-sync setState was refute-confirmed convergent (no loop). recharts renders with `isAnimationActive={false}` / fixed dims (deterministic in jsdom).
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new dependency (recharts already a dep); no secrets; all data still flows through the existing BFF query seams unchanged.
- [x] layering & dependencies follow CONVENTIONS.md — PageHeader is a token-only `components/ui/*` primitive (no raw hex/px, R3); pages compose the existing kit (Card/StatCard/states/DataTable/tabs); no cross-layer reach.
- [x] a person reviewed and approved the change — evidence-based auto-gate under `autonomy: auto` (non-security, non-architecture UI change); Tin reviews at the commit checkpoint (commit permission requested in the same turn).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] Each of the 4 pages renders the shared PageHeader — one h1 + muted description + right-aligned actions — confirmed by `monitoring-redesign.test` (`test_page_header_primitive`, `test_each_page_uses_page_header`) green + the four `.add/design/captures/monitoring-{usage,spend,slo,health}.png` component-render captures (user-confirmed pre-build).
- [x] Each page exposes a tablist with Overview default + the spec labels — confirmed by `test_each_page_has_tablist_and_hero` + the per-page tab-presence assertions (usage Overview/Records/Catalog/Trends; spend Overview/Breakdown; slo Overview/Latency; health Overview/History) all green.
- [x] Each page shows a `data-testid="<page>-hero"` region with its headline metric — confirmed by the four hero `findByTestId` + `within()` tests green.
- [x] Usage Trends tab renders a recharts trend derived from records[].created_at; empty records → no-data note + NO chart — confirmed by `test_usage_trends_tab_renders_recharts` + the empty-records case green.
- [x] SLO and Health show an honest "not available yet" affordance with NO chart svg — confirmed by `test_slo_health_no_fabricated_trend` (asserts absence of svg) green + the SLO/Health captures.
- [x] Every frozen data seam + testid is byte-identical — full legacy+bff+design-system suite PASS (774); co-evolution diffs are click-only (userEvent.click(tab)); `add.py check` shows no scope/tamper/contract finding for this task (the 36 check FAILs are the pre-existing unrelated `chat-workspace-page` `.pen` prototype lint).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `PageHeader` imported+rendered by all 4 pages; `UsageTrend` rendered in the Usage Trends panel; the `<page>-hero` regions + tab panels are asserted by the suite. `npx tsc --noEmit` = "No errors found" (no dangling imports), `eslint .` = 0 errors (the lone warning is pre-existing in untouched `data-table.tsx`).
- [x] DEAD-CODE (code) — no new unused/orphaned symbol: the removed overview sr-only spans deleted dead test-only DOM; PageHeader/UsageTrend are both consumed; `no-unused-vars` (eslint) clean on touched files.
- [x] SEMANTIC (prose / non-code) — read TASK.md §3 CONTRACT in full: every frozen testid + data seam in the build matches §3 verbatim; the honest-degrade promise (SLO/Health no fabricated metric) is upheld in code (no svg) and asserted by the suite.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: evidence-based auto-gate (autonomy:auto) — non-security, non-architecture UI change; Tin confirms at the commit checkpoint · date: 2026-06-28

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose Full per-page rethink — shared PageHeader + hero + tabbed IA + charts-where-data-exists; rejected PageHeader + Card sections (lighter) · PageHeader only (lightest)
- [human] freeze — froze §3 @ v1 (approved by Tin)
- [AI] build — strategy used: As planned — NEW token-only `PageHeader` primitive; each of usage/spend/slo/health recomposed into PageHeader + a `data-testid="<page>-hero"` region + a tablist (Overview default). Usage gained a `UsageTrend` recharts line (fixed 560×220, no ResponsiveContainer, per the SpendSparkline jsdom pattern) summing records[].created_at × cost_usd per UTC day. SLO/Health render honest "not available yet" notes (NO svg) — their time-series seams are DEFERRED to §7 spec deltas. Co-evolution was navigation-only (userEvent.click(tab) before a relocated assertion); zero seams/testids changed. ONE deviation surfaced by the verify refute-read and corrected here: the first build pass had leaned on hidden `sr-only` model-id spans on the Usage overview tab purely so `ui-ux-verify.test.tsx` asserted a record id without a tab switch — an overfit crutch. Removed it and co-evolved `ui-ux-verify.test.tsx` to navigate to the Records tab (declared in §4/§5 + re-crossed). The `ModelCatalogTable` sr-only bare-id span is KEPT (it is the legitimate exact-match target for the catalog test, whose visible node reads "ID: <id>") with its rationale comment corrected.
- [AI] verify — gate PASS (reviewed by evidence-based auto-gate (autonomy:auto) — non-security, non-architecture UI change; Tin confirms at the commit checkpoint)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] /admin/slo time-series buckets seam — extend the SLO read model with a bucketed availability/error-rate (and, if cheap, latency) series so the SLO Overview trend + Latency tab chart real data instead of the honest "not available yet" placeholder shipped here (evidence: monitoring redesign deferred SLO time-series per the freeze decision; latency_ms is null today, never fabricated).
- [SPEC · open] upstream-health history seam — persist and expose a per-upstream status history (checked_at × up/down) so the Health "History" tab charts real uptime instead of the honest "not available yet" placeholder shipped here (evidence: monitoring redesign deferred Health history per the freeze decision; only live rows render today).
- [SPEC · open] monitoring real-page captures through the live edge — the four verify captures are component-render (real components + mock data), not full-stack-through-Envoy; capture the built pages against a running dev stack once the agent-browser daemon is unwedged / via Playwright, to close the browser-render residue (evidence: agent-browser daemon returned os error 35 during this task; structural + a11y proven in jsdom).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
