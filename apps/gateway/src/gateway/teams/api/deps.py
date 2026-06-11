"""Dependency providers for teams API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.errors import ProblemError
from gateway.keys.api.deps import get_identity
from gateway.teams.application.use_cases import (
    AddMemberUseCase,
    CreateTeamUseCase,
    DeleteTeamUseCase,
    GetTeamUseCase,
    ListTeamsUseCase,
    RemoveMemberUseCase,
    UpdateTeamBudgetUseCase,
)
from gateway.teams.infrastructure.repository import SqlAlchemyTeamRepository
from gateway.tenants.domain.entities import Identity, Role


def require_owner_or_admin(
    identity: Annotated[Identity, Depends(get_identity)],
) -> Identity:
    """Raise 403 if the caller is a member."""
    if identity.role == Role.MEMBER:
        raise ProblemError(403, "ERR_AUTH_FORBIDDEN", "Insufficient role for this operation")
    return identity


def get_create_team_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CreateTeamUseCase:
    return CreateTeamUseCase(SqlAlchemyTeamRepository(session))


def get_list_teams_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ListTeamsUseCase:
    return ListTeamsUseCase(SqlAlchemyTeamRepository(session))


def get_get_team_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GetTeamUseCase:
    return GetTeamUseCase(SqlAlchemyTeamRepository(session))


def get_delete_team_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeleteTeamUseCase:
    return DeleteTeamUseCase(SqlAlchemyTeamRepository(session))


def get_add_member_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AddMemberUseCase:
    return AddMemberUseCase(SqlAlchemyTeamRepository(session))


def get_remove_member_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RemoveMemberUseCase:
    return RemoveMemberUseCase(SqlAlchemyTeamRepository(session))


def get_update_team_budget_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UpdateTeamBudgetUseCase:
    return UpdateTeamBudgetUseCase(SqlAlchemyTeamRepository(session))


def get_team_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyTeamRepository:
    return SqlAlchemyTeamRepository(session)
