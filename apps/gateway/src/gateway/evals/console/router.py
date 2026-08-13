"""FastAPI router for the evals CONSOLE (/admin/evals/*) — evals-console §3.

This is the SESSION-authed control-plane twin of the /v1/evals API-key surface. It exists so
the dashboard BFF (which forwards the operator's session JWT as a Bearer token) can read and
author evals without ever holding a raw API key.

Auth (M1): every endpoint depends on ``get_current_identity`` (Bearer JWT -> ``Identity`` with
``tenant_id`` + ``role``) — NOT ``_authenticate`` (which reads an ``sk-...`` API key). Any token
failure raises ProblemError(401) from the shared catalog dependency.

Reuse, not fork (R:LOGIC_FORK, M1): this router computes NOTHING new. It resolves the tenant
from the session Identity and calls the SAME stores and the SAME verdict core the /v1 surface
uses — ``SqlAlchemyEvalStore`` (+ the Create* use-cases), ``SqlAlchemyEvalRunStore``,
``SqlAlchemyEvalBaselineStore``, and ``build_verdict_body``. The wire objects are the frozen
builders from the /v1 routers (``_eval_set_object`` / ``_eval_case_object`` / ``_run_object``),
so /admin and /v1 render byte-identical objects.

No launch, no raw key (M2, R:RAW_KEY_IN_CONSOLE): there is deliberately NO run-launch route
here — launching dials upstreams and must bill the launching key as live traffic
(eval-run-executor A1), which needs a raw key the session must never hold. Launch stays on the
/v1 API-key path; the console links to it. This module never imports or calls
``_extract_raw_key``.

Tenant isolation (M1, R:CROSS_TENANT): every store call passes ``identity.tenant_id``; an absent
OR cross-tenant set/run is a uniform 404 (ERR_EVAL_SET_NOT_FOUND / ERR_EVAL_RUN_NOT_FOUND).

Error envelope: unlike /v1 (which speaks the OpenAI ``{"error": {...}}`` body for SDK
compatibility), this control-plane surface raises ProblemError so failures render as the
gateway's native RFC-9457 problem+json — the shape the dashboard's ``BffError.problem`` reads.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.api.deps import get_current_identity
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    EVAL_CASE_INVALID,
    EVAL_RUN_NOT_FOUND,
    EVAL_SET_NAME_CONFLICT,
    EVAL_SET_NOT_FOUND,
)

# Wire-object builders + the ONE verdict core are owned by the /v1 routers; reusing them (rather
# than re-deriving) is exactly what keeps /admin and /v1 from drifting (R:LOGIC_FORK). Same
# sanctioned private-reuse pattern the run + verdict routers already use across the surface.
from gateway.evals.api.router import (
    _eval_case_object,  # pyright: ignore[reportPrivateUsage]
    _eval_set_object,  # pyright: ignore[reportPrivateUsage]
    _unix,  # pyright: ignore[reportPrivateUsage]
)
from gateway.evals.application.use_cases import (
    CreateEvalCaseUseCase,
    CreateEvalSetUseCase,
)
from gateway.evals.domain.errors import EvalSetNameConflict, EvalSetNotFound
from gateway.evals.infrastructure.repository import SqlAlchemyEvalStore
from gateway.evals.runs.api.run_router import _run_object  # pyright: ignore[reportPrivateUsage]
from gateway.evals.runs.infrastructure.repository import SqlAlchemyEvalRunStore
from gateway.evals.scoring.scorers import DeterministicScorer
from gateway.evals.verdict.api.router import build_verdict_body
from gateway.evals.verdict.infrastructure.repository import SqlAlchemyEvalBaselineStore
from gateway.evals.wire_id import (
    parse_run_wire_id,
    parse_set_wire_id,
    to_case_wire_id,
    to_run_wire_id,
    to_set_wire_id,
)
from gateway.tenants.domain.entities import Identity

evals_console_router = APIRouter(tags=["evals-console"])

# The deterministic scorer is PURE + stateless — one shared instance is safe, and it is the SAME
# class the verdict core counts with, so per-case `passed` never disagrees with the run verdict.
_SCORER = DeterministicScorer()

_MAX_NAME_LEN = 256


def _run_store(request: Request) -> SqlAlchemyEvalRunStore:
    return SqlAlchemyEvalRunStore(request.app.state.sessionmaker)


def _baseline_store(request: Request) -> SqlAlchemyEvalBaselineStore:
    return SqlAlchemyEvalBaselineStore(request.app.state.sessionmaker)


def _require_name(value: Any) -> str:
    """A set name must be a non-empty string of <= 256 chars — else 422, nothing persisted."""
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_NAME_LEN:
        raise EVAL_CASE_INVALID.exc()
    return value


@evals_console_router.get("/admin/evals/sets", status_code=200, response_model=None)
async def list_sets(
    identity: Annotated[Identity, Depends(get_current_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """The session tenant's eval sets, newest first, each with its live case_count (M2/A2)."""
    store = SqlAlchemyEvalStore(session)
    rows = await store.list_sets(tenant_id=identity.tenant_id)
    data = [
        _eval_set_object(
            row,
            case_count=await store.count_cases(tenant_id=identity.tenant_id, eval_set_id=row.id),
        )
        for row in rows
    ]
    return {"object": "list", "data": data}


