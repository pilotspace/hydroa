"""Suite-local fixtures for gpt-realtime-relay-billing tests (TASK.md §4).

Mirrors tests/tiered_token_billing/conftest.py but extends FakeSession's fake
`pricing_snapshots` row to the 11-tuple the FROZEN §3 contract pins for
`_fetch_latest_pricing`: the 8 pre-existing positions plus 3 new trailing
audio prices (audio_prompt, audio_completion, audio_cached).

Before BUILD: the live `_fetch_latest_pricing` returns only an 8-tuple, so the
audio-tier assertions here go RED for the right reason (missing audio pricing),
not a broken harness.

Also provides a FakeWebSocket (mirrors tests/realtime_relay/test_openai_adapter.py)
for driving OpenAIRealtimeSession.events() without network, and a SpyRecorder that
captures full record() call kwargs (not just a count) for identity/wiring assertions.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake DB primitives (extended to the 11-tuple pricing row)
# ---------------------------------------------------------------------------


class FakeRow:
    """Minimal row proxy returned by FakeResult.fetchone()."""

    def __init__(self, values: tuple[Any, ...]) -> None:
        self._values = values

    def __getitem__(self, idx: int) -> Any:
        return self._values[idx]


class FakeResult:
    """Minimal query result returned by FakeSession.execute()."""

    def __init__(self, row: FakeRow | None) -> None:
        self._row = row

    def fetchone(self) -> FakeRow | None:
        return self._row


class FakeSession:
    """In-memory async session intercepting `_fetch_latest_pricing` +
    `_fetch_markup_pct`. Returns an 11-value pricing row:

      (id, prompt_price, completion_price, pricing_unit, unit_usd_per_unit,
       cached_input_price, reasoning_price, cache_creation_price,
       audio_prompt_price, audio_completion_price, audio_cached_price)

    The 3 audio prices (gpt-realtime-relay-billing TASK.md §3) are added at
    positions [8], [9], [10]; existing callers that don't set them get None
    (byte-identical — the flat/no-audio cost path never reads them).
    """

    def __init__(
        self,
        *,
        snapshot_id: uuid.UUID | None = None,
        prompt_price: Decimal = Decimal("0"),
        completion_price: Decimal = Decimal("0"),
        pricing_unit: str = "per_token",
        unit_usd_per_unit: Decimal | None = None,
        cached_input_price: Decimal | None = None,
        reasoning_price: Decimal | None = None,
        cache_creation_price: Decimal | None = None,
        audio_prompt_price: Decimal | None = None,
        audio_completion_price: Decimal | None = None,
        audio_cached_price: Decimal | None = None,
        markup_pct: Decimal = Decimal("0"),
        has_pricing: bool = True,
    ) -> None:
        self.snapshot_id = snapshot_id or uuid.uuid4()
        self.prompt_price = prompt_price
        self.completion_price = completion_price
        self.pricing_unit = pricing_unit
        self.unit_usd_per_unit = unit_usd_per_unit
        self.cached_input_price = cached_input_price
        self.reasoning_price = reasoning_price
        self.cache_creation_price = cache_creation_price
        self.audio_prompt_price = audio_prompt_price
        self.audio_completion_price = audio_completion_price
        self.audio_cached_price = audio_cached_price
        self.markup_pct = markup_pct
        self.has_pricing = has_pricing

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        sql = str(stmt)
        if "pricing_snapshots" in sql:
            if not self.has_pricing:
                return FakeResult(None)

            def _s(v: Decimal | None) -> str | None:
                return str(v) if v is not None else None

            return FakeResult(
                FakeRow(
                    (
                        str(self.snapshot_id),
                        str(self.prompt_price),
                        str(self.completion_price),
                        self.pricing_unit,
                        _s(self.unit_usd_per_unit),
                        _s(self.cached_input_price),
                        _s(self.reasoning_price),
                        _s(self.cache_creation_price),
                        _s(self.audio_prompt_price),
                        _s(self.audio_completion_price),
                        _s(self.audio_cached_price),
                    )
                )
            )
        if "tenants" in sql:
            return FakeResult(FakeRow((str(self.markup_pct),)))
        return FakeResult(None)

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeSessionFactory:
    """Async sessionmaker-compatible factory wrapping a FakeSession."""

    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def __call__(self) -> FakeSession:
        return self._session


# ---------------------------------------------------------------------------
# Fake Redis stream sink
# ---------------------------------------------------------------------------


class StreamCapture:
    """Minimal async Redis fake capturing xadd() fields + incrbyfloat() calls."""

    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []
        self.incr_calls: list[tuple[str, float]] = []

    async def xadd(self, stream_key: str, fields: dict[str, str]) -> bytes:
        self.events.append(dict(fields))
        return b"1234567890123-0"

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.incr_calls.append((key, amount))
        return amount

    @property
    def last_event(self) -> dict[str, str]:
        assert self.events, "No events captured — recorder was not called"
        return self.events[-1]


# ---------------------------------------------------------------------------
# Spy recorder — captures full record() call kwargs (identity/wiring tests)
# ---------------------------------------------------------------------------


class SpyRecorder:
    """Minimal UsageRecorder-compatible spy that captures every record() call's kwargs."""

    supported_extras: frozenset[str] = frozenset(
        {"usage_source", "pricing_unit", "quantity", "provider_generation_id"}
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    @property
    def last_call(self) -> dict[str, Any]:
        assert self.calls, "No record() calls captured"
        return self.calls[-1]


# ---------------------------------------------------------------------------
# Fake OpenAI Realtime WebSocket (mirrors tests/realtime_relay/test_openai_adapter.py)
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """A scripted RealtimeWebSocket: recv() drains `incoming`, send() records."""

    def __init__(self, incoming: list[str] | None = None) -> None:
        self._incoming = list(incoming or [])
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if not self._incoming:
            raise ConnectionError("socket closed")  # provider stream ended
        return self._incoming.pop(0)

    async def aclose(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshot_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def key_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def stream_capture() -> StreamCapture:
    return StreamCapture()
