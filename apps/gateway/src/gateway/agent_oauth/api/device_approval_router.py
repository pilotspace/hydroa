"""Authenticated device-approval endpoint — POST /oauth/device/approve + /deny.

Only a verified human session JWT may approve or deny a pending device authorization.
Binding (tenant_id, user_id) is ALWAYS taken from the verified JWT Identity — NEVER from
the request body. This makes cross-tenant grant architecturally impossible.

Design for failure (CLAUDE.md IO rule):
- Bounded body read (4096 bytes max) before any DB IO.
- Per-USER rate limit (fail-OPEN on Redis error): applied after identity resolution,
  before the DB lookup.  Keys off the user_id string so enumeration by an authed actor
  is bounded.
- One bounded DB UPDATE (approve / deny) inside a row-level WITH FOR UPDATE lock in the
  repository — no unbounded writes.
- plaintext user_code is NEVER logged; only the normalized form is used internally and
  only its SHA-256 hash is stored/compared.

Response shape (§3 CONTRACT, device-approval-flow TASK.md):
  POST /oauth/device/approve  →  200 { "status": "approved" }
  POST /oauth/device/deny     →  200 { "status": "denied" }
  401 → { "error": "unauthorized" }              # missing / invalid session JWT
  422 → { "error": "invalid_request" }           # missing user_code / malformed / oversized
  404 → { "error": "authorization_not_found" }   # user_code matches no row
  409 → { "error": "authorization_not_pending" } # already approved / denied / consumed
  410 → { "error": "authorization_expired" }     # pending but past expiry
  429 → { "error": "rate_limited" } + Retry-After # per-user window exceeded
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from gateway.agent_oauth.domain.errors import (
    AuthorizationExpiredError,
    AuthorizationNotPendingError,
)
from gateway.agent_oauth.infrastructure.ip_rate_limiter import (
    AgentOAuthIpRateLimiter,
    RateLimitedError,
)
from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
from gateway.core.error_catalog import AUTH_TOKEN_INVALID
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.tenants.api.deps import get_bearer_token
from gateway.tenants.application.use_cases import GetIdentityUseCase
from gateway.tenants.domain.entities import Identity

agent_oauth_approval_router = APIRouter(prefix="/oauth/device", tags=["agent-oauth"])

# Cap the bounded body read — mirrors device_authorize_router._MAX_BODY_BYTES.
_MAX_BODY_BYTES = 4096

# Alphabet used by task-1 user_code generator: "BCDFGHJKLMNPQRSTVWXZ"
# After normalization the expected format is "XXXX-XXXX" (8 code chars + dash).
_USER_CODE_CHARS = 8

# Contract wire strings — must match §3 CONTRACT exactly.
_ERR_INVALID_REQUEST = "invalid_request"
_ERR_NOT_FOUND = "authorization_not_found"
_ERR_NOT_PENDING = "authorization_not_pending"
_ERR_EXPIRED = "authorization_expired"
_ERR_RATE_LIMITED = "rate_limited"


class _ApprovalBody(BaseModel):
    """Bounded request body: only ``user_code`` is accepted.

    ``extra="ignore"`` so stray fields (e.g. an attacker injecting ``tenant_id`` or
    ``user_id``) are silently dropped.  The binding is always resolved from the JWT.
    """

    model_config = ConfigDict(extra="ignore")

    user_code: str


def _normalize_user_code(raw: str) -> str:
    """Strip whitespace, uppercase, re-insert the XXXX-XXXX dash.

    Humans type the code loosely (lowercase, spaces, no dash).  This helper produces
    the canonical form so sha256(normalized) matches sha256(user_code) as generated
    by task-1 (alphabet "BCDFGHJKLMNPQRSTVWXZ", format "XXXX-XXXX").

    Algorithm:
    1. Strip leading/trailing + internal whitespace (join split tokens).
    2. Uppercase.
    3. Remove any existing dash.
    4. If the result is exactly 8 chars (the expected bare length), re-insert a dash at
       position 4 → "XXXX-XXXX".
    5. Otherwise return as-is; an unknown length will hash to a non-matching value and
       the lookup will return None → 404 cleanly.
    """
    # 1+2: collapse internal whitespace and uppercase
    normalized = "".join(raw.split()).upper()
    # 3: remove any existing dash
    normalized = normalized.replace("-", "")
    # 4: re-insert dash if exactly 8 chars
    if len(normalized) == _USER_CODE_CHARS:
        normalized = normalized[:4] + "-" + normalized[4:]
    return normalized


def _require_identity(request: Request) -> Identity:
    """Resolve the caller's Identity from the verified JWT.

    Mirrors ``provider_keys_admin_router._require_owner_identity`` but WITHOUT the
    role restriction — any authenticated role (member / admin / owner) may approve.

    Raises:
        ProblemError 401: missing token (AUTH_TOKEN_MISSING) or invalid/expired token
            (AUTH_TOKEN_INVALID).
    """
    token = get_bearer_token(request)  # raises AUTH_TOKEN_MISSING (401) when absent
    use_case = GetIdentityUseCase(request.app.state.token_service)
    try:
        return use_case.execute(token)
    except Exception as exc:
        raise AUTH_TOKEN_INVALID.exc() from exc


async def _parse_approval_body(request: Request) -> _ApprovalBody | JSONResponse:
    """Read and parse the bounded JSON body.

    Returns the parsed ``_ApprovalBody`` on success, or a 422 ``JSONResponse`` on any
    validation failure (oversized, malformed JSON, missing / non-string ``user_code``).
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > _MAX_BODY_BYTES:
                return JSONResponse(status_code=422, content={"error": _ERR_INVALID_REQUEST})
        except ValueError:
            return JSONResponse(  # pragma: no cover — httpx always sends valid Content-Length
                status_code=422, content={"error": _ERR_INVALID_REQUEST}
            )

    try:
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:  # pragma: no cover — Content-Length guard fires first
            return JSONResponse(status_code=422, content={"error": _ERR_INVALID_REQUEST})
        body = _ApprovalBody.model_validate_json(raw)
    except (ValidationError, ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=422, content={"error": _ERR_INVALID_REQUEST})

    return body


