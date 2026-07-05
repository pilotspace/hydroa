# TASK: Plan admin UI

slug: plan-admin-ui · created: 2026-07-05 · stage: production
milestone: platform-access-plan
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: build   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `PlanResponse` / `PlansListResponse` / `TenantPlanResponse` / `TenantPlanPutRequest`
    (`apps/gateway/src/gateway/tenants/api/platform_plans_router.py:76-101`, read directly this
    session, not taken from plan-catalog's TASK.md prose alone) — the 3 frozen, shipped DTOs this
    UI consumes verbatim: `PlanResponse {id, name, display_name, seat_cap,
    budget_usd_monthly_default, rpm_limit_default, tpm_limit_default}`; `TenantPlanResponse
    {tenant_id, plan: PlanResponse|null, seat_cap}`; `TenantPlanPutRequest {plan_id: uuid|null,
    seat_cap?: int|null}` (OPTIONAL key, 3-state: omitted/null/positive-int).
  - `list_platform_plans` / `get_platform_tenant_plan` / `put_platform_tenant_plan`
    (`platform_plans_router.py:159-305`) — the 3 live, already-registered endpoints: `GET
    /admin/platform/plans`, `GET`/`PUT /admin/platform/tenants/{tenant_id}/plan`. Gate order
    (`_require_target_tenant`, `platform_plans_router.py:142-151`): `require_superadmin` ->
    `authorize_tenant_scope` -> `get_tenant_by_id` (404) -> (PUT only) platform-kind check (403) ->
    plan_id-resolves check (404) -> seat_cap cross-field check (422) -> write ->
    `emit_platform_audit`. `put_platform_tenant_plan`'s own M8/M9 split reads
    `body.model_fields_set` to decide omit-vs-include (`seat_cap_given = "seat_cap" in
    fields_set`) — a PRESENCE check, not a value-equality check.
  - `PLAN_NOT_FOUND` / `PLAN_TENANT_INELIGIBLE` (`apps/gateway/src/gateway/core/error_catalog.py:
    645,650-652`) — `ErrorSpec(404, "ERR_PLAN_NOT_FOUND", "Plan not found")` / `ErrorSpec(403,
    "ERR_PLAN_TENANT_INELIGIBLE", "This tenant is not eligible for plan assignment")`. Both
    already shipped; this task adds no new `error_catalog.py` entry.
  - `PlatformTenantDetail` / `TAB_VALUES` (`apps/dashboard/components/platform/
    PlatformTenantDetail.tsx:46,63-143`) — the existing 4-tab (config/budget/keys/members)
    tenant-detail shell this task extends with a 5th tab; its own `["platform-tenant", tenantId]`
    query (`GET /admin/platform/tenants/{tenantId}` -> `{id, name, kind, created_at}`) is the
    `.kind` source the new tab reuses (shared cache, no extra network round-trip in practice).
  - `PlatformBudgetTab` (`apps/dashboard/components/platform/PlatformBudgetTab.tsx:52-170`) — the
    closest structural precedent this task's own inline assign/adjust flow mirrors: a unified
    loading/error gate, an `isEditing` reveal-BELOW-content (not a modal), and — the single most
    load-bearing line for this task's own null-ceiling wording — `const ceiling =
    data.budget_usd_monthly === null ? "Unlimited" : data.budget_usd_monthly;` (line 115).
  - `PlatformKeysTab` (`apps/dashboard/components/platform/PlatformKeysTab.tsx:163,267-297`) — the
    precedent for a focus-trapped, hand-rolled `role="dialog"` destructive-confirm overlay
    (`useFocusTrap`, Confirm/Cancel, kept-OPEN-on-error via `revokeError`) this task's own "Remove
    plan assignment" confirm mirrors verbatim.
  - `PlatformTenantDirectory` (`apps/dashboard/components/platform/PlatformTenantDirectory.tsx:
    47,120-127`) — the precedent for a top-level, tenant-agnostic list-page shell (`<section
    aria-labelledby>` + `PageHeader`) the new catalog page mirrors.
  - `PlatformNavGroup` / `showPlatformNav` / `PLATFORM_TENANTS_HREF`
    (`apps/dashboard/components/ui/app-shell.tsx:92-138`) — the existing superadmin-only ALLOWLIST
    nav group, today exactly ONE entry ("Tenants"); `showPlatformNav(role)` is `role ===
    "superadmin"` EXACTLY (fails CLOSED, unlike every other nav item's fail-OPEN `visibleItems`
    denylist) — this task adds a SECOND `SidebarItem` ("Plans") to the SAME group, under the SAME
    gate.
  - `bffGet` / `bffPut` / `BffError` (`apps/dashboard/lib/bff-client.ts`) — `handleBffResponse`
    throws `new BffError(res.status, body)` where `body` is the raw parsed JSON error, so
    `err.problem.title` reads whatever `title` field the mocked/real response body carries
    (confirmed by reading `handleBffResponse` directly) — every existing tab's own local
    `getErrorTitle(err)` helper (duplicated per-file, not shared — confirmed precedent, e.g.
    `PlatformBudgetTab.tsx:42-46`) relies on exactly this.
  - `apps/dashboard/app/(app)/app/platform/tenants/page.tsx` and `.../[tenantId]/page.tsx` — thin
    Server Component route wrappers (no client-side role gate; the gateway's `require_superadmin`
    is sole enforcement) this task's own new `.../plans/page.tsx` mirrors exactly.
  - `Card` / `CardTitle` (`apps/dashboard/components/ui/card.tsx:19-58`) — `CardTitle` defaults to
    a real `<h3>` (Radix `Slot`-overridable), confirming the plan-comparison card grid can use it
    for a correct heading level without a document-outline skip under the page's own single `<h1>`.
  - `apps/dashboard/tests/platform-tenant-detail.test.tsx` and `.../platform-nav.test.tsx` — BOTH
    from the DONE, merged `admin-console-ui` task; both are SHARED files this task's eventual
    BUILD step touches ADDITIVELY only (a new tab / a new nav link) — read in full this session to
    confirm neither file's existing assertions hard-codes an EXHAUSTIVE tab list or nav-link COUNT
    that a 5th tab / 2nd nav entry would break (confirmed: `platform-nav.test.tsx` only ever
    queries for the "tenants" link by name, never asserts total nav-item count;
    `platform-tenant-detail.test.tsx`'s own tab-switch tests query tabs by name, not by count).
Context (working folder): `.add/tasks/plan-catalog/TASK.md` (FROZEN @ v1, DONE, PR #58 — the API
  this UI is built against, read in full: §0 GROUND, §1 SPECIFY incl. Framings weighed, §3
  CONTRACT); `.add/milestones/platform-access-plan/MILESTONE.md` (Scope + Exit criteria — this
  task delivers the milestone's 2nd exit criterion, "A superadmin can do the above from the
  dashboard, not just the API"; also documents the 2026-07-05 literal-reading reversal on
  `plan-budget-enforcement`/`plan-rate-enforcement`, which does not touch this task's own scope —
  plan-catalog's own schema/API shape is confirmed unaffected either way, and so is everything
  this task builds on top of it).
Honors (patterns / conventions):
  - Reuse-over-invent (ui-designer persona, `.add/personas/ui-designer.md`): the catalog page
    mirrors `PlatformTenantDirectory`'s list-page shell; the per-tenant tab mirrors
    `PlatformBudgetTab`'s inline-edit shape and `PlatformKeysTab`'s destructive-confirm shape; the
    nav entry extends `PlatformNavGroup`'s existing allowlist gate verbatim — no new shell/dialog/
    list primitive invented.
  - Null-ceiling vocabulary consistency: every ceiling (seat_cap/budget/rpm/tpm) renders
    "Unlimited" when null, exactly matching `PlatformBudgetTab.tsx`'s own shipped line (above) —
    not a new "Custom"/"Negotiated" word invented for this task.
  - Presence-based (not value-based) seat_cap semantics on the client, mirroring the backend's own
    `body.model_fields_set` presence check read directly in `put_platform_tenant_plan`.
  - No client-side role gate on the new page/tab — the gateway's `require_superadmin`/
    `authorize_tenant_scope` remain sole enforcement, exactly matching `PlatformTenantDirectory`'s
    and `PlatformTenantDetail`'s own documented precedent.
Anchors the contract cites: `PlanResponse`, `PlansListResponse`, `TenantPlanResponse`,
  `TenantPlanPutRequest`, `list_platform_plans`, `get_platform_tenant_plan`,
  `put_platform_tenant_plan` (all `platform_plans_router.py`), `PLAN_NOT_FOUND`,
  `PLAN_TENANT_INELIGIBLE` (`error_catalog.py`), `PlatformTenantDetail`/`TAB_VALUES`,
  `PlatformNavGroup`/`showPlatformNav` (`app-shell.tsx`), `bffGet`/`bffPut`/`BffError`
  (`bff-client.ts`).
Issues/Risks (→ feed §1):
  - `apps/dashboard/tests/platform-tenant-detail.test.tsx` and `.../platform-nav.test.tsx` (both
    DONE/frozen, `admin-console-ui`) are SHARED files this task's own BUILD step must touch
    ADDITIVELY ONLY — confirmed neither hard-asserts an exhaustive tab/nav-item count (see Touches
    above), but BUILD must still re-run both files green, unmodified in substance, after adding the
    5th tab / 2nd nav entry. This task's OWN §4 TESTS deliberately adds NEW files only (never edits
    those two), leaving the small additive integration assertion (a 5th `TabsTrigger`/
    `TabsContent` actually renders; a 2nd nav link actually appears) to BUILD/VERIFY — consistent
    with never weakening an existing test, and with the frontend-engineer persona's own "frozen
    page contract... coexist by construction" rule.
  - No existing admin surface in this dashboard shows more than ONE governed dimension per screen
    (Budget/Keys/Config/Members each govern exactly one) — the Plan tab's 3-up ceiling-comparison
    card grid is a genuinely NEW visual pattern for the ADMIN surface specifically (the marketing
    `/pricing` page uses a similar 3-tier idiom, but that page is a static, hardcoded Server
    Component wholly unwired to any API — confirmed by re-reading plan-catalog's own §0 GROUND
    note on it — so it cannot be literally reused, only its visual language mirrored). Flagged per
    the ui-designer persona's "flag any new visual pattern... cite what it replaces or why nothing
    existing fit" rule, not silently introduced — see §1 Framings weighed.
  - `plans` rows can never be created/edited/deleted in v1 (plan-catalog's own M1: "No application
    code path creates, edits, or deletes a plans row in v1") — so a PUT's `ERR_PLAN_NOT_FOUND` is
    structurally UNREACHABLE from this UI in ordinary use (the UI only ever offers `plan_id`
    values it just fetched from the live catalog in the same session). Still scenario'd and
    tested defensively per this task's own explicit instruction ("both named error codes"), not
    skipped as "impossible" — mirrors plan-catalog's own R8 precedent of testing a
    structurally-guarded path anyway.
  - `ERR_PLAN_TENANT_INELIGIBLE` is meant to be PRE-EMPTED client-side (this task's own M8 renders
    no assign control at all for `kind === "platform"`) — its own scenario/test exercises the
    PUT's error-surfacing plumbing directly via a mocked 403, not a path reachable through this
    UI's own normal flow. Same defensive-testing rationale as above.
  - No optimistic-concurrency guard exists for two superadmins editing the SAME tenant's plan
    simultaneously (last-write-wins) — inherited from plan-catalog's own frozen contract, which
    carries no version/ETag field on `TenantPlanResponse` for this UI to key off of; adding one
    would mean reopening plan-catalog's own frozen §3, out of this task's authority. Disclosed,
    not silently assumed — the existing `emit_platform_audit` trail (already shipped, capturing
    old/new values per write) remains the sole reconciliation mechanism, identical to every other
    existing tenant-governance tab (Budget/Config/Keys/Members carry the exact same property
    today, none of them has a concurrency guard either).
Related intent: `.add/PROJECT.md` goal ("...see accurate, billable cost tracking") extended by
  `platform-access-plan`'s own goal ("a tenant can subscribe to a metered... plan") into "a
  superadmin can SEE and GOVERN that from the dashboard" — this task delivers the milestone's own
  2nd Exit criterion verbatim: "A superadmin can do the above from the dashboard, not just the
  API."
Ground SHA: cfbb464

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Plan admin UI — a superadmin-only dashboard surface (a new top-level "Plans" catalog page
  + a new "Plan" tab on the existing tenant-detail screen) letting a superadmin view the plan/tier
  catalog and view, assign, change, adjust, or remove a specific tenant's plan — a pure UI layer
  over the already-shipped, already-frozen `plan-catalog` API (`platform_plans_router.py`); zero
  gateway-side changes.
Framings weighed:
  - Where the catalog lives: a new top-level "Plans" page under the existing "Platform" nav group,
    alongside "Tenants" **(CHOSEN)** — vs. folding the catalog into the Plan tab only, with no
    standalone catalog page **(REJECTED)**. `GET /admin/platform/plans` is tenant-AGNOSTIC
    reference data, structurally identical in shape to the existing "Tenants" directory — the
    existing `PlatformNavGroup` already establishes exactly this "top-level list page" pattern for
    tenant-agnostic data; folding it into a per-tenant tab would hide reference data a superadmin
    needs to consult BEFORE opening any specific tenant behind an arbitrary tenant's own detail
    page, and would duplicate the same rendering logic once the per-tenant tab also needs it for
    comparison.
  - Where per-tenant assignment lives: a 5th tab ("Plan") on `PlatformTenantDetail`'s existing
    Tabs, mirroring Config/Budget/Keys/Members **(CHOSEN)** — vs. a modal/dialog launched from the
    Tenants directory row **(REJECTED)**. A tenant's plan is a governance DIMENSION exactly like
    its budget/keys/members — the existing 4-tab shell is the established home for every other
    per-tenant governance dimension; a dialog-from-the-directory would be the only governance
    surface NOT reachable from the tenant-detail screen, breaking the "one screen per tenant, N
    tabs" mental model this console has used since `admin-console-ui`.
  - Ceiling comparison layout: a 3-up (responsive) card grid, mirroring the marketing `/pricing`
    page's own tier-card idiom, with the tenant's current tier visually marked **(CHOSEN)** — vs. a
    single StatCard pair showing only the CURRENT tier's own ceilings, mirroring `PlatformBudgetTab`
    exactly **(REJECTED)**. The stated failure mode to avoid is a ceiling shown with no comparison
    ("is 500 rpm a lot? compared to what?") — a single-tier StatCard view reproduces exactly that
    failure; showing all 3 tiers side-by-side, every time, answers the question at zero extra
    clicks. Flagged as a genuinely NEW pattern for the ADMIN surface specifically — see §0
    Issues/Risks.
  - Assign interaction shape: click a target tier's own card button, revealing an INLINE (not
    modal) seat_cap-confirm step, mirroring `PlatformBudgetTab`'s inline-edit-reveal **(CHOSEN)** —
    vs. a single styled `<Select>` dropdown + a separate Save button **(REJECTED)**, and vs.
    mirroring `PlatformMembersTab`'s instant-fires-on-change raw `<select>` **(REJECTED)**. A
    dropdown would hide the very ceiling comparison the card grid exists to show; an
    instant-fire-on-change select suits a low-stakes per-row role tweak (Members' own case) but
    not a tenant-wide billing-tier change, which warrants the same explicit-confirm weight
    `PlatformBudgetTab` already gives a monetary ceiling change.
  - Seat_cap omit-vs-include on submit: an explicit `touched` boolean set on any edit of the
    prefilled field **(CHOSEN)** — vs. comparing the draft value against the prefilled default to
    infer intent **(REJECTED)**. Mirrors the backend's OWN `body.model_fields_set` presence check
    (read directly in `put_platform_tenant_plan`) exactly — a value-equality heuristic would
    misclassify "operator deliberately re-entered the same number" as "untouched."
  - Platform-tenant handling: pre-empt CLIENT-SIDE (no assign control rendered at all when
    `tenant.kind === "platform"`, just an explanatory note) **(CHOSEN)** — vs. rendering the normal
    form and letting the 403 surface reactively **(REJECTED)**. The stated failure mode to avoid is
    a control nobody can act on with confidence; a form GUARANTEED to 403 on every submission is
    the opposite of that — the Plan tab already re-fetches `["platform-tenant", tenantId]` (shared
    cache with the parent shell) purely to read `.kind`, so the signal is available at zero extra
    cost before rendering anything.
Must:
<must>
  - **[M1]** `GET /admin/platform/plans`'s full catalog is viewable from a new, superadmin-only,
    top-level "Plans" page (`/app/platform/plans`), reached via a second entry in the existing
    "Platform" nav group — every ceiling shown in human-labeled form (never a bare unlabeled
    integer), with a null default rendered as "Unlimited" (matching `PlatformBudgetTab`'s own
    shipped convention verbatim), in catalog order (Starter, Team, Enterprise).
  - **[M2]** The new "Plans" nav entry is gated by the EXACT SAME allowlist `showPlatformNav(role)`
    (`role === "superadmin"`) as the existing "Tenants" entry — visible in both the desktop rail
    and the mobile sheet for a superadmin, hidden (not merely disabled) for every other role
    including `owner`, and during the identity-loading window (role null/undefined).
  - **[M3]** A superadmin viewing any customer tenant's detail screen sees a 5th tab, "Plan",
    alongside Config/Budget/Keys/Members; opening it shows that tenant's CURRENT assignment (or
    "No plan assigned.") plus all 3 catalog tiers side-by-side for direct ceiling comparison — the
    assigned tier's own card is visually marked "Current plan".
  - **[M4]** From the Plan tab, assigning a plan to an unplanned tenant, or switching an
    already-planned tenant to a different tier, is one explicit two-step action (pick a tier's card
    -> confirm an inline seat_cap step, pre-filled with that tier's own default) — never a single
    instant-fire click, matching the stakes of a billing-tier change.
  - **[M5]** Leaving the pre-filled seat_cap default untouched on assign/switch sends a PUT with
    `seat_cap` OMITTED from the body (inherits the plan's own default, one-time copy —
    plan-catalog's M8); editing it before confirming sends `seat_cap` EXPLICITLY (including an
    intentionally-blanked field as literal `null` = unlimited override — plan-catalog's M9).
  - **[M6]** From the Plan tab, a superadmin can adjust ONLY the current tenant's seat_cap (the
    Enterprise/custom-negotiation case) without switching tiers, via an "Adjust seat cap" action on
    the current plan's own card — sends `PUT {plan_id: <the unchanged current plan id>, seat_cap:
    <new value>}`.
  - **[M7]** From the Plan tab, a superadmin can remove a tenant's plan assignment entirely via an
    explicit, focus-trapped confirm dialog (mirrors `PlatformKeysTab`'s revoke-confirm exactly)
    warning that the seat cap is cleared too; confirming sends `PUT {plan_id: null}` with
    `seat_cap` always omitted (never sent alongside a null `plan_id`, so the backend's own R7 can
    never be triggered by this client).
  - **[M8]** Opening the Plan tab for the platform's own reserved tenant (`kind === "platform"`)
    renders a plain explanatory note ("The platform tenant is Hydroa's own reserved tenant and is
    not eligible for plan assignment.") — no card grid, no button, nothing that could ever 403 is
    rendered.
  - **[M9]** A seat_cap entered as zero, negative, or non-numeric is rejected CLIENT-SIDE (a
    field-level error, mirrors `PlatformBudgetTab`'s own `validateBudget` precedent exactly) before
    any request fires.
  - **[M10]** Any of the 3 endpoints' failure (network error, or a 4xx this client did not
    anticipate — including the 2 named codes below, reached only defensively) renders a clear,
    human-readable inline message (from `BffError.problem.title`, the same `getErrorTitle` helper
    every existing tab already uses) — never a raw JSON/status-code dump, and never silent.
  - **[M11]** Every mutating action's own trigger button is disabled while its mutation is in
    flight (mirrors `PlatformBudgetTab`/`PlatformKeysTab`'s own `disabled={mutation.isPending}`
    precedent) — no double-submit race is reachable by a double-click.
</must>
Reject:
<reject>
  - **[R1]** A non-superadmin loads `/app/platform/plans` or the tenant-detail Plan tab directly by
    URL -> the gateway's existing "ERR_AUTH_FORBIDDEN" (403) surfaces as the standard full-surface
    `ErrorState`, identical to every other existing admin page's own R2/R5 handling — no new
    client-side role gate is added (§1 Framings weighed).
  - **[R2]** A PUT is attempted against the platform tenant despite M8's pre-empting UI (reachable
    only defensively, e.g. a stale `kind` read) -> "ERR_PLAN_TENANT_INELIGIBLE" (403) is shown as a
    clear inline alert (M10), not a raw dump — this task's own scenario mocks this directly rather
    than relying on it being reachable through the UI's own normal flow.
  - **[R3]** A PUT's `plan_id` no longer resolves (structurally unreachable in v1 — plan-catalog's
    M1 forbids any delete path — but tested defensively per this task's own instruction) ->
    "ERR_PLAN_NOT_FOUND" (404) is shown as a clear inline alert (M10).
  - **[R4]** A seat_cap of `0`, a negative number, or a non-numeric string is entered in the inline
    confirm step -> blocked client-side (M9), no request ever sent, a field-level error shown
    instead.
  - **[R5]** Any of the 3 GETs (tenant kind, tenant's plan, plan catalog) fails (network/500) -> the
    tab (or catalog page) renders a full `ErrorState` with a retry action — no partial/stale grid,
    no fabricated data.
</reject>
After:
<after>
  - After M1/M2: a superadmin can navigate Platform -> Plans and see all 3 tiers' real ceilings, at
    any time, independent of any specific tenant.
  - After M3 (view, no mutation yet): the Plan tab reflects EXACTLY what `GET .../plan` returns —
    no client-side caching artifact shows a plan the backend does not currently report.
  - After M4/M5 (successful assign/switch): the tenant's plan durably changed server-side (already
    guaranteed by plan-catalog's own frozen After-clauses); the Plan tab's own cache reflects the
    NEW state without a manual page reload, and the OLD tier's card no longer shows "Current plan".
  - After M6 (seat_cap-only adjustment): the tier is UNCHANGED; only the displayed seat_cap value
    changes.
  - After M7 (unassign): the tab shows "No plan assigned." again, byte-identical to a tenant that
    was never assigned one (mirrors plan-catalog's own After-clause); Cancel on the confirm dialog
    leaves the prior assignment completely unchanged and fires no request.
  - After M8: the platform tenant's Plan tab is permanently a read-only note, regardless of any
    other state.
  - After any Reject (R2-R5): the tenant's displayed plan/seat_cap is unchanged from immediately
    before the attempt; no stale success state lingers.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The 3-up card-grid comparison layout (vs. a simpler single-tier StatCard mirroring Budget
    exactly) is THIS draft's own design call, not something Tin has seen or confirmed — lowest
    confidence because it's the single biggest visual-design departure from existing admin-surface
    precedent (§0 Issues/Risks; the marketing pricing page uses a similar idiom, but no ADMIN
    screen does yet). If wrong (Tin prefers the plainer single-tier StatCard): cheap to fix — a
    Build-time layout swap, no contract/API shape changes, since both designs consume the exact
    same 2 GET responses.
  ⚠ The interaction shape for assign/switch (click-a-card -> inline seat_cap confirm) versus a
    `<Select>` dropdown is this draft's own judgment call, weighing "keep the comparison grid
    visible" against "one more click than a dropdown+button would need." If wrong: also a cheap
    Build-time swap (same data, same PUT shape) — no contract change.
  - [ ] The "Adjust seat cap" affordance on the CURRENT plan's own card (M6, the
    Enterprise/custom-negotiation case) is this draft's own addition — MILESTONE.md names the
    underlying NEED (per-tenant negotiated ceilings) but never explicitly asked for a DEDICATED UI
    affordance distinct from "switch tiers"; medium confidence this is what a superadmin would
    actually want, since the alternative (a "switch to Enterprise then immediately switch back to
    Enterprise with a new seat_cap" workaround) is clearly worse — confirm or deny at freeze.
  - [ ] Icon choice for the new "Plans" nav entry (`Tags`, from `lucide-react`, confirmed exported)
    is this draft's own pick, not confirmed with Tin/DESIGN.md — cosmetic only, a one-line
    Build-time change if a different icon is preferred — confirm or deny at freeze.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── M1/M2: catalog page + nav entry ───────────────────────────────────────────

Scenario: A superadmin views the plan catalog with human-labeled ceilings   # M1
  Given a SUPERADMIN identity and the 3 seeded plan tiers (Starter/Team/Enterprise)
  When the SUPERADMIN opens Platform -> Plans (/app/platform/plans)
  Then all 3 tiers render, in catalog order, each showing its own seat cap, monthly budget,
    requests/min, and tokens/min — any null default rendered as "Unlimited", never a bare number

Scenario: The "Plans" nav entry is visible only to a superadmin   # M2
  Given the AppShell rendered with role="superadmin"
  When the desktop rail and the mobile sheet are inspected
  Then a "Plans" link (href /app/platform/plans) appears in both, under the "Platform" group

Scenario: The "Plans" nav entry is hidden for every non-superadmin role   # M2
  Given the AppShell rendered with role in {null, undefined, "member", "admin", "owner"}
  When the primary nav is inspected
  Then no "Plans" link appears, for any of those roles
  And this is unchanged from the existing "Tenants" entry's own allowlist behavior

# ── M3: viewing a tenant's plan, with comparison ──────────────────────────────

Scenario: An unplanned tenant's Plan tab shows no assignment and all 3 tiers as assignable   # M3
  Given a SUPERADMIN identity and a customer tenant T with no plan assigned
  When the SUPERADMIN opens T's detail screen and selects the "Plan" tab
  Then "No plan assigned." is shown
  And all 3 tiers render side-by-side, none marked "Current plan"

Scenario: An assigned tenant's Plan tab marks its current tier and shows the others for comparison   # M3
  Given a SUPERADMIN identity and a customer tenant T currently assigned the "team" plan
  When the SUPERADMIN opens T's Plan tab
  Then the "Team" card is marked "Current plan" and shows T's own resolved seat_cap
  And the "Starter" and "Enterprise" cards render alongside it with their own ceilings, for
    direct comparison

# ── M4/M5: assign / switch, with the M8-vs-M9 omit/include split (plan-catalog's own M8/M9) ──

Scenario: Assigning a plan with the pre-filled seat cap left untouched omits seat_cap   # M4, M5
  Given a SUPERADMIN identity and an unplanned customer tenant T
  When the SUPERADMIN clicks "Assign Starter", leaves the pre-filled seat-cap field untouched,
    and confirms
  Then PUT .../plan fires with body { plan_id: <starter's id> } — seat_cap KEY ABSENT
  And the response's plan/seat_cap becomes T's newly displayed current plan

Scenario: Assigning a plan with an edited seat cap sends it explicitly   # M4, M5
  Given a SUPERADMIN identity and an unplanned customer tenant T, and the "enterprise" plan's own
    seat_cap default is null (unlimited)
  When the SUPERADMIN clicks "Assign Enterprise", edits the seat-cap field to 47, and confirms
  Then PUT .../plan fires with body { plan_id: <enterprise's id>, seat_cap: 47 } — EXPLICIT, not
    the plan's own null default
  And the tab displays seat_cap 47 for T

Scenario: Switching an already-assigned tenant to a different tier updates which card is current   # M4
  Given a SUPERADMIN identity and a customer tenant T currently assigned "starter"
  When the SUPERADMIN clicks "Assign Team" (on the non-current Team card) and confirms with the
    pre-filled default
  Then PUT .../plan fires with body { plan_id: <team's id> }
  And the "Team" card becomes marked "Current plan"; "Starter" no longer is

# ── M6: seat-cap-only adjustment, same tier ───────────────────────────────────

Scenario: Adjusting only the seat cap of the current plan leaves the tier unchanged   # M6
  Given a SUPERADMIN identity and a customer tenant T currently assigned "enterprise" with
    seat_cap=null
  When the SUPERADMIN clicks "Adjust seat cap" on the CURRENT ("Enterprise") card, enters 120,
    and confirms
  Then PUT .../plan fires with body { plan_id: <T's CURRENT enterprise id, unchanged>,
    seat_cap: 120 }
  And T's tier is still "Enterprise"; only the displayed seat cap changes to 120

# ── M7: unassign, focus-trapped confirm ───────────────────────────────────────

Scenario: Cancelling the remove-plan confirm makes no request and changes nothing   # M7
  Given a SUPERADMIN identity and a customer tenant T currently assigned a plan
  When the SUPERADMIN clicks "Remove plan assignment", then clicks "Cancel" inside the dialog
  Then no PUT request is ever sent
  And T's plan/seat_cap remain exactly as they were; the dialog closes

Scenario: Confirming remove-plan clears the assignment atomically   # M7
  Given a SUPERADMIN identity and a customer tenant T currently assigned "team" with seat_cap=25
  When the SUPERADMIN clicks "Remove plan assignment" and then "Confirm" inside the
    focus-trapped dialog
  Then PUT .../plan fires with body { plan_id: null } — seat_cap KEY ABSENT, never sent alongside
    a null plan_id
  And the tab now shows "No plan assigned.", indistinguishable from a tenant never assigned one

# ── M8: platform tenant is pre-emptively read-only ────────────────────────────

Scenario: The platform tenant's Plan tab is a read-only note, never a form   # M8
  Given a SUPERADMIN identity and the platform tenant's own tenant_id P (kind="platform")
  When the SUPERADMIN opens P's Plan tab
  Then a plain explanatory note renders ("...not eligible for plan assignment")
  And no card grid, no button, and no control that could ever fire a PUT is rendered anywhere in
    the tab

# ── M9/R4: client-side seat-cap validation ────────────────────────────────────

Scenario: A zero, negative, or non-numeric seat cap is blocked before any request fires   # M9, R4
  Given a SUPERADMIN identity and an unplanned customer tenant T
  When the SUPERADMIN clicks "Assign Starter" and enters "0", then separately "-5", then
    separately "abc" into the seat-cap field, confirming each time
  Then each attempt shows a field-level error and PUT is NEVER called
  And T's plan/seat_cap remain unchanged (still unplanned) after all three attempts

# ── M10/R2/R3: named error codes surfaced as clear inline alerts ─────────────

Scenario: A PUT rejected as plan-tenant-ineligible is shown as a clear alert, not a raw dump   # M10, R2
  Given a SUPERADMIN identity and a customer tenant T (kind="customer") mid-assignment
  When the PUT itself is mocked to return 403 { code: "ERR_PLAN_TENANT_INELIGIBLE", title: "This
    tenant is not eligible for plan assignment" } and the SUPERADMIN confirms the assign step
  Then an inline alert shows "This tenant is not eligible for plan assignment" — never a raw
    { code, title } object rendered

Scenario: A PUT rejected as plan-not-found is shown as a clear alert   # M10, R3
  Given a SUPERADMIN identity and a customer tenant T mid-assignment
  When the PUT itself is mocked to return 404 { code: "ERR_PLAN_NOT_FOUND", title: "Plan not
    found" } and the SUPERADMIN confirms the assign step
  Then an inline alert shows "Plan not found"
  And T's previously-displayed plan/seat_cap remain unchanged (the failed attempt is not
    optimistically applied)

# ── R1: non-superadmin access is rejected identically to every existing admin page ──

Scenario: A non-superadmin (or unauthenticated) request against either new surface renders the standard ErrorState   # R1
  Given no superadmin identity (missing/invalid bearer, or a non-superadmin role)
  When GET /admin/platform/plans (catalog page), or any of the Plan tab's own 3 GETs, returns 403
    ERR_AUTH_FORBIDDEN (or 401)
  Then the respective surface renders the SAME standard full-surface ErrorState every other
    existing admin page already uses — no new client-side role gate, no special-cased copy
  And no plans/tenant data is shown anywhere on the page

# ── R5: fetch failure ──────────────────────────────────────────────────────────

Scenario: A fetch failure on the catalog or the tenant's plan renders a full ErrorState   # R5
  Given a SUPERADMIN identity
  When GET /admin/platform/plans (catalog page), and separately GET .../{tenantId}/plan (Plan
    tab), each fail with a 500
  Then the respective surface renders a full ErrorState with a retry action
  And no partial/stale card grid is shown alongside it

# ── M11/edge: double-submit guard ─────────────────────────────────────────────

Scenario: The confirm button is disabled while its own mutation is in flight   # M11
  Given a SUPERADMIN identity and an unplanned customer tenant T, and the PUT response is
    deliberately delayed
  When the SUPERADMIN clicks "Assign Starter", confirms, then immediately clicks the confirm
    button again before the response arrives
  Then the button is disabled for the duration of the in-flight request
  And exactly ONE PUT request is ever sent

# ── edge case: catalog empty (boundary, defensive) ────────────────────────────

Scenario: An empty catalog renders an Empty state, not a blank grid   # edge case, defensive
  Given GET /admin/platform/plans returns zero rows (structurally invariant-guarded against
    today, per plan-catalog's own M1, but not enforced at this UI's own type level)
  When the SUPERADMIN opens Platform -> Plans
  Then an Empty state renders ("No plan tiers configured.")
  And no error is thrown, no blank/broken grid is shown
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# UI CONTRACT — screens/regions, and which already-shipped route backs each. HTTP shapes below are
# CITED, not redefined (owned by the frozen, shipped `plan-catalog` task, FROZEN @ v1, unchanged by
# this design-only task) — GATEWAY-SIDE CHANGE NEEDED: NONE. All 3 endpoints already exist with
# response shapes that fully cover this UI (confirmed by reading platform_plans_router.py directly
# this session, not merely trusting plan-catalog's TASK.md prose).

SCREEN 1 — Plan catalog (NEW, read-only)              route: /app/platform/plans (NEW)
  region: PageHeader                    title="Platform · Plans", description="Reference catalog
                                          of usage-governance tiers. Assign or change a tenant's
                                          plan from its own detail page."
  region: 3-up (responsive) card grid   backs: GET /admin/platform/plans -> PlansListResponse
                                          { plans: PlanResponse[] } (CITED, plan-catalog §3)
                                         each card: CardTitle=display_name (h3) + 4 labeled rows:
                                           "Seats" / "Monthly budget" / "Requests/min" /
                                           "Tokens/min" — null -> "Unlimited" (M1, mirrors
                                           PlatformBudgetTab.tsx's own shipped null-ceiling line)
  region: Loading / Empty / ErrorState  Loading label="Loading plans" · zero rows -> Empty
                                          "No plan tiers configured." (edge case) · GET failure ->
                                          ErrorState w/ retry (R5)

NAV — apps/dashboard/components/ui/app-shell.tsx (EDIT, additive)
  a SECOND `SidebarItem` ("Plans" -> /app/platform/plans) inside the EXISTING `PlatformNavGroup`,
  alongside "Tenants" — SAME `showPlatformNav(role)` allowlist gate (role === "superadmin"
  exactly), rendered in both the desktop rail and the mobile sheet. Zero changes to the existing
  "Tenants" entry, `visibleItems()`, or any of the 19 `NAV_ITEMS` (M2).

SCREEN 2 — Tenant detail, 5th tab "Plan" (EDIT, additive)   route: .../platform/tenants/[tenantId]
                                                              (EXISTING, ?tab=plan added)
  region: PlatformTenantDetail.tsx EDIT   `TAB_VALUES` gains `"plan"`; a 5th `TabsTrigger`/
                                          `TabsContent` pair mounts `PlatformPlanTab` — mirrors the
                                          existing 4 tabs' own "independent panel, own useQuery"
                                          shape exactly (M3). Existing 4 tabs / their own frozen
                                          `platform-tenant-detail.test.tsx` assertions: UNCHANGED.

  TAB Plan (NEW component: components/platform/PlatformPlanTab.tsx)
    fetches (3, independent, unified loading/error gate):
      (a) GET /admin/platform/tenants/{tenantId}        -> TenantSummaryResponse {..., kind}
          (CITED, admin-console-ui §3) — queryKey ["platform-tenant", tenantId], SHARED cache w/
          PlatformTenantDetail's own identical query; read ONLY for `.kind` (M8's pre-empt check)
      (b) GET /admin/platform/tenants/{tenantId}/plan    -> TenantPlanResponse {tenant_id,
          plan: PlanResponse|null, seat_cap} (CITED, plan-catalog §3) — queryKey
          ["platform-tenant-plan", tenantId]
      (c) GET /admin/platform/plans                      -> PlansListResponse (CITED, same as
          Screen 1) — queryKey ["platform-plans"], SHARED cache w/ Screen 1

    IF kind === "platform" (M8):
      region: plain note          "The platform tenant is Hydroa's own reserved tenant and is not
                                   eligible for plan assignment." — NO grid, NO button, nothing
                                   that could fire a PUT.

    ELSE:
      region: assignment line     plan === null -> "No plan assigned." (else nothing extra; the
                                   grid's own "Current plan" badge carries that information) (M3)
      region: 3-up card grid      same 4 labeled rows as Screen 1's cards, PLUS:
                                    - the card matching `plan?.id` shows a "Current plan" Badge +
                                      an "Adjust seat cap" button (M6)
                                    - every OTHER card shows an "Assign {display_name}" button (M4)
      region: inline confirm      opened by either button above (mirrors PlatformBudgetTab's
        (reveal-below-grid,        `isEditing` reveal, NOT a modal): a Seats `Input`
         never a modal)            (id="platform-plan-seatcap-input", label "Seats", placeholder
                                   "leave empty for unlimited"), pre-filled with the TARGET plan's
                                   own seat_cap (blank if null) · Save/Cancel.
                                     - Save, value UNTOUCHED since open -> PUT body {plan_id}
                                       (seat_cap key ABSENT — M5/M8-inherit)
                                     - Save, value EDITED (touched flag, not value-equality — a
                                       PRESENCE check mirroring the backend's own
                                       `body.model_fields_set`) -> client-side validates
                                       (blank->null; else /^\d+$/ and >0, else a field error, R4)
                                       -> PUT body {plan_id, seat_cap} EXPLICIT (M5/M9-override)
                                     - Save button disabled while its own mutation isPending (M11)
                                     - onError: alert stays inside the reveal (kept OPEN, mirrors
                                       PlatformKeysTab's revoke-kept-open-on-error precedent),
                                       title from BffError.problem.title (M10)
      region: remove-plan confirm  ONLY rendered when a plan IS currently assigned: a
        (focus-trapped dialog,     de-emphasized "Remove plan assignment" trigger -> a hand-rolled
         mirrors PlatformKeysTab)  role="dialog" aria-modal aria-label="Confirm plan removal"
                                   overlay (useFocusTrap, mirrors PlatformKeysTab.tsx:163,267-297
                                   verbatim) — copy warns the seat cap clears too — Confirm
                                   (destructive)/Cancel.
                                     - Confirm -> PUT body {plan_id: null} — seat_cap key ALWAYS
                                       absent (never sent alongside a null plan_id; the backend's
                                       own R7 is therefore structurally unreachable from this
                                       client) (M7)
                                     - Cancel -> no request, dialog closes, nothing changes

    PUT /admin/platform/tenants/{tenantId}/plan  (CITED verbatim, plan-catalog §3 — no new body
      shape, no new response shape)
      200 -> TenantPlanResponse (same shape as GET (b) above) -> caches (b) updated, grid re-marks
        "Current plan"
      403 -> { code: "ERR_PLAN_TENANT_INELIGIBLE", title: "..." } — shown as inline alert (M10, R2;
        pre-empted by M8 in normal use, tested defensively)
      404 -> { code: "ERR_PLAN_NOT_FOUND", title: "..." } — shown as inline alert (M10, R3;
        structurally unreachable given plans are never deleted in v1, tested defensively)
      422 -> { code: "ERR_PAYLOAD_INVALID", title: "..." } — never actually reachable from THIS
        client (client-side validation, M9/R4, blocks first) — no dedicated UI region needed beyond
        the generic inline-alert path, since a well-behaved client never sends an invalid seat_cap

Schema: no new tables/columns/migrations, no new gateway route, no new error_catalog.py entry — a
  PURE UI CONSUMER of the already-frozen, already-shipped plan-catalog contract. GATEWAY-SIDE
  CHANGE NEEDED: NONE (explicitly confirmed — see the 3 endpoints' response shapes above, each
  independently re-read from platform_plans_router.py this session, sufficient for every Must this
  task specifies; no gap found that would require reopening plan-catalog's own frozen contract).
```

Glossary deltas: none — this task introduces no new domain term; "plan"/"plan tier"/"seat cap" are
  already glossary-proposed by plan-catalog's own §3 (pending this milestone's fold), unchanged
  here. (One UI-only label choice worth recording for consistency, not a GLOSSARY term: every null
  ceiling — seat_cap/budget/rpm/tpm alike — renders as the single word "Unlimited" everywhere in
  this task's own new surfaces, matching `PlatformBudgetTab.tsx`'s existing shipped convention.)

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — not yet presented for freeze; see the ranked flags below for what the freeze
  decision should weigh first.

