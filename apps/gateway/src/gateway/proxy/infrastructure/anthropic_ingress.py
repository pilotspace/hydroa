"""Infrastructure adapter: Anthropic-wire INGRESS translation.

Contract FROZEN @ v1 (anthropic-messages-ingress TASK.md §3).

The architectural MIRROR, one layer up, of the EGRESS ``anthropic_upstream.py``
adapter: that module translates the internal OpenAI-shape ⇄ the real Anthropic
Messages API (Hydroa calling Anthropic AS a provider). This module translates a
CLIENT's Anthropic-wire request/response/SSE ⇄ the SAME internal OpenAI-shape
``CompletionUseCase.complete``/``.stream`` already consumes/produces (a client
calling Hydroa AS an Anthropic-compatible gateway).

Never imports from or mutates ``anthropic_upstream.py`` — that file is a
separate frozen contract; this module only mirrors its field vocabulary
in reverse (documented per-function below).

No IO here — pure translation + one stateful stepper class. Reuses the
canonical ``tool_translation`` helpers (``dump_tool_arguments`` /
``load_tool_arguments``) so tool-call argument (de)serialization stays
byte-identical to every other provider translator in the codebase.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from gateway.proxy.domain.tool_translation import dump_tool_arguments, load_tool_arguments

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AnthropicIngressError(Exception):
    """Raised when a client's Anthropic-wire request body fails translation.

    Always maps to R1 (400 ERR_PAYLOAD_INVALID, Anthropic-shaped
    invalid_request_error) at the router boundary — raised and caught BEFORE any
    governance call runs (Safety rule, TASK.md §5): a malformed body never
    reaches (or partially consumes) authn/budget/rate-limit/credit holds.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Anthropic error-envelope + status-bucket mapping (M9, R1-R7, contract §3)
# ---------------------------------------------------------------------------


def status_to_anthropic_error_type(status: int) -> str:
    """Map an HTTP status code to the Anthropic error envelope `type` bucket.

    The invariant is "SAME HTTP status /v1/chat/completions would raise for the
    identical governance failure; only the envelope shape changes" (contract
    §3) — this is a STATUS bucket, never a hardcoded assumption about which
    ERR_* code produces which status (that is owned entirely by the reused
    governance layer; ingress never re-decides it).
    """
    if status == 401:
        return "authentication_error"
    if status == 403:
        return "permission_error"
    if status == 429:
        return "rate_limit_error"
    if status >= 500:
        return "api_error"
    # 400/402/404/409/410/413/422/... — every other client-side rejection class
    # (including the 402 credits-exhausted case, which has no dedicated
    # Anthropic error type) degrades to the generic request-problem bucket.
    return "invalid_request_error"


def anthropic_error_body(code: str, message: str, *, status: int) -> dict[str, Any]:
    """Build the Anthropic error envelope: ``{type:"error", error:{type, message}}``.

    ``code`` (the gateway ERR_* machine code) is folded into the message so it
    stays discoverable by a client/operator even though Anthropic's own error
    shape has no separate machine-code field — never dropped silently.
    """
    return {
        "type": "error",
        "error": {
            "type": status_to_anthropic_error_type(status),
            "message": f"{message} ({code})" if code else message,
        },
    }


# ---------------------------------------------------------------------------
# Request translation: Anthropic Messages body -> internal OpenAI-shape body
# ---------------------------------------------------------------------------


def _is_valid_cache_control(cc: Any) -> bool:
    """Same validity rule as the egress adapter's own helper (mirrored, not imported —
    that file is frozen and off-limits to import internals from)."""
    return isinstance(cc, dict) and isinstance(cc.get("type"), str)


