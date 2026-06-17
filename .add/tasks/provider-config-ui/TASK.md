# TASK: Provider Config Ui

slug: provider-config-ui · created: 2026-06-17 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Task: the tenant-facing **dashboard UI** for BYOK provider credentials — the front end for the
task-4 admin API (`/admin/provider-keys`, commit `de39433`). A tenant OWNER views which of the 6
providers are configured, adds/replaces a provider's credential (provider-discriminated form), and
deletes one — never seeing a stored secret echoed back. Decisions (Tin 2026-06-17): mount as a NEW
"Provider Keys" tab in the existing Settings hub · support ALL 6 providers · list all 6 with status +
a Configure MODAL per provider + Delete confirm.

App: `apps/dashboard` (Next.js 16 App Router, TS, TanStack Query 5.80, shadcn/ui, vitest+RTL+msw).

Touches (files · symbols · signatures):
- **NEW** `components/settings/ProviderKeysSettings.tsx` — the tab content: `useQuery(["admin-provider-keys"], bffGet<{keys: ProviderKeyStatus[]}>("/admin/provider-keys"))`; render the fixed 6-provider list merged with status; OWNER-only (403→ErrorState); open Configure modal / Delete confirm; mutations invalidate the query.
- **NEW** `components/settings/ConfigureProviderDialog.tsx` — provider-discriminated modal form; on submit `bffPut(\`/admin/provider-keys/${provider}\`, body)`; maps 422 → field/global error. Mirrors `keys/CreateKeyDialog.tsx`.
- **MODIFY** `components/settings/SettingsPage.tsx` — add a `<TabsTrigger value="provider-keys">` + `<TabsContent>` rendering `<ProviderKeysSettings/>` (mirrors the existing `sso`→`OidcSettings` tab; lines 28-47).
- **Data layer (EXISTS — reuse)** `lib/bff-client.ts`: `bffGet/bffPut/bffDelete` (credentials:"include"; the BFF proxy attaches the JWT — NO client-side Authorization), `BffError` with `.status` + `.problem.{title,code,status}` (RFC-9457). 204 → undefined; 401 → redirect /login.
- **Backend consumed (task 4, EXISTS — NO change)** `/admin/provider-keys`: GET → `{keys: ProviderKeyStatus[]}`; PUT /{provider} provider-discriminated body → 200 `ProviderKeyStatus{provider,configured,enabled,auth_mode,updated_at}` (no secret) | 422 ERR_PROVIDER_UNKNOWN | 422 ERR_PROVIDER_CREDENTIAL_INCOMPLETE | 403 ERR_AUTH_FORBIDDEN | 409 ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE; GET/{provider} 404 ERR_PROVIDER_KEY_NOT_FOUND; DELETE /{provider} → 204 | 404.

Context (working folder):
- **PRIMARY analog to mirror** — `components/settings/OidcSettings.tsx`: the write-only-secret config tab — `type=password` secret input that STARTS EMPTY and is NEVER prefilled from GET, save BLOCKED if empty, cleared after save, `autoComplete="new-password"`; 404→empty form, 403→`ErrorState`, 200→prefill (except secret); "adjust-state-during-render" seeding guard (no setState in effect); useMutation(bffPut)→setQueryData.
- **SECONDARY analog** — `components/keys/{KeysPage,CreateKeyDialog,KeyRow}.tsx`: list+create+delete CRUD; inline modal (`role="dialog" aria-modal aria-labelledby`) + `useFocusTrap` (`lib/use-focus-trap`); `zod` `safeParse` form validation; states Loading/ErrorState/Empty/Table; delete confirm dialog; queryKey + invalidate.
- **UI kit** `@/components/ui` (index.ts): Tabs/TabsList/TabsTrigger/TabsContent · Input · Button · Switch · Select · Table family · Card · Badge · Loading · ErrorState · Empty · dialog.
- **Tests** `tests/keys.test.tsx` (vitest + RTL + `@testing-library/user-event` + **msw** `http`/`HttpResponse` + `tests/mocks/server` + `tests/mocks/next-navigation`; QueryClient wrapper retry:false; RED via missing import). Gate: `npm run lint` (eslint) · `npm run test` (vitest run) · `npm run build` (next typecheck+build).

