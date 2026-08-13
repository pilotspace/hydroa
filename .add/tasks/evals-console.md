---
type: Task
title: evals-console
status: direction
milestone: evals-regression-gate
needs:
  - baseline-and-verdict.md
gives:
  - S1 the evals console section — verdict-first IA, per-case diff signature element, keyboard-navigable, WCAG AA
generated: { by: add/3.2.0, at: 2026-08-12 }
verified:
  - { by: "cli", at: 2026-08-13, act: freeze, authority: process, direction: "sha256:0b0747287fa1ed99" }
  - { by: "cli", at: 2026-08-13, act: brief, authority: process, brief: "sha256:60cf00293d919fbc" }
advised_by: ui-designer
---
## CARD
goal: author sets, launch runs, read the verdict with per-case drill-down — UDD loop, verdict-first IA, per-case diff as the signature element
why: a user-facing surface takes the UDD design loop, not a CRUD table; the verdict is the page's primary object, not a row count
beat: direction · next: add freeze evals-console

## RULES
<must>
- M1 A NEW session-authed control-plane surface `/admin/evals/*` serves the console. It resolves the tenant from the session `Identity` (JWT via `get_current_identity`) — NEVER an API key — and REUSES the frozen eval modules ([[eval-set-store]] `SqlAlchemyEvalStore`, [[eval-run-executor]] `SqlAlchemyEvalRunStore`, [[baseline-and-verdict]] `SqlAlchemyEvalBaselineStore` + `score_run`/`decide`); no scoring/verdict/store logic is re-implemented (R:LOGIC_FORK). Every read/write is tenant-scoped to the session's tenant in the resolving query; an absent OR cross-tenant set/run is a uniform 404 (R:CROSS_TENANT) — the same guarantees the /v1 surface gives, under session auth.
- M2 The surface is READ + basic authoring, and NEVER handles a raw API key (R:RAW_KEY_IN_CONSOLE): GET sets · GET set(+cases) · GET runs · GET run verdict · GET run cases; POST set · POST case · PUT baseline. There is NO launch endpoint here — launching dials upstreams and must bill the launching key as live traffic ([[eval-run-executor]] A1), so it stays on the API-key `/v1` path (surfaced in the UI as "launch via API"). The console holds no key at rest or in flight.
- M3 Verdict-FIRST IA (UDD: named IA · primary object · signature element). The run page's PRIMARY object is the VERDICT: it leads with a pass / fail / no_baseline banner + candidate-vs-baseline scores BEFORE any table (R:VERDICT_NOT_PRIMARY). IA is Sets list → Set detail (cases · runs · pinned baseline) → Run verdict. Routes live under `app/(app)/app/evals/` inheriting the group `DashboardShell` (no competing layout); exactly one `<h1>` via `PageHeader`; content in `<section aria-labelledby>`.
- M4 The per-case DIFF is the signature element. Each case shows: its status (completed-pass · completed-fail · refused · errored), the assertion (kind + expected), the model's actual `response_text`, and on a fail the `reason`/detail — the expected-vs-actual diff, never a bare red dot (A6). A case with no answer (refused/errored) shows its status + reason and NO fabricated "actual" (E3).
- M5 Keyboard-navigable + WCAG AA are ACCEPTANCE criteria, not polish (experience.md). Clickable run/set rows carry `tabIndex=0` + `aria-label` + Enter/Space activation + `focus-visible:ring-*` (the InvoicesListPage idiom); styling is token-only (no raw hex/px, dark mode via `.dark`); an axe pass with ZERO serious/critical findings across the section routes (the `@axe-core/playwright` harness on a `next build`), plus the authed capture harness (`e2e-review/capture.spec.ts` + `fixtures.ts`).
- M6 Fail closed in the UI (experience.md). The nav entry uses the fail-OPEN `minRole` denylist (UX only; the gateway enforces RBAC), but the SECTION renders nothing actionable for a still-loading or absent identity — never a component that renders null while identity resolves (R:NULL_RENDER_LEAK). Every error state names the failed subsystem + the next step from `BffError.problem` — never blames the user's data for our outage — via the `states.tsx` `Loading`/`Empty`/`ErrorState` primitives.
</must>
<reject>
- R:LOGIC_FORK the `/admin/evals` surface re-implements scoring, the verdict, or tenant-scoping instead of reusing the frozen eval modules -> "reuse the stores + verdict core; never fork the logic"
- R:CROSS_TENANT a session-authed read or write serves or mutates another tenant's set/run/baseline -> "R:CROSS_TENANT"
- R:RAW_KEY_IN_CONSOLE the console surface (server or browser) accepts, forwards, or persists a raw API key -> "the session console must NEVER touch a raw key; launch stays on the /v1 API-key path"
- R:VERDICT_NOT_PRIMARY the run page leads with a table or a row-count instead of the verdict -> "verdict-first: the verdict is the page's primary object"
- R:NULL_RENDER_LEAK an identity-gated surface renders an empty component for a still-loading identity instead of being absent -> "fail closed: absent for a still-loading identity, never a null render"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1 · the request does not say who may read or author; taking "any authenticated tenant member (the session Identity's tenant) reads the console AND authors sets/cases/pins — R7 has no separate eval-admin role, matching the tenant-scoped-any-key /v1 surface; the nav entry sits in the tenant console (not the superadmin Platform allowlist)" -> if wrong, a role gate is missing or the surface is mis-placed. · probe: a member-role session reads the console and creates a set; a tenant-B session hitting tenant-A ids gets 404.
- A2 [which] covers: S1 · the request does not say which rows appear; taking "the session tenant's sets; a set's runs newest-first; a run's per-case results in the API's creation order; a run's verdict/score over the run's own launch snapshot (baseline-and-verdict A2) — the console renders exactly what the tenant-scoped API returns" -> if wrong, the console shows stale or cross-tenant rows. · probe: the sets list contains only the session tenant's sets.
- A3 [when] covers: S1 · the request does not say what a not-yet-finished run shows; taking "a run whose cases are not all terminal renders an explicit PENDING/partial state — NOT a pass/fail verdict (a verdict on an incomplete run would be a false green)" -> if wrong, a mid-flight run reads as a verdict. · probe: a pending run's page shows a pending state, never a pass banner.
- A4 [absent] covers: S1 · the request does not say what missing data renders; taking "no sets → an Empty state with a create affordance; a run with no pinned baseline → the verdict view shows the explicit `no_baseline` state (never a pass-styled banner, mirroring the API M4); a case with no response → status + reason, no fabricated actual" -> if wrong, an empty or unpinned surface reads green. · probe: a no_baseline run renders the explicit no_baseline state, not a pass.
- A5 [order] covers: S1 · [order] n/a · ordering is the API's guarantee (runs newest-first, cases in creation order per baseline-and-verdict/eval-run A5); the console renders in received order and introduces no new ordering or tie-break.
- A6 [experience] covers: S1 · the request does not say who reads a fail and what makes it hard; taking "the operator hunting a regression reads the verdict banner then drills into the failing cases — so a FAIL names WHICH cases regressed with their expected-vs-actual diff + reason, actionable at a glance; a gateway error names the subsystem + a retry, never 'your data is bad'" -> if wrong, a failing gate is an unactionable red dot. · probe: a fail verdict lists the failing cases with their diffs; a BffError renders an ErrorState naming the subsystem.

