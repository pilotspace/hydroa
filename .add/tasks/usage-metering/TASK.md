# TASK: Usage capture incl. streaming, write-behind ledger, marked-up cost

slug: usage-metering · created: 2026-06-10 · stage: mvp · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope: risk: high + autonomy: conservative declared above.
     The engine refuses an unguarded completion (`unguarded_high_risk_auto`, run.md guard). -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Usage metering — capture streaming and non-streaming token usage, compute marked-up cost in Decimal arithmetic, write events to a Redis Stream (write-behind), flush asynchronously to a Postgres append-only ledger, expose totals via an admin API
Framings weighed: write-behind Redis Stream → Postgres ledger (chosen) · synchronous write to Postgres on hot path (rejected: blocks streaming, adds latency on every completion) · Kafka/topic-based pipeline (rejected: operational complexity at MVP; Redis is already in the stack)
Must:
<must>
  - RecordingUsageRecorder implements the UsageRecorder port (gateway/proxy/domain/ports.py, FROZEN) — on record(), resolve the latest pricing snapshot for the model AND the tenant's markup_pct, compute cost_usd = (prompt_tokens × prompt_price + completion_tokens × completion_price) × (1 + markup_pct / 100) using ALL Decimal arithmetic, then push exactly one JSON event to the Redis Stream key `usage:events` via redis.asyncio
  - After pushing the stream event, INCRBYFLOAT the per-tenant-month spend counter `usage:spend:<tenant_id>:<YYYYMM>` by cost_usd (floating-point counter is advisory only — the ledger is the billing source of truth)
  - Usage with usage=None (streaming edge case) OR unknown model pricing → record with prompt_tokens=0, completion_tokens=0, cost_usd=0 BUT the raw payload is stored; the event MUST still be pushed to the Redis Stream — never drop an event
  - RecordingUsageRecorder must NEVER raise into the proxy path — Redis unavailability must NOT fail completions; all failures are swallowed and logged internally (availability-over-metering tradeoff)
  - UsageLedgerFlusher reads the Redis Stream via a consumer group (XGROUP CREATE with MKSTREAM, XREADGROUP, XACK), inserts one row per event into the Postgres table `usage_records` with an idempotent upsert (ON CONFLICT (id) DO NOTHING using the Redis event id as the UUID primary key — at-least-once + idempotency = exactly-once semantic in the ledger)
  - usage_records schema: id uuid PK (= Redis event id), tenant_id uuid NOT NULL, key_id uuid NOT NULL, model_id text NOT NULL, prompt_tokens int NOT NULL DEFAULT 0, completion_tokens int NOT NULL DEFAULT 0, cost_usd numeric(14,8) NOT NULL DEFAULT 0, status int NOT NULL, pricing_snapshot_id uuid NULL, raw jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
  - Ledger is APPEND-ONLY — no UPDATE or DELETE ever issued against usage_records
  - UsageLedgerFlusher.flush_once() is the deterministic entry point for tests; it also runs as a background lifespan task (interval 1s) in main.py — tests never rely on timing
  - The streaming proxy path MUST be extended (additive, cross-module touch) to tee SSE chunks through a usage extractor; the usage module provides extract_usage_from_sse(chunks: list[bytes]) -> dict | None, a pure function parsing `data: {...}` frames for a `usage` key; the recorder receives the final usage dict; forwarded bytes must remain byte-identical; ALL existing proxy tests must stay green unmodified
  - GET /admin/usage (Authorization: Bearer <JWT>) returns the authenticated tenant's ledger totals and the 50 newest records; totals come from the Postgres ledger, NOT the Redis counter
  - All gateway-generated error responses are RFC 9457 problem+json (gateway.core.errors)
  - Clean architecture: gateway/usage/{domain,application,infrastructure,api} mirroring gateway/tenants; domain has zero framework imports
</must>
Reject:
<reject>
  - GET /admin/usage with a missing, malformed, expired, or wrong-signature JWT → "ERR_AUTH_INVALID_TOKEN" (401)
  - GET /admin/usage with a valid JWT for tenant B → returns only tenant B's records (tenant isolation; no tenant A data visible)
