import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_CREDENTIALS_INVALID,
    AUTH_EMAIL_TAKEN,
    AUTH_PASSWORD_WEAK,
    AUTH_TOKEN_INVALID,
    PLAN_SEAT_CAP_EXCEEDED,
    RATE_LIMITED,
    SIGNUP_CONFIRM_EXPIRED,
    SIGNUP_CONFIRM_INVALID,
    SIGNUP_INVITE_ONLY,
    SIGNUP_PLAN_UNPROVISIONED,
)
from gateway.core.net import resolve_trusted_client_ip
from gateway.domain_capture.api.deps import (
    get_domain_claim_resolver,
    get_issue_member_verify_code_use_case,
    get_join_tenant_use_case,
)
from gateway.tenants.api.deps import (
    get_bearer_token,
    get_confirm_pending_signup_use_case,
    get_identity_use_case,
    get_issue_pending_signup_use_case,
    get_login_use_case,
    get_signup_use_case,
)
from gateway.tenants.api.schemas import (
    ConfirmSignupRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    SignupPendingResponse,
    SignupRequest,
    SignupResponse,
)
from gateway.tenants.application.use_cases import (
    GetIdentityUseCase,
    LoginUseCase,
    SignupUseCase,
)
from gateway.tenants.domain.errors import (
    EmailAlreadyRegisteredError,
    IndividualPlanMissingError,
    InvalidCredentialsError,
    InvalidTokenError,
    PendingSignupExpiredError,
    PendingSignupNotFoundError,
    SeatCapExceededError,
    WeakPasswordError,
)
from gateway.tenants.infrastructure.invite_public_rate_limiter import (
    InvitePublicRateLimiter,
    InviteRateLimitedError,
)
from gateway.tenants.infrastructure.repository import get_tenant_by_id

router = APIRouter(prefix="/admin/auth", tags=["tenant-identity"])

_log = logging.getLogger(__name__)


