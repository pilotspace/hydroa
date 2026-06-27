# TASK: Next.js dashboard chart: standalone Dockerfile + Deployment/Service (BFF→gateway in-cluster)

slug: dashboard-chart · created: 2026-06-26 · stage: production
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

Touches (files · symbols · signatures): NEW dashboard image + chart templates filling the FROZEN `dashboard{}` placeholder; grounded in the real Next.js dashboard + the gateway chart pattern it mirrors —
  - `apps/dashboard/` — the real Next.js 16 app (`next: ^16.2.9`, React 19). `next.config.ts` ships security headers (CSP `connect-src 'self'`) but **NO `output: 'standalone'`** → THIS task adds it so the image can ship the minimal standalone server (`node server.js`, not `next start`). Build = `next build`; no Dockerfile exists yet (`apps/dashboard/Dockerfile` is NEW). `proxy.ts` = cookie route-guard on /app/* (Node runtime, self-hosted behind Envoy).
  - `apps/dashboard/app/api/gw/[...path]/route.ts:43` `gatewayUrl()` — the canonical BFF→gateway resolver: `process.env.GATEWAY_URL ?? NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080"`. ALL BFF/server routes (auth login/signup/me, oidc, gw proxy) read the SAME chain. So the Deployment sets `GATEWAY_URL` to the in-cluster gateway Service URL (server-to-server, never the public edge — consistent with `connect-src 'self'`). Companion knobs: `GATEWAY_PROXY_TIMEOUT_MS` (default 15000, design-for-failure timeout) · `GW_MAX_BODY_BYTES` (default 32 MiB).
  - NO dashboard health route exists (`app/api/` = {auth, gw}) → probe target is a freeze decision: the PUBLIC marketing `/` (proxy.ts does NOT gate it → 200, no auth/DB) vs a NEW minimal `app/api/health/route.ts`.
  - `charts/ai-proxy/values.yaml:203` — the FROZEN `dashboard: {}` placeholder THIS task fills. Mirror the gateway pattern: shared top-level `image{}` is the GATEWAY image (`ghcr.io/pilotspace/ai-proxy-gateway:0.4.0`) — the dashboard needs its OWN `dashboard.image{}` (separate repo/tag/pullPolicy). Reuse `gateway.fullname`/`serviceDNS` to point `GATEWAY_URL` in-cluster.
  - `apps/gateway/Dockerfile` — the multi-stage non-root (uid 1000) pattern to mirror; `charts/ai-proxy/templates/gateway-deployment.yaml` + `gateway-service.yaml` — the Deployment/Service shape to mirror (ClusterIP, port→named targetPort, env block, probes liveness/readiness/startup, resources req+limits). `_helpers.tpl` — REUSE `ai-proxy.fullname`/`.labels`/`gateway.fullname`/`serviceDNS`; ADD `ai-proxy.dashboard.fullname`/selectorLabels (frozen helpers untouched).
Context (working folder): `.add/milestones/v53/MILESTONE.md` line 32 (dashboard-chart scope) + line 23 (DASHBOARD = STANDALONE NEXT SERVER glossary: `output:'standalone'` Node Deployment, BFF→gateway via in-cluster Service URL, never the public edge) + exit criterion line 44. `charts/ai-proxy/templates/envoy-configmap.yaml` (task-3 edge — its route table currently sends `/` → gateway_cluster; the milestone's browser→dashboard path-routing is the OPEN interplay).
Honors (patterns / conventions): FROZEN-SCHEMA → fill `dashboard{}` ONLY; gateway/datastores/envoy keys + templates untouched. DESIGN-FOR-FAILURE → liveness/readiness/startup probes, resources req+limits, PDB, non-root. SECRETS-NEVER-IN-CHART → the dashboard needs no secret of its own (the BFF forwards the browser's JWT cookie; GATEWAY_URL is a non-secret in-cluster URL); any future secret = secretRef, never a literal. CSP-CONSISTENT → BFF reaches the gateway server-to-server via the in-cluster Service, matching `connect-src 'self'`.
Anchors the contract cites: NEW `apps/dashboard/Dockerfile` (multi-stage Next standalone, non-root, `node server.js`, PORT 3000) · `apps/dashboard/next.config.ts` (+ `output: 'standalone'`) · NEW `charts/ai-proxy/templates/dashboard-deployment.yaml` (image=`dashboard.image`, env GATEWAY_URL=in-cluster gateway Service, probes, resources) · `dashboard-service.yaml` (ClusterIP :3000) · `dashboard-pdb.yaml` · the EXTENDED `dashboard{}` values sub-schema (`enabled`, `image{}`, `replicas`, `service`, `gatewayUrl`, `proxyTimeoutMs`, `maxBodyBytes`, `resources`, `probes`, `pdb`, `podSecurityContext`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: The Next.js dashboard as a deployable in-cluster unit — a standalone image (NEW Dockerfile + `output:'standalone'`) + Deployment/Service/PDB filling the frozen `dashboard{}` block, with its BFF wired to the gateway over the in-cluster Service URL, AND the Envoy edge extended to path-route browser traffic to the dashboard while API paths stay on the gateway.
Framings weighed: standalone Node server (`output:'standalone'` → `node server.js`) in a minimal multi-stage image, edge route-split browser→dashboard / API→gateway (chosen — milestone glossary "DASHBOARD = STANDALONE NEXT SERVER" + exit criterion; smallest runtime, server-to-server BFF matches `connect-src 'self'`) · `next start` full-image (rejected — ships dev/build deps, larger attack surface, not the standalone contract) · serve the dashboard THROUGH the gateway (rejected — couples two services, the gateway is a Python API not a static/SSR host). Edge routing extended HERE per Tin (one coherent task), gated on dashboard.enabled so the envoy config stays valid when the dashboard is off.
Must:
<must>
  - M1 — the dashboard ships as a STANDALONE image: `apps/dashboard/next.config.ts` sets `output: 'standalone'`; a NEW multi-stage `apps/dashboard/Dockerfile` builds it and the runtime stage runs the minimal server (`node server.js`) as a NON-ROOT user on PORT 3000, copying `.next/standalone` + `.next/static` + `public` (no dev deps, no source).
  - M2 — `dashboard.enabled` (default true) renders a Deployment (image from `dashboard.image{}` — its OWN repo/tag/pullPolicy, NOT the shared gateway `image{}`) whose env sets `GATEWAY_URL` to the in-cluster gateway Service URL (helper-derived `ai-proxy.gateway.fullname`:8000, server-to-server — never the public edge), plus `GATEWAY_PROXY_TIMEOUT_MS` and `GW_MAX_BODY_BYTES` from values (design-for-failure timeout + body cap).
  - M3 — a ClusterIP Service `ai-proxy-dashboard` exposes :3000 (named port → targetPort), selecting the dashboard pods via NEW `ai-proxy.dashboard.fullname`/selectorLabels helpers (frozen helpers untouched); the gateway/datastores/envoy objects are unchanged by the dashboard templates themselves.
  - M4 — design-for-failure: a NEW minimal `apps/dashboard/app/api/health/route.ts` returns 200 (no auth/DB/SSR); liveness+readiness+startup probes hit `/api/health`; resources requests+limits set; a PodDisruptionBudget renders; the pod runs non-root (runAsNonRoot, matching the Dockerfile user).
  - M5 — the Envoy edge path-routes browser→dashboard: when `dashboard.enabled`, the envoy ConfigMap adds a `dashboard_cluster` (STRICT_DNS → the dashboard Service :3000) and the catch-all `/` route targets `dashboard_cluster` (ext_authz disabled — the dashboard owns its cookie auth), while `/v1/` (ext_authz ON) and `/admin/` stay on `gateway_cluster` and `/internal/` stays 403. When `dashboard.enabled=false`, `/` falls back to `gateway_cluster` (today's behavior) and no dashboard_cluster renders.
  - M6 — external-ready/frozen-schema: `dashboard.enabled=false` renders NO dashboard Deployment/Service/PDB and reverts the edge `/` route to the gateway; only the `dashboard{}` values sub-tree is added; `helm lint`/`helm template` stay green on defaults; no secret value is shipped (the dashboard needs none — the BFF forwards the browser's JWT cookie; GATEWAY_URL is a non-secret in-cluster URL).
</must>
Reject:
<reject>
  - the dashboard image built as a non-standalone `next start` runtime, or running as root, or on a port other than the values-driven one -> "image_not_standalone"
  - `GATEWAY_URL` (or the BFF gateway base) pointing at the public edge / a localhost / a hardcoded literal instead of the in-cluster gateway Service -> "gateway_url_not_in_cluster"
  - a probe pointed at a path that requires auth/DB (e.g. `/app` or `/`-SSR) instead of the dependency-free health route -> "probe_target_unsafe"
  - the edge still sending browser `/` to the gateway when dashboard.enabled (browser never reaches the dashboard), OR `/v1/` losing its ext_authz check -> "edge_route_split_broken"
  - `helm lint`/`helm template` non-zero on the extended chart (default values) -> "chart_invalid"
  - any secret value, or a frozen gateway/datastore/envoy values key, shipped/modified by the dashboard sub-tree -> "frozen_schema_violation"
</reject>
After:
<after>
  - `helm template` renders a standalone-image dashboard Deployment+Service+PDB whose BFF dials the in-cluster gateway Service; probes hit `/api/health`; the edge route table sends browser `/` → the dashboard and `/v1/`+`/admin/` → the gateway; `dashboard.enabled=false` removes it all and reverts `/` → gateway; `helm lint` exits 0; the frozen gateway/datastore/envoy output (except the intended additive `/`-route + dashboard_cluster) is unchanged; the Dockerfile builds a non-root standalone server.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] Extending the task-3 Envoy ConfigMap in-place (add dashboard_cluster + flip the `/` route, gated on dashboard.enabled) keeps the frozen task-3 auth posture intact — lowest confidence because the `/` route is the catch-all and a slip could send `/v1/` or `/admin/` to the dashboard or drop an ext_authz check, opening an unauth API path. Mitigation: tests assert the FULL post-split route table (/v1/→gateway+ext_authz ON, /admin/→gateway, /internal/→403, /→dashboard when enabled / →gateway when disabled) AND that task-3's envoy suite still passes unchanged; the config still yaml-parses.
  ⚠ [spec] `output:'standalone'` + `node server.js` is the correct Next 16 runtime and the standalone bundle includes everything the BFF/server routes need at runtime — lowest confidence because a missing traced dependency surfaces only at container start (not at `helm template`); if wrong: the dashboard pod crash-loops. Mitigation: flagged for the live e2e (kind-bootstrap / e2e-ui) which actually starts the image; the chart tests assert the wiring (command, port, env), the Dockerfile the build shape.
  - [x] The dashboard needs no Secret of its own (BFF forwards the browser cookie; GATEWAY_URL non-secret) — confirmed (grounding: all BFF routes read GATEWAY_URL + forward the request cookie).
  - [x] Probe target = NEW `/api/health` (Tin) — confirmed; dependency-free 200.
  - [x] Edge routing extended in THIS task (Tin) — confirmed; gated on dashboard.enabled.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# --- one per Must ---

Scenario: Standalone image (M1)
  Given apps/dashboard/Dockerfile and next.config.ts
  When they are inspected
  Then next.config.ts sets output: 'standalone'
  And the Dockerfile is multi-stage, copies .next/standalone + .next/static + public, runs `node server.js` as a non-root user, and EXPOSEs the dashboard port (3000)

Scenario: Deployment wires the BFF to the in-cluster gateway (M2)
  Given default values (dashboard.enabled=true)
  When the chart is rendered
  Then a Deployment "ai-proxy-dashboard" runs the dashboard.image{} (its own repo/tag, not the gateway image{})
  And its env sets GATEWAY_URL to http://ai-proxy-gateway:8000 (in-cluster Service, never the public edge)
  And GATEWAY_PROXY_TIMEOUT_MS and GW_MAX_BODY_BYTES come from values

Scenario: Service exposes the dashboard (M3)
  Given default values
  When the chart is rendered
  Then a ClusterIP Service "ai-proxy-dashboard" exposes port 3000 to a named targetPort
  And it selects the dashboard pods via the dashboard selector labels

Scenario: Design-for-failure (M4)
  Given default values
  When the rendered Deployment is inspected
  Then liveness, readiness, and startup probes all GET /api/health
  And resources requests+limits are set, a PodDisruptionBudget renders, and the pod runs non-root
  And apps/dashboard/app/api/health/route.ts returns 200 without auth/DB

Scenario: Edge path-routes browser to the dashboard (M5)
  Given default values (dashboard.enabled=true)
  When the rendered Envoy config is parsed
  Then a dashboard_cluster resolves to the dashboard Service :3000
  And the catch-all "/" route targets dashboard_cluster with ext_authz disabled
  And "/v1/" still targets gateway_cluster with ext_authz ENABLED, "/admin/" targets gateway_cluster, "/internal/" is 403

Scenario: Dashboard pod is hardened (M4 hardening — refute-read v2)
  Given default values
  When the rendered dashboard Deployment + NetworkPolicy are inspected
  Then the dashboard container has a securityContext with allowPrivilegeEscalation:false and capabilities.drop [ALL]
  And a NetworkPolicy "ai-proxy-dashboard" allows ingress only from envoy pods on :3000 and egress only to the gateway (:8000) + DNS
  And networkPolicy.enabled=false renders no NetworkPolicy

Scenario: External-ready / frozen schema (M6)
  Given dashboard.enabled=false
  When the chart is rendered
  Then no dashboard Deployment, Service, or PDB is rendered
  And the Envoy "/" route falls back to gateway_cluster and no dashboard_cluster renders
  And the gateway + datastore + envoy(other) objects and the frozen values keys are unchanged

# --- one per Reject ---

Scenario: Non-standalone / root image is rejected (image_not_standalone)
  Given the Dockerfile or next.config
  When inspected
  Then a `next start` runtime, a root USER, or a missing output:'standalone' is treated as a failing build

Scenario: Gateway URL must be in-cluster (gateway_url_not_in_cluster)
  Given the rendered dashboard Deployment env
  When GATEWAY_URL is read
  Then it is the in-cluster gateway Service URL, never the public edge, a localhost, or a hardcoded literal

Scenario: Probe target must be dependency-free (probe_target_unsafe)
  Given the rendered probes
  When their httpGet paths are read
  Then every probe targets /api/health, never /app or a `/`-SSR/auth/DB path

Scenario: Edge route split must hold (edge_route_split_broken)
  Given dashboard.enabled=true
  When the rendered route table is parsed
  Then browser "/" reaches the dashboard AND "/v1/" keeps its ext_authz check on the gateway
  And a config still sending "/" to the gateway (when enabled) or dropping /v1/ ext_authz fails the check

Scenario: Invalid extended chart fails fast (chart_invalid)
  Given the extended chart at default values
  When `helm template` / `helm lint` runs
  Then both exit 0; a misconfiguration exits non-zero

Scenario: Frozen schema is not violated (frozen_schema_violation)
  Given the default render + values
  When scanned
  Then no secret value is shipped and no frozen gateway/datastore/envoy values key is modified by the dashboard sub-tree

Scenario: Server BFF must not read a NEXT_PUBLIC gateway var (server_resolver_reads_public_var)   # v3
  Given the server-side BFF route handlers that resolve the in-cluster gateway URL
  When their gatewayUrl() resolver is read
  Then it reads ONLY the non-public GATEWAY_URL (then the localhost default), never NEXT_PUBLIC_GATEWAY_URL
  And any server resolver still referencing a NEXT_PUBLIC_-prefixed gateway var fails the check
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

CONTRACT = the `dashboard{}` values sub-schema + the standalone image shape + the rendered k8s
objects + the additive Envoy route-split. EXTENDS the frozen `dashboard: {}` placeholder; the ONLY
edit to a sibling-frozen artifact is the ADDITIVE Envoy route-split (dashboard_cluster + `/`-route),
gated on dashboard.enabled (Tin-approved at specify).

```
# --- INPUT: values.yaml `dashboard{}` sub-schema (defaults shown) ---
dashboard:
  enabled: true
  image:                       # the dashboard's OWN image (separate from the shared gateway image{})
    repository: ghcr.io/pilotspace/ai-proxy-dashboard
    tag: "0.4.0"
    pullPolicy: IfNotPresent
  replicas: 2
  service:
    type: ClusterIP
    port: 3000
  env:
    gatewayUrl: ""             # "" -> helper-derived in-cluster gateway Service (http://ai-proxy-gateway:8000); override for a managed gateway
    proxyTimeoutMs: 15000      # GATEWAY_PROXY_TIMEOUT_MS (design-for-failure upstream timeout)
    maxBodyBytes: 33554432     # GW_MAX_BODY_BYTES (32 MiB DoS guard, matches the BFF default)
  resources:
    requests: { cpu: 100m, memory: 256Mi }
    limits:   { cpu: "1",  memory: 512Mi }
  probes:
    liveness:  { initialDelaySeconds: 10, periodSeconds: 10, timeoutSeconds: 3, failureThreshold: 3 }
    readiness: { initialDelaySeconds: 5,  periodSeconds: 10, timeoutSeconds: 3, failureThreshold: 3 }
    startup:   { initialDelaySeconds: 5,  periodSeconds: 5,  timeoutSeconds: 3, failureThreshold: 30 }
  pdb:
    minAvailable: 1
  automountServiceAccountToken: false  # v4 hardening: dashboard never calls the k8s API → no SA token mounted
  podSecurityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000               # v4 hardening: explicit non-root group
    fsGroup: 1000                  # v4 hardening: emptyDir volumes owned by the node group (writable under RO-rootfs)
    seccompProfile: { type: RuntimeDefault }
  containerSecurityContext:        # v2 hardening (refute-read MEDIUM): container-level, mirrors PSS-restricted
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true   # v4 hardening: immutable container FS; writable paths via emptyDir below
    runAsNonRoot: true             # v4 hardening: mirror pod-level at the container
    capabilities: { drop: ["ALL"] }
    seccompProfile: { type: RuntimeDefault }   # v4 hardening: container-level seccomp (was pod-only)
  networkPolicy:
    enabled: true                  # v2 hardening (refute-read MEDIUM): ingress only from envoy; egress only gateway+DNS

  # v4 hardening: with readOnlyRootFilesystem the Next standalone server needs two writable mounts —
  # /tmp (temp files) and /app/.next/cache (ISR/fetch + image-opt cache; NOT in the standalone copy).
  # Rendered as emptyDir volumes + volumeMounts in the Deployment (no values knob — implementation detail).

# --- INPUT: the image shape (apps/dashboard) ---
next.config.ts            adds `output: 'standalone'` (keeps the existing security headers)
apps/dashboard/Dockerfile multi-stage: builder (npm ci + next build) -> runtime copies
                          .next/standalone + .next/static; non-root USER; EXPOSE 3000;
                          ENV PORT 3000 HOSTNAME 0.0.0.0; CMD ["node","server.js"]
apps/dashboard/.dockerignore (NEW, v2): excludes .env*/.next/node_modules/test artifacts from the build context
app/api/health/route.ts   GET -> 200 (no auth/DB/SSR); `export const dynamic = "force-dynamic"`
server BFF gatewayUrl()   (v3 hardening, refute-read MEDIUM) the 6 SERVER-side resolvers
                          [app/api/gw/[...path], app/api/auth/{login,signup,me}, app/api/auth/oidc/login,
                          app/auth/oidc/callback] drop the `?? NEXT_PUBLIC_GATEWAY_URL` fallback ->
                          `GATEWAY_URL ?? "http://localhost:8080"`. A NEXT_PUBLIC_-prefixed var is INLINED
                          into the client bundle at build, so a server resolver reading it invites operators
                          to set it -> leaks the in-cluster gateway address to browsers. Server code reads
                          ONLY the non-public GATEWAY_URL (which the chart always sets). Behavior-preserving:
                          GATEWAY_URL already wins; no client code reads the var (no lib/api-client exists).

