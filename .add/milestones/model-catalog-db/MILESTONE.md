# MILESTONE: DB-backed model catalog: SQL seed + provider refresh

goal: The model catalog + pricing live in the DB as the source of truth (seed migration replaces in-code static seeds), refreshed on a schedule by an in-process asyncio sweeper
rationale: sub-milestone (Tin-confirmed 2026-07-16) — the in-code static seed constants (MINIMAX/GPT_REALTIME/BEDROCK/VERTEX/OPENAI seeds) become a DB SQL seed migration (source of truth), then a scheduler re-runs SyncCatalogUseCase periodically to keep it current. Split B1 (seed) + B2 (scheduled refresh) per Tin. NOTE: B2's mechanism was changed from Celery worker+beat to an asyncio lifespan sweeper (Tin change-request 2026-07-16) — celery/kombu caps redis<6.5 but the repo runs redis 8.x; see `.add/tasks/catalog-celery-refresh/TASK.md` ⚠ CHANGE-REQUEST v2.
stage: production · status: active · created: 2026-07-16T09:47:52+00:00
release: pending

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  (B1) A SQL seed migration that seeds `models` + `pricing_snapshots` from the current in-code static
     seeds (MiniMax ×3, gpt-realtime ×1, Bedrock ×6, Vertex ×4, OpenAI ×6) PLUS verified expansion rows;
     extend `CatalogModel` with `cache_creation_usd_per_token` / `pricing_unit` / `unit_usd_per_unit` +
     wire through `_insert_snapshot`; seed REAL non-token prices (dall-e-3 $/image, whisper $/sec, tts
     $/char, cache-write $/M); make `sync_catalog` deactivation PROVIDER-SCOPED (not blanket); remove
     `static_models` from `CompositeCatalogSource` in main.py; delete the 5 in-code seed files.
     (B2) A scheduled refresh (asyncio lifespan sweeper — was Celery, see rationale) that periodically fetches each provider's model list and upserts the catalog.
Out: dynamic price fetching cadence/infra beyond the scheduler skeleton; UNVERIFIED / "confirm-before-seeding"
     rows from the research doc (seed only officially-confirmed prices); any UI for the catalog.

## Shared decisions & glossary deltas   (living — every task must honor these)
- SOURCE OF TRUTH: the DB (`models` + `pricing_snapshots`) — in-code static seed lists are DELETED, not kept as a fallback.
- Tin-locked (2026-07-16): EXTEND `CatalogModel` with `cache_creation_usd_per_token` / `pricing_unit` / `unit_usd_per_unit`; seed REAL non-token prices (no more 0.0 placeholders for dall-e-3/whisper/tts).
- Tin-locked: `sync_catalog` deactivation must be PROVIDER-SCOPED (a provider's refresh only deactivates that provider's absent models) — else migrating the static seed away + a single-provider Celery refresh would blanket-deactivate every other provider's rows.
- Seed only VERIFIED prices (official page cited + fetched 2026-07-16); skip every "confirm before seeding" / UNVERIFIED row the research flagged.

## Shared / risky contracts (freeze these first)
- `CatalogModel` dataclass shape (new pricing fields) + `_insert_snapshot` mapping -> owning task catalog-db-seed
- provider-scoped `sync_catalog` deactivation predicate -> owning task catalog-db-seed

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] catalog-db-seed        depends-on: none            — B1: extend dataclass + SQL seed migration + provider-scoped sync_catalog + remove static_models + delete 5 seed files + verified expansion  (gate=PASS 2026-07-16)
- [x] catalog-celery-refresh depends-on: catalog-db-seed — B2: scheduled per-provider fetch → upsert (asyncio lifespan sweeper; was Celery — redis-8 change-request) refreshing the DB catalog  (gate=PASS 2026-07-16; v2 re-verify PASS after mechanism switch)

