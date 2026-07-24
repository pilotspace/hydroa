"""Boundary translation for the OpenAI Responses wire (POST /v1/responses).

Contract FROZEN @ v1 (responses-api-core PLAN.md §3).

Ingress-is-translation-only (milestone invariant): the router reuses
``CompletionUseCase.complete()/.stream()`` — the governance/router/recorder
chokepoint — COMPLETELY UNCHANGED. This module only reshapes wire bytes at the
boundary:

  * ``responses_request_to_chat`` — Responses request body -> internal chat body.
  * ``chat_response_to_responses`` — internal chat completion body -> Response object.
  * ``translate_chat_stream_to_responses`` — internal chat SSE byte stream ->
    Responses named-event SSE (``event: <type>`` frames, monotonic
    ``sequence_number``, terminal ``response.completed``/``response.failed``,
    NO ``data: [DONE]`` sentinel).

The module is IO-free: no outbound call, no timeout/retry/breaker surface of its
own — the single upstream dial stays inside the existing breaker-wrapped
``CompletionUseCase`` path (PLAN.md §3 Strategy item 4). Errors that reject a
request BEFORE governance are raised as ``ResponsesIngressError`` and mapped to
the frozen problem+json codes by the router.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from gateway.usage.domain.extractor import extract_usage_from_sse

# Reject codes (PLAN.md §1 Reject / §3 Contract). Each is a 400 problem+json.
ERR_PAYLOAD_INVALID = "ERR_PAYLOAD_INVALID"
ERR_RESPONSES_BACKGROUND_UNSUPPORTED = "ERR_RESPONSES_BACKGROUND_UNSUPPORTED"
ERR_RESPONSES_TOOL_UNSUPPORTED = "ERR_RESPONSES_TOOL_UNSUPPORTED"
ERR_RESPONSES_STORE_UNSUPPORTED = "ERR_RESPONSES_STORE_UNSUPPORTED"

# Hosted/built-in tool types the stateless core never dials (PLAN.md §1 Reject).
_HOSTED_TOOL_TYPES = frozenset(
    {
        "web_search",
        "web_search_preview",
        "file_search",
        "computer_use_preview",
        "code_interpreter",
        "image_generation",
        "mcp",
        "local_shell",
    }
)


class ResponsesIngressError(Exception):
    """A boundary-level reject: carries the frozen problem+json code + message.

    Raised BEFORE any governance/upstream dial so a rejected request never
    consumes a hold or writes a usage row (PLAN.md §1 After; messages_router
    safety rule)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Request translation: Responses -> internal chat shape (M2)
# ---------------------------------------------------------------------------


def validate_responses_request(raw_body: Any) -> None:
    """Run the non-payload reject checks (background · hosted tools · non-object body).
    Raises ResponsesIngressError.

    ``store`` / ``previous_response_id`` acceptance is owned by the wave-2
    responses-state-store contract (PLAN.md §3 "State extension point"): this validator
    NO LONGER terminates them here — the responses_router orchestrates the ZDR gate,
    chain resolution, caps, and persistence around the unchanged core dial.

    Order-independent of translation: these terminate the request with their own
    codes; malformed/empty ``input`` is caught later inside
    ``responses_request_to_chat`` as ERR_PAYLOAD_INVALID."""
    if not isinstance(raw_body, dict):
        raise ResponsesIngressError(ERR_PAYLOAD_INVALID, "Request body must be a JSON object")

    if raw_body.get("background"):
        raise ResponsesIngressError(
            ERR_RESPONSES_BACKGROUND_UNSUPPORTED, "background mode is not supported"
        )

    tools = raw_body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict) and tool.get("type") in _HOSTED_TOOL_TYPES:
                raise ResponsesIngressError(
                    ERR_RESPONSES_TOOL_UNSUPPORTED,
                    f"hosted tool type '{tool.get('type')}' is not supported",
                )