Honors (patterns / conventions):
- `"use client"` components; data via TanStack Query + `bff-client` ONLY (no direct fetch to gateway; no Authorization header client-side; no localStorage).
- **Secret hygiene (UI)**: secret inputs are `type=password`, START EMPTY, NEVER prefilled/echoed; status view carries NO secret (backend never returns one); secret cleared from state after a successful save.
- OWNER-only: backend 403 → render `ErrorState`, no form (mirror OidcSettings).
- a11y: modal `role=dialog`/`aria-modal`/`aria-labelledby` + `useFocusTrap` + ESC; errors `role="alert" aria-live="polite"` (the repo runs axe + keys-dialog-a11y tests).
- RFC-9457 errors surfaced via `BffError.problem.title`; 422 → field/inline error.

Anchors the contract cites:
- `ProviderKeysSettings` · `ConfigureProviderDialog` · `SettingsPage` (provider-keys tab)
- `bffGet` · `bffPut` · `bffDelete` · `BffError` · `/admin/provider-keys` · `ProviderKeyStatus`
- `useFocusTrap` · `@/components/ui` (Tabs, Input, Button, Switch, Select, Table, Badge, Loading, ErrorState, Empty)
- the 6 providers: openrouter · openai · anthropic · google · bedrock · azure(api_key|aad)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Provider Keys settings — a tenant OWNER manages their own per-provider BYOK credentials from
the dashboard: see which of the 6 providers are configured, add/replace a provider's credential via a
provider-discriminated modal form, and delete one. Secrets are write-only (entered, sent, never
echoed/prefilled). The front end for the task-4 admin API.

Framings weighed:
  - **New "Provider Keys" tab in the Settings hub + Configure-modal per provider** (chosen) — mirrors
    the SSO/OIDC tab (same security class) and the keys CreateKeyDialog; smallest, most consistent
    surface; all 6 providers always listed with status.
  - Dedicated /provider-keys nav page (rejected — adds a route + nav item; OIDC, the closest analog,
    lives in Settings, so a tab is more consistent).
  - Inline expanding editor per provider (rejected — denser on-screen state; a modal isolates the
    provider-discriminated form and matches CreateKeyDialog).

Must:
<must>
  - The Settings hub shows a "Provider Keys" tab; selecting it renders the provider-keys panel, which
    GETs `/admin/provider-keys` and lists ALL 6 providers (openrouter, openai, anthropic, google,
    bedrock, azure), each showing configured/not, enabled, auth_mode (azure), and updated_at when present.
  - A configured provider shows a "Configured" status (e.g. badge); an unconfigured one shows "Not
    configured". Status is derived ONLY from the GET — no secret appears anywhere in the list.
  - "Configure" on a provider opens a modal with that provider's fields: bearer→{secret}; bedrock→
    {access_key_id, secret_access_key, region, session_token?}; azure→{mode: api_key|aad} then either
    {api_key, endpoint, api_version} or {tenant_id, client_id, client_secret, endpoint, api_version}.
    Optional "Enabled" toggle (default on). Submitting PUTs `/admin/provider-keys/{provider}` and, on
    200, closes the modal, refreshes the list (now Configured), and clears all secret fields.
  - Every secret field is `type=password`, starts EMPTY, is NEVER prefilled from any GET, and is
    cleared from state after a successful save (write-only — mirror OidcSettings).
  - "Delete" on a configured provider asks for confirmation; confirming DELETEs
    `/admin/provider-keys/{provider}` and, on 204, refreshes the list (now Not configured).
  - Loading shows a loading state; a non-403 GET error shows an error state with the problem title.
  - All gateway calls go through `bff-client` (cookie auth via the BFF proxy) — the UI never reads or
    constructs a bearer token.
</must>
Reject:
<reject>
  - GET/PUT/DELETE returns 403 (non-owner) -> render `ErrorState` with the problem title; NO form/list
    shown, NO mutation possible (OWNER-only, backend-enforced).
  - Submitting an incomplete provider body -> backend 422 "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" ->
    surface the problem title as an inline form error; modal STAYS open; list unchanged.
  - A client-side empty required secret (e.g. bearer secret blank) -> block submit with an inline
    "required" error; NO request is made.
  - PUT returns 409 "ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE" (platform misconfig) -> surface the
    problem title as a form error; modal stays open; list unchanged.
  - Any mutation error (4xx/5xx) -> the secret fields are NOT cleared (so the user can retry) and the
    list is not optimistically changed.
