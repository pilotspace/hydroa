"""Admin API for tenant email-domain ownership claims (domain-capture TASK.md §3 — FROZEN @
v1, SECURITY task).

Routes (OWNER-only, prefix /admin/domain-claims):
  POST   /admin/domain-claims              — create/reissue a DNS-TXT challenge (M2)
  GET    /admin/domain-claims               — list the caller's own tenant's claims (M5)
  POST   /admin/domain-claims/{id}/verify   — perform the bounded DNS TXT lookup (M6, M13)
  DELETE /admin/domain-claims/{id}          — revoke a claim in any status (M7)

`_get_owner_identity` is duplicated here (NOT imported) — mirrors saml_admin_router.py's own
documented precedent: avoid a dependency on a presumptively-frozen sibling admin-router file.

Every endpoint is rate-limited per tenant_id (M14, fail-open on Redis outage — matches the
invite limiter's own posture) BEFORE any DB IO beyond the owner-identity check itself.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_FORBIDDEN_OWNER_REQUIRED,
    AUTH_TOKEN_INVALID,
    DNS_LOOKUP_FAILED,
    DOMAIN_ALREADY_VERIFIED,
    DOMAIN_CLAIM_EXPIRED,
    DOMAIN_CLAIM_NOT_FOUND,
    DOMAIN_INVALID,
    DOMAIN_VERIFICATION_FAILED,
    RATE_LIMITED,
)
from gateway.domain_capture.api.deps import (
    get_create_claim_use_case,
    get_domain_claim_rate_limiter,
    get_domain_claim_repository,
    get_revoke_claim_use_case,
    get_verify_claim_use_case,
)
from gateway.domain_capture.api.schemas import (
    DomainClaimCreateRequest,
    DomainClaimCreateResponse,
    DomainClaimListResponse,
    DomainClaimVerifyResponse,
    to_create_response,
    to_list_item,
    to_verify_response,
)
from gateway.domain_capture.domain.errors import (
    DnsLookupFailedError,
    DomainAlreadyVerifiedError,
    DomainClaimExpiredError,
    DomainClaimNotFoundError,
    DomainClaimRateLimitedError,
    DomainInvalidError,
    DomainVerificationFailedError,
)
from gateway.tenants.application.use_cases import GetIdentityUseCase
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.infrastructure.impersonation_session_guard import DbImpersonationSessionGuard

domain_claims_router = APIRouter(prefix="/admin/domain-claims", tags=["domain-claims-admin"])


async def _get_owner_identity(request: Request, session: AsyncSession) -> Identity:
    """Resolve the caller's full Identity, enforcing owner role (duplicated from
    oidc_admin_router.py / saml_admin_router.py per those files' own §3 freeze precedent)."""
    from gateway.tenants.api.deps import get_bearer_token

    token = get_bearer_token(request)
    tokens = request.app.state.token_service
    use_case = GetIdentityUseCase(
        tokens,
        guard_factory=lambda: DbImpersonationSessionGuard(
            session=session,
            timeout_seconds=request.app.state.settings.impersonation_live_check_timeout_seconds,
        ),
    )
    try:
        identity = await use_case.execute(token)
    except Exception as exc:
        raise AUTH_TOKEN_INVALID.exc() from exc

    if identity.role != Role.OWNER:
        raise AUTH_FORBIDDEN_OWNER_REQUIRED.exc()

    return identity


async def _rate_limit(request: Request, *, action: str, tenant_id: uuid.UUID, limit: int) -> None:
    limiter = get_domain_claim_rate_limiter(request)
    try:
        await limiter.check(action=action, tenant_id=tenant_id, limit=limit)
    except DomainClaimRateLimitedError as exc:
        raise RATE_LIMITED.exc(headers={"Retry-After": str(exc.retry_after)}) from None


@domain_claims_router.post("", status_code=201, response_model=DomainClaimCreateResponse)
async def create_domain_claim(
    request: Request,
    body: DomainClaimCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainClaimCreateResponse:
    identity = await _get_owner_identity(request, session)
    settings = request.app.state.settings
    await _rate_limit(
        request,
        action="create",
        tenant_id=identity.tenant_id,
        limit=settings.domain_claim_create_rpm,
    )

    use_case = get_create_claim_use_case(request, session)
    try:
        claim = await use_case.execute(
            tenant_id=identity.tenant_id,
            domain_raw=body.domain,
            created_by_user_id=identity.user_id,
        )
    except DomainInvalidError:
        raise DOMAIN_INVALID.exc() from None
    except DomainAlreadyVerifiedError:
        raise DOMAIN_ALREADY_VERIFIED.exc() from None
    return to_create_response(claim)


@domain_claims_router.get("", response_model=DomainClaimListResponse)
async def list_domain_claims(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainClaimListResponse:
    identity = await _get_owner_identity(request, session)
    repository = get_domain_claim_repository(request, session)
    claims = await repository.list_for_tenant(identity.tenant_id)
    return DomainClaimListResponse(claims=[to_list_item(c) for c in claims])


@domain_claims_router.post("/{claim_id}/verify", response_model=DomainClaimVerifyResponse)
async def verify_domain_claim(
    request: Request,
    claim_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainClaimVerifyResponse:
    identity = await _get_owner_identity(request, session)
    settings = request.app.state.settings
    await _rate_limit(
        request,
        action="verify",
        tenant_id=identity.tenant_id,
        limit=settings.domain_claim_verify_rpm,
    )

    use_case = get_verify_claim_use_case(request, session)
    try:
        claim = await use_case.execute(claim_id=claim_id, tenant_id=identity.tenant_id)
    except DomainClaimNotFoundError:
        raise DOMAIN_CLAIM_NOT_FOUND.exc() from None
    except DomainClaimExpiredError:
        raise DOMAIN_CLAIM_EXPIRED.exc() from None
    except DnsLookupFailedError:
        raise DNS_LOOKUP_FAILED.exc() from None
    except DomainVerificationFailedError:
        raise DOMAIN_VERIFICATION_FAILED.exc() from None
    except DomainAlreadyVerifiedError:
        raise DOMAIN_ALREADY_VERIFIED.exc() from None
    return to_verify_response(claim)


@domain_claims_router.delete("/{claim_id}", status_code=204)
async def revoke_domain_claim(
    request: Request,
    claim_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    identity = await _get_owner_identity(request, session)
    use_case = get_revoke_claim_use_case(request, session)
    try:
        await use_case.execute(claim_id=claim_id, tenant_id=identity.tenant_id)
    except DomainClaimNotFoundError:
        raise DOMAIN_CLAIM_NOT_FOUND.exc() from None
