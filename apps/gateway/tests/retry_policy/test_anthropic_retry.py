"""Red suite: Anthropic upstream gains unified retries (M2 + M4 + M6 + M7).

RED until BUILD wires AnthropicCompletionUpstream.complete() onto execute_with_retry.
Today Anthropic does exactly one attempt and raises on 5xx/transport — so every
retry assertion below fails for the right reason (no retry loop yet).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from gateway.observability.metrics import MetricsRegistry
from gateway.proxy.domain.errors import UpstreamUnavailableError
from prometheus_client import CollectorRegistry

from .conftest import (
    ANTHROPIC_PAYLOAD,
    ANTHROPIC_SUCCESS_BODY,
    CountingCircuitBreaker,
    SequencedMockTransport,
    make_anthropic_upstream,
    make_json_response,
)


def _metrics() -> MetricsRegistry:
    return MetricsRegistry(registry=CollectorRegistry())


def _counter(reg: MetricsRegistry, *, reason: str, outcome: str) -> float:
    return reg.upstream_retries_total.labels(
        provider="anthropic", reason=reason, outcome=outcome
    )._value.get()


async def test_anthropic_retries_503_then_200() -> None:
    transport = SequencedMockTransport(
        [
            make_json_response(503, {"type": "error"}),
            make_json_response(200, ANTHROPIC_SUCCESS_BODY),
        ]
    )
    breaker = CountingCircuitBreaker()
    reg = _metrics()
    upstream = make_anthropic_upstream(
        transport, breaker=breaker, max_retries=1, metrics_registry=reg
    )
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, body = await upstream.complete(ANTHROPIC_PAYLOAD)
    assert status == 200
    assert body["choices"][0]["message"]["content"] == "ok"  # translated to OpenAI shape
    assert transport.call_count == 2
    assert _counter(reg, reason="upstream_5xx", outcome="retried") == 1.0


async def test_anthropic_408_is_retried() -> None:
    transport = SequencedMockTransport(
        [
            make_json_response(408, {"type": "error"}),
            make_json_response(200, ANTHROPIC_SUCCESS_BODY),
        ]
    )
    reg = _metrics()
    upstream = make_anthropic_upstream(transport, max_retries=1, metrics_registry=reg)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, _ = await upstream.complete(ANTHROPIC_PAYLOAD)
    assert status == 200
    assert transport.call_count == 2
    assert _counter(reg, reason="upstream_408", outcome="retried") == 1.0


async def test_anthropic_connect_error_then_200() -> None:
    transport = SequencedMockTransport(
        [httpx.ConnectError("refused"), make_json_response(200, ANTHROPIC_SUCCESS_BODY)]
    )
    breaker = CountingCircuitBreaker()
    upstream = make_anthropic_upstream(transport, breaker=breaker, max_retries=1)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, _ = await upstream.complete(ANTHROPIC_PAYLOAD)
    assert status == 200
    assert transport.call_count == 2
    assert breaker.error_call_count == 1
    assert breaker.success_call_count == 1


async def test_anthropic_default_off_byte_identical() -> None:
    """max_retries=0 → exactly 1 attempt, 5xx raises (today's behavior preserved)."""
    transport = SequencedMockTransport([make_json_response(503, {"type": "error"})])
    upstream = make_anthropic_upstream(transport, max_retries=0)
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleeper:
        with pytest.raises(UpstreamUnavailableError):
            await upstream.complete(ANTHROPIC_PAYLOAD)
    assert transport.call_count == 1
    sleeper.assert_not_awaited()


async def test_anthropic_400_passthrough_not_retried() -> None:
    transport = SequencedMockTransport(
        [make_json_response(400, {"type": "error", "error": {"message": "bad"}})]
    )
    upstream = make_anthropic_upstream(transport, max_retries=3)
    status, _ = await upstream.complete(ANTHROPIC_PAYLOAD)
    assert status == 400
    assert transport.call_count == 1  # terminal — no retry


async def test_anthropic_stream_never_retried() -> None:
    """M7: stream() must have ZERO retry machinery even with max_retries>0."""
    transport = SequencedMockTransport(
        [httpx.ConnectError("refused"), make_json_response(200, ANTHROPIC_SUCCESS_BODY)]
    )
    upstream = make_anthropic_upstream(transport, max_retries=3)
    with pytest.raises(UpstreamUnavailableError):
        async for _ in upstream.stream(ANTHROPIC_PAYLOAD):
            pass
    assert transport.call_count == 1  # no retry on the stream path
