"""Red suite for web_search feature — translate web_search:true into native grounding tools.

Tests the full seam:
  - native_web_search_tool() helper returns the correct provider-native tool shape.
  - OpenAI/OpenRouter adapter: web_search injected into tools, flag stripped.
  - Anthropic adapter: web_search_20250305 injected, flag never copied.
  - Gemini adapter: googleSearch sibling entry injected, flag never copied.
  - Existing function tools are preserved alongside the grounding tool.
  - Knob-off (GATEWAY_WEB_SEARCH_ENABLED=False): flag stripped, no tool injected.
  - Non-grounding providers (bedrock/azure shape): nothing injected, no error.
  - SECURITY pin: upstream body NEVER has "web_search" key for any provider.
  - Anthropic non-stream citations survive -> response["grounding"] set.
  - Gemini non-stream grounding metadata survives -> response["grounding"] set.
  - No grounding in response -> "grounding" key absent (no fabrication).

All HTTP behavior uses httpx.MockTransport — no network, no real key.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.credential_context import reset_provider_credential, set_provider_credential
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.anthropic_upstream import (
    AnthropicCompletionUpstream,
    _anthropic_to_openai,
    _openai_to_anthropic_request,
)
from gateway.proxy.infrastructure.gemini_upstream import (
    GeminiCompletionUpstream,
    _gemini_to_openai,
    _openai_to_gemini_request,
)
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider

# RED until BUILD creates web_search.py
from gateway.proxy.domain.web_search import (
    WEB_SEARCH_FLAG,
    _normalize_gemini_grounding,
    native_web_search_tool,
)

pytestmark = pytest.mark.asyncio

_TEST_SECRET = "test-key-abc"
_TEST_CRED = BearerCredential(secret=_TEST_SECRET)

# ---------------------------------------------------------------------------
# Minimal fixture helpers
# ---------------------------------------------------------------------------

_BASE_MESSAGES = [{"role": "user", "content": "What is the weather today?"}]

_ANTHROPIC_200 = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-sonnet-20241022",
    "content": [{"type": "text", "text": "It's sunny."}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 5},
}

_GEMINI_200 = {
    "candidates": [
        {
            "content": {"parts": [{"text": "Sunny."}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 10,
        "candidatesTokenCount": 3,
        "totalTokenCount": 13,
    },
}

_OPENAI_200 = {
    "id": "chatcmpl-01",
    "object": "chat.completion",
    "created": 1234567890,
    "model": "gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Sunny."},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
}


def _openrouter_adapter(handler: object) -> OpenRouterCompletionUpstream:
    adapter = OpenRouterCompletionUpstream.__new__(OpenRouterCompletionUpstream)
    from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
    adapter._breaker = CircuitBreaker()
    adapter._client = httpx.AsyncClient(
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    adapter._max_retries = 0
    adapter._backoff_base = 0.5
    adapter._retry_deadline_s = 0.0
    adapter._metrics_registry = None
    adapter._usage_accounting = False
    return adapter


def _openai_adapter(handler: object) -> OpenAIDirectProvider:
    adapter = OpenAIDirectProvider.__new__(OpenAIDirectProvider)
    from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
    adapter._breaker = CircuitBreaker()
    adapter._client = httpx.AsyncClient(
        base_url="https://api.openai.com/v1",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    adapter._max_retries = 0
    adapter._backoff_base = 0.5
    adapter._retry_deadline_s = 0.0
    adapter._metrics_registry = None
    return adapter


def _anthropic_adapter(handler: object) -> AnthropicCompletionUpstream:
    adapter = AnthropicCompletionUpstream(
        base_url="https://api.anthropic.com/v1",
        default_max_tokens=4096,
    )
    adapter._client = httpx.AsyncClient(
        base_url="https://api.anthropic.com/v1",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type,attr-defined]
    )
    return adapter


def _gemini_adapter(handler: object) -> GeminiCompletionUpstream:
    _BASE = "https://generativelanguage.googleapis.com/v1beta"
    adapter = GeminiCompletionUpstream(base_url=_BASE, default_max_tokens=4096)
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url=_BASE, transport=httpx.MockTransport(handler)  # type: ignore[arg-type]
    )
    return adapter


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


# ---------------------------------------------------------------------------
# Unit tests: native_web_search_tool helper
# ---------------------------------------------------------------------------


def test_native_web_search_tool_openai() -> None:
    tool = native_web_search_tool("openai")
    assert tool == {"type": "web_search_preview"}


def test_native_web_search_tool_openrouter() -> None:
    tool = native_web_search_tool("openrouter")
    assert tool == {"type": "web_search_preview"}


def test_native_web_search_tool_anthropic() -> None:
    tool = native_web_search_tool("anthropic")
    assert tool == {"type": "web_search_20250305", "name": "web_search"}


def test_native_web_search_tool_google() -> None:
    tool = native_web_search_tool("google")
    assert tool == {"googleSearch": {}}


def test_native_web_search_tool_bedrock_is_none() -> None:
    assert native_web_search_tool("bedrock") is None


def test_native_web_search_tool_azure_is_none() -> None:
    assert native_web_search_tool("azure") is None


def test_native_web_search_tool_unknown_is_none() -> None:
    assert native_web_search_tool("unknown_provider") is None


def test_web_search_flag_constant() -> None:
    assert WEB_SEARCH_FLAG == "web_search"


# ---------------------------------------------------------------------------
# test_openai_native_tool: OpenRouter/OpenAI — web_search injected; flag stripped
# ---------------------------------------------------------------------------


async def test_openai_native_tool_openrouter() -> None:
    """OpenRouter: web_search:true -> tools contains {"type":"web_search_preview"}; flag stripped."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_OPENAI_200)

    adapter = _openrouter_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await adapter.complete(
            {
                "model": "openai/gpt-4o",
                "messages": _BASE_MESSAGES,
                "web_search": True,
            }
        )
    finally:
        reset_provider_credential(token)

    body = captured["body"]
    assert "web_search" not in body, "web_search flag must be stripped from upstream body"
    tools = body.get("tools", [])
    assert {"type": "web_search_preview"} in tools, "web_search_preview tool must be injected"


