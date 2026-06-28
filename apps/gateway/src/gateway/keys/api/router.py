"""FastAPI routers for API key endpoints (contract §3).

Two routers:
  admin_router  — /admin/keys  (JWT-authenticated)
  internal_router — /internal/authz  (X-Api-Key or Authorization: Bearer authenticated, no JWT)
"""

import asyncio
import datetime as dt
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.error_catalog import (
    AUTH_FORBIDDEN,
    AUTH_KEY_INVALID_AUTHZ,
    KEY_NOT_FOUND,
    PAYLOAD_EXPIRES_AT_INVALID,
    TEAM_NOT_FOUND,
)
from gateway.core.ids import uuid7
from gateway.keys.api.deps import (
    get_authz_authenticator,
    get_create_key_use_case,
    get_identity,
    get_list_keys_use_case,
    get_revoke_key_use_case,
    get_rotate_key_use_case,
    get_update_key_use_case,
    require_owner_or_admin,
)
from gateway.keys.api.schemas import (
    AuthzResponse,
    CreateKeyRequest,
    CreateKeyResponse,
    KeyInfoResponse,
    PatchKeyRequest,
    PlaygroundTokenResponse,
    RotateKeyRequest,
    RotateKeyResponse,
)
from gateway.keys.application.use_cases import (
    CreateKeyUseCase,
    ListKeysUseCase,
    RevokeKeyUseCase,
    RotateKeyUseCase,
    UpdateKeyUseCase,
)
from gateway.keys.domain.errors import ForbiddenError, InvalidApiKeyError, KeyNotFoundError
from gateway.proxy.infrastructure.composite_key_authenticator import CompositeKeyAuthenticator
from gateway.teams.api.deps import get_team_repository
from gateway.teams.infrastructure.repository import SqlAlchemyTeamRepository
from gateway.tenants.domain.entities import Identity

admin_router = APIRouter(prefix="/admin/keys", tags=["api-keys"])
authz_router = APIRouter(tags=["api-keys-internal"])


def _decimal_or_none(s: str | None) -> Decimal | None:
    return Decimal(s) if s is not None else None


def _datetime_or_none(s: str | None) -> dt.datetime | None:
    if s is None:
        return None
    # Parse ISO-8601 UTC string
    try:
        parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return parsed.astimezone(dt.UTC).replace(tzinfo=dt.UTC)
    except ValueError:
        raise PAYLOAD_EXPIRES_AT_INVALID.exc(value=s) from None


def _fmt_expires(ts: dt.datetime | None) -> str | None:
    if ts is None:
        return None
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


@admin_router.post("", status_code=201, response_model=CreateKeyResponse)
async def create_key(
    request: Request,
    body: CreateKeyRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[CreateKeyUseCase, Depends(get_create_key_use_case)],
    team_repo: Annotated[SqlAlchemyTeamRepository, Depends(get_team_repository)],
) -> CreateKeyResponse:
    """Issue a new API key for the caller's tenant.

    The plaintext key is returned EXACTLY ONCE in this response and never stored.
    """
    # Validate team attribution: team_id must belong to the caller's tenant
    if body.team_id is not None:
        if not await team_repo.get_team_for_tenant(body.team_id, identity.tenant_id):
            raise TEAM_NOT_FOUND.exc()

    key_id: uuid.UUID = uuid7()
    result = await use_case.execute(
        tenant_id=identity.tenant_id,
        name=body.name,
        key_id=key_id,
        monthly_budget_usd=_decimal_or_none(body.monthly_budget_usd),
        soft_budget_usd=_decimal_or_none(body.soft_budget_usd),
        expires_at=_datetime_or_none(body.expires_at),
        model_allowlist=body.model_allowlist,
        rpm_limit=body.rpm_limit,
        tpm_limit=body.tpm_limit,
        team_id=body.team_id,
        cache_enabled=body.cache_enabled,
    )

    # Audit emit — fail-open fire-and-forget; key ID only, NEVER the secret material
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="key.create",
                target_type="api_key",
                target_id=str(result.key_id),
                result="success",
                metadata={"key_name": result.name},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return CreateKeyResponse(
        key_id=result.key_id,
        name=result.name,
        key=result.key,
        monthly_budget_usd=(
            str(result.monthly_budget_usd) if result.monthly_budget_usd is not None else None
        ),
        soft_budget_usd=(
            str(result.soft_budget_usd) if result.soft_budget_usd is not None else None
        ),
        expires_at=_fmt_expires(result.expires_at),
        model_allowlist=result.model_allowlist,
        rpm_limit=result.rpm_limit,
        tpm_limit=result.tpm_limit,
        team_id=result.team_id,
        cache_enabled=result.cache_enabled,
    )


