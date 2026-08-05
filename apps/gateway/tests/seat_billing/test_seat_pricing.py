"""RED suite — seat/proration lines folded into InvoiceGenerator (seat-billing TASK.md
§3 — FROZEN @ v2, M1/M2/M4/M5/M7/M8/M9/M10/M14).

RED before BUILD: `plans.seat_price_usd_monthly` / `seat_membership_events` do not exist
yet, so every seed/generate call fails — the honest missing-implementation red.

DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role
from tests.invoice_generation.conftest import seed_usage_record

from .conftest import (
    AUGUST_START,
    JULY_START,
    assign_plan,
    get_invoice_detail,
    lines_of_type,
    make_generator,
    mint_role_token,
    seed_event,
    seed_plan_with_seat_price,
    seed_user,
    signup_tenant,
)

NIL_SEAT_KEY_ID = "00000000-0000-0000-0000-000000000000"


async def _generate(app: Any, tenant_id: str, period_start: Any = JULY_START) -> str:
    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tenant_id), period_start)
    assert invoice_id is not None
    return str(invoice_id)


@pytest.fixture(autouse=True)
async def _drop_test_installed_triggers(db_session: AsyncSession) -> AsyncIterator[None]:
    """suite-stability M2 — undo the DDL the helper below installs.

    `create_all` does not replay the migration's triggers, so these tests install
    them by hand. The schema is now built ONCE per xdist worker, so a trigger left
    behind silently changes the behaviour of every later test on that worker — it
    made tests/audit_export fail depending only on which worker ran it. DELETE
    cannot undo DDL, so the DDL has to undo itself.

    Discovered from pg_trigger rather than hand-listed, so a trigger added to the
    helper later is still cleaned up ([[add-cross-manifest-table-drift]]).
    """
    yield
    installed = (
        await db_session.execute(
            text(
                "SELECT c.relname, t.tgname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE NOT t.tgisinternal AND t.tgname LIKE '%_immutable_guard'"
            )
        )
    ).all()
    for table, trigger in installed:
        await db_session.execute(text(f'DROP TRIGGER IF EXISTS "{trigger}" ON "{table}"'))
    if installed:
        await db_session.commit()


async def _install_immutability_trigger(db_session: AsyncSession, table: str) -> None:
    """create_all doesn't replay the migration's own BEFORE UPDATE/DELETE trigger —
    mirrors tests/invoice_generation/test_api.py's own `_install_immutability_triggers`."""
    await db_session.execute(
        text(
            f"""
            CREATE OR REPLACE FUNCTION {table}_immutable_guard_fn() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'invoice_immutable_violation: {table} rows are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    await db_session.execute(
        text(
            f"""
            CREATE OR REPLACE TRIGGER {table}_immutable_guard
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {table}_immutable_guard_fn()
            """
        )
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# M1 — a pending invite is never a seat
# ---------------------------------------------------------------------------


async def test_pending_invite_is_never_a_seat(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Invite Seat Co", email="is@seat.io")
    plan_id = await seed_plan_with_seat_price(
        db_session, name="pending-invite-plan", seat_price="10.00"
    )
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    # Owner is seat #1 — seed its 'joined' event, then ONE more active user (seat #2).
    owner_id = (
        await db_session.execute(text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid})
    ).scalar_one()
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=str(owner_id),
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )
    extra_user = await seed_user(db_session, tenant_id=tid, created_at=JULY_START.replace(month=6))
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=extra_user,
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )
    # A pending invite — never creates a users row (M1's own decisive fact).
    await db_session.execute(
        text(
            "INSERT INTO invites (id, tenant_id, email, role, token_hash, status, expires_at, invited_by_user_id)"
            " VALUES (:id, :tid, 'pending@is.seat.io', 'member', 'hash-x', 'pending',"
            " now() + interval '7 days', :owner_id)"
        ),
        {"id": uuid.uuid4(), "tid": tid, "owner_id": owner_id},
    )
    await db_session.commit()

    invoice_id = await _generate(app, tid)
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="is-sub@seat.io")
    detail = await get_invoice_detail(client, token=token, invoice_id=invoice_id)

    seat_lines = lines_of_type(detail, "seat")
    assert len(seat_lines) == 1
    assert seat_lines[0]["request_count"] == 2, "exactly the 2 REAL users, never the pending invite"


# ---------------------------------------------------------------------------
# M2 — inert for an unplanned tenant / a plan with no or zero seat price
# ---------------------------------------------------------------------------


async def test_unplanned_tenant_produces_zero_seat_lines(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Unplanned Co", email="up@seat.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="5.00", created_at=JULY_START)
    for _ in range(4):
        await seed_user(db_session, tenant_id=tid, created_at=JULY_START)

    invoice_id = await _generate(app, tid)
    row = (
        (
            await db_session.execute(
                text("SELECT total_usd FROM invoices WHERE id = :id"), {"id": invoice_id}
            )
        )
        .mappings()
        .one()
    )
    lines = (
        (
            await db_session.execute(
                text("SELECT line_type FROM invoice_lines WHERE invoice_id = :id"),
                {"id": invoice_id},
            )
        )
        .scalars()
        .all()
    )

    assert "seat" not in lines and "proration" not in lines
    assert Decimal(str(row["total_usd"])) == Decimal("5.00"), "byte-identical to usage-only total"


async def test_zero_seat_price_plan_is_byte_identical_to_unplanned(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """M2's reachable half: seat_price_usd_monthly IS NULL. The '= 0.00' half of this
    scenario is UNREACHABLE via any real write path — `ck_plans_seat_price_positive`
    (mirrors ck_plans_seat_cap_positive) rejects a literal 0 at the DB layer BEFORE it
    could ever be assigned to a tenant; see test_seat_price_zero_rejected_at_db_check
    below for the Reject-list proof of that half."""
    _owner, tid = await signup_tenant(client, tenant_name="NullPrice Co", email="np@seat.io")
    plan_id = await seed_plan_with_seat_price(db_session, name="null-price-plan", seat_price=None)
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="3.00", created_at=JULY_START)
    for _ in range(4):
        await seed_user(db_session, tenant_id=tid, created_at=JULY_START)

    invoice_id = await _generate(app, tid)
    row = (
        (
            await db_session.execute(
                text("SELECT total_usd FROM invoices WHERE id = :id"), {"id": invoice_id}
            )
        )
        .mappings()
        .one()
    )
    lines = (
        (
            await db_session.execute(
                text("SELECT line_type FROM invoice_lines WHERE invoice_id = :id"),
                {"id": invoice_id},
            )
        )
        .scalars()
        .all()
    )

    assert "seat" not in lines and "proration" not in lines
    assert Decimal(str(row["total_usd"])) == Decimal("3.00")


async def test_seat_price_zero_rejected_at_db_check_constraint(db_session: AsyncSession) -> None:
    """Reject list: 'a migration-seed or (hypothetical future) write attempting
    seat_price_usd_monthly <= 0 -> rejected at the DB CHECK-constraint level, not an HTTP
    code' — ck_plans_seat_price_positive."""
    with pytest.raises(Exception, match=r"ck_plans_seat_price_positive|check"):
        await db_session.execute(
            text(
                "INSERT INTO plans (id, name, display_name, seat_price_usd_monthly)"
                " VALUES (:id, 'zero-price-plan', 'Zero', 0.00)"
            ),
            {"id": uuid.uuid4()},
        )
        await db_session.commit()
    await db_session.rollback()


# ---------------------------------------------------------------------------
# M5 — a ledger-less user falls back to current-state columns, never dropped
# ---------------------------------------------------------------------------


async def test_ledger_less_user_falls_back_never_dropped(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Fallback Co", email="fb@seat.io")
    plan_id = await seed_plan_with_seat_price(db_session, name="fallback-plan", seat_price="20.00")
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    # The owner is ledger-less by construction (signup is NOT one of the 5 instrumented
    # write sites) — but its real `users.created_at` is the WALL-CLOCK instant this test
    # runs, not a fixed fixture date. Backdating it to June here (mirrors `seed_user`'s own
    # June default below) keeps this test deterministic regardless of run date: without
    # this, the owner's fallback event would land INSIDE the July fixture period on any
    # run date before 2026-08-01 (producing a PARTIAL proration line, not a full seat) and
    # OUTSIDE it entirely on any run date on/after 2026-08-01 (producing NO line at all) —
    # neither is what this test means to exercise.
    owner_id = (
        await db_session.execute(text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid})
    ).scalar_one()
    await db_session.execute(
        text("UPDATE users SET created_at = :june WHERE id = :uid"),
        {"june": JULY_START.replace(month=6, tzinfo=None), "uid": owner_id},
    )
    await db_session.commit()
    # A users row with ZERO seat_membership_events rows (simulated data-integrity gap) —
    # deliberately no seed_event() call for this user.
    ledgerless = await seed_user(db_session, tenant_id=tid, created_at=JULY_START.replace(month=6))

    invoice_id = await _generate(app, tid)
    # The owner (seat #1, also ledger-less by construction — signup never seeds a
    # membership event) + this seeded user both fall back to M5's current-state columns —
    # both roll into the aggregate 'seat' line (not keyed by user id), so assert via
    # the aggregate's request_count rather than a per-line key_id.
    seat_row = (
        (
            await db_session.execute(
                text(
                    "SELECT request_count FROM invoice_lines WHERE invoice_id = :id AND line_type = 'seat'"
                ),
                {"id": invoice_id},
            )
        )
        .mappings()
        .one()
    )
    assert seat_row["request_count"] == 2, "both ledger-less users still price as full seats"
    assert ledgerless is not None


# ---------------------------------------------------------------------------
# M6/M7 — proration by calendar days touched
# ---------------------------------------------------------------------------


async def test_mid_month_join_prorates(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="MidJoin Co", email="mj@seat.io")
    plan_id = await seed_plan_with_seat_price(db_session, name="midjoin-plan", seat_price="31.00")
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    joiner = await seed_user(
        db_session, tenant_id=tid, created_at=JULY_START.replace(day=15, hour=14)
    )
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=joiner,
        event_type="joined",
        occurred_at=JULY_START.replace(day=15, hour=14),
    )

    invoice_id = await _generate(app, tid)
    line = (
        (
            await db_session.execute(
                text(
                    "SELECT raw_amount_usd, amount_usd, request_count FROM invoice_lines"
                    " WHERE invoice_id = :id AND line_type = 'proration' AND key_id = :uid"
                ),
                {"id": invoice_id, "uid": joiner},
            )
        )
        .mappings()
        .one()
    )
    expected_raw = Decimal("31.00") * 17 / Decimal(31)
    assert Decimal(str(line["raw_amount_usd"])) == expected_raw
    assert Decimal(str(line["amount_usd"])) == expected_raw.quantize(Decimal("0.01"))
    assert line["request_count"] == 1


async def test_mid_month_leave_and_full_period_aggregate_separately(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """M7 — 3 full-period seats aggregate into ONE 'seat' line; 1 mid-period seat gets
    its OWN separate 'proration' line (also covers M8/M9's shape assertions)."""
    _owner, tid = await signup_tenant(client, tenant_name="Aggregate Co", email="ag@seat.io")
    plan_id = await seed_plan_with_seat_price(db_session, name="aggregate-plan", seat_price="10.00")
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)

    owner_id = (
        await db_session.execute(text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid})
    ).scalar_one()
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=str(owner_id),
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )
    full_ids = [str(owner_id)]
    for _ in range(2):
        uid = await seed_user(db_session, tenant_id=tid, created_at=JULY_START.replace(month=6))
        await seed_event(
            db_session,
            tenant_id=tid,
            user_id=uid,
            event_type="joined",
            occurred_at=JULY_START.replace(month=6),
        )
        full_ids.append(uid)

    partial_id = await seed_user(db_session, tenant_id=tid, created_at=JULY_START)
    await seed_event(
        db_session, tenant_id=tid, user_id=partial_id, event_type="joined", occurred_at=JULY_START
    )
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=partial_id,
        event_type="deactivated",
        occurred_at=JULY_START.replace(day=11),
    )

    invoice_id = await _generate(app, tid)
    seat_lines = (
        (
            await db_session.execute(
                text("SELECT * FROM invoice_lines WHERE invoice_id = :id AND line_type = 'seat'"),
                {"id": invoice_id},
            )
        )
        .mappings()
        .all()
    )
    proration_lines = (
        (
            await db_session.execute(
                text(
                    "SELECT * FROM invoice_lines WHERE invoice_id = :id AND line_type = 'proration'"
                ),
                {"id": invoice_id},
            )
        )
        .mappings()
        .all()
    )

    assert len(seat_lines) == 1
    assert seat_lines[0]["request_count"] == 3
    assert str(seat_lines[0]["key_id"]) == NIL_SEAT_KEY_ID, "M9 aggregate sentinel key_id"
    assert str(seat_lines[0]["model_id"]) == "seat"
    assert seat_lines[0]["team_id"] is None
    assert Decimal(str(seat_lines[0]["raw_amount_usd"])) == Decimal("30.00")

    assert len(proration_lines) == 1
    assert str(proration_lines[0]["key_id"]) == partial_id, (
        "M9: proration key_id = the seat's own user_id"
    )
    assert proration_lines[0]["request_count"] == 1