def responses_request_to_chat(raw_body: dict[str, Any]) -> dict[str, Any]:
    """Translate a validated Responses body into the internal chat body.

    Assumes ``validate_responses_request`` already passed. Raises
    ResponsesIngressError(ERR_PAYLOAD_INVALID) for missing/empty ``input`` or an
    unknown input-item type."""
    messages = _translate_input(raw_body.get("instructions"), raw_body.get("input"))

    internal: dict[str, Any] = {"model": raw_body.get("model"), "messages": messages}

    max_out = raw_body.get("max_output_tokens")
    if max_out is not None:
        internal["max_tokens"] = max_out

    tools = raw_body.get("tools")
    if isinstance(tools, list) and tools:
        internal["tools"] = _translate_tools(tools)

    if "tool_choice" in raw_body and raw_body["tool_choice"] is not None:
        internal["tool_choice"] = _translate_tool_choice(raw_body["tool_choice"])

    response_format = _translate_text_format(raw_body.get("text"))
    if response_format is not None:
        internal["response_format"] = response_format

    # Fields whose shape is identical in both dialects — forwarded verbatim when present.
    for field in ("temperature", "top_p", "parallel_tool_calls", "user"):
        if field in raw_body and raw_body[field] is not None:
            internal[field] = raw_body[field]

    return internal


def _translate_input(instructions: Any, input_: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})

    if isinstance(input_, str):
        if not input_:
            raise ResponsesIngressError(ERR_PAYLOAD_INVALID, "Field 'input' must not be empty")
        messages.append({"role": "user", "content": input_})
        return messages

    if isinstance(input_, list):
        if not input_:
            raise ResponsesIngressError(ERR_PAYLOAD_INVALID, "Field 'input' must not be empty")
        for item in input_:
            _translate_input_item(item, messages)
        return messages

    raise ResponsesIngressError(ERR_PAYLOAD_INVALID, "Field 'input' is required")


def _translate_input_item(item: Any, messages: list[dict[str, Any]]) -> None:
    if not isinstance(item, dict):
        raise ResponsesIngressError(ERR_PAYLOAD_INVALID, "Each input item must be an object")

    item_type = item.get("type")

    # A message item is identified by a `role` (its `type` is "message" or absent).
    if "role" in item and item_type in (None, "message"):
        messages.append(
            {"role": item["role"], "content": _translate_message_content(item.get("content"))}
        )
        return

    if item_type == "function_call":
        tool_call: dict[str, Any] = {
            "id": item.get("call_id"),
            "type": "function",
            "function": {
                "name": item.get("name"),
                "arguments": item.get("arguments", ""),
            },
        }
        # Merge consecutive function_call items into a single assistant turn.
        last = messages[-1] if messages else None
        if isinstance(last, dict) and last.get("role") == "assistant" and "tool_calls" in last:
            last["tool_calls"].append(tool_call)
        else:
            messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})
        return

    if item_type == "function_call_output":
        messages.append(
            {
                "role": "tool",
                "tool_call_id": item.get("call_id"),
                "content": item.get("output", ""),
            }
        )
        return

    raise ResponsesIngressError(ERR_PAYLOAD_INVALID, f"Unknown input item type '{item_type}'")


def _translate_message_content(content: Any) -> Any:
    """A string passes through; a typed-part list flattens to chat content.

    A pure input_text list collapses to a plain string (chat's canonical form);
    a list carrying an image part becomes a chat multipart list."""
    if content is None or isinstance(content, str):
        return content

    if not isinstance(content, list):
        raise ResponsesIngressError(ERR_PAYLOAD_INVALID, "Message content must be a string or list")

    parts: list[dict[str, Any]] = []
    has_image = False
    for part in content:
        if not isinstance(part, dict):
            raise ResponsesIngressError(ERR_PAYLOAD_INVALID, "Each content part must be an object")
        ptype = part.get("type")
        if ptype in ("input_text", "output_text", "text"):
            parts.append({"type": "text", "text": part.get("text", "")})
        elif ptype in ("input_image", "image_url"):
            has_image = True
            url = part.get("image_url")
            if isinstance(url, dict):
                url = url.get("url")
            parts.append({"type": "image_url", "image_url": {"url": url}})
        else:
            raise ResponsesIngressError(ERR_PAYLOAD_INVALID, f"Unknown content part type '{ptype}'")

    if not has_image and all(p["type"] == "text" for p in parts):
        return "".join(str(p["text"]) for p in parts)
    return parts


