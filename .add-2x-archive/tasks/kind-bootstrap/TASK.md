# TASK: kind bootstrap harness: build+load both images, helm install, whole-stack Ready + LLM upstream stub

slug: kind-bootstrap · created: 2026-06-27 · stage: production
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

Touches (files · symbols · signatures): a NET-NEW reproducible kind harness that builds BOTH images, loads them, installs the chart with a kind overlay + an in-cluster LLM upstream stub, and waits the whole stack Ready. Grounded in the real chart + the existing edge harness it supersedes —
  - **NEW `Makefile` targets `kind-up`/`kind-down`/`kind-load`/`kind-smoke`** (root `Makefile:1` has `GATEWAY`/`DASHBOARD` vars + the analog `edge`/`edge-up`/`edge-down`/`edge-smoke` block at `:64-103` driving `infra/docker-compose.e2e.yml` via `docker compose … --wait`). kind replaces compose: `docker build` both Dockerfiles → `kind load docker-image` → `helm install` → `kubectl wait`. `.PHONY` line `:17` must gain the new targets.
  - **NEW kind cluster config** (none exists; `kind get clusters` = none) — a `infra/kind/cluster.yaml` (`kind: Cluster`, `apiVersion: kind.x-k8s.io/v1alpha4`) with an `extraPortMappings` for the Envoy edge NodePort so the host e2e (tasks 7–9) can reach `https://127.0.0.1:<port>`.
  - **NEW kind values overlay `charts/ai-proxy/values-kind.yaml`** — overrides the frozen `values.yaml` schema for local images + test secrets: `image.repository`+`image.tag`+`image.pullPolicy:Never` (built tag), `dashboard.image.*`+`pullPolicy:Never` (`values.yaml:7-10` gateway image `ghcr.io/pilotspace/ai-proxy-gateway:0.4.0`; `:225-228` dashboard `ghcr.io/pilotspace/ai-proxy-dashboard:0.4.0`), `gateway.jwtSecret.createSecret:true` (`:62-65` default false→references `ai-proxy-gateway-secrets` key `jwt-secret`), `datastores.secrets.create:true`+creds (`:118-120` default false→references `ai-proxy-datastore-secrets`), `gateway.env.databaseUrlSecretRef.name:ai-proxy-datastore-secrets`+key (`:35-37`), `envoy.tls.existingSecret` (`:191` `ai-proxy-edge-tls` kubernetes.io/tls — harness mints a self-signed cert), and `gateway.upstreamBaseUrls.{openrouter,openai,anthropic,google}` (`:87-91`, real provider URLs by default) → the in-cluster stub Service URL.
  - **NEW in-cluster LLM upstream stub** (none exists — `infra/docker-compose.e2e.yml:43-65` runs the gateway OFFLINE-SAFE with an empty `GATEWAY_OPENROUTER_API_KEY` and NO stub; live smoke used a real key). The stub is a tiny HTTP server answering the OpenAI-wire chat-completions shape (so a real proxied completion succeeds with NO provider keys). DESIGN FORK for §1/§3 (surface to Tin): (A) a separate manifest `kubectl apply`-ed by the harness (keeps the prod chart stub-free) vs (B) a `gateway.upstreamStub.enabled`-gated chart template (one install, but a test concern in the prod chart). Lean (A).
  - `apps/gateway/Dockerfile` (builds the gateway image — task 5 added the migrations/ + alembic.ini COPY so the initContainer can migrate) · `apps/dashboard/Dockerfile` + `.dockerignore` + `next.config.ts` (task 4, `output:'standalone'` → `node server.js`). The harness `docker build`s both; `kind load docker-image` injects them (no registry push).
  - The whole chart (23 templates under `charts/ai-proxy/templates/`): gateway Deployment (initContainers wait-for-db→migrate, task 5) · dashboard · envoy (initContainer JWKS subst, TLS) · postgres/redis/minio StatefulSets+Services (task 2) · `minio-createbucket-job.yaml` (post-install hook) · the create-gated `datastore-secret.yaml`/`gateway-secret.yaml`. `kubectl wait --for=condition=Ready` across all of them is the "whole stack Ready" proof.
