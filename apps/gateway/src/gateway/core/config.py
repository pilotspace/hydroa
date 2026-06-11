import json

from pydantic import model_validator
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

    # ── OIDC SSO (sso-oidc task) ─────────────────────────────────────────────
    oidc_enabled: bool = False  # GATEWAY_OIDC_ENABLED
    oidc_issuer: str = ""  # GATEWAY_OIDC_ISSUER (e.g. https://accounts.google.com)
    oidc_authorize_url: str = ""  # GATEWAY_OIDC_AUTHORIZE_URL (optional)
    oidc_client_id: str = ""  # GATEWAY_OIDC_CLIENT_ID
    oidc_client_secret: str = ""  # GATEWAY_OIDC_CLIENT_SECRET (treated as secret; never logged)
    oidc_redirect_uri: str = ""  # GATEWAY_OIDC_REDIRECT_URI
    oidc_domain_mapping: str = "[]"  # GATEWAY_OIDC_DOMAIN_MAPPING (JSON list)
    oidc_post_login_redirect: str = "/"  # GATEWAY_OIDC_POST_LOGIN_REDIRECT

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
