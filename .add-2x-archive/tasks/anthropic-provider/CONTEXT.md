# Shared context — anthropic-provider (v9 task 2/4)

Frozen spec: `.add/tasks/anthropic-provider/TASK.md` (§1–§4; §3 CONTRACT FROZEN @ v1).
Read it FIRST and in full — single source of truth. Do NOT edit it.

## Goal of this BUILD
Turn the RED suite `apps/gateway/tests/anthropic_provider/test_anthropic_provider.py` GREEN
(17 tests) by creating the Anthropic Messages API chat adapter + its pure translation
helpers, and wiring it into the v9 `_chat_adapters` map at the composition root. NO change to
the frozen v8 router, the v9 dispatch wrapper, the chat use case, or any other frozen test.

## Hard rules (NON-NEGOTIABLE — violation = HARD-STOP)
- The Anthropic api key is a SECRET: NEVER log/echo/commit it or put it in a metric label,
  span attribute, or exception message. Auth header is `x-api-key` + `anthropic-version`,
  NEVER an Authorization Bearer.
- provider=openrouter / openai / embeddings paths stay BYTE-IDENTICAL. Do NOT touch
  fallback_router.py, use_cases.py, provider_aware_upstream.py, openrouter_upstream.py, or any
  frozen test. Empty `anthropic_api_key` → adapter NOT constructed (no `x-api-key:""` ever).
- Do NOT change the §3 contract or weaken the red tests — make them pass as written.
- Allow-list deps only: stdlib + httpx + structlog + existing imports. No new third-party dep.

## File to CREATE
`apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` — mirror the STRUCTURE of
`openrouter_upstream.py` (httpx.AsyncClient with the same timeouts, a per-instance
CircuitBreaker, UpstreamUnavailableError on 5xx/transport, `stream()` is a sync def returning an
inner async-gen via `return _gen()`), but translate OpenAI⇄Anthropic. Implement EXACTLY the §3
symbols (tests import these names — keep them):
- `class AnthropicCompletionUpstream` with ctor
  `__init__(self, *, api_key, base_url="https://api.anthropic.com/v1",
   anthropic_version="2023-06-01", default_max_tokens=4096, metrics_registry=None)`.
  Build `self._client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(connect=10,
  read=120, write=120, pool=10))` and `self._breaker = CircuitBreaker()`. Store api_key/version/
  default_max_tokens privately. (Tests swap `adapter._client` with a MockTransport client — so the
  client MUST be a plain attribute named `_client`, and complete()/stream() MUST use `self._client`.)
  - `async def complete(payload) -> tuple[int, dict]`: `self._breaker.guard()`; POST
    `/messages` with `_openai_to_anthropic_request(payload, default_max_tokens=self._default_max_tokens)`
    and headers `{"x-api-key": self._api_key, "anthropic-version": self._version,
    "content-type": "application/json"}`. Transport
    (ConnectError/ConnectTimeout/PoolTimeout/ReadTimeout/WriteTimeout/NetworkError) →
    `breaker.on_upstream_error()` + raise `UpstreamUnavailableError`. resp.status>=500 → same.
    resp 4xx → `breaker.record_success()`; `return (status, _anthropic_error_to_openai(resp.json()))`.
    resp 200 → `breaker.record_success()`; `return (200, _anthropic_to_openai(resp.json()))`.
  - `def stream(payload) -> AsyncIterator[bytes]`: `self._breaker.guard()`; inner `_gen()`:
    `async with self._client.stream("POST", "/messages", json=<request with stream=True>,
    headers=..., timeout=httpx.Timeout(connect=10, read=300, write=120, pool=10)) as response:`
    if status>=500 → on_upstream_error + raise UpstreamUnavailableError; else record_success and
    iterate the Anthropic SSE: parse frames (split on blank lines; each frame has `event:`/`data:`
    lines), JSON-decode each `data:` payload, and feed `(event_name, data_obj)` into
    `_translate_anthropic_sse(...)` — yielding the OpenAI chunk bytes it produces. Wrap the
    network in try/except `(httpx.TimeoutException, httpx.NetworkError)` → on_upstream_error + raise.
    NOTE: stream() must consume the Anthropic byte stream incrementally (use `response.aiter_lines()`
    or aiter_bytes + a small buffer) and translate event-by-event; the terminal frame + `[DONE]`
    come from `_translate_anthropic_sse` when it sees `message_stop` (or stream end).
