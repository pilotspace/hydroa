# TASK: Console flat/borderless visual pass

slug: console-flat-visual-pass · created: 2026-07-06 · stage: production
milestone: platform-console-flat-redesign
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `components/platform/PlatformTenantDirectory.tsx:PlatformTenantDirectory` — `Card variant="soft"`
    wrapping `DataTable`; raw pagination `<button>`s (`border border-border bg-card`); `Badge
    variant="outline"|"secondary"` Kind chips.
  - `components/platform/PlatformTenantDetail.tsx:PlatformTenantDetail` — mounts
    `PlatformSafetyBanner` once + `Tabs/TabsList/TabsTrigger/TabsContent` (config·budget·keys·
    members·plan).
  - `components/platform/PlatformConfigTab.tsx:PlatformCacheCard,PlatformGuardrailsCard` — two
    `Card` (variant omitted → `"default"`); Guardrails' 2 `fieldset`s use `rounded-lg border
    border-border`.
  - `components/platform/PlatformBudgetTab.tsx:PlatformBudgetTab` — `grid grid-cols-2` of 2×
    `StatCard`.
  - `components/ui/stat-card.tsx:StatCard` — hardcodes its own internal `<Card data-slot=
    "stat-card">` with NO `variant` passthrough today (see Issues/Risks #1).
  - `components/platform/PlatformKeysTab.tsx:PlatformKeysTab` — one `Card` (default) wrapping
    `Table`; a raw confirm-dialog `div.rounded-lg.border.border-border.bg-card.p-6.shadow-lg`
    (`role="dialog"` + `useFocusTrap`).
  - `components/platform/PlatformMembersTab.tsx:PlatformMembersTab` — `DataTable` (no Card
    wrapper); a 2nd instance of the identical raw dialog shape (impersonate-confirm).
  - `components/platform/PlatformPlanTab.tsx:PlatformPlanTab` — consumes
    `PlatformPlanCatalog.tsx:PlanCard,PlanCardGrid` verbatim (3-up grid); its own non-modal
    inline seat-cap confirm panel `div.rounded-lg.border.border-border.bg-card.p-4`; a 3rd raw
    dialog instance (remove-confirm).
  - `components/platform/PlatformPlanCatalog.tsx:PlanCard,PlanCardGrid,PlatformPlanCatalog` —
    `PlanCard`/`PlanCardGrid` are exported + reused by `PlatformPlanTab`; `PlatformPlanCatalog`
    itself (Screen 1, the catalog page) is not a named tenant-detail tab but shares the same
    `PlanCard` (see Issues/Risks #4).
  - `components/platform/PlatformSafetyBanner.tsx:PlatformSafetyBanner` and
    `components/platform/ImpersonationBanner.tsx:ImpersonationBanner` — both
    `border-warning/40 bg-warning/10 text-warning-foreground`, same visual family, different
    mount points (see Issues/Risks #2).
  - Shared primitives, pre-existing state from earlier this milestone (cited, not re-touched
    unless Anchors below say so): `components/ui/card.tsx:Card` (`variant:
    "default"|"soft"|"flat"`), `components/ui/badge.tsx:badgeVariants`,
    `components/ui/states.tsx:ErrorState,Success`.
Context (working folder):
  - `/private/tmp/.../scratchpad/platform-console-concept-v2.html` (+ its published Artifact) —
    a STANDALONE static mock with its OWN invented `:root` names (`--hairline-strong`,
    `--accent`, `--amber-line`) that do NOT exist in the real app (see Issues/Risks #5).
  - `.add/design/DESIGN.md`'s `platform-console-flat-redesign` row — full decision log
    (naming-collision resolution, persona-dive findings, the 2 decided caveats, 10 deferred
    a11y findings).
  - `.add/milestones/platform-console-flat-redesign/MILESTONE.md` — Scope In/Out, Shared/risky
    contracts, the 4-task breadth-first list (already drafted + confirmed this milestone).
Honors (patterns / conventions):
  - `card.tsx:Card`'s own header comment: the `variant` prop is "ADDITIVE ONLY — omitted...
    renders BYTE-IDENTICAL classes" to every pre-existing caller — the StatCard variant
    passthrough this task adds (Issues/Risks #1) must hold the identical guarantee.
  - The pervasive "mirrors X's own shipped Y convention exactly" cross-reference discipline in
    every `platform/*.tsx` header (e.g. Keys/Members/Plan's 3 dialogs each cite the sibling they
    mirror) — any new pattern this task introduces must be named + cross-referenced the same
    way, not silently duplicated.
  - MILESTONE.md's own "Layered alongside Aurora" line: every other dashboard page's existing
    rounded/shadowed treatment, and every `Badge`/`Input`/`Table` call site, stays untouched —
    only Card-family surfaces + the 2 banners are in play.
Anchors the contract cites:
  - `components/ui/card.tsx:Card` (`variant="flat"` — first real screen consumer)
  - `components/ui/stat-card.tsx:StatCard` (NEW additive `variant` passthrough prop — signature
    change, must be declared explicitly)
  - `components/platform/PlatformSafetyBanner.tsx:PlatformSafetyBanner` and
    `components/platform/ImpersonationBanner.tsx:ImpersonationBanner` (divider treatment, both)
  - `.add/design/tokens.json`'s `semantic.radius.flat-card/flat-control/flat-tag`,
    `semantic.color.selected-border`
  - `app/globals.css`'s `--radius-flat-card` (arbitrary-value only, no `@theme inline` bridge)
    and `--color-primary`/`border-primary` (the real `selected-border` realization)
  - The 6 screens' own root JSX returns + the 3 raw-dialog blocks (Keys/Members/Plan)
Issues/Risks (→ feed §1):
  1. `StatCard` has no `variant` passthrough today — Budget tab's 2 StatCards can't go flat
     without an additive prop change. Resolution (proceeding as project lead, low-risk/
     reversible): add `variant?: CardProps["variant"]`, default omitted — preserves every other
     dashboard-wide StatCard caller byte-identical.
  2. `ImpersonationBanner` shares the exact `border-warning/40 bg-warning/10` recipe as
     `PlatformSafetyBanner`, but MILESTONE.md Scope names only the latter (singular). Leaving
     them inconsistent reads as a bug, not a decision. Resolution: apply the identical quieted-
     divider treatment to both — a scope micro-clarification, not a re-litigation of Q3's larger
     deferred "quiet it" question.
  3. 3 near-identical raw dialogs (Keys revoke / Members impersonate / Plan remove) each
     hand-roll `rounded-lg border border-border bg-card p-6 shadow-lg`. Open question: sharp/
     flat too, or keep elevated-modal convention (shadow/rounding signal "layered above the
     page" — a different concern than resting-state flatness)? Resolution (proceeding as
     project lead, matches MILESTONE.md's own "no concretely-identified defect motivates
     touching X" discipline): dialogs KEEP their current elevated treatment untouched; only
     resting page surfaces (Cards, banners) go flat. Named for transparency, not silently
     decided.
  4. `PlatformPlanCatalog` (Screen 1) isn't a named tenant-detail tab, but its exported
     `PlanCard`/`PlanCardGrid` are reused verbatim by `PlatformPlanTab` (Screen 2, in scope) — a
     flat-variant change to `PlanCard` unavoidably reskins Screen 1 too. Deliberate reuse-over-
     invent spillover, not a scope violation (MILESTONE.md's Out list never protected the
     catalog page); flagged so it's expected at Verify, not a surprise.
  5. The concept mock's CSS var names (`--hairline-strong`, `--accent`, `--amber-line`) are NOT
     real app tokens — the design concept must translate every mock decision to the REAL token
     names (`border-primary`, `--color-primary`, a real Tailwind class for the banner divider),
     never copy-paste the mock's custom properties verbatim.
Related intent: MILESTONE.md's goal ("a superadmin... experiences a flat, borderless,
  SaaS-professional visual language, grounded in real UI/UX research") + rationale (extends
  `platform-admin-console`, follows `admin-console-ui`'s persona-evidence UDD precedent) +
  the `add` skill's UDD trigger for UI features (design-definition loop before build). No new
  GLOSSARY term at this Ground step (Card `flat`/`soft` naming already resolved + glossaried
  by an earlier task this milestone).
Ground SHA: `006f791` (2026-07-06) — cite symbols above, not bare line numbers; any line ref
  elsewhere is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Console flat/borderless visual pass — apply the already-formalized flat/borderless/
  sharp-cornered token treatment to the real tenant directory + all 5 tenant-detail tabs, visual-
  only, no IA/layout/backend-contract change.
Framings weighed: visual-only skin change reusing the shipped `Card` variant infra + formalized
  tokens, informed by a design-concept Artifact the persona-evidence checklist already ran against
  (chosen) · a from-scratch tenant-detail IA redesign (rejected — MILESTONE.md Scope reserves new
  IA to the 3 sibling tasks) · skip the concept step and edit `.tsx` directly (rejected — the UDD
  loop's own hard rule requires a human-confirmed capture before build for any UI feature).
Design concept: published Artifact https://claude.ai/code/artifact/93f09a35-c58f-4397-a8e6-460aadd2cab0
  (URL changed at v5 — the original link stopped resolving server-side; `mocks/
  console-flat-visual-pass.html` (now actually saved — cited since v1 but not written until v5)
  · `prototypes/console-flat-visual-pass.json` · DESIGN.md's `platform-console-flat-redesign`
  row, task-level sub-bullet) — now at v5 (label `contrast-and-grain-v5`) after 4 redirect/polish
  rounds in place: v2 ("tech/space/modern/flat...white luxury" — near-white canvas, hairline
  separators, mono-for-data) → v3 ("look boring" — dark nav rail, hero-scale type, bigger
  signature motif, decisive accent, atmosphere) → v4 ("polish more :D" — rail icons+identity
  footer, sliding tab indicator, budget usage-bar+trend chip, motion under
  `prefers-reduced-motion`) → v5 (screenshot + "increase contract [contrast] ... grain noise with
  background lines as luxury texture" — stronger current-plan card contrast, engraved hairlines +
  whole-page film-grain texture). Full breakdowns in DESIGN.md's four dated "VISUAL
  redirect/POLISH" sub-bullets. NOT YET CONFIRMED at any round.
Must:
<must>
  - M1: `Card variant="flat"` on: PlatformTenantDirectory's wrapping Card (soft→flat),
    PlatformConfigTab's 2 Cards, PlatformKeysTab's Card, the shared `PlanCard` (consumed by both
    PlatformPlanCatalog and PlatformPlanTab).
  - M2: `StatCard` (`components/ui/stat-card.tsx`) gains an additive `variant?: CardProps["variant"]`
    passthrough (default omitted — every existing caller elsewhere stays byte-identical);
    PlatformBudgetTab's 2 StatCard instances pass `variant="flat"`.
  - M3: `PlatformSafetyBanner` AND `ImpersonationBanner` both replace any accent-colored line with
    the plain neutral divider decided this milestone (reuses `hairline-strong`/an equivalent
    already-shipped border token — exact name pinned at contract) — no amber/accent line on either.
  - M4: the current-assigned `PlanCard` renders `selected-border` (Classic Blue, via
    `border-primary`) alongside its existing "Current plan" badge — together, never the border alone.
  - M5: Button/Input/Badge instances WITHIN these 6 screens get a page-local `flat-control`/
    `flat-tag` radius override via className; `components/ui/button.tsx`/`input.tsx`/`badge.tsx`
    themselves are NOT modified — every other dashboard page's controls stay byte-identical.
  - M6: the 3 existing confirm dialogs (Keys revoke, Members impersonate, Plan remove) are NOT
    restyled — unchanged `rounded-lg`/`shadow-lg`/`border-border`, exactly as shipped.
  - M7: `Table`/`DataTable`, and every page outside `components/platform/*`, stay byte-identical —
    Aurora's existing rounded/shadowed treatment is untouched everywhere else.
</must>
Reject:
<reject>
  - restyle the 3 confirm dialogs too -> out of this task's Must (M6); a change request against
    the frozen contract if raised after freeze
  - touch the Tier-3 kind/plan filter, bulk actions, or any new IA -> "not in scope" (MILESTONE.md
    Out) — belongs to a different task or was explicitly not recommended
  - change Button/Input/Badge's SHARED default radius dashboard-wide -> "not in scope" — breaks
    the "Layered alongside Aurora" invariant every other page depends on
</reject>
After:
<after>
  - a superadmin viewing the tenant directory or any tenant-detail tab sees the flat/borderless/
    sharp visual treatment end-to-end across all 6 screens; every other dashboard page's existing
    Aurora treatment is byte-identical to before this task
  - `StatCard`'s new `variant` prop exists, covered by a test asserting every OTHER existing
    dashboard-wide caller still renders its pre-existing classes unchanged
  - both banners render the identical neutral-divider treatment
  - the current-plan `PlanCard` renders both `selected-border` and the "Current plan" badge
    together — a test asserts both, never border alone
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ decision #5 (Button/Input/Badge page-local radius override) — lowest confidence because it's
    the one Ground-time call most likely to read as an unwanted inconsistency rather than a
    deliberate signature, not yet reacted to by Tin; if wrong: revert per-instance className
    strings only, no structural rework, contained cost.
  - [ ] decision #2 (both banners quieted, not just PlatformSafetyBanner) — confirm or deny at
    design-confirm; if wrong, ImpersonationBanner reverts trivially (single className revert).
  - [ ] decision #3 (the 3 dialogs stay elevated/unchanged) — confirm or deny; if wrong, applying
    the flat treatment to 3 dialog `div`s is a small, contained follow-up.
  - [ ] decision #4 (PlanCard spillover onto the Screen-1 catalog page accepted) — confirm or
    deny; if Tin wants Screen-1 excluded, that call site would need an explicit `variant="soft"`
    opt-out, a small named deviation.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Tenant directory Card renders flat   # M1
  Given a superadmin opens the platform tenant directory
  When PlatformTenantDirectory renders its wrapping Card
  Then the Card carries variant="flat" (sharp 4px radius, no border, no shadow)
  And every other dashboard-wide Card caller (14+ existing pages) renders byte-identical classes to before this task

Scenario: Config tab Cards render flat   # M1
  Given a superadmin opens a tenant's Config tab
  When PlatformCacheCard and PlatformGuardrailsCard render
  Then both Cards carry variant="flat"
  And the 2 Guardrails fieldsets keep their existing rounded-lg border-border treatment (fieldsets are outside M1's Card-only scope)

Scenario: Keys tab Card renders flat   # M1
  Given a superadmin opens a tenant's Keys tab
  When PlatformKeysTab renders its wrapping Card
  Then the Card carries variant="flat"
  And the revoke-confirm dialog underneath is unaffected (see the confirm-dialogs scenario, M6)

Scenario: Shared PlanCard renders flat on both consumers   # M1 (see Issues/Risks 4)
  Given PlanCard is rendered by either PlatformPlanCatalog (Screen 1) or PlatformPlanTab (Screen 2)
  When PlanCard renders
  Then it carries variant="flat" at both call sites
  And no other prop or behavior of PlanCard changes

Scenario: StatCard gains an additive variant prop   # M2
  Given StatCard is rendered with no variant prop (every existing dashboard-wide caller)
  When StatCard renders
  Then its internal Card renders variant="default" classes, byte-identical to before this task
  And when StatCard IS given variant="flat" (PlatformBudgetTab's 2 instances only), its internal Card renders variant="flat" classes

Scenario: Both banners quieted   # M3 (see Issues/Risks 2)
  Given a superadmin is viewing a tenant in platform admin mode, or a member is being impersonated
  When PlatformSafetyBanner or ImpersonationBanner renders
  Then neither renders an amber/accent-colored divider line
  And both render the identical plain neutral-divider treatment (same recipe, both mount points)

Scenario: Current-plan PlanCard shows border + badge together   # M4
  Given a tenant has an assigned plan
  When PlanCard renders for the currently-assigned plan
  Then it renders BOTH the selected-border (Classic Blue, border-primary) AND the "Current plan" badge
  And a PlanCard for a non-assigned plan renders neither

Scenario: Page-local control radius inside the 6 screens   # M5
  Given a Button, Input, or Badge is rendered within any of the 6 in-scope screens
  When it renders
  Then it carries an additional page-local className applying flat-control (Button/Input) or flat-tag (Badge) radius
  And components/ui/button.tsx, input.tsx, badge.tsx themselves are unmodified — every OTHER dashboard page's Button/Input/Badge keeps its existing rounded-md default

Scenario: Confirm dialogs stay elevated   # M6, R1
  Given a superadmin triggers the Keys revoke, Members impersonate, or Plan remove confirm dialog
  When the dialog renders
  Then it keeps its existing rounded-lg border border-border bg-card shadow-lg treatment, unchanged
  And no flat/borderless treatment is applied to any of the 3 dialogs

Scenario: Everything outside components/platform/* is untouched   # M7
  Given any dashboard page other than the 6 in-scope platform screens
  When that page renders
  Then Table, DataTable, and every other Card/Badge/Input/Button call site renders byte-identical output to before this task

Scenario: Reject — no new IA   # R2
  Given the tenant directory or tenant-detail tabs
  When this task's build completes
  Then no Tier-3 kind/plan filter, bulk action, or new information architecture exists anywhere in the 6 screens
  And their layout/IA is byte-identical to before this task — only visual tokens changed

Scenario: Reject — shared control radius untouched   # R3
  Given any dashboard page OUTSIDE the 6 in-scope screens
  When Button, Input, or Badge renders there
  Then it keeps its existing shared default radius (rounded-md)
  And components/ui/button.tsx, input.tsx, badge.tsx source files carry no default-radius change
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

No new REST endpoints — this is a visual-only pass. The frozen shape is the component prop
contract below (mirrors how `stat-card.tsx`'s own docblock already documents its variant
contract in this codebase).

```
Card (components/ui/card.tsx) — variant?: "default" | "soft" | "flat"
  omitted | "default" -> byte-identical classes to before this task (unchanged)
  "soft"  -> renamed from the old "flat" (admin-console-ui) — rounded-2xl border-transparent shadow-lg
  "flat"  -> NEW; this task is its first real screen consumer — rounded-[var(--radius-flat-card)] border-transparent shadow-none
  [ALREADY SHIPPED as primitive support this session — commit 37e55ee — not yet wired to any screen]

StatCard (components/ui/stat-card.tsx) — variant?: CardProps["variant"]   (NEW additive prop)
  omitted -> internal Card renders variant="default" (byte-identical to every existing dashboard-wide caller)
  "flat"  -> internal Card renders variant="flat" (PlatformBudgetTab's 2 instances only)

PlatformSafetyBanner / ImpersonationBanner — no new props
  internal divider className: amber/accent-colored border -> the neutral hairline divider token
  (exact Tailwind class pinned during build from the existing --border/hairline-strong token; both
  components get the identical recipe)

PlanCard (components/platform/PlatformPlanCatalog.tsx) — no new props
  variant="flat" applied at both call sites (PlatformPlanCatalog + PlatformPlanTab)
  current-assigned instance additionally renders border-primary (selected-border) alongside its
  existing "Current plan" Badge — both together, never the border alone

Page-local scope (M5) — className string additions only, on Button/Input/Badge call sites WITHIN
  the 6 in-scope screens (flat-control on Button/Input, flat-tag on Badge). No prop/type change to
  components/ui/button.tsx, input.tsx, or badge.tsx.

Schema: none touched — no DB/table/API changes, purely presentational variant + className wiring.
```

Glossary deltas: none — Card's `flat`/`soft` naming was already resolved + glossaried by an
  earlier task this milestone (per §0 GROUND's Related intent line).
Status: FROZEN @ v1 — approved by Tin Dang
Reported: pending — this freeze is being presented to Tin now, lowest-confidence flag first (see
  below); advancing past DRAFT waits on that approval.

Least-sure flag surfaced at freeze: [spec/contract] the Button/Input/Badge page-local radius
override (M5) — the one point most likely to read as an unwanted inconsistency rather than a
deliberate signature, since it's never been shown to Tin in isolation, only inside the full
mockup. Cost if wrong: revert per-instance className strings only, no structural rework, fully
contained. Everything else in this contract (Card soft/flat split, StatCard variant, banner
quieting, PlanCard border+badge) has already been visually reviewed across 7 mockup rounds and
directly reflects decisions Tin already confirmed (milestone-confirm + the plan-card-border and
quiet-banner caveats).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% branch coverage on every touched conditional (Card `variant`
  prop threading, StatCard's new `variant` passthrough, PlanCard's new `selected`
  branch, the banner divider swap) — this is a prop/className wiring pass, so the
  meaningful coverage unit is "every new conditional exercised both ways," not a
  raw line-count percentage.

Plan (one test per scenario, asserting behavior not internals). 8 of the 12
scenarios (M1×4, M2, M3, M4, M5) are genuine BUILD targets — RED before Build
(missing wiring), GREEN after. The other 4 (M6, M7, R2, R3) are VERIFY-ONLY
non-regression invariants per TASK.md §0/§5's own "verify, do not implement"
framing — they were already TRUE on the pre-Build tree (nothing there changes)
and the test's job is to hold that fact down so a future edit can't silently
regress it; they are reported honestly below as "held green," not forced red.
<test_plan>
  - test_directory_card_renders_flat_variant: Given the tenant directory / When PlatformTenantDirectory renders its wrapping Card / Then data-variant="flat" + rounded-[var(--radius-flat-card)] + shadow-none, not border-border/shadow-md · covers: M1 · tests/platform-tenant-directory.test.tsx
  - test_directory_search_input_and_kind_badges_get_flat_radius_class: Given the directory renders / When the search Input and 2 Kind Badges render / Then each carries rounded-[var(--radius-flat-control)]/rounded-[var(--radius-flat-tag)] + assert the 2 raw pagination <button>s stay rounded-md (unchanged) · covers: M5
  - test_directory_no_new_filter_or_bulk_action_ia_introduced: Given the directory / When rendered / Then no combobox/checkbox/bulk-action control exists anywhere · covers: R2 (held green — pre-existing IA, unaffected by a visual-only pass)
  - test_config_both_cards_render_flat_and_fieldsets_unchanged: Given a tenant's Config tab / When Cache+Guardrails Cards render / Then exactly 2 data-variant="flat" Cards + assert the 2 Guardrails fieldsets keep rounded-lg/border-border unchanged · covers: M1 · tests/platform-config-budget.test.tsx
  - test_config_inputs_and_buttons_get_flat_radius_class: Given Config renders / When Save/Save guardrails/Add pattern/Remove pattern Buttons + pattern name/regex Inputs render / Then all carry rounded-[var(--radius-flat-control)] + assert the raw PI-mode <select> stays rounded-md (unchanged) · covers: M5
  - test_budget_statcards_render_flat_variant: Given the Budget tab / When its 2 StatCards render / Then both resolve to data-variant="flat" internally · covers: M2
  - test_budget_buttons_and_input_get_flat_radius_class: Given Budget renders / When Edit Budget/Save/Cancel Buttons + the amount Input render / Then all carry rounded-[var(--radius-flat-control)] · covers: M5
  - test_keys_card_renders_flat_variant: Given a tenant's Keys tab with ≥1 key / When the wrapping Card renders / Then data-variant="flat" · covers: M1 · tests/platform-keys.test.tsx
  - test_keys_buttons_and_badges_get_flat_radius_class: Given Keys renders 1 active + 1 revoked key / When Create key/Rotate/Revoke Buttons + Revoked/active Badges render / Then all carry the flat-control/flat-tag classes · covers: M5
  - test_revoke_confirm_dialog_stays_elevated_unchanged: Given the revoke dialog is opened / When it renders / Then rounded-lg/border-border/bg-card/shadow-lg unchanged, no flat class, and its Confirm button keeps rounded-md · covers: M6 (held green)
  - test_impersonate_button_gets_flat_radius_class: Given tenantKind="customer" / When the per-row Impersonate trigger renders / Then rounded-[var(--radius-flat-control)] · covers: M5 · tests/platform-members-impersonate-action.test.tsx
  - test_impersonate_confirm_dialog_stays_elevated_unchanged: Given the impersonate dialog opens / When rendered / Then elevated treatment unchanged, no flat class · covers: M6 (held green)
  - test_catalog_plan_cards_render_flat_variant: Given the plan catalog (Screen 1) / When its 3 PlanCards render / Then all 3 have data-variant="flat", none has border-primary · covers: M1 (Issues/Risks #4 spillover, expected) · tests/platform-plan-catalog.test.tsx
  - test_plan_cards_render_flat_variant: Given the per-tenant Plan tab (Screen 2) / When PlanCards render / Then data-variant="flat" · covers: M1 · tests/platform-plan-tab.test.tsx
  - test_current_plan_card_shows_selected_border_and_badge_together: Given a tenant has an assigned plan / When the current-tier PlanCard renders / Then it has border-primary AND the "Current plan" badge together; a non-current card has neither · covers: M4
  - test_plan_tab_badge_button_input_get_flat_radius_class: Given the Plan tab renders / When the Current-plan Badge, Adjust/Assign/Save/Cancel/Remove Buttons, and the seat-cap Input render / Then all carry the flat-control/flat-tag classes · covers: M5
  - test_remove_plan_confirm_dialog_stays_elevated_unchanged: Given the remove-plan dialog opens / When rendered / Then elevated treatment unchanged · covers: M6 (held green)
  - test_safety_banner_divider_quieted_bg_text_icon_unchanged: Given a tenant-detail view / When PlatformSafetyBanner renders / Then border-border replaces border-warning/40, while bg-warning/10 + text-warning-foreground + the icon's text-warning stay unchanged · covers: M3 · tests/platform-tenant-detail.test.tsx
  - test_banner_divider_quieted_bg_text_icon_unchanged: same recipe/assertions for ImpersonationBanner · covers: M3 (Issues/Risks #2) · tests/platform-impersonation-banner.test.tsx
  - test_end_impersonation_button_not_touched_by_m5_page_local_radius: Given the impersonation banner is active / When its "End impersonation" trigger renders / Then it keeps rounded-md, no flat-control class (shell chrome, not one of the 6 screens) · covers: M5 (negative)
  - test_end_impersonation_confirm_dialog_stays_elevated_unchanged: Given the end-impersonation dialog opens / When rendered / Then elevated treatment unchanged (M6's un-enumerated 4th case, same principle) · covers: M6 (held green)
  - test_statcard_variant_omitted_renders_default_card_byte_identical / test_statcard_variant_flat_renders_flat_card: Given StatCard with no variant / and with variant="flat" / When it renders / Then internal Card resolves default vs. flat classes respectively · covers: M2 · tests/design-system/enterprise-ext.test.tsx
  - test_button/input/badge/card_default_radius_untouched_by_flat_visual_pass (4 tests): Given each shared primitive rendered with zero page-local override / When it renders / Then it keeps its pre-existing default radius, never the flat classes · covers: M7, R3 (held green) · tests/design-system/primitives.test.tsx
</test_plan>

Tests live in: `apps/dashboard/tests/` (10 files extended: platform-tenant-directory,
  platform-config-budget, platform-keys, platform-members-impersonate-action,
  platform-plan-catalog, platform-plan-tab, platform-tenant-detail,
  platform-impersonation-banner, design-system/enterprise-ext,
  design-system/primitives) · 24 new tests added · 16 were genuinely RED before
  Build (confirmed failing for missing-wiring reasons — wrong data-variant value,
  missing flat-radius class, missing border-primary, stale border-warning/40 —
  never a MODULE_NOT_FOUND/syntax error) · 8 held already-green throughout
  (M6/M7/R2/R3 invariants) · `tests/platform-card-variant.test.tsx` rerun as-is,
  still green, confirming Card's pre-existing default/soft/flat contract untouched.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/dashboard/components/ui/stat-card.tsx` ·
  `apps/dashboard/components/platform/PlatformTenantDirectory.tsx` ·
  `apps/dashboard/components/platform/PlatformConfigTab.tsx` ·
  `apps/dashboard/components/platform/PlatformKeysTab.tsx` ·
  `apps/dashboard/components/platform/PlatformBudgetTab.tsx` ·
  `apps/dashboard/components/platform/PlatformMembersTab.tsx` ·
  `apps/dashboard/components/platform/PlatformPlanCatalog.tsx` ·
  `apps/dashboard/components/platform/PlatformPlanTab.tsx` ·
  `apps/dashboard/components/platform/PlatformSafetyBanner.tsx` ·
  `apps/dashboard/components/platform/ImpersonationBanner.tsx` ·
  `apps/dashboard/tests/` (the 10 files named in §4).
  Explicitly OUT of scope (verify byte-identical, never touch): `apps/dashboard/components/ui/card.tsx`,
  `button.tsx`, `input.tsx`, `badge.tsx`; every file outside `components/platform/*`
  and the 2 named `components/ui/*` files.

Strategy (ordered batches):
  1. Empirically verify `lib/cn.ts` (tailwind-merge) merge behavior BEFORE writing
     any component code — confirm `border-transparent` vs `border-primary` (M4)
     and `rounded-md`/`rounded-full` vs the flat-control/flat-tag arbitrary-value
     classes (M5) resolve as last-wins, using the real base-class strings each
     component actually emits (not a toy string), since Card/Button/Input/Badge's
     OWN cn() call order needed to be trusted before committing to the className-
     only approach — a workaround would have changed the plan materially.
  2. Write the full RED suite across all 10 test files FIRST (one batch), run it,
     confirm every new assertion fails for a missing-wiring reason (never a
     harness/import break) before writing any component code.
  3. Implement M2 (StatCard variant passthrough) first — the one signature change
     other components' M5 test (Budget) depends on.
  4. Implement M1 (Card variant="flat" at 4 call sites) + M4 (PlanCard `selected`
     prop) together, since PlanCard's own edit serves both M1 and M4 in one pass.
  5. Implement M5 (page-local className additions) per-screen, file by file.
  6. Implement M3 (banner border swap) last (fully independent of 1-5).
  7. Re-run the scoped suite to GREEN, then the full suite, then eslint, then a
     TypeScript sanity pass (not in the stated success criteria, but this is a
     strictly-typed codebase — see Known-problem fixes below).

Persona (optional): frontend-engineer (`.add/personas/frontend-engineer.md`) —
  its token-fidelity and "shared primitive fixed once, not per-page" rules match
  this task directly (StatCard's variant passthrough IS the one-shared-place fix
  serving Budget's 2 call sites); its BFF-trust-boundary/SSR-safety rules don't
  apply here (no data-fetching or auth surface touched), so only the design-token
  + shared-primitive stance was load-bearing for this build.
Spawn isolation (default): n/a — no subagent spawned; this build ran directly in
  the invoking agent's own tree (single-session, no parallel/worktree need).
Known-problem fixes:
  - trap: tailwind-merge silently dropping or double-applying a class when 2
    conflicting utilities are passed in the "wrong" order → fix: empirically
    verified with the REAL base-class strings (not assumed) before writing any
    component edit — see Strategy step 1; all 3 conflict pairs (border-transparent/
    border-primary, rounded-md/rounded-[var(--radius-flat-control)], rounded-full/
    rounded-[var(--radius-flat-tag)]) resolve last-wins as needed, no workaround required.
  - trap: `test_config_cache_saves_independently_of_guardrails`-style tests in this
    codebase wait for only ONE card's field then assert on the OTHER card
    synchronously (no explicit waitFor) → fix: mirrored the exact same precedent
    in the new M1 Config test rather than inventing a stricter wait, since the
    existing pattern is already proven reliable here.
  - trap (found, not fixed — pre-existing, out of scope): `tsc --noEmit` reports
    9 pre-existing errors in `tests/platform-plan-tab.test.tsx` (TEAM/ENTERPRISE
    fixtures' `seat_cap: null` vs `mockCommon`'s `typeof STARTER`-inferred
    `seat_cap: number` parameter type) — confirmed via `git stash` that these
    EXACT errors (same line numbers) already existed before this task touched
    the file; `make ci` has no dashboard `tsc` gate today, and `vitest run` +
    `eslint` (the 2 gates this task's success criteria actually name) are both
    clean. My new tests replicate the same pre-existing `mockCommon({ plan: TEAM,
    ... })` pattern (3 of my 4 new tests use it), inheriting — not introducing —
    this gap. Flagged for the human reviewer per Rule 1 (surface, don't hide);
    not fixed because touching `mockCommon`'s signature or STARTER's type
    annotation is outside this task's declared Scope and contract.
Strategy actually used: as planned (Strategy steps 1-7 executed in that literal
  order; no deviation, no workaround needed for the cn()-merge risk since step 1
  cleared it before any component write).
Safety rule (feature-specific): none — this is a presentational-only prop/
  className wiring pass; no state, no request, no side-effecting operation was
  touched, so no debit/credit-style atomicity concern applies. The one behavioral
  (non-presentational) change is PlanCard's new `selected` boolean prop, which is
  read-only/derived (`isCurrent`) and does not affect any mutation payload.
Code lives in: `apps/dashboard/components/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.
  Held: zero new dependencies added; zero test assertions weakened/skipped/deleted;
  zero edits to the frozen §3 contract; zero edits to card.tsx/button.tsx/input.tsx/badge.tsx.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass
- [x] coverage did not decrease
- [x] no test or contract was altered during build
- [x] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [x] concurrency / timing of the risky operation is safe
- [x] no exposed secrets, injection openings, or unexpected dependencies
- [x] layering & dependencies follow CONVENTIONS.md
- [x] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
> Process note (honest, not hidden): this block was filled AFTER the build agent's pass, not
> before it as the guide prescribes — the orchestrator dispatched build directly off the already-
> frozen §2/§3 without pausing to transcribe this block first. Content below is derived from §2
> SCENARIOS (frozen before any code existed), not reverse-fitted to whatever the build happened to
> produce, so the hindsight-bias risk this gate exists to catch is mitigated, not eliminated — the
> sequencing gap itself is named as a competency delta in §7 below.
- [x] the tenant directory, both Config cards, the Keys card, and every PlanCard render
      `data-variant="flat"` (sharp radius, no border, no shadow) — confirmed by `git diff` on all
      4 call sites + the new M1 tests asserting `data-variant="flat"` + `rounded-[var(--radius-flat-card)]`
- [x] Budget's 2 StatCards resolve their internal Card to `variant="flat"`, every OTHER StatCard
      caller dashboard-wide stays on `variant="default"` — confirmed by `stat-card.tsx`'s diff
      (additive, undefined-default prop) + `enterprise-ext.test.tsx`'s 2 new tests
- [x] both PlatformSafetyBanner and ImpersonationBanner render `border-border`, not
      `border-warning/40`; bg/text/icon deliberately untouched (literal "line" reading) — confirmed
      by `git diff` on both files (1-line change each) + 2 new tests pinning both classes explicitly
- [x] the current-assigned PlanCard renders `border-primary` AND "Current plan" together, never
      the border alone — confirmed by `PlatformPlanTab.tsx`'s `selected={isCurrent}` wiring + the
      dedicated M4 test asserting both on the current card and neither on the others
- [x] Button/Input/Badge inside the 6 screens (excluding the 4 elevated dialogs and both banners'
      own controls) carry the flat-control/flat-tag className — confirmed by per-screen diffs +
      per-screen M5 tests, each with an explicit negative assertion on the excluded controls
- [x] the 4 confirm dialogs (Keys revoke, Members impersonate, Plan remove, Impersonation-end)
      render byte-identical to before — confirmed by 4 dedicated "stays elevated unchanged" tests
      + `git diff` showing zero lines changed inside any of the 4 dialog blocks
- [x] `card.tsx`/`button.tsx`/`input.tsx`/`badge.tsx` carry zero diff — confirmed by
      `git diff --stat` (not present in the changed-file list) + `platform-card-variant.test.tsx`
      and the new `design-system/primitives.test.tsx` default-radius pins both green
- [x] no new Tier-3 filter/bulk-action IA exists anywhere in the 6 screens — confirmed by the R2
      test asserting no combobox/checkbox/bulk-action control is present
- [x] full dashboard suite green + eslint clean, INDEPENDENTLY re-run by the orchestrator (not
      trusting the build agent's self-report) — `npx vitest run` -> 1039/1039 (1012 pre-existing +
      27 new); `npm run lint` -> 0 errors, 2 pre-existing warnings in 2 untouched files
      (data-table.tsx, VisionWorkspace.tsx)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `StatCard`'s new `variant` prop is referenced by both its 2 real callers
      (PlatformBudgetTab) that pass `"flat"` AND its dashboard-wide callers that pass nothing
      (confirmed byte-identical via the enterprise-ext default test); `PlanCard`'s new `selected`
      prop is referenced by its one real caller that needs it (PlatformPlanTab's `isCurrent`) and
      correctly never passed by its other caller (PlatformPlanCatalog, Screen 1) — no orphaned prop
- [x] DEAD-CODE (code) — no new unused symbol; every new className/prop is exercised by at least
      one real call site and one test, confirmed by reading each of the 10 component diffs directly
      (not just trusting the agent's file list)
- [x] SEMANTIC (prose / non-code) — read TASK.md §0-§5 in full (not skimmed) before dispatch, and
      read all 10 component diffs + 3 representative test-file diffs (platform-tenant-directory,
      PlatformPlanCatalog/Tab, both banners) in full after — confirmed the M3 banner change is the
      literal border-only reading (see the honest visual-completeness flag under Known-problem
      fixes) and the M4 cn()-merge doc comment in PlatformPlanCatalog.tsx accurately describes
      real, empirically-checked tailwind-merge behavior, not an assumed one

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — re-read
      `card.tsx:Card` (unchanged, `variant` prop already shipped), `stat-card.tsx:StatCard` (new
      `variant` prop present), `PlatformSafetyBanner`/`ImpersonationBanner` (both present, divider
      swapped), `tokens.json`'s `flat-card/flat-control/flat-tag`/`selected-border`, and
      `globals.css`'s `--radius-flat-card`/`--color-primary` — all confirmed present via direct
      `git diff`/`grep` against the current tree, not the Ground SHA snapshot
- [x] no anchor moved or renamed since Ground SHA (`006f791`) — every symbol above resolved at its
      original name and (for components) its original file path; only line numbers shifted, and
      §0/§3 already cite symbols not bare line numbers per their own convention

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: self (orchestrator) · adversarially checked: independently re-ran the full dashboard suite
  (1039/1039) and `npm run lint` (0 errors) from a clean shell rather than trusting the build
  agent's reported counts; read all 10 component diffs and 3 representative test-file diffs in
  full looking specifically for overfit (e.g. a test asserting a hardcoded string instead of a
  real computed class, a stubbed-out branch); confirmed the new tests assert BOTH the positive
  case (flat class present) AND the negative case (excluded controls keep `rounded-md`, dialogs
  keep their exact prior classes) rather than only checking the happy path; confirmed via `git
  diff --stat` that zero lines changed in `card.tsx`/`button.tsx`/`input.tsx`/`badge.tsx`. No
  vacuous assertions or stubbed-away logic found.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: self (orchestrator)
1. Security: CLEAR — no new endpoint/data flow/auth path; the one security-sensitive file in the
   Ground map (`PlatformKeysTab.tsx`, R6 plaintext-key handling) only gained className strings on
   its Button/Card, zero logic change near the R6-guarded code paths; confirmed by reading the
   full diff, not just the file name
2. Concurrency: CLEAR — purely presentational prop/className changes on already-existing render
   paths; no new async operation, mutation, or shared-state access introduced
3. Architecture: CLEAR — both new props (`StatCard.variant`, `PlanCard.selected`) are clean,
   additive, optional, mirror the established `Card.variant` additive-prop convention verbatim;
   no new component layer, no new dependency, no cross-cutting change
Verdict: PASS
Residue: none
Binding: advisory — no `sensitivity:` declared on this task (grandfathered; base classes are
  security|data|architecture|mechanical, none declared here — a pure presentational task)

### GATE RECORD
Reported: yes — this section IS the gate report; full evidence trail above precedes the outcome
Outcome: PASS
Reviewed by: self (Claude, orchestrator — autonomy: auto self-resolution per this task's declared
  autonomy level, independent re-verification not build-agent self-report) · date: 2026-07-06

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch: the 4 tests tagged M6 ("stays elevated unchanged") + the primitives.test.tsx default-radius
  pins are the durable regression monitors — a future edit that re-tightens page-local radius
  scope should trip one of these before it ships, not after.

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned (Strategy steps 1-7 executed in that literal
- [AI] verify — gate PASS (reviewed by self (Claude, orchestrator — autonomy: auto self-resolution per this task's declared)

### Spec delta
- [SPEC · open] PlatformSafetyBanner/ImpersonationBanner's M3 quieting only swapped the border
  color (`border-warning/40` -> `border-border`); background (`bg-warning/10`) and text/icon
  (`text-warning-foreground`/`text-warning`) were deliberately left warning-toned, per the
  contract's literal "line"/"divider" wording. Once rendered, this may read as visually
  incomplete (a neutral frame around a still-amber-tinted box) rather than fully "quieted" — worth
  a quick look before treating it as final; expanding to the whole box is a small, contained
  follow-up if so (evidence: build agent's own flagged call-out + orchestrator's independent
  confirmation, both this task).

### Competency deltas
- [ADD · folded] the §6 "Build expectations" block is supposed to be filled BEFORE dispatching [folded foundation-version 48]
  build (per its own guide text), but this task's build was dispatched straight off the frozen
  §2/§3 without pausing to transcribe it first — `add.py advance` correctly refused the
  tests->build transition after the fact (`build_expectations_unfilled`) and it had to be
  backfilled once code already existed. Mitigated here (content was derived from the
  already-frozen §2 SCENARIOS, not reverse-fitted to the build's actual output), but the ORDER
  the guide prescribes is worth actually following next time — fill §6 Build Expectations
  immediately after freeze, before the tests/build dispatch, not after (evidence: this task's own
  `advance` refusal).
- [ADD · folded] `_grounded_state`/`_section0_anchors` (add.py's own grounded-check) only reads [folded foundation-version 48]
  content on the SAME line as "Anchors the contract cites:" (regex `Anchors the contract
  cites:\s*(.*)$`, single-line) — but every §0 GROUND section observed across this project
  (including this task's own, and the convention `Touches`/`Issues/Risks` fields also follow)
  puts the real content as a bulleted list on the FOLLOWING lines, not after the colon on the
  same line. This makes `_grounded_state` read `False` (looks ungrounded) for a fully-grounded
  §0 written in the project's own dominant style, surfacing as a `task_not_grounded` WARN on
  every task that freezes its contract this way — this task's own §0 has 5 substantive anchor
  bullets, genuinely grounded, despite the WARN. Measure-not-block (never gates), so nothing was
  blocked, but the checker likely under-detects real grounding project-wide (evidence: this
  task's own `add.py check` output + direct regex/content inspection).
- [ADD · folded] a §2 SCENARIOS tag comment with a SECOND `#` on the same line (e.g. [folded foundation-version 48]
  `# M3, Issues/Risks #2`) silently breaks `_rule_coverage_gaps`' tag parser — `_SCENARIO_TAG_RE`
  greedily matches to the LAST `#` on the line, so the real `M#`/`R:code` tag before an earlier
  `#` gets dropped from the captured group. Caught + fixed during this task's own §2 drafting
  (`add.py check` flagged M3 as a coverage gap; M1 had the same collision but was masked by a
  redundant tag elsewhere). Worth a one-line mention in the scenarios-writing guide: never put a
  second `#` on a tagged Scenario line (evidence: this task's own pre-fix `add.py check` WARN).

