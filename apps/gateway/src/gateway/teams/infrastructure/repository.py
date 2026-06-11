"""SQLAlchemy repository for teams."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.teams.domain.entities import Team, TeamDetail, TeamMember
from gateway.teams.domain.errors import (
    MemberExistsError,
    TeamExistsError,
    TeamNotFoundError,
    UserNotFoundError,
)
from gateway.teams.infrastructure.orm import TeamMemberRow, TeamRow

# Lazy-load api_keys ORM to avoid circular imports at module level
_API_KEYS_TABLE = "api_keys"


class SqlAlchemyTeamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        name: str,
    ) -> Team:
        """Insert a new teams row atomically.

        Raises TeamExistsError on duplicate (tenant_id, name).
        """
        row = TeamRow(id=team_id, tenant_id=tenant_id, name=name)
        try:
            async with self._session.begin():
                self._session.add(row)
        except IntegrityError as exc:
            err_str = str(exc.orig).lower() if exc.orig else ""
            if "teams_tenant_name_uq" in err_str or "unique" in err_str:
                raise TeamExistsError from None
            raise

        await self._session.refresh(row)
        return Team(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            created_at=row.created_at,
            member_count=0,
            key_count=0,
            team_budget_usd=row.team_budget_usd,
        )

    async def list_by_tenant(self, tenant_id: uuid.UUID) -> list[Team]:
        """Return all teams with member_count and key_count aggregates."""
        from gateway.keys.infrastructure.orm import ApiKeyRow

        # Subquery: member counts per team
        member_sq = (
            select(TeamMemberRow.team_id, func.count().label("mc"))
            .group_by(TeamMemberRow.team_id)
            .subquery()
        )
        # Subquery: key counts per team
        key_sq = (
            select(ApiKeyRow.team_id, func.count().label("kc"))
            .where(ApiKeyRow.team_id.isnot(None))
            .group_by(ApiKeyRow.team_id)
            .subquery()
        )

        stmt = (
            select(
                TeamRow,
                func.coalesce(member_sq.c.mc, 0).label("member_count"),
                func.coalesce(key_sq.c.kc, 0).label("key_count"),
            )
            .outerjoin(member_sq, TeamRow.id == member_sq.c.team_id)
            .outerjoin(key_sq, TeamRow.id == key_sq.c.team_id)
            .where(TeamRow.tenant_id == tenant_id)
            .order_by(TeamRow.created_at.desc())
        )

        rows = (await self._session.execute(stmt)).all()
        return [
            Team(
                id=row.TeamRow.id,
                tenant_id=row.TeamRow.tenant_id,
                name=row.TeamRow.name,
                created_at=row.TeamRow.created_at,
                member_count=row.member_count,
                key_count=row.key_count,
                team_budget_usd=row.TeamRow.team_budget_usd,
            )
            for row in rows
        ]

    async def get_by_id(
        self,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> TeamDetail | None:
        """Return TeamDetail with members list, scoped to tenant_id."""
        from gateway.keys.infrastructure.orm import ApiKeyRow

        # Count members
        member_count_result = await self._session.execute(
            select(func.count()).where(TeamMemberRow.team_id == team_id)
        )
        # Count keys
        key_count_result = await self._session.execute(
            select(func.count()).where(
                ApiKeyRow.team_id == team_id,
            )
        )

        team_row = (
            await self._session.execute(
                select(TeamRow).where(
                    TeamRow.id == team_id,
                    TeamRow.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()

        if team_row is None:
            return None

        member_rows = (
            (
                await self._session.execute(
                    select(TeamMemberRow).where(TeamMemberRow.team_id == team_id)
                )
            )
            .scalars()
            .all()
        )

        members = [
            TeamMember(
                team_id=m.team_id,
                user_id=m.user_id,
                role=m.role,
                added_at=m.added_at,
            )
            for m in member_rows
        ]

        return TeamDetail(
            id=team_row.id,
            tenant_id=team_row.tenant_id,
            name=team_row.name,
            created_at=team_row.created_at,
            member_count=member_count_result.scalar_one(),
            key_count=key_count_result.scalar_one(),
            members=members,
            team_budget_usd=team_row.team_budget_usd,
        )

    async def delete(
        self,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> bool:
        """Delete team row. Returns True if deleted, False if not found or cross-tenant.

        FK cascade:
          - team_members rows: CASCADE DELETE (DB handles)
          - api_keys.team_id: ON DELETE SET NULL (DB handles)
        """
        result = await self._session.execute(
            delete(TeamRow)
            .where(
                TeamRow.id == team_id,
                TeamRow.tenant_id == tenant_id,
            )
            .returning(TeamRow.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def add_member(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
    ) -> TeamMember:
        """Add user to team atomically.

        Raises TeamNotFoundError if team not in tenant.
        Raises UserNotFoundError if user not in tenant's users table.
        Raises MemberExistsError on duplicate (team_id, user_id).
        """
        from gateway.tenants.infrastructure.orm import UserRow

        async with self._session.begin():
            # Verify team belongs to tenant
            team_row = (
                await self._session.execute(
                    select(TeamRow).where(
                        TeamRow.id == team_id,
                        TeamRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if team_row is None:
                raise TeamNotFoundError

            # Verify user exists in this tenant
            user_row = (
                await self._session.execute(
                    select(UserRow).where(
                        UserRow.id == user_id,
                        UserRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if user_row is None:
                raise UserNotFoundError

            # Insert member row
            member = TeamMemberRow(team_id=team_id, user_id=user_id, role=role)
            try:
                self._session.add(member)
                await self._session.flush()
            except IntegrityError as exc:
                err_str = str(exc.orig).lower() if exc.orig else ""
                if "unique" in err_str or "pkey" in err_str or "primary" in err_str:
                    raise MemberExistsError from None
                raise

        await self._session.refresh(member)
        return TeamMember(
            team_id=member.team_id,
            user_id=member.user_id,
            role=member.role,
            added_at=member.added_at,
        )

    async def remove_member(
        self,
        *,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """Remove user from team. Returns True if removed, False if not a member."""
        result = await self._session.execute(
            delete(TeamMemberRow)
            .where(
                TeamMemberRow.team_id == team_id,
                TeamMemberRow.user_id == user_id,
            )
            .returning(TeamMemberRow.user_id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def get_team_for_tenant(
        self,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> bool:
        """Return True if team_id belongs to tenant_id."""
        row = (
            await self._session.execute(
                select(TeamRow.id).where(
                    TeamRow.id == team_id,
                    TeamRow.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        return row is not None

    async def update_budget(
        self,
        team_id: uuid.UUID,
        tenant_id: uuid.UUID,
        team_budget_usd: Decimal | None,
    ) -> Team | None:
        """Set or clear team_budget_usd for a team scoped to tenant_id.

        Returns the updated Team on success, None if not found or cross-tenant.
        """
        result = await self._session.execute(
            update(TeamRow)
            .where(
                TeamRow.id == team_id,
                TeamRow.tenant_id == tenant_id,
            )
            .values(team_budget_usd=team_budget_usd)
            .returning(TeamRow)
        )
        await self._session.commit()
        row = result.scalar_one_or_none()
        if row is None:
            return None

        # Fetch member_count and key_count aggregates for the response
        from gateway.keys.infrastructure.orm import ApiKeyRow

        member_sq = (
            select(TeamMemberRow.team_id, func.count().label("mc"))
            .where(TeamMemberRow.team_id == team_id)
            .group_by(TeamMemberRow.team_id)
            .subquery()
        )
        key_sq = (
            select(ApiKeyRow.team_id, func.count().label("kc"))
            .where(ApiKeyRow.team_id == team_id)
            .group_by(ApiKeyRow.team_id)
            .subquery()
        )
        agg_row = (
            await self._session.execute(
                select(
                    func.coalesce(member_sq.c.mc, 0).label("member_count"),
                    func.coalesce(key_sq.c.kc, 0).label("key_count"),
                ).select_from(
                    member_sq.join(key_sq, member_sq.c.team_id == key_sq.c.team_id, isouter=True)
                )
            )
        ).fetchone()

        member_count = int(agg_row[0]) if agg_row else 0
        key_count = int(agg_row[1]) if agg_row else 0

        return Team(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            created_at=row.created_at,
            member_count=member_count,
            key_count=key_count,
            team_budget_usd=row.team_budget_usd,
        )
