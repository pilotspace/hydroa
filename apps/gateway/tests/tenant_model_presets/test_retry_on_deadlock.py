"""Unit suite for `_retry_on_deadlock`/`_is_deadlock` (tenant_model_preset_store.py).

Fast-follow from PR #51's review: the deadlock-retry primitive was previously only
exercised empirically (an 8x stress run) via a *separate copy* of `_is_deadlock` living in
each test file's own DB-bootstrap harness — the real production helpers in
`gateway.proxy.infrastructure.tenant_model_preset_store` had zero direct unit coverage.

Pure unit tests: `op` is a plain async callable and `DBAPIError` instances are constructed
in-memory (no real Postgres connection, no session/transaction) — this is unrelated to the
DB-backed suite in `test_tenant_model_preset_store_db.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

from gateway.proxy.infrastructure.tenant_model_preset_store import (
    _DEADLOCK_MAX_ATTEMPTS,  # pyright: ignore[reportPrivateUsage]
    _is_deadlock,  # pyright: ignore[reportPrivateUsage]
    _retry_on_deadlock,  # pyright: ignore[reportPrivateUsage]
)


class _FakeOrig(Exception):
    """Minimal stand-in for a DBAPI driver exception carrying a `.sqlstate`."""

    def __init__(self, sqlstate: str | None = None, msg: str = "synthetic db error") -> None:
        super().__init__(msg)
        self.sqlstate = sqlstate


def _deadlock_error() -> DBAPIError:
    return DBAPIError("UPDATE ...", {}, _FakeOrig(sqlstate="40P01"))


def _deadlock_error_message_only() -> DBAPIError:
    """No `.sqlstate` attribute at all — exercises `_is_deadlock`'s message-substring fallback."""
    orig = Exception("server closed the connection: deadlock detected")
    return DBAPIError("UPDATE ...", {}, orig)


def _unrelated_db_error() -> DBAPIError:
    """A real but non-deadlock DBAPIError (unique_violation, SQLSTATE 23505)."""
    return DBAPIError("INSERT ...", {}, _FakeOrig(sqlstate="23505", msg="duplicate key value"))


# ---------------------------------------------------------------------------
# _is_deadlock
# ---------------------------------------------------------------------------


def test_is_deadlock_true_for_sqlstate_40p01() -> None:
    assert _is_deadlock(_deadlock_error()) is True


def test_is_deadlock_true_for_message_fallback() -> None:
    assert _is_deadlock(_deadlock_error_message_only()) is True


def test_is_deadlock_false_for_unrelated_error() -> None:
    assert _is_deadlock(_unrelated_db_error()) is False


# ---------------------------------------------------------------------------
# _retry_on_deadlock
# ---------------------------------------------------------------------------


def _counting_op(
    calls: list[int], raises: Callable[[int], BaseException | None]
) -> Callable[[], Any]:
    """Build an async op; on call N (1-indexed) either raises `raises(N)` or returns N."""

    async def op() -> int:
        calls.append(1)
        n = len(calls)
        exc = raises(n)
        if exc is not None:
            raise exc
        return n

    return op


@pytest.mark.asyncio
async def test_retry_on_deadlock_succeeds_after_transient_deadlocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two transient deadlocks then success — retried transparently, no error surfaces."""
    monkeypatch.setattr(
        "gateway.proxy.infrastructure.tenant_model_preset_store.asyncio.sleep",
        _instant_sleep,
    )
    calls: list[int] = []
    op = _counting_op(calls, lambda n: _deadlock_error() if n < 3 else None)

    result = await _retry_on_deadlock(op)

    assert result == 3
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_retry_on_deadlock_reraises_non_deadlock_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-deadlock DBAPIError must never be retried — it propagates on the first attempt."""
    monkeypatch.setattr(
        "gateway.proxy.infrastructure.tenant_model_preset_store.asyncio.sleep",
        _instant_sleep,
    )
    calls: list[int] = []
    op = _counting_op(calls, lambda n: _unrelated_db_error())

    with pytest.raises(DBAPIError, match="duplicate key value"):
        await _retry_on_deadlock(op)

    assert len(calls) == 1, "a non-deadlock error must not trigger a retry"


@pytest.mark.asyncio
async def test_retry_on_deadlock_reraises_after_exhausting_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PERSISTENT deadlock (every attempt fails) must re-raise, never be silently swallowed."""
    monkeypatch.setattr(
        "gateway.proxy.infrastructure.tenant_model_preset_store.asyncio.sleep",
        _instant_sleep,
    )
    calls: list[int] = []
    op = _counting_op(calls, lambda n: _deadlock_error())

    with pytest.raises(DBAPIError):
        await _retry_on_deadlock(op)

    assert len(calls) == _DEADLOCK_MAX_ATTEMPTS, (
        "must attempt exactly _DEADLOCK_MAX_ATTEMPTS times, then re-raise — never retry forever "
        "and never swallow the final failure"
    )


async def _instant_sleep(*_args: Any, **_kwargs: Any) -> None:
    """Replace the real backoff sleep so this suite runs in milliseconds."""
    return None
