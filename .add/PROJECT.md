# PROJECT — living documentation (cross-milestone context)

> The durable foundation that outlives every milestone and feeds context into each
> TDD⇄ADD loop. Read this FIRST in any session.

slug: ai-proxy · stage: production · updated: 2026-06-13 · foundation-version: 12
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
- Folded from v8 (2026-06-12):
  - A **model group** (alias) is an ordered list of **Deployments** (model_id + optional
    weight/tpm_limit/rpm_limit), not bare model-id strings; a bare string coerces to a
    weight-1/no-limit Deployment (v6 back-compat). Three ORTHOGONAL per-deployment gates
    coexist, each a distinct domain concept with its own port: **cooldown** (UNHEALTHY,
    v6) · **load** (IN-FLIGHT/LATENCY, balance-strategies) · **limit** (SATURATED). Naming
    them as separate glossary terms keeps the router logic and the 429-saturated vs
    503-cooldown-exhausted distinction unambiguous; billing still keys on the SERVED
    deployment's catalog id (v6 invariant preserved across load-balanced selection)
- Folded from v9 (2026-06-13):
  - **Provider** graduates from non-chat-only metadata to a FIRST-CLASS routing
    dimension on EVERY modality including chat. Provider ∈ {openrouter (default),
    openai, anthropic, google}; the catalog model row's `provider` (TEXT — already
    present, NO migration) selects the upstream adapter. A **ChatTranslator** is the
    per-provider seam mapping an OpenAI chat-completions request ⇄ a provider-native
    request/response (+ SSE), DISTINCT from the v7 `UpstreamProvider` (transport-only,
    non-chat). One provider can span BOTH seams (Gemini: chat via the dispatch map +
    embeddings via the v7 registry) without touching either frozen seam.
  - INVARIANT (proven live, v9): adding a provider NEVER changes the default path —
    provider=openrouter/openai chat stays byte-identical; billing still keys on the
    SERVED model id with the provider's NATIVE usage tokens. Gemini embeddings are the
    one exception: no native token count → a documented `max(1, ceil(chars/4))`
    ESTIMATE (exact counting is an open follow-up)
- Folded from v10 (2026-06-13):
  - **Tool / function-call** enters the domain as a canonical (OpenAI) vocabulary EVERY
    provider maps to/from: a request MAY carry `tools` + `tool_choice`; a response MAY
    carry `message.tool_calls` (finish_reason "tool_calls"); a follow-up turn carries
    `role:"tool"` messages keyed by `tool_call_id`. **Tool-call-id synthesis** is a
    first-class concept for id-less native providers — the id is gateway-owned,
    name+index-derived (blake2b), secret-free. `function.arguments` is a JSON STRING on
    the OpenAI wire but a JSON OBJECT natively → translate with json dumps/loads AT the
    boundary, fail-safe (malformed args forward raw, never crash a translator)
  - Each provider's tool model has a distinct SHAPE the translator must respect:
    Anthropic is CONTENT-BLOCK-based (tool_use / tool_result blocks restructure the
    MESSAGE — assistant tool_calls → content blocks; a run of role:"tool" → ONE user
    turn of tool_result blocks), while Gemini is id-LESS (functionCall correlated back by
    NAME via an id→name map rebuilt from the assistant tool_calls echoed in the same
    request — id is for the OpenAI client, name is for Gemini). Same-name PARALLEL Gemini
    calls remain name-ambiguous on return (documented residual risk)
  - INVARIANT (proven live, v10): a request WITHOUT `tools` engages ZERO tool plumbing
    and is byte-identical to v9; OpenRouter/OpenAI tool requests stay byte-identical
    passthrough; tool-call tokens are counted by the provider's native usage (no separate
    tool billing), still keyed on the SERVED model id
