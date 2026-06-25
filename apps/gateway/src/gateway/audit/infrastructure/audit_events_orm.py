"""SQLAlchemy ORM row for the audit_events table.

Schema contract (audit-log-store TASK.md §3 — FROZEN @ v1):
  audit_events (
    id            UUID         PRIMARY KEY,
    tenant_id     UUID         NULL,
    actor_user_id UUID         NULL,
    actor_email   TEXT         NULL,
    action        TEXT         NOT NULL,
    target_type   TEXT         NULL,
    target_id     TEXT         NULL,
    result        TEXT         NOT NULL,
    metadata      JSONB        NOT NULL  (ORM attribute: event_metadata to avoid SA collision),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
  )

IMMUTABILITY: enforced at the DB level by a RULE that blocks UPDATE and DELETE
(migration creates: CREATE RULE audit_events_no_update ... DO INSTEAD NOTHING;
                    CREATE RULE audit_events_no_delete ... DO INSTEAD NOTHING).

NOTE: tenant_id FK (→ tenants.id) is intentionally absent from this ORM mapped_column,
following the alert_events_orm.py pattern, so Base.metadata.create_all (used in test
schema bootstrap) does NOT enforce the FK.

NOTE: The ORM attribute is named 'event_metadata' to avoid collision with SQLAlchemy
internals; it maps to the DB column 'metadata' via 'key' / column name parameter.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class AuditEventRow(Base):
    """Append-only audit event row — owned by audit-log-store.

    NOTE: tenant_id FK (audit_events_tenant_id_fkey → tenants.id) is intentionally
    absent from this ORM mapped_column so Base.metadata.create_all in tests does NOT
    enforce the FK constraint, allowing system events with NULL tenant_id without
    requiring a matching tenants row.

    NOTE: ORM attribute is 'event_metadata'; DB column is 'metadata'.
    """

    __tablename__ = "audit_events"

    __table_args__ = (
        Index(
            "audit_events_tenant_created_idx",
            "tenant_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    actor_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    # ORM attribute 'event_metadata' maps to DB column 'metadata'
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
