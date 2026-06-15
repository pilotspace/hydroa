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

from gateway.proxy.infrastructure.anthropic_upstream import AnthropicCompletionUpstream
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.gemini_upstream import GeminiCompletionUpstream
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream

FAKE_API_KEY = "sk-test-retry-policy"
FAKE_ANTHROPIC_KEY = "sk-ant-test-retry"
FAKE_GEMINI_KEY = "gm-test-retry"
COMPLETION_PATH = "/chat/completions"

SUCCESS_BODY = {
    "id": "gen-retry-1",
    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

PAYLOAD = {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hello"}]}

# --- Anthropic / Gemini provider-shaped fixtures (for the cross-provider retry tests) ---

ANTHROPIC_PAYLOAD = {
    "model": "claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "hello"}],
}
ANTHROPIC_SUCCESS_BODY = {
    "id": "msg_01ABC",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-sonnet-20241022",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 5, "output_tokens": 3},
}

GEMINI_PAYLOAD = {
    "model": "gemini-1.5-flash",
    "messages": [{"role": "user", "content": "hello"}],
}
GEMINI_SUCCESS_BODY = {
    "candidates": [
        {
            "content": {"parts": [{"text": "ok"}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 5,
        "candidatesTokenCount": 3,
        "totalTokenCount": 8,
    },
}


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
    retry_deadline_s: float = 0.0,
    metrics_registry: Any | None = None,
) -> OpenRouterCompletionUpstream:
    """Build an OpenRouterCompletionUpstream wired with a custom transport.

    Sets every attribute complete() reads — including the v19 retry-seam additions
    (retry_deadline_s, metrics_registry) so this helper stays valid after BUILD
    threads them into the unified executor.
    """
    upstream = OpenRouterCompletionUpstream.__new__(OpenRouterCompletionUpstream)
    upstream._api_key = FAKE_API_KEY
    upstream._breaker = breaker if breaker is not None else CountingCircuitBreaker()
    upstream._client = httpx.AsyncClient(
        base_url="https://openrouter.ai/api/v1",
        transport=transport,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
    )
    # Retry knobs — read by the unified executor (set as attributes for now)
    upstream._max_retries = max_retries
    upstream._backoff_base = backoff_base
    upstream._retry_deadline_s = retry_deadline_s
    upstream._metrics_registry = metrics_registry
    return upstream


def make_anthropic_upstream(
    transport: httpx.AsyncBaseTransport,
    breaker: CircuitBreaker | None = None,
    max_retries: int = 0,
    backoff_base: float = 0.5,
    retry_deadline_s: float = 0.0,
    metrics_registry: Any | None = None,
) -> AnthropicCompletionUpstream:
    """Build an AnthropicCompletionUpstream wired with a custom transport.

    Constructed via the real ctor (works pre- and post-BUILD), then the client is
    swapped and the retry knobs are set as attributes. The retry knobs are inert
    until BUILD wires complete() onto the unified executor — that is the red signal.
    """
    upstream = AnthropicCompletionUpstream(
        api_key=FAKE_ANTHROPIC_KEY,
        base_url="https://api.anthropic.com/v1",
        metrics_registry=metrics_registry,
    )
    upstream._client = httpx.AsyncClient(
        base_url="https://api.anthropic.com/v1",
        transport=transport,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
    )
    if breaker is not None:
        upstream._breaker = breaker
    upstream._max_retries = max_retries
    upstream._backoff_base = backoff_base
    upstream._retry_deadline_s = retry_deadline_s
    return upstream


def make_gemini_upstream(
    transport: httpx.AsyncBaseTransport,
    breaker: CircuitBreaker | None = None,
    max_retries: int = 0,
    backoff_base: float = 0.5,
    retry_deadline_s: float = 0.0,
    metrics_registry: Any | None = None,
) -> GeminiCompletionUpstream:
    """Build a GeminiCompletionUpstream wired with a custom transport (see make_anthropic_upstream)."""
    upstream = GeminiCompletionUpstream(
        api_key=FAKE_GEMINI_KEY,
        base_url="https://generativelanguage.googleapis.com/v1beta",
        metrics_registry=metrics_registry,
    )
    upstream._client = httpx.AsyncClient(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        transport=transport,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
    )
    if breaker is not None:
        upstream._breaker = breaker
    upstream._max_retries = max_retries
    upstream._backoff_base = backoff_base
    upstream._retry_deadline_s = retry_deadline_s
    return upstream
