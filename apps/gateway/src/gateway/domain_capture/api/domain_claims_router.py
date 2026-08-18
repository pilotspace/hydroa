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
    AUTH_UNAVAILABLE,
    DNS_LOOKUP_FAILED,
    DOMAIN_ALREADY_VERIFIED,
    DOMAIN_CLAIM_EXPIRED,
    DOMAIN_CLAIM_NOT_FOUND,
    DOMAIN_CLAIM_NOT_PENDING,
    DOMAIN_GENERIC,
    DOMAIN_INVALID,
    DOMAIN_VERIFICATION_FAILED,
    MEMBER_VERIFY_CODE_EXPIRED,
    MEMBER_VERIFY_CODE_INVALID,
    MEMBER_VERIFY_DOMAIN_MISMATCH,
    MEMBER_VERIFY_NOT_ELIGIBLE,
    MEMBER_VERIFY_TOO_MANY_ATTEMPTS,
    RATE_LIMITED,
)
from gateway.domain_capture.api.deps import (
    get_create_claim_use_case,
    get_domain_claim_rate_limiter,
    get_domain_claim_repository,
    get_member_verify_resend_use_case,
    get_member_verify_use_case,
    get_notify_optin_use_case,
    get_notify_optout_use_case,
    get_registrar_hint_use_case,
    get_revoke_claim_use_case,
    get_verify_claim_use_case,
)
from gateway.domain_capture.api.schemas import (
    DomainClaimCreateRequest,
    DomainClaimCreateResponse,
    DomainClaimListItem,
    DomainClaimListResponse,
    DomainClaimVerifyResponse,
    MemberVerifyRequest,
    RegistrarHintResponse,
    to_create_response,
    to_list_item,
    to_registrar_hint_response,
    to_verify_response,
)
from gateway.domain_capture.domain.errors import (
    DnsLookupFailedError,
    DomainAlreadyVerifiedError,
    DomainClaimExpiredError,
    DomainClaimNotFoundError,
    DomainClaimNotPendingError,
    DomainClaimRateLimitedError,
    DomainGenericError,
    DomainInvalidError,
    DomainVerificationFailedError,
    MemberVerifyCodeExpiredError,
    MemberVerifyCodeInvalidError,
    MemberVerifyDomainMismatchError,
    MemberVerifyNotEligibleError,
    MemberVerifyTooManyAttemptsError,
)
from gateway.tenants.application.use_cases import GetIdentityUseCase
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.domain.errors import SessionRevocationUnavailableError
from gateway.tenants.infrastructure.impersonation_session_guard import DbImpersonationSessionGuard
from gateway.tenants.infrastructure.repository import get_tenant_by_id
from gateway.tenants.infrastructure.session_revocation import DbSessionRevocationGuard

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
        # auth-hardening-login-sessions TASK.md §3 M5 — REQUIRED keyword: this site
        # cannot silently skip the revocation check.
        revocation_guard_factory=lambda: DbSessionRevocationGuard(
            session=session,
            timeout_seconds=request.app.state.settings.session_revocation_check_timeout_seconds,
        ),
    )
    try:
        identity = await use_case.execute(token)
    except SessionRevocationUnavailableError:
        # M6 (auth-hardening-login-sessions): store failure is a 503, never a 401
        # that lies about a live token — and never swallowed by the catch-all below.
        raise AUTH_UNAVAILABLE.exc() from None
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


