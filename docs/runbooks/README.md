# Hydroa runbooks

Operational guide for **Hydroa** — a multi-tenant AI proxy over
[OpenRouter](https://openrouter.ai) (and OpenAI / Anthropic / Gemini / Bedrock /
Azure) with per-tenant cost tracking, key governance, budgets, rate limiting,
spend analytics, alerting, and an admin dashboard.

These runbooks are written for three audiences. Pick your path:

| You are… | Start here | Then |
|----------|-----------|------|
| **Running the platform** (deploy, configure, operate) | [01 — Getting started](./01-getting-started.md) | [02 — Admin guide](./02-admin-guide.md) |
| **A tenant admin** (manage keys, budgets, members, providers) | [02 — Admin guide](./02-admin-guide.md) | [04 — Multi-tenant guide](./04-multi-tenant-guide.md) |
| **An API client / app developer** (call LLMs through the proxy) | [03 — API client guide](./03-api-client-guide.md) | — |
| **Designing tenancy / auth / RBAC** | [04 — Multi-tenant guide](./04-multi-tenant-guide.md) | [02 — Admin guide](./02-admin-guide.md) |
| **Using the admin dashboard** (screen-by-screen) | [06 — Dashboard UI walkthrough](./06-dashboard-ui.md) | [02 — Admin guide](./02-admin-guide.md) |
| **Stuck on an error** | [05 — Troubleshooting & FAQ](./05-troubleshooting.md) | — |

> **Verification.** Every command and example response in these runbooks was
> executed live against the `make edge` Docker stack on 2026-06-27 (gateway
> commit on `main`). Captured responses are marked _“verified live”_. A handful
> of provider-specific surfaces (Azure AAD, Bedrock SigV4) are documented from
> the source contract rather than executed.

---

## What Hydroa is, in one diagram

Every request enters through the **Envoy edge**. The FastAPI **gateway** is never
reachable directly from outside. Two planes share the edge:

- **Data plane** — `POST /v1/*` LLM calls authenticated by an `sk-` API key (or
  an agent OAuth token). Envoy calls the gateway’s `/internal/authz` to resolve
  the key → tenant before the request proceeds (`ext_authz`).
- **Control plane** — `/admin/*` management calls authenticated by a tenant JWT.
  Envoy validates the JWT signature (`jwt_authn`) at the edge.

```
  API client (sk-… / agent token)            Browser (admin)
          │                                         │ session cookie
          │ Authorization: Bearer sk-…              ▼
          │                              ┌────────────────────────────┐
          │                              │ Next.js dashboard (BFF)     │
          │                              │  /api/gw/[...path]          │
          │                              │  attaches Bearer JWT        │
          ▼                              └───────────────┬─────────────┘
  ┌───────────────────────────────────────────────────────────────────┐
  │ Envoy edge        :8080 (HTTP)   :8443 (HTTPS+HSTS)   :9901 (admin) │
  │   • local_ratelimit  — 50 rps / worker                             │
  │   • jwt_authn        — HS256 on /admin/*  (login+signup exempt)    │
  │   • ext_authz        — /v1/* → POST /internal/authz (resolve key)  │
  │   • route            — /internal/* → 403  (hard-blocked)           │
  └───────────────────────────────┬───────────────────────────────────┘
                                   │  (in-cluster only)
                                   ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │ FastAPI gateway   :8000   (DDD modules, one bounded context each)  │
  │                                                                    │
  │  proxy   auth   agent_oauth   tenants   keys   usage   catalog     │
  │  budgets   teams   rate_limits   ops   audit   alerting            │
  │  memory   conversations   artifacts   video   objectstore         │
  └───────┬───────────────────────────┬───────────────────┬───────────┘
          ▼                           ▼                   ▼
     PostgreSQL                    Redis           LLM providers (per-tenant BYOK)
   (tenants, keys, usage,    (rate limits, budgets,   OpenRouter · OpenAI · Anthropic
    audit, catalog, …)        cache, cooldown, …)     Gemini · Bedrock · Azure
```

---

## The trust boundary (read this once)

| Path prefix | Edge policy | Who reaches it |
|-------------|-------------|----------------|
| `/v1/*` | `ext_authz` → `/internal/authz` resolves the `sk-`/agent token to a tenant | API clients with a valid key |
| `/admin/*` | `jwt_authn` validates the HS256 JWT (signup + login exempt) | Tenant users with a session JWT |
| `/oauth/*` | public (device flow) + JWT (approve/deny) | Headless agents + approving humans |
| `/auth/oidc/*` | public (IdP-driven) | SSO login redirects |
| `/ops/*` | mTLS client cert (XFCC), fail-closed | Platform operators only |
| `/internal/*` | **403 at the edge** — never externally reachable | Cluster-internal callers only |
| `/health` | public | Load balancers / probes |

**Verified live:** `/internal/health` → `HTTP 403`; `/v1/chat/completions`
without a key → `HTTP 401`; `/admin/usage` without a JWT → `HTTP 401`.

---

## Key concepts / glossary

| Term | Meaning |
|------|---------|
| **Tenant** | The top-level isolation boundary (an org). Every row carries `tenant_id`. |
| **User / role** | A member of a tenant with one role: `owner`, `admin`, `operator`, `billing_admin`, `viewer`, `member`. See the [permission matrix](./04-multi-tenant-guide.md#roles--permissions). |
| **API key (`sk-…`)** | A tenant-scoped credential an API client uses to call `/v1/*`. Shown once on create. Carries budget / rate-limit / model-allowlist governance. |
| **Agent token** | A short-lived bearer token minted via the OAuth **device flow** (RFC 8628) for headless agents. Accepted on `/v1/chat/completions`. |
| **BYOK** | _Bring Your Own Key._ Each tenant stores its own provider API keys (Fernet-encrypted at rest). The proxy resolves them **per request** — there is no shared platform provider key for completions. |
| **Catalog** | The model list, synced from OpenRouter. 339 models in the live verification. |
| **Edge** | The Envoy proxy — the single public ingress. |
| **Reconciliation / drift** | The mechanism that compares what providers charged vs. what Hydroa billed, to catch unbilled upstream cost. |

---

## Environments at a glance

| Environment | Command | Edge URL | When to use | Runbook |
|-------------|---------|----------|-------------|---------|
| **Local Docker edge** | `make edge` | `http://127.0.0.1:8080` | Fastest production-shaped local stack; demos, manual testing | [01 §Local Docker edge](./01-getting-started.md#a-local-docker-edge-stack-make-edge) |
| **Local dev (datastores only)** | `docker compose -f infra/docker-compose.dev.yml up -d` | gateway runs on host | Running the test suite / gateway on the host | [01 §Local dev](./01-getting-started.md#b-local-dev-stack-datastores-only) |
| **Local Kubernetes (kind)** | `make kind-up` | `https://127.0.0.1:8443` | Production-shaped k8s with the Helm chart, zero cloud creds | [01 §kind](./01-getting-started.md#c-local-kubernetes-kind--helm) |
| **Production cloud (Helm)** | `helm upgrade --install …` | your LB host | Real deployment (human-run) | [01 §Production cloud](./01-getting-started.md#d-production-cloud-helm) |

## Operator deep-dives

Two existing operator runbooks complement the deployment guide above:

- [`cloud-deploy.md`](./cloud-deploy.md) — the full human-run production deploy
  procedure (image build → registry → Helm apply, with the NetworkPolicy and
  encryption-key pre-apply gates).
- [`backup-rollback.md`](./backup-rollback.md) — backup, restore, and Alembic
  downgrade procedures for rollback.
