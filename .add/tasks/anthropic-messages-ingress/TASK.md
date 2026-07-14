# TASK: Native /v1/messages ingress (+ count_tokens): Anthropic-wire in, shared governance/router/recorder path, billing parity

slug: anthropic-messages-ingress · created: 2026-07-14 · stage: production
milestone: agent-gateway-v1
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/api/router.py::completions` (L33-118) — the existing OpenAI-wire `POST /v1/chat/completions` endpoint. `/v1/messages` is its Anthropic-wire sibling at the API layer; same DI shape (`use_case`, `upstream`, `usage_recorder`, `raw_key` deps), same non-stream/stream branch structure.
- `apps/gateway/src/gateway/proxy/api/embeddings_router.py` (whole file, small) — the per-modality router-FILE convention (`APIRouter()` + one endpoint function + its own `*_deps.py`) that `images_router.py`/`audio_router.py`/`embeddings_router.py` all follow, rather than growing the monolithic `router.py`. The new `/v1/messages` (+`count_tokens`) endpoints should live in a sibling `messages_router.py`, not appended to `router.py`.
- `apps/gateway/src/gateway/proxy/api/deps.py::get_raw_api_key` (L105-111) — CURRENT in-process authn extraction for `/v1/chat/completions`: `Authorization: Bearer` ONLY, no `X-Api-Key` fallback. Gap this task must close for `/v1/messages` clients that send `x-api-key` (Anthropic SDK default).
- `apps/gateway/src/gateway/proxy/api/deps.py::get_completion_use_case` (L114-294) — builds `CompletionUseCase` with ~20 optional collaborators resolved from `request.app.state` via `getattr(..., None)` (budget guard, rate limiter, response cache, guardrail evaluator, residency lookup, tier capacity guard, credit guard, tenant model preset store, vector cache, cost recovery, bandwidth bucket, payload capture, …). The new endpoint MUST reuse this exact function unmodified — re-implementing DI here would silently drop optional governance features for Anthropic-wire traffic.
- `apps/gateway/src/gateway/keys/api/router.py::_extract_raw_key` (helper immediately above L558) and `::authz` (L558-588) — the Envoy ext_authz edge seam's OWN key extraction, documented priority contract: `Authorization: Bearer` checked first (any non-Bearer scheme treated as absent), `X-Api-Key` fallback; identical opaque 401 (`ERR_AUTH_INVALID_KEY`) for every failure mode. This is the EXACT extraction logic the new in-process `/v1/messages` seam must replicate so both authn seams agree byte-identically (milestone shared decision).
- `infra/envoy/envoy-prod.yaml` L106-135 — `ext_authz` for the `/v1/` route ALREADY allows both `authorization` and `x-api-key` as forwarded headers to `/internal/authz` (`authorization_request.allowed_headers.patterns`). The EDGE seam is already ready; only the in-process handler needs new extraction.
- `apps/gateway/src/gateway/proxy/application/use_cases.py::CompletionUseCase.complete` (L2120-2791) and `::CompletionUseCase.stream` (L2793-3616) — the governance→router→recorder chokepoint every chat request already traverses: `_authenticate` (L1136-1152), `_check_model_catalog`/`_check_single_model` (L1338-1400), `_enforce_rate_limits` (L1402-1431), `_check_team_budget`/`_check_per_key_budget` (L1433-1466, L1604-1660), `_enforce_governance` (L1468-1602, composes guardrails/residency/tier), `_try_cache_lookup` (L1812-2118), provider resolution/fallback, and usage recording. This task's ingress MUST call `.complete`/`.stream` UNCHANGED — same signature, same call site pattern as `router.py::completions`.
- `apps/gateway/src/gateway/proxy/domain/ports.py::CompletionUpstream` (L153-170) — the `Protocol` every provider adapter implements: `complete(payload: dict) -> (status, dict)` / `stream(payload: dict) -> AsyncIterator[bytes]`, payload/response ALWAYS the internal OpenAI-shape. This is the seam BELOW `CompletionUseCase`; ingress translation never touches it — the use-case and every adapter stay byte-identical.
- `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` — the EGRESS Anthropic adapter (internal OpenAI-shape → real Anthropic API, for when Hydroa itself calls Anthropic as a provider). Ingress is its architectural MIRROR one layer up (client Anthropic-wire → internal OpenAI-shape), not a peer or reuse-in-place. Concrete field-mapping vocabulary this task reverses:
  - `_openai_to_anthropic_request` (L265-497): system-message lift, tool_calls↔tool_use, tool-role-run↔tool_result, `cache_control` pass-through/auto-inject, `max_tokens` always-required, `stop`↔`stop_sequences`, `tools`/`tool_choice` mapping, `reasoning_effort`→`thinking.budget_tokens` (D1/D2 FROZEN ratio).
  - `_tools_to_anthropic` (L153-163) / `_tool_choice_to_anthropic` (L223-238): `parameters`↔`input_schema`; `"auto"`↔`{type:auto}`, `"required"`↔`{type:any}`, `{type:function,function:{name}}`↔`{type:tool,name}`.
  - `_anthropic_to_openai` (L523-617): content-block concatenation, `tool_use`→`tool_calls`, `usage.input_tokens/output_tokens`→`prompt_tokens/completion_tokens` (+ `cache_read_input_tokens`/`cache_creation_input_tokens`→`prompt_tokens_details`), `stop_reason` mapping.
  - `_anthropic_error_to_openai` (L620-641) + `_KNOWN_ERROR_TYPES`: Anthropic error-type allow-list, unknown→`"upstream_error"`.
  - `_AnthropicSSEStepper` (L644-811): stateful, one-event-at-a-time translator; event vocabulary `message_start`/`content_block_start`/`content_block_delta` (`text_delta`|`input_json_delta`)/`message_delta`/`message_stop`/`ping`/`error` — ingress needs the REVERSE stepper (internal OpenAI SSE chunks → this exact Anthropic event sequence).
  - `_extract_reasoning_effort` (L72-125) / `_compute_anthropic_budget` (L128-132) / `_REASONING_EFFORT_RATIO`: FROZEN `reasoning_effort↔budget_tokens` ratio. Confirmed (via grep) ABSENT from `bedrock_upstream.py` and `vertex_upstream.py` — extended thinking has no Bedrock/Vertex equivalent today.
- `apps/gateway/src/gateway/core/errors.py::ProblemError/problem_response/register_error_handlers` (whole file) — the project-wide RFC 9457 `application/problem+json` convention every OTHER surface uses. `/v1/messages` deliberately does NOT use this envelope (an Anthropic-wire client expects Anthropic's own `{"type":"error","error":{...}}` shape) — same class of documented, precedented carve-out as `apps/gateway/src/gateway/scim/api/errors.py` (RFC 7644 SCIM envelope reserved for `/scim/v2/*`).
- `apps/gateway/src/gateway/core/error_catalog.py::AUTH_KEY_INVALID/MODEL_UNKNOWN/MODEL_NOT_ALLOWED/PAYLOAD_INVALID/...` (`ErrorSpec` instances, L83-260+) — the ERR_* codes `CompletionUseCase` already raises as `ProblemError`. Ingress must catch these at the boundary and re-wrap Anthropic-shaped — never let a raw problem+json body reach an Anthropic-wire client.
- `apps/gateway/src/gateway/catalog/infrastructure/orm.py::ModelRow` (L19-36) + `apps/gateway/src/gateway/catalog/infrastructure/bedrock_seed.py` (header comment) — `models.id` is the catalog PK and is ALREADY the provider-native model-id string (e.g. Bedrock rows use AWS's own `us.anthropic.claude-3-5-sonnet-20241022-v2:0`; OpenRouter rows use `anthropic/claude-opus-4`). Confirms NO new alias table is needed for model-name mapping: an Anthropic-wire client's `model` field passes straight through as the internal `body["model"]`, exactly like an OpenAI-wire client's `model` field does today — reusing `CompletionUseCase._check_model_catalog`'s existing alias/model_groups machinery (L1338-1377) unmodified.
- `apps/gateway/src/gateway/proxy/infrastructure/composite_key_authenticator.py::CompositeKeyAuthenticator` — accepts both `sk-` API keys and minted agent tokens; reused unmodified by the new authn extraction (same authenticator instance `get_completion_use_case` already builds).
- No existing `count_tokens` / token-counting utility anywhere in `apps/gateway/src` (confirmed via repo-wide grep for `count_tokens|token_count|CountTokens`) — this endpoint is new ground, not a re-wire.
- `apps/gateway/src/gateway/main.py` L149-150, L1454 — router-registration precedent (`from gateway.proxy.api.router import proxy_router` / `app.include_router(proxy_router)`); the new `messages_router` registers the same way.

Context (working folder): `FEATURES.md` L18 (provider breadth incl. Anthropic), L33-37 (existing EGRESS-side "thinking token passthrough" + "mid-stream provider error → terminal SSE error frame" product copy — this task brings the same fidelity to ingress, not net-new product behavior); `docs/roadmap/2026-07-14-enterprise-roadmap.html` §4 R1 (roadmap rationale); no existing `.add/SEAMS.md` entry for wire-ingress translation (this task is the first of its kind, not a re-derivation).

Honors (patterns / conventions): project-wide RFC 9457 problem+json is the DEFAULT (`core/errors.py`) — this task's Anthropic-error-envelope exception must be documented inline exactly as `scim/api/errors.py` documents its own RFC 7644 exception; per-modality router-file convention (`embeddings_router.py`/`images_router.py`/`audio_router.py`); `app.state`-singleton-with-`getattr`-default DI convention used throughout `deps.py` (never re-implement, always reuse `get_completion_use_case`); the D1/D2 `reasoning_effort` FROZEN ratio contract in `anthropic_upstream.py` (never re-derive its constants).

Seams consulted: none pre-exist — no `.add/SEAMS.md#*` entry yet covers ingress-side wire translation.

Anchors the contract cites: `CompletionUseCase.complete`/`.stream` signatures (`use_cases.py`); `CompletionUpstream` Protocol (`ports.py`); `ModelRow.id` (`catalog/infrastructure/orm.py`); `ProblemError`/`error_catalog.py` ERR_* codes; the Anthropic SSE event vocabulary defined by `_AnthropicSSEStepper`; `get_raw_api_key`/`_extract_raw_key` priority contract; `get_completion_use_case` (`deps.py`).

Issues/Risks (→ feed §1):
1. The in-process `/v1` authn seam (`get_raw_api_key`) does not read `X-Api-Key` today — a real gap vs. the milestone's "both authn seams agree, byte-identical 401s" invariant; must add extraction mirroring `_extract_raw_key`'s priority (Bearer first, X-Api-Key fallback) for `/v1/messages` specifically (NOT retrofitted onto `/v1/chat/completions` — out of this task's scope).
2. Extended-thinking `budget_tokens` has no Bedrock/Vertex equivalent — a client sending `thinking:{budget_tokens}` whose request gets routed (by tier/fallback/preset) to a non-Anthropic candidate has no faithful destination for that field.
3. The internal OpenAI-shape stream never carries a prompt-token count until its terminal chunk (unlike real Anthropic's `message_start.usage.input_tokens`, accurate up front) — a byte-faithful `message_start` is not achievable without either (a) a token-count pre-flight call before every streamed response, or (b) reporting 0/absent `usage` at `message_start` and correcting only at `message_delta`/`message_stop`.
4. `count_tokens` has no existing analog anywhere in the codebase — genuinely new ground. RESOLVED (Freeze decision, Tin 2026-07-14): it DOES traverse the full governance chokepoint (authn, model-check, budget, rate-limit, plan/credit) exactly like `/v1/messages` — the only difference is a $0-billed, no-usage-record SUCCESS outcome; see M12.
5. `CompletionUseCase.__init__` takes ~20 optional collaborators wired via `getattr(request.app.state, ...)` in `get_completion_use_case` (L114-294) — the ingress endpoint must reuse this SAME function unmodified, or every optional governance feature (budget, tier capacity, residency, guardrails, credit hold, …) silently goes missing for Anthropic-wire traffic.
6. `usage_records.usage_source` (`usage/infrastructure/orm.py` L107) is an established discriminator with a fixed vocabulary (`frame`/`stream_fallback`/`client_disconnect`/`openrouter_recovered`, consumed by reconciliation queries in `usage/application/reconciliation.py`/`cost_recovery.py`) — it must NOT be overloaded with an ingress-dialect value; a `/v1/messages`-served row must be schema-indistinguishable from an equivalent `/v1/chat/completions` row (billing parity, M11).

Related intent: `MILESTONE.md` "Ingress is translation-only" (requests traverse governance→router→recorder byte-identically) + "ONE billing path" (no parallel ledger) + "Both authn seams... byte-identical 401s"; roadmap rationale = Anthropic officially supports gateway-fronting since Apr 2026, verified absent in Hydroa; sequenced before the dependent `claude-gateway-protocol-compat` task. GLOSSARY: introduces no changes to existing terms; new terms proposed in §3.

Ground SHA: c948576

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Native `/v1/messages` ingress (+ `count_tokens`): Anthropic-wire in, shared governance/router/recorder path, billing parity

Framings weighed:
(chosen) Thin bidirectional translation layer ABOVE `CompletionUseCase` — a new `messages_router.py` + a new `anthropic_ingress.py` translator module that converts client Anthropic-wire ⇄ the SAME internal OpenAI-shape `CompletionUseCase.complete`/`.stream` already consumes/produces, calling them UNCHANGED (zero edits to governance/routing/recording code).
· A new parallel `AnthropicMessagesUseCase` duplicating governance logic for Anthropic-wire requests — REJECTED: violates the "ingress is translation-only" milestone invariant outright and doubles the maintenance surface, the exact bug class that produced 2 stale cross-task response-shape defects at the last milestone's pre-merge full-suite gate (per project history).
· Reuse the EGRESS `anthropic_upstream.py`'s private `_openai_to_anthropic_request`/`_anthropic_to_openai` functions directly, called in reverse, as the sole translation mechanism — REJECTED: those helpers carry EGRESS-shaped defaults and config (e.g. `default_max_tokens`/`auto_cache` tied to Hydroa-as-caller-of-Anthropic), are unexported private functions of an already-frozen contract, and INGRESS needs INBOUND-shaped defaults (Anthropic requires `max_tokens`; the internal OpenAI-shape does not) — a new, independent, mirrored translation module avoids coupling two separately-frozen surfaces.

Must:
<must>
  - M1: `POST /v1/messages` accepts a well-formed Anthropic Messages request body (`model`, `messages`, `max_tokens` required; `system`, `tools`, `tool_choice`, `stop_sequences`, `temperature`, `top_p`, `thinking`, `stream`, `metadata` optional) and translates it into the internal OpenAI-shape body `CompletionUseCase.complete`/`.stream` already accept — both non-stream and stream.
  - M2: after translation, the request traverses `CompletionUseCase.complete`/`.stream` UNCHANGED — same authn, model catalog/allowlist, rate limits, team/per-key budget, credit hold, tier capacity, residency, guardrails (pre+post), response cache, provider routing/fallback, and usage recording as `/v1/chat/completions` (byte-identical governance — milestone invariant).
  - M3: the in-process authn seam for `/v1/messages` accepts BOTH `x-api-key` and `Authorization: Bearer`, with the SAME priority contract Envoy's ext_authz + `/internal/authz` already implement (Bearer checked first; `x-api-key` fallback when Authorization is absent or non-Bearer) — both authn seams agree on every accept/reject.
  - M4: a non-streaming 200 is translated from the internal OpenAI-shape completion body into an Anthropic Messages response: `id`, `type:"message"`, `role:"assistant"`, `content` (text block(s) + `tool_use` block(s) when tool calls are present), `model`, `stop_reason`, `stop_sequence`, `usage:{input_tokens,output_tokens}` (+ cache-token fields when present).
  - M5: a streaming request emits Anthropic SSE events in Anthropic's real sequence: `message_start` → (`content_block_start` + `content_block_delta` [`text_delta` for text, `input_json_delta` for tool-call argument fragments] + `content_block_stop`)* → `message_delta` (stop_reason + cumulative usage) → `message_stop`.
  - M6: a request whose model resolves (by catalog/preset/fallback) to a NON-Anthropic provider candidate is served exactly as normal — translate-through, never refuse solely because the client spoke Anthropic-wire (the ingress-is-translation-only invariant applies regardless of which downstream provider ultimately serves the model).
  - M7: `thinking:{type:"enabled",budget_tokens}` is honored byte-exact ONLY when the request is actually served by the direct Anthropic provider adapter; for any other resolved provider the field is silently dropped (documented degrade, never a reject) — mirrors the existing precedent that `reasoning_effort`/`thinking` translation is Anthropic-adapter-only today (confirmed absent from Bedrock/Vertex adapters).
  - M8: `cache_control` breakpoints on system/message content blocks are carried through only to a resolved provider whose adapter already forwards `cache_control` (currently: the Anthropic egress adapter only) — silently dropped for every other provider.
  - M9: every governance-layer rejection that already raises a `ProblemError` for `/v1/chat/completions` (auth, model, payload, budget, rate-limit, guardrail, residency, tier) is caught at the `/v1/messages` boundary and re-emitted as an Anthropic-shaped error envelope (`{"type":"error","error":{"type":<mapped>,"message":<m>}}`) with the SAME HTTP status — never a raw problem+json body reaches an Anthropic-wire client (mirrors the SCIM `/scim/v2/*` documented carve-out from the project-wide RFC 9457 convention).
  - M10: a provider/upstream failure mid-stream (5xx, timeout, circuit-open, or an Anthropic-native `error` event surfacing while HTTP stays 200) terminates the SSE stream with an Anthropic-shaped terminal `error` event — never a silent truncation or hang.
  - M11: every served `/v1/messages` request bills through the SAME shared `UsageRecorder`/`RecordingUsageRecorder` into `usage_records` exactly once — no parallel ledger, no double-count, no under-count relative to the equivalent `/v1/chat/completions` call for the same tokens/model/tenant; the written row is schema-indistinguishable (same `usage_source` vocabulary, no new discriminator).
  - M12: `POST /v1/messages/count_tokens` accepts an Anthropic-wire request body (same shape as M1 minus `max_tokens`/`stream`) and returns `{"input_tokens": <int>}`. **Freeze decision (Tin 2026-07-14): count_tokens passes the SAME admission gates as `/v1/messages`** — authn, model-check, budget, rate-limit, and plan/credit gates ALL run exactly as they do for a chat call — and is billed **$0**: it produces NO `usage_records` row and NO charge, but an over-budget / over-rate-limit / no-credit tenant gets the identical structured refusal a chat call would get. The milestone's "same governance path" invariant now holds with **zero carve-outs**; M2 applies to `count_tokens` without exception.
</must>
Reject:
<reject>
  - R1: request body fails Anthropic schema validation (missing `model`/`messages`/`max_tokens`, malformed `messages` roles/content, a `tool_result` referencing an unknown `tool_use_id`) -> "ERR_PAYLOAD_INVALID" (400, Anthropic-shaped `invalid_request_error`).
  - R2: neither `x-api-key` nor a `Bearer` `Authorization` header is present, or the credential is invalid/revoked -> "ERR_AUTH_INVALID_KEY" (401, Anthropic-shaped `authentication_error`) — identical trigger set/opacity as today's `/v1/chat/completions` 401 (no enumeration hint).
  - R3: `model` does not resolve to any active catalog row / preset / alias the tenant is permitted to use -> "ERR_MODEL_UNKNOWN" | "ERR_MODEL_NOT_ALLOWED" (400/403 per existing mapping, Anthropic-shaped `invalid_request_error`/`permission_error`).
  - R4: budget / credit / rate-limit / tier-capacity / residency gate rejects -> the SAME ERR_* code the OpenAI-wire path already raises, re-wrapped Anthropic-shaped, SAME HTTP status (e.g. 429 budget/rate-limit → `rate_limit_error`; 403 residency refusal → `permission_error`) — applies identically to `/v1/messages` AND `/v1/messages/count_tokens` (Freeze decision 2026-07-14: no governance carve-out for count_tokens; only the $0-billing/no-usage-row outcome differs on the SUCCESS path).
  - R5: a guardrail blocks the request pre-call -> the SAME ERR_* guardrail code, Anthropic-shaped `invalid_request_error`, refused BEFORE any upstream dial (fail-closed, zero egress — mirrors the MCP/residency refuse-not-reroute idiom).
  - R6: mid-stream upstream failure (5xx/timeout/circuit-open) -> "ERR_UPSTREAM_UNAVAILABLE" surfaced as a terminal Anthropic-shaped SSE `error` event (`api_error` type) + stream close — never a bare disconnect.
  - R7: `thinking.budget_tokens` present but the resolved provider is non-Anthropic -> NOT a reject; degrades per M7 (field dropped, request still served) — listed here only to make explicit this is NOT reject-class behavior despite looking like an incompatibility.
</reject>
After:
<after>
  - `POST /v1/messages` and `POST /v1/messages/count_tokens` exist, registered in `main.py` alongside `proxy_router`/`embeddings_router` (a new `messages_router.py`, per-modality-file convention).
  - An Anthropic-wire client (Claude Agent SDK, or a future `claude-gateway-protocol-compat` dependent surface) can complete a real chat turn through Hydroa with the exact same governance guarantees as an OpenAI-wire client, and the resulting `usage_records` row is indistinguishable in shape/accuracy from one produced by `/v1/chat/completions` for equivalent token counts.
  - Both authn seams (`/internal/authz` ext_authz + the new in-process `/v1/messages` extraction) accept identical credentials and reject identical failures with identical opacity.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  RESOLVED (Freeze decision, Tin 2026-07-14): count_tokens' governance path — decided AGAINST the draft's exemption. count_tokens now passes the FULL admission path (authn, model-check, budget, rate-limit, plan/credit gates) exactly like `/v1/messages`, billed $0, zero usage rows. The milestone's "same governance path" invariant holds with zero carve-outs; no longer an open assumption (see M12).
  - [x] DECIDED-AT-FREEZE (Tin 2026-07-14): `message_start.usage.input_tokens` reporting 0/absent (rather than an accurate upfront count) — ACCEPTED as a disclosed degrade. No redesign; build proceeds on this basis.
  - [x] DECIDED-AT-FREEZE (Tin 2026-07-14): thinking/cache_control silently degrade (translate-through, field dropped, never a reject) for a request served by a non-Anthropic provider — ACCEPTED. No redesign; M7/M8 stand as drafted.
  - [x] DECIDED-AT-FREEZE (Tin 2026-07-14): new `messages_router.py` file (mirroring `embeddings_router.py`'s per-modality-file convention) rather than appending to `router.py` — ACCEPTED. §5 Scope reflects this.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Non-stream chat completion via Anthropic wire   # M1, M2, M4
  Given a valid tenant API key and an active catalog model
  When the client POSTs /v1/messages with {model, messages, max_tokens, stream: false (default)}
  Then the response is 200 with an Anthropic Messages body {id, type:"message", role:"assistant", content:[{type:"text",text}], model, stop_reason, usage:{input_tokens,output_tokens}}
  And the request was authenticated, catalog-checked, budget-checked, guardrail-checked, and recorded via the SAME CompletionUseCase.complete() path /v1/chat/completions uses

Scenario: Streaming chat completion emits the Anthropic SSE event sequence   # M1, M5
  Given a valid tenant API key and an active catalog model
  When the client POSTs /v1/messages with {model, messages, max_tokens, stream: true}
  Then the response is text/event-stream emitting message_start, then content_block_start/content_block_delta(text_delta)/content_block_stop, then message_delta, then message_stop, in that order
  And no OpenAI-shaped chunk (choices/delta/finish_reason) ever reaches the client

Scenario: Tool-use round trip translates tool_calls <-> tool_use   # M1, M4
  Given a request whose messages include an Anthropic tool_result block referencing a prior tool_use_id
  When the client POSTs /v1/messages non-streaming
  Then the translated internal request carries an OpenAI-shape tool message with matching tool_call_id
  And a response containing a tool call is translated back into a content block {type:"tool_use", id, name, input}

Scenario: Streaming tool-call arguments stream as input_json_delta   # M1, M5
  Given a streaming request that triggers a tool call
  When the internal OpenAI SSE stream emits tool_calls delta fragments
  Then the client receives content_block_start{type:"tool_use"} followed by content_block_delta{type:"input_json_delta"} fragments and a content_block_stop

Scenario: x-api-key authenticates identically to Authorization Bearer   # M3
  Given a valid tenant API key
  When the client POSTs /v1/messages with only an x-api-key header (no Authorization header)
  Then the request authenticates successfully, identically to sending the same key via Authorization: Bearer

Scenario: Authorization Bearer takes priority over x-api-key   # M3
  Given a valid key A sent via Authorization: Bearer and a different (invalid) value B sent via x-api-key on the same request
  When the client POSTs /v1/messages
  Then the request authenticates using key A and key B is never evaluated

Scenario: A non-Anthropic provider candidate still serves an Anthropic-wire request   # M6
  Given a tenant whose model/preset resolves the request to an OpenAI-native or Bedrock provider candidate
  When the client POSTs /v1/messages for that model
  Then the request is served normally (translate-through) and the response is translated back to Anthropic-wire
  And the request is never refused solely because it arrived Anthropic-wire

Scenario: Extended thinking honored on a direct-Anthropic-served request   # M7
  Given a request with thinking:{type:"enabled",budget_tokens:2048} whose model resolves to the direct Anthropic provider
  When the client POSTs /v1/messages
  Then the internal request forwarded to the Anthropic adapter carries the exact budget_tokens value unchanged

Scenario: Extended thinking silently dropped on a non-Anthropic-served request   # M7
  Given a request with thinking:{type:"enabled",budget_tokens:2048} whose model resolves to a Bedrock or OpenAI-native candidate
  When the client POSTs /v1/messages
  Then the request is served successfully with the thinking field omitted from the internal request
  And no error is raised solely due to the dropped thinking field

Scenario: cache_control breakpoint carried through only to the Anthropic adapter   # M8
  Given a request with cache_control on a system block whose model resolves to the direct Anthropic provider
  When the client POSTs /v1/messages
  Then the cache_control marker reaches the Anthropic adapter's outbound request unchanged

Scenario: Malformed request body is rejected Anthropic-shaped   # R1
  Given a request body missing the required max_tokens field
  When the client POSTs /v1/messages
  Then the response is 400 with body {"type":"error","error":{"type":"invalid_request_error","message":...}} and code ERR_PAYLOAD_INVALID
  And no governance check beyond payload validation ran, and no upstream dial occurred

Scenario: Missing credential is rejected Anthropic-shaped   # R2
  Given a request with neither an x-api-key nor an Authorization header
  When the client POSTs /v1/messages
  Then the response is 401 with body {"type":"error","error":{"type":"authentication_error","message":...}} and code ERR_AUTH_INVALID_KEY
  And the SAME 401 shape/opacity is produced regardless of whether the key is missing, malformed, unknown, or revoked

Scenario: Invalid credential produces the SAME opaque 401 as /v1/chat/completions   # R2
  Given a revoked or unknown API key sent as x-api-key
  When the client POSTs /v1/messages
  Then the response is 401 ERR_AUTH_INVALID_KEY, identical in shape to the equivalent /v1/chat/completions failure
  And no detail is exposed that would help enumerate valid credential ids

Scenario: Unknown model is rejected Anthropic-shaped   # R3
  Given a request naming a model absent from the catalog
  When the client POSTs /v1/messages
  Then the response is 400 with error.type "invalid_request_error" and code ERR_MODEL_UNKNOWN
  And no upstream dial occurred, no usage_records row was written

Scenario: Budget-exhausted key is rejected Anthropic-shaped with the SAME code as OpenAI-wire   # R4
  Given a tenant whose per-key budget is already exhausted
  When the client POSTs /v1/messages
  Then the response is 429 with error.type "rate_limit_error" and the SAME ERR_* budget code /v1/chat/completions raises for the identical tenant state
  And no upstream dial occurred, no usage_records row was written

Scenario: Guardrail-blocked request is refused before any upstream dial   # R5
  Given a request whose content trips a configured pre-call guardrail
  When the client POSTs /v1/messages
  Then the response is 400 Anthropic-shaped invalid_request_error carrying the guardrail's ERR_* code
  And zero egress dials were made to any upstream provider (fail-closed)

Scenario: Mid-stream upstream failure terminates with an Anthropic-shaped terminal error event   # R6
  Given a streaming request where the upstream provider fails (5xx/timeout) after the stream has started
  When the failure occurs mid-stream
  Then the client receives a terminal SSE event {"type":"error","error":{"type":"api_error","message":...}} and the stream closes
  And the client is never left hanging on a silent truncation

Scenario: Anthropic-native mid-stream error event is translated, not passed through raw   # R6
  Given a streaming request routed to the direct Anthropic provider that itself emits a native "error" SSE event mid-stream (HTTP stays 200)
  When the event arrives
  Then it is translated into the SAME Anthropic-shaped terminal error event contract (not re-emitted byte-for-byte from the raw upstream frame) and the stream closes

Scenario: Billing parity — token counts and cost match the equivalent OpenAI-wire call   # M11
  Given two functionally identical prompts sent once via /v1/chat/completions and once via /v1/messages for the same model/tenant
  When both complete successfully
  Then both produce exactly one usage_records row each, with matching prompt/completion token counts and matching cost_usd computed via the same rate-card resolver
  And neither call produces more or fewer than one row (no double-count, no missing row)

Scenario: Healthy tenant calls count_tokens — full governance path, $0 billed   # M12 (Freeze decision 2026-07-14)
  Given a valid tenant API key, an active catalog model, sufficient budget/credit, and a key that is not rate-limited
  When the client POSTs /v1/messages/count_tokens with {model, messages}
  Then the response is 200 {"input_tokens": <int>}
  And the request passed the SAME admission gates as /v1/messages (authn, model-check, budget, rate-limit, plan/credit) — none were skipped or exempted
  And no usage_records row was written and no charge was made (the $0-billed outcome is a SUCCESS-path property, not a governance skip)

Scenario: Over-budget tenant calls count_tokens — structured refusal, zero usage rows   # M12, R4 (Freeze decision 2026-07-14)
  Given a tenant whose per-key or team budget is already exhausted
  When the client POSTs /v1/messages/count_tokens
  Then the response is a structured 4xx Anthropic-shaped refusal (the SAME ERR_* budget code /v1/messages would raise for the identical tenant state)
  And zero usage_records rows were written and no upstream/token-counting work was performed

Scenario: Rate-limited key calls count_tokens — 429 with Retry-After parity   # M12, R4 (Freeze decision 2026-07-14)
  Given a tenant whose API key has exceeded its configured rate limit
  When the client POSTs /v1/messages/count_tokens
  Then the response is 429 Anthropic-shaped rate_limit_error, carrying the SAME Retry-After semantics /v1/messages already returns for a rate-limited request
  And zero usage_records rows were written

Scenario: count_tokens still enforces authn and model existence   # M12
  Given a request to /v1/messages/count_tokens naming a model absent from the catalog
  When the client POSTs the request
  Then the response is 400 ERR_MODEL_UNKNOWN, Anthropic-shaped
  And this is one instance of the general rule (M12): count_tokens never skips any admission gate, only the billing/usage-record OUTCOME on success differs from /v1/messages

Scenario: Duplicate/consecutive tool-result messages collapse correctly (boundary)   # M1
  Given an Anthropic request whose messages include two consecutive tool_result blocks for two different tool_use_ids
  When translated to the internal OpenAI-shape request
  Then both tool results appear as separate tool-role messages with their respective tool_call_id, in original order

Scenario: Empty/whitespace-only text content block (edge case)   # M1
  Given an Anthropic request whose message content is a text block with an empty string
  When the client POSTs /v1/messages
  Then the request is NOT rejected for emptiness alone (payload validation only enforces required top-level fields, not content length) and translation proceeds

Scenario: Client disconnects mid-stream before message_stop (partial failure, boundary)   # M11
  Given a streaming request that has emitted at least one content_block_delta
  When the client disconnects before message_stop is reached
  Then the partial usage is still recorded via the SAME disconnect-billing path /v1/chat/completions streaming already uses (usage_source reflecting the disconnect, never a silent $0 with no attempt at recovery)

Scenario: Concurrent requests on the same API key do not cross-contaminate governance state   # M2 (concurrency)
  Given two concurrent /v1/messages requests on the same API key, one of which would exhaust the per-key budget
  When both requests are in flight simultaneously
  Then budget/credit hold accounting resolves the same way it would for two concurrent /v1/chat/completions requests (no double-spend, no phantom availability) — the SAME concurrency-safe hold/settle mechanism, not a parallel one
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /v1/messages
  body: {
    model: string, messages: [{role: "user"|"assistant", content: string | [Block]}],
    max_tokens: int,                                    # required (Anthropic contract)
    system?: string | [{type:"text", text, cache_control?}],
    tools?: [{name, description?, input_schema}], tool_choice?: {type:"auto"|"any"|"tool"|"none", name?},
    stop_sequences?: [string], temperature?: number, top_p?: number,
    thinking?: {type:"enabled", budget_tokens: int},
    stream?: bool, metadata?: {user_id?: string}
  }
  # Block = {type:"text",text,cache_control?} | {type:"tool_use",id,name,input} | {type:"tool_result",tool_use_id,content}
  200 (stream=false) -> {
    id: string, type:"message", role:"assistant",
    content: [Block],                                   # text block(s) and/or tool_use block(s)
    model: string, stop_reason: "end_turn"|"max_tokens"|"stop_sequence"|"tool_use",
    stop_sequence: string | null,
    usage: {input_tokens: int, output_tokens: int, cache_read_input_tokens?: int, cache_creation_input_tokens?: int}
  }
  200 (stream=true) -> text/event-stream:
    event: message_start        data: {type:"message_start", message:{id,type:"message",role,content:[],model,usage}}
    event: content_block_start  data: {type:"content_block_start", index, content_block: {type:"text",text:""} | {type:"tool_use",id,name,input:{}}}
    event: content_block_delta  data: {type:"content_block_delta", index, delta: {type:"text_delta",text} | {type:"input_json_delta",partial_json}}
    event: content_block_stop   data: {type:"content_block_stop", index}
    event: message_delta        data: {type:"message_delta", delta:{stop_reason,stop_sequence}, usage:{output_tokens}}
    event: message_stop         data: {type:"message_stop"}
    event: error (terminal, any point) data: {type:"error", error:{type:"api_error"|"overloaded_error"|<mapped>, message}}
  4xx -> { "type":"error", "error": {"type": "<anthropic_error_type>", "message": "<m>"} }
    400 invalid_request_error  -> "ERR_PAYLOAD_INVALID" | "ERR_MODEL_UNKNOWN"
    401 authentication_error   -> "ERR_AUTH_INVALID_KEY"
    403 permission_error       -> "ERR_MODEL_NOT_ALLOWED" | residency/tier ERR_* (per existing mapping)
    429 rate_limit_error       -> budget/rate-limit ERR_* (per existing mapping)
    502 api_error              -> "ERR_UPSTREAM_UNAVAILABLE"
  # HTTP status is IDENTICAL to whatever /v1/chat/completions would raise for the same governance
  # failure; only the response envelope shape changes (Anthropic error type/message vs. problem+json).

POST /v1/messages/count_tokens
  body: {
    model: string, messages: [{role, content: string | [Block]}],
    system?: string | [{type:"text",text}], tools?: [{name, description?, input_schema}],
    tool_choice?: {...}, thinking?: {type:"enabled", budget_tokens: int}
  }                                                       # NOTE: no max_tokens, no stream
  200 -> { "input_tokens": int }
  4xx -> { "type":"error", "error": {"type":"invalid_request_error"|"authentication_error"|"permission_error"|"rate_limit_error", "message":"<m>"} }
    400 -> "ERR_PAYLOAD_INVALID" | "ERR_MODEL_UNKNOWN"
    401 -> "ERR_AUTH_INVALID_KEY"
    403 -> "ERR_MODEL_NOT_ALLOWED" | residency ERR_* (per existing mapping)
    429 -> budget / rate-limit ERR_* (per existing mapping), Retry-After parity with /v1/messages
  # Freeze decision (Tin 2026-07-14): full governance path, $0-billed, no usage record. count_tokens
  # passes the SAME admission gates as /v1/messages (authn, model-check, budget, rate-limit,
  # plan/credit) — an over-budget/over-limit/no-credit tenant gets the identical structured 4xx a
  # chat call would get. On SUCCESS ONLY: billed $0, writes NO usage_records row (it produces no
  # completion). This supersedes the earlier drafted governance-exemption framing; the milestone's
  # "same governance path" invariant now holds with zero carve-outs for this endpoint.

Schema: no new tables, no new columns. usage_records rows written via the existing RecordingUsageRecorder
  (apps/gateway/src/gateway/usage/application/recorder.py) — same columns, same usage_source vocabulary
  (frame|stream_fallback|client_disconnect|openrouter_recovered); a /v1/messages-served row is
  schema-indistinguishable from the equivalent /v1/chat/completions row (M11). count_tokens writes
  NO row (M12). Access pattern: same CompletionUseCase/CompletionUpstream/UsageRecorder ports
  (apps/gateway/src/gateway/proxy/domain/ports.py) — zero new ports, zero changed signatures.
```

Glossary deltas:
- `Anthropic-wire ingress`: the client-facing `/v1/messages` (+`count_tokens`) surface that accepts Anthropic Messages API request/response/SSE shapes and translates them, at the edge, into the SAME internal OpenAI-shape `CompletionUseCase` consumes — the reverse-direction counterpart to the existing egress `AnthropicCompletionUpstream` adapter.
- `Anthropic error envelope`: the `{"type":"error","error":{"type":<t>,"message":<m>}}` response shape `/v1/messages` returns on any rejection — a documented, precedented exception to the project-wide RFC 9457 problem+json convention (same class as the SCIM `/scim/v2/*` carve-out), never leaking a raw problem+json body to an Anthropic-wire client.

Status: DRAFT
Reported: no
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/proxy/api/messages_router.py` (new)
  `apps/gateway/src/gateway/proxy/api/messages_deps.py` (new — mirrors embeddings_deps.py)
  `apps/gateway/src/gateway/proxy/infrastructure/anthropic_ingress.py` (new — bidirectional translator: request/response/SSE-stepper/error-envelope)
  `apps/gateway/src/gateway/proxy/api/deps.py` (additive: new `get_raw_key_ingress`-style dependency mirroring `_extract_raw_key`'s priority; do NOT change `get_raw_api_key`'s existing behavior)
  `apps/gateway/src/gateway/main.py` (additive: import + `app.include_router(messages_router)`)
  `apps/gateway/tests/anthropic_messages_ingress/` (new test dir)

Strategy (ordered batches):
  1. `anthropic_ingress.py` request-translation (Anthropic request -> internal OpenAI-shape body): mirror-in-reverse `_openai_to_anthropic_request`'s field vocabulary (system/tool_use/tool_result/tools/tool_choice/stop_sequences/cache_control/thinking); unit-test against the same fixtures class as `anthropic_upstream.py`'s existing egress tests, run in isolation from governance.
  2. `messages_router.py` non-stream endpoint wired to `get_completion_use_case`/`get_completion_upstream`/`get_usage_recorder` UNCHANGED (reuse `deps.py` verbatim) + the new x-api-key-aware raw-key dependency; response translation (`_anthropic_ingress_response` mirroring `_anthropic_to_openai` in reverse) + error-envelope translation (catch `ProblemError`, map ERR_* -> Anthropic error type).
  3. Streaming: an `_OpenAIToAnthropicSSEStepper` (mirrors `_AnthropicSSEStepper`'s one-event-at-a-time design) consuming the internal SSE byte stream `use_case.stream()` yields and emitting Anthropic event frames; wire into `messages_router.py`'s stream branch.
  4. `count_tokens` endpoint (Freeze decision 2026-07-14: FULL governance path, $0-billed): reuse the SAME `CompletionUseCase`-adjacent admission sequence as `/v1/messages` — authn, `_check_model_catalog`, rate limit, team/per-key budget, credit/plan gates — all UNCHANGED; only skip the actual upstream dial and the terminal `usage_recorder.record(...)` call (produces no completion, so nothing to bill or record). Token estimate via a documented, simple heuristic (no new third-party tokenizer dependency unless already vendored — check `pyproject.toml` first).
  5. Cross-cutting: thinking/cache_control conditional forwarding gated on which adapter `model_router`/`provider_resolver` actually selects (mirrors the existing `chat_modality_lookup`/`provider_resolver` getattr pattern in `deps.py` — do not invent a new resolution mechanism).

Persona (required): generic — no existing `.add/personas/` file matches a protocol-translation/wire-compat domain stance yet; recommend `add-persona` draft one (e.g. "protocol-translation-engineer") before BUILD if this milestone's remaining tasks (claude-gateway-protocol-compat) would reuse it.
Spawn isolation (default): worktree (multi-file new-surface build; not a shared-tree exception).
Known-problem fixes:
  - trap: re-implementing DI inside `messages_router.py` instead of reusing `get_completion_use_case` -> silently drops optional governance (budget/tier/residency/guardrails) for Anthropic-wire traffic. Fix: import and call the SAME `deps.py` functions, zero duplication.
  - trap: leaking a raw problem+json body to an Anthropic-wire client on an unhandled exception. Fix: wrap the endpoint body in a single translation boundary that catches `ProblemError` (and any uncaught exception) and ALWAYS re-emits the Anthropic error envelope before returning.
  - trap: overloading `usage_records.usage_source` with an ingress-dialect value. Fix: never pass a new `usage_source` value from the ingress path; let `RecordingUsageRecorder` default exactly as it does for `/v1/chat/completions`.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned")>
Safety rule (feature-specific): the request-translation step MUST run entirely before any governance call — a translation exception (e.g. `tool_call_id_required`-equivalent) must raise the Anthropic-shaped ERR_PAYLOAD_INVALID BEFORE `_authenticate`/budget-hold ever runs, so a malformed body never reaches (or partially consumes) a budget hold.
Code lives in: `apps/gateway/src/gateway/proxy/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear; do NOT modify `anthropic_upstream.py`'s existing egress translation functions (frozen contract, separate surface) — only mirror their vocabulary in the new ingress module.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