# --- OUTPUT: objects `helm template` MUST render (default values) ---
Deployment  ai-proxy-dashboard  image=<dashboard.image.repository>:<tag>; non-root podSecurityContext;
                                env GATEWAY_URL=http://ai-proxy-gateway:8000 (helper-derived when env.gatewayUrl=""),
                                  GATEWAY_PROXY_TIMEOUT_MS=<proxyTimeoutMs>, GW_MAX_BODY_BYTES=<maxBodyBytes>;
                                container port 3000; liveness+readiness+startup GET /api/health :3000; resources req+limits
                                container-level securityContext (allowPrivilegeEscalation:false, drop ALL) — v2
                                (v4) automountServiceAccountToken:false; podSecurityContext runAsGroup+fsGroup 1000;
                                container securityContext readOnlyRootFilesystem:true + runAsNonRoot + seccompProfile RuntimeDefault;
                                emptyDir volumes {tmp→/tmp, next-cache→/app/.next/cache} + matching volumeMounts
Service     ai-proxy-dashboard  type ClusterIP; port <service.port>(3000) -> named targetPort
PDB         ai-proxy-dashboard  minAvailable=<pdb.minAvailable>
NetworkPolicy ai-proxy-dashboard (v2, when networkPolicy.enabled) podSelector=dashboard; policyTypes=[Ingress,Egress];
                                ingress ONLY from envoy pods on :3000; egress ONLY to gateway pods :8000 + DNS :53
