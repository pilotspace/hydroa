"""SQLAlchemy repository for the finetune job-store (finetune-broker PLAN.md §3).

INVARIANT: every tenant-facing method takes ``tenant_id`` and every query filters on
it in the SAME query that checks existence — a cross-tenant or unknown id always
returns None/empty, NEVER 403 (T3: no enumeration oracle). NO method ever selects,
logs, or returns a credential — there is no credential-bearing column to select.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.finetune.domain.provider_port import TERMINAL_STATUSES
from gateway.finetune.infrastructure.orm import FinetuneJobEventRow, FinetuneJobRow


class FinetuneJobRepository:
    """All finetune job + event persistence, scoped to a single SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """The SQLAlchemy session this repository writes through.

        Published READ-ONLY (zdr-retention-inventory-extension M4) for exactly one
        caller: ``FinetuneBrokerService.create_job`` needs the SAME session its inserts
        land in to run the ZDR gate — the entry ``raise_if_zdr`` and, after the provider
        await returns, the ``raise_if_zdr_locked`` re-check that must be atomic with the
        commit. A gate on any other session would be a check of a different transaction's
        view, i.e. no gate at all.

        Not a general escape hatch: every persistence operation stays a method on this
        repository (no caller may execute its own statements through this handle).
        """
        return self._session

    async def commit(self) -> None:
        """Durably commit whatever this repository has flushed so far.

        HEAL (D4): used by ``FinetuneBrokerService.get_job`` to commit a winning
        terminal CAS transition INDEPENDENTLY of the completion-listener call that
        follows it — so a throwing listener can never roll back an already-decided
        terminal state.
        """
        await self._session.commit()

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        provider: str,
        model: str,
        training_file_id: uuid.UUID,
        validation_file_id: uuid.UUID | None,
        suffix: str | None,
        hyperparameters: dict[str, Any] | None,
    ) -> FinetuneJobRow:
        """Insert one finetune_jobs row (status=validating_files). Caller commits/flushes."""
        row = FinetuneJobRow(
            tenant_id=tenant_id,
            key_id=key_id,
            provider=provider,
            model=model,
            training_file_id=training_file_id,
            validation_file_id=validation_file_id,
            suffix=suffix,
            hyperparameters=hyperparameters,
            status="validating_files",
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get(self, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> FinetuneJobRow | None:
        """Load a single job row scoped to tenant_id. None for unknown/cross-tenant."""
        stmt = select(FinetuneJobRow).where(
            FinetuneJobRow.id == job_id, FinetuneJobRow.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self, *, tenant_id: uuid.UUID, limit: int, after: uuid.UUID | None = None
    ) -> tuple[list[FinetuneJobRow], bool]:
        """Newest-first tenant-scoped page. Returns (rows, has_more).

        ``after``: a keyset cursor — the id of the last row of the PREVIOUS page.
        When given, only jobs strictly older (created_at) than that row are returned.
        """
        stmt = select(FinetuneJobRow).where(FinetuneJobRow.tenant_id == tenant_id)
        if after is not None:
            cursor_row = await self.get(tenant_id=tenant_id, job_id=after)
            if cursor_row is not None:
                stmt = stmt.where(FinetuneJobRow.created_at < cursor_row.created_at)
        stmt = stmt.order_by(FinetuneJobRow.created_at.desc()).limit(limit + 1)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        has_more = len(rows) > limit
        return rows[:limit], has_more

    async def set_submit_result(
        self,
        *,
        job_id: uuid.UUID,
        provider_job_id: str | None,
        status: str,
        error: str | None,
    ) -> None:
        """Record the outcome of the inline submit call (M1): success -> queued +
        provider_job_id; failure -> failed + error (row already exists — never a
        silent half-write)."""
        await self._session.execute(
            update(FinetuneJobRow)
            .where(FinetuneJobRow.id == job_id)
            .values(
                status=status,
                provider_job_id=provider_job_id,
                error=error,
                finished_at=datetime.now(tz=UTC) if status in TERMINAL_STATUSES else None,
            )
        )
        await self._session.flush()

    async def apply_poll_result(
        self,
        *,
        job_id: uuid.UUID,
        tenant_id: uuid.UUID,
        new_status: str,
        fine_tuned_model: str | None,
    ) -> bool:
        """Conditionally transition a non-terminal job to ``new_status`` (CAS: only
        rows still non-terminal are touched — ``UPDATE ... WHERE status NOT IN
        (terminal)``). Returns True iff THIS call performed the transition (the
        caller fires the completion listener EXACTLY ONCE, only on a True return
        AND new_status=="succeeded" — M7).

        fine_tuned_model is persisted verbatim ONLY when new_status=="succeeded";
        otherwise the existing column value is left untouched.
        """
        # updated_at is NOT named: the column's `onupdate=func.now()` owns it (M9).
        # finished_at IS app-clock, deliberately — that column has no server_default
        # (DEFAULT NULL), so the app clock is its only writer and there is no second
        # clock to disagree with. It must also stay a plain Python value: a SQL
        # function here is not evaluatable, so `synchronize_session` would EXPIRE the
        # in-session row, and the SYNC serializer `_job_object` reads `row.finished_at`.
        now = datetime.now(tz=UTC)
        values: dict[str, Any] = {"status": new_status}
        if new_status in TERMINAL_STATUSES:
            values["finished_at"] = now
        if new_status == "succeeded":
            values["fine_tuned_model"] = fine_tuned_model

        result = await self._session.execute(
            update(FinetuneJobRow)
            .where(
                FinetuneJobRow.id == job_id,
                FinetuneJobRow.tenant_id == tenant_id,
                FinetuneJobRow.status.notin_(tuple(TERMINAL_STATUSES)),
            )
            .values(**values)
            .returning(FinetuneJobRow.id)
        )
        transitioned = result.fetchone() is not None
        await self._session.flush()
        return transitioned

    async def cancel(self, *, job_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """Transition a non-terminal job to "cancelled". Returns True iff this call
        performed the transition (False -> already terminal, caller raises 409)."""
        result = await self._session.execute(
            update(FinetuneJobRow)
            .where(
                FinetuneJobRow.id == job_id,
                FinetuneJobRow.tenant_id == tenant_id,
                FinetuneJobRow.status.notin_(tuple(TERMINAL_STATUSES)),
            )
            .values(
                status="cancelled",
                finished_at=datetime.now(tz=UTC),
            )
            .returning(FinetuneJobRow.id)
        )
        transitioned = result.fetchone() is not None
        await self._session.flush()
        return transitioned

    async def add_event(
        self,
        *,
        job_id: uuid.UUID,
        tenant_id: uuid.UUID,
        level: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> FinetuneJobEventRow:
        """Append one lifecycle event row."""
        row = FinetuneJobEventRow(
            job_id=job_id, tenant_id=tenant_id, level=level, message=message, data=data
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def list_events(
        self, *, tenant_id: uuid.UUID, job_id: uuid.UUID
    ) -> list[FinetuneJobEventRow]:
        """Newest-last event list, scoped to tenant_id (denormalized column — no join)."""
        stmt = (
            select(FinetuneJobEventRow)
            .where(FinetuneJobEventRow.job_id == job_id, FinetuneJobEventRow.tenant_id == tenant_id)
            .order_by(FinetuneJobEventRow.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
