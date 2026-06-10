"""Dependency providers for keys API endpoints."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.errors import ProblemError
from gateway.keys.application.use_cases import (
    AuthzUseCase,
    CreateKeyUseCase,
    ListKeysUseCase,
    RevokeKeyUseCase,
)
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.domain.errors import InvalidTokenError
from gateway.tenants.domain.ports import TokenService

# Singleton hasher — stateless, safe to share
_hasher = Sha256SecretHasher()


def get_token_service(request: Request) -> TokenService:
    tokens: TokenService = request.app.state.token_service
    return tokens


def get_bearer_token(request: Request) -> str:
    """Extract raw Bearer token from Authorization header."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ProblemError(401, "ERR_AUTH_INVALID_TOKEN", "Missing or malformed bearer token")
    return token


def get_identity(
    token: Annotated[str, Depends(get_bearer_token)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> Identity:
    """Decode JWT and return the caller's Identity; raise 401 on any failure."""
    try:
        return tokens.decode(token)
    except InvalidTokenError:
        raise ProblemError(401, "ERR_AUTH_INVALID_TOKEN", "Invalid or expired token") from None


def require_owner_or_admin(
    identity: Annotated[Identity, Depends(get_identity)],
) -> Identity:
    """Raise 403 if the caller is a member."""
    if identity.role == Role.MEMBER:
        raise ProblemError(403, "ERR_AUTH_FORBIDDEN", "Insufficient role for this operation")
    return identity


def get_create_key_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CreateKeyUseCase:
    return CreateKeyUseCase(SqlAlchemyApiKeyRepository(session), _hasher)


def get_list_keys_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ListKeysUseCase:
    return ListKeysUseCase(SqlAlchemyApiKeyRepository(session))


def get_revoke_key_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RevokeKeyUseCase:
    return RevokeKeyUseCase(SqlAlchemyApiKeyRepository(session))


def get_authz_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthzUseCase:
    return AuthzUseCase(SqlAlchemyApiKeyRepository(session), _hasher)
