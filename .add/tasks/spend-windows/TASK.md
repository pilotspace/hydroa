# TASK: Rolling spend windows, soft-budget alerts, spend query API

slug: spend-windows · created: 2026-06-11 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Rolling spend windows, soft-budget alert events, GET /admin/spend query API

Framings weighed:
  - Aggregate from ledger + separate alert_events table + fire-and-forget crossing detection
    (chosen) — usage_records is the append-only source of truth; aggregation via SQL
    date_trunc gives exact Decimal sums with no float lossiness; alert_events is a minimal
    additive table owned by this task and extended by health-alerting; crossing detection is
    a fire-and-forget asyncio task so it never touches the hot-path response latency.
  - Aggregate from Redis advisory counters (rejected) — counters are IEEE 754 floats and
    have drift over time; the exit criterion requires exact reconciliation with usage_records.
  - Sliding budget windows (rejected) — the MILESTONE shared decision resolves "rolling" in
    the goal as "queryable windowed aggregates", NOT sliding enforcement. Calendar windows
    (YYYYMM) are used for budget enforcement because the existing per-key and tenant Redis
    counters already key on calendar month. This interpretation is stated explicitly here.

INTERPRETATION NOTE — "rolling" windows:
  The v3 milestone goal says "rolling spend windows" but the shared decisions section
  establishes calendar-month budget windows (YYYYMM counter key) for enforcement. This task
  implements QUERYABLE windows (day / week / month / custom start+end) over the ledger, NOT
  sliding-window enforcement. The enforcement window stays calendar-month. This aligns with
  the key-governance frozen contract (usage:spend:key:{key_id}:{YYYYMM}) and avoids
  rewriting the budget counter semantics already in production.

<must>
  ### Spend query API
  - M1  GET /admin/spend (JWT admin auth, same Bearer token as /admin/usage):
        query params:
          window: "day" | "week" | "month"  (default: "month"; required if no start/end)
          key_id: UUID  (optional; filter to a single key; must belong to caller's tenant)
          group_by: "key_id"  (optional; adds per-key breakdown list to response)
          start: ISO-8601 date string "YYYY-MM-DD"  (optional; overrides window lower bound)
          end:   ISO-8601 date string "YYYY-MM-DD"  (optional; overrides window upper bound;
                 inclusive — end date includes the full end day up to 23:59:59.999 UTC)
        Response: windowed aggregates computed FROM usage_records (NOT Redis counters):
          - totals object: {bucket_start, bucket_end, requests, prompt_tokens,
                            completion_tokens, cost_usd}
          - buckets list: one item per date_trunc bucket within the window, same shape as totals
          - breakdown list (only when group_by=key_id): [{key_id, requests, prompt_tokens,
                            completion_tokens, cost_usd}] sorted by cost_usd DESC

  - M2  Aggregation SQL semantics:
        - date_trunc granularity: 'day' for window=day and start/end, 'week' for window=week,
          'month' for window=month — always UTC (AT TIME ZONE 'UTC' or server in UTC)
        - SUM(cost_usd) computed with Postgres NUMERIC arithmetic (not float) — Decimal-safe
        - COUNT(*) for requests
        - COALESCE(SUM(...), 0) so empty windows return zero, never NULL
        - All queries scoped to caller's tenant_id (tenant isolation invariant)
        - key_id filter adds AND key_id = :key_id to WHERE clause
        - group_by=key_id adds GROUP BY key_id to the breakdown query
        - Exit criterion exactness: SUM(cost_usd) over the same WHERE clause MUST equal
          the totals.cost_usd returned — the build MUST NOT use approximations

  - M3  Window boundary semantics:
        - window=month: bucket_start = date_trunc('month', now() AT TIME ZONE 'UTC');
          span covers the current calendar month to now()
        - window=week: bucket_start = date_trunc('week', now() AT TIME ZONE 'UTC');
          ISO week (Monday-aligned); span covers current ISO week to now()
        - window=day: buckets cover today UTC; span from midnight to now()
        - start/end overrides: if both supplied, ignore window and use them as the full span;
          if only one supplied with window, treat as a clamp on the default window boundary
        - Default (no params): window=month behavior

  - M4  Empty window: when no usage_records exist in the window, return 200 with all
        numeric fields = 0 (or "0" for cost_usd string). Never 404 on empty data.

  - M5  Soft-budget crossing detection + alert_events persistence:
        When _check_per_key_budget() computes soft_budget_exceeded = True (from the
        existing TODO seam in proxy/application/use_cases.py), the crossing detection MUST:
        a) Fire a fire-and-forget asyncio task (never awaited on the hot path)
        b) The task INSERTs one row into alert_events with:
               dedupe_key = "soft_budget:{key_id}:{YYYYMM}"
               ON CONFLICT (dedupe_key) DO NOTHING   (idempotent — no duplicate rows)
        c) The task swallows ALL exceptions (failures logged, never raised into the path)
        d) Redis unavailable → no event fired, no INSERT attempted, no HTTP error
        e) The completion response is NEVER modified, delayed, or blocked by this logic

  - M6  alert_events table (MINIMAL — owned by this task; health-alerting task extends/consumes):
        Columns: id UUID PK, tenant_id UUID NOT NULL FK(tenants.id), key_id UUID NULLABLE,
                 event_type TEXT NOT NULL, payload JSONB NOT NULL,
                 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                 delivered_at TIMESTAMPTZ NULL,
                 dedupe_key TEXT NOT NULL UNIQUE
        event_type for soft-budget: "soft_budget_exceeded"
        payload schema: {"soft_budget_usd": "<decimal string>", "key_spend_usd": "<decimal string>"}
        delivered_at: NULL at creation (health-alerting task sets this when webhook fires)
        Additive migration revises: c3f8a2e1d5b7 (current head)

  - M7  Cross-task boundary (alert_events DDL):
        This task creates the MINIMAL alert_events table. The health-alerting task will
        add webhook_url, retry_count, and delivery machinery as additive columns/indexes.
        The dedupe_key UNIQUE constraint and payload JSONB schema for soft_budget_exceeded
        are FROZEN here. Health-alerting MUST NOT change the column names created here.
        This boundary is explicitly flagged at the freeze (cross-task contract surface).
