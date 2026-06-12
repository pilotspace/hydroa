# TASK: Google Gemini adapter: chat (generateContent + streamGenerateContent SSE) via the dispatch seam + embeddings (embedContent/batchEmbedContents) via the v7 UpstreamProvider seam; x-goog-api-key auth; OpenAI<->native translation

slug: gemini-provider · created: 2026-06-13 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Google Gemini provider on TWO seams. (A) CHAT — a `GeminiCompletionUpstream`
implementing the EXISTING `CompletionUpstream` Protocol, translating OpenAI chat ⇄ Gemini
`generateContent` (non-stream) + `streamGenerateContent?alt=sse` (stream), registered in the
v9 `_chat_adapters` map under "google". (B) EMBEDDINGS — a `GoogleEmbeddingsProvider`
implementing the EXISTING v7 `UpstreamProvider` Protocol (post_json/post_multipart/stream_bytes),
translating OpenAI `/v1/embeddings` ⇄ Gemini `embedContent` (single) / `batchEmbedContents`
(list), registered in the v7 `ProviderRegistry` under "google". Auth is the Gemini API key via
the `x-goog-api-key` HEADER (NOT a `?key=` query param — keeps the secret out of URLs/access
logs). Mirrors the anthropic-provider adapter shape (own httpx client + CircuitBreaker + pure
translation helpers + terminal usage frame for the SSE extractor). v8 router, billing, dispatch,
and openrouter/openai/anthropic paths stay BYTE-IDENTICAL.

Framings weighed:
  - **Two dedicated adapters, one per existing seam (chosen)**: chat goes through the v9
    dispatch map (CompletionUpstream); embeddings through the v7 registry (UpstreamProvider).
    Each owns its native translation internally. No new protocol, no use-case change.
  - **One mega-adapter implementing both protocols (rejected)**: CompletionUpstream and
    UpstreamProvider are distinct seams reached by distinct routers; merging them couples chat
    + embeddings lifecycles and muddies isinstance-based wiring tests.
  - **Use the google-genai SDK (rejected)**: same decision as anthropic (2026-06-13) — raw
    per-provider httpx matches the LiteLLM parity target + the uniform CircuitBreaker/timeout/
    fallback seam, and avoids per-provider SDK dependency sprawl.

