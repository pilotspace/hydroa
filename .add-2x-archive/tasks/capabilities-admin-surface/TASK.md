# TASK: Surface model input capabilities on admin catalog API + dashboard

slug: capabilities-admin-surface · created: 2026-06-30 · stage: production
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

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/catalog/api/schemas.py` — `ModelItem` (L25; the GET /v1/models entry) STAYS LEAN (UNCHANGED — Tin 2026-06-30: keep the public OpenAI shape lean). ADD a NEW `AdminCatalogModelItem` (= ModelItem fields + `input_modalities: list[str]`) + `AdminCatalogModelsListResponse` for /admin/catalog/models. ADD `input_modalities: list[str]` to `AdminModelItem` (L48; /admin/models). Default `["text"]` so an omitting caller stays valid; the mappers always supply it.
- `apps/gateway/src/gateway/catalog/api/router.py` — `list_models` (L91→`ModelItem`) STAYS UNCHANGED (lean /v1/models). `list_catalog_models` (L118) STOPS delegating to list_models — instead it runs the SAME `ListModelsForTenantUseCase` and maps each `CatalogModel`→`AdminCatalogModelItem` WITH `input_modalities=sorted(parse_input_modalities(m.input_modalities))` (same 409 ERR_CATALOG_EMPTY behavior). `get_admin_models` (L147): add `ModelRow.input_modalities` to the SELECT + `input_modalities=sorted(parse_input_modalities(row.input_modalities))` on AdminModelItem.
- task-1 seam: `CatalogModel.input_modalities` (CSV str, already returned by `ListModelsForTenantUseCase.execute`) + `gateway.catalog.domain.entities.parse_input_modalities` (CSV→frozenset; sort for a deterministic JSON list).
- `apps/dashboard/components/models/ModelsPage.tsx` — operator page `/app/models`. Reads `GET /admin/models` via `bffGet` (queryKey ["admin-models"]); `AdminModelItem` TS interface (L35) + `columns: ColumnDef<AdminModelItem>[]` (L101) feeding `<DataTable>`. ADD `input_modalities: string[]` to the interface + an "Inputs" column rendering `<Badge>` chips (read-only).
- `apps/dashboard/components/models/ModelCatalogTable.tsx` — reads `/admin/catalog/models`; `ModelEntry` interface (L13) + a 4-col `grid` (Name/Context/Prompt/Completion, L70-105). ADD `input_modalities: string[]` to `ModelEntry` + a 5th "Inputs" column (grid-cols-4 → grid-cols-5).
- `@/components/ui` — exports `Badge` (components/ui/index.ts L31) — the chip primitive for the capability tokens.
Context (working folder): `.add/milestones/v55/MILESTONE.md` (task 2 of 4, the LAST; depends-on: model-input-capabilities DONE). Tin 2026-06-30 (AskUserQuestion ×2): surface on BOTH admin endpoints (/admin/catalog/models + /admin/models) but KEEP public /v1/models LEAN (admin-only field via a new AdminCatalogModelItem); READ-ONLY listing (no edit control). MILESTONE GAP RECONCILED: the milestone named only /admin/catalog/models, but the operator models page reads /admin/models — both are now in scope.
Honors (patterns / conventions): additive-only — extra response field (OpenAI clients ignore unknown keys; the /v1/models shape change is purely additive). UDD: the "Inputs" column is presentation-only, reusing the existing Badge + DataTable/grid patterns (the wireframe Tin approved in the AskUserQuestion preview). PROJECT.md — FE field names byte-identical to the gateway; bff-client credentials:"include" (no client Authorization). No write path (read-only), so no new mutation/PUT.
Anchors the contract cites: `ModelItem.input_modalities` · `AdminModelItem.input_modalities` · `list_models`/`get_admin_models` mappers · `parse_input_modalities` (task-1) · `ModelsPage` AdminModelItem interface + Inputs column · `ModelCatalogTable` ModelEntry + Inputs column · `Badge`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Surface each model's input_modalities (read-only) on the admin catalog APIs (lean public /v1/models), and list them as capability badges on the dashboard models page
Framings weighed: add input_modalities to a NEW AdminCatalogModelItem + AdminModelItem (public ModelItem stays lean) and render a read-only Inputs column (chosen — smallest seam, reuses task-1 data + the existing Badge/DataTable, keeps the public OpenAI shape untouched) · widen the SHARED ModelItem so /v1/models also carries it (rejected by Tin — keep public lean) · a separate /admin/capabilities endpoint (rejected: duplicates the model list, extra round-trip) · editable capabilities with a PUT path (rejected by Tin — this milestone is a capabilities LISTING, not an editor; deferrable)
Must:
<must>
  - GET /admin/catalog/models and GET /admin/models each return `input_modalities` on every model entry — a JSON array of the accepted input types (e.g. ["text"], ["text","image"], ["audio"]), derived from the catalog `input_modalities` CSV via parse_input_modalities and SORTED for deterministic order.
  - GET /v1/models STAYS LEAN: it does NOT gain input_modalities — the public OpenAI-compatible shape is byte-identical to today (Tin 2026-06-30).
  - The admin field is ADDITIVE: all other fields and ordering are byte-identical; existing clients/tests that ignore the new key keep passing.
  - The dashboard /app/models page (ModelsPage, GET /admin/models) shows a read-only "Inputs" column rendering each accepted type as a Badge chip. The ModelCatalogTable (/admin/catalog/models) likewise gains an Inputs column.
  - Read-only: NO edit control, NO new write/PUT path. The existing enable/disable toggle and re-sync are unchanged.
  - A model with an empty/absent capability set degrades gracefully (defaults to ["text"] from the catalog default; the UI shows the text badge, never a crash or blank).
</must>
Reject:
<reject>
  - (no new request-validation rejections — this task is read-only/additive; it introduces no new 4xx. Existing 409 ERR_CATALOG_EMPTY / 403 owner-or-admin gates on these endpoints are unchanged.)
</reject>
After:
<after>
  - Every model entry across the three endpoints carries a sorted input_modalities array matching its catalog row.
  - Operators see, per model, the input types it accepts as badges on the models page — with no change to enable/disable behavior.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ none material — RESOLVED by Tin 2026-06-30: keep /v1/models lean, so the field is admin-only via a NEW AdminCatalogModelItem (the public OpenAI shape is untouched). Residual: list_catalog_models stops delegating to list_models — it now re-maps the same use-case output, so the two must not drift; a test asserts /admin/catalog/models carries the same id/pricing as /v1/models PLUS input_modalities.
  - [x] input_modalities already lives on CatalogModel + ModelRow (task 1) and is returned by the list use case — confirmed; no new query/migration.
  - [x] Badge + DataTable already exist and are the established chip/table patterns — confirmed (components/ui).
  - [x] /admin/models is the real operator page (not /admin/catalog/models) — confirmed by reading ModelsPage.tsx; milestone doc reconciled.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: /v1/models stays lean (no input_modalities)
  Given a synced catalog where gpt-4o accepts text+image and whisper-1 accepts audio
  When a JWT client GETs /v1/models
  Then NO entry includes an input_modalities key
  And the response is byte-identical to today (id, name, pricing only)

Scenario: /admin/catalog/models exposes input_modalities
  Given the same synced catalog
  When a JWT client GETs /admin/catalog/models
  Then each entry includes a sorted input_modalities array (gpt-4o ["image","text"], whisper-1 ["audio"])
  And the id + pricing fields match the corresponding /v1/models entry

Scenario: /admin/models exposes input_modalities alongside enabled
  Given the same catalog and an owner/admin JWT
  When the client GETs /admin/models
  Then each entry includes input_modalities AND the existing enabled flag
  And the enable/disable behavior is unchanged

Scenario: dashboard models page lists capabilities as badges
  Given /admin/models returns gpt-4o with input_modalities ["image","text"]
  When the operator views /app/models
  Then the gpt-4o row shows an Inputs column with a "text" badge and an "image" badge
  And the enable/disable toggle still works

Scenario: a model with the default capability set degrades gracefully
  Given a chat model whose input_modalities is the default ["text"]
  When it is listed on the page
  Then the Inputs column shows a single "text" badge (no crash, no blank cell)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /v1/models             200 -> { object:"list", data: [ ModelItem ] }            # UNCHANGED, lean
GET /admin/catalog/models  200 -> { object:"list", data: [ AdminCatalogModelItem ] } # NEW item type
GET /admin/models          200 -> { object:"list", data: [ AdminModelItem ] }         # owner/admin

ModelItem            = { id, name, context_length, prompt_per_token, completion_per_token, object:"model" }
                       # ^ UNCHANGED — public OpenAI shape, NO input_modalities (Tin: keep lean)
AdminCatalogModelItem = ModelItem fields + { input_modalities: string[] }   # NEW schema
AdminModelItem        = { id, name, context_length, enabled, input_modalities: string[] }  # + NEW field

input_modalities = sorted(parse_input_modalities(<catalog CSV>))     # e.g. ["image","text"], ["audio"], ["text"]
  - tokens ∈ {text,image,audio} (task-1 VALID_INPUT_MODALITIES; video deferred)
  - always present + non-empty (catalog default "text" → ["text"]); sorted = deterministic
  - /admin/catalog/models is built from the SAME ListModelsForTenantUseCase as /v1/models
    (re-mapped, no longer delegating) so id+pricing never drift; only the extra field differs.

Dashboard (read-only):
  /app/models (ModelsPage, GET /admin/models): new "Inputs" column → one <Badge> per token
  ModelCatalogTable (GET /admin/catalog/models): new "Inputs" column (grid-cols-4 → 5)
  No edit control, no PUT, no new mutation. enable/disable + re-sync unchanged.

Schema: NO DB/migration change (input_modalities column exists from task 1). get_admin_models SELECT
  gains ModelRow.input_modalities; list_catalog_models re-maps the use-case output to AdminCatalogModelItem.
Untouched: GET /v1/models (lean), pricing/markup, enabled-override logic, FallbackModelRouter, the v55 guards, PUT /admin/models/{id}.
```

