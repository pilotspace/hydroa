# TASK: Seed GPT-Realtime dual-stream pricing into the catalog

slug: gpt-realtime-pricing-fields · created: 2026-07-01 · stage: production · risk: high
autonomy: conservative   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
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
  - `catalog/domain/entities.py:CatalogModel` (line ~128) — currently has one optional cache field
    (`cached_input_usd_per_token`, added by catalog-pricing-fields). Add 3 more `float | None =
    field(default=None)`: `audio_prompt_usd_per_token`, `audio_completion_usd_per_token`,
    `audio_cached_usd_per_token` — mirrors the existing field's exact shape.
  - `catalog/domain/entities.py:MarkedUpModel` (line ~186) — same treatment: add 3 matching
    `float | None = field(default=None)` output fields (`audio_prompt_per_token`,
    `audio_completion_per_token`, `audio_cached_per_token`), markup-adjusted mirrors of
    `cached_input_per_token`.
  - `catalog/infrastructure/repository.py:SqlAlchemyCatalogRepository` — 4 methods need the exact
    same 1-field-to-3-fields extension already done once for `cached_input_usd_per_token`:
    `_fetch_latest_prices` (bulk tuple + SELECT gains 3 more elements/columns),
    `list_active_models_with_markup` (SELECT + `MarkedUpModel` construction gains 3 more
    markup-adjusted fields), `_insert_snapshot` (passes the 3 new fields to `PricingSnapshotRow`
    — columns already exist, added by `gpt-realtime-schema-migration`, gate=PASS), `_price_changed`
    (comparison extends from a 3-way prompt/completion/cached tuple to a 6-way tuple so an
    audio-price-only change still appends a new append-only snapshot).
  - `catalog/infrastructure/minimax_seed.py` (61 lines, precedent) — read in full: a flat
    `list[CatalogModel]` module constant, hand-seeded with real, dated, cited pricing; wired into
    `main.py` via `CompositeCatalogSource(static_models=...)`. NEW file
    `catalog/infrastructure/gpt_realtime_seed.py` mirrors this exactly: one `CatalogModel` entry,
    id="gpt-realtime", modality="chat" (realtime is conversational, closest existing modality;
    no dedicated "realtime" modality exists), provider="openai", with all 6 real prices populated
    (confirmed 2026-07-02 via developers.openai.com/api/docs/models/gpt-realtime): prompt
    $4.00/1M, completion $16.00/1M, cached (text) $0.40/1M, audio_prompt $32.00/1M,
    audio_completion $64.00/1M, audio_cached $0.40/1M.
  - `main.py` (lines 44-45, 630-633) — `CompositeCatalogSource(primary=OpenRouterCatalogSource(...),
    static_models=MINIMAX_SEED_MODELS)`. Extend to
    `static_models=MINIMAX_SEED_MODELS + GPT_REALTIME_SEED_MODELS`.
  - `core/config.py:605` — `Settings.realtime_relay_openai_model: str = Field(default=
    "gpt-4o-realtime-preview")`. Per Tin's explicit decision (AskUserQuestion, 2026-07-02,
    "Switch relay to gpt-realtime"), change the default to `"gpt-realtime"` — the current GA
    model, cheaper and non-deprecated, matching the milestone's own billing-goal numbers. This is
    the ONE line in this task that changes real relay runtime behavior (which OpenAI model id
    every live realtime session actually calls), not just catalog/pricing data — flagged as the
    highest-risk single line in this task (see §1 Assumptions ⚠).
  - `catalog/api/schemas.py:ModelItem` / `AdminCatalogModelItem` — both currently have one optional
    `cached_input_usd_per_1m: float | None`. Add 3 more: `audio_prompt_usd_per_1m`,
    `audio_completion_usd_per_1m`, `audio_cached_usd_per_1m` (all `float | None`), same
    None-means-no-audio-stream semantics.
  - `catalog/api/router.py:list_models` / `list_catalog_models` — both construct `ModelItem`/
    `AdminCatalogModelItem` from a `MarkedUpModel`; extend both call sites with the 3 new
    `* 1_000_000` conversions, same None-passthrough pattern already used for
    `cached_input_usd_per_1m`.
  - `catalog/domain/ports.py:CatalogSource`/`CatalogRepository` — NO signature changes needed;
    both protocols are generic over `CatalogModel`/`MarkedUpModel` (confirmed by reading ports.py
    in full — method signatures don't enumerate fields).
  - `catalog/application/use_cases.py:ListModelsForTenantUseCase.execute` — pure passthrough
    (confirmed by reading in full); no changes needed.

Context (working folder): This is task 2 of 3 in the `gpt-realtime-pricing` milestone (schema [DONE,
  gate=PASS] -> catalog-seed [this task] -> relay-billing). GROUND-phase research this session
  (2026-07-02) surfaced a real mismatch: the relay's `realtime_relay_openai_model` config defaults
  to `"gpt-4o-realtime-preview"` (an older, more expensive preview model — text $5/$20/$2.50-cached,
  audio $40/$80/$2.50-cached per OpenAI's docs), NOT `"gpt-realtime"` (the GA model this milestone's
  own goal text was written around: text $4/$16/$0.40-cached, audio $32/$64/$0.40-cached). Surfaced
  to Tin via AskUserQuestion; he chose "Switch relay to gpt-realtime" — update the config default
  AND seed the catalog under the `"gpt-realtime"` id, rather than seeding two model ids or matching
  the old preview model's prices instead.
Honors (patterns / conventions):
  - Exact structural precedent: `catalog-pricing-fields` (this session, gate=PASS) already
    extended this EXACT SAME set of 7 files/methods to add ONE new optional cache-price field
    (`cached_input_usd_per_token`/`cached_input_per_token`/`cached_input_usd_per_1m`) end-to-end.
    This task repeats that precedent 3x (audio_prompt/audio_completion/audio_cached) for ONE model
    instead of extending an existing provider's models — same None-safe, additive-only,
    markup-multiplied, append-only-snapshot discipline throughout.
  - `pricing_snapshots` stays APPEND-ONLY — `_insert_snapshot` only ever INSERTs; `_price_changed`
    existing to detect when a NEW snapshot must be appended (never mutates a past row).
  - Seed-file precedent (`minimax_seed.py`/`openai_seed.py`): a flat `list[CatalogModel]` constant,
    real dated/cited prices in the module docstring, wired via `CompositeCatalogSource`'s
    `static_models` — no dynamic discovery for hand-seeded providers/models.
Anchors the contract cites: `CatalogModel.audio_prompt_usd_per_token` /
  `audio_completion_usd_per_token` / `audio_cached_usd_per_token`; `MarkedUpModel
  .audio_prompt_per_token` / `audio_completion_per_token` / `audio_cached_per_token`;
  `GPT_REALTIME_SEED_MODELS` (new); `Settings.realtime_relay_openai_model` (new default);
  `ModelItem`/`AdminCatalogModelItem.audio_prompt_usd_per_1m` / `audio_completion_usd_per_1m` /
  `audio_cached_usd_per_1m`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Seed GPT-Realtime's real dual-stream (text + audio) pricing into the catalog, so
  GET /v1/models and GET /admin/catalog/models list it with all 6 real prices, and switch the
  realtime relay to actually call the model whose price this seeds.
Framings weighed:
  - **(chosen)** Extend `CatalogModel`/`MarkedUpModel`/`ModelItem`/`AdminCatalogModelItem` with 3
    parallel `audio_*` fields (mirroring `cached_input_usd_per_token`'s existing 1-field
    precedent, done 3x), add one new hand-seeded `gpt_realtime_seed.py` (mirrors
    `minimax_seed.py`), extend `CompositeCatalogSource.static_models`, and change
    `Settings.realtime_relay_openai_model`'s default to `"gpt-realtime"`.
  - Seed GPT-Realtime WITHOUT changing the relay's default model id (keep calling
    `gpt-4o-realtime-preview`, price it at ITS real rates instead) — rejected per Tin's explicit
    decision (AskUserQuestion, 2026-07-02): he chose to switch the relay to the cheaper, current
    GA model rather than keep billing for the deprecated preview.
  - Seed BOTH model ids (`gpt-realtime` and `gpt-4o-realtime-preview`) so either one bills
    correctly regardless of which the relay calls — rejected per the same decision: Tin picked
    the single-model-id option, not the dual-seed hedge.
