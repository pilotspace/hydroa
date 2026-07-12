"""Shared row-locked ledger primitives — the ONE transactional shape every
credits write path (check_and_hold / settle / release / topup) uses verbatim.

Access pattern (credits-ledger TASK.md §3):
  BEGIN;
  INSERT INTO tenant_credit_balances (tenant_id) VALUES (:t) ON CONFLICT DO NOTHING;
  SELECT balance_usd, grace_usd FROM tenant_credit_balances WHERE tenant_id=:t FOR UPDATE;
  <business check>
  INSERT credit_ledger (...);  -- plain INSERT, no ON CONFLICT — see insert_ledger_row()
  UPDATE tenant_credit_balances SET balance_usd=:new, updated_at=now();
  COMMIT;

The lazy INSERT ... ON CONFLICT DO NOTHING before the SELECT ... FOR UPDATE means a
brand-new tenant's FIRST-ever concurrent admissions still serialize correctly: Postgres
blocks a second concurrent INSERT of the same PK until the first transaction commits or
rolls back, so the row lock closes the TOCTOU window (M3) even before any topup has ever
run for that tenant.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ZERO = Decimal("0")


async def lock_balance_row(session: AsyncSession, tenant_id: uuid.UUID) -> tuple[Decimal, Decimal]:
    """Ensure a tenant_credit_balances row exists, then SELECT ... FOR UPDATE it.

    MUST be called inside an open transaction (session.begin()); the row lock is
    held until that transaction commits or rolls back. Returns (balance_usd, grace_usd).
    """
    await session.execute(
        text(
            "INSERT INTO tenant_credit_balances (tenant_id, balance_usd, grace_usd)"
            " VALUES (:t, 0, 0) ON CONFLICT (tenant_id) DO NOTHING"
        ),
        {"t": str(tenant_id)},
    )
    row = (
        await session.execute(
            text(
                "SELECT balance_usd, grace_usd FROM tenant_credit_balances"
                " WHERE tenant_id = :t FOR UPDATE"
            ),
            {"t": str(tenant_id)},
        )
    ).fetchone()
    if row is None:  # pragma: no cover — defensive; the INSERT above guarantees a row
        return _ZERO, _ZERO
    return Decimal(str(row[0])), Decimal(str(row[1]))


async def update_balance(session: AsyncSession, tenant_id: uuid.UUID, new_balance_usd: Decimal) -> None:
    """Persist the new balance snapshot — caller already holds the row lock."""
    await session.execute(
        text(
            "UPDATE tenant_credit_balances SET balance_usd = :b, updated_at = now()"
            " WHERE tenant_id = :t"
        ),
        {"b": str(new_balance_usd), "t": str(tenant_id)},
    )


async def insert_ledger_row(
    session: AsyncSession,
    *,
    row_id: uuid.UUID,
    tenant_id: uuid.UUID,
    entry_type: str,
    amount_usd: Decimal,
    balance_after_usd: Decimal,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    note: str | None = None,
) -> None:
    """Insert one immutable credit_ledger row.

    Plain INSERT — deliberately WITHOUT "ON CONFLICT (id) DO NOTHING" (a §3-cited
    idiom that mirrors usage_records' insert_usage_row, but PostgreSQL rejects any
    INSERT ... ON CONFLICT against a table carrying an UPDATE rule:
    "INSERT with ON CONFLICT clause cannot be used with table that has INSERT or
    UPDATE rules" — a genuine engine-level conflict between §3's two literal
    instructions (the credit_ledger_no_update/no_delete RULEs, M8/R7, vs. the
    ON CONFLICT insert idiom). Resolved in favor of the RULE, since R7's scenario
    is the more specific, binding oracle (silent no-op, no exception) and — unlike
    usage_records, where the SAME deterministic event_id can race between an XADD
    stream-flush and a direct fallback insert — every call site here always mints a
    FRESH uuid.uuid4() row_id at insert time (see postgres_guard.py / topup_service.py),
    so there is no dual-path race for ON CONFLICT to guard against; dropping it is a
    behavior-preserving no-op for every actual call site.
    """
    await session.execute(
        text(
            "INSERT INTO credit_ledger"
            " (id, tenant_id, entry_type, amount_usd, balance_after_usd,"
            "  reference_type, reference_id, request_id, idempotency_key,"
            "  actor_user_id, note)"
            " VALUES"
            " (:id, :tenant_id, :entry_type, :amount_usd, :balance_after_usd,"
            "  :reference_type, :reference_id, :request_id, :idempotency_key,"
            "  :actor_user_id, :note)"
        ),
        {
            "id": str(row_id),
            "tenant_id": str(tenant_id),
            "entry_type": entry_type,
            "amount_usd": str(amount_usd),
            "balance_after_usd": str(balance_after_usd),
            "reference_type": reference_type,
            "reference_id": str(reference_id) if reference_id is not None else None,
            "request_id": str(request_id) if request_id is not None else None,
            "idempotency_key": idempotency_key,
            "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
            "note": note,
        },
    )


async def find_open_hold(
    session: AsyncSession, tenant_id: uuid.UUID, request_id: uuid.UUID
) -> tuple[uuid.UUID, Decimal] | None:
    """Return (hold_row_id, hold_amount_usd) for the OPEN hold on (tenant_id, request_id) —
    a hold with no settle/release/correction sibling yet. None if no open hold exists.

    "Open" = a hold row whose request_id has no OTHER row (settle/release) with the SAME
    request_id + tenant_id. request_id is unique-per-admission by construction (minted
    fresh at check_and_hold time), so at most one hold row can ever match.
    """
    row = (
        await session.execute(
            text(
                "SELECT h.id, h.amount_usd FROM credit_ledger h"
                " WHERE h.tenant_id = :t AND h.request_id = :r AND h.entry_type = 'hold'"
                " AND NOT EXISTS ("
                "   SELECT 1 FROM credit_ledger s"
                "   WHERE s.tenant_id = h.tenant_id AND s.request_id = h.request_id"
                "     AND s.entry_type IN ('settle', 'release')"
                " )"
            ),
            {"t": str(tenant_id), "r": str(request_id)},
        )
    ).fetchone()
    if row is None:
        return None
    return uuid.UUID(str(row[0])), Decimal(str(row[1]))


class TopupRow:
    """Duck-typed read of a credit_ledger topup row (avoids an ORM round-trip)."""

    __slots__ = ("id", "tenant_id", "amount_usd", "balance_after_usd", "idempotency_key", "created_at")

    def __init__(
        self,
        *,
        id: uuid.UUID,
        tenant_id: uuid.UUID,
        amount_usd: Decimal,
        balance_after_usd: Decimal,
        idempotency_key: str | None,
        created_at: object,
    ) -> None:
        self.id = id
        self.tenant_id = tenant_id
        self.amount_usd = amount_usd
        self.balance_after_usd = balance_after_usd
        self.idempotency_key = idempotency_key
        self.created_at = created_at


async def find_topup_by_idempotency_key(
    session: AsyncSession, idempotency_key: str
) -> TopupRow | None:
    """Return the existing topup row for idempotency_key, if any — looked up GLOBALLY
    (NOT scoped to a tenant_id): R4 treats a key reused with a DIFFERENT tenant_id as a
    conflict too, a stricter rule than the DB's own per-tenant UNIQUE (tenant_id,
    idempotency_key) index, so the app layer checks across all tenants.

    Caller MUST already hold the TARGET tenant's balance row lock (lock_balance_row)
    before calling this — that lock serializes concurrent topups for the SAME tenant
    sharing the same key so the common (non-cross-tenant-race) read-then-decide path is
    race-free (M9). A genuinely concurrent cross-tenant race on the identical key is a
    residual, documented low-probability gap (client-generated keys are expected to be
    unique enough) — closing it fully would need a global advisory lock per key.
    """
    row = (
        await session.execute(
            text(
                "SELECT id, tenant_id, amount_usd, balance_after_usd, idempotency_key, created_at"
                " FROM credit_ledger"
                " WHERE idempotency_key = :k AND entry_type = 'topup'"
            ),
            {"k": idempotency_key},
        )
    ).fetchone()
    if row is None:
        return None
    return TopupRow(
        id=uuid.UUID(str(row[0])),
        tenant_id=uuid.UUID(str(row[1])),
        amount_usd=Decimal(str(row[2])),
        balance_after_usd=Decimal(str(row[3])),
        idempotency_key=row[4],
        created_at=row[5],
    )
