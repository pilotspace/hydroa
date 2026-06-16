# TASK: Restyle Usage/Spend/Keys to shared shadcn blocks

slug: console-surfaces-redesign · created: 2026-06-15 · stage: production
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
- `apps/dashboard/components/ui/stat-card.tsx:StatCard` — KPI tile (label·value·delta·icon·footer). Root `<Card className="gap-0">`; value in `<div class="text-2xl font-semibold tracking-tight">`. NO data-slot, NO per-value testid hook today.
- `apps/dashboard/components/ui/data-table.tsx:DataTable<TData,TValue>` — sortable TanStack table over the v13 Table primitives; header cells are `<button>`; 0 rows → `<Empty>`. NO `aria-label`/`data-slot` passthrough today.
- `apps/dashboard/components/ui/chart.tsx:ChartContainer` — div `data-slot="chart"`, publishes `--color-<key>` vars from `config`; `cn("aspect-video w-full", className)`.
- `apps/dashboard/components/ui/{card,table}.tsx` — both `forwardRef` + spread `{...props}` to their root `<div>`/`<table>` ⇒ `data-slot`/`aria-label` pass through cleanly.
- USAGE (legacy project): `components/usage/UsageStatsCards.tsx:UsageStatsCards` (prop-driven; 4 hand-rolled `<Card>` tiles in `grid …sm:grid-cols-4`; `Loading data-testid="loading"`), `UsageTable.tsx:UsageTable` (prop-driven; raw `<Table>`, returns `null` on loading/error ⇒ 0 `role=row`; empty → `Empty "No usage records yet"`), `BudgetWidget.tsx:BudgetWidget` (prop-driven; 2 inline value spans + Edit button + `BudgetEditForm`), `UsagePage.tsx` (orchestrator; `modelsQuery.enabled = !!usageQuery.data` keeps catalog in a separate commit so `/0\.00/` stays a singleton).
- SPEND (bff project): `components/spend/SpendPage.tsx:SpendPage` (totals `<dl class="…sm:grid-cols-4">` with `<dd data-testid="totals-{cost,requests,prompt,completion}">`; "Totals ({window})" title; buckets `<ul><li data-testid="spend-bucket">`; native `<select>`×3; breakdown `<Table aria-label="Spend by {key,team}">`), `SpendSparkline.tsx:SpendSparkline` (prop-driven; `<figure data-testid="spend-chart" aria-label="Spend over time">`+`<figcaption>` wrapping a fixed-dim `<LineChart>`).
- KEYS (legacy + bff): `components/keys/KeysPage.tsx:KeysPage` — already on shared `Card`+`Table`+`Button`; each key is a `<Fragment>` of `KeyRow` + a sibling governance `<TableRow><TableCell colSpan=6>` (expand → `KeyGovernanceEditor`). The interleaved expand-row is incompatible with TanStack's flat column model ⇒ DataTable is the WRONG fit here.

