"""Infrastructure adapters: GeminiCompletionUpstream + GoogleEmbeddingsProvider.

Translates OpenAI chat-completions ⇄ Google Gemini generateContent /
streamGenerateContent SSE (chat) and OpenAI embeddings ⇄ Gemini
embedContent / batchEmbedContents (embeddings).

Wire protocol (chat):
  POST {base_url}/models/{model}:generateContent
  POST {base_url}/models/{model}:streamGenerateContent?alt=sse
  Headers: x-goog-api-key: <key>  content-type: application/json
  NEVER a ?key= query parameter — keeps the secret out of URLs/access logs.

Wire protocol (embeddings):
  POST {base_url}/models/{model}:embedContent    (single string input)
  POST {base_url}/models/{model}:batchEmbedContents  (list input)
  Headers: x-goog-api-key: <key>  content-type: application/json

Resilience mirrors AnthropicCompletionUpstream / OpenAIDirectProvider:
  - httpx.AsyncClient with per-timeout knobs
  - Per-instance CircuitBreaker (5 consecutive failures → 30 s open)
  - 5xx / transport errors → UpstreamUnavailableError (gateway → 502 / v8 fallback)
  - 4xx → pass through as OpenAI-shaped error body; no exception raised

Security:
  The Google api key is a SECRET — NEVER logged, echoed, committed, or placed in
  metric labels / span attributes / exception messages / URL query params.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import re
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import TYPE_CHECKING, Any

import httpx

from gateway.core.error_catalog import PAYLOAD_INPUT_TOO_LONG, UNSUPPORTED_CONTENT_PART
from gateway.proxy.domain.credential_context import get_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderKeyMissing
from gateway.proxy.domain.response_format_translation import extract_response_format
from gateway.proxy.domain.tool_translation import (
    build_tool_call_delta,
    dump_tool_arguments,
    load_tool_arguments,
    synthesize_tool_call_id,
)
from gateway.proxy.domain.web_search import (
    WEB_SEARCH_FLAG,
    _normalize_gemini_grounding,
    native_web_search_tool,
)
from gateway.proxy.infrastructure.tenant_breaker_registry import TenantScopedBreakerMixin
from gateway.proxy.infrastructure.upstream_retry import execute_with_retry
from gateway.usage.domain.partial_usage import publish_partial_usage

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# D1 reasoning budget constants (FROZEN @ reasoning-passthrough CONTRACT v1)
# ---------------------------------------------------------------------------

# ratio = {low: 0.2, medium: 0.5, high: 0.8}[effort]
# raw   = round((requested max_tokens OR default_max_tokens) * ratio)
# Gemini thinkingBudget = clamp(raw, 1, 24576)  # 2.5 Flash ceiling
_REASONING_EFFORT_RATIO: dict[str, float] = {"low": 0.2, "medium": 0.5, "high": 0.8}
_GEMINI_BUDGET_MIN = 1
_GEMINI_BUDGET_MAX = 24576


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


def _compute_gemini_budget(effort: str, max_tokens: int) -> int:
    """Compute Gemini thinkingBudget using the D1 ratio formula (FROZEN)."""
    ratio = _REASONING_EFFORT_RATIO[effort]
    raw = round(max_tokens * ratio)
    return max(_GEMINI_BUDGET_MIN, min(_GEMINI_BUDGET_MAX, raw))


_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0
_STREAM_READ_TIMEOUT = 300.0

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


# ---------------------------------------------------------------------------
# Pure translation helpers (module-level, no I/O — unit-tested directly)
# ---------------------------------------------------------------------------


def _tools_to_gemini(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI tools → Gemini ``tools:[{functionDeclarations:[...]}]``."""
    decls: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function", {})
        decl: dict[str, Any] = {"name": fn.get("name", "")}
        if "description" in fn:
            decl["description"] = fn["description"]
        if "parameters" in fn:
            decl["parameters"] = fn["parameters"]
        decls.append(decl)
    return [{"functionDeclarations": decls}]


def _tool_choice_to_gemini(choice: Any) -> dict[str, Any] | None:
    """Translate OpenAI tool_choice → Gemini functionCallingConfig.

    "auto"→{mode:AUTO} · "required"→{mode:ANY} · "none"→{mode:NONE} ·
    {type:function, function:{name}}→{mode:ANY, allowedFunctionNames:[name]}.
    Unknown → None (omit toolConfig).
    """
    if choice == "auto":
        return {"mode": "AUTO"}
    if choice == "required":
        return {"mode": "ANY"}
    if choice == "none":
        return {"mode": "NONE"}
    if isinstance(choice, dict) and choice.get("type") == "function":
        name = choice.get("function", {}).get("name", "")
        return {"mode": "ANY", "allowedFunctionNames": [name]}
    return None