async def test_openai_native_tool_openai() -> None:
    """OpenAI direct: web_search:true -> tools contains {"type":"web_search_preview"}; flag stripped."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_OPENAI_200)

    adapter = _openai_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await adapter.complete(
            {
                "model": "gpt-4o",
                "messages": _BASE_MESSAGES,
                "web_search": True,
            }
        )
    finally:
        reset_provider_credential(token)

    body = captured["body"]
    assert "web_search" not in body, "web_search flag must be stripped from OpenAI body"
    tools = body.get("tools", [])
    assert {"type": "web_search_preview"} in tools, "web_search_preview tool must be injected"


# ---------------------------------------------------------------------------
# test_anthropic_native_tool: Anthropic — web_search_20250305 injected; flag never copied
# ---------------------------------------------------------------------------


def test_anthropic_native_tool_request_translation() -> None:
    """_openai_to_anthropic_request: web_search -> anthropic tools has web_search_20250305."""
    result = _openai_to_anthropic_request(
        {
            "model": "claude-3-5-sonnet-20241022",
            "messages": _BASE_MESSAGES,
            "web_search": True,
        },
        default_max_tokens=4096,
    )
    # Flag must not appear in the translated body
    assert "web_search" not in result, "web_search flag must never leak into Anthropic body"
    tools = result.get("tools", [])
    assert any(
        t.get("type") == "web_search_20250305" for t in tools
    ), "web_search_20250305 tool must be in Anthropic tools"


async def test_anthropic_native_tool_adapter() -> None:
    """AnthropicCompletionUpstream: web_search:true -> upstream body has web_search_20250305 tool."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ANTHROPIC_200)

    adapter = _anthropic_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await adapter.complete(
            {
                "model": "claude-3-5-sonnet-20241022",
                "messages": _BASE_MESSAGES,
                "web_search": True,
            }
        )
    finally:
        reset_provider_credential(token)

    body = captured["body"]
    assert "web_search" not in body
    tools = body.get("tools", [])
    assert any(
        t.get("type") == "web_search_20250305" for t in tools
    ), "web_search_20250305 must be in Anthropic upstream body tools"


