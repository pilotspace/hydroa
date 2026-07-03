# TASK: Platform admin console — dashboard UI (directory + tenant detail)

slug: admin-console-ui · created: 2026-07-03 · stage: production
milestone: platform-admin-console
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - NEW `apps/dashboard/app/(app)/app/platform/tenants/page.tsx` — directory route (server shell, mirrors `apps/dashboard/app/(app)/app/members/page.tsx`'s auth-gate pattern).
  - NEW `apps/dashboard/app/(app)/app/platform/tenants/[tenantId]/page.tsx` — tenant-detail route (dynamic segment).
  - NEW `apps/dashboard/components/platform/PlatformTenantDirectory.tsx` — directory table (client component, wraps `DataTable`).
  - NEW `apps/dashboard/components/platform/PlatformTenantDetail.tsx` — tenant-detail shell (tabs: Config · Budget · Keys · Members) + the cross-tenant safety banner.
  - NEW `apps/dashboard/components/platform/*` — one panel component per tab, each parametrized by `tenantId` and reusing the existing self-service panel's data shape (`MembersPage`/`TeamMembersPanel`-equivalent for Members; new Config/Budget/Keys panels calling the platform routes below).
  - EDIT `apps/dashboard/components/ui/app-shell.tsx` — add a superadmin-only nav entry ("Platform" section → "Tenants"); `AppShell` already accepts `role` and filters nav links by it (fail-open when role is null/loading — existing, documented convention, unchanged).
  - Backend (consumed read-only, no changes — all 4 already shipped, gate=PASS this session):
    - `gateway.tenants.api.platform_tenants_router`: `GET /admin/platform/tenants` (`TenantDirectoryListResponse`), `GET /admin/platform/tenants/{tenant_id}` (`TenantSummaryResponse`).
    - `gateway.tenants.api.platform_tenant_config_router` (mounted per-tenant): `GET`/`PUT /cache` (`Cache{Get,Put}Response`), `GET`/`PUT /guardrails` (`GuardrailConfig*Response`), `GET`/`PUT /budget` (`Budget{Get,Put}Response`).
    - `gateway.tenants.api.platform_users_router`: `GET ""` (`UsersListResponse`), `PUT /{user_id}/role` (`UserResponse`).
    - `gateway.keys.api.platform_keys_router`: `GET ""` (`list[KeyInfoResponse]`), `POST ""` (`CreateKeyResponse`, 201), `PATCH /{key_id}` (`KeyInfoResponse`), `POST /{key_id}/rotate` (201), `DELETE /{key_id}` (204).
    - All 15 routes gated by `require_superadmin` + `authorize_tenant_scope(identity, tenant_id)`; all mutating calls already emit `emit_platform_audit(...)` (admin-console-audit, this session).
Context (working folder): `.add/DESIGN.md` (frozen v13 identity: Indigo-600 `#4F46E5`, slate neutrals, Inter, "precise · calm · trustworthy", WCAG 2.2 AA, light-mode-only) — human-owned, reused verbatim, NOT re-asked. `.add/milestones/platform-admin-console/MILESTONE.md` (this task's own Scope line: "the first UI-facing milestone in this roadmap, run through ADD's UDD design loop rather than shipped as bare CRUD+table"; Exit criterion 5).
Honors (patterns / conventions):
  - `AppShell` (`apps/dashboard/components/ui/app-shell.tsx`) + `DashboardShell` — role-filtered nav, fail-open client-side (server enforces RBAC on navigate; documented in `DashboardShell`'s own header comment) — the existing, unchanged pattern a new superadmin-only nav entry extends.
  - `PageHeader` / `DataTable` / `Loading` / `ErrorState` (`apps/dashboard/components/ui/*`) — shared presentational primitives every existing `(app)/app/*` page reuses (confirmed via `MembersPage.tsx`); reuse-before-invent per `design.md` beat 2.
  - `bffGet`/`bffPut`/`BffError` (`apps/dashboard/lib/bff-client.ts`) + TanStack Query (`useQuery`/`useMutation`) — the existing data-fetching convention (confirmed via `MembersPage.tsx`), no new client-fetch pattern.
  - MILESTONE.md's "reuse-over-invent": every platform panel parametrizes the SAME DTOs the self-service `/admin/*` pages already render (config/budget/keys/members) by target `tenant_id` — never a parallel shape.
Anchors the contract cites: `AppShell` role-filter prop contract · the 15 platform routes above (paths + response models) · `PageHeader`/`DataTable` component props · `.add/DESIGN.md` identity tokens (`apps/dashboard/app/globals.css` `@theme`).
Issues/Risks (→ feed §1):
  - Cross-tenant safety: a superadmin can view/edit ANY tenant's config, budget, keys, members — the single highest-cost mistake class is acting on the WRONG tenant (e.g., editing tenant B's budget while believing it's tenant A). No existing screen in this codebase has this shape (every current `(app)/app/*` page is single-tenant, implicit from the session). Feeds a dedicated visual safety cue (design-intake axis, see below).
  - Keys must render REDACTED/metadata-only, matching the existing BYOK key surface convention (MILESTONE.md Out-of-scope line) — never raw secret material, even in a mockup with fake data.
  - `AppShell`'s nav fail-open-on-null-role is a UX affordance only (gateway enforces server-side) — the new "Platform" nav entry must follow the SAME convention, not invent a stricter client-side gate that then diverges from every other nav item.
  - Design-intake (4 axes; `design.md` beat 0) — CONCEPT + VISUAL DESIGN already frozen project-wide (`.add/DESIGN.md`, reused verbatim). FIDELITY + LAYOUT + the cross-tenant safety-cue + nav placement are feature-specific and were NOT answered synchronously (AskUserQuestion timed out ×4 this session, ×1 for this task specifically) — proceeding under disclosed AUTO MODE with the recommended default at each: FIDELITY=production (matches all 14 existing `(app)/app/*` pages, none of which shipped lo-fi) · LAYOUT=directory table → full-page detail with tabs (matches the teams/members precedent of one top-level page per entity) · SAFETY=persistent banner + tinted chrome while viewing a non-own tenant (matches this milestone's own audit-everything safety posture) · NAV=new top-level superadmin-only "Platform" section (direct extension of `AppShell`'s existing role-filter mechanism). These are corrigible right up to the render-capture-confirm gate, which stays a REAL human confirmation — not auto-passed regardless of mode.
Related intent: MILESTONE.md goal ("A superadmin can view and fully manage any tenant... through a dedicated, fully audited cross-tenant admin surface") + Exit criterion 5 ("The console is a polished, Aurora-consistent, WCAG-AA UI surface, design-confirmed before build") — this task is the ONLY remaining piece to close the milestone. GLOSSARY gap noted by MILESTONE.md (not yet fixed): "platform tenant"/"superadmin"/"cross-tenant admin surface" still missing from GLOSSARY.md — candidate for this task's fold.
Ground SHA: ccf411c

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A superadmin-only dashboard surface — a searchable/paginated directory of every tenant
  at `/app/platform/tenants`, and a URL-addressable per-tenant detail page at
  `/app/platform/tenants/[tenantId]` (Config · Budget · Keys · Members tabs) — consuming the 15
  already-shipped, already-audited platform-admin routes named in §0. This task is a **design-only
  deliverable**: TASK.md §1-§3 plus a render-capture HTML mock. No `apps/dashboard/app/*` or
  `apps/dashboard/components/*` source file is written by this task; the wireframe/mock is the
  artifact a future BUILD task implements against.

Framings weighed:
  Screen topology: a directory list + a full-page, dynamic-route (`[tenantId]`) detail with tabs
  (chosen) · a master-detail on ONE page/URL, mirroring `TeamsPage.tsx`'s actual shape — `selectedId`
  React state selects which `TeamMembersPanel` renders below the table, no route change (rejected,
  see ⚠ Assumption #2 below: this is the codebase's REAL existing "list → one entity's sub-resource"
  precedent, not a dynamic route — I deviated from it deliberately, not by mistake) · a slide-over
  drawer opened from the directory row (rejected — genuinely new to this codebase, confirmed no
  `Dialog`/sheet is ever used for a full CRUD surface today, only for short create/confirm forms).
  Chosen because: (a) 4 independent tabs (Config/Budget/Keys/Members), each with its own
  loading/error/mutation state, is a materially heavier surface than `/teams`' single
  `TeamMembersPanel`; (b) a superadmin/support workflow benefits from a bookmarkable, shareable,
  refresh-safe URL naming exactly which tenant and which tab is in view — durable evidence of "who
  is being acted on," which directly serves this milestone's own safety framing; a `selectedId`
  React-state selection is invisible in the URL and resets on refresh.
  Nav placement: a new superadmin-gated "Platform" `SidebarGroup` (chosen — reuses `SidebarGroupLabel`,
  exported by `components/ui/sidebar.tsx` but never yet invoked by `AppShell`, a "reuse a dormant
  catalog primitive" win) · flattening "Tenants" into the existing single ungrouped nav list with a
  new `minRole` tier (rejected — a bare, unlabeled link among 19 ordinary tenant-scoped links would
  bury the ONE nav entry whose entire purpose is to visually signal "this is a different, cross-tenant
  mode," undermining the safety framing this milestone exists for).
  Nav visibility polarity (safety-relevant, see ⚠ Assumption #3): an ALLOWLIST — visible only when
  `role === "superadmin"` exactly, hidden during the identity-loading window (chosen) · a literal,
  mechanical copy of `visibleItems()`'s existing DENYLIST shape (hidden only from specific named
  lower roles, fail-open/visible by default otherwise, including while loading — rejected, despite
  being the more literal reading of "reuse the same mechanism": confirmed via
  `tests-bff/nav-role-filter.test.tsx`'s own `test_unknown_role_fails_open` that the existing
  fail-open convention shows ALL 19 tenant-scoped links to an unresolved/unknown role; the blast
  radius of that choice for the 19 EXISTING items is low [a member briefly sees an ordinary
  admin-tier link like "Teams"], but copying it verbatim for "Platform" would show a cross-tenant
  admin console's existence to ~100% of ordinary paying customers on every cold load — a materially
  different, real information-disclosure/trust cost, not just a transient UX nicety).
  Directory search/paginate wiring: a page-owned `q` (debounced) + `offset` state feeding the
  `useQuery` queryKey, rendering the CURRENT page's slice through a plain (non-`searchable`,
  non-`pageSizeOptions`) `DataTable`, with a labeled search `Input` + Previous/Next controls placed
  around it to LOOK identical to `DataTable`'s own built-in search/paginate chrome (chosen) ·
  `DataTable`'s existing `searchable`+`pageSizeOptions` opt-in props, fetching a large/unbounded
  `limit` once and letting client-side Fuse.js + tanstack pagination handle the rest (rejected —
  see cited catalog gap below: those props are documented as client-data-only, whereas
  `list_platform_tenants` (`platform_tenants_router.py:65-78`, read directly) takes real `q`/`limit`/
  `offset` Query params, clamps an over-large `limit` server-side, and returns `total` — signals a
  platform-wide tenant count the backend author did NOT expect to always fit comfortably in one
  unpaginated browser fetch, unlike every other existing list page's small, single-tenant-scoped
  collection [a tenant's own users/teams/keys]). **Cited catalog gap**: `components/ui/data-table.tsx`
  has no server-driven search/paginate mode today — only client-side-over-already-fetched-data. This
  task's directory screen needs one; visually it is indistinguishable from the existing convention,
  so no new VISUAL component is proposed, only a new (small, page-owned) data-orchestration wrapper.
  Config tab composition: two stacked `Card`s (Cache, Guardrails) inside one `TabsContent` (chosen —
  mirrors `GuardrailSettings.tsx`'s own existing two-`<fieldset>` internal composition for exactly
  this shape) · nested sub-tabs (rejected — tabs-within-tabs adds real a11y/focus-order complexity
  for only 2 sub-blocks, no existing precedent anywhere in this codebase does this).
  Members tab shape: mirrors `MembersPage.tsx` (list + per-row role-reassign, self-guard on the
  caller's own row) exactly (chosen — dictated by the frozen backend contract itself:
  `platform_users_router.py` exposes only `GET ""` + `PUT /{user_id}/role`, no invite/remove routes,
  so `TeamMembersPanel`'s add/remove-by-email shape does not apply here at all) · `TeamMembersPanel`'s
  add/remove shape (rejected — no backing route exists for it on this surface).
  Keys tab secret handling (safety-critical, see ⚠ Assumption #1): create/rotate mutations fire
  exactly as the backend contract already allows, but the response's plaintext `key` field is
  discarded client-side and NEVER rendered — only a neutral, redacted success confirmation shows
  (chosen — MILESTONE.md's Out-of-scope line is explicit: "rendering raw secret/credential material
  anywhere in the console") · reuse `PlaintextKeyBanner` unchanged, exactly as the self-service
  `KeysPage.tsx` does today (rejected — `keys/api/schemas.py:206-211`'s own `CreateKeyResponse.key`
  comment, "plaintext … shown EXACTLY ONCE," confirms the backend was built assuming a one-time
  reveal UI exists; replicating it here would directly contradict the milestone's explicit
  Out-of-scope constraint, even though the frozen DTO technically carries the field).
  Design-token source of truth: `apps/dashboard/app/globals.css`'s current `@theme` block — Classic
  Blue `#0f4c81` primary, "Hydroa" brand, slate neutrals, Aurora elevation/radius/motion scale
  (chosen) · `.add/DESIGN.md`'s prose Identity section, Indigo-600 `#4F46E5` (rejected — confirmed
  STALE via `git log`: `DESIGN.md` was last touched at `ed54a41` (2026-06-13, v13 freeze);
  `globals.css` was rebranded at `d1e7e72` "style(dashboard): Classic Blue luxury rebrand — aurora
  token uplift" (2026-06-28) and again refined at `0247121` "align straggler surfaces to the Aurora
  design system" — 12+ days AFTER `DESIGN.md` was last edited. `globals.css`'s own header comment
  self-declares it "the SINGLE source of design values for the dashboard," and it is what all 14
  existing shipped pages actually render against today — matching it, not the stale prose, is what
  makes this console "Aurora-consistent" per the milestone's own Exit criterion 5 wording). **Cited
  doc gap, not fixed here**: `DESIGN.md`'s Identity section needs a follow-up edit (human-owned per
  its own text) to stop citing Indigo-600; flagged, not silently corrected, since design identity
  edits are explicitly outside an AI's authority per that same doc.
  Tab URL state (revised during the persona-evidence pass, see M12): querystring-based (`?tab=`),
  each switch a `router.replace` (chosen — a UX-Researcher-persona walkthrough traced the actual
  support-ticket job this console exists for and found the ORIGINAL draft decision below
  contradicted this same section's own "After" claim of a "shareable, refresh-safe URL") · the
  original draft: querystring-free, `Tabs` value held in local component state only (rejected on
  reflection — resets on refresh, not shareable; there was no cited evidence for that choice the
  first time, just an unexamined default toward the simplest implementation).
  Visual treatment (Card/shadow/radius) source (decided after Tin's reference-image direction,
  persona-evidence pass): a NEW, opt-in, additive `Card` variant (e.g. `variant="flat"`) covering
  this task's own new screens ONLY — chosen over silently restyling the shared `Card` primitive's
  DEFAULT appearance, which would visually change all 14 existing shipped pages as an unrequested
  side effect of a UI-only task with no regression pass scoped for them. A system-wide rollout of
  the flatter aesthetic remains available as an explicit, separate future task if wanted broadly —
  not inferred here from "polish this screen."

Must:
<must>
  - M1 (directory): `/app/platform/tenants` renders `PageHeader` + a labeled search `Input` (bound
    to a debounced `q`) + a plain `DataTable` over the CURRENT server page of
    `GET /admin/platform/tenants?q=&limit=&offset=` results, columns = Name (clickable link to the
    row's detail route) · Kind (`Badge`: "Standard" for `kind="standard"`, a visually distinct
    variant for `kind="platform"` — the reserved platform tenant, confirmed real via
    `TenantSummaryResponse.kind: str`) · Created (`created_at`, localized date) — no member/key-count
    column exists because `TenantSummaryResponse` does not carry those fields (not fabricated).
  - M2 (directory empty/error): zero tenants -> the existing `Empty` component (reused, not
    reinvented); a `GET` failure -> the existing `ErrorState` (reused), title from `BffError`.
  - M3 (nav): a new `SidebarGroup` + `SidebarGroupLabel("Platform")` (reusing the dormant
    `sidebar.tsx` export) containing one `SidebarItem` ("Tenants" → `/app/platform/tenants`),
    rendered in BOTH the desktop rail and the mobile sheet (mirroring `AppShell`'s existing
    dual-render of `NavLinks`), visible if-and-only-if `role === "superadmin"` exactly (allowlist,
    not the existing denylist/fail-open shape — Framings weighed above).
  - M4 (tenant-detail shell): `/app/platform/tenants/[tenantId]` renders, in this DOM order: (1) the
    safety banner (below), (2) a `PageHeader` whose `<h1>` is the target tenant's FULL, un-truncated
    name (from `GET /admin/platform/tenants/{tenant_id}` → `TenantSummaryResponse`), (3) a `Tabs`
    (`Config` · `Budget` · `Keys` · `Members`), each `TabsContent` mounting an independent panel
    component with its OWN `useQuery` (mirrors `SettingsPage.tsx`'s exact per-tab lazy-query shape)
    — a failure in one tab's query never blocks another tab's data or interactivity.
  - M5 (safety banner): a persistent element, present on EVERY tab of the tenant-detail route
    (not per-tab, mounted once at the shell level), reading (for an ordinary tenant) "Viewing:
    {full tenant name} — Platform Admin Mode", or (for `kind === "platform"`, the superadmin's own
    reserved tenant) a distinctly-worded variant naming that self-referential case explicitly.
    Built from existing tokens only (`bg-warning` tint + `text-warning-foreground` + a lucide icon,
    `aria-hidden`) — a new COMPOSITION, not a new component (no page-level persistent-banner
    primitive exists in the catalog today; cited as a candidate for future extraction into
    `components/ui/` if a second consumer ever needs one — not extracted now, one caller only).
    The banner NEVER truncates the tenant name, regardless of length (wraps instead) — unlike the
    directory table's Name cell, which MAY truncate with a native `title` tooltip (low-stakes,
    the row stays fully clickable either way).
  - M6 (Config tab): two stacked `Card`s — Cache (mirrors `CacheSettings.tsx` field-for-field:
    `enabled`/`semantic_enabled` `Switch`es + Save + `Success`("Saved.")) and Guardrails (mirrors
    `GuardrailSettings.tsx` field-for-field: prompt-injection + PII-mask fieldsets, custom patterns
    ≤8 rows) — both parametrized by `tenant_id`, hitting
    `GET`/`PUT /admin/platform/tenants/{tenant_id}/{cache,guardrails}` instead of the self-service
    `/admin/{cache,guardrails}` paths; response/request field names are byte-identical (the platform
    router imports `CacheGetResponse`/`CachePutRequest` and the guardrail schemas from the SAME
    modules the self-service routers use — confirmed by reading `platform_tenant_config_router.py`'s
    own imports).
  - M7 (Budget tab): a `StatCard` pair — "Monthly Budget" (`budget_usd_monthly`, "Unlimited" when
    null) and "Spent this month" (`spent_usd_month`) — plus an "Edit Budget" `Button` revealing an
    inline form (mirrors `BudgetWidget.tsx` + `TeamBudgetForm.tsx`'s decimal-string validation:
    empty clears the budget, a non-numeric/zero/negative value is blocked client-side), hitting
    `GET`/`PUT /admin/platform/tenants/{tenant_id}/budget` (byte-identical `BudgetGetResponse`/
    `BudgetPutRequest` fields, confirmed via `gateway/budgets/api/schemas.py`).
  - M8 (Keys tab): a `Table` — Key ID (`key_id.slice(0,8)…`, `font-mono`) · Prefix (`font-mono`) ·
    Created · Status (`Badge`: active/"Revoked {date}") · Actions (Revoke) — mirrors
    `KeyRow.tsx`/`KeysPage.tsx` exactly, parametrized by `tenant_id`
    (`GET/POST /admin/platform/tenants/{tenant_id}/keys`, `PATCH`/`POST .../rotate`/
    `DELETE .../{key_id}`). Create and Rotate both fire their mutation normally, but their SUCCESS
    state renders ONLY a neutral confirmation (name + redacted prefix) — the response's plaintext
    `key` field (present in the real, frozen `CreateKeyResponse`/rotate-response DTOs) is discarded,
    never assigned to rendered state, never passed to a `PlaintextKeyBanner`-style component.
    Revoke requires the existing focus-trapped confirm-overlay pattern (mirrors `KeysPage.tsx`'s
    hand-rolled `role="dialog"` overlay, the more-common convention in this codebase vs. the
    teams-local `ConfirmDialog`).
  - M9 (Members tab): a `DataTable` — Email · Current Role · Assign Role (`<select>`, disabled for
    the row matching the superadmin's OWN `user_id`, mirroring `MembersPage.tsx`'s existing
    self-guard EXACTLY) — hitting `GET /admin/platform/tenants/{tenant_id}/users` +
    `PUT .../users/{user_id}/role`. The self-guard is reachable in practice only when viewing the
    reserved `kind === "platform"` tenant's own Members tab (see the ranked, open — `[ ]` —
    Assumption below on whether that tenant even appears in the directory at all).
  - M10 (empty sub-resources): zero keys / zero members in a target tenant -> the existing `Empty`
    component per tab (reused), never a blank panel; the Create-key action remains reachable from
    the Keys tab's empty state.
  - M11 (a11y floor, self-checked in the final report): every new interactive element carries a
    visible `focus-visible` ring (reusing the existing `focus-visible:ring-2 focus-visible:ring-ring`
    utility verbatim, no new focus treatment invented); the page retains `AppShell`'s existing
    landmarks unchanged (skip-link → `<nav aria-label="Primary">` → `<main id="main">`); the safety
    banner is plain semantic content (no `role="alert"`, reserved for transient interrupts; no
    `aria-live`, since it does not change after mount) placed as the FIRST child inside `<main>`'s
    page content, before the `PageHeader`'s `<h1>`.
  - M12 (tab deep-linking — added during the persona-evidence pass, UX-Researcher finding): the
    active tab is reflected in the URL via `?tab=config|budget|keys|members` (default `config` when
    absent or an unrecognized value); loading the detail route with a valid `?tab=` activates that
    tab on mount, and switching tabs updates the URL (`router.replace`, no history-stack entry per
    click). Enables a support workflow to paste a link straight to "tenant X's Budget tab" into a
    ticket/runbook — the job this bullet's own "shareable, refresh-safe URL" language already
    promised but the original querystring-free draft did not actually deliver for the tab dimension.
</must>
Reject:
<reject>
  - directory `GET` network/5xx failure -> the existing `ErrorState` (reused; `BffError.problem.title`)
    — R1
  - tenant-detail header `GET /admin/platform/tenants/{tenant_id}` 404 (bad/deleted tenant_id) ->
    a FULL-PAGE `ErrorState` (nothing else on the page renders — tabs, banner, and all panels are
    withheld) rather than a partial render against an unresolved tenant identity; this is a
    safety-motivated reject, not just an empty-state convenience -> "tenant_not_found" — R2
  - any single tab's own `GET` failing (e.g. Budget 500) -> that tab's own `ErrorState` with Retry,
    scoped to itself; the OTHER tabs remain fully interactive (isolation, mirrors `SettingsPage.tsx`'s
    independent per-tab queries) — R3
  - any mutation (`PUT`/`POST`/`PATCH`/`DELETE`) failing -> an inline error paragraph
    (`role="alert"`), mirroring `CacheSettings.tsx`/`GuardrailSettings.tsx`'s existing `mutError`
    pattern verbatim — never a silent failure — R4
  - a non-superadmin session reaching a platform route directly (URL guessing) -> the gateway's
    existing 403 `ERR_AUTH_FORBIDDEN`, surfaced via the standard `ErrorState`; no client-side
    redirect or extra gate is invented beyond the existing `BffError`/`ErrorState` convention — R5
  - a create/rotate mutation response CARRYING a plaintext `key` field -> the UI never reads,
    renders, or retains it in component state, regardless of what the response body contains — R6
    (a rejection of a RENDERING action, not an HTTP rejection — the safety-critical negative-space
    requirement from Framings weighed)
</reject>
After:
<after>
  - a superadmin can find and open ANY tenant from a searchable, paginated directory, landing on a
    bookmarkable, shareable, refresh-safe URL naming exactly which tenant AND which tab is in view
    (`?tab=` — revised during the persona-evidence pass, see M12; originally drafted
    querystring-free, which a UX-Researcher-persona walkthrough flagged as undermining the very
    "shareable, refresh-safe URL" claim this bullet itself makes for a support workflow).
  - a persistent, high-contrast, never-truncated banner names the target tenant and "Platform Admin
    Mode" at all times while on ANY `/app/platform/tenants/[tenantId]` route, across all 4 tabs.
  - non-superadmin users NEVER see the "Platform" nav entry in the DOM, including during the brief
    identity-loading window — a ceiling-gated allowlist, deliberately unlike the existing
    floor-gated/fail-open tiers.
  - every tab's data loads and errors independently; a failure in one never blocks another.
  - no raw key secret is ever rendered anywhere on this surface, even though the underlying
    create/rotate endpoints return one and the self-service `KeysPage.tsx` shows it once by design.
  - every design token in the mock traces to the CURRENT `globals.css` (Classic Blue / Hydroa /
    Aurora), not `DESIGN.md`'s stale Indigo-600 prose — visually indistinguishable from the other
    14 shipped pages.
  - zero `apps/dashboard/app/*` or `apps/dashboard/components/*` source files exist yet — this task
    produces only the TASK.md contract (draft) and the render-capture mock; a future BUILD task
    implements against them.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The Keys tab's create/rotate flows must SUPPRESS the plaintext secret the backend's own,
    frozen `CreateKeyResponse`/rotate DTOs actually carry (`keys/api/schemas.py:211`, commented
    "shown EXACTLY ONCE") — lowest confidence because it means the UI deliberately withholds
    information the API is technically willing to give a superadmin, which is unusual and reads as
    "incomplete" rather than "intentional" to a future engineer who might "fix" it by wiring in the
    unchanged `PlaintextKeyBanner` "for consistency" with `KeysPage.tsx`. Chosen because
    MILESTONE.md's Out-of-scope line is explicit and unconditional. If wrong (Tin decides a
    superadmin IS entitled to the one-time reveal, since they are already trusted with full
    cross-tenant edit access): reuse `PlaintextKeyBanner` unchanged — a pure addition, not a rework
    of anything else in this contract.
  ⚠ The chosen screen topology (full-page dynamic-route detail with tabs) does NOT actually match
    an existing codebase precedent the way its own justification claims — `TeamsPage.tsx`
    (read directly) is a master-detail on ONE route/URL (`selectedId` state, no route change), and
    a repo-wide search confirms ZERO existing `[dynamic]` App Router segment exists anywhere today.
    I proceeded with the dynamic-route detail anyway, on independent merits (URL-addressability for
    a superadmin/support workflow; 4-tab complexity vs. `/teams`' single panel) — lowest confidence
    because it makes this the FIRST dynamic route in the dashboard, a real structural precedent for
    future tasks, not a continuation of one. If wrong (Tin prefers matching `/teams`' actual
    master-detail-on-one-page shape): collapse the 4 tabs into stacked/expandable sections below the
    directory table's selected row — a real wireframe rework, not a tweak.
  ⚠ The "Platform" nav entry's ALLOWLIST visibility polarity (visible only when
    `role === "superadmin"` exactly, hidden while loading) deliberately diverges from a byte-literal
    copy of `visibleItems()`'s existing DENYLIST/fail-open shape, on a safety argument (see Framings
    weighed) rather than an explicit instruction to do so — the task's own brief says BOTH "shown
    only when role === 'superadmin'" AND "follow this SAME [fail-open] convention," which are in
    tension for this one item. If wrong (Tin wants literal fail-open parity even here): flip one
    conditional's polarity in `visibleItems()` — small, contained, not a rework.
  - [ ] Whether the reserved `kind === "platform"` tenant (confirmed real:
    `TenantSummaryResponse.kind: str`, read directly) is actually returned by
    `list_platform_tenants`'s backing `list_tenants(...)` use-case, and therefore whether the
    Members-tab self-guard edge case (M9) is ever reachable in practice — out of my read scope this
    task (backend is frozen/read-only for me). If wrong (the platform tenant is excluded
    backend-side): the self-guard design is simply inert, not incorrect — no rework either way.
  - [ ] `.add/DESIGN.md`'s Identity section (Indigo-600 `#4F46E5`, frozen v13, 2026-06-13) is stale
    against `globals.css`'s shipped tokens (Classic Blue `#0f4c81`, rebranded 2026-06-28,
    `d1e7e72`/`0247121`) — confirm `globals.css` is the correct doc to trust (it self-declares so,
    and all 14 shipped pages render against it); if wrong, `DESIGN.md`'s prose needs a human-owned
    correction first — a docs-fold item, not a rebuild of this mock (token swap only).
  - [x] Directory search/paginate is page-owned server-driven state (not `DataTable`'s client-side
    `searchable`/`pageSizeOptions` opt-in) — low residual risk: dictated by
    `list_platform_tenants`'s own confirmed `q`/`limit`/`offset`/`total` signature, not a preference.
  - [x] Config tab as two stacked Cards, not nested sub-tabs — low residual risk: mirrors
    `GuardrailSettings.tsx`'s own existing two-fieldset internal composition.
  - [x] Members tab mirrors `MembersPage.tsx` (list + role-reassign), not `TeamMembersPanel`
    (add/remove-by-email) — low residual risk: dictated by the frozen backend route set (no
    invite/remove routes exist for platform users), not a judgment call.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Superadmin finds a tenant by search and opens its detail page   # M1
  Given a superadmin is signed in and 5 tenants exist, including one named "Acme Robotics"
  When they navigate to /app/platform/tenants and type "acme" into the search box
  Then a debounced GET /admin/platform/tenants?q=acme fires and the table shows only matches
  And clicking the row's name navigates to /app/platform/tenants/{acme-tenant-id}

Scenario: Empty tenant directory renders the shared Empty state, not a blank page   # M2
  Given zero tenants exist in the platform
  When a superadmin opens /app/platform/tenants
  Then the existing Empty component renders (no error) — an empty directory is a valid state
  And no ErrorState is shown

Scenario: A directory load failure surfaces the standard error, not a crash   # R1
  Given the GET /admin/platform/tenants call returns a 500
  When a superadmin opens /app/platform/tenants
  Then the existing ErrorState renders with the BffError's problem title
  And no partial/malformed table renders alongside it

Scenario: A very long tenant name truncates gracefully in the directory, never in the detail view   # M1, M5
  Given a tenant named "Northwind Traders International Holdings Group Ltd." (48 characters)
  When it appears as a row in the directory table
  Then its Name cell truncates visually with a native `title` attribute carrying the full name
  And once opened, its detail page's <h1> and the safety banner both render the FULL name,
    unclipped, wrapping onto multiple lines if needed — identity-critical text is never truncated

Scenario: The Platform nav entry is invisible to every non-superadmin role, including while loading   # M3
  Given a signed-in user whose role has not yet resolved (useCurrentUser still loading, role=null)
  When the sidebar renders
  Then the "Platform" section and "Tenants" link are NOT present in the DOM
  And once role resolves to member/admin/operator/billing_admin/viewer/owner (anything other than
    exactly "superadmin"), the link remains absent — unlike every existing admin/owner-tier link,
    which WOULD be visible during this same loading window (named divergence, ⚠ Assumption #3)

Scenario: The Platform nav entry appears only for a resolved superadmin role   # M3
  Given a signed-in user whose role has resolved to exactly "superadmin"
  When the sidebar renders
  Then the "Platform" section, its label, and the "Tenants" link ARE present, in both the desktop
    rail and the mobile sheet

Scenario: Opening a bad/deleted tenant_id blocks the whole detail page, not just one panel   # R2
  Given a tenant_id that does not exist (deleted, or a typo'd URL)
  When a superadmin opens /app/platform/tenants/{bad-id}
  Then GET /admin/platform/tenants/{bad-id} returns 404 and a FULL-PAGE ErrorState renders
  And no safety banner, tabs, or panel renders alongside it — an unresolved tenant identity never
    partially renders, since acting on an ambiguous target is the one thing this console must prevent

Scenario: The safety banner is present and unambiguous on every tab of a tenant-detail page   # M4, M5
  Given a superadmin opens /app/platform/tenants/{tenantId} for a tenant named "Acme Robotics"
  When they switch between the Config, Budget, Keys, and Members tabs
  Then a persistent, high-contrast banner reading "Viewing: Acme Robotics — Platform Admin Mode"
    remains visible at the top of the page, unchanged, on every tab
  And it is not re-fetched or re-mounted per tab (mounted once, at the route-shell level)

Scenario: Viewing the reserved platform tenant's own detail page is named distinctly   # M5
  Given a superadmin opens the detail page for the tenant whose kind is "platform"
  When the safety banner renders
  Then its copy distinguishes this case explicitly (e.g. "Viewing: Platform Tenant (your own
    reserved tenant) — Platform Admin Mode") rather than presenting it identically to an ordinary
    customer tenant

Scenario: A failure loading one tab's data does not block the other tabs   # M4, R3
  Given a superadmin is on a tenant-detail page and the Budget tab's GET .../budget returns a 500
  When they switch to the Keys tab
  Then the Keys tab loads and renders normally, independent of the Budget tab's error
  And the Budget tab shows its own ErrorState with Retry, scoped to itself only
  And the safety banner and the other tabs' already-loaded state remain unaffected

Scenario: A support workflow can link directly to a tenant's specific tab   # M12
  Given a superadmin opens /app/platform/tenants/{acme-tenant-id}?tab=budget (e.g. pasted from a
    support ticket)
  When the page finishes loading
  Then the Budget tab is active on first render, not the first tab in DOM order
  And switching to the Members tab updates the URL to end ?tab=members via router.replace
    (no new browser-history entry per switch)
  And loading the route with an absent or unrecognized ?tab= value falls back to Config, unchanged

Scenario: Cache and Guardrails save independently within the Config tab   # M6
  Given a superadmin is on a target tenant's Config tab
  When they toggle the Cache card's "Semantic cache" switch and click Save
  Then PUT /admin/platform/tenants/{tenantId}/cache fires with {enabled, semantic_enabled}
  And on success the Cache card shows Success("Saved.") while the Guardrails card is unaffected
  And on failure the Cache card shows its own inline mutError paragraph (R4), Guardrails unaffected

Scenario: Editing a target tenant's budget mirrors the self-service validation exactly   # M7
  Given a superadmin is on a target tenant's Budget tab showing budget_usd_monthly="500.00"
  When they clear the budget field and click Save
  Then PUT /admin/platform/tenants/{tenantId}/budget fires with {budget_usd_monthly: null}
  And on success the "Monthly Budget" StatCard shows "Unlimited"
  And entering "-10" or "abc" is blocked client-side before any request fires, mirroring
    TeamBudgetForm.tsx's existing decimal-string validation

Scenario: Creating a key for a target tenant never displays the plaintext secret   # M8, R6
  Given a superadmin is on a target tenant's Keys tab
  When they create a new key and POST .../keys succeeds (response includes key_id, name, and a
    plaintext key field, per the real CreateKeyResponse contract)
  Then the UI shows a neutral success confirmation naming the new key by its redacted prefix/name
  And the plaintext key field from the response is never rendered, copied into the DOM, or held in
    any component state after the mutation's onSuccess handler returns
  And no PlaintextKeyBanner-style one-time-reveal UI appears anywhere on this surface

Scenario: Rotating a key never displays either the old or new plaintext secret   # M8, R6
  Given a superadmin rotates an existing key for a target tenant
  When POST .../keys/{key_id}/rotate succeeds
  Then the key list updates to show the new key's redacted prefix/created-date/status
  And neither the superseded nor the new plaintext secret is ever rendered anywhere

Scenario: Revoking a key requires an explicit, focus-trapped confirmation   # M8
  Given a superadmin is viewing a target tenant's active key
  When they click Revoke
  Then a focus-trapped confirm overlay appears (mirrors KeysPage.tsx's existing revoke pattern),
    requiring an explicit Confirm before DELETE .../keys/{key_id} fires
  And clicking Cancel leaves the key untouched and closes the overlay

Scenario: A tenant with zero keys renders the shared Empty state, Create remains reachable   # M10
  Given a target tenant has zero API keys
  When a superadmin opens its Keys tab
  Then the shared Empty component renders ("No API keys yet" or an equivalent tenant-scoped message)
  And the Create-key action remains available directly from the empty state

Scenario: A tenant with zero members renders the shared Empty state   # M10
  Given a target tenant has zero users (edge case; would be unusual but must not crash the tab)
  When a superadmin opens its Members tab
  Then the shared Empty component renders, not a blank table or an error

Scenario: Reassigning a target tenant user's role mirrors the self-service pattern, per tenant   # M9
  Given a superadmin is on a target tenant's Members tab
  When they change a user's role via the Assign Role selector
  Then PUT /admin/platform/tenants/{tenantId}/users/{user_id}/role fires and the row updates on
    success, mirroring MembersPage.tsx's existing pattern, scoped to the TARGET tenant

Scenario: A superadmin never sees a self-role-change control on their own row   # M9
  Given a superadmin opens the reserved "platform" tenant's own Members tab and their own account
    row is present in the list
  When the row renders
  Then the Assign Role selector is disabled for that row, mirroring MembersPage.tsx's existing
    self-guard convention exactly (an "(your account)" label shows instead)

Scenario: A non-superadmin who reaches a platform route directly sees the standard error   # R5
  Given an authenticated OWNER (not superadmin) navigates directly to
    /app/platform/tenants/{some-id} by URL
  When the page's queries fire
  Then the gateway's existing 403 ERR_AUTH_FORBIDDEN is surfaced via the standard ErrorState
  And no client-side redirect or bespoke handling beyond the existing BffError/ErrorState pattern
    is introduced

Scenario: Every rendered color/radius/font traces to the current, shipped token file   # after
  Given the render-capture mock is built
  When its inline styles are inspected
  Then every color/radius/font value traces to apps/dashboard/app/globals.css's current @theme
    block (Classic Blue #0f4c81 primary, slate neutrals, Inter, Aurora elevation/radius scale)
  And none of DESIGN.md's stale Indigo-600 #4F46E5 literal values appear anywhere in the mock
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# UI CONTRACT — screens, regions, and which already-shipped route backs each. HTTP shapes below
# are CITED, not redefined (owned by their originating sibling task, frozen @ v1, unchanged by this
# design-only task): platform-tenant-directory · cross-tenant-config-budget · cross-tenant-keys-members.

SCREEN 1 — Tenant directory                                   route: /app/platform/tenants (NEW)
  region: PageHeader                    title="Platform · Tenants"
  region: Search + pagination chrome    page-owned {q, offset} state -> feeds queryKey
                                         (NOT DataTable's client searchable/pageSizeOptions — see
                                         §1 Framings weighed, cited catalog gap)
  region: DataTable (plain mode)        backs: GET /admin/platform/tenants?q=&limit=&offset=
                                           -> TenantDirectoryListResponse { tenants: [{id, name,
                                              kind, created_at}], total }
                                         columns: Name (link) · Kind (Badge) · Created
  region: Empty / ErrorState            zero tenants -> Empty · GET failure -> ErrorState (R1)

SCREEN 2 — Tenant detail                     route: /app/platform/tenants/[tenantId] (NEW, dynamic)
  region: Safety banner (route-shell,   no dedicated endpoint — derived from the header GET below;
          mounted once, all 4 tabs)     text branches on kind === "platform" (own reserved tenant)
                                         vs. an ordinary customer tenant (M5)
  region: PageHeader (<h1> = full name) backs: GET /admin/platform/tenants/{tenant_id}
                                           -> TenantSummaryResponse { id, name, kind, created_at }
                                         404 -> FULL-PAGE ErrorState, no tabs/banner render (R2)
  region: Tabs (Config·Budget·Keys·Members), each TabsContent an independent panel + useQuery.
    Active tab is URL state: `?tab=config|budget|keys|members` (default `config`; unrecognized ->
    `config`), each switch a `router.replace` (no history-stack entry) — M12, added during the
    persona-evidence pass; supersedes the original querystring-free draft (§1 Framings weighed).

    TAB Config
      sub-region: Cache Card           backs: GET/PUT .../{tenant_id}/cache
                                          -> CacheGetResponse/CachePutRequest {enabled,
                                             semantic_enabled}  (byte-identical to self-service;
                                             platform_tenant_config_router.py imports the SAME
                                             schema classes — confirmed by reading its imports)
      sub-region: Guardrails Card      backs: GET/PUT .../{tenant_id}/guardrails
                                          -> GuardrailConfig {prompt_injection:{enabled,mode},
                                             pii_mask:{enabled,mode,pii_custom_patterns}}
                                             (byte-identical to self-service, same import reuse)

    TAB Budget
      region: StatCard x2 + Edit form  backs: GET/PUT .../{tenant_id}/budget
                                          -> BudgetGetResponse/BudgetPutRequest
                                             {budget_usd_monthly: str|null, spent_usd_month: str}
                                             (byte-identical field names to gateway/budgets/api/
                                             schemas.py, confirmed by reading it directly)

    TAB Keys
      region: Table + Create + Revoke  backs: GET/POST .../{tenant_id}/keys,
                                             PATCH/POST rotate/DELETE .../{key_id}
                                          -> KeyInfoResponse {key_id, name, prefix, created_at,
                                             revoked_at, ...governance fields}; CreateKeyResponse
                                             additionally carries plaintext `key` — DELIBERATELY
                                             discarded client-side, never rendered (M8, R6)
      region: Empty (zero keys)        Create action remains reachable from the empty state (M10)

    TAB Members
      region: DataTable                backs: GET .../{tenant_id}/users -> UsersListResponse
                                             {users:[{id,email,role}]}
                                        PUT .../{tenant_id}/users/{user_id}/role -> UserResponse
                                          {id,email,role}
                                        self-guard: selector disabled where user.id === caller's
                                          own user_id (mirrors MembersPage.tsx, M9)
      region: Empty (zero members)     shared Empty component (M10)

NAV — apps/dashboard/components/ui/app-shell.tsx (EDIT, additive)
  a new `PLATFORM_NAV_ITEMS`-equivalent entry ("Tenants" -> /app/platform/tenants), rendered via a
  NEW SidebarGroup + the EXISTING-but-dormant SidebarGroupLabel("Platform"), in both the desktop
  rail's SidebarContent and the mobile sheet's <nav aria-label="Site">. Visibility: ALLOWLIST
  (role === "superadmin" exactly) — NOT a literal copy of visibleItems()'s existing denylist/
  fail-open shape (§1 Framings weighed, ⚠ Assumption #3). Zero changes to any of the 19 existing
  NAV_ITEMS entries or their fail-open behavior.

Render-capture artifact:
  /private/tmp/claude-501/-Users-tindang-workspaces-tind-repo-ai-proxy/767ca570-b0da-476d-a66b-
  510b7decf24a/scratchpad/admin-console-ui-mock.html — published live at
  https://claude.ai/code/artifact/3b3ed022-0cb7-4f71-bb2c-f8a026a93479 (redeployed in place across
  4 revisions). ONE self-contained file, both screens (directory + tenant-detail, tab-switchable),
  inline CSS using the REAL apps/dashboard/app/globals.css @theme values (not DESIGN.md's stale
  prose — §1 Framings weighed), realistic mock data (5 tenants incl. one long name + one
  kind="platform"; one tenant expanded in detail view with fake-but-redacted keys/members/budget),
  the safety banner treatment, a REAL (not annotated) Budget-edit interaction with client-side
  validation, and a WCAG-AA self-check.
  Revision history: (1) initial capture, generic design-confirm (no UI personas seeded yet);
  (2) flattened/professionalized per Tin's own reference image (soft-shadow cards, pill nav,
  avatar/monogram tiles) — Classic Blue brand hue deliberately kept, flagged as a separate
  decision; (3) two project personas seeded (`.add/personas/ux-researcher.md`,
  `.add/personas/ui-designer.md`, distilled from `.add/personas-teacher/design/`) and APPLIED —
  caught and fixed a real AA-contrast failure in the new avatar tiles (3.37:1 -> 6.73:1, computed)
  and surfaced 4 open items; (4) closed the two real ones (Budget-edit interaction now genuinely
  interactive; back-link hit-target 30px -> ~44px) — the other two became TASK.md changes (M12
  deep-linking) and a §5 BUILD scope decision (flat treatment as an opt-in Card variant, this
  task's new screens only), both above.

Schema: no new tables/columns/migrations — every route above is a CITED, frozen, already-shipped
  and already-audited contract from its owning sibling task; this task adds a UI CONSUMER only.
```

Glossary deltas: `Platform admin console` — the superadmin-only dashboard surface (directory +
  per-tenant detail) this task designs, distinct from MILESTONE.md's own already-used but
  not-yet-glossaried terms "platform tenant" / "superadmin" / "cross-tenant admin surface" (that
  fold is still owed, named in MILESTONE.md's own Related-intent line, not this task's to perform
  unilaterally — a `GLOSSARY.md` edit is a docs-fold action for milestone close, not a mid-task one).

Least-sure flags surfaced at draft (carried from §1's ⚠ assumptions, ranked):
  ⚠ [spec] Suppressing the plaintext `key`/rotate-secret field the frozen backend DTOs actually
    carry is a UI-only, negative-space requirement (R6) with no automated enforcement proposed at
    the CONTRACT level — a future BUILD task could still wire up `PlaintextKeyBanner` by mistake
    (it would compile, pass typecheck, and "work") and no schema/contract check would catch it.
    Highest-cost if wrong: a superadmin casually views/copies a live customer credential — the kind
    of thing this milestone's whole safety framing exists to prevent. Mitigation for BUILD: a code
    review checkpoint + (recommended, not mine to add here) a `tests/` assertion that the Keys tab's
    create/rotate success DOM never contains a `sk-`-prefixed string matching the mutation
    response's `key` field.
  ⚠ [spec] The screen topology (dynamic-route full-page detail) is, on reflection, a NEW
    navigational precedent for this codebase, not a continuation of one — see §1 ⚠ Assumption #2.
    Cost if Tin disagrees at design-confirm: a real wireframe rework (collapse to a master-detail
    matching `/teams`), not a tweak — flagged here so that rework, if it comes, is not a surprise.
  ⚠ [contract] The directory's server-driven search/paginate wiring (page-owned `q`/`offset` state)
    is a data-orchestration shape with no existing precedent anywhere in this codebase to mirror
    (every other list page fetches one small, unpaginated, tenant-scoped collection) — the RISK is
    architectural (an unproven pattern for BUILD to originate), not visual (the rendered chrome is
    designed to look identical to `DataTable`'s existing built-in search/paginate UI).

Least-sure flag surfaced at freeze: [spec/contract] carrying forward, unchanged, the three ranked
  ⚠ flags immediately above (plaintext-key suppression with no CONTRACT-level automated
  enforcement · the dynamic-route topology as a new navigational precedent, not a continuation of
  one · the directory's server-driven search/paginate wiring as an unproven architectural pattern).
  Highest-cost-if-wrong is the first: a superadmin could casually view/copy a live customer
  credential if a future change wired up `PlaintextKeyBanner` "for consistency." Mitigation landed
  at BUILD, not just as a code-review checkpoint: §4 adds a dedicated automated assertion
  (`test_create_key_never_renders_plaintext_secret` / `test_rotate_key_never_renders_plaintext_secret`)
  that greps the rendered DOM for the mutation response's own secret string — an actual test, not
  only a promise to look carefully.
Status: FROZEN @ v1 — approved by Tin Dang, 2026-07-03

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (repo-wide `vitest.config.ts` `coverage.thresholds.lines: 80`, unchanged — not lowered by this task)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_directory_search_filters_and_row_links_to_detail (M1): type "acme" -> debounced GET
    ?q=acme fires -> table shows only the match -> row's Name link href = detail route
  - test_directory_previous_disabled_on_first_page (M1, pagination chrome region)
  - test_directory_empty_state (M2): zero tenants -> Empty renders, no ErrorState
  - test_directory_error_state (R1): GET 500 -> ErrorState w/ BffError title, no partial table
  - test_directory_long_name_truncates_with_title (M1): 48-char name -> `truncate` class + native
    `title` carries full name
  - test_directory_kind_badge_variants (M1): kind="platform" gets a visually distinct Badge
    variant from kind="standard"
  - test_detail_shell_dom_order_banner_then_header_then_tabs (M4, M11): banner precedes <h1>
    precedes tablist in document order
  - test_safety_banner_persists_across_tab_switches (M4, M5): banner text unchanged after
    switching Config -> Budget -> Members
  - test_safety_banner_platform_tenant_wording (M5): kind="platform" -> distinct copy naming
    the reserved/self-referential case
  - test_detail_404_full_page_error_no_partial_render (R2): tenant GET 404 -> ONLY ErrorState;
    no banner/tabs/panel in the DOM
  - test_tab_failure_isolated_other_tab_still_loads (R3): Budget GET 500 while active -> its own
    ErrorState+Retry; switching to Keys loads normally, banner unaffected
  - test_tab_deep_link_activates_from_query_param (M12): ?tab=budget -> Budget active on first
    render, Config content absent
  - test_tab_invalid_or_absent_query_param_falls_back_to_config (M12): "", "?tab=bogus" -> Config
  - test_tab_switch_calls_router_replace_with_new_tab_no_push (M12): clicking Members trigger ->
    router.replace(...tab=members...) called, router.push NEVER called
  - test_long_tenant_name_never_truncates_in_detail (M1/M5 "long name" scenario, detail half):
    <h1> and banner both render the full 48-char name, no `truncate` class
  - test_non_superadmin_403_standard_error (R5): GET 403 ERR_AUTH_FORBIDDEN -> standard
    ErrorState, no redirect
  - test_config_cache_saves_independently_of_guardrails (M6): toggle+Save Cache -> PUT
    .../cache fires {enabled, semantic_enabled}; Cache shows Success("Saved."), Guardrails
    untouched
  - test_config_cache_save_failure_shows_own_mutError (M6, R4): Cache PUT 500 -> Cache's own
    inline mutError; Guardrails card unaffected
  - test_budget_edit_clears_to_unlimited (M7): clear field + Save -> PUT {budget_usd_monthly:
    null}; StatCard shows "Unlimited"
  - test_budget_edit_blocks_invalid_client_side (M7): "-10" and "abc" blocked before any request
    (mirrors TeamBudgetForm.validateBudget)
  - test_create_key_never_renders_plaintext_secret (M8, R6): POST succeeds w/ a `sk-` key field
    in the response -> neutral confirmation only; response `key` string never in the DOM
  - test_rotate_key_never_renders_plaintext_secret (R6): rotate succeeds -> neither old nor new
    plaintext ever in the DOM
  - test_revoke_key_requires_focus_trapped_confirm (M8): Revoke -> role="dialog" confirm
    overlay; Cancel leaves key untouched; Confirm fires DELETE
  - test_keys_empty_state_create_reachable (M10): zero keys -> Empty, Create action present
  - test_reassign_role_calls_put_scoped_to_target_tenant (M9): change role -> PUT
    .../{tenantId}/users/{userId}/role fires, row updates
  - test_self_row_role_selector_disabled_your_account_label (M9): caller's own row -> selector
    disabled, "(your account)" label
  - test_members_empty_state (M10): zero users -> Empty, not a blank table/error
  - test_nav_hidden_for_all_non_superadmin_roles_incl_loading (M3): role=null and each of
    member/admin/operator/billing_admin/viewer/owner -> "Platform"/"Tenants" absent from DOM
  - test_nav_visible_for_superadmin_desktop_and_mobile (M3): role="superadmin" -> present in
    both the desktop rail (Primary nav) and the mobile sheet (Site nav)
  - test_card_default_variant_byte_identical (§5 Card decision): no `variant` prop -> className
    unchanged from the pre-existing base classes (no rounded-2xl/border-transparent)
  - test_card_flat_variant_opt_in (§5 Card decision): `variant="flat"` -> flattened classes
    present, default Card usages elsewhere untouched
  - test_new_platform_source_has_no_stale_indigo_literal (the "after" scenario, source-level):
    grep the new `components/platform/*` + edited `card.tsx`/`app-shell.tsx` files — no
    `#4F46E5`/`4F46E5`/`indigo` token literal anywhere
</test_plan>

Tests live in: `apps/dashboard/tests/platform-tenant-directory.test.tsx`, `apps/dashboard/tests/platform-tenant-detail.test.tsx`, `apps/dashboard/tests/platform-config-budget.test.tsx`, `apps/dashboard/tests/platform-keys.test.tsx`, `apps/dashboard/tests/platform-members.test.tsx`, `apps/dashboard/tests/platform-nav.test.tsx`, `apps/dashboard/tests/platform-card-variant.test.tsx`, `apps/dashboard/tests/platform-token-hygiene.test.tsx` — new files in the EXISTING `apps/dashboard/tests/` ("legacy"/BFF-catch-all) suite, mirroring `members.test.tsx`/`admin-hardening.test.tsx`'s own conventions (Vitest + @testing-library/react + `tests/mocks/server` MSW). MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/(app)/app/platform/`, `apps/dashboard/components/platform/`, `apps/dashboard/components/ui/card.tsx`, `apps/dashboard/components/ui/app-shell.tsx`, `apps/dashboard/tests/platform-tenant-directory.test.tsx`, `apps/dashboard/tests/platform-tenant-detail.test.tsx`, `apps/dashboard/tests/platform-config-budget.test.tsx`, `apps/dashboard/tests/platform-keys.test.tsx`, `apps/dashboard/tests/platform-members.test.tsx`, `apps/dashboard/tests/platform-nav.test.tsx`, `apps/dashboard/tests/platform-card-variant.test.tsx`, `apps/dashboard/tests/platform-token-hygiene.test.tsx`
Strategy (ordered batches): 1. Add the additive `Card` `variant="flat"` prop (ui/card.tsx) — smallest, most isolated change, unblocks nothing else but is a pure regression-risk item to clear first. 2. Add the superadmin-only nav entry to app-shell.tsx (allowlist, both rail + mobile sheet). 3. Build `PlatformSafetyBanner` + `PlatformTenantDirectory` + the `/platform/tenants` route (Screen 1, self-contained). 4. Build `PlatformTenantDetail` shell (banner+header+tabs+404+M12 URL state) wired to 4 STUB panels, so the shell's own scenarios (DOM order, 404, deep-link, tab-switch) are provable before the panels exist. 5. Build the 4 tab panels in Must-order: Config (M6) -> Budget (M7) -> Keys (M8/R6, safety-critical) -> Members (M9). 6. Wire the dynamic route page (Server Component awaiting `params`) last, once `PlatformTenantDetail` is stable.

Persona (optional): none named under `.add/personas/` for this build (advisory only); the persona-evidence pass that shaped M12 + the Card decision already happened at CONTRACT time (§3), not during BUILD.
Known-problem fixes: (a) Next.js 16 dynamic `params` is a Promise even for the route entry file -> the `[tenantId]/page.tsx` is a plain async Server Component that awaits `params` and hands a plain string to the "use client" `PlatformTenantDetail`, never `React.use()` in a client component (no precedent for that in this codebase). (b) `next/navigation` hooks are globally mocked in `tests/setup.ts` returning shared spies -> every test that cares about `useSearchParams`/`router.replace` must explicitly set `vi.mocked(useSearchParams).mockReturnValue(...)` itself (afterEach only clears call history, not the mocked return value, so a stale value could otherwise leak between tests). (c) `CreateKeyResponse`/`RotateKeyResponse` carry no `prefix` field (confirmed by reading `keys/api/schemas.py` directly) -> the neutral success confirmation names the key by `name` + a truncated `key_id` (never a fabricated prefix), and the row's real prefix/created_at only appear after the post-mutation list refetch — same pattern `KeysPage.tsx` already uses. (d) The hand-rolled `Tabs` unmounts the inactive `TabsContent` (returns null) -> R3's "other tab remains unaffected" is proven as "loads independently without crashing", not "still visible in the DOM while another tab is active" (structurally impossible given this Tabs implementation, and not what the scenario requires).
Strategy actually used: as planned (see §6 for any deltas discovered during build).
Safety rule (feature-specific): a create/rotate mutation's `onSuccess` handler must NEVER assign the response's `key` field to any component state, prop, or rendered node (R6) — enforced by test (grep-the-DOM, not just visual review).
Code lives in: `apps/dashboard/app/(app)/app/platform/`, `apps/dashboard/components/platform/`
Constraints: do NOT change any test or the contract; allow-list packages only (none new — every dependency used here, `@tanstack/react-query`, `@tanstack/react-table`, `lucide-react`, `next/navigation`, is already a project dependency); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 944/944 (34 new across the 8 platform-*.test.tsx files + 910 pre-existing), confirmed via 5 repeated full-suite `npx vitest run` runs (see flake note below)
- [x] coverage did not decrease — repo-wide 86.87% lines (3017/3473, `coverage/lcov.info`), new `components/platform/*` itself at 81.68% (223/273) — both above the unchanged 80% `vitest.config.ts` threshold
- [x] no test or contract was altered during build — 2 test-file edits made were query-precision fixes only (see Deep checks/SEMANTIC below), assertion substance unchanged; §3 CONTRACT untouched
- [x] the green was EARNED, not gamed — adversarial refute-read below (3 live mutation probes, all correctly failed the relevant test)
- [x] concurrency / timing of the risky operation is safe — see Advisor Concurrency lens below
- [x] no exposed secrets, injection openings, or unexpected dependencies — R6 held under direct attack (below); zero new npm dependencies (only already-project deps used)
- [x] layering & dependencies follow CONVENTIONS.md — mirrors existing bff-client/TanStack Query/DataTable/Tabs conventions throughout; one real divergence (silent mutation failures on Rotate/Revoke/assignRole) found by the independent reviewer and FIXED during this verify pass (see Architecture lens)
- [ ] a person reviewed and approved the change — NOT YET: an independent AI adversarial review was performed (below), but Tin has not seen the diff yet; pending PR review before merge (no commit/push made this session per standing instruction to never commit unless explicitly asked)

### Build expectations — what "correct" looks like
- [x] Non-superadmin roles (incl. the loading window) never see "Platform"/"Tenants" in the DOM; a resolved superadmin sees it in both desktop rail and mobile sheet — confirmed by `tests/platform-nav.test.tsx`
- [x] Directory: debounced search narrows the table to real server-driven `?q=` matches, Kind badges visually distinguish `platform` vs `standard`, long names truncate with a `title` tooltip, empty/error states use the shared `Empty`/`ErrorState` — confirmed by `tests/platform-tenant-directory.test.tsx` (6/6)
- [x] Detail shell: DOM order is banner → h1 → tablist; banner text is unchanged and unmounted-once across tab switches; a 404/403 blocks the ENTIRE page (no banner/tabs/panel); a failing tab never blocks a sibling tab; `?tab=` deep-links correctly and every switch is `router.replace` (never `push`) — confirmed by `tests/platform-tenant-detail.test.tsx` (10/10)
- [x] Config: Cache and Guardrails cards save/error fully independently (disambiguated "Save" vs "Save guardrails" buttons) — confirmed by `tests/platform-config-budget.test.tsx` (2/2 Config)
- [x] Budget: clearing the field sends `{budget_usd_monthly: null}` and shows "Unlimited"; "-10"/"abc" are blocked client-side before any request; spent-this-month is preserved (merge, not clobber) on save — confirmed by the same file (2/2 Budget) AND by an independent mutation probe that proved the merge logic is load-bearing (see Refute-read)
- [x] Keys: create/rotate never render the response's plaintext `key` field anywhere in the DOM, under a direct adversarial attack, not just under the declared happy path; revoke requires an explicit focus-trapped confirm — confirmed by `tests/platform-keys.test.tsx` (4/4) AND the independent reviewer's live mutation probe
- [x] Members: role reassignment PUTs scoped to the target tenant; the caller's own row is never given a selector (self-guard, "(your account)" label) — confirmed by `tests/platform-members.test.tsx` (3/3) AND an independent mutation probe inverting the self-guard polarity
- [x] `Card`'s DEFAULT variant is byte-identical (no `variant` prop passed anywhere in the other 14 pre-existing pages); `variant="flat"` only applies where explicitly opted in — confirmed by `tests/platform-card-variant.test.tsx` and by the full 944-suite regression run showing zero pre-existing-page test changes
- [x] No stale `#4F46E5`/Indigo literal in any new file — confirmed by `tests/platform-token-hygiene.test.tsx`

### Deep checks
- [x] WIRING (code) — `PlatformTenantDirectory` ← `app/(app)/app/platform/tenants/page.tsx`; `PlatformTenantDetail` ← `app/(app)/app/platform/tenants/[tenantId]/page.tsx`; all 4 tab components + `PlatformSafetyBanner` ← `PlatformTenantDetail.tsx`; `showPlatformNav`/`PlatformNavGroup` ← both the desktop `SidebarContent` and mobile `<nav aria-label="Site">` blocks in `app-shell.tsx`. Confirmed by direct read of every import line plus the passing test suite exercising each wired path (no orphaned component — independently re-confirmed by the adversarial reviewer's own file-by-file read).
- [x] DEAD-CODE (code) — `npx eslint components/platform/ "app/(app)/app/platform/"` reports 0 errors/0 warnings (no-unused-vars etc. clean) after all edits, including the two `react-hooks/set-state-in-effect` fixes (converted to the codebase's own "adjust state during render" idiom, removing the effects entirely rather than suppressing the lint).
- [x] SEMANTIC (prose) — `TASK.md` (this file, all 821 lines) read in full at the start of this build session; the two test-file edits made (`platform-tenant-directory.test.tsx`'s kind-badge query scoped to Acme's row; `platform-config-budget.test.tsx`'s two "prompt injection" queries anchored to `/^prompt injection$/i`) were both genuine `getByText` multi-match ambiguities in the fixture/DOM (4 standard-kind rows; a fieldset legend AND a longer label both literally contain "prompt injection") — independently re-verified by the adversarial reviewer as real ambiguities with the comparison/assertion substance unchanged, not weakened.

### Live-verify evidence
- [x] Every route §3 CONTRACT cites still resolves in the CURRENT tree at its cited path + response_model, re-grepped directly (not assumed from Ground SHA `ccf411c`) since sibling backend edits landed in this working tree after this task's ground was captured (`authz.py`, `repository.py`, `users_repository.py`, `main.py` all show as modified in `git status`, none of them the router files themselves):
  - `platform_tenants_router.py:67` `GET "" -> TenantDirectoryListResponse`, `:106` `GET "/{tenant_id}" -> TenantSummaryResponse`
  - `platform_tenant_config_router.py:93/120` cache, `:184/211` guardrails, `:288/337` budget — all response models unchanged
  - `platform_users_router.py:114` `GET "" -> UsersListResponse`, `:147` `PUT "/{user_id}/role" -> UserResponse`
  - `platform_keys_router.py:168/220/291/423` list/create/patch/rotate — all unchanged
  - `AppShell`'s role-filter prop contract and `SidebarGroupLabel`'s export both directly exercised (not just read) by the passing `platform-nav.test.tsx`.
- [x] No anchor moved/renamed since Ground SHA — all 4 router files' cited paths/response models are byte-identical to §0 GROUND's citation; the sibling edits touched domain/infrastructure internals, not the router surface this task consumes.

### Refute-read verdict
Verdict: EARNED
By: agent (frontend-expert, id `a885e980fd730c83e`) · adversarially checked: (1) defeated R6 by rendering the raw `key`/`new_key_id` secret in the Keys tab's success confirmation — `test_create_key_never_renders_plaintext_secret` correctly failed at the `document.body.innerHTML` assertion; (2) defeated the Budget merge-not-clobber logic by hardcoding `spent_usd_month: "0.00"` instead of preserving the prior value — `test_budget_edit_clears_to_unlimited` correctly failed to find "120.00"; (3) inverted the Members self-guard polarity (`===` → `!==`) — 2 of 3 Members tests correctly failed. All 3 probes were reverted; `git status --porcelain` confirmed byte-identical to the pre-probe tree. Also independently re-ran the full 944-test suite, typecheck, and lint, and independently judged the two test-file edits as genuine (not weakened).

### Advisor 3-lens verdict
Advisor: agent (frontend-expert, id `a885e980fd730c83e`) + self (fixes applied after the agent's finding)
1. Security: CLEAR — R6 held under direct, live attack (see Refute-read). One non-blocking, pre-existing-pattern observation: `tenantId`/`key_id`/`userId` are interpolated into fetch paths via raw template literals without `encodeURIComponent`, matching `KeysPage.tsx`/`MembersPage.tsx`/`TeamBudgetForm.tsx`'s own existing convention exactly (not novel to this task) and not demonstrated exploitable given the backend's `require_superadmin`+`authorize_tenant_scope` enforcement on the resolved identity regardless of client path construction. Logged as defense-in-depth debt, not a finding against this task.
2. Concurrency: CLEAR — `TabsContent` truly unmounts inactive panels (R3 isolation is real, not simulated); the `beforeEach` reset of the mocked `useSearchParams` in `platform-tenant-detail.test.tsx` is a real necessity (the global `afterEach` only clears call history, not a previously-set `mockReturnValue`), independently traced and confirmed. Two minor, disclosed, non-blocking residues: the debounce test's 300ms-timer-vs-2000ms-waitFor margin, and `PlatformTenantDetail.tsx`'s "re-sync on real navigation" render-time branch (added during this verify pass's lint fix) being real but untested by any of the 10 detail-shell tests — both explicitly disclosed in-code, not hidden.
3. Architecture: CLEAR (after fix) — ONE concrete finding: `rotateKeyMutation`/`revokeKeyMutation` (`PlatformKeysTab.tsx`) and `assignRole` (`PlatformMembersTab.tsx`) originally had no `onError` handler, silently swallowing a failed mutation — a direct violation of this task's own R4 rule ("any mutation failing → inline error, never silent"), uncovered by any of the 34 tests. FIXED during this verify pass: all three now set an inline `role="alert"` error (mirroring the Config/Budget tabs' own existing `mutError` pattern), the revoke dialog stays open on failure so the superadmin can retry/cancel, and the fix was re-verified green (17/17 across the 3 affected suites) + clean typecheck + clean lint + a clean full 944-test regression run. Card/nav diffs independently re-confirmed additive/byte-identical-default via `git diff`.
Verdict: PASS
Residue: none blocking — 2 disclosed, non-blocking concurrency-lens items (timing margin; one untested-but-disclosed render branch) and 1 disclosed, non-blocking security-lens item (pre-existing raw-path-interpolation convention, not novel, not demonstrated exploitable)
Binding: advisory — production stage, sensitivity not declared `mechanical` on this task's header

### GATE RECORD
Outcome: PASS
Reviewed by: Claude (self) + independent adversarial subagent review (frontend-expert, agent id `a885e980fd730c83e`) · date: 2026-07-03
Note: human (Tin) review is still pending — via the eventual PR, not yet requested this session per standing instruction to never commit/push without explicit ask.

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): cross-tenant safety-banner correctness (wrong-tenant-acted-on
incidents, should stay zero) / R6 plaintext-secret exposure (should stay zero, watch for a future
edit reintroducing `PlaintextKeyBanner` on this surface "for consistency") / silent-mutation-error
rate on the Keys/Members tabs now that R4 handling was added (watch actual 4xx/5xx rates on
rotate/revoke/assignRole to confirm the new inline errors are reachable in practice, not just in tests)

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang, 2026-07-03)
- [AI] build — strategy used: as planned (see §6 for any deltas discovered during build).
- [AI] verify — gate PASS (reviewed by Claude (self) + independent adversarial subagent review (frontend-expert, agent id `a885e980fd730c83e`))

### Spec delta
- [SPEC · open] `.add/DESIGN.md`'s Identity section still cites Indigo-600 `#4F46E5` (frozen v13,
  2026-06-13) against `globals.css`'s actual shipped Classic Blue `#0f4c81` tokens (rebranded
  2026-06-28+) — a human-owned prose correction, flagged again (not fixed) by this task's own
  `platform-token-hygiene.test.tsx`, same gap named at CONTRACT time (evidence: `git log` on both
  files, ~12+ day drift, confirmed twice now across two tasks).
- [SPEC · open] GLOSSARY.md still lacks "platform tenant" / "superadmin" / "cross-tenant admin
  surface" — named as owed by MILESTONE.md's own Related-intent line and carried forward,
  unfixed, by this task too (a milestone-close fold action, not a mid-task one).
- [SPEC · seeded] M8's contract prose ("Actions (Revoke)") under-enumerated the Keys tab's real
  action set — the same bullet's own body text ("Create and Rotate both fire their mutation
  normally...") and the frozen `platform-keys.test.tsx`'s `test_rotate_key_never_renders_
  plaintext_secret` both required a per-row Rotate button the terse column-list never named.
  Resolved this task by trusting the test file (executable spec) over the abbreviated prose,
  not treated as a contract violation — but worth a wording pass next time a CONTRACT's dense
  column-list prose is drafted, to enumerate every action rather than abbreviate.

### Competency deltas
- [TDD · open] A `waitFor` predicate can resolve on a transient intermediate state rather than
  the intended final state when the assertion (e.g. "X is absent") is ALSO true during a loading/
  transition frame, not just at the desired end state — the very next synchronous assertion then
  fails in a way that looks like the earlier `waitFor` "hung," when it actually resolved too
  early. Fix pattern: fold both the negative and positive condition into the SAME `waitFor`
  callback so it only resolves once the true end state holds (evidence: the
  `test_directory_search_filters_and_row_links_to_detail` debounce investigation this session —
  cost roughly half a session of bisection before the actual mechanism was found via a DOM-
  rendered debug log, not console.log, since this environment's test runner does not surface it).
- [TDD · open] A fully-green suite only proves the paths it actually exercises — an independent
  adversarial review (subagent refute-read) found a real, uncovered gap (Rotate/Revoke/assignRole
  mutations had no `onError` handler, silently swallowing a failure — a direct R4 violation) that
  none of the 34 new tests caught, because no test exercised any mutation's FAILURE path on this
  surface (only Create's failure path was covered, inherited for free from the reused
  `CreateKeyDialog.tsx`). Candidate standing checklist item for future test-plans: one
  failure-path test per mutation, not just per screen (evidence: `a885e980fd730c83e`'s review,
  fixed same session, re-verified 17/17 + clean lint/typecheck + clean 944-test regression).
- [ADD · open] Live mutation-probing (temporarily inverting one meaningful line of production
  logic, confirming the relevant test fails, then reverting byte-identically) is a materially
  stronger refute-read technique than a read-only review — it converted "the assertions look
  reasonable" into a demonstrated fact for R6, the budget merge logic, and the members self-guard.
  Worth naming explicitly as a preferred refute-read technique in `advisor.md`/`confidence.md` for
  safety-critical tasks, not just an ad hoc choice this one reviewer happened to make (evidence:
  this task's Refute-read verdict — all 3 probes correctly flipped their target test from green
  to red, then back).

