"""Red suite for azure-streaming-passthrough (v21 task 3).

Implements AzureCompletionUpstream.stream() as a byte-identical SSE passthrough
(mirrors OpenRouter.stream) + proves tools/response_format passthrough. HTTP behavior
via httpx.MockTransport (no network), mirroring tests/bedrock_streaming.

CONTRACT (FROZEN @ v1): azure-streaming-passthrough TASK.md §3
  - stream(payload) -> AsyncIterator[bytes], byte-passthrough; breaker pre-first-byte;
    5xx → UpstreamUnavailableError (0 chunks); billing via the application extractor.
  - tools/response_format forwarded verbatim (no translation).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.infrastructure.azure_config import AzureConfig
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream
from gateway.usage.domain.extractor import extract_usage_from_sse

_CFG = AzureConfig(
    api_key="secret-az-key",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"gpt-4o": "prod-4o"},
)

_SSE = (
    b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
    b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}\n\n'
    b"data: [DONE]\n\n"
)


def _make_adapter(handler: object) -> AzureCompletionUpstream:
    adapter = AzureCompletionUpstream(config=_CFG, backoff_base=0.0, retry_deadline_s=0.0)
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


_STREAM_PAYLOAD: dict[str, object] = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": True,
    "stream_options": {"include_usage": True},
}


async def test_stream_passthrough_url_and_api_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("api-key")
        return httpx.Response(200, content=_SSE)

    adapter = _make_adapter(handler)
    chunks = await _drain(adapter.stream(_STREAM_PAYLOAD))
    assert b"".join(chunks) == _SSE
    assert seen["url"] == (
        "https://r.openai.azure.com/openai/deployments/prod-4o/chat/completions"
        "?api-version=2024-10-21"
    )
    assert seen["api_key"] == "secret-az-key"


async def test_streamed_usage_is_billable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SSE)

    adapter = _make_adapter(handler)
    chunks = await _drain(adapter.stream(_STREAM_PAYLOAD))
    usage = extract_usage_from_sse(chunks)
    assert usage == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


async def test_stream_5xx_raises_before_any_chunk() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    adapter = _make_adapter(handler)
    chunks: list[bytes] = []
    with pytest.raises(UpstreamUnavailableError):
        async for chunk in adapter.stream(_STREAM_PAYLOAD):
            chunks.append(chunk)
    assert chunks == []


async def test_tools_and_response_format_forwarded() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "x", "usage": {}})

    payload: dict[str, object] = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "weather?"}],
        "tools": [{"type": "function", "function": {"name": "get_weather"}}],
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
    }
    adapter = _make_adapter(handler)
    await adapter.complete(payload)
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["tools"] == payload["tools"]
    assert body["tool_choice"] == "auto"
    assert body["response_format"] == {"type": "json_object"}


async def test_tool_calls_response_passthrough() -> None:
    upstream_body = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"city":"SF"}'},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=upstream_body)

    adapter = _make_adapter(handler)
    status, body = await adapter.complete({"model": "gpt-4o", "messages": []})
    assert status == 200
    assert (
        body["choices"][0]["message"]["tool_calls"]
        == upstream_body["choices"][0]["message"]["tool_calls"]
    )
