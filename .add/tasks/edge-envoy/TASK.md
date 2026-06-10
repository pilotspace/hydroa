# TASK: Envoy edge (TLS, jwt_authn, ext_authz, rate limit) + compose stack

slug: edge-envoy · created: 2026-06-10 · stage: mvp
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Envoy edge layer — jwt_authn + ext_authz + rate limit + compose e2e stack
Framings weighed: Envoy static config (chosen) · Nginx+lua · Caddy+forward-auth · Pure-FastAPI middleware

Must:
<must>
  - M1: infra/envoy/envoy.yaml — HTTP listener on :8080; static Envoy config (no xDS required for MVP)
  - M2: jwt_authn filter validates gateway-issued HS256 JWTs for requests to /admin/* paths; claim `iss` must equal "ai-proxy"; oct-key JWKS embedded inline from the same shared secret used by the gateway
  - M3: ext_authz HTTP filter calls gateway POST /internal/authz for /v1/* paths, forwarding the client's `authorization` and `x-api-key` headers; 200 response from gateway → Envoy passes the request onward; any non-200 → Envoy rejects the request with the gateway's status code and body
  - M4: /internal/authz gateway endpoint ADDITIVELY accepts `Authorization: Bearer <plaintext-key>` in addition to the existing `X-Api-Key` header; the Bearer value is the same raw key string ("sk-<hex>.<secret>"); behaviour is identical to the x-api-key path (same 200/401 responses, same response headers x-tenant-id and x-key-id)
  - M5: local rate limit filter applied globally: 50 req/s fill rate, burst 50 tokens (token bucket); Envoy returns 429 on exhaustion
  - M6: requests matching /internal/* are rejected by Envoy with a direct response (403 status) — the gateway is never contacted from outside
  - M7: everything not matched by M2/M3/M6 is routed to the gateway cluster (upstream host configurable; default: host.docker.internal:8000 / gateway:8000 in compose)
  - M8: apps/gateway/Dockerfile — uv-based image, runs `uvicorn gateway.main:create_app --factory`; production-safe (non-root user, no dev deps)
  - M9: infra/docker-compose.e2e.yml — services: envoy (port 8080:8080 + 9901:9901 admin), gateway (internal, not exposed on host), postgres, redis; all wired with healthchecks; envoy depends_on gateway healthy
  - M10: scripts/e2e_edge.sh — brings compose stack up, waits for all services healthy (with timeout), runs `uv run pytest tests/ -m e2e -q --no-cov`, tears down on exit (success or failure)
  - M11: apps/gateway/tests/edge/ — two test files (test_authz_bearer.py in-process; test_e2e_edge.py @pytest.mark.e2e); pyproject.toml marker registered + e2e excluded from default addopts
</must>

Reject:
<reject>
  - R1: request to /internal/* from outside Envoy -> 403 direct response (Envoy never forwards to gateway)
  - R2: /admin/* request with missing Authorization header -> jwt_authn rejects with 401 (Envoy direct)
  - R3: /admin/* request with expired JWT -> jwt_authn rejects with 401 (Envoy direct)
  - R4: /admin/* request with JWT signed by wrong secret -> jwt_authn rejects with 401 (Envoy direct)
  - R5: /admin/* request with JWT whose iss claim != "ai-proxy" -> jwt_authn rejects with 401 (Envoy direct)
  - R6: /v1/* request with invalid/revoked API key (x-api-key path) -> ext_authz passes gateway 401 through -> client sees 401
  - R7: /v1/* request with invalid/revoked API key (Bearer path) -> ext_authz passes gateway 401 through -> client sees 401
  - R8: /internal/authz POST with Authorization: Bearer <key> that is revoked -> "ERR_AUTH_INVALID_KEY"
  - R9: /internal/authz POST with Authorization: Bearer of malformed key format -> "ERR_AUTH_INVALID_KEY"
  - R10: /internal/authz POST with neither X-Api-Key nor Authorization header -> "ERR_AUTH_INVALID_KEY"
  - R11: request rate exceeds 50 req/s sustained burst -> 429 from Envoy local rate limiter
</reject>

After:
<after>
  - A1: a valid tenant API key can be used in Authorization: Bearer or X-Api-Key headers; both paths reach the gateway with x-tenant-id and x-key-id response headers set
  - A2: the Envoy compose stack is up; `curl -H "Authorization: Bearer <valid-key>" http://localhost:8080/v1/chat/completions` reaches the gateway
  - A3: /admin/* routes remain accessible through Envoy with a valid JWT (jwt_authn passes the request)
  - A4: /internal/* is permanently blocked at the edge — no path bypasses Envoy to reach it directly except gateway-internal calls on the compose network
  - A5: the existing 78 gateway tests pass untouched; the Bearer authz test suite is red (implementation not yet done) before the Build phase
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ ext_authz response header forwarding: the contract assumes Envoy's ext_authz HTTP filter will forward response headers from the authz service (x-tenant-id, x-key-id) to the upstream request automatically via `allowed_upstream_headers`. If this Envoy version (1.29+) has a behaviour difference for `allowed_upstream_headers_to_append` vs `allowed_upstream_headers`, the proxy-completions task may not receive tenant context headers — cost: proxy task breaks, requires Envoy config fixup and re-verify. Confirmed pattern from Envoy docs but untested in this exact compose topology.
  ⚠ HS256 oct-key JWKS inline embedding: jwt_authn in Envoy accepts JWKS with `{"keys":[{"kty":"oct","alg":"HS256","k":"<base64url-secret>"}]}` inline via `local_jwks`. The base64url encoding must be the raw bytes of the UTF-8 secret string (not a PEM or hex). If the gateway jwt_secret contains non-ASCII or special chars that alter base64url padding, the embedded JWKS will fail to verify tokens — cost: all /admin/* routes 401 in production (blocked at edge). Mitigation: the dev default secret is ASCII-only; the Dockerfile env var must document this constraint.
  - [ ] gateway Dockerfile base image: assumes `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` or equivalent uv+python image is available and suitable for production; confirm image pinning strategy before freeze
  - [ ] local rate limit token bucket: Envoy's local rate limit filter uses token_bucket fill_interval; 50 req/s at 1-second fill is equivalent to "50 req/s" but burst semantics differ from a sliding window — confirm acceptable for MVP (no per-tenant rate limit needed at this stage)
  - [ ] ext_authz failure mode: if the gateway /internal/authz endpoint is unreachable (gateway crash), Envoy's ext_authz default is to DENY the request — this is the safe/correct default for this system; confirm `failure_mode_allow: false` is the right posture
  - [ ] Postgres/Redis in compose e2e: assumes the same gateway_test DB name and credentials used in dev compose are reused in e2e compose; no separate e2e DB provisioning needed
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S1 — valid Bearer key accepted by /internal/authz (in-process)
  Given a tenant with an active API key "sk-<hex>.<secret>"
  When POST /internal/authz with header "Authorization: Bearer sk-<hex>.<secret>"
  Then response is 200 with body {"tenant_id": "<uuid>", "key_id": "<uuid>"}
  And response headers include x-tenant-id and x-key-id matching the key's tenant
  And the existing x-api-key path continues to return the same 200 response

Scenario: S2 — revoked key rejected via Bearer path
  Given a tenant with a revoked API key
  When POST /internal/authz with header "Authorization: Bearer <revoked-key>"
  Then response is 401 problem+json with code "ERR_AUTH_INVALID_KEY"
  And no tenant or key information is present in the response body
  And the x-api-key path for the same revoked key also returns 401 (unchanged)

Scenario: S3 — malformed Bearer value rejected
  Given any request context
  When POST /internal/authz with header "Authorization: Bearer not-a-valid-key-format"
  Then response is 401 problem+json with code "ERR_AUTH_INVALID_KEY"
  And the response body is byte-identical to the x-api-key malformed response

Scenario: S4 — no auth header rejected
  Given any request context
  When POST /internal/authz with neither X-Api-Key nor Authorization header
  Then response is 401 problem+json with code "ERR_AUTH_INVALID_KEY"
  And no tenant information is leaked

Scenario: S5 — /internal/* blocked at Envoy edge (e2e)
  Given the Envoy+gateway compose stack is running
  When GET http://localhost:8080/internal/authz (any path under /internal/)
  Then Envoy returns 403 with no forwarding to the gateway
  And gateway access logs show no corresponding request

Scenario: S6 — valid JWT passes /admin/* through Envoy (e2e)
  Given the compose stack is running and a valid gateway-issued HS256 JWT (iss="ai-proxy")
  When GET http://localhost:8080/admin/keys with Authorization: Bearer <valid-jwt>
  Then Envoy passes the request to the gateway and the gateway's response is returned (200 or 404 but not 401 from Envoy)
  And the request reaches the gateway (observable via gateway response body/headers)

Scenario: S7 — expired/invalid JWT rejected at Envoy jwt_authn (e2e)
  Given the compose stack is running and an expired JWT
  When GET http://localhost:8080/admin/keys with Authorization: Bearer <expired-jwt>
  Then Envoy returns 401 before the request reaches the gateway
  And the gateway access logs show no corresponding request

Scenario: S8 — valid API key in Authorization: Bearer passes /v1/* through ext_authz (e2e)
  Given the compose stack is running and a tenant with an active API key
  When POST http://localhost:8080/v1/chat/completions with Authorization: Bearer <valid-key>
  Then Envoy calls /internal/authz, receives 200, and forwards the request to the gateway
  And the gateway receives x-tenant-id and x-key-id headers from the authz response

Scenario: S9 — invalid API key in /v1/* rejected via ext_authz (e2e)
  Given the compose stack is running
  When POST http://localhost:8080/v1/chat/completions with Authorization: Bearer <invalid-key>
  Then Envoy forwards the ext_authz rejection and the client receives 401
  And no completion request reaches the OpenRouter upstream

Scenario: S10 — rate limit enforced at 50 req/s burst (e2e)
  Given the compose stack is running and a valid API key
  When 60 rapid requests are sent to /v1/chat/completions within 1 second
  Then at least one response has status 429
  And responses before limit was reached had status != 429
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
=== GATEWAY ADDITIVE CHANGE ===

POST /internal/authz   body: (none)
  Headers accepted (evaluated in priority order):
    1. Authorization: Bearer <raw-key>   (NEW — Bearer path)
    2. X-Api-Key: <raw-key>              (EXISTING — unchanged)
  
  raw-key format (both paths): "sk-<32-char-hex-uuid>.<urlsafe_b64_secret>"
  
  200 -> {"tenant_id": "<uuid>", "key_id": "<uuid>"}
         Response headers set: x-tenant-id: <uuid>, x-key-id: <uuid>
         (headers required by Envoy ext_authz allowed_upstream_headers)
  
  401 -> {"type": "about:blank", "title": "Unauthorized", "status": 401,
          "code": "ERR_AUTH_INVALID_KEY", "detail": "Invalid API key"}
         Identical body for ALL failure modes: missing header / malformed key /
         unknown key_id / wrong secret / revoked key
  
  Schema: api_keys table (read-only); no new columns; no migrations required.
  Access pattern: lookup by key_id (existing index), constant-time hash compare.
  Priority: Authorization: Bearer is checked first; if present, X-Api-Key is ignored.

=== ENVOY CONFIG SHAPE (infra/envoy/envoy.yaml) ===

static_resources:
  listeners:
    - name: listener_0
      address: socket_address {address: 0.0.0.0, port_value: 8080}
      filter_chains:
        - filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                stat_prefix: ingress_http
                http_filters:
                  [order matters — evaluated top-to-bottom per request]:
                  1. envoy.filters.http.local_ratelimit
                     token_bucket: max_tokens=50, tokens_per_fill=50, fill_interval=1s
                     filter_enabled: 100% (runtime_key: local_rate_limit_enabled)
                     filter_enforced: 100%
                     response_headers_to_add: [{header: {key: x-ratelimit-limit, value: "50"}}]
                  
                  2. envoy.filters.http.jwt_authn
                     providers:
                       gateway_jwt:
                         issuer: "ai-proxy"
                         local_jwks:
                           inline_string: '{"keys":[{"kty":"oct","alg":"HS256","use":"sig","k":"<base64url(GATEWAY_JWT_SECRET)>"}]}'
                         forward: true   # forward the JWT to the upstream for gateway re-validation
                         payload_in_metadata: "jwt_payload"
                     rules:
                       - match: {prefix: "/admin/"}
                         requires: {provider_name: "gateway_jwt"}
                       - match: {prefix: "/"}   # catch-all — no jwt_authn required
                         requires: {}
                  
                  3. envoy.filters.http.ext_authz
                     http_service:
                       server_uri:
                         uri: "http://gateway:8000/internal/authz"
                         cluster: "gateway_cluster"
                         timeout: 2s
                       authorization_request:
                         allowed_headers:
                           patterns: [{exact: "authorization"}, {exact: "x-api-key"}]
                       authorization_response:
                         allowed_upstream_headers:
                           patterns: [{exact: "x-tenant-id"}, {exact: "x-key-id"}]
                     with_request_body: false
                     failure_mode_allow: false
                     include_peer_certificate: false
                     route match rule applied ONLY to /v1/* prefix
                     [all other paths pass through ext_authz unconditionally — note: jwt_authn
                      already guards /admin/*; /internal/* blocked by direct_response below]
                  
                  4. envoy.filters.http.router (terminal)
                
                route_config:
                  virtual_hosts:
                    - name: local
                      domains: ["*"]
                      routes:
                        - match: {prefix: "/internal/"}
                          direct_response: {status: 403, body: {inline_string: "Forbidden"}}
                        
                        - match: {prefix: "/v1/"}
                          route: {cluster: "gateway_cluster"}
                          typed_per_filter_config:
                            envoy.filters.http.ext_authz:
                              "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute
                              check_settings:
                                context_extensions: {route: "v1"}
                        
                        - match: {prefix: "/admin/"}
                          route: {cluster: "gateway_cluster"}
                        
                        - match: {prefix: "/"}
                          route: {cluster: "gateway_cluster"}

  clusters:
    - name: gateway_cluster
      connect_timeout: 0.5s
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      load_assignment:
        cluster_name: gateway_cluster
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: {address: "gateway", port_value: 8000}

=== DOCKER FILES ===

apps/gateway/Dockerfile:
  Base: ghcr.io/astral-sh/uv:python3.12-bookworm-slim (pinned by digest in build)
  Build stage: copy pyproject.toml + uv.lock, run `uv sync --frozen --no-dev`
  Runtime stage: non-root user (uid 1000), expose 8000
  CMD: ["uv", "run", "uvicorn", "gateway.main:create_app", "--factory",
        "--host", "0.0.0.0", "--port", "8000"]
  ENV: GATEWAY_ENVIRONMENT=production (requires GATEWAY_JWT_SECRET to be set at runtime)

infra/docker-compose.e2e.yml:
  services:
    postgres:   image postgres:16-alpine, POSTGRES_DB=gateway_e2e, port 5433
    redis:      image redis:7-alpine, port 6380
    gateway:
      build: ../apps/gateway
      environment:
        GATEWAY_DATABASE_URL: postgresql+asyncpg://gateway:gateway@postgres:5432/gateway_e2e
        GATEWAY_REDIS_URL: redis://redis:6379/0
        GATEWAY_JWT_SECRET: ${GATEWAY_JWT_SECRET:-e2e-test-secret-change-me}
        GATEWAY_ENVIRONMENT: test
      depends_on: [postgres: {condition: service_healthy}, redis: {condition: service_healthy}]
      healthcheck: test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
    envoy:
      image: envoyproxy/envoy:v1.29-latest
      volumes: [./envoy/envoy.yaml:/etc/envoy/envoy.yaml:ro]
      environment:
        GATEWAY_JWT_SECRET: ${GATEWAY_JWT_SECRET:-e2e-test-secret-change-me}
      ports: ["8080:8080", "9901:9901"]
      depends_on: [gateway: {condition: service_healthy}]
      command: ["envoy", "-c", "/etc/envoy/envoy.yaml", "--log-level", "warn"]

scripts/e2e_edge.sh:
  set -euo pipefail
  compose up --build -d --wait
  trap 'compose down -v' EXIT
  uv run pytest tests/ -m e2e -q --no-cov (from apps/gateway/)

=== PYPROJECT.TOML ADDITIONS (additive only) ===

[tool.pytest.ini_options]
markers = ["e2e: end-to-end tests requiring the compose stack (excluded by default)"]
addopts = "--cov=gateway --cov-report=term-missing --cov-fail-under=80 -m 'not e2e'"
  [note: existing addopts line is replaced — the -m 'not e2e' is appended; e2e tests are
   collected but skipped by default marker expression]
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [contract] ext_authz response header forwarding — Envoy's `allowed_upstream_headers` in ext_authz HTTP service mode forwards headers from the authz response to the upstream request. If the Envoy version in the compose image processes these as request headers (correct) vs response headers (incorrect), x-tenant-id/x-key-id will never reach the gateway's proxy handler. Cost: proxy-completions task silently operates without tenant context, breaking per-tenant metering. Must be verified by running the e2e test S8 which asserts gateway receives the headers.
⚠ [contract] HS256 oct-key JWKS inline — Envoy jwt_authn with kty=oct for HS256 is supported but less commonly documented than RSA/EC. The base64url encoding of the raw UTF-8 secret bytes must be exact; any trailing `=` padding in the JSON string causes a parse failure (Envoy requires unpadded base64url). Cost: ALL /admin/* routes fail with 401 at Envoy level, blocking the entire control plane. Must be validated by the S6 e2e scenario before contract freeze.

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85% (additive; edge layer adds router logic; e2e excluded from coverage)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_bearer_valid_key_accepted (S1): arrange active key / act POST /internal/authz Authorization: Bearer <key> / assert 200 + tenant_id + key_id in body
  - test_bearer_revoked_key_rejected (S2): arrange revoked key / act Bearer authz / assert 401 ERR_AUTH_INVALID_KEY; also verify x-api-key path unchanged
  - test_bearer_malformed_key_rejected (S3): parametrize bad Bearer values / act / assert 401 ERR_AUTH_INVALID_KEY; assert byte-identical to x-api-key malformed response
  - test_bearer_no_auth_header_rejected (S4): no headers / act / assert 401 ERR_AUTH_INVALID_KEY
  - test_e2e_internal_blocked (S5): @e2e / GET localhost:8080/internal/authz / assert 403
  - test_e2e_valid_jwt_passes_admin (S6): @e2e / GET /admin/keys valid JWT / assert not 401 from Envoy
  - test_e2e_invalid_jwt_rejected_admin (S7): @e2e / GET /admin/keys expired JWT / assert 401
  - test_e2e_valid_key_bearer_passes_v1 (S8): @e2e / POST /v1/... Bearer valid key / assert Envoy forwards (not 401/403)
  - test_e2e_invalid_key_rejected_v1 (S9): @e2e / POST /v1/... Bearer invalid key / assert 401
  - test_e2e_rate_limit_enforced (S10): @e2e / 60 rapid requests / assert at least one 429
</test_plan>

Tests live in: `apps/gateway/tests/edge/test_authz_bearer.py` `apps/gateway/tests/edge/test_e2e_edge.py`
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): Envoy jwt_authn config must use `forward: true` so the gateway can re-verify the JWT independently; never configure Envoy as the sole JWT verifier for admin operations. Bearer key path in /internal/authz must share the SAME AuthzUseCase as the x-api-key path — no duplicated validation logic.
Code lives in: `apps/gateway/src/` `infra/` `scripts/`
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
