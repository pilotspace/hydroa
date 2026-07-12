"""RED suite — InvoiceGenerator (invoice-generation TASK.md §3 — FROZEN @ v1).

Covers §2 scenarios that exercise generation directly (M1-M4 grouping/rounding,
M10 empty-month, M12 mid-month markup blend, M13 idempotent re-run + concurrency,
M14 seat-billing extension point). RED before BUILD: gateway.billing.application.
invoice_generator does not exist yet, so every import fails — the honest
missing-implementation red.

DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    AUGUST_START,
    JULY_START,
    make_generator,
    seed_usage_record,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


async def _fetch_invoice_row(db_session: AsyncSession, invoice_id: Any) -> Any:
    result = await db_session.execute(
        text("SELECT * FROM invoices WHERE id = :id"), {"id": str(invoice_id)}
    )
    return result.mappings().one()


async def _fetch_lines(db_session: AsyncSession, invoice_id: Any) -> list[Any]:
    result = await db_session.execute(
        text("SELECT * FROM invoice_lines WHERE invoice_id = :id ORDER BY amount_usd"),
        {"id": str(invoice_id)},
    )
    return list(result.mappings().all())


# ---------------------------------------------------------------------------
# M1 — calendar-month bucketing
# ---------------------------------------------------------------------------


async def test_generation_buckets_usage_into_calendar_month_lines(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Calendar Co", email="cal@inv.io")
    excluded = await seed_usage_record(
        db_session,
        tenant_id=tid,
        cost_usd="5.00",
        created_at=datetime.datetime(2026, 6, 30, 23, 59, 59, tzinfo=datetime.UTC),
    )
    included = await seed_usage_record(
        db_session,
        tenant_id=tid,
        cost_usd="7.00",
        created_at=datetime.datetime(2026, 7, 1, 0, 0, 1, tzinfo=datetime.UTC),
    )

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    assert invoice_id is not None

    row = await _fetch_invoice_row(db_session, invoice_id)
    assert row["period_start"] == datetime.datetime(2026, 7, 1)
    assert row["period_end"] == datetime.datetime(2026, 8, 1)
    assert Decimal(str(row["total_usd"])) == Decimal("7.00")

    lines = await _fetch_lines(db_session, invoice_id)
    assert len(lines) == 1
    assert Decimal(str(lines[0]["raw_amount_usd"])) == Decimal("7.00")
    del excluded, included  # ids only used for readability/documentation


# ---------------------------------------------------------------------------
# M2 — untagged usage groups by (model, team, key) only
# ---------------------------------------------------------------------------


async def test_untagged_usage_groups_by_model_team_key_only(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Untagged Co", email="untag@inv.io")
    key_id = str(uuid.uuid4())
    await seed_usage_record(
        db_session, tenant_id=tid, key_id=key_id, cost_usd="1.00", created_at=JULY_START
    )
    await seed_usage_record(
        db_session, tenant_id=tid, key_id=key_id, cost_usd="2.00", created_at=JULY_START
    )

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    lines = await _fetch_lines(db_session, invoice_id)

    assert len(lines) == 1, "no line should be dropped/merged incorrectly for absent tag data"
    assert lines[0]["tags"] == {}
    assert Decimal(str(lines[0]["raw_amount_usd"])) == Decimal("3.00")


# ---------------------------------------------------------------------------
# M2 — tagged usage adds a tag-set grouping dimension
# ---------------------------------------------------------------------------


async def test_tagged_usage_adds_tag_set_grouping_dimension(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Tagged Co", email="tag@inv.io")
    key_id = str(uuid.uuid4())
    await seed_usage_record(
        db_session,
        tenant_id=tid,
        key_id=key_id,
        cost_usd="4.00",
        tags={"project": "alpha"},
        created_at=JULY_START,
    )
    await seed_usage_record(
        db_session, tenant_id=tid, key_id=key_id, cost_usd="6.00", created_at=JULY_START
    )

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    lines = await _fetch_lines(db_session, invoice_id)

    assert len(lines) == 2
    tagged = next(line for line in lines if line["tags"] == {"project": "alpha"})
    untagged = next(line for line in lines if line["tags"] == {})
    assert Decimal(str(tagged["raw_amount_usd"])) == Decimal("4.00")
    assert Decimal(str(untagged["raw_amount_usd"])) == Decimal(
        "6.00"
    ), "untagged line must exclude every cost_usd from the tagged rows"


# ---------------------------------------------------------------------------
# M2 (pre-freeze amendment) — multi-tag rows partition, never double-count
# ---------------------------------------------------------------------------


async def test_multi_tag_rows_partition_never_double_count(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="MultiTag Co", email="multi@inv.io")
    await seed_usage_record(
        db_session,
        tenant_id=tid,
        cost_usd="1.00",
        tags={"project": "alpha", "env": "prod"},
        created_at=JULY_START,
    )

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    lines = await _fetch_lines(db_session, invoice_id)

    assert len(lines) == 1, "exactly ONE line must include the multi-tag row"
    assert lines[0]["tags"] == {"env": "prod", "project": "alpha"}
    assert Decimal(str(lines[0]["raw_amount_usd"])) == Decimal("1.00")

    row = await _fetch_invoice_row(db_session, invoice_id)
    line_sum = sum((Decimal(str(line_row["raw_amount_usd"])) for line_row in lines), Decimal("0"))
    assert line_sum == Decimal(str(row["raw_total_usd"])) == Decimal("1.00")


# ---------------------------------------------------------------------------
# M3 — line amount is a pure sum of already-billed rows; no second price path
# ---------------------------------------------------------------------------


async def test_line_amount_is_pure_sum_no_second_price_path(
    client: Any, db_session: AsyncSession, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Pure Sum Co", email="sum@inv.io")
    key_id = str(uuid.uuid4())
    for amount in ("1.00000000", "2.00000000", "0.50000000"):
        await seed_usage_record(
            db_session, tenant_id=tid, key_id=key_id, cost_usd=amount, created_at=JULY_START
        )

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("resolve_markup_pct must never be invoked during generation")

    monkeypatch.setattr(
        "gateway.usage.application.rate_card_resolver.resolve_markup_pct", _boom, raising=True
    )

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    lines = await _fetch_lines(db_session, invoice_id)

    assert len(lines) == 1
    assert Decimal(str(lines[0]["raw_amount_usd"])) == Decimal("3.50000000")


# ---------------------------------------------------------------------------
# M4 — rounded-then-summed total matches the printed lines
# ---------------------------------------------------------------------------


async def test_rounded_then_summed_total_matches_printed_lines(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Rounding Co", email="round@inv.io")
    await seed_usage_record(
        db_session, tenant_id=tid, model_id="model-a", cost_usd="10.005", created_at=JULY_START
    )
    await seed_usage_record(
        db_session, tenant_id=tid, model_id="model-b", cost_usd="10.005", created_at=JULY_START
    )

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    lines = await _fetch_lines(db_session, invoice_id)
    row = await _fetch_invoice_row(db_session, invoice_id)

    for line_row in lines:
        assert Decimal(str(line_row["amount_usd"])) == Decimal("10.01"), (
            "each line rounds HALF_UP independently before summation"
        )
    assert Decimal(str(row["total_usd"])) == Decimal("20.02"), (
        "rounded-then-summed total must equal the sum of the DISPLAYED (rounded) lines, "
        "not 20.01 from summing full precision first"
    )


# ---------------------------------------------------------------------------
# M10 — zero-usage month still produces an invoice
# ---------------------------------------------------------------------------


async def test_zero_usage_month_still_produces_an_invoice(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Empty Co", email="empty@inv.io")

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), AUGUST_START)
    assert invoice_id is not None

    row = await _fetch_invoice_row(db_session, invoice_id)
    assert Decimal(str(row["total_usd"])) == Decimal("0.00")
    assert row["status"] == "issued"
    lines = await _fetch_lines(db_session, invoice_id)
    assert lines == []


# ---------------------------------------------------------------------------
# M12 — mid-month markup change requires no special handling
# ---------------------------------------------------------------------------


async def test_mid_month_markup_change_blends_correctly(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Markup Co", email="markup@inv.io")
    key_id = str(uuid.uuid4())
    # cost_usd already carries the point-in-time-resolved markup — generation just sums.
    await seed_usage_record(
        db_session,
        tenant_id=tid,
        key_id=key_id,
        cost_usd="1.20",  # billed at 20% markup
        created_at=datetime.datetime(2026, 7, 5, tzinfo=datetime.UTC),
    )
    await seed_usage_record(
        db_session,
        tenant_id=tid,
        key_id=key_id,
        cost_usd="1.30",  # billed at 30% markup (changed mid-month)
        created_at=datetime.datetime(2026, 7, 20, tzinfo=datetime.UTC),
    )

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    lines = await _fetch_lines(db_session, invoice_id)

    assert len(lines) == 1
    assert Decimal(str(lines[0]["raw_amount_usd"])) == Decimal("2.50")


# ---------------------------------------------------------------------------
# M13 — re-running month-close for an already-issued period is a no-op
# ---------------------------------------------------------------------------


async def test_rerun_already_issued_period_is_noop(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Rerun Co", email="rerun@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="9.00", created_at=JULY_START)

    generator = make_generator(app)
    first_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    assert first_id is not None

    second_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    assert second_id is None, "a re-run over an already-issued period must be a silent no-op"

    result = await db_session.execute(
        text("SELECT count(*) FROM invoices WHERE tenant_id = :tid"), {"tid": tid}
    )
    assert result.scalar() == 1


# ---------------------------------------------------------------------------
# M13 — two concurrent generation workers racing never duplicate
# ---------------------------------------------------------------------------


async def test_concurrent_generation_workers_never_duplicate(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Race Co", email="race@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="3.00", created_at=JULY_START)

    generator_a = make_generator(app)
    generator_b = make_generator(app)

    results = await asyncio.gather(
        generator_a.generate_for_tenant(uuid.UUID(tid), JULY_START),
        generator_b.generate_for_tenant(uuid.UUID(tid), JULY_START),
        return_exceptions=True,
    )

    for res in results:
        assert not isinstance(res, BaseException), f"neither worker may raise: {res!r}"

    result = await db_session.execute(
        text("SELECT count(*) FROM invoices WHERE tenant_id = :tid"), {"tid": tid}
    )
    assert result.scalar() == 1, "exactly one invoices row must exist after the race"
    winners = [r for r in results if r is not None]
    assert len(winners) == 1, "exactly one worker must have won the ON CONFLICT insert"


# ---------------------------------------------------------------------------
# M14 — seat-billing extension point exists but is inert
# ---------------------------------------------------------------------------


async def test_line_type_is_usage_and_never_seat_or_proration(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="LineType Co", email="lt@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="1.00", created_at=JULY_START)

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    lines = await _fetch_lines(db_session, invoice_id)

    assert len(lines) == 1
    assert lines[0]["line_type"] == "usage"
    assert lines[0]["line_type"] not in ("seat", "proration")
