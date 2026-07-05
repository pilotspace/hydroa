"""ORM models for the batch job store domain (batch-job-store TASK.md §3, frozen v1).

Two tables:
  batch_jobs       — tenant-scoped async batch job lifecycle (job-level state).
  batch_job_items  — one row per submitted line item (item-level state), FK'd to its job.

Status vocabulary (deliberately two different provider vocabularies — see TASK.md §0
GROUND "Status-vocabulary research"):
  batch_jobs.status       — OpenAI's exact batch.status enum (canonical wire, PROJECT.md v9)
  batch_job_items.status  — Anthropic's exact result.type enum + an added "pending" state

Both are DB TEXT + a Python Literal type alias — never a Postgres ENUM (PROJECT.md v7).
Indexes declared in BOTH __table_args__ AND the migration (v30 lesson).
All rows subclass gateway.core.db.Base.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base

# OpenAI's exact batch.status enum (job-level). This task only ever writes
# validating/in_progress/failed; the rest are reserved for openai-batch-adapter /
# anthropic-batch-adapter / a future cancel task.
BatchJobStatus = Literal[
    "validating",
    "failed",
    "in_progress",
    "finalizing",
    "completed",
    "expired",
    "cancelling",
    "cancelled",
]

# Anthropic's exact per-request result.type enum + an added "pending" state (neither
# provider names one for "not yet resolved" — both APIs are opaque per-item until the
# whole job resolves). This task only ever writes pending/errored (the no-processor path).
BatchJobItemStatus = Literal["pending", "succeeded", "errored", "canceled", "expired"]


class BatchJobRow(Base):
    __tablename__ = "batch_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        "tenant_id",
        UUID(as_uuid=True),
        nullable=False,
    )
    key_id: Mapped[uuid.UUID] = mapped_column(
        "key_id",
        UUID(as_uuid=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'validating'"),
    )
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (Index("ix_batch_jobs_tenant_created", "tenant_id", text("created_at DESC")),)


class BatchJobItemRow(Base):
    __tablename__ = "batch_job_items"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    batch_job_id: Mapped[uuid.UUID] = mapped_column(
        "batch_job_id",
        UUID(as_uuid=True),
        ForeignKey("batch_jobs.id"),
        nullable=False,
    )
    # Denormalized defense-in-depth (PROJECT.md: "every tenant-owned row carries
    # tenant_id") — cheap, even though every real query joins through batch_job_id.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        "tenant_id",
        UUID(as_uuid=True),
        nullable=False,
    )
    custom_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'"),
    )
    result_body: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_batch_job_items_job", "batch_job_id"),
        UniqueConstraint("batch_job_id", "custom_id", name="uq_batch_job_items_job_custom_id"),
    )
