# TASK: v6 live close harness — fault-injecting upstream stub + double-pass verify

slug: v6-live-verify · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: v6 live close harness — fault-injecting upstream stub + double-pass exit-criteria verification

Framings weighed:
  - **Single fault-stub server + overlay + unified verify script (chosen)**: one host-level HTTP
    server on :9920 speaks the OpenRouter completions surface and exposes a /__faults control
    endpoint; a v6 overlay threads one new Settings knob (openrouter_base_url) so the gateway
    under test calls the stub instead of real OpenRouter; scripts/live_v6_verify.py checks all
    six exit criteria through the TLS edge. This is the exact idiom established by live_v5_verify.py.
  - **Dedicated mock-upstream container in the compose stack (rejected)**: requires a custom Docker
    image, rebuild on every stub change, and more moving parts; the host-process style of
    live_v5_verify.py avoids all this and is already proven.
  - **Replay-only stub (canned responses, no fault table) (rejected)**: cannot test cooldown
    recovery or the C2 fallback path which requires per-model-id behavioral switching mid-run.

Must:
<must>
  - `GATEWAY_OPENROUTER_BASE_URL` Settings knob MUST be added to `Settings` as
    `openrouter_base_url: str = "https://openrouter.ai/api/v1"` (env prefix:
    `GATEWAY_OPENROUTER_BASE_URL`). The default MUST be byte-identical to the
    current module constant `_BASE_URL` in openrouter_upstream.py.
  - The knob MUST be threaded into `OpenRouterCompletionUpstream.__init__` as the
    `base_url` parameter, replacing the module constant. `create_app()` passes
    `settings.openrouter_base_url` to the upstream constructor.
  - Fault-stub `scripts/v6_fault_stub.py` MUST listen on 127.0.0.1:9920, expose
    `POST /api/v1/chat/completions` (completions surface) and
    `POST /__faults` (fault-table control endpoint). It MUST NOT bind 0.0.0.0.
  - The /__faults endpoint MUST accept `{"model": <str>, "behavior": <behavior>}` where
    `<behavior>` is one of: `"ok"` | `"fail_5xx"` | `{"fail_n": N}` | `{"status": 429,
    "retry_after": <s>}` | `"stream_cut"`. The fault table is mutable per-call; last write wins.
  - POST /api/v1/chat/completions MUST route behavior by the `model` field in the request body.
    For behavior `"ok"`: return a well-formed non-streaming JSON completions response with
    `{"model": <model_id>, "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}`.
    For behavior `"fail_5xx"`: return HTTP 500. For `{"fail_n": N}`: return 500 for the first N
    calls then "ok" (per-model counter). For `{"status": 429, "retry_after": s}`: return HTTP 429
    with `Retry-After: <s>` header. For `"stream_cut"`: return a streaming SSE response that
    emits exactly one `data: {...}` chunk then closes the connection mid-stream (simulates
    upstream stream abort after first byte forwarded).
  - Stub SSE (streaming) responses MUST include `Content-Type: text/event-stream`.
  - Overlay `infra/docker-compose.e2e.v6.yml` MUST set:
    `GATEWAY_OPENROUTER_BASE_URL=http://host.docker.internal:9920/api/v1`,
    `GATEWAY_UPSTREAM_MAX_RETRIES=2`,
    `GATEWAY_MODEL_GROUPS` with a test alias `v6-alias` over two stub model ids
    `stub/primary` and `stub/fallback`,
    `GATEWAY_COOLDOWN_FAILURE_THRESHOLD=2`,
    `GATEWAY_COOLDOWN_TTL_S=5`,
    `GATEWAY_COOLDOWN_WINDOW_S=60`.
  - `scripts/live_v6_verify.py` MUST verify all six exit criteria (C1–C6) through the TLS
    edge (`https://localhost:8443`) using a fresh `run_id` on every invocation.
  - C1 (retry): configure stub so `stub/primary` returns 500 twice then succeeds; send
    a non-streaming completion request for `v6-alias`; assert HTTP 200; assert exactly ONE
    usage_records row for the served candidate (the one that ultimately answered).
  - C2 (fallback): configure stub so `stub/primary` always fails (fail_5xx) and
    `stub/fallback` returns ok; send completion for `v6-alias`; assert 200 with the
    response model == `stub/fallback`; assert usage_records row model == `stub/fallback`.
  - C3 (cooldown): trip cooldown on `stub/primary` by forcing consecutive failures
    past the threshold (≥2); poll `GET /admin/routing` until `stub/primary` state == "open";
    assert that subsequent requests to `v6-alias` are served by `stub/fallback` (cooldown
    skips the cooled candidate); wait for TTL expiry (≥5 s); verify half-open probe recovery
    via a successful request and state transition; assert metric
    `gateway_cooldown_transitions_total` was incremented (read from
    `/internal/metrics` through the gateway container, not the TLS edge).
  - C4 (routing-admin): GET `/admin/routing` with tenant Bearer JWT; assert 200 with
    `retry_policy`, `cooldown`, `model_groups`, `candidates` all present and matching
    expected shape from routing-admin §3 FROZEN contract; assert `stub/primary` and
    `stub/fallback` appear in candidates list with correct alias; assert response contains
    no secret values (check openrouter_api_key sentinel is absent).
  - C5 (stream-cut): configure stub so `stub/primary` returns `stream_cut`; send a
    streaming completion request (`"stream": true`); assert that the gateway closes
    the stream (client receives some bytes then EOF or a non-2xx wrapping); assert NO
    new retry attempt is made (stream is the hard boundary — exactly one upstream call);
    assert NO usage_records row from a second attempt (the ledger records the one attempt).
  - C6 (TLS + double-pass isolation): all C1–C5 checks MUST go through
    `https://localhost:8443` (the Envoy TLS edge); every identity (tenant email, key name)
    MUST embed `run_id`; the orchestrator executes the script twice and BOTH runs must
    exit 0 (the double-pass rule is the orchestrator's job — the script documents it
    in its own re-run command block).
  - The red test suite (tests/upstream_base_url/) tests ONLY the gateway-source change:
    (a) Settings default pin, (b) constructor threading, (c) wiring (create_app).
    The fault stub, overlay, and verify script are harness artifacts; their §4 evidence
    is the live run (live_v5 precedent — no unit tests for the harness artifacts).
