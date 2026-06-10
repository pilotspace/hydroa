"""SQLAlchemy ORM row for api_keys table."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class ApiKeyRow(Base):
    """ORM row for the api_keys table.

    Schema mirrors §3 CONTRACT:
      id uuid PK (uuid7, explicit at construction — no column default)
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
      name text NOT NULL CHECK(length(name) BETWEEN 1 AND 200)
      key_hash text NOT NULL
      created_at timestamptz NOT NULL DEFAULT now()
      revoked_at timestamptz NULL
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint(
            "length(name) BETWEEN 1 AND 200",
            name="api_keys_name_length_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str]
    key_hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
