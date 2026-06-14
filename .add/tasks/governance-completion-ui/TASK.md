# TASK: Governance completion UI (key editor depth + spend breakdown)

slug: governance-completion-ui · created: 2026-06-14 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): DEPTH on two existing v13 FRONTEND surfaces — presentation-only over already-frozen gateway contracts (all fields/params already accepted server-side). NO gateway change (the cache_enabled list fidelity fix is the separate, DONE key-cache-enabled-fidelity task). SSO login is split to oidc-login-relay. Verified anchors:

KEY EDITOR DEPTH (`apps/dashboard/components/keys/KeyGovernanceEditor.tsx`):
- Today the editor (inline `<div data-testid="key-governance-editor">`, raw `bffPatch`, `isSubmitting`, `<p role="alert">` errors) edits monthly_budget_usd · soft_budget_usd · expires_at · model_allowlist and sends a DENSE PATCH (all four always, prefilled). `ApiKeyGovernance` interface (line 30) lacks rpm_limit/tpm_limit/team_id/cache_enabled.
- Gateway PATCH /admin/keys/{id} (`keys/api/router.py:patch_key`, require_owner_or_admin) ALREADY accepts (PatchKeyRequest, schemas.py:88): `rpm_limit:int|None` (>0 via _parse_positive_int, null=clear), `tpm_limit:int|None` (>0, null=clear), `team_id:uuid|None` (null=clear/un-team; a UUID is validated to belong to the tenant → 404 ERR_TEAM_NOT_FOUND if not), `cache_enabled:bool|None` (present+bool sets; present+null is a NO-OP, never a clear — so the UI sends a bool, never null). Response KeyInfoResponse now includes the TRUE cache_enabled (after key-cache-enabled-fidelity). Errors: 422 ERR_PAYLOAD_INVALID, 404 ERR_KEY_NOT_FOUND / ERR_TEAM_NOT_FOUND, 403 ERR_AUTH_FORBIDDEN.
- Teams for the team_id dropdown: GET /admin/teams → bare `list[TeamResponse]{id,name,…}` (owner/admin); query key `["admin-teams"]` (teams-governance-ui established). Dropdown: "No team"(value=""→null) + one option per team (value=id, label=name).

SPEND DEPTH (`apps/dashboard/components/spend/SpendPage.tsx`):
- Today SpendPage queries `bffGet("/admin/spend?window="+window)`, key `["admin-spend", window]`, renders totals + buckets + SpendSparkline + zero-state + 403/422 ErrorState; `breakdown` is hardcoded `null`.
- Gateway GET /admin/spend (`usage/api/router.py`, admin JWT, tenant-scoped) ALREADY accepts: `window∈{day,week,month}` (default month), `key_id` (uuid; 422 bad uuid, 404 not-in-tenant), `group_by∈{key_id,team_id}` (422 unknown; absent=no breakdown), `start`/`end` (iso). Response SpendWindowResponse.breakdown is polymorphic: `SpendBreakdownItem[]` (group_by=key_id: {key_id, requests, prompt_tokens, completion_tokens, cost_usd}) | `TeamSpendBreakdownItem[]` (group_by=team_id: {team_id|null, team_name|null, requests, prompt_tokens, completion_tokens, cost_usd, ledger_cost_usd}) | null. Errors: 422 ERR_PAYLOAD_INVALID, 404 ERR_KEY_NOT_FOUND.
- Keys for the key_id filter dropdown: GET /admin/keys → bare `list[KeyInfoResponse]{key_id,name,prefix,…}`; query key `["admin-keys"]` (KeysPage established). Dropdown: "All keys"(value=""→omit) + one option per key (value=key_id, label=name/prefix).

