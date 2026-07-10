# TASK: Admin settings UIs: SCIM token mgmt, SAML IdP config, retention/ZDR editor

slug: enterprise-identity-admin-ui · created: 2026-07-10 · stage: production
milestone: enterprise-identity-compliance
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->
<!-- ORCHESTRATOR NOTE: this task was added by the orchestrator (AUTO-MODE) to cover the milestone's
     "UI/UX in scope" line, which named no owning task in the original breadth-first decomposition.
     scim-provisioning §1 ⚠ (l.103) and saml-sso independently both flagged the same gap. Needs Tin's
     explicit confirmation at freeze that this task itself (not just its contract) is wanted. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/components/settings/SettingsPage.tsx` — the `/settings` tabbed hub (`Tabs defaultValue="cache"`, `TabsList`/`TabsTrigger`/`TabsContent` from `@/components/ui`). Currently 4 tabs: Cache/Guardrails/SSO/Provider Keys. This task ADDS three tabs (SCIM · SAML SSO · Retention & ZDR) to the SAME `TabsList`/`TabsContent` block — no new page archetype, no new route. `TabsContent` returns null when inactive (existing convention: each tab's `useQuery` only fires on activation) — every new tab must preserve this.
- `apps/dashboard/app/(app)/app/settings/page.tsx` — the route shell (`<SettingsPage />`), untouched.
- `apps/dashboard/components/settings/OidcSettings.tsx` (whole file) — the config-form pattern to mirror for the new SAML tab: owner-only GET/PUT, React "adjust state during render" re-seed guard (`seededData` ref-equality check, not a `useEffect`) driven off the TanStack Query data reference, inline `role="alert"` error surfacing, 404-vs-403-vs-other GET-error branching (`is404`/`is403`/`isOtherError`). SAML's admin config (`GET`/`PUT /admin/saml`, frozen §3 below) is shape-compatible with this exact pattern, with ONE deliberate divergence: `idp_x509_cert` is public key material (frozen contract note: "NOT a secret... returned in full on GET, no `<stored>` placeholder") — so unlike `client_secret`'s write-only/never-prefilled handling, the SAML form MAY prefill the cert field from GET.
- `apps/dashboard/components/settings/GuardrailSettings.tsx` (whole file) — the `Switch` + `<select>` fieldset, partial-merge PUT pattern to mirror for the new Retention & ZDR tab (`GET`/`PUT /admin/retention-policy`, frozen §3 below): a boolean toggle (`zdr_enabled`, mirrors `piEnabled`) plus a bounded numeric field (`window_days`, new shape — no direct precedent in this file, closest is the regex-pattern-row `Input` styling) and the same `seededData` re-seed guard.
- `apps/dashboard/components/keys/CreateKeyDialog.tsx` (whole file) — the create-credential modal to mirror for "Create SCIM token": `useFocusTrap`, Zod-validated `name` field (`CreateKeySchema`, 1-120 chars — the SCIM contract's `POST /admin/scim/tokens` body is `{ name: str }`, byte-identical shape), inline 422-vs-other error branching, `isSubmitting` disable-during-mutation.
- `apps/dashboard/components/keys/PlaintextKeyBanner.tsx` (whole file) — the reveal-once secret display to reuse VERBATIM (same component, new caller) for both SCIM token CREATE and ROTATE responses: `role="alert"`, `<code>` leaf only, copy-to-clipboard, parent-owned dismiss-clears-state contract ("plaintext key MUST be cleared from state when dismissed").
- `apps/dashboard/components/keys/KeyRow.tsx` + `apps/dashboard/components/keys/KeysPage.tsx` — the list+revoke table-row pattern to mirror for the SCIM token list (`GET /admin/scim/tokens`): `Badge variant="destructive"` for a revoked row, `isPendingRevoke` hides the action button mid-confirmation, `TableRow`/`TableCell` from `@/components/ui`.
- `apps/dashboard/components/teams/ConfirmDialog.tsx` (whole file) — the destructive-action confirmation to reuse for TWO distinct actions: (a) SCIM token revoke/rotate-supersede, (b) enabling ZDR (irreversible: frozen tenant-retention-zdr §3 scenario "Disabling ZDR does not retroactively restore purged rows" — a purge is a real DELETE, not reversible). `useFocusTrap`, `role="dialog"`, inline error-stays-open-on-failure.
- `apps/dashboard/lib/bff-client.ts:bffGet/bffPost/bffPut/bffPatch/bffDelete/BffError` — the typed fetch wrappers every settings tab calls; `BffError.status`/`.problem.title` is the existing error-shape contract every tab's inline `role="alert"` reads from.
- `apps/dashboard/components/ui/index.ts` (barrel) — `Tabs`/`TabsList`/`TabsTrigger`/`TabsContent`, `Switch`, `Button`, `Input`, `Loading`, `ErrorState`, `Badge`, `Table`/`TableRow`/`TableCell`, `PageHeader` — the ONLY primitives this task may draw from; no new visual pattern introduced.
- `apps/dashboard/tests-bff/tenant-settings.test.tsx` (whole file, 411 lines) — the EXISTING test file covering `SettingsPage`'s 4 shipped tabs: `describe("SettingsPage — <tab> tab")` blocks, a shared `renderPage()`/BFF-mock harness (implied by the per-`describe` GET/PUT mock setups), and a closing `describe("SettingsPage — axe + nav")` with `test_settings_axe_clean` (jest-axe) + `test_nav_exposes_settings`. This task's tests extend THIS file (new `describe` blocks per new tab) rather than a new file — one settings surface, one test file, matching the existing 1:1 component-to-describe-block convention.

Context (working folder): `.add/milestones/enterprise-identity-compliance/MILESTONE.md` ("UI/UX in scope" line, l.16: "admin settings surfaces only — SCIM token management, SAML IdP config, domain verification, retention policy editor... extend the existing dashboard settings IA and Aurora components; no new page archetype. WCAG 2.2 AA floor.") — domain verification is named in that line but owned by the sibling `domain-capture` task (still `phase: ground`, blank template as of this Ground pass — confirmed by reading it), NOT this task; `scim-provisioning/TASK.md` §1 ⚠ (l.103, "no sibling task in MILESTONE.md's task list owns a dashboard SCIM-token-management SCREEN... to be either folded into this task's own follow-up delta or a new sibling task, not silently built here") is the exact gap this task closes.

Honors (patterns / conventions): the "state pattern" convention documented at `apps/dashboard/components/ui/states.tsx:5-12` (Loading/Empty/ErrorState/Success rendered identically across every surface, copy always caller-supplied); the "adjust state during render" re-seed guard (no `setState` inside a `useEffect`) used identically in `OidcSettings.tsx`/`GuardrailSettings.tsx`; the anti-silent-failure standing rule (every `BffError` surfaced inline via `role="alert" aria-live="polite"`, never swallowed); WCAG 2.2 AA per the `ui-designer` persona's Default Requirement (contrast ≥4.5:1 body / ≥3:1 large text, visible `focus-visible`, ≥44px hit targets, correct landmark order — computed, not eyeballed) and Critical Rule ("reuse before invent" — flag any new visual pattern, cite what it replaces).

Anchors the contract cites: `SettingsPage` (extend), `OidcSettings` (SAML tab pattern), `GuardrailSettings` (Retention/ZDR tab pattern), `CreateKeyDialog`+`PlaintextKeyBanner`+`KeyRow` (SCIM tab pattern), `ConfirmDialog` (shared destructive-confirm), `bff-client.ts` (`bffGet`/`bffPost`/`bffPut`/`bffDelete`), `tenant-settings.test.tsx` (test-extension target).

Issues/Risks (→ feed §1):
- **All three consumed contracts are already FROZEN @ v1** (scim-provisioning Part A, saml-sso Part B, tenant-retention-zdr) — this is a LOW-risk consumption (the shapes are fixed, not co-designed live), but this task's own contract must match them exactly, field-for-field; any mismatch is a build-time bug, not a design ambiguity.
- **This is the "AUTO-MODE gap-fill" task named in the orchestrator's dispatch note** — no milestone Task row named it originally; both `scim-provisioning` and `saml-sso` independently flagged the same missing-owner gap in their own §1 assumptions. Surfaced at freeze, not silently assumed wanted.
- **SCIM token has no "rename"/"view name later" GET-list secret exposure** (frozen `GET /admin/scim/tokens` returns `{id, name, created_at, revoked_at}` — never `token_hash`, never the plaintext) — the UI must never attempt to re-display a token after its one-time reveal window closes; this is a hard floor from the frozen contract, not a UI choice.
- **Retention window ↔ ZDR interaction**: the frozen `tenant-retention-zdr` contract states ZDR always wins over `window_days` ("independent of, and subordinate to, ZDR"). The editor must show `window_days` as informational-but-inert once `zdr_enabled=true` (not hide it — GET always returns both), matching the contract's `effective_window_days` computed map. A UI-only judgment call, not covered by the backend contract's field shapes — flagged in §1 assumptions.
- **`saml_provider_configs.idp_x509_cert` is TEXT/PEM, potentially large** (a multi-line PEM block) — `OidcSettings.tsx`'s single-line `Input type="url"` styling does not fit; the SAML form needs a `Textarea` (already in the `ui` barrel: `apps/dashboard/components/ui/textarea.tsx`) for the cert field — a cited, deliberate substitution, not a new pattern (existing primitive, different field type).
- **Domain-verification UI boundary**: the SAML tab's frozen `email_domains: list[str]` field (same shape as OIDC's) is admin-editable here (mirrors OIDC exactly — the SAML contract does not gate `email_domains` behind a verification proof at the backend level yet); the sibling `domain-capture` task owns adding actual DNS-TXT verification UI/proof-of-ownership on top of this same field later. This task's SAML tab does NOT invent a verification affordance — flagged explicitly so it isn't silently built twice.

Related intent: `.add/PROJECT.md` UI/UX foundation (Aurora design system, `apps/dashboard/app/globals.css` token layer — never invented ad hoc); the `ui-ux-polish-standing-bar` standing preference (user-facing features need designed, polished UI/UX as a first-class deliverable, not bare CRUD); MILESTONE.md goal ("An enterprise tenant can provision users via SCIM, sign in via SAML..., set its own retention policy including a Zero-Data-Retention mode") — this task is the admin-facing surface for 3 of the milestone's 5 exit criteria (SCIM, SAML config half, retention/ZDR).

Ground SHA: `443a33a` (branch `chore/add-housekeeping-clusters`) — every symbol above cited as `path:symbol`; any bare line number is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Admin settings UI for SCIM token management, SAML IdP config, and retention/ZDR policy — three new tabs on the existing `/settings` hub
Framings weighed:
  A. (chosen) Three new `TabsTrigger`/`TabsContent` pairs on the EXISTING `SettingsPage` tab bar (SCIM · SAML SSO · Retention & ZDR), each a self-contained component file mirroring an existing sibling tab's data-fetch/mutate shape 1:1. No new route, no new page archetype, no new IA pattern.
  B. A separate "/settings/enterprise" sub-route with its own nav entry — rejected: the milestone scope line is explicit ("extend the existing dashboard settings IA... no new page archetype"); a second settings surface would fragment tenant admin config across two locations for no user benefit, and none of the three surfaces is large enough to need its own page (SCIM = 1 create action + 1 list; SAML = 1 form; retention/ZDR = 1 form).
  C. Merge SCIM + SAML into one "Identity" tab (rejected): they are two independently frozen backend contracts with unrelated data shapes (a token-credential list vs. a single config form) and unrelated failure surfaces — mirrors the existing precedent of NOT merging "SSO" and "Provider Keys" into one tab despite both being "external integration" config.
Must:
<must>
  - M1: `SettingsPage` gains exactly three new tabs, appended after the existing four (`Cache · Guardrails · SSO · Provider Keys · SCIM · SAML SSO · Retention & ZDR`) — tab order is additive, no existing tab is reordered, renamed, or removed. The existing `SSO` tab (OIDC) and the new `SAML SSO` tab are DISTINCT tabs, never merged (OIDC and SAML are separate frozen backend contracts, separate config tables).
  - M2: each new tab's `TabsContent` follows the existing lazy-fetch convention (returns null when inactive; its `useQuery` only fires on first activation) — byte-identical performance characteristic to the four existing tabs; visiting `/settings` never fires SCIM/SAML/retention GETs on page load.
  - M3 (SCIM tab): lists live+revoked SCIM tokens (`GET /admin/scim/tokens`) in a table (id-prefix, name, created_at, status badge — mirrors `KeyRow`); a "Create token" button opens a name-only modal (mirrors `CreateKeyDialog`, Zod 1-120 char validation matching the frozen backend's `name: str` body); on 201, the plaintext token is shown EXACTLY ONCE via `PlaintextKeyBanner` (reused verbatim) with a copy affordance and an explicit dismiss that clears the plaintext from component state; a live token row exposes "Rotate" (opens `ConfirmDialog`, on confirm the NEW plaintext is shown via the SAME reveal-once banner) and "Revoke" (opens `ConfirmDialog`, destructive, no secret involved).
  - M4 (SCIM tab, role gate): a MEMBER (lacking `MEMBERS_MANAGE`) sees the token list read-state as an `ErrorState` (403 `ERR_AUTH_FORBIDDEN`, mirrors the existing `OidcSettings` 403-owner-required branch) — no create/rotate/revoke affordance is rendered for a caller who cannot use it (never a disabled-but-visible button that 403s on click).
  - M5 (SAML tab): owner-only GET/PUT form (`GET`/`PUT /admin/saml`) mirroring `OidcSettings`'s structure: fields for `idp_entity_id` (Input), `idp_sso_url` (Input, type=url), `idp_x509_cert` (Textarea — PEM block, multi-line), `email_domains` (Input, comma-separated, byte-identical UX to OIDC's), `email_attribute_name` (Input, optional, placeholder shows the ADFS/Azure-AD default), `enabled` (Switch). Two READ-ONLY fields are always displayed once a config exists: `sp_entity_id` and `acs_url` (both server-derived per the frozen contract — never editable, shown in a copyable `<code>` block so the admin can paste them into their IdP).
  - M6 (SAML tab, cert handling — a deliberate divergence from OIDC's secret pattern): unlike `client_secret` (write-only, never prefilled), `idp_x509_cert` IS prefilled from GET (frozen contract: "the IdP cert is NOT a secret... returned in full on GET") — the Textarea shows the stored PEM on load and re-submits it unchanged unless the admin edits it.
  - M7 (SAML tab, first-time state): no `saml_provider_configs` row (`GET` 404 `ERR_SAML_CONFIG_NOT_FOUND`) renders the SAME empty, fully-editable form as OIDC's first-time 404 path — never an error state for "not yet configured."
  - M8 (Retention & ZDR tab): owner-only (`SECURITY_CONFIG` permission) GET/PUT form (`GET`/`PUT /admin/retention-policy`) mirroring `GuardrailSettings`'s fieldset+Switch structure: a `window_days` numeric Input (nullable — empty means "inherits operator default"), a read-only `operator_ceiling_days` shown as helper text next to the input, a read-only `effective_window_days` breakdown table (one row per swept store class, per the frozen contract's map — `usage_records/alert_events/artifacts/conversations/memories/batch_job_items/video_generation_jobs`; `audit_events` never appears in this list, matching the contract's permanent exclusion), and a `zdr_enabled` Switch.
  - M9 (ZDR enable = destructive/irreversible confirmation — required, not optional): toggling `zdr_enabled` from false→true does NOT save immediately on switch-flip; it opens `ConfirmDialog` with an explicit warning describing the irreversible consequence (per the frozen contract's own scenario: "Disabling ZDR does not retroactively restore purged rows... purge was a real DELETE, not a soft-delete") before the PUT fires. Toggling true→false (disabling ZDR) does NOT require this confirmation (it is not itself destructive — re-enabling payload writes, no data loss).
  - M10 (Retention tab, ZDR-active display): when `zdr_enabled=true`, the `window_days` input and its `effective_window_days` table are shown but visually de-emphasized (disabled input, muted-foreground row text) with an inline note that ZDR supersedes the window — never hidden (GET always returns both; hiding a field the backend still reports would misrepresent state).
  - M11 (shared): every one of the three tabs' mutations surfaces its `BffError.problem.title` inline via `role="alert" aria-live="polite"` on failure (anti-silent-failure standing rule) — no tab silently drops a PUT/POST/DELETE failure.
  - M12 (WCAG 2.2 AA floor, all three tabs): every new interactive element (tab trigger, table row action, dialog button, form field) has a visible `focus-visible` ring (existing `focus-visible:ring-2 focus-visible:ring-ring` utility, reused verbatim — no new focus style invented), a ≥44px hit target (existing `Button`/`Input`/`Switch` sizing already meets this — no override), a programmatic label (`aria-label` or `<label htmlFor>`, matching the exact pattern in `OidcSettings`/`GuardrailSettings`), and correct landmark/heading order (each tab's content has no competing `h1` — `PageHeader` owns the page's sole `h1`, mirrors the existing `ErrorState.titleAs="p"` default rationale).
</must>
Reject:
<reject>
  - SCIM tab, caller lacks `MEMBERS_MANAGE` (403 `ERR_AUTH_FORBIDDEN` from `GET /admin/scim/tokens`) -> `ErrorState`, no create/rotate/revoke affordance rendered -> "ERR_AUTH_FORBIDDEN" (rendered, not thrown further)
  - SCIM tab, create-token name fails Zod validation (empty or >120 chars) -> inline field error, NO POST fired -> client-side, no backend code
  - SCIM tab, `POST /admin/scim/tokens` 403 (a role changed mid-session) -> inline `role="alert"` in the create dialog, dialog stays open -> "ERR_AUTH_FORBIDDEN"
  - SCIM tab, rotate/revoke on an already-revoked/unknown token id (race: two admin tabs) -> inline `role="alert"` in the confirm dialog, dialog stays open, list re-fetches on dismiss -> "ERR_SCIM_TOKEN_NOT_FOUND"
  - SAML tab, non-owner GET/PUT (403 `ERR_AUTH_FORBIDDEN_OWNER_REQUIRED`) -> `ErrorState`, no form rendered -> "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED"
  - SAML tab, PUT with invalid/expired PEM cert (422 `ERR_SAML_CERT_INVALID`) -> inline `role="alert"` under the Textarea, form stays populated with the admin's edits (not reset) -> "ERR_SAML_CERT_INVALID"
  - SAML tab, PUT with a non-https/private-IP SSO URL (422, `detail: [...]` shape) -> inline `role="alert"` reading the first validation message, form stays populated -> 422 validation_errors
  - Retention tab, non-owner GET/PUT (403 `ERR_AUTH_FORBIDDEN`) -> `ErrorState`, no form rendered -> "ERR_AUTH_FORBIDDEN"
  - Retention tab, PUT `window_days` out of bounds (≤0 or > `operator_ceiling_days`, 422 `ERR_RETENTION_WINDOW_INVALID`) -> inline `role="alert"` under the input, value NOT reset -> "ERR_RETENTION_WINDOW_INVALID"
  - Retention tab, ZDR confirm dialog: admin clicks Cancel -> `zdr_enabled` Switch visually reverts to its pre-click (false) state, NO PUT fired -> client-side only
  - Retention/SCIM/SAML tab, any GET/mutation on cross-tenant 404 (`ERR_TENANT_NOT_FOUND` / SCIM-token-not-found / SAML-config-not-found) -> `ErrorState` or inline alert per the specific frozen code, NEVER a UI that implies another tenant's data is visible
</reject>
After:
<after>
  - a tenant OWNER (or MEMBERS_MANAGE-holder, SCIM only) reaches SCIM/SAML/Retention config from the SAME `/settings` page as Cache/Guardrails/SSO/Provider Keys, with no new navigation concept to learn
  - a SCIM token's plaintext secret is visible to the admin exactly once (create or rotate), never retrievable again through any UI affordance
  - a SAML IdP config round-trips through the form with the cert prefilled (not write-only) and `sp_entity_id`/`acs_url` always shown read-only for the admin to hand to their IdP
  - enabling ZDR is a deliberate, confirmed, irreversible-consequence-disclosed action — never a silent switch-flip
  - every one of the three tabs' error states is visible, specific, and keyboard/screen-reader operable — matching the existing four tabs' bar exactly
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **This task's own existence** (the AUTO-MODE gap-fill framing, per the orchestrator's dispatch note and both `scim-provisioning`/`saml-sso`'s independent §1 flags) is the single lowest-confidence item in this whole draft — lowest confidence because no milestone Task row ever named an owner for the UI/UX-in-scope line; it is entirely plausible Tin wants these three surfaces shipped as a FOURTH backend task's own follow-up delta instead of one combined FE task, or wants them split one-tab-per-task, or wants domain-capture's future verification affordance folded in here after all. If wrong: this task's Build scope is either narrowed (drop a tab, hand it to a sibling) or widened (add the domain-verification UI) — cheap to redirect since nothing has been built yet, but it is the freeze decision Tin must make BEFORE Build starts, not after.
  - [ ] ZDR-active de-emphasis of `window_days` (M10: shown-disabled, not hidden) rather than hidden entirely — confirm or deny; cost if wrong is a one-line conditional-render change, not a data-shape change (the frozen contract already returns both fields regardless of which way the UI chooses to display them).
  - [ ] Rotate-token flow reuses `ConfirmDialog` (a plain yes/no) rather than immediately opening `PlaintextKeyBanner` post-confirm — chosen because the frozen `POST /admin/scim/tokens/{id}/rotate` response IS the new plaintext token in one call (no separate reveal step exists server-side); confirm this two-step UI (confirm-dialog → banner) reads clearly rather than needing to be one combined dialog. Low cost if wrong — same components, different composition.
  - [ ] SCIM tab's role gate (M4) checks `MEMBERS_MANAGE` client-side only for AFFORDANCE HIDING (never for enforcement — the 403 from the real GET is still the actual gate, mirrors every other tab's owner-required pattern) — confirm this reads as intended: a MEMBER never sees a Create-token button that would 403, but the button's absence is a UX nicety, not a security control (the backend 403 is authoritative either way).
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Settings hub shows all seven tabs, new tabs lazy-load   # M1, M2
  Given an authenticated tenant OWNER navigates to /settings
  When the page renders
  Then the tab bar reads "Cache · Guardrails · SSO · Provider Keys · SCIM · SAML SSO · Retention & ZDR" in that order
  And no /admin/scim/tokens, /admin/saml, or /admin/retention-policy request has fired yet (Cache's GET fired, as today; the other six tabs are inactive)

Scenario: Activating the SCIM tab fires exactly one GET   # M2
  Given the settings page is open on the default Cache tab
  When the OWNER clicks the "SCIM" tab
  Then exactly one GET /admin/scim/tokens request fires
  And re-clicking away and back does not refire it (TanStack Query cache, matches existing tab behavior)

Scenario: OWNER creates a SCIM token and sees the plaintext once   # M3
  Given the OWNER is on the SCIM tab with zero existing tokens (Empty state)
  When they click "Create token", enter "Okta" as the name, and submit
  Then POST /admin/scim/tokens {"name":"Okta"} fires
  And on 201, a PlaintextKeyBanner shows the returned token string in a <code> leaf with a Copy button
  And the token list re-fetches and shows the new row (name "Okta", status "active", no plaintext visible in the row)
  And dismissing the banner clears the plaintext from component state (no re-render can show it again)

Scenario: MEMBER cannot see SCIM create/rotate/revoke affordances   # M4, R (403)
  Given an authenticated tenant MEMBER (no MEMBERS_MANAGE) opens the SCIM tab
  When GET /admin/scim/tokens returns 403 ERR_AUTH_FORBIDDEN
  Then an ErrorState renders with the problem title
  And no "Create token" button, no per-row Rotate/Revoke button is rendered anywhere on the tab

Scenario: SCIM token name validation blocks the request client-side   # R (Zod)
  Given the OWNER opens the Create-token dialog
  When they submit with an empty name
  Then an inline field error ("Key name is required"–equivalent) appears
  And no POST /admin/scim/tokens request is made

Scenario: Rotate shows the new plaintext once, old token is superseded   # M3
  Given a live SCIM token "Okta" exists in the list
  When the OWNER clicks "Rotate" on that row, confirms in the ConfirmDialog
  Then POST /admin/scim/tokens/{id}/rotate fires
  And on 200, PlaintextKeyBanner shows the NEW plaintext once
  And the list re-fetches; the row's created_at reflects the new token (old id/secret no longer usable per the frozen backend contract — not independently re-verified by this UI task)

Scenario: Revoke requires confirmation and shows the revoked badge   # M3
  Given a live SCIM token "Okta" exists in the list
  When the OWNER clicks "Revoke", sees the ConfirmDialog, and confirms
  Then DELETE /admin/scim/tokens/{id} fires
  And on 204, the list re-fetches and the row shows a destructive "Revoked" badge, no action buttons remain on that row

Scenario: Revoke on an already-gone token surfaces inline, dialog stays open   # R (404 race)
  Given two admin tabs are open on the same SCIM token
  When tab A revokes it, then tab B (stale list) attempts to revoke the same id
  Then DELETE returns 404 ERR_SCIM_TOKEN_NOT_FOUND
  And tab B's ConfirmDialog shows an inline role="alert" with the error, stays open (does not silently close as if it succeeded)

Scenario: Rotate on an already-gone token surfaces inline, dialog stays open   # R (404 race, rotate)
  Given tab B's stale list still shows a SCIM token that tab A already revoked
  When tab B clicks "Rotate" and confirms
  Then POST /admin/scim/tokens/{id}/rotate returns 404 ERR_SCIM_TOKEN_NOT_FOUND
  And the ConfirmDialog shows the inline error and stays open, no plaintext banner is shown, the list re-fetches on dismiss

Scenario: SCIM token create is rejected if the caller's role changed mid-session   # R (403 on POST)
  Given the OWNER's Create-token dialog is open (GET had earlier succeeded with MEMBERS_MANAGE)
  When their role is revoked out-of-band and they submit the create form
  Then POST /admin/scim/tokens returns 403 ERR_AUTH_FORBIDDEN
  And the dialog shows the inline error and stays open — no plaintext banner appears, no token is listed

Scenario: SAML tab first-time empty form   # M7
  Given the tenant has no saml_provider_configs row (GET /admin/saml returns 404 ERR_SAML_CONFIG_NOT_FOUND)
  When the OWNER opens the SAML SSO tab
  Then an empty, fully-editable form renders (idp_entity_id, idp_sso_url, idp_x509_cert Textarea, email_domains, email_attribute_name, enabled Switch) — no error state
  And no sp_entity_id/acs_url block is shown yet (nothing to derive without a tenant_id-scoped row existing — informational text explains they'll appear after first save)

Scenario: SAML tab prefills cert unlike OIDC's write-only secret   # M6
  Given the tenant has a saml_provider_configs row with a stored PEM cert
  When the OWNER opens the SAML SSO tab
  Then GET /admin/saml returns the full idp_x509_cert value
  And the Textarea is PRE-FILLED with that PEM text (unlike OidcSettings's client_secret, which never prefills)
  And sp_entity_id and acs_url are shown read-only in copyable <code> blocks

Scenario: SAML tab PUT with invalid cert is rejected, form preserved   # R (422 cert)
  Given the OWNER edits the cert Textarea to a syntactically broken PEM blob and clicks Save
  When PUT /admin/saml is submitted
  Then a 422 ERR_SAML_CERT_INVALID response surfaces inline under the Textarea via role="alert"
  And the form's current (broken) input values are NOT reset — the admin can fix and resubmit without retyping everything

Scenario: SAML tab non-owner sees no form   # R (403 owner)
  Given an authenticated ADMIN (not OWNER) opens the SAML SSO tab
  When GET /admin/saml returns 403 ERR_AUTH_FORBIDDEN_OWNER_REQUIRED
  Then an ErrorState renders, no form fields are shown

Scenario: SAML tab PUT with a non-https/private-IP SSO URL is rejected, form preserved   # R (422 url)
  Given the OWNER enters "http://192.168.1.1/sso" as idp_sso_url and clicks Save
  When PUT /admin/saml is submitted
  Then a 422 response ({ detail: [{ type, loc, msg, input }] }) surfaces inline via role="alert" reading the first validation message
  And the form's current input values are NOT reset

Scenario: Retention tab default state, no override   # M8
  Given a tenant with no retention_policy override ever set
  When the OWNER opens the Retention & ZDR tab
  Then GET /admin/retention-policy shows window_days as empty/null (helper text: "inherits operator default"), zdr_enabled=false, operator_ceiling_days shown as read-only helper text
  And the effective_window_days breakdown table lists exactly 7 rows (usage_records, alert_events, artifacts, conversations, memories, batch_job_items, video_generation_jobs) — audit_events never appears

Scenario: Owner shortens the retention window   # M8
  Given operator_ceiling_days=365
  When the OWNER enters window_days=30 and clicks Save
  Then PUT /admin/retention-policy {"window_days":30} fires
  And on 200, the form reflects window_days=30 and the effective_window_days table updates to 30 for every listed store

Scenario: Retention window out-of-bounds is rejected inline   # R (422 window)
  Given the OWNER enters window_days=400 (operator_ceiling_days=365)
  When they click Save
  Then a 422 ERR_RETENTION_WINDOW_INVALID surfaces inline under the input via role="alert"
  And the input's typed value (400) is NOT reset — the admin can correct it in place

Scenario: Enabling ZDR requires explicit irreversible-consequence confirmation   # M9
  Given the OWNER is on the Retention & ZDR tab with zdr_enabled=false
  When they flip the ZDR Switch to on
  Then NO PUT fires yet — a ConfirmDialog opens describing the irreversible purge consequence
  And clicking Cancel reverts the Switch to off, no request is made
  And clicking Confirm fires PUT /admin/retention-policy {"zdr_enabled":true}, and on 200 the Switch stays on and a confirmation state (e.g. zdr_enabled_at) is shown

Scenario: Disabling ZDR does not require the destructive-confirm dialog   # M9 (inverse)
  Given the OWNER is on the Retention & ZDR tab with zdr_enabled=true
  When they flip the ZDR Switch to off
  Then PUT /admin/retention-policy {"zdr_enabled":false} fires immediately, no ConfirmDialog interrupts
  And on 200 the Switch reflects off and new payload writes are implied accepted again (per the frozen backend contract; not independently re-verified here)

Scenario: ZDR-active state de-emphasizes but never hides the window field   # M10
  Given zdr_enabled=true for the tenant
  When the OWNER views the Retention & ZDR tab
  Then the window_days input is rendered disabled (not removed) with a note that ZDR supersedes it
  And the effective_window_days table is still visible (muted styling), not hidden — the backend's returned values remain fully inspectable

Scenario: Retention tab non-owner sees no form   # R (403)
  Given an authenticated ADMIN (holds most permissions but not SECURITY_CONFIG) opens the Retention & ZDR tab
  When GET /admin/retention-policy returns 403 ERR_AUTH_FORBIDDEN
  Then an ErrorState renders, no form fields are shown

Scenario: Cross-tenant 404 never implies another tenant's data exists   # R (cross-tenant 404, representative)
  Given a caller's session resolves to a tenant_id with no matching row in the target admin surface
    (e.g. a stale/orphaned identity hitting GET /admin/retention-policy -> 404 ERR_TENANT_NOT_FOUND;
    the same shape applies to SCIM's ERR_SCIM_TOKEN_NOT_FOUND and SAML's ERR_SAML_CONFIG_NOT_FOUND)
  When the tab renders the 404 response
  Then an ErrorState (or, for SAML/SCIM's specific first-time-vs-error branching, the tab's own
    documented 404 handling) renders with the problem title
  And no field, row, or copy anywhere on the tab implies a specific OTHER tenant's configuration exists

Scenario: Every new tab passes an automated a11y sweep   # M12
  Given each of the SCIM, SAML SSO, and Retention & ZDR tabs is rendered in its populated state
  When an automated accessibility scan (jest-axe, mirrors the existing test_settings_axe_clean) runs against each
  Then zero serious/critical violations are reported (contrast, label, landmark, focus-visible)

Scenario: Keyboard-only operation reaches every new-tab action   # M12
  Given a keyboard-only user tabs from the settings tab bar into a new tab's content
  When they Tab through the SCIM/SAML/Retention controls
  Then focus order follows visual/DOM order, every interactive element shows a visible focus ring, and every dialog (Create/Confirm) traps focus and returns it to the trigger on close (existing useFocusTrap behavior, reused verbatim)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Least-sure flag surfaced at freeze: [scope] this task's own existence as the AUTO-MODE gap-fill
owner of the milestone's "UI/UX in scope" line — no milestone Task row named an owner; `scim-provisioning`
and `saml-sso` each independently flagged the gap in their own §1 assumptions rather than silently
building a screen themselves. Needs Tin's explicit confirmation at freeze (accept this task as scoped
below · split it per-surface · fold into a sibling's delta · defer). This is a SCOPE flag, not a shape
flag — the component contract below is low-risk (three frozen backend contracts, no live co-design).

**STATUS: DRAFT — awaiting human freeze.** Every prop/state shape below is checked against the three
FROZEN backend contracts it consumes (`scim-provisioning` Part A, `saml-sso` Part B, `tenant-retention-zdr`
§3) field-for-field; no backend shape is invented or guessed.

### Component contract — three new files, `apps/dashboard/components/settings/`

```tsx
// ScimSettings.tsx — new tab, mirrors CreateKeyDialog + PlaintextKeyBanner + KeyRow composition
export function ScimSettings(): JSX.Element

