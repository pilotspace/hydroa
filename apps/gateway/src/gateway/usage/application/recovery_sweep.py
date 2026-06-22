"""OpenRouterRecoverySweeper — the periodic backstop for disconnect cost-recovery (v30 t6.3).

Inline recovery (t6.2c) is best-effort: it can be cancelled at teardown or skipped while the
knob was off. THIS sweep is the reliable backstop — it periodically scans the append-only
ledger for flushed client_disconnect rows that carry a provider_generation_id but still have
NO openrouter_recovered sibling, gates each to provider 'openrouter', and calls the (t6.2b)
recovery service for each. Idempotent with the inline path via the deterministic
correction-row id, so a row the inline path already recovered is excluded by the NOT-EXISTS.

Mirrors ReconciliationDriftChecker: check-once + run_forever loop that swallows all errors,
default-OFF via a config knob. READ-only against the ledger — recover() does its own append.
Bounded: a max-age window (permanent-404 rows age out) AND a per-cycle batch limit (no
full-table scan under a backlog). NEVER raises into the loop.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_log = logging.getLogger(__name__)

_OPENROUTER = "openrouter"

# Candidate scan: flushed client_disconnect rows carrying a generation id, newer than the
# max-age window, with NO openrouter_recovered sibling. Backed by ix_usage_records_gen_recovery.
_CANDIDATE_SQL = text(
    """
    SELECT DISTINCT u.tenant_id, u.key_id, u.model_id, u.provider_generation_id
      FROM usage_records u
     WHERE u.provider_generation_id IS NOT NULL
       AND u.usage_source = 'client_disconnect'
       AND u.created_at >= :since
       AND NOT EXISTS (
             SELECT 1 FROM usage_records r
              WHERE r.provider_generation_id = u.provider_generation_id
                AND r.usage_source = 'openrouter_recovered')
     LIMIT :batch
    """
)


def should_start_recovery_sweep(interval_seconds: int) -> bool:
    """The default-OFF guard: start the periodic sweep ONLY when the interval is > 0.

    A zero (or negative) interval leaves the backstop disabled — the safe default — so the
    lifespan never wires a sweeper an operator did not explicitly turn on. (The lifespan
    additionally requires the recovery service to be wired.)
    """
    return interval_seconds > 0


class _RecoveryService(Protocol):
    async def recover(
        self,
        *,
        tenant_id: Any,
        key_id: Any,
        model: str,
        provider_generation_id: str,
    ) -> Any: ...


class _ProviderResolver(Protocol):
    async def provider_for(self, model_id: str) -> str: ...


class OpenRouterRecoverySweeper:
    """Periodically recover authoritative cost for unrecovered OpenRouter disconnect rows."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        recovery_service: _RecoveryService,
        provider_resolver: _ProviderResolver,
        batch_size: int = 100,
        max_age_hours: int = 24,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._recovery_service = recovery_service
        self._provider_resolver = provider_resolver
        self._batch_size = batch_size
        self._max_age_hours = max_age_hours
        self._sleep = sleep
        self._monotonic = monotonic

    async def sweep_once(self) -> int:
        """One cycle: recover() every in-window unrecovered OpenRouter disconnect row.

        Returns the number of recover() attempts made. NEVER raises — every failure
        (DB, provider lookup, recover()) is swallowed and logged so the loop survives.
        """
        attempts = 0
        try:
            # asyncpg binds usage_records.created_at as NAIVE UTC (the column is
            # TIMESTAMP WITHOUT TIME ZONE) — drop tzinfo so the comparison binds, mirroring
            # reconciliation._as_naive_utc.
            since = (
                datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=self._max_age_hours)
            ).replace(tzinfo=None)
            async with self._session_factory() as session:
                result = await session.execute(
                    _CANDIDATE_SQL, {"since": since, "batch": self._batch_size}
                )
                candidates = result.all()

            # Observability: a full batch means there may be a backlog the next cycle picks
            # up — never a silent cap (the next interval drains the remainder).
            if len(candidates) >= self._batch_size:
                _log.info(
                    "recovery_sweep: batch limit (%d) reached — backlog deferred to next cycle",
                    self._batch_size,
                )

            # Resolve providers once per distinct model this cycle (cheap, bounded).
            provider_cache: dict[str, str] = {}
            for tenant_id, key_id, model_id, gid in candidates:
                try:
                    provider = provider_cache.get(model_id)
                    if provider is None:
                        provider = await self._provider_resolver.provider_for(model_id)
                        provider_cache[model_id] = provider
                    if provider != _OPENROUTER:
                        continue
                    await self._recovery_service.recover(
                        tenant_id=tenant_id,
                        key_id=key_id,
                        model=model_id,
                        provider_generation_id=gid,
                    )
                    attempts += 1
                except Exception as exc:  # one bad row must not abort the cycle
                    _log.warning(
                        "recovery_sweep: row recover() failed (swallowed) gid=%s: %s",
                        gid,
                        exc,
                        exc_info=exc,
                    )
        except Exception as exc:
            _log.warning("recovery_sweep: sweep cycle failed (swallowed): %s", exc, exc_info=exc)
        return attempts

    async def run_forever(self, *, interval_seconds: float) -> None:
        """Background loop: sweep_once() every interval_seconds. Swallows all errors."""
        while True:
            try:
                recovered = await self.sweep_once()
                if recovered:
                    _log.info("recovery_sweep: attempted recovery for %d row(s)", recovered)
            except Exception as exc:  # defence in depth — sweep_once already swallows
                _log.warning(
                    "recovery_sweep: background loop error (swallowed): %s", exc, exc_info=exc
                )
            await self._sleep(interval_seconds)
