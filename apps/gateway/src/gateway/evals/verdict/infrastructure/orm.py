"""ORM model for the baseline pin — baseline-and-verdict PLAN.md §3 (M3, M6).

One tenant-scoped table, a child of both eval-set-store and eval-run-executor:

  eval_baselines  — the ONE-baseline-per-set pin: which run is the reference a candidate is
                    verdicted against. ``UNIQUE(eval_set_id)`` structurally enforces one
                    baseline per set (M3); re-pin is an upsert. ``pinned_at`` is the auditable
                    moment the pin moved (SOC 2). FK -> eval_sets AND eval_runs, both CASCADE:
                    dropping a set or the pinned run drops the pin.

The index is declared in BOTH __table_args__ AND the migration (the v30 two-manifest lesson).
The row subclasses gateway.core.db.Base; the side-effect import in main.py + migrations/env.py
registers it on Base.metadata (the four-manifest rule — see the migration's header).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class EvalBaselineRow(Base):
    __tablename__ = "eval_baselines"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column("tenant_id", UUID(as_uuid=True), nullable=False)
    # One baseline per set — the UNIQUE below makes a re-pin an UPDATE, never a second row (M3).
    eval_set_id: Mapped[uuid.UUID] = mapped_column(
        "eval_set_id",
        UUID(as_uuid=True),
        ForeignKey("eval_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        "run_id",
        UUID(as_uuid=True),
        ForeignKey("eval_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    pinned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("eval_set_id", name="uq_eval_baselines_set"),
        Index("ix_eval_baselines_tenant_set", "tenant_id", "eval_set_id"),
    )
