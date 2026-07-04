"""FastAPI router for tenant invite issuance (member-invite-issuance TASK.md §3).

Endpoints:
  POST   /admin/invites               — create/re-invite (MEMBERS_MANAGE + escalation ceiling)
  GET    /admin/invites               — list pending invites (MEMBERS_MANAGE)
  DELETE /admin/invites/{invite_id}   — revoke a pending invite (MEMBERS_MANAGE, tenant-scoped)

Security (server-side, authoritative):
  - require_permission(MEMBERS_MANAGE) → only owner/admin pass; others get 403.
  - role == "superadmin" (or any unparseable role) rejected BEFORE the use case runs —
    byte-identical 422 shape to users_router.py's own pre-check (M2/R4). This is a REAL
    two-step check, not just a try/except: Role.SUPERADMIN is a genuine StrEnum member
    (entities.py:8-19), so ``Role("superadmin")`` parses successfully here — it does NOT
    raise ValueError — and must be explicitly excluded as its own step, exactly mirroring
    users_router.py::assign_user_role's two-part pre-check. (Omitting this explicit second
    check would let a superadmin invite reach the use case, where the shared
    assert_role_within_ceiling guard would still block it — but as a 403 ERR_AUTH_FORBIDDEN,
    not the 422 ERR_PAYLOAD_INVALID M2/R4 require — so both steps are required for the
    contracted shape, not just for defense-in-depth.)
  - ESCALATION CEILING enforced via the SAME shared predicate PUT /admin/users/{id}/role uses
    (assert_role_within_ceiling) — never a second, hand-rolled copy.
  - Anti-enumeration: CREATE never distinguishes a brand-new email from one that belongs to
    a user in a DIFFERENT tenant (teams-add-by-email precedent) — only a CALLER-tenant email
    collision is disclosed (409).
  - Tenant isolation: LIST/REVOKE never see or touch another tenant's invites; an unknown OR
    cross-tenant invite_id on REVOKE returns the SAME 404 as a truly unknown id (no oracle).
  - Audit: invite.create / invite.revoke fire fail-open; LIST is unaudited (mirrors list_users).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_FORBIDDEN,
    INVITE_EMAIL_ALREADY_MEMBER,
    INVITE_NOT_FOUND,
    INVITE_NOT_PENDING,
    PAYLOAD_INVALID,
)
from gateway.tenants.application.invite_use_cases import (
    CreateInviteUseCase,
    ListPendingInvitesUseCase,
    RevokeInviteUseCase,
)
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.domain.errors import (
    EscalationForbiddenError,
    InviteEmailAlreadyMemberError,
    InviteNotFoundError,
    InviteNotPendingError,
)
from gateway.tenants.infrastructure.invite_repository import InviteRepository

invites_router = APIRouter(prefix="/admin/invites", tags=["invites"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class InviteCreateRequest(BaseModel):
    email: EmailStr
    role: str


class InviteCreateResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    status: str
    expires_at: datetime
    created_at: datetime
    invited_by_user_id: uuid.UUID
    token: str  # PLAINTEXT — shown exactly once, never retrievable again (M3)


class InviteResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    status: str
    expires_at: datetime
    created_at: datetime
    invited_by_user_id: uuid.UUID
    # Deliberately NO token / token_hash field (M8).


class InviteListResponse(BaseModel):
    invites: list[InviteResponse]


# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


def _get_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InviteRepository:
    return InviteRepository(session)


# ---------------------------------------------------------------------------
# POST /admin/invites
# ---------------------------------------------------------------------------


@invites_router.post("", response_model=InviteCreateResponse, status_code=201)
async def create_invite(
    request: Request,
    body: InviteCreateRequest,
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    repo: Annotated[InviteRepository, Depends(_get_repo)],
) -> InviteCreateResponse:
    """POST /admin/invites — create (or atomically re-invite/supersede) a pending invite.

    Security (all enforced server-side):
      - MEMBERS_MANAGE gating (owner/admin only).
      - role == "superadmin" (or any unparseable role) → 422, BEFORE the use case runs.
      - ESCALATION CEILING: admin cannot invite owner/admin → 403.
      - Anti-enumeration: never reveals whether the email belongs to a different tenant.
    """
    # Validate the role value before hitting the use-case — byte-identical pre-check shape
    # to users_router.py::assign_user_role (M2/R3). "superadmin" IS a valid Role member
    # (unlike a truly unknown literal like "wizard"), so Role(body.role) below does NOT
    # raise for it — the explicit equality check immediately after is what rejects it, for
    # EVERY caller regardless of role/tenant, before CreateInviteUseCase ever runs.
    try:
        role = Role(body.role)
    except ValueError:
        raise PAYLOAD_INVALID.exc(detail=f"Unknown role: {body.role!r}") from None

    # M2/R4: "superadmin" is never assignable via this generic, unaudited-at-this-layer
    # endpoint, for EVERY caller — rejected here with the SAME 422 shape as an unparseable
    # role literal, byte-identical to users_router.py:121-129's own pre-check for the same
    # payload. Never routed through the escalation-guard 403 path.
    if role == Role.SUPERADMIN:
        raise PAYLOAD_INVALID.exc(detail=f"Unknown role: {body.role!r}") from None

    use_case = CreateInviteUseCase(repo)
    try:
        invite, token = await use_case.execute(
            caller_user_id=identity.user_id,
            caller_role=identity.role,
            tenant_id=identity.tenant_id,
            email=body.email,
            role=role,
            now=datetime.now(UTC),
        )
    except EscalationForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except InviteEmailAlreadyMemberError:
        raise INVITE_EMAIL_ALREADY_MEMBER.exc() from None

    # Audit — fire-and-forget, fail-open
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="invite.create",
                target_type="invite",
                target_id=str(invite.id),
                result="success",
                metadata={"email": invite.email, "role": invite.role.value},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return InviteCreateResponse(
        id=invite.id,
        email=invite.email,
        role=invite.role.value,
        status=invite.status.value,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
        invited_by_user_id=invite.invited_by_user_id,
        token=token,
    )


# ---------------------------------------------------------------------------
# GET /admin/invites
# ---------------------------------------------------------------------------


@invites_router.get("", response_model=InviteListResponse)
async def list_invites(
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    repo: Annotated[InviteRepository, Depends(_get_repo)],
) -> InviteListResponse:
    """GET /admin/invites — list all pending invites in the caller's tenant.

    Requires MEMBERS_MANAGE (owner/admin only). Never includes token/token_hash (M8).
    """
    use_case = ListPendingInvitesUseCase(repo)
    invites = await use_case.execute(tenant_id=identity.tenant_id)
    return InviteListResponse(
        invites=[
            InviteResponse(
                id=i.id,
                email=i.email,
                role=i.role.value,
                status=i.status.value,
                expires_at=i.expires_at,
                created_at=i.created_at,
                invited_by_user_id=i.invited_by_user_id,
            )
            for i in invites
        ]
    )


# ---------------------------------------------------------------------------
# DELETE /admin/invites/{invite_id}
# ---------------------------------------------------------------------------


@invites_router.delete("/{invite_id}", status_code=204)
async def revoke_invite(
    request: Request,
    invite_id: uuid.UUID,
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    repo: Annotated[InviteRepository, Depends(_get_repo)],
) -> None:
    """DELETE /admin/invites/{invite_id} — revoke a pending invite.

    Security (all enforced server-side):
      - MEMBERS_MANAGE gating (owner/admin only) — tenant-scoped, NOT creator-scoped: any
        current owner/admin may revoke any pending invite in their tenant.
      - Tenant isolation: unknown id OR cross-tenant id → SAME 404 (no oracle).
      - Status guard: already-accepted/already-revoked → 409.
    """
    use_case = RevokeInviteUseCase(repo)
    try:
        invite = await use_case.execute(invite_id=invite_id, tenant_id=identity.tenant_id)
    except InviteNotFoundError:
        raise INVITE_NOT_FOUND.exc() from None
    except InviteNotPendingError:
        raise INVITE_NOT_PENDING.exc() from None

    # Audit — fire-and-forget, fail-open
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="invite.revoke",
                target_type="invite",
                target_id=str(invite.id),
                result="success",
                metadata={"invite_id": str(invite.id)},
                created_at=datetime.now(UTC),
            ),
        )
    )
