"""FastAPI router for eval-run execution (/v1/evals/.../runs) — eval-run-executor §3.

Reuses the eval-set-store surface's auth + OpenAI-wire envelope wholesale (``_authenticate``,
``_extract_raw_key``, ``_err``, ``_err_from_problem``, ``_unix`` from
``gateway.evals.api.router``) so the whole /v1/evals surface speaks ONE ``{"error": {...}}``
body, and tenant scope is enforced identically.

Endpoints:
  POST /v1/evals/sets/{set_id}/runs   {model} -> 201 { id:"er_..", eval_set_id, model, status,
                                       case_count, created_at }
      403 ERR_ZDR_PAYLOAD_BLOCKED (ZDR tenant, refused outright at launch, M5)
      404 ERR_EVAL_SET_NOT_FOUND  (absent OR cross-tenant set, M6)
      422 ERR_EVAL_RUN_INVALID    (missing/blank model, validated before the set is resolved)
  GET  /v1/evals/runs/{run_id}         -> 200 { id, eval_set_id, model, status, case_count,
                                       counts:{completed,refused,errored,pending}, created_at }
  GET  /v1/evals/runs/{run_id}/cases   -> 200 { object:"list", data:[{eval_case_id, status,
                                       response_text?, reason?, usage_record_id?}] }  (A5 order)

Durability (M7): launch enqueues onto ``app.state.eval_run_queue`` when present and returns a
``pending`` run fast; a background worker drives it. When the queue is absent or enqueue fails
(Redis down, or ASGITransport tests), it FAILS OPEN to an inline drive — the vector-store
ingest idiom — so a run is never dropped. The raw key is passed straight through to the inline
drive; it is never persisted (auth-scoped resume).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import EVAL_RUN_INVALID, EVAL_RUN_NOT_FOUND, EVAL_SET_NOT_FOUND
from gateway.core.errors import ProblemError

# The eval-set-store router owns the /v1/evals auth + OpenAI-wire envelope; reusing its helpers
# is what keeps the WHOLE surface speaking one body (a fork would drift). Same sanctioned reuse
# pattern as images/audio/embeddings use_cases importing use_cases._fire_record_with_raw.
from gateway.evals.api.router import (
    _authenticate,  # pyright: ignore[reportPrivateUsage]
    _err,  # pyright: ignore[reportPrivateUsage]
    _err_from_problem,  # pyright: ignore[reportPrivateUsage]
    _extract_raw_key,  # pyright: ignore[reportPrivateUsage]
    _unix,  # pyright: ignore[reportPrivateUsage]
)
from gateway.evals.infrastructure.repository import SqlAlchemyEvalStore
from gateway.evals.runs.infrastructure.orm import EvalCaseResultRow, EvalRunRow
from gateway.evals.runs.infrastructure.repository import SqlAlchemyEvalRunStore
from gateway.evals.wire_id import (
    parse_run_wire_id,
    parse_set_wire_id,
    to_case_wire_id,
    to_run_wire_id,
    to_set_wire_id,
)
from gateway.keys.domain.entities import AuthzResult

eval_runs_router = APIRouter(tags=["evals"])

_log = logging.getLogger(__name__)


def _run_store(request: Request) -> SqlAlchemyEvalRunStore:
    return SqlAlchemyEvalRunStore(request.app.state.sessionmaker)


def _run_object(row: EvalRunRow, *, case_count: int) -> dict[str, Any]:
    return {
        "id": to_run_wire_id(row.id),
        "object": "eval.run",
        "created_at": _unix(row.created_at),
        "eval_set_id": to_set_wire_id(row.eval_set_id),
        "model": row.model,
        "status": row.status,
        "case_count": case_count,
    }


def _case_result_object(row: EvalCaseResultRow) -> dict[str, Any]:
    """The per-case result wire object. response_text is present only for a `completed` case."""
    obj: dict[str, Any] = {
        "object": "eval.case_result",
        "eval_case_id": to_case_wire_id(row.eval_case_id),
        "status": row.status,
    }
    if row.response_text is not None:
        obj["response_text"] = row.response_text
    if row.reason is not None:
        obj["reason"] = row.reason
    if row.usage_record_id is not None:
        obj["usage_record_id"] = str(row.usage_record_id)
    return obj


@eval_runs_router.post(
    "/v1/evals/sets/{set_id}/runs", status_code=201, response_model=None
)
async def launch_eval_run(
    set_id: str,
    body: dict[str, Any],
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | JSONResponse:
    """Launch a run of a tenant's set against a model (M1). ZDR tenant refused outright (M5)."""
    # Validate the caller's own input FIRST — reveals nothing about whether the set exists.
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        return _err(EVAL_RUN_INVALID)

    resolved_set = parse_set_wire_id(set_id)
    if resolved_set is None:
        return _err(EVAL_SET_NOT_FOUND)
    # M6: resolve the parent set in tenant scope — absent/cross-tenant is a uniform 404.
    parent = await SqlAlchemyEvalStore(session).get_set(
        tenant_id=authz.tenant_id, eval_set_id=resolved_set
    )
    if parent is None:
        return _err(EVAL_SET_NOT_FOUND)

    executor = request.app.state.eval_run_executor
    raw_key = _extract_raw_key(request)
    try:
        run = await executor.launch(
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            raw_key=raw_key,
            eval_set_id=resolved_set,
            model=model,
        )
    except ProblemError as exc:
        # M5: the ZDR gate refused the run outright at launch. Re-render in this surface's
        # envelope (403 ERR_ZDR_PAYLOAD_BLOCKED); nothing was created.
        return _err_from_problem(exc)

    # Snapshot denominator (A2): cases that existed at launch time.
    store = _run_store(request)
    snapshot = await store.snapshot_cases(
        tenant_id=run.tenant_id, eval_set_id=run.eval_set_id, created_at_max=run.created_at
    )
    case_count = len(snapshot)

    await _enqueue_or_drive(request, executor, run_id=run.id, raw_key=raw_key)

    # Re-read so the returned status reflects an inline drive (fail-open / test) if it ran.
    refreshed = await store.get_run(tenant_id=run.tenant_id, run_id=run.id) or run
    return _run_object(refreshed, case_count=case_count)