async def _run_transition(
    request: Request, *, user_code_hash: str, action: str, identity: Identity
) -> str:
    """Run the lookup + approve/deny inside a request-scoped session.

    Returns a small OUTCOME string ("ok" | "not_found" | "not_pending" | "expired") so the
    HTTP response is decided OUTSIDE the asyncpg/greenlet session context (_outcome_to_response).
    """
    async with request.app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        authorization = await repo.get_by_user_code_hash(user_code_hash)
        if authorization is None:
            return "not_found"
        try:
            if action == "approve":
                await repo.approve(
                    authorization_id=authorization.id,
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    now=datetime.now(UTC),
                )
            elif action == "deny":
                await repo.deny(authorization_id=authorization.id)
            else:  # pragma: no cover — only the two literal callers above exist
                raise ValueError(f"unknown action: {action}")
        except AuthorizationNotPendingError:
            return "not_pending"
        except AuthorizationExpiredError:
            return "expired"
        return "ok"


def _outcome_to_response(outcome: str, *, success_status: str) -> JSONResponse:
    """Map a transition outcome to its §3 CONTRACT HTTP response (pure, no IO)."""
    if outcome == "not_found":
        return JSONResponse(status_code=404, content={"error": _ERR_NOT_FOUND})
    if outcome == "not_pending":
        return JSONResponse(status_code=409, content={"error": _ERR_NOT_PENDING})
    if outcome == "expired":
        return JSONResponse(status_code=410, content={"error": _ERR_EXPIRED})
    return JSONResponse(status_code=200, content={"status": success_status})


@agent_oauth_approval_router.post("/approve")
async def device_approve(request: Request) -> JSONResponse:
    """POST /oauth/device/approve — AUTHENTICATED.

    Resolves the caller's Identity from the JWT, normalizes and hashes the user_code,
    looks up the pending authorization, and transitions it to 'approved' bound to the
    caller's (tenant_id, user_id).
    """
    settings: Any = request.app.state.settings

    # ── 1. Identity resolution (JWT required) ────────────────────────────────
    # ProblemError 401 is raised and handled by the registered error handler.
    identity = _require_identity(request)

    # ── 2. Per-USER rate limit (fail-open on Redis error) ────────────────────
    limiter: AgentOAuthIpRateLimiter = request.app.state.agent_oauth_ip_limiter
    rate_key = f"approve:{identity.user_id}"
    try:
        await limiter.check(ip=rate_key, limit=settings.agent_oauth_approve_rpm)
    except RateLimitedError as exc:
        return JSONResponse(
            status_code=429,
            content={"error": _ERR_RATE_LIMITED},
            headers={"Retry-After": str(exc.retry_after)},
        )

    # ── 3. Bounded body parse ─────────────────────────────────────────────────
    result = await _parse_approval_body(request)
    if isinstance(result, JSONResponse):
        return result
    body = result

    # ── 4. Normalize + hash the user_code (NEVER log the plaintext) ──────────
    normalized = _normalize_user_code(body.user_code)
    user_code_hash = Sha256SecretHasher().hash(normalized)

    # ── 5. Lookup + approve. Only the awaits live inside the session context; the
    # response decision is made OUTSIDE it (presentation out of the IO boundary).
    outcome = await _run_transition(
        request, user_code_hash=user_code_hash, action="approve", identity=identity
    )
    return _outcome_to_response(outcome, success_status="approved")


@agent_oauth_approval_router.post("/deny")
async def device_deny(request: Request) -> JSONResponse:
    """POST /oauth/device/deny — AUTHENTICATED.

    Resolves the caller's Identity from the JWT, normalizes and hashes the user_code,
    looks up the pending authorization, and transitions it to 'denied'.
    """
    settings: Any = request.app.state.settings

    # ── 1. Identity resolution (JWT required) ────────────────────────────────
    identity = _require_identity(request)

    # ── 2. Per-USER rate limit (fail-open on Redis error) ────────────────────
    limiter: AgentOAuthIpRateLimiter = request.app.state.agent_oauth_ip_limiter
    rate_key = f"approve:{identity.user_id}"
    try:
        await limiter.check(ip=rate_key, limit=settings.agent_oauth_approve_rpm)
    except RateLimitedError as exc:
        return JSONResponse(
            status_code=429,
            content={"error": _ERR_RATE_LIMITED},
            headers={"Retry-After": str(exc.retry_after)},
        )

    # ── 3. Bounded body parse ─────────────────────────────────────────────────
    result = await _parse_approval_body(request)
    if isinstance(result, JSONResponse):
        return result
    body = result

    # ── 4. Normalize + hash the user_code (NEVER log the plaintext) ──────────
    normalized = _normalize_user_code(body.user_code)
    user_code_hash = Sha256SecretHasher().hash(normalized)

    # ── 5. Lookup + deny. Awaits inside the session context; response decided outside.
    outcome = await _run_transition(
        request, user_code_hash=user_code_hash, action="deny", identity=identity
    )
    return _outcome_to_response(outcome, success_status="denied")
