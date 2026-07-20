import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_CREDENTIALS_INVALID,
    AUTH_EMAIL_TAKEN,
    AUTH_PASSWORD_WEAK,
    AUTH_TOKEN_INVALID,
    PLAN_SEAT_CAP_EXCEEDED,
    SIGNUP_INVITE_ONLY,
    SIGNUP_PLAN_UNPROVISIONED,
)
from gateway.domain_capture.api.deps import (
    get_domain_claim_resolver,
    get_issue_member_verify_code_use_case,
    get_join_tenant_use_case,
)
from gateway.tenants.api.deps import (
    get_bearer_token,
    get_identity_use_case,
    get_login_use_case,
    get_signup_use_case,
)
from gateway.tenants.api.schemas import (
    LoginRequest,
    LoginResponse,
    MeResponse,
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
    SeatCapExceededError,
    WeakPasswordError,
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
) -> SignupResponse:
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
