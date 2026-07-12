"""RetentionZdrPort — adapts the tenant-retention-zdr sibling task's published
`tenants.application.retention_policy.is_zdr(session, tenant_id)` read to the FROZEN
`ZdrOverridePort` Protocol shape (`is_zdr(tenant_id) -> bool`, no `session` parameter).

payload-capture-store verify fix (2026-07-10): this is the cross-milestone seam that
was never connected — `main.py` previously wired the permissive `AlwaysAllowCapture`
(always False) as the PRODUCTION default instead of this adapter, making the entire
ZDR fail-closed contract for payload capture dead code for any tenant that had
genuinely enabled ZDR via the real, already-shipped `tenants.zdr_enabled` column.

Design-for-failure: opens its own session per call via `session_factory()` (the same
per-call-session DI shape `logs/application/capture_writer.py:_insert` already uses for
other fire-and-forget app.state adapters), bounded by an explicit `asyncio.wait_for`
timeout. NEVER raises — any internal failure (DB error, connection-pool exhaustion,
timeout) is caught and mapped to True, matching `ZdrOverridePort.is_zdr`'s own
fail-closed contract (assume ZDR on doubt) and providing a second layer of defense
behind `SqlAlchemyPayloadCapture.capture()`'s own try/except-fail-closed wrapper.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.tenants.application.retention_policy import is_zdr as _query_is_zdr

_log = logging.getLogger(__name__)


class RetentionZdrPort:
    """ZdrOverridePort adapter backed by the real `tenants.zdr_enabled` column."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        timeout_seconds: float = 3.0,
    ) -> None:
        self._session_factory = session_factory
        self._timeout_seconds = timeout_seconds

    async def is_zdr(self, tenant_id: uuid.UUID) -> bool:
        """Fresh per-call read of tenants.zdr_enabled, bounded by timeout_seconds.

        Fail-CLOSED (returns True) on any error OR timeout — never raises, never hangs
        past its configured bound.
        """
        try:
            return await asyncio.wait_for(self._query(tenant_id), timeout=self._timeout_seconds)
        except Exception as exc:
            _log.warning(
                "RetentionZdrPort: is_zdr check failed or timed out after %.2fs "
                "(fail-CLOSED, assuming ZDR)",
                self._timeout_seconds,
                exc_info=exc,
                extra={"tenant_id": str(tenant_id)},
            )
            return True

    async def _query(self, tenant_id: uuid.UUID) -> bool:
        async with self._session_factory() as session:
            return await _query_is_zdr(session, tenant_id)


__all__ = ["RetentionZdrPort"]
