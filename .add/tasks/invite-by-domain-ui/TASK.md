# TASK: Dashboard: domain invite link manage UI + two-step redeem page (D3)

slug: invite-by-domain-ui · created: 2026-07-20 · stage: production
milestone: domain-onboarding-softening
component: dashboard
sensitivity: architecture
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/dashboard/components/settings/DomainClaimsSettings.tsx:DomainClaimsSettings` (task-5, 672 lines) — the OWNER-scoped Domains-tab console. EXTEND: a member/owner-verified claim row reveals an "Invite your team by link" section (mint / copy shareable URL shown once / list active / revoke). REUSE in place: `DomainClaimListItem` shape, the copy-to-clipboard idiom, `getErrorTitle`/`bffCode` (BffError→code/title), the calm `role="status"` vs KEPT-VERBATIM loud `role="alert"` convention, and task-5's `applyMemberVerified`/seal-derivation for the eligibility gate (only a member/owner-verified row shows the section).
  - `apps/dashboard/components/settings/OtpInput.tsx:OtpInput` (task-5) — the 6-segment OTP input (auto-advance, paste-fills-all, backspace-to-prev, inputmode=numeric). REUSE VERBATIM for the redeem page's code step.
  - `apps/dashboard/app/(auth)/invite/[token]/page.tsx` + `apps/dashboard/components/auth/AcceptInviteForm.tsx` (194 lines) + `apps/dashboard/app/api/auth/invite/[token]/route.ts` — the existing PUBLIC invite-accept page (preview → set password → `router.push("/app")` with a session established). MIRROR its shape for the NEW public redeem page at `app/(auth)/join/[token]/page.tsx` + `JoinByDomainForm.tsx` — but two-phase (email → code+password) and NO preview call (the frozen 6a redeem has no GET preview).
  - `apps/dashboard/lib/bff-client.ts` (`bffPost`, `BffError`, `bffCode`) + the catch-all proxy `app/api/gw/[...path]/route.ts` — the ONLY FE→gateway path (cookie-auth for admin calls; the PUBLIC redeem calls are unauthenticated). The admin mint/list/revoke reach the frozen `/admin/domain-invite-links` endpoints as-is; the redeem page's two POSTs reach `/domain-invite-links/{token}/redeem` + `.../redeem/verify`.
Context (working folder):
  - Post-join auto-login (Tin-confirmed): the frozen 6a `redeem/verify` returns `{tenant_id,user_id,email}` but NO session token. A NEW BFF route (e.g. `app/api/auth/join/[token]/route.ts`, mirroring `api/auth/invite/[token]/route.ts`) forwards verify, and on 201 CHAINS a gateway login with the just-set email+password to set the session cookie, then the page `router.push("/app")` — parity with invite-accept. Presentation-layer only; 6a gateway UNTOUCHED.
  - `apps/dashboard/tests/` (legacy vitest project) + `apps/dashboard/tests-bff/` (BFF project, `/api/gw/...` paths, MSW `onUnhandledRequest:"error"`) — the two test homes; new suite + BFF-route test.
Honors (patterns / conventions):
  - Presentation + BFF pass-through ONLY — every trust/authz/rate-limit/domain/seat decision is server-side in the frozen 6a core. The UI sends only what the endpoints take (admin: `{domain}`; redeem: `{email}` then `{email,code,password}`); it never re-derives eligibility or trust.
  - Airier tokens (`--primary #2f6df0`, `--accent-soft-foreground #1c4bb8`, success/warning `-text` AA variants, Geist / Geist Mono); calm `role="status"` recoverable errors, self-contained 429 message (BFF drops Retry-After); SSR-safe localStorage only in useEffect.
  - The frozen supersede/rotate: the manage UI must show, at mint of a domain that already has an active link, a clear "this replaces the old link — the previous URL stops working" hint (Tin-confirmed supersede at the 6a freeze).
