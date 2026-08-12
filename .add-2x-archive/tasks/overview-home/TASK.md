# TASK: Overview home: KPI cards + usage chart + recent-activity table

slug: overview-home · created: 2026-06-15 · stage: production
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
- NEW `apps/dashboard/components/overview/OverviewPage.tsx` — the Overview surface (client). Aggregates EXISTING reads (no new BFF route): KPI `StatCard`s + a usage-over-time `ChartCard` with a day/week/month range toggle + a recent-activity `DataTable`. Follows the `components/<area>/<Area>Page.tsx` pattern (e.g. `components/usage/UsagePage.tsx`, `components/spend/SpendPage.tsx`) — inline `useQuery` per source, role from `useCurrentUser()`.
- `apps/dashboard/app/page.tsx` — CURRENTLY `export const metadata={title:"Hydroa"}` + a sync `RootPage()` that `redirect("/login")`. This task makes `/` the authenticated Overview landing: a server component that checks the `ai_proxy_session` cookie (`next/headers` cookies()) — absent ⇒ `redirect("/login")` (same rule proxy.ts enforces for /keys,/usage), present ⇒ render `<DashboardShell><OverviewPage/></DashboardShell>`. proxy.ts is NOT modified (auth model byte-identical).
- `apps/dashboard/lib/api-client.ts` — `apiGet<T>(path)` (the read client UsagePage/SpendPage use). Sources: `apiGet<UsageData>("/admin/usage")`, `apiGet<SpendWindowResponse>("/admin/spend?window={day|week|month}")`, `apiGet<BudgetData>("/admin/budget")`.
- Data shapes consumed (read verbatim from the existing pages — NOT redefined):
  - `components/usage/UsageStatsCards.tsx` — `UsageData { total_cost_usd:string; total_requests:number; total_prompt_tokens:number; total_completion_tokens:number; records: UsageRecord[] }`; `UsageRecord { id; model_id; prompt_tokens; completion_tokens; cost_usd:string; status:number; created_at:string }`.
  - `components/spend/SpendPage.tsx` — `SpendWindowResponse { window; totals: SpendTotals; buckets: SpendBucket[]; … }`; `SpendBucket { bucket_start:string; requests:number; prompt_tokens:number; completion_tokens:number; cost_usd:string }` (the time series for the chart + the last-vs-prior-bucket trend delta); `SpendWindow = "day"|"week"|"month"`.
  - `components/usage/BudgetWidget.tsx` — `BudgetData { budget_usd_monthly: string|null; spent_usd_month: string }` (null ceiling ⇒ "Unlimited").
- v23 inventory consumed (shipped by `design-system-enterprise-ext`, used verbatim): `components/ui/stat-card.tsx` `StatCard{label,value,delta?:{direction:"up"|"down"|"neutral",text},icon?,footer?}` · `components/ui/chart.tsx` `ChartCard`/`ChartContainer`/`ChartTooltip`/`ChartTooltipContent` (recharts, token-driven `var(--color-chart-N)`) · `components/ui/data-table.tsx` `DataTable<TData,TValue>{columns:ColumnDef[],data,caption?,emptyMessage?}` · `components/ui/card.tsx` · all via the `@/components/ui` barrel.
- `apps/dashboard/components/dashboard-shell.tsx` — `DashboardShell` (the v23 live shell from task 2); `app/page.tsx` wraps the Overview in it so `/` gets the branded sidebar + active-route marking.
- `recharts` (already a dep + allow-listed) — Line/Area/Bar primitives for the chart. lucide-react icons for the KPI cards.

