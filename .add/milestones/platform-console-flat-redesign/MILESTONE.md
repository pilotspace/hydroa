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
release: pending

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
- [ ] console-flat-visual-pass   depends-on: none   — apply the flat/borderless/sharp treatment
      (Card `flat` variant, `flat-tag`/`flat-control` radii, `selected-border`, the quieted
      safety-banner divider) to the tenant directory + all 5 tenant-detail tabs
- [ ] tenant-overview-strip      depends-on: none   — build the Tenant Overview Strip (4
      independent queries, reusing the tabs' existing cache keys)
- [ ] command-palette            depends-on: none   — build the global ⌘K command palette
      (navigate-only, reuses the existing tenant-search endpoint, real ARIA listbox semantics)
- [ ] tenant-activity-tab        depends-on: none   — new superadmin-scoped audit-read route +
      a 5th "Activity" tenant-detail tab rendering it

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A superadmin viewing the tenant directory or any tenant-detail tab sees the flat/borderless/
      sharp visual treatment; every other dashboard page's existing rounded/shadowed Aurora
      treatment is unchanged        (← console-flat-visual-pass)
- [ ] A superadmin opening a tenant's detail page sees an Overview Strip showing plan/seats/keys/
      budget at a glance, each field loading and failing independently of the others
      (← tenant-overview-strip)
- [ ] A superadmin can open a ⌘K command palette from anywhere in the dashboard, search by tenant
      name, and navigate directly to that tenant        (← command-palette)
- [ ] A superadmin viewing a tenant's detail page can open an "Activity" tab and see that
      tenant's real audit history (actor/action/target/when), authorized the same way as the
      other tenant-detail routes        (← tenant-activity-tab)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
