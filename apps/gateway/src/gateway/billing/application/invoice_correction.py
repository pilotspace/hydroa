"""record_invoice_correction — the internal-only append-only correction write path
(invoice-generation TASK.md §3 — FROZEN @ v1, M6).

No HTTP route exposes correction CREATION in v1 (mirrors usage/application/
recorder.py:record_correction's own precedent, which likewise has no HTTP route —
called internally by the cost-recovery sweep). GET /admin/invoices/{id} surfaces
the resulting `corrections` list + `corrected_total_usd` (read-only, §3).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.billing.infrastructure.invoice_repository import InvoiceRepository


async def record_invoice_correction(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    invoice_id: uuid.UUID,
    delta_usd: Decimal,
    reason: str,
    created_by: str,
) -> uuid.UUID:
    """Append ONE new invoice_corrections row; never touches the original invoice
    or any invoice_line (M6). Opens its own session/transaction — a genuinely new
    document, not an edit to anything already committed."""
    async with session_factory() as session, session.begin():
        repo = InvoiceRepository(session)
        return await repo.record_correction(
            invoice_id=invoice_id, delta_usd=delta_usd, reason=reason, created_by=created_by
        )
