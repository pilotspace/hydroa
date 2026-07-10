"""FastAPI router for SCIM token management (scim-provisioning TASK.md §3 Part A).

Endpoints:
  POST   /admin/scim/tokens            — create (MEMBERS_MANAGE)
  GET    /admin/scim/tokens            — list (MEMBERS_MANAGE)
  POST   /admin/scim/tokens/{id}/rotate — atomic rotate (MEMBERS_MANAGE)
  DELETE /admin/scim/tokens/{id}       — revoke (MEMBERS_MANAGE)

RFC 9457 problem+json throughout (this is an /admin/* surface — the SCIM error envelope
is reserved for /scim/v2/*, see scim_router.py). Session-JWT auth via
require_permission(Permission.MEMBERS_MANAGE) — reused unmodified, no new Permission.

Security (server-side, authoritative):
  - MEMBERS_MANAGE gating: only owner/admin may manage a tenant's SCIM tokens.
  - Tenant isolation: rotate/revoke never touch another tenant's token (404, no oracle
    between "unknown id" and "cross-tenant id" — mirrors INVITE_NOT_FOUND's precedent).
  - The plaintext token is returned ONLY in the create/rotate response body — never
    again, never in GET/list (only the SHA-256 hash is persisted).
  - Audit: every mutation (token create/rotate/revoke) fires fail-open, actor_user_id
    set (a human OWNER/ADMIN authenticated this call via session JWT — NOT
    actor_scim_token_id, which is reserved for the machine-authenticated /scim/v2/*
    surface where no user identity exists at all).
  - Deactivation-escalation fix (verify-phase HARD-STOP finding): create/rotate ADDITIONALLY
    depend on require_active_user — a deactivated caller's still-valid session JWT can no
    longer mint (or rotate into) a NEW SCIM token, even though the JWT's role claim is
    unaffected by deactivation. list/revoke are unaffected (they don't mint a NEW
    credential; the accepted stale-JWT residual — TASK.md §1 M7 — still covers them).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.error_catalog import SCIM_TOKEN_NOT_FOUND
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.scim.application.token_use_cases import (
    CreateScimTokenUseCase,
    ListScimTokensUseCase,
    RevokeScimTokenUseCase,
    RotateScimTokenUseCase,
)
from gateway.scim.domain.errors import ScimTokenNotFoundError
from gateway.scim.infrastructure.repository import SqlAlchemyScimTokenRepository
from gateway.tenants.domain.authz import Permission, require_active_user, require_permission
from gateway.tenants.domain.entities import Identity

scim_token_router = APIRouter(prefix="/admin/scim/tokens", tags=["scim-tokens"])
_hasher = Sha256SecretHasher()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ScimTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ScimTokenCreateResponse(BaseModel):
    id: uuid.UUID
    name: str
    token: str  # PLAINTEXT — shown exactly once, never retrievable again
    created_at: datetime


class ScimTokenInfo(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    revoked_at: datetime | None
    # Deliberately NO token / token_hash field.


class ScimTokenListResponse(BaseModel):
    tokens: list[ScimTokenInfo]


# ---------------------------------------------------------------------------
# Dependency factory
# ---------------------------------------------------------------------------


def _get_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyScimTokenRepository:
    return SqlAlchemyScimTokenRepository(session)


def _audit(
    request: Request,
    *,
    identity: Identity,
    action: str,
    target_id: uuid.UUID,
    metadata: dict[str, object],
) -> None:
    """Fire-and-forget audit write — actor_user_id set (a human authenticated this call
    via session JWT; this is an /admin/* surface, NOT the machine-authenticated
    /scim/v2/* surface, so actor_scim_token_id is never set here)."""
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action=action,
                target_type="scim_token",
                target_id=str(target_id),
                result="success",
                metadata=metadata,
                created_at=datetime.now(UTC),
            ),
        )
    )


# ---------------------------------------------------------------------------
# POST /admin/scim/tokens
# ---------------------------------------------------------------------------


@scim_token_router.post("", response_model=ScimTokenCreateResponse, status_code=201)
async def create_scim_token(
    request: Request,
    body: ScimTokenCreateRequest,
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    _active: Annotated[Identity, Depends(require_active_user)],
    repo: Annotated[SqlAlchemyScimTokenRepository, Depends(_get_repo)],
) -> ScimTokenCreateResponse:
    use_case = CreateScimTokenUseCase(repo, _hasher)
    result = await use_case.execute(
        tenant_id=identity.tenant_id, name=body.name, created_by_user_id=identity.user_id
    )

    _audit(
        request,
        identity=identity,
        action="scim.token_create",
        target_id=result.id,
        metadata={"name": result.name},
    )
    return ScimTokenCreateResponse(
        id=result.id, name=result.name, token=result.token, created_at=result.created_at
    )


# ---------------------------------------------------------------------------
# GET /admin/scim/tokens
# ---------------------------------------------------------------------------


@scim_token_router.get("", response_model=ScimTokenListResponse)
async def list_scim_tokens(
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    repo: Annotated[SqlAlchemyScimTokenRepository, Depends(_get_repo)],
) -> ScimTokenListResponse:
    use_case = ListScimTokensUseCase(repo)
    tokens = await use_case.execute(tenant_id=identity.tenant_id)
    return ScimTokenListResponse(
        tokens=[
            ScimTokenInfo(id=t.id, name=t.name, created_at=t.created_at, revoked_at=t.revoked_at)
            for t in tokens
        ]
    )


# ---------------------------------------------------------------------------
# POST /admin/scim/tokens/{id}/rotate
# ---------------------------------------------------------------------------


@scim_token_router.post("/{token_id}/rotate", response_model=ScimTokenCreateResponse)
async def rotate_scim_token(
    request: Request,
    token_id: uuid.UUID,
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    _active: Annotated[Identity, Depends(require_active_user)],
    repo: Annotated[SqlAlchemyScimTokenRepository, Depends(_get_repo)],
) -> ScimTokenCreateResponse:
    use_case = RotateScimTokenUseCase(repo, _hasher)
    try:
        result = await use_case.execute(old_token_id=token_id, tenant_id=identity.tenant_id)
    except ScimTokenNotFoundError:
        raise SCIM_TOKEN_NOT_FOUND.exc() from None

    _audit(
        request,
        identity=identity,
        action="scim.token_rotate",
        target_id=result.id,
        metadata={"name": result.name, "superseded_token_id": str(result.superseded_token_id)},
    )
    return ScimTokenCreateResponse(
        id=result.id, name=result.name, token=result.token, created_at=result.created_at
    )


# ---------------------------------------------------------------------------
# DELETE /admin/scim/tokens/{id}
# ---------------------------------------------------------------------------


@scim_token_router.delete("/{token_id}", status_code=204)
async def revoke_scim_token(
    request: Request,
    token_id: uuid.UUID,
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    repo: Annotated[SqlAlchemyScimTokenRepository, Depends(_get_repo)],
) -> None:
    use_case = RevokeScimTokenUseCase(repo)
    try:
        await use_case.execute(token_id=token_id, tenant_id=identity.tenant_id)
    except ScimTokenNotFoundError:
        raise SCIM_TOKEN_NOT_FOUND.exc() from None

    _audit(
        request,
        identity=identity,
        action="scim.token_revoke",
        target_id=token_id,
        metadata={},
    )
