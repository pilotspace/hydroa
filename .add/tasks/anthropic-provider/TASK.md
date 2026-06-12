# TASK: Anthropic Messages API chat adapter: translate OpenAI chat <-> Anthropic /messages (request, response, SSE stream, usage, errors); register as provider='anthropic' chat adapter

slug: anthropic-provider · created: 2026-06-13 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: an `AnthropicCompletionUpstream` chat adapter implementing the EXISTING
`CompletionUpstream` Protocol (`complete(payload)->(status,body)` + `stream(payload)->
AsyncIterator[bytes]`), translating an OpenAI chat-completions request → the Anthropic
Messages API (`POST {base_url}/messages`) and the native response → an OpenAI-shaped
response, for BOTH non-stream and streaming SSE, with usage + error mapping. It registers
in the v9 `_chat_adapters` map under key "anthropic" (wired only when the Anthropic key is
set). Dispatch to it is the frozen provider-chat-dispatch seam — UNCHANGED here. Anthropic
has NO first-party embeddings → chat only.

Framings weighed:
  - **A dedicated AnthropicCompletionUpstream adapter that owns the full translation
    (chosen)**: mirrors OpenRouterCompletionUpstream's structure (httpx.AsyncClient +
    CircuitBreaker + timeouts + UpstreamUnavailableError on 5xx/transport), but its
    complete()/stream() translate OpenAI⇄Anthropic. Pure translation helpers
    (request/response/SSE-event/usage/error) are module-level functions, unit-testable
    without a network. Drops cleanly into the frozen `_chat_adapters` dict.
  - **A thin translator wrapped around the existing OpenRouter upstream (rejected)**:
    OpenRouter speaks OpenAI already; Anthropic needs a different base_url, auth header
    scheme (x-api-key + anthropic-version, NOT Bearer), endpoint (/messages not
    /chat/completions), and wire shapes — nothing of the OpenRouter upstream is reusable
    beyond the breaker pattern, which we re-instantiate.
  - **Translate in the dispatch wrapper (rejected)**: the frozen provider-chat-dispatch
    contract says the wrapper does SELECTION ONLY; translation belongs inside the adapter.

