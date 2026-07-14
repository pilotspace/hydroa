"""Read-only keyset repository over usage_records (art12-record-keeping-preset TASK.md §3,
FROZEN @ v1).

usage_records currently has no repository class of its own — the only existing reads are
`usage/api/router.py:get_usage` (raw ``text()``, 50-newest + aggregates) and the aggregate
windows (`get_spend`, `get_cost_by_tag`, `get_reconciliation`). This is the FIRST bounded,
paginated, period-filterable read over `usage_records` for an arbitrary period — added as an
INTERNAL-only method for the Art. 12 bundle's `usage_lineage` section. No standalone public
``/admin/usage/export`` route is added here (recorded as a follow-up change-request, TASK.md
§1 Issue #1) — this repository is not imported by any router other than
``compliance/api/router.py``.

Mirrors ``AuditRepository.list_for_tenant_keyset`` / ``LogsRepository.list_for_tenant_keyset``
byte-for-shape: same decomposed OR/AND keyset predicate over ``(created_at, id) DESC``
(``tuple_()``'s stub typing rejects plain literal operands under strict pyright), same
``limit+1``-fetch has_more derivation left to the caller.

``UsageLineageRow`` is a small local read-projection dataclass (not a `usage/domain/`
entity) — the `usage/` bounded context has no `domain/entities.py` today (its own reads are
raw ``text()`` projections), and this repository is consumed by exactly one caller
(`compliance/api/router.py`), so the projection lives beside the query it serves rather than
introducing a new domain module for a single internal call site.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.usage.infrastructure.orm import UsageRecordRow


@dataclass(frozen=True, slots=True)
class UsageLineageRow:
    """One usage_records row, projected for the Art. 12 bundle's usage_lineage section."""

    id: uuid.UUID
    key_id: uuid.UUID
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    cost_basis: str
    usage_source: str
    tier_served: str
    status: int
    created_at: datetime


class UsageRepository:
    """Read-only repository over usage_records. Exposes no write/update/delete method."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant_keyset(
        self,
        tenant_id: uuid.UUID,
        *,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[UsageLineageRow]:
        """Keyset page over (created_at, id) DESC; caller fetches limit+1 to derive has_more.

        Append-safe under concurrent writes for the identical structural reason as
        ``AuditRepository.list_for_tenant_keyset`` (the keyset predicate only ever walks
        toward smaller (created_at, id) pairs).
        """
        stmt = (
            select(UsageRecordRow)
            .where(UsageRecordRow.tenant_id == tenant_id)
            .order_by(desc(UsageRecordRow.created_at), desc(UsageRecordRow.id))
            .limit(limit)
        )
        if since is not None:
            stmt = stmt.where(UsageRecordRow.created_at >= since)
        if until is not None:
            stmt = stmt.where(UsageRecordRow.created_at <= until)
        if cursor is not None:
            cursor_created_at, cursor_id = cursor
            # Decomposed OR/AND form of `(created_at, id) < (cursor_created_at, cursor_id)`
            # — mirrors AuditRepository.list_for_tenant_keyset / LogsRepository verbatim
            # (tuple_()'s stub typing rejects plain literal operands under strict pyright).
            stmt = stmt.where(
                or_(
                    UsageRecordRow.created_at < cursor_created_at,
                    and_(
                        UsageRecordRow.created_at == cursor_created_at,
                        UsageRecordRow.id < cursor_id,
                    ),
                )
            )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [_row_to_entity(r) for r in rows]


def _row_to_entity(row: UsageRecordRow) -> UsageLineageRow:
    return UsageLineageRow(
        id=row.id,
        key_id=row.key_id,
        model_id=row.model_id,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        cost_usd=row.cost_usd,
        cost_basis=row.cost_basis,
        usage_source=row.usage_source,
        tier_served=row.tier_served,
        status=row.status,
        created_at=row.created_at,
    )
