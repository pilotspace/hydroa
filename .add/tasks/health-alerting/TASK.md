# TASK: Health checks + alert events + webhook delivery

slug: health-alerting · created: 2026-06-11 · stage: production
risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Alert Dispatcher + Webhook Delivery + Event Producers + Upstream Health Checker

Framings weighed:
  - push-on-write (chosen) — events written by producers at the moment they occur; a
    separate dispatcher polls the undelivered partial index and POSTs to the webhook;
    at-least-once semantics; dead webhook never loses the signal (persisted row survives)
  - pull-only (rejected) — caller polls /admin/alerts; requires dashboard plumbing not in
    scope and gives no push guarantee
  - pub-sub via Redis (rejected) — adds a dependency for what is a low-throughput signal path;
    event persistence would still need a separate store for durability

Must:
<must>
  - M1  Settings must expose GATEWAY_ALERT_WEBHOOK_URL (str, optional; empty string = alerting
        disabled but events still persist); GATEWAY_ALERT_RETRY_MAX (int, default 3);
        GATEWAY_HEALTH_CHECK_INTERVAL_SECONDS (int, default 60; 0 = health checker disabled)
  - M2  AlertDispatcher is a background task (lifespan-managed, like the flusher) that polls
        alert_events WHERE delivered_at IS NULL (via the partial index) and POSTs each row as
        JSON to GATEWAY_ALERT_WEBHOOK_URL; if the URL is empty the dispatcher idles without error
  - M3  Webhook payload schema (pinned — see §3): {event_id, event_type, tenant_id, key_id,
        created_at, payload}; secrets NEVER in payload; webhook URL never logged in full (host only)
  - M4  Dispatcher retries on non-2xx with bounded exponential back-off: up to RETRY_MAX attempts
        per poll cycle; after RETRY_MAX failures the row is left undelivered for the next cycle
        (at-least-once, never dropped)
  - M5  2xx response from webhook → dispatcher sets delivered_at = now() on the row
  - M6  AlertDispatcher exposes a public run_once() seam (processes all currently undelivered
        rows with up to RETRY_MAX attempts each) for deterministic test driving without real timers
  - M7  On CLOSED→OPEN breaker transition: emit event_type=circuit_breaker_open with
        dedupe_key=f"breaker_open:{episode_id}" where episode_id is a UUID generated at
        trip time (one unique ID per open episode guarantees a new row per episode without
        clock-skew ambiguity); tenant_id=NULL (requires M15); key_id=NULL; payload={"state":"open"}
  - M8  On drain_until_empty timeout: emit event_type=drain_timeout with dedupe_key="drain_timeout"
        (deduped per process — one row per shutdown; see A2 for rationale); tenant_id=NULL;
        key_id=NULL; payload={"timeout_seconds": <float>}; emission MUST be synchronous-but-bounded
        (fire-and-forget via asyncio.ensure_future from inside drain_until_empty; the flusher
        already swallows exceptions — the same pattern applies)
  - M9  UpstreamHealthChecker runs periodically (HEALTH_CHECK_INTERVAL_SECONDS) via a lifespan
        background task; pings the upstream by issuing a HEAD request to https://openrouter.ai/api/v1/models
        with a short timeout (3s); tracks consecutive_failures internally; on reaching
        HEALTH_FAIL_THRESHOLD (default 3) consecutive failures it emits event_type=upstream_health_fail
        (once per episode — dedupe_key=f"health_fail:{episode_id}" where episode_id is assigned at
        the start of a new failure run and cleared on recovery); tenant_id=NULL; key_id=NULL;
        payload={"consecutive_failures": <int>, "url": "<host-only>"}
  - M10 On first success after a upstream_health_fail episode: emit event_type=upstream_health_recovered
        (dedupe_key=f"health_recovered:{episode_id}" where episode_id is the same UUID as the
        paired fail event); payload={"recovered_after_failures": <int>}
  - M11 UpstreamHealthChecker exposes a public check_once() seam (runs one health check cycle —
        ping + state machine + event emission) for deterministic test driving without real timers/network
  - M12 HealthChecker and AlertDispatcher accept injected ports: a WebhookSink Protocol (post_json)
        and an UpstreamPinger Protocol (ping) so tests never touch real network
  - M13 All new event emission uses ON CONFLICT (dedupe_key) DO NOTHING (same pattern as alert_writer)
        to guarantee idempotency; events are always persisted regardless of webhook URL being set
  - M14 The dispatcher and health-checker are drain-friendly: their background tasks are cancelled
        in shutdown and they flush one final run_once()/check_once() cycle before teardown
  - M15 Additive migration chained after f4a9b3c7e8d2: ALTERs alert_events.tenant_id to allow NULL
        (was NOT NULL) so system events (breaker, drain, health) can use tenant_id=NULL instead of
        a sentinel UUID; migration is additive and rollback-safe (ALTER COLUMN ... DROP NOT NULL)