</must>

<reject>
  - R1  GET /admin/spend with window not in {"day","week","month"} (and no start/end override)
        -> "ERR_PAYLOAD_INVALID" (422)
  - R2  GET /admin/spend with invalid ISO date for start or end
        -> "ERR_PAYLOAD_INVALID" (422)
  - R3  GET /admin/spend with key_id belonging to a different tenant
        -> "ERR_KEY_NOT_FOUND" (404)  (no cross-tenant leak; identical to key CRUD pattern)
  - R4  GET /admin/spend without Authorization header or with invalid token
        -> "ERR_AUTH_INVALID_TOKEN" (401)
  - R5  GET /admin/spend with a member-role JWT
        -> "ERR_AUTH_FORBIDDEN" (403)
  - R6  Soft-budget crossing fires a second INSERT with the same dedupe_key
        -> silently ignored (ON CONFLICT DO NOTHING; no duplicate row, no error)
  - R7  Soft-budget crossing when Redis counter read fails
        -> fire-and-forget task is never scheduled; completion returns 200 unaffected
</reject>

<after>
  - After M1: GET /admin/spend?window=month returns totals whose cost_usd equals
    SUM(cost_usd) from usage_records WHERE tenant_id=:tid AND created_at >= month_start.
  - After M2: The SQL aggregation uses date_trunc with NUMERIC arithmetic — no float drift.
  - After M4: A tenant with zero usage rows gets 200 {"totals": {"cost_usd": "0", "requests": 0, ...}}.
  - After M5 (first crossing): exactly one alert_events row exists with the expected dedupe_key.
  - After M5 (repeated crossings): still exactly one alert_events row (ON CONFLICT idempotent).
  - After M5 (Redis down): zero alert_events rows; completion was 200 (fail-open).
  - After M6: alert_events table exists with UNIQUE(dedupe_key); health-alerting can extend it.
  - Soft-budget crossing: the completion response is always 200 when soft budget is crossed
    and no hard budget is exceeded — the seam never modifies the HTTP response.
</after>