@evals_console_router.get("/admin/evals/sets/{set_id}", status_code=200, response_model=None)
async def get_set_detail(
    set_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """A set with its cases, runs (newest first), and pinned baseline (M2/M3). Uniform 404 (M1)."""
    resolved = parse_set_wire_id(set_id)
    if resolved is None:
        raise EVAL_SET_NOT_FOUND.exc()
    store = SqlAlchemyEvalStore(session)
    parent = await store.get_set(tenant_id=identity.tenant_id, eval_set_id=resolved)
    if parent is None:
        raise EVAL_SET_NOT_FOUND.exc()

    case_count = await store.count_cases(tenant_id=identity.tenant_id, eval_set_id=resolved)
    cases = await store.list_cases(tenant_id=identity.tenant_id, eval_set_id=resolved)

    run_store = _run_store(request)
    runs = await run_store.list_runs(tenant_id=identity.tenant_id, eval_set_id=resolved)
    run_objs: list[dict[str, Any]] = []
    for run in runs:
        # Each run's own launch snapshot is its denominator (executor A2) — a historical run's
        # count is NOT the set's current case_count.
        snapshot = await run_store.snapshot_cases(
            tenant_id=run.tenant_id, eval_set_id=run.eval_set_id, created_at_max=run.created_at
        )
        run_objs.append(_run_object(run, case_count=len(snapshot)))

    baseline = await _baseline_store(request).get_baseline(
        tenant_id=identity.tenant_id, eval_set_id=resolved
    )

    obj = _eval_set_object(parent, case_count=case_count)
    obj["cases"] = [_eval_case_object(c) for c in cases]
    obj["runs"] = run_objs
    obj["baseline_run_id"] = to_run_wire_id(baseline.run_id) if baseline is not None else None
    return obj


@evals_console_router.get(
    "/admin/evals/runs/{run_id}/verdict", status_code=200, response_model=None
)
async def get_run_verdict(
    run_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> dict[str, Any]:
    """A run's verdict vs its set's pinned baseline — the SAME core as /v1 (M1). Uniform 404."""
    resolved = parse_run_wire_id(run_id)
    if resolved is None:
        raise EVAL_RUN_NOT_FOUND.exc()
    run_store = _run_store(request)
    run = await run_store.get_run(tenant_id=identity.tenant_id, run_id=resolved)
    if run is None:
        raise EVAL_RUN_NOT_FOUND.exc()
    return await build_verdict_body(run, run_store, _baseline_store(request))


@evals_console_router.get("/admin/evals/runs/{run_id}/cases", status_code=200, response_model=None)
async def get_run_cases(
    run_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> dict[str, Any]:
    """Per-case DIFF rows (M4): each launch-snapshot case joined with its result.

    One row per case that existed at launch (executor A2), in creation order. A case with no
    result row yet is ``pending`` and carries NO response_text (never a fabricated actual, E3);
    a refused/errored case carries its status + reason but no answer. The assertion (expected)
    travels with every row so the UI can render expected-vs-actual without a second call.

    A completed row also carries the AUTHORITATIVE per-case ``passed`` bool — computed by the
    SAME deterministic scorer the verdict counts with (M1). The UI renders that verdict directly
    and never re-derives pass/fail from the payload; a client-side re-implementation would be a
    scoring fork (R:LOGIC_FORK) AND would disagree with the banner for e.g. a ``contains``
    assertion (expected "echo" vs actual "echo:one" is a PASS the scorer sees but string equality
    would miss). Non-completed rows carry no ``passed`` (they are counted, never a pass, A4).
    """
    resolved = parse_run_wire_id(run_id)
    if resolved is None:
        raise EVAL_RUN_NOT_FOUND.exc()
    run_store = _run_store(request)
    run = await run_store.get_run(tenant_id=identity.tenant_id, run_id=resolved)
    if run is None:
        raise EVAL_RUN_NOT_FOUND.exc()

    snapshot = await run_store.snapshot_cases(
        tenant_id=run.tenant_id, eval_set_id=run.eval_set_id, created_at_max=run.created_at
    )
    results = await run_store.list_case_results(tenant_id=identity.tenant_id, run_id=resolved)
    by_case = {r.eval_case_id: r for r in results}

    data: list[dict[str, Any]] = []
    for case in snapshot:
        res = by_case.get(case.id)
        row: dict[str, Any] = {
            "object": "eval.case_diff",
            "eval_case_id": to_case_wire_id(case.id),
            "assertion": case.assertion,
            "status": res.status if res is not None else "pending",
        }
        if res is not None and res.response_text is not None:
            row["response_text"] = res.response_text
        if res is not None and res.reason is not None:
            row["reason"] = res.reason
        if res is not None and res.status == "completed":
            # Authoritative pass/fail from the SAME scorer the verdict uses — never re-derived
            # by the client (R:LOGIC_FORK). A pending/refused/errored case gets no `passed`.
            row["passed"] = _SCORER.score(
                assertion=case.assertion, output_text=res.response_text or ""
            ).passed
        data.append(row)
    return {"object": "list", "data": data}


@evals_console_router.post("/admin/evals/sets", status_code=201, response_model=None)
async def create_set(
    body: dict[str, Any],
    identity: Annotated[Identity, Depends(get_current_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Create a payload-free eval set for the session tenant (M2, authoring)."""
    name = _require_name(body.get("name"))
    description = body.get("description")
    if description is not None and not isinstance(description, str):
        raise EVAL_CASE_INVALID.exc()

    store = SqlAlchemyEvalStore(session)
    try:
        row = await CreateEvalSetUseCase(store).execute(
            tenant_id=identity.tenant_id, name=name, description=description
        )
    except EvalSetNameConflict:
        raise EVAL_SET_NAME_CONFLICT.exc() from None
    await session.commit()
    return _eval_set_object(row)


@evals_console_router.post("/admin/evals/sets/{set_id}/cases", status_code=201, response_model=None)
async def add_case(
    set_id: str,
    body: dict[str, Any],
    identity: Annotated[Identity, Depends(get_current_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Append a case (request_body + assertion) to a tenant's set (M2, authoring).

    The ZDR gate inside ``create_case`` raises ProblemError(403) if the tenant is ZDR-locked;
    it propagates as native problem+json and nothing is committed (the payload never lands).
    """
    request_body = body.get("request_body")
    assertion = body.get("assertion")
    if not isinstance(request_body, dict) or not request_body:
        raise EVAL_CASE_INVALID.exc()
    if not isinstance(assertion, dict) or not assertion:
        raise EVAL_CASE_INVALID.exc()

    resolved = parse_set_wire_id(set_id)
    if resolved is None:
        raise EVAL_SET_NOT_FOUND.exc()

    store = SqlAlchemyEvalStore(session)
    try:
        row = await CreateEvalCaseUseCase(store).execute(
            tenant_id=identity.tenant_id,
            eval_set_id=resolved,
            request_body=request_body,
            assertion=assertion,
        )
    except EvalSetNotFound:
        raise EVAL_SET_NOT_FOUND.exc() from None
    await session.commit()
    return _eval_case_object(row)


@evals_console_router.put(
    "/admin/evals/sets/{set_id}/baseline", status_code=200, response_model=None
)
async def pin_baseline(
    set_id: str,
    body: dict[str, Any],
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Pin a run of this set as its baseline (M2/M3). Uniform 404 for absent/cross-tenant (M1)."""
    resolved_set = parse_set_wire_id(set_id)
    if resolved_set is None:
        raise EVAL_SET_NOT_FOUND.exc()
    parent = await SqlAlchemyEvalStore(session).get_set(
        tenant_id=identity.tenant_id, eval_set_id=resolved_set
    )
    if parent is None:
        raise EVAL_SET_NOT_FOUND.exc()

    raw_run = body.get("run_id")
    resolved_run = parse_run_wire_id(raw_run) if isinstance(raw_run, str) else None
    if resolved_run is None:
        raise EVAL_RUN_NOT_FOUND.exc()
    run = await _run_store(request).get_run(tenant_id=identity.tenant_id, run_id=resolved_run)
    # The run must exist, be this tenant's, AND belong to THIS set — else uniform 404.
    if run is None or run.eval_set_id != resolved_set:
        raise EVAL_RUN_NOT_FOUND.exc()

    baseline = await _baseline_store(request).pin_baseline(
        tenant_id=identity.tenant_id, eval_set_id=resolved_set, run_id=resolved_run
    )
    return {
        "object": "eval.baseline",
        "eval_set_id": to_set_wire_id(resolved_set),
        "baseline_run_id": to_run_wire_id(baseline.run_id),
        "pinned_at": _unix(baseline.pinned_at),
    }
