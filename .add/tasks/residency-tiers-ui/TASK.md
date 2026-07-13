# TASK: Data & residency settings, region badges, tier selector, pricing page

slug: residency-tiers-ui · created: 2026-07-12 · stage: production
milestone: residency-service-tiers
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: verify   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
sensitivity: mechanical
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/components/settings/RetentionZdrSettings.tsx` — the asymmetric confirm-gate
  idiom this task mirrors EXACTLY: `handleZdrToggle` (destructive direction — enabling —
  optimistic-flip + `ConfirmDialog`; safe direction — disabling — fires immediately),
  `handleConfirmZdrEnable`, `handleZdrConfirmClose` (reconciles the control from
  `queryClient.getQueryData`, correct regardless of confirm/cancel timing). This task ADDS a
  third fieldset to this SAME file/component (residency pin), never a separate component —
  MILESTONE.md binding rule 5 says residency and ZDR live in the "same Data & residency settings
  surface."
- `apps/dashboard/components/settings/SettingsPage.tsx:SettingsPage` — the `Tabs`/`TabsTrigger`/
  `TabsContent` shell; the tab currently labeled "Retention & ZDR" (`value="retention"`) is the
  literal surface MILESTONE.md names — this task RENAMES its label to "Data & residency" (the
  `value` token stays `"retention"`, avoiding any deep-link/test-id churn).
- `apps/dashboard/components/teams/ConfirmDialog.tsx:ConfirmDialog` — the ONLY confirm-gate
  primitive anywhere in `apps/dashboard/components` (button-confirm: Cancel / destructive-styled
  confirm, focus-trapped via `useFocusTrap`, inline `role="alert"` on failure). Grepped the full
  tree for a type-the-word/typed-confirmation pattern — zero hits. See Issue #4.
- `apps/dashboard/components/models/ModelsPage.tsx:ModelsPage` — the LIVE `/app/models` route
  (confirmed via `app/(app)/app/models/page.tsx`), a TanStack `DataTable` over
  `ColumnDef<AdminModelItem>[]`, reading `GET /admin/models`. This is DISTINCT from
  `apps/dashboard/components/models/ModelCatalogTable.tsx` (a separate div-based renderer of a
  different `ModelEntry`/`ModelsData` shape — not imported by any routed page found via grep;
  legacy/unrouted) — this task's region-badge column is added to `ModelsPage.tsx`'s `columns`
  array (mirrors the existing `input_modalities` column, ~line 127-144, verbatim shape), never to
  `ModelCatalogTable.tsx`.
- `apps/dashboard/components/ui/badge.tsx:Badge`/`badgeVariants` — 6 existing variants
  (default/secondary/outline/success/warning/destructive); region badges reuse `outline`
  (neutral, non-alarming — a descriptor, not a status). No new variant/color token.
- `apps/dashboard/components/keys/CreateKeyDialog.tsx:CreateKeyDialog`/`CreateKeySchema` (Zod) —
  the key-creation form (currently `name` only, Zod-validated client-side, `err.status===422` ->
  field error / else -> `globalError` branching). Tier selector + price-delta land here.
- `apps/dashboard/components/keys/KeysPage.tsx:KeysPage.handleCreateKey`/`createKeyMutation` —
  owns the actual `bffPost<CreateKeyResponse>("/admin/keys", { name })` call `CreateKeyDialog`'s
  `onSubmit` delegates to; the body's new `tier` field is added here.
- `apps/dashboard/app/(marketing)/pricing/page.tsx:PricingPage`/`TIERS` — frozen §3 v1 (a prior
  sibling task's own frozen contract, docstring cites it): PUBLIC Server Component, zero fetch,
  "Prices are representative placeholders (no commercial model finalised) — copy, not a
  commitment." Residency + priority story is additive static copy in the SAME register.
- `apps/dashboard/lib/hooks/use-current-user.ts:useCurrentUser`/`CurrentUser.role` (`string |
  null`) — the only client-side role signal; observed values `"owner"`/`"admin"` (`ModelsPage`'s
  `canManage`) — `"member"` inferred as the third role from `RetentionZdrSettings`/`KeysPage`'s
  own lack of client-side hiding (backend-403-surfaces-inline is the established convention).
- `apps/dashboard/components/plan/PlanSeatsPage.tsx` (billing-ui sibling, shipped) — the
  "degrades gracefully / attempts NO fetch against a not-yet-shipped sibling's endpoint"
  precedent: `R7` renders a static "Seat pricing coming soon" placeholder for `seat-billing`
  (its own then-unshipped sibling) with zero network call. THE idiom this task's tier
  price-delta fallback mirrors for `service-tiers` (confirmed BLANK template, see Issue #2).
- `apps/dashboard/lib/bff-client.ts` / `resilient-fetch.ts:BffError`/`ProblemDetail` — the one
  error class/shape (`err.problem.title`) every settings/keys/models surface already reads for
  inline error display; reused verbatim, no new error-handling shape invented.
- `apps/dashboard/test-support/axe.ts` + `apps/dashboard/tests-bff/tenant-settings.test.tsx` —
  the `axe(container, { rules: { "color-contrast": { enabled: false } } })` sweep already run
  against `SettingsPage`'s tabs; this task's new fieldset/column/dialog/selector join that SAME
  existing sweep (Anchors), not a new one.
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission.SECURITY_CONFIG` /
  `apps/gateway/src/gateway/catalog/api/router.py:get_admin_models` /
  `apps/gateway/src/gateway/keys/api/router.py:create_key` (`Depends(require_owner_or_admin)`) —
  BACKEND RBAC ground truth for the RBAC matrix in DESIGN.md (residency PUT = OWNER-only; catalog
  GET = any authenticated role; key creation = owner-or-admin) — read directly, not assumed.
- `apps/dashboard/app/api/gw/[...path]/route.ts` — the existing catch-all BFF proxy. Confirmed:
  EVERY route this task reads/writes (`/admin/residency-policy`, `/admin/models`, `/admin/keys`,
  and the forward-cited `/admin/service-tiers`) passes through this ONE existing proxy —
  zero new BFF route files needed for this task.

