import json
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
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
    # ── Usage flusher PEL reclaim (usage-flusher-durability B5) ────────────────
    # XAUTOCLAIM min-idle (ms): an entry delivered by XREADGROUP but never ACKed
    # (consumer crashed mid-flush) is reclaimed after this idle so it is re-flushed
    # instead of stranded forever. Should exceed a normal flush cycle so a SINGLE
    # consumer's still-in-flight entry is not reclaimed under it. NOTE: under multiple
    # replicas sharing CONSUMER_NAME ('flusher-0'), idle time does not track one
    # consumer's liveness, so a slow replica's entry CAN be reclaimed + reprocessed by
    # another — harmless (ON CONFLICT id makes the re-insert a no-op, XACK is idempotent),
    # but a per-replica consumer name would be stronger (spec-delta).
    # env: GATEWAY_USAGE_PEL_RECLAIM_IDLE_MS
    usage_pel_reclaim_idle_ms: int = 60000

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

    @field_validator("reconciliation_check_interval_seconds")
    @classmethod
    def _validate_check_interval(cls, v: int) -> int:
        """Fail loud on a negative check-interval (v33 drift-threshold-validation).

        Mirrors `_validate_drift_threshold`: a negative seconds interval is a typo, not a
        disable signal — 0 is the OFF sentinel. Today `should_start_drift_checker`'s `> 0`
        silently treats a negative interval as OFF; rejecting at startup turns that silent
        misconfiguration into a clear boot error. A plain after-validator suffices — an int
        carries no inf/nan, so there is no coercion race like the Decimal threshold's.
        """
        if v < 0:
            raise ValueError(
                "INVALID_RECONCILIATION_CHECK_INTERVAL: "
                "GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS must be a non-negative "
                f"number of seconds (0 disables); got {v!r}"
            )
        return v

    # ── OpenTelemetry trace export (obs-callbacks task) ──────────────────────
    otel_enabled: bool = False  # GATEWAY_OTEL_ENABLED
    otel_export_url: str = ""  # GATEWAY_OTEL_EXPORT_URL (required when enabled)
    otel_service_name: str = "hydroa-gateway"  # GATEWAY_OTEL_SERVICE_NAME
    otel_flush_interval_seconds: float = 5.0  # GATEWAY_OTEL_FLUSH_INTERVAL_SECONDS
    otel_queue_max: int = 2048  # GATEWAY_OTEL_QUEUE_MAX

    # ── Public signup (signup-and-routing-authz S1) ──────────────────────────
    # Default OFF (invite-only): the unauthenticated POST /admin/auth/signup
    # new-tenant path is refused unless an operator explicitly opts in. A fresh
    # deploy bootstraps its first tenant by temporarily flipping this on.
    public_signup_enabled: bool = False  # GATEWAY_PUBLIC_SIGNUP_ENABLED

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

    # ── SAML 2.0 SSO (saml-sso task) ──────────────────────────────────────────
    # All optional; absence => SAML fully inert (M11: DB-config-only, no env fallback).
    saml_sp_entity_id_base: str = ""  # GATEWAY_SAML_SP_ENTITY_ID_BASE (e.g. https://gw.example.com/saml/sp)
    saml_acs_url: str = ""  # GATEWAY_SAML_ACS_URL (full external URL to /auth/saml/acs)
    saml_post_login_redirect: str = "/"  # GATEWAY_SAML_POST_LOGIN_REDIRECT
    saml_clock_skew_seconds: int = 60  # GATEWAY_SAML_CLOCK_SKEW_SECONDS
    saml_allow_http_urls: bool = False  # GATEWAY_SAML_ALLOW_HTTP_URLS (dev/test only)

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

    # ── MiniMax direct provider (minimax-adapter-registry task) ──────────────
    # GATEWAY_MINIMAX_BASE_URL — Override in e2e overlays to point at a stub.
    # NEVER set to a non-https URL in production deployments. BYOK-only — no
    # operator-level API key Settings field (dynamic-auth-byok precedent).
    minimax_base_url: str = "https://api.minimax.io/v1"

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
    # GATEWAY_GEMINI_INLINE_MAX_BYTES — maximum total decoded bytes for inline
    # data (images/video) per request.  0 = unlimited.  Default 20 MiB matches
    # the Gemini inline ceiling.
    gemini_inline_max_bytes: int = Field(default=20_971_520, ge=0)  # 20 MiB

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
    # GATEWAY_ANTHROPIC_AUTO_CACHE — when True (default), the Anthropic adapter
    # auto-injects cache_control:{type:"ephemeral"} on the stable prefix (system block +
    # last tool definition) when the client has NOT supplied any cache_control markers.
    # A model below Anthropic's min cacheable length simply won't cache (no error).
    # Set False to opt out (byte-identical request; no cache breakpoints added).
    # prompt-cache-passthrough TASK.md §3 (knob frozen default-ON, Tin 2026-06-23).
    anthropic_auto_cache: bool = Field(default=True)

    # GATEWAY_OPENROUTER_USAGE_ACCOUNTING — when True, the OpenRouter upstream injects
    # usage={"include": true} into the outbound request so OpenRouter returns its own
    # reported cost, which the recorder then bills on (cost_basis="provider"). Default
    # False = opt-in (byte-identical outbound request; recorder falls back to catalog).
    # provider-cost-reconciliation TASK.md §3 (knob frozen default-OFF, Tin 2026-06-17).
    openrouter_usage_accounting: bool = Field(default=False)

    # GATEWAY_WEB_SEARCH_ENABLED — when True, a client-supplied web_search:true flag in
    # the chat-completions body is translated into each provider's NATIVE web-search /
    # grounding tool before the upstream call; when False (default), the flag is stripped
    # centrally (before dispatch) so the outgoing upstream body is byte-identical to today.
    # Non-grounding providers (bedrock/azure) inject nothing and never raise.
    # web-search-grounding TASK.md §3 (knob frozen default-OFF).
    web_search_enabled: bool = Field(default=False)

    # GATEWAY_INPUT_MODALITY_GUARD_ENABLED — when True, a request whose required input
    # types (derived from chat messages content-parts or audio modality) exceed the
    # resolved model's catalog input_modalities is rejected with 400
    # ERR_UNSUPPORTED_INPUT_MODALITY BEFORE any upstream call, bandwidth acquire, or usage
    # record — a refused request is never billed. Default False = opt-in (byte-identical:
    # no lookup, no rejection; frozen router + every existing proxy/audio test unchanged).
    # unsupported-input-guard TASK.md §3 (knob frozen default-OFF).
    input_modality_guard_enabled: bool = Field(default=False)
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

    # ── TTS input-length ceiling (tts-input-guardrails task) ─────────────────────
    # GATEWAY_TTS_MAX_INPUT_CHARACTERS — default-ON cap on the TTS `input` length.
    # TTS bills per_character at-start (before streaming), so an unbounded input is a
    # runaway-billing / abuse vector. Over-cap → 413 PAYLOAD_INPUT_TOO_LONG raised
    # BEFORE governance/upstream/bill (no partial charge). DEFAULT-SAFE: 4096 mirrors
    # OpenAI's documented limit; ge=0 with 0 ⇒ DISABLED (operator escape hatch). A
    # within-cap request is byte-identical to today. (tts-input-guardrails TASK.md §3.)
    tts_max_input_characters: int = Field(default=4096, ge=0)

    # ── Global back-pressure / concurrency cap (concurrency-load-guard task) ──────
    # GATEWAY_MAX_CONCURRENT_REQUESTS — per-worker global cap on simultaneous in-flight
    # HTTP requests. 0 (default) = disabled = today's unbounded behavior (opt-in, byte-
    # identical). When > 0, the GlobalBackPressureMiddleware admits at most this many
    # concurrent requests per worker; excess requests receive 503 + Retry-After immediately
    # without invoking the downstream app. Total cluster cap = workers x this value.
    # A negative value is treated as 0 (disabled) + WARN at startup.
    max_concurrent_requests: int = Field(default=0)  # GATEWAY_MAX_CONCURRENT_REQUESTS
    # GATEWAY_BACK_PRESSURE_RETRY_AFTER_SECONDS — value of the Retry-After header on 503
    # shed responses. Default 1 second.
    back_pressure_retry_after_seconds: int = Field(
        default=1
    )  # GATEWAY_BACK_PRESSURE_RETRY_AFTER_SECONDS

    # ── Video generation jobs (video-generation-jobs task) ───────────────────
    # GATEWAY_VIDEO_JOB_TIMEOUT_SECONDS — per-job asyncio.wait_for timeout for the
    # video generator call. 0 = unlimited (no timeout). Default 300 s (5 minutes).
    # ge=0 so a negative value fails at config load.
    video_job_timeout_seconds: float = Field(default=300.0, ge=0)
    # GATEWAY_VIDEO_DURABLE_QUEUE_ENABLED — when True, video generation jobs are
    # enqueued to a Redis list (video:jobs:pending) and drained by an in-process
    # VideoJobWorker rather than a fire-and-forget asyncio.create_task. Jobs survive
    # gateway restarts; orphaned non-terminal rows are re-enqueued on startup.
    # Default False = opt-in (byte-identical to v48: inline asyncio.create_task).
    # Fail-open: if the Redis enqueue raises, the router falls back to the inline task.
    video_durable_queue_enabled: bool = Field(default=False)
    # GATEWAY_VIDEO_JOB_MAX_RETRIES — maximum number of times the durable worker will
    # attempt a job before setting status=failed error=max_retries_exceeded. Each
    # attempt increments retry_count on the row; when retry_count > this value the
    # job is poisoned. Default 3. **0 = UNLIMITED** (the codebase convention for a
    # 0-valued cap — matches video_job_timeout_seconds / *_max_bytes); a fresh job
    # is therefore always attempted at least once.
    video_job_max_retries: int = Field(default=3, ge=0)

    # ── Batch job store (batch-job-store task, v57) ───────────────────────────
    # GATEWAY_BATCH_DURABLE_QUEUE_ENABLED — when True, batch jobs are enqueued to a
    # Redis list (batch:jobs:pending) and drained by an in-process BatchJobWorker
    # rather than a fire-and-forget asyncio.create_task. Jobs survive gateway
    # restarts; orphaned non-terminal rows are re-enqueued on startup.
    # Default False = opt-in (mirrors video_durable_queue_enabled exactly).
    # Fail-open: if the Redis enqueue raises, the router falls back to the inline task.
    batch_durable_queue_enabled: bool = Field(default=False)
    # GATEWAY_BATCH_JOB_MAX_RETRIES — maximum number of times the durable worker will
    # attempt a job before setting status=failed error=max_retries_exceeded. **0 =
    # UNLIMITED** (the codebase convention for a 0-valued cap). Default 3.
    batch_job_max_retries: int = Field(default=3, ge=0)
    # GATEWAY_BATCH_JOB_TIMEOUT_SECONDS — per-job asyncio.wait_for timeout for the
    # BatchProcessor.process() call. 0 = unlimited (no timeout). Default 300 s.
    batch_job_timeout_seconds: float = Field(default=300.0, ge=0)
    # GATEWAY_BATCH_MAX_ITEMS_PER_JOB — the maximum number of line items a single
    # POST /v1/batches submission may contain. Default 500 for this MVP shell (well
    # under real provider limits of 50k/100k) — operator-configurable. Inclusive cap
    # (reject fires on ">"). Also doubles as BatchWindowBuffer's early-flush size cap
    # (batch-window-grouping): a window flushes as soon as its accumulated item count
    # reaches this value, even if batch_window_seconds has not yet elapsed.
    batch_max_items_per_job: int = Field(default=500, ge=1)
    # GATEWAY_BATCH_WINDOW_SECONDS — batch-window-grouping: the fixed-tick accumulation
    # window (seconds) for BatchWindowBuffer/BatchWindowFlusher. A tenant's FIRST
    # eligible request opens the window; it flushes as one multi-item batch job when
    # EITHER this many seconds have elapsed since that first arrival OR
    # batch_max_items_per_job items have accumulated, whichever comes first — a later
    # arrival in the same window never resets it (fixed-tick, not debounce). Read
    # fresh per-call (never snapshotted at construction) so it can be tuned live and
    # shrunk in tests after app construction. Default 3.0s.
    batch_window_seconds: float = Field(default=3.0, ge=0)

    @field_validator("back_pressure_retry_after_seconds", mode="before")
    @classmethod
    def _coerce_negative_retry_after(cls, v: object) -> object:
        """Coerce a negative GATEWAY_BACK_PRESSURE_RETRY_AFTER_SECONDS to 0 + emit a WARNING.

        A negative Retry-After value is a misconfiguration — RFC 7231 requires the header
        to be a non-negative integer. Rather than crashing startup with a validation error,
        the gateway coerces to 0 (no Retry-After delay hint) and emits a WARNING so the
        operator notices. Mirrors the max_concurrent_requests coercion convention.
        """
        import logging as _logging

        try:
            n = int(v)  # type: ignore[arg-type]  # try/except is the guard
        except (TypeError, ValueError):
            return v  # not an int — let Pydantic raise its normal type error
        if n < 0:
            _logging.getLogger(__name__).warning(
                "INVALID_BACK_PRESSURE_RETRY_AFTER_SECONDS: "
                "GATEWAY_BACK_PRESSURE_RETRY_AFTER_SECONDS=%r is negative; "
                "coercing to 0 (no Retry-After delay hint). "
                "Set to a non-negative integer for a valid RFC Retry-After value.",
                v,
            )
            return 0
        return n

    @field_validator("max_concurrent_requests", mode="before")
    @classmethod
    def _coerce_negative_max_concurrent(cls, v: object) -> object:
        """Coerce a negative GATEWAY_MAX_CONCURRENT_REQUESTS to 0 + emit a startup WARNING.

        A negative concurrency cap is a misconfiguration, not a disable signal — 0 is the
        OFF sentinel. Rather than crashing startup, the gateway coerces to 0 (disabled) and
        emits a WARNING so the operator notices. This matches the opt-in default convention
        (cooldown/deployment-limit) while failing loudly enough to be actionable.
        """
        import logging as _logging

        try:
            n = int(v)  # type: ignore[arg-type]  # try/except is the guard
        except (TypeError, ValueError):
            return v  # not an int — let Pydantic raise its normal type error
        if n < 0:
            _logging.getLogger(__name__).warning(
                "INVALID_MAX_CONCURRENT_REQUESTS: GATEWAY_MAX_CONCURRENT_REQUESTS=%r is negative; "
                "coercing to 0 (disabled). Set to a positive integer to enable back-pressure.",
                v,
            )
            return 0
        return n

    # ── Per-key bandwidth pacing (bandwidth-token-bucket task, v36) ─────────────
    # GATEWAY_BANDWIDTH_TOKENS_PER_SEC — per-API-key throughput ceiling (tokens/sec) the
    # aggregate Redis token-bucket paces toward. 0 (default) = disabled = today's unbounded
    # behavior (opt-in, byte-identical; the stream seam wires PassthroughBandwidthBucket).
    # A negative value is a misconfiguration → coerced to 0 (disabled) + WARN at startup.
    bandwidth_tokens_per_sec: int = Field(default=0)  # GATEWAY_BANDWIDTH_TOKENS_PER_SEC
    # GATEWAY_BANDWIDTH_BURST_TOKENS — bucket capacity (max level). 0 (default) ⇒ when the
    # rate is enabled, the bucket defaults burst to the rate (a 1-second burst window).
    bandwidth_burst_tokens: int = Field(default=0)  # GATEWAY_BANDWIDTH_BURST_TOKENS
    # GATEWAY_BANDWIDTH_MAX_WAIT_SECONDS — bounded-wait budget the stream seam passes to
    # acquire(); once a grant would need more waiting than this, the request is shed (503).
    # 0.0 (default) = no pacing wait. A negative value is coerced to 0.0 + WARN.
    bandwidth_max_wait_seconds: float = Field(default=0.0)  # GATEWAY_BANDWIDTH_MAX_WAIT_SECONDS

    @field_validator("bandwidth_tokens_per_sec", "bandwidth_burst_tokens", mode="before")
    @classmethod
    def _coerce_negative_bandwidth_int(cls, v: object) -> object:
        """Coerce a negative bandwidth token/burst knob to 0 (disabled) + WARN.

        A negative ceiling/capacity is a misconfiguration, not a disable signal — 0 is the
        OFF sentinel. Mirrors the max_concurrent_requests coercion convention.
        """
        import logging as _logging

        try:
            n = int(v)  # type: ignore[arg-type]  # try/except is the guard
        except (TypeError, ValueError):
            return v
        if n < 0:
            _logging.getLogger(__name__).warning(
                "INVALID_BANDWIDTH_KNOB: a GATEWAY_BANDWIDTH_* token/burst value=%r is negative; "
                "coercing to 0 (disabled). Set to a positive integer to enable bandwidth pacing.",
                v,
            )
            return 0
        return n

    @field_validator("bandwidth_max_wait_seconds", mode="before")
    @classmethod
    def _coerce_negative_bandwidth_wait(cls, v: object) -> object:
        """Coerce a negative GATEWAY_BANDWIDTH_MAX_WAIT_SECONDS to 0.0 + WARN."""
        import logging as _logging

        try:
            f = float(v)  # type: ignore[arg-type]  # try/except is the guard
        except (TypeError, ValueError):
            return v
        if f < 0:
            _logging.getLogger(__name__).warning(
                "INVALID_BANDWIDTH_MAX_WAIT_SECONDS: GATEWAY_BANDWIDTH_MAX_WAIT_SECONDS=%r is "
                "negative; coercing to 0.0 (no pacing wait).",
                v,
            )
            return 0.0
        return f

    # ── Tenant data retention & purge controls (data-retention-controls task) ───
    # Periodic RetentionSweeper deletes aged rows from time-series tables in bounded
    # batches. ALL windows default to non-zero (active by default — Tin-approved 2026-06-25).
    # 0 = OFF for that knob. GATEWAY_RETENTION_CHECK_INTERVAL_SECONDS=0 disables entirely.
    # env: GATEWAY_RETENTION_CHECK_INTERVAL_SECONDS
    retention_check_interval_seconds: int = Field(default=86400)
    # env: GATEWAY_RETENTION_USAGE_RECORDS_DAYS (0=skip)
    retention_usage_records_days: int = Field(default=365)
    # env: GATEWAY_RETENTION_ALERT_EVENTS_DAYS (0=skip)
    retention_alert_events_days: int = Field(default=90)
    # env: GATEWAY_RETENTION_AUDIT_EVENTS_DAYS (0=skip); EFFECTIVE=max(knob,floor)
    retention_audit_events_days: int = Field(default=730)
    # env: GATEWAY_RETENTION_AUDIT_FLOOR_DAYS (HARD floor — audit window never smaller)
    retention_audit_floor_days: int = Field(default=365)
    # env: GATEWAY_RETENTION_BATCH_SIZE (bounds each DELETE)
    retention_batch_size: int = Field(default=1000)

    # ── Per-model cooldown circuit breaker (cooldown-circuit task) ──────────────
    # GATEWAY_COOLDOWN_FAILURE_THRESHOLD — number of consecutive failures that trip
    # the cooldown for a model. 0 = disabled (feature off, v5 byte-identical behavior).
    cooldown_failure_threshold: int = Field(default=0, ge=0, le=100)
    # GATEWAY_COOLDOWN_TTL_S — seconds the cooldown open flag lives; also probe token TTL.
    cooldown_ttl_s: int = Field(default=60, ge=1, le=3600)
    # GATEWAY_COOLDOWN_WINDOW_S — failure counter expiry window (sliding; NX-set on first INCR).
    cooldown_window_s: int = Field(default=60, ge=1, le=3600)

    # ── Memory domain (memory-store task) ────────────────────────────────────
    # GATEWAY_MEMORY_EMBEDDING_MODEL — model id routed through the gateway's own embedding
    # upstream to vectorize memory content. Empty (default) = embedding disabled; POST still
    # returns 201 with embedding=NULL. Only populate when a real embedding model is available.
    memory_embedding_model: str = Field(default="")  # GATEWAY_MEMORY_EMBEDDING_MODEL
    # GATEWAY_MEMORY_SEARCH_DEFAULT_TOP_K — default number of results returned by
    # POST /v1/memories/search when top_k is not supplied by the caller. Clamped 1..100.
    memory_search_default_top_k: int = Field(
        default=5, ge=1, le=100
    )  # GATEWAY_MEMORY_SEARCH_DEFAULT_TOP_K

    # ── Artifact file store (artifacts-backend task) ──────────────────────────
    # GATEWAY_ARTIFACT_MAX_BYTES — per-artifact size cap (decoded bytes). 0 = disabled (no limit).
    # Default 10 MiB. Reject BEFORE insert (no partial write).
    artifact_max_bytes: int = Field(default=10_485_760, ge=0)  # GATEWAY_ARTIFACT_MAX_BYTES
    # GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES — comma-separated media-type allow-list.
    # Default "" = allow any content_type (byte-identical to pre-policy behaviour).
    # Non-empty: normalize(content_type) must be in the normalized set; else 415.
    # NOTE: kept as str (not list[str]) — pydantic-settings parses complex env types
    # as JSON, so a bare CSV env var would raise; str avoids that trap.
    artifact_allowed_content_types: str = Field(default="")

    # ── Object store (S3/MinIO) for artifact bytes (v51 object-store-port) ─────
    # Unset/incomplete -> build_object_store() returns None -> artifacts honest-degrade
    # to inline Postgres BYTEA (the v45 behavior). Set ALL of enabled/endpoint/bucket/
    # access-key/secret-key to route bytes through the object store instead.
    object_store_enabled: bool = False  # GATEWAY_OBJECT_STORE_ENABLED
    object_store_endpoint: str = ""  # GATEWAY_OBJECT_STORE_ENDPOINT  e.g. http://localhost:9000
    object_store_bucket: str = ""  # GATEWAY_OBJECT_STORE_BUCKET
    object_store_region: str = "us-east-1"  # GATEWAY_OBJECT_STORE_REGION
    object_store_access_key_id: str = ""  # GATEWAY_OBJECT_STORE_ACCESS_KEY_ID
    # GATEWAY_OBJECT_STORE_SECRET_ACCESS_KEY — masked at rest; never logged/repr'd.
    object_store_secret_access_key: SecretStr = SecretStr("")
    # GATEWAY_OBJECT_STORE_TIMEOUT_SECONDS — per-op connect+read timeout. Never a hang.
    object_store_timeout_seconds: float = Field(default=5.0, gt=0)
    # GATEWAY_OBJECT_STORE_MAX_RETRIES — bounded retry for IDEMPOTENT reads only (0 = off).
    object_store_max_retries: int = Field(default=2, ge=0)

    # ── Realtime WebSocket voice endpoint (/v1/realtime) ─────────────────────
    # GATEWAY_REALTIME_AUTH_TIMEOUT_SECONDS — how long the server waits for the
    # first {"type":"auth"} frame before closing with code 4408.  ge=0 allows
    # a zero timeout (immediate; useful in tests).  Default 10 s.
    realtime_auth_timeout_seconds: float = Field(default=10.0, ge=0)
    # GATEWAY_REALTIME_MAX_UTTERANCE_BYTES — per-turn audio buffer ceiling.
    # A commit whose accumulated audio exceeds this limit → error "utterance_too_large"
    # (no STT call, no billing).  0 = unlimited (operator opt-out).  Default 25 MiB.
    realtime_max_utterance_bytes: int = Field(default=26_214_400, ge=0)  # 25 MiB
    # ── Full-duplex realtime relay (v52) — /v1/realtime/relay pump ──
    # GATEWAY_REALTIME_RELAY_CONNECT_TIMEOUT_SECONDS — bounds the provider connect
    # AND each provider send; a hang past this → close 4503 (provider unavailable).
    realtime_relay_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    # GATEWAY_REALTIME_RELAY_IDLE_TIMEOUT_SECONDS — no client frame within this →
    # close 4408 (idle). No send-queue knob: client→provider is a direct awaited relay.
    realtime_relay_idle_timeout_seconds: float = Field(default=300.0, gt=0)
    # GATEWAY_REALTIME_RELAY_PROVIDER — which realtime provider /v1/realtime/relay dials.
    # "" = none configured → honest-degrade close 4404; else "openai" | "gemini".
    realtime_relay_provider: str = Field(default="")
    # gpt-realtime-pricing-fields TASK.md §3: switched from the older "gpt-4o-realtime-preview"
    # to the current GA "gpt-realtime" model (Tin 2026-07-02, AskUserQuestion) — see
    # catalog/infrastructure/gpt_realtime_seed.py for the corresponding pricing seed.
    realtime_relay_openai_model: str = Field(default="gpt-realtime")
    realtime_relay_gemini_model: str = Field(default="gemini-2.0-flash-exp")

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

    # ── Agent OAuth device-authorization endpoint (device-authorization-endpoint task) ──
    # GATEWAY_AGENT_OAUTH_VERIFICATION_URI — dashboard URL shown to the human approver.
    # Empty string = unconfigured (verification_uri_complete is omitted from the 200 body).
    agent_oauth_verification_uri: str = ""  # GATEWAY_AGENT_OAUTH_VERIFICATION_URI
    # GATEWAY_AGENT_OAUTH_DEVICE_CODE_TTL_SECONDS — how long a pending device_code lives.
    # Must be > 0; fails fast at boot when set to 0 or negative.
    agent_oauth_device_code_ttl_seconds: int = 600  # GATEWAY_AGENT_OAUTH_DEVICE_CODE_TTL_SECONDS
    # GATEWAY_AGENT_OAUTH_POLL_INTERVAL_SECONDS — minimum polling interval (RFC 8628 §3.5).
    # Must be > 0; fails fast at boot when set to 0 or negative.
    agent_oauth_poll_interval_seconds: int = 5  # GATEWAY_AGENT_OAUTH_POLL_INTERVAL_SECONDS
    # GATEWAY_AGENT_OAUTH_DEFAULT_SCOPE — scope assigned when the caller omits it.
    agent_oauth_default_scope: str = "proxy"  # GATEWAY_AGENT_OAUTH_DEFAULT_SCOPE
    # GATEWAY_AGENT_OAUTH_AUTHORIZE_RPM — per-IP fixed-window rate limit (requests/60 s).
    # Must be > 0; fails fast at boot when set to 0 or negative.
    agent_oauth_authorize_rpm: int = 12  # GATEWAY_AGENT_OAUTH_AUTHORIZE_RPM
    # GATEWAY_AGENT_OAUTH_APPROVE_RPM — per-USER fixed-window rate limit on approve/deny
    # (requests/60 s). Bounds user_code enumeration by an authenticated actor. Must be > 0;
    # fails fast at boot when set to 0 or negative. (device-approval-flow task §3)
    agent_oauth_approve_rpm: int = 30  # GATEWAY_AGENT_OAUTH_APPROVE_RPM

    # ── Agent OAuth token endpoint (agent-token-endpoint task) ──────────────────
    # GATEWAY_AGENT_OAUTH_ACCESS_TOKEN_TTL_SECONDS — lifetime of a minted access token.
    # Must be > 0; fails fast at boot when set to 0 or negative.
    agent_oauth_access_token_ttl_seconds: int = 3600  # GATEWAY_AGENT_OAUTH_ACCESS_TOKEN_TTL_SECONDS
    # GATEWAY_AGENT_OAUTH_REFRESH_TOKEN_TTL_SECONDS — lifetime of a minted refresh token.
    # 0 = disabled (no refresh token issued); >=0. Set to 0 to opt out of refresh tokens.
    agent_oauth_refresh_token_ttl_seconds: int = (
        2592000  # GATEWAY_AGENT_OAUTH_REFRESH_TOKEN_TTL_SECONDS
    )
    # GATEWAY_AGENT_OAUTH_TOKEN_RPM — per-IP fixed-window rate limit for POST /oauth/token.
    # Must be > 0; fails fast at boot when set to 0 or negative.
    agent_oauth_token_rpm: int = 60  # GATEWAY_AGENT_OAUTH_TOKEN_RPM

    # ── Agent OAuth data-plane budget cap (agent-token-authn-seam task) ────────
    # GATEWAY_AGENT_OAUTH_DEFAULT_BUDGET_USD — default monthly spend cap applied to
    # every agent token, mapped to AuthzResult.monthly_budget_usd so the existing
    # per-key guard enforces it at usage:spend:key:{token_id}:{YYYYMM}.
    # Must be > 0 (Decimal); fails loud at boot on zero, negative, or non-finite values.
    agent_oauth_default_budget_usd: Decimal = Decimal(
        "100.00"
    )  # GATEWAY_AGENT_OAUTH_DEFAULT_BUDGET_USD

    @field_validator("agent_oauth_default_budget_usd", mode="before")
    @classmethod
    def _validate_agent_oauth_budget(cls, v: object) -> object:
        """Fail loud on a non-positive or non-finite default budget cap.

        A zero or negative cap would immediately block every agent token (zero threshold
        passes no spend), while a non-finite value would silently disable enforcement.
        Rejecting at startup turns both into a clear boot error. Mirrors the
        reconciliation_drift_threshold validator style (mode='before' to catch
        inf/nan before Pydantic's Decimal coercion).
        """
        try:
            d = Decimal(str(v))
        except Exception:
            return v  # not parseable — let Pydantic raise its normal decimal error
        if not d.is_finite() or d <= 0:
            raise ValueError(
                "INVALID_AGENT_OAUTH_DEFAULT_BUDGET_USD: "
                "GATEWAY_AGENT_OAUTH_DEFAULT_BUDGET_USD must be a finite, positive "
                f"USD amount (> 0); got {v!r}"
            )
        return v

    @field_validator(
        "agent_oauth_device_code_ttl_seconds",
        "agent_oauth_poll_interval_seconds",
        "agent_oauth_authorize_rpm",
        "agent_oauth_approve_rpm",
        "agent_oauth_access_token_ttl_seconds",
        "agent_oauth_token_rpm",
    )
    @classmethod
    def _validate_agent_oauth_positive_knobs(cls, v: int) -> int:
        """Fail loud on a non-positive agent OAuth knob (device-authorization-endpoint).

        A zero or negative value is a misconfiguration, not a disable signal: all knobs
        (ttl, interval, rpm) must be strictly positive for a functioning endpoint.
        Rejecting at startup turns the silent-but-broken endpoint into a clear boot
        error. Mirrors the reconciliation check_interval validator style.
        """
        if v <= 0:
            raise ValueError(
                "INVALID_AGENT_OAUTH_KNOB: agent_oauth_device_code_ttl_seconds, "
                "agent_oauth_poll_interval_seconds, agent_oauth_authorize_rpm, "
                "agent_oauth_access_token_ttl_seconds, and agent_oauth_token_rpm "
                f"must each be a positive integer (> 0); got {v!r}"
            )
        return v

    @field_validator("agent_oauth_refresh_token_ttl_seconds")
    @classmethod
    def _validate_agent_oauth_refresh_ttl(cls, v: int) -> int:
        """Fail loud on a negative refresh TTL (0 is valid — means disabled).

        A negative value is a misconfiguration: 0 is the explicit disable sentinel.
        Rejecting < 0 prevents a silent misconfiguration that would pass the start
        guard but never correctly disable refresh tokens.
        """
        if v < 0:
            raise ValueError(
                "INVALID_AGENT_OAUTH_REFRESH_TTL: "
                "GATEWAY_AGENT_OAUTH_REFRESH_TOKEN_TTL_SECONDS must be >= 0 "
                f"(0 disables refresh tokens); got {v!r}"
            )
        return v

    # ── Dashboard playground token (playground-token-exchange task) ─────────────
    # The dashboard BFF exchanges a browser session (JWT) for a SHORT-LIVED,
    # spend-capped sk- key it uses SERVER-SIDE to reach the /v1 data plane (the
    # browser never sees a key). These bound the blast radius of that minted key.
    # GATEWAY_PLAYGROUND_TOKEN_TTL_SECONDS — lifetime of a minted playground key.
    # Must be > 0; fails fast at boot on 0 or negative.
    playground_token_ttl_seconds: int = 1800  # GATEWAY_PLAYGROUND_TOKEN_TTL_SECONDS
    # GATEWAY_PLAYGROUND_TOKEN_BUDGET_USD — hard monthly spend cap on a playground
    # key, enforced by the existing per-key budget guard. Must be > 0 (Decimal).
    playground_token_budget_usd: Decimal = Decimal("5.00")  # GATEWAY_PLAYGROUND_TOKEN_BUDGET_USD
    # GATEWAY_PLAYGROUND_TOKEN_MINT_RATE_PER_MINUTE — per-user ceiling on how many
    # playground keys may be minted in a 60-second window. The BFF caches the minted key
    # for its whole TTL, so a legitimate session mints rarely; this bounds a caller who
    # bypasses that cache to flood the mint endpoint (DB-row sprawl + audit-log dilution).
    # Must be > 0; enforced fail-open (a Redis outage never blocks a real session).
    playground_token_mint_rate_per_minute: int = 10  # GATEWAY_PLAYGROUND_TOKEN_MINT_RATE_PER_MINUTE

    @field_validator("playground_token_ttl_seconds")
    @classmethod
    def _validate_playground_ttl(cls, v: int) -> int:
        """Fail loud on a non-positive playground TTL (a short positive lifetime is the point)."""
        if v <= 0:
            raise ValueError(
                "INVALID_PLAYGROUND_TOKEN_TTL: GATEWAY_PLAYGROUND_TOKEN_TTL_SECONDS "
                f"must be a positive integer (> 0); got {v!r}"
            )
        return v

    @field_validator("playground_token_budget_usd", mode="before")
    @classmethod
    def _validate_playground_budget(cls, v: object) -> object:
        """Fail loud on a non-positive/non-finite playground cap (mirrors the agent-token cap)."""
        try:
            d = Decimal(str(v))
        except Exception:
            return v
        if not d.is_finite() or d <= 0:
            raise ValueError(
                "INVALID_PLAYGROUND_TOKEN_BUDGET_USD: GATEWAY_PLAYGROUND_TOKEN_BUDGET_USD "
                f"must be a finite, positive USD amount (> 0); got {v!r}"
            )
        return v

    @field_validator("playground_token_mint_rate_per_minute")
    @classmethod
    def _validate_playground_mint_rate(cls, v: int) -> int:
        """Fail loud on a non-positive mint rate (a positive per-minute ceiling is the point)."""
        if v <= 0:
            raise ValueError(
                "INVALID_PLAYGROUND_TOKEN_MINT_RATE: GATEWAY_PLAYGROUND_TOKEN_MINT_RATE_PER_MINUTE "
                f"must be a positive integer (> 0); got {v!r}"
            )
        return v

    # ── Impersonation session (impersonation-session-lifecycle task) ────────────
    # A time-boxed, revocable, superadmin-initiated session-store record + JWT pair
    # granting a superadmin the ability to act as one specific, eligible target user.
    # GATEWAY_IMPERSONATION_SESSION_TTL_SECONDS — hard session TTL; materially shorter
    # than jwt_ttl_seconds's own 86400s default. Must be > 0; fails fast at boot.
    impersonation_session_ttl_seconds: int = 900  # GATEWAY_IMPERSONATION_SESSION_TTL_SECONDS

    @field_validator("impersonation_session_ttl_seconds")
    @classmethod
    def _validate_impersonation_ttl(cls, v: int) -> int:
        """Fail loud on a non-positive impersonation TTL (mirrors playground_token_ttl_seconds's
        own style exactly — a short positive lifetime is the point)."""
        if v <= 0:
            raise ValueError(
                "INVALID_IMPERSONATION_SESSION_TTL: GATEWAY_IMPERSONATION_SESSION_TTL_SECONDS "
                f"must be a positive integer (> 0); got {v!r}"
            )
        return v

    # ── Member invite acceptance (member-invite-acceptance task) ────────────────
    # Per-client-IP fixed-window rate limits for the two PUBLIC, unauthenticated invite
    # endpoints (M7). Both must be > 0; fails fast at boot when set to 0 or negative.
    invite_preview_rpm: int = 30  # GATEWAY_INVITE_PREVIEW_RPM
    invite_accept_rpm: int = 10  # GATEWAY_INVITE_ACCEPT_RPM

    @field_validator("invite_preview_rpm", "invite_accept_rpm")
    @classmethod
    def _validate_invite_positive_knobs(cls, v: int) -> int:
        """Fail loud on a non-positive invite rate-limit knob (member-invite-acceptance).

        A zero or negative value is a misconfiguration, not a disable signal: a functioning
        per-IP limiter needs a strictly positive ceiling. Kept as its OWN validator (rather
        than folded into _validate_agent_oauth_positive_knobs above) so a failure here
        reports an invite-scoped field list, not an unrelated agent_oauth one.
        """
        if v <= 0:
            raise ValueError(
                "INVALID_INVITE_KNOB: invite_preview_rpm and invite_accept_rpm must each be "
                f"a positive integer (> 0); got {v!r}"
            )
        return v

    # ── Impersonation live-session guard (impersonation-live-session-guard task) ────
    # GATEWAY_IMPERSONATION_LIVE_CHECK_TIMEOUT_SECONDS — bounds the per-request
    # DbImpersonationSessionGuard DB read (revoked_at/expires_at). A struggling DB fails
    # THIS check fast/clean (401) rather than compounding into a long ambient hang —
    # fail-CLOSED on elapse (DbImpersonationSessionGuard.ensure_live). No ambient
    # statement_timeout exists today (confirmed at Ground) — this is a new, additive
    # bound. Mirrors object_store_timeout_seconds's exact style (gt=0, no bespoke
    # @field_validator beyond Pydantic's own gt-violation message).
    impersonation_live_check_timeout_seconds: float = Field(default=2.0, gt=0)

    # ── Edge input hardening (edge-input-hardening TASK.md §3, S2+S3+S4) ────────
    # GATEWAY_TRUSTED_PROXY_HOPS — number of trusted XFF hops Envoy appends (default 1:
    # the single rightmost token, the one hop Envoy itself appends per the compose/Helm
    # topology confirmed at Ground). resolve_trusted_client_ip trusts ONLY this many
    # tokens from the right; a value <=0 is a misconfiguration, not a disable signal —
    # coerced to 1 + startup WARNING (mirrors _coerce_negative_max_concurrent).
    trusted_proxy_hops: int = Field(default=1)  # GATEWAY_TRUSTED_PROXY_HOPS

    # GATEWAY_EGRESS_ALLOW_PRIVATE_RANGES — operator opt-in allowing a BYOK Azure
    # endpoint/authority to resolve to a loopback/link-local/RFC1918/ULA address (e.g. a
    # real Azure Private Link deployment). Default False (deny-by-default, Tin
    # 2026-07-10). Cloud-metadata addresses are ALWAYS denied regardless of this flag.
    egress_allow_private_ranges: bool = Field(default=False)  # GATEWAY_EGRESS_ALLOW_PRIVATE_RANGES
    # GATEWAY_EGRESS_ALLOW_HTTP_DEV — operator opt-in allowing a plain-http (non-https)
    # BYOK Azure endpoint/authority scheme, for local/dev only. Default False.
    egress_allow_http_dev: bool = Field(default=False)  # GATEWAY_EGRESS_ALLOW_HTTP_DEV
    # GATEWAY_EGRESS_DNS_RESOLVE_TIMEOUT_S — bounds the request-time DNS resolution the
    # egress policy performs before every BYOK-influenced outbound dial (PROJECT.md: no
    # outbound IO without a timeout). A resolver timeout fails CLOSED (deny the dial).
    # GATEWAY_EGRESS_DNS_RESOLVE_TIMEOUT_S
    egress_dns_resolve_timeout_s: float = Field(default=2.0, gt=0)

    # GATEWAY_MAX_JSON_BODY_BYTES — per-request cap for /v1/* JSON bodies (chat/embeddings/
    # images) and /admin/* write bodies, enforced by BodySizeLimitMiddleware. Default 20 MiB.
    max_json_body_bytes: int = Field(default=20_971_520, ge=0)  # GATEWAY_MAX_JSON_BODY_BYTES
    # GATEWAY_MAX_AUDIO_UPLOAD_BYTES — per-request cap for /v1/audio/{transcriptions,
    # translations} multipart bodies. Default 25 MiB (matches realtime_max_utterance_bytes
    # and OpenAI Whisper's documented 25 MB file ceiling).
    max_audio_upload_bytes: int = Field(default=26_214_400, ge=0)  # GATEWAY_MAX_AUDIO_UPLOAD_BYTES

    @field_validator("trusted_proxy_hops", mode="before")
    @classmethod
    def _coerce_nonpositive_trusted_proxy_hops(cls, v: object) -> object:
        """Coerce a non-positive GATEWAY_TRUSTED_PROXY_HOPS to 1 + emit a startup WARNING.

        A hop count <= 0 is a misconfiguration, not a disable signal — trusting ZERO hops
        would mean falling back to request.client.host unconditionally, which silently
        defeats the XFF resolver behind a real proxy. Mirrors
        _coerce_negative_max_concurrent's established convention.
        """
        import logging as _logging

        try:
            n = int(v)  # type: ignore[arg-type]  # try/except is the guard
        except (TypeError, ValueError):
            return v  # not an int — let Pydantic raise its normal type error
        if n <= 0:
            _logging.getLogger(__name__).warning(
                "INVALID_TRUSTED_PROXY_HOPS: GATEWAY_TRUSTED_PROXY_HOPS=%r is not positive; "
                "coercing to 1 (trust only the single rightmost XFF hop). Set to a positive "
                "integer matching your proxy topology.",
                v,
            )
            return 1
        return n

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
