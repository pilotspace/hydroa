# TASK: Region as a deployment dimension (us/eu/global) + EU catalog entries

slug: region-catalog-dimension · created: 2026-07-12 · stage: production
milestone: residency-service-tiers
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
sensitivity: data
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/catalog/domain/entities.py:CatalogModel` — frozen dataclass value object (upstream-sourced model). Add `region: str = field(default="global")`, mirroring the existing `modality`/`provider` additive-field convention (same file, same class).
- `apps/gateway/src/gateway/catalog/domain/entities.py:ModelRow` (domain dataclass, NOT the ORM row of the same name) — add `region: str = field(default="global")`.
- `apps/gateway/src/gateway/catalog/domain/entities.py:MarkedUpModel` — add `region: str = field(default="global")` passthrough (read-only surface, never derived — same doc convention as `input_modalities`).
- `apps/gateway/src/gateway/catalog/domain/entities.py:VALID_INPUT_MODALITIES` / `normalize_input_modalities` / `InputModality` (lines ~18-104) — the TEXT+Literal (not Postgres ENUM) validation pattern this task's NEW `Region` / `VALID_REGIONS` / `normalize_region()` mirrors exactly.
- `apps/gateway/src/gateway/catalog/infrastructure/orm.py:ModelRow` (SQLAlchemy row, `__tablename__ = "models"`) — add `region: Mapped[str] = mapped_column(Text, nullable=False, server_default="global")`, mirroring the `input_modalities` column (same file, line ~39).
- `apps/gateway/src/gateway/catalog/infrastructure/repository.py:SqlAlchemyCatalogRepository.list_active_models_with_markup` — add `ModelRow.region` to the joined select list and `region=row.region` to the `MarkedUpModel(...)` construction; the `sync_catalog` upsert (same file, ~line 295-297, alongside `modality=`/`provider=`/`input_modalities=`) needs `region=model.region`.
- `apps/gateway/src/gateway/catalog/api/schemas.py:AdminCatalogModelItem` and `:AdminModelItem` — add `region: str` (mirrors how `input_modalities` was added to these two admin/dashboard-facing DTOs, capabilities-admin-surface TASK.md precedent). `:ModelItem` (the lean `GET /v1/models` OpenAI-compatible shape) is READ-ONLY ground here — it stays byte-identical, same precedent as `input_modalities` never landing on it.
- `apps/gateway/src/gateway/catalog/api/router.py:list_catalog_models` (`GET /admin/catalog/models`) and `:get_admin_models` (`GET /admin/models`) — add `region=m.region` / `region=row.region` to their respective DTO constructions.
- `apps/gateway/src/gateway/catalog/domain/errors.py:InvalidInputModalityError` (lines 23-34) — pattern this task's NEW `InvalidRegionError(CatalogError)` mirrors (`code = "invalid_region"`).
- `apps/gateway/src/gateway/catalog/infrastructure/minimax_seed.py:MINIMAX_SEED_MODELS` — the static-seed-list pattern (a provider with no dynamic discovery API gets a hand-written `list[CatalogModel]`) this task's NEW `apps/gateway/src/gateway/catalog/infrastructure/bedrock_seed.py:BEDROCK_SEED_MODELS` follows.
- `apps/gateway/src/gateway/catalog/infrastructure/composite_source.py:CompositeCatalogSource` — read-only ground; consumed UNCHANGED (chains any `static_models` list into the same sync cycle, keeping seeded rows out of the deactivation sweep's blast radius — see Issue #6).
- `apps/gateway/src/gateway/main.py:867-869` — `app.state.catalog_source = CompositeCatalogSource(primary=OpenRouterCatalogSource(...), static_models=MINIMAX_SEED_MODELS + GPT_REALTIME_SEED_MODELS)`. This task's Build appends `+ BEDROCK_SEED_MODELS`.
- `apps/gateway/src/gateway/main.py:982` `_chat_adapters["bedrock"] = BedrockCompletionUpstream(endpoint_url=settings.bedrock_endpoint_url or None, ...)` — GROUND ONLY: confirms the Bedrock chat adapter is ALREADY registered unconditionally, so Bedrock catalog seeding is "config on an existing adapter," per the milestone's "no new adapter code" constraint.
- `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py:BedrockCompletionUpstream._endpoint_url` (~line 566-568) — GROUND ONLY: `self._endpoint_url_override or f"https://bedrock-runtime.{aws.region}.amazonaws.com"` — the actual outbound endpoint is ALWAYS derived from the tenant's own BYOK credential region (`aws.region`), NEVER from any catalog field. Critical for Issue #1 below.
- `apps/gateway/src/gateway/proxy/domain/provider_credentials.py:BedrockCredential.region` (lines 117-156) — GROUND ONLY: this is the tenant's own AWS SigV4 signing region (BYOK), a PRE-EXISTING and DIFFERENT concept from the `region` this task adds to the catalog. Never touched by this task.
- `apps/gateway/src/gateway/proxy/infrastructure/catalog_provider_resolver.py:CatalogProviderResolver.provider_for` — GROUND ONLY: unknown `model_id` fails OPEN to `"openrouter"`. Any catalog row whose `provider` has no matching `main.py` `_chat_adapters[...]` entry is a live dispatch hazard — the reason Vertex is scope-cut (Issue #2).
- `apps/gateway/src/gateway/core/config.py:Deployment` (lines 20-60, "deployment-model TASK.md §3 FROZEN @ v1") — GROUND ONLY, NOT touched: a routing-config `model_groups` member (`model_id`, `weight`, `tpm_limit`, `rpm_limit`). Name-collides with "deployment" language in MILESTONE.md — see Issue #8; this task's `region` lives on the CATALOG row (`ModelRow`), never on this frozen class.
- `apps/gateway/migrations/versions/c2e4a6f8b0d3_catalog_input_modalities.py` — the additive-column migration this task's new migration mirrors (`ADD COLUMN ... NOT NULL DEFAULT`, instant DDL on PG11+, no backfill `UPDATE` needed here since `"global"` is honestly correct for every existing row — unlike `input_modalities`, no row needs a different post-hoc value).
- `apps/gateway/migrations/versions/f1ef6b05a732_seat_billing.py` — confirmed current Alembic head (`alembic heads`, 2026-07-12) — the new migration's `down_revision`.
- `apps/gateway/tests/catalog_input_modalities/`, `apps/gateway/tests/minimax_catalog_seed/` — the two closest sibling test suites (additive-column + static-seed patterns) this task's suite mirrors.

Context (working folder): none beyond the code above — no docs/config/data files outside `apps/gateway/src/gateway/catalog/`, `apps/gateway/src/gateway/main.py`, and `apps/gateway/migrations/` are in scope.

Honors (patterns / conventions):
- TEXT+Literal domain-value columns, never a Postgres ENUM, to dodge `ALTER TYPE` migrations on future value additions (`Modality`/`InputModality` precedent, entities.py).
- static-seed-list + `CompositeCatalogSource` chaining for a provider with no dynamic discovery API (`minimax_seed.py`/`gpt_realtime_seed.py`).
- additive-column-with-`server_default` migration, instant DDL, no full-table rewrite (`c2e4a6f8b0d3` precedent).
- lean-public-vs-extended-admin surface split: `GET /v1/models` stays byte-identical; new fields land only on `GET /admin/catalog/models` / `GET /admin/models` (`input_modalities`, `catalog-pricing-fields` precedent).
- CLEAN ARCHITECTURE layering (CONVENTIONS.md) — region validation lives in `catalog/domain/`, zero framework imports; ORM/API layers only pass the already-validated value through.
- machine-readable `ERR_<DOMAIN>_<REASON>`-style codes (CONVENTIONS.md) — `InvalidRegionError.code = "invalid_region"` mirrors `InvalidInputModalityError`.

Seams consulted: none — no `.add/SEAMS.md` entry matches this task's shape.

Anchors the contract cites: `CatalogModel.region`, `ModelRow.region` (domain + ORM), `MarkedUpModel.region`, `normalize_region()`, `VALID_REGIONS`, `InvalidRegionError`, `AdminCatalogModelItem.region`, `AdminModelItem.region`, `BEDROCK_SEED_MODELS`, the new `models.region` column.

Issues/Risks (→ feed §1):
1. ⚠ **Catalog `region` does not, by itself, force the literal network egress region.** `BedrockCompletionUpstream` always signs and calls `bedrock-runtime.{tenant_credential.region}.amazonaws.com` — a tenant whose BYOK Bedrock credential is `us-east-1` can still be routed to a `region="eu"`-tagged catalog row and the request still physically leaves from `us-east-1` (AWS will likely reject a geography-mismatched cross-region-inference-profile call with its own error, but that is AWS's enforcement, not ours — not a structured, pre-flight 4xx). This task only adds the DESCRIPTOR; matching/enforcing tenant-credential region against policy region is `residency-policy`'s job (or a follow-on adapter-seam task) — named here so it is not silently assumed solved.
2. **Vertex AI has no adapter in this codebase.** Only `gemini_upstream.py` exists (Gemini API direct — API-key auth, no GCP-service-account/ADC, no regional-hosting-parity guarantee) — structurally and contractually a different product from Vertex AI. The milestone's "no new adapter code" constraint cannot be honestly met for Vertex: a `provider="vertex"` catalog row would have no `main.py` `_chat_adapters["vertex"]` entry to dispatch through. **Scope-cut in §1/§5 below** — flagged as the top ⚠.
3. Bedrock has ZERO existing catalog rows today (no `bedrock_seed.py`) even though its chat adapter is registered unconditionally — seeding Bedrock into the catalog at all is new ground this task establishes, not a pure region-tag-on-existing-rows change.
4. `models.id` is the catalog's PRIMARY KEY (bare string). The SAME literal Bedrock model id is reachable from multiple AWS regions, so a naive "one row per model id" cannot carry two different `region` values for what upstream considers the same model. **Resolved in §3** by seeding AWS Bedrock's own real cross-region inference-profile ids (`us.<model-id>` / `eu.<model-id>`) as the catalog `id` — already-unique by AWS's own convention, and directly usable by the existing adapter unchanged (no synthetic namespace invented).
5. MILESTONE.md asks to "enumerate the REAL Bedrock EU (Frankfurt/Ireland/Paris/Stockholm)" while binding rule #1 pins `region` to a COARSE 3-value enum. Resolved by keeping `region` coarse (`"eu"`) — the four literal AWS region codes (`eu-central-1`/`eu-west-1`/`eu-west-3`/`eu-north-1`) are exactly the region GROUP AWS's own `eu.` cross-region inference profile fans out across server-side; they are documented as a code comment on the seed row, not a 5th catalog column or four extra rows. Flagged as an open assumption (§1) in case the human wants real per-city candidate rows instead.
6. Dynamic OpenRouter-sourced rows (the majority of the catalog) carry no real region signal from upstream — defaulting `region="global"` for every dynamic row is an HONEST statement (OpenRouter aggregates many providers with no per-model residency guarantee today), not a placeholder.
7. No existing endpoint lets a tenant or admin edit a shared `ModelRow` field directly — `PUT /admin/models/{model_id}` only writes the PER-TENANT override row (`enabled`). `modality`/`provider`/`input_modalities` are ALL sync-controlled only. This task keeps `region` on that same footing (no new admin-PUT surface) — deliberate, not an oversight.
8. Name collision: MILESTONE.md's "deployment/catalog row" language reads naturally as `gateway.core.config.Deployment` (a FROZEN @ v1, routing-config `model_groups` member — weight/tpm/rpm, nothing about region) — but that class is NOT where region belongs; `region` lives on the catalog's `ModelRow`. Glossary delta below names both to prevent the collision from leaking into a sibling task's contract.

Related intent: MILESTONE.md shared decision 1 ("region is a deployment dimension... single source of truth... NEVER inferred from a provider URL at request time") and the milestone GOAL ("selling what Anthropic verifiably lacks — no first-party EU"). GLOSSARY.md `Model` / `Model catalog` (lines 8-9) — extended by this task's Glossary delta.

Ground SHA: c3f972d

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `region` catalog dimension (us|eu|global) + Bedrock-EU seed entries
Framings weighed:
(chosen) Coarse `region` enum as an additive TEXT column on the existing `ModelRow`/`CatalogModel` (honors binding rule #1 verbatim); Bedrock us/eu variants get AWS's own real cross-region-inference-profile ids (`us.`/`eu.` prefixed) as distinct catalog `id`s, since the same bare Bedrock model id is valid in multiple AWS regions and `id` is the PK; Vertex scope-cut for lack of an adapter.
· alternative A — composite PK `(id, region)` replacing the bare `id` PK on `models`: REJECTED — breaking change rippling through two FK-dependent tables (`pricing_snapshots.model_id`, `tenant_model_overrides.model_id`) and every bare-string-id caller (`CatalogProviderResolver`, admin `PUT /admin/models/{model_id:path}`); far outside additive-only scope and touches other FROZEN contracts.
· alternative B — fine-grained per-city `region` enum (`eu-central-1` etc. as first-class values): REJECTED — contradicts binding rule #1's explicit us|eu|global enum, and would need a new value every time AWS/GCP add a region while residency policy only ever reasons in the coarse bucket.

Must:
<must>
  - M1: `CatalogModel` / `ModelRow` (domain) / `MarkedUpModel` gain an additive `region: str` field, defaulting `"global"`.
  - M2: every dynamically-synced (OpenRouter) row and every pre-existing row gets `region="global"` (migration `server_default`, no differentiated backfill — "global" is honestly correct for 100% of today's catalog).
  - M3: `region` is validated at the domain boundary by a new `normalize_region()` against `VALID_REGIONS = frozenset({"us","eu","global"})`, mirroring `normalize_input_modalities()`.
  - M4: a new static seed (`bedrock_seed.py`) adds Bedrock catalog rows using AWS's real cross-region inference-profile ids: `region="us"` (`us.<model-id>`) and `region="eu"` (`eu.<model-id>`) siblings for each seeded Claude-on-Bedrock model; the `eu.` profile's real backing region group (Frankfurt `eu-central-1` / Ireland `eu-west-1` / Paris `eu-west-3` / Stockholm `eu-north-1`) is documented as a code comment, not a schema field.
  - M5: `GET /admin/catalog/models` (`AdminCatalogModelItem`) and `GET /admin/models` (`AdminModelItem`) expose `region`. `GET /v1/models` (`ModelItem`) stays byte-identical — no `region` field added there.
  - M6: `region` is sync-controlled only — set exclusively by `CatalogSource.list_models()` / seed definitions and persisted by `SyncCatalogUseCase` / `SqlAlchemyCatalogRepository.sync_catalog`; no endpoint can mutate an existing row's `region` directly.
  - M7: Bedrock seed rows are yielded on every sync cycle (via `CompositeCatalogSource`, unchanged), so they never fall into the deactivation sweep's `notin_(incoming_ids)` blast radius.
  - M8: every new/changed symbol stays `mypy --strict` / `ruff` clean and inside CLEAN ARCHITECTURE layering (region validation in `catalog/domain/` only) — confirmed by a passing `make ci` at Verify, not a runtime-observable API behavior.
</must>
Reject:
<reject>
  - R1: an unknown `region` token reaching `normalize_region()` (seed data, or any future admin surface) -> `"invalid_region"` (`InvalidRegionError.code`)
  - R2: a Vertex-provider catalog row (`provider="vertex"`) seeded anywhere in this task's Build -> `"vertex_adapter_missing"` — a BUILD-time/self-review guard (no live endpoint accepts a raw `provider` string from a caller today), not a runtime API error; Strategy (§5) forbids seeding any `provider="vertex"` row until a real Vertex adapter exists.
  - R3: two seeded catalog rows resolving to the SAME `id` (e.g. accidentally reusing the bare Bedrock model id for both the `us.` and `eu.` variant) -> the existing `models.id` PRIMARY KEY constraint rejects the upsert (`IntegrityError`); no new enforcement code needed, cited here so the seed-authoring Strategy explicitly avoids ever emitting a bare (unprefixed) Bedrock id.
  - R4: a `CatalogModel`/seed entry that OMITS `region` -> defaults to `"global"` — NOT a rejection, listed here only to distinguish "omitted" (fine) from "present-but-wrong" (R1).
</reject>
After:
<after>
  - Every `ModelRow` (dynamic + static) carries a `region` value in {us, eu, global}; the catalog is never NULL/ambiguous on region.
  - `GET /admin/catalog/models` and `GET /admin/models` show `region` per row; `GET /v1/models` is byte-identical to before this task.
  - At least one real Bedrock-EU catalog entry (`region="eu"`, an AWS cross-region inference-profile id) and its `region="us"` sibling exist after a catalog sync, each independently addressable by its own `id`.
  - No Vertex catalog row exists yet — MILESTONE.md's Vertex-EU exit criterion is EXPLICITLY deferred (fed back as a milestone scope note at freeze), never silently dropped.
  - `residency-policy` (the next task) has a real, queryable `region` column to filter candidates by, plus an explicit, documented (not enforced-by-this-task) gap: catalog `region` tagging alone does not force the literal AWS egress region — see Issue #1.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Vertex AI has no adapter in this codebase, so "Vertex EU catalog entries" (an explicit MILESTONE.md exit criterion) cannot be delivered by this task without violating its own "no new adapter code" constraint — lowest confidence because this is a genuine scope contradiction in the milestone text only the human can resolve (build a thin Vertex adapter now / defer Vertex to a later milestone / accept a documented gap); if wrong (human wants Vertex now): this task's Build scope and Strategy need a full re-spec adding real Vertex adapter work — a materially bigger task than "region-catalog-dimension" as scoped, costing a contract re-freeze, not just more code.
  - [ ] The four literal Bedrock-EU AWS region codes should be carried as an inert code-comment descriptor (this draft's choice), NOT four separate catalog rows — confirm or deny; if the human wants real per-city candidate rows (e.g. future latency/failover diversity within "eu"), the seed grows 4x and the id scheme needs a city segment.
  - [ ] The exact Claude-on-Bedrock model set to seed (this draft proposes Claude 3.5 Sonnet v2 + Claude 3.5 Haiku as the pair AWS documents with `eu.`-prefixed cross-region inference profiles) — confirm against a LIVE AWS Bedrock EU model-availability check before freeze; a stale/wrong id produces a catalog entry nothing can ever successfully call (AWS 4xx/5xx at request time, not caught by this task's own validation).
  - [ ] Seeding `region="us"` Bedrock rows (not just `"eu"`) even though only EU is named in MILESTONE.md's exit criterion — reasoning: an EU-only Bedrock catalog (everything else defaulting "global") would leave no `region="us"`-pinned or -preferring tenant a Bedrock/Claude candidate to route to, which reads as an accidental asymmetry; low cost if wrong — dropping the `"us"` rows is a pure subtraction, no rework.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: pre-existing catalog row defaults to global region   # M1, M2
  Given a `models` row synced before this task's migration ran
  When the migration `ADD COLUMN region TEXT NOT NULL DEFAULT 'global'` applies
  Then that row's `region` reads "global"
  And no other column on the row changed

Scenario: dynamic OpenRouter sync leaves region global   # M1, M2
  Given `OpenRouterCatalogSource.list_models()` yields a `CatalogModel` with no `region` set
  When `SyncCatalogUseCase.execute()` persists it
  Then the resulting `ModelRow.region` is "global"

Scenario: normalize_region accepts a valid token   # M3
  Given the raw token "eu"
  When `normalize_region("eu")` is called
  Then it returns "eu"

Scenario: normalize_region rejects an unknown token   # M3, R1
  Given the raw token "apac"
  When `normalize_region("apac")` is called
  Then it raises `InvalidRegionError` with `code == "invalid_region"`
  And no catalog row is written

Scenario: Bedrock EU seed row syncs with region=eu   # M4
  Given `BEDROCK_SEED_MODELS` contains a `CatalogModel(id="eu.anthropic.claude-3-5-sonnet-20241022-v2:0", region="eu", provider="bedrock", ...)`
  When a catalog sync runs
  Then a `ModelRow` with that exact `id` exists with `region == "eu"` and `provider == "bedrock"`

Scenario: Bedrock US seed row syncs with region=us and a distinct id   # M4, R3
  Given `BEDROCK_SEED_MODELS` also contains `CatalogModel(id="us.anthropic.claude-3-5-sonnet-20241022-v2:0", region="us", provider="bedrock", ...)`
  When a catalog sync runs
  Then a SECOND `ModelRow` exists with a DIFFERENT `id` than the EU sibling, `region == "us"`
  And no `IntegrityError` occurs (the two ids never collide)

Scenario: admin catalog surface exposes region   # M5
  Given an active `ModelRow` with `region == "eu"`
  When a tenant calls `GET /admin/catalog/models`
  Then the matching `AdminCatalogModelItem.region` is "eu"

Scenario: admin models surface exposes region   # M5
  Given an active `ModelRow` with `region == "eu"`
  When an owner/admin calls `GET /admin/models`
  Then the matching `AdminModelItem.region` is "eu"

Scenario: public models list stays byte-identical   # M5
  Given the same active `ModelRow` set as the two scenarios above
  When a caller requests `GET /v1/models`
  Then the response body contains no `region` key anywhere
  And every other field matches the pre-this-task `ModelItem` shape exactly

Scenario: region is not admin-editable   # M6
  Given an active `ModelRow` with `region == "us"`
  When `PUT /admin/models/{model_id}` is called with any body
  Then the row's `region` is unchanged after the call
  And the response shape is unaffected (still only toggles the per-tenant `enabled` override)

Scenario: Bedrock seed rows survive the deactivation sweep   # M7
  Given a prior sync already persisted both Bedrock seed rows as active
  When a second sync runs with the SAME `BEDROCK_SEED_MODELS` list
  Then both rows remain `active == true`
  And neither is swept by the `notin_(incoming_ids)` deactivation logic

Scenario: no Vertex row is ever seeded by this task   # R2
  Given this task's Build scope (`§5`)
  When the full seed-file diff for this task is reviewed
  Then no `CatalogModel`/`ModelRow` entry anywhere in the diff has `provider == "vertex"`
  And `main.py`'s `_chat_adapters` mapping is unchanged (no `"vertex"` key added)

Scenario: static seed omitting region defaults global, not rejected   # R4
  Given a `CatalogModel(...)` constructed WITHOUT a `region` kwarg
  When it is synced
  Then the persisted `ModelRow.region` is "global"
  And no `InvalidRegionError` is raised
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

### `region` semantics (citable verbatim by sibling tasks: residency-policy, region-pricing)

> `region: Literal["us", "eu", "ap", "global"]` — a coarse compliance/residency tag carried on every
> `models` (catalog) row. It is the ONE source of truth a residency policy filters by and a rate
> card multiplies by (MILESTONE.md binding rule #1/#3). It is set EXCLUSIVELY by catalog sync
> (seed data or a future region-aware dynamic source) — never inferred from a provider URL, never
> admin/tenant-PUT-editable, never derived at request time. `"global"` is the default and means
> "no residency guarantee is asserted for this row" (the honest state of every OpenRouter-sourced
> row today) — it is NOT a fourth "worldwide-compliant" claim. `region` describes WHERE this
> catalog candidate MAY be considered eligible; it does NOT by itself force the literal network
> egress region of a request (see Issue #1 — Bedrock's actual egress region is the tenant's own
> BYOK credential region, `BedrockCredential.region`, a distinct pre-existing concept). Any
> consumer that needs enforcement, not just candidate filtering, must compose `region` with that
> adapter-level fact — this task supplies the descriptor only.

```
Schema (models table, additive):
  ALTER TABLE models ADD COLUMN region TEXT NOT NULL DEFAULT 'global';
  -- no backfill UPDATE: 'global' is honestly correct for every existing row.
  -- migration file: <new_rev>_catalog_region.py, down_revision = f1ef6b05a732 (current head)
  -- downgrade: DROP COLUMN region (safe — no FK, no other table references it)

