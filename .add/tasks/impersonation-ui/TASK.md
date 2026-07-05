# TASK: Impersonation UI

slug: impersonation-ui · created: 2026-07-05 · stage: production
milestone: tenant-impersonation
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
  - `mint_impersonation_session` / `end_impersonation_session`
    (`apps/gateway/src/gateway/tenants/api/platform_impersonation_router.py:106-231,243-300`) — the
    FROZEN @v1 API this UI must call unmodified. Exact shapes confirmed by direct read (not assumed):
    Mint = `POST /admin/platform/tenants/{tenant_id}/users/{user_id}/impersonate`, no body → 201
    `ImpersonationMintResponse{token, expires_in, session_id, target:{user_id,tenant_id,email,role}}`;
    End = `DELETE /admin/platform/impersonation/sessions/{session_id}`, no body → 204 empty. 7 error
    codes across 401/403/404/409 (§3 CONTRACT lists them). No "list my active session" GET exists
    anywhere on the gateway — confirmed by reading the full 301-line router file (exactly 2 endpoints).
  - `ai_proxy_session` cookie + `getTokenFromRequest`/`proxyRequest`/`isControlPlanePath`/
    `buildClearCookieValue` (`apps/dashboard/app/api/gw/[...path]/route.ts:75-84,90-254`) — the SOLE
    existing token-transport mechanism. Confirmed by direct read that the dashboard holds ZERO
    client-visible JWTs today: `lib/auth.ts`'s own header states "getToken / setToken / clearToken /
    isTokenValid are deleted... token transport is now exclusively via httpOnly cookie
    ai_proxy_session" and `lib/bff-client.ts`'s own header states "No Authorization header is ever
    constructed or read client-side. No localStorage read or write." This is the load-bearing fact
    that reframes the milestone's "two-token client-state cost" question — see §1.
  - `GET /me` → `MeResponse{user_id,tenant_id,email,role}` (`apps/gateway/.../tenants/api/router.py:
    67-81`) — confirmed by direct read to carry NO `exp` and NO `impersonation` field. Reused
    verbatim (unmodified), relayed TWICE with two different cookies, to resolve both the target's and
    the actor's identity for this task's own status surface — the reason no gateway change is needed
    for identity resolution (see §3).
  - `apps/dashboard/app/api/auth/me/route.ts` (BFF relay of the above) and `useCurrentUser`
    (`lib/hooks/use-current-user.ts`) + `DashboardShell` (`components/dashboard-shell.tsx:23-31`) —
    confirmed UNCHANGED by this task (deliberate choice, §1). The existing frozen test
    `test_dashboard_shell_marks_route_and_reuses_query`
    (`tests/design-system/app-shell-sidebar.test.tsx:415-435`) asserts
    `useCurrentUser` is called exactly once from `DashboardShell` — my new, SEPARATE hook does not
    touch that call count.
  - `AppShell` / `AppShellProps` (`components/ui/app-shell.tsx:75-90,172-332`) — the frozen v13/v54
    shell contract, confirmed by direct read of `tests/design-system/app-shell-sidebar.test.tsx`: the
    skip-link is asserted to be `querySelectorAll("a,button,[tabindex]")[0]`, there is exactly one
    `navigation` landmark named "Primary", and `test_desktop_rail_full_height_fixed_viewport` asserts
    that the ONE element carrying the `lg:flex-row` class ALSO carries the literal substrings
    `lg:h-screen` and `lg:overflow-hidden` in that same `className`.
  - `PlatformSafetyBanner` (`components/platform/PlatformSafetyBanner.tsx`) — the established
    "persistent banner mounted once, never per-tab" pattern (a new composition from existing tokens,
    no new `components/ui/` primitive for one caller).
  - `PlatformKeysTab` (`components/platform/PlatformKeysTab.tsx:99-163,267-297`) — the established
    ad-hoc `role="dialog"` + `useFocusTrap` confirm-before-destructive-action pattern (Revoke), reused
    for this task's own Mint/End confirm steps.
  - `PlatformMembersTab` (`components/platform/PlatformMembersTab.tsx`) — the established per-row
    `DataTable` action + self-guard-against-own-row pattern (`isSelf`), the natural home for a new
    per-row "Impersonate" action (Mint needs exactly `{tenant_id, user_id}`, both already in scope
    here: `tenantId` prop + the row's own `user.id`).
  - `PlatformTenantDetail` (`components/platform/PlatformTenantDetail.tsx:63-143`) — already fetches
    `tenant.kind` (used today only by `PlatformSafetyBanner`); this task threads that same value down
    to `PlatformMembersTab` as a new prop (one-line, additive).
  - `useFocusTrap` (`lib/use-focus-trap.ts`) — reused verbatim.
  - `resilientFetch` / `BffError` (`lib/resilient-fetch.ts`, re-exported via `lib/bff-client.ts`) —
    the existing timeout/retry/circuit-breaker core; reused for this task's new client-side calls per
    CLAUDE.md's own design-for-failure rule (`useCurrentUser` itself currently uses a bare `fetch`,
    which this task does NOT copy — see §1 Framings).
  - `bffGet`/`bffPost`/`bffPut`/`bffDelete` (`lib/bff-client.ts`) are hardcoded to the `/api/gw` prefix
    (arbitrary-gateway-path proxy) — confirmed by direct read. `useCurrentUser` does NOT use them; it
    calls its OWN dedicated `/api/auth/me` route directly. This task's new Mint/End/Status calls are
    NOT gateway-arbitrary-path calls either, so they follow `useCurrentUser`'s precedent (a small
    dedicated `/api/platform/impersonation*` BFF surface), not `/api/gw`'s.