</must>

Reject:
<reject>
  - Stub binding to 0.0.0.0 → runtime security error / startup refusal (binding must be 127.0.0.1)
  - overlay setting GATEWAY_OPENROUTER_BASE_URL to a non-http(s) scheme → Settings
    validation fails at gateway startup (no contracted error code; it is a misconfiguration)
  - Red suite testing the fault stub server behavior or the live script logic →
    "ERR_SCOPE_VIOLATION" (harness artifacts have no unit tests per §1 Must and live_v5 precedent)
  - Any test that modifies or weakens a frozen test suite from a prior v6 task →
    "ERR_FROZEN_SUITE_VIOLATION" (foundation non-negotiable)
</reject>

After:
<after>
  - `Settings.openrouter_base_url` defaults to `"https://openrouter.ai/api/v1"` — no change
    to production behavior without an explicit env override.
  - `OpenRouterCompletionUpstream` receives its base URL from the Settings knob, not from
    the module constant, and the httpx.AsyncClient is constructed with that value.
  - `create_app()` passes `settings.openrouter_base_url` to the upstream constructor; the
    seam is confirmed by a wiring regression test identical in structure to
    `tests/retry_policy_wiring/`.
  - The fault stub is ready to run on :9920 for the live harness (build phase completes it).
  - The overlay is written and the verify script skeleton is ready for completion in build.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE [scenario/test]: The cooldown TTL of 5 s (C3 half-open recovery) may
    produce a timing flake on slow CI machines or under load. The harness poll loop
    (polling GET /admin/routing until state != "open") mitigates blind sleeping, but
    Redis TTL expiry is external and cannot be advanced. If wrong cost: C3 check fails
    intermittently (false negative) without any underlying code defect.
    Mitigation: set GATEWAY_COOLDOWN_TTL_S=5 with a 15 s poll ceiling in the verify
    script; accept up to 2 retries of the "wait for half-open" loop before declaring
    failure. Do not reduce TTL below 5 s (Redis TTL granularity is 1 s; sub-5 s TTLs
    produce race conditions on loaded machines).

  ⚠ SECOND-LOWEST CONFIDENCE [contract]: The `openrouter_base_url` knob threads into
    the `httpx.AsyncClient(base_url=...)` constructor argument. httpx treats `base_url`
    such that relative paths in `.post("/chat/completions")` are resolved against it.
    If the stub URL does not end with a trailing path separator compatible with httpx's
    base_url join semantics (e.g., `http://host.docker.internal:9920/api/v1` + relative
    `/chat/completions`), the resolved URL may double the path prefix. Risk: requests
    hit the wrong stub path → C1–C5 all fail at the live-run stage. Cost: a one-line
    fix (add trailing slash or adjust relative path in the upstream calls). Mitigation:
    the red suite tests include a base_url value check but cannot verify live join
    semantics; the build phase must confirm with a real httpx request to the stub.

  - [x] The fault stub will be started by the verify script on :9920 before any C1–C5
    checks run; no overlay reconfiguration (like the v5 gateway restart for OIDC) is needed
    for C1–C5. The gateway sees the stub immediately because GATEWAY_OPENROUTER_BASE_URL
    is baked into the overlay at compose-up time.
  - [x] `app.state.completion_upstream` is `OpenRouterCompletionUpstream`; no test already
    inspects `_base_url` on the upstream; the new wiring test cannot conflict with
    frozen suites (adding a read-only attribute check is additive).
  - [x] The routing-admin endpoint (C4) is implemented by the routing-admin task BUILD phase
    (running in a parallel agent); by the time v6-live-verify BUILD runs, it will exist.
    If it does not, C4 will fail with a clear "404" rather than a silent false-positive.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: BU1 — Settings.openrouter_base_url default is the production OpenRouter URL
  Given Settings constructed with no GATEWAY_OPENROUTER_BASE_URL env var
  When the Settings object is inspected
  Then settings.openrouter_base_url == "https://openrouter.ai/api/v1"

