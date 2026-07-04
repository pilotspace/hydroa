"""Red/Green test suite for batch-observability-scaffolding — GET /admin/batches/stats
gains 3 new fields (TASK.md §3, FROZEN @ v1).

Contract under test:
  avg_window_wait_ms         — REAL, computed from BatchWindowBuffer's additive Redis
                                writes (batch:window:wait_sum/wait_count per tenant,
                                see _CLAIM_DUE_LUA). None when no samples yet OR Redis
                                is unreachable — never a fabricated 0.0 (fail-open,
                                mirrors usage/api/router.py:_read_ratelimit_counters
                                EXACTLY: getattr(...redis_client..., None), bounded by
                                asyncio.timeout, catches (RedisError, OSError,
                                ValueError, TimeoutError)).
  avg_completion_latency_ms  — ALWAYS None today (no live BatchProcessor exists yet —
                                batch_processor is hardcoded None in main.py; pending
                                v58's openai-batch-adapter/anthropic-batch-adapter +
                                batch-billing-accuracy, none of which exist in this
                                repo yet).
  completion_tpm             — ALWAYS None today, same reason as above.

Deliberately a SEPARATE file from test_batch_stats.py (which already covers the
pre-existing 3 fields' auth/tenant-isolation/real-activity behavior and is left
completely unmodified by this task) and from test_batch_window_grouping.py (which
gains its own new, additive tests for _CLAIM_DUE_LUA's Redis side-effect at the
BatchWindowBuffer level, one layer below this file's HTTP-level tests).

RED before Build: BatchStatsResponse does not carry these 3 fields yet, so
resp.json()["avg_window_wait_ms"] etc. raise KeyError — the honest
missing-implementation red (the route/model exist; the new fields do not).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from gateway.proxy.infrastructure.batch_window_buffer import BatchWindowBuffer, _wait_count_key

pytestmark = pytest.mark.asyncio

STATS_URL = "/admin/batches/stats"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _signup_owner(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    login = await client.post("/admin/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, f"login failed: {login.text}"
    jwt = login.json()["access_token"]

    return {"tenant_id": tenant_id, "jwt": jwt}


class _FakeClock:
    """Same injectable-clock convention as test_batch_window_grouping.py."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, delta: float) -> None:
        self._t += delta


@pytest.fixture
async def owner(client: Any) -> dict[str, str]:
    return await _signup_owner(
        client,
        tenant_name="BatchStatsObservabilityTest",
        email="batch-stats-observability@example.io",
        password="batch-stats-observability-battery",
    )


# ---------------------------------------------------------------------------
# The two placeholder fields are always None — regardless of window-wait state.
# ---------------------------------------------------------------------------


