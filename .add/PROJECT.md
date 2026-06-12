# PROJECT — living documentation (cross-milestone context)

> The durable foundation that outlives every milestone and feeds context into each
> TDD⇄ADD loop. Read this FIRST in any session.

slug: ai-proxy · stage: production · updated: 2026-06-12 · foundation-version: 8
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
- Folded from v7 (2026-06-12):
  - Modality (chat·embedding·image·audio_stt·audio_tts) is a domain concept stored as
    TEXT with a `Literal` type alias, NOT a DB ENUM — the value set is bounded but may
    grow (e.g. "video"), and TEXT+Literal avoids `ALTER TYPE` migrations on each
    addition while keeping compile-time exhaustiveness. Provider (openrouter·openai) is
    catalog metadata, never client-specified — the provider-selection seam routes each
    modality to its direct provider by (modality, provider)

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
- Folded from v6 (2026-06-12):
  - Settled: served-model billing keys on the value the router RETURNS (the fallback
    router's 3-tuple `(status, body, served_model_id)`), never `response_body["model"]`
    — the upstream's body model string can drift from the catalog id (e.g. ":free"
    variants); the candidate id we routed to is the only authoritative billing signal
  - Settled: a frozen behavioral pin (e.g. "NEVER retry") is changed by the SUPERSESSION
    pattern — record the supersession at the new task's freeze, leave the frozen file
    untouched, and keep the new default behavior-preserving so the prior pin stays
    byte-identical until opted in
  - Settled: a distributed TTL-keyed state machine (Redis cooldown) gets an explicit
    state table in §3 — an in-process enum (CLOSED/OPEN/HALF_OPEN) has no direct
    multi-key Redis analogue; the half-open window needs its own marker key to be
    distinguishable from CLOSED (the defect a 5-row state table surfaced pre-build)
