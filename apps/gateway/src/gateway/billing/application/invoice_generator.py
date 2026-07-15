"""InvoiceGenerator — month-close generation job (invoice-generation TASK.md §3
— FROZEN @ v1).

CONTRACT:
  - generate_for_tenant(tenant_id, period_start) -> UUID | None: a pure
    aggregation (SUM(usage_records.cost_usd)) over the caller's tenant+period,
    grouped by (model_id, team_id, key_id, tags) — the canonical tag-SET
    dimension (M2, pre-freeze amendment). NEVER calls resolve_markup_pct or any
    other price-computing function (M3) — cost_usd is already the final,
    billed, point-in-time-resolved figure (recorder.py:record, line 248-253).
    Each line's amount_usd is ROUND_HALF_UP to cents AT GENERATION TIME (M4) for
    line-level display. invoice.total_usd (audit-remediation C4 fix, 2026-07-14 —
    supersedes the original M4 "rounded-then-summed" text below) is the FULL raw_total
    rounded EXACTLY ONCE — summing already-per-group-rounded amounts instead let
    high-cardinality (model,team,key,tags) fragmentation round many real sub-cent
    groups independently to $0.00 each, silently erasing revenue that was never
    actually zero in aggregate (HIGH/MED audit finding C4). Any (at most a few
    cents) gap between that true total and the naive per-group-rounded sum is
    reconciled onto exactly ONE usage line (the single largest raw contributor,
    deterministic tie-break) so invoice.total_usd still always equals the sum of
    its own persisted invoice_lines — see _reconcile_rounding_delta.
    Double-bill couple (audit-remediation, 2026-07-15 — supersedes the original
    billing_mode-gated C3 fix): generate_for_tenant SKIPS (returns None, writes
    nothing) iff real-time credit enforcement is ACTIVE — self._credits_gate_enabled,
    wired from the SAME settings.credits_gate_enabled knob that decides whether
    main.py installs the real PostgresCreditGuard (holds+settles every tenant) vs a
    Passthrough. One source of truth: invoice-skip and credit-holds can never diverge
    (no double-bill when the knob is on, no revenue leak when it is off). The prior
    tenants.billing_mode gate was unsafe both ways and no code path ever set it to
    'credits'; the column is retained but no longer drives billing.
    Idempotent + concurrency-safe via
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

from gateway.billing.application.seat_pricer import compute_seat_lines
from gateway.billing.infrastructure.invoice_repository import load_tenant_seat_membership
from gateway.billing.infrastructure.orm import InvoiceLineRow, InvoiceRow
from gateway.core.ids import uuid7
from gateway.tenants.infrastructure.orm import PlanRow, TenantRow
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


def _line_tie_break_key(spec: dict[str, Any]) -> tuple[Any, ...]:
    """Deterministic sort key for a usage line_spec — same inputs always resolve to
    the SAME line regardless of SQL GROUP BY return order (mirrors this module's own
    M13 idempotency doctrine: a re-run or a replay across two identically-seeded
    tenants must pick byte-identical output)."""
    return (
        str(spec["model_id"]),
        str(spec["team_id"]),
        str(spec["key_id"]),
        tuple(sorted(spec["tags"].items())),
    )


def _reconcile_rounding_delta(line_specs: list[dict[str, Any]], delta: Decimal) -> None:
    """audit-remediation C4 fix: apply `delta` (total_usd minus the naive per-group-
    rounded sum) across usage line amount_usd values so that invoice.total_usd
    always equals the sum of its own persisted invoice_lines (preserves the M9
    "PDF, CSV, and API total always agree" invariant) while total_usd itself is
    still the TRUE, revenue-preserving raw_total rounded once (never eroded by
    high-cardinality tag/team/key fragmentation, C4).

    delta > 0 (the common C4 case — many sub-cent groups rounded down to $0.00,
    erasing real revenue): the WHOLE delta lands on the single largest raw
    contributor (deterministic tie-break via `_line_tie_break_key`) — adding
    money to a line can never make it invalid.

    delta < 0 (naive per-group rounding overcounted, e.g. many boundary values
    that each independently rounded UP): walk lines largest-raw-first and remove
    at most each line's own amount_usd, moving to the next line if one is
    exhausted — a defensive largest-remainder-style distribution that GUARANTEES
    no line's amount_usd is ever driven negative, however many groups fragment
    the total (a negative invoice line would itself be a new correctness bug).

    Deliberately NEVER touches a seat/proration line: seat-billing TASK.md §3 is
    FROZEN @ v2 and independently asserts its OWN per-line
    `amount_usd == round_half_up(raw_amount_usd)` invariant
    (tests/seat_billing/test_seat_pricing.py) — folding a usage-side rounding
    remainder onto a seat line would violate that separate, out-of-package
    contract. A no-op when there is no usage line to absorb the delta (rare —
    e.g. a seat-only month with a fractional proration remainder); see
    invoice_generator's module docstring / this task's report for the residual-risk
    note on that narrow edge.
    """
    if delta == _ZERO or not line_specs:
        return
    ordered = sorted(
        line_specs, key=lambda s: (s["raw_amount_usd"], _line_tie_break_key(s)), reverse=True
    )
    if delta > _ZERO:
        ordered[0]["amount_usd"] += delta
        return
    remaining = -delta
    for spec in ordered:
        if remaining <= _ZERO:
            break
        take = min(spec["amount_usd"], remaining)
        spec["amount_usd"] -= take
        remaining -= take
    # remaining > 0 here only if EVERY line's amount_usd was already driven to
    # $0.00 and there is still a residual to remove — a pathological all-lines-
    # near-zero month; total_usd itself is unaffected (still the correct
    # round_half_up(raw_total)), only the rare cosmetic sum-of-lines==total_usd
    # display invariant can fall short by that residual in this extreme edge.


async def _load_seat_price(session: AsyncSession, tenant_id: uuid.UUID) -> Decimal | None:
    """seat-billing TASK.md §3 (FROZEN @ v2, M2/M7) — ONE extra SELECT (mirrors
    RedisBudgetGuard's own "one query" idiom, NOT the shared PlanEntitlementResolver, per
    §1 Framings weighed). Returns None for an unplanned tenant (no matching row, INNER
    JOIN excludes a NULL plan_id) OR a plan whose seat_price_usd_monthly is NULL/0 — both
    collapse to the SAME inert no-op (M2): this task writes ZERO seat/proration lines."""
    price = (
        await session.execute(
            select(PlanRow.seat_price_usd_monthly)
            .select_from(TenantRow)
            .join(PlanRow, PlanRow.id == TenantRow.plan_id)
            .where(TenantRow.id == tenant_id)
        )
    ).scalar_one_or_none()
    if price is None:
        return None
    decimal_price = Decimal(str(price))
    if decimal_price == _ZERO:
        return None
    return decimal_price


class InvoiceGenerator:
    """Generates immutable monthly invoices from usage_records (M1-M4, M10, M12, M13)."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        per_tenant_timeout_seconds: float = DEFAULT_PER_TENANT_TIMEOUT_SECONDS,
        stabilization_hours: int = DEFAULT_STABILIZATION_HOURS,
        credits_gate_enabled: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._per_tenant_timeout_seconds = per_tenant_timeout_seconds
        self._stabilization_hours = stabilization_hours
        # audit-remediation (double-bill couple): the SAME central knob
        # (settings.credits_gate_enabled) that wires the real-time PostgresCreditGuard.
        # True → every tenant is held+settled in real time, so a monthly invoice would
        # double-bill → skip. One source of truth; invoice-skip and credit-holds cannot
        # diverge. Default False = byte-identical to pre-credits-ledger behavior.
        self._credits_gate_enabled = credits_gate_enabled

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

            # audit-remediation (double-bill couple): skip iff real-time credit
            # enforcement is ACTIVE — the SAME predicate (settings.credits_gate_enabled)
            # that wires PostgresCreditGuard to hold+settle every tenant. Coupling both
            # invoice-skip and credit-holds to this one flag means they can never diverge:
            # no double-bill when the flag is on, and no revenue leak when it is off (the
            # prior tenants.billing_mode gate was never set to 'credits' by any code path,
            # and would have leaked a mode='credits' tenant with the flag off). Checked
            # before any query — a no-op for every tenant while the flag is off (default).
            if self._credits_gate_enabled:
                return None

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
                # audit-remediation C4 fix: `rounded_sum` is the OLD (buggy)
                # rounded-then-summed figure — kept ONLY to compute the
                # reconciliation delta below, never written as total_usd directly
                # (see _reconcile_rounding_delta / module docstring).
                rounded_sum = _ZERO
                for grp in groups:
                    raw_amount = (
                        Decimal(str(grp.raw_amount_usd))
                        if grp.raw_amount_usd is not None
                        else _ZERO
                    )
                    amount = _round_half_up(raw_amount)
                    raw_total += raw_amount
                    rounded_sum += amount
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

                # seat-billing TASK.md §3 (FROZEN @ v2, M7): additively fold seat/
                # proration lines into this SAME transaction, BEFORE the
                # `INSERT ... RETURNING id` — an issued invoice is immutable the instant
                # it is inserted (M5), so seat pricing MUST be computed and folded into
                # raw_total/total_usd here, never after. Inert (seat_price is None) for
                # an unplanned tenant or a plan with no/zero seat price (M2) — this block
                # is then a pure no-op, byte-identical to before this task shipped.
                seat_price = await _load_seat_price(session, tenant_id)
                seat_line_specs: list[Any] = []
                if seat_price is not None:
                    memberships = await load_tenant_seat_membership(session, tenant_id=tenant_id)
                    seat_line_specs = compute_seat_lines(
                        seat_price_usd_monthly=seat_price,
                        users=memberships,
                        period_start=normalized_start,
                        period_end=period_end,
                    )
                    for seat_spec in seat_line_specs:
                        raw_total += seat_spec.raw_amount_usd
                        rounded_sum += seat_spec.amount_usd

                # audit-remediation C4 fix: total_usd is the TRUE, revenue-preserving
                # figure — the full raw_total (every usage group + every seat/
                # proration line) rounded EXACTLY ONCE, never derived by summing
                # already-per-group-rounded amounts (that path lets high-cardinality
                # tag/team/key fragmentation round many real sub-cent groups
                # independently to $0.00 each, silently erasing revenue). Reconcile
                # the gap between this true total and the naive `rounded_sum` onto
                # exactly one usage line so total_usd still equals the sum of its own
                # persisted invoice_lines (M9 invariant preserved) — see
                # _reconcile_rounding_delta's own docstring for why seat/proration
                # lines are never touched here.
                total_usd = _round_half_up(raw_total)
                _reconcile_rounding_delta(line_specs, total_usd - rounded_sum)

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

                # seat-billing TASK.md §3 (FROZEN @ v2, M9): 'seat'/'proration' rows
                # reinterpret 3 existing NOT-NULL columns (model_id='seat' sentinel,
                # team_id=NULL always, key_id=NIL-UUID for the aggregate / the seat's own
                # user_id for a proration line) — schema unchanged, per-line_type
                # documented reinterpretation only.
                for seat_spec in seat_line_specs:
                    session.add(
                        InvoiceLineRow(
                            id=uuid7(),
                            invoice_id=inserted_id,
                            model_id="seat",
                            team_id=None,
                            key_id=seat_spec.key_id,
                            tags={},
                            amount_usd=seat_spec.amount_usd,
                            raw_amount_usd=seat_spec.raw_amount_usd,
                            prompt_tokens=0,
                            completion_tokens=0,
                            request_count=seat_spec.request_count,
                            line_type=seat_spec.line_type,
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