def _translate_tools(tools: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            # Non-function tool types are either hosted (already rejected) or
            # unknown; skip rather than fabricate a chat tool.
            continue
        fn: dict[str, Any] = {"name": tool.get("name")}
        if "description" in tool:
            fn["description"] = tool["description"]
        if "parameters" in tool:
            fn["parameters"] = tool["parameters"]
        if "strict" in tool:
            fn["strict"] = tool["strict"]
        out.append({"type": "function", "function": fn})
    return out


def _translate_tool_choice(tool_choice: Any) -> Any:
    # String forms ("auto"/"none"/"required") are identical in both dialects.
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        return {"type": "function", "function": {"name": tool_choice.get("name")}}
    return tool_choice


def _translate_text_format(text: Any) -> dict[str, Any] | None:
    if not isinstance(text, dict):
        return None
    fmt = text.get("format")
    if not isinstance(fmt, dict):
        return None
    ftype = fmt.get("type")
    if ftype == "json_object":
        return {"type": "json_object"}
    if ftype == "json_schema":
        json_schema: dict[str, Any] = {}
        for key in ("name", "schema", "strict", "description"):
            if key in fmt and fmt[key] is not None:
                json_schema[key] = fmt[key]
        return {"type": "json_schema", "json_schema": json_schema}
    return None


# ---------------------------------------------------------------------------
# Output translation: internal chat completion -> Response object (M1/M3/M4)
# ---------------------------------------------------------------------------


def _status_from_finish(finish_reason: str | None) -> tuple[str, dict[str, Any] | None]:
    if finish_reason == "length":
        return "incomplete", {"reason": "max_output_tokens"}
    if finish_reason == "content_filter":
        return "incomplete", {"reason": "content_filter"}
    return "completed", None


def _map_usage(chat_usage: Any) -> dict[str, Any]:
    usage = chat_usage if isinstance(chat_usage, dict) else {}
    ptd = usage.get("prompt_tokens_details")
    ctd = usage.get("completion_tokens_details")
    cached = ptd.get("cached_tokens", 0) if isinstance(ptd, dict) else 0
    reasoning = ctd.get("reasoning_tokens", 0) if isinstance(ctd, dict) else 0
    return {
        "input_tokens": usage.get("prompt_tokens", 0) or 0,
        "output_tokens": usage.get("completion_tokens", 0) or 0,
        "total_tokens": usage.get("total_tokens", 0) or 0,
        "input_tokens_details": {"cached_tokens": cached or 0},
        "output_tokens_details": {"reasoning_tokens": reasoning or 0},
    }


def _message_item(text: str, status: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": _new_id("msg_"),
        "role": "assistant",
        "status": status,
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _function_call_item(tool_call: dict[str, Any], status: str) -> dict[str, Any]:
    fn = tool_call.get("function") or {}
    return {
        "type": "function_call",
        "id": _new_id("fc_"),
        "call_id": tool_call.get("id"),
        "name": fn.get("name"),
        "arguments": fn.get("arguments", ""),
        "status": status,
    }


def _base_response(served_model: str, raw_request: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "id": _new_id("resp_"),
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": served_model,
        "output": [],
        "usage": _map_usage(None),
        "store": False,
        "previous_response_id": None,
        "incomplete_details": None,
        "error": None,
        "instructions": raw_request.get("instructions"),
        "metadata": raw_request.get("metadata"),
        "temperature": raw_request.get("temperature"),
        "top_p": raw_request.get("top_p"),
        "max_output_tokens": raw_request.get("max_output_tokens"),
        "tools": raw_request.get("tools"),
        "tool_choice": raw_request.get("tool_choice"),
        "text": raw_request.get("text"),
        "parallel_tool_calls": raw_request.get("parallel_tool_calls"),
        "user": raw_request.get("user"),
        "truncation": "disabled",
    }


def chat_response_to_responses(
    chat_body: dict[str, Any], *, served_model: str, raw_request: dict[str, Any]
) -> dict[str, Any]:
    """Translate an internal chat.completion body into a Response object (M1/M3/M4)."""
    choices = chat_body.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    raw_message = choice.get("message")
    message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
    finish_reason = choice.get("finish_reason")
    status, incomplete = _status_from_finish(finish_reason)

    model = chat_body.get("model") or served_model
    resp = _base_response(model, raw_request, status)

    output: list[dict[str, Any]] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        output.append(_message_item(content, status))
    for tool_call in message.get("tool_calls") or []:
        if isinstance(tool_call, dict):
            output.append(_function_call_item(tool_call, status))

    resp["output"] = output
    resp["usage"] = _map_usage(chat_body.get("usage"))
    resp["incomplete_details"] = incomplete
    return resp


# ---------------------------------------------------------------------------
# Streaming translation: internal chat SSE -> Responses named-event SSE (M5/M6)
# ---------------------------------------------------------------------------


def _sse_frame(event: str, data: dict[str, Any]) -> bytes:
    return b"event: " + event.encode() + b"\ndata: " + json.dumps(data).encode() + b"\n\n"


class _ResponsesSSEStepper:
    """Stateful internal-chat-SSE -> Responses-SSE translator, fed one raw byte
    chunk at a time. Emits named events with a monotonically increasing
    ``sequence_number`` from 0 and NO ``[DONE]`` sentinel (PLAN.md §1 M5). A
    mid-stream error frame terminates with ``response.failed`` and never
    fabricates a completion (M6)."""

    def __init__(self, *, served_model: str, raw_request: dict[str, Any]) -> None:
        self._served_model = served_model
        self._raw_request = raw_request
        self._seq = 0
        self._resp_id = _new_id("resp_")
        self._msg_id = _new_id("msg_")
        self._item_started = False
        self._text = ""
        # Every raw chunk is buffered so the TERMINAL usage is derived exactly the
        # way the frozen chat seam bills (extract_usage_from_sse: JOIN the chunks,
        # reverse-scan for the LAST usage frame). Per-frame capture is unsafe — the
        # default provider (OpenRouter) sends `finish_reason` in one frame and the
        # usage-only frame LATER (and can split that frame across network chunks),
        # so closing on finish_reason would report zeroed usage (M5 defect, heal 1).
        self._chunks: list[bytes] = []
        self._usage: dict[str, Any] | None = None
        self._finish_reason: str | None = None
        self._done = False

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq += 1
        return seq

    def _snapshot(self, status: str) -> dict[str, Any]:
        resp = _base_response(self._served_model, self._raw_request, status)
        resp["id"] = self._resp_id
        if self._usage is not None:
            resp["usage"] = _map_usage(self._usage)
        item_status = "in_progress" if status == "in_progress" else status
        item = {
            "type": "message",
            "id": self._msg_id,
            "role": "assistant",
            "status": item_status,
            "content": [{"type": "output_text", "text": self._text, "annotations": []}],
        }
        resp["output"] = [item] if (self._item_started or status != "in_progress") else []
        if status == "incomplete":
            resp["incomplete_details"] = _status_from_finish(self._finish_reason)[1]
        return resp

    def _emit(self, event: str, data: dict[str, Any]) -> bytes:
        payload: dict[str, Any] = {"type": event, "sequence_number": self._next_seq()}
        payload.update(data)
        return _sse_frame(event, payload)

    def start(self) -> Iterator[bytes]:
        yield self._emit("response.created", {"response": self._snapshot("in_progress")})
        yield self._emit("response.in_progress", {"response": self._snapshot("in_progress")})

    def _ensure_item_started(self) -> Iterator[bytes]:
        if self._item_started:
            return
        self._item_started = True
        yield self._emit(
            "response.output_item.added",
            {
                "output_index": 0,
                "item": {
                    "type": "message",
                    "id": self._msg_id,
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                },
            },
        )
        yield self._emit(
            "response.content_part.added",
            {
                "item_id": self._msg_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        )

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        # Buffer EVERY chunk (even after `done`) so the terminal usage extraction
        # sees the whole joined stream, matching the billing path.
        self._chunks.append(chunk)
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
            if self._done:
                return

    def _handle_object(self, obj: dict[str, Any]) -> Iterator[bytes]:
        # An error frame carries `error` with no `choices` — terminate as failed (M6).
        if "error" in obj and "choices" not in obj:
            err = obj.get("error") or {}
            message = err.get("message", "") if isinstance(err, dict) else ""
            code = err.get("code") if isinstance(err, dict) else None
            resp = self._snapshot("failed")
            resp["status"] = "failed"
            resp["error"] = {"code": code, "message": str(message)}
            yield self._emit("response.failed", {"response": resp})
            self._done = True
            return

        choices = obj.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}

        if delta.get("role") == "assistant":
            yield from self._ensure_item_started()

        content = delta.get("content")
        if content:
            yield from self._ensure_item_started()
            self._text += content
            yield self._emit(
                "response.output_text.delta",
                {
                    "item_id": self._msg_id,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": content,
                },
            )

        # NOTE: usage is intentionally NOT captured here per-frame, and the
        # terminal sequence is NOT emitted on finish_reason. Both are derived at
        # stream end from the JOINED buffer (see finish()) so a provider that
        # sends usage in a later/split frame still bills real tokens (M5).
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            self._finish_reason = finish_reason

    def _close_and_complete(self) -> Iterator[bytes]:
        yield from self._ensure_item_started()
        status = _status_from_finish(self._finish_reason)[0]

        yield self._emit(
            "response.output_text.done",
            {
                "item_id": self._msg_id,
                "output_index": 0,
                "content_index": 0,
                "text": self._text,
            },
        )
        yield self._emit(
            "response.content_part.done",
            {
                "item_id": self._msg_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": self._text, "annotations": []},
            },
        )
        yield self._emit(
            "response.output_item.done",
            {
                "output_index": 0,
                "item": {
                    "type": "message",
                    "id": self._msg_id,
                    "role": "assistant",
                    "status": "completed" if status == "completed" else status,
                    "content": [{"type": "output_text", "text": self._text, "annotations": []}],
                },
            },
        )
        yield self._emit("response.completed", {"response": self._snapshot(status)})
        self._done = True

    def finish(self) -> Iterator[bytes]:
        """Close the stream once the source is fully drained.

        This is the ONLY place ``response.completed`` is emitted: the terminal
        usage is derived here from the JOINED chunk buffer via
        ``extract_usage_from_sse`` — the exact function (join + reverse-scan for
        the LAST usage frame) the frozen chat seam bills with — so a usage frame
        that arrives after ``finish_reason`` or split across network chunks still
        carries real tokens into ``response.completed`` (M5, heal 1)."""
        if self._done:
            return
        extracted = extract_usage_from_sse(self._chunks)
        if isinstance(extracted, dict):
            self._usage = extracted
        yield from self._close_and_complete()


async def translate_chat_stream_to_responses(
    inner: AsyncIterator[bytes], *, served_model: str, raw_request: dict[str, Any]
) -> AsyncIterator[bytes]:
    """Wrap the internal chat-SSE byte generator, yielding Responses SSE bytes.

    Design-for-failure: the inner generator (``CompletionUseCase.stream()``)
    owns disconnect billing via its OWN GeneratorExit/CancelledError handling; a
    bare ``async for`` over an abandoned generator never calls ``.aclose()``, so
    the ``finally`` forwards close on every exit path (mirrors
    ``anthropic_ingress.translate_openai_stream_to_anthropic``)."""
    stepper = _ResponsesSSEStepper(served_model=served_model, raw_request=raw_request)
    try:
        for frame in stepper.start():
            yield frame
        async for chunk in inner:
            for frame in stepper.feed(chunk):
                yield frame
        for frame in stepper.finish():
            yield frame
    finally:
        aclose = getattr(inner, "aclose", None)
        if aclose is not None:
            await aclose()
