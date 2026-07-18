"""self-serve-plans-catalog TASK.md §3 (FROZEN @ v1) — pure read, no mutation.

`list_self_serve_plans` returns the self-serve UPGRADE catalog subset for a tenant:
  self_serve = true AND (audience IS NULL OR audience = account_type) AND id != current_plan_id
ordered by base_price_usd_monthly ASC, NULL first (the free tier sorts before any priced
tier). No migration, no new column — reads the `plans` table exactly as self-serve-checkout
(TASK.md §3, FROZEN @ v1) shipped it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.tenants.infrastructure.orm import PlanRow


@dataclass(frozen=True, slots=True)
class SelfServePlan:
    """One self-serve upgrade target — display metadata only, no entitlement data."""

    id: uuid.UUID
    name: str
    display_name: str
    base_price: Decimal | None


async def list_self_serve_plans(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    account_type: str | None,
    current_plan_id: uuid.UUID | None,
) -> list[SelfServePlan]:
    """SELECT PlanRow WHERE self_serve AND (audience IS NULL OR audience=account_type) AND
    id != current_plan_id, ORDER BY base_price_usd_monthly ASC NULLS FIRST.

    `account_type=None` (a legacy/grandfathered tenant with no account-type discriminator
    set) degrades to ONLY the no-audience-gate rows — `audience = NULL` never matches its own
    IS NULL check in SQL, so this Python branch mirrors that same fail-closed semantics rather
    than accidentally widening the catalog for an unset account_type.
    """
    stmt = select(PlanRow).where(PlanRow.self_serve.is_(True))
    if account_type is not None:
        stmt = stmt.where((PlanRow.audience.is_(None)) | (PlanRow.audience == account_type))
    else:
        stmt = stmt.where(PlanRow.audience.is_(None))
    if current_plan_id is not None:
        stmt = stmt.where(PlanRow.id != current_plan_id)
    stmt = stmt.order_by(PlanRow.base_price_usd_monthly.asc().nulls_first())

    async with session_factory() as session:
        rows = (await session.execute(stmt)).scalars().all()

    return [
        SelfServePlan(
            id=row.id,
            name=row.name,
            display_name=row.display_name,
            base_price=row.base_price_usd_monthly,
        )
        for row in rows
    ]


__all__ = ["SelfServePlan", "list_self_serve_plans"]
