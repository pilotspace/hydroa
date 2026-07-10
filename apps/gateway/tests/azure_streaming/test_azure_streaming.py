"""Red suite for azure-streaming-passthrough (v21 task 3).

Implements AzureCompletionUpstream.stream() as a byte-identical SSE passthrough
(mirrors OpenRouter.stream) + proves tools/response_format passthrough. HTTP behavior
via httpx.MockTransport (no network), mirroring tests/bedrock_streaming.

CONTRACT (FROZEN @ v1): azure-streaming-passthrough TASK.md §3
  - stream(payload) -> AsyncIterator[bytes], byte-passthrough; breaker pre-first-byte;
    5xx → UpstreamUnavailableError (0 chunks); billing via the application extractor.
  - tools/response_format forwarded verbatim (no translation).

v25 task-3 amendment: _make_adapter drops config= ctor arg; credentials travel via
AzureCredential in the contextvar. Each stream()/complete() call is wrapped with
set_provider_credential / reset_provider_credential.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from gateway.core.egress_policy import AllowAllEgressPolicy
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import AzureCredential
from gateway.proxy.infrastructure.azure_config import AzureConfig
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream
from gateway.usage.domain.extractor import extract_usage_from_sse

_CFG = AzureConfig(
    api_key="secret-az-key",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"gpt-4o": "prod-4o"},
)

# v25 task-3: AzureCredential (api_key mode) travels via contextvar.
_AZ_CRED = AzureCredential(
    mode="api_key",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"gpt-4o": "prod-4o"},
    api_key="secret-az-key",
)

_SSE = (
    b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
    b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}\n\n'
    b"data: [DONE]\n\n"
)


def _make_adapter(handler: object) -> AzureCompletionUpstream:
    """Construct the adapter (no ctor config — task-3), swap _client.

    v25 task-3: AzureCompletionUpstream drops config= and token_provider=; gains
    token_provider_cache=. Credentials travel via AzureCredential in the contextvar.
    The caller must set/reset the contextvar around stream()/complete() calls.

    RIGHT-REASON RED: existing ctor still requires config= → TypeError until BUILD.
    """
    adapter = AzureCompletionUpstream(  # type: ignore[call-arg]
        token_provider_cache=None,
        backoff_base=0.0,
        retry_deadline_s=0.0,
        egress_policy=AllowAllEgressPolicy(),
    )
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
    tok = set_provider_credential(_AZ_CRED)
    try:
        chunks = await _drain(adapter.stream(_STREAM_PAYLOAD))
    finally:
        reset_provider_credential(tok)
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
    tok = set_provider_credential(_AZ_CRED)
    try:
        chunks = await _drain(adapter.stream(_STREAM_PAYLOAD))
    finally:
        reset_provider_credential(tok)
    usage = extract_usage_from_sse(chunks)
    assert usage == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


async def test_stream_5xx_raises_before_any_chunk() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    adapter = _make_adapter(handler)
    chunks: list[bytes] = []
    tok = set_provider_credential(_AZ_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            async for chunk in adapter.stream(_STREAM_PAYLOAD):
                chunks.append(chunk)
    finally:
        reset_provider_credential(tok)
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
    tok = set_provider_credential(_AZ_CRED)
    try:
        await adapter.complete(payload)
    finally:
        reset_provider_credential(tok)
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
    tok = set_provider_credential(_AZ_CRED)
    try:
        status, body = await adapter.complete({"model": "gpt-4o", "messages": []})
    finally:
        reset_provider_credential(tok)
    assert status == 200
    assert (
        body["choices"][0]["message"]["tool_calls"]
        == upstream_body["choices"][0]["message"]["tool_calls"]
    )
