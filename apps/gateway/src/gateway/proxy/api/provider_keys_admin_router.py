"""Admin API for per-tenant provider credentials — the BYOK WRITE path.

Routes (OWNER-only, prefix /admin/provider-keys):
  PUT    /admin/provider-keys/{provider}  — create-or-replace (upsert) a credential
  GET    /admin/provider-keys             — list configured providers (no secrets)
  GET    /admin/provider-keys/{provider}  — one provider's status (no secrets); 404 if absent
  DELETE /admin/provider-keys/{provider}  — remove a credential; 204, or 404 if absent

§3 CONTRACT (provider-config-admin-api TASK.md) — FROZEN @ v1.

SECURITY INVARIANTS:
  - OWNER-only: tenant_id comes from the verified JWT, never a body/query param, so
    cross-tenant access is architecturally impossible.
  - Secrets are accepted as plaintext in the REQUEST only; they are persisted Fernet-
    encrypted by the task-1 store and NEVER returned in any response (ProviderKeyStatus
    carries no secret field) and NEVER logged.
  - The provider-discriminated body is built into the frozen ProviderCredential value-
    object; the value-object's @model_validator is the completeness gate. Any validation
    failure is mapped to 422 ERR_PROVIDER_CREDENTIAL_INCOMPLETE with ``from None`` so no
    field value leaks via exception chaining.
"""

from __future__ import annotations

import asyncio
import uuid as _uuid_mod
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.egress_policy import EgressDeniedError, assert_literal_host_not_denied
from gateway.core.error_catalog import (
    AUTH_FORBIDDEN_OWNER_REQUIRED,
    AUTH_TOKEN_INVALID,
    INTERNAL_ERROR,
    PROVIDER_CREDENTIAL_INCOMPLETE,
    PROVIDER_ENDPOINT_FORBIDDEN,
    PROVIDER_KEY_ENCRYPTION_UNAVAILABLE,
    PROVIDER_KEY_NOT_FOUND,
    PROVIDER_UNKNOWN,
)
from gateway.proxy.domain.provider_credentials import (
    BYOK_PROVIDERS,
    AzureCredential,
    BearerCredential,
    BedrockCredential,
    ProviderCredential,
    ProviderCredentialError,
    ProviderKeyStatus,
)
from gateway.tenants.api.deps import get_bearer_token
from gateway.tenants.application.use_cases import GetIdentityUseCase
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.infrastructure.impersonation_session_guard import DbImpersonationSessionGuard

provider_keys_admin_router = APIRouter(prefix="/admin/provider-keys", tags=["provider-keys-admin"])

#: Bearer-auth providers — a single shared secret each.
_BEARER_PROVIDERS: frozenset[str] = frozenset(
    {"openrouter", "openai", "anthropic", "google", "minimax"}
)


class ProviderKeyPutBody(BaseModel):
    """Provider-discriminated PUT body — every provider field is optional.

    Completeness is NOT enforced here on purpose: required fields are validated by
    constructing the frozen ProviderCredential value-object (whose @model_validator
    raises). This keeps a single source of truth and surfaces a stable
    ERR_PROVIDER_CREDENTIAL_INCOMPLETE rather than FastAPI's generic 422.
    """

    # bearer
    secret: str | None = None
    # bedrock
    access_key_id: str | None = None
    secret_access_key: str | None = None
    region: str | None = None
    session_token: str | None = None
    # azure (shared)
    mode: str | None = None
    endpoint: str | None = None
    api_version: str | None = None
    deployment_map: dict[str, str] | None = None
    # azure api_key
    api_key: str | None = None
    # azure aad
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None
    authority: str | None = None
    # common
    enabled: bool = True


