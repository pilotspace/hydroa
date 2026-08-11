"""Pin every fixed-window rate limiter's clock, per test (todo #111).

THE DEFECT. Each fixed-window limiter buckets on ``floor(now / WINDOW)``. A test that
fires N requests and expects the (N+1)th to be rejected is therefore assuming that no
window boundary falls between the first and the last — and nothing measured that
assumption. When a boundary does land mid-test the counter resets, the last request
returns 200, and the failure reads as "the limiter is broken" rather than "the test made a
bet". Probability per run is roughly test-duration / 60s, which is a few percent under
`-n 12` — squarely the rotating-tail signature where each run fails a different subset.

WHY A CLASS PATCH AND NOT ``monkeypatch.setattr(time, "time", ...)``. Freezing the global
clock also freezes it for JWT ``exp`` validation, Redis client internals, and every other
consumer in the same test, so it can mask real failures or invent new ones. The production
limiters now take an injectable ``now``, so the pin can be surgical: it moves ONLY the
limiter's own notion of the current window and nothing else.

WHY PATCH THE CLASS RATHER THAN ``app.state``. 91 test modules call ``create_app``, and
some build extra apps mid-test, so there is no single construction site to intercept. The
default argument ``now: Callable[[], float] = time.time`` binds at class-definition time,
which also rules out patching the module's ``time`` afterwards. Wrapping ``__init__`` is
what actually covers every instance, including ones built by helpers this file has never
heard of.

WHY A DISTINCT WINDOW PER TEST rather than one frozen instant for the session. A single
shared bucket would make every test in the run share one counter key, so a limiter test
would start already-incremented by whatever ran before it — trading a boundary race for
cross-test contamination, which is worse because it is order-dependent. Each test gets its
own far-future bucket derived from its node id: deterministic, unique across xdist workers
without coordination, and the keys still carry the limiter's own 60s EXPIRE.

An explicit ``now=`` passed by a caller always wins — a test that wants to drive the window
deliberately (to prove a rollover, say) keeps full control.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Iterator
from typing import Any

# Far enough ahead that a pinned bucket can never collide with a bucket some other code
# path derived from the real clock. Arbitrary but fixed: 2100-01-01T00:00:00Z.
_BASE_EPOCH = 4102444800

# Buckets are 60s. Landing mid-bucket means even an off-by-one in a limiter's own
# arithmetic cannot roll the window during a test.
_WINDOW = 60
_MID_BUCKET_OFFSET = _WINDOW // 2

# Spread of distinct buckets. Large enough that node-id collisions are vanishingly rare,
# and a collision would only reunite two tests in one bucket — the pre-existing situation,
# not a new failure mode.
_BUCKET_SPACE = 1_000_000


def frozen_instant_for(node_id: str) -> float:
    """A stable mid-bucket instant unique to this test."""
    bucket = zlib.crc32(node_id.encode()) % _BUCKET_SPACE
    return float(_BASE_EPOCH + bucket * _WINDOW + _MID_BUCKET_OFFSET)


def _limiter_classes() -> list[type[Any]]:
    """The fixed-window limiters — those bucketing on floor(now / WINDOW).

    Imported lazily and listed explicitly rather than discovered, so adding a limiter is a
    deliberate act. `RedisLuaRateLimiter` is NOT here: it is a SLIDING window over
    millisecond zset scores, so it has no boundary to straddle and pinning it would freeze
    the very timestamps it sorts by.
    """
    from gateway.access_requests.infrastructure.rate_limiter import AccessRequestIpRateLimiter
    from gateway.agent_oauth.infrastructure.ip_rate_limiter import AgentOAuthIpRateLimiter
    from gateway.domain_capture.infrastructure.rate_limiter import DomainClaimRateLimiter
    from gateway.keys.infrastructure.mint_rate_limiter import PlaygroundMintRateLimiter
    from gateway.proxy.infrastructure.redis_limit_gate import RedisDeploymentLimitGate
    from gateway.scim.infrastructure.rate_limiter import ScimTokenRateLimiter
    from gateway.tenants.infrastructure.invite_public_rate_limiter import InvitePublicRateLimiter

    return [
        AccessRequestIpRateLimiter,
        AgentOAuthIpRateLimiter,
        DomainClaimRateLimiter,
        InvitePublicRateLimiter,
        PlaygroundMintRateLimiter,
        RedisDeploymentLimitGate,
        ScimTokenRateLimiter,
    ]


def pin_fixed_windows(node_id: str) -> Iterator[None]:
    """Context manager body: pin, yield, restore.

    Restoration matters — leaving a patched `__init__` behind would silently pin every
    later test in the same worker, which is exactly the kind of invisible global state
    this file exists to avoid.
    """
    instant = frozen_instant_for(node_id)
    clock: Callable[[], float] = lambda: instant  # noqa: E731 — one expression, named for clarity
    originals: list[tuple[type[Any], Any]] = []

    for cls in _limiter_classes():
        original_init = cls.__init__
        originals.append((cls, original_init))

        def patched_init(
            self: Any,
            *args: Any,
            _original: Any = original_init,
            **kwargs: Any,
        ) -> None:
            caller_supplied_a_clock = "now" in kwargs
            _original(self, *args, **kwargs)
            if not caller_supplied_a_clock:
                self._now = clock  # noqa: SLF001 — the injected seam, set post-construction

        cls.__init__ = patched_init  # type: ignore[method-assign]

    try:
        yield
    finally:
        for cls, original_init in originals:
            cls.__init__ = original_init  # type: ignore[method-assign]
