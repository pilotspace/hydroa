"""Dependency providers for the ops-auth surface (operator-wide-reconciliation §3 FROZEN).

`require_ops` is the operator peer to `require_owner_or_admin`. It authorizes ONLY a request
carrying a recognised operator client cert (mTLS → Envoy → XFCC header, matched against the
fingerprint allow-list). Denial is deliberately split:

  - a VALID tenant JWT (wrong credential type)       -> 403 ERR_OPS_FORBIDDEN
  - everything else (missing/unrecognised/malformed
    XFCC, ops-auth not configured)                   -> 401 ERR_OPS_UNAUTHORIZED (byte-identical)

The 401 path is byte-identical across modes (no oracle on which check failed). Fail-closed:
an empty allow-list authorizes no one.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.error_catalog import OPS_FORBIDDEN, OPS_UNAUTHORIZED, PLATFORM_TENANT_MISSING
from gateway.core.errors import ProblemError
from gateway.keys.api.deps import get_token_service
from gateway.proxy.application.use_cases import resolve_provider_credential
from gateway.proxy.domain.ports import TenantCredentialResolver
from gateway.tenants.domain.errors import InvalidTokenError
from gateway.tenants.domain.ports import TokenService
from gateway.tenants.infrastructure.ops_cert_verifier import OpsCertVerifier, OpsIdentity
from gateway.tenants.infrastructure.repository import get_platform_tenant

_XFCC_HEADER = "x-forwarded-client-cert"


def get_ops_cert_verifier(request: Request) -> OpsCertVerifier:
    """Build the verifier from the live `ops_cert_fingerprints` setting (default-OFF)."""
    return OpsCertVerifier.from_settings(request.app.state.settings)


def require_ops(
    request: Request,
    verifier: Annotated[OpsCertVerifier, Depends(get_ops_cert_verifier)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> OpsIdentity:
    """Authorize the platform operator via the forwarded mTLS cert, else deny (403/401)."""
    identity = verifier.identify(request.headers.get(_XFCC_HEADER))
    if identity is not None:
        return identity

    # Not an operator. Distinguish a VALID tenant credential (wrong type → 403) from
    # everything else (→ byte-identical 401). A failed tenant decode falls through to 401.
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        try:
            tokens.decode(token)
        except InvalidTokenError:
            pass
        else:
            raise OPS_FORBIDDEN.exc()

    raise OPS_UNAUTHORIZED.exc()


async def resolve_platform_credential(
    resolver: TenantCredentialResolver | None,
    session: AsyncSession,
    provider: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> object | None:
    """Resolve the platform tenant's own credential for `provider` — now audited.

    PRECONDITION (not enforced by this signature — an architectural assumption,
    exactly like `_operator: Annotated[OpsIdentity, Depends(require_ops)]` in
    ops/api/router.py): the caller has already been authorized via
    `Depends(require_ops)`. This function performs no auth check of its own.

    Returns:
        None — resolver is None OR provider not in BYOK_PROVIDERS (identical
        pass-through to resolve_provider_credential; not an error; UNAUDITED —
        never reaches platform-tenant-specific logic).
        Otherwise the contextvar reset Token; the platform tenant's resolved
        ProviderCredential becomes readable via get_provider_credential() until
        the caller resets the token in a `finally`.

    Raises:
        ProblemError(500, "ERR_PLATFORM_TENANT_MISSING") — get_platform_tenant
            returned None (unmigrated/pre-seed). Fails closed; never fabricates
            a tenant_id.
        ProblemError(402, "ERR_PROVIDER_KEY_MISSING") — REUSED unchanged from
            resolve_provider_credential: no enabled credential for `provider`.

    NEW (superadmin-audit-foundation TASK.md §3 Part A — FROZEN @ v1): every
    invocation that reaches platform-tenant-specific logic (get_platform_tenant
    succeeded) emits exactly one fire-and-forget, fail-open AuditEvent via
    record_audit(session_factory, ...) — covering all three real outcomes below
    (success / tenant-missing / key-missing). tenant_id=None and
    actor_user_id=None on every row: FORCED by AuditEvent.__post_init__'s actor
    invariant, since ops-mTLS auth (OpsIdentity) carries no user_id to satisfy a
    non-None tenant_id. The platform tenant's id (once resolved) and `provider`
    are carried in metadata instead. `session_factory` is used ONLY to schedule
    the audit write — the credential-resolution logic above is byte-identical to
    before this task. Required (not defaulted) so no future caller can silently
    skip the audit wiring.
    """
    platform_tenant = await get_platform_tenant(session)
    if platform_tenant is None:
        asyncio.ensure_future(  # noqa: RUF006
            record_audit(
                session_factory,
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=None,
                    actor_user_id=None,
                    actor_email=None,
                    action="ops.platform_credential_resolve",
                    target_type="provider",
                    target_id=provider,
                    result="error",
                    metadata={"provider": provider},
                    created_at=datetime.now(UTC),
                ),
            )
        )
        raise PLATFORM_TENANT_MISSING.exc()
    try:
        result = await resolve_provider_credential(resolver, platform_tenant.id, provider)
    except ProblemError:
        asyncio.ensure_future(  # noqa: RUF006
            record_audit(
                session_factory,
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=None,
                    actor_user_id=None,
                    actor_email=None,
                    action="ops.platform_credential_resolve",
                    target_type="provider",
                    target_id=provider,
                    result="denied",
                    metadata={
                        "provider": provider,
                        "platform_tenant_id": str(platform_tenant.id),
                    },
                    created_at=datetime.now(UTC),
                ),
            )
        )
        raise
    if result is not None:
        asyncio.ensure_future(  # noqa: RUF006
            record_audit(
                session_factory,
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=None,
                    actor_user_id=None,
                    actor_email=None,
                    action="ops.platform_credential_resolve",
                    target_type="provider",
                    target_id=provider,
                    result="success",
                    metadata={
                        "provider": provider,
                        "platform_tenant_id": str(platform_tenant.id),
                    },
                    created_at=datetime.now(UTC),
                ),
            )
        )
    return result