# Defensive defaults if settings are not attached to app.state (mirrors deps.py).
_DEFAULT_PLAYGROUND_TTL_SECONDS = 1800
_DEFAULT_PLAYGROUND_BUDGET_USD = Decimal("5.00")


@admin_router.post("/playground-token", status_code=201, response_model=PlaygroundTokenResponse)
async def issue_playground_token(
    request: Request,
    identity: Annotated[Identity, Depends(get_identity)],
    use_case: Annotated[CreateKeyUseCase, Depends(get_create_key_use_case)],
) -> PlaygroundTokenResponse:
    """Mint a SHORT-LIVED, spend-capped sk- key for the dashboard BFF's server-side
    /v1 calls (browser session JWT → server-side data-plane token exchange).

    Gated on authentication ALONE (any tenant role) — unlike POST /admin/keys
    (owner/admin) — because the playground is a core member feature; risk is bounded
    by the short TTL + per-key monthly cap + the existing tenant-budget governance.
    The plaintext secret is returned ONCE; the BFF holds it server-side until expiry
    and never sends it to the browser.
    """
    settings = getattr(request.app.state, "settings", None)
    ttl_seconds = int(
        getattr(settings, "playground_token_ttl_seconds", _DEFAULT_PLAYGROUND_TTL_SECONDS)
    )
    budget_usd = getattr(settings, "playground_token_budget_usd", _DEFAULT_PLAYGROUND_BUDGET_USD)

    key_id: uuid.UUID = uuid7()
    expires_at = datetime.now(UTC) + dt.timedelta(seconds=ttl_seconds)
    result = await use_case.execute(
        tenant_id=identity.tenant_id,
        name=f"playground:{identity.user_id}",
        key_id=key_id,
        monthly_budget_usd=budget_usd,
        expires_at=expires_at,
    )

    # Audit emit — fail-open fire-and-forget; key ID + purpose only, NEVER the secret.
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="key.create",
                target_type="api_key",
                target_id=str(result.key_id),
                result="success",
                metadata={"key_name": result.name, "purpose": "playground"},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return PlaygroundTokenResponse(key=result.key, expires_at=_fmt_expires(result.expires_at))


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
            monthly_budget_usd=(
                str(item.monthly_budget_usd) if item.monthly_budget_usd is not None else None
            ),
            soft_budget_usd=(
                str(item.soft_budget_usd) if item.soft_budget_usd is not None else None
            ),
            expires_at=item.expires_at,
            model_allowlist=item.model_allowlist,
            rpm_limit=item.rpm_limit,
            tpm_limit=item.tpm_limit,
            team_id=item.team_id,
            cache_enabled=item.cache_enabled,
        )
        for item in items
    ]


