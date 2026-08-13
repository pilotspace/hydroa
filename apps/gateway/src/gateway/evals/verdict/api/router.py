"""FastAPI router for baseline pin + verdict (/v1/evals/…) — baseline-and-verdict §3.

Reuses the eval-set-store surface's auth + OpenAI-wire envelope wholesale (``_authenticate``,
``_err``, ``_unix`` from ``gateway.evals.api.router``) so the whole /v1/evals surface speaks ONE
``{"error": {...}}`` body and tenant scope is enforced identically.

Endpoints:
  PUT /v1/evals/sets/{set_id}/baseline  {run_id:"er_…"} -> 200 { eval_set_id, baseline_run_id,
                                        pinned_at }
      404 ERR_EVAL_SET_NOT_FOUND  (absent OR cross-tenant set, M5)
      404 ERR_EVAL_RUN_NOT_FOUND  (run absent, cross-tenant, or not in THIS set, M3)
  GET /v1/evals/runs/{run_id}/verdict -> 200 { run_id, score:{passed,total},
      baseline:{run_id, score:{passed,total}} | null, verdict:"pass"|"fail"|"no_baseline" }
      404 ERR_EVAL_RUN_NOT_FOUND  (absent OR cross-tenant run, M5)

A run's score is re-derived ON DEMAND (M1) — never read from a stored number (R:STALE_SCORE) —
by scoring each completed case through the PURE deterministic scorer. A missing baseline is the
explicit ``no_baseline`` state, never a silent pass (M4, R:SILENT_PASS_NO_BASELINE).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import EVAL_RUN_NOT_FOUND, EVAL_SET_NOT_FOUND

# The eval-set-store router owns the /v1/evals auth + OpenAI-wire envelope; reusing its helpers
# is what keeps the WHOLE surface speaking one body (a fork would drift). Same sanctioned reuse
# pattern as the eval-run-executor router.
from gateway.evals.api.router import (
    _authenticate,  # pyright: ignore[reportPrivateUsage]
    _err,  # pyright: ignore[reportPrivateUsage]
    _unix,  # pyright: ignore[reportPrivateUsage]
)
from gateway.evals.infrastructure.repository import SqlAlchemyEvalStore
from gateway.evals.runs.infrastructure.orm import EvalRunRow
from gateway.evals.runs.infrastructure.repository import SqlAlchemyEvalRunStore
from gateway.evals.scoring.scorers import DeterministicScorer
from gateway.evals.verdict.application.scoring import decide, score_run
from gateway.evals.verdict.domain.entities import CaseResultView, RunScore, ScorableCase
from gateway.evals.verdict.infrastructure.repository import SqlAlchemyEvalBaselineStore
from gateway.evals.wire_id import (
    parse_run_wire_id,
    parse_set_wire_id,
    to_run_wire_id,
    to_set_wire_id,
)
from gateway.keys.domain.entities import AuthzResult

eval_verdict_router = APIRouter(tags=["evals"])

# The scorer is PURE + stateless — one shared instance is safe and re-scorable (M1).
_SCORER = DeterministicScorer()


def _run_store(request: Request) -> SqlAlchemyEvalRunStore:
    return SqlAlchemyEvalRunStore(request.app.state.sessionmaker)


def _baseline_store(request: Request) -> SqlAlchemyEvalBaselineStore:
    return SqlAlchemyEvalBaselineStore(request.app.state.sessionmaker)


def _score_obj(score: RunScore) -> dict[str, int]:
    passed, total = score
    return {"passed": passed, "total": total}


async def _score_run_row(run: EvalRunRow, store: SqlAlchemyEvalRunStore) -> RunScore:
    """Re-derive a run's exact (pass,total) from its launch snapshot + per-case results (M1)."""
    snapshot = await store.snapshot_cases(
        tenant_id=run.tenant_id, eval_set_id=run.eval_set_id, created_at_max=run.created_at
    )
    results = await store.list_case_results(tenant_id=run.tenant_id, run_id=run.id)
    cases = [ScorableCase(id=c.id, assertion=c.assertion) for c in snapshot]
    by_case = {
        r.eval_case_id: CaseResultView(status=r.status, response_text=r.response_text)
        for r in results
    }
    return score_run(cases, by_case, _SCORER)


@eval_verdict_router.put("/v1/evals/sets/{set_id}/baseline", status_code=200, response_model=None)
async def pin_baseline(
    set_id: str,
    body: dict[str, Any],
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | JSONResponse:
    """Pin a run of this set as its baseline (M3). Uniform 404 for absent/cross-tenant (M5)."""
    resolved_set = parse_set_wire_id(set_id)
    if resolved_set is None:
        return _err(EVAL_SET_NOT_FOUND)
    # M5: resolve the parent set in tenant scope — absent/cross-tenant is a uniform 404.
    parent = await SqlAlchemyEvalStore(session).get_set(
        tenant_id=authz.tenant_id, eval_set_id=resolved_set
    )
    if parent is None:
        return _err(EVAL_SET_NOT_FOUND)

    raw_run = body.get("run_id")
    resolved_run = parse_run_wire_id(raw_run) if isinstance(raw_run, str) else None
    if resolved_run is None:
        return _err(EVAL_RUN_NOT_FOUND)
    run_store = _run_store(request)
    run = await run_store.get_run(tenant_id=authz.tenant_id, run_id=resolved_run)
    # M3: the run must exist, be this tenant's, AND belong to THIS set — else uniform 404.
    if run is None or run.eval_set_id != resolved_set:
        return _err(EVAL_RUN_NOT_FOUND)

    baseline = await _baseline_store(request).pin_baseline(
        tenant_id=authz.tenant_id, eval_set_id=resolved_set, run_id=resolved_run
    )
    return {
        "object": "eval.baseline",
        "eval_set_id": to_set_wire_id(resolved_set),
        "baseline_run_id": to_run_wire_id(baseline.run_id),
        "pinned_at": _unix(baseline.pinned_at),
    }


@eval_verdict_router.get("/v1/evals/runs/{run_id}/verdict", status_code=200, response_model=None)
async def get_verdict(
    run_id: str,
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
) -> dict[str, Any] | JSONResponse:
    """A candidate run's verdict vs its set's pinned baseline. Uniform 404 (M5)."""
    resolved = parse_run_wire_id(run_id)
    if resolved is None:
        return _err(EVAL_RUN_NOT_FOUND)
    run_store = _run_store(request)
    run = await run_store.get_run(tenant_id=authz.tenant_id, run_id=resolved)
    if run is None:
        return _err(EVAL_RUN_NOT_FOUND)

    candidate = await _score_run_row(run, run_store)

    baseline_pin = await _baseline_store(request).get_baseline(
        tenant_id=authz.tenant_id, eval_set_id=run.eval_set_id
    )
    body: dict[str, Any] = {
        "object": "eval.verdict",
        "run_id": to_run_wire_id(run.id),
        "score": _score_obj(candidate),
        "baseline": None,
        "verdict": "no_baseline",
    }
    if baseline_pin is None:
        # M4: a gate with no reference cannot render pass/fail — surface the absence, not green.
        return body

    baseline_run = await run_store.get_run(tenant_id=authz.tenant_id, run_id=baseline_pin.run_id)
    if baseline_run is None:
        # The pinned run is gone (FK CASCADE should prevent this) — degrade to no_baseline.
        return body

    baseline_score = await _score_run_row(baseline_run, run_store)
    body["baseline"] = {
        "run_id": to_run_wire_id(baseline_run.id),
        "score": _score_obj(baseline_score),
    }
    body["verdict"] = decide(candidate, baseline_score)
    return body
