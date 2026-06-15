"""Suite-local fixtures for streaming_resilience tests (v19 task 3).

Self-contained (no DB, no Redis, no live server): a plan-driven streaming fake
upstream so the pre-first-byte fallover / commit boundary can be unit-tested fast.

Plan grammar (per model id): a list whose items are either
  - bytes              -> yielded as an SSE chunk
  - an Exception inst  -> raised at that point in the stream
A plan of [exc] raises on the FIRST __anext__ (pre-first-byte). A plan of
[c0, exc] yields c0 then raises MID-stream (the commit boundary). A plan of []
ends immediately (empty upstream stream).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from gateway.keys.domain.entities import AuthzResult
from gateway.observability.metrics import MetricsRegistry
from prometheus_client import CollectorRegistry

# --- identities + aliases ---------------------------------------------------
TENANT_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
KEY_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

CAND_A = "model-A"
CAND_B = "model-B"
CAND_C = "model-C"
ALIAS = "fast"
PLAIN = "plain-model"

# --- SSE chunk constants ----------------------------------------------------
A0 = b'data: {"id":"A","choices":[{"delta":{"content":"a0"}}]}\n\n'
A1 = b'data: {"id":"A","choices":[{"delta":{"content":"a1"}}]}\n\n'
B0 = b'data: {"id":"B","choices":[{"delta":{"content":"b0"}}]}\n\n'
B1 = b'data: {"id":"B","choices":[{"delta":{"content":"b1"}}]}\n\n'
USAGE_B = b'data: {"usage":{"prompt_tokens":5,"completion_tokens":4,"total_tokens":42}}\n\n'
DONE = b"data: [DONE]\n\n"


def make_payload(model: str = ALIAS) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


class PlanStreamUpstream:
    """Fake CompletionUpstream driven by a per-model plan (see module docstring)."""

    def __init__(self, plans: dict[str, list[Any]]) -> None:
        self._plans = plans
        self.stream_calls: list[str] = []  # model ids passed to stream()
        self.complete_calls: list[str] = []

    @property
    def stream_call_count(self) -> int:
        return len(self.stream_calls)

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        model = str(payload.get("model", ""))
        self.stream_calls.append(model)
        plan = list(self._plans.get(model, []))

        async def _gen() -> AsyncIterator[bytes]:
            for item in plan:
                if isinstance(item, BaseException):
                    raise item
                if isinstance(item, type) and issubclass(item, BaseException):
                    raise item("planned stream error")
                yield item

        return _gen()

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.complete_calls.append(str(payload.get("model", "")))
        return 200, {}


class SyncCircuitUpstream:
    """Fake upstream whose stream() raises CircuitOpenError SYNCHRONOUSLY for models in
    `open_models` (mirrors BoundCircuitBreakerUpstream + provider guard() — circuit-open
    raises at the stream() CALL, not on __anext__). Other models serve their plan.
    """

    def __init__(self, *, open_models: set[str], plans: dict[str, list[Any]]) -> None:
        from gateway.proxy.domain.errors import CircuitOpenError

        self._open = set(open_models)
        self._plans = plans
        self._circuit_open = CircuitOpenError
        self.stream_calls: list[str] = []

    @property
    def stream_call_count(self) -> int:
        return len(self.stream_calls)

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        model = str(payload.get("model", ""))
        self.stream_calls.append(model)
        if model in self._open:
            raise self._circuit_open("circuit open (synchronous)")
        plan = list(self._plans.get(model, []))

        async def _gen() -> AsyncIterator[bytes]:
            for item in plan:
                if isinstance(item, BaseException):
                    raise item
                yield item

        return _gen()

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {}


class FakeAuthenticator:
    """KeyAuthenticator stub: returns a minimal AuthzResult (governance all-clear)."""

    def __init__(self, *, tenant_id: uuid.UUID = TENANT_A) -> None:
        self._tenant_id = tenant_id

    async def authenticate(self, raw_key: str) -> AuthzResult:
        return AuthzResult(tenant_id=self._tenant_id, key_id=KEY_ID)


class FakeModelChecker:
    """ModelChecker stub: every model is active (is_active → True; no check_for_tenant)."""

    async def is_active(self, model_id: str) -> bool:
        return True


class FakeUsageRecorder:
    """UsageRecorder stub recording every record() call for billing assertions."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))

    @property
    def call_count(self) -> int:
        return len(self.calls)


def make_metrics() -> MetricsRegistry:
    return MetricsRegistry(registry=CollectorRegistry())


def fallover_counter(
    reg: MetricsRegistry, *, alias: str, from_model: str, to_model: str, outcome: str
) -> float:
    """Read gateway_model_fallbacks_total for the given labels (0.0 if never incremented)."""
    return reg.model_fallbacks_total.labels(  # type: ignore[no-any-return]
        alias=alias, from_model=from_model, to_model=to_model, outcome=outcome
    )._value.get()
