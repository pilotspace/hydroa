"""Public RFC 8628 device-authorization endpoint.

POST /oauth/device/authorize  — NO auth required (RFC 8628 §3.1: the agent has
no credential yet; acquiring one is the whole point of the device flow).

Design for failure (CLAUDE.md IO rule):
- Per-IP fixed-window rate limit (fail-OPEN on Redis outage): rejects DoS before any DB IO.
- Single bounded DB INSERT per request; a transient CSPRNG hash collision → 503 + Retry-After
  (retryable), never a 500. The session is rolled back by the repository on collision.
- plaintext device_code / user_code appear ONCE in the 200 response body and are NEVER logged.
- Server-owned knobs (ttl, interval, now) are never client-settable.

Response shape (§3 CONTRACT, RFC 8628 §3.2):
  200 → { device_code, user_code, verification_uri, expires_in, interval,
           verification_uri_complete? (only when verification_uri != "") }
  422 → { "error": "invalid_request" }           — malformed / oversized body
  429 → { "error": "rate_limited" }              — per-IP limit + Retry-After
  503 → { "error": "temporarily_unavailable" }   — collision + Retry-After
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from gateway.agent_oauth.application.use_cases import AgentOAuthService
from gateway.agent_oauth.domain.errors import DeviceCodeCollisionError
from gateway.agent_oauth.infrastructure.ip_rate_limiter import (
    AgentOAuthIpRateLimiter,
    RateLimitedError,
)
from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

logger = structlog.get_logger(__name__)

agent_oauth_device_router = APIRouter(prefix="/oauth/device", tags=["agent-oauth"])

# The only valid body is a tiny optional {"scope": "..."} object. Cap the read so a
# public, pre-auth endpoint cannot be flooded with an unbounded payload (DoS surface).
_MAX_BODY_BYTES = 4096


class _DeviceAuthorizeBody(BaseModel):
    """Optional request body for POST /oauth/device/authorize.

    ``extra="ignore"`` so unknown client-supplied fields (e.g. ``expires_in``,
    ``interval``) are silently dropped — the client cannot influence server knobs.
    ``scope`` is optional; when absent the server default from Settings is used.
    """

    model_config = ConfigDict(extra="ignore")

    scope: str | None = None


def _resolve_client_ip(request: Request) -> str:
    """Return the left-most IP from X-Forwarded-For, falling back to request.client.host.

    Behind Envoy the trusted client IP is the left-most token in X-Forwarded-For.
    In direct/test calls (no proxy) ``request.client.host`` is used.
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


@agent_oauth_device_router.post("/authorize")
async def device_authorize(request: Request) -> JSONResponse:
    """POST /oauth/device/authorize — PUBLIC, no auth dependency.

    Mints a device_code + user_code (via task-1 AgentOAuthService), persists the
    pending authorization, and returns the RFC 8628 §3.2 payload.
    """
    settings: Any = request.app.state.settings

    # ── 1. Per-IP rate limit FIRST (header-only, cheap; fail-open on Redis error) ──
    # Gate before reading the body so a flood is rejected before any unbounded work.
    client_ip = _resolve_client_ip(request)
    limiter: AgentOAuthIpRateLimiter = request.app.state.agent_oauth_ip_limiter
    try:
        await limiter.check(ip=client_ip, limit=settings.agent_oauth_authorize_rpm)
    except RateLimitedError as exc:
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited"},
            headers={"Retry-After": str(exc.retry_after)},
        )

    # ── 2. Read + parse body, BOUNDED (reject oversized/malformed → 422) ──────
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > _MAX_BODY_BYTES:
                return JSONResponse(status_code=422, content={"error": "invalid_request"})
        except ValueError:
            return JSONResponse(status_code=422, content={"error": "invalid_request"})
    body: _DeviceAuthorizeBody
    try:
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:  # guards a missing/lying Content-Length
            return JSONResponse(status_code=422, content={"error": "invalid_request"})
        if raw:
            body = _DeviceAuthorizeBody.model_validate_json(raw)
        else:
            body = _DeviceAuthorizeBody()
    except (ValidationError, ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=422, content={"error": "invalid_request"})

    # ── 3. Mint device codes via task-1 service ────────────────────────────
    scope = body.scope or settings.agent_oauth_default_scope
    ttl: int = settings.agent_oauth_device_code_ttl_seconds
    interval: int = settings.agent_oauth_poll_interval_seconds
    now: datetime = datetime.now(UTC)

    async with request.app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        service = AgentOAuthService(repo=repo, hasher=Sha256SecretHasher())
        try:
            minted = await service.start_device_authorization(
                scope=scope,
                interval_seconds=interval,
                ttl_seconds=ttl,
                now=now,
            )
        except DeviceCodeCollisionError:
            # Transient CSPRNG hash collision — astronomically rare; always retryable.
            # The repository already rolled back the session on IntegrityError.
            logger.warning("device_code_collision_retry")
            return JSONResponse(
                status_code=503,
                content={"error": "temporarily_unavailable"},
                headers={"Retry-After": str(interval)},
            )

    # ── 4. Build RFC 8628 §3.2 response (plaintext codes returned ONCE) ───
    # SECURITY: device_code and user_code are NEVER logged — only emitted in the body.
    verification_uri: str = settings.agent_oauth_verification_uri

    response_body: dict[str, object] = {
        "device_code": minted.device_code,
        "user_code": minted.user_code,
        "verification_uri": verification_uri,
        "expires_in": ttl,
        "interval": interval,
    }
    if verification_uri:
        response_body["verification_uri_complete"] = (
            f"{verification_uri}?user_code={minted.user_code}"
        )

    return JSONResponse(status_code=200, content=response_body)