Data seam (BFF verbatim, no envelope): `bffGet<T>`, `bffPatch<T>(path,body)`; `BffError{status, problem.title}`. UI primitives: Switch, Table/TableHeader/…/TableCaption, native `<select>`, Input, Button, Loading/Empty/ErrorState. Tests: `apps/dashboard/tests-bff/` (msw at http://localhost:3000/api/gw/...); existing govern.test.tsx + spend-chart.test.tsx must STAY GREEN (KEY_FIXTURE/SPEND_*_FIXTURE lack the new fields → the editor/page must tolerate undefined: rpm/tpm ?? "", cache_enabled ?? false, team_id ?? null, breakdown ?? null).

Context (working folder): v15 MILESTONE.md governance-completion-ui (narrowed to the two pure-frontend depths; SSO→oidc-login-relay, cache list fidelity→key-cache-enabled-fidelity DONE).

Honors (patterns / conventions): the existing KeyGovernanceEditor dense-PATCH + inline-alert pattern; SpendPage window-selector + queryKey-driven refetch; ModelsPage/TeamsPage useQuery+state patterns; the four state patterns; v13/v15 a11y bar (labelled controls, keyboard, axe zero serious/critical); CLAUDE.md design-for-failure (every PATCH/GET surfaces its BffError title — anti-silent-failure; every contracted error gets a test). NO gateway/BFF change, NO new dependency.

Anchors the contract cites: KeyGovernanceEditor (+rpm/tpm/team_id select from `["admin-teams"]`/cache_enabled Switch via dense PATCH) · SpendPage (+group_by select / key_id select from `["admin-keys"]` / polymorphic breakdown Table; queryKey ["admin-spend",window,groupBy,keyId]) · the frozen PATCH /admin/keys + GET /admin/spend contracts · bffGet/bffPatch · getErrorTitle.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Governance completion UI — DEEPEN two v13 surfaces: (1) the key-governance editor gains rpm_limit, tpm_limit, a team_id dropdown (from the teams list), and a cache_enabled toggle; (2) the spend page gains a group_by selector, a key_id filter, and a polymorphic breakdown table. Presentation-only over already-frozen gateway contracts; NO gateway/BFF change.
Framings weighed: Extend the two existing components in place (chosen — the components, their tests, and the dense-PATCH/queryKey patterns already exist; extension keeps one editor + one spend page, no surface fragmentation) · New separate "advanced" sub-pages (rejected — fragments the key/spend mental model + duplicates state) · A generic key-field form generator (rejected — over-engineering for four typed fields with distinct validation).
Must:
<must>
  - KEY EDITOR: add rpm_limit and tpm_limit number inputs (empty = clear→null; a value must be a positive integer >0, else an inline error blocks the PATCH; the server 422 is also surfaced).
  - KEY EDITOR: add a team_id dropdown sourced from GET /admin/teams (["admin-teams"]) — "No team" (→ null, un-team) plus one option per team (value=team id, label=team name); the selection is sent in the PATCH; a team that the server rejects → 404 surfaces inline.
  - KEY EDITOR: add a cache_enabled toggle (Switch) reflecting the key's current value; the PATCH sends a boolean (never null — present+null is a server no-op); on success the persisted value is reflected.
  - KEY EDITOR: the PATCH stays the established dense-and-prefilled body (existing four fields + the four new ones); existing budget/expires/allowlist behavior + the existing govern.test.tsx stay unchanged/green.
  - SPEND: add a group_by selector (None | Key | Team) → maps to absent | group_by=key_id | group_by=team_id; the queryKey includes group_by (and key_id) so changing it refetches.
  - SPEND: add a key_id filter sourced from GET /admin/keys (["admin-keys"]) — "All keys" (→ omit) plus one option per key; selecting a key adds key_id to the query.
  - SPEND: when the response carries a non-null breakdown, render an accessible breakdown Table — polymorphic by group_by: key rows show key + requests/prompt/completion/cost; team rows show team (name or "(no team)") + the same metrics + ledger cost. The existing totals/buckets/chart still render.
  - a11y/UX/failure: every new control is labelled + keyboard-operable; axe zero serious/critical; every PATCH/GET surfaces its BffError title (no silent failure); a 422/404 leaves the prior view intact with an inline alert. NO gateway/BFF contract change.
</must>
Reject:
<reject>
  - rpm_limit/tpm_limit set to 0, negative, or non-integer -> client inline error blocks the PATCH; if it reaches the server -> 422 ERR_PAYLOAD_INVALID surfaced inline -> "bad_rate_limit"
  - team_id set to a team not in the tenant -> 404 ERR_TEAM_NOT_FOUND surfaced inline, key unchanged -> "cross_tenant_team"
  - Sending cache_enabled as null expecting a "clear" -> WRONG (server no-op); the UI sends a boolean only -> "cache_null_misuse"
  - A member (no owner/admin) PATCHing a key -> 403 ERR_AUTH_FORBIDDEN inline -> "role_leak"
  - spend group_by set to anything other than key_id|team_id, or an invalid key_id -> 422 ERR_PAYLOAD_INVALID inline (prior view intact) -> "bad_query"
  - spend key_id for a key not in the tenant -> 404 ERR_KEY_NOT_FOUND inline -> "cross_tenant_key"
  - Rendering a breakdown table that assumes one shape and crashes on the other (key vs team) -> "polymorphic_crash"
  - A `{data}` envelope unwrap, a new gateway/BFF endpoint, or a new npm dependency -> "scope_creep"
</reject>
After:
<after>
  - An owner edits a key's rpm/tpm/team/cache and the values persist; assigning a cross-tenant team or a bad rate limit surfaces an inline error without corrupting the key; the spend page groups by key or team, filters by key, and shows a correct breakdown table while keeping totals/buckets/chart; every contracted error (403/404/422) surfaces inline without a crash; the existing govern + spend suites stay green; no gateway/BFF contract changed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The DENSE PATCH body for the FOUR new fields. The editor keeps the existing always-send-all pattern and prefills every field, so a no-touch save preserves values — BUT because the gateway treats present+null as a CLEAR for rpm_limit/tpm_limit/team_id, the prefill MUST be correct or a save silently clears them. cache_enabled is now correctly prefilled (key-cache-enabled-fidelity fixed the list); rpm/tpm/team_id are in the list response already. Lowest confidence because if any field is NOT prefilled before a save, that field is cleared. Cost if wrong: an accidental clear. Mitigation: prefill all four from apiKey (rpm/tpm ?? "", team_id ?? "", cache_enabled ?? false) and test a no-change save preserves them; if a sparse PATCH is later preferred it's a follow-up (the server supports both via model_fields_set).
  - [ ] cache_enabled present+null is a server no-op (NOT a clear) — confirmed (router.py:231); the UI sends a boolean only.
  - [ ] breakdown is polymorphic (key vs team vs null) — confirmed by the gateway schemas; the table branches on group_by and tolerates null.
  - [ ] the team_id / key_id dropdowns reuse the already-established ["admin-teams"] / ["admin-keys"] queries (bare arrays, no envelope) — confirmed.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Edit rpm/tpm/team/cache and save
  Given the key editor for a key, with teams [Alpha, Beta] available
  When the owner sets rpm_limit=60, tpm_limit=1000, team=Beta, toggles cache on, and Saves
  And PATCH /admin/keys/{id} returns 200 with those values
  Then the PATCH body contains rpm_limit 60, tpm_limit 1000, team_id (Beta's id), cache_enabled true
  And the persisted values are reflected

Scenario: Clear rpm/tpm and un-team
  Given a key with rpm_limit=60, tpm_limit=1000, team=Beta
  When the owner clears rpm and tpm and selects "No team" and Saves
  Then the PATCH body contains rpm_limit null, tpm_limit null, team_id null

Scenario: Invalid rate limit blocked client-side
  Given the key editor
  When the owner enters rpm_limit=0 (or -1 or "abc") and Saves
  Then an inline role=alert is shown and NO PATCH request is made

Scenario: Server rejects a rate limit
  Given the key editor with rpm_limit set
  When PATCH returns 422 ERR_PAYLOAD_INVALID
  Then an inline role=alert is shown and the key view is intact

Scenario: Cross-tenant team rejected
  Given the key editor with a team selected
  When PATCH returns 404 ERR_TEAM_NOT_FOUND
  Then an inline role=alert is shown

Scenario: cache_enabled reflects the loaded key
  Given a key whose cache_enabled is true
  When the editor renders
  Then the cache Switch is on
  And a save that does not touch it sends cache_enabled true (boolean, never null)

Scenario: Spend group-by key shows a key breakdown table
  Given the spend page
  When the owner selects Group by = Key
  And GET /admin/spend?...&group_by=key_id returns a breakdown of key rows
  Then a breakdown table lists each key with its requests and cost
  And totals/buckets still render

Scenario: Spend group-by team shows a team breakdown table
  Given the spend page
  When the owner selects Group by = Team
  And the response returns team rows (one with team_name null)
  Then the table lists each team (the null team as "(no team)") with cost and ledger cost

Scenario: Spend key filter narrows the query
  Given the spend page with keys [k1, k2] available
  When the owner selects key = k1
  Then the request carries key_id=k1's id (queryKey refetch)

Scenario: Spend invalid query surfaces inline
  Given the spend page with a group_by/key_id selection
  When GET /admin/spend returns 422 (or 404 for a cross-tenant key)
  Then an inline role=alert is shown and the page does not crash

Scenario: Governance surfaces are accessible
  Given the key editor and the spend page with data
  When axe runs
  Then there are zero serious/critical violations and every new control has an accessible name
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SURFACE  KeyGovernanceEditor depth + SpendPage depth  (owner/admin; presentation-only over frozen contracts)

KEY EDITOR (components/keys/KeyGovernanceEditor.tsx — EXTEND):
  ApiKeyGovernance += rpm_limit:number|null, tpm_limit:number|null, team_id:string|null, cache_enabled:boolean
  CONTROLS (added):
    - <input> rpm_limit  (aria-label "Requests per minute (RPM) limit", inputMode numeric; empty→null, else positive int>0)
    - <input> tpm_limit  (aria-label "Tokens per minute (TPM) limit", inputMode numeric; empty→null, else positive int>0)
    - <select> team_id   (aria-label "Team"): option "No team"(value=""→null) + per team from useQuery(["admin-teams"]) (value=id label=name)
    - <Switch> cache_enabled (aria-label "Enable response cache")
  VALIDATION (client, blocks PATCH): rpm/tpm non-empty must be integer >0 → inline role=alert, no request
  PATCH body (dense, prefilled — existing 4 + new 4): { monthly_budget_usd, soft_budget_usd, expires_at, model_allowlist, rpm_limit, tpm_limit, team_id, cache_enabled }
    rpm_limit/tpm_limit: "" → null (clear); team_id: "" → null (un-team); cache_enabled: boolean (NEVER null)
  ERRORS: 422 ERR_PAYLOAD_INVALID / 404 ERR_TEAM_NOT_FOUND / 404 ERR_KEY_NOT_FOUND / 403 ERR_AUTH_FORBIDDEN → inline role=alert (BffError.problem.title); key view intact
  PRESERVED: existing budget/expires/allowlist behavior, rotation, dense-PATCH, data-testid="key-governance-editor"; existing govern.test.tsx green

SPEND PAGE (components/spend/SpendPage.tsx — EXTEND):
  STATE: groupBy: ""|"key_id"|"team_id"  ·  keyId: ""|<uuid>
  CONTROLS (added):
    - <select> group_by (aria-label "Group by"): "None"(""), "Key"("key_id"), "Team"("team_id")
    - <select> key_id   (aria-label "Filter by key"): "All keys"("") + per key from useQuery(["admin-keys"]) (value=key_id label=name (prefix))
  QUERY: bffGet(`/admin/spend?window=${w}` + (groupBy?`&group_by=${groupBy}`:"") + (keyId?`&key_id=${keyId}`:""))
         queryKey ["admin-spend", window, groupBy, keyId]
  TYPES: SpendBreakdownItem={key_id,requests,prompt_tokens,completion_tokens,cost_usd}
         TeamSpendBreakdownItem={team_id:string|null, team_name:string|null, requests, prompt_tokens, completion_tokens, cost_usd, ledger_cost_usd}
         SpendWindowResponse.breakdown: SpendBreakdownItem[] | TeamSpendBreakdownItem[] | null
  BREAKDOWN TABLE (rendered only when breakdown != null):
    - group_by=key_id → <table> caption "Spend by key": cols Key | Requests | Prompt | Completion | Cost (USD)
    - group_by=team_id → <table> caption "Spend by team": cols Team | Requests | Prompt | Completion | Cost (USD) | Ledger cost (USD); team_name null → "(no team)"
  PRESERVED: window selector, totals-*, spend-bucket list, SpendSparkline, zero-state, 403/422 ErrorState; existing spend-chart.test.tsx + govern spend tests green

DATA SEAM: BFF verbatim (no {data} unwrap). NO gateway/BFF change. NO new dependency.
OBSERVABLE: every new control labelled + keyboard-operable; axe zero serious/critical; every PATCH/GET error → inline role=alert.
```

Least-sure flag surfaced at freeze: [contract] The dense PATCH + present-null-clears semantics for the THREE clearable new fields (rpm_limit/tpm_limit/team_id). Because the editor sends all fields on every save and the gateway treats present+null as a CLEAR, the prefill of these three from the key MUST be correct or a no-touch save silently clears them. Why least-sure: it is the one place a benign save could destroy state. Cost if wrong: an accidental clear of a rate limit / team. Decision (auto): keep the established dense+prefilled pattern (consistent with the existing four fields), prefill all three from apiKey, and add a test that a no-change save round-trips them unchanged; cache_enabled is exempt (present+null is a server no-op, so the UI always sends a boolean). Secondary [contract]: the breakdown is polymorphic (key vs team vs null) — the table branches on group_by and renders nothing when null; a team row's null team_name renders "(no team)".

Status: FROZEN @ v1 — approved by ADD auto (bundle approval delegated per project autonomy=auto; presentation-only over already-frozen gateway contracts; no secret surface; the one risk — dense-PATCH clear semantics — is mitigated by prefill + a round-trip test).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (dashboard global gate; the new controls/branches aim higher).
Plan (RTL + msw at `http://localhost:3000/api/gw/...`, fresh QueryClient/test, `axeSeriousCritical`; NEW files to keep the 40K govern.test.tsx untouched):
<test_plan>
  tests-bff/govern-depth.test.tsx (KeyGovernanceEditor depth):
  - test_edit_rpm_tpm_team_cache_saves: set rpm/tpm/team(select)/cache(switch), Save, PATCH→200 / assert body rpm_limit/tpm_limit/team_id/cache_enabled correct
  - test_clear_rpm_tpm_and_unteam: from a key with values, clear inputs + "No team", Save / assert body nulls
  - test_invalid_rate_limit_blocked: rpm=0 (and a non-integer) / assert inline role=alert + NO PATCH
  - test_server_422_rate_limit: PATCH→422 / assert inline role=alert
  - test_cross_tenant_team_404: PATCH→404 ERR_TEAM_NOT_FOUND / assert inline role=alert
  - test_cache_switch_reflects_and_sends_bool: key cache_enabled true → switch on; no-touch Save sends cache_enabled true (boolean) AND rpm/tpm/team round-trip unchanged
  - test_member_403: PATCH→403 / assert inline role=alert
  - test_team_dropdown_from_admin_teams: GET /admin/teams → options rendered
  - test_govern_depth_axe_clean: axe zero serious/critical on the editor with the new controls

  tests-bff/spend-breakdown.test.tsx (SpendPage depth):
  - test_group_by_key_breakdown_table: select Group by=Key, GET returns key breakdown / assert a "Spend by key" table with key rows + totals/buckets still present
  - test_group_by_team_breakdown_table: select Group by=Team, response has a null-team row / assert "Spend by team" table incl "(no team)" + ledger cost column
  - test_key_filter_adds_key_id: keys available; select key=k1 / assert request carries key_id=k1 (capture request URL)
  - test_spend_invalid_query_422: group_by/key_id set, GET→422 / assert inline role=alert, no crash
  - test_spend_cross_tenant_key_404: key_id set, GET→404 ERR_KEY_NOT_FOUND / assert inline role=alert
  - test_group_by_none_no_table: default None / assert NO breakdown table (existing totals/buckets unchanged)
  - test_spend_breakdown_axe_clean: axe zero serious/critical with a breakdown table shown
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` (NEW govern-depth.test.tsx + spend-breakdown.test.tsx) · MUST run red before Build (new assertions fail against the un-extended components).

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/keys/KeyGovernanceEditor.tsx` `apps/dashboard/components/keys/KeysPage.tsx` `apps/dashboard/components/spend/SpendPage.tsx` `apps/dashboard/tests-bff/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/governance-completion-ui/`
<!-- SCOPE NOTE: EXTEND two existing components in place; NEW tests in tests-bff/. .next/coverage/tsbuildinfo are verify-tooling artifacts (coverage gitignored). NO gateway/BFF source, NO new route, NO new dependency. SSO login + the gateway cache fix are separate tasks.
     KeysPage.tsx added @ re-cross: extending ApiKeyGovernance to REQUIRE the 4 new fields makes KeysPage.toGovernanceKey both a type error AND (worse) a SILENT-CLEAR defect — it prefilled the editor blank, so a no-touch save would clear the real rpm/tpm/team/cache. Wiring KeysPage's ApiKey interface + toGovernanceKey to carry the 4 list fields IS the production realization of §3's #1 least-sure flag (prefill correctness). Verified GET /admin/keys returns all 4 (keys/api/router.py:151-154). Also: ui-ux-verify.test.tsx (v13) renders the editor standalone — now needs the QueryClientProvider the new useQuery(["admin-teams"]) requires + the extended fixture (existing-suite-green per §0). -->
Strategy (ordered batches): 1. RED tests (govern-depth + spend-breakdown). 2. KeyGovernanceEditor: extend ApiKeyGovernance + add rpm/tpm inputs + team_id select (useQuery ["admin-teams"]) + cache Switch; extend the dense PATCH body + client validation; prefill all. 3. SpendPage: add groupBy/keyId state + selects (key_id select via useQuery ["admin-keys"]) + query/queryKey extension + polymorphic breakdown Table. 4. existing govern + spend-chart suites + the two new suites + next lint + full vitest --coverage green.
Safety rule (feature-specific): dense PATCH — prefill rpm/tpm/team_id (present+null clears) so a no-touch save never silently clears; cache_enabled is a boolean, NEVER null. breakdown table branches on group_by and renders nothing when null; a null team_name → "(no team)". Every PATCH/GET error surfaces its BffError title (anti-silent-failure). NO {data} unwrap, NO gateway/BFF change.
Code lives in: `apps/dashboard/components/keys/` + `apps/dashboard/components/spend/`
Constraints: do NOT change any test or contract; do NOT break the existing govern.test.tsx / spend-chart.test.tsx; reuse existing primitives + helpers (NO new dependency); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `vitest run --coverage` EXIT=0: 27 files / 213 tests pass (was 206 before this task's depth + the adversarial-driven additions).
- [x] coverage did not decrease — 94.03% stmts / 85.61% branch (≥80% global gate held; up from 93.93%).
- [x] no test or contract was altered to force a pass — §3 contract untouched; test edits were RED-first (govern-depth wiring, spend prior-view-intact) or non-weakening type/harness completions (KEY_FIXTURE/KEY_GOV_FIXTURE field completion, QueryClientProvider wrapper, spendRouter typing). next lint clean; production code tsc-clean (the 18 remaining tsc errors are PRE-EXISTING test-file loosenesses, none in my files — zero introduced).
- [x] the green was EARNED — TWO adversarial refute-reads (sonnet). Pass 1 found a real DEFECT: spend 422/404 WIPED the prior view (line-229 `!isError` gate) — contract §1/reject "prior view intact" violation; fixed with keepPreviousData + a last-good ref + breakdown `!isError` gate + `viewData.window` label; pinned by strengthened 422/404 tests (now assert totals stay visible). It also found GAPs (validation variants -1/abc/1.5 + TPM; group_by→None; post-save reflect) — all added. Pass 2 (on the fix) found a real DEFECT: render-phase ref write (concurrent-unsafe) → moved to useEffect; its "window-label mismatch" was adjudicated correct-by-design (`viewData.window` labels the data shown, never the pending selector) and LOCKED with test_window_change_error_keeps_prior_view_with_matching_label. No vacuous/overfit asserts survive.
- [x] concurrency / timing safe — last-good ref written in useEffect (not during render) → React-19 concurrent-safe; dense-PATCH prefill verified end-to-end (KeysPage list → toGovernanceKey → editor state → PATCH body) so a no-touch save round-trips rpm/tpm/team unchanged and never silently clears.
- [x] no exposed secrets / injection / unexpected deps — no key/secret/JWT in any control, label, or breakdown row; BFF passed verbatim (no {data} unwrap); zero new dependencies (keepPreviousData/useRef/useEffect are existing). NOT a security task (presentation over frozen contracts).
- [x] layering & dependencies follow conventions — reused bffGet/bffPatch, ["admin-teams"]/["admin-keys"] queries, Switch/Table/Input/Button/Empty/ErrorState primitives, the dense-PATCH + inline-alert + queryKey-refetch patterns.
- [x] reviewed & approved — ADD auto-gate on complete evidence (autonomy=auto; presentation-only over frozen contracts; no security surface; the one functional risk — dense-PATCH silent-clear — is closed end-to-end by the KeysPage wiring + the test_list_fields_prefill_the_editor guard).

### Deep checks
- [x] WIRING — every new symbol referenced: KeysPage.toGovernanceKey now maps rpm/tpm/team_id/cache_enabled (KeysPage.tsx:78-87) into the editor (line 277); editor controls bound to state + dense PATCH body; SpendPage group_by/key_id selects → queryKey ["admin-spend",spendWindow,groupBy,keyId] → query params; viewData/lastGoodRef consumed by the render. Confirmed by 213 green tests incl. the KeysPage→editor wiring guard.
- [x] DEAD-CODE — none introduced; `window` shadow removed (renamed spendWindow); no orphaned symbol.
- [x] SEMANTIC — read TASK.md §0–§4 + gateway keys/api/router.py:91-154,209-246 + schemas.py in full; confirmed GET /admin/keys returns all 4 governance fields (the silent-clear fix is load-bearing) and PATCH present+null clears rpm/tpm/team but is a no-op for cache_enabled (UI sends a boolean).

### GATE RECORD
Outcome: PASS
Reviewed by: ADD auto-gate (autonomy=auto) + 2× adversarial sonnet refute-read · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): spend-query error rate by group_by/key_id (422/404); per-key dense-PATCH that flips rpm/tpm/team to null (a spike could mean a prefill regression silently clearing values); teams/keys dropdown fetch failure rate (the deliberately-tolerated secondary fetch — see delta below).
Spec delta for the next loop: extending a SHARED prop/DTO type (ApiKeyGovernance) silently re-types every caller — the real risk was not the editor but KeysPage.toGovernanceKey dropping the new fields (a silent dense-PATCH CLEAR). Future "add a field to an editor" tasks must trace the production caller(s), not just the component-in-isolation tests. Carried trade-off (G3): the team_id/key_id DROPDOWN queries deliberately swallow load/error ("tolerate loading/error, no crash") — consistent across SpendPage/KeysPage; the anti-silent-failure rule is enforced on user-triggered ops (Save, the primary spend GET), not background dropdown population. A future "degraded dropdown" indicator (subtle inline note when teams/keys fail to load) would close the gap without a blocking alert.

### Competency deltas
- TDD: a component-in-isolation test (render `<Editor apiKey={fixture}/>`) can be fully green while the PRODUCTION wiring (list→normalise→editor) is broken — add at least one through-the-caller test for any prefill/round-trip-sensitive surface. Evidence: test_list_fields_prefill_the_editor caught KeysPage.toGovernanceKey dropping rpm/tpm/team/cache (a silent-clear defect) that all 9 isolation tests missed. status: open
- TDD: "inline alert on error" tests are vacuous if they don't ALSO assert the prior view survived — assert the data is still on screen, not just that an alert appeared. Evidence: adversarial pass 1 showed test_spend_invalid_query_422 passed against code that wiped totals; strengthening it to assert totals-cost visible turned it RED and exposed the contract violation. status: open
- UDD: "leave the prior view intact on error" needs keepPreviousData (pending phase) AND a last-good ref (error phase, since an errored query has data===undefined) AND label from the DATA shown (viewData.window) not the pending control — three pieces, each with its own failure mode. status: open
- ADD: re-cross is cheap and correct — five re-crosses (wiring fix, type cleanup, D1, D1-ref-fix, GAP lock) each strengthened the suite without ever weakening a test or touching the frozen §3. The adversarial refute-read earned its keep twice (two real DEFECTs caught post-green). status: open
- SDD: the §3 contract can be internally tense — §3 typed the 4 fields as REQUIRED while §0 said "tolerate undefined"; the reconciliation is "required type + defensive ?? in the component + complete the fixtures," not "make the field optional" (which would hide the silent-clear class). status: open
