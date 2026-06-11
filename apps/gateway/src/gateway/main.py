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

from gateway.budgets.api.router import budget_router
from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard
from gateway.catalog.api.router import catalog_router, internal_catalog_router
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

        Shutdown ordering:
          1. Cancel flusher background task
          2. drain_until_empty(timeout=shutdown_drain_timeout_seconds)
          3. await redis_client.aclose()
          4. await engine.dispose()
          5. await httpx_client.aclose() if held
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

        yield  # ── application is running ──

        # ── Shutdown ─────────────────────────────────────────────────────
        # 1. Cancel the run_forever background task
        task: asyncio.Task[None] | None = getattr(app.state, "flusher_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # 2. Drain remaining PEL entries up to the timeout
        await flusher.drain_until_empty(timeout=float(_settings.shutdown_drain_timeout_seconds))

        # 3. Close Redis client
        with contextlib.suppress(Exception):
            await _redis.aclose()

        # 4. Dispose SQLAlchemy engine
        with contextlib.suppress(Exception):
            await _engine.dispose()

        # 5. Close httpx client if stored on app.state
        httpx_client: httpx.AsyncClient | None = getattr(app.state, "httpx_client", None)
        if httpx_client is not None:
            with contextlib.suppress(Exception):
                await httpx_client.aclose()

    app = FastAPI(title="AI Proxy Gateway", version="0.1.0", lifespan=lifespan)

    # Per-app Prometheus registry — prevents Duplicated timeseries errors when
    # multiple create_app() calls exist in a single pytest run.
    app.state.metrics_registry = MetricsRegistry(registry=CollectorRegistry())

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

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(internal_router)
    app.include_router(internal_catalog_router)
    app.include_router(tenants_router)
    app.include_router(catalog_router)
    app.include_router(keys_admin_router)
    app.include_router(keys_authz_router)
    app.include_router(proxy_router)
    app.include_router(usage_router)
    app.include_router(budget_router)

    # RequestIdMiddleware must be added AFTER routers are included so it wraps
    # the full ASGI app and captures final status codes including those set by
    # FastAPI exception handlers (401/402/4xx from ProblemError).
    app.add_middleware(RequestIdMiddleware)

    return app