def _translate_content_blocks_to_parts(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate a list of Anthropic text blocks -> internal content-array parts.

    Mirrors the internal OpenAI-shape convention ``_openai_to_anthropic_request``
    (egress) already reads on the way OUT: a content-array message part is
    ``{"type":"text","text":...}`` optionally carrying a verbatim ``cache_control``
    key. Malformed ``cache_control`` (not a dict with a string ``type``) is
    dropped, never propagated (matches the egress adapter's own defensive rule).
    """
    parts: list[dict[str, Any]] = []
    for block in blocks:
        # defensive isinstance retained: untrusted client JSON at the wire boundary
        if not isinstance(block, dict) or block.get("type") != "text":  # pyright: ignore[reportUnnecessaryIsInstance]
            continue
        part: dict[str, Any] = {"type": "text", "text": str(block.get("text", ""))}
        cc = block.get("cache_control")
        if cc is not None and _is_valid_cache_control(cc):
            part["cache_control"] = cc
        parts.append(part)
    return parts


def _translate_system(system: Any) -> dict[str, Any] | None:
    """Translate Anthropic ``system`` (str | [Block]) -> an internal system message.

    A plain string stays a plain string (byte-identical simple case). A list of
    blocks becomes a content-array message so any ``cache_control`` breakpoint
    survives the round trip to the egress adapter unchanged (M8).
    """
    if system is None:
        return None
    if isinstance(system, str):
        if not system:
            return None
        return {"role": "system", "content": system}
    if isinstance(system, list):
        parts = _translate_content_blocks_to_parts(system)
        if not parts:
            return None
        return {"role": "system", "content": parts}
    return None


def _assistant_content_to_openai(
    content: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Translate an Anthropic assistant content-block list -> (text, tool_calls).

    Reverse of ``anthropic_upstream.py::_assistant_tool_calls_to_content``: a
    leading ``text`` block becomes the message's plain string content; each
    ``tool_use`` block becomes an OpenAI ``tool_calls`` entry (``input`` object
    -> ``arguments`` JSON string, via the canonical ``dump_tool_arguments``).
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content:
        # defensive isinstance retained: untrusted client JSON at the wire boundary
        if not isinstance(block, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(str(block.get("text", "")))
        elif btype == "tool_use":
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
    text = "".join(text_parts)
    return (text or None, tool_calls)


def _translate_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate ONE Anthropic message -> 1+ internal OpenAI-shape messages.

    - Plain string content -> one message, content unchanged.
    - assistant content carrying tool_use blocks -> one assistant message with
      ``tool_calls`` (+ optional leading text).
    - user content carrying tool_result blocks -> EACH tool_result block expands
      into its OWN ``role:"tool"`` message (reverse of the egress adapter's
      "collapse a run of consecutive tool messages into ONE user message" —
      boundary scenario: two consecutive tool_result blocks for two different
      tool_use_ids collapse correctly, in original order, each its own message).
    - Otherwise: a content-array message, preserving any ``cache_control``.

    Raises AnthropicIngressError("tool_result missing tool_use_id") when a
    tool_result block lacks ``tool_use_id`` (R1 — no correlation id available).
    """
    role = msg.get("role")
    content = msg.get("content")

    if isinstance(content, str) or content is None:
        return [{"role": role, "content": content or ""}]

    if not isinstance(content, list):
        return [{"role": role, "content": ""}]

    has_tool_use = role == "assistant" and any(
        isinstance(b, dict) and b.get("type") == "tool_use" for b in content
    )
    if has_tool_use:
        text, tool_calls = _assistant_content_to_openai(content)
        out: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            out["tool_calls"] = tool_calls
        return [out]

    has_tool_result = role == "user" and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )
    if has_tool_result:
        results: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                # A stray text block alongside tool_results (uncommon but not
                # invalid) is carried as its own plain user message, preserving
                # original order relative to the surrounding tool results.
                if block.get("type") == "text":
                    results.append({"role": "user", "content": str(block.get("text", ""))})
                continue
            tool_use_id = block.get("tool_use_id")
            if not tool_use_id:
                raise AnthropicIngressError("tool_result missing tool_use_id")
            block_content = block.get("content", "")
            # tool_result content may itself be a string or a content-block list;
            # normalize to a plain string (the internal tool-message convention).
            if isinstance(block_content, list):
                block_content = "".join(
                    str(p.get("text", "")) for p in block_content if isinstance(p, dict)
                )
            results.append(
                {"role": "tool", "tool_call_id": tool_use_id, "content": str(block_content)}
            )
        return results

    # Plain content-array message (text block(s), possibly with cache_control).
    parts = _translate_content_blocks_to_parts(content)
    return [{"role": role, "content": parts}]


def _tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Anthropic ``tools`` -> OpenAI tools (reverse of ``_tools_to_anthropic``)."""
    out: list[dict[str, Any]] = []
    for tool in tools:
        # defensive isinstance retained: untrusted client JSON at the wire boundary
        if not isinstance(tool, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            continue
        fn: dict[str, Any] = {"name": tool.get("name", "")}
        if "description" in tool:
            fn["description"] = tool["description"]
        fn["parameters"] = tool.get("input_schema", {})
        out.append({"type": "function", "function": fn})
    return out


def _tool_choice_to_openai(choice: Any) -> Any:
    """Translate Anthropic ``tool_choice`` -> OpenAI tool_choice.

    Reverse of ``_tool_choice_to_anthropic``: {type:auto}->"auto" ·
    {type:any}->"required" · {type:none}->"none" ·
    {type:tool,name}->{type:function,function:{name}}. Unknown -> None (omit).
    """
    if not isinstance(choice, dict):
        return None
    t = choice.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "none":
        return "none"
    if t == "tool":
        return {"type": "function", "function": {"name": choice.get("name", "")}}
    return None


def anthropic_messages_request_to_openai(
    payload: dict[str, Any],
    *,
    require_max_tokens: bool = True,
) -> dict[str, Any]:
    """Translate a client Anthropic Messages request body -> internal OpenAI-shape body.

    ``require_max_tokens=False`` is the ``count_tokens`` variant (contract §3:
    "NOTE: no max_tokens, no stream").

    Raises AnthropicIngressError for any R1 malformed-body condition — the
    caller (messages_router.py) maps this to 400 ERR_PAYLOAD_INVALID,
    Anthropic-shaped, BEFORE any governance call runs (Safety rule).
    """
    # defensive isinstance retained: untrusted client JSON at the wire boundary
    if not isinstance(payload, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise AnthropicIngressError("request body must be a JSON object")

    model = payload.get("model")
    if not model or not isinstance(model, str):
        raise AnthropicIngressError("'model' is required")

    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise AnthropicIngressError("'messages' must be a non-empty list")
    for m in raw_messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            raise AnthropicIngressError("each message requires role 'user' or 'assistant'")

    if require_max_tokens:
        max_tokens = payload.get("max_tokens")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise AnthropicIngressError("'max_tokens' is required and must be a positive integer")

    messages: list[dict[str, Any]] = []
    system_msg = _translate_system(payload.get("system"))
    if system_msg is not None:
        messages.append(system_msg)

    for m in raw_messages:
        messages.extend(_translate_message(m))

    result: dict[str, Any] = {"model": model, "messages": messages}

    if require_max_tokens:
        result["max_tokens"] = payload["max_tokens"]

    if "temperature" in payload:
        result["temperature"] = payload["temperature"]
    if "top_p" in payload:
        result["top_p"] = payload["top_p"]

    stop_sequences = payload.get("stop_sequences")
    if stop_sequences:
        result["stop"] = list(stop_sequences)

    tools = payload.get("tools")
    if tools:
        result["tools"] = _tools_to_openai(tools)

    tool_choice = _tool_choice_to_openai(payload.get("tool_choice"))
    if tool_choice is not None:
        result["tool_choice"] = tool_choice

    # Extended thinking (M7): passed through as an additive `thinking` key on the
    # internal body, EXACT budget_tokens unchanged — this is "the internal
    # request forwarded to the Anthropic adapter" the frozen contract's M7
    # scenario asserts on. Disclosed residual gap (see TASK.md §7 OBSERVE): the
    # frozen egress `anthropic_upstream.py` only derives `thinking` from an
    # OpenAI-wire `reasoning_effort`/`reasoning.effort` string via the D1/D2
    # ratio formula — it does not (and, being frozen, cannot in this task) read
    # this raw `thinking` key, so a request actually dialed out to the real
    # Anthropic API today does not yet carry this exact budget_tokens value at
    # the real wire level. Every OTHER adapter simply ignores the key (inert),
    # which is exactly the M7/R7 "silently dropped, never an error" behavior
    # for non-Anthropic candidates.
    thinking = payload.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        budget_tokens = thinking.get("budget_tokens")
        if isinstance(budget_tokens, int) and not isinstance(budget_tokens, bool):
            result["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}

    return result


# ---------------------------------------------------------------------------
# Response translation: internal OpenAI-shape 200 body -> Anthropic Messages body
# ---------------------------------------------------------------------------


def _finish_reason_to_stop_reason(finish_reason: str | None) -> str:
    """OpenAI finish_reason -> Anthropic stop_reason (reverse of `_map_finish_reason`)."""
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "refusal",
    }
    return mapping.get(finish_reason or "", "end_turn")


def anthropic_response_from_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Translate an internal OpenAI chat.completion 200 body -> Anthropic Messages body.

    Reverse of ``anthropic_upstream.py::_anthropic_to_openai``: content text ->
    a ``text`` block; ``message.tool_calls`` -> ``tool_use`` blocks (``arguments``
    JSON string -> ``input`` object, via the canonical ``load_tool_arguments``).
    """
    choices = body.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}

    content_blocks: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content_blocks.append({"type": "text", "text": text})

    for call in message.get("tool_calls", []) or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function", {})
        content_blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id", ""),
                "name": fn.get("name", ""),
                "input": load_tool_arguments(fn.get("arguments", "")),
            }
        )

    if not content_blocks:
        content_blocks = [{"type": "text", "text": ""}]

    usage_raw: dict[str, Any] = body.get("usage") or {}
    usage_out: dict[str, Any] = {
        "input_tokens": usage_raw.get("prompt_tokens", 0),
        "output_tokens": usage_raw.get("completion_tokens", 0),
    }
    details = usage_raw.get("prompt_tokens_details")
    if isinstance(details, dict):
        if "cached_tokens" in details:
            usage_out["cache_read_input_tokens"] = details["cached_tokens"]
        if "cache_creation_tokens" in details:
            usage_out["cache_creation_input_tokens"] = details["cache_creation_tokens"]

    return {
        "id": body.get("id", ""),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": body.get("model", ""),
        "stop_reason": _finish_reason_to_stop_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": usage_out,
    }