</must>

Reject:
<reject>
  - R1  Webhook URL included in any log line -> log host portion only, redact path+credentials
  - R2  Secret values (API keys, JWT secrets, passwords) appear in any alert_events payload or
        webhook POST body -> "ERR_SECRETS_IN_PAYLOAD" (hard-stop; events are observable externally)
  - R3  Dispatcher raises into the background task loop on webhook 4xx/5xx -> swallow, leave
        row undelivered; background loop must be crash-safe
  - R4  Breaker OPEN transition emits more than one row per open episode (duplicate alert noise)
        -> dedupe via UNIQUE dedupe_key=f"breaker_open:{episode_id}"
  - R5  Health-fail episode emits more than one upstream_health_fail row per episode -> same
        deduplication via UNIQUE constraint + ON CONFLICT DO NOTHING
  - R6  Event emission (any producer) blocks the hot path or raises into the request handler ->
        fire-and-forget pattern; swallow all exceptions (same as existing alert_writer pattern)
  - R7  Webhook delivery blocks shutdown beyond GATEWAY_SHUTDOWN_DRAIN_TIMEOUT_SECONDS ->
        dispatcher must respect cancellation; at-most RETRY_MAX attempts per row per cycle
  - R8  AlertDispatcher or HealthChecker instantiated with real HTTP client in tests ->
        injected fake via WebhookSink/UpstreamPinger Protocol ports
</reject>