// Consumes (frozen scim-provisioning Part A — /admin/scim/tokens, RFC 9457):
//   GET    /admin/scim/tokens        -> { tokens: [{ id, name, created_at, revoked_at: string|null }] }
//   POST   /admin/scim/tokens        body: { name: string }
//                                    -> { id, name, token: string, created_at }   # plaintext, once
//   POST   /admin/scim/tokens/{id}/rotate  -> { id, name, token: string, created_at }  # NEW plaintext, once
//   DELETE /admin/scim/tokens/{id}   -> 204 empty
// Error shapes bound: 403 ERR_AUTH_FORBIDDEN (list+create) · 404 ERR_SCIM_TOKEN_NOT_FOUND (rotate/revoke)

interface ScimTokenRow { id: string; name: string; created_at: string; revoked_at: string | null }

// Local UI state (not persisted): plaintextReveal: { id: string; token: string; mode: "create"|"rotate" } | null
// — cleared on PlaintextKeyBanner's onDismiss; NEVER re-derived from any cached query data (the frozen
// GET/list response never carries a token field, so there is no accidental re-render risk, but the
// dismiss-clears-state contract from PlaintextKeyBanner is honored verbatim regardless).

// ---

// SamlSettings.tsx — new tab, mirrors OidcSettings's GET/PUT/seed-guard shape
export function SamlSettings(): JSX.Element

