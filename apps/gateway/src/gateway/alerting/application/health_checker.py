"""UpstreamHealthChecker — periodic health ping with event emission on fail/recovery.

CONTRACT (FROZEN @ health-alerting v3):
  - check_once()    → one ping + state machine + event emission
  - run_forever()   → background loop every interval_seconds
  - fail_threshold consecutive failures → emit upstream_health_fail (once per episode)
  - first success after a fail episode  → emit upstream_health_recovered (paired episode UUID)
  - Episode semantics: new UUID per failure run; cleared on recovery
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.alerting.application.event_emitter import emit_system_event
from gateway.alerting.domain.ports import UpstreamPinger

_log = logging.getLogger(__name__)

_HEALTH_CHECK_URL = "https://openrouter.ai/api/v1/models"


class UpstreamHealthChecker:
    """Runs periodic health checks; emits system events on failure/recovery episodes."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        pinger: UpstreamPinger,
        fail_threshold: int = 3,
    ) -> None:
        self._session_factory = session_factory
        self._pinger = pinger
        self._fail_threshold = fail_threshold

        # State machine
        self._consecutive_failures: int = 0
        self._episode_id: uuid.UUID | None = None  # set on new failure run; cleared on recovery
        self._fail_emitted: bool = False  # True = upstream_health_fail already emitted this episode

    async def check_once(self) -> None:
        """Run one health check cycle: ping + state machine + event emission."""
        try:
            await self._pinger.ping()
            # Success
            await self._on_success()
        except Exception as exc:
            _log.debug("health_checker: upstream ping failed: %s", exc)
            await self._on_failure()

    async def run_forever(self, *, interval_seconds: float = 60.0) -> None:
        """Background loop: check_once() every interval_seconds."""
        while True:
            try:
                await self.check_once()
            except Exception as exc:
                _log.warning(
                    "health_checker: background loop error (swallowed): %s", exc, exc_info=exc
                )
            await asyncio.sleep(interval_seconds)

    # ── state machine ─────────────────────────────────────────────────────────

    async def _on_failure(self) -> None:
        """Increment failure counter; emit event on reaching threshold (once per episode)."""
        self._consecutive_failures += 1

        if self._episode_id is None:
            # Start new failure episode
            self._episode_id = uuid.uuid4()
            self._fail_emitted = False

        if self._consecutive_failures >= self._fail_threshold and not self._fail_emitted:
            self._fail_emitted = True
            dedupe_key = f"health_fail:{self._episode_id}"
            payload = {
                "consecutive_failures": self._consecutive_failures,
                "url": _HEALTH_CHECK_URL,
            }
            _log.warning(
                "health_checker: %d consecutive failures — emitting upstream_health_fail"
                " (episode %s)",
                self._consecutive_failures,
                self._episode_id,
            )
            task = asyncio.ensure_future(
                emit_system_event(
                    self._session_factory,
                    event_type="upstream_health_fail",
                    dedupe_key=dedupe_key,
                    payload=payload,
                )
            )
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def _on_success(self) -> None:
        """Reset failure counter; emit recovery event if a fail episode was active."""
        if self._fail_emitted and self._episode_id is not None:
            # Recovery from an active fail episode
            episode_id = self._episode_id
            failures_before = self._consecutive_failures
            dedupe_key = f"health_recovered:{episode_id}"
            payload = {"recovered_after_failures": failures_before}
            _log.info(
                "health_checker: upstream recovered after %d failures (episode %s)",
                failures_before,
                episode_id,
            )
            task = asyncio.ensure_future(
                emit_system_event(
                    self._session_factory,
                    event_type="upstream_health_recovered",
                    dedupe_key=dedupe_key,
                    payload=payload,
                )
            )
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

        # Reset state
        self._consecutive_failures = 0
        self._episode_id = None
        self._fail_emitted = False
