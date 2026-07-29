"""SQLAlchemy repositories over tenant_report_schedules / compliance_report_runs
(compliance-report-center TASK.md §3 — FROZEN @ v1).

ReportScheduleRepository: single-row-per-tenant policy (get/upsert/delete) — mirrors
  tenants/api/retention_policy_router.py's own GET/PUT idiom.
ComplianceReportRunRepository: append-only evidence log (idempotent insert / tenant-
  scoped keyset list / tenant-scoped get-by-id) — mirrors billing/infrastructure/
  invoice_repository.py's own read-only-plus-one-append-path shape.

INVARIANT (mirrors artifacts/infrastructure/repository.py's own documented rule):
  every read method takes tenant_id and filters on it; a cross-tenant or unknown id
  always returns None/empty — NEVER 403. The router maps None -> 404, zero data leak.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.compliance.infrastructure.orm import ComplianceReportRunRow, TenantReportScheduleRow


class ReportScheduleRepository:
    """Single-row-per-tenant Art. 12 monthly-generation schedule POLICY."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: uuid.UUID) -> TenantReportScheduleRow | None:
        stmt = select(TenantReportScheduleRow).where(TenantReportScheduleRow.tenant_id == tenant_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        tenant_id: uuid.UUID,
        enabled: bool,
        day_of_month: int,
        created_by: uuid.UUID | None,
        next_run_at: datetime | None,
    ) -> TenantReportScheduleRow:
        """Insert-or-update the tenant's one schedule row; returns the resulting row."""
        existing = await self.get(tenant_id)
        if existing is None:
            row = TenantReportScheduleRow(
                tenant_id=tenant_id,
                enabled=enabled,
                day_of_month=day_of_month,
                created_by=created_by,
                next_run_at=next_run_at,
            )
            self._session.add(row)
            await self._session.flush()
            await self._session.refresh(row)
            return row

        existing.enabled = enabled
        existing.day_of_month = day_of_month
        existing.next_run_at = next_run_at
        if created_by is not None:
            existing.created_by = created_by
        await self._session.flush()
        await self._session.refresh(existing)
        return existing

    async def delete(self, tenant_id: uuid.UUID) -> bool:
        """Hard-delete the tenant's schedule row. Returns True iff a row existed."""
        result = await self._session.execute(
            text("DELETE FROM tenant_report_schedules WHERE tenant_id = :tid RETURNING tenant_id"),
            {"tid": str(tenant_id)},
        )
        return result.first() is not None


class ComplianceReportRunRepository:
    """Append-only evidence log of scheduler-generated Art. 12 bundles."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant_keyset(
        self,
        tenant_id: uuid.UUID,
        *,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[ComplianceReportRunRow]:
        """Keyset page over (generated_at, id) DESC; caller fetches limit+1 to derive
        has_more — mirrors AuditRepository.list_for_tenant_keyset's own idiom."""
        stmt = select(ComplianceReportRunRow).where(ComplianceReportRunRow.tenant_id == tenant_id)
        if cursor is not None:
            cursor_generated_at, cursor_id = cursor
            stmt = stmt.where(
                (ComplianceReportRunRow.generated_at < cursor_generated_at)
                | (
                    (ComplianceReportRunRow.generated_at == cursor_generated_at)
                    & (ComplianceReportRunRow.id < cursor_id)
                )
            )
        stmt = stmt.order_by(
            ComplianceReportRunRow.generated_at.desc(), ComplianceReportRunRow.id.desc()
        ).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_active(
        self, *, tenant_id: uuid.UUID, report_id: uuid.UUID
    ) -> ComplianceReportRunRow | None:
        """Tenant-scoped row lookup — returns None for unknown/cross-tenant (never a
        leak); the router maps None -> 404, NEVER 403 (M18/R11)."""
        stmt = select(ComplianceReportRunRow).where(
            ComplianceReportRunRow.id == report_id,
            ComplianceReportRunRow.tenant_id == tenant_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