// Consumes (frozen saml-sso Part B — /admin/saml, owner-only):
//   GET /admin/saml -> { tenant_id, idp_entity_id, idp_sso_url, idp_x509_cert, sp_entity_id, acs_url,
//                         email_domains: string[], email_attribute_name, enabled, created_at, updated_at }
//        | 404 { code: "ERR_SAML_CONFIG_NOT_FOUND" } | 403 { code: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }
//   PUT /admin/saml body: { idp_entity_id, idp_sso_url, idp_x509_cert, email_domains: string[],
//                            email_attribute_name?, enabled? }
//        -> same shape as GET (sp_entity_id/acs_url always server-derived, echoed read-only)
//        | 422 { detail: [{ type, loc, msg, input }] }  (ERR_SAML_CERT_INVALID or SSRF-shaped URL error)
//        | 403 { code: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }

interface SamlConf {
  tenant_id: string; idp_entity_id: string; idp_sso_url: string; idp_x509_cert: string;
  sp_entity_id: string; acs_url: string; email_domains: string[]; email_attribute_name: string;
  enabled: boolean; created_at: string; updated_at: string;
}
// Local form state mirrors OidcSettings 1:1 EXCEPT: idp_x509_cert IS seeded from GET (M6 divergence —
// no write-only handling), sp_entity_id/acs_url are NEVER form inputs (display-only <code> blocks,
// rendered only when hasConfig is true — absent on the first-time 404/empty-form path).