<assumptions>
  ⚠ A1 [LOWEST CONFIDENCE — cost: schema boundary drift] The alert_events table is owned by
     spend-windows (minimal DDL) and extended by health-alerting. If health-alerting requires
     a column that conflicts with a name defined here (e.g. health-alerting also needs
     "event_type" with a different type or constraint), both tasks must coordinate before
     either build phase. CROSS-TASK FREEZE FLAG: the dedupe_key + payload JSONB schema for
     "soft_budget_exceeded" is frozen here; health-alerting MUST NOT modify these columns.
     If wrong: both tasks fail at migration parity gate; requires a change request back to
     SPECIFY on both tasks. This is the primary freeze flag candidate.

  ⚠ A2 [HIGH CONCERN — cost: no events ever fire] The fire-and-forget task for soft-budget
     crossing detection must be scheduled WITHIN _check_per_key_budget() in proxy/
     application/use_cases.py, which already has the TODO comment naming this seam.
     If the builder misses this wiring (e.g. puts the logic in the recorder instead),
     the check never runs because the recorder fires AFTER upstream (not pre-flight).
     The pre-flight location is non-negotiable: detection must use the same spent value
     already computed at _check_per_key_budget() time, not a second Redis read.
     Cost if wrong: alert_events rows never written despite tests passing (fakes hide it).

  - A3 [Decimal string in JSON for cost_usd] The spend API uses str(Decimal) for cost_usd
     (same as /admin/usage frozen contract). This is exact and avoids JSON float lossiness.
     Alternative: numeric (float). Chosen: string decimal, consistent with existing frozen
     contract. Cost if wrong: float drift in billing displays; easy to change before freeze.

  - A4 [group_by=key_id returns all keys in the window] The breakdown includes keys with
     zero cost (if group_by + key_id filter is used). Alternative: only non-zero keys.
     Chosen: only non-zero cost keys appear in breakdown (simpler SQL; zero-cost keys are
     not billed and not interesting). If wrong: UI misses keys that had activity but zero cost
     (e.g. 402-blocked requests). Cost: low — document assumption, easy to change pre-freeze.

  - A5 [start/end are inclusive on both ends] end date includes the full 24h of that day
     (00:00:00 to 23:59:59.999999 UTC). Implementation: WHERE created_at < (end + 1 day).
     Alternative: end is exclusive (strict <). Chosen: inclusive end (intuitive for date ranges).
     Cost if wrong: off-by-one on the last day's records.

  - A6 [Window default when no params supplied] Default is month (current calendar month).
     Implementation: window param defaults to "month" in FastAPI Query. If wrong: callers
     get unexpected scope on ambiguous calls. Cost: low; documented in contract.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first,
     top two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── M1/M2: Windowed aggregates reconcile exactly with ledger ─────────────────

Scenario: Windowed aggregates reconcile exactly with seeded ledger rows
  Given an owner JWT for tenant Acme
  And   3 usage_records seeded directly into the DB with known prompt_tokens,
        completion_tokens, and cost_usd values within the current calendar month
  When  GET /admin/spend?window=month
  Then  200 with totals.cost_usd == SUM(seeded cost_usd) as a Decimal string
  And   totals.requests == 3
  And   totals.prompt_tokens == SUM(seeded prompt_tokens)
  And   totals.completion_tokens == SUM(seeded completion_tokens)

# ── M3: Bucket boundaries — UTC date_trunc alignment ─────────────────────────

Scenario: Daily buckets align to UTC midnight boundaries (date_trunc)
  Given an owner JWT for tenant Acme
  And   one usage_record seeded on UTC day D (hour=10) and one on UTC day D-1 (hour=5)
  When  GET /admin/spend?window=day&start=D-1&end=D
  Then  200 with buckets list containing exactly 2 items
  And   each bucket's cost_usd matches only the records for that day (no cross-day bleed)
  And   each bucket_start is aligned to UTC midnight (date_trunc('day', created_at AT TIME ZONE 'UTC'))

# ── M1 / key_id filter ────────────────────────────────────────────────────────

Scenario: key_id filter scopes aggregates to a single key
  Given an owner JWT for tenant Acme with two keys: key-A and key-B
  And   key-A has 1 usage_record with cost_usd="0.00100000"
  And   key-B has 1 usage_record with cost_usd="0.99000000"
  When  GET /admin/spend?window=month&key_id={key-A id}
  Then  200 with totals.cost_usd == "0.00100000"
  And   key-B's cost is NOT included in any field

# ── M1 / group_by ─────────────────────────────────────────────────────────────

Scenario: group_by=key_id returns per-key breakdown
  Given an owner JWT for tenant Acme with two keys: gkey-A and gkey-B
  And   each key has 1 usage_record with distinct cost values
  When  GET /admin/spend?window=month&group_by=key_id
  Then  200 with body.breakdown containing exactly 2 items
  And   each item has key_id, requests, prompt_tokens, completion_tokens, cost_usd fields
  And   each item's cost_usd matches only that key's seeded records

# ── M4: Empty window returns zeros, not 404 ───────────────────────────────────

Scenario: Empty window returns 200 with zero totals
  Given an owner JWT for a tenant with no usage_records this month
  When  GET /admin/spend?window=month
  Then  200 with totals.cost_usd == "0", totals.requests == 0,
        totals.prompt_tokens == 0, totals.completion_tokens == 0
  And   buckets list is empty (no rows to bucket)

# ── R1: Invalid window param → 422 ────────────────────────────────────────────

