"""RED suite — GET /admin/invoices/{id}/lines/{line_id}/seat-evidence (seat-billing
TASK.md §3 — FROZEN @ v2, M11/M12 + Reject list).

RED before BUILD: the route does not exist yet, so every request 404s at the ASGI
router level — the honest missing-implementation red.

DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role
from tests.invoice_generation.conftest import seed_usage_record

from .conftest import (
    JULY_START,
    assert_problem,
    assign_plan,
    auth,
    make_generator,
    mint_role_token,
    seat_evidence_url,
    seed_event,
    seed_plan_with_seat_price,
    seed_user,
    signup_tenant,
)

pytestmark = pytest.mark.asyncio


async def _generate(app: Any, tenant_id: str, period_start: Any = JULY_START) -> str:
    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tenant_id), period_start)
    assert invoice_id is not None
    return str(invoice_id)


async def _line_id(
    db_session: AsyncSession, *, invoice_id: str, line_type: str, key_id: str | None = None
) -> str:
    if key_id is not None:
        row = (
            await db_session.execute(
                text(
                    "SELECT id FROM invoice_lines WHERE invoice_id = :iid AND line_type = :lt"
                    " AND key_id = :kid"
                ),
                {"iid": invoice_id, "lt": line_type, "kid": key_id},
            )
        ).scalar_one()
    else:
        row = (
            await db_session.execute(
                text("SELECT id FROM invoice_lines WHERE invoice_id = :iid AND line_type = :lt"),
                {"iid": invoice_id, "lt": line_type},
            )
        ).scalar_one()
    return str(row)


async def _setup_tenant_with_full_and_partial_seats(
    client: Any, db_session: AsyncSession, app: Any, *, name: str, email: str
) -> tuple[str, str, list[str], str]:
    """Returns (tenant_id, invoice_id, full_price_user_ids, partial_user_id)."""
    _owner, tid = await signup_tenant(client, tenant_name=name, email=email)
    plan_id = await seed_plan_with_seat_price(db_session, name=f"{name}-plan", seat_price="10.00")
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

    partial_id = await seed_user(
        db_session, tenant_id=tid, created_at=JULY_START.replace(day=15, hour=14)
    )
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=partial_id,
        event_type="joined",
        occurred_at=JULY_START.replace(day=15, hour=14),
    )

    invoice_id = await _generate(app, tid)
    return tid, invoice_id, full_ids, partial_id


# ---------------------------------------------------------------------------
# M11 — proration line resolves to its ONE contributing user's events
# ---------------------------------------------------------------------------


async def test_seat_evidence_resolves_proration_line_to_its_user(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    tid, invoice_id, _full_ids, partial_id = await _setup_tenant_with_full_and_partial_seats(
        client, db_session, app, name="Proration Evidence Co", email="pe@seat.io"
    )
    line_id = await _line_id(
        db_session, invoice_id=invoice_id, line_type="proration", key_id=partial_id
    )
    token = mint_role_token(app, tenant_id=tid, role=Role.BILLING_ADMIN, email="pe-sub@seat.io")

    resp = await client.get(seat_evidence_url(invoice_id, line_id), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["user_id"] == partial_id
    assert body["items"][0]["event_type"] == "joined"
    assert body["has_more"] is False


# ---------------------------------------------------------------------------
# M11 — aggregate seat line paginates every full-price user's events
# ---------------------------------------------------------------------------


async def test_seat_evidence_resolves_aggregate_seat_line_to_every_full_price_user(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    tid, invoice_id, full_ids, partial_id = await _setup_tenant_with_full_and_partial_seats(
        client, db_session, app, name="Aggregate Evidence Co", email="ae@seat.io"
    )
    line_id = await _line_id(db_session, invoice_id=invoice_id, line_type="seat")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="ae-sub@seat.io")

    resp = await client.get(
        seat_evidence_url(invoice_id, line_id), params={"limit": "50"}, headers=auth(token)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    returned_user_ids = {item["user_id"] for item in body["items"]}
    assert returned_user_ids == set(full_ids)
    assert partial_id not in returned_user_ids


# ---------------------------------------------------------------------------
# M12 — seat-evidence against a 'usage' line is rejected, not silently empty
# ---------------------------------------------------------------------------


async def test_seat_evidence_against_usage_line_rejected(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Wrong Type Co", email="wt@seat.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="1.00", created_at=JULY_START)
    invoice_id = await _generate(app, tid)
    line_id = await _line_id(db_session, invoice_id=invoice_id, line_type="usage")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="wt-sub@seat.io")

    resp = await client.get(seat_evidence_url(invoice_id, line_id), headers=auth(token))

    assert_problem(resp, 400, "ERR_INVOICE_LINE_WRONG_TYPE")


# ---------------------------------------------------------------------------
# Reject — unknown or cross-tenant invoice is the same 404
# ---------------------------------------------------------------------------


async def test_seat_evidence_unknown_or_cross_tenant_is_same_404(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner_a, tid_a = await signup_tenant(
        client, tenant_name="Cross A Seat Co", email="csa@seat.io"
    )
    tid_b, invoice_id_b, _full, partial_id = await _setup_tenant_with_full_and_partial_seats(
        client, db_session, app, name="Cross B Seat Co", email="csb@seat.io"
    )
    line_id_b = await _line_id(
        db_session, invoice_id=invoice_id_b, line_type="proration", key_id=partial_id
    )
    token_a = mint_role_token(app, tenant_id=tid_a, role=Role.OWNER, email="csa-sub@seat.io")

    resp_unknown = await client.get(
        seat_evidence_url(str(uuid.uuid4()), line_id_b), headers=auth(token_a)
    )
    resp_cross = await client.get(seat_evidence_url(invoice_id_b, line_id_b), headers=auth(token_a))

    assert_problem(resp_unknown, 404, "ERR_INVOICE_NOT_FOUND")
    assert_problem(resp_cross, 404, "ERR_INVOICE_NOT_FOUND")
    assert resp_unknown.json() == resp_cross.json()


# ---------------------------------------------------------------------------
# Reject — bounded query timeout surfaces as the existing structured error
# ---------------------------------------------------------------------------


async def test_seat_evidence_timeout_maps_to_504(
    client: Any, db_session: AsyncSession, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid, invoice_id, _full, partial_id = await _setup_tenant_with_full_and_partial_seats(
        client, db_session, app, name="Timeout Seat Co", email="ts@seat.io"
    )
    line_id = await _line_id(
        db_session, invoice_id=invoice_id, line_type="proration", key_id=partial_id
    )
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="ts-sub@seat.io")

    from sqlalchemy.ext.asyncio import AsyncSession as SAAsyncSession

    orig_execute = SAAsyncSession.execute

    async def _flaky_execute(
        self: SAAsyncSession, statement: Any, *args: Any, **kwargs: Any
    ) -> Any:
        compiled = str(statement).lstrip()
        if compiled.startswith("SELECT") and "seat_membership_events" in compiled:
            raise TimeoutError("simulated seat_membership_events-query DB fault (test-only)")
        return await orig_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(SAAsyncSession, "execute", _flaky_execute)

    resp = await client.get(seat_evidence_url(invoice_id, line_id), headers=auth(token))

    assert_problem(resp, 504, "ERR_INVOICE_QUERY_TIMEOUT")
