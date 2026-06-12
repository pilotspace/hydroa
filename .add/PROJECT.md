# PROJECT — living documentation (cross-milestone context)

> The durable foundation that outlives every milestone and feeds context into each
> TDD⇄ADD loop. Read this FIRST in any session.

slug: ai-proxy · stage: production · updated: 2026-06-12 · foundation-version: 6
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
- Folded from v1 (2026-06-10):
  - AMENDMENT to the argon2 invariant above: API key secrets are stored as **SHA-256**
    hashes (high-entropy secrets don't need a slow KDF and argon2 broke hot-path authz
    latency); argon2 remains for user passwords. GLOSSARY amended at the api-keys freeze —
    the freeze flag caught this spec/GLOSSARY conflict before any code existed
  - Domain ports are `typing.Protocol`s with fakes injected via `app.state` — decouples
    every test from real HTTP/IO (model-catalog: 15 tests, zero network calls)

## Spec / Living Document (SDD) — what we are building, now

- Active milestone → none yet (see `add.py status`); first slice: tenant signup →
  API key → proxied completion (streaming + non) → usage row → budget enforcement
- Frozen contracts: none yet
- Settled vs still open: architecture + commercial model settled (see Key Decisions);
  open: OpenRouter streaming-usage/cost reconciliation semantics — verify against
  live API inside the cost-metering task (raw payloads kept, so recomputable)
- Folded from v1 (2026-06-10):
  - Settled: generate row ids explicitly at the call site (`uuid7()` at construction,
    passed down) — SQLAlchemy column defaults apply at flush, so reading `.id` before
    flush is the "child row with unset parent id" bug class
  - Settled: multi-row sync operations (upsert + snapshot + deactivate) live in ONE
    transaction — zero rows written on source failure, idempotent on unchanged input

## Users (UDD) — UI/UX: design before code

- Primary users & jobs: tenant **owner/admin** — provision org, issue/revoke keys,
  watch spend; tenant **developer** — call OpenAI-compatible API with a key;
  **platform operator** — margin and platform health
- Core user flows: signup → tenant created → login (JWT) → create key (shown once)
  → call `/v1/chat/completions` → usage & cost visible on dashboard; alternative:
  budget exhausted → request rejected `ERR_BUDGET_EXCEEDED` → owner raises budget
