import json
from decimal import Decimal, InvalidOperation
from typing import Annotated

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
    # ── Operator-wide reconciliation ops-auth (operator-wide-reconciliation task) ──
    # CSV allow-list of SHA-256 client-cert fingerprints (hex) trusted as the platform
    # operator on /ops/*. The cert is validated by Envoy (mTLS) and forwarded via the
    # x-forwarded-client-cert (XFCC) header. Default "" = OFF / fail-closed: the ops
    # surface authorizes NO ONE until an operator fingerprint is provisioned.
    ops_cert_fingerprints: str = Field(default="")  # GATEWAY_OPS_CERT_FINGERPRINTS
    redis_url: str = "redis://localhost:6380/0"
    shutdown_drain_timeout_seconds: int = 10  # env: GATEWAY_SHUTDOWN_DRAIN_TIMEOUT_SECONDS

    # ── Response cache (response-caching task) ────────────────────────────────
    cache_ttl_seconds: int = 300  # GATEWAY_CACHE_TTL_SECONDS
    # Cap for a per-request Cache-Control: max-age override (cache-controls task)
    cache_max_ttl_seconds: int = Field(default=86400)  # GATEWAY_CACHE_MAX_TTL_SECONDS

    # ── Embedding-similarity "vector" cache (semantic-cache task, v19) ─────────
    # The THIRD lookup layer (after exact + normalization): a near-duplicate prompt whose
    # embedding is within GATEWAY_VECTOR_CACHE_THRESHOLD cosine of a cached prompt serves a
    # cached response. Default-off ⇒ byte-identical. Per-tenant + per-model isolated.
    # env: GATEWAY_VECTOR_CACHE_ENABLED
    vector_cache_enabled: bool = Field(default=False)
    # Cosine hit threshold (0..1). High default = conservative (near-identical only); a false
    # hit serves a wrong-but-plausible answer, strictly worse than a miss. Operator-tunable.
    # env: GATEWAY_VECTOR_CACHE_THRESHOLD
    vector_cache_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    # Embedding model used to vectorize prompts (routed through the gateway's own embedding
    # upstream). Required (non-empty) to activate; the embed call is internal, never billed.
    # env: GATEWAY_VECTOR_CACHE_EMBED_MODEL
    vector_cache_embed_model: str = ""
    # Upper bound on candidate vectors scanned per (tenant, model) lookup — bounds latency.
    # env: GATEWAY_VECTOR_CACHE_MAX_CANDIDATES
    vector_cache_max_candidates: int = Field(default=100, ge=1, le=1000)

    # ── Alerting / health (health-alerting task) ──────────────────────────────
    alert_webhook_url: str = ""  # GATEWAY_ALERT_WEBHOOK_URL (empty = disabled)
    alert_retry_max: int = 3  # GATEWAY_ALERT_RETRY_MAX
    health_check_interval_seconds: int = 60  # GATEWAY_HEALTH_CHECK_INTERVAL_SECONDS (0 = disabled)

    # ── Reconciliation drift-alert (v29 drift-alert task) ─────────────────────
    # Fire a deduped operator-wide alert when a day's unbilled-upstream cost
    # (provider_cost>0 ∧ billed=0) exceeds this absolute-USD threshold. Both knobs
    # default to the OFF position — the checker is only started when BOTH are > 0.
    reconciliation_drift_threshold: Decimal = Decimal("0")  # GATEWAY_RECONCILIATION_DRIFT_THRESHOLD
    reconciliation_check_interval_seconds: int = 0  # GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS

    @field_validator("reconciliation_drift_threshold", mode="before")
    @classmethod
    def _validate_drift_threshold(cls, v: object) -> object:
        """Fail loud on a nonsense drift threshold (v30 drift-threshold-validation).

        A non-finite (inf/-inf/nan) or negative threshold is a misconfiguration, not a
        disable signal — 0 is the OFF sentinel. Rejecting at startup turns the silent-but-
        useless monitor (inf passes the `>0` start-guard yet can never fire) into a clear
        boot error. mode="before" so this coded error wins over Pydantic's generic
        finite_number coercion error for inf/nan.
        """
        try:
            d = Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError):
            return v  # not a parseable number — let Pydantic raise its normal decimal error
        if not d.is_finite() or d < 0:
            raise ValueError(
                "INVALID_RECONCILIATION_DRIFT_THRESHOLD: GATEWAY_RECONCILIATION_DRIFT_THRESHOLD "
                f"must be a finite, non-negative USD amount (0 disables); got {v!r}"
            )
        return v

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

    # ── Per-tenant provider credentials (provider-credential-store task) ─────
    # GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY — Fernet key (base64url); required for upsert/get.
    # Kept separate from GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY for blast-radius isolation:
    # a compromised OIDC key does not expose upstream provider secrets, and vice versa.
    provider_key_encryption_key: str = ""

    # ── Per-tenant credential resolution (credential-resolution-seam task) ───────
    # GATEWAY_PROVIDER_CREDENTIAL_CACHE_TTL_S — TTL in seconds for the in-memory
    # positive-result credential cache. Default 60 s. Only positive hits are cached
    # (miss NOT cached so a freshly-configured key takes effect immediately).
    provider_credential_cache_ttl_s: float = 60.0
    # GATEWAY_PROVIDER_CREDENTIAL_RESOLVE_TIMEOUT_S — bounded asyncio timeout on the
    # cold store.get() DB fetch. Timeout fails CLOSED: raises ProviderKeyMissing.
    provider_credential_resolve_timeout_s: float = 2.0

    # ── OpenRouter upstream base URL (v6-live-verify task) ──────────────────────
    # GATEWAY_OPENROUTER_BASE_URL — base URL for OpenRouterCompletionUpstream.
    # Default is byte-identical to the prior module constant (_BASE_URL).
    # Override in e2e overlays to point the gateway at the fault stub.
    # NEVER set to a non-https URL in production deployments.
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # ── OpenAI direct provider (provider-seam task) ───────────────────────────
    # GATEWAY_OPENAI_BASE_URL — Override in e2e overlays to point at a stub.
    # NEVER set to a non-https URL in production deployments.
    openai_base_url: str = "https://api.openai.com/v1"

    # ── Anthropic direct provider (provider-chat-dispatch task) ──────────────
    # GATEWAY_ANTHROPIC_BASE_URL — Override in e2e overlays to point at a stub.
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    # GATEWAY_ANTHROPIC_VERSION — Anthropic-Version header value.
    anthropic_version: str = "2023-06-01"
    # GATEWAY_ANTHROPIC_DEFAULT_MAX_TOKENS — default max_tokens for Anthropic requests
    # when the OpenAI caller omits max_tokens (Anthropic requires this field).
    anthropic_default_max_tokens: int = 4096

    # ── Google direct provider (provider-chat-dispatch task) ──────────────────
    # GATEWAY_GOOGLE_BASE_URL — Override in e2e overlays to point at a stub.
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # GATEWAY_GOOGLE_DEFAULT_MAX_TOKENS — default max_tokens for Gemini requests
    # when the OpenAI caller omits max_tokens.
    google_default_max_tokens: int = 4096

    # ── AWS Bedrock direct provider (bedrock-sigv4 task) ──────────────────────
    # Secret fields (bedrock_access_key_id, bedrock_secret_access_key,
    # bedrock_session_token) REMOVED in task-3 (dynamic-auth-byok): credentials
    # are now resolved per-tenant at request time via the contextvar seam.
    # GATEWAY_BEDROCK_REGION — kept as a harmless boot default / test override;
    # the per-tenant BedrockCredential.region takes precedence at request time.
    bedrock_region: str = "us-east-1"
    # GATEWAY_BEDROCK_ENDPOINT_URL — override endpoint for tests/e2e overlays.
    # Empty = derive from credential region at request time.
    bedrock_endpoint_url: str = ""

    # ── Azure OpenAI direct provider (azure-auth-routing task) ────────────────
    # Secret fields (azure_api_key, azure_client_secret) REMOVED in task-3
    # (dynamic-auth-byok): credentials are now resolved per-tenant via the seam.
    # Descriptive fields kept as harmless boot defaults / test overrides.
    # GATEWAY_AZURE_ENDPOINT — resource endpoint, e.g. "https://r.openai.azure.com".
    azure_endpoint: str = ""
    # GATEWAY_AZURE_API_VERSION — required api-version query param on every call.
    azure_api_version: str = "2024-10-21"
    # GATEWAY_AZURE_DEPLOYMENT_MAP — JSON object mapping client model -> Azure
    # deployment name. Empty = identity routing (model name == deployment name).
    azure_deployment_map: dict[str, str] = Field(default_factory=dict)
    # ── Azure AD (client-credentials) auth — descriptive knobs ────────────────
    # GATEWAY_AZURE_TENANT_ID / GATEWAY_AZURE_CLIENT_ID — kept as boot defaults.
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    # GATEWAY_AZURE_AD_SCOPE — OAuth2 scope override (empty = cognitive-services default).
    azure_ad_scope: str = ""
    # GATEWAY_AZURE_AD_AUTHORITY — AAD authority host for sovereign/government clouds.
    azure_ad_authority: str = ""

    # ── Per-tenant Azure AD token provider cache (dynamic-auth-byok task-3) ───
    # GATEWAY_AZURE_AD_PROVIDER_CACHE_TTL_S — per-entry TTL in seconds.
    # Controls how fast a rotated client_secret takes effect (bounded staleness).
    azure_ad_provider_cache_ttl_s: float = 300.0
    # GATEWAY_AZURE_AD_PROVIDER_CACHE_MAX — soft size cap (oldest-created evicted).
    azure_ad_provider_cache_max: int = 512

    # ── Upstream retry policy (retry-policy task) ─────────────────────────────
    # GATEWAY_UPSTREAM_MAX_RETRIES — max additional retry attempts after first failure.
    # Default 0 = opt-in (byte-identical to v5 "NEVER retry" behavior at default settings).
    # Valid range: 0..5. Values outside this range raise ValidationError at startup.
    # With max_retries=5 and base=0.5s the expected worst-case delay budget is ~18 s
    # (sum of backoff caps), leaving ~102 s of actual request time within the 120 s envelope.
    upstream_max_retries: int = Field(default=0, ge=0, le=5)
    # GATEWAY_UPSTREAM_RETRY_BACKOFF_BASE_S — base for exponential backoff (seconds).
    upstream_retry_backoff_base_s: float = Field(default=0.5, gt=0)
    # GATEWAY_UPSTREAM_RETRY_DEADLINE_S — cumulative wall-clock budget across all retry
    # attempts + backoff sleeps (v19 retry-seam-unify). 0 = disabled (no deadline; default,
    # byte-identical). When > 0, the loop stops before an attempt whose backoff would exceed it.
    upstream_retry_deadline_s: float = Field(default=0.0, ge=0)
    # GATEWAY_UPSTREAM_FALLBACK_ON_ERROR — when True, an alias-routed request whose
    # candidate returns a context-window-exceeded OR content-policy-blocked 4xx falls over
    # to the next deployment in its model-group (v19 error-aware-fallback). Default False =
    # opt-in (byte-identical to v6: a 4xx is returned to the client after the first candidate).
    # The existing UpstreamUnavailableError (retry-exhausted) fallover is unaffected by this knob.
    upstream_fallback_on_error: bool = Field(default=False)
    # GATEWAY_STREAM_RESILIENCE_ENABLED — when True, an alias-routed STREAMING request whose
    # candidate fails BEFORE the first SSE byte (transport error / circuit-open) falls over to
    # the next deployment in its model-group (v19 streaming-resilience). Default False = opt-in
    # (byte-identical: the first candidate only, no stream fallover). Once a byte reaches the
    # client the stream is committed — no replay. The retry seam stays complete()-only.
    upstream_stream_resilience_enabled: bool = Field(default=False)
    # GATEWAY_OPENROUTER_USAGE_ACCOUNTING — when True, the OpenRouter upstream injects
    # usage={"include": true} into the outbound request so OpenRouter returns its own
    # reported cost, which the recorder then bills on (cost_basis="provider"). Default
    # False = opt-in (byte-identical outbound request; recorder falls back to catalog).
    # provider-cost-reconciliation TASK.md §3 (knob frozen default-OFF, Tin 2026-06-17).
    openrouter_usage_accounting: bool = Field(default=False)
    # GATEWAY_OPENROUTER_COST_RECOVERY_ENABLED — when True, an OpenRouter stream aborted by
    # client disconnect schedules an inline fire-and-forget authoritative-cost recovery
    # (OpenRouterCostRecoveryService) from the disconnect handler. Default False = opt-in
    # (byte-identical streaming; no recovery scheduled). The periodic sweep (v30 t6.3) is
    # the reliable backstop for whatever inline misses. openrouter-cost-recovery-wiring §3.
    openrouter_cost_recovery_enabled: bool = Field(default=False)

    # GATEWAY_OPENROUTER_RECOVERY_SWEEP_INTERVAL_SECONDS — when > 0 (and the cost-recovery
    # service is wired), a periodic background sweeper (OpenRouterRecoverySweeper) scans the
    # ledger for flushed client_disconnect rows that still have no openrouter_recovered
    # sibling and calls recover() for each OpenRouter one — the reliable backstop for inline
    # misses (teardown-cancelled / knob-off rows). 0 = default-OFF (no task). The sweep is
    # idempotent with the inline path via the deterministic correction-row id (t6.2b).
    # openrouter-recovery-sweep §3.
    openrouter_recovery_sweep_interval_seconds: int = Field(default=0)

    # GATEWAY_STT_MAX_DURATION_SECONDS — upper clamp (seconds) on a billed STT per_second
    # duration. A corrupt/lying audio header (or a lying upstream body["duration"]) can
    # over-derive an absurd duration → over-bill; the resolved duration is clamped to this
    # max (+ a stt_duration_capped WARN) before billing. A normal file (< cap) bills
    # byte-identically. 14400 = 4h (a generous real-world ceiling). gt=0 → a non-positive
    # value fails fast at config load. (stt-duration-cap TASK.md §3, default frozen by Tin.)
    stt_max_duration_seconds: float = Field(default=14400.0, gt=0)

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