- Module-level PURE helpers (no I/O — the tests call these directly):
  - `_openai_to_anthropic_request(payload, *, default_max_tokens) -> dict`: lift role=="system"
    messages into top-level `system` (join multiple with "\n\n"); keep the rest as
    `messages:[{role, content}]` (content is the string as-is); `max_tokens` = payload's else
    default; pass through `temperature`/`top_p` when present; OpenAI `stop` (str→[str] | list) →
    `stop_sequences`; `model` verbatim; include `stream` only when the caller sets it (complete
    omits/false, stream sets True — implement by the adapter adding `stream=True` for the stream
    path, e.g. `{**_openai_to_anthropic_request(...), "stream": True}`).
  - `_anthropic_to_openai(body) -> dict`: OpenAI chat.completion (see §3). `created=int(time.time())`
    (import `time`). content = "".join(b["text"] for b in body.get("content",[]) if b.get("type")=="text").
  - `_anthropic_error_to_openai(body) -> dict`: `{"error":{"message":err["message"],
    "type":<mapped>, "code":<mapped>}}` where mapped type passes known Anthropic error types
    through (invalid_request_error/authentication_error/rate_limit_error) else "upstream_error";
    code == the mapped type. Be defensive: missing fields → "" message, "upstream_error" type.
  - `_map_finish_reason(stop_reason) -> str`: end_turn→stop, max_tokens→length, stop_sequence→stop,
    tool_use→tool_calls, None/other→stop.
  - `_translate_anthropic_sse(events) -> Iterable[bytes]`: events is an iterable of
    `(event_name: str, data: dict)`. Emit OpenAI `chat.completion.chunk` frames as
    `b"data: " + json.dumps(chunk).encode() + b"\n\n"`. Logic:
      * message_start → first chunk `choices:[{index:0, delta:{role:"assistant"}, finish_reason:null}]`;
        capture `data["message"]["usage"]["input_tokens"]` as prompt_tokens; id/model from data["message"].
      * content_block_delta where `data["delta"]["type"]=="text_delta"` → chunk
        `choices:[{index:0, delta:{content: data["delta"]["text"]}, finish_reason:null}]`.
      * message_delta → capture `_map_finish_reason(data["delta"].get("stop_reason"))` and
        `data["usage"]["output_tokens"]` as completion_tokens.
      * message_stop (or generator exhaustion) → emit ONE terminal chunk
        `choices:[{index:0, delta:{}, finish_reason:<captured or "stop">}]` PLUS
        `usage:{prompt_tokens, completion_tokens, total_tokens}` on the SAME frame, then
        `b"data: [DONE]\n\n"`.
      * ping / content_block_start / content_block_stop / unknown → ignored.
    Use a stable `created=int(time.time())` and chunk `object:"chat.completion.chunk"`. The
    terminal usage frame is REQUIRED (the gateway's `extract_usage_from_sse` reads the LAST data
    frame carrying a `usage` dict — test cross-checks this exact extractor).

## Files to MODIFY
1. `apps/gateway/src/gateway/core/config.py` — ADD `anthropic_default_max_tokens: int = 4096`
   near the existing `anthropic_*` fields (additive; GATEWAY_ANTHROPIC_DEFAULT_MAX_TOKENS).
2. `apps/gateway/src/gateway/main.py` — in the composition root, AFTER `_chat_adapters` is
   created with `{"openrouter": _openrouter_upstream}` and BEFORE
   `ProviderAwareCompletionUpstream(...)` is built: add
   ```
   if settings.anthropic_api_key:
       _chat_adapters["anthropic"] = AnthropicCompletionUpstream(
           api_key=settings.anthropic_api_key,
           base_url=settings.anthropic_base_url,
           anthropic_version=settings.anthropic_version,
           default_max_tokens=settings.anthropic_default_max_tokens,
           metrics_registry=app.state.metrics_registry,
       )
   ```
   ALSO expose the public seam `app.state.chat_adapters = _chat_adapters` (the wiring tests read
   `app.state.chat_adapters`; this mirrors the v9-task-1 `openrouter_completion_upstream` seam).
   Import `AnthropicCompletionUpstream` at the top with the other proxy.infrastructure imports.

## Verification (subagent runs; orchestrator re-runs authoritative)
- Target GREEN: `cd apps/gateway && uv run pytest tests/anthropic_provider -o addopts="" -q`
- Regression (byte-identical guard): `cd apps/gateway && uv run pytest tests/provider_chat_dispatch
  tests/provider_seam tests/retry_policy_wiring tests/upstream_base_url tests/proxy
  tests/model_fallbacks tests/embeddings_endpoint -o addopts="" -q`
- Lint: `cd apps/gateway && uv run ruff check . && uv run ruff format --check .`
  (if the new test file trips ruff-format, add it to the format `exclude` in pyproject.toml the
  same way the other frozen test files are listed — do NOT change test code to satisfy the formatter).
- Typecheck (repo ROOT): `make typecheck`. Allowlist (ROOT): `make allowlist`.

## Deliverables
- 1 new module + 2 modified files; 17-test suite GREEN; regression GREEN; lint/types/allowlist
  clean. Do NOT git commit. Do NOT edit TASK.md. Report: files touched, test counts, any
  deviation/risk, confidence scores. Confirm: api key never logged; openrouter/openai/embeddings
  byte-identical; no frozen router/use-case/dispatch/test edited.
