# 01 — Getting started: run & deploy

How to bring Hydroa up across all four environments, plus the configuration
reference. If you only want to _use_ a running instance, skip to the
[Admin guide](./02-admin-guide.md) or [API client guide](./03-api-client-guide.md).

- [Prerequisites](#prerequisites)
- [A. Local Docker edge stack (`make edge`)](#a-local-docker-edge-stack-make-edge)
- [⚠ The one gotcha that bites everyone: provider-key encryption](#-the-one-gotcha-that-bites-everyone-provider-key-encryption)
- [B. Local dev stack (datastores only)](#b-local-dev-stack-datastores-only)
- [C. Local Kubernetes (kind + Helm)](#c-local-kubernetes-kind--helm)
- [D. Production cloud (Helm)](#d-production-cloud-helm)
- [Configuration reference](#configuration-reference)
- [Migrations](#migrations)
- [Dashboard](#dashboard)

---

## Prerequisites

| Tool | Version (validated) | Used by |
|------|--------------------|---------|
| Docker + Compose v2 (`docker compose`) | Engine ≥ 24 | all local stacks |
| `uv` | latest | gateway install / tests |
| Node.js | 22 (`node:22-alpine`) | dashboard |
| `kind` | v0.32.0 | local Kubernetes |
| `kubectl` | ≥ 1.28 | local Kubernetes |
| `helm` | ≥ 3.16 | kind + cloud |
| `openssl` | stdlib | kind TLS cert |

Only Docker + a provider key are needed for the `make edge` path below.

---

## A. Local Docker edge stack (`make edge`)

The primary production-shaped local stack: **Envoy edge → gateway → Postgres +
Redis**, all in Docker. This is what every example in these runbooks was verified
against.

### What comes up

`infra/docker-compose.e2e.yml` (compose project `hydroa-e2e`):

| Container | Image | Host port |
|-----------|-------|-----------|
| `hydroa-e2e-postgres-1` | `postgres:16-alpine` | — (internal) |
| `hydroa-e2e-redis-1` | `redis:7-alpine` | — (internal) |
| `hydroa-e2e-gateway-1` | built from `apps/gateway/Dockerfile` | — (internal `:8000`) |
| `hydroa-e2e-envoy-1` | `envoyproxy/envoy:v1.29-latest` | **8080, 8443, 9901** |

Postgres/Redis bind no host port (so they don’t clash with the dev stack on
5433/6380). The JWT secret is shared between the gateway and Envoy’s JWKS at
start-up.

### Quickstart

```bash
# Put a real provider key where compose can read it (gitignored, optional).
# Used for the model-catalog sync (see the gotcha below about completions).
printf 'GATEWAY_OPENROUTER_API_KEY=sk-or-...\n' >> apps/gateway/.env

# Build + start the whole stack and wait for healthchecks, then sync the catalog.
make edge

# In a second terminal, point the dashboard at the edge.
make edge-dashboard          # → http://localhost:3000

# Prove the path end-to-end (auth + real completion + cost tracking).
make edge-smoke

# Tear down (add VOLUMES=1 to drop the DB/Redis data too).
make edge-down
```

| Address | What |
|---------|------|
| `http://127.0.0.1:8080` | Envoy HTTP — full filter chain |
| `https://127.0.0.1:8443` | Envoy HTTPS — same + HSTS |
| `http://127.0.0.1:9901` | Envoy admin (`/clusters`, `/stats`, `/config_dump`) |
| `http://localhost:3000` | Dashboard (when `make edge-dashboard` runs) |

Override the shared JWT secret with `make edge GATEWAY_JWT_SECRET=…`.

**Verified live:** after `make edge`, `curl http://127.0.0.1:8080/health` →
`{"status":"ok","service":"gateway"}`.

> **Known race:** Envoy depends on the gateway becoming healthy. On a cold first
> build, `--wait` can return before Envoy actually starts, leaving it in
> `Created` with no port bindings. If `:8080` is dead, run:
> ```bash
> GATEWAY_JWT_SECRET=e2e-test-secret-change-me \
>   docker compose --env-file apps/gateway/.env -f infra/docker-compose.e2e.yml \
>   up -d --force-recreate --no-deps envoy
> ```
> Also make sure nothing else holds `:8443` (a leftover `kind` cluster does —
> `make kind-down` frees it).

---

## ⚠ The one gotcha that bites everyone: provider-key encryption

The `make edge` stack will **500 on every `/v1` completion out of the box**, with
this in the gateway log:

```
gateway.proxy.domain.provider_credentials.ProviderCredentialError:
    ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE
```

This is by design, and it is the single most important operational fact about
Hydroa:

> **Completions resolve provider credentials per-tenant (BYOK). There is no
> shared platform provider key for the data plane.** The `GATEWAY_OPENROUTER_API_KEY`
> env var only powers the **catalog sync** (and optional cost-recovery), **not**
> `/v1` completions.

To make completions work you need **two** things:

1. **`GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY`** set to a Fernet key, so the gateway
   can encrypt/decrypt stored provider keys. The `e2e` compose does **not** set
   this; production (`values-prod`) requires it.
2. **A per-tenant BYOK provider key** configured via
   `PUT /admin/provider-keys/{provider}` (owner only) — see the
   [Admin guide](./02-admin-guide.md#5-byok--bring-your-own-provider-keys).

### Enable it on the local edge stack

```bash
# 1. Generate a Fernet key (the gateway image already has `cryptography`).
FERNET=$(docker exec hydroa-e2e-gateway-1 \
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# 2. Add it to the gateway via a compose override and recreate (DB data persists).
cat > /tmp/edge-override.yml <<YAML
services:
  gateway:
    environment:
      GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY: "${FERNET}"
YAML
GATEWAY_JWT_SECRET=e2e-test-secret-change-me \
  docker compose --env-file apps/gateway/.env \
  -f infra/docker-compose.e2e.yml -f /tmp/edge-override.yml \
  up -d --no-deps --force-recreate gateway
```

Then configure a tenant BYOK key (see Admin guide) and completions work:

**Verified live** — after setting the encryption key and
`PUT /admin/provider-keys/openrouter`:
```
POST /v1/chat/completions  →  HTTP 200 (1.0s)
{"choices":[{"message":{"content":"hydroa runbook live test ok"}}],
 "usage":{"prompt_tokens":11,"completion_tokens":7,"cost":3.9e-06}}
```

---

## B. Local dev stack (datastores only)

`infra/docker-compose.dev.yml` (project `hydroa-dev`) brings up **only** Postgres,
Redis and MinIO — no gateway, no Envoy. Use it when running the gateway on the
host or running `make test`.

| Container | Host port |
|-----------|-----------|
| postgres | **5433** |
| redis | **6380** |
| minio (S3 API / console) | **9000 / 9001** (bucket `artifacts` auto-created) |

```bash
docker compose -f infra/docker-compose.dev.yml up -d --wait
make migrate          # apply schema
make test             # full pytest suite (one process at a time!)
make test-fast        # pure-unit suites, no DB
```

> Run **one** `pytest` process at a time against `:5433` — concurrent runs
> cross-wipe the test DB and produce phantom failures.

To exercise the S3/MinIO artifact path, point the gateway at MinIO:

```
GATEWAY_OBJECT_STORE_ENABLED=true
GATEWAY_OBJECT_STORE_ENDPOINT=http://localhost:9000
GATEWAY_OBJECT_STORE_BUCKET=artifacts
GATEWAY_OBJECT_STORE_ACCESS_KEY_ID=minioadmin
GATEWAY_OBJECT_STORE_SECRET_ACCESS_KEY=minioadmin
```

---

## C. Local Kubernetes (kind + Helm)

Production-shaped Kubernetes with the `charts/ai-proxy` Helm chart, on a local
single-node `kind` cluster, with **zero cloud credentials**. Images are built and
loaded straight into the cluster (no registry). An in-cluster LLM **stub** stands
in for real providers.

```
Host :8443
  → kind extraPortMapping (node :30443)
    → NodePort Service ai-proxy-edge-nodeport (30443)
      → Envoy pods (:8443 https)
```

### Quickstart (ordered)

```bash
make kind-preflight   # verify kind/kubectl/helm/openssl/docker on PATH
make kind-up          # create cluster, build+load images, mint TLS, helm install, wait Ready
make kind-smoke       # curl -sk https://127.0.0.1:8443/api/health  → non-000 = up
make kind-e2e         # full API e2e (seed pricing → drive edge → assert usage+cost)
make kind-e2e-ui      # browser e2e (real login through the edge)
make ci-e2e           # what CI runs: kind-up + both e2e suites
make kind-down        # delete the cluster
```

**Verified live** (a running kind deployment from this project):
```
helm list            → ai-proxy  deployed  ai-proxy-0.1.0  app 0.4.0
kubectl get pods     → gateway×2, envoy×2, dashboard×2, postgres, redis, minio,
                       upstream-stub — all Running
GET https://127.0.0.1:8443/api/health → HTTP 200
```

### `values-kind.yaml` specifics (vs. prod)

| Key | kind value | Why |
|-----|-----------|-----|
| `image.pullPolicy` | `Never` | images loaded directly |
| `gateway.jwtSecret.value` | throwaway | local only |
| `gateway.providerKeyEncryption.value` | a **public** Fernet key | gateway must boot; **rotate in any real env** |
| `gateway.upstreamBaseUrls.*` | in-cluster stub | no provider keys needed |
| `envoy.networkPolicy.enabled` | **`false`** | kindnet enforces NP and the prod NPs block the edge path under kindnet |
| `dashboard.networkPolicy.enabled` | **`false`** | same |

> The NetworkPolicy-disabled overlay is **kind-specific**. Production keeps
> NetworkPolicy **on** (validated on GKE Autopilot/Cilium). Never copy the kind
> values into a real environment.

Diagnose the edge from outside the pod (the gateway image is distroless):

```bash
kubectl get pods -o wide
kubectl get events --sort-by=.lastTimestamp | tail -30
kubectl port-forward svc/ai-proxy-envoy 9901:9901   # then curl :9901/clusters
```

---

## D. Production cloud (Helm)

> **This apply is human-run, never automated by CI.** CI’s `ci-e2e` only ever
> targets the local kind context. See `docs/runbooks/cloud-deploy.md` (if present
> in your tree) for the full procedure; this is the condensed reference.

### Two blocking pre-apply gates

1. **NetworkPolicy under enforcement** — render the prod NetworkPolicies and
   validate them against a staging namespace on the real CNI **before**
   production. (They’ve been proven on GKE Cilium/Dataplane-V2.)
2. **BYOK encryption key present** — confirm the prod Secret carries a valid
   Fernet key at `provider-key-encryption-key`. It mounts `optional: true` (so old
   pods still boot), but **every `/v1` completion 500s if it’s absent** — the same
   gotcha as the local stack, in production.

### Build → push → configure → apply

```bash
# 1. Build and push images (amd64 for cloud nodes).
docker build -t <registry>/ai-proxy-gateway:<tag>   apps/gateway
docker build -t <registry>/ai-proxy-dashboard:<tag> apps/dashboard
docker push <registry>/ai-proxy-gateway:<tag>
docker push <registry>/ai-proxy-dashboard:<tag>

# 2. Create the secrets out of band (never commit them).
kubectl create secret generic ai-proxy-prod-secrets -n <ns> \
  --from-literal=jwt-secret='<signing-secret>' \
  --from-literal=provider-key-encryption-key='<fernet-key>' \
  --from-literal=pg-password='<db-password>' \
  --from-literal=redis-url='<redis-dsn>' \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Apply (values.yaml = defaults, values-prod.yaml = overrides).
helm upgrade --install ai-proxy charts/ai-proxy \
  --namespace <ns> --create-namespace \
  -f charts/ai-proxy/values.yaml -f charts/ai-proxy/values-prod.yaml \
  --timeout 600s
```

Migrations run automatically: a gateway init-container waits for Postgres
(bounded 120 s) then runs `alembic upgrade head` before the app container starts.

### `values-prod.yaml` highlights

| Key | Prod value |
|-----|-----------|
| `gateway.replicas` | `3` |
| `gateway.env.databaseUrl` / `redisUrl` | external managed datastores |
| `gateway.jwtSecret.existingSecret` | `ai-proxy-prod-secrets` |
| `envoy.networkPolicy.enabled` / `dashboard.networkPolicy.enabled` | `true` |

### Verify / rollback

```bash
kubectl rollout status deploy/ai-proxy-gateway -n <ns> --timeout=300s
curl -sS -o /dev/null -w '%{http_code}\n' https://<edge-host>/api/health
helm history  ai-proxy -n <ns>
helm rollback ai-proxy <revision> -n <ns> --timeout 600s
```

---

## Configuration reference

All gateway settings are `GATEWAY_`-prefixed env vars read by
`apps/gateway/src/gateway/core/config.py` (pydantic-settings). The most important
groups are below; the file is the exhaustive source.

> **Boot guard:** `GATEWAY_JWT_SECRET` defaults to `dev-only-secret-change-me`;
> the gateway **refuses to start** when `GATEWAY_ENVIRONMENT` is not `dev`/`test`
> and the default secret is still set.

### Core

| Env var | Default | Purpose |
|---------|---------|---------|
| `GATEWAY_ENVIRONMENT` | `dev` | `dev` / `test` / `staging` / `production` |
| `GATEWAY_DATABASE_URL` | `postgresql+asyncpg://…:5433/gateway_test` | Postgres DSN |
| `GATEWAY_REDIS_URL` | `redis://localhost:6380/0` | Redis URL |
| `GATEWAY_JWT_SECRET` | `dev-only-secret-change-me` | HS256 signing secret; shared with Envoy JWKS |
| `GATEWAY_JWT_TTL_SECONDS` | `86400` | session token lifetime |
| `GATEWAY_JWT_ISSUER` | `ai-proxy` | JWT `iss`; must match Envoy’s config |
| `GATEWAY_OPS_CERT_FINGERPRINTS` | `""` | CSV of SHA-256 fingerprints for `/ops/*` mTLS; empty = fail-closed |

### Secrets / encryption (required for the data plane)

| Env var | Default | Purpose |
|---------|---------|---------|
| `GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY` | `""` | **Fernet key** encrypting BYOK provider keys at rest. **Must be set for `/v1` completions.** |
| `GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY` | `""` | Fernet key for per-tenant OIDC config at rest (separate from the above) |

### Provider base URLs (the env keys feed catalog/cost-recovery, not completions)

`GATEWAY_OPENROUTER_BASE_URL`, `GATEWAY_OPENAI_BASE_URL`,
`GATEWAY_ANTHROPIC_BASE_URL` (+ `GATEWAY_ANTHROPIC_VERSION`),
`GATEWAY_GOOGLE_BASE_URL`, `GATEWAY_BEDROCK_REGION`, `GATEWAY_AZURE_ENDPOINT`
(+ `_API_VERSION`, `_DEPLOYMENT_MAP`). Plus per-provider default `max_tokens`.

### Feature toggles (default **OFF**)

| Env var | Default | Effect when on |
|---------|---------|----------------|
| `GATEWAY_WEB_SEARCH_ENABLED` | `false` | translate `web_search:true` into provider-native grounding |
| `GATEWAY_VECTOR_CACHE_ENABLED` | `false` | semantic (embedding-similarity) response cache |
| `GATEWAY_OPENROUTER_USAGE_ACCOUNTING` | `false` | request provider-reported cost from OpenRouter |
| `GATEWAY_OPENROUTER_COST_RECOVERY_ENABLED` | `false` | recover authoritative cost on client disconnect |
| `GATEWAY_VIDEO_DURABLE_QUEUE_ENABLED` | `false` | route video jobs through a restart-surviving Redis queue |
| `GATEWAY_OTEL_ENABLED` | `false` | OTLP trace export |
| `GATEWAY_OIDC_ENABLED` | `false` | SSO login |
| `GATEWAY_INPUT_MODALITY_GUARD_ENABLED` | `false` | reject chat/STT requests whose input type exceeds the model's catalog `input_modalities` (`400 ERR_UNSUPPORTED_INPUT_MODALITY`) |
| `GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES` | `""` (allow any) | comma-separated content-type allow-list for artifact uploads (`415 ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED`) |

### Governance knobs (mostly default OFF / fail-open)

`GATEWAY_UPSTREAM_MAX_RETRIES` (0), `GATEWAY_COOLDOWN_FAILURE_THRESHOLD` (0),
`GATEWAY_MAX_CONCURRENT_REQUESTS` (0), `GATEWAY_BANDWIDTH_TOKENS_PER_SEC` (0),
`GATEWAY_CACHE_TTL_SECONDS` (300), `GATEWAY_STT_MAX_DURATION_SECONDS` (14400),
`GATEWAY_TTS_MAX_INPUT_CHARACTERS` (4096).

### Reconciliation / retention / alerts

| Env var | Default | Note |
|---------|---------|------|
| `GATEWAY_RECONCILIATION_DRIFT_THRESHOLD` | `0` | both must be > 0 to start the drift checker |
| `GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS` | `0` | |
| `GATEWAY_RETENTION_CHECK_INTERVAL_SECONDS` | `86400` | **retention runs by default** — set windows before deploy |
| `GATEWAY_RETENTION_AUDIT_FLOOR_DAYS` | `365` | audit window never smaller than this |
| `GATEWAY_ALERT_WEBHOOK_URL` | `""` | operator alert webhook |

### Agent OAuth (device flow)

`GATEWAY_AGENT_OAUTH_VERIFICATION_URI`, `_DEVICE_CODE_TTL_SECONDS` (600),
`_ACCESS_TOKEN_TTL_SECONDS` (3600), `_REFRESH_TOKEN_TTL_SECONDS` (2592000; 0 = no
refresh), `_DEFAULT_BUDGET_USD` (**100.00** — the monthly cap per agent token),
`_AUTHORIZE_RPM` (12), `_TOKEN_RPM` (60), `_APPROVE_RPM` (30).

---

## Migrations

[Alembic](https://alembic.sqlalchemy.org); files in
`apps/gateway/src/gateway/migrations/`.

```bash
make migrate        # alembic upgrade head
make migrate-check  # alembic check (models in sync with migrations?)
```

In Kubernetes the chart runs `alembic upgrade head` as a gateway init-container
before the app starts (`gateway.migrate.enabled=true`). With `replicas > 1`,
Alembic serializes via an advisory lock — the losing pod sees head and exits 0.
For a large first migration: `helm upgrade … --set gateway.replicas=1`.

---

## Dashboard

Next.js 15 (`output: standalone`), a **BFF** — the browser never calls the
gateway directly. Every authenticated call goes through
`/api/gw/[...path]` (`apps/dashboard/app/api/gw/[...path]/route.ts`), which reads
the `ai_proxy_session` HttpOnly cookie and attaches `Authorization: Bearer <jwt>`
to the gateway request.

| Env var (server-side) | Default | Purpose |
|-----------------------|---------|---------|
| `GATEWAY_URL` | `http://localhost:8080` | in-cluster gateway address the BFF calls (never inlined into the browser) |
| `NEXT_PUBLIC_APP_URL` | — | public dashboard URL (inlined) |
| `GATEWAY_PROXY_TIMEOUT_MS` | `15000` | BFF header-phase timeout (streaming bodies aren’t killed after headers) |
| `GW_MAX_BODY_BYTES` | `33554432` | BFF request body cap (32 MiB) |

Run it against the edge:

```bash
make edge-dashboard
# = cd apps/dashboard && npm run build && \
#   GATEWAY_URL=http://127.0.0.1:8080 NEXT_PUBLIC_APP_URL=http://localhost:3000 \
#   npm run start -- -p 3000
```

`/app/*` routes are guarded (`proxy.ts`): no session cookie → 307 redirect to
`/login`.

---

**Next:** [02 — Admin guide](./02-admin-guide.md) ·
[03 — API client guide](./03-api-client-guide.md) ·
[04 — Multi-tenant guide](./04-multi-tenant-guide.md)
