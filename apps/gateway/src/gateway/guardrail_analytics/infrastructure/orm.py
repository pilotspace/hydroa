"""SQLAlchemy ORM row for the guardrail_verdict_events table.

Schema mirrors §3 CONTRACT (guardrail-analytics TASK.md, FROZEN @ v1):
  id               UUID PK (uuid7, mirrors request_logs/usage_records)
  tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
  key_id           UUID NOT NULL, no FK (append-only-ledger pattern, mirrors usage_records)
  team_id          UUID NULL, no FK (team deletion must not cascade, mirrors usage_records)
  guardrail        TEXT NOT NULL  -- "prompt_injection"|"pii_mask"|"ml_moderation"|"error"
  action           TEXT NOT NULL  -- "blocked"|"masked"|"audited"|"passed"|"error"|
                                   --  "unchecked"|"budget_exceeded"
  policy_source    TEXT NOT NULL  -- "key"|"tenant"|"none" — from AuthzResult.policy_source
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()

Append-only ledger — no UPDATE/DELETE path, mirrors usage_records/alert_events/audit_events/
request_logs. Rows are written by application/verdict_recorder.py (fire-and-forget, own
session) and read by api/router.py's windowed aggregation queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class GuardrailVerdictEventRow(Base):
    """ORM row for the guardrail_verdict_events table (guardrail-analytics §3, FROZEN @ v1)."""

    __tablename__ = "guardrail_verdict_events"
    __table_args__ = (
        Index("ix_guardrail_verdict_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_guardrail_verdict_tenant_guardrail_created", "tenant_id", "guardrail", "created_at"
        ),
        Index("ix_guardrail_verdict_tenant_key_created", "tenant_id", "key_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # No FK — deliberate append-only-ledger pattern, mirrors usage_records.key_id.
    key_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # No FK — team deletion must not cascade into recorded verdict rows, mirrors
    # usage_records.team_id / request_logs.team_id.
    team_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    guardrail: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    policy_source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