Must:
<must>
  - CHAT REQUEST (OpenAI chat → Gemini generateContent body):
    * messages role="system" → top-level `systemInstruction:{parts:[{text}]}` (join multiple
      with "\n\n"); role="user"→Gemini role "user", role="assistant"→Gemini role "model";
      each becomes `{role, parts:[{text: content}]}` in `contents:[...]`.
    * `generationConfig`: maxOutputTokens (from OpenAI max_tokens if present, else
      `google_default_max_tokens`, default 4096), temperature, topP (from top_p), stopSequences
      (from OpenAI stop str|list) — include each only when present.
    * `model` is NOT in the body — it goes in the URL: `POST {base_url}/models/{model}:generateContent`
      (stream: `:streamGenerateContent?alt=sse`). model read from payload["model"] verbatim.
    * auth header `x-goog-api-key: <google_api_key>` + `content-type: application/json`.
  - CHAT RESPONSE (Gemini generateContent 200 → OpenAI chat.completion), complete():
    * id = "" (Gemini gives none) or a synthesized id; object="chat.completion";
      created=int(time.time()); model = payload["model"] (echo).
    * content = concat the `text` of every `candidates[0].content.parts[]` with a `text` key.
    * finishReason map: STOP→"stop", MAX_TOKENS→"length", SAFETY→"content_filter",
      RECITATION→"stop", OTHER/unknown/None→"stop".
    * usage from `usageMetadata`: promptTokenCount→prompt_tokens,
      candidatesTokenCount→completion_tokens, totalTokenCount→total_tokens (default 0 each).
  - CHAT STREAM (Gemini SSE `data: {candidates,usageMetadata?}` → OpenAI chunk bytes):
    * first emitted chunk announces `delta:{role:"assistant"}`.
    * each Gemini chunk's `candidates[0].content.parts[].text` → a `delta:{content:<text>}` chunk.
    * capture finishReason (mapped) + the LAST usageMetadata seen.
    * Terminal: ONE final chunk carrying `delta:{}` + mapped finish_reason AND
      `usage:{prompt_tokens,completion_tokens,total_tokens}` (so the frozen extract_usage_from_sse
      bills the streamed call), THEN `data: [DONE]`. Each frame `b"data: " + json + b"\n\n"`.
  - EMBEDDINGS (OpenAI /v1/embeddings → Gemini embed), GoogleEmbeddingsProvider.post_json:
    * post_json receives ("/embeddings", openai_body); read model=body["model"],
      input=body["input"] (str | list[str]).
    * single string input → `POST {base_url}/models/{model}:embedContent` body
      `{content:{parts:[{text:input}]}}`; list input → `:batchEmbedContents` body
      `{requests:[{model:"models/{model}", content:{parts:[{text:s}]}} for s in input]}`.
    * Gemini response: embedContent → `{embedding:{values:[float]}}`; batchEmbedContents →
      `{embeddings:[{values:[float]}]}`. Translate → OpenAI `{object:"list",
      data:[{object:"embedding", index:i, embedding:[float]}], model, usage:{prompt_tokens,
      total_tokens}}` preserving input order.
    * usage: Gemini embed returns NO token count → ESTIMATE prompt_tokens =
      max(1, ceil(total_input_chars/4)); total_tokens == prompt_tokens (documented approximation;
      see the ⚠ flag). The use case bills on resp_body["usage"], so this field MUST be present.
    * auth header `x-goog-api-key`; post_multipart/stream_bytes raise UpstreamUnavailableError
      (Gemini images/audio out of scope — never reached for embedding-modality models).
  - RESILIENCE (both adapters mirror the anthropic/openai adapters): own httpx.AsyncClient
    (connect 10s/read 120s/stream 300s) + per-instance CircuitBreaker; breaker.guard() before
    each call; Gemini 5xx or transport timeout/network → UpstreamUnavailableError (→ 502 / v8
    fallback for chat); 4xx → pass status through with an OpenAI-shaped error body. No retry loop.
  - SECURITY: the Google api key is a SECRET — never logged/echoed/committed or placed in a URL,
    metric label, span attribute, or exception message; sent ONLY in the x-goog-api-key header.
    Settings: ADD `google_default_max_tokens: int = 4096` (google_api_key/google_base_url already
    exist from provider-chat-dispatch §3).
  - WIRING (composition root, main.py only): when `settings.google_api_key` is non-empty,
    (a) add `_chat_adapters["google"] = GeminiCompletionUpstream(...)` BEFORE building the
    dispatch wrapper, and (b) add a "google" entry to the `_providers` dict that builds the
    ProviderRegistry: `_providers["google"] = GoogleEmbeddingsProvider(...)`. Empty key → neither
    is registered → provider="google" chat dispatch-falls-back to openrouter; provider="google"
    embeddings raises the existing ERR_PROVIDER_UNAVAILABLE (503). openrouter/openai/anthropic
    paths BYTE-IDENTICAL.
</must>
Reject:
<reject>
  - Gemini 5xx / connect timeout / read timeout / network error -> UpstreamUnavailableError
    (chat → 502 / v8 fallback; embeddings → ERR_UPSTREAM_UNAVAILABLE 502 via the use case).
  - Gemini 4xx (e.g. 400 INVALID_ARGUMENT, 403 PERMISSION_DENIED, 429 RESOURCE_EXHAUSTED) ->
    PASS THE STATUS THROUGH with an OpenAI-shaped error body `{error:{message,type,code}}` mapped
    from Gemini's `{error:{code,message,status}}`; no exception. type/code = a lowercased Gemini
    `status` (e.g. "invalid_argument") or "upstream_error" when absent.
  - empty `google_api_key` at wiring time -> NEITHER adapter registered; NEVER send an empty
    x-goog-api-key (the v7 empty-bearer lesson generalized).
  - the Google api key placed anywhere in a URL/query string -> rejected by design (header-only).
