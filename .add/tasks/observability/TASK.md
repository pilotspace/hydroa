# TASK: Structured logs + metrics + monitors

slug: observability · created: 2026-06-10 · stage: mvp
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Structured observability — JSON logs with request_id/tenant_id + Prometheus metrics on /internal/metrics

Framings weighed:
- **prometheus-client library** (chosen) — stdlib-level text-format generation, thread/async safe,
  TSDB-compatible, already in `dependencies.allowlist`; no new dep needed; counter/gauge/histogram
  primitives map 1:1 to our metric set. Tradeoff: global registry is a test-isolation hazard — mitigated
  by using a fresh `CollectorRegistry()` per app instance injected via `app.state`.
- **hand-rolled exposition** — zero deps, trivial for <5 metrics; rejected because prometheus-client is
  already allowlisted for v2 (`prometheus-client` in `.add/dependencies.allowlist`), and hand-rolling
  loses histogram bucketing and future scrape compatibility for free.
- **OpenTelemetry SDK** — richer tracing + metrics; rejected for MVP — OTel SDK is NOT in the allowlist
  and would require a PR + human approval for a new dep class; defer to v3/enterprise observability tier.

Must:
<must>
  - M1: Every HTTP request produces a structured JSON log line to stdout at the end of the request,
        containing at minimum: `event`, `method`, `path`, `status_code`, `duration_ms`, `request_id`.
        Middleware generates and binds `request_id` (UUID4) per request via contextvars.
  - M2: Every log line where the tenant is known (post-auth paths: `/v1/*`, `/admin/*`, `/internal/authz`)
        also carries `tenant_id`. Log lines on unauthenticated paths (`/health`, `/internal/metrics`,
        `/internal/health`, pre-auth 401 exits) must NOT carry `tenant_id` (field absent, not null).
  - M3: Key material (plaintext API keys), `Authorization` header values, and JWT token strings must
        NEVER appear in any log line — enforced by sanitising the middleware log context.
  - M4: GET /internal/metrics responds with Prometheus text format 0.0.4 (Content-Type:
        text/plain; version=0.0.4; charset=utf-8) and HTTP 200. The body includes all contracted
        metric families (see §3 CONTRACT).
  - M5: `gateway_circuit_breaker_state` gauge reflects the live breaker state:
        0 = CLOSED, 1 = HALF_OPEN, 2 = OPEN. Updated on every request through the proxy path.
  - M6: `gateway_http_requests_total` counter is incremented on every completed HTTP request,
        labelled by `method` and exact `status_code` ("200", "402", "429", ...). The exit
        criterion requires the 402 (budget) rate specifically — distinguishable from 429
        (rate limit), an operationally opposite signal. Cardinality is bounded: the gateway
        emits ~10 distinct codes. (The duration HISTOGRAM keeps the coarser `status_class`
        label to cap series count — orchestrator decision at review, 2026-06-11.)
  - M7: `gateway_usage_flusher_pending_events` gauge reflects the Redis Stream backlog depth
        (XLEN usage:events). Updated each time /internal/metrics is scraped (lazy read, not background).
  - M8: `gateway_request_duration_seconds` histogram tracks request latency with buckets
        [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0] labelled by `method` and `status_class`.
        Chosen over summary: histograms are aggregatable across instances; summaries are not.
  - M9: `prometheus-client` added to `apps/gateway/pyproject.toml` dependencies; `pyproject.toml`
        already passes `make allowlist` (package is in `.add/dependencies.allowlist`).
  - M10: Metrics are registered on a per-app `CollectorRegistry` (not the global default registry)
         stored on `app.state.metrics_registry`. This prevents test-suite cross-contamination when
         multiple `create_app()` calls exist in a single pytest run.
  - M11: `/internal/metrics` is accessible without authentication (cluster-internal; Envoy blocks
         external access at edge — posture confirmed in PROJECT.md architecture note).
</must>

