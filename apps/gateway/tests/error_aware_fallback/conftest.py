"""Suite-local fixtures for error_aware_fallback tests (v19 task 2).

Self-contained (no DB, no Redis, no live server): fake upstreams + gates +
metrics helpers. Mirrors the model_fallbacks conftest style; kept independent so
this suite does not couple to a frozen sibling suite's fixtures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from prometheus_client import CollectorRegistry

from gateway.observability.metrics import MetricsRegistry

# ---------------------------------------------------------------------------
# Identifiers + bodies
# ---------------------------------------------------------------------------

CANDIDATE_A = "model-A"
CANDIDATE_B = "model-B"
ALIAS_FAST = "fast"

BODY_A: dict[str, Any] = {
    "id": "gen-A-1",
    "model": CANDIDATE_A,
    "choices": [{"message": {"role": "assistant", "content": "from A"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

BODY_B: dict[str, Any] = {
    "id": "gen-B-1",
    "model": CANDIDATE_B,
    "choices": [{"message": {"role": "assistant", "content": "from B"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
}

# 200 with a content-filter finish_reason — a SERVED response, NOT a fallover trigger.
BODY_A_CONTENT_FILTER_200: dict[str, Any] = {
    "id": "gen-A-cf",
    "model": CANDIDATE_A,
    "choices": [
        {"message": {"role": "assistant", "content": ""}, "finish_reason": "content_filter"}
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
}

# OpenAI-shaped error bodies (what every provider's complete() returns on a 4xx).
CTX_ERROR_BODY: dict[str, Any] = {
    "error": {
        "message": "This model's maximum context length is 8192 tokens.",
        "type": "invalid_request_error",
        "code": "context_length_exceeded",
    }
}
CONTENT_FILTER_BODY: dict[str, Any] = {
    "error": {
        "message": "The response was filtered due to the prompt triggering the content policy.",
        "type": "invalid_request_error",
        "code": "content_filter",
    }
}
AUTH_ERROR_BODY: dict[str, Any] = {
    "error": {
        "message": "Invalid API key",
        "type": "invalid_request_error",
        "code": "invalid_api_key",
    }
}


def make_payload(model: str = ALIAS_FAST) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


# ---------------------------------------------------------------------------
# Fake upstream — replays a sequence of (status, body) outcomes or raises
# ---------------------------------------------------------------------------


class SequencedFakeUpstream:
    """Replay a pre-built sequence of outcomes; record every payload sent.

    Each entry is one of:
      - (int, dict)  → returned as (status_code, json_body)
      - Exception instance / class → raised
    After the sequence is exhausted, raises RuntimeError (test-bug guard).
    """

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self._idx = 0
        self.calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def stream_call_count(self) -> int:
        return len(self.stream_calls)

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append(dict(payload))
        if self._idx >= len(self._outcomes):
            raise RuntimeError(
                f"SequencedFakeUpstream: ran out of outcomes after {self._idx} calls"
            )
        entry = self._outcomes[self._idx]
        self._idx += 1
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, type) and issubclass(entry, BaseException):
            raise entry("fake upstream error")
        status, body = entry
        return status, body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.stream_calls.append(dict(payload))

        async def _gen() -> AsyncIterator[bytes]:
            yield b"data: {}\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# Fake ModelHealthGate implementations
# ---------------------------------------------------------------------------


class AlwaysAvailableGate:
    """All candidates available; full ModelHealthGate protocol."""

    async def is_available(self, model_id: str, *, tenant_id: object) -> bool:
        return True

    async def record_failure(self, model_id: str, *, tenant_id: object) -> None:
        pass

    async def record_success(self, model_id: str, *, tenant_id: object) -> None:
        pass


class RecordingFakeGate:
    """ModelHealthGate stub recording every record_* call for assertion."""

    def __init__(self, unavailable: set[str] | None = None) -> None:
        self._unavailable: set[str] = unavailable or set()
        self.failure_calls: list[str] = []
        self.success_calls: list[str] = []

    async def is_available(self, model_id: str, *, tenant_id: object) -> bool:
        return model_id not in self._unavailable

    async def record_failure(self, model_id: str, *, tenant_id: object) -> None:
        self.failure_calls.append(model_id)

    async def record_success(self, model_id: str, *, tenant_id: object) -> None:
        self.success_calls.append(model_id)


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------


def make_metrics() -> MetricsRegistry:
    return MetricsRegistry(registry=CollectorRegistry())


def fallback_counter(
    reg: MetricsRegistry, *, alias: str, from_model: str, to_model: str, outcome: str
) -> float:
    """Read the current value of gateway_model_fallbacks_total for the given labels."""
    return reg.model_fallbacks_total.labels(  # type: ignore[no-any-return]
        alias=alias, from_model=from_model, to_model=to_model, outcome=outcome
    )._value.get()
