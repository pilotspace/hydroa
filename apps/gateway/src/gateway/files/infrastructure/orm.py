"""ORM model for the files domain (files-uploads-api PLAN.md §3 — FROZEN @ v1).

One table:
  files — tenant-scoped OpenAI-wire user-upload store with soft-delete.

Mirrors ``gateway.artifacts.infrastructure.orm.ArtifactRow`` — same inline-BYTEA /
s3 storage_backend dispatch, same soft-delete + tenant-scoped index shape. Distinct
from artifacts because the /v1/files wire has its own id shape (``file-<hex>``),
its own ``purpose`` vocabulary, and a ``status`` lifecycle field.

The ``content`` column is BYTEA (LargeBinary) — raw stored bytes for storage_backend
='inline'; NULL for 's3' (bytes live in the object store at ``object_key``).
The list endpoint uses load_only to skip loading content bytes.

Indexes declared in BOTH __table_args__ AND the migration (v30 lesson).
All rows subclass gateway.core.db.Base.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import LargeBinary

from gateway.core.db import Base


class FileRow(Base):
    __tablename__ = "files"

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
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    # OpenAI file purpose — vocabulary is exactly {batch, vision, user_data} (M6);
    # stored as TEXT + validated in the router (never a Postgres ENUM, PROJECT.md v7).
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Always 'processed' this task — synchronous store, no async validation pipeline
    # (§1 assumption: a status state machine is deferred).
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'processed'")
    )
    # storage_backend dispatches the byte path: 'inline' (BYTEA) or 's3' (bytes live
    # in the object store at object_key; content is NULL) — mirrors ArtifactRow.
    storage_backend: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'inline'")
    )
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (Index("ix_files_tenant_created", "tenant_id", text("created_at DESC")),)
