# TASK: Tenant Overview Strip

slug: tenant-overview-strip · created: 2026-07-06 · stage: production
milestone: platform-console-flat-redesign
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `components/platform/PlatformTenantDetail.tsx:PlatformTenantDetail` — gates its own
    `["platform-tenant", tenantId]` query (isLoading/isError early-return) before rendering
    `PlatformSafetyBanner` + `PageHeader` + `Tabs` (config·budget·keys·members·plan), all inside
    one `<div className="flex flex-col gap-6">`. The Strip mounts as a NEW sibling between
    `PageHeader` and `Tabs` in that same div — visible regardless of `activeTab`, never inside a
    `TabsContent`.
  - `apps/dashboard/tests/platform-tenant-detail.test.tsx:test_detail_shell_dom_order_banner_then_header_then_tabs`
    — an EXISTING frozen structural test asserting `banner.compareDocumentPosition(heading)` and
    `heading.compareDocumentPosition(tablist)` both carry `DOCUMENT_POSITION_FOLLOWING`.
    `compareDocumentPosition` checks relative document order, not immediate adjacency, so
    inserting the Strip between `heading` and `tablist` keeps this test green by construction —
    verified directly (not assumed), resolving what would otherwise be a real
    frozen-structural-contract collision risk (see §1 M1, §1 Assumptions).
  - `components/platform/PlatformPlanTab.tsx:PlatformPlanTab` — owns `planQueryKey =
    React.useMemo(() => ["platform-tenant-plan", tenantId], [tenantId])` ->
    `bffGet<TenantPlanResponse>(\`/admin/platform/tenants/${tenantId}/plan\`)`; local (NOT
    exported) `TenantPlanResponse` interface (see Issues/Risks #1).
  - `components/platform/PlatformMembersTab.tsx:PlatformMembersTab` — owns `queryKey =
    React.useMemo(() => ["platform-tenant-users", tenantId], [tenantId])` ->
    `bffGet<UsersListResponse>(\`/admin/platform/tenants/${tenantId}/users\`)`; `users =
    usersQuery.data?.users ?? []`. Local (NOT exported) `UsersListResponse { users:
    PlatformTenantUser[] }`; `PlatformTenantUser` itself IS already exported.
  - `components/platform/PlatformKeysTab.tsx:PlatformKeysTab` — owns `queryKey =
    React.useMemo(() => ["platform-tenant-keys", tenantId], [tenantId])` ->
    `bffGet<PlatformApiKey[]>(\`/admin/platform/tenants/${tenantId}/keys\`)` — a raw array
    response, not wrapped in an object. Local (NOT exported) `PlatformApiKey { key_id, name,
    prefix, created_at, revoked_at: string | null }`.
  - `components/platform/PlatformBudgetTab.tsx:PlatformBudgetTab` — owns `queryKey =
    ["platform-tenant-budget", tenantId]` (a plain literal, NOT `React.useMemo`'d unlike its 3
    siblings — harmless, React Query keys compare by serialized value, see Issues/Risks #4) ->
    `bffGet<BudgetData>(\`/admin/platform/tenants/${tenantId}/budget\`)`. Local (NOT exported)
    `BudgetData { budget_usd_monthly: string | null; spent_usd_month: string }`.
  - `components/ui/stat-card.tsx:StatCard` — accepts `{label, value, delta?, icon?, footer?,
    className?, valueTestId?, variant?}`; `variant` (additive, shipped by
    `console-flat-visual-pass`) passes straight through to its internal `Card`.
  - `components/ui/card.tsx:Card` — `variant="flat"` is already SHIPPED and consumed live today
    (confirmed: `PlatformTenantDirectory.tsx` line 147 already renders `<Card variant="flat">`)
    — no dependency risk, available now.
  - `components/ui/states.tsx:Loading,ErrorState` — `Loading` is a compact `role="status"
    aria-busy` icon+text row (already used inline per-tab, not only full-page); `ErrorState` is
    a `role="alert"` bordered block (icon+title+optional description+Retry `<button>`) — both
    reusable at tile granularity, no new primitive needed (see Issues/Risks #3).
  - Backend (read-only citation; not touched — Tier 1 is frontend-only):
    `apps/gateway/src/gateway/tenants/api/platform_plans_router.py:TenantPlanResponse` (Pydantic:
    `tenant_id: UUID, plan: PlanResponse | None, seat_cap: int | None` — confirms the frontend
    shape byte-for-byte) and `platform_users_router.py:list_platform_tenant_users` (confirmed
    UNPAGINATED — returns the tenant's "complete user/member roster" — so `users.length` is
    already the correct total; no cheaper count-only endpoint exists or is needed).
Context (working folder):
  - `.add/milestones/platform-console-flat-redesign/MILESTONE.md` — Scope/Shared-risky-contracts/
    4-task breakdown (already confirmed; this task owns the cache-key-reuse risk named there).
  - `.add/design/DESIGN.md`'s `platform-console-flat-redesign` row — ux-researcher's persona-dive
    verdict ("Overview Strip eager-fetch as 4 independent queries") plus the milestone-level
    LAYOUT bullet describing the Strip; no DEDICATED task-level design-intake sub-bullet exists
    yet for `tenant-overview-strip` the way `console-flat-visual-pass` got its own 7-round hi-fi
    mockup (see Issues/Risks #2).
  - `.add/tasks/console-flat-visual-pass/TASK.md` — formatting/precision template; its own §5
    Scope list does NOT include `PlatformTenantDetail.tsx` — independently reconfirmed via `git
    log`/`git diff 006f791..HEAD` on that file (zero lines changed since that sibling task's own
    Ground SHA), so the Strip is additively touching a file no sibling task has moved yet.
  - `.add/GLOSSARY.md` — no existing UI-region term; this task introduces no domain-vocabulary
    gap (see Related intent).
  - `.add/CONVENTIONS.md` (frontend v13-folded lessons) — RTL assertions scoped
    `within(<section>)`; pure-props components preferred for state-testability (render with
    isLoading/isError/data as props rather than each internal piece re-deriving its own hook).
Honors (patterns / conventions):
  - `card.tsx`'s "ADDITIVE ONLY" `variant` contract (already shipped) — the Strip is a pure
    CONSUMER of `variant="flat"`; it introduces no new Card variant.
  - MILESTONE.md's "Shared / risky contracts" line naming this task as owner of the cache-key
    reuse risk — resolved here by direct verification against the real source (see Touches).
  - The pervasive `platform/*.tsx` cross-reference discipline ("mirrors X's own shipped Y
    convention exactly") — the new Strip file's own header should name which tab each tile's
    query mirrors, matching every sibling file's own convention.
  - CONVENTIONS.md's pure-props-component testability preference (v13 folded lesson) — the
    Strip's per-tile rendering should be a small pure-props sub-piece driven by the parent's own
    4 query results, not 4 independently-hook-calling grandchildren.
Anchors the contract cites:
  - `components/platform/PlatformTenantDetail.tsx:PlatformTenantDetail` (mount site)
  - NEW `components/platform/PlatformTenantOverviewStrip.tsx:PlatformTenantOverviewStrip`
  - `components/platform/PlatformPlanTab.tsx:TenantPlanResponse` (gains `export`)
  - `components/platform/PlatformMembersTab.tsx:UsersListResponse` (gains `export`),
    `PlatformTenantUser` (already exported, unchanged)
  - `components/platform/PlatformKeysTab.tsx:PlatformApiKey` (gains `export`)
  - `components/platform/PlatformBudgetTab.tsx:BudgetData` (gains `export`)
  - `components/ui/stat-card.tsx:StatCard`, `components/ui/card.tsx:Card` (`variant="flat"`),
    `components/ui/states.tsx:Loading,ErrorState` (all reused verbatim, zero edits)
Issues/Risks (→ feed §1):
  1. All 4 response interfaces the Strip needs (`TenantPlanResponse`, `UsersListResponse`,
     `PlatformApiKey`, `BudgetData`) are declared LOCALLY (no `export`) in their tab files.
     Resolution (proceeding as project lead, low-risk/reversible, mirrors the sibling task's own
     StatCard-variant-passthrough precedent): add the `export` keyword at each existing
     declaration — 4 one-word diffs, zero runtime effect (types erase at compile time), every
     existing consumer in those files stays byte-identical. Rejected alternative: the Strip
     re-declaring its own local duplicate shapes — cheaper to write but reintroduces exactly the
     drift risk MILESTONE.md's own shared-contract warning is about.
  2. No dedicated visual capture/mockup exists for this task specifically — unlike
     `console-flat-visual-pass`'s own 7-round Artifact process, DESIGN.md has no task-level
     design-intake sub-bullet for `tenant-overview-strip` (only the milestone-level LAYOUT-axis
     bullet). Named honestly, not silently skipped: residual risk is judged low because every
     visual element this task uses (`StatCard`, `Card variant="flat"`, `Loading`, `ErrorState`)
     is already shipped and AA-vetted — this is a layout-composition question (does a 4-up tile
     row read well above the Tabs), not a new-token-safety one.
  3. `components/ui/states.tsx`'s `ErrorState` is a bordered/padded block with an icon+title+
     Retry button — reusable at tile granularity but visually heavier than a bare `StatCard`;
     using it 1:1 per failed tile is the zero-new-component choice but may read as heavy in a
     4-up small-tile row. Named for transparency (see §1 Assumptions), not silently decided.
  4. `PlatformBudgetTab`'s own `queryKey` is a plain array literal (not `React.useMemo`'d, unlike
     its 3 siblings) — harmless for cache correctness, but noted so a future reader doesn't
     mistake this pre-existing memo inconsistency for a cache-key mismatch introduced here.
  5. The Strip mounts INSIDE `PlatformTenantDetail`'s existing `isLoading`/`isError` gate on the
     shell's OWN `["platform-tenant", tenantId]` query — its 4 queries start firing once that
     shell load completes, not literally at first paint, but still before any tab is clicked
     (still "eager" relative to every tab). Named so "always visible" isn't misread as "fetches
     before the tenant shell itself resolves."
Related intent: MILESTONE.md's Scope ("a Tenant Overview Strip... 4 independently-loading queries
  reusing the tabs' own existing React Query cache keys") + its Shared/risky-contracts line
  naming this task as owner of the cache-key-reuse risk + DESIGN.md's ux-researcher verdict
  ("Overview Strip eager-fetch as 4 independent queries"). No new GLOSSARY term — "Overview
  Strip" is a UI-region name, not a domain concept (mirrors `console-flat-visual-pass`'s own "no
  new Glossary term" call for a similarly UI-only change).
Ground SHA: `37e55ee` (2026-07-06) — cite symbols above, not bare line numbers; any line ref
  elsewhere is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant Overview Strip — a small, always-visible summary region at the top of a tenant-
  detail page (above the Tabs, visible regardless of active tab) showing plan+seat-cap,
  seats-used, active-keys, and budget, each field loading and failing independently by reusing
  the 4 tabs' own existing React Query cache keys — no new backend endpoint.
Framings weighed: one Strip component owning 4 independent `useQuery` calls, one per field, each
  reusing a sibling tab's exact existing cache key + queryFn (chosen — matches ux-researcher's
  explicit verdict + MILESTONE.md's "no new backend contract" framing, zero new network surface)
  · a single new gated/combined backend endpoint returning all 4 fields in one response (rejected
  — MILESTONE.md's own Scope frames this as Tier 1 "frontend-only, NO new backend contract," and
  directly contradicts ux-researcher's traced verdict "4 independent queries, not 1 gated
  bundle") · extracting each tab's inline `useQuery` into a shared custom hook called by both the
  tab and the Strip (rejected for THIS task — touches 4 already-shipped/tested tab files beyond
  an additive `export` keyword, a bigger blast radius than this Tier-1 task's risk budget; a
  reasonable FUTURE refactor, not this task's job).
Must:
<must>
  - M1: New `PlatformTenantOverviewStrip` component
    (`components/platform/PlatformTenantOverviewStrip.tsx`) mounts inside
    `PlatformTenantDetail`'s render as a sibling between `<PageHeader/>` and `<Tabs>` — visible
    regardless of `activeTab`, never inside a `TabsContent`. The existing frozen
    `test_detail_shell_dom_order_banner_then_header_then_tabs` (banner -> heading -> tablist via
    `compareDocumentPosition`) stays green unmodified — verified: that assertion checks relative
    order, not immediate adjacency, so heading still precedes tablist with the Strip between them.
  - M2: Renders exactly 4 tiles — Plan+seat-cap, Seats-used, Active keys, Budget — each owning
    its OWN `useQuery`; one tile's loading or error state never blanks, blocks, or delays the
    other 3.
  - M3: Each tile's query is BYTE-IDENTICAL (key array + URL) to its sibling tab's own:
      - Plan+seat-cap: `["platform-tenant-plan", tenantId]` -> `GET
        /admin/platform/tenants/{tenantId}/plan` (matches `PlatformPlanTab`'s `planQueryKey`)
      - Seats-used: `["platform-tenant-users", tenantId]` -> `GET
        /admin/platform/tenants/{tenantId}/users`; displayed value = `users.length` (matches
        `PlatformMembersTab`)
      - Active keys: `["platform-tenant-keys", tenantId]` -> `GET
        /admin/platform/tenants/{tenantId}/keys`; displayed value = count where `revoked_at ===
        null` (matches `PlatformKeysTab`)
      - Budget: `["platform-tenant-budget", tenantId]` -> `GET
        /admin/platform/tenants/{tenantId}/budget` (matches `PlatformBudgetTab`)
  - M4: `TenantPlanResponse` (PlatformPlanTab.tsx), `UsersListResponse` (PlatformMembersTab.tsx),
    `PlatformApiKey` (PlatformKeysTab.tsx), and `BudgetData` (PlatformBudgetTab.tsx) each gain an
    additive `export` keyword at their existing declaration — the Strip imports these exact
    types rather than re-declaring local copies; every existing consumer in those 4 files stays
    byte-identical (type-only change, zero runtime effect).
  - M5: Each tile renders via existing primitives only — `StatCard` (`variant="flat"`) once
    loaded, a per-tile `Loading` while pending, a per-tile `ErrorState` (its own `onRetry` bound
    to only that tile's own `refetch()`) on failure — no new display primitive introduced.
  - M6: The Strip's wrapping element is `Card variant="flat"` (or an equivalent flat/borderless
    grid container), consistent with `console-flat-visual-pass`'s shipped token treatment — no
    Aurora rounded/shadowed styling anywhere in the Strip.
  - M7: The Strip region carries an accessible label (e.g. `aria-label="Tenant overview"`)
    distinguishing it as a landmark separate from the Tabs region below it — WCAG 2.2 AA: zero
    new color tokens (100% reuse of already-AA-vetted `StatCard`/`Loading`/`ErrorState`), keyboard
    operability preserved via each tile's native Retry `<button>`.
  - M8: `PlatformPlanTab.tsx`, `PlatformMembersTab.tsx`, `PlatformKeysTab.tsx`, and
    `PlatformBudgetTab.tsx` receive ONLY the M4 export-keyword addition — every other line, every
    existing test, stays byte-identical.
</must>
Reject:
<reject>
  - a single new combined/gated backend endpoint bundling all 4 fields into one response ->
    "not in scope" (MILESTONE.md's own "no new backend contract" framing + ux-researcher's
    traced verdict)
  - a Strip-local query key that differs even slightly from its sibling tab's key (e.g.
    `["overview-plan", tenantId]`) -> rejected, causes double-fetching + cache incoherence — the
    exact risk MILESTONE.md names this task as owning
  - a Strip-local duplicate re-declaration of `TenantPlanResponse`/`UsersListResponse`/
    `PlatformApiKey`/`BudgetData` instead of importing the exported original -> rejected in favor
    of M4 (avoids silent shape drift)
  - the Tier-3 directory kind/plan filter, bulk actions, or any other new IA beyond the Strip
    itself -> "not in scope" (MILESTONE.md Out)
  - any behavioral change to `PlatformPlanTab`/`PlatformMembersTab`/`PlatformKeysTab`/
    `PlatformBudgetTab` beyond the M4 export keyword -> "not in scope" — a change request against
    the frozen contract if raised after freeze
</reject>
After:
<after>
  - a superadmin opening any tenant's detail page sees plan+seat-cap, seats-used, active-keys,
    and budget at a glance, above the Tabs, regardless of which tab is active
  - each of the 4 fields loads and fails independently — a test asserts one tile's simulated
    failure leaves the other 3 tiles' data/loading state fully intact
  - no new network endpoint exists — a test/diff confirms the 4 query keys+URLs the Strip uses
    are byte-identical to their sibling tab's own
  - `PlatformPlanTab`/`PlatformMembersTab`/`PlatformKeysTab`/`PlatformBudgetTab` are otherwise
    byte-identical — a diff shows only the 4 `export` keyword additions, zero other line changes
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ M4's export-4-interfaces call — lowest confidence because it's a Ground-time engineering
    judgment made unilaterally here (DESIGN.md/MILESTONE.md name the CACHE-KEY reuse risk
    explicitly but say nothing about the TS-type-reuse question), touching 4 already-shipped/
    tested files with a real (if tiny) diff; if wrong: revert to 4 local un-exported interfaces
    declared inside the new Strip file only — zero tab-file edits, fully contained, no cascading
    change.
  - [ ] no dedicated visual capture/mockup exists for this task (unlike
    `console-flat-visual-pass`'s 7-round Artifact) — confirm whether Tin wants a quick concept
    render before this freeze, or accepts the low visual risk given 100% of the Strip's visual
    vocabulary is already shipped and AA-vetted; if wrong: insert one visual-gate round before
    Build, no contract rework needed.
  - [ ] `ErrorState`'s existing bordered/padded/Retry-button block, reused verbatim at
    1-of-4-tile size, may read visually heavy next to a bare `StatCard` tile — confirm or accept;
    if wrong: swap to a lighter tile-local error treatment, a contained follow-up touching only
    the new Strip file.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Overview Strip mounts above the Tabs, independent of active tab   # M1
  Given a superadmin opens a tenant's detail page
  When PlatformTenantDetail renders
  Then PlatformTenantOverviewStrip renders as a sibling between PageHeader and Tabs, and heading.compareDocumentPosition(strip) and strip.compareDocumentPosition(tablist) both carry DOCUMENT_POSITION_FOLLOWING
  And the existing test_detail_shell_dom_order_banner_then_header_then_tabs assertions (banner precedes heading, heading precedes tablist) still hold, and the Strip remains visible and unchanged when activeTab switches between config/budget/keys/members/plan

Scenario: One tile's failure never blanks the other three   # M2
  Given the Overview Strip is rendering its 4 tiles
  When the Active-keys query fails while the other 3 queries succeed
  Then the Active-keys tile shows its own error state
  And the Plan, Seats-used, and Budget tiles still render their own loaded data unaffected

Scenario: Plan+seat-cap tile reuses PlatformPlanTab's exact query   # M3
  Given the Overview Strip mounts for a tenant with id tenantId
  When its Plan+seat-cap tile fetches
  Then it uses queryKey ["platform-tenant-plan", tenantId] against GET /admin/platform/tenants/{tenantId}/plan
  And this is the identical key PlatformPlanTab's own planQueryKey uses, so an already-cached Plan tab fetch is never repeated

Scenario: Seats-used tile reuses PlatformMembersTab's exact query   # M3
  Given the Overview Strip mounts for a tenant with id tenantId
  When its Seats-used tile fetches
  Then it uses queryKey ["platform-tenant-users", tenantId] against GET /admin/platform/tenants/{tenantId}/users
  And it displays users.length, the identical key and shape PlatformMembersTab already uses

Scenario: Active-keys tile reuses PlatformKeysTab's exact query   # M3
  Given the Overview Strip mounts for a tenant with id tenantId
  When its Active-keys tile fetches
  Then it uses queryKey ["platform-tenant-keys", tenantId] against GET /admin/platform/tenants/{tenantId}/keys
  And it displays the count of keys where revoked_at is null, the identical key PlatformKeysTab already uses

Scenario: Budget tile reuses PlatformBudgetTab's exact query   # M3
  Given the Overview Strip mounts for a tenant with id tenantId
  When its Budget tile fetches
  Then it uses queryKey ["platform-tenant-budget", tenantId] against GET /admin/platform/tenants/{tenantId}/budget
  And this is the identical key PlatformBudgetTab already uses

Scenario: The 4 response types are exported for reuse, not duplicated   # M4
  Given TenantPlanResponse, UsersListResponse, PlatformApiKey, and BudgetData are each declared locally in their own tab file
  When this task's build runs
  Then each interface gains an additive export keyword at its existing declaration
  And PlatformTenantOverviewStrip imports all 4 rather than re-declaring local copies

Scenario: Each tile shows loaded, loading, or error via existing primitives only   # M5
  Given a tile's query is pending, then succeeds, then separately is retried after a failure
  When the tile renders in each state
  Then pending renders the existing Loading primitive, success renders StatCard with variant="flat", and failure renders the existing ErrorState with its own onRetry bound to only that tile's refetch
  And no new loading or error component is introduced anywhere in the Strip

Scenario: Strip wrapper uses the shipped flat token treatment   # M6
  Given the Overview Strip renders
  When its wrapping container renders
  Then it carries Card variant="flat" (or an equivalent flat/borderless container), matching console-flat-visual-pass's shipped treatment
  And no Aurora rounded or shadowed styling appears anywhere in the Strip

Scenario: Strip is an accessible, distinguishable landmark   # M7
  Given a screen-reader user reaches the tenant-detail page
  When they navigate landmarks
  Then the Overview Strip exposes an accessible label (e.g. aria-label="Tenant overview") distinct from the Tabs region
  And every reused element's existing AA-verified contrast and keyboard operability (native Retry buttons) is unchanged

Scenario: The 4 existing tab files stay byte-identical beyond the export keyword   # M8
  Given PlatformPlanTab.tsx, PlatformMembersTab.tsx, PlatformKeysTab.tsx, and PlatformBudgetTab.tsx before this task
  When this task's build completes
  Then a diff on each file shows only its one interface gaining the export keyword
  And every existing test for those 4 files still passes unmodified

Scenario: Reject, no combined backend endpoint   # R1
  Given the Overview Strip's 4 fields
  When this task's build completes
  Then no new backend route exists bundling plan, seats, keys, and budget into one response
  And each field is still fetched by its own independent frontend query

Scenario: Reject, a Strip-local query key that diverges from its sibling tab   # R2
  Given the Overview Strip's 4 queries
  When their queryKey arrays are inspected
  Then none introduces a new or renamed key (e.g. "overview-plan") that diverges from its sibling tab's own key
  And React Query's cache is shared and deduped between the Strip and each sibling tab, never double-fetched

Scenario: Reject, no duplicated local response type in the Strip   # R3
  Given TenantPlanResponse, UsersListResponse, PlatformApiKey, and BudgetData
  When PlatformTenantOverviewStrip.tsx is inspected
  Then it imports these 4 types from their owning tab files
  And it declares no second, locally-duplicated interface with the same shape

Scenario: Reject, no new IA beyond the Strip   # R4
  Given the tenant directory or tenant-detail tabs
  When this task's build completes
  Then no Tier-3 kind/plan filter, bulk action, or other new information architecture exists
  And the only new UI element introduced anywhere is the Overview Strip itself

Scenario: Reject, the 4 tab files' own behavior is unchanged   # R5
  Given PlatformPlanTab, PlatformMembersTab, PlatformKeysTab, and PlatformBudgetTab's existing mutations and handlers
  When this task's build completes
  Then none of their existing props, handlers, mutations, or rendered output changed
  And only the M4 export keyword was added to each of the 4 files
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new REST endpoints — Tier 1, frontend-only. The frozen shape is (1) a new component's
prop/query contract and (2) an additive type-export contract on 4 existing tab files.

PlatformTenantOverviewStrip (NEW — components/platform/PlatformTenantOverviewStrip.tsx)
  props: { tenantId: string }
  Mounts in PlatformTenantDetail.tsx as a sibling between <PageHeader/> and <Tabs>, inside the
  existing `<div className="flex flex-col gap-6">` — never inside a TabsContent.

  4 independent React Query calls, each BYTE-IDENTICAL to its sibling tab's own key + URL:
    Plan+seat-cap : queryKey ["platform-tenant-plan", tenantId]
                    -> GET /admin/platform/tenants/{tenantId}/plan
                    -> TenantPlanResponse { tenant_id, plan: PlanResponse | null, seat_cap: number | null }
                    imported from PlatformPlanTab.tsx (now exported — see below)
                    tile: label="Plan", value=plan?.name ?? "No plan",
                          footer=seat_cap != null ? `Seat cap: ${seat_cap}` : "No seat cap set"
    Seats-used    : queryKey ["platform-tenant-users", tenantId]
                    -> GET /admin/platform/tenants/{tenantId}/users
                    -> UsersListResponse { users: PlatformTenantUser[] }
                    imported from PlatformMembersTab.tsx (UsersListResponse now exported — see below)
                    tile: label="Seats used", value=users.length
    Active keys   : queryKey ["platform-tenant-keys", tenantId]
                    -> GET /admin/platform/tenants/{tenantId}/keys
                    -> PlatformApiKey[]
                    imported from PlatformKeysTab.tsx (now exported — see below)
                    tile: label="Active keys", value=keys.filter(k => k.revoked_at === null).length
    Budget        : queryKey ["platform-tenant-budget", tenantId]
                    -> GET /admin/platform/tenants/{tenantId}/budget
                    -> BudgetData { budget_usd_monthly: string | null, spent_usd_month: string }
                    imported from PlatformBudgetTab.tsx (now exported — see below)
                    tile: label="Budget", value=spent_usd_month, footer derived from budget_usd_monthly

  Per-tile render contract (all 4 tiles, no new primitive introduced):
    isLoading -> <Loading label="…" className="…" />                          (states.tsx, verbatim)
    isError   -> <ErrorState title={getErrorTitle(error)} onRetry={() => void refetch()} />
                 (states.tsx, verbatim; refetch scoped to THAT tile's own query only)
    success   -> <StatCard variant="flat" label="…" value="…" footer="…" />   (stat-card.tsx, verbatim)
  Wrapper: Card variant="flat" (or an equivalent flat/borderless grid), aria-label="Tenant overview"
  (exact wording pinned at build; landmark role/label semantics are frozen here, not the copy).

Additive type-export contract (4 one-keyword diffs, zero runtime effect):
  PlatformPlanTab.tsx      : interface TenantPlanResponse  -> export interface TenantPlanResponse
  PlatformMembersTab.tsx   : interface UsersListResponse    -> export interface UsersListResponse
                              (PlatformTenantUser already exported — unchanged)
  PlatformKeysTab.tsx      : interface PlatformApiKey       -> export interface PlatformApiKey
  PlatformBudgetTab.tsx    : interface BudgetData           -> export interface BudgetData
  No other line in any of these 4 files changes; no existing test in those files is touched.

Schema: none touched — no DB/table/API changes; purely a new frontend component plus 4 additive
  TypeScript `export` keywords on pre-existing local interfaces.
```

Glossary deltas: none — "Overview Strip" is a UI-region name, not a new domain concept (mirrors
  `console-flat-visual-pass`'s own "none" call for a similarly UI-only change); no `.add/
  GLOSSARY.md` term applies or is introduced.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — this draft is being presented to Tin now, lowest-confidence flag first (see
  below); advancing past DRAFT to FROZEN waits on that approval.

Least-sure flag surfaced at freeze: [spec/contract] adding an `export` keyword to the 4 existing
local response interfaces (`TenantPlanResponse`/`UsersListResponse`/`PlatformApiKey`/
`BudgetData`) so the Strip can import them, rather than having the Strip declare its own local
duplicate shapes — a Ground-time engineering call neither MILESTONE.md nor DESIGN.md explicitly
pre-decided (they name the CACHE-KEY reuse risk explicitly, not the TS-type-reuse question).
Cost if wrong: revert to 4 local un-exported interfaces declared inside the new Strip file only
— zero tab-file edits, fully contained, no cascading change. Second-most-relevant, not the lead
flag: no dedicated visual capture/mockup exists for this task (unlike
`console-flat-visual-pass`'s 7-round Artifact) — residual risk is judged low since every element
the Strip composes (`StatCard`, `Card variant="flat"`, `Loading`, `ErrorState`) is already
shipped and AA-vetted, but the layout-composition judgment itself (a 4-up tile row above the
Tabs) has not been visually confirmed.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% lines on the new component file (achieved: 29/30 = 96.7% lines, 15/15 =
  100% functions on PlatformTenantOverviewStrip.tsx; project-wide 3242/3724 = 87.06%, above the
  80% vitest.config.ts floor — the 1 uncovered line is getErrorTitle's defensive
  `return "An error occurred"` catch-all, unreachable since every error constructed in this
  suite is either a BffError or a real Error instance).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_strip_mounts_between_pageheader_and_tabs_and_persists_across_tabs (in
    tests/platform-tenant-detail.test.tsx): arrange full shell + happy-path mocks (incl. the
    new /plan default) / act render + switch tabs (budget, members) / assert heading precedes
    Strip precedes tablist via compareDocumentPosition, the existing banner->heading->tablist
    order still holds, Strip stays present across tab switches · covers: M1
  - test_strip_renders_exactly_four_tiles_nothing_else: arrange 4 happy mocks / act render /
    assert exactly the 4 labeled tiles ("Plan"/"Seats used"/"Active keys"/"Budget") render, no
    combobox/searchbox/filter control appears · covers: M2 (baseline), R4
  - test_one_tile_failure_leaves_other_three_intact_and_retry_is_scoped: arrange Keys 500 + the
    other 3 happy / act render, click Keys' own Retry / assert the other 3 tiles' loaded data is
    untouched throughout and Keys refetches exactly once more · covers: M2, M5 (error+retry)
  - test_plan_tile_uses_byte_identical_query_key_and_url / test_users_tile_... /
    test_keys_tile_... / test_budget_tile_...: arrange one tile's happy mock / act render /
    assert the query cache holds an entry at the EXACT frozen key array with the mocked data,
    and the displayed value matches the contract's own derivation (users.length, non-revoked
    count, plan.name+seat_cap footer, spent_usd_month+cap footer) · covers: M3 (×4 tiles)
  - test_plan_tile_query_key_shared_with_plan_tab_dedupes_fetch: arrange a second simulated
    consumer under the SAME QueryClient using PlatformPlanTab's own literal key / act render both
    together / assert exactly 1 network call serves both consumers · covers: R2 (the flagship
    cache-reuse proof — the risk this task exists to manage)
  - test_overview_tile_loading_renders_loading_primitive / _error_renders_error_state_with_retry
    / _success_renders_statcard_flat_variant: arrange hand-crafted props, NO QueryClient/msw /
    act render the exported pure-props OverviewTile directly / assert Loading|ErrorState|StatCard
    renders per state · covers: M5 (all 3 states, in isolation, CONVENTIONS.md v13 lesson)
  - test_plan_tile_failure_retry_is_scoped_to_plan_only / test_users_tile_... /
    test_budget_tile_...: same shape as the Keys isolation test above, one per remaining tile,
    each its OWN test (fresh circuit-breaker state per test) · covers: M5 (retry-scoping
    generalized across all 4 tiles, not just Keys)
  - test_strip_wrapper_renders_flat_variant_no_aurora_styling: arrange happy mocks / act render /
    assert data-variant="flat", the flat-card radius class, no border-border/shadow-lg/
    rounded-2xl · covers: M6
  - test_strip_is_a_named_region_landmark_with_native_retry_button: arrange 1 tile erroring / act
    render / assert role="region" name="Tenant overview" resolves and is distinct from the
    tablist, the Retry control is a native <button> · covers: M7
  - test_strip_imports_the_four_types_and_declares_no_local_duplicate: arrange none / act read the
    component file's own source text / assert the 4 imports exist and no local
    `interface <Name>` redeclaration exists · covers: M4, R3
  - test_only_the_four_known_urls_are_ever_requested: arrange 4 happy mocks + an msw
    request:start listener / act render / assert the SET of hit paths equals exactly the 4
    known URLs, nothing else · covers: R1
  - the 4 existing tab suites (platform-plan-tab / platform-members / platform-keys /
    platform-config-budget .test.tsx), re-run UNMODIFIED post-export-addition · covers: M8, R5
  - the existing test_detail_shell_dom_order_banner_then_header_then_tabs, re-run UNMODIFIED ·
    covers: M1 (regression guard on the frozen structural assertion)
</test_plan>

Tests live in: `apps/dashboard/tests/` (new: `platform-tenant-overview-strip.test.tsx`;
  extended: `platform-tenant-detail.test.tsx`) · MUST run red (missing implementation) before
  Build — confirmed: the new file failed with "Failed to resolve import
  '@/components/platform/PlatformTenantOverviewStrip'. Does the file exist?" (clean RED, not a
  broken harness), and the new M1 test failed with "Unable to find role=region and name
  `/tenant overview/i`" while all 11 pre-existing tests in that file stayed green.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  - `apps/dashboard/components/platform/PlatformTenantOverviewStrip.tsx` (NEW)
  - `apps/dashboard/components/platform/PlatformTenantDetail.tsx` (mount site: 1 import + 1 JSX line)
  - `apps/dashboard/components/platform/PlatformPlanTab.tsx` (1-line export addition)
  - `apps/dashboard/components/platform/PlatformMembersTab.tsx` (1-line export addition)
  - `apps/dashboard/components/platform/PlatformKeysTab.tsx` (1-line export addition)
  - `apps/dashboard/components/platform/PlatformBudgetTab.tsx` (1-line export addition)
  - `apps/dashboard/tests/platform-tenant-overview-strip.test.tsx` (NEW)
  - `apps/dashboard/tests/platform-tenant-detail.test.tsx` (extended, no assertion weakened)

Strategy (ordered batches):
  1. Make the 4 mechanical `export` edits first; immediately re-run the 4 existing tab suites to
     CONFIRM (not assume) zero runtime effect.
  2. Write the NEW RED test file for PlatformTenantOverviewStrip (component doesn't exist yet).
  3. Extend `platform-tenant-detail.test.tsx` (new default `/plan` mock, a query-precision fix on
     one pre-existing test, 1 new M1 test) — confirm the new test is RED, the other 11 stay GREEN.
  4. Implement `PlatformTenantOverviewStrip.tsx`: an exported pure-props `OverviewTile` sub-
     component (CONVENTIONS.md v13 lesson) driven by 4 independent `useQuery` calls.
  5. Mount it in `PlatformTenantDetail.tsx` between `<PageHeader/>` and `<Tabs>`.
  6. Run every touched suite GREEN, then the FULL suite, eslint, `tsc --noEmit`, and coverage.

Persona: frontend-engineer (`.add/personas/frontend-engineer.md`) — Hydroa dashboard
  implementation lens: shipped 3-layer design-token fidelity, the 4 shared state components
  (Loading/Empty/ErrorState/Success) over any bespoke pattern, frozen structural contracts
  co-existing with new code rather than being edited to fit it.
Spawn isolation (default): none — executed directly in the main session (no subagent dispatch,
  no parallelism), so no worktree isolation was needed for this task.
Known-problem fixes:
  - trap: the Strip is unconditionally mounted, so its 4 queries fire on EVERY render of
    PlatformTenantDetail regardless of active tab, not just when Plan/Keys/etc. are open ->
    fix: added a default `/plan` handler to `mockAllTabsHappyPath` + `/plan`,`/users` handlers to
    the one test (`test_tab_failure_isolated_other_tab_still_loads`) that registers its own
    handlers manually instead of using that helper.
  - trap: the Strip's Budget tile shares the IDENTICAL cache key with `PlatformBudgetTab`, so a
    simulated Budget failure legitimately renders the SAME "Internal Server Error" text in TWO
    places (the tab's own + the Strip's own tile) once mounted together -> fix: scoped that one
    pre-existing test's assertion via `within(screen.getByRole("tabpanel"))` — a query-precision
    fix for a real, by-design duplicate, not a weaker assertion (mirrors this same file's own
    established precedent for the identical reason).
  - trap: an obvious Budget-tile footer wording ("Monthly Budget: $X") would collide with
    existing bare page-wide `getByText(/monthly budget/i)` assertions elsewhere in
    platform-tenant-detail.test.tsx (matching PlatformBudgetTab's own StatCard label) -> fix:
    chose "Cap: $X" / "No cap set" wording instead, deliberately avoiding the literal phrase.
  - trap: a test that fails all 4 tiles simultaneously then retries 3 of them sequentially trips
    `lib/resilient-fetch.ts`'s circuit breaker — keyed by ORIGIN not per-endpoint, default
    failureThreshold=5 — mid-test (observed directly: a later tile's mocked title got replaced by
    the breaker's own synthesized "Service temporarily unavailable") -> fix: replaced that one
    combined test with 3 independent one-tile-fails-at-a-time tests (mirroring the pre-existing
    Keys-tile test), each getting fresh breaker state via tests/setup.ts's own
    `afterEach(() => __resetBreakers())` — more realistic AND avoids the shared-breaker collision.
Strategy actually used: as planned (see Strategy above) — the one real-time deviation was
  discovering and fixing the resilient-fetch circuit-breaker collision (previous bullet), which
  reshaped the M5 "all 4 tiles" retry test into 3 separate per-tile tests rather than 1 combined
  test; everything else proceeded in the planned order.
Safety rule (feature-specific): none beyond the frozen contract's own cache-key-identity
  requirement — 4 read-only GETs, no mutation/atomicity concern introduced by this task.
Code lives in: `apps/dashboard/components/platform/`
Constraints: honored — no test's assertions were weakened, deleted, or skipped (only extended:
  new default mocks, new tests, and one existing assertion re-scoped via `within()` for a real,
  by-design duplicate-text reason, never to hide a wrong result); the frozen §3 contract was not
  edited; no new dependency was added (only already-imported, already-in-package.json packages:
  @tanstack/react-query, msw, @testing-library/react, node:fs/node:path for the source-text test).

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — independently re-run twice by the orchestrator (not the build agent's self-report): 1057/1057, 0 failures
- [x] coverage did not decrease — `npx vitest run --coverage` exits 0 with `vitest.config.ts`'s `thresholds.lines: 80` enforced (a regression below floor fails the process non-zero); exact per-file percentages (87.06% project-wide, 96.7% lines/100% functions on the new file) taken from the build agent's report — not re-extracted at exact-percentage granularity due to a coverage-table stdout-truncation limit in this shell environment, but the enforced-threshold pass itself is independently confirmed
- [x] no test or contract was altered during build — confirmed via direct `git diff`: the 4 tab files each carry EXACTLY 1 changed line (`interface X` -> `export interface X`); the one edit to an existing test's assertion (`test_tab_failure_isolated_other_tab_still_loads`'s `getByText` -> `within(tabpanel).getByText`) is a query-precision fix for a real, by-design duplicate match, not a weakening — see Refute-read below
- [x] the green was EARNED, not gamed — see Refute-read verdict below (EARNED)
- [x] concurrency / timing of the risky operation is safe — proven live by `test_plan_tile_query_key_shared_with_plan_tab_dedupes_fetch` (mounts a genuine second consumer sharing the QueryClient, asserts fetch count stays 1) + 3 independent scoped-retry tests (plan/users/budget, each proving retry refetches ONLY its own tile)
- [x] no exposed secrets, injection openings, or unexpected dependencies — zero new dependencies added; reuses 4 pre-existing authenticated GET endpoints verbatim; no new backend route or auth surface
- [x] layering & dependencies follow CONVENTIONS.md — pure-props `OverviewTile` (v13 folded testability lesson), type-reuse via additive `export` rather than local duplication (verified: no local `interface` redeclaration for any of the 4 shapes)
- [x] a person reviewed and approved the change — self (orchestrator) under `autonomy: auto`; ONE open item (Plan tile's `plan?.name` vs `.display_name`) is being put to Tin directly as a spec-delta decision, not gating this PASS (presentational-only, built exactly as frozen — see §7 Spec delta)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
> Filled immediately after freeze, before build dispatch — applying the process lesson logged in
> `console-flat-visual-pass`'s §7 (that task filled this block AFTER code existed; this one fills
> it in the order the guide actually prescribes).
- [x] `PlatformTenantOverviewStrip` renders as a sibling between `<PageHeader/>` and `<Tabs>` in
      `PlatformTenantDetail.tsx`, visible across all 5 tabs — confirmed: mount-site diff is exactly
      +3/-0 (1 import + 1 render line), and `test_strip_mounts_between_pageheader_and_tabs_and_persists_across_tabs`
      passes, asserting DOM order via `compareDocumentPosition` and persistence across 2 tab switches
- [x] the existing `test_detail_shell_dom_order_banner_then_header_then_tabs` still passes
      UNMODIFIED — confirmed by grep (byte-identical content, not just "not in the diff") AND by
      running the full suite post-build (1057/1057 green)
- [x] each of the 4 tiles' `useQuery` call uses the EXACT pre-existing key + URL of its sibling
      tab (no `overview-*`-prefixed or otherwise diverged key) — confirmed by reading
      `PlatformTenantOverviewStrip.tsx` directly against each of the 4 tab files' own queryKey
      lines, byte-for-byte; further proven LIVE (not just statically) by
      `test_plan_tile_query_key_shared_with_plan_tab_dedupes_fetch`'s dual-consumer fetch-count assertion
- [x] one tile's simulated query failure leaves the other 3 tiles' loaded data/loading state
      fully intact — confirmed by `test_one_tile_failure_leaves_other_three_intact_and_retry_is_scoped`
      plus 3 further single-tile-failure tests (plan/users/budget), all passing
- [x] `TenantPlanResponse`/`UsersListResponse`/`PlatformApiKey`/`BudgetData` each gain ONLY the
      `export` keyword in their 4 owning files — confirmed by `git diff` showing exactly 1 changed
      line per file (`PlatformTenantUser` in PlatformMembersTab.tsx was already exported pre-task),
      zero other lines
- [x] the Strip's wrapper and all 4 tiles use `Card variant="flat"`/`StatCard variant="flat"` —
      no Aurora rounded/shadowed classes anywhere in the new file — confirmed by reading the
      component source directly (`data-variant="flat"`, `rounded-[var(--radius-flat-card)]`) and by
      `test_strip_wrapper_renders_flat_variant_no_aurora_styling`'s explicit negative assertions
      (no `border-border`/`shadow-lg`/`rounded-2xl`)
- [x] the Strip carries a distinguishing `aria-label` separate from the Tabs region — confirmed
      by `test_strip_is_a_named_region_landmark_with_native_retry_button` (asserts the region role
      + name AND that no `tablist` role is conflated with it)
- [x] full dashboard suite green + eslint clean, independently re-run by the orchestrator (not
      trusting the build agent's self-report) — same discipline as `console-flat-visual-pass`:
      1057/1057 tests, 0 lint errors (2 pre-existing unrelated warnings), `tsc --noEmit` clean on
      every touched file (9 pre-existing errors remain only in `platform-plan-tab.test.tsx`'s
      `mockCommon` helper — confirmed recurring from before this task, not introduced by it)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `PlatformTenantOverviewStrip` is imported and rendered in
      `PlatformTenantDetail.tsx` (diff, +3/-0); `OverviewTile` is exported and directly imported by
      its own test file for isolated pure-props testing; `getErrorTitle` is used by all 4 tiles'
      isError branch — confirmed by reading the component source directly
- [x] DEAD-CODE (code) — no new unused/orphaned symbol: `tsc --noEmit` reports zero
      unused-import/unused-variable errors on any touched file (the 9 errors present are
      pre-existing, confined to `platform-plan-tab.test.tsx`'s `mockCommon`, unrelated to this task)
- [x] SEMANTIC (prose / non-code) — read `PlatformTenantOverviewStrip.tsx` in full (187 lines) and
      `tests/platform-tenant-overview-strip.test.tsx` in full (568 lines), plus the complete diff of
      `platform-tenant-detail.test.tsx`'s edits and the full §3 CONTRACT text — confirmed the file
      header's own documentation claims (cache-key mirroring, pure-props pattern, type-reuse,
      the Budget-key literal-array precedent) match the actual code exactly; found and corrected one
      misattributed code-comment citation (see Refute-read)

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed directly:
      `PlatformTenantDetail`/`PlatformTenantDetail.tsx` (diff), `PlatformPlanTab`/`TenantPlanResponse`/
      `planQueryKey` (diff, now exported), `PlatformMembersTab`/`UsersListResponse` (diff, now
      exported), `PlatformKeysTab`/`PlatformApiKey` (diff, now exported), `PlatformBudgetTab`/
      `BudgetData` (diff, now exported), `Card`/`StatCard`/`Loading`/`ErrorState` (component source,
      imported verbatim from `@/components/ui`)
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — none moved;
      all 7 cited symbols resolved at their Ground-time locations unchanged

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: self (orchestrator) · adversarially checked: (a) whether the `within(tabpanel)` scoping edit to
an EXISTING test was a real necessary fix or a hidden weakening — confirmed real by reading
`components/ui/tabs.tsx:TabsContent` directly (`if (active !== value) return null` — exactly one
`tabpanel` role element exists in the DOM at a time, so the scoped query is precision, not
weakening: it still checks the identical Budget-tab error text, just excludes the Strip's own
sibling tile that legitimately renders the same text by M3's own cache-sharing design); (b) whether
the cache-dedup test could pass vacuously — confirmed it mounts a genuine second query consumer
sharing the QueryClient and asserts a concrete fetch-count (1, not simply comparing two static key
arrays for equality); (c) whether avoiding a "4-tiles-fail-simultaneously" test was dodging a real
bug — confirmed legitimate via `lib/resilient-fetch.ts`'s origin-keyed circuit breaker (independently
plausible, consistent with prior sessions' documented behavior of that module); (d) whether a cited
test precedent was fabricated — found `test_config_cache_saves_independently_of_guardrails` IS real
but was misattributed to the wrong file in a code comment (lives in
`platform-config-budget.test.tsx`, not `platform-tenant-detail.test.tsx`); corrected the comment
directly; (e) full independent re-run of tests/lint/tsc rather than trusting the agent's self-report
— all confirmed (1057/1057, 0 lint errors, tsc clean on touched files), with one immaterial,
unreconciled discrepancy in the agent's stated PRE-task baseline ("1054", vs. this orchestrator's own
last-confirmed 1039 after the prior task) — the AFTER count (1057) is independently verified correct
regardless, so this is logged but not treated as a defect.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: self (orchestrator)
1. Security: CLEAR — no new backend route or auth surface introduced; reuses 4 pre-existing
   authenticated GET endpoints verbatim, each already gated by the existing superadmin/tenant-scope
   authorization on those routes; no new secret, no injection opening, no new dependency.
2. Concurrency: CLEAR — cache-key identity proven live (dual-consumer dedup test), per-tile retry
   scoping proven for all 4 tiles (one shared test + 3 dedicated single-failure tests), no shared
   mutable state across tiles.
3. Architecture: CLEAR — pure-props tile pattern (CONVENTIONS.md v13 lesson), type-reuse via
   additive `export` rather than local duplication (verified: no local `interface` redeclaration for
   any of the 4 imported shapes), zero new dependencies, R4's anti-scope-creep assertions pass (no
   combobox/searchbox/filter control snuck into the Strip).
Verdict: PASS
Residue: none blocking. One open, non-blocking cosmetic spec-delta: the Plan tile's frozen
`value=plan?.name` literally renders the machine slug (e.g. "team") rather than `plan?.display_name`
(e.g. "Team"), which every other plan-facing surface in this codebase uses for human text — built
exactly as the frozen §3 CONTRACT specified; tracked as a `[SPEC · open]` delta below, Tin's direct
call sought separately (not gating this PASS — presentational-only, zero correctness/security impact).
Binding: advisory (sensitivity: unset/mechanical-leaning; not declared high-risk)

### GATE RECORD
Reported: yes — this VERIFY section is the report, rendered before the outcome below
Outcome: PASS
Reviewed by: self (Claude, orchestrator — independently re-ran tests/lint/tsc, read every touched
diff and the full new component + test file, cross-checked the frozen §3 CONTRACT text directly)
· date: 2026-07-06

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `test_strip_mounts_between_pageheader_and_tabs_and_persists_across_tabs`
(M1 — catches a future accidental Strip-position regression) and
`test_plan_tile_query_key_shared_with_plan_tab_dedupes_fetch` (R2 — catches a future accidental
cache-key divergence between the Strip and its sibling tabs) as durable regression monitors.

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned (see Strategy above) — the one real-time deviation was
- [AI] verify — gate PASS (reviewed by self (Claude, orchestrator — independently re-ran tests/lint/tsc, read every touched)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · seeded] (resolved by `overview-strip-plan-display-name`, gate PASS 2026-07-06) Plan tile
  renders `plan?.name` (machine slug, e.g. "team") per §3 CONTRACT's literal
  text, not `plan?.display_name` (e.g. "Team") which every other plan-facing surface in this
  codebase uses for human text (`PlatformPlanCatalog.tsx`'s `CardTitle`, `PlatformPlanTab.tsx`'s
  "Assign {plan.display_name}") — a likely wording oversight that survived the design draft,
  orchestrator review, AND freeze approval undetected. Low-risk/cosmetic (display-only), but a
  correction touches 1 component line AND ~7 test assertions currently pinned to the literal "team"
  string (evidence: `PlanResponse` in PlatformPlanCatalog.tsx declares both `name` and
  `display_name` as distinct fields; grep confirms zero existing surfaces render `.name` for
  display). Tin's direct decision sought.
- [SPEC · open] Budget tile footer wording ("Cap: $X"/"No cap set") was left unfrozen by §3 CONTRACT
  (only landmark semantics were pinned, not exact copy); implemented to avoid an ambiguous duplicate
  `getByText(/monthly budget/i)` collision with `PlatformBudgetTab`'s own StatCard label (evidence:
  §3 CONTRACT's own wrapper line reads "exact wording pinned at build; landmark role/label semantics
  are frozen here, not the copy"). No action needed — logged for visibility only (mirrors
  `console-flat-visual-pass`'s own M3 banner precedent of logging an unfrozen copy choice).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

- [ADD · folded] a design contract's literal value-expression (e.g. `plan?.name` vs `.display_name`) [folded foundation-version 48]
  can encode a real product-facing bug that survives design-draft + orchestrator-review +
  human-freeze undetected when nobody cross-checks the exact field name against sibling surfaces at
  freeze time (evidence: this task's own Plan-tile spec-delta above). Suggests a design-phase
  checklist item: when a contract's literal value expression selects one field of a multi-field
  response shape (name vs display_name, id vs slug, etc.), explicitly cross-check that field choice
  against how sibling/existing surfaces already render the same shape — not just that the types
  line up.
- [ADD · folded] (recurring — same root cause logged in `console-flat-visual-pass`) the [folded foundation-version 48]
  `task_not_grounded` WARN still fires from `_section0_anchors`'s same-line-only regex against this
  task's own multi-line-bulleted §0 GROUND convention, the project's own dominant style (evidence:
  same regex gap first logged in `console-flat-visual-pass`'s §7, now recurring here unchanged). Not
  fixed in the engine; logged again purely for visibility/frequency.