Context (working folder):
- `apps/dashboard/tests-bff/` — the BFF/integration test project (MSW). Convention: `import { server } from "./mocks/server"` + `http`/`HttpResponse` (msw) + a `QueryClientProvider` (retry:false). `tests-bff/mocks/handlers.ts` ALREADY mocks `/admin/usage`, `/admin/budget`, `/v1/models`; `/admin/spend` is NOT in the defaults → the Overview test adds it via `server.use(http.get(...))`. Verify file (milestone-named): `apps/dashboard/tests-bff/overview-home.test.tsx`.
- `apps/dashboard/proxy.ts` — cookie-presence guard, matcher `["/keys","/keys/:path*","/usage","/usage/:path*"]`; root `/` is NOT in it → the new `app/page.tsx` does the cookie check server-side itself (mirrors proxy's rule; leaves the matcher untouched).
- `apps/dashboard/vitest.config.*` — two projects (design-system `tests/` jsdom + bff `tests-bff/`); coverage threshold lines:80.
- `apps/dashboard/tests/design-system/allowlist.json` — frozen dep allow-list; recharts + lucide already listed → NO new dependency.
- `.add/milestones/v23/MILESTONE.md` — exit criterion: "Visiting `/` shows ≥4 KPI cards with trend deltas + a usage-over-time chart with working range toggles + a recent-activity table"; deferred flag #1 ("Overview metrics data contract: client-side aggregate vs new gateway endpoint") is OWNED + resolved at THIS task's §3 freeze.

Honors (patterns / conventions):
- v23 milestone constraint (MILESTONE.md): presentation-only — NO change to BFF routes, hooks, or field names; the data seam stays byte-identical. RESOLUTION of deferred flag #1 → client-side aggregate from the EXISTING `/admin/usage` + `/admin/spend` + `/admin/budget` reads (NO new gateway endpoint), so the seam is untouched.
- UDD fold v13/v23: consume the frozen design system — token utilities only (R3: no raw `#hex`/bare `Npx`); the v23 `StatCard`/`ChartCard`/`DataTable` are the sanctioned inventory; a11y = jsdom-axe serious|critical, color-contrast disabled; recharts is SVG (jsdom needs the ResizeObserver shim, per the v23 chart tests).
- Page pattern (v1/v15): `app/(area)/page.tsx` (thin route) → `components/<area>/<Area>Page.tsx` (the surface, inline `useQuery`, four-state: loading role=status · error role=alert · empty · data).
- Auth model (v18 fold): cookie-presence is a UX guard only; the gateway validates the JWT on every proxied call. The Overview's reads 401→handled by the existing api-client; `app/page.tsx`'s cookie check mirrors proxy.ts (no auth weakening).

Anchors the contract cites: NEW `components/overview/OverviewPage.tsx` (`OverviewPage()` — KPI `StatCard`s with last-vs-prior-bucket trend deltas · `ChartCard` over `/admin/spend?window=` buckets with a day/week/month range toggle · `DataTable` over `/admin/usage` records) · `app/page.tsx` (server cookie-gate → `<DashboardShell><OverviewPage/></DashboardShell>`) · data sources `apiGet("/admin/usage")` `apiGet("/admin/spend?window=…")` `apiGet("/admin/budget")` (existing, unchanged) · consumed shapes `UsageData`/`UsageRecord`/`SpendWindowResponse`/`SpendBucket`/`BudgetData` · v23 inventory `StatCard`/`ChartCard`/`ChartContainer`/`DataTable`/`Card` via `@/components/ui` · NO new BFF route, NO new dependency. New test: `apps/dashboard/tests-bff/overview-home.test.tsx`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Overview home — a new `/` landing that aggregates the EXISTING usage/spend/budget reads into an at-a-glance enterprise dashboard: ≥4 KPI stat cards (with trend deltas), a usage-over-time chart with day/week/month range toggles, and a recent-activity table — composed from the v23 inventory (StatCard/ChartCard/DataTable) with no new BFF route and no new dependency.

Framings weighed: client-side aggregate over existing `/admin/usage`+`/admin/spend`+`/admin/budget` reads (chosen — keeps the data seam byte-identical, the milestone's stated preference) · a NEW read-only `/admin/overview` gateway endpoint (rejected: adds a BFF/gateway surface the milestone says to avoid; the existing reads already carry every field needed) · a static/hardcoded demo Overview (rejected: not real data, fails the trend/range/table criteria honestly)

Must:
<must>
  - Provide `OverviewPage` (client) at the route `/` (via `app/page.tsx`), rendered inside `DashboardShell` so it gets the branded sidebar + active-route marking.
  - Render ≥4 KPI `StatCard`s from the existing reads: Requests, Cost, Tokens (prompt+completion) for the selected range (from `/admin/spend?window=` totals), and Monthly Spend (from `/admin/budget` `spent_usd_month`, footnoting the monthly budget or "Unlimited"). Each usage KPI shows a trend delta computed as the latest bucket vs the previous bucket (`direction` up/down/neutral + a % text), conveyed by text/aria not color alone.
  - Render a usage-over-time `ChartCard` over the `/admin/spend?window=` `buckets[]` time series (x = `bucket_start`, y = requests or cost), series color from a `var(--color-chart-N)` token (no raw hex), with an accessible title + description.
  - Provide a working range toggle (day · week · month): activating a range refetches `/admin/spend?window=<range>` (drives the TanStack queryKey, like SpendPage) and the chart + KPI values reflect the selected range; the active range is marked (aria-pressed / aria-current) and keyboard-operable.
  - Render a recent-activity `DataTable` over the `/admin/usage` `records[]` (most-recent first, capped, e.g. ≤10): columns model, tokens, cost, status, time; an Empty state when there are no records.
  - Honor the four-state pattern: loading exposes role=status, an error exposes role=alert, empty shows an empty state, success shows the data — for the aggregated reads.
  - Make `/` auth-correct: `app/page.tsx` (server) redirects to `/login` when the `ai_proxy_session` cookie is absent (mirroring proxy.ts), and renders the Overview when present — proxy.ts and the auth model are unchanged.
  - Consume the v23 design system only (StatCard/ChartCard/DataTable/Card via `@/components/ui`); R3 (no raw `#hex`/bare `Npx`); every interactive control labelled + keyboard-operable + axe serious|critical clean (color-contrast disabled in jsdom).
  - Add NO new BFF route, NO new hook signature, NO field rename, NO new npm dependency — the data seam stays byte-identical.
</must>
Reject:
<reject>
  - a new gateway/BFF route or a renamed field introduced for metrics -> "data_seam_changed"   (presentation-only invariant; reads stay `/admin/usage` · `/admin/spend` · `/admin/budget`)
  - fewer than 4 KPI cards, or a KPI with no trend delta -> "insufficient_kpis"
  - a range toggle that does not change the fetched window / chart data -> "range_toggle_inert"
  - the recent-activity table missing an Empty state on zero records -> "missing_empty_state"
  - a loading/error path with no role=status / role=alert -> "missing_state_pattern"
  - `/` reachable without a session cookie (no redirect to /login) -> "unauthenticated_overview"
  - a raw `#hex`/bare `Npx` literal in the new component -> "raw_value_in_ui"   (R3 guard)
  - a new package absent from allowlist.json -> "unlisted_dependency"   (R6 guard)
  - trend direction conveyed by color alone (no text/aria) -> "a11y_violation"   (WCAG 1.4.1 / axe)
</reject>
After:
<after>
  - Visiting `/` (authenticated) shows ≥4 KPI cards with trend deltas, a usage-over-time chart whose day/week/month toggle refetches and redraws, and a recent-activity table (or its empty state) — all from the existing reads, inside the branded shell.
  - The data seam is byte-identical (no new route/field/dep); `add.py check` + the full design-system + BFF suites stay green; the metrics-contract flag (#1) is resolved as "client-side aggregate".
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] trend deltas are computed client-side as latest-bucket vs previous-bucket of the `/admin/spend?window=` series (there is no period-over-period field in the existing reads) — lowest confidence because a single-bucket or empty series has no "previous" to compare, and the semantics ("vs previous {window}") are an invented-but-honest derivation. If wrong / a real comparison is wanted: fall back to a neutral delta (direction "neutral", text "—") when <2 buckets, and (only if a true period-over-period is later required) that becomes a NEW gateway field → a separate change request, not this task.
  ⚠ [contract] `/` becomes the authenticated Overview via a server cookie-check in `app/page.tsx` (mirroring proxy.ts) instead of adding `/` to the proxy matcher — lowest confidence because it duplicates the cookie rule in two places (proxy + page); if they drift, `/` could redirect differently than /keys,/usage. If wrong: add `"/"` to the proxy.ts matcher and make `app/page.tsx` a plain client page — a one-line matcher change, no Overview API change.
  - [x] the chart uses recharts (already a dep + allow-listed) inside the v23 ChartContainer → no new dependency; jsdom needs the ResizeObserver shim (established by the v23 chart tests).
  - [x] KPI/chart/table all read the SAME three existing endpoints UsagePage/SpendPage already call → no new network surface; MSW handlers exist for usage/budget, `/admin/spend` added in the test.
  - [x] range toggle = the SpendPage window mechanism (queryKey includes the window) → proven pattern, just re-skinned with v23 controls.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: the Overview renders the KPI cards with trend deltas
  Given OverviewPage with mocked /admin/spend, /admin/usage, /admin/budget
  When it loads
  Then at least 4 KPI stat cards render (Requests, Cost, Tokens, Monthly Spend)
  And each usage KPI shows a trend delta with a direction conveyed by text/aria, not color alone

Scenario: the usage-over-time chart renders from the spend buckets
  Given /admin/spend?window=month returns a buckets[] series
  When the Overview loads
  Then a titled, described chart renders with a token-driven series color (var(--color-chart-N), no raw hex)

Scenario: the range toggle refetches and redraws
  Given the Overview defaulting to one window
  When the user activates a different range (day/week/month)
  Then /admin/spend is refetched with window=<range> and the chart/KPIs reflect it
  And the active range is marked (aria-pressed/current) and keyboard-operable

Scenario: the recent-activity table lists records
  Given /admin/usage returns records[]
  When the Overview loads
  Then a table lists the most-recent records (capped) with model, tokens, cost, status, time columns

Scenario: the recent-activity table shows an empty state
  Given /admin/usage returns zero records
  When the Overview loads
  Then the table shows an empty state message

Scenario: the four-state pattern is honored
  Given the aggregated reads are loading, then error
  When the Overview renders each state
  Then loading exposes role=status and an error exposes role=alert

Scenario: visiting / unauthenticated redirects to login
  Given no ai_proxy_session cookie
  When / is requested
  Then the server redirects to /login
  And with the cookie present, / renders the Overview inside the shell

Scenario: the Overview is accessible
  Given the loaded Overview
  When axe runs (serious|critical, color-contrast disabled)
  Then there are no serious or critical violations and every control is labelled + keyboard-operable

# ── Reject scenarios (each names what must remain unchanged) ──
Scenario: a changed data seam is rejected
  Given the Overview's data needs
  When it fetches metrics
  Then it uses only /admin/usage, /admin/spend, /admin/budget (no new route, no renamed field) (data_seam_changed)
  And the BFF routes/hooks/field names remain byte-identical

Scenario: too few KPIs is rejected
  Given the KPI row
  When it renders
  Then there are at least 4 cards and each has a trend delta (insufficient_kpis)
  And the existing usage/spend/budget fields remain the source

Scenario: an inert range toggle is rejected
  Given the range control
  When a range is selected
  Then the fetched window actually changes (range_toggle_inert)
  And the default window data is otherwise unchanged

Scenario: a missing empty state is rejected
  Given zero records
  When the table renders
  Then an empty state is shown (missing_empty_state)
  And a populated table still lists records

Scenario: a missing loading/error pattern is rejected
  Given a loading or error read
  When the Overview renders
  Then role=status (loading) / role=alert (error) is present (missing_state_pattern)
  And the success state still renders the data

Scenario: an unauthenticated Overview is rejected
  Given no session cookie
  When / is requested
  Then it does not render the Overview (unauthenticated_overview)
  And it redirects to /login

Scenario: a raw value in the component is rejected
  Given the new components/overview file
  When the R3 guard runs
  Then it has no raw #hex or bare Npx (raw_value_in_ui)
  And token-named utilities remain the only styling path

Scenario: a new dependency is rejected
  Given package.json
  When the R6 guard runs
  Then no new package is introduced (unlisted_dependency)
  And recharts/lucide remain the only chart/icon deps

Scenario: color-only trend is rejected
  Given a KPI trend delta
  When it conveys direction
  Then it uses text/aria, not color alone (a11y_violation)
  And labelled, keyboard-operable controls remain the contract
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

UI/COMPONENT contract (no NEW HTTP surface). The frozen shape is: the metrics
data contract (RESOLVED = client-side aggregate over EXISTING reads), the
`OverviewPage` composition + observable markers, the `/` route gate, and the
reject→guard mapping. Presentation-only: NO new BFF route, NO renamed field, NO new dep.

### A · Metrics data contract — RESOLVED: client-side aggregate (resolves milestone flag #1)
```
Sources (existing reads, UNCHANGED — via apiGet, the BFF already proxies all three):
  GET /admin/usage              -> UsageData { total_*; records: UsageRecord[] }
  GET /admin/spend?window=<w>   -> SpendWindowResponse { window; totals: SpendTotals; buckets: SpendBucket[] }   w ∈ {day,week,month}
  GET /admin/budget             -> BudgetData { budget_usd_monthly: string|null; spent_usd_month: string }
Derivations (client-side, no new field persisted):
  KPI values  = spend totals (range-scoped) + budget.spent_usd_month
  trend delta = latest bucket vs previous bucket of buckets[]:
                 pct = prev>0 ? (latest-prev)/prev*100 : 0 ;  direction = pct>0 up | pct<0 down | else neutral ;
                 <2 buckets ⇒ direction "neutral", text "—"   (honest no-comparison)
NO new gateway endpoint. The data seam is byte-identical.
```

### B · Component — `apps/dashboard/components/overview/OverviewPage.tsx` (NEW, client, barrel-free local)
```
export function OverviewPage(): JSX.Element
  state: window: "day"|"week"|"month"  (default "month")
  queries (inline useQuery, like SpendPage/UsagePage):
    ["overview-spend", window] -> apiGet<SpendWindowResponse>(`/admin/spend?window=${window}`)   (keepPreviousData)
    ["admin-usage"]            -> apiGet<UsageData>("/admin/usage")
    ["admin-budget"]           -> apiGet<BudgetData>("/admin/budget")
  renders:
    - <h1> "Overview"
    - ≥4 StatCard: "Total Requests" · "Total Cost" · "Total Tokens" · "Monthly Spend"
        each usage KPI delta={direction,text} (text/aria conveys direction — NOT color alone, WCAG 1.4.1)
    - ChartCard title "Usage over time" + description; ChartContainer(config{requests|cost: color "var(--color-chart-1)"})
        wrapping a recharts series over buckets[] (x=bucket_start)
    - range toggle: 3 buttons day|week|month; active has aria-pressed="true"; click sets window (refetch)
    - recent-activity DataTable over usage.records (most-recent first, ≤10) columns: Model, Tokens, Cost, Status, Time
        emptyMessage shown when records=[]
  four-state: any read loading -> a role=status element ; any read error -> a role=alert element
  consumes @/components/ui (StatCard, ChartCard, ChartContainer, DataTable, Card) — token utilities only (R3)
```

### C · Route gate — `apps/dashboard/app/page.tsx` (server component; proxy.ts UNCHANGED)
```
export default async function RootPage()
  const jar = await cookies()                     // next/headers
  if (!jar.get("ai_proxy_session")) redirect("/login")
  return <DashboardShell><OverviewPage/></DashboardShell>
export const metadata = { title: "Hydroa" }       // retained
```

### D · Reject → enforcing guard (a response for every §1 Reject code)
```
data_seam_changed       -> overview-home.test.tsx : only /admin/usage|spend|budget requested (msw); no new route hit
insufficient_kpis       -> overview-home.test.tsx : ≥4 stat cards rendered, each with a delta marker
range_toggle_inert      -> overview-home.test.tsx : selecting a window issues a /admin/spend?window=<w> request + redraw
missing_empty_state     -> overview-home.test.tsx : zero records -> empty message present
missing_state_pattern   -> overview-home.test.tsx : loading -> role=status ; error -> role=alert
unauthenticated_overview-> overview-home.test.tsx : RootPage with no cookie calls redirect("/login") (next/navigation + next/headers mocked)
raw_value_in_ui         -> tokens.test.ts R3 (existing) : offenders === []
unlisted_dependency     -> tokens.test.ts R6 (existing) : stray === []
a11y_violation          -> overview-home.test.tsx : axe serious|critical === [] + trend direction in text/aria
```

Status: FROZEN @ v1 — approved by Tin Dang (standing auto-mode authorization, 2026-06-15: "implement in auto mode - with your best decision - do not ask"). autonomy: auto · risk: normal (presentation + a client-side cookie-gate that MIRRORS the existing proxy rule; no auth weakening, no money/data-loss/method scope → not unguarded_high_risk_auto).

Least-sure flag surfaced at freeze: [contract] trend deltas are a CLIENT-SIDE derivation (latest-bucket vs previous-bucket) because no period-over-period field exists in the existing reads — with <2 buckets it honestly degrades to neutral "—"; if a true period comparison is ever required that is a NEW gateway field = a separate change request, not this task. ALSO [contract] `/` is gated by a cookie check in `app/page.tsx` that DUPLICATES proxy.ts's rule (two places); if they drift `/` could behave differently than /keys,/usage — fallback is to add `"/"` to the proxy matcher and make the page a plain client component (one-line change, no Overview API change).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (the dashboard bar) on `components/overview/OverviewPage.tsx`.
Plan (one test per scenario, asserting OBSERVABLE behavior via MSW + RTL) — `tests-bff/overview-home.test.tsx` (+ the route-gate test mocks `next/headers` + `next/navigation`):
<test_plan>
  - test_overview_renders_kpi_cards_with_deltas: mock spend/usage/budget → ≥4 stat cards (Requests/Cost/Tokens/Monthly Spend); each usage KPI shows a delta whose direction is in text/aria (regex /up|down|increase|decrease|neutral|↑|↓|—/), not color alone
  - test_chart_renders_from_buckets: spend buckets[] → a titled+described chart present; ChartContainer injects a --color-* token var (no raw hex in source)
  - test_range_toggle_refetches: default month; click "day" → a /admin/spend?window=day request is observed (msw spy) and the active button has aria-pressed="true"
  - test_recent_activity_table_lists_records: usage records → table lists rows with model/tokens/cost/status/time
  - test_recent_activity_empty_state: usage records=[] → empty message present
  - test_four_state_loading_and_error: a never-resolving read → role=status; a 500 read → role=alert
  - test_root_redirects_unauthenticated: RootPage() with cookies() returning no ai_proxy_session → redirect("/login") called (next/navigation mocked); with the cookie → renders Overview (no redirect)
  - test_overview_axe_clean: loaded Overview → axe serious|critical === [] (color-contrast disabled)
  - test_data_seam_unchanged: across a full render only /admin/usage, /admin/spend, /admin/budget are requested — no other/new metrics route
  # raw_value_in_ui / unlisted_dependency enforced by the EXISTING tokens.test.ts (R3/R6); recharts+lucide already allow-listed.
</test_plan>
RED expectation: `@/components/overview/OverviewPage` does not exist (MODULE_NOT_FOUND at collect — the established true-red) and `app/page.tsx` still redirects unconditionally; every assertion fails until Build.
RED confirmed: 1 suite failed at collect — `Failed to resolve import "@/components/overview/OverviewPage"` (true-red; the whole suite is blocked until Build writes the component + route gate).

Tests live in: `apps/dashboard/tests-bff/overview-home.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/overview/` `apps/dashboard/app/page.tsx` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo`
Strategy (ordered batches): 1. `components/overview/OverviewPage.tsx` — inline useQuery for spend(window)/usage/budget; trend helper (latest-vs-prev bucket); ≥4 StatCard; ChartCard+ChartContainer over buckets (recharts); day/week/month toggle (aria-pressed); DataTable over records (≤10) with emptyMessage; four-state (role=status/alert). 2. `app/page.tsx` → async server component: cookies() gate → redirect("/login") | `<DashboardShell><OverviewPage/></DashboardShell>`. 3. run tests-bff/overview-home → green; 4. eslint + tsc + next build + add.py check.
Safety rule (feature-specific): PRESENTATION-ONLY — read exclusively the existing `/admin/usage` · `/admin/spend` · `/admin/budget` endpoints (NO new BFF route, NO field rename, NO new dep); trend deltas degrade to neutral "—" with <2 buckets; the `/` cookie gate MIRRORS proxy.ts (no auth weakening). R3 — token utilities only.
Code lives in: `apps/dashboard/components/overview/OverviewPage.tsx` (+ `app/page.tsx` route gate)
Constraints: do NOT change any test or the contract; allow-list packages only (recharts/lucide already listed); R3 — no raw hex/px; reads stay byte-identical; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full dashboard suite 306/306 (39 files); new overview-home.test 11/11
- [x] coverage did not decrease — 89%+ lines (threshold 80); OverviewPage.tsx 100% lines (branch 71 — the recharts axis/tooltip render paths jsdom can't paint)
- [x] no test or contract was altered during build — frozen §3 byte-identical; the ONLY test edits (refute-read strengthening + a tsc typing fix) were made AFTER stepping back to the tests phase with a tests→build re-snapshot each time — never during build
- [x] the green was EARNED — independent adversarial refute-read (sonnet subagent) returned EARNED-WITH-GAPS and CONFIRMED FROM CODE: (a) data seam byte-identical, (b) auth gate genuinely redirects, (c) range toggle genuinely refetches, (d) four-state genuinely wired. ALL findings resolved in-loop: #4 (REAL bug — `trendDelta` prev===0 returned a fabricated "+100%") FIXED to the frozen-formula neutral "—" + a red→green guard test; #6 (raw recharts `Tooltip` import) switched to the barrel `ChartTooltip` alias; #1 auth test now mocks redirect to THROW (proves Overview NOT rendered); #2 KPI test now asserts per-card deltas (+50.0%×2, +12.5%, ≥3 "increase"); #3 seam test now asserts all 3 sources ARE called; #5 (R3 guard scopes to components/ui/ only) = pre-existing guard-scope limit, OverviewPage manually complies (only `var(--color-*)` + numeric recharts props, the SpendSparkline precedent) → §7 note
- [x] concurrency / timing — N/A (presentation/read-only). Three independent useQuery reads; range refetch via queryKey (keepPreviousData avoids a loading flash); no shared mutable state
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secrets; userEmail/records render as PLAIN TEXT; NO new BFF route, NO renamed field (data seam byte-identical, proven by test_data_seam_unchanged); NO new dependency (recharts/lucide already allow-listed → R6 green); the `/` cookie gate MIRRORS proxy.ts (no auth weakening)
- [x] layering & dependencies follow conventions — follows the `app/(area)/page.tsx`→`components/<area>/<Area>Page.tsx` pattern; consumes the v23 inventory via `@/components/ui` (StatCard/ChartCard/ChartContainer/ChartTooltip/DataTable/Card); token utilities only (R3)
- [x] reviewed — auto-gate (autonomy: auto) on complete evidence + independent refute-read (EARNED post-remediation); recorded under Tin's standing authorization ("implement in auto mode - with your best decision - do not ask", 2026-06-15)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `OverviewPage` is rendered by `app/page.tsx` at `/` (inside `DashboardShell`); its 3 reads hit only the existing `/admin/usage|spend|budget`; `trendDelta`/`ACTIVITY_COLUMNS`/`CHART_CONFIG` all consumed; exercised by the 11-test suite (KPIs, chart token-var, range refetch, table, empty, four-state, route gate, axe, seam). next build compiled `/` as a dynamic route (ƒ — the cookie gate).
- [x] DEAD-CODE (code) — no orphan: every import used (after #6 dropped the unused recharts `Tooltip`); no unused local (eslint 0 errors); `app/page.tsx` replaced the old unconditional redirect (no dead redirect path left).
- [x] SEMANTIC — read OverviewPage.tsx + app/page.tsx + the test in full: the metrics contract is a pure client-side aggregate (no new route/field/dep), trend deltas follow the frozen formula incl. the neutral degenerate case, and the cookie gate mirrors proxy.ts.

### GATE RECORD
Outcome: PASS  (auto-resolved on evidence + independent refute-read; autonomy: auto, risk: normal)
Evidence: vitest 306/306 (39 files) · coverage ~89%L (OverviewPage 100%L) · eslint 0-err on new files (1 carried TanStack warning in data-table.tsx) · tsc clean · next build ✓ (`/` dynamic) · add.py check 37/0 · refute-read EARNED (post-remediation, 6/6 findings resolved)
Residue / follow-ups (→ §7 deltas): the R3 raw-value guard scans only `components/ui/` — feature dirs (`components/overview|usage|spend|…`) rely on manual compliance + review (pre-existing; a guard-scope widening is its own task); recharts axis/tooltip paint = browser-only (jsdom can't render the SVG → the chart's visual correctness is the standing browser-only residue); the `/` cookie rule is duplicated in proxy.ts + app/page.tsx (drift risk — fallback is the proxy-matcher one-liner).
Reviewed by: auto-gate (the run) under Tin Dang's standing auto-mode authorization · date: 2026-06-16

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