@domain_claims_router.get("/registrar-hint", response_model=RegistrarHintResponse)
async def get_registrar_hint(
    request: Request,
    domain: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RegistrarHintResponse:
    """Nameserver-inferred registrar deep-link hint (registrar-hint TASK.md §3 — FROZEN
    @ v1). OWNER-only (M8), rate-limited BEFORE any DNS IO (M7), ZERO database IO (M12) —
    `session` is only threaded through to `_get_owner_identity`'s verbatim-reused shape
    (an ordinary, non-impersonating identity's resolution never touches the DB, per
    `ensure_impersonation_session_live`'s own zero-overhead guarantee). Never a 5xx: the
    use case itself absorbs every DNS-lookup failure/miss into a graceful fallback (M4/M6)."""
    identity = await _get_owner_identity(request, session)
    settings = request.app.state.settings
    await _rate_limit(
        request,
        action="registrar_hint",
        tenant_id=identity.tenant_id,
        limit=settings.domain_claim_registrar_hint_rpm,
    )

    use_case = get_registrar_hint_use_case(request)
    try:
        result = await use_case.execute(domain)
    except DomainInvalidError:
        raise DOMAIN_INVALID.exc() from None
    return to_registrar_hint_response(result)


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


@domain_claims_router.post("/{claim_id}/notify", response_model=DomainClaimListItem)
async def opt_in_domain_claim_notify(
    request: Request,
    claim_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainClaimListItem:
    """domain-verify-notify TASK.md §3 M1 (FROZEN @ v1, SECURITY) — body is intentionally
    unread: NO email field exists on this route at all (R-sec-1, the anti-spam guard).
    The recipient is decided later, server-side, by DomainVerifyNotifyScheduler."""
    identity = await _get_owner_identity(request, session)
    settings = request.app.state.settings
    await _rate_limit(
        request,
        action="notify",
        tenant_id=identity.tenant_id,
        limit=settings.domain_claim_create_rpm,
    )

    use_case = get_notify_optin_use_case(request, session)
    try:
        claim = await use_case.execute(claim_id=claim_id, tenant_id=identity.tenant_id)
    except DomainClaimNotFoundError:
        raise DOMAIN_CLAIM_NOT_FOUND.exc() from None
    except DomainClaimNotPendingError:
        raise DOMAIN_CLAIM_NOT_PENDING.exc() from None
    except DomainClaimExpiredError:
        raise DOMAIN_CLAIM_EXPIRED.exc() from None
    return to_list_item(claim)


@domain_claims_router.post("/{claim_id}/member-verify", response_model=DomainClaimListItem)
async def member_verify_domain_claim(
    request: Request,
    claim_id: uuid.UUID,
    body: MemberVerifyRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainClaimListItem:
    """member-verified-recognition TASK.md §3 M3 (FROZEN @ v1, SECURITY) — OWNER-only,
    per-tenant rate-limited. Body is ONLY the 6-digit code; the recipient/domain are
    server-derived (the caller's own account email), never request-supplied (R-sec-3)."""
    identity = await _get_owner_identity(request, session)
    settings = request.app.state.settings
    await _rate_limit(
        request,
        action="member_verify",
        tenant_id=identity.tenant_id,
        limit=settings.member_verify_rpm,
    )

    use_case = get_member_verify_use_case(request, session)
    try:
        claim = await use_case.execute(
            claim_id=claim_id,
            tenant_id=identity.tenant_id,
            code=body.code,
            caller_email=identity.email,
        )
    except DomainClaimNotFoundError:
        raise DOMAIN_CLAIM_NOT_FOUND.exc() from None
    except DomainClaimNotPendingError:
        raise DOMAIN_CLAIM_NOT_PENDING.exc() from None
    except MemberVerifyDomainMismatchError:
        raise MEMBER_VERIFY_DOMAIN_MISMATCH.exc() from None
    except MemberVerifyCodeExpiredError:
        raise MEMBER_VERIFY_CODE_EXPIRED.exc() from None
    except MemberVerifyTooManyAttemptsError:
        raise MEMBER_VERIFY_TOO_MANY_ATTEMPTS.exc() from None
    except MemberVerifyCodeInvalidError:
        raise MEMBER_VERIFY_CODE_INVALID.exc() from None
    return to_list_item(claim)


@domain_claims_router.post("/{claim_id}/member-verify/resend", response_model=DomainClaimListItem)
async def resend_member_verify_domain_claim(
    request: Request,
    claim_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainClaimListItem:
    """member-verified-recognition TASK.md §3 M5 (FROZEN @ v1, SECURITY) — OWNER-only,
    per-tenant rate-limited (distinct action). Body is intentionally unread: the recipient
    is the caller's own server-derived account email (R-sec-3); a fresh code is issued for a
    pending, non-generic, business claim whose domain matches the caller's own email domain."""
    identity = await _get_owner_identity(request, session)
    settings = request.app.state.settings
    await _rate_limit(
        request,
        action="member_verify_resend",
        tenant_id=identity.tenant_id,
        limit=settings.member_verify_resend_rpm,
    )

    tenant = await get_tenant_by_id(session, identity.tenant_id)
    account_type = tenant.account_type if tenant is not None else None

    use_case = get_member_verify_resend_use_case(request, session)
    try:
        claim = await use_case.execute(
            claim_id=claim_id,
            tenant_id=identity.tenant_id,
            caller_email=identity.email,
            account_type=account_type,
        )
    except DomainClaimNotFoundError:
        raise DOMAIN_CLAIM_NOT_FOUND.exc() from None
    except DomainClaimNotPendingError:
        raise DOMAIN_CLAIM_NOT_PENDING.exc() from None
    except MemberVerifyDomainMismatchError:
        raise MEMBER_VERIFY_DOMAIN_MISMATCH.exc() from None
    except DomainGenericError:
        raise DOMAIN_GENERIC.exc() from None
    except MemberVerifyNotEligibleError:
        raise MEMBER_VERIFY_NOT_ELIGIBLE.exc() from None
    return to_list_item(claim)


@domain_claims_router.delete("/{claim_id}/notify", status_code=204)
async def opt_out_domain_claim_notify(
    request: Request,
    claim_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """domain-verify-notify TASK.md §3 M2 (FROZEN @ v1) — clears notify_requested_at;
    idempotent (opting out when not opted in is a no-op success)."""
    identity = await _get_owner_identity(request, session)
    use_case = get_notify_optout_use_case(request, session)
    try:
        await use_case.execute(claim_id=claim_id, tenant_id=identity.tenant_id)
    except DomainClaimNotFoundError:
        raise DOMAIN_CLAIM_NOT_FOUND.exc() from None
