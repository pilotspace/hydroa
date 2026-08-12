# TASK: Routing config editor — dashboard /routing edit forms (applies on restart)

slug: routing-config-editor · created: 2026-06-23 · stage: production
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

Touches (files · symbols · signatures):
- `apps/dashboard/components/routing/RoutingPage.tsx` — TODAY read-only (useQuery ["admin-routing"] → bffGet "/admin/routing"; 4 cards retry/cooldown/model-groups/candidates). The GET now ALSO returns `routing_strategy` + `deployments` (routing-config-write task, shipped). The editor is added as a new owner/admin-only section on this page.
- NEW `apps/dashboard/components/routing/RoutingEditor.tsx` — the edit form (mirrors `components/settings/GuardrailSettings.tsx`: useQuery seed-during-render + useMutation(bffPut) + useQueryClient.invalidateQueries + inline BffError). Fields: routing_strategy <select> (ordered|simple-shuffle|least-busy|latency); per-alias deployment rows {model_id, weight, rpm_limit, tpm_limit} with add/remove alias + add/remove deployment; Save → bffPut("/admin/routing", body) → invalidate ["admin-routing"]; a persistent "Changes apply on the next gateway restart" notice.
- `apps/dashboard/lib/bff-client.ts` — `bffPut<T>(path, body)` exists (PUT via the BFF catch-all `app/api/gw/[...path]/route.ts`, which forwards PUT with the server-side Bearer). No new BFF code.
- Role-gating: editor section is owner/admin-only. Pattern = `tests/catalog-sync.test.tsx` / ModelsPage "Re-sync" button gated via the current-user role (useCurrentUser / `/api/auth/me`).
Context (working folder):
- `.add/milestones/v32/MILESTONE.md` task #3 + exit criterion "An owner edits model-groups / routing strategy / deployment limits from the /routing dashboard and sees the saved config (with a clear 'applies on restart' indication)."
- Consumes the FROZEN routing-config-write §3 API contract (PUT body = {model_groups, routing_strategy, ...}; GET adds routing_strategy + deployments). NO new backend.
- Test harness: vitest + msw (`tests/mocks/server`), QueryClientProvider wrapper, `me(role)` mock for `/api/auth/me`, http handlers for `/api/gw/admin/routing` GET+PUT (see `tests/catalog-sync.test.tsx`). New suite `tests/routing-edit.test.tsx`.
Honors (patterns / conventions):
- GuardrailSettings/OidcSettings editor conventions: seed local editable state from server data during render (no setState-in-effect); inline BffError title (anti-silent-failure standing rule); retry:false on settled errors; invalidate the read query after a successful save.
- a11y (v13/v24 design-system): labelled form controls; state never by color alone; the "applies on restart" notice is text.
Anchors the contract cites: `RoutingEditor` (new component) · `RoutingPage` (host) · `bffPut`/`/admin/routing` · query key `["admin-routing"]` · routing_strategy/deployments (the GET fields shipped in routing-config-write).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Routing config editor — an owner/admin-only edit form on the /routing dashboard page to change routing strategy + per-deployment model-groups (model_id/weight/rpm/tpm), Save via PUT /admin/routing, with a clear "applies on next restart" notice.
Framings weighed: add a `RoutingEditor` section on the existing RoutingPage mirroring GuardrailSettings (chosen — reuses the proven seed-during-render + useMutation(bffPut) + invalidate pattern, keeps the read-only health cards) · a separate /routing/edit route (rejected — fragments the surface, extra nav) · inline-edit each health card (rejected — couples read-health rendering with write-state, messy).
Must:
<must>
  - An owner/admin sees an editor section on /routing pre-filled from GET /admin/routing (routing_strategy + deployments).
  - The editor lets the user change routing_strategy (4 options) and edit/add/remove model-group aliases and their deployment rows (model_id, weight, rpm_limit, tpm_limit).
  - Save issues PUT /admin/routing with the edited config; on success the read query ["admin-routing"] is invalidated/refetched so the displayed config updates.
  - A persistent notice states changes apply on the next gateway restart (not live).
  - A failed Save (e.g. 422 validation) surfaces the error inline (BffError title / detail) and does NOT clear the form.
  - A member never sees the editor (read-only health only).
</must>
Reject:
<reject>
  - Save returns 422 ERR_ROUTING_CONFIG_INVALID -> the inline error shows the validator code from `detail`; form state preserved; no optimistic mutation of the displayed config.
  - member role -> editor section not rendered (no PUT possible from the UI).
</reject>
After:
<after>
  - After a successful Save, GET /admin/routing reflects the new config and the page shows it; the "applies on restart" notice remains visible.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The PUT body serialization matches the frozen routing-config-write shape (model_groups as {alias:[{model_id,weight,rpm_limit,tpm_limit}]}) — lowest confidence because the editor must emit object-form deployments; if it emits bare strings the weight/limit edits are silently dropped. Cost: weight/limit edits don't persist. Mitigation: editor always emits object-form rows; a test asserts the PUT body carries weight/rpm/tpm.
  - [x] GET returns routing_strategy + deployments — confirmed (shipped in routing-config-write; routing_admin RA8 + routing_config_write tests green).
  - [x] bffPut forwards PUT through the BFF catch-all — confirmed (app/api/gw/[...path]/route.ts has a PUT handler).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: owner sees editor pre-filled
  Given an owner and GET /admin/routing returns routing_strategy "ordered" + deployments {gpt:[{model_id:"a",weight:1,rpm_limit:null,tpm_limit:null}]}
  When the /routing page loads
  Then the editor shows strategy "ordered" and a gpt row with model_id "a"

