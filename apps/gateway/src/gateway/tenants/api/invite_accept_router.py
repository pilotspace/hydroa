"""Public, unauthenticated invite-preview + invite-accept endpoints
(member-invite-acceptance TASK.md §3).

Endpoints:
  GET  /invites/{token}           — preview {tenant_name, email, role, expires_at} (M1)
  POST /invites/{token}/accept    — provision a user + flip the invite to accepted (M2)

NEITHER endpoint carries any auth dependency — the secret, unguessable token itself IS the
authorization. This is a SEPARATE router instance from invites_router.py (`/admin/invites`,
MEMBERS_MANAGE-gated) — mirrors how agent_oauth already splits its PUBLIC
device_authorize_router.py from its AUTHENTICATED device_approval_router.py despite both
operating on one shared table.

Security (server-side, authoritative):
  - Rate-limited per-client-IP FIRST, before any DB IO, on BOTH endpoints (M7) — fail-open
    on Redis outage (CLAUDE.md design-for-failure).
  - Distinguishable 404/409/410 for an already-known secret token — no new enumeration
    oracle, since the identifying key IS the unguessable secret itself (mirrors
    device-approval-flow's own AGENT_OAUTH_AUTHORIZATION_NOT_FOUND/_NOT_PENDING/_EXPIRED
    triad for an analogous secret-keyed resource).
  - Server-side-only identity resolution (M3): tenant_id/email/role always come from the
    invite row the token resolves to — InviteAcceptRequest uses extra="ignore" so any
    injected tenant_id/role/email in the body is silently DROPPED, never read.
  - Fail-closed atomicity (M5): every reject leaves the invites row unchanged and creates
    no users row — enforced by InviteRepository.accept's own one-transaction shape.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_EMAIL_TAKEN,
    AUTH_PASSWORD_WEAK,
    INVITE_EXPIRED,
    INVITE_NOT_FOUND,
    INVITE_NOT_PENDING,
    RATE_LIMITED,
)
from gateway.tenants.application.invite_accept_use_cases import (
    AcceptInviteUseCase,
    PreviewInviteUseCase,
)
from gateway.tenants.domain.errors import (
    EmailAlreadyRegisteredError,
    InviteExpiredError,
    InviteNotFoundError,
    InviteNotPendingError,
    WeakPasswordError,
)
from gateway.tenants.infrastructure.invite_public_rate_limiter import (
    InvitePublicRateLimiter,
    InviteRateLimitedError,
)
from gateway.tenants.infrastructure.invite_repository import InviteRepository

invite_accept_router = APIRouter(prefix="/invites", tags=["invites-public"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class InvitePreviewResponse(BaseModel):
    tenant_name: str
    email: str
    role: str
    expires_at: datetime


class InviteAcceptRequest(BaseModel):
    """``extra="ignore"``: any client-supplied identity field beyond ``password`` (e.g. an
    injected tenant_id/role/email, M3) is silently DROPPED at the schema boundary — never
    bound to a variable, never read by the router or use case."""

    model_config = ConfigDict(extra="ignore")

    password: str


class InviteAcceptResponse(BaseModel):
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    email: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_client_ip(request: Request) -> str:
    """Return the left-most IP from X-Forwarded-For, falling back to request.client.host.

    Byte-identical to agent_oauth's device_authorize_router.py::_resolve_client_ip — behind
    Envoy the trusted client IP is the left-most token in X-Forwarded-For; in direct/test
    calls (no proxy) request.client.host is used.
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def _get_repo(session: Annotated[AsyncSession, Depends(get_session)]) -> InviteRepository:
    return InviteRepository(session)


# ---------------------------------------------------------------------------
# GET /invites/{token}
# ---------------------------------------------------------------------------


@invite_accept_router.get("/{token}", response_model=InvitePreviewResponse)
async def preview_invite(
    token: str,
    request: Request,
    repo: Annotated[InviteRepository, Depends(_get_repo)],
) -> InvitePreviewResponse:
    """GET /invites/{token} — PUBLIC, no auth. Read-only preview of a pending invite (M1).

    Security: per-IP rate limit FIRST (fail-open on Redis outage), before any DB IO.
    """
    settings: Any = request.app.state.settings
    client_ip = _resolve_client_ip(request)
    limiter: InvitePublicRateLimiter = request.app.state.invite_public_limiter
    try:
        await limiter.check(key=client_ip, limit=settings.invite_preview_rpm)
    except InviteRateLimitedError as exc:
        raise RATE_LIMITED.exc(headers={"Retry-After": str(exc.retry_after)}) from None

    use_case = PreviewInviteUseCase(repo)
    try:
        preview = await use_case.execute(token=token, now=datetime.now(UTC))
    except InviteNotFoundError:
        raise INVITE_NOT_FOUND.exc() from None
    except InviteNotPendingError:
        raise INVITE_NOT_PENDING.exc() from None
    except InviteExpiredError:
        raise INVITE_EXPIRED.exc() from None

    return InvitePreviewResponse(
        tenant_name=preview.tenant_name,
        email=preview.email,
        role=preview.role.value,
        expires_at=preview.expires_at,
    )


# ---------------------------------------------------------------------------
# POST /invites/{token}/accept
# ---------------------------------------------------------------------------


@invite_accept_router.post("/{token}/accept", response_model=InviteAcceptResponse)
async def accept_invite(
    token: str,
    body: InviteAcceptRequest,
    request: Request,
    repo: Annotated[InviteRepository, Depends(_get_repo)],
) -> InviteAcceptResponse:
    """POST /invites/{token}/accept — PUBLIC, no auth. Provisions exactly ONE new user
    bound to the invite's tenant_id + role, flips the invite to accepted (M2/M3/M5/M6).

    Security: per-IP rate limit FIRST (fail-open on Redis outage), before any DB IO.
    """
    settings: Any = request.app.state.settings
    client_ip = _resolve_client_ip(request)
    limiter: InvitePublicRateLimiter = request.app.state.invite_public_limiter
    try:
        await limiter.check(key=client_ip, limit=settings.invite_accept_rpm)
    except InviteRateLimitedError as exc:
        raise RATE_LIMITED.exc(headers={"Retry-After": str(exc.retry_after)}) from None

    use_case = AcceptInviteUseCase(repo)
    try:
        tenant_id, user_id, email = await use_case.execute(
            token=token, password=body.password, now=datetime.now(UTC)
        )
    except WeakPasswordError:
        raise AUTH_PASSWORD_WEAK.exc() from None
    except InviteNotFoundError:
        raise INVITE_NOT_FOUND.exc() from None
    except InviteNotPendingError:
        raise INVITE_NOT_PENDING.exc() from None
    except InviteExpiredError:
        raise INVITE_EXPIRED.exc() from None
    except EmailAlreadyRegisteredError:
        raise AUTH_EMAIL_TAKEN.exc() from None

    # Audit — fire-and-forget, fail-open (M8). Preview is UNAUDITED (a read, no state
    # change to record — mirrors LIST being unaudited too). The newly-provisioned user is
    # both actor and target: no authenticated caller exists on this public surface, and
    # this action is genuinely self-service.
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=user_id,
                actor_email=email,
                action="invite.accept",
                target_type="user",
                target_id=str(user_id),
                result="success",
                metadata={"tenant_id": str(tenant_id), "user_id": str(user_id), "email": email},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return InviteAcceptResponse(tenant_id=tenant_id, user_id=user_id, email=email)
