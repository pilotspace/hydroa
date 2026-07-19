# TASK: Dashboard domain-claims console: create/list/verify(DNS-TXT)/revoke + joined-workspace onboarding confirmation

slug: domain-claims-console · created: 2026-07-19 · stage: production
milestone: enterprise-domain-onboarding
component: gateway, dashboard
autonomy: conservative
sensitivity: architecture
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): dashboard-only — a console UI + a "you joined {tenant}" confirmation over the ALREADY-BUILT, FROZEN gateway API. No gateway file is edited.
- CONSUME (frozen gateway, do NOT touch) `apps/gateway/src/gateway/domain_capture/api/domain_claims_router.py:domain_claims_router` — the 4 OWNER-only routes under prefix `/admin/domain-claims`: `create_domain_claim` (POST "" → 201 `DomainClaimCreateResponse`), `list_domain_claims` (GET "" → `DomainClaimListResponse`), `verify_domain_claim` (POST "/{claim_id}/verify" → `DomainClaimVerifyResponse`), `revoke_domain_claim` (DELETE "/{claim_id}" → 204). `_get_owner_identity` gates every route to `Role.OWNER` (else 403 `ERR_AUTH_FORBIDDEN`); create+verify are per-tenant rate-limited (429 `ERR_RATE_LIMITED` + `Retry-After`).
- CONSUME (frozen) `apps/gateway/src/gateway/domain_capture/api/schemas.py` — exact response shapes: `DomainClaimCreateResponse{claim_id:UUID, domain, status, dns_record_type:"TXT", dns_record_name, dns_record_value, expires_at}` · `DomainClaimListItem{claim_id, domain, status, dns_record_name, dns_record_value, expires_at, verified_at:datetime|None}` wrapped in `DomainClaimListResponse{claims:[…]}` · `DomainClaimVerifyResponse{claim_id, domain, status, verified_at}`. DNS record format is a pure function of the claim: `_dns_record_name(domain)= "_ai-proxy-challenge.{domain}"`, `_dns_record_value(token)= "ai-proxy-domain-verification={token}"`, type always `"TXT"`. NOTE: GET list already returns `dns_record_name`/`dns_record_value` per item → the challenge is renderable from the list alone, no per-claim GET-by-id endpoint exists.
- CONSUME (frozen) `apps/gateway/src/gateway/domain_capture/domain/entities.py:ClaimStatus(StrEnum)` — **the status field carries EXACTLY two values: `PENDING="pending"`, `VERIFIED="verified"`** (DB `CheckConstraint status IN ('pending','verified')` in `infrastructure/orm.py:TenantDomainClaimRow`). There is NO persisted `failed`/`revoked`/`expired` status. → the chip's third/fourth states are UI-DERIVED, not read from `status` (see Issues #1).
- CONSUME (frozen) `apps/gateway/src/gateway/core/error_catalog.py` — the reject vocabulary the console must render: `ERR_DOMAIN_INVALID`(400) · `ERR_DOMAIN_ALREADY_VERIFIED`(409) · `ERR_DOMAIN_VERIFICATION_FAILED`(422, DNS record present-but-mismatched/absent → claim STAYS pending) · `ERR_DNS_LOOKUP_FAILED`(503, resolver error/timeout → retry, claim untouched) · `ERR_DOMAIN_CLAIM_EXPIRED`(410/422) · `ERR_DOMAIN_CLAIM_NOT_FOUND`(404) · `ERR_AUTH_FORBIDDEN`(403 non-owner) · `ERR_RATE_LIMITED`(429).
- BUILD (dashboard, new) `apps/dashboard/components/settings/DomainClaimsSettings.tsx` — the console tab body (mirror `SamlSettings.tsx` conventions: `"use client"`, `@tanstack/react-query` `useQuery`/`useMutation`, `bffGet/bffPost/bffDelete`, `ErrorState`/`Loading`, owner-403 → `ErrorState` not a form).
- BUILD (new) `apps/dashboard/components/settings/DomainStatusSeal.tsx` — the signature status chip (see Anchors + Honors); a small `components/settings/DnsChallenge.tsx` for the copyable record + Verify-now may be co-located or split at build's discretion.
- EDIT (dashboard) `apps/dashboard/components/settings/SettingsPage.tsx` — add a `"domains"` entry to `TAB_VALUES` + a `<TabsTrigger value="domains">Domains</TabsTrigger>` + `<TabsContent value="domains"><DomainClaimsSettings/></TabsContent>` (URL-controlled `?tab=domains`, the frozen compliance-report-center tab pattern — a purely additive tab; default stays "cache").
- BUILD (new) the "you joined {tenant}" confirmation surface consuming task-2's shipped signal — `?joined=1` on the SSO/landing URL (`apps/dashboard/app/auth/oidc/callback` + new `saml/callback` forward it) and the un-dropped `joined_existing_tenant` from `apps/dashboard/app/api/auth/signup/route.ts` → rendered by a landing callout (placement is Axis 4, human decides).