Envoy edit  envoy-configmap.yaml (when dashboard.enabled): ADD cluster dashboard_cluster (STRICT_DNS ->
                                ai-proxy-dashboard:3000) AND set the catch-all "/" route cluster=dashboard_cluster
                                (ext_authz still disabled). /v1/ (ext_authz ON), /admin/, /internal/ (403) UNCHANGED.
                                dashboard.enabled=false -> "/" stays gateway_cluster, no dashboard_cluster.

Rejections -> render/inspection-time failures:
  image_not_standalone     -> next.config lacks output:'standalone', or Dockerfile uses `next start` / root USER
  gateway_url_not_in_cluster -> rendered GATEWAY_URL is the public edge / localhost / a hardcoded literal
  probe_target_unsafe      -> a probe httpGet path != /api/health
  edge_route_split_broken  -> "/" -> gateway while dashboard.enabled, OR /v1/ loses its ext_authz check_settings
  chart_invalid            -> `helm template`/`helm lint` non-zero on defaults
  frozen_schema_violation  -> a secret value shipped, or a frozen gateway/datastore/envoy values key modified
  server_resolver_reads_public_var -> a server-side BFF gatewayUrl() still references NEXT_PUBLIC_GATEWAY_URL (v3)
  pod_not_pss_restricted   -> (v4) the dashboard pod mounts a SA token, OR the container lacks readOnlyRootFilesystem
                             without the matching emptyDir writable mounts, OR fsGroup/seccomp/runAsGroup missing

