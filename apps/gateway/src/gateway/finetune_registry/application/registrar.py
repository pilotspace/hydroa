"""FinetuneModelRegistrar — the finetune-model-registry listener wired at the FROZEN
extension point ``app.state.finetune_completion_listener`` (finetune-broker
``FinetuneCompletionListener`` protocol, PLAN.md §3).

Flow (M1/M2): a job's winning CAS to "succeeded" (finetune-broker D4, FROZEN — the
listener runs AFTER the broker's own commit) registers ``fine_tuned_model`` as a
tenant-owned ``models`` row + ONE ``pricing_snapshots`` row copied from the BASE
model's LATEST snapshot (provider-passthrough basis) x ``finetune_pricing_multiplier``
(Settings knob, default 1.0 -> byte-identical to a plain copy).

Design-for-failure: this registrar opens its OWN session per call — it never shares
the broker's request-scoped session (D4: a listener failure must never roll back the
already-committed CAS). Every write path is DB-only (no outbound IO, no timeout/
breaker needed). Exactly-once (M5) is enforced by the DB itself
(``ON CONFLICT (id) DO NOTHING``) — safe under concurrent/duplicate fire from ANY
caller (the broker's own listener invocation, a direct second call, or the repair
sweep), never by in-process state.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.ids import uuid7

_log = logging.getLogger(__name__)

_INSERT_MODEL_SQL = text(
    "INSERT INTO models (id, name, tenant_id, provider, modality, active, region, context_length)"
    " VALUES (:id, :name, :tenant_id, :provider, 'chat', true, :region, :context_length)"
    " ON CONFLICT (id) DO NOTHING RETURNING id"
)

_INSERT_SNAPSHOT_SQL = text(
    "INSERT INTO pricing_snapshots"
    " (id, model_id, prompt_usd_per_token, completion_usd_per_token, pricing_unit)"
    " VALUES (:id, :model_id, :prompt, :completion, 'per_token')"
)

_SELECT_BASE_MODEL_SQL = text("SELECT context_length, region FROM models WHERE id = :id")

_SELECT_LATEST_SNAPSHOT_SQL = text(
    "SELECT prompt_usd_per_token, completion_usd_per_token FROM pricing_snapshots"
    " WHERE model_id = :model_id ORDER BY captured_at DESC LIMIT 1"
)

_SELECT_MISSED_JOBS_SQL = text(
    "SELECT * FROM finetune_jobs WHERE status = 'succeeded' AND fine_tuned_model IS NOT NULL"
    " AND NOT EXISTS (SELECT 1 FROM models WHERE models.id = finetune_jobs.fine_tuned_model)"
)


def _field(job: Any, name: str) -> Any:
    """Read one field off either a mapping (raw-SQL row) or an ORM instance/attr-object."""
    if isinstance(job, Mapping):
        return job[name]
    return getattr(job, name)


class FinetuneModelRegistrar:
    """Registers a succeeded fine-tune job's model in the tenant catalog.

    ``pricing_multiplier`` defaults to Decimal("1.0") — byte-identical to a plain
    copy of the base model's latest snapshot (the ⚠ under-billing flag's config-flip
    escape hatch: ``Settings.finetune_pricing_multiplier`` wires the real value here,
    never a re-freeze).
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        pricing_multiplier: Decimal = Decimal("1.0"),
    ) -> None:
        self._session_factory = session_factory
        self._pricing_multiplier = pricing_multiplier

    async def on_succeeded(self, job: Any) -> None:
        """Implements ``FinetuneCompletionListener`` (finetune/domain/provider_port.py,
        FROZEN). Invoked EXACTLY ONCE by the broker on a winning CAS to "succeeded" —
        but is itself idempotent (M5), so a defensive double-fire is always safe."""
        async with self._session_factory() as session:
            await self._register_one(session, job)

    async def repair_missed(self) -> int:
        """M6: sweep succeeded jobs with ``fine_tuned_model`` set but no ``models`` row
        yet, and register each idempotently. DB-only (no breaker needed). Returns the
        count of models actually registered by THIS sweep (a job whose basis is still
        unresolvable is left for the next tick)."""
        async with self._session_factory() as session:
            rows = (await session.execute(_SELECT_MISSED_JOBS_SQL)).mappings().all()
            missed = list(rows)

        registered = 0
        for row in missed:
            async with self._session_factory() as session:
                if await self._register_one(session, row):
                    registered += 1
        return registered

    async def _register_one(self, session: AsyncSession, job: Any) -> bool:
        """Register one job's ft model. Returns True iff THIS call's insert won the
        exactly-once race (i.e. it actually created the models row + snapshot)."""
        job_id = _field(job, "id")
        fine_tuned_model = _field(job, "fine_tuned_model")
        if not fine_tuned_model:
            _log.info(
                "finetune_registry_model_id_missing",
                extra={"job_id": str(job_id)},
            )
            return False

        tenant_id = _field(job, "tenant_id")
        provider = _field(job, "provider")
        base_model = _field(job, "model")

        base = await self._resolve_base_pricing(session, base_model)
        if base is None:
            _log.info(
                "finetune_registry_pricing_unresolved",
                extra={"job_id": str(job_id), "base_model": base_model},
            )
            return False
        context_length, region, base_prompt, base_completion = base

        result = await session.execute(
            _INSERT_MODEL_SQL,
            {
                "id": fine_tuned_model,
                "name": fine_tuned_model,
                "tenant_id": str(tenant_id),
                "provider": provider,
                "region": region,
                "context_length": context_length,
            },
        )
        won = result.fetchone() is not None
        if won:
            await session.execute(
                _INSERT_SNAPSHOT_SQL,
                {
                    "id": str(uuid7()),
                    "model_id": fine_tuned_model,
                    "prompt": str(base_prompt * self._pricing_multiplier),
                    "completion": str(base_completion * self._pricing_multiplier),
                },
            )
        await session.commit()
        return won

    async def _resolve_base_pricing(
        self, session: AsyncSession, base_model: str
    ) -> tuple[int | None, str, Decimal, Decimal] | None:
        """Boundary (PLAN.md §3): try exact ``models.id == base_model`` first, then
        ``"openai/" + base_model`` (OpenRouter-prefixed catalog variant); both miss (or
        the resolved base has no snapshot yet) -> None (the pricing_unresolved defer
        path — never an unpriced callable model)."""
        for candidate in (base_model, f"openai/{base_model}"):
            model_row = (
                (await session.execute(_SELECT_BASE_MODEL_SQL, {"id": candidate}))
                .mappings()
                .first()
            )
            if model_row is None:
                continue
            snap = (
                (await session.execute(_SELECT_LATEST_SNAPSHOT_SQL, {"model_id": candidate}))
                .mappings()
                .first()
            )
            if snap is None:
                return None
            return (
                model_row["context_length"],
                model_row["region"],
                Decimal(str(snap["prompt_usd_per_token"])),
                Decimal(str(snap["completion_usd_per_token"])),
            )
        return None
