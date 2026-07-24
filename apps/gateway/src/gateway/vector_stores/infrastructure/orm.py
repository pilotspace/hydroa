"""ORM models for the vector-stores substrate (vector-store-core PLAN.md §3 — FROZEN @ v1).

Three tables:
  vector_stores        — tenant-scoped OpenAI-wire container (metadata-only; M1-M5).
  vector_store_files   — attach records (wave-2 drives status; UNIQUE(vector_store_id,
                          file_id) for idempotent attach).
  vector_store_chunks  — the pgvector substrate: ``embedding vector(1536)`` + HNSW
                          cosine index, EMPTY this task, ready for wave-2 INSERTs and
                          wave-3 top-k without any schema change.

Mirrors ``gateway.files.infrastructure.orm.FileRow`` in shape (tenant-scoped, soft
module layout skipped in favor of the shipped files/ precedent — domain/ Protocol
port deliberately NOT introduced here, a conscious deviation matching that module).

Indexes declared in BOTH __table_args__ AND the migration (v30 lesson).
All rows subclass gateway.core.db.Base.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class VectorStoreRow(Base):
    __tablename__ = "vector_stores"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column("tenant_id", UUID(as_uuid=True), nullable=False)
    key_id: Mapped[uuid.UUID] = mapped_column("key_id", UUID(as_uuid=True), nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'completed'"))
    embedding_model: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'text-embedding-3-small'")
    )
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1536"))
    metadata_: Mapped[dict[str, str] | None] = mapped_column("metadata", JSONB, nullable=True)
    usage_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_vector_stores_tenant_created", "tenant_id", text("created_at DESC")),
    )


class VectorStoreFileRow(Base):
    __tablename__ = "vector_store_files"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    vector_store_id: Mapped[uuid.UUID] = mapped_column(
        "vector_store_id",
        UUID(as_uuid=True),
        ForeignKey("vector_stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column("tenant_id", UUID(as_uuid=True), nullable=False)
    file_id: Mapped[uuid.UUID] = mapped_column("file_id", UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'in_progress'"))
    last_error: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    usage_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("vector_store_id", "file_id", name="uq_vector_store_files_store_file"),
        Index("ix_vector_store_files_store_created", "vector_store_id", text("created_at DESC")),
    )


class VectorStoreChunkRow(Base):
    __tablename__ = "vector_store_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vector_store_file_id: Mapped[uuid.UUID] = mapped_column(
        "vector_store_file_id",
        UUID(as_uuid=True),
        ForeignKey("vector_store_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    vector_store_id: Mapped[uuid.UUID] = mapped_column(
        "vector_store_id", UUID(as_uuid=True), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column("tenant_id", UUID(as_uuid=True), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_vector_store_chunks_store", "vector_store_id"),
        Index(
            "ix_vector_store_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
