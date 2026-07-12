"""Idempotent platform-operator top-up (credits-ledger TASK.md §3 M9/R4).

Ordering is deliberate: lock_balance_row() is called BEFORE the idempotency-key
lookup, so two concurrent top-ups sharing the same key for the SAME tenant
serialize on the balance row lock — the second call only proceeds after the
first commits, and its idempotency-key read then sees the first call's
already-committed row (replay path), never a duplicate-insert race.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.error_catalog import CREDITS_IDEMPOTENCY_KEY_CONFLICT
from gateway.credits.infrastructure.ledger_store import (
    find_topup_by_idempotency_key,
    insert_ledger_row,
    lock_balance_row,
    update_balance,
)


@dataclass(frozen=True, slots=True)
class TopupResult:
    id: uuid.UUID
    tenant_id: uuid.UUID
    amount_usd: Decimal
    balance_after_usd: Decimal
    idempotency_key: str
    created: bool  # True -> 201 (new topup); False -> 200 (idempotent replay)


async def topup(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    amount_usd: Decimal,
    idempotency_key: str,
    note: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> TopupResult:
    """Credit a tenant's balance. Idempotent under retry (M9); conflicts on a
    reused key with a different tenant/amount (R4 — raises CREDITS_IDEMPOTENCY_KEY_CONFLICT).

    Caller (the API router) is responsible for: amount_usd validation (R2),
    Idempotency-Key presence (R3), superadmin auth (R5), and tenant-existence (R6)
    — all BEFORE calling this function, since those are cheap pre-transaction checks.
    """
    async with session_factory() as session, session.begin():
        balance, _grace = await lock_balance_row(session, tenant_id)

        existing = await find_topup_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            if existing.tenant_id != tenant_id or existing.amount_usd != amount_usd:
                raise CREDITS_IDEMPOTENCY_KEY_CONFLICT.exc()
            return TopupResult(
                id=existing.id,
                tenant_id=existing.tenant_id,
                amount_usd=existing.amount_usd,
                balance_after_usd=existing.balance_after_usd,
                idempotency_key=idempotency_key,
                created=False,
            )

        row_id = uuid.uuid4()
        new_balance = balance + amount_usd
        await insert_ledger_row(
            session,
            row_id=row_id,
            tenant_id=tenant_id,
            entry_type="topup",
            amount_usd=amount_usd,
            balance_after_usd=new_balance,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            note=note,
        )
        await update_balance(session, tenant_id, new_balance)

        return TopupResult(
            id=row_id,
            tenant_id=tenant_id,
            amount_usd=amount_usd,
            balance_after_usd=new_balance,
            idempotency_key=idempotency_key,
            created=True,
        )


__all__ = ["TopupResult", "topup"]
