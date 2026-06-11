"""Domain entities for teams — zero framework imports."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Team:
    """Domain entity for a team scoped to a tenant."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    created_at: datetime
    member_count: int = 0
    key_count: int = 0


@dataclass(frozen=True, slots=True)
class TeamMember:
    """Domain entity for a team membership row."""

    team_id: uuid.UUID
    user_id: uuid.UUID
    role: str  # "lead" | "member"
    added_at: datetime


@dataclass(frozen=True, slots=True)
class TeamDetail:
    """Team with embedded members list for GET /admin/teams/{id} response."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    created_at: datetime
    member_count: int
    key_count: int
    members: list[TeamMember] = field(default_factory=list)