@admin_router.patch("/{key_id}", response_model=KeyInfoResponse)
async def patch_key(
    key_id: uuid.UUID,
    body: PatchKeyRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[UpdateKeyUseCase, Depends(get_update_key_use_case)],
    team_repo: Annotated[SqlAlchemyTeamRepository, Depends(get_team_repository)],
) -> KeyInfoResponse:
    """Update governance fields on an active key.

    All body fields are optional — omit means no change.
    Returns 404 for revoked or cross-tenant keys (no information leak).
    """
    # For PATCH: field present+null = clear; field absent = no-change.
    # model_fields_set contains only the fields that were present in the JSON body.
    fields_to_clear: set[str] = set()
    new_monthly: Decimal | None = None
    new_soft: Decimal | None = None
    new_expires: dt.datetime | None = None
    new_allowlist: list[str] | None = None
    new_rpm: int | None = None
    new_tpm: int | None = None
    new_team_id: uuid.UUID | None = None
    new_cache_enabled: bool | None = None

    if "monthly_budget_usd" in body.model_fields_set:
        if body.monthly_budget_usd is None:
            fields_to_clear.add("monthly_budget_usd")
        else:
            new_monthly = _decimal_or_none(body.monthly_budget_usd)

    if "soft_budget_usd" in body.model_fields_set:
        if body.soft_budget_usd is None:
            fields_to_clear.add("soft_budget_usd")
        else:
            new_soft = _decimal_or_none(body.soft_budget_usd)

    if "expires_at" in body.model_fields_set:
        if body.expires_at is None:
            fields_to_clear.add("expires_at")
        else:
            new_expires = _datetime_or_none(body.expires_at)

    if "model_allowlist" in body.model_fields_set:
        if body.model_allowlist is None:
            fields_to_clear.add("model_allowlist")
        else:
            new_allowlist = body.model_allowlist

    if "rpm_limit" in body.model_fields_set:
        if body.rpm_limit is None:
            fields_to_clear.add("rpm_limit")
        else:
            new_rpm = body.rpm_limit

    if "tpm_limit" in body.model_fields_set:
        if body.tpm_limit is None:
            fields_to_clear.add("tpm_limit")
        else:
            new_tpm = body.tpm_limit

    # team_id PATCH: absent = no change; null = clear; UUID = set (validate ownership)
    if "team_id" in body.model_fields_set:
        if body.team_id is None:
            fields_to_clear.add("team_id")
        else:
            # Validate team belongs to caller's tenant (404, not 403 — no existence leak)
            if not await team_repo.get_team_for_tenant(body.team_id, identity.tenant_id):
                raise TEAM_NOT_FOUND.exc()
            new_team_id = body.team_id

    # cache_enabled PATCH: absent = no change; True/False = set
    if "cache_enabled" in body.model_fields_set:
        new_cache_enabled = body.cache_enabled

    try:
        updated = await use_case.execute(
            key_id=key_id,
            tenant_id=identity.tenant_id,
            role=identity.role,
            monthly_budget_usd=new_monthly,
            soft_budget_usd=new_soft,
            expires_at=new_expires,
            model_allowlist=new_allowlist,
            rpm_limit=new_rpm,
            tpm_limit=new_tpm,
            team_id=new_team_id,
            cache_enabled=new_cache_enabled,
            _fields_to_clear=fields_to_clear,
        )
    except ForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except KeyNotFoundError:
        raise KEY_NOT_FOUND.exc() from None

    return KeyInfoResponse(
        key_id=updated.id,
        name=updated.name,
        prefix=f"sk-{updated.id.hex[:8]}",
        created_at=updated.created_at,
        revoked_at=updated.revoked_at,
        monthly_budget_usd=(
            str(updated.monthly_budget_usd) if updated.monthly_budget_usd is not None else None
        ),
        soft_budget_usd=(
            str(updated.soft_budget_usd) if updated.soft_budget_usd is not None else None
        ),
        expires_at=updated.expires_at,
        model_allowlist=updated.model_allowlist,
        rpm_limit=updated.rpm_limit,
        tpm_limit=updated.tpm_limit,
        team_id=updated.team_id,
        cache_enabled=updated.cache_enabled,
    )