Least-sure flag surfaced at freeze: (multiple, ranked — carried from §1's ⚠ assumptions)
  ⚠ [contract/spec] The 3-up ceiling-comparison card grid (vs. a plainer single-tier StatCard
    mirroring PlatformBudgetTab exactly) is this draft's own design call, unconfirmed with Tin —
    the single biggest visual-design departure from existing admin-surface precedent (no admin
    screen today shows more than one governed dimension at a time). Cost if wrong: a Build-time
    layout swap only — both designs consume the identical 2 GET responses, no contract/API change
    either way.
  ⚠ [contract] The click-a-card -> inline-seat-cap-confirm interaction (vs. a `<Select>` dropdown +
    Save button) is this draft's own judgment call, trading one extra click for keeping the
    ceiling-comparison grid visible throughout the choice. Cost if wrong: also a cheap Build-time
    swap, same data/PUT shape.
  ⚠ [spec] The dedicated "Adjust seat cap" affordance on the current plan's own card (M6) is this
    draft's own addition — MILESTONE.md names the underlying Enterprise/custom-negotiation NEED but
    never asked for a UI affordance distinct from "switch tiers." Medium confidence this is wanted;
    if not, it's a small Build-time removal (the same result is still reachable via "switch to the
    same tier" as a workaround, just clumsier).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY (new
     terms declared as a Glossary delta) + the bundle's lowest-confidence flag was surfaced at
     the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (repo-wide `vitest.config.ts` `coverage.thresholds.lines: 80`, unchanged — not
  lowered by this task)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_catalog_lists_all_tiers_with_human_labeled_ceilings (M1): GET .../plans -> 3 cards
    render, each ceiling row shown human-labeled, null -> "Unlimited"
  - test_catalog_shows_empty_state_when_no_tiers_exist (edge case): GET .../plans -> [] -> Empty,
    no ErrorState, no broken grid
  - test_catalog_shows_error_state_on_fetch_failure (R5): GET .../plans -> 500 -> ErrorState w/
    retry, no partial grid
  - test_catalog_shows_standard_error_state_on_403_non_superadmin (R1): GET .../plans -> 403
    ERR_AUTH_FORBIDDEN -> the SAME standard ErrorState (no special-cased copy), no partial grid
  - test_plans_nav_visible_for_superadmin_desktop_and_mobile (M2): role="superadmin" -> "Plans"
    link (href=/app/platform/plans) in both the desktop rail and the mobile sheet
  - test_plans_nav_hidden_for_non_superadmin_roles (M2): role in
    {null,undefined,member,admin,owner} -> no "Plans" link, for any of them
  - test_unplanned_tenant_shows_no_plan_assigned_and_all_tiers_assignable (M3): GET .../plan ->
    plan=null -> "No plan assigned." + all 3 cards render, none marked current, no "Remove plan
    assignment" trigger
  - test_assigned_tenant_marks_current_tier_and_shows_others_for_comparison (M3): GET .../plan ->
    plan=team -> Team card marked "Current plan"; Starter/Enterprise also render with their own
    Assign buttons
  - test_assign_plan_with_default_seat_cap_omits_seat_cap_in_request (M4, M5): click "Assign
    Starter" -> confirm untouched -> captured PUT body has NO seat_cap key
  - test_assign_plan_with_edited_seat_cap_sends_explicit_override (M4, M5): click "Assign
    Enterprise" -> edit seat-cap to 47 -> confirm -> captured PUT body = {plan_id, seat_cap: 47}
  - test_switch_tier_updates_which_card_is_marked_current (M4): assigned "starter" -> click
    "Assign Team" -> confirm -> Team marked current, Starter no longer
  - test_adjust_seat_cap_without_changing_tier (M6): assigned "enterprise" -> click "Adjust seat
    cap" -> 120 -> confirm -> PUT body {plan_id: <same enterprise id>, seat_cap: 120}; tier still
    Enterprise
  - test_remove_plan_assignment_cancel_makes_no_request (M7): click "Remove plan assignment" ->
    Cancel in dialog -> no PUT fired, dialog closes, prior state unchanged
  - test_remove_plan_assignment_confirm_clears_atomically (M7): click "Remove plan assignment" ->
    Confirm -> captured PUT body = {plan_id: null}, seat_cap key ABSENT -> tab shows "No plan
    assigned." again
  - test_platform_tenant_shows_ineligible_note_not_a_form (M8): kind="platform" -> plain note
    renders; zero Assign/Adjust/Remove buttons anywhere in the tab
  - test_seat_cap_client_side_validation_blocks_invalid_values (M9, R4): "0", "-5", "abc" each ->
    field error (role=alert) shown, PUT never called, for all three
  - test_server_rejects_with_plan_tenant_ineligible_shown_as_alert (M10, R2): PUT mocked 403
    ERR_PLAN_TENANT_INELIGIBLE -> inline alert shows the title text, not a raw object
  - test_server_rejects_with_plan_not_found_shown_as_alert (M10, R3): PUT mocked 404
    ERR_PLAN_NOT_FOUND -> inline alert shows the title text; displayed plan/seat_cap (still
    unplanned) unchanged, not optimistically applied
  - test_plan_fetch_failure_shows_error_state_with_retry (R5): GET .../plan -> 500 -> ErrorState
    w/ retry, no partial grid
  - test_assign_button_disabled_while_mutation_pending (M11): delayed PUT response -> confirm
    button disabled for the duration; exactly one PUT call total despite a second click
