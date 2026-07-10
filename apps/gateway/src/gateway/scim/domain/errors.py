"""SCIM domain error classes — zero framework imports.

Each error maps to an HTTP status + shape in the API layer (scim_router.py /
token_router.py). Part A (/admin/scim/tokens) errors map to RFC 9457 problem+json via
gateway.core.error_catalog; Part B (/scim/v2/*) errors map to the RFC 7644 SCIM error
envelope (scim_schemas.scim_error) — a deliberate, documented exception to the
project-wide RFC 9457 convention (TASK.md §0 Honors).
"""


class ScimTokenNotFoundError(Exception):
    """Token id unknown, already revoked, or belongs to a different tenant — deliberately
    indistinguishable (mirrors InviteNotFoundError's own precedent). → 404
    ERR_SCIM_TOKEN_NOT_FOUND (RFC 9457, admin-API side)."""


class ScimInvalidTokenError(Exception):
    """SCIM bearer token missing, malformed, unknown, or revoked — all indistinguishable
    (no enumeration oracle, mirrors AuthzUseCase's own constant-shape failure). → 401
    SCIM Error, detail="invalid_token"."""


class ScimUserNotFoundError(Exception):
    """{id} unknown OR belongs to a different tenant than the bearer token's own tenant —
    deliberately indistinguishable (tenant-confusion defense). → 404 SCIM Error."""


class ScimUniquenessError(Exception):
    """POST /Users with an email that already exists (any tenant). → 409 SCIM Error,
    scimType="uniqueness"."""


class ScimInvalidPayloadError(Exception):
    """Malformed SCIM payload: missing schemas, missing required userName, invalid PATCH
    op. → 400 SCIM Error, scimType="invalidValue"."""


class ScimMutabilityError(Exception):
    """PATCH targets an immutable path (id, meta). → 400 SCIM Error, scimType="mutability"."""


class ScimGroupsUnsupportedError(Exception):
    """Any Groups write (POST/PUT/PATCH/DELETE). → 501 SCIM Error."""


class ScimRateLimitedError(Exception):
    """SCIM write rate limit exceeded for this token. → 429 SCIM Error + Retry-After."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"scim rate limited; retry after {retry_after}s")
        self.retry_after = retry_after
