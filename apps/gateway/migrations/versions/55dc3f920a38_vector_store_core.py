"""pgvector substrate + /v1/vector_stores CRUD tables — vector-store-core.

Revision ID: 55dc3f920a38
Revises: c7e0a4b2d9f1
Create Date: 2026-07-24

vector-store-core PLAN.md §3 (FROZEN @ v1). Additive:

  - ``CREATE EXTENSION IF NOT EXISTS vector`` — the single provisioning choke
    point; a deploy target whose postgres lacks the extension fails this
    migration LOUDLY (fail-closed, honest).
  - NEW TABLE vector_stores — tenant-scoped OpenAI-wire container (metadata-only).
  - NEW TABLE vector_store_files — attach records (wave-2 drives status; UNIQUE
    (vector_store_id, file_id) for idempotent attach); FK CASCADE from vector_stores.
  - NEW TABLE vector_store_chunks — the pgvector substrate: ``embedding vector(1536)``
    + HNSW cosine index (m=16, ef_construction=64 — pgvector defaults); FK CASCADE
    from vector_store_files. EMPTY this task, ready for wave-2/3 without any schema
    change (§3 Extension points).

Indexes (also declared in the ORM __table_args__ per the v30 lesson):
  ix_vector_stores_tenant_created         on vector_stores(tenant_id, created_at DESC)
  ix_vector_store_files_store_created     on vector_store_files(vector_store_id, created_at DESC)
  ix_vector_store_chunks_store            on vector_store_chunks(vector_store_id)
  ix_vector_store_chunks_embedding_hnsw   HNSW (embedding vector_cosine_ops)

HNSW over IVFFlat (§3 justification): these tables start EMPTY, so an IVFFlat
index would train on zero rows (useless recall); HNSW builds incrementally and
is correct from row 1.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "55dc3f920a38"
down_revision = "c7e0a4b2d9f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "vector_stores",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'completed'")),
        sa.Column(
            "embedding_model",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'text-embedding-3-small'"),
        ),
        sa.Column("embedding_dim", sa.Integer(), nullable=False, server_default=sa.text("1536")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("usage_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vector_stores_tenant_created",
        "vector_stores",
        ["tenant_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "vector_store_files",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("vector_store_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'in_progress'")),
        sa.Column("last_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("usage_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["vector_store_id"], ["vector_stores.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "vector_store_id", "file_id", name="uq_vector_store_files_store_file"
        ),
    )
    op.create_index(
        "ix_vector_store_files_store_created",
        "vector_store_files",
        ["vector_store_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "vector_store_chunks",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("vector_store_file_id", sa.UUID(), nullable=False),
        sa.Column("vector_store_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["vector_store_file_id"], ["vector_store_files.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_vector_store_chunks_store", "vector_store_chunks", ["vector_store_id"]
    )
    op.execute(
        "CREATE INDEX ix_vector_store_chunks_embedding_hnsw ON vector_store_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.drop_index("ix_vector_store_chunks_embedding_hnsw", table_name="vector_store_chunks")
    op.drop_index("ix_vector_store_chunks_store", table_name="vector_store_chunks")
    op.drop_table("vector_store_chunks")
    op.drop_index("ix_vector_store_files_store_created", table_name="vector_store_files")
    op.drop_table("vector_store_files")
    op.drop_index("ix_vector_stores_tenant_created", table_name="vector_stores")
    op.drop_table("vector_stores")
