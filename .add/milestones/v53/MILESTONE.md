# MILESTONE: Kubernetes deployment + full e2e validation

goal: An operator stands up the entire ai-proxy production stack (Next.js dashboard, gateway, Envoy edge, Postgres, Redis, object store) on a Kubernetes cluster from one env-parameterized Helm chart, proven by an automated end-to-end suite that drives the goal flow plus the dashboard UI, realtime-relay, artifacts, and admin surfaces against the live cluster.
rationale: new-major — a net-new operational/deployment pillar no archived milestone's goal covers (today the prod stack ships only as `infra/docker-compose.prod.yml`; zero k8s manifests exist). EXTENDS the production-deploy posture of every shipped milestone onto Kubernetes; DEPENDS-ON the v51 object-store seam (MinIO) and the v52 realtime relay (WS-at-edge) being on main. Decisions locked with Tin: env-parameterized HELM CHART · in-cluster datastores that are EXTERNAL-READY (conn strings = values) · CLOUD-READY but KIND-VALIDATED (real-cloud apply is a runbook, not executed — the one HARD-STOP boundary, no cloud creds) · the Next.js DASHBOARD is IN-SCOPE (Tin) — both the gateway data plane and the frontend deploy + are e2e-validated · e2e covers core goal flow + dashboard UI + realtime relay + artifacts/object-store + admin surfaces.
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  An env-parameterized **Helm chart** (`charts/ai-proxy/`) for the whole prod stack — **Next.js dashboard** Deployment+Service (standalone build + new Dockerfile) · gateway Deployment+Service · Envoy edge (TLS :443, ext_authz, WS-upgrade, path-routing browser→dashboard / API→gateway) · in-cluster Postgres/Redis/MinIO StatefulSets+PVCs · a pre-install/upgrade **alembic migration Job** · Secret/Config templates (fail-fast on unset) · liveness/readiness/startup probes, resource requests+limits, PDB, graceful-shutdown drain. A reproducible **kind** bootstrap harness (build BOTH images → load → `helm install` → wait-ready) + an in-cluster **LLM upstream stub**. An automated **e2e suite** driven through the Envoy edge against the live kind cluster covering the goal flow + the dashboard UI (browser) + realtime-relay WS round-trip + artifacts/object-store round-trip + key admin surfaces. **CI** wiring (kind-in-CI) + a **deploy runbook** for the real-cloud (values-prod) apply.
Out: Actually applying to a real managed cloud cluster (needs cluster/kubeconfig/registry/DNS/TLS/secrets — **HARD-STOP**, shipped as a documented runbook) · provisioning managed datastores (RDS/ElastiCache/R2 — values point at them; provisioning is external) · real upstream-provider keys in e2e (upstream is STUBBED) · GitOps/ArgoCD/Flux CD · service mesh (Istio/Linkerd) · autoscaling beyond a baseline HPA · multi-region/HA topology · an observability stack (Prometheus/Grafana/Loki) install.

