# TASK: Strict BFF test harness — tsc-clean tests-bff + scoped msw handlers

slug: bff-test-harness-strict-handlers · created: 2026-06-14 · stage: production
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

Touches (files · symbols · signatures): a behavior-preserving hardening of `apps/dashboard/tests-bff/`
(the BFF vitest project). Verified anchors (facts, 2026-06-14, on Next 16.2.9 post-v14):
- **tsc drift — 18 errors across 7 files** (a bare `./node_modules/.bin/tsc --noEmit` over tests-bff;
  `next build` is production-clean, so these are TEST-tree only). Four categories:
  - **TS2353 ×6 — Next 16 async-params** (the only Next-16-induced ones): `route-handlers.test.ts`
    (338,371,404,441) + `patch-passthrough.test.ts` (53,81) construct route-handler context as
    `{ params: { path: [...] } }`, but Next 16 types the 2nd arg's `params` as `Promise<{path:string[]}>`.
    Fix: `{ params: Promise.resolve({ path: [...] }) }` (the handlers already `await` params).
  - **TS2352 ×7 — casts TS wants routed through `unknown`**: `null as Request` (bff-client 97,141;
    govern 491), `window as Record<string,unknown>` (bff-forms 262), `null as Record<string,unknown>`
    (tenant-settings 206,310,311). Fix: cast through `unknown` first (`null as unknown as Request`).
  - **TS2300 ×2 + TS1119 ×1 — duplicate `href`**: `bff-client.test.tsx` (44-45) defines a window/location
    mock with BOTH a `href` data property AND a `href` accessor. Fix: keep one form.
  - **TS2345 ×2 — msw JsonBodyType**: `routing-health.test.tsx` (88), `tenant-settings.test.tsx` (69)
    pass `unknown` into `HttpResponse.json()`. Fix: type the body (or cast to the response shape).
- **msw shared fallbacks** — `tests-bff/mocks/handlers.ts:119-133` ships FOUR broad catch-alls:
  `http.{get,post,put,delete}(\`${APP}/api/gw/:path*\`)` returning generic defaults (GET→`[{key_id,name}]`,
  POST→new-key+secret, PUT→`{budget_usd_monthly}`, DELETE→204). `defaultHandlers = [...gatewayHandlers,
  ...bffHandlers]` (line 136); `mocks/server.ts` = `setupServer(...defaultHandlers)`; `setup.ts:38`
  ALREADY sets `onUnhandledRequest: "error"` — so the `:path*` wildcards are the ONLY thing letting a
  forgotten per-test gw handler return wrong-but-silent data instead of erroring loudly (the v15-folded
  convention names exactly this). 14 test files reference `/api/gw`; most register their own `server.use`
  (15 files use it; teams-governance has 22 server.use / 44 gw-refs) → narrowing the wildcard has a real
  blast radius (tests leaning on the default for incidental calls will error → need explicit per-test handlers).

Context (working folder): v17 MILESTONE.md (root task — react-hooks-strict-lint + devtool-vitest4-upgrade
depend on this). The 236-test floor (30 files) @ 94.03% is the behavior contract. No standalone `typecheck`
npm script today (the production type-gate is `next build`); this task makes a tests-tree tsc gate POSSIBLE.

Honors (patterns / conventions): behavior-preserving is the contract (236 assertions unchanged — making an
implicit default EXPLICIT per-test is preservation, not a behavior change); the v15-folded convention "scope
shared msw fallbacks to the paths that truly need them — a `/api/gw/:path*` wildcard defeats
onUnhandledRequest:error"; the v16-folded convention "the dashboard production type-gate is `next build`,
tests-bff drift tracked separately" — this task DISCHARGES that tracked drift.