Least-sure flag surfaced at freeze: [contract] public-surface breadth — decided LEAN: /v1/models stays byte-identical; input_modalities is admin-only via a NEW AdminCatalogModelItem (Tin chose this over widening the shared/public ModelItem). Residual: [test] list_catalog_models now re-maps (not delegates) the use-case output → a test pins id+pricing parity with /v1/models so the two can't drift.

Status: FROZEN @ v1 — approved by Tin 2026-06-30 (admin-only field; lean public /v1/models)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavior parity (additive field present on all 3 endpoints + UI column)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  BACKEND (apps/gateway/tests/catalog_input_capabilities/test_capabilities_surface.py):
  - test_v1_models_stays_lean: seed catalog (gpt-4o text,image; whisper-1 audio) / GET /v1/models / assert NO entry has an input_modalities key (public shape byte-identical)
  - test_admin_catalog_models_includes_input_modalities: GET /admin/catalog/models / assert each entry has sorted input_modalities AND id+pricing match the /v1/models entry
  - test_admin_models_includes_input_modalities: owner JWT / GET /admin/models / assert input_modalities present AND enabled flag still present
  FRONTEND (apps/dashboard/tests-bff/model-capabilities.test.tsx):
  - test_models_page_shows_inputs_badges: mock /admin/models → gpt-4o input_modalities ["image","text"] / render ModelsPage / assert "text" + "image" badges in the row / assert the enable Switch still renders
  - test_default_text_only_badge: model with ["text"] / assert exactly one "text" badge, no crash