- Folded from v11 (2026-06-13):
  - **response_format** enters the domain as a canonical (OpenAI) directive every provider
    maps to/from: `{type:"text"|"json_object"|"json_schema"}`, with `json_schema:{name,
    schema, strict?}`. The MODEL OUTPUT always comes back as `message.content` (a JSON
    STRING), NEVER a new response field. Two native mechanisms exist across providers:
    NATIVE structured-output (Gemini `generationConfig.responseMimeType` +
    `responseSchema`) vs **JSON-schema tool coercion** for a provider with NO native field
    (Anthropic) — emit one synthetic forced tool (`json_output`, a gateway-owned reserved
    name) whose `input_schema` IS the requested schema, then UNWRAP the returned tool_use
    block's `input` back into `message.content` (tool_use → content inversion). The
    coercion is gateway-owned and invisible to the caller (no tool_calls leak); `json_output`
    is the correlation key on every leg (request build, response unwrap, stream route)
  - response_format COMPOSES with v10 tools rather than conflicting: a request MAY carry
    BOTH a real `tools` list AND `response_format` — on Anthropic the coercion tool is
    APPENDED alongside caller tools (only the `json_output` block is unwrapped; caller tools
    still surface as `tool_calls`). The Anthropic json_schema path REUSES v10's
    Tool/ToolChoiceNamed + tool helpers wholesale rather than inventing a parallel mechanism
  - INVARIANT (proven live, v11): a request WITHOUT `response_format` (or `{type:"text"}`)
    engages ZERO json plumbing and is byte-identical to v10; OpenRouter/OpenAI response_format
    requests stay byte-identical passthrough; the gateway TRANSLATES the directive but does
    NOT validate/repair the model's output against the schema (translate-don't-enforce);
    billing still keys on the SERVED model id with native usage

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
    configured-yet-empty upstream key (evidence: the only v7 C5 failure mode — now
    DOUBLY evidenced: the v8 stack reproduced the identical "Illegal header value
    b'Bearer '" 500 on its first live run; still open as a gateway boot-guard)
- Folded from v8 (2026-06-12):
  - Settled: a fail-OPEN port returns a NEUTRAL value on error (in_flight=0 / ewma=0.0 /
    is_saturated=False) so the consumer degrades to a deterministic default (declared
    order) — an optimization/availability gate never becomes a correctness gate, no
    try/except past the port boundary
  - Settled: a new domain error reuses an EXISTING error-catalog spec (the router raises
    `AllDeploymentsSaturatedError`; one additive use-case except clause maps it to the
    existing RATE_LIMITED → 429) — no new status/code literal, the HTTP contract stays
    centralized and no API handler changes
  - Settled: extend a frozen config value as an ADDITIVE second view (model_groups
    `list[str]` → `list[Deployment]` PLUS a preserved string-view property), never a type
    change to the bound field — frozen exact-shape consumers (/admin/routing) keep reading
    the old view unchanged
  - Settled: a default strategy that returns its input unchanged (`OrderedStrategy →
    list(candidates)`) is the byte-identical-preservation lever — the entire v6 fallback
    loop is reused verbatim and frozen suites stay green with zero loop-body edits
  - Settled: a router that load-balances is only TRUSTWORTHY once distribution is
    OBSERVABLE at the edge (a per-deployment served-count readout), not just unit-asserted
    — the live close proved weighted-shuffle (dep-a:dep-b ≈ 8:32 then 13:27 over weight 1:3)
- Folded from v9 (2026-06-13):
  - Settled: the chat completion path is PROVIDER-AWARE via a dispatch wrapper
    (`ProviderAwareCompletionUpstream`) over a `dict[provider→adapter]` map, resolving
    the served model's provider through an in-memory `CatalogProviderResolver`
    (model_id→provider, refreshed at startup + on /internal/catalog/sync). Unknown/unset
    provider → fail-SAFE to the "openrouter" default adapter; the v8 router/billing path
    is UNCHANGED behind the wrapper
  - Settled: each non-OpenAI provider stream() MUST emit a TERMINAL OpenAI chunk carrying
    `usage:{prompt_tokens,completion_tokens,total_tokens}` before `data: [DONE]` — the
    frozen `extract_usage_from_sse` scans joined frames in reverse for the LAST usage
    frame, so translation correctness IS billing correctness on the stream path
  - Settled: a provider's wire translation is grounded in a VERBATIM SSE fixture shared
    between the adapter unit suite and the live stub (Anthropic 7/4, Gemini 9/6) — the
    stub bytes match the unit fixtures, so a green unit suite predicts a green live pass
  - OPEN: Anthropic + Gemini stream() both BUFFER the full upstream event list before
    translating (no incremental TTFB) — a streaming-hardening follow-up
  - OPEN: Gemini embeddings have no native token count → usage is ESTIMATED as
    `max(1, ceil(total_chars/4))`; exact counting (a tokenizer or count API) is a
    follow-up
- Folded from v10 (2026-06-13):
  - Settled: tool-use is DEPTH not breadth — tools landed as ADDITIVE branches in the
    SAME v9 per-provider helper triad (request/response/SSE) with ZERO adapter-class
    change; the v9 ChatTranslator seam absorbed a non-trivial richer request/response
    shape without a re-freeze. The freeze-first SHARED-SEAM pattern (freeze the canonical
    types + pure helpers + the passthrough/byte-identical pins FIRST, providers build
    against them) is now proven for a SHAPE change, not just a dispatch wrapper
  - Settled: provider tool-translation is a REPEATABLE 4-step shape (request
    tools/tool_choice + message restructure · response native-call→tool_calls · streaming
    native-event→delta fragment · no-tools byte-identical pin), proven twice
    (anthropic+gemini landed identically-shaped) — the next provider (Bedrock/Azure)
    follows the same template against the frozen contract
  - Settled: streaming tool-calls are ASYMMETRIC in granularity but UNIFORM at the OpenAI
    seam — Gemini emits one combined id+name+args fragment (whole functionCall in one
    part); Anthropic streams id+name then incremental input_json_delta fragments, needing
    an index REMAP (content-block index ≠ OpenAI tool_calls index, bridged by a
    block_to_tc dict) — both produced via the SAME build_tool_call_delta helper
  - OPEN (carried): parallel-tool-call streaming beyond fragment-per-index, JSON-mode /
    structured-outputs `response_format`, and same-name parallel Gemini call
    disambiguation remain follow-ups (Out of v10 scope)
- Folded from v11 (2026-06-13):
  - Settled: response_format is DEPTH on the v9 ChatTranslator seam (like v10 tools) AND
    COMPOSES with v10 — Gemini is REQUEST-SIDE ONLY (responseMimeType/responseSchema added
    to the existing generationConfig; the unchanged v9 response path already maps output to
    message.content — no response/SSE code), while Anthropic REUSES v10's tool-coercion
    seam (build_json_coercion_tool returns canonical v10 Tool/ToolChoiceNamed types). A new
    directive seam reused a prior seam's machinery wholesale rather than inventing a parallel
    mechanism
  - Settled: the frozen-contract extractor (extract_response_format) is the SHARED
    no-op/validation gate every provider reuses — a provider gets the byte-identical
    guarantee + the two rejections for free by calling it (Gemini: 1 import + 1 call
    delivered the whole request branch), rather than re-implementing the parse
  - Settled: the streaming coercion unwrap needs THREE coordinated touchpoints in one SSE
    pass (content_block_start MARKS the coercion block, input_json_delta ROUTES by that
    index to delta.content, message_delta OVERRIDES finish to "stop") bridged by a per-call
    state pair (coercion_block_index/saw_coercion); the same shape recurs for any provider
    that streams a coerced block. A live `_sse_has_tool_calls` guard makes the no-leak
    invariant OBSERVABLE, not just asserted-absent
  - OPEN (carried): `strict`-mode schema-subset rejection and parallel-tool +
    response_format co-existence remain unexercised; carried v9/v10 opens persist
    (incremental-SSE TTFB, exact Gemini-embed tokens, parallel-tool-call streaming,
    same-name Gemini disambiguation) plus the v7 non-chat soft-budget-alert + empty-key
    boot guard

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
| 2026-06-12 | A model group is an ordered list of Deployments (model_id+weight+tpm/rpm); three orthogonal per-deployment gates — cooldown(UNHEALTHY)/load(IN-FLIGHT/LATENCY)/limit(SATURATED) — each its own port; billing keys on the served deployment id (fold: DDD/deployment-limits) | distinct domain terms keep router logic + 429-vs-503 unambiguous; v6 billing invariant survives load-balancing | folded v8 |
| 2026-06-12 | Extend a frozen config value as an ADDITIVE second view + preserved old-view property, never a type change to the bound field (fold: SDD/deployment-model) | frozen exact-shape consumers (/admin/routing) keep reading the old view; 63/63 v6 regression green | folded v8 |
| 2026-06-12 | A new domain error reuses an EXISTING error-catalog spec via one additive use-case except clause (AllDeploymentsSaturatedError→RATE_LIMITED/429) (fold: SDD/deployment-limits) | no new status/code literal; HTTP contract stays centralized; no API handler change | folded v8 |
| 2026-06-12 | A fail-OPEN port returns a NEUTRAL value on error (in_flight=0/ewma=0/is_saturated=False) so the consumer degrades to a deterministic default (fold: SDD/balance-strategies) | optimization/availability gate never becomes a correctness gate; no try/except past the port boundary | folded v8 |
| 2026-06-12 | Frozen behavioral pin → supersession works ADDITIVELY: add an OPTIONAL async capability (aorder) selected via isinstance at the call site; frozen sync seam (order()) untouched (fold: ADD/routing-strategy+balance-strategies) | frozen tests keep calling the sync seam → zero re-freeze; the reusable recipe for evolving any frozen Protocol | folded v8 |
| 2026-06-12 | Cross-cutting candidate constraints filter UPSTREAM of the routing strategy (saturation skip) (fold: ADD/deployment-limits) | composes with EVERY strategy + the v6 loop untouched — the strategy only ever sees survivors | folded v8 |
| 2026-06-12 | A load-balancing router is only trustworthy once distribution is OBSERVABLE at the edge (per-deployment served-count readout), and a cooldown live check asserts the AUTHORITATIVE gate state (/admin/routing snapshot_state), not stub-counter inference (fold: SDD+ADD/v8-live-verify) | weighted-shuffle proven 8:32/13:27 over weight 1:3; stub-counter cooldown flaked under shuffle+retries, /admin/routing poll passed 29/29 ×2 | folded v8 |
| 2026-06-12 | A live harness firing bursts must pace under the edge rate limit (Envoy local_ratelimit 50 req/s global); a statistical check needs volume → it needs pacing (fold: TDD/v8-live-verify) | C1's 40-req sample + C5's trip loop drained the bucket → 429 local_rate_limited on a following /admin/keys; 50ms/req + settle fixed it | folded v8 |
| 2026-06-13 | Provider is a first-class chat routing dimension: ProviderAwareCompletionUpstream dispatches by the served model's catalog provider to a per-provider ChatTranslator; unknown/unset → fail-safe openrouter; v8 router/billing untouched behind the wrapper (fold: DDD+SDD/provider-chat-dispatch) | makes the OpenRouter-hardwired chat path provider-aware additively; default path stays byte-identical (C7 5/3/8 + 628-unit suite green) | folded v9 |
| 2026-06-13 | Raw httpx per provider over vendor SDKs (Anthropic/Google), one shared resilience contract (CircuitBreaker/timeout/UpstreamUnavailableError/v8-fallback) (fold: ADD/anthropic-provider+gemini-provider, user-confirmed) | matches LiteLLM's own hand-rolled llms/anthropic httpx; avoids per-provider SDK dependency sprawl + divergent resilience seams; Tin chose "Keep raw httpx" at the SDK fork | folded v9 |
| 2026-06-13 | Every non-OpenAI stream() emits a TERMINAL OpenAI usage chunk before [DONE]; wire translation grounded in a VERBATIM SSE fixture shared by the adapter unit suite + the live stub (fold: SDD+TDD/anthropic-provider+gemini-provider) | the frozen extract_usage_from_sse reads the LAST usage frame → translation IS billing on the stream path; matching stub/unit bytes make a green unit suite predict the live pass | folded v9 |
| 2026-06-13 | A new in-memory resolver map (model_id→provider) refreshes at lifespan startup + on /internal/catalog/sync; the live harness SEEDS rows then RESTARTS the gateway so refresh() reads them (fold: ADD/provider-chat-dispatch+v9-live-verify) | the freeze's least-sure flag (seed-then-restart refreshes the resolver) confirmed first-try; no source-sync = no deactivation; double-pass 35/35 ×2 | folded v9 |
| 2026-06-13 | Gemini embeddings usage is ESTIMATED max(1, ceil(total_chars/4)) — no native token count (fold: SDD/gemini-provider, open follow-up); Anthropic+Gemini stream() BUFFER full event list before translating (open TTFB follow-up) | embedContent/batchEmbedContents return no usageMetadata token counts; documented estimate billed, exact counting deferred | folded v9 (open follow-up) |
| 2026-06-13 | Tool/function-call is a canonical (OpenAI) vocabulary every provider maps to/from; tool-call-id SYNTHESIS (blake2b name+index, secret-free) is first-class for id-less providers (Gemini), correlated back by NAME; arguments are a JSON string on the wire / object natively, translated fail-safe at the boundary (fold: DDD/tool-use-contract) | one canonical shape keeps the /v1 surface uniform; Gemini's id-less functionCall round-trips via an id→name map rebuilt from echoed assistant tool_calls; 16/16 contract green | folded v10 |
| 2026-06-13 | Tool-use is DEPTH not breadth — tools landed as ADDITIVE branches in the SAME v9 per-provider helper triad (request/response/SSE), zero adapter-class change, no re-freeze; the freeze-first SHARED-SEAM pattern now proven for a SHAPE change (fold: SDD/anthropic-tool-use+gemini-tool-use) | the v9 ChatTranslator seam absorbed a non-trivial richer request/response shape; provider tool-translation is a repeatable 4-step template for the next provider | folded v10 |
| 2026-06-13 | Streaming tool-calls are ASYMMETRIC in granularity (Gemini one combined fragment; Anthropic id+name then incremental input_json_delta needing a content-block→tool_calls index REMAP) but UNIFORM at the OpenAI seam via one build_tool_call_delta helper (fold: ADD/anthropic-tool-use+gemini-tool-use) | a frozen streaming-fragment shape absorbed both providers without change; the block_to_tc index remap recurs for any provider interleaving text+tool events | folded v10 |
| 2026-06-13 | A multi-turn protocol is proven LIVE by a single STATELESS request-inspection stub (turn discriminated by the presence of a translated tool result), no server-side turn state (fold: ADD+TDD/tool-use-live-verify) | the freeze least-sure flag validated 18/18 ×2, both passes exit 0, no turn-state bug; operator-run live checks served as the red→green suite for cross-provider translation | folded v10 |
| 2026-06-13 | The request-side passthrough assumption was VERIFIED IN CODE before freezing (router.py:42 forwards a raw dict, so tools/tool_choice flow unstripped) — the contract pins a real invariant, not a hoped-for one (fold: ADD/tool-use-contract) | a Pydantic ChatRequest model would strip tools and break passthrough; §1 framing rejected that option on this verified ground; no-tools byte-identical to v9 | folded v10 |
| 2026-06-13 | response_format is a canonical OpenAI directive (text/json_object/json_schema) with TWO native mechanisms — native (Gemini responseMimeType/responseSchema) vs JSON-schema tool COERCION (Anthropic, no native field): a synthetic forced `json_output` tool UNWRAPPED back into message.content (tool_use→content inversion), no tool_calls leak (fold: DDD/response-format-contract+anthropic-json-mode) | the model output always returns as a message.content JSON string, never a new field; the gateway-owned json_output name is the correlation key on every leg; 15+10 unit green + LIVE C1/C2 no-leak | folded v11 |
| 2026-06-13 | response_format COMPOSES with v10 tools (coercion tool APPENDED alongside caller tools, only json_output unwrapped) and REUSES v10's Tool/ToolChoiceNamed + helpers — a new directive seam reused a prior seam's machinery wholesale (fold: SDD/anthropic-json-mode+response-format-contract) | freeze-first SHARED-SEAM pattern repeats a THIRD time and this time COMPOSES; caller tools still surface as tool_calls (composition test green) | folded v11 |
| 2026-06-13 | response_format on a native-field provider (Gemini) is REQUEST-SIDE ONLY (responseMimeType/responseSchema on the existing generationConfig; unchanged v9 response path maps output to message.content); the shared extract_response_format gate delivers byte-identical + the two rejections for free (fold: SDD+ADD/gemini-json-mode) | gemini-json-mode touched only _openai_to_gemini_request; 1 import + 1 call delivered the whole request branch; 38/38 gemini suites green | folded v11 |
| 2026-06-13 | The streaming coercion unwrap needs THREE coordinated SSE touchpoints (mark block / route input_json_delta to delta.content / override finish to stop) bridged by per-call state; a live `_sse_has_tool_calls` guard makes the no-leak invariant OBSERVABLE (fold: ADD+TDD/anthropic-json-mode+json-mode-live-verify) | the same shape recurs for any provider streaming a coerced block; double-pass 13/13 ×2 both exit 0, port :9925 seed-then-restart first-try | folded v11 |
| 2026-06-13 | A contract task proves passthrough/byte-identical pins GREEN-BY-DESIGN against UNCHANGED dispatch code with a spy adapter, and verifies the raw-dict passthrough invariant IN CODE before freezing (router.py:42) (fold: ADD/response-format-contract) | 3/15 tests guard response_format-unstripped + openrouter-verbatim + no-rf-byte-identical and pass before any provider build; reused the v10 _SpyAdapter/_ScriptedResolver pattern verbatim | folded v11 |
| 2026-06-13 | OPEN: the full `-m 'not e2e'` suite is NON-DETERMINISTIC against the shared dev Postgres (FK-violation flake, 16/34/44 varying); the trustworthy per-change gate is the no-DB blast-radius run (translation+dispatch suites) — the foundation needs per-test DB isolation OR a documented make test-fast (fold: TDD/response-format-contract, recurring v8) | each failing suite passes IN ISOLATION; a zero-blast-radius pure-module change still showed 16/34/44 variance; deterministic no-DB run was 83/83 | folded v11 (open follow-up) |
