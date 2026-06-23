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

from typing import Annotated

from fastapi import Depends, Request

from gateway.core.error_catalog import OPS_FORBIDDEN, OPS_UNAUTHORIZED
from gateway.keys.api.deps import get_token_service
from gateway.tenants.domain.errors import InvalidTokenError
from gateway.tenants.domain.ports import TokenService
from gateway.tenants.infrastructure.ops_cert_verifier import OpsCertVerifier, OpsIdentity

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
