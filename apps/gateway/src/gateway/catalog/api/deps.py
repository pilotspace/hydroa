"""Catalog API dependency wiring — mirrors gateway/tenants/api/deps.py pattern."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.application.use_cases import ListModelsForTenantUseCase, SyncCatalogUseCase
from gateway.catalog.domain.ports import CatalogSource
from gateway.catalog.infrastructure.repository import SqlAlchemyCatalogRepository
from gateway.core.db import get_session
from gateway.core.error_catalog import AUTH_TOKEN_INVALID, AUTH_TOKEN_MISSING
from gateway.tenants.domain.authz import ROLE_PERMISSIONS, Permission
from gateway.tenants.domain.entities import Identity
from gateway.tenants.domain.errors import InvalidTokenError
from gateway.tenants.domain.ports import TokenService


def get_catalog_source(request: Request) -> CatalogSource:
    """Pull the CatalogSource from app.state (injected at composition root or test override)."""
    source: CatalogSource = request.app.state.catalog_source
    return source


def get_token_service(request: Request) -> TokenService:
    """Reuse the token service already wired on app.state by main.create_app."""
    tokens: TokenService = request.app.state.token_service
    return tokens


def get_sync_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
    source: Annotated[CatalogSource, Depends(get_catalog_source)],
) -> SyncCatalogUseCase:
    return SyncCatalogUseCase(source, SqlAlchemyCatalogRepository(session))


def get_list_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ListModelsForTenantUseCase:
    return ListModelsForTenantUseCase(SqlAlchemyCatalogRepository(session))


def get_current_identity(
    request: Request,
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> Identity:
    """Validate Bearer JWT and return the decoded Identity.

    Raises ProblemError(401) for any token failure (missing, malformed,
    expired, wrong signature) — consistent with the tenants pattern.
    """
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AUTH_TOKEN_MISSING.exc()
    try:
        return tokens.decode(token)
    except InvalidTokenError:
        raise AUTH_TOKEN_INVALID.exc() from None


def require_owner_or_admin(
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> Identity:
    """Raise 403 ERR_AUTH_FORBIDDEN if the caller lacks KEYS_MANAGE.

    Re-expressed over ROLE_PERMISSIONS allowlist (rbac-roles refactor).
    Back-compat: OWNER/ADMIN pass, MEMBER 403 — byte-identical to pre-task.
    """
    if Permission.KEYS_MANAGE not in ROLE_PERMISSIONS.get(identity.role, frozenset()):
        from gateway.core.error_catalog import AUTH_FORBIDDEN

        raise AUTH_FORBIDDEN.exc()
    return identity


def require_catalog_sync(
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> Identity:
    """Require CATALOG_SYNC permission (owner/admin/operator)."""
    if Permission.CATALOG_SYNC not in ROLE_PERMISSIONS.get(identity.role, frozenset()):
        from gateway.core.error_catalog import AUTH_FORBIDDEN

        raise AUTH_FORBIDDEN.exc()
    return identity


def get_admin_models_session(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AsyncSession:
    """Pass through the DB session for admin model endpoints."""
    return session
