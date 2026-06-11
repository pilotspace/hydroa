"""SQLAlchemy repository for API keys."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.keys.domain.entities import ApiKey, ApiKeyInfo
from gateway.keys.infrastructure.orm import ApiKeyRow


def _row_to_api_key(row: ApiKeyRow) -> ApiKey:
    """Convert an ORM row to the ApiKey domain entity."""
    return ApiKey(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        key_hash=row.key_hash,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        monthly_budget_usd=row.monthly_budget_usd,
        soft_budget_usd=row.soft_budget_usd,
        expires_at=row.expires_at,
        model_allowlist=row.model_allowlist,
        rotated_from_key_id=row.rotated_from_key_id,
        rpm_limit=row.rpm_limit,
        tpm_limit=row.tpm_limit,
        team_id=row.team_id,
    )


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
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
        team_id: uuid.UUID | None = None,
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
            monthly_budget_usd=monthly_budget_usd,
            soft_budget_usd=soft_budget_usd,
            expires_at=expires_at,
            model_allowlist=model_allowlist,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            team_id=team_id,
        )
        # Use begin_nested() (savepoint) so this works whether or not a transaction
        # is already active on the session (e.g. when team_repo shares the same session).
        async with self._session.begin_nested():
            self._session.add(row)
        await self._session.commit()
        # Refresh to get server-side created_at
        await self._session.refresh(row)
        return _row_to_api_key(row)

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
                monthly_budget_usd=row.monthly_budget_usd,
                soft_budget_usd=row.soft_budget_usd,
                expires_at=row.expires_at,
                model_allowlist=row.model_allowlist,
                rpm_limit=row.rpm_limit,
                tpm_limit=row.tpm_limit,
                team_id=row.team_id,
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
        from gateway.teams.infrastructure.orm import TeamRow

        # LEFT JOIN teams to populate team_budget_usd in one query (zero extra DB reads)
        # MUST be LEFT JOIN so un-teamed keys and deleted-team keys still authenticate
        stmt = (
            select(ApiKeyRow, TeamRow.team_budget_usd)
            .outerjoin(TeamRow, ApiKeyRow.team_id == TeamRow.id)
            .where(ApiKeyRow.id == key_id)
        )
        result = (await self._session.execute(stmt)).first()
        if result is None:
            return None
        row, team_budget_usd = result
        return ApiKey(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            key_hash=row.key_hash,
            created_at=row.created_at,
            revoked_at=row.revoked_at,
            monthly_budget_usd=row.monthly_budget_usd,
            soft_budget_usd=row.soft_budget_usd,
            expires_at=row.expires_at,
            model_allowlist=row.model_allowlist,
            rotated_from_key_id=row.rotated_from_key_id,
            rpm_limit=row.rpm_limit,
            tpm_limit=row.tpm_limit,
            team_id=row.team_id,
            team_budget_usd=team_budget_usd,
        )

    async def update(
        self,
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
        team_id: uuid.UUID | None = None,
        _fields_to_clear: set[str] | None = None,
    ) -> ApiKey | None:
        """Update governance fields on an active (non-revoked) key owned by tenant_id.

        Returns updated ApiKey on success, None when not found/revoked/cross-tenant.
        _fields_to_clear: set of field names to explicitly set to NULL.
        """
        # Fetch the active key for this tenant
        row = (
            await self._session.execute(
                select(ApiKeyRow).where(
                    ApiKeyRow.id == key_id,
                    ApiKeyRow.tenant_id == tenant_id,
                    ApiKeyRow.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        # Apply updates — only fields that were explicitly provided
        clear = _fields_to_clear or set()
        if monthly_budget_usd is not None or "monthly_budget_usd" in clear:
            row.monthly_budget_usd = None if "monthly_budget_usd" in clear else monthly_budget_usd
        if soft_budget_usd is not None or "soft_budget_usd" in clear:
            row.soft_budget_usd = None if "soft_budget_usd" in clear else soft_budget_usd
        if expires_at is not None or "expires_at" in clear:
            row.expires_at = None if "expires_at" in clear else expires_at
        if model_allowlist is not None or "model_allowlist" in clear:
            row.model_allowlist = None if "model_allowlist" in clear else model_allowlist
        if rpm_limit is not None or "rpm_limit" in clear:
            row.rpm_limit = None if "rpm_limit" in clear else rpm_limit
        if tpm_limit is not None or "tpm_limit" in clear:
            row.tpm_limit = None if "tpm_limit" in clear else tpm_limit
        # team_id: absent (not in clear, team_id arg is None) = no change
        #           "team_id" in clear = set to NULL
        #           team_id is not None = set to UUID value
        if team_id is not None or "team_id" in clear:
            row.team_id = None if "team_id" in clear else team_id

        await self._session.commit()
        await self._session.refresh(row)
        return _row_to_api_key(row)

    async def rotate(
        self,
        *,
        old_key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        new_key_id: uuid.UUID,
        new_key_hash: str,
        new_name: str,
        monthly_budget_usd: Decimal | None = None,
        soft_budget_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        model_allowlist: list[str] | None = None,
    ) -> ApiKey | None:
        """Atomically revoke old key and insert new key in a single transaction.

        Returns the new ApiKey on success, None when old key not found/revoked/cross-tenant.
        Atomicity guarantee: both revoke + insert are committed together (M5/A6).

        Uses begin_nested() (savepoint) so this method is safe to call whether or not
        the session already has an active autobegin transaction.
        """
        # begin_nested() works both when a transaction is already open (uses savepoint)
        # and when none is open yet (starts one). The outer commit() flushes everything.
        async with self._session.begin_nested():
            # Fetch old key — must be active (not revoked) and owned by this tenant
            old_row = (
                await self._session.execute(
                    select(ApiKeyRow).where(
                        ApiKeyRow.id == old_key_id,
                        ApiKeyRow.tenant_id == tenant_id,
                        ApiKeyRow.revoked_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if old_row is None:
                return None

            # Revoke the old key in the same savepoint
            old_row.revoked_at = func.now()

            # Create the new key row
            new_row = ApiKeyRow(
                id=new_key_id,
                tenant_id=tenant_id,
                name=new_name,
                key_hash=new_key_hash,
                monthly_budget_usd=monthly_budget_usd,
                soft_budget_usd=soft_budget_usd,
                expires_at=expires_at,
                model_allowlist=model_allowlist,
                rotated_from_key_id=old_key_id,
            )
            self._session.add(new_row)
            # Savepoint released here — both writes flushed to the outer transaction

        # Commit the outer transaction (includes both the revoke + insert)
        await self._session.commit()

        # Refresh new row to get server-side created_at
        await self._session.refresh(new_row)
        return _row_to_api_key(new_row)