class TestPlaceholderFieldsAlwaysNone:
    async def test_completion_latency_and_tpm_always_none(
        self, client: Any, owner: dict[str, str]
    ) -> None:
        resp = await client.get(STATS_URL, headers=_bearer(owner["jwt"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["avg_completion_latency_ms"] is None
        assert body["completion_tpm"] is None


# ---------------------------------------------------------------------------
# avg_window_wait_ms: no samples yet -> None, never a fabricated 0.0
# ---------------------------------------------------------------------------


class TestWindowWaitNoSamplesYet:
    async def test_no_samples_yet_reads_null_not_zero(
        self, client: Any, owner: dict[str, str]
    ) -> None:
        """A tenant that has never had a window claimed must read None -- the
        wait_sum/wait_count keys are entirely absent, not present-with-zero."""
        resp = await client.get(STATS_URL, headers=_bearer(owner["jwt"]))
        assert resp.status_code == 200, resp.text
        assert resp.json()["avg_window_wait_ms"] is None


# ---------------------------------------------------------------------------
# avg_window_wait_ms: fail-open on an absent/erroring Redis client — never 500.
# ---------------------------------------------------------------------------


class TestWindowWaitFailOpen:
    async def test_redis_absent_avg_window_wait_null(
        self, client: Any, app: Any, owner: dict[str, str]
    ) -> None:
        # Simulate Redis being unavailable for the read path (same convention as
        # ratelimit_counter_view/test_ratelimit_counter_view.py:test_redis_unavailable_counters_null).
        app.state.redis_client = None
        resp = await client.get(STATS_URL, headers=_bearer(owner["jwt"]))
        assert resp.status_code == 200, resp.text  # fail-open, never 500
        assert resp.json()["avg_window_wait_ms"] is None

    async def test_redis_error_avg_window_wait_null(
        self, client: Any, app: Any, owner: dict[str, str]
    ) -> None:
        class _BoomRedis:
            async def mget(self, *_a: Any, **_k: Any) -> Any:
                # redis-py raises redis.exceptions.ConnectionError (a RedisError) in
                # prod -- model that exact arm, matching
                # bandwidth_counter_view/test_bandwidth_counter_view.py's convention.
                raise RedisConnectionError("redis down")

        app.state.redis_client = _BoomRedis()
        resp = await client.get(STATS_URL, headers=_bearer(owner["jwt"]))
        assert resp.status_code == 200, resp.text  # fail-open, never 500
        assert resp.json()["avg_window_wait_ms"] is None

    async def test_corrupted_wait_count_value_avg_window_wait_null(
        self, client: Any, app: Any, owner: dict[str, str]
    ) -> None:
        """A non-numeric stored value (corruption / manual tamper) must ALSO fail
        open to None, never surface as an uncaught ValueError -> 500 -- the
        int()/float() casts must sit inside the same try/except as the Redis
        reads, mirroring _read_ratelimit_counters's exact structural placement."""
        tenant_id = uuid.UUID(owner["tenant_id"])
        await app.state.redis_client.set(_wait_count_key(tenant_id), "not-a-number")

        resp = await client.get(STATS_URL, headers=_bearer(owner["jwt"]))
        assert resp.status_code == 200, resp.text  # fail-open, never 500
        assert resp.json()["avg_window_wait_ms"] is None


# ---------------------------------------------------------------------------
# §1 Accept: a real, positive average once windows have actually flushed.
# ---------------------------------------------------------------------------


class TestWindowWaitRealAverage:
    async def test_avg_window_wait_reflects_two_flushed_windows(
        self, client: Any, app: Any, owner: dict[str, str]
    ) -> None:
        """§1 Accept: a tenant whose stub BatchProcessor test harness has flushed 2
        windows sees avg_window_wait_ms as a real positive number reflecting those
        2 flushes' actual elapsed times, while the two placeholder fields stay None."""
        tenant_id = uuid.UUID(owner["tenant_id"])
        clock = _FakeClock()
        settings = app.state.settings
        settings.batch_window_seconds = 3.0
        settings.batch_max_items_per_job = 500
        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)

        await buffer.append(tenant_id=tenant_id, custom_id="w1", key_id=uuid.uuid4(), body={})
        clock.advance(3.5)  # first flush: elapsed 3.5s
        assert await buffer.claim_due(tenant_id) is not None

        await buffer.append(tenant_id=tenant_id, custom_id="w2", key_id=uuid.uuid4(), body={})
        clock.advance(4.0)  # second flush: elapsed 4.0s
        assert await buffer.claim_due(tenant_id) is not None

        resp = await client.get(STATS_URL, headers=_bearer(owner["jwt"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # (3.5 + 4.0) / 2 windows = 3.75s == 3750.0ms
        assert body["avg_window_wait_ms"] == pytest.approx(3750.0)
        assert body["avg_completion_latency_ms"] is None
        assert body["completion_tpm"] is None


# ---------------------------------------------------------------------------
# Regression: wait_sum/wait_count must be read via ONE atomic round trip.
#
# _CLAIM_DUE_LUA writes both keys together inside a single EVAL. Reading them back
# via two sequential GETs leaves a window where a concurrent claim's write can land
# between the two reads -- sum observed absent (pre-write), count observed already
# incremented (post-write) -- fabricating a 0.0 average even though real samples
# exist. A single MGET is itself one Redis command, so it always observes both keys
# at the same instant. This test pins the call shape so a future edit can't quietly
# reintroduce two sequential GETs.
# ---------------------------------------------------------------------------


class TestWindowWaitAtomicRead:
    async def test_reads_both_keys_via_single_mget_not_two_gets(
        self, client: Any, app: Any, owner: dict[str, str]
    ) -> None:
        real_redis = app.state.redis_client
        calls: list[str] = []

        class _RecordingRedis:
            async def get(self, *a: Any, **k: Any) -> Any:
                calls.append("get")
                return await real_redis.get(*a, **k)

            async def mget(self, *a: Any, **k: Any) -> Any:
                calls.append("mget")
                return await real_redis.mget(*a, **k)

        app.state.redis_client = _RecordingRedis()
        resp = await client.get(STATS_URL, headers=_bearer(owner["jwt"]))
        assert resp.status_code == 200, resp.text
        assert calls == ["mget"], f"expected a single atomic MGET, got {calls}"
