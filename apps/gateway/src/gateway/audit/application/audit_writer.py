"""Fire-and-forget audit event writer — fail-open.

Contract (audit-log-store TASK.md §3 — FROZEN @ v1):
  record_audit(session_factory, event) — insert one AuditEvent in a SEPARATE session
  from the admin action's own transaction. Swallows ALL exceptions (failures are logged,
  never raised into the request path). Must be scheduled as a fire-and-forget task
  (asyncio.ensure_future / asyncio.create_task), never awaited on the hot path.

FAIL-OPEN:
  An audit write failure MUST NOT change the HTTP outcome of the admin action.
  The audit write uses its own session (separate from the action's transaction),
  so a rollback of the action does not affect the audit trail, and vice versa.
  If the audit write raises for any reason, the exception is caught, logged, and swallowed.

Pattern follows alert_writer.py (same fire-and-forget + swallow contract).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.audit.domain.audit_event import AuditEvent
from gateway.audit.infrastructure.audit_repository import AuditRepository

_log = logging.getLogger(__name__)


async def record_audit(
    session_factory: async_sessionmaker[AsyncSession],
    event: AuditEvent,
) -> None:
    """Insert one audit event row into audit_events — fail-open.

    Uses its own session (SEPARATE from the caller's transaction) so:
    - An admin action rollback cannot lose a committed audit event.
    - An audit write failure cannot roll back the admin action.

    Swallows ALL exceptions — failures are logged but NEVER raised.
    Schedule via asyncio.ensure_future() or asyncio.create_task(), never await directly.
    """
    try:
        async with session_factory() as session:
            repo = AuditRepository(session)
            await repo.record(event)
            await session.commit()
    except Exception as exc:
        _log.warning(
            "audit_writer: failed to persist audit event (swallowed — fail-open)",
            exc_info=exc,
            extra={
                "audit_action": event.action,
                "audit_event_id": str(event.id),
                "audit_tenant_id": str(event.tenant_id) if event.tenant_id else None,
                "audit_actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
            },
        )
