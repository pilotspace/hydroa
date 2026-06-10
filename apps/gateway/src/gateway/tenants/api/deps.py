from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.errors import ProblemError
from gateway.tenants.application.use_cases import (
    GetIdentityUseCase,
    LoginUseCase,
    SignupUseCase,
)
from gateway.tenants.domain.ports import PasswordHasher, TokenService
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
    session: Annotated[AsyncSession, Depends(get_session)],
    hasher: Annotated[PasswordHasher, Depends(get_hasher)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> LoginUseCase:
    return LoginUseCase(SqlAlchemyIdentityRepository(session), hasher, tokens)


def get_identity_use_case(
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> GetIdentityUseCase:
    return GetIdentityUseCase(tokens)


def get_bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ProblemError(401, "ERR_AUTH_INVALID_TOKEN", "Missing or malformed bearer token")
    return token
