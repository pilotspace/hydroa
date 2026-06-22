"""Red suite for bedrock-incremental-stream (v30 t4) — TASK.md §4.

Bedrock ConverseStream must decode + translate INCREMENTALLY: each OpenAI SSE frame
yielded as its source binary EventStream frame completes on the wire, instead of
buffering the whole response (`b"".join(aiter_bytes())`) before decoding. The
complete-stream output stays byte-identical.

Two new surfaces under test:
  - aiter_event_stream(AsyncIterator[bytes]) — frame-boundary-aware incremental decoder
    (handles a frame split across chunks AND multiple frames per chunk).
  - _BedrockSSEStepper — step/finish translator driven live by the adapter.
"""

from __future__ import annotations

import json
import struct
from binascii import crc32
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BedrockCredential
from gateway.proxy.infrastructure import bedrock_upstream
from gateway.proxy.infrastructure.bedrock_eventstream import (
    EventStreamError,
    decode_event_stream,
)

# RED until BUILD adds these symbols.
from gateway.proxy.infrastructure.bedrock_eventstream import aiter_event_stream  # noqa: E402
from gateway.proxy.infrastructure.bedrock_upstream import (  # noqa: E402
    BedrockCompletionUpstream,
    _converse_stream_to_openai_sse,
    _openai_to_converse_request,
)

pytestmark = pytest.mark.asyncio

_FIXED_TIME = 1_700_000_000
_MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"
_CRED = BedrockCredential(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="fakesecretkey0000000000000000000000000000",
    region="us-east-1",
)


# ---------------------------------------------------------------------------
# AWS EventStream binary frame builder (byte-for-byte compatible with botocore)
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


_MSG_START = _es_message("messageStart", {"role": "assistant"})
_DELTA_HELLO = _es_message(
    "contentBlockDelta", {"delta": {"text": "Hello"}, "contentBlockIndex": 0}
)
_DELTA_WORLD = _es_message(
    "contentBlockDelta", {"delta": {"text": " world"}, "contentBlockIndex": 0}
)
_BLOCK_STOP = _es_message("contentBlockStop", {"contentBlockIndex": 0})
_MSG_STOP = _es_message("messageStop", {"stopReason": "end_turn"})
_METADATA = _es_message(
    "metadata", {"usage": {"inputTokens": 11, "outputTokens": 5, "totalTokens": 16}}
)
_FRAMES = [_MSG_START, _DELTA_HELLO, _DELTA_WORLD, _BLOCK_STOP, _MSG_STOP, _METADATA]
_FULL_STREAM = b"".join(_FRAMES)


async def _aiter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


def _counting_handler(
    chunks: list[bytes], counter: list[int]
) -> Callable[[httpx.Request], httpx.Response]:
    async def body() -> AsyncIterator[bytes]:
        for chunk in chunks:
            counter[0] += 1
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body(), headers={"content-type": "application/vnd.amazon.eventstream"}
        )

    return handler


