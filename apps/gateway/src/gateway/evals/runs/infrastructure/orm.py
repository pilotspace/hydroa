"""ORM models for eval-run execution (eval-run-executor PLAN.md §3 — FROZEN @ sha256:1353206d).

Two tenant-scoped tables, both children of the eval-set-store substrate:

  eval_runs          — one row per launched run: the launching key (A1 — a run bills the
                       launching key exactly as that key's live traffic), the named model,
                       and a status DERIVED from its cases (M7), never written speculatively.
  eval_case_results  — one row per case DRIVEN in a run. ``response_text`` is the model's
                       payload-at-rest (the console diff + re-scoring read it); it is the
                       ZDR-gated surface (M5). UNIQUE (eval_run_id, eval_case_id) makes a
                       re-drive on resume idempotent — a case already terminal is never
                       dialed or billed twice (M7 / R:DOUBLE_BILL).

Indexes are declared in BOTH __table_args__ AND the migration (the v30 two-manifest lesson).
All rows subclass gateway.core.db.Base; the side-effect import in main.py + migrations/env.py
registers them on Base.metadata (the four-manifest rule — see the migration's header).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class EvalRunRow(Base):
    __tablename__ = "eval_runs"

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
    # A1: the key that launched the run — the run bills THIS key, exactly as its live traffic.
    # NOT a secret: the raw key is NEVER persisted (auth-scoped resume, 2026-08-13) — only the
    # key_id, so a resume must re-supply the raw key via a fresh authenticated request.
    key_id: Mapped[uuid.UUID] = mapped_column("key_id", UUID(as_uuid=True), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    # status ∈ pending | completed | blocked. DERIVED from cases at drive end (M7): completed
    # when every snapshot case has a terminal result row; blocked iff a ZDR flip refused the run
    # mid-flight (M5). Never written speculatively.
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_eval_runs_tenant_set_created", "tenant_id", "eval_set_id", "created_at"),
    )


class EvalCaseResultRow(Base):
    __tablename__ = "eval_case_results"

    id: Mapped[uuid.UUID] = mapped_column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column("tenant_id", UUID(as_uuid=True), nullable=False)
    eval_run_id: Mapped[uuid.UUID] = mapped_column(
        "eval_run_id",
        UUID(as_uuid=True),
        ForeignKey("eval_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    eval_case_id: Mapped[uuid.UUID] = mapped_column(
        "eval_case_id", UUID(as_uuid=True), nullable=False
    )
    # status ∈ completed | refused | errored. completed = dialed 200 (carries response_text);
    # refused = governance denied before any dial (M3, carries reason, NO response_text, NO
    # usage row); errored = breaker-open / timeout / upstream 5xx (M4, carries reason).
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # The ZDR-gated payload-at-rest (M5). Present only for a `completed` case; a ZDR tenant's
    # run never reaches this write (refused outright at launch / atomic re-check mid-run).
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # An actionable, payload-free reason for a refused/errored case (A6). None on completed.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Best-effort back-reference to the usage_records row this dial produced (M2). Nullable:
    # the completion path bills fire-and-forget and does not return the id synchronously, so a
    # v1 run leaves this null and proves "exactly one usage_record per dialed case" by COUNTING
    # usage rows, not by this FK.
    usage_record_id: Mapped[uuid.UUID | None] = mapped_column(
        "usage_record_id", UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # M7 / R:DOUBLE_BILL: one result per (run, case) — a resumed drive that re-visits a
        # terminal case hits this constraint (or the skip-existing guard) and never re-dials.
        UniqueConstraint("eval_run_id", "eval_case_id", name="uq_eval_case_results_run_case"),
        Index("ix_eval_case_results_run_created", "tenant_id", "eval_run_id", "created_at"),
    )