# ---------------------------------------------------------------------------
# test_gemini_googlesearch: Gemini — googleSearch sibling; flag never copied
# ---------------------------------------------------------------------------


def test_gemini_googlesearch_request_translation() -> None:
    """_openai_to_gemini_request: web_search -> tools has a {"googleSearch":{}} sibling."""
    result = _openai_to_gemini_request(
        {
            "model": "gemini-1.5-flash",
            "messages": _BASE_MESSAGES,
            "web_search": True,
        },
        default_max_tokens=4096,
    )
    assert "web_search" not in result, "web_search flag must never leak into Gemini body"
    tools = result.get("tools", [])
    assert {"googleSearch": {}} in tools, "googleSearch entry must be in Gemini tools list"


async def test_gemini_googlesearch_adapter() -> None:
    """GeminiCompletionUpstream: web_search:true -> upstream body has googleSearch sibling."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_GEMINI_200)

    adapter = _gemini_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await adapter.complete(
            {
                "model": "gemini-1.5-flash",
                "messages": _BASE_MESSAGES,
                "web_search": True,
            }
        )
    finally:
        reset_provider_credential(token)

    body = captured["body"]
    assert "web_search" not in body
    tools = body.get("tools", [])
    assert {"googleSearch": {}} in tools, "googleSearch entry must be in Gemini upstream body"


# ---------------------------------------------------------------------------
# test_function_tools_preserved: existing function tools + web_search -> BOTH present
# ---------------------------------------------------------------------------

_FUNCTION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {"name": "get_weather", "description": "Gets weather"},
}


async def test_function_tools_preserved_openrouter() -> None:
    """OpenRouter: function tool + web_search -> BOTH present in the real upstream body.

    Drives the actual OpenRouterCompletionUpstream.complete() and asserts on the
    captured upstream body — so the test fails if the adapter's injection is removed
    (no inline re-implementation of production logic).
    """
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_OPENAI_200)

    adapter = _openrouter_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await adapter.complete(
            {
                "model": "openai/gpt-4o",
                "messages": _BASE_MESSAGES,
                "tools": [_FUNCTION_TOOL],
                "web_search": True,
            }
        )
    finally:
        reset_provider_credential(token)

    body = captured["body"]
    assert "web_search" not in body, "flag must be stripped from the upstream body"
    tools = body.get("tools", [])
    assert _FUNCTION_TOOL in tools, "the caller's function tool must be preserved"
    assert {"type": "web_search_preview"} in tools, "the native grounding tool must also be injected"


def test_function_tools_preserved_anthropic() -> None:
    """Anthropic: function tool + web_search -> both in result tools."""
    result = _openai_to_anthropic_request(
        {
            "model": "claude-3-5-sonnet-20241022",
            "messages": _BASE_MESSAGES,
            "tools": [_FUNCTION_TOOL],
            "web_search": True,
        },
        default_max_tokens=4096,
    )
    tools = result.get("tools", [])
    assert any(t.get("name") == "get_weather" for t in tools), "function tool must be preserved"
    assert any(
        t.get("type") == "web_search_20250305" for t in tools
    ), "web_search_20250305 must also be present"


def test_function_tools_preserved_gemini() -> None:
    """Gemini: functionDeclarations entry + googleSearch sibling both present."""
    result = _openai_to_gemini_request(
        {
            "model": "gemini-1.5-flash",
            "messages": _BASE_MESSAGES,
            "tools": [_FUNCTION_TOOL],
            "web_search": True,
        },
        default_max_tokens=4096,
    )
    tools = result.get("tools", [])
    has_function_decls = any("functionDeclarations" in t for t in tools)
    has_google_search = {"googleSearch": {}} in tools
    assert has_function_decls, "functionDeclarations entry must be preserved"
    assert has_google_search, "googleSearch sibling must also be present"


# ---------------------------------------------------------------------------
# test_knob_off_byte_identical: central knob-kill via use-case strip
# ---------------------------------------------------------------------------


def _use_case(web_search_enabled: bool) -> Any:
    """A CompletionUseCase shell for unit-testing _strip_web_search_flag in isolation.

    __new__ bypasses the heavy DI __init__ (mirrors the adapter helpers above); the
    method under test reads only self._web_search_enabled.
    """
    from gateway.proxy.application.use_cases import CompletionUseCase

    uc = CompletionUseCase.__new__(CompletionUseCase)
    uc._web_search_enabled = web_search_enabled
    return uc


def test_knob_off_strips_web_search_before_dispatch() -> None:
    """Knob OFF: the REAL CompletionUseCase._strip_web_search_flag removes the flag.

    Calls production code (not an inline re-implementation) so the test fails if the
    strip is removed or its condition is inverted.
    """
    body: dict[str, Any] = {
        "model": "some-model",
        "messages": _BASE_MESSAGES,
        "web_search": True,
    }
    _use_case(web_search_enabled=False)._strip_web_search_flag(body)
    assert "web_search" not in body, "knob off -> flag stripped centrally"


def test_knob_on_keeps_web_search_for_adapters() -> None:
    """Knob ON: the flag SURVIVES the central strip so adapters can inject the tool.

    Guards against an inverted condition — if _strip_web_search_flag stripped while
    enabled, web search would be silently dead even with the knob on.
    """
    body: dict[str, Any] = {
        "model": "some-model",
        "messages": _BASE_MESSAGES,
        "web_search": True,
    }
    _use_case(web_search_enabled=True)._strip_web_search_flag(body)
    assert body.get("web_search") is True, "knob on -> flag kept for adapter injection"


async def test_no_web_search_key_no_injection_openrouter() -> None:
    """OpenRouter: without web_search flag -> no extra tool injected, body byte-identical."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_OPENAI_200)

    adapter = _openrouter_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await adapter.complete(
            {"model": "openai/gpt-4o", "messages": _BASE_MESSAGES}
        )
    finally:
        reset_provider_credential(token)

    body = captured["body"]
    assert "web_search" not in body
    assert "tools" not in body, "no tools key must be injected when web_search absent"