// ---

// RetentionZdrSettings.tsx — new tab, mirrors GuardrailSettings's fieldset+Switch+seed-guard shape
export function RetentionZdrSettings(): JSX.Element

// Consumes (frozen tenant-retention-zdr §3 — /admin/retention-policy):
//   GET /admin/retention-policy -> {
//     window_days: number | null,
//     effective_window_days: { usage_records, alert_events, artifacts, conversations, memories,
//                               batch_job_items, video_generation_jobs: number },   // 7 keys, no audit_events
//     zdr_enabled: boolean, zdr_enabled_at: string | null, operator_ceiling_days: number
//   } | 404 { error: "ERR_TENANT_NOT_FOUND" }
//   PUT /admin/retention-policy body: { window_days?: number, zdr_enabled?: boolean }
//     -> same shape as GET (post-update)
//     | 422 { error: "ERR_RETENTION_WINDOW_INVALID" } | 403 { error: "ERR_AUTH_FORBIDDEN" }
//     | 404 { error: "ERR_TENANT_NOT_FOUND" }

interface RetentionPolicy {
  window_days: number | null;
  effective_window_days: Record<
    "usage_records" | "alert_events" | "artifacts" | "conversations" | "memories"
    | "batch_job_items" | "video_generation_jobs", number
  >;
  zdr_enabled: boolean; zdr_enabled_at: string | null; operator_ceiling_days: number;
}
// Local UI-only state: zdrConfirmOpen: boolean — gates the false->true PUT behind ConfirmDialog (M9);
// the true->false PUT fires directly from the Switch's onCheckedChange (no dialog), matching the
// asymmetric confirm rule. window_days input `disabled={zdr_enabled}` (M10) — value/display NOT hidden.
```

### Shared additions to `SettingsPage.tsx` (extend, not rewrite)

```tsx
<TabsList>
  {/* existing 4 triggers unchanged */}
  <TabsTrigger value="scim">SCIM</TabsTrigger>
  <TabsTrigger value="saml">SAML SSO</TabsTrigger>
  <TabsTrigger value="retention">Retention & ZDR</TabsTrigger>