</test_plan>

Tests live in: `apps/gateway/tests/catalog_input_capabilities/test_capabilities_surface.py` `apps/dashboard/tests-bff/model-capabilities.test.tsx` · MUST run red (field/column missing) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/catalog/api/schemas.py` `apps/gateway/src/gateway/catalog/api/router.py` `apps/gateway/src/gateway/catalog/domain/entities.py` `apps/gateway/src/gateway/catalog/infrastructure/repository.py` `apps/gateway/tests/catalog_input_capabilities/` `apps/gateway/tests/catalog/test_model_catalog.py` `apps/gateway/tests/catalog_input_modalities/test_input_modalities.py` `apps/dashboard/components/models/ModelsPage.tsx` `apps/dashboard/components/models/ModelCatalogTable.tsx` `apps/dashboard/tests-bff/model-capabilities.test.tsx` — the two response schemas, the mappers, the BE red test dir, the two dashboard tables + the FE red test. SCOPE EXPANDED at build (grounding miss): input_modalities had to be THREADED through the markup path (`MarkedUpModel` in entities.py + the `list_active_models_with_markup` SELECT in repository.py) — task 1 added it to CatalogModel/ModelRow but NOT to the marked-up DTO the list use case actually returns. Two existing tests updated as CONTRACT-DRIVEN (not weakened): tests/catalog/test_model_catalog.py (twin-vs-v1 byte-identity → field-level parity + lean-v1/has-admin assertions — STRONGER) and tests/catalog_input_modalities/test_input_modalities.py (task-1 SC6 "not surfaced yet" → AdminModelItem now surfaces it; ModelItem-lean + PutModelRequest guards preserved). No migration, no DB schema change, no new endpoint, no new write/PUT path (put_admin_model's response just carries the now-required field).
Strategy (ordered batches): BACKEND — 1. schemas: ModelItem UNCHANGED; add AdminCatalogModelItem (+ AdminCatalogModelsListResponse) and add input_modalities to AdminModelItem · 2. router: list_models UNCHANGED; list_catalog_models re-maps the use-case output to AdminCatalogModelItem with sorted(parse_input_modalities(...)); get_admin_models SELECT += ModelRow.input_modalities and maps it · 3. BE red→green. FRONTEND — 4. ModelsPage: AdminModelItem interface + Inputs column (Badge per token) · 5. ModelCatalogTable: ModelEntry + Inputs column (grid-cols-5) · 6. FE red→green.
Known-problem fixes: sort the parsed set (frozenset has no order → non-deterministic JSON without sorted()). Keep the field additive (do NOT reorder or rename existing keys — byte-identical to existing model-mgmt/catalog tests). Default-safe: parse_input_modalities("")→empty, but the catalog default is "text" so entries are non-empty; the UI must still tolerate an empty array (render nothing, not crash). FE field names byte-identical to the gateway JSON. Use the existing Badge variant; do not invent a new design token.
Strategy actually used: as planned, with one grounding correction harvested at build (delegated BE→backend-expert, FE→frontend-expert in parallel, disjoint trees). KEY [AI] build decision: input_modalities is surfaced by THREADING the raw CSV through the markup DTO — `MarkedUpModel` (entities.py) gained `input_modalities: str = "text"` and `SqlAlchemyCatalogRepository.list_active_models_with_markup` selects + carries it; the endpoints decide presentation (list_models stays lean for /v1/models; list_catalog_models maps AdminCatalogModelItem; get_admin_models + put_admin_model map AdminModelItem). My §0 wrongly assumed task 1 already exposed it on the use-case output — it had only reached CatalogModel/ModelRow, not MarkedUpModel. FE = read-only Badge column on ModelsPage + ModelCatalogTable; BFF confirmed transparent passthrough (no change). Two prior-task tests updated as contract-driven (documented in §5 scope).
Safety rule (feature-specific): read-only/additive — no write path, no mutation, no migration; the only data-plane change is one extra column in the get_admin_models SELECT (a pure read).
Code lives in: `./src/`
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

- [x] all tests pass — BE 49 passed (one pytest process: catalog_input_capabilities + catalog + model_mgmt + catalog_input_modalities + catalog_sync_trigger), re-run first-hand; FE 25 passed (model-capabilities + model-mgmt + model-catalog-paging-search), agent + diff/test review
- [x] coverage did not decrease — additive field threaded end-to-end; existing suites green; the two updated tests are STRONGER/correct (not gutted)
- [~] no test or contract was altered during build — frozen §3 contract UNCHANGED; TWO existing tests deliberately UPDATED as contract-driven (justified below + §5), NOT weakened — refute-read EARNED
- [x] the green was EARNED — refute-read below; the two test edits were adversarially checked and the new BE test pins lean-v1 + admin-has-field + pricing parity
- [x] concurrency / timing safe — pure read path; one extra column in two SELECTs; no new IO/locks/mutation; read-only UI
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps; parameterized selects; input_modalities is non-secret capability config
- [x] layering & dependencies follow CONVENTIONS.md — DTO (entities) ← repository ← router ← schema; FE component-local interfaces + Badge primitive; BFF passthrough untouched
- [ ] a person reviewed and approved the change — Tin (orchestrator auto-PASS under autonomy:auto; the two test-file edits surfaced explicitly for spot-audit)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] GET /v1/models is byte-identical (NO input_modalities key) — confirmed: test_v1_models_stays_lean + the updated test_admin_catalog_models_matches_v1_models asserts `"input_modalities" not in v1_entry`; ModelItem + list_models diffs show zero change
- [x] GET /admin/catalog/models carries sorted input_modalities AND id+pricing match /v1/models — confirmed: test_admin_catalog_models_includes_input_modalities + parity assertions
- [x] GET /admin/models carries input_modalities alongside enabled — confirmed: test_admin_models_includes_input_modalities; PUT response also carries it (put_admin_model diff)
- [x] dashboard /app/models shows a read-only Inputs column of Badge chips, enable Switch intact — confirmed: model-capabilities.test.tsx (badges render through the bffGet mock + Switch still present)
- [x] the field is threaded through the markup DTO, not faked — confirmed: MarkedUpModel + repository SELECT diffs read first-hand

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — input_modalities: ModelRow→repository SELECT→MarkedUpModel→list_catalog_models/get_admin_models/put_admin_model→AdminCatalog/AdminModelItem→BFF passthrough→ModelsPage/ModelCatalogTable Badge. Every hop read in the diffs.
- [x] DEAD-CODE (code) — AdminCatalogModelItem/AdminCatalogModelsListResponse used by list_catalog_models; parse_input_modalities imported+used; no orphan.
- [x] SEMANTIC — read both modified test diffs IN FULL: test_model_catalog parity rewrite is stronger (pricing approx + lean-v1 + admin-has-field); test_input_modalities flip preserves the ModelItem-lean + PutModelRequest guards. Legitimate contract evolution, no coverage gutted.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: self (orchestrator, post-subagent independent review of TWO parallel agents) · adversarially checked: (1) the two MODIFIED existing tests are the prime cheat-surface — I read both diffs in full: test_model_catalog dropped a byte-identity assert that was only valid under the OLD delegating impl and REPLACED it with STRONGER field-level parity + an explicit "input_modalities not in /v1/models" + "in /admin/catalog/models" assertion (it now directly tests the frozen contract); test_input_modalities only flipped the ONE assertion task 2 deliberately inverts (AdminModelItem surfaces the field) while KEEPING the ModelItem-lean and PutModelRequest-excluded guards — neither edit weakens coverage to force green; (2) lean /v1/models proven by the unchanged ModelItem/list_models diffs AND a positive test asserting the key is absent; (3) the field is real, not faked — threaded through MarkedUpModel + the repository SELECT (read first-hand), not hardcoded in the mapper; (4) re-ran 49 BE tests myself in one process (green) + ruff clean + pyright 0; (5) FE badges render through the bffGet mock (BFF passthrough proven end-to-end), enable Switch intact. RESIDUE (non-blocking): apps/dashboard/node_modules (agent's vitest install) is not gitignored — cleaned pre-gate; a couple of sync helper tests carry the asyncio mark (cosmetic, as in tasks 1/3/4). No overfit / stubbed-away logic.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: orchestrator (auto-PASS, autonomy:auto) · date: 2026-06-30 — the two contract-driven test edits surfaced for Tin's spot-audit; no security finding.

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose add input_modalities to a NEW AdminCatalogModelItem + AdminModelItem (public ModelItem stays lean) and render a read-only Inputs column; rejected widen the SHARED ModelItem so /v1/models also carries it (rejected by Tin — keep public lean) · a separate /admin/capabilities endpoint (rejected: duplicates the model list, extra round-trip) · editable capabilities with a PUT path (rejected by Tin — this milestone is a capabilities LISTING, not an editor; deferrable)
- [human] freeze — froze §3 @ v1 (approved by Tin 2026-06-30 (admin-only field; lean public /v1/models))
- [AI] build — strategy used: as planned, with one grounding correction harvested at build (delegated BE→backend-expert, FE→frontend-expert in parallel, disjoint trees). KEY [AI] build decision: input_modalities is surfaced by THREADING the raw CSV through the markup DTO — `MarkedUpModel` (entities.py) gained `input_modalities: str = "text"` and `SqlAlchemyCatalogRepository.list_active_models_with_markup` selects + carries it; the endpoints decide presentation (list_models stays lean for /v1/models; list_catalog_models maps AdminCatalogModelItem; get_admin_models + put_admin_model map AdminModelItem). My §0 wrongly assumed task 1 already exposed it on the use-case output — it had only reached CatalogModel/ModelRow, not MarkedUpModel. FE = read-only Badge column on ModelsPage + ModelCatalogTable; BFF confirmed transparent passthrough (no change). Two prior-task tests updated as contract-driven (documented in §5 scope).
- [AI] verify — gate PASS (reviewed by orchestrator (auto-PASS, autonomy:auto))

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
