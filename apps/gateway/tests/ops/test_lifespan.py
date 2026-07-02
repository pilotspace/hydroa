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
        # PEL: delivered-but-unacked entries (entry_id -> {"fields", "idle_ms"}).
        # An entry sits here between XREADGROUP delivery and XACK; a crash in that
        # window strands it until XAUTOCLAIM reclaims it.
        self._pel: dict[str, dict[str, Any]] = {}
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
        # '>' returns only NEVER-DELIVERED entries (not acked, not already in the
        # PEL). Delivering them registers them in the PEL (idle 0) until XACK —
        # so a re-read never re-returns them; only XAUTOCLAIM can reclaim a strand.
        undelivered = [
            (entry_id, fields)
            for entry_id, fields in self._stream
            if entry_id not in self._acked and entry_id not in self._pel
        ]
        if not undelivered:
            return []
        selected = undelivered[:count]
        for entry_id, fields in selected:
            self._pel[entry_id] = {"fields": dict(fields), "idle_ms": 0}
        entries = [
            (entry_id.encode(), {k.encode(): v.encode() for k, v in fields.items()})
            for entry_id, fields in selected
        ]
        stream_key = list(streams.keys())[0]
        return [(stream_key.encode() if isinstance(stream_key, str) else stream_key, entries)]

    async def xack(self, key: str, group: str, *entry_ids: Any) -> int:
        for eid in entry_ids:
            eid_str = eid.decode("utf-8") if isinstance(eid, bytes) else eid
            self._acked.add(eid_str)
            self._pel.pop(eid_str, None)
        return len(entry_ids)

    async def xpending(self, key: str, group: str) -> dict[str, Any]:
        """Summary form — matches redis-py's dict shape (the flusher's call site).

        {'pending': N, 'min': <id>, 'max': <id>, 'consumers': [...]}
        """
        unacked = [eid for eid, _ in self._stream if eid not in self._acked]
        return {
            "pending": len(unacked),
            "min": unacked[0].encode() if unacked else None,
            "max": unacked[-1].encode() if unacked else None,
            "consumers": [{"name": b"flusher-0", "pending": len(unacked)}] if unacked else [],
        }

    async def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str = "0-0",
        count: int | None = None,
        justid: bool = False,
    ) -> tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]], list[bytes]]:
        """Reclaim PEL entries idle >= min_idle_time to `consumername`.

        Return shape matches redis-py 7.x: [next_cursor, [(id, {fields}), ...], [deleted]].
        Reclaimed entries stay in the PEL (idle reset to 0) until the caller ACKs them.
        """
        claimed: list[tuple[bytes, dict[bytes, bytes]]] = []
        for entry_id, meta in self._pel.items():
            if entry_id in self._acked:
                continue
            if meta["idle_ms"] >= min_idle_time:
                fields = meta["fields"]
                claimed.append(
                    (entry_id.encode(), {k.encode(): v.encode() for k, v in fields.items()})
                )
                meta["idle_ms"] = 0  # reclaim resets idle (now owned by this consumer)
        return (b"0-0", claimed, [])

    def seed_pel(self, fields: dict[str, str], *, idle_ms: int) -> str:
        """Seed a delivered-but-unacked (PEL) entry with a given idle age — models a
        consumer that crashed after XREADGROUP but before XACK. Returns the entry id."""
        self._counter += 1
        entry_id = f"{self._counter * 1000}-0"
        self._stream.append((entry_id, dict(fields)))
        self._pel[entry_id] = {"fields": dict(fields), "idle_ms": idle_ms}
        return entry_id

    async def incrbyfloat(self, key: str, value: float) -> float:
        return value

    async def aclose(self) -> None:
        self.closed = True

    async def ping(self) -> bool:
        return True