After:
<after>
  - Undelivered alert_events rows (delivered_at IS NULL) are polled and POSTed to the webhook;
    delivered_at is set on 2xx; row is never deleted
  - A breaker OPEN transition produces exactly one alert_events row (per episode) regardless
    of how many times record_failure() is called
  - A drain_until_empty timeout produces exactly one alert_events row per shutdown
  - Three consecutive upstream health check failures produce exactly one upstream_health_fail row;
    recovery from that episode produces exactly one upstream_health_recovered row
  - When GATEWAY_ALERT_WEBHOOK_URL is empty, all events still persist in alert_events with
    delivered_at NULL; the dispatcher idles without error
  - make ci is green; the existing 195 tests remain green
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ A1  tenant_id nullability — the f4a9b3c7e8d2 migration declares tenant_id NOT NULL with a
         FK to tenants.id ON DELETE CASCADE. System events (breaker, drain, health) have no
         tenant context. DECISION: additive migration (M15) ALTERs the column nullable rather
         than using a sentinel UUID, because a sentinel would require a guaranteed-to-exist
         tenant row and creates a hidden coupling. Cost if wrong: the ALTER may fail on Postgres
         versions before 11 (they don't — ALTER COLUMN DROP NOT NULL is safe on PG 11+, and we
         target PG 15+). The FK ON DELETE CASCADE still applies when tenant_id IS NOT NULL —
         NULL rows are unaffected. FLAG AT FREEZE: ⚠ [contract] tenant_id nullable requires
         additive migration M15; confirm acceptable before freeze.

  ⚠ A2  drain_timeout event emission during shutdown — drain_until_empty runs during the
         lifespan shutdown sequence AFTER the flusher background task is cancelled. The event
         must be persisted before the engine is disposed. Using asyncio.ensure_future from inside
         drain_until_empty and then immediately awaiting that single task (bounded: no retry,
         just insert) keeps the emission synchronous-enough to complete before engine.dispose().
         Cost if wrong: the event may be lost if the engine is disposed first; mitigated by
         ordering M14 (drain before dispose). FLAG AT FREEZE: ⚠ [contract] drain_timeout emission
         ordering vs engine.dispose() — confirm emit-then-dispose sequencing in lifespan.

  - [ ]  A3  Episode-ID for breaker_open uses a UUID generated at trip-time in record_failure()/
             on_upstream_error(). The breaker does not currently carry this ID. Build adds an
             _open_episode_id: uuid.UUID | None field to CircuitBreaker. CLOSED→OPEN sets it;
             OPEN→HALF_OPEN→CLOSED clears it. Confirm: breaker is per-replica (not distributed)
             so no cross-replica deduplication needed — per current TASK.md §1 assumption.

  - [ ]  A4  Webhook HTTP client — the dispatcher uses httpx.AsyncClient (already a project
             dependency). No new package needed. The WebhookSink port wraps it.

  - [ ]  A5  UpstreamPinger health check target — HEAD https://openrouter.ai/api/v1/models with
             3s timeout. HEAD is idempotent and low-cost. If OpenRouter doesn't support HEAD,
             falls back to GET (same URL); the source code shows GET is used by the catalog,
             confirming the endpoint exists.

  - [ ]  A6  HEALTH_FAIL_THRESHOLD is hardcoded 3 (not a Settings field) to keep the env var
             surface minimal; can be promoted to Settings if operational experience demands it.
             This is a low-cost decision — change is additive.

  - [ ]  A7  New modules: gateway.alerting.application.dispatcher (AlertDispatcher),
             gateway.alerting.application.health_checker (UpstreamHealthChecker),
             gateway.alerting.domain.ports (WebhookSink, UpstreamPinger Protocols),
             gateway.alerting.application.event_emitter (emit_system_event helper, shared by
             breaker and drain producers). The alerting module is a new top-level bounded context
             under src/gateway/ — consistent with the existing module layout.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S01 — undelivered row is delivered on run_once
  Given an alert_events row with delivered_at NULL exists in the DB
  And GATEWAY_ALERT_WEBHOOK_URL is set to a test sink URL
  When AlertDispatcher.run_once() is called
  Then the webhook sink receives exactly one POST with the correct payload schema
  And the alert_events row has delivered_at set to a non-NULL timestamp
  And the row is NOT deleted from alert_events

Scenario: S02 — webhook 500 leaves row undelivered, never drops
  Given an alert_events row with delivered_at NULL exists in the DB
  And GATEWAY_ALERT_WEBHOOK_URL is set to a sink that always returns 500
  When AlertDispatcher.run_once() is called
  Then the sink receives exactly RETRY_MAX POST attempts
  And the alert_events row still has delivered_at NULL (not dropped)

Scenario: S03 — webhook disabled (no URL) events persist, dispatcher idles
  Given GATEWAY_ALERT_WEBHOOK_URL is empty
  And an alert_events row has been persisted with delivered_at NULL
  When AlertDispatcher.run_once() is called
  Then no HTTP POST is made
  And the alert_events row still exists with delivered_at NULL
  And no exception is raised

Scenario: S04 — webhook payload schema is exact
  Given an alert_events row with event_type=soft_budget_exceeded, tenant_id set, key_id set
  And GATEWAY_ALERT_WEBHOOK_URL is set to a recording sink
  When AlertDispatcher.run_once() delivers the row
  Then the posted JSON body contains exactly the keys: event_id, event_type, tenant_id, key_id, created_at, payload
  And tenant_id and key_id are serialised as strings (not UUIDs)
  And no other keys are present (no secrets, no internal fields)

Scenario: S05 — webhook URL is not logged in full
  Given GATEWAY_ALERT_WEBHOOK_URL is "https://user:secret@hooks.example.com/abc123?token=xyz"
  When AlertDispatcher delivers any event
  Then log output contains "hooks.example.com" (host only)
  And log output does NOT contain "secret" or "abc123" or "xyz"