Must:
<must>
  - `CatalogModel` gains exactly 3 new fields: `audio_prompt_usd_per_token`,
    `audio_completion_usd_per_token`, `audio_cached_usd_per_token` — all `float | None`, default
    `None` (mirrors `cached_input_usd_per_token`'s exact shape).
  - `MarkedUpModel` gains exactly 3 new fields: `audio_prompt_per_token`,
    `audio_completion_per_token`, `audio_cached_per_token` — all `float | None`, default `None`,
    markup-multiplied mirrors of `cached_input_per_token` (None stays None, never coerced to 0).
  - A new `gpt_realtime_seed.py` module exports `GPT_REALTIME_SEED_MODELS: list[CatalogModel]`
    with exactly one entry: id="gpt-realtime", provider="openai", modality="chat",
    input_modalities="text", prompt_usd_per_token=0.000004, completion_usd_per_token=0.000016,
    cached_input_usd_per_token=0.0000004, audio_prompt_usd_per_token=0.000032,
    audio_completion_usd_per_token=0.000064, audio_cached_usd_per_token=0.0000004 (all confirmed
    real prices from developers.openai.com/api/docs/models/gpt-realtime, fetched 2026-07-02).
  - `main.py`'s `CompositeCatalogSource(static_models=...)` includes `GPT_REALTIME_SEED_MODELS` in
    addition to the existing `MINIMAX_SEED_MODELS` — after a catalog sync, "gpt-realtime" is an
    active model row with a pricing_snapshots row carrying all 6 real prices.
  - `Settings.realtime_relay_openai_model`'s default becomes `"gpt-realtime"` (was
    `"gpt-4o-realtime-preview"`) — the relay now calls the model this task seeds pricing for.
  - `GET /v1/models` (`ModelItem`) and `GET /admin/catalog/models` (`AdminCatalogModelItem`) both
    gain 3 new optional fields (`audio_prompt_usd_per_1m`, `audio_completion_usd_per_1m`,
    `audio_cached_usd_per_1m`) and, for the "gpt-realtime" row specifically, all 6 price fields
    (existing 3 + new 3) are non-null and reflect the real per-1M prices x tenant markup.
  - Every pre-existing model/row is byte-identical: the 3 new `CatalogModel`/`MarkedUpModel`
    fields default to `None` and every pre-existing seed entry (MiniMax, OpenRouter-discovered,
    OPENAI_SEED_MODELS) leaves them unset -> None end-to-end -> the 3 new API fields are `null`
    for every model except gpt-realtime, never `0`.
  - `usage/infrastructure/orm.py`/`usage/application/recorder.py`'s billing math — untouched.
    This task only makes pricing DATA available (catalog + pricing_snapshots); it does not wire
    any billing computation to read the 3 new audio price fields — that is
    `gpt-realtime-relay-billing`'s job (task 3, still at `phase: ground`).