# ---------------------------------------------------------------------------
# Streaming translation: internal OpenAI SSE bytes -> Anthropic SSE bytes
# ---------------------------------------------------------------------------


def _sse_frame(event: str, data: dict[str, Any]) -> bytes:
    return b"event: " + event.encode() + b"\ndata: " + json.dumps(data).encode() + b"\n\n"


class _OpenAIToAnthropicSSEStepper:
    """Stateful internal-OpenAI-SSE -> Anthropic-SSE translator, fed one raw byte
    chunk at a time. Mirrors ``anthropic_upstream.py::_AnthropicSSEStepper``'s
    design in reverse: each Anthropic frame is emitted the instant its source
    OpenAI chunk is read, so TTFB is preserved (no buffering the whole stream).

    Disclosed degrade (contract §3, ACCEPTED at freeze): ``message_start.usage``
    reports 0 — the internal OpenAI SSE stream only surfaces prompt tokens at
    the TERMINAL chunk, never up front.
    """

    def __init__(self) -> None:
        self._started = False
        self._message_id = ""
        self._model = ""
        self._text_block_open = False
        self._text_block_index = 0
        self._next_block_index = 0
        self._tc_to_block: dict[int, int] = {}
        self._open_blocks: set[int] = set()
        self._finish_reason: str | None = None
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._done = False

    def _open_text_block(self) -> Iterator[bytes]:
        if not self._text_block_open:
            self._text_block_index = self._next_block_index
            self._next_block_index += 1
            self._text_block_open = True
            self._open_blocks.add(self._text_block_index)
            yield _sse_frame(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": self._text_block_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )

    def _close_all_blocks(self) -> Iterator[bytes]:
        for index in sorted(self._open_blocks):
            yield _sse_frame("content_block_stop", {"type": "content_block_stop", "index": index})
        self._open_blocks.clear()

    def _emit_message_delta_and_stop(self) -> Iterator[bytes]:
        stop_reason = _finish_reason_to_stop_reason(self._finish_reason)
        yield _sse_frame(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": self._completion_tokens},
            },
        )
        yield _sse_frame("message_stop", {"type": "message_stop"})
        self._done = True

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        if self._done:
            return
        for raw_line in chunk.split(b"\n"):
            line = raw_line.strip()
            if not line.startswith(b"data:"):
                continue
            raw = line[len(b"data:") :].strip()
            if not raw or raw == b"[DONE]":
                continue
            try:
                obj: dict[str, Any] = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            yield from self._handle_object(obj)

    def _handle_object(self, obj: dict[str, Any]) -> Iterator[bytes]:
        # An error frame carries `error` with no `choices` key (mirrors the
        # OpenAI-shaped `_sse_error_frame`/`_anthropic_error_to_openai` shape
        # used throughout use_cases.py / anthropic_upstream.py for a mid-stream
        # upstream failure — R6/R7).
        if "error" in obj and "choices" not in obj:
            if not self._started:
                yield from self._ensure_started("", "")
            yield from self._close_all_blocks()
            err = obj.get("error") or {}
            message = err.get("message", "") if isinstance(err, dict) else ""
            yield _sse_frame(
                "error",
                {"type": "error", "error": {"type": "api_error", "message": str(message)}},
            )
            self._done = True
            return

        choices = obj.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}

        if delta.get("role") == "assistant" and not self._started:
            yield from self._ensure_started(obj.get("id", ""), obj.get("model", ""))

        content = delta.get("content")
        if content:
            if not self._started:
                yield from self._ensure_started(obj.get("id", ""), obj.get("model", ""))
            yield from self._open_text_block()
            yield _sse_frame(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": self._text_block_index,
                    "delta": {"type": "text_delta", "text": content},
                },
            )

        for frag in delta.get("tool_calls") or []:
            if not isinstance(frag, dict):
                continue
            if not self._started:
                yield from self._ensure_started(obj.get("id", ""), obj.get("model", ""))
            tc_index = frag.get("index", 0)
            fn = frag.get("function") or {}
            if tc_index not in self._tc_to_block:
                block_index = self._next_block_index
                self._next_block_index += 1
                self._tc_to_block[tc_index] = block_index
                self._open_blocks.add(block_index)
                yield _sse_frame(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": frag.get("id", ""),
                            "name": fn.get("name", ""),
                            "input": {},
                        },
                    },
                )
            arguments = fn.get("arguments")
            if arguments:
                block_index = self._tc_to_block[tc_index]
                yield _sse_frame(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "input_json_delta", "partial_json": arguments},
                    },
                )

        finish_reason = choice.get("finish_reason")
        usage = obj.get("usage")
        if finish_reason is not None or usage is not None:
            if not self._started:
                yield from self._ensure_started(obj.get("id", ""), obj.get("model", ""))
            if finish_reason is not None:
                self._finish_reason = finish_reason
            if isinstance(usage, dict):
                self._prompt_tokens = usage.get("prompt_tokens", self._prompt_tokens)
                self._completion_tokens = usage.get("completion_tokens", self._completion_tokens)
            if finish_reason is not None:
                yield from self._close_all_blocks()
                yield from self._emit_message_delta_and_stop()

    def _ensure_started(self, message_id: str, model: str) -> Iterator[bytes]:
        self._started = True
        self._message_id = message_id or str(uuid.uuid4())
        self._model = model
        yield _sse_frame(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": self._message_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": self._model,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )

    def finish(self) -> Iterator[bytes]:
        """Defensive close when the source stream ended without a terminal chunk
        (e.g. an upstream/network failure the use_case itself didn't turn into
        an explicit error frame). Mirrors ``_AnthropicSSEStepper.finish()``."""
        if self._done:
            return
        if not self._started:
            yield from self._ensure_started("", "")
        yield from self._close_all_blocks()
        yield from self._emit_message_delta_and_stop()