Invariants:
  - dashboard.enabled=false renders NONE of {Deployment, Service, PDB} and the Envoy "/" route reverts to gateway_cluster.
  - GATEWAY_URL is ALWAYS the in-cluster gateway Service (helper-derived default), never the public edge.
  - Every probe targets /api/health (dependency-free); the pod + image run non-root.
  - The edge route-split is the ONLY change to a sibling-frozen artifact, ADDITIVE + dashboard.enabled-gated; task-3's
    envoy auth posture (filter order, /v1/ ext_authz ON, /admin/ + /internal/) is preserved — task-3's envoy suite stays green.
  - NO secret value; only the `dashboard{}` sub-tree + the additive envoy route-split are added.
  - (v2) the dashboard pod is hardened: container-level securityContext (allowPrivilegeEscalation:false, drop ALL) AND a default-on NetworkPolicy restricting ingress to envoy + egress to gateway/DNS; networkPolicy.enabled=false renders no NetworkPolicy.
  - (v3) NO server-side BFF resolver references a NEXT_PUBLIC_-prefixed gateway var; every gatewayUrl() resolves GATEWAY_URL -> localhost default only (so the in-cluster gateway address can never be inlined into the client bundle).
  - (v4) the dashboard pod is PSS-restricted: automountServiceAccountToken:false; readOnlyRootFilesystem:true with emptyDir mounts for /tmp + /app/.next/cache (so Next can still write); runAsGroup+fsGroup 1000; seccompProfile RuntimeDefault at BOTH pod and container. No NEXT_PUBLIC_GATEWAY_URL remains in the client test harness either (stale refs removed).