@router.post("/signup", status_code=201, response_model=SignupResponse)
async def signup(
    request: Request,
    body: SignupRequest,
    use_case: Annotated[SignupUseCase, Depends(get_signup_use_case)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SignupResponse | JSONResponse:
    # domain-capture TASK.md §3 M8 (FROZEN @ v1, disclosed amendment to S1 M2's own "zero
    # DB IO when disabled" property — Tin-confirmed at freeze): the verified-domain lookup
    # runs BEFORE the public_signup_enabled check below, so a tenant OWNER's proven domain
    # relaxes an otherwise invite-only deployment for THAT domain only. A pending/expired/
    # revoked/absent claim is indistinguishable from "no claim" and falls through
    # byte-identically to the existing S1 path (M15).
    domain = body.email.lower().rsplit("@", 1)[-1]
    resolver = get_domain_claim_resolver(request, session)
    claimed_tenant_id = await resolver.resolve_verified_tenant(domain)
    # The lookup above is a bare SELECT, but SQLAlchemy's AsyncSession autobegin means it
    # already opened an implicit transaction on `session`. Both branches below need a
    # CLEAN session to call `session.begin()` themselves (create_tenant_with_owner /
    # join_verified_tenant_domain each wrap their own INSERT in one explicit transaction,
    # mirroring _get_or_provision_sso_user's own documented flush()+commit() workaround for
    # this exact SQLAlchemy autobegin trap) — close the read-only lookup's transaction
    # first so neither raises "A transaction is already begun on this Session."
    await session.rollback()
    if claimed_tenant_id is not None:
        join_use_case = get_join_tenant_use_case(request, session)
        try:
            user_id = await join_use_case.execute(
                tenant_id=claimed_tenant_id, email=body.email, password=body.password
            )
        except WeakPasswordError:
            raise AUTH_PASSWORD_WEAK.exc() from None
        except EmailAlreadyRegisteredError:
            raise AUTH_EMAIL_TAKEN.exc() from None
        except SeatCapExceededError as exc:
            # plan-seat-cap TASK.md §3 (FROZEN @ v1, M5/R3) — a superseding addition to
            # this already-shipped router; the verified-domain auto-join branch (R3).
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
        # body.tenant_name is deliberately never read/persisted on this path (M11) — the
        # target tenant already exists.
        return SignupResponse(
            tenant_id=claimed_tenant_id, user_id=user_id, joined_existing_tenant=True
        )

    # scoped-self-serve-signup TASK.md §3 (FROZEN @ v1, SECURITY) — inserted ahead of the
    # S1 gate below (M14): a personal-tier signup that fell through the verified-domain-
    # claim check above may self-serve via a DEFERRED-creation, rate-limited, uniform-
    # response path when the NEW, narrow public_signup_personal_enabled flag is on. NEVER
    # read anywhere near public_signup_enabled (M1) — business signups (and personal
    # signups with this flag OFF) are entirely unaffected and fall through unchanged to
    # the S1 gate immediately below.
    settings = request.app.state.settings
    if body.account_type == "personal" and settings.public_signup_personal_enabled:
        # M3: rate-limit FIRST, uniform, fail-open — BEFORE any further DB IO, regardless
        # of whether the submitted email is registered. Reuses the EXISTING
        # InvitePublicRateLimiter / app.state.invite_public_limiter with 2 new `action`
        # labels; client IP via the EXISTING resolve_trusted_client_ip (never raw
        # request.client.host).
        limiter: InvitePublicRateLimiter = request.app.state.invite_public_limiter
        client_ip = resolve_trusted_client_ip(request, settings.trusted_proxy_hops)
        normalized_email = body.email.lower()
        try:
            await limiter.check(
                action="personal_signup_ip", key=client_ip, limit=settings.personal_signup_ip_rpm
            )
            await limiter.check(
                action="personal_signup_email",
                key=normalized_email,
                limit=settings.personal_signup_email_rpm,
            )
        except InviteRateLimitedError as exc:
            raise RATE_LIMITED.exc(headers={"Retry-After": str(exc.retry_after)}) from None

        issue_pending_use_case = get_issue_pending_signup_use_case(request, session)
        try:
            await issue_pending_use_case.execute(
                tenant_name=body.tenant_name, email=body.email, password=body.password
            )
        except WeakPasswordError:
            raise AUTH_PASSWORD_WEAK.exc() from None
        except IndividualPlanMissingError:
            raise SIGNUP_PLAN_UNPROVISIONED.exc() from None

        # M7: the uniform anti-enumeration response — IDENTICAL status/body whether the
        # target email was fresh (M8) or already registered (M9). Returned as a bare
        # JSONResponse (bypassing this route's own response_model=SignupResponse /
        # status_code=201 decorator defaults) since this branch's shape and status code
        # are contractually DIFFERENT (202 SignupPendingResponse) from every other branch
        # of this endpoint.
        pending_response = SignupPendingResponse(
            status="pending_verification", email=normalized_email
        )
        return JSONResponse(status_code=202, content=pending_response.model_dump(mode="json"))

    # Invite-only gate (S1): refuse public signup unless explicitly enabled. Checked
    # BEFORE the use case is invoked and before any FURTHER DB IO — so a disabled
    # gateway never leaks email-uniqueness (409) or password-strength (400) signal
    # regardless of body validity, for every domain with no verified claim (byte-identical
    # to the frozen S1 contract).
    if not request.app.state.settings.public_signup_enabled:
        raise SIGNUP_INVITE_ONLY.exc()
    try:
        tenant_id, user_id = await use_case.execute(
            tenant_name=body.tenant_name,
            email=body.email,
            password=body.password,
            account_type=body.account_type,
        )
    except WeakPasswordError:
        raise AUTH_PASSWORD_WEAK.exc() from None
    except EmailAlreadyRegisteredError:
        raise AUTH_EMAIL_TAKEN.exc() from None
    except IndividualPlanMissingError:
        # account-type-discriminator TASK.md §3 (FROZEN @ v1, R3), repointed by
        # plan-tiers-and-base-fee TASK.md §3 M3: a personal signup with the seeded free
        # plan absent is a server misconfiguration — fail loud (500), never a personal
        # tenant silently left unplanned.
        raise SIGNUP_PLAN_UNPROVISIONED.exc() from None

    # member-verified-recognition TASK.md §3 (FROZEN @ v1, SECURITY) — issuance hook on the
    # NEW-tenant BUSINESS branch ONLY. Best-effort / fail-OPEN: any failure (email down,
    # domain already verified by another tenant, DB hiccup) is logged + swallowed so signup
    # ALWAYS returns 201 (M2, R-fail-open). Personal accounts and generic/public domains are
    # never issued a code (the use case silently skips generic; personal is gated here).
    if body.account_type == "business":
        try:
            issue_use_case = get_issue_member_verify_code_use_case(request, session)
            await issue_use_case.execute(
                tenant_id=tenant_id,
                domain=domain,
                created_by_user_id=user_id,
                recipient_email=body.email.lower(),
            )
        except Exception:  # noqa: BLE001 — fail-OPEN: issuance is a convenience, never a signup gate.
            _log.warning(
                "member_verify_issuance_failed (swallowed — fail-open); signup still 201",
                exc_info=True,
                extra={"tenant_id": str(tenant_id)},
            )

    return SignupResponse(tenant_id=tenant_id, user_id=user_id)


@router.post("/signup/confirm", status_code=201, response_model=SignupResponse)
async def signup_confirm(
    request: Request,
    body: ConfirmSignupRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SignupResponse:
    """scoped-self-serve-signup TASK.md §3 (FROZEN @ v1, SECURITY) — M10/M11/M12/M13.

    PUBLIC, no bearer auth — the token is the ONLY credential (delivered solely by
    email). Distinguishing invalid/expired/taken here is safe (R-sec-6): reachable in a
    meaningful way ONLY by whoever already possesses the emailed token, never by an
    anonymous prober of the initial /admin/auth/signup endpoint.
    """
    settings = request.app.state.settings
    # M13: defense-in-depth per-IP rate limit on this endpoint — the 256-bit token space
    # is already brute-force-infeasible; this bounds abuse of the endpoint itself.
    limiter: InvitePublicRateLimiter = request.app.state.invite_public_limiter
    client_ip = resolve_trusted_client_ip(request, settings.trusted_proxy_hops)
    try:
        await limiter.check(
            action="personal_signup_confirm",
            key=client_ip,
            limit=settings.personal_signup_confirm_rpm,
        )
    except InviteRateLimitedError as exc:
        raise RATE_LIMITED.exc(headers={"Retry-After": str(exc.retry_after)}) from None

    confirm_use_case = get_confirm_pending_signup_use_case(request, session)
    try:
        tenant_id, user_id = await confirm_use_case.execute(token=body.token)
    except PendingSignupExpiredError:
        raise SIGNUP_CONFIRM_EXPIRED.exc() from None
    except PendingSignupNotFoundError:
        raise SIGNUP_CONFIRM_INVALID.exc() from None
    except IndividualPlanMissingError:
        raise SIGNUP_PLAN_UNPROVISIONED.exc() from None
    except EmailAlreadyRegisteredError:
        # M12/R7: confirm-time race against an UNRELATED signup path for the same email
        # (safe to reveal here — R-sec-6, the caller is token-possession-authenticated).
        raise AUTH_EMAIL_TAKEN.exc() from None

    return SignupResponse(tenant_id=tenant_id, user_id=user_id, joined_existing_tenant=False)


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    use_case: Annotated[LoginUseCase, Depends(get_login_use_case)],
) -> LoginResponse:
    try:
        token, expires_in = await use_case.execute(email=body.email, password=body.password)
    except InvalidCredentialsError:
        raise AUTH_CREDENTIALS_INVALID.exc() from None
    return LoginResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=MeResponse)
async def me(
    token: Annotated[str, Depends(get_bearer_token)],
    use_case: Annotated[GetIdentityUseCase, Depends(get_identity_use_case)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    try:
        identity = await use_case.execute(token)
    except InvalidTokenError:
        raise AUTH_TOKEN_INVALID.exc() from None
    # domain-claims-console TASK.md §4 CR: expose the caller's OWN tenant name so the
    # dashboard can name the joined workspace. Additive read (a valid session always has
    # a tenant); fall back to "" rather than 500 if the row is missing — /me must not crash.
    tenant = await get_tenant_by_id(session, identity.tenant_id)
    return MeResponse(
        user_id=identity.user_id,
        tenant_id=identity.tenant_id,
        email=identity.email,
        role=str(identity.role),
        tenant_name=tenant.name if tenant is not None else "",
    )
