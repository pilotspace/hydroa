# TASK: e2e double-pass: multi-deployment alias distributes by strategy, honors per-deployment limits, fallback+cooldown remove failures, chat billing unaffected — LIVE through the TLS edge

slug: v8-live-verify · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: v8 live close harness — a multi-deployment router stub + v8 compose overlay +
double-pass exit-criteria verification of the whole v8 router surface through the governed/billed
TLS edge: a model alias over MULTIPLE deployments distributes by the configured routing strategy,
skips a per-deployment-saturated deployment (clean 429 when all saturated), v6 fallback+cooldown
still remove failed/unhealthy deployments, and chat billing keys on the SERVED deployment id.

Framings weighed:
  - **New host-process v8 router stub (:9922) + v8 overlay + unified verify script (chosen)**: a
    host-level HTTP server on 127.0.0.1:9922 speaks the OpenRouter chat surface, routes by request
    `model`, keeps a per-deployment call counter exposed at GET /__counters, and honors a /__faults
    table (reused v6 idiom) so a deployment can be forced to fail/recover. A v8 overlay points
    GATEWAY_OPENROUTER_BASE_URL at :9922, sets GATEWAY_ROUTING_STRATEGY=simple-shuffle, and declares
    the v8 model groups (weighted, limited, all-saturated, fallback, billing aliases) plus the v6
    cooldown knobs. The verify script seeds the stub model ids (+pricing) via raw SQL, drives chat
    through https://localhost:8443, reads /__counters for distribution, pre-seeds Redis deplimit
    windows for deterministic saturation, and polls usage_records for billing. This is the exact
    idiom of live_v6/v7_verify.py + v6_fault_stub.py. NO gateway-source change — pure harness.
  - **Reuse the frozen v6 stub on :9920 (rejected)**: the v6 stub has no /__counters readout and is
    frozen harness for the v6 close; extending it risks the v6 double-pass invariant. A distinct
    :9922 stub keeps v6 untouched and adds only the counters readout v8 distribution needs.
  - **Fire N requests to organically saturate a deployment (rejected for the limit checks)**:
    non-deterministic and slow; instead pre-seed the Redis deplimit:rpm window key for the current
    bucket to the limit (the gate reads it READ-ONLY) — deterministic, fast, and exercises the real
    is_saturated peek path. Distribution (C1) DOES fire many requests (statistical, the only honest
    way to prove weighted-random), with a generous-margin assertion.

NO gateway-source change: the v8 router (routing-strategy + balance-strategies + deployment-limits,
all DONE) is already wired in main.py (RoutingStrategy from GATEWAY_ROUTING_STRATEGY; limit_gate
wired when any deployment declares a limit). v8-live-verify is a PURE HARNESS task — its evidence is
the live double-pass run, not a unit suite (the live_v5/v6/v7 precedent: harness artifacts have no
red suite; §4 is the executable check list C1–C7, the verify script IS the test).