Context (working folder): `.add/milestones/v53/MILESTONE.md` task line 34 (kind harness: build BOTH images → `kind load` → `helm install` → wait-ready + LLM upstream stub; reproducible, zero cloud creds) + exit criterion line 46 (`make kind-up` reports the whole stack Ready, no cloud credentials) + the CLOUD-READY/KIND-VALIDATED + SECRETS-NEVER-IN-CHART shared decisions (`:18`,`:20`). Analog scripts: `scripts/edge_smoke.sh` + `scripts/e2e_edge.sh` (auth+completion through the edge — patterns the later e2e tasks reuse; NOT this task) · `scripts/gen_dev_certs.sh` (self-signed cert gen — the TLS-Secret source for kind). `tests/helm/` (the 72-test render suite — kind-bootstrap adds a harness/manifest-shape test, not a new render path). `infra/docker-compose.e2e.yml` = the superseded compose analog (postgres:16-alpine, redis:7-alpine, gateway build, envoy v1.29 with the base64url JWKS entrypoint).
Honors (patterns / conventions): CLOUD-READY, KIND-VALIDATED (the PROOF is a local kind cluster; zero cloud creds — the HARD-STOP boundary) · SECRETS-NEVER-IN-CHART (kind test creds live in the harness/overlay, never committed as real secrets; the chart still fail-fasts on unset) · DESIGN-FOR-FAILURE (bounded `kubectl wait` with a timeout + a diagnostic dump on failure, idempotent up/down, no infinite loop — CLAUDE.md IO-failure rule) · MIGRATIONS-BEFORE-BOOT (kind is where the task-5 in-image `alembic upgrade head` initContainer actually RUNS at boot — discharges that runtime obligation) · E2E THROUGH THE EDGE (the kind Envoy edge is NodePort-exposed so the later host e2e drives ext_authz + WS + TLS).
Anchors the contract cites: NEW root `Makefile` targets `kind-up`/`kind-down` (+ helpers) · NEW `infra/kind/cluster.yaml` · NEW `charts/ai-proxy/values-kind.yaml` (the locked override of the frozen values schema) · NEW the in-cluster LLM upstream stub manifest (`infra/kind/upstream-stub.yaml`, option A) + its image/script · the reused `apps/gateway/Dockerfile` + `apps/dashboard/Dockerfile` + the whole `charts/ai-proxy/` chart + `scripts/gen_dev_certs.sh`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A reproducible, zero-cloud-creds **kind bootstrap harness** — `make kind-up` creates a local kind cluster, builds BOTH images (gateway + dashboard), `kind load`s them, applies a TEST-ONLY in-cluster LLM upstream stub, `helm upgrade --install`s the chart with a `values-kind.yaml` overlay, and reports the WHOLE stack Ready; `make kind-down` tears it down idempotently. This is the milestone's PROOF surface — it actually RUNS the chart that tasks 1–5 only rendered, discharging the task-4 (dashboard Ready under read-only rootfs) + task-5 (in-image `alembic upgrade head` initContainer runs at boot) runtime obligations.
Framings weighed: a **kind cluster + Makefile harness + `values-kind.yaml` overlay + a separate harness-applied stub manifest** (chosen — Tin 2026-06-27; the prod chart stays 100% test-free, the stub never ships to a real cluster, mirrors the existing `make edge`/compose analog) · a chart-gated `upstreamStub.enabled` template (rejected — puts a test concern in the prod chart even when off) · minikube/k3d (rejected — kind is already installed + is the CI-standard, matches the milestone wording) · a pure-script harness with no Makefile target (rejected — the milestone exit criterion names `make kind-up`).
Must:
<must>
  - M1 — `make kind-up` is REPRODUCIBLE from a clean machine with NO cloud credentials and NO registry push: ensure a named kind cluster (from `infra/kind/cluster.yaml`), `docker build` both `apps/gateway/Dockerfile` + `apps/dashboard/Dockerfile`, `kind load docker-image` both, `kubectl apply` the upstream-stub manifest, then `helm upgrade --install` with `charts/ai-proxy/values-kind.yaml`.
  - M2 — the kind overlay `charts/ai-proxy/values-kind.yaml` overrides ONLY environment-specific inputs of the FROZEN values schema (local `image`/`dashboard.image` refs + `pullPolicy: Never`, `datastores.secrets.create:true` + obviously-fake test creds, `gateway.jwtSecret.createSecret:true`, the `databaseUrlSecretRef` wiring, a self-signed `envoy.tls.existingSecret`, `gateway.upstreamBaseUrls.*` → the stub Service) — it renders with ZERO template edits (external-ready preserved: a managed-endpoint swap is the same overlay shape).
  - M3 — "whole stack Ready" is OBSERVABLE and ENFORCED: every Deployment (gateway · dashboard · envoy · upstream-stub) and StatefulSet (postgres · redis · minio) reaches Ready and the post-install bucket Job completes — proven by a BOUNDED `kubectl rollout status`/`wait` that returns green; the target also exposes the Envoy edge to the host (NodePort via `extraPortMappings`) so tasks 7–9 can drive `https://127.0.0.1:<port>`.
  - M4 — MIGRATIONS-BEFORE-BOOT actually executes: the gateway pod's task-5 initContainers (`wait-for-db` → `alembic upgrade head`) complete and the gateway container becomes Ready against a MIGRATED DB; the dashboard pod becomes Ready under `readOnlyRootFilesystem` (no EROFS) — both observable as the pods going Ready.
  - M5 — DESIGN-FOR-FAILURE (CLAUDE.md IO rule): every wait is BOUNDED by an explicit timeout; on timeout the harness DUMPS diagnostics (`kubectl get pods`, recent events, failing-pod logs/describe) and exits non-zero — never hangs, never reports false-green. `kind-up` is IDEMPOTENT (re-runnable → converges via `helm upgrade --install` + cluster reuse); `kind-down` removes the cluster idempotently (absent cluster = success).
  - M6 — the LLM upstream stub answers the OpenAI chat-completions wire shape (non-streaming JSON + streaming SSE `[DONE]` + a `usage` block) so a real proxied completion later succeeds with ZERO provider keys; it carries its own readiness probe and runs non-root.
  - M7 — SECRETS-NEVER-IN-CHART preserved: the kind test creds are obviously-fake literals confined to the overlay/harness (never a real secret value), and the chart still FAIL-FASTS if a required secret ref is unset (the kind overlay supplies them, it does not weaken the guard).
</must>
Reject:
<reject>
  - required tooling absent (docker daemon down, or kind/kubectl/helm not on PATH) -> harness PREFLIGHT fails fast with a named message + non-zero exit -> "kind_tooling_missing"
  - the stack does not reach Ready within the bounded timeout -> diagnostics dumped, non-zero exit, NO false-green, NO hang -> "kind_stack_not_ready"
  - a real managed-cloud apply is requested -> NOT attempted here (the HARD-STOP boundary); the harness only ever targets the local kind context -> "cloud_apply_out_of_scope"
