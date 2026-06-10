# PROJECT — living documentation (cross-milestone context)

> The durable foundation that outlives every milestone and feeds context into each
> TDD⇄ADD loop. Read this FIRST in any session.

slug: ai-proxy · stage: mvp · updated: 2026-06-10
goal: a user can set up their tenant → log in → call any LLM model through the proxy → see accurate, billable cost tracking

---

## Domain (DDD) — the language and the boundaries

- Core concepts: Tenant · User · API key · Proxy request · Model catalog ·
  Pricing snapshot · Usage record · Budget · Markup (full list: `GLOSSARY.md`)
- Bounded contexts / modules: `proxy/` (OpenAI-compatible data plane → OpenRouter),
  `tenants/` (signup, users, JWT), `auth/` (API keys, ext_authz), `usage/`
  (metering, ledger, budgets), `core/` (config, db, errors)
- Invariants that must always hold:
  - Every tenant-owned row carries `tenant_id`; every query is tenant-scoped
  - Usage ledger is append-only; raw upstream payload stored so cost is always recomputable
  - Every proxied request produces exactly one usage record
  - API keys exist only as argon2 hashes at rest; plaintext shown once at creation
  - No outbound IO without timeout + bounded retry (idempotent only) + circuit breaker on OpenRouter

## Spec / Living Document (SDD) — what we are building, now

- Active milestone → none yet (see `add.py status`); first slice: tenant signup →
  API key → proxied completion (streaming + non) → usage row → budget enforcement
- Frozen contracts: none yet
- Settled vs still open: architecture + commercial model settled (see Key Decisions);
  open: OpenRouter streaming-usage/cost reconciliation semantics — verify against
  live API inside the cost-metering task (raw payloads kept, so recomputable)

## Users (UDD) — UI/UX: design before code

- Primary users & jobs: tenant **owner/admin** — provision org, issue/revoke keys,
  watch spend; tenant **developer** — call OpenAI-compatible API with a key;
  **platform operator** — margin and platform health
- Core user flows: signup → tenant created → login (JWT) → create key (shown once)
  → call `/v1/chat/completions` → usage & cost visible on dashboard; alternative:
  budget exhausted → request rejected `ERR_BUDGET_EXCEEDED` → owner raises budget
- UI states every screen handles: loading · empty · error · success
- Design source of truth → `apps/dashboard/` (Next.js 15 + shadcn/ui + Tremor +
  TanStack Query, dark-mode-first, WCAG 2.2 AA); DESIGN.md per feature

## Architecture (settled at setup)

Envoy edge (TLS, jwt_authn for dashboard JWTs, ext_authz → gateway
`/internal/authz` for API keys, rate limits) → stateless FastAPI gateway
(`apps/gateway`: `/v1/*` proxy via httpx SSE pass-through, `/admin/*` control
plane, `/internal/*`) → PostgreSQL (tenants/users/keys/ledger) + Redis
(rate-limit counters, spend counters, usage write-behind buffer).

## Key Decisions (append-only)
| date | decision | why | outcome |
|------|----------|-----|---------|
| 2026-06-10 | Python 3.12 + FastAPI gateway | I/O-bound SSE workload; proxy overhead negligible vs upstream latency; richest AI ecosystem; user choice after tradeoff analysis. Escape hatch: Go data plane only if per-node concurrency becomes the bottleneck | locked |
| 2026-06-10 | MVP stage, code kept | thin vertical slice of the single goal | locked |
| 2026-06-10 | Envoy enforces auth at edge (jwt_authn + ext_authz); gateway issues JWTs, owns key hashes | enterprise pattern, auth off the app hot path; human confirmed interpretation | locked |
| 2026-06-10 | Dashboard: Next.js + shadcn/ui + Tremor + TanStack Query | 2026 default for admin/analytics tools (web research) | locked |
| 2026-06-10 | OpenRouter as sole upstream; OpenAI-compatible `/v1` surface | one integration buys 100+ models; industry lingua franca | locked |
| 2026-06-10 | Commercial model: platform OpenRouter key + **per-tenant markup** | platform pays upstream, bills tenants; ledger is billing source of truth; deals differ per customer | locked |
| 2026-06-10 | Self-serve signup (email+password creates tenant + owner atomically) | matches goal "user can setup their tenant" | locked |
| 2026-06-10 | Budget enforcement in slice 1: near-real-time (Redis spend counter, pre-flight check), small overage from in-flight streams acceptable | industry standard (LiteLLM-equivalent); hard-cap escrow deferred | locked |
| 2026-06-10 | All catalog models available to every tenant (per-tenant allowlists later) | matches goal "use any llm model" | locked |
| 2026-06-10 | PostgreSQL (SQLAlchemy 2 async, Alembic) + Redis; `uv` + allowlist-gated deps | boring, proven; ADD supply-chain gate | locked |
