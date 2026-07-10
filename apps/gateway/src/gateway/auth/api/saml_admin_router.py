"""Admin API for per-tenant SAML IdP configuration.

Routes (owner-only, prefix /admin/saml):
  GET  /admin/saml  — return per-tenant IdP config (idp_x509_cert IS returned —
                      it is public key material, not a secret; M10)
  PUT  /admin/saml  — upsert per-tenant IdP config

Mirrors auth/api/oidc_admin_router.py's shape (§3 freeze question 3): the
_get_owner_identity / SSRF-validator helpers are duplicated here rather than
imported, to avoid any dependency on the presumptively-frozen OIDC admin
router file — RECOMMENDED and chosen at freeze; a shared-extraction follow-up
is a possible later delta, not required now.

SECURITY INVARIANTS (M2, M10):
  - sp_entity_id / acs_url are ALWAYS server-derived — a body-supplied value
    for either field is silently ignored, never persisted (M2, Ground R6).
  - idp_x509_cert is validated as a parseable, non-expired PEM X.509
    certificate via cryptography.x509.load_pem_x509_certificate BEFORE any
    DB write.
  - idp_sso_url is validated https-only + no-private-IP (same predicate as
    oidc_admin_router.py::_validate_oidc_url/_is_private_ip).
"""

from __future__ import annotations

import asyncio
import ipaddress
import urllib.parse
import uuid as _uuid_mod
from datetime import UTC, datetime
from typing import Annotated, Any

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.auth.infrastructure.db_saml_config_resolver import derive_sp_entity_id
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_FORBIDDEN_OWNER_REQUIRED,
    AUTH_TOKEN_INVALID,
    INTERNAL_ERROR,
    SAML_CONFIG_NOT_FOUND,
)
from gateway.tenants.api.deps import get_bearer_token
from gateway.tenants.application.use_cases import GetIdentityUseCase
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.infrastructure.impersonation_session_guard import DbImpersonationSessionGuard

saml_admin_router = APIRouter(prefix="/admin/saml", tags=["saml-admin"])

# Private IP ranges for SSRF validation — duplicated verbatim from
# oidc_admin_router.py per §3 freeze question 3.
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


def _validate_saml_url(url: str, field_name: str, allow_http: bool) -> None:
    """Validate a SAML IdP URL for SSRF protection (mirrors _validate_oidc_url)."""
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()

    if not allow_http and scheme != "https":
        raise ValueError(
            f"{field_name}: must use https:// scheme in production "
            f"(got {scheme!r}); set GATEWAY_SAML_ALLOW_HTTP_URLS=true for dev/test only"
        )

    hostname = parsed.hostname or ""
    if hostname.lower() in ("localhost", "127.0.0.1", "::1") and not allow_http:
        raise ValueError(f"{field_name}: localhost URLs are not permitted in production")

    if _is_private_ip(hostname) and not allow_http:
        raise ValueError(
            f"{field_name}: RFC-1918/loopback IP addresses are not permitted "
            f"in production (got {hostname!r})"
        )


def _validate_x509_cert(cert_pem: str) -> None:
    """M10: idp_x509_cert must be a parseable, non-expired PEM X.509 certificate.

    Raises ValueError on any parse failure or expiry.
    """
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode(), default_backend())
    except Exception as exc:
        raise ValueError(f"idp_x509_cert: not a parseable PEM X.509 certificate ({exc})") from exc

    now = datetime.now(UTC)
    not_after = cert.not_valid_after_utc
    if not_after <= now:
        raise ValueError(f"idp_x509_cert: certificate expired at {not_after.isoformat()}")


class SamlConfigPutBody(BaseModel):
    """Request body for PUT /admin/saml.

    sp_entity_id / acs_url are intentionally NOT fields here — even if a
    caller includes them in the raw JSON body, pydantic silently drops
    unknown fields by default, so they can never reach persistence (M2).
    """

    idp_entity_id: str
    idp_sso_url: str
    idp_x509_cert: str
    email_domains: list[str]
    email_attribute_name: str = (
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
    )
    enabled: bool = True