</reject>
After:
<after>
  - a catalog model with provider="google" modality="chat" returns an OpenAI-shaped
    chat.completion (stream + non-stream) billed on the served model id with usageMetadata tokens.
  - a catalog model with provider="google" modality="embedding" returns an OpenAI-shaped
    embeddings list (order-preserving) billed with the estimated usage.
  - openrouter/openai/anthropic chat + all prior embeddings paths are byte-identical.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Gemini EMBEDDINGS usage is ESTIMATED (chars/4) because embedContent/batchEmbedContents return
    NO token count — lowest confidence because billing accuracy for google embeddings depends on a
    heuristic, not the wire; if a tenant expects exact tokens, embedding spend will be approximate.
    Cost: bounded + provider-scoped (chat usage IS native via usageMetadata; only google-embedding
    spend is an estimate). Mitigation: documented in the response + revisited if Gemini exposes a
    token count; the estimate is deterministic and never zero.
  - [ ] Gemini streaming wire is `data: {...}` SSE via `?alt=sse` with per-chunk candidates +
    a final usageMetadata — pinned from documented fixtures, not a live key; validated by task-4
    live-verify (same risk class as anthropic streaming). If wrong: streamed google calls mis-bill.
  - [ ] x-goog-api-key header auth (vs ?key= query) — chosen for secret-safety; both are documented
    by Google; header avoids URL logging. If a gateway/proxy strips it, calls 401 (caught at verify).
  - [ ] model id passes through verbatim into the `:generateContent` URL path (catalog id == Gemini
    model name, e.g. "gemini-1.5-flash") — matches the OpenRouter/Anthropic convention.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: chat non-stream request translated to generateContent and back
  Given OpenAI chat {model:"gemini-1.5-flash", messages:[{role:system,content:"S"},{role:user,content:"Hi"},{role:assistant,content:"prev"}], max_tokens:64}
  When complete() runs against a Gemini stub returning a candidate text + usageMetadata{promptTokenCount:8,candidatesTokenCount:4,totalTokenCount:12}
  Then the POST URL ends with /models/gemini-1.5-flash:generateContent
  And the body has systemInstruction.parts[0].text=="S", contents==[{role:"user",parts:[{text:"Hi"}]},{role:"model",parts:[{text:"prev"}]}], generationConfig.maxOutputTokens==64
  And the result is (200, OpenAI chat.completion) with choices[0].message.content=="<text>", finish_reason=="stop"
  And usage=={prompt_tokens:8,completion_tokens:4,total_tokens:12}

Scenario: chat auth uses x-goog-api-key header, never a query param
  Given google_api_key="g-key"
  When any Gemini call is POSTed
  Then the request has header x-goog-api-key=="g-key"
  And the request URL contains no "key=" query parameter

Scenario: chat finishReason mapping
  Given Gemini finishReason in {STOP, MAX_TOKENS, SAFETY, RECITATION}
  When complete() translates
  Then OpenAI finish_reason is {stop, length, content_filter, stop}

Scenario: chat streaming translated to OpenAI chunks with terminal usage
  Given a Gemini SSE stream of candidate-part chunks then a final usageMetadata
  When stream() is drained
  Then the first chunk delta=={role:"assistant"}, the content chunks carry each part text in order
  And the LAST data frame before [DONE] has finish_reason=="stop" AND usage{prompt_tokens,completion_tokens,total_tokens}
  And the final bytes are "data: [DONE]\n\n"

