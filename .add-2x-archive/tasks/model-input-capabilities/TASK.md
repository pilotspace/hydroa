# TASK: Additive per-model input_modalities catalog descriptor

slug: model-input-capabilities · created: 2026-06-30 · stage: production
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
- `apps/gateway/src/gateway/catalog/domain/entities.py` — `Modality` Literal + `VALID_MODALITIES` frozenset (the existing coarse classifier: chat·embedding·image·audio_stt·audio_tts) · `CatalogModel`(frozen dataclass: id·name·context_length·prompt/completion_usd·`modality="chat"`·`provider="openrouter"`) · `ModelRow`(id·name·context_length·active·modality·provider). THIS is where the new `input_modalities` descriptor + its `VALID_INPUT_MODALITIES` set live. Pattern to mirror: Literal + TEXT-at-rest (no PG ENUM) so adding a modality needs no ALTER TYPE.
- `apps/gateway/src/gateway/catalog/infrastructure/orm.py` — `ModelRow(Base)` table `models`: `modality`/`provider` are `Text, nullable=False, server_default=...`. Add `input_modalities` column the same way (TEXT, NOT NULL, server_default). `TenantModelOverrideRow` exists (per-tenant model enable/disable, composite PK) — precedent for v56 presets, NOT touched here.
- `apps/gateway/migrations/versions/b7c1d2e3f4a5_catalog_modality_provider.py` — the EXACT additive-migration template to copy: `op.add_column("models", sa.Column("<name>", sa.Text(), nullable=False, server_default="<v>"))`; instant on PG 11+, no rewrite; downgrade drops the column. New revision chains off the current head (find via `alembic heads`).
- `apps/gateway/src/gateway/catalog/infrastructure/repository.py` — `SqlAlchemyCatalogRepository`. CRITICAL: `_upsert_model` writes ONLY id·name·context_length·active (on_conflict_do_update set_ = name·context_length·active) — it does NOT write `modality`/`provider`. So a real OpenRouter sync leaves classifier columns at their default/seeded value and never clobbers them. `input_modalities` MUST follow the same rule: NOT written by `_upsert_model` (preserved across sync), populated by seed/default only — unless we deliberately add it to the upsert. `list_active_models_with_markup`/`get_latest_snapshot_prices` select explicit columns (would add `input_modalities` if surfaced).
- `apps/gateway/src/gateway/catalog/infrastructure/openai_seed.py` — the ONLY place `modality`/`provider` are set per-model (embedding/image/audio_stt/audio_tts rows). The natural seed point to set each model's `input_modalities` explicitly.
- `apps/gateway/src/gateway/catalog/infrastructure/openrouter_source.py` — builds `CatalogModel` from the upstream list; uses the dataclass defaults (modality="chat"). The default-derivation rule (chat ⇒ {text}) keeps these rows byte-identical.
- `apps/gateway/src/gateway/catalog/application/use_cases.py` — `SyncCatalogUseCase` · `ListModelsForTenantUseCase` (returns `MarkedUpModel` list; raises `CatalogEmptyError`). The list use case is the seam the admin-surface task (next) extends.
- `apps/gateway/src/gateway/catalog/api/schemas.py` — `ModelItem`(GET /v1/models + reused by /admin/catalog/models) · `AdminModelItem`(id·name·context_length·enabled; GET /admin/models) · `PutModelRequest`. Field-shape ORIGIN for capabilities; the SURFACING is the `capabilities-admin-surface` task, but this task freezes the entity/field name.
- `apps/gateway/src/gateway/catalog/api/router.py` — `list_catalog_models` (GET /admin/catalog/models → delegates to `list_models`, dashboard reads this via BFF session JWT) · `get_admin_models` (GET /admin/models, owner/admin, raw `select(ModelRow…)` + outerjoin overrides) · `put_admin_model`. These are READ surfaces capabilities will eventually flow through (next task), shown here so §3 names the right column set.
Context (working folder): `.add/milestones/v55/MILESTONE.md` (this task = breadth task 1, dependency of `capabilities-admin-surface` + `unsupported-input-guard`; shared decision: INPUT MODALITY ≠ MODALITY; additive-only, default-derived from `modality`). Existing precedent migrations for additive catalog columns: `b7c1d2e3f4a5` (modality/provider), the `pricing_unit`/tiered-token columns on `pricing_snapshots`. Test homes (gateway): `apps/gateway/tests/<feature>/` one dir per feature with a conftest; mirror that for a `catalog_input_modalities/` suite.
Honors (patterns / conventions): CLAUDE.md — additive migrations, design-for-failure (the column carries a safe default so an un-synced/under-seeded catalog never breaks reads). PROJECT.md — byte-identical data seams (existing `modality`/`provider`/`ModelItem` shape and every frozen catalog test stay unchanged); Literal-validated-at-boundary + TEXT-at-rest (no PG ENUM). Repository safety rule §5 — all sync writes in ONE transaction; pricing snapshots append-only. uuid7 IDs generated explicitly pre-flush.
Anchors the contract cites: `CatalogModel` · `ModelRow`(domain entity + ORM) · `Modality`/`VALID_MODALITIES` (and the new `input_modalities`/`VALID_INPUT_MODALITIES` mirror) · `SqlAlchemyCatalogRepository._upsert_model` (the no-clobber rule) · `openai_seed` model rows · the additive-migration shape from `b7c1d2e3f4a5` · `ModelItem`/`AdminModelItem` (field-name origin, surfaced next task).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-model `input_modalities` catalog descriptor — additive, default-derived from `modality`
Framings weighed: single normalized TEXT-at-rest set column on `models` validated against a `VALID_INPUT_MODALITIES` Literal/frozenset, mirroring the existing `modality` pattern (chosen) · one boolean column per input type (accepts_text/image/audio/video) — rejected: rigid, needs a migration per new type · a separate 1:N `model_capabilities` table — rejected: over-engineered for a small fixed token set; defer until a capability needs its own attributes
Must:
<must>
  - `input_modalities` is an ADDITIVE column on `models`: TEXT-at-rest, NOT NULL, with a server_default — every pre-existing row reads byte-identically (no rewrite; PG 11+ instant DDL, per the b7c1d2e3f4a5 template).
  - The domain entities `CatalogModel` and `ModelRow` gain an `input_modalities` field; a `VALID_INPUT_MODALITIES` frozenset {text, image, audio} + an `InputModality` Literal mirror the `Modality`/`VALID_MODALITIES` shape; values are validated at the application boundary (not a PG ENUM). (`video` is deliberately deferred from v55 — Tin 2026-06-30.)
  - A pure `default_input_modalities(modality) -> set` derivation maps the existing classifier to a conservative default: chat→{text}, embedding→{text}, image→{text}, audio_tts→{text}, audio_stt→{audio}. The dataclass default and the column server_default both realize chat⇒{text}.
  - The persisted value is NORMALIZED: deduped, restricted to VALID_INPUT_MODALITIES, and order-stable (canonical order text<image<audio) so reads and tests are deterministic.
  - The seed path (`openai_seed`) sets `input_modalities` explicitly per seeded model; `SqlAlchemyCatalogRepository._upsert_model` does NOT write `input_modalities` (preserved across every OpenRouter sync — same no-clobber rule as `modality`/`provider`).
  - Every model row carries a NON-EMPTY validated set; the field is readable from the domain entity so the next task (`capabilities-admin-surface`) can surface it and the guard can enforce it. No surfacing or enforcement in THIS task.