Must:
<must>
  - REQUEST translation (OpenAI chat → Anthropic Messages body):
    * `messages` with role="system" are LIFTED to the Anthropic top-level `system` string
      (multiple system messages joined with "\n\n"); remaining user/assistant messages map
      1:1 to Anthropic `messages:[{role, content}]` with `content` as the text string.
    * `max_tokens`: Anthropic REQUIRES it; OpenAI's is optional → use the request's
      max_tokens if present, else the configured default `anthropic_default_max_tokens`
      (Settings, default 4096).
    * pass through when present: `temperature`, `top_p`; OpenAI `stop` (str|list) →
      Anthropic `stop_sequences` (list); `model` passes through verbatim (the catalog model
      id IS the Anthropic model name — no rename).
    * `stream`: false/absent for complete(); true for stream().
    * auth: header `x-api-key: <anthropic_api_key>` + `anthropic-version: <anthropic_version>`
      (default "2023-06-01") + `content-type: application/json`. NEVER an Authorization Bearer.
  - RESPONSE translation (Anthropic 200 → OpenAI chat.completion), complete():
    * `id` passes through; `object`="chat.completion"; `created`=int(time.time()); `model`
      = Anthropic response `model`.
    * `content`: concatenate the `text` of every `content[]` block with type=="text"
      (non-text blocks ignored — tool_use is out of scope) → single assistant message string.
    * `choices`=[{index:0, message:{role:"assistant", content:<text>}, finish_reason:<mapped>}].
    * finish_reason map: end_turn→"stop", max_tokens→"length", stop_sequence→"stop",
      tool_use→"tool_calls", null/unknown→"stop".
    * `usage`: input_tokens→prompt_tokens, output_tokens→completion_tokens,
      total_tokens=prompt+completion. (Billing keys on these — v6 invariant.)
  - STREAM translation (Anthropic SSE events → OpenAI chat.completion.chunk SSE bytes):
    * On `message_start`: emit one chunk with `choices:[{index:0,delta:{role:"assistant"},
      finish_reason:null}]`; capture `usage.input_tokens` as prompt_tokens.
    * On each `content_block_delta` with `delta.type=="text_delta"`: emit a chunk with
      `choices:[{index:0,delta:{content:<text>},finish_reason:null}]`.
    * On `message_delta`: capture `delta.stop_reason` (mapped) + `usage.output_tokens`.
    * Terminal: emit ONE final chunk carrying BOTH `choices:[{index:0,delta:{},
      finish_reason:<mapped>}]` AND `usage:{prompt_tokens,completion_tokens,total_tokens}`
      (the gateway's SSE usage-extractor reads the LAST data frame with a usage dict — this
      frame is how streamed Anthropic calls get billed), THEN `data: [DONE]`.
    * `ping` and unrecognized events are ignored (no chunk emitted).
    * Each emitted frame is `b"data: " + json + b"\n\n"`; the closer is `b"data: [DONE]\n\n"`.
  - RESILIENCE (mirror OpenRouter upstream, no retry needed for v9): own httpx.AsyncClient
    (connect 10s, non-stream read 120s, stream read 300s) + a per-instance CircuitBreaker;
    breaker.guard() before each attempt; on Anthropic 5xx or transport timeout/network error
    → raise UpstreamUnavailableError (→ the gateway maps to 502 / triggers v8 fallback);
    on success record_success(). complete() needs NO retry loop (keep it single-attempt).
  - SECURITY: the Anthropic api key is a SECRET — never logged, echoed, committed, or placed
    in a metric label / span attribute / exception message. Settings knobs are additive and
    already exist from provider-chat-dispatch (`anthropic_api_key`, `anthropic_base_url`,
    `anthropic_version`); this task ADDS `anthropic_default_max_tokens: int = 4096`.
  - WIRING (composition root, main.py only): when `settings.anthropic_api_key` is non-empty,
    construct an AnthropicCompletionUpstream and add it to `_chat_adapters["anthropic"]`
    BEFORE building ProviderAwareCompletionUpstream. Empty key → adapter absent → models with
    provider="anthropic" dispatch-fallback to openrouter (the frozen fail-safe). The v8 router,
    use case, billing, and the openrouter/openai paths stay BYTE-IDENTICAL.
</must>
Reject:
<reject>
  - Anthropic responds 5xx / connect timeout / read timeout / network error -> raise
    UpstreamUnavailableError (gateway → 502 ERR_UPSTREAM_UNAVAILABLE / v8 fallback); never a
    partial/garbage OpenAI body.
  - Anthropic responds 4xx (e.g. 400 invalid_request, 401 authentication_error, 429
    rate_limit) -> complete() PASSES THE STATUS THROUGH and returns an OpenAI-shaped error
    body `{error:{message,type,code}}` mapped from the Anthropic `{type:"error",error:{type,
    message}}` envelope (no exception; the gateway forwards the status). type map:
    invalid_request_error→"invalid_request_error", authentication_error→"authentication_error",
    rate_limit_error→"rate_limit_error", others→"upstream_error".
  - empty `anthropic_api_key` at wiring time -> adapter NOT registered (absent from the map);
    NEVER construct an adapter that would send `x-api-key:` empty (the v7 empty-bearer lesson).
  - a request whose only messages are role="system" (no user turn) -> still translated; if
    Anthropic 400s, that 4xx passes through unchanged (we do not invent a user turn).
