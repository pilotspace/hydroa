"""SCIM error envelope (RFC 7644 §3.12) — a deliberate, documented exception to the
project-wide RFC 9457 problem+json convention (used ONLY under /scim/v2/*, TASK.md §0
Honors: "the same kind of accepted, documented inconsistency as the S4 edge-input-
hardening Envoy-native-413-body carve-out").

/admin/scim/tokens (Part A) uses the ordinary gateway.core.error_catalog / ProblemError
machinery instead — this module is exclusively for /scim/v2/* (Part B).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


class ScimApiError(Exception):
    """Raised by scim_router.py handlers; converted to the SCIM error envelope by the
    exception handler registered via register_scim_error_handlers()."""

    def __init__(
        self,
        status: int,
        detail: str,
        scim_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        self.headers = headers or {}


def scim_error_body(exc: ScimApiError) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemas": [ERROR_SCHEMA],
        "status": str(exc.status),
        "detail": exc.detail,
    }
    if exc.scim_type is not None:
        body["scimType"] = exc.scim_type
    return body


def scim_invalid_token() -> ScimApiError:
    """401 — missing/malformed/unknown/revoked bearer, no distinguishing detail (no
    enumeration oracle — mirrors AUTH_KEY_INVALID_AUTHZ's own single-shape contract)."""
    return ScimApiError(401, "invalid_token")


def scim_user_not_found() -> ScimApiError:
    """404 — unknown id OR cross-tenant id, deliberately indistinguishable."""
    return ScimApiError(404, "Resource not found")


def scim_uniqueness(detail: str = "userName already in use") -> ScimApiError:
    """409 — duplicate email (same tenant OR a different tenant)."""
    return ScimApiError(409, detail, scim_type="uniqueness")


def scim_invalid_value(detail: str) -> ScimApiError:
    """400 — malformed payload (missing schemas/userName, invalid PATCH op)."""
    return ScimApiError(400, detail, scim_type="invalidValue")


def scim_mutability(detail: str = "Attribute is immutable") -> ScimApiError:
    """400 — PATCH op targets an immutable path (id, meta)."""
    return ScimApiError(400, detail, scim_type="mutability")


def scim_groups_unsupported() -> ScimApiError:
    """501 — any Groups write."""
    return ScimApiError(501, "Group resource management is not supported")


def scim_rate_limited(retry_after: int) -> ScimApiError:
    """429 — SCIM token rate limit exceeded; Retry-After header always set."""
    return ScimApiError(429, "rate_limited", headers={"Retry-After": str(retry_after)})


def scim_seat_cap_exceeded() -> ScimApiError:
    """403 — admitting this SCIM user would exceed the tenant's seat cap (plan-seat-cap
    TASK.md §3 M5/R4, FROZEN @ v1). No scimType — RFC 7644's vocabulary has no clean fit
    for a business-quota reject (mirrors scim_invalid_token()'s own no-scimType
    precedent)."""
    return ScimApiError(403, "Seat cap exceeded for this tenant's plan")


def register_scim_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ScimApiError)
    async def on_scim_error(_request: Request, exc: ScimApiError) -> Response:  # pyright: ignore[reportUnusedFunction]  — registered via decorator; not called directly
        resp = JSONResponse(status_code=exc.status, content=scim_error_body(exc))
        for key, val in exc.headers.items():
            resp.headers[key] = val
        return resp