## Shared decisions & glossary deltas   (living — every task must honor these)
- HELM VALUES = THE CONTRACT (NEW glossary, freeze first): one `values.yaml` is the single source of every environment-specific input (image ref · replicas · per-component resources · connection-string fields · secret refs · edge host/TLS · upstream base-url override). Templates contain ZERO hardcoded env values; `values-prod.yaml` is the overlay. Every other template + the e2e harness consume this schema.
- IN-CLUSTER-NOW, EXTERNAL-READY (HARD, Tin): Postgres/Redis/MinIO ship as in-cluster StatefulSets for e2e, but every connection string is a values-driven field — swapping to a managed endpoint (RDS/ElastiCache/R2) is a values change, NEVER a template edit.
- CLOUD-READY, KIND-VALIDATED (HARD, Tin): manifests are structured for a real cluster (env overlays, resources, probes, secrets), but this milestone's PROOF is a local **kind** cluster in CI. The real-cloud apply is a documented runbook, not executed here (no cloud creds = the single HARD-STOP boundary).
- MIGRATIONS-BEFORE-BOOT (HARD, inherited from `docker-compose.prod.yml`): schema bootstrap runs alembic to head BEFORE the gateway container becomes ready — the gateway never serves against an unmigrated DB. **Mechanism (Tin-decided 2026-06-27, migration-and-secrets §1):** a gateway-pod **initContainer** (`alembic upgrade head` after a bounded wait-for-Postgres), NOT a Helm pre-install hook Job — because on a fresh in-cluster `helm install` pre-install hooks run before the (non-hook) Postgres StatefulSet exists, so a hook Job cannot reach the DB. The initContainer is naturally ordered after the StatefulSet (via its wait loop) and works identically on fresh-install, upgrade, and external/managed DB. Prereq: the gateway image must carry `migrations/` + `alembic.ini` (Dockerfile COPY added here).
- SECRETS NEVER IN THE CHART (HARD, security): no secret value is committed; templates reference k8s Secrets populated from values/CI, fail-fast if unset (mirrors the prod-compose no-default posture). Security findings here are always HARD-STOP.
- E2E THROUGH THE EDGE (HARD): the e2e drives the stack through the Envoy edge Service (not the gateway directly) so the proof covers ext_authz + WS-upgrade + TLS path, matching production topology; the LLM upstream is an in-cluster STUB (no real provider keys).
- DESIGN-FOR-FAILURE IN-CLUSTER (HARD, CLAUDE.md): liveness/readiness/startup probes, resource requests+limits, restart policy, PodDisruptionBudget, and graceful shutdown (`stop_grace` > drain timeout) carry the compose resilience posture into the chart.
- DASHBOARD = STANDALONE NEXT SERVER (NEW glossary): the Next.js dashboard ships as a `output:'standalone'` Node Deployment (its own new Dockerfile — none exists today); its BFF/server routes reach the gateway via a values-driven IN-CLUSTER Service URL (server-to-server, never the public edge — consistent with the `connect-src 'self'` CSP), and the edge path-routes browser traffic to the dashboard while API paths go to the gateway.

## Shared / risky contracts (freeze these first)
- The Helm `values.yaml` schema (image · replicas · per-component resources · connection-string fields · secret refs · edge host/TLS · upstream base-url override) + the in-cluster naming/DNS `_helpers.tpl` -> owning task `helm-chart-scaffold` (frozen first; every other template + the kind harness + the e2e suite consume it).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] helm-chart-scaffold     depends-on: none                                  — `charts/ai-proxy/` skeleton: Chart.yaml · values.yaml (+ values-prod.yaml overlay) · `_helpers.tpl` naming/DNS · the gateway Deployment+Service. FREEZES the values schema contract (incl. dashboard + datastore + edge fields).
- [ ] datastore-statefulsets  depends-on: helm-chart-scaffold                   — in-cluster Postgres · Redis · MinIO as StatefulSets+PVCs+Services; gateway reaches them via values-driven connection strings (external-ready — point at managed via values alone).
- [ ] envoy-edge-manifests    depends-on: helm-chart-scaffold                   — Envoy as Deployment+Service+ConfigMap (templated `envoy-prod.yaml`): TLS edge (:443/:80), ext_authz to the in-cluster gateway, WS-upgrade for `/v1/realtime*`, path-routing browser→dashboard / API→gateway; edge Service/Ingress + TLS Secret.
- [ ] dashboard-chart         depends-on: helm-chart-scaffold                   — NEW `apps/dashboard/Dockerfile` (Next.js `output:'standalone'`) + Deployment+Service templates; BFF/server routes reach the gateway via a values-driven in-cluster Service URL (server-to-server, not the public edge).
- [ ] migration-and-secrets   depends-on: datastore-statefulsets               — pre-install/upgrade alembic Helm-hook Job (migrate-before-boot) + Secret/Config templates (fail-fast on unset) + probes/resources/PDB/graceful-shutdown wiring.
- [ ] kind-bootstrap          depends-on: envoy-edge-manifests, dashboard-chart, migration-and-secrets — kind cluster config + a `make`/script harness (build BOTH images → `kind load` → `helm install` → wait whole stack Ready) + an in-cluster LLM upstream stub. Reproducible, zero cloud creds.
- [ ] e2e-core-flow           depends-on: kind-bootstrap                        — automated e2e through the Envoy edge against the live kind cluster: tenant signup → login → proxied chat completion (stubbed upstream) → assert an accurate usage+cost row.
- [ ] e2e-platform-features   depends-on: e2e-core-flow                         — extend the e2e: realtime-relay WS round-trip through Envoy · an artifact object-store round-trip (MinIO) · key admin surfaces — all against the live cluster.
- [ ] e2e-ui                  depends-on: e2e-core-flow                         — browser-driven (Playwright, already in the dashboard) UI e2e through the edge against the live cluster: load the dashboard → log in → exercise a real authenticated surface (e.g. an API-key / usage view).
- [ ] ci-e2e-pipeline         depends-on: e2e-platform-features, e2e-ui         — wire kind-up + the full e2e (API + UI) into CI (kind-in-CI workflow) + author the real-cloud deploy runbook (values-prod swap, the HARD-STOP boundary documented).