</reject>
After:
<after>
  - a catalog model with provider="anthropic" called via /v1/chat/completions returns an
    OpenAI-shaped chat.completion (non-stream) or chat.completion.chunk SSE stream, billed on
    the served model id with prompt/completion tokens mapped from Anthropic input/output_tokens.
  - the openrouter + openai + embeddings paths are byte-identical to v8/v9-task-1.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The Anthropic SSE event sequence + field names (message_start.usage.input_tokens,
    content_block_delta.delta.text_delta.text, message_delta.delta.stop_reason +
    message_delta.usage.output_tokens, message_stop) are pinned from the documented Messages
    API streaming format — lowest confidence because they are validated here against
    DOCUMENTED fixtures, not a live key; if a real field name differs, the streaming usage/
    finish_reason mapping breaks (cost: streamed Anthropic calls bill 0 tokens or mis-finish).
    Mitigation: provider-breadth-live-verify (task 4) replays a recorded Anthropic stream
    through the TLS edge; any drift is caught there before the milestone closes.
  - [ ] max_tokens default 4096 when the OpenAI request omits it — reasonable; if a tenant
    expected unbounded, output truncates. Confirmed acceptable (Anthropic mandates the field).
  - [ ] model id passes through verbatim (catalog id == Anthropic model name) — matches the
    OpenRouter convention; a prefixed alias would need stripping (deferred; not in scope).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: non-stream request translated to Anthropic and response back to OpenAI
  Given an OpenAI chat request {model:"claude-x", messages:[{role:system,content:"S"},{role:user,content:"Hi"}], max_tokens:100}
  When complete() runs against an Anthropic stub returning a text message with usage{input_tokens:10,output_tokens:5}
  Then the POSTed Anthropic body has system=="S", messages==[{role:user,content:"Hi"}], max_tokens==100
  And the returned (status,body) is (200, OpenAI chat.completion) with choices[0].message.content=="<text>" and finish_reason=="stop"
  And body.usage == {prompt_tokens:10, completion_tokens:5, total_tokens:15}

Scenario: max_tokens defaulted when OpenAI request omits it
  Given an OpenAI chat request with no max_tokens
  When complete() builds the Anthropic body
  Then the Anthropic body max_tokens == anthropic_default_max_tokens (4096)

Scenario: auth headers use x-api-key and anthropic-version (never Bearer)
  Given anthropic_api_key="sk-ant-xxx" and anthropic_version="2023-06-01"
  When any request is POSTed
  Then headers contain x-api-key=="sk-ant-xxx" and anthropic-version=="2023-06-01"
  And no Authorization header is present

Scenario: finish_reason mapping
  Given Anthropic stop_reason in {end_turn, max_tokens, stop_sequence, tool_use}
  When complete() translates the response
  Then OpenAI finish_reason is {stop, length, stop, tool_calls} respectively

Scenario: streaming SSE translated to OpenAI chunks with terminal usage frame
  Given an Anthropic event stream (message_start, content_block_delta x2, message_delta, message_stop) with input_tokens:10 output_tokens:5
  When stream() is drained
  Then the first chunk delta=={role:"assistant"}
  And the content chunks carry delta.content for each text_delta in order
  And the LAST data frame before [DONE] has finish_reason=="stop" AND usage{prompt_tokens:10,completion_tokens:5,total_tokens:15}
  And the final bytes are "data: [DONE]\n\n"

Scenario: Anthropic 5xx raises UpstreamUnavailableError (fallback)
  Given the Anthropic stub returns 503
  When complete() runs
  Then UpstreamUnavailableError is raised
  And the v8 router/openrouter path is unchanged (adapter raises; dispatch does not swallow)

Scenario: Anthropic 4xx error envelope passed through as OpenAI error body
  Given the Anthropic stub returns 400 {type:"error",error:{type:"invalid_request_error",message:"bad"}}
  When complete() runs
  Then it returns (400, {error:{message:"bad",type:"invalid_request_error",code:"invalid_request_error"}})
  And no exception is raised (the gateway forwards the status)

Scenario: empty api key -> adapter not wired (dispatch-fallback to openrouter)
  Given settings.anthropic_api_key == ""
  When create_app() builds _chat_adapters
  Then "anthropic" is absent from the adapter map
  And a model with provider="anthropic" dispatch-falls-back to the openrouter adapter (no x-api-key:"" call)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
