"""RED suite — stream-graceful-close-mapping (v35 task 2/3) — TASK.md §4.

Each provider adapter's streaming _gen() currently maps:
  except (httpx.TimeoutException, httpx.NetworkError[, httpx.ConnectError]) as exc:
      self._breaker.on_upstream_error()
      raise UpstreamUnavailableError(str(exc)) from None

`httpx.RemoteProtocolError` is a ProtocolError, NOT a NetworkError → it escapes
unmapped → the raw httpx error surfaces to the caller instead of UpstreamUnavailableError.

RED REASON: 5 tests fail because `httpx.RemoteProtocolError` is NOT caught by the
existing tuples; it propagates as a raw httpx.RemoteProtocolError through to pytest.
BUILD will add `httpx.RemoteProtocolError` to each tuple → all 5 turn GREEN.

1 regression guard (ReadError) is already GREEN — it was already a NetworkError
and the existing mapping already handles it.

Mechanism (§4): `_RemoteProtocolStream` yields one valid SSE chunk then raises
`httpx.RemoteProtocolError("peer closed connection without sending complete message
body", request=...)`. Wired via httpx.MockTransport returning a 200 SSE response
with `stream=<that stream>`. Each adapter is constructed the same way its existing
test suite does — the construction + MockTransport-swap pattern is copied verbatim
from those suites (no new harness invented).

Contract: TASK.md §3 (FROZEN @ v1) — approved by Tin Dang 2026-06-24.
"""

from __future__ import annotations

import json
import struct
from binascii import crc32
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import (
    AzureCredential,
    BedrockCredential,
    BearerCredential,
)
from gateway.proxy.infrastructure.anthropic_upstream import AnthropicCompletionUpstream
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream
from gateway.proxy.infrastructure.bedrock_upstream import BedrockCompletionUpstream
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.gemini_upstream import GeminiCompletionUpstream
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Shared test credentials — one per provider type, mirrors existing suites
# ---------------------------------------------------------------------------

_BEARER_CRED = BearerCredential(secret="sk-test-graceful-close")

_AZ_CRED = AzureCredential(
    mode="api_key",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"gpt-4o": "prod-4o"},
    api_key="secret-az-key",
)

_BEDROCK_CRED = BedrockCredential(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="fakesecretkey0000000000000000000000000000",
    region="us-east-1",
)


# ---------------------------------------------------------------------------
# Shared SSE byte stream helper
# ---------------------------------------------------------------------------

_ONE_VALID_SSE_CHUNK = (
    b'data: {"choices":[{"delta":{"role":"assistant","content":"hi"}}]}\n\n'
)

_ONE_VALID_ANTHROPIC_SSE_CHUNK = (
    b"event: message_start\n"
    b'data: {"type":"message_start","message":{"id":"msg_01","model":"claude-3-5-sonnet-20241022",'
    b'"usage":{"input_tokens":5,"output_tokens":0}}}\n\n'
)

_ONE_VALID_GEMINI_SSE_CHUNK = (
    b'data: {"candidates":[{"content":{"parts":[{"text":"hi"}],"role":"model"}}]}\n\n'
)


# ---------------------------------------------------------------------------
# _RemoteProtocolStream — yields one valid SSE chunk then raises
# httpx.RemoteProtocolError (the graceful mid-stream peer close)
# ---------------------------------------------------------------------------


class _RemoteProtocolStream(httpx.AsyncByteStream):
    """AsyncByteStream that yields `first_chunk` then raises RemoteProtocolError.

    This faithfully models a graceful upstream peer close mid-stream:
    the server sends 200 + one SSE event then closes the connection (FIN),
    which httpx surfaces as RemoteProtocolError (ProtocolError, NOT NetworkError).
    """

    def __init__(self, first_chunk: bytes) -> None:
        self._first_chunk = first_chunk

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._first_chunk
        # Simulate graceful mid-stream FIN-close: httpx raises RemoteProtocolError.
        # We use a dummy request object — httpx.RemoteProtocolError requires one.
        dummy_request = httpx.Request("POST", "https://example.com/")
        raise httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body",
            request=dummy_request,
        )


# ---------------------------------------------------------------------------
# Drain helper — mirrors the pattern in each existing adapter test suite
# ---------------------------------------------------------------------------


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


# ---------------------------------------------------------------------------
# AWS EventStream binary frame builder for Bedrock
# (mirrors bedrock_streaming/test_bedrock_streaming.py exactly)
# ---------------------------------------------------------------------------


def _hdr(name: str, value: str) -> bytes:
    n = name.encode()
    v = value.encode()
    return bytes([len(n)]) + n + bytes([7]) + struct.pack(">H", len(v)) + v


