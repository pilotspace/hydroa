import json
import os
from collections.abc import Mapping
from typing import Annotated, Final

from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-only-secret-change-me"  # noqa: S105 — dev default; prod sets GATEWAY_JWT_SECRET


class EmptyUpstreamKeyError(ValueError):
    """Raised at BOOT when an upstream API key env var is present but empty.

    A fatal startup misconfiguration — never mapped to an HTTP status (the app must not
    start). Distinct from an ABSENT var, which cleanly disables that provider.
    """


#: Upstream API key env vars guarded at boot. A new provider MUST add its var here.
_UPSTREAM_KEY_ENV_VARS: Final[tuple[str, ...]] = (
    "GATEWAY_OPENROUTER_API_KEY",
    "GATEWAY_OPENAI_API_KEY",
    "GATEWAY_ANTHROPIC_API_KEY",
    "GATEWAY_GOOGLE_API_KEY",
)


def validate_upstream_keys(env: Mapping[str, str] | None = None) -> None:
    """Fail fast at boot if any upstream key env var is PRESENT but empty/whitespace.

    Only the raw environment can distinguish "configured-yet-empty" (a misconfiguration
    → boot failure) from "absent" (the provider is intentionally disabled → allowed),
    because Settings collapses both to "". An empty key would otherwise reach an adapter
    as ``Bearer ''`` and surface as an opaque per-request 500 (the v7+v8 live failure).

    The error names ONLY the offending variable + a fix hint — never a key value.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    for name in _UPSTREAM_KEY_ENV_VARS:
        if name in environ and environ[name].strip() == "":
            raise EmptyUpstreamKeyError(
                f"{name} is set but empty; unset it to disable the provider or provide a "
                f"non-empty key"
            )


class Deployment(BaseModel):
    """One normalized member of a model group (deployment-model TASK.md §3 FROZEN @ v1).

    A model group's members are Deployments: a concrete model_id plus an optional
    weight (default 1) and optional tpm/rpm limits. A bare model-id string in
    GATEWAY_MODEL_GROUPS coerces to Deployment(model_id, weight=1, tpm_limit=None,
    rpm_limit=None), so the v6 all-string config is byte-identical.

    Immutable value object. weight/tpm/rpm are CARRIED here; the routing strategy
    (routing-strategy) and limit enforcement (deployment-limits) are later v8 tasks.
    `protected_namespaces=()` allows the `model_id` field name (mirrors how
    Settings already carries `model_groups`).
    """

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    # validate_default=True so a deployment object that omits model_id (default "")
    # still triggers the non-empty check (DEPLOYMENT_MODEL_ID_REQUIRED) rather than
    # silently constructing an id-less deployment.
    model_id: str = Field(default="", validate_default=True)
    weight: int = 1
    tpm_limit: int | None = None
    rpm_limit: int | None = None

    @field_validator("model_id")
    @classmethod
    def _require_model_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "DEPLOYMENT_MODEL_ID_REQUIRED: a deployment must have a non-empty model_id"
            )
        return v

    @field_validator("weight")
    @classmethod
    def _positive_weight(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(
                f"INVALID_DEPLOYMENT_WEIGHT: weight must be a positive integer, got {v}"
            )
        return v

    @field_validator("tpm_limit", "rpm_limit")
    @classmethod
    def _positive_limit(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError(
                f"INVALID_DEPLOYMENT_LIMIT: tpm_limit/rpm_limit must be positive when set, got {v}"
            )
        return v


def _coerce_deployment(value: object) -> object:
    """BeforeValidator: a bare string member becomes a Deployment dict (weight-1, no limits)."""
    if isinstance(value, str):
        return {"model_id": value}
    return value


# A model-group member: a bare string (coerced) or a deployment object.
DeploymentSpec = Annotated[Deployment, BeforeValidator(_coerce_deployment)]


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

    # ── OpenRouter upstream base URL (v6-live-verify task) ──────────────────────
    # GATEWAY_OPENROUTER_BASE_URL — base URL for OpenRouterCompletionUpstream.
    # Default is byte-identical to the prior module constant (_BASE_URL).
    # Override in e2e overlays to point the gateway at the fault stub.
    # NEVER set to a non-https URL in production deployments.
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # ── OpenAI direct provider (provider-seam task) ───────────────────────────
    # GATEWAY_OPENAI_API_KEY — secret; empty = OpenAI provider absent from registry.
    # Treated as a secret: NEVER logged, echoed, committed, or placed in metric
    # labels/span attributes.  Follows the same handling as openrouter_api_key.
    openai_api_key: str = ""
    # GATEWAY_OPENAI_BASE_URL — Override in e2e overlays to point at a stub.
    # NEVER set to a non-https URL in production deployments.
    openai_base_url: str = "https://api.openai.com/v1"

    # ── Anthropic direct provider (provider-chat-dispatch task) ──────────────
    # GATEWAY_ANTHROPIC_API_KEY — secret; empty = Anthropic provider absent.
    # Treated as a secret: NEVER logged, echoed, committed, or placed in metric
    # labels/span attributes.
    anthropic_api_key: str = ""
    # GATEWAY_ANTHROPIC_BASE_URL — Override in e2e overlays to point at a stub.
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    # GATEWAY_ANTHROPIC_VERSION — Anthropic-Version header value.
    anthropic_version: str = "2023-06-01"
    # GATEWAY_ANTHROPIC_DEFAULT_MAX_TOKENS — default max_tokens for Anthropic requests
    # when the OpenAI caller omits max_tokens (Anthropic requires this field).
    anthropic_default_max_tokens: int = 4096

    # ── Google direct provider (provider-chat-dispatch task) ──────────────────
    # GATEWAY_GOOGLE_API_KEY — secret; empty = Google provider absent.
    # Treated as a secret: NEVER logged, echoed, committed, or placed in metric
    # labels/span attributes.
    google_api_key: str = ""
    # GATEWAY_GOOGLE_BASE_URL — Override in e2e overlays to point at a stub.
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # GATEWAY_GOOGLE_DEFAULT_MAX_TOKENS — default max_tokens for Gemini requests
    # when the OpenAI caller omits max_tokens.
    google_default_max_tokens: int = 4096

    # ── Upstream retry policy (retry-policy task) ─────────────────────────────
    # GATEWAY_UPSTREAM_MAX_RETRIES — max additional retry attempts after first failure.
    # Default 0 = opt-in (byte-identical to v5 "NEVER retry" behavior at default settings).
    # Valid range: 0..5. Values outside this range raise ValidationError at startup.
    # With max_retries=5 and base=0.5s the expected worst-case delay budget is ~18 s
    # (sum of backoff caps), leaving ~102 s of actual request time within the 120 s envelope.
    upstream_max_retries: int = Field(default=0, ge=0, le=5)
    # GATEWAY_UPSTREAM_RETRY_BACKOFF_BASE_S — base for exponential backoff (seconds).
    upstream_retry_backoff_base_s: float = Field(default=0.5, gt=0)

    # ── Per-model cooldown circuit breaker (cooldown-circuit task) ──────────────
    # GATEWAY_COOLDOWN_FAILURE_THRESHOLD — number of consecutive failures that trip
    # the cooldown for a model. 0 = disabled (feature off, v5 byte-identical behavior).
    cooldown_failure_threshold: int = Field(default=0, ge=0, le=100)
    # GATEWAY_COOLDOWN_TTL_S — seconds the cooldown open flag lives; also probe token TTL.
    cooldown_ttl_s: int = Field(default=60, ge=1, le=3600)
    # GATEWAY_COOLDOWN_WINDOW_S — failure counter expiry window (sliding; NX-set on first INCR).
    cooldown_window_s: int = Field(default=60, ge=1, le=3600)

    # ── Model-group aliases → ordered Deployments (model-fallbacks v6 + deployment-model v8) ──
    # GATEWAY_MODEL_GROUPS — JSON dict mapping alias string to an ordered member list.
    # A member is EITHER a bare model-id string (v6 shape) OR a deployment object:
    #   {"model_id": "vendor/model", "weight": 3, "tpm_limit": 100000, "rpm_limit": 600}
    # A bare string coerces to Deployment(model_id, weight=1, tpm_limit=None, rpm_limit=None),
    # so the v6 all-string config is byte-identical.
    #   e.g. GATEWAY_MODEL_GROUPS='{"fast": ["vendor/a:free", {"model_id":"vendor/b","weight":2}]}'
    # Default {} = feature off, v5/v6 byte-identical behavior.
    # `deployments` is the canonical normalized view; `model_groups` (property) is the
    # bare-string view that the FallbackModelRouter, /admin/routing (RA1/RA8), and the
    # alias-aware catalog check read — kept byte-identical to v6.
    # Bound to the SAME env var (GATEWAY_MODEL_GROUPS) and the `model_groups=` init kwarg
    # via validation_alias, so every existing caller and overlay keeps working unchanged.
    deployments: dict[str, list[DeploymentSpec]] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("model_groups", "GATEWAY_MODEL_GROUPS"),
    )

    # ── Routing strategy (routing-strategy v8) ────────────────────────────────
    # GATEWAY_ROUTING_STRATEGY — how a model group's PRIMARY deployment is selected.
    #   "ordered"        — declared order (default; v6 byte-identical)
    #   "simple-shuffle" — weighted-random primary by Deployment.weight, rest as fallback tail
    # Unknown value → ValidationError "UNKNOWN_ROUTING_STRATEGY" (fail-closed at startup).
    routing_strategy: str = "ordered"

    @property
    def model_groups(self) -> dict[str, list[str]]:
        """Bare-string view of the deployment groups (alias -> [model_id, ...], order-preserved).

        v6 byte-identical: this is what create_app passes to the router, what
        /admin/routing returns, and what the alias-aware catalog check iterates.
        """
        return {
            alias: [d.model_id for d in deployments]
            for alias, deployments in self.deployments.items()
        }

    # ── Load-balancing knobs (balance-strategies v8) ──────────────────────────
    # GATEWAY_LOADBAL_EWMA_ALPHA — smoothing factor for the per-deployment latency
    # EWMA (0 < alpha <= 1). Default 0.3 (moderate smoothing). Used only when
    # routing_strategy in {least-busy, latency}.
    loadbal_ewma_alpha: float = 0.3
    # GATEWAY_LOADBAL_INFLIGHT_TTL_S — TTL in seconds for the per-deployment
    # in-flight counter key in Redis. A missed release self-heals within this
    # window. Must be > 0. Default 60.
    loadbal_inflight_ttl_s: int = 60

    @model_validator(mode="after")
    def _validate_loadbal_alpha(self) -> "Settings":
        """Reject an out-of-range GATEWAY_LOADBAL_EWMA_ALPHA at startup."""
        if not (0.0 < self.loadbal_ewma_alpha <= 1.0):
            raise ValueError(
                f"INVALID_LOADBAL_ALPHA: loadbal_ewma_alpha must be in (0, 1], "
                f"got {self.loadbal_ewma_alpha}"
            )
        return self

    @model_validator(mode="after")
    def _validate_loadbal_ttl(self) -> "Settings":
        """Reject a non-positive GATEWAY_LOADBAL_INFLIGHT_TTL_S at startup."""
        if self.loadbal_inflight_ttl_s <= 0:
            raise ValueError(
                f"INVALID_LOADBAL_TTL: loadbal_inflight_ttl_s must be > 0, "
                f"got {self.loadbal_inflight_ttl_s}"
            )
        return self

    @model_validator(mode="after")
    def _validate_routing_strategy(self) -> "Settings":
        """Reject an unknown GATEWAY_ROUTING_STRATEGY at startup (fail-closed)."""
        valid = {"ordered", "simple-shuffle", "least-busy", "latency"}
        if self.routing_strategy not in valid:
            raise ValueError(
                f"UNKNOWN_ROUTING_STRATEGY: '{self.routing_strategy}' is not one of {sorted(valid)}"
            )
        return self

    @model_validator(mode="after")
    def _validate_model_groups(self) -> "Settings":
        """Validate the normalized deployment groups at startup (fail-closed).

        Per-member rules (INVALID_DEPLOYMENT_WEIGHT / INVALID_DEPLOYMENT_LIMIT /
        DEPLOYMENT_MODEL_ID_REQUIRED) fire during Deployment field validation. This
        after-validator enforces the cross-member rules over the model_id view:
          - EMPTY_CANDIDATE_LIST       — a group with no members
          - DUPLICATE_DEPLOYMENT       — same model_id twice in one group (ambiguous target)
          - TOO_MANY_CANDIDATES        — > 5 members (bounds per-request catalog cost; v6)
          - ALIAS_COLLIDES_WITH_CANDIDATE — an alias key also used as a member model_id (v6)
        """
        groups = self.deployments
        if not groups:
            return self

        # Collect all member model_ids across all groups for the collision check.
        all_member_ids: set[str] = set()
        for deployments in groups.values():
            for dep in deployments:
                all_member_ids.add(dep.model_id)

        errors: list[str] = []
        for alias, deployments in groups.items():
            member_ids = [dep.model_id for dep in deployments]
            if len(member_ids) == 0:
                errors.append(
                    f"EMPTY_CANDIDATE_LIST: alias '{alias}' must have at least one candidate"
                )
                continue
            if len(member_ids) > 5:
                errors.append(
                    f"TOO_MANY_CANDIDATES: alias '{alias}' has {len(member_ids)} candidates"
                    f" (at most 5 allowed — bounds per-request catalog-validation cost)"
                )
            if len(set(member_ids)) != len(member_ids):
                errors.append(
                    f"DUPLICATE_DEPLOYMENT: alias '{alias}' lists the same model_id more than once"
                )
            if alias in all_member_ids:
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
