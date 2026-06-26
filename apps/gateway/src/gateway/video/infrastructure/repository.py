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
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.video.infrastructure.orm import VideoGenerationJobRow


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
        """Insert a new job row (status=queued) and return it (with server defaults populated)."""
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
        now = datetime.now(tz=UTC)
        values["updated_at"] = now
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