Reject:
<reject>
  - N/A — this is a pure additive catalog-seed + config-default change with no new endpoint, no
    new request/response shape (only new OPTIONAL response fields), and no new validation surface.
    The only failure mode is a regression in existing behavior (byte-identical Must above), covered
    by the full regression suite, not a rejection scenario.
</reject>
After:
<after>
  - A fresh catalog sync produces an active "gpt-realtime" model row with a pricing_snapshots row
    carrying all 6 real prices; every pre-existing model is unaffected.
  - `GET /v1/models`/`GET /admin/catalog/models` list "gpt-realtime" with all 6 real, tenant-marked-up
    prices; every other model's 3 new fields are null.
  - A new realtime relay session calls OpenAI with model="gpt-realtime" (not the old preview id).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Whether "gpt-realtime" supports the SAME session/API surface the relay code already implements
  for "gpt-4o-realtime-preview" (i.e., changing only the model id string is sufficient, with no
  other wire-protocol differences) is UNCONFIRMED by this task — lowest confidence because this
  task's scope is catalog/config only, not relay wire-protocol code (that's
  `gpt-realtime-relay-billing`'s territory, and even that task doesn't change the WebSocket
  session-setup protocol, only usage-parsing). If wrong: the relay would fail to establish/maintain
  realtime sessions after this config change ships, a functional regression bigger than a pricing
  gap. Mitigation: this is a config DEFAULT (overridable via env var), and the full regression
  suite (including `tests/realtime/test_realtime_ws.py`) will be run at VERIFY to catch any
  test-level break; a full live-call smoke test against real OpenAI is OUT OF SCOPE for this
  catalog-seed task (no live OpenAI realtime credential in this test environment) and is flagged
  forward as a Spec delta for `gpt-realtime-relay-billing` or a deploy-time smoke check.
  - [x] Whether OpenAI's "gpt-realtime" pricing numbers are current and correctly sourced —
    confirmed via a direct WebFetch of developers.openai.com/api/docs/models/gpt-realtime
    (2026-07-02), cross-checked against gpt-4o-realtime-preview's distinctly different numbers on
    the same site to rule out a copy-paste mix-up.
  - [x] Which model id to seed under (gpt-realtime vs. gpt-4o-realtime-preview vs. both) — resolved
    by Tin's explicit AskUserQuestion decision this session; not carried forward as open.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: GRPF1 — a catalog sync produces an active gpt-realtime row with all 6 real prices
  Given GPT_REALTIME_SEED_MODELS is wired into CompositeCatalogSource.static_models
  When SyncCatalogUseCase.execute() runs against a fresh catalog
  Then models has an active id="gpt-realtime" row
  And pricing_snapshots has a row for it with prompt/completion/cached_input/audio_prompt/
      audio_completion/audio_cached all populated at the real confirmed prices
  And every pre-existing model's snapshot row is unaffected (byte-identical)

