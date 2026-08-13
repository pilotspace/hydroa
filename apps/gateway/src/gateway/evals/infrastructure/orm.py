"""ORM models for the evals substrate (eval-set-store PLAN.md §3 — FROZEN @ v1).

Two tables, both tenant-scoped:
  eval_sets  — tenant-owned metadata (name + optional description). NO payload. UNIQUE
               (tenant_id, name) so a duplicate name is refused, never a silent second set.
  eval_cases — the payload-bearing surface: request_body (the OpenAI-shaped request to
               replay) + a deterministic assertion. Append-only. FK -> eval_sets ON DELETE
               CASCADE. Indexed (tenant_id, eval_set_id, created_at) for creation-order list.

Indexes declared in BOTH __table_args__ AND the migration (v30 lesson). All rows subclass
gateway.core.db.Base — the side-effect import in main.py registers them on Base.metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class EvalSetRow(Base):
    __tablename__ = "eval_sets"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column("tenant_id", UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_eval_sets_tenant_name"),
        Index("ix_eval_sets_tenant_created", "tenant_id", text("created_at DESC")),
    )


class EvalCaseRow(Base):
    __tablename__ = "eval_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column("tenant_id", UUID(as_uuid=True), nullable=False)
    eval_set_id: Mapped[uuid.UUID] = mapped_column(
        "eval_set_id",
        UUID(as_uuid=True),
        ForeignKey("eval_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    assertion: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_eval_cases_set_created", "tenant_id", "eval_set_id", "created_at"),)
