"""Public API for access requests (signup-refusal-router TASK.md §3 — FROZEN @ v1,
SECURITY task).

Routes (PUBLIC, unauthenticated — prefix /admin/auth, alongside signup/login):
  POST /admin/auth/access-requests   — capture-only lead: {email} -> 202 {ok: true} (M4-M6)

Mirrors tenants/api/domain_invite_redeem_router.py's public-endpoint shape EXACTLY:
resolve_trusted_client_ip + a dedicated per-IP rate limiter (checked FIRST, before any DB
IO, fail-open on Redis outage) + the RFC 9457 RATE_LIMITED ErrorSpec reused verbatim.

SAFETY RULE (binding, §3): every response for a syntactically-valid email is byte-
identical (status 202, body {"ok": true}) regardless of what the server does or doesn't
know about that email or its domain — no branch in this router (or the use case it calls)
reads `resolve_verified_tenant`, any user/tenant table, or any domain-claim state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.access_requests.api.schemas import AccessRequestCreate, AccessRequestCreateResponse
from gateway.access_requests.application.create_access_request_use_case import (
    CreateAccessRequestUseCase,
)
from gateway.access_requests.infrastructure.rate_limiter import (
    AccessRequestIpRateLimiter,
    AccessRequestRateLimitedError,
)
from gateway.access_requests.infrastructure.repository import SqlAlchemyAccessRequestRepository
from gateway.core.db import get_session
from gateway.core.error_catalog import RATE_LIMITED
from gateway.core.net import resolve_trusted_client_ip

access_requests_router = APIRouter(prefix="/admin/auth", tags=["access-requests-public"])


def _get_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CreateAccessRequestUseCase:
    return CreateAccessRequestUseCase(SqlAlchemyAccessRequestRepository(session))


async def _rate_limit(request: Request) -> None:
    """Per-IP limit FIRST, before any DB IO; fail-open on Redis outage (M7)."""
    settings: Any = request.app.state.settings
    client_ip = resolve_trusted_client_ip(request, settings.trusted_proxy_hops)
    limiter: AccessRequestIpRateLimiter = request.app.state.access_request_limiter
    try:
        await limiter.check(key=client_ip, limit=settings.access_request_rpm)
    except AccessRequestRateLimitedError as exc:
        raise RATE_LIMITED.exc(headers={"Retry-After": str(exc.retry_after)}) from None


@access_requests_router.post(
    "/access-requests", response_model=AccessRequestCreateResponse, status_code=202
)
async def create_access_request(
    body: AccessRequestCreate,
    request: Request,
    use_case: Annotated[CreateAccessRequestUseCase, Depends(_get_use_case)],
) -> AccessRequestCreateResponse:
    await _rate_limit(request)
    await use_case.execute(email_raw=body.email, now=datetime.now(UTC))
    return AccessRequestCreateResponse()
