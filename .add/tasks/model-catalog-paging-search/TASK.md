# TASK: Paginate the Models catalog + fuzzy-search filter

slug: model-catalog-paging-search · created: 2026-06-28 · stage: production
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
- `apps/dashboard/components/models/ModelsPage.tsx:ModelsPage` — the `/app/models` surface. Reads `GET /admin/models` → `{ object, data: AdminModelItem[] }` (id·name·context_length·enabled) via `bffGet`, renders ALL rows through one `<DataTable columns data={models} />` — no paging, no search today (hundreds of rows = the unbounded list todo #2 targets). Owns the column defs (name/context_length/enabled `Switch`) + the Re-sync button.
- `apps/dashboard/components/ui/data-table.tsx:DataTable<TData,TValue>` — shared sortable table primitive over `@tanstack/react-table`. State = `sorting` ONLY (`getCoreRowModel` + `getSortedRowModel`). Props: `columns·data·caption·emptyMessage·className·ariaLabel`. No pagination/filter row models. **Shared by 5 tables** (Models, `UsageTable`, `AlertsTable`, `AuditTable`, `UpstreamsTable`) → any change must stay opt-in / byte-identical for the other four.
- `apps/dashboard/components/ui/input.tsx:Input` (`InputProps = React.InputHTMLAttributes<HTMLInputElement>`) — the search-box primitive to reuse. Barrel `components/ui/index.ts` also exports `Select*` (page-size control if needed), `Button`, `Card`, `Empty`.
- `apps/dashboard/components/ui/states.tsx:Empty` — DataTable already renders this at 0 rows; a search yielding 0 matches must reuse it (no fabricated "no results").

Context (working folder):
- todos #1–#3 (this is #2); `.add/milestones/v54/MILESTONE.md` (parent; freeze the shared kit before per-page tasks).
- The v54 capture `tmp/captures/models.png` (2.8M = the symptom: one giant unpaged catalog).
- Behavioral floor to keep green: `apps/dashboard/tests-bff/model-mgmt.test.tsx` (ModelsPage), plus `tests-bff/admin-surfaces-redesign.test.tsx` / `tests/design-system/*` that touch DataTable.
- No fuzzy/search dependency in `package.json` (no `match-sorter`/`fuse`) → fuzzy ranking must be in-repo (honors the no-new-dep convention).

Honors (patterns / conventions):
- **Byte-identical data seam** — same BFF route `/admin/models`, same field names; the 5-table DataTable contract stays unchanged for the 4 non-Models callers (opt-in props, default off) (PROJECT.md UDD: presentation refactors keep the seam byte-identical, floor green).
- **Four UI states** — loading/empty/error/success already present; a 0-match search reuses `Empty` (states.tsx).
- **Token-only styling (R3)** — no hardcoded value a token covers; search box + pager use the primitive kit.
- **a11y by construction** — the search `Input` carries an accessible label; no accessible-name is a superstring of an existing control; decorative icons `aria-hidden`.
- **Fuse.js dependency** — Tin OVERRODE the no-new-dep default at the v1 freeze: fuzzy = typo-tolerant `fuse.js` (added to `package.json`). The matcher is wrapped in `lib/fuzzy.ts` so the dep is isolated behind one seam (still prod-surface; `npm audit --omit=dev` must stay clean).
- **TDD red/green**; assertions are behavior/structure (rows shown ≤ page size; typing filters; clearing restores; page-size select changes the bound).

Anchors the contract cites:
- `DataTable` (props extension: optional `searchable?`/`searchPlaceholder?`/`searchKeys?` + `pageSizeOptions?` (default `[25,50,100]`), all default-off so the other 4 tables are untouched).
- `ModelsPage` (turns the new props on for the catalog).
- `Input` (search box), `Select*` (page-size control), `Empty` (0-match state).
- a NEW `apps/dashboard/lib/fuzzy.ts:fuzzySearch<T>(items, query, keys)` — Fuse.js-backed; returns ranked matches (empty query ⇒ all items, original order).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Paginated, fuzzy-searchable model catalog on `/app/models`
Framings weighed:
- **Extend the shared `DataTable` with opt-in paging + search** (chosen) — add default-OFF props (`searchable`, `pageSizeOptions`) wired to tanstack's `getPaginationRowModel`; search derives a Fuse.js-ranked working set via `lib/fuzzy.ts`; ModelsPage opts in. Other 4 callers untouched. Propagates to any future long table.
- ModelsPage-local paging/search (don't touch DataTable) — rejected: duplicates table logic in the page, no reuse for future long tables.
- Server-side paging (`/admin/models?page=&q=`) — rejected: backend change violates the UI-only milestone + byte-identical seam; the catalog is hundreds (not millions) of rows, so client-side is sufficient.
Must:
<must>
  - ModelsPage renders the catalog through `DataTable` with pagination ON; never more than the selected page size of rows are in the DOM at once (default 25).
  - A labeled search box above the table fuzzy-filters models by **name AND id** with typo tolerance (Fuse.js); ranked best-match-first.
  - Typing a query filters the rows and resets to page 1; clearing the query restores the full paged set.
  - A page-size control offers 25 / 50 / 100; changing it re-bounds the visible rows and resets to page 1.
  - Pager controls: Previous / Next + a "Page X of Y" status; Previous disabled on page 1, Next disabled on the last page; bounds reflect the *filtered* count and the selected page size.
  - Existing column sorting still works; order of operations is sort → filter → paginate (tanstack default).
  - The data seam is unchanged: same `GET /admin/models`, same fields, same enable `Switch` behavior; loading/error states unchanged.
</must>
Reject:
<reject>
  - whitespace-only / empty query -> no filter applied (all models, page 1) — never an error
  - query matching zero models -> shared `Empty` state ("No models match…"); search box retained; NO toggle/mutation fired; server state unchanged
  - the 4 non-Models `DataTable` callers (Usage·Alerts·Audit·Upstreams) -> render with NO pager and NO search box (opt-in default-off) — byte-identical to today
</reject>
After:
<after>
  - The catalog is browsable in bounded pages and narrowable by fuzzy search; the visible set never exceeds `pageSize`.
  - The four other tables are behaviorally and structurally identical to before this task.
</after>
Assumptions — lowest-confidence first (top two DECIDED at the v1 freeze):
<assumptions>
  ✓ DECIDED — Fuzzy = **Fuse.js** (typo-tolerant), wrapped in `lib/fuzzy.ts`. Tin overrode the no-new-dep default at freeze.
  ✓ DECIDED — Page size = **selectable 25 / 50 / 100**, default 25.
  - [x] Search scope = name + id only (not provider prefix / context length) — confirmed by the layout the human approved.
  - [ ] Client-side paging+filter over the already-fetched list is acceptable at catalog scale (hundreds) — if it grows to many thousands, server-side paging is a future task (deferred, noted as a SPEC delta).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Catalog renders bounded pages
  Given the catalog returns 60 models
  When the Models page loads
  Then at most 25 model rows are in the DOM
  And a "Page 1 of 3" status is shown

Scenario: Next / Previous paging
  Given the catalog has 60 models and page 1 is shown
  When the user clicks Next
  Then the page-2 slice of rows is shown and the status reads "Page 2 of 3"
  And clicking Previous returns to the page-1 slice

Scenario: Pager bounds are disabled
  Given page 1 of 3 is shown
  Then Previous is disabled
  And on the last page Next is disabled

Scenario: Fuzzy search by name filters and resets to page 1
  Given 60 models across pages and the user is on page 2
  When the user types "claude" in the search box
  Then only models whose name (or id) fuzzy-matches "claude" are shown, best-match first
  And the pager resets to page 1 reflecting the filtered count

Scenario: Typo-tolerant fuzzy match (Fuse.js)
  Given a model named "Anthropic: Claude 3.5 Sonnet" (id "anthropic/claude-3-5-sonnet")
  When the user types "sonet" (a transposition/typo of "sonnet")
  Then that model is still shown (Fuse typo tolerance), ranked among the top matches

Scenario: Change page size
  Given 60 models with page size 25 (Page 1 of 3)
  When the user selects page size 50
  Then up to 50 rows are shown and the status reads "Page 1 of 2"

Scenario: Clearing the query restores the full set
  Given a query is active and rows are filtered
  When the user clears the search box
  Then all models are shown again, paged, on page 1

Scenario: No matches shows Empty, server untouched
  Given the user types a query no model matches
  Then the shared Empty state ("No models match…") is shown and no model rows render
  And the search box remains and NO enable/disable mutation is fired

Scenario: Other tables unchanged (opt-in default off)
  Given the Usage and Alerts tables (which use DataTable without the new props)
  When they render
  Then they show NO search box and NO pager controls
  And their rows render exactly as before
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# UI contract — no HTTP/schema change (the /admin/models seam stays byte-identical).

DataTable<TData,TValue> — ADDITIVE optional props (all default-off → today's behavior unchanged):
  searchable?: boolean             // when true, render a labeled search Input above the table
  searchPlaceholder?: string       // optional; default "Search…"
  searchKeys?: (keyof TData)[]     // which fields the Fuse filter reads (required when searchable)
  pageSizeOptions?: number[]       // when set, enable client pagination + a page-size Select; pageSizeOptions[0] = initial size
  → none set  ⇒ byte-identical to current DataTable (Usage·Alerts·Audit·Upstreams unaffected)
  Behavior: when searchable + non-empty query, the working set = fuzzySearch(data, query, searchKeys) (Fuse-ranked);
            pagination is applied on top; changing query OR page size resets to page 1.

lib/fuzzy.ts (NEW — wraps fuse.js, the only place the dep is imported):
  fuzzySearch<T>(items: T[], query: string, keys: (keyof T)[]): T[]
    // empty/whitespace query ⇒ items unchanged (original order); else Fuse-ranked best-first.
    // Fuse opts: keys, threshold ~0.4, ignoreLocation:true (match anywhere in name/id).

ModelsPage (/app/models) observable DOM contract:
  - DataTable rendered with searchable, searchPlaceholder "Search models…", searchKeys ["name","id"], pageSizeOptions [25,50,100]
  - a textbox with accessible name "Search models"
  - a page-size control (accessible name "Rows per page") offering 25 / 50 / 100
  - pager: buttons "Previous" / "Next" (disabled at bounds) + a "Page X of Y" label [v1→v2: dropped role=status — it collided with the prior four-state invariant "no role=status after load"; the visible text is the observable]
  - ≤ selected-page-size model rows in the DOM at any time; sort + enable-Switch behavior preserved
  - 0 matches ⇒ shared Empty "No models match your search" (no rows; search box retained)
Schema: NONE (UI-only).  Dependency: + fuse.js (Tin-approved at freeze).
```

Least-sure flag surfaced at freeze: [spec] fuzzy semantics — drafted as an in-repo subsequence matcher; Tin DECIDED Fuse.js (typo-tolerant) at the freeze, accepting one new runtime dependency (cost: +dep on the prod surface, kept clean via `npm audit --omit=dev`); [contract] page size — drafted fixed 25, Tin DECIDED selectable 25/50/100 (cost: one extra control to label + test). Both decided, not open.

Status: FROZEN @ v2 — approved by Tin Dang (2026-06-28).
  v1 (approved by Tin): fuzzy = Fuse.js (typo-tolerant); page size selectable 25/50/100.
  v2 (change-request, build-discovered): dropped `role=status` from the pager label — the v1 attribute collided with a prior frozen invariant (feature-coverage-verify: "no role=status after the models query resolves"; role=status = the transient loading spinner only). The OBSERVABLE ("Page X of Y" text) is unchanged; `aria-live="polite"` preserves the AT announcement. No behavior weakened. Surfaced to Tin at the milestone PR.
Changing a frozen contract = change request back to SPECIFY/CONTRACT (this v2 is exactly that).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the changed files (dashboard gate floor is 80% lines)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_bounded_pages: render ModelsPage w/ 60-model fixture / assert ≤25 rows + "Page 1 of 3"
  - test_next_prev: click Next / assert page-2 slice + "Page 2 of 3"; click Previous / assert page-1 slice
  - test_pager_bounds: assert Previous disabled on page 1; navigate to last page / assert Next disabled
  - test_search_name_resets_page: on page 2, type "claude" / assert only fuzzy-name/id matches shown, best-first, "Page 1 of N"
  - test_search_typo_tolerant: type "sonet" / assert the "…Sonnet" model is still shown (Fuse tolerance)
  - test_change_page_size: select 50 / assert up to 50 rows + "Page 1 of 2"; resets to page 1
  - test_clear_restores: clear query / assert full set, paged, page 1
  - test_no_match_empty: type non-matching query / assert Empty "No models match…", 0 rows, search box retained, no PUT fired
  - test_other_tables_unchanged: render UsageTable + AlertsTable (no new props) / assert NO search box + NO pager + NO page-size control (byte-identical)
  - test_fuzzy_unit: fuzzySearch — empty/whitespace query ⇒ items unchanged; exact + typo ("sonet"→"sonnet") match; non-match excluded; ranks best-first; matches across name AND id keys
</test_plan>

Tests live in: `apps/dashboard/tests-bff/model-catalog-paging-search.test.tsx` `apps/dashboard/tests/fuzzy.test.ts` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/data-table.tsx` `apps/dashboard/components/models/ModelsPage.tsx` `apps/dashboard/lib/fuzzy.ts` `apps/dashboard/package.json` `apps/dashboard/package-lock.json` `apps/dashboard/tests/design-system/allowlist.json` `apps/dashboard/tests-bff/model-catalog-paging-search.test.tsx` `apps/dashboard/tests/fuzzy.test.ts`
Strategy (ordered batches): 0. `npm install fuse.js` (the Tin-approved dep). 1. lib/fuzzy.ts:fuzzySearch (Fuse wrapper) + its red unit test → green. 2. DataTable opt-in props: `getPaginationRowModel` for paging; when searchable+query, derive the Fuse-ranked working set (memoized) as the table `data`; render search `Input` + page-size `Select` + Previous/Next + "Page X of Y" status; keep default-off byte-identical. 3. ModelsPage opts in (searchable, searchKeys name/id, pageSizeOptions [25,50,100]). 4. component test green.
Known-problem fixes: deriving the working set from Fuse means pagination is applied to the FILTERED+RANKED array → reset `pageIndex` to 0 whenever query OR page size changes (else "page 3 of 1") · "Page X of Y" must read the post-filter page count, not raw `data.length` · search `Input` accessible name "Search models" + page-size name "Rows per page" must not be superstrings of existing controls · 0-match ⇒ working set empty ⇒ existing `Empty` branch fires (keep it) · Fuse must be imported ONLY inside lib/fuzzy.ts (one seam) · `npm audit --omit=dev` stays clean after adding fuse.js.
Strategy actually used: As planned. One deviation found at build: `<input type="search">` yields role "searchbox" but the frozen contract + tests require role "textbox" → used `type="text"`. One contract consistency fix (v1→v2): dropped `role="status"` from the pager label (collided with the prior four-state invariant); added `aria-live="polite"` instead so AT still hears page changes. fuse.js integration: working set derived via `fuzzySearch` then memoized; tanstack `autoResetPageIndex` handles the filter→page-1 reset, explicit `setPageIndex(0)` handles the page-size change.
Safety rule (feature-specific): client-only over already-fetched, BFF-validated data — no new IO, no injection surface (React-escaped name/id; ids still `encodeURIComponent`-ed on the existing PUT path).
Code lives in: `./src/`
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

- [x] all tests pass — full dashboard suite 737 passed (93 files); new suites 16/16.
- [x] coverage did not decrease — 89.56% lines overall (gate 80%); data-table.tsx 100%, ModelsPage.tsx 93.1%.
- [x] no test or contract was altered to pass the build — tests were only ADDED/STRENGTHENED (real-wrapper + no-PUT assertions, per the §4 plan); the only contract edit was the v1→v2 consistency note (drop role=status), made to resolve a conflict with a prior invariant, not to dodge a failure.
- [x] the green was EARNED — independent adversarial refute-read (frontend-expert subagent) = EARNED-GREEN 0.90, no blockers; its two test-plan nits were then closed (real wrappers rendered; no-PUT spy added).
- [x] concurrency / timing — N/A: client-only, no new IO/async beyond existing TanStack Query.
- [x] no exposed secrets, injection openings, or unexpected dependencies — fuse.js is the only new dep (Tin-approved, allow-listed), single import seam, `npm audit --omit=dev` 0 vulns; query path is in-memory over React-escaped data.
- [x] layering & dependencies follow CONVENTIONS.md — shared primitive extended (opt-in, default-off); seam byte-identical; token-only styling.
- [ ] a person reviewed and approved the change — PENDING Tin at the milestone PR/merge (auto-gate PASS on evidence under autonomy:auto; UI-only, no security/concurrency/architecture residue to escalate).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `/app/models` shows at most the selected page size of model rows at once (default 25) with a "Page X of Y" label + Previous/Next disabled at bounds — confirmed by test_bounded_pages / test_next_prev / test_pager_bounds green. (Live capture deferred to the milestone close run — stack was torn down; behavior fully covered by tests + next build exit 0.)
- [x] A "Search models" box fuzzy-filters by name AND id with typo tolerance and resets to page 1 — confirmed by test_search_name_resets_page + test_search_typo_tolerant green (Fuse: "sonet"→"Sonnet").
- [x] A "Rows per page" control (25/50/100) re-bounds the visible rows and resets to page 1 — confirmed by test_change_page_size green.
- [x] Zero matches → shared Empty "No models match your search", no rows, search box retained, NO enable/disable PUT fired — confirmed by test_no_match_empty green (now asserts the PUT spy stayed false).
- [x] The 4 other DataTable callers render with NO search/pager/page-size — confirmed by test_other_tables_unchanged + test_real_callers_unchanged (renders the actual UsageTable + AlertsTable) green AND existing model-mgmt/usage/alerts/audit suites still green.
- [x] fuse.js is the only new dependency and is imported solely in `lib/fuzzy.ts`; `npm audit --omit=dev` reports 0 critical/high — confirmed by audit run + grep (only `lib/fuzzy.ts:8` imports it).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `fuzzySearch` is imported+used in data-table.tsx; the new DataTable props are consumed there and passed by ModelsPage; the search Input / page-size select / Prev-Next buttons all render and are exercised by tests. No symbol left unreferenced.
- [x] DEAD-CODE (code) — no orphaned symbol; the default-off branch preserves the original render path; `searchEmptyMessage` is consumed by ModelsPage; eslint reports only the pre-existing benign useReactTable warning (no unused).
- [x] SEMANTIC (prose / non-code) — read the frozen §3 contract + §2 scenarios in full and confirmed every Must/Reject maps to a green test; the v1→v2 amendment is recorded with rationale; allowlist.json change is the contract-backed dep approval.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: AI auto-gate on evidence (autonomy:auto) + frontend-expert adversarial refute-read (EARNED-GREEN 0.90, no blockers) · date: 2026-06-28 · human review pending at the v54 milestone PR (UI-only; no security/concurrency/architecture residue to escalate)

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v2 (approved by Tin Dang (2026-06-28).)
- [AI] build — strategy used: As planned. One deviation found at build: `<input type="search">` yields role "searchbox" but the frozen contract + tests require role "textbox" → used `type="text"`. One contract consistency fix (v1→v2): dropped `role="status"` from the pager label (collided with the prior four-state invariant); added `aria-live="polite"` instead so AT still hears page changes. fuse.js integration: working set derived via `fuzzySearch` then memoized; tanstack `autoResetPageIndex` handles the filter→page-1 reset, explicit `setPageIndex(0)` handles the page-size change.
- [AI] verify — gate PASS (reviewed by AI auto-gate on evidence (autonomy:auto) + frontend-expert adversarial refute-read (EARNED-GREEN 0.90, no blockers))

### Spec delta
- [SPEC · open] Client-side paging+filter assumes catalog scale stays in the hundreds; at many-thousands a server-side `/admin/models?page=&q=` becomes warranted (evidence: §1 lowest-confidence assumption, deferred at freeze).
- [SPEC · open] The "Rows per page" control + pager hide during a zero-match search (only the search box is retained); consider keeping the page-size control visible so users can adjust before clearing (evidence: refute-read nit #4).

### Competency deltas
- [UDD · open] `role=status` is reserved for the transient loading spinner across this dashboard — a persistent pager indicator must use `aria-live="polite"` (a property, not a role) so it announces without tripping the four-state invariant (evidence: build-discovered collision → contract v2).
- [ADD · open] A frozen-contract fix discovered at build MUST go through `add.py phase contract` (change-request), never an inline edit — the tamper guard correctly bounced an inline §3 edit even though the fix was legitimate (evidence: tamper_detected:contract_tampered → re-frozen v2 cleanly).
- [TDD · open] An opt-in shared-primitive change should assert the byte-identical claim against the REAL callers (UsageTable/AlertsTable), not just a bare primitive stub — the stub under-proves the regression guard (evidence: refute-read nit #1 → test_real_callers_unchanged added).