Scenario: BU2 — GATEWAY_OPENROUTER_BASE_URL env var overrides the default
  Given Settings constructed with GATEWAY_OPENROUTER_BASE_URL="http://stub:9920/api/v1"
  When the Settings object is inspected
  Then settings.openrouter_base_url == "http://stub:9920/api/v1"

Scenario: BU3 — OpenRouterCompletionUpstream constructor accepts base_url and stores it
  Given OpenRouterCompletionUpstream constructed with api_key="k", base_url="http://stub:9920/api/v1"
  When the instance is inspected
  Then upstream._base_url == "http://stub:9920/api/v1"
  And the httpx.AsyncClient was constructed with base_url="http://stub:9920/api/v1"

Scenario: BU4 — create_app() threads settings.openrouter_base_url into completion_upstream
  Given Settings with openrouter_base_url="http://stub:9920/api/v1"
  When create_app(settings) is called
  Then app.state.completion_upstream._base_url == "http://stub:9920/api/v1"
  And app.state.completion_upstream is an OpenRouterCompletionUpstream

Scenario: BU5 — create_app() with default settings wires the production base_url (no regression)
  Given Settings constructed with all defaults (no override)
  When create_app(settings) is called
  Then app.state.completion_upstream._base_url == "https://openrouter.ai/api/v1"

Scenario: LV-C1 (LIVE) — pre-stream 5xx retried within budget; exactly one ledger row
  Given the e2e stack with v6 overlay (stub on :9920, max_retries=2)
  And stub configured: stub/primary returns 500 twice then ok; stub/fallback returns ok
  When a non-streaming completion request is sent for alias "v6-alias" through the TLS edge
  Then the gateway returns HTTP 200
  And exactly one usage_records row exists for the key used in this request
  And the served model in the ledger row is the candidate that ultimately answered

Scenario: LV-C2 (LIVE) — alias fallback to next candidate; ledger carries served model
  Given the e2e stack with v6 overlay
  And stub configured: stub/primary always fail_5xx; stub/fallback returns ok
  When a non-streaming completion request is sent for alias "v6-alias"
  Then the gateway returns HTTP 200
  And the response body contains model == "stub/fallback"
  And the usage_records row model == "stub/fallback"

