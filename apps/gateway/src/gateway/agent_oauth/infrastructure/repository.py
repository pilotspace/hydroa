"""SQLAlchemy repository for the agent OAuth device-grant store.

Receives already-hashed secrets + an injected `now`. Mint is a single transaction
(INSERT agent_tokens + UPDATE device_authorizations.status='consumed'); resolve is
fail-closed (revoked / expired / unknown → None). State checks raise the §1 domain errors.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.domain.entities import (
    AgentPrincipal,
    AgentToken,
    AgentTokenBinding,
    DeviceAuthorization,
)
from gateway.agent_oauth.domain.errors import (
    AgentPrincipalKilledError,
    AgentPrincipalNameConflictError,
    AgentPrincipalNotFoundError,
    AgentTokenAlreadyAttachedError,
    AgentTokenNotFoundError,
    AuthorizationAlreadyConsumedError,
    AuthorizationExpiredError,
    AuthorizationNotApprovedError,
    AuthorizationNotPendingError,
    DeviceCodeCollisionError,
)
from gateway.agent_oauth.infrastructure.orm import (
    AgentPrincipalRow,
    AgentTokenRow,
    DeviceAuthorizationRow,
)
from gateway.core.ids import uuid7


def _to_authorization(row: DeviceAuthorizationRow) -> DeviceAuthorization:
    return DeviceAuthorization(
        id=row.id,
        status=row.status,
        scope=row.scope,
        interval_seconds=row.interval_seconds,
        created_at=row.created_at,
        expires_at=row.expires_at,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        approved_at=row.approved_at,
        consumed_at=row.consumed_at,
    )


def _to_principal(row: AgentPrincipalRow, *, attached_token_count: int) -> AgentPrincipal:
    return AgentPrincipal(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        owner_user_id=row.owner_user_id,
        monthly_budget_usd=row.monthly_budget_usd,
        rpm_limit=row.rpm_limit,
        tpm_limit=row.tpm_limit,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        killed_at=row.killed_at,
        attached_token_count=attached_token_count,
    )


def _to_token(row: AgentTokenRow) -> AgentToken:
    return AgentToken(
        id=row.id,
        authorization_id=row.authorization_id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        scope=row.scope,
        created_at=row.created_at,
        access_expires_at=row.access_expires_at,
        refresh_expires_at=row.refresh_expires_at,
        revoked_at=row.revoked_at,
    )


class SqlAlchemyAgentOAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pending(
        self,
        *,
        device_code_hash: str,
        user_code_hash: str,
        scope: str,
        interval_seconds: int,
        expires_at: datetime,
    ) -> DeviceAuthorization:
        row = DeviceAuthorizationRow(
            id=uuid7(),
            device_code_hash=device_code_hash,
            user_code_hash=user_code_hash,
            status="pending",
            scope=scope,
            interval_seconds=interval_seconds,
            expires_at=expires_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise DeviceCodeCollisionError() from exc
        await self._session.commit()
        await self._session.refresh(row)
        return _to_authorization(row)

    async def get_by_device_code_hash(self, device_code_hash: str) -> DeviceAuthorization | None:
        row = await self._session.scalar(
            select(DeviceAuthorizationRow).where(
                DeviceAuthorizationRow.device_code_hash == device_code_hash
            )
        )
        return _to_authorization(row) if row is not None else None

    async def get_by_user_code_hash(self, user_code_hash: str) -> DeviceAuthorization | None:
        row = await self._session.scalar(
            select(DeviceAuthorizationRow).where(
                DeviceAuthorizationRow.user_code_hash == user_code_hash
            )
        )
        return _to_authorization(row) if row is not None else None

    async def _locked_authorization(
        self, authorization_id: uuid.UUID
    ) -> DeviceAuthorizationRow | None:
        return await self._session.scalar(
            select(DeviceAuthorizationRow)
            .where(DeviceAuthorizationRow.id == authorization_id)
            .with_for_update()
        )

    async def approve(
        self,
        *,
        authorization_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        now: datetime,
    ) -> DeviceAuthorization:
        row = await self._locked_authorization(authorization_id)
        if row is None or row.status != "pending":
            await self._session.rollback()
            raise AuthorizationNotPendingError()
        if row.expires_at <= now:
            await self._session.rollback()
            raise AuthorizationExpiredError()
        row.status = "approved"
        row.tenant_id = tenant_id
        row.user_id = user_id
        row.approved_at = now
        await self._session.commit()
        await self._session.refresh(row)
        return _to_authorization(row)

    async def deny(self, *, authorization_id: uuid.UUID) -> DeviceAuthorization:
        row = await self._locked_authorization(authorization_id)
        if row is None or row.status != "pending":
            await self._session.rollback()
            raise AuthorizationNotPendingError()
        row.status = "denied"
        await self._session.commit()
        await self._session.refresh(row)
        return _to_authorization(row)

    async def mint_token(
        self,
        *,
        authorization_id: uuid.UUID,
        access_token_hash: str,
        refresh_token_hash: str | None,
        access_expires_at: datetime,
        refresh_expires_at: datetime | None,
        now: datetime,
    ) -> AgentToken:
        row = await self._locked_authorization(authorization_id)
        if row is None or row.status == "consumed":
            await self._session.rollback()
            raise AuthorizationAlreadyConsumedError()
        if row.status != "approved":
            await self._session.rollback()
            raise AuthorizationNotApprovedError()
        if row.tenant_id is None or row.user_id is None:  # defensive: approved implies bound
            await self._session.rollback()
            raise AuthorizationNotApprovedError()

        token = AgentTokenRow(
            id=uuid7(),
            authorization_id=authorization_id,
            access_token_hash=access_token_hash,
            refresh_token_hash=refresh_token_hash,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            scope=row.scope,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )
        # Single transaction: the token INSERT and the consume UPDATE commit together.
        row.status = "consumed"
        row.consumed_at = now
        self._session.add(token)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # UNIQUE(authorization_id) backstop — a concurrent mint already consumed it.
            await self._session.rollback()
            raise AuthorizationAlreadyConsumedError() from exc
        await self._session.commit()
        await self._session.refresh(token)
        return _to_token(token)

    async def resolve_access_token(
        self, *, access_token_hash: str, now: datetime
    ) -> AgentTokenBinding | None:
        # agent-identity-governance TASK.md §3 (FROZEN @ v1): LEFT JOIN agent_principals
        # so an attached token's principal budget rides along for free (zero extra DB
        # reads — mirrors SqlAlchemyApiKeyRepository.get_by_id's own team/tenant LEFT
        # JOIN precedent). No killed-principal re-check needed here: a kill's own
        # transaction already sets THIS row's revoked_at (M7), so the FAIL-CLOSED check
        # immediately below already rejects it — the kill is effective BY CONSTRUCTION.
        row_result = await self._session.execute(
            select(AgentTokenRow, AgentPrincipalRow.monthly_budget_usd)
            .outerjoin(AgentPrincipalRow, AgentTokenRow.principal_id == AgentPrincipalRow.id)
            .where(AgentTokenRow.access_token_hash == access_token_hash)
        )
        pair = row_result.first()
        if pair is None:
            return None
        row, principal_budget_usd = pair
        if row.revoked_at is not None or row.access_expires_at <= now:
            return None  # FAIL-CLOSED
        return AgentTokenBinding(
            token_id=row.id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            scope=row.scope,
            principal_id=row.principal_id,
            principal_budget_usd=principal_budget_usd,
        )

    # -------------------------------------------------------------------------
    # agent-identity-governance TASK.md §3 (FROZEN @ v1) — agent principals
    # -------------------------------------------------------------------------

    async def create_principal(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
        owner_user_id: uuid.UUID | None,
        monthly_budget_usd: Decimal | None,
        rpm_limit: int | None,
        tpm_limit: int | None,
    ) -> AgentPrincipal:
        row = AgentPrincipalRow(
            id=uuid7(),
            tenant_id=tenant_id,
            name=name,
            owner_user_id=owner_user_id,
            monthly_budget_usd=monthly_budget_usd,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AgentPrincipalNameConflictError() from exc
        await self._session.commit()
        await self._session.refresh(row)
        return _to_principal(row, attached_token_count=0)

    async def list_principals(self, *, tenant_id: uuid.UUID) -> list[AgentPrincipal]:
        result = await self._session.execute(
            select(AgentPrincipalRow, func.count(AgentTokenRow.id))
            .outerjoin(AgentTokenRow, AgentTokenRow.principal_id == AgentPrincipalRow.id)
            .where(AgentPrincipalRow.tenant_id == tenant_id)
            .group_by(AgentPrincipalRow.id)
            .order_by(AgentPrincipalRow.created_at.desc())
        )
        return [_to_principal(row, attached_token_count=count) for row, count in result.all()]

    async def _get_principal_scoped(
        self, *, principal_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> AgentPrincipalRow | None:
        return await self._session.scalar(
            select(AgentPrincipalRow).where(
                AgentPrincipalRow.id == principal_id, AgentPrincipalRow.tenant_id == tenant_id
            )
        )

    async def kill_principal(
        self, *, principal_id: uuid.UUID, tenant_id: uuid.UUID, now: datetime
    ) -> tuple[AgentPrincipal, bool]:
        # Single conditional UPDATE — Postgres row-level locking decides the race via
        # its OWN row-count, never a prior SELECT (§3 CONTRACT).
        result = await self._session.execute(
            update(AgentPrincipalRow)
            .where(
                AgentPrincipalRow.id == principal_id,
                AgentPrincipalRow.tenant_id == tenant_id,
                AgentPrincipalRow.killed_at.is_(None),
            )
            .values(killed_at=now)
            .returning(AgentPrincipalRow.id)
        )
        won_race = result.scalar_one_or_none() is not None
        if won_race:
            # SAME transaction: bulk-revoke every attached, not-yet-revoked token.
            await self._session.execute(
                update(AgentTokenRow)
                .where(
                    AgentTokenRow.principal_id == principal_id,
                    AgentTokenRow.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            await self._session.commit()
            row = await self._get_principal_scoped(principal_id=principal_id, tenant_id=tenant_id)
            assert row is not None  # just updated in this same transaction
            return _to_principal(row, attached_token_count=0), True

        # Lost the race (or never existed) — one cheap follow-up read disambiguates
        # "already killed" (200 idempotent no-op) from "unknown/cross-tenant" (404).
        await self._session.rollback()
        row = await self._get_principal_scoped(principal_id=principal_id, tenant_id=tenant_id)
        if row is None:
            raise AgentPrincipalNotFoundError()
        return _to_principal(row, attached_token_count=0), False

    async def attach_token(
        self, *, principal_id: uuid.UUID, token_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        # Single conditional UPDATE — never a separate SELECT-then-UPDATE (closes the
        # same TOCTOU race mint_token's with_for_update() closes for device
        # authorizations). The killed-principal check rides in the SAME WHERE clause
        # via a correlated EXISTS subquery — a kill racing an in-flight attach cannot
        # attach a token to a principal that just became killed.
        result = await self._session.execute(
            update(AgentTokenRow)
            .where(
                AgentTokenRow.id == token_id,
                AgentTokenRow.tenant_id == tenant_id,
                AgentTokenRow.principal_id.is_(None),
                select(AgentPrincipalRow.id)
                .where(
                    AgentPrincipalRow.id == principal_id,
                    AgentPrincipalRow.tenant_id == tenant_id,
                    AgentPrincipalRow.killed_at.is_(None),
                )
                .exists(),
            )
            .values(principal_id=principal_id)
            .returning(AgentTokenRow.id)
        )
        if result.scalar_one_or_none() is not None:
            await self._session.commit()
            return

        # 0 rows updated — disambiguate with cheap follow-up reads (priority order:
        # principal existence -> token existence -> principal killed -> already attached).
        await self._session.rollback()
        principal = await self._get_principal_scoped(principal_id=principal_id, tenant_id=tenant_id)
        if principal is None:
            raise AgentPrincipalNotFoundError()
        token = await self._session.scalar(
            select(AgentTokenRow).where(
                AgentTokenRow.id == token_id, AgentTokenRow.tenant_id == tenant_id
            )
        )
        if token is None:
            raise AgentTokenNotFoundError()
        if principal.killed_at is not None:
            raise AgentPrincipalKilledError()
        raise AgentTokenAlreadyAttachedError()

    async def detach_token(
        self, *, principal_id: uuid.UUID, token_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        result = await self._session.execute(
            update(AgentTokenRow)
            .where(
                AgentTokenRow.id == token_id,
                AgentTokenRow.tenant_id == tenant_id,
                AgentTokenRow.principal_id == principal_id,
            )
            .values(principal_id=None)
            .returning(AgentTokenRow.id)
        )
        if result.scalar_one_or_none() is not None:
            await self._session.commit()
            return

        await self._session.rollback()
        principal = await self._get_principal_scoped(principal_id=principal_id, tenant_id=tenant_id)
        if principal is None:
            raise AgentPrincipalNotFoundError()
        raise AgentTokenNotFoundError()
