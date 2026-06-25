"""SQLAlchemy repository for the agent OAuth device-grant store.

Receives already-hashed secrets + an injected `now`. Mint is a single transaction
(INSERT agent_tokens + UPDATE device_authorizations.status='consumed'); resolve is
fail-closed (revoked / expired / unknown → None). State checks raise the §1 domain errors.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.domain.entities import (
    AgentToken,
    AgentTokenBinding,
    DeviceAuthorization,
)
from gateway.agent_oauth.domain.errors import (
    AuthorizationAlreadyConsumedError,
    AuthorizationExpiredError,
    AuthorizationNotApprovedError,
    AuthorizationNotPendingError,
    DeviceCodeCollisionError,
)
from gateway.agent_oauth.infrastructure.orm import AgentTokenRow, DeviceAuthorizationRow
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
        row = await self._session.scalar(
            select(AgentTokenRow).where(AgentTokenRow.access_token_hash == access_token_hash)
        )
        if row is None:
            return None
        if row.revoked_at is not None or row.access_expires_at <= now:
            return None  # FAIL-CLOSED
        return AgentTokenBinding(
            token_id=row.id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            scope=row.scope,
        )
