# TASK: Command Palette

slug: command-palette · created: 2026-07-06 · stage: production
milestone: platform-console-flat-redesign
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/dashboard/components/ui/app-shell.tsx:AppShell` — the global shell every dashboard
    surface inherits (own docblock: "the responsive, accessible application shell every
    dashboard surface inherits"). Currently `{children, activePath, role, userEmail, banner}`.
    Already renders a Radix `Dialog` (mobile nav sheet) AND an already-shipped fail-CLOSED nav
    gate `showPlatformNav(role) => role === "superadmin"` EXACTLY (line ~122, contrasted in its
    own code comment with every other nav item's fail-OPEN default), gating the inline
    `PlatformNavGroup` (Tenants/Plans links). This task adds ONE additive prop
    `commandPalette?: React.ReactNode`, rendered once near `{banner}` (see Issues/Risks #1).
  - `apps/dashboard/components/dashboard-shell.tsx:DashboardShell` — the ONLY caller of
    `AppShell` (mounted once by `apps/dashboard/app/(app)/app/layout.tsx`; persists across
    client-side navigation within that route group — confirmed this is how "open the palette
    from anywhere in the dashboard" is satisfied for free). Already computes `role` via
    `useCurrentUser()` and conditionally threads `<ImpersonationBanner/>` into `banner` — THE
    exact recipe this task mirrors for `commandPalette` (see Honors).
  - `apps/dashboard/lib/hooks/use-current-user.ts:useCurrentUser` —
    `useQuery<CurrentUser>({queryKey:["current-user"], queryFn, retry:false, staleTime:5*60*1000})`;
    `CurrentUser.role: string | null`. Confirmed elsewhere (`app-shell.tsx:122`,
    `PlatformMembersTab.tsx:211`) that the wire value for a platform operator is the literal
    lowercase string `"superadmin"`.
  - `apps/dashboard/components/platform/PlatformTenantDirectory.tsx:PlatformTenantDirectory` —
    the EXISTING tenant-search UI (Screen 1). `useQuery<TenantDirectoryListResponse>({queryKey:
    ["platform-tenants", debouncedQuery, offset], queryFn: () =>
    bffGet(\`/admin/platform/tenants?q=${encodeURIComponent(debouncedQuery)}&limit=${PAGE_LIMIT}&offset=${offset}\`),
    retry:false})`; `PAGE_LIMIT=20`, `SEARCH_DEBOUNCE_MS=300` (plain `setTimeout` debounce).
    `export interface PlatformTenantSummary {id,name,kind,created_at}` IS exported — but the
    wrapper `interface TenantDirectoryListResponse {tenants, total}` is NOT exported (see
    Issues/Risks #6). Local (not exported) `getErrorTitle` helper, duplicated per-file — an
    established convention (also in `PlatformTenantOverviewStrip.tsx`).
  - `apps/gateway/src/gateway/tenants/api/platform_tenants_router.py:list_platform_tenants` —
    `GET /admin/platform/tenants` (FROZEN @ v1, `platform-tenant-directory` task) ->
    `TenantDirectoryListResponse {tenants: TenantSummaryResponse[], total: int}`,
    `TenantSummaryResponse {id,name,kind,created_at}`; params `q: str|None`, `limit` (default 50,
    clamped to `_MAX_LIMIT=200`), `offset` (default 0); gated by `require_superadmin`. This task
    cites this endpoint verbatim — zero backend change (Tier 1, frontend-only, mirrors
    `console-flat-visual-pass`/`tenant-overview-strip`'s own "no new REST endpoint" precedent).
  - `apps/gateway/src/gateway/tenants/domain/authz.py:require_superadmin` —
    `if identity.role != Role.SUPERADMIN: raise AUTH_FORBIDDEN.exc()` -> 401
    `ERR_AUTH_INVALID_TOKEN` (missing/invalid bearer) or 403 `ERR_AUTH_FORBIDDEN` (wrong role).
    THE authoritative, already-existing, unmodified-by-this-task gate protecting the search
    endpoint.
  - `apps/gateway/src/gateway/tenants/domain/authz.py:authorize_tenant_scope` —
    `if identity.role == Role.SUPERADMIN or identity.tenant_id == target_tenant_id: return; raise
    AUTH_FORBIDDEN.exc()` — gates `GET /admin/platform/tenants/{tenant_id}`, the destination
    page's own data fetch (`platform_tenants_router.py:get_platform_tenant_by_id`). Also
    unmodified by this task.
  - `apps/dashboard/components/platform/PlatformTenantDetail.tsx:PlatformTenantDetail` — the
    navigation TARGET; route `/app/platform/tenants/[tenantId]`, prop `{tenantId: string}`. Own
    docblock confirms: "a 404/403 renders a FULL-PAGE ErrorState; nothing else (banner, tabs,
    panels) renders alongside it" — a mis-navigated non-superadmin session hitting this route
    directly is ALREADY safe, independent of anything this task builds.
  - `apps/dashboard/components/ui/dialog.tsx:Dialog,DialogTrigger,DialogContent,DialogTitle,DialogDescription`
    — thin wrapper over `@radix-ui/react-dialog` (`^1.1.16`, `package.json:17`). Radix's
    `Dialog.Content` already provides focus-trap, Escape-to-close, backdrop-click-to-close, and
    focus-return-to-trigger NATIVELY — confirmed this is the SAME primitive `AppShell` itself
    already uses for its own mobile nav sheet.
  - `apps/dashboard/lib/use-focus-trap.ts:useFocusTrap` — confirmed NOT applicable here: its own
    docblock scopes it to "inline `role=\"dialog\"` elements... No portal, no dependency" (the
    Keys/Members/Plan raw confirm-dialogs' pattern) — a DIFFERENT pattern from the Radix-portal
    Dialog this task builds on.
  - `apps/dashboard/components/memory/MemoryLibraryPane.tsx:MemoryLibraryPane` (esp.
    `handleListKeyDown`, lines 226-244) — the ONLY existing "real ARIA listbox" precedent in this
    codebase: `<ul role="listbox" tabIndex={0} onKeyDown>` of `<li role="option"
    aria-selected>`; `ArrowDown`/`ArrowUp` move a selected index (CLAMPED at both ends, never
    wraps), `Enter` confirms the current selection (a no-op until the user has arrow-keyed at
    least once — `currentIdx !== -1` guard). No `aria-activedescendant`, no `role="combobox"`
    anywhere in this codebase (confirmed via grep) — see Issues/Risks #2 and #3.
  - `apps/dashboard/components/ui/states.tsx:Loading,ErrorState,Empty` — reused verbatim for the
    palette's loading/error/no-results states, matching `PlatformTenantDirectory`'s and
    `PlatformTenantOverviewStrip`'s own precedent.
  - `apps/dashboard/components/ui/input.tsx:Input` — plain `React.forwardRef` over a native
    `<input>`; no built-in combobox semantics — the palette's search box is a plain `Input` with
    `aria-*` attributes applied at the call site, same as `PlatformTenantDirectory`'s own box.
  - No `cmdk` or other command-palette library in `package.json`; no `role="combobox"` or
    `aria-activedescendant` anywhere in `apps/dashboard` (confirmed via grep across
    `components`+`tests`); no existing `metaKey`/`ctrlKey` usage anywhere (confirmed via grep —
    every existing `onKeyDown` reacts to a plain, unmodified key: Enter/Escape/Tab/Arrow*) — this
    task is the first modifier-key GLOBAL shortcut in this codebase.
Context (working folder):
  - `.add/milestones/platform-console-flat-redesign/MILESTONE.md` — this task's own Task line +
    Exit criterion + the "Shared / risky contracts" note requiring direct verification of
    `app-shell.tsx`/`PlatformTenantDetail.tsx` touch history (see Ground SHA below).
  - `.add/tasks/tenant-overview-strip/TASK.md` (DONE, gate=PASS) — its own §5 Scope list did NOT
    include `app-shell.tsx` (only `PlatformTenantDetail.tsx` + the 4 tab files); independently
    reconfirmed by direct `git log` (see Ground SHA).
  - `.add/tasks/console-flat-visual-pass/TASK.md` (DONE, gate=PASS) — its own §5 Scope list did
    NOT include `app-shell.tsx` OR `PlatformTenantDetail.tsx` (only the 6 platform screens +
    `stat-card.tsx`); independently reconfirmed by direct `git log`.
  - `.add/tasks/overview-strip-plan-display-name/TASK.md` (DONE, gate=PASS, fast-lane) — the
    fast-lane formatting template, read for reference only per the brief; this task is full-lane.
Honors (patterns / conventions):
  - `dashboard-shell.tsx`'s existing `banner={impersonation.data?.active ? <ImpersonationBanner/>
    : undefined}` recipe — THE precedent this task's own `commandPalette={data?.role ===
    "superadmin" ? <PlatformCommandPalette/> : undefined}` mirrors byte-for-byte in shape: a NEW
    additive `React.ReactNode` prop on `AppShell`, computed from data `DashboardShell` already
    fetches (no new network call), `undefined` (never a component that itself renders null) when
    the gate fails.
  - `app-shell.tsx`'s existing `showPlatformNav(role) => role === "superadmin"` fail-CLOSED
    allowlist (contrasted explicitly, in its own code comment, with every OTHER nav item's
    fail-OPEN default) — the identical reasoning ("a real information-disclosure/trust cost, not
    just a transient UX nicety") applies to gating the command palette's very EXISTENCE, not just
    its data.
  - CONVENTIONS.md's pervasive "reuse before invent" discipline — reusing Radix `Dialog` (already
    in `package.json`, already used by `AppShell` itself) rather than a new headless-combobox
    dependency; reusing `Loading`/`ErrorState`/`Empty` rather than a new display primitive;
    reusing `MemoryLibraryPane`'s listbox/keydown shape rather than inventing a new ARIA pattern.
  - CONVENTIONS.md v13 fold: pure-props-component testability preference — the palette's
    results-list rendering should be a small pure-props sub-piece (mirrors
    `PlatformTenantOverviewStrip`'s own `OverviewTile` precedent).
  - The pervasive `platform/*.tsx` cross-reference discipline ("mirrors X's own shipped Y
    convention exactly") — this task's own file header should name every precedent it mirrors.
Anchors the contract cites:
  - `apps/dashboard/components/ui/app-shell.tsx:AppShell` (gains additive
    `commandPalette?: React.ReactNode` prop)
  - `apps/dashboard/components/dashboard-shell.tsx:DashboardShell` (threads the new prop,
    conditioned on `role === "superadmin"`)
  - NEW `apps/dashboard/components/platform/PlatformCommandPalette.tsx:PlatformCommandPalette`
  - `apps/dashboard/components/platform/PlatformTenantDirectory.tsx:PlatformTenantSummary`
    (imported, already exported — zero edits to this file)
  - `apps/gateway/src/gateway/tenants/api/platform_tenants_router.py:list_platform_tenants`
    (GET /admin/platform/tenants — cited verbatim, zero backend edits)
  - `apps/dashboard/components/ui/dialog.tsx:Dialog,DialogContent,DialogTitle,DialogDescription`
    (reused verbatim)
  - `apps/dashboard/components/ui/states.tsx:Loading,ErrorState,Empty` (reused verbatim)
  - `apps/dashboard/components/ui/input.tsx:Input` (reused verbatim)
Issues/Risks (→ feed §1):
  1. `AppShell`'s new `commandPalette` prop's literal render POSITION inside its JSX is visually
     inconsequential (unlike `banner`, which affects a real in-flow height calc) — because
     `PlatformCommandPalette`'s own output is a Radix `Dialog` (portals to `document.body`,
     invisible until open) plus a `position:fixed` trigger button (escapes normal flow).
     Resolution (proceeding as project lead, low-risk/reversible): render `{commandPalette}`
     immediately after `{banner}`, before the `Dialog` wrapper — zero effect on the existing
     `lg:h-screen`/calc height-class contract the sibling shell test already pins.
  2. This project's own "real ARIA listbox semantics" bar (MILESTONE.md Tasks line) has exactly
     ONE precedent in this codebase (`MemoryLibraryPane`'s `role="listbox"`/`role="option"`/
     `aria-selected` + Arrow/Enter keydown) — NOT the stricter WAI-ARIA APG "editable combobox"
     pattern (`role="combobox"` + `aria-expanded` + `aria-activedescendant` on the input itself),
     which has ZERO precedent anywhere in this codebase. Resolution (proceeding as project lead,
     mirrors the one real precedent, satisfies the literal milestone wording): mirror
     `MemoryLibraryPane`'s listbox shape. A stricter combobox pattern remains a reasonable future
     upgrade, not this task's bar (flagged, see §1 Assumptions).
  3. `MemoryLibraryPane`'s own keyboard precedent requires an explicit `ArrowDown` before `Enter`
     does anything (`currentIdx` starts at `-1`). For a COMMAND PALETTE specifically, the
     near-universal convention (and this milestone's own "search... and navigate directly"
     phrasing) implies typing + immediate Enter should jump to the top match. Resolution
     (proceeding as project lead, a deliberate, named departure from the one sibling precedent):
     auto-highlight the FIRST result the moment results load. Flagged at freeze (see §3).
  4. The milestone's own Task line says "reuses the existing tenant-search **endpoint**"
     (singular emphasis on the backend route) — CONTRASTED with the Overview Strip sibling task,
     whose own MILESTONE.md line said "reusing the tabs' own existing React Query **cache
     keys**." Sharing `PlatformTenantDirectory`'s exact queryKey would require the IDENTICAL
     `limit` too (else two different-limit fetches under one key clobber each other's cache —
     the exact risk class `tenant-overview-strip` exists to manage), but a compact palette
     naturally wants a SMALLER limit than the Directory's paginated `PAGE_LIMIT=20`. Resolution
     (proceeding as project lead, avoids a real clobber risk, matches this task's own narrower
     milestone wording): the palette owns its OWN distinct queryKey and its own smaller limit
     constant, hitting the SAME backend URL pattern but never sharing a cache entry with the
     Directory page. Flagged at freeze (see §3).
  5. No visible, mouse/touch-operable trigger for the palette exists anywhere in the milestone's
     own wording (only "⌘K" is named) — a keyboard-only affordance is unreachable for a
     touch/no-hardware-keyboard session. Resolution (proceeding as project lead, closes a real
     reachability gap, additive/self-contained/low-risk): `PlatformCommandPalette` also renders
     one small always-visible, all-breakpoints trigger button inside its own output — no AppShell
     chrome restructuring needed since it is CSS-`fixed`-positioned, independent of its
     React-tree render position (see Issues/Risks #1). Flagged at freeze — the ONE piece of new
     visible chrome this task adds that Tin has not seen (see §3 lead flag).
  6. `PlatformTenantDirectory.tsx`'s `TenantDirectoryListResponse` wrapper interface is NOT
     exported (only its nested `PlatformTenantSummary` is). Resolution (proceeding as project
     lead, keeps M16/R7's "PlatformTenantDirectory.tsx byte-identical" claim TRUE with zero
     exception, unlike the Overview Strip's own 4-type additive-export call): declare a small
     local 2-field envelope type (`TenantSearchResponse`) inside the NEW
     `PlatformCommandPalette.tsx` file, importing ONLY the already-exported
     `PlatformTenantSummary` for its array element — a much smaller duplication than the Overview
     Strip's 4 full response shapes (only a trivial envelope is duplicated, not the meaningful
     nested type).
Related intent: MILESTONE.md's Scope ("a global Command palette (⌘K, navigate-only, reuses the
  existing tenant-search endpoint, real ARIA combobox/listbox semantics — not the concept mock's
  bare hover-divs)") + its own Task line ("build the global ⌘K command palette (navigate-only,
  reuses the existing tenant-search endpoint, real ARIA listbox semantics)") + its Exit criterion
  ("A superadmin can open a ⌘K command palette from anywhere in the dashboard, search by tenant
  name, and navigate directly to that tenant") + its WCAG-AA Scope bullet naming "real focus
  management for the Command palette specifically (the concept mock's own rows lack ARIA
  selected-state annotation today)." No existing GLOSSARY term for "command palette" (checked
  `.add/GLOSSARY.md` directly — none present); this task introduces one (see §3 Glossary deltas).
Ground SHA: `37e55ee` (2026-07-06; current HEAD, unchanged since `tenant-overview-strip` —
  confirmed via `git rev-parse --short HEAD` against a clean working tree) —
  `git log 006f791..HEAD -- apps/dashboard/components/ui/app-shell.tsx` AND
  `git log 37e55ee..HEAD -- apps/dashboard/components/ui/app-shell.tsx` both return EMPTY,
  directly confirming (per MILESTONE.md's own instruction to verify, not assume) that NEITHER
  `console-flat-visual-pass` NOR `tenant-overview-strip` touched `app-shell.tsx` — its last real
  commit remains `006f791`, which is itself the commit that PRECEDES `console-flat-visual-pass`'s
  own Ground SHA (i.e. no task in this milestone has touched it yet). `PlatformTenantDetail.tsx`
  WAS touched, but only by `tenant-overview-strip` (its own declared, already-verified Scope) —
  cite symbols above, not bare line numbers; any line ref elsewhere is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Global Command Palette — a superadmin-only, keyboard-triggered (⌘K / Ctrl+K) overlay
  reachable from any dashboard route that searches tenants by name (reusing the existing
  `/admin/platform/tenants` search endpoint) and navigates directly to a selected tenant's detail
  page. Navigate-only: no command execution, no action beyond search + select + navigate + close.
Framings weighed: a single self-contained `PlatformCommandPalette` component (own state, own
  Radix Dialog, own debounced query, own distinct cache key), mounted once via a new additive
  `AppShell` prop threaded from `DashboardShell` exactly like the existing `banner` prop (chosen —
  mirrors an exact, already-shipped, already-tested precedent; zero AppShell chrome
  restructuring; the palette's own output is portal/fixed-positioned so its render position in
  the tree carries no visual weight) · sharing `PlatformTenantDirectory`'s exact query key +
  `limit` so the two dedupe fetches for identical search text (rejected — the palette's natural
  compact result count differs from the Directory's `PAGE_LIMIT=20`; a different `limit` under an
  identical key silently clobbers whichever query ran last, the exact cache-incoherence class of
  bug `tenant-overview-strip` was created to prevent; this task's own MILESTONE.md wording says
  "reuses the...endpoint," not "cache keys," unlike that sibling task's explicit wording) · a new
  general-purpose `Command`/combobox primitive in `components/ui/` for future reuse beyond
  tenants (rejected for this task — no second consumer exists yet, and MILESTONE.md's Out list
  already rejects unrelated new-IA scope creep for this milestone) · gating the palette ONLY
  behind the backend's existing `require_superadmin` 403 (mount it unconditionally for every role
  and let a non-superadmin's search calls fail server-side) (rejected — the mere presence of a
  working "search across all tenants" affordance discloses a cross-tenant admin surface to ~100%
  of ordinary customers, the EXACT information-disclosure cost `app-shell.tsx`'s own
  `showPlatformNav` code comment already names as unacceptable for the Platform nav links; the
  destination page is separately safe regardless, but this task's own mount must not, by itself,
  leak the feature's existence).
Must:
<must>
  - M1 (ACCESS CONTROL — the critical boundary): `PlatformCommandPalette` is mounted (rendered
    into the tree AT ALL) if and only if `useCurrentUser().data?.role === "superadmin"` EXACTLY —
    computed once in `DashboardShell` (no new network call; reuses the existing `["current-user"]`
    query) and threaded into a NEW additive `AppShell` prop `commandPalette?: React.ReactNode`,
    `undefined` when the check fails (never a component that itself renders null). This is a
    UI-layer, fail-CLOSED, defense-in-depth layer ON TOP OF — never a replacement for — the
    backend's own unmodified `require_superadmin` (403 `ERR_AUTH_FORBIDDEN`) gate on `GET
    /admin/platform/tenants` and the `authorize_tenant_scope` gate on the destination
    tenant-detail fetch; both stay untouched by this task.
  - M2: `AppShell` (`components/ui/app-shell.tsx`) gains ONE additive prop
    `commandPalette?: React.ReactNode`, rendered once (immediately after the existing `{banner}`
    slot); omitted (every non-superadmin session) renders byte-identical output to today — no
    other line in `AppShell` changes.
  - M3: A global `keydown` listener (attached once, only while `PlatformCommandPalette` is
    mounted — i.e. only for a superadmin session) opens the palette on `(event.metaKey ||
    event.ctrlKey) && event.key.toLowerCase() === "k"`, calling `preventDefault()` so the
    browser's own Cmd/Ctrl+K binding (if any) never fires; pressing the SAME shortcut again while
    the palette is open closes it (toggle).
  - M4: `PlatformCommandPalette` also renders one always-visible (all breakpoints, not
    desktop-only), independently-clickable trigger control (own accessible name mentioning
    "search"/"tenants"/"command palette") that opens the palette — the fallback path for a
    session with no physical keyboard shortcut available (touch/mobile) or a user unaware of the
    shortcut.
  - M5: The palette is built on the existing `Dialog`/`DialogContent` (Radix,
    `components/ui/dialog.tsx`) — inheriting, with zero new code, Radix's native focus-trap,
    Escape-to-close, backdrop-click-to-close, and return-focus-to-the-triggering-element
    behavior; `useFocusTrap` (the inline non-portal pattern) is NOT used here.
  - M6: On open, the search `Input` receives focus automatically (a user can type immediately
    with no extra Tab/click).
  - M7: The search input is a plain `Input` (`components/ui/input.tsx`) with a debounced (300ms,
    matching `PlatformTenantDirectory`'s own `SEARCH_DEBOUNCE_MS`) query; the underlying
    `useQuery` is `enabled` only while the trimmed query is non-empty — no fetch fires on open
    before the user types.
  - M8: The results query is `queryKey: ["platform-tenant-search", debouncedQuery]` ->
    `bffGet<TenantSearchResponse>(\`/admin/platform/tenants?q=${encodeURIComponent(debouncedQuery)}&limit=${PALETTE_RESULT_LIMIT}&offset=0\`)`
    — the SAME backend endpoint `PlatformTenantDirectory` uses, a DELIBERATELY DISTINCT cache key
    + `limit` (`PALETTE_RESULT_LIMIT=8`) from that component's own
    `["platform-tenants", debouncedQuery, offset]` key, so the two never clobber each other's
    cache. `TenantSearchResponse` is a small LOCAL envelope type (`{tenants:
    PlatformTenantSummary[], total: number}`) importing only the already-exported
    `PlatformTenantSummary` — `PlatformTenantDirectory.tsx` itself gains zero edits (see §0
    Issues/Risks #6).
  - M9: Results render as a `role="listbox"` (`aria-label="Tenant search results"`) of
    `role="option"` items (`aria-selected`), mirroring `MemoryLibraryPane`'s own shape:
    `ArrowDown`/`ArrowUp` move a highlighted index (CLAMPED, never wraps, matching that precedent
    exactly), `Enter` navigates to the highlighted result, a mouse click on any result does the
    same. UNLIKE `MemoryLibraryPane`, the FIRST result is auto-highlighted the instant results
    load, so `Enter` with no prior arrow-key press still navigates to the top match.
  - M10: Selecting a result (`Enter` or click) calls
    `router.push(\`/app/platform/tenants/${tenant.id}\`)` (Next.js `useRouter` from
    `next/navigation`, matching `PlatformTenantDetail`'s own import) and closes the palette.
  - M11: Closing the palette by ANY means (Escape, backdrop click, selecting a result, pressing
    the shortcut again) clears its internal search query, so the next open always starts from the
    empty/prompt state. Closing via a NON-selecting path (Escape, backdrop click, or the shortcut
    again) triggers NO navigation and no `router.push` call — only M10's explicit select action
    ever navigates; closing is otherwise a pure no-op on the current route.
  - M12: Zero query typed (or only whitespace) renders a prompt state ("Type to search tenants by
    name" or equivalent) — no fetch, no results list, no loading/error UI.
  - M13: A non-empty query with zero matching tenants renders the shared `Empty` primitive
    (`components/ui/states.tsx`), matching `MemoryLibraryPane`'s own "No results" precedent —
    never a bare blank list.
  - M14: A pending search renders the shared `Loading` primitive; a failed search renders the
    shared `ErrorState` primitive (both `components/ui/states.tsx`, reused verbatim, matching
    `PlatformTenantDirectory`'s own equivalent handling) — no new loading/error component.
  - M15: When the backend reports more matches than are shown (`total > tenants.length`, i.e.
    more than `PALETTE_RESULT_LIMIT`), an informational, non-interactive hint is shown (e.g.
    "Showing 8 of N — refine your search") — the palette never grows its own pagination controls.
  - M16: `PlatformTenantDirectory.tsx`, `PlatformTenantDetail.tsx`'s existing tabs,
    `platform_tenants_router.py`, and every other existing file this task cites are NOT modified
    beyond `app-shell.tsx`'s M2 additive prop and `dashboard-shell.tsx`'s M1 wiring — zero backend
    change, zero change to any existing test.
</must>
Reject:
<reject>
  - the palette executing any action beyond search+navigate (e.g. "create key," "revoke,"
    "impersonate," any mutation) -> "not in scope" (MILESTONE.md's explicit "navigate-only"
    framing)
  - mounting `PlatformCommandPalette` (or attaching its global keydown listener) for any role
    other than an exact `"superadmin"` match -> rejected as an information-disclosure risk —
    matches `app-shell.tsx`'s own fail-CLOSED `showPlatformNav` reasoning; a session whose role is
    still loading/null/anything else never sees or can trigger the palette
  - relying SOLELY on the backend's existing 403 as the only access control (no frontend mount
    gate) -> rejected (see M1) — the existence of the affordance is itself the risk, not just its
    data
  - sharing `PlatformTenantDirectory`'s exact `["platform-tenants", debouncedQuery, offset]` query
    key -> rejected, would silently clobber cache between two different `limit` values (see §0
    Issues/Risks #4)
  - a new general-purpose `components/ui/` combobox/command primitive -> "not in scope" — no
    second consumer exists yet, a reasonable future generalization, not this task
  - any pagination control (Previous/Next, infinite scroll) inside the palette itself -> "not in
    scope" — a fixed top-N result list only, matching "navigate-only"'s narrow framing
  - any change to `platform_tenants_router.py`, `PlatformTenantDirectory.tsx`, or any existing
    tenant-detail tab -> "not in scope" — a change request against the frozen contract if raised
    after freeze
</reject>
After:
<after>
  - a superadmin, from any dashboard route, presses ⌘K (or Ctrl+K, or clicks the palette's own
    trigger control) and sees a focused search input
  - typing a tenant name shows matching tenants (debounced 300ms) as a real ARIA listbox; the top
    match is pre-highlighted
  - pressing Enter (or clicking a result) navigates to that tenant's
    `/app/platform/tenants/{id}` detail page and the palette closes, its query cleared
  - a non-superadmin session never renders the palette, never attaches its keydown listener, and
    never sees any of its chrome — verified by a test rendering the shell with a
    non-superadmin/null/loading role
  - the destination tenant-detail route remains independently protected by its own existing,
    unmodified `authorize_tenant_scope` + full-page-`ErrorState` behavior, regardless of this task
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The new always-visible fallback trigger control (M4) — its exact placement/visual treatment
    has never been shown to or confirmed by Tin (only the ⌘K mechanism is named in MILESTONE.md);
    lowest confidence because it is the one piece of genuinely new, unreviewed visible chrome this
    task adds, purely this task's own inference to close a real touch/no-keyboard reachability
    gap. If wrong: it is fully self-contained inside the new `PlatformCommandPalette.tsx` file (a
    `position:fixed` element, zero AppShell restructuring) — trivially removable, repositionable,
    or restyled without touching any other file.
  - [ ] The palette's own distinct React Query cache key (`["platform-tenant-search", ...]`, not
    sharing `PlatformTenantDirectory`'s `["platform-tenants", ...]`) — confirm or deny Tin is fine
    with two separate network requests when the same query text is searched in both places during
    one session; if wrong, the fix is to match `PALETTE_RESULT_LIMIT` to `PAGE_LIMIT` and adopt
    the identical key+shape — a contained, mechanical change.
  - [ ] Auto-highlighting the first result on load (a deliberate departure from
    `MemoryLibraryPane`'s own "no highlight until the first ArrowDown" precedent) — confirm or
    deny; if wrong, drop the auto-highlight initializer, reverting to `MemoryLibraryPane`'s exact
    behavior (Enter is a no-op until arrow-keyed).
  - [ ] Listbox-only ARIA (not the stricter `role="combobox"` + `aria-activedescendant` pattern)
    — confirm or deny this meets the bar; if wrong, upgrading to a full combobox pattern is a
    larger, separate follow-up (no existing precedent to mirror), not a same-file tweak.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Palette mounts only for an exact superadmin role   # M1
  Given a dashboard session where useCurrentUser's role is exactly "superadmin"
  When DashboardShell renders AppShell
  Then AppShell receives a defined commandPalette prop containing PlatformCommandPalette
  And a session whose role is member, admin, owner, null, or still loading receives commandPalette=undefined, so nothing renders and no keydown listener exists for that session

Scenario: AppShell's new prop is additive and byte-identical when absent   # M2
  Given AppShell renders with no commandPalette prop passed, matching every pre-existing call site
  When AppShell renders
  Then its output is byte-identical to before this task
  And the existing app-shell-sidebar test suite passes unmodified

Scenario: Global shortcut opens and toggles the palette   # M3
  Given a superadmin session on any dashboard route with the palette closed
  When the user presses Cmd+K or Ctrl+K
  Then the palette opens and the browser's own default action for that key combination is prevented
  And pressing the identical shortcut again while it is open closes it

Scenario: Click or tap trigger opens the palette without a keyboard   # M4
  Given a superadmin session on a touch device with no physical keyboard
  When the user activates the palette's own always-visible trigger control
  Then the palette opens exactly as it would via the keyboard shortcut
  And the trigger control is present and operable at every breakpoint, not desktop-only

Scenario: Opening and closing the palette manages focus natively   # M5
  Given the palette is closed and some other button on the page currently has focus
  When the user opens the palette, presses Tab repeatedly, then closes it via Escape
  Then focus is trapped inside the palette's dialog content throughout
  And focus returns to the exact element that had focus before the palette opened

Scenario: Search input is focused automatically on open   # M6
  Given the palette is closed
  When the user opens it by any trigger
  Then the search input has document focus immediately, with no extra click or Tab

Scenario: Typed query is debounced before it fires   # M7
  Given the palette is open with an empty query
  When the user types a tenant name
  Then no fetch fires until 300ms after the last keystroke
  And a still-empty or whitespace-only query never enables the results fetch at all

Scenario: Results query uses its own distinct cache key against the shared endpoint   # M8
  Given the palette's debounced query is "acme"
  When its results query fires
  Then it requests GET /admin/platform/tenants with q=acme, limit=8, offset=0 under queryKey platform-tenant-search plus acme
  And this queryKey is never equal to PlatformTenantDirectory's own platform-tenants key, so neither component's cached data ever overwrites the other's

Scenario: Results render as a real ARIA listbox with clamped arrow navigation   # M9
  Given a search returns three matching tenants
  When the results render
  Then the container carries role listbox and each result carries role option with aria-selected
  And the first result is highlighted immediately, and ArrowDown/ArrowUp move the highlight one item at a time and stop at the first or last item rather than wrapping

Scenario: Enter and click both navigate to the selected tenant   # M10
  Given a search has results and the second result is highlighted
  When the user presses Enter, or alternatively clicks a different result directly
  Then the app navigates to that tenant's platform tenant detail route by id
  And the palette closes

Scenario: Closing by any path resets the query for next time   # M11
  Given the palette is open with a non-empty query and visible results
  When the user closes it via Escape, a backdrop click, or the shortcut again, without selecting any result
  Then the palette's internal query state clears and no navigation occurs at all
  And the next time it opens, it shows the empty prompt state, not the previous search

Scenario: Empty query shows a prompt, not a fetch   # M12
  Given the palette just opened with no text typed
  When it renders
  Then it shows a type-to-search prompt
  And no network request, loading indicator, or results list is present

Scenario: No matches shows the shared Empty state   # M13
  Given the palette's query matches zero tenants
  When the results settle
  Then the shared Empty primitive renders a no-tenants-found message
  And no stray blank listbox is rendered

Scenario: Loading and error states reuse the shared primitives   # M14
  Given the palette's results query is in flight, and separately, given it fails
  When each state renders
  Then the in-flight state shows the shared Loading primitive and the failed state shows the shared ErrorState primitive
  And neither is a newly introduced display component

Scenario: More matches than shown surfaces a refine hint, never pagination   # M15
  Given the backend reports a total greater than the number of tenants returned
  When the results render
  Then a non-interactive showing-N-of-total hint is shown
  And no Previous/Next control or infinite-scroll control exists anywhere in the palette

Scenario: Every existing platform file and route stays untouched   # M16
  Given PlatformTenantDirectory, PlatformTenantDetail's tabs, and the platform tenants router before this task
  When this task's build completes
  Then a diff on each shows zero changed lines
  And every existing test for those files still passes unmodified

Scenario: Reject, no command execution beyond search and navigate   # R1
  Given the palette is open with a tenant result visible
  When the results and their interactions are inspected
  Then no create-key, revoke, impersonate, or any mutating action is reachable from the palette
  And selecting a result only ever navigates

Scenario: Reject, the palette never mounts for a non-exact-superadmin role   # R2
  Given a session whose role is admin, owner, member, null, or still loading
  When DashboardShell renders
  Then AppShell's commandPalette prop is undefined
  And no keydown listener, trigger control, or palette markup exists anywhere in the rendered tree

Scenario: Reject, the backend gate is never the only safeguard   # R3
  Given the frontend mount gate of R2 is bypassed in a test to simulate a stale or misread role
  When the underlying search request is made
  Then the existing require_superadmin dependency still independently rejects a non-superadmin caller
  And this backend behavior is confirmed unmodified by this task, not newly built by it

Scenario: Reject, no shared cache key with the tenant directory   # R4
  Given the palette and PlatformTenantDirectory are both mounted with identical search text
  When both fire their own queries
  Then their queryKeys differ as in M8
  And neither's cached result is overwritten by the other's differing limit

Scenario: Reject, no new general-purpose combobox primitive   # R5
  Given the shared ui components directory before and after this task
  When the directory is inspected
  Then no new command or combobox family file exists there
  And the palette's listbox lives only inside the new PlatformCommandPalette component file

Scenario: Reject, no pagination inside the palette   # R6
  Given more tenants match than the palette's own result limit
  When the results render
  Then no Previous/Next button, page number, or infinite-scroll trigger is present anywhere in the palette
  And only the M15 refine hint communicates the truncation

Scenario: Reject, no existing file beyond the two additive touches is modified   # R7
  Given the platform tenants router, PlatformTenantDirectory, and every tenant-detail tab
  When this task's diff is inspected
  Then the only touched existing files are AppShell (M2) and DashboardShell (M1)
  And every other existing file in the citation list is byte-identical
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new REST endpoint — Tier 1, frontend-only (mirrors console-flat-visual-pass / tenant-overview-
strip's own "no new backend contract" precedent). The frozen shape is (1) one additive AppShell
prop, (2) DashboardShell's wiring of it, and (3) a new component's full prop/state/query/keyboard
contract.

AppShell (components/ui/app-shell.tsx) — additive prop
  commandPalette?: React.ReactNode
  omitted (every session whose role isn't exactly "superadmin") -> byte-identical output to
  before this task; no other AppShellProps field or rendered line changes.
  Render position: immediately after the existing {banner} slot, before the Dialog wrapper — a
  no-visual-footprint insertion (PlatformCommandPalette's own output is a Radix-portaled Dialog
  plus a CSS-fixed trigger button, so its literal position in AppShell's JSX tree has no layout
  effect, unlike {banner} which reserves real in-flow height).

DashboardShell (components/dashboard-shell.tsx) — wiring, mirrors the existing `banner` recipe
  commandPalette={data?.role === "superadmin" ? <PlatformCommandPalette /> : undefined}
  Computed from the SAME useCurrentUser() call already made here — no new network request.

PlatformCommandPalette (NEW — components/platform/PlatformCommandPalette.tsx)
  export function PlatformCommandPalette(): JSX.Element   -- zero props; fully self-contained.
  Only ever mounted when the caller (DashboardShell) has already confirmed role === "superadmin"
  (M1/R2) — this component performs NO internal role re-check and makes NO ["current-user"]
  fetch of its own; its very presence in the tree IS the frontend gate.

  State: open: boolean (default false) · query: string (default "") · debouncedQuery: string
  (300ms setTimeout debounce, matching PlatformTenantDirectory's own SEARCH_DEBOUNCE_MS) ·
  highlightedIndex: number (auto-set to 0 whenever a new non-empty result set loads).

  Local type (PlatformTenantDirectory.tsx stays byte-identical, see §0 Issues/Risks #6):
    interface TenantSearchResponse { tenants: PlatformTenantSummary[]; total: number }
    (PlatformTenantSummary imported from PlatformTenantDirectory.tsx, already exported there)

  Global shortcut (attached via useEffect on document, only while this component is mounted):
    keydown where (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k"
      -> e.preventDefault(); setOpen(o => !o)   (toggle)

  Trigger control (always rendered, all breakpoints, own accessible name e.g.
  aria-label="Search tenants (Command K)"): a DialogTrigger-wrapped <button>, CSS position:fixed
  (exact corner/offset pinned at build — cosmetic only, not load-bearing), opens the palette
  identically to the keyboard shortcut.

  Dialog: built on components/ui/dialog.tsx's Dialog/DialogContent (Radix) — open/onOpenChange
  controlled by this component's own `open` state (not DialogTrigger's own internal state, since
  the keyboard shortcut must also drive it). Radix's native behavior is relied on verbatim for:
  focus trap inside DialogContent, Escape-to-close, backdrop-click-to-close, and focus-return to
  whichever element triggered the open. DialogTitle (visually hideable, e.g. sr-only) +
  DialogDescription are present for the accessible name/description (mirrors AppShell's own
  existing DialogContent usage for the mobile nav sheet).

  On open: the search Input receives focus automatically (native Radix Content default focus, or
  an explicit autoFocus/onOpenAutoFocus — mechanism is build's choice; the observable contract is
  "search Input has document focus immediately on open").

  On close (Escape, backdrop click, selecting a result, or the shortcut again): query and
  debouncedQuery both reset to "" and highlightedIndex resets to 0, so the next open starts fresh.

  Search input: Input (components/ui/input.tsx), type="text", value=query, onChange updates query
  (and restarts the 300ms debounce timer) · aria-label="Search tenants by name" ·
  placeholder="Search tenants…" (exact copy non-binding, mirrors console-flat-visual-pass/
  tenant-overview-strip's own "wording pinned at build" precedent for non-safety-critical copy).

  Results query (enabled only while debouncedQuery.trim().length > 0 — a still-empty/whitespace
  query never fires this query at all):
    queryKey: ["platform-tenant-search", debouncedQuery]
    queryFn: () => bffGet<TenantSearchResponse>(
      `/admin/platform/tenants?q=${encodeURIComponent(debouncedQuery)}&limit=${PALETTE_RESULT_LIMIT}&offset=0`)
    PALETTE_RESULT_LIMIT = 8 (own constant; DELIBERATELY independent of PlatformTenantDirectory's
    own PAGE_LIMIT=20 — see §0 Issues/Risks #4, §1 Framings weighed). retry: false (matches
    PlatformTenantDirectory's own retry policy).
    This is the SAME backend endpoint/auth (GET /admin/platform/tenants, require_superadmin,
    platform_tenants_router.py) PlatformTenantDirectory itself calls — zero backend change.

  Render states (mutually exclusive, keyed off query/debouncedQuery/isLoading/isError/data):
    query.trim() === "" -> a prompt ("Type to search tenants by name" or equivalent; exact copy
      non-binding) — no fetch, no Loading/ErrorState/Empty/listbox rendered
    isLoading (debouncedQuery non-empty, fetch in flight) -> <Loading .../> (states.tsx, verbatim)
    isError -> <ErrorState title={getErrorTitle(error)} /> (states.tsx, verbatim; a local
      getErrorTitle helper, matching the established per-file-duplication convention already used
      by PlatformTenantDirectory.tsx/PlatformTenantOverviewStrip.tsx)
    data loaded, tenants.length === 0 -> <Empty title="No tenants found" .../> (states.tsx,
      verbatim)
    data loaded, tenants.length > 0 -> the listbox (below)

  Listbox (mirrors MemoryLibraryPane's own shape — components/memory/MemoryLibraryPane.tsx — the
  only existing "real ARIA listbox" precedent in this codebase):
    <ul role="listbox" aria-label="Tenant search results">
      one <li role="option" aria-selected={index === highlightedIndex}> per tenant, rendering the
      tenant's name (+ optionally its `kind`, matching PlatformTenantDirectory's own Badge
      treatment — non-binding cosmetic detail)
    Keyboard (bound on the listbox / palette root, matching MemoryLibraryPane's onKeyDown shape):
      ArrowDown -> highlightedIndex = min(highlightedIndex + 1, tenants.length - 1)  (clamps)
      ArrowUp   -> highlightedIndex = max(highlightedIndex - 1, 0)                   (clamps)
      Enter     -> select tenants[highlightedIndex]  (see Select below)
    UNLIKE MemoryLibraryPane: highlightedIndex initializes to 0 (not -1) whenever a new non-empty
    result set loads, so Enter navigates to the top match with no prior ArrowDown required (§1
    Framings weighed, §0 Issues/Risks #3 — flagged at freeze, below).
    Mouse click on any <li role="option"> selects that tenant identically to Enter.
    More-results hint: when data.total > tenants.length, a non-interactive line below the listbox
    reads e.g. "Showing {tenants.length} of {total} — refine your search" (exact copy non-binding)
    — never a Previous/Next control.

  Select (Enter or click on a result):
    router.push(`/app/platform/tenants/${tenant.id}`)   (next/navigation useRouter, matching
    PlatformTenantDetail.tsx's own import) — then close the palette (see On close, above).

Schema: none touched — no DB/table/API changes; platform_tenants_router.py,
  PlatformTenantDirectory.tsx, and every tenant-detail tab file are byte-identical before/after
  this task (M16, R7).
```

Glossary deltas: `Command palette: a superadmin-only, keyboard-triggered (⌘K / Ctrl+K) global
  overlay reachable from any dashboard route that searches tenants by name and navigates directly
  to a selected tenant's detail page; navigate-only — no command execution beyond search + select
  + navigate (added command-palette, platform-console-flat-redesign).`
Status: FROZEN @ v1 — approved by Claude (orchestrator) — AUTO MODE per CLAUDE.md Rule 2, no chat response after 60s; see chat + TASK.md for reasoning
Reported: no — this is a DRAFT awaiting the orchestrator's independent verification of this
  agent's riskiest claims (see the flag below); the orchestrator presents the freeze ask to Tin
  and only `add.py freeze`/Status: FROZEN is recorded after his explicit go-ahead.

Least-sure flag surfaced at freeze: [spec/contract] the new always-visible fallback trigger
control (M4) — the one piece of genuinely new, unreviewed visible chrome this task adds;
MILESTONE.md names only the ⌘K mechanism, and this control's exact placement/visual treatment has
never been shown to or confirmed by Tin. Cost if wrong: fully self-contained inside the new
PlatformCommandPalette.tsx (a CSS position:fixed element) — trivially removable, repositionable,
or restyled without touching AppShell, DashboardShell, or any other file. Second-most-relevant,
not the lead flag: [contract] the palette's deliberately distinct React Query cache key
(["platform-tenant-search", ...], never sharing PlatformTenantDirectory's own
["platform-tenants", ...] key or its PAGE_LIMIT=20) — chosen to avoid a real cache-clobber risk
(§0 Issues/Risks #4), but a genuine Ground-time judgment call neither MILESTONE.md nor DESIGN.md
pre-decided for this specific task (unlike tenant-overview-strip, whose own milestone line named
cache-key reuse explicitly). Cost if wrong: adopt PlatformTenantDirectory's identical limit + key
shape — a small, mechanical, fully contained change. The access-control boundary itself (M1/R2/R3)
is NOT flagged here — it directly mirrors an exact, already-shipped, already-reasoned precedent
(`showPlatformNav`/`banner`-conditional-mount) plus the backend's own unmodified, independently-
verified `require_superadmin`/`authorize_tenant_scope` gates, so confidence there is high.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  - `apps/dashboard/components/ui/app-shell.tsx` (M2: additive `commandPalette` prop)
  - `apps/dashboard/components/dashboard-shell.tsx` (M1: wiring, mirrors the `banner` recipe)
  - `apps/dashboard/components/platform/PlatformCommandPalette.tsx` (NEW)
  - `apps/dashboard/tests/design-system/app-shell-sidebar.test.tsx` (extended: additive-prop
    passthrough + byte-identical-when-absent tests)
  - `apps/dashboard/tests/platform-command-palette.test.tsx` (NEW: the palette's own suite)
  - `apps/dashboard/tests/dashboard-shell.test.tsx` (NEW, only if the role-gating/wiring tests
    (M1/R2) don't fit naturally inside `app-shell-sidebar.test.tsx` — declared here preemptively
    so crossing into build never produces a stale-scope false positive either way)
Pre-filled by the orchestrator (not the build agent) immediately after freeze, BEFORE the
  tests->build crossing — applying the sequencing lesson from `console-flat-visual-pass` and
  `overview-strip-plan-display-name` (§5 Scope filled too late causes a stale scope-snapshot
  false positive at `add.py check`/gate time; re-crossing tests->build to fix it is a known but
  avoidable extra step).
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features>

Persona (optional): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; absent = generic>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full dashboard suite 1078/1078, independently re-run by the orchestrator
      (`npx vitest run`), matching the build agent's own claim exactly (1057 pre-existing + 21 new
      scenario-mapped tests: 18 in `platform-command-palette.test.tsx` + 3 added to
      `app-shell-sidebar.test.tsx` for M1/M2/R2)
- [x] coverage did not decrease — the coverage run's own `thresholds.lines: 80` floor is
      enforced by a non-zero exit on miss (per `vitest.config.ts`); the run exited 0. Exact
      before/after percentages were not extractable this session (no `json-summary`/`lcov.info`
      reporter output reachable through the sandboxed shell — same tooling limitation recorded on
      `tenant-overview-strip`) — recording the objective floor-held signal honestly rather than a
      fabricated percentage.
- [x] no test or contract was altered during build — `git diff` on `app-shell-sidebar.test.tsx`
      shows purely ADDITIVE new `describe` blocks (no existing test body edited); §3 CONTRACT's
      Status line is unchanged since freeze; the only other test file is wholly NEW
      (`platform-command-palette.test.tsx`)
- [x] the green was EARNED, not gamed — refute-read below; independently re-verified by the
      orchestrator reading all 18 dedicated tests plus the 3 sidebar tests in full (not sampled),
      confirming every assertion targets observable DOM/router/cache state, not internals, and
      none is vacuous (e.g. R5 reads the real `components/ui/` directory listing from disk; R3
      forces a real 403 response and asserts no tenant data leaks into the DOM)
- [x] concurrency / timing of the risky operation is safe — the only timing-sensitive logic is
      the 300ms debounce timer (plain `setTimeout`, cleared on every keystroke via the effect's
      cleanup — no leak, no stacked timers) and the deliberately-distinct-cache-key design
      preventing the one real race class (two differently-limited fetches under one key
      clobbering each other) — DIRECTLY tested by `test_reject_no_shared_cache_key_with_the_tenant_directory`,
      a real dual-mount integration test (both `PlatformTenantDirectory` and
      `PlatformCommandPalette` mounted together under one shared `QueryClient`, searching
      identical text, both independently confirmed to fire and cache without clobber). No
      server-side concurrent-write risk — this task is frontend-only, zero backend change.
- [x] no exposed secrets, injection openings, or unexpected dependencies — zero new package.json
      dependency (reuses existing Radix Dialog, `bffGet`, React Query, `lucide-react`); the search
      query is passed through `encodeURIComponent` before reaching the URL (matches
      `PlatformTenantDirectory`'s own established pattern); no new secret or credential handling
      anywhere in the new component
- [x] layering & dependencies follow CONVENTIONS.md — reuses `Dialog`/`Loading`/`ErrorState`/
      `Empty`/`Input` primitives verbatim per the "reuse before invent" discipline named in §0
      Honors; zero new `components/ui/` primitive added (independently confirmed via R5's
      filesystem-read test AND my own directory listing)
- [x] a person reviewed and approved the change — AUTO-GATED under `autonomy: auto`, per the
      engine's own design (verify auto-resolves on complete evidence; a human gate is reserved for
      residue/security escalation). This is the orchestrator's own independent, adversarial
      re-verification substituting for a literal human read — **Tin has not personally reviewed
      this diff yet**; surfaced explicitly at report-back, same honesty standard applied to the
      §3 freeze attribution above (no fabricated human sign-off).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] A superadmin session (`role === "superadmin"` exactly) renders `PlatformCommandPalette` into
      the tree; every other role (member/admin/owner/null/loading) renders `commandPalette=undefined`
      — no palette markup, no global keydown listener, no trigger button anywhere in the DOM for
      those sessions — confirmed by reading `test_palette_mounts_only_for_exact_superadmin_role`
      (M1, static presence/absence across all 5 non-superadmin cases) AND
      `test_reject_palette_never_mounts_for_non_exact_superadmin_role` (R2, the ADVERSARIAL dynamic
      check — actively fires the real Cmd+K keydown for each non-superadmin case and asserts no
      dialog ever appears, proving no listener was ever attached, not just that markup is hidden)
      directly in `app-shell-sidebar.test.tsx`, both in full
- [x] `AppShell` with no `commandPalette` prop passed (every pre-existing call site) renders
      byte-identical output to before this task — confirmed by
      `test_appshell_commandpalette_prop_is_additive_and_absent_by_default` (read directly) AND by
      independently re-running the full `app-shell-sidebar.test.tsx` suite myself (not trusting the
      build agent's self-report)
- [x] Cmd+K / Ctrl+K opens the palette from a superadmin session and toggles it closed on repeat;
      the browser's own default action for that key combination is prevented — confirmed by
      reading `test_global_shortcut_opens_and_toggles_the_palette` directly: asserts a
      `preventDefault` spy was called, asserts open->closed->open->closed across a full toggle
      cycle, AND separately confirms Ctrl+K works case-insensitively (`"k"` vs `"K"`) — a case the
      contract's prose alone didn't spell out but the test covers
- [x] The always-visible fallback trigger control opens the palette identically to the keyboard
      shortcut, at every breakpoint — confirmed by reading
      `test_trigger_control_opens_the_palette_without_a_keyboard` directly: asserts the trigger's
      className carries neither a bare `hidden` nor an `lg:hidden` responsive-hide class, then
      clicks it directly (no keydown simulation) and confirms the dialog opens
- [x] Opening moves focus into the search input automatically; closing (Escape, backdrop click, or
      re-pressing the shortcut) returns focus to whatever triggered the open, and clears the query
      — confirmed by reading `test_focus_is_trapped_and_returns_to_the_triggering_element_on_close`
      (5-tab focus-trap loop + Escape + focus-return, all via Radix's native behavior, zero bespoke
      code) and `test_search_input_is_focused_automatically_on_open`, both in full. The test's own
      choice to open via trigger-click (rather than the global shortcut) to make this assertion
      unambiguous is a deliberate, well-reasoned scoping decision (Radix's `onCloseAutoFocus`
      default targets the registered `DialogTrigger` ref specifically, not "whatever had focus
      before" in the abstract) — independently reconciled by the orchestrator, not a gap
- [x] The results query fires ONLY after a 300ms debounce and ONLY when the trimmed query is
      non-empty; its `queryKey` (`["platform-tenant-search", debouncedQuery]`) is never equal to
      `PlatformTenantDirectory`'s own `["platform-tenants", debouncedQuery, offset]` key — confirmed
      by reading the component source directly against `PlatformTenantDirectory.tsx`'s own key
      byte-for-byte, AND by reading all 3 debounce/cache tests in full, including the real
      dual-mount integration test proving no clobber under a SHARED QueryClient
- [x] Results render as `role="listbox"`/`role="option"`/`aria-selected`, the first result
      pre-highlighted, Arrow keys move the highlight (clamped, no wrap), Enter or click navigates
      via `router.push` to `/app/platform/tenants/{id}` and closes the palette — confirmed by
      reading `test_results_render_as_aria_listbox_with_clamped_arrow_navigation` (clamps verified
      at BOTH ends, 3 ArrowDown presses on a 3-item list, 3 ArrowUp presses back) and
      `test_enter_and_click_both_navigate_to_the_selected_tenant` (both the Enter path AND a
      separate direct-click-on-a-different-result path, asserting the exact `router.push` URL for
      each) in full
- [x] Zero query shows a prompt (no fetch); zero matches shows the shared `Empty` primitive; a
      pending/failed query shows the shared `Loading`/`ErrorState` primitives — no new display
      component introduced anywhere — confirmed by reading the component's imports directly
      (`Loading, ErrorState, Empty` from `components/ui/states.tsx`, verbatim) AND all 4 render-state
      tests in full, including one proving the empty-query state fires literally zero requests even
      after waiting past the debounce window
- [x] `platform_tenants_router.py`, `PlatformTenantDirectory.tsx`, and every existing tenant-detail
      tab file are byte-identical before/after this task (M16/R7) — confirmed by `git status
      --porcelain` on `apps/gateway/` returning EMPTY (zero backend diff at all) and by
      `git diff --stat` on the frontend M16/R7 file list showing diffstat numbers BYTE-IDENTICAL to
      what the orchestrator already independently verified for `console-flat-visual-pass` and
      `tenant-overview-strip` — proving this task's own build added nothing further to any of them
- [x] Full dashboard suite green + eslint clean + `tsc --noEmit` clean on touched files,
      independently re-run by the orchestrator (not trusting the build agent's self-report) — same
      discipline as every prior task this milestone. Suite 1078/1078, lint 0 errors (2 pre-existing
      warnings, unchanged), `tsc --noEmit` 9 errors all confirmed pre-existing in
      `platform-plan-tab.test.tsx` (unrelated to this task) — all three independently re-run, not
      inferred from the build agent's report alone

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `PlatformCommandPalette` is imported and
      rendered by `dashboard-shell.tsx` (confirmed reading the file in full); `commandPalette` prop
      is destructured and rendered by `app-shell.tsx` (confirmed via `git diff`); `TenantSearchResponse`
      and every local helper/state variable inside `PlatformCommandPalette.tsx` is used at least
      once (confirmed reading the 291-line file in full, tracing every declared symbol)
- [x] DEAD-CODE (code) — no new unused or orphaned symbol introduced — confirmed during the same
      full read of `PlatformCommandPalette.tsx`; no leftover scaffolding, no unused import
- [x] SEMANTIC (prose / non-code) — read in full, not skimmed: the ENTIRE 527-line
      `platform-command-palette.test.tsx` (all 18 tests), the R2 adversarial mount-gate test block
      in `app-shell-sidebar.test.tsx` (lines 450-553), the full 291-line `PlatformCommandPalette.tsx`
      component, the full `dashboard-shell.tsx` (61 lines), and `app-shell.tsx`'s diff — confirmed
      the code matches the frozen §3 CONTRACT in every traced detail, including the
      highlight-index-reset-during-render edge case under cache-hit re-search scenarios

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed directly:
      `AppShell` (`components/ui/app-shell.tsx`, diff read), `DashboardShell`
      (`components/dashboard-shell.tsx`, read in full — line 56 has the exact gated expression),
      `PlatformCommandPalette` (NEW, `components/platform/PlatformCommandPalette.tsx`, read in
      full), `PlatformTenantSummary` (still `export interface` at
      `PlatformTenantDirectory.tsx:26`, reconfirmed via grep just before this gate),
      `list_platform_tenants`/`platform_tenants_router.py` (confirmed untouched via
      `git status --porcelain`), `Dialog/DialogContent/DialogTitle/DialogDescription`
      (`components/ui/dialog.tsx`, imported and used exactly as cited),
      `Loading/ErrorState/Empty` (`components/ui/states.tsx`, imported and used exactly as cited),
      `Input` (`components/ui/input.tsx`, imported and used exactly as cited)
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — none moved;
      Ground SHA `37e55ee` is still current HEAD (no other work landed on this branch between
      ground and this gate)

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: build agent (self, adversarial mutation of the access-control gate) + orchestrator
  (independent re-verification, elevated rigor). Adversarially checked: (1) the build agent
  temporarily mutated `dashboard-shell.tsx`'s `commandPalette` wiring to an unconditional
  fail-open expression and confirmed R2 (`test_reject_palette_never_mounts_for_non_exact_superadmin_role`)
  actually FAILS against the mutant, then reverted and re-confirmed GREEN — proving R2 is
  genuinely discriminating, not vacuous. (2) The agent self-reported a near-miss during this
  process: a `diff` check it ran misleadingly reported the file "identical" while it was still
  in the mutated state; caught via direct `Read`/`grep`, reverted, full suite re-run GREEN. (3)
  Given that self-reported near-miss, the orchestrator did NOT take the agent's final report at
  face value — independently read `dashboard-shell.tsx` in full as the FIRST verification action
  (before checking anything else) and confirmed line 56 has the correct fail-closed expression,
  not the mutated one; independently re-ran the full suite/lint/tsc; independently read the R2
  test's actual logic (not just its name) to confirm it truly fires a real keydown and asserts
  no dialog, for every one of the 5 non-superadmin cases.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: self (orchestrator) — elevated rigor given the build agent's own self-reported
  mutation-testing near-miss on this exact security boundary; see Refute-read verdict above.
1. Security: CLEAR — the frontend mount gate (M1) is fail-CLOSED, mirrors an existing shipped
   pattern (`showPlatformNav`) exactly, and is backstopped by the backend's own unmodified
   `require_superadmin`/`authorize_tenant_scope` gates (independently confirmed zero backend diff
   via `git status --porcelain`). R3's test proves the degrade path leaks no tenant data even if
   the frontend gate were bypassed. The one near-miss during the build agent's OWN mutation
   testing was caught and corrected before this gate, and independently re-confirmed by the
   orchestrator reading the current on-disk state directly — not a live finding, a resolved one.
2. Concurrency: CLEAR — see checklist above (debounce timer cleanup, distinct-cache-key design,
   directly tested dual-mount non-clobber integration test); no server-side concurrent-write
   surface, this task is frontend-only.
3. Architecture: CLEAR — reuses existing primitives per CONVENTIONS.md's "reuse before invent";
   zero new `components/ui/` primitive (independently confirmed, not just test-asserted); mirrors
   two already-shipped precedents (`banner` prop recipe, `showPlatformNav` fail-closed gate)
   byte-for-byte in shape.
Verdict: PASS
Residue: none blocking. Non-blocking, already-flagged-at-freeze items carried into §7 Spec
  delta below (the fallback trigger's cosmetic placement; the distinct-cache-key/auto-highlight
  judgment calls) — none contradicted by the build, all confirmed as originally reasoned.
Binding: advisory — sensitivity unset on this task (defaults to project autonomy: auto); Security
  lens was CLEAR, so no HARD-STOP escalation applies.

### GATE RECORD
Reported: yes — this VERIFY section (checklist, Build Expectations, Deep checks, Live-verify,
  Refute-read, Advisor 3-lens) constitutes the gate report; presented to Tin at report-back
  immediately following this record, per report-template.md
Outcome: PASS
Reviewed by: Claude (orchestrator) — AUTO-GATED per `autonomy: auto`; Tin's own review is still
  pending and explicitly flagged above (checklist item 8) and at report-back · date: 2026-07-06

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): M1/R2's access-control mount gate (watch for any future
  AppShell/DashboardShell refactor that could silently drop the `role === "superadmin"` check —
  the R2 test is the durable regression monitor); R4's distinct-cache-key non-clobber (watch if
  `PlatformTenantDirectory`'s own `PAGE_LIMIT` or queryKey shape ever changes, re-confirm the two
  still can't collide); the fallback trigger's placement (M4, unreviewed by Tin — watch for
  direct feedback once seen live).

### Decisions (ADR)
- [AI] mirrored `dashboard-shell.tsx`'s existing `banner` conditional-prop recipe byte-for-byte
  for `commandPalette` — reuses an already-shipped, already-tested pattern rather than inventing
  a new wiring shape (§1 Framings weighed)
- [AI] gave the palette its OWN distinct React Query cache key + smaller result limit
  (`PALETTE_RESULT_LIMIT=8` vs `PlatformTenantDirectory`'s `PAGE_LIMIT=20`) rather than sharing
  the Directory's key — avoids a real cache-clobber class of bug (§0 Issues/Risks #4); flagged at
  freeze as the second-most-relevant judgment call
- [AI] added a new always-visible fallback trigger button (M4) beyond what MILESTONE.md's prose
  named (only "⌘K" was specified) — closes a real touch/no-keyboard reachability gap; flagged at
  freeze as the LEAD (lowest-confidence) judgment call, fully self-contained/reversible if Tin
  wants it changed
- [AI] auto-highlights the first result on load (departs from `MemoryLibraryPane`'s own
  "no highlight until first ArrowDown" precedent) — matches near-universal command-palette
  convention (type + immediate Enter jumps to top match); flagged at freeze
- [AI] built the M5 focus-return test by opening via trigger-click rather than the global
  keyboard shortcut, to make "focus returns to the triggering element" an unambiguous assertion
  against Radix's actual `onCloseAutoFocus` default (which targets the registered `DialogTrigger`
  ref specifically) — independently reconciled by the orchestrator at verify as sound test design,
  not a coverage gap
- [AI] orchestrator re-crossed tests->build only after §5 Scope was pre-filled completely and
  accurately BEFORE dispatching the build agent — proactively applying the stale-scope-snapshot
  lesson from `tenant-overview-strip`/`overview-strip-plan-display-name`; the crossing produced no
  scope_violation warning, confirming the fix

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] confirm the fallback trigger control's (M4) exact placement/visual treatment
  with Tin once seen live — the one piece of genuinely new, unreviewed chrome this task adds
  (evidence: §3 freeze's own lead low-confidence flag; MILESTONE.md's prose names only "⌘K", not
  a visible button)
- [SPEC · open] consider upgrading the listbox to a full `role="combobox"` +
  `aria-activedescendant` pattern if a future accessibility audit calls for it beyond this
  codebase's one existing listbox precedent (evidence: §0 Issues/Risks #2 — no combobox
  precedent exists anywhere in this codebase today, listbox was the pragmatic choice)
- [SPEC · open] `DialogContent` (`components/ui/dialog.tsx`) still has no motion-safe
  entrance/exit transition of its own (only `DialogOverlay` fades) — the command palette inherits
  this pre-existing gap, unchanged by this task (evidence: Tin's own UX question this session
  about animated/progressive-disclosure components; already tracked as a standing `add.py todo`
  from `tenant-overview-strip`, not newly introduced here)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] a `diff`-based self-check inside an adversarial mutation test can itself lie — [folded foundation-version 48]
  the build agent's own mutation-testing `diff` call misreported a still-mutated
  `dashboard-shell.tsx` as "identical" to its pre-mutation backup; only a direct `Read`/`grep` of
  the actual file content caught it (evidence: build agent's own self-report, independently
  reconfirmed by the orchestrator re-reading the file as the FIRST verification action). Lesson:
  when adversarially mutating a file to prove a test catches a bug, verify the revert with a
  content read, not a diff-tool exit code/summary alone — a diff invocation can itself be
  misconfigured or race a write.
- [ADD · folded] pre-filling §5 BUILD Scope completely and accurately BEFORE dispatching the build [folded foundation-version 48]
  agent (rather than letting the agent or a post-hoc pass fill it) fully prevented the
  stale-scope-snapshot false positive that hit both prior tasks this milestone (evidence: the
  `tests`->`build` phase crossing for this task produced zero `scope_violation` warning, the
  first clean crossing all milestone). Promote this to the standard sequencing for every future
  full-lane task, not just a reactive fix.
- [UDD · folded] a security-critical access-control feature benefits from BOTH a static [folded foundation-version 48]
  presence/absence test (M1) AND a separate, actively-adversarial dynamic test (R2, firing the
  real trigger and asserting no effect) — the static check alone would not have caught a
  fail-open bug where only markup was conditionally hidden but a listener was unconditionally
  attached (evidence: R2's own code comment names this exact failure mode; independently
  confirmed sound by the orchestrator reading its implementation).
