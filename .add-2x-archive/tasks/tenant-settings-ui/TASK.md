# TASK: Tenant settings UI (/settings tabbed hub)

slug: tenant-settings-ui · created: 2026-06-14 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): NEW `/settings` tabbed hub (Cache · Guardrails · SSO/OIDC) — presentation-only over THREE already-frozen gateway contracts; NO gateway/BFF change. Verified anchors:
- NEW route `apps/dashboard/app/(dashboard)/settings/page.tsx` → renders `<SettingsPage/>` (mirrors models/teams route shape). NEW `apps/dashboard/components/settings/SettingsPage.tsx` (`"use client"`) = a `Tabs` shell; decomposed sub-files `CacheSettings.tsx`, `GuardrailSettings.tsx`, `OidcSettings.tsx` (700-line limit).
- Tabs (`components/ui/tabs.tsx`): `Tabs{value?,defaultValue?,onValueChange?}` · `TabsList` (role=tablist) · `TabsTrigger{value}` (role=tab, roving tabindex, ArrowLeft/Right/Home/End) · `TabsContent{value}` (role=tabpanel). The EXACT 3-tab Cache/Guardrails/SSO layout + arrow-key roving is already proven in `tests/design-system/extension.test.tsx:97`.
- Data seam (BFF verbatim, no envelope): `bffGet<T>`, `bffPut<T>(path,body)`; `BffError{status, problem.title}`. Form pattern to mirror: `components/keys/KeyGovernanceEditor.tsx` (bffPatch + local isSubmitting + `<p role="alert">` + `disabled={isSubmitting}`); query+invalidate pattern from ModelsPage/TeamsPage.
- CACHE (`gateway/tenants/api/cache_router.py`, `/admin/cache`): GET → `{enabled:bool, semantic_enabled:bool}` (any role) · PUT `{enabled?:bool, semantic_enabled?:bool}` (absent=no-change) → `{enabled, semantic_enabled}` (require_owner_or_admin; member→403 ERR_AUTH_FORBIDDEN). Two booleans, NO TTL. Two `Switch` toggles.
- GUARDRAILS (`gateway/tenants/api/guardrail_router.py`, `/admin/guardrails`): GET → `{prompt_injection: dict|null, pii_mask: dict|null}` (raw JSONB) · PUT `{prompt_injection?: {enabled:bool, mode:"block"|"audit"}|null, pii_mask?: {enabled:bool, mode:"mask"|"audit", pii_custom_patterns?: [{name, pattern}]}|null}` → merged config (owner/admin; member→403). prompt_injection.mode ∈ {block,audit}; pii_mask.mode ∈ {mask,audit}. Custom patterns: name `^[A-Z][A-Z0-9_]{0,31}$`, regex `pattern`, max 8, server-validated V1–V7 → 422 ERR_PAYLOAD_INVALID. UI: a Switch + a native `<select>` mode + an editable name/pattern list (add/remove rows).
- OIDC/SSO (`gateway/auth/api/oidc_admin_router.py`, `/admin/oidc`) — **OWNER-ONLY** (admin & member → 403 ERR_AUTH_FORBIDDEN "Owner role required"): GET → `{tenant_id, issuer, client_id, client_secret:"<stored>", authorize_url, token_url, jwks_url, email_domains:[str], enabled:bool, created_at, updated_at}` (404 ERR_OIDC_CONFIG_NOT_FOUND when unconfigured) · PUT `{issuer, client_id, client_secret, token_url, jwks_url, email_domains:[str], authorize_url?, enabled?}` → same shape (409 ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED if key absent; 422 SSRF url validation). **client_secret is WRITE-ONLY**: GET ALWAYS returns the literal sentinel `"<stored>"`, never the plaintext/ciphertext; PUT REQUIRES client_secret (re-entered every save). The login endpoint `GET /auth/oidc/login` is a LATER task (governance-completion-ui), out of scope here.
- NAV: `components/ui/app-shell.tsx` `NAV_ITEMS` += `{href:"/settings", label:"Settings", icon: Settings}` (lucide `Settings`).
- `getErrorTitle(err)` inline per page (BffError→problem.title; Error→message; else default). Tests: `tests-bff/` project, msw at `http://localhost:3000/api/gw/...`, fresh QueryClient/test, local `axeSeriousCritical` (color-contrast off); jsdom Tabs arrow-keys proven.

