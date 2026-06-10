# TASK: OpenRouter catalog sync, pricing snapshots, marked-up /v1/models

slug: model-catalog · created: 2026-06-10 · stage: mvp · autonomy: auto
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Model catalog — OpenRouter catalog sync, pricing snapshots, marked-up GET /v1/models
Framings weighed: catalog-as-gateway-module (chosen) · cached-proxy pass-through (rejected: no snapshot ledger, can't compute historical cost) · external catalog service (rejected at scope: monorepo MVP, no infra cost)
Must:
<must>
  - POST /internal/catalog/sync fetches the current model list from the CatalogSource port and upserts model rows (id=openrouter model id string, name, context_length, active=true); models absent from the upstream response are marked active=false (soft-delete)
  - Every sync run appends new pricing_snapshot rows for each model whose prompt_usd_per_token or completion_usd_per_token changed since the last snapshot; if prices are unchanged no new snapshot is written (idempotent)
  - pricing_snapshot rows are append-only and immutable: never updated or deleted; the row captured_at is set by the database server clock at insert time
  - GET /v1/models requires a valid Bearer JWT (gateway.tenants token service); it returns the list of active models with prices marked up by the caller's tenant markup_pct: displayed_price = upstream_price × (1 + markup_pct / 100)
  - The markup_pct is stored on the tenants table (additive column, numeric, default 20.0); each tenant can carry a different markup
  - All error responses are RFC 9457 problem+json carrying a machine-readable `code`
</must>
Reject:
<reject>
  - POST /internal/catalog/sync when CatalogSource raises an unreachable/network error -> "ERR_UPSTREAM_UNAVAILABLE" (502)
  - GET /v1/models with a missing, malformed, expired, or wrong-signature Bearer JWT -> "ERR_AUTH_INVALID_TOKEN" (401)
  - GET /v1/models when no sync has ever been run (catalog table is empty) -> "ERR_CATALOG_EMPTY" (409)
</reject>
After:
<after>
  - The models table reflects the current OpenRouter catalog (active flags reconciled); pricing_snapshots holds an immutable ledger of every price change; GET /v1/models returns a tenant-scoped, marked-up list; the tenants table carries a markup_pct column used for price calculation
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The markup_pct column is added to the existing tenants table (TenantRow in gateway/tenants/infrastructure/orm.py) — lowest confidence because this is a cross-module schema touch; if wrong (rejected by a future strict-boundary rule): move markup to a separate catalog_tenant_settings table — migration and repository changes, contract unchanged
  ⚠ POST /internal/catalog/sync has no auth in MVP (Envoy guards /internal/* at the edge) — lowest confidence because if the edge guard is misconfigured any caller can trigger a sync; if wrong: add a static operator token — small change, contained to the router
  - [x] CatalogSource is a typing.Protocol port in the catalog domain; tests use a FakeCatalogSource injected via app.state / dependency override — no real HTTP in tests
  - [x] The most-recent snapshot per model is the one used for markup calculation (MAX captured_at per model_id)
  - [x] context_length may be null/absent in some OpenRouter model entries; stored as nullable integer
  - [x] ERR_CATALOG_EMPTY fires when zero active models exist, not zero rows (a sync that deactivates everything triggers it)
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: sync populates catalog from source
  Given no models exist in the database
  When POST /internal/catalog/sync is called and the FakeCatalogSource returns two models
  Then the response is 200 with {"synced": 2}
  And two model rows exist in the models table with active=true
  And two pricing_snapshot rows exist (one per model)

Scenario: sync is idempotent when prices unchanged
  Given a prior sync has run with model "anthropic/claude-opus-4" at price P
  When POST /internal/catalog/sync is called again with the same model at the same price P
  Then the response is 200 with {"synced": 1}
  And still exactly one pricing_snapshot row exists for "anthropic/claude-opus-4"

Scenario: sync appends a new snapshot when price changes
  Given a prior sync captured a snapshot for "anthropic/claude-opus-4" at price P
  When POST /internal/catalog/sync is called with that model at a new price P2
  Then the response is 200 with {"synced": 1}
  And two pricing_snapshot rows exist for "anthropic/claude-opus-4" (one per price)
  And the latest snapshot reflects price P2

Scenario: sync marks absent models inactive
  Given two models were synced on a prior run
  When POST /internal/catalog/sync returns only one of those two models
  Then the response is 200 with {"synced": 1}
  And the absent model row has active=false
  And the present model row still has active=true

Scenario: sync fails when source is unreachable
  Given the FakeCatalogSource is configured to raise an unreachable error
  When POST /internal/catalog/sync is called
  Then the response is 502 with code "ERR_UPSTREAM_UNAVAILABLE"
  And no model rows and no snapshot rows were written

Scenario: GET /v1/models returns marked-up prices for authenticated tenant
  Given a sync has run and the tenant's markup_pct is 20.0
  When GET /v1/models is called with a valid Bearer JWT for that tenant
  Then the response is 200 with a list of active models
  And each model's prompt_per_token equals the upstream price × 1.20
  And each model's completion_per_token equals the upstream price × 1.20

Scenario: GET /v1/models applies the tenant's own markup_pct
  Given two tenants with markup_pct 10.0 and 50.0 respectively
  And a sync has run with a known upstream price U
  When each tenant calls GET /v1/models with their own JWT
  Then tenant-A sees prices = U × 1.10 and tenant-B sees prices = U × 1.50

Scenario: GET /v1/models with invalid token is rejected
  Given a missing, expired, or wrong-signature token
  When GET /v1/models is called with it
  Then the response is 401 with code "ERR_AUTH_INVALID_TOKEN"
  And no model data is leaked in the response

Scenario: GET /v1/models before any sync returns catalog-empty error
  Given no sync has ever been run (zero active models in the catalog)
  When GET /v1/models is called with a valid Bearer JWT
  Then the response is 409 with code "ERR_CATALOG_EMPTY"
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /internal/catalog/sync   body: (none)
  200 -> { synced: int }   # count of models processed (active+inactive combined)
  502 -> problem+json { code: "ERR_UPSTREAM_UNAVAILABLE" }

GET /v1/models   header: Authorization: Bearer <jwt>
  200 -> {
    object: "list",
    data: [
      {
        id: str,                         # OpenRouter model id, e.g. "anthropic/claude-opus-4"
        name: str,
        context_length: int | null,
        prompt_per_token: float,         # upstream price × (1 + markup_pct/100)
        completion_per_token: float,
        object: "model"
      }
    ]
  }
  401 -> problem+json { code: "ERR_AUTH_INVALID_TOKEN" }
  409 -> problem+json { code: "ERR_CATALOG_EMPTY" }

problem+json shape (RFC 9457, platform-wide):
  { type: "about:blank", title: str, status: int, code: "ERR_*", detail?: str }

Schema (additive, no destructive changes):
  models(id text PK,                      # OpenRouter model id string
         name text NOT NULL,
         context_length int nullable,
         active bool NOT NULL DEFAULT true,
         created_at timestamptz DEFAULT now(),
         updated_at timestamptz DEFAULT now())

  pricing_snapshots(id uuidv7 PK,
                    model_id text NOT NULL REFERENCES models(id),
                    prompt_usd_per_token numeric(20,10) NOT NULL,
                    completion_usd_per_token numeric(20,10) NOT NULL,
                    captured_at timestamptz NOT NULL DEFAULT now())
  -- append-only; no UPDATE or DELETE ever issued against this table

  tenants (existing table — ADDITIVE column only):
    + markup_pct numeric(7,4) NOT NULL DEFAULT 20.0

Domain port (typing.Protocol, zero framework imports):
  CatalogSource.list_models() -> AsyncIterator[CatalogModel]
    # raises CatalogSourceUnavailableError on network/upstream failure

Access patterns:
  sync: SELECT latest snapshot per model → compare prices → INSERT new snapshots;
        UPSERT model rows; mark absent models active=false — all in ONE transaction
  GET /v1/models: JOIN models + latest pricing_snapshot per model_id + tenant.markup_pct;
                  filter active=true; no N+1 (single joined query)
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [spec] markup_pct lives on the existing tenants table (cross-module touch to gateway/tenants/infrastructure/orm.py TenantRow) — lowest confidence because it couples catalog to the tenants module at the schema level; if wrong (strict boundary enforcement needed): introduce a separate catalog_tenant_config table — migration and repository changes, GET /v1/models contract unchanged.
⚠ [contract] POST /internal/catalog/sync returns count of models processed (not just inserted) — lowest confidence because the caller may expect "new rows written" semantics; if wrong: change {"synced": N} to {"upserted": N, "snapshots_added": M} — additive response field, non-breaking.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_sync_populates_catalog: arrange FakeCatalogSource with 2 models / act POST /internal/catalog/sync / assert 200 {"synced":2} + 2 model rows active=true + 2 snapshot rows
  - test_sync_idempotent_when_prices_unchanged: arrange prior sync + same source data / act second POST sync / assert 200 + still exactly 1 snapshot row for the model
  - test_sync_appends_snapshot_on_price_change: arrange prior sync at price P / act POST sync with price P2 / assert 200 + 2 snapshot rows for model + latest has P2
  - test_sync_marks_absent_models_inactive: arrange 2-model sync / act POST sync returning only 1 model / assert absent model active=false, present model active=true
  - test_sync_upstream_unavailable: arrange FakeCatalogSource raising error / act POST sync / assert 502 ERR_UPSTREAM_UNAVAILABLE + zero rows written
  - test_get_models_returns_markedup_prices: arrange sync + tenant with markup 20.0 / act GET /v1/models with tenant JWT / assert 200 + prices = upstream × 1.20
  - test_get_models_respects_per_tenant_markup: arrange 2 tenants markup 10 and 50 + same upstream price / act each GET /v1/models / assert each sees their own markup applied
  - test_get_models_invalid_token_rejected: act GET /v1/models with missing/expired/wrong-sig tokens / assert 401 ERR_AUTH_INVALID_TOKEN each, no model data leaked
  - test_get_models_before_sync_returns_catalog_empty: arrange zero active models / act GET /v1/models / assert 409 ERR_CATALOG_EMPTY
</test_plan>

Tests live in: `apps/gateway/tests/catalog/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): sync upsert + snapshot inserts + active=false updates happen in ONE database transaction — a source fetch failure must not leave partial rows; pricing_snapshots are never UPDATEd or DELETEd; the markup_pct column on tenants is additive (Alembic migration, no existing rows affected — default 20.0 covers all pre-existing tenants); cross-module touch (tenants ORM) noted here and in §3 Schema — the catalog module READS markup_pct via a read-only query, it does NOT own the tenants table.
Code lives in: `apps/gateway/src/gateway/` (new module `catalog/` with `domain/`, `application/`, `infrastructure/`, `api/` layers; cross-module read of `tenants` table for markup_pct is acceptable in the infrastructure layer only)
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

Safety rule IMPLEMENTED: `SqlAlchemyCatalogRepository.sync_catalog` wraps all writes (upsert + snapshot insert + active=false) in a single `async with self._session.begin()` block — any exception inside rolls back the entire transaction, leaving no partial rows. `PricingSnapshotRow` rows are added via `session.add()` only (never `session.execute(update(...))` or `DELETE`). `markup_pct Numeric(7,4) server_default='20.0'` added to `TenantRow` — additive only, all existing rows get 20.0 automatically.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 15 passed (tests/catalog/), 14 regression (tenants+health+config), 29 total
- [x] coverage did not decrease — 86.96% total vs 80% floor (catalog module new; openrouter_source intentionally low at 32% — the real HTTP adapter is never exercised in tests by design; all other catalog modules at 84-100%)
- [x] no test or contract was altered during build — only `pyproject.toml` per-file-ignores extended to suppress pre-existing S107/RUF002 in the frozen test file (no logic change); contract untouched
- [x] concurrency / timing of the risky operation is safe — all sync writes inside ONE `session.begin()` transaction; no concurrent write conflict risk for MVP single-instance deployment; snapshot idempotency checked by bulk price-fetch before any insert (within the same transaction, so no TOCTOU gap)
- [x] no exposed secrets, injection openings, or unexpected dependencies — ORM-parameterised queries only (pg_insert, select, update via SQLAlchemy); no raw string interpolation into SQL; no new packages added (httpx was already present); OpenRouter source never called in tests (injected via app.state); markup_pct is read-only by the catalog module
- [x] layering & dependencies follow CONVENTIONS.md — clean architecture: domain (zero framework imports, typing.Protocol ports) <- application (use cases) <- infrastructure (SQLAlchemy + httpx adapters) <- api (FastAPI routers/deps/schemas); composition root in main.create_app; catalog reads tenants.markup_pct only in infrastructure layer
- [x] a person reviewed and approved the change — delegated auto mode (standing approval, Tin Dang 2026-06-10); bundle + evidence reported in chat

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: domain entities/ports/errors <- application use_cases <- infrastructure repository/openrouter_source <- api deps/router <- main.create_app (internal_catalog_router + catalog_router registered; catalog_source wired on app.state); TenantRow.markup_pct referenced by SqlAlchemyCatalogRepository.list_active_models_with_markup; confirmed via 86.96% coverage (all catalog modules imported and exercised except the real HTTP path which is intentionally injection-excluded)
- [x] DEAD-CODE (code) — CatalogRepository.get_latest_snapshot_prices Protocol method is defined but the concrete implementation delegates to _fetch_latest_prices (bulk path) instead; get_latest_snapshot_prices remains on the port for future per-model queries — not dead, just not exercised by current use cases; ruff F401/F841 clean across all 38 mypy-checked source files
- [x] SEMANTIC (prose / non-code) — TASK.md §1-§4 read in full; contract FROZEN @ v1 confirmed; §3 schema shape matched exactly in ORM (models table text PK, pricing_snapshots uuidv7 PK append-only, tenants markup_pct additive); all Must/Reject rules satisfied by test evidence

### GATE RECORD
Outcome: PASS  (auto-resolved under autonomy: auto — evidence complete: 15 catalog tests green · 14 regression tests green · 86.96% coverage above 80% floor · ruff check+format clean · mypy strict 0 errors · no contract edit · no test weakened · no security residue; loops dry)
Reviewed by: Claude (Sonnet 4.6) under delegated auto mode, standing approval Tin Dang · date: 2026-06-10

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): sync error rate (ERR_UPSTREAM_UNAVAILABLE) · /v1/models 409 rate (catalog never synced signal) · p99 sync latency (OpenRouter fetch cost) · snapshot table row growth (pricing volatility signal)
Spec delta for the next loop: the DISTINCT ON subquery pattern for latest-per-model works correctly in PostgreSQL but is not ANSI SQL — if the DB ever changes, replace with a window function (ROW_NUMBER OVER PARTITION BY model_id ORDER BY captured_at DESC); the markup multiplier uses Decimal arithmetic to avoid float rounding on billing-grade prices — maintain this invariant in any future markup computation

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

- [DDD · open] CatalogSource as a typing.Protocol port with FakeCatalogSource injected via app.state decouples all tests from real HTTP — evidence: 15 tests ran without any network call, CatalogSourceUnavailableError path exercised via raise_unavailable flag
- [SDD · open] Single-transaction sync (upsert + snapshot + deactivate) is the correct safety boundary for an append-only ledger — evidence: test_sync_upstream_unavailable confirms zero rows written on source failure; test_sync_idempotent_when_prices_unchanged confirms no duplicate snapshot on re-sync
- [TDD · open] Red suite (15 failures on 404) confirmed before any implementation line was written — evidence: gate check output captured; green achieved in first implementation pass without test edits
- [ADD · open] Pre-existing ruff S107/RUF002 errors in frozen test files require pyproject.toml per-file-ignore extension rather than test edits — architectural decision: suppress at config level to maintain test immutability contract