Anchors the contract cites: `tests-bff/` tsc-clean (0 errors via `tsc --noEmit`) · `tests-bff/mocks/handlers.ts`
(no `/api/gw/:path*` catch-all in `defaultHandlers`; gw defaults scoped to specific paths) · `setup.ts`
onUnhandledRequest:"error" (unchanged) · the 236-test floor green @ ≥80% cov (assertions unchanged).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a behavior-preserving strict-harness pass over `apps/dashboard/tests-bff/` — make the tree
tsc-clean (fix the 18 errors with real type fixes) and remove the `/api/gw/:path*` catch-all so a
forgotten per-test gw handler ERRORS (onUnhandledRequest:"error") instead of returning silent wrong data.
Framings weighed: real type fixes + narrow the wildcards to scoped defaults, adding explicit per-test
handlers where tests break (chosen — discharges the v15+v16 deltas; behavior-preserving) · blanket
`@ts-expect-error`/`any` to silence tsc (rejected — suppression, not a fix; banned by §Reject) · delete
the wildcards wholesale without back-filling per-test handlers (rejected — would weaken tests that lean
on the default; a real break, not preservation).
Must:
<must>
  - a bare `tsc --noEmit` over `apps/dashboard/tests-bff/` reports ZERO errors (all 18 fixed: 6 async-params
    via `Promise.resolve(...)`, 7 casts via `as unknown as T`, the duplicate-`href` mock, 2 msw body types) —
    REAL type fixes, never `@ts-ignore`/`@ts-expect-error`/`any`-suppression.
  - `defaultHandlers` (tests-bff/mocks/handlers.ts) contains NO `/api/gw/:path*` catch-all; gw defaults are
    scoped to the specific path(s) that truly need them; `setup.ts` keeps `onUnhandledRequest:"error"` so an
    unhandled gw path fails LOUDLY.
  - the 236-test floor (30 files) stays green at ≥80% coverage; NO assertion is weakened or deleted — a test
    that loses a wildcard default gets an EXPLICIT per-test `server.use` returning the SAME data it implicitly got.
  - production code (app/ · components/ · lib/) is UNTOUCHED; no gateway/BFF change; no app behavior change.
</must>
Reject:
<reject>
  - a behavioral assertion weakened/deleted to make the suite pass -> HARD-STOP -> "test_weakened"
  - a tsc error suppressed via @ts-ignore / @ts-expect-error / `any` instead of a real type fix -> "type_suppressed"
  - `onUnhandledRequest` loosened (to "warn"/"bypass") to dodge the scoping work -> "harness_loosened"
  - production/app code changed under a test-harness task -> "scope_creep"
</reject>
After:
<after>
  - tests-bff is tsc-clean (0 errors), defaultHandlers has no `/api/gw/:path*` wildcard, the harness errors on
    unhandled gw paths, and the 236 tests are green at ≥80% cov with every assertion intact.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ narrowing the gw wildcard has an UNKNOWN blast radius — some of the 14 gw-using files may lean on the
    default for incidental requests; lowest confidence because only running the full suite reveals which.
    Cost if wrong: a cascade of per-test handler additions. Mitigation: narrow empirically, run all 236,
    add explicit same-data `server.use` handlers where tests break (behavior-preserving); if the radius is
    unmanageably large, scope the wildcard to the known resource prefixes (keys/budget) rather than full
    removal — still kills the silent-wrong-data mode for every other resource (documented, not hidden).
  - [ ] the 6 TS2353 are the ONLY Next-16-induced errors (the casts/href/JsonBodyType are version-independent,
    would fail on 15 too) — confirm at build by category.
  - [ ] casting through `unknown` is compile-time only (vitest transpiles via esbuild, ignoring types) so no
    runtime behavior changes — confirm via the green suite.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: tests-bff is tsc-clean
  Given tests-bff has 18 tsc errors on Next 16 (async-params, casts, duplicate href, msw body)
  When `tsc --noEmit` runs over the tree after real type fixes
  Then it reports 0 errors
  And no error was suppressed via @ts-ignore / @ts-expect-error / `any`

Scenario: the gw wildcard is gone and unhandled paths error
  Given defaultHandlers ships `http.{get,post,put,delete}(/api/gw/:path*)` catch-alls
  When the wildcards are removed and a test issues a gw call it did not register a handler for
  Then msw raises (onUnhandledRequest:"error") instead of returning silent default data
  And defaultHandlers contains no `/api/gw/:path*` pattern

