import json

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-only-secret-change-me"  # noqa: S105 — dev default; prod sets GATEWAY_JWT_SECRET


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    environment: str = "dev"
    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
    jwt_secret: str = _DEV_JWT_SECRET
    jwt_ttl_seconds: int = 86400
    jwt_issuer: str = "ai-proxy"
    openrouter_api_key: str = ""  # Required in production; empty default for dev/test
    redis_url: str = "redis://localhost:6380/0"
    shutdown_drain_timeout_seconds: int = 10  # env: GATEWAY_SHUTDOWN_DRAIN_TIMEOUT_SECONDS

    # ── Response cache (response-caching task) ────────────────────────────────
    cache_ttl_seconds: int = 300  # GATEWAY_CACHE_TTL_SECONDS

    # ── Alerting / health (health-alerting task) ──────────────────────────────
    alert_webhook_url: str = ""  # GATEWAY_ALERT_WEBHOOK_URL (empty = disabled)
    alert_retry_max: int = 3  # GATEWAY_ALERT_RETRY_MAX
    health_check_interval_seconds: int = 60  # GATEWAY_HEALTH_CHECK_INTERVAL_SECONDS (0 = disabled)

    # ── OpenTelemetry trace export (obs-callbacks task) ──────────────────────
    otel_enabled: bool = False  # GATEWAY_OTEL_ENABLED
    otel_export_url: str = ""  # GATEWAY_OTEL_EXPORT_URL (required when enabled)
    otel_service_name: str = "hydroa-gateway"  # GATEWAY_OTEL_SERVICE_NAME
    otel_flush_interval_seconds: float = 5.0  # GATEWAY_OTEL_FLUSH_INTERVAL_SECONDS
    otel_queue_max: int = 2048  # GATEWAY_OTEL_QUEUE_MAX

    # ── OIDC SSO (sso-oidc task) ─────────────────────────────────────────────
    oidc_enabled: bool = False  # GATEWAY_OIDC_ENABLED
    oidc_issuer: str = ""  # GATEWAY_OIDC_ISSUER (e.g. https://accounts.google.com)
    oidc_authorize_url: str = ""  # GATEWAY_OIDC_AUTHORIZE_URL (optional)
    oidc_client_id: str = ""  # GATEWAY_OIDC_CLIENT_ID
    oidc_client_secret: str = ""  # GATEWAY_OIDC_CLIENT_SECRET (treated as secret; never logged)
    oidc_redirect_uri: str = ""  # GATEWAY_OIDC_REDIRECT_URI
    oidc_domain_mapping: str = "[]"  # GATEWAY_OIDC_DOMAIN_MAPPING (JSON list)
    oidc_post_login_redirect: str = "/"  # GATEWAY_OIDC_POST_LOGIN_REDIRECT
    oidc_jwks_url: str = ""  # GATEWAY_OIDC_JWKS_URL (optional; enables RS256 JWKS verification)

    # ── Per-tenant OIDC config (oidc-tenant-config task) ─────────────────────
    # GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY — Fernet key (base64url); required for PUT /admin/oidc
    oidc_config_encryption_key: str = ""
    # GATEWAY_OIDC_ALLOW_HTTP_URLS — dev/test only; never True in production
    oidc_allow_http_urls: bool = False

    # ── Upstream retry policy (retry-policy task) ─────────────────────────────
    # GATEWAY_UPSTREAM_MAX_RETRIES — max additional retry attempts after first failure.
    # Default 0 = opt-in (byte-identical to v5 "NEVER retry" behavior at default settings).
    # Valid range: 0..5. Values outside this range raise ValidationError at startup.
    # With max_retries=5 and base=0.5s the expected worst-case delay budget is ~18 s
    # (sum of backoff caps), leaving ~102 s of actual request time within the 120 s envelope.
    upstream_max_retries: int = Field(default=0, ge=0, le=5)
    # GATEWAY_UPSTREAM_RETRY_BACKOFF_BASE_S — base for exponential backoff (seconds).
    upstream_retry_backoff_base_s: float = Field(default=0.5, gt=0)

    # ── Model-group aliases with ordered candidate fallbacks (model-fallbacks task) ──
    # GATEWAY_MODEL_GROUPS — JSON dict mapping alias string to ordered candidate list.
    # e.g. GATEWAY_MODEL_GROUPS='{"fast": ["vendor/model-a:free", "vendor/model-b"]}'
    # Default {} = feature off, v5 byte-identical behavior.
    # Validators (model_validator, mode="after"):
    #   1. All candidate lists must be non-empty → ValidationError "EMPTY_CANDIDATE_LIST"
    #   2. No alias key may appear as a candidate id in ANY group
    #      → ValidationError "ALIAS_COLLIDES_WITH_CANDIDATE"
    #   3. No candidate list may exceed 5 entries → ValidationError "TOO_MANY_CANDIDATES"
    #      (bounds the per-request catalog-validation cost per §3 CATALOG INTERACTION)
    model_groups: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_model_groups(self) -> "Settings":
        """Validate model_groups at startup (§3 SETTINGS validators 1-3).

        1. Empty candidate list → ValidationError "EMPTY_CANDIDATE_LIST"
        2. Alias key collides with any candidate id in any group
           → ValidationError "ALIAS_COLLIDES_WITH_CANDIDATE"
        3. Candidate list exceeds 5 entries → ValidationError "TOO_MANY_CANDIDATES"
        """
        groups = self.model_groups
        if not groups:
            return self

        # Collect all candidate ids across all groups for collision check.
        all_candidate_ids: set[str] = set()
        for candidates in groups.values():
            for cid in candidates:
                all_candidate_ids.add(cid)

        errors: list[str] = []
        for alias, candidates in groups.items():
            if len(candidates) == 0:
                errors.append(
                    f"EMPTY_CANDIDATE_LIST: alias '{alias}' must have at least one candidate"
                )
                continue
            if len(candidates) > 5:
                errors.append(
                    f"TOO_MANY_CANDIDATES: alias '{alias}' has {len(candidates)} candidates"
                    f" (at most 5 allowed — bounds per-request catalog-validation cost)"
                )
            if alias in all_candidate_ids:
                errors.append(
                    f"ALIAS_COLLIDES_WITH_CANDIDATE: alias '{alias}' collides with a"
                    f" candidate id in the same or another group"
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_otel_config(self) -> "Settings":
        """If otel_enabled=True, otel_export_url must be non-empty (startup guard)."""
        if self.otel_enabled and not self.otel_export_url:
            raise ValueError("GATEWAY_OTEL_EXPORT_URL must be set when GATEWAY_OTEL_ENABLED=true")
        return self

    @model_validator(mode="after")
    def _forbid_dev_secret_outside_dev(self) -> "Settings":
        if self.environment not in ("dev", "test") and self.jwt_secret == _DEV_JWT_SECRET:
            raise ValueError(
                "GATEWAY_JWT_SECRET must be set when GATEWAY_ENVIRONMENT is not dev/test"
            )
        return self

    @model_validator(mode="after")
    def _validate_oidc_config(self) -> "Settings":
        """If OIDC is enabled, required fields must be non-empty and domain_mapping valid JSON."""
        if self.oidc_enabled:
            missing = [
                field
                for field, value in [
                    ("oidc_issuer", self.oidc_issuer),
                    ("oidc_client_id", self.oidc_client_id),
                    ("oidc_client_secret", self.oidc_client_secret),
                    ("oidc_redirect_uri", self.oidc_redirect_uri),
                ]
                if not value
            ]
            if missing:
                raise ValueError(
                    f"GATEWAY_OIDC_ENABLED=true requires non-empty: {', '.join(missing)}"
                )
        # Always validate domain mapping JSON
        try:
            json.loads(self.oidc_domain_mapping)
        except json.JSONDecodeError as exc:
            raise ValueError(f"GATEWAY_OIDC_DOMAIN_MAPPING is not valid JSON: {exc}") from exc
        return self