Scenario: owner edits strategy + deployment and saves
  Given the owner editor is loaded
  When they change strategy to "simple-shuffle", set gpt's weight to 5, and click Save
  Then a PUT /admin/routing is sent with routing_strategy "simple-shuffle" and deployments gpt weight 5
  And the read query is refetched and the page shows the saved config

Scenario: owner adds a new alias + deployment and saves
  Given the owner editor is loaded
  When they add alias "fast" with a deployment model_id "b" and Save
  Then the PUT body's model_groups includes fast -> [{model_id:"b", ...}]

Scenario: applies-on-restart notice is shown
  Given the owner editor is loaded
  When the page renders
  Then a notice stating changes apply on the next restart is visible

Scenario: invalid save surfaces error inline, form preserved
  Given the owner editor with edits made
  When Save returns 422 ERR_ROUTING_CONFIG_INVALID detail "UNKNOWN_ROUTING_STRATEGY"
  Then an inline error referencing the failure is shown
  And the form still holds the user's edits (not cleared)

Scenario: member sees no editor
  Given a member
  When the /routing page loads
  Then the read-only health is shown
  And no routing editor / Save control is rendered
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
UI component contract (no NEW network contract — consumes the FROZEN routing-config-write API).

RoutingEditor (rendered inside RoutingPage, owner/admin only):
  reads:  GET /admin/routing -> { routing_strategy, deployments: {alias:[{model_id,weight,rpm_limit,tpm_limit}]}, ... }
  writes: PUT /admin/routing  body: { routing_strategy, model_groups: {alias:[{model_id,weight,rpm_limit,tpm_limit}]} }
          200 -> updated config (read query ["admin-routing"] invalidated)
          422 ERR_ROUTING_CONFIG_INVALID -> inline error (detail = validator code); form preserved

Observable DOM contract (what tests assert):
  - strategy <select> with the 4 options, value seeded from GET.
  - per-alias group with an editable alias + deployment rows (model_id text, weight/rpm/tpm number inputs).
  - "Add group" / "Add deployment" / remove controls.
  - a Save button (role=button, name ~ /save/i).
  - a persistent "applies on the next restart" notice (text).
  - on 422, an inline error node referencing the failure; inputs retain edited values.
  - member: NONE of the above editor controls render (read-only health only).