# ---------------------------------------------------------------------------
# M8 — seat lines fold into the SAME rounded-then-summed total as usage lines
# ---------------------------------------------------------------------------


async def test_seat_lines_fold_into_the_same_total_as_usage(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Total Fold Co", email="tf@seat.io")
    plan_id = await seed_plan_with_seat_price(
        db_session, name="total-fold-plan", seat_price="30.00"
    )
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="100.00", created_at=JULY_START)
    owner_id = (
        await db_session.execute(text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid})
    ).scalar_one()
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=str(owner_id),
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )

    invoice_id = await _generate(app, tid)
    row = (
        (
            await db_session.execute(
                text("SELECT total_usd FROM invoices WHERE id = :id"), {"id": invoice_id}
            )
        )
        .mappings()
        .one()
    )
    assert Decimal(str(row["total_usd"])) == Decimal("130.00")


# ---------------------------------------------------------------------------
# M10 — cap vs price independence
# ---------------------------------------------------------------------------


async def test_over_cap_tenant_still_billed_in_full(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="OverCap Co", email="oc@seat.io")
    plan_id = await seed_plan_with_seat_price(db_session, name="overcap-plan", seat_price="10.00")
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    await db_session.execute(text("UPDATE tenants SET seat_cap = 3 WHERE id = :tid"), {"tid": tid})
    await db_session.commit()

    owner_id = (
        await db_session.execute(text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid})
    ).scalar_one()
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=str(owner_id),
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )
    for _ in range(4):  # + owner = 5 total, over the seat_cap=3
        uid = await seed_user(db_session, tenant_id=tid, created_at=JULY_START.replace(month=6))
        await seed_event(
            db_session,
            tenant_id=tid,
            user_id=uid,
            event_type="joined",
            occurred_at=JULY_START.replace(month=6),
        )

    invoice_id = await _generate(app, tid)
    seat_row = (
        (
            await db_session.execute(
                text(
                    "SELECT request_count, raw_amount_usd FROM invoice_lines WHERE invoice_id = :id AND line_type = 'seat'"
                ),
                {"id": invoice_id},
            )
        )
        .mappings()
        .one()
    )
    assert seat_row["request_count"] == 5, (
        "billed for every real seat, cap never read/enforced here"
    )
    assert Decimal(str(seat_row["raw_amount_usd"])) == Decimal("50.00")


