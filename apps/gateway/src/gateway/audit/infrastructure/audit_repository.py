"""SQLAlchemy implementation of the AuditLog port.

Contract (audit-log-store TASK.md §3 — FROZEN @ v1):
  - record(event) — INSERT one AuditEventRow; never UPDATE/DELETE.
  - list_for_tenant(tenant_id, limit, before) — SELECT newest-first, tenant-scoped.

IMMUTABILITY ENFORCEMENT:
  - Application layer: this class exposes NO update/delete/patch/remove/mutate methods.
  - DB layer: migration f2a4c6e8b0d3 installs a BEFORE UPDATE OR DELETE trigger on
    audit_events. UPDATE always RAISEs; DELETE RAISEs unless the txn-scoped GUC
    app.audit_purge='on' is set (only the retention sweeper does so, inside its own
    transaction). Any stray mutation fails loudly. Verified by test_db_enforced_immutability.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.domain.audit_event import AuditEvent
from gateway.audit.infrastructure.audit_events_orm import AuditEventRow


class AuditRepository:
    """Append-only SqlAlchemy repository for audit_events.

    Implements the AuditLog port.
    Deliberately exposes NO update, delete, patch, remove, or mutate methods.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, event: AuditEvent) -> None:
        """INSERT one audit event row.

        Never updates or deletes. The session is NOT committed here —
        the caller (or record_audit writer) manages the commit.
        """
        row = AuditEventRow(
            id=event.id,
            tenant_id=event.tenant_id,
            actor_user_id=event.actor_user_id,
            actor_email=event.actor_email,
            action=event.action,
            target_type=event.target_type,
            target_id=event.target_id,
            result=event.result,
            event_metadata=event.metadata,
            # created_at is DB-defaulted (server_default=now())
        )
        self._session.add(row)

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[AuditEvent]:
        """Return audit events for a tenant, newest-first, bounded by limit.

        before: if provided, only return events created before this timestamp.
        """
        stmt = (
            select(AuditEventRow)
            .where(AuditEventRow.tenant_id == tenant_id)
            .order_by(desc(AuditEventRow.created_at))
            .limit(limit)
        )
        if before is not None:
            stmt = stmt.where(AuditEventRow.created_at < before)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [_row_to_event(r) for r in rows]


    async def count_for_tenant(self, tenant_id: uuid.UUID) -> int:
        """Return total count of audit events for a tenant (for pagination envelopes)."""
        result = await self._session.execute(
            select(func.count())
            .select_from(AuditEventRow)
            .where(AuditEventRow.tenant_id == tenant_id)
        )
        return int(result.scalar() or 0)

    async def list_for_tenant_paged(
        self,
        tenant_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Return audit events for a tenant, newest-first, with offset-based pagination."""
        stmt = (
            select(AuditEventRow)
            .where(AuditEventRow.tenant_id == tenant_id)
            .order_by(desc(AuditEventRow.created_at), desc(AuditEventRow.id))
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [_row_to_event(r) for r in rows]


def _row_to_event(row: AuditEventRow) -> AuditEvent:
    """Convert an ORM row to the domain AuditEvent entity."""
    # System events (tenant_id=None) may have actor_user_id=None — bypass the invariant
    # by constructing directly. For tenant-scoped events, actor_user_id is guaranteed non-null
    # because the DB constraint was satisfied at write time.
    return AuditEvent(
        id=row.id,
        tenant_id=row.tenant_id,
        actor_user_id=row.actor_user_id,
        actor_email=row.actor_email,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        result=row.result,
        metadata=dict(row.event_metadata) if row.event_metadata else {},
        created_at=row.created_at,
    )
