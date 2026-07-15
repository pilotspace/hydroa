"""RED suite: M0 — tenants.rpm_limit / tenants.tpm_limit additive override columns +
their `> 0 OR NULL` CHECK constraints (plan-rate-enforcement TASK.md §3, FROZEN @ v1).

Real Postgres (tests/conftest.py's `db_session`, schema built via create_all against the
CURRENT ORM — no migration replay needed for a CHECK-constraint test). RED until
TenantRow has rpm_limit/tpm_limit columns at all (AttributeError / TypeError) — once
those columns exist without the CHECK constraints, this test would insert 0/-5 without
error, so the constraint's absence is ALSO a right-reason red once the bare columns land
first (asserting IntegrityError never raises).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def _insert_tenant(db_session: AsyncSession, *, rpm_limit: int | None, tpm_limit: int | None) -> None:
    from gateway.tenants.infrastructure.orm import TenantRow

    row = TenantRow(
        id=uuid.uuid4(),
        name=f"CheckConstraintCo-{uuid.uuid4().hex[:8]}",
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
    )
    db_session.add(row)
    await db_session.commit()


async def test_rpm_limit_zero_rejected_by_check_constraint(db_session: AsyncSession) -> None:
    """rpm_limit=0 violates `ck_tenants_rpm_limit_positive`. Covers: M0."""
    with pytest.raises(IntegrityError):
        await _insert_tenant(db_session, rpm_limit=0, tpm_limit=None)
    await db_session.rollback()


async def test_rpm_limit_negative_rejected_by_check_constraint(db_session: AsyncSession) -> None:
    """rpm_limit=-5 violates `ck_tenants_rpm_limit_positive`. Covers: M0."""
    with pytest.raises(IntegrityError):
        await _insert_tenant(db_session, rpm_limit=-5, tpm_limit=None)
    await db_session.rollback()


async def test_tpm_limit_zero_rejected_by_check_constraint(db_session: AsyncSession) -> None:
    """tpm_limit=0 violates `ck_tenants_tpm_limit_positive`. Covers: M0."""
    with pytest.raises(IntegrityError):
        await _insert_tenant(db_session, rpm_limit=None, tpm_limit=0)
    await db_session.rollback()


async def test_positive_rpm_and_tpm_limit_accepted(db_session: AsyncSession) -> None:
    """A positive rpm_limit/tpm_limit override is accepted and persisted (control case —
    proves the CHECK constraint isn't over-broad and rejecting valid values). Covers: M0.
    """
    from sqlalchemy import text

    tid = uuid.uuid4()
    await _insert_tenant_ok(db_session, tid=tid, rpm_limit=42, tpm_limit=99999)
    row = (
        await db_session.execute(
            text("SELECT rpm_limit, tpm_limit FROM tenants WHERE id = :id"), {"id": str(tid)}
        )
    ).one()
    assert row[0] == 42
    assert row[1] == 99999


async def _insert_tenant_ok(
    db_session: AsyncSession, *, tid: uuid.UUID, rpm_limit: int, tpm_limit: int
) -> None:
    from gateway.tenants.infrastructure.orm import TenantRow

    row = TenantRow(id=tid, name=f"OkCo-{tid.hex[:8]}", rpm_limit=rpm_limit, tpm_limit=tpm_limit)
    db_session.add(row)
    await db_session.commit()
