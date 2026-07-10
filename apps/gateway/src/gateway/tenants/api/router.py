from typing import Annotated

from fastapi import APIRouter, Depends

from gateway.core.error_catalog import (
    AUTH_CREDENTIALS_INVALID,
    AUTH_EMAIL_TAKEN,
    AUTH_PASSWORD_WEAK,
    AUTH_TOKEN_INVALID,
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
    InvalidCredentialsError,
    InvalidTokenError,
    WeakPasswordError,
)

router = APIRouter(prefix="/admin/auth", tags=["tenant-identity"])


@router.post("/signup", status_code=201, response_model=SignupResponse)
async def signup(
    body: SignupRequest,
    use_case: Annotated[SignupUseCase, Depends(get_signup_use_case)],
) -> SignupResponse:
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
