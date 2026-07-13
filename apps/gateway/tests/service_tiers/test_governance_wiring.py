"""RED-first suite for service-tiers M3/M5/M7/M8/M9/R4 — the dual-copy governance
wiring, admission-capacity shedding, hold-release-on-later-rejection, hold-persists-
through-a-slow-response, and disabled-tiering byte-identity (TASK.md §4, contract
FROZEN @ v1).

RED reason before BUILD: TierCapacityGuard.check_and_hold is never called from
either CompletionUseCase._enforce_governance or NonChatGovernance.authorize.

Strategy: a REAL RedisTierCapacityGuard (mirrors credits_ledger's real
PostgresCreditGuard pattern) wired directly onto app.state.tier_capacity_guard,
exercised through the actual HTTP surface — proves the wiring, not just the guard's
own mechanics (already proven directly in test_capacity_guard.py).
"""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.proxy.infrastructure.tier_capacity_guard import RedisTierCapacityGuard
from tests.service_tiers.conftest import assert_problem, auth_key, bearer

COMPLETIONS = "/v1/chat/completions"


class FakeCompletionUpstream:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body or {
            "id": "gen-tiers-1",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body


class _GatedCompletionUpstream:
    """Blocks INSIDE complete() until `release_event` is set — lets a test hold a
    request in flight to observe the tier-capacity slot while it is genuinely
    occupied (M9), then release it deterministically."""

    def __init__(self, release_event: asyncio.Event, body: dict[str, Any] | None = None) -> None:
        self.release_event = release_event
        self.body = body or {
            "id": "gen-tiers-gated-1",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        self.calls = 0
        self.entered = asyncio.Event()

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        self.entered.set()
        await self.release_event.wait()
        return 200, self.body


async def _pool_occupancy(redis_client: Any) -> int:
    total = 0
    for key in (b"tier:pool:priority_floor", b"tier:pool:standard_floor", b"tier:pool:shared"):
        total += await redis_client.zcard(key)
    return total


async def poll_until_zero(redis_client: Any, *, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if await _pool_occupancy(redis_client) == 0:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("tier pool occupancy never returned to 0")


# ---------------------------------------------------------------------------
# Dual-copy governance — the tier hold is placed at BOTH choke points (M3/M7)
# ---------------------------------------------------------------------------


async def test_tier_hold_wired_at_chat_choke_point(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    guard = RedisTierCapacityGuard(
        redis=redis_client,
        cluster_cap=1,
        priority_reserved_pct=0.0,
        standard_reserved_pct=0.0,
        hold_ttl_s=600,
    )
    app.state.tier_capacity_guard = guard
    app.state.completion_upstream = FakeCompletionUpstream()

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(api_key["key"]),
    )
    assert resp.status_code == 200, resp.text
    await poll_until_zero(redis_client)  # the hold must have been placed AND released

    # A second, independent guard instance sharing the SAME Redis proves occupancy
    # actually moved through Redis (not just an in-process call) — pre-fill the sole
    # slot directly, then confirm the SAME choke point now sheds.
    await redis_client.zadd("tier:pool:shared", {uuid.uuid4().hex: time.time() * 1000})
    resp2 = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(api_key["key"]),
    )
    assert_problem(resp2, 503, "ERR_TIER_CAPACITY_EXHAUSTED")


async def test_tier_hold_wired_at_nonchat_choke_point(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    redis_client: Any,
) -> None:
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

    model_id = "text-embedding-3-small"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 8192, true, 'embedding', 'openai')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": model_id, "name": "text-embedding-3-small"},
    )
    await db_session.commit()

    class _FakeEmbeddingProvider:
        name = "openai"

        async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            return 200, {
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": model_id,
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            }

        async def post_multipart(self, *a: Any, **kw: Any) -> tuple[int, dict[str, Any]]:
            return 200, {}

        def stream_bytes(self, *a: Any, **kw: Any) -> Any:
            async def _gen() -> Any:
                yield b""

            return _gen()

    registry = getattr(app.state, "provider_registry", None)
    if registry is not None and hasattr(registry, "_providers"):
        registry._providers["openai"] = _FakeEmbeddingProvider()  # type: ignore[attr-defined]
    else:
        app.state.provider_registry = ProviderRegistry({"openai": _FakeEmbeddingProvider()})  # type: ignore[arg-type]

    guard = RedisTierCapacityGuard(
        redis=redis_client,
        cluster_cap=1,
        priority_reserved_pct=0.0,
        standard_reserved_pct=0.0,
        hold_ttl_s=600,
    )
    app.state.tier_capacity_guard = guard
    # Pre-fill the sole slot — a non-chat request must ALSO be shed by the same pool.
    await redis_client.zadd("tier:pool:shared", {uuid.uuid4().hex: time.time() * 1000})

    resp = await client.post(
        "/v1/embeddings",
        json={"model": model_id, "input": "hello world"},
        headers=auth_key(api_key["key"]),
    )
    assert_problem(resp, 503, "ERR_TIER_CAPACITY_EXHAUSTED")


