"""SQLAlchemy adapter for the EvalRunStore port (eval-run-executor PLAN.md §3, M6/M7).

Sessionmaker-based (NOT a single injected session) because its primary caller is the durable
background worker, which — like ``VectorStoreIngestWorker`` — opens a FRESH short-lived
session per operation and commits each case result as it lands (durability, M7). The API
router reuses the same store for its tenant-scoped reads.

INVARIANT (M6): every tenant-facing read filters on ``tenant_id`` in the resolving query; an
absent or cross-tenant id returns None/[]. ``load_run`` is the ONE un-scoped read — it trusts
an id claimed from the internal queue and re-derives the tenant from the row.

``record_case_result`` is the payload write for a ``completed`` case: it calls
``raise_if_zdr_locked`` (SELECT … FOR UPDATE) as the FIRST statement in the committing
transaction, so the ZDR decision and the payload write are atomic (M5) — a flip landing
mid-run persists nothing. UNIQUE(eval_run_id, eval_case_id) + a pre-insert existence check
make a resumed/raced drive a no-op (M7 / R:DOUBLE_BILL).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.evals.infrastructure.orm import EvalCaseRow
from gateway.evals.runs.domain.entities import CaseOutcome
from gateway.evals.runs.infrastructure.orm import EvalCaseResultRow, EvalRunRow
from gateway.tenants.application.retention_policy import raise_if_zdr_locked


class SqlAlchemyEvalRunStore:
    """EvalRunStore adapter over an async_sessionmaker (fresh session per operation)."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create_run(
        self, *, tenant_id: uuid.UUID, key_id: uuid.UUID, eval_set_id: uuid.UUID, model: str
    ) -> EvalRunRow:
        async with self._sessionmaker() as session:
            row = EvalRunRow(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                eval_set_id=eval_set_id,
                key_id=key_id,
                model=model,
                status="pending",
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            await session.commit()
            # Expunge so the detached row is safe to read after the session closes.
            session.expunge(row)
            return row

    async def get_run(self, *, tenant_id: uuid.UUID, run_id: uuid.UUID) -> EvalRunRow | None:
        async with self._sessionmaker() as session:
            stmt = select(EvalRunRow).where(
                EvalRunRow.id == run_id, EvalRunRow.tenant_id == tenant_id
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is not None:
                session.expunge(row)
            return row

    async def list_runs(
        self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID
    ) -> list[EvalRunRow]:
        """A set's runs, newest first (A2 order). Tenant-scoped in the resolving query (M6)."""
        async with self._sessionmaker() as session:
            stmt = (
                select(EvalRunRow)
                .where(
                    EvalRunRow.tenant_id == tenant_id,
                    EvalRunRow.eval_set_id == eval_set_id,
                )
                .order_by(EvalRunRow.created_at.desc(), EvalRunRow.id.desc())
            )
            rows = list((await session.execute(stmt)).scalars().all())
            for r in rows:
                session.expunge(r)
            return rows

    async def load_run(self, run_id: uuid.UUID) -> EvalRunRow | None:
        async with self._sessionmaker() as session:
            row = await session.get(EvalRunRow, run_id)
            if row is not None:
                session.expunge(row)
            return row

    async def snapshot_cases(
        self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID, created_at_max: object
    ) -> list[EvalCaseRow]:
        async with self._sessionmaker() as session:
            stmt = (
                select(EvalCaseRow)
                .where(
                    EvalCaseRow.tenant_id == tenant_id,
                    EvalCaseRow.eval_set_id == eval_set_id,
                    EvalCaseRow.created_at <= created_at_max,
                )
                .order_by(EvalCaseRow.created_at.asc(), EvalCaseRow.id.asc())
            )
            rows = list((await session.execute(stmt)).scalars().all())
            for r in rows:
                session.expunge(r)
            return rows

    async def existing_result_case_ids(self, run_id: uuid.UUID) -> set[uuid.UUID]:
        async with self._sessionmaker() as session:
            stmt = select(EvalCaseResultRow.eval_case_id).where(
                EvalCaseResultRow.eval_run_id == run_id
            )
            return set((await session.execute(stmt)).scalars().all())

    async def record_case_result(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        eval_case_id: uuid.UUID,
        outcome: CaseOutcome,
    ) -> bool:
        async with self._sessionmaker() as session:
            # M5: a completed case persists the response payload — gate it ATOMICALLY with the
            # insert. FOR UPDATE on the tenant row blocks a concurrent ZDR flip until this
            # transaction resolves; a ZDR tenant raises 403 here and NOTHING commits. A
            # refused/errored case carries no payload, so it needs no gate.
            if outcome.status == "completed":
                await raise_if_zdr_locked(session, tenant_id)
            row = EvalCaseResultRow(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                eval_run_id=run_id,
                eval_case_id=eval_case_id,
                status=outcome.status,
                response_text=outcome.response_text,
                reason=outcome.reason,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError:
                # UNIQUE(eval_run_id, eval_case_id): a racer/resume already recorded this case.
                # Roll back and report a no-op — never a double-write, never a double-bill.
                await session.rollback()
                return False
            await session.commit()
            return True

    async def counts_by_status(self, run_id: uuid.UUID) -> dict[str, int]:
        async with self._sessionmaker() as session:
            stmt = (
                select(EvalCaseResultRow.status, func.count())
                .where(EvalCaseResultRow.eval_run_id == run_id)
                .group_by(EvalCaseResultRow.status)
            )
            rows = (await session.execute(stmt)).all()
            counts = {"completed": 0, "refused": 0, "errored": 0}
            for status, n in rows:
                counts[status] = int(n)
            return counts

    async def set_run_status(self, run_id: uuid.UUID, status: str) -> None:
        async with self._sessionmaker() as session:
            row = await session.get(EvalRunRow, run_id)
            if row is None:
                return
            row.status = status
            await session.commit()

    async def list_case_results(
        self, *, tenant_id: uuid.UUID, run_id: uuid.UUID
    ) -> list[EvalCaseResultRow]:
        async with self._sessionmaker() as session:
            # A5: order by the CASE's creation order (join eval_cases), not wall-clock completion
            # order, so a baseline and a candidate run of the same set align case-for-case.
            stmt = (
                select(EvalCaseResultRow)
                .join(EvalCaseRow, EvalCaseRow.id == EvalCaseResultRow.eval_case_id)
                .where(
                    EvalCaseResultRow.tenant_id == tenant_id,
                    EvalCaseResultRow.eval_run_id == run_id,
                )
                .order_by(EvalCaseRow.created_at.asc(), EvalCaseRow.id.asc())
            )
            rows = list((await session.execute(stmt)).scalars().all())
            for r in rows:
                session.expunge(r)
            return rows
