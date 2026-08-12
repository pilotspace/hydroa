# TASK: Per-key bandwidth panel on /keys

slug: bandwidth-panel · created: 2026-06-24 · stage: production
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
  NEW (this task owns):
  - `apps/dashboard/components/keys/BandwidthPanel.tsx` — NEW read-only client panel, a near-exact
    twin of `RatelimitsPanel.tsx` (90 lines). `useQuery({queryKey:["admin-bandwidth"], queryFn:()=>
    bffGet<BandwidthData>("/admin/bandwidth")})` → `<DataTable>` inside an aria-labelled `<section>`;
    four states loading/error/empty/success via `Loading`/`ErrorState`/`DataTable` from `@/components/ui`.
  - `apps/dashboard/tests/bandwidth.test.tsx` — NEW vitest suite, mirrors `tests/ratelimits.test.tsx`
    (149 lines): msw `http.get(`${APP}/api/gw/admin/bandwidth`)` + the four-state + null→"—" assertions.

  CHANGES (this task owns):
  - `apps/dashboard/components/keys/KeysPage.tsx` — import `BandwidthPanel` (sibling of the existing
    `import { RatelimitsPanel } from "./RatelimitsPanel"` @27) and mount `<BandwidthPanel />` beside
    `<RatelimitsPanel />` @306 (same admin-reachable /keys page).

  CONSUMES (FROZEN — exists, do NOT change):
  - gateway `GET /admin/bandwidth` (usage/api/router.py:841, FROZEN @ v1) → response envelope
    `{ enabled: bool, rate_per_sec: int, burst: int, keys: [{ key_id: str, name: str, level: int|null }] }`.
    `level` = refill-adjusted current bucket level; null when the key is untouched / Redis down /
    pacing disabled. `burst` = capacity. Tenant-scoped + owner/admin enforced server-side.
  - `apps/dashboard/lib/bff-client.ts:bffGet<T>(path)` — typed GET through the `/api/gw/[...path]`
    BFF proxy; throws `BffError` (carries `problem.title`) on non-2xx → drives the error state.
  - `@/components/ui` barrel: `DataTable`(columns, data, ariaLabel, emptyMessage) · `Loading`(label)
    · `ErrorState`(title); `@tanstack/react-table` `ColumnDef`; `@tanstack/react-query` `useQuery`.

Context (working folder):
  - The v36 backend shipped `GET /admin/bandwidth` with NO UI (the trigger for v37). This panel is the
    render-only consumer; no gateway/BFF change.
  - DISPLAY: show each key's `level` vs `burst` as "level / capacity" (mirror ratelimits "current /
    limit"). null `level` → "—" (unknown — Redis down / untouched / disabled), NEVER "0". When
    `enabled` is false the section still renders but reads as disabled (levels all "—") — confirm the
    exact disabled affordance at §1/§3.

Honors (patterns / conventions):
  - The established read-only viewer recipe (RatelimitsPanel): four states, null→"—" never 0, aria
    `region` + `h2` heading, owner/admin enforced by the gateway not the FE.
  - npm-test-only gate (dashboard: vitest + a11y + build); design is INHERITED from RatelimitsPanel
    (reuse, not a net-new UDD design loop) — see milestone shared decisions.

Anchors the contract cites:
  - `BandwidthPanel` · `bffGet("/admin/bandwidth")` · the response envelope `{ enabled, rate_per_sec,
    burst, keys:[{key_id, name, level}] }` · the "level / capacity" + null→"—" render rule · the
    KeysPage mount point.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A read-only "Bandwidth usage" panel on /keys showing each key's current bucket level vs capacity
Framings weighed:
  - Twin of RatelimitsPanel, mounted on /keys (CHOSEN) — same admin page, same four-state recipe, one
    DataTable, "level / capacity" mirroring "current / limit". Lowest-risk: reuses an approved surface.
  - A standalone /bandwidth route — rejected: per-key throughput belongs next to per-key rate limits on
    /keys; a new route adds nav + a near-empty page for one table.
  - A column added to the existing keys table — rejected: levels are live/pollable (their own query +
    loading/error lifecycle); bolting them onto the static keys table tangles two refresh models.
