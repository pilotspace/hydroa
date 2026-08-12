# Shared context — gemini-provider (v9 task 3/4)

Frozen spec: `.add/tasks/gemini-provider/TASK.md` (§1–§4; §3 CONTRACT FROZEN @ v1).
Read it FIRST and in full — single source of truth. Do NOT edit it.

## Goal of this BUILD
Turn the RED suite `apps/gateway/tests/gemini_provider/test_gemini_provider.py` GREEN (~21 tests)
by creating the Google Gemini provider on TWO seams + wiring both at the composition root:
- CHAT: `GeminiCompletionUpstream` (CompletionUpstream) → generateContent / streamGenerateContent,
  registered in the v9 `_chat_adapters["google"]`.
- EMBEDDINGS: `GoogleEmbeddingsProvider` (the v7 UpstreamProvider) → embedContent /
  batchEmbedContents, registered in the v7 ProviderRegistry under "google".
NO change to any frozen router/use-case/dispatch/registry or any prior test.

## Reference adapters (mirror their STRUCTURE exactly)
- `proxy/infrastructure/anthropic_upstream.py` — the CHAT template: httpx.AsyncClient + per-instance
  CircuitBreaker + the timeout envelope; complete() returns (status, body); stream() is a sync def
  returning an inner async-gen that parses provider SSE `data:` frames and emits OpenAI chunk bytes
  with a TERMINAL usage frame + `b"data: [DONE]\n\n"`; pure module-level translation helpers.
- `proxy/infrastructure/openai_provider.py` (OpenAIDirectProvider) — the EMBEDDINGS template: the
  UpstreamProvider shape (post_json / post_multipart / stream_bytes), breaker.guard() per call,
  5xx/transport → UpstreamUnavailableError.
- `usage/domain/extractor.py` (extract_usage_from_sse) — the terminal usage frame MUST satisfy it.
- `proxy/infrastructure/provider_registry.py` — ProviderRegistry(dict) + select_provider.

## Hard rules (NON-NEGOTIABLE — violation = HARD-STOP)
- The Google api key is a SECRET: NEVER log/echo/commit it or put it in a URL/query string, metric
  label, span attribute, or exception message. Auth is the `x-goog-api-key` HEADER only — NEVER a
  `?key=` query param.
- provider=openrouter/openai/anthropic + all prior embeddings paths stay BYTE-IDENTICAL. Do NOT
  edit fallback_router.py, use_cases.py, embeddings_use_case.py, provider_aware_upstream.py,
  anthropic_upstream.py, openrouter_upstream.py, openai_provider.py, provider_registry.py, or any
  frozen test. Empty `google_api_key` → NEITHER adapter constructed.
- Do NOT change the §3 contract or weaken the red tests — make them pass as written.
- Allow-list deps only: stdlib (json/math/time) + httpx + existing imports. No new third-party dep.

## File to CREATE
`apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` containing EXACTLY the §3 symbols
(the tests import these names — keep them):
- `class GeminiCompletionUpstream` (CompletionUpstream): ctor `(*, api_key, base_url=
  "https://generativelanguage.googleapis.com/v1beta", default_max_tokens=4096, metrics_registry=None)`;
  build `self._client = httpx.AsyncClient(base_url=..., timeout connect=10/read=120/write=120/pool=10)`
  and `self._breaker = CircuitBreaker()`; api_key/default_max_tokens stored privately.
  - `async def complete(payload)`: breaker.guard(); `model = payload["model"]`; POST
    `f"/models/{model}:generateContent"` with `_openai_to_gemini_request(payload,
    default_max_tokens=self._default_max_tokens)` + headers `{"x-goog-api-key": self._api_key,
    "content-type": "application/json"}`. transport/5xx → on_upstream_error + raise
    UpstreamUnavailableError. 4xx → record_success; `return (status, _gemini_error_to_openai(json))`.
    200 → record_success; `return (200, _gemini_to_openai(json, model=model))`.
  - `def stream(payload)`: breaker.guard(); inner `_gen()`: `self._client.stream("POST",
    f"/models/{model}:streamGenerateContent", params={"alt":"sse"}, json=_openai_to_gemini_request(...),
    headers=..., timeout read=300)`. 5xx before first byte → on_upstream_error + raise. Else
    record_success; parse SSE: iterate `response.aiter_lines()`, for `data:` lines JSON-decode the
    payload (skip blanks/`[DONE]`), collect the chunk dicts, then feed them to
    `_translate_gemini_sse(chunks)` and yield its bytes. transport TimeoutException/NetworkError →
    on_upstream_error + raise. (Buffer-then-translate is fine — same approach as anthropic.)
- `class GoogleEmbeddingsProvider` (UpstreamProvider): ctor `(*, api_key, base_url=
  "https://generativelanguage.googleapis.com/v1beta", metrics_registry=None)`; own client+breaker.
  - `async def post_json(path, body)`: breaker.guard(); `model=body["model"]; inp=body["input"]`.
    str input → POST `f"/models/{model}:embedContent"` json `{"content":{"parts":[{"text":inp}]}}`;
    list input → POST `f"/models/{model}:batchEmbedContents"` json `{"requests":[{"model":
    f"models/{model}", "content":{"parts":[{"text":s}]}} for s in inp]}`. headers x-goog-api-key +
    content-type. transport/5xx → on_upstream_error + raise. 4xx → record_success; return
    (status, _gemini_error_to_openai(json)). 200 → record_success; return (200,
    _gemini_embed_to_openai(json, model, inp)).
  - `async def post_multipart(path, files, data)`: `raise UpstreamUnavailableError("gemini: unsupported modality")`.
  - `def stream_bytes(path, payload)`: return an async-gen that raises UpstreamUnavailableError on
    first iteration (Gemini images/audio out of scope). (Implement as a sync def returning an inner
    `_gen()` that `raise`s — so `isinstance` + the protocol shape hold; the test drains it and
    expects the raise.)