- Folded from v7 (2026-06-12):
  - Settled: non-chat billing is unit-dispatched by a `pricing_unit` discriminator
    (per_token·per_image·per_second·per_character) + `quantity` on the ledger; the bill
    fires exactly once per accepted request against the SERVED model (single-bill
    preserved per modality). Image quantity = entries upstream RETURNED (never
    requested-n — no over-bill on failed/empty)
  - Settled: STT duration source depends on the caller requesting `verbose_json`
    response_format — absent a duration field, cost is $0 with a WARN (never a guess);
    accurate per_second billing requires the explicit format
  - Settled: TTS bills at-start (per_character on len(input)) the moment a 200 is
    committed, BEFORE streaming bytes — customers are charged for stream failures after
    the 200, matching OpenAI's billing model (no post-stream reconciliation)
  - OPEN: the chat M11 soft-budget-alert (advisory, fire-and-forget) is DROPPED on the
    non-chat path — `NonChatGovernance` preserves the HARD 402 but omits the alert for
    embeddings/images/audio. Revisit: add a shared alert seam used by chat + non-chat,
    or accept the gap explicitly (3 inherited deltas this milestone)
  - OPEN: an empty-but-present upstream API key produces an opaque client-side 500 with
    no actionable message; the spec should require a boot-time guard that rejects a
    configured-yet-empty upstream key (evidence: the only v7 C5 failure mode)

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
| 2026-06-12 | Served-model billing keys on the router's returned candidate id (3-tuple 3rd element), never body["model"] (fold: SDD/model-fallbacks, corrected at fold) | OpenRouter body model string can drift from the catalog id (":free" variants); the routed candidate id is the only authoritative billing signal | folded v6 |
| 2026-06-12 | Frozen behavioral pins changed via SUPERSESSION (record at new freeze, frozen file untouched, default behavior-preserving) (fold: SDD/retry-policy) | "NEVER retry" superseded by a precise retryable set; default retries=0 keeps v5 byte-identical; JwksKeyCache precedent | folded v6 |
| 2026-06-12 | Distributed TTL-keyed state machines get an explicit §3 state table; the half-open window needs its own marker key (fold: SDD/cooldown-circuit) | an in-process enum has no multi-key Redis analogue; without a half marker CLOSED and HALF_OPEN are indistinguishable and healthy traffic throttles to ~1 req/TTL | folded v6 |
| 2026-06-12 | Full-jitter backoff timing asserted by monkeypatching BOTH random.uniform AND asyncio.sleep — capture the computed delay, never wall-clock (fold: TDD/retry-policy) | deterministic, fast timing assertions with zero real sleeps | folded v6 |
| 2026-06-12 | GREEN-BY-DESIGN tests (assert ABSENCE of behavior) are marked explicitly in the §4 plan (fold: TDD/model-fallbacks) | prevents red-phase confusion when an "absence" test is green before build (e.g. no stream fallback) | folded v6 |
| 2026-06-12 | Fake async Redis for concurrent SET-NX tests processes commands atomically and orders task yields explicitly (fold: TDD/cooldown-circuit) | asyncio.gather does not guarantee interleaving; the single-probe NX guarantee needs deterministic ordering in the fake | folded v6 |
| 2026-06-12 | risk=high tasks carry an explicit retryable-classification TABLE in §1 (fold: ADD/retry-policy) | the table format is load-bearing — it prevents ambiguous build-phase interpretation of which failures retry | folded v6 |
| 2026-06-12 | A [contract]-flag spec alone cannot resolve becomes a BUILD constraint with an acceptance criterion, not just a §3 flag (fold: ADD/cooldown-circuit) | the concurrent-probe race needs the TTL relationship (probe duration < probe TTL) enforced, not merely noted | folded v6 |
| 2026-06-12 | Parallel tasks sharing a protocol: the OWNING task defines + freezes the interface before the consuming task builds (fold: ADD/model-fallbacks) | ModelHealthGate owned by model-fallbacks, consumed by cooldown-circuit — frozen-first avoids divergent duck-typed copies | folded v6 |
| 2026-06-12 | Modality stored as TEXT + Literal alias (not DB ENUM); provider is catalog metadata, never client-specified (fold: DDD/provider-seam) | bounded-but-growable value set avoids ALTER TYPE per addition while keeping compile-time exhaustiveness; the seam routes (modality, provider) → direct provider | folded v7 |
| 2026-06-12 | Non-chat billing is unit-dispatched by a pricing_unit discriminator + quantity, billed once per accepted request against the served model; image quantity = entries upstream RETURNED (fold: SDD/pricing-units + images-endpoint) | per_token/per_image/per_second/per_character need distinct math; billing requested-n over-bills failed/empty responses | folded v7 |
| 2026-06-12 | STT per_second billing requires the caller request verbose_json; absent duration → $0 + WARN, never a guess (fold: SDD/audio-endpoints) | duration is only present in verbose_json; guessing would misbill | folded v7 |
| 2026-06-12 | TTS bills at-start (per_character on len(input)) the moment a 200 is committed, before streaming bytes (fold: SDD/audio-endpoints) | matches OpenAI's model — customers charged for post-200 stream failures; no post-stream reconciliation | folded v7 |
| 2026-06-12 | OPEN: chat M11 soft-budget-alert is dropped on the non-chat path (HARD 402 preserved); revisit a shared alert seam or accept the gap (fold: SDD/embeddings+images+audio, 3 inherited) | NonChatGovernance is a standalone re-impl of the chat checks; the advisory alert was not ported | folded v7 (open follow-up) |
| 2026-06-12 | OPEN: spec should require a boot-time guard rejecting a configured-yet-empty upstream key (fold: SDD/v7-live-verify) | an empty key yields a malformed Bearer header → opaque client-side 500; a startup guard converts it to a clear boot error | folded v7 (open follow-up) |
| 2026-06-12 | A "module stays byte-identical" invariant has no compile-time enforcement — it rests on a behavioral test + manual git diff of named INVIOLABLE files; downstream contracts spell out the forbidden import (fold: ADD/embeddings+provider-seam) | future: an ArchUnit-style import test; chat-untouched held this milestone via EM11 + git diff | folded v7 |
| 2026-06-12 | Billed-quantity / fallback policy is a BUSINESS decision surfaced as a [contract] flag at §3 top, resolved at freeze, never silently coded (fold: ADD/images-endpoint) | images dropped the `or requested-n` fallback at freeze to bill exactly len(data) | folded v7 |
| 2026-06-12 | Live-verify e2e closes self-contain upstream creds (non-secret placeholder) in the compose overlay, never operator shell env (fold: ADD/v7-live-verify) | empty `${VAR:-}` interpolation → malformed Bearer rejected by httpx/h11 before egress → opaque 500 (v7 C5); audit v4–v6 overlays | folded v7 |