# ---------------------------------------------------------------------------
# R4 — every applicable pool exhausted sheds with the tier-specific code
# ---------------------------------------------------------------------------


async def test_all_pools_exhausted_sheds_with_tier_specific_code(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    guard = RedisTierCapacityGuard(
        redis=redis_client,
        cluster_cap=1,
        priority_reserved_pct=0.0,
        standard_reserved_pct=0.0,
        hold_ttl_s=600,
    )
    app.state.tier_capacity_guard = guard
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    await redis_client.zadd("tier:pool:shared", {uuid.uuid4().hex: time.time() * 1000})

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(api_key["key"]),
    )
    assert_problem(resp, 503, "ERR_TIER_CAPACITY_EXHAUSTED")
    assert resp.headers.get("Retry-After") is not None
    assert upstream.calls == 0, "a shed request must never reach the provider"

    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM usage_records WHERE tenant_id = :t"),
            {"t": api_key["tenant_id"]},
        )
    ).scalar()
    assert count == 0, "a shed request must never write a usage record"


# ---------------------------------------------------------------------------
# M5 (edge) — a LATER governance rejection (RPM) reverses the already-placed tier hold
# ---------------------------------------------------------------------------


async def test_tier_hold_released_on_later_rpm_rejection(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    guard = RedisTierCapacityGuard(
        redis=redis_client,
        cluster_cap=5,
        priority_reserved_pct=0.0,
        standard_reserved_pct=0.0,
        hold_ttl_s=600,
    )
    app.state.tier_capacity_guard = guard
    app.state.completion_upstream = FakeCompletionUpstream()

    await client.patch(
        f"/admin/keys/{api_key['key_id']}",
        json={"rpm_limit": 1},
        headers=bearer(api_key["jwt"]),
    )
    now_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    rl_redis = getattr(getattr(app.state, "rate_limiter", None), "_redis", None)
    assert rl_redis is not None, "rate_limiter must be wired with a real redis client for this test"
    await rl_redis.zadd(
        f"ratelimit:rpm:{api_key['key_id']}", {str(now_ms - 1000).encode(): now_ms - 1000}
    )

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(api_key["key"]),
    )
    assert_problem(resp, 429, "ERR_RATE_LIMITED")

    # The tier hold placed BEFORE the RPM check must be reversed, not stranded, even
    # though the request never reached the provider.
    assert await _pool_occupancy(redis_client) == 0


# ---------------------------------------------------------------------------
# M9 — the slot is held for the whole (slow) response, then released
# ---------------------------------------------------------------------------


async def test_slot_held_for_whole_response_then_released(
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    guard = RedisTierCapacityGuard(
        redis=redis_client,
        cluster_cap=1,
        priority_reserved_pct=0.0,
        standard_reserved_pct=0.0,
        hold_ttl_s=600,
    )
    app.state.tier_capacity_guard = guard
    release_event = asyncio.Event()
    gated_upstream = _GatedCompletionUpstream(release_event)
    app.state.completion_upstream = gated_upstream

    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        request_task = asyncio.ensure_future(
            c.post(
                COMPLETIONS,
                json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_key(api_key["key"]),
            )
        )
        await asyncio.wait_for(gated_upstream.entered.wait(), timeout=5.0)

        # The request is genuinely in flight — the sole slot must be occupied, and a
        # second concurrent request for the SAME single-slot pool must be shed.
        assert await _pool_occupancy(redis_client) == 1
        second = await c.post(
            COMPLETIONS,
            json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_key(api_key["key"]),
        )
        assert_problem(second, 503, "ERR_TIER_CAPACITY_EXHAUSTED")

        release_event.set()
        first_resp = await asyncio.wait_for(request_task, timeout=5.0)
        assert first_resp.status_code == 200, first_resp.text

        await poll_until_zero(redis_client)


# ---------------------------------------------------------------------------
# Disabled tiering (default cluster_cap=0) is byte-identical
# ---------------------------------------------------------------------------


async def test_disabled_tiering_is_byte_identical(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    """No guard override — the app's own default PassthroughTierCapacityGuard
    (cluster_cap=0) never touches Redis; the pool keys stay entirely absent."""
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(api_key["key"]),
    )
    assert resp.status_code == 200, resp.text
    assert upstream.calls == 1
    assert await _pool_occupancy(redis_client) == 0