@admin_router.post("/{key_id}/rotate", status_code=201, response_model=RotateKeyResponse)
async def rotate_key(
    request: Request,
    key_id: uuid.UUID,
    body: RotateKeyRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    use_case: Annotated[RotateKeyUseCase, Depends(get_rotate_key_use_case)],
) -> RotateKeyResponse:
    """Rotate an API key: revoke old, issue new atomically.

    Fields not supplied in the body are inherited from the old key.
    Returns 201 with the new plaintext key (shown once).
    Returns 403 for member-role callers.
    Returns 404 for revoked or cross-tenant keys.
    """
    # Detect which fields were explicitly provided vs. absent (for inherit semantics)
    budget_provided = "monthly_budget_usd" in body.model_fields_set
    soft_provided = "soft_budget_usd" in body.model_fields_set
    expires_provided = "expires_at" in body.model_fields_set
    allowlist_provided = "model_allowlist" in body.model_fields_set

    try:
        result = await use_case.execute(
            old_key_id=key_id,
            tenant_id=identity.tenant_id,
            role=identity.role,
            monthly_budget_usd=(
                _decimal_or_none(body.monthly_budget_usd) if budget_provided else None
            ),
            soft_budget_usd=(_decimal_or_none(body.soft_budget_usd) if soft_provided else None),
            expires_at=_datetime_or_none(body.expires_at) if expires_provided else None,
            model_allowlist=body.model_allowlist if allowlist_provided else None,
            _budget_provided=budget_provided,
            _soft_budget_provided=soft_provided,
            _expires_provided=expires_provided,
            _allowlist_provided=allowlist_provided,
        )
    except ForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except KeyNotFoundError:
        raise KEY_NOT_FOUND.exc() from None

    # Audit emit — fail-open fire-and-forget; key IDs only, NEVER the secret material
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="key.rotate",
                target_type="api_key",
                target_id=str(result.new_key_id),
                result="success",
                metadata={
                    "superseded_key_id": str(result.superseded_key_id),
                    "key_name": result.name,
                },
                created_at=datetime.now(UTC),
            ),
        )
    )

    return RotateKeyResponse(
        new_key_id=result.new_key_id,
        superseded_key_id=result.superseded_key_id,
        key=result.key,
        name=result.name,
        monthly_budget_usd=(
            str(result.monthly_budget_usd) if result.monthly_budget_usd is not None else None
        ),
        soft_budget_usd=(
            str(result.soft_budget_usd) if result.soft_budget_usd is not None else None
        ),
        expires_at=_fmt_expires(result.expires_at),
        model_allowlist=result.model_allowlist,
    )


@admin_router.delete("/{key_id}", status_code=204)
async def revoke_key(
    request: Request,
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
        raise AUTH_FORBIDDEN.exc() from None
    except KeyNotFoundError:
        raise KEY_NOT_FOUND.exc() from None

    # Audit emit — fail-open fire-and-forget
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="key.revoke",
                target_type="api_key",
                target_id=str(key_id),
                result="success",
                metadata={},
                created_at=datetime.now(UTC),
            ),
        )
    )


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
    authenticator: Annotated[CompositeKeyAuthenticator, Depends(get_authz_authenticator)],
) -> AuthzResponse:
    """Validate a credential from Authorization: Bearer or X-Api-Key header.

    Accepts both sk- API keys (existing path, byte-identical) and minted agent tokens
    (widened by CompositeKeyAuthenticator — closes the headless-agent edge gap).

    Priority: Authorization: Bearer is evaluated first; if present, X-Api-Key is ignored.

    Returns 200 {tenant_id, key_id} on success.
    Response headers x-tenant-id and x-key-id are set for Envoy ext_authz
    allowed_upstream_headers forwarding.

    Returns 401 ERR_AUTH_INVALID_KEY on any failure — IDENTICAL response body
    for all failure modes (malformed / unknown / revoked / wrong secret / missing header).
    No detail is exposed that would help enumerate valid credential ids.
    Raw token is NEVER logged.
    """
    raw_key = _extract_raw_key(request)
    try:
        result = await authenticator.authenticate(raw_key)
    except InvalidApiKeyError:
        raise AUTH_KEY_INVALID_AUTHZ.exc() from None
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
    authenticator: Annotated[CompositeKeyAuthenticator, Depends(get_authz_authenticator)],
) -> AuthzResponse:
    """Envoy ext_authz entry point: path_prefix appends the ORIGINAL request path.

    Envoy's ext_authz HTTP service ignores the server_uri path and sends the
    check request to path_prefix + original path (e.g. /internal/authz/v1/chat/
    completions), mirroring the original method. This additive route accepts
    any subpath and delegates to the exact same authz semantics. Never reachable
    from outside: Envoy serves /internal/* as a 403 direct response at the edge.
    """
    return await authz(request, response, authenticator)
