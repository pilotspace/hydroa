"""Infrastructure adapter: AnthropicCompletionUpstream.

Translates OpenAI chat-completions ⇄ Anthropic Messages API for both
non-streaming (complete) and streaming SSE (stream) paths.

Wire protocol:
  POST {base_url}/messages
  Headers: x-api-key: <key>  anthropic-version: <version>  content-type: application/json
  NEVER an Authorization Bearer header (Anthropic uses x-api-key).

Resilience mirrors OpenRouterCompletionUpstream:
  - httpx.AsyncClient with per-timeout knobs
  - Per-instance CircuitBreaker (5 consecutive failures → 30 s open)
  - 5xx / transport errors → UpstreamUnavailableError (gateway → 502 / v8 fallback)
  - 4xx → pass through as OpenAI-shaped error body; no exception raised

Security:
  The Anthropic api key is a SECRET — NEVER logged, echoed, committed, or placed in
  metric labels / span attributes / exception messages.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any

import httpx

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.response_format_translation import (
    build_json_coercion_tool,
    extract_response_format,
    is_coercion_tool_call,
    unwrap_coerced_tool_input,
)
from gateway.proxy.domain.tool_translation import (
    build_tool_call_delta,
    dump_tool_arguments,
    load_tool_arguments,
)
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.upstream_retry import execute_with_retry

# v11 JSON-mode: instruction appended to the system prompt for response_format
# json_object (free-form JSON, no schema to force a tool with).
_JSON_OBJECT_INSTRUCTION = "You must respond with a single valid JSON object and nothing else."

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0
_STREAM_READ_TIMEOUT = 300.0

# Anthropic error types that map through verbatim
_KNOWN_ERROR_TYPES = frozenset(
    {"invalid_request_error", "authentication_error", "rate_limit_error"}
)


# ---------------------------------------------------------------------------
# Pure translation helpers (module-level, no I/O — unit-tested directly)
# ---------------------------------------------------------------------------


def _tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI tools → Anthropic tools (``parameters`` → ``input_schema``)."""
    out: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function", {})
        entry: dict[str, Any] = {"name": fn.get("name", "")}
        if "description" in fn:
            entry["description"] = fn["description"]
        entry["input_schema"] = fn.get("parameters", {})
        out.append(entry)
    return out


def _tool_choice_to_anthropic(choice: Any) -> dict[str, Any] | None:
    """Translate OpenAI tool_choice → Anthropic tool_choice.

    "auto"→{type:auto} · "required"→{type:any} · "none"→{type:none} ·
    {type:function, function:{name}}→{type:tool, name}. Unknown → None (omit).
    """
    if choice == "auto":
        return {"type": "auto"}
    if choice == "required":
        return {"type": "any"}
    if choice == "none":
        return {"type": "none"}
    if isinstance(choice, dict) and choice.get("type") == "function":
        name = choice.get("function", {}).get("name", "")
        return {"type": "tool", "name": name}
    return None


