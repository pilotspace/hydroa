import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.domain.errors import OidcTenantConflictError
from gateway.auth.domain.saml_errors import SamlTenantConflictError
from gateway.core.ids import uuid7
from gateway.tenants.application.entitlements import assert_seat_available
from gateway.tenants.domain.entities import Role, User
from gateway.tenants.domain.errors import EmailAlreadyRegisteredError, SeatCapExceededError
from gateway.tenants.infrastructure.orm import TenantRow, UserRow


async def get_platform_tenant(session: AsyncSession) -> TenantRow | None:
    """Resolve the reserved platform tenant row — the sole sanctioned lookup.

    Returns None (never raises) if the seed migration has not run yet, so
    callers on an unmigrated DB degrade rather than crash. An unmigrated DB
    surfaces as a ProgrammingError (undefined column/relation) that aborts the
    current transaction — rollback so the session stays usable for the caller.
    """
    try:
        return (
            await session.execute(select(TenantRow).where(TenantRow.kind == "platform"))
        ).scalar_one_or_none()
    except ProgrammingError:
        await session.rollback()
        return None


async def list_tenants(
    session: AsyncSession, *, q: str | None, limit: int, offset: int
) -> tuple[list[TenantRow], int]:
    """List every tenant (kind='customer' AND kind='platform'), optionally filtered by a
    case-insensitive name substring — the superadmin-only cross-tenant directory
    (platform-tenant-directory TASK.md §3). Never filters by tenant_id — the deliberate,
    narrow exception to the WHERE tenant_id = identity.tenant_id default (authz.py docstring).
    """
    stmt = select(TenantRow)
    count_stmt = select(func.count()).select_from(TenantRow)
    if q:
        name_filter = TenantRow.name.ilike(f"%{q}%")
        stmt = stmt.where(name_filter)
        count_stmt = count_stmt.where(name_filter)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        (await session.execute(stmt.order_by(TenantRow.created_at).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), total


async def get_tenant_by_id(session: AsyncSession, tenant_id: uuid.UUID) -> TenantRow | None:
    """Resolve any tenant by id, regardless of kind — the superadmin-only cross-tenant
    get-one (platform-tenant-directory TASK.md §3)."""
    return (
        await session.execute(select(TenantRow).where(TenantRow.id == tenant_id))
    ).scalar_one_or_none()


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

    async def join_verified_tenant_domain(
        self, *, tenant_id: uuid.UUID, email: str, password_hash: str
    ) -> uuid.UUID:
        """ADDITIVE (domain-capture TASK.md §3 M9 — FROZEN @ v1): ONE INSERT of a new
        UserRow(tenant_id=<claimed tenant>, role=Role.MEMBER); auth_method keeps its
        column default ('password') — a domain-capture-joined user is a REAL password
        user, never a sentinel-hash SSO row.

        Deliberately INSERT-only (mirrors create_tenant_with_owner's own
        INSERT+catch-IntegrityError shape), NOT the get-or-provision shape
        _get_or_provision_sso_user uses — see ports.py's own docstring for why.

        plan-seat-cap TASK.md §3 (FROZEN @ v1, M4): assert_seat_available is the FIRST
        statement inside this existing `async with self._session.begin():` block, before
        the INSERT — SeatCapExceededError propagates out uncaught (the router translates
        it, M5); the transaction rolls back automatically on any exception exiting the
        `async with` block, so a reject leaves no partial write.
        """
        user = UserRow(
            id=uuid7(),
            tenant_id=tenant_id,
            email=email,
            password_hash=password_hash,
            role=Role.MEMBER,
        )
        try:
            async with self._session.begin():
                await assert_seat_available(self._session, tenant_id)
                self._session.add(user)
        except IntegrityError as exc:
            raise EmailAlreadyRegisteredError from exc
        return user.id

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
            deactivated_at=row.deactivated_at,
        )

    async def get_or_provision_oidc_user(
        self,
        *,
        email: str,
        tenant_id: uuid.UUID,
        password_hash: str,
    ) -> User:
        """Get existing user by email OR create with role=member if absent.

        Raises OidcTenantConflictError if user exists bound to a different tenant_id.
        The provisioned role is ALWAYS member — never from any claim.
        """
        return await self._get_or_provision_sso_user(
            email=email,
            tenant_id=tenant_id,
            password_hash=password_hash,
            auth_method="oidc",
            conflict_error_cls=OidcTenantConflictError,
        )

    async def get_or_provision_saml_user(
        self,
        *,
        email: str,
        tenant_id: uuid.UUID,
        password_hash: str,
    ) -> User:
        """Get existing user by email OR create with role=member if absent.

        ADDITIVE (saml-sso TASK.md §3 Part E — FROZEN @ v1): byte-identical
        shape to get_or_provision_oidc_user, delegating to the same shared
        helper parameterized by auth_method="saml" — no change to the
        existing OIDC method or its call sites.

        Raises SamlTenantConflictError if user exists bound to a different tenant_id.
        The provisioned role is ALWAYS member — never from any assertion attribute.
        """
        return await self._get_or_provision_sso_user(
            email=email,
            tenant_id=tenant_id,
            password_hash=password_hash,
            auth_method="saml",
            conflict_error_cls=SamlTenantConflictError,
        )

    async def _get_or_provision_sso_user(
        self,
        *,
        email: str,
        tenant_id: uuid.UUID,
        password_hash: str,
        auth_method: str,
        conflict_error_cls: type[Exception],
    ) -> User:
        """Shared JIT-provisioning helper for every federated-identity method
        (OIDC, SAML, ...) — get existing user by email OR create with
        role=member if absent; role is ALWAYS member for NEW rows, an
        EXISTING user's stored role is preserved (never downgraded).
        """
        row = (
            await self._session.execute(select(UserRow).where(UserRow.email == email))
        ).scalar_one_or_none()

        if row is not None:
            # User exists — verify tenant matches the domain mapping
            if row.tenant_id != tenant_id:
                raise conflict_error_cls(
                    f"User {email!r} exists in tenant {row.tenant_id} but "
                    f"domain mapping targets tenant {tenant_id}"
                )
            return User(
                id=row.id,
                tenant_id=row.tenant_id,
                email=row.email,
                password_hash=row.password_hash,
                role=Role(row.role),
                deactivated_at=row.deactivated_at,
            )

        # Provision new user — role is ALWAYS member (never from claims)
        # auth_method set at INSERT for all SSO-provisioned users (the
        # migration backfills existing rows with the sentinel hash).
        #
        # plan-seat-cap TASK.md §3 (FROZEN @ v1, M4): assert_seat_available is consulted
        # in THIS branch ONLY — never the existing-user branch above (M7, an existing
        # member's re-login is never gated). Reuses the SELECT-above's already-open
        # autobegin transaction (the same trap noted below for begin()); on
        # SeatCapExceededError, an explicit rollback() (this branch has no existing
        # try/except to piggyback on) before re-raising.
        try:
            await assert_seat_available(self._session, tenant_id)
        except SeatCapExceededError:
            await self._session.rollback()
            raise

        new_user = UserRow(
            id=uuid7(),
            tenant_id=tenant_id,
            email=email,
            password_hash=password_hash,
            role=Role.MEMBER,
            auth_method=auth_method,
        )
        # Use flush() + commit() rather than begin() because the SELECT above
        # already auto-began the session transaction; calling begin() again
        # raises InvalidRequestError.
        self._session.add(new_user)
        await self._session.flush()
        await self._session.commit()

        return User(
            id=new_user.id,
            tenant_id=new_user.tenant_id,
            email=new_user.email,
            password_hash=new_user.password_hash,
            role=Role.MEMBER,
        )
