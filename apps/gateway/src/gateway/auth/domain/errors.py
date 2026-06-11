"""OIDC domain error classes.

Each error maps to an HTTP status code + ERR_OIDC_* code in the API layer.
"""


class OidcUpstreamError(Exception):
    """IdP token endpoint failed: timeout, network error, or non-200 status. → 502."""


class OidcTokenInvalidError(Exception):
    """ID token claims validation failed (iss, aud, nonce, email). → 401."""


class OidcTokenExpiredError(Exception):
    """ID token exp claim is in the past. → 401."""


class OidcStateMismatchError(Exception):
    """State query param != oidc_state cookie. → 400."""


class OidcSessionExpiredError(Exception):
    """oidc_state cookie absent (session expired or already consumed). → 400."""


class OidcInvalidCallbackError(Exception):
    """Missing code or state query params. → 400."""


class OidcDomainNotMappedError(Exception):
    """Email domain not in GATEWAY_OIDC_DOMAIN_MAPPING. → 403."""


class OidcTenantConflictError(Exception):
    """User email exists bound to a different tenant than the domain mapping. → 403."""
