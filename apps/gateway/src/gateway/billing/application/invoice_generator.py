"""InvoiceGenerator — month-close generation job (invoice-generation TASK.md §3
— FROZEN @ v1).

CONTRACT:
  - generate_for_tenant(tenant_id, period_start) -> UUID | None: a pure
    aggregation (SUM(usage_records.cost_usd)) over the caller's tenant+period,
    grouped by (model_id, team_id, key_id, tags) — the canonical tag-SET
    dimension (M2, pre-freeze amendment). NEVER calls resolve_markup_pct or any
    other price-computing function (M3) — cost_usd is already the final,
    billed, point-in-time-resolved figure (recorder.py:record, line 248-253).
    Each line's amount_usd is ROUND_HALF_UP to cents AT GENERATION TIME (M4);
    invoice.total_usd is the SUM of the already-rounded amounts (rounded-then-
    summed, never summed-then-rounded). Idempotent + concurrency-safe via
    ON CONFLICT (tenant_id, period_start) DO NOTHING (M13, mirrors flusher.py's
    own idempotent-insert idiom) — a re-run or a genuine race resolves to
    exactly one row, silently, never a duplicate and never a crash. Bounded by
    asyncio.timeout per tenant.
  - sweep_once() / run_forever() / should_start_invoice_generator(): the
    conditionally-started background loop, mirroring RetentionSweeper's
    run_forever() + should_start_retention_sweep() shape (usage/application/
    retention_sweep.py) — the ONLY existing "conditionally-started background
    loop" pattern in this codebase. Generates the most recently completed
    eligible calendar month (period_end + stabilization_hours <= now) for every
    tenant, every tick — a pure no-op for any tenant/period already issued.

Auto-issue (§1 Assumption, Tin-confirmed 2026-07-12): generation only ever
runs AFTER the stabilization window has already elapsed, so every invoice this
task writes is inserted directly as status='issued' — there is no separate
draft->issued UPDATE transition in v1 (M5 immutability holds from the moment
of insert).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from collections.abc import Awaitable, Callable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.billing.infrastructure.orm import InvoiceLineRow, InvoiceRow
from gateway.core.ids import uuid7
from gateway.tenants.infrastructure.orm import TenantRow
from gateway.usage.infrastructure.orm import UsageRecordRow

_log = logging.getLogger(__name__)

_CENTS = Decimal("0.01")
_ZERO = Decimal("0")

DEFAULT_STABILIZATION_HOURS = 72
DEFAULT_PER_TENANT_TIMEOUT_SECONDS = 30.0


class _Settings(Protocol):
    """Minimal protocol for settings consumed by InvoiceGenerator."""

    invoice_generation_interval_seconds: int
    invoice_stabilization_hours: int


def should_start_invoice_generator(settings: _Settings) -> bool:
    """Default-ON guard: start the generator loop when interval > 0."""
    return settings.invoice_generation_interval_seconds > 0


def _as_naive_utc(dt: datetime.datetime) -> datetime.datetime:
    """Normalize to naive UTC — asyncpg binds the create_all TIMESTAMP columns as
    naive UTC datetimes; production's Alembic column is TIMESTAMPTZ (mirrors the
    `_as_naive_utc` idiom used throughout usage/application/reconciliation.py,
    retention_sweep.py, audit/api/router.py)."""
    if dt.tzinfo is not None:
        return dt.astimezone(datetime.UTC).replace(tzinfo=None)
    return dt


def _month_start(dt: datetime.datetime) -> datetime.datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(dt: datetime.datetime) -> datetime.datetime:
    dt = _month_start(dt)
    if dt.month == 12:
        return dt.replace(year=dt.year + 1, month=1)
    return dt.replace(month=dt.month + 1)


def _prev_month(dt: datetime.datetime) -> datetime.datetime:
    dt = _month_start(dt)
    if dt.month == 1:
        return dt.replace(year=dt.year - 1, month=12)
    return dt.replace(month=dt.month - 1)


def eligible_period_start(
    *, now: datetime.datetime, stabilization_hours: int
) -> datetime.datetime | None:
    """The most recently completed calendar month whose period_end +
    stabilization_hours <= now, or None if no month is eligible yet (M1, §1 ⚠)."""
    now = _as_naive_utc(now)
    current_month_start = _month_start(now)
    prior_month_start = _prev_month(current_month_start)
    prior_month_end = current_month_start
    if prior_month_end + datetime.timedelta(hours=stabilization_hours) <= now:
        return prior_month_start
    return None


def _round_half_up(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


class InvoiceGenerator:
    """Generates immutable monthly invoices from usage_records (M1-M4, M10, M12, M13)."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        per_tenant_timeout_seconds: float = DEFAULT_PER_TENANT_TIMEOUT_SECONDS,
        stabilization_hours: int = DEFAULT_STABILIZATION_HOURS,
    ) -> None:
        self._session_factory = session_factory
        self._per_tenant_timeout_seconds = per_tenant_timeout_seconds
        self._stabilization_hours = stabilization_hours

    async def generate_for_tenant(
        self, tenant_id: uuid.UUID, period_start: datetime.datetime
    ) -> uuid.UUID | None:
        """Generate (or idempotently skip) the invoice for tenant_id+period_start.

        Returns the new invoice id if THIS call created it, None if an invoice for
        that (tenant_id, period_start) already existed (sequential re-run) or a
        concurrently-racing caller won the ON CONFLICT insert (M13). Bounded by
        asyncio.timeout — a timeout propagates to the caller (sweep_once isolates
        per-tenant failures; a direct caller sees the TimeoutError).
        """
        async with asyncio.timeout(self._per_tenant_timeout_seconds):
            normalized_start = _as_naive_utc(_month_start(period_start))
            period_end = _next_month(normalized_start)

            async with self._session_factory() as session, session.begin():
                agg_stmt = (
                    select(
                        UsageRecordRow.model_id,
                        UsageRecordRow.team_id,
                        UsageRecordRow.key_id,
                        UsageRecordRow.tags,
                        func.sum(UsageRecordRow.cost_usd).label("raw_amount_usd"),
                        func.sum(UsageRecordRow.prompt_tokens).label("prompt_tokens"),
                        func.sum(UsageRecordRow.completion_tokens).label("completion_tokens"),
                        func.count().label("request_count"),
                    )
                    .where(
                        UsageRecordRow.tenant_id == tenant_id,
                        UsageRecordRow.created_at >= normalized_start,
                        UsageRecordRow.created_at < period_end,
                    )
                    .group_by(
                        UsageRecordRow.model_id,
                        UsageRecordRow.team_id,
                        UsageRecordRow.key_id,
                        UsageRecordRow.tags,
                    )
                )
                groups = (await session.execute(agg_stmt)).all()

                line_specs: list[dict[str, Any]] = []
                raw_total = _ZERO
                total_usd = _ZERO
                for grp in groups:
                    raw_amount = (
                        Decimal(str(grp.raw_amount_usd))
                        if grp.raw_amount_usd is not None
                        else _ZERO
                    )
                    amount = _round_half_up(raw_amount)
                    raw_total += raw_amount
                    total_usd += amount
                    line_specs.append(
                        {
                            "model_id": grp.model_id,
                            "team_id": grp.team_id,
                            "key_id": grp.key_id,
                            "tags": dict(grp.tags) if grp.tags else {},
                            "amount_usd": amount,
                            "raw_amount_usd": raw_amount,
                            "prompt_tokens": int(grp.prompt_tokens or 0),
                            "completion_tokens": int(grp.completion_tokens or 0),
                            "request_count": int(grp.request_count or 0),
                        }
                    )

                new_id = uuid7()
                now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
                insert_stmt = (
                    pg_insert(InvoiceRow)
                    .values(
                        id=new_id,
                        tenant_id=tenant_id,
                        period_start=normalized_start,
                        period_end=period_end,
                        status="issued",
                        currency="USD",
                        total_usd=total_usd,
                        raw_total_usd=raw_total,
                        tax_usd=_ZERO,
                        issued_at=now,
                        created_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["tenant_id", "period_start"])
                    .returning(InvoiceRow.id)
                )
                result = await session.execute(insert_stmt)
                inserted_id = result.scalar_one_or_none()
                if inserted_id is None:
                    # Either a prior sequential run already issued this period, or a
                    # concurrently-racing worker won the insert — silent no-op either
                    # way (M13). The transaction has nothing to commit; rolling back
                    # is harmless (no writes were made).
                    return None

                for spec in line_specs:
                    session.add(
                        InvoiceLineRow(
                            id=uuid7(),
                            invoice_id=inserted_id,
                            model_id=spec["model_id"],
                            team_id=spec["team_id"],
                            key_id=spec["key_id"],
                            tags=spec["tags"],
                            amount_usd=spec["amount_usd"],
                            raw_amount_usd=spec["raw_amount_usd"],
                            prompt_tokens=spec["prompt_tokens"],
                            completion_tokens=spec["completion_tokens"],
                            request_count=spec["request_count"],
                            line_type="usage",
                        )
                    )

                return inserted_id

    async def sweep_once(self, *, now: datetime.datetime | None = None) -> dict[str, Any]:
        """One sweep tick: generate the most recently completed eligible month for
        every tenant. NEVER raises — any per-tenant failure is logged and skipped
        (fail-open isolation, mirrors RetentionSweeper.sweep_once)."""
        now = now or datetime.datetime.now(datetime.UTC)
        period_start = eligible_period_start(now=now, stabilization_hours=self._stabilization_hours)
        if period_start is None:
            return {"period_start": None, "generated": 0, "skipped": 0}

        async with self._session_factory() as session:
            tenant_ids = (await session.execute(select(TenantRow.id))).scalars().all()

        generated = 0
        skipped = 0
        for tenant_id in tenant_ids:
            try:
                result = await self.generate_for_tenant(tenant_id, period_start)
                if result is not None:
                    generated += 1
                else:
                    skipped += 1
            except Exception as exc:
                _log.warning(
                    "invoice_generator: generate_for_tenant failed for tenant=%s (swallowed): %s",
                    tenant_id,
                    exc,
                    exc_info=exc,
                )
        return {
            "period_start": period_start.isoformat(),
            "generated": generated,
            "skipped": skipped,
        }

    async def run_forever(
        self,
        *,
        interval_seconds: float,
        _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Background loop: sweep_once() every interval_seconds. Swallows all
        non-CancelledError exceptions; propagates CancelledError for clean task
        cancellation from the lifespan shutdown (mirrors RetentionSweeper.run_forever)."""
        while True:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning(
                    "invoice_generator: background loop error (swallowed): %s", exc, exc_info=exc
                )
            await _sleep(interval_seconds)
