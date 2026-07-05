from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import AUTH_TOKEN_MISSING
from gateway.tenants.application.use_cases import (
    GetIdentityUseCase,
    LoginUseCase,
    SignupUseCase,
)
from gateway.tenants.domain.ports import PasswordHasher, TokenService
from gateway.tenants.infrastructure.impersonation_session_guard import DbImpersonationSessionGuard
from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository


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
    )


def get_bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AUTH_TOKEN_MISSING.exc()
    return token
