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
    assert Decimal(str(untagged["raw_amount_usd"])) == Decimal("6.00"), (
        "untagged line must exclude every cost_usd from the tagged rows"
    )


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
# C4 (audit-remediation fix, supersedes the original M4 "rounded-then-summed"
# contract) — total_usd is the raw SUM rounded ONCE, never derived by summing
# already-per-group-rounded lines; the reconciliation delta lands on exactly
# one line so total_usd still equals the sum of the printed lines.
#
# DELIBERATE CONTRACT CHANGE (audit finding C4, 2026-07-14): this test used to
# be test_rounded_then_summed_total_matches_printed_lines and asserted the OLD
# buggy behavior — total_usd == 20.02, the sum of two independently-HALF_UP-
# rounded 10.005 lines. That is exactly the defect C4 flags: per-group rounding
# before summation can silently manufacture OR erase cents relative to the
# tenant's real aggregate spend (raw_total here is 20.010, which rounds to
# 20.01, not 20.02). See test_high_cardinality_tags_never_erase_revenue below
# for the sharper case (many sub-cent groups each rounding to $0.00).
# ---------------------------------------------------------------------------


async def test_total_usd_is_raw_total_rounded_once_reconciled_to_printed_lines(
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

    # raw_total = 10.005 + 10.005 = 20.010 -> round_half_up ONCE = 20.01 (never 20.02:
    # that figure only ever existed by rounding each 10.005 boundary value up
    # independently THEN summing, which is exactly the C4 defect).
    assert Decimal(str(row["total_usd"])) == Decimal("20.01")
    assert Decimal(str(row["raw_total_usd"])) == Decimal("20.010000")

    # M9 still holds: total_usd must equal the sum of the actually-printed lines —
    # the 1-cent reconciliation delta (20.01 - 20.02 naive = -0.01) lands on
    # exactly one line (deterministic tie-break: model-b sorts after model-a).
    line_sum = sum((Decimal(str(line_row["amount_usd"])) for line_row in lines), Decimal("0"))
    assert line_sum == Decimal(str(row["total_usd"])) == Decimal("20.01")
    by_model = {line_row["model_id"]: Decimal(str(line_row["amount_usd"])) for line_row in lines}
    assert by_model == {"model-a": Decimal("10.01"), "model-b": Decimal("10.00")}


async def test_high_cardinality_tags_never_erase_revenue(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """C4 (HIGH/MED audit finding): 100 usage rows sharing the SAME model/key but each
    carrying a UNIQUE client tag land in 100 distinct (model,team,key,tags) groups.
    Each group's raw cost (0.0049) independently rounds HALF_UP to $0.00 — under the
    OLD rounded-then-summed contract the ENTIRE $0.49 of real spend would vanish from
    the invoice. Tag cardinality must never be able to erase revenue."""
    _owner, tid = await signup_tenant(client, tenant_name="Fragment Co", email="frag@inv.io")
    key_id = str(uuid.uuid4())
    for i in range(100):
        await seed_usage_record(
            db_session,
            tenant_id=tid,
            key_id=key_id,
            cost_usd="0.0049",
            tags={"client_request_id": f"req-{i}"},
            created_at=JULY_START,
        )

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    lines = await _fetch_lines(db_session, invoice_id)
    row = await _fetch_invoice_row(db_session, invoice_id)

    assert len(lines) == 100, "each unique tag value is still its own evidence-linkable line"
    assert Decimal(str(row["raw_total_usd"])) == Decimal("0.4900")
    assert Decimal(str(row["total_usd"])) == Decimal("0.49"), (
        "100 groups each rounding to $0.00 individually must NOT erase the real "
        "$0.49 of aggregate revenue from the invoice total"
    )
    line_sum = sum((Decimal(str(line_row["amount_usd"])) for line_row in lines), Decimal("0"))
    assert line_sum == Decimal(str(row["total_usd"])) == Decimal("0.49"), (
        "M9: total_usd must still equal the sum of the printed lines"
    )
    zero_lines = [lr for lr in lines if Decimal(str(lr["amount_usd"])) == Decimal("0.00")]
    assert len(zero_lines) == 99, "exactly one line absorbs the reconciled $0.49"


# ---------------------------------------------------------------------------
# C3 (audit-remediation Blocker fix) — the invoice skip is COUPLED to the SAME
# predicate that turns on real-time credit enforcement (settings.credits_gate_enabled →
# PostgresCreditGuard holds+settles every tenant). One source of truth: invoice-skip and
# credit-holds can never diverge. (The previous billing_mode gate was unsafe in BOTH
# directions: nothing ever set billing_mode='credits' so it double-billed when the flag
# was on; and a mode='credits' tenant with the flag OFF got neither a hold nor an invoice
# — a revenue leak.)
# ---------------------------------------------------------------------------


async def test_credit_enforcement_active_skips_invoice_never_double_bills(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """Flag ON → the tenant is held+settled in real time by the credit ledger, so
    invoicing the same usage would double-bill → skip. Holds regardless of billing_mode
    (default 'invoice'), because the enforcement guard itself is not billing_mode-gated."""
    _owner, tid = await signup_tenant(client, tenant_name="Credits Co", email="credits@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="42.00", created_at=JULY_START)

    generator = make_generator(app, credits_gate_enabled=True)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)

    assert invoice_id is None, "under active credit enforcement no monthly invoice may be created"
    result = await db_session.execute(
        text("SELECT count(*) FROM invoices WHERE tenant_id = :tid"), {"tid": tid}
    )
    assert result.scalar() == 0, "no invoice row may exist while credit enforcement is active"


async def test_credit_enforcement_off_still_invoices_no_revenue_leak(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """Flag OFF → the credit guard is a Passthrough (no real-time hold/settle), so the
    tenant MUST still be invoiced or it is never billed at all. Proves the skip is driven
    by the enforcement flag, not by a billing_mode column that no path ever sets."""
    _owner, tid = await signup_tenant(client, tenant_name="No Leak Co", email="noleak@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="42.00", created_at=JULY_START)

    generator = make_generator(app, credits_gate_enabled=False)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)

    assert invoice_id is not None, "with enforcement off the tenant must still be invoiced"
    row2 = await _fetch_invoice_row(db_session, invoice_id)
    assert Decimal(str(row2["total_usd"])) == Decimal("42.00")


async def test_default_generator_preserves_existing_invoice_behavior(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """The generator defaults credits_gate_enabled=False (the settings default), so
    generation is byte-identical to before this fix shipped unless an operator flips the
    central credits knob on."""
    _owner, tid = await signup_tenant(client, tenant_name="Default Mode Co", email="dm@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="42.00", created_at=JULY_START)

    generator = make_generator(app)  # credits_gate_enabled defaults False
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)

    assert invoice_id is not None, "default (flag off) tenants keep getting invoiced as before"
    row2 = await _fetch_invoice_row(db_session, invoice_id)
    assert Decimal(str(row2["total_usd"])) == Decimal("42.00")


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