Scenario: LV-C3 (LIVE) — cooldown trips, recovery via half-open probe after TTL
  Given the e2e stack with v6 overlay (cooldown threshold=2, ttl_s=5)
  And stub configured: stub/primary always fail_5xx
  When 2 consecutive failures are induced on stub/primary
  Then GET /admin/routing shows stub/primary state == "open" within 10 s
  And subsequent requests to v6-alias are served by stub/fallback
  When 5 s pass (TTL expiry) and stub/primary is restored to "ok"
  Then a probe request transitions stub/primary to "half_open" then "closed"
  And gateway_cooldown_transitions_total metric was incremented

Scenario: LV-C4 (LIVE) — GET /admin/routing returns correct shape, tenant-authenticated
  Given the e2e stack with v6 overlay
  When GET /admin/routing with valid owner Bearer JWT
  Then 200 with retry_policy, cooldown, model_groups, candidates all present
  And candidates list includes stub/primary and stub/fallback with alias "v6-alias"
  And the response body does not contain the openrouter_api_key value

Scenario: LV-C5 (LIVE) — mid-stream failure keeps v5 semantics; no retry
  Given the e2e stack with v6 overlay
  And stub configured: stub/primary returns stream_cut
  When a streaming completion request is sent for stub/primary
  Then the gateway closes the stream (client receives EOF or error after first chunk)
  And no second upstream attempt is made (stream boundary is the retry hard stop)

Scenario: LV-C6 (LIVE) — all checks through TLS edge; run_id isolation; double-pass
  Given the e2e stack with v6 overlay
  When live_v6_verify.py is run with a fresh run_id
  Then all C1–C5 checks pass through https://localhost:8443
  And every identity (tenant email, key name) embeds the run_id
  And a second consecutive run also exits 0 (double-pass isolation rule)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LOWEST-CONFIDENCE FLAGS AT DRAFT

  ⚠ [scenario] TIMING FLAKE RISK — C3 cooldown TTL (5 s): on loaded machines Redis
    key expiry may arrive later than expected, causing the half-open probe poll to time
    out before the key expires. Mitigation: the verify script polls GET /admin/routing
    with a 15 s ceiling and 1 s interval, accepting any state != "open" as recovery;
    the test does not assert a specific wall-clock elapsed time. The TTL knob
    (GATEWAY_COOLDOWN_TTL_S=5) is deliberately short for testability — NOT a value
    safe for production. The orchestrator documents this in the overlay comment.
    If mitigation insufficient: operator may raise TTL to 10 s and extend poll ceiling.
    [part]

  ⚠ [contract] HTTPX BASE_URL JOIN SEMANTICS — the stub URL
    "http://host.docker.internal:9920/api/v1" combined with the relative path
    "/chat/completions" used inside OpenRouterCompletionUpstream must resolve to
    "http://host.docker.internal:9920/api/v1/chat/completions". httpx merges a
    base_url with a relative path by appending it (if base_url ends without trailing
    slash and the path starts with /, httpx strips the path component of base_url and
    appends). The BUILD phase MUST verify this join in a smoke test against the live stub.
    If wrong: adjust either the base_url trailing slash or the relative path constant
    inside the upstream. Zero gateway-source change required if the stub's route matches
    the resolved URL.

---

SETTINGS KNOB CONTRACT

  New field in apps/gateway/src/gateway/core/config.py (Settings class):

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # env: GATEWAY_OPENROUTER_BASE_URL
    # Controls the base URL for OpenRouterCompletionUpstream.
    # Default is byte-identical to the prior module constant (_BASE_URL).
    # Override in e2e overlays to point the gateway at the fault stub.
    # NEVER set to a non-https URL in production deployments.

  Environment variable: GATEWAY_OPENROUTER_BASE_URL
  Type: str (no validation beyond non-empty; URLs are validated by the upstream at
        request time, not at Settings construction time — consistent with redis_url)
  Default: "https://openrouter.ai/api/v1" (byte-identical to prior _BASE_URL constant)
  Placement: after the upstream_retry_backoff_base_s field in the retry-policy block

