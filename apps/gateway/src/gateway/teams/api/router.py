"""FastAPI router for teams endpoints (contract §3)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.error_catalog import (
    MEMBER_EXISTS,
    MEMBER_NOT_FOUND,
    TEAM_EXISTS,
    TEAM_NOT_FOUND,
    USER_NOT_FOUND,
)
from gateway.teams.api.deps import (
    get_add_member_use_case,
    get_create_team_use_case,
    get_delete_team_use_case,
    get_get_team_use_case,
    get_list_teams_use_case,
    get_remove_member_use_case,
    get_update_team_budget_use_case,
    require_owner_or_admin,
)
from gateway.teams.api.schemas import (
    AddMemberRequest,
    AddMemberResponse,
    CreateTeamRequest,
    MemberResponse,
    PatchTeamBudgetRequest,
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
    UpdateTeamBudgetUseCase,
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
        raise TEAM_EXISTS.exc() from None

    return TeamResponse(
        id=team.id,
        name=team.name,
        tenant_id=team.tenant_id,
        created_at=team.created_at,
        member_count=team.member_count,
        key_count=team.key_count,
        team_budget_usd=str(team.team_budget_usd) if team.team_budget_usd is not None else None,
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
            team_budget_usd=str(t.team_budget_usd) if t.team_budget_usd is not None else None,
        )
        for t in teams
    ]


@teams_router.patch("/{team_id}", response_model=TeamResponse)
async def patch_team_budget(
    team_id: uuid.UUID,
    body: PatchTeamBudgetRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[UpdateTeamBudgetUseCase, Depends(get_update_team_budget_use_case)],
) -> TeamResponse:
    """PATCH /admin/teams/{team_id} — set or clear team_budget_usd."""
    from decimal import Decimal

    budget = Decimal(body.team_budget_usd) if body.team_budget_usd is not None else None
    try:
        team = await use_case.execute(
            team_id=team_id,
            tenant_id=identity.tenant_id,
            team_budget_usd=budget,
        )
    except TeamNotFoundError:
        raise TEAM_NOT_FOUND.exc() from None

    return TeamResponse(
        id=team.id,
        name=team.name,
        tenant_id=team.tenant_id,
        created_at=team.created_at,
        member_count=team.member_count,
        key_count=team.key_count,
        team_budget_usd=str(team.team_budget_usd) if team.team_budget_usd is not None else None,
    )


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
        raise TEAM_NOT_FOUND.exc() from None

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
        team_budget_usd=str(detail.team_budget_usd) if detail.team_budget_usd is not None else None,
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
        raise TEAM_NOT_FOUND.exc() from None


@teams_router.post("/{team_id}/members", status_code=201, response_model=AddMemberResponse)
async def add_member(
    request: Request,
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
            email=body.email,
            role=body.role,
        )
    except TeamNotFoundError:
        raise TEAM_NOT_FOUND.exc() from None
    except UserNotFoundError:
        raise USER_NOT_FOUND.exc() from None
    except MemberExistsError:
        raise MEMBER_EXISTS.exc() from None

    # Audit emit — fail-open fire-and-forget (role assignment; no secrets in metadata)
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="member.role_assign",
                target_type="user",
                target_id=str(member.user_id),
                result="success",
                metadata={
                    "team_id": str(team_id),
                    "assigned_role": member.role,
                },
                created_at=datetime.now(UTC),
            ),
        )
    )

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
        raise TEAM_NOT_FOUND.exc() from None
    except MemberNotFoundError:
        raise MEMBER_NOT_FOUND.exc() from None