Scenario: the behavioral floor holds, assertions intact
  Given the 236-test floor (30 files) currently leans partly on the wildcard defaults
  When the suite runs after the wildcards are replaced with explicit per-test handlers
  Then all 236 tests pass at ≥80% coverage
  And every test keeps its original assertions (an explicit handler returns the SAME data the wildcard gave)

Scenario: no scope creep
  Given a test-harness-only task
  When the diff is reviewed
  Then no production code (app/ · components/ · lib/) changed and no app behavior changed
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
HARNESS HARDENING CONTRACT (no runtime API surface — a test-tree quality invariant set, all observable,
verified by a structural guard suite + gate evidence)

tests-bff/ (apps/dashboard):
  - `tsc --noEmit` over the tree           : 0 errors (REAL fixes; zero @ts-ignore/@ts-expect-error/`any` added)
  - tests-bff/mocks/handlers.ts            : `defaultHandlers` contains NO `/api/gw/:path*` substring;
                                             gw defaults (if any remain) are scoped to explicit concrete paths
  - tests-bff/setup.ts                     : `onUnhandledRequest: "error"` UNCHANGED

BEHAVIOR (preserved — the floor contract):
  - the 236-test floor (30 files) stays green; every assertion intact; a test that lost a wildcard default
    gets an EXPLICIT `server.use(http.<m>(<concrete path>, ...))` returning the SAME data
  - production code (app/ · components/ · lib/) byte-unchanged; no gateway/BFF/data-contract change

GATE EVIDENCE (recorded in §6):
  - `tsc --noEmit` (tests-bff) EXIT 0, 0 errors  ·  grep `:path\*` in handlers.ts = none
  - `vitest run --coverage` EXIT 0, 236 tests, ≥80% cov  ·  `git diff` shows no app/components/lib change
```

Least-sure flag surfaced at freeze: [test] the gw-wildcard-narrowing BLAST RADIUS — removing the 4
`/api/gw/:path*` catch-alls may force explicit per-test `server.use` back-fills across some of the 14
gw-using files; a green 236-suite is the proof of preservation, but the EDIT COUNT is unknown until the
suite runs. Why least-sure: only execution reveals which tests leaned on the default for incidental calls.
Cost if wrong: a large (but mechanical, behavior-preserving) set of per-test handler additions. Decision
(auto, behavior-preserving): narrow empirically; if the radius is unmanageable, fall back to scoping the
wildcard to concrete resource prefixes (keys/budget) — documented in §6, never a silent re-add of `:path*`.
Secondary [build]: the 7 cast fixes route through `unknown` (compile-time only; vitest esbuild ignores types
→ zero runtime change, proven by the green suite).

Status: FROZEN @ v1 — auto-approved (autonomy:auto; behavior-preserving test-harness hardening, no security
surface, no production code). The bundle's lowest-confidence flag (wildcard blast radius) is surfaced above
with its empirical mitigation + documented fallback. Changing this frozen contract = change request → SPECIFY.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (the dashboard global gate must still hold).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  NEW `tests-bff/strict-harness.test.ts` (structural guard — RED before Build):
  - test_no_gw_wildcard_in_default_handlers: read mocks/handlers.ts source → asserts it contains no
    `/api/gw/:path*` pattern (RED now — 4 wildcards present; GREEN after they are removed/scoped)
  - test_unhandled_request_is_error: read setup.ts source → asserts `onUnhandledRequest: "error"` present
    (regression guard — GREEN now, must stay; a loosening to warn/bypass turns it RED)
  GATE EVIDENCE (not vitest): `tsc --noEmit` over tests-bff EXIT 0 / 0 errors (RED now — 18 errors).
  FLOOR (unchanged, must stay green): the existing 30 files / 236 tests — the real proof that narrowing
  the wildcards preserved behavior (per-test explicit handlers back-fill any incidental default).
</test_plan>

Tests live in: `./tests-bff/strict-harness.test.ts` (NEW structural guard) · MUST run red (wildcards present
+ tsc 18 errors) before Build. [token note: `tests-bff/` resolves under apps/dashboard, the BFF vitest project]
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/tests-bff/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/bff-test-harness-strict-handlers/`
<!-- SCOPE NOTE (gitignored build artifacts — the v13-folded convention): `coverage/` is regenerated by every
     `vitest run --coverage` gate-evidence run, and `tsconfig.tsbuildinfo` by `tsc` (incremental:true) — both
     gitignored, both must be DECLARED in §5 or the scope-gate flags them (engine _SCOPE_EXCLUDE_DIRS only
     covers .git/.add/__pycache__/node_modules). -->
