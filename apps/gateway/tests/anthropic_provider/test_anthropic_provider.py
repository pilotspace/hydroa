"""Red suite for anthropic-provider (v9 task 2/4) — TASK.md §4.

Tests the Anthropic Messages API chat adapter: OpenAI <-> Anthropic translation
(request, response, SSE stream, usage, errors) + composition-root wiring. Pure
translation helpers are unit-tested directly; the adapter's HTTP behavior uses an
httpx.MockTransport (no network, no real key).

Contract: TASK.md §3 (FROZEN @ v1).
  - AnthropicCompletionUpstream implements the EXISTING CompletionUpstream Protocol.
  - complete(): 200 -> OpenAI chat.completion; 4xx -> OpenAI error body (passthrough);
    5xx/transport -> UpstreamUnavailableError.
  - stream(): Anthropic SSE -> OpenAI chunk bytes; terminal frame carries finish_reason
    + usage; ends with "data: [DONE]\n\n".
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

# RED until BUILD creates the module/symbols.
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.ports import CompletionUpstream
from gateway.proxy.infrastructure.anthropic_upstream import (
    AnthropicCompletionUpstream,
    _anthropic_error_to_openai,
    _anthropic_to_openai,
    _map_finish_reason,
    _openai_to_anthropic_request,
    _translate_anthropic_sse,
)
from gateway.usage.domain.extractor import extract_usage_from_sse

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_ANTHROPIC_200 = {
    "id": "msg_01ABC",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-sonnet-20241022",
    "content": [{"type": "text", "text": "Hello world"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 5},
}

_ANTHROPIC_SSE = (
    'event: message_start\n'
    'data: {"type":"message_start","message":{"id":"msg_01ABC","model":"claude-3-5-sonnet-20241022","usage":{"input_tokens":10,"output_tokens":1}}}\n\n'
    'event: content_block_start\n'
    'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    'event: ping\n'
    'data: {"type":"ping"}\n\n'
    'event: content_block_delta\n'
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
    'event: content_block_delta\n'
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}\n\n'
    'event: content_block_stop\n'
    'data: {"type":"content_block_stop","index":0}\n\n'
    'event: message_delta\n'
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":5}}\n\n'
    'event: message_stop\n'
    'data: {"type":"message_stop"}\n\n'
).encode()


def _make_adapter_with_handler(handler: object) -> AnthropicCompletionUpstream:
    """Construct the adapter, then swap its client for a MockTransport-backed one."""
    adapter = AnthropicCompletionUpstream(
        api_key="sk-ant-test",
        base_url="https://api.anthropic.com/v1",
        anthropic_version="2023-06-01",
        default_max_tokens=4096,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url="https://api.anthropic.com/v1",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


# ---------------------------------------------------------------------------
# Pure translation helpers
# ---------------------------------------------------------------------------


def test_request_translation_system_lift() -> None:
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "system", "content": "You are S"},
            {"role": "user", "content": "Hi"},
        ],
        "max_tokens": 100,
        "temperature": 0.7,
    }
    body = _openai_to_anthropic_request(payload, default_max_tokens=4096)
    assert body["system"] == "You are S"
    assert body["messages"] == [{"role": "user", "content": "Hi"}]
    assert body["max_tokens"] == 100
    assert body["temperature"] == 0.7
    assert body["model"] == "claude-3-5-sonnet-20241022"


def test_max_tokens_defaulted() -> None:
    body = _openai_to_anthropic_request(
        {"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]},
        default_max_tokens=4096,
    )
    assert body["max_tokens"] == 4096


def test_stop_sequences_mapping() -> None:
    body = _openai_to_anthropic_request(
        {"model": "c", "messages": [{"role": "user", "content": "x"}], "stop": ["STOP", "END"]},
        default_max_tokens=4096,
    )
    assert body["stop_sequences"] == ["STOP", "END"]


def test_response_translation_non_stream() -> None:
    openai_body = _anthropic_to_openai(_ANTHROPIC_200)
    assert openai_body["object"] == "chat.completion"
    assert openai_body["id"] == "msg_01ABC"
    assert openai_body["model"] == "claude-3-5-sonnet-20241022"
    choice = openai_body["choices"][0]
    assert choice["index"] == 0
    assert choice["message"] == {"role": "assistant", "content": "Hello world"}
    assert choice["finish_reason"] == "stop"
    assert openai_body["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_finish_reason_mapping() -> None:
    assert _map_finish_reason("end_turn") == "stop"
    assert _map_finish_reason("max_tokens") == "length"
    assert _map_finish_reason("stop_sequence") == "stop"
    assert _map_finish_reason("tool_use") == "tool_calls"
    assert _map_finish_reason(None) == "stop"
    assert _map_finish_reason("something_new") == "stop"


def test_error_envelope_mapping() -> None:
    out = _anthropic_error_to_openai(
        {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    )
    assert out == {
        "error": {"message": "bad", "type": "invalid_request_error", "code": "invalid_request_error"}
    }


def test_sse_translation_helper() -> None:
    events = [
        ("message_start", {"type": "message_start", "message": {"id": "msg_1", "model": "c", "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}),
        ("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    chunks = list(_translate_anthropic_sse(events))
    joined = b"".join(chunks)
    # first chunk announces the assistant role
    first = json.loads(chunks[0].decode().removeprefix("data: ").strip())
    assert first["object"] == "chat.completion.chunk"
    assert first["choices"][0]["delta"] == {"role": "assistant"}
    # content deltas present in order
    assert b'"content": "Hello"' in joined or b'"content":"Hello"' in joined
    assert b'"content": " world"' in joined or b'"content":" world"' in joined
    # terminal frame carries finish_reason + usage, then [DONE]
    assert chunks[-1] == b"data: [DONE]\n\n"
    usage = extract_usage_from_sse(chunks)
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


# ---------------------------------------------------------------------------
# Adapter HTTP behavior (MockTransport)
# ---------------------------------------------------------------------------


async def test_auth_headers_x_api_key_no_bearer() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=_ANTHROPIC_200)

    adapter = _make_adapter_with_handler(handler)
    await adapter.complete({"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]})
    assert seen.get("x-api-key") == "sk-ant-test"
    assert seen.get("anthropic-version") == "2023-06-01"
    assert "authorization" not in seen


async def test_complete_200_translates_to_openai() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        assert sent["system"] == "S"
        assert sent["messages"] == [{"role": "user", "content": "Hi"}]
        assert request.url.path.endswith("/messages")
        return httpx.Response(200, json=_ANTHROPIC_200)

    adapter = _make_adapter_with_handler(handler)
    status, body = await adapter.complete(
        {
            "model": "claude-x",
            "messages": [
                {"role": "system", "content": "S"},
                {"role": "user", "content": "Hi"},
            ],
            "max_tokens": 50,
        }
    )
    assert status == 200
    assert body["choices"][0]["message"]["content"] == "Hello world"
    assert body["usage"]["total_tokens"] == 15


async def test_5xx_raises_upstream_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})

    adapter = _make_adapter_with_handler(handler)
    with pytest.raises(UpstreamUnavailableError):
        await adapter.complete({"model": "c", "messages": [{"role": "user", "content": "x"}]})


async def test_4xx_error_envelope_passthrough() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}})

    adapter = _make_adapter_with_handler(handler)
    status, body = await adapter.complete({"model": "c", "messages": [{"role": "user", "content": "x"}]})
    assert status == 400
    assert body == {"error": {"message": "bad", "type": "invalid_request_error", "code": "invalid_request_error"}}


async def test_stream_translation_end_to_end() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        assert sent.get("stream") is True
        return httpx.Response(200, content=_ANTHROPIC_SSE, headers={"content-type": "text/event-stream"})

    adapter = _make_adapter_with_handler(handler)
    chunks = await _drain(adapter.stream({"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]}))
    joined = b"".join(chunks)
    assert b'"role": "assistant"' in joined or b'"role":"assistant"' in joined
    assert b"Hello" in joined and b" world" in joined
    assert chunks[-1] == b"data: [DONE]\n\n"
    assert extract_usage_from_sse(chunks) == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


async def test_complete_satisfies_protocol() -> None:
    adapter = _make_adapter_with_handler(lambda req: httpx.Response(200, json=_ANTHROPIC_200))
    assert isinstance(adapter, CompletionUpstream)


# ---------------------------------------------------------------------------
# Composition-root wiring
# ---------------------------------------------------------------------------


def _make_settings(**kwargs: object):  # type: ignore[no-untyped-def]
    from gateway.core.config import Settings

    defaults: dict[str, object] = {
        "database_url": "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        "jwt_secret": "test-secret-not-for-production-0123456789",
        "redis_url": "redis://localhost:6380/9",
        "environment": "test",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_wiring_anthropic_present_when_key_set() -> None:
    from gateway.main import create_app

    app = create_app(_make_settings(anthropic_api_key="sk-ant-live"))
    adapters = app.state.chat_adapters
    assert "anthropic" in adapters
    assert isinstance(adapters["anthropic"], AnthropicCompletionUpstream)


def test_wiring_anthropic_absent_when_key_empty() -> None:
    from gateway.main import create_app

    app = create_app(_make_settings())  # anthropic_api_key defaults to ""
    assert "anthropic" not in app.state.chat_adapters
    # the openrouter adapter is always present (dispatch-fallback target)
    assert "openrouter" in app.state.chat_adapters


# ---------------------------------------------------------------------------
# Hardening guard: a realistic recorded Anthropic stream (drift detector)
# ---------------------------------------------------------------------------

# A fuller, real-world-shaped Messages stream: content_block_start/stop with index
# fields, a ping, text split across three deltas (punctuation + spacing), and a
# message_delta carrying stop_sequence + output_tokens. This is the field-name/
# sequence drift detector promised at freeze; task-4 live-verify replays a captured
# stream end-to-end, but this keeps the translation honest in CI without a live key.
_ANTHROPIC_SSE_REALISTIC = (
    "event: message_start\n"
    'data: {"type":"message_start","message":{"id":"msg_01XYZ","type":"message","role":"assistant","model":"claude-3-5-sonnet-20241022","content":[],"stop_reason":null,"usage":{"input_tokens":17,"output_tokens":1}}}\n\n'
    "event: content_block_start\n"
    'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    "event: ping\n"
    'data: {"type":"ping"}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"The "}}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"answer is "}}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"42."}}\n\n'
    "event: content_block_stop\n"
    'data: {"type":"content_block_stop","index":0}\n\n'
    "event: message_delta\n"
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":9}}\n\n'
    "event: message_stop\n"
    'data: {"type":"message_stop"}\n\n'
).encode()


async def test_stream_realistic_recorded_reconstruction() -> None:
    """A realistic recorded Anthropic stream reconstructs exactly into OpenAI chunks."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_ANTHROPIC_SSE_REALISTIC,
            headers={"content-type": "text/event-stream"},
        )

    adapter = _make_adapter_with_handler(handler)
    chunks = await _drain(
        adapter.stream({"model": "claude-3-5-sonnet-20241022", "messages": [{"role": "user", "content": "q"}]})
    )

    # Reconstruct the assistant text from the OpenAI content deltas, in order.
    reconstructed = ""
    saw_role = False
    for raw in chunks:
        if raw == b"data: [DONE]\n\n":
            continue
        frame = json.loads(raw.decode().removeprefix("data: ").strip())
        delta = frame["choices"][0]["delta"]
        if delta.get("role") == "assistant":
            saw_role = True
        if "content" in delta:
            reconstructed += delta["content"]

    assert saw_role
    assert reconstructed == "The answer is 42."
    assert chunks[-1] == b"data: [DONE]\n\n"
    # Billing: the FROZEN extractor must read input/output tokens from the terminal frame.
    assert extract_usage_from_sse(chunks) == {
        "prompt_tokens": 17,
        "completion_tokens": 9,
        "total_tokens": 26,
    }