Context (working folder): The gateway API is 100% built + FROZEN (domain-capture TASK §3 v1) — task-3 writes ZERO gateway code. The authenticated BFF proxy `apps/dashboard/app/api/gw/[...path]/route.ts` already attaches the session bearer server-side and forwards to the gateway, so the console needs NO new BFF route — it calls `/admin/domain-claims*` straight through `@/lib/bff-client` (`bffGet<T>(path)`, `bffPost<T>(path,body)`, `bffDelete(path)`). Existing owner-scoped settings tabs (`OidcSettings`, `SamlSettings`, `ScimSettings`) are the exact reuse templates. Task-2 (`domain-auto-assign-login`, phase: done) already ships the join SIGNAL end-to-end (`?joined=1` redirect param + `joined_existing_tenant` BFF field + a MINIMAL SignupForm message) and explicitly hands the POLISHED confirmation UI to this task (task-2 §0 Risk #7). Design tokens: `apps/dashboard/app/globals.css` "Airier" — azure `--primary:#2f6df0`, cool graphite neutrals, Geist/Geist Mono (`--font-sans`/`--font-mono`), semantic `--success #16955f/--success-text #0f7a4d`, `--warning #bd8410/--warning-foreground #8a5a0a`, `--destructive #cc3d37/--destructive-text #c0332e`, plus dark counterparts + `--accent-soft-foreground` (AA fix). WCAG AA is the standing bar.

Honors (patterns / conventions):
- **Signature chip REUSES the immutability-marker idiom** — `apps/dashboard/components/invoices/InvoiceStatusSeal.tsx` (and its clone `components/compliance/BundleEvidenceSeal.tsx`): a `Badge variant="success"` + `<Lock className="size-3">` + visible label + an `sr-only` assertion of finality. `components/ui/badge.tsx` semantic variants (`success`/`warning`/`destructive`) are `bg-*/10 text-*-text font-mono tabular-nums` — the VERIFIED chip = the "issued/sealed" branch verbatim (lock + success + sr-only "ownership confirmed"); pending = `warning`, expired = `destructive`. NOT a generic badge.
- Reuse `@/components/ui` primitives (`Badge`, `Button`, `Input`, `Loading`, `ErrorState`, `PageHeader`, `Tabs*`, `data-table`/`table`) + `@/lib/bff-client` + `@tanstack/react-query`; owner-403 renders `ErrorState`, never a broken form (SamlSettings precedent). data-slot marker + refute-read-before-gate per the ui-restyle-recipe. Style ONLY through tokens (never hardcode hex; honor the tailwind-v4 font-token-collision gotcha — verify computed style on a live render).
- Milestone shared decisions honored: a verified `tenant_domain_claims` row is the ONE source of truth (the console never invents a second surface); the console is pure pass-through over the frozen API — never weakens the owner gate or the DNS-TXT verification (both server-side); design-for-failure on every BFF call (timeout/loading/error states, retryable `ERR_DNS_LOOKUP_FAILED` vs terminal `ERR_DOMAIN_VERIFICATION_FAILED`).

Seams consulted: none new — the BFF proxy seam (`app/api/gw/[...path]`) and the settings-tab URL-control seam (`SettingsPage.tsx`, compliance-report-center §3 pattern) are existing, cited above.

Anchors the contract cites: `domain_claims_router` (4 routes + paths + status codes) · `DomainClaimCreateResponse` · `DomainClaimListItem`/`DomainClaimListResponse` · `DomainClaimVerifyResponse` · `_dns_record_name`/`_dns_record_value` · `ClaimStatus{pending,verified}` · the error codes (`ERR_DOMAIN_INVALID`, `ERR_DOMAIN_ALREADY_VERIFIED`, `ERR_DOMAIN_VERIFICATION_FAILED`, `ERR_DNS_LOOKUP_FAILED`, `ERR_DOMAIN_CLAIM_EXPIRED`, `ERR_DOMAIN_CLAIM_NOT_FOUND`, `ERR_AUTH_FORBIDDEN`, `ERR_RATE_LIMITED`) · `InvoiceStatusSeal.tsx` (chip idiom) · `SettingsPage.tsx:TAB_VALUES` · `bffGet/bffPost/bffDelete` · task-2's `?joined=1` + `joined_existing_tenant`.

Issues/Risks (→ feed §1):
1. **CHIP STATE ≠ status enum (the load-bearing design finding).** The milestone mandates a "pending → verified → failed" chip, but `status` persists ONLY `pending|verified`. So the chip must be DERIVED, not a 1:1 map of `status`: **verified** = `status=="verified"` (the sealed marker); **pending** = `status=="pending"` and not past `expires_at`; **expired** = `status=="pending"` and `expires_at < now` (client-derived — the API also enforces it via `ERR_DOMAIN_CLAIM_EXPIRED` on verify); **failed** is NOT durable — a failed `verify` returns `ERR_DOMAIN_VERIFICATION_FAILED` and leaves the claim `pending`, so "failed" is the EPHEMERAL result of the last Verify-now attempt (inline alert), not a stored chip state. **revoke** is a hard DELETE (no tombstone) → the row vanishes on refetch. §1 must decide how (and whether) to persist "last-attempt-failed" across a reload (it can't from the API alone — it's local/session UI state).
2. **No per-claim GET-by-id.** The challenge (record name/value) is only available from `DomainClaimListItem` in the list response (and from the create response). The console renders the DNS challenge inline from the list row — there is no detail-fetch endpoint to lean on. Fine, but the create response is the ONLY place the freshly-minted record appears with certainty right after POST (list refetch also carries it).
3. **`ERR_DNS_LOOKUP_FAILED`(503) vs `ERR_DOMAIN_VERIFICATION_FAILED`(422) are different UX.** 503 = "DNS is flaky/timed out, try again" (retryable, claim untouched); 422 = "record found but doesn't match / not present" (fix the record, then retry). Same button, two distinct inline messages — do not collapse them.
4. **Owner-only surface.** All 4 routes are `Role.OWNER`; a member/admin hitting the tab gets 403 `ERR_AUTH_FORBIDDEN`. The tab must render an `ErrorState` (mirror SamlSettings), and ideally the trigger is hidden/disabled for non-owners — but the SERVER gate is the real defense; the UI must never assume role client-side for security, only for affordance.
5. **`?joined=1` is an advisory UI signal only** (task-2 §6 safety rule) — the confirmation surface must be READ-ONLY celebration; it must not trigger any state change, re-POST, or influence routing. It also fires for SSO logins resolved via the operator-ENV fallback (task-2 §7 open delta) — copy should say "joined {tenant}" generically, not assert "your domain is DNS-verified".
6. **Tenant NAME for the confirmation.** "You joined {Acme}" needs the tenant display name; confirm the landing context already has it (session/tenant context) vs needs a fetch — §1/§3 must name the source. `?joined=1` alone carries no name.
7. **`expires_at` rendering + clock skew.** Expiry is derived by comparing `expires_at` to client `now` — near the boundary the client chip may say "expired" while the API still accepts (or vice-versa). Treat the chip as advisory; the authoritative answer is the verify call's response. Use `tabular-nums`, honor the standing AA bar.

Related intent: MILESTONE.md §"UI/UX in scope? YES" + Exit criteria "An admin can create, verify (via the shown DNS-TXT challenge), and revoke a domain claim entirely from the dashboard" and "A user who joined an existing workspace sees that outcome in the UI"; the signature-element mandate (verification seal reusing the compliance/invoice immutability marker). GLOSSARY: **domain claim** (DNS-TXT-verified assertion a tenant owns an email domain) · **domain auto-join** (MEMBER assignment into the domain-owning tenant on signup/SSO login). Tin's standing UI/UX bar (never bare CRUD+table — [[ui-ux-polish-standing-bar]]). Task-2 §0 Risk #7 explicitly deferred the polished confirmation UI to this task.

Ground SHA: `086b903` (grounded via serena + reads; every cited gateway symbol + dashboard component opened firsthand).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A dashboard domain-claims console (a "Domains" tab in Settings) where a tenant OWNER creates, verifies (via a shown DNS-TXT challenge), and revokes domain claims over the frozen gateway API — plus the polished "you joined {tenant}" onboarding confirmation consuming task-2's signal. Design confirmed by Tin 2026-07-19 (wireframe artifact 655ae92f): honest chip states, tenant name from session context.
Framings weighed: **dashboard console + BFF pass-through over the frozen /admin/domain-claims API, chip states DERIVED from status+expires_at** (chosen) · a durable client-persisted "failed"/"action-needed" state (rejected — Tin chose honest states; inventing state the API doesn't hold risks drift) · fold expired into pending (rejected — hides a closed verification window).
Must:
<must>
  - M1 — a NEW owner-scoped "Domains" tab in `SettingsPage.tsx` (URL-controlled `?tab=domains`, additive to TAB_VALUES; default tab unchanged) lists the tenant's claims from `GET /admin/domain-claims` via `bffGet` — domain, status seal, added/expires, actions.
  - M2 — the status SEAL reuses the `InvoiceStatusSeal` immutability-marker idiom (Badge + Lock + sr-only ownership assertion, mono/tabular), with states DERIVED not 1:1: **Verified** = `status=="verified"` (the sealed branch); **Pending DNS** = `status=="pending"` && not past `expires_at`; **Expired** = `status=="pending"` && `expires_at < now` (client-derived, advisory). Never a generic badge.
  - M3 — "Add domain" → `POST /admin/domain-claims` via `bffPost`; on 201 the DNS-TXT challenge (Type=`TXT`, Name=`_ai-proxy-challenge.{domain}`, Value=`ai-proxy-domain-verification={token}`) renders inline with per-field COPY affordances, sourced from the create/list response (no per-claim GET exists).
  - M4 — "Verify now" → `POST /admin/domain-claims/{id}/verify` via `bffPost`; on success the row's seal flips to Verified; on `ERR_DOMAIN_VERIFICATION_FAILED`(422) an EPHEMERAL inline alert "record absent/mismatched — fix + retry" (claim stays pending); on `ERR_DNS_LOOKUP_FAILED`(503) a DISTINCT retryable alert "DNS lookup failed, try again" (claim untouched). The two are never collapsed.
  - M5 — "Revoke" → `DELETE /admin/domain-claims/{id}` via `bffDelete` behind a confirm affordance; on 204 the row disappears on refetch (hard delete, no tombstone).
  - M6 — a READ-ONLY, dismissible "You joined {tenant} workspace" confirmation callout on the post-login landing, shown when task-2's `?joined=1` (SSO) or `joined_existing_tenant` (signup) is present; the tenant display NAME comes from the already-loaded session/tenant context (no extra fetch — Tin-confirmed). Reuses the seal idiom for the celebration marker.
  - M7 — design-for-failure on every BFF call: explicit loading / empty / error states; a non-owner (403 `ERR_AUTH_FORBIDDEN`) renders an `ErrorState`, never a broken form; `ERR_RATE_LIMITED`(429) surfaces the `Retry-After`; AA contrast + token-only styling + `data-slot` markers (ui-restyle-recipe).
</must>
Reject:
<reject>
  - R1 — a non-owner never sees or mutates claim data -> the tab renders `ErrorState` on 403 `ERR_AUTH_FORBIDDEN`; the claims list/create/verify/revoke controls are not usable. (Client role is affordance-only; the SERVER gate is the defense — the console never assumes role client-side for security.)
  - R2 — a failed Verify-now (422 or 503) NEVER flips the seal to Verified and NEVER deletes/mutates the claim -> the row stays `pending`, only the ephemeral inline alert changes.
  - R3 — the confirmation callout NEVER triggers a state change, re-POST, or routing influence -> it is advisory-only, absent when no `?joined`/`joined_existing_tenant` signal is present; dismiss is local UI only.
  - R4 — the console NEVER invents a second domain→tenant surface and NEVER weakens the owner gate or the DNS-TXT verification (both server-side) -> it is pure pass-through over the frozen gateway API; no gateway file is edited.
</reject>
After:
<after>
  - An owner can claim → see the DNS-TXT record → publish it → Verify now → watch the seal flip to Verified → later revoke, entirely from the dashboard; a domain-joined user lands on a confirmation that names their workspace; a non-owner is cleanly refused; no gateway code changed and no second routing surface exists.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [spec] (M6 tenant-name source) that the post-login landing's session/tenant context ALREADY carries the tenant display name without an extra fetch — lowest confidence because if the current-user/tenant context exposes only an id, M6 needs a fetch (a round-trip Tin's "session context" answer assumed away); if wrong: fall back to a one-shot tenant-name fetch or generic "an existing workspace" copy. Mitigation: §4 pins M6 against a mocked context that carries the name; the build confirms the real context shape at first wiring.
  - [ ] that `DomainClaimListItem` carries `status` + `expires_at` (+ dns fields) sufficient to derive all three chip states and render the challenge without a per-claim GET — GROUNDED TRUE (schemas.py), listed for the record.
  - [ ] that `bffGet/bffPost/bffDelete` through `/api/gw/[...path]` attach the owner session bearer server-side so the console needs no new BFF route — GROUNDED TRUE (bff-client + gw proxy), listed for the record.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: owner opens the Domains tab and sees their claims   # M1, M2
  Given an owner whose tenant has a verified acme.com claim and a pending acme.dev claim
  When they open Settings ?tab=domains
  Then the list shows acme.com with a Verified seal (lock + sr-only ownership) and acme.dev with a Pending DNS seal

Scenario: expired claim renders a derived Expired seal   # M2
  Given a pending claim whose expires_at is in the past
  When the list renders
  Then that row shows an Expired seal (not Pending), derived client-side from status+expires_at

Scenario: create a claim reveals the DNS-TXT challenge   # M3
  Given an owner on the Domains tab
  When they add "team.acme.com" and the gateway returns 201 with the record
  Then the inline challenge shows Type TXT, Name _ai-proxy-challenge.team.acme.com, Value ai-proxy-domain-verification=<token>, each copyable

Scenario: verify now succeeds and the seal flips   # M4
  Given a pending claim whose DNS record is published
  When the owner clicks Verify now and the gateway returns verified
  Then the row's seal becomes Verified

Scenario: verify now fails with a mismatch   # M4, R2
  Given a pending claim whose DNS record is absent/wrong
  When the owner clicks Verify now and the gateway returns ERR_DOMAIN_VERIFICATION_FAILED (422)
  Then an inline "record absent/mismatched" alert shows and the seal stays Pending
  And the claim is not verified, deleted, or otherwise mutated

Scenario: verify now hits a DNS lookup error   # M4
  Given a pending claim
  When Verify now returns ERR_DNS_LOOKUP_FAILED (503)
  Then a DISTINCT retryable "DNS lookup failed, try again" alert shows (not the mismatch copy) and the claim is untouched

Scenario: revoke removes the claim   # M5
  Given a verified acme.com claim
  When the owner confirms Revoke and the gateway returns 204
  Then the row disappears from the list on refetch

Scenario: non-owner is refused   # R1
  Given a MEMBER (not owner) opening ?tab=domains
  When the claims request returns 403 ERR_AUTH_FORBIDDEN
  Then an ErrorState renders and no claim data or mutating control is shown
  And no claim is created, verified, or revoked

Scenario: joined-workspace confirmation on landing   # M6
  Given a user who joined an existing tenant (?joined=1 or joined_existing_tenant) whose session context names the tenant "Acme, Inc."
  When they reach the post-login landing
  Then a read-only, dismissible "You joined the Acme, Inc. workspace" callout appears
  And dismissing it changes no server state

Scenario: no confirmation without the signal   # R3
  Given a normal login with no ?joined signal
  When the landing renders
  Then no joined-workspace callout appears
  And no state change or re-POST occurs
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── DASHBOARD CONSOLE (this task builds; consumes the FROZEN gateway API — no gateway edit) ──

# Settings tab (SettingsPage.tsx — additive)
TAB_VALUES += "domains"    # URL ?tab=domains; owner-scoped; default tab unchanged

# DomainClaimsSettings.tsx  (new, "use client", tanstack-query + bff-client)
GET  /admin/domain-claims                 via bffGet    -> { claims: DomainClaimListItem[] }   # list
POST /admin/domain-claims  {domain}        via bffPost   -> 201 DomainClaimCreateResponse        # create → challenge
POST /admin/domain-claims/{id}/verify      via bffPost   -> DomainClaimVerifyResponse            # verify now
DELETE /admin/domain-claims/{id}           via bffDelete -> 204                                  # revoke
  # error render map (frozen error_catalog codes → UI):
  #   403 ERR_AUTH_FORBIDDEN          -> ErrorState (whole tab; R1)
  #   422 ERR_DOMAIN_VERIFICATION_FAILED -> ephemeral inline alert "record absent/mismatched, fix+retry"; claim stays pending (R2)
  #   503 ERR_DNS_LOOKUP_FAILED       -> DISTINCT ephemeral retryable alert "DNS lookup failed, try again"; claim untouched
  #   409 ERR_DOMAIN_ALREADY_VERIFIED / 400 ERR_DOMAIN_INVALID / 410|422 ERR_DOMAIN_CLAIM_EXPIRED / 404 ERR_DOMAIN_CLAIM_NOT_FOUND / 429 ERR_RATE_LIMITED(+Retry-After) -> inline field/row message

# Chip state (DomainStatusSeal.tsx, new — reuses InvoiceStatusSeal idiom, NOT 1:1 with status):
sealState(claim) =
  claim.status == "verified"                         -> "verified"   # sealed/immutable branch (Lock + sr-only ownership)
  claim.status == "pending" && claim.expires_at > now -> "pending"    # warning
  claim.status == "pending" && claim.expires_at <= now-> "expired"    # destructive, client-DERIVED, advisory
  # a failed verify is EPHEMERAL (claim stays pending) — an inline alert, never a seal state; revoke = row vanishes

# Onboarding confirmation (new landing surface):
landing shows JoinedWorkspaceCallout  IFF  (?joined=1  OR  signup joined_existing_tenant)   # task-2 signals
  tenantName <- session/tenant context (already loaded; NO extra fetch — Tin-confirmed)
  read-only · dismissible (local UI) · never mutates server state (R3)

Schema: NONE. Zero DB or gateway change. Reads/writes go through the existing authenticated BFF proxy
`/api/gw/[...path]` (attaches the owner session bearer server-side) against the frozen /admin/domain-claims
routes. All authorization + DNS-TXT verification stays server-side (R4).
```

Glossary deltas: none new — `domain claim` and `domain auto-join` are already defined (milestone GLOSSARY); this task adds the CONSOLE + confirmation UI over them, no new domain term.
Least-sure flag surfaced at freeze: [spec] (M6 tenant-name source) — that the post-login landing's session/tenant context already carries the tenant DISPLAY NAME without an extra fetch. Tin answered "session context" at design confirmation, but if the loaded context exposes only a tenant id, M6 needs a one-shot name fetch (a round-trip the answer assumed away). Mitigation: §4 pins M6 against a mocked context that carries the name; the build confirms the real context shape at first wiring and falls back to a fetch or generic "an existing workspace" copy if absent — a build-local adjustment, not a contract change.
Status: FROZEN @ v1 — approved by Tin Dang 2026-07-19
Reported: yes — freeze report (banner/ARC/SHAPE/FLAGS) rendered before this froze

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the new console + seal + callout components.
Plan (one test per scenario, asserting behavior not internals — dashboard vitest + Testing Library, MSW-mocked BFF):
<test_plan>
  - test_owner_sees_claims_with_seals: mock GET returns verified acme.com + pending acme.dev / render Domains tab / assert a Verified seal (getByText + lock/sr-only "ownership") and a Pending DNS seal · M1,M2
  - test_expired_seal_derived: mock a pending claim with expires_at in the past / render / assert an Expired seal, not Pending · M2
  - test_create_reveals_dns_challenge: mock POST 201 with the record / add team.acme.com / assert Name `_ai-proxy-challenge.team.acme.com` + Value `ai-proxy-domain-verification=<token>` shown with copy affordances · M3
  - test_verify_now_success_flips_seal: pending claim / click Verify now, mock verified / assert the seal becomes Verified · M4
  - test_verify_mismatch_alert_no_flip: click Verify now, mock 422 ERR_DOMAIN_VERIFICATION_FAILED / assert mismatch inline alert AND seal stays Pending AND no delete/verify mutation · M4,R2
  - test_verify_dns_lookup_distinct_alert: click Verify now, mock 503 ERR_DNS_LOOKUP_FAILED / assert a DISTINCT retryable alert (different copy from the 422) · M4
  - test_revoke_removes_row: confirm Revoke, mock 204 + refetch without the row / assert the row is gone · M5
  - test_non_owner_error_state: mock 403 ERR_AUTH_FORBIDDEN / render tab / assert ErrorState and NO claims list/create control · R1
  - test_joined_callout_named: session context names tenant "Acme, Inc." + ?joined=1 / render landing / assert read-only "You joined the Acme, Inc. workspace" callout; dismiss changes no server state · M6
  - test_no_signal_no_callout: no ?joined signal / render landing / assert no joined callout, no fetch/POST · R3
</test_plan>

GATEWAY (CR 2026-07-19 — added after Tin chose "add tenant_name to /me"; pytest, real Postgres):
  - test_me_returns_tenant_name: signup+login a tenant "Acme, Inc." / GET /me / assert response carries tenant_name == "Acme, Inc." (+ existing user_id/tenant_id/email/role unchanged) · covers: M6 gateway half
Tests live in: `apps/dashboard/tests/domain-claims-console.test.tsx` · `apps/dashboard/tests/joined-workspace-callout.test.tsx` · `apps/gateway/tests/domain_claims_console_me/test_me_tenant_name.py` · MUST run red (missing field/components) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/settings/` · `apps/dashboard/components/onboarding/` · `apps/dashboard/app/(app)/app/keys/page.tsx` · `apps/dashboard/tests/` · `apps/dashboard/tests-bff/tenant-settings.test.tsx` · `apps/dashboard/app/api/auth/me/route.ts` · `apps/dashboard/lib/hooks/use-current-user.ts` · `apps/gateway/src/gateway/tenants/api/schemas.py` · `apps/gateway/src/gateway/tenants/api/router.py` · `apps/gateway/tests/domain_claims_console_me/` · `apps/gateway/tests/tenants/test_tenant_identity.py`
(Prose note — NO leading slash, project-root-relative directory tokens [scope anchor is captured at the tests→build crossing; see the scope-anchor lesson]. settings/ covers NEW DomainClaimsSettings.tsx + DomainStatusSeal.tsx + optional DnsChallenge.tsx AND the additive SettingsPage.tsx tab edit; onboarding/ is a NEW dir for JoinedWorkspaceCallout.tsx; the keys page is the authed landing that mounts the callout. NO gateway file, NO new BFF route — the frozen /admin/domain-claims API is consumed via the existing /api/gw proxy + bff-client.
CROSS-TASK RECONCILIATION (sanctioned, additive-ONLY): `tests-bff/tenant-settings.test.tsx:551` asserts the EXACT ordered tab list via `toEqual([...8 tabs])`; the new "Domains" tab (M1) requires appending `"Domains"` to that array. INTENT-PRESERVING (still asserts the complete ordered tab set, now 9) — NOT a weakening. This is the ONLY permitted edit to that frozen file; any OTHER assertion change is forbidden. Surfaced at the verify gate with the exact diff for Tin.)
CROSS-TASK RECONCILIATION #2 (sanctioned, additive-ONLY — surfaced by the verify refute-read, §6 line "no frozen /me or tenants suite regressed"): `apps/gateway/tests/tenants/test_tenant_identity.py:134` asserts the EXACT /me body via `body == {user_id, tenant_id, email, role}`. The Tin-approved M6 CR additively adds `tenant_name` to /me, so this over-specified consumer needs `"tenant_name": "Acme"` (ADA's own tenant name) appended to the expected dict. INTENT-PRESERVING — still a COMPLETE exact-equality that fails on any UNEXPECTED field; the new expected key is ADDED, nothing loosened. This is the ONLY permitted edit to that frozen file; any OTHER assertion change is forbidden. It was the SOLE exact-shape /me consumer in the gateway suite (superadmin_login reads keys individually; scim asserts only status; member-invite/users assert non-/me endpoints) — swept before healing so no second casualty remains. Surfaced at the verify gate with the exact diff for Tin.)
Strategy (ordered batches):
  1. DomainStatusSeal.tsx — the signature chip, reusing InvoiceStatusSeal's Badge+Lock+sr-only idiom; pure function `sealState(claim)` deriving verified/pending/expired from status+expires_at. Unit-testable in isolation first.
  2. DomainClaimsSettings.tsx — the console body mirroring SamlSettings.tsx (tanstack-query useQuery/useMutation, bffGet/bffPost/bffDelete, Loading/ErrorState); list + create(challenge inline w/ copy) + verify-now(distinct 422 vs 503 alerts) + revoke(confirm). Owner-403 → ErrorState.
  3. SettingsPage.tsx — additive "domains" tab (TAB_VALUES + TabsTrigger + TabsContent), default tab untouched.
  4. onboarding/JoinedWorkspaceCallout.tsx + mount on app/keys/page.tsx — read-only, dismissible, tenant name from session/tenant context, shown IFF ?joined=1 / joined_existing_tenant.
Persona (required): frontend-engineer
Spawn isolation (default): shared tree — sequential single-task pipeline, I review the diff between build and verify (no parallel writer).
Known-problem fixes:
  - tailwind-v4 font-token-collision → style ONLY through tokens; verify computed style on a live render, never trust a green build alone.
  - a failed Verify-now must NEVER optimistically flip the seal → mutate only on the success response; keep the 422/503 result ephemeral (claim stays pending).
  - client role must never gate SECURITY (only affordance) → the server 403 is the defense; render ErrorState, don't hide the truth.
  - the confirmation callout must be inert → no eff/mutation on mount beyond reading context; dismiss is local state only.
Strategy actually used: as planned, cross-component after the M6 CR. Gateway (orchestrator hand-completed — the fable build skipped it): additive `MeResponse.tenant_name` + `me()` loads it via `get_tenant_by_id(session, identity.tenant_id).name`, fail-safe "" if absent (never 500). Dashboard (fable): DomainStatusSeal (InvoiceStatusSeal idiom, derived states), DomainClaimsSettings (SamlSettings pattern, distinct 422/503 alerts, owner-403→ErrorState), additive Domains tab, JoinedWorkspaceCallout reading tenant_name from useCurrentUser→/api/auth/me relay. TWO sanctioned additive test edits: (a) appended "Domains" to tenant-settings.test.tsx's ordered tab `toEqual`; (b) verify refute-read caught a cross-task-drift casualty the build's /me-adjacent-only run missed — `tests/tenants/test_tenant_identity.py:134`'s exact-equality /me body assertion — reconciled additively (append `"tenant_name": "Acme"`, exact-equality preserved) after sweeping the whole gateway suite to confirm it was the SOLE exact-shape /me consumer. Returned build→verify via `add.py phase build` (refreshes the §5 scope anchor for the added test token; NOT `heal` — this is honest collateral drift from an approved shape change, not a gamed green, so it must not burn a monotonic cheat attempt). Evidence: gw 59 passed across /me-adjacent suites + full tests/tenants/ green post-reconcile; dash 94 passed (target+sanctioned+regression); ruff/pyright/tsc clean.
Safety rule (feature-specific): the console is pure pass-through — it never weakens the owner gate or DNS-TXT verification (both server-side) and never introduces a second domain→tenant surface.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — gateway 19 (tests/tenants/ 11 + domain_claims_console_me/ 1 + superadmin_login/ 7) + dashboard 94; the attack-4 regression is healed & re-green
- [x] coverage did not decrease — additive-only (new field + new component files + additive tab); the sole existing test touched (`test_tenant_identity.py:134`) was WIDENED, not narrowed
- [x] no test or contract was altered during build — §3 frozen intact (tripwire re-snapshotted at the `phase build` return, no divergence); the two frozen-test touches are DECLARED sanctioned ADDITIVE reconciliations (see §5), not build tampering
- [x] the green was EARNED, not gamed — adversarial refute-read by opus verifier a8705d715a291def4: attacks 1/2/3/5/6/7 CONFIRMED-SAFE, attack 4 (cross-task drift) DEFECT→healed→re-verified CLEAR; earned-green EARNED, confidence 0.93–0.97
- [x] concurrency / timing of the risky operation is safe — the gateway `/me` addition is a single read-only `get_tenant_by_id` on the caller's OWN session-derived tenant_id (no shared mutable state, no write); console mutations are per-tenant server-rate-limited (frozen task-1)
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new gateway dep; dashboard uses only allow-listed `@tanstack/react-query` + existing `bff-client`; DNS-TXT token is server-issued, never client-crafted; owner-403 defense stays server-side
- [ ] a person reviewed and approved the change
- [x] layering & dependencies follow CONVENTIONS.md — gateway change stays in the tenants api layer (schema + router → repository read); dashboard console mirrors SamlSettings (client component → bff-client → frozen gateway API), no new BFF route

### Component green bars (cross-component after CR: gateway + dashboard)
- [x] DASHBOARD — `vitest` green on `apps/dashboard/tests/domain-claims-console.test.tsx` + `joined-workspace-callout.test.tsx` + the frozen `tenant-settings.test.tsx` (additive tab reconcile) — 94 passed across target+sanctioned+regression suites; `tsc --noEmit` exit 0
- [x] GATEWAY — `pytest` green on `apps/gateway/tests/domain_claims_console_me/` (the additive `MeResponse.tenant_name`) + `tests/tenants/` (the healed frozen /me consumer) + `tests/superadmin_login/` — 19 passed, NO frozen /me or tenants suite red

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] The Domains tab lists claims with a Verified SEAL (lock + sr-only ownership, the InvoiceStatusSeal idiom — not a generic badge), Pending DNS, and a client-DERIVED Expired seal — confirmed by `domain-claims-console.test.tsx` (seal-state cases distinguish verified/pending/expired) + DomainStatusSeal.tsx reusing the Badge+Lock+sr-only idiom; UDD wireframe confirmed by Tin (artifact 655ae92f)
- [x] Add-domain reveals the exact DNS-TXT record (`_ai-proxy-challenge.{domain}` / `ai-proxy-domain-verification=<token>`) with working copy affordances — confirmed by the create test rendering the challenge from the list response (no per-id GET)
- [x] Verify-now flips the seal on success; a 422 shows a mismatch alert and a 503 shows a DISTINCT retryable alert, neither flipping the seal — confirmed by the three verify tests (R2: verifier attack 3 CONFIRMED-SAFE — seal mutates only on success response, 422/503 stay ephemeral, claim stays pending)
- [x] A non-owner sees an ErrorState with no claim data/controls; the joined-workspace callout names the tenant from session context and is inert — confirmed by the R1 owner-403→ErrorState test + the M6 `tenant_name` end-to-end (gateway /me → /api/auth/me relay → useCurrentUser → callout) + callout inert (attack 7 CONFIRMED-SAFE: no mutation on mount/dismiss, dismiss is local state)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `MeResponse.tenant_name` set by `me()` (router.py:159); dashboard chain `get_tenant_by_id`→MeResponse→`/api/auth/me` relay (route.ts:102)→`use-current-user` (tenant_name:25)→`JoinedWorkspaceCallout` (reads at :48); DomainClaimsSettings mounted via SettingsPage additive Domains tab; DomainStatusSeal consumed by DomainClaimsSettings
- [x] DEAD-CODE (code) — no orphaned symbol: verifier attack 2 earned-green read found no vacuous/stubbed assertions; every new component is mounted and exercised by a test
- [x] SEMANTIC (prose / non-code) — TASK.md §5 reconciliation notes read in full; both frozen-test touches confirmed ADDITIVE (tab append + `tenant_name` key add), neither weakens an assertion

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — the frozen `/admin/domain-claims` router + schemas + `ClaimStatus` all resolve (consumed unchanged); `MeResponse`/`me()` resolve at the edited anchors; verifier re-ran both suites against the current tree
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — none moved; the only tree changes are this task's own additive edits

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: agent a8705d715a291def4 (opus, add-verify) · adversarially checked: overfit/vacuous asserts across the 10 dashboard + 1 gateway tests; seal-state test distinguishes verified/pending/expired (not a constant); verify-422 proves no seal-flip + no extra mutation (R2); no-signal callout proves no fetch/POST (R3); additive-not-weakening on BOTH sanctioned frozen-test edits; whole-gateway-suite sweep confirmed `test_tenant_identity.py:134` was the SOLE exact-shape /me consumer (no second casualty)

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: agent a8705d715a291def4 (opus, add-verify)
1. Security: CLEAR — /me exposes only the caller's OWN tenant name (get_tenant_by_id(identity.tenant_id), token-derived; no cross-tenant read); fail-safe "" never 500s; console owner-403 defense stays server-side (attacks 4-authz/5 CONFIRMED-SAFE)
2. Concurrency: CLEAR — read-only single-SELECT on the request's own session; no shared mutable state
3. Architecture: CLEAR — additive field + pass-through console; no second domain→tenant surface, no new BFF route; one initial cross-consumer-reconciliation miss (attack 4) caught by verify and healed additively
Verdict: PASS
Residue: none — the sole defect (attack 4 cross-task drift) is healed & independently re-verified CLEAR
Binding: advisory — architecture (sensitivity=architecture; conservative autonomy holds the gate for the human)

