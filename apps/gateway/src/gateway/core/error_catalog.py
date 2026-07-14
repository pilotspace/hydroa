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
        extra: dict[str, object] | None = None,
        **fmt: object,
    ) -> ProblemError:
        """Return a ``ProblemError`` ready to be raised.

        Args:
            detail: optional human-readable elaboration included in the
                RFC 9457 ``detail`` field.
            headers: optional response headers (e.g. ``Retry-After``).
            extra: optional additive structured-data fields merged into the
                response body (e.g. ``raw_output``/``validation_errors`` on
                ``ERR_OUTPUT_SCHEMA_VALIDATION_FAILED``). None for every other
                call site — byte-identical by construction.
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
            extra=extra,
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
# Ops-auth — operator-wide (cross-tenant) surface (operator-wide-reconciliation)
# ---------------------------------------------------------------------------

#: No recognised operator client cert (missing / unrecognised / malformed XFCC, or
#: ops-auth not configured). BYTE-IDENTICAL across every failure mode — no oracle.
OPS_UNAUTHORIZED = ErrorSpec(401, "ERR_OPS_UNAUTHORIZED", "Operator authorization required")

#: A valid TENANT credential was presented to the operator surface — wrong credential
#: type; cross-tenant power never rides a tenant token.
OPS_FORBIDDEN = ErrorSpec(
    403, "ERR_OPS_FORBIDDEN", "Tenant credentials cannot access the operator surface"
)

#: The reserved kind='platform' tenants row does not exist (unmigrated/pre-seed state).
#: Fails closed — resolve_platform_credential never fabricates a substitute tenant_id.
PLATFORM_TENANT_MISSING = ErrorSpec(
    500, "ERR_PLATFORM_TENANT_MISSING", "Platform tenant not provisioned"
)

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

#: Public signup rejected because it is disabled (invite-only). Checked FIRST,
#: before any body validation or DB IO — see signup-and-routing-authz S1.
SIGNUP_INVITE_ONLY = ErrorSpec(
    403,
    "ERR_SIGNUP_INVITE_ONLY",
    "Public signup is disabled; ask an existing member for an invite",
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
# Routing config write (routing-config-write)
# ---------------------------------------------------------------------------

#: PUT /admin/routing body failed the Settings/Deployment validators. The underlying
#: validator code (e.g. UNKNOWN_ROUTING_STRATEGY, DUPLICATE_DEPLOYMENT) is passed as `detail`.
ROUTING_CONFIG_INVALID = ErrorSpec(
    422, "ERR_ROUTING_CONFIG_INVALID", "Routing configuration failed validation"
)

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

#: Proxy: TTS ``input`` exceeds GATEWAY_TTS_MAX_INPUT_CHARACTERS (audio TTS endpoint).
#: 413 Payload Too Large — rejected BEFORE governance/upstream/bill (no partial charge).
PAYLOAD_INPUT_TOO_LONG = ErrorSpec(
    413, "ERR_PAYLOAD_INPUT_TOO_LONG", "TTS input exceeds the maximum allowed length"
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

#: ``tier`` is not one of {priority, standard, null} (service-tiers TASK.md §3 R1).
PAYLOAD_TIER_INVALID = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID", "tier must be one of 'priority', 'standard', or null"
)

#: ``priority_reserved_pct + standard_reserved_pct`` exceeds 1.0 (service-tiers
#: TASK.md §3 R5 — the live PUT /admin/platform/service-tiers write path).
PAYLOAD_TIER_PCT_SUM_INVALID = ErrorSpec(
    422,
    "ERR_PAYLOAD_INVALID",
    "priority_reserved_pct + standard_reserved_pct must be <= 1.0",
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

# --- cost-attribution-tags TASK.md §3 — X-Gateway-Tags header / tag_key filter ---

#: X-Gateway-Tags header malformed, or ?tag_key= filter malformed (cost-attribution-tags
#: TASK.md §3 R1-R5, R8). Detail carries the exact validation-failure reason.
PAYLOAD_TAGS_INVALID = ErrorSpec(422, "ERR_PAYLOAD_INVALID", "Tags validation failed")

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

#: agent-identity-governance TASK.md §3 (FROZEN @ v1): agent principal id unknown
#: or belongs to another tenant (byte-identical — no enumeration leak).
AGENT_PRINCIPAL_NOT_FOUND = ErrorSpec(
    404, "ERR_AGENT_PRINCIPAL_NOT_FOUND", "Agent principal not found"
)

#: agent-identity-governance TASK.md §3 (FROZEN @ v1): agent_tokens id unknown or
#: belongs to another tenant (byte-identical — no enumeration leak).
AGENT_TOKEN_NOT_FOUND = ErrorSpec(404, "ERR_AGENT_TOKEN_NOT_FOUND", "Agent token not found")

#: agent-identity-governance TASK.md §3 (FROZEN @ v1): attach target token is already
#: attached to a (possibly different) principal.
AGENT_TOKEN_ALREADY_ATTACHED = ErrorSpec(
    409, "ERR_AGENT_TOKEN_ALREADY_ATTACHED", "Agent token is already attached to a principal"
)

#: agent-identity-governance TASK.md §3 (FROZEN @ v1): create with a name already
#: used by another principal in the same tenant.
AGENT_PRINCIPAL_NAME_CONFLICT = ErrorSpec(
    409, "ERR_AGENT_PRINCIPAL_NAME_CONFLICT", "Agent principal name already in use"
)

#: agent-identity-governance TASK.md §3 (FROZEN @ v1): a fresh token cannot be
#: attached to an already-killed principal.
AGENT_PRINCIPAL_KILLED = ErrorSpec(
    409, "ERR_AGENT_PRINCIPAL_KILLED", "Agent principal has been killed"
)

#: User not found in the team / tenant.
USER_NOT_FOUND = ErrorSpec(404, "ERR_USER_NOT_FOUND", "User not found in this tenant")

#: Invite not found — unknown id OR belongs to a different tenant (no distinguishing oracle;
#: member-invite-issuance TASK.md R8).
INVITE_NOT_FOUND = ErrorSpec(404, "ERR_INVITE_NOT_FOUND", "Invite not found")

#: Team member not found.
MEMBER_NOT_FOUND = ErrorSpec(404, "ERR_MEMBER_NOT_FOUND", "Member not found")

#: Tenant not found (platform-tenant-directory TASK.md §3 — superadmin cross-tenant lookup).
TENANT_NOT_FOUND = ErrorSpec(404, "ERR_TENANT_NOT_FOUND", "Tenant not found")

# ---------------------------------------------------------------------------
# Conflict / already exists
# ---------------------------------------------------------------------------

#: A team with this name already exists in the tenant.
TEAM_EXISTS = ErrorSpec(409, "ERR_TEAM_EXISTS", "A team with this name already exists")

#: Invite-create rejected — the target email already belongs to a user in the CALLER's own
#: tenant (member-invite-issuance TASK.md R7; safe to disclose — caller already sees this
#: roster via GET /admin/users).
INVITE_EMAIL_ALREADY_MEMBER = ErrorSpec(
    409, "ERR_INVITE_EMAIL_ALREADY_MEMBER", "This email already belongs to a member of your tenant"
)

#: Invite-revoke rejected — the resolved invite's status is not 'pending' (already accepted
#: or already revoked; member-invite-issuance TASK.md R9).
INVITE_NOT_PENDING = ErrorSpec(409, "ERR_INVITE_NOT_PENDING", "Invite is not in pending state")

#: Invite resolved (preview or accept) and its status IS 'pending', but expires_at has
#: passed — a fresh, invite-scoped 410 (member-invite-acceptance TASK.md R3), sibling to
#: INVITE_NOT_FOUND/INVITE_NOT_PENDING above. NOT a reuse of agent_oauth's own
#: AGENT_OAUTH_EXPIRED (differently-scoped resource) — mirrors this codebase's
#: per-feature-scoped error-naming convention.
INVITE_EXPIRED = ErrorSpec(410, "ERR_INVITE_EXPIRED", "Invite has expired")

#: User is already a member of the team.
MEMBER_EXISTS = ErrorSpec(409, "ERR_MEMBER_EXISTS", "User is already a member of this team")

# ---------------------------------------------------------------------------
# Budget / spend errors
# ---------------------------------------------------------------------------

#: Tenant, team, or per-key monthly budget has been exceeded.
#: Detail is dynamic (caller provides it via ``detail=``).
BUDGET_EXCEEDED = ErrorSpec(402, "ERR_BUDGET_EXCEEDED", "Monthly budget exceeded")

# ---------------------------------------------------------------------------
# Prepaid credits (credits-ledger TASK.md §3 — FROZEN @ v1)
# ---------------------------------------------------------------------------

#: balance_usd - hold_estimate_usd < -grace_usd at admission (R1).
CREDITS_EXHAUSTED = ErrorSpec(402, "ERR_CREDITS_EXHAUSTED", "Prepaid credit balance exhausted")

#: top-up amount_usd <= 0, non-decimal, or non-finite (R2).
CREDITS_TOPUP_INVALID = ErrorSpec(
    422, "ERR_CREDITS_TOPUP_INVALID", "amount_usd must be a positive, finite decimal string"
)

#: top-up request missing the Idempotency-Key header (R3).
CREDITS_IDEMPOTENCY_KEY_REQUIRED = ErrorSpec(
    400, "ERR_CREDITS_IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required"
)

#: Idempotency-Key reused with a different tenant_id or amount_usd (R4).
CREDITS_IDEMPOTENCY_KEY_CONFLICT = ErrorSpec(
    409,
    "ERR_CREDITS_IDEMPOTENCY_KEY_CONFLICT",
    "Idempotency-Key already used with a different tenant_id or amount_usd",
)

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

#: RPM or TPM rate limit exceeded.
#: Detail and ``Retry-After`` header are always provided by the caller.
RATE_LIMITED = ErrorSpec(429, "ERR_RATE_LIMITED", "Rate limit exceeded")

#: Per-key bandwidth (tokens/sec) budget exhausted (stream-bandwidth-pacing, v36).
#: 503 on the non-stream PRE-FLIGHT shed path (Retry-After always provided by the caller);
#: mid-stream it is only the SSE error-frame body's ``code`` (status is already 200).
BANDWIDTH_EXHAUSTED = ErrorSpec(503, "ERR_BANDWIDTH_EXHAUSTED", "Bandwidth limit exceeded")

#: A request's tier-reserved + shared capacity is exhausted, even though the frozen
#: base GlobalBackPressureMiddleware admitted it (service-tiers TASK.md §3 M8/R4).
#: Distinct from ERR_OVERLOADED — this means "the base guard admitted you, but your
#: tier's pools are full cluster-wide". Retry-After always provided by the caller.
#: App/upstream NEVER invoked; no usage record is written for a shed request.
TIER_CAPACITY_EXHAUSTED = ErrorSpec(503, "ERR_TIER_CAPACITY_EXHAUSTED", "Tier capacity exhausted")

# ---------------------------------------------------------------------------
# Guardrail / content policy
# ---------------------------------------------------------------------------

#: Request blocked by an active guardrail policy.
GUARDRAIL_BLOCKED = ErrorSpec(400, "ERR_GUARDRAIL_BLOCKED", "Request blocked by guardrail policy")

# ---------------------------------------------------------------------------
# Payload capture (payload-capture-store TASK.md §3, FROZEN @ v1)
# ---------------------------------------------------------------------------

#: PUT /admin/capture {enabled: true} rejected — tenant is under Zero-Data-Retention.
#: Honest-degradation requirement: the toggle must never read as "on" when it can
#: never actually capture anything.
CAPTURE_ZDR_BLOCKED = ErrorSpec(
    409,
    "ERR_CAPTURE_ZDR_BLOCKED",
    "Cannot enable payload capture — tenant is under Zero-Data-Retention",
)

# ---------------------------------------------------------------------------
# Provider credentials (BYOK admin API — provider-config-admin-api §3 CONTRACT)
# ---------------------------------------------------------------------------

#: No stored credential for this tenant + provider (admin GET-status / DELETE).
PROVIDER_KEY_NOT_FOUND = ErrorSpec(
    404, "ERR_PROVIDER_KEY_NOT_FOUND", "No provider credential found for this tenant"
)

#: Path provider is not one of the supported BYOK providers.
PROVIDER_UNKNOWN = ErrorSpec(422, "ERR_PROVIDER_UNKNOWN", "Unknown or unsupported provider")

#: Credential body is missing required fields for the provider (and mode).
PROVIDER_CREDENTIAL_INCOMPLETE = ErrorSpec(
    422, "ERR_PROVIDER_CREDENTIAL_INCOMPLETE", "Incomplete or invalid credential for this provider"
)

#: Encryption key not set — cannot store provider credentials (mirrors OIDC's 409).
PROVIDER_KEY_ENCRYPTION_UNAVAILABLE = ErrorSpec(
    409,
    "ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE",
    "GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY must be set to store provider credentials",
)

# ---------------------------------------------------------------------------
# Edge input hardening (edge-input-hardening §3 CONTRACT — S3 SSRF/IMDS deny, S4 body caps)
# ---------------------------------------------------------------------------

#: Write-time literal check on a BYOK Azure endpoint/authority denied the host
#: (loopback / link-local / metadata / RFC1918 without opt-in, or a disallowed scheme).
PROVIDER_ENDPOINT_FORBIDDEN = ErrorSpec(
    422, "ERR_PROVIDER_ENDPOINT_FORBIDDEN", "Provider endpoint or authority is not permitted"
)

#: Request-time egress check denied a BYOK-influenced outbound dial (resolved address
#: is metadata/private/loopback without opt-in, or DNS resolution failed/timed out).
UPSTREAM_EGRESS_DENIED = ErrorSpec(
    502, "ERR_UPSTREAM_EGRESS_DENIED", "Upstream host is not permitted for outbound requests"
)

#: Request body exceeds the applicable app-level size cap (declared Content-Length or
#: actual streamed size) — S4 body-size guard.
REQUEST_BODY_TOO_LARGE = ErrorSpec(
    413, "ERR_REQUEST_BODY_TOO_LARGE", "Request body exceeds the maximum allowed size"
)

# ---------------------------------------------------------------------------
# Upstream / catalog service errors
# ---------------------------------------------------------------------------

#: Upstream LLM service returned HTTP 429 (rate-limited) after exhausting all retry attempts.
#: When the upstream supplied a Retry-After value the caller adds that header.
UPSTREAM_RATE_LIMITED = ErrorSpec(429, "ERR_UPSTREAM_RATE_LIMITED", "Upstream rate limit exceeded")

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

#: Resolved user's deactivated_at is set (scim-provisioning TASK.md §3, M7) — same
#: denial family as the password-login path; no session JWT is issued.
OIDC_ACCOUNT_DEACTIVATED = ErrorSpec(403, "ERR_OIDC_ACCOUNT_DEACTIVATED", "Account is deactivated")

# ---------------------------------------------------------------------------
# SAML 2.0 SSO errors (saml-sso task)
# ---------------------------------------------------------------------------

#: No enabled SAML config matches the requested domain (M1).
SAML_NOT_CONFIGURED = ErrorSpec(
    404, "ERR_SAML_NOT_CONFIGURED", "SAML login is not configured for this domain"
)

#: No pending-request record matches the (unverified) InResponseTo — includes
#: genuinely IdP-initiated, unsolicited responses (M6).
SAML_REQUEST_NOT_FOUND = ErrorSpec(
    400, "ERR_SAML_REQUEST_NOT_FOUND", "No matching SAML login request found"
)

#: The pending-request record was already consumed (M3 single-use).
SAML_REQUEST_ALREADY_USED = ErrorSpec(
    400, "ERR_SAML_REQUEST_ALREADY_USED", "SAML login request has already been used"
)

#: XML signature verification failed (M4).
SAML_SIGNATURE_INVALID = ErrorSpec(
    401, "ERR_SAML_SIGNATURE_INVALID", "SAML assertion signature validation failed"
)

#: Assertion/response Issuer does not match the tenant's configured idp_entity_id.
SAML_ISSUER_MISMATCH = ErrorSpec(401, "ERR_SAML_ISSUER_MISMATCH", "SAML issuer mismatch")

#: Audience does not match the tenant's sp_entity_id.
SAML_AUDIENCE_MISMATCH = ErrorSpec(401, "ERR_SAML_AUDIENCE_MISMATCH", "SAML audience mismatch")

#: The signed SubjectConfirmationData/@InResponseTo does not match the pending request_id (M5.4).
SAML_RESPONSE_MISMATCH = ErrorSpec(
    401, "ERR_SAML_RESPONSE_MISMATCH", "SAML response does not match the pending request"
)

#: NotBefore/NotOnOrAfter violated outside the configured clock-skew window (M5.5).
SAML_ASSERTION_EXPIRED = ErrorSpec(401, "ERR_SAML_ASSERTION_EXPIRED", "SAML assertion has expired")

#: Assertion @ID already present in the consumed-assertion cache (M5.6).
SAML_ASSERTION_REPLAYED = ErrorSpec(
    401, "ERR_SAML_ASSERTION_REPLAYED", "SAML assertion has already been used"
)

#: No email resolvable via the M13 ranked lookup.
SAML_EMAIL_MISSING = ErrorSpec(
    401, "ERR_SAML_EMAIL_MISSING", "SAML assertion has no resolvable email"
)

#: The verified email is bound to a different tenant.
SAML_TENANT_CONFLICT = ErrorSpec(
    403, "ERR_SAML_TENANT_CONFLICT", "Email is bound to a different tenant"
)

#: app.state.redis_client unreachable during the M3/M5.6 replay checks (M12, fail-CLOSED).
SAML_STORE_UNAVAILABLE = ErrorSpec(
    503, "ERR_SAML_STORE_UNAVAILABLE", "SAML login request store is temporarily unavailable"
)

#: No saml_provider_configs row found for the tenant (admin GET endpoint).
SAML_CONFIG_NOT_FOUND = ErrorSpec(
    404, "ERR_SAML_CONFIG_NOT_FOUND", "No SAML configuration found for this tenant"
)

#: PUT /admin/saml with an unparseable or expired idp_x509_cert.
SAML_CERT_INVALID = ErrorSpec(
    422, "ERR_SAML_CERT_INVALID", "SAML IdP certificate is invalid or expired"
)

#: PUT /admin/saml with an email_domains entry already claimed by a DIFFERENT
#: tenant's saml_provider_configs row — collision guard added post-freeze
#: (add-verify Finding 3, 2026-07-10: an unguarded collision crashes GET
#: /auth/saml/login with an unhandled MultipleResultsFound for every tenant
#: sharing the domain).
SAML_DOMAIN_ALREADY_CLAIMED = ErrorSpec(
    409,
    "ERR_SAML_DOMAIN_ALREADY_CLAIMED",
    "One or more email_domains are already claimed by another tenant's SAML configuration",
)

# ---------------------------------------------------------------------------
# Domain capture — verified email-domain claims (domain-capture TASK.md §3, FROZEN @ v1)
# ---------------------------------------------------------------------------

#: POST /admin/domain-claims with a malformed/single-label/IP-literal domain (M3, R1).
DOMAIN_INVALID = ErrorSpec(400, "ERR_DOMAIN_INVALID", "Domain is not a valid claimable hostname")

#: A DIFFERENT tenant already holds a verified claim on this domain — the M4 UX pre-check
#: (R2) AND the M6 cross-tenant verify-race rejection (R7, M1's partial unique index).
DOMAIN_ALREADY_VERIFIED = ErrorSpec(
    409,
    "ERR_DOMAIN_ALREADY_VERIFIED",
    "This domain is already verified by another tenant",
)

#: POST .../verify — the DNS TXT record was missing or did not match this claim's token (R5).
DOMAIN_VERIFICATION_FAILED = ErrorSpec(
    400,
    "ERR_DOMAIN_VERIFICATION_FAILED",
    "The expected DNS TXT record was not found or did not match",
)

#: POST .../verify after now > expires_at — caller must re-POST to reissue a fresh token (R6).
DOMAIN_CLAIM_EXPIRED = ErrorSpec(
    410,
    "ERR_DOMAIN_CLAIM_EXPIRED",
    "This verification challenge has expired; request a new one",
)

#: Unknown claim_id OR a claim_id belonging to a different tenant — deliberately
#: indistinguishable (R9), mirrors InviteNotFoundError's own precedent.
DOMAIN_CLAIM_NOT_FOUND = ErrorSpec(404, "ERR_DOMAIN_CLAIM_NOT_FOUND", "Domain claim not found")

#: DNS resolver error/NXDOMAIN/empty-answer/timeout — fail CLOSED, never a verified match (M13, R8).
DNS_LOOKUP_FAILED = ErrorSpec(
    503, "ERR_DNS_LOOKUP_FAILED", "DNS lookup failed or timed out; try again"
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

# ---------------------------------------------------------------------------
# Artifact file store errors (artifacts-backend task)
# ---------------------------------------------------------------------------

#: Artifact: content_base64 field could not be decoded as valid base64.
PAYLOAD_INVALID_BASE64 = ErrorSpec(
    422, "ERR_PAYLOAD_INVALID_BASE64", "content_base64 is not valid base64"
)

#: Artifact: uploaded content_type is not in the configured allow-list.
#: Dynamic: {content_type} — the value supplied by the caller (safe to echo).
ARTIFACT_CONTENT_TYPE_NOT_ALLOWED = ErrorSpec(
    415,
    "ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED",
    "content_type '{content_type}' is not allowed",
)

#: Object store (S3/MinIO) is unreachable or failed (timeout, transport, 5xx, breaker open).
#: v51 object-store-port. 503 — transient; the caller may retry.
OBJECT_STORE_UNAVAILABLE = ErrorSpec(
    503, "ERR_OBJECT_STORE_UNAVAILABLE", "object store is unavailable"
)

# ---------------------------------------------------------------------------
# Gemini multimodal / inline data errors
# ---------------------------------------------------------------------------

#: Gemini multimodal: a content part type is not text/image_url/video_url, or
#: a non-data: URL was provided (SSRF guard), or base64 is malformed.
#: 400 — caller-fixable; no echo of the bad URL (security).
UNSUPPORTED_CONTENT_PART = ErrorSpec(
    400, "ERR_UNSUPPORTED_CONTENT_PART", "Unsupported or invalid content part"
)

#: Capability-aware input-modality guard (unsupported-input-guard task §3).
#: Fired when a chat content-part or STT request requires an input modality the
#: resolved model's catalog entry does not support.  400 — caller-fixable; the
#: detail field names the offending type and the model's supported set.
UNSUPPORTED_INPUT_MODALITY = ErrorSpec(
    400,
    "ERR_UNSUPPORTED_INPUT_MODALITY",
    "Model does not accept '{input_type}' input",
)

#: Coarse operation-type guard (preset-capability-validation task §3).
#: Fired when a (preset-resolved or directly-named) model's catalog `modality`
#: (chat/embedding/image/audio_stt/audio_tts) does not match the endpoint's expected
#: operation type — e.g. a chat model resolved for /v1/images/generations.  400 —
#: caller-fixable; fired BEFORE select_provider/upstream/billing (single-bill invariant).
#: Applies to /v1/images/generations, /v1/embeddings, /v1/audio/speech (TTS) only — chat
#: has no ModelRow query in its seam, and STT is already safe by construction (see
#: gateway.proxy.infrastructure.openrouter_upstream_provider's post_multipart).
MODEL_MODALITY_MISMATCH = ErrorSpec(
    400,
    "ERR_MODEL_MODALITY_MISMATCH",
    "Model '{model_id}' does not support this operation",
)

# ---------------------------------------------------------------------------
# Video generation job errors (video-generation-jobs task)
# ---------------------------------------------------------------------------

#: Requested video generation job not found, or belongs to another tenant.
#: NEVER distinguish between "not found" and "belongs to another tenant" — no oracle.
VIDEO_JOB_NOT_FOUND = ErrorSpec(404, "ERR_VIDEO_JOB_NOT_FOUND", "Video generation job not found")

# ---------------------------------------------------------------------------
# Agent OAuth — device-authorization endpoint (device-authorization-endpoint task)
# ---------------------------------------------------------------------------

#: Per-IP fixed-window rate limit exceeded on POST /oauth/device/authorize.
#: ``Retry-After`` header is always set by the caller (seconds to window reset).
AGENT_OAUTH_RATE_LIMITED = ErrorSpec(
    429, "ERR_AGENT_OAUTH_RATE_LIMITED", "Device-authorization rate limit exceeded"
)

#: Transient device_code hash collision (astronomically rare CSPRNG clash).
#: Retryable — client should wait for ``Retry-After`` and re-submit.
#: ``Retry-After`` header is always set by the caller (poll-interval seconds).
AGENT_OAUTH_TEMPORARILY_UNAVAILABLE = ErrorSpec(
    503,
    "ERR_AGENT_OAUTH_TEMPORARILY_UNAVAILABLE",
    "Device authorization temporarily unavailable — please retry",
)

# ---------------------------------------------------------------------------
# Agent OAuth — device-approval-flow (device-approval-flow task)
# ---------------------------------------------------------------------------

#: user_code matches no pending authorization row.
AGENT_OAUTH_AUTHORIZATION_NOT_FOUND = ErrorSpec(
    404, "ERR_AGENT_OAUTH_AUTHORIZATION_NOT_FOUND", "Authorization not found"
)

#: Authorization exists but is not in 'pending' state (already approved/denied/consumed).
AGENT_OAUTH_NOT_PENDING = ErrorSpec(
    409, "ERR_AGENT_OAUTH_NOT_PENDING", "Authorization is not in pending state"
)

#: Authorization is pending but its expiry has passed.
AGENT_OAUTH_EXPIRED = ErrorSpec(410, "ERR_AGENT_OAUTH_EXPIRED", "Authorization has expired")

# ---------------------------------------------------------------------------
# Tenant model presets (tenant-preset-store task — store layer only, v56)
# ---------------------------------------------------------------------------

#: Preset upsert target_model is not an active model in the catalog.
#: Raised by DbTenantModelPresetStore.upsert as ModelPresetError; this entry
#: exists for the later HTTP admin-API task to surface it as problem+json.
PRESET_TARGET_UNKNOWN = ErrorSpec(
    400, "ERR_PRESET_TARGET_UNKNOWN", "Preset target model is not active in the catalog"
)

#: Preset selector token (preset_name / alias_key) is empty, over 64 chars, or
#: contains a colon. Raised by DbTenantModelPresetStore.upsert as
#: ModelPresetError; this entry exists for the later HTTP admin-API task.
PRESET_SELECTOR_INVALID = ErrorSpec(
    400, "ERR_PRESET_SELECTOR_INVALID", "Preset selector token is invalid"
)

#: model field named a `<preset>:<alias>` selector (colon present) but
#: TenantModelPresetStore.resolve(tenant_id, preset_name, alias_key) returned None
#: — no matching row for the calling tenant (preset-resolution-ingress task, v56).
#: Fires BEFORE any authorization/catalog/budget/billing/upstream call, at ingress,
#: for all 5 entry points (chat, images, embeddings, audio STT, audio TTS).
PRESET_NOT_FOUND = ErrorSpec(
    400, "ERR_PRESET_NOT_FOUND", "Preset selector did not resolve to a known model"
)

# ---------------------------------------------------------------------------
# Plan catalog (plan-catalog task — superadmin plan/tier assignment surface)
# ---------------------------------------------------------------------------

#: PUT .../plan — plan_id (non-null) does not resolve to a real plans row (R5). Follows
#: TENANT_NOT_FOUND/USER_NOT_FOUND's "admin action references an unknown id -> 404" idiom,
#: NOT PRESET_NOT_FOUND's 400 (that code fires on a proxy-hot-path RUNTIME resolution
#: failure — a different situation entirely).
PLAN_NOT_FOUND = ErrorSpec(404, "ERR_PLAN_NOT_FOUND", "Plan not found")

#: PUT .../plan — target tenant's kind == 'platform' (R4). The platform tenant can never
#: hold a plan; a DB-level CHECK (ck_tenants_platform_no_plan) is the defense-in-depth
#: backstop for this same invariant.
PLAN_TENANT_INELIGIBLE = ErrorSpec(
    403, "ERR_PLAN_TENANT_INELIGIBLE", "This tenant is not eligible for plan assignment"
)

# ---------------------------------------------------------------------------
# Plan enforcement (plan-enforcement task — request-time plan governance)
# ---------------------------------------------------------------------------

#: A model passes the existing key-level allowlist (or the key has none) but is outside
#: the tenant's ASSIGNED PLAN's model_allowlist (M4/R2). Distinct from MODEL_NOT_ALLOWED
#: (key-level) so billing-ui (wave-2) can render distinct upgrade messaging per dimension.
#: Always carries extra.upgrade_hint (plan_id, plan_name, model) — M9.
PLAN_MODEL_NOT_ALLOWED = ErrorSpec(
    403, "ERR_PLAN_MODEL_NOT_ALLOWED", "Model not permitted by this tenant's plan"
)

#: A tenant's assigned plan's feature_flags does not list the requested feature key
#: (M6/R3-R6): batch-policy write, ml_moderation guardrail write, logs-explorer query, or
#: realtime-relay connect. Always carries extra.upgrade_hint (plan_id, plan_name,
#: feature) — M9. Inert (never raised) for an unplanned tenant (plan_id IS NULL, M7).
PLAN_FEATURE_NOT_ENABLED = ErrorSpec(
    403, "ERR_PLAN_FEATURE_NOT_ENABLED", "This feature is not enabled on the tenant's plan"
)

#: Admitting one more active member would meet-or-exceed the tenant's effective seat cap
#: (plan-seat-cap TASK.md §3 M2/M5, FROZEN @ v1) — raised at 4 admission seams: invite
#: accept, OIDC/SAML JIT-provisioning (new-user branch only), domain-capture auto-join
#: signup. Always carries extra.upgrade_hint (plan_id, plan_name, seat_cap, current_seats).
#: The SCIM /scim/v2/Users seam raises `scim_seat_cap_exceeded()` instead (RFC 7644).
PLAN_SEAT_CAP_EXCEEDED = ErrorSpec(
    403, "ERR_PLAN_SEAT_CAP_EXCEEDED", "Adding this member would exceed the tenant's seat cap"
)

# ---------------------------------------------------------------------------
# Impersonation session lifecycle (impersonation-session-lifecycle task)
# ---------------------------------------------------------------------------

#: Mint — target tenant's kind == 'platform' (R4), OR target user's role is (defense-in-
#: depth) superadmin (R6). Same code for both — `detail` distinguishes the reason.
IMPERSONATION_TARGET_INVALID = ErrorSpec(
    403, "ERR_IMPERSONATION_TARGET_INVALID", "Target is not eligible for impersonation"
)

#: Mint — the calling superadmin already holds an active (revoked_at IS NULL AND
#: expires_at > now()) impersonation session (M7/R7). At most one at a time, per actor.
IMPERSONATION_SESSION_ALREADY_ACTIVE = ErrorSpec(
    409,
    "ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE",
    "An impersonation session is already active for this superadmin",
)

#: End — session_id never resolves to any minted row (R8).
IMPERSONATION_SESSION_NOT_FOUND = ErrorSpec(
    404, "ERR_IMPERSONATION_SESSION_NOT_FOUND", "Impersonation session not found"
)

#: End — session_id resolves, but revoked_at IS NOT NULL or expires_at <= now() already
#: (R9). Covers BOTH a repeat explicit End and a never-explicitly-ended-but-naturally-
#: expired session under one rule/code.
IMPERSONATION_SESSION_ALREADY_ENDED = ErrorSpec(
    409,
    "ERR_IMPERSONATION_SESSION_ALREADY_ENDED",
    "Impersonation session already ended or expired",
)

# ---------------------------------------------------------------------------
# Batch job store errors (batch-job-store task, v57)
# ---------------------------------------------------------------------------

#: Requested batch job not found, or belongs to another tenant.
#: NEVER distinguish between "not found" and "belongs to another tenant" — no oracle.
BATCH_JOB_NOT_FOUND = ErrorSpec(404, "ERR_BATCH_JOB_NOT_FOUND", "Batch job not found")

#: POST /v1/batches with an empty line_items array.
BATCH_ITEMS_EMPTY = ErrorSpec(422, "batch_items_empty", "line_items must not be empty")

#: POST /v1/batches with more line items than the configured cap
#: (Settings.batch_max_items_per_job). The cap is inclusive — reject fires on ">".
BATCH_ITEMS_TOO_MANY = ErrorSpec(
    422, "batch_items_too_many", "line_items exceeds the maximum allowed"
)

# ---------------------------------------------------------------------------
# Tenant retention window + Zero-Data-Retention (tenant-retention-zdr task)
# ---------------------------------------------------------------------------

#: PUT /admin/retention-policy — window_days is <= 0, non-integer, or exceeds
#: operator_ceiling_days (Settings.retention_tenant_window_ceiling_days).
RETENTION_WINDOW_INVALID = ErrorSpec(
    422,
    "ERR_RETENTION_WINDOW_INVALID",
    "window_days must be a positive integer within the operator ceiling",
)

#: A write to one of the 5 gated payload repositories while tenants.zdr_enabled=true
#: (read fresh per call, see gateway.tenants.application.retention_policy.raise_if_zdr).
#: The row is never created — not partially written, not written-then-deleted.
ZDR_PAYLOAD_BLOCKED = ErrorSpec(
    403,
    "ERR_ZDR_PAYLOAD_BLOCKED",
    "Zero-Data-Retention is enabled for this tenant; payload writes are blocked",
)

#: A line item failed validation: missing model, missing/empty messages, or a
#: custom_id that duplicates another item in the same submission. The whole
#: submission is atomic — no row is created on this reject.
BATCH_ITEM_INVALID = ErrorSpec(422, "batch_item_invalid", "a line item failed validation")

# ---------------------------------------------------------------------------
# Residency policy (residency-policy TASK.md — FROZEN @ v2)
# ---------------------------------------------------------------------------

#: PUT /admin/residency-policy — region is not one of {null, "us", "eu", "ap"}.
RESIDENCY_REGION_INVALID = ErrorSpec(
    422,
    "ERR_RESIDENCY_REGION_INVALID",
    "region must be one of null, 'us', 'eu', 'ap'",
)

#: No deployment-selection candidate satisfies the tenant's pinned residency region
#: (fresh per-call check, see gateway.proxy.application.residency). Raised at every
#: deployment-selection surface (chat alias/plain, embeddings, images, audio STT/TTS,
#: realtime relay/WS) — the request is refused, NEVER silently rerouted out-of-region
#: (M6). A refused request is never billed and produces no usage record (M8).
RESIDENCY_NO_ELIGIBLE_REGION = ErrorSpec(
    403,
    "ERR_RESIDENCY_NO_ELIGIBLE_REGION",
    "No deployment available in tenant's pinned region '{region}' for model '{model_id}'",
)

# ---------------------------------------------------------------------------
# Bedrock BYOK region guard (residency-bedrock-region-guard TASK.md §3 — FROZEN @ v1)
# ---------------------------------------------------------------------------

#: A Bedrock BYOK credential's AWS region geo (us-/eu-/ap-) does not match the
#: resolved model id's cross-region-inference-profile geo (us./eu./apac.) — raised
#: by ``_assert_region_consistent`` (bedrock_upstream.py) BEFORE any Bedrock HTTP
#: dial, at the top of BedrockCompletionUpstream.complete/.stream and
#: BedrockEmbeddingsProvider.post_json. Fail-closed: an unclassifiable/malformed
#: credential region under a geo-prefixed model id raises this too — never fails
#: open. The message names neither the secret nor the full credential, only the
#: required vs. actual region geo.
BEDROCK_REGION_MISMATCH = ErrorSpec(
    403,
    "ERR_BEDROCK_REGION_MISMATCH",
    "Bedrock credential region does not match the model's residency region",
)

# ---------------------------------------------------------------------------
# Compliance export errors (compliance-export-api TASK.md §3 — FROZEN @ v1)
# ---------------------------------------------------------------------------

#: GET /admin/audit/export?cursor=... is undecodable, malformed, or the wrong shape.
CURSOR_INVALID = ErrorSpec(422, "ERR_CURSOR_INVALID", "Export cursor is malformed or unrecognized")

#: GET /admin/audit/export's bounded DB read exceeded its time budget (M12).
EXPORT_QUERY_TIMEOUT = ErrorSpec(
    504,
    "ERR_EXPORT_TIMEOUT",
    "Export query exceeded the time budget; narrow the range or reduce limit",
)

# ---------------------------------------------------------------------------
# Invoice errors (invoice-generation TASK.md §3 — FROZEN @ v1)
# ---------------------------------------------------------------------------

#: Unknown invoice id, unknown line id, a line not owned by the invoice, or a
#: cross-tenant invoice/line — byte-identical across every unmatched-row cause
#: (no leak of "exists but not yours" vs "doesn't exist").
INVOICE_NOT_FOUND = ErrorSpec(404, "ERR_INVOICE_NOT_FOUND", "Invoice not found")

#: A list/detail/evidence read on GET /admin/invoices/* exceeded its bounded
#: asyncio.timeout query budget.
INVOICE_QUERY_TIMEOUT = ErrorSpec(
    504, "ERR_INVOICE_QUERY_TIMEOUT", "Invoice query exceeded its time budget"
)

#: RESERVED for a future mutating route (e.g. a manual-issue/admin-override
#: surface) — no v1 route can trigger this; M5 immutability is enforced by the
#: DB trigger itself (invoices/invoice_lines/invoice_corrections), tested
#: directly against the write path, not via an HTTP route.
INVOICE_IMMUTABLE = ErrorSpec(409, "ERR_INVOICE_IMMUTABLE", "Issued invoices cannot be modified")

# ---------------------------------------------------------------------------
# Seat-billing errors (seat-billing TASK.md §3 — FROZEN @ v2)
# ---------------------------------------------------------------------------

#: GET .../lines/{line_id}/seat-evidence called against a line whose
#: line_type == 'usage' (M12) — a 'usage' line has real evidence at the EXISTING
#: /evidence route; this is never a silent alias for it.
INVOICE_LINE_WRONG_TYPE = ErrorSpec(
    400, "ERR_INVOICE_LINE_WRONG_TYPE", "Evidence type does not match this line"
)

# ---------------------------------------------------------------------------
# Margin-dashboard errors (margin-dashboard TASK.md §3 — FROZEN @ v1)
# ---------------------------------------------------------------------------

#: One of the 4 GET /admin/platform/margin/* reads exceeded its bounded
#: asyncio.timeout query budget (M8).
MARGIN_QUERY_TIMEOUT = ErrorSpec(
    504, "ERR_MARGIN_QUERY_TIMEOUT", "Margin query exceeded its time budget"
)

# ---------------------------------------------------------------------------
# Output-schema validation errors (output-schema-validation task, TASK.md §3)
# ---------------------------------------------------------------------------

#: M1 gate engaged (operator flag ON + validate_output:true) + stream:true — the
#: retry loop has no buffered "final body" to validate-then-swap on a stream
#: (structurally out of reach, not a scoping preference). Never billed.
OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM = ErrorSpec(
    400,
    "ERR_OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM",
    "Output validation is not supported on streaming requests",
)

#: M1 gate engaged but response_format is absent, {type:"text"}, or
#: {type:"json_object"} — nothing to validate the output against. Never billed.
OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA = ErrorSpec(
    400,
    "ERR_OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA",
    "validate_output requires response_format.type == 'json_schema'",
)

#: M1 gate engaged and response_format.json_schema.schema fails JSON-Schema
#: meta-validation (M3, pre-flight — zero upstream calls) OR extract_response_format
#: itself raised ERR_INVALID_JSON_SCHEMA for a json_schema directive missing its
#: schema object. REUSES the v11 response_format_translation.py CODE STRING (same
#: class of problem, new trigger) per PROJECT.md v8's error-catalog-reuse convention
#: — response_format_translation.py itself has no HTTP-layer mapping today, so this
#: is this task's own first wiring of that code to an ErrorSpec, not a duplicate.
INVALID_JSON_SCHEMA = ErrorSpec(
    400, "ERR_INVALID_JSON_SCHEMA", "The provided JSON Schema is invalid"
)

#: Both attempt 1 and attempt 2 (the one bounded retry) failed validation (M5/M8
#: exhausted). Carries raw_output + validation_errors via ErrorSpec.exc(extra=...).
#: Both attempts' usage rows are still recorded (M8) — never a silent free retry.
OUTPUT_SCHEMA_VALIDATION_FAILED = ErrorSpec(
    422,
    "ERR_OUTPUT_SCHEMA_VALIDATION_FAILED",
    "Model output failed schema validation after 1 retry",
)

# ---------------------------------------------------------------------------
# SCIM token management (scim-provisioning task — /admin/scim/tokens, RFC 9457 side only;
# /scim/v2/* uses the separate SCIM error envelope — gateway.scim.api.errors)
# ---------------------------------------------------------------------------

#: /admin/scim/tokens/{id}/rotate|DELETE — unknown id, already revoked, or cross-tenant.
#: Deliberately indistinguishable (no oracle), mirrors INVITE_NOT_FOUND's own precedent.
SCIM_TOKEN_NOT_FOUND = ErrorSpec(404, "ERR_SCIM_TOKEN_NOT_FOUND", "SCIM token not found")

# ---------------------------------------------------------------------------
# Logs Explorer (logs-explorer-api TASK.md §3 — FROZEN @ v1)
# ---------------------------------------------------------------------------

#: GET /admin/logs/{log_id} — unknown id OR cross-tenant id. Deliberately indistinguishable
#: (no oracle), mirrors SCIM_TOKEN_NOT_FOUND / INVITE_NOT_FOUND's own precedent.
LOG_NOT_FOUND = ErrorSpec(404, "ERR_LOG_NOT_FOUND", "Request log not found")

#: GET /admin/logs's bounded DB read exceeded its time budget (mirrors EXPORT_QUERY_TIMEOUT).
LOGS_QUERY_TIMEOUT = ErrorSpec(
    504,
    "ERR_LOGS_QUERY_TIMEOUT",
    "Logs query exceeded the time budget; narrow the range or reduce limit",
)
