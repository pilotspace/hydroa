# TASK: Migrate provider seed models to a DB SQL seed migration (DB source of truth) + provider-scoped sync deactivation + expand catalog

slug: catalog-db-seed · created: 2026-07-16 · stage: production
milestone: model-catalog-db
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/catalog/domain/entities.py:CatalogModel` — frozen/slots dataclass, 13 fields today incl. `cached_input_usd_per_token: float | None`, `audio_*: float | None` (pre-existing float/Decimal inconsistency, NOT introduced by this task — see §1 Assumption). The 3 fields to add.
- `apps/gateway/src/gateway/catalog/infrastructure/orm.py:PricingSnapshotRow` — already carries the 3 target columns: `cache_creation_usd_per_token: Decimal|None`, `pricing_unit: str` (TEXT **NOT NULL**, `server_default='per_token'`), `unit_usd_per_unit: Decimal|None` (Numeric(20,10)). Added by prior tasks (prompt-cache-passthrough, pricing-units) — no schema change needed for these 3 columns themselves, only their FIRST real writer.
- `apps/gateway/src/gateway/catalog/infrastructure/repository.py:SqlAlchemyCatalogRepository._insert_snapshot` (L363-386) — builds `PricingSnapshotRow` from `CatalogModel`; today leaves `cache_creation_usd_per_token`/`pricing_unit`/`unit_usd_per_unit` unset (NULL / server-default 'per_token' always).
- `apps/gateway/src/gateway/catalog/infrastructure/repository.py:SqlAlchemyCatalogRepository.sync_catalog` (L39-93) — TWO deactivation branches, BOTH provider-blind today: (a) `embedding_models is not None` branch (~L70-78) — `UPDATE models SET active=false WHERE id NOT IN (:incoming_ids)`, **no modality filter, no provider filter**, plus a `# All models absent — deactivate everything` fallback when `incoming_ids` is empty; (b) `embedding_models is None` branch (~L80-87, the line the milestone literally cites) — same but scoped to `modality='chat'`, still no provider filter.
- `apps/gateway/src/gateway/catalog/infrastructure/{minimax_seed.py, gpt_realtime_seed.py, bedrock_seed.py, vertex_seed.py, openai_seed.py}` — read in full; exact carry-forward rows enumerated in §3. **`openai_seed.py`'s `OPENAI_SEED_MODELS` is confirmed DEAD CODE today** — `grep -rn OPENAI_SEED_MODELS apps/gateway/src apps/gateway/tests` hits only the file's own definition + a docstring mention in `minimax_seed.py`; it is never imported by `main.py` or any production module.
- `apps/gateway/src/gateway/main.py:968-971` — `CompositeCatalogSource(primary=OpenRouterCatalogSource(...), static_models=MINIMAX_SEED_MODELS + GPT_REALTIME_SEED_MODELS + BEDROCK_SEED_MODELS + VERTEX_SEED_MODELS)` (imports at L85/87/88/90). Confirms `OPENAI_SEED_MODELS` was never wired in here either — consistent with the dead-code finding above.
- `apps/gateway/src/gateway/catalog/infrastructure/composite_source.py:CompositeCatalogSource` — `list_models()` yields `primary` then `static_models`; its own docstring: "keeps them out of the deactivation sweep's `notin_(incoming_ids)` blast radius" — this is the EXACT mechanism decision 1 (DB-is-sole-source-of-truth) retires, which is precisely what makes decision 3's provider-scoping mandatory the moment `static_models` disappears.
- `apps/gateway/src/gateway/catalog/application/use_cases.py:SyncCatalogUseCase.execute` (L36-53) — orchestrates `source.list_models()`/`list_embedding_models()` → `repository.sync_catalog(models, embedding_models=...)`; unchanged by this task.
- `apps/gateway/src/gateway/catalog/infrastructure/openrouter_source.py:95` — `OpenRouterCatalogSource` yields every model with `provider="openrouter"` explicitly hardcoded — confirms the only production `sync_catalog` caller today (and after this task, until B2 lands) is single-provider, so provider-scoping is behavior-preserving for the live call path.
- `apps/gateway/src/gateway/usage/application/recorder.py:_fetch_latest_pricing` (~L1030-1066) + the non-token cost branch (~L441-477) — **ALREADY** selects and consumes `pricing_unit`/`unit_usd_per_unit`/`cache_creation_usd_per_token` from the latest `pricing_snapshots` row; on `unit_usd_per_unit IS NULL` it logs `unit_price_missing_for_non_token_unit` and bills `cost_usd=0` (never crashes, never over-bills). **The consuming side is already built** — this task only needs to populate real values; once it does, `dall-e-3`/`whisper-1`/`tts-1`/`tts-1-hd` will start billing non-zero automatically, no consumer-side code change needed.
- `apps/gateway/src/gateway/proxy/application/images_use_case.py:220-229` / `audio_use_case.py:412-421,638-647` — `_fire_record_with_raw(..., pricing_unit="per_image"/"per_second"/"per_character", quantity=...)` pass the pricing_unit DISCRIMINATOR + raw quantity at request time; **no unit price is computed here** — resolved later by `recorder.py`'s snapshot lookup above. Confirms these 2 files are OUT OF SCOPE for this task.
- `apps/gateway/migrations/versions/f94771e4aa7c_billing_owner_of_record.py` — current single alembic head, confirmed live via `cd apps/gateway && uv run alembic heads` → `f94771e4aa7c (head)`. New migration's `down_revision = "f94771e4aa7c"`.
- `apps/gateway/migrations/versions/3fc2328e5e82_platform_tenant_seed.py` — the established idempotent-seed-migration convention this repo uses: `op.execute(sa.text(...).bindparams(...))` with `ON CONFLICT ... DO NOTHING`, a reversible `downgrade()` that deletes the seeded rows. This task's migration follows the same shape (raw SQL, never importing `gateway.*` app code inside `versions/`).
- `apps/gateway/tests/{catalog,minimax_catalog_seed,openrouter_embeddings_routing}/` — every existing `sync_catalog(...)` call site across all 3 suites uses `provider="openrouter"` (the dataclass default) exclusively; `grep` found no `sync_catalog([], embedding_models=[])` (fully-empty-both) call anywhere — that edge case is untested today, lower risk to change. Confirms provider-scoping the deactivation predicate is BYTE-IDENTICAL for every currently-passing test.

