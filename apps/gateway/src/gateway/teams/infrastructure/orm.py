"""SQLAlchemy ORM rows for teams and team_members tables."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class TeamRow(Base):
    """ORM row for the teams table.

    DDL per TASK.md §3 contract:
      id         UUID        PRIMARY KEY
      tenant_id  UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
      name       TEXT        NOT NULL CHECK(length(name) BETWEEN 1 AND 200)
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
      UNIQUE (tenant_id, name)

    Forward-compat note (team-governance task):
      No __table_args__ that would block an additive team_budget_usd column.
      The CheckConstraint and UniqueConstraint are defined per §3 DDL.
    """

    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint(
            "length(name) BETWEEN 1 AND 200",
            name="teams_name_length_check",
        ),
        UniqueConstraint("tenant_id", "name", name="teams_tenant_name_uq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TeamMemberRow(Base):
    """ORM row for the team_members table.

    DDL per TASK.md §3 contract:
      team_id   UUID        NOT NULL REFERENCES teams(id)  ON DELETE CASCADE
      user_id   UUID        NOT NULL REFERENCES users(id)  ON DELETE CASCADE
      role      TEXT        NOT NULL CHECK(role IN ('lead', 'member'))
      added_at  TIMESTAMPTZ NOT NULL DEFAULT now()
      PRIMARY KEY (team_id, user_id)
    """

    __tablename__ = "team_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('lead', 'member')",
            name="team_members_role_check",
        ),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())
