"""Domain ports (Protocol interfaces) for teams."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Protocol

from gateway.teams.domain.entities import Team, TeamDetail, TeamMember


class TeamRepository(Protocol):
    async def create(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        name: str,
    ) -> Team:
        """Insert a new teams row; raises TeamExistsError on duplicate (tenant_id, name)."""
        ...

    async def list_by_tenant(self, tenant_id: uuid.UUID) -> list[Team]:
        """Return all teams for a tenant with member_count and key_count aggregates."""
        ...

    async def get_by_id(
        self,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> TeamDetail | None:
        """Return team with members list, scoped to tenant_id. None = not found or cross-tenant."""
        ...

    async def delete(
        self,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> bool:
        """Delete team row (cascades members; keys get team_id = NULL via DB FK).

        Returns True if deleted, False if not found or cross-tenant.
        """
        ...

    async def add_member(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        email: str | None = None,
        role: str,
    ) -> TeamMember:
        """Add user to team by user_id OR email (exactly one).

        When user_id is None, `email` is resolved tenant-scoped to a user_id.

        Raises TeamNotFoundError if team_id not found for tenant.
        Raises UserNotFoundError if user_id/email not found in tenant.
        Raises MemberExistsError if user is already a member.
        """
        ...

    async def remove_member(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """Remove user from team.

        Returns True if removed, False if member not found.
        Raises TeamNotFoundError if team_id not found for tenant.
        """
        ...

    async def get_team_for_tenant(
        self,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> bool:
        """Return True if team_id belongs to tenant_id; False otherwise.

        Used by keys module to validate team attribution without coupling modules.
        """
        ...

    async def update_budget(
        self,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        team_budget_usd: Decimal | None,
    ) -> Team | None:
        """Set or clear team_budget_usd for a team scoped to tenant_id.

        Returns the updated Team on success, None if not found or cross-tenant.
        """
        ...