def _assistant_tool_calls_to_parts(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Build Gemini ``model`` parts from an OpenAI assistant message with tool_calls.

    Leading text becomes a ``{text}`` part; each tool call a ``{functionCall:{name,args}}``
    part (``arguments`` JSON string → ``args`` object).
    """
    parts: list[dict[str, Any]] = []
    text = msg.get("content")
    if isinstance(text, str) and text:
        parts.append({"text": text})
    for call in msg.get("tool_calls", []):
        fn = call.get("function", {})
        parts.append(
            {
                "functionCall": {
                    "name": fn.get("name", ""),
                    "args": load_tool_arguments(fn.get("arguments", "")),
                }
            }
        )
    return parts


# ---------------------------------------------------------------------------
# Multimodal / inline-data helpers  (FROZEN CONTRACT v1)
# ---------------------------------------------------------------------------

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;,]+);base64,(?P<b64>.*)$", re.DOTALL)


def _data_url_to_inline(
    url: str,
    max_inline_bytes: int,
    running_total: list[int],
) -> dict[str, str]:
    """Decode a data: URL into a Gemini inlineData dict.

    Args:
        url: Must match ``^data:<mime>;base64,<b64>$``.
        max_inline_bytes: Running cap across the whole request (0 = unlimited).
        running_total: Single-element list accumulating decoded byte count in-place.

    Returns:
        ``{"mimeType": "<mime>", "data": "<b64-string>"}`` — Gemini wants the
        base64 STRING, not raw bytes.

    Raises:
        ValueError("only_data_url_supported"): URL does not match the data: pattern
            (e.g. an https:// URL — SSRF guard; we never fetch remote URLs).
        ValueError("invalid_data_url_base64"): The base64 payload is malformed.
        ValueError("inline_too_large"): Adding this part would exceed max_inline_bytes.
    """
    m = _DATA_URL_RE.match(url)
    if m is None:
        raise ValueError("only_data_url_supported")
    mime: str = m.group("mime")
    b64: str = m.group("b64")
    try:
        decoded = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid_data_url_base64") from exc
    running_total[0] += len(decoded)
    if max_inline_bytes > 0 and running_total[0] > max_inline_bytes:
        raise ValueError("inline_too_large")
    return {"mimeType": mime, "data": b64}


def _content_to_gemini_parts(
    content: object,
    max_inline_bytes: int,
    running_total: list[int],
) -> list[dict[str, Any]]:
    """Translate an OpenAI message ``content`` value into a list of Gemini parts.

    - ``str``  → ``[{"text": content}]``  (byte-identical to the pre-multimodal path).
    - ``list`` → each part dict translated by type:
        - ``"text"``      → ``{"text": part["text"]}``
        - ``"image_url"`` → ``{"inlineData": ...}`` via _data_url_to_inline
        - ``"video_url"`` → ``{"inlineData": ...}`` via _data_url_to_inline
        - anything else   → ``ValueError("unsupported_content_part")``
    - any other type (None, int, …) → ``[{"text": str(content)}]``  (leniency parity).

    Raises:
        ValueError: forwarded from _data_url_to_inline, or "unsupported_content_part".
    """
    if isinstance(content, str):
        return [{"text": content}]
    if isinstance(content, list):
        parts: list[dict[str, Any]] = []
        for part in content:
            t = part.get("type") if isinstance(part, dict) else None
            if t == "text":
                try:
                    parts.append({"text": part["text"]})
                except KeyError as exc:
                    raise ValueError("unsupported_content_part") from exc
            elif t == "image_url":
                try:
                    url: str = part["image_url"]["url"]
                except KeyError as exc:
                    raise ValueError("unsupported_content_part") from exc
                parts.append(
                    {"inlineData": _data_url_to_inline(url, max_inline_bytes, running_total)}
                )
            elif t == "video_url":
                try:
                    url = part["video_url"]["url"]
                except KeyError as exc:
                    raise ValueError("unsupported_content_part") from exc
                parts.append(
                    {"inlineData": _data_url_to_inline(url, max_inline_bytes, running_total)}
                )
            else:
                raise ValueError("unsupported_content_part")
        return parts
    # Fallback: coerce via str() — preserves current leniency for None/int/etc.
    return [{"text": str(content)}]


def _openai_to_gemini_request(
    payload: dict[str, Any],
    *,
    default_max_tokens: int,
    max_inline_bytes: int = 0,
) -> dict[str, Any]:
    """Translate an OpenAI chat-completions request body → Gemini generateContent body.

    - role=="system" messages → top-level ``systemInstruction:{parts:[{text}]}``;
      multiple system messages joined with "\\n\\n".
    - role=="user" → Gemini role "user"; role=="assistant" → Gemini role "model".
    - An assistant message with ``tool_calls`` → a ``model`` content of ``functionCall``
      parts (v10). A ``role:"tool"`` message → a ``user`` content with a
      ``functionResponse`` part, the function name resolved from the tool_call_id via the
      assistant ``tool_calls`` seen earlier in the request (Gemini correlates by name).
    - Each remaining non-system message → ``{role, parts:[{text: content}]}``.
    - ``generationConfig``: maxOutputTokens (from max_tokens or default), plus
      temperature / topP / stopSequences when present.
    - ``tools`` → Gemini ``tools`` (functionDeclarations); ``tool_choice`` → ``toolConfig``.
    - ``model`` is NOT in the body — it goes in the URL path.

    Raises ValueError("tool_call_id_required") when a ``role:"tool"`` message lacks
    ``tool_call_id`` (no key to resolve the functionResponse name).
    Raises ValueError("only_data_url_supported" | "invalid_data_url_base64" |
    "unsupported_content_part" | "inline_too_large") from _content_to_gemini_parts
    when content is a multimodal list with invalid / oversized parts.
    """
    messages: list[dict[str, Any]] = payload.get("messages", [])

    # Single running_total shared across ALL messages so the size cap is per-request.
    running_total: list[int] = [0]

    # First pass: map every assistant tool_call id → its function name (for the
    # functionResponse correlation, which Gemini keys by name, not id).
    id_to_name: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "assistant":
            for call in msg.get("tool_calls", []):
                cid = call.get("id")
                if cid:
                    id_to_name[cid] = call.get("function", {}).get("name", "")

    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            # systemInstruction is text-only — keep str() coercion unchanged.
            system_parts.append(str(msg.get("content", "")))
        elif role == "assistant" and msg.get("tool_calls"):
            contents.append({"role": "model", "parts": _assistant_tool_calls_to_parts(msg)})
        elif role == "assistant":
            contents.append(
                {
                    "role": "model",
                    "parts": _content_to_gemini_parts(
                        msg.get("content", ""), max_inline_bytes, running_total
                    ),
                }
            )
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if not tool_call_id:
                raise ValueError("tool_call_id_required")
            name = id_to_name.get(tool_call_id, tool_call_id)
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": name,
                                "response": {"result": msg.get("content", "")},
                            }
                        }
                    ],
                }
            )
        else:
            contents.append(
                {
                    "role": "user",
                    "parts": _content_to_gemini_parts(
                        msg.get("content", ""), max_inline_bytes, running_total
                    ),
                }
            )

    _mt_raw = payload.get("max_tokens", default_max_tokens)
    base_max_tokens: int = int(_mt_raw) if _mt_raw is not None else default_max_tokens

    generation_config: dict[str, Any] = {
        "maxOutputTokens": base_max_tokens,
    }
    if "temperature" in payload:
        generation_config["temperature"] = payload["temperature"]
    if "top_p" in payload:
        generation_config["topP"] = payload["top_p"]
    stop = payload.get("stop")
    if stop is not None:
        generation_config["stopSequences"] = [stop] if isinstance(stop, str) else list(stop)

    # D1 reasoning: reasoning_effort → generationConfig.thinkingConfig (FROZEN CONTRACT v1)
    effort = _extract_reasoning_effort(payload)
    if effort is not None:
        thinking_budget = _compute_gemini_budget(effort, base_max_tokens)
        generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

    # response_format → Gemini native structured output (v11). extract_response_format
    # returns None for absent / {type:"text"} (byte-identical v9/v10), else json_object /
    # json_schema; it raises ERR_UNSUPPORTED_RESPONSE_FORMAT / ERR_INVALID_JSON_SCHEMA.
    response_format = extract_response_format(payload)
    if response_format is not None:
        generation_config["responseMimeType"] = "application/json"
        json_schema = response_format.get("json_schema")
        if response_format["type"] == "json_schema" and json_schema is not None:
            generation_config["responseSchema"] = json_schema["schema"]

    result: dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
    }
    if system_parts:
        result["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

    tools = payload.get("tools")
    if tools:
        result["tools"] = _tools_to_gemini(tools)
    fcc = _tool_choice_to_gemini(payload.get("tool_choice"))
    if fcc is not None:
        result["toolConfig"] = {"functionCallingConfig": fcc}

    # web-search-grounding: if the source payload has truthy web_search, append the
    # Gemini native {"googleSearch":{}} as a SEPARATE sibling entry in result["tools"]
    # (NOT inside functionDeclarations). The raw flag is NEVER copied into the Gemini
    # body (it builds fresh), so it cannot leak.
    #
    # KNOWN LIMITATION (v41 delta): some Gemini model versions reject a request that
    # carries BOTH googleSearch grounding AND functionDeclarations in the same `tools`
    # array (returns 400). v41's chat surface never sends function tools, so this cannot
    # arise from the product; a raw API caller combining both gets Gemini's own 400
    # surfaced faithfully (v35 principle). We do NOT speculatively reject here because
    # newer Gemini relaxes the constraint — tracked as a SPEC delta for live-verify.
    if payload.get(WEB_SEARCH_FLAG):
        gs_tool = native_web_search_tool("google")
        if gs_tool is not None:
            existing_tools = result.get("tools", [])
            result["tools"] = [*list(existing_tools), gs_tool]

    return result


def _map_gemini_finish_reason(fr: str | None) -> str:
    """Map Gemini finishReason → OpenAI finish_reason.

    STOP               → "stop"
    MAX_TOKENS         → "length"
    SAFETY             → "content_filter"
    RECITATION         → "stop"
    BLOCKLIST          → "content_filter"
    PROHIBITED_CONTENT → "content_filter"
    SPII               → "content_filter"
    IMAGE_SAFETY       → "content_filter"
    None / other       → "stop"
    """
    mapping: dict[str, str] = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "stop",
        # Content-policy codes — all map to "content_filter" so clients can distinguish
        # policy-blocked responses from normal completions.
        "BLOCKLIST": "content_filter",
        "PROHIBITED_CONTENT": "content_filter",
        "SPII": "content_filter",
        "IMAGE_SAFETY": "content_filter",
    }
    return mapping.get(fr or "", "stop")


def _gemini_to_openai(body: dict[str, Any], *, model: str) -> dict[str, Any]:
    """Translate a Gemini generateContent 200 response → OpenAI chat.completion body.

    Defensive: missing candidates → empty content, finish_reason "stop", usage zeros.
    """
    candidates: list[dict[str, Any]] = body.get("candidates", [])
    tool_calls: list[dict[str, Any]] = []
    if candidates:
        candidate = candidates[0]
        content_obj: dict[str, Any] = candidate.get("content", {})
        parts: list[dict[str, Any]] = content_obj.get("parts", [])
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        for part in parts:
            fc = part.get("functionCall")
            if fc:
                name = fc.get("name", "")
                tool_calls.append(
                    {
                        "id": synthesize_tool_call_id(name, len(tool_calls)),
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": dump_tool_arguments(fc.get("args", {})),
                        },
                    }
                )
        # functionCall presence signals a tool turn (Gemini has no distinct finishReason)
        finish_reason = (
            "tool_calls" if tool_calls else _map_gemini_finish_reason(candidate.get("finishReason"))
        )
    else:
        text = ""
        finish_reason = "stop"

    usage_raw: dict[str, Any] = body.get("usageMetadata", {})
    prompt_tokens: int = usage_raw.get("promptTokenCount", 0)
    completion_tokens: int = usage_raw.get("candidatesTokenCount", 0)
    total_tokens: int = usage_raw.get("totalTokenCount", 0)
    # D3 authoritative reasoning tokens: thoughtsTokenCount → completion_tokens_details
    thoughts_token_count: int | None = usage_raw.get("thoughtsTokenCount")

    # content is null when the model returned only tool calls (OpenAI convention)
    message: dict[str, Any] = {
        "role": "assistant",
        "content": text if text else (None if tool_calls else ""),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage_out: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if thoughts_token_count is not None:
        usage_out["completion_tokens_details"] = {"reasoning_tokens": thoughts_token_count}
    # prompt-cache-passthrough (TASK.md §3): Gemini 2.5 implicit caching surfaces the
    # cached portion via cachedContentTokenCount; promptTokenCount already includes it.
    cached_content_tokens: int | None = usage_raw.get("cachedContentTokenCount")
    if cached_content_tokens is not None and cached_content_tokens > 0:
        usage_out["prompt_tokens_details"] = {"cached_tokens": cached_content_tokens}

    response: dict[str, Any] = {
        "id": "",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage_out,
    }

    # web-search-grounding citation passthrough (non-stream only).
    # If the first candidate carries groundingMetadata, normalize it into
    # response["grounding"]. Absent → do NOT add the field (no fabrication).
    if candidates:
        grounding_meta = candidates[0].get("groundingMetadata")
        if isinstance(grounding_meta, dict):
            grounding = _normalize_gemini_grounding(grounding_meta)
            if grounding is not None:
                response["grounding"] = grounding

    return response


def _gemini_error_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Translate a Gemini error envelope → OpenAI error body.

    Gemini shape: ``{error:{code, message, status}}``
    OpenAI shape: ``{error:{message, type, code}}``

    type/code = lowercased Gemini status (e.g. "invalid_argument") or "upstream_error"
    when absent. Defensive: missing fields degrade gracefully.
    """
    err_obj: dict[str, Any] = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
    message: str = str(err_obj.get("message", "")) if "message" in err_obj else ""
    status: str = str(err_obj.get("status", "")) if "status" in err_obj else ""

    mapped_type = status.lower() if status else "upstream_error"

    return {
        "error": {
            "message": message,
            "type": mapped_type,
            "code": mapped_type,
        }
    }


class _GeminiSSEStepper:
    """Stateful Gemini-SSE → OpenAI-SSE translator, fed one chunk at a time.

    ``step(chunk)`` yields a one-time role announcement before the first content/tool
    frame, then content/tool frames for that chunk; ``finish()`` yields the terminal
    finish_reason + usage frame and ``[DONE]``. This single core drives BOTH the
    buffered wrapper (``_translate_gemini_sse``) and the live streaming adapter — so
    each frame flows as its source chunk arrives, byte-identical to the buffered output.
    The per-instance ``created`` timestamp is stamped once at construction.

    Frame order:
      1. role-announcement chunk with ``delta:{role:"assistant"}`` (once, lazily).
      2. For each chunk's candidates[0].content.parts[].text/functionCall: a frame.
      3. Terminal ``delta:{}`` chunk carrying finish_reason AND usage, then ``[DONE]``.
    extract_usage_from_sse (reverse scan for "usage") still finds the terminal frame.
    """

    def __init__(self) -> None:
        self._created: int = int(time.time())
        self._role_emitted: bool = False
        self._finish_reason: str = "stop"
        self._last_usage: dict[str, Any] = {}
        self._tc_count: int = 0
        self._saw_tool_call: bool = False

    def _make_chunk(self, delta: dict[str, Any], finish_reason: str | None) -> dict[str, Any]:
        return {
            "id": "",
            "object": "chat.completion.chunk",
            "created": self._created,
            "model": "",
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }

    def _role_frame(self) -> Iterator[bytes]:
        """Emit the role-announcement chunk exactly once, before any content."""
        if not self._role_emitted:
            self._role_emitted = True
            yield (
                b"data: "
                + json.dumps(self._make_chunk({"role": "assistant"}, None)).encode()
                + b"\n\n"
            )

    def step(self, chunk: dict[str, Any]) -> Iterator[bytes]:
        yield from self._role_frame()
        candidates: list[dict[str, Any]] = chunk.get("candidates", [])
        if candidates:
            candidate = candidates[0]
            # Capture finish reason from this chunk if present
            if "finishReason" in candidate:
                self._finish_reason = _map_gemini_finish_reason(candidate["finishReason"])
            content_obj: dict[str, Any] = candidate.get("content", {})
            parts: list[dict[str, Any]] = content_obj.get("parts", [])
            for part in parts:
                if "text" in part:
                    yield (
                        b"data: "
                        + json.dumps(self._make_chunk({"content": part["text"]}, None)).encode()
                        + b"\n\n"
                    )
                elif "functionCall" in part:
                    # Gemini emits the whole call in one part → one combined fragment
                    fc = part["functionCall"]
                    name = fc.get("name", "")
                    frag = build_tool_call_delta(
                        self._tc_count,
                        id=synthesize_tool_call_id(name, self._tc_count),
                        name=name,
                        arguments_fragment=dump_tool_arguments(fc.get("args", {})),
                    )
                    self._tc_count += 1
                    self._saw_tool_call = True
                    yield (
                        b"data: "
                        + json.dumps(self._make_chunk({"tool_calls": [frag]}, None)).encode()
                        + b"\n\n"
                    )
        # Capture the last usageMetadata seen
        if "usageMetadata" in chunk:
            self._last_usage = chunk["usageMetadata"]
            # disconnect-billing-all-providers (v34): publish to the partial-usage sink
            # so the disconnect handler has a floor even before finish() runs.
            publish_partial_usage(
                self._last_usage.get("promptTokenCount", 0),
                self._last_usage.get("candidatesTokenCount", 0),
            )

    def finish(self) -> Iterator[bytes]:
        # Emit the role frame even for an empty stream (matches the buffered output).
        yield from self._role_frame()
        # A functionCall in the stream signals a tool turn (Gemini has no tool finishReason)
        if self._saw_tool_call:
            self._finish_reason = "tool_calls"

        prompt_tokens: int = self._last_usage.get("promptTokenCount", 0)
        completion_tokens: int = self._last_usage.get("candidatesTokenCount", 0)
        total_tokens: int = self._last_usage.get("totalTokenCount", 0)
        # D3 authoritative reasoning tokens: thoughtsTokenCount → completion_tokens_details
        thoughts_token_count: int | None = self._last_usage.get("thoughtsTokenCount")

        terminal_chunk = self._make_chunk({}, self._finish_reason)
        usage_out: dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        if thoughts_token_count is not None:
            usage_out["completion_tokens_details"] = {"reasoning_tokens": thoughts_token_count}
        # prompt-cache-passthrough (TASK.md §3): surface Gemini implicit cache count.
        cached_content_tokens: int | None = self._last_usage.get("cachedContentTokenCount")
        if cached_content_tokens is not None and cached_content_tokens > 0:
            usage_out["prompt_tokens_details"] = {"cached_tokens": cached_content_tokens}
        terminal_chunk["usage"] = usage_out
        yield b"data: " + json.dumps(terminal_chunk).encode() + b"\n\n"
        yield b"data: [DONE]\n\n"


def _translate_gemini_sse(chunks: Iterable[dict[str, Any]]) -> Iterable[bytes]:
    """Translate an iterable of Gemini SSE data dicts → OpenAI SSE chunk bytes.

    Thin buffered wrapper over ``_GeminiSSEStepper`` (byte-identical to the historical
    implementation). The streaming adapter drives the same stepper live.
    """
    stepper = _GeminiSSEStepper()
    for chunk in chunks:
        yield from stepper.step(chunk)
    yield from stepper.finish()


def _gemini_embed_to_openai(
    body: dict[str, Any],
    model: str,
    inp: str | list[str],
    *,
    exact_tokens: int | None = None,
) -> dict[str, Any]:
    """Translate a Gemini embed response → OpenAI embeddings list.

    Single (embedContent): body has ``{embedding:{values:[float]}}``
    Batch (batchEmbedContents): body has ``{embeddings:[{values:[float]}]}``

    Order is preserved. Usage billing (v12):
      - ``exact_tokens`` set  → bill on the EXACT Gemini ``:countTokens`` count.
      - ``exact_tokens`` None → documented FALLBACK to the ``ceil(chars/4)`` estimate
        (Gemini embed responses carry no token count).
    """
    if "embedding" in body:
        # Single embedContent response
        vectors = [body["embedding"]["values"]]
    else:
        # Batch batchEmbedContents response
        vectors = [e["values"] for e in body.get("embeddings", [])]

    data = [{"object": "embedding", "index": i, "embedding": vec} for i, vec in enumerate(vectors)]

    if exact_tokens is not None:
        tokens = exact_tokens
    else:
        # Fallback estimate: chars / 4 (documented approximation; used only when the
        # :countTokens leg failed — see GoogleEmbeddingsProvider.post_json).
        if isinstance(inp, str):
            total_chars = len(inp)
        else:
            total_chars = sum(len(s) for s in inp)
        tokens = max(1, math.ceil(total_chars / 4))

    return {
        "object": "list",
        "data": data,
        "model": model,
        "usage": {
            "prompt_tokens": tokens,
            "total_tokens": tokens,
        },
    }


# ---------------------------------------------------------------------------
# GeminiCompletionUpstream — chat seam (CompletionUpstream Protocol)
# ---------------------------------------------------------------------------


class GeminiCompletionUpstream(TenantScopedBreakerMixin):
    """Forwards chat completions to the Google Gemini generateContent API.

    Implements the CompletionUpstream Protocol (complete + stream).
    Translates OpenAI chat-completions ⇄ Gemini generateContent shapes.

    A single instance is shared for the lifetime of the application.
    The circuit breaker state is per-instance (per-replica).

    SECURITY: the api_key is NEVER logged, echoed, or placed in any
    metric label / span attribute / exception message / URL query param.
    Auth is the x-goog-api-key HEADER only.
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        default_max_tokens: int = 4096,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
        max_inline_bytes: int = 0,
    ) -> None:
        self._default_max_tokens = default_max_tokens
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._retry_deadline_s = retry_deadline_s
        self._metrics_registry = metrics_registry
        self._max_inline_bytes = max_inline_bytes

        self._init_tenant_breakers()
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
        """Build Gemini auth headers from the request-scoped credential contextvar.

        NEVER includes a ?key= query param — keeps the secret out of URLs/access logs.
        Raises ProviderKeyMissing when the contextvar is unset or non-Bearer.
        """
        cred = get_provider_credential()
        if not isinstance(cred, BearerCredential):
            raise ProviderKeyMissing("google")
        return {
            "x-goog-api-key": cred.secret.get_secret_value(),
            "content-type": "application/json",
        }

    @staticmethod
    def _map_translation_error(exc: ValueError) -> None:
        """Convert a translation-layer ValueError into the appropriate ProblemError.

        Mirrors the call-site error-mapping pattern used for tool_call_id_required.
        Called at the start of complete() and stream() before any upstream contact.

        - "inline_too_large"           → 413 PAYLOAD_INPUT_TOO_LONG
        - "only_data_url_supported"
          "invalid_data_url_base64"
          "unsupported_content_part"   → 400 UNSUPPORTED_CONTENT_PART
        - anything else (e.g. "tool_call_id_required", ERR_UNSUPPORTED_RESPONSE_FORMAT)
          → re-raise so the existing caller-level handling is undisturbed.
        """
        msg = str(exc)
        if msg == "inline_too_large":
            raise PAYLOAD_INPUT_TOO_LONG.exc(
                detail="Inline data exceeds the per-request size limit"
            ) from exc
        if msg in (
            "only_data_url_supported",
            "invalid_data_url_base64",
            "unsupported_content_part",
        ):
            raise UNSUPPORTED_CONTENT_PART.exc() from exc
        raise  # re-raise — e.g. tool_call_id_required, ERR_UNSUPPORTED_RESPONSE_FORMAT

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward a non-streaming chat request to the Gemini generateContent API.

        Returns (status_code, openai_shaped_body).
        - 200  → translated OpenAI chat.completion
        - 4xx (non-408) → OpenAI error body (no exception; gateway forwards the status)
        - 5xx / 429 / 408 / connect error / pool timeout → retried up to max_retries
          (unified retry seam); exhausted → UpstreamUnavailableError
        - read/write timeout / network error → UpstreamUnavailableError (not retried)

        Request translation is pure and runs ONCE, outside the retry loop.
        Translation ValueErrors (inline_too_large, only_data_url_supported, …) are
        converted to ProblemError 4xx BEFORE any upstream contact.
        """
        breaker = self._breaker_for()
        model: str = payload["model"]
        try:
            gemini_body = _openai_to_gemini_request(
                payload,
                default_max_tokens=self._default_max_tokens,
                max_inline_bytes=self._max_inline_bytes,
            )
        except ValueError as exc:
            self._map_translation_error(exc)
            raise  # unreachable — _map_translation_error always raises

        async def _do_request() -> httpx.Response:
            return await self._client.post(
                f"/models/{model}:generateContent",
                json=gemini_body,
                headers=self._auth_headers(),
            )

        def _render(resp: httpx.Response) -> tuple[int, dict[str, Any]]:
            if resp.status_code >= 400:
                return resp.status_code, _gemini_error_to_openai(resp.json())
            return 200, _gemini_to_openai(resp.json(), model=model)

        return await execute_with_retry(
            _do_request,
            _render,
            breaker=breaker,
            provider="gemini",
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            deadline_s=self._retry_deadline_s,
            metrics_registry=self._metrics_registry,
        )

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Return an async generator that yields OpenAI SSE chunk bytes.

        The circuit breaker is checked before the stream opens.
        Gemini SSE data: frames are translated LIVE via a stateful _GeminiSSEStepper —
        each OpenAI chunk is yielded as its source frame arrives (incremental delivery);
        finish() emits the terminal usage frame + [DONE].
        Translation ValueErrors are converted to ProblemError 4xx before any bytes yield.
        """
        breaker = self._breaker_for()
        breaker.guard()

        # Translate BEFORE entering the async generator so errors surface as
        # ProblemError (caught by the use-case pre-generator path → clean 4xx),
        # not as mid-stream UpstreamUnavailableError.
        try:
            gemini_body = _openai_to_gemini_request(
                payload,
                default_max_tokens=self._default_max_tokens,
                max_inline_bytes=self._max_inline_bytes,
            )
        except ValueError as exc:
            self._map_translation_error(exc)
            raise  # unreachable

        async def _gen() -> AsyncIterator[bytes]:
            model: str = payload["model"]

            try:
                async with self._client.stream(
                    "POST",
                    f"/models/{model}:streamGenerateContent",
                    params={"alt": "sse"},
                    json=gemini_body,
                    headers=self._auth_headers(),
                    timeout=httpx.Timeout(
                        connect=_CONNECT_TIMEOUT,
                        read=_STREAM_READ_TIMEOUT,
                        write=_NON_STREAM_TIMEOUT,
                        pool=_CONNECT_TIMEOUT,
                    ),
                ) as response:
                    if response.status_code >= 500:
                        breaker.on_upstream_error()
                        raise UpstreamUnavailableError(
                            f"Upstream returned {response.status_code} on stream"
                        )
                    breaker.record_success()

                    # Drive the stepper LIVE: translate + yield each OpenAI frame the
                    # instant its source Gemini SSE chunk arrives (incremental delivery
                    # — TTFB ≈ first token). finish() emits the terminal frame + [DONE].
                    stepper = _GeminiSSEStepper()
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        raw = line[len("data:") :].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            data_obj: dict[str, Any] = json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        for frame in stepper.step(data_obj):
                            yield frame
                    for frame in stepper.finish():
                        yield frame

            # RemoteProtocolError = graceful mid-stream peer-close (Finding C, v35):
            # a ProtocolError, not a NetworkError — map it like any upstream failure so
            # the use-case mid-stream catch can emit the terminal SSE error frame + [DONE].
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as exc:
                breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from None

        return _gen()


# ---------------------------------------------------------------------------
# GoogleEmbeddingsProvider — embeddings seam (UpstreamProvider Protocol)
# ---------------------------------------------------------------------------


class GoogleEmbeddingsProvider(TenantScopedBreakerMixin):
    """Direct HTTP adapter for Google Gemini embedding endpoints.

    Implements the UpstreamProvider Protocol (post_json / post_multipart / stream_bytes).
    Translates OpenAI /v1/embeddings ⇄ Gemini embedContent / batchEmbedContents.

    A single instance is created per create_app() call when google_api_key is set.

    SECURITY: the api_key is NEVER logged, echoed, or placed in any
    metric label / span attribute / exception message / URL query param.
    Auth is the x-goog-api-key HEADER only.
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        self._metrics_registry = metrics_registry

        self._init_tenant_breakers()
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
        """Build Gemini auth headers from the request-scoped credential contextvar.

        NEVER includes a ?key= query param — keeps the secret out of URLs/access logs.
        Raises ProviderKeyMissing when the contextvar is unset or non-Bearer.
        """
        cred = get_provider_credential()
        if not isinstance(cred, BearerCredential):
            raise ProviderKeyMissing("google")
        return {
            "x-goog-api-key": cred.secret.get_secret_value(),
            "content-type": "application/json",
        }

    async def _count_gemini_tokens(self, model: str, inp: str | list[str]) -> int | None:
        """Return the EXACT Gemini token count for the embed input, or None (fail-SAFE).

        POST /models/{model}:countTokens with ALL inputs as contents (one round-trip,
        not one-per-input) → {totalTokens:int>=1}. Any failure (timeout/network/status
        >=400 / missing-or-<1 totalTokens) returns None so the caller falls back to the
        chars/4 estimate. A count-leg failure NEVER raises and NEVER trips the embed
        circuit breaker — billing accuracy is best-effort; the embedding is the product.
        """
        texts = [inp] if isinstance(inp, str) else list(inp)
        request_body: dict[str, Any] = {"contents": [{"parts": [{"text": s}]} for s in texts]}
        try:
            resp = await self._client.post(
                f"/models/{model}:countTokens",
                json=request_body,
                headers=self._auth_headers(),
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            return None
        if resp.status_code >= 400:
            return None
        try:
            total = resp.json().get("totalTokens")
        except (ValueError, AttributeError):
            return None
        if not isinstance(total, int) or total < 1:
            return None
        return total

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """POST embeddings path; translate OpenAI input → Gemini embed wire call.

        str input  → POST /models/{model}:embedContent
        list input → POST /models/{model}:batchEmbedContents
        Returns (status_code, openai_shaped_body).
        """
        breaker = self._breaker_for()
        breaker.guard()

        model: str = payload["model"]
        inp: str | list[str] = payload["input"]

        if isinstance(inp, str):
            url = f"/models/{model}:embedContent"
            request_body: dict[str, Any] = {"content": {"parts": [{"text": inp}]}}
        else:
            url = f"/models/{model}:batchEmbedContents"
            request_body = {
                "requests": [
                    {
                        "model": f"models/{model}",
                        "content": {"parts": [{"text": s}]},
                    }
                    for s in inp
                ]
            }

        try:
            resp = await self._client.post(
                url,
                json=request_body,
                headers=self._auth_headers(),
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            breaker.on_upstream_error()
            raise UpstreamUnavailableError(str(exc)) from None

        status = resp.status_code
        if status >= 500:
            breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Upstream returned {status}")

        breaker.record_success()

        if status >= 400:
            return status, _gemini_error_to_openai(resp.json())

        # Exact-token billing (v12): count tokens via :countTokens AFTER a successful
        # embed; fall back to the chars/4 estimate (WARN) if the count leg fails.
        exact_tokens = await self._count_gemini_tokens(model, inp)
        if exact_tokens is None:
            logger.warning("gemini embed token count unavailable; billing on chars/4 estimate")

        return 200, _gemini_embed_to_openai(resp.json(), model, inp, exact_tokens=exact_tokens)

    async def post_multipart(
        self,
        path: str,
        files: dict[str, Any],
        data: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """Raise UpstreamUnavailableError — Gemini images/audio out of scope."""
        raise UpstreamUnavailableError("gemini: unsupported modality")

    def stream_bytes(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Return an async generator that raises UpstreamUnavailableError on first iteration.

        Gemini images/audio out of scope — never reached for embedding-modality models.
        """

        async def _gen() -> AsyncIterator[bytes]:
            raise UpstreamUnavailableError("gemini: unsupported modality")
            # Unreachable — makes the type checker happy that this is an async generator
            yield b""  # pragma: no cover

        return _gen()


__all__ = [
    "GeminiCompletionUpstream",
    "GoogleEmbeddingsProvider",
    "_GeminiSSEStepper",
    "_content_to_gemini_parts",
    "_data_url_to_inline",
    "_gemini_embed_to_openai",
    "_gemini_error_to_openai",
    "_gemini_to_openai",
    "_map_gemini_finish_reason",
    "_openai_to_gemini_request",
    "_translate_gemini_sse",
]