Context (working folder): `.add/milestones/tenant-impersonation/MILESTONE.md` (Scope/Shared-decisions
  — the "Shared/risky contracts" section explicitly names the two-token client-state question as
  THIS task's own to resolve, not to defer further); `.add/tasks/impersonation-session-lifecycle/
  TASK.md` §7 OBSERVE (the carried-forward Spec-delta re-stating the same question, plus N1's
  deploy-timing note, which is `impersonation-live-session-guard`'s concern, not this task's — noted
  only so this task doesn't silently assume N1 is resolved); `.add/tasks/
  impersonation-live-session-guard/TASK.md` (still a bare template — confirms no overlap yet; that
  task owns per-request LIVE re-validation of `revoked_at`/`expires_at` against the gateway's own
  self-service routes, a server-side concern this task does not touch or depend on).

Honors (patterns / conventions):
  - Reuse-over-invent: zero new gateway surface (§3); Mint/End/Status compose entirely from the
    already-frozen router + the already-frozen `/admin/auth/me`; the confirm-dialog pattern, the
    persistent-banner pattern, and the per-row-action pattern are each reused verbatim from
    `PlatformKeysTab`/`PlatformSafetyBanner`/`PlatformMembersTab`, not reinvented.
  - Additive-only extension of frozen/shared surfaces: `AppShellProps` gains one optional prop,
    `PlatformMembersTab` gains one optional prop, `/api/gw/[...path]`'s existing behavior is
    byte-identical whenever no impersonation cookie exists — mirrors the sibling gateway task's own
    "Identity's 4 fields NOT modified" discipline, applied here to `/api/auth/me`, `useCurrentUser`,
    and `AppShell`'s existing default rendering.
  - "Nav is UX-only, fails open; the gateway remains the source of truth" — `NavItem`'s own documented
    philosophy (`app-shell.tsx:37-48`) — this task deliberately does NOT reshape nav-link visibility to
    the impersonated target's role (§1 ⚠), consistent with treating nav as a rough guide, never the
    real enforcement.

Anchors the contract cites: `mint_impersonation_session`/`end_impersonation_session`
  (`platform_impersonation_router.py`), `ImpersonationMintResponse`/`ImpersonationTargetResponse`,
  `GET /me` (`router.py:67`), `getTokenFromRequest`/`proxyRequest`/`isControlPlanePath`
  (`app/api/gw/[...path]/route.ts`), `AppShellProps` (`app-shell.tsx:75`), `DashboardShell`
  (`dashboard-shell.tsx`), `useCurrentUser` (`use-current-user.ts`), `useFocusTrap`
  (`use-focus-trap.ts`), `resilientFetch` (`resilient-fetch.ts`).

Issues/Risks (→ feed §1):
  - **THE central design question, confirmed by direct code read (not assumed)**: the dashboard
    already holds ZERO client-visible JWTs (`lib/auth.ts`'s own deletion note). This REFRAMES the
    milestone's "two-token client-state cost" question away from "how do we juggle two raw JWTs in
    React state/localStorage" (the naive SPA framing) and towards "how do two SERVER-SIDE httpOnly
    cookies coexist, and which one does the ONE shared proxy route attach to which outbound request."
    Getting the per-request cookie choice wrong is the precise mechanism behind the persona's own
    named failure mode #2 ("leaks the wrong one into the wrong request") — but its SEVERITY is
    asymmetric, confirmed by tracing both directions: (a) impersonation token reaching an
    `/admin/platform/*` call is caught by the gateway's OWN unconditional `require_superadmin` gate
    (an impersonation identity's role is never superadmin, per the sibling task's own frozen,
    security-reviewed proof) — so this direction degrades to a FUNCTIONAL bug (superadmin locked out
    of Platform pages while impersonating), not a privilege-escalation hole; (b) the ORIGINAL
    superadmin token reaching an ordinary `/v1/*` or non-platform `/admin/*` call while impersonating
    is NOT a security hole either (it would just run as the superadmin's own platform-tenant identity)
    but IS a data-integrity/billing-attribution bug (usage/spend recorded against the wrong tenant) —
    exactly the cost this task's own design must close, precisely and deliberately, not by hand-wave.
  - **A second, more serious gap found only by working through the exact request/response shapes (not
    just the prose)**: `MeResponse` carries no `session_id` at all (confirmed: `router.py:67-81`'s
    `me()` handler reads only `identity.user_id/tenant_id/email/role`, never `identity.impersonation`
    even though `JwtTokenService.decode()` already reconstructs it). Relaying the impersonation cookie
    to `GET /me` therefore CANNOT recover `session_id` — meaning naively, a page refresh mid-session
    would leave the banner with no way to call End at all (not merely a missing countdown, an
    UNREACHABLE End). Fixed in §3 without any gateway change: the impersonation cookie's VALUE is a
    small JSON envelope `{token, session_id, expires_at}` — not a bare JWT — written once at Mint from
    data Mint's OWN response already contains, read back by both the Status route (recovers
    `session_id` AND `expires_at`, refresh or not — this ALSO fully closes what would otherwise have
    been a countdown-survives-refresh gap, for free) and the `/api/gw` proxy (extracts `.token` before
    using it as the Bearer value). Must NOT be the naive "decode the JWT payload without verifying it"
    shortcut this codebase already fixed once (`GET /api/auth/me`'s own v18 lesson) — the envelope is
    the BFF's OWN plaintext value, never a JWT claim read without verification.
  - `AppShell`'s fixed-viewport desktop math (`lg:h-screen` + `lg:overflow-hidden` on the SAME element
    the frozen test inspects) means a new persistent banner ABOVE the shell cannot simply be added as
    an outer sibling without either double-counting viewport height (a real, visible layout bug: the
    row would overflow by the banner's own height) or being restructured in a way that risks the
    frozen `toContain("lg:h-screen")` assertion. Resolved in §3 by making the row's own height class
    CONDITIONAL (identical literal string when no banner is passed; a calc-based reservation only when
    one is) rather than restructuring the tree — verified achievable without touching the frozen
    test, but flagged here because it is the one piece of this contract most likely to be subtly
    gotten wrong at Build (see Known-problem fixes, §5).
  - `queryClient` cache leakage across the impersonation boundary: every existing page's React Query
    cache is keyed WITHOUT any actor/target distinction (e.g. `["usage", ...]`). Neither Mint nor End
    changing the underlying identity is itself reflected in any existing query key — left unhandled,
    a page visited before Mint (or before End) would keep showing the OTHER identity's stale cached
    data after the boundary is crossed. This is the persona's failure mode #2 again, at the
    client-cache layer rather than the cookie layer.

Related intent: this session's own standing UI/UX bar (user-facing features need genuinely designed,
  polished UI/UX, not bare CRUD); `.add/milestones/tenant-impersonation/MILESTONE.md`'s exit criterion
  "A superadmin can Mint and End a session from the dashboard, without calling the API directly" and
  its own "Shared/risky contracts" line naming this task as the owner of the two-token question;
  GLOSSARY term "impersonation session" (already defined by the sibling task, reused not redefined).

Ground SHA: cfbb464

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Impersonation dashboard surface — Mint/End entry points, an unmissable persistent banner,
and a two-httpOnly-cookie BFF architecture that lets the rest of the app "act as" the target for free.

Framings weighed:
Two-cookie BFF architecture — a NEW, additive `ai_proxy_impersonation_session` httpOnly cookie
coexists with the UNTOUCHED `ai_proxy_session`; the ONE shared gateway-proxy route
(`/api/gw/[...path]`) becomes path-aware about which cookie to attach per request (chosen — see §0:
zero raw JWT ever reaches client JS, matching this codebase's own v2 BFF hardening exactly; EVERY
existing self-service page — Chat/Usage/Keys/Teams/etc., all ~19 `NAV_ITEMS` — automatically reflects
the impersonated target with ZERO page-level code change, because they all already funnel through
this ONE proxy route) · vs a client-visible token swap (React state/localStorage holds whichever JWT
is "active," client code picks which to attach) — rejected: reintroduces exactly the client-visible-
JWT trust boundary `lib/auth.ts` deliberately eliminated, and would require every consuming page's own
fetch code to become impersonation-aware instead of zero of them · vs overwriting the SAME
`ai_proxy_session` cookie at Mint and restoring it at End — rejected: makes "the original token" a
mutable, ephemeral thing the BFF must remember to restore (fragile across a crash/multi-tab session),
strictly worse than two independent, additive cookies that are each simply present-or-absent.

Nav/role display stays anchored to the REAL superadmin throughout an active session — `useCurrentUser`
and `/api/auth/me` are NOT touched by this task, so `AppShell`'s nav keeps showing the superadmin's
own full nav (including the Platform group) while impersonating (chosen — zero risk to the 10+
existing consumers of `useCurrentUser` across this codebase, and zero risk of breaking
`PlatformMembersTab`'s own self-guard if a superadmin directly navigates to a Platform page mid-session;
the PERSISTENT BANNER, not the nav, is the authoritative "you are impersonating" signal) · vs making
`/api/auth/me` prefer the impersonation cookie so nav genuinely reshapes to the target's own
role-appropriate view (rejected — more "immersive," but breaks the Members-tab self-guard edge case
and touches a widely-consumed hook whose full consumer set this task has not audited; flagged as a
reversible, bounded follow-up if Tin wants the fuller immersion, not closed off by this design).

Must:
<must>
  - M1: TWO httpOnly cookies coexist for the duration of a session: `ai_proxy_session` (existing,
    NEVER read/written by Mint/End as anything other than the "original" identity source) and a NEW
    `ai_proxy_impersonation_session` (HttpOnly, `Secure` in prod, `SameSite=Strict`, `Path=/`,
    `Max-Age=<expires_in>` sourced directly from Mint's own response field — never a hardcoded value).
    The impersonation cookie's VALUE is `encodeURIComponent(JSON.stringify({token, session_id,
    expires_at}))` — a small envelope, not a bare JWT (`expires_at` = `Date.now() + expires_in*1000`,
    computed once at Mint from the gateway's own authoritative `expires_in`) — parsed with a
    `try/catch` everywhere it is read (a malformed/corrupted value degrades to "no valid session,"
    self-healing, never an unhandled exception). This is what lets `session_id` and the countdown
    survive a page refresh with ZERO gateway change (§0). Neither cookie's raw value, nor the token
    inside the envelope, is ever included in a JSON response body (mirrors `/api/auth/login`'s own
    "never exposes the JWT in the response body" rule exactly).
  - M2: NEW `POST /api/platform/impersonation` (`apps/dashboard/app/api/platform/impersonation/
    route.ts`) — body `{tenant_id, user_id}`. Reads `ai_proxy_session` ONLY; absent → 401
    `ERR_AUTH_NO_SESSION` without calling upstream. Present → relays as `Authorization: Bearer` to the
    gateway's `mint_impersonation_session`. On upstream 201: builds the M1 envelope from the upstream's
    OWN `{token, session_id, expires_in}` fields, sets it as the impersonation cookie (Max-Age =
    `expires_in`), and returns `{session_id, expires_in, target}` in the JSON body — the token itself
    is inside the `Set-Cookie` envelope only, never the JSON body. On any upstream 4xx: relays status +
    `{code}` verbatim, no cookie set.
  - M3: NEW `DELETE /api/platform/impersonation/sessions/{sessionId}` (`apps/dashboard/app/api/
    platform/impersonation/sessions/[sessionId]/route.ts`) — reads `ai_proxy_session` ONLY, even when
    the impersonation cookie is ALSO present on the same request (this is the client-side mirror of
    the sibling task's own frozen, security-reviewed "End requires the ORIGINAL JWT, never the active
    impersonation token" contract — non-negotiable, see Safety rule §5). Absent original cookie → 401
    `ERR_AUTH_NO_SESSION` without calling upstream. On upstream 204: clears the impersonation cookie
    (`Max-Age=0`, mirrors `buildClearCookieValue`'s existing exact pattern) and returns 204. On any
    upstream 4xx: relays verbatim, impersonation cookie is left UNTOUCHED (an already-ended/expired
    session on the server should not have its client cookie silently cleared behind a 409, so a retry
    of End against the SAME stale cookie stays diagnosable).
  - M4: NEW `GET /api/platform/impersonation` (status check) — no impersonation cookie present →
    `{active: false}`. Present → parse the M1 envelope (malformed → treat as absent: clear the cookie,
    return `{active:false}`); relay its `.token` to `GET /me` for the VERIFIED target identity, and
    separately relay `ai_proxy_session` (if present) to the SAME endpoint for the VERIFIED actor
    identity. `session_id` and `expires_at` come from the envelope directly (not from `/me`, which
    carries neither) — this is what makes both available on a fresh page load, not only within the
    minting tab's own memory. Target-relay 401 (the impersonation token itself is invalid/rejected) →
    self-heal: clear the impersonation cookie and return `{active: false}` (mirrors the existing
    gw-proxy's own "control-plane 401 clears the cookie" convention). Actor-relay failure alone →
    degrade to `{active: true, session_id, expires_at, target, actor: null}` (partial info beats a hard
    failure — target identity, countdown, and End control all remain fully correct without the actor's
    own email). Response shape: `{active:false} | {active:true, session_id, expires_at,
    target:{user_id,tenant_id,email,role}, actor:{email,role}|null}`.
  - M5: `apps/dashboard/app/api/gw/[...path]/route.ts`'s token resolution becomes PATH-AWARE (replaces
    the current single `getTokenFromRequest` call, every other existing behavior — streaming, timeouts,
    playground-token exchange, body-size guards, hop-by-hop headers — stays UNCHANGED): a path of
    `admin/platform` or starting with `admin/platform/` ALWAYS resolves to `ai_proxy_session` (the raw
    cookie value — unchanged shape), regardless of whether the impersonation cookie is also present.
    Every other path (ordinary `admin/*` and all `v1/*`) prefers the impersonation cookie when present
    — parsing the M1 envelope and using its `.token` field as the Bearer value (a malformed envelope is
    treated as absent, falling through to `ai_proxy_session`) — else falls back to `ai_proxy_session` —
    byte-identical to today's behavior when no impersonation cookie exists. The existing
    control-plane-401-clears-cookie logic becomes SOURCE-aware: it clears whichever cookie actually
    supplied the rejected upstream token, never unconditionally `ai_proxy_session`.
  - M6: `AppShellProps` (`components/ui/app-shell.tsx`) gains ONE new optional prop:
    `banner?: React.ReactNode`, rendered between the existing skip-link and the existing
    `lg:flex-row` row (after the skip-link in DOM order, so the skip-link stays the first focusable
    element — the frozen contract). The row's own height utility class is CONDITIONAL: the literal
    string `lg:h-screen` when `banner` is absent (byte-identical default — every existing call site
    that never passes `banner` is unaffected), a calc-based reservation (e.g.
    `lg:h-[calc(100vh-2.75rem)]`) only when `banner` is present, so the banner and the fixed-viewport
    row together always total exactly 100vh — no overlap, no extra page-level scroll.
  - M7: `DashboardShell` (`components/dashboard-shell.tsx`) additionally calls the new
    `useImpersonationStatus()` hook and passes `<ImpersonationBanner />` as `AppShell`'s new `banner`
    prop when active. `useCurrentUser()`'s own call, and the `role`/`userEmail` props passed to
    `AppShell`, are UNCHANGED (still always the real superadmin's own identity, per §1 Framings) —
    `DashboardShell` calls `useCurrentUser` exactly once, exactly as the existing frozen test asserts.
  - M8: NEW `ImpersonationBanner` (`components/platform/ImpersonationBanner.tsx`) — self-contained
    (reads `useImpersonationStatus()` itself, no props), renders nothing when inactive. When active:
    shows the target's email + role, the actor's email when resolved (omitted, not fabricated, when
    `actor` is null), a live countdown derived from `expires_at` (present on EVERY active response,
    fresh load or not, since `expires_at` lives in the M1 cookie envelope, not in tab-local memory —
    the countdown display itself is a pure `Date.now()` diff computed on each tick, never re-fetched),
    and an "End impersonation" control behind a confirm dialog mirroring `PlatformKeysTab`'s own
    `role="dialog"` + `useFocusTrap` pattern exactly.
  - M9: `PlatformMembersTab` gains a new OPTIONAL prop `tenantKind?: string` (threaded from
    `PlatformTenantDetail`'s already-fetched `tenant.kind` — a one-line additive pass-through there)
    and a new "Impersonate" per-row action. The action is NOT RENDERED (proactive prevention, not a
    post-click error) for: the caller's own row, any row whose `role === "superadmin"`, or any row
    when `tenantKind !== "customer"` (absent/unrecognized `tenantKind` fails CLOSED — the action stays
    hidden — so the existing `tests/platform-members.test.tsx`, which never passes this new prop,
    needs zero changes). When rendered, the action is DISABLED with a short inline explanation while
    `useImpersonationStatus()` reports `active: true` (proactively prevents R7 rather than surfacing it
    only after a click). Clicking triggers a confirm dialog (same pattern as M8); confirming calls the
    Mint mutation with this row's `tenant_id`/`user_id`.
  - M10: BOTH the Mint mutation's `onSuccess` and the End mutation's `onSuccess` call
    `queryClient.clear()` (the full TanStack Query in-memory cache, not a hand-maintained key
    allowlist) — the chosen, deliberately blunt instrument against the client-cache-leakage risk named
    in §0, on the reasoning that a missed key in a manual allowlist is a silent leak, while a full
    clear is a visible, bounded loading flash that is never wrong.
  - M11: Zero gateway-side files change (`platform_impersonation_router.py`, `jwt_service.py`,
    `authz.py`, `entities.py`, `router.py`'s `/me` handler, or any migration). The M1 cookie-envelope
    design is what makes this possible while still recovering `session_id` and `expires_at` across a
    refresh — a genuinely zero-gateway-footprint solution, not a workaround that quietly patches the
    gateway instead.
  - M12: Every new client-side call to the 3 new BFF routes uses `resilientFetch` (the same
    timeout/retry/circuit-breaker core `bff-client.ts` already uses), per this project's own
    design-for-failure rule — not a bare `fetch` (which is what `useCurrentUser` itself currently uses;
    this task does not copy that shortcut for its OWN new code). `useImpersonationStatus()` polls with
    `retry:false` at the React Query layer and a bounded `refetchInterval` (~5s) — no retry storm
    stacked on top of `resilientFetch`'s own already-bounded retries.
</must>

Reject:
<reject>
  - R1: Mint, End, or Status called with no `ai_proxy_session` cookie -> "ERR_AUTH_NO_SESSION" (401),
    never calls upstream (mirrors `/api/gw`'s own existing convention for the identical condition)
  - R2: Mint or End relay the gateway's own 4xx verbatim, zero BFF-invented codes -> one of
    "ERR_AUTH_TOKEN_INVALID" (401) / "ERR_AUTH_FORBIDDEN" (403) / "ERR_TENANT_NOT_FOUND" (404) /
    "ERR_USER_NOT_FOUND" (404) / "ERR_IMPERSONATION_TARGET_INVALID" (403) /
    "ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE" (409) / "ERR_IMPERSONATION_SESSION_NOT_FOUND" (404) /
    "ERR_IMPERSONATION_SESSION_ALREADY_ENDED" (409)
  - R3: a row whose `role === "superadmin"`, the caller's own row, or any row when
    `tenantKind !== "customer"` -> the Impersonate action is not rendered at all (no error code — the
    control simply does not exist for that row)
  - R4: an already-active session (per `useImpersonationStatus()`) -> every row's Impersonate action
    renders DISABLED with inline explanatory text (no error code — proactive prevention of R2's 409)
  - R5: a `/api/gw` proxy call to any `admin/platform` (or `admin/platform/...`) path while the
    impersonation cookie is ALSO present -> `ai_proxy_session`'s token is used, unconditionally, never
    the impersonation one
  - R6: a control-plane 401 upstream -> only the cookie that actually supplied the rejected token is
    cleared; the other cookie and its own identity are completely unaffected
</reject>

After:
<after>
  - A superadmin can start and end an impersonation session entirely from the dashboard (Members tab
    row action → confirm → persistent banner → End), never calling the gateway API directly.
  - Every existing self-service page (all ~19 `NAV_ITEMS`, zero of them touched by this task)
    automatically reflects the impersonated target's own data for the session's duration, because the
    ONE shared proxy route resolves the correct cookie per request.
  - The Platform admin surface (`admin/platform/*`) keeps resolving to the REAL superadmin's own
    identity at all times, even if navigated to directly mid-session.
  - No raw JWT is ever present in a response body, in `localStorage`, or in any client-readable
    location — both cookies are HttpOnly, exactly matching this codebase's existing token-transport
    invariant.
  - Ending (explicitly, or letting `Max-Age` lapse) leaves no stale, wrong-identity data visible on
    any page a superadmin subsequently revisits.
  - Zero lines change in the gateway; the sibling `impersonation-session-lifecycle` contract is
    consumed exactly as frozen.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The impersonation-cookie envelope (`{token, session_id, expires_at}`, §3 Part A/M1) is a NEW
    parsing dependency shared by 3 call sites (Mint writes it, Status reads it, the `/api/gw` proxy
    reads it) — lowest confidence because a subtle encoding slip (e.g. forgetting the
    `encodeURIComponent`/`decodeURIComponent` round-trip, or a `JSON.parse` call missing its
    `try/catch`) would silently break impersonation ENTIRELY the moment it shipped, across all 3 sites
    at once, rather than one contained bug — a broader single-bug blast radius than a bare-token cookie
    would have had. The mitigating fact: every failure mode degrades CLOSED (treated as "no session,"
    never a wrong-identity leak or an unhandled exception) — confirmed by design, not yet by a running
    build. If wrong (a real encoding bug ships), the fix is confined to the 1-2 shared helper functions
    that build/parse the envelope, not a contract-shape change.
  ⚠ `queryClient.clear()` (blanket) rather than a precise identity-scoped-key allowlist on Mint/End
    success — chosen for safety (never leak stale data across the boundary) at the cost of a brief,
    repo-wide loading flash immediately after Mint/End, including for identity-INDEPENDENT data (e.g.
    theme). If this feels heavier than warranted, a later refinement could scope it to a naming
    convention instead — the more easily reversible of the two ⚠s here.
  - [x] Nav/role display stays anchored to the real superadmin throughout (not the impersonated
    target) — confirmed as the lower-risk choice: zero touch to `useCurrentUser`'s 10+ existing
    consumers, and the Members-tab self-guard edge case (direct URL nav to a Platform page
    mid-session) stays correct by construction rather than by coincidence.
  - [x] The Impersonate action lives on the Members tab (not a new page) — confirmed as the
    lowest-friction, most-reused-pattern placement: Mint needs exactly `{tenant_id, user_id}`, both
    already in scope at that exact row, and the tab already has an established per-row-action +
    self-guard convention to extend rather than invent.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Superadmin mints an impersonation session from the Members tab   # M2, M9
  Given a superadmin viewing a customer tenant's Members tab, and an eligible (non-superadmin) row for user U
  When they click Impersonate, confirm the dialog, and the gateway accepts the mint
  Then POST /api/platform/impersonation is called with {tenant_id, user_id: U}
  And the response sets an ai_proxy_impersonation_session cookie and returns {session_id, expires_in, target} with no token in the JSON body

Scenario: The original session cookie is never touched by a successful mint   # M1
  Given a superadmin with an existing ai_proxy_session cookie
  When they successfully mint an impersonation session
  Then the response's Set-Cookie header names ONLY ai_proxy_impersonation_session
  And no Set-Cookie for ai_proxy_session appears anywhere in the response

Scenario: The persistent banner appears immediately after a successful mint   # M7, M8
  Given a successful mint response {session_id, target: {email, role}, expires_in}
  When the impersonation-status query is seeded/refetched
  Then ImpersonationBanner renders with the target's email and role, a live countdown derived from expires_in, and an End control

Scenario: An ordinary self-service path resolves the impersonation cookie, unmodified by this task's code   # M5
  Given both ai_proxy_session and ai_proxy_impersonation_session are present on a request
  When /api/gw/admin/usage (or any v1/* path) is proxied
  Then the upstream Authorization header carries the impersonation cookie's token, not the original

Scenario: A direct request to the Platform admin surface always uses the real superadmin's identity   # M5, R5
  Given both cookies are present
  When /api/gw/admin/platform/tenants/{id} is proxied
  Then the upstream Authorization header carries ai_proxy_session's token, unconditionally
  And this holds regardless of whether the impersonation cookie is present or absent

Scenario: Superadmin ends the session from the banner   # M3, M10
  Given an active session with session_id = SID
  When they click End, confirm the dialog, and the gateway accepts the end
  Then DELETE /api/platform/impersonation/sessions/SID is called and returns 204
  And the impersonation cookie is cleared, and queryClient.clear() has been called

Scenario: End always relays the ORIGINAL cookie, never the impersonation one, even when both are present   # M3 (frozen sibling-contract mirror)
  Given a request to end a session carries BOTH ai_proxy_session and ai_proxy_impersonation_session
  When the End route handler runs
  Then the upstream Authorization header is built from ai_proxy_session's value
  And this holds unconditionally — there is no code path that falls back to the impersonation cookie

Scenario: Mint is proactively blocked while a session is already active   # M9, R4
  Given useImpersonationStatus() reports active: true
  When the Members tab renders any eligible row
  Then that row's Impersonate button is present but disabled, with inline explanatory text
  And no mint request is made without an explicit override action

Scenario: A same-superadmin second mint attempt still surfaces a clean error if it reaches the server   # R2 (409 defense-in-depth)
  Given the disabled-button guard is somehow bypassed (e.g. a stale render) and a second mint fires
  When the gateway rejects it with 409 ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE
  Then the confirm dialog shows that error inline
  And the existing active session is completely unaffected

Scenario: The Impersonate action is hidden for a superadmin-role row   # R3
  Given a Members tab row whose role is "superadmin"
  When the row renders
  Then no Impersonate button/action exists for that row
  And the assign-role control for that row is unaffected (unchanged from today)

Scenario: The Impersonate action is hidden entirely on the platform tenant's own Members tab   # R3
  Given tenantKind === "platform"
  When the Members tab renders any row
  Then no Impersonate button/action exists for any row on this tab
  And the tab's existing list/assign-role functionality is otherwise unaffected

Scenario: A stale or invalid impersonation cookie self-heals on the next status check   # M4
  Given an impersonation cookie present but rejected (401) by GET /me
  When the status route runs
  Then the response is {active: false}
  And the impersonation cookie is cleared in that same response

Scenario: Status check degrades gracefully when only the actor-relay fails   # M4 (edge case)
  Given a valid impersonation cookie but a missing/failed ai_proxy_session relay
  When the status route runs
  Then the response is {active: true, session_id, target, actor: null}
  And the banner still renders target/End correctly, omitting only the "signed in as" clause

Scenario: A control-plane 401 while impersonating clears only the impersonation cookie   # R6
  Given the impersonation cookie supplied the token for a rejected admin/* (non-platform) call
  When the proxy handles the upstream 401
  Then only ai_proxy_impersonation_session is cleared
  And ai_proxy_session and the real login session remain completely intact

Scenario: Ending a session clears cached data so a revisited page never shows the other identity's data   # M10
  Given a page was viewed during an active impersonation session (its query cache holds the target's data)
  When the session ends and that same page is revisited
  Then the page's data is refetched fresh under the (now-restored) real superadmin identity
  And no stale, target-attributed value from before End is ever rendered

Scenario: No session cookie at all short-circuits every impersonation BFF route   # R1
  Given a request to Mint, End, or Status carries no ai_proxy_session cookie
  When the route handler runs
  Then the response is 401 ERR_AUTH_NO_SESSION
  And no fetch to the gateway is made

Scenario: The global banner and the per-tenant safety banner coexist without collision   # boundary case
  Given an active impersonation session AND a direct navigation to some Platform tenant-detail page
  When the page renders
  Then both ImpersonationBanner (shell-level) and PlatformSafetyBanner (page-level) are visible, structurally distinct, non-duplicated
  And each is driven by its own independent data source (impersonation-status vs. the tenant-detail query)

Scenario: AppShell's default rendering (no banner passed) stays byte-identical to before this task   # M6 (regression guard)
  Given AppShell is rendered without a banner prop (every existing call site)
  When the frozen v13/v54 shell-contract assertions run
  Then the row element carrying the lg:flex-row class still literally contains "lg:h-screen" and "lg:overflow-hidden"
  And the skip-link remains the first focusable element

Scenario: The countdown survives a fresh load with no client-side mint memory   # M1, M4, M8
  Given a fresh QueryClient (simulating a hard refresh) and a status response {active:true, session_id, expires_at, target, actor} — expires_at sourced from the cookie envelope, not from any client memory
  When ImpersonationBanner renders
  Then it shows target/actor/End correctly
  And it displays a live countdown derived from expires_at, identical in kind to the same-tab-mint case

Scenario: A malformed impersonation-cookie envelope degrades to "no session," never a crash or a leak   # M1 (edge case)
  Given the impersonation cookie's value is not valid JSON (corrupted or truncated)
  When the Status route or the /api/gw proxy reads it
  Then the outcome is treated as "no impersonation cookie present" (Status -> {active:false}; proxy -> falls back to ai_proxy_session or 401 ERR_AUTH_NO_SESSION if that is also absent)
  And no exception propagates and no other identity's data is exposed
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
═══════════════════════════════════════════════════════════════════════════════
PART A — new BFF routes (apps/dashboard/app/api/platform/impersonation/...)
═══════════════════════════════════════════════════════════════════════════════

POST /api/platform/impersonation   body: { tenant_id: string, user_id: string }
  201 -> { session_id: string, expires_in: number,
           target: { user_id: string, tenant_id: string, email: string, role: string } }
         + Set-Cookie: ai_proxy_impersonation_session=<uri-encoded {token,session_id,expires_at}
                        envelope — see M1, NOT a bare jwt>; HttpOnly[; Secure]; SameSite=Strict;
                        Path=/; Max-Age=<expires_in>
  401 -> { code: "ERR_AUTH_NO_SESSION" }                                    (no ai_proxy_session; no upstream call)
  401 -> { code: "ERR_AUTH_TOKEN_INVALID" }                                 (relayed)
  403 -> { code: "ERR_AUTH_FORBIDDEN" | "ERR_IMPERSONATION_TARGET_INVALID" } (relayed)
  404 -> { code: "ERR_TENANT_NOT_FOUND" | "ERR_USER_NOT_FOUND" }            (relayed)
  409 -> { code: "ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE" }               (relayed)

GET /api/platform/impersonation   (no body)
  200 -> { active: false }
       | { active: true, session_id: string, expires_at: number,
           target: { user_id, tenant_id, email, role },
           actor: { email: string, role: string } | null }
  (session_id/expires_at come from the M1 cookie envelope, not from GET /me — available refresh or
   not. never non-200 — an unreadable/rejected impersonation cookie self-heals to {active:false} + a
   Set-Cookie clearing it, not an error response)

DELETE /api/platform/impersonation/sessions/{sessionId}   (no body)
  204 -> (empty) + Set-Cookie: ai_proxy_impersonation_session=; HttpOnly[; Secure]; SameSite=Strict;
                    Path=/; Max-Age=0        (cleared ONLY on 204)
  401 -> { code: "ERR_AUTH_NO_SESSION" }                       (no ai_proxy_session; no upstream call)
  401 -> { code: "ERR_AUTH_TOKEN_INVALID" }                    (relayed)
  403 -> { code: "ERR_AUTH_FORBIDDEN" }                        (relayed)
  404 -> { code: "ERR_IMPERSONATION_SESSION_NOT_FOUND" }       (relayed)
  409 -> { code: "ERR_IMPERSONATION_SESSION_ALREADY_ENDED" }   (relayed)
  Authorization for the upstream call is built from ai_proxy_session ONLY — unconditionally, even
  when ai_proxy_impersonation_session is also present on the request. Non-negotiable (mirrors the
  sibling task's own frozen "End requires the ORIGINAL JWT" contract) — see Safety rule, §5.

═══════════════════════════════════════════════════════════════════════════════
PART B — MODIFIED: apps/dashboard/app/api/gw/[...path]/route.ts
═══════════════════════════════════════════════════════════════════════════════

Replaces today's single `getTokenFromRequest(req)` with a path-aware resolution:

  function resolveUpstream(req, pathStr): { token: string, cookieSource: "original" | "impersonation" } | null
    original = read ai_proxy_session                          (raw value, unchanged shape)
    impersonation = parseEnvelope(read ai_proxy_impersonation_session)?.token   (M1 envelope; a
                    malformed/absent envelope parses to undefined, never throws)
    isPlatformPath = pathStr === "admin/platform" || pathStr.startsWith("admin/platform/")
    if isPlatformPath:            return original ? {token: original, cookieSource: "original"} : null
    else:                         return impersonation ? {token: impersonation, cookieSource: "impersonation"}
                                          : (original ? {token: original, cookieSource: "original"} : null)

  null -> 401 { code: "ERR_AUTH_NO_SESSION" }, unchanged from today.

Every downstream behavior (isDataPlane / getPlaygroundToken exchange / streaming / timeouts /
body-size guard / hop-by-hop headers) is UNCHANGED — it operates on whichever token was resolved,
exactly as it operates on the one token resolved today.

The existing control-plane-401 handling becomes source-aware:
  if upstream.status === 401 && isControlPlanePath:
    clear ONLY the cookie named by `cookieSource` (ai_proxy_session -> buildClearCookieValue(),
    ai_proxy_impersonation_session -> its own equivalent clear value) — never unconditionally
    ai_proxy_session as today's code does. `ERR_AUTH_SESSION_EXPIRED` is returned regardless of
    which cookie was cleared (the response CODE is unchanged; only which Set-Cookie is emitted changes).

═══════════════════════════════════════════════════════════════════════════════
PART C — component/hook contract (apps/dashboard)
═══════════════════════════════════════════════════════════════════════════════

AppShellProps (components/ui/app-shell.tsx) — ADDITIVE:
  banner?: React.ReactNode
  Rendered between the skip-link and the existing lg:flex-row row. That row's own className:
    banner absent -> the literal, unchanged string containing "lg:h-screen" (byte-identical default)
    banner present -> a calc-based reservation (e.g. "lg:h-[calc(100vh-2.75rem)]") instead, so
                       banner-height + row-height together always total exactly 100vh at the lg
                       breakpoint. Every other existing class/prop/behavior is unchanged.

useImpersonationStatus() (lib/hooks/use-impersonation-status.ts) — NEW:
  useQuery({ queryKey: ["impersonation-status"], queryFn: <GET /api/platform/impersonation via
    resilientFetch>, retry: false, refetchInterval: ~5000 })
  Returns the GET response shape from Part A verbatim (active:false, or active:true + session_id +
  expires_at + target + actor).

ImpersonationBanner (components/platform/ImpersonationBanner.tsx) — NEW, no props, self-contained:
  data-testid="impersonation-banner". Renders null when !active. When active: target email + role,
  actor email when non-null, a live countdown derived from `expires_at` (a `Date.now()` diff recomputed
  on a display tick, e.g. every second — `expires_at` itself is never re-fetched more often than
  useImpersonationStatus()'s own ~5s refetchInterval; available on every active response, fresh load
  or not, per M1/M4), and an End control behind a role="dialog" + useFocusTrap confirm step mirroring
  PlatformKeysTab's own revoke-confirm exactly.

PlatformMembersTab (components/platform/PlatformMembersTab.tsx) — MODIFIED, additive:
  New prop: tenantKind?: string (absent/anything other than exactly "customer" -> the new action
  renders for NO row — fails closed; the existing tests/platform-members.test.tsx, which never
  passes this prop, needs zero changes).
  New per-row "Impersonate" action, gated as described in M9/R3/R4, calling POST
  /api/platform/impersonation with this row's {tenant_id: tenantId, user_id: row.id}.

PlatformTenantDetail (components/platform/PlatformTenantDetail.tsx) — MODIFIED, one line:
  <PlatformMembersTab tenantId={tenantId} tenantKind={tenant.kind} /> (tenant.kind already fetched;
  no new query).

DashboardShell (components/dashboard-shell.tsx) — MODIFIED, additive:
  Calls useImpersonationStatus() (a SEPARATE hook call from the existing, untouched useCurrentUser()
  call) and passes <ImpersonationBanner /> as AppShell's new banner prop when active.

Schema: no new/changed database table anywhere — the gateway's own `impersonation_sessions` table
  (frozen by the sibling task) remains the sole durable source of truth. This task's only "storage"
  is two httpOnly cookies (opaque to the BFF beyond forwarding) and the client's in-memory React
  Query cache (cleared, not persisted, across Mint/End — M10).
```

Glossary deltas:
- `impersonation cookie`: the second, additive httpOnly cookie (`ai_proxy_impersonation_session`)
  that coexists with — and never replaces or is derived by overwriting — the operator's own
  `ai_proxy_session` cookie for the duration of an active impersonation session; cleared on explicit
  End or left to lapse via its own `Max-Age` at the session's natural TTL. Its VALUE is a small,
  URI-encoded JSON envelope (`{token, session_id, expires_at}`), not a bare JWT — see M1.

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY (new
     terms declared as a Glossary delta) + the bundle's lowest-confidence flag was surfaced at
     the freeze (or an honest "none material"). -->

Least-sure flag surfaced at freeze: (multiple, carried from §1, restated here for the freeze reader)
⚠ [contract] The impersonation-cookie envelope (M1) concentrates 3 call sites' correctness (Mint
  writes, Status reads, `/api/gw` reads) into 1-2 shared encode/decode helpers — a single encoding bug
  there breaks impersonation entirely at once (though always fail-CLOSED, never a leak), a broader
  blast radius than a bare-token cookie would have had, in exchange for `session_id` and the countdown
  surviving a page refresh with zero gateway change. Worth an explicit, careful look at those 1-2
  helper functions specifically at Build/Verify, not just at the call sites that use them.
⚠ [spec] `queryClient.clear()` (blanket) on Mint/End success rather than a precise key allowlist — the
  safer default, at the cost of a brief repo-wide loading flash; the more easily reversible of the
  two flags if it reads as heavy-handed once seen running.
⚠ [contract] This header block does NOT declare `sensitivity: security`, unlike its two milestone
  siblings (`impersonation-session-lifecycle`, `impersonation-live-session-guard`, both scaffolded
  with that line already present) — this task's own surface (cookie precedence, token-leak-into-
  wrong-request prevention) is security-ADJACENT even though the underlying gateway auth boundary is
  untouched and independently already reviewed. Left exactly as scaffolded per this drafting
  session's own instructions (not mine to add); flagged here explicitly so the freeze decision-maker
  can choose to add it before or at freeze, rather than the omission passing unnoticed.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (matches this codebase's existing `components/**/*.tsx` + `lib/**/*.ts`
  coverage gate; the modified `app/api/gw/[...path]/route.ts` is exercised directly, not just via
  component tests, so its new branch is not a coverage blind spot).

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_mint_relays_original_cookie_sets_impersonation_cookie_never_echoes_token: arrange a request
    carrying ai_proxy_session / act POST mint / assert 201 body has no token field, Set-Cookie names
    ai_proxy_impersonation_session, no Set-Cookie for ai_proxy_session · covers: M1, M2
  - test_mint_no_session_cookie_401_never_calls_upstream: arrange a request with no cookie / act POST
    mint / assert 401 ERR_AUTH_NO_SESSION and the mocked gateway endpoint was never hit · covers: R1
  - test_mint_relays_gateway_403_target_invalid: arrange a mocked gateway 403 / act POST mint / assert
    403 ERR_IMPERSONATION_TARGET_INVALID relayed verbatim, no cookie set · covers: M2, R2
  - test_mint_relays_gateway_409_already_active: arrange a mocked gateway 409 / act POST mint / assert
    409 ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE relayed verbatim · covers: M2, R2
  - test_status_no_impersonation_cookie_returns_inactive: arrange a request with only ai_proxy_session
    / act GET status / assert {active:false}, no gateway call made · covers: M4
  - test_status_active_resolves_target_and_actor_via_two_relay_calls: arrange both cookies present,
    mocked GET /me returns different identities per Bearer value / act GET status / assert
    {active:true, target, actor} both correctly resolved from their OWN relay call · covers: M4
  - test_status_invalid_impersonation_cookie_self_heals: arrange an impersonation cookie the mocked
    GET /me rejects with 401 / act GET status / assert {active:false} AND a Set-Cookie clearing the
    impersonation cookie · covers: M4
  - test_status_actor_relay_failure_degrades_to_actor_null: arrange a valid impersonation cookie but
    no/failing ai_proxy_session relay / act GET status / assert {active:true, target, actor:null} ·
    covers: M4 (edge case)
  - test_end_uses_original_cookie_never_impersonation_even_when_both_present: arrange a request
    carrying BOTH cookies with DISTINGUISHABLE values / act DELETE end / assert the captured upstream
    Authorization header equals the ORIGINAL cookie's value · covers: M3 (the core frozen-contract
    mirror)
  - test_end_no_session_cookie_401_never_calls_upstream: arrange no ai_proxy_session / act DELETE end
    / assert 401 ERR_AUTH_NO_SESSION, gateway never hit · covers: R1
  - test_end_clears_impersonation_cookie_on_204: arrange a mocked gateway 204 / act DELETE end /
    assert Set-Cookie clears ai_proxy_impersonation_session with Max-Age=0 · covers: M3
  - test_end_409_already_ended_does_not_clear_cookie: arrange a mocked gateway 409 / act DELETE end /
    assert 409 relayed AND no Set-Cookie is emitted · covers: M3, R2
  - test_gw_proxy_prefers_impersonation_cookie_for_ordinary_admin_path: arrange both cookies, path
    admin/usage / act GET via the proxy handler / assert upstream Authorization used the
    impersonation cookie's value · covers: M5
  - test_gw_proxy_always_uses_original_cookie_for_admin_platform_path: arrange both cookies, path
    admin/platform/tenants/x / act GET via the proxy handler / assert upstream Authorization used the
    ORIGINAL cookie's value · covers: M5, R5
  - test_gw_proxy_falls_back_to_original_when_no_impersonation_cookie: arrange only ai_proxy_session,
    any ordinary path / act GET / assert upstream Authorization unchanged from pre-task behavior ·
    covers: M5 (regression guard)
  - test_gw_proxy_control_plane_401_clears_only_the_cookie_actually_used: arrange the impersonation
    cookie supplied the token, mocked upstream 401 on a control-plane path / act GET / assert
    Set-Cookie clears ONLY the impersonation cookie · covers: M5, R6
  - test_banner_hidden_when_inactive: arrange status {active:false} / act render ImpersonationBanner
    / assert nothing rendered · covers: M8
  - test_banner_shows_target_and_actor_when_active: arrange status {active:true, target, actor} / act
    render / assert both emails + target role visible · covers: M8
  - test_banner_countdown_survives_fresh_load_via_cookie_envelope: arrange a FRESH QueryClient (no
    same-tab mint memory) whose status response already includes expires_at (sourced server-side from
    the cookie envelope) / act render / assert a live countdown is shown, identical in kind to the
    same-tab-mint case · covers: M1, M4, M8
  - test_status_malformed_envelope_degrades_to_inactive_never_throws: arrange an impersonation cookie
    whose value is corrupted/non-JSON / act GET status / assert {active:false}, no thrown exception ·
    covers: M1 (edge case)
  - test_banner_end_requires_confirm_before_calling_end: arrange active session / act click End
    without confirming / assert no DELETE call made; act confirm / assert DELETE called with the
    right session_id · covers: M8
  - test_banner_end_success_clears_query_cache: arrange a populated QueryClient with an unrelated
    cached query / act End succeeds / assert that unrelated query's cached data is gone · covers: M10
  - test_impersonate_action_visible_for_eligible_row: arrange tenantKind="customer", a non-superadmin
    non-self row / act render / assert the Impersonate button is present and enabled · covers: M9
  - test_impersonate_action_hidden_for_superadmin_role_row: arrange a role="superadmin" row / act
    render / assert no Impersonate action for that row · covers: R3
  - test_impersonate_action_hidden_when_tenant_kind_not_customer: arrange tenantKind="platform" (and
    separately, tenantKind absent) / act render / assert no Impersonate action for any row in either
    case · covers: R3
  - test_impersonate_action_hidden_for_callers_own_row: arrange a row matching the current user id /
    act render / assert no Impersonate action on that row · covers: R3
  - test_impersonate_action_disabled_when_session_already_active: arrange useImpersonationStatus
    active:true / act render an eligible row / assert the button is present but disabled with
    explanatory text · covers: R4
  - test_impersonate_confirm_cancel_does_not_mutate: arrange eligible row / act click Impersonate,
    then Cancel in the dialog / assert no POST mint call made · covers: M9
  - test_impersonate_confirm_calls_mint_with_correct_tenant_and_user: arrange eligible row / act
    click Impersonate, then Confirm / assert POST mint called with {tenant_id, user_id} matching the
    row and current tenant · covers: M2, M9
</test_plan>

Tests live in (split by import-availability so each file reds for ONE clean reason — a file mixing a
  not-yet-existing import with an existing one would fail to load entirely, per
  `tests-bff/route-handlers.test.ts`'s own "each imported separately" convention, taken one step
  further here into separate files):
  `apps/dashboard/tests-bff/impersonation-mint-end-status-routes.test.ts` (13 tests — Part A: the 3
    NEW BFF route handlers, including the cookie-envelope encode/decode behavior named in the ⚠
    freeze flag above; MODULE_NOT_FOUND red, direct-handler-invocation style mirroring
    `tests-bff/route-handlers.test.ts`)
  `apps/dashboard/tests-bff/impersonation-gw-proxy-cookie-precedence.test.ts` (4 tests — Part B: the
    MODIFIED, already-existing `/api/gw/[...path]/route.ts`; logic-assertion red — the import
    succeeds today, the new cookie-precedence behavior does not exist yet)
  `apps/dashboard/tests/platform-impersonation-banner.test.tsx` (5 tests — the new banner component;
    MODULE_NOT_FOUND red, MSW+RTL style mirroring `tests/platform-keys.test.tsx`)
  `apps/dashboard/tests/platform-members-impersonate-action.test.tsx` (7 tests — the new Members-tab
    row action on the ALREADY-EXISTING `PlatformMembersTab`; logic-assertion red (import succeeds,
    the new action doesn't exist yet) — a NEW file so `tests/platform-members.test.tsx` is untouched)
MUST run red (missing implementation) before Build — verified this session; see the run log below.

Run log (this session — `cd apps/dashboard && npx vitest run
  tests-bff/impersonation-mint-end-status-routes.test.ts
  tests-bff/impersonation-gw-proxy-cookie-precedence.test.ts
  tests/platform-impersonation-banner.test.tsx
  tests/platform-members-impersonate-action.test.tsx`):
  Exit code 1 — RED, confirmed. 4/4 files report failed; the JSON reporter shows 11 tests actually
  collected (2 passed / 9 failed) plus 2 files (18 tests) that fail at COLLECTION time, before any
  assertion runs (Vite's own "Failed to resolve import ... Does the file exist?" — the MODULE_NOT_FOUND
  equivalent under this stack):
  - `impersonation-mint-end-status-routes.test.ts` (13 tests): fails to resolve
    `@/app/api/platform/impersonation/route` and `.../sessions/[sessionId]/route` — neither BFF route
    file exists yet. Correct.
  - `impersonation-gw-proxy-cookie-precedence.test.ts` (4 tests): 2 FAILED / 2 PASSED.
    FAILED (genuinely red, new behavior missing): `test_gw_proxy_prefers_impersonation_cookie_for_
    ordinary_admin_path` (captured `Bearer original-session-jwt-001`, wanted the impersonation token —
    today's code has no impersonation-cookie concept at all yet); `test_gw_proxy_control_plane_401_
    clears_only_the_cookie_actually_used` (today unconditionally clears `ai_proxy_session`, not yet
    source-aware). PASSED (by design, not a gap — see the file's own header comment):
    `test_gw_proxy_always_uses_original_cookie_for_admin_platform_path` (today's UNMODIFIED code
    already always resolves `ai_proxy_session` for every path, which coincidentally already satisfies
    this one path's requirement; its real regression-catching value is entirely post-Build) and
    `test_gw_proxy_falls_back_to_original_when_no_impersonation_cookie` (an explicit regression guard,
    intended to hold both before and after Build, per its own test_plan framing).
  - `platform-impersonation-banner.test.tsx` (5 tests): fails to resolve
    `@/components/platform/ImpersonationBanner` — the component doesn't exist yet. Correct.
  - `platform-members-impersonate-action.test.tsx` (7 tests): all 7 FAILED — "Unable to find an
    accessible element with the role button and name /impersonate/i" (`PlatformMembersTab` itself
    imports fine; the new action doesn't exist yet). Every "hidden" assertion in this file is a
    same-test count check (`getAllByRole(...).toHaveLength(1)` against a 2-row fixture) or an explicit
    same-test control render that DOES expect the button — never a bare
    `queryByRole(...).not.toBeInTheDocument()` in isolation — specifically so none of the 3
    fail-closed tests could pass vacuously merely because the feature doesn't exist yet; confirmed all
    7 genuinely fail today, for the intended reason.

<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/dashboard/app/api/platform/impersonation/route.ts` (NEW — POST mint, GET status)
  `apps/dashboard/app/api/platform/impersonation/sessions/[sessionId]/route.ts` (NEW — DELETE end)
  `apps/dashboard/app/api/gw/[...path]/route.ts` (MODIFIED — SHARED, heavily-tested; token-resolution
    step only, see Known-problem fixes below — do not touch streaming/timeout/body-guard logic)
  `apps/dashboard/lib/hooks/use-impersonation-status.ts` (NEW)
  `apps/dashboard/components/platform/ImpersonationBanner.tsx` (NEW)
  `apps/dashboard/components/platform/PlatformMembersTab.tsx` (MODIFIED — additive `tenantKind` prop
    + new row action only; do not touch the existing assign-role column/mutation)
  `apps/dashboard/components/platform/PlatformTenantDetail.tsx` (MODIFIED — one-line prop thread only)
  `apps/dashboard/components/ui/app-shell.tsx` (MODIFIED — additive `banner` prop only; do not touch
    any existing landmark, class, or prop)
  `apps/dashboard/components/dashboard-shell.tsx` (MODIFIED — additive hook call + prop pass-through
    only; the existing `useCurrentUser()` call and its props to `AppShell` stay untouched)
  `apps/dashboard/tests-bff/impersonation-mint-end-status-routes.test.ts` (NEW)
  `apps/dashboard/tests-bff/impersonation-gw-proxy-cookie-precedence.test.ts` (NEW)
  `apps/dashboard/tests/platform-impersonation-banner.test.tsx` (NEW)
  `apps/dashboard/tests/platform-members-impersonate-action.test.tsx` (NEW)

Strategy (ordered batches):
  1. BFF layer first (the 2 new route files + the `/api/gw` token-resolution change) — everything
     else depends on this; get every test in `impersonation-mint-end-status-routes.test.ts` and
     `impersonation-gw-proxy-cookie-precedence.test.ts` green before touching any component.
  2. `useImpersonationStatus()` — thin, depends only on step 1's GET status route.
  3. `ImpersonationBanner` + `AppShellProps.banner` + `DashboardShell` wiring — get
     `platform-impersonation-banner.test.tsx` green; re-run `tests/design-system/
     app-shell-sidebar.test.tsx` immediately after touching `app-shell.tsx` (before moving on) to
     catch any frozen-contract regression at the earliest possible point.
  4. `PlatformMembersTab`'s new row action + `PlatformTenantDetail`'s one-line prop thread — get
     `platform-members-impersonate-action.test.tsx` green; re-run the existing
     `tests/platform-members.test.tsx` and `tests/platform-tenant-detail.test.tsx` immediately after.
  5. Full `apps/dashboard` suite, BOTH vitest projects (`tests/` and `tests-bff/`) — confirm zero
     regressions anywhere this task's shared-file edits could have reached.

Persona (optional): frontend-engineer (`.add/personas/frontend-engineer.md` — this repo's own
  BFF-trust-boundary + SSR-safety + design-token-fidelity lens); consult `ui-designer`/`ux-researcher`
  (`.add/personas/ui-designer.md`, `ux-researcher.md`) for the visual/IA judgment calls specifically
  (banner composition, WCAG contrast on the new banner surface, hit-target sizing on its controls).
Spawn isolation (default): worktree (this repo's own standing default for build/verify spawns) —
  this DESIGN draft itself was authored directly in the primary checkout per the orchestrating
  session's own explicit instruction for this drafting pass only.
Known-problem fixes:
  - The gw-proxy's control-plane-401 cookie-clear MUST become source-aware (clear whichever cookie
    supplied the rejected token) — a naive patch that keeps unconditionally clearing `ai_proxy_session`
    would silently log a superadmin out of their own real session on an impersonation-token rejection.
  - `AppShell`'s row-height class must be a CONDITIONAL swap (the literal string `lg:h-screen` when
    `banner` is absent), never an always-on calc-based class — verified by keeping
    `tests/design-system/app-shell-sidebar.test.tsx` fully green, unedited.
  - `PlatformMembersTab`'s new `tenantKind` prop must fail CLOSED (hide the action) on
    absent/unrecognized values, not fail open — this is what keeps the existing
    `tests/platform-members.test.tsx` passing with zero changes.
  - Never resolve `admin/platform/*` via the impersonation cookie, even opportunistically — the path
    check is the ONLY gate; do not add a heuristic or a "try original, fall back to impersonation"
    ordering for this prefix.
  - The M1 cookie envelope MUST be `encodeURIComponent(JSON.stringify(...))` when set and
    `JSON.parse(decodeURIComponent(...))` wrapped in `try/catch` when read — a raw, un-encoded JSON
    string is not a valid cookie-octet (RFC 6265 excludes `,`/`"`/`;` from a bare cookie value) and
    would silently corrupt/truncate; a missing `try/catch` would turn one malformed cookie into an
    unhandled 500 instead of the required "treat as absent" degrade (see the ⚠ freeze flag, §3).
  - Once `DashboardShell` calls `useImpersonationStatus()` unconditionally (step 3), EVERY existing
    test across BOTH vitest projects that renders `DashboardShell` (directly, or via a page that mounts
    inside it) will make a new `GET /api/platform/impersonation` call for the first time. Both
    `tests/mocks/handlers.ts` and `tests-bff/mocks/handlers.ts` run under `onUnhandledRequest:"error"`
    (`tests/setup.ts`/`tests-bff/setup.ts`) — an unmocked call fails LOUDLY, not silently. Add ONE
    default handler (`{active:false}`) to EACH shared handlers.ts file BEFORE step 3's full-suite run,
    rather than chasing what would otherwise look like a wave of unrelated test breakage across other
    tasks' own test files.
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): End's upstream Authorization header is built from `ai_proxy_session`
  ONLY — never conditionally, never as a fallback from the impersonation cookie. This one line is the
  client-side mirror of the sibling task's own frozen, security-reviewed "End requires the ORIGINAL
  JWT" contract; getting this branch wrong defeats the entire reason two cookies exist.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the gateway's frozen contract; do NOT add a new gateway
  endpoint or modify `platform_impersonation_router.py` / `jwt_service.py` / `router.py`'s `/me`
  handler — the M1 cookie envelope is what keeps `session_id`/`expires_at` recoverable without any
  gateway change, so there is no remaining reason to touch the gateway for this task's own scope;
  allow-list packages only — no new npm dependency should be needed, everything here composes
  existing primitives (`resilient-fetch.ts`, `use-focus-trap.ts`, `@tanstack/react-query`); ask if
  unclear.

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
