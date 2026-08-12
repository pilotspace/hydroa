# MILESTONE: Platform console flat/borderless visual redesign

goal: A superadmin using the platform admin console (tenant directory + tenant-detail tabs) experiences a flat, borderless, SaaS-professional visual language, grounded in real UI/UX research into how superadmins actually scan and act on this surface
rationale: new-major (precedent: v13 design-system-foundation → v23 enterprise-UI → v37
  ui-fidelity → v50 → v54 were each their own visual-language new-major). Tin requested a UI/UX
  polish initiative for the platform admin console (2026-07-05), grounded in real research rather
  than an ad-hoc restyle, then explicitly expanded scope from visual-only to "features and design
  concept ... effective for user" (2026-07-05). Relationship: extends `platform-admin-console`
  (the console this milestone reskins + extends) and follows `admin-console-ui`'s persona-evidence
  UDD precedent; `add.py search` confirms no existing milestone's goal already covers this (only
  self-matches + unrelated tangential keyword hits).
stage: production · status: active · created: 2026-07-05T07:03:08+00:00
release: 0.8.0

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - Visual hierarchy + design tokens: flat/borderless/sharp-cornered treatment for the tenant
    directory table + every tenant-detail tab (Config/Budget/Keys/Members/Plan), consuming the
    already-formalized tokens (`flat-tag`/`flat-control`/`flat-card` radii, `success-text`/
    `destructive-text`/`warning-foreground` AA-safe text, `selected-border`) and the shipped
    `Card` `flat` variant. Layered alongside Aurora — every other dashboard page keeps its
    existing rounded/shadowed treatment untouched.
  - Interaction pattern (Tier 1, frontend-only, no new backend contract): a Tenant Overview Strip
    (plan+seat-cap, seats-used, active-keys, budget — 4 independently-loading queries reusing the
    tabs' own existing React Query cache keys) and a global Command palette (⌘K, navigate-only,
    reuses the existing tenant-search endpoint, real ARIA combobox/listbox semantics — not the
    concept mock's bare hover-divs).
  - User journey (Tier 2, one new backend route, low risk — reuses `AuditRepository
    .list_for_tenant_paged`/`count_for_tenant` + the existing `require_superadmin`+
    `authorize_tenant_scope` pattern): a per-tenant Activity tab.
  - Accessibility floor (WCAG 2.2 AA): on every element this milestone touches or adds —
    contrast, keyboard operability, and real focus management for the Command palette
    specifically (the concept mock's own rows lack ARIA selected-state annotation today).
Out:
  - The directory kind/plan filter (Tier 3) — reopens the FROZEN `platform_tenants_router.py`
    for a cohort-browsing job none of this milestone's actual scenarios need (ux-researcher
    verdict, 2026-07-06); deferred pending real usage evidence.
  - Bulk tenant actions, saved views/filters, a platform-wide (cross-tenant) activity feed, key
    "last used at" — all explicitly NOT recommended by this milestone's own research, named with
    reasons rather than silently dropped.
  - The 10 still-deferred accessibility findings from the persona-dive sweep that predate this
    milestone and aren't specific to its new/touched surfaces (route-change focus management,
    both banners' live-regions, confirm-dialog button order, etc.) — tracked in DESIGN.md, own
    follow-up task, not this milestone's scope.
  - Dark mode (standing "Light-only" decision, predates this milestone) and any change to
    Button/Input/Table beyond what's already shipped (no concretely-identified defect motivates
    touching them).

