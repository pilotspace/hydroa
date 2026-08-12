# TASK: Scheduled catalog refresh into the DB (asyncio sweeper — was Celery worker+beat)

slug: catalog-celery-refresh · created: 2026-07-17 · stage: production
milestone: model-catalog-db
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## ⚠ CHANGE-REQUEST v2 (Tin-approved 2026-07-16) — mechanism switch: Celery → asyncio sweeper

**What changed & why (a REAL change to the frozen §3 contract → a change-request back to Specify,
NOT a build fudge):** the v1 contract (below, kept intact for provenance) froze the mechanism as a
**Celery worker + beat**. At commit time the fatal, non-negotiable blocker surfaced: `celery[redis]`
(celery 5.6.3 / kombu 5.6.2) hard-caps `redis!=4.5.5,!=5.0.2,<6.5,>=4.5.2`, but **this repo runs
redis 8.x** (`pyproject redis>=5` → 8.0.1). Installing celery silently **downgraded redis 8→6.4**,
which broke pyright + risked the redis-8 runtime code paths (batches / video / mcp_connector / proxy
/ alerting). **No released celery/kombu supports redis 8.** Surfaced to Tin (Rule 1 — a real blocker
STOPS); Tin chose **"Switch B2 to an asyncio scheduler"** — the repo's OWN dominant precedent for
periodic work (6 existing `should_start_*` + `run_forever(interval, *, _sleep)` lifespan sweepers).

**New mechanism (the shape that now governs — supersedes the v1 §3 CONTRACT below):**
```
MODULE   apps/gateway/src/gateway/catalog/application/refresh_scheduler.py
  def should_start_catalog_refresh(interval_seconds: int) -> bool         # interval>0 gate
  class CatalogRefreshScheduler:
      __init__(*, session_factory: async_sessionmaker, catalog_source: CatalogSource)
      async refresh_once() -> int
          # opens a session off the SHARED sessionmaker, builds SqlAlchemyCatalogRepository(session),
          # runs SyncCatalogUseCase(source, repo).execute(); returns count.
          # FAIL-OPEN: CatalogSourceUnavailableError OR any Exception → logged, return 0 (NEVER raises,
          # no partial write — sync_catalog is atomic; self-heals on the next tick).
      async run_forever(*, interval_seconds: float, _sleep=asyncio.sleep) -> None
          # work-then-sleep loop; swallows non-CancelledError; propagates CancelledError for shutdown.

WIRING   apps/gateway/src/gateway/main.py lifespan — mirrors RetentionSweeper exactly:
  app.state.catalog_refresh_task = None
  if should_start_catalog_refresh(_settings.catalog_refresh_interval_seconds):
      app.state.catalog_refresh_task = asyncio.create_task(
          CatalogRefreshScheduler(session_factory=_sessionmaker,
                                  catalog_source=app.state.catalog_source).run_forever(
              interval_seconds=float(_settings.catalog_refresh_interval_seconds)))
  # cancelled on lifespan shutdown alongside the other sweeper tasks.

CONFIG   core/config.py — catalog_refresh_interval_seconds: int = Field(default=3600, ge=0)  KEPT.
  # GATEWAY_CATALOG_REFRESH_INTERVAL_SECONDS. Default-ON 3600 (Tin's freeze, unchanged). 0 = not started.
  # REMOVED vs v1: celery_broker_db knob (no broker exists).

DROPPED vs v1 (no longer exist — celery-specific): gateway/worker/{celery_app,tasks}.py,
  CatalogProviderUnsupportedError, PROVIDER_SOURCES resolver, celery[redis] dep, celery_broker_db knob,
  Helm worker/beat-deployment.yaml, docker-compose worker/beat services. NO new deploy unit — the
  sweeper runs IN the existing gateway process (the whole point: no new pods, no broker, no redis downgrade).

KEPT from v1 intent: single interval knob (default-ON 3600); reuses SyncCatalogUseCase VERBATIM
  (no fetch/upsert logic duplicated); openrouter is the only live source (minimax/bedrock/vertex/openai
  survive each refresh via B1's provider-scoped deactivation); idempotent → a retried/duplicated tick
  is safe; NO PARTIAL WRITE (inherited from sync_catalog's atomic session.begin()).

TESTS    apps/gateway/tests/catalog_refresh_scheduler/ (7 green): should_start predicate · refresh_once
  happy-path (returns count, writes rows) · refresh_once fail-open (source down → 0, zero rows, no raise)
  · provider-scoped deactivation (openrouter refresh leaves minimax untouched) · run_forever survives a
  raised cycle + cancels cleanly · lifespan wired when interval>0 · lifespan task=None when interval=0.
```
**Simpler-by-construction:** the async/sync bridge (asyncio.run + per-run engine disposal), the broker
db-isolation, the beat-singleton deployment constraint, and the unsupported-provider rejection — all v1
risk surface — **evaporate**: no separate process, no broker, no per-run engine (reuses the app's
sessionmaker), source late-bound from `app.state.catalog_source`. Re-verified GREEN (see §6 v2 note).

**The v1 Celery contract & sections below are RETAINED AS PROVENANCE — superseded, not live.**

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/catalog/application/use_cases.py:SyncCatalogUseCase.execute` — the UNIT OF WORK B2 schedules. `__init__(source: CatalogSource, repository: CatalogRepository)`; `async execute() -> int` does fetch (`source.list_models()` + `list_embedding_models()`) → `repository.sync_catalog(models, embedding_models=...)`. Chat-fetch failure raises `CatalogSourceUnavailableError` (whole sync fails, NO partial write); embeddings-fetch failure is caught → degrades to `embedding_models=None`. B2 wraps this — does NOT reimplement sync.
- `apps/gateway/src/gateway/catalog/infrastructure/openrouter_source.py:OpenRouterCatalogSource` — `__init__(client: httpx.AsyncClient)`, module `_TIMEOUT = httpx.Timeout(10.0)`, catches `httpx.TimeoutException/NetworkError/HTTPStatusError`. THE ONLY live CatalogSource post-B1 (yields provider="openrouter" rows). minimax/bedrock/vertex/openai rows are DB-seeded (migration 9cdca76231c6) with NO live source — they survive each openrouter refresh via B1's provider-scoped deactivation.
- `apps/gateway/src/gateway/catalog/infrastructure/composite_source.py:CompositeCatalogSource` — `__init__(primary, static_models: list|None=None)`; post-B1 wired `CompositeCatalogSource(primary=OpenRouterCatalogSource(httpx.AsyncClient()))` in main.py (no static_models). This is the `source` the scheduled task builds.
- `apps/gateway/src/gateway/catalog/infrastructure/repository.py:SqlAlchemyCatalogRepository` — `__init__(session: AsyncSession)`; async SQLAlchemy. `sync_catalog` is idempotent+transactional (single `async with session.begin()`, ON CONFLICT upsert, provider-scoped deactivation) — so a retried/duplicated refresh is SAFE (the property that makes Celery autoretry sound).
- `apps/gateway/src/gateway/main.py` — lifespan builds `create_async_engine`/`async_sessionmaker` → `app.state.sessionmaker`; the manual sync trigger path is `POST /admin/catalog/sync` + `POST /internal/catalog/sync` (`catalog/api/router.py:sync_catalog`) → `get_sync_use_case` (`catalog/api/deps.py`) → `SyncCatalogUseCase(source, SqlAlchemyCatalogRepository(session))`. A Celery task runs OUTSIDE this lifespan/request scope → must construct its OWN engine+sessionmaker+httpx client+source inside the task.
- `apps/gateway/src/gateway/core/config.py:Settings.redis_url` (default `redis://localhost:6380/0`) — the Redis already used for cache/rate-limit (db 0). Celery broker+result-backend reuse this host but MUST take a DEDICATED db number (e.g. /3) — cross-db-0 contamination is a recorded gotcha ([[shared-test-postgres-no-timeouts]]-adjacent Redis-db contamination lessons).

