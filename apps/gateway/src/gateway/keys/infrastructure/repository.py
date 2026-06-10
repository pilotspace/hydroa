"""SQLAlchemy repository for API keys."""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.keys.domain.entities import ApiKey, ApiKeyInfo
from gateway.keys.infrastructure.orm import ApiKeyRow


class SqlAlchemyApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        name: str,
        key_hash: str,
    ) -> ApiKey:
        """Insert a new api_keys row.

        key_id is passed explicitly — never rely on column default for uuid7
        because the plaintext key embeds the id hex and must be consistent.
        Safety (task §5): key_hash stored; plaintext secret never touches this layer.
        """
        row = ApiKeyRow(
            id=key_id,
            tenant_id=tenant_id,
            name=name,
            key_hash=key_hash,
        )
        async with self._session.begin():
            self._session.add(row)
        # Refresh to get server-side created_at
        await self._session.refresh(row)
        return ApiKey(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            key_hash=row.key_hash,
            created_at=row.created_at,
            revoked_at=row.revoked_at,
        )

    async def list_by_tenant(self, tenant_id: uuid.UUID) -> list[ApiKeyInfo]:
        result = await self._session.execute(
            select(ApiKeyRow)
            .where(ApiKeyRow.tenant_id == tenant_id)
            .order_by(ApiKeyRow.created_at.desc())
        )
        rows = result.scalars().all()
        return [
            ApiKeyInfo(
                key_id=row.id,
                name=row.name,
                prefix=f"sk-{row.id.hex[:8]}",
                created_at=row.created_at,
                revoked_at=row.revoked_at,
            )
            for row in rows
        ]

    async def revoke(self, *, key_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """Set revoked_at = now() WHERE id = key_id AND tenant_id = tenant_id.

        Returns True if exactly one row was updated, False if zero rows matched
        (key_id unknown OR belongs to another tenant — identical result, no leak).
        """
        result = await self._session.execute(
            update(ApiKeyRow)
            .where(ApiKeyRow.id == key_id, ApiKeyRow.tenant_id == tenant_id)
            .values(revoked_at=func.now())
            .returning(ApiKeyRow.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def get_by_id(self, key_id: uuid.UUID) -> ApiKey | None:
        row = (
            await self._session.execute(select(ApiKeyRow).where(ApiKeyRow.id == key_id))
        ).scalar_one_or_none()
        if row is None:
            return None
        return ApiKey(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            key_hash=row.key_hash,
            created_at=row.created_at,
            revoked_at=row.revoked_at,
        )