Scenario: S06 — breaker OPEN transition emits exactly one event per episode
  Given the circuit breaker is CLOSED with failure_count 4
  And an async_sessionmaker is wired to the real test DB
  When record_failure() (or on_upstream_error()) is called one more time (5th failure)
  Then exactly one alert_events row with event_type=circuit_breaker_open exists in the DB
  And the row has tenant_id NULL, key_id NULL, dedupe_key matching "breaker_open:<uuid>"
  And calling record_failure() a 6th time (breaker already OPEN) produces NO additional row

Scenario: S07 — breaker OPEN triggers a new episode row each time it re-trips
  Given the circuit breaker has opened, recovered to CLOSED, and then accumulates 5 new failures
  When the 5th new failure trips the breaker OPEN again
  Then a second alert_events row with event_type=circuit_breaker_open exists in the DB
  And the two rows have DIFFERENT dedupe_key values (different episode UUIDs)

Scenario: S08 — drain timeout emits event persisted synchronously-bounded
  Given the flusher has pending events that cannot be drained within the timeout
  And the session_factory is wired to the real test DB
  When drain_until_empty(timeout=<very_short>) times out
  Then exactly one alert_events row with event_type=drain_timeout exists in the DB
  And the row has tenant_id NULL, key_id NULL, dedupe_key="drain_timeout"
  And the flusher exits within timeout + small buffer (the emit does not block indefinitely)

Scenario: S09 — health 3 consecutive failures emit exactly one upstream_health_fail
  Given UpstreamHealthChecker with a fake pinger that always raises (simulating failure)
  And consecutive_failures is 0 and no prior episode active
  When check_once() is called 3 times
  Then exactly one alert_events row with event_type=upstream_health_fail exists in the DB
  And the row has tenant_id NULL, payload.consecutive_failures == 3
  And calling check_once() a 4th time with continued failure produces NO additional row (same episode)

Scenario: S10 — health recovery emits upstream_health_recovered with matching episode_id
  Given UpstreamHealthChecker that emitted upstream_health_fail with episode_id X
  When check_once() is called and the fake pinger succeeds
  Then exactly one alert_events row with event_type=upstream_health_recovered exists in the DB
  And the row's dedupe_key is f"health_recovered:{X}" (paired episode UUID)
  And consecutive_failures is reset to 0

Scenario: S11 — health fails again after recovery starts a new episode
  Given UpstreamHealthChecker that has recovered (episode X cleared)
  When check_once() is called 3 more times with a failing pinger
  Then a second alert_events row with event_type=upstream_health_fail exists with a NEW episode UUID
  And the second row's dedupe_key differs from the first episode's dedupe_key

Scenario: S12 — webhook 2xx on retry succeeds after initial failures
  Given an alert_events row with delivered_at NULL
  And a fake sink that returns 500 on the first attempt, then 200 on the second
  And RETRY_MAX >= 2
  When AlertDispatcher.run_once() is called
  Then the sink receives exactly 2 POST attempts
  And delivered_at is set on the row (row is marked delivered)

Scenario: S13 — system event with tenant_id NULL is accepted by the DB schema
  Given the additive migration M15 has been applied (tenant_id nullable)
  When an alert_events row is inserted with tenant_id NULL directly via the ORM
  Then the INSERT succeeds (no NOT NULL constraint violation)
  And the row is retrievable with tenant_id IS NULL

Scenario: S14 — Settings has the three new alert/health fields with correct defaults
  Given a default Settings() instance (no env vars set)
  When the settings are inspected
  Then alert_webhook_url == "" (empty string, alerting disabled)
  And alert_retry_max == 3
  And health_check_interval_seconds == 60

Scenario: S15 — health_check_interval_seconds=0 disables health checker
  Given Settings(health_check_interval_seconds=0)
  When the app lifespan starts
  Then no UpstreamHealthChecker background task is scheduled
  And no health_checker_task is stored on app.state (or it is None)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# No new HTTP endpoints — this task is entirely background / infrastructure.

# ── Settings (new fields) ────────────────────────────────────────────────────
Settings fields (env prefix GATEWAY_):
  alert_webhook_url: str = ""                    # GATEWAY_ALERT_WEBHOOK_URL
  alert_retry_max: int = 3                       # GATEWAY_ALERT_RETRY_MAX
  health_check_interval_seconds: int = 60        # GATEWAY_HEALTH_CHECK_INTERVAL_SECONDS
                                                 # 0 = health checker disabled