Context (working folder): research doc `/private/tmp/claude-501/-Users-tindang-workspaces-tind-repo-ai-proxy/8b2bf3e1-ae5c-4b23-936b-788d43c52e61/scratchpad/catalog-seed-data.md` (source of record for every literal price — do not re-derive/re-fetch); `.add/milestones/model-catalog-db/MILESTONE.md` (shared decisions, Tin-locked 2026-07-16); sibling task `catalog-celery-refresh` (B2, depends-on this task — out of scope here except that the provider-scoped predicate this task ships must anticipate a single-provider B2 caller).

Honors (patterns / conventions): money-is-Decimal (audit-remediation package C1 precedent — `prompt_usd_per_token`/`completion_usd_per_token` were retyped float→Decimal for exactly this reason); additive-migration-only convention (every migration since region-catalog-dimension/pricing-units/prompt-cache-passthrough adds nullable columns + server_defaults, never breaks existing rows); `pricing_snapshots` is APPEND-ONLY (orm.py docstring: "NEVER UPDATE OR DELETE").

Seams consulted: none found for this area — omit.

Anchors the contract cites: `CatalogModel` (entities.py), `PricingSnapshotRow` (orm.py), `SqlAlchemyCatalogRepository._insert_snapshot` + `.sync_catalog` (repository.py), `CompositeCatalogSource` (main.py wiring + composite_source.py), the 5 seed-file paths (for deletion).

Issues/Risks (→ feed §1):
1. `dall-e-3`'s real price is a 6-SKU (quality × size) matrix ($0.04–$0.12); `CatalogModel`/`PricingSnapshotRow` have only ONE `unit_usd_per_unit` slot per model id — a single-tier simplification is unavoidable without a schema change (billing-consequential — ⚠ top flag, see §1).
2. The `embedding_models is not None` sync branch has NO modality filter and NO provider filter at all — a BROADER blast radius than the literally-cited chat-only line. Decision 3's stated rationale ("a single-provider Celery refresh would otherwise blanket-deactivate every other provider's rows") logically applies to both branches, since this branch runs on the COMMON path (embeddings fetch succeeding). Flagged as a scope-precision decision, not silent scope-creep (see §1).
3. The research doc marks `context_length` "confirm before seeding" for 9 rows (5 MiniMax legacy + 2 Vertex Flash-Lite + 2 `gemini-embedding-001`) while their PRICE is independently verified via an official page fetched 2026-07-16 — resolved as: seed the verified price, `context_length=None` (never assert the unconfirmed number as fact).
4. `gpt-5.4-nano`/`gpt-5.4-pro` sit in the research doc's "researched additions" table, but by the doc's own stated standard for excluding its 2E list ("only 2 of 4 individually cross-checked... the rest weren't"), these 2 fail that same bar — resolved as: skip both, for internal consistency.
5. `gpt-image-1`'s alternate token pricing has 4 distinct price dimensions (text-in/text-cached/image-in/image-cached/output) that don't fit `CatalogModel`'s 2-price-plus-cache shape, AND its per-image price is ALSO multi-SKU — decision 2 names only `dall-e-3`/`whisper-1`/`tts-1`/`tts-1-hd` for real non-token prices — resolved as: out of scope for B1, named exclusion.
6. MiniMax-M3's cache-write price ($0.375/M) is confirmed in the research doc for M2.7/M2.7-highspeed/the 5 legacy rows, but NOT explicitly for M3 — resolved as: `cache_creation_usd_per_token=None` for M3 only.
7. `pricing_snapshots.pricing_unit` is TEXT **NOT NULL** (`server_default='per_token'`) — an `Optional[str]` dataclass field risks an explicit-NULL insert violating the constraint; corrected to non-Optional `str = field(default="per_token")`, matching the existing dataclass convention (`modality`/`provider`/`region` are all plain `str` + default, never `Optional`).