async def _enqueue_or_drive(
    request: Request, executor: Any, *, run_id: Any, raw_key: str
) -> None:
    """Enqueue for the durable worker; FAIL OPEN to an inline drive (vector-store idiom, M7)."""
    queue = getattr(request.app.state, "eval_run_queue", None)
    if queue is not None:
        try:
            await queue.enqueue(run_id)
            return
        except Exception:
            _log.warning(
                "eval_run: enqueue failed for run %s, failing open to inline drive", run_id
            )
    await executor.drive(run_id, raw_key=raw_key)


@eval_runs_router.get("/v1/evals/runs/{run_id}", status_code=200, response_model=None)
async def get_eval_run(
    run_id: str,
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
) -> dict[str, Any] | JSONResponse:
    """A run's status + per-status rollup (M6-scoped; uniform 404 for absent/cross-tenant)."""
    resolved = parse_run_wire_id(run_id)
    if resolved is None:
        return _err(EVAL_RUN_NOT_FOUND)
    store = _run_store(request)
    run = await store.get_run(tenant_id=authz.tenant_id, run_id=resolved)
    if run is None:
        return _err(EVAL_RUN_NOT_FOUND)

    snapshot = await store.snapshot_cases(
        tenant_id=run.tenant_id, eval_set_id=run.eval_set_id, created_at_max=run.created_at
    )
    case_count = len(snapshot)
    counts = await store.counts_by_status(run.id)
    terminal = counts["completed"] + counts["refused"] + counts["errored"]
    counts["pending"] = max(0, case_count - terminal)

    obj = _run_object(run, case_count=case_count)
    obj["counts"] = counts
    return obj


@eval_runs_router.get("/v1/evals/runs/{run_id}/cases", status_code=200, response_model=None)
async def list_eval_run_cases(
    run_id: str,
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
) -> dict[str, Any] | JSONResponse:
    """A run's per-case results in the set's creation order (A5). Uniform 404 (M6)."""
    resolved = parse_run_wire_id(run_id)
    if resolved is None:
        return _err(EVAL_RUN_NOT_FOUND)
    store = _run_store(request)
    run = await store.get_run(tenant_id=authz.tenant_id, run_id=resolved)
    if run is None:
        return _err(EVAL_RUN_NOT_FOUND)
    results = await store.list_case_results(tenant_id=authz.tenant_id, run_id=resolved)
    return {"object": "list", "data": [_case_result_object(r) for r in results]}
