"""ORM model for the video generation jobs domain.

One table:
  video_generation_jobs — tenant-scoped async video generation job lifecycle.

The result bytes live in the v45 artifacts table (NOT duplicated here).
result_artifact_id is the FK link to artifacts.id after a successful generation.

Indexes declared in BOTH __table_args__ AND the migration (v30 lesson).
All rows subclass gateway.core.db.Base.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class VideoGenerationJobRow(Base):
    __tablename__ = "video_generation_jobs"

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
        server_default=text("'queued'"),
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    result_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        "result_artifact_id",
        UUID(as_uuid=True),
        nullable=True,
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

    __table_args__ = (
        Index("ix_video_jobs_tenant_created", "tenant_id", text("created_at DESC")),
    )