def _es_message(event_type: str, payload: dict[str, Any]) -> bytes:
    headers = (
        _hdr(":event-type", event_type)
        + _hdr(":content-type", "application/json")
        + _hdr(":message-type", "event")
    )
    body = json.dumps(payload).encode()
    headers_len = len(headers)
    total_len = 12 + headers_len + len(body) + 4
    prelude = struct.pack(">II", total_len, headers_len)
    prelude_full = prelude + struct.pack(">I", crc32(prelude) & 0xFFFFFFFF)
    msg_wo_crc = prelude_full + headers + body
    return msg_wo_crc + struct.pack(">I", crc32(msg_wo_crc) & 0xFFFFFFFF)


_BEDROCK_ONE_CHUNK = _es_message("messageStart", {"role": "assistant"})


class _BedrockRemoteProtocolStream(httpx.AsyncByteStream):
    """AsyncByteStream that yields one valid Bedrock EventStream frame then raises
    httpx.RemoteProtocolError, simulating a graceful peer-close mid-stream.
    """

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _BEDROCK_ONE_CHUNK
        dummy_request = httpx.Request("POST", "https://bedrock-runtime.us-east-1.amazonaws.com/")
        raise httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body",
            request=dummy_request,
        )


# ---------------------------------------------------------------------------
# Adapter factories — each mirrors its existing test suite exactly
# ---------------------------------------------------------------------------


def _make_openrouter(handler: Any) -> OpenRouterCompletionUpstream:
    """Mirror retry_policy/conftest.py make_upstream() — __new__ + manual attrs."""
    upstream = OpenRouterCompletionUpstream.__new__(OpenRouterCompletionUpstream)
    upstream._breaker = CircuitBreaker()
    upstream._client = httpx.AsyncClient(
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
    )
    upstream._max_retries = 0
    upstream._backoff_base = 0.5
    upstream._retry_deadline_s = 0.0
    upstream._metrics_registry = None
    upstream._usage_accounting = False
    return upstream