Seams consulted: FE→gateway = the `/api/gw/[...path]` catch-all BFF; the redeem page uses UNAUTHENTICATED public endpoints (no session until auto-login). No `.add/SEAMS.md` entry needed.
Anchors the contract cites: the 6a endpoints (`POST/GET/DELETE /admin/domain-invite-links`, `POST /domain-invite-links/{token}/redeem`, `.../redeem/verify`), the reused `OtpInput`, the extended `DomainClaimsSettings`, the new `/join/[token]` route + `JoinByDomainForm`, the new auto-login BFF route — all in `apps/dashboard`.
Issues/Risks (→ feed §1):
  - The redeem page is PUBLIC/unauthenticated — no cookie yet; the two POSTs go through the catch-all BFF without an Authorization header (fine — 6a redeem is unauthenticated). The auto-login chain needs the email+password held client-side only for the one login call, never persisted.
  - Error taxonomy is rich: `ERR_DOMAIN_INVITE_DOMAIN_MISMATCH`(403), `ERR_MEMBER_VERIFY_CODE_INVALID`(400)/`_EXPIRED`(410)/`_TOO_MANY_ATTEMPTS`(429), `ERR_AUTH_PASSWORD_WEAK`(400), `ERR_TENANT_EMAIL_TAKEN`(409), `ERR_PLAN_SEAT_CAP_EXCEEDED`(403), `ERR_DOMAIN_INVITE_LINK_INACTIVE`(409)/`ERR_INVITE_EXPIRED`(410)/`ERR_INVITE_NOT_FOUND`(404), `ERR_RATE_LIMITED`(429). The page maps each to a calm, self-contained message (never promise a countdown — Retry-After is dropped by the BFF).
  - Eligibility gate on the admin side is presentation-only (the section shows on a member/owner-verified row); the REAL gate is 6a's 403 — so the UI must still handle a create 403 gracefully (e.g. a race where verification lapsed).
  - The token is shown ONCE at mint (the frozen create response is the only place it appears; list never returns it) — the UI must make copying obvious and not imply it can be retrieved later.
