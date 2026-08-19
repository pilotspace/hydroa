"""FinetuneBrokerService — the brokering flow (finetune-broker PLAN.md §3, FROZEN @ v1).

Flow (M1/M2): authenticate (router) -> validate training_file AND (when supplied)
validation_file through the ONE shared check (T2: byte-identical uniform 422 on any
miss of either field) -> derive provider server-side from model (never a client
field) -> resolve credential via the ONE shared resolver seam (fail-closed 402,
platform fallback NEVER consulted) -> persist row -> submit inline (bounded, no
retry — non-idempotent).

Refresh-on-read (M7): GET on a non-terminal job with a provider_job_id polls the
provider; ANY inability to poll (provider failure OR credential-resolution failure,
e.g. the tenant rotated/deleted its key) serves the last-known LOCAL state —
stale-ok, never a 5xx/402 on a read. Terminal transitions commit via CAS; the
completion listener fires EXACTLY ONCE, only on the winning CAS to "succeeded".

Retry/timeout ownership: the PORT (OpenAIFinetuneClient) owns the bounded-timeout /
retry policy per operation (submit=1 attempt, poll<=2 retries, cancel=1 retry) — this
service calls each port method exactly once and reacts to success/failure, so the
retry count is never duplicated between layers.

HEAL (2026-07-24, dual security refute-read — 4 defects):
  D1 every ``self._provider_port.submit/poll/cancel`` call now passes ``tenant_id``
     FIRST so the port can key its outbound-IO circuit breaker per tenant (mirrors
     the moderations CR-1 heal) — one tenant's provider outage can no longer 502
     every other tenant.
  D2 credential resolution now catches ``ProviderCredentialError`` (a corrupted
     stored credential / Fernet decrypt failure) alongside ``ProviderKeyMissing`` at
     all 3 entry points: create/cancel -> contracted 402 (never an uncaught 500);
     get -> stale-ok (never a 5xx read, M7).
  D4 a winning terminal CAS transition in ``get_job`` is committed BEFORE the
     completion listener is invoked, and any listener exception is caught+logged —
     never rolled back, never re-fired (the CAS itself is still exactly-once).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from gateway.core.error_catalog import (
    FINETUNE_JOB_NOT_CANCELLABLE,
    FINETUNE_MODEL_UNSUPPORTED,
    FINETUNE_PROVIDER_UNREACHABLE,
    FINETUNE_TRAINING_FILE_INVALID,
)
from gateway.core.errors import ProblemError
from gateway.files.infrastructure.repository import FileRepository
from gateway.files.wire_id import parse_wire_id
from gateway.finetune.domain.provider_port import (
    TERMINAL_STATUSES,
    fold_provider_status,
    is_valid_provider_job_id,
    resolve_finetune_provider,
    unwrap_credential_secret,
)
from gateway.finetune.infrastructure.orm import FinetuneJobEventRow, FinetuneJobRow
from gateway.finetune.infrastructure.repository import FinetuneJobRepository
from gateway.finetune.wire_id import parse_job_wire_id
from gateway.proxy.domain.provider_credentials import ProviderCredentialError, ProviderKeyMissing
from gateway.tenants.application.retention_policy import raise_if_zdr, raise_if_zdr_locked

_log = logging.getLogger(__name__)

_FINETUNE_PURPOSE = "fine-tune"


class FinetuneBrokerService:
    """The finetune-broker brokering flow, scoped to one request's session."""

    def __init__(
        self,
        *,
        repo: FinetuneJobRepository,
        files_repo: FileRepository,
        credential_resolver: Any,
        provider_port: Any,
        completion_listener: Any | None,
    ) -> None:
        self._repo = repo
        self._files_repo = files_repo
        self._credential_resolver = credential_resolver
        self._provider_port = provider_port
        self._completion_listener = completion_listener

    async def _validate_file_ref(self, *, tenant_id: uuid.UUID, wire_id: str) -> uuid.UUID:
        """T2: ONE shared check for training_file AND validation_file — absent,
        malformed, cross-tenant, or wrong-purpose all fold to the SAME uniform 422
        (no enumeration oracle)."""
        parsed = parse_wire_id(wire_id)
        if parsed is None:
            raise FINETUNE_TRAINING_FILE_INVALID.exc()
        file_row = await self._files_repo.get_active(tenant_id=tenant_id, file_id=parsed)
        if file_row is None or file_row.purpose != _FINETUNE_PURPOSE:
            raise FINETUNE_TRAINING_FILE_INVALID.exc()
        return parsed

    async def create_job(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        training_file: str,
        validation_file: str | None,
        suffix: str | None,
        hyperparameters: dict[str, Any] | None,
    ) -> FinetuneJobRow:
        # ZDR ENTRY GATE (zdr-retention-inventory-extension M4/A10) — the FIRST statement,
        # before any file IO, any credential resolution, any row insert and above all any
        # provider call: a finetune job persists the tenant's hyperparameters, suffix and
        # provider error text, so a ZDR tenant must never buy a provider round-trip it is
        # not allowed to keep the result of. Plain (non-locking) read, mirroring the evals
        # launch gate (evals/runs/application/run_executor.py:104) — it is the cheap
        # already-ZDR refusal, NOT the atomicity guarantee. That is the locked re-check at
        # the bottom of this method: the two are a pair, and neither alone is the contract.
        await raise_if_zdr(self._repo.session, tenant_id)

        # Derive provider server-side BEFORE any file/credential IO — never a client field.
        provider = resolve_finetune_provider(model)
        if provider is None:
            raise FINETUNE_MODEL_UNSUPPORTED.exc()

        # T2: validate BOTH file references through the ONE shared check, in order,
        # BEFORE any credential resolution or outbound IO.
        training_file_id = await self._validate_file_ref(tenant_id=tenant_id, wire_id=training_file)
        validation_file_id: uuid.UUID | None = None
        if validation_file is not None:
            validation_file_id = await self._validate_file_ref(
                tenant_id=tenant_id, wire_id=validation_file
            )

        # M2 fail-closed: own-key-or-402. Platform fallback is NEVER consulted here —
        # no row is created and ZERO outbound IO occurs on a miss. D2 heal: a
        # corrupted stored credential (ProviderCredentialError, e.g. Fernet decrypt
        # failure) is ALSO fail-closed 402 here — previously uncaught -> 500. The
        # static detail string never includes the exception (no secret in it).
        try:
            credential = await self._credential_resolver.resolve(tenant_id, provider)
        except (ProviderKeyMissing, ProviderCredentialError) as exc:
            raise ProblemError(
                402, exc.code, "Provider key not configured for this tenant"
            ) from None

        row = await self._repo.create(
            tenant_id=tenant_id,
            key_id=key_id,
            provider=provider,
            model=model,
            training_file_id=training_file_id,
            validation_file_id=validation_file_id,
            suffix=suffix,
            hyperparameters=hyperparameters,
        )
        await self._repo.add_event(
            job_id=row.id, tenant_id=tenant_id, level="info", message="created"
        )

        # M1: the row is persisted FIRST (above); submit is inline, ONE attempt (the
        # port owns the no-retry policy — a retried create risks a double submit).
        request_payload: dict[str, Any] = {
            "model": model,
            "training_file": training_file,
            "validation_file": validation_file,
            "suffix": suffix,
            "hyperparameters": hyperparameters,
        }
        try:
            provider_job_id = await self._provider_port.submit(
                tenant_id, unwrap_credential_secret(credential), request_payload
            )
            if not is_valid_provider_job_id(provider_job_id):
                raise ValueError(f"malformed provider_job_id shape: {provider_job_id!r}")
        except Exception as exc:
            error_message = f"provider unreachable at submit: {exc}"
            await self._repo.set_submit_result(
                job_id=row.id, provider_job_id=None, status="failed", error=error_message
            )
            await self._repo.add_event(
                job_id=row.id,
                tenant_id=tenant_id,
                level="error",
                message="finetune_provider_unreachable",
                data={"error": str(exc)},
            )
            row.status = "failed"
            row.error = error_message
        else:
            await self._repo.set_submit_result(
                job_id=row.id, provider_job_id=provider_job_id, status="queued", error=None
            )
            await self._repo.add_event(
                job_id=row.id, tenant_id=tenant_id, level="info", message="submitted"
            )
            row.status = "queued"
            row.provider_job_id = provider_job_id

        # ZDR RE-CHECK, ATOMIC WITH THE COMMIT (zdr-retention-inventory-extension M4/A9;
        # [[zdr-toctou-async-write-paths]], HARD-STOPPED twice on this codebase).
        #
        # The entry gate above decided on a value read BEFORE the provider await; a flip
        # landing inside that await would otherwise persist finetune_jobs +
        # finetune_job_events rows at rest for a tenant who is ZDR by the time they
        # commit. `raise_if_zdr_locked` (SELECT ... FOR UPDATE) makes the decision and the
        # write atomic: a concurrent flip for this tenant either commits before this read
        # (we block on its row lock, then read true, then refuse — nothing was ever
        # committed, get_session's close rolls the whole transaction back) or lands after
        # our commit (rows written, then removed by the sweeper's ZDR purge pass, which as
        # of this task actually covers both finetune tables). It can never land between.
        #
        # PLACEMENT IS THE CONTRACT, both halves of it:
        #   * LAST statement before the router's `await session.commit()`
        #     (finetune/api/router.py) — every insert this method makes is already
        #     flushed into this transaction, so refusing here discards all of them.
        #   * AFTER the provider await returns, never at the insert — a lock taken before
        #     `submit` would be HELD ACROSS AN OUTBOUND ROUND-TRIP, turning one slow
        #     provider into a per-tenant head-of-line stall on every concurrent create.
        #     Trading a TOCTOU for an availability defect is not a fix.
        #
        # Both submit outcomes pass through here: the failure branch persists rows too
        # (status=failed carries the provider's error text), so it is gated identically.
        try:
            await raise_if_zdr_locked(self._repo.session, tenant_id)
        except Exception:
            # The refusal is correct and stays — but when submit already SUCCEEDED, the
            # rollback erases our only record of a job that is now live upstream, burning
            # the tenant's BYOK spend where nobody can poll or cancel it. Name it before
            # the transaction dies, or it is unrecoverable. Log-only: never swallow.
            # `row.provider_job_id`, not the local: on the submit-failure branch the
            # local is never bound, and there is no upstream job to orphan anyway.
            if row.provider_job_id is not None:
                _log.warning(
                    "finetune_job_orphaned_by_zdr_refusal",
                    extra={
                        "tenant_id": str(tenant_id),
                        "provider": provider,
                        "provider_job_id": row.provider_job_id,
                    },
                )
            raise
        return row

    async def get_job(self, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> FinetuneJobRow | None:
        row = await self._repo.get(tenant_id=tenant_id, job_id=job_id)
        if row is None:
            return None
        if row.status in TERMINAL_STATUSES or row.provider_job_id is None:
            # Terminal jobs are never re-polled or re-fired (M7); a never-submitted
            # job has nothing to refresh against.
            return row

        # Refresh-on-read: ANY inability to poll — credential-resolution failure
        # (ProviderKeyMissing, OR a corrupted stored credential -> D2 heal:
        # ProviderCredentialError) OR provider failure — serves the last-known
        # LOCAL state (stale-ok, M7). Never a 5xx/402 on a read.
        try:
            credential = await self._credential_resolver.resolve(tenant_id, row.provider)
        except (ProviderKeyMissing, ProviderCredentialError):
            return row
        try:
            poll_result = await self._provider_port.poll(
                tenant_id, unwrap_credential_secret(credential), row.provider_job_id
            )
        except Exception:
            return row

        new_status = fold_provider_status(str(poll_result.get("status")))
        fine_tuned_model = poll_result.get("fine_tuned_model")
        if new_status == row.status:
            return row
        if new_status not in TERMINAL_STATUSES:
            # "Terminal transitions commit via CAS" (PLAN.md §3 M7) — an intermediate
            # non-terminal provider status (e.g. queued -> running) is NOT persisted;
            # only a transition INTO a terminal status is ever written. This also
            # keeps a job's status stable across an unrelated failed write (e.g. a
            # failed cancel) followed by a plain read.
            return row

        transitioned = await self._repo.apply_poll_result(
            job_id=row.id,
            tenant_id=tenant_id,
            new_status=new_status,
            fine_tuned_model=fine_tuned_model,
        )
        if not transitioned:
            # Lost a concurrent race to another reader/writer — re-fetch, don't refire.
            refreshed = await self._repo.get(tenant_id=tenant_id, job_id=job_id)
            return refreshed if refreshed is not None else row

        refreshed = await self._repo.get(tenant_id=tenant_id, job_id=job_id)
        assert refreshed is not None
        if new_status == "succeeded":
            await self._repo.add_event(
                job_id=refreshed.id, tenant_id=tenant_id, level="info", message="succeeded"
            )

        # D4 heal: commit the terminal CAS transition (+ the "succeeded" event, if
        # any) BEFORE invoking the completion listener. A throwing listener must
        # never roll back an already-won, already-decided terminal state — without
        # this, an exception here would propagate through the router and the
        # request's rollback would undo the CAS too, leaving the job stuck stale
        # forever (every subsequent GET would keep re-polling the real provider).
        await self._repo.commit()

        if new_status == "succeeded" and self._completion_listener is not None:
            try:
                await self._completion_listener.on_succeeded(refreshed)
            except Exception:
                _log.exception(
                    "finetune_completion_listener_failed",
                    extra={"job_id": str(refreshed.id)},
                )
        return refreshed

    async def list_jobs(
        self, *, tenant_id: uuid.UUID, limit: int, after: str | None
    ) -> tuple[list[FinetuneJobRow], bool]:
        after_uuid = parse_job_wire_id(after) if after else None
        return await self._repo.list_for_tenant(tenant_id=tenant_id, limit=limit, after=after_uuid)

    async def cancel_job(self, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> FinetuneJobRow | None:
        row = await self._repo.get(tenant_id=tenant_id, job_id=job_id)
        if row is None:
            return None
        if row.status in TERMINAL_STATUSES:
            raise FINETUNE_JOB_NOT_CANCELLABLE.exc()

        if row.provider_job_id is not None:
            # A submitted job cancels at the provider first (status stays unchanged
            # on failure so cancel remains retryable — R:provider_unreachable).
            try:
                credential = await self._credential_resolver.resolve(tenant_id, row.provider)
            except (ProviderKeyMissing, ProviderCredentialError) as exc:
                # D2 heal: a corrupted stored credential is ALSO fail-closed 402 here
                # (previously uncaught -> 500).
                raise ProblemError(
                    402, exc.code, "Provider key not configured for this tenant"
                ) from None
            try:
                await self._provider_port.cancel(
                    tenant_id, unwrap_credential_secret(credential), row.provider_job_id
                )
            except Exception:
                raise FINETUNE_PROVIDER_UNREACHABLE.exc() from None
        # A never-submitted job cancels locally with no outbound call (M4).

        transitioned = await self._repo.cancel(job_id=row.id, tenant_id=tenant_id)
        if not transitioned:
            # Raced to terminal between the check above and the CAS (e.g. a
            # concurrent refresh-on-read observed "succeeded" first).
            raise FINETUNE_JOB_NOT_CANCELLABLE.exc()

        await self._repo.add_event(
            job_id=row.id, tenant_id=tenant_id, level="info", message="cancelled"
        )
        refreshed = await self._repo.get(tenant_id=tenant_id, job_id=job_id)
        assert refreshed is not None
        return refreshed

    async def list_events(
        self, *, tenant_id: uuid.UUID, job_id: uuid.UUID
    ) -> list[FinetuneJobEventRow] | None:
        row = await self._repo.get(tenant_id=tenant_id, job_id=job_id)
        if row is None:
            return None
        return await self._repo.list_events(tenant_id=tenant_id, job_id=job_id)
