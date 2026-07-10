"""Dependency providers for the /scim/v2/* API surface.

Mirrors keys/api/deps.py's get_bearer_token/get_identity shape exactly, but resolves a
SCIM token (not a session JWT or an api_key) and raises the SCIM 401 error envelope
(not ProblemError) on any failure — see gateway.scim.api.errors.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.scim.api.errors import scim_invalid_token
from gateway.scim.application.token_use_cases import AuthenticateScimUseCase, ScimIdentity
from gateway.scim.domain.errors import ScimInvalidTokenError
from gateway.scim.infrastructure.repository import (
    SqlAlchemyScimTokenRepository,
    SqlAlchemyScimUserRepository,
)

# Singleton hasher — stateless, safe to share (mirrors keys/api/deps.py).
_hasher = Sha256SecretHasher()


def get_scim_bearer_token(request: Request) -> str:
    """Extract raw Bearer token from Authorization header — 401 SCIM envelope if absent
    or malformed (mirrors keys/api/deps.py::get_bearer_token, different error shape)."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise scim_invalid_token()
    return token


def get_scim_token_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyScimTokenRepository:
    return SqlAlchemyScimTokenRepository(session)


def get_scim_user_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyScimUserRepository:
    return SqlAlchemyScimUserRepository(session)


async def get_scim_identity(
    token: Annotated[str, Depends(get_scim_bearer_token)],
    repo: Annotated[SqlAlchemyScimTokenRepository, Depends(get_scim_token_repo)],
) -> ScimIdentity:
    """Resolve the SCIM bearer token to a (tenant_id, scim_token_id) pair.

    Raises the SCIM 401 envelope on ANY failure mode — missing, malformed, unknown, or
    revoked token are ALL byte-identical (no enumeration oracle, M2/Reject).
    """
    use_case = AuthenticateScimUseCase(repo, _hasher)
    try:
        return await use_case.execute(token)
    except ScimInvalidTokenError:
        raise scim_invalid_token() from None
