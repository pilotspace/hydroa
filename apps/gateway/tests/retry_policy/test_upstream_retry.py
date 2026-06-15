"""Red suite: the unified upstream retry seam (execute_with_retry + RetryPolicy).

Scenario map (TASK.md §2):
  M1   behavior-preserving extraction — executor 503,503,200 → (200,body), 3 attempts
  M3   classify_status / classify_exception (retryable vs terminal)
  M4   408 Request Timeout is now retryable
  M1   compute_backoff full-jitter window · parse_retry_after bounds
  M5   cumulative deadline stops further attempts (outcome="deadline_exceeded")
  M6   default-off (max_retries=0, deadline=0) → exactly 1 attempt, no sleep
  M8   non-retryable transport (ReadTimeout) trips breaker, no retry
  M9   provider label present on the counter
  M10  served-attempt-only body · no api-key substring in logs/labels
  Rejects: retries_exhausted · retry_deadline_exceeded · breaker_open · not_retryable · config_invalid

RED until BUILD creates apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py,
adds the upstream_retry_deadline_s config field, and gives upstream_retries_total a provider label.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from prometheus_client import CollectorRegistry
from pydantic import ValidationError

from gateway.core.config import Settings
from gateway.observability.metrics import MetricsRegistry
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.infrastructure.upstream_retry import (
    DEFAULT_RETRY_POLICY,
    compute_backoff,
    execute_with_retry,
    parse_retry_after,
)

from .conftest import (
    SUCCESS_BODY,
    CountingCircuitBreaker,
    make_json_response,
)


# --------------------------------------------------------------------------- helpers


def _seq_do_request(
    items: list[Any],
) -> tuple[Callable[[], Awaitable[httpx.Response]], dict[str, int]]:
    """Return (do_request, counter) replaying `items` (Response or Exception) one per call."""
    state = {"i": 0}

    async def _do() -> httpx.Response:
        i = state["i"]
        state["i"] += 1
        entry = items[i]
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, type) and issubclass(entry, BaseException):
            raise entry("mock transport error")
        return entry

    return _do, state


def _render(resp: httpx.Response) -> tuple[int, dict[str, Any]]:
    return resp.status_code, resp.json()


def _metrics() -> MetricsRegistry:
    return MetricsRegistry(registry=CollectorRegistry())


def _counter(reg: MetricsRegistry, *, provider: str, reason: str, outcome: str) -> float:
    return reg.upstream_retries_total.labels(
        provider=provider, reason=reason, outcome=outcome
    )._value.get()


# --------------------------------------------------------------------------- M1 extraction


async def test_executor_503_503_200_is_retried_success() -> None:
    do_request, state = _seq_do_request(
        [
            make_json_response(503, {"error": "overloaded"}),
            make_json_response(503, {"error": "overloaded"}),
            make_json_response(200, SUCCESS_BODY),
        ]
    )
    breaker = CountingCircuitBreaker()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, body = await execute_with_retry(
            do_request,
            _render,
            breaker=breaker,
            provider="openrouter",
            max_retries=2,
            backoff_base=0.5,
        )
    assert (status, body) == (200, SUCCESS_BODY)
    assert state["i"] == 3
    assert breaker.error_call_count == 2
    assert breaker.success_call_count == 1


# --------------------------------------------------------------------------- M3 / M4 policy


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, "upstream_429"),
        (408, "upstream_408"),  # M4 — newly retryable
        (500, "upstream_5xx"),
        (503, "upstream_5xx"),
        (400, None),
        (401, None),
        (404, None),
        (200, None),
    ],
)
def test_classify_status(status: int, expected: str | None) -> None:
    assert DEFAULT_RETRY_POLICY.classify_status(status) == expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("x"), "connect_error"),
        (httpx.ConnectTimeout("x"), "connect_error"),
        (httpx.PoolTimeout("x"), "pool_timeout"),
        (httpx.ReadTimeout("x"), None),
        (httpx.WriteTimeout("x"), None),
        (httpx.NetworkError("x"), None),
    ],
)
def test_classify_exception(exc: BaseException, expected: str | None) -> None:
    assert DEFAULT_RETRY_POLICY.classify_exception(exc) == expected


def test_compute_backoff_full_jitter_window() -> None:
    # full-jitter: 0 <= delay <= min(cap, base*2**attempt)
    for attempt in range(1, 6):
        delay = compute_backoff(attempt, backoff_base=0.5)
        assert 0.0 <= delay <= min(8.0, 0.5 * (2**attempt))


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("1", 1.0),
        ("60", 60.0),
        ("0", 0.0),
        ("61", None),
        ("-1", None),
        ("Wed, 21 Oct", None),
        ("", None),
    ],
)
def test_parse_retry_after(header: str, expected: float | None) -> None:
    assert parse_retry_after(header) == expected


# --------------------------------------------------------------------------- M4 408 end-to-end


async def test_408_is_retried_then_succeeds() -> None:
    do_request, state = _seq_do_request(
        [
            make_json_response(408, {"error": "request timeout"}),
            make_json_response(200, SUCCESS_BODY),
        ]
    )
    reg = _metrics()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, _ = await execute_with_retry(
            do_request,
            _render,
            breaker=CountingCircuitBreaker(),
            provider="openrouter",
            max_retries=1,
            backoff_base=0.5,
            metrics_registry=reg,
        )
    assert status == 200
    assert state["i"] == 2
    assert _counter(reg, provider="openrouter", reason="upstream_408", outcome="retried") == 1.0


# --------------------------------------------------------------------------- M5 deadline


async def test_cumulative_deadline_stops_attempts() -> None:
    do_request, state = _seq_do_request([make_json_response(503, {}) for _ in range(6)])
    reg = _metrics()
    # compute_backoff pinned high so (elapsed + delay) > deadline on the first retry decision
    with (
        patch("gateway.proxy.infrastructure.upstream_retry.compute_backoff", return_value=5.0),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        with pytest.raises(UpstreamUnavailableError):
            await execute_with_retry(
                do_request,
                _render,
                breaker=CountingCircuitBreaker(),
                provider="anthropic",
                max_retries=5,
                backoff_base=0.5,
                deadline_s=1.0,
                metrics_registry=reg,
            )
    assert state["i"] == 1  # only the initial attempt; deadline killed the rest
    assert (
        _counter(reg, provider="anthropic", reason="upstream_5xx", outcome="deadline_exceeded")
        == 1.0
    )
    assert _counter(reg, provider="anthropic", reason="upstream_5xx", outcome="retried") == 0.0


# --------------------------------------------------------------------------- M6 default-off


async def test_default_off_single_attempt_no_sleep() -> None:
    do_request, state = _seq_do_request([make_json_response(503, {})])
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleeper:
        with pytest.raises(UpstreamUnavailableError):
            await execute_with_retry(
                do_request,
                _render,
                breaker=CountingCircuitBreaker(),
                provider="openrouter",
                max_retries=0,
                backoff_base=0.5,
            )
    assert state["i"] == 1
    sleeper.assert_not_awaited()


# --------------------------------------------------------------------------- M8 terminal transport


async def test_read_timeout_trips_breaker_no_retry() -> None:
    do_request, state = _seq_do_request(
        [httpx.ReadTimeout("slow"), make_json_response(200, SUCCESS_BODY)]
    )
    breaker = CountingCircuitBreaker()
    with pytest.raises(UpstreamUnavailableError):
        await execute_with_retry(
            do_request,
            _render,
            breaker=breaker,
            provider="gemini",
            max_retries=3,
            backoff_base=0.5,
        )
    assert state["i"] == 1
    assert breaker.error_call_count == 1


# --------------------------------------------------------------------------- rejects


async def test_retries_exhausted() -> None:
    do_request, state = _seq_do_request([make_json_response(503, {}) for _ in range(4)])
    reg = _metrics()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(UpstreamUnavailableError):
            await execute_with_retry(
                do_request,
                _render,
                breaker=CountingCircuitBreaker(),
                provider="openrouter",
                max_retries=2,
                backoff_base=0.5,
                metrics_registry=reg,
            )
    assert state["i"] == 3  # 1 + 2 retries
    assert _counter(reg, provider="openrouter", reason="upstream_5xx", outcome="exhausted") == 1.0


async def test_breaker_open_aborts_loop() -> None:
    do_request, state = _seq_do_request(
        [make_json_response(503, {}), make_json_response(200, SUCCESS_BODY)]
    )
    breaker = CountingCircuitBreaker(failure_threshold=1)  # opens after 1 failure
    reg = _metrics()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(CircuitOpenError):
            await execute_with_retry(
                do_request,
                _render,
                breaker=breaker,
                provider="openrouter",
                max_retries=5,
                backoff_base=0.5,
                metrics_registry=reg,
            )
    assert state["i"] == 1  # second guard() raised before any further POST
    assert (
        _counter(reg, provider="openrouter", reason="upstream_5xx", outcome="breaker_open") == 1.0
    )


async def test_terminal_4xx_passthrough_records_success() -> None:
    do_request, state = _seq_do_request([make_json_response(400, {"error": "bad"})])
    breaker = CountingCircuitBreaker()
    status, body = await execute_with_retry(
        do_request,
        _render,
        breaker=breaker,
        provider="openrouter",
        max_retries=3,
        backoff_base=0.5,
    )
    assert status == 400
    assert body == {"error": "bad"}
    assert state["i"] == 1
    assert breaker.success_call_count == 1
    assert breaker.error_call_count == 0


# --------------------------------------------------------------------------- M9 provider label


async def test_provider_label_distinct_per_provider() -> None:
    reg = _metrics()
    for provider in ("openrouter", "anthropic", "gemini"):
        do_request, _ = _seq_do_request(
            [make_json_response(503, {}), make_json_response(200, SUCCESS_BODY)]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await execute_with_retry(
                do_request,
                _render,
                breaker=CountingCircuitBreaker(),
                provider=provider,
                max_retries=1,
                backoff_base=0.5,
                metrics_registry=reg,
            )
        assert _counter(reg, provider=provider, reason="upstream_5xx", outcome="retried") == 1.0


# --------------------------------------------------------------------------- M10 billing + secrets


async def test_served_attempt_body_only() -> None:
    served = {"id": "served", "choices": [], "usage": {"total_tokens": 9}}
    do_request, _ = _seq_do_request(
        [make_json_response(503, {"id": "discarded"}), make_json_response(200, served)]
    )
    with patch("asyncio.sleep", new_callable=AsyncMock):
        status, body = await execute_with_retry(
            do_request,
            _render,
            breaker=CountingCircuitBreaker(),
            provider="openrouter",
            max_retries=1,
            backoff_base=0.5,
        )
    assert (status, body) == (200, served)
    assert body["id"] != "discarded"


# --------------------------------------------------------------------------- config_invalid


def test_config_rejects_max_retries_above_bound() -> None:
    with pytest.raises(ValidationError):
        Settings(upstream_max_retries=9)


def test_config_rejects_negative_deadline() -> None:
    with pytest.raises(ValidationError):
        Settings(upstream_retry_deadline_s=-1.0)