</must>
Reject:
<reject>
  - an input-modality token not in VALID_INPUT_MODALITIES (e.g. "pdf") at the validation boundary -> "invalid_input_modality"
  - an empty / all-blank input_modalities set (a model must accept at least one input type) -> "empty_input_modalities"
</reject>
After:
<after>
  - the `models` table and both domain entities expose a non-empty, validated, normalized `input_modalities` for every row; existing rows default to the `modality`-derived set; `modality`/`provider`/`ModelItem`/`AdminModelItem`/pricing reads and every frozen catalog test are byte-identical; nothing yet rejects traffic or changes any API response body.
</after>
Assumptions — lowest-confidence first (all RESOLVED at specify by Tin via AskUserQuestion 2026-06-30):
<assumptions>
  ✓ RESOLVED — conservative default-derivation (chat ⇒ {text} ONLY), opt-in enrichment via openai_seed + admin edit. Tin chose "conservative + opt-in" over sync-time inference / static multimodal allowlist. Posture: a chat model is text-only until explicitly enriched; bounded by the default-OFF guard so no traffic breaks. (Was the lowest-confidence call; now decided.)
  ✓ RESOLVED — storage representation = a normalized TEXT token set (canonical-ordered, delimiter-joined), mirroring `modality`'s TEXT-at-rest — NOT a PG array or JSON column. Keeps the no-ENUM/no-array convention and frozen-seam simplicity. (Recommended + adopted; no objection raised.)
  ✓ RESOLVED — token set is exactly {text, image, audio}; `video` DEFERRED from v55 (Tin). The guard will not reason about video input until a later slice; note as a v55 out-of-scope item.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Additive column, existing rows byte-identical
  Given a models row that predates this change with modality="chat"
  When the additive migration runs
  Then that row has input_modalities="text"
  And its id, name, context_length, active, modality, provider, created_at, updated_at are unchanged

