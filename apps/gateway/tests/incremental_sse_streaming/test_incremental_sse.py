"""Red suite for incremental-sse-translation (v30 t3) — TASK.md §4.

Anthropic + Gemini chat streaming must deliver each translated OpenAI SSE frame
the instant its source upstream event arrives (incremental), while keeping the
complete-stream output byte-identical to the buffered status quo.

The incremental tests drive the adapter through an httpx MockTransport whose
response body is an async generator that COUNTS how many upstream event-chunks
have been pulled. This distinguishes:
  - incremental delivery  → first frame produced after pulling only 1 chunk
  - buffered status quo    → first frame produced only after ALL chunks drained

The byte-identical tests pin time.time() so the per-call `created` field is
constant, then assert the adapter-drained bytes equal the pure translator output.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure import anthropic_upstream, gemini_upstream
from gateway.proxy.infrastructure.anthropic_upstream import (
    AnthropicCompletionUpstream,
    _translate_anthropic_sse,
)
from gateway.proxy.infrastructure.gemini_upstream import (
    GeminiCompletionUpstream,
    _translate_gemini_sse,
)
from gateway.usage.domain.extractor import extract_usage_from_sse

pytestmark = pytest.mark.asyncio

_CRED = BearerCredential(secret="sk-test")
_FIXED_TIME = 1_700_000_000


# ---------------------------------------------------------------------------
# Per-event upstream byte chunks (each a complete SSE frame ending in \n\n,
# so httpx aiter_lines never needs the next chunk to complete a line).
# ---------------------------------------------------------------------------

_ANTHROPIC_CHUNKS: list[bytes] = [
    b"event: message_start\n"
    b'data: {"type":"message_start","message":{"id":"msg_1","model":"c",'
    b'"usage":{"input_tokens":10,"output_tokens":1}}}\n\n',
    b"event: content_block_delta\n"
    b'data: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"Hello"}}\n\n',
    b"event: content_block_delta\n"
    b'data: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":" world"}}\n\n',
    b"event: message_delta\n"
    b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":5}}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
]

_ANTHROPIC_EVENTS = [
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "model": "c",
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": " world"},
        },
    ),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 5},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
]

_GEMINI_CHUNKS: list[bytes] = [
    b'data: {"candidates":[{"content":{"parts":[{"text":"Hello"}]}}]}\n\n',
    b'data: {"candidates":[{"content":{"parts":[{"text":" world"}]},'
    b'"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":10,'
    b'"candidatesTokenCount":5,"totalTokenCount":15}}\n\n',
]

_GEMINI_EVENTS = [
    {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]},
    {
        "candidates": [{"content": {"parts": [{"text": " world"}]}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "totalTokenCount": 15,
        },
    },
]


def _counting_handler(
    chunks: list[bytes], counter: list[int]
) -> Callable[[httpx.Request], httpx.Response]:
    """Return a MockTransport handler whose body yields `chunks` lazily, counting pulls."""

    async def body() -> AsyncIterator[bytes]:
        for chunk in chunks:
            counter[0] += 1
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})

    return handler


def _anthropic_adapter(handler: object) -> AnthropicCompletionUpstream:
    adapter = AnthropicCompletionUpstream(base_url="https://api.anthropic.com/v1")
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url="https://api.anthropic.com/v1",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


def _gemini_adapter(handler: object) -> GeminiCompletionUpstream:
    adapter = GeminiCompletionUpstream()
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url="https://generativelanguage.googleapis.com/v1beta",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


_PAYLOAD = {"model": "c", "messages": [{"role": "user", "content": "hi"}]}


# ---------------------------------------------------------------------------
# Incremental delivery (RED on the buffered status quo)
# ---------------------------------------------------------------------------


async def test_anthropic_incremental_first_frame_before_full_drain() -> None:
    counter = [0]
    adapter = _anthropic_adapter(_counting_handler(_ANTHROPIC_CHUNKS, counter))
    token = set_provider_credential(_CRED)
    try:
        gen = adapter.stream(_PAYLOAD)
        first = await gen.__anext__()
        # Incremental: the role frame is produced after pulling only the first
        # upstream event — NOT after draining the whole stream.
        assert counter[0] < len(_ANTHROPIC_CHUNKS)
        assert b'"role"' in first and b"assistant" in first
        rest = [chunk async for chunk in gen]
    finally:
        reset_provider_credential(token)

    all_chunks = [first, *rest]
    assert all_chunks[-1] == b"data: [DONE]\n\n"
    assert extract_usage_from_sse(all_chunks) == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


async def test_gemini_incremental_first_frame_before_full_drain() -> None:
    counter = [0]
    adapter = _gemini_adapter(_counting_handler(_GEMINI_CHUNKS, counter))
    token = set_provider_credential(_CRED)
    try:
        gen = adapter.stream(_PAYLOAD)
        first = await gen.__anext__()
        assert counter[0] < len(_GEMINI_CHUNKS)
        assert b'"role"' in first and b"assistant" in first
        rest = [chunk async for chunk in gen]
    finally:
        reset_provider_credential(token)

    all_chunks = [first, *rest]
    assert all_chunks[-1] == b"data: [DONE]\n\n"
    assert extract_usage_from_sse(all_chunks) == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


# ---------------------------------------------------------------------------
# Byte-identical complete-stream output (guard — green on either impl)
# ---------------------------------------------------------------------------


async def test_anthropic_stream_byte_identical_to_translator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(anthropic_upstream.time, "time", lambda: _FIXED_TIME)
    counter = [0]
    adapter = _anthropic_adapter(_counting_handler(_ANTHROPIC_CHUNKS, counter))
    token = set_provider_credential(_CRED)
    try:
        drained = [chunk async for chunk in adapter.stream(_PAYLOAD)]
    finally:
        reset_provider_credential(token)
    expected = list(_translate_anthropic_sse(_ANTHROPIC_EVENTS))
    assert b"".join(drained) == b"".join(expected)


async def test_gemini_stream_byte_identical_to_translator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gemini_upstream.time, "time", lambda: _FIXED_TIME)
    counter = [0]
    adapter = _gemini_adapter(_counting_handler(_GEMINI_CHUNKS, counter))
    token = set_provider_credential(_CRED)
    try:
        drained = [chunk async for chunk in adapter.stream(_PAYLOAD)]
    finally:
        reset_provider_credential(token)
    expected = list(_translate_gemini_sse(_GEMINI_EVENTS))
    assert b"".join(drained) == b"".join(expected)


# ---------------------------------------------------------------------------
# Failure paths unchanged — no frame emitted on 5xx (before first yield)
# ---------------------------------------------------------------------------


async def test_anthropic_5xx_yields_no_frame() -> None:
    from gateway.proxy.domain.errors import UpstreamUnavailableError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"type": "error", "error": {"message": "busy"}})

    adapter = _anthropic_adapter(handler)
    token = set_provider_credential(_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            [chunk async for chunk in adapter.stream(_PAYLOAD)]
    finally:
        reset_provider_credential(token)


# ---------------------------------------------------------------------------
# Mid-stream network error — incremental delivery means partial frames MAY have
# already been delivered before the error surfaces (consistent with the existing
# OpenRouter passthrough). The breaker still records the error and
# UpstreamUnavailableError is still raised. This pins that intended behavior.
# ---------------------------------------------------------------------------


def _midstream_drop_handler(
    first_chunk: bytes,
) -> Callable[[httpx.Request], httpx.Response]:
    async def body() -> AsyncIterator[bytes]:
        yield first_chunk  # a complete frame → at least the role frame is deliverable
        raise httpx.NetworkError("connection dropped mid-stream")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})

    return handler


async def test_anthropic_midstream_network_error_raises_after_partial() -> None:
    from gateway.proxy.domain.errors import UpstreamUnavailableError

    adapter = _anthropic_adapter(_midstream_drop_handler(_ANTHROPIC_CHUNKS[0]))
    token = set_provider_credential(_CRED)
    delivered: list[bytes] = []
    try:
        with pytest.raises(UpstreamUnavailableError):
            async for chunk in adapter.stream(_PAYLOAD):
                delivered.append(chunk)
    finally:
        reset_provider_credential(token)
    # The role frame was delivered incrementally before the drop surfaced.
    assert delivered and b"assistant" in delivered[0]
    # The breaker recorded the upstream failure.
    assert adapter._breaker._failure_count > 0  # type: ignore[attr-defined]


async def test_gemini_midstream_network_error_raises_after_partial() -> None:
    from gateway.proxy.domain.errors import UpstreamUnavailableError

    adapter = _gemini_adapter(_midstream_drop_handler(_GEMINI_CHUNKS[0]))
    token = set_provider_credential(_CRED)
    delivered: list[bytes] = []
    try:
        with pytest.raises(UpstreamUnavailableError):
            async for chunk in adapter.stream(_PAYLOAD):
                delivered.append(chunk)
    finally:
        reset_provider_credential(token)
    assert delivered and b"assistant" in delivered[0]
    assert adapter._breaker._failure_count > 0  # type: ignore[attr-defined]