</reject>
After:
<after>
  - After a successful Configure: the provider shows "Configured" (+ enabled/auth_mode), the modal is
    closed, and no secret value exists in any component state or the DOM.
  - After a successful Delete: the provider shows "Not configured" and is absent from the GET result.
  - At no point does a plaintext secret appear in the rendered list, a status badge, or a prefilled
    input; the only place a secret exists is the password input the user is actively typing into.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The provider-discriminated MODAL form (one dialog whose visible fields switch on provider, and for
    azure on a mode select) is the genuinely new surface. Lowest confidence: the azure api_key|aad
    mode toggle inside the modal (a `Select`/segmented control that swaps field sets) is the trickiest
    interaction to model + test cleanly. If wrong: re-shape `ConfigureProviderDialog` + its tests only
    (the list panel, data layer, and backend are untouched). Recommend: a single modal that branches
    its rendered fields on `provider` (and `mode` for azure), submitting the matching JSON body.
  - [ ] List shows ALL 6 providers always (configured + unconfigured), not just configured ones —
    derived by merging a static 6-provider list with the GET statuses. Recommend: yes (fixed known set).
  - [ ] No client-side URL/format validation of azure endpoint/api_version beyond "required" — rely on
    the backend value-object validator for completeness (422 → inline error). Recommend: yes (single
    source of truth; mirror how CreateKeyDialog leans on the backend for domain rules).
  - [ ] "Enabled" is folded into the Configure modal (a Switch, default on); no separate enable/disable
    control in the list row in v25. Recommend: fold in (matches the backend PUT `enabled` field).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner sees all six providers with status                          # M1
  Given the owner is on Settings and GET /admin/provider-keys returns openrouter configured+enabled
  When the owner opens the "Provider Keys" tab
  Then all six providers are listed
  And openrouter shows "Configured" while the other five show "Not configured"
  And no secret value appears anywhere in the list

Scenario: Owner configures a bearer provider                                 # M2
  Given the owner is on the Provider Keys tab and openai is not configured
  When the owner clicks Configure on openai, enters a secret, and saves (PUT 200)
  Then the modal closes, the list refetches, and openai shows "Configured"
  And the secret input value is no longer present in the DOM or state

Scenario: Owner configures bedrock with all fields                           # M3
  Given the owner clicks Configure on bedrock
  When the owner fills access_key_id, secret_access_key, region (and optional session_token) and saves
  Then the PUT body carries those fields and on 200 bedrock shows "Configured"

Scenario: Owner configures azure in aad mode                                 # M4
  Given the owner clicks Configure on azure and selects mode "aad"
  When the owner fills tenant_id, client_id, client_secret, endpoint, api_version and saves
  Then the PUT body has mode:"aad" + those fields and on 200 azure shows "Configured" with auth_mode "aad"

Scenario: Owner switches azure to api_key mode                               # M5
  Given the owner clicks Configure on azure and selects mode "api_key"
  Then the form shows api_key/endpoint/api_version fields (not the aad fields)
  And saving PUTs mode:"api_key" with those fields

Scenario: Owner deletes a configured provider                                # M6
  Given openrouter is configured and the owner clicks Delete then confirms (DELETE 204)
  When the list refetches
  Then openrouter shows "Not configured"
  And it is absent from the GET result

Scenario: Secret is never prefilled when re-configuring                      # M7
  Given azure is already configured (GET shows it configured, no secret)
  When the owner clicks Configure on azure again
  Then every secret field (client_secret/api_key) is EMPTY, never prefilled from any response

Scenario: Incomplete body is rejected inline                                 # R1
  Given the owner is configuring azure in aad mode and omits client_secret on the server side
  When the PUT returns 422 ERR_PROVIDER_CREDENTIAL_INCOMPLETE
  Then the modal stays open and shows the problem title as an inline error
  And the provider's list status is unchanged