UPSTREAM CONSTRUCTOR CONTRACT

  OpenRouterCompletionUpstream.__init__ gains a new keyword-only parameter:

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        max_retries: int = 0,
        backoff_base: float = 0.5,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None: ...

  The parameter MUST be stored as `self._base_url = base_url`.
  The httpx.AsyncClient MUST be constructed with `base_url=base_url` (replacing the
  module constant _BASE_URL). The module constant _BASE_URL is RETAINED as documentation
  but removed from the client construction call.

CREATE_APP WIRING CONTRACT

  In apps/gateway/src/gateway/main.py, the OpenRouterCompletionUpstream construction
  block (currently at line ~328) gains one argument:

    app.state.completion_upstream = OpenRouterCompletionUpstream(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,   # <-- NEW (threaded from Settings)
        max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        metrics_registry=app.state.metrics_registry,
    )

FAULT STUB CONTRACT (scripts/v6_fault_stub.py)

  Binding:  127.0.0.1:9920  (localhost only; never 0.0.0.0)
  Startup:  synchronous HTTPServer (stdlib http.server, same style as live_v5_verify.py
            IdP mocks). The live_v6_verify.py script starts it in a daemon thread before
            the first check and shuts it down at exit.

  POST /__faults
    Request body: {"model": "<str>", "behavior": <behavior>}
    <behavior> ::= "ok"
                 | "fail_5xx"
                 | {"fail_n": <int>}         # fail for first N calls, then "ok"
                 | {"status": 429, "retry_after": <int>}   # 429 + Retry-After header
                 | "stream_cut"              # first SSE chunk then connection close
    Response: 200 {"ok": true}
    The fault table is global mutable state; last write wins per model id.
    Per-model call counters for {"fail_n": N} reset when the fault is re-configured.

  POST /api/v1/chat/completions
    Request body: {"model": "<model_id>", "stream": <bool>, ...}  (standard OpenRouter shape)
    Behavior routing: look up request body "model" field in fault table.
    Default (model not in table): behavior "ok".

    "ok" (non-streaming):
      200  {"id": "stub-...", "model": "<model_id>",
            "choices": [{"message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop", "index": 0}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}
      Content-Type: application/json

    "ok" (streaming, "stream": true in request):
      200 Content-Type: text/event-stream
      data: {"id": "stub-...","model":"<model_id>","choices":[{"delta":{"role":"assistant","content":"ok"},"finish_reason":null,"index":0}]}

      data: {"id": "stub-...","model":"<model_id>","choices":[{"delta":{},"finish_reason":"stop","index":0}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}

      data: [DONE]

    "fail_5xx": 500  {"error": "stub upstream error"}
    {"fail_n": N}: first N calls → 500; N+1th and later → "ok" (per-model counter)
    {"status": 429, "retry_after": s}: 429 with header Retry-After: <s>
    "stream_cut" (streaming only):
      200 Content-Type: text/event-stream
      data: {"id":"stub-...","model":"<model_id>","choices":[{"delta":{"role":"assistant","content":"st"},"finish_reason":null,"index":0}]}
      <connection closed mid-stream — no [DONE] frame>

OVERLAY CONTRACT (infra/docker-compose.e2e.v6.yml)

  Composes on top of base + v4 + v5 overlays (additive; does not override v5 keys).

  services:
    gateway:
      environment:
        GATEWAY_OPENROUTER_BASE_URL: "http://host.docker.internal:9920/api/v1"
        GATEWAY_UPSTREAM_MAX_RETRIES: "2"
        GATEWAY_MODEL_GROUPS: '{"v6-alias": ["stub/primary", "stub/fallback"]}'
        GATEWAY_COOLDOWN_FAILURE_THRESHOLD: "2"
        GATEWAY_COOLDOWN_TTL_S: "5"
        GATEWAY_COOLDOWN_WINDOW_S: "60"

VERIFY SCRIPT CONTRACT (scripts/live_v6_verify.py)

  Startup guard: GATEWAY_OPENROUTER_API_KEY must be set (not used for real calls, but
    the gateway requires it at startup; the overlay does NOT override it).
  Stub port: STUB_PORT = 9920 (localhost only)
  Base URL: BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")
  run_id: int(time.time()) — embedded in every tenant email and key name.
  Checks: C1, C2, C3, C4, C5, C6 mapped to MILESTONE.md exit criteria 1–6.
  Output: PASS/FAIL per check; summary; exit 0 = all pass, 1 = any failure, 2 = key absent.
  Re-run command block (documented in the script's final output):
    docker compose \
        -f infra/docker-compose.e2e.yml \
        -f infra/docker-compose.e2e.v4.yml \
        -f infra/docker-compose.e2e.v5.yml \
        -f infra/docker-compose.e2e.v6.yml \
        up -d --wait
    uv run --project apps/gateway python scripts/live_v6_verify.py

EXIT-CRITERIA CHECK TABLE

  C1 → MILESTONE exit criterion 1 (pre-stream retries; single ledger row)
       Check: non-streaming request to v6-alias with stub/primary configured fail_n=2;
              assert 200; wait for flusher; assert exactly 1 usage_records row for key.
       Observable: HTTP status + DB row count via docker exec psql.

  C2 → MILESTONE exit criterion 2 (alias fallback; ledger carries served model)
       Check: v6-alias with stub/primary=fail_5xx, stub/fallback=ok;
              assert 200; assert response.json()["model"] == "stub/fallback";
              assert usage_records row model == "stub/fallback".
       Observable: HTTP response body + DB row via docker exec psql.

  C3 → MILESTONE exit criterion 3 (cooldown trip, half-open recovery)
       Check: force 2+ consecutive failures on stub/primary (threshold=2);
              poll GET /admin/routing (1 s interval, 10 s ceiling) until stub/primary state=="open";
              assert next request to v6-alias returns 200 via stub/fallback;
              wait for TTL (poll until state != "open", 15 s ceiling, 1 s interval);
              restore stub/primary to "ok"; make one completion request to v6-alias;
              assert metric gateway_cooldown_transitions_total > 0 (read from internal metrics).
       Observable: GET /admin/routing JSON + internal metrics endpoint + HTTP status.
       Timing risk: mitigated by poll ceiling (see [part] flag above).

  C4 → MILESTONE exit criterion 4 (routing-admin surface; secrets-free)
       Check: GET /admin/routing with owner Bearer JWT;
              assert 200; assert body has retry_policy/cooldown/model_groups/candidates;
              assert "stub/primary" in [c["model_id"] for c in body["candidates"]];
              assert openrouter_api_key value not in json.dumps(body).
       Observable: HTTP response body.
       Source: routing-admin TASK.md §3 FROZEN contract (consumed read-only here).

  C5 → MILESTONE exit criterion 5 (mid-stream failure; no retry/fallback)
       Check: configure stub/primary to "stream_cut"; send streaming request for stub/primary;
              assert client receives at least one chunk then stream closes (non-200 wrapping
              OR connection reset); assert usage_records has exactly 1 row (one attempt only).
       Observable: streaming response behavior + DB row count.

  C6 → MILESTONE exit criterion 6 (TLS edge + double-pass)
       Check: all C1–C5 requests use BASE = https://localhost:8443;
              assert run_id embedded in every identity created;
              document double-pass in the re-run command block (orchestrator runs twice).
       Observable: BASE prefix in every request URL + run_id in tenant/key names + exit codes.

DOUBLE-PASS CLOSE RULE

  The orchestrator (not the script) runs scripts/live_v6_verify.py twice in sequence.
  Both runs must exit 0. This is the "two consecutive clean passes = isolation proof"
  rule from the MILESTONE shared decisions. The script documents the re-run command.
  The script does NOT enforce the double-pass itself.

RED SUITE SCOPE DECLARATION

  The red suite in `apps/gateway/tests/upstream_base_url/` covers ONLY the three
  unit-testable gateway-source changes: (BU1) Settings default pin, (BU2) Settings
  override, (BU3) constructor threading, (BU4) create_app wiring with override,
  (BU5) create_app wiring with default (no regression). The fault stub, overlay, and
  verify script are live-harness artifacts. Per live_v5 precedent, they have NO unit
  tests; their evidence is the live run itself. This is the binding orchestrator decision.
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [scenario] C3 cooldown TTL timing flake — ACCEPTED
with the poll-based mitigation (15 s ceiling, 1 s interval via GET /admin/routing; no
wall-clock assertions). Second flag (httpx base_url join) — LOW RISK: the existing
upstream already posts a relative path against base_url="https://openrouter.ai/api/v1"
and has passed five milestones of live verification; BUILD smoke-confirms against the
stub anyway.

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the three gateway-source changes (Settings knob, constructor threading,
create_app wiring). Harness artifacts (stub, overlay, script) are not unit-tested — evidence
is the live run (live_v5 precedent, binding orchestrator decision stated in §3).

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_bu1_settings_default_base_url:
      arrange: Settings() with no GATEWAY_OPENROUTER_BASE_URL in env
      act: inspect settings.openrouter_base_url
      assert: == "https://openrouter.ai/api/v1"
      RED reason: Settings has no openrouter_base_url field → AttributeError

  - test_bu2_settings_env_override:
      arrange: Settings(openrouter_base_url="http://stub:9920/api/v1")
      act: inspect settings.openrouter_base_url
      assert: == "http://stub:9920/api/v1"
      RED reason: AttributeError (field absent)

  - test_bu3_upstream_constructor_stores_base_url:
      arrange: OpenRouterCompletionUpstream(api_key="k", base_url="http://stub:9920/api/v1")
      act: inspect upstream._base_url
      assert: upstream._base_url == "http://stub:9920/api/v1"
      RED reason: __init__ has no base_url parameter → TypeError

  - test_bu4_create_app_wires_custom_base_url:
      arrange: Settings with openrouter_base_url="http://stub:9920/api/v1" + minimal defaults
      act: create_app(settings); inspect app.state.completion_upstream._base_url
      assert: app.state.completion_upstream._base_url == "http://stub:9920/api/v1"
      RED reason: TypeError from constructor (no base_url param) or AttributeError

  - test_bu5_create_app_default_base_url_no_regression:
      arrange: Settings() with all defaults (no override)
      act: create_app(settings); inspect app.state.completion_upstream._base_url
      assert: app.state.completion_upstream._base_url == "https://openrouter.ai/api/v1"
      RED reason: AttributeError (field absent from upstream)
</test_plan>

Tests live in: `apps/gateway/tests/upstream_base_url/` · `apps/gateway/tests/upstream_base_url/__init__.py` · `apps/gateway/tests/upstream_base_url/test_upstream_base_url.py`

Note: scripts/live_v6_verify.py, scripts/v6_fault_stub.py, and infra/docker-compose.e2e.v6.yml
are harness-skeleton artifacts written at the spec/test phase (clearly marked as DRAFT/SKELETON).
They have no unit test coverage. The build phase completes them. Their §4 evidence is the
live double-pass run executed by the orchestrator.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the `openrouter_base_url` default MUST remain
`"https://openrouter.ai/api/v1"` — any regression in the default causes the production
gateway to hit the wrong upstream. The wiring test BU5 is the guard. The fault stub
MUST NOT bind to 0.0.0.0 (security). The overlay MUST NOT disable TLS or auth.

Code lives in:
  - `apps/gateway/src/gateway/core/config.py` (additive: one new field)
  - `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py` (additive: base_url param)
  - `apps/gateway/src/gateway/main.py` (one new kwarg in OpenRouterCompletionUpstream construction)
  - `scripts/v6_fault_stub.py` (new file — complete the skeleton)
  - `scripts/live_v6_verify.py` (new file — complete the skeleton)
  - `infra/docker-compose.e2e.v6.yml` (new file — finalize the draft)

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
- [ ] WIRING (code) — openrouter_base_url field in Settings; base_url param in OpenRouterCompletionUpstream.__init__; create_app passes settings.openrouter_base_url; BU4+BU5 confirm
- [ ] DEAD-CODE (code) — module constant _BASE_URL retained as doc comment only (not used in client construction); no orphaned symbols
- [ ] SEMANTIC (prose / non-code) — live_v6_verify.py and v6_fault_stub.py read in full by orchestrator before live run

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): C3 poll ceiling hit rate (indicator of TTL undershoot
on loaded machines); C5 stream-cut latency (time from first chunk to client EOF); any
C4 admin/routing 401 rate (auth misconfiguration signal)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
