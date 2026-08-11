"""The fixed-window pin must be LIVE, and no new limiter may escape it (todo #111).

Three separate things are checked here, because a green rate-limit suite proves none of
them on its own — those tests passed before the pin existed and would keep passing if the
pin silently did nothing.

1. The defect is real: a clock that steps across a window boundary mid-test resets the
   counter, so the request that should be rejected is admitted.
2. The pin is in effect: a limiter constructed anywhere during a test gets the frozen
   clock, and an explicitly-passed clock still wins.
3. No limiter escapes: every fixed-window bucket in `src/gateway` reads `self._now()` and
   its class is registered for pinning. A new limiter that hard-codes `time.time()` is
   un-pinnable, and would reintroduce the flake in a file this helper has never heard of —
   the same cross-manifest drift that has bitten this repo before.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from gateway.tenants.infrastructure.invite_public_rate_limiter import (
    InvitePublicRateLimiter,
    InviteRateLimitedError,
)
from tests import _rate_limit_clock

GATEWAY_SRC = Path(__file__).resolve().parents[2] / "src" / "gateway"


class _CountingRedis:
    """Minimal INCR/EXPIRE double. Keyed exactly as Redis would be, so the bucket suffix in
    the key is what decides whether a counter continues or restarts."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> bool:  # noqa: ARG002
        return True


class _SteppingClock:
    """Returns each supplied instant in turn, repeating the last one forever."""

    def __init__(self, *instants: float) -> None:
        self._instants = list(instants)
        self._index = 0

    def __call__(self) -> float:
        instant = self._instants[min(self._index, len(self._instants) - 1)]
        self._index += 1
        return instant


async def _drive(limiter: InvitePublicRateLimiter, *, limit: int, calls: int) -> int:
    """Return how many of `calls` were ADMITTED."""
    admitted = 0
    for _ in range(calls):
        try:
            await limiter.check(action="preview", key="1.2.3.4", limit=limit)
            admitted += 1
        except InviteRateLimitedError:
            pass
    return admitted


@pytest.mark.asyncio
async def test_a_boundary_crossing_clock_admits_a_request_that_should_be_rejected() -> None:
    """THE DEFECT, reproduced deterministically instead of waited for.

    Three calls at limit=2. The clock steps from 0.9s before a boundary to 0.1s after it,
    which is exactly what happens when a real minute rolls between two requests: the key's
    bucket suffix changes, INCR starts a fresh counter, and the third call is admitted.

    In a real test that reads as "the rate limiter stopped working". It is instead the test
    having assumed something nobody measured.
    """
    boundary = float(_rate_limit_clock._BASE_EPOCH)  # noqa: SLF001 — a bucket edge by construction
    limiter = InvitePublicRateLimiter(
        redis=_CountingRedis(),
        now=_SteppingClock(boundary - 0.9, boundary - 0.8, boundary + 0.1),
    )
    admitted = await _drive(limiter, limit=2, calls=3)
    assert admitted == 3, (
        "expected the boundary crossing to reset the counter and admit all three — if this "
        "fails, the fixed-window mechanism changed and this test's premise is stale"
    )


