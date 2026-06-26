"""ORM models for the memory domain.

One table:
  memories — tenant-scoped semantic memory store with soft-delete and optional embedding.

The ``embedding`` column is DOUBLE PRECISION[] (PostgreSQL float8 array) — no pgvector.
Cosine ranking is performed in Python (see application/search.py).

The ``metadata`` column name is RESERVED by SQLAlchemy Declarative; the Python attribute
is named ``meta`` and mapped to the ``metadata`` column via mapped_column("metadata", JSONB).

Indexes declared in BOTH __table_args__ AND the migration (v30 lesson).
All rows subclass gateway.core.db.Base.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Float

from gateway.core.db import Base


class MemoryRow(Base):
    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        # SA 2.0 UUID type — uses Python uuid.UUID, stored as PostgreSQL UUID
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
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # DOUBLE PRECISION[] — stores float8 array; nullable because embedding is best-effort.
    embedding: Mapped[list[float] | None] = mapped_column(
        ARRAY(Float(precision=53)),  # Float(53) == DOUBLE PRECISION in PostgreSQL
        nullable=True,
    )
    # "metadata" is a reserved SQLAlchemy attribute; map python attr `meta` to column "metadata"
    meta: Mapped[dict[str, object] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )
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
        Index("ix_memories_tenant_created", "tenant_id", text("created_at DESC")),
    )