# ── Webhook POST payload (FROZEN) ────────────────────────────────────────────
POST <GATEWAY_ALERT_WEBHOOK_URL>
  Content-Type: application/json
  Body: {
    "event_id":   "<UUID string>",          # alert_events.id
    "event_type": "<string>",               # e.g. "soft_budget_exceeded"
    "tenant_id":  "<UUID string | null>",   # NULL for system events
    "key_id":     "<UUID string | null>",   # NULL for tenant/system events
    "created_at": "<ISO 8601 UTC string>",  # alert_events.created_at
    "payload":    { ... }                   # event-specific JSONB payload
  }
  # Exactly these 6 keys — no additional fields
  # Secrets NEVER included; webhook URL never logged in full (host only)

  2xx → dispatcher sets delivered_at = now() on the row; row is never deleted
  non-2xx (after RETRY_MAX attempts) → row left undelivered (delivered_at NULL); try again next cycle
  connection error → treated as non-2xx; same retry/leave semantics

# ── Event type enum + dedupe_key formats (FROZEN) ────────────────────────────
soft_budget_exceeded  dedupe_key = "soft_budget:{key_id}:{YYYYMM}"           (owned by spend-windows — do not change)
circuit_breaker_open  dedupe_key = "breaker_open:{episode_uuid}"              (one UUID per trip event)
drain_timeout         dedupe_key = "drain_timeout"                            (one per shutdown; idempotent per process)
upstream_health_fail  dedupe_key = "health_fail:{episode_uuid}"               (one UUID per failure run)
upstream_health_recovered dedupe_key = "health_recovered:{episode_uuid}"      (same UUID as paired fail event)

# ── Port signatures (FROZEN) ─────────────────────────────────────────────────
Protocol WebhookSink:
  async def post_json(self, url: str, payload: dict[str, object]) -> int:
    """POST payload as JSON to url; return HTTP status code.
    Raises on connection error (caller treats as failure).
    """

Protocol UpstreamPinger:
  async def ping(self) -> None:
    """Ping the upstream health endpoint.
    Raises on any failure (timeout, connection error, non-2xx).
    """

class AlertDispatcher:
  def __init__(
    self,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    webhook_sink: WebhookSink,
    webhook_url: str,
    retry_max: int = 3,
  ) -> None: ...

  async def run_once(self) -> None:
    """Query all undelivered rows; attempt delivery; set delivered_at on 2xx."""

  async def run_forever(self, *, interval_seconds: float = 5.0) -> None:
    """Background loop: run_once() every interval_seconds."""

class UpstreamHealthChecker:
  def __init__(
    self,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    pinger: UpstreamPinger,
    fail_threshold: int = 3,
  ) -> None: ...

  async def check_once(self) -> None:
    """Run one health check cycle: ping + state machine + event emission."""

  async def run_forever(self, *, interval_seconds: float = 60.0) -> None:
    """Background loop: check_once() every interval_seconds."""

# ── Schema (additive changes) ────────────────────────────────────────────────
Migration chained after f4a9b3c7e8d2:
  ALTER TABLE alert_events ALTER COLUMN tenant_id DROP NOT NULL;
  -- Additive, rollback: ALTER TABLE alert_events ALTER COLUMN tenant_id SET NOT NULL;
  -- (rollback only safe if no NULL rows exist)

alert_events table (after M15):
  id            UUID         PRIMARY KEY
  tenant_id     UUID         NULL        FK(tenants.id) ON DELETE CASCADE  ← changed
  key_id        UUID         NULL
  event_type    TEXT         NOT NULL
  payload       JSONB        NOT NULL
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
  delivered_at  TIMESTAMPTZ  NULL
  dedupe_key    TEXT         NOT NULL UNIQUE

