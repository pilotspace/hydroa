import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.email.application.email_dispatch import send_email
from gateway.email.application.signup_confirm_email_template import render_signup_confirm_email
from gateway.email.application.signup_conflict_notice_email_template import (
    render_signup_conflict_notice_email,
)
from gateway.email.domain.ports import EmailSender
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.tenants.domain.authz import ensure_impersonation_session_live
from gateway.tenants.domain.entities import MIN_PASSWORD_LENGTH, Identity, Role
from gateway.tenants.domain.errors import (
    IndividualPlanMissingError,
    InvalidCredentialsError,
    PendingSignupExpiredError,
    PendingSignupNotFoundError,
    WeakPasswordError,
)
from gateway.tenants.domain.personal_signup_confirm import generate_confirm_token
from gateway.tenants.domain.ports import (
    IdentityRepository,
    ImpersonationSessionGuard,
    PasswordHasher,
    TokenService,
)

# scoped-self-serve-signup TASK.md §3 (FROZEN @ v1, SECURITY): the confirm token is
# 256-bit CSPRNG, hashed at rest with a BARE SHA-256 — the SAME entropy class + hasher
# already used for invite tokens (tenants/application/invite_use_cases.py's own
# module-level `_hasher = Sha256SecretHasher()`), NOT the HMAC-pepper scheme used for the
# low-entropy member-verify code (see Ground R-sec-4).
_token_hasher = Sha256SecretHasher()


