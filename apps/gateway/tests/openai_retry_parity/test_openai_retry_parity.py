"""Red suite: OpenAI direct-chat gains unified retries (parity with the other 5 adapters).

RED until BUILD routes OpenAIDirectProvider.complete() through execute_with_retry and threads
settings.upstream_* into the main.py ctor. Today OpenAI does exactly one attempt and only raises
on >=500 — so:
  - retry assertions fail for the right reason (no retry loop yet),
  - the 408 case fails because 408 is currently passed through, not retried,
  - the class-default and wiring assertions fail because the knobs are unwired (no _max_retries).
A handful are GREEN by design (invariant guards): default-off byte-identity, 4xx passthrough,
stream() retry-freeness, fail-closed, and the dual-Protocol surface.

Run only this suite:
  cd apps/gateway && uv run pytest tests/openai_retry_parity/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from prometheus_client import CollectorRegistry

from gateway.main import create_app
from gateway.observability.metrics import MetricsRegistry
from gateway.proxy.domain.credential_context import current_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.ports import CompletionUpstream, UpstreamProvider
from gateway.proxy.domain.provider_credentials import ProviderKeyMissing
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider
from tests.retry_policy.conftest import (
    PAYLOAD,
    SUCCESS_BODY,
    CountingCircuitBreaker,
    SequencedMockTransport,
    make_json_response,
)

from .conftest import make_openai_upstream, make_settings


def _metrics() -> MetricsRegistry:
    return MetricsRegistry(registry=CollectorRegistry())


def _counter(reg: MetricsRegistry, *, reason: str, outcome: str) -> float:
    return reg.upstream_retries_total.labels(
        provider="openai", reason=reason, outcome=outcome
    )._value.get()


# ===========================================================================
# Retry behavior (RED until complete() routes through execute_with_retry)
# ===========================================================================


async def test_openai_retries_503_then_200() -> None:
    transport = SequencedMockTransport(
        [
            make_json_response(503, {"error": "overloaded"}),
            make_json_response(200, SUCCESS_BODY),
        ]
    )
    breaker = CountingCircuitBreaker()
    reg = _metrics()
    upstream = make_openai_upstream(transport, breaker=breaker, max_retries=1, metrics_registry=reg)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, body = await upstream.complete(PAYLOAD)
    assert status == 200
    assert body == SUCCESS_BODY
    assert transport.call_count == 2
    assert breaker.error_call_count == 1
    assert breaker.success_call_count == 1
    assert _counter(reg, reason="upstream_5xx", outcome="retried") == 1.0


async def test_openai_408_is_retried() -> None:
    transport = SequencedMockTransport(
        [
            make_json_response(408, {"error": "timeout"}),
            make_json_response(200, SUCCESS_BODY),
        ]
    )
    reg = _metrics()
    upstream = make_openai_upstream(transport, max_retries=1, metrics_registry=reg)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, _ = await upstream.complete(PAYLOAD)
    assert status == 200
    assert transport.call_count == 2
    assert _counter(reg, reason="upstream_408", outcome="retried") == 1.0


async def test_openai_429_retried_and_honors_retry_after() -> None:
    """429 is retryable (parity with the seam's R4) and the Retry-After header sets the delay."""
    r429 = httpx.Response(
        status_code=429,
        headers={"content-type": "application/json", "Retry-After": "1"},
        content=b'{"error": "rate limited"}',
    )
    transport = SequencedMockTransport([r429, make_json_response(200, SUCCESS_BODY)])
    upstream = make_openai_upstream(transport, max_retries=1)

    delays: list[float] = []

    async def _capture(delay: float) -> None:
        delays.append(delay)

    with patch("asyncio.sleep", side_effect=_capture):
        status, _ = await upstream.complete(PAYLOAD)
    assert status == 200
    assert transport.call_count == 2
    assert delays and delays[0] >= 1.0, f"expected Retry-After delay >= 1.0s, got {delays}"


async def test_openai_persistent_503_exhausts_all_retries() -> None:
    transport = SequencedMockTransport(
        [
            make_json_response(503, {"error": "down"}),
            make_json_response(503, {"error": "down"}),
            make_json_response(503, {"error": "down"}),
        ]
    )
    breaker = CountingCircuitBreaker()
    upstream = make_openai_upstream(transport, breaker=breaker, max_retries=2)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(UpstreamUnavailableError):
            await upstream.complete(PAYLOAD)
    assert transport.call_count == 3
    assert breaker.error_call_count == 3


async def test_openai_connect_error_then_200() -> None:
    transport = SequencedMockTransport(
        [httpx.ConnectError("refused"), make_json_response(200, SUCCESS_BODY)]
    )
    breaker = CountingCircuitBreaker()
    upstream = make_openai_upstream(transport, breaker=breaker, max_retries=1)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, _ = await upstream.complete(PAYLOAD)
    assert status == 200
    assert transport.call_count == 2
    assert breaker.error_call_count == 1
    assert breaker.success_call_count == 1


# ===========================================================================
# Invariant guards (GREEN by design — pin behavior the refactor must preserve)
# ===========================================================================


@pytest.mark.parametrize("status", [503, 408, 429])
async def test_openai_default_off_single_attempt_retryable_raises(status: int) -> None:
    """max_retries=0 → exactly 1 attempt, no sleep, and ANY retryable status raises.

    For 5xx this is byte-identical to the pre-retry code. For 408/429 it is an INTENTIONAL
    change: the old hand-rolled complete() passed 408/429 through as (status, body) (status<500);
    routing through the shared seam classifies them retryable, so with retries disabled they
    surface as UpstreamUnavailableError — exact parity with the other 5 chat adapters.
    """
    transport = SequencedMockTransport([make_json_response(status, {"error": "x"})])
    upstream = make_openai_upstream(transport, max_retries=0)
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleeper:
        with pytest.raises(UpstreamUnavailableError):
            await upstream.complete(PAYLOAD)
    assert transport.call_count == 1
    sleeper.assert_not_awaited()


async def test_openai_400_passthrough_not_retried() -> None:
    error_body = {"error": "bad request", "code": "invalid_model"}
    transport = SequencedMockTransport([make_json_response(400, error_body)])
    upstream = make_openai_upstream(transport, max_retries=3)
    status, body = await upstream.complete(PAYLOAD)
    assert status == 400
    assert body == error_body
    assert transport.call_count == 1  # terminal — no retry


async def test_openai_stream_never_retried() -> None:
    """stream() must have ZERO retry machinery even with max_retries>0."""
    transport = SequencedMockTransport(
        [httpx.ConnectError("refused"), make_json_response(200, SUCCESS_BODY)]
    )
    upstream = make_openai_upstream(transport, max_retries=3)
    with pytest.raises(UpstreamUnavailableError):
        async for _ in upstream.stream(PAYLOAD):
            pass
    assert transport.call_count == 1  # no retry on the stream path


async def test_openai_failclosed_no_credential_no_http() -> None:
    """Fail-closed survives the retry refactor: no contextvar credential → ProviderKeyMissing
    BEFORE any HTTP, even with retries configured."""
    # The autouse fixture injected a credential; clear it for this test only.
    token = current_provider_credential.set(None)
    try:
        transport = SequencedMockTransport([make_json_response(200, SUCCESS_BODY)])
        upstream = make_openai_upstream(transport, max_retries=3)
        with pytest.raises(ProviderKeyMissing) as exc_info:
            await upstream.complete(PAYLOAD)
        assert exc_info.value.code == "ERR_PROVIDER_KEY_MISSING"
        assert transport.call_count == 0
    finally:
        current_provider_credential.reset(token)


async def test_openai_still_satisfies_both_protocols() -> None:
    transport = SequencedMockTransport([make_json_response(200, SUCCESS_BODY)])
    upstream = make_openai_upstream(transport)
    assert isinstance(upstream, CompletionUpstream)
    assert isinstance(upstream, UpstreamProvider)


# ===========================================================================
# Class-default + production-wiring (RED until __init__/main.py are wired)
# ===========================================================================


def test_new_built_provider_uses_class_default_retries() -> None:
    """A __new__-built provider (the FROZEN openai_chat_dispatch pattern) must read the
    three retry knobs as class defaults — never AttributeError."""
    provider = OpenAIDirectProvider.__new__(OpenAIDirectProvider)
    assert provider._max_retries == 0
    assert provider._backoff_base == 0.5
    assert provider._retry_deadline_s == 0.0


def test_wiring_default_max_retries_zero() -> None:
    app = create_app(make_settings())
    adapter = app.state.chat_adapters["openai"]
    assert isinstance(adapter, OpenAIDirectProvider)
    assert adapter._max_retries == 0


def test_wiring_custom_retry_settings_threaded() -> None:
    app = create_app(
        make_settings(
            upstream_max_retries=3,
            upstream_retry_backoff_base_s=1.5,
            upstream_retry_deadline_s=2.0,
        )
    )
    adapter = app.state.chat_adapters["openai"]
    assert adapter._max_retries == 3
    assert adapter._backoff_base == 1.5
    assert adapter._retry_deadline_s == 2.0