Context (working folder): the v15 MILESTONE.md tenant-settings-ui task. NO gateway/BFF contract change — three existing admin contracts consumed read+write.

Honors (patterns / conventions): the model/teams surface shape (header + state blocks); KeyGovernanceEditor form/error pattern; the four state patterns; CLAUDE.md design-for-failure (every PUT has onError surfacing the title — the STANDING anti-silent-failure rule; every contracted error 403/404/409/422 gets a §4 test + an ErrorState/inline alert); v13/v15 a11y bar; **SECURITY: client_secret never prefilled, never rendered from GET, never logged — the `"<stored>"` sentinel is informational only ("a secret is stored"), the password input starts empty + required on save.**

Anchors the contract cites: the NEW `/settings` `Tabs` hub · the three frozen contracts (cache 2-bool · guardrails injection/pii/patterns · oidc owner-only write-only-secret) via `bffGet/bffPut` · per-tab Loading/Empty/Error + role-gated 403 (SSO admin→ErrorState) · write-only client_secret handling · `NAV_ITEMS` + `getErrorTitle`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant settings UI — a `/settings` tabbed hub (Cache · Guardrails · SSO/OIDC) where an owner/admin views + edits cache toggles, guardrail config (injection · PII · custom patterns), and SSO/OIDC config (client_secret write-only). Presentation-only over three frozen gateway contracts; NO gateway/BFF change.
Framings weighed: One `/settings` page with the design-system `Tabs` (chosen — one route, one RTL suite, the 3-tab layout is already proven; tabs keep three small forms in one place) · Three separate routes /settings/cache|guardrails|sso (rejected — fragments one exit criterion + triples the surface) · Inline-everything single form (rejected — three unrelated contracts on one form is a save-semantics + a11y mess).
Must:
<must>
  - Render a `Tabs` hub with three tabs (Cache · Guardrails · SSO); default tab = Cache (works for every role, so admin/member never land on a 403 SSO tab); tabs are keyboard-operable (roving tabindex) and labelled.
  - CACHE tab: `GET /admin/cache` → two `Switch` toggles (`enabled`, `semantic_enabled`); a Save issues `PUT /admin/cache {enabled, semantic_enabled}`; on 200 the toggles reflect the persisted state; the GET works for any role; a member's PUT → 403 surfaces an inline `role="alert"`.
  - GUARDRAILS tab: `GET /admin/guardrails` → prompt_injection (`Switch` enabled + native `<select>` mode block|audit) and pii_mask (`Switch` enabled + `<select>` mode mask|audit + an editable custom-pattern list of {name, pattern} rows, add/remove, ≤8); a null block defaults to disabled; Save issues `PUT /admin/guardrails`; on 200 the merged config shows; a bad mode/pattern → 422 inline `role="alert"`, config unchanged.
  - SSO tab (OWNER-only): `GET /admin/oidc` → issuer · client_id · authorize_url · token_url · jwks_url · email_domains · enabled; **client_secret is shown only as an informational note ("A client secret is stored") and the password input starts EMPTY** (never prefilled from the `"<stored>"` sentinel); a 404 (unconfigured) renders an editable empty form (first-time setup), NOT an error; Save issues `PUT /admin/oidc` (client_secret REQUIRED — re-entered each save); on 200 the saved config shows (secret still masked).
  - Per-tab state: each tab renders its own Loading + inline Error; NAV gains a `/settings` entry.
  - a11y/UX/SECURITY: axe zero serious/critical; every control labelled; every PUT disables Save while pending AND surfaces its error (the STANDING anti-silent-failure rule); client_secret is NEVER prefilled, rendered from GET, or logged. NO gateway/BFF contract change.
