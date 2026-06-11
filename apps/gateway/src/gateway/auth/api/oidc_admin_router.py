"""Admin API for per-tenant OIDC IdP configuration.

Routes (owner-only, prefix /admin/oidc):
  GET  /admin/oidc  — return per-tenant IdP config (client_secret NEVER returned)
  PUT  /admin/oidc  — upsert per-tenant IdP config (Fernet-encrypted at rest)

SECURITY INVARIANTS:
  - client_secret is NEVER returned in GET or PUT responses.
    The response always shows client_secret: "<stored>".
  - client_secret is NEVER logged — not in debug, info, warning, or error lines.
  - Fernet encryption BEFORE any DB INSERT/UPDATE.
  - PUT without GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY → 409.
  - URL validation (https-only + no-private-IP) runs in the handler before DB write.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_FORBIDDEN_OWNER_REQUIRED,
    AUTH_TOKEN_INVALID,
    INTERNAL_ERROR,
    OIDC_CONFIG_NOT_FOUND,
    OIDC_ENCRYPTION_NOT_CONFIGURED,
)
from gateway.tenants.api.deps import get_bearer_token
from gateway.tenants.application.use_cases import GetIdentityUseCase
from gateway.tenants.domain.entities import Role

oidc_admin_router = APIRouter(prefix="/admin/oidc", tags=["oidc-admin"])

# Sentinel returned in responses instead of the plaintext/ciphertext client_secret.
_SECRET_PLACEHOLDER = "<stored>"  # noqa: S105 — not a real credential; response sentinel

# Private IP ranges for SSRF validation
_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_private_ip(hostname: str) -> bool:
    """Return True if hostname is a private/loopback IP address."""
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_RANGES)
    except ValueError:
        return False


def _validate_oidc_url(url: str, field_name: str, allow_http: bool) -> None:
    """Validate an OIDC URL for SSRF protection.

    Raises ValueError if:
    - scheme is http:// and allow_http is False
    - hostname is localhost or resolves to a private IP range
    """
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()

    if not allow_http and scheme != "https":
        raise ValueError(
            f"{field_name}: must use https:// scheme in production "
            f"(got {scheme!r}); set GATEWAY_OIDC_ALLOW_HTTP_URLS=true for dev/test only"
        )

    hostname = parsed.hostname or ""
    if hostname.lower() in ("localhost", "127.0.0.1", "::1"):
        if not allow_http:
            raise ValueError(f"{field_name}: localhost URLs are not permitted in production")

    if _is_private_ip(hostname):
        if not allow_http:
            raise ValueError(
                f"{field_name}: RFC-1918/loopback IP addresses are not permitted "
                f"in production (got {hostname!r})"
            )


class OidcConfigPutBody(BaseModel):
    """Request body for PUT /admin/oidc."""

    issuer: str
    client_id: str
    client_secret: str
    authorize_url: str = ""
    token_url: str
    jwks_url: str
    email_domains: list[str]
    enabled: bool = True


class OidcConfigResponse(BaseModel):
    """Response body for GET and PUT /admin/oidc.

    SECURITY: client_secret is ALWAYS "<stored>" — never the plaintext.
    """

    tenant_id: str
    issuer: str
    client_id: str
    client_secret: str  # always "<stored>"
    authorize_url: str
    token_url: str
    jwks_url: str
    email_domains: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


async def _get_owner_tenant_id(request: Request, session: AsyncSession) -> str:
    """Resolve the caller's tenant_id, enforcing owner role."""
    token = get_bearer_token(request)
    tokens = request.app.state.token_service
    use_case = GetIdentityUseCase(tokens)
    try:
        identity = use_case.execute(token)
    except Exception as exc:
        raise AUTH_TOKEN_INVALID.exc() from exc

    if identity.role != Role.OWNER:
        raise AUTH_FORBIDDEN_OWNER_REQUIRED.exc()

    return str(identity.tenant_id)