# ---------------------------------------------------------------------------
# test_nongrounding_provider_noop: bedrock-like adapter — nothing injected, no error
# ---------------------------------------------------------------------------


def test_nongrounding_provider_noop() -> None:
    """native_web_search_tool returns None for bedrock/azure -> no tool injected."""
    for provider in ("bedrock", "azure"):
        tool = native_web_search_tool(provider)
        assert tool is None, f"Expected None for {provider}"

    # Simulate what an adapter would do: if tool is None, just pop the flag
    payload: dict[str, Any] = {
        "model": "anthropic.claude-v2",
        "messages": _BASE_MESSAGES,
        "web_search": True,
    }
    tool = native_web_search_tool("bedrock")
    outbound = dict(payload)
    outbound.pop("web_search", None)
    if tool is not None:
        outbound["tools"] = [*outbound.get("tools", []), tool]

    assert "web_search" not in outbound
    assert "tools" not in outbound, "no tool injection for bedrock"


# ---------------------------------------------------------------------------
# test_flag_never_reaches_upstream: SECURITY pin — no "web_search" key in upstream body
# ---------------------------------------------------------------------------


async def test_flag_never_reaches_upstream_openrouter_with_flag() -> None:
    """SECURITY: OpenRouter upstream body must never contain 'web_search' key (flag=True)."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_OPENAI_200)

    adapter = _openrouter_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await adapter.complete(
            {"model": "openai/gpt-4o", "messages": _BASE_MESSAGES, "web_search": True}
        )
    finally:
        reset_provider_credential(token)
    assert "web_search" not in captured["body"]


async def test_flag_never_reaches_upstream_openrouter_without_flag() -> None:
    """SECURITY: OpenRouter upstream body must never contain 'web_search' key (flag absent)."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_OPENAI_200)

    adapter = _openrouter_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await adapter.complete(
            {"model": "openai/gpt-4o", "messages": _BASE_MESSAGES}
        )
    finally:
        reset_provider_credential(token)
    assert "web_search" not in captured["body"]