Scenario: GRPF2 — GET /v1/models lists gpt-realtime with all 6 tenant-marked-up prices
  Given a tenant with a 0% markup and a synced catalog including gpt-realtime
  When the tenant calls GET /v1/models
  Then the gpt-realtime ModelItem has non-null audio_prompt_usd_per_1m/audio_completion_usd_per_1m/
      audio_cached_usd_per_1m matching the real prices x 1_000_000
  And every other model's 3 new fields are null, never 0

Scenario: GRPF3 — GET /admin/catalog/models lists gpt-realtime with all 6 tenant-marked-up prices
  Given the same synced catalog as GRPF2
  When the tenant calls GET /admin/catalog/models
  Then the gpt-realtime AdminCatalogModelItem has the same non-null 6-price shape as GRPF2
  And every other model's 3 new fields are null, never 0

Scenario: GRPF4 — markup is applied identically to audio prices as to existing prices
  Given a tenant with a nonzero markup_pct (e.g. 10%)
  When GET /v1/models is called
  Then gpt-realtime's audio_prompt_usd_per_1m/audio_completion_usd_per_1m/audio_cached_usd_per_1m
      each equal the real per-token price x (1 + markup_pct/100) x 1_000_000
  And this is the exact same multiplier already applied to prompt_usd_per_1m/completion_usd_per_1m

Scenario: GRPF5 — a price-only change to the audio fields still appends a new snapshot
  Given gpt-realtime already has one pricing_snapshots row
  When sync_catalog runs again with an unchanged CatalogModel except a different
      audio_completion_usd_per_token
  Then a NEW pricing_snapshots row is appended (never UPDATEd) reflecting the changed audio price
  And the previous snapshot row is untouched (append-only preserved)

Scenario: GRPF6 — the realtime relay now calls "gpt-realtime" by default
  Given Settings is constructed with no explicit realtime_relay_openai_model override
  When a new realtime relay session is established
  Then the outbound OpenAI Realtime connection uses model="gpt-realtime"
  And an explicit env-var override still works unchanged (config default only, not hardcoded)

Scenario: GRPF7 (regression) — the full pre-existing test suite is unaffected
  Given the complete apps/gateway/tests/ suite as it existed before this task
  When the full suite is run after this task's changes land
  Then every test that passed before still passes, with the exact same count (no new failures)
  And no existing test needed modification to accommodate the new fields (pure addition)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new endpoint. Existing endpoints gain 3 new OPTIONAL response fields each.

GET /v1/models   (unchanged path/method)
  200 -> ModelsListResponse.data[].{
    ...existing fields unchanged...,
    audio_prompt_usd_per_1m: float | null,       # NEW
    audio_completion_usd_per_1m: float | null,   # NEW
    audio_cached_usd_per_1m: float | null,       # NEW
  }
  null for every model except "gpt-realtime"; never 0.

GET /admin/catalog/models   (unchanged path/method)
  200 -> AdminCatalogModelsListResponse.data[].{ same 3 new fields, same semantics }

