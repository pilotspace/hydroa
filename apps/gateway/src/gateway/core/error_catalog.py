"""Centralized error catalog for gateway ProblemError construction.

Every ``raise ProblemError(...)`` in the gateway MUST go through this module.
Calling sites import a catalog entry and call ``.exc(...)`` to obtain the
``ProblemError`` instance — no site should ever construct ``ProblemError``
directly with raw status/code/title literals.

Design:
- ``ErrorSpec`` is a frozen dataclass holding ``(status, code, title_template)``.
- ``title_template`` follows ``str.format(**fmt)`` conventions; for entries
  whose title is always static, the template contains no placeholders.
- ``ErrorSpec.exc(detail, headers, **fmt)`` formats the title and wraps it
  in a ``ProblemError``.  Sites that have a dynamic ``detail`` pass it via
  the ``detail=`` keyword; sites that need response headers (e.g. Retry-After)
  pass them via ``headers=``.
- One module-level constant per distinct ``(status, code, title_template)``
  triple found in the codebase — enumerated exhaustively from all 113 raise
  sites as of the initial migration.

Layering (CONVENTIONS.md): this module lives in ``gateway/core/`` so that all
layers (api, application, infrastructure) may import it.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway.core.errors import ProblemError


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """Immutable descriptor for a class of HTTP problem errors.

    ``title_template`` may contain ``str.format``-style placeholders.
    Pass values for those placeholders as ``**fmt`` to ``exc()``.
    """

    status: int
    code: str
    title_template: str

    def exc(
        self,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        **fmt: object,
    ) -> ProblemError:
        """Return a ``ProblemError`` ready to be raised.

        Args:
            detail: optional human-readable elaboration included in the
                RFC 9457 ``detail`` field.
            headers: optional response headers (e.g. ``Retry-After``).
            **fmt: keyword arguments forwarded to ``title_template.format()``.
                   When the template has no placeholders, pass nothing.

        Returns:
            A ``ProblemError`` instance — callers must ``raise`` it themselves
            so that exception chaining (``from err``) works naturally.
        """
        title = self.title_template.format(**fmt) if fmt else self.title_template
        return ProblemError(
            status=self.status,
            code=self.code,
            title=title,
            detail=detail,
            headers=headers,
        )


# ---------------------------------------------------------------------------
# Auth — token / JWT errors
# ---------------------------------------------------------------------------

#: Bearer token absent or scheme is not "Bearer".
AUTH_TOKEN_MISSING = ErrorSpec(401, "ERR_AUTH_INVALID_TOKEN", "Missing or malformed bearer token")

#: Bearer token present but signature / expiry / decode fails.
AUTH_TOKEN_INVALID = ErrorSpec(401, "ERR_AUTH_INVALID_TOKEN", "Invalid or expired token")

#: Caller's role is insufficient for the requested operation (member → owner/admin endpoint).
AUTH_FORBIDDEN = ErrorSpec(403, "ERR_AUTH_FORBIDDEN", "Insufficient role for this operation")

#: Admin OIDC endpoint requires owner role specifically.
AUTH_FORBIDDEN_OWNER_REQUIRED = ErrorSpec(403, "ERR_AUTH_FORBIDDEN", "Owner role required")

# ---------------------------------------------------------------------------
# Auth — API key errors
# ---------------------------------------------------------------------------

#: API key missing or hash does not match any known key.
AUTH_KEY_INVALID = ErrorSpec(401, "ERR_AUTH_INVALID_KEY", "Missing or invalid API key")

#: API key recognised but its ``expires_at`` is in the past.
AUTH_KEY_EXPIRED = ErrorSpec(401, "ERR_AUTH_KEY_EXPIRED", "API key has expired")

#: ``/internal/authz`` endpoint — single opaque error for all key failures.
AUTH_KEY_INVALID_AUTHZ = ErrorSpec(401, "ERR_AUTH_INVALID_KEY", "Invalid API key")

# ---------------------------------------------------------------------------
# Auth — credential errors (signup / login)
# ---------------------------------------------------------------------------

#: Email / password pair did not match a known identity.
AUTH_CREDENTIALS_INVALID = ErrorSpec(401, "ERR_AUTH_INVALID_CREDENTIALS", "Invalid credentials")

#: Signup rejected because the email address is already registered.
AUTH_EMAIL_TAKEN = ErrorSpec(
    409, "ERR_TENANT_EMAIL_TAKEN", "An account with this email already exists"
)

#: Signup rejected because the chosen password is too short.
AUTH_PASSWORD_WEAK = ErrorSpec(
    400, "ERR_AUTH_PASSWORD_WEAK", "Password must be at least 10 characters"
)

# ---------------------------------------------------------------------------
# Model errors
# ---------------------------------------------------------------------------

#: Requested model is not active in the catalog (dynamic: model_id).
MODEL_UNKNOWN = ErrorSpec(400, "ERR_MODEL_UNKNOWN", "Model '{model_id}' is not available")

#: Model is disabled for the calling tenant (tenant override = false).
MODEL_DISABLED = ErrorSpec(403, "ERR_MODEL_DISABLED", "Model is disabled for this tenant")

#: Model is not in the key-level allowlist.
MODEL_NOT_ALLOWED = ErrorSpec(403, "ERR_MODEL_NOT_ALLOWED", "Model not permitted for this API key")

#: Model not found in catalog admin endpoints (dynamic: model_id).
MODEL_NOT_FOUND = ErrorSpec(404, "ERR_MODEL_NOT_FOUND", "Model '{model_id}' not found in catalog")

# ---------------------------------------------------------------------------
# Payload / request validation errors
# ---------------------------------------------------------------------------

# The majority of 422 sites use a bespoke static title that describes EXACTLY
# which field failed.  Each gets its own entry so the title is preserved
# byte-identically.

#: Generic field validation — used by RequestValidationError handler in errors.py.
#: Also raised directly for ``model`` and ``messages`` field checks in the proxy.
PAYLOAD_INVALID = ErrorSpec(422, "ERR_PAYLOAD_INVALID", "Request payload failed validation")

#: Proxy: ``model`` field missing or empty.
PAYLOAD_MODEL_REQUIRED = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "Field 'model' is required and non-empty"
)

#: Proxy: ``messages`` field missing, not a list, or empty.
PAYLOAD_MESSAGES_REQUIRED = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "Field 'messages' must be a non-empty list"
)

#: Proxy: ``input`` field missing or empty (embeddings endpoint).
PAYLOAD_INPUT_REQUIRED = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "Field 'input' is required and non-empty"
)

#: Proxy: ``prompt`` field missing or empty (images endpoint).
PAYLOAD_PROMPT_REQUIRED = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "Field 'prompt' is required and non-empty"
)

#: Proxy: ``file`` field missing from multipart form (audio STT endpoint).
PAYLOAD_FILE_REQUIRED = ErrorSpec(422, "ERR_PAYLOAD_INVALID", "Field 'file' is required")

#: Proxy: ``voice`` field missing or empty (audio TTS endpoint).
PAYLOAD_VOICE_REQUIRED = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "Field 'voice' is required and non-empty"
)

# --- keys/api/schemas.py field validators ---

#: ``monthly_budget_usd`` is negative.
PAYLOAD_MONTHLY_BUDGET_NEGATIVE = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "monthly_budget_usd must be >= 0"
)

#: ``soft_budget_usd`` is negative.
PAYLOAD_SOFT_BUDGET_NEGATIVE = ErrorSpec(422, "ERR_PAYLOAD_INVALID", "soft_budget_usd must be >= 0")

#: ``model_allowlist`` is not a list.
PAYLOAD_ALLOWLIST_NOT_LIST = ErrorSpec(422, "ERR_PAYLOAD_INVALID", "model_allowlist must be a list")

#: An element of ``model_allowlist`` is not a non-empty string.
PAYLOAD_ALLOWLIST_BAD_ELEMENT = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "model_allowlist elements must be non-empty strings"
)

#: ``soft_budget_usd`` exceeds ``monthly_budget_usd``.
PAYLOAD_SOFT_EXCEEDS_HARD = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "soft_budget_usd must be <= monthly_budget_usd"
)

#: A decimal field cannot be parsed (dynamic: field_name).
PAYLOAD_DECIMAL_INVALID = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "{field_name} must be a valid decimal string"
)

#: An integer field is not parseable or not positive (dynamic: field_name).
PAYLOAD_INT_INVALID = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "{field_name} must be a positive integer"
)

#: An integer field parsed but is zero or negative (dynamic: field_name).
PAYLOAD_INT_NOT_POSITIVE = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "{field_name} must be a positive integer (> 0)"
)

#: ``expires_at`` is not a valid ISO-8601 string (dynamic: value).
PAYLOAD_EXPIRES_AT_INVALID = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "Invalid expires_at format: {value!r}"
)

# --- budgets/api/router.py ---

#: ``budget_usd_monthly`` could not be parsed as a decimal.
PAYLOAD_BUDGET_DECIMAL_INVALID = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "budget_usd_monthly must be a valid decimal string or null"
)

#: ``budget_usd_monthly`` is negative.
PAYLOAD_BUDGET_NEGATIVE = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "budget_usd_monthly must be non-negative"
)

# --- usage/api/router.py ---

#: ``window`` query param has an invalid value (dynamic: window).
PAYLOAD_WINDOW_INVALID = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "window must be one of: day, week, month; got {window!r}"
)

#: ``start`` query param is not a valid ISO date (dynamic: start).
PAYLOAD_START_DATE_INVALID = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "Invalid ISO date for start: {start!r}"
)

#: ``end`` query param is not a valid ISO date (dynamic: end).
PAYLOAD_END_DATE_INVALID = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "Invalid ISO date for end: {end!r}"
)

#: ``key_id`` query param is not a valid UUID (dynamic: key_id).
PAYLOAD_KEY_ID_UUID_INVALID = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "Invalid UUID for key_id: {key_id!r}"
)

#: ``group_by`` query param has an invalid value (dynamic: group_by).
PAYLOAD_GROUP_BY_INVALID = ErrorSpec(
    422,
    "ERR_PAYLOAD_INVALID",
    "group_by must be one of: {valid}; got {group_by!r}",
)

# --- tenants/api/guardrail_router.py — custom pattern validation ---

#: Custom pattern list exceeds max count (V1). Detail carries exact message.
PAYLOAD_CUSTOM_PATTERN_INVALID = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "Custom pattern validation failed"
)

# ---------------------------------------------------------------------------
# Resource not found
# ---------------------------------------------------------------------------

#: API key not found or does not belong to the caller's tenant.
KEY_NOT_FOUND = ErrorSpec(404, "ERR_KEY_NOT_FOUND", "Key not found")

#: Key not found when scoped to spend-window endpoint.
KEY_NOT_FOUND_IN_TENANT = ErrorSpec(
    404, "ERR_KEY_NOT_FOUND", "Key not found or does not belong to this tenant"
)

#: Team not found or does not belong to the caller's tenant.
TEAM_NOT_FOUND = ErrorSpec(404, "ERR_TEAM_NOT_FOUND", "Team not found")

#: User not found in the team / tenant.
USER_NOT_FOUND = ErrorSpec(404, "ERR_USER_NOT_FOUND", "User not found in this tenant")

#: Team member not found.
MEMBER_NOT_FOUND = ErrorSpec(404, "ERR_MEMBER_NOT_FOUND", "Member not found")

# ---------------------------------------------------------------------------
# Conflict / already exists
# ---------------------------------------------------------------------------

#: A team with this name already exists in the tenant.
TEAM_EXISTS = ErrorSpec(409, "ERR_TEAM_EXISTS", "A team with this name already exists")

#: User is already a member of the team.
MEMBER_EXISTS = ErrorSpec(409, "ERR_MEMBER_EXISTS", "User is already a member of this team")

# ---------------------------------------------------------------------------
# Budget / spend errors
# ---------------------------------------------------------------------------

#: Tenant, team, or per-key monthly budget has been exceeded.
#: Detail is dynamic (caller provides it via ``detail=``).
BUDGET_EXCEEDED = ErrorSpec(402, "ERR_BUDGET_EXCEEDED", "Monthly budget exceeded")

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

#: RPM or TPM rate limit exceeded.
#: Detail and ``Retry-After`` header are always provided by the caller.
RATE_LIMITED = ErrorSpec(429, "ERR_RATE_LIMITED", "Rate limit exceeded")

# ---------------------------------------------------------------------------
# Guardrail / content policy
# ---------------------------------------------------------------------------

#: Request blocked by an active guardrail policy.
GUARDRAIL_BLOCKED = ErrorSpec(400, "ERR_GUARDRAIL_BLOCKED", "Request blocked by guardrail policy")

# ---------------------------------------------------------------------------
# Upstream / catalog service errors
# ---------------------------------------------------------------------------

#: Upstream LLM service returned an error or is unreachable.
UPSTREAM_UNAVAILABLE = ErrorSpec(502, "ERR_UPSTREAM_UNAVAILABLE", "Upstream service unavailable")

#: Upstream catalog source is unreachable during sync.
CATALOG_UPSTREAM_UNAVAILABLE = ErrorSpec(
    502, "ERR_UPSTREAM_UNAVAILABLE", "Upstream catalog source unavailable"
)

#: No active models have been synced into the catalog yet.
CATALOG_EMPTY = ErrorSpec(409, "ERR_CATALOG_EMPTY", "No active models in catalog — run sync first")

# ---------------------------------------------------------------------------
# OIDC / SSO errors
# ---------------------------------------------------------------------------

#: OIDC is not configured on this platform (no DB config, env-disabled).
OIDC_NOT_CONFIGURED = ErrorSpec(
    404, "ERR_OIDC_NOT_CONFIGURED", "OIDC login is not configured on this platform"
)

#: Per-tenant OIDC config resolved but found disabled/missing.
OIDC_TENANT_NOT_CONFIGURED = ErrorSpec(
    404, "ERR_OIDC_NOT_CONFIGURED", "OIDC configuration not found or disabled for this tenant"
)

#: No OIDC configuration row found for the tenant (admin GET endpoint).
OIDC_CONFIG_NOT_FOUND = ErrorSpec(
    404, "ERR_OIDC_CONFIG_NOT_FOUND", "No OIDC configuration found for this tenant"
)

#: Encryption key not set — cannot store OIDC client secrets.
OIDC_ENCRYPTION_NOT_CONFIGURED = ErrorSpec(
    409,
    "ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED",
    "GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY must be set to store OIDC client secrets",
)

#: ``oidc_tenant_id`` cookie is missing when the state cookie is present,
#: indicating the login session lost its tenant binding mid-flow.
OIDC_TENANT_COOKIE_MISSING_BINDING = ErrorSpec(
    400,
    "ERR_OIDC_TENANT_COOKIE_MISSING",
    "OIDC login session lost its tenant binding; restart login",
)

#: Use-case raised ``OidcTenantCookieMissingError``.
OIDC_TENANT_COOKIE_MISSING = ErrorSpec(
    400, "ERR_OIDC_TENANT_COOKIE_MISSING", "oidc_tenant_id cookie is required"
)

#: ``code`` or ``state`` query params missing from IdP redirect.
OIDC_INVALID_CALLBACK = ErrorSpec(400, "ERR_OIDC_INVALID_CALLBACK", "Invalid callback parameters")

#: OIDC state/nonce cookies have expired.
OIDC_SESSION_EXPIRED = ErrorSpec(400, "ERR_OIDC_SESSION_EXPIRED", "OIDC session expired")

#: ``state`` param does not match the cookie-pinned state.
OIDC_STATE_MISMATCH = ErrorSpec(400, "ERR_OIDC_STATE_MISMATCH", "State parameter mismatch")

#: IdP token exchange failed (upstream error from the identity provider).
OIDC_UPSTREAM_ERROR = ErrorSpec(502, "ERR_OIDC_UPSTREAM_ERROR", "IdP token exchange failed")

#: The ID token ``exp`` claim is in the past.
OIDC_TOKEN_EXPIRED = ErrorSpec(401, "ERR_OIDC_TOKEN_EXPIRED", "ID token has expired")

#: ID token signature / claims validation failed.
OIDC_TOKEN_INVALID = ErrorSpec(401, "ERR_OIDC_TOKEN_INVALID", "ID token validation failed")

#: The verified email domain is not mapped to any tenant.
OIDC_DOMAIN_NOT_MAPPED = ErrorSpec(
    403, "ERR_OIDC_DOMAIN_NOT_MAPPED", "Email domain not permitted for SSO login"
)

#: The verified email is bound to a different tenant.
OIDC_TENANT_CONFLICT = ErrorSpec(
    403, "ERR_OIDC_TENANT_CONFLICT", "Email is bound to a different tenant"
)

# ---------------------------------------------------------------------------
# Internal / server errors
# ---------------------------------------------------------------------------

#: Unexpected internal error (used in the OIDC admin router after a DB upsert
#: that succeeded but then returned no row on the follow-up SELECT).
INTERNAL_ERROR = ErrorSpec(500, "ERR_INTERNAL", "Failed to retrieve saved OIDC config")

# ---------------------------------------------------------------------------
# Provider selection errors (provider-seam task)
# ---------------------------------------------------------------------------

#: Requested provider is absent from the registry (key not configured or disabled).
#: Dynamic: {provider} — the provider name from the catalog row.
PROVIDER_UNAVAILABLE = ErrorSpec(
    503,
    "ERR_PROVIDER_UNAVAILABLE",
    "Provider '{provider}' is not configured or unavailable",
)