async def test_flag_never_reaches_upstream_openai_with_flag() -> None:
    """SECURITY: OpenAI upstream body must never contain 'web_search' key (flag=True)."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_OPENAI_200)

    adapter = _openai_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await adapter.complete(
            {"model": "gpt-4o", "messages": _BASE_MESSAGES, "web_search": True}
        )
    finally:
        reset_provider_credential(token)
    assert "web_search" not in captured["body"]


def test_flag_never_reaches_upstream_anthropic_with_flag() -> None:
    """SECURITY: Anthropic translated body must never contain 'web_search' key."""
    result = _openai_to_anthropic_request(
        {"model": "claude-3-5-sonnet-20241022", "messages": _BASE_MESSAGES, "web_search": True},
        default_max_tokens=4096,
    )
    assert "web_search" not in result


def test_flag_never_reaches_upstream_gemini_with_flag() -> None:
    """SECURITY: Gemini translated body must never contain 'web_search' key."""
    result = _openai_to_gemini_request(
        {"model": "gemini-1.5-flash", "messages": _BASE_MESSAGES, "web_search": True},
        default_max_tokens=4096,
    )
    assert "web_search" not in result


# ---------------------------------------------------------------------------
# test_anthropic_citations_survive: web_search_result block -> response["grounding"]
# ---------------------------------------------------------------------------


def test_anthropic_citations_survive() -> None:
    """An Anthropic response with web_search_result blocks -> grounding field in OpenAI response."""
    body_with_citations = {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [
            {"type": "text", "text": "The weather is sunny."},
            {
                "type": "web_search_result",
                "url": "https://example.com/weather",
                "title": "Weather Today",
                "encrypted_content": "...",
                "page_age": "2024-01-01",
            },
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    result = _anthropic_to_openai(body_with_citations)
    assert "grounding" in result, "grounding field must be present when web_search_result exists"
    grounding = result["grounding"]
    assert isinstance(grounding, list)
    assert len(grounding) >= 1
    item = grounding[0]
    assert "url" in item or "title" in item or "snippet" in item


def test_anthropic_citations_survive_multiple() -> None:
    """Multiple web_search_result blocks -> grounding list with all sources."""
    body = {
        "id": "msg_02",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [
            {"type": "text", "text": "The weather."},
            {
                "type": "web_search_result",
                "url": "https://source1.com",
                "title": "Source 1",
            },
            {
                "type": "web_search_result",
                "url": "https://source2.com",
                "title": "Source 2",
            },
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    result = _anthropic_to_openai(body)
    assert "grounding" in result
    assert len(result["grounding"]) == 2


# ---------------------------------------------------------------------------
# test_gemini_grounding_survives: groundingMetadata -> response["grounding"]
# ---------------------------------------------------------------------------


def test_gemini_grounding_survives() -> None:
    """A Gemini response with groundingMetadata -> grounding field in OpenAI response."""
    body_with_grounding = {
        "candidates": [
            {
                "content": {"parts": [{"text": "Sunny."}], "role": "model"},
                "finishReason": "STOP",
                "groundingMetadata": {
                    "groundingChunks": [
                        {
                            "web": {
                                "uri": "https://weather.example.com",
                                "title": "Weather Forecast",
                            }
                        }
                    ],
                    "groundingSupports": [
                        {
                            "segment": {"text": "Sunny."},
                            "groundingChunkIndices": [0],
                        }
                    ],
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 3,
            "totalTokenCount": 13,
        },
    }
    result = _gemini_to_openai(body_with_grounding, model="gemini-1.5-flash")
    assert "grounding" in result, "grounding field must be present when groundingMetadata exists"
    grounding = result["grounding"]
    assert isinstance(grounding, list)
    assert len(grounding) >= 1
    item = grounding[0]
    assert "url" in item or "title" in item


def test_gemini_grounding_survives_multiple_chunks() -> None:
    """Multiple groundingChunks -> grounding list with all items."""
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": "Answer."}], "role": "model"},
                "finishReason": "STOP",
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"uri": "https://s1.com", "title": "S1"}},
                        {"web": {"uri": "https://s2.com", "title": "S2"}},
                    ],
                },
            }
        ],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2, "totalTokenCount": 7},
    }
    result = _gemini_to_openai(body, model="gemini-1.5-flash")
    assert "grounding" in result
    assert len(result["grounding"]) == 2


# ---------------------------------------------------------------------------
# test_no_grounding_no_field: no grounding -> "grounding" key absent (no fabrication)
# ---------------------------------------------------------------------------


def test_no_grounding_no_field_anthropic() -> None:
    """Anthropic response with NO web_search_result blocks -> no 'grounding' key in response."""
    result = _anthropic_to_openai(_ANTHROPIC_200)
    assert "grounding" not in result, "must not fabricate grounding when none present"


def test_no_grounding_no_field_gemini() -> None:
    """Gemini response with NO groundingMetadata -> no 'grounding' key in response."""
    result = _gemini_to_openai(_GEMINI_200, model="gemini-1.5-flash")
    assert "grounding" not in result, "must not fabricate grounding when none present"


def test_gemini_grounding_chunks_null_no_crash() -> None:
    """Robustness: groundingChunks present but NULL must not crash (key-present null).

    `.get("groundingChunks", [])` returns None here (the key exists), so iterating it
    would TypeError and 500 the non-stream translation. The normalizer must collapse
    null -> [] -> None (no grounding), never raise.
    """
    assert _normalize_gemini_grounding({"groundingChunks": None}) is None
    # also via the full translator path
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": "x"}], "role": "model"},
                "finishReason": "STOP",
                "index": 0,
                "groundingMetadata": {"groundingChunks": None},
            }
        ],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
    }
    result = _gemini_to_openai(body, model="gemini-1.5-flash")
    assert "grounding" not in result, "null chunks -> no grounding, no crash"


def test_gemini_grounding_non_dict_chunk_skipped() -> None:
    """Robustness: a non-dict chunk in groundingChunks is skipped, not crashed on."""
    assert _normalize_gemini_grounding({"groundingChunks": ["oops", 123, None]}) is None


# ---------------------------------------------------------------------------
# Additional SECURITY: stream path also never leaks the flag
# ---------------------------------------------------------------------------


async def test_flag_never_reaches_upstream_openrouter_stream() -> None:
    """SECURITY: OpenRouter stream path must also strip 'web_search' from the upstream body."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        # Return a minimal valid SSE stream
        sse_content = b"data: [DONE]\n\n"
        return httpx.Response(
            200, content=sse_content, headers={"content-type": "text/event-stream"}
        )

    adapter = _openrouter_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await _drain(
            adapter.stream(
                {"model": "openai/gpt-4o", "messages": _BASE_MESSAGES, "web_search": True}
            )
        )
    finally:
        reset_provider_credential(token)
    assert "web_search" not in captured["body"]


async def test_flag_never_reaches_upstream_openai_stream() -> None:
    """SECURITY: OpenAI direct stream path must also strip 'web_search' from the upstream body."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=b"data: [DONE]\n\n", headers={"content-type": "text/event-stream"}
        )

    adapter = _openai_adapter(handler)
    token = set_provider_credential(_TEST_CRED)
    try:
        await _drain(
            adapter.stream({"model": "gpt-4o", "messages": _BASE_MESSAGES, "web_search": True})
        )
    finally:
        reset_provider_credential(token)
    assert "web_search" not in captured["body"]
    # and the native tool WAS injected on the stream path
    assert {"type": "web_search_preview"} in captured["body"].get("tools", [])
