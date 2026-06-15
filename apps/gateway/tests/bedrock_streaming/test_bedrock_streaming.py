"""Red suite for bedrock-streaming (v20 task 3/4) — frozen contract.

Tests the AWS Bedrock ConverseStream → OpenAI SSE adapter:
  - AWS event-stream binary decoder (bedrock_eventstream.py)
  - BedrockCompletionUpstream.stream() and _converse_stream_to_openai_sse helper
    (bedrock_upstream.py)

Contract: FROZEN @ v20 task 3.
  - decode_event_stream: decodes binary AWS EventStream frames; validates CRC;
    raises EventStreamError on CRC mismatch or truncation.
  - BedrockCompletionUpstream.stream(): ConverseStream events → OpenAI SSE chunks;
    terminal chunk carries finish_reason + usage; ends with b"data: [DONE]\\n\\n".
  - Signs every request with AWS SigV4 (Authorization: AWS4-HMAC-SHA256 …).
  - 5xx before first byte → UpstreamUnavailableError; zero chunks yielded.

Async tests are marked via the module-level pytestmark.
Pure decode tests (BS1–BS3) are sync plain `def`.
"""

from __future__ import annotations

import json
import struct
from binascii import crc32
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote, unquote

import httpx
import pytest

# ── Stable existing imports ─────────────────────────────────────────────────
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.ports import CompletionUpstream
from gateway.proxy.infrastructure.bedrock_sigv4 import AwsCredentials
from gateway.usage.domain.extractor import extract_usage_from_sse

