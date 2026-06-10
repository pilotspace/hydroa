# PROJECT.md — AI Proxy Platform

> The context every task reads first. Domain · active spec · UI/UX · key decisions.

## Domain

Multi-tenant **AI proxy platform** for a startup, built on top of the
[OpenRouter API](https://openrouter.ai/docs/api/reference/overview). Tenants
provision themselves, issue API keys, and route OpenAI-compatible LLM traffic
through the proxy to any model OpenRouter exposes — with per-request **cost
tracking** rolled up per key / user / tenant. An **admin dashboard** gives
tenant admins login, key management, model access, and usage/cost analytics.

**Single goal (MVP):** a user can set up their tenant → log in → call any LLM
model through the proxy → see accurate cost tracking.

Target: enterprise-grade under high workload. The proxy data path is
I/O-bound SSE streaming; design is stateless and horizontally scalable.

## Current Stage

**MVP** (ADD depth matrix: Specify/Scenarios/Contract **deep**, Tests/Build/Verify
**core**, Operate **light**). Code is **kept** — write it to be hardened, not
rewritten, when we move to Production stage.

## Active Spec

None yet. First feature slice (pending `playbook/1_specify.md`):
**tenant signup → API key issuance → proxied chat completion → usage row recorded**.
Specs live in `SPEC.md` per feature; scenarios in `features/`; frozen
interfaces in `contracts/`.

## Architecture (locked at setup)

```
client ──► Envoy (edge: TLS, jwt_authn for dashboard JWTs,
           ext_authz → gateway /internal/authz for API keys, rate limits)
              │
              ▼
        FastAPI gateway (stateless, N replicas)
        ├── /v1/*        OpenAI-compatible proxy → OpenRouter (httpx, SSE pass-through)
        ├── /admin/*     control plane: tenants, users, keys, usage queries
        └── /internal/*  ext_authz endpoint, health
              │
   ┌──────────┴──────────┐
   ▼                     ▼
 PostgreSQL          Redis
 (tenants, users,    (rate-limit counters,
  keys hashed,        usage write-behind buffer)
  usage ledger)
              ▲
 Next.js dashboard (separate app) ──► /admin/* via Envoy
```

- **Cost tracking:** capture OpenRouter `usage` (and generation-cost lookup)
  per request; buffer in Redis stream; async worker flushes to the Postgres
  usage ledger (write-behind). Ledger is append-only.
- **Failure design (non-negotiable):** every outbound IO has explicit
  timeouts, bounded retries with jitter (idempotent ops only), circuit breaker
  on OpenRouter upstream, and a documented rollback path per migration.

## UI/UX

Admin dashboard: **Next.js 15+ (App Router) + shadcn/ui + Tremor charts +
TanStack Query**, dark-mode-first, WCAG 2.2 AA. Pages (MVP): login, tenant
setup, API keys, model catalog, usage & cost analytics. Lives in
`apps/dashboard/`.

## Key Decisions

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| D1 | Backend language | Python 3.12 + FastAPI | Proxy is I/O-bound; overhead negligible vs upstream LLM latency; richest AI ecosystem; fastest to ship. Re-evaluate hot path in Go only if a tenant exceeds ~50k concurrent streams. |
| D2 | Stage | MVP | Thin vertical slice, code kept. |
| D3 | Edge & auth | Envoy: `jwt_authn` (dashboard JWTs), `ext_authz` (API keys), rate limiting. Gateway issues JWTs; API keys hashed (argon2) at rest. | Enterprise pattern; auth enforcement off the app hot path. |
| D4 | Dashboard stack | Next.js + shadcn/ui + Tremor + TanStack Query | 2026 default for admin tools; what LiteLLM/Portkey/Helicone-class products converge on. |
| D5 | Upstream | OpenRouter only (MVP) | One integration buys 100+ models. |
| D6 | API surface | OpenAI-compatible `/v1/chat/completions` etc. | Zero-friction client migration; industry lingua franca. |
| D7 | Service topology | Single FastAPI service (proxy + control plane routers), stateless | One deployable for MVP; split data/control plane at Production stage if needed. |
| D8 | Persistence | PostgreSQL (SQLAlchemy 2 async + Alembic) + Redis | Boring, proven, multi-tenant via `tenant_id` scoping on every table. |
| D9 | Package management | `uv` + `pyproject.toml`; deps gated by `dependencies.allowlist` in CI | ADD requirement: pipeline rejects unknown packages. |

Full confidence-tagged rationale: `SETUP-REVIEW.md`.