Scenario: Invalid window parameter returns 422
  Given an owner JWT
  When  GET /admin/spend?window=fortnight
  Then  422 ERR_PAYLOAD_INVALID
  And   no usage_records are read or modified

# ── R4: Unauthenticated → 401 ──────────────────────────────────────────────────

Scenario: Missing Authorization header returns 401
  Given no Authorization header
  When  GET /admin/spend?window=month
  Then  401 ERR_AUTH_INVALID_TOKEN
  And   no data is returned

# ── S8 / tenant isolation ─────────────────────────────────────────────────────

Scenario: Tenant isolation — cross-tenant rows never visible
  Given owner JWTs for tenant A and tenant B
  And   tenant A has 1 usage_record with cost_usd="0.00010000"
  And   tenant B has 1 usage_record with cost_usd="9.99000000"
  When  GET /admin/spend?window=month using tenant-A JWT
  Then  200 with totals.cost_usd == "0.00010000"
  And   tenant-B's cost is NOT included

# ── M3: week and month windows accepted ───────────────────────────────────────

Scenario: window=week returns valid 200 with Monday-aligned bucket_start
  Given an owner JWT
  When  GET /admin/spend?window=week
  Then  200 with at least one bucket whose bucket_start is the ISO Monday of the current week

Scenario: window=month returns valid 200 with month-start aligned bucket_start
  Given an owner JWT
  When  GET /admin/spend?window=month
  Then  200 with at least one bucket whose bucket_start is the first day of the current month

# ── M1 / start/end overrides ──────────────────────────────────────────────────

Scenario: start/end ISO overrides filter the window precisely
  Given an owner JWT and 3 usage_records on distinct days D-10, D-3, D-2
  When  GET /admin/spend?window=day&start=D-3&end=D-2
  Then  200 with totals.cost_usd == SUM of D-3 and D-2 records only
  And   D-10 record is NOT included

# ── M5/M6: Soft-budget crossing persists exactly ONE alert_events row ─────────

