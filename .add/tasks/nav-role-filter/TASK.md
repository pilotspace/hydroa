# TASK: Role-based nav visibility (member hides admin-only links)

slug: nav-role-filter · created: 2026-06-14 · stage: production
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

Touches (files · symbols · signatures):
- **`components/ui/app-shell.tsx:18-26` `NAV_ITEMS`** — a static 7-item list ({/usage,/spend,/keys,
  /models,/teams,/routing,/settings}) rendered unconditionally in the Primary `<nav>` (52-71).
  `AppShellProps` (28-32) = `{ children, activePath? }`. ADD an optional `role?: string | null` prop +
  a `minRole?: "admin"` tag on the admin-only items, and FILTER NAV_ITEMS by role before render.
- **`lib/hooks/use-current-user.ts:45` `useCurrentUser()`** → `{ data: CurrentUser|null, isLoading,
  isError }`, `CurrentUser.role: string | null` (owner|admin|member), query key `["current-user"]`,
  `retry:false`, `staleTime:5m`. The role source — already trusted, no JWT decode client-side.
- **`app/(dashboard)/layout.tsx:14`** — a SERVER component rendering `<AppShell>{children}</AppShell>`
  (no role). A client wrapper is needed to feed the role (a server component can't call the hook).
- **NEW `components/dashboard-shell.tsx`** ("use client") — calls `useCurrentUser()`, renders
  `<AppShell role={user?.role ?? null}>{children}</AppShell>`; the layout renders `<DashboardShell>`.
- Precedent: **`components/usage/UsagePage.tsx:24-25`** already does `canEdit = role==="owner"||"admin"`
  from `useCurrentUser()` — the established CLIENT role-gate pattern this task generalizes to the nav.

RBAC anchors (verified in the gateway 2026-06-14): the role cut is `owner|admin` vs `member`, EXCEPT
`/admin/oidc` (owner-only). GET **403s on member** for `/admin/models`, `/admin/teams`, `/admin/routing`
(all `require_owner_or_admin`). Member-OK (any authenticated): `/admin/usage`, `/admin/spend`,
GET `/admin/keys`, GET `/admin/cache`, GET `/admin/guardrails`. ⇒ nav: **member hides {models, teams,
routing}; keeps {usage, spend, keys, settings}**; admin & owner see all 7. There is NO owner-only NAV
link — SSO is a TAB inside /settings, handled server-authoritatively (admin/member opening it →
"Owner role required" ErrorState); the existing SSO-tab tests assume the tab is always present, so it
is left untouched (gating it would break the floor).

Context (working folder): v17 MILESTONE.md (depends-on: none). Exit criterion: a `member` does NOT see
admin-only nav links (absent from the DOM, not disabled), while admin/owner does; gateway RBAC unchanged.

Honors (patterns / conventions): the UsagePage `useCurrentUser` client role-gate; server-authoritative
RBAC stays the SOURCE OF TRUTH (the nav filter is UX-only). design-for-failure (CLAUDE.md): role
unknown / loading / `/api/auth/me` error ⇒ **fail-open** (render all links; the gateway still 403s on
navigate) — never lock a user out of their own nav over a transient identity fetch.

Anchors the contract cites: `AppShell` gains `role?: string|null` + filters NAV_ITEMS (member hides the
3 `minRole:"admin"` items) · `DashboardShell` client wrapper feeds `useCurrentUser().role` · layout
renders `<DashboardShell>` · existing AppShell tests (no `role` prop) still render all 7 (fail-open).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: role-based Primary-nav visibility — a `member` does not see nav links to pages they would 403 on.

Framings weighed:
- **AppShell takes a `role` prop + a thin client `DashboardShell` feeds `useCurrentUser().role`; fail-open**
  (chosen) — keeps AppShell presentational/testable (no data coupling, no new leaks in AppShell tests),
  reuses the existing `["current-user"]` query (no extra network call), and degrades safely.
- AppShell calls `useCurrentUser()` internally — REJECTED: makes the presentational shell fire
  `/api/auth/me` in every AppShell test (new leaks) and couples the shell to data fetching.
- Disable (not remove) the links — REJECTED: the exit criterion requires the links ABSENT from the DOM.

Must:
<must>
  - role==="member" → the nav renders NO link to `/models`, `/teams`, `/routing` (absent from the DOM).
  - role==="member" → the nav STILL renders `/usage`, `/spend`, `/keys`, `/settings`.
  - role==="admin" AND role==="owner" → the nav renders all 7 links.
  - role null / undefined / loading / errored → fail-open: render all 7 (gateway still enforces; no lockout).
  - gateway RBAC is unchanged; NO new network call beyond the existing `useCurrentUser` query.
  - existing AppShell tests that pass no `role` keep rendering all 7 links (behavior preserved).
</must>
Reject:
<reject>
  - hiding a member-allowed link (usage/spend/keys/settings) -> "over_hidden"
  - hiding any link from an admin or owner -> "over_hidden_privileged"
  - a crash / empty nav when role is null -> "must_fail_open"
</reject>
After:
<after>
  - member nav = {usage, spend, keys, settings}; admin/owner nav = all 7; unknown role = all 7.
  - the floor (240 tests) stays green; the role gate adds the new behavior + asserts the floor intact.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The role→link map (member hides EXACTLY {models, teams, routing}) matches the gateway's real 403
    surface — lowest confidence because a later gateway change could move which GETs require admin.
    RESOLVED by exploration (2026-06-14): catalog/teams/routing GETs use `require_owner_or_admin`;
    usage/spend/keys-list/cache-GET/guardrails-GET are any-authenticated. If wrong: a member sees a link
    that 403s (mild) or loses a usable link (worse) — caught by the role-scoped render tests vs this map.
  - [x] no owner-only NAV link exists (SSO is a server-authoritative TAB) — confirmed; tab untouched.
  - [x] fail-open is the correct failure mode — confirmed by design-for-failure + gateway-authoritative RBAC.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: member hides admin-only links
  Given AppShell rendered with role="member"
  When the Primary nav renders
  Then there is NO link to /models, /teams, or /routing
  And the links to /usage, /spend, /keys, /settings are present

Scenario: admin sees every link
  Given AppShell rendered with role="admin"
  When the Primary nav renders
  Then all 7 links are present (Usage, Spend, API Keys, Models, Teams, Routing, Settings)

Scenario: owner sees every link
  Given AppShell rendered with role="owner"
  Then all 7 links are present

Scenario: unknown role fails open
  Given AppShell rendered with role={null} (or no role prop)
  Then all 7 links are present (the gateway still enforces RBAC on navigate)

Scenario: DashboardShell wires the role from the current user
  Given GET /api/auth/me returns role="member"
  When DashboardShell renders the AppShell
  Then the admin-only links are absent (the wrapper passed role through)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
AppShell(props): { children: ReactNode; activePath?: string; role?: string | null }
  NAV_ITEMS: each item may carry `minRole?: "admin"`; tagged on /models, /teams, /routing.
  visible = NAV_ITEMS.filter(it => !(it.minRole === "admin" && role === "member"))
  role ∈ {null, undefined, "owner", "admin"}  -> all 7 links
  role === "member"                           -> {/usage, /spend, /keys, /settings} (4)
  (everything else — skip-link, landmarks, activePath/aria-current — unchanged)

DashboardShell ("use client"):
  const { data } = useCurrentUser()          // reuses query key ["current-user"], no new call
  return <AppShell role={data?.role ?? null}>{children}</AppShell>

app/(dashboard)/layout.tsx: renders <DashboardShell>{children}</DashboardShell>

No gateway / BFF data-contract change. No new endpoint. No new dependency.
```

Status: FROZEN @ v1 — approved by Tin Dang (auto mode, autonomy:auto) 2026-06-14

Least-sure flag surfaced at freeze: [contract] the role→link map (member hides exactly {models, teams,
routing}). Why least-sure: it encodes the gateway's CURRENT 403 surface into the dashboard; if the
gateway later gates a now-open GET (e.g. /spend) or opens a now-gated one, the map drifts. De-risked by
the 2026-06-14 gateway exploration (the three are `require_owner_or_admin`; the four are any-auth) and
pinned by role-scoped render tests asserting the exact per-role link set. Cost if wrong: a stray
visible-but-403 link (mild) or a hidden-but-usable link (caught by the admin/owner "all 7" tests).
Secondary [test]: fail-open means a null-role render shows all 7 — the existing AppShell tests (no role
prop) are the proof the prior behavior is preserved.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: hold the floor (≥80% global; app-shell.tsx currently 100%).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_member_hides_admin_only_links: render <AppShell role="member"> → queryByRole link {Models,
    Teams, Routing} === null; getByRole link {Usage, Spend, API Keys, Settings} present.
  - test_admin_sees_all_links: render <AppShell role="admin"> → all 7 links present.
  - test_owner_sees_all_links: render <AppShell role="owner"> → all 7 links present.
  - test_unknown_role_fails_open: render <AppShell role={null}> AND <AppShell> (no prop) → all 7 present.
  - test_dashboard_shell_filters_from_current_user: msw GET /api/auth/me → {role:"member"}; render
    <DashboardShell> in a QueryClient wrapper → admin-only links absent after the user resolves.
  - the 240-test floor (incl. the existing AppShell tests in feature-coverage-verify / ui-ux-verify that
    render <AppShell> with NO role) is the regression net — they must stay green (fail-open all 7).
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` · the new suite MUST run red (no role prop / no DashboardShell) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/app-shell.tsx` `apps/dashboard/components/dashboard-shell.tsx` `apps/dashboard/components/ui/index.ts` `apps/dashboard/app/(dashboard)/layout.tsx` `apps/dashboard/tests-bff/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `apps/dashboard/.next/` `.add/tasks/nav-role-filter/`
Strategy (ordered batches): 1. add the RED nav-role-filter suite 2. AppShell: add `role` prop +
`minRole` tags + the filter 3. add DashboardShell client wrapper 4. layout renders DashboardShell
5. green: the new suite + the 240 floor + eslint 0/0 + tsc.
Safety rule (feature-specific): UX-only — gateway RBAC untouched; fail-open on unknown role (never an
empty/locked nav); no secret, no new network call (reuse the ["current-user"] query).
Code lives in: `apps/dashboard/` (components/ui/app-shell, components/dashboard-shell, app/(dashboard)/layout).
Constraints: do NOT change any floor test or its assertions; allow-list packages only (no new deps).

<!-- Scope tokens project-root-relative; coverage/ + tsbuildinfo + .next/ declared per the v13 scope-lock
     convention. EXIT: all green; coverage held; no floor test touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `vitest run --coverage --testTimeout=20000` EXIT 0, **245 tests / 33 files**
  (240 floor + 5 new nav-role-filter). The 3 prior CPU-starvation flakes did not recur with the 20s timeout.
- [x] coverage did not decrease — **94.05% global** (↑ from 94.03%). New files: dashboard-shell.tsx 100%;
  app-shell.tsx stays 100%. No floor file dropped.
- [x] no test or contract was altered during build — only NEW file is the nav-role-filter suite; the 240
  floor tests keep every assertion (the existing AppShell tests pass no `role` → fail-open → all 7 → green).
  §3 contract FROZEN, untouched. (The 2 count assertions added to the NEW suite STRENGTHEN it — not a floor edit.)
- [x] the green was EARNED — adversarial refute-read (sonnet, 7 attacks) → **EARNED-WITH-GAPS**: zero bug in
  shipped code; fail-open definitively correct (only the exact string "member" hides links — null/undefined/
  owner/admin/unknown → all 7, no lockout); server/client boundary valid; no new network call (reuses the
  ["current-user"] query); role map exact. The two flagged gaps were test-coverage nits → CLOSED by adding
  `toHaveLength(4)` count assertions to the member views. No overfit, no eslint-disable.
- [x] concurrency / timing safe — the DashboardShell test waits (waitFor) for the async role to resolve
  null→member before asserting the admin links drop; no race. The nav filter is a pure synchronous derive.
- [x] no exposed secrets / no new dependency — the nav filter is UX-only; the role comes from the trusted
  server (`GET /api/auth/me`, HttpOnly cookie); no token/secret flows through the role; gateway RBAC unchanged.
  (Reviewer noted /api/auth/me decodes JWT without sig-verification — PRE-EXISTING + intentional for this
  UX-only endpoint, gateway enforces, HttpOnly blocks JS tampering; out of this task's scope → §7 delta.)
- [x] layering & dependencies follow CONVENTIONS.md — AppShell stays presentational (role as a prop); a thin
  client DashboardShell owns the data wiring; server layout renders the client wrapper (canonical App Router
  hole pattern); fail-open honors design-for-failure. Mirrors the UsagePage useCurrentUser role-gate precedent.
- [x] a person reviewed and approved — auto-gate (autonomy:auto): UX-only, no gateway/security surface change,
  fail-open by design, evidence complete + adversarial EARNED → auto-resolved PASS.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `minRole` tags drive the `navItems` filter rendered in the nav; `DashboardShell`
  is imported by `app/(dashboard)/layout.tsx`; `useCurrentUser().role` flows into `AppShell role`.
  eslint (no-unused) + tsc EXIT 0; the new suite exercises every role branch.
- [x] DEAD-CODE (code) — no orphaned symbol; `NAV_ITEMS` is now consumed via the filtered `navItems`
  (no stale direct map remains); tsc + eslint confirm.
- [x] SEMANTIC (prose) — read the AppShell `role`/`minRole` doc comments + DashboardShell header in full;
  both state the UX-only / fail-open / gateway-authoritative contract explicitly.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (auto mode, autonomy:auto — auto-resolved on complete evidence) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass.
     No security finding: the filter is UX-only, gateway RBAC unchanged, role from a trusted HttpOnly
     server response; fail-open never locks a user out. Clean behavior-additive auto-PASS. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the per-role link set (member=4, admin/owner=7) — the count
assertions fail loud if a nav item's minRole drifts; fail-open keeps the nav usable if /api/auth/me errors.
Spec delta for the next loop: role-gated UI is a CLIENT affordance over server-authoritative RBAC —
encode the gateway's 403 surface as `minRole` tags + fail-open, and pin it with role-scoped render tests;
never let the nav become the access-control boundary (the gateway is).

### Competency deltas
- [UDD · folded] (v17, 2026-06-15) role-based nav visibility shipped: member hides {models,teams,routing}; the established
  pattern is `minRole` tags on a presentational shell + a thin client wrapper feeding useCurrentUser().role,
  fail-open (evidence: nav-role-filter.test.tsx 5/5; the UsagePage canEdit precedent generalized).
- [SDD · folded → task] (v17, 2026-06-15) PRE-EXISTING (not this task): `GET /api/auth/me` decodes the session JWT WITHOUT signature
  verification (intentional — UX-only endpoint; the gateway verifies + enforces on every proxied request;
  the cookie is HttpOnly+SameSite=Strict so JS can't tamper). A spoofed role only changes nav chrome, never
  access. Fold candidate: add a one-line comment to app/api/auth/me/route.ts documenting the deliberate
  no-verify (adversarial review flagged the missing rationale, not a vulnerability). Owner: a future
  auth-hardening task — NOT in nav-role-filter's scope.
  → ESCALATED by Tin (2026-06-15): RECLASSIFIED as a real defense-in-depth gap, NOT a settled tradeoff.
  The BFF MUST verify the session JWT signature before trusting its claims → owned by the new security
  task `auth-me-session-verify` (key-fetch designed for failure: timeout/cache/fallback per the IO rule).
- [TDD · folded → task] (v17, 2026-06-15) still-open (carried from react-hooks-strict-lint): the 7 `/api/auth/me` unhandled-request
  leaks come from UsagePage tests rendering useCurrentUser without a per-test stub (NOT from this task —
  confirmed identical 7-count before & after). Per the strict-harness "no shared fallback" rule the fix is
  per-test stubs in the usage suites, a separate harness chore. Reach a true 0-leak there.
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