Scenario: embeddings single string -> embedContent
  Given OpenAI {model:"text-embedding-004", input:"hello"} routed to provider="google"
  When post_json("/embeddings", body) runs against a stub returning {embedding:{values:[0.1,0.2]}}
  Then the POST URL ends with /models/text-embedding-004:embedContent and body=={content:{parts:[{text:"hello"}]}}
  And the result is (200, {object:"list", data:[{object:"embedding",index:0,embedding:[0.1,0.2]}], model:"text-embedding-004", usage:{prompt_tokens:<est>,total_tokens:<est>}})

Scenario: embeddings list input -> batchEmbedContents preserving order
  Given OpenAI {model:"text-embedding-004", input:["a","bb"]}
  When post_json runs against a stub returning {embeddings:[{values:[1.0]},{values:[2.0]}]}
  Then the URL ends with :batchEmbedContents and data==[{index:0,embedding:[1.0]},{index:1,embedding:[2.0]}]

Scenario: Gemini 5xx raises UpstreamUnavailableError
  Given the Gemini stub returns 503
  When complete() (or post_json) runs
  Then UpstreamUnavailableError is raised
  And the openrouter/anthropic paths are unchanged

Scenario: Gemini 4xx error envelope passed through
  Given the Gemini stub returns 400 {error:{code:400,message:"bad",status:"INVALID_ARGUMENT"}}
  When complete() runs
  Then it returns (400, {error:{message:"bad",type:"invalid_argument",code:"invalid_argument"}})
  And no exception is raised

Scenario: empty api key -> neither adapter wired
  Given settings.google_api_key == ""
  When create_app() builds _chat_adapters and the provider registry
  Then "google" is absent from app.state.chat_adapters
  And provider_registry.get("google") is None (embeddings → ERR_PROVIDER_UNAVAILABLE)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
NEW class GeminiCompletionUpstream  (proxy/infrastructure/gemini_upstream.py)
  implements CompletionUpstream (EXISTING Protocol — chat seam, v9 _chat_adapters["google"]).
  __init__(self, *, api_key, base_url="https://generativelanguage.googleapis.com/v1beta",
           default_max_tokens=4096, metrics_registry=None)
    own httpx.AsyncClient (connect10/read120/write120/pool10) + CircuitBreaker; api_key private.
  async def complete(payload) -> tuple[int,dict]:
    breaker.guard(); POST /models/{payload["model"]}:generateContent with
    _openai_to_gemini_request(payload, default_max_tokens) + {"x-goog-api-key":key,"content-type":...}
    - transport/5xx -> on_upstream_error + raise UpstreamUnavailableError
    - 4xx -> record_success; return (status, _gemini_error_to_openai(body))
    - 200 -> record_success; return (200, _gemini_to_openai(body, model=payload["model"]))
  def stream(payload) -> AsyncIterator[bytes]:   # sync def returns inner async-gen
    breaker.guard(); _gen(): client.stream POST /models/{model}:streamGenerateContent?alt=sse
    (read=300); 5xx before first byte -> raise; else record_success, parse SSE `data:` frames →
    _translate_gemini_sse(...) → OpenAI chunk bytes (terminal usage frame + [DONE]); transport
    TimeoutException/NetworkError -> on_upstream_error + raise.

NEW class GoogleEmbeddingsProvider  (same module)
  implements UpstreamProvider (EXISTING v7 Protocol — embeddings seam, registry["google"]).
  __init__(self, *, api_key, base_url=<google_base_url>, metrics_registry=None)  # own client+breaker
  async def post_json(path, body) -> tuple[int,dict]:
    breaker.guard(); model=body["model"]; inp=body["input"]
    - str input  -> POST /models/{model}:embedContent      json {content:{parts:[{text:inp}]}}
    - list input -> POST /models/{model}:batchEmbedContents json
                    {requests:[{model:f"models/{model}",content:{parts:[{text:s}]}} for s in inp]}
    headers {"x-goog-api-key":key,"content-type":"application/json"}
    - transport/5xx -> on_upstream_error + raise UpstreamUnavailableError
    - 4xx -> record_success; return (status, _gemini_error_to_openai(body))
    - 200 -> record_success; return (200, _gemini_embed_to_openai(resp, model, inp))
  async def post_multipart(...) -> raise UpstreamUnavailableError  ("gemini: unsupported modality")
  def stream_bytes(...) -> raise UpstreamUnavailableError           ("gemini: unsupported modality")

