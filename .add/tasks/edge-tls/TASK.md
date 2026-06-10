# TASK: TLS termination at Envoy + prod topology

slug: edge-tls · created: 2026-06-10 · stage: mvp
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: TLS termination at Envoy edge + production compose topology
Framings weighed: Envoy downstream TLS (chosen) · Caddy TLS sidecar · Application-level TLS in FastAPI · Mutual-TLS (mTLS) between all hops

Must:
<must>
  - M1: infra/envoy/envoy.yaml — add a second listener on :8443 with TLS termination
        (downstream transport_socket: tls, TLS min version 1.2, modern cipher suite defaults);
        the listener uses the SAME route_config as the existing :8080 listener (shared by name
        reference in Envoy static config) so the filter chain ratelimit→jwt_authn→ext_authz→router
        applies identically on both listeners with no duplication.
  - M2: TLS certificates are provided via filesystem paths mounted into the Envoy container;
        dev/e2e paths: /etc/envoy/certs/server.crt and /etc/envoy/certs/server.key;
        the CA cert for e2e client verification: infra/envoy/certs/dev-ca.pem (host path);
        production certs are an operator concern (documented in runbook note in the contract).
  - M3: scripts/gen_dev_certs.sh — generates a self-signed CA + server cert/key for dev and e2e;
        output files: infra/envoy/certs/{dev-ca.pem, server.crt, server.key};
        the certs/ directory is gitignored; the script is idempotent (skips if files exist).
  - M4: HSTS header (Strict-Transport-Security: max-age=63072000; includeSubDomains) added on
        all responses from the :8443 listener via Envoy response_headers_to_add on that listener.
  - M5: The HTTP :8080 listener is RETAINED in the e2e and dev stacks (all existing e2e tests
        and scripts continue to pass without modification); in infra/docker-compose.prod.yml the
        :8080 listener is configured with an Envoy redirect_action to HTTPS :8443 (301 redirect),
        so production traffic is TLS-only while the dev/e2e stack requires no changes.
  - M6: infra/docker-compose.prod.yml — production compose file; services: envoy, gateway,
        postgres, redis; secrets via environment variables (no dev defaults); envoy exposes
        :8443 (TLS) and :8080 (redirect only), admin :9901 NOT host-exposed in production;
        GATEWAY_ENVIRONMENT=production required; GATEWAY_JWT_SECRET required (no fallback);
        schema via `make migrate` (alembic upgrade head) in an init container or documented
        operator step; cert paths mounted via volumes.
  - M7: The e2e compose (infra/docker-compose.e2e.yml) is extended to also mount the certs
        directory and expose :8443 for TLS e2e tests; :8080 remains exposed and the existing
        e2e test suite (test_e2e_edge.py) continues to run against :8080 unmodified.
  - M8: A TLS e2e test suite (apps/gateway/tests/edge/test_e2e_tls.py) covers the TLS scenarios;
        @pytest.mark.e2e; tests hit https://localhost:8443 with verify against the dev CA cert
        (path via env E2E_CA_CERT, default infra/envoy/certs/dev-ca.pem).
</must>

