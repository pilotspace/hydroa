"""ORM models for the artifacts domain.

One table:
  artifacts — tenant-scoped binary file store with soft-delete.

The ``content`` column is BYTEA (LargeBinary) — stores raw decoded bytes.
The list endpoint uses load_only to skip loading content bytes.

Indexes declared in BOTH __table_args__ AND the migration (v30 lesson).
All rows subclass gateway.core.db.Base.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import LargeBinary

from gateway.core.db import Base


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        type_=__import__("sqlalchemy.dialects.postgresql", fromlist=["UUID"]).UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        "tenant_id",
        __import__("sqlalchemy.dialects.postgresql", fromlist=["UUID"]).UUID(as_uuid=True),
        nullable=False,
    )
    key_id: Mapped[uuid.UUID] = mapped_column(
        "key_id",
        __import__("sqlalchemy.dialects.postgresql", fromlist=["UUID"]).UUID(as_uuid=True),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_artifacts_tenant_created", "tenant_id", text("created_at DESC")),
    )