Must:
<must>
  - Render an aria-labelled `region` "Bandwidth usage" section that fetches GET /admin/bandwidth via
    `bffGet` (through the /api/gw BFF proxy), keyed `["admin-bandwidth"]`.
  - Per key row: show the key `name` and its level vs capacity. When `level` is a number → `{level} / {burst}`.
    When `level` is null → "—" (unknown: Redis down / untouched / pacing disabled), NEVER "0".
  - Render the four states like every dashboard surface: loading (a status/aria-busy spinner),
    error (ErrorState carrying the BffError title, no table rows), empty (a "No keys" message, no error),
    success (DataTable of rows).
  - Show a caption of the configured capacity: when `enabled` → "{rate_per_sec} tokens/sec · burst {burst}";
    when `enabled` is false → a "Pacing disabled" caption (so an all-"—" table is not read as a fault).
  - Mount `<BandwidthPanel />` on the existing /keys page beside `<RatelimitsPanel />`; no other page changes.
  - Rely on the gateway for owner/admin authz (the endpoint is require_owner_or_admin); the FE never
    re-implements the role check.
</must>
Reject:
<reject>
  - GET /admin/bandwidth returns non-2xx (bffGet throws BffError) -> ERROR STATE: show the error title,
    render ZERO table rows (never a half-table).
  - keys array empty -> EMPTY STATE: "No keys" message, and NO error/alert shown.
  - a key's level is null -> render "—" (unknown), never "0" (which would falsely read "fully throttled").
</reject>
After:
<after>
  - A returning owner/admin opening /keys sees each key's live bandwidth level against capacity (or an
    honest "—"/"Pacing disabled") without needing to curl GET /admin/bandwidth.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The DISABLED + null-level cell format — `level===null ? "—" : ${level} / ${burst}` plus an
    enabled/disabled caption — is the right honest readout. Lowest confidence because the endpoint
    returns `burst:0` + all `level:null` when pacing is OFF, so a naive "{level} / {burst}" would print
    "— / 0"; if my caption+bare-"—" choice is wrong the disabled state reads confusingly (cosmetic, no
    data risk). THIS is the §3 freeze flag.
  - [x] Capacity denominator is `burst` (the bucket max, the "out of" number), not `rate_per_sec` —
    confirmed from the v36 contract (level ∈ [−burst, burst], burst = full).
  - [x] bffGet path is "/admin/bandwidth" (the BFF prefixes /api/gw) — confirmed: RatelimitsPanel uses
    bffGet("/admin/ratelimits") and msw mocks `/api/gw/admin/ratelimits`.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: renders level vs capacity (enabled)
  Given GET /admin/bandwidth returns enabled=true, rate_per_sec=100, burst=200,
        keys=[{name:"prod-key", level:150}]
  When the BandwidthPanel mounts on /keys
  Then the "Bandwidth usage" region shows "prod-key" and "150 / 200"
  And a caption shows "100 tokens/sec" and "burst 200"

Scenario: null level renders unknown, never zero
  Given GET /admin/bandwidth returns a key with level=null
  When the panel renders
  Then that key's level cell shows "—"
  And it does NOT show "0" (which would falsely read "fully throttled")

Scenario: pacing disabled caption
  Given GET /admin/bandwidth returns enabled=false, rate_per_sec=0, burst=0, keys=[{name:"k", level:null}]
  When the panel renders
  Then a "Pacing disabled" caption is shown
  And the key row's level shows "—" (not "— / 0")

Scenario: empty state
  Given GET /admin/bandwidth returns keys=[]
  When the panel renders
  Then a "No keys" empty message is shown
  And NO error/alert is shown