</test_plan>

Tests live in: `apps/dashboard/tests/platform-plan-catalog.test.tsx` (catalog page + nav entry —
  first 6 tests above), `apps/dashboard/tests/platform-plan-tab.test.tsx` (per-tenant tab —
  remaining 14 tests above) — new files in the EXISTING `apps/dashboard/tests/` suite, mirroring
  `platform-config-budget.test.tsx`/`platform-keys.test.tsx`/`platform-nav.test.tsx`'s own
  conventions (Vitest + @testing-library/react + `tests/mocks/server` MSW). MUST run red (missing
  implementation) before Build. Neither `PlatformPlanCatalog` nor `PlatformPlanTab` exists yet;
  `platform-tenant-detail.test.tsx` / `platform-nav.test.tsx` (both pre-existing, DONE) are
  deliberately NOT touched by this task's own tests — their additive integration assertions (a 5th
  tab / 2nd nav link actually renders) are BUILD/VERIFY's job, not drafted here, so this task never
  edits a file it did not create.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features>

Persona (optional): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; absent = generic>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

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
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
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

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. The Advisor 3-lens verdict and the Refute-read verdict are both measured by `add.py audit` (`advisor_verdict_unrecorded` · `refute_unrecorded`) — neither is engine-blocked; a human spot-audit is the backstop for any finding the AI did not surface or record. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
