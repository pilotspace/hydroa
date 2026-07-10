from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_CREDENTIALS_INVALID,
    AUTH_EMAIL_TAKEN,
    AUTH_PASSWORD_WEAK,
    AUTH_TOKEN_INVALID,
    SIGNUP_INVITE_ONLY,
)
from gateway.domain_capture.api.deps import get_domain_claim_resolver, get_join_tenant_use_case
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
    InvalidCredentialsError,
    InvalidTokenError,
    WeakPasswordError,
)

router = APIRouter(prefix="/admin/auth", tags=["tenant-identity"])


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
            tenant_name=body.tenant_name, email=body.email, password=body.password
        )
    except WeakPasswordError:
        raise AUTH_PASSWORD_WEAK.exc() from None
    except EmailAlreadyRegisteredError:
        raise AUTH_EMAIL_TAKEN.exc() from None
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
) -> MeResponse:
    try:
        identity = await use_case.execute(token)
    except InvalidTokenError:
        raise AUTH_TOKEN_INVALID.exc() from None
    return MeResponse(
        user_id=identity.user_id,
        tenant_id=identity.tenant_id,
        email=identity.email,
        role=str(identity.role),
    )
