"""Persistence for checkout_sessions (self-serve-checkout TASK.md §3).

Every read is tenant-scoped (WHERE tenant_id = identity.tenant_id) in the SAME query that
checks existence — a cross-tenant id and an unknown id return the IDENTICAL None (no
enumeration oracle, M1). `lock_for_confirm` takes SELECT ... FOR UPDATE so the pending->
succeeded flip is serialized (M3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.payments.infrastructure.orm import CheckoutSessionRow


async def find_by_idempotency_key(
    session: AsyncSession, tenant_id: uuid.UUID, idempotency_key: str
) -> CheckoutSessionRow | None:
    """The existing session for (tenant_id, idempotency_key), if any (M4 idempotent create)."""
    return (
        await session.execute(
            select(CheckoutSessionRow).where(
                CheckoutSessionRow.tenant_id == tenant_id,
                CheckoutSessionRow.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()


async def find_by_id(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> CheckoutSessionRow | None:
    """Tenant-scoped lookup — cross-tenant + unknown both return None (M1)."""
    return (
        await session.execute(
            select(CheckoutSessionRow).where(
                CheckoutSessionRow.id == session_id,
                CheckoutSessionRow.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()


async def lock_for_confirm(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> CheckoutSessionRow | None:
    """SELECT ... FOR UPDATE the tenant's session row (M3). Must run inside a transaction."""
    return (
        await session.execute(
            select(CheckoutSessionRow)
            .where(
                CheckoutSessionRow.id == session_id,
                CheckoutSessionRow.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


def insert_session(
    session: AsyncSession,
    *,
    row_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    provider: str,
    intent_type: str,
    intent_payload: dict[str, Any],
    idempotency_key: str,
    status: str,
    provider_ref: str | None,
    preview: dict[str, Any],
    created_at: datetime,
    expires_at: datetime,
) -> CheckoutSessionRow:
    """Add a new checkout_sessions row to the session (caller commits)."""
    row = CheckoutSessionRow(
        id=row_id,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        provider=provider,
        intent_type=intent_type,
        intent_payload=intent_payload,
        idempotency_key=idempotency_key,
        status=status,
        provider_ref=provider_ref,
        applied_ref=None,
        preview=preview,
        created_at=created_at,
        expires_at=expires_at,
    )
    session.add(row)
    return row
