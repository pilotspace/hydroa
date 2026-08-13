"""EvalRunExecutor — replay a set through the governance path (eval-run-executor §3, M1-M8).

A run REPLAYS each case's stored ``request_body`` against the run's named model THROUGH the
existing ``CompletionUseCase.complete`` — never around it (M1). That single reuse makes the
security-critical invariants hold BY CONSTRUCTION:

  M1 governance  — complete() runs the SAME ordered guards a live request runs (auth · expiry ·
                   allowlist · catalog/model_checker · per-key/team/tenant budget · credit ·
                   tier · rate-limit). A run enters no back door.
  M2 billing     — complete() writes ONE usage_records row per dial, on the SERVED model id,
                   the SAME shape a live request produces. A refused case never dials (M3).
  M4 isolation   — the executor injects its OWN ``TenantBreakerUpstream`` into
                   complete(upstream=...): a per-tenant breaker + concurrency + per-call
                   timeout, so a run trips only THAT tenant's breaker, never the global one
                   the live path uses ([[per-tenant-breaker-recurring-defect]], R:GLOBAL_BREAKER).

The executor owns durability (M7): each case result is committed as it lands, and a resumed
drive skips cases that already have a terminal result row — no case is dialed or billed twice.

SECURITY — auth-scoped resume (2026-08-13): complete() authenticates a RAW key, which is NEVER
persisted at rest (a gateway key is high-value secret material). The launching request holds it
in memory for the run's lifetime (``_raw_keys``), so an in-process crash resumes via
recover_orphans. A cross-process (redeploy) resume must RE-SUPPLY the raw key via a fresh
authenticated request — ``drive(run_id, raw_key=...)``. Without it, pending cases stay pending
rather than being dialed under a forged identity.

ZDR (M5): a ZDR tenant's run is refused OUTRIGHT at launch (403), and the first payload write
re-checks ATOMICALLY (``raise_if_zdr_locked`` inside record_case_result) — a flip landing
mid-run persists nothing further and the run is marked ``blocked``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.errors import ProblemError
from gateway.evals.infrastructure.orm import EvalCaseRow
from gateway.evals.runs.domain.entities import CaseOutcome
from gateway.evals.runs.domain.ports import EvalRunStore
from gateway.evals.runs.infrastructure.orm import EvalRunRow
from gateway.evals.runs.infrastructure.upstream_adapter import (
    TenantBreakerUpstream,
    TenantExecutionRegistry,
)
from gateway.proxy.domain.ports import CompletionUpstream, UsageRecorder
from gateway.tenants.application.retention_policy import raise_if_zdr

_log = logging.getLogger(__name__)

# ProblemError codes that mean a DIAL WAS ATTEMPTED and failed (→ `errored`, fail-closed).
# Everything else complete() raises is a pre-dial governance refusal (→ `refused`, no dial).
_ERRORED_CODES = frozenset({"ERR_UPSTREAM_UNAVAILABLE", "ERR_UPSTREAM_RATE_LIMITED"})

#: A run's build_use_case seam: given a fresh session, return a live-wired CompletionUseCase
#: (main.py supplies the same get_completion_use_case the request path uses, over a shim).
UseCaseFactory = Callable[[AsyncSession], Any]


class EvalRunExecutor:
    """Launches + drives eval runs. One instance on app.state, shared by the router + worker."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        store: EvalRunStore,
        build_use_case: UseCaseFactory,
        get_upstream_delegate: Callable[[], CompletionUpstream],
        get_usage_recorder: Callable[[], UsageRecorder],
        get_model_router: Callable[[], Any],
        registry: TenantExecutionRegistry,
        per_call_timeout_seconds: float = 30.0,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._store = store
        self._build_use_case = build_use_case
        self._get_upstream_delegate = get_upstream_delegate
        self._get_usage_recorder = get_usage_recorder
        self._get_model_router = get_model_router
        self._registry = registry
        self._timeout = per_call_timeout_seconds
        # In-memory raw-key map — the ONLY place the launching secret lives (never at rest).
        self._raw_keys: dict[uuid.UUID, str] = {}

    async def launch(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        raw_key: str,
        eval_set_id: uuid.UUID,
        model: str,
    ) -> EvalRunRow:
        """Create a pending run for the tenant. Refuses a ZDR tenant OUTRIGHT at launch (M5)."""
        # M5: ZDR refuse outright at launch — the ENTRY check (the atomic locked re-check lives
        # at the first payload write). Raises ProblemError 403 ERR_ZDR_PAYLOAD_BLOCKED.
        async with self._sessionmaker() as session:
            await raise_if_zdr(session, tenant_id)
        run = await self._store.create_run(
            tenant_id=tenant_id, key_id=key_id, eval_set_id=eval_set_id, model=model
        )
        self._raw_keys[run.id] = raw_key
        return run

    async def drive(self, run_id: uuid.UUID, *, raw_key: str | None = None) -> None:
        """Drive (or resume) a run: dial every still-pending snapshot case, commit as each lands.

        Bounded per-tenant concurrency (A3): the tenant semaphore caps in-flight dials. A5's
        result ordering is enforced at READ time, so commit order does not matter. A ZDR flip
        mid-run (M5) aborts further persistence and marks the run blocked.
        """
        run = await self._store.load_run(run_id)
        if run is None:
            _log.warning("eval_run_executor: run %s not found (stale enqueue?), skipping", run_id)
            return
        key = raw_key or self._raw_keys.get(run_id)

        cases = await self._store.snapshot_cases(
            tenant_id=run.tenant_id,
            eval_set_id=run.eval_set_id,
            created_at_max=run.created_at,
        )
        existing = await self._store.existing_result_case_ids(run_id)
        pending = [c for c in cases if c.id not in existing]

        if pending and key is None:
            # Cross-process resume without a re-supplied key: we cannot re-enter governance under
            # the launching identity, and we will NOT forge one. Leave the cases pending for an
            # authenticated resume; never dial under a synthetic key.
            _log.info(
                "eval_run_executor: run %s has %d pending cases but no raw key in memory;"
                " awaiting an authenticated resume",
                run_id,
                len(pending),
            )
            return

        blocked = _BlockedFlag()
        semaphore = self._registry.semaphore_for(run.tenant_id)

        async def _one(case: EvalCaseRow) -> None:
            if blocked.is_set:
                return
            async with semaphore:  # A3: never more than `ceiling` dials in flight per tenant
                if blocked.is_set:
                    return
                outcome = await self._drive_case(run, case, key)
            try:
                await self._store.record_case_result(
                    tenant_id=run.tenant_id,
                    run_id=run_id,
                    eval_case_id=case.id,
                    outcome=outcome,
                )
            except ProblemError:
                # M5: the atomic locked ZDR re-check refused this payload write — a flip landed
                # mid-run. Persist nothing further; the run is blocked.
                blocked.set()

        await asyncio.gather(*(_one(c) for c in pending))

        if blocked.is_set:
            await self._store.set_run_status(run_id, "blocked")
            return
        await self._finalize_status(run_id, total=len(cases))

    async def resume(self, run_id: uuid.UUID, *, raw_key: str) -> None:
        """Authenticated cross-process resume: re-supply the raw key and drive pending cases."""
        await self.drive(run_id, raw_key=raw_key)

    async def _finalize_status(self, run_id: uuid.UUID, *, total: int) -> None:
        """Derive the run's terminal status from its cases (M7) — never speculative.

        completed when every snapshot case has a terminal result row (incl. the vacuous
        empty/all-refused run, A4). Otherwise it stays pending (a resume will finish it).
        """
        counts = await self._store.counts_by_status(run_id)
        terminal = counts["completed"] + counts["refused"] + counts["errored"]
        if terminal >= total:
            await self._store.set_run_status(run_id, "completed")

    async def _drive_case(
        self, run: EvalRunRow, case: EvalCaseRow, raw_key: str | None
    ) -> CaseOutcome:
        """Replay one case through complete(); map its outcome to a CaseOutcome (M1-M4)."""
        # M1: replay the stored request_body against the RUN's named model (override model).
        body = {**dict(case.request_body), "model": run.model}
        delegate = self._get_upstream_delegate()
        breaker = self._registry.breaker_for(run.tenant_id)
        upstream = TenantBreakerUpstream(breaker, delegate, timeout_seconds=self._timeout)
        try:
            async with self._sessionmaker() as session:
                use_case = self._build_use_case(session)
                status, response_body, _ = await use_case.complete(
                    raw_key=raw_key,
                    body=body,
                    upstream=upstream,
                    usage_recorder=self._get_usage_recorder(),
                    model_router=self._get_model_router(),
                )
        except ProblemError as exc:
            if exc.code in _ERRORED_CODES:
                # A dial was attempted and failed (breaker-open / timeout / upstream 5xx / 429)
                # → fail closed, the run continues (M4, E5).
                return CaseOutcome(status="errored", reason=exc.code)
            # A pre-dial governance refusal (budget/credit/tier/rate/allowlist/catalog/…): NO
            # dial, NO usage row for this case (M3, R:GOVERNANCE_BYPASS).
            return CaseOutcome(status="refused", reason=exc.code)
        except Exception:
            _log.exception("eval_run_executor: unexpected error driving case %s", case.id)
            return CaseOutcome(status="errored", reason="internal error")

        if status == 200 and isinstance(response_body, dict):
            return CaseOutcome(status="completed", response_text=_extract_text(response_body))
        # An upstream 4xx pass-through — the provider answered the caller got something wrong.
        return CaseOutcome(status="errored", reason=f"upstream returned {status}")


class _BlockedFlag:
    """A tiny shared abort flag for the ZDR mid-run stop (avoids a nonlocal in the closure)."""

    __slots__ = ("is_set",)

    def __init__(self) -> None:
        self.is_set = False

    def set(self) -> None:
        self.is_set = True


def _extract_text(response_body: dict[str, Any]) -> str:
    """Pull the assistant text (choices[0].message.content). '' if the shape is unexpected."""
    try:
        choices = response_body.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
    except Exception:
        return ""
    return ""


__all__ = ["EvalRunExecutor", "UseCaseFactory"]
