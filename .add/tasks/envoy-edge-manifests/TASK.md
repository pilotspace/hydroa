# TASK: Envoy edge Deployment/Service/ConfigMap (TLS · ext_authz · WS-upgrade)

slug: envoy-edge-manifests · created: 2026-06-26 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): NEW Envoy edge templates extending the FROZEN chart `envoy{}` placeholder; grounded in the real, proven prod Envoy config they translate —
  - `infra/envoy/envoy-prod.yaml` — the production Envoy config THIS task ports to k8s. Two listeners: `listener_tls` :8443 (TLS from `/etc/envoy/certs/server.{crt,key}`) → http_connection_manager filter chain [local_ratelimit · jwt_authn (inline JWKS HS256 with the `GATEWAY_JWT_SECRET_BASE64URL` placeholder) · ext_authz (path_prefix `/internal/authz`, cluster `gateway_cluster`) · router] with per-route ext_authz on /v1/*; `listener_http_redirect` :8080 (301→https). `gateway_cluster` → socket_address `gateway`:8000. Admin :9901. The ONLY k8s delta: upstream host `gateway` → the gateway Service `ai-proxy-gateway`.
  - `infra/docker-compose.prod.yml` (envoy service, lines 80-105) — the proven BOOT mechanism: image `envoyproxy/envoy:v1.29-latest`; entrypoint computes `B64=base64url(GATEWAY_JWT_SECRET)`, `sed`s the `GATEWAY_JWT_SECRET_BASE64URL` placeholder in the mounted template → `/tmp/envoy.yaml`, then `exec envoy -c`. GATEWAY_JWT_SECRET from env; TLS cert/key from operator host paths; ports 443→8443, 80→8080; admin 9901 NOT exposed; depends_on gateway healthy.
  - `charts/ai-proxy/values.yaml` — the FROZEN `envoy: {}` placeholder THIS task fills (sibling-owned). The gateway workload it fronts is `ai-proxy-gateway` (helper `ai-proxy.gateway.fullname`) on port 8000 (/health, /internal/authz). The SAME JWT secret the gateway uses (`gateway.jwtSecret.existingSecret`="ai-proxy-gateway-secrets", `existingKey`="jwt-secret") must feed Envoy's JWKS — reuse helper `ai-proxy.gateway.jwtSecretName`.
  - `charts/ai-proxy/templates/_helpers.tpl` — REUSE `ai-proxy.fullname`/`.labels`/`ai-proxy.gateway.fullname`/`ai-proxy.gateway.jwtSecretName`; ADD `ai-proxy.envoy.fullname`/selectorLabels (do NOT edit frozen helpers).
Context (working folder): `infra/envoy/README.md` (edge posture) · `infra/envoy/envoy.yaml` (the dev/e2e variant — non-TLS, same filters) · `.add/milestones/v53/MILESTONE.md` Shared decisions (E2E-THROUGH-THE-EDGE: the e2e MUST drive the goal flow through Envoy, not direct-to-gateway; SECRETS-NEVER-IN-CHART; CLOUD-READY/KIND-VALIDATED). NO envoy k8s manifests exist yet.
Honors (patterns / conventions): E2E-THROUGH-THE-EDGE → Envoy is the front door; its Service is what the e2e dials. SECRETS-NEVER-IN-CHART → TLS cert/key + JWT secret via k8s Secrets (operator/kind-bootstrap provides; never a values literal), mirroring the gateway jwtSecret pattern. DESIGN-FOR-FAILURE → readiness/liveness on Envoy admin `/ready` :9901, resources req+limits, PDB. FROZEN-SCHEMA → extend `envoy{}` ONLY; the gateway/datastores/image keys are untouched. CONFIG-AS-DATA → the Envoy config rides in a ConfigMap (the template); the `GATEWAY_JWT_SECRET_BASE64URL` substitution happens at boot via an initContainer (mirrors the proven compose entrypoint), keeping the JWT secret out of the rendered config.
Anchors the contract cites: NEW templates `charts/ai-proxy/templates/envoy-configmap.yaml` (the Envoy config template, upstream host = `ai-proxy.gateway.fullname`) · `envoy-deployment.yaml` (image + initContainer base64url+sed → emptyDir, TLS Secret mount, GATEWAY_JWT_SECRET secretKeyRef, admin probes) · `envoy-service.yaml` (:8443 https + :8080 http; type values-driven LoadBalancer|ClusterIP|NodePort) · the EXTENDED `envoy{}` values sub-schema (`enabled`, `image`, `replicas`, `service.type`, `tls.existingSecret`, `resources`, `probes`, `pdb`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Envoy edge (TLS termination · JWT/JWKS · ext_authz · WS-upgrade) as k8s manifests — a FULLY Helm-TEMPLATED Envoy config (values-driven knobs) in a ConfigMap + Deployment + Service that front the gateway, filling the frozen `envoy{}` block.
Framings weighed: fully Helm-templated config in the ConfigMap, driven by values, faithful to the proven `envoy-prod.yaml` filter chain (chosen — Tin: operators tune rate-limit/timeouts/HSTS/issuer without editing a baked blob) · near-verbatim copy of envoy-prod.yaml with only the host templated (rejected by Tin — too opaque to tune) · bake the JWT into the ConfigMap at render time (rejected — a secret-derived value in a ConfigMap violates SECRETS-NEVER-IN-CHART). JWKS substitution stays an initContainer (mirrors the proven compose boot); the JWT secret never enters the ConfigMap.
Must:
<must>
  - M1 — `envoy.enabled` (default true) renders an Envoy Deployment (`envoyproxy/envoy:v1.29-latest`, values-driven), a Service, and a ConfigMap whose `envoy.yaml` is HELM-TEMPLATED from values (not a static copy).
  - M2 — the templated config faithfully reproduces the proven filter chain IN ORDER — local_ratelimit · jwt_authn (HS256, issuer values-driven, JWKS placeholder) · ext_authz (path_prefix `/internal/authz`) · router — with the route table (per-route ext_authz enabled on `/v1/`, disabled on `/admin/` + `/` + 403 `/internal/`), the WS upgrade, HSTS, and the :8443/:8080/:9901 listeners.
  - M3 — the magic numbers are values-driven (NO hardcoded literal in the template): rate-limit token bucket (`maxTokens`/`tokensPerFill`/`fillInterval`), ext_authz `timeout`, upstream `connectTimeout`, HSTS `maxAge`/`includeSubDomains`, JWT `issuer`, TLS `minimumProtocolVersion`. The `gateway_cluster` upstream resolves to the gateway Service `ai-proxy-gateway:8000` (helper-derived, NEVER the literal `gateway`).
  - M4 — the JWT/JWKS secret is handled out-of-band: an initContainer reads `GATEWAY_JWT_SECRET` via secretKeyRef from the SAME Secret the gateway uses (`ai-proxy.gateway.jwtSecretName` + `gateway.jwtSecret.existingKey`), computes base64url, `sed`s `GATEWAY_JWT_SECRET_BASE64URL` in the ConfigMap template into a shared emptyDir; the rendered ConfigMap holds the literal placeholder, never the secret.
  - M5 — TLS cert/key come from a k8s Secret (`envoy.tls.existingSecret`, type kubernetes.io/tls) mounted at `/etc/envoy/certs/` (server.crt/server.key) — never a values literal; the chart ships no cert material. The Service exposes 8443 (https) + 8080 (http) at the values-driven `envoy.service.type` (default ClusterIP for kind; LoadBalancer/NodePort for cloud) with NO template change.
  - M6 — design-for-failure: readiness+liveness probes hit the Envoy admin `/ready` on :9901, resources requests+limits set, a PodDisruptionBudget renders; WebSocket upgrade (`upgrade_configs`/websocket) is preserved for the v52 realtime relay path.
  - M7 — external-ready/frozen-schema: `envoy.enabled=false` renders NOTHING for Envoy; the frozen gateway/datastores/image keys + templates are untouched; a missing TLS secret ref in a non-dev env fails closed (`tls_secret_ref_missing`, mirrors the gateway guard family).
</must>
Reject:
<reject>
  - the JWT secret (or its base64url) appearing in the rendered ConfigMap or any values default -> "secret_in_configmap_forbidden"
  - a TLS cert/key value inlined in a template or values default -> "tls_literal_forbidden"
  - `helm lint`/`helm template` non-zero on the extended chart (default values) -> "chart_invalid"
  - the Envoy upstream pointing at the literal `gateway` host instead of the rendered gateway Service -> "upstream_host_mismatch" (the edge would never reach the gateway in-cluster)
  - Envoy rendered without readiness probe or without the WS-upgrade config -> "edge_incomplete"
</reject>
After:
<after>
  - `helm template` renders an Envoy Deployment + Service + ConfigMap; the ConfigMap dials `ai-proxy-gateway:8000` and holds only the JWKS placeholder; TLS + JWT come from Secrets via mount/initContainer; the Service exposes 8443+8080 at the values-driven type; `envoy.enabled=false` removes it all; `helm lint` exits 0; the frozen scaffold + datastore output is unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] The fully-templated config reproduces EVERY filter in the proven chain, in order, with the per-route ext_authz table intact (enabled on /v1/, disabled on /admin/ + / + 403 /internal/) — lowest confidence because a templating slip could silently drop a filter or flip an ext_authz toggle, changing the AUTH posture; if wrong: an unauthenticated path or a broken edge. Mitigation: a test parses the rendered config and asserts the 4 filters by name+order, the 4 route entries + their ext_authz disabled/enabled state, the WS upgrade, and the upstream host — `envoy --mode validate` confirms it parses.
  ⚠ [spec] initContainer base64url+sed faithfully reproduces the compose entrypoint (`printf '%s' | base64 | tr '+/' '-_' | tr -d '='`) — lowest confidence because a busybox vs coreutils base64 difference could change the JWKS; if wrong: the gateway rejects Envoy-minted JWT validation — caught by the e2e-core-flow task (live), flagged here.
  - [x] Service default type = ClusterIP (kind e2e dials in-cluster / port-forwards); cloud sets LoadBalancer via values — confirmed (draft default).
  - [x] Envoy reuses the gateway's jwt Secret (one source feeds the gateway verification + Envoy JWKS) — confirmed (single-secret).
  - [x] TLS secret REQUIRED when envoy.enabled in a non-dev env (fails closed) — confirmed; kind-bootstrap provides a self-signed Secret.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# --- one per Must ---

Scenario: Envoy edge renders (M1)
  Given default values (envoy.enabled=true)
  When the chart is rendered with `helm template`
  Then a Deployment "ai-proxy-envoy" runs image envoyproxy/envoy:v1.29-latest
  And a Service "ai-proxy-envoy" and a ConfigMap "ai-proxy-envoy" are rendered

Scenario: Templated config reproduces the proven filter chain (M2)
  Given default values
  When the rendered ConfigMap's Envoy config is parsed
  Then the :8443 http_filters are exactly [local_ratelimit, jwt_authn, ext_authz, router] in that order
  And the route table has per-route ext_authz ENABLED on /v1/, DISABLED on /admin/ and /, and a 403 direct_response on /internal/
  And the config preserves the websocket upgrade and the :8443/:8080 listeners + :9901 admin
  And `envoy --mode validate` (or yaml parse) accepts the rendered config

Scenario: Magic numbers are values-driven (M3)
  Given envoy.rateLimit.maxTokens=7 and envoy.extAuthz.timeout=9s and envoy.hsts.maxAge=11 and envoy.jwt.issuer=acme
  When the chart is rendered
  Then the Envoy config token_bucket.max_tokens is 7, ext_authz timeout is 9s, HSTS max-age is 11, and the jwt issuer is acme
  And a default render uses the proven defaults (50 / 2s / 63072000 / ai-proxy)
  And gateway_cluster's endpoint address is "ai-proxy-gateway" port 8000 (never the literal "gateway")

Scenario: JWT secret is handled out-of-band (M4)
  Given default values
  When the chart is rendered
  Then the ConfigMap contains the literal "GATEWAY_JWT_SECRET_BASE64URL" placeholder (never a resolved secret)
  And an initContainer reads GATEWAY_JWT_SECRET via secretKeyRef from ai-proxy-gateway-secrets/jwt-secret
  And the initContainer computes base64url + seds the template into a shared emptyDir the envoy container reads

Scenario: TLS comes from a Secret (M5)
  Given default values (envoy.tls.existingSecret set)
  When the chart is rendered
  Then the Deployment mounts the TLS Secret at /etc/envoy/certs (server.crt/server.key)
  And no cert/key value appears in any template or values default

Scenario: Service type is values-driven (M5)
  Given envoy.service.type overridden to LoadBalancer
  When the chart is rendered
  Then the Envoy Service type is LoadBalancer and exposes ports 8443 and 8080
  And the default render uses ClusterIP (kind) with the same ports

Scenario: Design-for-failure on the edge (M6)
  Given default values
  When the rendered Envoy Deployment is inspected
  Then it has readiness+liveness probes on /ready port 9901
  And resources requests+limits are set and a PodDisruptionBudget renders
  And the Envoy config preserves WebSocket upgrade (for the v52 realtime relay)

Scenario: External-ready / frozen schema (M7)
  Given envoy.enabled=false
  When the chart is rendered
  Then no Envoy Deployment, Service, or ConfigMap is rendered
  And the gateway + datastore objects and the frozen values keys are unchanged

Scenario: Admin port is network-restricted (M6 hardening — gate-driven v2)
  Given default values (envoy.networkPolicy.enabled=true)
  When the chart is rendered
  Then a NetworkPolicy "ai-proxy-envoy" selects the Envoy pods with policyTypes [Ingress]
  And its ingress allows ports 8443 and 8080 but NOT the admin port 9901
  And envoy.networkPolicy.enabled=false renders no NetworkPolicy

Scenario: Admin is localhost-only with a separate probe listener (M6 hardening — gate-driven v3)
  Given default values
  When the rendered Envoy config + Deployment are inspected
  Then the admin address is 127.0.0.1 (never 0.0.0.0) on the admin port 9901
  And a dedicated health listener binds 0.0.0.0:9902 with a health_check filter answering /ready 200
  And the readiness+liveness probes target the health port 9902, never the admin port 9901
  And the listener set is {8443, 8080, 9902}

Scenario: Images are digest-pinned (M1 hardening — gate-driven v4)
  Given default values
  When the rendered Envoy Deployment is inspected
  Then the envoy container image is the v1.29-latest tag pinned with an @sha256: digest
  And the initContainer image is busybox:1.36 pinned with an @sha256: digest
  And an operator can still override either image via values

# --- one per Reject ---

Scenario: Secret never lands in the ConfigMap (secret_in_configmap_forbidden)
  Given any default render
  When the ConfigMap is scanned
  Then neither the JWT secret nor its base64url appears — only the placeholder
  And the gateway's jwtSecret stays referenced, never inlined

Scenario: TLS literal is forbidden (tls_literal_forbidden)
  Given a template or values default carrying a cert/key
  When the default render is scanned
  Then no PEM/cert/key material appears outside a referenced Secret

Scenario: Invalid extended chart fails fast (chart_invalid)
  Given a misconfiguration (envoy.tls.existingSecret="" with envoy.enabled and env=production)
  When the chart is rendered
  Then `helm template` exits non-zero with a clear tls_secret_ref_missing message
  And a valid default render still exits 0 and lints clean

Scenario: Upstream host mismatch is rejected (upstream_host_mismatch)
  Given the rendered Envoy ConfigMap
  When gateway_cluster's endpoint host is compared to the gateway Service name
  Then it equals ai-proxy-gateway (NOT the literal "gateway")
  And a config still dialing "gateway" fails the render check

Scenario: Incomplete edge is rejected (edge_incomplete)
  Given the rendered Envoy Deployment + config
  When inspected
  Then a readiness probe AND the WS-upgrade config are both present
  And an Envoy missing either is treated as a failing render
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

CONTRACT = the `envoy{}` values sub-schema (input) + the rendered k8s objects (output).
EXTENDS the frozen `envoy: {}` placeholder; touches NO other frozen key.

```
# --- INPUT: values.yaml `envoy{}` sub-schema (defaults shown) ---
envoy:
  enabled: true
  image: envoyproxy/envoy:v1.29-latest@sha256:5a292b91adc87aa56146fb9ee52fc85c30570d0f175d95b48cde8035a2e641dd   # digest-pinned (tag kept for readability)
  initImage: busybox:1.36@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662   # jwks-substitute init; digest-pinned
  replicas: 2
  service:
    type: ClusterIP            # kind default; LoadBalancer | NodePort for cloud (values-only)
    httpsPort: 8443
    httpPort: 8080
  tls:
    existingSecret: "ai-proxy-edge-tls"   # kubernetes.io/tls Secret (server.crt/key); operator/kind-bootstrap provides
    minimumProtocolVersion: TLSv1_2
  adminPort: 9901              # bound to 127.0.0.1 ONLY (off-pod unreachable; config_dump never exposed)
  healthPort: 9902             # dedicated 0.0.0.0 health listener for kubelet probes (health_check filter, no admin surface)
  jwt:
    issuer: "ai-proxy"         # jwt_authn provider issuer (templated)
  rateLimit:                   # :8443 local_ratelimit token bucket (templated)
    maxTokens: 50
    tokensPerFill: 50
    fillInterval: 1s
  extAuthz:
    timeout: 2s                # ext_authz http_service timeout (templated)
  upstream:
    connectTimeout: 0.5s       # gateway_cluster connect_timeout (templated)
  hsts:
    maxAge: 63072000           # Strict-Transport-Security max-age (templated)
    includeSubDomains: true
  resources:
    requests: { cpu: 100m, memory: 128Mi }
    limits:   { cpu: "1",  memory: 256Mi }
  probes:
    readiness: { initialDelaySeconds: 5,  periodSeconds: 10, timeoutSeconds: 3, failureThreshold: 6 }
    liveness:  { initialDelaySeconds: 15, periodSeconds: 15, timeoutSeconds: 3, failureThreshold: 3 }
  pdb:
    minAvailable: 1
  networkPolicy:
    enabled: true              # restrict admin :9901 to node-level probes; ENFORCED in cloud, INERT in kind (kindnet ignores NP)

# --- OUTPUT: objects `helm template` MUST render (default values) ---
ConfigMap   ai-proxy-envoy   data.envoy.yaml = FULLY Helm-TEMPLATED Envoy config faithful to envoy-prod.yaml:
                             :8443 http_filters [local_ratelimit · jwt_authn · ext_authz · router] IN ORDER;
                             route table (ext_authz enabled /v1/, disabled /admin/ + /, 403 /internal/); WS upgrade;
                             gateway_cluster host=ai-proxy-gateway:8000; ext_authz path_prefix=/internal/authz;
                             token_bucket/timeouts/HSTS/issuer/TLS-min from envoy.* values; the literal
                             "GATEWAY_JWT_SECRET_BASE64URL" placeholder (never the secret); data listeners 8443+8080;
                             admin bound 127.0.0.1:9901 (off-pod unreachable); dedicated health listener 0.0.0.0:9902
                             (health_check filter, /ready -> 200, NO admin/config_dump surface)
Deployment  ai-proxy-envoy   image=<envoy.image> DIGEST-PINNED by default (envoyproxy/envoy:v1.29-latest@sha256:5a292b91…)
                             initContainer image=<envoy.initImage> DIGEST-PINNED (busybox:1.36@sha256:73aaf090…)
                             initContainer: reads GATEWAY_JWT_SECRET (secretKeyRef ai-proxy-gateway-secrets/jwt-secret),
                               base64url + sed the ConfigMap template -> emptyDir /etc/envoy/generated/envoy.yaml
                             container: envoy -c /etc/envoy/generated/envoy.yaml; mounts TLS Secret at /etc/envoy/certs;
                               readiness+liveness GET /ready on the health port :9902 (NOT the admin port); resources req+limits
Service     ai-proxy-envoy   type=<envoy.service.type>; ports 8443(https)+8080(http)
PDB         ai-proxy-envoy   minAvailable=<envoy.pdb.minAvailable>
NetworkPolicy ai-proxy-envoy (when networkPolicy.enabled) podSelector=envoy; policyTypes=[Ingress];
                             ingress allows ONLY 8443+8080 — admin :9901 intentionally omitted (pod-to-pod
                             denied; config_dump leaks the substituted JWKS secret). kubelet probes (node-sourced)
                             are exempt from NP. Inert under kindnet; enforced under a NP-capable CNI.

Rejections -> render-time failures (asserted over `helm template` exit + parsed YAML):
  secret_in_configmap_forbidden -> JWT secret/base64url present in the rendered ConfigMap (only the placeholder allowed)
  tls_literal_forbidden         -> cert/key material outside a referenced Secret in the default render
  chart_invalid                 -> `helm template`/`helm lint` non-zero on a misconfig (envoy.tls.existingSecret="" + enabled + non-dev env -> tls_secret_ref_missing; default render exits 0)
  upstream_host_mismatch        -> gateway_cluster endpoint host != ai-proxy-gateway
  edge_incomplete               -> Envoy Deployment missing a readiness probe OR the config missing WS-upgrade

Invariants:
  - envoy.enabled=false renders NONE of its objects (ConfigMap/Deployment/Service/PDB).
  - The templated config is FAITHFUL to envoy-prod.yaml: filters [local_ratelimit·jwt_authn·ext_authz·router] in order; per-route ext_authz enabled on /v1/, disabled on /admin/ + /, 403 on /internal/; WS upgrade; HSTS at route_config. It parses (`envoy --mode validate` or yaml.safe_load).
  - The JWT secret is NEVER in the ConfigMap; only the initContainer (out-of-band) resolves it. The SAME Secret feeds the gateway + Envoy JWKS (single source).
  - Upstream host is helper-derived (ai-proxy.gateway.fullname), never the literal `gateway`.
  - NO frozen scaffold/gateway/datastore key is modified; only the `envoy{}` sub-tree is added.
  - When networkPolicy.enabled (default), a NetworkPolicy restricts ingress to 8443+8080 ONLY — the admin :9901 is never an allowed pod-to-pod ingress port (config_dump exposes the substituted JWKS secret); networkPolicy.enabled=false renders no NetworkPolicy.
  - Defense-in-depth (v3): the Envoy admin binds 127.0.0.1:adminPort ONLY (never 0.0.0.0) — unreachable off-pod regardless of CNI/NetworkPolicy enforcement; kubelet probes hit a SEPARATE 0.0.0.0:healthPort health listener (health_check filter → /ready 200) that exposes no admin/config_dump surface. Listener set = {8443, 8080, healthPort}; the probe port is healthPort, never adminPort.
  - Supply-chain (v4): both images (envoy.image, envoy.initImage) are DIGEST-PINNED (`tag@sha256:…`) by default — immutable pulls, tag retained for readability; an operator may still override either via values.
```

Least-sure flag surfaced at freeze: [contract] the fully-templated config must reproduce every filter in order + the per-route ext_authz table (enabled /v1/, disabled /admin/ + /, 403 /internal/) — a templating slip could silently change the AUTH posture (unauth path or broken edge). Mitigation: tests assert filters by name+order, the route/ext_authz toggle states, WS upgrade, upstream host, AND that the rendered config parses. Secondary: [spec] initContainer base64url must byte-match the proven compose entrypoint (else JWKS mismatch → gateway rejects Envoy JWT; caught live in e2e-core-flow).

Status: FROZEN @ v4 — approved by Tin (2026-06-26). v4 = third gate-driven change request: Tin chose "pin image digests now" → both envoy.image + envoy.initImage digest-pinned (`tag@sha256:…`, resolved live from the Docker registry); initContainer image made values-driven. v3 = "more hardening first" → admin binds 127.0.0.1 ONLY + dedicated 0.0.0.0:healthPort health listener (off-pod admin unreachable regardless of CNI). v2 = "harden admin :9901 now" → `networkPolicy` input + NetworkPolicy output + restriction invariant. v1 = fully-templated config per Tin's freeze choice.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: every Must + every Reject ≥1 test; assertions parse the templated `data["envoy.yaml"]` (behavior), never template text.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_envoy_edge_renders (M1): default render → Deployment+Service+ConfigMap "ai-proxy-envoy", image envoyproxy/envoy:v1.29-latest.
  - test_filter_chain_faithful (M2): parse ConfigMap envoy.yaml → :8443 http_filters == [local_ratelimit, jwt_authn, ext_authz, router] in order; routes: /v1/ ext_authz enabled (check_settings), /admin/ + / disabled:true, /internal/ 403 direct_response; WS upgrade present; admin :9901; the config yaml-parses.
  - test_magic_numbers_values_driven (M3): --set rateLimit.maxTokens=7/extAuthz.timeout=9s/hsts.maxAge=11/jwt.issuer=acme → those appear in the config; default render uses 50/2s/63072000/ai-proxy; gateway_cluster host==ai-proxy-gateway:8000.
  - test_jwt_secret_out_of_band (M4): ConfigMap holds literal GATEWAY_JWT_SECRET_BASE64URL; an initContainer reads GATEWAY_JWT_SECRET via secretKeyRef ai-proxy-gateway-secrets/jwt-secret; seds into a shared emptyDir the envoy container reads.
  - test_tls_from_secret (M5): Deployment mounts envoy.tls.existingSecret at /etc/envoy/certs; Service exposes 8443+8080; no cert/key literal anywhere.
  - test_service_type_values_driven (M5): --set service.type=LoadBalancer → Service type LoadBalancer; default ClusterIP; both expose 8443+8080.
  - test_design_for_failure (M6): readiness+liveness GET /ready :9901; resources req+limits; PDB renders.
  - test_external_ready_frozen (M7): envoy.enabled=false → no Envoy objects; gateway+datastore objects + frozen keys unchanged.
  - test_secret_not_in_configmap (secret_in_configmap_forbidden): default ConfigMap contains the placeholder, never a resolved secret value.
  - test_no_tls_literal (tls_literal_forbidden): no BEGIN CERTIFICATE/PRIVATE KEY in values or rendered output; envoy.tls is a ref only.
  - test_chart_invalid_fails (chart_invalid): tls.existingSecret="" + enabled + env=production → non-zero tls_secret_ref_missing; default render exits 0 + lints clean.
  - test_upstream_host_match (upstream_host_mismatch): gateway_cluster endpoint host == ai-proxy-gateway (never literal "gateway").
  - test_edge_complete (edge_incomplete): Deployment has a readiness probe AND the config has the WS upgrade.
  - test_admin_port_network_restricted (M6 hardening v2): default render → NetworkPolicy "ai-proxy-envoy" selects envoy pods, policyTypes [Ingress], ingress allows {8443,8080} and NOT 9901; networkPolicy.enabled=false → no NetworkPolicy.
  - test_admin_localhost_only (M6 hardening v3): admin address==127.0.0.1 on :9901; a health listener binds 0.0.0.0:9902 with a health_check filter (/ready); probes target :9902 not :9901; listener set=={8443,8080,9902}.
  - test_images_digest_pinned (M1 hardening v4): envoy container image == v1.29-latest tag + @sha256:<64hex>; initContainer image == busybox:1.36 + @sha256:<64hex>; both overridable via --set.
</test_plan>

Tests live in: `tests/helm/test_envoy_edge_manifests.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `charts/ai-proxy/values.yaml` (extend `envoy{}` ONLY) · `charts/ai-proxy/templates/_helpers.tpl` (ADD envoy name/label helpers + the tls-secret guard; frozen helpers untouched) · `charts/ai-proxy/templates/envoy-configmap.yaml` · `envoy-deployment.yaml` · `envoy-service.yaml` · `envoy-pdb.yaml` · `envoy-networkpolicy.yaml` (v2 gate-driven hardening)
Strategy (ordered batches): 1. extend `envoy{}` values sub-schema + add helpers (envoy.fullname/selectorLabels + ai-proxy.envoy.validateTLS guard). 2. envoy-configmap.yaml (fully-templated config: filters, route table, WS, HSTS, token bucket/timeouts/issuer/TLS-min from values, upstream = gateway helper, JWKS placeholder, admin block). 3. envoy-deployment.yaml (initContainer base64url+sed → emptyDir, TLS Secret mount, GATEWAY_JWT_SECRET secretKeyRef, /ready probes, resources). 4. envoy-service.yaml (values-driven type, 8443+8080). 5. envoy-pdb.yaml. Re-run the FULL tests/helm suite after each batch.
Safety rule (feature-specific): the JWT secret NEVER enters the ConfigMap (only the initContainer resolves it); TLS/JWT are secretKeyRef/mount only — no literal; touch ONLY the `envoy{}` sub-tree + NEW files.
Code lives in: `charts/ai-proxy/`
Constraints: do NOT change any test or the contract; allow-list packages only (pure Helm/YAML, no new dep); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `tests/helm/` 45 passed (16 envoy incl. auth/F1 + v2 NetworkPolicy + v3 localhost-admin asserts).
- [x] coverage did not decrease — pure additive Helm templates + tests; no app code touched (run with --no-cov; chart has no coverage surface).
- [x] no test or contract was altered during build — §3 re-FROZEN @ v3 via two gate-driven change requests (Tin-approved at the gate); build edits were template/values only. Test edits happened in the `tests` phase each heal cycle, never during build.
- [x] the green was EARNED — adversarial refute-read (security-expert) run; it found a HIGH base64-newline-wrap defect a render-only test missed → FIXED (`tr -d '\n'`) + a render-level regression guard added + proven behaviorally (64-byte secret → single-line valid JWKS). Auth-posture asserts strengthened (failure_mode_allow:false, path_prefix, allowed/upstream headers).
- [x] concurrency / timing — initContainer ordering is the only sequencing: it MUST write the generated config before the envoy container reads it; k8s init ordering guarantees this. emptyDir shared, readOnly on the main mount.
- [x] no exposed secrets, injection openings, or unexpected dependencies — JWT secret stays out of the ConfigMap (placeholder only, asserted); TLS via Secret mount; no new chart dependency (pure Helm/YAML). The refute-read's admin-:9901 exposure is hardened THREE ways: (1) admin binds 127.0.0.1 ONLY — unreachable off-pod regardless of CNI (v3); (2) probes use a dedicated 0.0.0.0:9902 health listener carrying only health_check (no admin/config_dump surface — verified by parsing the rendered config); (3) a default-on NetworkPolicy denies pod-to-pod ingress to :9901 (v2). Tests: test_admin_localhost_only + test_admin_port_network_restricted. Supply chain: both images digest-pinned `tag@sha256:` (v4, test_images_digest_pinned).
- [x] layering & dependencies follow CONVENTIONS.md — extends `envoy{}` only; reuses frozen gateway helpers; mirrors the gateway/datastore secret-guard family.
- [x] a person reviewed and approved the change — security gate signed off by Tin (2026-06-26) after three gate-driven hardening CRs (NetworkPolicy · localhost-admin+health-listener · digest-pinned images), each independently tested.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `helm template` renders Envoy ConfigMap+Deployment+Service+PDB+NetworkPolicy (image envoyproxy/envoy:v1.29-latest@sha256:…, digest-pinned) — confirmed by render + M1 test.
- [x] The templated `envoy.yaml` parses AND its :8443 http_filters are [local_ratelimit·jwt_authn·ext_authz·router] in order, with /v1/ ext_authz enabled, /admin/ + / disabled, /internal/ 403, WS upgrade, admin :9901 — confirmed by test_filter_chain_faithful (now also asserts failure_mode_allow:false + path_prefix + allowed/upstream headers).
- [x] gateway_cluster endpoint == ai-proxy-gateway:8000 (never literal "gateway"); rate-limit/timeout/HSTS/issuer/TLS-min flow from envoy.* values — confirmed by test_upstream_host_match + test_magic_numbers_values_driven.
- [x] The ConfigMap holds the literal GATEWAY_JWT_SECRET_BASE64URL placeholder (never the secret); an initContainer reads it via secretKeyRef + seds into a shared emptyDir — confirmed by test_jwt_secret_out_of_band + render grep; base64url pipeline now strips newlines (proven: 64-byte secret → single-line valid JWKS).
- [x] TLS from envoy.tls.existingSecret mounted at /etc/envoy/certs; no PEM literal anywhere; Service exposes 8443+8080 at the values-driven type — confirmed by test_tls_from_secret + test_no_tls_literal.
- [x] design-for-failure: /ready probes on the dedicated health port :9902 (admin :9901 is loopback-only), resources req+limits, PDB — confirmed by test_design_for_failure + test_admin_localhost_only; initContainer hardened (runAsNonRoot, drop ALL, readOnlyRootFilesystem).
- [x] envoy.enabled=false removes all Envoy objects; gateway+datastores+frozen keys unchanged; helm lint 0; full tests/helm green — confirmed by test_external_ready_frozen + `helm lint` (0 failed) + 46/46.
- [x] Misconfig (envoy.tls.existingSecret="" + enabled + non-dev) fails with tls_secret_ref_missing; default render exits 0 — confirmed by test_chart_invalid_fails.
- [x] both images (envoy + busybox init) are digest-pinned `tag@sha256:<64hex>` and remain operator-overridable — confirmed by test_images_digest_pinned + render grep.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new helper is consumed: envoy.fullname/selectorLabels by all 5 envoy templates; envoy.validateTLS by envoy-deployment.yaml line 1; gateway.fullname/jwtSecretName reused in configmap/deployment. ConfigMap→Deployment (volume), initContainer→main container (shared emptyDir), Service→pods (selector). No orphan template.
- [x] DEAD-CODE (code) — no unused value key: every envoy.* knob is referenced in a template (verified by the magic-numbers test driving each + render inspection). No symbol introduced without a consumer.
- [x] SEMANTIC (prose / non-code) — read infra/envoy/envoy-prod.yaml in full vs the templated ConfigMap: filter order, per-route ext_authz table, WS upgrade, HSTS, upstream host all faithful. Intended k8s deltas (each a deliberate, tested improvement): gateway-Service host; newline-strip hardening; admin → 127.0.0.1 + dedicated :9902 health listener (v3); default-on NetworkPolicy (v2); digest-pinned images (v4). refute-read (security-expert) verdict: "auth posture template is faithful."

### GATE RECORD
Outcome: PASS   <!-- security gate — HIGH refute-read finding FIXED+proven; 3 gate-driven hardening CRs added (NP · localhost-admin+health-listener · digest-pins) -->
If RISK-ACCEPTED -> owner: — · ticket: — · expires: —   (never for a security gap)
Reviewed by: Tin · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · seeded] Restrict the Envoy admin :9901 — RESOLVED IN-TASK three ways (two gate-driven CRs): admin binds 127.0.0.1 ONLY (v3, off-pod unreachable regardless of CNI) + a dedicated 0.0.0.0:9902 health listener (health_check only, no admin surface) for probes + a default-on NetworkPolicy (v2) denying pod-to-pod ingress to :9901. NP enforcement is CNI-dependent (inert under kindnet) → admin-restriction is validated only on a NP-capable cluster; the localhost-admin bind needs no CNI. The kind-bootstrap/cloud-apply runbook should note both (evidence: task-3 refute-read MEDIUM → mitigated).
- [SPEC · seeded] Pin image digests — RESOLVED IN-TASK (v4 gate-driven, Tin chose "pin now"): both envoy.image + envoy.initImage default to `tag@sha256:` (digests resolved live from the Docker registry), still values-overridable. Follow-up: wire a Renovate/dependabot digest-bump so the pins don't rot (evidence: supply-chain hygiene → mitigated; automation is the open tail).
- [SPEC · open] Expose Envoy stats/metrics for Prometheus now that admin is loopback-only — e.g. a stats-only listener or a sidecar scraping 127.0.0.1:9901/stats (evidence: localhost-admin removes off-pod /stats access; observability task).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [TDD · folded] A render-only helm test cannot catch a runtime shell defect; the base64 newline-wrap (busybox wraps at 76 cols → broke the inline JWKS for >57-byte secrets) slipped past 13 green tests and was caught only by an adversarial refute-read. Added a render-level guard asserting the pipeline strips newlines (`tr -d '\n'`), but the real proof is a live e2e exercising the initContainer (evidence: task-3 HIGH finding; covered live by e2e-core-flow). [folded foundation-version 39]
- [ADD · folded] Faithfully porting a proven config (`infra/envoy/envoy-prod.yaml`) carries its latent bugs forward — the compose entrypoint had the SAME missing `tr -d '\n'`. "Faithful to the proven artifact" must mean faithful to intent, hardened where the runtime differs (busybox vs the compose shell) (evidence: task-3 refute-read). [folded foundation-version 39]
