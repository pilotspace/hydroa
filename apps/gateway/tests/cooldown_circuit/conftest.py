"""Suite-local fixtures for cooldown_circuit tests.

Uses a fully-controlled in-memory fake async Redis — no live Redis dependency.
The fake tracks:
  - A dict store mapping key → (value, expires_at | None)
  - A command log recording every command name issued
  - An injectable monotonic clock (float → float callable)
  - NX and EX semantics matching redis.asyncio behavior exactly

Design notes:
  - asyncio is single-threaded, so "concurrent" tests use asyncio.gather which
    interleaves tasks at each `await`. The fake's operations are synchronous
    within each await boundary, so SET NX is atomic from the test's perspective:
    two tasks in asyncio.gather will each execute their SET NX call fully before
    yielding, giving us deterministic NX behavior.
  - The fake raises ConnectionError when constructed with raise_on_command=True.
  - TTL bookkeeping: store expiry as a monotonic timestamp; the fake clock
    starts at 0.0 and can be advanced via fake.advance_clock(seconds).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake async Redis
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory async Redis fake with TTL bookkeeping and command logging.

    Supports: GET, SET (EX, NX), INCR, EXPIRE (NX), DEL, EXISTS.
    Does NOT support: Lua scripts, pipelines, pub/sub, streams.
    """

    def __init__(
        self,
        *,
        raise_on_command: bool = False,
        clock: Callable[[], float] | None = None,
    ) -> None:
        # key → (value: str, expires_at: float | None)
        self._store: dict[str, tuple[str, float | None]] = {}
        self.command_log: list[str] = []
        self._raise = raise_on_command
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic

    # ── internal helpers ────────────────────────────────────────────────────

    def _now(self) -> float:
        return self._clock()

    def _is_expired(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return True
        _, exp = entry
        if exp is not None and exp <= self._now():
            del self._store[key]
            return True
        return False

    def _get_live(self, key: str) -> str | None:
        if self._is_expired(key):
            return None
        entry = self._store.get(key)
        return entry[0] if entry is not None else None

    def _log(self, cmd: str) -> None:
        self.command_log.append(cmd)

    def _maybe_raise(self) -> None:
        if self._raise:
            raise ConnectionError("FakeRedis: simulated connection error")

    def advance_clock(self, seconds: float) -> None:
        """Advance the fake's internal clock by `seconds` (simulates TTL expiry)."""
        current = self._clock()
        new_time = current + seconds
        self._clock = lambda: new_time  # type: ignore[assignment]

    def set_clock(self, t: float) -> None:
        """Set the fake's internal clock to an absolute value."""
        self._clock = lambda: t  # type: ignore[assignment]

    # ── redis.asyncio-compatible async interface ─────────────────────────────

    async def set(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
        nx: bool = False,
        **kwargs: Any,
    ) -> bool | None:
        """SET [EX seconds] [NX].

        Returns True if SET was performed, None if NX condition prevented it.
        (Mirrors redis.asyncio return: True on success, None on NX failure.)
        """
        self._log("SET")
        self._maybe_raise()
        existing = self._get_live(key)
        if nx and existing is not None:
            return None  # NX: key already exists, do not set
        expires_at = (self._now() + ex) if ex is not None else None
        self._store[key] = (str(value), expires_at)
        return True

    async def get(self, key: str) -> str | None:
        self._log("GET")
        self._maybe_raise()
        return self._get_live(key)

    async def scan(
        self, cursor: int = 0, match: str | None = None, count: int | None = None
    ) -> tuple[int, list[str]]:
        """Single-shot SCAN over live keys (the cursor always returns to 0).

        tenant-scoped-breaker-cooldown (R9 P0 #3): `snapshot_state(tenant_id=None)`
        — the superadmin routing board's cross-partition aggregate — walks the
        keyspace with SCAN. `match` is only ever the fixed key prefix plus "*"
        (the gate never puts caller-supplied data in the pattern), so a prefix
        compare is a faithful stand-in for Redis glob here. Mirrors the same
        method on tests/routing_admin/conftest.py's fake so there is one
        behaviour, not two.
        """
        self._log("SCAN")
        del count
        self._maybe_raise()
        keys = [k for k in list(self._store) if self._get_live(k) is not None]
        if match is not None:
            prefix = match[:-1] if match.endswith("*") else match
            keys = [k for k in keys if k.startswith(prefix)]
        return 0, keys

    async def incr(self, key: str) -> int:
        self._log("INCR")
        self._maybe_raise()
        current_val = self._get_live(key)
        # Preserve existing TTL on INCR (Redis behavior)
        existing_entry = self._store.get(key)
        existing_exp = existing_entry[1] if existing_entry is not None else None
        new_val = (int(current_val) + 1) if current_val is not None else 1
        self._store[key] = (str(new_val), existing_exp)
        return new_val

    async def expire(self, key: str, seconds: int, nx: bool = False, **kwargs: Any) -> int:
        """EXPIRE key seconds [NX].

        NX: set expiry only if key does NOT already have an expiry.
        Returns 1 if expiry was set, 0 if key does not exist or NX condition failed.
        """
        self._log("EXPIRE")
        self._maybe_raise()
        entry = self._store.get(key)
        if entry is None or self._is_expired(key):
            return 0
        _, existing_exp = entry
        if nx and existing_exp is not None:
            return 0  # NX: already has expiry, do not change
        val, _ = self._store[key]
        self._store[key] = (val, self._now() + seconds)
        return 1

    async def delete(self, *keys: str) -> int:
        self._log("DEL")
        self._maybe_raise()
        deleted = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                deleted += 1
        return deleted

    async def exists(self, *keys: str) -> int:
        self._log("EXISTS")
        self._maybe_raise()
        return sum(1 for k in keys if self._get_live(k) is not None)

    async def ttl(self, key: str) -> int:
        """Returns TTL in seconds; -2 if key missing; -1 if no expiry."""
        self._log("TTL")
        self._maybe_raise()
        entry = self._store.get(key)
        if entry is None or self._is_expired(key):
            return -2
        _, exp = entry
        if exp is None:
            return -1
        remaining = exp - self._now()
        return max(0, int(remaining))

    # ── aclose — no-op for compatibility ────────────────────────────────────
    async def aclose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis() -> FakeRedis:
    """Fresh FakeRedis instance per test."""
    return FakeRedis()


@pytest.fixture
def error_redis() -> FakeRedis:
    """FakeRedis that raises ConnectionError on every command."""
    return FakeRedis(raise_on_command=True)


# ---------------------------------------------------------------------------
# Log capture helper for structlog WARNING assertions
# ---------------------------------------------------------------------------


class CapturingProcessor:
    """structlog processor that captures log events for test assertions."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        if method in ("warning", "warn"):
            self.events.append(dict(event_dict))
        return event_dict

    @property
    def warning_count(self) -> int:
        return len(self.events)

    def has_event(self, event: str) -> bool:
        return any(e.get("event") == event for e in self.events)


@pytest.fixture
def log_capture(monkeypatch: pytest.MonkeyPatch) -> CapturingProcessor:
    """Capture structlog WARNING events emitted during the test."""
    import structlog

    processor = CapturingProcessor()

    # Wrap structlog's bound logger so warnings are captured
    original_get_logger = structlog.get_logger

    class CapturingLogger:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._inner = original_get_logger(*args, **kwargs)
            self._processor = processor

        def warning(self, event: str, **kw: Any) -> None:
            processor.events.append({"event": event, **kw})

        def warn(self, event: str, **kw: Any) -> None:
            self.warning(event, **kw)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    monkeypatch.setattr(structlog, "get_logger", lambda *a, **kw: CapturingLogger(*a, **kw))
    return processor
