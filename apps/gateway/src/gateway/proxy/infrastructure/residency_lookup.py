"""SqlAlchemyResidencyLookup — infra adapter for the ResidencyLookup port.

Additive extension @ residency-policy TASK.md §3 (FROZEN @ v2).

Constructed ONCE at app startup with a sessionmaker (mirrors
DbTenantModelPresetStore / DbTenantProviderKeyStore: "constructs its own session per
call, scoped to the session it opens" — see main.py's DbTenantModelPresetStore
docstring precedent). Every method opens+closes its own short-lived session, so this
ONE instance is shared safely across every concurrent request AND across both
enforcement tiers (Tier 1 governance checks, Tier 2 router dial-constraint filter) —
and every read is FRESH per-call (mirrors
gateway.tenants.application.retention_policy.raise_if_zdr's "no caching beyond this
one SELECT" contract, M5).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.catalog.infrastructure.orm import ModelRow


class SqlAlchemyResidencyLookup:
    """ResidencyLookup port implementation backed by the `tenants` and `models` tables."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def tenant_pin(self, tenant_id: uuid.UUID) -> str | None:
        """Fresh per-call read of tenants.residency_region — never cached."""
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    text("SELECT residency_region FROM tenants WHERE id = :tid"),
                    {"tid": str(tenant_id)},
                )
            ).first()
            return row[0] if row is not None else None

    async def regions_for(self, model_ids: list[str]) -> dict[str, str]:
        """Fresh per-call {model_id: region} lookup for the given catalog ids.

        An id absent from the returned dict (unknown to the catalog) is treated by
        the caller as NEVER eligible for a specific pin — fail-closed (M6).
        """
        if not model_ids:
            return {}
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(ModelRow.id, ModelRow.region).where(ModelRow.id.in_(model_ids))
                )
            ).all()
            return {row[0]: row[1] for row in rows}