def _make_adapter(handler: object) -> BedrockCompletionUpstream:
    adapter = BedrockCompletionUpstream(
        endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        default_max_tokens=4096,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


_PAYLOAD = {"model": _MODEL_ID, "messages": [{"role": "user", "content": "hi"}]}


# ---------------------------------------------------------------------------
# aiter_event_stream — frame-boundary-aware incremental decoder
# ---------------------------------------------------------------------------


async def test_aiter_frame_split_across_chunks() -> None:
    # Split the single messageStart frame across two byte chunks.
    mid = len(_MSG_START) // 2
    results = [ev async for ev in aiter_event_stream(_aiter([_MSG_START[:mid], _MSG_START[mid:]]))]
    assert len(results) == 1
    headers, payload = results[0]
    assert headers.get(":event-type") == "messageStart"
    assert json.loads(payload) == {"role": "assistant"}


async def test_aiter_multiple_frames_one_chunk() -> None:
    results = [ev async for ev in aiter_event_stream(_aiter([_MSG_START + _DELTA_HELLO]))]
    assert [h.get(":event-type") for h, _ in results] == ["messageStart", "contentBlockDelta"]


@pytest.mark.parametrize(
    "chunking",
    [
        [_FULL_STREAM],  # one big chunk
        _FRAMES,  # one frame per chunk
        [_FULL_STREAM[i : i + 7] for i in range(0, len(_FULL_STREAM), 7)],  # tiny 7-byte chunks
    ],
)
async def test_aiter_matches_buffered_decode(chunking: list[bytes]) -> None:
    incremental = [ev async for ev in aiter_event_stream(_aiter(chunking))]
    buffered = list(decode_event_stream(_FULL_STREAM))
    assert incremental == buffered


async def test_aiter_crc_mismatch_raises() -> None:
    corrupt = bytearray(_MSG_START)
    corrupt[-1] ^= 0xFF  # break the message CRC, leave the prelude valid
    with pytest.raises(EventStreamError):
        [ev async for ev in aiter_event_stream(_aiter([bytes(corrupt)]))]


async def test_aiter_truncated_tail_raises() -> None:
    # Stream ends mid-frame → truncation.
    with pytest.raises(EventStreamError):
        [ev async for ev in aiter_event_stream(_aiter([_MSG_START[:-5]]))]


# ---------------------------------------------------------------------------
# Adapter-level incremental delivery + byte-identity
# ---------------------------------------------------------------------------


async def test_bedrock_incremental_first_frame_before_full_drain() -> None:
    counter = [0]
    adapter = _make_adapter(_counting_handler(_FRAMES, counter))
    token = set_provider_credential(_CRED)
    try:
        gen = adapter.stream(_PAYLOAD)
        first = await gen.__anext__()
        # Incremental: role frame produced after pulling only the first frame's bytes.
        assert counter[0] < len(_FRAMES)
        assert b"assistant" in first
        rest = [chunk async for chunk in gen]
    finally:
        reset_provider_credential(token)
    all_chunks = [first, *rest]
    assert all_chunks[-1] == b"data: [DONE]\n\n"


async def test_bedrock_stream_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bedrock_upstream.time, "time", lambda: _FIXED_TIME)
    counter = [0]
    adapter = _make_adapter(_counting_handler(_FRAMES, counter))
    token = set_provider_credential(_CRED)
    try:
        drained = [chunk async for chunk in adapter.stream(_PAYLOAD)]
    finally:
        reset_provider_credential(token)
    model_id, _ = _openai_to_converse_request(_PAYLOAD, default_max_tokens=4096)
    events = [
        (h.get(":event-type", ""), json.loads(p)) for h, p in decode_event_stream(_FULL_STREAM)
    ]
    expected = list(_converse_stream_to_openai_sse(events, model_id=model_id))
    assert b"".join(drained) == b"".join(expected)


async def test_bedrock_5xx_yields_no_frame() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    adapter = _make_adapter(handler)
    token = set_provider_credential(_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            [chunk async for chunk in adapter.stream(_PAYLOAD)]
    finally:
        reset_provider_credential(token)


async def test_bedrock_midstream_network_error_after_partial() -> None:
    async def body() -> AsyncIterator[bytes]:
        yield _MSG_START  # role frame deliverable
        raise httpx.NetworkError("dropped mid-stream")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body(), headers={"content-type": "application/vnd.amazon.eventstream"}
        )

    adapter = _make_adapter(handler)
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


def test_bedrock_stepper_finish_idempotent() -> None:
    """A second finish() call yields nothing — no duplicate terminal frame / [DONE]."""
    from gateway.proxy.infrastructure.bedrock_upstream import _BedrockSSEStepper

    stepper = _BedrockSSEStepper(model_id=_MODEL_ID)
    first = list(stepper.finish())
    second = list(stepper.finish())
    assert first and first[-1] == b"data: [DONE]\n\n"
    assert second == []
