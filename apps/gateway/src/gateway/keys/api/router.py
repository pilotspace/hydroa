"""FastAPI routers for API key endpoints (contract §3).

Two routers:
  admin_router  — /admin/keys  (JWT-authenticated)
  internal_router — /internal/authz  (X-Api-Key or Authorization: Bearer authenticated, no JWT)
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

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


def _extract_raw_key(request: Request) -> str:
    """Extract the raw API key from the request.

    Priority (contract §3):
      1. Authorization: Bearer <raw-key>  — checked first; X-Api-Key ignored if present
      2. X-Api-Key: <raw-key>             — fallback when no Authorization header

    Only the "Bearer" scheme is accepted in Authorization.
    Any other scheme (Basic, Token, …) is treated as missing and falls through to X-Api-Key.
    Returns an empty string when neither header is present (use case raises InvalidApiKeyError).
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header:
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() == "bearer":
            # Bearer present — use the token value (may be empty/malformed; use case validates)
            return token
        # Non-Bearer scheme: treat as if Authorization header was absent; fall through
    return request.headers.get("X-Api-Key", "")


@authz_router.post("/internal/authz", response_model=AuthzResponse)
async def authz(
    request: Request,
    response: Response,
    use_case: Annotated[AuthzUseCase, Depends(get_authz_use_case)],
) -> AuthzResponse:
    """Validate an API key from Authorization: Bearer or X-Api-Key header.

    Priority: Authorization: Bearer is evaluated first; if present, X-Api-Key is ignored.
    The Bearer value is the same raw key string as X-Api-Key ("sk-<hex>.<secret>").

    Returns 200 {tenant_id, key_id} on success.
    Response headers x-tenant-id and x-key-id are set for Envoy ext_authz
    allowed_upstream_headers forwarding.

    Returns 401 ERR_AUTH_INVALID_KEY on any failure — IDENTICAL response body
    for all failure modes (malformed / unknown / revoked / wrong secret / missing header).
    No detail is exposed that would help enumerate valid key_ids.
    """
    raw_key = _extract_raw_key(request)
    try:
        result = await use_case.execute(raw_key)
    except InvalidApiKeyError:
        raise ProblemError(401, "ERR_AUTH_INVALID_KEY", "Invalid API key") from None
    # Set response headers for Envoy ext_authz allowed_upstream_headers forwarding
    response.headers["x-tenant-id"] = str(result.tenant_id)
    response.headers["x-key-id"] = str(result.key_id)
    return AuthzResponse(tenant_id=result.tenant_id, key_id=result.key_id)


@authz_router.api_route(
    "/internal/authz/{_subpath:path}",
    methods=["GET", "POST"],
    response_model=AuthzResponse,
    include_in_schema=False,
)
async def authz_subpath(
    _subpath: str,
    request: Request,
    response: Response,
    use_case: Annotated[AuthzUseCase, Depends(get_authz_use_case)],
) -> AuthzResponse:
    """Envoy ext_authz entry point: path_prefix appends the ORIGINAL request path.

    Envoy's ext_authz HTTP service ignores the server_uri path and sends the
    check request to path_prefix + original path (e.g. /internal/authz/v1/chat/
    completions), mirroring the original method. This additive route accepts
    any subpath and delegates to the exact same authz semantics. Never reachable
    from outside: Envoy serves /internal/* as a 403 direct response at the edge.
    """
    return await authz(request, response, use_case)