async def translate_openai_stream_to_anthropic(
    inner: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """Wrap an internal OpenAI-SSE byte generator, yielding Anthropic SSE bytes.

    Design-for-failure: the inner generator (``CompletionUseCase.stream()``'s
    ``_wrapped()``) owns disconnect billing via its OWN GeneratorExit/
    CancelledError handling — that only fires if something actually closes
    THAT generator. Wrapping it in `async for` does not automatically propagate
    a client disconnect down to it (a bare `async for` over an abandoned
    generator never calls its `.aclose()`), so the `finally` below explicitly
    forwards close to the inner generator on every exit path (normal
    completion, exception, or GeneratorExit) — otherwise a disconnected
    Anthropic-wire client would silently skip the SAME disconnect-billing path
    `/v1/chat/completions` streaming already relies on (M11 boundary scenario).
    """
    stepper = _OpenAIToAnthropicSSEStepper()
    try:
        async for chunk in inner:
            for frame in stepper.feed(chunk):
                yield frame
        for frame in stepper.finish():
            yield frame
    finally:
        aclose = getattr(inner, "aclose", None)
        if aclose is not None:
            await aclose()


# ---------------------------------------------------------------------------
# count_tokens: a simple, documented, dependency-free token estimate
# ---------------------------------------------------------------------------


def estimate_input_tokens(internal_body: dict[str, Any]) -> int:
    """Estimate input token count for ``POST /v1/messages/count_tokens``.

    Documented heuristic (no new third-party tokenizer dependency — TASK.md §5
    Strategy item 4): ``max(1, total_chars // 4)`` across every message's text
    content (system + user/assistant + tool bodies), a coarse but stable
    approximation in the same family every OpenAI-compatible estimate in this
    codebase already uses (see the bandwidth-pacing pre-flight estimator in
    use_cases.py, `_bw_prompt_chars // 4`).
    """
    total_chars = 0
    for msg in internal_body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total_chars += len(str(part.get("text", "")))
        for call in msg.get("tool_calls", []) or []:
            if isinstance(call, dict):
                fn = call.get("function", {})
                total_chars += len(str(fn.get("arguments", "")))
    return max(1, total_chars // 4)


__all__ = [
    "AnthropicIngressError",
    "anthropic_error_body",
    "anthropic_messages_request_to_openai",
    "anthropic_response_from_openai",
    "estimate_input_tokens",
    "status_to_anthropic_error_type",
    "translate_openai_stream_to_anthropic",
]