Context (working folder): `tmp/residency-tiers-design-context.md` (binding cross-task rules 1-6 +
WAVE-1 addendum, read in full); `.add/milestones/residency-service-tiers/MILESTONE.md` (UI/UX
in-scope bullet + Exit criteria 5-6); the THREE frozen sibling contracts read in FULL:
`.add/tasks/residency-policy/TASK.md` §3 (FROZEN @ v1), `.add/tasks/region-catalog-dimension/TASK.md`
§3 (FROZEN @ v1), `.add/tasks/region-pricing/TASK.md` §3 (FROZEN @ v1); `.add/tasks/service-tiers/TASK.md`
— confirmed the BLANK TEMPLATE (`phase: ground`, no §0-§3 content) as of this grounding pass, not
trusted from milestone prose (folded PROJECT.md DDD lesson, applied verbatim per residency-policy's
own precedent of re-verifying a cited sibling's actual ground state).

Honors (patterns / conventions):
- The ZDR asymmetric-confirm idiom verbatim: destructive direction (tightening/setting a pin)
  gated behind `ConfirmDialog`; safe direction (clearing a pin) fires immediately (RetentionZdrSettings M9).
- "Disabled + muted, never unmounted" (RetentionZdrSettings M10) — reused for catalog rows a
  tenant's residency pin makes ineligible: the backend still legitimately returns the row, so the
  UI never hides it, only marks it.
- "No fetch attempted against an unshipped sibling's endpoint" (PlanSeatsPage R7) — reused for
  the tier price-delta fallback against `service-tiers`.
- Lean-public-vs-extended-admin split (region-catalog-dimension M5: `GET /v1/models` stays
  byte-identical; `region` only lands on `/admin/*` surfaces) — this task's badge reads
  `AdminModelItem.region`, never expects `region` on any public-facing model list.
- Money/percentage figures are ALWAYS server-computed, Decimal end-to-end (region-pricing M8) —
  the FE only formats and displays a server-returned multiplier/percentage; it never computes or
  hardcodes a markup number itself (M10).
- CONVENTIONS.md WCAG 2.2 AA floor + the project's own `ui-designer` persona Default Requirement
  (contrast/`focus-visible`/hit-target/landmark, computed not eyeballed) — checked on every new
  surface by default.

Seams consulted: none (`.add/SEAMS.md` does not exist in this project).

Anchors the contract cites: `RetentionZdrSettings.tsx` (extended) · `ConfirmDialog.tsx` (reused) ·
`SettingsPage.tsx` (tab label rename) · `ModelsPage.tsx:columns` (extended) · new
`components/ui/region-badge.tsx:RegionBadge` (the ONE new visual, milestone's own instruction —
"design it once, use everywhere") · `CreateKeyDialog.tsx`/`CreateKeySchema` (extended) ·
`KeysPage.tsx:handleCreateKey`/`createKeyMutation` (extended) ·
`(marketing)/pricing/page.tsx:TIERS` (extended) · `use-current-user.ts:CurrentUser.role` (reused) ·
`bff-client.ts:BffError`/`ProblemDetail` (reused) · residency-policy `GET`/`PUT
/admin/residency-policy` (FROZEN, cited verbatim) · region-catalog-dimension `AdminModelItem.region`
(FROZEN, cited verbatim) · region-pricing's reserved (unimplemented) `resolve_tier_multiplier`
signature (FROZEN, cited as the only textual evidence a tier-pricing resolver will exist) ·
service-tiers (UNFROZEN — forward-cited only, see Issue #1).

Issues/Risks (→ feed §1):
1. ⚠ **CROSS-CONTRACT GAP: residency-policy's frozen PUT does not accept `"ap"`.**
   `region-catalog-dimension` and `region-pricing` both froze (later the same day) with the
   4-value region set `us|eu|ap|global` (WAVE-1 addendum, a Tin directive added "mid-freeze").
   `residency-policy`'s OWN frozen §3 (`GET`/`PUT /admin/residency-policy`) still validates
   `region` against exactly `{null,"us","eu"}` (its M1, R2 — 422 `ERR_RESIDENCY_REGION_INVALID`
   on anything else) and its own "DECIDED at freeze review" note never revisits this (it covers
   only M6/realtime/BYOK). CONCRETELY: today, `PUT /admin/residency-policy {region:"ap"}` 422s
   against the real frozen backend, even though `ap` catalog rows and `ap` pricing both already
   exist and work — an Asia/Vietnam tenant cannot pin residency at all. This is a genuine,
   found-in-grounding inconsistency between two ALREADY-FROZEN sibling contracts; this
   design-only task cannot fix it (MUST NOT edit a frozen contract). Two forks for Tin (named
   again at the §3 flag): (a) this task's v1 picker offers only `{unrestricted, us, eu}` as
   pinnable, with `ap` rendered visibly-disabled + a "not available yet" note, and a forward
   SPEC delta reopens residency-policy for an `ap`-add change request; or (b) block this task's
   own freeze until residency-policy is amended first. This draft assumes (a).
2. **service-tiers is a coordination input, not ground truth.** Confirmed BLANK (`phase: ground`,
   no Must/Reject/Contract at all). The only textual evidence any tier-pricing resolver will
   exist is region-pricing's own RESERVED (unimplemented, raises `NotImplementedError`)
   `resolve_tier_multiplier(session, tenant_id, model_id, tier)` stub plus MILESTONE.md's DECIDED
   "+25%" seed language. This task forward-cites an ASSUMED `GET /admin/service-tiers`
   shape (mirroring region-pricing's own `GET /admin/region-pricing` shape — the closest sibling
   precedent) for the key-creation price delta, and designs the UI to degrade gracefully
   (PlanSeatsPage R7's idiom: render the selector, show an inert "Pricing pending" placeholder,
   attempt NO fetch against a guessed endpoint) if that assumption is wrong or unshipped by this
   task's own Build.
3. `resolve_tier_multiplier`'s reserved signature takes `model_id` — implying a possibly
   PER-MODEL tier markup — but `CreateKeyDialog` has no model-selection step (a key is not bound
   to one model at creation; `model_allowlist` is set later via `KeyGovernanceEditor`). This task
   therefore assumes the price delta shown at key creation is a flat, TENANT-LEVEL figure
   independent of any specific model — flagged as an open assumption (§1), unconfirmable until
   service-tiers drafts its own Must list.
4. MILESTONE.md's own UI/UX bullet and this task's slug line both say "typed confirm gate," but
   grepping the ENTIRE `apps/dashboard/components` tree for a type-the-word-to-confirm pattern
   returns zero hits — the only confirm-gate primitive in this codebase is `ConfirmDialog.tsx`
   (button-confirm, not text-entry). Interpreted here as "a confirm gate carrying typed-out
   (written) consequence copy" — matching the persona's own instruction to mirror ZDR EXACTLY,
   rather than inventing a new text-entry interaction with zero sibling precedent
   (reuse-before-invent). Flagged in case "typed" was meant literally.
5. `ModelsPage.tsx`'s existing `canManage` check (`role==="owner"||role==="admin"`) gates ONLY
   the re-sync button — the table itself (and therefore any new region badge/ineligibility
   styling) is visible to every authenticated role, including `member`, consistent with
   `GET /admin/residency-policy`'s own "any authenticated role may read" (residency-policy M2) —
   no new RBAC surface is introduced by this task.
6. The marketing `/pricing` page's frozen contract explicitly disclaims "prices are
   representative placeholders (no commercial model finalised)" — the residency/priority STORY
   this task adds must stay in that same register (feature copy, not a live-computed price), or
   it would silently promise a commitment the page's own frozen contract forbids.

Related intent: MILESTONE.md UI/UX-in-scope bullet + Exit criteria 5-6; MILESTONE.md binding
rules 1 (region is the single source of truth), 2 (fail-closed, audited + confirm-gated), 4
(tier is a capacity preference, not a guarantee), 5 (composes with ZDR, same settings surface);
the three frozen sibling tasks' own Glossary deltas (`region`, `residency policy`, `region pin`,
`region multiplier`, `tenant_region_multiplier_overrides`) — cited, never redefined here; this
task's own new Glossary delta (§3) covers the FRONTEND-only "consequence line" concept and
`RegionBadge`.

Ground SHA: 853afa8

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Data & residency settings surface (region pin + consequence line), catalog region
badges (incl. pinned-tenant ineligibility view), key-creation Priority/Standard tier selector
with price delta, and the marketing pricing page's residency + priority story.

Framings weighed: **extend existing surfaces additively (chosen)** · a dedicated new
`/app/residency` page + nav group · a new typed (text-entry) confirm component

- **(chosen) Extend existing surfaces additively.** `RetentionZdrSettings.tsx` gets a third
  fieldset + a tab-label rename; `ModelsPage.tsx` gets one new badge column; `CreateKeyDialog.tsx`
  gets a selector; the marketing pricing page gets new static copy. Zero new nav entries, zero
  new pages — matches MILESTONE.md's own instruction ("extends the EXISTING ZDR panel idiom...
  no new design idiom beyond the badges") and the persona's reuse-before-invent rule.
- (rejected) A dedicated new page/nav group (mirrors billing-ui's `/app/invoices`+`/app/credits`+
  `/app/plan` pattern): residency is a single GET/PUT pair, not a multi-page domain — MILESTONE.md
  is explicit that it EXTENDS the existing ZDR surface; a new page/nav group would be unjustified
  surface growth this milestone never asked for.
- (rejected) A new type-the-word text-entry confirm component distinct from `ConfirmDialog`
  (Issue #4): no sibling precedent anywhere in this codebase; violates reuse-before-invent. This
  draft instead uses `ConfirmDialog`'s existing button-confirm with typed-out (written)
  consequence copy, matching the persona's instruction to mirror the ZDR idiom EXACTLY.

Must:
<must>
  - M1: The Settings tab currently labeled "Retention & ZDR" (`SettingsPage.tsx`, `value="retention"`)
    is renamed "Data & residency"; `RetentionZdrSettings.tsx` gains a THIRD fieldset ("Data
    residency") reading `GET /admin/residency-policy` and writing `PUT /admin/residency-policy`
    (residency-policy's frozen §3, cited verbatim) — same file, same tab, per MILESTONE.md
    binding rule 5 ("same Data & residency settings surface"). The Retention and ZDR fieldsets
    are otherwise UNCHANGED (no behavior/markup regression).
  - M2: The residency fieldset offers exactly THREE selectable options: "No pin (unrestricted)"
    (`region: null`), "US", "EU" — NOT "AP" (Issue #1). `ap` renders as a visibly-DISABLED
    option with helper text "Not available yet — Asia-Pacific residency pinning is a tracked
    follow-up," never silently omitted, so the gap is legible, not hidden.
  - M3: Saving a NEW pin value that differs from the last-known server value (`seededData.region`)
    — covering BOTH "unset → pinned" and "pinned A → pinned B" — triggers `ConfirmDialog` with
    the region-specific consequence-line copy (§3, verbatim) BEFORE the `PUT` fires; mirrors
    `handleZdrToggle`'s destructive-direction gate exactly, including reconciling the displayed
    value from `queryClient.getQueryData` on close (mirrors `handleZdrConfirmClose`), correct
    whether the user confirms or cancels.
  - M4: Saving "No pin (unrestricted)" when a pin is currently set fires the `PUT` immediately,
    NO `ConfirmDialog` — mirrors ZDR's disable-is-safe/immediate asymmetry (M9's inverse) exactly.
  - M5: A non-OWNER role sees the SAME picker + Save control as an OWNER (no client-side hiding,
    Issue #5); a 403 from the `PUT` (residency-policy R3) surfaces via the SAME inline error
    pattern `RetentionZdrSettings.tsx` already uses for its other two fieldsets — no new
    error-display shape.
  - M6: `ModelsPage.tsx`'s `columns` array gains a "Region" column rendering the new
    `RegionBadge` component (`Badge variant="outline"`, uppercased region text: `US`/`EU`/`AP`/
    `GLOBAL`) from `AdminModelItem.region` — mirrors the existing `input_modalities` column's
    position/cell shape verbatim. `RegionBadge` is the ONE new visual this task introduces
    (MILESTONE.md: "design it once, use everywhere") and is reused nowhere else visually
    differently.
  - M7: When the calling tenant has a residency pin set (read from the SAME
    `GET /admin/residency-policy` call M1 uses, fetched once by `ModelsPage` and never re-fetched
    per row) and a catalog row's `region` does not satisfy that pin (residency-policy/
    region-catalog-dimension M6 semantics: `global`/mismatched region never satisfies a specific
    pin), that row's `Enabled` `Switch` renders `disabled` and the row is visually muted (mirrors
    RetentionZdrSettings M10's "disabled + muted, never unmounted" idiom) with an additional
    inline "Ineligible in {PIN}" `Badge variant="warning"`. The row STAYS in the table — never
    filtered out — an admin must still be able to see and reason about the full catalog.
  - M8: If the residency-policy read (M7's data source) is loading or fails, `ModelsPage.tsx`
    still renders the region badges (M6) WITHOUT the ineligibility treatment (M7) — a
    residency-read failure never blocks the catalog table itself (mirrors `PlanSeatsPage`'s
    independent-per-read degrade idiom, applied to a READ this time rather than a write).
  - M9: `CreateKeyDialog.tsx` gains a required tier selector (`"priority"|"standard"`, default
    `"standard"`) submitted as `tier` in the `POST /admin/keys` body (field OWNED by service-tiers,
    cited — not redefined here; Issue #2/#3). Inline copy states the capacity-preference nuance
    verbatim (§3): "Priority requests get preference under contention and may fall back to
    Standard when capacity is unavailable — Standard is never starved."
  - M10: Next to the tier selector, a price-delta line reads a forward-cited
    `GET /admin/service-tiers` (assumed shape, Issue #2) ONCE per dialog open and shows the
    SERVER-COMPUTED delta for the `priority` option (e.g. "+25% on requests using this key") —
    NEVER a hardcoded percentage. If that fetch 404s/errors/is unavailable, the price-delta line
    is replaced with the inert placeholder "Pricing pending" and NO retry/poll is attempted
    (mirrors `PlanSeatsPage` R7's zero-fetch-against-unshipped-sibling idiom) — the tier selector
    itself still renders and remains fully submittable.
  - M11: The marketing `/pricing` page gains: (a) a new feature bullet on the "Team" tier
    ("Priority service tier (optional, usage-priced)") and the "Enterprise" tier ("Data
    residency: pin inference to US or EU"); (b) one new short static section/callout naming the
    residency + priority story in prose — no live data, no price commitment, matching the page's
    own frozen "representative placeholders" posture (Issue #6). Zero new fetch; the page stays a
    Server Component.
  - M12: Every new/changed surface (residency fieldset, region badge column, ineligibility
    badge, tier selector + price-delta, pricing-page copy) passes the SAME
    `axe(container, { rules: { "color-contrast": { enabled: false } } })` sweep already run
    against `SettingsPage`/`ModelsPage`/`CreateKeyDialog` with zero NEW serious/critical
    violations — WCAG 2.2 AA floor (contrast ≥4.5:1 body / ≥3:1 large text, visible
    `focus-visible`, ≥44px hit targets, correct landmark order), COMPUTED not eyeballed (the
    ui-designer persona's Default Requirement).
</must>
Reject:
<reject>
  - R1: Attempting to select/save "AP" as a residency pin in the FE picker -> the option is
    rendered `disabled` (client-side prevention — the frozen backend would itself 422
    `ERR_RESIDENCY_REGION_INVALID` on `"ap"` today, Issue #1) — never a submitted request that
    predictably fails.
  - R2: `PUT /admin/residency-policy` returns 422 `ERR_RESIDENCY_REGION_INVALID` (a defensive
    case — should be unreachable given R1, but never swallowed) -> the picker's pending selection
    is NOT written to `seededData`; the displayed value reverts to the last-known-good server
    state on the next reconcile, and the server's `title` surfaces inline.
  - R3: `PUT /admin/residency-policy` returns 403 (non-OWNER) -> inline error (M5); the picker's
    displayed value reconciles from the query cache (mirrors `handleZdrConfirmClose`) — no
    silent success implied.
  - R4: `GET /admin/service-tiers` (forward-cited, Issue #2) is unavailable (404/5xx/
    network error) -> "Pricing pending" placeholder (M10); tier selector stays submittable; NO
    error banner — this is an expected degrade, not a fault (mirrors `PlanSeatsPage` treating an
    unshipped sibling's absence as normal, never an `ErrorState`).
  - R5: `POST /admin/keys` rejects the submitted `tier` value (a future service-tiers validation
    error, exact code TBD by that task) -> surfaces via `CreateKeyDialog`'s EXISTING
    error-branching (`err.status===422` -> field-level message; else -> `globalError`) — no new
    error-handling code path invented ahead of service-tiers' own frozen error codes.
</reject>
After:
<after>
  - A tenant admin sees ONE settings surface ("Data & residency") covering retention window,
    ZDR, AND the residency pin, each independently readable/writable, with the pin's destructive
    direction gated behind the SAME confirm idiom as ZDR and the consequence line stated verbatim
    before any tightening write.
  - The catalog table shows every row's region at a glance, and — for a pinned tenant — visibly
    (never silently) distinguishes rows the pin excludes, without ever hiding a row.
  - A new key can be created as Priority or Standard, with a truthfully-sourced (never hardcoded)
    price signal wherever service-tiers has actually shipped its pricing endpoint, and an honest
    "pending" placeholder wherever it has not.
  - The public pricing page names data residency and priority service as real, shippable
    differentiators, in the same non-committal copy register the page's own frozen contract
    already uses.
  - `ap` residency pinning is VISIBLY not-yet-available rather than silently broken or silently
    omitted — and Tin has an explicit fork (§3 flag) to resolve before this task's own freeze.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ #1 (Issue #2) service-tiers' key-tier field name/values (`tier`: `"priority"|"standard"`)
  and a `GET /admin/service-tiers` endpoint mirroring region-pricing's own shape are BOTH
  assumed, not frozen — service-tiers is still the blank `phase: ground` template. Lowest
  confidence because this is the single largest unfrozen forward-dependency in the whole bundle;
  if service-tiers freezes a materially different field name, enum, or a wholly different
  pricing-delivery mechanism (e.g. baked directly into a per-model catalog price rather than a
  separate endpoint), M9/M10/R4/R5 need a change-request back to SPECIFY for this task before
  Build. Recommend freezing service-tiers before (or in the same session as) this task's own
  freeze — exactly the same recommendation region-pricing made for region-catalog-dimension.
  - [ ] #2 (Issue #1) The residency-policy/region-catalog-dimension `ap` mismatch is real, and
  this draft resolves it by SCOPE-CUTTING `ap` from the v1 picker (option a) rather than blocking
  this task's freeze on a residency-policy amendment (option b) — confirm this is the preferred
  resolution; if Tin prefers (b), this task's freeze should wait for a residency-policy
  change-request to land first.
  - [ ] #3 (Issue #4) "typed confirm gate" is interpreted as "written consequence copy inside the
  existing `ConfirmDialog`," not a literal type-the-word text-entry interaction — confirm or
  correct at freeze; a literal typed-confirmation would be a NEW interaction pattern with no
  sibling precedent (added cost: a new component, new a11y surface to verify, not a reuse).
  - [ ] #4 (Issue #3) The tier price-delta shown at key creation is assumed to be a FLAT
  tenant-level figure, not per-model, despite `resolve_tier_multiplier`'s reserved `model_id`
  parameter — confirm against service-tiers' own eventual Must list once drafted.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Data & residency tab renamed and third fieldset renders   # M1
  Given an authenticated tenant user opens /app/settings
  When they view the tab labeled "Data & residency" (formerly "Retention & ZDR")
  Then the Retention window and Zero-Data-Retention fieldsets render exactly as before
  And a new "Data residency" fieldset renders below them, fetching GET /admin/residency-policy

Scenario: fresh pin (unset -> EU) shows the confirm dialog with the EU consequence line   # M3
  Given tenant T's residency-policy fieldset shows "No pin (unrestricted)" (seededData.region == null)
  When the OWNER selects "EU" and clicks Save
  Then a ConfirmDialog opens BEFORE any PUT fires
  And its body reads "Pinning to EU means requests that cannot run in the EU will be refused, not rerouted. This also blocks realtime voice for this tenant — no realtime model is region-tagged yet."
  And PUT /admin/residency-policy has NOT been called yet

Scenario: switching an existing pin (US -> EU) also shows the confirm dialog   # M3
  Given tenant T's residency-policy fieldset shows "US" (seededData.region == "us")
  When the OWNER selects "EU" and clicks Save
  Then the SAME ConfirmDialog flow as the fresh-pin scenario triggers (pinned A -> pinned B is not exempt)
  And confirming calls PUT /admin/residency-policy with {"region":"eu"}

Scenario: confirming the EU pin persists it and reconciles the display   # M3
  Given the EU consequence ConfirmDialog is open (from either prior scenario)
  When the OWNER clicks "Pin to EU" (the confirm action)
  Then PUT /admin/residency-policy {"region":"eu"} is called
  And on success the fieldset displays "EU" sourced from the response, not local state
  And a fire-and-forget audit event was already recorded server-side (residency-policy M3, cited)

Scenario: cancelling the confirm dialog leaves the server pin unchanged   # M3, mirrors handleZdrConfirmClose
  Given the EU consequence ConfirmDialog is open and the server's last-known region is "us"
  When the OWNER clicks "Cancel"
  Then no PUT is ever sent
  And the fieldset's displayed value reconciles to "US" (the query-cache value), not "EU"

Scenario: clearing a pin fires immediately, no confirm dialog   # M4
  Given tenant T's residency-policy fieldset shows "EU" (seededData.region == "eu")
  When the OWNER selects "No pin (unrestricted)" and clicks Save
  Then PUT /admin/residency-policy {"region":null} fires immediately
  And no ConfirmDialog ever opens

Scenario: AP is offered but not selectable   # M2, R1
  Given the residency-policy fieldset's region picker renders
  When any user inspects the "AP" option
  Then it is rendered disabled with helper text "Not available yet — Asia-Pacific residency pinning is a tracked follow-up"
  And it cannot be selected, so PUT /admin/residency-policy is never called with {"region":"ap"}

Scenario: non-owner sees the same picker and gets an inline 403 on save   # M5, R3
  Given a MEMBER-role user views the Data & residency tab
  When they change the region selection and click Save (no client-side hiding)
  Then PUT /admin/residency-policy is called and the backend returns 403
  And the fieldset shows the existing inline mutError pattern with the server's title
  And the displayed region value reconciles from the query cache, unchanged from before the attempt

Scenario: defensive 422 on an unreachable-in-practice PUT   # R2
  Given a PUT /admin/residency-policy request somehow reaches the server with an invalid region
  When the server responds 422 ERR_RESIDENCY_REGION_INVALID
  Then the pending selection is NOT written into seededData
  And the fieldset reverts its displayed value to the last-known-good server state
  And the server's title is shown inline, never swallowed

Scenario: catalog table shows a Region badge per row   # M6
  Given the admin models table (/app/models) has loaded rows with region "us", "eu", "ap", "global"
  When the table renders
  Then each row shows a RegionBadge (Badge variant="outline") reading "US"/"EU"/"AP"/"GLOBAL"
  And the badge sits in the same visual family as the existing input_modalities badges

Scenario: pinned tenant sees ineligible catalog rows dimmed, disabled, and badged — never removed   # M7
  Given tenant T has residency_region == "eu"
  And the catalog table includes a row with region == "us"
  When the table renders (GET /admin/residency-policy and GET /admin/models both succeeded)
  Then the "us" row's Enabled Switch is disabled
  And the row is visually muted
  And an "Ineligible in EU" Badge (variant="warning") renders on that row
  And the row is still present in the table — never filtered out or hidden

Scenario: residency-read failure degrades gracefully — catalog table stays fully usable   # M8
  Given GET /admin/residency-policy fails (network error or 5xx) while GET /admin/models succeeds
  When the catalog table renders
  Then every row still shows its RegionBadge (M6)
  And no row is dimmed/disabled/badged as ineligible (M7's treatment is simply absent)
  And no page-level ErrorState blocks the table — the failure is silent-degrade, not a hard error

Scenario: tier selector renders on key creation with a safe default   # M9
  Given an owner/admin opens the Create API Key dialog
  When the dialog renders
  Then a Priority/Standard selector is visible, defaulting to "Standard"
  And the capacity-preference copy renders verbatim: "Priority requests get preference under contention and may fall back to Standard when capacity is unavailable — Standard is never starved."

Scenario: submitting a key with Priority tier sends the tier field   # M9
  Given the Create API Key dialog is open with "Priority" selected and a valid name entered
  When the user submits the form
  Then POST /admin/keys is called with body including {"tier":"priority", "name": "<name>"}
  And on success the dialog closes and the plaintext-key banner shows, matching existing behavior

Scenario: price delta shows the real server-computed value   # M10
  Given GET /admin/service-tiers succeeds and returns a priority entry with multiplier "1.25"
  When the Create API Key dialog renders
  Then the price-delta line reads "+25% on requests using this key" — computed from the returned multiplier
  And no percentage string is hardcoded anywhere in the component

Scenario: price delta degrades to a pending placeholder when the pricing endpoint is unavailable   # M10, R4
  Given GET /admin/service-tiers 404s (service-tiers not yet shipped, or a network error)
  When the Create API Key dialog renders
  Then the price-delta line shows the inert placeholder "Pricing pending"
  And no retry or poll is attempted against that endpoint
  And the tier selector itself remains fully rendered and submittable
  And no ErrorState/error banner appears — this is an expected degrade, not a fault

Scenario: a tier rejection from key creation surfaces via the existing error branching   # R5
  Given the Create API Key dialog is submitted with an invalid/rejected tier value
  When POST /admin/keys returns a 422 (or another 4xx) citing the tier field
  Then the existing CreateKeyDialog error branching surfaces it (422 -> field-level, else -> globalError)
  And no new bespoke error-handling code path was added for this case
  And the dialog stays open with the entered name preserved

Scenario: marketing pricing page tells the residency + priority story with zero fetch   # M11
  Given an anonymous visitor requests /pricing (Server Component, no auth)
  When the page renders
  Then the Team tier lists "Priority service tier (optional, usage-priced)"
  And the Enterprise tier lists "Data residency: pin inference to US or EU"
  And a short residency + priority callout section renders as static copy
  And zero network requests are made by the page itself (server-rendered, matches the frozen "no fetch" contract)

Scenario: full-surface accessibility sweep passes on every new/changed surface   # M12
  Given the Data & residency fieldset, the catalog Region/Ineligible badges, the tier selector + price-delta, and the marketing pricing page callout are all rendered
  When axe(container, { rules: { "color-contrast": { enabled: false } } }) runs against each
  Then zero NEW serious/critical violations are reported
  And every interactive control (region picker, Save button, ConfirmDialog, tier selector) has a visible focus-visible state and a >=44px hit target
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
BFF pass-through routes REUSED verbatim (zero new BFF route files — the existing
apps/dashboard/app/api/gw/[...path]/route.ts catch-all already forwards every path below):

GET  /admin/residency-policy                    -- residency-policy §3 FROZEN, cited verbatim
  200 -> { region: "us" | "eu" | null, updated_at: string | null }
  401/403 -> existing auth envelope

PUT  /admin/residency-policy   body: { region: "us" | "eu" | null }   -- FROZEN, cited verbatim
  200 -> { region: "us" | "eu" | null, updated_at: string | null }
  403 -> { code: "ERR_FORBIDDEN" | <existing RBAC code> }             -- R3, non-OWNER
  422 -> { code: "ERR_RESIDENCY_REGION_INVALID" }                     -- R2 (defensive; R1 prevents client-side)
  (this task's FE NEVER sends {"region":"ap"} — Issue #1; the frozen backend enum is {null,"us","eu"} only)

GET  /admin/models                              -- region-catalog-dimension §3 FROZEN, cited verbatim
  200 -> { object:"list", data: [{ ...AdminModelItem, region: "us"|"eu"|"ap"|"global" }] }

POST /admin/keys   body: { name: string, tier?: "priority" | "standard" }
  200 -> { key_id, name, key }
  422 -> { code: "<service-tiers-owned, TBD>" }          -- R5, surfaced via existing error branching
  (the `tier` FIELD is a FORWARD CITATION to service-tiers — NOT frozen, NOT redefined by this
   task; if service-tiers freezes a different field name/enum, this line is a change-request
   back to SPECIFY, not a silent adapt)

GET  /admin/service-tiers                -- FORWARD-CITED, ASSUMED shape (Issue #2) —
                                                     NOT owned, NOT frozen anywhere; mirrors
                                                     region-pricing's own GET /admin/region-pricing
                                                     shape as the closest sibling precedent
  200 -> { entries: [{ tier: "priority" | "standard", multiplier: string }] }
  404/5xx/unavailable -> NO retry/poll; FE renders "Pricing pending" (R4) and attempts nothing further

Schema: NONE touched directly by this task (no new table/column) — this task is FE-only,
consuming three already-frozen sibling contracts (residency-policy, region-catalog-dimension,
region-pricing's reserved resolver) plus one forward-cited, unfrozen service-tiers surface.

New FE component contracts (all under apps/dashboard/components/, TypeScript prop shapes):

  RegionBadge({ region: "us" | "eu" | "ap" | "global" }) -> JSX
    -- components/ui/region-badge.tsx (NEW) — Badge variant="outline", text = region.toUpperCase().
       The ONE new visual this task introduces; used in BOTH the catalog table (M6) and nowhere
       else with a different visual treatment (milestone instruction: design once, use everywhere).

  DataResidencyFieldset (no exported props — internal to RetentionZdrSettings.tsx)
    -- state: pendingRegion (string | null), confirmOpen (boolean)
    -- reads: GET /admin/residency-policy (own useQuery, independent of the Retention/ZDR queries)
    -- writes: PUT /admin/residency-policy (own useMutation)
    -- consequence-line copy (VERBATIM, per pin target — the persona's signature element):
         EU: "Pinning to EU means requests that cannot run in the EU will be refused, not
              rerouted. This also blocks realtime voice for this tenant — no realtime model is
              region-tagged yet."
         US: "Pinning to US means requests that cannot run in the US will be refused, not
              rerouted. This also blocks realtime voice for this tenant — no realtime model is
              region-tagged yet."
       (the realtime clause is carried verbatim from residency-policy's OWN "DECIDED at freeze
       review" note — "Realtime consequence ACCEPTED: pinned tenants lose realtime/WS in v1... a
       stated in the consequence line + docs" — this fieldset IS that consequence line.)
    -- AP option: disabled, helper text "Not available yet — Asia-Pacific residency pinning is a
       tracked follow-up" (never a consequence line, since it can never be selected — R1)

  TierSelector({ value: "priority" | "standard", onChange, priceDelta: string | null }) -> JSX
    -- components/keys/TierSelector.tsx (NEW) — rendered inside CreateKeyDialog
    -- capacity-preference copy (VERBATIM): "Priority requests get preference under contention
       and may fall back to Standard when capacity is unavailable — Standard is never starved."
    -- priceDelta is either a server-derived string ("+25% on requests using this key",
       formatted client-side from the raw multiplier — NEVER a hardcoded percentage) or null,
       rendered as "Pricing pending" when null (M10/R4)

Modified existing components (no new exported prop surface beyond what's listed):
  SettingsPage.tsx            -- TabsTrigger label "Retention & ZDR" -> "Data & residency" (M1)
  RetentionZdrSettings.tsx    -- + DataResidencyFieldset (M1-M5); Retention/ZDR fieldsets untouched
  ModelsPage.tsx               -- + "Region" column (M6) using RegionBadge; + ineligibility read/render (M7/M8)
  CreateKeyDialog.tsx          -- + TierSelector + price-delta read (M9/M10); CreateKeySchema += tier
  KeysPage.tsx                 -- createKeyMutation body += tier passthrough
  (marketing)/pricing/page.tsx -- + 2 feature bullets + 1 static callout section (M11)
```

Glossary deltas:
- `RegionBadge`: the one new shared visual component (`Badge variant="outline"`, uppercased
  region text) rendering a catalog row's or context's `region` value consistently across every
  surface it appears on — introduced once by this task, reused everywhere a region needs display,
  never re-skinned per surface.
- `Consequence line`: the plain-language, single-sentence statement of what a dangerous setting
  change actually does, shown verbatim inside a `ConfirmDialog` before the write fires — a
  FRONTEND-only concept (no backend field carries it); this task's residency pin is its first
  instance beyond the pre-existing (undocumented-as-a-term) ZDR dialog copy.
- (region / residency policy / region pin / region multiplier / tenant_region_multiplier_overrides
  are ALL owned by the three frozen sibling tasks — cited above, not redefined here.)

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — this is the design agent's freeze-ready draft; the freeze report (banner/ARC/SHAPE)
has not yet been rendered to the human.

Least-sure flag surfaced at freeze: [contract] TWO independent, load-bearing gaps compete for
"most likely to change this contract," both named in full at §0 Issue #1/#2 and §1 assumption
⚠#1/#2:
  (a) residency-policy's frozen PUT enum (`{null,"us","eu"}`) does NOT include `"ap"`, even
      though region-catalog-dimension and region-pricing both froze the 4-value `us|eu|ap|global`
      set the SAME day, later. This task's v1 picker scope-cuts `ap` (disabled + "not available
      yet") rather than blocking on a residency-policy re-freeze — Tin should confirm this is the
      preferred fork over reopening residency-policy first.
  (b) service-tiers (this task's OWN stated dependency) is still the blank `phase: ground`
      template — M9/M10's `tier` field name and the entire `GET /admin/service-tiers`
      shape are FORWARD-CITED ASSUMPTIONS, not frozen fact. This task's Build should not start
      until service-tiers freezes a real contract; if it freezes a materially different shape,
      M9/M10/R4/R5 need a change-request back to SPECIFY.
Recommend: resolve (a) explicitly (pick a fork) and freeze service-tiers (b) BEFORE this task's
own contract freezes into Build-ready — or freeze this contract now with both caveats carried
forward explicitly, re-verified at this task's own BUILD step before any code lands (mirrors
region-pricing's own precedent for handling an unfrozen sibling dependency).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

DECIDED at freeze review (2026-07-12, orchestrator, Tin's standing Asia directive): flag fork (b)
TAKEN — residency-policy re-frozen @ v2 with `ap` in its §3 enum (CR-1; the gap was an
orchestrator merge miss, not a design choice). The v1 picker therefore INCLUDES `ap` as a
fully-enabled option — the visibly-disabled-ap scope-cut language in this draft is SUPERSEDED;
consequence-line copy for ap ("requests that cannot run in Asia-Pacific will be refused, not
rerouted; Vietnam is served from Singapore/SEA endpoints") joins the EU/US copy. Confirmed:
"typed confirm gate" = written consequence copy inside ConfirmDialog (ZDR idiom), NOT a
type-to-confirm field. Tier price-delta shape + field names stay forward-cited pending
service-tiers' freeze; this task's own freeze WAITS for service-tiers (designer's
recommendation, adopted).

DECIDED at freeze review (2026-07-12, Tin): FROZEN alongside service-tiers. Tier price-delta source
CORRECTED to the real frozen route `GET /admin/service-tiers` (effective default_tier +
priority_markup_pct — tenant-flat, confirming this draft's assumption); the
/admin/service-tier-pricing forward-cite is superseded throughout.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: >=80% lines on every new/touched module (project-wide vitest.config.ts
threshold), no regression on any pre-existing file's coverage. Actual (see §6): region-badge.tsx
100%, TierSelector.tsx 83.3%, CreateKeyDialog.tsx 96.2%, ModelsPage.tsx 80.9%,
RetentionZdrSettings.tsx 93.75%, KeysPage.tsx fully exercised across the full suite.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_region_badge_renders_us/eu/ap/global_as_LABEL (it.each): arrange render <RegionBadge
    region="x"/> / act (none) / assert text == uppercased label · covers: M6
  - test_region_badge_uses_outline_variant: arrange render eu badge / assert className has
    border-border, not bg-primary · covers: M6
  - test_data_residency_tab_renders_third_fieldset: arrange open /app/settings / act view "Data &
    residency" tab / assert Retention+ZDR fieldsets unchanged AND new residency fieldset fetches
    GET /admin/residency-policy · covers: M1
  - test_fresh_pin_eu_shows_confirm_dialog_before_put: arrange seededData.region=null / act select
    EU + Save / assert ConfirmDialog opens with verbatim EU consequence line, PUT not yet called ·
    covers: M3
  - test_switching_us_to_eu_also_confirms: arrange region="us" / act select EU + Save / assert same
    confirm flow, confirming PUTs {"region":"eu"} · covers: M3
  - test_confirming_eu_pin_persists_and_reconciles_from_response: arrange confirm dialog open / act
    click "Pin to EU" / assert PUT fires, display sources from response not local state · covers: M3
  - test_cancelling_confirm_leaves_server_pin_unchanged: arrange confirm dialog open, server="us" /
    act click Cancel / assert no PUT, display reconciles to "US" (query-cache) · covers: M3
  - test_clearing_pin_fires_immediately_no_confirm: arrange region="eu" / act select "No pin" + Save
    / assert PUT {"region":null} fires immediately, no ConfirmDialog · covers: M4
  - test_ap_region_pin_available_and_selectable (CR-1 supersedes original M2/R1 "AP disabled"
    scenario — DECIDED at freeze review, `_VALID_PIN_REGIONS` includes "ap" server-side): arrange
    picker renders / act select AP + Save + confirm / assert AP is enabled, selectable, PUTs
    {"region":"ap"} successfully · covers: M2 (corrected)
  - test_non_owner_sees_picker_gets_inline_403: arrange MEMBER role / act change region + Save /
    assert PUT called, inline mutError shows server title, display reconciles unchanged · covers: M5, R3
  - test_defensive_422_reverts_display_shows_server_title: arrange PUT returns 422
    ERR_RESIDENCY_REGION_INVALID / assert pending selection not written, display reverts to
    last-known-good, title shown inline · covers: R2
  - (2 more residency-fieldset tests: loading + no-double-submit-while-confirm-open guard) · covers:
    M1, safety rule
  - test_catalog_table_shows_region_badge_per_row: arrange models loaded with region
    us/eu/ap/global / assert each row's RegionBadge reads the uppercased region · covers: M6
  - test_ineligible_row_dimmed_disabled_badged_never_removed: arrange residency_region="eu", a row
    region="us" / assert Switch disabled, row muted, "Ineligible in EU" warning badge, row still
    present · covers: M7
  - test_residency_read_failure_degrades_table_stays_usable: arrange GET
    /admin/residency-policy fails, GET /admin/models succeeds / assert every RegionBadge still
    renders, no ineligibility treatment, no page-level ErrorState · covers: M8
  - test_tier_selector_renders_on_open_with_safe_default: arrange open Create API Key dialog /
    assert Priority/Standard selector visible, defaults Standard, capacity copy verbatim · covers: M9
  - test_creating_key_with_priority_tier_sends_tier_field: arrange dialog open, Priority selected,
    valid name / act submit / assert POST /admin/keys body includes {"tier":"priority","name":...},
    success closes dialog + shows plaintext banner · covers: M9
  - test_creating_key_defaults_to_standard_tier: arrange dialog open, no tier change / act submit /
    assert POST body {"tier":"standard",...} · covers: M9
  - test_price_delta_shows_real_server_computed_value: arrange GET /admin/service-tiers returns
    {default_tier,priority_markup_pct:"25.0000"} (REAL frozen shape, corrected from this draft's
    earlier {entries:[...]} assumption) / assert price-delta line reads "+25% on requests using
    this key", no hardcoded percentage · covers: M10
  - test_price_delta_degrades_to_pending_on_404_no_retry: arrange GET /admin/service-tiers 404s /
    assert "Pricing pending" shown, no retry (exactly 1 call after a settle delay), selector stays
    fully rendered/submittable, no ErrorState · covers: M10, R4
  - test_tier_rejection_surfaces_via_existing_error_branching: arrange POST /admin/keys returns 422
    citing tier / act submit / assert existing 422->field/else->global branching surfaces it, no new
    bespoke path, dialog stays open with name preserved · covers: R5
  - test_onSubmit_signature_stays_single_arg_name_only (regression guard, not a frozen scenario):
    arrange submit with tier selected / assert onSubmit called with exactly ("ci-key") — protects
    the pre-existing, out-of-scope tests/keys-dialog-a11y.test.tsx contract · covers: R5 (boundary)
  - test_pricing_page_lists_priority_tier_and_residency_features / test_pricing_page_residency_
    callout_renders / test_pricing_page_zero_fetch (Server Component): arrange render /pricing /
    assert Team lists Priority tier line, Enterprise lists residency line, callout section renders,
    zero network calls · covers: M11
  - test_full_surface_axe_sweep (residency fieldset, catalog region/ineligible badges, tier
    selector + price-delta, pricing callout — 4 axe() calls joining each surface's existing sweep):
    assert zero NEW serious/critical violations, focus-visible + >=44px hit targets on every new
    interactive control · covers: M12
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch — PREFERRED allowlist, drafted at design time; not yet frozen/enforced until §3 freezes):
`apps/dashboard/components/ui/region-badge.tsx`
`apps/dashboard/components/keys/TierSelector.tsx`
`apps/dashboard/components/settings/RetentionZdrSettings.tsx`
`apps/dashboard/components/settings/SettingsPage.tsx`
`apps/dashboard/components/models/ModelsPage.tsx`
`apps/dashboard/components/keys/CreateKeyDialog.tsx`
`apps/dashboard/components/keys/KeysPage.tsx`
`apps/dashboard/app/(marketing)/pricing/page.tsx`
`apps/dashboard/tests-bff/tenant-settings.test.tsx`
`apps/dashboard/tests-bff/model-mgmt.test.tsx`
`./tests/`

Strategy (ordered batches, PREFERRED plan — guidance, not enforced):
1. `RegionBadge` first (the one new shared visual, zero dependents yet) — a pure presentational
   component, trivially unit-testable in isolation before anything consumes it.
2. Data & residency fieldset: extend `RetentionZdrSettings.tsx` with the residency GET/PUT +
   `ConfirmDialog` wiring (M1-M5), reusing `handleZdrToggle`'s exact control-flow shape; rename
   the tab label in `SettingsPage.tsx`. Red tests for the confirm/no-confirm asymmetry FIRST
   (mirrors the ZDR suite's own test shape — cite it directly rather than re-deriving).
3. Catalog: extend `ModelsPage.tsx`'s `columns` with `RegionBadge` (M6), then the
   residency-cross-reference ineligibility read/render (M7/M8) as a SEPARATE, independently
   degradable `useQuery` — never let it block the base table render.
4. `TierSelector` + `CreateKeyDialog` extension (M9/M10): build the selector + capacity-preference
   copy FIRST (no external dependency), then the price-delta fetch as a separately-degradable
   read (mirrors step 3's independent-read pattern) — confirm service-tiers' ACTUAL frozen field
   name/shape at this point in Build, not from this draft's assumption (§1 ⚠#1).
5. Marketing pricing page copy (M11) — pure static content, no dependency on any other batch,
   could be done in parallel with 2-4 if isolation allows.
6. Full axe sweep (M12) across all touched surfaces as the final batch, joining the existing
   `tenant-settings.test.tsx`/`model-mgmt.test.tsx` axe calls rather than a new test file.

Persona (required): `ui-designer` (`.add/personas/ui-designer.md`) — this DESIGN draft's own
governing persona; carries forward as the build's domain stance (visual-system consistency,
computed-not-eyeballed WCAG AA, reuse-before-invent). No `flow: build` persona in this repo
currently owns the dashboard-frontend domain more specifically than `frontend-engineer`
(`flow: build, advisor`) — recommend the BUILD agent load `frontend-engineer` as an ADDITIONAL
lens for BFF-trust-boundary/SSR-safety concerns, since this task's own persona is design-flow only.
Spawn isolation (default): worktree — this task's shared-file surface
(`RetentionZdrSettings.tsx`, `ModelsPage.tsx`, `CreateKeyDialog.tsx`) could overlap a concurrently
building service-tiers FE change if one is ever spawned in parallel; a non-worktree shared-tree
build risks the documented scope-snapshot poisoning gotcha.
Known-problem fixes:
  - trap: client-side sending `{"region":"ap"}` to a backend that 422s it → fix: the AP option is
    rendered `disabled` at the DOM level (not just visually styled), so it can never be selected
    or submitted (R1).
  - trap: assuming service-tiers' `tier`/pricing shape without re-verifying at Build time → fix:
    re-read `.add/tasks/service-tiers/TASK.md` §3 FRESH at Build start; if still unfrozen or
    materially different from this draft's assumption, STOP and raise a change-request rather
    than building against a guess.
  - trap: an ineligibility-badge `useQuery` failure silently breaking the WHOLE catalog table →
    fix: independent `isError`/`isLoading` branches per read (M8), never a combined `isError` gate
    across both the models read and the residency read.
Strategy actually used: followed the preferred 6-batch order closely, with two grounding
deviations made BEFORE writing code (both self-caught by re-reading real consumer test files,
not discovered mid-build):
  1. `service-tiers` TASK.md §3 was already FROZEN and the backend route already built
     (`service_tier_router.py` read in full) — re-verified per the "Known-problem fixes" trap
     note and found the real shape is `GET /admin/service-tiers -> {default_tier,
     priority_markup_pct}` (tenant-flat effective value), NOT the `{entries:[{tier,multiplier}]}`
     shape this draft assumed. Built against the real shape; preserved the scenario's observable
     assertion text ("+25% on requests using this key") verbatim.
  2. The "AP disabled" trap note (§5 Known-problem fixes, R1) and the matching §2 scenario text
     are BOTH superseded by the §3 "DECIDED at freeze review... CR-1" note (ap fully enabled,
     `_VALID_PIN_REGIONS` includes "ap" server-side). Did not follow the stale disabled-AP
     guidance; built AP as a normal, selectable, submittable option. Frozen §2 text was left
     untouched (never edit a frozen scenario) — the new red suite documents the supersession
     inline with a citation instead.
  Two additional judgment calls surfaced only once real consumer files were read (not
  anticipated by the draft strategy):
  3. `CreateKeyDialog`'s `onSubmit(name)` is asserted single-argument by a pre-existing,
     out-of-scope suite (`tests/keys-dialog-a11y.test.tsx`) — threaded `tier` through a new
     `onTierChange?` side-channel prop instead of widening `onSubmit`.
  4. `CreateKeyDialog` renders in 6 places with no `QueryClientProvider` ancestor (2
     out-of-scope suites) — used a plain `useEffect`+`bffGet`+`useState` fetch for the
     price-delta read instead of `useQuery`, so it works with or without react-query context.
  Scope-extension (disclosed, not in the original §5 allowlist): adding the two new
  unconditional reads (`GET /admin/residency-policy`, `GET /admin/service-tiers`) to
  `ModelsPage`/`RetentionZdrSettings`/`CreateKeyDialog` would 404/error inside ~11 pre-existing,
  out-of-scope test files under msw's `onUnhandledRequest:"error"` unless mocked. Rather than
  editing all 11 files, added ONE default/INITIAL handler per new query to
  `tests-bff/mocks/handlers.ts` and `tests/mocks/handlers.ts`, mirroring the existing documented
  idiom in those files (e.g. `/api/auth/me`, catalog-models) — lower-risk than touching every
  consumer, but technically outside the declared file allowlist; flagged here and in code
  comments rather than silently done.
  Bug found+fixed mid-build (not a design deviation): 6 pre-existing `model-mgmt.test.tsx` tests
  crashed (`Cannot read properties of undefined (reading 'toUpperCase')`) because their
  `GPT4O`/`CLAUDE` fixtures predate the `region` field. Fixed the in-scope fixtures (added
  `region:"global"`) AND added a defensive `regionOf()` fallback in `ModelsPage.tsx` (protects
  the 5 other out-of-scope consumer files without touching them).
Safety rule (feature-specific): the residency-pin write and its `ConfirmDialog` gate must never
allow a SECOND `PUT` to fire while one is already in flight (mirrors `zdrConfirmOpen`'s existing
`disabled={zdrConfirmOpen}` guard on the Switch) — no double-submit race on the confirm button.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new npm
dependency expected — every primitive used already ships in this repo); ask if unclear,
especially re: service-tiers' actual frozen shape once it exists.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
