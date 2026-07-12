# TASK: Tenant Billing console (Invoices/Credits/Plan & seats) — Aurora financial-document idiom

slug: billing-ui · created: 2026-07-12 · stage: production
sensitivity: mechanical
milestone: monetization-core
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/components/ui/app-shell.tsx:NAV_GROUPS` / `NavGroup` / `NavItem` (incl. `minRole?: "admin"|"owner"`) / `itemVisible()` (denylist, fail-open) / `showPlatformNav()` (allowlist, fail-closed — reserved for genuine cross-tenant disclosure risk per its own doc comment) — this task ADDS one new `NavGroup { label: "Billing", items: [...] }` between the existing "Insights" and "Configure" groups; zero edits to any of the 26 existing `NAV_ITEMS`/`NAV_GROUPS` entries, `itemVisible`, or `showPlatformNav`.
- `apps/dashboard/components/logs/LogDetailDrawer.tsx:LogDetailDrawer` — Radix `Dialog`/`DrawerContent` chrome + the captured-`document.activeElement`-on-open / restored-on-close focus idiom (lines 216-225, 234-238) — reused STRUCTURALLY (not copied byte-for-byte: this drawer's body is a paginated LIST, `LogDetailDrawer`'s is a single-record fetch) for the new `InvoiceEvidenceDrawer`.
- `apps/dashboard/components/logs/LogsExplorerPage.tsx` — the client `cursorStack`/`hasMore`/`canGoPrevious`/`onNext`/`onPrevious` keyset-pagination idiom (lines ~147, 259-270) — reused verbatim in shape for the Invoices list, the Credits history table, and the evidence drawer's own internal pager.
- `apps/dashboard/components/memory/MemoryScoreBar.tsx:MemoryScoreBar` — the `role="progressbar"` meter idiom (null value → text-only branch, never a fabricated percentage) — generalized into a new `EntitlementMeter` for the Plan & seats budget readout.
- `apps/dashboard/lib/bff-client.ts:bffGet` + `apps/dashboard/app/api/gw/[...path]/route.ts` — confirmed (read in full this session) the route's `isBinaryPassthrough` branch (line ~362: non-JSON, non-streaming, `upstream.body != null` → forwarded verbatim including `Content-Disposition`) ALREADY covers a PDF/CSV export response — the invoice export links need **zero new BFF code**.
- `apps/dashboard/lib/format.ts:formatUsd` / `formatNumber` / `formatTimestamp` — reused for every money/count/timestamp field; no new shared formatter added (see §1 Framings — the statement-period label uses a small page-local `Intl.DateTimeFormat` call instead, YAGNI until a second consumer needs "month name only").
- `apps/dashboard/components/ui/index.ts` — barrel already exports `Card/CardHeader/CardTitle/CardContent`, `Table/TableHeader/TableBody/TableRow/TableHead/TableCell`, `Badge`, `Dialog/DrawerContent/DialogTitle/DialogDescription`, `Loading/Empty/ErrorState`, `StatCard`, `PageHeader` — every visual primitive this task needs already ships; no new base component required.
- `apps/dashboard/app/globals.css` — Aurora token layer: `--font-mono` (the v7 "spec-sheet" tabular-numerals convention, already used by `PageHeader`'s meta strip and `MemoryScoreBar`'s score), `--success`/`--success-text`, `--warning`, `--destructive`/`--destructive-text` (status/seal colors), `--accent-soft` (hero StatCard tint). No new token added.
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission.INVOICES_READ` + `ROLE_PERMISSIONS` (lines 80, 87-136, read directly this session) — CONFIRMED role set: `OWNER`/`ADMIN`/`BILLING_ADMIN`/`SUPERADMIN` hold it; `OPERATOR`/`VIEWER`/`MEMBER` do not.
- `apps/gateway/src/gateway/budgets/api/router.py:get_budget` (lines 33-46) — "Accessible to any authenticated role" RBAC precedent, mirrored verbatim for the new `GET /admin/plan` endpoint's own gating; its `spent_usd_month` field is reused directly by the Plan & seats budget meter.
- `.add/tasks/invoice-generation/TASK.md` §3 (FROZEN @ v1, read in full) — `GET /admin/invoices` (list/detail/evidence/export) shapes, `Permission.INVOICES_READ` RBAC delta, error catalog (`ERR_INVOICE_NOT_FOUND`, `ERR_INVOICE_QUERY_TIMEOUT`, `ERR_INVOICE_IMMUTABLE`) — cited verbatim, never redefined.
- `.add/tasks/credits-ledger/TASK.md` §3 (FROZEN @ v1, read in full) — `GET /admin/credits/balance`, `GET /admin/credits/history` shapes + its M10 line ("any authenticated tenant role, read-only") — cited verbatim.
- `.add/tasks/plan-enforcement/TASK.md` §3 (FROZEN @ v1, read in full) — `ResolvedEntitlements` dataclass + `PlanEntitlementResolver.resolve(tenant_id)` Protocol port (`gateway/tenants/domain/ports.py`, `gateway/tenants/domain/entitlements.py`) + its own M8 note: "no HTTP endpoint added (Non-goal — flagged, not silently dropped)" — the seam this task's new `GET /admin/plan` wires into.
- `apps/gateway/src/gateway/tenants/api/platform_plans_router.py:PlanResponse` (lines 77-85) — the existing Pydantic shape this task's `PlanSummary` subset mirrors field-for-field; never redefined independently.
- `apps/dashboard/components/members/MembersPage.tsx` + `GET /admin/members` (already shipped, `teams-core`/`cross-tenant-keys-members`) — reused VERBATIM for the seat-roster COUNT; no new endpoint, no touch to `plan-seat-cap`'s (sibling, unfrozen) own territory.
- `apps/dashboard/components/platform/PlatformBudgetTab.tsx:115` — the "Unlimited" null-ceiling text convention, reused verbatim (never a raw `null`/blank cell).
- `apps/dashboard/components/audit/AuditPage.tsx` (header comment) — the shipped precedent this task's Invoices nav-gating shape mirrors: `AUDIT_READ` also excludes `billing_admin`/`viewer`, yet the nav uses the same coarse `minRole:"admin"` denylist (shows the link, gateway 403s the ineligible roles) — an already-accepted looser-nav/stricter-gateway shape, not a new invention.

Context (working folder): `tmp/monetization-core-design-context.md` (binding rules 1-6; read in full) · `.add/milestones/monetization-core/MILESTONE.md` (UI/UX scope paragraph naming the financial-document idiom verbatim + Exit criteria 1/7 + "UDD design-definition loop required for billing-ui before its build") · the three sibling FROZEN@v1 TASK.md files above (read in full, not summarized) · `.add/tasks/app-shell-sidebar/TASK.md` (frozen `NavGroup`/`AppShellProps` shape, the file this task additively extends) · `.add/tasks/plan-admin-ui/TASK.md` (precedent for citing-not-redefining an already-shipped Pydantic response shape, and for the "Unlimited" convention).