### GATE RECORD
Reported: yes — the gate report (banner/ARC) rendered to Tin before this outcome recorded
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-19

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose **dashboard console + BFF pass-through over the frozen /admin/domain-claims API, chip states DERIVED from status+expires_at**; rejected a durable client-persisted "failed"/"action-needed" state (rejected — Tin chose honest states; inventing state the API doesn't hold risks drift) · fold expired into pending (rejected — hides a closed verification window).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang 2026-07-19)
- [AI] build — strategy used: as planned, cross-component after the M6 CR. Gateway (orchestrator hand-completed — the fable build skipped it): additive `MeResponse.tenant_name` + `me()` loads it via `get_tenant_by_id(session, identity.tenant_id).name`, fail-safe "" if absent (never 500). Dashboard (fable): DomainStatusSeal (InvoiceStatusSeal idiom, derived states), DomainClaimsSettings (SamlSettings pattern, distinct 422/503 alerts, owner-403→ErrorState), additive Domains tab, JoinedWorkspaceCallout reading tenant_name from useCurrentUser→/api/auth/me relay. TWO sanctioned additive test edits: (a) appended "Domains" to tenant-settings.test.tsx's ordered tab `toEqual`; (b) verify refute-read caught a cross-task-drift casualty the build's /me-adjacent-only run missed — `tests/tenants/test_tenant_identity.py:134`'s exact-equality /me body assertion — reconciled additively (append `"tenant_name": "Acme"`, exact-equality preserved) after sweeping the whole gateway suite to confirm it was the SOLE exact-shape /me consumer. Returned build→verify via `add.py phase build` (refreshes the §5 scope anchor for the added test token; NOT `heal` — this is honest collateral drift from an approved shape change, not a gamed green, so it must not burn a monotonic cheat attempt). Evidence: gw 59 passed across /me-adjacent suites + full tests/tenants/ green post-reconcile; dash 94 passed (target+sanctioned+regression); ruff/pyright/tsc clean.
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