</must>
Reject:
<reject>
  - A member (or admin) editing CACHE or GUARDRAILS without owner/admin role -> 403 "ERR_AUTH_FORBIDDEN" (inline alert, settings unchanged)
  - An admin OR member opening the SSO tab (owner-only GET/PUT) -> 403 "ERR_AUTH_FORBIDDEN" (ErrorState "Owner role required", no form rendered)
  - SSO Save with an EMPTY client_secret -> client blocks the submit (no request; the field is required)
  - SSO Save with a non-https / private-IP url -> 422 "ERR_PAYLOAD_INVALID" (inline alert, config unchanged)
  - SSO Save when the server encryption key is absent -> 409 "ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED" (inline alert)
  - GUARDRAILS Save with an invalid custom pattern name/regex or mode -> 422 "ERR_PAYLOAD_INVALID" (inline alert, config unchanged)
  - Prefilling/echoing the client_secret from the GET `"<stored>"` sentinel into the password input, or logging it -> "secret_leak"
  - A new gateway/BFF endpoint, a TTL field the cache contract lacks, or a `{data}` envelope unwrap -> "scope_creep"
</reject>
After:
<after>
  - `/settings` shows Cache · Guardrails · SSO tabs; an owner edits all three; an admin edits cache + guardrails while the SSO tab 403s to an ErrorState; every contracted error (403/404/409/422) surfaces inline without a crash; client_secret never reaches the input from the server; the full dashboard suite stays green; no gateway/BFF contract changed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The write-only client_secret UX — the gateway PUT REQUIRES client_secret, so the SSO form must require it re-entered on EVERY save (input empty + required, never prefilled from `"<stored>"`). Lowest confidence because a user may expect to edit issuer/urls without re-typing the secret; if wrong (a partial-update UX is wanted), cost = a backend change (out of scope → change-request) or a "keep existing secret" affordance the gateway can't honor. Mitigation: a clear hint "Re-enter the client secret to save; it is never displayed", and the required-field error guides the user.
  - [ ] A 404 from GET /admin/oidc means "not configured yet" → render an editable empty form (enabled=false defaults), not an ErrorState — confirm: first-time setup must be possible.
  - [ ] Guardrails GET dicts may be null → the UI defaults a null prompt_injection/pii_mask to {enabled:false} — confirmed by the dict|null response shape.
  - [ ] Default tab = Cache so a non-owner doesn't open on the 403 SSO tab — low risk; the SSO tab still 403s gracefully when selected.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Settings hub renders three tabs, Cache default
  Given the /settings page loads with GET /admin/cache returning {enabled:true, semantic_enabled:false}
  When the page renders
  Then a tablist exposes Cache, Guardrails, and SSO tabs
  And the Cache tab is selected and shows two switches reflecting enabled=on, semantic=off

Scenario: Tabs are keyboard-operable
  Given the Cache tab is focused
  When the user presses ArrowRight
  Then the Guardrails tab becomes selected (roving tabindex)

Scenario: Save cache settings
  Given the Cache tab with both switches toggled
  When Save is clicked and PUT /admin/cache returns 200 {enabled:false, semantic_enabled:true}
  Then the toggles reflect the persisted state
  And the Save button was disabled while the request was in flight

Scenario: Member cannot save cache
  Given the Cache tab and a member identity
  When Save is clicked and PUT /admin/cache returns 403 ERR_AUTH_FORBIDDEN
  Then an inline role="alert" is shown
  And the cache settings are unchanged

Scenario: View + save guardrails
  Given the Guardrails tab with GET returning prompt_injection {enabled:true, mode:"block"} and pii_mask null
  When the tab renders
  Then the injection switch is on with mode "block" and the pii switch is off
  When a custom pattern {name:"SSN", pattern:"\\d{3}"} is added under pii and Save is clicked and PUT returns 200
  Then the saved config is reflected

