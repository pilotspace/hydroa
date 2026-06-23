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
import logging
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import TYPE_CHECKING, Any

import httpx

from gateway.proxy.domain.credential_context import get_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderKeyMissing
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

logger = logging.getLogger(__name__)

# v11 JSON-mode: instruction appended to the system prompt for response_format
# json_object (free-form JSON, no schema to force a tool with).
_JSON_OBJECT_INSTRUCTION = "You must respond with a single valid JSON object and nothing else."

# ---------------------------------------------------------------------------
# D1 reasoning budget constants (FROZEN @ reasoning-passthrough CONTRACT v1)
# ---------------------------------------------------------------------------

# ratio = {low: 0.2, medium: 0.5, high: 0.8}[effort]
# raw   = round((requested max_tokens OR default_max_tokens) * ratio)
# Anthropic budget_tokens = clamp(raw, 1024, 128000)
_REASONING_EFFORT_RATIO: dict[str, float] = {"low": 0.2, "medium": 0.5, "high": 0.8}
_ANTHROPIC_BUDGET_MIN = 1024
_ANTHROPIC_BUDGET_MAX = 128000


def _extract_reasoning_effort(payload: dict[str, Any]) -> str | None:
    """Extract the OpenAI-wire reasoning effort string from the payload.

    Accepts both top-level ``reasoning_effort`` (str) and nested
    ``reasoning.effort`` (str).  Returns None when absent, malformed, or
    the value is not in {low, medium, high} (fail-safe — callers log WARN).

    Fail-safe: malformed inputs log WARN and return None — never raise.
    """
    # 1. Top-level reasoning_effort (str)
    top_level = payload.get("reasoning_effort")
    if top_level is not None:
        if not isinstance(top_level, str):
            logger.warning(
                "reasoning_field_malformed",
                extra={"field": "reasoning_effort", "value": repr(top_level)},
            )
            return None
        effort = top_level
        if effort not in _REASONING_EFFORT_RATIO:
            logger.warning(
                "reasoning_effort_unrecognized",
                extra={"effort": effort},
            )
            return None
        return effort

    # 2. Nested reasoning.effort (dict with str effort)
    nested = payload.get("reasoning")
    if nested is not None:
        if not isinstance(nested, dict):
            logger.warning(
                "reasoning_field_malformed",
                extra={"field": "reasoning", "value": repr(nested)},
            )
            return None
        effort_val = nested.get("effort")
        if effort_val is None:
            return None
        if not isinstance(effort_val, str):
            logger.warning(
                "reasoning_field_malformed",
                extra={"field": "reasoning.effort", "value": repr(effort_val)},
            )
            return None
        if effort_val not in _REASONING_EFFORT_RATIO:
            logger.warning(
                "reasoning_effort_unrecognized",
                extra={"effort": effort_val},
            )
            return None
        return effort_val

    return None


def _compute_anthropic_budget(effort: str, max_tokens: int) -> int:
    """Compute Anthropic budget_tokens using the D1 ratio formula (FROZEN)."""
    ratio = _REASONING_EFFORT_RATIO[effort]
    raw = round(max_tokens * ratio)
    return max(_ANTHROPIC_BUDGET_MIN, min(_ANTHROPIC_BUDGET_MAX, raw))


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


def _has_client_cache_control(messages: list[dict[str, Any]]) -> bool:
    """Return True if ANY message content part carries a ``cache_control`` key.

    Detects the OpenRouter convention: cache_control on a content-array part.
    Used to decide whether to suppress auto-inject (client intent respected).
    """
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "cache_control" in part:
                    return True
    return False


def _is_valid_cache_control(cc: Any) -> bool:
    """Return True iff cc is a dict with a string ``type`` key.

    Anthropic supports {type:"ephemeral"} and may add more types; we validate
    that the shape is a dict with at least a string type field (forward-compatible).
    """
    return isinstance(cc, dict) and isinstance(cc.get("type"), str)