@pytest.mark.asyncio
async def test_a_pinned_clock_cannot_cross_a_boundary(request: pytest.FixtureRequest) -> None:
    """The same three calls, with the clock the autouse fixture installs: 2 admitted, 1 rejected.

    The pinned instant sits mid-bucket, so no sequence of calls within one test can roll the
    window regardless of how long the test takes or how loaded the host is.

    The KEY assertion is the second one. `admitted == 2` on its own proves nothing about the
    pin: three calls take ~0.2ms, so an unpinned real clock rolls a minute between them
    roughly never, and this test passed with the fixture deleted. Asserting on the bucket in
    the Redis key is what makes the verdict depend on the pin actually being installed —
    otherwise this is a gate that reports green either way.
    """
    redis = _CountingRedis()
    limiter = InvitePublicRateLimiter(redis=redis)
    admitted = await _drive(limiter, limit=2, calls=3)
    assert admitted == 2, f"limit=2 must admit exactly 2 of 3, got {admitted}"

    pinned_bucket = int(_rate_limit_clock.frozen_instant_for(request.node.nodeid) // 60)
    assert list(redis.counters) == [f"invite:public:rl:preview:1.2.3.4:{pinned_bucket}"], (
        f"all three calls must land in the single pinned bucket {pinned_bucket}; got keys "
        f"{sorted(redis.counters)}. More than one key means the window rolled; a different "
        f"bucket means the limiter read a clock other than the pinned one."
    )


def test_the_autouse_pin_is_actually_in_effect(request: pytest.FixtureRequest) -> None:
    """Proves the fixture is LIVE rather than silently inert.

    Without this, every rate-limit test would keep passing whether or not the pin worked —
    they passed before it existed. So the assertion is on the clock itself, not on a verdict
    that happens to be reachable both ways.
    """
    expected = _rate_limit_clock.frozen_instant_for(request.node.nodeid)
    for cls in _rate_limit_clock._limiter_classes():  # noqa: SLF001 — the registry under test
        instance = cls(redis=_CountingRedis())
        actual = instance._now()  # noqa: SLF001 — the injected seam
        assert actual == expected, (
            f"{cls.__name__} was constructed with clock {actual}, expected the pinned "
            f"{expected}. The autouse fixture is not reaching this class."
        )


def test_an_explicitly_passed_clock_still_wins() -> None:
    """A test that drives the window deliberately must not be overridden by the pin."""
    limiter = InvitePublicRateLimiter(redis=_CountingRedis(), now=lambda: 12345.0)
    assert limiter._now() == 12345.0  # noqa: SLF001 — the injected seam


def _fixed_window_classes_in_src() -> dict[str, Path]:
    """Classes computing a fixed-window bucket, found by reading src rather than by listing.

    Matches `int(<clock> // <window>)` inside a class body. Discovery is the point: a
    hand-maintained list of limiters would drift exactly the way `migrations/env.py` did.
    """
    found: dict[str, Path] = {}
    bucket_expr = re.compile(r"int\(\s*(self\._now\(\)|time\.time\(\))\s*(?://|\)\s*//)")
    for path in sorted(GATEWAY_SRC.rglob("*.py")):
        source = path.read_text()
        if not bucket_expr.search(source):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            body_src = ast.get_source_segment(source, node) or ""
            if bucket_expr.search(body_src):
                found[node.name] = path
    return found


def test_every_fixed_window_limiter_uses_the_injectable_clock() -> None:
    """A limiter reading `time.time()` directly is UN-PINNABLE, so the flake comes back."""
    offenders: list[str] = []
    for name, path in _fixed_window_classes_in_src().items():
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == name:
                body_src = ast.get_source_segment(source, node) or ""
                if re.search(r"int\(\s*time\.time\(\)", body_src):
                    offenders.append(f"{path.relative_to(GATEWAY_SRC.parent.parent)}::{name}")
    assert not offenders, (
        "these fixed-window limiters bucket on `time.time()` directly, so no test can pin "
        "their window and each one reintroduces the boundary race:\n  "
        + "\n  ".join(offenders)
        + "\n\nTake `now: Callable[[], float] = time.time` in __init__ and read `self._now()` "
        "for BOTH the bucket and the retry-after — two separate `time.time()` reads can "
        "straddle a boundary and disagree with each other."
    )


def test_every_fixed_window_limiter_is_registered_for_pinning() -> None:
    """Discovered in src but absent from the registry == silently unpinned.

    This is the drift check. The registry is deliberately explicit (adding a limiter should
    be a decision), which is exactly why something has to notice when the two disagree.
    """
    registered = {cls.__name__ for cls in _rate_limit_clock._limiter_classes()}  # noqa: SLF001
    discovered = set(_fixed_window_classes_in_src())
    missing = sorted(discovered - registered)
    assert not missing, (
        "these fixed-window limiters exist in src/gateway but are NOT registered in "
        f"tests/_rate_limit_clock.py::_limiter_classes, so their window is never pinned "
        f"and any 429 test against them keeps the boundary race: {missing}"
    )
    stale = sorted(registered - discovered)
    assert not stale, (
        "these classes are registered for pinning but no longer compute a fixed-window "
        f"bucket in src/gateway — the registry is stale and the pin is doing nothing "
        f"for them: {stale}"
    )