def _assistant_tool_calls_to_content(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Build an Anthropic assistant content-block list from an OpenAI assistant message.

    Any text ``content`` becomes a leading ``{type:"text",text}`` block; each entry in
    ``tool_calls`` becomes a ``{type:"tool_use", id, name, input}`` block (``arguments``
    JSON string → ``input`` object).
    """
    blocks: list[dict[str, Any]] = []
    text = msg.get("content")
    if isinstance(text, str) and text:
        blocks.append({"type": "text", "text": text})
    for call in msg.get("tool_calls", []):
        fn = call.get("function", {})
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id", ""),
                "name": fn.get("name", ""),
                "input": load_tool_arguments(fn.get("arguments", "")),
            }
        )
    return blocks


def _openai_to_anthropic_request(
    payload: dict[str, Any],
    *,
    default_max_tokens: int,
) -> dict[str, Any]:
    """Translate an OpenAI chat-completions request body → Anthropic Messages body.

    - role=="system" messages are lifted to the top-level ``system`` string;
      multiple system messages are joined with "\\n\\n".
    - An assistant message carrying ``tool_calls`` becomes an assistant message whose
      ``content`` is a ``tool_use`` block list (v10).
    - A run of consecutive ``role:"tool"`` messages collapses into ONE ``user`` message
      whose ``content`` is a ``tool_result`` block list (v10).
    - Remaining messages map 1:1 to ``messages:[{role, content}]``.
    - ``max_tokens``: uses the request value when present, else ``default_max_tokens``
      (Anthropic requires this field; OpenAI makes it optional).
    - Pass-through when present: ``temperature``, ``top_p``.
    - OpenAI ``stop`` (str | list) → Anthropic ``stop_sequences`` (list).
    - ``tools`` → Anthropic ``tools`` (input_schema); ``tool_choice`` mapped (v10).
    - ``model`` passes through verbatim.
    - ``stream`` is NOT included by this helper; the caller adds it when needed.

    Raises ValueError("tool_call_id_required") when a ``role:"tool"`` message lacks
    ``tool_call_id`` (no correlation id for the tool_result block).
    """
    messages: list[dict[str, Any]] = payload.get("messages", [])

    system_parts: list[str] = []
    non_system: list[dict[str, Any]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        role = msg.get("role")
        if role == "system":
            system_parts.append(str(msg.get("content", "")))
            i += 1
        elif role == "tool":
            # Collapse a run of consecutive tool messages into ONE user message.
            results: list[dict[str, Any]] = []
            while i < n and messages[i].get("role") == "tool":
                tmsg = messages[i]
                tool_call_id = tmsg.get("tool_call_id")
                if not tool_call_id:
                    raise ValueError("tool_call_id_required")
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": tmsg.get("content", ""),
                    }
                )
                i += 1
            non_system.append({"role": "user", "content": results})
        elif role == "assistant" and msg.get("tool_calls"):
            non_system.append(
                {"role": "assistant", "content": _assistant_tool_calls_to_content(msg)}
            )
            i += 1
        else:
            non_system.append({"role": role, "content": msg.get("content", "")})
            i += 1

    result: dict[str, Any] = {
        "model": payload["model"],
        "messages": non_system,
        "max_tokens": payload.get("max_tokens", default_max_tokens),
    }

    if system_parts:
        result["system"] = "\n\n".join(system_parts)

    if "temperature" in payload:
        result["temperature"] = payload["temperature"]
    if "top_p" in payload:
        result["top_p"] = payload["top_p"]

    stop = payload.get("stop")
    if stop is not None:
        result["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)

    tools = payload.get("tools")
    if tools:
        result["tools"] = _tools_to_anthropic(tools)
    tool_choice = _tool_choice_to_anthropic(payload.get("tool_choice"))
    if tool_choice is not None:
        result["tool_choice"] = tool_choice

    # response_format → Anthropic (v11). Anthropic has no native field:
    #   json_schema  → append a synthetic forced "json_output" tool (input_schema = the
    #                  requested schema) ALONGSIDE caller tools; force its tool_choice.
    #   json_object  → append a JSON-only system instruction (no schema to force).
    # extract returns None for absent / {type:"text"} (byte-identical v9/v10) and raises
    # ERR_UNSUPPORTED_RESPONSE_FORMAT / ERR_INVALID_JSON_SCHEMA.
    response_format = extract_response_format(payload)
    if response_format is not None:
        if response_format["type"] == "json_schema":
            caller_names = [t.get("function", {}).get("name", "") for t in (tools or [])]
            coercion_tool, coercion_choice = build_json_coercion_tool(
                response_format, existing_tool_names=caller_names
            )
            coercion_entry: dict[str, Any] = dict(coercion_tool)
            result["tools"] = result.get("tools", []) + _tools_to_anthropic([coercion_entry])
            forced = _tool_choice_to_anthropic(coercion_choice)
            if forced is not None:
                result["tool_choice"] = forced
        else:  # json_object
            existing_system = result.get("system", "")
            result["system"] = (
                f"{existing_system}\n\n{_JSON_OBJECT_INSTRUCTION}".strip()
                if existing_system
                else _JSON_OBJECT_INSTRUCTION
            )

    return result


def _map_finish_reason(stop_reason: str | None) -> str:
    """Map Anthropic stop_reason → OpenAI finish_reason.

    end_turn        → "stop"
    max_tokens      → "length"
    stop_sequence   → "stop"
    tool_use        → "tool_calls"
    None / unknown  → "stop"
    """
    mapping = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
    }
    return mapping.get(stop_reason or "", "stop")  # type: ignore[arg-type]


def _anthropic_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Translate an Anthropic 200 Messages response → OpenAI chat.completion body.

    Content: concatenate the ``text`` of every ``text`` content block. ``tool_use``
    blocks (v10) become OpenAI ``message.tool_calls`` (``input`` object → ``arguments``
    JSON string). When only ``tool_use`` blocks are present, ``message.content`` is null.
    Usage: input_tokens→prompt_tokens, output_tokens→completion_tokens.
    """
    content_blocks: list[dict[str, Any]] = body.get("content", [])
    text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")

    # v11 JSON-mode: a tool_use block named like the coercion tool is UNWRAPPED — its
    # input becomes message.content (a JSON string), NOT a tool_calls entry.
    coerced_content: str | None = None
    tool_calls: list[dict[str, Any]] = []
    for block in content_blocks:
        if block.get("type") != "tool_use":
            continue
        if is_coercion_tool_call(block.get("name", "")):
            coerced_content = unwrap_coerced_tool_input(block.get("input", {}))
            continue
        tool_calls.append(
            {
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": dump_tool_arguments(block.get("input", {})),
                },
            }
        )

    usage_raw: dict[str, Any] = body.get("usage", {})
    prompt_tokens: int = usage_raw.get("input_tokens", 0)
    completion_tokens: int = usage_raw.get("output_tokens", 0)

    # JSON-mode content wins; else text; else null when only (real) tool calls; else "".
    if coerced_content is not None:
        content: str | None = coerced_content
    elif text:
        content = text
    else:
        content = None if tool_calls else ""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls

    # When the coercion tool was unwrapped and there are no REAL tool calls, the stop is a
    # normal "stop" (the caller asked for JSON content, not a tool call).
    finish_reason = (
        "stop"
        if coerced_content is not None and not tool_calls
        else _map_finish_reason(body.get("stop_reason"))
    )

    return {
        "id": body.get("id", ""),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", ""),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _anthropic_error_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Translate an Anthropic error envelope → OpenAI error body.

    Anthropic shape: ``{"type":"error","error":{"type":<t>,"message":<m>}}``
    OpenAI shape:    ``{"error":{"message":<m>,"type":<mapped>,"code":<mapped>}}``

    Known types pass through verbatim; others become "upstream_error".
    Defensive: missing fields degrade gracefully.
    """
    err_obj: dict[str, Any] = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
    raw_type: str = err_obj.get("type", "") if isinstance(err_obj.get("type"), str) else ""
    message: str = err_obj.get("message", "") if isinstance(err_obj.get("message"), str) else ""

    mapped_type = raw_type if raw_type in _KNOWN_ERROR_TYPES else "upstream_error"

    return {
        "error": {
            "message": message,
            "type": mapped_type,
            "code": mapped_type,
        }
    }


def _translate_anthropic_sse(
    events: Iterable[tuple[str, dict[str, Any]]],
) -> Iterable[bytes]:
    """Translate an iterable of (event_name, data_obj) pairs → OpenAI SSE chunk bytes.

    Yields ``b"data: " + json_chunk + b"\\n\\n"`` for each meaningful event, then
    a terminal frame carrying ``finish_reason`` + ``usage``, then ``b"data: [DONE]\\n\\n"``.

    Events consumed:
      message_start         → first chunk ``delta:{role:"assistant"}``; capture input_tokens.
      content_block_start   → tool_use block → first ``delta:{tool_calls:[{id,name}]}`` (v10).
      content_block_delta   → text_delta → chunk ``delta:{content:<text>}``;
                              input_json_delta → ``delta:{tool_calls:[{arguments}]}`` (v10).
      message_delta         → capture stop_reason + output_tokens.
      message_stop          → emit terminal frame + [DONE].
      ping / content_block_stop / unknown → ignored.
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    chunk_id: str = ""
    chunk_model: str = ""
    created: int = int(time.time())
    terminal_emitted: bool = False
    # v10 tool streaming: map each Anthropic content-block index → its OpenAI
    # tool_calls index (counts only tool_use blocks, text blocks excluded).
    block_to_tc: dict[int, int] = {}
    tc_count: int = 0
    # v11 JSON-mode: a streamed coercion ("json_output") tool_use block is unwrapped —
    # its input_json_delta fragments stream as delta.content, not delta.tool_calls; the
    # block is excluded from block_to_tc and the terminal finish_reason is "stop".
    coercion_block_index: int | None = None
    saw_coercion: bool = False

    def _make_chunk(delta: dict[str, Any], fr: str | None) -> dict[str, Any]:
        return {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": chunk_model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": fr,
                }
            ],
        }

    for event_name, data in events:
        if event_name == "message_start":
            msg = data.get("message", {})
            chunk_id = msg.get("id", "")
            chunk_model = msg.get("model", "")
            usage = msg.get("usage", {})
            prompt_tokens = usage.get("input_tokens", 0)
            # Yield first role chunk
            chunk = _make_chunk({"role": "assistant"}, None)
            yield b"data: " + json.dumps(chunk).encode() + b"\n\n"

        elif event_name == "content_block_start":
            block = data.get("content_block", {})
            if block.get("type") == "tool_use":
                block_index = data.get("index", 0)
                if is_coercion_tool_call(block.get("name", "")):
                    # JSON-mode coercion block: its input streams as content (below);
                    # emit no tool_calls fragment and skip the tool index.
                    coercion_block_index = block_index
                    saw_coercion = True
                else:
                    tc_index = tc_count
                    block_to_tc[block_index] = tc_index
                    tc_count += 1
                    frag = build_tool_call_delta(
                        tc_index, id=block.get("id", ""), name=block.get("name", "")
                    )
                    chunk = _make_chunk({"tool_calls": [frag]}, None)
                    yield b"data: " + json.dumps(chunk).encode() + b"\n\n"

        elif event_name == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                chunk = _make_chunk({"content": text}, None)
                yield b"data: " + json.dumps(chunk).encode() + b"\n\n"
            elif delta.get("type") == "input_json_delta":
                block_index = data.get("index", 0)
                if coercion_block_index is not None and block_index == coercion_block_index:
                    # coercion JSON streams as delta.content, not delta.tool_calls
                    chunk = _make_chunk({"content": delta.get("partial_json", "")}, None)
                    yield b"data: " + json.dumps(chunk).encode() + b"\n\n"
                else:
                    tc_index = block_to_tc.get(block_index)
                    if tc_index is not None:
                        frag = build_tool_call_delta(
                            tc_index, arguments_fragment=delta.get("partial_json", "")
                        )
                        chunk = _make_chunk({"tool_calls": [frag]}, None)
                        yield b"data: " + json.dumps(chunk).encode() + b"\n\n"

        elif event_name == "message_delta":
            delta = data.get("delta", {})
            finish_reason = _map_finish_reason(delta.get("stop_reason"))
            # JSON-mode: when only the coercion tool was used, the stop is a normal "stop".
            if saw_coercion and tc_count == 0:
                finish_reason = "stop"
            usage = data.get("usage", {})
            completion_tokens = usage.get("output_tokens", completion_tokens)

        elif event_name == "message_stop":
            # Emit terminal frame
            terminal_chunk: dict[str, Any] = _make_chunk({}, finish_reason)
            terminal_chunk["usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
            yield b"data: " + json.dumps(terminal_chunk).encode() + b"\n\n"
            yield b"data: [DONE]\n\n"
            terminal_emitted = True

        # ping / content_block_start / content_block_stop / unknown → ignored

    # If the stream ends without a message_stop event, emit the terminal frame anyway
    if not terminal_emitted:
        terminal_chunk = _make_chunk({}, finish_reason)
        terminal_chunk["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        yield b"data: " + json.dumps(terminal_chunk).encode() + b"\n\n"
        yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class AnthropicCompletionUpstream:
    """Forwards chat completions to the Anthropic Messages API.

    Implements the CompletionUpstream Protocol (complete + stream).
    Translates OpenAI chat-completions ⇄ Anthropic Messages API shapes.

    A single instance is shared for the lifetime of the application.
    The circuit breaker state is per-instance (per-replica).

    SECURITY: the api_key is NEVER logged, echoed, or placed in any
    metric label / span attribute / exception message.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.anthropic.com/v1",
        anthropic_version: str = "2023-06-01",
        default_max_tokens: int = 4096,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        # Stored privately — never exposed in logs/errors/metrics
        self._api_key = api_key
        self._version = anthropic_version
        self._default_max_tokens = default_max_tokens
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._retry_deadline_s = retry_deadline_s
        self._metrics_registry = metrics_registry

        self._breaker = CircuitBreaker()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_NON_STREAM_TIMEOUT,
                write=_NON_STREAM_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            ),
        )

    def _auth_headers(self) -> dict[str, str]:
        """Build Anthropic auth headers. NEVER includes Authorization Bearer."""
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._version,
            "content-type": "application/json",
        }

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward a non-streaming chat request to the Anthropic Messages API.

        Returns (status_code, openai_shaped_body).
        - 200  → translated OpenAI chat.completion
        - 4xx (non-408) → OpenAI error body (no exception; gateway forwards the status)
        - 5xx / 429 / 408 / connect error / pool timeout → retried up to max_retries
          (unified retry seam); exhausted → UpstreamUnavailableError
        - read/write timeout / network error → UpstreamUnavailableError (not retried)

        Request translation is pure and runs ONCE, outside the retry loop.
        """
        anthropic_body = _openai_to_anthropic_request(
            payload,
            default_max_tokens=self._default_max_tokens,
        )

        async def _do_request() -> httpx.Response:
            return await self._client.post(
                "/messages",
                json=anthropic_body,
                headers=self._auth_headers(),
            )

        def _render(resp: httpx.Response) -> tuple[int, dict[str, Any]]:
            if resp.status_code >= 400:
                return resp.status_code, _anthropic_error_to_openai(resp.json())
            return 200, _anthropic_to_openai(resp.json())

        return await execute_with_retry(
            _do_request,
            _render,
            breaker=self._breaker,
            provider="anthropic",
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            deadline_s=self._retry_deadline_s,
            metrics_registry=self._metrics_registry,
        )

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Return an async generator that yields OpenAI SSE chunk bytes.

        The circuit breaker is checked before the stream opens.
        Anthropic SSE events are collected from the wire stream, then the complete
        event list is fed to _translate_anthropic_sse (stateful helper that needs the
        full sequence to emit the terminal usage frame correctly). The translated
        OpenAI chunk bytes are yielded in order.
        """
        self._breaker.guard()

        async def _gen() -> AsyncIterator[bytes]:
            anthropic_body = {
                **_openai_to_anthropic_request(
                    payload,
                    default_max_tokens=self._default_max_tokens,
                ),
                "stream": True,
            }

            try:
                async with self._client.stream(
                    "POST",
                    "/messages",
                    json=anthropic_body,
                    headers=self._auth_headers(),
                    timeout=httpx.Timeout(
                        connect=_CONNECT_TIMEOUT,
                        read=_STREAM_READ_TIMEOUT,
                        write=_NON_STREAM_TIMEOUT,
                        pool=_CONNECT_TIMEOUT,
                    ),
                ) as response:
                    if response.status_code >= 500:
                        self._breaker.on_upstream_error()
                        raise UpstreamUnavailableError(
                            f"Upstream returned {response.status_code} on stream"
                        )
                    self._breaker.record_success()

                    # Collect Anthropic SSE events incrementally line-by-line.
                    # _translate_anthropic_sse is stateful (accumulates prompt/completion
                    # tokens across events) so we buffer the complete sequence and pass
                    # it once to the translator for correct terminal-frame generation.
                    events: list[tuple[str, dict[str, Any]]] = []
                    current_event: str = ""
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if line.startswith("event:"):
                            current_event = line[len("event:") :].strip()
                        elif line.startswith("data:"):
                            raw = line[len("data:") :].strip()
                            if not raw or raw == "[DONE]":
                                continue
                            try:
                                data_obj: dict[str, Any] = json.loads(raw)
                            except (json.JSONDecodeError, ValueError):
                                continue
                            # Use event_type from the data object when event: line absent
                            event_name = current_event or data_obj.get("type", "")
                            events.append((event_name, data_obj))
                            current_event = ""
                        elif line == "":
                            # blank line = SSE frame boundary; reset pending event name
                            current_event = ""

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self._breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from exc

            # Translate the buffered event sequence → OpenAI SSE chunk bytes
            for chunk in _translate_anthropic_sse(events):
                yield chunk

        return _gen()
