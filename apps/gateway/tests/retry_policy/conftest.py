"""Suite-local fixtures for retry_policy tests.

Uses httpx.MockTransport exclusively — no DB, no live server.
All circuit breaker instances are freshly constructed per test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream

FAKE_API_KEY = "sk-test-retry-policy"
COMPLETION_PATH = "/chat/completions"

SUCCESS_BODY = {
    "id": "gen-retry-1",
    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

PAYLOAD = {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hello"}]}


class CountingCircuitBreaker(CircuitBreaker):
    """CircuitBreaker subclass that counts method calls for assertion."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.error_call_count: int = 0
        self.success_call_count: int = 0

    def on_upstream_error(self) -> None:
        self.error_call_count += 1
        super().on_upstream_error()

    def record_success(self) -> None:
        self.success_call_count += 1
        super().record_success()


class SequencedMockTransport(httpx.AsyncBaseTransport):
    """Replay a pre-built sequence of responses; record every request made.

    Each entry in `responses` is one of:
      - httpx.Response object → returned as-is
      - an Exception class or instance → raised
    After the sequence is exhausted, raises StopAsyncIteration (test bug).
    """

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.requests: list[httpx.Request] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._idx >= len(self._responses):
            raise RuntimeError(
                f"SequencedMockTransport: ran out of responses after {self._idx} calls"
            )
        entry = self._responses[self._idx]
        self._idx += 1
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, type) and issubclass(entry, BaseException):
            raise entry("mock transport error")
        return entry


def make_json_response(status: int, body: dict[str, Any] | None = None) -> httpx.Response:
    """Build a minimal httpx.Response with JSON body."""
    import json

    content = json.dumps(body or {}).encode()
    return httpx.Response(
        status_code=status,
        headers={"content-type": "application/json"},
        content=content,
    )


def make_upstream(
    transport: httpx.AsyncBaseTransport,
    breaker: CircuitBreaker | None = None,
    max_retries: int = 0,
    backoff_base: float = 0.5,
) -> OpenRouterCompletionUpstream:
    """Build an OpenRouterCompletionUpstream wired with a custom transport.

    The upstream does NOT yet have retry logic (source unchanged for red phase);
    we pass the knobs so tests assert the DESIRED behavior that BUILD will implement.
    """
    upstream = OpenRouterCompletionUpstream.__new__(OpenRouterCompletionUpstream)
    upstream._api_key = FAKE_API_KEY
    upstream._breaker = breaker if breaker is not None else CountingCircuitBreaker()
    upstream._client = httpx.AsyncClient(
        base_url="https://openrouter.ai/api/v1",
        transport=transport,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
    )
    # Retry knobs — will be read by the new retry loop (set as attributes for now)
    upstream._max_retries = max_retries
    upstream._backoff_base = backoff_base
    return upstream