# ---------------------------------------------------------------------------
# M14 — an issued seat/proration line is immutable
# ---------------------------------------------------------------------------


async def test_issued_seat_line_is_immutable(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Immutable Seat Co", email="ims@seat.io")
    plan_id = await seed_plan_with_seat_price(
        db_session, name="immutable-seat-plan", seat_price="12.00"
    )
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    owner_id = (
        await db_session.execute(text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid})
    ).scalar_one()
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=str(owner_id),
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )

    invoice_id = await _generate(app, tid)
    line_id = (
        await db_session.execute(
            text("SELECT id FROM invoice_lines WHERE invoice_id = :id AND line_type = 'seat'"),
            {"id": invoice_id},
        )
    ).scalar_one()
    await _install_immutability_trigger(db_session, "invoice_lines")

    before = (
        await db_session.execute(
            text("SELECT amount_usd FROM invoice_lines WHERE id = :id"), {"id": str(line_id)}
        )
    ).scalar()

    with pytest.raises(Exception, match="invoice_immutable"):
        await db_session.execute(
            text("UPDATE invoice_lines SET amount_usd = 0 WHERE id = :id"), {"id": str(line_id)}
        )
        await db_session.commit()
    await db_session.rollback()

    after = (
        await db_session.execute(
            text("SELECT amount_usd FROM invoice_lines WHERE id = :id"), {"id": str(line_id)}
        )
    ).scalar()
    assert before == after