async def _require_owner_identity(request: Request, session: AsyncSession) -> Identity:
    """Resolve the caller's full Identity from the verified JWT, enforcing OWNER role.

    Raises 401 ERR_AUTH_INVALID_TOKEN (missing/malformed/invalid token) or
    403 ERR_AUTH_FORBIDDEN (authenticated non-owner).

    impersonation-live-session-guard TASK.md §3 Part D.5 — one of GetIdentityUseCase's
    3 direct-construction call sites.
    """

    token = get_bearer_token(request)  # raises AUTH_TOKEN_MISSING (401) when absent
    use_case = GetIdentityUseCase(
        request.app.state.token_service,
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


async def _require_owner_tenant_id(request: Request, session: AsyncSession) -> UUID:
    """Resolve the caller's tenant_id from the verified JWT, enforcing OWNER role.

    Raises 401 ERR_AUTH_INVALID_TOKEN (missing/malformed/invalid token) or
    403 ERR_AUTH_FORBIDDEN (authenticated non-owner).
    """
    identity = await _require_owner_identity(request, session)
    return identity.tenant_id


def _build_credential(provider: str, body: ProviderKeyPutBody) -> ProviderCredential:
    """Construct the frozen value-object for *provider* from the flat request body.

    Raises ``pydantic.ValidationError`` / ``ValueError`` when the body is incomplete
    for the provider (and mode); the caller maps that to 422.
    """
    if provider in _BEARER_PROVIDERS:
        return BearerCredential(secret=SecretStr(body.secret or ""))

    if provider == "bedrock":
        return BedrockCredential(
            access_key_id=body.access_key_id or "",
            secret_access_key=SecretStr(body.secret_access_key or ""),
            region=body.region or "",
            session_token=SecretStr(body.session_token) if body.session_token else None,
        )

    # azure — api_key OR aad, discriminated by ``mode``
    common: dict[str, object] = {
        "endpoint": body.endpoint or "",
        "deployment_map": body.deployment_map or {},
    }
    if body.api_version is not None:
        common["api_version"] = body.api_version

    if body.mode == "api_key":
        return AzureCredential(
            mode="api_key",
            api_key=SecretStr(body.api_key) if body.api_key is not None else None,
            **common,  # type: ignore[arg-type]
        )
    if body.mode == "aad":
        if body.scope is not None:
            common["scope"] = body.scope
        if body.authority is not None:
            common["authority"] = body.authority
        return AzureCredential(
            mode="aad",
            tenant_id=body.tenant_id,
            client_id=body.client_id,
            client_secret=(
                SecretStr(body.client_secret) if body.client_secret is not None else None
            ),
            **common,  # type: ignore[arg-type]
        )
    # Missing or invalid mode → an invalid azure body.
    raise ValueError("ERR_PROVIDER_CREDENTIAL_INCOMPLETE")


async def _status_for(request: Request, tenant_id: UUID, provider: str) -> ProviderKeyStatus | None:
    """Return the ProviderKeyStatus for *provider* from the store's list view, or None."""
    store = request.app.state.tenant_provider_key_store
    statuses: list[ProviderKeyStatus] = await store.list(tenant_id)
    for status in statuses:
        if status.provider == provider:
            return status
    return None


@provider_keys_admin_router.put("/{provider}")
async def put_provider_key(
    provider: str,
    body: ProviderKeyPutBody,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderKeyStatus:
    """Create or replace (upsert) the caller-tenant's credential for *provider*."""
    identity = await _require_owner_identity(request, session)
    tenant_id = identity.tenant_id
    if provider not in BYOK_PROVIDERS:
        raise PROVIDER_UNKNOWN.exc()

    try:
        credential = _build_credential(provider, body)
    except (ValidationError, ValueError):
        # ``from None`` strips the chain so no field value leaks via exception chaining.
        raise PROVIDER_CREDENTIAL_INCOMPLETE.exc() from None

    # S3 SSRF/IMDS/credential-exfiltration write-time guard (edge-input-hardening TASK.md
    # §3 Part B) — a cheap, DNS-free literal-IP check on the Azure endpoint/authority BEFORE
    # persistence. A non-IP hostname always passes here (DNS deferred to request time); this
    # is the write-time first filter, not the authoritative layer.
    if provider == "azure":
        settings = request.app.state.settings
        try:
            assert_literal_host_not_denied(
                body.endpoint or "",
                allow_private_ranges=settings.egress_allow_private_ranges,
                allow_http=settings.egress_allow_http_dev,
            )
            if body.authority:
                assert_literal_host_not_denied(
                    body.authority,
                    allow_private_ranges=settings.egress_allow_private_ranges,
                    allow_http=settings.egress_allow_http_dev,
                )
        except EgressDeniedError:
            raise PROVIDER_ENDPOINT_FORBIDDEN.exc() from None

    store = request.app.state.tenant_provider_key_store
    try:
        await store.upsert(tenant_id, provider, credential, enabled=body.enabled)
    except ProviderCredentialError as exc:
        # Map the store's domain errors to RFC-9457 problems instead of a raw 500
        # (design-for-failure on the store IO path). The code carries no secret; `from None`
        # keeps the project secret-chain floor. ERR_PROVIDER_UNKNOWN is unreachable here
        # (guarded above) but mapped defensively.
        if exc.code == "ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE":
            raise PROVIDER_KEY_ENCRYPTION_UNAVAILABLE.exc() from None
        if exc.code == "ERR_PROVIDER_UNKNOWN":
            raise PROVIDER_UNKNOWN.exc() from None
        raise INTERNAL_ERROR.exc() from None

    status = await _status_for(request, tenant_id, provider)
    if status is None:  # pragma: no cover — defensive: a row was just upserted
        raise INTERNAL_ERROR.exc()

    # Audit emit — fail-open fire-and-forget; provider name only, NEVER secret material
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=_uuid_mod.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="provider_key.put",
                target_type="provider",
                target_id=provider,
                result="success",
                metadata={"provider": provider, "enabled": body.enabled},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return status


@provider_keys_admin_router.get("")
async def list_provider_keys(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, list[ProviderKeyStatus]]:
    """List the caller-tenant's configured providers (no secrets; [] when none)."""
    tenant_id = await _require_owner_tenant_id(request, session)
    store = request.app.state.tenant_provider_key_store
    statuses: list[ProviderKeyStatus] = await store.list(tenant_id)
    return {"keys": statuses}


@provider_keys_admin_router.get("/{provider}")
async def get_provider_key(
    provider: str, request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> ProviderKeyStatus:
    """Return one provider's status (no secret); 404 when not configured."""
    tenant_id = await _require_owner_tenant_id(request, session)
    if provider not in BYOK_PROVIDERS:
        raise PROVIDER_UNKNOWN.exc()
    status = await _status_for(request, tenant_id, provider)
    if status is None:
        raise PROVIDER_KEY_NOT_FOUND.exc()
    return status


@provider_keys_admin_router.delete("/{provider}", status_code=204)
async def delete_provider_key(
    provider: str, request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Response:
    """Delete the caller-tenant's credential for *provider*; 204, or 404 if absent."""
    tenant_id = await _require_owner_tenant_id(request, session)
    if provider not in BYOK_PROVIDERS:
        raise PROVIDER_UNKNOWN.exc()
    store = request.app.state.tenant_provider_key_store
    removed: bool = await store.delete(tenant_id, provider)
    if not removed:
        raise PROVIDER_KEY_NOT_FOUND.exc()
    return Response(status_code=204)