> UI/UX in scope? Name it precisely, not "make it nice" — information architecture ·
> interaction pattern · visual hierarchy · design tokens · component states ·
> accessibility floor (WCAG AA) · responsive breakpoints · user journey
> (`.add/personas-teacher/design/`). Precise ≠ distinctive: skip generic AI-design
> defaults (cream+serif+terracotta · near-black+neon · broadsheet-hairline) and name ONE
> deliberate signature element instead (Claude Code's `frontend-design` skill). A UI
> feature also triggers DESIGN.md via the `add` skill's design.md.

## Shared decisions & glossary deltas   (living — every task must honor these)
- Visual tokens for this surface are FORMALIZED (2026-07-06): see `.add/design/DESIGN.md`'s
  `platform-console-flat-redesign` row + `.add/design/tokens.json` (new: `emerald.700`,
  `radius.2xs`/`.xs`, `font.family.mono` primitives; `success-text`, `flat-tag`/`flat-control`/
  `flat-card`, `font.family.mono` semantic aliases). Layered alongside Aurora — every existing
  `control`/`card` radius and the shipped Classic-Blue accent stay untouched for every other page.
  Any task under this milestone building the real screens must reuse these names, not invent new
  ones or silently rename Aurora's existing tokens.
- Naming collision RESOLVED (2026-07-06): `Card`'s old `variant="flat"` (more shadow/radius) is now
  `variant="soft"` (one call site + tests updated, red/green-verified); a genuine `variant="flat"`
  (no border/shadow, sharp radius) exists now — component-primitive only, no screen consumes it yet.
  Any task under this milestone should use `flat`/`soft` as defined in `card.tsx`, not reintroduce a
  third meaning.
- Component-primitive layer is WIRED (2026-07-06): `globals.css` realizes `success-text`/
  `flat-tag`/`flat-control`/`flat-card`; `Badge`'s `warning`/`success` variants use the AA-safe text
  tokens; `PlatformTenantDirectory`'s "Platform" tag category-fixed (`warning`→`outline`). Full
  dashboard suite 1008/1008. This is NOT the screen build — no page beyond the one existing `soft`
  call site consumes the flat recipe yet.
- Persona deep-dive complete (2026-07-06): `ui-designer`/`ux-researcher`/`accessibility-auditor`
  (newly seeded — see `.add/personas/accessibility-auditor.md`) each independently researched this
  milestone's 6 open questions; see DESIGN.md's row for the full verdict set + the published report.
  **Recommendations are NOT yet accepted** — still gated on Tin's own review, same as every other
  open question here. Separately, and independent of this milestone's scope: 13 real accessibility
  findings surfaced in code ALREADY SHIPPING today (not the concept mock) — tracked in DESIGN.md.
  4 fixed (2026-07-06: `Badge` `destructive`, `ui/states.tsx`'s `Success`+`ErrorState`, `ui/
  stat-card.tsx`'s delta tones — all red/green-verified, full dashboard suite 1012/1012). The other
  10 (focus management, live-regions, dialog button order, heading levels, field-error aria wiring,
  and 3 smaller/process findings) remain unactioned — bigger blast-radius, left for a dedicated
  follow-up task.
- **Milestone CONFIRMED (2026-07-06, Tin: "both milestone-confirm platform-console-flat-redesign"
  after discussing + deciding the 2 flagged caveats).** Scope/Tasks/Exit-criteria drafted per
  `scope.md`'s rubric (goal unchanged — already one outcome sentence; rationale + In/Out +
  Shared/risky-contracts + 4 breadth-first tasks + 4 exit criteria all filled, `add.py search`
  confirmed no duplicate goal). `add.py milestone-confirm` run — `new-task` is now open for this
  milestone. `add.py check` unchanged after (86 passed/87 failed). Still NOT itself a decision on
  the remaining un-actioned parts: Q1's literalness elsewhere, Q3's overall "quiet it" call as a
  top-level question, the other 4 open questions' finer details, and the 10 deferred accessibility
  findings all still stand exactly as recorded above — `milestone-confirm` accepted the
  RECOMMENDATIONS as the scope's working direction, it did not re-litigate each one line by line.

## Shared / risky contracts (freeze these first)
- New superadmin-scoped audit-read route (reuses `AuditRepository.list_for_tenant_paged`/
  `count_for_tenant`, the `require_superadmin`+`authorize_tenant_scope` pattern already used by
  the other tenant-detail routes — traced by ux-researcher, 2026-07-06, low risk) -> owning task
  `tenant-activity-tab`
- Overview Strip's React-Query cache-key reuse: MUST match the tabs' own existing keys exactly
  (`["platform-tenant-plan", tenantId]` etc.) or risk double-fetching + cache incoherence
  (ux-researcher flagged this as the one place a UX call and an engineering contract detail are
  inseparable) -> owning task `tenant-overview-strip`
- Shared-file coordination: `console-flat-visual-pass`, `tenant-overview-strip`, and
  `command-palette` may all additively touch `PlatformTenantDetail.tsx` / `app-shell.tsx` — no
  frozen contract between them, but whichever build agents run first should report their exact
  diff to those files so a later one reconciles rather than blind-merges (same pattern already
  used for the `plan-admin-ui`/`impersonation-ui` collision earlier this program).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] console-flat-visual-pass   depends-on: none   — apply the flat/borderless/sharp treatment
      (Card `flat` variant, `flat-tag`/`flat-control` radii, `selected-border`, the quieted
      safety-banner divider) to the tenant directory + all 5 tenant-detail tabs
- [x] tenant-overview-strip      depends-on: none   — build the Tenant Overview Strip (4
      independent queries, reusing the tabs' existing cache keys)
- [x] command-palette            depends-on: none   — build the global ⌘K command palette
      (navigate-only, reuses the existing tenant-search endpoint, real ARIA listbox semantics)
- [x] tenant-activity-tab        depends-on: none   — new superadmin-scoped audit-read route +
      a 5th "Activity" tenant-detail tab rendering it (shipped as the milestone's 6th tab —
      Overview Strip added a tab slot ahead of it; see Ship-by-domain above)

## Exit criteria (observable; map each to the task that delivers it)
- [x] A superadmin viewing the tenant directory or any tenant-detail tab sees the flat/borderless/
      sharp visual treatment; every other dashboard page's existing rounded/shadowed Aurora
      treatment is unchanged        (← console-flat-visual-pass)
      (verify: console-flat-visual-pass TASK.md §6, gate=PASS, full dashboard suite green)
- [x] A superadmin opening a tenant's detail page sees an Overview Strip showing plan/seats/keys/
      budget at a glance, each field loading and failing independently of the others
      (← tenant-overview-strip)
      (verify: tenant-overview-strip TASK.md §6 gate=PASS + overview-strip-plan-display-name
      TASK.md §6 gate=PASS follow-up fix, both green)
- [x] A superadmin can open a ⌘K command palette from anywhere in the dashboard, search by tenant
      name, and navigate directly to that tenant        (← command-palette)
      (verify: command-palette TASK.md §6, gate=PASS, 1078/1078 full dashboard suite)
- [x] A superadmin viewing a tenant's detail page can open an "Activity" tab and see that
      tenant's real audit history (actor/action/target/when), authorized the same way as the
      other tenant-detail routes        (← tenant-activity-tab)
      (verify: tenant-activity-tab TASK.md §6, gate=PASS, 27/27 backend + 1092/1092 dashboard suite)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched — no `add.py`/`state.json` schema/template change; this milestone is a
  pure product-feature/visual-redesign body of work
- skill   : untouched — no `SKILL.md`/`phases/*`/guide change
- book    : untouched — no `.add/docs/*` change
- dashboard (frontend) : SHIPPED — flat/borderless design tokens + `Card` `flat`/`soft` variants
  consumed across the tenant directory + all 5 (now 6) tenant-detail tabs
  (`console-flat-visual-pass`); a new `PlatformTenantOverviewStrip` (plan/seats/keys/budget, 4
  independent queries) mounted above the tabs (`tenant-overview-strip` +
  `overview-strip-plan-display-name`'s display-name fix); a new global ⌘K
  `PlatformCommandPalette` mounted via an additive `AppShell`/`DashboardShell` prop
  (`command-palette`); a new `PlatformActivityTab` as a 6th tenant-detail tab, consuming a new
  backend route (`tenant-activity-tab`)
- gateway (backend) : SHIPPED — exactly one new file this entire milestone,
  `platform_audit_router.py` (`GET /admin/platform/tenants/{tenant_id}/audit`), reusing
  `AuditRepository`/`authz.py` verbatim, registered via one additive `main.py` line
  (`tenant-activity-tab` only — every other task in this milestone was frontend-only, confirmed
  via `git status`/`git diff` at each task's own verify gate)

### Cross-task evidence   (one row per task)
- console-flat-visual-pass : gate=PASS · tests=full dashboard suite green at the time (1008/1008
  per its own TASK.md) · residue=none
- tenant-overview-strip : gate=PASS · tests=green (full dashboard suite) · residue=one spec delta
  (Plan tile showing the raw `plan.name` slug instead of `display_name`) — resolved same-day by
  the `overview-strip-plan-display-name` fast-lane follow-up below, not left open
- overview-strip-plan-display-name : gate=PASS · tests=9 assertions updated + green · residue=none
  (fast-lane, resolves the spec delta above)
- command-palette : gate=PASS · tests=1078/1078 full dashboard suite (18 dedicated + 3 mount-gate
  tests new) · residue=none blocking; one build-agent self-reported near-miss during its own
  adversarial mutation testing (a misleading `diff` summary on a still-mutated file) was caught by
  the agent, then independently re-confirmed resolved by the orchestrator reading the file
  directly as the first verification action
- tenant-activity-tab : gate=PASS · tests=27/27 new backend (isolated re-run) + 1092/1092 full
  dashboard suite (14 new) · residue=none blocking; two judgment calls (real pagination vs. bounded
  fetch; self-audit-of-reads) resolved under AUTO MODE after Tin did not respond to a direct ask —
  both flagged in TASK.md, both cheaply reversible; one pre-existing, unrelated full-suite flake
  independently reconfirmed via isolated re-run + git history (predates this session)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] "flat/borderless/sharp visual treatment on directory + tabs, other pages unchanged" —
  satisfied by `console-flat-visual-pass`'s Ship-by-domain change (dashboard row above), gate=PASS
- [x] "Overview Strip showing plan/seats/keys/budget, each independent" — satisfied by
  `tenant-overview-strip` + `overview-strip-plan-display-name`'s Ship-by-domain change, gate=PASS
- [x] "⌘K command palette, search by tenant name, navigate directly" — satisfied by
  `command-palette`'s Ship-by-domain change, gate=PASS
- [x] "Activity tab showing real audit history, authorized the same way as other tenant-detail
  routes" — satisfied by `tenant-activity-tab`'s Ship-by-domain change (both the new
  `platform_audit_router.py` route and the new `PlatformActivityTab` consuming it), gate=PASS
- goal: "A superadmin using the platform admin console experiences a flat, borderless,
  SaaS-professional visual language, grounded in real UI/UX research" — met: all 4 Exit criteria
  above are independently satisfied by their own gated task, each independently re-verified by the
  orchestrator against real source files (not merely trusted from a build agent's self-report),
  with zero regression to any existing route/tab/test confirmed via `git diff` at every gate.

### Deferred / explicitly out of this ship (named, not silently dropped)
- The Tier-3 directory kind/plan filter — deferred per the ux-researcher's own 2026-07-06 verdict
  (reopens the FROZEN `platform_tenants_router.py` for a job none of this milestone's scenarios
  need); tracked for real-usage-evidence-gated reconsideration, not this milestone.
- The 10 still-deferred accessibility findings from the persona-dive sweep (route-change focus
  management, both banners' live-regions, confirm-dialog button order, heading levels, field-error
  aria wiring) — pre-date this milestone, aren't specific to its new/touched surfaces, tracked in
  DESIGN.md as their own follow-up.
- `DialogContent`'s missing motion-safe entrance/exit transition — a standing `add.py todo` since
  `tenant-overview-strip`, unchanged by any task in this milestone.
- The repo-wide `asyncio.sleep(0.05)` fire-and-forget-audit-write test-drain idiom's confirmed
  load-sensitive flakiness (surfaced by `tenant-activity-tab`'s own verify pass) — a hardening
  candidate for a future task, not blocking this ship (the one affected test is unrelated to this
  milestone's own scope).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [x] Tin reviewed the accumulated diff and authorized commit (2026-07-06)
- [x] committed `0e542d3` (49 files, +7699/-196) on top of the existing `37e55ee` token-foundation
  commit, after an independent full-diff review pass (general-purpose subagent, verdict=CLEAN) —
  see commit message for the itemized per-task breakdown
- [x] pushed `feat/platform-console-flat-redesign` (HTTPS, `pilotspacex-byte` account — this repo's
  origin is SSH-only for `TinDang97`, which cannot push here) and opened PR #62
  (https://github.com/pilotspace/hydroa/pull/62, main ← feat/platform-console-flat-redesign,
  61 files/+9550/-86 for the full branch incl. the token-foundation commit)
- [ ] Tin reviews + merges PR #62
- [ ] bundle into the next `add.py release <version>` cut alongside the other releasable
  milestones/tasks already queued (per `add.py status`'s own "releasable" count) — tag/publish/
  deploy remain human-run, per release.md