- Module-level PURE helpers (tests call directly):
  - `_openai_to_gemini_request(payload, *, default_max_tokens) -> dict`: system→
    `systemInstruction:{parts:[{text}]}` (join multiple "\n\n"); user→role"user", assistant→role
    "model"; each `{role, parts:[{text:content}]}` into `contents`; `generationConfig` with
    maxOutputTokens (payload max_tokens else default), temperature/topP(from top_p)/stopSequences
    (from stop str→[str]|list) only when present.
  - `_gemini_to_openai(body, *, model) -> dict`: OpenAI chat.completion (see §3); content =
    "".join(p["text"] for p in candidates[0].content.parts if "text" in p); created=int(time.time());
    finish_reason via `_map_gemini_finish_reason(candidates[0].get("finishReason")`; usage from
    usageMetadata (promptTokenCount/candidatesTokenCount/totalTokenCount, default 0). Be defensive
    (missing candidates → empty content, finish_reason "stop", usage zeros).
  - `_gemini_embed_to_openai(body, model, inp) -> dict`: `{object:"list", data:[{object:"embedding",
    index:i, embedding:values}], model, usage:{prompt_tokens:est, total_tokens:est}}`. Read vectors
    from `body["embedding"]["values"]` (single) OR `[e["values"] for e in body["embeddings"]]`
    (batch); preserve order. est = `max(1, math.ceil(total_chars/4))` where total_chars = sum of
    len() over the inputs (inp may be str or list[str]).
  - `_gemini_error_to_openai(body) -> dict`: `{error:{message, type, code}}` from
    `{error:{code,message,status}}`; type=code= `status.lower()` if present else "upstream_error";
    message = error.message or "". Defensive on missing fields.
  - `_map_gemini_finish_reason(fr) -> str`: STOP→stop, MAX_TOKENS→length, SAFETY→content_filter,
    RECITATION→stop, None/other→stop.
  - `_translate_gemini_sse(chunks: Iterable[dict]) -> Iterable[bytes]`: emit OpenAI
    chat.completion.chunk frames `b"data: " + json.dumps(chunk).encode() + b"\n\n"`. First: a
    `delta:{role:"assistant"}` chunk (id "", model "", created int once). For each input chunk:
    for each `candidates[0].content.parts[].text` emit `delta:{content:text}`; capture
    `candidates[0].finishReason` (map) and the LAST `usageMetadata`. After all chunks: emit ONE
    terminal `delta:{}` + finish_reason chunk carrying `usage:{prompt_tokens,completion_tokens,
    total_tokens}` from the captured usageMetadata, then `b"data: [DONE]\n\n"`.

## Files to MODIFY
1. `apps/gateway/src/gateway/core/config.py` — ADD `google_default_max_tokens: int = 4096` near the
   existing `google_*` fields (GATEWAY_GOOGLE_DEFAULT_MAX_TOKENS).
2. `apps/gateway/src/gateway/main.py`:
   - import GeminiCompletionUpstream + GoogleEmbeddingsProvider from the new module.
   - in the composition root, where `_chat_adapters` is built (right after the anthropic block,
     before ProviderAwareCompletionUpstream): add
     `if settings.google_api_key: _chat_adapters["google"] = GeminiCompletionUpstream(api_key=
     settings.google_api_key, base_url=settings.google_base_url, default_max_tokens=
     settings.google_default_max_tokens, metrics_registry=app.state.metrics_registry)`.
   - find the `_providers: dict[str, UpstreamProvider] = {"openrouter": _openrouter_facade}` block
     (where "openai" is conditionally added) and add, with the SAME conditional style:
     `if settings.google_api_key: _providers["google"] = GoogleEmbeddingsProvider(api_key=
     settings.google_api_key, base_url=settings.google_base_url, metrics_registry=
     app.state.metrics_registry)` — placed BEFORE `ProviderRegistry(_providers)` is constructed.
   - do NOT touch the existing app.state.chat_adapters seam line beyond it already existing.

## Verification (subagent runs; orchestrator re-runs authoritative)
- Target GREEN: `cd apps/gateway && uv run pytest tests/gemini_provider -o addopts="" -q`
- Regression: `cd apps/gateway && uv run pytest tests/anthropic_provider tests/provider_chat_dispatch
  tests/provider_seam tests/retry_policy_wiring tests/upstream_base_url tests/proxy
  tests/embeddings_endpoint tests/model_fallbacks -o addopts="" -q`
- Lint: `cd apps/gateway && uv run ruff check . && uv run ruff format --check .` (if the new test
  file trips ruff-format, add it to the pyproject.toml format `exclude` list like the others — never
  edit test code to satisfy the formatter).
- Typecheck (ROOT): `make typecheck`. Allowlist (ROOT): `make allowlist`.

## Deliverables
- 1 new module + 2 modified files; the ~21-test suite GREEN; regression GREEN; lint/types/allowlist
  clean. Do NOT git commit. Do NOT edit TASK.md. Report: files touched, test counts, any deviation/
  risk, confidence scores. Confirm: api key header-only + never logged/in-URL; openrouter/openai/
  anthropic + prior embeddings byte-identical; no frozen file edited.
