# ruff: noqa: S311 — seeded random.Random is for deterministic test assertions, not crypto
"""Red-first suite for the v8 routing-strategy task (TASK.md §3 FROZEN @ v1).

Adds a RoutingStrategy seam consulted by FallbackModelRouter:
  - OrderedStrategy (default) → declared order, v6 byte-identical.
  - SimpleShuffleStrategy(rng) → weighted-random PRIMARY, rest as fallback tail.
  - GATEWAY_ROUTING_STRATEGY setting selects it; unknown → UNKNOWN_ROUTING_STRATEGY.
  - Router validates the strategy returns a PERMUTATION → else INVALID_ROUTING_ORDER.

Runs RED before build:
  - `from gateway.proxy.application.routing_strategy import ...` → ImportError.
  - FallbackModelRouter(strategy=...) → TypeError (no such kwarg yet).
  - Settings(routing_strategy=...) → field absent (extra=ignore) → no validation / no attr.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from typing import Any

import pytest

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.domain.errors import UpstreamUnavailableError

ALIAS = "fast"
A, B, C = "model-A", "model-B", "model-C"
PAYLOAD: dict[str, Any] = {"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]}


def _body(model: str) -> dict[str, Any]:
    return {"id": f"gen-{model}", "model": model, "choices": [], "usage": {}}


class _Upstream:
    """Replay a sequence of (status, body) or exceptions; record payloads."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self._i = 0
        self.calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append(dict(payload))
        entry = self._outcomes[self._i]
        self._i += 1
        if isinstance(entry, BaseException):
            raise entry
        return entry

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.stream_calls.append(dict(payload))

        async def _gen() -> AsyncIterator[bytes]:
            yield b"chunk"

        return _gen()


class _Gate:
    async def is_available(self, model_id: str) -> bool:
        return True

    async def record_failure(self, model_id: str) -> None:  # noqa: D401
        pass

    async def record_success(self, model_id: str) -> None:
        pass


def _deployment(model_id: str, weight: int):
    from gateway.core.config import Deployment

    return Deployment(model_id=model_id, weight=weight)


# ---------------------------------------------------------------------------
# RS1 / RS2 — OrderedStrategy is the v6-byte-identical default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs1_ordered_default_byte_identical_complete() -> None:
    from gateway.proxy.application.routing_strategy import OrderedStrategy

    up = _Upstream([(200, _body(A))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B, C]},
        health_gate=_Gate(),
        strategy=OrderedStrategy(),
    )
    status, body, served = await router.complete(dict(PAYLOAD))
    assert (status, served) == (200, A)
    assert up.calls[0]["model"] == A


@pytest.mark.asyncio
async def test_rs2_ordered_preserves_fallback_order() -> None:
    from gateway.proxy.application.routing_strategy import OrderedStrategy

    up = _Upstream([UpstreamUnavailableError("a down"), (200, _body(B))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B, C]},
        health_gate=_Gate(),
        strategy=OrderedStrategy(),
    )
    status, body, served = await router.complete(dict(PAYLOAD))
    assert served == B
    assert [c["model"] for c in up.calls] == [A, B]


# ---------------------------------------------------------------------------
# RS3 / RS4 — SimpleShuffle weighted primary + full permutation fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs3_simple_shuffle_weighted_primary() -> None:
    from gateway.proxy.application.routing_strategy import SimpleShuffleStrategy

    strat = SimpleShuffleStrategy(rng=random.Random(1234))
    deployments = [_deployment(A, 1), _deployment(B, 9)]
    primaries = [strat.order(ALIAS, [A, B], deployments)[0] for _ in range(1000)]
    b_share = primaries.count(B) / len(primaries)
    assert 0.80 < b_share < 0.98, f"weighted primary B share off: {b_share}"
    assert primaries.count(A) > 0, "weight-1 candidate should still win sometimes"