Scenario: Guardrails rejects an invalid custom pattern
  Given the Guardrails tab with a custom pattern Save
  When PUT /admin/guardrails returns 422 ERR_PAYLOAD_INVALID
  Then an inline role="alert" is shown
  And the guardrail config is unchanged

Scenario: Owner views SSO config without the secret
  Given the SSO tab and an owner, GET /admin/oidc returning client_secret:"<stored>", issuer set, enabled true
  When the tab renders
  Then issuer and client_id are shown
  And the client_secret password input is EMPTY (the "<stored>" sentinel never appears in any input value)
  And an informational note indicates a secret is stored

Scenario: First-time SSO setup (unconfigured)
  Given the SSO tab and an owner, GET /admin/oidc returning 404 ERR_OIDC_CONFIG_NOT_FOUND
  When the tab renders
  Then an editable empty SSO form is shown (not an ErrorState)

Scenario: Admin is forbidden from the SSO tab
  Given the SSO tab and an admin, GET /admin/oidc returning 403 ERR_AUTH_FORBIDDEN
  When the tab renders
  Then a role="alert" ErrorState ("Owner role required") is shown
  And no SSO form fields are rendered

Scenario: Save SSO config
  Given the SSO tab with issuer/client_id/urls filled and a freshly typed client_secret
  When Save is clicked and PUT /admin/oidc returns 200
  Then the saved config is reflected and the secret input is cleared/masked

Scenario: SSO Save with an empty secret is blocked client-side
  Given the SSO tab with client_secret left empty
  When Save is clicked
  Then a role="alert" field error is shown
  And no PUT request is made

Scenario: SSO Save rejected for a bad URL
  Given the SSO tab with a non-https issuer
  When Save is clicked and PUT /admin/oidc returns 422 ERR_PAYLOAD_INVALID
  Then an inline role="alert" is shown
  And the config is unchanged

Scenario: SSO Save rejected when encryption key absent
  Given the SSO tab
  When Save is clicked and PUT /admin/oidc returns 409 ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED
  Then an inline role="alert" is shown

Scenario: A failing tab GET shows an alert
  Given the Cache tab with GET /admin/cache returning 500
  When the tab renders
  Then a role="alert" ErrorState is shown (no crash)

Scenario: Settings surfaces are accessible
  Given any tab is open
  When axe runs
  Then there are zero serious/critical violations
  And every control has an accessible name

Scenario: NAV exposes Settings
  Given the app shell with activePath /settings
  Then the Primary nav has a Settings link to /settings marked aria-current=page
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SURFACE  /settings  (owner/admin dashboard hub — presentation-only over 3 frozen contracts)
  ROUTE  apps/dashboard/app/(dashboard)/settings/page.tsx -> <SettingsPage/> (metadata.title="Hydroa")
  NAV    app-shell.tsx NAV_ITEMS += { href:"/settings", label:"Settings", icon: Settings }
  SHELL  Tabs defaultValue="cache": TabsList[Cache,Guardrails,SSO] + TabsContent per tab

DATA SEAM (BFF verbatim, no {data} envelope) — bffGet/bffPut:
  GET  /admin/cache       -> { enabled:bool, semantic_enabled:bool }                  (any role)
  PUT  /admin/cache       { enabled, semantic_enabled } -> 200 same | 403 ERR_AUTH_FORBIDDEN
  GET  /admin/guardrails  -> { prompt_injection: PIConf|null, pii_mask: PiiConf|null }
  PUT  /admin/guardrails  { prompt_injection: PIConf|null, pii_mask: PiiConf|null } -> 200 merged | 403 | 422 ERR_PAYLOAD_INVALID
  GET  /admin/oidc        -> OidcConf (client_secret:"<stored>") | 404 ERR_OIDC_CONFIG_NOT_FOUND | 403 ERR_AUTH_FORBIDDEN   (OWNER-only)
  PUT  /admin/oidc        OidcPut -> 200 OidcConf | 403 | 409 ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED | 422 ERR_PAYLOAD_INVALID

