"""Red suite: RelayPump bandwidth pacing (B2 TASK.md §2, M5).

RED until RelayPump.__init__ accepts bandwidth_bucket/key_id and _client_to_provider
paces audio frames through it. Pure-unit: FAKE provider session + FAKE client
transport (same fakes as test_relay_pump.py), no WebSocket and no network.
"""

from __future__ import annotations

import asyncio
import uuid

from gateway.core.config import Settings
from gateway.proxy.application.realtime_relay_pump import RelayPump
from gateway.rate_limits.domain.errors import BandwidthExhaustedError
from gateway.rate_limits.domain.ports import BandwidthGrant

from .test_relay_pump import FakeClientTransport, FakeProviderSession


def _settings(**over) -> Settings:
    base = dict(
        realtime_relay_connect_timeout_seconds=0.1,
        realtime_relay_idle_timeout_seconds=0.2,
    )
    base.update(over)
    return Settings(**base)


class FakeBandwidthBucket:
    """Scripted BandwidthBucket: grants until `fail_after` acquisitions, then raises."""

    def __init__(self, *, fail_after: int | None = None) -> None:
        self._fail_after = fail_after
        self.calls: list[tuple[uuid.UUID, int, float]] = []

    async def acquire(self, key_id: uuid.UUID, estimated_tokens: int, max_wait_s: float):
        self.calls.append((key_id, estimated_tokens, max_wait_s))
        if self._fail_after is not None and len(self.calls) > self._fail_after:
            raise BandwidthExhaustedError(str(key_id), estimated_tokens, 1)
        return BandwidthGrant(key_id=key_id, consumed=estimated_tokens, waited_s=0.0)

    async def try_consume(self, key_id, tokens):  # pragma: no cover - unused here
        raise NotImplementedError

    async def reconcile(self, key_id, grant, real_tokens):  # pragma: no cover - unused here
        return

    async def level(self, key_id):  # pragma: no cover - unused here
        return 0


async def test_bandwidth_grant_within_cap_forwards_frame_unchanged() -> None:
    key_id = uuid.uuid4()
    bucket = FakeBandwidthBucket()
    session = FakeProviderSession(block_events=True)
    transport = FakeClientTransport(frames=[b"\x01mic"], disconnect_after=True)
    pump = RelayPump(
        transport,
        session,
        _settings(bandwidth_max_wait_seconds=1.0),
        bandwidth_bucket=bucket,
        key_id=key_id,
    )
    await asyncio.wait_for(pump.run(), timeout=2.0)

    assert session.sent_audio == [b"\x01mic"]
    assert bucket.calls == [(key_id, len(b"\x01mic"), 1.0)]


async def test_bandwidth_exhausted_mid_session_closes_4429() -> None:
    key_id = uuid.uuid4()
    bucket = FakeBandwidthBucket(fail_after=0)  # every acquire raises
    session = FakeProviderSession(block_events=True)
    transport = FakeClientTransport(frames=[b"\x02mic"], idle=True)
    pump = RelayPump(
        transport,
        session,
        _settings(bandwidth_max_wait_seconds=0.5),
        bandwidth_bucket=bucket,
        key_id=key_id,
    )
    await asyncio.wait_for(pump.run(), timeout=2.0)

    assert transport.close_code == 4429
    assert transport.close_code != 4503, "bandwidth exhaustion must be distinguishable from 4503"


async def test_bandwidth_bucket_absent_is_byte_identical_to_today() -> None:
    """No bandwidth_bucket configured -> default PassthroughBandwidthBucket -> no pacing."""
    session = FakeProviderSession(
        events=[{"type": "session.created"}, b"\x01audio", {"type": "response.done"}]
    )
    transport = FakeClientTransport(idle=True)
    pump = RelayPump(transport, session, _settings())
    await asyncio.wait_for(pump.run(), timeout=2.0)

    assert transport.sent_audio == [b"\x01audio"]
    assert transport.close_code == 1000


async def test_bandwidth_exhausted_does_not_trip_the_provider_breaker() -> None:
    """A self-imposed pace cap is not 'the provider is down' — breaker must stay closed."""
    from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

    key_id = uuid.uuid4()
    bucket = FakeBandwidthBucket(fail_after=0)
    session = FakeProviderSession(block_events=True)
    transport = FakeClientTransport(frames=[b"\x03mic"], idle=True)
    breaker = CircuitBreaker()
    pump = RelayPump(
        transport,
        session,
        _settings(bandwidth_max_wait_seconds=0.5),
        breaker=breaker,
        bandwidth_bucket=bucket,
        key_id=key_id,
    )
    await asyncio.wait_for(pump.run(), timeout=2.0)

    assert transport.close_code == 4429
    assert breaker.is_open() is False