# RED until BUILD creates these modules/symbols.
from gateway.proxy.infrastructure.bedrock_eventstream import (  # noqa: E402
    EventStreamError,
    decode_event_stream,
)
from gateway.proxy.infrastructure.bedrock_upstream import (  # noqa: E402
    BedrockCompletionUpstream,
    _converse_stream_to_openai_sse,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

_DUMMY_CREDS = AwsCredentials(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="fakesecretkey0000000000000000000000000000",
    region="us-east-1",
)

_MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"


# ---------------------------------------------------------------------------
# Authentic AWS EventStream binary frame builder
# (byte-for-byte compatible with botocore EventStreamBuffer)
# ---------------------------------------------------------------------------


def _hdr(name: str, value: str) -> bytes:
    """Build a single string header (type code 7) in EventStream encoding."""
    n = name.encode()
    v = value.encode()
    return bytes([len(n)]) + n + bytes([7]) + struct.pack(">H", len(v)) + v


def _es_message(event_type: str, payload: dict[str, Any]) -> bytes:
    """Build one AWS EventStream message with authentic prelude + message CRCs."""
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


def _es_stream(*messages: bytes) -> bytes:
    """Concatenate multiple EventStream messages into a wire-format byte stream."""
    return b"".join(messages)


# ---------------------------------------------------------------------------
# Standard ConverseStream event sequence (used in BS4 / BS5 / BS6)
# ---------------------------------------------------------------------------

_MSG_START = _es_message("messageStart", {"role": "assistant"})
_DELTA_HELLO = _es_message(
    "contentBlockDelta",
    {"delta": {"text": "Hello"}, "contentBlockIndex": 0},
)
_DELTA_WORLD = _es_message(
    "contentBlockDelta",
    {"delta": {"text": " world"}, "contentBlockIndex": 0},
)
_BLOCK_STOP = _es_message("contentBlockStop", {"contentBlockIndex": 0})
_MSG_STOP = _es_message("messageStop", {"stopReason": "end_turn"})
_METADATA = _es_message(
    "metadata",
    {"usage": {"inputTokens": 11, "outputTokens": 5, "totalTokens": 16}},
)

_FULL_STREAM = _es_stream(_MSG_START, _DELTA_HELLO, _DELTA_WORLD, _BLOCK_STOP, _MSG_STOP, _METADATA)

# max_tokens variant (BS6)
_MSG_STOP_MAX_TOKENS = _es_message("messageStop", {"stopReason": "max_tokens"})
_FULL_STREAM_MAX_TOKENS = _es_stream(
    _MSG_START,
    _DELTA_HELLO,
    _DELTA_WORLD,
    _BLOCK_STOP,
    _MSG_STOP_MAX_TOKENS,
    _METADATA,
)


# ---------------------------------------------------------------------------
# Adapter factory — mirrors bedrock_provider suite's _make_adapter exactly
# ---------------------------------------------------------------------------


def _make_adapter(
    handler: object,
    *,
    creds: AwsCredentials = _DUMMY_CREDS,
    region: str = "us-east-1",
    endpoint_url: str = "https://bedrock-runtime.us-east-1.amazonaws.com",
    max_retries: int = 0,
) -> BedrockCompletionUpstream:
    """Construct the adapter, then swap its _client for a MockTransport-backed one."""
    adapter = BedrockCompletionUpstream(
        credentials=creds,
        region=region,
        endpoint_url=endpoint_url,
        default_max_tokens=4096,
        max_retries=max_retries,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


# ---------------------------------------------------------------------------
# BS1 — decode_event_stream: 6-message stream decodes to 6 tuples in order
# ---------------------------------------------------------------------------


def test_decode_multi_message() -> None:
    """decode_event_stream on a 6-message _es_stream yields 6 tuples.

    Asserts:
    - Exactly 6 tuples returned (one per _es_message in insertion order).
    - headers dict for each tuple contains ":event-type" with the correct value.
    - json.loads(payload) round-trips the original dict payload intact.
    """
    event_types = [
        "messageStart",
        "contentBlockDelta",
        "contentBlockDelta",
        "contentBlockStop",
        "messageStop",
        "metadata",
    ]
    payloads = [
        {"role": "assistant"},
        {"delta": {"text": "Hello"}, "contentBlockIndex": 0},
        {"delta": {"text": " world"}, "contentBlockIndex": 0},
        {"contentBlockIndex": 0},
        {"stopReason": "end_turn"},
        {"usage": {"inputTokens": 11, "outputTokens": 5, "totalTokens": 16}},
    ]

    wire = _es_stream(*[_es_message(et, p) for et, p in zip(event_types, payloads, strict=True)])
    results = list(decode_event_stream(wire))

    assert len(results) == 6, f"Expected 6 tuples, got {len(results)}"

    for i, ((headers, payload), expected_type, expected_payload) in enumerate(
        zip(results, event_types, payloads, strict=True)
    ):
        assert headers[":event-type"] == expected_type, (
            f"Frame {i}: expected event-type {expected_type!r}, got {headers[':event-type']!r}"
        )
        decoded = json.loads(payload)
        assert decoded == expected_payload, (
            f"Frame {i}: payload mismatch: {decoded!r} != {expected_payload!r}"
        )


# ---------------------------------------------------------------------------
# BS2 — decode_event_stream: CRC mismatch raises EventStreamError
# ---------------------------------------------------------------------------


def test_decode_crc_mismatch() -> None:
    """Flipping a byte inside the payload region corrupts the message CRC.

    After building a valid _es_message, one byte inside the payload region
    (offset 12 + headers_len + 1, safely within body) is flipped. Iterating
    decode_event_stream must raise EventStreamError — not silently yield the
    corrupt frame.
    """
    # Build a valid single-message stream.
    valid = _es_message("messageStart", {"role": "assistant"})

    # Parse prelude to find where the payload starts: offset 12 + headers_len.
    total_len = struct.unpack(">I", valid[:4])[0]
    headers_len = struct.unpack(">I", valid[4:8])[0]
    # Flip a byte inside the body region (after 12-byte prelude + headers).
    flip_offset = 12 + headers_len + 1  # +1 to land safely inside the JSON body
    assert flip_offset < total_len - 4, "Flip offset must be inside the body, not the CRC"

    corrupted = bytearray(valid)
    corrupted[flip_offset] ^= 0xFF
    wire = bytes(corrupted)

    with pytest.raises(EventStreamError):
        list(decode_event_stream(wire))


# ---------------------------------------------------------------------------
# BS3 — decode_event_stream: truncated buffer raises EventStreamError
# ---------------------------------------------------------------------------


def test_decode_truncated() -> None:
    """A buffer sliced shorter than its declared total_len raises EventStreamError.

    decode_event_stream must detect the truncation (it cannot read total_len bytes)
    and raise EventStreamError rather than silently returning an empty iterator or
    yielding a partial frame.
    """
    valid = _es_message("messageStart", {"role": "assistant"})
    # Truncate to half the message — well short of total_len.
    truncated = valid[: len(valid) // 2]

    with pytest.raises(EventStreamError):
        list(decode_event_stream(truncated))


# ---------------------------------------------------------------------------
# BS4 — stream() end-to-end translation (MockTransport 200)
# ---------------------------------------------------------------------------


async def test_stream_translation_end_to_end() -> None:
    """BedrockCompletionUpstream.stream() over a MockTransport 200 response.

    Asserts (in order):
    1. First chunk: role-delta frame {"choices":[{"delta":{"role":"assistant"},...}]}.
    2. Content deltas carry "Hello" then " world" in sequence.
    3. Terminal chunk: finish_reason "stop" AND top-level usage dict present.
    4. chunks[-1] == b"data: [DONE]\\n\\n".
    5. Outgoing request path (raw_path decoded once) ends with "/converse-stream".
    6. Authorization header starts with "AWS4-HMAC-SHA256".
    """
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(
            200,
            content=_FULL_STREAM,
            headers={"content-type": "application/vnd.amazon.eventstream"},
        )

    adapter = _make_adapter(handler)
    chunks = await _drain(
        adapter.stream(
            {
                "model": _MODEL_ID,
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
    )

    # Must have produced at least role + 2 content + terminal + [DONE]
    assert len(chunks) >= 4, f"Expected ≥4 chunks, got {len(chunks)}: {chunks!r}"

    # chunks[-1] is the sentinel
    assert chunks[-1] == b"data: [DONE]\n\n", (
        f"Last chunk must be b'data: [DONE]\\n\\n', got {chunks[-1]!r}"
    )

    joined = b"".join(chunks)

    # Role delta must appear first (before any content delta)
    role_chunk = json.loads(chunks[0].decode().removeprefix("data: ").strip())
    assert role_chunk["object"] == "chat.completion.chunk"
    role_delta = role_chunk["choices"][0]["delta"]
    assert role_delta.get("role") == "assistant", (
        f"First delta must carry role:'assistant', got {role_delta!r}"
    )

    # Content "Hello" and " world" must both appear
    assert b'"Hello"' in joined or b"Hello" in joined, "Content delta 'Hello' missing from stream"
    assert b'" world"' in joined or b" world" in joined, (
        "Content delta ' world' missing from stream"
    )

    # Terminal frame: finish_reason "stop" + usage
    # The terminal frame is the last data: frame before [DONE]
    terminal_raw = chunks[-2].decode().removeprefix("data: ").strip()
    terminal = json.loads(terminal_raw)
    assert terminal["choices"][0]["finish_reason"] == "stop", (
        f"Terminal finish_reason must be 'stop', got {terminal['choices'][0]['finish_reason']!r}"
    )
    assert "usage" in terminal, f"Terminal frame must carry top-level usage, got {terminal!r}"
    usage = terminal["usage"]
    assert usage.get("prompt_tokens") == 11
    assert usage.get("completion_tokens") == 5
    assert usage.get("total_tokens") == 16

    # Request path check
    req = captured["req"]
    wire = req.url.raw_path.decode()
    assert wire.endswith("/converse-stream"), (
        f"Request path must end with /converse-stream, got {wire!r}"
    )

    # SigV4 Authorization header
    auth = req.headers.get("authorization", "")
    assert auth.startswith("AWS4-HMAC-SHA256"), (
        f"Authorization must start with AWS4-HMAC-SHA256, got {auth!r}"
    )


# ---------------------------------------------------------------------------
# BS5 — extract_usage_from_sse works on the drained stream
# ---------------------------------------------------------------------------


async def test_stream_usage_extractable() -> None:
    """extract_usage_from_sse on the drained stream returns the correct token counts.

    The terminal frame emitted by _converse_stream_to_openai_sse must embed a
    top-level "usage" key so that the gateway's billing extractor can read it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_FULL_STREAM,
            headers={"content-type": "application/vnd.amazon.eventstream"},
        )

    adapter = _make_adapter(handler)
    chunks = await _drain(
        adapter.stream(
            {
                "model": _MODEL_ID,
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
    )

    usage = extract_usage_from_sse(chunks)
    assert usage == {
        "prompt_tokens": 11,
        "completion_tokens": 5,
        "total_tokens": 16,
    }, f"extract_usage_from_sse returned unexpected result: {usage!r}"


# ---------------------------------------------------------------------------
# BS6 — stopReason "max_tokens" maps to finish_reason "length"
# ---------------------------------------------------------------------------


async def test_stream_finish_reason_length() -> None:
    """ConverseStream messageStop with stopReason 'max_tokens' translates to
    finish_reason 'length' in the terminal OpenAI SSE frame.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_FULL_STREAM_MAX_TOKENS,
            headers={"content-type": "application/vnd.amazon.eventstream"},
        )

    adapter = _make_adapter(handler)
    chunks = await _drain(
        adapter.stream(
            {
                "model": _MODEL_ID,
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
    )

    assert chunks[-1] == b"data: [DONE]\n\n"

    # Terminal frame is second-to-last
    terminal_raw = chunks[-2].decode().removeprefix("data: ").strip()
    terminal = json.loads(terminal_raw)
    finish_reason = terminal["choices"][0]["finish_reason"]
    assert finish_reason == "length", (
        f"stopReason 'max_tokens' must map to finish_reason 'length', got {finish_reason!r}"
    )


# ---------------------------------------------------------------------------
# BS7 — 5xx pre-first-byte raises UpstreamUnavailableError; zero chunks yielded
# ---------------------------------------------------------------------------


async def test_stream_5xx_pre_first_byte_raises() -> None:
    """MockTransport 503 → stream() raises UpstreamUnavailableError.

    No chunks must be yielded before the exception is raised — the error is
    detected at the HTTP status-check before any event-stream decoding begins.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"message": "Service unavailable", "__type": "ServiceUnavailableException"},
        )

    adapter = _make_adapter(handler)

    collected: list[bytes] = []
    with pytest.raises(UpstreamUnavailableError):
        async for chunk in adapter.stream(
            {"model": _MODEL_ID, "messages": [{"role": "user", "content": "x"}]}
        ):
            collected.append(chunk)

    assert collected == [], (
        f"Zero chunks must be yielded before UpstreamUnavailableError, got {collected!r}"
    )


# ---------------------------------------------------------------------------
# BS8 — Request path encoding + Protocol compliance
# ---------------------------------------------------------------------------


async def test_stream_signs_converse_stream_path() -> None:
    """The wire request-target routes to the EXACT model id on the converse-stream path.

    Asserts:
    - unquote(wire) == f"/model/{_MODEL_ID}/converse-stream"
      (catches double-encode: …v2%253A0 decodes once to …v2%3A0 — the wrong model)
    - The SigV4 canonical URI single-encodes ':' as %3A (never %253A).
    """
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(
            200,
            content=_FULL_STREAM,
            headers={"content-type": "application/vnd.amazon.eventstream"},
        )

    adapter = _make_adapter(handler)
    await _drain(
        adapter.stream({"model": _MODEL_ID, "messages": [{"role": "user", "content": "hi"}]})
    )

    req = captured["req"]
    wire = req.url.raw_path.decode()

    # Single-decode must resolve to the exact unencoded path
    assert unquote(wire) == f"/model/{_MODEL_ID}/converse-stream", (
        f"Wire target must route to the exact model id; got {wire!r} -> {unquote(wire)!r}"
    )

    # SigV4 canonical URI: single-encode ':' as %3A (never double-encode as %253A)
    canonical_uri = quote(unquote(wire), safe="/~")
    assert "%3A" in canonical_uri, (
        f"Canonical URI must contain %3A (single-encoded ':'), got: {canonical_uri}"
    )
    assert "%253A" not in canonical_uri, (
        f"Canonical URI must NOT contain %253A (double-encoded ':'), got: {canonical_uri}"
    )


async def test_protocol() -> None:
    """isinstance(adapter, CompletionUpstream) must be True (structural Protocol check)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_FULL_STREAM,
            headers={"content-type": "application/vnd.amazon.eventstream"},
        )

    adapter = _make_adapter(handler)
    assert isinstance(adapter, CompletionUpstream), (
        "BedrockCompletionUpstream must satisfy the CompletionUpstream Protocol"
    )