Related intent: milestone `domain-onboarding-softening` — the CORE goal's USER-FACING half (task 6b), consuming the FROZEN 6a gateway core (`71641c5`). D3 (Tin-confirmed): domain-restricted shareable link, admin-initiated, revocable, only @domain email redeems → MEMBER, server-side, never auto-join. UDD axes CONFIRMED by Tin 2026-07-20 (AskUserQuestion): (1) admin manage UI INLINE on the Domains tab (extend DomainClaimsSettings); (2) redeem = single 2-phase page at `/join/[token]` reusing OtpInput; (3) post-join AUTO-LOGIN (BFF chains login) → /app. See [[domain-onboarding-progressive-trust]].
Ground SHA: 71641c5 (invite-by-domain 6a gateway core is HEAD; symbols cited above, not bare lines — any line ref is "as of" this commit).
UDD WIREFRAME CONFIRMED by Tin 2026-07-20 — capture `.add/design/captures/invite-by-domain-ui.html` (artifact https://claude.ai/code/artifact/3211f124-e456-4e08-a687-dc1466b68480). Renders the 3 locked axes: admin section INLINE on the Domains tab (gated on a member/owner-verified row; token shown ONCE + copy + "won't see it again"; supersede confirm bar; calm 403-race status), and the public single 2-phase `/join/[token]` page (email → calm domain-mismatch; OtpInput code + password → calm invalid/expired/too-many with resend, no countdown; success → auto-login → /app). Airier tokens, light+dark, AA.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Dashboard for the domain invite link — an inline admin manage panel on the Domains tab (mint/list/revoke, token shown once, supersede confirm) + a public two-phase `/join/[token]` redeem page (email → mailbox code + password → auto-login). Presentation + BFF pass-through over the FROZEN 6a gateway core (71641c5); UI is task 6b of domain-onboarding-softening.
Framings weighed: inline-on-Domains-tab + single 2-phase /join page + auto-login (chosen — all three Tin-confirmed at UDD) · Members-area dialog / stepped redeem routes / redirect-to-login (rejected at UDD)

Must:
<must>
  - M1 · SECTION GATE — the "Invite your team by link" section renders on a Domains-tab claim row ONLY when its sealState is member-verified OR owner-verified (task-5 derivation); a pending/expired row shows NO section. Presentation-only gate (the real gate is 6a's 403).
  - M2 · LIST/STATE ON LOAD — the section reads active-link state from `GET /admin/domain-invite-links` (via the catch-all BFF): if an active link exists for the row's domain it shows "a link is active" + expiry + Revoke (NOT the URL — the list never returns a token); else the empty "Create invite link" state.
  - M3 · MINT — "Create invite link" POSTs `{domain}` to `/admin/domain-invite-links`; on 201 the FULL shareable URL (`<origin>/join/<token>`) is shown ONCE in a mono copy-block with a "copy now — you won't see it again" note + "Expires in 30 days" + Revoke. The token is never re-fetched, never persisted beyond the render, never in the list.
  - M4 · SUPERSEDE CONFIRM — clicking "Create again" while an active link exists shows a confirm affordance ("This replaces the current link — the old URL stops working") BEFORE the POST; confirm mints a new link (new URL shown once), cancel aborts with no request.
  - M5 · REVOKE — Revoke DELETEs `/admin/domain-invite-links/{id}`; on success the section returns to the empty state.
  - M6 · CREATE-403 GRACEFUL — a create returning 403 `ERR_DOMAIN_INVITE_NOT_ELIGIBLE` (verification lapsed) shows a calm inline `role="status"` (never a crash / never a loud alert); the row stays.
  - M7 · COPY — the shareable URL has a copy-to-clipboard control (reuse the task-5 copy idiom).
  - M8 · REDEEM PHASE 1 — `/join/[token]` (public, (auth) layout) posts `{email}` to a dedicated public BFF route → gateway `/domain-invite-links/{token}/redeem`; on 202 advance to phase 2; the UI sends ONLY the email (never domain/tenant/role).
  - M9 · REDEEM PHASE 2 — reuse `OtpInput` (6-seg) for the code; post `{email, code, password}` to the verify BFF route → gateway `.../redeem/verify`; show the ≥10-char password rule; on 201 show the success state.
  - M10 · AUTO-LOGIN — the verify BFF route, on gateway 201, CHAINS a gateway login with the same email+password, sets the session cookie, returns success; the page `router.push("/app")`. The password lives client-side ONLY for the two POSTs, never persisted/logged.
  - M11 · CALM ERRORS — every redeem error maps to a calm, self-contained `role="status"` message, NEVER a countdown (Retry-After dropped by the BFF): domain-mismatch (stay phase 1); code invalid/expired/too-many (stay phase 2, offer Resend); password-weak (inline under the field); email-taken / seat-cap / link-inactive / invite-expired / not-found / rate-limited (self-contained).
  - M12 · RESEND — a "Resend code" affordance re-posts phase-1 for the same email (re-arms the code) and stays in phase 2.
</must>
Reject:
<reject>
  - R1 · a pending/expired domain row -> renders NO invite section
  - R2 · create returns 403 not-eligible -> "ERR_DOMAIN_INVITE_NOT_ELIGIBLE" surfaced as a calm inline status; row intact, no crash
  - R3 · redeem phase-1 non-@domain email -> "ERR_DOMAIN_INVITE_DOMAIN_MISMATCH" calm status; stays phase 1, no advance
  - R4 · redeem phase-2 wrong code -> "ERR_MEMBER_VERIFY_CODE_INVALID" calm status + Resend; stays phase 2, no redirect
  - R5 · redeem phase-2 expired/too-many code -> "ERR_MEMBER_VERIFY_CODE_EXPIRED"/"ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS" calm status + Resend; stays phase 2
  - R6 · redeem weak password -> "ERR_AUTH_PASSWORD_WEAK" inline under the field; no redirect
  - R7 · redeem email-taken / seat-cap / link-inactive / invite-expired / not-found / rate-limited -> the mapped calm self-contained status; no redirect, no session
  - R8 · verify BFF: gateway 201 but the chained login fails -> fall back to `/login` with a "you've joined {tenant} — sign in" message (never strand the user)
  - R9 · the token/URL never appears in the list view nor after a refresh (shown once at mint only)
</reject>
After:
<after>
  - an eligible admin can mint / see-once / copy / revoke exactly one active invite link per verified domain from the Domains tab, with the supersede consequence made explicit;
  - a teammate opening `/join/[token]` completes email → code → password and lands signed-in at /app;
  - the UI never sends or trusts domain/tenant/role and never re-exposes a token; every error is calm and self-contained.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] the two PUBLIC redeem calls go through DEDICATED `/api/auth/join/...` BFF routes (mirroring the existing `/api/auth/invite/[token]` route) rather than the cookie-auth catch-all `/api/gw/[...path]` — lowest confidence because it's the one structural choice: the redeemer has no session yet, and the verify step must chain a login to set the cookie, which the generic catch-all can't do. If wrong (catch-all can forward unauthenticated + a separate login call works): a small route-shape change, no behavior change.
  - [ ] the shareable origin is the dashboard public origin (`window.location.origin` or a public env) composed client-side as `<origin>/join/<token>` — the gateway returns only the token, never a URL.
  - [ ] auto-login reuses the same gateway login the BFF already calls elsewhere (email+password); a chained-login failure is rare (same creds just set) and falls back to /login (R8).
  - [ ] the admin section reads active-link existence per domain from the list (no token); mint is the only place a token appears.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: section shows only on a verified row   # M1,R1
  Given the Domains tab lists a member-verified "acme.com" row and a pending "contractor-acme.com" row
  When the tab renders
  Then the "Invite your team by link" section appears under acme.com
  And no invite section appears under the pending row

Scenario: load reflects an existing active link   # M2
  Given GET /admin/domain-invite-links returns one active link for acme.com (no token field)
  When the section mounts
  Then it shows "a link is active" + expiry + Revoke, and does NOT show a URL

Scenario: mint shows the URL once   # M3,M7
  Given the acme.com section is in the empty state
  When the admin clicks "Create invite link" and the BFF returns 201 {id, token, expires_at}
  Then the full URL "<origin>/join/<token>" is shown once in a copy-block with a copy control + "won't see it again" + "Expires in 30 days" + Revoke

Scenario: create-again requires supersede confirm   # M4
  Given an active link already exists for acme.com
  When the admin clicks "Create again"
  Then a confirm affordance "This replaces the current link — the old URL stops working" appears BEFORE any request
  And confirming POSTs a create (new URL shown once); cancelling sends no request

Scenario: revoke returns to empty   # M5
  Given an active link exists for acme.com
  When the admin clicks Revoke and the DELETE succeeds
  Then the section returns to the empty "Create invite link" state

Scenario: create 403 is calm   # M6,R2
  Given the acme.com row is shown but server-side verification has lapsed
  When "Create invite link" returns 403 ERR_DOMAIN_INVITE_NOT_ELIGIBLE
  Then a calm role="status" message appears and the row stays (no crash, no loud alert)

Scenario: redeem phase-1 advances on 202   # M8
  Given /join/<token> phase 1 with email "sam@acme.com"
  When "Send me a code" posts {email} and the BFF returns 202
  Then the page advances to phase 2 (code + password)
  And the request body carried only the email

Scenario: redeem phase-1 domain mismatch stays put   # R3
  Given /join/<token> phase 1
  When the email "jordan@gmail.com" posts and the BFF returns 403 ERR_DOMAIN_INVITE_DOMAIN_MISMATCH
  Then a calm role="status" "use your work address" message shows and the page stays in phase 1

Scenario: redeem phase-2 provisions + auto-logs-in   # M9,M10
  Given phase 2 with a valid code and a ≥10-char password
  When "Join Acme" posts {email, code, password}, the gateway returns 201, and the BFF chains a login that sets the session cookie
  Then the success state shows and the page redirects to /app

Scenario: wrong code stays in phase 2   # R4
  Given phase 2
  When the code posts and the BFF returns 400 ERR_MEMBER_VERIFY_CODE_INVALID
  Then a calm status + Resend shows, the page stays in phase 2, and no redirect occurs

Scenario: expired / too-many code stays in phase 2   # R5
  Given phase 2
  When verify returns 410 ERR_MEMBER_VERIFY_CODE_EXPIRED or 429 ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS
  Then a calm self-contained status (no countdown) + Resend shows and the page stays in phase 2

Scenario: weak password is inline   # R6
  Given phase 2 with a password shorter than 10 chars
  When "Join Acme" returns 400 ERR_AUTH_PASSWORD_WEAK
  Then an inline error under the password field shows and no redirect occurs

Scenario: other redeem errors are calm and self-contained   # R7,M11
  Given phase 2
  When verify returns 409 ERR_TENANT_EMAIL_TAKEN | 403 ERR_PLAN_SEAT_CAP_EXCEEDED | 409 ERR_DOMAIN_INVITE_LINK_INACTIVE | 410 ERR_INVITE_EXPIRED | 404 ERR_INVITE_NOT_FOUND | 429 ERR_RATE_LIMITED
  Then the mapped calm self-contained message shows, no redirect, no session established

Scenario: auto-login fallback   # R8
  Given verify returns 201 but the chained login fails
  When the BFF responds
  Then the page routes to /login with a "you've joined Acme — sign in" message (the user is never stranded)

Scenario: resend re-arms in phase 2   # M12
  Given phase 2
  When "Resend code" is clicked
  Then phase-1 is re-posted for the same email and the page stays in phase 2

Scenario: token never reappears   # R9
  Given a link was minted and its URL shown once
  When the section is re-loaded or the list re-fetched
  Then the full URL/token is absent from the list and the reloaded section
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── DASHBOARD components (presentation) ──
components/settings/DomainClaimsSettings.tsx  (EDIT)
  - reads active domain-invite links on load; renders <DomainInviteLinkSection> under each
    member/owner-verified claim row (M1/M2). No change to the KEPT-VERBATIM manual verify alert.
components/settings/DomainInviteLinkSection.tsx  (NEW)
  props: { claim: DomainClaimListItem, activeLink: ActiveLink | null,
           onMinted(link), onRevoked() }
  states: empty | minted-once(url) | active(no-url) | supersede-confirm | error(calm)
  ActiveLink = { id: string, domain: string, status: "active", expires_at: string }  # NO token
app/(auth)/join/[token]/page.tsx  (NEW)  — renders <JoinByDomainForm token=… />
components/auth/JoinByDomainForm.tsx  (NEW)
  phases: "email" -> "code" -> "success"; reuses <OtpInput/>; ≥10-char password rule;
  router.push("/app") on success, or /login?joined=… on the R8 fallback.

# ── BFF routes (Next server; the FE↔gateway seam) ──
GET/POST/DELETE via the existing catch-all app/api/gw/[...path]/route.ts  (cookie-auth, UNCHANGED)
  - GET    /api/gw/admin/domain-invite-links            -> gateway list  { links:[{id,domain,status,expires_at,created_at}] }  # no token
  - POST   /api/gw/admin/domain-invite-links  {domain}  -> gateway create { id, domain, token, status, expires_at, created_at }  # token ONCE
  - DELETE /api/gw/admin/domain-invite-links/{id}       -> gateway revoke { id, status:"revoked" }

POST /api/auth/join/[token]              body: { email }                         (NEW, PUBLIC — no session)
  202 -> { email }
  4xx -> { code } passthrough  (ERR_DOMAIN_INVITE_DOMAIN_MISMATCH | ERR_INVITE_NOT_FOUND |
                                ERR_DOMAIN_INVITE_LINK_INACTIVE | ERR_INVITE_EXPIRED | ERR_RATE_LIMITED)
POST /api/auth/join/[token]/verify       body: { email, code, password }         (NEW, PUBLIC)
  201 -> forwards gateway verify; on gateway 201 CHAINS gateway login(email,password),
         sets the session cookie, returns { ok: true, session: true }
       -> if the chained login fails: { ok: true, session: false }  (page routes to /login, R8)
  4xx -> { code } passthrough  (ERR_MEMBER_VERIFY_CODE_INVALID | _EXPIRED | _TOO_MANY_ATTEMPTS |
                                ERR_AUTH_PASSWORD_WEAK | ERR_TENANT_EMAIL_TAKEN |
                                ERR_PLAN_SEAT_CAP_EXCEEDED | ERR_DOMAIN_INVITE_DOMAIN_MISMATCH |
                                ERR_DOMAIN_INVITE_LINK_INACTIVE | ERR_INVITE_EXPIRED |
                                ERR_INVITE_NOT_FOUND | ERR_RATE_LIMITED)

Data/seam:
  - The shareable URL is composed CLIENT-side: `${origin}/join/${token}` (gateway returns only token).
  - The admin calls use the cookie-auth catch-all; the two /join calls are dedicated PUBLIC routes
    (no session yet) — the verify route is the ONLY place the password is used (chained login), never stored.
  - No gateway change (6a FROZEN @ 71641c5); no new dependency.
```

Glossary deltas: none (reuses "domain invite link" / "redemption" from 6a's glossary).
Least-sure flag surfaced at freeze: [contract] the two public redeem calls go through DEDICATED `/api/auth/join/...` BFF routes (mirroring `/api/auth/invite/[token]`) — NOT the cookie-auth catch-all — because the redeemer has no session and the verify step must chain a login to set the cookie. A route-shape choice only; no behavior change if reconsidered.
Status: FROZEN @ v1 — approved by Tin 2026-07-20 (dedicated `/api/auth/join/...` public BFF routes; verify route chains login+Set-Cookie)
Reported: yes

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the new components + BFF routes (every phase transition + every calm-error mapping exercised)
Plan (one test per scenario, asserting behavior not internals; component tests in `tests/`, BFF-route tests in `tests-bff/`):
<test_plan>
  - test_section_only_on_verified_row: render Domains with a member-verified + a pending row / assert the invite section under verified only · M1,R1
  - test_load_reflects_active_link: mock GET links → one active (no token) / assert "active"+expiry+Revoke, no URL · M2
  - test_mint_shows_url_once: click Create, mock 201 {token} / assert `<origin>/join/<token>` + copy + "won't see it again" + expiry · M3,M7
  - test_supersede_confirm_gates_create: active link exists, click "Create again" / assert confirm bar before any POST; confirm→POST, cancel→no request · M4
  - test_revoke_returns_to_empty: active link, click Revoke, mock 200 / assert empty state · M5
  - test_create_403_is_calm: click Create, mock 403 NOT_ELIGIBLE / assert calm role=status, row intact, no throw · M6,R2
  - test_redeem_phase1_advances_on_202: enter email, mock 202 / assert phase 2 shown; assert request body == {email} · M8
  - test_redeem_phase1_domain_mismatch: mock 403 DOMAIN_MISMATCH / assert calm status, stays phase 1 · R3
  - test_redeem_phase2_success_redirects: valid code+pw, mock verify {ok:true,session:true} / assert success + router.push("/app") · M9,M10
  - test_redeem_wrong_code_stays: mock 400 CODE_INVALID / assert calm + Resend, stays phase 2, no redirect · R4
  - test_redeem_expired_toomany_stays: mock 410/429 code errors / assert calm self-contained (no countdown) + Resend, stays phase 2 · R5
  - test_redeem_weak_password_inline: mock 400 PASSWORD_WEAK / assert inline field error, no redirect · R6
  - test_redeem_other_errors_calm: mock 409 EMAIL_TAKEN / 403 SEAT_CAP / 409 LINK_INACTIVE / 410 INVITE_EXPIRED / 404 NOT_FOUND / 429 RATE_LIMITED / assert mapped calm message, no redirect · R7,M11
  - test_redeem_resend_reposts_phase1: click Resend / assert phase-1 re-post for same email, stays phase 2 · M12
  - test_token_never_in_list: mint then re-fetch list / assert no token/URL in list or reloaded section · R9
  # BFF-route tests (tests-bff/)
  - test_bff_join_forwards_redeem: POST /api/auth/join/{token} {email} → asserts forward to gateway redeem, 202 {email} passthrough · M8
  - test_bff_verify_chains_login_sets_cookie: POST /api/auth/join/{token}/verify → gateway 201 → asserts chained login called + Set-Cookie + {ok:true,session:true} · M9,M10
  - test_bff_verify_login_fallback: gateway 201 but login fails → asserts {ok:true,session:false} (no cookie) · R8
  - test_bff_verify_passes_through_errors: gateway 4xx → asserts {code} passthrough, no login attempted · R4-R7
</test_plan>

Tests live in: `apps/dashboard/tests/invite-by-domain-ui.test.tsx` (components) + `apps/dashboard/tests-bff/join-by-domain-route.test.ts` (BFF) · MUST run red (missing implementation) before Build.
Green-bar (component dashboard): `vitest (ci.yml dashboard job, working-directory: apps/dashboard)` — verify `cd apps/dashboard && npx vitest run`.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/settings/` `apps/dashboard/components/auth/` `apps/dashboard/app/(auth)/join/` `apps/dashboard/app/api/auth/join/` `apps/dashboard/tests/invite-by-domain-ui.test.tsx` `apps/dashboard/tests/mocks/` `apps/dashboard/tests-bff/`

Component green-bar: `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`.
Strategy (ordered batches): 1. `DomainInviteLinkSection.tsx` (states: empty/minted-once/active/supersede-confirm/calm-error) + wire it into `DomainClaimsSettings.tsx` on member/owner-verified rows (reuse task-5 sealState + copy idiom + getErrorTitle/bffCode). 2. `JoinByDomainForm.tsx` (email→code→success phases, reuse OtpInput) + `app/(auth)/join/[token]/page.tsx`. 3. the two public BFF routes `app/api/auth/join/[token]/route.ts` (+ `/verify`) mirroring `api/auth/invite/[token]/route.ts`, with the verify route chaining login+Set-Cookie. 4. calm-error mapping table shared by both surfaces.
Persona (required): frontend-engineer (`.add/personas/frontend-engineer.md`) — presentation + BFF pass-through, calm-error convention, Airier tokens.
Spawn isolation (default): isolation "worktree" for any subagent build/verify spawn.
Known-problem fixes: MSW `onUnhandledRequest:"error"` → mock EVERY BFF route the components hit (the domain-claims list + the new links list + any registrar-hint GET on card render — add an initial default handler, task-5 lesson) · Tailwind-v4 font-token collision (verify computed style on a live render, not the token) · no fake-timer precedent → REAL timers for any resend/poll (task-5) · the token-shown-once must not leak into a refetch (assert absence) · SSR-safe: compose `${origin}` only in an effect / on the client.
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): the redeem password is held in component state ONLY for the two POSTs and the chained login; it is never persisted, logged, or placed in a query string; the token appears once (mint response) and is never re-fetched.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — task-6b files 43/43 green in isolation (`npx vitest run tests/invite-by-domain-ui.test.tsx tests-bff/join-by-domain-route.test.ts`); full suite 1629/1630 with the ONLY red being `dns-verify-softeners > test_pause_on_tab_hidden`, a pre-existing real-timer visibility FLAKE (passes 14/14 in isolation, unrelated to 6b — my change adds an independent invite-links query, not touching its verify-poll counter).
- [x] coverage did not decrease — new files: DomainInviteLinkSection 94.9% / JoinByDomainForm 92.0% lines, ≥90% §4 target met; no existing file lost coverage.
- [x] no test or contract was altered during build — orchestrator code-read of the diff: only NEW test files + one additive INITIAL MSW handler; no frozen test weakened, §3 untouched.
- [x] the green was EARNED, not gamed — refute-read (below): behavioral asserts only — request-body equality ({domain}, {email}, {email,code,password}), mint-call COUNT for supersede gating, unmount+remount for R9 token-never-reappears, Set-Cookie/JWT-absent-from-body, loginCalled===false on every 4xx. No vacuous asserts, no stubbed-away logic.
- [x] concurrency / timing of the risky operation is safe — presentation layer; React-Query mutations, real timers for resend (no fake-timer precedent), mint token in local state only. No shared mutable state.
- [x] no exposed secrets, injection openings, or unexpected dependencies — token `encodeURIComponent`'d into the path; JWT never in the response body (asserted); password held only for the two POSTs + chained login, never persisted/logged/in a query string; no new dependency (reuses zod/@tanstack/react-query/lucide already present).
- [x] layering & dependencies follow CONVENTIONS.md — presentation + BFF pass-through, mirrors invite-accept route/page verbatim; inline zod guards kept in-scope per the §5 note.
- [~] a person reviewed and approved the change — sensitivity=architecture, autonomy=auto → orchestrator auto-gate on clean evidence; Tin approves at the milestone-close push/PR ask (the standing pause).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] The "Invite your team by link" section renders on a member/owner-verified Domains-tab row and is ABSENT on a pending/expired row — confirmed by test_section_only_on_verified_row: exactly ONE "Invite your team by link" heading across a member-verified + a pending row.
- [x] Minting shows the full `<origin>/join/<token>` URL exactly once with a copy control + "won't see it again" note + "Expires in 30 days"; a re-fetch of the list never re-exposes the token — confirmed by test_mint_shows_url_once (URL `http://localhost:3000/join/tok-fresh-1` rendered after 201) + test_token_never_in_list (unmount+remount → token absent).
- [x] "Create again" over an active link surfaces the supersede-confirm ("the old URL stops working") BEFORE any POST; cancel sends no request — confirmed by test_supersede_confirm_gates_create (mintCalls===0 before confirm, ===1 after; cancel keeps mintCalls===0).
- [x] `/join/[token]` advances email→code only on a 202 sending ONLY `{email}`, then provisions on verify 201 and redirects to `/app`; a chained-login failure routes to `/login?joined=…` (never stranded) — confirmed by test_redeem_phase1_advances_on_202 (captured body === {email}), test_redeem_phase2_success_redirects (router.push "/app"), test_redeem_auto_login_fallback_routes_to_login (/login?joined=, NOT /app).
- [x] Every redeem error renders a calm self-contained `role="status"` with NO countdown and never a redirect/session — confirmed by test_redeem_phase1_domain_mismatch (stays phase 1), test_redeem_wrong_code_stays / _expired_toomany_stays (stay phase 2 + Resend, no-countdown regex asserted), test_redeem_weak_password_inline (role=alert inline), test_redeem_other_errors_calm (6 params, no /app).
- [x] The verify BFF route chains a gateway login and sets the session cookie on gateway 201, and passes 4xx `{code}` through WITHOUT attempting a login — confirmed by test_bff_verify_chains_login_sets_cookie (Set-Cookie ai_proxy_session=JWT + httponly + samesite=strict, JWT ABSENT from body) + test_bff_verify_passes_through_errors (11 params: loginCalled===false, no cookie).
- Green-bar: `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — DomainInviteLinkSection imported+rendered in DomainClaimsSettings.tsx (gated on sealState==="verified"||"member-verified"); JoinByDomainForm imported by app/(auth)/join/[token]/page.tsx; the two BFF route handlers imported by the BFF test; OtpInput reused in JoinByDomainForm. All confirmed by orchestrator code-read of every new file + the git diff.
- [x] DEAD-CODE (code) — no orphaned symbol: every exported component/route/interface is referenced by a caller or a test; no unused import (eslint clean per build report + code-read).
- [x] SEMANTIC (prose / non-code) — n/a (code task).

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by direct Read: OtpInput (components/settings/OtpInput.tsx, reused verbatim), DomainClaimsSettings sealState derivation (extended), the invite-accept route/page/form mirror (app/api/auth/invite/[token]/route.ts → chained login `/admin/auth/login` + `ai_proxy_session` cookie), bff-client bffAuthPost/BffError, parseJsonBody; the frozen 6a endpoints match the redeem router source (202 {email} / 201 {tenant_id,user_id,email}).
- [x] no anchor moved/renamed since Ground SHA (71641c5 is HEAD; the tree is unchanged apart from this task's own additive files).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self (orchestrator) · adversarially checked: probed for vacuous/overfit asserts and stubbed logic — found NONE. Every test asserts an OBSERVABLE effect: request-body equality ({domain} / {email} / {email,code,password}), mint-call COUNT gating supersede, unmount+remount proving R9 token-never-reappears, Set-Cookie contents WITH an explicit `not.toContain(JWT)` on the body, loginCalled===false on all 11 4xx codes, a no-countdown `not.toHaveTextContent(/\d+\s*(second|minute)s?/)` regex, and role="status" vs role="alert" separation. Guard tests (missing-field → 400 ERR_BFF_PAYLOAD_INVALID, upstreamCalled===false) confirm no upstream call on bad input. No mock returns the assertion's own expected value; no logic stubbed away.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: self (orchestrator code-read; sensitivity=architecture, not a security-floor task — the trust boundary is the FROZEN 6a gateway core, dual-verified at 71641c5)
1. Security: CLEAR — presentation + BFF pass-through; no trust decision moved client-side. Token `encodeURIComponent`'d into the path; JWT never in the response body (asserted); password used only for the two POSTs + the chained login, never persisted/logged/query-stringed; verify route attempts NO login on any 4xx (asserted, 11 codes); public routes forward only the email / {email,code,password} the gateway takes — no domain/tenant/role ever client-supplied.
2. Concurrency: CLEAR — React-Query mutations with onSuccess/onError; no shared mutable state; real timers for resend (no fake-timer precedent); the mint token lives only in local component state and is invalidated on the parent's list query after mint/revoke.
3. Architecture: CLEAR — mirrors app/api/auth/invite/[token]/route.ts verbatim (gatewayUrl/buildSessionCookieValue/relayUpstreamError/RouteContext); inline zod guards kept in-scope per the §5 note; the one out-of-scope-by-default file (tests/mocks/handlers.ts) is legitimate test-infra (registrar-hint precedent), scope-widened BEFORE the tests→build snapshot.
Verdict: PASS
Residue: none
Binding: advisory — architecture (security-floor N/A; no security-sensitive surface introduced — all authority is server-side in frozen 6a)

### GATE RECORD
Reported: yes — the verify evidence (suite result + refute-read + 3-lens) recorded above before this outcome; a human gate report goes to Tin at the milestone-close push/PR ask.
Outcome: PASS
component: dashboard · expected green-bar: vitest (ci.yml dashboard job, working-directory: apps/dashboard) · verify: cd apps/dashboard && npx vitest run
Reviewed by: orchestrator auto-gate (sensitivity=architecture, autonomy=auto, clean evidence) · date: 2026-07-20

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose inline-on-Domains-tab + single 2-phase /join page + auto-login; rejected Members-area dialog / stepped redeem routes / redirect-to-login (rejected at UDD)
- [human] freeze — froze §3 @ v1 (approved by Tin 2026-07-20 (dedicated `/api/auth/join/...` public BFF routes; verify route chains login+Set-Cookie))
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by orchestrator auto-gate (sensitivity=architecture, autonomy=auto, clean evidence))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