TYPES (mirror gateway schemas):
  PIConf   = { enabled:bool, mode:"block"|"audit" }
  PiiConf  = { enabled:bool, mode:"mask"|"audit", pii_custom_patterns?: {name:string, pattern:string}[] }
  OidcConf = { tenant_id, issuer, client_id, client_secret:string("<stored>"), authorize_url, token_url, jwks_url, email_domains:string[], enabled:bool, created_at, updated_at }
  OidcPut  = { issuer, client_id, client_secret, token_url, jwks_url, email_domains:string[], authorize_url?, enabled? }

QUERY KEYS / MUTATIONS:
  useQuery ["admin-cache"]      = bffGet("/admin/cache");      saveCache = bffPut("/admin/cache", body)      -> onSuccess setQueryData(["admin-cache"], resp)
  useQuery ["admin-guardrails"] = bffGet("/admin/guardrails"); saveGuardrails = bffPut("/admin/guardrails", body) -> onSuccess setQueryData(["admin-guardrails"], resp)
  useQuery ["admin-oidc"]       = bffGet("/admin/oidc");       saveOidc = bffPut("/admin/oidc", body)         -> onSuccess setQueryData(["admin-oidc"], resp)
  every mutation has onError -> inline role="alert" with BffError.problem.title (NO silent failure)

OBSERVABLE DOM CONTRACT:
  - tablist role=tablist with 3 role=tab (Cache/Guardrails/SSO); roving tabindex; Cache selected by default
  - Cache: two Switch (aria-label "Enable response cache" / "Enable semantic cache") + Save; PUT body {enabled, semantic_enabled}
  - Guardrails: injection Switch + <select> mode(block|audit); pii Switch + <select> mode(mask|audit) + custom-pattern rows (name <input>, pattern <input>, Remove btn) + "Add pattern" btn (≤8) + Save
  - SSO: <input> issuer/client_id/authorize_url/token_url/jwks_url, email_domains (comma input), enabled Switch, client_secret <input type=password> EMPTY (required); note "A client secret is stored" when configured; 404 -> empty form; admin/member 403 -> ErrorState "Owner role required"
  - per tab: Loading(role=status) while fetching; ErrorState(role=alert) on GET failure
  - every PUT: Save disabled while pending; error -> inline role="alert"
  - SECURITY: the string "<stored>" NEVER appears as any input's value; client_secret never rendered/logged
  - axe: zero serious/critical (color-contrast excluded)
