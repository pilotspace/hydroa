import asyncio
import contextlib
import re
from collections.abc import AsyncIterator

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CollectorRegistry
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gateway.alerting.application.dispatcher import AlertDispatcher
from gateway.alerting.application.health_checker import UpstreamHealthChecker
from gateway.alerting.infrastructure.httpx_pinger import HttpxUpstreamPinger
from gateway.alerting.infrastructure.httpx_webhook_sink import HttpxWebhookSink
from gateway.auth.api.oidc_admin_router import oidc_admin_router
from gateway.auth.api.oidc_router import oidc_router
from gateway.auth.infrastructure.orm import (  # noqa: F401 — registers OidcProviderConfigRow on Base.metadata
    OidcProviderConfigRow as _OidcProviderConfigRow,
)
from gateway.budgets.api.router import budget_router
from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard
from gateway.catalog.api.router import admin_models_router, catalog_router, internal_catalog_router
from gateway.catalog.infrastructure.openrouter_source import OpenRouterCatalogSource
from gateway.core.config import Settings
from gateway.core.errors import register_error_handlers
from gateway.keys.api.router import admin_router as keys_admin_router
from gateway.keys.api.router import authz_router as keys_authz_router
from gateway.observability.logging_config import configure_structlog
from gateway.observability.metrics import MetricsRegistry, expose_metrics
from gateway.observability.middleware import RequestIdMiddleware
from gateway.proxy.api.router import proxy_router
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream
from gateway.rate_limits.infrastructure.redis_lua_limiter import RedisLuaRateLimiter
from gateway.teams.api.router import teams_router
from gateway.teams.infrastructure.orm import (  # noqa: F401 — registers TeamRow/TeamMemberRow on Base.metadata
    TeamMemberRow as _TeamMemberRow,
)
from gateway.tenants.api.cache_router import cache_router
from gateway.tenants.api.guardrail_router import guardrail_router
from gateway.tenants.api.router import router as tenants_router
from gateway.tenants.infrastructure.argon2_hasher import Argon2PasswordHasher
from gateway.tenants.infrastructure.jwt_service import JwtTokenService
from gateway.tenants.infrastructure.orm import (
    TenantRow as _TenantRow,  # noqa: F401 — ensures budget_usd_monthly column is in ORM metadata
)
from gateway.usage.api.router import usage_router
from gateway.usage.application.flusher import UsageLedgerFlusher
from gateway.usage.application.recorder import RecordingUsageRecorder
from gateway.usage.infrastructure.alert_events_orm import (
    AlertEventRow as _AlertEventRow,  # noqa: F401 — registers alert_events ORM metadata
)
from gateway.usage.infrastructure.orm import (
    UsageRecordRow as _UsageRecordRow,  # noqa: F401 — registers ORM metadata
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


def create_app(settings: Settings | None = None) -> FastAPI:
    """Composition root: wires infrastructure adapters into domain ports."""
    settings = settings if settings is not None else Settings()

    # Configure structlog once per process (idempotent).  Must run before any
    # logger call — including those triggered during module imports below.
    configure_structlog()

    @contextlib.asynccontextmanager
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

        # 2. Final run_once()/check_once() drain cycle for dispatcher/health
        with contextlib.suppress(Exception):
            await dispatcher.run_once()
        if _settings.health_check_interval_seconds > 0:
            hc = getattr(app.state, "health_checker", None)
            if hc is not None:
                with contextlib.suppress(Exception):
                    await hc.check_once()

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
    app.state.completion_upstream = OpenRouterCompletionUpstream(
        api_key=settings.openrouter_api_key
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

    # Cache TTL — exposed on app.state so proxy router can read it per-request
    app.state.cache_ttl_seconds = settings.cache_ttl_seconds

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
    app.include_router(health_router)
    app.include_router(internal_router)
    app.include_router(internal_catalog_router)
    app.include_router(tenants_router)
    app.include_router(cache_router)
    app.include_router(guardrail_router)
    app.include_router(catalog_router)
    app.include_router(keys_admin_router)
    app.include_router(keys_authz_router)
    app.include_router(teams_router)
    app.include_router(admin_models_router)
    app.include_router(proxy_router)
    app.include_router(usage_router)
    app.include_router(budget_router)

    # RequestIdMiddleware must be added AFTER routers are included so it wraps
    # the full ASGI app and captures final status codes including those set by
    # FastAPI exception handlers (401/402/4xx from ProblemError).
    app.add_middleware(RequestIdMiddleware)

    return app