</reject>
After:
<after>
  - After a non-streaming completion: exactly one `usage:events` stream entry exists; one row exists in usage_records (after flush_once()); cost_usd equals (prompt_tokens × prompt_price + completion_tokens × completion_price) × (1 + markup_pct/100) in Decimal; pricing_snapshot_id references the snapshot used; spend counter incremented
  - After a streaming completion: SSE bytes forwarded unchanged; usage extracted from frames; one ledger row created with correct cost; existing proxy tests unmodified and green
  - After Redis unavailability during record(): the completion response was 200; no exception escaped to the caller; the failure was logged
  - After duplicate flush of the same event id: still exactly one ledger row (idempotent)
  - After unknown model pricing: one row with tokens 0, cost 0, raw payload stored
  - GET /admin/usage returns totals computed from ledger rows + list of ≤50 newest rows; tenant B sees none of tenant A's rows
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Decimal cost precision truncated when stored in Redis INCRBYFLOAT — lowest confidence because Redis INCRBYFLOAT uses IEEE 754 double-precision float, which cannot represent all Decimal values exactly; if wrong (auditor requires exact counter): replace INCRBYFLOAT with a Lua script storing string-encoded Decimal — the spend counter is explicitly advisory; ledger (Postgres numeric(14,8)) is the billing source of truth; contained tradeoff, no contract change
  ⚠ At-least-once delivery from Redis Stream may produce duplicate events at the flusher boundary during crash-restart — lowest confidence because XREADGROUP + XACK only acknowledges after a successful Postgres INSERT; if the process crashes after INSERT but before XACK the event is re-delivered and the ON CONFLICT DO NOTHING guard makes it idempotent; if wrong (out-of-order event ids collide with a different event): event ids use the Redis auto-generated stream id (millisecond timestamp + sequence) as UUID — extremely low collision probability; contained, no contract change
  - [x] Redis stream consumer group name is "ledger-flusher"; stream key is "usage:events"; these are configuration constants, not settings-file values at MVP
  - [x] The INCRBYFLOAT key `usage:spend:<tenant_id>:<YYYYMM>` has no TTL set at MVP — key accumulates; the budgets task reads it; cleanup deferred to v2
  - [x] pricing_snapshot_id is NULL when model pricing is unknown (cost 0 row); this is valid per schema; no error raised
  - [x] GET /admin/usage is authenticated via the same JWT mechanism as other /admin/* routes; no new auth scheme introduced
  - [x] The usage extractor pure function only needs to parse the final SSE usage frame; it does not need to reassemble partial frames; upstream sends `data: {"usage": {...}}` as a complete SSE event
  - [x] markup_pct is stored on the Tenant ORM row (already exists from model-catalog task); RecordingUsageRecorder reads it via the existing ORM session
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost-if-wrong. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: non-streaming completion produces one ledger row with correct marked-up Decimal cost
  Given tenant "Acme" with markup_pct=20, an active model "openai/gpt-4o" with prompt_price=0.0000025 and completion_price=0.00001, and a RecordingUsageRecorder wired in
  When record() is called with usage={prompt_tokens: 100, completion_tokens: 50}, status=200
  Then flush_once() inserts exactly one row in usage_records
  And cost_usd = (100×0.0000025 + 50×0.00001) × 1.20 = 0.00066 exactly in Decimal
  And pricing_snapshot_id references the snapshot used
  And the spend counter `usage:spend:<tenant_id>:<YYYYMM>` is incremented by 0.00066

Scenario: streaming completion captures usage from SSE frames and prices it
  Given an active model and markup_pct=0 and a FakeCompletionUpstream emitting SSE_CHUNKS including a usage frame
  When POST /v1/chat/completions with stream=true
  Then the SSE bytes forwarded to the client are byte-identical to what the upstream emitted
  And flush_once() inserts one ledger row with the prompt_tokens and completion_tokens extracted from the SSE usage frame
  And cost_usd is computed from the extracted tokens × pricing

Scenario: duplicate flush of same event produces only one ledger row
  Given one usage event already flushed and committed (one row in usage_records)
  When flush_once() is called again with the same event (simulating at-least-once re-delivery)
  Then still exactly one row exists in usage_records with the original values unchanged

Scenario: Redis unavailable — completion still 200, event lost is logged
  Given a RecordingUsageRecorder backed by a fake Redis client that always raises on XADD
  When record() is called
  Then no exception is raised to the caller
  And the failure is logged (swallowed internally)

Scenario: unknown model pricing — cost 0, raw payload stored
  Given a model with no pricing snapshot in the catalog
  When record() is called with that model_id and usage={prompt_tokens: 10, completion_tokens: 5}
  Then flush_once() inserts one row with prompt_tokens=0, completion_tokens=0, cost_usd=0
  And raw jsonb contains the original usage payload
  And pricing_snapshot_id is NULL

Scenario: spend counter incremented by cost
  Given tenant "Acme" with markup_pct=10 and a known model pricing
  When record() is called with usage={prompt_tokens: 200, completion_tokens: 100}
  Then the Redis key `usage:spend:<tenant_id>:<YYYYMM>` holds the correct cost value after INCRBYFLOAT

Scenario: GET /admin/usage returns totals and 50 newest records for authenticated tenant
  Given tenant "Acme" with 3 ledger rows flushed
  When GET /admin/usage with Acme's valid JWT
  Then the response is 200 with total_cost_usd, total_requests=3, total_prompt_tokens, total_completion_tokens, and records list of ≤50 entries
  And each record contains id, model_id, prompt_tokens, completion_tokens, cost_usd, status, created_at

Scenario: GET /admin/usage tenant isolation — tenant B sees none of tenant A's rows
  Given tenant A has 2 flushed rows and tenant B has 1 flushed row
  When tenant B calls GET /admin/usage with their valid JWT
  Then the response records list contains only tenant B's row
  And total_requests is 1

Scenario: GET /admin/usage rejected without JWT
  Given no Authorization header
  When GET /admin/usage
  Then the response is 401 problem+json with code "ERR_AUTH_INVALID_TOKEN"
  And no usage data is present in the response body
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/usage
  header: Authorization: Bearer <jwt>
  200 -> {
    total_cost_usd: str,            # str(Decimal) — exact, no float lossyness
    total_requests: int,
    total_prompt_tokens: int,
    total_completion_tokens: int,
    records: [                      # ≤50 newest, ordered created_at DESC
      {
        id: uuid,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: str,              # str(Decimal)
        status: int,
        created_at: str             # ISO 8601 timestamptz
      }
    ]
  }
  401 -> problem+json { type: "about:blank", title: str, status: 401, code: "ERR_AUTH_INVALID_TOKEN" }

UsageRecorder port (FROZEN, gateway/proxy/domain/ports.py):
  async def record(
    self, *, tenant_id: UUID, key_id: UUID, model: str,
    usage: dict | None, status: int
  ) -> None
  — must not raise; Redis down must not fail callers

Redis write-behind (internal, not user-facing):
  Stream key: "usage:events"
  Consumer group: "ledger-flusher"
  Event fields: tenant_id, key_id, model_id, prompt_tokens, completion_tokens,
                cost_usd (str of Decimal), pricing_snapshot_id (uuid str or ""),
                status, raw (JSON string), created_at (ISO 8601)
  Spend counter key: "usage:spend:<tenant_id>:<YYYYMM>"

Schema:
  usage_records (
    id                  uuid        PRIMARY KEY,         -- = Redis stream event id
    tenant_id           uuid        NOT NULL,
    key_id              uuid        NOT NULL,
    model_id            text        NOT NULL,
    prompt_tokens       int         NOT NULL DEFAULT 0,
    completion_tokens   int         NOT NULL DEFAULT 0,
    cost_usd            numeric(14,8) NOT NULL DEFAULT 0,
    status              int         NOT NULL,
    pricing_snapshot_id uuid        NULL,
    raw                 jsonb       NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
  )
  Constraint: ON CONFLICT (id) DO NOTHING (idempotent ledger insert)
  APPEND-ONLY: no UPDATE or DELETE ever issued

  tenants table: markup_pct numeric(5,2) NOT NULL DEFAULT 0
    (additive column; migration is part of this task's build)

Streaming seam (additive cross-module touch):
  gateway.usage.domain.extractor.extract_usage_from_sse(chunks: list[bytes]) -> dict | None
  — pure function; parses SSE frames for `"usage"` key in the final data event;
    returns dict on success, None if no usage frame found
  — the proxy streaming path (gateway/proxy/application/use_cases.py CompletionUseCase.stream)
    collects chunks via a tee generator, calls extract_usage_from_sse on completion,
    then calls usage_recorder.record() with the extracted usage dict (may be None)
  — forwarded bytes to the client must be byte-identical to upstream output

Module layout:
  gateway/usage/
    domain/
      extractor.py      — extract_usage_from_sse (pure, zero framework imports)
      errors.py         — UsageMeteringError
    application/
      recorder.py       — RecordingUsageRecorder (implements UsageRecorder port)
      flusher.py        — UsageLedgerFlusher (flush_once(), lifespan task)
    infrastructure/
      orm.py            — UsageRecordRow (SQLAlchemy ORM)
      redis_stream.py   — thin async wrapper around redis.asyncio calls
    api/
      router.py         — GET /admin/usage
      deps.py           — dependency injection helpers
      schemas.py        — Pydantic response models
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [spec] Decimal cost truncated in Redis INCRBYFLOAT (IEEE 754 float) — lowest confidence because the spend counter read by the budgets task will accumulate rounding error over many requests; if wrong (auditor requires exact budget enforcement): replace INCRBYFLOAT with a Lua script or store string-encoded Decimal — the counter is advisory; the ledger (Postgres numeric(14,8)) is the billing source of truth; contained change, no API contract impact.
⚠ [contract] markup_pct column added to tenants table as an additive migration — lowest confidence because it touches an existing table owned by the tenant-identity task; if wrong (migration ordering conflict with a parallel task): add markup_pct in a separate Alembic migration version chained after tenant-identity's; contained, no API contract change.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_non_streaming_ledger_row_correct_decimal_cost: arrange tenant markup_pct=20 + model pricing snapshot in DB + Redis flush / act recorder.record(usage={prompt_tokens:100, completion_tokens:50}) + flush_once() / assert exactly one usage_records row; cost_usd == Decimal("0.00066000"); pricing_snapshot_id matches snapshot; spend counter value matches
  - test_streaming_usage_extracted_from_sse_and_priced: arrange api_key + active_model + RecordingUsageRecorder on app.state + flush-capable flusher / act POST /v1/chat/completions stream=true (SSE_CHUNKS include usage frame) / assert bytes byte-identical to SSE_CHUNKS; flush_once() → one row with correct token counts and cost
  - test_duplicate_flush_idempotent: arrange one flushed row / act flush_once() again with the same event re-delivered / assert still exactly one row in usage_records
  - test_redis_unavailable_completion_still_200: arrange FakeRedis that raises on XADD + valid api_key + active_model / act POST /v1/chat/completions / assert response is 200; no exception raised; failure logged (check that log captured or no exception propagated)
  - test_unknown_model_pricing_cost_zero_raw_stored: arrange model with no pricing snapshot + RecordingUsageRecorder / act recorder.record(model="ghost/model", usage={prompt_tokens:10, completion_tokens:5}) + flush_once() / assert one row: tokens=0, cost_usd=0, raw contains original usage, pricing_snapshot_id is NULL
  - test_spend_counter_incremented: arrange tenant markup_pct=10 + model pricing + Redis / act recorder.record(usage={prompt_tokens:200, completion_tokens:100}) / assert Redis key `usage:spend:<tenant_id>:<YYYYMM>` equals correct Decimal cost (float tolerance)
  - test_admin_usage_totals_and_records: arrange tenant + 3 flushed rows via flush_once() / act GET /admin/usage with JWT / assert 200; total_requests=3; total_prompt_tokens sum; total_completion_tokens sum; total_cost_usd sum as str; records list has ≤50 entries each with id/model_id/prompt_tokens/completion_tokens/cost_usd/status/created_at
  - test_admin_usage_tenant_isolation: arrange tenant A (2 rows) + tenant B (1 row) / act GET /admin/usage with tenant B's JWT / assert records list has exactly 1 entry; total_requests=1; tenant A's rows absent
  - test_admin_usage_rejected_without_jwt: act GET /admin/usage with no Authorization header / assert 401 problem+json code "ERR_AUTH_INVALID_TOKEN"
</test_plan>

Tests live in: `apps/gateway/tests/usage/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): cost_usd MUST be computed with ALL Decimal arithmetic — never float; RecordingUsageRecorder MUST NOT raise into the caller (swallow + log all failures); the ledger insert MUST be idempotent (ON CONFLICT DO NOTHING); forwarded SSE bytes MUST be byte-identical to upstream (tee, never re-serialize); UsageLedgerFlusher MUST use XREADGROUP + XACK (at-least-once, never lose an event on the happy path).
Code lives in: `apps/gateway/src/gateway/` (new module `usage/`); additive wiring in `main.py`; additive cross-module touch in `gateway/proxy/application/use_cases.py` (streaming tee only)
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

Safety compliance evidence:
- cost_usd: all arithmetic uses `Decimal(str(...))` chain — no float intermediate at any point (recorder.py lines 108–111)
- RecordingUsageRecorder.record() wraps `_record_internal` in `except Exception` with `_log.warning` — never re-raises (recorder.py lines 64–81)
- Ledger insert uses `ON CONFLICT (id) DO NOTHING` with stream_id_to_uuid(stream_id) as idempotency key (flusher.py lines 147–156)
- SSE tee: `collected.append(chunk); yield chunk` — bytes forwarded first, extraction happens after full stream (use_cases.py _wrapped())
- XREADGROUP + XACK: flusher reads via consumer group, ACKs only after successful INSERT (flusher.py lines 64–70, 171–172)

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 69 passed, 0 failed (`uv run pytest -q`, 2026-06-10)
- [x] coverage did not decrease — 82.38% total (was ~82% before; floor 80% passed)
- [x] no test or contract was altered during build — only `gateway/usage/**`, `main.py`, `config.py`, `proxy/application/use_cases.py` touched; diff confirmed additive-only
- [x] concurrency / timing of the risky operation is safe — background flusher guarded by `app.on_event` which ASGITransport never calls; tests drive `flush_once()` directly with no timing dependency; `asyncio.create_task` result held in `app.state.flusher_task` to prevent GC
- [x] no exposed secrets, injection openings, or unexpected dependencies — redis_url is a settings field with dev default; JWT auth on `/admin/usage` reuses existing JwtTokenService; no raw SQL injection surface (all params bound via SQLAlchemy `text()` named params); `ruff check` with `S` rules passes
- [x] layering & dependencies follow CONVENTIONS.md — domain layer (extractor.py, errors.py) has zero framework imports; application layer imports only domain + infra types; infrastructure imports SQLAlchemy + redis; api layer imports FastAPI + application
- [x] a person reviewed and approved the change — delegated auto mode, Tin Dang (2026-06-10)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced:
  - `RecordingUsageRecorder`: wired in `main.py` as `app.state.usage_recorder`; injected by tests via `app.state.usage_recorder = recorder`
  - `UsageLedgerFlusher`: instantiated in `main.py` `_start_flusher`; instantiated directly in each test
  - `UsageRecordRow`: imported in `main.py` as `_UsageRecordRow` to register SQLAlchemy metadata before `Base.metadata.create_all`
  - `extract_usage_from_sse`: called in `use_cases.py` `_wrapped()` after stream completion
  - `usage_router`: included in `main.py` via `app.include_router(usage_router)`
  - `redis_url` setting: read in `main.py` via `settings.redis_url`
- [x] DEAD-CODE (code) — `usage/api/deps.py` helper functions (`get_token_service`, `decode_jwt`, `get_bearer_token`) are not referenced by router.py (router inlines auth for simplicity); file is retained as the contract specifies `api/deps.py` must exist — no orphaned symbols in executed paths
- [x] SEMANTIC (prose / non-code) — TASK.md §1–§4 read in full; all Must/Reject/After rules verified against implementation; assumptions cross-checked: markup_pct column present on TenantRow (orm.py line 20); Redis INCRBYFLOAT advisory spend counter (recorder.py line 138–142); stream_id_to_uuid uses UUID5(NAMESPACE_DNS) for deterministic idempotency key

### GATE RECORD
Outcome: PASS
Reviewed by: auto-agent (Tin Dang delegated) · date: 2026-06-10

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): usage event drop rate (Redis unavailable) · ledger row count vs stream event count (flusher lag) · cost_usd precision drift (Decimal vs float counter divergence over time) · GET /admin/usage p99 latency · 401 rate on /admin/usage
Spec delta for the next loop:
- The `created_at` field stored in the Redis stream event is informational only; the flusher uses the DB server default (`now()`) on INSERT rather than replaying the stream timestamp, which means ledger `created_at` reflects flush time not request time. If request-time accuracy is required for billing disputes, store `created_at` as a proper ISO timestamptz in a separate field and parse it to a tz-aware Python `datetime` before passing to asyncpg.
- `usage/api/deps.py` was specified in the contract module layout but its helper functions are not consumed by the router (router inlines auth). Either delete deps.py or wire its helpers into the router in the next loop to eliminate dead code.
- The background flusher uses `@app.on_event("startup/shutdown")` (deprecated in FastAPI); migrate to `@asynccontextmanager` lifespan in the next loop.

### Competency deltas
- DDD · open: domain extractor (extract_usage_from_sse) is a pure function with no framework imports — confirmed clean boundary; evidence: zero imports in extractor.py
- SDD · open: write-behind pattern (Redis Stream → Postgres) decouples hot path latency from ledger writes; evidence: test_redis_unavailable_completion_still_200 passes even with BrokenRedis
- TDD · open: flush_once() as deterministic test entry point eliminates timing from all 9 scenarios; evidence: no asyncio.sleep in any usage test
- ADD · open: idempotent ledger upsert (ON CONFLICT DO NOTHING + UUID5 stream id) enables at-least-once delivery without double-counting; evidence: test_duplicate_flush_idempotent passes