Scenario: Empty required secret blocks the request                           # R2
  Given the owner is configuring openai and leaves the secret blank
  When the owner clicks Save
  Then an inline "required" error is shown
  And NO PUT request is made

Scenario: Non-owner is denied                                               # R3
  Given GET /admin/provider-keys returns 403 ERR_AUTH_FORBIDDEN (admin/member)
  When the Provider Keys tab renders
  Then an error state with the problem title is shown
  And no provider list and no Configure control are rendered

Scenario: Missing-encryption-key surfaces as a form error                    # R4
  Given the owner saves a provider and the PUT returns 409 ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE
  Then the modal stays open and shows the problem title
  And the list is unchanged and the secret fields are not cleared

Scenario: A failed save preserves the entered secret for retry              # R5
  Given the owner saves and the PUT returns a 5xx error
  Then the secret fields retain what the user typed (not cleared)
  And the list is not optimistically marked Configured
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

UI component contract (no new HTTP endpoints — consumes task-4's `/admin/provider-keys`).

```
SettingsPage.tsx — add a tab:
  <TabsTrigger value="provider-keys">Provider Keys</TabsTrigger>
  <TabsContent value="provider-keys"><ProviderKeysSettings/></TabsContent>   # mirrors the sso tab

ProviderKeysSettings()  ("use client")
  PROVIDERS = ["openrouter","openai","anthropic","google","bedrock","azure"]   # fixed render order
  query:  useQuery(["admin-provider-keys"], () => bffGet<{keys: ProviderKeyStatus[]}>("/admin/provider-keys"), {retry:false})
    ProviderKeyStatus = { provider, configured:boolean, enabled:boolean, auth_mode:string|null, updated_at:string }
  states:
    isLoading            -> <Loading/>
    403 (BffError.status) -> <ErrorState title={problem.title}/>   # owner-only; NO list, NO controls
    other GET error       -> <ErrorState title={problem.title}/>
    success               -> render all 6 rows: merge PROVIDERS with statusByProvider(data.keys)
                             configured row -> "Configured" badge (+ enabled, auth_mode, updated_at) + [Configure][Delete]
                             unconfigured   -> "Not configured" + [Configure]
  saveMutation:   bffPut<ProviderKeyStatus>(`/admin/provider-keys/${provider}`, body)
                  onSuccess -> close modal, invalidate ["admin-provider-keys"], clear secret fields
  deleteMutation: bffDelete(`/admin/provider-keys/${provider}`)  onSuccess -> invalidate query
                  Delete is guarded by a confirm dialog (role=dialog, useFocusTrap) before firing.

ConfigureProviderDialog({ provider, isOpen, onClose, onSubmit })  — inline modal (role=dialog,
  aria-modal, aria-labelledby, useFocusTrap, ESC closes). Fields branch on provider:
    bearer (openrouter|openai|anthropic|google): secret*                       # password
    bedrock: access_key_id*, secret_access_key*(password), region*, session_token?(password)
    azure: mode select (api_key|aad) ->
       api_key: api_key*(password), endpoint*, api_version*
       aad:     tenant_id*, client_id*, client_secret*(password), endpoint*, api_version*
    + Enabled switch (default true).  ALL password fields start EMPTY, never prefilled.
  submit builds the matching JSON body and calls onSubmit -> saveMutation.mutate.
    client-side: a blank required secret blocks submit with an inline "required" error (no request).
    on BffError: 422/409 -> inline form error from problem.title; modal stays open; secret NOT cleared.

PUT body by provider (sent to /admin/provider-keys/{provider}):
  bearer  : { secret, enabled? }
  bedrock : { access_key_id, secret_access_key, region, session_token?, enabled? }
  azure   : { mode:"api_key", api_key, endpoint, api_version, enabled? }
          | { mode:"aad", tenant_id, client_id, client_secret, endpoint, api_version, enabled? }
Errors surfaced (RFC-9457 via BffError.problem.title): 403 ERR_AUTH_FORBIDDEN ·
  422 ERR_PROVIDER_CREDENTIAL_INCOMPLETE · 422 ERR_PROVIDER_UNKNOWN · 409 ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE.
Data layer: bff-client only (credentials:"include"; BFF proxy attaches JWT). No new types file required —
  ProviderKeyStatus + body types live in the two new component files (mirrors OidcSettings inline types).
```

Least-sure flag surfaced at freeze: [contract] the provider-discriminated modal — ONE
`ConfigureProviderDialog` whose visible fields branch on `provider` (and, for azure, on a `mode`
select that swaps the api_key vs aad field set), building the matching JSON body. The azure dual-mode
toggle is the trickiest single point. If wrong: re-shape `ConfigureProviderDialog` + its tests ONLY
(the list panel, the data layer, and the task-4 backend are untouched). [scenario] secondary: the
list always renders all 6 providers (configured ∪ unconfigured) by merging a static list with the GET
statuses — if product later wants "configured only", that's a render-filter change, additive.

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-17
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (the two new components)
Plan (one test per scenario; vitest + RTL + user-event + msw — mirror `apps/dashboard/tests/keys.test.tsx`:
QueryClient wrapper retry:false, msw handlers for `/api/gw/admin/provider-keys*`, RED via the missing
`@/components/settings/ProviderKeysSettings` import; assert observable DOM/behavior, never internals):
<test_plan>
  - test_M1_lists_six_with_status: GET returns openrouter configured → all 6 rendered, openrouter "Configured", others "Not configured", no secret text anywhere
  - test_M2_configure_bearer: Configure openai → type secret → Save → msw asserts PUT /admin/provider-keys/openai {secret}; on 200 modal closes, refetch shows openai Configured; secret input gone from DOM
  - test_M3_configure_bedrock: Configure bedrock → fill 4 fields → Save → PUT body has access_key_id/secret_access_key/region/session_token
  - test_M4_configure_azure_aad: Configure azure → mode aad → fill fields → Save → PUT body {mode:"aad",tenant_id,client_id,client_secret,endpoint,api_version}; row shows auth_mode aad
  - test_M5_azure_mode_toggle: Configure azure → switching mode api_key↔aad swaps the visible field set (api_key vs tenant_id/client_id/client_secret)
  - test_M6_delete_confirmed: Delete openrouter → confirm → msw asserts DELETE /admin/provider-keys/openrouter; on 204 refetch shows Not configured
  - test_M7_secret_never_prefilled: azure configured → Configure → client_secret/api_key inputs are empty (value==="")
  - test_R1_incomplete_422_inline: PUT → 422 ERR_PROVIDER_CREDENTIAL_INCOMPLETE → inline error with problem title; modal still open; row status unchanged
  - test_R2_empty_secret_blocks: openai Configure, blank secret, Save → inline "required" error AND msw records ZERO PUT calls
  - test_R3_forbidden_403: GET → 403 → ErrorState with title; no provider rows, no Configure button
  - test_R4_encryption_unavailable_409: PUT → 409 ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE → inline error; modal open; list unchanged
  - test_R5_failed_save_keeps_secret: PUT → 500 → secret input still holds typed value; row not marked Configured
  - test_WIRING_tab_registered: SettingsPage renders a "Provider Keys" tab that mounts ProviderKeysSettings (the panel's GET fires when the tab is activated)
</test_plan>

Tests live in: `apps/dashboard/tests/provider-keys.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/settings/ProviderKeysSettings.tsx` · `apps/dashboard/components/settings/ConfigureProviderDialog.tsx` · `apps/dashboard/components/settings/SettingsPage.tsx`
Strategy (ordered batches):
  1. ConfigureProviderDialog (provider-discriminated form + password fields + zod required-guard + error mapping).
  2. ProviderKeysSettings (useQuery list of 6, status merge, save/delete mutations, confirm dialog, states).
  3. SettingsPage — add the Provider Keys tab trigger + content.
Safety rule (feature-specific): secret inputs are `type=password`, start empty, are NEVER prefilled
  from any GET, and are cleared from state only on a SUCCESSFUL save (kept on error for retry). No
  secret is ever rendered into the list, a badge, or a value attribute.
Code lives in: `apps/dashboard/components/settings/`
Constraints: do NOT change any test or the contract; reuse existing deps only (TanStack Query, zod,
  @/components/ui, bff-client — no new packages); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
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

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
