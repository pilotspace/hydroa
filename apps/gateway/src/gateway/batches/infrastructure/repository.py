"""SQLAlchemy repository for the batch job store domain.

INVARIANT: every tenant-facing method takes ``tenant_id`` and every query filters on
it. A cross-tenant or unknown id always returns None / empty — NEVER 403/200. The
router maps None -> 404 with zero data leak.

Methods:
  create(*, tenant_id, key_id, line_items) -> BatchJobRow
    [creates the job row + one BatchJobItemRow per line item, ONE transaction — the
     caller commits]
  get(*, tenant_id, job_id) -> BatchJobRow | None
  list_items(*, tenant_id, job_id) -> list[BatchJobItemRow]  [batch-auto-grouping §3 extension]
  list_for_tenant(*, tenant_id, limit, offset) -> list[BatchJobRow]
  status_counts(*, job_id) -> dict[str, int]  [all 5 item-vocabulary keys always present]
  tenant_status_counts(*, tenant_id) -> dict[str, int]  [same, tenant-wide instead of per-job]
  set_in_progress(*, job_id) -> None   [fresh session — called from background task]
  set_failed(*, job_id, error) -> None  [cascades every still-pending item to errored —
    a terminal job never leaves pending items behind, TASK.md §1 Must]
  increment_retry(job_id) -> int
  list_nonterminal_ids() -> list[uuid.UUID]  [operator-wide — orphan recovery only]
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.batches.infrastructure.orm import BatchJobItemRow, BatchJobRow
from gateway.tenants.application.retention_policy import raise_if_zdr

# Job-level non-terminal statuses (OpenAI's vocabulary) — used by the retry/orphan guards.
_JOB_NONTERMINAL_STATUSES = ("validating", "in_progress", "finalizing", "cancelling")
# Item-level vocabulary (Anthropic's result.type + pending) — status_counts always
# reports all five keys, even when a count is zero.
_ITEM_STATUSES = ("pending", "succeeded", "errored", "canceled", "expired")


class BatchJobRepository:
    """All batch job + item persistence, scoped to a single SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        line_items: list[dict[str, Any]],
        input_file_id: uuid.UUID | None = None,
    ) -> BatchJobRow:
        """Insert one batch_jobs row (status=validating) + one batch_job_items row per
        line item (status=pending), in the SAME transaction. Caller commits.

        line_items: [{"custom_id": str, "body": dict}, ...] — already validated (non-empty,
        under the cap, unique custom_id, model+messages present) by the caller.

        input_file_id (files-uploads-api PLAN.md §3 — ADDITIVE, default None): when a
        purpose='batch' file drove this job, the caller-validated files row id is recorded
        here and line_items is empty (item_count=0); the JSONL->line-item expansion is v58.
        For every existing line_items submission it stays None — byte-identical.

        Fail-closed ZDR gate (tenant-retention-zdr TASK.md §3 M5): raises 403
        ERR_ZDR_PAYLOAD_BLOCKED, checked fresh, BEFORE any row is constructed — every
        line item in this submission carries a request_body, so the whole job is gated.
        """
        await raise_if_zdr(self._session, tenant_id)
        job = BatchJobRow(
            tenant_id=tenant_id,
            key_id=key_id,
            status="validating",
            item_count=len(line_items),
            input_file_id=input_file_id,
        )
        self._session.add(job)
        await self._session.flush()  # populate job.id (server default)

        for item in line_items:
            self._session.add(
                BatchJobItemRow(
                    batch_job_id=job.id,
                    tenant_id=tenant_id,
                    custom_id=item["custom_id"],
                    request_body=item["body"],
                    status="pending",
                )
            )

        await self._session.flush()  # populate server defaults (created_at, updated_at)
        await self._session.refresh(job)
        return job

    async def get(
        self,
        *,
        tenant_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> BatchJobRow | None:
        """Load a single job row scoped to tenant_id.

        Returns None for unknown or cross-tenant id — never 403.
        """
        stmt = select(BatchJobRow).where(
            BatchJobRow.id == job_id,
            BatchJobRow.tenant_id == tenant_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_items(
        self,
        *,
        tenant_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> list[BatchJobItemRow]:
        """Load every item row for a job, scoped to tenant_id (same isolation invariant
        as get() — BatchJobItemRow.tenant_id is denormalized, no join needed).

        Returns an empty list for an unknown or cross-tenant job_id — never 403
        (mirrors get()'s None-for-unknown convention at the item-list level).
        """
        stmt = (
            select(BatchJobItemRow)
            .where(
                BatchJobItemRow.batch_job_id == job_id,
                BatchJobItemRow.tenant_id == tenant_id,
            )
            .order_by(BatchJobItemRow.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_tenant(
        self,
        *,
        tenant_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> list[BatchJobRow]:
        """Return jobs for the tenant, newest first (ix_batch_jobs_tenant_created)."""
        stmt = (
            select(BatchJobRow)
            .where(BatchJobRow.tenant_id == tenant_id)
            .order_by(BatchJobRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def status_counts(self, *, job_id: uuid.UUID) -> dict[str, int]:
        """Per-item-status breakdown for a job — all 5 item-vocabulary keys always
        present (0 when none), so the caller never has to defensively .get() them."""
        stmt = (
            select(BatchJobItemRow.status, func.count())
            .where(BatchJobItemRow.batch_job_id == job_id)
            .group_by(BatchJobItemRow.status)
        )
        result = await self._session.execute(stmt)
        counts: dict[str, int] = dict.fromkeys(_ITEM_STATUSES, 0)
        for status_value, count in result.all():
            if status_value in counts:
                counts[status_value] = int(count)
        return counts

    async def tenant_status_counts(self, *, tenant_id: uuid.UUID) -> dict[str, int]:
        """Tenant-wide per-item-status breakdown across EVERY batch job the tenant has
        ever submitted — same convention as status_counts() (all 5 item-vocabulary keys
        always present, 0 when none), just scoped by tenant_id instead of one job_id.

        BatchJobItemRow.tenant_id is denormalized on the item row itself (see create()) —
        no join through BatchJobRow needed. Used by batch-dashboard-surface's read-only
        stats page (total_requests = sum(these values), no separate count query)."""
        stmt = (
            select(BatchJobItemRow.status, func.count())
            .where(BatchJobItemRow.tenant_id == tenant_id)
            .group_by(BatchJobItemRow.status)
        )
        result = await self._session.execute(stmt)
        counts: dict[str, int] = dict.fromkeys(_ITEM_STATUSES, 0)
        for status_value, count in result.all():
            if status_value in counts:
                counts[status_value] = int(count)
        return counts

    async def set_in_progress(self, *, job_id: uuid.UUID) -> None:
        """Transition to status=in_progress — only from validating (idempotent otherwise)."""
        await self._session.execute(
            update(BatchJobRow)
            .where(
                BatchJobRow.id == job_id,
                BatchJobRow.status == "validating",
            )
            .values(status="in_progress")
        )
        await self._session.flush()

    async def set_failed(self, *, job_id: uuid.UUID, error: str) -> None:
        """Transition to status=failed — only from a non-terminal status — and cascade
        every still-"pending" item under this job to "errored" carrying the same error.

        A terminal job never leaves pending items behind (TASK.md §1 Must / §2 Scenario
        "A terminal job never leaves pending items behind").
        """
        result = await self._session.execute(
            update(BatchJobRow)
            .where(
                BatchJobRow.id == job_id,
                BatchJobRow.status.in_(_JOB_NONTERMINAL_STATUSES),
            )
            .values(status="failed", error=error)
            .returning(BatchJobRow.id)
        )
        transitioned = result.fetchone() is not None
        if transitioned:
            await self._session.execute(
                update(BatchJobItemRow)
                .where(
                    BatchJobItemRow.batch_job_id == job_id,
                    BatchJobItemRow.status == "pending",
                )
                .values(status="errored", error=error)
            )
        await self._session.flush()

    async def increment_retry(self, job_id: uuid.UUID) -> int:
        """Atomically increment retry_count for a non-terminal job and return the new value.

        Only executes the UPDATE when status is job-level non-terminal — the same guard
        every other status transition uses. If the job is already terminal (or missing),
        no row is updated and 0 is returned so the caller's ``> max_retries`` cap check
        is harmless (0 never exceeds a non-negative cap).

        The caller must commit (or be inside an already-open transaction).
        """
        result = await self._session.execute(
            update(BatchJobRow)
            .where(
                BatchJobRow.id == job_id,
                BatchJobRow.status.in_(_JOB_NONTERMINAL_STATUSES),
            )
            .values(
                retry_count=BatchJobRow.retry_count + 1,
            )
            .returning(BatchJobRow.retry_count)
        )
        await self._session.flush()
        row = result.fetchone()
        return int(row[0]) if row is not None else 0

    async def list_nonterminal_ids(self) -> list[uuid.UUID]:
        """Return all job ids whose status is not yet terminal.

        Used by ``recover_orphans`` on startup to re-enqueue jobs that were in-flight
        when the gateway last restarted. The query is intentionally un-scoped (no
        tenant_id filter) because orphan recovery is operator-wide.
        """
        stmt = select(BatchJobRow.id).where(BatchJobRow.status.in_(_JOB_NONTERMINAL_STATUSES))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