</reject>
After:
<after>
  - the local kind context holds the full stack Ready; the Envoy edge is reachable from the host at `https://127.0.0.1:<port>`; the gateway is serving a MIGRATED DB; the dashboard is Ready under a read-only rootfs.
  - re-running `make kind-up` converges (no error, no duplicate cluster); `make kind-down` leaves no cluster; both are zero-cloud-creds.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The locally-built images actually RUN under the chart's PSS-restricted securityContexts on a real kubelet (dashboard `readOnlyRootFilesystem` + the gateway/dashboard `runAsNonRoot`/uid 1000/drop ALL) — lowest confidence because the 72 render tests prove SHAPE, not RUNTIME: only a live pod reveals an EROFS on a missed Next write-path or a uid/ownership mismatch. If wrong: pods CrashLoopBackOff and kind-up never goes green → Dockerfile/securityContext/emptyDir rework (this is exactly the task-4/5 runtime obligation this task discharges).
  - [ ] the task-5 in-image alembic finds `migrations/` + `alembic.ini` at `/app` and reaches head at boot (only proven once the initContainer runs) — confirm via the gateway pod going Ready.
  - [ ] the envoy initContainer base64url JWKS substitution (the v3 shell fix) works at runtime — confirm via envoy Ready + an authenticated request through the edge.
  - [ ] kind's default CNI (kindnet) IGNORES NetworkPolicy, so the NP-based isolation from tasks 2–4 is NOT validated here — accepted (a cloud-apply-runbook concern; note it, do not block).
  - [ ] a passwordless in-cluster Redis (no `redisUrlSecretRef`) is acceptable for the kind proof — confirm the gateway connects with the literal `gateway.env.redisUrl`.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: kind-up brings the whole stack Ready from clean (M1, M3)
  Given a machine with docker+kind+kubectl+helm and NO kind cluster and NO cloud creds
  When the operator runs `make kind-up`
  Then a named kind cluster exists, both images are built and `kind load`ed, the upstream-stub
       manifest is applied, the chart is `helm upgrade --install`ed with values-kind.yaml,
       and every Deployment (gateway·dashboard·envoy·upstream-stub) + StatefulSet (postgres·redis·
       minio) reports Ready and the bucket Job Completed
  And no image was pulled from a remote registry and no cloud credential was used

Scenario: the kind overlay overrides only env-specific inputs, no template edit (M2)
  Given the FROZEN values.yaml schema and charts/ai-proxy/values-kind.yaml
  When `helm template ai-proxy charts/ai-proxy -f charts/ai-proxy/values-kind.yaml` renders
  Then the gateway+dashboard images carry the local tag with pullPolicy:Never, the datastore+jwt
       Secrets are created with fake test creds, and gateway.upstreamBaseUrls.* point at the
       in-cluster stub Service
  And no file under charts/ai-proxy/templates/ was modified to achieve it

Scenario: migrate-before-boot actually runs and the dashboard survives read-only rootfs (M4)
  Given kind-up has installed the chart
  When the gateway pod starts
  Then its initContainers wait-for-db then `alembic upgrade head` complete, the gateway container
       becomes Ready against a migrated DB, AND the dashboard pod becomes Ready under
       readOnlyRootFilesystem with no EROFS CrashLoop
  And the gateway never reports Ready before the migration initContainer succeeded

Scenario: the edge is reachable from the host (M3)
  Given the stack is Ready
  When the host opens `https://127.0.0.1:<mapped-port>/` through the Envoy NodePort
  Then the Envoy edge answers (TLS terminated; routes browser→dashboard, API→gateway)
  And the gateway is never exposed to the host directly (only via the edge)

Scenario: the upstream stub answers the OpenAI wire shape with zero provider keys (M6)
  Given the upstream-stub Deployment is Ready and gateway.upstreamBaseUrls.* point at it
  When a chat-completions request reaches the stub (non-streaming and streaming)
  Then it returns a valid OpenAI-shaped completion incl. a usage block, and the SSE path ends [DONE]
  And no real provider API key was configured anywhere

Scenario: kind-up is idempotent and kind-down is clean (M5)
  Given a cluster already up from a prior `make kind-up`
  When the operator runs `make kind-up` again, then `make kind-down`, then `make kind-down` again
  Then the second up converges with no error/no duplicate cluster, the first down removes the
       cluster, and the second down succeeds on an already-absent cluster
  And no orphaned cluster or context remains

Scenario: missing tooling fails fast (Reject kind_tooling_missing)
  Given the docker daemon is down OR kind/kubectl/helm is not on PATH
  When the operator runs `make kind-up`
  Then the harness preflight prints a clear named message and exits non-zero BEFORE building images
  And no kind cluster is created and no partial state is left