Domain (no wire shape, but part of the frozen contract):
  CatalogModel gains: audio_prompt_usd_per_token, audio_completion_usd_per_token,
    audio_cached_usd_per_token — float | None, default None.
  MarkedUpModel gains: audio_prompt_per_token, audio_completion_per_token,
    audio_cached_per_token — float | None, default None, markup-multiplied.

Catalog seed data (new module, no wire shape):
  gpt_realtime_seed.py:GPT_REALTIME_SEED_MODELS = [CatalogModel(
    id="gpt-realtime", provider="openai", modality="chat", input_modalities="text",
    prompt_usd_per_token=0.000004, completion_usd_per_token=0.000016,
    cached_input_usd_per_token=0.0000004, audio_prompt_usd_per_token=0.000032,
    audio_completion_usd_per_token=0.000064, audio_cached_usd_per_token=0.0000004,
  )]
  Wired into main.py's CompositeCatalogSource(static_models=MINIMAX_SEED_MODELS +
    GPT_REALTIME_SEED_MODELS).

Config default change (no wire shape, but a real runtime-behavior change):
  Settings.realtime_relay_openai_model default: "gpt-4o-realtime-preview" -> "gpt-realtime"
  (env-var override still works unchanged; this only changes what happens with NO override set).

Schema: no new columns (all 6 pricing_snapshots columns already exist from
  gpt-realtime-schema-migration, gate=PASS). This task only WRITES data into them via a new
  catalog-sync insert path — access pattern is INSERT-only (pricing_snapshots stays append-only,
  _price_changed extended to a 6-way comparison so an audio-price-only delta still appends).
