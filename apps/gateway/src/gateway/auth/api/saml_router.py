"""SAML gateway routes — public (no auth deps).

Routes:
  GET  /auth/saml/login   — initiate SP-initiated SAML login (M1)
  POST /auth/saml/acs     — Assertion Consumer Service; mints session cookie (M3-M9, M13)

Cookie posture:
  NONE set at /login (M1: "no cookies set" — tenant binding is server-side,
  Ground R1: SameSite=Lax cookies do not survive a cross-site POST to /acs).
  ai_proxy_session: HttpOnly; SameSite=Strict; Path=/; Max-Age=<expires_in>
    (+ Secure in non-dev environments) — IDENTICAL attributes to the OIDC
    callback's cookie-set call (M8).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.api.saml_deps import get_saml_acs_use_case, get_saml_login_init_use_case
from gateway.auth.domain.saml_errors import (
    SamlAssertionExpiredError,
    SamlAssertionReplayedError,
    SamlAudienceMismatchError,
    SamlEmailMissingError,
    SamlIssuerMismatchError,
    SamlNotConfiguredError,
    SamlRequestAlreadyUsedError,
    SamlRequestNotFoundError,
    SamlResponseMismatchError,
    SamlSignatureInvalidError,
    SamlStoreUnavailableError,
    SamlTenantConflictError,
)
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    PLAN_SEAT_CAP_EXCEEDED,
    SAML_ASSERTION_EXPIRED,
    SAML_ASSERTION_REPLAYED,
    SAML_AUDIENCE_MISMATCH,
    SAML_EMAIL_MISSING,
    SAML_ISSUER_MISMATCH,
    SAML_NOT_CONFIGURED,
    SAML_REQUEST_ALREADY_USED,
    SAML_REQUEST_NOT_FOUND,
    SAML_RESPONSE_MISMATCH,
    SAML_SIGNATURE_INVALID,
    SAML_STORE_UNAVAILABLE,
    SAML_TENANT_CONFLICT,
)
from gateway.tenants.domain.errors import SeatCapExceededError

saml_router = APIRouter(prefix="/auth/saml", tags=["saml"])

_SESSION_COOKIE_PATH = "/"


@saml_router.get("/login")
async def saml_login(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """GET /auth/saml/login — initiate SP-initiated SAML login (M1).

    Accepts ?domain=<email_domain>; the tenant is resolved via the verified
    tenant_domain_claims source of truth (domain-routing-unification §3 M3),
    then the SamlProviderConfig is loaded by tenant_id.
    Returns 302 to the IdP SSO URL with a deflate+base64 SAMLRequest param.
    Returns 404 ERR_SAML_NOT_CONFIGURED when the domain has no verified claim,
    the claimed tenant has no enabled config, or no domain is supplied
    (domain-routing-unification §3 M3/R1 — FROZEN @ v2/CR-v2: reuses the
    EXISTING 404, no new SAML-specific 403 code).
    No cookies are set (M1 — tenant binding is server-side, see M3).
    """
    domain: str | None = request.query_params.get("domain")
    relay_state: str | None = request.query_params.get("RelayState")

    use_case = get_saml_login_init_use_case(request, session)
    try:
        redirect_url = await use_case.execute(domain=domain, relay_state=relay_state)
    except SamlNotConfiguredError as exc:
        raise SAML_NOT_CONFIGURED.exc() from exc
    except SamlStoreUnavailableError as exc:
        raise SAML_STORE_UNAVAILABLE.exc() from exc

    return RedirectResponse(url=redirect_url, status_code=302)


@saml_router.post("/acs")
async def saml_acs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """POST /auth/saml/acs — Assertion Consumer Service (M3-M9, M12, M13).

    Body: form-encoded {SAMLResponse: <base64 XML>, RelayState?: <str>}.
    Tenant identity is resolved EXCLUSIVELY via the server-side pending-
    request store (M3) — never a cookie, never any unverified SAMLResponse
    field.

    Returns 302 with ai_proxy_session cookie set on success (M8).
    Returns 4xx/503 problem+json on any validation/replay/store failure.
    """
    settings = request.app.state.settings
    form = await request.form()
    saml_response_b64_raw = form.get("SAMLResponse")
    relay_state_raw = form.get("RelayState")
    saml_response_b64: str = saml_response_b64_raw if isinstance(saml_response_b64_raw, str) else ""
    relay_state: str | None = relay_state_raw if isinstance(relay_state_raw, str) else None

    use_case = get_saml_acs_use_case(request, session)

    if not saml_response_b64:
        raise SAML_REQUEST_NOT_FOUND.exc(detail="SAMLResponse form field is missing")

    try:
        jwt_token, expires_in, redirect_path = await use_case.execute(
            saml_response_b64=saml_response_b64, relay_state=relay_state
        )
    except SamlRequestAlreadyUsedError as exc:
        raise SAML_REQUEST_ALREADY_USED.exc() from exc
    except SamlRequestNotFoundError as exc:
        raise SAML_REQUEST_NOT_FOUND.exc() from exc
    except SamlSignatureInvalidError as exc:
        raise SAML_SIGNATURE_INVALID.exc() from exc
    except SamlIssuerMismatchError as exc:
        raise SAML_ISSUER_MISMATCH.exc() from exc
    except SamlAudienceMismatchError as exc:
        raise SAML_AUDIENCE_MISMATCH.exc() from exc
    except SamlResponseMismatchError as exc:
        raise SAML_RESPONSE_MISMATCH.exc() from exc
    except SamlAssertionExpiredError as exc:
        raise SAML_ASSERTION_EXPIRED.exc() from exc
    except SamlAssertionReplayedError as exc:
        raise SAML_ASSERTION_REPLAYED.exc() from exc
    except SamlEmailMissingError as exc:
        raise SAML_EMAIL_MISSING.exc() from exc
    except SamlTenantConflictError as exc:
        raise SAML_TENANT_CONFLICT.exc() from exc
    except SamlNotConfiguredError as exc:
        raise SAML_NOT_CONFIGURED.exc() from exc
    except SamlStoreUnavailableError as exc:
        raise SAML_STORE_UNAVAILABLE.exc() from exc
    except SeatCapExceededError as exc:
        # plan-seat-cap TASK.md §3 (FROZEN @ v1, M5/R2) — a superseding addition to this
        # already-shipped, frozen router; identical shape to the OIDC callback's own
        # translation, the "provision new user" branch only (M7).
        raise PLAN_SEAT_CAP_EXCEEDED.exc(
            extra={
                "upgrade_hint": {
                    "plan_id": str(exc.plan_id),
                    "plan_name": exc.plan_name,
                    "seat_cap": exc.seat_cap,
                    "current_seats": exc.current_seats,
                }
            }
        ) from exc

    secure = settings.environment != "dev"
    response = RedirectResponse(url=redirect_path, status_code=302)
    response.set_cookie(
        key="ai_proxy_session",
        value=jwt_token,
        max_age=expires_in,
        path=_SESSION_COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=secure,
    )
    return response
