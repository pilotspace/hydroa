from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import AUTH_TOKEN_MISSING
from gateway.tenants.application.use_cases import (
    ConfirmPasswordResetUseCase,
    ConfirmPendingSignupUseCase,
    GetIdentityUseCase,
    IssuePendingSignupUseCase,
    LoginUseCase,
    LogoutUseCase,
    RequestPasswordResetUseCase,
    SignupUseCase,
)
from gateway.tenants.domain.ports import PasswordHasher, TokenService
from gateway.tenants.infrastructure.impersonation_session_guard import DbImpersonationSessionGuard
from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository
from gateway.tenants.infrastructure.session_revocation import DbSessionRevocationGuard


def get_hasher(request: Request) -> PasswordHasher:
    hasher: PasswordHasher = request.app.state.password_hasher
    return hasher


def get_token_service(request: Request) -> TokenService:
    tokens: TokenService = request.app.state.token_service
    return tokens


def get_signup_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
    hasher: Annotated[PasswordHasher, Depends(get_hasher)],
) -> SignupUseCase:
    return SignupUseCase(SqlAlchemyIdentityRepository(session), hasher)


def get_login_use_case(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    hasher: Annotated[PasswordHasher, Depends(get_hasher)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> LoginUseCase:
    return LoginUseCase(
        SqlAlchemyIdentityRepository(session),
        hasher,
        tokens,
        # NEW (superadmin-audit-foundation TASK.md §3 Part B — FROZEN @ v1): audit-only,
        # the same live app.state.sessionmaker global every other record_audit call
        # site (Part A's resolve_platform_credential, Part C's OidcLoginUseCase) reads.
        session_factory=request.app.state.sessionmaker,
    )


def get_identity_use_case(
    request: Request,
    tokens: Annotated[TokenService, Depends(get_token_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GetIdentityUseCase:
    return GetIdentityUseCase(
        tokens,
        guard_factory=lambda: DbImpersonationSessionGuard(
            session=session,
            timeout_seconds=request.app.state.settings.impersonation_live_check_timeout_seconds,
        ),
        # auth-hardening-login-sessions TASK.md §3 M5 — REQUIRED keyword: this site
        # cannot silently skip the revocation check.
        revocation_guard_factory=lambda: DbSessionRevocationGuard(
            session=session,
            timeout_seconds=request.app.state.settings.session_revocation_check_timeout_seconds,
        ),
    )


def get_issue_pending_signup_use_case(
    request: Request, session: AsyncSession
) -> IssuePendingSignupUseCase:
    """scoped-self-serve-signup TASK.md §3 (FROZEN @ v1, SECURITY). Reuses the SAME
    request.app.state.password_hasher instance every other signup/login use-case reads —
    the M6 timing-mask call-count assertion (test_probe_registered_vs_unknown_
    indistinguishable) depends on this being the identical, monkeypatchable instance, not
    a fresh Argon2PasswordHasher(). Called directly (request, session) — mirrors
    domain_capture/api/deps.py::get_join_tenant_use_case's own plain-function shape, the
    style tenants/api/router.py::signup already uses for its sibling call sites."""
    settings = request.app.state.settings
    return IssuePendingSignupUseCase(
        SqlAlchemyIdentityRepository(session),
        request.app.state.password_hasher,
        request.app.state.email_sender,
        confirm_ttl_seconds=settings.personal_signup_confirm_ttl_seconds,
        origin=settings.dashboard_public_origin,
    )


def get_confirm_pending_signup_use_case(
    request: Request, session: AsyncSession
) -> ConfirmPendingSignupUseCase:
    return ConfirmPendingSignupUseCase(SqlAlchemyIdentityRepository(session))


def get_request_password_reset_use_case(
    request: Request, session: AsyncSession
) -> RequestPasswordResetUseCase:
    """auth-hardening-login-sessions TASK.md §3 M2/M3 (FROZEN @ v1, SECURITY). Plain
    (request, session) shape — mirrors get_issue_pending_signup_use_case above."""
    settings = request.app.state.settings
    return RequestPasswordResetUseCase(
        SqlAlchemyIdentityRepository(session),
        request.app.state.email_sender,
        reset_ttl_seconds=settings.password_reset_ttl_seconds,
        origin=settings.dashboard_public_origin,
    )


def get_confirm_password_reset_use_case(
    request: Request, session: AsyncSession
) -> ConfirmPasswordResetUseCase:
    """auth-hardening-login-sessions TASK.md §3 M3/M4 (FROZEN @ v1, SECURITY). Reuses
    the SAME request.app.state.password_hasher instance every sibling use-case reads."""
    return ConfirmPasswordResetUseCase(
        SqlAlchemyIdentityRepository(session),
        request.app.state.password_hasher,
        session_factory=request.app.state.sessionmaker,
    )


def get_logout_use_case(request: Request, session: AsyncSession) -> LogoutUseCase:
    """auth-hardening-login-sessions TASK.md §3 M5 (FROZEN @ v1, SECURITY)."""
    settings = request.app.state.settings
    return LogoutUseCase(
        SqlAlchemyIdentityRepository(session),
        jwt_ttl_seconds=settings.jwt_ttl_seconds,
        session_factory=request.app.state.sessionmaker,
    )


def get_bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AUTH_TOKEN_MISSING.exc()
    return token
