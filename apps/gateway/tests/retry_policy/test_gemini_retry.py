"""Red suite: Gemini upstream gains unified retries (M2 + M4 + M6 + M7).

RED until BUILD wires GeminiCompletionUpstream.complete() onto execute_with_retry.
Today Gemini does exactly one attempt and raises on 5xx/transport.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from prometheus_client import CollectorRegistry

from gateway.observability.metrics import MetricsRegistry
from gateway.proxy.domain.errors import UpstreamUnavailableError

from .conftest import (
    GEMINI_PAYLOAD,
    GEMINI_SUCCESS_BODY,
    CountingCircuitBreaker,
    SequencedMockTransport,
    make_gemini_upstream,
    make_json_response,
)


def _metrics() -> MetricsRegistry:
    return MetricsRegistry(registry=CollectorRegistry())


def _counter(reg: MetricsRegistry, *, reason: str, outcome: str) -> float:
    return reg.upstream_retries_total.labels(
        provider="gemini", reason=reason, outcome=outcome
    )._value.get()


async def test_gemini_retries_503_then_200() -> None:
    transport = SequencedMockTransport(
        [
            make_json_response(503, {"error": {"message": "overloaded"}}),
            make_json_response(200, GEMINI_SUCCESS_BODY),
        ]
    )
    breaker = CountingCircuitBreaker()
    reg = _metrics()
    upstream = make_gemini_upstream(transport, breaker=breaker, max_retries=1, metrics_registry=reg)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, body = await upstream.complete(GEMINI_PAYLOAD)
    assert status == 200
    assert body["choices"][0]["message"]["content"] == "ok"  # translated to OpenAI shape
    assert transport.call_count == 2
    assert _counter(reg, reason="upstream_5xx", outcome="retried") == 1.0


async def test_gemini_connect_error_then_200() -> None:
    transport = SequencedMockTransport(
        [httpx.ConnectError("refused"), make_json_response(200, GEMINI_SUCCESS_BODY)]
    )
    breaker = CountingCircuitBreaker()
    reg = _metrics()
    upstream = make_gemini_upstream(transport, breaker=breaker, max_retries=1, metrics_registry=reg)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, _ = await upstream.complete(GEMINI_PAYLOAD)
    assert status == 200
    assert transport.call_count == 2
    assert breaker.error_call_count == 1
    assert _counter(reg, reason="connect_error", outcome="retried") == 1.0


async def test_gemini_408_is_retried() -> None:
    transport = SequencedMockTransport(
        [
            make_json_response(408, {"error": {"message": "timeout"}}),
            make_json_response(200, GEMINI_SUCCESS_BODY),
        ]
    )
    reg = _metrics()
    upstream = make_gemini_upstream(transport, max_retries=1, metrics_registry=reg)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, _ = await upstream.complete(GEMINI_PAYLOAD)
    assert status == 200
    assert transport.call_count == 2
    assert _counter(reg, reason="upstream_408", outcome="retried") == 1.0


async def test_gemini_default_off_byte_identical() -> None:
    transport = SequencedMockTransport([make_json_response(503, {"error": {"message": "x"}})])
    upstream = make_gemini_upstream(transport, max_retries=0)
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleeper:
        with pytest.raises(UpstreamUnavailableError):
            await upstream.complete(GEMINI_PAYLOAD)
    assert transport.call_count == 1
    sleeper.assert_not_awaited()


async def test_gemini_400_passthrough_not_retried() -> None:
    transport = SequencedMockTransport([make_json_response(400, {"error": {"message": "bad"}})])
    upstream = make_gemini_upstream(transport, max_retries=3)
    status, _ = await upstream.complete(GEMINI_PAYLOAD)
    assert status == 400
    assert transport.call_count == 1


async def test_gemini_stream_never_retried() -> None:
    """M7: stream() must have ZERO retry machinery even with max_retries>0."""
    transport = SequencedMockTransport(
        [httpx.ConnectError("refused"), make_json_response(200, GEMINI_SUCCESS_BODY)]
    )
    upstream = make_gemini_upstream(transport, max_retries=3)
    with pytest.raises(UpstreamUnavailableError):
        async for _ in upstream.stream(GEMINI_PAYLOAD):
            pass
    assert transport.call_count == 1
