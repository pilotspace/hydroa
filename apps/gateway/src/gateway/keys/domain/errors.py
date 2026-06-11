"""Domain errors for API keys."""


class KeyError_(Exception):
    """Base for API key domain failures."""


class KeyNotFoundError(KeyError_):
    """Key not found (or belongs to another tenant — identical response, no leak)."""


class InvalidApiKeyError(KeyError_):
    """Key validation failed: malformed, unknown, revoked, or wrong secret.

    All failure modes raise this error with identical messages so the API
    layer returns byte-identical responses (no enumeration).
    """


class ForbiddenError(KeyError_):
    """Caller's role is not permitted for this operation."""


class KeyExpiredError(KeyError_):
    """Key has passed its expires_at timestamp.

    Post-identification error (key hash matched, not revoked, but expired).
    Distinct from InvalidApiKeyError — expiry is not an enumeration risk
    because the key is already identified. Raised ONLY after hash-match succeeds.
    """


class ModelNotAllowedError(KeyError_):
    """Requested model is not in the key's model_allowlist."""
