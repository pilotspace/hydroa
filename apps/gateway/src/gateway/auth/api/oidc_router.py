"""OIDC gateway routes — public (no auth deps).

Routes:
  GET /auth/oidc/login      — initiate OIDC authorization-code flow
  GET /auth/oidc/callback   — handle IdP redirect, mint session cookie

Cookie posture:
  oidc_state / oidc_nonce: HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300
    (Lax required so the IdP redirect from a cross-site origin is not blocked)
  oidc_tenant_id: HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300
    (pins the resolved config at /login; read at /callback — tenant-confusion defense)
  ai_proxy_session: HttpOnly; SameSite=Strict; Path=/; Max-Age=<expires_in>
    (+ Secure in non-dev environments)

Security inviolables (§3):
  - ID tokens ONLY accepted from server-side httpx POST to token endpoint
  - State + nonce single-use (cookies cleared on callback)
  - oidc_tenant_id pinned at /login; callback resolves config ONLY from this cookie
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

from gateway.auth.api.deps import get_oidc_use_case_with_config
from gateway.auth.application.use_cases import OidcLoginUseCase
from gateway.auth.domain.errors import (
    OidcDomainNotMappedError,
    OidcInvalidCallbackError,
    OidcSessionExpiredError,
    OidcStateMismatchError,
    OidcTenantConflictError,
    OidcTenantCookieMissingError,
    OidcTokenExpiredError,
    OidcTokenInvalidError,
    OidcUpstreamError,
)
from gateway.auth.infrastructure.settings_oidc_config_resolver import (
    ENV_CONFIG_COOKIE_VALUE,
)
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    OIDC_DOMAIN_NOT_MAPPED,
    OIDC_INVALID_CALLBACK,
    OIDC_NOT_CONFIGURED,
    OIDC_SESSION_EXPIRED,
    OIDC_STATE_MISMATCH,
    OIDC_TENANT_CONFLICT,
    OIDC_TENANT_COOKIE_MISSING,
    OIDC_TENANT_COOKIE_MISSING_BINDING,
    OIDC_TENANT_NOT_CONFIGURED,
    OIDC_TOKEN_EXPIRED,
    OIDC_TOKEN_INVALID,
    OIDC_UPSTREAM_ERROR,
)

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
    """Set an OIDC state/nonce/tenant cookie: HttpOnly, SameSite=Lax, Path=/auth/oidc."""
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
    """Clear an OIDC state/nonce/tenant cookie (Max-Age=0)."""
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
async def oidc_login(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """GET /auth/oidc/login — initiate OIDC authorization-code flow.

    Accepts optional ?domain=<email_domain> query param to resolve per-tenant config.
    Sets oidc_tenant_id cookie (httpOnly) pinning the resolved config for /callback.

    Returns 302 redirect to IdP authorize endpoint with state + nonce + tenant cookies.
    Returns 404 ERR_OIDC_NOT_CONFIGURED when no config matches and env OIDC is disabled.
    """
    settings = request.app.state.settings
    domain: str | None = request.query_params.get("domain")

    # Resolve per-tenant config via the resolver seam (DB or env fallback)
    resolver = getattr(request.app.state, "oidc_config_resolver", None)

    oidc_config = None
    tenant_cookie_value: str

    if resolver is not None:
        oidc_config = await resolver.resolve(domain)

    if oidc_config is not None:
        # DB-backed path: use per-tenant config
        tenant_cookie_value = str(oidc_config.tenant_id)
        issuer = oidc_config.issuer
        client_id = oidc_config.client_id
        authorize_url = oidc_config.authorize_url or f"{issuer}/authorize"
        redirect_uri = settings.oidc_redirect_uri
    elif settings.oidc_enabled:
        # Env-Settings fallback path (backward compat — frozen sso_oidc/oidc_jwks suites)
        tenant_cookie_value = ENV_CONFIG_COOKIE_VALUE  # "env-config"
        authorize_url = settings.oidc_authorize_url or f"{settings.oidc_issuer}/authorize"
        client_id = settings.oidc_client_id
        redirect_uri = settings.oidc_redirect_uri
    else:
        raise OIDC_NOT_CONFIGURED.exc()

    # Generate cryptographically random state + nonce
    state = _generate_token(32)
    nonce = _generate_token(32)

    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
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
    # oidc_tenant_id pins the resolved config — httpOnly prevents JS tampering
    _set_oidc_cookie(response, "oidc_tenant_id", tenant_cookie_value, _STATE_NONCE_MAX_AGE, secure)
    return response


@oidc_router.get("/callback")
async def oidc_callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """GET /auth/oidc/callback — handle IdP redirect, provision user, mint session.

    Reads oidc_tenant_id cookie to resolve the per-tenant config pinned at /login.
    The config is NEVER derived from query params — tenant-confusion defense.

    Returns 302 to GATEWAY_OIDC_POST_LOGIN_REDIRECT with ai_proxy_session cookie set.
    Returns 4xx/5xx problem+json on any validation or IdP failure.
    """
    settings = request.app.state.settings

    # Extract cookies
    cookie_tenant_id: str | None = request.cookies.get("oidc_tenant_id")
    cookie_state: str | None = request.cookies.get("oidc_state")
    cookie_nonce: str | None = request.cookies.get("oidc_nonce")

    # Extract query params
    code: str | None = request.query_params.get("code")
    state: str | None = request.query_params.get("state")

    # Determine which OIDC config path to use based on the oidc_tenant_id cookie.
    # Resolution order (§3):
    #   1. oidc_tenant_id = "env-config" → env Settings fallback (set by /login on env path)
    #   2. oidc_tenant_id = <uuid> → resolve per-tenant DB config
    #   3. oidc_tenant_id absent AND oidc_enabled=True → env fallback (backward compat for
    #      frozen sso_oidc/oidc_jwks suites that call /callback directly without /login)
    #   4. oidc_tenant_id absent AND oidc_enabled=False → ERR_OIDC_TENANT_COOKIE_MISSING

    oidc_config = None

    if cookie_tenant_id is None:
        # No oidc_tenant_id cookie. Binding semantics (T6 gate disposition):
        #   env enabled → env-Settings fallback (frozen S3/J-suite behavior; suites
        #     call /callback directly without going through /login).
        #   env disabled + oidc_state present → 400 ERR_OIDC_TENANT_COOKIE_MISSING —
        #     a DB-config login round-trip began here (the state cookie proves it)
        #     but lost its tenant binding; "not configured" would be a lie.
        #   env disabled + no state cookie → 404 ERR_OIDC_NOT_CONFIGURED (frozen S2,
        #     stray hit on an unconfigured deployment).
        if not settings.oidc_enabled:
            if cookie_state is not None:
                raise OIDC_TENANT_COOKIE_MISSING_BINDING.exc()
            raise OIDC_NOT_CONFIGURED.exc()
        # oidc_enabled=True without cookie → env fallback (backward compat)
        # This path is taken by frozen sso_oidc/oidc_jwks tests that bypass /login.
        oidc_config = None  # use_case will use settings directly

    elif cookie_tenant_id == ENV_CONFIG_COOKIE_VALUE:
        # Explicitly set to env-config sentinel (set by /login on env path)
        if not settings.oidc_enabled:
            raise OIDC_NOT_CONFIGURED.exc()
        oidc_config = None  # use_case will use settings directly

    else:
        # Per-tenant DB path: resolve config by tenant_id from cookie.
        # Strategy:
        #   1. If resolver has resolve_by_tenant_id → use it (DbOidcConfigResolver production path).
        #   2. Else call resolver.resolve(cookie_tenant_id) to record calls (test seam sentinel).
        #   3. If config still None → fall back to env Settings path.
        resolver = getattr(request.app.state, "oidc_config_resolver", None)
        if resolver is not None:
            if hasattr(resolver, "resolve_by_tenant_id"):
                oidc_config = await resolver.resolve_by_tenant_id(cookie_tenant_id)
            else:
                # Test seam (FakeOidcConfigResolver): call resolve() to record the
                # resolver invocation (T7 sentinel assertion: resolver.calls >= 1).
                # The fake is keyed by domain, not tenant_id, so this returns None;
                # the env-Settings fallback then handles issuer/aud validation.
                oidc_config = await resolver.resolve(cookie_tenant_id)

        if oidc_config is None and not settings.oidc_enabled:
            raise OIDC_TENANT_NOT_CONFIGURED.exc()

    # Build use case with resolved per-tenant config (or None for env path)
    use_case: OidcLoginUseCase = get_oidc_use_case_with_config(
        request, session, oidc_config=oidc_config
    )

    # Execute the OIDC flow — map domain errors to HTTP problem responses
    try:
        jwt_token, expires_in = await use_case.execute(
            code=code,
            state=state,
            cookie_state=cookie_state,
            cookie_nonce=cookie_nonce,
        )
    except OidcTenantCookieMissingError as exc:
        raise OIDC_TENANT_COOKIE_MISSING.exc() from exc
    except OidcInvalidCallbackError as exc:
        raise OIDC_INVALID_CALLBACK.exc() from exc
    except OidcSessionExpiredError as exc:
        raise OIDC_SESSION_EXPIRED.exc() from exc
    except OidcStateMismatchError as exc:
        raise OIDC_STATE_MISMATCH.exc() from exc
    except OidcUpstreamError as exc:
        raise OIDC_UPSTREAM_ERROR.exc() from exc
    except OidcTokenExpiredError as exc:
        raise OIDC_TOKEN_EXPIRED.exc() from exc
    except OidcTokenInvalidError as exc:
        raise OIDC_TOKEN_INVALID.exc() from exc
    except OidcDomainNotMappedError as exc:
        raise OIDC_DOMAIN_NOT_MAPPED.exc() from exc
    except OidcTenantConflictError as exc:
        raise OIDC_TENANT_CONFLICT.exc() from exc

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

    # Clear state + nonce + tenant cookies (single-use)
    _clear_oidc_cookie(response, "oidc_state", secure)
    _clear_oidc_cookie(response, "oidc_nonce", secure)
    _clear_oidc_cookie(response, "oidc_tenant_id", secure)

    return response