def _make_anthropic(handler: Any) -> AnthropicCompletionUpstream:
    """Mirror anthropic_provider/test_anthropic_provider.py _make_adapter_with_handler()."""
    adapter = AnthropicCompletionUpstream(
        base_url="https://api.anthropic.com/v1",
        anthropic_version="2023-06-01",
        default_max_tokens=4096,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url="https://api.anthropic.com/v1",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


def _make_azure(handler: Any) -> AzureCompletionUpstream:
    """Mirror azure_streaming/test_azure_streaming.py _make_adapter()."""
    adapter = AzureCompletionUpstream(  # type: ignore[call-arg]
        token_provider_cache=None,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


def _make_gemini(handler: Any) -> GeminiCompletionUpstream:
    """Mirror gemini_provider/test_gemini_provider.py _chat_adapter()."""
    _BASE = "https://generativelanguage.googleapis.com/v1beta"
    adapter = GeminiCompletionUpstream(base_url=_BASE, default_max_tokens=4096)
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url=_BASE,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


def _make_bedrock(handler: Any) -> BedrockCompletionUpstream:
    """Mirror bedrock_streaming/test_bedrock_streaming.py _make_adapter()."""
    adapter = BedrockCompletionUpstream(  # type: ignore[call-arg]
        endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        default_max_tokens=4096,
        max_retries=0,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


# ---------------------------------------------------------------------------
# GC-1: OpenRouter graceful mid-stream close → UpstreamUnavailableError
#
# RED REASON: httpx.RemoteProtocolError is a ProtocolError, NOT a NetworkError.
# The current except tuple is (TimeoutException, NetworkError) — RemoteProtocolError
# escapes unmapped. After BUILD adds it to the tuple, this turns GREEN.
# ---------------------------------------------------------------------------


async def test_openrouter_graceful_close_maps_to_unavailable() -> None:
    """GC-1: OpenRouter stream yields 1 chunk then FIN-closes → UpstreamUnavailableError.

    RED: raw httpx.RemoteProtocolError escapes the _gen() except clause.
    """
    stream = _RemoteProtocolStream(_ONE_VALID_SSE_CHUNK)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream"},
        )

    adapter = _make_openrouter(handler)
    tok = set_provider_credential(_BEARER_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await _drain(adapter.stream({"model": "openai/gpt-4o", "messages": []}))
    finally:
        reset_provider_credential(tok)


# ---------------------------------------------------------------------------
# GC-2: Anthropic graceful mid-stream close → UpstreamUnavailableError
#
# RED REASON: same as GC-1 — RemoteProtocolError not in the except tuple.
# ---------------------------------------------------------------------------


async def test_anthropic_graceful_close_maps_to_unavailable() -> None:
    """GC-2: Anthropic stream yields 1 SSE chunk then FIN-closes → UpstreamUnavailableError.

    RED: raw httpx.RemoteProtocolError escapes the _gen() except clause.
    """
    stream = _RemoteProtocolStream(_ONE_VALID_ANTHROPIC_SSE_CHUNK)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream"},
        )

    adapter = _make_anthropic(handler)
    tok = set_provider_credential(_BEARER_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await _drain(
                adapter.stream(
                    {
                        "model": "claude-3-5-sonnet-20241022",
                        "messages": [{"role": "user", "content": "hi"}],
                    }
                )
            )
    finally:
        reset_provider_credential(tok)


# ---------------------------------------------------------------------------
# GC-3: Azure graceful mid-stream close → UpstreamUnavailableError
#
# RED REASON: same as GC-1 — RemoteProtocolError not in the except tuple.
# ---------------------------------------------------------------------------


async def test_azure_graceful_close_maps_to_unavailable() -> None:
    """GC-3: Azure stream yields 1 SSE chunk then FIN-closes → UpstreamUnavailableError.

    RED: raw httpx.RemoteProtocolError escapes the _gen() except clause.
    """
    stream = _RemoteProtocolStream(_ONE_VALID_SSE_CHUNK)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream"},
        )

    adapter = _make_azure(handler)
    tok = set_provider_credential(_AZ_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await _drain(
                adapter.stream(
                    {
                        "model": "gpt-4o",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": True,
                    }
                )
            )
    finally:
        reset_provider_credential(tok)


# ---------------------------------------------------------------------------
# GC-4: Gemini graceful mid-stream close → UpstreamUnavailableError
#
# RED REASON: same as GC-1 — RemoteProtocolError not in the except tuple.
# ---------------------------------------------------------------------------


async def test_gemini_graceful_close_maps_to_unavailable() -> None:
    """GC-4: Gemini stream yields 1 SSE chunk then FIN-closes → UpstreamUnavailableError.

    RED: raw httpx.RemoteProtocolError escapes the _gen() except clause.
    """
    stream = _RemoteProtocolStream(_ONE_VALID_GEMINI_SSE_CHUNK)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream"},
        )

    adapter = _make_gemini(handler)
    tok = set_provider_credential(_BEARER_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await _drain(
                adapter.stream(
                    {
                        "model": "gemini-1.5-flash",
                        "messages": [{"role": "user", "content": "hi"}],
                    }
                )
            )
    finally:
        reset_provider_credential(tok)


# ---------------------------------------------------------------------------
# GC-5: Bedrock graceful mid-stream close → UpstreamUnavailableError
#
# RED REASON: same as GC-1 — RemoteProtocolError not in the except tuple (bedrock
# currently has: except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)).
# ---------------------------------------------------------------------------


async def test_bedrock_graceful_close_maps_to_unavailable() -> None:
    """GC-5: Bedrock stream yields 1 EventStream frame then FIN-closes → UpstreamUnavailableError.

    RED: raw httpx.RemoteProtocolError escapes the _gen() except clause.
    """
    stream = _BedrockRemoteProtocolStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "application/vnd.amazon.eventstream"},
        )

    adapter = _make_bedrock(handler)
    tok = set_provider_credential(_BEDROCK_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await _drain(
                adapter.stream(
                    {
                        "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
                        "messages": [{"role": "user", "content": "hi"}],
                    }
                )
            )
    finally:
        reset_provider_credential(tok)


# ---------------------------------------------------------------------------
# GC-6: Regression guard — ReadError (NetworkError) still → UpstreamUnavailableError
#
# GREEN by design — httpx.ReadError IS a NetworkError, already in the existing
# except tuple for all adapters. Proves the additive change (BUILD) did NOT
# accidentally drop the existing mapping.
# ---------------------------------------------------------------------------


class _ReadErrorStream(httpx.AsyncByteStream):
    """AsyncByteStream that yields one chunk then raises httpx.ReadError."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _ONE_VALID_SSE_CHUNK
        dummy_request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        raise httpx.ReadError("connection reset by peer", request=dummy_request)


async def test_openrouter_readerror_still_maps_to_unavailable() -> None:
    """GC-6 (regression guard): httpx.ReadError mid-stream → UpstreamUnavailableError.

    This test is GREEN before and after BUILD — it verifies the existing mapping
    (ReadError is a NetworkError, already caught) is preserved after the additive
    RemoteProtocolError addition.
    """
    stream = _ReadErrorStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream"},
        )

    adapter = _make_openrouter(handler)
    tok = set_provider_credential(_BEARER_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await _drain(adapter.stream({"model": "openai/gpt-4o", "messages": []}))
    finally:
        reset_provider_credential(tok)