Pure helpers (module-level, no I/O — unit-tested directly):
  _openai_to_gemini_request(payload, *, default_max_tokens) -> dict
    { systemInstruction?:{parts:[{text}]}, contents:[{role:"user"|"model",parts:[{text}]}],
      generationConfig:{maxOutputTokens, temperature?, topP?, stopSequences?} }
  _gemini_to_openai(body, *, model) -> dict   # chat.completion
    { id, object:"chat.completion", created:int, model,
      choices:[{index:0, message:{role:"assistant", content}, finish_reason}],
      usage:{prompt_tokens, completion_tokens, total_tokens} }
  _gemini_embed_to_openai(body, model, inp) -> dict   # OpenAI embeddings list
    { object:"list", data:[{object:"embedding", index, embedding:[float]}], model,
      usage:{prompt_tokens:<est>, total_tokens:<est>} }   # est = max(1, ceil(total_chars/4))
  _gemini_error_to_openai(body) -> dict   { error:{message,type,code} }  # from {error:{code,message,status}}
  _map_gemini_finish_reason(fr: str|None) -> str
    STOP->stop · MAX_TOKENS->length · SAFETY->content_filter · RECITATION->stop · _->stop
  _translate_gemini_sse(chunks: iterable[dict]) -> iterable[bytes]
    first chunk delta{role:assistant}; each candidates[0].content.parts[].text -> delta{content};
    capture finishReason + last usageMetadata; terminal {delta:{},finish_reason}+usage + [DONE].
  OpenAI chunk shape == chat.completion.chunk (same as anthropic adapter).

HTTP wire (Gemini, base_url default https://generativelanguage.googleapis.com/v1beta):
  POST /models/{model}:generateContent              headers x-goog-api-key + content-type
    body { systemInstruction?, contents, generationConfig }
    200 -> { candidates:[{content:{parts:[{text}],role:"model"}, finishReason}],
             usageMetadata:{promptTokenCount, candidatesTokenCount, totalTokenCount} }
  POST /models/{model}:streamGenerateContent?alt=sse  -> SSE `data: {candidates,usageMetadata?}` frames
  POST /models/{model}:embedContent                   body {content:{parts:[{text}]}}
    200 -> { embedding:{values:[float]} }
  POST /models/{model}:batchEmbedContents             body {requests:[{model,content}]}
    200 -> { embeddings:[{values:[float]}] }
  4xx -> { error:{code,message,status} }   (passed through, status preserved)
  5xx / transport -> UpstreamUnavailableError

Settings (additive): google_default_max_tokens: int = 4096
  (google_api_key / google_base_url already exist from provider-chat-dispatch §3).
  Secret: google_api_key NEVER logged/echoed/committed/in-URL; header-only.

Wiring (main.py, composition root ONLY):
  if settings.google_api_key:
      _chat_adapters["google"] = GeminiCompletionUpstream(api_key=..., base_url=settings.google_base_url,
          default_max_tokens=settings.google_default_max_tokens, metrics_registry=...)
      _providers["google"] = GoogleEmbeddingsProvider(api_key=..., base_url=settings.google_base_url,
          metrics_registry=...)        # _providers builds ProviderRegistry
  v8 router / use cases / openrouter / openai / anthropic paths UNCHANGED + byte-identical.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)

