"""Public, unauthenticated two-step redeem router for domain invite links
(invite-by-domain TASK.md §3, FROZEN @ v1, SECURITY, task 6a).

Endpoints (NO auth — the secret token IS the resource key; the redeemer has no account yet):
  POST /domain-invite-links/{token}/redeem         — step-1: supply @domain email → 6-digit code
  POST /domain-invite-links/{token}/redeem/verify  — step-2: code+password → provision ONE MEMBER

A SIBLING of invite_accept_router.py — reuses VERBATIM: resolve_trusted_client_ip, the per-IP
InvitePublicRateLimiter (checked FIRST, before any DB IO, fail-open on Redis outage),
fire-and-forget fail-open record_audit, and the extra="ignore" identity-drop schema guard.
The link's domain/tenant/role are NEVER client-supplied — role is FIXED MEMBER, never
SUPERADMIN, and resolve_verified_tenant (the DNS-only auto-join gate) is never consulted here.
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
    DOMAIN_INVITE_DOMAIN_MISMATCH,
    DOMAIN_INVITE_LINK_INACTIVE,
    INVITE_EXPIRED,
    INVITE_NOT_FOUND,
    MEMBER_VERIFY_CODE_EXPIRED,
    MEMBER_VERIFY_CODE_INVALID,
    MEMBER_VERIFY_TOO_MANY_ATTEMPTS,
    PLAN_SEAT_CAP_EXCEEDED,
    RATE_LIMITED,
)
from gateway.core.net import resolve_trusted_client_ip
from gateway.domain_capture.domain.errors import (
    MemberVerifyCodeExpiredError,
    MemberVerifyCodeInvalidError,
    MemberVerifyTooManyAttemptsError,
)
from gateway.tenants.application.domain_invite_link_use_cases import (
    IssueDomainRedemptionCodeUseCase,
    VerifyDomainRedemptionUseCase,
)
from gateway.tenants.domain.errors import (
    DomainInviteDomainMismatchError,
    DomainInviteLinkInactiveError,
    EmailAlreadyRegisteredError,
    InviteExpiredError,
    InviteNotFoundError,
    SeatCapExceededError,
    WeakPasswordError,
)
from gateway.tenants.infrastructure.domain_invite_link_repository import (
    DomainInviteLinkRepository,
)
from gateway.tenants.infrastructure.invite_public_rate_limiter import (
    InvitePublicRateLimiter,
    InviteRateLimitedError,
)

domain_invite_redeem_router = APIRouter(
    prefix="/domain-invite-links", tags=["domain-invite-links-public"]
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DomainRedeemRequest(BaseModel):
    """``extra="ignore"``: only ``email`` is read; any injected field is dropped."""

    model_config = ConfigDict(extra="ignore")

    email: str


class DomainRedeemResponse(BaseModel):
    email: str


class DomainRedeemVerifyRequest(BaseModel):
    """``extra="ignore"``: any injected tenant_id/role/domain (R15) is silently DROPPED —
    identity is resolved EXCLUSIVELY from the link + the (link, email) redemption row."""

    model_config = ConfigDict(extra="ignore")

    email: str
    code: str
    password: str


class DomainRedeemVerifyResponse(BaseModel):
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    email: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainInviteLinkRepository:
    return DomainInviteLinkRepository(session)


async def _rate_limit(request: Request, *, action: str, limit: int) -> None:
    """Per-IP limit FIRST, before any DB IO; fail-open on Redis outage (M11)."""
    settings: Any = request.app.state.settings
    client_ip = resolve_trusted_client_ip(request, settings.trusted_proxy_hops)
    limiter: InvitePublicRateLimiter = request.app.state.invite_public_limiter
    try:
        await limiter.check(action=action, key=client_ip, limit=limit)
    except InviteRateLimitedError as exc:
        raise RATE_LIMITED.exc(headers={"Retry-After": str(exc.retry_after)}) from None


# ---------------------------------------------------------------------------
# POST /domain-invite-links/{token}/redeem
# ---------------------------------------------------------------------------


@domain_invite_redeem_router.post(
    "/{token}/redeem", response_model=DomainRedeemResponse, status_code=202
)
async def redeem_domain_invite_link(
    token: str,
    body: DomainRedeemRequest,
    request: Request,
    repo: Annotated[DomainInviteLinkRepository, Depends(_get_repo)],
) -> DomainRedeemResponse:
    """Step-1 (issue): the redeemer supplies their @domain email → a 6-digit code is armed
    and emailed. No account-existence disclosure (M8)."""
    settings: Any = request.app.state.settings
    await _rate_limit(request, action="domain_redeem", limit=settings.domain_invite_redeem_rpm)

    use_case = IssueDomainRedemptionCodeUseCase(
        repo,
        request.app.state.email_sender,
        jwt_secret=settings.jwt_secret,
        code_ttl_minutes=settings.domain_invite_code_ttl_minutes,
        origin=settings.dashboard_public_origin,
    )
    try:
        email = await use_case.execute(token=token, email_raw=body.email, now=datetime.now(UTC))
    except InviteNotFoundError:
        raise INVITE_NOT_FOUND.exc() from None
    except DomainInviteLinkInactiveError:
        raise DOMAIN_INVITE_LINK_INACTIVE.exc() from None
    except InviteExpiredError:
        raise INVITE_EXPIRED.exc() from None
    except DomainInviteDomainMismatchError:
        raise DOMAIN_INVITE_DOMAIN_MISMATCH.exc() from None

    return DomainRedeemResponse(email=email)


# ---------------------------------------------------------------------------
# POST /domain-invite-links/{token}/redeem/verify
# ---------------------------------------------------------------------------


@domain_invite_redeem_router.post(
    "/{token}/redeem/verify", response_model=DomainRedeemVerifyResponse, status_code=201
)
async def verify_domain_invite_redemption(
    token: str,
    body: DomainRedeemVerifyRequest,
    request: Request,
    repo: Annotated[DomainInviteLinkRepository, Depends(_get_repo)],
) -> DomainRedeemVerifyResponse:
    """Step-2 (verify + provision): code+password provision exactly ONE MEMBER of the link's
    tenant, in one FOR-UPDATE transaction (M7). Fixed role MEMBER; injected identity dropped."""
    settings: Any = request.app.state.settings
    await _rate_limit(request, action="domain_verify", limit=settings.domain_invite_verify_rpm)

    use_case = VerifyDomainRedemptionUseCase(
        repo,
        jwt_secret=settings.jwt_secret,
        max_attempts=settings.domain_invite_code_max_attempts,
    )
    try:
        tenant_id, user_id, email = await use_case.execute(
            token=token,
            email_raw=body.email,
            code=body.code,
            password=body.password,
            now=datetime.now(UTC),
        )
    except WeakPasswordError:
        raise AUTH_PASSWORD_WEAK.exc() from None
    except InviteNotFoundError:
        raise INVITE_NOT_FOUND.exc() from None
    except DomainInviteLinkInactiveError:
        raise DOMAIN_INVITE_LINK_INACTIVE.exc() from None
    except InviteExpiredError:
        raise INVITE_EXPIRED.exc() from None
    except DomainInviteDomainMismatchError:
        raise DOMAIN_INVITE_DOMAIN_MISMATCH.exc() from None
    except MemberVerifyCodeInvalidError:
        raise MEMBER_VERIFY_CODE_INVALID.exc() from None
    except MemberVerifyCodeExpiredError:
        raise MEMBER_VERIFY_CODE_EXPIRED.exc() from None
    except MemberVerifyTooManyAttemptsError:
        raise MEMBER_VERIFY_TOO_MANY_ATTEMPTS.exc() from None
    except SeatCapExceededError as exc:
        raise PLAN_SEAT_CAP_EXCEEDED.exc(
            extra={
                "upgrade_hint": {
                    "plan_id": str(exc.plan_id),
                    "plan_name": exc.plan_name,
                    "seat_cap": exc.seat_cap,
                    "current_seats": exc.current_seats,
                }
            }
        ) from None
    except EmailAlreadyRegisteredError:
        raise AUTH_EMAIL_TAKEN.exc() from None

    # Audit — fire-and-forget, fail-open (M12). Step-1 issue is UNAUDITED (mirrors preview).
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=user_id,
                actor_email=email,
                action="domain_invite_link.redeem",
                target_type="user",
                target_id=str(user_id),
                result="success",
                metadata={"tenant_id": str(tenant_id), "user_id": str(user_id), "email": email},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return DomainRedeemVerifyResponse(tenant_id=tenant_id, user_id=user_id, email=email)