<!-- SCOPE NOTE: the whole tests-bff/ subtree is declared because the wildcard-narrowing's blast radius
     (which test files need an explicit per-test handler) is empirically discovered at build. The
     substantive writes: the 7 tsc-error files, mocks/handlers.ts (remove/scope the 4 wildcards), the NEW
     strict-harness.test.ts, + per-test server.use back-fills. PRODUCTION (app/ components/ lib/) is OUT
     OF SCOPE — touching it is `scope_creep`. No package.json/lockfile change (no dep add). -->
Strategy (ordered batches): 1. NEW strict-harness.test.ts (RED — wildcards present). 2. Fix the 18 tsc
errors by category (async-params Promise.resolve, casts as-unknown-as-T, dedupe href mock, msw body types)
— verify `tsc --noEmit` over tests-bff hits 0; commit (the certain win). 3. Remove the 4 `/api/gw/:path*`
wildcards from handlers.ts; run the full 236 suite; for each breakage add an EXPLICIT per-test `server.use`
returning the SAME data the wildcard gave (behavior-preserving). 4. GREEN gates: strict-harness.test green,
tsc 0, vitest --coverage EXIT 0, git diff shows no app/components/lib change.
Safety rule (feature-specific): NEVER weaken/delete an assertion to pass; a per-test handler must return the
SAME shape the wildcard returned (preservation). NEVER suppress a tsc error (@ts-ignore/expect-error/any) —
real type fixes only. NEVER loosen onUnhandledRequest. If the wildcard-removal blast radius proves
unmanageably large, FALL BACK to scoping the wildcard to concrete resource prefixes (documented in §6), not
a silent re-add.
Code lives in: `apps/dashboard/tests-bff/`
Constraints: do NOT change the frozen §3 invariants; do NOT touch production/app code; no new dependency.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `vitest run --coverage` EXIT 0: 31 files / 238 tests passed (236 floor + 2 new strict-harness guards). 5/5 stability runs green.
- [x] coverage did not decrease — All files 94.03% stmts / 85.67% branch / 89.09% func; ≥80% global gate held (EXIT 0). Identical to the pre-task 94.03% baseline.
- [x] no test or contract was altered during build to weaken it — the 18 tsc fixes are REAL type fixes (Promise.resolve for async-params, `as unknown as T` double-casts, get/set href dedup, msw body casts via `Parameters<typeof HttpResponse.json>[0]`); the wildcard removal + per-test teams defaults PRESERVE every assertion (no expect weakened/deleted). §3 frozen invariants untouched.
- [x] the green was EARNED, not gamed — adversarial refute-read (subagent, model sonnet) verdict EARNED-WITH-GAPS: confirmed zero suppression keywords, production untouched, casts compile-time-only, the teams `[]` default masks no assertion (govern.test.tsx asserts no teams content; govern-depth's teams tests override via msw prepend), strict-harness regex genuinely goes red on a re-added wildcard. The ONE gap it found — `ui-ux-verify.test.tsx` had 2 forgotten teams handlers (the "2 remaining leaks" I had mis-diagnosed as benign cross-file late-resolves; the reviewer correctly identified them as in-file forgotten handlers) — was FIXED (same beforeEach pattern) → unhandled-request count now 0.
- [x] concurrency / timing — N/A (test-harness change; no production runtime concurrency). The msw handler-ordering (server.use prepend → per-test override wins over the beforeEach default) is verified by the green teams-asserting tests in govern-depth.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no dependency added (no package.json/lockfile change); the only "secret"-shaped fixture is the FAKE `sk-new.SECRET` removed with the POST wildcard. No new prod surface.
- [x] layering & dependencies follow CONVENTIONS.md — discharges the v15-folded "scope shared msw fallbacks" convention + the v16-folded "production type-gate is next build, tests-bff drift tracked separately" delta; this task makes tests-bff tsc-clean.
- [x] auto-gate (autonomy:auto) — behavior-preserving test-harness hardening, NO security surface, NO concurrency/architecture residue → auto-resolved PASS on complete evidence (an explicit PASS, not a skip). No human gate required (escalates only on security / risk:high / conservative — none apply).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — strict-harness.test.ts reads mocks/handlers.ts + setup.ts source (both guards green); the per-test teams beforeEach in govern/govern-depth/ui-ux-verify is consumed by KeyGovernanceEditor's useQuery(["admin-teams"]); the kept concrete `/api/gw/admin/keys` default serves the common read.
- [x] DEAD-CODE (code) — the 4 gw `:path*` wildcards removed (no orphan); no unused import after the fixes; `beforeEach` newly imported where added and used.
- [x] SEMANTIC (prose / non-code) — read in full: the full git diff (test files + handlers.ts + the new guard), the adversarial review report, the msw unhandled-request logs (13→2→0 across the fix iterations), the tsc error list (18→0 by category).

### GATE RECORD
Outcome: PASS — auto-resolved (autonomy:auto). Behavior-preserving harness hardening: tests-bff tsc-clean (0 errors), no `/api/gw/:path*` catch-all, onUnhandledRequest:"error" preserved, 238 tests green @ 94.03% (5/5 stable), production untouched, ZERO unhandled-request leaks. Adversarial refute-read EARNED (its one gap closed). Not a security gate; no residue requiring human escalation.
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: ADD auto-gate (adversarial refute-read: sonnet subagent) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the msw unhandled-request count over `vitest run --project bff` stderr (must stay 0 — a new forgotten handler regresses it); a bare `tsc --noEmit` over tests-bff (must stay 0 — now a CANDIDATE for a standing `typecheck` gate the harness can finally join); the 238-test floor.
Spec delta for the next loop: removing a broad test-mock catch-all is a HIGH-VALUE hygiene move but its blast radius is only knowable empirically — run the suite, read the stderr unhandled-request log (NOT just pass/fail; msw "error" mode in Node resolves a 500 rather than rejecting, so a forgotten handler can LOG loudly yet the test still PASSES), and back-fill the concrete per-test/per-file default. The msw "error→500-resolve not reject" behavior means "0 failures" is NOT proof of "0 leaks" — the stderr count is the real monitor. tests-bff is now tsc-clean → the next loop CAN add a `typecheck` npm script + gate (currently the production type-gate is only `next build`).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · open] msw `onUnhandledRequest:"error"` in Node/jsdom does NOT reject the fetch — the interceptor resolves a 500 Response — so a forgotten handler LOGS loudly but a test that doesn't assert on that request still PASSES; "0 test failures" is NOT "0 leaks". The real monitor is the stderr unhandled-request COUNT (evidence: this task's 13→2→0 reduction; the suite stayed green at all three counts) (evidence: tests-bff/strict-harness.test.ts + the leak-count iterations).
- [ADD · open] an adversarial refute-read catches MIS-DIAGNOSIS, not just cheating: I labeled 2 residual leaks "benign cross-file late-resolves" and was WRONG — the sonnet reviewer traced them to in-file forgotten teams handlers in ui-ux-verify.test.tsx (fixed → 0). Never hand-wave a residual; trace every leak to its source file (evidence: the EARNED-WITH-GAPS review + the ui-ux-verify.test.tsx beforeEach fix that zeroed the count).
- [TDD · open] tests-bff is now tsc-clean → a standing test-tree `typecheck` gate is newly possible; the v16 "production type-gate is next build, tests-bff excluded" delta can tighten to include the harness (evidence: `tsc --noEmit` 18→0 over tests-bff; this task discharges bff-test-harness-strict-handlers).