```

Least-sure flag surfaced at freeze: [spec] The write-only client_secret UX — because the gateway PUT REQUIRES client_secret, the SSO form requires it RE-ENTERED on every save (password input starts empty, required; the GET `"<stored>"` sentinel is shown only as a note, never in an input). Why least-sure: a user may expect to edit issuer/urls without retyping the secret, but the gateway contract forbids a partial update. Cost if wrong: a backend change-request (out of scope) or a "keep existing" affordance the gateway can't honor. Decision (auto): require the secret each save + a clear hint; this is the only contract-faithful + secure option. Secondary [contract]: guardrails custom-pattern validation (name regex, ≤8, ReDoS) is SERVER-authoritative — the UI does light client checks but surfaces the server 422 as the source of truth (no client re-implementation of V1–V7).

Status: FROZEN @ v1 — approved by ADD auto (bundle approval delegated per project autonomy=auto; presentation-only over already-frozen gateway contracts; SECURITY-reviewed: client_secret is write-only, never prefilled/rendered/logged — the one security surface, handled by design).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
Coverage target: 80% (dashboard global gate; the new surface aims higher — every tab + error branch tested)
Plan (one test per scenario — RTL + msw at `http://localhost:3000/api/gw/...`, fresh QueryClient/test, `axeSeriousCritical`):
<test_plan>
  - test_renders_three_tabs_cache_default: GET cache → render / assert tablist [Cache,Guardrails,SSO] + Cache selected + 2 switches reflect state
  - test_tabs_keyboard_roving: focus Cache tab, ArrowRight / assert Guardrails selected
  - test_save_cache: toggle both, PUT→200 / assert toggles reflect persisted + Save disabled mid-flight (delay)
  - test_member_cannot_save_cache: PUT→403 / assert inline role=alert + unchanged
  - test_view_and_save_guardrails: GET pi{enabled,block}+pii null → assert injection on/block + pii off; add pattern, PUT→200 / assert saved
  - test_guardrails_invalid_pattern_422: PUT→422 / assert inline role=alert
  - test_owner_views_sso_no_secret: GET oidc client_secret="<stored>" / assert issuer shown + password input EMPTY + no input value equals "<stored>" + "secret is stored" note
  - test_sso_first_time_404_empty_form: GET→404 / assert editable empty form (not ErrorState)
  - test_admin_forbidden_sso: GET→403 / assert ErrorState "owner role required" + no form fields
  - test_save_sso: fill fields + secret, PUT→200 / assert saved (secret input cleared/masked)
  - test_sso_empty_secret_blocked: leave secret empty, Save / assert field role=alert + no PUT
  - test_sso_bad_url_422: PUT→422 / assert inline role=alert
  - test_sso_encryption_409: PUT→409 / assert inline role=alert
  - test_tab_get_failure_shows_alert: GET cache→500 / assert role=alert (no crash)
  - test_settings_axe_clean: render each tab / assert axeSeriousCritical==[] + every control has accessible name
  - test_nav_exposes_settings: AppShell activePath=/settings / assert Settings link href=/settings aria-current=page
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/settings/` `apps/dashboard/app/(dashboard)/settings/` `apps/dashboard/components/ui/app-shell.tsx` `apps/dashboard/tests-bff/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/tenant-settings-ui/`
<!-- SCOPE NOTE: mirrors model/teams declarations. NEW source = components/settings/ + app/(dashboard)/settings/; the ONLY shared-file edit is app-shell.tsx NAV (one entry). tests-bff/ holds the RTL suite. .next/coverage/tsbuildinfo are verify-tooling artifacts (declared so the scope gate doesn't red). NO gateway/BFF source, NO lib/ change (bff-client reused), NO new dependency. -->
Strategy (ordered batches): 1. RED RTL suite `apps/dashboard/tests-bff/tenant-settings.test.tsx`. 2. sub-forms: `CacheSettings.tsx` (2 switches), `GuardrailSettings.tsx` (injection+pii+patterns), `OidcSettings.tsx` (write-only secret). 3. `SettingsPage.tsx` (Tabs shell). 4. route + NAV. 5. bff suite + next lint + full vitest --coverage green.
Safety rule (feature-specific): client_secret is WRITE-ONLY — never prefill the password input from the GET `"<stored>"` sentinel, never render/log it; the input starts empty + required. Every PUT mutation has onError surfacing the BffError title (anti-silent-failure STANDING rule). Each Save disabled while pending. Client blocks empty secret before any request.
Code lives in: `apps/dashboard/components/settings/` + `apps/dashboard/app/(dashboard)/settings/`
Constraints: do NOT change any test or the contract; reuse existing primitives + helpers only (NO new npm dependency); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 16/16 tenant-settings + 24/24 test files; full vitest --coverage EXIT=0
- [x] coverage did not decrease — All files 92.91% (gate 80%); settings: Cache 96.29%, Guardrail 95.05%, Oidc 97.51%
- [x] no test or contract was altered during build — §3 FROZEN untouched; build_tampered tripwire held across both re-cross advances
- [x] the green was EARNED, not gamed — adversarial refute-read (subagent, sonnet) returned EARNED-WITH-GAPS, NO DEFECTs; the two security-relevant GAPs (post-save secret clear; admin-403 no secret field) closed this re-cross with explicit assertions; remaining items are spec-compliant judgment calls / NITs (see below)
- [x] concurrency / timing of the risky operation is safe — Save disabled while pending on all three tabs (saveX.isPending); no shared mutable state across tabs (TabsContent unmounts inactive panels); query/mutation isolation per tab
- [x] no exposed secrets, injection openings, or unexpected dependencies — SECURITY trace (OidcSettings): client_secret WRITE-ONLY — "<stored>" sentinel never enters state/input/PUT/log (useState("") + useEffect skips data.client_secret + PUT body from state + onSuccess clears + empty blocked pre-request + 403→ErrorState no form); now test-asserted. No new npm dependency; bff-client reused
- [x] layering & dependencies follow CONVENTIONS.md — presentation-only over 3 frozen gateway contracts; bffGet/bffPut seam (verbatim, no envelope); NO gateway/BFF/lib change; mirrors ModelsPage/TeamsPage/KeyGovernanceEditor patterns
- [x] a person reviewed and approved the change — auto-resolved under autonomy=auto (delegated): presentation-only, contracts frozen, the one security surface reviewed CLEAN by design + now test-asserted; no security finding (no HARD-STOP)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — SettingsPage → Cache/Guardrail/Oidc sub-forms (3 TabsContent); route app/(dashboard)/settings/page.tsx renders <SettingsPage/>; NAV_ITEMS += /settings (app-shell.tsx); all referenced + test-covered (test_nav_exposes_settings, test_renders_three_tabs_cache_default)
- [x] DEAD-CODE (code) — no orphaned symbol; OidcConf.client_secret field typed (mirrors gateway schema) but deliberately never read into state (the write-only invariant) — intentional, documented
- [x] SEMANTIC (prose / non-code) — n/a (code task)

ADVERSARIAL VERDICT (this loop): EARNED-WITH-GAPS → security GAPs CLOSED. No DEFECTs.
  CLOSED: test_save_sso now asserts post-save secret input == "" + no "<stored>" in DOM; test_admin_forbidden_sso now asserts no client-secret field + no textbox (ErrorState only).
  ACCEPTED (spec-compliant judgment calls, not defects): (1) guardrails Save sends {enabled:false,…} for an untouched null block — §1 Must "a null block defaults to disabled" + PUT accepts a valid disabled PIConf; (2) useEffect([data]) re-syncs on refetch — same pattern as ModelsPage/TeamsPage; retry:false now added so a settled 403/404 doesn't retry-storm. NITs (Switch label+aria-label dup; key={idx} on pattern rows) — cosmetic, non-blocking.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: ADD auto (autonomy=auto; security surface reviewed CLEAN + test-asserted) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): SSO PUT 422/409 rate (url/encryption misconfig); guardrails 422 rate (custom-pattern validation); cache/guardrails GET 403 rate (a member reaching /settings → NAV role-filtering signal); per-tab GET 500 rate.
Spec delta for the next loop: the write-only-secret re-enter-each-save UX is the one friction point — if owners report re-typing fatigue editing only issuer/urls, that is a change-request to the gateway (partial-update PUT keeping the stored secret), not a UI workaround. NAV currently shows /settings to every role; a member who opens it hits a graceful 403 ErrorState per tab, but role-filtered NAV is the cleaner end-state (carried to feature-coverage-verify).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] A write-only-secret surface needs an EXPLICIT negative DOM assertion (input == "" + no "<stored>" anywhere + no secret field on the role-denied path) — the first-pass tests proved the secret reached the PUT but not that the sentinel stayed out of the DOM; the adversarial refute-read caught the gap (evidence: test_save_sso/test_admin_forbidden_sso strengthened this re-cross).
- [UDD · folded] Deterministic read errors (403/404) must set retry:false on the query — the OIDC tab had it (404=unconfigured) but Cache/Guardrails didn't, so a settled 403 would retry-storm before the inline alert (evidence: retry:false added to all three settings queries for parity).
- [ADD · folded] The re-cross ritual (phase tests → advance → advance) is the correct mechanism to STRENGTHEN already-green tests mid-verify without tripping build_tampered — used here to add 4 security assertions whose behavior already held (evidence: tripwire held, 16/16 still green).
