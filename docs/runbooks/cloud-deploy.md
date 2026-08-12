# Real-Cloud Deploy Runbook

This runbook covers applying the ai-proxy Helm chart to a **real managed Kubernetes cluster** (the
`values-prod.yaml` swap). It is the documented counterpart to the kind-validated bring-up: the chart
and the e2e are proven locally on kind, and this is the human procedure for taking the same chart to
production.

Chart: `charts/ai-proxy/`
Base values: `charts/ai-proxy/values.yaml`
Production overlay: `charts/ai-proxy/values-prod.yaml`
Kind overlay (local only, NOT used here): `charts/ai-proxy/values-kind.yaml`

---

## Scope & the HARD-STOP boundary

**This apply is HUMAN-RUN. It is never executed by CI.** It is the single HARD-STOP boundary of the
v53 milestone: the kind-e2e CI workflow (`.github/workflows/kind-e2e.yml`) proves the stack on a
local kind cluster only — it does **not** hold cloud credentials and does **not** apply to any
managed cluster. A real-cloud apply requires a human operator with a cluster, a kubeconfig, an image
registry, DNS, TLS, and the production secrets — none of which live in this repo or in CI.

Do not wire this procedure into a pipeline without a separate, deliberate decision (real secrets +
an environment protection gate). Until then, treat every step below as run **manually** by an
operator who has reviewed the pre-apply gates.

---

## Prerequisites

Before you begin, confirm you have:

- A managed Kubernetes cluster (EKS / GKE / AKS / etc.) and a `kubeconfig` whose current context
  points at it (`kubectl config current-context`).
- A container image registry reachable by the cluster, with both images built **from the tagged
  release commit** and pushed under that release's tag.

  ⚠ Tag the image to the RELEASE, then make the chart match — not the other way round. This bullet
  used to read "tagged to match `image.tag` in `values-prod.yaml`", which inverts the dependency: the
  chart's tags had drifted nine releases (`0.4.0` while the gateway shipped `0.13.0`), so following it
  literally would have you build 0.14.0 code and publish it as `0.4.0-prod` — a deployed image tag
  that lies about what is running, which is precisely what CC8.1 asks you to be able to evidence.
  Nothing in CI builds or publishes images, so this is entirely manual; see todo #110.

  ⚠ **Build for the CLUSTER's architecture, not your laptop's.** A bare `docker build` produces an
  image for the host arch and says nothing about it. Measured 2026-08-12 on an Apple-silicon host:
  both images built `linux/arm64`. Pushed as `-prod` to a typical amd64 node pool, that image fails
  at **deploy** time with `exec format error` — *after* the artifact is published and the tag already
  means the wrong thing. Nothing catches it: the chart declares no arch `nodeSelector`, and no CI job
  builds or publishes images at all. Use `buildx` with an explicit platform list. See todo #117.

  ```bash
  # RELEASE must equal the git tag you are deploying, e.g. 0.14.1
  RELEASE=0.14.1
  git checkout "v$RELEASE"          # build from the tagged commit, never from a dirty tree

  # --platform is REQUIRED. --push (not `docker push`) is what publishes a multi-arch
  # manifest list; a plain `docker push` after a buildx build uploads only one arch.
  docker buildx build --platform linux/amd64,linux/arm64 \
    -t <registry>/ai-proxy-gateway:"$RELEASE-prod"   --push apps/gateway
  docker buildx build --platform linux/amd64,linux/arm64 \
    -t <registry>/ai-proxy-dashboard:"$RELEASE-prod" --push apps/dashboard
  ```

  Then verify the PUBLISHED manifest actually carries both arches — the build succeeding is not
  evidence that the right thing was uploaded:
  ```bash
  docker buildx imagetools inspect <registry>/ai-proxy-gateway:"$RELEASE-prod" | grep -i platform
  # expect BOTH linux/amd64 and linux/arm64
  ```

  ⚠ On an arm64 host the amd64 half is built under QEMU emulation. It is slower, and **"it built"
  is not "it runs"** — no amd64 host is available locally to execute it, so the first real amd64
  execution is the deploy itself. Treat the staging apply below as that verification.

  Then confirm the chart agrees before applying — these four must all name `$RELEASE`:
  `Chart.yaml` `appVersion`, `values.yaml` `image.tag`, `values.yaml` `dashboard.image.tag`, and both
  `-prod` overrides in `values-prod.yaml`.
  ```bash
  grep -rn "appVersion\|tag:" charts/ai-proxy/Chart.yaml charts/ai-proxy/values.yaml charts/ai-proxy/values-prod.yaml
  ```
- Managed datastores (or in-cluster equivalents) reachable at the connection strings in
  `values-prod.yaml` (`gateway.env.databaseUrl`, `gateway.env.redisUrl`, the object-store endpoint).
- DNS for the edge host and a TLS certificate (cert-manager or a `kubernetes.io/tls` Secret).
- `helm` (v3.16+) and `kubectl` installed locally and authenticated against the cluster.

---

## Pre-apply gates (MUST pass first)

These are blocking. Do **not** run the apply until each is satisfied — they are known gaps the
kind validation could not exercise.