## Exit criteria (observable; map each to the task that delivers it)
- [x] The catalog + pricing (incl. real non-token prices) are served entirely from the DB; the 5 in-code seed files no longer exist and `CompositeCatalogSource` has no `static_models`   (← catalog-db-seed)
- [x] A single provider's `sync_catalog` refresh deactivates ONLY that provider's now-absent models, never another provider's rows   (← catalog-db-seed)
- [x] A scheduled asyncio sweeper refreshes each provider's model list into the DB catalog on a schedule   (← catalog-celery-refresh; mechanism switched from Celery per Tin's redis-8 change-request)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched (no add.py/state.json/template change).
- skill   : untouched.
- book    : untouched.
- catalog (product): CatalogModel gained cache_creation_usd_per_token/pricing_unit/unit_usd_per_unit; _insert_snapshot writes them; sync_catalog deactivation is now PROVIDER-SCOPED on both branches + empty-batch no-op; CompositeCatalogSource.static_models dropped from main.py wiring; the 5 in-code seed files deleted.
- persistence : new alembic migration 9cdca76231c6 seeds 34 models + 34 pricing_snapshots (real non-token prices for whisper/tts/gpt-realtime → live billing ON; dall-e-3 scope-cut NULL). No new tables/columns (columns pre-existed).
- catalog scheduler (asyncio, in-process): gateway/catalog/application/refresh_scheduler.py — CatalogRefreshScheduler (refresh_once/run_forever/should_start_catalog_refresh) wired into the main.py lifespan alongside the 6 existing sweepers; reuses SyncCatalogUseCase verbatim. NO new deps, NO new deploy units, NO broker (runs in the gateway process). MECHANISM CHANGE from the frozen v1 Celery contract — Tin change-request 2026-07-16 (celery/kombu caps redis<6.5; repo runs redis 8.x). All v1 celery artifacts (worker/, celery[redis], Helm worker+beat, compose worker+beat, celery_broker_db) deleted; redis restored to 8.0.1.
- config : +catalog_refresh_interval_seconds(=3600, default-ON per Tin). (v1's +celery_broker_db REMOVED — no broker.)

### Cross-task evidence   (one row per task)
- catalog-db-seed        : gate=PASS · tests=311 green (102 catalog incl. real-alembic migration harness + 209 adjacent sync/billing) · residue=none (2 non-blocking todos #48/#49 for milestone-close/typing) · TWO independent adversarial verifies both EARNED.
- catalog-celery-refresh : gate=PASS (v1 Celery) → **v2 re-verify PASS (asyncio sweeper, after Tin's redis-8 change-request)** · tests=7 own + 80 catalog blast-radius green · residue=none blocking (1 named follow-up: provider_resolver cache staleness — now cheaper, in-process; the v1 redis-URL residue is gone) · self refute-read EARNED (v2) + 3-lens CLEAR (v2).

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which): EC1 (DB is sole source of truth, seed files gone, no static_models) ← catalog-db-seed catalog+persistence ship rows + its 311-green suite; EC2 (provider-scoped deactivation) ← catalog-db-seed's test_provider_scoped_sync + its dual-verify; EC3 (scheduled refresh) ← catalog-celery-refresh scheduler ship row + its 7-green suite (CatalogRefreshScheduler started in the lifespan at default interval 3600, refreshing openrouter through the idempotent provider-scoped sync).
- goal: The model catalog + pricing live in the DB as the source of truth (seed migration replaced the in-code static seeds), refreshed on a schedule by an in-process asyncio sweeper — PROVEN by: the 5 seed files are deleted + migration 9cdca76231c6 seeds 34 rows the app now reads exclusively, and the CatalogRefreshScheduler re-runs SyncCatalogUseCase("openrouter") hourly through the idempotent provider-scoped sync (mechanism switched from Celery per Tin's redis-8 change-request; re-verified GREEN).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Fix pre-commit blockers in the shared uncommitted bundle FIRST: todo #50 (config.py:407 E501 from platform-key-default) — `make ci` will fail otherwise; and confirm the down_revision chain (B2 adds no migration; B1's 9cdca76231c6 rides on f94771e4aa7c, uncommitted).
- [ ] Commit per-task (Tin-authorized): the model-catalog-db bundle is entangled in the working tree with platform-key-default + account-tiers-billing + billing-owner-signup-population — decide whether to PR them together or split. Held for Tin.
- [ ] Deploy note: default-ON scheduler (3600s) means the GATEWAY process makes an ~hourly OpenRouter call from first deploy (no separate worker/beat pods, no broker) — ensure the OpenRouter key + egress are provisioned for the gateway pods before rollout. Set GATEWAY_CATALOG_REFRESH_INTERVAL_SECONDS=0 to opt out.
- [ ] Follow-ups (not blocking this milestone): dall-e-3 SKU pricing (todo from B1); provider_resolver cache-refresh on scheduled sync (now in-process — the scheduler can call the local resolver.refresh() directly); per-provider live sources for minimax/bedrock/vertex (the "dynamic price fetching" the milestone scoped OUT).
- [ ] tag / publish / deploy  (human-run, per release.md) — this milestone is one of ≥6 releasable since 0.8.0.
