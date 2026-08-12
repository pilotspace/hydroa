import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.domain.errors import OidcTenantConflictError
from gateway.auth.domain.saml_errors import SamlTenantConflictError
from gateway.core.ids import uuid7
from gateway.tenants.application.entitlements import assert_seat_available
from gateway.tenants.domain.entities import PendingPersonalSignup, Role, User
from gateway.tenants.domain.errors import EmailAlreadyRegisteredError, SeatCapExceededError
from gateway.tenants.infrastructure.orm import (
    PendingPersonalSignupRow,
    PlanRow,
    SeatMembershipEventRow,
    TenantRow,
    UserRow,
)


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
    paged = stmt.order_by(TenantRow.created_at, TenantRow.id).limit(limit).offset(offset)
    rows = (await session.execute(paged)).scalars().all()
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

    async def get_plan_id_by_name(self, name: str) -> uuid.UUID | None:
        # account-type-discriminator TASK.md §3 (FROZEN @ v1): plans are seed-only; `name`
        # is UNIQUE, so scalar_one_or_none never sees a multi-row result.
        return (
            await self._session.execute(select(PlanRow.id).where(PlanRow.name == name))
        ).scalar_one_or_none()

    async def create_tenant_with_owner(
        self,
        *,
        tenant_name: str,
        email: str,
        password_hash: str,
        account_type: str = "business",
        plan_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        tenant = TenantRow(id=uuid7(), name=tenant_name, account_type=account_type, plan_id=plan_id)
        user = UserRow(
            id=uuid7(),
            tenant_id=tenant.id,
            email=email,
            password_hash=password_hash,
            role=Role.OWNER,
        )
        # account-type-discriminator TASK.md §3 (FROZEN @ v1): the caller resolves plan_id
        # via get_plan_id_by_name (a SELECT) BEFORE calling us — SQLAlchemy's AsyncSession
        # autobegin means that read already opened an implicit transaction, so our explicit
        # begin() below would raise "A transaction is already begun on this Session." Close
        # the read-only lookup's transaction first (a no-op when none is open) — mirrors
        # router.signup's own documented rollback for this exact autobegin trap.
        await self._session.rollback()
        try:
            # Safety rule (TASK §5): both inserts in ONE transaction — or neither.
            async with self._session.begin():
                self._session.add(tenant)
                self._session.add(user)
                # billing-owner-signup-population TASK.md §3 (FROZEN @ v1): stamp the founding
                # OWNER as the tenant's billing-owner-of-record so a fresh tenant is never left
                # with billing_owner_user_id NULL (the parent billing-owner-of-record task
                # backfilled only EXISTING tenants). The tenants<->users FK is circular
                # (users.tenant_id -> tenants.id AND tenants.billing_owner_user_id -> users.id),
                # so the pointer CANNOT be set at INSERT time — the referenced user row does not
                # exist yet, and use_alter is DDL-only (runtime FK checks still fire). Flush both
                # INSERTs first (tenants inserts before users; the tenants->users FK is excluded
                # from the sort by use_alter), THEN assign — the dirty column flushes as a
                # cycle-breaking UPDATE on commit, after the user row exists. Mirrors
                # join_verified_tenant_domain's own flush-before-dependent-write idiom.
                await self._session.flush()
                tenant.billing_owner_user_id = user.id
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
                # Flush the parent `users` INSERT before adding the FK-dependent event row:
                # SQLAlchemy's unit-of-work only orders INSERTs across DIFFERENT mapped
                # classes via a declared relationship() dependency processor — with none
                # configured between UserRow and SeatMembershipEventRow, an unflushed pairing
                # can emit the event INSERT before the user INSERT and trip a FK violation
                # (mirrors SqlAlchemyScimUserRepository.create_user's own explicit
                # add()+flush()-before-second-add() shape for the identical hazard).
                await self._session.flush()
                # seat-billing TASK.md §3 (FROZEN @ v2/CR-1, M3(b3)): append ONE 'joined'
                # event in the SAME transaction as the new users row.
                self._session.add(
                    SeatMembershipEventRow(
                        id=uuid7(),
                        tenant_id=tenant_id,
                        user_id=user.id,
                        event_type="joined",
                        occurred_at=datetime.now(UTC),
                    )
                )
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
    ) -> tuple[User, bool]:
        """Get existing user by email OR create with role=member if absent.

        Returns (user, newly_provisioned) — newly_provisioned is True IFF this
        call INSERTed the user (domain-auto-assign-login TASK.md §3 M1).

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
    ) -> tuple[User, bool]:
        """Get existing user by email OR create with role=member if absent.

        Returns (user, newly_provisioned) — newly_provisioned is True IFF this
        call INSERTed the user (domain-auto-assign-login TASK.md §3 M1).

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
    ) -> tuple[User, bool]:
        """Shared JIT-provisioning helper for every federated-identity method
        (OIDC, SAML, ...) — get existing user by email OR create with
        role=member if absent; role is ALWAYS member for NEW rows, an
        EXISTING user's stored role is preserved (never downgraded).

        Returns (user, newly_provisioned) — True IFF this call INSERTed the
        user (the new-user branch), False when it matched an existing row
        (domain-auto-assign-login TASK.md §3 M1).
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
            return (
                User(
                    id=row.id,
                    tenant_id=row.tenant_id,
                    email=row.email,
                    password_hash=row.password_hash,
                    role=Role(row.role),
                    deactivated_at=row.deactivated_at,
                ),
                False,
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
        # Flush the parent `users` INSERT before adding the FK-dependent event row: see
        # join_verified_tenant_domain's identical comment — without a relationship()
        # between UserRow and SeatMembershipEventRow, the unit-of-work has no dependency
        # processor ordering the two mappers' INSERTs, so an unflushed pairing can emit the
        # event INSERT first and trip a FK violation.
        await self._session.flush()
        # seat-billing TASK.md §3 (FROZEN @ v2/CR-1, M3(b2)): NEW-USER BRANCH ONLY —
        # append ONE 'joined' event in the SAME transaction; an EXISTING member's SSO
        # re-login (the branch above, `if row is not None`) never reaches this line.
        self._session.add(
            SeatMembershipEventRow(
                id=uuid7(),
                tenant_id=tenant_id,
                user_id=new_user.id,
                event_type="joined",
                occurred_at=datetime.now(UTC),
            )
        )
        await self._session.flush()
        await self._session.commit()

        return (
            User(
                id=new_user.id,
                tenant_id=new_user.tenant_id,
                email=new_user.email,
                password_hash=new_user.password_hash,
                role=Role.MEMBER,
            ),
            True,
        )

    # ── scoped-self-serve-signup TASK.md §3 (FROZEN @ v1, SECURITY) — additive ─────────

    async def issue_or_reissue_pending_signup(
        self,
        *,
        email: str,
        tenant_name: str,
        password_hash: str,
        confirm_token_hash: str,
        expires_at: datetime,
    ) -> None:
        """UPSERT-by-email (M8, create-or-reissue idiom — mirrors
        CreateDomainClaimUseCase/SqlAlchemyDomainClaimRepository.create_or_reissue's own
        pg_insert(...).on_conflict_do_update shape exactly): a repeat not-yet-confirmed
        submission for the SAME email overwrites token hash + expiry, so the previous
        (now-superseded) token stops matching any row."""
        stmt = (
            pg_insert(PendingPersonalSignupRow)
            .values(
                id=uuid7(),
                email=email,
                tenant_name=tenant_name,
                password_hash=password_hash,
                confirm_token_hash=confirm_token_hash,
                expires_at=expires_at,
            )
            .on_conflict_do_update(
                index_elements=[PendingPersonalSignupRow.email],
                set_={
                    "tenant_name": tenant_name,
                    "password_hash": password_hash,
                    "confirm_token_hash": confirm_token_hash,
                    "expires_at": expires_at,
                },
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def consume_pending_signup(
        self, *, confirm_token_hash: str
    ) -> PendingPersonalSignup | None:
        """M10: one atomic DELETE ... WHERE confirm_token_hash = :hash AND
        expires_at > now() RETURNING * — single-use by construction. Under Postgres
        READ COMMITTED, a concurrent second DELETE targeting the SAME row blocks on the
        row lock until the first commits, then re-evaluates the WHERE clause and finds no
        matching row — so a concurrent double-confirm of the same token can never both
        return a row (the M10 atomicity scenario)."""
        stmt = (
            delete(PendingPersonalSignupRow)
            .where(
                PendingPersonalSignupRow.confirm_token_hash == confirm_token_hash,
                PendingPersonalSignupRow.expires_at > func.now(),
            )
            .returning(PendingPersonalSignupRow)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return PendingPersonalSignup(
            email=row.email, tenant_name=row.tenant_name, password_hash=row.password_hash
        )

    async def pop_expired_pending_signup(self, *, confirm_token_hash: str) -> bool:
        """M11: called ONLY when consume_pending_signup misses — deletes a matching row
        regardless of expiry so the caller can distinguish "expired" (a row existed here)
        from "never existed / already consumed" (nothing to delete), with cleanup as a
        side effect. True iff a row was deleted."""
        stmt = (
            delete(PendingPersonalSignupRow)
            .where(PendingPersonalSignupRow.confirm_token_hash == confirm_token_hash)
            .returning(PendingPersonalSignupRow.id)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.scalar_one_or_none() is not None