Least-sure flag surfaced at freeze: [spec/contract] (1) Gemini EMBEDDINGS usage is an ESTIMATE
(chars/4) — embedContent returns no token count, so google-embedding SPEND is approximate (chat
usage is native via usageMetadata; bounded, provider-scoped). (2) The streaming wire
(`streamGenerateContent?alt=sse` → per-chunk `data:{candidates,usageMetadata}`) is pinned from
documented fixtures, not a live key — same risk class as anthropic streaming, caught by task-4
live-verify before the milestone closes. The freeze deliberately pins Gemini's translation only
(per the v2 fixture-grounded-per-provider lesson). x-goog-api-key header auth chosen over ?key=
query for secret-safety (no key in URLs/logs).
<!-- Approved -> Status: FROZEN @ vN. Changing a frozen contract = change request back to SPECIFY. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% of the new module (chat + embeddings + helpers).
Plan (one test per scenario, via httpx.MockTransport — no network, no real key):
<test_plan>
  - test_chat_request_translation: system→systemInstruction; assistant→role "model"; URL :generateContent; maxOutputTokens
  - test_chat_response_translation: candidate text joined; usageMetadata→usage; finish_reason
  - test_chat_finish_reason_mapping: STOP/MAX_TOKENS/SAFETY/RECITATION → stop/length/content_filter/stop
  - test_auth_header_x_goog_api_key_no_query: x-goog-api-key present; no key= in URL
  - test_chat_stream_translation: SSE chunks → role chunk, content chunks in order, terminal usage frame, [DONE]
  - test_chat_stream_usage_extractable: extract_usage_from_sse(chunks) == native usageMetadata mapping
  - test_embeddings_single_embedContent: str input → :embedContent; OpenAI list out; usage present
  - test_embeddings_batch_preserves_order: list input → :batchEmbedContents; data indices 0..n in order
  - test_embeddings_usage_estimate: usage.prompt_tokens == max(1, ceil(total_chars/4))
  - test_5xx_raises_upstream_unavailable: chat + embeddings stub 503 → UpstreamUnavailableError
  - test_4xx_error_envelope_passthrough: stub 400 gemini error → (400,{error:{message,type,code}}); no raise
  - test_embeddings_provider_satisfies_upstreamprovider_protocol: isinstance(GoogleEmbeddingsProvider, UpstreamProvider)
  - test_chat_satisfies_completionupstream_protocol: isinstance(GeminiCompletionUpstream, CompletionUpstream)
  - test_wiring_google_present_when_key_set: create_app(google_api_key set) → chat_adapters["google"] + provider_registry.get("google")
  - test_wiring_google_absent_when_key_empty: create_app(google_api_key="") → no "google" in either seam
</test_plan>

Tests live in: `./tests/` (`apps/gateway/tests/gemini_provider/`) · MUST run red (missing module
`gateway.proxy.infrastructure.gemini_upstream`) before Build.
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

- [x] all tests pass — 628 passed -m 'not e2e'; target gemini_provider suite 19/19 (chat + embeddings
      + helpers + wiring, both seams)
- [x] coverage did not decrease — 82.47% TOTAL (≥80 floor; was 82.20% at anthropic close)
- [x] no test or contract was altered to weaken it — §3 contract UNTOUCHED; no frozen
      v6/v7/v8/v9 test edited (only the new test file added to pyproject ruff-format exclude, the
      established pattern — no test code changed)
- [x] concurrency / timing safe — both adapters hold one httpx.AsyncClient + a per-instance
      CircuitBreaker (same model as anthropic/openai); no shared mutable state across requests; no
      retry loop. Same non-blocking streaming-buffer caveat as anthropic (see §7): stream() buffers
      the Gemini SSE before emitting → correct output + billing, not incremental.
- [x] no exposed secrets / injection / unexpected deps — google api key stored as self._api_key,
      sent ONLY in the x-goog-api-key header; stream uses params={"alt":"sse"} (key NEVER in a URL/
      query); exceptions use str(transport_exc) or f"Upstream returned {int_status}". Zero new deps
      (httpx + stdlib json/math/time). Decision (with Tin, 2026-06-13): raw httpx over google-genai
      SDK — same rationale as anthropic.
