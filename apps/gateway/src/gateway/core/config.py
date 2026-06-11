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

    @model_validator(mode="after")
    def _forbid_dev_secret_outside_dev(self) -> "Settings":
        if self.environment not in ("dev", "test") and self.jwt_secret == _DEV_JWT_SECRET:
            raise ValueError(
                "GATEWAY_JWT_SECRET must be set when GATEWAY_ENVIRONMENT is not dev/test"
            )
        return self