@pytest.mark.asyncio
async def test_rs4_simple_shuffle_full_permutation_fallback() -> None:
    from gateway.proxy.application.routing_strategy import SimpleShuffleStrategy

    strat = SimpleShuffleStrategy(rng=random.Random(7))
    deployments = [_deployment(A, 1), _deployment(B, 9)]
    order = strat.order(ALIAS, [A, B], deployments)
    assert sorted(order) == sorted([A, B]), f"order must be a permutation: {order}"
    # primary fails → router falls through to the other candidate and serves it
    primary = order[0]
    other = order[1]
    up = _Upstream([UpstreamUnavailableError("primary down"), (200, _body(other))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={ALIAS: deployments},
        strategy=SimpleShuffleStrategy(rng=random.Random(7)),
    )
    _status, _body_, served = await router.complete(dict(PAYLOAD))
    assert served == other
    assert [c["model"] for c in up.calls] == [primary, other]


# ---------------------------------------------------------------------------
# RS5 — stream uses the strategy primary; ordered == candidates[0]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs5_stream_uses_strategy_primary() -> None:
    from gateway.proxy.application.routing_strategy import OrderedStrategy, SimpleShuffleStrategy

    deployments = [_deployment(A, 1), _deployment(B, 9)]
    seeded = SimpleShuffleStrategy(rng=random.Random(7))
    expected_primary = seeded.order(ALIAS, [A, B], deployments)[0]

    up = _Upstream([])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        deployments={ALIAS: deployments},
        strategy=SimpleShuffleStrategy(rng=random.Random(7)),
    )
    gen = router.stream(dict(PAYLOAD))
    async for _ in gen:
        break
    assert up.stream_calls[0]["model"] == expected_primary

    # ordered → candidates[0]
    up2 = _Upstream([])
    router2 = FallbackModelRouter(
        upstream=up2,
        model_groups={ALIAS: [A, B]},
        strategy=OrderedStrategy(),
    )
    gen2 = router2.stream(dict(PAYLOAD))
    async for _ in gen2:
        break
    assert up2.stream_calls[0]["model"] == A


# ---------------------------------------------------------------------------
# RS6 — plain (non-alias) id unaffected; strategy not consulted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs6_plain_model_id_unaffected() -> None:
    from gateway.proxy.application.routing_strategy import RoutingStrategy  # noqa: F401

    class _SpyStrategy:
        def __init__(self) -> None:
            self.called = False

        def order(self, alias: str, candidates: list[str], deployments: list[Any]) -> list[str]:
            self.called = True
            return list(candidates)

    spy = _SpyStrategy()
    up = _Upstream([(200, _body("vendor/x"))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        strategy=spy,
    )
    status, _body_, served = await router.complete({"model": "vendor/x", "messages": []})
    assert (status, served) == (200, "vendor/x")
    assert spy.called is False, "strategy must NOT be consulted on the plain path"


# ---------------------------------------------------------------------------
# RS7 — unknown strategy rejected at startup; default is "ordered"
# ---------------------------------------------------------------------------


def test_rs7_unknown_strategy_rejected() -> None:
    from pydantic import ValidationError

    from gateway.core.config import Settings

    with pytest.raises(ValidationError) as exc:
        Settings(
            database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
            jwt_secret="test-secret-not-for-production-0123456789",
            redis_url="redis://localhost:6380/9",
            environment="test",
            routing_strategy="round-robin",
        )
    assert "UNKNOWN_ROUTING_STRATEGY" in str(exc.value)


def test_rs_settings_default_ordered() -> None:
    from gateway.core.config import Settings

    s = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )
    assert s.routing_strategy == "ordered"


# ---------------------------------------------------------------------------
# RS8 — non-permutation strategy rejected at runtime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs8_non_permutation_rejected() -> None:
    class _DropStrategy:
        def order(self, alias: str, candidates: list[str], deployments: list[Any]) -> list[str]:
            return candidates[:1]  # drops the rest — not a permutation

    up = _Upstream([(200, _body(A))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        strategy=_DropStrategy(),
    )
    with pytest.raises(Exception) as exc:  # noqa: B017 — message-based assertion below
        await router.complete(dict(PAYLOAD))
    assert "INVALID_ROUTING_ORDER" in str(exc.value)


# ---------------------------------------------------------------------------
# Wiring — create_app builds the configured strategy
# ---------------------------------------------------------------------------


def test_rs_create_app_wires_strategy() -> None:
    from gateway.core.config import Settings
    from gateway.main import create_app
    from gateway.proxy.application.routing_strategy import SimpleShuffleStrategy

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
        routing_strategy="simple-shuffle",
    )
    app = create_app(settings)
    strat = app.state.model_router._strategy  # type: ignore[attr-defined]
    assert isinstance(strat, SimpleShuffleStrategy)