- [x] layering & dependencies follow CONVENTIONS.md — chat adapter (CompletionUpstream) +
      embeddings adapter (UpstreamProvider) in proxy/infrastructure; pure helpers module-level; no
      domain→infra import; wiring at composition root only.
- [x] a person reviewed and approved — delegated auto mode (Tin Dang, 2026-06-13) + the standing
      SDK/httpx steer; orchestrator manually reviewed the full module (Rule 5) incl. the secret-in-
      URL check + re-ran the authoritative gate; security clean (no HARD-STOP).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — GeminiCompletionUpstream + GoogleEmbeddingsProvider imported in main.py,
      conditionally registered (chat → _chat_adapters["google"]; embeddings → _providers["google"]
      → ProviderRegistry) only when google_api_key set; google_default_max_tokens in config.py. The
      6 pure helpers each referenced by the adapters and unit-tested directly. Wiring tests assert
      both seams present (key set) / absent (key empty).
- [x] DEAD-CODE (code) — no orphaned symbol; both classes + all helpers wired and tested.
- [x] SEMANTIC — n/a (code task); orchestrator read the new 597-line module in full + the diffs.

### GATE RECORD
Outcome: PASS
Evidence: 628 passed -m 'not e2e' · cov 82.47% (≥80) · ruff check + format clean · pyright 0 errors ·
          allowlist OK · gemini target 19/19 · openrouter/openai/anthropic + prior embeddings
          byte-identical · api key header-only, never in URL/log (manual review).
Reviewed by: Tin Dang (delegated auto mode + SDK/httpx steer) · date: 2026-06-13 ·
          security: clean (no finding → no HARD-STOP)

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): Gemini 5xx/transport rate (→ v8 fallback for chat); 4xx
status distribution (PERMISSION_DENIED spike = key/quota drift); streamed-call usage==0 rate
(SSE field drift); google-embedding estimated-vs-actual spend skew (the chars/4 heuristic — revisit
if Gemini ever returns a token count); time-to-first-byte on streamed Gemini calls (high until the
buffering caveat is fixed).
Spec delta for the next loop: provider-breadth-live-verify (task 4) is the ONLY remaining v9 task;
both providers are wired on both seams. It must replay recorded Anthropic + Gemini chat streams +
a Gemini embedding through per-provider stubs at the TLS edge (double-pass) and assert: billing on
the served model id with correct usage, governance (401/402) intact, openrouter/openai byte-identical.

### Competency deltas
- [SDD · folded] Gemini exercised BOTH provider seams at once — chat via the v9 CompletionUpstream
  dispatch AND embeddings via the v7 UpstreamProvider registry — proving a single provider can span
  both without touching either frozen seam (evidence: 19/19 green; both wiring tests assert
  presence/absence on each seam). Confirms the v7+v9 seam split composes cleanly.
- [DDD · folded] Provider value-set widened to {openrouter,openai,anthropic,google} across chat +
  embeddings with NO datastore/migration change (catalog ModelRow.provider already TEXT) — the
  "provider as first-class routing dimension" glossary delta is fully realized for v9's scope.
- [TDD · folded] FOLLOW-UP (non-blocking, shared with anthropic): Gemini stream() also buffers the
  full SSE before emitting → correct billing, not incremental TTFB. One streaming-latency hardening
  slice should cover BOTH anthropic + gemini (same buffer-then-translate shape).
- [UDD · folded] FOLLOW-UP: google-embedding usage is a chars/4 ESTIMATE (Gemini embed returns no
  token count) → embedding SPEND is approximate for one provider. If exact embedding billing matters
  to a tenant, this needs a real tokenizer or a Gemini countTokens pre-call (a cost/accuracy slice).
  Evidence: _gemini_embed_to_openai estimates usage; flagged as the freeze's ⚠ least-sure point.
