"""Suite-local fixtures for model_fallbacks tests.

Follows the retry_policy conftest style: fake upstreams, no DB, no Redis,
no live server.  All FallbackModelRouter instances are freshly constructed
per test.

Key fakes:
  - SequencedFakeUpstream   — replay a sequence of (status, body) outcomes or
                              raise an exception; records every call.
  - AlwaysAvailableGate     — ModelHealthGate stub: all candidates available.
  - SelectiveGate           — ModelHealthGate stub: caller declares unavailable set.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Response body constants
# ---------------------------------------------------------------------------

CANDIDATE_A = "model-A"
CANDIDATE_B = "model-B"
ALIAS_FAST = "fast"

BODY_A = {
    "id": "gen-A-1",
    "model": CANDIDATE_A,
    "choices": [{"message": {"role": "assistant", "content": "from A"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

BODY_B = {
    "id": "gen-B-1",
    "model": CANDIDATE_B,
    "choices": [{"message": {"role": "assistant", "content": "from B"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
}

BODY_4XX = {"error": {"code": "invalid_request", "message": "bad input"}}

BASE_PAYLOAD: dict[str, Any] = {
    "model": ALIAS_FAST,
    "messages": [{"role": "user", "content": "hello"}],
}


# ---------------------------------------------------------------------------
# Fake upstream
# ---------------------------------------------------------------------------


class SequencedFakeUpstream:
    """Replay a pre-built sequence of outcomes; record every payload sent.

    Each entry in `outcomes` is one of:
      - (int, dict)  → returned as (status_code, json_body)
      - Exception instance or class → raised
    After the sequence is exhausted, raises RuntimeError (test bug guard).
    """

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self._idx = 0
        self.calls: list[dict[str, Any]] = []  # payload snapshots per call
        self.stream_calls: list[dict[str, Any]] = []  # stream() payload snapshots

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def stream_call_count(self) -> int:
        return len(self.stream_calls)

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        # Snapshot the payload (deep copy not needed — we only inspect model field)
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
        # entry is (status, body)
        status, body = entry
        return status, body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Record the call and raise UpstreamUnavailableError to simulate stream failure."""
        from gateway.proxy.domain.errors import UpstreamUnavailableError

        self.stream_calls.append(dict(payload))

        async def _gen() -> AsyncIterator[bytes]:
            raise UpstreamUnavailableError("fake stream error")
            yield b""  # unreachable — keeps type checker happy

        return _gen()


# ---------------------------------------------------------------------------
# Fake ModelHealthGate implementations
# ---------------------------------------------------------------------------


class AlwaysAvailableGate:
    """All candidates are available — no cooldowns. Implements full ModelHealthGate protocol."""

    async def is_available(self, model_id: str) -> bool:
        return True

    async def record_failure(self, model_id: str) -> None:
        pass

    async def record_success(self, model_id: str) -> None:
        pass


class SelectiveGate:
    """Caller declares which model ids are unavailable (cooled). Implements full ModelHealthGate protocol."""

    def __init__(self, unavailable: set[str]) -> None:
        self._unavailable = unavailable

    async def is_available(self, model_id: str) -> bool:
        return model_id not in self._unavailable

    async def record_failure(self, model_id: str) -> None:
        pass

    async def record_success(self, model_id: str) -> None:
        pass


class RecordingFakeGate:
    """ModelHealthGate stub that records every recording call for assertion (A2).

    Optionally marks specific model ids as unavailable (same as SelectiveGate).
    Tracks record_failure and record_success calls per model id.
    """

    def __init__(self, unavailable: set[str] | None = None) -> None:
        self._unavailable: set[str] = unavailable or set()
        self.failure_calls: list[str] = []  # model ids passed to record_failure
        self.success_calls: list[str] = []  # model ids passed to record_success

    async def is_available(self, model_id: str) -> bool:
        return model_id not in self._unavailable

    async def record_failure(self, model_id: str) -> None:
        self.failure_calls.append(model_id)

    async def record_success(self, model_id: str) -> None:
        self.success_calls.append(model_id)


# ---------------------------------------------------------------------------
# Fake usage recorder (tracks calls for billing assertions)
# ---------------------------------------------------------------------------


class FakeUsageRecorder:
    """Minimal UsageRecorder that records every call for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))

    @property
    def call_count(self) -> int:
        return len(self.calls)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_payload(model: str = ALIAS_FAST) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}