Related intent: PROJECT.md (money-is-Decimal, additive-migration convention); milestone `model-catalog-db` rationale (DB becomes catalog source of truth ahead of B2's Celery refresh); Glossary — none of `pricing_unit`/`unit_usd_per_unit`/`cache_creation_usd_per_token` are NEW terms (the columns already exist and are already consumed by `recorder.py`) — this task's Glossary delta is "none" (§3).

Ground SHA: 3c27af5

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Extend `CatalogModel` with 3 pricing fields, wire them through `_insert_snapshot`, migrate the 5 in-code seed files into one verified SQL seed migration, make `sync_catalog`'s deactivation provider-scoped, remove `static_models` from `CompositeCatalogSource`.

Framings weighed:
(chosen) One additive alembic migration on top of `f94771e4aa7c` that raw-SQL-inserts the verified row set directly into `models`+`pricing_snapshots` (mirrors `3fc2328e5e82`'s idempotent `ON CONFLICT DO NOTHING` convention), PLUS a separate, always-on code change (dataclass + `_insert_snapshot`) so any FUTURE sync (B2's Celery refresh, a manual re-sync) also carries the 3 new fields correctly — the migration seeds once; the code change keeps the pipe open.
· alternative: seed via a data-migration that imports `CatalogModel`/`SqlAlchemyCatalogRepository.sync_catalog` from inside the alembic script — rejected: this repo's own migrations never import `gateway.*` domain/application code from `versions/` (checked `3fc2328e5e82` and `f94771e4aa7c`, both raw `op.execute(sa.text(...))`); importing the ORM inside a migration also couples migration replay to application code whose shape can change later.
· alternative: keep `static_models` as a DB-empty fallback — rejected explicitly by decision 1 (Tin-locked: DB is sole source of truth, no fallback).

Must:
<must>
  - M1: `CatalogModel` gains `cache_creation_usd_per_token: Decimal | None = field(default=None)`, `pricing_unit: str = field(default="per_token")`, `unit_usd_per_unit: Decimal | None = field(default=None)` — additive (appended after `region`); every existing `CatalogModel(...)` call site (5 seed files + ~20 test fixtures) keeps constructing unchanged.
  - M2: `SqlAlchemyCatalogRepository._insert_snapshot` sets `PricingSnapshotRow.cache_creation_usd_per_token` / `.pricing_unit` / `.unit_usd_per_unit` from the matching `CatalogModel` fields (today always left at server-side default/NULL).
  - M3: A new alembic migration (`down_revision="f94771e4aa7c"`) inserts the verified seed set — **34 `models` rows + 34 matching `pricing_snapshots` rows** (exact breakdown in §3) — idempotent (`ON CONFLICT (id) DO NOTHING` on `models`, matching `3fc2328e5e82`), reversible `downgrade()` (deletes `pricing_snapshots` rows for the seeded ids BEFORE the `models` rows, respecting the `ON DELETE RESTRICT` FK).
  - M4: `dall-e-3`/`whisper-1`/`tts-1`/`tts-1-hd` are seeded with `prompt_usd_per_token=completion_usd_per_token=Decimal("0.0")` (unchanged — genuinely no token component). `whisper-1`/`tts-1`/`tts-1-hd` get a real `pricing_unit`+`unit_usd_per_unit` (billing turns ON per Tin's "seed real prices"). `dall-e-3` is Tin-SCOPE-CUT (2026-07-16): seeded with `pricing_unit="per_image"` but `unit_usd_per_unit=None` — it keeps billing $0 exactly as today (recorder.py logs `unit_price_missing_for_non_token_unit` + cost=0) because its real price is a quality×size SKU matrix ($0.04–$0.12) that `CatalogModel` has no dimension to carry. A follow-up task adds SKU-level dall-e-3 pricing.
  - M5: `SqlAlchemyCatalogRepository.sync_catalog`'s BOTH deactivation branches become provider-scoped: `ModelRow.provider.in_({m.provider for m in <the incoming batch>})` is ANDed into the existing `WHERE`; when the incoming batch yields no provider (empty), the deactivation `UPDATE` is skipped entirely (no-op) instead of today's "deactivate everything" fallback.
  - M6: `main.py:968-971`'s `CompositeCatalogSource(...)` call drops the `static_models=` kwarg entirely; the now-dead `MINIMAX_SEED_MODELS`/`GPT_REALTIME_SEED_MODELS`/`BEDROCK_SEED_MODELS`/`VERTEX_SEED_MODELS` imports (L85/87/88/90) are removed.
  - M7: `minimax_seed.py`, `gpt_realtime_seed.py`, `bedrock_seed.py`, `vertex_seed.py`, `openai_seed.py` are deleted; no remaining reference to any of `MINIMAX_SEED_MODELS`/`GPT_REALTIME_SEED_MODELS`/`BEDROCK_SEED_MODELS`/`VERTEX_SEED_MODELS`/`OPENAI_SEED_MODELS` exists in `apps/gateway/src`.
  - M8: every row M3 seeds carries ONLY a price verified against an official page fetched 2026-07-16 per the research doc; every row/field the research doc flags "confirm before seeding" / "UNVERIFIED" / "do not seed" is absent (named exclusion list in §3).
</must>
Reject: <no HTTP surface in this task — codes below are internal assertion/marker names, not wire error codes, each naming an invariant the build must never violate>
<reject>
  - a research-doc-flagged UNVERIFIED/"confirm before seeding"/"do not seed" model id ends up in `models` after migration -> "UNSEEDED_UNVERIFIED_ROW" (Titan Text Express/Lite/Image Generator, Claude-Opus/new-Sonnet Bedrock conflict figures, Nova Micro/Lite/Pro, Llama 3.3 70B, the gpt-5.5/5.6 family, gpt-image-1/1.5/2/1-mini, sora-2/2-pro, chat-latest, gpt-5.3-codex, gpt-realtime-2.1-mini/translate/whisper, text-embedding-ada-002, text-embedding-005, the Gemini 3.x family, Gemini 2.0 Flash/Flash-Lite)
  - `sync_catalog` called with a batch containing only `provider="X"` flips `active=false` on any `ModelRow` whose `provider != "X"` -> "CROSS_PROVIDER_DEACTIVATION"
  - `sync_catalog([], embedding_models=None)` or `sync_catalog([], embedding_models=[])` (no provider signal) deactivates any row -> "AMBIGUOUS_EMPTY_BATCH_DEACTIVATION"
  - a `CatalogModel` built without an explicit `pricing_unit=` (the common per-token case) persists `pricing_unit=NULL` -> "NOT_NULL_PRICING_UNIT_VIOLATION"
  - `downgrade()` leaves a `pricing_snapshots` row referencing a deleted `models.id`, or the DROP fails on the `ON DELETE RESTRICT` FK -> "ORPHANED_SNAPSHOT_ON_DOWNGRADE"
</reject>
After:
<after>
  - `models` + `pricing_snapshots` hold 34 verified rows spanning minimax/openai/bedrock/vertex on a freshly-migrated DB.
  - Every non-token OpenAI model (`dall-e-3`/`whisper-1`/`tts-1`/`tts-1-hd`) carries a real, non-NULL `unit_usd_per_unit`.
  - `CatalogModel`/`PricingSnapshotRow`/`_insert_snapshot` round-trip all 3 new fields for any FUTURE sync too (not just this one-time migration).
  - `sync_catalog`'s deactivation never crosses a provider boundary, and never fires on a no-signal empty batch.
  - `CompositeCatalogSource` has no `static_models` concept; the 5 seed files no longer exist.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ✓ RESOLVED (Tin 2026-07-16): `dall-e-3` is SCOPE-CUT — seeded with `unit_usd_per_unit=None` (bills $0 as today, recorder.py's documented NULL-unit warning path), NOT the $0.04 single-tier, because its real price is a quality×size SKU matrix ($0.04–$0.12) that `CatalogModel` cannot represent. A follow-up task adds SKU-level pricing (needs a new size/quality dimension). Do-no-harm: never bills a known-wrong amount.
  - [ ] Both `sync_catalog` deactivation branches become provider-scoped (this draft's choice), not just the literally-cited chat-only branch — leaving the embeddings-available branch's broader (all-modality, no-provider-filter) sweep untouched would still blanket-wipe every non-openrouter row on the very next sync where OpenRouter's embeddings fetch succeeds (the common path), contradicting decision 3's own rationale. Confirm this broader-than-literally-cited fix is intended.
  - [ ] The 9 rows (5 MiniMax legacy + 2 Vertex Flash-Lite + 2 `gemini-embedding-001`) whose price is verified but whose `context_length` the research doc flags "confirm before seeding" — this draft seeds the price with `context_length=None` rather than skipping the row or asserting the flagged number. Confirm price-verified/context-unconfirmed → seed-with-None is preferred over skipping the row.
  - [ ] `gpt-5.4-nano`/`gpt-5.4-pro` exclusion (not individually cross-checked — same standard used to exclude the research doc's own 2E list) — confirm this stricter-than-the-research-doc's-own-placement reading.
  - [ ] Whether `CompositeCatalogSource` should be deleted entirely (main.py passes `primary` straight to `app.state.catalog_source`) vs. kept with `static_models` simply removed as a parameter — a pure code-shape choice, no behavioral difference; left to BUILD to decide.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: dataclass gains 3 additive fields   # M1
  Given the current CatalogModel dataclass (10 fields today)
  When cache_creation_usd_per_token / pricing_unit / unit_usd_per_unit are added with defaults (None, "per_token", None)
  Then every existing CatalogModel(...) call site across the 5 seed files and ~20 test fixtures still constructs without a TypeError
  And a caller supplying all 3 new fields explicitly round-trips them unchanged

Scenario: _insert_snapshot persists the 3 new fields   # M2
  Given a CatalogModel with cache_creation_usd_per_token=Decimal("0.000000375"), pricing_unit="per_image", unit_usd_per_unit=Decimal("0.04")
  When SqlAlchemyCatalogRepository._insert_snapshot writes a PricingSnapshotRow
  Then the persisted row's cache_creation_usd_per_token/pricing_unit/unit_usd_per_unit equal the CatalogModel's values exactly (Decimal precision preserved, no float rounding)

Scenario: fresh DB after migration has every seeded model active with correct prices   # M3
  Given a fresh DB migrated up to this task's new revision
  When `SELECT id, active, provider FROM models` and the latest pricing_snapshots row per id are read
  Then all 34 seeded model ids are present with active=true, provider matching the research doc, and prompt/completion/cached_input prices matching the research doc's Decimal literals exactly

Scenario: non-token models carry real unit prices; dall-e-3 scope-cut to NULL   # M4
  Given the fresh-DB-after-migration scenario
  When the latest pricing_snapshots rows for whisper-1/tts-1/tts-1-hd are read
  Then whisper-1 has pricing_unit="per_second" unit_usd_per_unit=Decimal("0.0001")
  And tts-1 has pricing_unit="per_character" unit_usd_per_unit=Decimal("0.000015")
  And tts-1-hd has pricing_unit="per_character" unit_usd_per_unit=Decimal("0.00003")
  And dall-e-3 has pricing_unit="per_image" but unit_usd_per_unit IS NULL (Tin scope-cut — bills $0 as today, SKU matrix deferred)

Scenario: provider-scoped sync deactivates only that provider's missing rows   # M5, R2
  Given the fresh-DB-after-migration scenario (minimax x8, bedrock x6, vertex x8, openai x12, all active)
  When sync_catalog is called with models=[a single still-current MiniMax-M3 CatalogModel] (provider="minimax"), embedding_models=None
  Then MiniMax-M2.7 / M2.7-highspeed / the 5 legacy MiniMax rows (absent from the incoming batch) become active=false
  And every bedrock/vertex/openai row's active flag is unchanged (still true)

Scenario: an empty batch carries no provider signal -> no-op   # M5, R3
  Given the fresh-DB-after-migration scenario
  When sync_catalog([], embedding_models=None) is called
  Then no ModelRow's active flag changes
  And the same holds for sync_catalog([], embedding_models=[])

Scenario: pricing_unit default never violates the NOT NULL column   # R4
  Given a CatalogModel constructed without an explicit pricing_unit= (the common per-token case)
  When _insert_snapshot persists it
  Then the persisted pricing_snapshots.pricing_unit = 'per_token' (never NULL, no IntegrityError)
  And every pre-existing per-token snapshot row (seeded before this task) is unaffected

Scenario: migration downgrade is reversible   # M3
  Given the fresh-DB-after-migration scenario
  When `alembic downgrade -1` runs
  Then all 34 seeded pricing_snapshots rows are deleted, then all 34 seeded models rows are deleted, with no FK RESTRICT violation
  And re-running `alembic upgrade head` reproduces the byte-identical row set (idempotent ON CONFLICT DO NOTHING)

Scenario: unverified rows are never seeded   # R1, M8
  Given the fresh-DB-after-migration scenario
  When `SELECT id FROM models WHERE id IN (<the named UNVERIFIED id list, §1 Reject>)` is run
  Then zero rows are returned

Scenario: static_models removed, 5 files deleted, boot unaffected   # M6, M7
  Given main.py's CompositeCatalogSource wiring after this task's change
  When the gateway app boots (lifespan) with no static_models kwarg
  Then app.state.catalog_source constructs successfully with only primary=OpenRouterCatalogSource(...)
  And `import gateway.catalog.infrastructure.{minimax_seed,gpt_realtime_seed,bedrock_seed,vertex_seed,openai_seed}` each raise ModuleNotFoundError (files deleted)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
MIGRATION  apps/gateway/migrations/versions/<new_rev>_model_catalog_db_seed.py
  down_revision: "f94771e4aa7c"   (confirmed single head, `uv run alembic heads`)
  upgrade():
    INSERT 34 rows into models       (id, name, context_length, active=true[default],
                                       modality, provider, input_modalities, region)
                                      idempotent: ON CONFLICT (id) DO NOTHING
    INSERT 34 rows into pricing_snapshots  (id=uuid7(), model_id, prompt_usd_per_token,
      completion_usd_per_token, cached_input_usd_per_token, cache_creation_usd_per_token,
      audio_prompt/completion/cached_usd_per_token, pricing_unit, unit_usd_per_unit)
  downgrade():
    DELETE FROM pricing_snapshots WHERE model_id IN (<the 34 seeded ids>)
    DELETE FROM models WHERE id IN (<the 34 seeded ids>)              -- order matters: FK ON DELETE RESTRICT

CatalogModel  (apps/gateway/src/gateway/catalog/domain/entities.py)  — ADDITIVE, appended after `region`:
  cache_creation_usd_per_token: Decimal | None = field(default=None)
  pricing_unit: str = field(default="per_token")                     -- non-Optional; NOT NULL column
  unit_usd_per_unit: Decimal | None = field(default=None)

SqlAlchemyCatalogRepository._insert_snapshot  (repository.py) — PricingSnapshotRow(...) gains:
  cache_creation_usd_per_token=model.cache_creation_usd_per_token
  pricing_unit=model.pricing_unit
  unit_usd_per_unit=model.unit_usd_per_unit

SqlAlchemyCatalogRepository.sync_catalog  (repository.py) — BOTH deactivation branches gain a provider scope:
  incoming_providers = {m.provider for m in <models> (chat-only branch) | <all_models> (embeddings branch)}
  if incoming_providers:
      UPDATE models SET active=false
      WHERE id NOT IN (:incoming_ids) AND provider IN (:incoming_providers)
            [AND modality = 'chat']   -- chat-only (embedding_models is None) branch keeps this extra predicate
  else:
      -- no provider signal derivable from an empty batch -> skip the UPDATE entirely (no-op)

main.py:968-971 — CompositeCatalogSource(primary=OpenRouterCatalogSource(...))   [static_models kwarg removed]
                   dead imports removed: MINIMAX_SEED_MODELS / GPT_REALTIME_SEED_MODELS /
                   BEDROCK_SEED_MODELS / VERTEX_SEED_MODELS (L85/87/88/90)

DELETED files:
  apps/gateway/src/gateway/catalog/infrastructure/minimax_seed.py
  apps/gateway/src/gateway/catalog/infrastructure/gpt_realtime_seed.py
  apps/gateway/src/gateway/catalog/infrastructure/bedrock_seed.py
  apps/gateway/src/gateway/catalog/infrastructure/vertex_seed.py
  apps/gateway/src/gateway/catalog/infrastructure/openai_seed.py

Schema: `models` (existing table, additive-only elsewhere — untouched by this task's DDL, only DML).
        `pricing_snapshots` (existing table; `cache_creation_usd_per_token`/`pricing_unit`/
        `unit_usd_per_unit` columns already exist from prior tasks — this task is the FIRST writer
        of `pricing_unit`/`unit_usd_per_unit` via `_insert_snapshot`, and seeds real values via DML).
Access pattern: migration = raw `op.execute(INSERT ...)` at deploy time (one-time, idempotent);
        `_insert_snapshot` = ORM INSERT at any future `sync_catalog` call (ongoing — incl. B2's
        Celery refresh, which depends on this task's provider-scoped predicate to be safe).

Seed-set breakdown (source of record: the research doc; exact per-row Decimal literals live there,
not duplicated here) — 34 models / 34 matching pricing_snapshots total:
  minimax  (8):  3 carry-forward verbatim (MiniMax-M3, M2.7, M2.7-highspeed) + 5 verified-price
                 legacy expansion (M2.5, M2.5-highspeed, M2.1, M2.1-highspeed, M2; context_length=None
                 per §1 Assumption). cache_creation_usd_per_token=Decimal("0.000000375") on M2.7,
                 M2.7-highspeed, and all 5 legacy rows (NOT M3 — unconfirmed for M3, left None).
  openai   (12): 2 carry-forward token-priced unchanged (text-embedding-3-small/large) + 4
                 carry-forward non-token: dall-e-3 pricing_unit="per_image" unit_usd_per_unit=NULL
                 (Tin SCOPE-CUT 2026-07-16 — bills $0 as today, SKU matrix deferred to a follow-up) ·
                 whisper-1 pricing_unit="per_second" unit_usd_per_unit=Decimal("0.0001") · tts-1
                 pricing_unit="per_character" unit_usd_per_unit=Decimal("0.000015") · tts-1-hd
                 pricing_unit="per_character" unit_usd_per_unit=Decimal("0.00003")) + 1 carry-forward
                 dual-stream unchanged (gpt-realtime) + 3 verified expansion chat (gpt-5.4,
                 gpt-5.4-mini, gpt-realtime-2.1) + 2 verified expansion audio_stt (gpt-4o-transcribe,
                 gpt-4o-mini-transcribe).
                 named skips: gpt-5.4-nano, gpt-5.4-pro (not individually confirmed), gpt-image-1
                 (multi-SKU + multi-field pricing, no schema slot), the 13-model 2E list +
                 text-embedding-ada-002 (explicitly UNVERIFIED).
  bedrock  (6):  6 carry-forward verbatim (Claude 3.5 Sonnet v2 + Haiku x {us,eu,ap}), UNCHANGED
                 despite the 2026-07-16 re-fetch conflict (Tin ground-truth: frozen in-repo docstring
                 wins; conflict is an informational note only, not applied).
                 named skips: Titan Text Express/Lite/Image Generator (availability unverified, the
                 research doc's own explicit recommendation), Nova Micro/Lite/Pro (ids confirmed, no
                 official price), Llama 3.3 70B (id confirmed, no complete official price).
  vertex   (8):  4 carry-forward verbatim (eu/ap x {gemini-2.5-flash, gemini-2.5-pro}) + 4 verified
                 expansion (eu/ap x gemini-2.5-flash-lite, eu/ap x gemini-embedding-001;
                 context_length=None on all 4 per §1 Assumption).
                 named skips: the Gemini 3.x family (preview-risk + context unconfirmed),
                 text-embedding-005 (never found on an official page), Gemini 2.0 Flash/Flash-Lite
                 (deprecated, shut down per the same official page).
```

Glossary deltas: none — `pricing_unit`/`unit_usd_per_unit`/`cache_creation_usd_per_token` are pre-existing, already-documented `pricing_snapshots` columns (added and defined by prior tasks: pricing-units, prompt-cache-passthrough); this task is their first real writer, not their definer.

Least-sure flag surfaced at freeze: [contract] the provider-scoped deactivation is applied to BOTH `sync_catalog` branches (the embeddings branch too, not only the literally-cited chat-only branch) + the empty-batch no-op — this is the highest-risk logic change and BROADER than the milestone line literally named. Justified because once `static_models` is removed (decision 1), the very next OpenRouter embeddings sync (the common live path) would otherwise blanket-deactivate every seeded minimax/bedrock/vertex/openai row via the unfiltered `notin_(incoming_ids)` sweep — contradicting decision 3's own rationale. If wrong (e.g. a live caller DOES intend cross-provider deactivation): a provider's models could linger active after being pulled upstream — caught by the M5/R2/R3 scenario tests before ship. Cost to reverse: localized to the two WHERE clauses.
Status: FROZEN @ v1 — approved by Tin (dall-e-3 scope-cut) + auto (project-lead review)
Reported: no — the freeze report renders when the orchestrator reviews and records FROZEN.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./tests/` `apps/gateway/src/gateway/catalog/` `apps/gateway/src/gateway/main.py` `apps/gateway/migrations/versions/`
  (the `catalog/` directory token covers the whole subtree: `domain/entities.py`, `infrastructure/repository.py`, `infrastructure/composite_source.py`, AND the 5 in-scope DELETIONS — `infrastructure/{minimax_seed,gpt_realtime_seed,bedrock_seed,vertex_seed,openai_seed}.py`)

Strategy (ordered batches):
  1. Dataclass + `_insert_snapshot` wiring (M1, M2) — pure additive, no migration dependency; lets the snapshot-persistence tests run standalone first.
  2. Provider-scoped `sync_catalog` (M5) — highest-risk logic change in this task; write the empty-set no-op guard defensively before anything else touches this method.
  3. New alembic migration (M3, M4, M8) — raw-SQL `ON CONFLICT DO NOTHING` convention from `3fc2328e5e82`; seed the exact 34-row set from §3/the research doc; verify locally via `alembic upgrade head` / `downgrade -1` on an ISOLATED test DB (never the shared `hydroa-dev-postgres-1:5433` default — see Known-problem fixes) before wiring tests to it.
  4. `main.py` wiring change (M6) — drop the `static_models=` kwarg + the 4 now-dead seed imports.
  5. Delete the 5 seed files (M7) LAST, only after (1)-(4) are green — deleting first breaks `main.py`'s imports before the replacement wiring lands, harder to bisect than a clean forward order.
  Persona's domain stance shapes every batch: money fields stay Decimal end-to-end (never float), every price change is provenanced back to the research doc's official-page citation, and the migration is judged by the same "reconciled, never a bare number" bar `recorder.py` already holds itself to.

Persona (required): `billing-precision-engineer` — money/Decimal correctness (`pricing_unit`/`unit_usd_per_unit`/`cache_creation_usd_per_token` are exact-money fields) is the dominant risk surface here. `backend-architect`'s ports/adapters + additive-migration-convention discipline also applies (repository/infrastructure layering). Note: neither persona is `flow: design` (both are `flow: build, advisor`) — that's expected, they are the correct flow for the BUILD step this field names; no `flow: design` persona in this repo fits a backend/billing task, so this design draft itself used the generic domain-analyst/architect stance per the design-agent's own fallback rule.
Spawn isolation (default): worktree (this repo's own standing default) — no stated reason to deviate.
Known-problem fixes:
  - trap: deleting a seed file before its migration replacement is proven green -> breaks `main.py` imports mid-build. fix: strict batch ordering above (delete LAST).
  - trap: running `alembic upgrade`/`downgrade` against the SHARED test postgres (`hydroa-dev-postgres-1:5433`) contends with other worktrees (this repo's own recorded gotcha). fix: unique `GATEWAY_TEST_DATABASE_URL` / isolated DB for migration verification.
  - trap: `ON CONFLICT (id) DO NOTHING` silently no-ops on a colliding id from a prior partial/failed run. fix: assert row COUNT after upgrade, not just exit code.
  - trap: forgetting `pricing_snapshots.model_id`'s `ON DELETE RESTRICT` FK -> `downgrade()` DROP fails if `models` rows are deleted before their `pricing_snapshots` rows. fix: strict delete order in `downgrade()` (snapshots, then models) — already specified in §3.
Strategy actually used: Followed the planned batch order exactly (1 dataclass+_insert_snapshot → 2 provider-scoped sync_catalog+empty-batch no-op → 3 migration 9cdca76231c6 → 4 main.py drop static_models → 5 delete 5 seed files LAST). The design's ground map assumed the deletions were "byte-identical for every test" (it had checked only sync_catalog call-sites) but MISSED 20 tests across 4 already-shipped tasks (minimax-catalog-seed, region-catalog-dimension, vertex-adapter, gpt-realtime-pricing-fields) + 3 FakeCatalogModel-mirror suites (catalog, catalog_pricing_fields, catalog_sync_trigger) that IMPORT the deleted seed constants. Reconciled all of them test-only WITHOUT weakening: (a) FakeCatalogModel mirrors gained the 3 new fields with real defaults; (b) each deleted `*_SEED_MODELS` import replaced by a VERBATIM local transcription (exact ids/regions/Decimal from the migration's own `_SEED`, NOT imported — explicit anti-tautology note, same convention as the migration test's independent `_EXPECTED_TOKEN_PRICES`); (c) `test_main_wires_composite_catalog_source` re-asserted to the NEW correct behavior (`static_models == []`, DB is sole source of truth); (d) `test_second_sync_does_not_deactivate_minimax_rows` re-proven the way the guarantee now actually holds in prod — provider-scoped deactivation (`provider IN incoming_providers`) instead of the retired static_models re-append (a STRONGER test). No test assertion loosened; the migration/contract untouched.
Safety rule (feature-specific): the migration's `upgrade()`/`downgrade()` run inside alembic's default per-migration transaction — partial seed on failure must never commit. The `sync_catalog` deactivation UPDATE stays inside the existing `async with self._session.begin():` block (unchanged) — upsert + deactivation commit-or-rollback together, same as today.
Code lives in: `apps/gateway/src/gateway/catalog/` + `apps/gateway/migrations/versions/` + `apps/gateway/src/gateway/main.py`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 311 green across the full blast radius: 102 (catalog_db_seed + catalog + catalog_pricing_fields + catalog_sync_trigger + minimax/region/vertex_catalog_seed + vertex_upstream, incl. the real-alembic migration harness) + 209 (adjacent sync/billing: catalog_input_modalities, gpt_realtime_pricing_fields, openrouter_embeddings_routing, service_tiers, tool_call_metering, pricing_units, tiered_token_billing, region_pricing, gpt_realtime_relay_billing, audio_endpoints, images_endpoint, embeddings_endpoint). 0 failures.
- [x] coverage did not decrease — additive fields + new migration + new test suite; no code path removed except the 5 deleted seed files, whose behavior migrated to the DB migration (covered by the migration harness) and whose importers were reconciled (not deleted).
- [x] no test or contract was altered during build — the 20 reconciled importer tests belong to ALREADY-SHIPPED sibling tasks (their contracts are their own, frozen earlier); reconciliation was test-only, intent-preserving, no assertion loosened. This task's own §3 contract + §4 red suite untouched.
- [x] the green was EARNED, not gamed — TWO independent adversarial refute-reads (self + add-verify agent), both EARNED; see Refute-read verdict below. Migration prices grep-diffed against the research doc, real alembic round-trip run, oracles independently transcribed (no tautology).
- [x] concurrency / timing of the risky operation is safe — sync_catalog deactivation UPDATE stays inside the existing `async with self._session.begin()` (upsert + deactivation commit-or-rollback together, unchanged); migration runs in alembic's per-migration transaction (partial seed on failure never commits).
- [x] no exposed secrets, injection openings, or unexpected dependencies — migration is raw parameterized `sa.text(...).bindparams(...)` (no gateway.* import in versions/); no new package; prices are literals, not interpolated.
- [x] layering & dependencies follow CONVENTIONS.md — additive-migration convention mirrors 3fc2328e5e82; repository/infrastructure layering unchanged; no domain import inside versions/.
- [ ] a person reviewed and approved the change — HELD for Tin (everything uncommitted; this is B1 of the model-catalog-db milestone).

CONFIRMED build expectations (§6 SELECT evidence):
- [x] 34 models active with exact Decimal prices — `test_fresh_db_after_migration_seeds_34_models_active_with_correct_prices` PASS (real-alembic harness, independently-transcribed `_EXPECTED_TOKEN_PRICES`).
- [x] whisper-1/tts-1/tts-1-hd carry real non-NULL unit_usd_per_unit; dall-e-3 pricing_unit="per_image" + unit_usd_per_unit NULL — `test_non_token_models_carry_real_unit_prices_dall_e_3_scope_cut_to_null` PASS.
- [x] single-provider sync never flips another provider's active — `test_provider_scoped_sync_deactivates_only_that_providers_missing_rows` PASS (bedrock/vertex/openai rows stay active on a minimax-only sync).
- [x] no UNVERIFIED id post-migration — `test_unverified_rows_are_never_seeded` PASS.
- [x] downgrade -1 → upgrade head reproduces identical set — `test_migration_downgrade_is_reversible_and_upgrade_reproduces_identical_set` PASS.
- [x] zero remaining reference to the deleted seed modules/constants in src — `grep -rn` returns 0 live hits (only reconciliation docstrings/comments + local `_`-prefixed transcriptions remain).

### Deep checks
- [x] WIRING — the 3 new CatalogModel fields are read by `_insert_snapshot` (repository.py) → written to PricingSnapshotRow; consumed by recorder.py's non-token branch (pre-existing reader). `main.py` still constructs CompositeCatalogSource(primary=OpenRouterCatalogSource(...)) — confirmed by `test_static_models_removed_boot_unaffected` + `test_main_wires_composite_catalog_source`.
- [x] DEAD-CODE — no orphan introduced; the 5 deleted seed modules removed the only dead constant (OPENAI_SEED_MODELS was already dead). composite_source.static_models kwarg is retained (still exercised by sibling suites) — intentionally kept, not orphaned.
- [x] SEMANTIC — read the migration `_SEED` in full + reconciled test diffs in full (not skimmed): every reconciliation is a verbatim transcription or additive-default mirror; the one behavioral test change (second-sync) is a strengthening, not a weakening.

### Live-verify evidence
- [x] every symbol §3 cites still resolves — CatalogModel (+3 fields), _insert_snapshot, sync_catalog (both branches provider-scoped), CompositeCatalogSource, PricingSnapshotRow, migration head chain f94771e4aa7c→9cdca76231c6 all resolve in the current tree; confirmed by the 311-test run + `alembic` harness.
- [x] no anchor moved silently — down_revision f94771e4aa7c is the confirmed prior head (billing-owner-of-record, uncommitted in shared tree, per §0).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] All 34 seeded models are `active=true` with the exact Decimal prices from the research doc — confirmed by `SELECT` against a freshly-migrated isolated test DB.
- [ ] `whisper-1`/`tts-1`/`tts-1-hd` each carry a non-NULL real `unit_usd_per_unit`; `dall-e-3` carries `pricing_unit="per_image"` with `unit_usd_per_unit` NULL (Tin scope-cut) — confirmed by `SELECT` on `pricing_snapshots`.
- [ ] A single-provider `sync_catalog` call never flips another provider's `active` flag — confirmed by the M5/R2 scenario test.
- [ ] No UNVERIFIED-flagged id (§1 Reject list) appears in `models` post-migration — confirmed by the R1 scenario test (`id NOT IN` check).
- [ ] `alembic downgrade -1` then `upgrade head` reproduces the identical row set — confirmed by a round-trip test or documented local verification (never against the shared `:5433` DB).
- [ ] No remaining reference to `static_models`/`MINIMAX_SEED_MODELS`/`GPT_REALTIME_SEED_MODELS`/`BEDROCK_SEED_MODELS`/`VERTEX_SEED_MODELS`/`OPENAI_SEED_MODELS` in `apps/gateway/src` — confirmed by `grep -rn` returning zero hits.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self + independent add-verify agent (a19f4c49585c552d9, billing-precision-engineer persona) — TWO independent adversarial passes, both EARNED. Adversarially checked: (1) provider-scope blast radius — read both sync_catalog branches line-by-line + ran test_provider_scoped_sync + test_empty_batch_noop against real Postgres: a minimax-only sync provably leaves bedrock/vertex/openai untouched, empty batch no-ops on both signatures; (2) migration price correctness — every one of 34 _SEED Decimal literals grep-diffed against the research doc (whisper 0.006/min→0.0001/sec, tts 15/1M→0.000015/char etc. all exact), ZERO float literals, dall-e-3 correctly NULL not fabricated, 9 context_length=None rows match the "confirm-before-seeding" list; (3) downgrade reversibility — RAN alembic upgrade→downgrade→upgrade on an isolated DB, 34→0→34 with Decimal re-equality, snapshots-before-models FK order correct; (4) earned-green — _EXPECTED_TOKEN_PRICES/_EXPECTED_UNIT_PRICES are independent hand-transcribed oracles (NOT importing _SEED = no tautology), cross-provider test asserts specific active flips not vacuous; (5) billing turn-on safety — recorder's non-token branch explicit-checks `if unit_usd_per_unit is None` before any Decimal conversion, no NULL-crash path for dall-e-3, multiply-path Decimal end-to-end. No defect found by either verifier.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: independent add-verify agent a19f4c49585c552d9 (+ self-corroborated)
1. Security: CLEAR — migration is static Python literals via SQLAlchemy sa.table/pg_insert/bindparams, no string-interpolated input in upgrade()/downgrade(); sync_catalog only NARROWS an existing internal deactivation sweep (no new auth/secret/injection surface).
2. Concurrency: CLEAR — migration runs in alembic's per-revision transaction (partial seed can't commit); sync_catalog deactivation UPDATE stays inside the pre-existing `async with self._session.begin()` — upsert+deactivate commit-or-rollback atomically, boundary unchanged.
3. Architecture: CLEAR — migration imports nothing from gateway.* except the precedented pure-stdlib gateway.core.ids.uuid7 (used identically by 2 prior migrations); CompositeCatalogSource kept with static_models made Optional (contract-sanctioned §1 Assumption #4), backward-compatible (3 sibling suites still construct it with static_models= and pass).
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-16

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin (dall-e-3 scope-cut) + auto (project-lead review))
- [AI] build — strategy used: Followed the planned batch order exactly (1 dataclass+_insert_snapshot → 2 provider-scoped sync_catalog+empty-batch no-op → 3 migration 9cdca76231c6 → 4 main.py drop static_models → 5 delete 5 seed files LAST). The design's ground map assumed the deletions were "byte-identical for every test" (it had checked only sync_catalog call-sites) but MISSED 20 tests across 4 already-shipped tasks (minimax-catalog-seed, region-catalog-dimension, vertex-adapter, gpt-realtime-pricing-fields) + 3 FakeCatalogModel-mirror suites (catalog, catalog_pricing_fields, catalog_sync_trigger) that IMPORT the deleted seed constants. Reconciled all of them test-only WITHOUT weakening: (a) FakeCatalogModel mirrors gained the 3 new fields with real defaults; (b) each deleted `*_SEED_MODELS` import replaced by a VERBATIM local transcription (exact ids/regions/Decimal from the migration's own `_SEED`, NOT imported — explicit anti-tautology note, same convention as the migration test's independent `_EXPECTED_TOKEN_PRICES`); (c) `test_main_wires_composite_catalog_source` re-asserted to the NEW correct behavior (`static_models == []`, DB is sole source of truth); (d) `test_second_sync_does_not_deactivate_minimax_rows` re-proven the way the guarantee now actually holds in prod — provider-scoped deactivation (`provider IN incoming_providers`) instead of the retired static_models re-append (a STRONGER test). No test assertion loosened; the migration/contract untouched.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