Context (working folder): vitest projects (`vitest.config.ts`) — `legacy` = `tests/**` (usage/keys/design-system, MSW `tests/mocks`), `bff` = `tests-bff/**` (spend/govern, MSW `tests-bff/mocks`). Coverage `lines:80`, include `components/**`. Verify = `cd apps/dashboard && npm test`.
Honors (patterns / conventions): v13 frozen design system — 3-layer DTCG tokens; R3 (no raw hex/`Npx` in `components/ui/*`); R6 (deps ⊆ allowlist.json); `data-slot` is the established shadcn marker convention (already on ChartContainer). Presentation-only: every BFF route, queryKey, field name, and asserted display string stays byte-identical.
Anchors the contract cites: `StatCard` (+ new `valueTestId`, `data-slot="stat-card"`), `DataTable` (+ new `ariaLabel`, `data-slot="data-table"`), `ChartContainer`, `UsageStatsCards`, `UsageTable`, `BudgetWidget`, `SpendSparkline`, `SpendPage` totals/breakdown.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Restyle Usage · Spend · Keys onto the shared StatCard / DataTable / ChartContainer blocks — presentation-only, data seam byte-identical.
Framings weighed: extend-the-blocks-then-adopt (chosen — add tiny `data-slot`/`valueTestId`/`ariaLabel` seams to StatCard+DataTable so surfaces can adopt them without losing the frozen test hooks) · force-DataTable-everywhere (rejected — breaks Keys' interleaved governance expand-row + drops `totals-*`/`spend-by-*` aria hooks) · pure-CSS-reskin (rejected — leaves hand-rolled tiles/tables, fails the "render via the shared components" exit criterion).
Must:
<must>
  - StatCard gains an optional `valueTestId` (→ `data-testid` on the value node) and a stable `data-slot="stat-card"` marker; all existing props/behavior unchanged.
  - DataTable gains an optional `ariaLabel` (forwarded to its `<table>`) and a stable `data-slot="data-table"` marker; existing sort/empty behavior unchanged.
  - UsageStatsCards renders its 4 aggregates via `StatCard` inside the existing `grid …sm:grid-cols-4` wrapper; loading→`Loading data-testid="loading"`, error→`ErrorState`, identical value strings ("3"/"300"/"150"/"1.23").
  - UsageTable renders via `DataTable` (sortable headers, `emptyMessage="No usage records yet"`) and STILL returns nothing on loading/error (0 `role="row"`); cell text byte-identical (model_id, prompt_tokens, completion_tokens, cost_usd, status, created_at).
  - BudgetWidget renders ceiling + spent as two `StatCard`s; keeps the owner/admin "Edit Budget" button + inline `BudgetEditForm`; member sees no edit affordance; null ceiling → "Unlimited".
  - SpendSparkline wraps its `<LineChart>` in a `ChartContainer` (series color via `--color-*` var) while preserving the `<figure data-testid="spend-chart" aria-label="Spend over time">` + visible `<figcaption>`.
  - SpendPage totals render via 4 `StatCard`s carrying `valueTestId="totals-{cost,requests,prompt,completion}"` inside a `…sm:grid-cols-4` grid, under a preserved "Totals ({window})" heading; spend-by-key / spend-by-team render via `DataTable` (`ariaLabel`+caption "Spend by key"/"Spend by team"); buckets list + native selects + zero/error/loading branches unchanged.
  - KeysPage keeps its composed `Card`+`Table` (DataTable cannot model the governance expand-row); no behavioral change — documented decision, not an omission.
  - Every BFF route, queryKey, request field, and asserted display string stays byte-identical; both vitest projects stay green; coverage ≥ 80% lines.
</must>
Reject:
<reject>
  - any change to a BFF route / queryKey / request body / response field -> "data_seam_drift" (forbidden — presentation-only)
  - editing or weakening any pre-existing (frozen) test to make the restyle pass -> "frozen_test_tamper" (HARD-STOP)
  - swapping a native `<select>` for a non-native control, or dropping a `data-testid`/`aria-label`/role the frozen suites query -> "lost_test_hook"
  - introducing a raw hex / bare `Npx` in `components/ui/*` or a dep outside allowlist.json -> "design_system_violation" (R3/R6)
</reject>
After:
<after>
  - Usage · Spend render their KPIs via `StatCard`, their flat tables via `DataTable`, and Spend's chart via `ChartContainer`; Keys stays on shared `Card`+`Table`.
  - `npm test` (both projects) green; new red→green suite proves the adoption; coverage ≥ 80%.
  - `add.py check` still 37/0 (R3/R6/M6 intact); zero data-seam diff.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ A `<caption>`+`ariaLabel` on the DataTable yields the same accessible name the frozen `getByRole("table",{name:/spend by key/i})` query expects — lowest confidence because RTL's accname computation for `<table>` is the subtle bit; if wrong: the spend-breakdown suite goes red. Mitigation: forward `ariaLabel` straight to the `<table>` element (aria-label wins over caption), exactly mirroring the current raw-`<Table aria-label>` that already passes.
  - [x] StatCard swap keeps `/0\.00/` a singleton in the budget-null test — confirmed: UsagePage's `modelsQuery.enabled=!!usageQuery.data` sequencing is untouched, so catalog price leaves never co-commit with the budget "0.00".
  - [x] Returning `null` (not an empty DataTable) on UsageTable loading/error keeps `queryAllByRole("row")===0` — confirmed: the guard stays above the DataTable render.
  - [x] `data-slot` is a safe additive marker — confirmed: `Card`/`Table` spread `{...props}`; ChartContainer already uses the same convention; no frozen test asserts its absence.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: StatCard value test hook + slot
  Given a StatCard with valueTestId="totals-cost" value="1.23"
  When it renders
  Then getByTestId("totals-cost").textContent === "1.23"
  And the root carries data-slot="stat-card"

Scenario: DataTable accessible name + slot
  Given a DataTable with ariaLabel="Spend by key" over one row
  When it renders
  Then getByRole("table", { name: /spend by key/i }) resolves
  And the table carries data-slot="data-table"

Scenario: Usage stats render via StatCard
  Given UsageStatsCards with data {requests:3, prompt:300, completion:150, cost:"1.23"}
  When it renders
  Then four [data-slot="stat-card"] tiles appear inside a [class*="sm:grid-cols-4"] wrapper
  And getByText("3"), getByText("300"), getByText("150"), getByText("1.23") all resolve

Scenario: Usage stats loading/error unchanged
  Given UsageStatsCards isLoading=true (then isError=true)
  When it renders
  Then loading shows getByTestId("loading"); error shows role="alert"
  And no [data-slot="stat-card"] tile renders in either state

Scenario: Usage records render via sortable DataTable
  Given UsageTable with two records
  When it renders
  Then it contains [data-slot="data-table"] and a header button getByRole("button", { name: /cost/i })
  And every record's cell text (model_id, tokens, cost_usd, status, created_at) is present

Scenario: Usage records loading/error keep zero rows
  Given UsageTable isLoading=true (then isError=true)
  When it renders
  Then queryAllByRole("row").length === 0
  And empty data still shows getByText(/no usage records yet/i)

Scenario: Budget renders via StatCards, edit preserved
  Given BudgetWidget data {ceiling:"25.00", spent:"10.50"} canEdit=true
  When it renders
  Then two [data-slot="stat-card"] tiles show 25.00 and 10.50
  And getByRole("button", { name: /edit budget/i }) is present (absent when canEdit=false; null ceiling → /unlimited/i)

Scenario: Spend chart wraps a ChartContainer in the figure
  Given SpendSparkline with two buckets
  When it renders
  Then within the figure[data-testid="spend-chart"] there is a [data-slot="chart"] and the figcaption /spend over time/i
  And buckets.length===0 still renders nothing

Scenario: Spend totals render via StatCards keeping testids
  Given SpendPage data state with totals {cost:"1.23", requests:3}
  When it renders
  Then getByTestId("totals-cost").textContent==="1.23" and getByTestId("totals-requests").textContent==="3"
  And the "Totals (month)" heading and a [class*="sm:grid-cols-4"] wrapper are present

Scenario: Spend breakdown renders via DataTable (data seam intact)
  Given SpendPage grouped by key_id with one breakdown row {key_id:"key-001", cost:"3.50"}
  When it renders
  Then getByRole("table", { name: /spend by key/i }) shows key-001 and 3.50
  And no BFF route/queryKey/field changed (presentation-only)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Presentation contract (no HTTP/data surface changes — every BFF route, queryKey,
# request body, and response field is FROZEN-as-is). The shape below is the COMPONENT API
# + the DOM hooks the frozen suites query.

## Design-system seams (additive, backward-compatible)
StatCard props (added): valueTestId?: string   # → data-testid on the value node
  root element: data-slot="stat-card"           # additive marker
DataTable props (added): ariaLabel?: string     # → aria-label forwarded to the <table>
  root element (the <table>): data-slot="data-table"
  (existing props/behavior — columns, data, caption, emptyMessage, className, sorting, Empty — UNCHANGED)

## Usage (legacy project — must stay green)
UsageStatsCards: 4 × StatCard inside <div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
  values byte-identical "3"/"300"/"150"/"1.23"; loading→Loading data-testid="loading"; error→ErrorState(role=alert)
UsageTable: DataTable(columns=[Model,Prompt Tokens,Completion Tokens,Cost (USD),Status,Date],
  emptyMessage="No usage records yet"); on isLoading||isError → return null (0 role="row")
BudgetWidget: 2 × StatCard (ceiling, spent) + (canEdit ? "Edit Budget" button + BudgetEditForm) ; null ceiling → "Unlimited"

## Spend (bff project — must stay green)
SpendSparkline: <figure data-testid="spend-chart" aria-label="Spend over time"><figcaption>Spend over time</figcaption>
  <ChartContainer data-slot="chart" config={cost}> <LineChart .../> </ChartContainer></figure>; buckets.length===0 → null
SpendPage totals: <h2>Totals ({window})</h2> + <section class="grid grid-cols-2 gap-4 sm:grid-cols-4">
  4 × StatCard valueTestId in {totals-cost, totals-requests, totals-prompt, totals-completion}, raw values (no "$")
SpendPage breakdown: DataTable ariaLabel+caption "Spend by key" (cols Key,Requests,Prompt,Completion,Cost (USD))
  / "Spend by team" (cols Team,Requests,Prompt,Completion,Cost (USD),Ledger cost (USD)); rendered only when !isError && breakdown!=null && groupBy matches
SpendPage UNCHANGED: native <select>×3 (window/group-by/key-filter), buckets <ul><li data-testid="spend-bucket">,
  zero-state (data-testid="spend-zero-state"), loading (spend-loading), error (spend-error), keepPreviousData/last-good

## Keys (legacy + bff — must stay green)
KeysPage: UNCHANGED — stays on composed Card+Table (governance expand-row precludes DataTable; documented decision).

Schema: none. Forbidden: data_seam_drift, frozen_test_tamper (HARD-STOP), lost_test_hook, design_system_violation.
```

Status: FROZEN @ v1 — approved by Tin Dang (standing auto-mode authorization, 2026-06-16)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥ 80% lines (project gate) — surfaces are presentation; the existing frozen suites carry behavioral coverage, the new suite drives the block adoption.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_stat_card_value_testid_and_slot: render StatCard valueTestId → getByTestId text + data-slot="stat-card"
  - test_data_table_aria_label_and_slot: render DataTable ariaLabel → getByRole("table",{name}) + data-slot="data-table"
  - test_usage_stats_render_via_statcard: 4 [data-slot="stat-card"] in sm:grid-cols-4; values "3"/"300"/"150"/"1.23"
  - test_usage_stats_loading_error_render_no_tile: loading→testid loading, error→role alert, 0 tiles (regression guard)
  - test_usage_records_render_via_datatable: [data-slot="data-table"] + sortable header button; cell text intact
  - test_usage_records_loading_error_zero_rows: 0 role=row on loading/error; empty → "No usage records yet" (guard)
  - test_budget_renders_via_statcards: 2 tiles (25.00/10.50) + Edit button; member none; null → Unlimited
  - test_spend_chart_wraps_chart_container: figure keeps figcaption; contains [data-slot="chart"]
  - test_spend_chart_absent_when_empty: 0 buckets → no figure (guard)
  - test_spend_totals_render_via_statcards (bff): totals-* tiles closest [data-slot="stat-card"]; Totals (month) + sm:grid-cols-4
  - test_spend_breakdown_renders_via_datatable (bff): group_by=key_id → table[name=/spend by key/i] is [data-slot="data-table"], shows key-001/3.50
</test_plan>

RED CONFIRMED: 8 failed (missing data-slot / ariaLabel / sortable header / ChartContainer) + 3 green regression guards; both bff reds land after the data seam (totals-cost, breakdown table) resolves — proving harness + seam intact.

Tests live in: `tests/design-system/console-surfaces-redesign.test.tsx` `tests-bff/console-spend-redesign.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/stat-card.tsx` `data-table.tsx` `apps/dashboard/components/usage/UsageStatsCards.tsx` `UsageTable.tsx` `BudgetWidget.tsx` `apps/dashboard/components/spend/SpendPage.tsx` `SpendSparkline.tsx`
Strategy (ordered batches): 1. Add the design-system seams (StatCard.valueTestId + data-slot="stat-card"; DataTable.ariaLabel + data-slot="data-table"). 2. Adopt in Usage (UsageStatsCards→StatCard, UsageTable→DataTable, BudgetWidget→StatCards). 3. Adopt in Spend (SpendSparkline→ChartContainer; SpendPage totals→StatCards, breakdown→DataTable). 4. Run both projects green + add.py check.
Safety rule (feature-specific): presentation-only — never touch a BFF route/queryKey/request field/response field; preserve every frozen test hook (data-testid, aria-label, role, native <select>, the loading/error null-render guard for 0 role=row).
Code lives in: `apps/dashboard/components/`
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

- [x] all tests pass — `npm test` (vitest run) 41 files / 317 passed, 0 failed (both projects)
- [x] coverage did not decrease — `vitest run --coverage` exit 0, All files 89.66% lines ≥ 80% gate
- [x] no test or contract was altered during build — tripwire clean; only SRC touched; `add.py check` 37/0
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) verdict EARNED: 6/6 checklist NONE (zero data-seam drift, every frozen hook preserved, new data-slot asserts genuinely discriminating, no overfit)
- [x] concurrency / timing — N/A (presentation-only; no IO/state change; data seam byte-identical)
- [x] no exposed secrets, injection openings, or unexpected dependencies — R6 intact (no new deps; @tanstack/react-table/recharts pre-existing); no secrets; no raw-HTML sink added
- [x] layering & dependencies follow CONVENTIONS.md — surfaces compose the v13 design-system blocks; R3 intact (no raw hex/Npx in components/ui/*; SpendSparkline uses var(--color-cost))
- [x] a person reviewed and approved the change — Tin Dang (standing auto-mode authorization, 2026-06-16)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: StatCard.valueTestId→SpendPage totals-*; DataTable.ariaLabel→SpendPage breakdown; data-slot consumed by surfaces + the new suite; KEY/TEAM_BREAKDOWN_COLUMNS, UsageTable COLUMNS, SpendSparkline CHART_CONFIG all wired. Confirmed by tsc (0 errors) + green suite.
- [x] DEAD-CODE (code) — removed now-unused Table/Card primitive imports from SpendPage/UsageTable/UsageStatsCards/BudgetWidget; eslint (0 errors) + tsc (0 errors) on all 7 files confirm no orphaned symbol/import.
- [ ] SEMANTIC (prose / non-code) — N/A (code task)

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: — · ticket: — · expires: —   (never for a security gap)
Reviewed by: Tin Dang (standing auto-mode authorization) · date: 2026-06-16

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the frozen usage/keys/spend/govern suites are the regression monitors — any future restyle that drifts the data seam or drops a hook turns one of them red.
Spec delta for the next loop: the `data-slot="…"` marker + `valueTestId`/`ariaLabel` passthrough is now the repeatable pattern for "adopt a shared block while keeping a frozen test hook" — admin-surfaces-redesign should reuse it verbatim.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · folded] The §5 scope baseline walks the working tree (excludes only .git/.add/__pycache__/node_modules) — a gitignored build artifact dir like `apps/dashboard/coverage/` present at the tests→build snapshot pollutes the baseline, so a later `--coverage` run (or deleting it) trips `scope_violation` (evidence: WARN after `vitest run --coverage`; fixed by removing coverage/ + re-snapshotting via phase tests→advance). Candidate engine fix: add `coverage` to `_SCOPE_EXCLUDE_DIRS`. Run the gate command (`npm test` = no coverage) — keep `--coverage` to a one-off off-baseline check.
- [TDD · folded] For a presentation-only restyle, frozen behavioral suites are the regression net; the NEW red→green suite only needs to assert the *adoption* — a stable `data-slot` marker is a non-brittle, genuinely-discriminating hook (beats asserting CSS classes) (evidence: 8 reds landed exactly on missing data-slot/ariaLabel/sortable-header/ChartContainer; refute-read confirmed non-vacuous).
- [UDD · folded] Not every surface fits every block: Keys' interleaved governance expand-row is incompatible with TanStack's flat column model, so forcing DataTable would have broken the frozen expand→prefill flow — adopting where it fits + documenting where it doesn't is the honest call (evidence: §1 framing rejected force-DataTable-everywhere; Keys stays on composed Card+Table).