Context (working folder): `.add/milestones/model-catalog-db/MILESTONE.md` (B2 = "a Celery worker that periodically fetches each provider's model list and upserts the catalog"; exit criterion "A Celery worker refreshes each provider's model list into the DB catalog on a schedule"; OUT = "dynamic price fetching cadence/infra beyond the worker skeleton"). Deployment surface: `infra/docker-compose.dev.yml`, `infra/docker-compose.prod.yml`, Helm chart (`infra/kind/` + chart from v53) — worker + beat are NEW deployment units (same gateway image, different command).

Honors (patterns / conventions): DESIGN-FOR-FAILURE (global rule): the OpenRouter fetch is network IO — timeout (source already 10s) + Celery autoretry-with-backoff; idempotent sync makes retry safe; a fetch failure must NOT partially write (SyncCatalogUseCase already guarantees this). money-is-Decimal (unchanged — B2 writes nothing new, only re-runs B1's writer). NOTE the DEPARTURE: the 6 existing periodic jobs (RetentionSweeper, OpenRouterRecoverySweeper, CreditHoldRecoverySweeper, ReconciliationDriftChecker, UpstreamHealthChecker, BatchWindowFlusher) are all asyncio lifespan tasks gated by a `GATEWAY_*_INTERVAL_SECONDS` knob — Tin chose real Celery over that convention (2026-07-16 AskUserQuestion) to establish a reusable async-task framework; B2 is the FIRST Celery user in the repo.

Seams consulted: none for Celery (no prior Celery seam exists — this task establishes it).

Anchors the contract cites: `SyncCatalogUseCase`, `OpenRouterCatalogSource`, `CompositeCatalogSource`, `SqlAlchemyCatalogRepository`, `Settings.redis_url`, `create_async_engine`/`async_sessionmaker`, the new celery app module + task, docker-compose.prod.yml/Helm worker+beat units.

Issues/Risks (→ feed §1):
- ASYNC/SYNC BRIDGE (highest risk): the entire catalog stack is async (httpx.AsyncClient + async SQLAlchemy) but a Celery task body is sync. The task must `asyncio.run(_refresh_async(provider))` and construct+dispose its own engine/session/client INSIDE (never reuse the FastAPI app's engine — different/no event loop). Engine disposal per task-run to avoid leaked pools.
- PER-PROVIDER modeling vs single live source: only "openrouter" resolves to a real source today. Task should be provider-parameterized (`refresh_catalog(provider)`) with a provider→source resolver; beat schedules only providers that HAVE a live source (openrouter), the rest are skeleton-ready. Avoid pretending minimax/bedrock/vertex refresh when they have no source.
- MULTI-REPLICA beat: exactly ONE beat scheduler must run (N beats = N× duplicate schedules). Worker replicas are fine (idempotent). Deployment must run beat as a singleton (replicas=1).
- BROKER db isolation: Celery on redis_url db 0 would collide with cache/rate-limit keys → dedicated db number + config knob.
- TESTING without a live broker: Celery `task_always_eager`/direct task-function call + a real Redis integration smoke; do NOT require a running worker in the unit suite.
- DEFAULT ON/OFF: mirror the sweepers' safety instinct — a config knob for the beat interval; decide default-ON (at a sane interval) vs default-OFF (opt-in). Feeds §1.
Related intent: model-catalog-db milestone goal — "refreshed per-provider by a Celery worker" is the half B1 left open; B2 closes it. PROJECT goal: accurate billable cost tracking needs a fresh catalog.
Ground SHA: 3c27af5

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A real Celery worker + beat scheduler that wraps the existing `SyncCatalogUseCase` in a provider-parameterized task, disposed-per-run async bridge, and a singleton beat schedule for the OpenRouter provider — establishing the first Celery seam in the repo.

Framings weighed:
(chosen) A provider-parameterized `refresh_catalog(provider: str)` Celery task whose sync body does `asyncio.run(_refresh_async(provider))`, constructing+disposing its own engine/session/httpx client per run, driven by a beat schedule that only ever names "openrouter" (the sole live-source provider per §0) — the task never reimplements fetch/upsert, only orchestrates `SyncCatalogUseCase.execute()` inside a fresh async context.
· alternative: extend the existing asyncio-lifespan-sweeper convention (`RetentionSweeper`-shaped: `should_start_*(settings)` + `run_forever(interval, _sleep)` in-process background task) with a `CatalogRefreshSweeper` — REJECTED by Tin's explicit 2026-07-16 decision (§0) to use real Celery instead specifically to establish a reusable async-task framework (multi-process scaling, dead-letter/retry primitives, a standalone beat scheduler) that the in-process sweeper pattern cannot offer; recorded here only because it is the repo's own dominant precedent and the departure needs to be visible, not because it is live.
· alternative: one generic task that loops every known provider internally (`refresh_catalog()` with no args, iterating a hardcoded provider list) — REJECTED: collapses the provider→source resolver into an implicit loop, so a source-less provider (minimax/bedrock/vertex) either silently no-ops or needs special-casing INSIDE the task body; also couples one provider's retry/backoff blast radius to every other provider's schedule (an OpenRouter timeout would delay/retry a MiniMax "refresh" that does not exist). The provider-parameterized task (chosen) keeps each provider's schedule/retry independent and makes an unsupported provider a first-class rejection (R1) instead of a silent branch.

Must:
<must>
  - M1 (async/sync bridge): `refresh_catalog`'s Celery task body is SYNC; it calls `asyncio.run(_refresh_async(provider))`. `_refresh_async` constructs its OWN `create_async_engine(settings.database_url)` + `async_sessionmaker(engine, expire_on_commit=False)` + `httpx.AsyncClient()` INSIDE the run — it never imports or reuses `app.state.engine`/`app.state.sessionmaker` from `main.py` (§0: a Celery worker process has no FastAPI lifespan, no shared event loop, no `app.state`). All three resources are disposed (`await engine.dispose()`, `await client.aclose()`) in a `finally` before the coroutine returns, on BOTH the success and the exception path — no connection-pool growth across repeated scheduled ticks.
  - M2 (provider-parameterized task + resolver): `refresh_catalog(provider: str) -> int` looks up `provider` in a small resolver mapping (`PROVIDER_SOURCES: dict[str, Callable[[httpx.AsyncClient], CatalogSource]]`). Only `"openrouter"` resolves to a live source: `CompositeCatalogSource(primary=OpenRouterCatalogSource(client))` — byte-identical construction to `main.py`'s post-B1 wiring (§0). No other provider key exists in the map today (minimax/bedrock/vertex/openai are DB-seeded with no live source, per §0/B1).
  - M3 (reuse the unit of work, never reimplement it): inside `_refresh_async`, the resolved source + a `SqlAlchemyCatalogRepository(session)` are passed straight into `SyncCatalogUseCase(source, repository).execute()` — the EXACT class B1 ships, called exactly as `catalog/api/deps.py:get_sync_use_case` already does for the manual `POST /admin/catalog/sync` path. No fetch, upsert, or deactivation logic is duplicated in `worker/`.
  - M4 (idempotent autoretry): the task is decorated with `autoretry_for=(CatalogSourceUnavailableError, httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)`, `retry_backoff=True` with a capped `retry_backoff_max`, and a finite `max_retries`. This is SAFE only because `SyncCatalogUseCase.execute()` → `SqlAlchemyCatalogRepository.sync_catalog` is idempotent — ON CONFLICT upsert + provider-scoped deactivation (§0 ground fact, B1-frozen) — so a retried run re-applies the same upsert, never double-inserts or double-bills.
  - M5 (no partial write — inherited invariant): the task issues NO database write of its own outside the call into `SyncCatalogUseCase.execute()`. A chat-fetch failure raises `CatalogSourceUnavailableError` BEFORE `sync_catalog` is ever called (§0: uncaught, whole sync fails); once `sync_catalog` starts, its single `async with session.begin()` commits or rolls back atomically. The task adds zero new failure window between fetch and write.
  - M6 (beat schedule, openrouter-only): the Celery app's `beat_schedule` conditionally registers ONE entry — `refresh_catalog.s("openrouter")` — on an interval read from a new Settings knob at process start. The entry is present only when the interval is `> 0` (see R-adjacent knob convention below); no entry is ever registered for minimax/bedrock/vertex/openai (§0: no live source — scheduling them would either silently no-op or need a fake rejection every tick, both worse than simply never scheduling them).
  - M7 (singleton beat, scalable workers): exactly ONE beat-scheduler replica may run cluster-wide (N beats would fire N duplicate schedules) — enforced structurally, not just documented: the Helm `beat` Deployment hard-codes `replicas: 1` (not a `values.yaml`-tunable field), and `docker-compose.{dev,prod}.yml` define `beat` as a single service with no scale directive. Worker (task-EXECUTING) replicas may scale to N freely — M4's idempotency makes concurrent/duplicate task execution safe, so worker scaling carries no beat-singleton constraint.
  - M8 (same-image, different-command deployment): `worker` and `beat` are two NEW deployment units built from the SAME gateway container image as today's `gateway`/`envoy` units, differing only in the container `command` (`celery -A gateway.worker.celery_app worker --loglevel=info` / `... beat --loglevel=info`). No new Dockerfile, no new image build step.
  - M9 (broker/backend db isolation): Celery's `broker_url`/`result_backend` reuse `Settings.redis_url`'s host/port/auth but override the db index with a NEW dedicated knob (default distinct from `/0`) — Celery's own key traffic (task queue, results, beat's schedule lock) never shares a db with the existing cache/rate-limit keys (§0 ground fact: cross-db-0 contamination is a recorded project gotcha).
  - M10 (test without a live broker): the unit suite (`./tests/`) exercises `_refresh_async` / the provider resolver / `refresh_catalog`'s body directly — either an `await`ed direct call to `_refresh_async`, or the Celery task invoked through a `task_always_eager=True` test app — against the test DB fixture. No test in the default unit-suite run requires a running Redis broker or a live worker/beat process.
</must>
Reject:
<reject>
  - `refresh_catalog(provider)` called with a provider absent from `PROVIDER_SOURCES` (e.g. "minimax", "bedrock", a typo) -> "ERR_CATALOG_PROVIDER_UNSUPPORTED" — raised as `CatalogProviderUnsupportedError` BEFORE any engine/session/http client is constructed (fail fast, no wasted connection per M1); this exception is deliberately NOT in `autoretry_for` (M4) — it is a caller/config bug, not a transient failure, so Celery marks the task FAILURE immediately rather than burning the retry budget on a condition retrying can never fix.
  - The OpenRouter fetch fails (`CatalogSourceUnavailableError` / `httpx.TimeoutException` / `httpx.NetworkError` / `httpx.HTTPStatusError`) on every attempt through `max_retries` -> "ERR_UPSTREAM_UNAVAILABLE" — the task ends in Celery's terminal FAILURE state (dead-lettered; no further auto-retry), logged with the SAME code string `core/error_catalog.py:CATALOG_UPSTREAM_UNAVAILABLE.code` the manual `/admin/catalog/sync` 502 already uses (one grep/alert signal for both paths) — there is no HTTP response here, so no new wire-level `ErrorSpec` is added, only a task-domain failure. Per M5, the catalog is left EXACTLY as it was before the run — zero row change.
</reject>
After:
<after>
  - A successful scheduled `refresh_catalog("openrouter")` run leaves `models`/`pricing_snapshots` rows for `provider="openrouter"` reflecting the latest OpenRouter fetch — same upsert + deactivation semantics as today's manual `POST /admin/catalog/sync` (§0, B1-frozen) — while minimax/bedrock/vertex/openai rows are byte-for-byte UNTOUCHED (provider-scoped deactivation, inherited from B1, never re-derived here).
  - `worker` and `beat` are running deployment units in `infra/docker-compose.dev.yml`, `infra/docker-compose.prod.yml`, and the Helm chart (`charts/ai-proxy/templates/`), sharing the gateway image; exactly one `beat` replica is running cluster-wide.
  - The default unit-test run (`./tests/`) is fully green with no live Redis broker and no running worker/beat process.
  - NAMED GAP (not silently ignored, not closed by this task — milestone OUT boundary is "dynamic price fetching cadence/infra beyond the worker skeleton"): a Celery-triggered sync does NOT call the running API pods' in-memory `provider_resolver.refresh()` the way the manual sync router does as a fail-safe (§0 `catalog/api/router.py:sync_catalog`) — a Celery-driven catalog change can be briefly stale in a live gateway pod's model→provider cache until that pod's own resolver TTL/next refresh. Flagged for a follow-up task, not solved here.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Beat schedule default-ON vs default-OFF (`GATEWAY_CATALOG_REFRESH_INTERVAL_SECONDS` default value) — lowest confidence because the codebase's OWN precedent splits both ways, not uniformly: purely-internal DB-maintenance sweeps default ON (`retention_check_interval_seconds=86400`, `credits_recovery_sweep_interval_seconds=60`, `invoice_generation_interval_seconds=3600`), while sweeps that call an EXTERNAL upstream provider default OFF (`openrouter_recovery_sweep_interval_seconds=0`, `compliance_report_schedule_interval_seconds=0`). `refresh_catalog("openrouter")` calls OUT to the live OpenRouter API — the SAME external upstream `openrouter_recovery_sweep_interval_seconds` already guards default-OFF — which is the closer precedent than the internal-maintenance group. Recommendation: default-OFF (`0` = no beat entry registered, opt-in), matching that closer precedent and avoiding a surprise first-deploy outbound call to OpenRouter before an operator has vetted credentials/egress. If wrong (should default ON): a fresh deploy's catalog silently never auto-refreshes until an operator notices and sets the knob — degraded but non-breaking (B1's seed migration still serves a correct-at-seed-time catalog, and the manual `/admin/catalog/sync` route still works). If default-OFF is the WRONG call and Tin wants ON: reversing costs one Settings default edit. THIS IS THE FREEZE DECISION — Tin decides at §3 freeze.
  - [ ] Celery-app module path: proposed `apps/gateway/src/gateway/worker/celery_app.py` (app instance) + `apps/gateway/src/gateway/worker/tasks.py` (the task + resolver) — a NEW top-level bounded-context-shaped dir sitting beside `catalog/`, `tenants/`, etc., but cross-cutting (infra, not a domain) rather than following the domain/application/infrastructure/api split those contexts use, since there is no domain/API surface here. Confirm the path + the "no domain/application split for `worker/`" shape at freeze.
  - [ ] Dedicated Redis db number for the Celery broker/backend — proposed `GATEWAY_CELERY_BROKER_DB` (int, default `3`); no other Settings knob or code path in the repo selects a Redis db index today (the only `redis_url` consumer is the cache/rate-limit path on the URL's own db, `/0`) — no found collision, but this is a new knob category worth Tin's eyes at freeze.
  - [ ] If default-OFF (the ⚠ above) is confirmed, the beat INTERVAL magnitude once an operator opts in — proposed `3600` (hourly, matching `invoice_generation_interval_seconds`'s cadence class) as a documented starting suggestion, since OpenRouter's model list changes rarely; this is a low-stakes freeze-time detail riding on the ⚠ decision, not independently risky.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: task disposes its own engine/client on success   # M1
  Given _refresh_async("openrouter") is invoked directly (no FastAPI app.state involved)
  When the run completes successfully
  Then a create_async_engine + async_sessionmaker + httpx.AsyncClient were constructed INSIDE the call
  And engine.dispose() and client.aclose() were both called before the coroutine returns
  And repeating the call N times leaves no growth in open connections (pool not leaked)

Scenario: task disposes its own engine/client even on failure   # M1, M5
  Given _refresh_async("openrouter") is invoked with a source that raises CatalogSourceUnavailableError mid-fetch
  When the call raises
  Then engine.dispose() and client.aclose() were still called (finally path)
  And no models/pricing_snapshots row was written

Scenario: provider resolver maps openrouter to the live composite source   # M2
  Given refresh_catalog("openrouter") is invoked
  When the provider resolver looks up "openrouter"
  Then it constructs CompositeCatalogSource(primary=OpenRouterCatalogSource(client)) — the same shape main.py wires at boot
  And SyncCatalogUseCase.execute() is called with that source

Scenario: scheduled refresh reuses SyncCatalogUseCase verbatim, updates only openrouter rows   # M3, A1
  Given a DB seeded with active minimax/bedrock/vertex/openai/openrouter rows (B1 baseline)
  When refresh_catalog("openrouter") runs against a stubbed OpenRouterCatalogSource returning a smaller model list than currently active
  Then the openrouter rows absent from the new fetch become active=false (provider-scoped deactivation, B1-frozen predicate)
  And every minimax/bedrock/vertex/openai row's active flag is unchanged
  And the returned int equals the count SyncCatalogUseCase.execute() itself would return

Scenario: transient upstream failure autoretries with backoff, then succeeds   # M4
  Given a stubbed OpenRouterCatalogSource that raises httpx.TimeoutException on the first call and succeeds on the second
  When refresh_catalog("openrouter") runs
  Then Celery's autoretry_for fires exactly one retry with a backoff delay
  And the second attempt's success is byte-identical to a first-attempt success (same upsert outcome)

Scenario: no partial write across a mid-transaction failure   # M5
  Given a stubbed CatalogSource whose embeddings fetch fails after the chat fetch already succeeded
  When refresh_catalog("openrouter") runs
  Then the chat-catalog write still commits (sync_catalog's degrade-to-embedding_models=None path, inherited from SyncCatalogUseCase)
  And no row is left half-written — the chat upsert is fully committed or the embeddings-only failure never touches a partially-applied embeddings row

Scenario: beat schedules only openrouter, at the configured interval   # M6
  Given GATEWAY_CATALOG_REFRESH_INTERVAL_SECONDS is set to a positive value
  When the Celery app builds its beat_schedule
  Then exactly one entry exists, task="gateway.worker.tasks.refresh_catalog", args=("openrouter",), schedule=<the configured interval>
  And no entry exists for minimax/bedrock/vertex/openai

Scenario: interval=0 registers no beat schedule entry at all (default-OFF boundary)   # M6, ⚠ assumption
  Given GATEWAY_CATALOG_REFRESH_INTERVAL_SECONDS=0 (proposed default)
  When the Celery app builds its beat_schedule
  Then beat_schedule is empty — no refresh_catalog entry is registered
  And the task remains manually invocable (celery call / a future admin trigger) even though nothing schedules it automatically

Scenario: beat runs as a structural singleton across a rollout   # M7 (deployment/conceptual)
  Given the Helm chart's beat-deployment.yaml
  When the manifest is rendered for any values.yaml input
  Then replicas is hard-coded to 1 (not derived from a values.yaml replicaCount field for this Deployment)
  And docker-compose.prod.yml's beat service carries no scale/replica directive
  And the worker Deployment/service, by contrast, DOES expose a replica count (safe to scale per M4's idempotency)

Scenario: two beat replicas both fire (misconfiguration despite M7) is a tolerated race, not an app-level rejection   # edge case — concurrency
  Given (a deliberately misconfigured cluster) two beat processes both fire refresh_catalog("openrouter") near-simultaneously
  When both task executions run sync_catalog concurrently
  Then the DB ends up consistent (ON CONFLICT upsert, idempotent per M4) — no duplicate rows, no crash
  And this is documented as operationally wasteful, not as a condition the task itself detects or rejects — M7's replicas:1 is the actual prevention, not a runtime guard

Scenario: worker and beat are the same image, different command   # M8
  Given the gateway container image built for this release
  When infra/docker-compose.prod.yml's worker and beat services are inspected
  Then both reference the SAME image as the gateway/dashboard services
  And their command differs only in the trailing "worker" vs "beat" celery subcommand

Scenario: Celery broker/backend use a dedicated Redis db, not db 0   # M9
  Given Settings.redis_url="redis://localhost:6380/0" and GATEWAY_CELERY_BROKER_DB=3 (proposed default)
  When the Celery app constructs its broker_url/result_backend
  Then both resolve to db index 3 on the same host:port as redis_url
  And no Celery key (task queue, result, beat lock) is ever written under db 0

Scenario: unit suite runs green with no live broker   # M10
  Given the default `./tests/` run (no Redis broker, no worker/beat process started)
  When test_worker_tasks / test_celery_app (or equivalent) run
  Then _refresh_async is exercised via a direct await or task_always_eager=True app
  And the suite passes without a ConnectionError to any Redis broker

Scenario: unknown provider is rejected before any resource is constructed   # R1
  Given refresh_catalog("minimax") is invoked (minimax has no live source per §0)
  When the provider resolver looks up "minimax"
  Then CatalogProviderUnsupportedError is raised (ERR_CATALOG_PROVIDER_UNSUPPORTED) BEFORE create_async_engine/httpx.AsyncClient are constructed
  And the task is NOT retried (not in autoretry_for) — Celery marks it FAILURE immediately
  And no models/pricing_snapshots row is touched

Scenario: exhausted retries dead-letter without a partial write   # R2
  Given a stubbed OpenRouterCatalogSource that raises httpx.NetworkError on every attempt
  When refresh_catalog("openrouter") runs and exhausts max_retries
  Then the task ends in Celery's terminal FAILURE state, logged with CATALOG_UPSTREAM_UNAVAILABLE.code ("ERR_UPSTREAM_UNAVAILABLE")
  And every models/pricing_snapshots row is BYTE-IDENTICAL to its pre-run state — zero row change
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
CELERY APP   apps/gateway/src/gateway/worker/celery_app.py
  Celery("gateway", broker=<redis host:port from Settings.redis_url>/<celery_broker_db>,
                     backend=<same host:port>/<celery_broker_db>)
  beat_schedule (built at process start from Settings, NOT hardcoded):
    catalog_refresh_interval_seconds == 0  -> beat_schedule = {}  (no entry; default-OFF path, §1 ⚠)
    catalog_refresh_interval_seconds  > 0  -> beat_schedule = {
        "refresh-openrouter-catalog": {
            "task": "gateway.worker.tasks.refresh_catalog",
            "schedule": <catalog_refresh_interval_seconds>,
            "args": ("openrouter",),
        }
    }
    -- mirrors the interval-only OFF-sentinel convention already used by
       GATEWAY_OPENROUTER_RECOVERY_SWEEP_INTERVAL_SECONDS / GATEWAY_COMPLIANCE_REPORT_SCHEDULE_INTERVAL_SECONDS
       (core/config.py) — deliberately NO separate GATEWAY_CELERY_ENABLED bool.

CELERY TASK  gateway.worker.tasks.refresh_catalog(provider: str) -> int
  bind=True
  autoretry_for=(CatalogSourceUnavailableError, httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)
  retry_backoff=True, retry_backoff_max=<cap, e.g. 300s>, max_retries=<finite, e.g. 5>
  success -> int   (models processed; == SyncCatalogUseCase.execute()'s own return value, unmodified)
  failure — provider not in PROVIDER_SOURCES -> raises CatalogProviderUnsupportedError
      BEFORE any engine/session/http client is constructed (fail fast); NOT in autoretry_for
      -> Celery task state FAILURE immediately; logged code "ERR_CATALOG_PROVIDER_UNSUPPORTED"
  failure — upstream unreachable through max_retries -> Celery task state FAILURE (dead-lettered);
      logged code "ERR_UPSTREAM_UNAVAILABLE" (reuses core/error_catalog.py's
      CATALOG_UPSTREAM_UNAVAILABLE.code string — same grep/alert signal as the manual
      /admin/catalog/sync 502; no new wire-level ErrorSpec — this is a task-domain failure,
      there is no HTTP caller here)

  internal shape (apps/gateway/src/gateway/worker/tasks.py):
    PROVIDER_SOURCES: dict[str, Callable[[httpx.AsyncClient], CatalogSource]] = {
        "openrouter": lambda client: CompositeCatalogSource(primary=OpenRouterCatalogSource(client)),
    }   # unknown key -> CatalogProviderUnsupportedError, resolved BEFORE any IO resource opens

    async def _refresh_async(provider: str) -> int:
        if provider not in PROVIDER_SOURCES:
            raise CatalogProviderUnsupportedError(provider)
        engine = create_async_engine(settings.database_url)
        try:
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            async with httpx.AsyncClient() as client, sessionmaker() as session:
                source = PROVIDER_SOURCES[provider](client)
                use_case = SyncCatalogUseCase(source, SqlAlchemyCatalogRepository(session))
                return await use_case.execute()
        finally:
            await engine.dispose()

    @celery_app.task(bind=True, autoretry_for=(...), retry_backoff=True, ...)
    def refresh_catalog(self, provider: str) -> int:
        return asyncio.run(_refresh_async(provider))

New domain error — apps/gateway/src/gateway/catalog/domain/errors.py (sibling of the existing
  CatalogSourceUnavailableError(CatalogError)):
    class CatalogProviderUnsupportedError(CatalogError): ...

Settings (apps/gateway/src/gateway/core/config.py) — new GATEWAY_-prefixed knobs:
  celery_broker_db: int = Field(default=3)
      # GATEWAY_CELERY_BROKER_DB — dedicated Redis db INDEX for Celery broker+result-backend.
      # Host/port/auth still come from Settings.redis_url; only the db index is overridden, so
      # Celery's task-queue/result/beat-lock keys never collide with the existing cache/rate-limit
      # keys on db 0 (§0 ground fact).
  catalog_refresh_interval_seconds: int = Field(default=3600, ge=0)
      # GATEWAY_CATALOG_REFRESH_INTERVAL_SECONDS — FREEZE DECISION (Tin 2026-07-16): default 3600
      # (hourly, default-ON) — overrides the draft's default-OFF recommendation; Tin chose to match
      # the internal-maintenance sweep group (retention/invoice/credits default-ON) so a fresh deploy
      # keeps its catalog current with zero operator action. 0 = no beat entry registered (opt-OUT
      # still available); >0 = seconds between scheduled refresh_catalog("openrouter") runs.
      # Single-knob interval-sentinel convention (no separate *_ENABLED bool).
      # NOTE (design-for-failure consequence of default-ON): the worker/beat make an outbound
      # OpenRouter API call ~hourly starting at first deploy — M4's capped autoretry + M5's
      # no-partial-write bound the blast radius of an unreachable/misconfigured upstream.

Deployment units — SAME gateway container image as today's gateway/dashboard/envoy units, command-only diff:
  infra/docker-compose.dev.yml / infra/docker-compose.prod.yml:
    worker:  command: celery -A gateway.worker.celery_app worker --loglevel=info   (replica-safe, M4)
    beat:    command: celery -A gateway.worker.celery_app beat --loglevel=info     (exactly 1; no scale directive)
    both:    env GATEWAY_DATABASE_URL / GATEWAY_REDIS_URL / GATEWAY_CELERY_BROKER_DB /
                 GATEWAY_CATALOG_REFRESH_INTERVAL_SECONDS (beat only needs the interval; worker needs none extra)
  charts/ai-proxy/templates/worker-deployment.yaml — replicaCount: <values.yaml-tunable, mirrors gateway-deployment.yaml>
  charts/ai-proxy/templates/beat-deployment.yaml   — replicas: 1  (HARD-CODED — not a values.yaml field;
                                                        structurally prevents the N-beats-N-schedules failure mode)

Schema: no new tables/columns. Writes flow through the EXISTING, UNCHANGED
  `SqlAlchemyCatalogRepository.sync_catalog` (B1's provider-scoped predicate, already frozen) against
  the EXISTING `models` / `pricing_snapshots` tables.
Access pattern: one new async engine + session + httpx client PER TASK RUN — created fresh at the
  start of `_refresh_async`, disposed in `finally` before it returns — never a long-lived pool shared
  across runs or with the FastAPI app's own `app.state.engine`/`app.state.sessionmaker` (M1).
```

Glossary deltas:
  - `refresh_catalog`: the provider-parameterized Celery task wrapping `SyncCatalogUseCase.execute()`
    for a scheduled/worker-process context (as opposed to the request-scoped manual sync route).
  - `PROVIDER_SOURCES`: the provider-string → live-`CatalogSource`-factory resolver map; a provider
    key absent from this map is UNSUPPORTED (a contracted rejection, R1), never a silent no-op.
  - `CatalogProviderUnsupportedError`: new domain error (sibling of `CatalogSourceUnavailableError`)
    raised when `refresh_catalog`/`_refresh_async` is invoked for a provider with no live source.

Least-sure flag surfaced at freeze: [contract] `GATEWAY_CATALOG_REFRESH_INTERVAL_SECONDS` default ON vs OFF (§1 ⚠) — the draft recommended default-OFF (`0`, opt-in) to match the closest same-upstream precedent (`openrouter_recovery_sweep_interval_seconds`, also OpenRouter-calling, defaults OFF); the counter-precedent is the internal-maintenance-sweep group (retention/invoice/credits) which defaults ON. If default-ON is wrong: an ~hourly outbound OpenRouter call fires from first deploy before creds/egress are vetted (M4 autoretry + M5 no-partial-write bound the blast radius). If default-OFF is wrong: a fresh deploy's catalog never auto-refreshes until an operator sets the knob (non-breaking — B1 seed + manual sync still serve). Reversing either costs one Settings default edit. RESOLVED by Tin 2026-07-16 → **default-ON, 3600s (hourly)**; the two low-stakes confirmations (module path `gateway/worker/`; Redis broker db=3) approved as-proposed.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: yes

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% line coverage on `apps/gateway/src/gateway/worker/` (new module); no
decrease to the repo-wide `--cov-fail-under=80` gate.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_refresh_async_disposes_engine_and_client_on_success: arrange stub openrouter source /
    act `_refresh_async("openrouter")` x3 / assert dispose+aclose fire once per call, no growth ·
    covers: M1
  - test_refresh_async_disposes_on_failure_and_writes_nothing: arrange stub raising
    CatalogSourceUnavailableError mid-fetch / act `_refresh_async` raises / assert dispose+aclose
    still fired (finally) + zero ModelRow written · covers: M1, M5
  - test_provider_resolver_maps_openrouter_to_composite_source: arrange real httpx.AsyncClient /
    act `PROVIDER_SOURCES["openrouter"](client)` / assert CompositeCatalogSource wrapping
    OpenRouterCatalogSource(client) — byte-identical to main.py's wiring · covers: M2
  - test_refresh_async_provider_scoped_deactivation_leaves_other_providers_untouched: arrange DB
    seeded with openrouter+minimax+bedrock+vertex+openai active rows / act
    `_refresh_async("openrouter")` against a SMALLER stubbed fetch / assert absent openrouter row
    deactivated, all 4 other providers' rows unchanged, return == len(models) · covers: M3
  - test_refresh_async_embeddings_failure_degrades_chat_still_commits: arrange stub whose
    embeddings fetch raises CatalogSourceUnavailableError after chat succeeds / act
    `_refresh_async` / assert chat row commits (embedding_models=None degrade), no exception ·
    covers: M5
  - test_refresh_async_unsupported_provider_rejected_before_resources: arrange no stub (real
    resolver) / act `_refresh_async("minimax")` / assert CatalogProviderUnsupportedError raised
    AND zero engine/client constructed (fail-fast ordering) · covers: R1
  - test_refresh_catalog_transient_failure_autoretries_then_succeeds: arrange eager-mode celery +
    factory failing once (httpx.TimeoutException) then succeeding / act
    `refresh_catalog.apply(("openrouter",))` / assert success, factory called exactly twice ·
    covers: M4
  - test_refresh_catalog_exhausted_retries_dead_letters_no_partial_write: arrange eager-mode
    celery + factory always raising httpx.NetworkError + a pre-seeded row / act `refresh_catalog
    .apply(("openrouter",))` / assert Celery FAILURE (not raised past the task boundary), attempt
    count == max_retries+1, seeded row byte-identical after · covers: R2, M5
  - test_refresh_catalog_unsupported_provider_fails_immediately_not_retried: arrange eager-mode
    celery / act `refresh_catalog.apply(("minimax",))` / assert FAILURE with
    CatalogProviderUnsupportedError, exception NOT in autoretry_for · covers: R1 (task-level)
  - test_beat_schedule_positive_interval_schedules_openrouter_only /
    test_beat_schedule_zero_interval_is_empty / test_celery_app_beat_schedule_matches_settings_at_import:
    act `_build_beat_schedule(n)` (pure fn) + the real module-level `celery_app.conf.beat_schedule`
    / assert exactly one openrouter-only entry when >0, `{}` when ==0 · covers: M6
  - test_broker_and_backend_use_dedicated_redis_db_not_zero / test_redis_url_with_db_swaps_only_the_trailing_db_index:
    assert `celery_app.conf.broker_url`/`result_backend` end in `/{celery_broker_db}`, never `/0`
    · covers: M9
  - test_importing_celery_app_does_not_touch_a_live_broker: act import the module / assert no
    raise (lazy construction, no broker required) · covers: M10
  - test_settings_new_knobs_have_frozen_defaults / test_catalog_refresh_interval_seconds_rejects_negative:
    assert `celery_broker_db==3`, `catalog_refresh_interval_seconds==3600`, negative rejected ·
    covers: §3 CONTRACT Settings block
  - test_prod_compose_worker_and_beat_share_the_gateway_image /
    test_prod_compose_worker_and_beat_commands_differ_only_in_subcommand /
    test_dev_compose_defines_worker_and_beat: parse compose YAML / assert same image, command
    diffs only in trailing subcommand · covers: M8
  - test_prod_compose_beat_has_no_scale_directive / test_helm_beat_deployment_hardcodes_replicas_one:
    assert no `deploy.replicas` in compose beat service; Helm beat template has a literal
    `replicas: 1` with no `.Values` reference on that line · covers: M7
  - test_helm_worker_deployment_replicas_is_values_tunable /
    test_helm_worker_and_beat_use_the_same_gateway_image /
    test_helm_worker_and_beat_commands_differ_only_in_subcommand: parse Helm template text /
    assert worker replicas line references `.Values`, both templates reuse gateway's own
    `.Values.image.*` line, commands differ only in `"worker"`/`"beat"` · covers: M7, M8
</test_plan>

Tests live in: `apps/gateway/tests/catalog_celery_refresh/` (4 files: test_refresh_async.py,
test_celery_task.py, test_celery_app.py, test_deployment_units.py) · ran RED before Build —
`ImportError: cannot import name 'CatalogProviderUnsupportedError'` (test_refresh_async.py,
test_celery_task.py) and `ModuleNotFoundError: No module named 'gateway.worker'`
(test_celery_app.py, test_deployment_units.py, via the local conftest's autouse fixture) — both
missing-implementation reasons, not a broken harness.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./tests/` `apps/gateway/src/gateway/worker/` `apps/gateway/src/gateway/catalog/domain/errors.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/pyproject.toml` `infra/docker-compose.dev.yml` `infra/docker-compose.prod.yml` `charts/ai-proxy/templates/`
  (`worker/` is a NEW directory token — covers its whole subtree: `celery_app.py`, `tasks.py`, and
  any `__init__.py`. `apps/gateway/src/gateway/main.py` is DELIBERATELY NOT in scope — the worker is
  a fully separate process entrypoint that constructs its own engine/session/client per M1; it does
  not read or extend `create_app`'s lifespan wiring, so no main.py edit is anticipated. If BUILD finds
  a genuine need to touch main.py, that is a scope surprise to flag, not silently absorbed.)

Strategy (ordered batches):
  1. `catalog/domain/errors.py` — add `CatalogProviderUnsupportedError(CatalogError)` (pure additive
     sibling of `CatalogSourceUnavailableError`, no dependency on anything else in this batch list).
  2. `core/config.py` — add `celery_broker_db` + `catalog_refresh_interval_seconds` Settings fields,
     mirroring the file's own negative-value-coercion / interval-OFF-sentinel conventions already
     used throughout (e.g. `_coerce_negative_max_concurrent`, `openrouter_recovery_sweep_interval_seconds`).
  3. `pyproject.toml` — add `celery[redis]` to `[project].dependencies` (kombu ships as celery's own
     transitive dependency; do not pin it separately unless a real version conflict surfaces).
  4. `apps/gateway/src/gateway/worker/celery_app.py` — the `Celery()` app instance; broker/backend URL
     constructed from `Settings.redis_url` host/port + `celery_broker_db`; `beat_schedule` built
     conditionally on `catalog_refresh_interval_seconds > 0` (§3). Verify the app constructor is LAZY
     (no eager broker connection at import time) before relying on it in tests.
  5. `apps/gateway/src/gateway/worker/tasks.py` — `PROVIDER_SOURCES` resolver, `_refresh_async`,
     `refresh_catalog` task (the resolver lookup happens BEFORE `create_async_engine` — fail-fast
     ordering per R1/M1). Write the "no partial write" / "dispose in finally" plumbing defensively
     first — it is the highest-risk logic in this task (§0's top-flagged async/sync-bridge risk).
  6. Tests (`./tests/`) — direct `await _refresh_async(...)` tests (no broker) for the resolver +
     success/failure/dispose paths first; a `task_always_eager=True` smoke for the Celery task wrapper
     itself second; a Settings-knob test (interval=0 → empty beat_schedule) third. Any test that
     genuinely needs a live Redis broker (e.g. an end-to-end beat-fires-a-task smoke) is marked/skipped
     out of the default unit-suite run, not silently included.
  7. Deployment wiring LAST, only once (1)-(6) are green — `docker-compose.dev.yml` /
     `docker-compose.prod.yml` `worker`+`beat` services, then the 2 new Helm templates
     (`worker-deployment.yaml` with a tunable `replicaCount`, `beat-deployment.yaml` with `replicas: 1`
     hard-coded). Deployment-only changes carry no test coverage of their own — reviewed by inspection
     (M7/M8 scenarios are deployment/conceptual, not suite-asserted).
  The SRE-reliability stance shapes every batch: every new IO resource (engine, session, http client)
  gets an explicit disposal path verified by a test, every retryable exception is retried ONLY because
  idempotency was independently confirmed (not assumed), and the beat-singleton guarantee is enforced
  structurally (hard-coded replicas) rather than left to operator discipline or a comment.

Persona (required): `sre-reliability-engineer` (primary — design-for-failure/timeout/retry/backoff/
  singleton-deployment is the dominant risk surface: `.add/personas/sre-reliability-engineer.md`) with
  `backend-architect` (secondary — `worker/`'s placement as a new cross-cutting infra module beside the
  existing bounded contexts, and the `PROVIDER_SOURCES` resolver's Protocol-shaped `CatalogSource` port,
  per `.add/personas/backend-architect.md`). Note: neither is `flow: design` (both are `flow: build,
  advisor`) — expected, they are the correct personas for the BUILD step this field names. No `flow:
  design` persona in this repo fits a Celery/infra task, so THIS design draft itself used the generic
  domain-analyst/architect stance per the design-agent's own documented fallback rule.
Spawn isolation (default): worktree (this repo's own standing default) — no stated reason to deviate;
  a brand-new dependency (celery/kombu) + a new top-level module is exactly the kind of change that
  benefits from an isolated tree before merging back.
Known-problem fixes:
  - trap: calling `asyncio.run()` from inside an ALREADY-running event loop (e.g. a future test that
    imports the task inside pytest-asyncio's own loop) -> RuntimeError. fix: test code calls
    `await _refresh_async(...)` directly; reserve `asyncio.run()` for the real sync Celery entrypoint
    (`refresh_catalog`) only — never call `refresh_catalog` itself from inside an async test.
  - trap: engine/client leaked across repeated task runs -> Postgres/Redis connection-pool exhaustion
    after N scheduled ticks (a slow, silent production failure). fix: `try/finally: await engine.dispose()`
    (+ `async with httpx.AsyncClient()`) every run, verified by a test asserting dispose() fires on BOTH
    the success and the exception path (§2 scenarios M1).
  - trap: Celery broker/backend silently default to redis_url's own db (`/0`) -> corrupts cache/rate-limit
    keys (§0 recorded gotcha). fix: `celery_broker_db` knob wired into broker_url/result_backend
    construction — never pass the bare `settings.redis_url` straight into `Celery(broker=...)`.
  - trap: naive Helm rollout runs `beat` at the chart's default gateway replicaCount -> N duplicate
    schedules. fix: `beat-deployment.yaml` hard-codes `replicas: 1`, not sourced from `values.yaml`
    (structural prevention, not a comment/convention).
  - trap: unsupported-provider path still opens an engine/session before failing -> wasted connection on
    every misconfigured call. fix: resolver lookup happens BEFORE `create_async_engine` in `_refresh_async`
    (§3's sketch; R1's scenario asserts this ordering).
  - trap: `Celery(...)` eagerly pings its broker at import time -> importing `celery_app.py` in a
    broker-less test environment fails at collection time, not at call time. fix: verify lazy
    construction; if the chosen Celery config is NOT lazy, gate the import behind a fixture that
    supplies a reachable (or `task_always_eager`) broker config for tests.
Strategy actually used: As planned (batches 1→7 in order: domain error → config knobs → celery[redis] dep → celery_app.py → tasks.py → tests → deployment last). ONE deliberate deviation from the stated "worktree" spawn-isolation default: the build ran in the SHARED tree, NOT a worktree — because B1 (catalog-db-seed) + platform-key-default + account-tiers-billing are all UNCOMMITTED, and a worktree branches from stale session-start main ([[worktree-agent-stale-base]]), which would MISS B1's provider-scoped sync_catalog, the new CatalogModel fields, and catalog/domain/errors.py's current shape — the build depends on all of them. Shared-tree was the only correct choice (same reason B1's own build ran shared-tree). All 7 batches landed; the async/sync bridge (asyncio.run + dispose-in-finally) and the openrouter-only resolver were built defensively-first as planned. celery installed cleanly (5.6.3). No scope surprise — main.py was NOT touched by this build (its diff is entirely pre-existing B1/platform-key/billing-owner work).
Safety rule (feature-specific): NO PARTIAL WRITE — the task issues no database write of its own outside
  `SyncCatalogUseCase.execute()` → `sync_catalog`'s single `async with session.begin()` transaction
  (B1-frozen, unchanged here); on ANY exception (resolver, fetch, IO) the task fails before or after
  that boundary, never mid-transaction with a dangling uncommitted write. IDEMPOTENT RETRY: every
  exception in `autoretry_for` is retry-safe ONLY because `sync_catalog` is a pure upsert (ON CONFLICT)
  — this was independently confirmed against B1's ground facts (§0), not assumed from Celery's own
  autoretry convenience.
Code lives in: `apps/gateway/src/gateway/worker/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 25 green in tests/catalog_celery_refresh; 85 green across the catalog blast radius (+ catalog, catalog_db_seed, catalog_sync_trigger, catalog_pricing_fields, minimax_catalog_seed). No live Redis broker required.
- [x] coverage did not decrease — additive new module + new suite; the only shared-file edits are additive (config.py +2 Settings fields, errors.py +1 exception class).
- [x] no test or contract was altered during build — B2's own §3 contract + §4 red suite intact; no sibling test modified (worker/ is a brand-new subsystem, reuses B1's frozen sync_catalog unchanged).
- [x] the green was EARNED — see Refute-read below (self). Tests are DB-backed + non-vacuous: dispose asserted via a monkeypatched dispose_tracker (counts engine.dispose/client.aclose, incl. N-runs-no-growth), provider-scoped deactivation asserted against real seeded rows across 5 providers, no-partial-write asserted by querying ModelRow after a mid-fetch failure, fail-fast asserted by dispose_tracker==0 on the unsupported-provider path. Deployment test parses the real docker-compose.prod.yml/Helm via yaml.safe_load (not a stub).
- [x] concurrency / timing safe — the risky op (network fetch + DB write) reuses SyncCatalogUseCase's existing single `async with session.begin()` (commit-or-rollback atomic); each task run builds+disposes its OWN engine/session/httpx client (no cross-run/cross-loop shared state); autoretry is safe ONLY because sync_catalog is idempotent (ON CONFLICT), independently confirmed against B1 not assumed; beat singleton enforced structurally (helm replicas:1 hard-coded).
- [x] no exposed secrets, injection, or unexpected deps — one intended new dep celery[redis]>=5.4 (Tin-approved); broker URL built by urlsplit/urlunsplit from the existing redis_url (no interpolation of untrusted input); no secret in code.
- [x] layering & dependencies follow CONVENTIONS.md — worker/ is a cross-cutting infra module importing catalog application/infrastructure/domain (correct direction, no domain→infra inversion); reuses the CatalogSource port; celery decorator's untyped-stub issue handled with a scoped pyright-ignore, not a repo-wide config loosening.
- [ ] a person reviewed and approved the change — HELD for Tin (uncommitted; B2 of the model-catalog-db milestone). The contract WAS Tin-approved at freeze (v1, 2026-07-16).

### Build expectations — what "correct" looks like
- [x] A scheduled refresh_catalog("openrouter") updates only openrouter rows, leaves minimax/bedrock/vertex/openai active — confirmed by test_refresh_async_provider_scoped_deactivation_leaves_other_providers_untouched (real DB).
- [x] Each run constructs + disposes its own engine + httpx client on BOTH success and failure paths (no pool leak across ticks) — confirmed by the two dispose tests (dispose_tracker counts 1/1 per run, 3/3 over N runs, and 1/1 even on mid-fetch failure).
- [x] An unsupported provider is rejected BEFORE any engine/client opens — confirmed by test_refresh_async_unsupported_provider_rejected_before_resources (dispose_tracker==0).
- [x] beat_schedule has exactly the openrouter entry when interval>0, empty when interval=0 — confirmed by test_celery_app (pure _build_beat_schedule) + the live import showing beat_schedule=['refresh-openrouter-catalog'] at default 3600.
- [x] Celery broker/backend resolve to the dedicated db (celery_broker_db=3), never db 0 — confirmed by test_celery_app (_redis_url_with_db) — key isolation from cache/rate-limit.
- [x] worker + beat are the same gateway image, different command; beat replicas hard-coded 1 — confirmed by test_deployment_units (yaml.safe_load of the real compose + Helm templates).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `refresh_catalog`/`_refresh_async`/`PROVIDER_SOURCES` all referenced by the celery task decorator + tests; `celery_app` referenced by tasks.py + the compose/helm commands (`celery -A gateway.worker.celery_app`); `celery_broker_db`/`catalog_refresh_interval_seconds` read in celery_app.py; `CatalogProviderUnsupportedError` raised in `_refresh_async` + imported by tests. Confirmed by the 25-test run + `imports=("gateway.worker.tasks",)` registering the task on the app.
- [x] DEAD-CODE — no orphan. Every new symbol has a caller. `_build_beat_schedule`/`_redis_url_with_db` are module-private helpers invoked at app construction (and unit-tested). No unused import (ruff clean).
- [x] SEMANTIC — read tasks.py + celery_app.py + the 4 test files IN FULL (not skimmed): the async/sync bridge disposes on both paths, resolver fail-fast precedes IO, autoretry excludes the config-bug error, beat is openrouter-only + interval-gated, broker db override preserves auth/host. One minor observation (non-blocking): `_redis_url_with_db` drops any query string from redis_url (would matter only for a `rediss://?ssl_...` TLS URL; the default has none) — noted, not a defect for this skeleton.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 cites still resolves — SyncCatalogUseCase, OpenRouterCatalogSource, CompositeCatalogSource, SqlAlchemyCatalogRepository, CatalogSource port, Settings.redis_url/.database_url, create_async_engine/async_sessionmaker, CatalogError base — all resolve (confirmed by the app+celery import smoke + the 85-test run + pyright 0 errors on worker/).
- [x] no anchor moved since Ground SHA 3c27af5 — all B1 anchors (provider-scoped sync_catalog, CatalogModel fields, errors.py) present and unchanged; the worker reuses them without modification.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self · adversarially checked: (1) probed the async/sync bridge for a leak — read `_refresh_async`: engine disposed in `finally` on BOTH paths, httpx client + session closed by `async with`; the dispose tests count 1/1 per run + 3/3 over N runs + 1/1 on failure → no pool growth. (2) probed the provider-scope for a cross-provider leak — the sync it wraps is B1's frozen provider-scoped predicate (already dual-verified in B1); the M3 test proves minimax/bedrock/vertex/openai stay active on an openrouter refresh. (3) probed the resolver for a silent no-op on an unsupported provider — it RAISES CatalogProviderUnsupportedError before any IO (dispose_tracker==0), NOT in autoretry_for. (4) probed the tests for vacuousness — all are DB-backed or parse real deployment yaml; dispose is verified via a real monkeypatched counter, not a mock-return. (5) probed the broker-db override for a db-0 collision — `_redis_url_with_db` forces `/celery_broker_db` (3), unit-tested. No cheat, no overfit found.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: self (B2 is not security-classified — a scheduling wrapper reusing B1's already-dual-verified frozen sync; proportionate to self-verify, unlike B1's migration+billing surface which got an independent agent)
1. Security: CLEAR — new dep celery[redis] is Tin-approved; broker URL built via urlsplit/urlunsplit from the existing trusted redis_url (no untrusted interpolation); no secret in code; the task takes only a provider STRING (validated against a fixed allow-map, unknown → reject) — no injection surface.
2. Concurrency: CLEAR — per-run isolated engine/session/client (no shared mutable state across ticks or the event loop); the DB write stays in SyncCatalogUseCase's existing atomic `session.begin()`; autoretry is idempotent-safe (ON CONFLICT, independently confirmed); duplicate/concurrent worker execution is safe by that idempotency; beat singleton enforced structurally (replicas:1).
3. Architecture: CLEAR — worker/ is a cross-cutting infra module with correct dependency direction (imports catalog app/infra/domain, none import it); reuses the CatalogSource port + SyncCatalogUseCase verbatim (no logic duplication); celery's untyped decorator handled with a scoped ignore, not a repo-wide pyright loosening.
Verdict: PASS
Residue: none blocking. Two named non-blocking follow-ups: (a) the in-memory provider_resolver cache staleness gap (§1 After NAMED GAP — a Celery-driven sync can't refresh live API pods' resolver like the manual route does); (b) `_redis_url_with_db` drops a redis URL query string (only matters for a TLS rediss:// URL). Both out of this skeleton's scope.
Binding: advisory — architecture (B2 sensitivity: architecture/infrastructure, non-security)

### GATE RECORD
Reported: yes — evidence rendered above (25 own + 85 blast-radius tests green; CI ruff/format/pyright clean on B2 lines; refute-read EARNED self; advisor 3-lens PASS)
Outcome: PASS
Reviewed by: self (auto-gate under autonomy:auto — evidence-complete, non-security, contract Tin-approved at freeze) · date: 2026-07-16 · human sign-off HELD for Tin with the milestone bundle

### GATE RECORD — v2 (asyncio sweeper re-verify, after the CHANGE-REQUEST v2 mechanism switch)
Reported: yes. Evidence: **7 own tests green** (tests/catalog_refresh_scheduler/ — predicate, refresh_once
  happy/fail-open/provider-scoped, run_forever survive+cancel, lifespan wired/not-wired) + **80 green across
  the catalog blast radius** (catalog, catalog_db_seed, catalog_sync_trigger, catalog_pricing_fields,
  minimax_catalog_seed, openrouter_recovery_sweep, catalog_refresh_scheduler — the last confirms the main.py
  lifespan edit didn't disturb the sibling sweeper wiring). `ruff check` + `ruff format` clean on the new
  module + suite; `uv run pyright src/gateway/catalog/application/refresh_scheduler.py src/gateway/main.py`
  = **0 errors**. Redis restored to 8.0.1 (venv + uv.lock); all celery artifacts deleted.
Refute-read v2 — EARNED (self): (1) probed fail-open — refresh_once catches CatalogSourceUnavailableError
  AND bare Exception, returns 0, asserted by a test that also proves ZERO rows written on a down source
  (no partial write). (2) probed provider-scope — the M3 test seeds openrouter+minimax, refreshes openrouter
  with a smaller list, asserts openrouter/drop deactivated + minimax untouched (B1's frozen predicate, reused
  verbatim). (3) probed the loop — run_forever survives a first-tick RuntimeError and keeps ticking (≥3),
  cancels cleanly via CancelledError. (4) probed wiring — lifespan starts the task iff interval>0, cancels on
  shutdown (asserted task.cancelled()/done()). (5) probed vacuousness — all DB-backed against real seeded rows,
  not mocks; the wired test swaps in a zero-model FakeCatalogSource so the boot refresh hits no network.
Advisor 3-lens v2 (self — non-security scheduling wrapper): Security CLEAR (no new dep — celery REMOVED;
  no broker; source is app.state-wired, task takes no untrusted input). Concurrency CLEAR (per-tick fresh
  session off the shared sessionmaker; the write stays in sync_catalog's atomic session.begin(); idempotent
  upsert makes a duplicated tick safe; single in-process task, cancelled on shutdown). Architecture CLEAR
  (refresh_scheduler.py is an application-layer service in the catalog bounded context — cleaner than v1's
  cross-cutting worker/ dir; reuses SyncCatalogUseCase + CatalogSource port, no logic duplication; mirrors
  the 6 existing lifespan sweepers exactly).
Outcome: PASS (v2). Reviewed by: self (auto-gate under autonomy:auto — evidence-complete, non-security;
  the MECHANISM change was Tin-directed 2026-07-16, the default-ON 3600 interval unchanged from the v1 freeze).
  date: 2026-07-16 · human sign-off HELD for Tin with the milestone bundle.
Residue: none blocking. The v1 "provider_resolver cache staleness" NAMED GAP still applies (a scheduled sync
  can't refresh a live pod's in-memory model→provider cache) — but now LESS severe: the sweeper runs IN the
  gateway process, so a follow-up can call the local resolver.refresh() directly (no cross-process hop). The
  v1 "_redis_url_with_db drops query string" residue is GONE (no broker URL construction exists anymore).

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: As planned (batches 1→7 in order: domain error → config knobs → celery[redis] dep → celery_app.py → tasks.py → tests → deployment last). ONE deliberate deviation from the stated "worktree" spawn-isolation default: the build ran in the SHARED tree, NOT a worktree — because B1 (catalog-db-seed) + platform-key-default + account-tiers-billing are all UNCOMMITTED, and a worktree branches from stale session-start main ([[worktree-agent-stale-base]]), which would MISS B1's provider-scoped sync_catalog, the new CatalogModel fields, and catalog/domain/errors.py's current shape — the build depends on all of them. Shared-tree was the only correct choice (same reason B1's own build ran shared-tree). All 7 batches landed; the async/sync bridge (asyncio.run + dispose-in-finally) and the openrouter-only resolver were built defensively-first as planned. celery installed cleanly (5.6.3). No scope surprise — main.py was NOT touched by this build (its diff is entirely pre-existing B1/platform-key/billing-owner work).
- [AI] verify — gate PASS (reviewed by self (auto-gate under autonomy:auto — evidence-complete, non-security, contract Tin-approved at freeze))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