## PLAN
contract:
```
BACKEND — session-authed control-plane surface (reuses the frozen stores + verdict core):
  src/gateway/evals/api/admin_router.py   (Depends(get_current_identity) -> tenant_id)
  GET  /admin/evals/sets                         -> { items:[{id,name,case_count,created_at}], ... }
  GET  /admin/evals/sets/{set_id}                -> { id,name, cases:[...], runs:[...], baseline_run_id }
  GET  /admin/evals/runs/{run_id}/verdict        -> { score, baseline|null, verdict }   (reuse score_run/decide)
  GET  /admin/evals/runs/{run_id}/cases          -> { data:[{eval_case_id,status,response_text?,reason?}] }
  POST /admin/evals/sets            {name}        -> 201  ·  POST /admin/evals/sets/{id}/cases {request_body,assertion}
  PUT  /admin/evals/sets/{id}/baseline {run_id}   -> 200
  (NO launch endpoint here; uniform 404 for absent/cross-tenant; wired in main.py)

FRONTEND — the evals console section (Next 16 App Router, TanStack Query + bffGet, tokens-only):
  app/(app)/app/evals/page.tsx                              -> <EvalsListPage/>
  app/(app)/app/evals/[setId]/page.tsx                     -> <SetDetailPage/>
  app/(app)/app/evals/[setId]/runs/[runId]/page.tsx        -> <RunVerdictPage/>   (verdict-first, hero)
  components/evals/{EvalsListPage,SetDetailPage,RunVerdictPage,VerdictBanner,CaseDiffList,CaseDiffRow,EvalStatusBadge}.tsx
  nav: components/ui/app-shell.tsx NAV_GROUPS (Insights group, /app/evals, all roles)
  bff types: hand-written interfaces at the bffGet<T> call sites; Zod only for the create-set/add-case dialogs
```
scope:
- src/gateway/evals/api/admin_router.py + src/gateway/main.py (include the router) + error_catalog reuse (EVAL_SET_NOT_FOUND / EVAL_RUN_NOT_FOUND)
- apps/dashboard/app/(app)/app/evals/** + apps/dashboard/components/evals/** + components/ui/app-shell.tsx (nav entry)
- apps/dashboard/tests/evals-*.test.tsx (vitest + Testing Library + MSW) + e2e-a11y/a11y.spec.ts route + e2e-review/{capture.spec.ts routes, fixtures.ts bodies}
- REUSE the frozen backend modules (no logic fork); REUSE lib/bff-client, components/ui/{states,page-header,badge,table,dialog}
considered-and-rejected: calling /v1/evals from the BFF with the session — /v1 is API-key-only and rejects a session JWT; and console-launch — needs a raw key the session must not hold (deferred, user-approved read-first scope 2026-08-13).

## EDGES
- E1 a no_baseline run → the verdict view shows the explicit no_baseline state, never a pass-styled banner (M3/A4, mirrors API M4).
- E2 a pending run (cases not all terminal) → a pending/partial state, not a verdict (A3).
- E3 a refused/errored case → the diff shows status + reason and NO fabricated actual response (M4).
- E4 an empty sets list → an Empty state with a create affordance (A4).
- E5 a cross-tenant or absent set/run via /admin/evals → uniform 404; the UI shows not-found, never another tenant's data (R:CROSS_TENANT).
- E6 a gateway BffError → ErrorState naming the subsystem + a retry, never blaming the user's data (M6/A6).
- E7 keyboard: a run/set row is reachable AND activatable by keyboard (tabIndex + Enter/Space + focus-visible ring) (M5).
- E8 axe: zero serious/critical across the sets/detail/verdict routes (M5).

## CHECKS
- test_admin_evals_session_scoped_reads · covers: M1, A1, A2, R:CROSS_TENANT, E5 · a tenant-A session reads A's sets/runs/verdict; tenant-B ids → 404, no cross-tenant rows.
- test_admin_evals_verdict_matches_v1_reuse · covers: M1, R:LOGIC_FORK · the /admin verdict for a run equals the /v1 verdict for the same run (same score/verdict object) — proves the stores + verdict core are reused, not forked.
- test_admin_evals_authoring_session_writes · covers: M2, A1 · POST set + POST case + PUT baseline via a session Identity create/pin tenant-scoped rows.
- test_admin_evals_has_no_launch_and_no_raw_key · covers: M2, R:RAW_KEY_IN_CONSOLE · the /admin/evals route table has NO launch route and no handler reads a raw key.
- test_run_page_leads_with_verdict_banner · covers: M3, R:VERDICT_NOT_PRIMARY · the run view renders the VerdictBanner before any results table (DOM order / landmark).
- test_case_diff_shows_expected_actual_reason · covers: M4, A6, E3 · a failing case renders assertion(kind+expected) + actual response + reason; a refused/errored case renders status+reason with no fabricated actual.
- test_no_baseline_run_renders_explicit_state · covers: M3, A4, E1 · a no_baseline run shows the explicit no_baseline state, not a pass banner.
- test_pending_run_is_not_a_verdict · covers: A3, E2 · a pending run shows a pending state, never pass/fail.
- test_section_fail_closed_for_loading_identity · covers: M6, R:NULL_RENDER_LEAK · a still-loading identity → the gated surface is absent, not an empty render.
- test_error_state_names_subsystem_not_user_data · covers: M6, E6 · a BffError renders an ErrorState naming the subsystem + a retry, never blaming the user's data.
- test_run_rows_keyboard_navigable · covers: M5, E7 · run rows expose tabIndex + aria-label + Enter/Space activation + focus-visible ring.
- test_evals_routes_axe_clean · covers: M5, E8 · axe reports zero serious/critical across the sets/detail/verdict routes.
- test_empty_sets_shows_create_affordance · covers: E4 · an empty sets list renders an Empty state with a create affordance.
red-first: every check MUST fail first.

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
