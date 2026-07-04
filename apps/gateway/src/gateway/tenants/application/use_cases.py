import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.tenants.domain.entities import MIN_PASSWORD_LENGTH, Identity, Role
from gateway.tenants.domain.errors import InvalidCredentialsError, WeakPasswordError
from gateway.tenants.domain.ports import IdentityRepository, PasswordHasher, TokenService


class SignupUseCase:
    def __init__(self, repository: IdentityRepository, hasher: PasswordHasher) -> None:
        self._repository = repository
        self._hasher = hasher

    async def execute(
        self, *, tenant_name: str, email: str, password: str
    ) -> tuple[uuid.UUID, uuid.UUID]:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError
        return await self._repository.create_tenant_with_owner(
            tenant_name=tenant_name,
            email=email.lower(),
            password_hash=self._hasher.hash(password),
        )


class LoginUseCase:
    def __init__(
        self,
        repository: IdentityRepository,
        hasher: PasswordHasher,
        tokens: TokenService,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._tokens = tokens
        # NEW (superadmin-audit-foundation TASK.md §3 Part B — FROZEN @ v1): used ONLY
        # to schedule the post-issuance audit write below — never for the credential
        # check or JWT issuance, which are byte-identical to before this task.
        # Keyword-only + required (no default) so no future caller can silently skip
        # the audit wiring — mirrors Part A's resolve_platform_credential and Part C's
        # OidcLoginUseCase precedent. The `*` is a style choice for consistency across
        # this task's three call sites: __init__ had zero defaulted params before this
        # change, so no ordering constraint actually required it.
        self._session_factory = session_factory

    async def execute(self, *, email: str, password: str) -> tuple[str, int]:
        user = await self._repository.get_user_by_email(email.lower())
        # verify() runs against a dummy hash when user is None, so both
        # failure paths cost the same time and return the same error.
        if not self._hasher.verify(user.password_hash if user else None, password) or user is None:
            raise InvalidCredentialsError
        token, expires_in = self._tokens.issue(
            user_id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email
        )

        # NEW (superadmin-audit-foundation TASK.md §3 Part B — FROZEN @ v1): fires iff
        # the just-issued JWT's role is superadmin, evaluated post-issuance — never on a
        # rejected login attempt (InvalidCredentialsError above raises before this line
        # is ever reached, so no role lookup is added ahead of the existing constant-
        # time password check). Identical firing rule to the OIDC/SSO side (Part C),
        # same event shape except auth_method="password". Fire-and-forget + fail-open:
        # reuses record_audit's already-frozen contract verbatim (own isolated session,
        # swallow-all-exceptions-log-a-warning) — an audit-subsystem failure must never
        # block a superadmin's login response.
        if user.role == Role.SUPERADMIN:
            asyncio.ensure_future(  # noqa: RUF006
                record_audit(
                    self._session_factory,
                    AuditEvent(
                        id=uuid.uuid4(),
                        tenant_id=user.tenant_id,
                        actor_user_id=user.id,
                        actor_email=user.email,
                        action="auth.superadmin_login",
                        target_type="user",
                        target_id=str(user.id),
                        result="success",
                        metadata={"role": "superadmin", "auth_method": "password"},
                        created_at=datetime.now(UTC),
                    ),
                )
            )

        return token, expires_in


class GetIdentityUseCase:
    def __init__(self, tokens: TokenService) -> None:
        self._tokens = tokens

    def execute(self, token: str) -> Identity:
        return self._tokens.decode(token)
