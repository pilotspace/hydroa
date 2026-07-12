"""Idempotent platform-operator top-up (credits-ledger TASK.md §3 M9/R4).

Ordering: lock_balance_row() is called BEFORE the idempotency-key pre-check read, so
two concurrent top-ups sharing the same key for the SAME tenant serialize on the
balance row lock — the second call only proceeds after the first commits, and its
idempotency-key read then sees the first call's already-committed row (replay path),
never a duplicate-insert race for the same-tenant case.

HEAL (credits-ledger verify finding 3 / 🟡, 2026-07-12): find_topup_by_idempotency_key
is a GLOBAL lookup (unscoped by tenant_id, per R4's own wording — a key reused with a
DIFFERENT tenant_id must 409), but was previously serialized only by the CALLER's own
tenant balance row lock. Two concurrent top-ups for the SAME key but DIFFERENT tenants
lock DIFFERENT balance rows, so neither blocked the other — both could commit,
silently violating R4 under genuine concurrency (only reachable by a superadmin, so a
financial/audit-integrity gap, not an attacker-facing exploit — still real duplicated
money from one operator action).

Fix (INSERT-first-with-conflict-detection, migration 1891020e487c): a NEW global
partial UNIQUE INDEX `credit_ledger_idempotency_key_global_uq` on
`credit_ledger(idempotency_key) WHERE idempotency_key IS NOT NULL` — additive, the
frozen §3 per-tenant `UNIQUE (tenant_id, idempotency_key)` index is untouched. The
read-then-decide pre-check below stays (fast path — no behavior change for the
overwhelmingly common non-racing case), but the INSERT itself is now the ACTUAL source
of truth: a losing concurrent insert for the same key raises IntegrityError (caught
below), and we re-resolve replay-vs-conflict against whichever row actually landed.

A `pg_advisory_xact_lock(hashtext(key))` held across the read-then-decide section was
tried first and rejected: it serializes correctly in production, but it deadlocks
against the verify repro's technique of forcing genuine concurrent interleaving with an
`asyncio.Barrier` INSIDE the idempotency-key lookup (both coroutines must reach the
barrier to unblock each other) — a lock held across that same section means only ONE
coroutine can ever reach the barrier at a time, so neither ever does. The
constraint-based approach lets both transactions run concurrently (satisfying the
repro's rendezvous) and resolves the race reactively via Postgres's own uniqueness
check, with no cross-transaction lock held at all.
Repro: tests/credits_ledger/test_verify_adversarial.py::
test_verify_concurrent_topup_same_key_different_tenants_bypasses_r4.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.error_catalog import CREDITS_IDEMPOTENCY_KEY_CONFLICT
from gateway.credits.infrastructure import ledger_store
from gateway.credits.infrastructure.ledger_store import (
    TopupRow,
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


def _replay_or_conflict(
    existing: TopupRow, *, tenant_id: uuid.UUID, amount_usd: Decimal, idempotency_key: str
) -> TopupResult:
    """R4: identical (tenant_id, amount_usd) replay -> 200 with the ORIGINAL row;
    any mismatch on the same key -> 409 CREDITS_IDEMPOTENCY_KEY_CONFLICT."""
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
    row_id = uuid.uuid4()
    try:
        async with session_factory() as session, session.begin():
            balance, _grace = await lock_balance_row(session, tenant_id)

            # Fast path: the common (non-racing) case never needs the constraint —
            # a same-tenant retry always finds its own already-committed row here,
            # serialized correctly by the balance-row lock above.
            existing = await find_topup_by_idempotency_key(session, idempotency_key)
            if existing is not None:
                return _replay_or_conflict(
                    existing,
                    tenant_id=tenant_id,
                    amount_usd=amount_usd,
                    idempotency_key=idempotency_key,
                )

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
    except IntegrityError:
        # HEAL (finding 3): the global unique index caught a genuine concurrent
        # cross-tenant (or same-tenant) race the pre-check above could not — another
        # transaction committed a topup for this EXACT key between our read and our
        # insert. The `async with session.begin()` block above already rolled back
        # on this exception; re-resolve against a FRESH session/transaction exactly
        # like the ordinary pre-check path.
        #
        # Calls ledger_store.find_topup_by_idempotency_key directly (module-qualified)
        # rather than the top-level `find_topup_by_idempotency_key` name used in the
        # pre-check above — deliberately a DIFFERENT reference, not an alias for the
        # same call: the pre-check is the overridable "read what might already be
        # committed" seam every caller goes through; this fallback fires only after
        # OUR OWN insert has already lost a genuine DB-level race and just needs the
        # winning row's data to render 200-replay vs 409-conflict — a plain, direct,
        # one-shot read with no reason to indirect through the same seam twice.
        async with session_factory() as session:
            existing = await ledger_store.find_topup_by_idempotency_key(
                session, idempotency_key
            )
        if existing is None:  # pragma: no cover — defensive: implies a row exists
            raise
        return _replay_or_conflict(
            existing, tenant_id=tenant_id, amount_usd=amount_usd, idempotency_key=idempotency_key
        )


__all__ = ["TopupResult", "topup"]
