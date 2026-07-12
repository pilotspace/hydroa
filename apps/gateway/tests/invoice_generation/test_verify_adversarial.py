"""Adversarial VERIFY repros for invoice-generation (financial-auditor stance).

Uncommitted, verify-only. Probes: determinism/order-independence, JSONB
canonicalization under an ACTUAL key-order swap, a HALF_UP-vs-banker's rounding
boundary, evidence-predicate exactness (cross-model leak + pre-tags-column
emulation), and PDF renderer robustness against adversarial tag values.
"""

from __future__ import annotations

import datetime
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.billing.application.invoice_export import render_pdf
from gateway.billing.domain.invoice import Invoice, InvoiceLine

from .conftest import JULY_START, make_generator, seed_usage_record, signup_tenant


async def _fetch_lines(db_session: AsyncSession, invoice_id: Any) -> list[Any]:
    result = await db_session.execute(
        text("SELECT * FROM invoice_lines WHERE invoice_id = :id ORDER BY amount_usd"),
        {"id": str(invoice_id)},
    )
    return list(result.mappings().all())


async def _fetch_invoice_row(db_session: AsyncSession, invoice_id: Any) -> Any:
    result = await db_session.execute(
        text("SELECT * FROM invoices WHERE id = :id"), {"id": str(invoice_id)}
    )
    return result.mappings().one()


# ---------------------------------------------------------------------------
# Determinism: insertion order must not affect grouping/sums
# ---------------------------------------------------------------------------


