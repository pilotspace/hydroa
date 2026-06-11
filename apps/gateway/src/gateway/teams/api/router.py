"""FastAPI router for teams endpoints (contract §3)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from gateway.core.errors import ProblemError
from gateway.teams.api.deps import (
    get_add_member_use_case,
    get_create_team_use_case,
    get_delete_team_use_case,
    get_get_team_use_case,
    get_list_teams_use_case,
    get_remove_member_use_case,
    require_owner_or_admin,
)
from gateway.teams.api.schemas import (
    AddMemberRequest,
    AddMemberResponse,
    CreateTeamRequest,
    MemberResponse,
    TeamDetailResponse,
    TeamResponse,
)
from gateway.teams.application.use_cases import (
    AddMemberUseCase,
    CreateTeamUseCase,
    DeleteTeamUseCase,
    GetTeamUseCase,
    ListTeamsUseCase,
    RemoveMemberUseCase,
)
from gateway.teams.domain.errors import (
    MemberExistsError,
    MemberNotFoundError,
    TeamExistsError,
    TeamNotFoundError,
    UserNotFoundError,
)
from gateway.tenants.domain.entities import Identity

teams_router = APIRouter(prefix="/admin/teams", tags=["teams"])


@teams_router.post("", status_code=201, response_model=TeamResponse)
async def create_team(
    body: CreateTeamRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[CreateTeamUseCase, Depends(get_create_team_use_case)],
) -> TeamResponse:
    """POST /admin/teams — create a team scoped to the caller's tenant."""
    try:
        team = await use_case.execute(
            tenant_id=identity.tenant_id,
            name=body.name,
        )
    except TeamExistsError:
        raise ProblemError(409, "ERR_TEAM_EXISTS", "A team with this name already exists") from None

    return TeamResponse(
        id=team.id,
        name=team.name,
        tenant_id=team.tenant_id,
        created_at=team.created_at,
        member_count=team.member_count,
        key_count=team.key_count,
    )


@teams_router.get("", response_model=list[TeamResponse])
async def list_teams(
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[ListTeamsUseCase, Depends(get_list_teams_use_case)],
) -> list[TeamResponse]:
    """GET /admin/teams — list all teams for the caller's tenant."""
    teams = await use_case.execute(tenant_id=identity.tenant_id)
    return [
        TeamResponse(
            id=t.id,
            name=t.name,
            tenant_id=t.tenant_id,
            created_at=t.created_at,
            member_count=t.member_count,
            key_count=t.key_count,
        )
        for t in teams
    ]


@teams_router.get("/{team_id}", response_model=TeamDetailResponse)
async def get_team(
    team_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[GetTeamUseCase, Depends(get_get_team_use_case)],
) -> TeamDetailResponse:
    """GET /admin/teams/{team_id} — get team with members list."""
    try:
        detail = await use_case.execute(
            team_id=team_id,
            tenant_id=identity.tenant_id,
        )
    except TeamNotFoundError:
        raise ProblemError(404, "ERR_TEAM_NOT_FOUND", "Team not found") from None

    return TeamDetailResponse(
        id=detail.id,
        name=detail.name,
        tenant_id=detail.tenant_id,
        created_at=detail.created_at,
        member_count=detail.member_count,
        key_count=detail.key_count,
        members=[
            MemberResponse(
                user_id=m.user_id,
                role=m.role,
                added_at=m.added_at,
            )
            for m in detail.members
        ],
    )


@teams_router.delete("/{team_id}", status_code=204)
async def delete_team(
    team_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[DeleteTeamUseCase, Depends(get_delete_team_use_case)],
) -> None:
    """DELETE /admin/teams/{team_id} — hard delete (cascades members, nulls keys)."""
    try:
        await use_case.execute(
            team_id=team_id,
            tenant_id=identity.tenant_id,
        )
    except TeamNotFoundError:
        raise ProblemError(404, "ERR_TEAM_NOT_FOUND", "Team not found") from None


@teams_router.post("/{team_id}/members", status_code=201, response_model=AddMemberResponse)
async def add_member(
    team_id: uuid.UUID,
    body: AddMemberRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[AddMemberUseCase, Depends(get_add_member_use_case)],
) -> AddMemberResponse:
    """POST /admin/teams/{team_id}/members — add a user to the team."""
    try:
        member = await use_case.execute(
            team_id=team_id,
            tenant_id=identity.tenant_id,
            user_id=body.user_id,
            role=body.role,
        )
    except TeamNotFoundError:
        raise ProblemError(404, "ERR_TEAM_NOT_FOUND", "Team not found") from None
    except UserNotFoundError:
        raise ProblemError(404, "ERR_USER_NOT_FOUND", "User not found in this tenant") from None
    except MemberExistsError:
        raise ProblemError(
            409, "ERR_MEMBER_EXISTS", "User is already a member of this team"
        ) from None

    return AddMemberResponse(
        team_id=member.team_id,
        user_id=member.user_id,
        role=member.role,
        added_at=member.added_at,
    )


@teams_router.delete("/{team_id}/members/{user_id}", status_code=204)
async def remove_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[RemoveMemberUseCase, Depends(get_remove_member_use_case)],
) -> None:
    """DELETE /admin/teams/{team_id}/members/{user_id} — remove a user from the team."""
    try:
        await use_case.execute(
            team_id=team_id,
            tenant_id=identity.tenant_id,
            user_id=user_id,
        )
    except TeamNotFoundError:
        raise ProblemError(404, "ERR_TEAM_NOT_FOUND", "Team not found") from None
    except MemberNotFoundError:
        raise ProblemError(404, "ERR_MEMBER_NOT_FOUND", "Member not found") from None