Must:
<must>
  - Stub `scripts/v8_router_stub.py` MUST listen on 127.0.0.1:9922 (NEVER 0.0.0.0) and speak the
    OpenRouter chat surface the gateway calls (base_url + "/chat/completions"):
    - POST /api/v1/chat/completions → routes by body `model`; default behavior "ok" → 200 a
      well-formed non-streaming completion whose `model` field ECHOES the served model id and whose
      `usage` carries prompt/completion/total tokens > 0 (so billing has a non-zero quantity).
    - POST /__faults {"model": <id>, "behavior": <behavior>} → per-model behavior; reuse the v6
      behaviors at minimum "ok" | "fail_5xx" | {"fail_n": N}. Resets that model's call counter.
    - GET /__counters → 200 JSON {"<model_id>": <int calls since last fault-config>, ...} — the
      distribution + skip readout (v8-new; the v6 stub lacks it).
    It MUST expose `make_stub_server()` + `start_stub_in_thread(server)` like v6_fault_stub.py, and
    bind 127.0.0.1 only (asserted in the verify script).
  - Overlay `infra/docker-compose.e2e.v8.yml` MUST compose ADDITIVELY on top of base+v4+v5+v6 and
    set on the gateway service ONLY these (overriding the v6 values where noted):
      GATEWAY_OPENROUTER_BASE_URL="http://host.docker.internal:9922/api/v1"  (→ v8 stub)
      GATEWAY_ROUTING_STRATEGY="simple-shuffle"
      GATEWAY_MODEL_GROUPS=<the v8 aliases below, as JSON>
      (it keeps the v6 cooldown knobs: FAILURE_THRESHOLD=2, TTL_S=5, WINDOW_S=60, and MAX_RETRIES.)
    The v8 model groups (FROZEN shapes in §3):
      "v8-dist"   : [{model_id:"stub/dep-a",weight:1}, {model_id:"stub/dep-b",weight:3}]  (no limits)
      "v8-limit"  : [{model_id:"stub/lim-a",rpm_limit:5}, {model_id:"stub/lim-b"}]
      "v8-allsat" : [{model_id:"stub/sat-a",rpm_limit:3}, {model_id:"stub/sat-b",rpm_limit:3}]
      "v8-fb"     : ["stub/fb-primary","stub/fb-secondary"]   (bare strings = v6 shape, no limits)
      "v8-bill"   : [{model_id:"stub/bill-a",weight:1}, {model_id:"stub/bill-b",weight:1}]
    Because ≥1 deployment declares a limit, the gateway wires the limit_gate (the no-limit aliases
    hit the zero-Redis fast path → never saturate → v6-byte-identical).
  - `scripts/live_v8_verify.py` MUST verify every v8 exit criterion through the TLS edge
    (https://localhost:8443) with a fresh run_id every invocation, seeding the eight stub model ids
    (provider='openrouter', active=true) + pricing_snapshots (per_token, non-zero), and asserting
    via /__counters, the HTTP status, and usage_records rows (poll ≤30 s). Criteria (C1–C7):
    - C1 DISTRIBUTION: GATEWAY_ROUTING_STRATEGY=simple-shuffle; fire ≥40 chat requests to "v8-dist";
      read /__counters; assert BOTH dep-a and dep-b served (count>0 each → not always-first) AND
      dep-b served strictly more than dep-a (weight 3 vs 1 honored; generous margin, not exact).
    - C2 LIMIT SKIP: pre-seed Redis gateway:deplimit:rpm:stub/lim-a:{bucket}=5 (== rpm_limit);
      fire several chat requests to "v8-limit"; assert ALL are served by stub/lim-b (lim-a skipped
      at selection), each 200, and zero increase in lim-a's /__counters.
    - C3 ALL SATURATED → 429: pre-seed Redis deplimit:rpm for BOTH stub/sat-a and stub/sat-b to 3
      (== rpm_limit); POST chat to "v8-allsat"; assert 429 ERR_RATE_LIMITED (NOT 500), a Retry-After
      header, and that NEITHER sat deployment's /__counters increased (no upstream call) and NO
      usage_records row was written.
    - C4 FALLBACK: /__faults set stub/fb-primary → fail_5xx; POST chat to "v8-fb"; assert 200 served
      by stub/fb-secondary (the primary failed and fallback removed it); exactly one usage_records
      row billed on stub/fb-secondary.
    - C5 COOLDOWN: keep stub/fb-primary failing; drive ≥2 failures (threshold=2) then assert the
      cooldown gate marks fb-primary cooled (subsequent "v8-fb" requests skip it, served by
      fb-secondary) and, after TTL_S(5s)+catalog recovery (clear fault), fb-primary is eligible
      again (half-open recovery) — distinct from C3 saturation (503-vs-429 already unit-proven).
    - C6 BILLING: a successful "v8-bill" chat produces EXACTLY ONE usage_records row whose model_id
      is the SERVED deployment id (stub/bill-a or stub/bill-b — one of the two, matching /__counters),
      NEVER the alias "v8-bill" and NEVER response_body["model"] if it differed; cost_usd > 0.
    - C7 GOVERNANCE + TLS intact: a chat to "v8-dist" with NO bearer → 401, zero usage_records rows;
      all checks ran through https://localhost:8443 with a fresh run_id (chat governance unaffected).
  - SECURITY (verbatim foundation rule): the stub binds 127.0.0.1 only (asserted); NO real key is
    read/logged/echoed/committed; GATEWAY_OPENROUTER_API_KEY stays the NON-SECRET placeholder
    "stub-openrouter-key" from the v6 overlay (an empty bearer fails client-side — the v7 lesson);
    Redis keys + logs carry deployment_id only; no .env file is read by the harness.
  - Double-pass close rule: `live_v8_verify.py` is run TWICE in sequence; BOTH must exit 0 with
    "ALL CRITERIA PASS". Each pass resets gateway+Redis state and uses a fresh run_id so the two
    passes are independent (no cross-pass contamination of counters/cooldown/limit windows).
</must>
Reject:
<reject>
  - every deployment in "v8-allsat" saturated → the chat returns 429 ERR_RATE_LIMITED + Retry-After
    (NEVER a 500, NEVER an upstream call, NEVER a usage_records row) — C3.
  - the stub bound to anything other than 127.0.0.1 → HARD-STOP security finding (it must be
    loopback-only; asserted before any check runs).
  - any gateway SOURCE or frozen-test change to make the live run pass → "ERR_FROZEN_VIOLATION":
    v8-live-verify is harness-only (scripts/ + infra/); `git show --stat` MUST list only
    scripts/v8_router_stub.py + scripts/live_v8_verify.py + infra/docker-compose.e2e.v8.yml.
  - a distribution that is always-first (dep-b count == 0 OR dep-a count == 0 under simple-shuffle)
    → C1 FAIL (the router is not distributing / not honoring weight).
</reject>
After:
<after>
  - The whole v8 router surface is proven LIVE end-to-end through the real create_app wiring + Envoy
    TLS edge + Redis + Postgres billing: distribution by strategy (C1), usage-based skip (C2),
    all-saturated 429 (C3), v6 fallback (C4) + cooldown (C5), served-id billing (C6), governance
    intact (C7) — twice, cleanly.
  - The last v8 MILESTONE exit criterion ("proven LIVE … two consecutive clean passes") is met;
    v8 can close (milestone-done) and fold into foundation v9.
  - A no-limit / bare-string alias ("v8-fb") behaves as v6 (byte-identical chat path) under the live
    stack — the back-compat invariant holds through the real edge, not just in unit fakes.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE: that pre-seeding the Redis `gateway:deplimit:rpm:{id}:{bucket}` key to the
    limit reliably forces is_saturated=True for the request's window — the bucket is
    `floor(time.time()/60)`, so a request landing in the NEXT minute reads a fresh (empty) bucket
    and the skip would not fire. Lowest confidence because it is a wall-clock race at the minute
    boundary. Mitigation: the verify script computes the bucket itself and seeds BOTH the current
    and the next bucket (and re-seeds if it detects a rollover), and fires the C2/C3 requests
    immediately after seeding; if wrong the cost is a flaky C2/C3 (false 200 instead of skip/429),
    caught by the double-pass and re-run.
  - [ ] simple-shuffle over weight 1:3 with ≥40 samples gives dep-b > dep-a with overwhelming
    probability (expected ~10:30) — confirm the margin assertion is loose enough (dep-b > dep-a AND
    both > 0) to never flake on a fair shuffle, while still catching always-first. If wrong: a rare
    C1 flake; caught by double-pass + re-run.
  - [ ] the v8 overlay's GATEWAY_OPENROUTER_BASE_URL override fully redirects chat to :9922 and the
    v6 stub on :9920 is irrelevant to the v8 run (the verify script auto-starts the :9922 stub) —
    confirm no v6-overlay key silently re-points chat. If wrong: C1 served-by counters read empty.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: distribution by strategy (C1)
  Given a "v8-dist" alias over deployments stub/dep-a (weight 1) and stub/dep-b (weight 3)
    And GATEWAY_ROUTING_STRATEGY=simple-shuffle
  When 40+ chat completions are sent to "v8-dist" through https://localhost:8443
  Then GET /__counters shows both dep-a>0 and dep-b>0 (not always-first)
    And dep-b's count is strictly greater than dep-a's (weight honored)
    And the chat success/billing path is unchanged from v6

Scenario: per-deployment limit skip (C2)
  Given "v8-limit" = [stub/lim-a (rpm_limit 5), stub/lim-b (no limit)]
    And the Redis rpm window for stub/lim-a is pre-seeded to 5 (saturated)
  When several chat completions are sent to "v8-limit"
  Then every request is 200 served by stub/lim-b (lim-a skipped at selection)
    And stub/lim-a's /__counters did not increase

Scenario: all deployments saturated → clean 429 (C3)
  Given "v8-allsat" with both stub/sat-a and stub/sat-b at their rpm_limit (saturated)
  When a chat completion is sent to "v8-allsat"
  Then the response is 429 ERR_RATE_LIMITED with a Retry-After header
    And no upstream call was made (neither sat deployment's counter increased)
    And no usage_records row was written (never a 500)

Scenario: v6 fallback removes a failed deployment (C4)
  Given "v8-fb" = [stub/fb-primary, stub/fb-secondary] and fb-primary forced fail_5xx
  When a chat completion is sent to "v8-fb"
  Then the response is 200 served by stub/fb-secondary
    And exactly one usage_records row is billed on stub/fb-secondary

Scenario: v6 cooldown then half-open recovery (C5)
  Given fb-primary keeps failing past the cooldown threshold (2)
  When subsequent "v8-fb" requests are sent
  Then fb-primary is skipped (cooled) and fb-secondary serves
    And after TTL (5s) with the fault cleared, fb-primary becomes eligible again

Scenario: billing keys on the served deployment id (C6)
  Given a successful "v8-bill" chat distributed to one of stub/bill-a / stub/bill-b
  When the flusher writes usage
  Then exactly one usage_records row exists with model_id = the SERVED deployment id
    And it is never the alias "v8-bill" and cost_usd > 0

Scenario: governance + TLS intact (C7)
  Given the v8 router is active
  When a chat to "v8-dist" is sent with NO bearer token through the TLS edge
  Then the response is 401 and zero usage_records rows are written
    And all checks ran through https://localhost:8443 (chat governance unaffected)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
HARNESS ARTIFACTS (3 files; harness-only, NO gateway source/test change)

scripts/v8_router_stub.py        bind 127.0.0.1:9922 ONLY (never 0.0.0.0)
  POST /api/v1/chat/completions  body:{ "model": <id>, ... }
    behavior "ok" (default)   -> 200 {"id","object":"chat.completion","model":<id ECHOED>,
                                       "choices":[{"index":0,"message":{"role":"assistant",
                                         "content":"ok from <id>"},"finish_reason":"stop"}],
                                       "usage":{"prompt_tokens":N>0,"completion_tokens":M>0,
                                         "total_tokens":N+M}}
    behavior "fail_5xx"       -> 500
    behavior {"fail_n":K}     -> 500 for first K calls to this model, then "ok"
    (each successful or attempted call increments the model's counter)
  POST /__faults  body:{ "model": <id>, "behavior": <behavior> }  -> 200; resets that counter
  GET  /__counters                                                -> 200 { "<model_id>": <int>, ... }
  module API: make_stub_server() -> HTTPServer ; start_stub_in_thread(server) -> Thread (daemon)

infra/docker-compose.e2e.v8.yml  (additive on base+v4+v5+v6; gateway.environment only)
  GATEWAY_OPENROUTER_BASE_URL: "http://host.docker.internal:9922/api/v1"   # override v6 → v8 stub
  GATEWAY_ROUTING_STRATEGY:    "simple-shuffle"
  GATEWAY_MODEL_GROUPS: |
    {"v8-dist":[{"model_id":"stub/dep-a","weight":1},{"model_id":"stub/dep-b","weight":3}],
     "v8-limit":[{"model_id":"stub/lim-a","rpm_limit":5},{"model_id":"stub/lim-b"}],
     "v8-allsat":[{"model_id":"stub/sat-a","rpm_limit":3},{"model_id":"stub/sat-b","rpm_limit":3}],
     "v8-fb":["stub/fb-primary","stub/fb-secondary"],
     "v8-bill":[{"model_id":"stub/bill-a","weight":1},{"model_id":"stub/bill-b","weight":1}]}
  # keeps the v6 cooldown + retry knobs (FAILURE_THRESHOLD=2, TTL_S=5, WINDOW_S=60, MAX_RETRIES=2)
  # keeps GATEWAY_OPENROUTER_API_KEY="stub-openrouter-key" (non-secret placeholder; never empty)

scripts/live_v8_verify.py  — exit 0 iff ALL of C1..C7 pass; "ALL CRITERIA PASS (n/n)" on success.
  BASE=https://localhost:8443 ; run_id=int(time.time()) fresh per invocation.
  containers: hydroa-e2e-{postgres,gateway,redis}-1 ; psql via docker exec ; redis via docker exec.
  Redis deplimit key (READ by the gate): gateway:deplimit:rpm:{deployment_id}:{bucket}
    bucket = floor(time.time()/60) ; verify SEEDS current AND next bucket to the limit (boundary-safe)
  Seeds: 8 stub model ids (provider='openrouter', active=true) + per_token pricing (non-zero).
  Asserts: /__counters (distribution+skip), HTTP status (200/429/401), usage_records (poll ≤30s).

DOUBLE-PASS: run live_v8_verify.py twice; both exit 0. Each pass resets gateway+Redis, fresh run_id.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] the Redis deplimit bucket is wall-clock-minute keyed
(`floor(time.time()/60)`); the C2/C3 pre-seed forces saturation only if the gateway reads the SAME
bucket the verify seeded — a request crossing the minute boundary reads a fresh empty bucket and the
skip/429 silently would not fire (false 200). Why most likely wrong: it is a real time race, not a
logic choice. Cost if wrong: a flaky C2/C3 → re-run. Mitigation in the contract: the verify seeds
BOTH the current and next bucket and fires immediately after seeding; the double-pass catches a
boundary straddle. (Secondary [test] flag: the C1 weighted-shuffle margin is statistical — asserted
loosely as dep-b>dep-a AND both>0 over ≥40 samples to never flake on a fair shuffle.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: N/A — PURE HARNESS task (live_v5/v6/v7-live-verify precedent: no unit red suite).
The executable check list C1–C7 in `scripts/live_v8_verify.py` IS the test; its evidence is the
live double-pass (both runs exit 0, "ALL CRITERIA PASS"), not a pytest coverage number. The gateway
source is unchanged (no new code to cover); the regression suite (572+ tests) stays green and is
re-run by the orchestrator's authoritative `make ci` as the no-regression guard.

Plan (one check per scenario, asserting observable behavior through the live edge):
<test_plan>
  - C1 test_distribution: fire ≥40 chats to v8-dist / assert /__counters dep-a>0 ∧ dep-b>0 ∧ dep-b>dep-a
  - C2 test_limit_skip: seed lim-a rpm window / fire chats to v8-limit / assert all 200 by lim-b, lim-a counter flat
  - C3 test_all_saturated: seed both sat windows / chat v8-allsat / assert 429 ERR_RATE_LIMITED + Retry-After, no counter, no usage row
  - C4 test_fallback: fault fb-primary fail_5xx / chat v8-fb / assert 200 by fb-secondary, 1 usage row on fb-secondary
  - C5 test_cooldown: drive ≥2 fb-primary failures / assert fb-primary skipped (cooled) then eligible after TTL
  - C6 test_billing_served_id: chat v8-bill / assert exactly 1 usage row, model_id = served id (not alias), cost>0
  - C7 test_governance_tls: chat v8-dist no-bearer / assert 401, 0 usage rows; all via https://localhost:8443
</test_plan>

Tests live in: `scripts/live_v8_verify.py` · the harness IS the executable check; the live run is its red→green.
<!-- declare paths as backticked tokens on this line -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the stub MUST bind 127.0.0.1 only (never 0.0.0.0); NO real key is
read/logged/echoed; harness-only — NO gateway source or frozen-test change.
Code lives in: `scripts/` + `infra/` (harness/ops layer; does not import gateway internals beyond the HTTP edge).
Constraints: do NOT change any gateway source, test, or the §3 contract; allow-list packages only (stdlib http.server + httpx + the existing verify deps); ask if unclear.

<!-- EXIT: live double-pass both exit 0; gateway source/tests untouched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — LIVE double-pass: both runs "ALL CRITERIA PASS (29/29)", exit 0
      (tmp/v8_pass1.log, tmp/v8_pass2.log). C1 distribution varied across passes
      (dep-a=8/dep-b=32, then 13/27 — both honor weight 3:1, proving real shuffle).
      No-regression guard: authoritative gateway suite 583 passed / 0 failed,
      81.97% coverage (>=80% gate); pyright 0 errors; ruff + format + allowlist clean.
- [x] coverage did not decrease — harness-only task (zero apps/gateway files touched,
      `git status` confirmed); gateway coverage byte-identical to deployment-limits
      close (583 passed / 81.97%). The 3 harness files live under scripts/ + infra/,
      outside the gateway coverage/lint/typecheck scope (live_v5/v6/v7 precedent).
- [x] no test or contract was altered during build — §3 CONTRACT frozen @ v1; the 3
      build files match it; no §1–§4 edit after freeze. Two in-build harness fixes
      (overlay placeholder key; C5 authoritative-signal rewrite) are TEST-HARNESS
      corrections, not contract/spec changes — see GATE RECORD disposition.
- [x] concurrency / timing of the risky operation is safe — the §3 lowest-confidence
      flag (Redis deplimit minute-bucket race) was mitigated by seeding BOTH current
      and next bucket; C2/C3 passed clean on both passes. The Envoy 50 req/s edge
      bucket is respected by pacing the C1/C5 bursts (EDGE_PACE_S=50ms ⇒ ~20 req/s)
      + a settle. Cooldown trip/recovery uses the AUTHORITATIVE /admin/routing
      snapshot_state poll (robust to simple-shuffle pick order + upstream retries),
      not fragile stub-counter inference. Stub handlers are loopback-only daemon
      threads; the verify polls (usage_records ≤30 s) rather than racing.
- [x] no exposed secrets, injection openings, or unexpected dependencies — the stub
      binds 127.0.0.1 ONLY (asserted at runtime before any check; HARD-STOP guard).
      GATEWAY_OPENROUTER_API_KEY="stub-openrouter-key" is a NON-SECRET placeholder
      (the documented v7 fix); no real key is read/logged/echoed/committed; no .env
      is read; Redis keys carry deployment_id only. No new gateway dependency (stub
      = stdlib http.server; verify = httpx, already a dep).
- [x] layering & dependencies follow CONVENTIONS.md — harness lives under scripts/ +
      infra/ (test/ops layer); touches the gateway only through the public HTTPS edge
      and docker exec (psql/redis-cli) — no import of gateway internals.
- [x] a person reviewed and approved the change — orchestrator manually reviewed the
      subagent build (stub, overlay, verify) + applied 2 fixes + ran the live
      double-pass; gated under delegated auto mode (non-security harness residue only).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new harness symbol is exercised LIVE: the stub's
      /api/v1/chat/completions, /__faults, /__counters (do_GET) and make_stub_server/
      start_stub_in_thread all fired across C1–C7; the overlay's 5 aliases + strategy
      + base_url + placeholder key all took effect (config proven by C1 distribution,
      C2/C3 limit gate, C5 cooldown via /admin/routing). _poll_candidate_state +
      _seed_deplimit_rpm both used. No orphaned symbol.
- [x] DEAD-CODE (code) — no orphaned symbol. (COOLDOWN_THRESHOLD remains as a
      documented config mirror after the C5 rewrite moved to the authoritative
      /admin/routing signal — harmless module constant, not a dead branch.)
- [x] SEMANTIC (prose / non-code) — read live_v8_verify.py C1–C7 in full + the v8
      overlay header. Confirmed the two failures hit during verify were HARNESS-env
      gaps, not gateway regressions: (1) empty GATEWAY_OPENROUTER_API_KEY → "Illegal
      header value b'Bearer '" (the exact v7 lesson — v8 stack composes base+v4+v5+v6,
      not v7, so the v7 placeholder was absent) → fixed in the v8 overlay; (2) the
      Envoy 50 req/s edge bucket 429'd a post-burst /admin/keys call → fixed by pacing.
      Chat source is byte-identical to deployment-limits (no gateway file touched).

### GATE RECORD
Outcome: PASS
Evidence: LIVE double-pass 29/29 ×2, both exit 0 (tmp/v8_pass1.log, tmp/v8_pass2.log)
through the Envoy TLS edge (https://localhost:8443) with the real create_app wiring,
Redis, and Postgres billing. No-regression: 583 passed / 81.97% cov; pyright 0; ruff +
format + allowlist clean. Harness-only (git: zero apps/gateway changes).
Disposition (in-build harness fixes, none touching gateway source/tests/contract):
(1) baked the non-secret OpenRouter placeholder into the v8 overlay (base defaults it
empty; v6 doesn't set it; v8 doesn't compose v7) — NOT a security finding (fake value,
no exposure); (2) paced C1/C5 bursts under the Envoy 50 req/s bucket; (3) rewrote C5 to
poll /admin/routing snapshot_state (authoritative cooldown signal) instead of inferring
from stub counters muddied by upstream retries — a stronger test, not a weaker one.
Reviewed by: Tin Dang (delegated auto mode, 2026-06-12) · date: 2026-06-12

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): per-deployment served-share within a model group
(distribution skew vs configured weights), per-deployment saturation-429 rate, cooldown
open/half-open/closed transition counts per deployment, served-id billing-row count
(exactly 1 per accepted chat, keyed on the served deployment id never the alias), chat
success rate (v6 regression guard), TLS-edge-only access.
Spec delta for the next loop: an e2e stack must be SELF-CONTAINED in its own overlay —
the v8 stack came up with an empty GATEWAY_OPENROUTER_API_KEY because the placeholder
lived only in the v7 overlay (which v8 does not compose), reproducing the exact v7 C5
"Illegal header value b'Bearer '" failure. Every live-verify overlay that drives an
upstream needs its own non-secret placeholder key, not a key inherited from a sibling
overlay or the operator shell. The standing v7 follow-up (a boot-time guard rejecting a
configured-yet-empty upstream key) would convert this opaque runtime 500 into a clear
startup error and is now doubly evidenced (v7 C5 + v8 first run).

### Competency deltas
- [ADD · open] live-verify overlays must be self-contained: each overlay that drives an
  upstream must SET its own non-secret placeholder key, never inherit it from a sibling
  overlay it doesn't compose. Evidence: v8 stack (base+v4+v5+v6+v8, no v7) booted with an
  empty OpenRouter key → every upstream chat 500'd ("Illegal header value b'Bearer '"),
  the identical v7 C5 failure; fixed by baking the placeholder into the v8 overlay.
- [ADD · open] a cooldown/health-gate live check should assert the AUTHORITATIVE gate
  state (GET /admin/routing snapshot_state), not infer it from upstream-stub call
  counters: under a non-deterministic strategy (simple-shuffle) + upstream retries the
  counter is muddied and the inference flaked. Evidence: C5 stub-counter version failed
  (primary counter 3->6 under retries); the /admin/routing-poll version passed 29/29 ×2.
- [TDD · open] a live harness that fires bursts must respect the edge rate limit
  (Envoy local_ratelimit = 50 req/s global): C1's 40-request distribution sample + C5's
  trip loop drained the bucket and 429'd a following /admin/keys call ("local_rate_limited");
  pacing bursts under the bucket (50 ms/req) + a settle fixed it. A statistical check
  (weighted distribution) needs volume, so it needs pacing — the two are coupled.
- [SDD · open] proving load-balanced distribution LIVE needs a per-deployment served-count
  readout the v6 stub lacked; the v8 stub's GET /__counters made weighted-shuffle observable
  (dep-a:dep-b ≈ 8:32 then 13:27 over weight 1:3). A router that distributes is only
  trustworthy once distribution is *observable* at the edge, not just unit-asserted.