def _auto_inject_cache_control(result: dict[str, Any]) -> None:
    """Inject cache_control:{type:"ephemeral"} on system block + last tool (≤4 breakpoints).

    Mutates *result* in-place:
    - If ``system`` is a string → converted to a list of one text block with cache_control.
    - Last tool in ``tools`` list → gets cache_control:{type:"ephemeral"} added.
    Anthropic allows ≤4 cache breakpoints; this helper uses at most 2.
    """
    injected = 0

    # System block: convert to list form and add cache_control
    if "system" in result:
        sys_val = result["system"]
        if isinstance(sys_val, str):
            result["system"] = [
                {"type": "text", "text": sys_val, "cache_control": {"type": "ephemeral"}}
            ]
        elif isinstance(sys_val, list) and sys_val:
            # Already in list form (from a passthrough path) — add to last block
            last = sys_val[-1]
            if isinstance(last, dict) and "cache_control" not in last:
                sys_val[-1] = {**last, "cache_control": {"type": "ephemeral"}}
        injected += 1

    # Last tool definition
    tools = result.get("tools", [])
    if tools and injected < 4:
        last_tool = tools[-1]
        if isinstance(last_tool, dict) and "cache_control" not in last_tool:
            tools[-1] = {**last_tool, "cache_control": {"type": "ephemeral"}}
        injected += 1


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
    auto_cache: bool = False,
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

    Cache control (prompt-cache-passthrough TASK.md §3):
    - PASS-THROUGH: if any content part carries ``cache_control``, forward it verbatim
      to the Anthropic block and suppress auto-inject (client intent respected).
      Malformed ``cache_control`` (not a dict with a string ``type``) → drop + WARN.
    - AUTO-INJECT (auto_cache=True AND no client cc): inject ephemeral breakpoint on
      the system block + last tool. When there is nothing to anchor → no-op + DEBUG.
    - auto_cache=False AND no client cc → byte-identical output (no new keys).

    Raises ValueError("tool_call_id_required") when a ``role:"tool"`` message lacks
    ``tool_call_id`` (no correlation id for the tool_result block).
    """
    messages: list[dict[str, Any]] = payload.get("messages", [])

    # Detect whether the client supplied any cache_control markers (before we translate)
    client_has_cc = _has_client_cache_control(messages)

    system_parts: list[str] = []
    # system_blocks tracks blocks with potential cache_control (only populated when a
    # system message uses list-content form so we can preserve the block structure).
    system_blocks: list[dict[str, Any]] = []
    system_has_cc = False
    non_system: list[dict[str, Any]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        role = msg.get("role")
        if role == "system":
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    text = str(part.get("text", ""))
                    system_parts.append(text)
                    cc = part.get("cache_control")
                    if cc is not None:
                        if _is_valid_cache_control(cc):
                            system_blocks.append(
                                {"type": "text", "text": text, "cache_control": cc}
                            )
                            system_has_cc = True
                        else:
                            logger.warning(
                                "cache_control_malformed",
                                extra={"cache_control": repr(cc), "role": "system"},
                            )
                            system_blocks.append({"type": "text", "text": text})
                    else:
                        system_blocks.append({"type": "text", "text": text})
            else:
                text = str(content)
                system_parts.append(text)
                system_blocks.append({"type": "text", "text": text})
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
            # Non-system, non-tool message: check for content-array form with cache_control
            content = msg.get("content")
            if isinstance(content, list):
                blocks: list[dict[str, Any]] = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    block: dict[str, Any] = {"type": "text", "text": str(part.get("text", ""))}
                    cc = part.get("cache_control")
                    if cc is not None:
                        if _is_valid_cache_control(cc):
                            block["cache_control"] = cc
                        else:
                            logger.warning(
                                "cache_control_malformed",
                                extra={"cache_control": repr(cc), "role": role},
                            )
                    blocks.append(block)
                non_system.append({"role": role, "content": blocks})
            else:
                non_system.append({"role": role, "content": content or ""})
            i += 1

    # D1/D2 reasoning: resolve effort → budget_tokens → bump max_tokens (FROZEN CONTRACT v1)
    _mt_raw = payload.get("max_tokens", default_max_tokens)
    base_max_tokens: int = int(_mt_raw) if _mt_raw is not None else default_max_tokens
    effort = _extract_reasoning_effort(payload)
    if effort is not None:
        budget_tokens = _compute_anthropic_budget(effort, base_max_tokens)
        # D2: bump max_tokens so the answer keeps its full room above the thinking budget
        effective_max_tokens = budget_tokens + base_max_tokens
        thinking_block: dict[str, Any] = {"type": "enabled", "budget_tokens": budget_tokens}
    else:
        effective_max_tokens = base_max_tokens
        thinking_block = {}

    result: dict[str, Any] = {
        "model": payload["model"],
        "messages": non_system,
        "max_tokens": effective_max_tokens,
    }

    if thinking_block:
        result["thinking"] = thinking_block

    if system_parts:
        # Use block form when the client explicitly set cache_control on a system part;
        # otherwise use plain string form (byte-identical to the pre-cache code path).
        if system_has_cc:
            result["system"] = system_blocks
        else:
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
            # json_object may run after cache injection → preserve block form if present
            if isinstance(existing_system, list):
                result["system"] = [
                    *existing_system,
                    {"type": "text", "text": _JSON_OBJECT_INSTRUCTION},
                ]
            else:
                result["system"] = (
                    f"{existing_system}\n\n{_JSON_OBJECT_INSTRUCTION}".strip()
                    if existing_system
                    else _JSON_OBJECT_INSTRUCTION
                )

    # prompt-cache-passthrough (TASK.md §3): cache injection runs AFTER response_format
    # processing so the final tools list is complete (json_schema appends a coercion tool).
    # Anchors are ONLY caller-supplied system/tools: the synthetic json_object system
    # instruction is NOT a stable prefix worth caching; likewise a json_schema coercion
    # tool is transient. We therefore check against what the ORIGINAL payload carried:
    #   - original_has_system: at least one system message (system_parts populated)
    #   - original_tools: caller's own tools (not the coercion tool)
    # The coercion tool IS included in inject (it's in result["tools"] already) because
    # it acts as a stable function definition alongside real tools; the json_object system
    # instruction is EXCLUDED (it's pure synthetic glue, not a stable prompt prefix).
    original_has_system = bool(system_parts)
    original_has_tools = bool(payload.get("tools"))
    if auto_cache and not client_has_cc:
        if original_has_system or original_has_tools:
            _auto_inject_cache_control(result)
        else:
            logger.debug("cache_nothing_to_anchor", extra={"model": payload.get("model", "")})

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
    input_tokens: int = usage_raw.get("input_tokens", 0)
    # prompt-cache-passthrough (TASK.md §3): surface Anthropic cache token counts.
    cache_read: int = usage_raw.get("cache_read_input_tokens", 0)
    cache_creation: int = usage_raw.get("cache_creation_input_tokens", 0)
    # Total prompt_tokens = fresh input + cache_read + cache_creation
    prompt_tokens: int = input_tokens + cache_read + cache_creation
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

    usage_out: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    # Only emit prompt_tokens_details when there are cache tokens to surface.
    if cache_read > 0 or cache_creation > 0:
        usage_out["prompt_tokens_details"] = {
            "cached_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
        }

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
        "usage": usage_out,
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


class _AnthropicSSEStepper:
    """Stateful Anthropic-SSE → OpenAI-SSE translator, fed one event at a time.

    ``step(event_name, data)`` yields 0+ OpenAI chunk frames for that event;
    ``finish()`` yields the terminal frame + ``[DONE]`` if not already emitted.
    This single core drives BOTH the buffered wrapper (``_translate_anthropic_sse``)
    and the live streaming adapter — so each translated frame can be emitted the
    instant its source event arrives, without changing the byte output. The
    per-instance ``created`` timestamp is stamped once at construction.

    Events consumed:
      message_start         → first chunk ``delta:{role:"assistant"}``; capture input_tokens.
      content_block_start   → tool_use block → first ``delta:{tool_calls:[{id,name}]}`` (v10).
      content_block_delta   → text_delta → chunk ``delta:{content:<text>}``;
                              input_json_delta → ``delta:{tool_calls:[{arguments}]}`` (v10).
      message_delta         → capture stop_reason + output_tokens.
      message_stop          → emit terminal frame + [DONE].
      ping / content_block_stop / unknown → ignored.
    """

    def __init__(self) -> None:
        self._prompt_tokens: int = 0
        self._completion_tokens: int = 0
        self._finish_reason: str = "stop"
        self._chunk_id: str = ""
        self._chunk_model: str = ""
        self._created: int = int(time.time())
        self._terminal_emitted: bool = False
        # v10 tool streaming: map each Anthropic content-block index → its OpenAI
        # tool_calls index (counts only tool_use blocks, text blocks excluded).
        self._block_to_tc: dict[int, int] = {}
        self._tc_count: int = 0
        # v11 JSON-mode: a streamed coercion ("json_output") tool_use block is unwrapped —
        # its input_json_delta fragments stream as delta.content, not delta.tool_calls; the
        # block is excluded from block_to_tc and the terminal finish_reason is "stop".
        self._coercion_block_index: int | None = None
        self._saw_coercion: bool = False
        # prompt-cache-passthrough (TASK.md §3): cache token counts from message_start.usage
        self._cached_tokens: int = 0
        self._cache_creation_tokens: int = 0

    def _make_chunk(self, delta: dict[str, Any], fr: str | None) -> dict[str, Any]:
        return {
            "id": self._chunk_id,
            "object": "chat.completion.chunk",
            "created": self._created,
            "model": self._chunk_model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": fr,
                }
            ],
        }

    def _emit_terminal(self) -> Iterator[bytes]:
        terminal_chunk: dict[str, Any] = self._make_chunk({}, self._finish_reason)
        usage_out: dict[str, Any] = {
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "total_tokens": self._prompt_tokens + self._completion_tokens,
        }
        # prompt-cache-passthrough (TASK.md §3): emit cache token details when present.
        if self._cached_tokens > 0 or self._cache_creation_tokens > 0:
            usage_out["prompt_tokens_details"] = {
                "cached_tokens": self._cached_tokens,
                "cache_creation_tokens": self._cache_creation_tokens,
            }
        terminal_chunk["usage"] = usage_out
        yield b"data: " + json.dumps(terminal_chunk).encode() + b"\n\n"
        yield b"data: [DONE]\n\n"
        self._terminal_emitted = True

    def step(self, event_name: str, data: dict[str, Any]) -> Iterator[bytes]:
        if event_name == "message_start":
            msg = data.get("message", {})
            self._chunk_id = msg.get("id", "")
            self._chunk_model = msg.get("model", "")
            usage = msg.get("usage", {})
            # prompt-cache-passthrough (TASK.md §3): capture cache token counts from
            # message_start.usage; total prompt_tokens = fresh input + read + creation.
            cache_read: int = usage.get("cache_read_input_tokens", 0)
            cache_creation: int = usage.get("cache_creation_input_tokens", 0)
            self._cached_tokens = cache_read
            self._cache_creation_tokens = cache_creation
            self._prompt_tokens = usage.get("input_tokens", 0) + cache_read + cache_creation
            # Yield first role chunk
            chunk = self._make_chunk({"role": "assistant"}, None)
            yield b"data: " + json.dumps(chunk).encode() + b"\n\n"

        elif event_name == "content_block_start":
            block = data.get("content_block", {})
            if block.get("type") == "tool_use":
                block_index = data.get("index", 0)
                if is_coercion_tool_call(block.get("name", "")):
                    # JSON-mode coercion block: its input streams as content (below);
                    # emit no tool_calls fragment and skip the tool index.
                    self._coercion_block_index = block_index
                    self._saw_coercion = True
                else:
                    tc_index = self._tc_count
                    self._block_to_tc[block_index] = tc_index
                    self._tc_count += 1
                    frag = build_tool_call_delta(
                        tc_index, id=block.get("id", ""), name=block.get("name", "")
                    )
                    chunk = self._make_chunk({"tool_calls": [frag]}, None)
                    yield b"data: " + json.dumps(chunk).encode() + b"\n\n"

        elif event_name == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                chunk = self._make_chunk({"content": text}, None)
                yield b"data: " + json.dumps(chunk).encode() + b"\n\n"
            elif delta.get("type") == "input_json_delta":
                block_index = data.get("index", 0)
                if (
                    self._coercion_block_index is not None
                    and block_index == self._coercion_block_index
                ):
                    # coercion JSON streams as delta.content, not delta.tool_calls
                    chunk = self._make_chunk({"content": delta.get("partial_json", "")}, None)
                    yield b"data: " + json.dumps(chunk).encode() + b"\n\n"
                else:
                    tc_index = self._block_to_tc.get(block_index)
                    if tc_index is not None:
                        frag = build_tool_call_delta(
                            tc_index, arguments_fragment=delta.get("partial_json", "")
                        )
                        chunk = self._make_chunk({"tool_calls": [frag]}, None)
                        yield b"data: " + json.dumps(chunk).encode() + b"\n\n"

        elif event_name == "message_delta":
            delta = data.get("delta", {})
            self._finish_reason = _map_finish_reason(delta.get("stop_reason"))
            # JSON-mode: when only the coercion tool was used, the stop is a normal "stop".
            if self._saw_coercion and self._tc_count == 0:
                self._finish_reason = "stop"
            usage = data.get("usage", {})
            self._completion_tokens = usage.get("output_tokens", self._completion_tokens)

        elif event_name == "message_stop":
            yield from self._emit_terminal()

        # ping / content_block_start / content_block_stop / unknown → ignored

    def finish(self) -> Iterator[bytes]:
        # If the stream ends without a message_stop event, emit the terminal frame anyway.
        if not self._terminal_emitted:
            yield from self._emit_terminal()


def _translate_anthropic_sse(  # pyright: ignore[reportUnusedFunction]  # kept as the byte-identical translation contract exercised by the unit tests
    events: Iterable[tuple[str, dict[str, Any]]],
) -> Iterable[bytes]:
    """Translate an iterable of (event_name, data_obj) pairs → OpenAI SSE chunk bytes.

    Thin buffered wrapper over ``_AnthropicSSEStepper`` (byte-identical to the
    historical implementation). The streaming adapter drives the same stepper live;
    this entry point is retained as the unit-tested translation contract.
    """
    stepper = _AnthropicSSEStepper()
    for event_name, data in events:
        yield from stepper.step(event_name, data)
    yield from stepper.finish()


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
        base_url: str = "https://api.anthropic.com/v1",
        anthropic_version: str = "2023-06-01",
        default_max_tokens: int = 4096,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
        auto_cache: bool = False,
    ) -> None:
        self._version = anthropic_version
        self._default_max_tokens = default_max_tokens
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._retry_deadline_s = retry_deadline_s
        self._metrics_registry = metrics_registry
        # prompt-cache-passthrough (TASK.md §3): whether to auto-inject ephemeral
        # cache_control on the stable prefix (system block + last tool) when the
        # client has not supplied any cache_control markers.
        self._auto_cache = auto_cache

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
        """Build Anthropic auth headers from the request-scoped credential contextvar.

        NEVER includes Authorization Bearer (Anthropic uses x-api-key).
        Raises ProviderKeyMissing when the contextvar is unset or non-Bearer.
        """
        cred = get_provider_credential()
        if not isinstance(cred, BearerCredential):
            raise ProviderKeyMissing("anthropic")
        return {
            "x-api-key": cred.secret.get_secret_value(),
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
            auto_cache=self._auto_cache,
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
        Anthropic SSE events are translated LIVE via a stateful _AnthropicSSEStepper:
        each OpenAI chunk is yielded the instant its source event is read off the wire
        (incremental delivery — TTFB ≈ first token). finish() emits the terminal usage
        frame + [DONE] after the last event.
        """
        self._breaker.guard()

        async def _gen() -> AsyncIterator[bytes]:
            anthropic_body = {
                **_openai_to_anthropic_request(
                    payload,
                    default_max_tokens=self._default_max_tokens,
                    auto_cache=self._auto_cache,
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

                    # Drive the stepper LIVE: translate + yield each OpenAI frame the
                    # instant its source Anthropic event arrives on the wire (incremental
                    # delivery — TTFB ≈ first token, not full generation). The stepper is
                    # stateful across events; finish() emits the terminal frame + [DONE].
                    stepper = _AnthropicSSEStepper()
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
                            for frame in stepper.step(event_name, data_obj):
                                yield frame
                            current_event = ""
                        elif line == "":
                            # blank line = SSE frame boundary; reset pending event name
                            current_event = ""
                    for frame in stepper.finish():
                        yield frame

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self._breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from None

        return _gen()
