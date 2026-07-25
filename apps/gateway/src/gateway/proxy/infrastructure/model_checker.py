"""Infrastructure adapter: ModelChecker → queries catalog ORM.

Additive extension (model-mgmt TASK.md §3):
  check_for_tenant — single LEFT JOIN query piggybacking on the existing ModelChecker
  hot-path hit; adds sub-millisecond indexed lookup (PK on tenant_model_overrides).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.infrastructure.orm import ModelRow, TenantModelOverrideRow
from gateway.proxy.domain.ports import ModelAccess


class SqlAlchemyModelChecker:
    """Checks model availability by querying the models table.

    A new instance is created per-request (session-scoped).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_active(self, model_id: str) -> bool:
        """Return True iff models.id = model_id AND models.active = true.

        FROZEN — signature must not change; frozen proxy-completions fakes depend on it.
        """
        row = (
            await self._session.execute(select(ModelRow.active).where(ModelRow.id == model_id))
        ).scalar_one_or_none()
        if row is None:
            return False
        return bool(row)

    async def check_for_tenant(self, model_id: str, tenant_id: uuid.UUID) -> ModelAccess:
        """Return tri-state access enum for (model_id, tenant_id).

        Single LEFT JOIN query:
          SELECT m.active, m.tenant_id, COALESCE(tmo.enabled, true) AS tenant_enabled
          FROM models m
          LEFT JOIN tenant_model_overrides tmo
            ON tmo.model_id = m.id AND tmo.tenant_id = :tenant_id
          WHERE m.id = :model_id

        ACTIVE          — catalog active=true AND tenant_enabled=true AND (m.tenant_id
                           IS NULL OR m.tenant_id = :tenant_id).
        UNKNOWN         — model absent from catalog OR catalog active=false OR the row
                           is owned by a DIFFERENT tenant (finetune-model-registry
                           PLAN.md §3 M4 — a tenant-owned row is invisible to every
                           other tenant, byte-identical to a nonexistent model; never a
                           distinguishable 403/leak).
        TENANT_DISABLED — catalog active=true AND tenant override enabled=false.
        """
        stmt = (
            select(
                ModelRow.active,
                ModelRow.tenant_id,
                TenantModelOverrideRow.enabled,
            )
            .outerjoin(
                TenantModelOverrideRow,
                (TenantModelOverrideRow.model_id == ModelRow.id)
                & (TenantModelOverrideRow.tenant_id == tenant_id),
            )
            .where(ModelRow.id == model_id)
        )
        row = (await self._session.execute(stmt)).one_or_none()

        if row is None or not bool(row.active):
            return ModelAccess.UNKNOWN

        if row.tenant_id is not None and row.tenant_id != tenant_id:
            return ModelAccess.UNKNOWN

        # COALESCE(tmo.enabled, true): None means no override row → default enabled
        tenant_enabled = row.enabled if row.enabled is not None else True
        if not tenant_enabled:
            return ModelAccess.TENANT_DISABLED

        return ModelAccess.ACTIVE
