"""FastAPI routers for API key endpoints (contract §3).

Two routers:
  admin_router  — /admin/keys  (JWT-authenticated)
  internal_router — /internal/authz  (X-Api-Key authenticated, no JWT)
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from gateway.core.errors import ProblemError
from gateway.core.ids import uuid7
from gateway.keys.api.deps import (
    get_authz_use_case,
    get_create_key_use_case,
    get_identity,
    get_list_keys_use_case,
    get_revoke_key_use_case,
    require_owner_or_admin,
)
from gateway.keys.api.schemas import (
    AuthzResponse,
    CreateKeyRequest,
    CreateKeyResponse,
    KeyInfoResponse,
)
from gateway.keys.application.use_cases import (
    AuthzUseCase,
    CreateKeyUseCase,
    ListKeysUseCase,
    RevokeKeyUseCase,
)
from gateway.keys.domain.errors import ForbiddenError, InvalidApiKeyError, KeyNotFoundError
from gateway.tenants.domain.entities import Identity

admin_router = APIRouter(prefix="/admin/keys", tags=["api-keys"])
authz_router = APIRouter(tags=["api-keys-internal"])


@admin_router.post("", status_code=201, response_model=CreateKeyResponse)
async def create_key(
    body: CreateKeyRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[CreateKeyUseCase, Depends(get_create_key_use_case)],
) -> CreateKeyResponse:
    """Issue a new API key for the caller's tenant.

    The plaintext key is returned EXACTLY ONCE in this response and never stored.
    """
    key_id: uuid.UUID = uuid7()
    result = await use_case.execute(
        tenant_id=identity.tenant_id,
        name=body.name,
        key_id=key_id,
    )
    return CreateKeyResponse(key_id=result.key_id, name=result.name, key=result.key)


@admin_router.get("", response_model=list[KeyInfoResponse])
async def list_keys(
    identity: Annotated[Identity, Depends(get_identity)],
    use_case: Annotated[ListKeysUseCase, Depends(get_list_keys_use_case)],
) -> list[KeyInfoResponse]:
    """List all API keys for the caller's tenant — secrets and hashes never included."""
    items = await use_case.execute(tenant_id=identity.tenant_id)
    return [
        KeyInfoResponse(
            key_id=item.key_id,
            name=item.name,
            prefix=item.prefix,
            created_at=item.created_at,
            revoked_at=item.revoked_at,
        )
        for item in items
    ]


@admin_router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[RevokeKeyUseCase, Depends(get_revoke_key_use_case)],
) -> None:
    """Soft-revoke a key; returns 404 for unknown or cross-tenant key (no leak)."""
    try:
        await use_case.execute(
            key_id=key_id,
            tenant_id=identity.tenant_id,
            role=identity.role,
        )
    except ForbiddenError:
        raise ProblemError(
            403, "ERR_AUTH_FORBIDDEN", "Insufficient role for this operation"
        ) from None
    except KeyNotFoundError:
        raise ProblemError(404, "ERR_KEY_NOT_FOUND", "Key not found") from None


@authz_router.post("/internal/authz", response_model=AuthzResponse)
async def authz(
    request: Request,
    use_case: Annotated[AuthzUseCase, Depends(get_authz_use_case)],
) -> AuthzResponse:
    """Validate an API key from the X-Api-Key header.

    Returns 200 {tenant_id, key_id} on success.
    Returns 401 ERR_AUTH_INVALID_KEY on any failure — IDENTICAL response body
    for all failure modes (malformed / unknown / revoked / wrong secret).
    No detail is exposed that would help enumerate valid key_ids.
    """
    raw_key = request.headers.get("X-Api-Key", "")
    try:
        result = await use_case.execute(raw_key)
    except InvalidApiKeyError:
        raise ProblemError(401, "ERR_AUTH_INVALID_KEY", "Invalid API key") from None
    return AuthzResponse(tenant_id=result.tenant_id, key_id=result.key_id)