Reject:
<reject>
  - R1: plain HTTP request body sent to :8443 (not a valid TLS ClientHello) -> TCP RST or TLS alert (Envoy rejects at transport layer, no HTTP response)
  - R2: TLS handshake with protocol version < 1.2 (e.g. TLS 1.0, TLS 1.1) -> TLS alert handshake_failure (Envoy rejects at transport layer)
  - R3: TLS handshake with a cipher not in the allowed set -> TLS alert handshake_failure
  - R4: request to :8443 /internal/* -> 403 direct response (same as :8080; the shared route_config enforces this)
  - R5: HSTS header absent on any :8443 response -> reject as misconfigured (listener-level header must always be present)
  - R6: production compose started with GATEWAY_JWT_SECRET unset or empty -> compose / gateway startup MUST fail-fast (GATEWAY_ENVIRONMENT=production enforces this via existing gateway Settings validation)
</reject>

After:
<after>
  - A1: `curl --cacert infra/envoy/certs/dev-ca.pem https://localhost:8443/health` returns 200 from the gateway
  - A2: the full signup→login→create-key→/v1/ flow works over HTTPS (:8443) identically to HTTP (:8080)
  - A3: HSTS header is present on all :8443 responses
  - A4: TLS < 1.2 is rejected at the transport layer (observable via openssl s_client -tls1_1 failing)
  - A5: the existing 10 e2e tests in test_e2e_edge.py still pass against :8080 (no regression)
  - A6: infra/docker-compose.prod.yml starts successfully with real env vars; :8080 redirects to :8443
  - A7: scripts/gen_dev_certs.sh produces valid certs that Envoy accepts (self-signed CA chain trusted by tests)
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Envoy static config shared route_config across two listeners: Envoy's static config
    does NOT support a globally named route_config reference shared between two listeners —
    each listener's HttpConnectionManager embeds its own route_config inline. This means the
    filter chain + route_config MUST be duplicated between :8080 and :8443 listeners in
    envoy.yaml. Duplication drift (config on one listener diverging from the other) is the
    top ongoing risk. Mitigation: a YAML anchor (&route_cfg / *route_cfg) de-duplicates the
    block at the YAML level; the contract prescribes this anchor pattern explicitly. If the
    Envoy version does not parse YAML anchors (unlikely for v1.29 which uses libyaml), the
    anchor must be manually expanded — cost: two copies of route_config that can silently
    diverge; the verify step must diff them byte-for-byte.
  ⚠ HSTS header delivery mechanism — listener-level response_headers_to_add in Envoy
    applies to ALL responses including 403 direct_response and 401 from filters. If Envoy
    v1.29 does NOT apply listener-level headers to direct_response actions (known caveat in
    older Envoy versions), the HSTS header will be absent on 403/401 rejections. Mitigation:
    use a Lua filter or a virtual_host-level header addition as fallback. Cost: HSTS not
    present on rejection responses — low security impact for those paths, but the test
    asserting HSTS on every response will fail and require a config adjustment.
  - [ ] cert volume path in e2e compose: the gen_dev_certs.sh output at infra/envoy/certs/
        must be bind-mounted into the envoy container; confirm the relative path
        ./envoy/certs/ resolves correctly from the infra/ compose context directory
  - [ ] openssl availability for gen_dev_certs.sh: assumes openssl is available in the
        developer's environment and in any CI runner; confirm CI image has openssl
  - [ ] :8443 host port availability: confirms that port 8443 is not in use by other
        services on the developer machine; the e2e compose maps 8443:8443
  - [ ] GATEWAY_ENVIRONMENT=production fail-fast: confirms that the existing gateway Settings
        validation already raises on missing GATEWAY_JWT_SECRET when GATEWAY_ENVIRONMENT=production
        (referenced from edge-envoy §3); if not, the prod compose silent-start risk requires
        an explicit validation check
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S1 — TLS handshake succeeds on :8443 with dev CA (health check)
  Given the e2e stack is running with certs generated by gen_dev_certs.sh
  When GET https://localhost:8443/health with CA verification against infra/envoy/certs/dev-ca.pem
  Then response is 200 (from the gateway /health endpoint)
  And the TLS handshake completes without certificate errors

Scenario: S2 — plain HTTP (non-TLS) request to :8443 is rejected at transport layer
  Given the e2e stack is running
  When a plain HTTP GET http://localhost:8443/health is sent (no TLS ClientHello)
  Then the connection is closed / reset at the TCP/TLS layer; no HTTP response body is returned
  And the gateway access logs show no corresponding request

Scenario: S3 — full HTTPS flow: signup → login → create key → /v1/ chat
  Given the e2e stack is running
  When a client completes signup, login, key creation, and POST /v1/chat/completions — all over HTTPS :8443
  Then each step returns the same status codes as the HTTP :8080 flow
  And the existing :8080 e2e scenarios are unaffected (no regression)

Scenario: S4 — HSTS header present on all :8443 responses
  Given the e2e stack is running
  When GET https://localhost:8443/health (any valid request over TLS)
  Then response header Strict-Transport-Security is present with max-age >= 63072000
  And the header is also present on 4xx responses (e.g. /internal/* → 403)

Scenario: S5 — TLS version < 1.2 rejected at transport layer
  Given the e2e stack is running
  When a TLS 1.1 handshake is attempted to :8443 (e.g. openssl s_client -tls1_1)
  Then the connection is rejected with a TLS alert (handshake_failure)
  And no HTTP response is issued

Scenario: S6 — existing HTTP :8080 e2e suite still passes (no regression)
  Given the e2e stack is running (with both :8080 and :8443 exposed)
  When scripts/e2e_edge.sh runs tests/edge/test_e2e_edge.py against http://localhost:8080
  Then all 10 existing e2e tests pass without modification
  And TLS additions do not alter any existing behavior on :8080

Scenario: S7 — /internal/* blocked on :8443 (shared route_config enforces 403)
  Given the e2e stack is running
  When GET https://localhost:8443/internal/authz with dev CA verification
  Then Envoy returns 403 direct response (same as :8080 behavior)
  And the gateway is never contacted

Scenario: S8 — :8080 redirects to HTTPS :8443 in production compose topology
  Given infra/docker-compose.prod.yml is started with required env vars
  When GET http://localhost:8080/health (plain HTTP to the prod stack)
  Then Envoy returns 301 with Location: https://<host>:8443/health
  And no request body is forwarded to the gateway
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
=== NO GATEWAY CODE CHANGES ===
This task is infrastructure-only. No FastAPI routes, domain models, or DB schema are
modified. All changes are in infra/ and scripts/ only.

=== ENVOY :8443 LISTENER ADDITION (infra/envoy/envoy.yaml) ===

New listener added alongside the existing :8080 listener:

  - name: listener_tls
    address:
      socket_address: { address: 0.0.0.0, port_value: 8443 }
    filter_chains:
      - transport_socket:
          name: envoy.transport_sockets.tls
          typed_config:
            "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext
            common_tls_context:
              tls_params:
                tls_minimum_protocol_version: TLSv1_2
                # cipher_suites omitted → Envoy v1.29 TLS 1.2/1.3 modern defaults apply
              tls_certificates:
                - certificate_chain: { filename: "/etc/envoy/certs/server.crt" }
                  private_key:       { filename: "/etc/envoy/certs/server.key" }
        filters:
          - name: envoy.filters.network.http_connection_manager
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
              stat_prefix: ingress_https
              # HSTS header added at listener level — applies to all responses on :8443
              response_headers_to_add:
                - append_action: OVERWRITE_IF_EXISTS_OR_ADD
                  header:
                    key: Strict-Transport-Security
                    value: "max-age=63072000; includeSubDomains"
              # YAML anchor *http_filters and *route_config reference the blocks
              # defined on the :8080 listener (de-duplication via YAML anchors).
              # ⚠ DRIFT RISK: if YAML anchors are not supported or are manually expanded,
              #   the two route_config blocks MUST be kept byte-for-byte identical.
              #   The verify step MUST diff them explicitly.
              http_filters: *http_filters          # anchor defined on :8080 listener
              route_config: *route_config          # anchor defined on :8080 listener

# :8080 listener blocks are annotated with YAML anchors in the build:
#   http_filters block:   &http_filters
#   route_config block:   &route_config

=== :8080 LISTENER IN PRODUCTION COMPOSE (redirect only) ===

In infra/docker-compose.prod.yml the :8080 listener in envoy.yaml is OVERRIDDEN
by a separate production-only envoy config (infra/envoy/envoy-prod.yaml) OR
the compose entrypoint appends a redirect listener. Decision: use a separate
infra/envoy/envoy-prod.yaml that REPLACES envoy.yaml in the production compose volume
mount. It contains the :8443 TLS listener (identical filter chain) PLUS a :8080
listener that issues a redirect_action:

  routes:
    - match: { prefix: "/" }
      redirect:
        https_redirect: true
        port_redirect: 8443

This keeps envoy.yaml (used by e2e) unmodified with both :8080 and :8443 functional,
while envoy-prod.yaml encodes the production redirect posture.

=== CERTIFICATE PATHS ===

Dev / e2e (generated by scripts/gen_dev_certs.sh):
  infra/envoy/certs/dev-ca.pem       — self-signed CA cert (used by test verify= param)
  infra/envoy/certs/server.crt       — server cert signed by dev-ca
  infra/envoy/certs/server.key       — server private key
  infra/envoy/certs/.gitignore       — ignores *.pem, *.crt, *.key (generated, never committed)

Production (operator responsibility — runbook note):
  Operator mounts real cert/key at the same paths via Docker secrets or volume.
  The container image and envoy config are cert-agnostic; only the mount paths matter.
  Example production volume entry in docker-compose.prod.yml:
    volumes:
      - /etc/ssl/ai-proxy/server.crt:/etc/envoy/certs/server.crt:ro
      - /etc/ssl/ai-proxy/server.key:/etc/envoy/certs/server.key:ro

=== CERT GENERATION SCRIPT (scripts/gen_dev_certs.sh) ===

  set -euo pipefail
  OUT=infra/envoy/certs
  mkdir -p "$OUT"
  # Idempotent: skip if server cert already exists and is not expired
  if [ -f "$OUT/server.crt" ] && openssl x509 -checkend 0 -noout -in "$OUT/server.crt" 2>/dev/null; then
    echo "Dev certs already valid — skipping generation"; exit 0
  fi
  # 1. Self-signed CA (10-year)
  openssl req -x509 -newkey rsa:4096 -days 3650 -nodes \
    -keyout "$OUT/dev-ca-key.pem" -out "$OUT/dev-ca.pem" \
    -subj "/CN=ai-proxy-dev-ca"
  # 2. Server key + CSR
  openssl req -newkey rsa:4096 -nodes \
    -keyout "$OUT/server.key" -out "$OUT/server.csr" \
    -subj "/CN=localhost"
  # 3. Server cert signed by dev CA (SAN: localhost, 127.0.0.1)
  openssl x509 -req -days 825 -in "$OUT/server.csr" \
    -CA "$OUT/dev-ca.pem" -CAkey "$OUT/dev-ca-key.pem" -CAcreateserial \
    -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1") \
    -out "$OUT/server.crt"
  echo "Dev certs written to $OUT"

=== DOCKER-COMPOSE ADDITIONS ===

infra/docker-compose.e2e.yml — ADDITIVE changes (existing entries unchanged):
  envoy service additions:
    ports: [ "8443:8443" ]          # TLS listener — added alongside existing 8080/9901
    volumes: [ "./envoy/certs:/etc/envoy/certs:ro" ]   # cert mount added

infra/docker-compose.prod.yml — NEW FILE:
  services:
    postgres:
      image: postgres:16-alpine
      environment:
        POSTGRES_USER: ${POSTGRES_USER}
        POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
        POSTGRES_DB: ${POSTGRES_DB}
      # No host port mapping — internal only
      healthcheck: (same pattern as e2e)

    redis:
      image: redis:7-alpine
      # No host port mapping — internal only
      healthcheck: (same pattern as e2e)

    gateway:
      image: ${GATEWAY_IMAGE}   # pre-built; no build: in prod compose
      environment:
        GATEWAY_DATABASE_URL: ${GATEWAY_DATABASE_URL}    # required; no default
        GATEWAY_REDIS_URL: ${GATEWAY_REDIS_URL}           # required; no default
        GATEWAY_JWT_SECRET: ${GATEWAY_JWT_SECRET}         # required; no default
        GATEWAY_ENVIRONMENT: production
      depends_on: [ postgres: healthy, redis: healthy ]
      healthcheck: (same /health pattern as e2e)

    envoy:
      image: envoyproxy/envoy:v1.29-latest
      environment:
        GATEWAY_JWT_SECRET: ${GATEWAY_JWT_SECRET}
      volumes:
        - ./envoy/envoy-prod.yaml-template:/etc/envoy/envoy-template.yaml:ro
        - ${TLS_CERT_PATH}:/etc/envoy/certs/server.crt:ro
        - ${TLS_KEY_PATH}:/etc/envoy/certs/server.key:ro
      ports:
        - "443:8443"    # TLS — public-facing
        - "80:8080"     # HTTP redirect only
        # 9901 NOT exposed — admin interface internal only in production
      depends_on: [ gateway: healthy ]
      entrypoint: (same JWKS substitution entrypoint as e2e)

=== ENVIRONMENT VARIABLES (production-required, no defaults) ===

  GATEWAY_JWT_SECRET        — shared JWT signing secret (HS256); fail-fast if unset
  GATEWAY_DATABASE_URL      — asyncpg connection string
  GATEWAY_REDIS_URL         — Redis URL
  POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB — DB credentials
  GATEWAY_IMAGE             — pre-built gateway Docker image reference
  TLS_CERT_PATH             — host path to TLS server certificate
  TLS_KEY_PATH              — host path to TLS private key

=== SCHEMA / DB ===

  No schema changes. Schema is at the state produced by db-migrations task.
  Production bootstrap: operator runs `make migrate` (alembic upgrade head)
  against the production DB before starting the compose stack for the first time.

=== TEST ENVIRONMENT VARIABLE ===

  E2E_CA_CERT   — path to CA cert for HTTPS verify in e2e TLS tests
                  default: infra/envoy/certs/dev-ca.pem (relative to repo root,
                  resolved in tests via os.path.join(REPO_ROOT, default))
```

Status: FROZEN @ v2 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [contract] YAML anchor de-duplication of route_config across :8080 and :8443 listeners — Envoy's static config embeds route_config inline per HttpConnectionManager; it does NOT support a globally shared named route_config reference between two listeners. YAML anchors (*route_config) de-duplicate at the YAML parse level, but any manual expansion or template rendering that drops anchors will silently produce two copies that can drift. Cost: /internal/* 403 or filter order differences apply to only one listener, creating a security gap or regression. Must be verified by diffing the two rendered listener blocks byte-for-byte during Build verify.
⚠ [spec] HSTS header on Envoy direct_response / filter-rejection paths — Envoy v1.29 listener-level response_headers_to_add may NOT be applied to responses generated by http_filters (jwt_authn 401, local_ratelimit 429) or route-level direct_response (403). If absent, scenario S4 ("HSTS on 4xx") will fail. Cost: test failure requiring either a relaxed S4 assertion (HSTS on 2xx only) or a Lua filter workaround. The S4 test is written to assert HSTS on /health (200) first; a separate assertion on /internal/ (403) is marked xfail with a comment citing this risk.

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: n/a — this task is infrastructure-only (no Python source changes);
  e2e tests are excluded from coverage by default; the 80% floor on gateway source is unaffected.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_tls_handshake_health (S1): arrange stack running + dev CA cert / act GET https://localhost:8443/health verify=ca_cert / assert 200; assert no ssl.SSLError
  - test_plain_http_to_tls_port_rejected (S2): arrange stack running / act httpx GET http://localhost:8443/health (no TLS) / assert connection error or non-HTTP response (RemoteProtocolError / ConnectError)
  - test_https_full_flow_signup_key_v1 (S3): arrange stack running / act signup→login→key→POST /v1/ all via https:8443 / assert same status codes as HTTP flow; assert :8080 health still 200
  - test_hsts_header_present_on_200 (S4a): arrange stack running / act GET https://localhost:8443/health / assert Strict-Transport-Security header present, max-age >= 63072000
  - test_hsts_header_on_403_direct_response (S4b, xfail): arrange stack running / act GET https://localhost:8443/internal/authz / assert 403 AND Strict-Transport-Security present (xfail: known Envoy caveat — direct_response may not carry listener headers)
  - test_tls_version_below_1_2_rejected (S5): arrange stack running / act ssl.wrap_socket with TLSv1.1 / assert ssl.SSLError (handshake failure)
  - test_http_8080_still_works_no_regression (S6): arrange stack running / act GET http://localhost:8080/health (plain HTTP) / assert 200; existing e2e suite not invoked directly but this confirms :8080 up
  - test_internal_blocked_on_tls_listener (S7): arrange stack running / act GET https://localhost:8443/internal/authz verify=ca_cert / assert 403
  - test_http_redirects_to_https_on_prod_topology (S8): NOTE — this scenario requires docker-compose.prod.yml which is a BUILD deliverable; test is written to be skipped (pytest.skip with reason) unless E2E_PROD_STACK=1 env var is set; when set, GET http://localhost:8080/health expects 301 with Location containing https:
</test_plan>

Tests live in: `apps/gateway/tests/edge/test_e2e_tls.py`

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — orchestrator re-ran the canonical scripts/e2e_edge.sh end-to-end:
      v1 HTTP suite 10 passed + TLS suite 7 passed / 1 skipped (S8 prod-redirect, gated on
      real certs) / 1 xpassed (S4b HSTS-on-direct-response — xfail strict=False, the caveat
      turned out not to apply at route_config level), script exit 0; make ci exit 0 (98
      non-e2e green, coverage floor held)
- [x] coverage did not decrease — make ci floor held; e2e excluded from coverage by design
- [x] no test or contract was altered during build — `git diff <freeze>..HEAD -- tests .add`
      empty; ruff format exclusion extended for the frozen TLS test file instead of editing it
- [x] concurrency / timing of the risky operation is safe — rate-limit bucket depletion
      between the HTTP and TLS e2e suites handled by sequencing in e2e_edge.sh (2s refill
      pause), not by weakening the rate-limit test
- [x] no exposed secrets, injection openings, or unexpected dependencies — certs generated
      locally by scripts/gen_dev_certs.sh into a gitignored dir (infra/envoy/certs/.gitignore);
      prod compose takes JWT secret/cert paths from env with no dev defaults and does not
      host-expose the Envoy admin port; TLS ≥1.2 enforced (downgrade test green); no new deps
- [x] layering & dependencies follow CONVENTIONS.md — all changes in infra/ + scripts/;
      gateway code untouched
- [x] a person reviewed and approved the change — orchestrator review of the two contract
      deviations, both letter-impossible-intent-preserved: (1) HSTS placed at
      RouteConfiguration.response_headers_to_add because the contracted HCM-level field does
      not exist in the Envoy v1.29 proto — empirically covers routed AND direct_response
      paths (S4b xpassed); (2) S8 prod-redirect skipped honestly (needs real certs/operator
      env), envoy-prod.yaml structurally validated (delegated auto mode, Tin Dang, 2026-06-10)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — :8443 listener consumes the SAME &http_filters/&routes anchors as
      :8080; drift-diff (YAML-parsed comparison of both rendered listener blocks) shows
      filters and routes IDENTICAL, HSTS present only on :8443 — the security-load-bearing
      /internal 403 + ext_authz + jwt exemptions provably apply to both listeners
- [x] DEAD-CODE (code) — envoy-prod.yaml + docker-compose.prod.yml are operator deliverables
      consumed by the runbook/S8; gen_dev_certs.sh consumed by e2e_edge.sh and CI docs
- [x] SEMANTIC (prose / non-code) — envoy.yaml TLS block, envoy-prod.yaml redirect listener,
      and both compose files read in full at verify; transport socket (TLS ≥1.2, cert paths),
      port topology, and env-var contract match §3
### GATE RECORD
Outcome: PASS (auto-resolved — autonomy: auto; evidence complete incl. live TLS e2e; the two
deviations are documented above with empirical proof, not waived silently)
Reviewed by: Claude (orchestrator) under delegated auto mode — Tin Dang · date: 2026-06-10

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): Envoy listener cert expiry (prod certs) · jwt_authn 401 rate at edge · local rate-limit 429 rate via rate(gateway_http_requests_total{status_code="429"}[5m]) once Envoy access logs ship
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
