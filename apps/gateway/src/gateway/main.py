import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CollectorRegistry
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gateway.access_requests.api.access_requests_router import access_requests_router
from gateway.access_requests.infrastructure.rate_limiter import AccessRequestIpRateLimiter
from gateway.agent_oauth.api.agent_principal_router import agents_router
from gateway.agent_oauth.api.device_approval_router import agent_oauth_approval_router
from gateway.agent_oauth.api.device_authorize_router import agent_oauth_device_router
from gateway.agent_oauth.api.token_router import agent_oauth_token_router
from gateway.agent_oauth.infrastructure.ip_rate_limiter import AgentOAuthIpRateLimiter
from gateway.agent_oauth.infrastructure.orm import (  # noqa: F401 — registers agent OAuth tables on Base.metadata
    AgentTokenRow as _AgentTokenRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.alerting.application.dispatcher import AlertDispatcher
from gateway.alerting.application.health_checker import UpstreamHealthChecker
from gateway.alerting.infrastructure.httpx_pinger import HttpxUpstreamPinger
from gateway.alerting.infrastructure.httpx_webhook_sink import HttpxWebhookSink
from gateway.artifacts.api.router import artifacts_router
from gateway.artifacts.infrastructure.orm import (  # noqa: F401 — registers ArtifactRow on Base.metadata
    ArtifactRow as _ArtifactRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.audit.api.router import audit_export_router
from gateway.auth.api.oidc_admin_router import oidc_admin_router
from gateway.auth.api.oidc_router import oidc_router
from gateway.auth.api.saml_admin_router import saml_admin_router
from gateway.auth.api.saml_router import saml_router
from gateway.auth.infrastructure.orm import (  # noqa: F401 — registers OidcProviderConfigRow on Base.metadata
    OidcProviderConfigRow as _OidcProviderConfigRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.auth.infrastructure.saml_orm import (  # noqa: F401 — registers SamlProviderConfigRow on Base.metadata
    SamlProviderConfigRow as _SamlProviderConfigRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.batches.api.router import batch_router
from gateway.batches.api.stats_router import batch_stats_router
from gateway.batches.application.window_flusher import (
    DEFAULT_TICK_INTERVAL_SECONDS as BATCH_WINDOW_TICK_INTERVAL_SECONDS,
)
from gateway.batches.application.window_flusher import (
    BatchWindowFlusher,
    should_start_batch_window_flusher,
)
from gateway.batches.application.worker import (
    BatchJobWorker,
    RedisBatchJobQueue,
    should_start_batch_worker,
)
from gateway.batches.application.worker import (
    recover_orphans as recover_batch_orphans,
)
from gateway.batches.infrastructure.orm import (  # noqa: F401 — registers BatchJobRow/BatchJobItemRow on Base.metadata
    BatchJobItemRow as _BatchJobItemRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.billing.api.router import invoices_router
from gateway.billing.application.invoice_generator import (
    InvoiceGenerator,
    should_start_invoice_generator,
)
from gateway.billing.infrastructure.orm import (  # noqa: F401 — registers InvoiceRow/InvoiceLineRow/InvoiceCorrectionRow on Base.metadata
    InvoiceCorrectionRow as _InvoiceCorrectionRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.billing.infrastructure.orm import (  # noqa: F401
    InvoiceLineRow as _InvoiceLineRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.billing.infrastructure.orm import (  # noqa: F401
    InvoiceRow as _InvoiceRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.budgets.api.router import budget_router
from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard
from gateway.catalog.api.router import (
    admin_catalog_router,
    admin_models_router,
    catalog_router,
    internal_catalog_router,
)
from gateway.catalog.application.refresh_scheduler import (
    CatalogRefreshScheduler,
    should_start_catalog_refresh,
)
from gateway.catalog.infrastructure.composite_source import CompositeCatalogSource
from gateway.catalog.infrastructure.openrouter_source import OpenRouterCatalogSource
from gateway.compliance.api.report_schedule_router import report_schedule_router
from gateway.compliance.api.router import compliance_router
from gateway.compliance.application.report_schedule_generator import (
    ReportScheduleGenerator,
    should_start_report_schedule_generator,
)
from gateway.compliance.infrastructure.orm import (  # noqa: F401 — registers TenantReportScheduleRow/ComplianceReportRunRow on Base.metadata
    ComplianceReportRunRow as _ComplianceReportRunRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.compliance.infrastructure.orm import (  # noqa: F401
    TenantReportScheduleRow as _TenantReportScheduleRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.conversations.api.router import conversations_router
from gateway.conversations.infrastructure.orm import (  # noqa: F401 — registers ConversationRow/ConversationMessageRow on Base.metadata
    ConversationMessageRow as _ConversationMessageRow,  # pyright: ignore[reportUnusedImport]  — side-effect import
)
from gateway.conversations.infrastructure.orm import (
    ConversationRow as _ConversationRow,  # noqa: F401  # pyright: ignore[reportUnusedImport]  — side-effect import
)
from gateway.core.body_size_guard import BodySizeLimitMiddleware
from gateway.core.config import Settings
from gateway.core.egress_policy import DenyPrivateAndMetadataEgressPolicy
from gateway.core.errors import register_error_handlers
from gateway.credits.api.router import credits_platform_router, credits_router
from gateway.credits.application.recovery_sweep import (
    CreditHoldRecoverySweeper,
    should_start_credit_recovery_sweep,
)
from gateway.credits.domain.ports import PassthroughCreditGuard
from gateway.credits.infrastructure.orm import (  # noqa: F401 — registers CreditLedgerRow/TenantCreditBalanceRow on Base.metadata
    CreditLedgerRow as _CreditLedgerRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.credits.infrastructure.postgres_guard import PostgresCreditGuard
from gateway.domain_capture.api.domain_claims_router import domain_claims_router
from gateway.domain_capture.application.notify_scheduler import (
    DomainVerifyNotifyScheduler,
    should_start_domain_verify_notify,
)
from gateway.domain_capture.infrastructure.dns_resolver import DnsPythonTxtResolver
from gateway.domain_capture.infrastructure.orm import (  # noqa: F401 — registers TenantDomainClaimRow on Base.metadata
    TenantDomainClaimRow as _TenantDomainClaimRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.domain_capture.infrastructure.rate_limiter import DomainClaimRateLimiter
from gateway.email.domain.ports import EmailSender
from gateway.email.infrastructure.console_email_sender import ConsoleEmailSender
from gateway.email.infrastructure.smtp_email_sender import SmtpEmailSender
from gateway.files.api.router import files_router
from gateway.guardrail_analytics.api.router import guardrail_analytics_router
from gateway.guardrail_analytics.infrastructure.orm import (  # noqa: F401 — registers GuardrailVerdictEventRow on Base.metadata
    GuardrailVerdictEventRow as _GuardrailVerdictEventRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.keys.api.key_guardrail_router import key_guardrail_router
from gateway.keys.api.platform_keys_router import platform_keys_router
from gateway.keys.api.router import admin_router as keys_admin_router
from gateway.keys.api.router import authz_router as keys_authz_router
from gateway.keys.infrastructure.mint_rate_limiter import PlaygroundMintRateLimiter
from gateway.logs.api.capture_config_router import capture_router
from gateway.logs.api.logs_query_router import logs_query_router
from gateway.logs.infrastructure.orm import (  # noqa: F401 — registers RequestLogRow on Base.metadata
    RequestLogRow as _RequestLogRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.logs.infrastructure.sqlalchemy_capture import SqlAlchemyPayloadCapture
from gateway.logs.infrastructure.zdr_retention_adapter import RetentionZdrPort
from gateway.mcp_connector.api.admin_router import mcp_admin_router
from gateway.mcp_connector.api.key_router import mcp_key_router
from gateway.mcp_connector.api.proxy_router import mcp_proxy_router
from gateway.memory.api.router import memories_router
from gateway.memory.infrastructure.orm import (  # noqa: F401 — registers MemoryRow on Base.metadata
    MemoryRow as _MemoryRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.objectstore import build_object_store
from gateway.observability.logging_config import configure_structlog
from gateway.observability.metrics import MetricsRegistry, expose_metrics
from gateway.observability.middleware import RequestIdMiddleware
from gateway.ops.api.router import ops_router
from gateway.payments.api.error_handler import register_checkout_error_handler
from gateway.payments.api.router import checkout_router
from gateway.payments.infrastructure.provider_factory import build_payment_provider
from gateway.proxy.api.audio_router import audio_router
from gateway.proxy.api.concurrency_guard import GlobalBackPressureMiddleware
from gateway.proxy.api.discovery_router import discovery_router
from gateway.proxy.api.embeddings_router import embeddings_router
from gateway.proxy.api.images_router import images_router
from gateway.proxy.api.messages_router import messages_router
from gateway.proxy.api.moderations_router import moderations_router
from gateway.proxy.api.presets_admin_router import presets_admin_router
from gateway.proxy.api.provider_keys_admin_router import provider_keys_admin_router
from gateway.proxy.api.realtime_relay_ws import realtime_relay_router
from gateway.proxy.api.realtime_ws import realtime_router
from gateway.proxy.api.responses_router import responses_router
from gateway.responses_store.api.router import stored_responses_router
from gateway.responses_store.infrastructure.orm import (  # noqa: F401 — registers StoredResponseRow on Base.metadata
    StoredResponseRow as _StoredResponseRow,  # pyright: ignore[reportUnusedImport]  — side-effect import
)
from gateway.proxy.api.router import proxy_router
from gateway.proxy.api.routing_admin_router import routing_admin_router
from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.platform_fallback import PlatformCredentialFallbackService
from gateway.proxy.application.routing_config_merge import merge_routing_config
from gateway.proxy.application.routing_strategy import build_strategy
from gateway.proxy.domain.ports import CompletionUpstream, UpstreamProvider
from gateway.proxy.infrastructure.anthropic_upstream import AnthropicCompletionUpstream
from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache
from gateway.proxy.infrastructure.azure_embeddings import AzureEmbeddingsProvider
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream
from gateway.proxy.infrastructure.batch_diversion import BatchDiversionAdapter
from gateway.proxy.infrastructure.batch_window_buffer import BatchWindowBuffer
from gateway.proxy.infrastructure.bedrock_embeddings import BedrockEmbeddingsProvider
from gateway.proxy.infrastructure.bedrock_upstream import BedrockCompletionUpstream
from gateway.proxy.infrastructure.cached_tenant_credential_resolver import (
    CachedTenantCredentialResolver,
)
from gateway.proxy.infrastructure.catalog_provider_resolver import CatalogProviderResolver
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.gemini_upstream import (
    GeminiCompletionUpstream,
    GoogleEmbeddingsProvider,
)
from gateway.proxy.infrastructure.ml_moderation_evaluator import OpenAiModerationClient
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream
from gateway.proxy.infrastructure.openrouter_upstream_provider import OpenRouterUpstreamFacade
from gateway.proxy.infrastructure.orm import (
    TenantModelPresetRow as _TenantModelPresetRow,  # noqa: F401 — registers TenantModelPresetRow on Base.metadata  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.proxy.infrastructure.orm import (
    TenantProviderKeyRow as _TenantProviderKeyRow,  # noqa: F401 — registers TenantProviderKeyRow on Base.metadata  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.proxy.infrastructure.provider_aware_upstream import ProviderAwareCompletionUpstream
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry
from gateway.proxy.infrastructure.redis_cooldown_gate import RedisCooldownGate
from gateway.proxy.infrastructure.redis_limit_gate import RedisDeploymentLimitGate
from gateway.proxy.infrastructure.redis_load_gate import RedisDeploymentLoadGate
from gateway.proxy.infrastructure.residency_lookup import SqlAlchemyResidencyLookup
from gateway.proxy.infrastructure.routing_config_repository import RoutingConfigRepository
from gateway.proxy.infrastructure.tenant_model_preset_store import DbTenantModelPresetStore
from gateway.proxy.infrastructure.tenant_provider_key_store import DbTenantProviderKeyStore
from gateway.proxy.infrastructure.tier_capacity_guard import (
    PassthroughTierCapacityGuard,
    RedisTierCapacityGuard,
)
from gateway.proxy.infrastructure.vertex_ad import VertexTokenProviderCache
from gateway.proxy.infrastructure.vertex_upstream import VertexCompletionUpstream
from gateway.rate_limits.application.passthrough import PassthroughBandwidthBucket
from gateway.rate_limits.infrastructure.plan_rate_limit_resolver import PlanRateLimitResolver
from gateway.rate_limits.infrastructure.redis_lua_limiter import RedisLuaRateLimiter
from gateway.rate_limits.infrastructure.redis_token_bucket import RedisTokenBucket
from gateway.scim.api.errors import register_scim_error_handlers
from gateway.scim.api.scim_router import scim_router
from gateway.scim.api.token_router import scim_token_router
from gateway.scim.infrastructure.orm import (  # noqa: F401 — registers ScimTokenRow on Base.metadata
    ScimTokenRow as _ScimTokenRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.scim.infrastructure.rate_limiter import ScimTokenRateLimiter
from gateway.teams.api.router import teams_router
from gateway.teams.infrastructure.orm import (  # noqa: F401 — registers TeamRow/TeamMemberRow on Base.metadata
    TeamMemberRow as _TeamMemberRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.tenants.api.batch_policy_router import batch_policy_router
from gateway.tenants.api.billing_owner_router import billing_owner_router
from gateway.tenants.api.cache_router import cache_router
from gateway.tenants.api.domain_invite_links_router import domain_invite_links_router
from gateway.tenants.api.domain_invite_redeem_router import domain_invite_redeem_router
from gateway.tenants.api.guardrail_router import guardrail_router
from gateway.tenants.api.invite_accept_router import invite_accept_router
from gateway.tenants.api.invites_router import invites_router
from gateway.tenants.api.plan_router import plan_router
from gateway.tenants.api.platform_audit_router import platform_audit_router
from gateway.tenants.api.platform_impersonation_router import platform_impersonation_router
from gateway.tenants.api.platform_plans_router import platform_plans_router
from gateway.tenants.api.platform_service_tier_router import platform_service_tier_router
from gateway.tenants.api.platform_tenant_config_router import platform_tenant_config_router
from gateway.tenants.api.platform_tenants_router import platform_tenants_router
from gateway.tenants.api.platform_users_router import platform_users_router
from gateway.tenants.api.rate_card_router import rate_card_router
from gateway.tenants.api.region_pricing_router import region_pricing_router
from gateway.tenants.api.residency_policy_router import residency_policy_router
from gateway.tenants.api.retention_policy_router import retention_policy_router
from gateway.tenants.api.router import router as tenants_router
from gateway.tenants.api.service_tier_router import service_tier_router
from gateway.tenants.api.users_router import users_router
from gateway.tenants.infrastructure.argon2_hasher import Argon2PasswordHasher
from gateway.tenants.infrastructure.invite_public_rate_limiter import InvitePublicRateLimiter
from gateway.tenants.infrastructure.jwt_service import JwtTokenService
from gateway.tenants.infrastructure.orm import (
    TenantRow as _TenantRow,  # noqa: F401 — ensures budget_usd_monthly column is in ORM metadata  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.tool_call_metering.infrastructure.observer import MeteringToolCallObserver
from gateway.usage.api.margin_router import margin_router
from gateway.usage.api.openai_usage_router import openai_usage_router
from gateway.usage.api.router import usage_router
from gateway.usage.application.cost_recovery import OpenRouterCostRecoveryService
from gateway.usage.application.drift_checker import (
    ReconciliationDriftChecker,
    should_start_drift_checker,
)
from gateway.usage.application.flusher import UsageLedgerFlusher
from gateway.usage.application.recorder import RecordingUsageRecorder
from gateway.usage.application.recovery_sweep import (
    OpenRouterRecoverySweeper,
    should_start_recovery_sweep,
)
from gateway.usage.application.retention_sweep import (
    RetentionSweeper,
    should_start_retention_sweep_with_zdr,
)
from gateway.usage.infrastructure.alert_events_orm import (
    AlertEventRow as _AlertEventRow,  # noqa: F401 — registers alert_events ORM metadata  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.usage.infrastructure.orm import (
    UsageRecordRow as _UsageRecordRow,  # noqa: F401 — registers ORM metadata  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.video.api.router import video_router
from gateway.video.application.worker import (
    RedisVideoJobQueue,
    VideoJobWorker,
    recover_orphans,
    should_start_video_worker,
)
from gateway.video.infrastructure.orm import (  # noqa: F401 — registers VideoGenerationJobRow on Base.metadata
    VideoGenerationJobRow as _VideoGenerationJobRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)

internal_router = APIRouter(prefix="/internal")
health_router = APIRouter()

_CREDENTIALS_RE = re.compile(r"(?:://[^@]*@|password[=:]\s*\S+|passwd[=:]\s*\S+)", re.IGNORECASE)


def _strip_credentials(text: str) -> str:
    """Remove connection URLs with credentials and password= fragments from error strings."""
    return _CREDENTIALS_RE.sub("<redacted>", text)


@internal_router.get("/health")
async def internal_health() -> dict[str, str]:
    # Liveness only: must never touch Postgres/Redis/OpenRouter, so a
    # dependency outage can't cascade into the fleet being marked dead.
    return {"status": "ok", "service": "gateway"}


@internal_router.get("/health/live")
async def live_probe() -> dict[str, str]:
    """GET /internal/health/live — liveness probe.

    Returns 200 without touching any external dependency.
    Use for k8s livenessProbe / Envoy active health check (process-up only).
    """
    return {"status": "ok", "service": "gateway"}


@internal_router.get("/health/ready")
async def ready_probe(request: Request) -> Response:
    """GET /internal/health/ready — readiness probe.

    Checks SELECT 1 on Postgres AND PING on Redis concurrently (3s timeouts).
    Returns 200 when both pass; 503 when either fails.
    Credentials are stripped from all error detail strings.
    """
    app = request.app
    engine = app.state.engine
    redis_client = app.state.redis_client

    async def _check_db() -> str:
        try:
            async with asyncio.timeout(3.0):
                async with engine.connect() as conn:
                    await conn.execute(sa_text("SELECT 1"))
            return "ok"
        except Exception as exc:
            return f"error: {_strip_credentials(str(exc))}"

    async def _check_redis() -> str:
        try:
            async with asyncio.timeout(3.0):
                await redis_client.ping()
            return "ok"
        except Exception as exc:
            return f"error: {_strip_credentials(str(exc))}"

    db_result, redis_result = await asyncio.gather(_check_db(), _check_redis())

    checks = {"db": db_result, "redis": redis_result}
    if db_result == "ok" and redis_result == "ok":
        return JSONResponse({"status": "ready", "checks": checks}, status_code=200)
    return JSONResponse({"status": "not_ready", "checks": checks}, status_code=503)


@internal_router.get("/metrics")
async def metrics_endpoint(request: Request) -> Response:
    """GET /internal/metrics — Prometheus text format 0.0.4.

    Never returns a non-200.  Redis/DB errors yield sentinel values in the body.
    No authentication required (cluster-internal; blocked at Envoy edge).
    """
    body, content_type = await expose_metrics(request.app)
    return Response(content=body, status_code=200, media_type=content_type)


@health_router.get("/health")
async def health() -> dict[str, str]:
    # Top-level liveness probe for docker-compose healthcheck and Envoy routing.
    # Identical contract to /internal/health — never touches Postgres/Redis/OpenRouter.
    return {"status": "ok", "service": "gateway"}


def build_model_router(
    settings: Settings,
    *,
    redis_client: Any,
    completion_upstream: Any,
    cooldown_gate: Any,
    metrics_registry: Any,
    residency_lookup: Any = None,
) -> FallbackModelRouter:
    """Construct the FallbackModelRouter from settings (extracted so it can be REBUILT at boot
    from a persisted routing config — v32 routing-config-store). Byte-identical to the inline
    create_app construction when called with the env settings.

    Gate construction does NOT connect to Redis (safe without lifespan): the load gate exists
    only for {least-busy, latency}; the limit gate only when some deployment declares a limit.
    """
    if settings.routing_strategy in {"least-busy", "latency"}:
        load_gate: RedisDeploymentLoadGate | None = RedisDeploymentLoadGate(
            redis=redis_client,
            alpha=settings.loadbal_ewma_alpha,
            in_flight_ttl_s=settings.loadbal_inflight_ttl_s,
        )
    else:
        load_gate = None

    has_any_limit = any(
        d.rpm_limit is not None or d.tpm_limit is not None
        for deps in settings.deployments.values()
        for d in deps
    )
    limit_gate: RedisDeploymentLimitGate | None = (
        RedisDeploymentLimitGate(redis=redis_client) if has_any_limit else None
    )

    return FallbackModelRouter(
        upstream=completion_upstream,
        model_groups=settings.model_groups,
        health_gate=cooldown_gate,
        metrics_registry=metrics_registry,
        deployments=settings.deployments,
        strategy=build_strategy(settings.routing_strategy, load_gate),
        load_gate=load_gate,
        limit_gate=limit_gate,
        fallback_on_error=settings.upstream_fallback_on_error,
        stream_resilience_enabled=settings.upstream_stream_resilience_enabled,
        residency_lookup=residency_lookup,
    )


def build_email_sender(settings: Settings) -> EmailSender:
    """Select the EmailSender adapter — mirrors build_object_store(settings)'s shape
    exactly. email_smtp_enabled=False (default) -> ConsoleEmailSender (no real
    delivery); email_smtp_enabled=True (boot-validated: host + dashboard origin
    required) -> SmtpEmailSender.
    """
    return SmtpEmailSender(settings) if settings.email_smtp_enabled else ConsoleEmailSender()


# files-uploads-api PLAN.md §3 — headroom the /v1/files BodySizeLimitMiddleware cap carries
# over files_max_bytes so the coarse outer body guard never pre-empts the router's precise
# per-file ERR_FILE_TOO_LARGE check: the multipart body (file part + `purpose` field + MIME
# framing) is at most a few hundred bytes larger than the raw file, so 1 MiB is a generous
# margin that keeps the router the sole decider for any raw file up to files_max_bytes.
_FILES_MULTIPART_HEADROOM_BYTES = 1024 * 1024
# When files_max_bytes=0 (operator-disabled per-file cap), the router applies NO size limit;
# the outer guard then falls back to a large finite ceiling (Envoy enforces the real coarse
# outer bound independently — body_size_guard.py docstring) rather than an unbounded cap.
_FILES_UNLIMITED_ROUTE_CAP = 5 * 1024 * 1024 * 1024  # 5 GiB


def _files_route_cap(files_max_bytes: int) -> int:
    """The BodySizeLimitMiddleware cap for /v1/files: files_max_bytes + multipart headroom
    (so the router owns the exact ERR_FILE_TOO_LARGE boundary), or a large finite ceiling
    when the per-file cap is disabled (files_max_bytes=0)."""
    if files_max_bytes <= 0:
        return _FILES_UNLIMITED_ROUTE_CAP
    return files_max_bytes + _FILES_MULTIPART_HEADROOM_BYTES


def create_app(settings: Settings | None = None) -> FastAPI:
    """Composition root: wires infrastructure adapters into domain ports."""
    settings = settings if settings is not None else Settings()

    # Configure structlog once per process (idempotent).  Must run before any
    # logger call — including those triggered during module imports below.
    configure_structlog()

    # service-tiers TASK.md §3 (DECIDED at freeze-review, 2026-07-12): a REQUIRED
    # startup warning — tier_capacity_cluster_cap < max_concurrent_requests is the one
    # per-process-detectable certain misconfiguration (the two knobs are independently
    # operator-set with no shared worker-count basis in this codebase; nothing else
    # enforces they stay consistent). Fires ONLY when tiering is actually enabled
    # (cluster_cap > 0) — a disabled tier gate (0, the default) has nothing to
    # misconfigure relative to the back-pressure cap. Ops guidance: cluster_cap ~=
    # workers x max_concurrent_requests (settings docstrings + runbook).
    if (
        settings.tier_capacity_cluster_cap > 0
        and settings.tier_capacity_cluster_cap < settings.max_concurrent_requests
    ):
        structlog.get_logger(__name__).warning(
            "tier_capacity_cluster_cap_below_max_concurrent_requests",
            tier_capacity_cluster_cap=settings.tier_capacity_cluster_cap,
            max_concurrent_requests=settings.max_concurrent_requests,
            hint="cluster_cap should be >= workers x max_concurrent_requests",
        )

    # domain-routing-unification TASK.md §3 (FROZEN @ v2/CR-v2): GATEWAY_OIDC_DOMAIN_MAPPING
    # is a trusted OPERATOR-set FALLBACK, not deleted (v1 removed it outright and broke
    # ~23 legacy env-routing tests; CR-v2 reverted that). A verified tenant_domain_claims
    # row ALWAYS takes precedence when one exists; the env mapping is only ever consulted
    # when the callback has no per-tenant DB config pinned. Still warn LOUDLY at startup —
    # a deployment relying SOLELY on the env var (no per-tenant config, no verified claim)
    # should DNS-TXT-verify the domain so it stops depending on the lower-trust fallback.
    try:
        _legacy_domain_mapping = json.loads(settings.oidc_domain_mapping)
    except (ValueError, TypeError):  # Settings already validates; belt-and-suspenders
        _legacy_domain_mapping = None
    if _legacy_domain_mapping:
        structlog.get_logger(__name__).warning(
            "oidc_domain_mapping_is_a_fallback_verified_claims_take_precedence",
            mapped_domains=[
                entry.get("email_domain")
                for entry in _legacy_domain_mapping
                if isinstance(entry, dict)
            ],
            hint=(
                "GATEWAY_OIDC_DOMAIN_MAPPING is a fallback — a verified tenant_domain_claims "
                "row (DNS-TXT via POST /admin/domain-claims) always takes precedence over it. "
                "It is only consulted when neither a verified claim nor a per-tenant DB OIDC "
                "config exists for a domain."
            ),
        )

    @contextlib.asynccontextmanager  # pyright: ignore[reportDeprecated]  — Pyright 1.1.410 stub false-positive; stdlib asynccontextmanager is not deprecated
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Single lifespan context manager — replaces the deprecated on_event handlers.

        Startup ordering:
          1. Schema bootstrap if environment in ("dev", "test")
          2. Create UsageLedgerFlusher bound to redis_client + sessionmaker
          3. Start flusher background task → app.state.flusher_task
          4. Start AlertDispatcher background task → app.state.dispatcher_task
          5. Start UpstreamHealthChecker background task → app.state.health_checker_task
             (skipped when health_check_interval_seconds == 0)

        Shutdown ordering:
          1. Cancel dispatcher + health_checker background tasks
          2. Run one final run_once()/check_once() cycle for each
          3. Cancel flusher background task
          4. drain_until_empty(timeout=shutdown_drain_timeout_seconds)
          5. await redis_client.aclose()
          6. await engine.dispose()
          7. await httpx_client.aclose() if held
        """
        _engine = app.state.engine
        _redis = app.state.redis_client
        _sessionmaker = app.state.sessionmaker
        _settings: Settings = app.state.settings

        # ── Startup ──────────────────────────────────────────────────────
        # Idempotent schema bootstrap for dev/test environments.
        # In production the schema is managed by Alembic migrations.
        if _settings.environment in ("dev", "test"):
            from gateway.core.db import Base  # local import to avoid circular at module level

            async with _engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        # Warm up the provider resolver cache from the catalog DB.
        # Fail-safe: refresh() never raises; empty map at startup → all "openrouter".
        await app.state.provider_resolver.refresh()

        # Apply the persisted operator-wide routing config OVER the env Settings (v32
        # routing-config-store). DB-wins-when-present, env fallback. RESTART-TO-APPLY: the
        # router is rebuilt HERE at startup (before serving) — never mutated under live traffic.
        # Design-for-failure: a DB read or validation failure must NOT crash startup; we log and
        # keep the env config + env-built router.
        try:
            _stored_routing = await RoutingConfigRepository(_sessionmaker).get()
            if _stored_routing is not None:
                _merged = merge_routing_config(_settings, _stored_routing)
                # Build the router FIRST; only commit both fields once it succeeds so
                # settings and model_router can never drift out of sync if the build raises
                # (the except below then keeps BOTH at the env config).
                _merged_router = build_model_router(
                    _merged,
                    redis_client=_redis,
                    completion_upstream=app.state.completion_upstream,
                    cooldown_gate=app.state.cooldown_gate,
                    metrics_registry=app.state.metrics_registry,
                    residency_lookup=getattr(app.state, "residency_lookup", None),
                )
                app.state.settings = _merged
                app.state.model_router = _merged_router
                structlog.get_logger(__name__).info(
                    "routing_config_applied",
                    routing_strategy=_merged.routing_strategy,
                    model_groups=list(_merged.model_groups.keys()),
                )
        except Exception:
            structlog.get_logger(__name__).warning(
                "routing_config_apply_failed_fallback_env", exc_info=True
            )

        flusher = UsageLedgerFlusher(
            redis=_redis,
            session_factory=_sessionmaker,
            pel_reclaim_idle_ms=_settings.usage_pel_reclaim_idle_ms,
        )
        app.state.flusher = flusher
        app.state.flusher_task = asyncio.create_task(flusher.run_forever())

        # AlertDispatcher — background webhook delivery
        webhook_sink = HttpxWebhookSink()
        dispatcher = AlertDispatcher(
            session_factory=_sessionmaker,
            webhook_sink=webhook_sink,
            webhook_url=_settings.alert_webhook_url,
            retry_max=_settings.alert_retry_max,
        )
        app.state.dispatcher = dispatcher
        app.state.dispatcher_task = asyncio.create_task(dispatcher.run_forever())

        # OtelFlusher — background OTLP span export (started only when otel_enabled=True)
        app.state.otel_flusher_task = None
        if _settings.otel_enabled:
            import asyncio as _asyncio

            from gateway.observability.otel import OtelFlusher, QueueOtelSpanEmitter

            _otel_queue: _asyncio.Queue[object] = _asyncio.Queue(maxsize=_settings.otel_queue_max)
            _otel_flusher = OtelFlusher(
                queue=_otel_queue,  # type: ignore[arg-type]
                export_url=_settings.otel_export_url,
                service_name=_settings.otel_service_name,
                httpx_client=httpx.AsyncClient(),
                metrics_registry=app.state.metrics_registry,
            )
            app.state.span_emitter = QueueOtelSpanEmitter(
                queue=_otel_queue,  # type: ignore[arg-type]
                metrics_registry=app.state.metrics_registry,
            )
            app.state.otel_flusher = _otel_flusher
            app.state.otel_flusher_task = asyncio.create_task(
                _otel_flusher.run_forever(
                    interval_seconds=float(_settings.otel_flush_interval_seconds)
                )
            )

        # UpstreamHealthChecker — periodic health ping (disabled when interval == 0)
        app.state.health_checker_task = None
        if _settings.health_check_interval_seconds > 0:
            pinger = HttpxUpstreamPinger()
            health_checker = UpstreamHealthChecker(
                session_factory=_sessionmaker,
                pinger=pinger,
            )
            app.state.health_checker = health_checker
            app.state.health_checker_task = asyncio.create_task(
                health_checker.run_forever(
                    interval_seconds=float(_settings.health_check_interval_seconds)
                )
            )

        # ReconciliationDriftChecker — periodic operator-wide unbilled-upstream leak monitor
        # (v29 drift-alert). Default-OFF: started only when BOTH knobs are > 0.
        app.state.drift_checker_task = None
        if should_start_drift_checker(
            _settings.reconciliation_drift_threshold,
            _settings.reconciliation_check_interval_seconds,
        ):
            drift_checker = ReconciliationDriftChecker(
                session_factory=_sessionmaker,
                threshold=_settings.reconciliation_drift_threshold,
            )
            app.state.drift_checker = drift_checker
            app.state.drift_checker_task = asyncio.create_task(
                drift_checker.run_forever(
                    interval_seconds=float(_settings.reconciliation_check_interval_seconds)
                )
            )

        # OpenRouterRecoverySweeper — periodic backstop that recovers authoritative cost for
        # client-disconnect rows the inline path (t6.2c) missed (v30 t6.3). Default-OFF:
        # started only when the interval knob is > 0 AND the cost-recovery service is wired.
        app.state.recovery_sweep_task = None
        _recovery_service = getattr(app.state, "cost_recovery_service", None)
        if (
            should_start_recovery_sweep(_settings.openrouter_recovery_sweep_interval_seconds)
            and _recovery_service is not None
        ):
            recovery_sweeper = OpenRouterRecoverySweeper(
                session_factory=_sessionmaker,
                recovery_service=_recovery_service,
                provider_resolver=app.state.provider_resolver,
            )
            app.state.recovery_sweeper = recovery_sweeper
            app.state.recovery_sweep_task = asyncio.create_task(
                recovery_sweeper.run_forever(
                    interval_seconds=float(_settings.openrouter_recovery_sweep_interval_seconds)
                )
            )

        # CreditHoldRecoverySweeper (credits-ledger TASK.md §3 M6) — periodic backstop that
        # releases orphaned HOLDs (a crashed/never-finalized request) older than
        # credits_hold_timeout_seconds. Default-ON (60s) — unlike the OpenRouter accuracy
        # backstop above, this protects tenant AVAILABILITY (an orphaned hold silently
        # starves a balance), so it defaults to running rather than opt-in.
        app.state.credit_recovery_sweep_task = None
        if should_start_credit_recovery_sweep(_settings.credits_recovery_sweep_interval_seconds):
            credit_recovery_sweeper = CreditHoldRecoverySweeper(
                session_factory=_sessionmaker,
                credit_guard=app.state.credit_guard,
                hold_timeout_s=_settings.credits_hold_timeout_seconds,
            )
            app.state.credit_recovery_sweeper = credit_recovery_sweeper
            app.state.credit_recovery_sweep_task = asyncio.create_task(
                credit_recovery_sweeper.run_forever(
                    interval_seconds=float(_settings.credits_recovery_sweep_interval_seconds)
                )
            )

        # RetentionSweeper — periodic bounded DELETE of aged time-series rows
        # (data-retention-controls v38). Default-ON at configured defaults; started only
        # when interval>0 AND at least one per-table window>0. Wired after recovery sweep.
        app.state.retention_sweeper_task = None
        # audit-remediation: ZDR-aware start-gate — also start the sweeper when any tenant
        # has zdr_enabled=true, even if every operator-level window knob is 0 (so ZDR
        # unconditional purge always runs). Honest-degrades to settings-only on DB error.
        if await should_start_retention_sweep_with_zdr(_settings, session_factory=_sessionmaker):
            retention_sweeper = RetentionSweeper(
                session_factory=_sessionmaker,
                settings=_settings,
                # tenant-retention-zdr TASK.md §3 — the ZDR unconditional purge pass
                # calls ObjectStore.delete() for s3-backed artifacts and bounded Redis
                # SCAN/DEL over resp-cache:/vec-cache: namespaces. Both are already
                # wired on app.state before lifespan startup runs.
                redis=_redis,
                object_store=app.state.object_store,
            )
            app.state.retention_sweeper = retention_sweeper
            app.state.retention_sweeper_task = asyncio.create_task(
                retention_sweeper.run_forever(
                    interval_seconds=float(_settings.retention_check_interval_seconds)
                )
            )

        # CatalogRefreshScheduler — periodic re-sync of the DB model catalog from the
        # wired CatalogSource (catalog-celery-refresh v2 — asyncio sweeper, NOT Celery:
        # the repo runs redis 8.x and celery/kombu caps redis<6.5). Default-ON (interval
        # 3600) per Tin's freeze; started only when catalog_refresh_interval_seconds>0.
        # Reuses SyncCatalogUseCase verbatim against app.state.catalog_source (already
        # wired in create_app before this lifespan runs) + the shared sessionmaker.
        app.state.catalog_refresh_task = None
        if should_start_catalog_refresh(_settings.catalog_refresh_interval_seconds):
            catalog_refresh_scheduler = CatalogRefreshScheduler(
                session_factory=_sessionmaker,
                catalog_source=app.state.catalog_source,
            )
            app.state.catalog_refresh_scheduler = catalog_refresh_scheduler
            app.state.catalog_refresh_task = asyncio.create_task(
                catalog_refresh_scheduler.run_forever(
                    interval_seconds=float(_settings.catalog_refresh_interval_seconds)
                )
            )

        # DomainVerifyNotifyScheduler — domain-verify-notify TASK.md §3 (FROZEN @ v1,
        # SECURITY): periodically re-checks opted-in pending domain claims via the FROZEN
        # VerifyDomainClaimUseCase DNS-TXT proof (reused verbatim, fail-closed) and emails
        # the claim owner exactly once on the first match. Pure asyncio sweeper (mirrors
        # CatalogRefreshScheduler) — NO celery. Reuses app.state.dns_resolver /
        # app.state.email_sender (already wired above this lifespan runs) + the shared
        # sessionmaker; started only when domain_verify_notify_interval_seconds>0.
        app.state.domain_verify_notify_task = None
        if should_start_domain_verify_notify(_settings.domain_verify_notify_interval_seconds):
            domain_verify_notify_scheduler = DomainVerifyNotifyScheduler(
                session_factory=_sessionmaker,
                dns_resolver=app.state.dns_resolver,
                email_sender=app.state.email_sender,
                dns_timeout_seconds=_settings.domain_verification_dns_timeout_seconds,
                origin=_settings.dashboard_public_origin,
            )
            app.state.domain_verify_notify_scheduler = domain_verify_notify_scheduler
            app.state.domain_verify_notify_task = asyncio.create_task(
                domain_verify_notify_scheduler.run_forever(
                    interval_seconds=float(_settings.domain_verify_notify_interval_seconds)
                )
            )

        # VideoJobWorker — durable Redis-backed in-process worker (v48 durable-queue).
        # Default-OFF: started only when video_durable_queue_enabled=True.
        # recover_orphans() runs BEFORE run_forever so restart-orphaned rows are
        # re-enqueued before the worker loop begins consuming.
        app.state.video_worker_task = None
        if should_start_video_worker(_settings):
            _video_queue = RedisVideoJobQueue(_redis)
            app.state.video_job_queue = _video_queue
            await recover_orphans(_sessionmaker, _video_queue)
            _video_worker = VideoJobWorker(
                sessionmaker=_sessionmaker,
                queue=_video_queue,
                settings=_settings,
                get_video_generator=lambda: getattr(app.state, "video_generator", None),
            )
            app.state.video_worker = _video_worker
            app.state.video_worker_task = asyncio.create_task(_video_worker.run_forever())

        # BatchJobWorker — durable Redis-backed in-process worker (batch-job-store, v57).
        # Default-OFF: started only when batch_durable_queue_enabled=True. Structurally
        # copied from the VideoJobWorker wiring immediately above.
        app.state.batch_worker_task = None
        if should_start_batch_worker(_settings):
            _batch_queue = RedisBatchJobQueue(_redis)
            app.state.batch_job_queue = _batch_queue
            await recover_batch_orphans(_sessionmaker, _batch_queue)
            _batch_worker = BatchJobWorker(
                sessionmaker=_sessionmaker,
                queue=_batch_queue,
                settings=_settings,
                get_batch_processor=lambda: getattr(app.state, "batch_processor", None),
            )
            app.state.batch_worker = _batch_worker
            app.state.batch_worker_task = asyncio.create_task(_batch_worker.run_forever())

        # BatchWindowFlusher — background drain of due BatchWindowBuffer windows
        # (batch-window-grouping §3). The buffer itself (app.state.batch_window_buffer)
        # is constructed in create_app()'s synchronous body (above) so it's always
        # present, even under ASGITransport-based tests; only this background
        # run_forever() task is lifespan-gated here, mirroring RetentionSweeper/
        # BatchJobWorker's wiring shape exactly. should_start_batch_window_flusher is
        # an operator escape hatch (batch_window_seconds<=0 disables the loop) —
        # BatchDiversionAdapter's own append path is unaffected either way.
        app.state.batch_window_flusher_task = None
        if should_start_batch_window_flusher(_settings):
            _batch_window_flusher = BatchWindowFlusher(
                buffer=app.state.batch_window_buffer,
                sessionmaker=_sessionmaker,
                app_state=app.state,
                settings=_settings,
                get_batch_processor=lambda: getattr(app.state, "batch_processor", None),
            )
            app.state.batch_window_flusher = _batch_window_flusher
            app.state.batch_window_flusher_task = asyncio.create_task(
                _batch_window_flusher.run_forever(
                    interval_seconds=BATCH_WINDOW_TICK_INTERVAL_SECONDS
                )
            )

        # InvoiceGenerator — month-close background loop (invoice-generation TASK.md
        # §3). Default-ON at configured defaults; started only when
        # invoice_generation_interval_seconds>0. Structurally copied from the
        # RetentionSweeper wiring immediately above.
        app.state.invoice_generator_task = None
        if should_start_invoice_generator(_settings):
            _invoice_generator = InvoiceGenerator(
                session_factory=_sessionmaker,
                stabilization_hours=_settings.invoice_stabilization_hours,
                # Couple invoice-skip to the SAME knob that turns on real-time credit
                # enforcement — one source of truth, they can never diverge (double-bill).
                credits_gate_enabled=_settings.credits_gate_enabled,
            )
            app.state.invoice_generator = _invoice_generator
            app.state.invoice_generator_task = asyncio.create_task(
                _invoice_generator.run_forever(
                    interval_seconds=float(_settings.invoice_generation_interval_seconds)
                )
            )

        # ReportScheduleGenerator — monthly Art. 12 bundle generation background loop
        # (compliance-report-center TASK.md §3 M15). Default-safe: OFF unless an
        # operator sets compliance_report_schedule_interval_seconds>0. Structurally
        # copied from the InvoiceGenerator wiring immediately above.
        app.state.report_schedule_generator_task = None
        if should_start_report_schedule_generator(_settings):
            _report_schedule_generator = ReportScheduleGenerator(
                session_factory=_sessionmaker,
                object_store=app.state.object_store,
                settings=_settings,
            )
            app.state.report_schedule_generator = _report_schedule_generator
            app.state.report_schedule_generator_task = asyncio.create_task(
                _report_schedule_generator.run_forever(
                    interval_seconds=float(_settings.compliance_report_schedule_interval_seconds)
                )
            )

        yield  # ── application is running ──

        # ── Shutdown ─────────────────────────────────────────────────────
        # 1. Cancel dispatcher + health_checker tasks (before flusher drain)
        dispatcher_task: asyncio.Task[None] | None = getattr(app.state, "dispatcher_task", None)
        if dispatcher_task is not None:
            dispatcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dispatcher_task

        health_task: asyncio.Task[None] | None = getattr(app.state, "health_checker_task", None)
        if health_task is not None:
            health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health_task

        drift_task: asyncio.Task[None] | None = getattr(app.state, "drift_checker_task", None)
        if drift_task is not None:
            drift_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drift_task

        sweep_task: asyncio.Task[None] | None = getattr(app.state, "recovery_sweep_task", None)
        if sweep_task is not None:
            sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task

        credit_sweep_task: asyncio.Task[None] | None = getattr(
            app.state, "credit_recovery_sweep_task", None
        )
        if credit_sweep_task is not None:
            credit_sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await credit_sweep_task

        retention_task: asyncio.Task[None] | None = getattr(
            app.state, "retention_sweeper_task", None
        )
        if retention_task is not None:
            retention_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retention_task

        catalog_refresh_task: asyncio.Task[None] | None = getattr(
            app.state, "catalog_refresh_task", None
        )
        if catalog_refresh_task is not None:
            catalog_refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await catalog_refresh_task

        domain_verify_notify_task: asyncio.Task[None] | None = getattr(
            app.state, "domain_verify_notify_task", None
        )
        if domain_verify_notify_task is not None:
            domain_verify_notify_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await domain_verify_notify_task

        video_worker_task: asyncio.Task[None] | None = getattr(app.state, "video_worker_task", None)
        if video_worker_task is not None:
            video_worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await video_worker_task

        batch_worker_task: asyncio.Task[None] | None = getattr(app.state, "batch_worker_task", None)
        if batch_worker_task is not None:
            batch_worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await batch_worker_task

        batch_window_flusher_task: asyncio.Task[None] | None = getattr(
            app.state, "batch_window_flusher_task", None
        )
        if batch_window_flusher_task is not None:
            batch_window_flusher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await batch_window_flusher_task

        invoice_generator_task: asyncio.Task[None] | None = getattr(
            app.state, "invoice_generator_task", None
        )
        if invoice_generator_task is not None:
            invoice_generator_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await invoice_generator_task

        report_schedule_generator_task: asyncio.Task[None] | None = getattr(
            app.state, "report_schedule_generator_task", None
        )
        if report_schedule_generator_task is not None:
            report_schedule_generator_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await report_schedule_generator_task

        # 2. Final run_once()/check_once() drain cycle for dispatcher/health
        with contextlib.suppress(Exception):
            await dispatcher.run_once()
        if _settings.health_check_interval_seconds > 0:
            hc = getattr(app.state, "health_checker", None)
            if hc is not None:
                with contextlib.suppress(Exception):
                    await hc.check_once()
        dc = getattr(app.state, "drift_checker", None)
        if dc is not None:  # only set when the default-OFF guard started it
            with contextlib.suppress(Exception):
                await dc.check_once()

        # 2b. Cancel OtelFlusher task + final flush_once()
        otel_flusher_task: asyncio.Task[None] | None = getattr(app.state, "otel_flusher_task", None)
        if otel_flusher_task is not None:
            otel_flusher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await otel_flusher_task
        otel_flusher = getattr(app.state, "otel_flusher", None)
        if otel_flusher is not None:
            with contextlib.suppress(Exception):
                await otel_flusher.flush_once()

        # 2c. Cancel outstanding video generation job tasks
        video_tasks: set[asyncio.Task[None]] = getattr(app.state, "video_jobs_tasks", set())
        for _vt in list(video_tasks):
            _vt.cancel()
        if video_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*video_tasks, return_exceptions=True)

        # 2d. Cancel outstanding batch job tasks
        batch_tasks: set[asyncio.Task[None]] = getattr(app.state, "batch_jobs_tasks", set())
        for _bt in list(batch_tasks):
            _bt.cancel()
        if batch_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*batch_tasks, return_exceptions=True)

        # 3. Cancel the flusher background task
        flusher_task: asyncio.Task[None] | None = getattr(app.state, "flusher_task", None)
        if flusher_task is not None:
            flusher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher_task

        # 4. Drain remaining PEL entries up to the timeout
        await flusher.drain_until_empty(timeout=float(_settings.shutdown_drain_timeout_seconds))

        # 5. Close Redis client
        with contextlib.suppress(Exception):
            await _redis.aclose()

        # 6. Dispose SQLAlchemy engine
        with contextlib.suppress(Exception):
            await _engine.dispose()

        # 7. Close httpx client if stored on app.state
        httpx_client: httpx.AsyncClient | None = getattr(app.state, "httpx_client", None)
        if httpx_client is not None:
            with contextlib.suppress(Exception):
                await httpx_client.aclose()

    app = FastAPI(title="Hydroa Gateway", version="0.1.0", lifespan=lifespan)

    # Per-app Prometheus registry — prevents Duplicated timeseries errors when
    # multiple create_app() calls exist in a single pytest run.
    app.state.metrics_registry = MetricsRegistry(registry=CollectorRegistry())

    # Pre-initialize lifespan-managed task handles so tests using ASGITransport
    # (which does not trigger lifespan) can still inspect these attributes.
    # The lifespan overwrites them on startup.
    app.state.health_checker_task = None
    app.state.drift_checker_task = None
    app.state.recovery_sweep_task = None
    app.state.credit_recovery_sweep_task = None
    app.state.retention_sweeper_task = None
    app.state.batch_window_flusher_task = None
    app.state.invoice_generator_task = None
    app.state.report_schedule_generator_task = None

    # Video generation seam — default: no provider (honest degradation).
    # Tests override via app.state.video_generator = <stub>.
    app.state.video_generator = None
    # Tracked in-process asyncio.Task set for video jobs.
    # The lifespan cancels any outstanding tasks on shutdown.
    app.state.video_jobs_tasks = set()  # set[asyncio.Task[None]] — no inline type on app.state
    # Durable video job worker task (v48 durable-queue). Default None (OFF).
    # Set to the running asyncio.Task when video_durable_queue_enabled=True.
    app.state.video_worker_task = None

    # Batch processor seam (batch-job-store, v57) — default: no processor (honest
    # degradation to status=failed error=no_batch_processor_configured). Tests override
    # via app.state.batch_processor = <stub>. openai-batch-adapter / anthropic-batch-adapter
    # (downstream tasks) plug in a real one.
    app.state.batch_processor = None
    # Tracked in-process asyncio.Task set for batch jobs — mirrors video_jobs_tasks.
    app.state.batch_jobs_tasks = set()  # set[asyncio.Task[None]] — no inline type on app.state
    # Durable batch job worker task. Default None (OFF).
    # Set to the running asyncio.Task when batch_durable_queue_enabled=True.
    app.state.batch_worker_task = None

    engine = create_async_engine(settings.database_url)
    app.state.settings = settings
    # ObjectStore for the artifacts byte path — None when unconfigured (honest-degrade
    # to inline BYTEA, the v45 behavior). Tests override app.state.object_store.
    app.state.object_store = build_object_store(settings)
    # EmailSender for ancillary fire-and-forget outbound email (transactional-email) —
    # ConsoleEmailSender (no real delivery) unless email_smtp_enabled=true. Tests
    # override app.state.email_sender.
    app.state.email_sender = build_email_sender(settings)
    app.state.engine = engine
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    # residency-policy TASK.md §3 (FROZEN @ v2): ONE shared ResidencyLookup instance,
    # wired into BOTH enforcement tiers (Tier 1 governance checks via *_deps.py/
    # realtime_*.py/memory/api/router.py, Tier 2 router dial-constraint filter via
    # build_model_router below) plus the chat CompletionUseCase and cache-hit
    # re-validation. Constructed here (not per-request) — mirrors
    # DbTenantModelPresetStore/DbTenantProviderKeyStore: opens its own short-lived
    # session per call, safe to share across concurrent requests. Tests override via
    # app.state.residency_lookup.
    app.state.residency_lookup = SqlAlchemyResidencyLookup(sessionmaker=app.state.sessionmaker)
    app.state.password_hasher = Argon2PasswordHasher()
    app.state.token_service = JwtTokenService(settings)
    # self-serve-checkout TASK.md §3 (M10): dev provider is DEFAULT ON; stripe is selected
    # only when configured (the Settings boot validator guarantees a non-empty stripe key).
    # Tests override app.state.payment_provider to inject a failing/stripe adapter.
    app.state.payment_provider = build_payment_provider(settings)
    # Default catalog source — tests override via app.state.catalog_source.
    # catalog-db-seed TASK.md §3 (FROZEN @ v1, M6): the DB seed migration is now the SOLE
    # source of truth for the former static seed rows (minimax/gpt-realtime/bedrock/vertex);
    # `static_models` is dropped entirely, so the provider-scoped `sync_catalog` deactivation
    # (M5) never wipes them on an OpenRouter-only sync.
    app.state.catalog_source = CompositeCatalogSource(
        primary=OpenRouterCatalogSource(httpx.AsyncClient()),
    )
    # Proxy defaults — tests inject fakes via app.state
    app.state.circuit_breaker = CircuitBreaker()
    # audit-remediation package C1 (MED proxy global breaker): per-provider breaker
    # registry consulted by proxy/api/deps.py::get_completion_upstream. Keyed lazily
    # by resolved catalog provider (dict.setdefault) so a trip on one provider's
    # breaker never blocks another's. app.state.circuit_breaker above is kept
    # unchanged for backward compatibility with callers outside deps.py (e.g. the
    # realtime websocket path in proxy/api/realtime_ws.py, which is out of scope
    # for this fix and still uses the single legacy breaker).
    # dict[str, CircuitBreaker], lazily populated per resolved provider.
    app.state.provider_circuit_breakers = {}
    # Raw OpenRouter upstream — used directly by the provider adapter map and the
    # OpenRouterUpstreamFacade (embeddings/images). NOT the dispatch wrapper.
    # No api_key= argument: auth is resolved per-request from the contextvar
    # set by the use-case (credential-resolution-seam §3).
    _openrouter_upstream = OpenRouterCompletionUpstream(
        base_url=settings.openrouter_base_url,
        max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        retry_deadline_s=settings.upstream_retry_deadline_s,
        metrics_registry=app.state.metrics_registry,
        usage_accounting=settings.openrouter_usage_accounting,
    )
    # Public seam for production-wiring regression tests: the RAW OpenRouter
    # upstream is the live adapter that Settings (max_retries / backoff_base /
    # base_url / metrics_registry) are threaded into. v9 relocated it out of
    # app.state.completion_upstream (now the dispatch wrapper), so the v6
    # wiring-regression rule asserts config threading against this name.
    app.state.openrouter_completion_upstream = _openrouter_upstream

    # Build the catalog loader closure for the provider resolver.
    # Imports ModelRow locally to avoid a circular import at module level.
    from sqlalchemy import select as _sa_select

    from gateway.catalog.infrastructure.orm import ModelRow as _ModelRow

    # chat-modality-guard (v56 §3): a single query fetches id/provider/modality; the
    # modality column is stashed in this closure-scoped cache so _load_modality_map()
    # below can hand it to the resolver with ZERO extra I/O (same refresh cycle,
    # ordered: refresh() always calls _load_provider_map() before _load_modality_map()).
    _last_modality_cache: dict[str, str] = {}

    async def _load_provider_map() -> dict[str, str]:
        async with app.state.sessionmaker() as _session:
            _rows = (
                await _session.execute(
                    _sa_select(_ModelRow.id, _ModelRow.provider, _ModelRow.modality)
                )
            ).all()
            _last_modality_cache.clear()
            _last_modality_cache.update({row.id: row.modality for row in _rows if row.modality})
            return {row.id: row.provider for row in _rows}

    async def _load_modality_map() -> dict[str, str]:
        return dict(_last_modality_cache)

    app.state.provider_resolver = CatalogProviderResolver(
        loader=_load_provider_map,
        modality_loader=_load_modality_map,
    )

    # Chat adapter map — ALL providers (openrouter / anthropic / google / openai /
    # bedrock / azure) are registered UNCONDITIONALLY. Per-tenant key gating moved to
    # resolve time (credential-resolution-seam §3 + dynamic-auth-byok §3):
    # ProviderKeyMissing is raised at request dispatch, not at boot. Bedrock/Azure now
    # resolve their credentials per-request from the contextvar (task-3 dynamic-auth-byok).
    _chat_adapters: dict[str, CompletionUpstream] = {"openrouter": _openrouter_upstream}

    # Anthropic adapter — UNCONDITIONAL (credential resolved per-request from contextvar).
    _chat_adapters["anthropic"] = AnthropicCompletionUpstream(
        base_url=settings.anthropic_base_url,
        anthropic_version=settings.anthropic_version,
        default_max_tokens=settings.anthropic_default_max_tokens,
        max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        retry_deadline_s=settings.upstream_retry_deadline_s,
        metrics_registry=app.state.metrics_registry,
        auto_cache=settings.anthropic_auto_cache,
    )

    # Google (Gemini) adapter — UNCONDITIONAL (credential resolved per-request from contextvar).
    _chat_adapters["google"] = GeminiCompletionUpstream(
        base_url=settings.google_base_url,
        default_max_tokens=settings.google_default_max_tokens,
        max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        retry_deadline_s=settings.upstream_retry_deadline_s,
        metrics_registry=app.state.metrics_registry,
        max_inline_bytes=settings.gemini_inline_max_bytes,
    )

    # OpenAI direct adapter — UNCONDITIONAL (credential resolved per-request from contextvar).
    # Registered in both _chat_adapters (for chat dispatch) and _providers (for non-chat
    # modalities: embeddings/images/audio). The same instance is reused in _providers below.
    _openai_direct = OpenAIDirectProvider(
        base_url=settings.openai_base_url,
        max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        retry_deadline_s=settings.upstream_retry_deadline_s,
        metrics_registry=app.state.metrics_registry,
    )
    _chat_adapters["openai"] = _openai_direct

    # MiniMax direct adapter — UNCONDITIONAL (credential resolved per-request from
    # contextvar). Chat-only: registered in _chat_adapters ONLY, NOT in _providers
    # (minimax has no embeddings/images/audio modality — provider is OpenAI-wire
    # compatible for /chat/completions only). Same shape as _openai_direct above,
    # base_url swapped, provider_name="minimax" so errors/metrics label correctly.
    _chat_adapters["minimax"] = OpenAIDirectProvider(
        base_url=settings.minimax_base_url,
        provider_name="minimax",
        max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        retry_deadline_s=settings.upstream_retry_deadline_s,
        metrics_registry=app.state.metrics_registry,
    )

    # AWS Bedrock adapter — registered UNCONDITIONALLY (task-3 dynamic-auth-byok).
    # Credentials are resolved per-request from the tenant contextvar; no boot-env
    # credential check. A request with no tenant Bedrock key → 402 at resolve time.
    _chat_adapters["bedrock"] = BedrockCompletionUpstream(
        endpoint_url=settings.bedrock_endpoint_url or None,
        default_max_tokens=settings.anthropic_default_max_tokens,
        max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        retry_deadline_s=settings.upstream_retry_deadline_s,
        metrics_registry=app.state.metrics_registry,
    )

    # S3 SSRF/IMDS/credential-exfiltration egress policy (edge-input-hardening TASK.md §3
    # Part B) — ONE settings-derived instance shared across every BYOK-influenced Azure
    # construction site below (token cache + both adapters), so an operator's
    # GATEWAY_EGRESS_ALLOW_PRIVATE_RANGES / GATEWAY_EGRESS_ALLOW_HTTP_DEV / resolve-timeout
    # config reaches every dial consistently. This is the REAL, deny-by-default policy —
    # only test suites explicitly override with AllowAllEgressPolicy at their own
    # construction sites.
    _azure_egress_policy = DenyPrivateAndMetadataEgressPolicy(
        allow_private_ranges=settings.egress_allow_private_ranges,
        allow_http=settings.egress_allow_http_dev,
        resolve_timeout_s=settings.egress_dns_resolve_timeout_s,
    )

    # Per-tenant Azure AD token provider cache — one shared instance on app.state,
    # injected into both Azure adapters (chat + embeddings). Keyed by the NON-SECRET
    # AzureADConfig identity (tenant_id, client_id, authority, scope).
    _azure_ad_token_provider_cache = AzureADTokenProviderCache(
        ttl_s=settings.azure_ad_provider_cache_ttl_s,
        max_size=settings.azure_ad_provider_cache_max,
        metrics_registry=app.state.metrics_registry,
        egress_policy=_azure_egress_policy,
    )
    app.state.azure_ad_token_provider_cache = _azure_ad_token_provider_cache

    # Azure OpenAI adapter — registered UNCONDITIONALLY (task-3 dynamic-auth-byok).
    # Credentials (endpoint, api_key / AAD config) are resolved per-request from the
    # tenant contextvar. A request with no tenant Azure key → 402 at resolve time.
    _chat_adapters["azure"] = AzureCompletionUpstream(
        token_provider_cache=_azure_ad_token_provider_cache,
        max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        retry_deadline_s=settings.upstream_retry_deadline_s,
        metrics_registry=app.state.metrics_registry,
        egress_policy=_azure_egress_policy,
    )

    # Per-tenant Vertex JWT-bearer token provider cache — one shared instance on
    # app.state. Keyed by the NON-SECRET identity (client_email, project_id).
    _vertex_token_provider_cache = VertexTokenProviderCache(
        ttl_s=settings.vertex_token_cache_ttl_s,
        max_size=settings.vertex_token_cache_max,
        metrics_registry=app.state.metrics_registry,
    )
    app.state.vertex_token_provider_cache = _vertex_token_provider_cache

    # Google Vertex AI adapter — registered UNCONDITIONALLY (vertex-adapter task,
    # mirrors every sibling adapter). Credentials (GoogleServiceAccountCredential) are
    # resolved per-request from the tenant contextvar. A request with no tenant Vertex
    # key → 402 at resolve time (M12/R2). The provider="vertex" catalog rows now live in
    # the DB seed migration (catalog-db-seed) — M10/R6 still holds: never seed a
    # provider="vertex" catalog row without the matching adapter registered here.
    _chat_adapters["vertex"] = VertexCompletionUpstream(
        token_provider_cache=_vertex_token_provider_cache,
        default_max_tokens=settings.vertex_default_max_tokens,
        max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        retry_deadline_s=settings.upstream_retry_deadline_s,
        metrics_registry=app.state.metrics_registry,
    )

    # Public seam for wiring tests: exposes the adapter map so tests can assert
    # which adapters are registered (mirrors the openrouter_completion_upstream seam).
    app.state.chat_adapters = _chat_adapters

    # Dispatch wrapper — implements CompletionUpstream; selection only.
    app.state.completion_upstream = ProviderAwareCompletionUpstream(
        adapters=_chat_adapters,
        resolver=app.state.provider_resolver,
    )

    # Usage metering: wire RecordingUsageRecorder + background flusher
    # Tests inject fakes via app.state.usage_recorder AFTER app creation;
    # the default here is used in production only.
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
    app.state.redis_client = redis_client
    app.state.usage_recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )

    # tool-call-metering TASK.md §3 M1: MeteringToolCallObserver is the ONLY production
    # wiring for gateway.mcp_connector.domain.ports.ToolCallObserver, replacing the
    # sibling mcp-connector-passthrough task's NoopToolCallObserver default (its
    # api/deps.py getattr()-falls-back-to-Noop pattern is unchanged; this just makes
    # app.state.mcp_tool_call_observer non-None in production). Constructed with the
    # SAME usage_recorder + redis_client instances already wired immediately above —
    # no second Redis/session instance (M1). Tests override via
    # app.state.mcp_tool_call_observer AFTER app creation, same pattern as usage_recorder.
    app.state.mcp_tool_call_observer = MeteringToolCallObserver(
        usage_recorder=app.state.usage_recorder,
        redis=redis_client,
    )

    # Budget guard: wire RedisBudgetGuard for production;
    # tests override via app.state.budget_guard after app creation.
    app.state.budget_guard = RedisBudgetGuard(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )

    # Plan rate-limit resolver (plan-rate-enforcement TASK.md §3): resolves a tenant's
    # effective rpm/tpm ceiling (tenant override -> plan default -> None) for the
    # tenant-layer rate window, composing with the existing per-key ceiling. Active by
    # default (Tin 2026-07-15, matching the plan's budget ceiling being enforced by
    # default); fail-open — resolver.resolve never raises, so a DB error admits the
    # request. Tests override via app.state.plan_rate_limit_resolver after app creation.
    app.state.plan_rate_limit_resolver = PlanRateLimitResolver(
        session_factory=app.state.sessionmaker,
    )

    # Credit guard (credits-ledger TASK.md §3): CENTRAL KNOB-KILL via
    # settings.credits_gate_enabled (default False) — PassthroughCreditGuard until an
    # operator explicitly opts in, so no existing tenant is fail-closed at $0 the moment
    # this module ships (a tenant that never topped up has NO tenant_credit_balances
    # row; check_and_hold would otherwise reject its very first request). Tests override
    # via app.state.credit_guard after app creation, same as budget_guard above.
    app.state.credit_guard = (
        PostgresCreditGuard(
            session_factory=app.state.sessionmaker,
            metrics=app.state.metrics_registry,
        )
        if settings.credits_gate_enabled
        else PassthroughCreditGuard()
    )

    # Rate limiter: wire RedisLuaRateLimiter for production;
    # tests override via app.state.rate_limiter after app creation.
    app.state.rate_limiter = RedisLuaRateLimiter(redis=redis_client)

    # Per-IP rate limiter for the device-authorization endpoint (fail-open on Redis outage).
    # Built from the same redis_client; no IO at construction (safe without lifespan).
    app.state.agent_oauth_ip_limiter = AgentOAuthIpRateLimiter(redis=redis_client)

    # Per-user rate limiter for the playground-token mint (security review F1): bounds a
    # caller who bypasses the BFF cache to flood POST /admin/keys/playground-token.
    # Same redis_client; no IO at construction; fail-open on Redis outage.
    app.state.playground_mint_limiter = PlaygroundMintRateLimiter(redis=redis_client)

    # Per-client-IP rate limiter for the public invite preview/accept endpoints
    # (member-invite-acceptance TASK.md §3, M7). Same redis_client; no IO at construction;
    # fail-open on Redis outage.
    app.state.invite_public_limiter = InvitePublicRateLimiter(redis=redis_client)

    # Per-client-IP rate limiter for the public access-requests endpoint
    # (signup-refusal-router TASK.md §3, M7). Same redis_client; no IO at construction;
    # fail-open on Redis outage.
    app.state.access_request_limiter = AccessRequestIpRateLimiter(redis=redis_client)

    # Per-scim_token_id rate limiter for /scim/v2/* writes (scim-provisioning TASK.md §3,
    # M12). Same redis_client; no IO at construction; fail-open on Redis outage.
    app.state.scim_rate_limiter = ScimTokenRateLimiter(redis=redis_client)

    # Per-tenant rate limiter for the domain-claims create/verify endpoints (domain-capture
    # TASK.md §3, M14). Same redis_client; no IO at construction; fail-open on Redis outage.
    app.state.domain_claim_rate_limiter = DomainClaimRateLimiter(redis=redis_client)

    # DNS TXT resolver for domain-claim verification (domain-capture TASK.md §3, M6/M13).
    # Stateless — no IO at construction, safe without lifespan. Tests override via
    # app.state.dns_resolver.
    app.state.dns_resolver = DnsPythonTxtResolver()

    # Domain-claim repository/resolver — constructed PER-REQUEST in
    # domain_capture/api/deps.py (needs a request-scoped session); these are the
    # test-injection seams only (mirrors app.state.saml_config_resolver = None).
    app.state.domain_claim_repository = None
    app.state.domain_claim_resolver = None

    # Bandwidth pacing (stream-bandwidth-pacing, v36): per-key aggregate token-bucket.
    # rate==0 (default) → PassthroughBandwidthBucket → byte-identical (no pacing, no Redis).
    # Construction does NOT connect to Redis (safe without lifespan); tests override via app.state.
    if settings.bandwidth_tokens_per_sec > 0:
        app.state.bandwidth_bucket = RedisTokenBucket(
            redis=redis_client,
            rate=settings.bandwidth_tokens_per_sec,
            burst=settings.bandwidth_burst_tokens,
        )
    else:
        app.state.bandwidth_bucket = PassthroughBandwidthBucket()

    # Tier-capacity guard (service-tiers TASK.md §3): cluster_cap<=0 (default) →
    # PassthroughTierCapacityGuard → byte-identical (no admission gating, no Redis).
    # Construction does NOT connect to Redis (register_script only — safe without
    # lifespan, same precedent as bandwidth_bucket/rate_limiter above). Tests override
    # via app.state.tier_capacity_guard after app creation.
    if settings.tier_capacity_cluster_cap > 0:
        app.state.tier_capacity_guard = RedisTierCapacityGuard(
            redis=redis_client,
            cluster_cap=settings.tier_capacity_cluster_cap,
            priority_reserved_pct=settings.tier_priority_reserved_pct,
            standard_reserved_pct=settings.tier_standard_reserved_pct,
            hold_ttl_s=settings.tier_capacity_hold_ttl_s,
        )
    else:
        app.state.tier_capacity_guard = PassthroughTierCapacityGuard()

    # Cooldown circuit breaker gate — constructed only when threshold > 0.
    # Construction does NOT connect to Redis (safe without lifespan).
    # threshold == 0 (default) → gate is None; preserves v5 behavior byte-identically.
    if settings.cooldown_failure_threshold > 0:
        app.state.cooldown_gate = RedisCooldownGate(
            redis=redis_client,
            metrics_registry=app.state.metrics_registry,
            threshold=settings.cooldown_failure_threshold,
            ttl_s=settings.cooldown_ttl_s,
            window_s=settings.cooldown_window_s,
        )
    else:
        app.state.cooldown_gate = None

    # Model-group alias router — sits above completion_upstream in the use-case call chain.
    # health_gate is wired from app.state.cooldown_gate (None when feature disabled).
    # SEAM NOTE: The router is constructed with app.state.completion_upstream as its default
    # upstream. However, the use case passes the per-request upstream (circuit-breaker-wrapped,
    # potentially a test fake) via the `upstream` override kwarg on router.complete() and
    # router.stream(). This preserves the frozen-suite injection contract: tests that set
    # app.state.completion_upstream = FakeUpstream still work correctly.
    # Model router built from the env settings. The load/limit gates are constructed inside
    # build_model_router (load gate only for least-busy/latency; limit gate only when a
    # deployment declares a limit) — no Redis IO at construction (safe without lifespan).
    # A persisted routing config, when present, REBUILDS this at boot in the lifespan startup
    # (v32 routing-config-store; restart-to-apply — no live-traffic mutation).
    app.state.model_router = build_model_router(
        settings,
        redis_client=redis_client,
        completion_upstream=app.state.completion_upstream,
        cooldown_gate=app.state.cooldown_gate,
        metrics_registry=app.state.metrics_registry,
        residency_lookup=getattr(app.state, "residency_lookup", None),
    )

    # Provider registry — additive seam for non-chat modalities (provider-seam TASK.md §3).
    # The chat path (app.state.completion_upstream / app.state.model_router) is UNCHANGED.
    # The registry is consulted ONLY by non-chat endpoint tasks (embeddings/images/audio).
    # "openrouter" facade wraps completion_upstream for interface consistency.
    # "openai" is added only when openai_api_key is non-empty (empty = provider absent).
    # CRITICAL: facade wraps the RAW _openrouter_upstream, NOT the dispatch wrapper.
    # The dispatch wrapper is for chat only; the facade is for non-chat modalities
    # (embeddings/images/audio) and must not add provider-dispatch overhead.
    _openrouter_facade = OpenRouterUpstreamFacade(upstream=_openrouter_upstream)
    # Bearer provider registry entries — UNCONDITIONAL (credential resolved per-request).
    # openai and google are always wired; per-tenant key gating moved to resolve time.
    _providers: dict[str, UpstreamProvider] = {
        "openrouter": _openrouter_facade,
        "openai": _openai_direct,  # reuse the instance already in _chat_adapters
        "google": GoogleEmbeddingsProvider(
            base_url=settings.google_base_url,
            metrics_registry=app.state.metrics_registry,
        ),
    }
    # AWS Bedrock embeddings adapter — registered UNCONDITIONALLY (task-3 dynamic-auth-byok).
    _providers["bedrock"] = BedrockEmbeddingsProvider(
        endpoint_url=settings.bedrock_endpoint_url or None,
        metrics_registry=app.state.metrics_registry,
    )
    # Azure OpenAI embeddings adapter — registered UNCONDITIONALLY (task-3 dynamic-auth-byok).
    # Shares the same AzureADTokenProviderCache as the chat adapter (one cache, no double minting).
    _providers["azure"] = AzureEmbeddingsProvider(
        token_provider_cache=_azure_ad_token_provider_cache,
        metrics_registry=app.state.metrics_registry,
        egress_policy=_azure_egress_policy,
    )
    app.state.provider_registry = ProviderRegistry(_providers)

    # Tenant provider key store + resolver (credential-resolution-seam §3).
    # Wire on app.state so tests can override via app.state.tenant_credential_resolver.
    app.state.tenant_provider_key_store = DbTenantProviderKeyStore(
        sessionmaker=app.state.sessionmaker,
        settings=settings,
    )
    app.state.tenant_credential_resolver = CachedTenantCredentialResolver(
        store=app.state.tenant_provider_key_store,
        settings=settings,
    )
    # platform-key-default: default-ON platform-tenant credential fallback (kill-switch
    # GATEWAY_PLATFORM_CREDENTIAL_FALLBACK_ENABLED). A keyless tenant is served the reserved
    # kind='platform' tenant's own credential; its OWN key always takes precedence. Composed
    # OUTSIDE resolver.resolve() so the fail-closed invariant holds for every other caller.
    # Tests can override via app.state.platform_credential_fallback.
    app.state.platform_credential_fallback = PlatformCredentialFallbackService(
        session_factory=app.state.sessionmaker,
        enabled=settings.platform_credential_fallback_enabled,
    )

    # ml-moderation-layer (§3 CONTRACT — FROZEN @ v1): a DEDICATED OpenAIDirectProvider
    # + CircuitBreaker instance for the moderation IO seam, isolated from _openai_direct
    # (chat/embeddings) so a moderation-provider outage can never trip real completions
    # and a completions outage can never disable moderation (§0 R3, M8). Tighter
    # timeouts than the chat default (connect=1.5s/read=2.5s vs. 10s/120s) — a hot-path
    # pre-call check must fail fast (§0 R1). No separate global kill-switch
    # (FREEZE-QUESTION 4, decided at freeze): the true off switch is the per-tenant
    # ml_moderation.enabled flag (guardrail_router.py) — wiring here only makes the
    # feature REACHABLE; deps.py still gates construction of the composite evaluator
    # on this being non-None (M9).
    app.state.ml_moderation_provider = OpenAiModerationClient(
        OpenAIDirectProvider(
            base_url=settings.openai_base_url,
            connect_timeout=1.5,
            read_timeout=2.5,
            metrics_registry=app.state.metrics_registry,
        )
    )

    # Tenant model-preset store (tenant-preset-store v56). upsert() constructs its own
    # SqlAlchemyModelChecker per call, scoped to the session it opens (see the store's
    # module docstring) — matching every other call site in the codebase. Consumed by
    # the proxy ingress via getattr(app.state, "tenant_model_preset_store", None) in
    # deps.py/images_deps.py/embeddings_deps.py/audio_deps.py/realtime_ws.py
    # (preset-resolution-ingress v56).
    app.state.tenant_model_preset_store = DbTenantModelPresetStore(
        sessionmaker=app.state.sessionmaker,
    )

    # batch-window-grouping (§3): the per-tenant fixed-tick accumulation buffer.
    # Constructed here (create_app()'s synchronous body), NOT inside the async
    # lifespan below — unlike BatchWindowFlusher's background drain loop, the buffer
    # itself is touched on every eligible request (BatchDiversionAdapter.try_divert),
    # so it must exist even under ASGITransport-based tests (which never fire
    # lifespan). No I/O happens at construction (redis.asyncio clients are lazy),
    # mirroring RedisBatchJobQueue/BatchDiversionAdapter's own safe-without-lifespan
    # shape. Tests override via app.state.batch_window_buffer.
    app.state.batch_window_buffer = BatchWindowBuffer(redis=redis_client, settings=settings)

    # Batch-auto-grouping diversion adapter (v57 §3), superseded body @ batch-window-
    # grouping (§3): safety comes from the per-tenant authz.batch_grouping_enabled
    # flag (default false, checked in CompletionUseCase.complete()) and the restored
    # M4 safety-gate inside the adapter itself (batch_processor is None today, pre
    # openai-batch-adapter/anthropic-batch-adapter, so try_divert() always returns
    # None ⇒ byte-identical). Tests override via app.state.batch_diversion.
    app.state.batch_diversion = BatchDiversionAdapter(
        settings=settings,
        buffer=app.state.batch_window_buffer,
    )

    # payload-capture-store (§3): opt-in, PII-scrubbed request/response capture.
    # zdr_port is wired to RetentionZdrPort — a live adapter over the sibling
    # tenant-retention-zdr task's SHIPPED tenants.zdr_enabled column (via
    # tenants/application/retention_policy.py:is_zdr), bounded by
    # capture_persist_timeout_seconds and fail-closed on any error/timeout (verify
    # fix 2026-07-10: this was previously wired to the permissive AlwaysAllowCapture
    # no-op, making the entire ZDR fail-closed contract for payload capture dead code
    # in production). Tests override via app.state.zdr_port. payload_capture is the
    # REAL DB-backed adapter (not NoopPayloadCapture — capture is opt-in/default-off
    # via the per-tenant/per-key toggle, not via a Noop production wiring); tests
    # override via app.state.payload_capture.
    app.state.zdr_port = RetentionZdrPort(
        session_factory=app.state.sessionmaker,
        timeout_seconds=settings.capture_persist_timeout_seconds,
    )
    app.state.payload_capture = SqlAlchemyPayloadCapture(
        session_factory=app.state.sessionmaker,
        zdr_port=app.state.zdr_port,
        timeout_seconds=settings.capture_persist_timeout_seconds,
        max_field_bytes=settings.capture_max_field_bytes,
        max_body_bytes=settings.capture_max_body_bytes,
        max_concurrent_tasks=settings.capture_max_concurrent_tasks,
    )

    # openrouter-cost-recovery-wiring (v30 t6.2c): inline authoritative-cost recovery for
    # disconnected OpenRouter streams. Constructed ONLY when the knob is on (default OFF ⇒
    # None ⇒ byte-identical streaming). The use-case schedules recover() fire-and-forget
    # from the disconnect handler; the periodic sweep (t6.3) is the reliable backstop.
    # Tests override via app.state.cost_recovery_service after app creation.
    app.state.cost_recovery_service = (
        OpenRouterCostRecoveryService(
            upstream=app.state.openrouter_completion_upstream,
            recorder=app.state.usage_recorder,
            session_factory=app.state.sessionmaker,
            credential_resolver=app.state.tenant_credential_resolver,
        )
        if settings.openrouter_cost_recovery_enabled
        else None
    )

    # Cache TTL — exposed on app.state so proxy router can read it per-request.
    # cache_max_ttl_seconds caps any per-request Cache-Control: max-age override.
    app.state.cache_ttl_seconds = settings.cache_ttl_seconds
    app.state.cache_max_ttl_seconds = settings.cache_max_ttl_seconds
    # Pre-first-byte streaming resilience flag — read by deps to wire CompletionUseCase.
    app.state.stream_resilience_enabled = settings.upstream_stream_resilience_enabled
    # Embedding-similarity "vector" cache knobs (semantic-cache v19) — read by deps to wire
    # the RedisVectorCache + its embedding adapter. Default-off ⇒ deps wires None ⇒ byte-identical.
    app.state.vector_cache_enabled = settings.vector_cache_enabled
    app.state.vector_cache_threshold = settings.vector_cache_threshold
    app.state.vector_cache_embed_model = settings.vector_cache_embed_model
    app.state.vector_cache_max_candidates = settings.vector_cache_max_candidates

    # JWKS key cache — always created so tests can inject the seam regardless of
    # oidc_enabled (per-tenant DB config can be used even when oidc_enabled=False).
    # app.state.jwks_client starts unset (None by absence); tests inject via
    # oidc_app.state.jwks_client = FakeJwksClient(...).
    from gateway.auth.application.jwks_key_cache import JwksKeyCache

    app.state.jwks_key_cache = JwksKeyCache()

    # OidcConfigResolver seam — always initialized (None = production DB resolver
    # constructed per-request in deps.py; tests override via app.state.oidc_config_resolver).
    # None here means: use DbOidcConfigResolver (session-scoped) in production.
    app.state.oidc_config_resolver = None

    # SAML seams — always initialized to None (= production adapters constructed
    # per-request in saml_deps.py; tests override via app.state.saml_config_resolver /
    # saml_request_store / saml_replay_cache). Mirrors the OIDC seam pattern above.
    app.state.saml_config_resolver = None
    app.state.saml_request_store = None
    app.state.saml_replay_cache = None

    register_error_handlers(app)
    register_scim_error_handlers(app)
    register_checkout_error_handler(app)
    app.include_router(agent_oauth_device_router)
    app.include_router(agent_oauth_approval_router)
    app.include_router(agent_oauth_token_router)
    app.include_router(agents_router)
    app.include_router(oidc_router)
    app.include_router(saml_router)
    app.include_router(saml_admin_router)
    app.include_router(oidc_admin_router)
    app.include_router(domain_claims_router)
    app.include_router(provider_keys_admin_router)
    app.include_router(presets_admin_router)
    app.include_router(health_router)
    app.include_router(internal_router)
    app.include_router(internal_catalog_router)
    app.include_router(tenants_router)
    app.include_router(users_router)
    app.include_router(invites_router)
    app.include_router(invite_accept_router)
    app.include_router(domain_invite_links_router)
    app.include_router(domain_invite_redeem_router)
    app.include_router(access_requests_router)
    app.include_router(scim_token_router)
    app.include_router(scim_router)
    app.include_router(platform_tenants_router)
    app.include_router(platform_users_router)
    app.include_router(platform_tenant_config_router)
    app.include_router(platform_plans_router)
    app.include_router(platform_service_tier_router)
    app.include_router(service_tier_router)
    app.include_router(platform_impersonation_router)
    app.include_router(platform_audit_router)
    app.include_router(cache_router)
    app.include_router(capture_router)
    app.include_router(logs_query_router)
    app.include_router(batch_policy_router)
    app.include_router(billing_owner_router)
    app.include_router(guardrail_router)
    app.include_router(retention_policy_router)
    app.include_router(residency_policy_router)
    app.include_router(rate_card_router)
    app.include_router(region_pricing_router)
    app.include_router(catalog_router)
    app.include_router(keys_admin_router)
    app.include_router(key_guardrail_router)
    app.include_router(mcp_admin_router)
    app.include_router(mcp_key_router)
    app.include_router(mcp_proxy_router)
    app.include_router(keys_authz_router)
    app.include_router(platform_keys_router)
    app.include_router(teams_router)
    app.include_router(admin_models_router)
    app.include_router(admin_catalog_router)
    app.include_router(routing_admin_router)
    app.include_router(proxy_router)
    app.include_router(messages_router)
    app.include_router(responses_router)
    app.include_router(stored_responses_router)
    app.include_router(discovery_router)
    app.include_router(embeddings_router)
    app.include_router(images_router)
    app.include_router(moderations_router)
    app.include_router(audio_router)
    app.include_router(realtime_router)
    app.include_router(realtime_relay_router)
    app.include_router(usage_router)
    app.include_router(openai_usage_router)
    app.include_router(guardrail_analytics_router)
    app.include_router(audit_export_router)
    app.include_router(compliance_router)
    app.include_router(report_schedule_router)
    app.include_router(ops_router)
    app.include_router(budget_router)
    app.include_router(plan_router)
    app.include_router(credits_platform_router)
    app.include_router(credits_router)
    app.include_router(checkout_router)
    app.include_router(invoices_router)
    app.include_router(margin_router)
    app.include_router(conversations_router)
    app.include_router(memories_router)
    app.include_router(artifacts_router)
    app.include_router(files_router)
    app.include_router(video_router)
    app.include_router(batch_router)
    app.include_router(batch_stats_router)

    # RequestIdMiddleware must be added AFTER routers are included so it wraps
    # the full ASGI app and captures final status codes including those set by
    # FastAPI exception handlers (401/402/4xx from ProblemError).
    app.add_middleware(RequestIdMiddleware)

    # GlobalBackPressureMiddleware is registered AFTER RequestIdMiddleware so that
    # Starlette's reversed build order makes it the outermost USER middleware — the
    # guard runs first on every request before RequestIdMiddleware, auth, and routing.
    # (Starlette's ServerErrorMiddleware is technically the outermost layer; this is
    # outermost among application-registered middleware.)
    # Starlette add_middleware inserts at index 0; build_middleware_stack iterates
    # reversed(user_middleware), so the LAST add_middleware call = outermost wrapper.
    # When max_concurrent_requests=0 (default), the middleware is a pure pass-through
    # (byte-identical to today's behavior).
    app.add_middleware(
        GlobalBackPressureMiddleware,
        max_concurrent=settings.max_concurrent_requests,
        retry_after_s=settings.back_pressure_retry_after_seconds,
    )

    # BodySizeLimitMiddleware (edge-input-hardening TASK.md §3 Part C) is registered
    # AFTER GlobalBackPressureMiddleware so it becomes the new OUTERMOST layer — a
    # request too large to even want back-pressure accounting is rejected before that
    # accounting runs, and well before auth/routing/governance. Longest-prefix-match:
    # "/v1/audio/" gets the wider audio cap; "/v1/" and "/admin/" get the JSON cap;
    # anything unmatched falls back to the JSON cap (fail-closed, never unlimited).
    app.add_middleware(
        BodySizeLimitMiddleware,
        route_caps={
            # files-uploads-api PLAN.md §3: /v1/files uploads may be far larger than a JSON
            # body (files_max_bytes default 512 MiB), so a dedicated longest-prefix cap
            # overrides the wider "/v1/" JSON cap (20 MiB) — WITHOUT it, every upload in the
            # 20 MiB→files_max_bytes range was rejected 413 ERR_REQUEST_BODY_TOO_LARGE and
            # the contracted 413 ERR_FILE_TOO_LARGE could never fire (mirrors how
            # "/v1/audio/" already overrides "/v1/" for multipart STT). The cap carries a
            # small headroom over files_max_bytes so this coarse outer guard NEVER pre-empts
            # the router's precise per-file check at the exact boundary — the multipart body
            # (file part + purpose field + framing) is a few hundred bytes larger than the
            # raw file, so a raw file of exactly files_max_bytes must still pass the outer
            # guard and reach the router, which owns ERR_FILE_TOO_LARGE.
            "/v1/files": _files_route_cap(settings.files_max_bytes),
            "/v1/audio/": settings.max_audio_upload_bytes,
            "/v1/": settings.max_json_body_bytes,
            "/admin/": settings.max_json_body_bytes,
        },
        default_cap=settings.max_json_body_bytes,
    )

    # Expose the back-pressure middleware instance on app.state for tests/metrics.
    # The middleware stack is built lazily on first request; accessing it here forces
    # the build so app.state.back_pressure is available immediately after create_app().
    _mw_stack = app.middleware_stack  # triggers build_middleware_stack
    from starlette.types import ASGIApp as _ASGIApp  # local import avoids circular

    _cur: _ASGIApp = _mw_stack  # type: ignore[assignment]  # None guarded by if _cur is None: break
    for _ in range(20):
        if isinstance(_cur, GlobalBackPressureMiddleware):
            app.state.back_pressure = _cur
            break
        _cur = getattr(_cur, "_app", None) or getattr(_cur, "app", None)  # type: ignore[assignment]
        if _cur is None:
            break
    else:
        app.state.back_pressure = None  # middleware not found — unexpected stack-shape change

    return app
