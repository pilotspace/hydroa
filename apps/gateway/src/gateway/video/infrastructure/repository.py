"""SQLAlchemy repository for the video generation jobs domain.

INVARIANT: every method takes ``tenant_id`` and every query filters on it.
A cross-tenant or unknown id always returns None / empty — NEVER 403/200.
The router maps None → 404 with zero data leak.

Methods:
  create(*, tenant_id, key_id, model, prompt, params) -> VideoGenerationJobRow
  get(*, tenant_id, job_id) -> VideoGenerationJobRow | None
  list_for_tenant(*, tenant_id, limit, offset) -> list[VideoGenerationJobRow]
  set_running(*, job_id) -> None  [fresh session — called from background task]
  set_succeeded(*, job_id, artifact_id) -> None
  set_failed(*, job_id, error) -> None
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.application.retention_policy import raise_if_zdr
from gateway.video.infrastructure.orm import VideoGenerationJobRow

# Terminal statuses — a job in one of these states must not be re-transitioned.
_TERMINAL_STATUSES = ("succeeded", "failed")
_NONTERMINAL_STATUSES = ("queued", "running")


class VideoJobRepository:
    """All video job persistence, scoped to a single SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        prompt: str,
        params: dict[str, object] | None,
    ) -> VideoGenerationJobRow:
        """Insert a new job row (status=queued) and return it (with server defaults populated).

        Fail-closed ZDR gate (tenant-retention-zdr TASK.md §3 M5): raises 403
        ERR_ZDR_PAYLOAD_BLOCKED, checked fresh, BEFORE the row is constructed.
        """
        await raise_if_zdr(self._session, tenant_id)
        row = VideoGenerationJobRow(
            tenant_id=tenant_id,
            key_id=key_id,
            status="queued",
            model=model,
            prompt=prompt,
            params=params,
        )
        self._session.add(row)
        await self._session.flush()  # populate server defaults (id, created_at, updated_at)
        await self._session.refresh(row)
        return row

    async def get(
        self,
        *,
        tenant_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> VideoGenerationJobRow | None:
        """Load a single job row scoped to tenant_id.

        Returns None for unknown or cross-tenant id — never 403.
        """
        stmt = select(VideoGenerationJobRow).where(
            VideoGenerationJobRow.id == job_id,
            VideoGenerationJobRow.tenant_id == tenant_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self,
        *,
        tenant_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> list[VideoGenerationJobRow]:
        """Return jobs for the tenant, newest first (ix_video_jobs_tenant_created)."""
        stmt = (
            select(VideoGenerationJobRow)
            .where(VideoGenerationJobRow.tenant_id == tenant_id)
            .order_by(VideoGenerationJobRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _update_status(
        self,
        *,
        job_id: uuid.UUID,
        values: dict[str, object],
        allowed_from: tuple[str, ...],
    ) -> None:
        """Update job fields by id, ONLY when the current status is in allowed_from.

        The status guard makes transitions idempotent: a job that is already
        terminal (succeeded/failed) is never re-transitioned, so a re-driven /
        duplicated task (the durable-worker delta) cannot clobber a terminal
        result. Legal linear transitions (queued→running→terminal) are unaffected.
        """
        # updated_at is deliberately NOT set here: the column carries
        # onupdate=func.now(), so the DB clock that owns its server_default also
        # owns every bump (suite-stability M9). Writing datetime.now() here made
        # this repository MIXED-clock once increment_retry moved to func.now(),
        # which is the exact inversion CR v3 was raised to remove.
        await self._session.execute(
            update(VideoGenerationJobRow)
            .where(
                VideoGenerationJobRow.id == job_id,
                VideoGenerationJobRow.status.in_(allowed_from),
            )
            .values(**values)
        )
        await self._session.flush()

    async def set_running(self, *, job_id: uuid.UUID) -> None:
        """Transition to status=running — only from queued (idempotent otherwise)."""
        await self._update_status(
            job_id=job_id, values={"status": "running"}, allowed_from=("queued",)
        )

    async def set_succeeded(
        self,
        *,
        job_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> None:
        """Transition to status=succeeded — only from a non-terminal status."""
        await self._update_status(
            job_id=job_id,
            values={"status": "succeeded", "result_artifact_id": artifact_id},
            allowed_from=("queued", "running"),
        )

    async def set_failed(self, *, job_id: uuid.UUID, error: str) -> None:
        """Transition to status=failed — only from a non-terminal status."""
        await self._update_status(
            job_id=job_id,
            values={"status": "failed", "error": error},
            allowed_from=("queued", "running"),
        )

    async def increment_retry(self, job_id: uuid.UUID) -> int:
        """Atomically increment retry_count for a non-terminal job and return the new value.

        Only executes the UPDATE when status is in ('queued', 'running') — the same
        guard that all other status transitions use. If the job is already terminal
        (or missing), no row is updated and 0 is returned so the caller's ``> max_retries``
        cap check is harmless (0 never exceeds a non-negative cap).

        The caller must commit (or be inside an already-open transaction).
        """
        result = await self._session.execute(
            update(VideoGenerationJobRow)
            .where(
                VideoGenerationJobRow.id == job_id,
                VideoGenerationJobRow.status.in_(_NONTERMINAL_STATUSES),
            )
            .values(
                retry_count=VideoGenerationJobRow.retry_count + 1,
            )
            .returning(VideoGenerationJobRow.retry_count)
        )
        await self._session.flush()
        row = result.fetchone()
        return int(row[0]) if row is not None else 0

    async def list_nonterminal_ids(self) -> list[uuid.UUID]:
        """Return all job ids whose status is not yet terminal (queued or running).

        Used by ``recover_orphans`` on startup to re-enqueue jobs that were in-flight
        when the gateway last restarted. The query is intentionally un-scoped (no
        tenant_id filter) because orphan recovery is operator-wide.
        """
        stmt = select(VideoGenerationJobRow.id).where(
            VideoGenerationJobRow.status.in_(_NONTERMINAL_STATUSES)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
