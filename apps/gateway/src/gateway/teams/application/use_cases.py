"""Application use cases for teams."""

from __future__ import annotations

import uuid
from decimal import Decimal

from gateway.teams.domain.entities import Team, TeamDetail, TeamMember
from gateway.teams.domain.errors import (
    MemberNotFoundError,
    TeamNotFoundError,
)
from gateway.teams.domain.ports import TeamRepository


class CreateTeamUseCase:
    def __init__(self, repository: TeamRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
    ) -> Team:
        """Create a new team for the tenant.

        Raises TeamExistsError if name is already taken in this tenant.
        """
        team_id = uuid.uuid4()
        return await self._repo.create(
            team_id=team_id,
            tenant_id=tenant_id,
            name=name,
        )


class ListTeamsUseCase:
    def __init__(self, repository: TeamRepository) -> None:
        self._repo = repository

    async def execute(self, *, tenant_id: uuid.UUID) -> list[Team]:
        return await self._repo.list_by_tenant(tenant_id)


class GetTeamUseCase:
    def __init__(self, repository: TeamRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> TeamDetail:
        """Fetch team with members. Raises TeamNotFoundError if not found or cross-tenant."""
        result = await self._repo.get_by_id(team_id=team_id, tenant_id=tenant_id)
        if result is None:
            raise TeamNotFoundError
        return result


class DeleteTeamUseCase:
    def __init__(self, repository: TeamRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        """Delete team. Raises TeamNotFoundError if not found or cross-tenant."""
        deleted = await self._repo.delete(team_id=team_id, tenant_id=tenant_id)
        if not deleted:
            raise TeamNotFoundError


class AddMemberUseCase:
    def __init__(self, repository: TeamRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
    ) -> TeamMember:
        """Add user to team.

        Raises TeamNotFoundError, UserNotFoundError, or MemberExistsError as appropriate.
        """
        return await self._repo.add_member(
            team_id=team_id,
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
        )


class RemoveMemberUseCase:
    def __init__(self, repository: TeamRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Remove user from team.

        Raises TeamNotFoundError if team not found or cross-tenant.
        Raises MemberNotFoundError if user is not a member.
        """
        # First verify team exists for this tenant
        detail = await self._repo.get_by_id(team_id=team_id, tenant_id=tenant_id)
        if detail is None:
            raise TeamNotFoundError
        removed = await self._repo.remove_member(
            team_id=team_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if not removed:
            raise MemberNotFoundError


class UpdateTeamBudgetUseCase:
    """Set or clear team_budget_usd on an existing team (PATCH semantics)."""

    def __init__(self, repository: TeamRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        team_budget_usd: Decimal | None,
    ) -> Team:
        """Update team_budget_usd.

        Raises TeamNotFoundError if team not found or cross-tenant.
        """
        result = await self._repo.update_budget(
            team_id=team_id,
            tenant_id=tenant_id,
            team_budget_usd=team_budget_usd,
        )
        if result is None:
            raise TeamNotFoundError
        return result
