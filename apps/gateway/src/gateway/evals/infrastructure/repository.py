"""SQLAlchemy adapter for the EvalStore port (eval-set-store PLAN.md §3, M6).

INVARIANT: every method takes ``tenant_id`` and every query filters on it. A cross-tenant or
unknown id returns None/[] — never a distinguishable error (M4/M5); the router maps that to a
uniform 404 (ERR_EVAL_SET_NOT_FOUND).

``create_case`` is the payload write. It calls ``raise_if_zdr_locked`` (SELECT ... FOR UPDATE,
the shared primitive in tenants/application/retention_policy.py) as the FIRST statement inside
the transaction that commits the case, so the ZDR decision and the write are atomic — a flip
landing mid-await persists nothing (M3). The caller commits the session.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.evals.domain.errors import EvalSetNameConflict
from gateway.evals.infrastructure.orm import EvalCaseRow, EvalSetRow
from gateway.tenants.application.retention_policy import raise_if_zdr_locked


class SqlAlchemyEvalStore:
    """EvalStore adapter, scoped to a single SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_set(
        self, *, tenant_id: uuid.UUID, name: str, description: str | None
    ) -> EvalSetRow:
        row = EvalSetRow(id=uuid.uuid4(), tenant_id=tenant_id, name=name, description=description)
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # E4: UNIQUE(tenant_id, name). Roll back the poisoned transaction and raise a
            # domain error — the router maps it to 409, never a bare 500 or a silent second set.
            await self._session.rollback()
            raise EvalSetNameConflict from exc
        await self._session.refresh(row)
        return row

    async def list_sets(self, *, tenant_id: uuid.UUID) -> list[EvalSetRow]:
        stmt = (
            select(EvalSetRow)
            .where(EvalSetRow.tenant_id == tenant_id)
            .order_by(EvalSetRow.created_at.desc(), EvalSetRow.id.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_set(self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID) -> EvalSetRow | None:
        stmt = select(EvalSetRow).where(
            EvalSetRow.id == eval_set_id,
            EvalSetRow.tenant_id == tenant_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_cases(self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(
            EvalCaseRow.tenant_id == tenant_id,
            EvalCaseRow.eval_set_id == eval_set_id,
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def create_case(
        self,
        *,
        tenant_id: uuid.UUID,
        eval_set_id: uuid.UUID,
        request_body: dict[str, object],
        assertion: dict[str, object],
    ) -> EvalCaseRow:
        # M3: the ZDR decision is atomic with the payload write. FOR UPDATE on the tenant row
        # blocks a concurrent flip until this transaction resolves; a ZDR tenant raises 403 here
        # and the enclosing session closes WITHOUT commit, so zero rows land at rest.
        await raise_if_zdr_locked(self._session, tenant_id)
        row = EvalCaseRow(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            eval_set_id=eval_set_id,
            request_body=request_body,
            assertion=assertion,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def list_cases(
        self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID
    ) -> list[EvalCaseRow]:
        stmt = (
            select(EvalCaseRow)
            .where(
                EvalCaseRow.tenant_id == tenant_id,
                EvalCaseRow.eval_set_id == eval_set_id,
            )
            .order_by(EvalCaseRow.created_at.asc(), EvalCaseRow.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
