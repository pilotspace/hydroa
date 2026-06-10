"""Failing-first (RED) suite for lifespan migration and graceful drain.

Scenarios covered (ops-hardening TASK.md §2):
  - Scenario: lifespan replaces on_event — no deprecated handlers remain
  - Scenario: shutdown_drain_timeout_seconds is configurable with default 10
  - Scenario: graceful drain flushes all pending events before shutdown completes
  - Scenario: drain timeout — process still exits when drain cannot complete
  - Scenario: drain during Redis unavailability — bounded timeout, then exit

RED reason expected:
  - test_no_on_event_in_main: FAILS because @app.on_event still present in main.py
  - test_shutdown_drain_timeout_default / _override: FAILS because
    Settings has no shutdown_drain_timeout_seconds field yet
  - test_drain_*: FAILS because UsageLedgerFlusher has no drain_until_empty()
    method and the lifespan drain logic does not exist yet
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

# ── Settings must grow a new field ────────────────────────────────────────
from gateway.core.config import Settings

# ── Source path for structural assertion ──────────────────────────────────
MAIN_PY = (
    Path(__file__).resolve().parent.parent.parent
    / "src" / "gateway" / "main.py"
)

# ── Fakes ─────────────────────────────────────────────────────────────────


class FakeRedisStream:
    """In-memory Redis stream fake; supports xadd, xreadgroup, xack,
    xgroup_create, xpending, aclose, ping — enough for drain tests.

    All events are stored in self._stream as (entry_id, fields) pairs.
    The pending-entry-list (PEL) tracks un-acked entries.
    """

    def __init__(self) -> None:
        self._stream: list[tuple[str, dict[str, str]]] = []
        self._acked: set[str] = set()
        self._counter = 0
        self._group_exists = False
        self.closed = False

    # ── Redis Stream API stubs ─────────────────────────────────────────

    async def xgroup_create(
        self, key: str, group: str, id: str = "0", mkstream: bool = False
    ) -> None:
        if self._group_exists:
            raise Exception("BUSYGROUP Consumer Group name already exists")
        self._group_exists = True

    async def xadd(self, key: str, fields: dict[str, str]) -> bytes:
        self._counter += 1
        entry_id = f"{self._counter * 1000}-0"
        self._stream.append((entry_id, dict(fields)))
        return entry_id.encode()

    async def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict[str, str],
        count: int = 100,
        block: int = 0,
    ) -> list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]]:
        # Return all un-acked entries
        pending = [
            (entry_id, fields)
            for entry_id, fields in self._stream
            if entry_id not in self._acked
        ]
        if not pending:
            return []
        entries = [
            (entry_id.encode(), {k.encode(): v.encode() for k, v in fields.items()})
            for entry_id, fields in pending[:count]
        ]
        stream_key = list(streams.keys())[0]
        return [(stream_key.encode() if isinstance(stream_key, str) else stream_key, entries)]

    async def xack(self, key: str, group: str, *entry_ids: Any) -> int:
        for eid in entry_ids:
            eid_str = eid.decode("utf-8") if isinstance(eid, bytes) else eid
            self._acked.add(eid_str)
        return len(entry_ids)

    async def xpending(
        self,
        key: str,
        group: str,
        min_id: str = "-",
        max_id: str = "+",
        count: int = 1,
    ) -> list[Any]:
        """Return list of pending (un-acked) entry summaries."""
        return [
            (eid.encode(), b"flusher-0", 0, 1)
            for eid, _ in self._stream
            if eid not in self._acked
        ]

    async def incrbyfloat(self, key: str, value: float) -> float:
        return value

    async def aclose(self) -> None:
        self.closed = True

    async def ping(self) -> bool:
        return True


class BrokenRedis(FakeRedisStream):
    """Redis fake that raises ConnectionError on every xreadgroup call."""

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("Redis is down")

    async def ping(self) -> Any:
        raise ConnectionError("Redis is down")


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_fake_event_fields(n: int = 1) -> dict[str, str]:
    """Return minimal valid fields for a usage event (matching flusher expectations)."""
    import uuid
    return {
        "tenant_id": str(uuid.uuid4()),
        "key_id": str(uuid.uuid4()),
        "model_id": "openai/gpt-4o",
        "prompt_tokens": "10",
        "completion_tokens": "5",
        "cost_usd": "0.001",
        "pricing_snapshot_id": str(uuid.uuid4()),
        "status": "200",
        "raw": "{}",
        "created_at": "2026-06-11T00:00:00+00:00",
    }


# ══════════════════════════════════════════════════════════════════════════
# Structural: no @app.on_event in main.py
# ══════════════════════════════════════════════════════════════════════════


def test_no_on_event_in_main() -> None:
    """After build: main.py must not contain @app.on_event decorators.

    RED reason: @app.on_event is still present in main.py (lines 111, 131).
    """
    source = MAIN_PY.read_text()
    assert "@app.on_event" not in source, (
        "main.py still uses deprecated @app.on_event — "
        "must be replaced with a lifespan context manager"
    )
    # Positive assertion: lifespan must be wired
    assert "lifespan" in source, (
        "main.py does not mention 'lifespan' — "
        "the lifespan context manager is not yet wired"
    )


# ══════════════════════════════════════════════════════════════════════════
# Settings: new shutdown_drain_timeout_seconds field
# ══════════════════════════════════════════════════════════════════════════


def test_shutdown_drain_timeout_default() -> None:
    """Settings() must default shutdown_drain_timeout_seconds to 10.

    RED reason: Settings has no shutdown_drain_timeout_seconds attribute yet.
    """
    s = Settings()
    # This will raise AttributeError until the field is added
    assert s.shutdown_drain_timeout_seconds == 10  # type: ignore[attr-defined]


def test_shutdown_drain_timeout_override() -> None:
    """Settings with explicit value must honour it.

    RED reason: same as above — field does not exist yet.
    """
    s = Settings(shutdown_drain_timeout_seconds=30)  # type: ignore[call-arg]
    assert s.shutdown_drain_timeout_seconds == 30  # type: ignore[attr-defined]


# ══════════════════════════════════════════════════════════════════════════
# UsageLedgerFlusher: drain_until_empty must exist
# ══════════════════════════════════════════════════════════════════════════


async def test_flusher_has_drain_until_empty_method() -> None:
    """UsageLedgerFlusher must expose drain_until_empty(timeout=…).

    RED reason: the method does not exist on UsageLedgerFlusher yet.
    """
    from gateway.usage.application.flusher import UsageLedgerFlusher
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    fake_redis = FakeRedisStream()
    # We don't need a real session factory; the test just checks the method exists
    # and is callable without raising AttributeError
    flusher = UsageLedgerFlusher(
        redis=fake_redis,
        # Pass a dummy sessionmaker — drain_until_empty with 0 events won't call it
        session_factory=None,  # type: ignore[arg-type]
    )
    assert hasattr(flusher, "drain_until_empty"), (
        "UsageLedgerFlusher must have a drain_until_empty(timeout) method"
    )
    assert asyncio.iscoroutinefunction(flusher.drain_until_empty), (
        "drain_until_empty must be an async method"
    )


async def test_drain_empty_stream_completes_immediately() -> None:
    """drain_until_empty with an empty stream must return without blocking.

    RED reason: drain_until_empty does not exist yet.
    """
    from gateway.usage.application.flusher import UsageLedgerFlusher

    fake_redis = FakeRedisStream()
    flusher = UsageLedgerFlusher(redis=fake_redis, session_factory=None)  # type: ignore[arg-type]

    start = time.monotonic()
    await flusher.drain_until_empty(timeout=5.0)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"drain_until_empty on empty stream took {elapsed:.2f}s — expected < 1s"


async def test_drain_timeout_exits_cleanly() -> None:
    """drain_until_empty with timeout=0 must exit immediately even with pending events.

    RED reason: drain_until_empty does not exist yet.
    """
    from gateway.usage.application.flusher import UsageLedgerFlusher

    fake_redis = FakeRedisStream()
    # Pre-populate 3 events in the stream (never ACKed — they stay pending)
    for _ in range(3):
        await fake_redis.xadd("usage:events", _make_fake_event_fields())

    flusher = UsageLedgerFlusher(redis=fake_redis, session_factory=None)  # type: ignore[arg-type]

    start = time.monotonic()
    # timeout=0 → drain loop should skip immediately and log a warning
    await flusher.drain_until_empty(timeout=0.0)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, (
        f"drain_until_empty(timeout=0) with pending events took {elapsed:.2f}s — expected < 1s"
    )
    # Events must still be in the PEL (durable, not lost)
    assert len(fake_redis._stream) == 3, "Events must not be lost from the stream"
    assert len(fake_redis._acked) == 0, "Events must not be ACKed on a timeout=0 drain"


async def test_drain_redis_unavailable_exits_within_timeout() -> None:
    """drain_until_empty must exit within timeout even when Redis raises.

    RED reason: drain_until_empty does not exist yet.
    """
    from gateway.usage.application.flusher import UsageLedgerFlusher

    broken = BrokenRedis()
    flusher = UsageLedgerFlusher(redis=broken, session_factory=None)  # type: ignore[arg-type]

    timeout_s = 1.0
    start = time.monotonic()
    # Must not raise; must exit within timeout + reasonable buffer
    await flusher.drain_until_empty(timeout=timeout_s)
    elapsed = time.monotonic() - start

    assert elapsed < timeout_s + 1.5, (
        f"drain_until_empty with broken Redis took {elapsed:.2f}s "
        f"(timeout={timeout_s}s + 1.5s buffer exceeded)"
    )


# ══════════════════════════════════════════════════════════════════════════
# Zero event loss: integration test (real or fake Redis)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
async def test_drain_zero_event_loss_fake_redis() -> None:
    """All events written before shutdown must be durable after drain.

    Uses FakeRedisStream (no real Redis required for this variant).
    The drain test confirms the drain-loop logic; the integration variant
    (real Redis) provides crash-safe semantics validation.

    RED reason: drain_until_empty + lifespan not implemented yet.
    """
    from gateway.usage.application.flusher import UsageLedgerFlusher

    fake_redis = FakeRedisStream()
    event_count = 5

    # Pre-populate events — simulates events written during request handling
    for _ in range(event_count):
        await fake_redis.xadd("usage:events", _make_fake_event_fields())

    # We need a minimal session factory that captures INSERT calls (no real DB)
    inserted: list[dict[str, Any]] = []

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def begin(self) -> "FakeSession":
            return self

        async def execute(self, stmt: Any, params: Any) -> None:
            inserted.append(dict(params))

    class FakeSessionFactory:
        def __call__(self) -> FakeSession:
            return FakeSession()

    flusher = UsageLedgerFlusher(redis=fake_redis, session_factory=FakeSessionFactory())  # type: ignore[arg-type]

    # Drain with generous timeout
    await flusher.drain_until_empty(timeout=10.0)

    # All 5 events must be ACKed (moved to ledger)
    assert len(fake_redis._acked) == event_count, (
        f"Expected {event_count} ACKed events after drain, got {len(fake_redis._acked)}"
    )