Scenario: error state
  Given GET /admin/bandwidth returns 500
  When the panel renders
  Then the error title is shown
  And ZERO table rows are rendered

Scenario: loading state
  Given GET /admin/bandwidth never resolves
  When the panel renders
  Then a loading spinner (status / aria-busy) is shown
  And ZERO table rows are rendered
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Component: BandwidthPanel()  (apps/dashboard/components/keys/BandwidthPanel.tsx)
  CONSUMES (existing, FROZEN @ v36): GET /admin/bandwidth  via bffGet("/admin/bandwidth")
    200 -> { enabled: boolean, rate_per_sec: number, burst: number,
             keys: [{ key_id: string, name: string, level: number | null }] }
    non-2xx -> bffGet throws BffError (.problem.title)

  RENDERS: <section role="region" aria-label="Bandwidth usage"> with an <h2> "Bandwidth usage"
    - caption: enabled  -> "{rate_per_sec} tokens/sec · burst {burst}"
               !enabled -> "Pacing disabled"
    - state machine (mutually exclusive):
        loading -> <Loading> (role=status / aria-busy), 0 rows
        error   -> <ErrorState title={BffError.title}>, 0 rows
        success -> <DataTable columns=[Key, Level] data=keys ariaLabel="Bandwidth level per key"
                            emptyMessage="No keys">
    - Level cell:  level === null  ->  "—"            (unknown; NEVER "0")
                   level is number ->  "{level} / {burst}"
  EXPORTS: `export function BandwidthPanel()` ; TS interfaces BandwidthRow {key_id,name,level} +
           BandwidthData {enabled,rate_per_sec,burst,keys}.

Mount: KeysPage.tsx renders <BandwidthPanel /> beside <RatelimitsPanel />. No new route, no nav change.
Schema: none (read-only FE; no DB, no new endpoint, no BFF change).
```

Status: FROZEN @ v1 — approved by Tin 2026-06-24 (chose "freeze as drafted": null level → bare "—" + "Pacing disabled" caption; over "— / 0" literal or hide-table). Least-sure flag surfaced at freeze: [contract] the disabled/null-level cell format (cosmetic-only risk).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: the panel component (mirror ratelimits.test.tsx — 6 behavioral tests, msw-mocked endpoint)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_bandwidth_renders_level_vs_capacity: mock 200 {enabled:true,rate_per_sec:100,burst:200,
    keys:[{name:"prod-key",level:150}]} / render / assert "prod-key" + "150 / 200" + caption "100 tokens/sec".
  - test_bandwidth_null_level_renders_unknown: mock a key level:null / render / assert "—" present AND
    no "0" in that row.
  - test_bandwidth_disabled_caption: mock {enabled:false,rate_per_sec:0,burst:0,keys:[{name:"k",level:null}]}
    / render / assert "Pacing disabled" caption AND the row shows "—" (not "— / 0").
  - test_bandwidth_empty_state: mock {..,keys:[]} / render / assert "No keys" AND no role=alert.
  - test_bandwidth_error_state: mock 500 / render / assert error title shown AND 0 rows.
  - test_bandwidth_loading_state: mock never-resolving / render / assert spinner (status/aria-busy) AND 0 rows.
</test_plan>

Tests live in: `dashboard/tests` · MUST run red (missing implementation → MODULE_NOT_FOUND) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/keys/BandwidthPanel.tsx` `KeysPage.tsx` `dashboard/tests/bandwidth.test.tsx`
Strategy (ordered batches):
  1. Write `dashboard/tests/bandwidth.test.tsx` (mirror ratelimits.test.tsx) → run RED (MODULE_NOT_FOUND).
  2. Write `BandwidthPanel.tsx` (twin of RatelimitsPanel: useQuery + bffGet + 4 states + Level cell + caption).
  3. Mount `<BandwidthPanel />` in KeysPage.tsx beside RatelimitsPanel → green.