```

Least-sure flag surfaced at freeze: [contract] the in-place Envoy route-split must add the dashboard path WITHOUT disturbing the frozen task-3 auth posture — a slip on the catch-all `/` could send `/v1/`/`/admin/` to the dashboard or drop an ext_authz check (unauth API path). Mitigation: tests assert the FULL route table both ways (enabled → `/`=dashboard, disabled → `/`=gateway) + `/v1/` ext_authz ON + the task-3 envoy suite still green. Secondary: [spec] `output:'standalone'`/`node server.js` correctness only proves at container start (not `helm template`) → flagged for the live e2e (kind-bootstrap / e2e-ui).

Status: FROZEN @ v4 — approved by Tin (2026-06-26). v4 = gate-driven hardening (Tin picked all four): (a) automountServiceAccountToken:false — the dashboard never calls the k8s API; (b) readOnlyRootFilesystem:true with emptyDir writable mounts for /tmp + /app/.next/cache (immutable container FS without breaking Next standalone's cache writes); (c) runAsGroup+fsGroup 1000 + container-level seccompProfile RuntimeDefault (PSS-restricted belt-and-suspenders); (d) removed the now-stale NEXT_PUBLIC_GATEWAY_URL refs from the client test harness. The RO-rootfs writable paths only fully prove at container start → kind-bootstrap must confirm the pod stays Ready (§7). v3 = gate-driven change request (Tin: "Harden NEXT_PUBLIC now"): pull the pre-existing NEXT_PUBLIC_GATEWAY_URL footgun fix IN-TASK rather than deferring it — the 6 server-side BFF gatewayUrl() resolvers drop the `?? NEXT_PUBLIC_GATEWAY_URL` fallback so the in-cluster gateway address can never be inlined into the client bundle (verified: no client code reads the var; behavior-preserving since GATEWAY_URL already wins). v2 (2026-06-26) = verify-stage hardening from the adversarial refute-read (security-expert, no HIGH): add a container-level securityContext + a default-on dashboard NetworkPolicy + a .dockerignore (all MEDIUM/LOW). v1 (2026-06-26) = edge route-split extended in-task (Tin@specify) + probe /api/health; lowest-confidence flag (route-split vs frozen auth posture) surfaced + accepted at freeze.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: every Must + every Reject ≥1 test. Helm tests parse rendered objects (behavior);
image/source tests parse the real files (Dockerfile, next.config.ts, the health route).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_standalone_image (M1): apps/dashboard/next.config.ts contains `output: 'standalone'`; Dockerfile is multi-stage, copies .next/standalone + .next/static + public, CMD `node server.js`, declares a non-root USER, EXPOSE/PORT 3000, NO `next start`.
  - test_deployment_wires_gateway (M2): default render → Deployment "ai-proxy-dashboard" image == dashboard.image repo:tag (NOT the gateway image{}); env GATEWAY_URL == http://ai-proxy-gateway:8000; GATEWAY_PROXY_TIMEOUT_MS + GW_MAX_BODY_BYTES from values.
  - test_service (M3): ClusterIP Service "ai-proxy-dashboard" port 3000 → named targetPort; selector matches the dashboard selectorLabels.
  - test_design_for_failure (M4): liveness+readiness+startup all httpGet /api/health :3000; resources req+limits; PDB renders; podSecurityContext runAsNonRoot; apps/dashboard/app/api/health/route.ts exists and returns 200 (status 200, no auth/DB import).
  - test_edge_route_split (M5): default render → envoy config has cluster dashboard_cluster (host ai-proxy-dashboard, port 3000); route "/" cluster == dashboard_cluster, ext_authz disabled; "/v1/" cluster == gateway_cluster WITH check_settings (ext_authz ON); "/admin/" gateway; "/internal/" 403.
  - test_external_ready_frozen (M6): dashboard.enabled=false → no dashboard Deployment/Service/PDB; envoy "/" route cluster == gateway_cluster; no dashboard_cluster; gateway+datastore objects + frozen keys unchanged.
  - test_image_not_standalone (reject): next.config WITHOUT output:'standalone' OR a `next start`/root Dockerfile is caught by the M1 assertions (guard test documents the failure shape).
  - test_gateway_url_in_cluster (reject): rendered GATEWAY_URL is never the public edge / localhost / a literal; an env.gatewayUrl override still renders (operator-set managed gateway) but the DEFAULT is the in-cluster Service.
  - test_probe_target_safe (reject): no probe path is /app or "/"; all == /api/health.
  - test_edge_split_authz_intact (reject): /v1/ ALWAYS keeps check_settings (ext_authz ON) regardless of dashboard.enabled; "/" never routes to gateway when dashboard.enabled.
  - test_chart_valid (chart_invalid): default `helm template` exits 0 + `helm lint` 0.
  - test_no_secret_no_frozen_touch (frozen_schema_violation): no BEGIN CERTIFICATE/PRIVATE KEY or password literal in the dashboard render; the task-3 envoy auth posture preserved — re-parse /v1/ ext_authz ON AND (v2 strengthening) /internal/ 403 + /admin/ ext_authz disabled.
  - test_pod_hardened (M4 hardening v2): dashboard container securityContext allowPrivilegeEscalation:false + drop [ALL]; a NetworkPolicy "ai-proxy-dashboard" with policyTypes [Ingress,Egress], ingress from envoy selector on :3000, egress to gateway selector :8000 + DNS :53; networkPolicy.enabled=false → no NetworkPolicy.
  - test_bff_no_public_gateway_var (server_resolver_reads_public_var, v3): each of the 6 server-side BFF route files [app/api/gw/[...path]/route.ts, app/api/auth/login/route.ts, app/api/auth/signup/route.ts, app/api/auth/me/route.ts, app/api/auth/oidc/login/route.ts, app/auth/oidc/callback/route.ts] contains NO "NEXT_PUBLIC_GATEWAY_URL" (anywhere, incl. comments) AND still references "GATEWAY_URL" + the localhost default (resolver still works server-side).
  - test_pod_pss_restricted (pod_not_pss_restricted, v4): default render → pod spec automountServiceAccountToken == false; podSecurityContext runAsGroup==1000, fsGroup==1000, seccompProfile.type==RuntimeDefault; container securityContext readOnlyRootFilesystem==true, runAsNonRoot==true, seccompProfile.type==RuntimeDefault; two emptyDir volumes with matching volumeMounts at /tmp and /app/.next/cache (writable under RO-rootfs).
</test_plan>

Tests live in: `tests/helm/test_dashboard_chart.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/next.config.ts` (+ output:'standalone') · `apps/dashboard/Dockerfile` (NEW) · `apps/dashboard/app/api/health/route.ts` (NEW) · `charts/ai-proxy/values.yaml` (fill `dashboard{}` ONLY) · `charts/ai-proxy/templates/_helpers.tpl` (ADD dashboard helpers; frozen helpers untouched) · `charts/ai-proxy/templates/dashboard-deployment.yaml` (NEW) · `charts/ai-proxy/templates/dashboard-service.yaml` (NEW) · `charts/ai-proxy/templates/dashboard-pdb.yaml` (NEW) · `charts/ai-proxy/templates/dashboard-networkpolicy.yaml` (NEW, v2) · `apps/dashboard/.dockerignore` (NEW, v2) · `charts/ai-proxy/templates/envoy-configmap.yaml` (ADDITIVE route-split: dashboard_cluster + `/`-route, dashboard.enabled-gated) · (v3) the 6 server-side BFF resolvers `apps/dashboard/app/api/gw/[...path]/route.ts` · `apps/dashboard/app/api/auth/login/route.ts` · `apps/dashboard/app/api/auth/signup/route.ts` · `apps/dashboard/app/api/auth/me/route.ts` · `apps/dashboard/app/api/auth/oidc/login/route.ts` · `apps/dashboard/app/auth/oidc/callback/route.ts` (drop the NEXT_PUBLIC_GATEWAY_URL fallback ONLY) · (v4) test-harness cleanup `apps/dashboard/tests/setup.ts` · `apps/dashboard/tests-bff/setup.ts` · `apps/dashboard/tests/mocks/handlers.ts` · `apps/dashboard/tests/harness.smoke.test.ts` (remove the now-stale NEXT_PUBLIC_GATEWAY_URL refs)
Strategy (ordered batches): 1. app: next.config output:'standalone' + app/api/health/route.ts + Dockerfile. 2. values: fill `dashboard{}` sub-schema. 3. helpers: ai-proxy.dashboard.fullname/selectorLabels. 4. dashboard-deployment.yaml (image, GATEWAY_URL helper-derived, timeout/body-cap env, /api/health probes, resources, non-root). 5. dashboard-service.yaml + dashboard-pdb.yaml. 6. envoy-configmap.yaml route-split (add dashboard_cluster + flip `/` route, gated). Re-run tests/helm after each batch.
Safety rule (feature-specific): the envoy route-split is ADDITIVE + dashboard.enabled-gated — /v1/ keeps ext_authz ON, /admin/ + /internal/ unchanged, the task-3 envoy suite MUST stay green; touch ONLY the dashboard sub-tree + the gated envoy `/`-route + dashboard_cluster; no secret literal; no other frozen key.
Code lives in: `apps/dashboard/` + `charts/ai-proxy/`
Constraints: do NOT change any test or the contract; allow-list only (pure Helm/YAML + a Dockerfile + a tiny Next route — no new npm/py dep); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `tests/helm/` 60 passed (16 scaffold + 14 datastore + 16 envoy + 14 dashboard incl. the v3 BFF guard + v4 PSS-restricted check); task-3 envoy suite stays green. The dashboard's FULL vitest suite re-ran 688 passed (the v4 test-harness cleanup — removing the stale NEXT_PUBLIC_GATEWAY_URL refs — touched the client setup, so the whole suite was re-run) + tsc clean + eslint exit 0.
- [x] coverage did not decrease — additive Helm templates + a Dockerfile + a tiny Next route + a one-line-per-file resolver edit + tests; no behavioral path removed (dashboard tsc + eslint clean).
- [x] no test or contract was altered during build — §3 re-FROZEN @ v3 (gate-driven CR "Harden NEXT_PUBLIC now"); the new test_bff_no_public_gateway_var was authored in the `tests` phase (ran RED first), not build.
- [x] the green was EARNED — adversarial refute-read (security-expert) run: VERDICT partial, NO HIGH (could not refute the /v1/ auth path, secret handling, non-root, or the disabled-revert). v2 closed 3 MEDIUM + 2 LOW (container securityContext, dashboard NetworkPolicy, .dockerignore, strengthened frozen-posture test). v3 (Tin's call) pulled in the deferred NEXT_PUBLIC_GATEWAY_URL footgun (all 6 server resolvers → non-public GATEWAY_URL only). v4 (Tin picked all four) takes the pod to PSS-restricted: automountServiceAccountToken:false + readOnlyRootFilesystem (with emptyDir for /tmp + /app/.next/cache) + runAsGroup/fsGroup 1000 + container-level seccomp, and removed the stale NEXT_PUBLIC refs from the client test harness. Each change went RED-first (test_pod_pss_restricted) then GREEN; render independently confirmed via Python (container readOnlyRootFilesystem:true, both emptyDir mounts present).
- [x] concurrency / timing — none material (declarative manifests); the only runtime ordering (BFF→gateway) is a normal request path with the values-driven timeout (GATEWAY_PROXY_TIMEOUT_MS) as the design-for-failure guard.
- [x] no exposed secrets, injection openings, or unexpected dependencies — NO secret shipped (the dashboard needs none; BFF forwards the browser cookie, GATEWAY_URL is a non-secret in-cluster URL); .dockerignore keeps .env out of the build context; no new chart/npm/py dependency. Dashboard pod hardened: non-root + container securityContext (drop ALL, no-priv-esc) + a default-on NetworkPolicy (ingress only from envoy, egress only gateway+DNS).
- [x] layering & dependencies follow CONVENTIONS.md — extends `dashboard{}` only; reuses frozen gateway/envoy helpers; the ONLY sibling-frozen edit is the additive, dashboard.enabled-gated envoy route-split.
- [ ] a person reviewed and approved the change — security-adjacent gate (edge route-split + pod hardening); escalating to Tin (refute-read findings = HARD-STOP, never auto-PASS).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `helm template` renders dashboard Deployment+Service+PDB (image = dashboard.image repo:tag, distinct from the gateway image) — confirmed by render / test_deployment_wires_gateway + test_service.
- [x] the rendered dashboard Deployment env has GATEWAY_URL=http://ai-proxy-gateway:8000 (in-cluster, helper-derived), plus GATEWAY_PROXY_TIMEOUT_MS + GW_MAX_BODY_BYTES from values — confirmed by parsed env / test_deployment_wires_gateway + test_gateway_url_in_cluster.
- [x] all three probes (liveness/readiness/startup) GET /api/health on :3000; pod runs non-root; resources req+limits; PDB renders — confirmed by test_design_for_failure + test_probe_target_safe.
- [x] apps/dashboard/next.config.ts sets output:'standalone' and the NEW Dockerfile is multi-stage non-root running `node server.js` on 3000 (no `next start`) — confirmed by file parse / test_standalone_image; apps/dashboard/app/api/health/route.ts returns 200.
- [x] the envoy config (dashboard.enabled) adds dashboard_cluster (→ai-proxy-dashboard:3000) and routes `/`→dashboard_cluster (ext_authz off) while `/v1/` keeps ext_authz ON, `/admin/`→gateway, `/internal/`→403 — confirmed by parsed envoy config / test_edge_route_split + test_edge_split_authz_intact.
- [x] dashboard.enabled=false removes the dashboard objects AND reverts envoy `/`→gateway_cluster (no dashboard_cluster); gateway+datastores+frozen keys unchanged; helm lint 0; full tests/helm green incl. the task-3 envoy suite — confirmed by test_external_ready_frozen + test_no_secret_no_frozen_touch + `helm lint` + run the dir.
- [x] no secret value anywhere in the dashboard render; only the dashboard{} sub-tree + the additive gated envoy route-split changed — confirmed by test_no_secret_no_frozen_touch + git diff review.
- [x] (v3) every server-side BFF gatewayUrl() resolver reads ONLY GATEWAY_URL → localhost default, never NEXT_PUBLIC_GATEWAY_URL — so the in-cluster gateway address can never be inlined into the client bundle — confirmed by test_bff_no_public_gateway_var (all 6 routes) + grep shows zero remaining production references.
- [x] (v4) the dashboard pod renders PSS-restricted — automountServiceAccountToken:false; pod runAsGroup+fsGroup 1000 + seccompProfile RuntimeDefault; container readOnlyRootFilesystem:true + runAsNonRoot + seccompProfile, with emptyDir writable mounts at /tmp + /app/.next/cache so Next standalone can still write — confirmed by test_pod_pss_restricted + a direct Python render. RUNTIME caveat: RO-rootfs write-paths only fully prove at container start → kind-bootstrap must confirm the pod stays Ready (§7 delta).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new helper/value is referenced: `ai-proxy.dashboard.fullname` → used by dashboard-deployment/service/pdb/networkpolicy + envoy-configmap dashboard_cluster host; `ai-proxy.dashboard.selectorLabels` → deployment selector + service selector + networkpolicy podSelector; `ai-proxy.dashboard.gatewayUrl` → deployment GATEWAY_URL env. Every `dashboard.*` value is consumed by a template. The new `/api/health` route is the target of all 3 probes; next.config `output:'standalone'` is consumed by the Dockerfile runtime stage; `.dockerignore` is consumed by `docker build`.
- [x] DEAD-CODE (code) — no orphan: each new template guards on `dashboard.enabled`; the envoy route-split + dashboard_cluster are gated by the same flag (enabled=false reverts both, asserted by test_external_ready_frozen). No unused helper, value, or env var introduced.
- [x] SEMANTIC (prose / non-code) — read in full: re-read all 6 BFF resolvers — now `GATEWAY_URL ?? "http://localhost:8080"` only (the NEXT_PUBLIC fallback removed); searched the whole `apps/dashboard` tree to confirm NO other production code reads NEXT_PUBLIC_GATEWAY_URL (no `lib/api-client` exists — the tests/setup.ts comment naming it is stale; the browser reaches the gateway only via same-origin BFF) → the fix is complete, not partial; re-read the rendered envoy config to confirm `/v1/` ext_authz stays ON and `/internal/`→403 with dashboard enabled (test_edge_split_authz_intact); confirmed NO secret value in any rendered dashboard object.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin · date: 2026-06-26
Notes: Security-adjacent gate (edge route-split + BFF resolver + PSS-restricted pod) — HELD as HARD-STOP, never auto-PASSed; Tin signed off after driving 3 gate-driven hardening rounds (v2 refute-read fixes → v3 NEXT_PUBLIC footgun → v4 PSS-restricted). Contract FROZEN @ v4. One runtime obligation carried to kind-bootstrap (§7): confirm the pod stays Ready under readOnlyRootFilesystem.

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · dropped] Remove the `NEXT_PUBLIC_GATEWAY_URL` fallback from the BFF gateway resolver — RESOLVED IN-TASK (contract v3, Tin: "Harden NEXT_PUBLIC now"). All 6 server-side resolvers now read only the non-public `GATEWAY_URL`; guarded by test_bff_no_public_gateway_var. No follow-up task needed (evidence: refute-read MEDIUM → fixed + verified complete).
- [SPEC · open] Verify the standalone image at runtime — `output:'standalone'` + `node server.js` correctness (all traced deps present, server binds :3000, /api/health 200) AND that the v4 readOnlyRootFilesystem + emptyDir mounts (/tmp, /app/.next/cache) let Next write at runtime (pod stays Ready, no EROFS crash) only prove at container start, not at `helm template`/tsc. Cover in kind-bootstrap (image builds + pod Ready) + e2e-ui (browser loads through the edge) (evidence: §1 ⚠ assumption + v4 RO-rootfs).
- [SPEC · dropped] readOnlyRootFilesystem for the dashboard container with emptyDir for Next's writable paths (.next/cache, /tmp) — RESOLVED IN-TASK (contract v4, Tin picked it). Rendered + guarded by test_pod_pss_restricted; runtime write-path validation folded into the kind-bootstrap delta above (evidence: refute-read suggested-fix → implemented).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [TDD · folded] Helm renders large YAML ints in scientific notation (`33554432` → `3.3554432e+07`); a test caught it (test_deployment_wires_gateway) → fix is `| int` in the template before `| quote`. A render assertion on the exact string value is the guard (evidence: build batch-2 failure). [folded foundation-version 39]
- [ADD · folded] An explanatory CODE COMMENT can fail a substring-based source test (the Dockerfile comment "never `next start`" tripped `"next start" not in df`; the health-route comment "no cookie" tripped `"cookie" not in route`). Source-scan tests must target real constructs, or the code must avoid the forbidden token even in prose (evidence: build batch-1 false-positives). [folded foundation-version 39]