Reject:
<reject>
  - R1: Any log line containing the literal value of an Authorization header or a raw API key string
        → log sanitiser strips/replaces with "<redacted>" before structlog processes the event.
  - R2: `tenant_id` appearing in a log line produced by a request that did not pass auth
        (unauthenticated path or 401 exit) → field must be absent from the log context.
  - R3: GET /internal/metrics with a malformed or missing Redis connection (XLEN fails) →
        endpoint still returns 200 with all other metrics intact; `gateway_usage_flusher_pending_events`
        is set to -1 (sentinel value signalling "unavailable") rather than dropping the scrape.
  - R4: Registering a prometheus-client metric more than once on the same registry
        (e.g. test teardown + create_app again without clearing the registry) → per-app registry
        prevents `ValueError: Duplicated timeseries` crash.
  - R5: Prometheus scrape of /internal/metrics returning a non-200 status or a body with
        invalid text-format → must always be 200 text/plain version=0.0.4 regardless of upstream
        or DB state (metrics endpoint must not depend on Postgres).
</reject>

After:
<after>
  - Every HTTP access log line in stdout is valid JSON with `request_id` present.
  - A Prometheus scraper hitting GET /internal/metrics receives a 200 text/plain body that
    parses without error and contains `gateway_circuit_breaker_state`,
    `gateway_http_requests_total`, `gateway_usage_flusher_pending_events`, and
    `gateway_request_duration_seconds`.
  - The §7 error-rate, per-rejection (402/429) rate, and upstream-latency monitors can be
    expressed as PromQL queries over the contracted metric names without additional labels.
  - No plaintext key material or Authorization header value exists in any log sink.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ A1 [LOWEST CONFIDENCE] — structlog is already a runtime dependency (`structlog>=25.1` in
     pyproject.toml) but the codebase currently uses `logging.getLogger(__name__)` everywhere
     (recorder.py, flusher.py) — it has NOT been configured as the stdlib bridge. Assumption:
     configuring structlog JSON processor + stdlib bridge in `create_app` startup is safe to add
     without breaking existing log consumers. Confidence low because: (a) existing usage of
     stdlib `logging` may emit duplicate or malformatted lines if structlog bridge is not set up
     correctly; (b) the test suite may capture log output and assert on its format. If wrong:
     the build breaks formatter assertions in existing tests or produces double-log lines in
     production — MEDIUM cost, solvable at build time but not contractually invisible.

  - [ ] A2 — `prometheus-client` is in `.add/dependencies.allowlist` (confirmed: line 19 of the
     allowlist). It is NOT yet in `apps/gateway/pyproject.toml` dependencies. Build must add it;
     `make allowlist` will pass once added.

  - [ ] A3 — Redis XLEN `usage:events` is a safe lazy-read operation on the scrape path (no
     per-request overhead). Pending: confirm XLEN returns 0 when the stream does not yet exist
     (Redis returns 0 for non-existent keys — confirmed by Redis docs; safe).

  - [ ] A4 — The existing test suite does NOT assert on specific log output format; adding
     structlog JSON configuration will not break existing tests. Needs confirmation at build time
     by running the suite.

  - [ ] A5 — `app.state.circuit_breaker` is always a `CircuitBreaker` instance in production
     wiring (confirmed: `main.py` line 65). The metrics reader can safely call
     `app.state.circuit_breaker._state.value` to read the enum string ("closed"/"open"/"half_open")
     and map it to the gauge integer. If the state attribute is renamed during build, tests catch it.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── Must scenarios ──────────────────────────────────────────────────────────

Scenario: M1 — access log line emitted per request with required fields
  Given a running gateway app with structlog JSON configured
  When any HTTP request is made (e.g. GET /health)
  Then stdout contains exactly one JSON log line per request
  And the log line contains keys: event, method, path, status_code, duration_ms, request_id
  And request_id is a non-empty string (UUID4 format)
  And duration_ms is a non-negative number

Scenario: M2 — tenant_id present on authenticated paths, absent on public paths
  Given a running gateway app
  When a request to /v1/chat/completions is made with a valid API key
  Then the access log line for that request contains tenant_id matching the key owner's tenant
  When a request to GET /health is made (unauthenticated path)
  Then the access log line does NOT contain the key tenant_id

Scenario: M3 — Authorization header never logged
  Given a running gateway app
  When a request is made with Authorization: Bearer <some-token>
  Then no log line at any level contains the value of the Authorization header