NEW class AnthropicCompletionUpstream  (proxy/infrastructure/anthropic_upstream.py)
  implements CompletionUpstream (the EXISTING Protocol — no new protocol).
  __init__(self, *, api_key: str, base_url: str = "https://api.anthropic.com/v1",
           anthropic_version: str = "2023-06-01", default_max_tokens: int = 4096,
           metrics_registry: MetricsRegistry | None = None) -> None
    - builds its own httpx.AsyncClient(base_url, timeout connect=10/read=120/write=120/pool=10)
      and a per-instance CircuitBreaker. api_key stored privately; NEVER logged.

  async def complete(payload) -> tuple[int, dict]:
    breaker.guard(); POST {base_url}/messages with translated body + auth headers.
    - transport ConnectError/ConnectTimeout/PoolTimeout/ReadTimeout/WriteTimeout/NetworkError
      -> breaker.on_upstream_error(); raise UpstreamUnavailableError
    - resp 5xx (>=500) -> breaker.on_upstream_error(); raise UpstreamUnavailableError
    - resp 4xx          -> breaker.record_success(); return (status, _anthropic_error_to_openai(body))
    - resp 200          -> breaker.record_success(); return (200, _anthropic_to_openai(body))

  def stream(payload) -> AsyncIterator[bytes]:   # sync def returning inner async-gen (idiom)
    breaker.guard(); inside _gen(): client.stream POST /messages (read=300);
      5xx before first byte -> on_upstream_error + raise UpstreamUnavailableError;
      else record_success and translate the Anthropic event stream → OpenAI chunk bytes
      (terminal frame carries finish_reason + usage, then b"data: [DONE]\n\n").
    transport TimeoutException/NetworkError mid-stream -> on_upstream_error + raise.

Pure translation helpers (module-level, no I/O — unit-tested directly):
  _openai_to_anthropic_request(payload, *, default_max_tokens) -> dict
    { model, system?, messages:[{role,content}], max_tokens, temperature?, top_p?,
      stop_sequences?, stream? }
  _anthropic_to_openai(body) -> dict   # chat.completion (non-stream)
    { id, object:"chat.completion", created:int, model,
      choices:[{index:0, message:{role:"assistant", content}, finish_reason}],
      usage:{prompt_tokens, completion_tokens, total_tokens} }
  _anthropic_error_to_openai(body) -> dict
    { error:{ message, type, code } }   # mapped from {type:"error",error:{type,message}}
  _map_finish_reason(stop_reason: str|None) -> str
    end_turn->stop · max_tokens->length · stop_sequence->stop · tool_use->tool_calls · _->stop
  _translate_anthropic_sse(events: iterable[ (event_name, json_obj) ]) -> iterable[bytes]
    message_start -> first chunk delta{role:assistant} (capture input_tokens)
    content_block_delta(text_delta) -> chunk delta{content:text}
    message_delta -> capture stop_reason + output_tokens
    message_stop / end -> terminal chunk {choices:[{delta:{},finish_reason}], usage:{...}} + [DONE]
  OpenAI chunk shape: { id, object:"chat.completion.chunk", created:int, model,
                        choices:[{index:0, delta:{...}, finish_reason:null|str}] }

HTTP wire (Anthropic Messages API):
  POST {base_url}/messages
    headers: x-api-key:<api_key> · anthropic-version:<version> · content-type:application/json
    body (non-stream): { model, system?, messages, max_tokens, temperature?, top_p?, stop_sequences?, stream:false? }
    200 -> { id, type:"message", role:"assistant", model, content:[{type:"text",text}],
             stop_reason, stop_sequence, usage:{input_tokens, output_tokens} }
    4xx -> { type:"error", error:{ type, message } }   (passed through with its status)
    5xx / transport -> UpstreamUnavailableError
    stream:true -> SSE events: message_start, content_block_start, content_block_delta,
                   content_block_stop, message_delta, message_stop (+ ping ignored)

Settings (additive): anthropic_default_max_tokens: int = 4096
  (anthropic_api_key / anthropic_base_url / anthropic_version already exist from
   provider-chat-dispatch §3). Secret handling unchanged: api_key NEVER logged/echoed/committed.

Wiring (main.py, composition root ONLY):
  if settings.anthropic_api_key:
      _chat_adapters["anthropic"] = AnthropicCompletionUpstream(
          api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url,
          anthropic_version=settings.anthropic_version,
          default_max_tokens=settings.anthropic_default_max_tokens,
          metrics_registry=app.state.metrics_registry)
  built BEFORE ProviderAwareCompletionUpstream. v8 router / use case / openrouter / openai
  / embeddings paths UNCHANGED + byte-identical.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)

