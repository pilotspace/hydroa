"""The bidirectional realtime relay pump (v52 realtime-relay-port).

Drives a full-duplex voice session between a `RealtimeClientTransport` (the
browser side) and a `RealtimeRelaySession` (a provider adapter). The pump depends
ONLY on those Protocols — never a concrete provider or WebSocket.

Design-for-failure (Tin's freeze — NO send queue; direct awaited relay):
  - connect is guarded by a CircuitBreaker and bounded by a timeout → 4503.
  - two cancel-safe relay tasks (client→provider, provider→client); the FIRST to
    finish wins, the sibling is cancelled.
  - each provider send is timeout-bounded → a hang raises ProviderUnavailable → 4503.
  - no client frame within the idle timeout → 4408.
  - a provider `events()` raise → an {error} frame then close 1011.
  - teardown runs exactly once in `finally`: send any error frame, close the
    transport with a typed code, aclose the provider — all idempotent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from gateway.core.config import Settings
from gateway.proxy.domain.errors import CircuitOpenError
from gateway.proxy.domain.realtime_relay import (
    RealtimeClientTransport,
    RealtimeProviderUnavailableError,
    RealtimeRelayClosed,
    RealtimeRelaySession,
)
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.rate_limits.application.passthrough import PassthroughBandwidthBucket
from gateway.rate_limits.domain.errors import BandwidthExhaustedError
from gateway.rate_limits.domain.ports import BandwidthBucket

_log = logging.getLogger(__name__)

# Close codes (pump → transport.close)
_CODE_NORMAL = 1000
_CODE_PROVIDER_ERROR = 1011
_CODE_PROVIDER_UNAVAILABLE = 4503
_CODE_IDLE = 4408
# realtime-relay-governance (B2 TASK.md §3, M5): a self-imposed pace cap is not "the
# provider is down" — kept distinct from _CODE_PROVIDER_UNAVAILABLE (4503) even though
# both mean "you're going too fast", same code family as the connect-time RATE_LIMITED.
_CODE_BANDWIDTH_EXHAUSTED = 4429


class _IdleTimeout(Exception):
    """Internal: no client frame within the idle window."""


def _error_frame(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


def _is_audio(frame: object) -> bool:
    return isinstance(frame, (bytes, bytearray))


class RelayPump:
    """Pumps frames both ways between a client transport and a provider session."""

    def __init__(
        self,
        transport: RealtimeClientTransport,
        session: RealtimeRelaySession,
        settings: Settings,
        *,
        breaker: CircuitBreaker | None = None,
        bandwidth_bucket: BandwidthBucket | None = None,
        key_id: uuid.UUID | None = None,
    ) -> None:
        self._t = transport
        self._s = session
        self._connect_timeout = settings.realtime_relay_connect_timeout_seconds
        self._idle_timeout = settings.realtime_relay_idle_timeout_seconds
        self._breaker = breaker or CircuitBreaker()
        # realtime-relay-governance (B2 TASK.md §3, M5): mirrors the existing optional
        # `breaker` param — default PassthroughBandwidthBucket() is byte-identical / zero
        # pacing when unset (matches the v36 bandwidth-token-bucket default-OFF precedent).
        self._bandwidth_bucket = bandwidth_bucket or PassthroughBandwidthBucket()
        self._bandwidth_max_wait_s = settings.bandwidth_max_wait_seconds
        self._key_id = key_id
        # Observable outcome for the endpoint's session_closed audit event (M6) — set once,
        # in _teardown, right before the transport close.
        self.close_code: int | None = None

    # -- the two relay directions -------------------------------------------

    async def _client_to_provider(self) -> None:
        """Read client frames and forward them to the provider (timeout-bounded)."""
        while True:
            try:
                async with asyncio.timeout(self._idle_timeout):
                    frame = await self._t.receive()
            except TimeoutError as exc:
                raise _IdleTimeout from exc
            except RealtimeRelayClosed:
                return  # client disconnected — a normal end of session
            if _is_audio(frame) and self._key_id is not None:
                # realtime-relay-governance (B2 TASK.md §3, M5): pace each AUDIO frame
                # (never control frames) BEFORE forwarding it. Outside the connect_timeout
                # window below — acquire()'s own bounded wait (bandwidth_max_wait_s) is a
                # separate governance surface from the provider-send timeout.
                # BandwidthExhaustedError propagates uncaught — handled by run()'s outcome
                # dispatch (distinct 4429, never the provider-unavailable 4503 path).
                # key_id is None only when the caller never threaded one through (byte-
                # identical to the Passthrough default either way — it ignores key_id).
                await self._bandwidth_bucket.acquire(
                    self._key_id, len(frame), self._bandwidth_max_wait_s
                )
            try:
                async with asyncio.timeout(self._connect_timeout):
                    if _is_audio(frame):
                        await self._s.send_audio(bytes(frame))  # type: ignore[arg-type]
                    else:
                        await self._s.send_client_event(frame)  # type: ignore[arg-type]
            except TimeoutError as exc:
                raise RealtimeProviderUnavailableError("provider send timed out") from exc

    async def _provider_to_client(self) -> None:
        """Read provider events and forward them to the client. Raises on provider error."""
        async for frame in self._s.events():
            if _is_audio(frame):
                await self._t.send_audio(bytes(frame))  # type: ignore[arg-type]
            else:
                await self._t.send_event(frame)  # type: ignore[arg-type]
        # events() exhausted → the provider ended the stream normally

    # -- orchestration ------------------------------------------------------

    async def run(self) -> None:
        # 1) breaker gate — refuse without touching the provider when OPEN
        try:
            self._breaker.guard()
        except CircuitOpenError:
            await self._teardown(_CODE_PROVIDER_UNAVAILABLE, None)
            return

        # 2) connect (timeout-bounded); any failure → provider unavailable
        try:
            async with asyncio.timeout(self._connect_timeout):
                await self._s.connect()
        except Exception:  # connect timeout / refusal / adapter error
            self._breaker.on_upstream_error()
            await self._teardown(_CODE_PROVIDER_UNAVAILABLE, None)
            return
        self._breaker.record_success()

        # 3) full-duplex pump — first task to finish wins, sibling cancelled
        c2p = asyncio.create_task(self._client_to_provider())
        p2c = asyncio.create_task(self._provider_to_client())
        code = _CODE_NORMAL
        error_frame: dict[str, Any] | None = None
        try:
            done, pending = await asyncio.wait({c2p, p2c}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                try:
                    exc = task.exception()
                except asyncio.CancelledError:
                    exc = None
                if exc is None:
                    continue
                if isinstance(exc, _IdleTimeout):
                    code = _CODE_IDLE
                elif isinstance(exc, BandwidthExhaustedError):
                    # A self-imposed pace cap is not "the provider is down" — the breaker
                    # tracks PROVIDER health, so it must stay untouched here.
                    code = _CODE_BANDWIDTH_EXHAUSTED
                elif isinstance(exc, RealtimeProviderUnavailableError):
                    code = _CODE_PROVIDER_UNAVAILABLE
                    self._breaker.on_upstream_error()
                else:
                    code = _CODE_PROVIDER_ERROR
                    error_frame = _error_frame("provider_error", str(exc))
                    self._breaker.on_upstream_error()
                break
        finally:
            await self._teardown(code, error_frame)

    async def _teardown(self, code: int, error_frame: dict[str, Any] | None) -> None:
        """Send any error frame, close the transport, aclose the provider.

        Idempotent and never raises (teardown must not mask the outcome).
        """
        self.close_code = code
        if error_frame is not None:
            try:
                await self._t.send_event(error_frame)
            except Exception:
                _log.debug("relay teardown: error-frame send failed", exc_info=True)
        try:
            await self._t.close(code)
        except Exception:
            _log.debug("relay teardown: transport close failed", exc_info=True)
        try:
            await self._s.aclose()
        except Exception:
            _log.debug("relay teardown: provider aclose failed", exc_info=True)
