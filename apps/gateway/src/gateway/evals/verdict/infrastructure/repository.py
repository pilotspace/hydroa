"""SQLAlchemy adapter for the EvalBaselineStore port — baseline-and-verdict §3 (M3, M6).

Sessionmaker-based (fresh session per op, commit-per-write), mirroring ``SqlAlchemyEvalRunStore``
so a pin is durable the instant it returns and a FRESH store instance reads it (M6 — the
redeploy proxy). ``pin_baseline`` is an idempotent upsert on ``UNIQUE(eval_set_id)`` (M3): a
re-pin UPDATEs the single row and moves ``pinned_at``, never inserts a second.

INVARIANT (M5): ``get_baseline`` filters on ``tenant_id`` in the resolving query — an absent or
cross-tenant set returns None. Callers only ever pin AFTER resolving the set + run in tenant
scope, so the write path is tenant-safe by construction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.evals.verdict.infrastructure.orm import EvalBaselineRow


class SqlAlchemyEvalBaselineStore:
    """EvalBaselineStore adapter over an async_sessionmaker (fresh session per operation)."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def pin_baseline(
        self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID, run_id: uuid.UUID
    ) -> EvalBaselineRow:
        """Upsert the set's baseline to ``run_id`` (idempotent on eval_set_id, M3)."""
        async with self._sessionmaker() as session:
            stmt = (
                pg_insert(EvalBaselineRow)
                .values(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    eval_set_id=eval_set_id,
                    run_id=run_id,
                )
                .on_conflict_do_update(
                    index_elements=["eval_set_id"],
                    set_={"run_id": run_id, "tenant_id": tenant_id, "pinned_at": func.now()},
                )
            )
            await session.execute(stmt)
            await session.commit()
            # Re-read the (possibly updated) row so the caller gets the committed pinned_at.
            row = (
                await session.execute(
                    select(EvalBaselineRow).where(EvalBaselineRow.eval_set_id == eval_set_id)
                )
            ).scalar_one()
            session.expunge(row)
            return row

    async def get_baseline(
        self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID
    ) -> EvalBaselineRow | None:
        """The set's pinned baseline for this tenant, or None (absent/cross-tenant, M5/M6)."""
        async with self._sessionmaker() as session:
            stmt = select(EvalBaselineRow).where(
                EvalBaselineRow.eval_set_id == eval_set_id,
                EvalBaselineRow.tenant_id == tenant_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is not None:
                session.expunge(row)
            return row
