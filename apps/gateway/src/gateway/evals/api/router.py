"""FastAPI router for the evals domain (/v1/evals/*) — OpenAI-wire CRUD.

eval-set-store PLAN.md §3 (FROZEN @ v1). Auth + tenant-scope mirror
``gateway.vector_stores.api.router``; the wire (``es_<32hex>`` / ``ec_<32hex>`` ids,
a payload-free set object, a case object that echoes NO payload) is this task's own.

Auth: every endpoint requires ``Authorization: Bearer sk-...`` via
``SqlAlchemyKeyAuthenticator`` -> ``AuthzResult{tenant_id, key_id}``. Missing/invalid
key -> 401 (ERR_AUTH_INVALID_KEY). Expired key -> 401 (ERR_AUTH_KEY_EXPIRED).

Tenant isolation (M4/M5): every store call passes ``tenant_id`` from the authenticated
key. An unknown OR cross-tenant OR malformed set id -> 404 (ERR_EVAL_SET_NOT_FOUND),
byte-identical for both causes so ownership never leaks (no enumeration oracle).

ZDR (M3): the CASE write is the only payload-bearing surface. The gate
(``raise_if_zdr_locked``, SELECT ... FOR UPDATE) fires ATOMICALLY with the insert inside
``SqlAlchemyEvalStore.create_case`` — a flip landing mid-await persists nothing. It raises
a ``ProblemError``; this router catches it and re-renders it in the SAME OpenAI ``{"error":
{"code": ...}}`` envelope as every other failure on this surface, so the whole /v1/evals
surface speaks one envelope (M7: a payload-free SET is NOT gated — a ZDR tenant may create
one; only the case is refused).

Wire error envelope: the OpenAI SDK expects a bare ``{"error": {"code": ...}}`` body for
this surface — NOT the gateway's RFC-9457 problem+json shape — so every domain (422/404/403)
failure is returned as a JSONResponse here directly, mirroring ``vector_stores.api.router``.
Auth failures (401) still raise ProblemError (their contract pins only the status code).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import build_audit_event, record_audit
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_KEY_EXPIRED,
    AUTH_KEY_INVALID,
    EVAL_CASE_INVALID,
    EVAL_SET_NAME_CONFLICT,
    EVAL_SET_NOT_FOUND,
    ErrorSpec,
)
from gateway.core.errors import ProblemError
from gateway.evals.application.use_cases import (
    CreateEvalCaseUseCase,
    CreateEvalSetUseCase,
)
from gateway.evals.domain.errors import EvalSetNameConflict, EvalSetNotFound
from gateway.evals.infrastructure.orm import EvalCaseRow, EvalSetRow
from gateway.evals.infrastructure.repository import SqlAlchemyEvalStore
from gateway.evals.wire_id import parse_set_wire_id, to_case_wire_id, to_set_wire_id
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator

evals_router = APIRouter(tags=["evals"])

# Singleton stateless hasher — safe to share.
_hasher = Sha256SecretHasher()

_MAX_NAME_LEN = 256
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _err(spec: ErrorSpec) -> JSONResponse:
    """Render an ErrorSpec as the OpenAI-wire ``{"error": {"code": ...}}`` envelope."""
    return JSONResponse(
        status_code=spec.status,
        content={"error": {"code": spec.code, "message": spec.title_template}},
    )


def _err_from_problem(exc: ProblemError) -> JSONResponse:
    """Re-render a ProblemError (e.g. the ZDR gate's 403) into THIS surface's envelope.

    Keeps the whole /v1/evals surface speaking one ``{"error": {"code": ...}}`` body
    instead of leaking the gateway's flat problem+json for the one exception that
    bubbles up from the store layer (M3 ZDR refuse).
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.title}},
    )


def _not_found() -> JSONResponse:
    return _err(EVAL_SET_NOT_FOUND)