# ── Modules-touched hard boundary ────────────────────────────────────────────
NEW:
  src/gateway/alerting/                          (new bounded context)
  src/gateway/alerting/__init__.py
  src/gateway/alerting/domain/
  src/gateway/alerting/domain/__init__.py
  src/gateway/alerting/domain/ports.py           (WebhookSink, UpstreamPinger Protocols)
  src/gateway/alerting/application/
  src/gateway/alerting/application/__init__.py
  src/gateway/alerting/application/dispatcher.py (AlertDispatcher)
  src/gateway/alerting/application/health_checker.py (UpstreamHealthChecker)
  src/gateway/alerting/application/event_emitter.py (emit_system_event helper)
  src/gateway/alerting/infrastructure/
  src/gateway/alerting/infrastructure/__init__.py
  src/gateway/alerting/infrastructure/httpx_webhook_sink.py  (HttpxWebhookSink)
  src/gateway/alerting/infrastructure/httpx_pinger.py        (HttpxUpstreamPinger)
  apps/gateway/migrations/versions/<rev>_health_alerting_tenant_nullable.py

MODIFIED (additive only):
  src/gateway/core/config.py                     (add 3 Settings fields)
  src/gateway/proxy/infrastructure/circuit_breaker.py  (add _open_episode_id; callback hook)
  src/gateway/usage/application/flusher.py       (emit drain_timeout event in drain_until_empty)
  src/gateway/main.py                            (wire AlertDispatcher + HealthChecker lifespan tasks)

NOT TOUCHED:
  Any existing test file
  alert_events_orm.py ORM (the AlertEventRow ORM is modified only via the new migration; if
    the ORM needs updating for nullable tenant_id, update alert_events_orm.py mapped_column only)
  spend-windows TASK.md or contracts (CROSS-TASK FREEZE honored)
