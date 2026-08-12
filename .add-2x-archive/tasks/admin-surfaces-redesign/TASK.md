# TASK: Restyle Models/Teams/Routing/Settings to shared shadcn blocks

slug: admin-surfaces-redesign · created: 2026-06-15 · stage: production
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

Touches (files · symbols · signatures): (all bff project — render via bffGet; the v23 DataTable seam — `ariaLabel`+`data-slot="data-table"` — already shipped in console-surfaces-redesign)
- `components/models/ModelsPage.tsx:ModelsPage` — queryKey `["admin-models"]` GET `/admin/models`; PUT `/admin/models/{id}` `{enabled}`; raw `<Card><CardContent p-0><Table>` cols Model(name+id) · Context length · Enabled(`<Switch aria-label="Enable {name}">` disabled while `pendingId===id`). loading→`Loading`, error→`ErrorState`, empty→`Empty "No models available"`, toggle-error→inline `ErrorState`.
- `components/teams/TeamsPage.tsx:TeamsPage` — queryKey `["admin-teams"]` GET `/admin/teams` (BARE array); POST/DELETE; raw `<Table>` cols Team(`<button>{name}</button>` aria-current) · Members(member_count) · Keys(key_count) · Monthly budget (USD)(`<TeamBudgetForm>` inline PATCH; renders `{team_budget_usd ?? "—"}`) · Actions(`<Button aria-label="Delete team {name}">`). Master-detail: `<TeamMembersPanel>` below + `CreateTeamDialog`/`ConfirmDialog` (hand-rolled, aria-modal). `TeamResponse{id,name,tenant_id,created_at,member_count,key_count,team_budget_usd}`.
- `components/routing/RoutingPage.tsx:RoutingPage` — queryKey `["admin-routing"]` GET `/admin/routing` `{retry_policy,cooldown,model_groups,candidates}`, `retry:false`; metric `<Card>`s (Retry policy/Cooldown via `Metric` label/value + Badge) + Model groups list + candidates `<Table><TableCaption>Routing candidates and their circuit state</TableCaption>` cols Alias·Model(model_id)·State(`<Badge>{state}</Badge>`). early-return on loading/error (no card shells).
- `components/settings/{SettingsPage,CacheSettings,GuardrailSettings,OidcSettings}.tsx` — `<Tabs>` (Cache/Guardrails/SSO) of forms: `Switch` (aria-label "Enable response/semantic cache", "Enable prompt injection protection", "Enable PII masking", "Enable SSO"), native `<select>` (pi/pii mode), `Input` patterns + OIDC fields. NO table, NO KPI tile.
- `components/ui/data-table.tsx:DataTable` — `cell.column.columnDef.cell ? flexRender : String(getValue)`; sortable only when `getCanSort()`; 0 rows → `<Empty>`; `ariaLabel`→`<table aria-label>`; `data-slot="data-table"`.