Safety rule (feature-specific): READ-ONLY render — no mutation, no new endpoint/route/nav; the BffError
  path must render the error state (never an unhandled throw); null level → "—" never 0 (no false signal).
Code lives in: `apps/dashboard/components/keys/`
Constraints: do NOT change any test or the contract; reuse @/components/ui + existing deps only (no new
  package); design inherited from RatelimitsPanel; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — bandwidth.test.tsx 7/7; full dashboard vitest 391 passed (was 384; +7); build exit 0
- [x] coverage did not decrease — additive component + 7 new tests; no existing test touched
- [x] no test or contract was altered during build — §3 frozen untouched; one test ADDED post-refute (level:0 guard), then re-crossed tests→build
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) = UPHOLD, 0 blockers. Verified: BffError.message=problem.title flows to the error title; null-guard is `=== null` (0 not collapsed); region a11y name resolves (else section() throws → all red); tests component-specific (region named /bandwidth/). 1 MINOR (no level:0 pin) → FIXED by adding test_bandwidth_zero_level_is_not_unknown.
- [x] concurrency / timing — read-only useQuery; no mutation/races; error path renders state (never an unhandled throw)
- [x] no exposed secrets / deps — no new package; bffGet through existing BFF; carries only key_id+name+int
- [x] layering & dependencies follow CONVENTIONS.md — exact twin of RatelimitsPanel; reuses @/components/ui; FE never re-implements authz
- [ ] a person reviewed and approved the change — PENDING Tin (commit/PR held)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] /keys shows a "Bandwidth usage" region with each key's "{level} / {burst}" — confirmed by test_bandwidth_renders_level_vs_capacity + the build rendering /keys (exit 0)
- [x] null level → "—", a real 0 → "0 / burst" (never collapsed) — confirmed by null + zero guard tests
- [x] disabled → "Pacing disabled" caption, no "— / 0" — confirmed by test_bandwidth_disabled_caption
- [x] four states honest (loading/error/empty/success), error shows 0 rows — confirmed by the 4 state tests

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `<BandwidthPanel />` imported + rendered in KeysPage.tsx:310 (beside RatelimitsPanel); levelVsCapacity/makeColumns/caption all on the live render path; reached by all 7 tests
- [x] DEAD-CODE (code) — no orphaned symbol; component is mounted, not just imported
- [x] SEMANTIC — n/a (code task)

### GATE RECORD
Outcome: PASS
Evidence: bandwidth.test.tsx 7/7 · full dashboard vitest 391 passed · tsc clean · eslint 0/0 on both
source files · `npm run build` exit 0 (/keys compiled) · refute-read (sonnet) UPHOLD 0-blockers, 1
MINOR (level:0 pin) FIXED. Twin of the approved RatelimitsPanel; read-only, no new endpoint/route.
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a
Reviewed by: AI auto-gate (autonomy:auto) · human approval (Tin) PENDING for commit/PR · date: 2026-06-24

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] the panel is fetch-once (no auto-poll); a live-updating level would want a refetchInterval or a manual refresh affordance (evidence: ratelimits has the same static-read shape; revisit if operators want live drain).
- [SPEC · open] no per-key rate/burst override exists yet (v36 deferred the column), so the caption shows ONE global capacity for all keys (evidence: v36 backlog delta — when per-key override lands, the panel must show per-row capacity).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] a render-only "null→unknown" cell needs a companion ZERO test — null and 0 are distinct truths and a `!value` falsy refactor silently merges them; pin both (evidence: refute-read MINOR on this task, added test_bandwidth_zero_level_is_not_unknown). [folded foundation-version 34]
- [UDD · folded] mirroring an APPROVED sibling component (RatelimitsPanel) collapses the UDD design loop to "reuse" — inherit its four-state recipe + a11y region pattern verbatim rather than re-deriving a design (evidence: this panel shipped with zero new design decisions beyond the frozen disabled-caption). [folded foundation-version 34]
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