Least-sure flag surfaced at freeze: [spec/contract] the Anthropic STREAMING event field
names + sequence (message_start.message.usage.input_tokens, content_block_delta.delta.text,
message_delta.delta.stop_reason + message_delta.usage.output_tokens) — pinned from the
documented Messages API streaming format and validated here against DOCUMENTED fixtures, not
a live key. If a field differs in production, streamed Anthropic calls would mis-bill or
mis-finish. Cost: bounded — caught by provider-breadth-live-verify (task 4) replaying a
recorded Anthropic stream through the TLS edge before the milestone closes; the non-stream
path (simpler, higher-confidence) is unaffected. The freeze deliberately pins THIS adapter's
translation only (Gemini is its own task), per the v2 fixture-grounded-per-provider lesson.
<!-- Approved -> Status: FROZEN @ vN. Changing a frozen contract = change request back to SPECIFY. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% of the new module (translation helpers + adapter paths).
Plan (one test per scenario, asserting behavior via a fake httpx transport — no network):
<test_plan>
  - test_request_translation_system_lift: system message → top-level system; user stays in messages; max_tokens threaded
  - test_max_tokens_defaulted: omit max_tokens → Anthropic body max_tokens == 4096
  - test_auth_headers_x_api_key_no_bearer: x-api-key + anthropic-version present; no Authorization
  - test_response_translation_non_stream: Anthropic 200 → OpenAI chat.completion; content joined; usage mapped
  - test_finish_reason_mapping: end_turn/max_tokens/stop_sequence/tool_use → stop/length/stop/tool_calls
  - test_stream_translation_chunks_and_terminal_usage: events → first role chunk, content chunks in order, terminal frame has finish_reason+usage, ends with [DONE]
  - test_stream_usage_extractable_by_gateway_extractor: extract_usage_from_sse(translated chunks) == {prompt_tokens,completion_tokens,total_tokens} (cross-check the FROZEN extractor)
  - test_5xx_raises_upstream_unavailable: stub 503 → UpstreamUnavailableError
  - test_4xx_error_envelope_passthrough: stub 400 anthropic error → (400, {error:{message,type,code}}); no raise
  - test_complete_protocol_shape: returns (int, dict); satisfies CompletionUpstream structurally
  - test_wiring_anthropic_present_when_key_set: create_app(anthropic_api_key set) → _chat_adapters has AnthropicCompletionUpstream (via app.state — see seam)
  - test_wiring_anthropic_absent_when_key_empty: create_app(anthropic_api_key="") → no "anthropic" adapter; provider="anthropic" dispatch-falls-back to openrouter
</test_plan>

Tests live in: `./tests/` (`apps/gateway/tests/anthropic_provider/`) · MUST run red (missing
module `gateway.proxy.infrastructure.anthropic_upstream`) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 609 passed -m 'not e2e'; target anthropic_provider suite 16/16 (15 from the
      red plan + 1 added hardening "realistic recorded stream" drift detector)
- [x] coverage did not decrease — 82.20% TOTAL (≥80 floor; was 82.02% at v9-task-1 close); new
      module exercised by helper + MockTransport + wiring tests
- [x] no test or contract was altered to weaken it — §3 contract UNTOUCHED; the red suite was only
      ADDED to (one new strengthening test); no frozen v6/v7/v8/v9-task-1 test edited
- [x] concurrency / timing safe — adapter holds one httpx.AsyncClient + a per-instance CircuitBreaker
      (same model as OpenRouter upstream); no shared mutable state across requests; complete() is
      single-attempt (no retry loop); stream() guards the breaker before opening the stream.
      KNOWN non-blocking caveat (see §7): stream() BUFFERS the full Anthropic event sequence before
      emitting OpenAI chunks → correct output + correct billing, but time-to-first-byte == full
      generation time (not incremental). Recorded as an open follow-up, not a gate blocker.
- [x] no exposed secrets / injection / unexpected deps — api key stored as self._api_key, used ONLY
      in _auth_headers() x-api-key; never logged/echoed/in exception (exceptions use str(transport_exc)
      or f"Upstream returned {int_status}"). Zero new third-party deps (httpx + stdlib json/time).
      Decision (with Tin, 2026-06-13): keep raw httpx over the official anthropic SDK — matches
      LiteLLM's own per-provider-httpx architecture + the uniform CircuitBreaker/timeout/fallback
      seam; avoids per-provider SDK dependency sprawl; wire/SSE drift caught by task-4 live-verify.