Domain (apps/gateway/src/gateway/catalog/domain/entities.py):
  Region = Literal["us", "eu", "ap", "global"]
  VALID_REGIONS: frozenset[str] = frozenset({"us", "eu", "global"})
  def normalize_region(value: str) -> str: ...   # -> "us"|"eu"|"global", raises InvalidRegionError
  CatalogModel.region: str = field(default="global")
  ModelRow.region: str = field(default="global")            # domain dataclass
  MarkedUpModel.region: str = field(default="global")

Errors (apps/gateway/src/gateway/catalog/domain/errors.py):
  class InvalidRegionError(CatalogError):
      code: str = "invalid_region"

Infrastructure (apps/gateway/src/gateway/catalog/infrastructure/):
  orm.py: ModelRow.region: Mapped[str] = mapped_column(Text, nullable=False, server_default="global")
  repository.py: list_active_models_with_markup() SELECTs ModelRow.region, passes region=row.region
                 into MarkedUpModel(...); sync_catalog() upsert sets region=model.region
  bedrock_seed.py (NEW): BEDROCK_SEED_MODELS: list[CatalogModel] — AWS cross-region
    inference-profile ids as the catalog `id` (already globally unique by AWS's own convention):
      id="us.anthropic.claude-3-5-sonnet-20241022-v2:0"  region="us"  provider="bedrock"
      id="eu.anthropic.claude-3-5-sonnet-20241022-v2:0"  region="eu"  provider="bedrock"
      id="us.anthropic.claude-3-5-haiku-20241022-v1:0"   region="us"  provider="bedrock"
      id="eu.anthropic.claude-3-5-haiku-20241022-v1:0"   region="eu"  provider="bedrock"
    (exact model set + pricing to be confirmed against live AWS Bedrock docs before freeze —
    §1 ⚠ open assumption. The `eu.` profile's real backing region group — Frankfurt
    eu-central-1 / Ireland eu-west-1 / Paris eu-west-3 / Stockholm eu-north-1 — is a code
    comment on this list, not a schema field.)
  main.py:867-869: static_models=MINIMAX_SEED_MODELS + GPT_REALTIME_SEED_MODELS + BEDROCK_SEED_MODELS

API (apps/gateway/src/gateway/catalog/api/):
  GET /admin/catalog/models
    200 -> { object: "list", data: [{ ..., region: "us"|"eu"|"global", ... }] }   # AdminCatalogModelItem += region
  GET /admin/models
    200 -> { object: "list", data: [{ ..., region: "us"|"eu"|"global" }] }        # AdminModelItem += region
  GET /v1/models
    200 -> { object: "list", data: [{ ...unchanged... }] }                       # ModelItem: NO region field (byte-identical)
  PUT /admin/models/{model_id}
    unchanged — still only toggles the per-tenant `enabled` override; cannot write `region`
  POST /internal/catalog/sync , POST /admin/catalog/sync
    unchanged response shape — region flows through the existing sync pipeline silently
```

Scope-cut (explicit, not silent): NO `provider="vertex"` catalog row is created by this task.
MILESTONE.md's "Vertex EU deployment entries" exit criterion is DEFERRED — see §1 ⚠. A forward
SPEC delta ("Vertex AI adapter needed before Vertex catalog rows can be seeded") is recorded at
§7 OBSERVE and should be folded back into MILESTONE.md's own Exit criteria at the freeze
conversation, not discovered later by `residency-tiers-ui`.

Glossary deltas:
- `region`: a coarse compliance/residency tag (`us`|`eu`|`global`) on each catalog `models` row —
  the single source of truth a residency policy filters by and a rate card multiplies by; never
  inferred from a URL, never request-time-derived. Distinct from `BedrockCredential.region`
  (pre-existing, GLOSSARY-untracked until now) — the tenant's OWN AWS SigV4 signing region; the
  two are related-but-independent and must never be conflated by a sibling task.
- `region-tagged catalog row`: one `models` row `(id, provider, region)` — e.g. an AWS Bedrock
  cross-region inference profile (`eu.anthropic.claude-3-5-sonnet-20241022-v2:0`, `region="eu"`)
  — a residency-policy candidate. Distinct from `gateway.core.config.Deployment` (FROZEN @ v1,
  a routing-config `model_groups` member carrying weight/tpm/rpm, no region field, NOT touched
  by this task) — the two "deployment" senses collide in MILESTONE.md prose; this glossary entry
  exists to stop that collision from leaking into a frozen sibling contract.

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — draft only; the freeze report renders when a human reviews this for FROZEN.

Least-sure flag surfaced at freeze: ⚠ [spec] Vertex AI has no adapter in this codebase, so
MILESTONE.md's "Vertex EU deployment entries" exit criterion cannot be delivered by this task
without violating its own "no new adapter code" constraint. This task ships Bedrock-EU only and
scope-cuts Vertex with an explicit forward SPEC delta. If the human wants Vertex in THIS
milestone, this task's Build scope (§5) and Strategy need a real re-spec (a genuine adapter build,
not a config-data addition) before Build starts — cost of being wrong here is a contract
re-freeze, not a code patch.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

DECIDED at freeze review (2026-07-12, Tin): (1) Vertex gap resolved by GROWING M2 — a NEW
`vertex-adapter` task (real Vertex AI adapter: service-account auth, {region}-aiplatform
endpoints, EU entries) joins the milestone; this task's scope stays Bedrock-only and its
region shape is what vertex-adapter's entries will cite. (2) Coarse `eu` granularity CONFIRMED
(matches AWS eu. profile fan-out; cities stay a comment). (3) Seed BOTH symmetric us. and eu.
Bedrock inference-profile rows CONFIRMED.
(4) [Tin directive 2026-07-12, mid-freeze] ASIA SUPPORT ADDED: the region value set grows to
`us | eu | ap | global`. Seed AWS's real `apac.` cross-region inference-profile rows
(region="ap"; the apac. profile fans across Tokyo/Seoul/Singapore/Sydney/Mumbai — same
coarse-region rationale as eu). VIETNAM has NO hyperscaler region today (AWS: none; GCP: none)
— Vietnamese tenants are served in-region via the ap pin (nearest: Singapore/Thailand
endpoints); a `vn` value would promise what no provider can deliver and is deliberately NOT
added. The Literal/validation set everywhere in this contract reads us|eu|ap|global.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features; let the named Persona's domain stance (below) shape the approach, not just architecture patterns>

Persona (required): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; name "generic" if no project persona fits yet>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
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
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

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
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

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
