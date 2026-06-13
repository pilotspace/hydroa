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

import json
import logging
import math
import time
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any

import httpx

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.response_format_translation import extract_response_format
from gateway.proxy.domain.tool_translation import (
    build_tool_call_delta,
    dump_tool_arguments,
    load_tool_arguments,
    synthesize_tool_call_id,
)
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

logger = logging.getLogger(__name__)

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


def _openai_to_gemini_request(
    payload: dict[str, Any],
    *,
    default_max_tokens: int,
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
    """
    messages: list[dict[str, Any]] = payload.get("messages", [])

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
            system_parts.append(str(msg.get("content", "")))
        elif role == "assistant" and msg.get("tool_calls"):
            contents.append({"role": "model", "parts": _assistant_tool_calls_to_parts(msg)})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": str(msg.get("content", ""))}]})
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
            contents.append({"role": "user", "parts": [{"text": str(msg.get("content", ""))}]})

    generation_config: dict[str, Any] = {
        "maxOutputTokens": payload.get("max_tokens", default_max_tokens),
    }
    if "temperature" in payload:
        generation_config["temperature"] = payload["temperature"]
    if "top_p" in payload:
        generation_config["topP"] = payload["top_p"]
    stop = payload.get("stop")
    if stop is not None:
        generation_config["stopSequences"] = [stop] if isinstance(stop, str) else list(stop)

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

    return result


def _map_gemini_finish_reason(fr: str | None) -> str:
    """Map Gemini finishReason → OpenAI finish_reason.

    STOP          → "stop"
    MAX_TOKENS    → "length"
    SAFETY        → "content_filter"
    RECITATION    → "stop"
    None / other  → "stop"
    """
    mapping: dict[str, str] = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "stop",
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

    # content is null when the model returned only tool calls (OpenAI convention)
    message: dict[str, Any] = {
        "role": "assistant",
        "content": text if text else (None if tool_calls else ""),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
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
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


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


def _translate_gemini_sse(chunks: Iterable[dict[str, Any]]) -> Iterable[bytes]:
    """Translate an iterable of Gemini SSE data dicts → OpenAI SSE chunk bytes.

    Yields ``b"data: " + json_chunk + b"\\n\\n"`` frames:
      1. First: a role-announcement chunk with ``delta:{role:"assistant"}``.
      2. For each input chunk's candidates[0].content.parts[].text: a content chunk.
      3. Terminal: ONE final ``delta:{}`` chunk carrying finish_reason AND usage,
         then ``b"data: [DONE]\\n\\n"``.

    This satisfies extract_usage_from_sse (which scans in reverse for a "usage" key).
    """
    created: int = int(time.time())

    def _make_chunk(delta: dict[str, Any], finish_reason: str | None) -> dict[str, Any]:
        return {
            "id": "",
            "object": "chat.completion.chunk",
            "created": created,
            "model": "",
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }

    # First chunk: role announcement
    yield b"data: " + json.dumps(_make_chunk({"role": "assistant"}, None)).encode() + b"\n\n"

    finish_reason: str = "stop"
    last_usage: dict[str, Any] = {}
    tc_count: int = 0
    saw_tool_call: bool = False

    chunk_list = list(chunks)
    for chunk in chunk_list:
        candidates: list[dict[str, Any]] = chunk.get("candidates", [])
        if candidates:
            candidate = candidates[0]
            # Capture finish reason from this chunk if present
            if "finishReason" in candidate:
                finish_reason = _map_gemini_finish_reason(candidate["finishReason"])
            content_obj: dict[str, Any] = candidate.get("content", {})
            parts: list[dict[str, Any]] = content_obj.get("parts", [])
            for part in parts:
                if "text" in part:
                    yield (
                        b"data: "
                        + json.dumps(_make_chunk({"content": part["text"]}, None)).encode()
                        + b"\n\n"
                    )
                elif "functionCall" in part:
                    # Gemini emits the whole call in one part → one combined fragment
                    fc = part["functionCall"]
                    name = fc.get("name", "")
                    frag = build_tool_call_delta(
                        tc_count,
                        id=synthesize_tool_call_id(name, tc_count),
                        name=name,
                        arguments_fragment=dump_tool_arguments(fc.get("args", {})),
                    )
                    tc_count += 1
                    saw_tool_call = True
                    yield (
                        b"data: "
                        + json.dumps(_make_chunk({"tool_calls": [frag]}, None)).encode()
                        + b"\n\n"
                    )
        # Capture the last usageMetadata seen
        if "usageMetadata" in chunk:
            last_usage = chunk["usageMetadata"]

    # A functionCall in the stream signals a tool turn (Gemini has no tool finishReason)
    if saw_tool_call:
        finish_reason = "tool_calls"

    # Terminal chunk: finish_reason + usage
    prompt_tokens: int = last_usage.get("promptTokenCount", 0)
    completion_tokens: int = last_usage.get("candidatesTokenCount", 0)
    total_tokens: int = last_usage.get("totalTokenCount", 0)

    terminal_chunk = _make_chunk({}, finish_reason)
    terminal_chunk["usage"] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    yield b"data: " + json.dumps(terminal_chunk).encode() + b"\n\n"
    yield b"data: [DONE]\n\n"


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


class GeminiCompletionUpstream:
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
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        default_max_tokens: int = 4096,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        # Stored privately — never exposed in logs/errors/metrics
        self._api_key = api_key
        self._default_max_tokens = default_max_tokens
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
        """Build Gemini auth headers. NEVER includes a ?key= query param."""
        return {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward a non-streaming chat request to the Gemini generateContent API.

        Returns (status_code, openai_shaped_body).
        - 200  → translated OpenAI chat.completion
        - 4xx  → OpenAI error body (no exception; gateway forwards the status)
        - 5xx  → UpstreamUnavailableError
        - transport error → UpstreamUnavailableError
        """
        self._breaker.guard()

        model: str = payload["model"]
        gemini_body = _openai_to_gemini_request(
            payload,
            default_max_tokens=self._default_max_tokens,
        )

        try:
            resp = await self._client.post(
                f"/models/{model}:generateContent",
                json=gemini_body,
                headers=self._auth_headers(),
            )
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.PoolTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.NetworkError,
        ) as exc:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(str(exc)) from exc

        status = resp.status_code

        if status >= 500:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Upstream returned {status}")

        self._breaker.record_success()

        if status >= 400:
            return status, _gemini_error_to_openai(resp.json())

        return 200, _gemini_to_openai(resp.json(), model=model)

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Return an async generator that yields OpenAI SSE chunk bytes.

        The circuit breaker is checked before the stream opens.
        Gemini SSE data: frames are collected then translated via
        _translate_gemini_sse → OpenAI chunk bytes (terminal usage frame + [DONE]).
        """
        self._breaker.guard()

        async def _gen() -> AsyncIterator[bytes]:
            model: str = payload["model"]
            gemini_body = _openai_to_gemini_request(
                payload,
                default_max_tokens=self._default_max_tokens,
            )

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
                        self._breaker.on_upstream_error()
                        raise UpstreamUnavailableError(
                            f"Upstream returned {response.status_code} on stream"
                        )
                    self._breaker.record_success()

                    # Collect Gemini SSE data: frames into chunk dicts
                    chunks: list[dict[str, Any]] = []
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
                        chunks.append(data_obj)

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self._breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from exc

            # Translate the buffered chunk sequence → OpenAI SSE chunk bytes
            for chunk_bytes in _translate_gemini_sse(chunks):
                yield chunk_bytes

        return _gen()


# ---------------------------------------------------------------------------
# GoogleEmbeddingsProvider — embeddings seam (UpstreamProvider Protocol)
# ---------------------------------------------------------------------------


class GoogleEmbeddingsProvider:
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
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        # Stored privately — never exposed in logs/errors/metrics
        self._api_key = api_key
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
        """Build Gemini auth headers. NEVER includes a ?key= query param."""
        return {
            "x-goog-api-key": self._api_key,
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
        self._breaker.guard()

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
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(str(exc)) from exc

        status = resp.status_code
        if status >= 500:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Upstream returned {status}")

        self._breaker.record_success()

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
    "_gemini_embed_to_openai",
    "_gemini_error_to_openai",
    "_gemini_to_openai",
    "_map_gemini_finish_reason",
    "_openai_to_gemini_request",
    "_translate_gemini_sse",
]