1. **NetworkPolicy under enforcement** — *required.* The chart ships default-on NetworkPolicies for
   the envoy and dashboard workloads, but the local **kind** overlay DISABLES them because kindnet
   (kind's CNI) enforces NetworkPolicy in a way that blocks the edge path during validation. As a
   result the production NetworkPolicies have **never been validated under a real enforcing CNI** and
   are likely to block legitimate pod-to-pod traffic (edge → gateway, gateway → datastores). Before
   applying:
   - render and read the policies: `helm template ai-proxy charts/ai-proxy -f charts/ai-proxy/values.yaml -f charts/ai-proxy/values-prod.yaml --show-only templates/envoy-networkpolicy.yaml`
   - apply to a **staging** namespace on the real CNI first, run `make ci-e2e`-equivalent smoke
     against it, and fix any policy that drops a required hop (ingress to the edge ports, egress to
     the gateway + datastores + DNS).
   - only promote to production once the edge path is proven green **with** the policies enforced.
2. **Encryption-key fail-fast** — *open item.* The gateway resolves the BYOK provider-key encryption
   key from a Secret with `optional: true` (so an existing deploy whose Secret predates the key still
   boots; completions then fail closed). There is no boot-time fail-fast yet when the key is empty in
   a production environment. Until that lands, **manually confirm** the `ai-proxy-*-secrets` Secret
   carries a valid Fernet key (`gateway.providerKeyEncryption`) before the apply, or every `/v1`
   completion will 500 at runtime.
3. **Postgres collation provider — this one is a DATA-LOSS control.** *Required whenever an existing
   volume is involved.* The chart ships `datastores.postgres.image: pgvector/pgvector:pg16`, which is
   **glibc**. Earlier deploys ran `postgres:16-alpine`, which is **musl**. Booting the Debian image on
   an Alpine-initdb'd PVC is a collation-provider change: every `text` btree index is potentially
   mis-ordered, and **the database does not complain** — queries silently return wrong rows.

   Do not treat this as a tag bump. Follow **[`pgvector-deploy.md`](./pgvector-deploy.md)** and run
   its §1 preflight *before* the apply.
   - ⚠ Its §4a same-volume remedy **cannot complete** on the musl→glibc case: `ALTER DATABASE …
     REFRESH COLLATION VERSION` errors rather than clearing the preflight (walked 2026-08-10). Plan
     for **§4b dump/restore** into a database created under the new libc.
   - ⚠ `make pg-preflight` collapses FAIL into exit 2 — never branch automation on its exit code;
     read the output.
   - A genuinely **fresh** volume (first-ever install, no existing data) is unaffected. Confirm which
     case you are in before applying; "I think it's new" is not confirmation.

---

## Populate secrets (out of band)

**No secret value is committed to this repo or this runbook.** The chart references k8s Secrets by
name; you populate them from your vault / secret manager before the apply. `values-prod.yaml`
references `ai-proxy-prod-secrets` (`gateway.jwtSecret.existingSecret`).

```bash
kubectl create secret generic ai-proxy-prod-secrets \
  --namespace <namespace> \
  --from-literal=jwt-secret='<your-jwt-signing-secret>' \
  --from-literal=provider-key-encryption-key='<your-fernet-key>' \
  --from-literal=pg-password='<db-password>' \
  --from-literal=redis-url='<redis-connection-string>'
```

Use `--dry-run=client -o yaml | kubectl apply -f -` to make the create idempotent. Rotate by
recreating the Secret and restarting the affected workloads. **Never** put any of these values in a
values file or a workflow — only `${{ secrets.* }}` references (CI) or k8s Secret references (chart)
are permitted.

---

## Apply (values-prod swap)

The production apply is a single layered `helm upgrade --install` — the `values-prod.yaml` overlay
swaps the image tag, datastore endpoints, replica counts, and the secret reference **without editing
any template**:

```bash
helm upgrade --install ai-proxy charts/ai-proxy \
  --namespace <namespace> --create-namespace \
  -f charts/ai-proxy/values.yaml \
  -f charts/ai-proxy/values-prod.yaml \
  --timeout 600s
```

The gateway pod's init containers run the bounded wait-for-Postgres then `alembic upgrade head`
before the gateway container becomes ready, so the gateway never serves an unmigrated database.

---

## Verify the apply

After the apply, verify the rollout and an edge smoke before announcing the deploy:

```bash
# every workload reaches Ready (bounded — fail loudly, never hang):
kubectl rollout status deploy/ai-proxy-gateway   --namespace <namespace> --timeout=300s
kubectl rollout status deploy/ai-proxy-dashboard --namespace <namespace> --timeout=300s
kubectl rollout status deploy/ai-proxy-envoy     --namespace <namespace> --timeout=300s

# the edge answers through TLS at the real host (expect a 200/401, never a connection error):
curl -sS -o /dev/null -w '%{http_code}\n' https://<edge-host>/api/health
```

For a deeper check, run the goal-flow smoke (signup → login → a stubbed/real completion → an
accurate usage+cost row) against the real edge host, mirroring `scripts/e2e_kind.sh`.

---

## Rollback

If verification fails, roll back. Helm keeps revision history:

```bash
helm history  ai-proxy --namespace <namespace>
helm rollback ai-proxy <previous-revision> --namespace <namespace> --timeout 600s
```

For database rollback (alembic downgrade), image rollback, and backup/restore drills, follow the
companion [backup-rollback.md](backup-rollback.md) runbook. A schema downgrade must be coordinated
with the image rollback so the running gateway matches the migrated schema.
