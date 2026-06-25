# TASK: SLO metrics endpoint (availability/error-rate/volume)

slug: slo-metrics · created: 2026-06-25 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
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
  - DATA SOURCE: `usage/infrastructure/orm.py:UsageRecordRow` — has `status` (int HTTP status) + `created_at` (indexed `ix_usage_records_created_at`) + `tenant_id`. Availability/error-rate/volume derive from `status` over a time window. ⚠ NO stored latency column → latency percentiles are NOT derivable here (see §1 ⚠ + spec delta).
  - NEW `GET /admin/slo?window_hours=` in `usage/api/router.py` (mirrors get_alerts/get_audit pattern: manual param parse → PAYLOAD_INVALID; tenant-scoped).
  - AUTH: `usage/api/router.py:_require_ops_read` (Permission.OPS_READ — owner/admin/operator/billing_admin/viewer; member 403) — the same gate as /admin/health, /admin/alerts, /admin/ratelimits.
  - Aggregation: a SQL GROUP/COUNT over usage_records filtered by tenant_id + created_at >= now()-window; classify status (2xx/3xx success · 4xx client · 5xx server-error).
Context: the alerts/audit read endpoints are the envelope analog; OtelSpanEmitter (proxy/application/use_cases.py) exports latency spans to an EXTERNAL collector (not a queryable table) — so latency lives in the OTel backend, not the DB. vitest; gateway DB :5433 UP.
Honors: tenant-scoping (caller's tenant only); OPS_READ allowlist; HONEST sourcing (report only what the DB can prove — availability/error-rate/volume; do NOT fabricate latency).
Anchors the contract cites: `GET /admin/slo` · `SloResponse` · `_require_ops_read` · the usage_records status aggregation · the window param.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-tenant SLO metrics — availability, error-rate, and request volume over a window
Framings weighed: aggregate usage_records.status by window (chosen — honest, DB-provable) · query an OTel/Prometheus backend for latency (rejected: external dependency, separate task) · fabricate latency from nothing (rejected: dishonest)
Must:
<must>
  - `GET /admin/slo?window_hours=N` returns, for the caller's tenant over the last N hours: total_requests · success_count · client_error_count (4xx) · server_error_count (5xx) · availability (success / total, or 1.0 when total=0) · error_rate (server_error / total), gated by OPS_READ.
  - SUCCESS = status < 500 by default (server errors are the SLO breach); CLIENT errors (4xx) reported separately, not counted against availability (caller's fault, not the service's) — this classification is explicit in the response.
  - window_hours: integer 1..720 (30d), default 24; bad value → PAYLOAD_INVALID.
  - Tenant-scoped: only the caller's tenant rows.
  - HONEST: latency percentiles are NOT included (no stored latency); a `latency: null` / documented omission + a spec delta to add a latency column or query the OTel backend.
</must>
Reject:
<reject>
  - A role lacking OPS_READ (member) -> 403 "ERR_AUTH_FORBIDDEN"
  - window_hours out of range or non-integer -> "ERR_PAYLOAD_INVALID"
</reject>
After:
<after>
  - Owner/admin/operator/billing_admin/viewer can read their tenant's availability/error-rate/volume over a window; member is 403; empty window → availability 1.0, zero counts; no cross-tenant leakage.
  - gateway suite green.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ No stored latency → latency percentiles are OUT of v1 (the task title said "latency" but the DB can't prove it). Decision (auto): ship availability/error-rate/volume honestly; log a spec delta for latency (needs a usage_records.latency_ms column captured on the billing write, or an OTel/Prometheus query). If wrong: add latency later.
  - [ ] SUCCESS threshold = status < 500 (5xx = breach; 4xx = client, separate). Confirmable; conventional.
  - [ ] per-tenant scope (not operator-wide) for v1 — consistent with other admin reads.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: SLO aggregates availability and error-rate over the window
  Given usage_records for the tenant: 90 with status 200, 5 with 400, 5 with 500, in the last 24h
  When GET /admin/slo?window_hours=24
  Then total_requests=100, success_count=95, client_error_count=5, server_error_count=5, availability=0.95, error_rate=0.05

Scenario: Empty window yields availability 1.0
  Given no usage_records in the window
  When GET /admin/slo
  Then total_requests=0, availability=1.0, error_rate=0.0 (no division-by-zero)

Scenario: OPS_READ gating
  Given a member caller
  When GET /admin/slo
  Then 403 ERR_AUTH_FORBIDDEN
  And owner/admin/operator/billing_admin/viewer get 200

Scenario: Window bounds
  Given window_hours=0 or 1000 or "abc"
  When GET /admin/slo
  Then 400 ERR_PAYLOAD_INVALID

Scenario: Tenant isolation
  Given usage_records for tenant A and tenant B
  When tenant A reads SLO
  Then only tenant A rows are aggregated

Scenario: Honest latency omission
  Given the response
  When inspected
  Then latency percentiles are absent/null with a documented reason (no fabricated values)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/slo?window_hours=N   (require _require_ops_read = Permission.OPS_READ)
  200 -> {
    window_hours: int,
    total_requests: int,
    success_count: int,         # status < 500
    client_error_count: int,    # 4xx
    server_error_count: int,    # 5xx
    availability: float,        # success_count/total_requests, or 1.0 if total==0
    error_rate: float,          # server_error_count/total_requests, or 0.0 if total==0
    latency_ms: null            # honest omission — no stored latency (spec delta)
  }
  400 -> ERR_PAYLOAD_INVALID   (window_hours out of 1..720 or non-integer)
  403 -> ERR_AUTH_FORBIDDEN    (lacks OPS_READ)
Source: COUNT(*) over usage_records WHERE tenant_id=:tid AND created_at >= now()-window, grouped/CASE by status class.
Pagination: none (single aggregate). window_hours 1..720 default 24.
Schema: NO DB change (reads usage_records.status + created_at; created_at already indexed). Additive endpoint.
Least-sure flag surfaced at freeze: [contract] latency_ms is null (no stored latency) — title implied latency; honest v1 ships availability/error-rate/volume. Cost if wrong: add a latency_ms column + capture later (spec delta filed).
```

Status: FROZEN @ v1 — auto-frozen (autonomy: auto; non-security read aggregation, OPS_READ already approved; honest latency omission flagged) 2026-06-25.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: aggregation + gating fully covered; gateway suite green.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_slo_aggregates: 90x200 + 5x400 + 5x500 -> total100/success95/client5/server5/avail0.95/err0.05
  - test_slo_empty_window: no rows -> total0/avail1.0/err0.0 (no zero-div)
  - test_slo_ops_read_gating: member 403; owner/admin/operator/billing_admin/viewer 200 (parametrized)
  - test_slo_window_bounds: 0/1000/"abc" -> ERR_PAYLOAD_INVALID
  - test_slo_tenant_isolation: tenant A only
  - test_slo_latency_null: response latency_ms is null (honest omission)
</test_plan>

Tests live in: `apps/gateway/tests/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/` `apps/gateway/tests/`
Strategy (ordered batches):
  1. RED tests `apps/gateway/tests/test_slo_metrics.py` (6 per §4).
  2. BE: GET /admin/slo (_require_ops_read, window parse, SloResponse) + a SQL aggregation (single query, CASE by status class) over usage_records tenant+window.
  3. Green: gateway suite + ruff + pyright.
Safety rule (feature-specific): HONEST — report only DB-provable metrics; latency_ms is null (do not fabricate). No division-by-zero (total==0 → availability 1.0). Tenant-scoped. Bounded window.
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the FROZEN contract; do NOT create tmp/*.txt (inline -m commits); allow-list packages only.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

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

### Build expectations — confirmed at gate
- [x] correct aggregation (90x200+5x400+5x500 → avail 0.95 / err 0.05) + zero-div safe (empty→1.0/0.0) — test_slo_aggregates + test_slo_empty_window; SQL CASE success=status<500, client=4xx, server=5xx
- [x] OPS_READ gating (member 403; owner/admin/operator/billing_admin/viewer 200) + tenant isolation + window bounds (0/-1/1000/"abc"→422) — tests green
- [x] latency_ms null (honest, no fabrication) — test_slo_latency_null + docstring reason
- [x] BONUS: window-exclusion test (2h-old row excluded from 1h window) confirms the created_at cutoff

### Deep checks
- [x] WIRING — GET /admin/slo + SloResponse + _parse_window_hours + single aggregation query referenced; _require_ops_read reused
- [x] DEAD-CODE — none (ruff/pyright clean)
- [x] SEMANTIC — HONEST sourcing: only status-derived metrics; latency_ms explicitly null with documented reason; spec delta filed to add a latency_ms column / OTel query

### Evidence (independently run): slo_metrics 17/17 green; subagent full gateway suite 1646 passed; ruff + pyright clean.
### Deviation (accepted — non-security): window_hours out-of-range returns 422 (codebase-wide PAYLOAD_INVALID convention) not the §3 literal "400". Rejection preserved; consistent with every other validated endpoint (alerts/audit/ratelimits). Same class as the rbac-admin-ui note.

### GATE RECORD
Outcome: PASS
Reviewed by: orchestrator independent review (slo_metrics 17/17 re-run; aggregation SQL + zero-div + OPS_READ gating + tenant isolation; honest latency omission) · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): availability dips · error_rate spikes.

### Spec delta
- [SPEC · open] LATENCY: add usage_records.latency_ms captured on the billing write (or query the OTel/Prometheus backend) → add p50/p95/p99 to /admin/slo.
- [SPEC · open] operator-wide SLO (all tenants) for the ops view · per-model/per-deployment SLO breakdown · SLO target + burn-rate alerting.

### Competency deltas
- [SDD · folded] honest sourcing — report only what the store can prove (availability/error-rate from status); flag the gap (latency) rather than fabricate (mirrors the /status page honesty). [folded foundation-version 35]