Scenario: Non-chat existing rows re-derived in the same migration
  Given pre-existing rows with modality in {embedding, image, audio_tts, audio_stt}
  When the additive migration runs
  Then embedding/image/audio_tts rows have input_modalities="text" and audio_stt rows have input_modalities="audio"
  And no other column on those rows changes

Scenario: Default derivation is a pure total function
  Given the helper default_input_modalities(modality)
  When called with each of chat, embedding, image, audio_tts, audio_stt, and an unknown value
  Then it returns {"text"} for chat/embedding/image/audio_tts, {"audio"} for audio_stt, and {"text"} for unknown (safe fallback)

Scenario: Normalization is deterministic
  Given the values ["audio", "text", "text"] passed to normalize_input_modalities
  When normalized
  Then the result is the canonical, deduped string "text,audio"

Scenario: Seed sets capabilities explicitly and sync never clobbers them
  Given a model seeded with input_modalities {text, image}
  When a later OpenRouter catalog sync upserts that same model id with a changed name/context_length
  Then the row's name/context_length update but input_modalities stays "text,image"
  And _upsert_model's written column set is unchanged from today (no input_modalities key)

Scenario: Field is readable from the domain entity with no API surfacing yet
  Given the feature has shipped
  When a CatalogModel/ModelRow is loaded and GET /v1/models, GET /admin/catalog/models, GET /admin/models are called
  Then entity.input_modalities exposes the normalized set
  And every one of those three response bodies is byte-identical to before (no new field surfaced; no enforcement)

Scenario: Unknown input-modality token is rejected
  Given input_modalities containing an unknown token "pdf"
  When normalize_input_modalities validates it at the boundary
  Then it raises an error carrying code "invalid_input_modality"
  And no model row is written or modified

Scenario: Empty input-modality set is rejected
  Given an empty / all-blank input_modalities value
  When normalize_input_modalities validates it at the boundary
  Then it raises an error carrying code "empty_input_modalities"
  And no model row is written or modified
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# This task has NO HTTP surface. The contract is the SCHEMA + DOMAIN API that the
# next task (capabilities-admin-surface) and the guard (unsupported-input-guard) build on.

DDL — additive migration  c2e4a6f8b0d3 ; down_revision = "e2f4a6b8c0d1" (real current head)
  # GROUND-FACT CORRECTION (verify): the frozen draft said down_revision="f2a4c6e8b0d3", but that
  # was an orchestrator head-detection bug (a regex miss — f2a4c6e8b0d3 is mid-chain, used as a
  # down_revision by b3d5f7a9c1e4). Authoritative `alembic heads` = e2f4a6b8c0d1; the build chained
  # off it, preserving the contract's INTENT ("chains off the current head"). Single head after:
  # c2e4a6f8b0d3 (verified). This is a factual fix to a ground error, NOT a weakening of the contract.
  upgrade:
    ALTER TABLE models ADD COLUMN input_modalities TEXT NOT NULL DEFAULT 'text';
    # server_default 'text' realizes chat/embedding/image/audio_tts ⇒ {text} for every existing row.
    UPDATE models SET input_modalities = 'audio' WHERE modality = 'audio_stt';   # the only non-text default
  downgrade:
    ALTER TABLE models DROP COLUMN input_modalities;
  Properties: instant DDL on PG 11+ (no rewrite); no pricing_snapshots change; one-tx.

Domain — apps/gateway/src/gateway/catalog/domain/entities.py
  InputModality            = Literal["text", "image", "audio"]            # video deferred from v55
  VALID_INPUT_MODALITIES   : frozenset[str] = {"text", "image", "audio"}
  _INPUT_MODALITY_ORDER    = ("text", "image", "audio")                   # canonical sort key
  default_input_modalities(modality: str) -> frozenset[str]
     audio_stt -> {"audio"} ; chat|embedding|image|audio_tts|<unknown> -> {"text"}   # pure, total, safe fallback
  normalize_input_modalities(values: Iterable[str]) -> str
     strip blanks, dedupe, sort by _INPUT_MODALITY_ORDER, join with ","      # e.g. ["audio","text","text"] -> "text,audio"
     unknown token  -> raise InvalidInputModalityError   (code "invalid_input_modality")
     empty result   -> raise EmptyInputModalitiesError   (code "empty_input_modalities")
  parse_input_modalities(text: str) -> frozenset[str]                       # "text,image" -> {"text","image"}
  CatalogModel.input_modalities : str = field(default="text")   # normalized csv, mirrors modality: str
  ModelRow.input_modalities     : str = field(default="text")   # normalized csv, mirrors modality: str