Honors (patterns / conventions):
- Reuse-over-invent (ui-designer persona, `.add/personas/ui-designer.md`): every visual primitive traces to an existing token/component; the ONE new pattern (the financial-document seal/tabular-nums idiom) is named explicitly, not silently introduced.
- UX-only nav, fails open unless a REAL cross-tenant disclosure risk exists (the `showPlatformNav` precedent's own documented distinction) — Invoices nav-gating stays a denylist, not a new allowlist.
- Append-only/immutable money display: no client-side edit affordance is ever rendered on an issued invoice (matches the frozen contract's own GET+export-only shape — no PUT/PATCH exists to render a button for).
- BFF pass-through discipline: no `Authorization` header ever constructed client-side; no sk-/session token reaches the browser; every new page calls `bffGet` exclusively.
- "One resolver" (MILESTONE.md binding rule 2, read UI-side): the client renders exactly what the API returns and never re-derives a money total client-side (an invoice's `total_usd` is trusted verbatim, never re-summed from `lines[]` in the UI).

Seams consulted: none in `.add/SEAMS.md` yet for a financial-document idiom — first task to establish it; a `.add/SEAMS.md#financial-document-idiom` entry (tabular-nums money columns · visibly-immutable issued-doc seal · evidence-drawer-over-cross-link) is proposed at BUILD for the next reader (`margin-dashboard`, wave-2, will likely want the same seal/tabular-nums convention on the platform-console Margin page).

Anchors the contract cites: `NAV_GROUPS`, `LogDetailDrawer`, `LogsExplorerPage`'s cursor-stack idiom, `MemoryScoreBar`, `bffGet`, the BFF `isBinaryPassthrough` branch, `formatUsd`/`formatNumber`/`formatTimestamp`, `Permission.INVOICES_READ`/`ROLE_PERMISSIONS`, `get_budget`, `PlanEntitlementResolver`/`ResolvedEntitlements`, `PlanResponse`, `GET /admin/members`, `PlatformBudgetTab`'s Unlimited convention, `AuditPage`'s nav-vs-gateway precedent.

Issues/Risks (→ feed §1):
- [Major, feeds ⚠1] No tenant-self plan/entitlements HTTP endpoint exists anywhere in the codebase — `plan-enforcement`'s own FROZEN M8 explicitly deferred this as a Non-goal, flagged for a future consumer. This task's contract must either invent one (chosen — see §1 Framings) or block on reopening a frozen, Tin-approved contract.
- [Major] `credits_gate_enabled` is a GLOBAL (platform-wide, not per-tenant) `Settings` kill-switch with **no API exposure** — the Credits page cannot honestly distinguish "credits disabled platform-wide" from "enabled but genuinely unused" from the outside. Resolved: one unified empty/informative state, never hidden (see §1 Framings, ⚠2).
- [Minor] `Permission.INVOICES_READ`'s exact role set (owner/admin/billing_admin/superadmin) matches NEITHER existing `NavItem.minRole` value at byte precision (`"admin"` hides member only; `"owner"` hides member+admin) — operator/viewer still see the Invoices link and get a 403 on click. Resolved by mirroring the already-shipped `AUDIT_READ`/`LOGS_READ` precedent (same imprecision, already accepted) rather than inventing a new allowlist `minRole` variant for one item.
- [Minor] `plans.seat_cap` (tier default, this task's own read) vs. a tenant-level seat-cap OVERRIDE column `plan-seat-cap` (sibling, milestone `platform-access-plan`, still `phase: ground`) will eventually own — this task reads ONLY `plans.seat_cap`, never the tenant-override column, honoring milestone binding rule 5 ("never duplicate seat-cap logic").
- [Ruled out, not silently] A live RPM/TPM meter on the Plan & seats page — no endpoint anywhere exposes a rolling per-tenant RPM/TPM figure today. `rpm_limit_default`/`tpm_limit_default` are shown as STATIC catalog reference values only, explicitly not framed as an enforced live meter (only the budget dimension is actually enforced, per `plan-enforcement`'s own 3-dimension scope).

Related intent: MILESTONE.md's UI/UX scope paragraph (the financial-document idiom, named verbatim: "statement-style invoice detail with tabular-nums currency columns, an issued-invoice surface that is visibly immutable ..., and per-line evidence drill-down reusing the Logs-Explorer drawer pattern") + Exit criteria 1 ("every line drills down to the usage rows that produced it") and 7 ("all Billing surfaces pass axe ... and the issued invoice is visibly immutable in the UI"). GLOSSARY: this task reuses every domain term already proposed by the three sibling frozen contracts (invoice, invoice line, evidence link, credit ledger, hold, grace, entitlement resolution, plan default, plan-gated feature) — introduces no new domain term (see §3 Glossary deltas for the UI-only convention notes instead).

Ground SHA: 71641a9

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant console **Billing** nav group — Invoices, Credits, and Plan & seats: three read-mostly
pages plus an evidence drill-down drawer, realizing the milestone's financial-document idiom on top
of three already-FROZEN backend contracts, with exactly ONE new backend read endpoint (tenant-self
plan entitlements) this task's own scope introduces.
Framings weighed:
  - **Source of "my plan" data**: a NEW tenant-self `GET /admin/plan` backed by the already-frozen
    `PlanEntitlementResolver` port **(CHOSEN)** — vs. reusing the platform-scoped `GET /admin/
    platform/tenants/{tenant_id}/plan` **(REJECTED — `require_superadmin`-gated first-line
    dependency, structurally unreachable from a tenant console session)** — vs. blocking this page
    entirely pending a `plan-enforcement` change request **(REJECTED — slower, and the resolver was
    explicitly built for exactly this in-process-consumer shape per its own M8 docstring)**.
  - **Credits kill-switch visibility**: always render the Credits page, ONE unified empty/
    informative state covering both "genuinely unused" and "platform-wide disabled" **(CHOSEN)** —
    vs. hiding the Credits nav/page entirely when disabled **(REJECTED — no API signal exists to
    detect this without yet another new endpoint, and hiding on a guess risks hiding a legitimately-
    enabled-but-zero-balance tenant)** — vs. adding a `gate_enabled` field to `credits-ledger`'s
    response **(REJECTED — that contract is FROZEN @ v1; editing it is a change request outside this
    task's authority)**.
  - **Evidence drawer reuse depth**: reuse the Logs-Explorer drawer's CHROME (Dialog/DrawerContent/
    focus-return) but write a NEW paginated-list body **(CHOSEN — the evidence data shape is a LIST,
    not a single record; a byte-identical `LogDetailDrawer` copy would misrepresent the shape)** —
    vs. deep-linking to Logs Explorer's own drawer with a `log_id` **(REJECTED — no `log_id` exists
    in the frozen `UsageEvidenceItem` shape, AND a `billing_admin` viewer lacks `LOGS_READ`, so the
    link would 403 for a legitimate invoice viewer)**.
  - **Seat roster count source**: reuse the already-shipped `GET /admin/members` list length
    **(CHOSEN)** — vs. waiting on `plan-seat-cap`'s own (unfrozen, `phase: ground`) read surface
    **(REJECTED — blocks this task on a sibling still mid-design; reading a member COUNT is not
    seat-cap ENFORCEMENT logic, so milestone binding rule 5 is not violated by this choice)**.
  - **Invoices nav-gating shape**: reuse the existing denylist `minRole:"admin"` (shows the link to
    operator/billing_admin/viewer, who then 403 at the gateway) **(CHOSEN, mirrors the already-
    shipped `AUDIT_READ`/`LOGS_READ` precedent)** — vs. inventing a new allowlist `minRole` variant
    precise to `INVOICES_READ`'s exact role set **(REJECTED — the codebase reserves allowlist-gating
    for genuine cross-tenant information-disclosure risk, per `showPlatformNav`'s own doc comment; a
    same-tenant invoice link that merely 403s on click is not that risk)**.
Must:
<must>
  - **[M1]** Add ONE new `NavGroup { label: "Billing", items: [Invoices, Credits, "Plan & seats"] }`
    to `NAV_GROUPS` (between "Insights" and "Configure"). "Invoices" carries `minRole: "admin"`
    (hides from `member` only, mirrors `AUDIT_READ`/`LOGS_READ`); "Credits" and "Plan & seats" carry
    NO `minRole` (visible to every authenticated role, mirrors `GET /admin/budget`'s own gating).
  - **[M2]** Invoices list page (`/app/invoices`) renders `GET /admin/invoices` as a keyset-paginated
    table (Period · Status seal · Total), reusing `LogsExplorerPage`'s `cursorStack`/Next/Previous
    idiom verbatim in shape; a row click navigates to `/app/invoices/[invoiceId]`.
  - **[M3]** Invoice detail page (`/app/invoices/[invoiceId]`) renders `GET /admin/invoices/{id}`:
    a header with the statement period + a visibly-immutable status seal (`Badge` "Issued"
    success-variant + lock icon, or "Draft" secondary-variant), a Lines table grouped by
    (model, team, key, tags) with a `tabular-nums` Amount column and a per-line "View evidence"
    action, a Corrections section (its own empty state when none), and a Total / corrected-total
    footer row. NO edit affordance is ever rendered (matches the frozen contract's GET+export-only
    shape — no PUT/PATCH exists to back a button).
  - **[M4]** `InvoiceEvidenceDrawer` opens from a line's "View evidence" action; fetches
    `GET /admin/invoices/{id}/lines/{lineId}/evidence` keyset-paginated; renders a compact usage-row
    table (created_at, model_id, tokens, `cost_usd` tabular-nums, request_id). Reuses
    `LogDetailDrawer`'s Dialog/DrawerContent chrome + captured-trigger focus-return idiom
    structurally; deliberately does NOT link out to Logs Explorer (see Framings).
  - **[M5]** PDF/CSV export via two plain `<a href="/api/gw/admin/invoices/{id}/export?format=pdf|
    csv" download>` links ("Download PDF" / "Download CSV") — zero new BFF code; the existing
    `isBinaryPassthrough` branch already forwards the response (incl. `Content-Disposition`)
    verbatim.
  - **[M6]** Credits page (`/app/credits`) renders a hero `StatCard` (balance_usd tabular-nums, +
    a grace note when `grace_usd > 0`) from `GET /admin/credits/balance`, and a keyset-paginated
    history table (Date · Type badge · Amount · Balance after) from `GET /admin/credits/history`.
    Zero entries → ONE unified informative `Empty` state ("No credit activity yet" / "Your platform
    operator manages credit top-ups — contact them to add credits.") that covers BOTH "unused" and
    "kill-switch off" (no API signal distinguishes them — see Framings); the section is NEVER hidden.
  - **[M7]** Plan & seats page (`/app/plan`) renders the NEW `GET /admin/plan`: plan `display_name`
    (or "No plan assigned — usage governed by tenant-level defaults only" when null), an
    `EntitlementMeter` plotting `GET /admin/budget`'s `spent_usd_month` against the resolved
    `effective_budget_usd_monthly` ("Unlimited" when null, mirrors `PlatformBudgetTab`), a
    model-allowlist readout (list or "All models"), a feature-flags readout (badge list or "None"),
    and a seat line combining `plan.seat_cap` (or "Unlimited") against a LIVE roster count reused
    from `GET /admin/members` (zero new endpoint). RPM/TPM are shown as static catalog reference
    values, explicitly not framed as a live meter (see §0 Issues, Ruled-out).
  - **[M8]** NEW backend endpoint `GET /admin/plan` (tenant-self; any authenticated role, mirrors
    `GET /admin/budget`'s RBAC exactly) — a THIN read composed from two already-frozen pieces, zero
    new query shape invented: `PlanEntitlementResolver.resolve(tenant_id)` (plan-enforcement §3,
    FROZEN, explicitly built for in-process consumers) for the resolved budget/allowlist/features,
    plus one `PlanRow` lookup (plan-catalog's own schema, unchanged) for display_name/seat_cap/
    rpm_limit_default/tpm_limit_default. The ONE new backend surface this design-only UI task
    introduces.
  - **[M9]** Every page's own 403 handling shows a page-specific inline message (mirrors
    `LogsExplorerPage`'s `getErrorTitle` "You don't have access to X" idiom) as page-level
    defense-in-depth beneath the UX-only nav filter (Invoices' nav item is shown to operator/viewer
    too, per M1) — a direct-URL or stale-link visit never shows a raw JSON error.
  - **[M10]** Every currency/token/count column renders `font-mono tabular-nums` (mirrors the v7
    "spec-sheet" numerals convention) via the EXISTING `formatUsd`/`formatNumber`/`formatTimestamp`
    helpers — no new SHARED formatter is added; the statement-period label (period_start/period_end)
    uses a small page-local `Intl.DateTimeFormat` call instead (YAGNI — no second consumer needs a
    "month name only" label today, so it is not promoted to `lib/format.ts`).
  - **[M11]** All three pages + the evidence drawer pass axe (WCAG 2.2 AA) with zero serious/
    critical violations, reusing the shipped a11y idioms verbatim (`Loading` role="status",
    `ErrorState` role="alert", `Empty` non-alarming, `DrawerContent`'s Radix focus-trap/Escape/
    focus-return, exactly one `h1` per page via `PageHeader`).
  - **[M12]** Responsive at the EXISTING dashboard breakpoints (no new breakpoint introduced) —
    tables use the same horizontal-scroll-inside-container convention as `LogsTable`/`AuditTable` at
    narrow widths, never breaking out of the shell's `lg:overflow-y-auto` main region.
</must>
Reject:
<reject>
  - **[R1]** A `member` role hits any Invoices route/BFF call directly -> "ERR_AUTH_FORBIDDEN" (403,
    `INVOICES_READ`) rendered as a page-level `ErrorState` ("You don't have access to Invoices").
    Credits/Plan pages never reject a `member` (any-role access, M1/M8).
  - **[R2]** An `operator`/`viewer`/`billing_admin` clicks the Invoices nav link (shown per M1's
    fail-open UX-only nav) and the gateway 403s -> the SAME `ErrorState` render as R1, never a raw
    error dump.
  - **[R3]** A cross-tenant or unknown `invoice_id`/`line_id` on the detail/evidence
    routes -> "ERR_INVOICE_NOT_FOUND" (404) rendered as `ErrorState` "Invoice not found" -> And no
    other tenant's invoice data is ever fetched or rendered (the BFF forwards the gateway's own
    tenant-scoped 404, never a client-side filter).
  - **[R4]** Export requested with an unsupported/missing `format` -> the UI never constructs such a
    link (only the literal `"pdf"`/`"csv"` query values are ever emitted) — pre-flight prevention,
    mirrors `LogsExplorerPage`'s own R3 `validateFilters` precedent; the underlying 422
    `ERR_PAYLOAD_INVALID` path is defensively unreachable from this well-behaved client.
  - **[R5]** `credits_gate_enabled` is platform-wide OFF (no per-tenant signal exists) -> the Credits
    page renders the SAME unified empty/informative state as a genuinely-unused tenant (M6) -> And
    no attempt is made to infer or fabricate an "enabled" flag from absent data.
  - **[R6]** A tenant with `plan_id = NULL` loads `/app/plan` -> `GET /admin/plan` returns
    `plan: null`, `resolved: { effective_budget_usd_monthly: <tenant's own explicit budget or
    null>, plan_model_allowlist: null, plan_feature_flags: [] }` (mirrors `plan-enforcement`'s own
    M7 grandfathered-unlimited semantics verbatim) -> the page shows "No plan assigned" while the
    tenant-level budget meter still renders (the data remains meaningful) -> And nothing on the page
    implies the tenant is mis-configured or errored.
  - **[R7]** Seat-billing has not yet frozen at build time -> the "Per-seat pricing" slot renders its
    inert placeholder note, never a fetch to a non-existent endpoint and never a broken/blank layout
    gap -> And the rest of the Plan & seats page is unaffected.
</reject>
After:
<after>
  - After M2/M3: every invoice line's Amount sums to the invoice's `total_usd` because the client
    renders exactly what the API returns and never re-derives a money total client-side (the
    milestone's "one resolver" rule extended to the UI: no second, silently-drifting arithmetic
    path).
  - After M4: closing the evidence drawer returns focus to the triggering line's "View evidence"
    control (mirrors `LogDetailDrawer`'s own focus-return contract exactly).
  - After M6: a top-up performed by a platform operator (out of this UI's own scope) is visible on
    the NEXT load of this same Credits page — no client-side cache-staleness workaround needed
    beyond the existing react-query defaults.
  - After M7: switching between a planned and an unplanned tenant's session renders the correct
    plan/no-plan state without a stale cached read (react-query keyed per tenant via the existing
    session-cookie scoping, identical to every other admin surface).
  - After R3: a single detail-page 404 leaves the invoice LIST's own pagination state untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Inventing a NEW tenant-self `GET /admin/plan` endpoint (M8) to back the Plan & seats page —
    `plan-enforcement`'s own FROZEN @ v1 contract explicitly named "no HTTP endpoint" as a
    deliberate Non-goal for its `PlanEntitlementResolver` port, flagging (not silently dropping)
    exactly this future need. This task picks that up additively, in its OWN contract, never
    touching `plan-enforcement`'s frozen file — but it IS new backend surface born inside what was
    framed as a "UI task," and the RIGHT OWNING TASK for that router file is a genuine judgment
    call, not a derivation. Lowest confidence because the alternative (a change request back to
    `plan-enforcement`, already frozen and Tin-approved) is slower but arguably more "correct"
    process; if wrong: Tin redirects the endpoint's home (e.g., a small dedicated task) at freeze —
    cheap, since the underlying resolver call is unchanged either way, only which task's §5 Scope
    owns the router file moves.
  - [ ] Credits section ALWAYS visible with one unified empty state for "unused" vs. "kill-switch
    off" (M6/R5) — medium confidence; the alternative (hiding Credits when platform-wide disabled)
    is impossible to implement honestly today without either more backend scope creep (a second new
    endpoint) or an unreliable heuristic. Cost if wrong: a Build-time addition of a third new
    backend field, not a redesign.
  - [ ] Invoices nav gated by the EXISTING `minRole:"admin"` denylist rather than a new allowlist
    value (§1 Framings) — medium-high confidence, directly mirrors the shipped `AUDIT_READ`/
    `LOGS_READ` precedent. Cost if wrong: a small nav-predicate change, zero data-shape impact.
  - [ ] Seat roster count reuses the already-shipped `GET /admin/members` list length rather than
    waiting on `plan-seat-cap` (sibling, still `phase: ground`) — high confidence; explicitly
    instructed by the dispatch to "degrade gracefully."
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── M1: Billing nav group + RBAC-aware visibility ─────────────────────────────

Scenario: Owner sees all three Billing nav items   # M1
  Given a signed-in user with role "owner"
  When the app shell renders
  Then the "Billing" nav group shows "Invoices", "Credits", and "Plan & seats"

Scenario: Member's nav hides Invoices but keeps Credits and Plan & seats   # M1, R1
  Given a signed-in user with role "member"
  When the app shell renders
  Then the "Billing" group shows "Credits" and "Plan & seats" only
  And "Invoices" is absent from the rendered nav (never a disabled/greyed link)

Scenario: Operator's nav still shows Invoices (fail-open UX, gateway is the real gate)   # M1, R2
  Given a signed-in user with role "operator" (lacks INVOICES_READ)
  When the app shell renders
  Then the "Billing" group shows "Invoices" alongside "Credits" and "Plan & seats"
  And no security decision was made client-side — the link's mere presence discloses nothing
    cross-tenant (unlike the superadmin-only Platform group)

# ── M2/M9/R1/R2: Invoices list ────────────────────────────────────────────────

Scenario: Invoices list renders a keyset-paginated table   # M2
  Given a tenant with 3 issued invoices
  When the tenant admin opens /app/invoices
  Then a table shows Period, a status seal, and Total for each invoice, newest first
  And clicking a row navigates to /app/invoices/{that invoice's id}

Scenario: Invoices list next/previous mirrors the Logs Explorer cursor idiom   # M2
  Given a tenant with more invoices than one page
  When the admin clicks "Next"
  Then the next keyset page loads via the SAME cursorStack idiom LogsExplorerPage uses
  And clicking "Previous" returns to the prior page without an extra network round trip

Scenario: Empty invoices list shows an informative empty state, not a blank table   # M2 (edge)
  Given a brand-new tenant with zero invoices
  When the admin opens /app/invoices
  Then an Empty state renders: "No invoices yet" + a description that a monthly statement appears
    after the tenant's first billable usage
  And no table skeleton or pagination controls render

Scenario: A member visiting /app/invoices directly gets an inline forbidden message   # R1
  Given a signed-in user with role "member" (Invoices link absent from their own nav, M1)
  When they navigate to /app/invoices directly (e.g. a stale bookmark)
  Then the page renders ErrorState "You don't have access to Invoices" (not a raw 403 JSON body)
  And no invoice data is fetched or rendered

Scenario: An operator visiting Invoices via the (shown) nav link gets the same inline message   # R2
  Given a signed-in user with role "operator" (Invoices link SHOWN per M1)
  When they click "Invoices" and the gateway returns 403
  Then the page renders the SAME ErrorState "You don't have access to Invoices" as R1

# ── M3/M10: Invoice detail — visibly-immutable financial-document idiom ──────

Scenario: An issued invoice renders a visibly-immutable seal and no edit affordance   # M3
  Given an invoice with status "issued"
  When the admin opens its detail page
  Then a success-variant seal Badge reads "Issued" with a lock icon
  And no button, input, or other control on the page implies the document can be edited

Scenario: A draft invoice renders a distinct, non-final seal   # M3 (edge)
  Given an invoice with status "draft"
  When the admin opens its detail page
  Then a secondary-variant Badge reads "Draft" (visually distinct from "Issued")

Scenario: Invoice lines render grouped, tabular-nums amounts that trace to the invoice total   # M3, M10
  Given an invoice with 4 lines grouped by (model, team, key, tags)
  When the detail page renders
  Then each line's Amount column uses font-mono tabular-nums formatting via formatUsd
  And the footer Total row is rendered from total_usd verbatim (never re-summed client-side)

Scenario: Corrections render as signed deltas, never mutating the original lines   # M3 (edge)
  Given an invoice with one correction (delta_usd = -12.50, reason "duplicate line")
  When the detail page renders
  Then the Corrections section shows the signed delta, reason, created_by, and created_at
  And the original Lines table is completely unchanged by the correction's presence

Scenario: An invoice with zero corrections shows its own empty state, not an omitted section   # M3 (edge)
  Given an invoice with no corrections
  When the detail page renders
  Then the Corrections section renders "No corrections" instead of disappearing entirely

# ── M4: Evidence drawer — reuses Logs-Explorer chrome, new paginated body ────

Scenario: Opening a line's evidence drawer fetches and lists its usage rows   # M4
  Given an invoice line grouped by (model="gpt-4o-mini", team=null, key=K1, tags={})
  When the admin clicks that line's "View evidence" action
  Then the drawer opens and lists the underlying usage_records rows (created_at, tokens, cost_usd,
    request_id) for that exact grouping key
  And the drawer does NOT render any link out to the Logs Explorer page

Scenario: Evidence drawer paginates like the Logs Explorer drawer's list idiom   # M4
  Given a line whose evidence spans more than one page
  When the admin clicks "Next" inside the drawer
  Then the next keyset page of usage rows loads without closing the drawer

Scenario: Closing the evidence drawer returns focus to the triggering control   # M4
  Given the evidence drawer is open, triggered from a specific line's "View evidence" button
  When the admin presses Escape
  Then focus returns to that exact "View evidence" button (not the document body)

Scenario: An unknown or cross-tenant line id shows a not-found state inside the drawer   # M4, R3
  Given a line_id that does not belong to this invoice
  When the evidence drawer attempts to open
  Then it renders ErrorState "Evidence not found" instead of a raw 404 body
  And the invoice detail page beneath it is unaffected

# ── M5/R4: Export ─────────────────────────────────────────────────────────────

Scenario: Downloading an invoice as PDF uses a plain link, no new BFF code   # M5
  Given an issued invoice's detail page
  When the admin clicks "Download PDF"
  Then the browser navigates to /api/gw/admin/invoices/{id}/export?format=pdf
  And the BFF's existing binary-passthrough branch forwards the PDF body and Content-Disposition
    header verbatim (no new route file needed)

Scenario: The UI never constructs an export link with an invalid format   # R4
  Given the invoice detail page's two export links
  When they are rendered
  Then their href query strings contain only "format=pdf" or "format=csv", never any other value

# ── M6/R5: Credits ────────────────────────────────────────────────────────────

Scenario: Credits page shows a hero balance and a paginated history   # M6
  Given a tenant with balance_usd=42.50, grace_usd=5.00, and 3 ledger entries
  When the admin opens /app/credits
  Then the hero StatCard shows "$42.50" (tabular-nums) with a note "+ $5.00 grace"
  And the history table lists the 3 entries (Date, Type badge, Amount, Balance after)

Scenario: A brand-new tenant with zero ledger activity sees the informative empty state   # M6 (edge)
  Given a tenant with balance_usd=0.00 and zero ledger entries
  When the admin opens /app/credits
  Then an Empty state renders: "No credit activity yet" + "Your platform operator manages credit
    top-ups — contact them to add credits."
  And the Credits nav item and page are still fully visible (never hidden)

Scenario: Platform-wide credits are disabled (kill-switch off) — same honest empty state   # R5
  Given credits_gate_enabled is False platform-wide (no per-tenant API signal exists for this)
  When the admin opens /app/credits for a tenant that has therefore never held/settled anything
  Then the SAME "No credit activity yet" Empty state renders as the brand-new-tenant case
  And the UI never claims or implies credits are "disabled" (a claim it cannot actually verify)

# ── M7/M8/R6/R7: Plan & seats ──────────────────────────────────────────────────

Scenario: A planned tenant sees its plan, budget meter, allowlist, features, and seat count   # M7, M8
  Given a tenant assigned plan "team" (budget_usd_monthly_default=500.00, model_allowlist=null,
    feature_flags=["logs_explorer","batch"], seat_cap=25) with $120.00 spent this month and 6
    active members
  When the admin opens /app/plan
  Then the page shows plan display_name "Team", a budget meter at $120.00 of $500.00, "All models"
    (allowlist is null), badges "logs_explorer"/"batch", and "6 of 25 seats"

Scenario: An unplanned tenant sees an honest "no plan" state, not an error   # M7, R6
  Given a tenant with plan_id = NULL and its own explicit budget_usd_monthly=null
  When the admin opens /app/plan
  Then the page shows "No plan assigned — usage governed by tenant-level defaults only"
  And the budget meter still renders using the resolved (tenant-level) effective budget, showing
    "Unlimited" when that is also null
  And nothing on the page reads as an error or misconfiguration

Scenario: Budget meter renders "Unlimited" text only when the ceiling is null, never a fabricated %   # M7
  Given a tenant whose resolved effective_budget_usd_monthly is null
  When the EntitlementMeter renders
  Then it shows the spent amount and the word "Unlimited" — no progressbar role, no percentage

Scenario: Seat-billing has not frozen yet — the pricing slot degrades gracefully   # R7
  Given seat-billing (a sibling task) has not yet frozen its own contract at build time
  When the Plan & seats page renders
  Then the "Per-seat pricing" region shows an inert placeholder note ("Seat pricing coming soon")
  And no network request is attempted against a non-existent seat-pricing endpoint
  And the rest of the page (plan, meter, allowlist, features, seat count) renders normally

# ── M11: Accessibility ────────────────────────────────────────────────────────

Scenario: All three Billing pages and the evidence drawer pass axe with zero serious/critical hits   # M11
  Given each of /app/invoices, /app/invoices/{id}, /app/credits, /app/plan, and the open evidence
    drawer, rendered with representative data
  When an automated axe scan runs against each
  Then zero serious or critical violations are reported on any of them

# ── M12: Responsive ────────────────────────────────────────────────────────────

Scenario: Invoice lines table scrolls horizontally inside its own container at narrow widths   # M12
  Given a narrow (mobile-width) viewport
  When the invoice detail page's Lines table overflows its column set
  Then the table scrolls horizontally within its own bordered container
  And the page body itself never scrolls horizontally, matching LogsTable's existing convention
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── NEW backend endpoint (M8) — the ONE new HTTP surface this task introduces ────

GET /admin/plan
  # tenant-self, any authenticated role (owner/admin/operator/billing_admin/viewer/member/
  # superadmin) — mirrors gateway/budgets/api/router.py:get_budget's RBAC exactly (no
  # require_permission call, only get_identity)
  200 -> {
    plan: { id: uuid, name: string, display_name: string, seat_cap: int|null,
            rpm_limit_default: int|null, tpm_limit_default: int|null } | null,
    resolved: { effective_budget_usd_monthly: string|null,   # Decimal-as-string, mirrors every
                                                                # other money field codebase-wide
                plan_model_allowlist: string[]|null,
                plan_feature_flags: string[] }
  }
  401 -> { error: "ERR_AUTH_INVALID_TOKEN" }
  # NO 403 case for this endpoint — read-only, any authenticated role, R6 (unplanned tenant)
  # returns plan: null with a 200, never an error

  Access pattern (2 reads, zero new query shape, admin/config path — not the hot proxy path):
    1. resolved = await plan_entitlement_resolver.resolve(identity.tenant_id)
       # CITED verbatim: gateway/tenants/domain/entitlements.py:resolve_entitlements via
       # gateway/tenants/domain/ports.py:PlanEntitlementResolver (plan-enforcement §3, FROZEN @ v1)
    2. IF resolved.plan_id is not None:
         SELECT id, name, display_name, seat_cap, rpm_limit_default, tpm_limit_default
         FROM plans WHERE id = :plan_id
       ELSE: plan = null
  New file: apps/gateway/src/gateway/tenants/api/plan_router.py
    plan_router = APIRouter(prefix="/admin/plan", tags=["plans"])
  Registration: apps/gateway/src/gateway/main.py gains one
    app.include_router(plan_router) alongside the existing include_router block.
  Schema: no new table, no new column, no new error_catalog.py entry — a pure composition of two
    already-frozen reads (plan-enforcement's resolver port + plan-catalog's own `plans` table).

# ── Reused, CITED verbatim (NOT redefined) — owned by their sibling FROZEN @ v1 contracts ────

GET /admin/invoices?limit=&cursor=                                    (invoice-generation §3)
GET /admin/invoices/{invoice_id}                                      (invoice-generation §3)
GET /admin/invoices/{invoice_id}/lines/{line_id}/evidence?limit=&cursor=  (invoice-generation §3)
GET /admin/invoices/{invoice_id}/export?format=pdf|csv                (invoice-generation §3)
GET /admin/credits/balance                                            (credits-ledger §3)
GET /admin/credits/history?cursor=&limit=                             (credits-ledger §3)
GET /admin/budget                                                     (budgets, shipped FROZEN @ v1)
GET /admin/members                                                    (already shipped)

# ── UI CONTRACT — routes, components, and which endpoint backs each ──────────────

NAV — apps/dashboard/components/ui/app-shell.tsx (EDIT, additive)
  ONE new NavGroup inserted into NAV_GROUPS, between "Insights" and "Configure":
    { label: "Billing", items: [
        { href: "/app/invoices", label: "Invoices", icon: FileText, minRole: "admin" },
        { href: "/app/credits",  label: "Credits",  icon: Wallet },
        { href: "/app/plan",     label: "Plan & seats", icon: BadgeCheck },
    ] }
  Zero changes to any existing NavGroup, itemVisible(), or showPlatformNav() (M1).

ROUTE /app/invoices                          NEW   apps/dashboard/app/(app)/app/invoices/page.tsx
  renders components/invoices/InvoicesListPage.tsx
  backs: GET /admin/invoices -> keyset table (Period · status seal · Total), row -> detail (M2)
  states: Loading | ErrorState (403 -> "You don't have access to Invoices", 404 impossible here) |
    Empty ("No invoices yet") | success table with LogsExplorerPage-shaped cursorStack pager

ROUTE /app/invoices/[invoiceId]              NEW   apps/dashboard/app/(app)/app/invoices/
                                                      [invoiceId]/page.tsx
  renders components/invoices/InvoiceDetailPage.tsx
  backs: GET /admin/invoices/{id} -> header (period + InvoiceStatusSeal) + InvoiceLinesTable +
    InvoiceCorrectionsTable + Total/corrected-total footer + 2 export <a> links (M3, M5, M10)
  states: Loading | ErrorState (403 "You don't have access to Invoices" | 404 "Invoice not found") |
    success

  component: components/invoices/InvoiceStatusSeal.tsx
    InvoiceStatusSeal({ status: "draft" | "issued" })
      "issued" -> Badge variant="success" + Lock icon + "Issued" + sr-only
        " — this document is final and cannot be edited"
      "draft"  -> Badge variant="secondary" + "Draft"

  component: components/invoices/InvoiceLinesTable.tsx
    InvoiceLinesTable({ lines: InvoiceLineItem[], onViewEvidence: (lineId: string) => void })
      columns: Model · Team · Key · Tags · Requests · Tokens (prompt/completion) · Amount
        (tabular-nums, formatUsd) · "View evidence" icon-button per row (M3, M4)

  component: components/invoices/InvoiceCorrectionsTable.tsx
    InvoiceCorrectionsTable({ corrections: InvoiceCorrectionItem[] })
      corrections.length === 0 -> "No corrections" (not an omitted section)
      else -> table (signed delta_usd w/ up/down icon mirroring StatCard's delta tone, reason,
        created_by, created_at) (M3, edge)

  component: components/invoices/InvoiceEvidenceDrawer.tsx
    InvoiceEvidenceDrawer({ invoiceId: string, lineId: string | null, onClose: () => void })
      lineId === null -> closed (mirrors LogDetailDrawerProps' logId convention)
      fetches GET /admin/invoices/{invoiceId}/lines/{lineId}/evidence, keyset cursorStack (client,
        mirrors LogsExplorerPage)
      renders: Loading | ErrorState (404 -> "Evidence not found") | Table of UsageEvidenceItem rows
        (created_at, model_id, prompt_tokens/completion_tokens, cost_usd tabular-nums, request_id
        font-mono) + Next/Previous
      focus-return: captures document.activeElement on lineId's null->non-null transition (mirrors
        LogDetailDrawer.tsx:216-225 verbatim), restores it in onCloseAutoFocus on close (M4)
      NO link to Logs Explorer is ever rendered inside this drawer (Framings)

ROUTE /app/credits                            NEW   apps/dashboard/app/(app)/app/credits/page.tsx
  renders components/credits/CreditsPage.tsx
  backs: GET /admin/credits/balance -> hero StatCard (balance_usd tabular-nums + grace note) (M6)
         GET /admin/credits/history -> components/credits/CreditsHistoryTable.tsx, keyset-paginated
           (Date · entry_type Badge · Amount · Balance after)
  states: Loading | ErrorState (generic — no expected-403 case, any role passes) | Empty
    ("No credit activity yet" / "Your platform operator manages credit top-ups — contact them to
    add credits.") when entries.length === 0, covering BOTH R5 (kill-switch off) and the
    brand-new-tenant case identically | success table

ROUTE /app/plan                               NEW   apps/dashboard/app/(app)/app/plan/page.tsx
  renders components/plan/PlanSeatsPage.tsx
  backs: GET /admin/plan (M8, this task's own new endpoint) + GET /admin/budget (spent_usd_month,
    reused) + GET /admin/members (roster length, reused)
  regions:
    - plan name (display_name, or "No plan assigned — usage governed by tenant-level defaults
      only" when plan === null) (M7, R6)
    - components/plan/EntitlementMeter.tsx:
        EntitlementMeter({ label: string, valueUsd: string, ceilingUsd: string | null })
          ceilingUsd === null -> label + valueUsd + "Unlimited" text ONLY, no progressbar (mirrors
            MemoryScoreBar's null-score "text match" branch — never fabricate a percentage)
          ceilingUsd !== null -> role="progressbar" aria-valuenow/aria-valuemin=0/
            aria-valuemax={ceiling}, percentage capped at 100, tone flips to destructive at/over
            ceiling (mirrors StatCard's delta tone convention)
    - model-allowlist readout: plan_model_allowlist === null -> "All models"; else the list
    - feature-flags readout: plan_feature_flags.length === 0 -> "None"; else a Badge per flag
    - seat line: `{memberCount} of {plan.seat_cap ?? "Unlimited"} seats` (roster count from the
      reused GET /admin/members list length; PlatformBudgetTab's "Unlimited" convention)
    - RPM/TPM: 2 plain labeled rows, catalog reference values only ("Requests/min (plan
      reference)" / "Tokens/min (plan reference)") — explicitly NOT a live meter (§0 Ruled-out)
    - "Per-seat pricing" slot: an inert placeholder note ("Seat pricing coming soon") — reserved
      extension point for seat-billing (wave-2); never a fetch, never a layout gap (R7)
  states: Loading | ErrorState (generic — any role passes) | success (plan or no-plan variant, both
    non-error, per R6)
```

Glossary deltas: none — every domain term (invoice, invoice line, evidence link, credit ledger,
  hold, grace, entitlement resolution, plan default, plan-gated feature) is already defined by the
  three sibling FROZEN contracts this task consumes. Two UI-only CONVENTION notes (not GLOSSARY
  terms, recorded here for the next Billing-adjacent task — `margin-dashboard`, wave-2 — to match):
  every currency/token column across the Billing surfaces renders `font-mono tabular-nums`; every
  null ceiling renders the single word "Unlimited" (mirrors `PlatformBudgetTab.tsx`'s own shipped
  convention).

Status: FROZEN @ v1 — approved by Tin Dang
Least-sure flag surfaced at freeze: [contract] Inventing a NEW tenant-self `GET /admin/plan`
  endpoint (M8) to back the Plan & seats page — `plan-enforcement`'s own FROZEN @ v1 contract
  explicitly named "no HTTP endpoint" as a deliberate Non-goal for its `PlanEntitlementResolver`
  port, flagging (not silently dropping) exactly this future need. This task picks that up
  additively, in its OWN contract, never touching `plan-enforcement`'s frozen file — but it IS new
  backend surface born inside what was framed as a "UI task." Tin should confirm this is the right
  home for it (vs. a small dedicated task, or folding it into a `plan-enforcement` fast-follow)
  before BUILD. Cost if wrong: cheap — the resolver call underneath is unchanged either way; only
  which task's §5 Scope owns the router file moves.

DECIDED at freeze review (2026-07-12, Tin): billing-ui OWNS `GET /admin/plan` — one thin additive router
over the already-frozen `PlanEntitlementResolver` port + `plans` lookup; endpoint and its only
consumer stay in one task / one verify. `plan-enforcement`'s frozen contract is not touched.
Reported: no — this design-only draft is presented to Tin separately from the wave-1 batch freeze
  (billing-ui is wave-2, depends-on all three wave-1 contracts, which are already FROZEN @ v1).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: backend `apps/gateway` suite holds its existing 80% gate (`--cov-fail-under=80`,
  unchanged); dashboard `apps/dashboard` suite holds its existing 80% lines threshold
  (`vitest.config.ts` coverage.thresholds.lines, unchanged) — no new relaxation of either.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  Backend — `apps/gateway/tests/plans/test_plan_router.py` (12 tests, RED confirmed via a
  temporary router/registration revert -> 404 for the right reason, then restored):
  - test_any_authenticated_role_passes[owner|admin|operator|billing_admin|viewer|member]: arrange a
    signed-up tenant / act GET /admin/plan with each role's JWT / assert 200 · covers: M8
  - test_unauthenticated_request_is_rejected: act GET /admin/plan, no Authorization header / assert 401
  - test_unplanned_tenant_with_explicit_budget_returns_plan_null_and_tenant_budget: arrange
    plan_id NULL + tenant budget=30.00 / assert plan:null, resolved.effective_budget="30.00" · covers: R6
  - test_unplanned_tenant_with_no_explicit_budget_resolves_null_ceiling: arrange no plan, no budget /
    assert resolved.effective_budget_usd_monthly is null · covers: M7
  - test_planned_tenant_returns_plan_summary_and_resolved_entitlements: arrange plan "team" (seat_cap
    25, budget 500, features) / assert full PlanSummary + resolved shape verbatim · covers: M7, M8
  - test_planned_tenant_explicit_budget_still_wins_over_plan_default: arrange plan default 500 + tenant
    explicit 10 / assert resolved=10.00 (precedence unchanged) · covers: M7
  - test_planned_tenant_with_model_allowlist_returns_it_verbatim: arrange plan with a model_allowlist /
    assert resolved.plan_model_allowlist echoes it

  Dashboard — `apps/dashboard/tests/billing-nav.test.tsx` (5 tests, M1/R1/R2 — pass-on-write since
    app-shell.tsx's additive edit was verified structurally first; regression-locked here):
  - test_billing_group_sits_between_insights_and_configure_with_the_right_gating: covers M1
  - test_zero_changes_to_the_26_pre_existing_nav_items_hrefs: covers M1 (non-regression)
  - test_owner_sees_all_three_billing_nav_items / test_members_nav_hides_invoices_but_keeps_credits_
    and_plan_seats / test_operators_nav_still_shows_invoices_fail_open: covers M1, R1, R2

  `apps/dashboard/tests/billing-invoices.test.tsx` (17 tests, RED confirmed via MODULE_NOT_FOUND):
  - test_list_renders_keyset_paginated_table_and_row_click_navigates,
    test_next_previous_mirrors_the_cursor_stack_idiom, test_empty_invoices_list_shows_informative_
    empty_state, test_forbidden_access_shows_the_shared_inline_message, test_axe_no_serious_violations
    · covers: M2, M9, R1, R2, M11
  - test_issued_invoice_renders_visibly_immutable_seal_and_no_edit_affordance,
    test_draft_invoice_renders_a_distinct_non_final_seal, test_lines_render_grouped_tabular_nums_
    amounts_tracing_to_the_total, test_corrections_render_as_signed_deltas_never_mutating_original_
    lines, test_zero_corrections_shows_its_own_empty_state_not_an_omitted_section,
    test_unknown_or_cross_tenant_invoice_id_shows_not_found, test_axe_no_serious_violations
    · covers: M3, M10, R3, M11
  - test_opening_a_lines_evidence_action_fetches_and_lists_usage_rows,
    test_drawer_paginates_without_closing, test_closing_the_drawer_returns_focus_to_the_triggering_
    control, test_unknown_line_id_shows_not_found_inside_drawer_detail_page_unaffected · covers: M4, R3
  - test_export_links_point_at_the_binary_passthrough_route_zero_new_bff_code,
    test_export_hrefs_never_carry_any_format_other_than_pdf_or_csv · covers: M5, R4
  - test_lines_table_scrolls_horizontally_inside_its_own_container · covers: M12

  `apps/dashboard/tests/billing-credits.test.tsx` (4 tests, RED confirmed via MODULE_NOT_FOUND):
  - test_hero_balance_and_paginated_history_render,
    test_brand_new_tenant_zero_activity_sees_informative_empty_state_nav_still_visible,
    test_platform_wide_kill_switch_off_renders_the_same_honest_empty_state, test_axe_no_serious_
    violations · covers: M6, R5, M11

  `apps/dashboard/tests/billing-plan.test.tsx` (5 tests, RED confirmed via MODULE_NOT_FOUND):
  - test_planned_tenant_sees_plan_budget_allowlist_features_and_seat_count,
    test_seat_billing_unfrozen_pricing_slot_degrades_gracefully · covers: M7, M8, R7
  - test_unplanned_tenant_sees_honest_no_plan_state_not_an_error,
    test_budget_meter_renders_unlimited_text_only_when_ceiling_is_null_never_a_percentage · covers: R6, M7
  - test_axe_no_serious_violations · covers: M11

  Amended (pre-existing, not this task's own scenario, but its own documented "UPDATED by <task>"
  convention required a count bump for the +3 new nav items) —
  `apps/dashboard/tests-bff/nav-role-filter.test.tsx`: 5 pre-existing tests' hardcoded link counts
  raised (member 10→12, admin 21→24, owner/unknown 22→25 ×3) with a new "UPDATED by billing-ui" doc
  comment, mirroring the ~15 prior nav-adding tasks' own identical amend-in-place precedent in this
  same file.
</test_plan>

Tests live in: `apps/gateway/tests/plans/` (backend, 12 tests) · `apps/dashboard/tests/` (dashboard,
  4 new files: billing-nav/billing-invoices/billing-credits/billing-plan, 31 tests) ·
  `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (pre-existing, amended, not counted as new).
  MUST run red (missing implementation) before Build — confirmed: backend via a temporary
  router-file-move + main.py-registration revert (12/12 -> 404 for the right reason, then restored);
  dashboard via MODULE_NOT_FOUND on the new component imports (billing-invoices/credits/plan) —
  billing-nav ran green-on-write since its only dependency (app-shell.tsx's additive NAV_GROUPS
  edit) was verified structurally before the test was written, so it stands as a regression lock
  rather than a red-first scenario test.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/tenants/api/plan_router.py` (new) ·
  `apps/gateway/src/gateway/main.py` (additive: 1 import + 1 include_router line) ·
  `apps/gateway/tests/plans/` (new) ·
  `apps/dashboard/components/invoices/` (new) ·
  `apps/dashboard/components/credits/` (new) ·
  `apps/dashboard/components/plan/` (new) ·
  `apps/dashboard/app/(app)/app/invoices/` (new) ·
  `apps/dashboard/app/(app)/app/credits/` (new) ·
  `apps/dashboard/app/(app)/app/plan/` (new) ·
  `apps/dashboard/components/ui/app-shell.tsx` (additive: 1 NavGroup + 3 lucide icon imports) ·
  `apps/dashboard/tests/billing-nav.test.tsx`, `apps/dashboard/tests/billing-invoices.test.tsx`,
    `apps/dashboard/tests/billing-credits.test.tsx`, `apps/dashboard/tests/billing-plan.test.tsx` (new) ·
  `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (amended: hardcoded link counts only, per its
    own "UPDATED by <task>" convention — never a weakened assertion).
  Explicitly NOT touched: any file under `plan-enforcement`'s own frozen scope (ports.py,
    entitlements.py, plan_entitlement_resolver.py — imported only), `invoice-generation`'s or
    `credits-ledger`'s own routers/schemas (consumed via bffGet only).

Strategy (ordered batches):
  1. Backend first (small): read the 3 sibling FROZEN contracts + ground anchors (budgets router
     RBAC precedent, PlanEntitlementResolver port, plans ORM columns) verbatim; write
     `plan_router.py` (thin composition, 2 reads) + register in `main.py`; write
     `tests/plans/test_plan_router.py`; confirm RED (temporary revert), confirm GREEN (restore).
  2. Dashboard ground: read app-shell.tsx/LogDetailDrawer.tsx/LogsExplorerPage.tsx/
     MemoryScoreBar.tsx/bff-client.ts/format.ts/table.tsx/dialog.tsx/card.tsx/badge.tsx/
     stat-card.tsx/page-header.tsx in full — every visual primitive this task needs already ships.
  3. Nav: additive NavGroup edit to app-shell.tsx (Billing between Insights/Configure).
  4. Write the 4 dashboard test files (one test per §2 scenario); confirm RED
     (MODULE_NOT_FOUND for 3 of 4 files; the nav file was a structural regression-lock, not a
     scenario red, since its dependency was a already-verified small additive edit).
  5. Build components bottom-up: InvoiceStatusSeal -> InvoiceLinesTable -> InvoiceCorrectionsTable
     -> InvoiceEvidenceDrawer -> InvoicesListPage -> InvoiceDetailPage -> CreditsHistoryTable ->
     CreditsPage -> EntitlementMeter -> PlanSeatsPage -> the 4 thin Next.js route wrappers.
  6. Fix the fallout in `tests-bff/nav-role-filter.test.tsx` (pre-existing, hardcoded link counts;
     this file's OWN header comment documents ~15 prior "UPDATED by <task>" amendments as the
     sanctioned pattern for a legitimate additive nav change — not a frozen-contract edit).
  7. tsc --noEmit, eslint, ruff, pyright on every touched file; full dashboard `npx vitest run`
     (137 files / 1228 tests); backend regression subset (budgets/credits_ledger/
     invoice_generation/plan_catalog/plan_enforcement/plans/tenants, 172 tests) + a full-suite
     `--collect-only` pass (3173 tests collected, zero import errors) to catch any main.py-wide
     breakage cheaply before committing to a full ~3000-test run.

Persona (required): `frontend-engineer` (`.add/personas/frontend-engineer.md`) — the dashboard
  implementation lens (BFF trust-boundary discipline, SSR-safety, design-token fidelity); its
  "shared-primitive fix applied once" rule shaped M12 (Table already wraps itself in
  `overflow-auto` — zero new scroll-wrapper code needed) and its "frozen structural contract"
  rule shaped the nav-role-filter.test.tsx fix (update the documented count, never the
  role-visibility assertions themselves).
Spawn isolation (default): none — no subagent was spawned; all work done directly in this
  dedicated worktree (already isolated at the top level).
Known-problem fixes:
  - trap: calling `setState` synchronously inside a `useEffect` (InvoiceEvidenceDrawer's
    per-line cursor reset) trips `react-hooks/set-state-in-effect` → fix: the "adjust state
    during render" escape hatch (compare `lineId` to a tracked `lastOpenedLineId` during render,
    mirrors `LogsExplorerPage`'s own `lastGoodPage` precedent), not a useEffect.
  - trap: `GET /admin/members` named in §0/§3 does not exist in the current tree (ground-anchor
    drift since the contract was drafted) → fix: verified live, the real shipped endpoint is
    `GET /admin/users` returning `{ users: TenantUser[] }` (confirmed via `MembersPage.tsx` and
    `main.py`'s `users_router` registration); built against the real endpoint, named in §6
    Live-verify below rather than left silent.
  - trap: `ruff check --fix` on `main.py` reorders an unrelated PRE-EXISTING out-of-order import
    block (gateway.core.* misplaced after gateway.credits.*) → fix: reverted that hunk, kept only
    the 2 lines this task actually adds (minimal diff, lower merge-conflict surface with the two
    sibling tasks — margin-dashboard, plan-seat-cap — building on the same main.py concurrently).
Strategy actually used: as planned (batches 1-7 above), with one addition not originally
  anticipated: batch 6 (the nav-role-filter.test.tsx count fix) was discovered only by running the
  FULL dashboard suite post-build, not predicted at plan time — the lesson: an additive nav change
  still requires a full-suite run, not just the new task's own tests, because the shell is a
  cross-cutting shared surface with its own hardcoded-count regression tests.
Safety rule (feature-specific): the client renders exactly what the API returns and never
  re-derives a money total (an invoice's `total_usd`/`corrected_total_usd` is trusted verbatim,
  never re-summed from `lines[]`/`corrections[]` in the UI) — enforced by construction: neither
  `InvoiceLinesTable` nor `InvoiceCorrectionsTable` accepts or computes a total; the page composes
  the footer directly from the two API-supplied total fields.
Code lives in: `apps/gateway/src/gateway/tenants/api/plan_router.py` ·
  `apps/dashboard/components/{invoices,credits,plan}/` · `apps/dashboard/app/(app)/app/{invoices,credits,plan}/`.
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.
  Held: zero new dependency added (backend: stdlib + already-imported gateway/sqlalchemy/pydantic
  modules only; dashboard: zero new npm package, every component built from already-shipped
  `@/components/ui` primitives + `@tanstack/react-query` + `lucide-react`, all pre-existing).

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

Persona: `tdd-verifier` (advisor flow; no closer-fit persona found for a UI+thin-router
  verify pass) — refute-the-green stance, severity tags 🔴 blocker · 🟡 concern · 💭 note.

- [x] all tests pass — backend `tests/plans/test_plan_router.py` 12/12 green (real Postgres,
  `gateway_test_verify_bui`); dashboard 4 new files + amended `nav-role-filter.test.tsx`
  38/38 green; FULL dashboard suite 138 files / 1234 tests green (`./node_modules/.bin/vitest
  run`, real binary via a symlink to the sibling worktree's `node_modules` — identical
  `package.json`/`package-lock.json` diffed byte-equal first, symlink removed before commit);
  backend regression subset (budgets/credits_ledger/invoice_generation/plan_catalog/
  plan_enforcement/plans/tenants) 172/172 green, matching §5's own claimed counts exactly.
  PLUS 2 independent NEW backend probes (`tests/plans/test_verify_plan_router.py`, this
  verify pass — SUPERADMIN role coverage the builder's own parametrize omitted + an explicit
  2-tenant isolation probe) 2/2 green, and 1 NEW dashboard probe
  (`tests/billing-plan-verify-probe.test.tsx` — reproduces F1 below) 1/1 green. Full dashboard
  suite re-run with the new probe included: 139 files / 1235 tests green.
- [x] coverage did not decrease — `vitest.config.ts` and the backend's `--cov-fail-under=80`
  config: zero diff between `ed5ec82` (pre-billing-ui) and `5edd931` (post). Subset-run
  coverage% dips are expected/non-diagnostic (full-suite gate is what's held).
- [x] no test or contract was altered during build — `git diff ed5ec82 5edd931 --stat`: only
  `nav-role-filter.test.tsx` touched among pre-existing files, and its diff is exactly the
  declared "UPDATED by billing-ui" count-bump (12/24/25) + new assertions for Credits/Plan
  seats/Invoices-hidden-from-member — zero weakened assertion. `plan-enforcement`'s frozen
  files (`ports.py`, `entitlements.py`, `plan_entitlement_resolver.py`) diff empty.
- [x] the green was EARNED — self refute-read: read all 4 new dashboard test files + the
  backend suite in full; assertions target real rendered text/roles/hrefs/focus, not
  component internals; the one duplicate-fixture test (`test_platform_wide_kill_switch_off_
  renders_the_same_honest_empty_state` mirrors `test_brand_new_tenant...` byte-for-byte) is
  DELIBERATE, not vacuous — R5's own rule is "no API signal distinguishes the two states," so
  an identical fixture/assertion pair is the correct proof, not a copy-paste miss.
- [x] concurrency / timing — read-only surface, no writes, no shared mutable state; the 3
  independent `useQuery` calls in `PlanSeatsPage` (plan/budget/users) race safely (react-query
  isolates each), and `isLoading`/`isError` deliberately exclude the (degrading) users query.
  `InvoiceEvidenceDrawer`'s per-line cursor reset uses React's documented adjust-during-render
  pattern (not a `useEffect`), avoiding a stale-cursor flash — no residue found.
- [x] no exposed secrets, injection openings, or unexpected dependencies — `plan_router.py`
  uses parameterized `text()` (no string-built SQL), never constructs an `Authorization`
  header client-side (`bffGet`-only, confirmed by reading the module), zero new npm/pip
  dependency (grep confirms only already-imported modules). No security HARD-STOP.
- [x] layering & dependencies — router → frozen `PlanEntitlementResolver` port → `plans` table
  read, matches the codebase's existing thin-router convention (`budgets/api/router.py`
  precedent, cited and followed); dashboard components call `bffGet` exclusively, zero direct
  fetch/Authorization construction. No architecture residue.
- [ ] a person reviewed and approved the change — pending Tin's read of this report (contract
  itself was FROZEN @ v1 by Tin at design time; this is the separate BUILD-output review).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] `GET /admin/plan` returns 200 for every role and 401 unauthenticated, never a 403 — confirmed
  by `tests/plans/test_plan_router.py::test_any_authenticated_role_passes[*]` (6 roles) +
  `test_unauthenticated_request_is_rejected`, all green against real Postgres.
- [x] An unplanned tenant (`plan_id IS NULL`) gets `plan: null` with a 200, never an error, and its
  own explicit budget still resolves — confirmed by `test_unplanned_tenant_with_explicit_budget_
  returns_plan_null_and_tenant_budget` (200, `plan: null`, `resolved.effective_budget_usd_monthly:
  "30.00"`).
- [x] The Billing nav group sits between Insights and Configure with the exact RBAC split (Invoices
  admin-only, Credits/Plan & seats any-role) — confirmed by `billing-nav.test.tsx`'s 5 tests +ally by
  `tests-bff/nav-role-filter.test.tsx`'s updated counts (member 12, admin 24, owner/unknown 25).
- [x] An issued invoice never renders an edit affordance (no button/input implying mutability) —
  confirmed by `test_issued_invoice_renders_visibly_immutable_seal_and_no_edit_affordance`
  (`queryByRole("button",{name:/^edit$/i})` and `queryByRole("textbox")` both absent).
- [x] Every invoice line's Amount and the footer Total both render `formatUsd(total_usd)` verbatim
  (never re-summed) — confirmed by `test_lines_render_grouped_tabular_nums_amounts_tracing_to_the_
  total` (both the line cell and the footer show the identical `$1,204.55` from the fixture).
- [x] Closing the evidence drawer returns focus to the exact triggering "View evidence" button —
  confirmed by `test_closing_the_drawer_returns_focus_to_the_triggering_control`
  (`document.activeElement === trigger` after `{Escape}`).
- [x] The two export links carry ONLY `format=pdf`/`format=csv` and `download`, zero new BFF route
  file — confirmed by `test_export_links_point_at_the_binary_passthrough_route_zero_new_bff_code` +
  a manual re-read of `app/api/gw/[...path]/route.ts`'s `isBinaryPassthrough` branch (unedited).
- [x] Credits renders the SAME empty state for "genuinely unused" and "kill-switch off" (no API
  signal distinguishes them, never a fabricated "disabled" claim) — confirmed by
  `test_platform_wide_kill_switch_off_renders_the_same_honest_empty_state`
  (`queryByText(/disabled/i)` absent).
- [x] The Plan & seats budget meter renders "Unlimited" text with NO `role="progressbar"` when the
  ceiling is null — confirmed by `test_budget_meter_renders_unlimited_text_only_when_ceiling_is_
  null_never_a_percentage` (`queryByRole("progressbar")` absent).
- [x] All 4 Billing surfaces + the evidence drawer pass axe with zero serious/critical violations —
  confirmed by the 4 `test_axe_no_serious_violations` tests (color-contrast disabled, the
  standing jsdom-canvas convention every sibling page's axe test already carries).
- [x] Zero regression to the pre-existing dashboard/backend suites — confirmed by a full
  `npx vitest run` (137 files / 1228 tests green) and a targeted backend regression subset
  (172 tests green) + a full-suite `--collect-only` pass (3173 tests collected, 0 import errors).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced (min 2 hits: definition + ≥1 consumer/test) —
  `InvoiceStatusSeal`(3) `InvoiceLinesTable`(2) `InvoiceCorrectionsTable`(2)
  `InvoiceEvidenceDrawer`(2) `InvoicesListPage`(3) `InvoiceDetailPage`(5)
  `CreditsHistoryTable`(2) `CreditsPage`(3) `EntitlementMeter`(2) `PlanSeatsPage`(3) — all wired,
  confirmed via `grep -rl` across `app/`+`components/`+`tests/`.
- [x] DEAD-CODE (code) — no orphaned symbol; all 4 new route wrappers (`invoices/page.tsx`,
  `invoices/[invoiceId]/page.tsx`, `credits/page.tsx`, `plan/page.tsx`) import and render their
  page component; `plan_router` is imported + `include_router`'d in `main.py` (confirmed, not
  just declared).
- [x] SEMANTIC (prose) — DESIGN.md read in full section-by-section against the built components
  (see fidelity walk below); TASK.md §0–§6 read in full; not skimmed.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] Every §3 CONTRACT symbol re-resolved in the current tree: `PlanEntitlementResolver`/
  `ResolvedEntitlements` (`domain/ports.py`, `domain/entitlements.py` — untouched, byte-diff
  empty vs pre-billing-ui), `SqlAlchemyPlanEntitlementResolver`, `plans` table columns (all
  present, `test_plan_router.py` exercises them live against real Postgres), `get_budget`'s RBAC
  precedent (mirrored — confirmed no `require_permission` on the new route), `bffGet` +
  `isBinaryPassthrough` (read in full, unedited), `LogDetailDrawer`'s focus-return idiom (lines
  216-238, byte-compared — `InvoiceEvidenceDrawer` structurally identical), `NAV_GROUPS` (additive
  diff only, confirmed via `git diff`).
- [x] **One anchor moved and IS already named, not silent**: §0/§3 cite `GET /admin/members`,
  which does not exist — the real shipped endpoint is `GET /admin/users` (`MEMBERS_MANAGE`-gated,
  owner/admin/superadmin only). The builder documented this drift honestly in §5 Known-problem
  fixes. **Re-verified independently here**: `plan-seat-cap` (sibling, merged into this same
  integration branch before billing-ui) did NOT add a `/admin/members` endpoint or touch
  `users_router.py` — confirmed via `git diff 9c2c204~1 9c2c204 --stat`, zero `users_router`/
  `members` hits. So the drift is stable, not newly compounded — but see 🟡 finding F1 below: the
  drift has a REAL, untested behavioral consequence the builder's fix note doesn't fully surface.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self (add-verify, `tdd-verifier`-persona advisor pass) · adversarially checked: (1) hand-read
  all 4 new dashboard test files + the 12-test backend suite for vacuous asserts / fixture
  overfit — none found, one deliberately-duplicate fixture pair (R5) is correct-by-design, not a
  cheat; (2) re-derived the RBAC/nav-visibility matrix from `authz.py`'s live `ROLE_PERMISSIONS`
  and cross-checked against `nav-role-filter.test.tsx`'s updated counts — matches exactly;
  (3) traced the "never re-sum money client-side" safety rule by reading `InvoiceLinesTable`/
  `InvoiceCorrectionsTable`'s own prop signatures — neither accepts nor computes a total, so the
  rule is enforced by construction, not merely test-asserted; (4) ran the full dashboard suite
  (1234/1234) and a targeted backend regression subset (172/172) myself rather than trusting §5's
  claimed counts — both reproduced exactly. No overfit / stubbed-away logic found. One real
  UNTESTED gap found (F1, seat-count 403-degrade) — it does not invalidate the recorded green
  (no false assertion, no gamed test), but it is a genuine scenario-coverage hole feeding §7.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: self (add-verify)
1. Security: CLEAR — parameterized SQL only (`text()` w/ named params, no interpolation); zero
   `Authorization` header construction client-side (grep confirms `bffGet`-only across all 4
   components); RBAC on the new `GET /admin/plan` matches its own frozen contract exactly
   (any-role, tenant-scoped via `identity.tenant_id`, no cross-tenant reach); zero new
   dependency (backend or npm). No finding.
2. Concurrency: CLEAR — read-only surface, no writes, no shared mutable state; independent
   `useQuery` races in `PlanSeatsPage` are isolated by react-query and the page's `isLoading`/
   `isError` deliberately exclude the degrading `admin-users` query; the evidence drawer's
   cursor-reset uses the documented adjust-during-render pattern, not a `useEffect` race. No
   finding.
3. Architecture: RESIDUE (🟡, non-blocking) — see F1: `GET /admin/plan`'s own §1 Framings
   promised "Plan & seats carries NO minRole (visible to every authenticated role)" and M7
   promised "a seat line combining plan.seat_cap ... against a LIVE roster count." Because the
   ground-anchor endpoint drifted to `MEMBERS_MANAGE`-gated `GET /admin/users`, 4 of 7 roles
   (operator/billing_admin/viewer/member) who CAN reach `/app/plan` will silently see "— of 25
   seats" instead of a live count — a real, honest, non-crashing degrade (good defensive coding)
   but completely UNTESTED (no scenario, no test exercises a `GET /admin/users` 403 on this
   page) and not named in TASK.md §1/§2/§6 anywhere until this verify pass. Not a security or
   correctness bug (no data leak, no crash, no misleading claim) — a coverage/fidelity gap: the
   design's own "live roster count" promise silently downgrades for most non-admin viewers.
Verdict: PASS
Residue: F1 (🟡 concern, architecture/coverage) — untested seat-count degrade for
  operator/billing_admin/viewer/member on `/app/plan`; recommend a regression test + a TASK.md
  §1/§2 note before the next Plan&seats-adjacent task (e.g. `margin-dashboard`) copies this
  pattern blind. Non-blocking: behavior is honest and non-crashing, just unverified.
Binding: advisory — sensitivity: mechanical

### Fidelity walk — DESIGN.md vs built pages (per-page verdict)
- **Nav (§2)**: MATCH — group placement, icons, minRole split exactly as wireframed; RBAC
  matrix (§2 table) reproduced independently from live `authz.py` and matches.
- **Invoices list (§3)**: MATCH — table columns, empty state copy, error state copy, cursor
  pager all match verbatim (copy strings byte-compared against DESIGN.md).
- **Invoice detail (§4)**: MATCH — seal, lines table + evidence icon, corrections
  (empty-not-omitted), footer totals (verbatim, never re-summed), export links, evidence drawer
  chrome/pagination/focus-return all match. 💭 note: DESIGN.md's evidence-drawer subtitle line
  ("gpt-4o-mini · Eng team · key k_a1 · {} tags") is NOT rendered by `InvoiceEvidenceDrawer` —
  the drawer opens with just "Evidence" as its `DialogTitle` and no per-line context header. A
  user with several open lines could lose track of which line's evidence they're viewing once
  the drawer is open (no visible reminder). Low severity — the "View evidence" trigger button is
  per-row and focus returns correctly, but this is a real, if minor, DESIGN.md fidelity gap.
- **Credits (§5)**: MATCH — hero StatCard + grace note (conditional on `grace_usd > 0`, matches
  wireframe's parenthetical), history table columns, unified empty state (R5) all match.
- **Plan & seats (§6)**: MATCH for the planned-tenant layout and the unplanned-tenant variant
  (Seats/RPM-TPM/pricing-slot correctly omitted when `plan === null`, mirroring DESIGN.md's own
  unplanned wireframe which also omits them). 🟡 gap: see F1 — the wireframe's "6 of 25" seat
  count is a LIVE promise the real endpoint cannot honor for most non-owner/admin roles; DESIGN.md
  does not show or discuss a "— of 25" degraded state anywhere.
- **A11y/responsive (§10)**: MATCH — axe suites (5 files) all green; `overflow-auto` wrapper
  confirmed present on `InvoiceLinesTable`'s underlying `Table` via the M12 test; single-`h1`-
  per-page via `PageHeader` confirmed by reading each page component (no second `h1`/`h2`-as-title
  anywhere).

### Findings (severity-tagged)
- 🟡 **F1 — untested seat-count 403-degrade for 4 of 7 roles on `/app/plan`.** `GET /admin/users`
  requires `Permission.MEMBERS_MANAGE` (owner/admin/superadmin only, confirmed in
  `authz.py:87-136`); operator/billing_admin/viewer/member can all reach `/app/plan` (no
  `minRole`, M1) and will see the seat line's roster count silently fall back to "—" (handled
  gracefully in code, `PlanSeatsPage.tsx:70-84`, comment self-documents the degrade) — was ZERO
  test coverage at build time, and it is not named as a scenario in §1/§2 anywhere (only R7's
  "seat-billing unfrozen" placeholder-degrade is tested, a different failure mode). **Now
  reproduced as an executable probe**: `tests/billing-plan-verify-probe.test.tsx` (NEW, this
  verify pass, 1/1 green) — confirms the degrade IS honest (no page-level ErrorState swallows
  the rest of the page) AND confirms the seat count genuinely shows "— of 25 seats", not the
  "6 of 25" the design promises, for a mocked operator-shaped 403. Not a security/correctness
  defect (honest, non-crashing, no data leak) — a real scenario-coverage gap in a task whose own
  M11/M2-style discipline elsewhere is otherwise thorough. Recommend: fold this probe into
  `billing-plan.test.tsx` proper at the next touch + a one-line TASK.md §1 Assumption note,
  forwarded via §7 Spec delta.
- 💭 **F2 — evidence drawer omits its DESIGN.md-specified per-line context subtitle.**
  DESIGN.md §4's ASCII wireframe shows "gpt-4o-mini · Eng team · key k_a1 · {} tags" under the
  drawer's "Evidence" title; the built `InvoiceEvidenceDrawer` renders only "Evidence" +  an
  sr-only description, no visible per-line identifier. Minor UX polish gap, not a correctness or
  a11y issue (the `DialogTitle`/`DialogDescription` pair is still valid); worth a fast-follow,
  not a blocker.
- 💭 **F3 — evidence-drawer "0 rows" empty state is implemented but untested.** DESIGN.md §9
  names it ("should not occur ... but handled"); `InvoiceEvidenceDrawer.tsx:127-128` implements
  it; no test in the 4-file suite exercises it. Matches the design doc's own "should not occur"
  framing — acceptable to leave untested, noted for completeness only.

### GATE RECORD
Reported: no — verify findings are reported here; the orchestrator records the gate outcome
  (this task's own dispatch: "Do NOT gate — orchestrator gates").
Outcome: <RESERVED FOR ORCHESTRATOR — verify recommendation: PASS with 1 non-blocking 🟡
  residue (F1) + 2 💭 notes (F2/F3); zero security findings; zero HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: add-verify (self) · date: 2026-07-12 — pending Tin's human sign-off

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned (batches 1-7 above), with one addition not originally anticipated: batch 6 (the nav-role-filter.test.tsx count fix) was discovered only by running the FULL dashboard suite post-build, not predicted at plan time — the lesson: an additive nav change still requires a full-suite run, not just the new task's own tests, because the shell is a cross-cutting shared surface with its own hardcoded-count regression tests.
- [AI] verify — gate <RESERVED (reviewed by add-verify (self))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

