# PROJECT — living documentation (cross-milestone context)

> The durable foundation that outlives every milestone and feeds context into each
> TDD⇄ADD loop. Read this FIRST in any session.

slug: ai-proxy · stage: production · updated: 2026-06-18 · foundation-version: 53
goal: a user can set up their tenant → log in → call any LLM model through the proxy → see accurate, billable cost tracking

---

## Domain (DDD) — the language and the boundaries
- (DDD) a guardrail check that performs real outbound IO needs a THIRD verdict state (`unchecked`) beyond the deterministic checks' `passed`/`blocked`/`audited` vocabulary, plus its own config axis (`failure_mode`, orthogonal to `mode`) — the first guardrail in this codebase with an external failure mode of its own (evidence: §1 M6, Glossary delta "Unchecked").  [folded foundation-version 50 · from ml-moderation-layer]
- (DDD) a milestone's "the sibling task freezes that hook here" cross-reference can point at a task that is itself still ungrounded — a design agent must verify the CURRENT state of a cited dependency rather than trusting the milestone prose, and record a port/contract the other side can consume later instead of assuming the hook already exists (evidence: `payload-capture-store/TASK.md` read in full — still the blank template).  [folded foundation-version 50 · from tenant-retention-zdr]
- (DDD) Gemini Live re-bills the FULL cumulative context every turn (growing per-turn promptTokenCount is real spend, not a double-count bug) — fold into PROJECT.md billing-precision notes so a future engineer doesn't "fix" it. (evidence: live forum + docs re-verified at VERIFY)  [folded foundation-version 49 · from realtime-relay-governance]
- (DDD) A `Permission`-shaped RBAC gate cannot express "excludes tenant OWNER" under this matrix's own completeness guard (`ROLE_PERMISSIONS[Role.OWNER] == frozenset(Permission)`) — any genuinely operator-wide (non-tenant-scoped) resource needs a role-only gate (`require_superadmin` or equivalent), never a new `Permission` enum member, no matter how the feature request is worded ("a dedicated permission"). Worth stating explicitly in CONVENTIONS.md or authz.py's own docstring so a future task doesn't attempt the structurally-impossible path this draft ruled out (evidence: §1 Framings weighed Part B).  [folded foundation-version 49 · from signup-and-routing-authz]
- (DDD) "pass-through" is not capability-neutral: the OpenAI-compatible wire hides that providers DROP or 400 on params. Research-before-build (verify the seam against the live provider APIs + the gateway's real translation) caught a misleading-no-op UX before it shipped (evidence: Tin's "research first" instruction → the provider-variance findings → v2 gating change-request).  [folded foundation-version 40 · from chat-parameters-panel]
- (DDD) Environment assumptions decay: "kindnet ignores NetworkPolicy" was true once, false in kind v0.32/k8s v1.36 — assumptions about external tooling behavior must be RE-VALIDATED live each milestone, not carried forward (evidence: NP enforcement broke the edge despite the documented assumption).  [folded foundation-version 39 · from kind-bootstrap]
- (DDD) "audit event" is a distinct bounded concept from "alert event" (actor-attributed + immutable + compliance vs operational + deliverable + dedup'd) — separate module/table was correct (evidence: reuse-alert_events framing rejected at specify).  [folded foundation-version 35 · from audit-log-store]
- (DDD) "retention/purge" is an operator-wide lifecycle policy distinct from tenant-scoped CRUD — modelled as a periodic application sweeper, not an API (evidence: on-demand endpoint deferred).  [folded foundation-version 35 · from data-retention-controls]
- (DDD) role assignment (privilege grant) is a security surface distinct from team membership — separate endpoint + escalation guard (evidence: teams role is lead/member).  [folded foundation-version 35 · from rbac-admin-ui]
- (DDD) "public health summary" is a new domain concept (a non-authed, coarse, cache-friendly view distinct from the gated /admin health) — name it before building the live wiring.  [folded foundation-version 35 · from trust-status-page]

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
- Folded from v12 (2026-06-13) — billing accuracy + ops hardening:
  - **exact token billing** is the domain rule: every modality bills on a REAL count, an
    estimate is a documented last-resort fallback, never the default. Gemini embeddings carry
    NO inline usage → recover the count via a SEPARATE provider endpoint on the same adapter
    (`:countTokens`), made FAIL-SAFE (None → ceil(chars/4) fallback) so billing accuracy is
    never an availability gate (the count leg NEVER trips the embed circuit breaker; the
    embedding is the product). Billing still keys on the SERVED model id.
  - **boot guard** (fail-fast at the composition root): a configured-yet-EMPTY upstream key
    (`GATEWAY_*_API_KEY=""`) is a MISCONFIGURATION, not a disabled provider — `create_app`
    raises `EmptyUpstreamKeyError` before any adapter, converting the opaque per-request
    "Bearer ''" 500 (seen live v7+v8) into a clear startup error. ABSENT key = provider
    disabled (allowed). Some misconfigs are observable ONLY at the raw `os.environ` level —
    Settings collapses unset and set-empty to `""`, so the guard reads the environment directly.
  - **soft-budget alerts are UNIFORM across chat and non-chat**: an embeddings/images/audio
    request crossing a key's `soft_budget_usd` writes the SAME `soft_budget_exceeded`
    alert_event (shared `persist_soft_budget_alert` + `alert_events`, dedupe_key
    `soft_budget:{key_id}:{YYYYMM}`, fire-and-forget, idempotent) as chat. Advisory only — the
    HARD 402 path is byte-identical. Proven live (C2: one row, idempotent on repeat, 200).
- Folded from v15 (2026-06-14) — dashboard feature-coverage:
  - **additive identity-resolution** (email→user_id) belongs in the REPOSITORY, inside the
    existing transaction, tenant-scoped, with defense-in-depth: the resolve filter AND the
    team-membership check each enforce isolation independently, so a cross-tenant email
    returns 404 even if either guard alone were removed (evidence: teams add-by-email CR).

## Spec / Living Document (SDD) — what we are building, now
- (SDD) Composing new behavior OUTSIDE a frozen fail-closed seam (a wrapper that catches the seam's own exception) let a hard "NEVER fallback" invariant be superseded for ONE caller without editing the frozen contract or weakening it for every other caller — a reusable pattern for "add an escape hatch to a fail-closed gate." (evidence: _resolve_platform_fallback composes over resolve() untouched)  [folded foundation-version 53 · from platform-credential-fallback]
- (SDD) a BYOK provider used for an ANCILLARY IO seam (moderation) needs an ISOLATED CircuitBreaker/client instance from the SAME provider's PRIMARY seam (chat completions) — sharing one adapter instance across two independent failure domains would cross-contaminate breaker state; worth a general pattern note for any future secondary use of an existing provider adapter (evidence: §0 R3, §1 M8).  [folded foundation-version 50 · from ml-moderation-layer]
- (SDD) a new self-service WRITE API silently widens the blast radius of PRE-EXISTING read-time semantics elsewhere (recovery re-resolve; env.py autogen); a build's grounding should scan "what does making X mutable newly expose?" not just "does X compute right" (evidence: C1 + C2 both pre-existing, both newly reachable via this task).  [folded foundation-version 49 · from tiered-rate-cards]
- (SDD) A "bound EVERY X call" Must must be verified against EACH call in the path, not the one the pseudocode illustrates — first build wrapped only XADD and left the three advisory `incrbyfloat` awaits bare, partially missing the frozen §1 B4-timeout Must; caught by the pre-gate advisor review, not by tests (no test asserted the advisory-call timeout). Evidence: recorder.py advisory block.  [folded foundation-version 49 · from usage-flusher-durability]
- (SDD) a §3 CONTRACT's illustrative Python is not automatically valid Python — this task's own Part C snippet had a required param placed after already-defaulted ones with no `*` separator (a straightforward syntax error), and a false "already imported" note for `Role`. Neither was semantic (no Must/Reject changed), both were still worth a real syntax/ import sanity pass before freezing a contract's code block, not just a shape/decision review (evidence: §5 "Strategy actually used" (2)-(3); mirrors `superadmin-login`'s own SDD delta about a prose path string drifting from its §0 anchor — the same underlying lesson, contract prose/code needs the same rigor as contract decisions).  [folded foundation-version 44 · from superadmin-audit-foundation]
- (SDD) a contract-widening pass (adding Part C mid-freeze-cycle) can update one section's rule (§1 Must) for consistency across ALL affected parts, while leaving a SIBLING section's illustrative text (§2 scenario prose, §3 Python) stale for the part NOT being actively rebuilt in that pass — a distinct failure mode from deviation (2)-(3)'s single-snippet syntax error above: this is cross-section drift within one freeze, invisible unless §1 is read against §2/§3 side-by-side for every part, not just the part being changed. Found here: §1's Must for Part B silently gained an `auth_method` field when Part C was added, but §2/§3's own Part-B text did not (evidence: §5 "Strategy actually used" (6)). Suggests a freeze-time checklist item: when widening a contract for one part, diff-check whether the widening implies a change to any OTHER already-drafted part's text too, not just the part being added.  [folded foundation-version 44 · from superadmin-audit-foundation]
- (SDD) a frozen §2/§3's prose path string (`/admin/auth/oidc-config`) drifted from the real mounted route (`/admin/oidc`, `oidc_admin_router.py:45`) even though the §0 GROUND anchor citing the exact file:line was correct throughout — the concrete anchor should be treated as more authoritative than a restated path string when drafting contract prose, and ideally the restated string should be generated FROM the anchor, not typed independently (evidence: this task's build agent caught it by cross-checking against `rbac_roles/test_rbac_roles.py`'s existing real-route usage, not by the contract text alone).  [folded foundation-version 44 · from superadmin-login]
- (SDD) raw SQLAlchemy UPDATE does not trigger ORM onupdate hooks — workaround: always supply updated_at=now() explicitly in VALUES (evidence: rename_title implementation; mirrors append_message lesson from v40)  [folded foundation-version 40 · from chat-conversation-mgmt]
- (SDD) A frozen default can collide with a SIBLING task's frozen invariant — task-2's create=true default broke task-1's "no populated Secret by default"; caught at tests phase, fixed via CR-1 (evidence: the secure-by-default flip mirroring gateway jwtSecret).  [folded foundation-version 39 · from datastore-statefulsets]
- (SDD) a Helm chart guard that claims to mirror an app-side validator MUST mirror its exact predicate — exact-string `=="production"` silently under-guarded vs the app's `not in {dev,test}` (evidence: refute-read F2; fixed + test_secret_guard_fires_for_any_non_dev_env).  [folded foundation-version 39 · from helm-chart-scaffold]
- (SDD) when HONEST-DEGRADATION is a HARD invariant, even an UNREACHABLE corrupt-row state (s3 row with NULL object_key) must surface an honest 5xx, never a masking `or ""`/`or b""` that yields a misleading 404 or empty 200 (evidence: refute-read NIT → hardened the s3 object_key guard).  [folded foundation-version 38 · from artifacts-s3-persistence]
- (SDD) the existing CircuitBreaker is IO-tier-agnostic — it dropped onto a brand-new object-store IO seam unchanged (guard/record_success/on_upstream_error), confirming the breaker is a reusable primitive, not completion-path-specific (evidence: reused verbatim, 0 edits) [object-store-port]  [folded foundation-version 38 · from object-store-port]
- (SDD) When a task's drafted status code (422) collides with a shipped test (400), PRESERVE the shipped contract and reconcile the spec wording — never weaken the test for a cosmetic code (evidence: 400-preservation freeze flag honored).  [folded foundation-version 37 · from bff-input-validation]
- (SDD) Next 16 special-file signature: error.tsx/global-error.tsx MUST be "use client" with `{error, reset}`; global-error renders its OWN html/body and can't use providers — keep it inline-styled/dependency-light (evidence: built + compiled into the route tree).  [folded foundation-version 37 · from failure-state-segments]
- (SDD) A shared `buildMetadata` helper + root-layout defaults gives consistent SEO with title-template inheritance — far better than per-page literal objects (no OG, drift); the title template (`%s · Hydroa`) means pages store just `"Pricing"` (evidence: 8 pages unified).  [folded foundation-version 37 · from harden-marketing]
- (SDD) A single error class must live in the LOWEST layer and be re-exported upward (BffError defined in resilient-fetch.ts, re-exported by bff-client.ts) — defining it in the consumer would force a circular import and break `instanceof` across the app (evidence: refute-read confirmed instanceof holds).  [folded foundation-version 37 · from resilient-bff-fetch]
- (SDD) A static `next.config.ts` `headers()` is fully UNIT-testable by importing `nextConfig` and calling `headers()` (no running server needed for the red/green), while the RUNTIME emission is confirmed separately via a live `next start` + curl — the two together prove both the config shape AND that Next actually serves it (evidence: 4 unit tests + live curl on / and /login).  [folded foundation-version 37 · from security-headers-csp]
- (SDD) CSP relaxations must be recorded at the freeze as an auditable decision with a named upgrade path (here: `'unsafe-inline'`→nonce SPEC delta) — never a silent permanent allowance (evidence: §3 Least-sure flag + the SPEC delta above).  [folded foundation-version 37 · from security-headers-csp]
- (SDD) mint_token derives `scope` from the authorization row instead of taking the frozen §3 `scope` param — a benign, strictly-more-correct refinement of the port sketch (a token's scope is ALWAYS its authorization's scope; passing it invites mismatch). Recorded per the foundation "fix-if-strictly-more-correct, record the deviation" rule (evidence: ports.py mint_token signature vs §3 line 249).  [folded foundation-version 36 · from agent-oauth-grant-store]
- (SDD) reusing the task-2 per-IP limiter keyed by `approve:{user_id}` for a per-USER limit is a clean primitive reuse (no new infra) — the limiter's key is just an opaque string (evidence: §0 reuse note).  [folded foundation-version 36 · from device-approval-flow]
- (SDD) when reusing a primitive that doesn't fit (the per-UUID RedisLuaRateLimiter vs an unauthenticated caller), spec a NEW fit-for-purpose seam rather than forcing the old one — the per-IP limiter is the right call but should be Lua-atomic like its sibling (evidence: §0 GROUND limiter note + SPEC delta above).  [folded foundation-version 36 · from device-authorization-endpoint]
- (SDD) read surfaces mirror an existing frozen envelope (alerts) for consistency — cheap and predictable.  [folded foundation-version 35 · from audit-log-surface]
- (SDD) honest sourcing — report only what the store can prove (availability/error-rate from status); flag the gap (latency) rather than fabricate (mirrors the /status page honesty).  [folded foundation-version 35 · from slo-metrics]
- (SDD) when a refute-read's worst-case rests on an unphysical assumption (a token deficit that "stays 1 forever" despite refill closing it each slice), FIX the underlying defect anyway if the fix is strictly-more-correct + harmless, but record the corrected severity rather than the reviewer's headline (evidence: acquire() actual-slept budgeting fix; 50000-iter case bounded to ~1 slice by refill). See the `add` skill's `deltas.md`.  [folded foundation-version 33 · from bandwidth-token-bucket]
- (SDD) a subclass error (`UpstreamRateLimitedError(UpstreamUnavailableError)`) is the clean way to add a NEW HTTP mapping without disturbing existing `except` sites — mirrors the AllDeploymentsSaturatedError→429 precedent. Evidence: 0 regression across 1524 tests.  [folded foundation-version 32 · from upstream-ratelimit-passthrough]
- (SDD) delegating D1 to web research (Tin) beat my fixed-number guess — the OpenRouter ratio formula scales with max_tokens and is the industry convention; surfacing "investigate latest docs" as a freeze option is worth repeating for provider-API-shaped decisions (evidence: ratio formula replaced low=1024/med=8000/high=16000).  [folded foundation-version 31 · from reasoning-passthrough]
- (SDD) when a contract grants a second exception to a core invariant, record the decision verbatim at the §3 freeze AND mirror it as an inline comment at the enforcing WHERE clause — so the relaxation is auditable from the code, not just the TASK (evidence: get_alerts docstring + §3 "Decided at freeze").  [folded foundation-version 29 · from alerts-events-viewer]
- (SDD) exposing a previously-internal (Envoy-guarded, no-auth) operation as an authed external endpoint is a thin, safe move when the op is idempotent + delegates to the same use case — give it a SEPARATE response model so the internal contract stays byte-identical (don't extend the shared DTO). Evidence: CatalogSyncResponse vs SyncResponse.  [folded foundation-version 29 · from catalog-sync-trigger]
- (SDD) mTLS behind a reverse proxy = an XFCC EDGE-TRUST model: the app can only be the verify-half (match the forwarded fingerprint); the cryptographic check + the anti-spoof strip live in Envoy. Capture this as the standard shape for any future operator/edge-authed surface — freeze the strip+path-restriction as a release requirement, keep the app fail-closed (evidence: this task's §3 trust boundary + Least-sure flag).  [folded foundation-version 29 · from operator-wide-reconciliation]
- (SDD) read-after-write under a RESTART-TO-APPLY model must read the persisted store (merge over settings), NOT the live app.state (which is stale until restart) — and stay byte-identical when no row exists so the existing read contract is preserved (evidence: GET/PUT render merge_routing_config(settings, stored); routing-admin frozen blocks unchanged when no row).  [routing-config-write]  [folded foundation-version 29 · from routing-config-write]
- (SDD) a "add X" task where X already EXISTS → ground re-scopes to the real adjacent gap (here: the SSO button existed; the gap was the domain field) BEFORE building the wrong thing — surface the re-scope to the human at ground/specify (evidence: this task's §0 RE-SCOPE FINDING).  [folded foundation-version 29 · from sso-login-button]
- (SDD) mirroring an existing field end-to-end (v27 usage_source) is the cheapest safe way to add a ledger column — same extras seam, same NULL-encoding, same migration shape (evidence: byte-for-byte template).  [folded foundation-version 28 · from provider-generation-id-capture]

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
- Folded from v13 (2026-06-14):
  - Settled: WCAG 2.2 AA a11y is verifiable in CI/jsdom only as STRUCTURAL axe (serious|
    critical, color-contrast rule DISABLED — jsdom has no canvas for pixel sampling) plus
    keyboard/state/responsive-utility PRESENCE; axe color-contrast RATIOS and true visual
    breakpoint rendering are a NAMED browser-only residue (Playwright/agent-browser + a stub
    gateway), declared under an `unverifiable_claim` reject and carried as a follow-up infra
    task — never silently claimed as passed (evidence: v13 closed on the jsdom bar, 122/122)
  - OPEN (carried): the browser-axe color-contrast + visual-breakpoint pass; the deferred
    UI/UX surfaces (auth, model catalog, SSO/OIDC config, routing-admin, team-governance) are
    a separate UI/UX milestone mapping the dashboard to every backend feature

- Folded from v15 (2026-06-14) — dashboard feature-coverage:
  - a spec rule names the OBSERVABLE, not a mechanism — "consumes useCurrentUser" created a
    phantom requirement (the member error state is produced by the server 403, not the hook);
    state "member → role=alert error" and let the mechanism follow (else untestable dead code).
  - hand-written per-endpoint serializers DRIFT from their schema (list_keys dropped
    cache_enabled while patch_key forwarded it → list silently defaulted False); a shared
    `from_domain(item)` builder makes every endpoint forward every field by construction.
  - an "exactly-one-of" optional-identifier contract is cleanly a Pydantic
    `@model_validator(mode="after")` + `str_strip_whitespace`, so whitespace-only collapses to
    "absent" (evidence: add-member both/neither/whitespace → 422).
  - a master-detail UI satisfies one "view + manage members" criterion with one route + one
    suite — the in-page selection model is the lighter contract when deep-linking isn't required
    (chosen at the §3 least-sure flag; all 22 teams tests in one file).
- Folded from v17 (2026-06-15) — hardening / clear carried debt:
  - SECURITY (escalated, Tin 2026-06-15): `GET /api/auth/me` currently DECODES the session JWT
    WITHOUT signature verification. The original framing called this a settled UX-only tradeoff
    (the gateway verifies+enforces on every proxied request; the cookie is HttpOnly+SameSite=
    Strict so JS cannot tamper; a spoofed role only changes nav chrome, never access). Tin
    RECLASSIFIED the missing BFF-side verification as a real defense-in-depth gap, NOT a settled
    tradeoff → OPEN, owned by the `auth-me-session-verify` security task: the BFF must verify the
    session JWT signature before trusting its claims (with a designed-for-failure key-fetch path —
    timeout/cache/fallback — per the IO rule). The dashboard never weakens to the gateway being
    the *sole* verifier.
- Folded from v18 (2026-06-15) — auth session hardening (DISCHARGES the v17 escalation above):
  - SETTLED: a same-origin BFF endpoint that returns identity claims is itself a TRUST BOUNDARY — it
    MUST verify (or delegate verification of) the session token before trusting it; "the gateway
    enforces RBAC on proxied requests" does NOT cover a BFF endpoint that hands claims to the client UI
    (the nav/role derives from them). `GET /api/auth/me` no longer base64-decodes an unverified payload
    (evidence: forged-token test 401s fail-closed; v18 auth-me-session-verify).
  - SETTLED: the verification pattern is a BFF RELAY to the authoritative verifier — forward the
    session cookie as `Authorization: Bearer` to the gateway's existing `GET /admin/auth/me`
    (HS256 + iss + required-claims + exp), trust ONLY a gateway 200, FAIL-CLOSED on every other path
    (401→ERR_AUTH_INVALID_SESSION, unreachable/timeout/5xx/3xx→503 ERR_AUTH_UPSTREAM, zero claims).
    The dashboard holds NO signing secret (no sprawl); reusable for any BFF-trusts-a-token surface.
- Folded from v27 (2026-06-18) — billing precision (true per-tier cost on every call):
  - SETTLED: every usage_records row carries the PROVENANCE/BASIS of its money, never a bare number — a
    `cost_basis` ('provider' | 'catalog'), per-tier counts (`cached_tokens`/`reasoning_tokens`), and a
    `usage_source` ('frame' | 'stream_fallback'). A $0 row is always EXPLAINED; "prefer the authoritative
    source, fall back to a documented+flagged estimate" is the billing-accuracy floor (cached/reasoning
    priced distinctly, provider-reported cost preferred, audio duration derived, streams never silently $0).
  - SETTLED: "consume the upstream-reported cost" had a hidden DORMANCY trap — reading `usage["cost"]` is
    correct but NEVER fires unless the gateway opts INTO OpenRouter usage accounting. Surface the enabling
    knob (default-OFF `GATEWAY_OPENROUTER_USAGE_ACCOUNTING`) at the freeze so a feature is operator-flippable,
    not a silent no-op (evidence: provider-cost-reconciliation).
  - SETTLED: a frozen contract can carry a GUARD ASYMMETRY — the STT decoder spec had `math.isfinite` but the
    upstream-duration branch did not, so an `inf` would bill `Decimal('Infinity')` into the NUMERIC ledger.
    Mirror an invariant across EVERY sibling code path when freezing; the fix is a CHANGE-REQUEST re-freeze
    (§3 v2), never a silent edit (evidence: stt-duration-derivation).
  - OPEN follow-up: a streaming client DISCONNECT (GeneratorExit through `_wrapped` before the terminal frame)
    still bills a silent $0 with NO `usage_source` marker — distinct from the missing-frame case v27 closed;
    candidate next-loop task to stamp `usage_source='client_disconnect'` so EVERY $0 stream row is explained.

## Users (UDD) — UI/UX: design before code
- (UDD) a green `next build` proves compilation, NOT that a font/theme applies — verify computed style on a LIVE render (evidence: Geist fell back to ui-sans-serif while the build was green; caught only by a playwright probe).  [folded foundation-version 53 · from airier-theme-restyle]
- (UDD) an accent hue that works as a solid FILL can fail AA as TEXT on its own soft tint — give accent-as-text its own AA-safe token (evidence: #2f6df0 on #eef3fe = 4.14:1, failed on ~30 routes via the shared active-nav).  [folded foundation-version 53 · from airier-theme-restyle]
- (UDD) A financial-document idiom (InvoiceStatusSeal/InvoiceDetailPage) does not always transplant its exact vocabulary onto a structurally-similar-but-semantically-different document (an Art. 12 bundle has no draft state) — the lesson is to translate the IDIOM (dated header, tabular-nums, visible immutability marker) rather than force-reuse the exact component/prop union (evidence: BundleEvidenceSeal introduced as a sibling, not an InvoiceStatusSeal prop-union widening).  [folded foundation-version 53 · from compliance-report-center]
- (UDD) a security-critical access-control feature benefits from BOTH a static presence/absence test (M1) AND a separate, actively-adversarial dynamic test (R2, firing the real trigger and asserting no effect) — the static check alone would not have caught a fail-open bug where only markup was conditionally hidden but a listener was unconditionally attached (evidence: R2's own code comment names this exact failure mode; independently confirmed sound by the orchestrator reading its implementation).  [folded foundation-version 48 · from command-palette]

- (UDD) the "build-grounding scrub" (re-checking an approved mock against the frozen contract and build-time reality immediately before implementing, correcting transparently rather than building blindly or silently deviating) held 3-for-3 within this single task alone (`savings_usd` constant at build time; the `.toggle-row` referencing an unbuilt sibling task's control; the hero-sub copy asserting a live query that doesn't exist) — worth naming as a standing UDD step rather than rediscovering it ad hoc per task (evidence: this task, 2026-07-03).  [folded foundation-version 44 · from batch-dashboard-surface]
- (UDD) honest gating > silent no-op: disabling + annotating ("Ignored by <Provider>") an unsupported control, and omitting it from the body, is the truthful UI when the backend would silently drop it (evidence: the live-vs-gated capture).  [folded foundation-version 40 · from chat-parameters-panel]
- (UDD) the per-page-fit standard (PageHeader everywhere; hero+tabs only where the page warrants) scales the monitoring redesign cleanly to a heterogeneous page set — simple tables stayed header+table, complex pages got tabbed IA, with zero forced/empty tabs (evidence: 6 governance pages shipped under one frozen contract, 794 green).  [folded foundation-version 40 · from governance-pages-redesign]
- (UDD) `role=status` is reserved for the transient loading spinner across this dashboard — a persistent pager indicator must use `aria-live="polite"` (a property, not a role) so it announces without tripping the four-state invariant (evidence: build-discovered collision → contract v2).  [folded foundation-version 40 · from model-catalog-paging-search]
- (UDD) The never-axe'd auth forms + new failure segments passed serious/critical on the first check — the shared primitives (labeled Input, ErrorState role=alert) carry a11y by construction (evidence: 0 violations surfaced).  [folded foundation-version 37 · from a11y-ci-coverage]
- (UDD) Reusing the v13 `states.tsx` primitives made the failure segments a thin composition (one RouteError + thin wrappers) with no new visual language — the state-pattern investment pays off again (evidence: 7 files, ~all delegate to ErrorState/Loading).  [folded foundation-version 37 · from failure-state-segments]
- (UDD) Owning the route entrance ONCE in the shared shell (keyed by activePath) beats wrapping N pages — uniform motion, zero per-page churn, re-triggers on nav via React key remount (evidence: 13 routes covered by one wrap).  [folded foundation-version 37 · from harden-admin]
- (UDD) Sharing the entrance via AuthShell (one wrap) covers both auth pages with zero per-page churn — same shell-owns-motion pattern as the admin AppShell (evidence: 2 pages, 1 swap).  [folded foundation-version 37 · from harden-auth]
- (UDD) The a11y guarantee (reduced-motion) belongs in a GLOBAL css net (covers everything unconditionally), while the per-component primitive (Reveal) is the opt-in polish — separating "guarantee" from "enhancement" keeps the invariant robust even if a component forgets the motion-safe gate (evidence: M1 net independent of M2 Reveal).  [folded foundation-version 37 · from motion-primitives]
- (UDD) uplifting two shared primitives (StatCard + AppShell) propagates one consistent language to all 14 admin surfaces with no per-page edit — the cheapest path to the milestone's "consistent fidelity" goal (evidence: 514 green touching 2 files; the /app/usage capture shows the canvas+nav+card uplift on an untouched page).  [folded foundation-version 37 · from admin-fidelity]
- (UDD) a frozen page §3 (structure: one h1, ordered anchors) and a visual uplift coexist cleanly — restyle = className + aria-hidden decorative layers, asserted by structure-invariant tests, so the freeze never blocks the polish (evidence: landing-page.test.tsx stayed green through the Aurora hero).  [folded foundation-version 37 · from landing-fidelity]
- (UDD) the engine DTCG validator allows only color/dimension/number/fontFamily/fontWeight/duration — composite KINDS (box-shadow, cubic-bezier) are realised in globals.css (runtime source) + recorded as a token-graph note, not typed in tokens.json (evidence: `add.py check` 10 unknown_type FAILs → relocated → layer-valid PASS).  [folded foundation-version 37 · from visual-language]
- (UDD) a token-led refresh propagates the elevated language to EVERY surface via the shared primitive kit + `@theme` utilities — 508/508 green touching only 4 primitives + globals.css + tokens.json, no per-page edits (evidence: full suite green pre-application-tasks).  [folded foundation-version 37 · from visual-language]
- (UDD) a shared marketing section/card pattern now recurs across landing/pricing/legal/docs — candidate for one section primitive.  [folded foundation-version 35 · from docs-blog-scaffold]
- (UDD) marketing pages now repeat a section/prose pattern — LegalPage wrapper is the first shared extraction.  [folded foundation-version 35 · from legal-pages]
- (UDD) marketing pages share a section/tier pattern — candidate for a reusable layout (evidence: landing+pricing repeat structure).  [folded foundation-version 35 · from pricing-page]
- (UDD) honest placeholders for not-yet-available metrics (latency "not available yet") keep the UI truthful (mirrors /status + slo-metrics honesty).  [folded foundation-version 35 · from slo-dashboard]
- (UDD) mirroring an APPROVED sibling component (RatelimitsPanel) collapses the UDD design loop to "reuse" — inherit its four-state recipe + a11y region pattern verbatim rather than re-deriving a design (evidence: this panel shipped with zero new design decisions beyond the frozen disabled-caption).  [folded foundation-version 34 · from bandwidth-panel]
- (UDD) when a STATIC always-on hint already exists, a "saved" CONFIRMATION is a SEPARATE transient affordance (role=status) that must survive the save's own refetch-reseed but clear on the next user edit — gate it on user-edit handlers, NOT on a [data]-dep effect (evidence: refute attack-vector 1; reseed uses setGroups directly so saved survives).  [folded foundation-version 34 · from routing-editor-feedback]
- (UDD) a localStorage seed must read in an effect (not a lazy useState initializer) to stay SSR-safe; the `react-hooks/set-state-in-effect` lint flags it → a single-line scoped disable directly above the setState (multi-line directive misses the target line) (evidence: directive on the comment-continuation line read as "unused").  [folded foundation-version 34 · from sso-login-polish]
- (UDD) for a SMALL UI change, an AskUserQuestion `preview` (ASCII layout) served as the design-confirm — no full render-loop needed; the human picked the layout before build (evidence: Tin approved the /login layout preview 2026-06-22).  [folded foundation-version 29 · from sso-login-button]

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
- Folded from v13 (2026-06-14):
  - A shared design system is FROZEN FIRST as its own contract (UDD 3-layer token set:
    primitive=literal · semantic→primitive · component→semantic, fail-closed, `add.py check`-
    linted; + a component-variant inventory + the four state components Loading/Empty/
    ErrorState/Success + a responsive breakpoint scale + a11y primitives). Every surface
    redesign CONSUMES it; no surface hardcodes a value a token covers. The named-set
    (tokens+catalog+prototype) is a good freeze-first shape (evidence: design set lints clean)
  - Assert the SUBSTANTIVE a11y guarantee, not a version-specific attribute: this Radix
    version signals dialog modality via labelled + focus-guards, NOT `aria-modal="true"` —
    so dialogs are verified by "accessible name present + focus trapped (Tab AND Shift+Tab
    wrap) + Escape closes + focus restores", via a self-contained `lib/use-focus-trap.ts`
    (chosen over the Radix Dialog primitive, which needs polyfills absent from the shared
    test setups). The focusables selector must NOT filter on `offsetParent` (jsdom has no
    layout) (evidence: 3 dialogs keyboard-operable, focus-trap branch covered)
  - The four state patterns + responsive intent are jsdom-verifiable as PRESENCE proxies
    (role=status/alert, the Empty component, `sm:`/`lg:` breakpoint classes not fixed px);
    presentation refactors keep the data seam BYTE-IDENTICAL (same BFF route + hook + field
    names; `monthly_budget_usd` per-key ≠ `budget_usd_monthly` tenant) so the behavioral
    floor stays green (evidence: 122/122, zero data-seam diff across 3 surface redesigns)
- Folded from v15 (2026-06-14):
  - jsdom axe is a PROXY: it proves roles/labels/landmarks/focusability but NEVER color-contrast
    or true viewport layout — the real-browser a11y pass (Playwright + viewport) is a STANDING
    milestone residue, declared once and shared across v13/v15, not re-litigated per task.
  - role-based NAV visibility is a cross-surface need (admin-only `/models`, owner-only SSO):
    the static NAV shows links a `member` cannot use (they 403 on navigate — a UX gap, NOT a
    security hole; the gateway enforces RBAC) → carried as the `nav-role-filter` follow-up.
  - decorative icons paired with a visible text label MUST carry `aria-hidden` — one attribute
    removes a redundant SR announcement across all 7 nav items (consistent with states.tsx).
  - design accessible names so no control's name is a SUPERSTRING of another's; when they
    collide, role-scoped queries (`getByRole("textbox",{name})`) are the disambiguator (the
    budget input vs the "Save budget for X" button collided under getByLabelText substring-match).
  - deterministic read errors (403/404) set `retry:false` on the query, else a settled error
    retry-storms before the inline alert renders (parity fix across all settings queries).
- Folded from v14 (2026-06-14):
  - the enterprise npm-advisory SECURITY gate is scoped to the SHIPPED/production surface
    (`npm audit --omit=dev` = 0 critical/high), NOT the full dev+prod audit — dev-toolchain
    advisories (vitest/vite/esbuild) are never shipped to the deployed app, so they are triaged +
    ticketed (`devtool-vitest4-upgrade`) rather than blocking a clean production upgrade; conflating
    the two either gates production on dev debt or masks real shipped risk (evidence: v14 Next 16
    upgrade — prod surface 0/0/0 while the full audit retained 7 pre-existing dev-only advisories).
- Folded from v17 (2026-06-15):
  - role-based NAV visibility shipped: the established pattern is `minRole` tags on the
    presentational nav shell + a thin client wrapper feeding `useCurrentUser().role`, fail-open
    (a missing/unknown role shows the base nav; the gateway stays the real RBAC enforcer). It
    generalizes the v13 UsagePage `canEdit` precedent — render decisions read role from one hook,
    never from scattered checks (evidence: nav-role-filter.test.tsx 5/5; member hides
    {models,teams,routing}). NOTE: the role source (`useCurrentUser`) is exactly the claim the
    `auth-me-session-verify` task hardens — nav trust tightens for free once /api/auth/me verifies.
- Folded from v23 (2026-06-16, enterprise UI overhaul):
  - A shared **shell component is the seam for page chrome**: `AuthShell` (split-screen auth) OWNS the
    single `<main>` landmark so each page keeps exactly one main + one form, and its decorative brand panel
    is `aria-hidden="true"` + heading-free + focusable-free — which satisfies BOTH jsdom (no CSS engine, so
    `hidden lg:flex` does NOT hide it from the a11y tree) AND real-Chromium axe (which skips aria-hidden
    subtrees incl. color-contrast). Same pattern as the v13 AppShell (evidence: auth-pages-redesign,
    test_auth_shell_brand_panel_decorative + jsdom-axe green).
  - `Button asChild` (Radix Slot) is the canonical way to style a real NAVIGATION `<a>` as a button without
    turning it into a `<button>`: the link keeps href + role=link + accessible name while gaining
    `buttonVariants` classes (the SSO-link pattern; evidence: SSO `<a>` tagName==="A" + href + `inline-flex`).
  - A shadcn block (TanStack `DataTable`) can host fully INTERACTIVE rows — Switch toggle, name-button,
    inline stateful form, destructive action — via in-component `columnDef.cell` closures with
    `enableSorting:false`; keying rows by `row.id` preserves in-cell form state across data updates. Adoption
    is NOT limited to display-only tables (evidence: admin-surfaces-redesign teams/models/routing).
  - Not every surface fits every block: force-fitting where the data model diverges (Keys' interleaved
    governance expand-row vs TanStack's flat column model) would break a frozen flow — adopt the block where
    it fits, KEEP composed primitives where it doesn't, and say which at §1 (evidence: console-surfaces-redesign,
    Keys stays on composed Card+Table).
- Folded from v24 (2026-06-16, UI polish & a11y follow-ups):
  - A DS title primitive needs an explicit **heading-level escape hatch**, not a hardcoded tag: `CardTitle`
    gained `asChild` (Radix Slot) and `ChartCard` a `headingLevel?: 2|3` (default 3 = byte-identical). A card
    sitting directly under a page `<h1>` opts its title to `<h2>` so the outline never skips a level — fixing
    the defect at the shared block instead of inlining a bespoke heading per surface (evidence: v23 shipped an
    h1→h3 skip on `/`; v24 test_overview_outline_has_no_level_skip green + real-Chromium axe heading-order clean).
  - The no-flash theme `<script>` must render from a **Server Component**: a function exported from a
    `"use client"` module becomes a client *reference* and cannot be called during server render, so `themeScript`
    lives in its own non-client module (`components/ui/theme-script.ts`), the client context (ThemeProvider +
    QueryClientProvider) moves to a `"use client"` `app/providers.tsx`, and `app/layout.tsx` is a plain Server
    Component. Removes the React 19 client-`<head>` dev warning; `next build` stays clean (evidence: theme-script-
    server.test.ts 5/5 + build 18 routes). RESIDUAL: the inline script has no CSP nonce/hash — wire one if a CSP
    layer lands at Envoy/Vercel (unchanged from prior code; carried to backlog).
  - A pure-dedup refactor with **no behavioral delta** (removing the redundant consumer `aria-label` while the DS
    default stays the single source of truth) has no honest red→green — label it GREEN-BY-DESIGN and lean on a
    preservation assertion + the refute-read, never invent a fake red (evidence: test_sidebartrigger_name_from_ds_default
    passes before and after; icon-only controls keep a default name as a safety net).

## Architecture (settled at setup)

Envoy edge (TLS, jwt_authn for dashboard JWTs, ext_authz → gateway
`/internal/authz` for API keys, rate limits) → stateless FastAPI gateway
(`apps/gateway`: `/v1/*` proxy via httpx SSE pass-through, `/admin/*` control
plane, `/internal/*`) → PostgreSQL (tenants/users/keys/ledger) + Redis
(rate-limit counters, spend counters, usage write-behind buffer).

## Key Decisions (append-only)
| date | decision | why | outcome |
|------|----------|-----|---------|
| 2026-07-17 | fold all → foundation-version 53 (SDD 1 · UDD 3 · TDD 1 · ADD 3 · GLOSSARY 11) | consolidate captured OBSERVE lessons into the versioned foundation | 8 lessons open→folded; +8 routed bullets; 10 glossary term(s) added; 52→53 |
| 2026-07-13 | fold all → foundation-version 52 (GLOSSARY 4) | consolidate captured OBSERVE lessons into the versioned foundation | 0 lessons open→folded; +0 routed bullets; 4 glossary term(s) added; 51→52 |
| 2026-07-12 | fold all → foundation-version 51 (ADD 1 · GLOSSARY 4) | consolidate captured OBSERVE lessons into the versioned foundation | 1 lessons open→folded; +1 routed bullets; 3 glossary term(s) added; 50→51 |
| 2026-07-11 | fold all → foundation-version 50 (DDD 2 · SDD 1 · GLOSSARY 7) | consolidate captured OBSERVE lessons into the versioned foundation | 3 lessons open→folded; +3 routed bullets; 7 glossary term(s) added; 49→50 |
| 2026-07-10 | fold all → foundation-version 49 (DDD 2 · SDD 2 · TDD 2 · ADD 3 · GLOSSARY 3) | consolidate captured OBSERVE lessons into the versioned foundation | 9 lessons open→folded; +9 routed bullets; 3 glossary term(s) added; 48→49 |
| 2026-07-06 | fold all → foundation-version 48 (UDD 1 · TDD 7 · ADD 14) | consolidate captured OBSERVE lessons into the versioned foundation | 22 lessons open→folded; +22 routed bullets; 47→48 |
| 2026-07-04 | merge reconciliation: main (42→44 via v56+platform-identity folds) + PR #55/batch branch (42→45 via 3 independent task folds, own numbering below) → cumulative foundation-version 47 | two branches independently forked from foundation-version 42 and each folded lessons using their own local counter; a correct merge sums both branches' fold counts rather than picking one side's number | 5 fold-events total (2 main + 3 batch); 44+3=47 |
| 2026-07-03 | fold all → foundation-version 44 (SDD 3 · TDD 5 · ADD 3) | consolidate captured OBSERVE lessons into the versioned foundation | 11 lessons open→folded; +11 routed bullets; 43→44 |
| 2026-07-02 | fold all → foundation-version 43 (TDD 1 · ADD 3) | consolidate captured OBSERVE lessons into the versioned foundation | 4 lessons open→folded; +4 routed bullets; 42→43 |
| 2026-07-03 | fold --task batch-claim-drain-del → foundation-version 45 (TDD 1 · ADD 1) | consolidate captured OBSERVE lessons into the versioned foundation | 2 lessons open→folded; +2 routed bullets; 44→45 |
| 2026-07-03 | fold --task batch-dashboard-surface → foundation-version 44 (UDD 1) | consolidate captured OBSERVE lessons into the versioned foundation | 1 lessons open→folded; +1 routed bullets; 43→44 |
| 2026-07-03 | fold --task batch-auto-grouping → foundation-version 43 (TDD 1 · ADD 4) | consolidate captured OBSERVE lessons into the versioned foundation | 5 lessons open→folded; +5 routed bullets; 42→43 |
| 2026-07-02 | fold all → foundation-version 42 (ADD 5) | consolidate captured OBSERVE lessons into the versioned foundation | 5 lessons open→folded; +5 routed bullets; 41→42 |
| 2026-07-01 | fold all → foundation-version 41 (TDD 2 · ADD 5) | consolidate captured OBSERVE lessons into the versioned foundation | 7 lessons open→folded; +7 routed bullets; 40→41 |
| 2026-07-01 | fold all → foundation-version 40 (DDD 1 · SDD 1 · UDD 3 · TDD 7 · ADD 4) | consolidate captured OBSERVE lessons into the versioned foundation | 16 lessons open→folded; +16 routed bullets; 39→40 |
| 2026-06-27 | fold all → foundation-version 39 (DDD 1 · SDD 2 · TDD 11 · ADD 11) | consolidate captured OBSERVE lessons into the versioned foundation | 25 lessons open→folded; +25 routed bullets; 38→39 |
| 2026-06-26 | fold all → foundation-version 38 (SDD 2 · TDD 3 · ADD 3) | consolidate captured OBSERVE lessons into the versioned foundation | 8 lessons open→folded; +8 routed bullets; 37→38 |
<!-- NOTE: the v40–v49 program (this branch) and the v33–v50 line (main) folded the foundation
     CONCURRENTLY on separate branches; both reached "v37" via different lesson sets. The rows
     below from both histories are preserved verbatim (append-only) and reconciled at the
     v40–v49 ↔ main merge. The next `add.py fold` re-baselines from this merged record. -->
| 2026-06-26 | fold v50 → foundation-version 37 (SDD 6 · UDD 5 · TDD 7 · ADD 5) | consolidate captured OBSERVE lessons into the versioned foundation | 23 lessons open→folded; +23 routed bullets; 36→37 |
| 2026-06-26 | fold ui-fidelity → foundation-version 37 (UDD 4 · TDD 3 · ADD 3) — concurrent branch fold, reconciled at the v50↔main merge | consolidate captured OBSERVE lessons into the versioned foundation | 10 lessons open→folded; +10 routed bullets; 36→37 |
| 2026-06-26 | fold v40–v49 program → foundation-version 37 (TDD 1 · ADD 1) — concurrent branch fold, reconciled at the v40–v49↔main merge | consolidate captured OBSERVE lessons into the versioned foundation | 2 lessons open→folded; +2 routed bullets; 36→37 |
| 2026-06-26 | fold v40–v49 program → foundation-version 36 (ADD 1) — concurrent branch fold | consolidate captured OBSERVE lessons into the versioned foundation | 1 lessons open→folded; +1 routed bullets; 35→36 |
| 2026-06-25 | fold all → foundation-version 36 (SDD 3 · TDD 6 · ADD 6) | consolidate captured OBSERVE lessons into the versioned foundation | 15 lessons open→folded; +15 routed bullets; 35→36 |
| 2026-06-25 | fold all → foundation-version 35 (DDD 4 · SDD 2 · UDD 4 · ADD 3) | consolidate captured OBSERVE lessons into the versioned foundation | 13 lessons open→folded; +13 routed bullets; 34→35 |
| 2026-06-24 | fold all → foundation-version 34 (UDD 3 · TDD 3 · ADD 1) | consolidate captured OBSERVE lessons into the versioned foundation | 7 lessons open→folded; +7 routed bullets; 33→34 |
| 2026-06-24 | fold all → foundation-version 33 (SDD 1 · TDD 2 · ADD 1) | consolidate captured OBSERVE lessons into the versioned foundation | 4 lessons open→folded; +4 routed bullets; 32→33 |
| 2026-06-24 | fold all → foundation-version 32 (SDD 1 · TDD 1 · ADD 1) | consolidate captured OBSERVE lessons into the versioned foundation | 3 lessons open→folded; +3 routed bullets; 31→32 |
| 2026-06-23 | fold all → foundation-version 31 (SDD 1 · TDD 7 · ADD 8) | consolidate captured OBSERVE lessons into the versioned foundation | 16 lessons open→folded; +16 routed bullets; 30→31 |
| 2026-06-23 | fold all → foundation-version 30 (TDD 4 · ADD 4) | consolidate captured OBSERVE lessons into the versioned foundation | 8 lessons open→folded; +8 routed bullets; 29→30 |
| 2026-06-23 | fold all → foundation-version 29 (SDD 5 · UDD 1 · TDD 6 · ADD 9) | consolidate captured OBSERVE lessons into the versioned foundation | 21 lessons open→folded; +21 routed bullets; 28→29 |
| 2026-06-22 | fold all → foundation-version 28 (SDD 1 · TDD 8 · ADD 8) | consolidate captured OBSERVE lessons into the versioned foundation | 17 lessons open→folded; +17 routed bullets; 27→28 |
| 2026-06-18 | fold all → foundation-version 27 (TDD 5 · ADD 4) | consolidate captured OBSERVE lessons into the versioned foundation | 9 lessons open→folded; +9 routed bullets; 26→27 |
| 2026-06-18 | fold all → foundation-version 26 (TDD 4 · ADD 4) | consolidate captured OBSERVE lessons into the versioned foundation | 8 lessons open→folded; +8 routed bullets; 25→26 |
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
| 2026-06-13 | OPEN: the full `-m 'not e2e'` suite is NON-DETERMINISTIC against the shared dev Postgres (FK-violation flake, 16/34/44 varying); the trustworthy per-change gate is the no-DB blast-radius run (translation+dispatch suites) — the foundation needs per-test DB isolation OR a documented make test-fast (fold: TDD/response-format-contract, recurring v8) | each failing suite passes IN ISOLATION; a zero-blast-radius pure-module change still showed 16/34/44 variance; deterministic no-DB run was 83/83 | RESOLVED v12 (see below) |
| 2026-06-13 | Gemini embeddings bill EXACT tokens via a fail-safe `:countTokens` round-trip on the same adapter (None → documented ceil(chars/4) fallback; count leg never trips the embed circuit breaker) (fold: SDD/gemini-embed-tokens) | provider carries no inline embed usage; billing accuracy must never be an availability gate; LIVE C1 billed exact 42 not 3 | folded v12 |
| 2026-06-13 | Empty-key BOOT GUARD at the composition root: create_app raises EmptyUpstreamKeyError on a configured-yet-empty GATEWAY_*_API_KEY (read from raw os.environ — Settings collapses unset/set-empty to ""); absent = disabled (fold: SDD+ADD/empty-key-boot-guard) | converts the opaque per-request "Bearer ''" 500 (live v7+v8) into a clear startup error at the boundary; LIVE C3: empty→exit1, absent→boots | folded v12 |
| 2026-06-13 | Non-chat soft-budget alert reuses the chat seam via EXPLICIT reflection-free constructor DI (NonChatGovernance gains optional session_factory; fires the shared persist_soft_budget_alert); hard-402 byte-identical (fold: ADD/nonchat-soft-budget-alert) | one alert seam not a parallel re-impl; honors the no-hasattr/inspect rule (the chat getattr seam is the pattern NOT to copy); LIVE C2: 1 row idempotent, 200 | folded v12 |
| 2026-06-13 | RESOLVED the FK-violation flake: a SURGICAL group-preserving per-test Redis clear (autouse XTRIM usage:events + DEL usage:spend:*, NEVER FLUSHDB which destroys the ledger-flusher consumer group); fixtures NEVER cancel tasks (kills the pytest-asyncio runner); `make test-fast` is the no-DB fast gate (fold: TDD+ADD/test-db-isolation) | 2 consecutive clean full runs (730 passed ×2) vs FLUSHDB's 5 failures + 3x slowdown; the contaminator was leaked undelivered usage:events consumed cross-suite | folded v12 |
| 2026-06-13 | LIVE-VERIFY count assertions use a before/after DELTA, never absolute/recent-window counts — the e2e Postgres is shared across double-pass runs (fold: TDD/v12-live-verify) | v12 C4b false-positived on pass 2 against pass 1's legitimate rows; delta==0 isolates the bad-key request; double-pass 11/11 ×2 | folded v12 |
| 2026-06-14 | a11y is gated in jsdom on axe impact serious\|critical with color-contrast DISABLED (no canvas); true contrast + visual breakpoints are a NAMED browser-only residue, never silently passed (fold: SDD+TDD+UDD/v13-ui-ux-verify) | toHaveNoViolations() fails on MODERATE region/landmark rules when a component is scanned in isolation, masking the real gate; v13 closed on the jsdom bar with the residue carried | folded v14 |
| 2026-06-14 | a VERIFY-ONLY task may be legitimately green-on-first-run (no product code); honest red-first = file-absence, integrity proven by a DISCRIMINATING MUTATION check (inject a known-critical violation through the same helper, confirm caught, delete) (fold: TDD/v13-ui-ux-verify) | proves the zero-violation result is real not a vacuous/crashed scan; img-no-alt→image-alt caught, 12/12 green earned | folded v14 |
| 2026-06-14 | the design system is FROZEN FIRST as a 3-layer UDD token contract (primitive/semantic/component, fail-closed) + state-component + a11y-primitive inventory that every surface CONSUMES; Radix modality asserted as labelled+focus-trap (Tab+Shift+Tab+Escape) not aria-modal, via self-contained use-focus-trap (fold: UDD/v13) | prevents ad-hoc per-surface style drift; the Radix version exposes no aria-modal; focusables selector must not filter offsetParent in jsdom | folded v14 |
| 2026-06-14 | the §5 scope-lock flags gitignored build artifacts (.next/, coverage/, tsconfig.tsbuildinfo) — declare them in §5 Scope; strengthening tests mid-build requires going BACK to phase tests and RE-CROSSING tests→build to re-snapshot the tripwire (fold: ADD/v13) | engine _SCOPE_EXCLUDE_DIRS = .git/.add/__pycache__/node_modules only; editing tests while in build trips build_tampered; the declared list is frozen at the tests→build snapshot | folded v14 |
| 2026-06-14 | run the COVERAGE gate (vitest run --coverage) not --no-coverage before claiming "coverage held"; the adversarial earned-green refute-read (subagent) pays off on VERIFY tasks too (fold: TDD+ADD/v13) | --no-coverage HID a real 78.14%<80% regression (task1); the refute-read returned EARNED-WITH-GAPS and surfaced 3 real coverage gaps the green hid (task4, all closed) | folded v14 |
| 2026-06-14 | additive identity-resolution (email→user_id) lives in the repository txn, tenant-scoped, defense-in-depth (resolve filter AND membership check each enforce isolation) (fold: DDD/teams-add-by-email) | cross-tenant email returns 404 even if either guard alone were removed; the only sanctioned v15 gateway change | folded v15 |
| 2026-06-14 | a spec rule names the OBSERVABLE not a mechanism; "consumes useCurrentUser" was a phantom requirement (the member error comes from the server 403) (fold: SDD/model-management-ui) | naming the hook would have been untestable dead code; "member → role=alert error" lets the mechanism follow | folded v15 |
| 2026-06-14 | hand-written per-endpoint serializers drift — a shared from_domain(item) builder forwards every field by construction (fold: SDD/key-cache-enabled-fidelity) | list_keys dropped cache_enabled while patch_key forwarded it → list silently defaulted False (RED test pinned it) | folded v15 |
| 2026-06-14 | exactly-one-of optional identifier = Pydantic @model_validator(mode=after) + str_strip_whitespace; master-detail UI = one route + one suite when no deep-link (fold: SDD/teams-add-by-email+teams-governance-ui) | whitespace-only collapses to absent (both/neither/whitespace→422); in-page selection is the lighter contract (22 tests, one file) | folded v15 |
| 2026-06-14 | jsdom axe is a PROXY (roles/labels/landmarks/focusability only) — the real-browser axe+viewport pass is a STANDING residue shared v13/v15, not re-litigated per task (fold: UDD/feature-coverage-verify) | color-contrast + true layout are unprovable without canvas/layout; declared once, carried | folded v15 |
| 2026-06-14 | role-based NAV visibility is a cross-surface need → nav-role-filter follow-up; decorative label-paired icons carry aria-hidden; deterministic read errors (403/404) set retry:false (fold: UDD/model-management-ui+routing-health-ui+tenant-settings-ui) | a member sees admin links that 403 (UX gap not security hole — gateway enforces RBAC); a settled error retry-storms before the alert without retry:false | folded v15 |
| 2026-06-14 | design accessible names so none is a SUPERSTRING of another; role-scoped getByRole({name}) disambiguates collisions (fold: UDD/teams-governance-ui) | budget input vs "Save budget for X" button collided under getByLabelText substring-match (3 tests failed "multiple elements") | folded v15 |
| 2026-06-14 | every useMutation has an onError surfacing the BffError title AND every contracted error branch (404/409/422/403) has its own red→green test — STANDING rule (fold: TDD/model-management-ui+teams-governance-ui) | silent-mutation-failure DEFECT recurred (model toggle, then budget PATCH); a missing path with no red anchor makes a suite "look complete" | folded v15 |
| 2026-06-14 | loading asserts must prove RESOLUTION (loading→data), skip-link tests query ALL focusable types, fidelity tests pin per-entity (A=true WITH B=false), no-leak asserts enumerate every block, enum fixtures per VALUE, write-only secrets get explicit negative DOM asserts (fold: TDD/feature-coverage-verify+key-cache-enabled-fidelity+routing-health-ui+tenant-settings-ui) | each vacuous variant passes a broken impl: permanent role=status, first-anchor-not-focusable, always-true cheat, empty-shell render, silently-skipped half_open, sentinel-in-DOM | folded v15 |
| 2026-06-14 | scope shared msw fallbacks to needed paths — a /api/gw/:path* wildcard defeats onUnhandledRequest:error (fold: TDD/feature-coverage-verify) | a forgotten per-test handler returns wrong data not a loud failure; deferred to bff-test-harness-strict-handlers | folded v15 |
| 2026-06-14 | a milestone-EXIT verify suite legitimately lands GREEN on first run (earned-green proven by adversarial refute-read, not first-run red); a build-time Protocol signature change is scope-correction not contract-change; GROUND records the response envelope per-endpoint (fold: ADD/feature-coverage-verify+teams-add-by-email+teams-governance-ui) | "RED for the right reason" = the consolidated bar newly codified + provably held; /admin/teams is a BARE array while /admin/models is {object,data} — a wrong unwrap is a silent footgun | folded v15 |
| 2026-06-14 | a risk:high major-dep bump landing WITHOUT CI must capture prod-server smoke curl output verbatim as gate evidence — `next build` + `next start` (127.0.0.1) + curl authed/unauthed guard (fold: ADD/next16-upgrade) | a green jsdom suite cannot prove Turbopack-bundle / Edge→Node-runtime / prefetch-cache parity; the 5-curl proxy smoke was byte-identical to the v13 guard | folded v16 |
| 2026-06-14 | the enterprise npm-advisory security gate is scoped to the SHIPPED surface (`npm audit --omit=dev` 0 critical/high); dev-toolchain advisories are triaged + ticketed (devtool-vitest4-upgrade), never gate a clean prod upgrade (fold: SDD+TDD/next16-upgrade) | conflating dev+prod audit either blocks production on dev debt or masks shipped risk; v14 prod 0/0/0 vs full 7 (all dev-only, pre-existing) | folded v16 |
| 2026-06-14 | a framework's NEW lint rules on pre-existing code → downgrade error→warn (visible, never eslint-disable) + ticket the fix, never break the 0-error baseline; the dashboard production type-gate is `next build` (not bare tsc), tests-bff drift tracked separately (fold: TDD/next16-upgrade) | eslint-config-next 16 flagged 60 v13/v15 patterns (react-hooks-strict-lint); Next 16 async-params Promise<{path}> typing drifts tests-bff while prod stays clean (bff-test-harness-strict-handlers) | folded v16 |
| 2026-06-15 | ESCALATED to a security gap (Tin): `/api/auth/me` decodes the session JWT WITHOUT signature verification — the BFF must verify the signature (defense-in-depth), not lean on the gateway as the SOLE verifier (fold: SDD/nav-role-filter → task auth-me-session-verify) | a forged/replayed session cookie would spoof nav role/tenant in the UI even though the gateway blocks real access; HttpOnly+SameSite narrows but does not close the BFF-trust gap | folded v17 → task |
| 2026-06-15 | role-based nav = `minRole` tags on a presentational shell + a thin client wrapper feeding `useCurrentUser().role`, fail-open (fold: UDD/nav-role-filter) | a member sees only usable links; the gateway stays the RBAC enforcer; generalizes the v13 UsagePage canEdit precedent (one hook, not scattered checks) | folded v17 |
| 2026-06-15 | msw `onUnhandledRequest:"error"` does NOT reject the fetch (it resolves a 500) — the real leak monitor is the stderr unhandled-request COUNT, not test pass/fail (fold: TDD/bff-test-harness-strict-handlers) | "0 failures" is not "0 leaks"; a forgotten handler returns wrong data silently unless the count is watched (13→2→0 this milestone) | folded v17 |
| 2026-06-15 | tests-bff is now tsc-clean → a standing test-tree typecheck gate is newly possible; the v16 "tests-bff excluded from the type-gate" delta can tighten to include the harness (fold: TDD/bff-test-harness-strict-handlers) | tests-bff drifted under Next 16 async-params; at 18→0 it can join the gate instead of being tracked separately | folded v17 |
| 2026-06-15 | residual `/api/auth/me` unhandled-request leaks (UsagePage/AppShell render useCurrentUser with no per-test stub) → reach TRUE 0-leak via a shared AppShell stub + per-test stubs (fold: TDD/react-hooks-strict-lint+nav-role-filter → task) | the strict-harness "no shared fallback" rule needs every useCurrentUser render stubbed; couples with auth-me-session-verify (verified tokens need real stubs anyway) | folded v17 → task |
| 2026-06-15 | run the vitest floor with a generous `--testTimeout` (20s) so a CPU-starved load flake (axe ≥5s, in-flight toBeDisabled windows) never reads as a regression (fold: TDD/react-hooks-strict-lint) | 3 false failures under load → 240/240 green isolated; the `make test-fast` no-DB gate plus a timeout floor is the convention | folded v17 |
| 2026-06-15 | an adversarial refute-read catches MIS-DIAGNOSIS, not just cheating — trace every residual leak to its SOURCE file; never hand-wave a "benign late-resolve" (fold: ADD/bff-test-harness-strict-handlers) | 2 leaks labeled benign were in-file forgotten handlers in ui-ux-verify.test.tsx; the reviewer traced them → fixed to 0 | folded v17 |
| 2026-06-15 | the v16 error→warn convention now has a worked DISCHARGE template: fix behavior-preservingly (the floor is the proof) → flip the rule to error → pin with a config-text ratchet-guard test (fold: ADD/react-hooks-strict-lint) | mirrors v17 strict-harness.test.ts; the ratchet test is config-text-only, `eslint .` 0/0 is the real gate | folded v17 |
| 2026-06-15 | DISCHARGED the v17 escalation: a same-origin BFF endpoint returning identity claims is a TRUST BOUNDARY — it must verify (or delegate verification of) the session token, NOT base64-decode an unverified payload (fold: SDD/auth-me-session-verify) | the dashboard nav/role derives from /api/auth/me claims; a forged cookie could spoof identity in the UI even though the gateway blocks proxied access | folded v18 |
| 2026-06-15 | the BFF verification pattern is a RELAY to the authoritative verifier: forward the cookie as `Authorization: Bearer` to the gateway's existing `GET /admin/auth/me`, trust ONLY a 200, fail-CLOSED otherwise; the dashboard holds NO signing secret (fold: SDD/auth-me-session-verify) | one verifier that can't drift + no secret sprawl; reusable for any BFF-trusts-a-token surface (HS256+iss+exp stays the gateway's job) | folded v18 |
| 2026-06-15 | an msw default handler must be an INITIAL handler (`setupServer(...)`), NEVER a runtime `server.use()` in a setupFile — `afterEach(resetHandlers())` wipes runtime handlers after test #1 (fold: TDD/auth-me-session-verify) | this was the ROOT CAUSE of the carried v17 /api/auth/me 0-leak ("0 unloaded / N loaded"); moving it to initial handlers → 0 unhandled ×2 | folded v18 |
| 2026-06-15 | a server-side fetch RELAY must set `redirect:"manual"` + treat every non-200 as fail-closed — a followed 3xx can chain to a trusted 200 from another origin (fail-OPEN identity bypass) (fold: ADD/auth-me-session-verify) | caught by the adversarial refute-read; fixed in-scope with a redirect→503 test; pairs with AbortSignal.timeout + fail-fast no-retry | folded v18 |
| 2026-06-15 | a STRUCTURAL source-grep guard must be PRECISE, not a bare keyword: `/SECRET/i` false-positived on a comment EXPLAINING the absence of a secret (fold: ADD/auth-me-session-verify) | the precise form (`process.env.*(secret\|key\|hmac\|…)` + jwt-lib imports + verify-call names) still catches a real secret read without tripping on prose; recurring over-broad-assert smell | folded v18 |
| 2026-06-15 | test a pure classifier's pattern list in BOTH directions — true-positives AND generic false-positives ("field too long", "blocked by firewall") (fold: TDD/error-aware-fallback) | a too-broad pattern fails DANGEROUS (spurious fallover), not safe; 5 guard tests added after the refute-read flagged bare "too long"/"safety"/"blocked by" | folded v19 |
| 2026-06-15 | for cumulative-deadline / retry-exhaustion logic, test the is_last × active-deadline CROSS-state explicitly (fold: TDD/retry-seam-unify) | the green suite missed an is_last/deadline mislabel the verify-gate refute-read caught; boundary states on retry/timeout code earn the adversarial pass | folded v19 |
| 2026-06-15 | at freeze, cross-check a broad §3 RANGE against §1's explicit REJECT enumeration (fold: ADD/error-aware-fallback) | the freeze gate did NOT catch §3 "status 400-499" contradicting §1 "429 already retry-handled"; the refute-read did → classifier excludes 408/429 | folded v19 |
| 2026-06-15 | a verify-time refinement that STRENGTHENS assertions + leaves §3 byte-identical is legitimate — act in-loop, then re-cross tests→build to re-snapshot (fold: ADD/error-aware-fallback) | no test weakened; PASS after refine-and-re-cross; keeps the gate honest without inverting the method | folded v19 |
| 2026-06-15 | declare the TEST SURFACE in §5 when the build lints/formats newly-authored tests, OR format inside the tests phase before the snapshot (fold: ADD/retry-seam-unify) | ruff/eslint on new test files diverges them from the tests→build snapshot → scope-gate trip; declaring or pre-formatting resolves it cleanly | folded v19 |
| 2026-06-15 | post-freeze refinements go in §6/§7, NEVER §3 — the tamper tripwire md5s the WHOLE §3 body, comments included (fold: ADD/retry-seam-unify) | even editing a §3 pseudocode COMMENT after the snapshot trips the tripwire; the frozen body is immutable bytes | folded v19 |
| 2026-06-15 | the §5 "Scope (may touch):" line is parsed from a SINGLE physical line and FROZEN into the state.json anchor at the tests→build snapshot — keep all tokens on one line; re-snapshot if corrected (fold: ADD/reliability-verify) | a wrapped continuation path is silently dropped (scripts/* line 1 ok, infra/* line 2 missed → scope_violation); the gate reads anchor.declared not the live §5, so editing §5 alone does nothing | folded v19 |
| 2026-06-15 | an external-protocol signer/encoder is tested against the ACTUAL service's path/identifier shape, not just the canonical happy vector (fold: TDD/bedrock-sigv4-auth) | all SigV4 fixtures used path "/", hiding that the path is signed RAW; Bedrock model IDs' ':' suffix must canonicalize to %3A or every versioned-model call 403s — SV8 caught it | folded v20 |
| 2026-06-15 | a vendor-protocol live verification uses an INDEPENDENT-ORACLE stub: re-impl the auth from spec (not our signer), pin it to the vendor's published vector, prove it ACCEPTS the real sig and REJECTS a tampered one (fold: TDD/bedrock-verify) | turns "live double-pass" into a CI-able cryptographic cross-check stronger than MockTransport and not docker-gated; BV1 pins AWS get-vanilla, BV3 proves 403, BV2 proves the %3A path | folded v20 |
| 2026-06-15 | pin a security primitive's core math to an AUTHORITATIVE published vector via a small exposed seam, so self-computed higher-level expectations ride on a non-self-referential anchor (fold: ADD/bedrock-sigv4-auth) | the green stays trustworthy when the public API shape has no published known-answer; SV0 anchors SV1/SV2/SV8 | folded v20 |
| 2026-06-15 | the §5 scope-token grammar cannot express a project-root-level file (bare token = sibling-of-previous-dir; only '/'-containing tokens resolve to root) — scope root-file edits into a subdir-resident file or land them standalone (fold: ADD/bedrock-verify) | bedrock-verify's gate tripped scope_violation on a bare `Makefile` token resolving to `infra/Makefile`; the bedrock-in-test-fast floor edit was deferred to a follow-up | folded v20 |
| 2026-06-15 | split a live-infra verify task into a docker-free EARNED-GREEN core (real adapters → real socket → independent oracle) + operator scripts for the edge/cache/billing pass (fold: ADD/bedrock-verify) | the gate never blocks on a heavy stack and the residue stays honest; bedrock-verify gated on the pytest core, then the TLS-edge ×2 ran 10/10 once the e2e stack came up | folded v20 |
| 2026-06-15 | an OpenAI-compatible provider (Azure) is a THIN passthrough — chat/stream/tools/response_format need NO translation and content-filter "mapping" is a no-op (the FROZEN classify_fallback_trigger already covers "content_filter"/"content management") (fold: SDD/azure-chat) | reuse beats re-implement: zero new mapping code; only deployment-URL routing + the auth seam are genuinely new vs v9 dispatch | folded v21 |
| 2026-06-15 | a pure IO-free config/URL-routing/secret seam landed FIRST (before any adapter), frozen + fully unit-covered offline, is inherited by every sibling adapter (fold: TDD/azure-auth-routing) | high-leverage breadth-first: 11/11 offline routing+secret tests; chat/embeddings/aad inherit a proven primitive with zero docker/network | folded v21 |
| 2026-06-15 | one shared token-exchange provider INSTANCE (object identity asserted) is the single point auth plugs into across every modality (fold: SDD/azure-aad-auth + azure-embeddings) | chat + embeddings reuse the same AzureADTokenProvider → one token cache, no double IDP calls; a wiring test asserts `is` identity across adapters | folded v21 |
| 2026-06-15 | `raise ... from None` whenever wrapping an exception whose request/response could hold a secret is a TESTABLE security property — assert `exc.__cause__ is None` (fold: TDD/azure-aad-auth + azure-embeddings) | the httpx transport error chains the request whose headers/body hold the api-key/client_secret; `from None` keeps it out of any crash-reporter chain-walk | folded v21 |
| 2026-06-15 | any auth/secret task's verify gate MUST run an INDEPENDENT adversarial security subagent, not just the author's refute-read (fold: ADD/azure-aad-auth + azure-embeddings) | the subagent caught a real api-key/client_secret leak (`from exc`) + WEAK-test gaps the self-review missed, on tasks that looked like thin passthrough | folded v21 |
| 2026-06-15 | a resilience-seam test injects a CircuitBreaker SPY (counts on_upstream_error/record_success) so breaker transitions are ASSERTED, not assumed (fold: TDD/azure-embeddings) | a 5xx test that only checks `pytest.raises` passes an impl that never trips the breaker; the spy closes that gap | folded v21 |
| 2026-06-15 | a token-exchange provider's live oracle MINTS the credential the gateway must echo back (token endpoint → Bearer-accept-only-if-minted) — an end-to-end auth proof, the token analogue of v20's SigV4 re-impl (fold: ADD/azure-verify) | AV3 + live C1 accept ONLY the stub-minted token; reusable for any exchange auth (managed-identity, GCP SA, AWS STS) | folded v21 |
| 2026-06-15 | scope-snapshot cache prophylaxis: build-phase ruff with RUFF_CACHE_DIR=/tmp + pytest `-p no:cacheprovider --no-cov` so NO cache enters the tests→build snapshot; a subagent's ruff can still pollute repo-root → clean + re-snapshot (fold: ADD/azure-*) | tasks using the prophylaxis gated first-try; azure-auth-routing + azure-verify bounced on stray caches until a clean re-snapshot — a root Makefile floor edit then LANDED via re-snapshot (vs v20's defer) | folded v21 |
| 2026-06-15 | OPEN: the systemic `raise ... from exc` across the shared execute_with_retry seam + every non-Azure adapter (openai/bedrock/gemini/anthropic) chains the secret-bearing request — propose a cross-cutting `provider-secret-chain-hardening` sweep → `from None` + regression tests (fold: SECURITY/azure-embeddings) | spans multiple frozen contracts so it is its own task; azure_ad.py + azure_embeddings.py are leak-free today | RESOLVED v22 (see below) |
| 2026-06-15 | OPEN: GATEWAY_AZURE_AD_AUTHORITY is not env-configurable (resolve_azure_ad_config ignores authority) — add the knob so the live double-pass can drive AAD, not only api-key (fold: SDD/azure-verify) | AAD is proven end-to-end in the pytest layer (AV3); the live edge used api-key; a small additive config change closes the gap | RESOLVED v22 (see below) |
| 2026-06-15 | `from None` is now the PROJECT-WIDE secret-chain floor: ALL 13 secret-bearing transport-error wraps (shared execute_with_retry seam ×3 + openrouter/openai/anthropic/gemini/bedrock/azure stream+post_json) raise `from None`, generalizing the v21 Azure bar; greppable invariant (`rg "from exc\|from terminal_exc" infrastructure/` → zero) + one `__cause__ is None` test per site (fold: SECURITY+TDD/provider-secret-chain-hardening, RESOLVES the v21 follow-up) | the chained httpx error's `.request` carried the api-key/SigV4/client_secret to any crash-reporter walking `__cause__`; behavior-preserving (same type+message), 13/13 earned-green + 477-test regression | folded v22 |
| 2026-06-15 | Azure AD authority is env-configurable (GATEWAY_AZURE_AD_AUTHORITY → Settings → resolve_azure_ad_config carries it → AzureADConfig.authority → minted-token URL); unset = public-cloud DEFAULT_AUTHORITY, byte-identical (fold: SDD+DDD/azure-ad-authority-config, RESOLVES the v21 follow-up) | sovereign/gov clouds (login.microsoftonline.us/.cn) could not be reached; a partially-wired seam (authority consumed but never sourced) looked configurable but wasn't — an END-TO-END test (settings→URL) exposed the gap | folded v22 |
| 2026-06-15 | calibrate the §5 `risk:` level to ACTUAL reversibility/blast, not the topic — a behavior-preserving security REMEDIATION (verify CONFIRMS a fix, not discovers a finding) with full regression is auto-gateable at `risk: medium`; `risk: high`+`autonomy: auto` trips the `unguarded_high_risk_auto` guard (human-owned gate). Record calibration transparently; human may override (fold: ADD/provider-secret-chain-hardening) | over-flagging blocks the auto loop on a change the project bar (v21 azure-aad-auth/azure-embeddings, unlabelled) already auto-gated after remediation; a security FINDING (run.md) is a discovered problem, which this was not | folded v22 |
| 2026-06-16 | A presentation-only restyle freezes the COMPONENT SHAPE + DOM/a11y markers (not a network contract); the NEW red→green suite asserts ONLY the adoption via a stable `data-slot` marker, while the dense FROZEN behavioral suites stay the regression net — every restyle confirmed by an adversarial refute-read returning EARNED (fold: TDD+UDD/console+admin+auth-redesign) | data-slot is non-brittle + genuinely discriminating (beats CSS-class asserts; survives interactive cells); zero data-seam diff across all 7 surfaces + auth, 329/329 green | folded v23 |
| 2026-06-16 | A shared shell component OWNS page chrome + the single `<main>` landmark; a decorative panel is made a11y-invisible by `aria-hidden="true"` + heading-free + focusable-free (works in BOTH jsdom — no CSS engine — and real-Chromium axe, which skips aria-hidden subtrees incl. color-contrast); `Button asChild` (Radix Slot) styles a nav `<a>` without making it a `<button>` (fold: UDD/auth-pages-redesign) | split-screen AuthShell answered "login too simple" while keeping every POST route/validation/redirect byte-identical; SSO link keeps href+role=link+name | folded v23 |
| 2026-06-16 | The §5 scope baseline walks the working tree (excludes only .git/.add/__pycache__/node_modules), so a gitignored BUILD ARTIFACT present at the tests→build snapshot pollutes it: `apps/dashboard/coverage/` (a `--coverage` run) or `tsconfig.tsbuildinfo` (any `tsc --noEmit`) between snapshot and gate trips `scope_violation`. Workaround: delete artifact + re-snapshot (phase tests→advance) + run ONLY `npm test` for the gate (fold: ADD/console+admin+auth-redesign) | THREE recurrences across v23 (coverage ×1, tsbuildinfo ×2) ⇒ ship the engine fix: extend the scope-walk exclusion to gitignored build artifacts (`coverage/`, `*.tsbuildinfo`) | folded v23 |
| 2026-06-16 | DS title primitives carry an explicit heading-level ESCAPE HATCH, not a hardcoded tag: `CardTitle asChild` (Radix Slot) + `ChartCard headingLevel?: 2\|3` (default 3 = byte-identical) let a card under a page `<h1>` opt its title to `<h2>` — the outline is fixed at the SHARED block, never by inlining a bespoke heading per surface (fold: UDD/overview-heading-a11y-fix) | v23 shipped an h1→h3 skip on `/` because CardTitle was a fixed `<h3>`; assert via getByRole heading-level + real-Chromium axe heading-order, not CSS | folded v24 |
| 2026-06-16 | The no-flash theme `<script>` renders from a SERVER COMPONENT: a function exported from a `"use client"` module is a client *reference* (uncallable in server render), so `themeScript` lives in a non-client module, the client context moves to `app/providers.tsx`, and `app/layout.tsx` is a plain Server Component (fold: UDD+SDD/overview-heading-a11y-fix) | removes the React 19 client-`<head>` dev warning + is idiomatic App Router; `next build` clean (18 routes); RESIDUAL backlog: inline script has no CSP nonce/hash — wire one if a CSP layer lands | folded v24 |
| 2026-06-16 | §5 scope-walk papercut RECURRED a 4th time, now acute — a background `tsc` (`incremental:true`) regenerates `tsconfig.tsbuildinfo` AFTER a clean re-snapshot, so delete-then-gate races and the gate trips on an artifact it cannot prevent; in-task fix = DECLARE the artifact as an in-scope token on the §5 Scope line (truthful: tsc produces it) (fold: ADD/overview-heading-a11y-fix) | FOUR recurrences ⇒ the engine fix is now overdue: extend `_SCOPE_EXCLUDE_DIRS`(+`.next`) and `_SCOPE_EXCLUDE_FILES`(+`tsconfig.tsbuildinfo`, `*.tsbuildinfo` suffix) in add.py | folded v24 |
| 2026-06-16 | A pure-dedup/refactor with NO behavioral delta gets a GREEN-BY-DESIGN preservation assertion + the refute-read, never a fabricated red→green; icon-only controls keep a DS default accessible name as a safety net (the consumer dedup removes the duplicate, not the name) (fold: TDD/overview-heading-a11y-fix) | test_sidebartrigger_name_from_ds_default passes before and after — inventing a fake red would be the dishonest move the method forbids | folded v24 |
| 2026-06-18 | CATCH-UP: v25 & v26 deltas were never consolidated (their milestones archived without the fold step) — swept into the foundation at the v27 close (fold: ADD/foundation-debt-reconcile) | foundation-version was 24 while v25/v26/v27 deltas sat open; one session clears all 19 genuinely-open deltas (the 4 v24 strays were already folded, status only flipped) | folded v25 |
| 2026-06-18 | A delegated/subagent per-task "green" on a SCHEMA-touching change is not trustworthy — only the FULL-suite blast-radius run catches a 2nd hardcoded table-manifest (fold: TDD/provider-credential-store) | tests/guardrails/test_guardrails_core.py held a second manifest the narrow run missed | folded v25 |
| 2026-06-18 | The §5 "Scope (may touch):" anchor freezes from a SINGLE physical line at tests→build; legitimately touching more needs an explicit amend + re-snapshot (fold: ADD/provider-credential-store) | hit 4× (main.py/env.py · tenants ORM+migrations manifest · guardrails manifest · gate-added test) | folded v25 |
| 2026-06-18 | A risk:high SECRET task's verify MUST run an INDEPENDENT adversarial security subagent (fold: ADD/provider-credential-store) | it found a real api_key encrypt→decrypt path never DB-tested that the all-green suite hid; human gate closed it | folded v25 |
| 2026-06-18 | Earned-green must test the dispatch CONTRACT (complete()), not only the adapter TRANSPORT (post_json) — protocol-surface tests assert isinstance against the Protocol the CALLER uses; never `# type: ignore` a Protocol-adapter mismatch (fold: TDD+ADD/openai-chat-complete) | a type:ignore masked a Protocol mismatch → latent 500 only the end-to-end verify surfaced | folded v26 |
| 2026-06-18 | Class-level attribute defaults extend an adapter ctor without breaking a sibling task's `__new__`-built doubles (fold: TDD/openai-retry-parity) | kept frozen openai_chat_dispatch green; re-confirmed v27 for `_usage_accounting=False` (9 retry doubles) | folded v26 |
| 2026-06-18 | Retiring dead code whose tests doubled as weak invariant guards = RE-EXPRESS the invariant against a live surface (Settings.model_fields), never delete the assertion (fold: ADD/retire-empty-key-guard) | the BYOK invariant stayed pinned to a live config surface after the guard was removed | folded v26 |
| 2026-06-18 | Every usage_records row carries the PROVENANCE of its money (cost_basis · per-tier counts · usage_source); a $0 row is always EXPLAINED — prefer the authoritative source, else a documented+flagged fallback (fold: SDD/tiered-token-billing+provider-cost-reconciliation+stt-duration-derivation+stream-usage-completeness) | the v27 billing-accuracy floor: cached/reasoning priced distinctly, provider cost preferred, audio derived, streams never silent $0 | folded v27 |
| 2026-06-18 | "Consume upstream cost" had a DORMANCY trap — reading usage["cost"] never fires unless the gateway opts into usage accounting; surface the default-OFF knob at freeze (fold: SDD/provider-cost-reconciliation) | GATEWAY_OPENROUTER_USAGE_ACCOUNTING turned a silent no-op into an operator-flippable feature | folded v27 |
| 2026-06-18 | A frozen contract can carry a GUARD ASYMMETRY — mirror an invariant across every sibling code path when freezing; fix via CHANGE-REQUEST re-freeze, not a silent edit (fold: SDD/stt-duration-derivation) | the STT decoder had math.isfinite but the upstream-duration branch did not → inf would bill Decimal('Infinity'); §3 re-froze @ v2 | folded v27 |
| 2026-06-18 | Alembic `env.py fileConfig()` defaults disable_existing_loggers=True → it silently disables every gateway.* logger in-process, emptying downstream caplog (RED only full-suite); fix disable_existing_loggers=False (fold: TDD/provider-cost-reconciliation) | 3 caplog-on-app-logger tests RED in the full suite, green isolated; bisected to tests/migrations | folded v27 |
| 2026-06-18 | A pure-TOTAL predicate's test table enumerates the TYPE-CONFUSION axis (bool/float/negative/None/non-dict), not just the value axis (fold: TDD/stream-usage-completeness) | SU7 shipped green at 9 params; the refute-read found 3 missing type rows → 12 params | folded v27 |
| 2026-06-18 | An inf-via-HTTP billing test is confounded (allow_nan=False makes the response raise pre-status) — pin the LEDGER: pytest.raises on the call + poll the ensure_future usage spy for the billed quantity (fold: TDD/stt-duration-derivation) | SD8 saw Decimal('Infinity') in the spy RED, finite GREEN after the isfinite guard | folded v27 |
| 2026-06-18 | The verify-gate adversarial refute-read keeps paying off on fully-GREEN builds; editing a declared test during VERIFY needs the sanctioned tripwire re-cross (phase tests→advance ×2), never an in-place edit (fold: ADD/provider-cost-reconciliation+stt-duration-derivation+stream-usage-completeness) | refute-reads found PC13/PC14 + isfinite/cap + 3 predicate NITs across the 3 tasks; stream-usage re-crossed clean | folded v27 |
| 2026-06-18 | OPEN follow-ups (v27, feed the next loop): (1) client-disconnect/GeneratorExit still bills a silent $0 with no marker; (2) no UPPER magnitude cap on a billed STT duration (tinytag header trust); (3) inf/nan upstream STT duration still 500s on response serialization (fold: SDD+ADD/stream-usage-completeness+stt-duration-derivation) | three carried candidate-next-loop deltas, out of their tasks' frozen scope | folded v27 (open follow-up) |