async def test_generation_is_order_independent_across_insertion_sequence(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """Same set of rows, seeded in two different orders across two tenants ->
    identical line set (tags + raw_amount_usd), byte-for-byte."""
    _owner_a, tid_a = await signup_tenant(client, tenant_name="Order A Co", email="ordera@inv.io")
    _owner_b, tid_b = await signup_tenant(client, tenant_name="Order B Co", email="orderb@inv.io")
    key_id = str(uuid.uuid4())

    rows = [
        ("1.11", {"env": "prod", "project": "alpha"}),
        ("2.22", {}),
        ("3.33", {"project": "alpha", "env": "prod"}),  # same tag SET, different key order
        ("4.44", {"team": "x"}),
    ]

    for amount, tags in rows:
        await seed_usage_record(
            db_session, tenant_id=tid_a, key_id=key_id, cost_usd=amount, tags=tags,
            created_at=JULY_START,
        )
    for amount, tags in reversed(rows):
        await seed_usage_record(
            db_session, tenant_id=tid_b, key_id=key_id, cost_usd=amount, tags=tags,
            created_at=JULY_START,
        )

    generator = make_generator(app)
    inv_a = await generator.generate_for_tenant(uuid.UUID(tid_a), JULY_START)
    inv_b = await generator.generate_for_tenant(uuid.UUID(tid_b), JULY_START)

    lines_a = await _fetch_lines(db_session, inv_a)
    lines_b = await _fetch_lines(db_session, inv_b)

    def _shape(lines: list[Any]) -> set[tuple[str, str]]:
        return {(json.dumps(line_row["tags"], sort_keys=True), str(line_row["raw_amount_usd"]))
                for line_row in lines}

    assert len(lines_a) == 3, "the two differently-key-ordered rows must land in ONE line"
    assert _shape(lines_a) == _shape(lines_b), "insertion order must not change the result"

    row_a = await _fetch_invoice_row(db_session, inv_a)
    row_b = await _fetch_invoice_row(db_session, inv_b)
    assert row_a["total_usd"] == row_b["total_usd"] == Decimal("11.10")


# ---------------------------------------------------------------------------
# Rounding: a HALF_UP vs banker's-rounding discriminating boundary
# ---------------------------------------------------------------------------


async def test_half_up_rounding_at_a_bankers_rounding_discriminator(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """0.125 -> ROUND_HALF_UP -> 0.13. Banker's rounding (ROUND_HALF_EVEN) would give
    0.12 instead (rounds to the nearest EVEN digit: 2). This is the one boundary
    value that actually distinguishes the two rounding modes."""
    _owner, tid = await signup_tenant(client, tenant_name="Bankers Co", email="bankers@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="0.125", created_at=JULY_START)

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    lines = await _fetch_lines(db_session, invoice_id)

    assert len(lines) == 1
    assert Decimal(str(lines[0]["amount_usd"])) == Decimal("0.13"), (
        "must be true ROUND_HALF_UP (0.13), not banker's ROUND_HALF_EVEN (0.12)"
    )


# ---------------------------------------------------------------------------
# Evidence predicate: exactness — no cross-model/period leak, incl. tags={} rows
# that predate a tags column value (server_default emulation)
# ---------------------------------------------------------------------------


async def test_evidence_predicate_excludes_cross_model_and_out_of_period_rows(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    from gateway.billing.infrastructure.invoice_repository import InvoiceRepository

    _owner, tid = await signup_tenant(client, tenant_name="Evidence Co", email="evid@inv.io")
    key_id = str(uuid.uuid4())
    matching_ids = set()
    for i in range(5):
        rid = await seed_usage_record(
            db_session, tenant_id=tid, key_id=key_id, model_id="gpt-4o",
            cost_usd="1.00", created_at=JULY_START + datetime.timedelta(hours=i),
        )
        matching_ids.add(str(rid))
    # a different model, same key/period -> must NOT appear
    await seed_usage_record(
        db_session, tenant_id=tid, key_id=key_id, model_id="gpt-3.5",
        cost_usd="9.00", created_at=JULY_START,
    )
    # same model/key, but OUTSIDE the period (June) -> must NOT appear
    await seed_usage_record(
        db_session, tenant_id=tid, key_id=key_id, model_id="gpt-4o",
        cost_usd="9.00", created_at=datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC),
    )

    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    lines = await _fetch_lines(db_session, invoice_id)
    gpt4o_line = next(line_row for line_row in lines if line_row["model_id"] == "gpt-4o")

    async with app.state.sessionmaker() as session:
        repo = InvoiceRepository(session)
        invoice = await repo.get_invoice(uuid.UUID(tid), gpt4o_line["invoice_id"])
        line = await repo.get_line(gpt4o_line["invoice_id"], gpt4o_line["id"])
        evidence = await repo.evidence_keyset(
            tenant_id=uuid.UUID(tid), invoice=invoice, line=line, limit=100
        )

    got_ids = {str(e.usage_record_id) for e in evidence}
    assert got_ids == matching_ids, (
        f"evidence predicate leaked or dropped rows: extra={got_ids - matching_ids}, "
        f"missing={matching_ids - got_ids}"
    )


# ---------------------------------------------------------------------------
# PDF renderer: adversarial tag values must not crash the export
# ---------------------------------------------------------------------------


def test_pdf_renderer_survives_adversarial_tag_values() -> None:
    """RTL override chars + a huge tag value must not crash render_pdf — either it
    renders (truncated/escaped) or the finding is a real robustness gap."""
    invoice = Invoice(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        period_start=JULY_START,
        period_end=datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC),
        status="issued",
        currency="USD",
        total_usd=Decimal("1.00"),
        raw_total_usd=Decimal("1.00"),
        tax_usd=Decimal("0"),
        issued_at=JULY_START,
        created_at=JULY_START,
    )
    adversarial_tags = {
        "rtl": "‮مرحبا‬",  # RTL override + Arabic
        "huge": "A" * 5000,
        "null_ish": "\x00\x01\x02",
    }
    line = InvoiceLine(
        id=uuid.uuid4(),
        invoice_id=invoice.id,
        model_id="gpt-4o",
        team_id=None,
        key_id=uuid.uuid4(),
        tags=adversarial_tags,
        amount_usd=Decimal("1.00"),
        raw_amount_usd=Decimal("1.00"),
    )
    pdf_bytes = render_pdf(invoice, [line])
    assert pdf_bytes.startswith(b"%PDF"), "must still produce a well-formed PDF"
