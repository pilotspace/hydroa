"""ORM models for the finetune job-store (finetune-broker PLAN.md §3, FROZEN @ v1).

Two tables, mirroring ``gateway.batches.infrastructure.orm`` shape (job-store
precedent):
  finetune_jobs        — tenant-scoped job lifecycle (OpenAI's exact 6-state vocab).
  finetune_job_events  — one row per lifecycle event, FK'd to its job.

NO credential-bearing column exists on either table (T4 threat model) — the
plaintext credential lives only inside the outbound-call frame, never here.
Both DB TEXT + a Python Literal alias, never a Postgres ENUM (repo convention).
Indexes declared in BOTH __table_args__ AND the migration (v30 lesson).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class FinetuneJobRow(Base):
    __tablename__ = "finetune_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column("tenant_id", UUID(as_uuid=True), nullable=False)
    key_id: Mapped[uuid.UUID] = mapped_column("key_id", UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # Set on submit success only — a submit-failure row stays NULL (M1 honest degradation).
    provider_job_id: Mapped[str | None] = mapped_column("provider_job_id", Text, nullable=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    training_file_id: Mapped[uuid.UUID] = mapped_column(
        "training_file_id", UUID(as_uuid=True), nullable=False
    )
    validation_file_id: Mapped[uuid.UUID | None] = mapped_column(
        "validation_file_id", UUID(as_uuid=True), nullable=True
    )
    suffix: Mapped[str | None] = mapped_column(Text, nullable=True)
    hyperparameters: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    # OpenAI exact 6-state vocab (validating_files|queued|running|succeeded|failed|cancelled).
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'validating_files'")
    )
    # The finetune-model-registry EXTENSION POINT — persisted verbatim at "succeeded",
    # never re-derived, never overwritten after the winning CAS (M7).
    fine_tuned_model: Mapped[str | None] = mapped_column("fine_tuned_model", Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        "finished_at", DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_finetune_jobs_tenant_created", "tenant_id", text("created_at DESC")),
    )


class FinetuneJobEventRow(Base):
    __tablename__ = "finetune_job_events"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        "job_id",
        UUID(as_uuid=True),
        ForeignKey("finetune_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalized defense-in-depth (every tenant-owned row carries tenant_id) — the
    # events query filters on this directly, no join through finetune_jobs required.
    tenant_id: Mapped[uuid.UUID] = mapped_column("tenant_id", UUID(as_uuid=True), nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'info'"))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_finetune_job_events_job_created", "job_id", "created_at"),)