- UI states every screen handles: loading · empty · error · success
- Folded from v1 (2026-06-10):
  - Security/UX tradeoffs (e.g. localStorage JWT and its XSS exposure) are surfaced in
    the spec ⚠ assumptions and the freeze flag — never hidden in code; the production
    upgrade path (httpOnly-cookie BFF) is named in the contract that accepts the risk
  - Scenario observables must name WHERE text/state appears (which section/component),
    not just that it appears — an unanchored observable was satisfiable by a different
    element's text (dashboard-usage catalog-row divergence, caught only at manual review)
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
| 2026-06-10 | API key secrets hashed with SHA-256; argon2 only for passwords (fold: DDD/api-keys) | high-entropy secrets need no slow KDF; argon2 broke authz hot-path latency | folded v1 |
| 2026-06-10 | Ports as typing.Protocol + fakes via app.state (fold: DDD/model-catalog) | zero-network tests; adapters swappable at composition root | folded v1 |
| 2026-06-10 | Row ids generated explicitly at call site, never read from pre-flush defaults (fold: SDD/api-keys) | column defaults apply at flush; prevents unset-parent-id bug class | folded v1 |
| 2026-06-10 | Multi-row sync = single transaction (fold: SDD/model-catalog) | zero rows on failure; idempotent re-sync; safe boundary for append-only ledger | folded v1 |
| 2026-06-10 | Security/UX tradeoffs surfaced in spec ⚠ + freeze flag, production path named (fold: UDD/dashboard-shell) | localStorage-JWT XSS risk accepted explicitly, not buried in code | folded v1 |
| 2026-06-10 | Scenario observables must anchor WHERE text appears (fold: UDD/dashboard-usage) | unanchored observable was satisfied by the wrong element; caught only by manual review | folded v1 |
| 2026-06-10 | Byte-identical failure responses across all authz failure modes, test-enforced (fold: TDD/api-keys) | prevents content-length/timing oracles enumerating valid keys | folded v1 |
| 2026-06-10 | UI red suites scope RTL assertions with within(section) (fold: TDD/dashboard-usage) | bare getByText over-constrained the build into a fetch waterfall and a hidden field | folded v1 |
| 2026-06-10 | Red confirmed for the RIGHT reason before any build line (fold: TDD/model-catalog) | red-for-wrong-reason tests (budgets create_token bug) invalidate the gate | folded v1 |
| 2026-06-10 | Freeze flag ritual checks cross-artifact consistency (spec vs GLOSSARY) (fold: ADD/api-keys) | caught the argon2/SHA-256 conflict before code existed | folded v1 |
| 2026-06-10 | Node deps are NOT governed by dependencies.allowlist — lockfile + orchestrator review until the allowlist format is extended (fold: ADD/dashboard-shell) | Python-only gate today; gap documented rather than implied | folded v1 |
| 2026-06-10 | Lint conflicts with frozen tests resolved via pyproject per-file-ignores/exclusions, never test edits (fold: ADD/model-catalog) | preserves test-immutability contract while keeping CI strict for src | folded v1 |
| 2026-06-11 | External stream protocols pinned by VERBATIM live-captured fixtures, not assumed shapes (fold: SDD/live-upstream-smoke) | mock-shaped fixtures passed while live billing recorded 0/0 vs upstream 24/73 | folded v2 |
| 2026-06-11 | Contextvars that must survive into middleware logging require pure-ASGI middleware, never BaseHTTPMiddleware (fold: SDD/observability) | BaseHTTPMiddleware runs handlers in a child task; bindings are lost at log emission | folded v2 |
| 2026-06-11 | Freeze review checks that every exit-criterion rate is expressible from contracted labels (fold: ADD/observability) | status_class aggregate could not express the required 402 rate; caught pre-freeze | folded v2 |
| 2026-06-11 | Runbook-prescribed config is enforced in the artifact it describes, not just documented (fold: ADD/ops-hardening) | stop_grace_period advice now lives in docker-compose.prod.yml itself | folded v2 |
| 2026-06-11 | New admin dashboard surfaces reuse the BFF catch-all /api/gw/[...path] proxy — no per-endpoint route handlers (fold: ADD/dashboard-govern) | held for a 2nd milestone: four new gateway endpoints needed zero new route handlers; the cookie->Bearer gate lives in exactly one place | folded v3 |
| 2026-06-11 | Per-key vs tenant budget field names pinned in GLOSSARY with contrast note; body-capture tests assert field names verbatim (fold: UDD/dashboard-govern) | monthly_budget_usd (per-key) vs budget_usd_monthly (tenant) — a silent mismatch saves nothing and passes mocked tests | folded v3 |
| 2026-06-11 | Additive kwargs on a frozen port use a typed capability seam: `TypedDict(total=False)` extras + implementation-declared `supported_extras: frozenset` — SUPERSEDES the v3 hasattr seam (fold: SDD/response-caching, user-mandated) | runtime reflection (inspect.signature/hasattr) hides the contract; an explicit declaration is reviewable and type-checkable; v1-Protocol fakes lacking the attr get base kwargs only | folded v4 |
| 2026-06-11 | Security decisions that skip an "expected" control are SPEC-SANCTIONED only via a primary-spec citation + pinned preconditions in §3 (fold: ADD/sso-oidc) | OIDC Core 1.0 §3.1.3.7(6) sanctions TLS-channel ID-token validation; preconditions (server-side-only receipt, verify never disabled, trusted endpoint URLs, nonce + full claim checks) make the sanction auditable and falsifiable | folded v4 |
| 2026-06-11 | Milestone close requires LIVE end-to-end verification through the real edge — frozen suites are necessary, not sufficient (fold: TDD/guardrails-core) | live v4 run found a real defect 326 green tests missed: pii_masked marker never recorded on the non-blocking mask path; suites assert behavior they were written to see | folded v4 |
| 2026-06-11 | Test fixtures that enable feature flags live in the OWNING suite's conftest, never repo-root autouse bridges (fold: ADD/obs-callbacks) | a root-level autouse fixture silently switches features on for every future suite; containment keeps the blast radius reviewable | folded v4 |
| 2026-06-12 | Rename/branding tasks contract as a FILE-BY-FILE rename table + explicit wire compat-pin list — the §3 API-shape schema is replaced, not force-fitted (fold: ADD/rename-hydroa) | a rename has no API shape; what needs freezing is exactly which identifiers change and which are wire-frozen | folded v5 |
| 2026-06-12 | Every app.state test seam gets a PAIRED production-wiring regression test asserting the default (no-seam) construction (fold: TDD/oidc-tenant-config live defects) | two per-tenant-OIDC paths were production-dead while every frozen test passed — fakes injected at the seams bypassed exactly the broken constructions | folded v5 |
| 2026-06-12 | Milestone-close LIVE edge verification is load-bearing and stays binding at every close — never waived for an all-gates-green milestone (fold: ADD/v5-close evidence) | the v5 live pass caught two production defects (exchanger + resolver wiring) invisible to 399 green tests | folded v5 |
| 2026-06-12 | Pure-file/grep regression suites (no DB/network) pin rename/branding invariants and wire compat literals (fold: TDD/rename-hydroa) | they catch reverts and merge accidents in milliseconds, before integration suites even start | folded v5 |
| 2026-06-12 | Next.js "use client" root layouts cannot export metadata — surface the constraint at §1 spec time and place metadata in the nearest server components (fold: SDD/rename-hydroa) | discovering the constraint at build time forces unplanned mechanism choices | folded v5 |