@oidc_admin_router.get("")
async def get_oidc_config(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """GET /admin/oidc — return per-tenant IdP config.

    client_secret is ALWAYS returned as "<stored>" — never the plaintext.
    Returns 404 ERR_OIDC_CONFIG_NOT_FOUND when no row exists for the tenant.
    """
    from gateway.auth.infrastructure.orm import OidcProviderConfigRow

    tenant_id_str = await _get_owner_tenant_id(request, session)

    import uuid as _uuid

    tenant_id = _uuid.UUID(tenant_id_str)

    stmt = select(OidcProviderConfigRow).where(OidcProviderConfigRow.tenant_id == tenant_id)
    result = await session.execute(stmt)
    row: OidcProviderConfigRow | None = result.scalar_one_or_none()

    if row is None:
        raise OIDC_CONFIG_NOT_FOUND.exc()

    # SECURITY: client_secret NEVER returned
    return {
        "tenant_id": str(row.tenant_id),
        "issuer": row.issuer,
        "client_id": row.client_id,
        "client_secret": _SECRET_PLACEHOLDER,  # NEVER the plaintext or ciphertext
        "authorize_url": row.authorize_url,
        "token_url": row.token_url,
        "jwks_url": row.jwks_url,
        "email_domains": list(row.email_domains or []),
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@oidc_admin_router.put("")
async def put_oidc_config(
    request: Request,
    body: OidcConfigPutBody,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    """PUT /admin/oidc — upsert per-tenant IdP config.

    Fernet-encrypts client_secret before DB write.
    Returns 409 when GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY is not set.
    Returns 422 for URL validation failures (https-only + no-private-IP).

    SECURITY: client_secret in body is NEVER logged or echoed back.
    """
    from gateway.auth.infrastructure.orm import OidcProviderConfigRow

    settings = request.app.state.settings
    tenant_id_str = await _get_owner_tenant_id(request, session)

    import uuid as _uuid

    tenant_id = _uuid.UUID(tenant_id_str)

    # Check encryption key before anything else
    enc_key = settings.oidc_config_encryption_key
    if not enc_key:
        raise OIDC_ENCRYPTION_NOT_CONFIGURED.exc()

    # SSRF URL validation — runs in handler before DB write (§3 contract)
    allow_http = settings.oidc_allow_http_urls
    url_fields = [
        ("issuer", body.issuer),
        ("token_url", body.token_url),
        ("jwks_url", body.jwks_url),
    ]
    if body.authorize_url:
        url_fields.append(("authorize_url", body.authorize_url))

    validation_errors: list[dict[str, Any]] = []
    for field_name, url in url_fields:
        try:
            _validate_oidc_url(url, field_name, allow_http)
        except ValueError as exc:
            validation_errors.append(
                {
                    "type": "url_scheme_invalid",
                    "loc": ["body", field_name],
                    "msg": str(exc),
                    "input": url,
                }
            )

    if validation_errors:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=422,
            content={"detail": validation_errors},
        )

    # Encrypt client_secret with Fernet — NEVER store plaintext
    from cryptography.fernet import Fernet

    fernet = Fernet(enc_key.encode() if isinstance(enc_key, str) else enc_key)
    # SECURITY: client_secret plaintext is used ONLY here for encryption; not logged
    client_secret_enc: bytes = fernet.encrypt(body.client_secret.encode())

    # UPSERT: INSERT ON CONFLICT (tenant_id) DO UPDATE
    from sqlalchemy import func as sa_func

    now = sa_func.now()
    stmt = (
        pg_insert(OidcProviderConfigRow)
        .values(
            tenant_id=tenant_id,
            issuer=body.issuer,
            client_id=body.client_id,
            client_secret_enc=client_secret_enc,
            authorize_url=body.authorize_url,
            token_url=body.token_url,
            jwks_url=body.jwks_url,
            email_domains=body.email_domains,
            enabled=body.enabled,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id"],
            set_={
                "issuer": body.issuer,
                "client_id": body.client_id,
                "client_secret_enc": client_secret_enc,
                "authorize_url": body.authorize_url,
                "token_url": body.token_url,
                "jwks_url": body.jwks_url,
                "email_domains": body.email_domains,
                "enabled": body.enabled,
                "updated_at": now,
            },
        )
        .returning(OidcProviderConfigRow)
    )

    await session.execute(stmt)
    await session.commit()

    # Fetch the row after upsert to return accurate timestamps
    from sqlalchemy import select as sa_select

    fetch_stmt = sa_select(OidcProviderConfigRow).where(
        OidcProviderConfigRow.tenant_id == tenant_id
    )
    fetch_result = await session.execute(fetch_stmt)
    row: OidcProviderConfigRow | None = fetch_result.scalar_one_or_none()

    if row is None:
        raise INTERNAL_ERROR.exc()

    # SECURITY: client_secret NEVER returned
    return {
        "tenant_id": str(row.tenant_id),
        "issuer": row.issuer,
        "client_id": row.client_id,
        "client_secret": _SECRET_PLACEHOLDER,  # NEVER the plaintext or ciphertext
        "authorize_url": row.authorize_url,
        "token_url": row.token_url,
        "jwks_url": row.jwks_url,
        "email_domains": list(row.email_domains or []),
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