Scenario: M4 — GET /internal/metrics returns 200 Prometheus text 0.0.4
  Given a running gateway app
  When GET /internal/metrics is requested
  Then the response status is 200
  And the Content-Type header contains "text/plain" and "version=0.0.4"
  And the body is non-empty

Scenario: M5 — breaker state gauge reflects live breaker state
  Given a running gateway app with circuit breaker in CLOSED state
  When GET /internal/metrics is requested
  Then the body contains "gateway_circuit_breaker_state 0.0"
  When the circuit breaker is tripped to OPEN (failure threshold reached)
  And GET /internal/metrics is requested again
  Then the body contains "gateway_circuit_breaker_state 2.0"

Scenario: M6 — http_requests_total counter incremented and labelled by exact status code
  Given a running gateway app
  When GET /health is requested (yields 200)
  Then GET /internal/metrics contains a line matching
       gateway_http_requests_total{method="GET",status_code="200"} with value >= 1.0
  When a request is rejected with a 4xx code (e.g. 401 invalid key)
  Then GET /internal/metrics contains a line with that exact status_code label >= 1.0
  And the 402 budget rate is therefore expressible as rate(...{status_code="402"})

Scenario: M7 — flusher_pending_events gauge reflects Redis stream backlog
  Given a running gateway app with Redis available
  And the usage:events stream has N pending events (N > 0, not yet flushed)
  When GET /internal/metrics is requested
  Then the body contains "gateway_usage_flusher_pending_events N.0"
  When all events are flushed (stream empty)
  And GET /internal/metrics is requested
  Then the body contains "gateway_usage_flusher_pending_events 0.0"

Scenario: M8 — request_duration_seconds histogram present in metrics output
  Given a running gateway app
  When at least one HTTP request has been processed
  And GET /internal/metrics is requested
  Then the body contains "gateway_request_duration_seconds_bucket"
  And the body contains "gateway_request_duration_seconds_sum"
  And the body contains "gateway_request_duration_seconds_count"

Scenario: M10 — per-app registry prevents duplicate-registration crash
  Given two separate create_app() calls in the same process (as occurs in the test suite)
  When both apps handle a request
  Then no ValueError about duplicated timeseries is raised

# ── Reject scenarios ────────────────────────────────────────────────────────

Scenario: R1 — Authorization header value never appears in logs
  Given a running gateway app
  When a request is made with Authorization: Bearer supersecrettoken99
  Then searching all log output for "supersecrettoken99" yields no match
  And the request is processed normally (non-secret fields still logged)

Scenario: R2 — tenant_id absent from log lines on unauthenticated paths
  Given a running gateway app
  When GET /health is requested (no auth header)
  Then the JSON log line for the request does not contain the key "tenant_id"
  And the log line does contain "request_id"

Scenario: R3 — metrics endpoint returns 200 even when Redis XLEN fails
  Given a running gateway app with a broken Redis (raises on XLEN)
  When GET /internal/metrics is requested
  Then the response status is 200
  And the body contains "gateway_usage_flusher_pending_events -1.0"
  And the body still contains "gateway_circuit_breaker_state"
  And the body still contains "gateway_http_requests_total"

Scenario: R4 — per-app registry prevents duplicate-registration ValueError
  Given two create_app() calls in the same process (simulating test setup re-entry)
  When each app serves a request
  Then no ValueError("Duplicated timeseries") is raised at any point

Scenario: R5 — metrics endpoint returns 200 regardless of Postgres state
  Given a running gateway app with Postgres unreachable (no real DB connection needed here)
  When GET /internal/metrics is requested
  Then the response status is 200
  And the body is valid Prometheus text format (contains at least one metric family)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /internal/metrics
  200 -> body: Prometheus text format 0.0.4
         Content-Type: text/plain; version=0.0.4; charset=utf-8
         Required metric families (names, types, labels):

         # HELP gateway_circuit_breaker_state Circuit breaker state: 0=closed 1=half_open 2=open
         # TYPE gateway_circuit_breaker_state gauge
         gateway_circuit_breaker_state 0.0   (or 1.0 or 2.0)

         # HELP gateway_http_requests_total Total HTTP requests by method and exact status code
         # TYPE gateway_http_requests_total counter
         gateway_http_requests_total{method="<METHOD>",status_code="<NNN>"} <float>

         # HELP gateway_usage_flusher_pending_events Pending events in the usage Redis stream; -1 if Redis unavailable
         # TYPE gateway_usage_flusher_pending_events gauge
         gateway_usage_flusher_pending_events <float>   (-1.0 on Redis error)

         # HELP gateway_request_duration_seconds Request duration in seconds
         # TYPE gateway_request_duration_seconds histogram
         gateway_request_duration_seconds_bucket{le="0.005",method="<METHOD>",status_class="<Nxx>"} <float>
         ... (standard histogram bucket/sum/count lines)

  (no 4xx/5xx — endpoint never fails; Redis/DB errors produce sentinel values, not HTTP errors)