Domain errors — apps/gateway/src/gateway/catalog/domain/errors.py
  InvalidInputModalityError(code = "invalid_input_modality")
  EmptyInputModalitiesError(code = "empty_input_modalities")

ORM — apps/gateway/src/gateway/catalog/infrastructure/orm.py
  ModelRow.input_modalities : Mapped[str] = mapped_column(Text, nullable=False, server_default="text")

Population seams
  openai_seed.py     : each seeded model passes an explicit normalized input_modalities (image/vision rows enriched here).
  repository._upsert_model : UNCHANGED write set (no input_modalities key) -> sync NEVER clobbers it (no-clobber, matches modality).

UNCHANGED (byte-identical — assert in tests, do NOT touch):
  ModelItem, AdminModelItem, PutModelRequest, ModelsListResponse, AdminModelsListResponse  (no new field this task)
  GET /v1/models · GET /admin/catalog/models · GET /admin/models  response bodies
  list_models / ListModelsForTenantUseCase / list_active_models_with_markup  (no input_modalities surfaced yet)
  modality / provider columns + every existing catalog test
```

Least-sure flag surfaced at freeze:
  [contract] Entity field type is `str` (normalized csv) NOT `frozenset` — chosen to MIRROR the existing
  `modality: str` seam exactly and keep the frozen dataclass byte-identical in spirit; set-semantics live in
  the helper functions (parse/normalize). Cost if wrong: the guard task would prefer a set on the entity and
  we'd add a parse call there instead — a one-line cost, fully reversible, contained to the next task. Every
  §1 assumption (conservative default · TEXT-at-rest · {text,image,audio}, video deferred) was resolved by Tin.

Status: FROZEN @ v1 — approved by Tin 2026-06-30 (AskUserQuestion: "Freeze v1, proceed"). Changing this contract now = a change request back to SPECIFY.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: changed catalog files ≥ 80% (project floor); 8 tests, one per §2 scenario.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_sc1 additive column: insert chat ModelRow w/o input_modalities → server_default 'text'; other cols unchanged
  - test_sc2 migration data-patch: insert one row per modality, run the exact UPDATE…WHERE modality='audio_stt' → audio only; modality unchanged
  - test_sc3 default_input_modalities: chat/embedding/image/audio_tts/unknown→{text}, audio_stt→{audio}
  - test_sc4 normalize: ["audio","text","text"]→"text,audio"; reverse→canonical; single; blanks stripped
  - test_sc5 no-clobber: seed row 'text,image', call REAL repo._upsert_model(CatalogModel) → name/context updated, input_modalities preserved
  - test_sc6 readable + no surfacing: entity.input_modalities default 'text'; ModelItem/AdminModelItem/PutModelRequest model_fields lack it
  - test_sc7 invalid token "pdf" → InvalidInputModalityError code "invalid_input_modality"
  - test_sc8 empty / all-blank → EmptyInputModalitiesError code "empty_input_modalities"
</test_plan>

Tests live in: `apps/gateway/tests/catalog_input_modalities/` · ran RED for the right reason (ImportError/AttributeError/missing-column) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/catalog/` `apps/gateway/migrations/versions/` `apps/gateway/tests/catalog_input_modalities/`   — domain entities/errors, ORM column, openai_seed enrichment, openrouter_source default, repository read-mapping; the new additive migration; the new red suite. No HTTP/router/schema files (surfacing is the next task).
Strategy (ordered batches): 1. domain entities/errors helpers · 2. ORM column · 3. additive migration · 4. openai_seed enrichment · 5. red→green.
Known-problem fixes: down_revision must be the REAL alembic head (not a regex-guess) → resolve via `alembic heads`; lazy-import errors inside normalize to dodge circular import; raw UPDATE needs `expire_all()` before re-SELECT; ONE pytest process (DB cross-wipe).
Strategy actually used: as planned (delegated to backend-expert subagent). Deviation: corrected the contract's down_revision from the orchestrator's buggy f2a4c6e8b0d3 to the real head e2f4a6b8c0d1 (ground-fact fix, contract intent preserved). repository.py left UNTOUCHED (no-clobber by omission — cleaner than the GROUND's "or add to upsert" alternative). openrouter_source.py unchanged (dataclass default "text" suffices).
Safety rule (feature-specific): additive column carries a safe server_default so an un-synced/under-seeded catalog never breaks reads; sync upsert never writes input_modalities (no-clobber).
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

- [x] all tests pass — 62 passed (8 new + 54 regression) in one pytest process, re-run first-hand by the orchestrator
- [x] coverage did not decrease — changed catalog files clean; regression suites byte-identical (no behavior touched)
- [x] no test or contract was altered during build — only the one ground-fact down_revision correction (recorded in §3, intent preserved)
- [x] the green was EARNED, not gamed — refute-read below; SC5 drives the REAL repo._upsert_model, SC6 asserts schema-absence, SC2 runs the exact migration UPDATE
- [x] concurrency / timing safe — single additive DDL + one-shot UPDATE in one implicit tx; no runtime concurrency added
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps; static SQL (no interpolation); ruff+pyright clean
- [x] layering & dependencies follow CONVENTIONS.md — pure-domain helpers in entities.py, errors subclass CatalogError, ORM-only column; no cross-layer leak
- [ ] a person reviewed and approved the change — Tin (orchestrator auto-PASS under autonomy:auto; surfaced for spot-audit)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `models` table has a NOT NULL `input_modalities` TEXT column defaulting 'text' — confirmed: ORM `sa_inspect` column present (SC1), migration ADD COLUMN server_default='text'
- [x] audio_stt rows resolve to 'audio', all others 'text' — confirmed: SC2 runs the migration UPDATE and asserts the per-modality result
- [x] a real sync upsert preserves a seeded 'text,image' value — confirmed: SC5 calls repository._upsert_model and reads back 'text,image'
- [x] the three /models response bodies are byte-identical (no new field) — confirmed: SC6 asserts ModelItem/AdminModelItem/PutModelRequest model_fields lack input_modalities; provider_seam+catalog+model_mgmt suites green
- [x] `alembic heads` resolves a single head — confirmed: head = c2e4a6f8b0d3 → e2f4a6b8c0d1 (orchestrator-run)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — new symbols referenced: default_input_modalities/normalize_input_modalities/parse_input_modalities + both error classes exercised by the suite; ORM column + migration build the schema; seed sets the field. (parse_input_modalities + default_input_modalities are PUBLIC API for the next two tasks — intentional forward-wiring, exercised by SC3; not dead.)
- [x] DEAD-CODE (code) — no orphaned symbol: every added function is either tested now (SC3/SC4/SC7/SC8) or a declared seam for capabilities-admin-surface / unsupported-input-guard.
- [x] SEMANTIC — read the full impl diff first-hand (entities/errors/orm/seed/migration): matches the frozen §3 shape exactly; lazy error-import dodges circular dep; static UPDATE SQL.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: self (orchestrator, post-subagent independent review) · adversarially checked: (1) probed for vacuous asserts — SC5 exercises the REAL _upsert_model not a mock, SC6 asserts true schema-absence not a tautology, SC2 runs the literal migration UPDATE; (2) probed the migration head — re-ran authoritative `alembic heads` (single head) after catching that the frozen down_revision was a regex bug; (3) probed scope — git status shows only in-scope files, repository.py untouched (no-clobber by omission); (4) re-ran the full regression set first-hand (62 passed) + ruff + pyright (0 errors). No overfit, no stubbed-away logic found.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: orchestrator (auto-PASS, autonomy:auto) · date: 2026-06-30  — surfaced to Tin for spot-audit; no security finding.

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose single normalized TEXT-at-rest set column on `models` validated against a `VALID_INPUT_MODALITIES` Literal/frozenset, mirroring the existing `modality` pattern; rejected one boolean column per input type (accepts_text/image/audio/video) — rejected: rigid, needs a migration per new type · a separate 1:N `model_capabilities` table — rejected: over-engineered for a small fixed token set; defer until a capability needs its own attributes
- [human] freeze — froze §3 @ v1 (approved by Tin 2026-06-30 (AskUserQuestion: "Freeze v1, proceed"). Changing this contract now = a change request back to SPECIFY.)
- [AI] build — strategy used: as planned (delegated to backend-expert subagent). Deviation: corrected the contract's down_revision from the orchestrator's buggy f2a4c6e8b0d3 to the real head e2f4a6b8c0d1 (ground-fact fix, contract intent preserved). repository.py left UNTOUCHED (no-clobber by omission — cleaner than the GROUND's "or add to upsert" alternative). openrouter_source.py unchanged (dataclass default "text" suffices).
- [AI] verify — gate PASS (reviewed by orchestrator (auto-PASS, autonomy:auto))

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
