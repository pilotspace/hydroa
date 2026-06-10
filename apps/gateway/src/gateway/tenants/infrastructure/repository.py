import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.ids import uuid7
from gateway.tenants.domain.entities import Role, User
from gateway.tenants.domain.errors import EmailAlreadyRegisteredError
from gateway.tenants.infrastructure.orm import TenantRow, UserRow


class SqlAlchemyIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_tenant_with_owner(
        self, *, tenant_name: str, email: str, password_hash: str
    ) -> tuple[uuid.UUID, uuid.UUID]:
        tenant = TenantRow(id=uuid7(), name=tenant_name)
        user = UserRow(
            id=uuid7(),
            tenant_id=tenant.id,
            email=email,
            password_hash=password_hash,
            role=Role.OWNER,
        )
        try:
            # Safety rule (TASK §5): both inserts in ONE transaction — or neither.
            async with self._session.begin():
                self._session.add(tenant)
                self._session.add(user)
        except IntegrityError as exc:
            raise EmailAlreadyRegisteredError from exc
        return tenant.id, user.id

    async def get_user_by_email(self, email: str) -> User | None:
        row = (
            await self._session.execute(select(UserRow).where(UserRow.email == email))
        ).scalar_one_or_none()
        if row is None:
            return None
        return User(
            id=row.id,
            tenant_id=row.tenant_id,
            email=row.email,
            password_hash=row.password_hash,
            role=Role(row.role),
        )