Schema: no DB/network schema change — the PUT/GET shapes are owned by routing-config-write §3 (FROZEN).
```

Status: FROZEN @ v1 — approved by Tin (UI task, no security surface; consumes the already-Tin-approved write endpoint)
Least-sure flag surfaced at freeze: [contract] the editor must emit OBJECT-form deployment rows ({model_id,weight,rpm_limit,tpm_limit}) in the PUT model_groups — if it ever emits bare strings, weight/limit edits are silently dropped by the backend. Why least sure: it's the one place the UI shape must exactly match the frozen routing-config-write body. Cost if wrong: weight/limit edits don't persist (silent). Mitigation: a test asserts the captured PUT body carries object-form rows with weight/rpm/tpm. ([scenario] member-never-sees-editor is fail-closed via canEdit default-false — also covered by a test.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ~90% of the editor component branches (seed, edit, save-success, save-error, member-hidden)
Plan (one test per scenario, asserting behavior not internals; vitest + msw, mirror tests/catalog-sync.test.tsx):
<test_plan>
  - test_owner_editor_prefilled: msw GET returns strategy+deployments → editor shows strategy value + gpt model_id "a".
  - test_owner_edit_and_save: change strategy select + weight input, click Save → assert captured PUT body has routing_strategy "simple-shuffle" + deployments gpt weight 5; GET refetched.
  - test_owner_add_alias_and_save: add group "fast" + deployment "b", Save → PUT body model_groups.fast == [{model_id:"b",...}].
  - test_restart_notice_visible: editor renders a /restart/i notice.
  - test_save_422_inline_error_form_preserved: PUT → 422 ERR_ROUTING_CONFIG_INVALID detail UNKNOWN_ROUTING_STRATEGY → inline error visible; the edited input value still present.
  - test_member_no_editor: me("member") → no Save button / no strategy select rendered.
</test_plan>

Tests live in: `apps/dashboard/tests/routing-edit.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/routing/RoutingEditor.tsx` `apps/dashboard/components/routing/RoutingPage.tsx`
Strategy (ordered batches): 1. RoutingEditor.tsx (seed-during-render from GET, strategy select + per-alias deployment rows, Save→bffPut→invalidate, inline BffError, restart notice). 2. wire RoutingEditor into RoutingPage gated owner/admin (useCurrentUser role). 3. green the vitest suite.
Safety rule (feature-specific): editor emits OBJECT-form deployments (model_id+weight+rpm/tpm) so weight/limit edits persist; never bare strings. No optimistic mutation — rely on invalidate+refetch.
Code lives in: `apps/dashboard/components/routing/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full dashboard vitest suite **384 passed (51 files)**; `tests/routing-edit.test.tsx` 6 passed; tsc --noEmit clean; eslint clean (changed files).
- [x] coverage did not decrease — editor branches covered: prefill/seed, edit+save, add-alias, restart-notice, 422-inline-error+form-preserved, member-hidden.
- [x] no test or contract was altered during build — §3 FROZEN untouched. Test edits = STRENGTHENING from the refute-read (assert the validator code is surfaced; assert default weight===1 / object-form). No existing test weakened (routing-health regression fixed properly by renaming the editor's own heading to "Deployment groups", not by editing the health test).
- [x] the green was EARNED — adversarial refute-read (sonnet) ran; I evaluated each finding: F1 (member flash) REFUTED — canEdit defaults false (fail-closed), a member never sees the editor at any timing; F3 (re-seed after save) BY-DESIGN — my scenario requires a refetch and re-seeding to the saved config is intended; F2 (422 gave no field guidance) FIXED — inline error now surfaces the `detail` validator code; F5 (weak add-alias assert) FIXED — now asserts default weight===1. No CHEAT, no weakened test → not a HARD-STOP.
- [x] concurrency / timing safe — seed-during-render guard (no setState-in-effect, converges in one render); no optimistic mutation (invalidate+refetch); restart-to-apply means the UI never implies live effect.
- [x] no exposed secrets / injection / unexpected deps — no new deps; reuses bffPut/useCurrentUser/Button/Input; PUT body is object-form routing fields only; member role cannot render the write controls.
- [x] layering & dependencies follow CONVENTIONS.md — presentation-only; RoutingEditor consumes the frozen API via bffPut; mirrors GuardrailSettings conventions; a11y labels on every control.
- [x] reviewed — UI task, NO security surface (consumes the already-Tin-approved write endpoint). Auto-resolved under `autonomy: auto` on complete evidence + refute-read (findings actioned).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] Owner sees editor pre-filled from GET — `test_owner_editor_prefilled` (strategy select value + gpt model_id input).
- [x] Edit + Save sends correct object-form PUT body, GET refetched — `test_owner_edit_and_save` (PUT body routing_strategy "simple-shuffle", model_groups.gpt[0].weight===5 number; getCalls increases).
- [x] Add alias emits object-form row with valid default weight — `test_owner_add_alias_and_save` (model_groups.fast[0] {model_id:"b", weight:1} + rpm/tpm keys).
- [x] Restart notice visible — `test_restart_notice_visible` (/restart/i).
- [x] 422 surfaces the validator code inline, form preserved — `test_save_422_inline_error_form_preserved` (UNKNOWN_ROUTING_STRATEGY shown; strategy select still "simple-shuffle").
- [x] Member sees no editor — `test_member_no_editor` (no strategy select / no Save button; health still renders).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — RoutingEditor imported + rendered in RoutingPage gated by `canEdit` (currentUser role owner/admin); bffPut→/admin/routing; invalidateQueries(["admin-routing"]).
- [x] DEAD-CODE (code) — no orphaned symbols; STRATEGY_OPTIONS/emptyRow/handlers all used.
- [x] SEMANTIC — refute-read read the component + RoutingPage + tests in full; findings evaluated and actioned.

### GATE RECORD
Outcome: PASS
Reviewed by: auto-resolved (autonomy:auto, UI task no security surface) + sonnet refute-read (findings evaluated: 2 fixed, 2 refuted/by-design) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): editor Save success vs 422 rate; whether operators discover "applies on restart".

### Spec delta
- [SPEC · open] add per-deployment validation hints in the editor (e.g. weight must be > 0) so an empty/zero weight is caught client-side before the 422 round-trip (evidence: refute F2 — empty weight sends 0 → backend 422; surfacing the validator code helps but client-side guard is better UX).  [routing-config-editor]
- [SPEC · open] a "restart now / pending-restart" affordance or banner once a config is saved-but-not-yet-applied, so operators know a restart is required to take effect (evidence: restart-to-apply is easy to forget after Save).  [routing-config-editor]

### Competency deltas
- [ADD · folded] delegating a UI build to a subagent is fine under auto, but INDEPENDENTLY re-verify its claims — re-run the suite with the REAL test binary (npx shim prints fake green here), read the diff, and run a refute-read — don't trust the subagent's reported counts (evidence: re-ran node_modules/.bin/vitest → confirmed 6/384; refute-read then surfaced 2 real fixes).  [routing-config-editor] [folded foundation-version 29]
- [TDD · folded] a role-gated UI control's "member sees nothing" test is only meaningful when the gate is fail-closed by construction (canEdit defaults false via optional chaining) — assert absence with queryBy*/toBeNull, and note the gate can never render for the wrong role regardless of async ordering (evidence: test_member_no_editor + canEdit default-false refutes the "flash" concern).  [routing-config-editor] [folded foundation-version 29]