class BrokenRedis(FakeRedisStream):
    """Redis fake that raises ConnectionError on every read/reclaim call."""

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("Redis is down")

    async def xautoclaim(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("Redis is down")

    async def ping(self) -> Any:
        raise ConnectionError("Redis is down")


class _CapturingSession:
    """Minimal async session that records INSERT params to a shared sink."""

    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self._sink = sink

    async def __aenter__(self) -> "_CapturingSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def begin(self) -> "_CapturingSession":
        return self

    async def execute(self, stmt: Any, params: Any) -> None:
        self._sink.append(dict(params))


class _CapturingSessionFactory:
    """Session factory whose sessions capture INSERT params (no real DB)."""

    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    def __call__(self) -> _CapturingSession:
        return _CapturingSession(self.inserted)


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


# ══════════════════════════════════════════════════════════════════════════
# usage-flusher-durability (B5) — XAUTOCLAIM reclaims crash-stranded PEL entries
# ══════════════════════════════════════════════════════════════════════════
# Default reclaim idle is 60_000 ms (GATEWAY_USAGE_PEL_RECLAIM_IDLE_MS); the
# flusher defaults to it when constructed without an override.
_DEFAULT_RECLAIM_IDLE_MS = 60_000


async def test_stranded_pel_entry_reclaimed() -> None:
    """B5: a PEL entry idle > threshold (consumer crashed before XACK) is reclaimed
    by XAUTOCLAIM on the next flush_once, INSERTed, and ACKed.

    RED reason: flush_once() does not run XAUTOCLAIM yet, so a PEL entry (never
    re-delivered by '>') is never reclaimed → stays un-ACKed forever.
    """
    from gateway.usage.application.flusher import UsageLedgerFlusher

    fake = FakeRedisStream()
    sf = _CapturingSessionFactory()
    eid = fake.seed_pel(_make_fake_event_fields(), idle_ms=_DEFAULT_RECLAIM_IDLE_MS + 60_000)

    flusher = UsageLedgerFlusher(redis=fake, session_factory=sf)  # type: ignore[arg-type]
    await flusher.flush_once()

    assert eid in fake._acked, "a stranded PEL entry (idle > threshold) must be reclaimed + ACKed"
    assert len(sf.inserted) == 1, "the reclaimed entry must be INSERTed into the ledger"


async def test_inflight_entry_not_stolen() -> None:
    """B5: XAUTOCLAIM respects min_idle_time — it reclaims a stale strand but leaves
    a freshly in-flight entry (idle < threshold) untouched.

    RED reason: without XAUTOCLAIM the stale entry is never reclaimed, so the
    'stale IS reclaimed' half of the discrimination fails.
    """
    from gateway.usage.application.flusher import UsageLedgerFlusher

    fake = FakeRedisStream()
    sf = _CapturingSessionFactory()
    stale = fake.seed_pel(_make_fake_event_fields(), idle_ms=_DEFAULT_RECLAIM_IDLE_MS + 60_000)
    fresh = fake.seed_pel(_make_fake_event_fields(), idle_ms=5_000)  # < threshold

    flusher = UsageLedgerFlusher(redis=fake, session_factory=sf)  # type: ignore[arg-type]
    await flusher.flush_once()

    assert stale in fake._acked, "the stale strand must be reclaimed"
    assert fresh not in fake._acked, "a fresh in-flight entry (idle < threshold) must NOT be stolen"
    assert len(sf.inserted) == 1, "only the stale strand should have been flushed"


async def test_drain_clears_preexisting_pel() -> None:
    """B5: drain_until_empty reclaims a pre-existing PEL regardless of idle age
    (shutdown reclaims aggressively, min_idle=0) and completes within the timeout.

    RED reason: without XAUTOCLAIM the pre-existing PEL entry is never flushed, so
    drain loops until it times out (and the entry is never ACKed).
    """
    from gateway.usage.application.flusher import UsageLedgerFlusher

    fake = FakeRedisStream()
    sf = _CapturingSessionFactory()
    # A FRESH strand (idle 0): only an idle-agnostic drain reclaim can clear it.
    eid = fake.seed_pel(_make_fake_event_fields(), idle_ms=0)

    flusher = UsageLedgerFlusher(redis=fake, session_factory=sf)  # type: ignore[arg-type]
    start = time.monotonic()
    await flusher.drain_until_empty(timeout=3.0)
    elapsed = time.monotonic() - start

    assert eid in fake._acked, "drain must reclaim + flush a pre-existing PEL entry"
    assert len(sf.inserted) == 1, "the pre-existing PEL entry must be INSERTed"
    assert elapsed < 3.0, "drain must clear the PEL well within the timeout (not loop to timeout)"


# ── B5 regression: Finding A (poison entry must not starve its reclaim batch) ──


class _FailFirstSession:
    """Async session whose FIRST execute raises a (transient) DB error, then succeeds."""

    def __init__(self, factory: "_FailFirstSessionFactory") -> None:
        self._factory = factory

    async def __aenter__(self) -> "_FailFirstSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def begin(self) -> "_FailFirstSession":
        return self

    async def execute(self, stmt: Any, params: Any) -> None:
        self._factory.calls += 1
        if self._factory.calls == 1:
            raise ConnectionError("transient DB error")  # NOT a parse error → retryable
        self._factory.inserted.append(dict(params))


class _FailFirstSessionFactory:
    """Session factory whose first INSERT raises (transient), the rest succeed."""

    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []
        self.calls = 0

    def __call__(self) -> _FailFirstSession:
        return _FailFirstSession(self)


async def test_poison_reclaim_entry_does_not_starve_batch() -> None:
    """B5 regression (Finding A): a poison PEL entry (an unparseable REQUIRED field)
    claimed BEFORE a good entry in the same XAUTOCLAIM batch must NOT starve the good one.

    The poison entry is a deterministic parse failure → DROPPED (ACKed, so the PEL can
    drain); the good entry (ordered after it) is still INSERTed + ACKed. Without a
    broadened poison classification + per-entry resilience, the bare int() parse failure
    propagates out of the batch loop and aborts it — and because XAUTOCLAIM re-claims the
    poison oldest-first every cycle, the good entry starves forever (silent lost-bill).

    RED reason (pre-fix): int('not-a-number') raises a bare ValueError (NOT
    MalformedUsageEventError), which propagates out of the batch loop → the good entry is
    never processed (not inserted, not acked) and the poison is never acked.
    """
    from gateway.usage.application.flusher import UsageLedgerFlusher

    fake = FakeRedisStream()
    sf = _CapturingSessionFactory()
    # Poison seeded FIRST → oldest id → XAUTOCLAIM returns it first in the batch.
    poison_fields = _make_fake_event_fields()
    poison_fields["prompt_tokens"] = "not-a-number"  # deterministic int() parse failure
    poison = fake.seed_pel(poison_fields, idle_ms=_DEFAULT_RECLAIM_IDLE_MS + 60_000)
    good = fake.seed_pel(_make_fake_event_fields(), idle_ms=_DEFAULT_RECLAIM_IDLE_MS + 60_000)

    flusher = UsageLedgerFlusher(redis=fake, session_factory=sf)  # type: ignore[arg-type]
    await flusher.flush_once()

    assert good in fake._acked, (
        "the good entry, claimed AFTER the poison in the same batch, must still be "
        "flushed + ACKed — a poison entry must not starve its batch"
    )
    assert len(sf.inserted) == 1, "exactly the good entry should have been INSERTed"
    assert poison in fake._acked, (
        "a deterministically-unparseable entry must be dropped (ACKed), not retried forever"
    )


async def test_transient_db_error_does_not_abort_reclaim_batch() -> None:
    """B5 regression (Finding A, per-entry resilience): a transient (retryable) DB error
    on one reclaimed entry must NOT abort the rest of the batch.

    The failing entry is NOT acked (it is retryable → stays in the PEL for the next
    cycle); the sibling entry claimed after it is still processed + ACKed.

    RED reason (pre-fix): the ConnectionError from the first insert propagates out of the
    single batch-level try/except → the second entry is never processed (not acked).
    """
    from gateway.usage.application.flusher import UsageLedgerFlusher

    fake = FakeRedisStream()
    sf = _FailFirstSessionFactory()
    first = fake.seed_pel(_make_fake_event_fields(), idle_ms=_DEFAULT_RECLAIM_IDLE_MS + 60_000)
    second = fake.seed_pel(_make_fake_event_fields(), idle_ms=_DEFAULT_RECLAIM_IDLE_MS + 60_000)

    flusher = UsageLedgerFlusher(redis=fake, session_factory=sf)  # type: ignore[arg-type]
    await flusher.flush_once()

    assert second in fake._acked, (
        "a transient DB error on the first entry must not abort the batch — "
        "the second entry must still be flushed + ACKed"
    )
    assert first not in fake._acked, (
        "the entry that hit a retryable DB error must NOT be acked (it stays in the PEL)"
    )
