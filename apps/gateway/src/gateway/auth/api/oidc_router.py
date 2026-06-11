"""OIDC gateway routes — public (no auth deps).

Routes:
  GET /auth/oidc/login      — initiate OIDC authorization-code flow
  GET /auth/oidc/callback   — handle IdP redirect, mint session cookie

Cookie posture:
  oidc_state / oidc_nonce: HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300
    (Lax required so the IdP redirect from a cross-site origin is not blocked)
  ai_proxy_session: HttpOnly; SameSite=Strict; Path=/; Max-Age=<expires_in>
    (+ Secure in non-dev environments)

Security inviolables (§3):
  - ID tokens ONLY accepted from server-side httpx POST to token endpoint
  - State + nonce single-use (cookies cleared on callback)
  - Provisioned role is ALWAYS member (hardcoded in use case)
  - client_secret never logged or in response body
  - No tokens in response body — only httpOnly cookies
"""

from __future__ import annotations

import secrets
import urllib.parse
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.api.deps import get_oidc_use_case
from gateway.auth.application.use_cases import OidcLoginUseCase
from gateway.auth.domain.errors import (
    OidcDomainNotMappedError,
    OidcInvalidCallbackError,
    OidcSessionExpiredError,
    OidcStateMismatchError,
    OidcTenantConflictError,
    OidcTokenExpiredError,
    OidcTokenInvalidError,
    OidcUpstreamError,
)
from gateway.core.db import get_session
from gateway.core.errors import ProblemError

oidc_router = APIRouter(prefix="/auth/oidc", tags=["oidc"])

_OIDC_COOKIE_PATH = "/auth/oidc"
_SESSION_COOKIE_PATH = "/"
_STATE_NONCE_MAX_AGE = 300  # 5 minutes


def _generate_token(n_bytes: int = 32) -> str:
    """Generate a cryptographically random base64url-encoded token."""
    return secrets.token_urlsafe(n_bytes)


def _set_oidc_cookie(
    response: Response,
    name: str,
    value: str,
    max_age: int,
    secure: bool,
) -> None:
    """Set an OIDC state/nonce cookie: HttpOnly, SameSite=Lax, Path=/auth/oidc."""
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        path=_OIDC_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def _clear_oidc_cookie(response: Response, name: str, secure: bool) -> None:
    """Clear an OIDC state/nonce cookie (Max-Age=0)."""
    response.set_cookie(
        key=name,
        value="",
        max_age=0,
        path=_OIDC_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


@oidc_router.get("/login")
async def oidc_login(request: Request) -> Response:
    """GET /auth/oidc/login — initiate OIDC authorization-code flow.

    Returns 302 redirect to IdP authorize endpoint with state + nonce cookies set.
    Returns 404 ERR_OIDC_NOT_CONFIGURED when OIDC is disabled.
    """
    settings = request.app.state.settings

    if not settings.oidc_enabled:
        raise ProblemError(
            404,
            "ERR_OIDC_NOT_CONFIGURED",
            "OIDC login is not configured on this platform",
        )

    # Generate cryptographically random state + nonce
    state = _generate_token(32)
    nonce = _generate_token(32)

    # Derive authorize URL: use GATEWAY_OIDC_AUTHORIZE_URL if set, else {issuer}/authorize
    authorize_url = settings.oidc_authorize_url or f"{settings.oidc_issuer}/authorize"

    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
        }
    )
    location = f"{authorize_url}?{params}"

    secure = settings.environment != "dev"

    response = RedirectResponse(url=location, status_code=302)
    _set_oidc_cookie(response, "oidc_state", state, _STATE_NONCE_MAX_AGE, secure)
    _set_oidc_cookie(response, "oidc_nonce", nonce, _STATE_NONCE_MAX_AGE, secure)
    return response


@oidc_router.get("/callback")
async def oidc_callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """GET /auth/oidc/callback — handle IdP redirect, provision user, mint session.

    Returns 302 to GATEWAY_OIDC_POST_LOGIN_REDIRECT with ai_proxy_session cookie set.
    Returns 4xx/5xx problem+json on any validation or IdP failure.
    """
    settings = request.app.state.settings

    if not settings.oidc_enabled:
        raise ProblemError(
            404,
            "ERR_OIDC_NOT_CONFIGURED",
            "OIDC login is not configured on this platform",
        )

    # Extract query params
    code: str | None = request.query_params.get("code")
    state: str | None = request.query_params.get("state")

    # Extract cookies
    cookie_state: str | None = request.cookies.get("oidc_state")
    cookie_nonce: str | None = request.cookies.get("oidc_nonce")

    # Build use case
    use_case: OidcLoginUseCase = get_oidc_use_case(request, session)

    # Execute the OIDC flow — map domain errors to HTTP problem responses
    try:
        jwt_token, expires_in = await use_case.execute(
            code=code,
            state=state,
            cookie_state=cookie_state,
            cookie_nonce=cookie_nonce,
        )
    except OidcInvalidCallbackError as exc:
        raise ProblemError(400, "ERR_OIDC_INVALID_CALLBACK", "Invalid callback parameters") from exc
    except OidcSessionExpiredError as exc:
        raise ProblemError(400, "ERR_OIDC_SESSION_EXPIRED", "OIDC session expired") from exc
    except OidcStateMismatchError as exc:
        raise ProblemError(400, "ERR_OIDC_STATE_MISMATCH", "State parameter mismatch") from exc
    except OidcUpstreamError as exc:
        raise ProblemError(502, "ERR_OIDC_UPSTREAM_ERROR", "IdP token exchange failed") from exc
    except OidcTokenExpiredError as exc:
        raise ProblemError(401, "ERR_OIDC_TOKEN_EXPIRED", "ID token has expired") from exc
    except OidcTokenInvalidError as exc:
        raise ProblemError(401, "ERR_OIDC_TOKEN_INVALID", "ID token validation failed") from exc
    except OidcDomainNotMappedError as exc:
        raise ProblemError(
            403, "ERR_OIDC_DOMAIN_NOT_MAPPED", "Email domain not permitted for SSO login"
        ) from exc
    except OidcTenantConflictError as exc:
        raise ProblemError(
            403, "ERR_OIDC_TENANT_CONFLICT", "Email is bound to a different tenant"
        ) from exc

    secure = settings.environment != "dev"
    post_login_redirect = settings.oidc_post_login_redirect

    response = RedirectResponse(url=post_login_redirect, status_code=302)

    # Set session cookie — matches BFF /api/auth/login cookie attributes exactly
    response.set_cookie(
        key="ai_proxy_session",
        value=jwt_token,
        max_age=expires_in,
        path=_SESSION_COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=secure,
    )

    # Clear state + nonce cookies (single-use)
    _clear_oidc_cookie(response, "oidc_state", secure)
    _clear_oidc_cookie(response, "oidc_nonce", secure)

    return response
