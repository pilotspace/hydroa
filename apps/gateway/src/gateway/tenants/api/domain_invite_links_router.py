"""Authenticated admin router for domain-restricted shareable invite links
(invite-by-domain TASK.md §3, FROZEN @ v1, SECURITY, task 6a).

Endpoints (all MEMBERS_MANAGE-gated, tenant-scoped):
  POST   /admin/domain-invite-links        — create-if-eligible (member/owner-verified)
  GET    /admin/domain-invite-links        — list ACTIVE links (token-free)
  DELETE /admin/domain-invite-links/{id}   — revoke (idempotent, tenant-scoped, no oracle)

A SIBLING surface to the per-email invites_router.py — reuses its router/schema/`_get_repo`
idiom + the shared require_permission(MEMBERS_MANAGE) gate; the per-email endpoints are
untouched. The member-verified/owner-verified create-eligibility gate is the create-side
security boundary (M1, anti-confused-deputy).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.error_catalog import DOMAIN_INVITE_NOT_ELIGIBLE, INVITE_NOT_FOUND
from gateway.tenants.application.domain_invite_link_use_cases import (
    CreateDomainInviteLinkUseCase,
    ListDomainInviteLinksUseCase,
    RevokeDomainInviteLinkUseCase,
)
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity
from gateway.tenants.domain.errors import (
    DomainInviteNotEligibleError,
    InviteNotFoundError,
)
from gateway.tenants.infrastructure.domain_invite_link_repository import (
    DomainInviteLinkRepository,
)

domain_invite_links_router = APIRouter(
    prefix="/admin/domain-invite-links", tags=["domain-invite-links"]
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DomainInviteLinkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DomainInviteLinkCreateResponse(BaseModel):
    id: uuid.UUID
    domain: str
    token: str  # PLAINTEXT — returned exactly once, never retrievable again (M2)
    status: str
    expires_at: datetime
    created_at: datetime


class DomainInviteLinkItem(BaseModel):
    id: uuid.UUID
    domain: str
    status: str
    expires_at: datetime
    created_at: datetime
    # Deliberately NO token / token_hash field (M4).


class DomainInviteLinkListResponse(BaseModel):
    links: list[DomainInviteLinkItem]


class DomainInviteLinkRevokeResponse(BaseModel):
    id: uuid.UUID
    status: str


# ---------------------------------------------------------------------------
# Dependency factory
# ---------------------------------------------------------------------------


def _get_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainInviteLinkRepository:
    return DomainInviteLinkRepository(session)


# ---------------------------------------------------------------------------
# POST /admin/domain-invite-links
# ---------------------------------------------------------------------------


@domain_invite_links_router.post("", response_model=DomainInviteLinkCreateResponse, status_code=201)
async def create_domain_invite_link(
    request: Request,
    body: DomainInviteLinkCreateRequest,
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    repo: Annotated[DomainInviteLinkRepository, Depends(_get_repo)],
) -> DomainInviteLinkCreateResponse:
    """Create (or atomically supersede) an active domain invite link — eligible only if the
    caller's tenant is member/owner-verified on the domain (M1/M2/M3)."""
    settings = request.app.state.settings
    use_case = CreateDomainInviteLinkUseCase(
        repo, link_ttl_days=settings.domain_invite_link_ttl_days
    )
    try:
        link, token = await use_case.execute(
            tenant_id=identity.tenant_id,
            domain_raw=body.domain,
            created_by_user_id=identity.user_id,
            now=datetime.now(UTC),
        )
    except DomainInviteNotEligibleError:
        raise DOMAIN_INVITE_NOT_ELIGIBLE.exc() from None

    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="domain_invite_link.create",
                target_type="domain_invite_link",
                target_id=str(link.id),
                result="success",
                metadata={"domain": link.domain},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return DomainInviteLinkCreateResponse(
        id=link.id,
        domain=link.domain,
        token=token,
        status=link.status.value,
        expires_at=link.expires_at,
        created_at=link.created_at,
    )


# ---------------------------------------------------------------------------
# GET /admin/domain-invite-links
# ---------------------------------------------------------------------------


@domain_invite_links_router.get("", response_model=DomainInviteLinkListResponse)
async def list_domain_invite_links(
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    repo: Annotated[DomainInviteLinkRepository, Depends(_get_repo)],
) -> DomainInviteLinkListResponse:
    """List ACTIVE links in the caller's tenant — never a token, never a cross-tenant row (M4)."""
    use_case = ListDomainInviteLinksUseCase(repo)
    links = await use_case.execute(tenant_id=identity.tenant_id)
    return DomainInviteLinkListResponse(
        links=[
            DomainInviteLinkItem(
                id=lk.id,
                domain=lk.domain,
                status=lk.status.value,
                expires_at=lk.expires_at,
                created_at=lk.created_at,
            )
            for lk in links
        ]
    )


# ---------------------------------------------------------------------------
# DELETE /admin/domain-invite-links/{link_id}
# ---------------------------------------------------------------------------


@domain_invite_links_router.delete("/{link_id}", response_model=DomainInviteLinkRevokeResponse)
async def revoke_domain_invite_link(
    request: Request,
    link_id: uuid.UUID,
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    repo: Annotated[DomainInviteLinkRepository, Depends(_get_repo)],
) -> DomainInviteLinkRevokeResponse:
    """Revoke a link in the caller's tenant (idempotent). Unknown OR cross-tenant id → 404
    (no oracle, R13)."""
    use_case = RevokeDomainInviteLinkUseCase(repo)
    try:
        link = await use_case.execute(link_id=link_id, tenant_id=identity.tenant_id)
    except InviteNotFoundError:
        raise INVITE_NOT_FOUND.exc() from None

    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="domain_invite_link.revoke",
                target_type="domain_invite_link",
                target_id=str(link.id),
                result="success",
                metadata={"domain": link.domain},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return DomainInviteLinkRevokeResponse(id=link.id, status=link.status.value)
