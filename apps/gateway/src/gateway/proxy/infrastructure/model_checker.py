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
          SELECT m.active, COALESCE(tmo.enabled, true) AS tenant_enabled
          FROM models m
          LEFT JOIN tenant_model_overrides tmo
            ON tmo.model_id = m.id AND tmo.tenant_id = :tenant_id
          WHERE m.id = :model_id

        ACTIVE          — catalog active=true AND tenant_enabled=true.
        UNKNOWN         — model absent from catalog OR catalog active=false.
        TENANT_DISABLED — catalog active=true AND tenant override enabled=false.
        """
        stmt = (
            select(
                ModelRow.active,
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

        # COALESCE(tmo.enabled, true): None means no override row → default enabled
        tenant_enabled = row.enabled if row.enabled is not None else True
        if not tenant_enabled:
            return ModelAccess.TENANT_DISABLED

        return ModelAccess.ACTIVE
