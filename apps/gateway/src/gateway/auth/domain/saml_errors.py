"""SAML domain error classes.

Each error maps to an HTTP status code + ERR_SAML_* code in the API layer
(TASK.md §3 Part A/B, §2 Reject list). Mirrors auth/domain/errors.py's OIDC
error-class shape — a distinct file per the §1 Framings decision (new files
only, zero edits to OIDC files).
"""


class SamlNotConfiguredError(Exception):
    """No enabled SamlProviderConfig matches the requested domain. → 404."""


class SamlRequestNotFoundError(Exception):
    """No pending-request record matches the (unverified) InResponseTo/Issuer —
    includes genuinely IdP-initiated, unsolicited responses (M6). → 400."""


class SamlRequestAlreadyUsedError(Exception):
    """The pending-request record was already consumed (GETDEL returned nothing
    on a second attempt — M3 single-use enforcement). → 400."""


class SamlSignatureInvalidError(Exception):
    """XML signature verification failed: missing, invalid, or a structurally
    wrapped/duplicated signed node (M4, XSW-class defense). → 401."""


class SamlIssuerMismatchError(Exception):
    """Assertion/response Issuer != the tenant's configured idp_entity_id. → 401."""


class SamlAudienceMismatchError(Exception):
    """Audience (Conditions/AudienceRestriction) != the tenant's sp_entity_id. → 401."""


class SamlResponseMismatchError(Exception):
    """The signed SubjectConfirmationData/@InResponseTo != the pending request's
    request_id — the TRUST-BEARING check (M5.4). → 401."""


class SamlAssertionExpiredError(Exception):
    """NotBefore/NotOnOrAfter violated outside the configured clock-skew window
    (M5.5). → 401."""


class SamlAssertionReplayedError(Exception):
    """Assertion @ID already present in the consumed-assertion cache — the
    independent second replay layer (M5.6). → 401."""


class SamlEmailMissingError(Exception):
    """No email resolvable via the M13 ranked lookup. → 401."""


class SamlTenantConflictError(Exception):
    """Resolved email exists bound to a DIFFERENT tenant than the one the
    pending-request pinned. → 403."""


class SamlStoreUnavailableError(Exception):
    """app.state.redis_client unreachable during the M3/M5.6 replay checks —
    fail CLOSED, never treated as "not replayed" (M12). → 503."""


class SamlConfigNotFoundError(Exception):
    """GET /admin/saml with no saml_provider_configs row for the caller's
    tenant. → 404."""


class SamlCertInvalidError(Exception):
    """PUT /admin/saml with an unparseable or expired idp_x509_cert. → 422."""
