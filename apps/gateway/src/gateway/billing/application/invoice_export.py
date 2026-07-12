"""render_csv / render_pdf — invoice export renderers (invoice-generation TASK.md
§3 — FROZEN @ v1, M9).

Both derive from the SAME persisted, already-rounded Invoice/InvoiceLine figures
the API detail response reads — they can never disagree with each other or with
the API detail response because there is exactly one source of truth (the
persisted `amount_usd`/`total_usd` columns) and no renderer re-derives a number.
"""

from __future__ import annotations

import csv
import io
import json

from gateway.billing.domain.invoice import Invoice, InvoiceLine


def export_filename(invoice: Invoice, ext: str) -> str:
    period = invoice.period_start.strftime("%Y-%m")
    return f"invoice-{period}.{ext}"


def render_csv(invoice: Invoice, lines: list[InvoiceLine]) -> bytes:
    """One row per invoice_line, stable columns, machine-readable (M9)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "model_id",
            "team_id",
            "key_id",
            "tags",
            "amount_usd",
            "prompt_tokens",
            "completion_tokens",
            "request_count",
            "line_type",
        ]
    )
    for line in lines:
        writer.writerow(
            [
                str(line.id),
                line.model_id,
                str(line.team_id) if line.team_id is not None else "",
                str(line.key_id),
                json.dumps(line.tags, sort_keys=True),
                f"{line.amount_usd:.2f}",
                line.prompt_tokens,
                line.completion_tokens,
                line.request_count,
                line.line_type,
            ]
        )
    return buf.getvalue().encode("utf-8")


def render_pdf(invoice: Invoice, lines: list[InvoiceLine]) -> bytes:
    """A statement-style PDF: tenant/period/issued date, line table, tax line, total.

    Page compression is disabled so the printed total is a byte-searchable literal
    in the content stream (test evidence — M9's "PDF's printed total" assertion),
    not merely metadata.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setPageCompression(0)

    def _new_page_header() -> float:
        y = 750.0
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, y, "INVOICE")
        y -= 22
        c.setFont("Helvetica", 10)
        c.drawString(50, y, f"Tenant: {invoice.tenant_id}")
        y -= 14
        c.drawString(50, y, f"Period: {invoice.period_start.date()} to {invoice.period_end.date()}")
        y -= 14
        c.drawString(50, y, f"Status: {invoice.status}    Issued: {invoice.issued_at}")
        y -= 24
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, "Model / Team / Key / Tags")
        c.drawRightString(500, y, "Amount USD")
        y -= 16
        c.setFont("Helvetica", 9)
        return y

    y = _new_page_header()
    for line in lines:
        label = (
            f"{line.model_id} | team={line.team_id or '-'} | key={line.key_id} "
            f"| tags={line.tags or '{}'}"
        )
        c.drawString(50, y, label[:95])
        c.drawRightString(500, y, f"{line.amount_usd:.2f}")
        y -= 13
        if y < 90:
            c.showPage()
            c.setPageCompression(0)
            y = _new_page_header()

    y -= 12
    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"Tax: {invoice.tax_usd:.2f}")
    y -= 16
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, f"TOTAL USD: {invoice.total_usd:.2f}")

    c.showPage()
    c.save()
    return buf.getvalue()