```

Status: FROZEN @ v1 — approved by Tin Dang (2026-07-02, via AskUserQuestion, "Approve as-is")
Least-sure flag surfaced at freeze:
⚠ [spec] Whether "gpt-realtime"'s WebSocket session/API surface is otherwise identical to
"gpt-4o-realtime-preview"'s (this task changes only the model-id string, not any wire-protocol
code) is unconfirmed by live testing — mitigated by the full regression suite at VERIFY
(tests/realtime/test_realtime_ws.py included); a live smoke test against real OpenAI is out of
scope for this catalog-seed task and flagged forward as a Spec delta.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: every new field/seed entry exercised; zero regression in the existing suite
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_grpf1_sync_persists_gpt_realtime_with_all_6_prices: sync GPT_REALTIME_SEED_MODELS via a
    FakeCatalogSource-injected static list (mirrors catalog_pricing_fields's FakeCatalogModel
    pattern) / assert a pricing_snapshots row exists with all 6 real prices, and a pre-existing
    OpenRouter-shaped model's snapshot is unaffected
  - test_grpf2_v1_models_lists_gpt_realtime_6_prices: GET /v1/models / assert gpt-realtime's 3 new
    fields are non-null and equal real-price x markup x 1e6; every other model's 3 new fields null
  - test_grpf3_admin_catalog_models_mirrors_grpf2: GET /admin/catalog/models / assert same 6-price
    shape as GRPF2 plus input_modalities present (mirrors CPF2's mirroring pattern)
  - test_grpf4_markup_applied_to_audio_prices: sync + GET /v1/models with a nonzero markup_pct /
    assert audio prices scale by the exact same (1 + markup/100) multiplier as existing prices
  - test_grpf5_audio_price_only_change_appends_snapshot: sync gpt-realtime, resync with only
    audio_completion_usd_per_token changed / assert snapshot count goes 1 -> 2, latest reflects
    the new value (mirrors CPF4's append-only-on-price-change pattern)
  - test_grpf6_realtime_relay_default_model_is_gpt_realtime: construct Settings with no override /
    assert realtime_relay_openai_model == "gpt-realtime"; construct with an explicit env override /
    assert the override wins (config-level test, no live WebSocket needed)
  - GRPF7 (regression) is verified at VERIFY time by running the FULL suite, not a single test —
    matching the gpt-realtime-schema-migration/catalog-pricing-fields precedent for schema/seed-
    touching tasks.
</test_plan>

Tests live in: `./tests/gpt_realtime_pricing_fields/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/catalog/domain/entities.py` `apps/gateway/src/gateway/catalog/infrastructure/repository.py` `apps/gateway/src/gateway/catalog/infrastructure/gpt_realtime_seed.py` `apps/gateway/src/gateway/catalog/api/schemas.py` `apps/gateway/src/gateway/catalog/api/router.py` `apps/gateway/src/gateway/main.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/tests/gpt_realtime_pricing_fields/` `apps/gateway/tests/catalog_sync_trigger/conftest.py` `apps/gateway/tests/catalog/test_model_catalog.py` `apps/gateway/tests/catalog_pricing_fields/test_catalog_pricing_fields.py` `apps/gateway/tests/minimax_catalog_seed/test_minimax_catalog_seed.py`
Strategy (ordered batches):
  1. `catalog/domain/entities.py` — add 3 fields to `CatalogModel` + 3 to `MarkedUpModel`.
  2. `catalog/infrastructure/gpt_realtime_seed.py` (NEW) — `GPT_REALTIME_SEED_MODELS` with the 6
     real confirmed prices.
  3. `catalog/infrastructure/repository.py` — extend `_fetch_latest_prices`/
     `list_active_models_with_markup`/`_insert_snapshot`/`_price_changed` to the 6-field shape.
  4. `catalog/api/schemas.py` — 3 new fields on `ModelItem`/`AdminCatalogModelItem`.
  5. `catalog/api/router.py` — populate the 3 new fields in both list handlers.
  6. `main.py` — wire `GPT_REALTIME_SEED_MODELS` into `CompositeCatalogSource.static_models`.
  7. `core/config.py` — change `realtime_relay_openai_model`'s default to `"gpt-realtime"`.
  8. Run GRPF1-6 to green; run the full regression suite (GRPF7) before VERIFY.
Known-problem fixes: sibling-worktree orphaned `tenant_model_presets` table can block the `app`
  fixture's `drop_all` — confirm `pgrep -fl "worktrees/"` (any sibling) is idle before the full-
  suite run, per the documented project gotcha (not a defect in this task's own code). The
  scope-walker's `.claude/worktrees` exclusion fixed in `gpt-realtime-schema-migration` should
  prevent a repeat of that task's scope_violation false-positive.
Strategy actually used: mostly as planned, plus an unplanned batch 9 discovered by the full
  regression run (GRPF7): `_insert_snapshot`'s unconditional attribute access on the 3 new
  audio_* fields broke 3 sibling suites' independent `FakeCatalogModel` fixtures (duck-typed,
  not the real `CatalogModel` class) — fixed by adding the 3 fields (default=None) to each,
  mirroring their own established mirroring convention. Also fixed
  `minimax_catalog_seed::test_main_wires_composite_catalog_source`'s exact-id-list assertion,
  which no longer held now that `main.py`'s `static_models` includes gpt-realtime too — updated
  the expected list, did not weaken the assertion's rigor.
Safety rule (feature-specific): `pricing_snapshots` stays APPEND-ONLY — `_insert_snapshot` must
  only ever INSERT; `_price_changed`'s extended 6-way comparison must not change its append
  trigger for the pre-existing 3 fields (byte-identical decision for every model without audio
  prices, i.e. every model except gpt-realtime).
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

- [x] all tests pass — targeted suite (55 tests: GRPF1-6 + GSM + 4 sibling regression suites)
      green; full-suite regression (2108 passed) confirmed clean of this task's changes — the
      1 failure + 8 errors seen mid-run were transient Docker/Postgres suspend-resume flakes
      (machine sleep during the run), re-verified 10/10 green in isolation afterward.
- [x] coverage did not decrease — all new code paths exercised by GRPF1-6; no new branch left
      untested.
- [x] no test or contract was altered during build — the 4 sibling-suite edits ADD missing
      default fields / strengthen one assertion; none loosen or delete an existing check.
- [x] the green was EARNED, not gamed — adversarial refute-read (agent, see below) verdict
      EARNED.
- [x] concurrency / timing of the risky operation is safe — pure additive DB columns/appends;
      no new locking/ordering surface (mirrors the already-verified catalog-pricing-fields
      append-only discipline).
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new secret, no
      new external dependency; prices are hardcoded literals cited to a public pricing page.
- [x] layering & dependencies follow CONVENTIONS.md — domain entities stay framework-free;
      infra/api layering unchanged from the existing catalog-pricing-fields precedent.
- [x] a person reviewed and approved the change — Tin Dang, via AskUserQuestion gate approval.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `pricing_snapshots` rows for gpt-realtime carry all 6 real prices (text + audio) — confirmed
      by GRPF1 reading the raw row back via `_latest_prices`.
- [x] GET /v1/models and GET /admin/catalog/models both expose the 3 new `audio_*_usd_per_1m`
      fields, markup-multiplied identically to the existing text fields, null for every other
      model — confirmed by GRPF2/GRPF3/GRPF4 asserting the exact markup ratio.
- [x] pricing_snapshots stays append-only; an audio-price-only change still appends a new
      snapshot row (not an UPDATE) — confirmed by GRPF5 (count 1→2).
- [x] the realtime relay's default OpenAI model is `"gpt-realtime"` (GA), with env override still
      honored — confirmed by GRPF6 (default) and its env-override sibling test.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `GPT_REALTIME_SEED_MODELS` imported and concatenated into
      `CompositeCatalogSource.static_models` in `main.py`; the 3 new `CatalogModel`/`MarkedUpModel`
      fields flow entities.py → repository.py (4 methods) → schemas.py → router.py (both handlers);
      `realtime_relay_openai_model`'s new default is read by the realtime relay module unchanged
      (no relay code touched — value flows through existing `Settings` wiring).
- [x] DEAD-CODE (code) — no new unused symbol; every new field/function param is read by at
      least one of GRPF1-6 or the router/repository call sites.
- [ ] SEMANTIC (prose / non-code) — n/a (no prose/doc artifact in this task's scope).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: agent-ac73c32f601c6441b · adversarially checked: vacuous is-not-None asserts (none found —
  GRPF1 asserts exact Decimal tuple equality), markup applied to wrong field / double-multiplied
  (traced full pipeline, single-pass multiply, correct field mapping), the 1.20 markup being a
  lucky hardcoded coincidence (confirmed against the real tenant markup_pct server_default),
  `_price_changed`'s 6-way comparison always-True/always-False (manually replayed both a true
  delta and a true no-op through the literal comparison logic — correctly bidirectional), the
  4 sibling-suite regression fixes being weakened tests in disguise (confirmed all are minimal
  additive fixes; the one assertion rewrite is strengthened, not loosened), wiring/dead-code.
  Independently re-ran the targeted 55-test suite: green (15.90s).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-07-02

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch: gpt-realtime sync-error rate (upstream has no discovery API, so this seed can only go
stale, never fail loudly); audio_*_usd_per_1m null-rate on /v1/models (should stay ~0% for
gpt-realtime, 100% for every other model — a flip either way signals a mapping bug).

### Decisions (ADR)
- [2026-07-02 · Tin Dang] Switched `realtime_relay_openai_model`'s default from
  `gpt-4o-realtime-preview` to `gpt-realtime` (GA) rather than seed pricing for both ids or
  match the older preview model's prices.
- [2026-07-02 · Tin Dang] Approved CONTRACT as-is (no changes requested).
- [2026-07-02 · Tin Dang] Approved gate PASS.

### Spec delta
- [SPEC · seeded] `gpt-realtime-relay-billing` (task 3/3, still at phase: ground) must parse
  the relay's discarded `response.done` usage object and compute dual-stream cost using the
  6 fields this task added — evidence: this task only seeds catalog prices, it does not wire
  them into any billing/usage-record path yet.
- [SPEC · open] gpt-realtime has no OpenRouter-style discovery API, so its seed entry can only
  go silently stale if OpenAI changes pricing — no monitor currently detects this (evidence:
  same gap already accepted for the MiniMax seed precedent).

### Competency deltas
- [ADD · open] The GROUND phase's initial research missed that the relay's actual default
  model id (`gpt-4o-realtime-preview`) differed from the milestone's assumed pricing target
  (`gpt-realtime`) — caught only by live WebFetch pricing research, not by reading code alone
  (evidence: required a user decision via AskUserQuestion mid-GROUND).
- [ADD · open] A single additive field access in `_insert_snapshot` (`repository.py`) broke 3
  sibling suites' independently-duck-typed `FakeCatalogModel` fixtures plus one exact-id-list
  wiring assertion — none of these were in this task's originally declared scope, only
  surfaced by running the FULL regression suite, not the targeted one (evidence: GRPF7 caught
  what the targeted 7-test run could not). Reinforces: always run the full suite before VERIFY
  on any change touching a shared domain entity, even when the task's own tests are green.