- [x] layering & dependencies follow CONVENTIONS.md — adapter in proxy/infrastructure; pure helpers
      module-level; no domain→infra import; wiring at composition root (main.py) only.
- [x] a person reviewed and approved — delegated auto mode (Tin Dang, 2026-06-13) + an explicit
      human steer on the SDK-vs-httpx fork (chose httpx); orchestrator manually reviewed the full
      adapter module (Rule 5) + re-ran the authoritative gate; security clean (no HARD-STOP).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — AnthropicCompletionUpstream imported main.py:42, conditionally registered
      main.py:383-390 (only when anthropic_api_key set), exposed via app.state.chat_adapters
      (main.py:394) and consumed by ProviderAwareCompletionUpstream through the frozen dispatch map;
      anthropic_default_max_tokens config.py:152. The 5 pure helpers are each referenced by the
      adapter and unit-tested directly.
- [x] DEAD-CODE (code) — no orphaned symbol; every helper + the class is wired and tested.
- [x] SEMANTIC — n/a (code task); orchestrator read the new module in full + the diff to main.py/config.py.

### GATE RECORD
Outcome: PASS
Evidence: 609 passed -m 'not e2e' · cov 82.20% (≥80) · ruff check + format clean · pyright 0 errors ·
          allowlist OK · anthropic target 16/16 · openrouter/openai/embeddings byte-identical (no
          frozen file edited) · api key never logged/echoed (manual review).
Reviewed by: Tin Dang (delegated auto mode + explicit SDK/httpx steer) · date: 2026-06-13 ·
          security: clean (no finding → no HARD-STOP)

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): Anthropic 5xx/transport rate (→ v8 fallback); 4xx error-type
distribution (authentication_error spikes = key/version drift); streamed-call usage==0 rate (would
signal SSE field-name drift); time-to-first-byte on streamed Anthropic calls (will be high until
the buffering caveat below is fixed).
Spec delta for the next loop: gemini-provider mirrors THIS adapter shape (own httpx client +
CircuitBreaker + pure translation helpers + terminal usage frame for the extractor) but for
generateContent/embedContent; the chat_adapters seam + dispatch are ready — Gemini just adds
`_chat_adapters["google"]` + a "google" UpstreamProvider for embeddings.

### Competency deltas
- [SDD · folded] First real schema-TRANSLATION surface (OpenAI⇄Anthropic) landed as a self-contained
  adapter with module-level pure helpers — non-stream + stream + usage + errors — proving the v9
  dispatch seam carries a non-OpenAI wire format end-to-end (evidence: 16/16 green incl. a realistic
  recorded-stream drift detector; extract_usage_from_sse cross-check passes). Reusable template for
  every future provider.
- [DDD · folded] Decision recorded: raw per-provider httpx translation OVER vendor SDKs — matches the
  LiteLLM parity target (its llms/anthropic is hand-rolled httpx) and keeps ONE resilience contract
  (CircuitBreaker/timeout/UpstreamUnavailableError/v8-fallback) across all upstreams; avoids
  per-provider SDK dependency sprawl (anthropic+google-genai+boto3+azure…). Applies to Gemini +
  all later providers (evidence: human steer 2026-06-13).
- [TDD · folded] FOLLOW-UP (non-blocking): stream() buffers the FULL Anthropic event sequence before
  emitting any OpenAI chunk → output + billing correct, but time-to-first-byte == full generation
  time (not incremental). The frozen `_translate_anthropic_sse(events)` helper (sync, consumes the
  whole iterable) shaped this; true incremental streaming needs a stateful per-event translator
  (process-one-event + finalize) without changing that frozen signature. Track for a streaming-
  latency hardening slice (applies to Gemini too). Evidence: anthropic_upstream.py stream() buffers
  into `events` then translates after the `async with` closes.
- [ADD · folded] The live-verify (task 4) is the designated catch for the freeze's least-sure flag
  (SSE field names validated against documented fixtures, not a live key) — the CI hardening test
  reduces but does not eliminate that risk; the milestone must not close until task-4's recorded-
  stream replay passes the TLS-edge double-pass.