```

Status: FROZEN @ v3 — approved by Tin Dang (delegated auto mode, 2026-06-11; v3 roadmap confirmed "Proceed as drafted").
Build-time disposition (orchestrator, 2026-06-11):
| artifact | defect | disposition |
|---|---|---|
| test_s08 dedupe_key assertion (and §3 dedupe format for drain_timeout) | pinned the CONSTANT key "drain_timeout" — ON CONFLICT(dedupe_key) would then swallow every drain-timeout event after the first one in the table's lifetime, permanently blinding the alert | dedupe_key amended to per-episode "drain_timeout:{uuid4}"; test assertion relaxed to startswith("drain_timeout"); count-per-drain assertion unchanged. Strengthens the alert; weakens nothing. |

Least-sure flag surfaced at freeze:
⚠ [contract] alert_events.tenant_id is NOT NULL today (spend-windows f4a9b3c7e8d2) but
  system events (breaker/drain/health) carry no tenant — this contract's additive migration
  DROPs the NOT NULL. Verified non-conflicting with the spend-windows frozen surface
  (dedupe_key UNIQUE + soft_budget payload untouched); cost if wrong: cross-task migration
  fork — ordering pinned (chains after f4a9b3c7e8d2).
⚠ [contract] drain_timeout event is persisted during SHUTDOWN with a 0.5s-bounded await
  before engine.dispose(); if the bound lapses the event is logged-not-persisted — accepted:
  shutdown must never hang; cost if wrong: a missed drain alert, observable in logs anyway.

⚠ FREEZE FLAGS (lowest-confidence first):
  ⚠ [contract] tenant_id nullability — M15 alters tenant_id to nullable via a new migration.
    The spend-windows CROSS-TASK FREEZE covers dedupe_key UNIQUE + soft_budget payload only —
    the nullable ALTER is additive and does not conflict. Confirm acceptable before freeze.
  ⚠ [contract] drain_timeout event ordering — emitted inside drain_until_empty via
    asyncio.ensure_future; the task is awaited with a short bounded timeout (0.5s) before
    engine.dispose() in the lifespan sequence. If the event session cannot open within 0.5s
    the event is logged but not persisted. Confirm bounded-await pattern is acceptable.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of new alerting module code

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_s01_undelivered_row_delivered_on_run_once:
      arrange: insert alert_events row (delivered_at NULL) into real DB; fake sink returns 200
      act: AlertDispatcher.run_once()
      assert: sink received one POST with correct schema; row.delivered_at is not None

  - test_s02_webhook_500_leaves_row_undelivered_never_drops:
      arrange: insert row; fake sink always returns 500
      act: AlertDispatcher.run_once() with retry_max=3
      assert: sink received exactly 3 POST attempts; row.delivered_at is still None; row exists

  - test_s03_no_url_events_persist_dispatcher_idles:
      arrange: AlertDispatcher(webhook_url=""); insert row
      act: AlertDispatcher.run_once()
      assert: no POST made (sink.call_count == 0); row still exists with delivered_at NULL

  - test_s04_webhook_payload_schema_exact:
      arrange: insert row with event_type=soft_budget_exceeded, tenant_id=<uuid>, key_id=<uuid>
      act: AlertDispatcher.run_once()
      assert: posted JSON has exactly keys {event_id, event_type, tenant_id, key_id, created_at, payload}
              tenant_id and key_id are str; no extra keys

  - test_s05_webhook_url_not_logged_in_full:
      arrange: AlertDispatcher with webhook_url containing credentials and path token
      act: trigger delivery (with recording log handler)
      assert: log output contains host only; "secret", path token NOT in log output

  - test_s06_breaker_open_emits_exactly_one_row_per_episode:
      arrange: CircuitBreaker wired with session_factory to real DB; failure_count=4
      act: record_failure() 5th time → OPEN; then record_failure() 6th time (already OPEN)
      assert: DB has exactly 1 row with event_type=circuit_breaker_open;
              dedupe_key matches "breaker_open:<uuid>"; tenant_id IS NULL

  - test_s07_breaker_new_episode_after_recovery_produces_new_row:
      arrange: same breaker; trip → recover → trip again (5 new failures)
      act: second OPEN transition
      assert: DB has 2 rows with event_type=circuit_breaker_open; different dedupe_keys

  - test_s08_drain_timeout_emits_event:
      arrange: flusher with pending events; session_factory wired to real DB; tiny timeout
      act: drain_until_empty(timeout=0.01)
      assert: DB has 1 row with event_type=drain_timeout; dedupe_key="drain_timeout";
              tenant_id IS NULL; drain exits within 2s

  - test_s09_health_3_consecutive_failures_emit_one_fail_event:
      arrange: UpstreamHealthChecker with fake pinger always raising; session_factory to real DB
      act: check_once() × 3
      assert: DB has exactly 1 row with event_type=upstream_health_fail;
              payload.consecutive_failures == 3; tenant_id IS NULL

  - test_s10_health_recovery_emits_recovered_with_matching_episode_id:
      arrange: same checker after 3 failures (upstream_health_fail emitted); pinger now succeeds
      act: check_once()
      assert: DB has 1 row with event_type=upstream_health_recovered;
              dedupe_key == f"health_recovered:{episode_id_from_fail_row}"

  - test_s11_health_new_failure_run_after_recovery_new_episode:
      arrange: checker that recovered; pinger fails again 3 times
      act: check_once() × 3 post-recovery
      assert: DB has 2 rows with event_type=upstream_health_fail; distinct dedupe_keys

  - test_s12_webhook_retries_then_succeeds:
      arrange: fake sink: 500 on attempt 1, 200 on attempt 2; retry_max >= 2
      act: AlertDispatcher.run_once()
      assert: sink received exactly 2 POST calls; row.delivered_at is not None

  - test_s13_system_event_tenant_id_null_accepted_by_schema:
      arrange: apply migrations up to M15; attempt INSERT with tenant_id=NULL
      act: INSERT into alert_events via ORM/raw SQL
      assert: INSERT succeeds; row queryable with tenant_id IS NULL

  - test_s14_settings_new_fields_defaults:
      arrange: Settings() with no env overrides
      act: inspect Settings instance
      assert: alert_webhook_url == ""; alert_retry_max == 3; health_check_interval_seconds == 60

  - test_s15_health_check_interval_zero_disables_checker:
      arrange: Settings(health_check_interval_seconds=0)
      act: create_app(settings) and use lifespan (via async context manager or TestClient)
      assert: app.state has no health_checker_task (or it is None)
</test_plan>

Tests live in: `apps/gateway/tests/health_alerting/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
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

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