Scenario: a stuck stack fails loudly, never false-green (Reject kind_stack_not_ready)
  Given a pod cannot become Ready within the bounded wait timeout (e.g. an image won't run)
  When `make kind-up` reaches the wait step
  Then the harness dumps diagnostics (get pods, events, failing-pod describe/logs) and exits non-zero
  And it NEVER hangs indefinitely and NEVER reports the stack Ready

Scenario: real cloud apply is out of scope (Reject cloud_apply_out_of_scope)
  Given no cloud credentials are present (the HARD-STOP boundary)
  When the harness runs
  Then it only ever targets the local kind context and never attempts a managed-cloud apply
  And the cloud apply remains a documented runbook (ci-e2e-pipeline, task 10), not executed here
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is an INFRA/HARNESS contract: the frozen shape is the operator-facing commands + the file
artifacts + the observable "stack Ready" definition (no HTTP API surface).

```
# === Makefile targets (root Makefile; added to .PHONY) ===
make kind-up        # preflight tools → ensure cluster → build+load both images → apply stub →
                    #   helm upgrade --install -f values-kind.yaml → bounded wait Ready. Idempotent.
                    #   exit 0 iff whole stack Ready; exit≠0 + diagnostics on timeout/preflight-fail.
make kind-down      # delete the kind cluster; success even if already absent (idempotent).
make kind-load      # (helper) docker build + kind load both images into the cluster.
make kind-smoke     # (helper) curl the edge NodePort /health through TLS; exit≠0 if unreachable.
KIND_CLUSTER ?= ai-proxy        # overridable cluster name
KIND_EDGE_PORT ?= 8443          # host port mapped to the Envoy edge NodePort
KIND_WAIT_TIMEOUT ?= 300s       # bound for every rollout/wait

# === infra/kind/cluster.yaml ===
kind: Cluster ; apiVersion: kind.x-k8s.io/v1alpha4
nodes: [ { role: control-plane, extraPortMappings: [ { containerPort: <edge-nodePort>,
          hostPort: ${KIND_EDGE_PORT}, protocol: TCP } ] } ]

# === charts/ai-proxy/values-kind.yaml (overlay; overrides ONLY env-specific inputs of the frozen schema) ===
image:            { repository: ai-proxy-gateway,   tag: kind-local, pullPolicy: Never }
dashboard.image: { repository: ai-proxy-dashboard, tag: kind-local, pullPolicy: Never }
gateway.jwtSecret.createSecret: true                 # + jwtSecret value (fake test literal)
gateway.env.databaseUrlSecretRef: { name: ai-proxy-datastore-secrets, key: url }
datastores.secrets: { create: true, <fake test creds: pgPassword/minioRootUser/minioRootPassword> }
gateway.upstreamBaseUrls.{openrouter,openai,anthropic,google}: http://ai-proxy-upstream-stub:8080/v1
envoy.tls.existingSecret: ai-proxy-edge-tls          # harness mints a self-signed kubernetes.io/tls Secret
# NO charts/ai-proxy/templates/* edit — overlay only.

# === infra/kind/upstream-stub.yaml (harness-applied; NOT in the Helm chart) ===
Deployment ai-proxy-upstream-stub  (non-root, readinessProbe) +
Service     ai-proxy-upstream-stub:8080
behavior: POST /v1/chat/completions → 200 OpenAI-shaped { id, object, choices[].message,
          usage{prompt_tokens,completion_tokens,total_tokens} } ; stream=true → SSE chunks + [DONE].

# === "WHOLE STACK READY" (the gate-able definition) ===
Ready ⟺ rollout/condition=Ready on Deployments {gateway, dashboard, envoy, upstream-stub}
        AND StatefulSets {postgres, redis, minio} AND the minio bucket Job Completed —
        each awaited with `--timeout ${KIND_WAIT_TIMEOUT}`; on breach → dump diag, exit≠0.

# === Failure contract (design-for-failure) ===
kind_tooling_missing       -> preflight prints the missing tool, exits≠0 BEFORE any build/cluster op
kind_stack_not_ready       -> on wait-timeout: `kubectl get pods` + events + failing-pod describe/logs,
                              exit≠0 ; NEVER hangs, NEVER reports false-green
cloud_apply_out_of_scope   -> harness targets ONLY the local kind context; never a managed-cloud apply
```
Schema: no DB/table changes. Files: NEW `infra/kind/cluster.yaml` · `infra/kind/upstream-stub.yaml` ·
`infra/kind/edge-nodeport.yaml` · `charts/ai-proxy/values-kind.yaml`; EDIT root `Makefile` (+targets, +.PHONY).
The stub server script is embedded in upstream-stub.yaml's ConfigMap (self-contained `kubectl apply -f`).
The reused gateway+dashboard Dockerfiles are UNCHANGED.

# === v2 CHANGE REQUEST (Tin 2026-06-27): one minimal chart-template fix the live kind run forced ===
# The live `make kind-up` proved the task-3 envoy ConfigMap is BROKEN in real Kubernetes: its
# STRICT_DNS clusters use SHORT service names (ai-proxy-gateway, ai-proxy-dashboard). Envoy's
# c-ares resolver does NOT apply the resolv.conf `search` domains glibc uses, so those names
# never resolve → clusters never initialize → envoy hangs in INITIALIZING → NO listener binds
# (incl. :9902 health) → liveness fails → CrashLoopBackOff. Render tests + the task-3 refute-read
# could not catch this (valid YAML; only a live resolver reveals it). Proven fix (envoy reached
# LIVE in-cluster once applied): FQDN the two cluster load_assignment addresses —
#   charts/ai-proxy/templates/envoy-configmap.yaml:222  ai-proxy.gateway.fullname   -> + .{{ .Release.Namespace }}.svc.cluster.local
#   charts/ai-proxy/templates/envoy-configmap.yaml:237  ai-proxy.dashboard.fullname -> + .{{ .Release.Namespace }}.svc.cluster.local
# This is STRICTLY MORE CORRECT (the same bug breaks a real cloud deploy) — a production fix, not
# a kind hack. The ext_authz uri (:89) needs NO change (it connects via the named gateway_cluster).
# CONTRACT DELTA: this task MAY edit `charts/ai-proxy/templates/envoy-configmap.yaml` for this fix
# ONLY (FQDN cluster addresses). A tests/kind regression asserts the envoy cluster addresses are
# FQDNs ending `.svc.cluster.local`. No other chart template changes.

# === v3 CHANGE REQUEST (orchestrator-amended 2026-06-27; Tin ratifies at the verify gate — HARD-STOP, edge) ===
# The FQDN fix (v2) is necessary but NOT sufficient on a COLD kind cluster: envoy still CrashLoopBackOff'd
# from a fresh bootstrap. Root cause, proven by reading the chart against the live pods: the envoy
# Deployment renders ONLY readinessProbe + livenessProbe — NO startupProbe. Both sibling workloads HAVE
# one (gateway startupProbe /health, dashboard /api/health; envoy <none> — confirmed live) AND define it
# in their values.yaml probes blocks; the ENVOY probes block (values.yaml envoy.probes, readiness+liveness
# only) is MISSING the `startup` key entirely — so envoy is the odd one out at BOTH the values and the
# template layer. Envoy's health listener (:9902) binds only AFTER init completes, and STRICT_DNS cluster
# warming is part of init; on a cold cluster (CoreDNS still coming up) that warming window exceeds the
# liveness budget (initialDelay 15s + 3×15s ≈ 45s), so the kubelet kills envoy via the liveness probe
# BEFORE it can converge → permanent CrashLoopBackOff. The startupProbe is the k8s-designed mechanism that
# SUPPRESSES liveness until the first /ready 200 (or the startup budget) — exactly this cold-start case.
# Render tests + the task-3 refute-read could not catch this (valid YAML; only a live cold kubelet reveals
# the kill race). This is a CHART BUG at two layers (values miss the key; the template never wired it),
# STRICTLY MORE CORRECT for any real cluster — not a kind hack.
# Proven fix (TWO files, mirroring gateway/dashboard exactly): (1) ADD an `envoy.probes.startup` default to
# `charts/ai-proxy/values.yaml` (failureThreshold 30 × 5s = 150s budget, same shape as the gateway/dashboard
# startup defaults), and (2) render `startupProbe` in envoy-deployment.yaml from it (httpGet /ready :healthPort).
# CONTRACT DELTA: this task MAY ALSO edit `charts/ai-proxy/templates/envoy-deployment.yaml` (add the
# startupProbe block) AND `charts/ai-proxy/values.yaml` (add the envoy.probes.startup default) for THIS
# fix ONLY — no other change to either file. A tests/kind regression asserts the envoy Deployment renders
# a startupProbe whose httpGet path is /ready on the health port. No chart-template changes beyond
# envoy-configmap.yaml (v2) + envoy-deployment.yaml (v3); the only non-template chart edit is the additive
# values.yaml startup default.

# === v4 CHANGE REQUEST (orchestrator-amended 2026-06-27; Tin ratifies at the verify gate — HARD-STOP, edge) ===
# With the v3 startupProbe stopping the crashloop, the cold-kind run exposed TWO MORE edge defects that
# only a live cold kubelet reveals (proven live, each masked the prior):
#  (A) STRICT_DNS resolves ZERO hosts. envoy /clusters showed gateway_cluster + dashboard_cluster with NO
#      host rows → /v1 403 (ext_authz fails closed) and / 503. Envoy's default `dns_lookup_family: AUTO`
#      attempts AAAA/IPv6 lookups that misbehave on an IPv4-only kind cluster, leaving the clusters
#      host-less (no DNS-failure log). PROVEN FIX: set `dns_lookup_family: V4_ONLY` on BOTH STRICT_DNS
#      clusters in envoy-configmap.yaml → clusters immediately resolved to the Service ClusterIPs
#      (health_flags::healthy). This is a PROD template fix (correct for V4 ClusterIP Services everywhere).
#  (B) kindnet ENFORCES NetworkPolicy. The task-1 §1 assumption "kindnet IGNORES NP" is FALSE for this kind
#      (v0.32 / k8s v1.36 — kindnetd now enforces NP). With DNS fixed, envoy→ClusterIP connections ALL
#      failed (cx_connect_fail == cx_total) → still 503/403. The ingress-only `ai-proxy-dashboard` +
#      `ai-proxy-envoy` NPs block the legit edge→upstream path under enforcement. PROVEN FIX: deleting both
#      NPs → /api/health 200, / 200, /v1/models 401 (REAL gateway auth). Resolution: DISABLE NP in the KIND
#      OVERLAY ONLY (`envoy.networkPolicy.enabled:false` + `dashboard.networkPolicy.enabled:false` in
#      values-kind.yaml) — prod keeps NP enabled. This is consistent with the FROZEN §1 assumption ("NP not
#      validated in kind") — only the REASON changed (we disable rather than kindnet ignoring).
# HARD CONSEQUENCE (flag + §7 delta): because kindnet really enforces NP, the PROD envoy/dashboard NPs are
# now likely BROKEN under real enforcement (they blocked legitimate edge→upstream traffic) → NP-CORRECTNESS
# IS A REQUIRED CLOUD-RUNBOOK FIX BEFORE ANY CLOUD APPLY (the milestone's HARD-STOP boundary). Not fixed
# here (a multi-template task-2/3/4 effort, out of kind-bootstrap scope) — surfaced as an open SPEC delta.
# CONTRACT DELTA: (A) ADD `dns_lookup_family: V4_ONLY` to the 2 STRICT_DNS clusters in the already-authorized
# envoy-configmap.yaml (still the only template edited besides envoy-deployment.yaml); (B) ADD the two
# networkPolicy.enabled:false lines to the already-in-scope values-kind.yaml. NO new scope tokens. tests/kind
# regressions assert (A) the STRICT_DNS clusters set dns_lookup_family V4_ONLY and (B) the kind overlay
# disables the envoy + dashboard NetworkPolicies.

Least-sure flag surfaced at freeze: [spec] the PSS-restricted locally-built images actually RUN on a real kubelet — the 72 render tests prove SHAPE, not RUNTIME, so the dashboard `readOnlyRootFilesystem` + the gateway/dashboard `runAsNonRoot`/uid-1000/drop-ALL are unproven until a live pod boots; if wrong the pods CrashLoopBackOff (EROFS on a missed Next write-path, or a uid/ownership mismatch) and `kind-up` never reaches Ready. Cost if wrong: Dockerfile/securityContext/emptyDir rework — but this is precisely the task-4/task-5 runtime obligation this task exists to discharge, so a failure here is a CAUGHT regression, not wasted work. Secondary: [contract] the in-image `alembic upgrade head` finding `migrations/`+`alembic.ini` at `/app` at boot (task-5, only provable when the initContainer runs).

Least-sure flag surfaced at freeze (v2): [contract] the FQDN fix assumes the chart always installs into a namespace whose in-cluster DNS suffix is the standard `.svc.cluster.local` (true for kind + every default k8s; a cluster with a custom `clusterDomain` would need that overridden) — accepted because it is the canonical k8s form and strictly better than the broken short name; cost if a non-default clusterDomain is ever used: a values knob for the suffix (future delta).

Least-sure flag surfaced at freeze (v3): [contract] the startupProbe fix assumes envoy's `:9902` health listener returns 200 within the 150s startup budget (failureThreshold 30 × 5s) on a cold cluster — true once init completes and the listener binds (DNS warming finishes well under 150s even with a slow CoreDNS); if a cluster were so degraded that warming exceeded 150s the startupProbe would (correctly) fail and surface the real problem rather than mask it. Accepted: the startupProbe only DELAYS liveness, never disables it — strictly safer than today's no-startupProbe template, and mirrors the gateway/dashboard pattern already in the chart.

Least-sure flag surfaced at freeze (v4): [contract] `dns_lookup_family: V4_ONLY` is correct for V4 ClusterIP Services (kind + every default k8s) but would not resolve on a hypothetical V6-only cluster — accepted (internal Services are V4; a future values knob could expose V4_PREFERRED for dual-stack). BIGGER risk surfaced, not hidden: [spec] kindnet ENFORCES NetworkPolicy here, so the PROD envoy/dashboard NPs — which blocked legitimate edge→upstream traffic under enforcement — are now a REQUIRED cloud-runbook fix before any cloud apply; disabling NP in the kind overlay unblocks the kind proof but DEFERS NP-correctness to a §7 delta (the milestone's documented cloud-apply HARD-STOP boundary owns it). This is the most-likely-wrong assumption in the whole bundle and is now explicit.

Status: FROZEN @ v4 — approved by Tin · 2026-06-27 (v1 = harness shape; v2 = +envoy-configmap FQDN; v3 = +startupProbe; v4 = +envoy-configmap `dns_lookup_family: V4_ONLY` for IPv4 STRICT_DNS resolution + values-kind NetworkPolicy disable because kindnet ENFORCES NP and the prod NPs block the edge path). v3+v4 were orchestrator-amended from live cold-cluster evidence and RATIFIED by Tin at the verify gate ("Gate PASS — ratify v3+v4", 2026-06-27), together with the NP-correctness cloud-runbook HARD-STOP delta.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: harness/artifact SHAPE is unit-tested (fast, CI-safe, no cluster); the LIVE
`kind-up`→stack-Ready proof is the build/verify EVIDENCE (a real cluster bring-up, recorded at
the gate), not a pytest in the fast suite — too heavy/non-deterministic to gate every change on.
Plan (one test per scenario, asserting behavior not internals — render/parse the real files):
<test_plan>
  - test_kind_overlay_renders_local_images: `helm template -f values-kind.yaml` → gateway+dashboard images carry the kind-local tag with pullPolicy:Never (M1/M2, scenario "overlay overrides only env-specific inputs").
  - test_kind_overlay_creates_test_secrets: render → datastores.secrets.create=true mints ai-proxy-datastore-secrets AND gateway.jwtSecret creates ai-proxy-gateway-secrets; gateway.env.databaseUrlSecretRef points at the datastore Secret (M2/M7).
  - test_kind_overlay_points_upstream_at_stub: render → all four GATEWAY_*_BASE_URL envs resolve to the in-cluster stub Service URL (M2/M6).
  - test_kind_overlay_no_template_edit: `git` shows charts/ai-proxy/templates/ unchanged vs HEAD; the overlay is the ONLY new chart file (M2 "no template edit").
  - test_upstream_stub_manifest_shape: parse infra/kind/upstream-stub.yaml → a Deployment (non-root securityContext, readinessProbe) + a Service named ai-proxy-upstream-stub on port 8080 (M6).
  - test_cluster_config_shape: parse infra/kind/cluster.yaml → kind Cluster v1alpha4 with an extraPortMappings exposing the edge to the host (M3 "edge reachable from host").
  - test_makefile_declares_kind_targets: the root Makefile defines kind-up, kind-down, kind-load, kind-smoke and lists them in .PHONY; kind-up references build+kind load+helm+a bounded wait (--timeout) (M1/M5).
  - test_makefile_wait_is_bounded_and_dumps_diag: kind-up's wait carries an explicit --timeout AND the recipe has a diagnostics-on-failure path (kubectl get pods/events/describe) — never an unbounded wait (M5, Reject kind_stack_not_ready).
  - test_no_real_secret_literal: values-kind.yaml + infra/kind/* carry no real secret (no BEGIN PRIVATE KEY / real-looking creds); the fake test creds are obviously-fake (M7, SECRETS-NEVER-IN-CHART).
  - test_envoy_clusters_are_fqdn (v2 regression): render the chart → every STRICT_DNS cluster `load_assignment` address in the envoy ConfigMap is an FQDN ending `.svc.cluster.local` (NOT a bare short name), so envoy's c-ares resolver resolves its upstreams in real k8s and the edge can ever come Ready.
  - test_envoy_renders_startup_probe (v3 regression): render the chart → the envoy Deployment container declares a startupProbe whose httpGet path is /ready on the health port (so the kubelet suppresses liveness until envoy first becomes ready), mirroring the gateway/dashboard templates; guards against regressing to the no-startupProbe form that lets liveness kill envoy during cold-start DNS warming.
  - test_envoy_clusters_v4_only (v4 regression): render the chart → every STRICT_DNS cluster in the envoy ConfigMap sets `dns_lookup_family: V4_ONLY`, so envoy resolves V4 ClusterIP Services on an IPv4 cluster instead of leaving the cluster host-less under the default AUTO/AAAA behavior.
  - test_kind_overlay_disables_networkpolicies (v4 regression): render with values-kind.yaml → NO NetworkPolicy object is emitted (envoy + dashboard NP disabled), because kindnet ENFORCES NP here and the prod NPs block the legit edge→upstream path; the kind proof runs NP-free (consistent with the §1 "NP not validated in kind" assumption), prod keeps NP.
  - (LIVE, not in the fast suite — build/verify evidence): `make kind-up` → every Deployment+StatefulSet Ready + bucket Job Completed; gateway Ready against a migrated DB; dashboard Ready under read-only rootfs; edge answers on https://127.0.0.1:8443; `make kind-up` re-run converges; `make kind-down` clean (M1/M3/M4/M5).
</test_plan>

Tests live in: `tests/kind/` `tests/helm/test_envoy_edge_manifests.py` `tests/helm/test_dashboard_chart.py` · MUST run red (missing implementation) before Build.
<!-- v2 tail: the FQDN fix MOVES the rendered envoy cluster address (short → FQDN), so the 2 task-3/4 tests that PINNED the short address as their expected value must update to the FQDN (faithful correction, not weakening — they still assert the address IS the gateway/dashboard Service, now correctly resolvable). Deployment-NAME assertions stay short (only the cluster ADDRESS gains the suffix). My own test_kind_overlay_no_template_edit narrows to allow ONLY the v2-authorized envoy-configmap.yaml edit. -->
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `Makefile` `infra/kind/cluster.yaml` `infra/kind/upstream-stub.yaml` `infra/kind/edge-nodeport.yaml` `charts/ai-proxy/values.yaml` `charts/ai-proxy/values-kind.yaml` `charts/ai-proxy/templates/envoy-configmap.yaml` `charts/ai-proxy/templates/envoy-deployment.yaml` `tests/kind/test_kind_bootstrap.py` `tests/helm/test_envoy_edge_manifests.py` `tests/helm/test_dashboard_chart.py`
<!-- v2: ADD charts/ai-proxy/templates/envoy-configmap.yaml — the ONE chart-template edit the v2 change request authorizes (FQDN the 2 STRICT_DNS cluster addresses so envoy resolves its upstreams in real k8s). No other template touched. -->
<!-- v3: ADD charts/ai-proxy/templates/envoy-deployment.yaml (render the startupProbe) + charts/ai-proxy/values.yaml (the additive envoy.probes.startup default it reads) — the v3 change request, mirroring gateway/dashboard, so liveness no longer kills envoy during cold-start DNS warming. The ONLY edits to those two files are the startupProbe wiring + its value default. -->
<!-- v3 scope note: charts/ai-proxy/values.yaml is the FROZEN base schema from tasks 1–5; this adds ONE additive key (envoy.probes.startup) consistent with the existing gateway/dashboard startup defaults — no existing key changed. -->
<!-- build refinement: the stub server script is embedded in upstream-stub.yaml's ConfigMap (self-contained `kubectl apply -f`), so no separate upstream_stub.py. ADD infra/kind/edge-nodeport.yaml: a harness-applied NodePort Service (nodePort 30443 → envoy https) so cluster.yaml's extraPortMappings (host 8443→node 30443) routes deterministically WITHOUT a chart-template edit (the chart envoy Service stays ClusterIP). -->
Strategy (ordered batches): 1. `charts/ai-proxy/values-kind.yaml` overlay (overrides only env-specific schema inputs) → render-tests green. 2. `infra/kind/upstream-stub.yaml` (+ stdlib `infra/kind/upstream_stub.py` server, runnable as `python -m http.server`-style via a ConfigMap-mounted script OR a `python:3.12-slim` image with the script baked) → manifest-shape test green. 3. `infra/kind/cluster.yaml` → config-shape test green. 4. root `Makefile` kind targets (preflight → ensure cluster → build+load → apply stub → helm upgrade --install → bounded wait + diag-on-fail; +.PHONY) → makefile tests green. 5. LIVE: `make kind-up` → drive the real cluster to Ready; iterate on any runtime failure (the ⚠ flag); then `make kind-down`.
Safety rule (feature-specific): every `kubectl wait`/`rollout status` carries `--timeout`; on breach the recipe dumps diagnostics and exits non-zero (no unbounded wait, no false-green). kind context only — never a cloud apply. Test creds are obviously-fake literals (SECRETS-NEVER-IN-CHART).
Code lives in: `infra/kind/` + `charts/ai-proxy/values-kind.yaml` + root `Makefile`
Constraints: do NOT change any test or the contract; do NOT edit any file under `charts/ai-proxy/templates/` (overlay only); allow-list packages only (stdlib for the stub); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 85 helm+kind render tests green (incl. v2/v3/v4 regressions)
- [x] coverage did not decrease — added tests (+ no source removed); render coverage grew
- [x] no test or contract was altered during build — contract changes were CHANGE-REQUESTS (heal cycle back to contract), not build-time edits; tests were ADDED/STRENGTHENED, never weakened
- [x] the green was EARNED — adversarial refute-read (security-expert subagent) VERDICT SOUND; the 3 new tests are red-before-fix; one non-blocking vacuous-post-commit test logged as a TDD delta (not a cheat)
- [x] concurrency / timing — startupProbe only DELAYS liveness (never disables); bounded waits everywhere; envoy restarts:0 from cold proves the timing fix
- [x] no exposed secrets / injection / unexpected deps — only obviously-fake `kind-local-*` creds in the overlay; no new packages; refute-read confirmed
- [x] layering & dependencies — overlay + two authorized envoy-template edits + additive value; prod posture unchanged (refute-read confirmed all edge controls intact)
- [x] a person reviewed and approved — Tin ratified v3+v4 at the gate ("Gate PASS — ratify v3+v4")

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `helm template -f charts/ai-proxy/values-kind.yaml` renders gateway+dashboard with local image refs + pullPolicy:Never, mints both test Secrets, and the gateway's four GATEWAY_*_BASE_URL envs point at the in-cluster stub Service — CONFIRMED: tests/kind suite green + live render.
- [x] ONLY the v2/v3/v4-authorized chart edits exist: templates envoy-configmap.yaml (FQDN + dns_lookup_family) + envoy-deployment.yaml (startupProbe); non-template values.yaml (envoy.probes.startup) + values-kind.yaml (overlay incl. NP-disable) — CONFIRMED: `git status` shows exactly those two templates + values + overlay; refute-read verified prod still renders both NetworkPolicies (no silent removal).
- [x] `make kind-up` from a clean machine drives the WHOLE stack Ready from COLD — CONFIRMED: all 10 pods Ready in ~72s, envoy restarts:0, kind-up exit 0, harness printed "✅ kind stack Ready"; bucket created (`/data/ai-proxy-artifacts` in minio).
- [x] the gateway pod's initContainers ran wait-for-db → `alembic upgrade head` against a MIGRATED DB; the dashboard pod Ready under readOnlyRootFilesystem — CONFIRMED: migrate initContainer log reached head `e2f4a6b8c0d1`; dashboard pods 1/1 restarts:0 (no EROFS). (discharges the task-4/5 ⚠ flag.)
- [x] the Envoy edge answers from the host at `https://127.0.0.1:8443` (TLS terminated) — CONFIRMED: `make kind-smoke` exit 0 (HTTP 200).
- [x] the edge ROUTES correctly end-to-end — CONFIRMED: `/api/health` 200, `/` 200 (dashboard), `/v1/models` 401 (REAL gateway auth); envoy `/clusters` both clusters resolved to ClusterIP hosts (health_flags::healthy, connections succeed).
- [x] `make kind-up` re-run converges and `make kind-down` removes the cluster idempotently — CONFIRMED: 2nd up RC 0 ("✅ Ready"), down#1 RC 0, down#2 RC 0 (absent), `kind get clusters` = none.
- [x] a bounded wait + diagnostics-on-failure path exists — CONFIRMED: Makefile `--timeout` on every rollout + diag-on-fail block + test_makefile_wait_is_bounded_and_dumps_diag.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (infra/chart) — `startupProbe` reads `.Values.envoy.probes.startup` (rendered, confirmed live: envoy pod carries the probe); `dns_lookup_family: V4_ONLY` on both STRICT_DNS clusters (confirmed live: both clusters resolved to ClusterIP hosts); kind overlay `networkPolicy.enabled:false` consumed by the NP templates (confirmed live: 0 NetworkPolicies under the overlay). No new code symbols.
- [x] DEAD-CODE — none; every added value/key is consumed by a template that renders.
- [x] SEMANTIC — adversarial refute-read (security-expert subagent) read TASK.md §3/§5/§6/§7 + the 4 chart files + tests + the actual `helm template` renders. VERDICT: SOUND, no HARD-STOP. Confirmed: prod STILL renders both NetworkPolicies (kind renders 0 — no silent prod NP removal); ext_authz failure_mode_allow:false, admin :9901 loopback, TLSv1_2 min, HSTS, rate-limit all UNCHANGED; no real secret committed; the 3 new v3/v4 tests are REAL (red before fix). ONE non-blocking finding: `test_kind_overlay_only_authorized_template_edit` (pre-existing, v2-session) goes vacuous post-commit (diffs git working tree → empty after commit) → logged as a TDD delta; the 3 substantive render tests + review carry the guard. Pre-existing comment typo in envoy-networkpolicy.yaml ("/ready :9901" should read :9902) noted — not introduced here, no behavior impact.

### GATE RECORD
Outcome: PASS   <!-- security-sensitive edge gate; Tin ratified v3+v4 ("Gate PASS — ratify v3+v4") after the full cold-cluster live proof + a SOUND adversarial refute-read. NP-correctness-under-enforcement deferred to the cloud-runbook HARD-STOP (open §7 delta), not a kind-proof blocker. -->
Reviewed by: Tin · date: 2026-06-27

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] NP-correctness-under-enforcement (HARD-STOP cloud-runbook prereq): kindnet ENFORCES NetworkPolicy in kind v0.32/k8s v1.36 (the task-1 "kindnet ignores NP" assumption was FALSE), and the prod `ai-proxy-envoy` + `ai-proxy-dashboard` NPs BLOCK the legit edge→upstream path under enforcement (evidence: envoy→ClusterIP cx_connect_fail==cx_total → /api/health 503, /v1 403; deleting both NPs → 200/200/401). Disabled in the kind overlay to unblock; the prod NPs MUST be fixed (correct from-selectors + stateful return traffic, likely add gateway/datastore NPs) and re-validated on a NP-capable cluster BEFORE any cloud apply. Own task spanning the task-2/3/4 NP templates.
- [SPEC · open] envoy dns_lookup_family knob for dual/V6 stacks: V4_ONLY (v4 fix) is correct for V4 ClusterIP Services but a V6-only cluster would need V4_PREFERRED/AUTO — expose `envoy.dnsLookupFamily` as a values knob if a dual-stack/V6 target ever appears (evidence: hard-coded V4_ONLY in envoy-configmap.yaml).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->

- [TDD · folded] A "no unauthorized edit" guard that diffs `git status --porcelain` is VACUOUS once the change is committed (working tree clean → assert trivially passes), so it gives false CI confidence (evidence: refute-read on test_kind_overlay_only_authorized_template_edit). Prefer guarding the INVARIANT via the rendered output (e.g. assert prod renders exactly the expected NetworkPolicies / probe shape) rather than VCS working-tree state. [folded foundation-version 39]
- [TDD · folded] Render-only (helm template) tests prove SHAPE, not RUNTIME: 72→85 green tests passed while THREE distinct live-only edge defects (no startupProbe→crashloop, dns_lookup_family AUTO→0 hosts, kindnet-enforced NP→blocked edge) sat undetected until a cold `make kind-up` (evidence: all three only surfaced live). A live bring-up is a REQUIRED gate evidence tier for infra, not optional. [folded foundation-version 39]
- [DDD · folded] Environment assumptions decay: "kindnet ignores NetworkPolicy" was true once, false in kind v0.32/k8s v1.36 — assumptions about external tooling behavior must be RE-VALIDATED live each milestone, not carried forward (evidence: NP enforcement broke the edge despite the documented assumption). [folded foundation-version 39]