Context (working folder): bff project (`tests-bff/**`, MSW `tests-bff/mocks`). Verify = `cd apps/dashboard && npm test`. Frozen suites: `model-mgmt`, `teams-governance`, `routing-health`, `tenant-settings`, `feature-coverage-verify`, `nav-role-filter`.
Honors (patterns / conventions): v13/v23 design system; R3 (no raw hex/`Npx` in `components/ui/*` — surfaces here are NOT in components/ui so unaffected, but reuse tokens); R6 deps ⊆ allowlist (no new deps; `@tanstack/react-table` already present). Presentation-only; same UDD lesson as console task — adopt DataTable where the table is flat, KEEP composed Card/forms where a block would be overfit.
Anchors the contract cites: `DataTable` (existing seam), `ModelsPage`, `TeamsPage`, `RoutingPage` (candidates table), `SettingsPage` (no-op — already shared primitives), `Switch`/`Badge`/`TeamBudgetForm` (cell renderers, byte-identical).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Restyle Models · Teams · Routing · Settings onto the shared DataTable block (+ keep composed Card/forms where a block is overfit) — presentation-only, data seam byte-identical.
Framings weighed: adopt-DataTable-for-flat-tables-keep-the-rest (chosen — Models/Teams-list/Routing-candidates are flat row-per-entity tables that fit DataTable via custom cell renderers; Routing's metric cards + all of Settings have no tabular/KPI data) · force-StatCard-on-routing-metrics (rejected — retry/cooldown are multi-metric/status cards, StatCard is overfit per the map) · leave-everything (rejected — flat tables should match Usage/Spend/Keys for visual consistency).
Must:
<must>
  - Models list renders via `DataTable` (cols Model[name+id], Context length, Enabled) with the Enabled cell an unchanged `<Switch aria-label="Enable {name}">` (checked state, disabled while `pendingId===id`, same onCheckedChange→PUT `{enabled}`); loading/error/empty/toggle-error branches unchanged (no table on loading/error/empty).
  - Teams list renders via `DataTable` (cols Team, Members, Keys, Monthly budget (USD), Actions) with cells byte-identical: team-name `<button>{name}</button>` (aria-current), member_count, key_count, inline `<TeamBudgetForm>`, delete `<Button aria-label="Delete team {name}">`; master-detail `TeamMembersPanel`, dialogs, mutations unchanged.
  - Routing candidates render via `DataTable ariaLabel="Routing candidates and their circuit state"` (cols Alias, Model, State=`<Badge>{state}</Badge>`); keep the empty guard (`<Empty title="No routing candidates">`, no table) and the early-return on loading/error; the metric cards (Retry policy, Cooldown, Model groups) stay composed Cards.
  - Settings stays as-is (Tabs of Card/Switch/native-select/Input forms) — already composes the shared components; no tabular/KPI data to migrate (documented decision).
  - Admin DataTables are non-sortable (`enableSorting:false` per column) — behavior-preserving (admin tables are unsorted today); they still gain `data-slot="data-table"` + the shared empty handling.
  - Every BFF route, queryKey, request body field, response field name, and every frozen hook (Switch/Badge/button/input aria-labels, `getByRole("table",{name:/candidate/i})`, role=status/alert, native `<select>`, dialog aria-modal, row counts) stays byte-identical; the bff suite stays green.
</must>
Reject:
<reject>
  - any change to a BFF route / queryKey / request body / response field -> "data_seam_drift" (forbidden — presentation-only)
  - editing or weakening any pre-existing (frozen) test to make the restyle pass -> "frozen_test_tamper" (HARD-STOP)
  - swapping a native `<select>` (AddMemberDialog role, guardrail modes) for a Radix Select, or dropping a frozen aria-label/role/caption -> "lost_test_hook"
  - dropping the candidates table's accessible name (caption AND ariaLabel both gone) -> "nameless_table" (breaks getByRole table name)
</reject>
After:
<after>
  - Models · Teams · Routing-candidates render via `DataTable`; Routing metric cards + Settings stay composed; `npm test` green; new red→green suite proves the adoption; zero data-seam diff; `add.py check` 37/0.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Mounting the stateful inline `<TeamBudgetForm>` inside a DataTable `columnDef.cell` keeps its draft state, validation, PATCH `{team_budget_usd}` body, and `Budget for {name}`/`Save budget for {name}` aria-labels intact — lowest confidence because a cell renderer is a new mount site; if wrong: teams-governance budget tests go red. Mitigation: the cell renders the SAME `<TeamBudgetForm team={row.original}/>` subtree with the same props; sorting is disabled so rows never reorder/remount.
  - [x] DataTable cell renderers preserve exact aria-labels/button names (Switch "Enable {name}", team button name, "Delete team {name}") — confirmed: cells return the identical JSX, DataTable only wraps each in a `<TableCell>`.
  - [x] Non-sortable columns render plain `<TableHead>` (no button) — confirmed: DataTable's `if (!canSort) return <TableHead>{content}` path; `enableSorting:false` ⇒ `getCanSort()` false.
  - [x] Candidates `getByRole("table",{name:/candidate/i})` survives — confirmed: ariaLabel forwarded to `<table aria-label>` (aria-label wins; caption also kept).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Models render via DataTable, Switch hook intact
  Given GET /admin/models returns two models (GPT-4o enabled, Claude disabled)
  When ModelsPage renders
  Then the list is a [data-slot="data-table"] and getByRole("switch",{name:"Enable GPT-4o"}) resolves with aria-checked
  And toggling still PUTs {enabled} to /admin/models/{id} (data seam unchanged)

Scenario: Models loading/error render no table
  Given GET /admin/models is loading (then 403)
  When ModelsPage renders
  Then loading shows role=status and error shows role=alert
  And no [data-slot="data-table"] and queryByRole("switch") is null

Scenario: Teams render via DataTable, cell hooks intact
  Given GET /admin/teams returns [platform (100.00), research (—)]
  When TeamsPage renders
  Then the list is a [data-slot="data-table"]; getByRole("button",{name:"platform"}), getByRole("button",{name:/delete team platform/i}), getByRole("textbox",{name:/budget for research/i}) all resolve
  And getByText("100.00") and getByText("—") still render (TeamBudgetForm unchanged)

Scenario: Routing candidates render via named DataTable
  Given GET /admin/routing returns candidates [vendor/a closed, vendor/b open]
  When RoutingPage renders
  Then getByRole("table",{name:/candidate/i}) is a [data-slot="data-table"] showing vendor/a and the state Badges
  And the Retry policy / Cooldown metric cards stay composed Cards (getByRole heading retry policy)

Scenario: Routing empty candidates render no table
  Given GET /admin/routing returns candidates []
  When RoutingPage renders
  Then getByText(/no routing candidates/i) and queryByRole("table") is null
  And the data seam (queryKey ["admin-routing"]) is unchanged

Scenario: Settings keeps the shared composed forms
  Given GET /admin/cache resolves on the default Cache tab
  When SettingsPage renders
  Then role=tablist with tabs and getByRole("switch",{name:/response cache/i}) resolve (no DataTable added)
  And native <select>s in guardrails are unchanged (no data seam change)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Presentation contract (no HTTP/data surface changes — every BFF route, queryKey, request
# body, and response field is FROZEN-as-is). Below = the render shape + the DOM hooks the
# frozen bff suites query. Reuses the DataTable seam shipped in console-surfaces-redesign.

## Models (model-mgmt + feature-coverage)
ModelsPage list → DataTable (inside the existing <Card><CardContent p-0>), columns (all enableSorting:false):
  Model     → cell: <div font-medium>{name}</div><div text-xs muted>{id}</div>
  Context length → cell: {context_length!==null ? toLocaleString() : "—"} (muted)
  Enabled   → cell: <Switch checked={enabled} aria-label={`Enable ${name}`} disabled={isPending&&pendingId===id} onCheckedChange→handleToggle> (id col, no accessor)
  loading→Loading(role=status) · error/toggle-error→ErrorState(role=alert) · empty→Empty "No models available" (NO table on any of these)
  PUT /admin/models/{encodeURIComponent(id)} body {enabled} — UNCHANGED

## Teams (teams-governance + feature-coverage)
TeamsPage list → DataTable (inside <Card><CardContent p-0>), columns (all enableSorting:false):
  Team      → <button onClick=setSelectedId aria-current=…>{name}</button>
  Members   → {member_count} (muted) · Keys → {key_count} (muted)
  Monthly budget (USD) → <TeamBudgetForm team={row.original}/> (id col)
  Actions   → header <span class="sr-only">Actions</span>; cell <div text-right><Button variant=destructive size=sm aria-label={`Delete team ${name}`}>Delete</Button></div> (id col)
  TeamMembersPanel, CreateTeamDialog, ConfirmDialog, all mutations (POST/DELETE/PATCH bodies) — UNCHANGED

## Routing (routing-health + feature-coverage)
RoutingPage candidates → DataTable ariaLabel="Routing candidates and their circuit state" caption=same,
  columns (enableSorting:false): Alias → {alias} · Model → {model_id} · State (id col) → <Badge variant={STATE_VARIANT[state]}>{state}</Badge>
  KEEP: candidates.length===0 ? <Empty title="No routing candidates" …> : <DataTable…> ; early-return Loading/ErrorState;
  Retry policy / Cooldown / Model groups Cards (CardTitle headings, Metric rows, Badge) — UNCHANGED

## Settings (tenant-settings)
SettingsPage + Cache/Guardrail/Oidc — UNCHANGED (already composes Tabs/Card/Switch/native-select/Input; no tabular/KPI data).

Schema: none. Forbidden: data_seam_drift, frozen_test_tamper (HARD-STOP), lost_test_hook, nameless_table.
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

Coverage target: ≥ 80% lines (project gate) — frozen bff suites carry behavioral coverage; the new suite drives the block adoption.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_models_render_via_datatable: [data-slot="data-table"] + switch "Enable GPT-4o" aria-checked + model id text
  - test_models_error_renders_no_table: 403 → role alert, no data-table, no switch (guard)
  - test_teams_render_via_datatable: [data-slot="data-table"] + team button "platform" + delete aria-label + budget input + 100.00/— text
  - test_routing_candidates_render_via_datatable: getByRole table /candidate/ is data-table + vendor/a + open Badge + Retry policy heading composed
  - test_routing_empty_candidates_no_table: [] → "No routing candidates" + no table (guard)
  - test_settings_keeps_shared_forms: tablist + response-cache switch, NO data-table (documents Settings no-op)
</test_plan>

RED CONFIRMED: 3 failed (Models/Teams/Routing missing data-slot) + 3 green guards (error/empty/settings); reds land after the data seam resolves (switch/team-button/candidate-table found) — proving harness + seam intact.

Tests live in: `tests-bff/admin-surfaces-redesign.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/models/ModelsPage.tsx` `apps/dashboard/components/teams/TeamsPage.tsx` `apps/dashboard/components/routing/RoutingPage.tsx`
Strategy (ordered batches): 1. Models → DataTable (Switch cell). 2. Routing candidates → DataTable (Badge cell, ariaLabel+caption). 3. Teams → DataTable (button/budget-form/delete cells). 4. Settings = no change (documented). 5. bff suite green + add.py check.
Safety rule (feature-specific): presentation-only — never touch a BFF route/queryKey/request field/response field; reuse the EXISTING DataTable seam (no components/ui edits this task); preserve every frozen hook (aria-labels, native <select>, role=status/alert, table accessible name, row counts, dialog aria-modal).
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

- [x] all tests pass — `npm test` (vitest run) 42 files / 323 passed, 0 failed (both projects)
- [x] coverage did not decrease — `vitest run --coverage` exit 0, All files 89.77% lines ≥ 80% (ModelsPage 90.47 · RoutingPage 90.47 · TeamsPage 93.93)
- [x] no test or contract was altered during build — tripwire clean; only the 3 declared SRC files touched; `add.py check` 38/0
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) verdict EARNED: 6/6 checklist NONE (zero data-seam drift, every frozen hook preserved, row-count exact at 2, TeamBudgetForm state survives via TanStack row-key stability, new data-slot asserts discriminating)
- [x] concurrency / timing — N/A (presentation-only; no IO/state-machine change; mutations/queryKeys byte-identical)
- [x] no exposed secrets, injection openings, or unexpected dependencies — R6 intact (no new deps; reused existing DataTable + @tanstack/react-table); no secrets; native <select>s untouched
- [x] layering & dependencies follow CONVENTIONS.md — surfaces compose the shared DataTable block; R3 N/A (no components/ui edits this task)
- [x] a person reviewed and approved the change — Tin Dang (standing auto-mode authorization, 2026-06-16)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: DataTable + in-component `columns` (Models/Teams) + module `CANDIDATE_COLUMNS` (Routing) all consumed; cell renderers wire the SAME handlers (handleToggle, setSelectedId, setDeleteTarget, TeamBudgetForm). Confirmed by tsc (0 errors) + green suite.
- [x] DEAD-CODE (code) — removed now-unused Table/TableHeader/TableBody/TableRow/TableHead/TableCell(/TableCaption) imports from all three; eslint (0 errors) + tsc (0 errors) confirm no orphan.
- [ ] SEMANTIC (prose / non-code) — N/A (code task)

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: — · ticket: — · expires: —   (never for a security gap)
Reviewed by: Tin Dang (standing auto-mode authorization) · date: 2026-06-16

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the frozen admin suites (model-mgmt, teams-governance, routing-health, tenant-settings) are the regression monitors for any future admin restyle.
Spec delta for the next loop: DataTable now proven to host interactive cells (Switch, button, stateful inline form, Badge) via in-component `columnDef.cell` closures — the repeatable recipe for migrating ANY flat interactive table.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · folded] tsc's incremental `tsconfig.tsbuildinfo` is the SAME scope-baseline pollutant as `coverage/` — any `tsc --noEmit` between the tests→build snapshot and the gate trips `scope_violation` on a gitignored artifact (evidence: WARN after `tsc`; fixed by re-snapshotting tests→advance and running ONLY `npm test` for the gate). Reinforces the candidate engine fix: extend `_SCOPE_EXCLUDE_DIRS`/files to gitignored build artifacts (`coverage`, `*.tsbuildinfo`).
- [UDD · folded] DataTable can host fully interactive rows (Switch toggle, name-button, inline stateful TeamBudgetForm, delete) via in-component `columnDef.cell` closures with `enableSorting:false` — adoption no longer means "display-only tables"; row-key stability keeps in-cell form state across mutations (evidence: teams-governance budget-save + 409 row-count(2) stayed green).
- [TDD · folded] For interactive-cell restyles the new red→green suite needs only the `data-slot` adoption marker + a couple of frozen-hook spot-checks per surface; the dense behavioral frozen suites (mutations, validation, dialogs, axe) ARE the safety net (evidence: refute-read found zero behavioral drift across all 6 admin suites — the adoption suite asserts only presentation).