def _extract_raw_key(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return ""


async def _authenticate(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthzResult:
    """Dependency: authenticate Bearer sk-... key -> AuthzResult. 401 on any failure."""
    raw_key = _extract_raw_key(request)
    repo = SqlAlchemyApiKeyRepository(session)
    authz_use_case = AuthzUseCase(repo, _hasher)
    authenticator = SqlAlchemyKeyAuthenticator(authz_use_case)
    try:
        authz = await authenticator.authenticate(raw_key)
    except InvalidApiKeyError:
        raise AUTH_KEY_INVALID.exc() from None
    if authz.expires_at is not None:
        exp = authz.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp <= datetime.now(tz=UTC):
            raise AUTH_KEY_EXPIRED.exc() from None
    return authz


def _get_store(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyEvalStore:
    return SqlAlchemyEvalStore(session)


def _unix(created: datetime) -> int:
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return int(created.timestamp())


def _eval_set_object(row: EvalSetRow, *, case_count: int | None = None) -> dict[str, Any]:
    """The eval_set wire object (created_at as a unix int). NO payload — metadata only."""
    obj: dict[str, Any] = {
        "id": to_set_wire_id(row.id),
        "object": "eval.set",
        "created_at": _unix(row.created_at),
        "name": row.name,
        "description": row.description,
    }
    if case_count is not None:
        obj["case_count"] = case_count
    return obj


def _eval_case_object(row: EvalCaseRow) -> dict[str, Any]:
    """The eval_case wire object. Echoes the ASSERTION but NEVER the request_body payload.

    The request_body is write-only from the API's view (it is the replayed prompt, the
    payload-bearing field the ZDR gate protects); only the assertion — the deterministic
    check — is readable back.
    """
    return {
        "id": to_case_wire_id(row.id),
        "object": "eval.case",
        "created_at": _unix(row.created_at),
        "eval_set_id": to_set_wire_id(row.eval_set_id),
        "assertion": row.assertion,
    }


def _validate_name(name: Any) -> tuple[str, JSONResponse | None]:
    """A set name must be a non-empty string of <= 256 chars. Reject -> 422, nothing persisted."""
    if not isinstance(name, str):
        return "", _err(EVAL_CASE_INVALID)
    stripped = name.strip()
    if not stripped or len(name) > _MAX_NAME_LEN:
        return "", _err(EVAL_CASE_INVALID)
    return name, None


def _validate_case_payload(body: dict[str, Any]) -> JSONResponse | None:
    """request_body + assertion must EACH be a non-empty object. Reject -> 422 (R:CASE_INVALID).

    Validated on the caller's own input BEFORE the parent set is resolved, so it never
    reveals whether the set exists (no oracle).
    """
    request_body = body.get("request_body")
    assertion = body.get("assertion")
    if not isinstance(request_body, dict) or not request_body:
        return _err(EVAL_CASE_INVALID)
    if not isinstance(assertion, dict) or not assertion:
        return _err(EVAL_CASE_INVALID)
    return None


@evals_router.post("/v1/evals/sets", status_code=201, response_model=None)
async def create_eval_set(
    body: dict[str, Any],
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    store: Annotated[SqlAlchemyEvalStore, Depends(_get_store)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | JSONResponse:
    """Create a payload-free eval set for the authenticated tenant (M1).

    A ZDR tenant MAY create one — the set carries no payload, so the ZDR gate does not
    apply here (M7); only the case write (below) is gated.
    """
    name, name_error = _validate_name(body.get("name"))
    if name_error is not None:
        return name_error
    description = body.get("description")
    if description is not None and not isinstance(description, str):
        return _err(EVAL_CASE_INVALID)

    try:
        row = await CreateEvalSetUseCase(store).execute(
            tenant_id=authz.tenant_id, name=name, description=description
        )
    except EvalSetNameConflict:
        # E4: the store already rolled the transaction back on the UNIQUE(tenant_id, name)
        # violation — nothing to commit, no silent second set.
        return _err(EVAL_SET_NAME_CONFLICT)
    await session.commit()
    audit_event = build_audit_event(
        action="evals.set_create",
        target_type="eval_set",
        target_id=to_set_wire_id(row.id),
        tenant_id=authz.tenant_id,
        actor_key_id=authz.key_id,
    )
    if audit_event is not None:
        await record_audit(request.app.state.sessionmaker, audit_event)
    return _eval_set_object(row)


@evals_router.get("/v1/evals/sets", status_code=200)
async def list_eval_sets(
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    store: Annotated[SqlAlchemyEvalStore, Depends(_get_store)],
) -> dict[str, Any]:
    """List the tenant's eval sets, newest first, each with its live case_count (M1)."""
    rows = await store.list_sets(tenant_id=authz.tenant_id)
    data = [
        _eval_set_object(
            row,
            case_count=await store.count_cases(tenant_id=authz.tenant_id, eval_set_id=row.id),
        )
        for row in rows
    ]
    return {"object": "list", "data": data}


@evals_router.post("/v1/evals/sets/{set_id}/cases", status_code=201, response_model=None)
async def create_eval_case(
    set_id: str,
    body: dict[str, Any],
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    store: Annotated[SqlAlchemyEvalStore, Depends(_get_store)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | JSONResponse:
    """Append a case (request_body + assertion) to a tenant's set — the payload write (M2).

    Order: validate the caller's payload shape first (R:CASE_INVALID, reveals nothing about
    the set) -> resolve the parent set in tenant scope (absent/cross-tenant/malformed -> a
    uniform 404, M4/M5) -> ZDR gate ATOMIC with the insert (M3). Nothing is committed on any
    reject, so a refused write persists ZERO rows.
    """
    payload_error = _validate_case_payload(body)
    if payload_error is not None:
        return payload_error

    resolved = parse_set_wire_id(set_id)
    if resolved is None:
        return _not_found()

    try:
        row = await CreateEvalCaseUseCase(store).execute(
            tenant_id=authz.tenant_id,
            eval_set_id=resolved,
            request_body=body["request_body"],
            assertion=body["assertion"],
        )
    except EvalSetNotFound:
        return _not_found()
    except ProblemError as exc:
        # M3: the ZDR gate refused inside create_case. The session is left uncommitted,
        # so nothing lands at rest; re-render the 403 in this surface's envelope.
        return _err_from_problem(exc)

    await session.commit()
    audit_event = build_audit_event(
        action="evals.case_create",
        target_type="eval_case",
        target_id=to_case_wire_id(row.id),
        tenant_id=authz.tenant_id,
        actor_key_id=authz.key_id,
        metadata={"eval_set_id": to_set_wire_id(resolved)},
    )
    if audit_event is not None:
        await record_audit(request.app.state.sessionmaker, audit_event)
    return _eval_case_object(row)


@evals_router.get("/v1/evals/sets/{set_id}/cases", status_code=200, response_model=None)
async def list_eval_cases(
    set_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    store: Annotated[SqlAlchemyEvalStore, Depends(_get_store)],
) -> dict[str, Any] | JSONResponse:
    """List a set's cases in creation order (M5-order). 404 for unknown/cross-tenant/malformed.

    Echoes the assertion but NEVER the request_body (write-only payload).
    """
    resolved = parse_set_wire_id(set_id)
    if resolved is None:
        return _not_found()
    parent = await store.get_set(tenant_id=authz.tenant_id, eval_set_id=resolved)
    if parent is None:
        return _not_found()

    rows = await store.list_cases(tenant_id=authz.tenant_id, eval_set_id=resolved)
    return {"object": "list", "data": [_eval_case_object(row) for row in rows]}