## Exit criteria (observable; map each to the task that delivers it)
- [ ] `helm lint` + `helm template` render the full stack from values with zero hardcoded env values, and a `values-prod` overlay swaps image/host/secrets/datastore-endpoints with no template edit   (← helm-chart-scaffold)
- [ ] in-cluster Postgres/Redis/MinIO come up as StatefulSets with PVCs and are reachable by the gateway via values-driven connection strings; pointing any one at an external endpoint needs only a values change   (← datastore-statefulsets)
- [ ] the Envoy edge terminates TLS, routes browser traffic to the dashboard and API paths to the in-cluster gateway, enforces ext_authz, and upgrades WS for `/v1/realtime*` — all reachable via the edge Service   (← envoy-edge-manifests)
- [ ] the Next.js dashboard runs as an in-cluster standalone Deployment (new Dockerfile) and its BFF reaches the gateway via a values-driven in-cluster Service URL   (← dashboard-chart)
- [ ] a gateway-pod initContainer runs alembic to head before the gateway container becomes ready (gateway never serves an unmigrated DB; Tin-decided mechanism — robust on fresh kind install + upgrade + external DB), and every secret is Secret-sourced with fail-fast on unset   (← migration-and-secrets)
- [ ] `make kind-up` (or equiv) builds both images, loads them into a local kind cluster, installs the chart, and reports the whole stack Ready — reproducibly, with no cloud credentials   (← kind-bootstrap)
- [ ] an automated e2e drives the goal flow through the Envoy edge against the live kind cluster — signup → login → proxied chat completion (stubbed upstream) → accurate usage+cost row — and passes   (← e2e-core-flow)
- [ ] the e2e also exercises a realtime-relay WS round-trip (through Envoy), an artifact object-store round-trip (MinIO), and key admin surfaces against the live cluster   (← e2e-platform-features)
- [ ] a browser-driven e2e logs into the dashboard UI through the edge against the live cluster and exercises a real authenticated surface   (← e2e-ui)
- [ ] the full kind-up + e2e (API + UI) runs in CI on a runner, and a deploy runbook documents the values-prod swap for a real-cloud apply   (← ci-e2e-pipeline)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] commit the v53 work on a feature branch (Helm chart + dashboard Dockerfile + kind harness + e2e suite [API+UI] + CI workflow + runbook) — message per CLAUDE.md format
- [ ] open a PR from the Close ship-review above; Tin reviews + merges
- [ ] (deploy-time) execute the deploy runbook: swap `values-prod.yaml`, supply real secrets, `helm upgrade --install` against the real cluster (human-run — the HARD-STOP boundary)
- [ ] fold + archive v53, then bundle into the next release cut (human-run, per release.md)
