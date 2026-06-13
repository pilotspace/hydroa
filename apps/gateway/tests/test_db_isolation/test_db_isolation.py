"""Red suite for test-db-isolation (v12): a global autouse fixture must isolate the
shared Redis (db 9) between tests so leaked usage:events stream entries cannot
contaminate a later flusher-driving suite (the FK-violation flake, recurring since v8).

Root cause (code-traced): usage_recorder.record() pushes to the usage:events stream
(db 9) fire-and-forget; the DB INSERT into usage_records happens when a flusher-driving
suite later consumes the stream. With no GLOBAL flush, leaked events get flushed against
a freshly-recreated schema → FK violation + varying counts. The fix is a shared autouse
fixture in tests/conftest.py that FLUSHDBs db 9 before each test and drains pending
fire-and-forget tasks at teardown.

Contract: test-db-isolation TASK.md §3 (FROZEN @ v1).

These tests assert the OBSERVABLE isolation invariant (a prior test's seed does not
survive into the next test) plus the structural presence of the drain + make target.
test_a_* must run before test_b_* (pytest preserves definition order within a file).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import redis.asyncio as aioredis

from gateway.core.config import Settings
from gateway.usage.infrastructure.redis_stream import STREAM_KEY

pytestmark = pytest.mark.integration

# Repo root: .../apps/gateway/tests/test_db_isolation/test_db_isolation.py → parents[3]
_GATEWAY_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _GATEWAY_ROOT.parent.parent


async def test_a_seed_stream_in_db9(settings: Settings) -> None:
    """Seed the usage:events stream in db 9 — the leak that test_b must NOT inherit."""
    r = aioredis.from_url(settings.redis_url)
    try:
        await r.xadd(STREAM_KEY, {"data": "leaked-1"})
        await r.xadd(STREAM_KEY, {"data": "leaked-2"})
        await r.set("usage:spend:tenant:leaked:202606", "9.99")
        assert await r.xlen(STREAM_KEY) >= 2
    finally:
        await r.aclose()


async def test_b_stream_clean_at_start(settings: Settings) -> None:
    """The autouse fixture must have flushed db 9 → no leaked stream/counter survives.

    RED before the fixture exists: test_a's seed persists → xlen >= 2 and the spend key
    is present. GREEN once the autouse pre-test FLUSHDB wipes db 9.
    """
    r = aioredis.from_url(settings.redis_url)
    try:
        assert await r.xlen(STREAM_KEY) == 0, "leaked usage:events survived into the next test"
        assert await r.get("usage:spend:tenant:leaked:202606") is None, (
            "leaked spend counter survived into the next test"
        )
    finally:
        await r.aclose()


def test_conftest_has_autouse_store_isolation() -> None:
    """tests/conftest.py must declare a global autouse fixture that clears usage leaks."""
    import tests.conftest as root_conftest

    src = inspect.getsource(root_conftest)
    assert "autouse=True" in src, "root conftest must define an autouse fixture"
    assert "_clear_usage_leaks_if_reachable" in src, (
        "the autouse fixture must clear leaked usage state before each test"
    )


def test_conftest_is_surgical_and_group_preserving() -> None:
    """The clear must be surgical (XTRIM + targeted DEL), NOT a blanket FLUSHDB.

    A FLUSHDB destroys the flusher's `ledger-flusher` consumer group (NOGROUP on
    XREADGROUP), breaking every flusher-driving suite. XTRIM preserves the group.
    The fixture must also NOT cancel pending tasks (that kills the pytest-asyncio runner).
    """
    import tests.conftest as root_conftest

    src = inspect.getsource(root_conftest)
    assert "xtrim" in src, "must XTRIM usage:events (clears backlog, keeps the group)"
    assert "usage:spend:" in src, "must clear the usage:spend:* counters"
    assert "flushdb" not in src, "must NOT FLUSHDB (it destroys the consumer group)"
    assert "t.cancel()" not in src, "the fixture must NOT cancel pending tasks"


def test_make_test_fast_target_defined() -> None:
    """The root Makefile must define a `test-fast` no-DB blast-radius target."""
    makefile = (_REPO_ROOT / "Makefile").read_text()
    assert "test-fast:" in makefile, "Makefile must define a test-fast target"
    assert "test-fast" in makefile.split(".PHONY")[1].split("\n")[0], (
        "test-fast must be declared .PHONY"
    )
    # The fast gate runs without coverage gating (subset run).
    fast_block = makefile.split("test-fast:")[1]
    assert "--no-cov" in fast_block, "test-fast must run with --no-cov (subset gate)"
    assert "tool_translation" in fast_block, "test-fast must include the translation suites"
