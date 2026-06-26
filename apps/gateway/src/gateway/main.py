import asyncio
import contextlib
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

from gateway.alerting.application.dispatcher import AlertDispatcher
from gateway.alerting.application.health_checker import UpstreamHealthChecker
from gateway.alerting.infrastructure.httpx_pinger import HttpxUpstreamPinger
from gateway.alerting.infrastructure.httpx_webhook_sink import HttpxWebhookSink
from gateway.artifacts.api.router import artifacts_router
from gateway.artifacts.infrastructure.orm import (  # noqa: F401 — registers ArtifactRow on Base.metadata
    ArtifactRow as _ArtifactRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.auth.api.oidc_admin_router import oidc_admin_router
from gateway.auth.api.oidc_router import oidc_router
from gateway.auth.infrastructure.orm import (  # noqa: F401 — registers OidcProviderConfigRow on Base.metadata
    OidcProviderConfigRow as _OidcProviderConfigRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.budgets.api.router import budget_router
from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard
from gateway.catalog.api.router import (
    admin_catalog_router,
    admin_models_router,
    catalog_router,
    internal_catalog_router,
)
from gateway.catalog.infrastructure.openrouter_source import OpenRouterCatalogSource
from gateway.conversations.api.router import conversations_router
from gateway.conversations.infrastructure.orm import (  # noqa: F401 — registers ConversationRow/ConversationMessageRow on Base.metadata
    ConversationMessageRow as _ConversationMessageRow,  # pyright: ignore[reportUnusedImport]  — side-effect import
)
from gateway.conversations.infrastructure.orm import (
    ConversationRow as _ConversationRow,  # noqa: F401  # pyright: ignore[reportUnusedImport]  — side-effect import
)
from gateway.core.config import Settings
from gateway.core.errors import register_error_handlers
from gateway.keys.api.router import admin_router as keys_admin_router
from gateway.keys.api.router import authz_router as keys_authz_router
from gateway.memory.api.router import memories_router
from gateway.memory.infrastructure.orm import (  # noqa: F401 — registers MemoryRow on Base.metadata
    MemoryRow as _MemoryRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.observability.logging_config import configure_structlog
from gateway.observability.metrics import MetricsRegistry, expose_metrics
from gateway.observability.middleware import RequestIdMiddleware
from gateway.ops.api.router import ops_router
from gateway.proxy.api.audio_router import audio_router
from gateway.proxy.api.concurrency_guard import GlobalBackPressureMiddleware
from gateway.proxy.api.embeddings_router import embeddings_router
from gateway.proxy.api.images_router import images_router
from gateway.proxy.api.provider_keys_admin_router import provider_keys_admin_router
from gateway.proxy.api.realtime_ws import realtime_router
from gateway.proxy.api.router import proxy_router
from gateway.proxy.api.routing_admin_router import routing_admin_router
from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.routing_config_merge import merge_routing_config
from gateway.proxy.application.routing_strategy import build_strategy
from gateway.proxy.domain.ports import CompletionUpstream, UpstreamProvider
from gateway.proxy.infrastructure.anthropic_upstream import AnthropicCompletionUpstream
from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache
from gateway.proxy.infrastructure.azure_embeddings import AzureEmbeddingsProvider
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream
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
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream
from gateway.proxy.infrastructure.openrouter_upstream_provider import OpenRouterUpstreamFacade
from gateway.proxy.infrastructure.orm import (
    TenantProviderKeyRow as _TenantProviderKeyRow,  # noqa: F401 — registers TenantProviderKeyRow on Base.metadata  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.proxy.infrastructure.provider_aware_upstream import ProviderAwareCompletionUpstream
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry
from gateway.proxy.infrastructure.redis_cooldown_gate import RedisCooldownGate
from gateway.proxy.infrastructure.redis_limit_gate import RedisDeploymentLimitGate
from gateway.proxy.infrastructure.redis_load_gate import RedisDeploymentLoadGate
from gateway.proxy.infrastructure.routing_config_repository import RoutingConfigRepository
from gateway.proxy.infrastructure.tenant_provider_key_store import DbTenantProviderKeyStore
from gateway.rate_limits.application.passthrough import PassthroughBandwidthBucket
from gateway.rate_limits.infrastructure.redis_lua_limiter import RedisLuaRateLimiter
from gateway.rate_limits.infrastructure.redis_token_bucket import RedisTokenBucket
from gateway.teams.api.router import teams_router
from gateway.teams.infrastructure.orm import (  # noqa: F401 — registers TeamRow/TeamMemberRow on Base.metadata
    TeamMemberRow as _TeamMemberRow,  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.tenants.api.cache_router import cache_router
from gateway.tenants.api.guardrail_router import guardrail_router
from gateway.tenants.api.router import router as tenants_router
from gateway.tenants.api.users_router import users_router
from gateway.tenants.infrastructure.argon2_hasher import Argon2PasswordHasher
from gateway.tenants.infrastructure.jwt_service import JwtTokenService
from gateway.tenants.infrastructure.orm import (
    TenantRow as _TenantRow,  # noqa: F401 — ensures budget_usd_monthly column is in ORM metadata  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
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
    should_start_retention_sweep,
)
from gateway.usage.infrastructure.alert_events_orm import (
    AlertEventRow as _AlertEventRow,  # noqa: F401 — registers alert_events ORM metadata  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.usage.infrastructure.orm import (
    UsageRecordRow as _UsageRecordRow,  # noqa: F401 — registers ORM metadata  # pyright: ignore[reportUnusedImport]  — side-effect import; registers ORM table on Base.metadata
)
from gateway.video.api.router import video_router
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
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Composition root: wires infrastructure adapters into domain ports."""
    settings = settings if settings is not None else Settings()

    # Configure structlog once per process (idempotent).  Must run before any
    # logger call — including those triggered during module imports below.
    configure_structlog()

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

        # RetentionSweeper — periodic bounded DELETE of aged time-series rows
        # (data-retention-controls v38). Default-ON at configured defaults; started only
        # when interval>0 AND at least one per-table window>0. Wired after recovery sweep.
        app.state.retention_sweeper_task = None
        if should_start_retention_sweep(_settings):
            retention_sweeper = RetentionSweeper(
                session_factory=_sessionmaker,
                settings=_settings,
            )
            app.state.retention_sweeper = retention_sweeper
            app.state.retention_sweeper_task = asyncio.create_task(
                retention_sweeper.run_forever(
                    interval_seconds=float(_settings.retention_check_interval_seconds)
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

        retention_task: asyncio.Task[None] | None = getattr(
            app.state, "retention_sweeper_task", None
        )
        if retention_task is not None:
            retention_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retention_task

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
    app.state.retention_sweeper_task = None

    # Video generation seam — default: no provider (honest degradation).
    # Tests override via app.state.video_generator = <stub>.
    app.state.video_generator = None
    # Tracked in-process asyncio.Task set for video jobs.
    # The lifespan cancels any outstanding tasks on shutdown.
    app.state.video_jobs_tasks: set[asyncio.Task[None]] = set()

    engine = create_async_engine(settings.database_url)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    app.state.password_hasher = Argon2PasswordHasher()
    app.state.token_service = JwtTokenService(settings)
    # Default catalog source — tests override via app.state.catalog_source
    app.state.catalog_source = OpenRouterCatalogSource(httpx.AsyncClient())
    # Proxy defaults — tests inject fakes via app.state
    app.state.circuit_breaker = CircuitBreaker()
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

    async def _load_provider_map() -> dict[str, str]:
        async with app.state.sessionmaker() as _session:
            _rows = (await _session.execute(_sa_select(_ModelRow.id, _ModelRow.provider))).all()
            return {row.id: row.provider for row in _rows}

    app.state.provider_resolver = CatalogProviderResolver(loader=_load_provider_map)

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

    # Per-tenant Azure AD token provider cache — one shared instance on app.state,
    # injected into both Azure adapters (chat + embeddings). Keyed by the NON-SECRET
    # AzureADConfig identity (tenant_id, client_id, authority, scope).
    _azure_ad_token_provider_cache = AzureADTokenProviderCache(
        ttl_s=settings.azure_ad_provider_cache_ttl_s,
        max_size=settings.azure_ad_provider_cache_max,
        metrics_registry=app.state.metrics_registry,
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

    # Budget guard: wire RedisBudgetGuard for production;
    # tests override via app.state.budget_guard after app creation.
    app.state.budget_guard = RedisBudgetGuard(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )

    # Rate limiter: wire RedisLuaRateLimiter for production;
    # tests override via app.state.rate_limiter after app creation.
    app.state.rate_limiter = RedisLuaRateLimiter(redis=redis_client)

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

    register_error_handlers(app)
    app.include_router(oidc_router)
    app.include_router(oidc_admin_router)
    app.include_router(provider_keys_admin_router)
    app.include_router(health_router)
    app.include_router(internal_router)
    app.include_router(internal_catalog_router)
    app.include_router(tenants_router)
    app.include_router(users_router)
    app.include_router(cache_router)
    app.include_router(guardrail_router)
    app.include_router(catalog_router)
    app.include_router(keys_admin_router)
    app.include_router(keys_authz_router)
    app.include_router(teams_router)
    app.include_router(admin_models_router)
    app.include_router(admin_catalog_router)
    app.include_router(routing_admin_router)
    app.include_router(proxy_router)
    app.include_router(embeddings_router)
    app.include_router(images_router)
    app.include_router(audio_router)
    app.include_router(realtime_router)
    app.include_router(usage_router)
    app.include_router(ops_router)
    app.include_router(budget_router)
    app.include_router(conversations_router)
    app.include_router(memories_router)
    app.include_router(artifacts_router)
    app.include_router(video_router)

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
    # Expose the back-pressure middleware instance on app.state for tests/metrics.
    # The middleware stack is built lazily on first request; accessing it here forces
    # the build so app.state.back_pressure is available immediately after create_app().
    _mw_stack = app.middleware_stack  # triggers build_middleware_stack
    from starlette.types import ASGIApp as _ASGIApp  # local import avoids circular

    _cur: _ASGIApp = _mw_stack
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