async def _get_owner_identity(request: Request, session: AsyncSession) -> Identity:
    """Resolve the caller's full Identity, enforcing owner role (duplicated
    from oidc_admin_router.py per §3 freeze question 3)."""
    token = get_bearer_token(request)
    tokens = request.app.state.token_service
    use_case = GetIdentityUseCase(
        tokens,
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


def _serialize_row(row: object, *, settings: Any) -> dict[str, Any]:
    from gateway.auth.infrastructure.saml_orm import SamlProviderConfigRow

    assert isinstance(row, SamlProviderConfigRow)
    return {
        "tenant_id": str(row.tenant_id),
        "idp_entity_id": row.idp_entity_id,
        "idp_sso_url": row.idp_sso_url,
        "idp_x509_cert": row.idp_x509_cert,  # NOT a secret — public key material (M10)
        "sp_entity_id": derive_sp_entity_id(settings, row.tenant_id),
        "acs_url": settings.saml_acs_url,
        "email_domains": list(row.email_domains or []),
        "email_attribute_name": row.email_attribute_name,
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@saml_admin_router.get("")
async def get_saml_config(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """GET /admin/saml — return per-tenant IdP config.

    Returns 404 ERR_SAML_CONFIG_NOT_FOUND when no row exists for the tenant.
    """
    from gateway.auth.infrastructure.saml_orm import SamlProviderConfigRow

    identity = await _get_owner_identity(request, session)
    settings = request.app.state.settings

    stmt = select(SamlProviderConfigRow).where(
        SamlProviderConfigRow.tenant_id == identity.tenant_id
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        raise SAML_CONFIG_NOT_FOUND.exc()

    return _serialize_row(row, settings=settings)


@saml_admin_router.put("")
async def put_saml_config(
    request: Request,
    body: SamlConfigPutBody,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    """PUT /admin/saml — upsert per-tenant IdP config.

    Validates idp_sso_url (https-only + no-private-IP) and idp_x509_cert
    (parseable, non-expired PEM) BEFORE any DB write. sp_entity_id/acs_url
    are ALWAYS server-derived (M2) — never accepted from the request body.
    """
    from gateway.auth.infrastructure.saml_orm import SamlProviderConfigRow

    settings = request.app.state.settings
    actor_identity = await _get_owner_identity(request, session)
    tenant_id = actor_identity.tenant_id

    allow_http = settings.saml_allow_http_urls
    validation_errors: list[dict[str, Any]] = []

    try:
        _validate_saml_url(body.idp_sso_url, "idp_sso_url", allow_http)
    except ValueError as exc:
        validation_errors.append(
            {
                "type": "url_scheme_invalid",
                "loc": ["body", "idp_sso_url"],
                "msg": str(exc),
                "input": body.idp_sso_url,
            }
        )

    cert_error: str | None = None
    try:
        _validate_x509_cert(body.idp_x509_cert)
    except ValueError as exc:
        cert_error = str(exc)

    if validation_errors:
        return JSONResponse(status_code=422, content={"detail": validation_errors})

    if cert_error is not None:
        from gateway.core.error_catalog import SAML_CERT_INVALID

        raise SAML_CERT_INVALID.exc(detail=cert_error)

    now = datetime.now(UTC)
    stmt = (
        pg_insert(SamlProviderConfigRow)
        .values(
            tenant_id=tenant_id,
            idp_entity_id=body.idp_entity_id,
            idp_sso_url=body.idp_sso_url,
            idp_x509_cert=body.idp_x509_cert,
            email_domains=body.email_domains,
            email_attribute_name=body.email_attribute_name,
            enabled=body.enabled,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id"],
            set_={
                "idp_entity_id": body.idp_entity_id,
                "idp_sso_url": body.idp_sso_url,
                "idp_x509_cert": body.idp_x509_cert,
                "email_domains": body.email_domains,
                "email_attribute_name": body.email_attribute_name,
                "enabled": body.enabled,
                "updated_at": now,
            },
        )
        .returning(SamlProviderConfigRow)
    )

    await session.execute(stmt)
    await session.commit()

    fetch_stmt = select(SamlProviderConfigRow).where(SamlProviderConfigRow.tenant_id == tenant_id)
    fetch_result = await session.execute(fetch_stmt)
    row = fetch_result.scalar_one_or_none()

    if row is None:
        raise INTERNAL_ERROR.exc()

    # Audit emit — fail-open fire-and-forget; NEVER the cert (Part B contract).
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=_uuid_mod.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=actor_identity.user_id,
                actor_email=actor_identity.email,
                action="saml.put",
                target_type="saml",
                target_id="tenant_saml_config",
                result="success",
                metadata={"idp_entity_id": body.idp_entity_id, "enabled": body.enabled},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return _serialize_row(row, settings=settings)
