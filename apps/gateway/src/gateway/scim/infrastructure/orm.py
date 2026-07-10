"""SQLAlchemy ORM row for the scim_tokens table."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class ScimTokenRow(Base):
    """ORM row for the scim_tokens table (scim-provisioning TASK.md §3, FROZEN @ v1).

    Schema:
      id                  uuid PK (uuid7, explicit at construction — no column default)
      tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
      name                text NOT NULL CHECK(length(name) BETWEEN 1 AND 200)
      token_hash          text NOT NULL
      created_by_user_id  uuid NULL REFERENCES users(id) ON DELETE SET NULL
      created_at          timestamptz NOT NULL DEFAULT now()
      revoked_at          timestamptz NULL
    """

    __tablename__ = "scim_tokens"
    __table_args__ = (
        CheckConstraint(
            "length(name) BETWEEN 1 AND 200",
            name="scim_tokens_name_length_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
