# TASK: Overview Strip: Plan tile uses display_name

slug: overview-strip-plan-display-name · created: 2026-07-06 · stage: production
milestone: platform-console-flat-redesign
autonomy: auto
phase: done
fast: true

> Fast lane — one small task, minimal sections, filled top-to-bottom. The trust floor still
> holds: a FROZEN §3 contract · ≥1 red test before build · a recorded §6 gate (security = HARD-STOP).
> The acceptance scenario collapses into §1 `Accept:`; OBSERVE is one optional line at the gate.

---

## 0 · GROUND — the real codebase

Touches (files · symbols):
  - `apps/dashboard/components/platform/PlatformTenantOverviewStrip.tsx:PlatformTenantOverviewStrip`
    — the Plan tile's `value={planQuery.data?.plan?.name ?? "No plan"}` line (currently renders the
    machine slug, e.g. "team").
  - `apps/dashboard/tests/platform-tenant-overview-strip.test.tsx` — 9 assertions across 9 tests,
    all `expect(screen.getByTestId("platform-overview-plan-value")).toHaveTextContent("team")`
    (lines 178, 200, 296, 324, 342, 416, 427, 451, 462); the `PLAN_OK` fixture (line 47) already
    carries both `name: "team"` and `display_name: "Team"` — unchanged, it correctly models a real
    API response where the two differ.
Context (working folder):
  - `.add/tasks/tenant-overview-strip/TASK.md` §7 Spec delta — this task resolves that
    `[SPEC · open]` finding: the just-shipped Strip's §3 CONTRACT literally froze `value=plan?.name`,
    which the build agent implemented exactly as specified and correctly flagged rather than
    silently deviating from. Tin confirmed the fix via direct question (chat, 2026-07-06):
    "Fix to display_name (Recommended)".
Honors (patterns / conventions):
  - Every other plan-facing surface in this codebase renders `plan.display_name` for human text —
    `PlatformPlanCatalog.tsx`'s `CardTitle` and `PlatformPlanTab.tsx`'s "Assign {plan.display_name}"
    — confirmed via grep (zero existing surfaces render `.name` for display). This task brings the
    Strip in line with that established convention.
Anchors the contract cites:
  - `components/platform/PlatformTenantOverviewStrip.tsx:PlatformTenantOverviewStrip` (the Plan tile)

---

## 1 · SPECIFY — the rules

Feature: Overview Strip's Plan tile shows the plan's human-readable display name, not its slug
Must:
  - The Plan tile's value renders `plan?.display_name` (falls back to `"No plan"` when `plan` is
    null), matching `PlatformPlanCatalog`/`PlatformPlanTab`'s own convention.
Reject:
  - (none — pure display-field swap, no new failure mode)
Accept: Given a tenant with an assigned plan whose `display_name` is "Team" (slug `name: "team"`),
  when the Tenant Overview Strip renders, then the Plan tile's value reads "Team", not "team".
Assumptions: none material — biggest risk: some other test file outside the 2 already-grepped
  (`platform-tenant-overview-strip.test.tsx`, `platform-tenant-detail.test.tsx`) asserts the literal
  "team" string against this tile; grep confirms neither test in this task's own file collection nor
  the sibling detail-shell file has any other such assertion.

---

## 3 · CONTRACT — freeze the shape

```
PlatformTenantOverviewStrip.tsx — Plan tile, single-line change:
  before: value={planQuery.data?.plan?.name ?? "No plan"}
  after:  value={planQuery.data?.plan?.display_name ?? "No plan"}

tests/platform-tenant-overview-strip.test.tsx — 9 assertions updated in lockstep:
  before: .toHaveTextContent("team")
  after:  .toHaveTextContent("Team")
  (the PLAN_OK fixture itself is unchanged — it already declares both name and display_name)

No other file, line, or test changes.
```

`Least-sure flag surfaced at freeze:` [test] the 9-assertion count was obtained by grep just before
this freeze — if a 10th assertion exists somewhere ungrepped, it would still show "team" post-fix and
fail loudly (never silently), so the cost of being wrong is a caught, not a silent, failure.
Status: FROZEN @ v1 — approved by Tin Dang

---

## 4 · TESTS — failing-first (red)

Plan: test_<accept> — assert the §1 Accept line's Then (behavior, not internals).
Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code

Scope (may touch):
  - `apps/dashboard/components/platform/PlatformTenantOverviewStrip.tsx` (1-line value-expression change)
  - `apps/dashboard/tests/platform-tenant-overview-strip.test.tsx` (9 assertions, literal string only)
Strategy & known-problem fixes: (1) update the 9 test assertions first, confirm RED against the
  unchanged component; (2) flip the component's one value expression; (3) confirm GREEN on the
  touched file, then the full suite. No known-problem traps — a pure literal-string/field-name swap.
Strategy actually used: as planned. RED confirmed at PASS (11) FAIL (6) — exactly the 6 distinct
  tests (of 9 assertion occurrences, 3 tests each carry 2) referencing the Plan tile's value; GREEN
  confirmed at PASS (29) FAIL (0) on the two touched files, then PASS (1057) FAIL (0) project-wide.
Code lives in: `apps/dashboard/components/platform/`   ·   Constraints honored: no test assertion
  was weakened (9 updated from an old-correct-per-old-contract value to a new-correct-per-new-contract
  value, in lockstep with the component change, never to dodge a result); no contract edited during
  build (this task's OWN §3 was frozen first, per the fast lane); no new dependency.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no test or contract altered during build — independently
      re-run by the orchestrator: full suite 1057/1057 (same count as before this task — a pure
      text-content fix adds/removes no test), 0 lint errors (2 pre-existing unrelated warnings),
      `tsc --noEmit` shows the same 9 pre-existing errors confined to `platform-plan-tab.test.tsx`
      (unrelated, recurring from before this task)
- [x] green was EARNED — no overfit / vacuous asserts / stubbed-away logic: the RED state was
      directly observed (PASS 11 FAIL 6, DOM showing "team" vs assertion expecting "Team") before
      the fix, and GREEN directly observed after — a real red-to-green transition, not a
      pre-satisfied assertion
- [x] no exposed secrets, injection openings, or unexpected dependencies — pure display-field swap,
      zero new dependency, zero new surface

Build expectations (from §1 Accept + §3 CONTRACT): the Plan tile renders "Team" (not "team") for a
  tenant whose plan has `display_name: "Team"` — confirmed directly: `PlatformTenantOverviewStrip.tsx`
  now reads `plan?.display_name`, and all 9 test assertions (6 distinct tests) pass against the
  literal "Team" string.

### GATE RECORD
Outcome: PASS
Reviewed by: self (Claude, orchestrator — independently re-ran the full suite/lint/tsc, observed the
real RED before the fix and real GREEN after) · date: 2026-07-06

[SPEC · seeded] this closes the `[SPEC · open]` finding logged in `tenant-overview-strip`'s §7
(evidence: this task's own GATE RECORD above — the Plan tile now renders `display_name`, matching
every other plan surface in the codebase).