Access log line (one per request, JSON, to stdout via structlog):
  Required fields:
    event:        "http_request"   (literal string)
    method:       HTTP method string (e.g. "GET")
    path:         request path string (e.g. "/v1/chat/completions")
    status_code:  integer HTTP status
    duration_ms:  float, milliseconds elapsed
    request_id:   UUID4 string, generated per-request by middleware, bound via contextvars
  Conditional fields (present only when available):
    tenant_id:    UUID string — present on authenticated paths (/v1/*, /admin/*, /internal/authz);
                  ABSENT (not null, not empty) on unauthenticated paths and pre-auth 401 exits
  Prohibited fields / values:
    Authorization header value — MUST NOT appear at any log level
    Raw API key strings — MUST NOT appear at any log level
    JWT token strings — MUST NOT appear at any log level

Modules touched:
  NEW:   gateway/observability/__init__.py
         gateway/observability/metrics.py       — MetricsRegistry, expose_metrics(), state_value()
         gateway/observability/middleware.py    — RequestIdMiddleware (adds request_id, access log)
         gateway/observability/logging_config.py — configure_structlog() called at app startup
  WIRED: gateway/main.py — add_middleware(RequestIdMiddleware), configure_structlog(),
                            app.state.metrics_registry = MetricsRegistry(registry=CollectorRegistry()),
                            wire /internal/metrics route
  pyproject.toml — add prometheus-client>=0.21 to [project.dependencies]

Dep allowlist: prometheus-client is already in .add/dependencies.allowlist (line 19).
  Required pyproject.toml addition: "prometheus-client>=0.21"
  make allowlist will pass after this addition (no new allowlist PR needed).

structlog configuration: configure_structlog() sets up:
  - JSON renderer (structlog.processors.JSONRenderer)
  - stdlib logging bridge so existing logging.getLogger() calls emit JSON via structlog
  - TimeStamper ISO format
  - Called once at create_app() time; idempotent (structlog.is_configured() guard)

PromQL monitor queries (§7 wiring):
  Error rate:          rate(gateway_http_requests_total{status_code=~"5.."}[5m])
  402 (budget) rate:   rate(gateway_http_requests_total{status_code="402"}[5m])
  429 (rate-limit) rate: rate(gateway_http_requests_total{status_code="429"}[5m])
  Upstream latency p99: histogram_quantile(0.99, rate(gateway_request_duration_seconds_bucket[5m]))
```

Status: FROZEN @ v2 — approved by Tin Dang (delegated auto mode, 2026-06-11).

Amendment (change-request disposition, executed by orchestrator 2026-06-11):
| test | defect | disposition |
|---|---|---|
| test_tenant_id_present_on_authenticated_path | arrange posted to /tenants/signup + /tenants/login — routes that have NEVER existed (canonical: /admin/auth/*). The build agent satisfied the defective arrange by adding an unauthenticated /tenants compat router — rejected at orchestrator review: expands public auth surface and exceeds this contract's "Modules touched" list. | arrange revised to canonical /admin/auth/signup (with required tenant_name) + /admin/auth/login; every assertion unchanged (tenant_id must appear in the access log). Compat router removed from main.py. No behavior weakened — the test now exercises the real product surface. |

Least-sure flag surfaced at freeze:
⚠ [spec/A1] the structlog→stdlib bridge may double-emit or break existing log-capture
  assumptions in the 78-test suite — lowest confidence because the codebase currently uses
  bare logging.getLogger() everywhere; cost if wrong: build-time test breakage (contained,
  fixable in build without touching frozen artifacts).
⚠ [contract] the duration histogram keeps the coarse status_class label (series-count cap),
  so per-status-code latency is NOT observable — cost if wrong: a breaking label extension
  on the histogram timeseries in a later task. The requests COUNTER carries exact status_code,
  so the 402/429 monitor rates required by the v2 exit criterion are fully expressible.

<!-- Freeze flag working notes (resolved candidates):

[spec/A1] — structlog stdlib bridge may produce duplicate or malformatted log lines if existing
  `logging.getLogger()` calls are not correctly captured. Cost: broken test assertions or
  double-log lines in production. Must verify at build time by running full suite with bridge active.

[contract] — RESOLVED at orchestrator review (2026-06-11): the counter now uses exact
  `status_code` (the v2 exit criterion demands the 402 rate specifically; a 4xx aggregate
  conflates budget exhaustion with rate-limiting). Residual flag: the duration HISTOGRAM keeps
  the coarse `status_class` label to cap series count — per-code latency is NOT observable;
  cost if wrong: a follow-up label extension (breaking timeseries change) for the histogram only.

Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
contract = change request back to SPECIFY. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_access_log_fields:
      arrange: create_app + TestClient + capture structlog JSON output
      act: GET /health
      assert: one JSON log line with event/method/path/status_code/duration_ms/request_id

  - test_tenant_id_present_on_authenticated_path:
      arrange: create_app + TestClient + fake auth producing known tenant_id
      act: POST /v1/chat/completions with valid key (fake upstream, no real OpenRouter)
      assert: access log line contains tenant_id == known UUID

  - test_tenant_id_absent_on_public_path:
      arrange: create_app + TestClient
      act: GET /health (no auth)
      assert: access log line has no "tenant_id" key

  - test_authorization_header_never_logged:
      arrange: create_app + TestClient + capture all log output
      act: any request with Authorization: Bearer supersecrettoken99
      assert: no captured log line (at any level) contains "supersecrettoken99"

  - test_metrics_endpoint_200_text_format:
      arrange: create_app + TestClient
      act: GET /internal/metrics
      assert: status 200, Content-Type contains text/plain and version=0.0.4, body non-empty

  - test_metrics_breaker_state_closed:
      arrange: create_app + TestClient (circuit breaker starts CLOSED)
      act: GET /internal/metrics
      assert: body contains "gateway_circuit_breaker_state 0.0"

  - test_metrics_breaker_state_open:
      arrange: create_app + TestClient, trip breaker to OPEN via record_failure() calls
      act: GET /internal/metrics
      assert: body contains "gateway_circuit_breaker_state 2.0"

  - test_metrics_requests_counter_2xx:
      arrange: create_app + TestClient
      act: GET /health (→ 200), then GET /internal/metrics
      assert: body contains gateway_http_requests_total{method="GET",status_code="200"} >= 1.0

  - test_metrics_requests_counter_4xx_exact_code:
      arrange: create_app + TestClient
      act: a request rejected 4xx (invalid key → 401), then GET /internal/metrics
      assert: body contains the exact status_code label (e.g. "401") counter >= 1.0
              (proves exact-code labelling, which is what makes the 402 rate expressible)

  - test_metrics_flusher_pending_events_count:
      arrange: create_app(fake_redis) where fake_redis.xlen() returns known N
      act: GET /internal/metrics
      assert: body contains "gateway_usage_flusher_pending_events N.0"

  - test_metrics_flusher_pending_events_redis_down:
      arrange: create_app(broken_redis) where xlen() raises
      act: GET /internal/metrics
      assert: status 200, body contains "gateway_usage_flusher_pending_events -1.0"
              AND contains "gateway_circuit_breaker_state"

  - test_metrics_duration_histogram_present:
      arrange: create_app + TestClient
      act: GET /health, then GET /internal/metrics
      assert: body contains _bucket, _sum, _count lines for gateway_request_duration_seconds

  - test_per_app_registry_no_duplicate_error:
      arrange: two separate create_app() calls in the same process
      act: each app handles one GET /health request
      assert: no ValueError raised, both /internal/metrics return 200

  - test_metrics_200_when_redis_xlen_fails:
      arrange: create_app with broken Redis XLEN
      act: GET /internal/metrics
      assert: 200, body valid, other metrics present

  - test_metrics_200_regardless_of_db_state:
      arrange: create_app with no real DB wiring (in-memory enough, TestClient)
      act: GET /internal/metrics
      assert: 200, body contains gateway_circuit_breaker_state
</test_plan>

Tests live in: `apps/gateway/tests/observability/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): structlog configuration must be idempotent (guard with `structlog.is_configured()`); per-app CollectorRegistry must be used — NEVER the global prometheus_client default registry; Authorization header values and raw key strings must never be bound to any log context variable.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — tests/observability 16/16; full suite 120 passed, 19 deselected (e2e marker); make ci exit 0 (lint+typecheck+allowlist+test)
- [x] coverage did not decrease — 85.07% vs 83.39% pre-task (floor 80%)
- [x] no test or contract was altered during build — one exception, executed by the ORCHESTRATOR (not the builder) as a change-request disposition recorded in §3: the defective frozen arrange posting to never-existent /tenants/* routes was revised to canonical /admin/auth/*; every assertion unchanged. The builder's compat-router workaround was rejected and removed at review.
- [x] concurrency / timing — request_id/tenant_id live in contextvars (per-request isolation under asyncio); middleware is pure ASGI so handler-context bindings remain visible at log emission; per-app CollectorRegistry removes cross-app races; prometheus-client counters/histograms are thread/async safe; XLEN is a single read with sentinel fallback
- [x] no exposed secrets / injection / unexpected deps — middleware never reads the Authorization header; test_authorization_header_never_logged proves no leak; only new dep is allowlisted prometheus-client; /internal/metrics carries no tenant data and is edge-blocked (403) at Envoy
- [x] layering — observability module has no domain deps; main.py wires it (composition root); jwt_service (infrastructure) binding structlog contextvars is an infra-layer side effect, sanctioned; metrics.py imports STREAM_KEY from usage.infrastructure to avoid constant drift (cross-module infra-constant import, noted)
- [x] reviewed — orchestrator line-by-line review under delegated auto mode: caught and reversed the unauthenticated /tenants compat router (public-surface expansion beyond the contract's Modules-touched list); added the missing M2 /admin/* tenant_id binding at JwtTokenService.decode

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — configure_structlog (main.py:72), MetricsRegistry (main.py:78), expose_metrics (/internal/metrics route main.py:55), RequestIdMiddleware (app.add_middleware main.py:155, deliberately outermost); bind_contextvars at proxy use_cases._authenticate and tenants jwt_service.decode — both confirmed by grep + green behavior tests
- [x] DEAD-CODE (code) — state_value used by expose_metrics; test helper FakeBrokenBudgetGuard removed when its test was superseded; no orphaned symbols (ruff + review)
- [x] SEMANTIC (prose) — §3 contract re-read in full post-build: metric names/labels/buckets, 0.0.4 content type (hardcoded because prometheus-client ≥0.14 CONTENT_TYPE_LATEST is OpenMetrics 1.0.0), log field set, and PromQL monitor queries all match the implementation

### GATE RECORD
Outcome: PASS (auto-resolved under autonomy: auto — complete evidence; security checks affirmative; the one frozen-artifact change was an orchestrator-executed, §3-documented disposition that strengthened the test)
Reviewed by: Claude (orchestrator, delegated auto mode for Tin Dang) · date: 2026-06-11

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
  - Error rate:   rate(gateway_http_requests_total{status_code=~"5.."}[5m]) > 0.01
  - 402 (budget exceeded) rate: rate(gateway_http_requests_total{status_code="402"}[5m]) > 0.05
  - 429 (rate limit) rate:      rate(gateway_http_requests_total{status_code="429"}[5m]) > 0.05
  - Upstream latency p99:
                  histogram_quantile(0.99, rate(gateway_request_duration_seconds_bucket[5m])) > 2.0
  - Breaker open: gateway_circuit_breaker_state == 2
  - Flusher lag:  gateway_usage_flusher_pending_events > 1000

Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