class SignupUseCase:
    def __init__(self, repository: IdentityRepository, hasher: PasswordHasher) -> None:
        self._repository = repository
        self._hasher = hasher

    async def execute(
        self,
        *,
        tenant_name: str,
        email: str,
        password: str,
        account_type: str = "business",
    ) -> tuple[uuid.UUID, uuid.UUID]:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError
        # account-type-discriminator TASK.md §3 (FROZEN @ v1) + plan-tiers-and-base-fee
        # TASK.md §3 M3 (repoints this literal "individual" -> "free"): a personal account
        # lands on the seeded `free` plan; business stays unplanned (plan_id NULL) as
        # today. R3/SIGNUP_PLAN_UNPROVISIONED fail-closed shape UNCHANGED: if the free
        # plan is absent, raise loud (uncaught → 500) — never create a personal tenant
        # silently left unplanned.
        plan_id: uuid.UUID | None = None
        if account_type == "personal":
            plan_id = await self._repository.get_plan_id_by_name("free")
            if plan_id is None:
                raise IndividualPlanMissingError
        return await self._repository.create_tenant_with_owner(
            tenant_name=tenant_name,
            email=email.lower(),
            password_hash=self._hasher.hash(password),
            account_type=account_type,
            plan_id=plan_id,
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
        # NEW (scim-provisioning TASK.md §3, M7 — FROZEN @ v1): a deactivated user's
        # password login is rejected BYTE-IDENTICAL to a wrong-password attempt — same
        # error, same point in the flow (AFTER the constant-time hash verify above, so
        # no early-exit timing signal distinguishes "deactivated" from "wrong password").
        # Anti-enumeration (CONVENTIONS.md fold): never a distinct error/status/detail.
        if user.deactivated_at is not None:
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
    """impersonation-live-session-guard TASK.md §3 Part D.5 — call site 5/5.

    guard_factory is a domain-Protocol-typed (ImpersonationSessionGuard) zero-arg
    callable — NEVER a direct infrastructure/ import here (CONVENTIONS.md's
    application-layer rule). The caller (tenants/api/deps.py::get_identity_use_case,
    or one of the 3 direct-construction call sites) resolves a session and closes
    over it + the concrete DbImpersonationSessionGuard when building the factory.
    """

    def __init__(
        self, tokens: TokenService, guard_factory: Callable[[], ImpersonationSessionGuard]
    ) -> None:
        self._tokens = tokens
        self._guard_factory = guard_factory

    async def execute(self, token: str) -> Identity:
        identity = self._tokens.decode(token)
        await ensure_impersonation_session_live(identity, self._guard_factory())
        return identity


class IssuePendingSignupUseCase:
    """scoped-self-serve-signup TASK.md §3 (FROZEN @ v1, SECURITY) — M6/M7/M8/M9.

    NEVER raises for an email-exists conflict (that branch is silent-success, M9) — only
    WeakPasswordError / IndividualPlanMissingError propagate, translated by the router
    exactly like today's SignupUseCase (AUTH_PASSWORD_WEAK / SIGNUP_PLAN_UNPROVISIONED).

    Order (M4/M5/M6, BEFORE the email-existence branch): password length -> plan
    resolution -> UNCONDITIONAL argon2 hash (the M6 timing mask) -> get_user_by_email ->
    branch M8 (fresh) / M9 (conflict). BOTH branches schedule their email via
    asyncio.ensure_future — NEVER awaited inline — mirroring LoginUseCase.execute's own
    fire-and-forget superadmin-login audit write above: the ⚠ freeze flag named the
    closest sibling (IssueMemberVerifyCodeUseCase) awaiting `send_email` inline as the
    risk this task must NOT repeat, since a measurable send-latency delta between the
    confirm-email and conflict-notice templates would reopen the enumeration oracle M7
    exists to close.
    """

    def __init__(
        self,
        repository: IdentityRepository,
        hasher: PasswordHasher,
        email_sender: EmailSender,
        *,
        confirm_ttl_seconds: int,
        origin: str,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._email_sender = email_sender
        self._confirm_ttl_seconds = confirm_ttl_seconds
        self._origin = origin

    async def execute(self, *, tenant_name: str, email: str, password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError
        # M5: plan-availability checked uniformly, before the email-existence branch — a
        # server-misconfiguration signal, never a function of the target email.
        plan_id = await self._repository.get_plan_id_by_name("free")
        if plan_id is None:
            raise IndividualPlanMissingError

        normalized_email = email.lower()
        # M6 timing mask: hash UNCONDITIONALLY, before the email-existence branch — the
        # dominant cost is paid identically whether this hash is stored (M8) or discarded
        # (M9), mirroring Argon2PasswordHasher.verify's own dummy-hash equalization.
        password_hash = self._hasher.hash(password)

        existing = await self._repository.get_user_by_email(normalized_email)
        if existing is not None:
            # M9: conflict handled ENTIRELY out-of-band — no row written, no exception,
            # no observable difference in the caller's HTTP response (M7).
            message = render_signup_conflict_notice_email(to=normalized_email, origin=self._origin)
            asyncio.ensure_future(send_email(self._email_sender, message))  # noqa: RUF006
            return

        # M8: create-or-reissue the pending row; mint + hash a fresh confirm token.
        token = generate_confirm_token()
        token_hash = _token_hasher.hash(token)
        expires_at = datetime.now(UTC) + timedelta(seconds=self._confirm_ttl_seconds)
        await self._repository.issue_or_reissue_pending_signup(
            email=normalized_email,
            tenant_name=tenant_name,
            password_hash=password_hash,
            confirm_token_hash=token_hash,
            expires_at=expires_at,
        )
        message = render_signup_confirm_email(
            to=normalized_email, tenant_name=tenant_name, token=token, origin=self._origin
        )
        asyncio.ensure_future(send_email(self._email_sender, message))  # noqa: RUF006


class ConfirmPendingSignupUseCase:
    """scoped-self-serve-signup TASK.md §3 (FROZEN @ v1, SECURITY) — M10/M11/M12.

    PUBLIC — the token is the ONLY credential (delivered solely by email). Distinguishing
    invalid/expired/taken here is safe (R-sec-6): reachable in a meaningful way ONLY by
    whoever already possesses the emailed token, never by an anonymous prober of the
    initial signup endpoint.
    """

    def __init__(self, repository: IdentityRepository) -> None:
        self._repository = repository

    async def execute(self, *, token: str) -> tuple[uuid.UUID, uuid.UUID]:
        token_hash = _token_hasher.hash(token)
        # M10: atomic single-statement consume — single-use by construction.
        pending = await self._repository.consume_pending_signup(confirm_token_hash=token_hash)
        if pending is None:
            # M11: distinguish "expired" (cleaned up here) from "never existed / already
            # consumed" — a SEPARATE read/delete, only reached on a consume miss.
            if await self._repository.pop_expired_pending_signup(confirm_token_hash=token_hash):
                raise PendingSignupExpiredError
            raise PendingSignupNotFoundError

        # Re-resolve the free plan id FRESH at confirm-time (it could have vanished since
        # issuance) — never reuse a plan_id captured at M8.
        plan_id = await self._repository.get_plan_id_by_name("free")
        if plan_id is None:
            raise IndividualPlanMissingError

        # M12/R-sec-5: create_tenant_with_owner reused UNCHANGED; a race against an
        # UNRELATED signup path for the SAME email between issuance and confirm surfaces
        # as EmailAlreadyRegisteredError, left for the router to translate (R-sec-6 — safe
        # to reveal here since the caller is token-possession-authenticated).
        return await self._repository.create_tenant_with_owner(
            tenant_name=pending.tenant_name,
            email=pending.email,
            password_hash=pending.password_hash,
            account_type="personal",
            plan_id=plan_id,
        )
