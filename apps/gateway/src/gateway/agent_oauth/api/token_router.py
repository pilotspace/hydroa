"""Public RFC 8628 token endpoint — POST /oauth/token (device_code grant).

The agent polls with its device_code; the gateway maps the authorization state to the
RFC 8628 §3.5 polling response, and once approved, mints + returns the agent access token
(single-use device_code) so the agent can call the data plane.

Design for failure (CLAUDE.md IO rule):
- Per-IP fixed-window rate limit FIRST (fail-OPEN on Redis outage): rejects DoS before DB IO.
- Bounded body read (4096 bytes) before any processing; oversized/malformed → 422.
- slow_down (RFC 8628 §3.5): Redis SET NX EX=interval per device_code_hash; fail-OPEN.
- One bounded SELECT (device_code_hash lookup) + (on approved) one atomic mint tx per request.
- Mint race (AlreadyConsumed / NotApproved) → 400 invalid_grant, NEVER a 500.
- Plaintext access/refresh tokens are returned ONCE in the 200 body and NEVER logged.
- ttl / refresh lifetime / now are all server-owned knobs; client cannot influence them.

Response shape (§3 CONTRACT, RFC 8628 §3.5):
  200 → { access_token, token_type:"Bearer", expires_in, scope, refresh_token? }
  400 → { "error": "<rfc8628-code>" }
  422 → { "error": "invalid_request" }    — malformed / oversized body
  429 → { "error": "rate_limited" }       — per-IP poll window exceeded  + Retry-After
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from redis.exceptions import RedisError

from gateway.agent_oauth.application.use_cases import AgentOAuthService
from gateway.agent_oauth.domain.errors import (
    AuthorizationAlreadyConsumedError,
    AuthorizationNotApprovedError,
)
from gateway.agent_oauth.infrastructure.ip_rate_limiter import (
    AgentOAuthIpRateLimiter,
    RateLimitedError,
)
from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
from gateway.core.net import resolve_trusted_client_ip
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

logger = logging.getLogger(__name__)

agent_oauth_token_router = APIRouter(prefix="/oauth", tags=["agent-oauth"])

# RFC 8628 §3.4 grant type URN — the only accepted grant_type value.
_DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# Public pre-auth endpoint: cap body to prevent DoS via unbounded payload.
_MAX_BODY_BYTES = 4096


class _TokenRequestBody(BaseModel):
    """Request body for POST /oauth/token (device_code grant).

    ``extra="forbid"`` would reject unknown fields; we use ``extra="ignore"`` to match
    the device-authorize router's style and silently drop any extra client-supplied fields
    (e.g. client_id, client_secret) that a strict RFC 8628 client may include.
    """

    model_config = ConfigDict(extra="ignore")

    grant_type: str
    device_code: str | None = None


async def _probe_slow_down(redis: Any, device_code_hash: str, interval_seconds: int) -> str:
    """RFC 8628 §3.5 slow_down probe for a still-pending poll.

    Sets ``agent_oauth:poll:{hash}`` with SET NX EX=interval. A truthy result means the
    key was fresh (this poll is in-cadence) → "fresh"; a falsy result means the key already
    existed (the client polled faster than ``interval``) → "too_fast". Fail-OPEN: any Redis
    error returns "fresh" so a Redis outage never blocks a legitimate poll.
    """
    poll_key = f"agent_oauth:poll:{device_code_hash}"
    try:
        key_was_new = await redis.set(poll_key, "1", nx=True, ex=interval_seconds)
    except (RedisError, OSError):
        logger.warning(
            "agent_oauth_slow_down_redis_error",
            extra={"device_code_hash_prefix": device_code_hash[:8]},
            exc_info=True,
        )
        return "fresh"
    return "fresh" if key_was_new else "too_fast"


@agent_oauth_token_router.post("/token")
async def token_endpoint(request: Request) -> JSONResponse:
    """POST /oauth/token — PUBLIC, no auth required.

    Implements the RFC 8628 §3.5 polling state machine:
      pending (not expired) → authorization_pending (+ slow_down on fast repoll)
      pending (expired)     → expired_token
      denied                → access_denied
      consumed              → invalid_grant
      unknown               → invalid_grant
      approved              → MINT → 200 token response
    """
    settings: Any = request.app.state.settings

    # ── 1. Bounded body read (reject oversized/malformed → 422) ─────────────────
    # Read the body BEFORE the rate limiter so we can detect oversized bodies even
    # on rate-limited requests. The rate limiter is cheap (Redis INCR, header-only)
    # and runs immediately after — no unbounded work is done before the guard.
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > _MAX_BODY_BYTES:
                return JSONResponse(status_code=422, content={"error": "invalid_request"})
        except ValueError:
            return JSONResponse(status_code=422, content={"error": "invalid_request"})

    try:
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:
            return JSONResponse(status_code=422, content={"error": "invalid_request"})
        body = _TokenRequestBody.model_validate_json(raw)
    except (ValidationError, ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=422, content={"error": "invalid_request"})

    # ── 2. Per-IP rate limit (fail-OPEN on Redis error) ──────────────────────────
    client_ip = resolve_trusted_client_ip(request, settings.trusted_proxy_hops)
    limiter: AgentOAuthIpRateLimiter = request.app.state.agent_oauth_ip_limiter
    # Namespace the token-poll counter so it does NOT share the per-IP budget with the
    # /oauth/device/authorize endpoint (which keys on the bare IP) — otherwise authorize
    # traffic would bleed into the poll budget for the same IP. Mirrors the approve router.
    rate_key = f"token:{client_ip}"
    try:
        await limiter.check(ip=rate_key, limit=settings.agent_oauth_token_rpm)
    except RateLimitedError as exc:
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited"},
            headers={"Retry-After": str(exc.retry_after)},
        )

    # ── 3. grant_type validation ──────────────────────────────────────────────────
    if body.grant_type != _DEVICE_CODE_GRANT:
        return JSONResponse(status_code=400, content={"error": "unsupported_grant_type"})

    # ── 4. device_code presence check ────────────────────────────────────────────
    if not body.device_code:
        return JSONResponse(status_code=400, content={"error": "invalid_request"})

    # ── 5. Hash the submitted device_code for lookup ─────────────────────────────
    hasher = Sha256SecretHasher()
    device_code_hash = hasher.hash(body.device_code)

    # ── 6. DB lookup + state-machine + optional mint (inside session context) ────
    redis = request.app.state.redis_client
    access_ttl: int = settings.agent_oauth_access_token_ttl_seconds
    refresh_ttl_setting: int = settings.agent_oauth_refresh_token_ttl_seconds
    # Pass None when refresh is disabled (0) so the service skips token generation.
    refresh_ttl: int | None = refresh_ttl_setting if refresh_ttl_setting > 0 else None
    now = datetime.now(UTC)

    # IO-vs-decision separation (task-3 lesson): inside the session context do ONLY the
    # awaits (lookup · optional slow_down probe · optional mint) and capture raw results;
    # the pure state-machine branching + response building happen OUTSIDE the greenlet
    # context below — so coverage instruments them and the session closes cleanly first.
    minted: Any = None
    mint_raced = False
    slow_down_probe: str | None = None  # "fresh" | "too_fast" | "redis_down" (pending only)

    async with request.app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        auth = await repo.get_by_device_code_hash(device_code_hash)
        auth_status = auth.status if auth is not None else None

        if auth is not None and auth_status == "pending" and now < auth.expires_at:
            slow_down_probe = await _probe_slow_down(redis, device_code_hash, auth.interval_seconds)
        elif auth is not None and auth_status == "approved":
            service = AgentOAuthService(repo=repo, hasher=hasher)
            try:
                minted = await service.mint(
                    authorization_id=auth.id,
                    access_ttl_seconds=access_ttl,
                    refresh_ttl_seconds=refresh_ttl,
                    now=now,
                )
            except (AuthorizationNotApprovedError, AuthorizationAlreadyConsumedError):
                mint_raced = True  # concurrent mint race → invalid_grant, never 500

    # ── 7. Pure state machine + response (OUTSIDE the session) — RFC 8628 §3.5 ───
    if auth is None or auth_status == "consumed" or mint_raced:
        # unknown device_code, already-minted (second poll), or a mint race
        return JSONResponse(status_code=400, content={"error": "invalid_grant"})
    if auth_status == "denied":
        return JSONResponse(status_code=400, content={"error": "access_denied"})
    if auth_status == "pending" and now >= auth.expires_at:
        return JSONResponse(status_code=400, content={"error": "expired_token"})
    if auth_status == "pending":
        error = "slow_down" if slow_down_probe == "too_fast" else "authorization_pending"
        return JSONResponse(status_code=400, content={"error": error})
    if minted is not None:
        # SECURITY: plaintext tokens are emitted ONCE here and NEVER logged.
        response_body: dict[str, Any] = {
            "access_token": minted.access_token,
            "token_type": "Bearer",
            "expires_in": access_ttl,
            "scope": minted.token.scope,
        }
        if minted.refresh_token is not None:
            response_body["refresh_token"] = minted.refresh_token
        return JSONResponse(status_code=200, content=response_body)
    # Defensive: unexpected status value → invalid_grant
    return JSONResponse(status_code=400, content={"error": "invalid_grant"})  # pragma: no cover
