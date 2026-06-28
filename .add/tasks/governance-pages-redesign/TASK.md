# TASK: Redesign governance pages (keys·members·routing·alerts·audit·settings) to the refreshed standard

slug: governance-pages-redesign · created: 2026-06-28 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): the six authenticated governance pages — each a `"use client"` component behind a thin server route `app/(app)/app/<page>/page.tsx` (`metadata`+`return <XPage/>`), EXCEPT members whose route file is itself `"use client"` and owns the `["current-user"]` (`bffAuthGet("me")`) query it passes down. Each page hand-rolls its `<h1>`; NONE use the new `PageHeader`; only SETTINGS already uses `Tabs`.
- `components/keys/KeysPage.tsx` — h1 "API Keys". MOST COMPLEX. Queries `["admin-keys"]`→`/admin/keys` (fields key_id·name·prefix·created_at·revoked_at·monthly_budget_usd·soft_budget_usd·expires_at·model_allowlist·rpm_limit·tpm_limit·team_id·cache_enabled), plus child panels' own queries `["admin-teams"]`/`["admin-ratelimits"]`/`["admin-bandwidth"]`. Mutations: POST `/admin/keys` · DELETE `/admin/keys/{id}` · PATCH `/admin/keys/{id}` · POST `/admin/keys/{id}/rotate`. Children: `CreateKeyDialog` (role=dialog "Create API key"), revoke confirm (`data-testid="revoke-overlay"`, dialog "Confirm revocation"), `KeyGovernanceEditor` (`data-testid="key-governance-editor"`, rotate dialog "Confirm key rotation", testids monthly-budget-input·soft-budget-input·expires-at-input·model-allowlist-input·add-model-button·current-monthly-budget), `RatelimitsPanel`, `BandwidthPanel`. Loading `data-testid="loading"`.
- `components/members/MembersPage.tsx` — h1 "Members" (`id="members-heading"` inside `<section role=region aria-labelledby="members-heading">`). `["admin-users"]`→`/admin/users` (users[].id·email·role). Mutation PUT `/admin/users/{id}/role`. Per-row `<select aria-label="Assign role to {email}">`; own row shows "(your account)". DataTable `ariaLabel="Members"` emptyMessage "No members yet."
- `components/routing/RoutingPage.tsx` — h1 "Routing health" + description. `["admin-routing"]`(retry:false)→`/admin/routing` (routing_strategy·retry_policy·cooldown·model_groups·deployments·candidates[].{model_id,alias,state}). Cards: Retry policy·Cooldown·Model groups·Candidate circuit state (DataTable `aria-label="Routing candidates and their circuit state"`, Badge text closed/open/half_open/unknown). `RoutingEditor` (role-gated via `useCurrentUser()`; `<section aria-labelledby="routing-editor-heading">`, strategy select, dynamic alias/deployment rows, Save, role=alert client error, role=status "Saved — restart…"). Mutation PUT `/admin/routing`. Loading/error are FULL-PAGE early returns.
- `components/alerts/AlertsPage.tsx` — h1 "Alerts" (`id="alerts-heading"`, `<section role=region>`). `["admin-alerts"]`→`/admin/alerts` (items[].{id,event_type,payload,created_at,delivered,delivered_at}·total). `AlertsTable`→DataTable `ariaLabel="Alert events"` empty "No alerts yet" (cols Type·When·Status[Delivered/Pending]·Payload). NO mutations.
- `components/audit/AuditPage.tsx` — h1 "Audit Log" (`id="audit-heading"`, `<section role=region>`). `["admin-audit"]`→`/admin/audit` (items[].{id,actor_email,action,target_type,target_id,result,metadata,created_at}·total). `AuditTable`→DataTable `ariaLabel="Audit events"` empty "No audit events yet" (cols Actor·Action·Target·Result·When). NO mutations. Structurally identical to Alerts.
- `components/settings/SettingsPage.tsx` — h1 "Settings" + description. ALREADY tabbed `Tabs defaultValue="cache"`: Cache(`["admin-cache"]`)/Guardrails(`["admin-guardrails"]`)/SSO(`["admin-oidc"]`)/Provider Keys(`["admin-provider-keys"]`), each a sub-component with its own query(retry:false)+PUT. OIDC SECURITY INVARIANT: `client_secret` returns sentinel `"<stored>"`, NEVER prefilled into the password input; save blocked when secret empty+no prior config; secret cleared after save. Provider order openrouter·openai·anthropic·google·bedrock·azure; `<li data-provider>`; delete confirm `data-testid="delete-provider-overlay"`.
- SHARED KIT (consume, don't fork): `components/ui/page-header.tsx` `PageHeader{title,description?,actions?,className?,titleId?}` (NEW — one `<header>`, exactly one h1, token utilities only); `components/ui/tabs.tsx` Tabs/TabsList/TabsTrigger/TabsContent (inactive TabsContent → null, so a child query fires only when its tab mounts; tablist roving tabindex+arrows); `components/ui/stat-card.tsx` `StatCard{label,value,delta?,icon?,footer?,valueTestId?}` (`data-slot="stat-card"`); `components/ui/data-table.tsx` `DataTable{columns,data,caption?,emptyMessage?,ariaLabel?,searchable?,searchKeys?,pageSizeOptions?}` (zero rows → `<Empty title=emptyMessage>`); `components/ui/states.tsx` Loading(role=status,aria-busy)/ErrorState(role=alert)/Empty/Success.
Context (working folder): `.add/milestones/v54/MILESTONE.md` (task `governance-pages-redesign` → keys·members·routing·alerts·audit·settings; deps aurora-polish-tokens + responsive-app-shell both DONE) · the just-shipped `monitoring-pages-redesign` (PageHeader+hero+tabbed-IA precedent, `tmp/monitoring-build-spec.md`, captures `.add/design/captures/monitoring-*.png`) · `apps/dashboard/vitest.config.ts` (two projects `tests/`+`tests-bff/`, both base http://localhost:3000, coverage ≥80%).
Honors (patterns / conventions): PROJECT.md UDD invariants — 3-layer DTCG tokens fail-closed · byte-identical data seams · four UI states · WCAG 2.2 AA · design-before-code. CONVENTIONS.md — exactly one `<h1>`/route · decorative icons `aria-hidden` · `role=status`/`role=alert` · scope assertions via `within(<section>)`. v54 shared decisions — byte-identical seams (query keys/BFF paths/field names/frozen testids inviolable) · token-led no-hardcode (`add.py check` fail-closed) · four-state from `states.tsx` · native `<select>` preserved · relocated assertion reached by navigation (userEvent.click(tab)), NEVER weakened. SECURITY: OIDC write-only secret invariant is inviolable.
Anchors the contract cites: `PageHeader` · `KeysPage` · `MembersPage` · `RoutingPage` · `AlertsPage` · `AuditPage` · `SettingsPage` · `StatCard` · `Tabs`/`TabsContent` · `DataTable` · `states.tsx`(Loading/ErrorState/Empty) · the frozen seam set (query keys admin-keys/admin-teams/admin-ratelimits/admin-bandwidth/admin-users/admin-routing/admin-alerts/admin-audit/admin-cache/admin-guardrails/admin-oidc/admin-provider-keys; testids loading/revoke-overlay/rotate-overlay/key-governance-editor/monthly-budget-input/soft-budget-input/expires-at-input/model-allowlist-input/add-model-button/current-monthly-budget/delete-provider-overlay/data-provider; region headings members-heading/alerts-heading/audit-heading/routing-editor-heading; DataTable ariaLabels Members/Alert events/Audit events/"Routing candidates…").

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: The six authenticated governance pages (keys · members · routing · alerts · audit · settings) redesigned to the refreshed standard — a shared PageHeader, all four UI states, and a per-page-fit hero + tabbed IA — with every data seam and frozen test hook byte-identical.
Framings weighed: Same-as-monitoring, per-page-fit — PageHeader+states everywhere; hero+tabs only where the page warrants (chosen, Tin) · Lighter (PageHeader+states only) · Heaviest (tabs+hero forced on all six)
Must:
<must>
  - All six pages render their heading through the shared `PageHeader` (EXACTLY one h1 per page; pinned heading text preserved — "API Keys" · "Members" · "Routing health" · "Alerts" · "Audit Log" · "Settings"; routing + settings keep their description text).
  - All four UI states present on every page, composed from `states.tsx` (Loading role=status · ErrorState role=alert · Empty · Success) — no hand-rolled replacements.
  - KEYS: PageHeader title="API Keys" with the Create-key button in the actions slot (same role/visibility gating as today — hidden while a dialog is open) + a hero region (`data-testid="keys-hero"`) showing the active-key count derived from the already-fetched `["admin-keys"]` data + Tabs default "keys": **Keys** (the keys table + inline `KeyGovernanceEditor` rows) / **Rate limits** (`RatelimitsPanel`) / **Bandwidth** (`BandwidthPanel`). The Create / revoke / rotate dialogs + `PlaintextKeyBanner` stay mounted OUTSIDE the tabs.
  - ROUTING: PageHeader title="Routing health" (description preserved) + a hero region (`data-testid="routing-hero"`) showing `routing_strategy` + circuit health (N healthy / M candidates, derived from `candidates[].state`) + Tabs default "overview": **Overview** (the Retry policy / Cooldown / Model groups / Candidate circuit-state cards) / **Editor** (`RoutingEditor`). The Editor tab is present ONLY for owner/admin (same `useCurrentUser()` gate as today — a non-admin sees Overview only, no empty Editor tab).
  - SETTINGS: PageHeader title="Settings" (description preserved) REPLACES the hand-rolled header; KEEP the existing 4 tabs (Cache / Guardrails / SSO / Provider Keys), every sub-component query+mutation seam, and the OIDC write-only-secret invariant intact. No hero (no single headline metric).
  - MEMBERS / ALERTS / AUDIT: PageHeader rendering the h1 via `titleId` = the frozen heading id (`members-heading` / `alerts-heading` / `audit-heading`) INSIDE the existing `<section role=region aria-labelledby=…>`, so the region→heading association survives; KEEP the existing DataTable + its ariaLabel/emptyMessage. NO hero.
  - Every query key, BFF path, response field, mutation path, and frozen test hook (all `data-testid`s, region headings, DataTable ariaLabels, dialog accessible-names, governance-editor input labels) stays BYTE-IDENTICAL. A relocated assertion is reached by `userEvent.click(<tab>)`, never by loosening it.
  - No hardcoded token-covered value (R3; `add.py check` lints the 3-layer DTCG set fail-closed); reuse the primitive kit (PageHeader · Tabs · StatCard · Card · DataTable · states.tsx); recharts already a dep; NO new dependency.
</must>
Reject:
<reject>
  - A data seam (query key · BFF path · response field · mutation path) changed, or a frozen testid/aria-label renamed -> "seam_drift"
  - A test weakened, deleted, or its assertion target loosened (instead of navigation) to make the build pass -> "test_weakened"
  - A fabricated or placeholder metric rendered where the seam carries no such data (heroes derive ONLY from already-fetched fields) -> "fabricated_metric"
  - OIDC `client_secret` prefilled into any input, or the `"<stored>"` sentinel leaked into an editable field -> "secret_leak"  (HARD-STOP)
  - More than one `<h1>` on any page -> "multiple_h1"
  - A raw hex / `NNpx` value a token covers introduced in a `components/ui/*` file -> "untokened_value"
</reject>
After:
<after>
  - All six pages share the PageHeader structure; keys + routing carry a hero; keys (Keys/Rate limits/Bandwidth) + routing (Overview/Editor) are tabbed; settings keeps its 4 tabs; members/alerts/audit are PageHeader + table.
  - The full dashboard suite (legacy `tests/` + `tests-bff/`) is green, `tsc --noEmit` clean, `eslint .` 0 errors, and `add.py check` shows no scope / seam / secret finding for the task.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The keys tab reorg leaves the keys-list / create / revoke / rotate / governance-editor tests green on the DEFAULT "keys" tab AND the standalone Rate-limits / Bandwidth panel suites unaffected — lowest confidence because keys is the most-wired page (4 queries, 4 mutations, 3 focus-trap dialogs); if wrong: keys.test/keys-dialog-a11y co-evolution balloons or a dialog focus-trap breaks under the tab DOM. (Ground evidence FOR: `bandwidth.test`/`ratelimits.test` render the panels standalone — not via KeysPage; `keys.test` asserts the keys table = default tab; dialogs stay mounted outside the tabs.)
  - [ ] routing Editor-as-a-tab keeps the role-gate honest — the Editor tab renders ONLY for owner/admin; a non-admin sees Overview only (no empty/locked Editor tab). Confirm the conditional-tab reads cleanly and no routing test expects the editor for a viewer.
  - [ ] members/alerts/audit PageHeader-with-titleId preserves `getByRole("region",{name})` — the section keeps `aria-labelledby` pointing at the PageHeader-owned h1 id. Confirm the association survives the header swap.
  - [ ] settings needs no hero and only swaps its header for PageHeader — confirm no `tenant-settings`/`provider-keys` test asserts the old hand-rolled header DOM (vs the tablist/sub-component seams, which are preserved).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: every governance page renders one PageHeader h1
  Given any of the six pages is rendered with data loaded
  When the page mounts
  Then exactly one <h1> appears, rendered by PageHeader, with the page's pinned title text
  And the query key + BFF path the page calls are unchanged

Scenario: keys page tabs Keys / Rate limits / Bandwidth
  Given the keys page is rendered with keys, ratelimits, and bandwidth data
  When the page mounts
  Then a tablist shows Keys (selected) / Rate limits / Bandwidth and the keys table is visible
  When the user clicks the "Rate limits" tab
  Then the RatelimitsPanel content is shown
  And the keys query key/path and the Create/revoke/rotate dialog hooks are unchanged

Scenario: keys hero shows active-key count
  Given the keys page is rendered with N non-revoked keys
  When the page mounts
  Then a region data-testid="keys-hero" shows the active-key count derived from ["admin-keys"]
  And no value is fabricated beyond the fetched keys data

Scenario: keys dialogs survive the tab DOM
  Given the keys page is rendered
  When the user opens the Create / revoke / rotate dialog
  Then the dialog (role=dialog, frozen accessible-name) opens and traps focus as today
  And it is reachable regardless of the active tab (mounted outside the tabs)

Scenario: routing hero + Overview/Editor tabs, editor role-gated
  Given the routing page is rendered for an owner with routing data
  When the page mounts
  Then a region data-testid="routing-hero" shows routing_strategy + N healthy/M candidates
  And a tablist shows Overview (selected) / Editor; clicking Editor shows RoutingEditor
  And the ["admin-routing"] seam + candidate table ariaLabel are unchanged

Scenario: routing editor tab hidden for a viewer
  Given the routing page is rendered for a non owner/admin user
  When the page mounts
  Then only the Overview tab exists (no Editor tab, no empty/locked editor)
  And the read-side cards + candidate table render unchanged

Scenario: settings keeps its four tabs under a PageHeader
  Given the settings page is rendered
  When the page mounts
  Then PageHeader shows "Settings" + description and the Cache/Guardrails/SSO/Provider Keys tablist is intact
  And every sub-component query+mutation seam and the OIDC write-only-secret invariant are unchanged

Scenario: members/alerts/audit keep region heading under PageHeader
  Given the members (or alerts/audit) page is rendered
  When the page mounts
  Then getByRole("region",{name}) still resolves via the section's aria-labelledby → the PageHeader h1 id
  And the DataTable ariaLabel + emptyMessage are unchanged

Scenario: every page renders all four UI states
  Given a governance page in loading / error / empty / success
  When each state renders
  Then Loading=role=status, ErrorState=role=alert, Empty=the page's emptyMessage, Success=content — from states.tsx
  And the state seams (testid "loading", error titles) are unchanged

# ---- rejections ----

Scenario: seam drift is rejected
  Given the redesign of any page
  When a query key, BFF path, response field, mutation path, or frozen testid would change
  Then that is "seam_drift" and is not allowed
  And the original seam/testid stays byte-identical

Scenario: weakening a test is rejected
  Given a frozen assertion target moved behind a tab
  When the build makes it pass
  Then it must navigate (userEvent.click(tab)), not loosen/delete the assertion ("test_weakened")
  And the assertion target stays the same

Scenario: fabricated hero metric is rejected
  Given a page whose seam carries no headline metric (members/alerts/audit)
  When the redesign is applied
  Then no hero with an invented number is rendered ("fabricated_metric")
  And those pages stay PageHeader + table

Scenario: OIDC secret leak is rejected
  Given the settings SSO tab with a stored client_secret ("<stored>" sentinel)
  When the form renders
  Then the secret is NEVER prefilled into any input ("secret_leak" = HARD-STOP)
  And the write-only-secret save rule is unchanged

Scenario: multiple h1 is rejected
  Given a redesigned page
  When it renders
  Then it must contain exactly one <h1> ("multiple_h1" otherwise)
  And the PageHeader owns that single h1

Scenario: untokened value is rejected
  Given an edit to a components/ui/* file
  When a raw hex or NNpx a token covers is introduced
  Then add.py check flags "untokened_value" fail-closed
  And only token utilities are used
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

UI structural contract (no API/DB change — every data seam BYTE-IDENTICAL). Frozen shape per page:

```
SHARED
  PageHeader{title, description?, actions?, titleId?}  → one <header> with EXACTLY one <h1> (id=titleId)
  Hero convention: data-testid="<page>-hero" (ONLY keys + routing); derived from already-fetched data
  Tabs: tabs.tsx; default tab renders first; inactive TabsContent → null (relocated testid reached by click)
  States: states.tsx Loading(role=status)/ErrorState(role=alert)/Empty/Success — unchanged seams

KEYS  (components/keys/KeysPage.tsx)
  PageHeader title="API Keys"  actions={Create-key button — getByRole button /create key/i, hidden while a dialog open}
  data-testid="keys-hero" → active-key count (non-revoked) from ["admin-keys"]→GET /admin/keys
  Tabs default "keys": [Keys] keys table + inline KeyGovernanceEditor(data-testid="key-governance-editor")
                       [Rate limits] RatelimitsPanel(["admin-ratelimits"])
                       [Bandwidth]   BandwidthPanel(["admin-bandwidth"])
  OUTSIDE tabs (always mounted): CreateKeyDialog(role=dialog "Create API key"), revoke confirm
    (data-testid="revoke-overlay", dialog "Confirm revocation"), PlaintextKeyBanner, Loading(data-testid="loading")
  FROZEN seams: ["admin-keys"]/["admin-teams"]/["admin-ratelimits"]/["admin-bandwidth"];
    POST /admin/keys · DELETE /admin/keys/{id} · PATCH /admin/keys/{id} · POST /admin/keys/{id}/rotate;
    testids monthly-budget-input·soft-budget-input·expires-at-input·model-allowlist-input·add-model-button·
    current-monthly-budget·rotate-overlay; dialog "Confirm key rotation"

MEMBERS  (components/members/MembersPage.tsx)
  <section role=region aria-labelledby="members-heading"> → PageHeader title="Members" titleId="members-heading"
  NO hero. DataTable ariaLabel="Members" emptyMessage="No members yet."; per-row <select aria-label="Assign role to {email}">
  FROZEN: ["admin-users"]→GET /admin/users; PUT /admin/users/{id}/role; "(your account)"; ["current-user"] in route

ROUTING  (components/routing/RoutingPage.tsx)
  PageHeader title="Routing health" description=<existing>
  data-testid="routing-hero" → routing_strategy + "N healthy / M candidates" (from candidates[].state, state!=="open"=healthy)
  Tabs default "overview": [Overview] Retry policy·Cooldown·Model groups·Candidate circuit-state cards
                           [Editor]   RoutingEditor — TAB PRESENT ONLY for owner/admin (useCurrentUser)
  FROZEN: ["admin-routing"](retry:false)→GET /admin/routing; PUT /admin/routing;
    candidate DataTable ariaLabel="Routing candidates and their circuit state"; Badge text closed/open/half_open/unknown;
    routing-editor-heading; role=alert client error; role=status "Saved — restart…"; /restart/ notice

ALERTS  (components/alerts/AlertsPage.tsx)
  <section role=region aria-labelledby="alerts-heading"> → PageHeader title="Alerts" titleId="alerts-heading"
  NO hero. AlertsTable→DataTable ariaLabel="Alert events" emptyMessage="No alerts yet" (cols Type·When·Status·Payload)
  FROZEN: ["admin-alerts"]→GET /admin/alerts; "Delivered"/"Pending"

AUDIT  (components/audit/AuditPage.tsx)
  <section role=region aria-labelledby="audit-heading"> → PageHeader title="Audit Log" titleId="audit-heading"
  NO hero. AuditTable→DataTable ariaLabel="Audit events" emptyMessage="No audit events yet" (cols Actor·Action·Target·Result·When)
  FROZEN: ["admin-audit"]→GET /admin/audit

SETTINGS  (components/settings/SettingsPage.tsx)
  PageHeader title="Settings" description=<existing> REPLACES the hand-rolled header
  KEEP Tabs defaultValue="cache": Cache/Guardrails/SSO/Provider Keys (every sub-component seam unchanged)
  NO hero. FROZEN: ["admin-cache"]/["admin-guardrails"]/["admin-oidc"]/["admin-provider-keys"] + their PUTs;
    OIDC write-only secret invariant ("<stored>" never in an input, save blocked when empty+no prior config,
    secret cleared after save); provider order openrouter·openai·anthropic·google·bedrock·azure;
    <li data-provider>; data-testid="delete-provider-overlay"
```

Schema: NONE — UI-only. No tables/fields/migrations touched; all data flows through the existing BFF query/mutation seams above, byte-identical.

Status: FROZEN @ v1 — approved by Tin
Least-sure flag surfaced at freeze: [contract/test] the keys tab reorg leaves the keys-list/create/revoke/rotate/governance-editor tests green on the DEFAULT "keys" tab AND the standalone Rate-limits/Bandwidth panel suites untouched — keys is the most-wired page (4 queries, 4 mutations, 3 focus-trap dialogs); if wrong, keys co-evolution balloons or a dialog focus-trap breaks under the tab DOM. Ground evidence FOR: bandwidth/ratelimits tests render the panels standalone (not via KeysPage), keys-table tests assert the default tab, dialogs mount outside the tabs. (Design-confirm: keys·settings·audit mocks captured + approved by Tin; audit Export/filter + settings 3rd toggle are mock flourishes excluded from the byte-identical build.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥80% lines (project gate); new structure at parity or better. Asserts structure + state-presence, never internals.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  NEW — structural suite `tests/design-system/governance-redesign.test.tsx`:
  - test_each_governance_page_uses_page_header: keys/members/routing/alerts/audit/settings each render their PINNED h1 (API Keys/Members/Routing health/Alerts/Audit Log/Settings) via PageHeader; EXACTLY one <h1>. (Must 1, Reject multiple_h1)
  - test_keys_hero_and_tabs: keys-hero present (active-key count from ["admin-keys"]) + tablist Keys(default)/Rate limits/Bandwidth; click "Rate limits" → RatelimitsPanel content; click "Bandwidth" → BandwidthPanel content. (Must keys)
  - test_keys_dialogs_outside_tabs: Create/revoke dialog reachable regardless of active tab (mounted outside tabs); role=dialog accessible-names unchanged. (Must keys)
  - test_routing_hero_and_tabs_owner: routing-hero (routing_strategy + "N healthy / M candidates") + tablist Overview(default)/Editor for owner; click Editor → RoutingEditor. (Must routing)
  - test_routing_editor_tab_hidden_for_viewer: a non owner/admin user sees ONLY the Overview tab (no Editor); read-side cards render. (Must routing, Reject — honest role-gate)
  - test_settings_pageheader_keeps_four_tabs: Settings PageHeader (title+description) + tablist Cache(default)/Guardrails/SSO/Provider Keys intact. (Must settings)
  - test_simple_pages_no_hero: members/alerts/audit expose NO data-testid="*-hero"; getByRole("region",{name}) still resolves via the preserved heading. (Must members/alerts/audit, Reject fabricated_metric)
  - test_no_untokened_value: page files contain no raw hex/'NNpx' a token covers. (Reject untokened_value)
  NEW — a11y cases (extend `tests/design-system/a11y.test.tsx` or in the new suite): tablist roving arrow-keys + 0 serious/critical axe (color-contrast off) on keys + routing + settings. (Must a11y)
  CO-EVOLVED (navigation added, seam/asserts unchanged — Reject test_weakened):
  - tests/routing-edit.test.tsx + tests-bff/routing-health.test.tsx: RoutingEditor assertions → click the /editor/i tab first; Overview cards/candidate table/empty/loading/error stay default/page-level.
  - tests/keys.test.tsx + tests/keys-dialog-a11y.test.tsx: keys table=default tab, dialogs outside tabs → expected NO nav; add nav ONLY if a real target moved (verify after build).
  - tests-bff/tenant-settings.test.tsx + tests/provider-keys.test.tsx: tabs unchanged → expected NO nav; co-evolve ONLY if a test asserted the old hand-rolled header DOM (assert via PageHeader h1 instead).
  - tests/members.test.tsx · tests/alerts.test.tsx · tests/audit.test.tsx · tests/ui-ux-verify.test.tsx (keys axe): region+h1 preserved → expected NO nav (verify).
  UNCHANGED FLOOR (must stay green verbatim): every query-key/BFF-path/field-name assertion; all frozen testids (loading/revoke-overlay/rotate-overlay/key-governance-editor/budget inputs/delete-provider-overlay/data-provider); region headings; DataTable ariaLabels; native <select>s; states role=status/role=alert; OIDC write-only-secret invariant.
</test_plan>

Tests live in: `tests/design-system/governance-redesign.test.tsx` `tests/design-system/a11y.test.tsx` `tests/routing-edit.test.tsx` `tests/keys.test.tsx` `tests/keys-dialog-a11y.test.tsx` `tests/members.test.tsx` `tests/alerts.test.tsx` `tests/audit.test.tsx` `tests/provider-keys.test.tsx` `tests/ui-ux-verify.test.tsx` `tests-bff/routing-health.test.tsx` `tests-bff/tenant-settings.test.tsx` · the NEW structural suite MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/keys/` `apps/dashboard/components/members/` `apps/dashboard/components/routing/` `apps/dashboard/components/alerts/` `apps/dashboard/components/audit/` `apps/dashboard/components/settings/` `apps/dashboard/tests/design-system/governance-redesign.test.tsx` `apps/dashboard/tests/design-system/a11y.test.tsx` `apps/dashboard/tests/routing-edit.test.tsx` `apps/dashboard/tests/keys.test.tsx` `apps/dashboard/tests/keys-dialog-a11y.test.tsx` `apps/dashboard/tests/members.test.tsx` `apps/dashboard/tests/alerts.test.tsx` `apps/dashboard/tests/audit.test.tsx` `apps/dashboard/tests/provider-keys.test.tsx` `apps/dashboard/tests/ui-ux-verify.test.tsx` `apps/dashboard/tests-bff/routing-health.test.tsx` `apps/dashboard/tests-bff/tenant-settings.test.tsx`
Strategy (ordered batches): 1. NEW structural red suite `governance-redesign.test.tsx` → red. 2. SIMPLE pages (mechanical PageHeader+titleId swap inside the existing region): members · alerts · audit. 3. SETTINGS: swap hand-rolled header → PageHeader, keep its 4 tabs + every sub-component seam. 4. KEYS: PageHeader (Create-key in actions, same gating) + keys-hero (active count) + Tabs Keys(default)/Rate limits/Bandwidth; dialogs + banner mounted OUTSIDE tabs; keys table + governance editor in the Keys panel. 5. ROUTING: PageHeader + routing-hero (strategy + N/M healthy) + Tabs Overview(default)/Editor; Editor trigger+panel only for owner/admin; co-evolve routing-edit + routing-health (nav to Editor). 6. green full suite (legacy+bff+design-system) + tsc + eslint + `add.py check`; capture real built pages at verify.
Known-problem fixes: tabs.tsx inactive panel→null (relocated frozen testid → co-evolve test with userEvent.click(tab), NEVER weaken) · keys dialogs MUST stay mounted outside TabsContent (else focus-trap dialog a11y tests break when a non-keys tab is active) · members/alerts/audit: PageHeader with titleId so the section's aria-labelledby still resolves the region name · routing loading/error stay FULL-PAGE early returns (hero+tabs only on success) · routing Editor tab is conditional on role (no empty/locked tab for a viewer) · settings: keep ONLY the 2 real cache toggles (no mock 3rd toggle); OIDC "<stored>" NEVER prefilled (secret_leak=HARD-STOP) · R3 no raw hex/px in components/ui/* (PageHeader already token-only) · keep native <select> on members/routing/settings · audit: NO Export/filter (mock flourishes).
Strategy actually used: As planned. Simple pages (members/alerts/audit) = a 1-for-1 swap of the hand-rolled h1 for PageHeader with titleId set to the preserved heading id, inside the unchanged region section (so getByRole region-by-name still resolves). Settings = header→PageHeader swap only; its 4 tabs + every sub-component seam + the OIDC write-only-secret invariant untouched (OidcSettings not in the diff). Keys = PageHeader (Create-key in the actions slot, same hidden-while-dialog gating) + keys-hero (activeKeyCount = keys.filter(!revoked_at).length) + Tabs Keys(default)/Rate limits/Bandwidth; the keys table + inline KeyGovernanceEditor stay in the Keys panel; RatelimitsPanel/BandwidthPanel moved into their tabs (now lazy-mount, harmless — their suites render them standalone); CreateKeyDialog + revoke overlay + PlaintextKeyBanner stay mounted OUTSIDE the tabs. Routing = PageHeader + routing-hero (routing_strategy + healthyCount/total where healthy = state!=="open") + Tabs Overview(default)/Editor; the Editor trigger+panel render only when canEdit (owner/admin), so a viewer sees Overview only; loading/error stay full-page early returns. ONE co-evolution: routing-edit.test.tsx renderRouting() became async — wait for the tablist, then click the Editor tab WHEN present (queryByRole, so the member/viewer test skips it and its editor-absent assertions stand); navigation only, no assertion changed; re-crossed `add.py phase build` to re-baseline the tripwire after the edit. No new dependency; no recharts (no time-series on governance).
Safety rule (feature-specific): every data seam (query key · BFF path · response field · mutation) and every frozen test hook (testids · region heading ids · DataTable ariaLabels · dialog accessible-names · OIDC secret invariant) stays BYTE-IDENTICAL; a relocated assertion is reached by tab navigation, never by loosening it.
Code lives in: `apps/dashboard/components/{keys,members,routing,alerts,audit,settings}`
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

- [x] all tests pass — `npx vitest run` (both projects): PASS (794) FAIL (0) (+20 from the new governance-redesign suite; routing co-evolution green at 22/22).
- [x] coverage did not decrease — co-evolution was navigation-only; the redesign added structure + a new 20-test suite, removed no assertion; per-project ≥80% gate held (suite green includes thresholds).
- [x] no test or contract was altered during build — §3 frozen; build wrote only source. The ONE test change (routing-edit Editor-tab nav) was a declared co-evolution; re-crossed `add.py phase build` re-baselined the tripwire; `add.py check` reports no contract/test tamper for this task.
- [x] the green was EARNED, not gamed — adversarial refute-read by frontend-expert subagent (agent ab1deb8d7db880eac): EARNED-GREEN 0.97, all 7 checks PASS. Grepped all 6 pages for sr-only/aria-hidden/hidden — ZERO hidden-DOM crutches (unlike the prior monitoring sr-only bug). Heroes derived from real fetched data; co-evolution navigation-only; no vacuous asserts.
- [x] concurrency / timing of the risky operation is safe — UI-only; no new async/shared state. Tab panels lazy-mount (tabs.tsx); keys dialogs/banner kept OUTSIDE the tabs so their focus-trap stays reachable from any tab; routing loading/error stay full-page early returns.
- [x] no exposed secrets, injection openings, or unexpected dependencies — SECURITY HARD-STOP cleared: OIDC write-only-secret invariant intact (OidcSettings untouched, refute-read confirmed no client_secret prefill). No new dependency; all data flows through the existing BFF seams unchanged.
- [x] layering & dependencies follow CONVENTIONS.md — pages compose the existing kit (PageHeader/Tabs/StatCard/Card/DataTable/states); PageHeader stays token-only (R3); no cross-layer reach; one h1 per page.
- [x] a person reviewed and approved the change — design-confirm: Tin approved the keys/settings/audit mocks + froze §3. Evidence-based auto-gate under `autonomy: auto` (the lone security surface — OIDC secret — is verified intact, not a finding); Tin confirms at the commit checkpoint (commit permission requested in the same turn).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] All six pages render their pinned h1 through the shared PageHeader (the h1 sits inside a header element; on settings the h1's parent is the PageHeader flex div) — confirmed by governance-redesign.test (test_each_governance_page_uses_page_header, 6 sub-cases, green) + the keys/settings/audit design-confirm captures.
- [x] Keys exposes data-testid keys-hero (active-key count) + a tablist Keys(default)/Rate limits/Bandwidth; clicking Rate limits shows the RatelimitsPanel region, Bandwidth shows the BandwidthPanel region; the Create-key dialog opens from any tab (mounted outside the tabs) — confirmed by test_keys_hero_and_tabs + test_keys_dialogs_outside_tabs (green) + the keys capture.
- [x] Routing exposes data-testid routing-hero (strategy + N/M healthy) + tablist Overview(default)/Editor; the Editor tab and panel appear ONLY for owner/admin (a viewer sees Overview only); clicking Editor shows RoutingEditor — confirmed by test_routing_hero_and_tabs_owner + test_routing_editor_tab_hidden_for_viewer (green) + the co-evolved routing-edit suite 22/22.
- [x] Settings keeps its four tabs (Cache default / Guardrails / SSO / Provider Keys) under the new PageHeader, every sub-component seam + the OIDC write-only-secret invariant intact — confirmed by test_settings_pageheader_keeps_four_tabs + the unchanged tenant-settings/provider-keys suites (green) + the settings capture + the refute-read OIDC check.
- [x] Members/alerts/audit show PageHeader + their existing table with NO hero region, and getByRole region-by-name still resolves via the preserved heading id — confirmed by test_simple_pages_no_hero + the unchanged members/alerts/audit suites (green).
- [x] Every frozen data seam + testid is byte-identical: the full legacy + bff + design-system suite (incl. the unchanged floor and the co-evolved routing tests passing by navigation, not weakening) is green (794) and add.py check is clean — confirmed by the suite run + the click-only co-evolution diff + the refute-read seam check.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: PageHeader imported+rendered by all 6 pages; keys-hero/routing-hero regions + the new tab panels asserted by the suite; RatelimitsPanel/BandwidthPanel re-parented into keys tabs (still rendered). tsc --noEmit "No errors found"; eslint 0 errors (the lone warning is pre-existing in untouched data-table.tsx).
- [x] DEAD-CODE (code) — no new unused/orphaned symbol: each page did a 1-for-1 header swap (old hand-rolled h1 deleted); no leftover imports (tsc/eslint clean); no component left unmounted.
- [x] SEMANTIC (prose / non-code) — read §3 CONTRACT in full: every frozen testid + data seam + the OIDC secret invariant in the build matches §3 verbatim; the per-page-fit hero decision (only keys+routing) is honored (members/alerts/audit have no hero).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED (0.97)
By: agent ab1deb8d7db880eac (frontend-expert) · adversarially checked: seam drift (every query key/path/field/testid byte-identical), hidden-DOM crutches (grepped all 6 pages for sr-only/aria-hidden/hidden — ZERO), fabricated metrics (heroes derived from fetched data), co-evolution honesty (routing-edit nav-only, no assertion loosened, viewer editor-absent assertion intact), OIDC secret invariant (OidcSettings untouched, no client_secret prefill), one-h1-per-page, non-vacuous new asserts. All 7 PASS.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: evidence-based auto-gate (autonomy:auto) — security surface (OIDC write-only secret) verified INTACT, not a finding; Tin confirms at the commit checkpoint · date: 2026-06-28

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose Same-as-monitoring, per-page-fit — PageHeader+states everywhere; hero+tabs only where the page warrants; rejected Lighter (PageHeader+states only) · Heaviest (tabs+hero forced on all six)
- [human] freeze — froze §3 @ v1 (approved by Tin)
- [AI] build — strategy used: As planned. Simple pages (members/alerts/audit) = a 1-for-1 swap of the hand-rolled h1 for PageHeader with titleId set to the preserved heading id, inside the unchanged region section (so getByRole region-by-name still resolves). Settings = header→PageHeader swap only; its 4 tabs + every sub-component seam + the OIDC write-only-secret invariant untouched (OidcSettings not in the diff). Keys = PageHeader (Create-key in the actions slot, same hidden-while-dialog gating) + keys-hero (activeKeyCount = keys.filter(!revoked_at).length) + Tabs Keys(default)/Rate limits/Bandwidth; the keys table + inline KeyGovernanceEditor stay in the Keys panel; RatelimitsPanel/BandwidthPanel moved into their tabs (now lazy-mount, harmless — their suites render them standalone); CreateKeyDialog + revoke overlay + PlaintextKeyBanner stay mounted OUTSIDE the tabs. Routing = PageHeader + routing-hero (routing_strategy + healthyCount/total where healthy = state!=="open") + Tabs Overview(default)/Editor; the Editor trigger+panel render only when canEdit (owner/admin), so a viewer sees Overview only; loading/error stay full-page early returns. ONE co-evolution: routing-edit.test.tsx renderRouting() became async — wait for the tablist, then click the Editor tab WHEN present (queryByRole, so the member/viewer test skips it and its editor-absent assertions stand); navigation only, no assertion changed; re-crossed `add.py phase build` to re-baseline the tripwire after the edit. No new dependency; no recharts (no time-series on governance).
- [AI] verify — gate PASS (reviewed by evidence-based auto-gate (autonomy:auto) — security surface (OIDC write-only secret) verified INTACT, not a finding; Tin confirms at the commit checkpoint)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] governance real-page captures through the live edge — the verify evidence rests on the approved keys/settings/audit design-confirm mocks + the 794-green structural suite, NOT full-stack-through-Envoy screenshots of the built pages; capture the real pages against a running dev stack (or Playwright) once available, to close the browser-render residue (evidence: same residue carried by monitoring-pages-redesign; structural + a11y proven in jsdom).
- [SPEC · open] keys-hero secondary stats — the mock showed "revoked" + "expiring soon" secondary counts; only the active-key count shipped. If wanted, add the revoked count (clean from revoked_at) and an expiring-soon count (clean from expires_at) to the hero (evidence: design-confirm mock; both are honest derivations from the already-fetched list).
- [SPEC · dropped] audit Export CSV + filter bar, settings "bypass cache on streaming" toggle — mock flourishes with no backend seam; excluded from this UI-only build to keep seams byte-identical (evidence: §3 freeze note; would each need a new BE seam = own task).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [UDD · open] the per-page-fit standard (PageHeader everywhere; hero+tabs only where the page warrants) scales the monitoring redesign cleanly to a heterogeneous page set — simple tables stayed header+table, complex pages got tabbed IA, with zero forced/empty tabs (evidence: 6 governance pages shipped under one frozen contract, 794 green).
- [TDD · open] a tab reorg's co-evolution cost is bounded by where the relocated content is TESTED, not how complex the page is — keys (most-wired) needed ZERO co-evolution because its panels are tested standalone and its table is the default tab; routing needed one async-nav helper (evidence: the ⚠ freeze flag was confirmed correct — keys suites untouched). <!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