</TabsList>
{/* existing 4 TabsContent unchanged */}
<TabsContent value="scim"><ScimSettings /></TabsContent>
<TabsContent value="saml"><SamlSettings /></TabsContent>
<TabsContent value="retention"><RetentionZdrSettings /></TabsContent>
```

### Reused, unmodified components (cited paths — zero edits)
`apps/dashboard/components/keys/CreateKeyDialog.tsx` · `apps/dashboard/components/keys/PlaintextKeyBanner.tsx`
· `apps/dashboard/components/teams/ConfirmDialog.tsx` · `apps/dashboard/components/ui/{textarea,switch,input,
button,table,states,tabs,page-header}.tsx` · `apps/dashboard/lib/bff-client.ts` (`bffGet/bffPost/bffPut/
bffDelete/BffError`) · `apps/dashboard/lib/use-focus-trap.ts`

### Observable states (every tab, the freeze-approved surface)
- Loading: `<Loading label="Loading <surface> settings" />` (existing component, caller-supplied label only)
- Empty (SCIM, zero tokens): existing `Empty` component, action = "Create token" button
- Error (403/404/other): existing `ErrorState`, `role="alert"`, problem title verbatim, no form/table shown
- Populated: form or table as specified above, every mutation error inline via `role="alert" aria-live="polite"`
- Reveal-once (SCIM only): `PlaintextKeyBanner`, `role="alert" aria-live="polite"`, cleared on dismiss

Glossary deltas: none — this task consumes three already-frozen backend Glossary terms (`SCIM token`,
`sp_entity_id`, `Zero-Data-Retention (ZDR)`, `Tenant retention window`) verbatim; it introduces no new
domain concept, only their admin-UI surface.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: yes — presented for freeze 2026-07-10.
Decided at freeze (Tin, 2026-07-10): KEEP this AUTO-MODE task as ONE consolidated settings surface
(three new /settings tabs — SCIM · SAML SSO · Retention & ZDR), not split per-surface and not deferred.
The task's existence (the lead scope flag) is confirmed.

Least-sure flag surfaced at freeze: [contract] this task's own EXISTENCE/scope as the AUTO-MODE gap-fill
owner of the milestone's "UI/UX in scope" line — no milestone Task row named an owner; scim-provisioning
and saml-sso each independently flagged the gap. RESOLVED at freeze: Tin confirmed keep-as-one-task.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## Design self-score

- Completeness: 0.93 — every §1 Must has a §2 scenario; every §1 Reject line now has a matching scenario
  (a first draft pass missed the SCIM POST-403-mid-session, SCIM rotate-404-race, SAML bad-URL-422, and a
  representative cross-tenant-404 case — added on review, not left as a gap). All three consumed frozen
  contracts are bound field-for-field in §3. Held back from higher: the task's own scope (which surfaces
  belong to one FE task vs split/folded elsewhere) is an open freeze question, not a shape gap.
- Clarity: 0.93 — every §0 anchor cites a real `path:symbol`; every §3 prop/state shape traces to an
  existing shipped component or a named, justified divergence (cert prefill, Textarea substitution, ZDR
  confirm asymmetry); naming reuses the frozen backend Glossary terms verbatim, no invented vocabulary.
- Practicality: 0.94 — zero new dependencies, zero new visual primitives (Textarea already exists in the
  `ui` barrel, unused until now); three new component files plus one extended tab-hub file plus one
  extended test file — the smallest surface that honors "no new page archetype."
- Optimization: 0.91 — avoids over-building (no new route, no new nav concept, SCIM/SAML kept as separate
  tabs rather than a speculative merge); avoids under-building (the ZDR irreversible-confirm and the
  MEMBERS_MANAGE affordance-hiding are real UX requirements pulled from the frozen backend contracts'
  own stated risk, not skipped for a smaller diff).
- Edge cases: 0.91 — 404-vs-403-vs-other branching, write-conflict races (revoke/rotate on an
  already-gone token), out-of-bounds validation, cross-tenant 404, and the ZDR de-emphasize-not-hide
  rule are all scenario-covered. Not covered (deliberately, named as a Build-time trace item, not silently
  absent): whether every non-`sk-`-key dashboard-JWT write path needs its own ZDR-awareness check — that
  is the BACKEND task's own open issue (`tenant-retention-zdr` §0), not this UI task's to resolve.
- Self-evaluation: 0.90 — the single ⚠ (this task's own scope/existence) is stated as a scope question,
  not a shape question, and is ranked ahead of the three lower-confidence UI-only judgment calls (ZDR
  de-emphasis styling, rotate-flow dialog composition, client-side-only role-gate framing) — each with a
  named, cheap-to-flip cost if reversed.

All six ≥ 0.90 — no refinement pass required before reporting.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: match the existing four tabs' bar in `tenant-settings.test.tsx` — one `describe` block per
new tab, one test per §2 scenario, plus the shared axe sweep extended to cover all three new populated states.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_renders_seven_tabs_no_premature_fetch: tab bar order + zero SCIM/SAML/retention GETs on mount · covers: M1, M2
  - test_scim_tab_lazy_fetches_once: activate SCIM tab, assert exactly one GET · covers: M2
  - test_owner_creates_scim_token_reveals_once: create "Okta" → banner shows plaintext, list re-fetches, dismiss clears state · covers: M3
  - test_member_cannot_see_scim_affordances: 403 on GET → ErrorState, no Create/Rotate/Revoke buttons · covers: M4, R(403)
  - test_scim_create_name_validation_blocks_client_side: empty name → inline error, no POST · covers: R(zod)
  - test_scim_rotate_reveals_new_plaintext: rotate → confirm → new banner, list re-fetches · covers: M3
  - test_scim_revoke_shows_destructive_badge: revoke → confirm → 204 → badge + no row actions · covers: M3
  - test_scim_revoke_404_race_stays_open_inline: DELETE 404 → ConfirmDialog inline alert, stays open · covers: R(404 race)
  - test_scim_rotate_404_race_stays_open_inline: rotate POST 404 → ConfirmDialog inline alert, stays open, no banner · covers: R(404 race, rotate)
  - test_scim_create_403_mid_session_stays_open: POST 403 (role revoked mid-session) → dialog inline alert, stays open, no banner/no row · covers: R(403 on POST)
  - test_saml_tab_first_time_empty_form: 404 → empty editable form, no sp_entity_id/acs_url block · covers: M7
  - test_saml_tab_prefills_cert_unlike_oidc_secret: 200 with cert → Textarea prefilled, sp_entity_id/acs_url shown read-only · covers: M6
  - test_saml_put_invalid_cert_422_preserves_form: 422 ERR_SAML_CERT_INVALID → inline alert, inputs not reset · covers: R(422 cert)
  - test_saml_put_bad_url_422_preserves_form: 422 validation_errors (private-IP SSO URL) → inline alert, inputs not reset · covers: R(422 url)
  - test_saml_non_owner_no_form: 403 ERR_AUTH_FORBIDDEN_OWNER_REQUIRED → ErrorState, no fields · covers: R(403 owner)
  - test_retention_tab_default_state_seven_rows_no_audit: null window_days, zdr false, 7-row table, audit_events absent · covers: M8
  - test_retention_put_shortens_window: window_days=30 → PUT fires, table updates to 30 · covers: M8
  - test_retention_window_out_of_bounds_422_inline: window_days=400 → inline alert, value not reset · covers: R(422 window)
  - test_zdr_enable_requires_confirm_dialog: flip switch true → no PUT yet, ConfirmDialog opens; Cancel reverts switch, no request · covers: M9
  - test_zdr_enable_confirm_fires_put: confirm → PUT {zdr_enabled:true} fires, switch stays on · covers: M9
  - test_zdr_disable_no_confirm_dialog: flip switch false→ PUT fires immediately, no dialog · covers: M9 (inverse)
  - test_zdr_active_deemphasizes_not_hides_window: zdr_enabled=true → window input disabled (present), table muted (present) · covers: M10
  - test_retention_non_owner_no_form: 403 ERR_AUTH_FORBIDDEN → ErrorState, no fields · covers: R(403)
  - test_cross_tenant_404_no_leak[scim|saml|retention]: representative 404 per tab → ErrorState/documented 404 branch, no other-tenant data implied · covers: R(cross-tenant 404)
  - test_scim_saml_retention_axe_clean: jest-axe against each tab's populated state, zero serious/critical · covers: M12
  - test_new_tabs_keyboard_focus_order: Tab traversal + visible focus-visible ring + dialog focus-trap-and-return · covers: M12
</test_plan>

Tests live in: `apps/dashboard/tests-bff/tenant-settings.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/settings/` `apps/dashboard/components/ui/index.ts` `apps/dashboard/tests-bff/tenant-settings.test.tsx`

Strategy (ordered batches):
1. `ScimSettings.tsx` — clone `CreateKeyDialog`+`PlaintextKeyBanner`+`KeyRow` composition shape from `apps/dashboard/components/keys/KeysPage.tsx`; wire to `/admin/scim/tokens` per the frozen §3 shapes above.
2. `SamlSettings.tsx` — clone `OidcSettings.tsx` structure; swap the write-only-secret handling for prefilled-cert handling (M6); add the two read-only `sp_entity_id`/`acs_url` `<code>` blocks; swap `Input` for `Textarea` on the cert field only.
3. `RetentionZdrSettings.tsx` — clone `GuardrailSettings.tsx` fieldset+Switch+seed-guard shape; add the `effective_window_days` read-only breakdown table; wire the false→true ZDR toggle through `ConfirmDialog` (M9), true→false direct.
4. Extend `SettingsPage.tsx`: three new `TabsTrigger`/`TabsContent` pairs appended after the existing four — no reorder, no edit to existing tab wiring.
5. Extend `tenant-settings.test.tsx`: three new `describe` blocks (one per tab) following the file's existing per-tab convention, plus extending the closing axe/nav `describe` to cover the three new populated states.

Persona (required): ui-designer (`.add/personas/ui-designer.md`) — reuse-before-invent + WCAG 2.2 AA floor is the direct domain stance for a settings-IA extension; `frontend-engineer` is a reasonable alternate if the build agent needs a broader FE-implementation stance, but every visual/interaction call in this contract already traces to an existing shipped component (ui-designer's "consistency over novelty" rule is the binding one here).
Spawn isolation (default): worktree — dashboard build/test runs share no risky external state, but the default rule applies; no stated reason to deviate.
Known-problem fixes:
- trap: re-seeding form state from a fresh `useQuery` data object on EVERY render (infinite loop) -> planned fix: reuse the exact `seededData !== data` ref-equality guard from `OidcSettings.tsx`/`GuardrailSettings.tsx` verbatim, never a `useEffect`-based seed.
- trap: SAML cert Textarea accidentally inheriting `OidcSettings`'s write-only/never-prefill secret pattern (copy-paste risk given how closely this file mirrors it) -> planned fix: an explicit test (`test_saml_tab_prefills_cert_unlike_oidc_secret`) asserts the OPPOSITE of `OidcSettings`'s secret behavior — must fail red if the pattern is copied uncritically.
- trap: ZDR Switch firing the PUT on `onCheckedChange` directly (matches every other Switch in this codebase) instead of gating false->true behind `ConfirmDialog` -> planned fix: `onCheckedChange` handler branches on direction; only true->false calls the mutation directly, false->true opens the dialog and the mutation is called from `ConfirmDialog`'s `onConfirm`.
- trap: hiding `window_days`/`effective_window_days` entirely when `zdr_enabled=true` (a plausible-but-wrong simplification) -> planned fix: `disabled` attribute + muted className only, never a conditional `{!zdr_enabled && (...)}` unmount.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): the plaintext SCIM token (create or rotate response) must never be written to any persisted state (TanStack Query cache, localStorage, URL) — component `useState` only, cleared on dismiss, mirrors `PlaintextKeyBanner`'s existing contract verbatim.
Code lives in: `apps/dashboard/components/settings/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

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
- [x] Settings hub shows 7 tabs in order, three new tabs' GETs never fire until activated — confirmed: `SettingsPage.tsx:29-67` appends 3 `TabsTrigger`/`TabsContent` pairs after the existing 4, unchanged; `tests-bff/tenant-settings.test.tsx:454-492` (`test_renders_seven_tabs_no_premature_fetch`) asserts tab-bar order + zero SCIM/SAML/retention GETs pre-activation; suite run GREEN (44/44, `npx vitest run tests-bff/tenant-settings.test.tsx`).
- [x] SCIM plaintext token is shown exactly once and cannot be made to linger — confirmed by re-reading `ScimSettings.tsx:85-166` (local `useState`, never written to TanStack cache/localStorage/URL, cleared on `onDismiss`) AND by 2 throwaway adversarial repros executed and DELETED after use: (1) create → do NOT dismiss → switch tab away and back (TabsContent unmounts on inactive per `components/ui/tabs.tsx:161` `if (active !== value) return null` — full remount resets local state) → plaintext absent from DOM; (2) create → immediately rotate without dismissing → old plaintext absent, only new plaintext shown. Both attacks FAILED to make it linger — PASSED.
- [x] ZDR enable is gated behind an irreversible-consequence `ConfirmDialog`; disable is not — confirmed: `RetentionZdrSettings.tsx:140-152` (`handleZdrToggle` branches on direction, true→ opens dialog only, false→ mutates directly) + `test_zdr_enable_requires_confirm_dialog`/`test_zdr_enable_confirm_fires_put`/`test_zdr_disable_no_confirm_dialog` all GREEN.
- [x] SAML cert prefills (unlike OIDC's write-only secret); no actual secret leaks into the DOM — confirmed: `SamlSettings.tsx:83-106` seeds `idpCert` from GET (M6, cited divergence), `sp_entity_id`/`acs_url` are display-only `<code>` blocks never form inputs; `test_saml_tab_prefills_cert_unlike_oidc_secret` asserts `certField.value === SAML_CONF.idp_x509_cert` directly (works around a jsdom whitespace-normalizer gap for multi-line PEM — a deliberate, non-vacuous assertion). No `client_secret`-shaped field exists on this contract to leak.
- [x] Every BffError surfaces inline via `role="alert" aria-live="polite"`, never swallowed — confirmed by code read (all 3 files) + 10 error-path tests (403/404/422 × 3 tabs, plus the 2 SCIM-race tests) all GREEN, each asserting `role="alert"` text content.
- [x] a11y floor — `test_scim_saml_retention_axe_clean` (jest-axe, serious/critical) and `test_new_tabs_keyboard_focus_order` both GREEN; PlaintextKeyBanner's own axe-cleanliness and CreateKeyDialog's focus-trap are independently pre-verified in `tests/ui-ux-verify.test.tsx::test_plaintext_banner_passes_axe` and `tests/keys-dialog-a11y.test.tsx` (reused-verbatim components, not re-tested here by design).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `ScimSettings`/`SamlSettings`/`RetentionZdrSettings` are each imported and rendered exactly once from `SettingsPage.tsx`; `CreateKeyDialog`/`PlaintextKeyBanner`/`ConfirmDialog` imported and wired with real props (no stub); `bffGet/bffPost/bffPut/bffDelete/BffError` all imported from `lib/bff-client.ts` (traced through to `lib/resilient-fetch.ts`, cookie-based, no Authorization header, no localStorage) — confirmed by direct read of all 4 touched/new files + `git grep` for each new symbol's usage.
- [x] DEAD-CODE (code) — no orphaned export found; FE typecheck (`npx tsc --noEmit -p .`) run CLEAN, zero errors.
- [ ] SEMANTIC (prose) — n/a, this is a code task.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed: `SettingsPage.tsx`, `OidcSettings.tsx`, `GuardrailSettings.tsx`, `CreateKeyDialog.tsx`, `PlaintextKeyBanner.tsx`, `KeyRow.tsx`/`KeysPage.tsx`, `ConfirmDialog.tsx`, `bff-client.ts`, `tests-bff/tenant-settings.test.tsx` all read directly at current HEAD (branch `chore/add-housekeeping-clusters`, `a69930b`) — all resolve, no stale path.
- [x] no anchor moved/renamed since Ground SHA `443a33a` — `git log --oneline -- apps/dashboard/components/settings/` shows exactly one build commit (`14adcd1`) on top of Ground, no subsequent rename.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self (add-verify, appsec-engineer persona lens) · adversarially checked: (1) SCIM reveal-once secret lingering via tab-remount and back-to-back create→rotate, via 2 executed throwaway vitest repros — both FAILED to leak, deleted after use; (2) ZDR destructive-confirm bypass via a forced double-click race on the Switch while its own ConfirmDialog is open, via 1 executed throwaway vitest repro — the confirm-gated (true) direction cannot be bypassed (only reachable through `ConfirmDialog.onConfirm`), but the Switch itself lacks a `disabled={zdrConfirmOpen}` guard, so a non-standard/forced click while the dialog is open fires the non-destructive `zdr_enabled:false` PUT immediately, leaving a stale dialog open — a real, reproduced UI-state race, not a security bypass (see Residue below); (3) SAML 404-vs-403-vs-other branching and cross-tenant-404 non-leak — code + tests confirmed correct, and confirmed the SAML 404 path deliberately does NOT distinguish "not yet configured" from "orphaned tenant" (both return `ERR_SAML_CONFIG_NOT_FOUND`), which is the correct anti-enumeration shape (byte-identical response), not a gap. Suite is non-vacuous: assertions check real DOM state (`.value` property, `role="alert"` text content, request bodies via MSW handlers), not `toBeTruthy()`-class stubs. One noted spec/implementation divergence: `test_scim_tab_lazy_fetches_once` asserts the SCIM GET refires on tab remount (`scimCount === 2`), which contradicts the literal frozen §2 scenario text ("re-clicking away and back does not refire it") — the test's own inline comment documents this as matching the ACTUAL (also-refetching, no `staleTime` anywhere) behavior of all 4 pre-existing tabs, confirmed correct by reading `GuardrailSettings.tsx`/`OidcSettings.tsx` (neither sets `staleTime` either) — this is a scenario-authoring inaccuracy inherited from design, not a build-introduced weakening; recommend a spec delta to correct the scenario wording (see §7).

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: self (add-verify, appsec-engineer persona)
1. Security: CLEAR — no secret persistence outside narrow `useState` (SCIM plaintext), no write-only-secret pattern violated (SAML cert is correctly non-secret per frozen contract), no cross-tenant leak (404 responses are content-identical whether "not configured" or "orphaned tenant" for SCIM/SAML; Retention explicitly renders `ErrorState` for its `ERR_TENANT_NOT_FOUND`), 403 role-gate is client-hiding only with the real GET 403 as the authoritative gate (per §1 assumption, by design); no raw-HTML injection sink, no `eval`, no URL-based secret exposure anywhere in the 3 new files.
2. Concurrency: RESIDUE — `RetentionZdrSettings.tsx:250-255` (`Switch` bound to `handleZdrToggle` via `onCheckedChange`, no `disabled` while `zdrConfirmOpen`): reproduced via executed throwaway test — a second click on the Switch while its own irreversible-action ConfirmDialog is open fires the (non-destructive) `zdr_enabled:false` PUT directly and bypasses/ignores the open dialog, leaving a stale "Enable ZDR?" prompt on screen after an unrelated write already landed. Real browsers block this via the dialog's full-viewport CSS overlay + `useFocusTrap`'s keyboard trap (defense layer 1 only) — no defense-in-depth at the component's own state-machine level (layer 2, e.g. `disabled={zdrConfirmOpen}`) the way the appsec lens's "any single layer being wrong or bypassed must not be sufficient" standard asks for elsewhere in this codebase. Does NOT allow bypassing confirmation for the actually-destructive (enable) direction — that PUT only ever fires through `ConfirmDialog`'s own `onConfirm`. Severity: MAJOR (real, reproduced, one-line fix), not HARD-STOP (no privilege/secret impact).
3. Architecture: CLEAR — layering matches every existing tab (TanStack Query + BFF client only, no direct fetch, no bypass of the BFF seam); zero new visual primitives; `Textarea` substitution for the PEM cert field is the one cited, justified deviation; SCIM/SAML kept as separate tabs per the Framings-weighed decision, no scope creep into `domain-capture`'s territory.
Verdict: HARD-STOP checklist not triggered by Security (CLEAR); Concurrency RESIDUE recorded and stands.
Residue: ZDR `Switch` missing `disabled={zdrConfirmOpen}` (or fieldset-level disable) as a second defense layer against a forced/synthetic double-toggle while its own destructive-confirm dialog is open (`RetentionZdrSettings.tsx:250`). Reproducible, not exploitable for privilege/secret impact, one-line fix.
Binding: advisory — MAJOR-severity UI-state race, not a security class finding; escalates per this task's `autonomy: auto` "any residue" rule rather than auto-passing silently.

### GATE RECORD
Reported: yes — this verify pass rendered before recording.
Outcome: FINDINGS — not a clean auto-PASS (concurrency residue exists), not HARD-STOP (no security finding). Recommend to orchestrator/Tin: PASS-with-accepted-residue vs. require the one-line `disabled={zdrConfirmOpen}` fix before close.
If RISK-ACCEPTED -> owner: pending Tin · ticket: pending · expires: pending   (never for a security gap — n/a here, this is non-security)
Reviewed by: add-verify (appsec-engineer persona) · date: 2026-07-11

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