Scenario: Soft-budget crossing persists exactly one alert_events row (idempotent)
  Given an owner JWT and a key with soft_budget_usd="0.00050000"
  And   per-key Redis counter pre-seeded at "0.00100000" (above soft budget)
  And   a model active in the catalog
  When  POST /v1/chat/completions with that key 3 times
  Then  all 3 completions return 200 (soft budget never blocks)
  And   exactly 1 row in alert_events with dedupe_key="soft_budget:{key_id}:{YYYYMM}"
  And   the row has event_type="soft_budget_exceeded"
  And   the row's payload contains soft_budget_usd and key_spend_usd fields
  And   delivered_at IS NULL (webhook delivery is health-alerting's job)

# ── M5: Soft-budget idempotency via UNIQUE constraint ─────────────────────────

Scenario: Repeated INSERT with same dedupe_key produces exactly 1 row
  Given an alert_events table with UNIQUE(dedupe_key)
  When  two INSERT ... ON CONFLICT (dedupe_key) DO NOTHING with the same dedupe_key
  Then  COUNT(*) WHERE dedupe_key = :dk == 1
  And   no IntegrityError is raised

# ── M5: Soft budget never blocks the hot path ─────────────────────────────────

Scenario: Soft-budget crossing never blocks the completion response
  Given an owner JWT and a key with soft_budget_usd="0.00001000"
  And   per-key Redis counter at "1.00000000" (way above soft budget)
  And   a model active in the catalog
  When  POST /v1/chat/completions with that key
  Then  200 (upstream called once)
  And   the response is NOT a 402 ERR_BUDGET_EXCEEDED

# ── R7: Redis unavailable → fail-open, no alert_events ────────────────────────

Scenario: Redis unavailable — no alert_events row, completion succeeds
  Given an owner JWT and a key with soft_budget_usd="0.00001000"
  And   Redis client raises ConnectionError on every call
  And   a model active in the catalog
  When  POST /v1/chat/completions with that key
  Then  200 (upstream called once — fail-open)
  And   no alert_events row is inserted (0 rows in alert_events)

# ── R5: Admin auth required ────────────────────────────────────────────────────

Scenario: GET /admin/spend requires at least owner/admin role JWT
  Given an owner JWT for tenant Acme
  When  GET /admin/spend?window=month with owner JWT
  Then  200
  When  GET /admin/spend?window=month without any JWT
  Then  401 ERR_AUTH_INVALID_TOKEN
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/spend
  auth: Bearer <JWT>  (admin/owner role)
  query params:
    window:   "day" | "week" | "month"   default "month"
    key_id:   UUID                        optional; must belong to caller's tenant
    group_by: "key_id"                   optional
    start:    "YYYY-MM-DD"               optional ISO date override (inclusive)
    end:      "YYYY-MM-DD"               optional ISO date override (inclusive)
  200 -> {
    "window": "day" | "week" | "month",
    "bucket_size": "day" | "week" | "month",   -- granularity of buckets list
    "totals": {
      "bucket_start":       string,             -- ISO-8601 UTC (first bucket start)
      "bucket_end":         string,             -- ISO-8601 UTC (last bucket end = now() or end param)
      "requests":           int,
      "prompt_tokens":      int,
      "completion_tokens":  int,
      "cost_usd":           string              -- str(Decimal); exact; never float
    },
    "buckets": [                                -- empty list when no records in window
      {
        "bucket_start":       string,           -- ISO-8601 UTC
        "requests":           int,
        "prompt_tokens":      int,
        "completion_tokens":  int,
        "cost_usd":           string            -- str(Decimal)
      }
    ],
    "breakdown": [                              -- omitted when group_by not supplied
      {
        "key_id":             uuid,
        "requests":           int,
        "prompt_tokens":      int,
        "completion_tokens":  int,
        "cost_usd":           string            -- str(Decimal); sorted DESC
      }
    ] | null
  }
  401 -> { "code": "ERR_AUTH_INVALID_TOKEN" }  -- missing/invalid JWT
  403 -> { "code": "ERR_AUTH_FORBIDDEN" }      -- member role
  404 -> { "code": "ERR_KEY_NOT_FOUND" }       -- key_id filter cross-tenant or nonexistent
  422 -> { "code": "ERR_PAYLOAD_INVALID" }     -- invalid window, invalid date format

Aggregation SQL contract (the build MUST use this pattern for exactness):
  SELECT
    date_trunc(:granularity, created_at AT TIME ZONE 'UTC') AS bucket_start,
    COUNT(*)                                               AS requests,
    COALESCE(SUM(prompt_tokens), 0)                        AS prompt_tokens,
    COALESCE(SUM(completion_tokens), 0)                    AS completion_tokens,
    COALESCE(SUM(cost_usd), 0)                             AS cost_usd
  FROM usage_records
  WHERE tenant_id = :tenant_id
    AND created_at >= :window_start
    AND created_at <  :window_end       -- window_end = (end_date + 1 day) for inclusive end
    [AND key_id = :key_id]              -- optional filter
  GROUP BY bucket_start
  ORDER BY bucket_start ASC

  Totals are the SUM of the above bucket rows (or a separate aggregate query with
  the same WHERE — same result, builder's choice). The exit criterion MUST hold:
    SUM(bucket.cost_usd for bucket in buckets) == totals.cost_usd

alert_events DDL (additive migration — revises: c3f8a2e1d5b7):
  CREATE TABLE alert_events (
    id            UUID         PRIMARY KEY,
    tenant_id     UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key_id        UUID         NULL,        -- nullable: some events are tenant-level
    event_type    TEXT         NOT NULL,    -- e.g. "soft_budget_exceeded"
    payload       JSONB        NOT NULL,    -- event-specific data (see below)
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    delivered_at  TIMESTAMPTZ  NULL,        -- NULL = undelivered; health-alerting sets this
    dedupe_key    TEXT         NOT NULL UNIQUE
  );
  -- Partial index for efficient undelivered query (health-alerting uses this):
  CREATE INDEX alert_events_undelivered_idx ON alert_events (created_at)
    WHERE delivered_at IS NULL;

  dedupe_key format for soft_budget_exceeded:
    "soft_budget:{key_id}:{YYYYMM}"
    Example: "soft_budget:019eb4a1-6b7b-7718-a8de-79e4efc18cb5:202606"

  payload schema for event_type = "soft_budget_exceeded":
    {
      "soft_budget_usd": "<decimal string>",   -- from key's soft_budget_usd field
      "key_spend_usd":   "<decimal string>"    -- Redis counter value at detection time
    }

  delivered_at: NULL at creation; health-alerting sets this to now() after webhook delivery.
  No backfill; downgrade drops the table and index.

  Migration revision: <next Alembic hash — generated at build time, revises c3f8a2e1d5b7>

Soft-budget detection seam (in proxy/application/use_cases.py _check_per_key_budget()):
  When soft_budget_exceeded is True:
    asyncio.ensure_future(_persist_soft_budget_alert(
        session_factory, tenant_id, key_id, soft_budget_usd, spent
    ))
    # _persist_soft_budget_alert: swallows all exceptions; logs failures; never raises.
    # Must NOT be awaited on the hot path.

Modules touched (hard boundary for the builder — no other modules):
  gateway/usage/api/router.py              -- add GET /admin/spend endpoint
  gateway/usage/api/schemas.py             -- add SpendWindowResponse + bucket/breakdown schemas
  gateway/proxy/application/use_cases.py   -- promote _soft_exceeded seam: schedule
                                              _persist_soft_budget_alert fire-and-forget task
  gateway/usage/application/alert_writer.py -- NEW: _persist_soft_budget_alert() implementation
  apps/gateway/migrations/versions/<hash>_alert_events.py -- new additive migration

  NOTE: alert_events is a SHARED TABLE SURFACE — health-alerting task will add columns.
  The boundary: this task owns the DDL creation; health-alerting owns delivery columns.
  Freeze flag candidate: see ⚠ FREEZE FLAG CANDIDATES below.
```

Status: FROZEN @ v3 — approved by Tin Dang (delegated auto mode, 2026-06-11; v3 roadmap confirmed "Proceed as drafted").
Least-sure flag surfaced at freeze:
⚠ [contract] alert_events is a CROSS-TASK surface: THIS task creates the minimal table
  (id, tenant_id, key_id, event_type, payload JSONB, created_at, delivered_at, dedupe_key
  UNIQUE) by migration; health-alerting extends/consumes it and its migration MUST chain
  after this one — cost if uncoordinated: duplicate CREATE TABLE / migration fork. Build
  ordering pinned: spend-windows builds first (orchestrator sequencing).
⚠ [spec] soft-crossing detection must be wired at the EXISTING pre-flight seam
  (_check_per_key_budget's computed _soft_exceeded), fire-and-forget, idempotent via the
  dedupe_key UNIQUE — cost if wrong: green tests with zero production alert rows; verify
  phase must confirm the wiring in src, not just test output.
Freeze-time note: the first red draft was INVERTED (asserted the absent state); orchestrator
caught it pre-freeze and the suite was rewritten to assert target behavior (15 red for the
right reasons + 1 deliberate invariant lock). Candidate TDD delta at task close.
<!-- Freeze requires human approval. Lowest-confidence flags must be resolved first. -->

⚠ FREEZE FLAG CANDIDATES (lowest-confidence first — block approval until resolved):

1. [contract / cross-task] A1 — alert_events table boundary with health-alerting:
   This task creates the minimal alert_events DDL (id, tenant_id, key_id, event_type,
   payload, created_at, delivered_at, dedupe_key UNIQUE). Health-alerting will add
   webhook_url, retry_count, next_retry_at, and delivery logic as additive columns.
   MUST coordinate: if health-alerting's freeze happens BEFORE this task's build,
   health-alerting must depend on this task's migration (revises c3f8a2e1d5b7 →
   alert_events migration → health-alerting migration). Build ordering is:
   spend-windows migration FIRST, health-alerting migration second.
   Flag resolution: both tasks must acknowledge this ordering before either build runs.
   Cost if wrong: migration conflict; two tasks trying to CREATE TABLE alert_events.

2. [spec] A2 — Soft-budget detection location (pre-flight seam in _check_per_key_budget):
   The fire-and-forget task must be scheduled INSIDE _check_per_key_budget() at the
   point where _soft_exceeded is already computed, NOT in the recorder (which fires
   post-upstream). The proxy/application/use_cases.py TODO comment names this seam.
   Build must not move this to the recorder. Verify wiring at verify phase.
   Cost if wrong: alert_events rows never written; tests pass because fakes hide it.

<!-- EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY
     + the bundle's lowest-confidence flag was surfaced at the freeze. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of new code paths (SQL aggregation branches, alert detection,
  idempotent INSERT, fail-open Redis path, auth checks, empty-window path)

Plan (one test per scenario, asserting TARGET behavior — suite is RED now, GREEN after build):
<test_plan>
  - test_windowed_aggregates_reconcile_with_ledger:
      arrange: seed 3 usage_records with known cost/token values in the current calendar month
      act: GET /admin/spend?window=month (owner JWT)
      assert: 200 + body.window=="month" + totals.requests==3 + totals.cost_usd==SUM(seeded) as Decimal string + totals.prompt_tokens/completion_tokens match sums [EXIT CRITERION]
      RED reason: route not registered → 404; asserting 200 FAILS

  - test_daily_bucket_boundaries_align_to_utc_date_trunc:
      arrange: seed 1 row on UTC day D (hour=10) and 1 on D-1 (hour=5)
      act: GET /admin/spend?window=day&start=D-1&end=D (owner JWT)
      assert: 200 + len(buckets)==2 + each bucket.cost_usd matches only that day's seeded record + 2 distinct bucket_start values
      RED reason: route not registered → 404; asserting 200 FAILS

  - test_key_id_filter_scopes_aggregates:
      arrange: 2 keys; key-A has cost=0.00100000, key-B has cost=0.99000000
      act: GET /admin/spend?window=month&key_id={key-A-id} (owner JWT)
      assert: 200 + totals.cost_usd=="0.00100000" (key-B excluded) + totals.requests==1
      RED reason: route not registered → 404; asserting 200 FAILS

  - test_group_by_key_id_returns_per_key_breakdown:
      arrange: 2 keys (gkey-A cost=0.00010000, gkey-B cost=0.00020000)
      act: GET /admin/spend?window=month&group_by=key_id (owner JWT)
      assert: 200 + breakdown list has 2 items + each item has key_id/requests/prompt_tokens/completion_tokens/cost_usd + per-key costs match seeded values
      RED reason: route not registered → 404; asserting 200 FAILS

  - test_empty_window_returns_zeros_not_404:
      arrange: tenant with no usage_records
      act: GET /admin/spend?window=month (owner JWT)
      assert: 200 + totals.requests==0 + totals.cost_usd=="0" + totals.prompt_tokens==0 + totals.completion_tokens==0 + buckets==[]
      RED reason: route not registered → 404; asserting 200 with zeros FAILS

  - test_invalid_window_param_returns_422:
      act: GET /admin/spend?window=fortnight (owner JWT)
      assert: 422 ERR_PAYLOAD_INVALID (via assert_problem helper)
      RED reason: route not registered → 404; asserting 422 FAILS

  - test_unauthenticated_returns_401:
      act: GET /admin/spend?window=month (no Authorization header)
      assert: 401 ERR_AUTH_INVALID_TOKEN (via assert_problem helper)
      RED reason: route not registered → 404; asserting 401 FAILS

  - test_spend_tenant_isolation:
      arrange: tenant-A (cost=0.00010000) + tenant-B (cost=9.99000000)
      act: GET /admin/spend?window=month using tenant-A JWT
      assert: 200 + totals.cost_usd=="0.00010000" (tenant-B excluded) + totals.requests==1
      RED reason: route not registered → 404; asserting 200 FAILS

  - test_window_week_accepted:
      act: GET /admin/spend?window=week (owner JWT)
      assert: 200 + body.window=="week"
      RED reason: route not registered → 404; asserting 200 FAILS

  - test_window_month_accepted:
      act: GET /admin/spend?window=month (owner JWT)
      assert: 200 + body.window=="month"
      RED reason: route not registered → 404; asserting 200 FAILS

  - test_start_end_iso_overrides_filter_window:
      arrange: rows on D-10, D-3, D-2; start=D-3&end=D-2 spans only 2 in-range days
      act: GET /admin/spend?window=day&start=D-3&end=D-2 (owner JWT)
      assert: 200 + totals.cost_usd==SUM(D-3, D-2 costs) + D-10 cost excluded + totals.requests==2
      RED reason: route not registered → 404; asserting 200 FAILS

  - test_soft_budget_crossing_persists_one_alert_event:
      arrange: key with soft_budget_usd=0.00050000 + Redis counter at 1.00 (above budget) + fake upstream
      act: 3x POST /v1/chat/completions + asyncio.sleep(0.05) for fire-and-forget to settle
      assert: all 3 completions 200 + SELECT from alert_events WHERE key_id=... returns 1 row + row.event_type=="soft_budget_exceeded" + row.dedupe_key=="soft_budget:{key_id}:{YYYYMM}" + payload has soft_budget_usd+key_spend_usd fields + delivered_at IS NULL
      RED reason: alert_events table absent → SELECT raises ProgrammingError → test FAILS

  - test_soft_budget_alert_idempotent_unique_constraint:
      act: 2x INSERT INTO alert_events ... ON CONFLICT (dedupe_key) DO NOTHING with same dedupe_key
      assert: COUNT(*) WHERE dedupe_key==... == 1 (second INSERT silently discarded)
      RED reason: alert_events table absent → first INSERT raises ProgrammingError → test FAILS

  - test_soft_budget_crossing_never_blocks_response: [CONTRACT LOCK — legitimately PASSES now]
      arrange: key with soft_budget_usd=0.00001000 + Redis counter at 1.00 + fake upstream
      act: POST /v1/chat/completions
      assert: 200 + upstream.calls==1 (soft budget never blocks)
      GREEN invariant: already implemented in key-governance build; locked here to prevent regression

  - test_redis_unavailable_no_alert_event_no_failure:
      arrange: BrokenRedis (raises ConnectionError on every call) + fake upstream
      act: POST /v1/chat/completions
      assert: 200 (fail-open) + SELECT COUNT(*) FROM alert_events == 0
      RED reason: alert_events table absent → COUNT query raises ProgrammingError → test FAILS

  - test_admin_spend_requires_owner_or_admin_jwt:
      act 1: GET /admin/spend?window=month with owner JWT → assert 200
      act 2: GET /admin/spend?window=month with no JWT → assert 401 ERR_AUTH_INVALID_TOKEN
      RED reason: route not registered → both return 404; asserting 200 and 401 FAIL
</test_plan>

Tests live in: `apps/gateway/tests/spend_windows/` · MUST run red (missing implementation) before Build.

Right-reason red evidence (confirmed 2026-06-11, rewritten to true-red):
  15 FAILED, 1 PASSED — analysis of fail/pass reasons per test:

  S1–S11 (routing tests — 11 tests): FAIL for right reason:
    GET /admin/spend route not registered → FastAPI returns 404 Not Found.
    Tests assert resp.status_code == 200 (TARGET) → AssertionError: 404 != 200.
    When GREEN: route registered → 200 returned → assertions pass.

  S6 test_invalid_window_param_returns_422: FAIL for right reason:
    Route not registered → 404; test asserts 422 via assert_problem → AssertionError: 404 != 422.

  S7 test_unauthenticated_returns_401: FAIL for right reason:
    Route not registered → 404; test asserts 401 via assert_problem → AssertionError: 404 != 401.

  S11 test_soft_budget_crossing_persists_one_alert_event: FAIL for right reason:
    After 3 completions, SELECT FROM alert_events raises ProgrammingError
    ("relation 'alert_events' does not exist") — propagates uncaught → test FAILS.

  S12 test_soft_budget_alert_idempotent_unique_constraint: FAIL for right reason:
    First INSERT INTO alert_events raises ProgrammingError (table absent) → test FAILS.

  S13 test_soft_budget_crossing_never_blocks_response: PASS — CONTRACT LOCK (legitimately green):
    Soft budget non-blocking behavior already implemented in key-governance build.
    This test is an invariant lock: asserts the same contract before and after build.
    It passes now and must continue to pass after build (no regression allowed).

  S14 test_redis_unavailable_no_alert_event_no_failure: FAIL for right reason:
    Completion returns 200 (fail-open already correct from key-governance); then
    SELECT COUNT(*) FROM alert_events raises ProgrammingError (table absent) → FAILS.

  S15 test_admin_spend_requires_owner_or_admin_jwt: FAIL for right reason:
    Route not registered → owner JWT gets 404 (not 200); test asserts 200 → FAILS.

  Existing suite: 179 passed, 19 deselected (e2e marker; excluded by default addopts)
  No regressions in existing 179 tests.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  - Aggregation SQL MUST use Postgres NUMERIC (SUM on NUMERIC column) — never cast to float.
    The exit criterion "SUM over usage_records reconciles exactly" fails on float arithmetic.
  - alert_events INSERT MUST use ON CONFLICT (dedupe_key) DO NOTHING — never upsert, never
    raise on duplicate. The UNIQUE constraint is the idempotency mechanism.
  - The fire-and-forget task for soft-budget detection MUST be scheduled INSIDE
    _check_per_key_budget() (pre-flight), NOT in the recorder (post-upstream). Moving it
    to the recorder means it fires AFTER the upstream call and AFTER the budget is relevant.
  - Fire-and-forget task MUST use asyncio.ensure_future() with a done-callback that swallows
    exceptions (same pattern as _fire_record in use_cases.py). NEVER await it on the hot path.
  - Build order: alert_events migration FIRST (spend-windows), then health-alerting migration.
    Health-alerting MUST declare down_revision pointing to the alert_events migration hash.
  - Never expose delivered_at, raw payload internals, or alert_events row id in the spend API
    response — alert_events is an internal table; the spend API returns aggregates only.
  - Tenant scoping: every usage_records query MUST include WHERE tenant_id = :tenant_id.
    Missing this is a data breach (cross-tenant leakage). mypy + the test isolation scenario
    are the double gate.

Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
  - GET /admin/spend 4xx rate (401/403/422) — indicates auth or param errors
  - alert_events INSERT rate vs. soft-budget crossing detection rate — should correlate
  - alert_events rows with delivered_at IS NULL growing unboundedly — webhook backlog
  - SUM(usage_records.cost_usd) vs. totals.cost_usd drift over time (reconciliation monitor)

Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
