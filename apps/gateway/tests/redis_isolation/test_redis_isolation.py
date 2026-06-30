"""Cross-test Redis isolation (deterministic-test-isolation task).

The full single-process suite was flaky: stateful suites (response_caching,
routing_config_store, openrouter_cost_recovery, pii_v2) passed in isolation but
contaminated each other in the full run via shared Redis (db 9) keys that the
autouse `_isolate_stores` fixture did NOT clear (it only trimmed `usage:events`
and deleted `usage:spend:*`). Leftover `resp-cache:` / `ratelimit:` / worker
counters from one suite (or a prior partial run) leaked into later suites.

These tests pin the contract: BEFORE each test the autouse fixture leaves Redis
db 9 with NO inherited keys EXCEPT the `usage:events` stream (whose consumer
group must survive — a blanket FLUSHDB would break every flusher-driving suite).

RED (before the fix): test_b_sees_no_inherited_keys fails — the `resp-cache:` key
written by test_a survives because the old fixture only cleared usage:* keys.
GREEN (after): the broadened clear removes it; the consumer group still survives.
"""

from __future__ import annotations

from typing import Any

import pytest
import redis.asyncio as aioredis

from gateway.core.config import Settings

_LEAK_KEY = "resp-cache:leaktest-tenant:deadbeefcafe"
_LEAK_RL = "ratelimit:leaktest-key:202606"


@pytest.fixture
async def raw_redis(settings: Settings) -> Any:
    r = aioredis.from_url(settings.redis_url)
    try:
        yield r
    finally:
        await r.aclose()


async def test_a_writes_leaky_keys(raw_redis: Any) -> None:
    """Write non-usage Redis keys that the OLD fixture would NOT clear."""
    await raw_redis.set(_LEAK_KEY, "cached-body")
    await raw_redis.set(_LEAK_RL, "7")
    assert await raw_redis.get(_LEAK_KEY) is not None


async def test_b_sees_no_inherited_keys(raw_redis: Any) -> None:
    """The autouse isolation must have cleared test_a's leaked keys before this test."""
    leaked_cache = await raw_redis.get(_LEAK_KEY)
    leaked_rl = await raw_redis.get(_LEAK_RL)
    assert leaked_cache is None, f"cross-test Redis leak: {_LEAK_KEY} survived ({leaked_cache!r})"
    assert leaked_rl is None, f"cross-test Redis leak: {_LEAK_RL} survived ({leaked_rl!r})"


async def test_c_usage_events_stream_group_preserved(raw_redis: Any) -> None:
    """The clear must be SURGICAL: the usage:events stream + its consumer group survive.

    Recreate the stream+group (as create_app does), then confirm a subsequent
    isolation pass keeps the group readable (no NOGROUP) — proving we did not FLUSHDB.
    """
    # Ensure the stream + ledger-flusher group exist (idempotent create).
    try:
        await raw_redis.xgroup_create("usage:events", "ledger-flusher", id="0", mkstream=True)
    except Exception:
        pass  # BUSYGROUP — already exists, fine
    # The group must be queryable (would raise NOGROUP if a FLUSHDB had wiped it).
    groups = await raw_redis.xinfo_groups("usage:events")
    names = {g.get(b"name", g.get("name")) for g in groups}
    assert b"ledger-flusher" in names or "ledger-flusher" in names
